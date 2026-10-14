using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using BepInEx;
using BepInEx.Unity.IL2CPP;
using HarmonyLib;

namespace DubSync.ReplacedProbe;

[BepInPlugin("com.dubsync.replaced.probe", "DubSync REPLACED Probe", "0.1.0")]
public sealed class Plugin : BasePlugin
{
    private const string ManagerType = "SadCatStudios.Dialogs.SubtitlesManager";
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(2) };
    private static Plugin? _instance;
    private static string? _activeTriggerType;
    private static string? _activeTriggerId;
    private static string? _activeDialogueTextKey;
    private readonly string _gameRoot = ResolveGameRoot();
    private readonly ConcurrentDictionary<string, byte> _patched = new(StringComparer.Ordinal);
    private Harmony? _harmony;
    private Timer? _scanTimer;
    private string _apiBaseUrl = "http://127.0.0.1:8766";
    private string _projectId = "replaced";
    private readonly object _playbackGate = new();
    private readonly object _runtimeEntriesGate = new();
    private readonly object _learnedTriggerGate = new();
    private object? _currentChannel;
    private object? _currentSound;
    private string? _loadedPackageDirectory;
    private DateTime _loadedManifestWriteUtc;
    private float _runtimeVolumePercent = 100f;
    private bool _volumeApiWarningLogged;
    private List<RuntimeAudioEntry> _runtimeEntries = new();

    private sealed record RuntimeAudioEntry(string OccurrenceId, string TakeId, string AudioPath,
        string SpeakerKey, string[] SourceMatchKeys, string[] DubMatchKeys, string[] LegacyMatchKeys,
        string? TriggerType, string? TriggerId);
    private sealed record LearnedTriggerCorrelation(string OccurrenceId, string TriggerType, string TriggerId);
    private sealed record PlaybackResult(bool Started, string? MatchMode, string? TakeId,
        string? OccurrenceId, string? Error, string? MatchKeyType);

    public override void Load()
    {
        _instance = this;
        LoadLocalConfig();
        Log.LogInfo($"DubSync REPLACED adapter started; source=SubtitlesManager.InitLine/ReleaseLine; game_root={_gameRoot}; process_cwd={Environment.CurrentDirectory}.");
        _harmony = new Harmony("com.dubsync.replaced.probe");
        AppDomain.CurrentDomain.AssemblyLoad += OnAssemblyLoad;
        ScanLoadedAssemblies();
        _scanTimer = new Timer(_ => ScanLoadedAssemblies(), null, TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(2));
        if (_patched.ContainsKey("patch-complete-init") && _patched.ContainsKey("patch-complete-release"))
            _scanTimer.Change(Timeout.Infinite, Timeout.Infinite);
    }

    private void LoadLocalConfig()
    {
        var configPath = Path.Combine(_gameRoot, "BepInEx", "config", "DubSync.ReplacedProbe.json");
        if (!File.Exists(configPath)) return;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(configPath));
            var root = document.RootElement;
            if (root.TryGetProperty("api_base_url", out var url) && Uri.TryCreate(url.GetString(), UriKind.Absolute, out var uri)
                && uri.Scheme == Uri.UriSchemeHttp && IPAddressLoopback(uri.Host))
                _apiBaseUrl = uri.ToString().TrimEnd('/');
            if (root.TryGetProperty("project_id", out var project) && project.GetString() is { Length: > 0 and <= 48 } projectId)
                _projectId = projectId;
        }
        catch (Exception error)
        {
            Log.LogWarning($"Ignored invalid local adapter config: {error.GetType().Name}: {error.Message}");
        }
    }

    private static bool IPAddressLoopback(string host)
    {
        return string.Equals(host, "127.0.0.1", StringComparison.OrdinalIgnoreCase)
            || string.Equals(host, "localhost", StringComparison.OrdinalIgnoreCase)
            || string.Equals(host, "::1", StringComparison.OrdinalIgnoreCase);
    }

    private static string ResolveGameRoot()
    {
        try
        {
            var assemblyPath = typeof(Plugin).Assembly.Location;
            if (!string.IsNullOrWhiteSpace(assemblyPath))
            {
                var directory = Directory.GetParent(Path.GetFullPath(assemblyPath));
                while (directory is not null)
                {
                    if (string.Equals(directory.Name, "BepInEx", StringComparison.OrdinalIgnoreCase)
                        && directory.Parent is { } gameRoot)
                        return gameRoot.FullName;
                    directory = directory.Parent;
                }
            }
        }
        catch
        {
            // Some Unity loaders do not expose Assembly.Location; use the executable base path below.
        }

        return Path.GetFullPath(AppContext.BaseDirectory);
    }

    private void OnAssemblyLoad(object? sender, AssemblyLoadEventArgs args) => InspectAssembly(args.LoadedAssembly);

    private void ScanLoadedAssemblies()
    {
        foreach (var assembly in AppDomain.CurrentDomain.GetAssemblies())
            InspectAssembly(assembly);
    }

    private void InspectAssembly(Assembly assembly)
    {
        var assemblyName = assembly.GetName().Name ?? "unknown";
        if (!assemblyName.Contains("Assembly-CSharp", StringComparison.OrdinalIgnoreCase)) return;

        Type[] types;
        try { types = assembly.GetTypes(); }
        catch (ReflectionTypeLoadException error) { types = error.Types.Where(type => type is not null).Cast<Type>().ToArray(); }
        catch (Exception error)
        {
            Log.LogDebug($"Could not enumerate {assemblyName}: {error.GetType().Name}: {error.Message}");
            return;
        }

        var manager = types.FirstOrDefault(type => string.Equals(type.FullName, ManagerType, StringComparison.Ordinal));
        if (manager is null) return;
        var methods = manager.GetMethods(BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
        foreach (var method in methods)
        {
            var normalizedName = method.Name.Replace("_", string.Empty, StringComparison.Ordinal).ToLowerInvariant();
            if (normalizedName is not ("initline" or "releaseline")) continue;
            var signatureKey = $"signature:{method.Module.ModuleVersionId}:{method.MetadataToken}";
            if (_patched.TryAdd(signatureKey, 0))
            {
                var parameters = string.Join(", ", method.GetParameters().Select(parameter =>
                    $"{parameter.ParameterType.FullName} {parameter.Name}"));
                Log.LogInfo($"callback: {ManagerType}.{method.Name}({parameters})");
            }
            TryPatch(method);
        }
    }

    private void TryPatch(MethodInfo method)
    {
        var key = $"patch:{method.Module.ModuleVersionId}:{method.MetadataToken}";
        if (!_patched.TryAdd(key, 0)) return;
        try
        {
            var prefix = typeof(Plugin).GetMethod(nameof(SubtitlesManagerPrefix), BindingFlags.NonPublic | BindingFlags.Static)!;
            _harmony!.Patch(method, prefix: new HarmonyMethod(prefix));
            Log.LogInfo($"Installed lifecycle hook on {ManagerType}.{method.Name}.");
            if (method.Name.Replace("_", string.Empty, StringComparison.Ordinal).Equals("InitLine", StringComparison.OrdinalIgnoreCase))
                _patched.TryAdd("patch-complete-init", 0);
            if (method.Name.Replace("_", string.Empty, StringComparison.Ordinal).Equals("ReleaseLine", StringComparison.OrdinalIgnoreCase))
                _patched.TryAdd("patch-complete-release", 0);
            if (_patched.ContainsKey("patch-complete-init") && _patched.ContainsKey("patch-complete-release"))
                _scanTimer?.Change(Timeout.Infinite, Timeout.Infinite);
        }
        catch (Exception error)
        {
            Log.LogWarning($"Could not patch {ManagerType}.{method.Name}: {error.GetType().Name}: {error.Message}");
        }
    }

    private static void SubtitlesManagerPrefix(MethodBase __originalMethod, object[] __args)
    {
        var plugin = _instance;
        if (plugin is null) return;
        var callback = __originalMethod.Name;
        if (callback.Replace("_", string.Empty, StringComparison.Ordinal).Equals("InitLine", StringComparison.OrdinalIgnoreCase))
        {
            var articyRef = __args is { Length: > 0 } ? __args[0] : null;
            var fragment = ResolveDialogueFragment(articyRef, out var fragmentDiagnostic);
            var externalId = ExtractExternalId(fragment ?? articyRef, out var resolutionDiagnostic);
            var triggerType = externalId is null ? "articy_object_id" : "articy_external_id";
            var objectIdDiagnostic = "";
            var triggerId = externalId ?? ExtractArticyObjectId(articyRef, out objectIdDiagnostic);
            var dialogueText = ExtractDialogueText(fragment);
            var speakerKey = ExtractSpeakerKey(fragment);
            var dialogueTextKey = NormalizeDialogueText(dialogueText);
            if (triggerId is null)
                plugin.Log.LogWarning($"LIVE InitLine without Articy trigger; trying exact dialogue-text lookup. argument={DescribeValue(articyRef)}; fragment={fragmentDiagnostic}; resolution={resolutionDiagnostic}; object_id={objectIdDiagnostic}");
            if (string.Equals(_activeTriggerType, triggerType, StringComparison.Ordinal)
                && string.Equals(_activeTriggerId, triggerId, StringComparison.Ordinal)
                && string.Equals(_activeDialogueTextKey, dialogueTextKey, StringComparison.Ordinal)) return;
            if (triggerId is null && dialogueTextKey.Length > 0
                && string.Equals(_activeDialogueTextKey, dialogueTextKey, StringComparison.Ordinal)) return;
            if (_activeTriggerId is not null)
                plugin.SendRuntimeEvent("dialogue_end", _activeTriggerType, _activeTriggerId, callback, "next_dialogue_started");
            _activeTriggerType = triggerType;
            _activeTriggerId = triggerId;
            _activeDialogueTextKey = dialogueTextKey;
            plugin.StopCurrentPlayback();
            var playback = plugin.TryPlayDialogue(dialogueText, speakerKey,
                triggerId is null ? null : triggerType, triggerId);
            var playbackDetails = new Dictionary<string, object?>
            {
                ["playback_started"] = playback.Started,
                ["match_mode"] = playback.MatchMode,
            };
            if (playback.MatchKeyType is not null) playbackDetails["match_key_type"] = playback.MatchKeyType;
            if (playback.TakeId is not null) playbackDetails["matched_take_id"] = playback.TakeId;
            if (playback.OccurrenceId is not null) playbackDetails["matched_occurrence_id"] = playback.OccurrenceId;
            if (playback.Error is not null) playbackDetails["playback_error"] = playback.Error;
            if (!playback.Started)
                plugin.Log.LogWarning($"DubSync did not start game audio: reason={playback.Error ?? "audio_not_started"}; trigger={triggerType}:{triggerId ?? "(none)"}; speaker={speakerKey ?? "(unknown)"}; subtitle={FormatSubtitleForLog(dialogueText)}");
            if (triggerId is null && playback.OccurrenceId is null)
            {
                plugin.Log.LogWarning($"LIVE dialogue_start unmatched; speaker={speakerKey ?? "(unknown)"}; subtitle={FormatSubtitleForLog(dialogueText)}");
                plugin.Log.LogDebug("Dialogue has no trigger and no unique ready exact-text match; no dub audio was played.");
                plugin.SendRuntimeEvent("error", null, null, callback,
                    playback.Error ?? "unmatched_dialogue_text", playbackDetails);
                return;
            }
            if (triggerId is null)
                plugin.Log.LogInfo($"LIVE dialogue_start resolved by approved text match: occurrence={playback.OccurrenceId}; take={playback.TakeId}; subtitle={FormatSubtitleForLog(dialogueText)}");
            else
                plugin.Log.LogInfo($"LIVE dialogue_start {triggerType}={triggerId}; subtitle={FormatSubtitleForLog(dialogueText)}");
            plugin.SendRuntimeEvent("dialogue_start", triggerType, triggerId, callback,
                playback.Error, playbackDetails);
            return;
        }

        if (callback.Replace("_", string.Empty, StringComparison.Ordinal).Equals("ReleaseLine", StringComparison.OrdinalIgnoreCase))
        {
            var activeType = _activeTriggerType;
            var activeId = _activeTriggerId;
            _activeTriggerType = null;
            _activeTriggerId = null;
            _activeDialogueTextKey = null;
            if (activeId is null) return;
            plugin.Log.LogInfo($"LIVE dialogue_end {activeType}={activeId}");
            plugin.SendRuntimeEvent("dialogue_end", activeType, activeId, callback, "release_line");
        }
    }

    private void SendRuntimeEvent(string eventType, string? triggerType, string? triggerId, string callback,
        string? reason, Dictionary<string, object?>? extraDetails = null)
    {
        var details = new Dictionary<string, object?> { ["callback"] = callback };
        if (reason is not null) details["reason"] = reason;
        if (extraDetails is not null)
            foreach (var item in extraDetails) details[item.Key] = item.Value;
        var payload = new Dictionary<string, object?>
        {
            ["event_id"] = $"EVT_{Guid.NewGuid():N}".ToUpperInvariant(),
            ["event_type"] = eventType,
            ["occurred_at"] = DateTimeOffset.UtcNow.ToString("O"),
            ["trigger"] = triggerId is null ? null : new Dictionary<string, string>
                { ["type"] = triggerType!, ["id"] = triggerId },
            ["details"] = details,
        };
        _ = Task.Run(async () =>
        {
            try
            {
                using var request = new HttpRequestMessage(HttpMethod.Post, $"{_apiBaseUrl}/api/integration/runtime/events");
                request.Headers.Add("X-DubSync-Project", _projectId);
                request.Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");
                using var response = await Http.SendAsync(request).ConfigureAwait(false);
                if (!response.IsSuccessStatusCode)
                    Log.LogWarning($"DubSync event bridge returned HTTP {(int)response.StatusCode}.");
            }
            catch (Exception error)
            {
                Log.LogDebug($"DubSync event bridge unavailable: {error.GetType().Name}: {error.Message}");
            }
        });
    }

    private PlaybackResult TryPlayDialogue(string? dialogueText, string speakerKey,
        string? triggerType, string? triggerId)
    {
        if (!TryResolveAudio(dialogueText, speakerKey, triggerType, triggerId,
                out var audioPath, out var entry, out var matchMode, out var matchKeyType, out var resolutionError))
            return new PlaybackResult(false, matchMode, null, null, resolutionError, matchKeyType);

        try
        {
            if (!TryPlayWithGameFmod(audioPath, out var playbackError))
                return new PlaybackResult(false, matchMode, entry?.TakeId, entry?.OccurrenceId, playbackError, matchKeyType);
            Log.LogInfo($"DubSync in-game audio started: take={entry!.TakeId}; match={matchMode}; volume={_runtimeVolumePercent}%.");
            return new PlaybackResult(true, matchMode, entry!.TakeId, entry.OccurrenceId, null, matchKeyType);
        }
        catch (Exception error)
        {
            var detail = $"{error.GetBaseException().GetType().Name}: {error.GetBaseException().Message}";
            Log.LogWarning($"DubSync could not play the matched game audio: {detail}");
            return new PlaybackResult(false, matchMode, entry?.TakeId, entry?.OccurrenceId, detail, matchKeyType);
        }
    }

    private bool TryResolveAudio(string? dialogueText, string speakerKey, string? triggerType,
        string? triggerId, out string audioPath, out RuntimeAudioEntry? resolved,
        out string? matchMode, out string? matchKeyType, out string? error)
    {
        audioPath = "";
        resolved = null;
        matchMode = null;
        matchKeyType = null;
        error = null;
        if (!RefreshRuntimePackage(out var packageDirectory))
        {
            error = "runtime_package_not_installed";
            return false;
        }

        RuntimeAudioEntry? selected = null;
        var triggerCandidateCount = 0;
        var exactTextCandidateCount = 0;
        if (!string.IsNullOrWhiteSpace(triggerType) && !string.IsNullOrWhiteSpace(triggerId))
        {
            var triggerMatches = _runtimeEntries.Where(item =>
                string.Equals(item.TriggerType, triggerType, StringComparison.Ordinal)
                && string.Equals(item.TriggerId, triggerId, StringComparison.Ordinal)).Take(2).ToList();
            triggerCandidateCount = triggerMatches.Count;
            if (triggerMatches.Count == 1)
            {
                selected = triggerMatches[0];
                matchMode = "saved_trigger";
            }
            else if (triggerMatches.Count > 1)
                error = "saved_trigger_is_ambiguous";
        }

        if (selected is null && error is null)
        {
            var textKey = NormalizeDialogueText(dialogueText);
            if (textKey.Length > 0)
            {
                var textMatch = FindUniqueTextMatch(_runtimeEntries, textKey, speakerKey,
                    out var textCandidates, out var textMatchKeyType);
                exactTextCandidateCount = textCandidates;
                if (textMatch is not null)
                {
                    selected = textMatch;
                    matchKeyType = textMatchKeyType;
                }
                else if (textCandidates > 1) error = "ambiguous_exact_text";
                if (selected is not null) matchMode = "exact_text";
            }
        }

        if (selected is null)
        {
            error ??= NormalizeDialogueText(dialogueText).Length == 0
                ? "dialogue_text_unavailable" : "approved_take_not_found";
            Log.LogWarning($"DubSync lookup details: package={Path.GetFileName(packageDirectory)}; entries={_runtimeEntries.Count}; trigger_candidates={triggerCandidateCount}; exact_text_candidates={exactTextCandidateCount}; speaker_key_present={!string.IsNullOrWhiteSpace(speakerKey)}; normalized_text_length={NormalizeDialogueText(dialogueText).Length}.");
            return false;
        }

        var fullPath = Path.GetFullPath(Path.Combine(packageDirectory, selected.AudioPath));
        var packagePrefix = packageDirectory.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
            + Path.DirectorySeparatorChar;
        if (!fullPath.StartsWith(packagePrefix, StringComparison.OrdinalIgnoreCase))
        {
            error = "audio_path_outside_active_package";
            return false;
        }
        if (!File.Exists(fullPath))
        {
            error = "approved_audio_file_missing";
            return false;
        }
        audioPath = fullPath;
        resolved = selected;
        return true;
    }

    private static RuntimeAudioEntry? FindUniqueTextMatch(List<RuntimeAudioEntry> entries, string textKey,
        string speakerKey, out int candidateCount, out string? matchKeyType)
    {
        matchKeyType = null;
        var candidates = entries.Where(item => item.SourceMatchKeys.Contains(textKey, StringComparer.Ordinal)
                || item.DubMatchKeys.Contains(textKey, StringComparer.Ordinal)
                || item.LegacyMatchKeys.Contains(textKey, StringComparer.Ordinal))
            .GroupBy(item => item.OccurrenceId, StringComparer.Ordinal)
            .Select(group => group.First()).ToList();
        candidateCount = candidates.Count;
        RuntimeAudioEntry? selected = null;
        if (candidateCount == 1) selected = candidates[0];
        else if (candidateCount > 1 && !string.IsNullOrWhiteSpace(speakerKey))
        {
            var normalizedSpeaker = NormalizeDialogueText(speakerKey);
            var speakerMatches = candidates.Where(item => item.SpeakerKey.Length > 0
                && string.Equals(NormalizeDialogueText(item.SpeakerKey), normalizedSpeaker,
                    StringComparison.Ordinal)).ToList();
            if (speakerMatches.Count == 1) selected = speakerMatches[0];
        }
        if (selected is null) return null;
        matchKeyType = selected.SourceMatchKeys.Contains(textKey, StringComparer.Ordinal)
            ? "source_text"
            : selected.DubMatchKeys.Contains(textKey, StringComparer.Ordinal) ? "dub_text" : "legacy_text";
        return selected;
    }

    private bool RefreshRuntimePackage(out string packageDirectory)
    {
        packageDirectory = "";
        var pluginRoot = Path.Combine(_gameRoot, "BepInEx", "plugins", "DubSync.ReplacedProbe");
        var packagesRoot = Path.GetFullPath(Path.Combine(pluginRoot, "packages"));
        var pointerPath = Path.Combine(pluginRoot, "active-package.json");
        try
        {
            _runtimeVolumePercent = 100;
            if (!File.Exists(pointerPath))
            {
                _runtimeEntries = new List<RuntimeAudioEntry>();
                _loadedPackageDirectory = null;
                return false;
            }

            using var pointer = JsonDocument.Parse(File.ReadAllText(pointerPath));
            if (pointer.RootElement.TryGetProperty("volume_percent", out var volumeValue)
                && volumeValue.TryGetSingle(out var volumePercent))
                _runtimeVolumePercent = Math.Clamp(volumePercent, 0f, 3162.2777f);
            if (!pointer.RootElement.TryGetProperty("directory", out var directoryValue)) return false;
            var directoryName = directoryValue.GetString();
            if (string.IsNullOrWhiteSpace(directoryName) || directoryName != Path.GetFileName(directoryName)
                || directoryName.Any(character => !(char.IsLetterOrDigit(character) || character is '-' or '_' or '.')))
                return false;
            packageDirectory = Path.GetFullPath(Path.Combine(packagesRoot, directoryName));
            if (!packageDirectory.StartsWith(packagesRoot.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar,
                    StringComparison.OrdinalIgnoreCase)) return false;
            var manifestPath = Path.Combine(packageDirectory, "runtime_manifest.json");
            if (!File.Exists(manifestPath)) return false;
            var manifestWriteUtc = File.GetLastWriteTimeUtc(manifestPath);
            if (string.Equals(_loadedPackageDirectory, packageDirectory, StringComparison.OrdinalIgnoreCase)
                && _loadedManifestWriteUtc == manifestWriteUtc)
                return true;

            using var manifest = JsonDocument.Parse(File.ReadAllText(manifestPath));
            if (!manifest.RootElement.TryGetProperty("entries", out var entriesElement)
                || entriesElement.ValueKind != JsonValueKind.Array) return false;
            var entries = new List<RuntimeAudioEntry>();
            foreach (var item in entriesElement.EnumerateArray())
            {
                var audioPath = item.TryGetProperty("audio", out var audio)
                    && audio.TryGetProperty("path", out var pathValue) ? pathValue.GetString() : null;
                if (string.IsNullOrWhiteSpace(audioPath)) continue;
                var keys = item.TryGetProperty("auto_match_keys", out var keysElement)
                    && keysElement.ValueKind == JsonValueKind.Array
                    ? keysElement.EnumerateArray().Where(value => value.ValueKind == JsonValueKind.String)
                        .Select(value => value.GetString() ?? "").Where(value => value.Length > 0)
                        .Distinct(StringComparer.Ordinal).ToArray()
                    : Array.Empty<string>();
                var hasSeparatedKeys = item.TryGetProperty("source_match_keys", out var sourceKeysElement)
                    || item.TryGetProperty("dub_match_keys", out _);
                var sourceKeys = sourceKeysElement.ValueKind == JsonValueKind.Array
                    ? sourceKeysElement.EnumerateArray().Where(value => value.ValueKind == JsonValueKind.String)
                        .Select(value => value.GetString() ?? "").Where(value => value.Length > 0)
                        .Distinct(StringComparer.Ordinal).ToArray()
                    : Array.Empty<string>();
                var dubKeys = item.TryGetProperty("dub_match_keys", out var dubKeysElement)
                    && dubKeysElement.ValueKind == JsonValueKind.Array
                    ? dubKeysElement.EnumerateArray().Where(value => value.ValueKind == JsonValueKind.String)
                        .Select(value => value.GetString() ?? "").Where(value => value.Length > 0)
                        .Distinct(StringComparer.Ordinal).ToArray()
                    : Array.Empty<string>();
                var occurrenceId = item.TryGetProperty("occurrence_id", out var occurrenceValue)
                    ? occurrenceValue.GetString() ?? "" : "";
                var takeId = item.TryGetProperty("take_id", out var takeValue) ? takeValue.GetString() ?? "" : "";
                var speakerKey = item.TryGetProperty("speaker_key", out var speakerValue)
                    ? speakerValue.GetString() ?? "" : "";
                string? triggerType = null;
                string? triggerId = null;
                if (item.TryGetProperty("key", out var keyElement)
                    && keyElement.ValueKind == JsonValueKind.Object)
                {
                    triggerType = keyElement.TryGetProperty("runtime_trigger_type", out var typeValue)
                        ? typeValue.GetString() : null;
                    triggerId = keyElement.TryGetProperty("runtime_trigger_id", out var idValue)
                        ? idValue.GetString() : null;
                }
                entries.Add(new RuntimeAudioEntry(occurrenceId, takeId, audioPath!, speakerKey,
                    sourceKeys, dubKeys, hasSeparatedKeys ? Array.Empty<string>() : keys,
                    triggerType, triggerId));
            }
            _runtimeEntries = entries;
            _loadedPackageDirectory = packageDirectory;
            _loadedManifestWriteUtc = manifestWriteUtc;
            Log.LogInfo($"Loaded DubSync runtime package {Path.GetFileName(packageDirectory)} with {entries.Count} approved takes; directory={packageDirectory}.");
            return true;
        }
        catch (Exception error)
        {
            Log.LogWarning($"DubSync runtime package could not be loaded: {error.GetBaseException().GetType().Name}: {error.GetBaseException().Message}");
            return false;
        }
    }

    private bool TryPlayWithGameFmod(string audioPath, out string? error)
    {
        error = null;
        var fmodAssembly = AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(assembly =>
            assembly.GetType("FMODUnity.RuntimeManager", false) is not null);
        if (fmodAssembly is null)
        {
            try { fmodAssembly = Assembly.Load("FMODUnity"); }
            catch { }
        }
        var runtimeManager = fmodAssembly?.GetType("FMODUnity.RuntimeManager", false);
        var coreSystem = runtimeManager?.GetProperty("CoreSystem", BindingFlags.Public | BindingFlags.Static)?.GetValue(null);
        if (fmodAssembly is null || runtimeManager is null || coreSystem is null)
        {
            error = "game_fmod_system_unavailable";
            return false;
        }

        var modeType = fmodAssembly.GetType("FMOD.MODE", false);
        if (modeType is null)
        {
            error = "fmod_mode_type_unavailable";
            return false;
        }
        var defaultMode = Enum.Parse(modeType, "DEFAULT", ignoreCase: true);
        var createSound = coreSystem.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .FirstOrDefault(method => method.Name == "createSound" && method.GetParameters().Length == 3
                && method.GetParameters()[0].ParameterType == typeof(string)
                && method.GetParameters()[2].ParameterType.IsByRef);
        if (createSound is null)
        {
            error = "fmod_create_sound_api_unavailable";
            return false;
        }
        var createArguments = new object?[] { audioPath, defaultMode, null };
        var createResult = createSound.Invoke(coreSystem, createArguments)?.ToString();
        if (!string.Equals(createResult, "OK", StringComparison.OrdinalIgnoreCase) || createArguments[2] is null)
        {
            error = $"fmod_create_sound_failed:{createResult ?? "unknown"}";
            return false;
        }
        var sound = createArguments[2]!;
        var playSound = coreSystem.GetType().GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .FirstOrDefault(method => method.Name == "playSound" && method.GetParameters().Length == 4
                && method.GetParameters()[0].ParameterType == sound.GetType()
                && method.GetParameters()[3].ParameterType.IsByRef);
        if (playSound is null)
        {
            sound.GetType().GetMethod("release", BindingFlags.Public | BindingFlags.Instance)?.Invoke(sound, null);
            error = "fmod_play_sound_api_unavailable";
            return false;
        }
        var playArguments = new object?[] { sound, null, false, null };
        var playResult = playSound.Invoke(coreSystem, playArguments)?.ToString();
        if (!string.Equals(playResult, "OK", StringComparison.OrdinalIgnoreCase) || playArguments[3] is null)
        {
            sound.GetType().GetMethod("release", BindingFlags.Public | BindingFlags.Instance)?.Invoke(sound, null);
            error = $"fmod_play_sound_failed:{playResult ?? "unknown"}";
            return false;
        }

        _currentSound = sound;
        var channel = playArguments[3]!;
        var setVolume = channel.GetType().GetMethod("setVolume", BindingFlags.Public | BindingFlags.Instance,
            binder: null, types: new[] { typeof(float) }, modifiers: null);
        if (setVolume is null)
        {
            if (!_volumeApiWarningLogged)
            {
                Log.LogWarning("FMOD channel setVolume is unavailable; DubSync audio will use the game default level.");
                _volumeApiWarningLogged = true;
            }
        }
        else
        {
            try
            {
                var volumeResult = setVolume.Invoke(channel, new object[] { _runtimeVolumePercent / 100f })?.ToString();
                if (!string.Equals(volumeResult, "OK", StringComparison.OrdinalIgnoreCase) && !_volumeApiWarningLogged)
                {
                    Log.LogWarning($"FMOD could not apply DubSync volume ({volumeResult ?? "unknown"}); using the game default level.");
                    _volumeApiWarningLogged = true;
                }
            }
            catch (Exception volumeError)
            {
                if (!_volumeApiWarningLogged)
                {
                    Log.LogWarning($"FMOD could not apply DubSync volume: {volumeError.GetBaseException().Message}");
                    _volumeApiWarningLogged = true;
                }
            }
        }
        _currentChannel = channel;
        return true;
    }

    private static string FormatSubtitleForLog(string? subtitle)
    {
        if (string.IsNullOrWhiteSpace(subtitle)) return "[texto indisponível]";
        var normalized = string.Join(" ", subtitle.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        if (normalized.Length > 400) normalized = normalized[..400] + "…";
        return JsonSerializer.Serialize(normalized);
    }

    private void StopCurrentPlayback()
    {
        lock (_playbackGate)
        {
            try
            {
                _currentChannel?.GetType().GetMethod("stop", BindingFlags.Public | BindingFlags.Instance)?.Invoke(_currentChannel, null);
            }
            catch (Exception error) { Log.LogDebug($"FMOD channel stop: {error.GetBaseException().Message}"); }
            try
            {
                _currentSound?.GetType().GetMethod("release", BindingFlags.Public | BindingFlags.Instance)?.Invoke(_currentSound, null);
            }
            catch (Exception error) { Log.LogDebug($"FMOD sound release: {error.GetBaseException().Message}"); }
            _currentChannel = null;
            _currentSound = null;
        }
    }

    private static object? ResolveDialogueFragment(object? articyRef, out string diagnostic)
    {
        diagnostic = "reference_missing";
        if (articyRef is null || articyRef.GetType().FullName != "Articy.Unity.ArticyRef") return null;
        try
        {
            var referenceType = articyRef.GetType();
            var fragmentType = referenceType.Assembly.GetType("Articy.Replaced.DialogueFragment")
                ?? AppDomain.CurrentDomain.GetAssemblies()
                    .Select(assembly => assembly.GetType("Articy.Replaced.DialogueFragment", false))
                    .FirstOrDefault(candidate => candidate is not null);
            if (fragmentType is null) { diagnostic = "dialogue_fragment_type_missing"; return null; }
            var getter = referenceType.GetMethods(BindingFlags.Instance | BindingFlags.Public)
                .FirstOrDefault(method => method.Name == "GetObject" && method.IsGenericMethodDefinition
                    && method.GetParameters().Length == 0);
            if (getter is null) { diagnostic = "typed_get_object_missing"; return null; }
            var fragment = getter.MakeGenericMethod(fragmentType).Invoke(articyRef, null);
            if (fragment is null || fragment.GetType() != fragmentType)
            {
                diagnostic = "referenced_object_not_dialogue_fragment";
                return null;
            }
            diagnostic = "dialogue_fragment_resolved";
            return fragment;
        }
        catch (Exception error)
        {
            diagnostic = $"fragment_error:{error.GetBaseException().GetType().Name}:{error.GetBaseException().Message}";
            return null;
        }
    }

    private static string? ExtractDialogueText(object? fragment)
    {
        if (fragment is null) return null;
        var type = fragment.GetType();
        foreach (var memberName in new[] { "Text", "text", "DialogueText", "dialogueText" })
        {
            var value = ReadMember(fragment, type, memberName);
            var text = ReadLocalizedText(value);
            if (!string.IsNullOrWhiteSpace(text)) return text;
        }
        return null;
    }

    private static string? ReadLocalizedText(object? value)
    {
        if (value is null) return null;
        if (value is string direct) return IsDialogueText(direct) ? direct.Trim() : null;

        var type = value.GetType();
        foreach (var memberName in new[] { "Value", "UnresolvedValue", "mValue" })
        {
            var localized = ReadMember(value, type, memberName);
            if (localized is string text && IsDialogueText(text)) return text.Trim();
        }

        // Articy multi-language wrappers do not stringify to their displayed line.
        if (string.Equals(type.FullName, "Articy.Unity.ArticyMultiLanguageString", StringComparison.Ordinal))
            return null;
        var fallback = value.ToString();
        return fallback is not null && IsDialogueText(fallback) ? fallback.Trim() : null;
    }

    private static bool IsDialogueText(string text) =>
        !string.IsNullOrWhiteSpace(text) && text.Length <= 8192 && !text.Any(char.IsControl);

    private static string ExtractSpeakerKey(object? fragment)
    {
        if (fragment is null) return "";
        var type = fragment.GetType();
        foreach (var memberName in new[] { "Speaker", "speaker", "SpeakerName", "speakerName", "Character", "character", "Actor", "actor" })
        {
            var value = ReadMember(fragment, type, memberName);
            if (value is null) continue;
            var nestedType = value.GetType();
            foreach (var nestedName in new[] { "DisplayName", "displayName", "Name", "name", "TechnicalName", "technicalName", "ShortName", "shortName" })
            {
                var nested = ReadMember(value, nestedType, nestedName);
                var key = NormalizeDialogueText(nested?.ToString());
                if (key.Length > 0 && key.Length < 120) return key;
            }
            if (value is string || nestedType.IsPrimitive || nestedType.IsEnum)
            {
                var direct = NormalizeDialogueText(value.ToString());
                if (direct.Length > 0 && direct.Length < 120) return direct;
            }
            else
            {
                var direct = NormalizeDialogueText(value.ToString());
                if (direct.Length > 0 && direct.Length < 120
                    && !direct.Contains("unityengine", StringComparison.Ordinal)
                    && !direct.Contains("articyref", StringComparison.Ordinal)
                    && !direct.Contains("articy", StringComparison.Ordinal)) return direct;
            }
        }
        return "";
    }

    private static string NormalizeDialogueText(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return "";
        var decomposed = value.Normalize(NormalizationForm.FormKD).ToLowerInvariant();
        var normalized = new StringBuilder(decomposed.Length);
        foreach (var character in decomposed)
        {
            var category = char.GetUnicodeCategory(character);
            if (category is UnicodeCategory.NonSpacingMark or UnicodeCategory.SpacingCombiningMark
                or UnicodeCategory.EnclosingMark) continue;
            if (char.IsLetterOrDigit(character)) normalized.Append(character);
            else if (normalized.Length > 0 && normalized[^1] != ' ') normalized.Append(' ');
        }
        var tokens = normalized.ToString().Split(' ', StringSplitOptions.RemoveEmptyEntries);
        var canonical = new List<string>(tokens.Length);
        for (var index = 0; index < tokens.Length;)
        {
            if (tokens[index].Length != 1 || !char.IsLetter(tokens[index][0]))
            {
                canonical.Add(tokens[index++]);
                continue;
            }
            var end = index + 1;
            while (end < tokens.Length && tokens[end].Length == 1 && char.IsLetter(tokens[end][0])) end++;
            canonical.Add(end - index >= 3
                ? string.Concat(tokens, index, end - index)
                : string.Join(" ", tokens, index, end - index));
            index = end;
        }
        return string.Join(" ", canonical);
    }

    private static string? ExtractExternalId(object? value, out string diagnostic)
    {
        var visited = new HashSet<object>(ReferenceEqualityComparer.Instance);
        var steps = new List<string>();
        var externalId = ExtractExternalId(value, visited, 0, steps);
        diagnostic = string.Join(";", steps);
        return externalId;
    }

    private static string? ExtractArticyObjectId(object? articyRef, out string diagnostic)
    {
        diagnostic = "reference_missing";
        if (articyRef is null || articyRef.GetType().FullName != "Articy.Unity.ArticyRef") return null;
        try
        {
            var referenceType = articyRef.GetType();
            var fragmentType = AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType("Articy.Replaced.DialogueFragment", false))
                .FirstOrDefault(candidate => candidate is not null);
            if (fragmentType is null) { diagnostic = "dialogue_fragment_type_missing"; return null; }
            var typedGetObject = referenceType.GetMethods(BindingFlags.Instance | BindingFlags.Public)
                .FirstOrDefault(method => method.Name == "GetObject" && method.IsGenericMethodDefinition
                    && method.GetParameters().Length == 0);
            if (typedGetObject is null) { diagnostic = "typed_get_object_missing"; return null; }
            var fragment = typedGetObject.MakeGenericMethod(fragmentType).Invoke(articyRef, null);
            if (fragment is null || fragment.GetType() != fragmentType)
            {
                diagnostic = "referenced_object_not_dialogue_fragment";
                return null;
            }
            var referenceId = ReadMember(articyRef, referenceType, "id");
            var objectId = ReadMember(fragment, fragmentType, "Id");
            if (referenceId is null || objectId is null
                || !ulong.TryParse(Convert.ToString(referenceId, CultureInfo.InvariantCulture),
                    NumberStyles.None, CultureInfo.InvariantCulture, out var refNumber)
                || !ulong.TryParse(Convert.ToString(objectId, CultureInfo.InvariantCulture),
                    NumberStyles.None, CultureInfo.InvariantCulture, out var objectNumber)
                || refNumber == 0 || refNumber != objectNumber)
            {
                diagnostic = $"reference_object_id_mismatch:ref={referenceId ?? "null"},object={objectId ?? "null"}";
                return null;
            }
            diagnostic = "verified_dialogue_fragment_id";
            return objectNumber.ToString(CultureInfo.InvariantCulture);
        }
        catch (Exception error)
        {
            diagnostic = $"object_id_error:{error.GetBaseException().GetType().Name}:{error.GetBaseException().Message}";
            return null;
        }
    }

    private static string? ExtractExternalId(object? value, HashSet<object> visited, int depth, List<string> steps)
    {
        if (value is null || depth > 3 || !visited.Add(value)) return null;
        var type = value.GetType();
        foreach (var name in new[] { "ExternalId", "ExternalID", "externalId" })
        {
            var memberValue = ReadMember(value, type, name);
            var text = memberValue?.ToString()?.Trim();
            if (!string.IsNullOrEmpty(text) && text.Length <= 160 && !text.Any(char.IsControl)) return text;
        }
        steps.Add($"type={type.FullName}");
        if (string.Equals(type.FullName, "Articy.Unity.ArticyRef", StringComparison.Ordinal))
        {
            // InitLine receives an ArticyRef. The ExternalId lives on the referenced
            // DialogueFragment, not on the reference's internal numeric id.
            var fragmentType = type.Assembly.GetType("Articy.Replaced.DialogueFragment")
                ?? AppDomain.CurrentDomain.GetAssemblies()
                    .Select(assembly => assembly.GetType("Articy.Replaced.DialogueFragment", false))
                    .FirstOrDefault(candidate => candidate is not null);
            steps.Add($"fragment_type={(fragmentType is null ? "missing" : "found")}");
            if (fragmentType is not null)
            {
                try
                {
                    var typedGetObject = type.GetMethods(BindingFlags.Instance | BindingFlags.Public)
                        .FirstOrDefault(method => method.Name == "GetObject" && method.IsGenericMethodDefinition
                            && method.GetParameters().Length == 0);
                    steps.Add($"typed_get_object={(typedGetObject is null ? "missing" : "found")}");
                    if (typedGetObject is not null)
                    {
                        var fragment = typedGetObject.MakeGenericMethod(fragmentType).Invoke(value, null);
                        steps.Add($"typed_result={fragment?.GetType().FullName ?? "null"}");
                        var found = ExtractExternalId(fragment, visited, depth + 1, steps);
                        if (found is not null) return found;
                    }
                }
                catch (Exception error) { steps.Add($"typed_error={error.GetBaseException().GetType().Name}:{error.GetBaseException().Message}"); }
            }
            try
            {
                var getObject = type.GetMethods(BindingFlags.Instance | BindingFlags.Public)
                    .FirstOrDefault(method => method.Name == "GetObject" && !method.IsGenericMethod
                        && method.GetParameters().Length == 0);
                steps.Add($"get_object={(getObject is null ? "missing" : "found")}");
                var articyObject = getObject?.Invoke(value, null);
                steps.Add($"object_result={articyObject?.GetType().FullName ?? "null"}");
                if (articyObject is not null && fragmentType is not null)
                {
                    var tryCast = articyObject.GetType().GetMethods(BindingFlags.Instance | BindingFlags.Public)
                        .FirstOrDefault(method => method.Name == "TryCast" && method.IsGenericMethodDefinition
                            && method.GetParameters().Length == 0);
                    steps.Add($"try_cast={(tryCast is null ? "missing" : "found")}");
                    var fragment = tryCast?.MakeGenericMethod(fragmentType).Invoke(articyObject, null);
                    steps.Add($"cast_result={fragment?.GetType().FullName ?? "null"}");
                    var found = ExtractExternalId(fragment, visited, depth + 1, steps);
                    if (found is not null) return found;
                }
            }
            catch (Exception error) { steps.Add($"object_error={error.GetBaseException().GetType().Name}:{error.GetBaseException().Message}"); }
        }
        foreach (var name in new[] { "ArticyObject", "Object", "Target", "ReferencedObject", "Reference" })
        {
            var nested = ReadMember(value, type, name);
            var found = ExtractExternalId(nested, visited, depth + 1, steps);
            if (found is not null) return found;
        }
        return null;
    }

    private static object? ReadMember(object value, Type type, string name)
    {
        try
        {
            var property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property is not null && property.GetIndexParameters().Length == 0) return property.GetValue(value);
            return type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)?.GetValue(value);
        }
        catch { return null; }
    }

    private static string DescribeValue(object? value)
    {
        if (value is null) return "<null>";
        var type = value.GetType();
        var members = new[] { "ExternalId", "ExternalID", "externalId", "ShortId", "Id", "id", "instanceId", "ID", "ArticyObject", "Object" };
        var details = string.Join(", ", members.Select(name =>
        {
            var memberValue = ReadMember(value, type, name);
            return memberValue is null ? null : $"{name}={memberValue}";
        }).Where(item => item is not null));
        string preview;
        try { preview = value.ToString() ?? ""; }
        catch { preview = "<ToString failed>"; }
        if (preview.Length > 240) preview = preview[..240];
        return $"{type.FullName}({details}) '{preview}'";
    }
}

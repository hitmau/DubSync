using System.Text.Json;
using System.IO;

namespace DubSync.Desktop.Services;

public sealed class DesktopPreferences
{
    public int ApiPort { get; set; } = 8766;
    public double UiScale { get; set; } = 1.0;
    public double FontScale { get; set; } = 1.0;
    public string? ActiveProjectId { get; set; }
    public string ActivePage { get; set; } = "projects";
    public string ActiveCaptureMode { get; set; } = "video";
    public double? WindowLeft { get; set; }
    public double? WindowTop { get; set; }
    public double? WindowWidth { get; set; }
    public double? WindowHeight { get; set; }
    public bool WindowMaximized { get; set; }
}

public sealed class DesktopPreferencesStore
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };

    public string DirectoryPath { get; } = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "DubSync", "Desktop");

    public string PreferencesPath => Path.Combine(DirectoryPath, "settings.json");
    public string LayoutPath => Path.Combine(DirectoryPath, "layout.json");
    public string MyLayoutPath => Path.Combine(DirectoryPath, "my-layout.json");

    public DesktopPreferences Load()
    {
        try
        {
            if (File.Exists(PreferencesPath))
            {
                var saved = JsonSerializer.Deserialize<DesktopPreferences>(File.ReadAllText(PreferencesPath));
                if (saved is not null && saved.ApiPort is >= 1 and <= 65535 &&
                    saved.UiScale is >= 0.8 and <= 1.6 && saved.FontScale is >= 0.8 and <= 1.5)
                    return saved;
            }
        }
        catch (IOException) { }
        catch (JsonException) { }

        return new DesktopPreferences { ApiPort = ReadLauncherPort() };
    }

    public void Save(DesktopPreferences preferences)
    {
        Directory.CreateDirectory(DirectoryPath);
        var temporaryPath = PreferencesPath + ".tmp";
        File.WriteAllText(temporaryPath, JsonSerializer.Serialize(preferences, JsonOptions));
        File.Move(temporaryPath, PreferencesPath, overwrite: true);
    }

    private static int ReadLauncherPort()
    {
        var portPath = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "DubSync", "port.txt");
        try
        {
            if (int.TryParse(File.ReadAllText(portPath).Trim(), out var port) && port is >= 1 and <= 65535)
                return port;
        }
        catch (IOException) { }
        return 8766;
    }
}

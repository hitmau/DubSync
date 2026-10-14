using System.Net.Http;
using System.Net.Http.Json;
using DubSync.Desktop.Models;

namespace DubSync.Desktop.Services;

public sealed class DubSyncApiClient : IDisposable
{
    private readonly HttpClient _http;

    public int Port { get; }
    public Uri BaseAddress { get; }
    public string Endpoint => BaseAddress.ToString().TrimEnd('/');

    public DubSyncApiClient(int port)
    {
        Port = port;
        BaseAddress = new Uri($"http://127.0.0.1:{port}/", UriKind.Absolute);
        _http = new HttpClient { BaseAddress = BaseAddress, Timeout = TimeSpan.FromSeconds(5) };
    }

    public void SetProjectScope(string? projectId)
    {
        _http.DefaultRequestHeaders.Remove("X-DubSync-Project");
        if (!string.IsNullOrWhiteSpace(projectId))
            _http.DefaultRequestHeaders.Add("X-DubSync-Project", projectId);
    }

    public async Task<ApiHealth> GetHealthAsync(CancellationToken cancellationToken = default)
    {
        return await _http.GetFromJsonAsync<ApiHealth>("api/health", cancellationToken)
            ?? throw new InvalidOperationException("O serviço DubSync não retornou o estado de saúde.");
    }

    public async Task<IReadOnlyList<ProjectSummary>> GetProjectsAsync(CancellationToken cancellationToken = default)
    {
        var response = await _http.GetFromJsonAsync<ProjectListResponse>(
            "api/projects?status=all&sort=activity", cancellationToken)
            ?? throw new InvalidOperationException("O serviço DubSync não retornou a lista de projetos.");

        foreach (var project in response.Projects)
        {
            if (!string.IsNullOrWhiteSpace(project.CoverPath))
                project.CoverUrl = new Uri(BaseAddress,
                    $"api/projects/{Uri.EscapeDataString(project.ProjectId)}/cover").AbsoluteUri;
        }
        return response.Projects;
    }

    public async Task<ProjectSummary> OpenProjectAsync(string projectId, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync(
            $"api/projects/{Uri.EscapeDataString(projectId)}/open", new { }, cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        var opened = await response.Content.ReadFromJsonAsync<ProjectOpenResponse>(cancellationToken: cancellationToken)
            ?? throw new InvalidOperationException("O DubSync não confirmou a abertura do projeto.");
        if (!string.IsNullOrWhiteSpace(opened.Project.CoverPath))
            opened.Project.CoverUrl = new Uri(BaseAddress,
                $"api/projects/{Uri.EscapeDataString(opened.Project.ProjectId)}/cover").AbsoluteUri;
        return opened.Project;
    }

    public async Task<ProjectSummary> CreateProjectAsync(ProjectCreateRequest project, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync("api/projects", project, cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        var created = await response.Content.ReadFromJsonAsync<ProjectSummary>(cancellationToken: cancellationToken)
            ?? throw new InvalidOperationException("O DubSync não retornou o projeto criado.");
        if (!string.IsNullOrWhiteSpace(created.CoverPath))
            created.CoverUrl = new Uri(BaseAddress,
                $"api/projects/{Uri.EscapeDataString(created.ProjectId)}/cover").AbsoluteUri;
        return created;
    }

    public async Task<IReadOnlyList<CaptureSession>> GetSessionsAsync(string projectId, CancellationToken cancellationToken = default)
    {
        var sessions = await _http.GetFromJsonAsync<CaptureSessionListResponse>(
            $"api/sessions?project_id={Uri.EscapeDataString(projectId)}", cancellationToken)
            ?? throw new InvalidOperationException("O DubSync não retornou as sessões do projeto.");
        return sessions.Sessions;
    }

    public async Task<IReadOnlyList<ReviewEvent>> GetEventsAsync(string? sessionId = null, string? status = null,
        CancellationToken cancellationToken = default)
    {
        var query = new List<string>();
        if (!string.IsNullOrWhiteSpace(sessionId)) query.Add($"session_id={Uri.EscapeDataString(sessionId)}");
        if (!string.IsNullOrWhiteSpace(status) && status != "all") query.Add($"status={Uri.EscapeDataString(status)}");
        var route = "api/events" + (query.Count > 0 ? "?" + string.Join("&", query) : "");
        var result = await _http.GetFromJsonAsync<EventListResponse>(route, cancellationToken)
            ?? throw new InvalidOperationException("O DubSync não retornou os diálogos para revisão.");
        return result.Events;
    }

    public async Task<System.Text.Json.JsonElement> ReviewEventAsync(string eventId, string action, object? changes = null,
        CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync(
            $"api/events/{Uri.EscapeDataString(eventId)}/review/{Uri.EscapeDataString(action)}",
            changes ?? new { }, cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        return await response.Content.ReadFromJsonAsync<System.Text.Json.JsonElement>(cancellationToken: cancellationToken);
    }

    public async Task<byte[]> GetEventAssetAsync(string projectId, string sessionId, string assetPath,
        CancellationToken cancellationToken = default)
    {
        var encodedPath = string.Join("/", assetPath.Split('/', StringSplitOptions.RemoveEmptyEntries).Select(Uri.EscapeDataString));
        var route = $"assets/{Uri.EscapeDataString(sessionId)}/{encodedPath}?project_id={Uri.EscapeDataString(projectId)}";
        using var response = await _http.GetAsync(route, cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        return await response.Content.ReadAsByteArrayAsync(cancellationToken);
    }

    public async Task<FilePreview> GetFilePreviewAsync(string path, double start, CancellationToken cancellationToken = default)
    {
        var route = $"api/file-preview?path={Uri.EscapeDataString(path)}&start={start.ToString(System.Globalization.CultureInfo.InvariantCulture)}";
        using var response = await _http.GetAsync(route, cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        return await response.Content.ReadFromJsonAsync<FilePreview>(cancellationToken: cancellationToken)
            ?? throw new InvalidOperationException("O DubSync não retornou a prévia do vídeo.");
    }

    public async Task<IReadOnlyList<RuntimeWindow>> GetRuntimeWindowsAsync(CancellationToken cancellationToken = default)
    {
        var result = await _http.GetFromJsonAsync<RuntimeWindowListResponse>(
            "api/runtime-capture/processes", cancellationToken)
            ?? throw new InvalidOperationException("O DubSync não retornou as janelas disponíveis.");
        return result.Windows;
    }

    public async Task<RuntimePendingEventList> GetRuntimePendingAsync(string sessionId, CancellationToken cancellationToken = default)
    {
        return await _http.GetFromJsonAsync<RuntimePendingEventList>(
            $"api/runtime-capture/{Uri.EscapeDataString(sessionId)}/pending", cancellationToken)
            ?? throw new InvalidOperationException("O DubSync não retornou os textos recentes.");
    }

    public async Task<System.Text.Json.JsonElement> GetRuntimeStatusAsync(string sessionId, CancellationToken cancellationToken = default)
    {
        using var response = await _http.GetAsync(
            $"api/runtime-capture/{Uri.EscapeDataString(sessionId)}/status", cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        return await response.Content.ReadFromJsonAsync<System.Text.Json.JsonElement>(cancellationToken: cancellationToken);
    }

    public async Task<System.Text.Json.JsonElement> PostAsync(string route, object body, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PostAsJsonAsync(route, body, cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        return await response.Content.ReadFromJsonAsync<System.Text.Json.JsonElement>(cancellationToken: cancellationToken);
    }

    public async Task<System.Text.Json.JsonElement> PutAsync(string route, object body, CancellationToken cancellationToken = default)
    {
        using var response = await _http.PutAsJsonAsync(route, body, cancellationToken);
        await EnsureSuccessAsync(response, cancellationToken);
        return await response.Content.ReadFromJsonAsync<System.Text.Json.JsonElement>(cancellationToken: cancellationToken);
    }

    public Task<System.Text.Json.JsonElement> PostEmptyAsync(string route, CancellationToken cancellationToken = default) =>
        PostAsync(route, new { }, cancellationToken);

    private static async Task EnsureSuccessAsync(HttpResponseMessage response, CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode) return;
        var body = await response.Content.ReadAsStringAsync(cancellationToken);
        var message = body;
        try
        {
            using var document = System.Text.Json.JsonDocument.Parse(body);
            if (document.RootElement.TryGetProperty("detail", out var detail))
                message = detail.ValueKind == System.Text.Json.JsonValueKind.String ? detail.GetString() ?? body : detail.ToString();
            else if (document.RootElement.TryGetProperty("message", out var text))
                message = text.GetString() ?? body;
        }
        catch (System.Text.Json.JsonException) { }

        if (string.IsNullOrWhiteSpace(message)) message = $"HTTP {(int)response.StatusCode} {response.ReasonPhrase}";
        throw new InvalidOperationException(message);
    }

    public void Dispose() => _http.Dispose();
}

public sealed class ProjectCreateRequest
{
    [System.Text.Json.Serialization.JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("game_title")]
    public string GameTitle { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("slug")]
    public string Slug { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("game_version")]
    public string? GameVersion { get; set; }

    [System.Text.Json.Serialization.JsonPropertyName("platform")]
    public string Platform { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("narrative_type")]
    public string NarrativeType { get; set; } = "unknown";

    [System.Text.Json.Serialization.JsonPropertyName("original_language")]
    public string OriginalLanguage { get; set; } = "en";

    [System.Text.Json.Serialization.JsonPropertyName("capture_language")]
    public string CaptureLanguage { get; set; } = "en";

    [System.Text.Json.Serialization.JsonPropertyName("dub_language")]
    public string DubLanguage { get; set; } = "pt-BR";

    [System.Text.Json.Serialization.JsonPropertyName("notes")]
    public string Notes { get; set; } = "";

    [System.Text.Json.Serialization.JsonPropertyName("cover_data")]
    public string? CoverData { get; set; }
}

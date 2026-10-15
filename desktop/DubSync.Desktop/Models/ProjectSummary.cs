using System.Text.Json.Serialization;

namespace DubSync.Desktop.Models;

public sealed class ProjectSummary
{
    [JsonPropertyName("project_id")]
    public string ProjectId { get; set; } = "";

    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("game_title")]
    public string GameTitle { get; set; } = "";

    [JsonPropertyName("slug")]
    public string Slug { get; set; } = "";

    [JsonPropertyName("capture_language")]
    public string CaptureLanguage { get; set; } = "en";

    [JsonPropertyName("status")]
    public string Status { get; set; } = "active";

    [JsonPropertyName("cover_path")]
    public string? CoverPath { get; set; }

    [JsonPropertyName("last_opened_at")]
    public string? LastOpenedAt { get; set; }

    [JsonPropertyName("summary")]
    public ProjectProgress Summary { get; set; } = new();

    [JsonIgnore]
    public string StatusLabel => Status switch
    {
        "active" => "ATIVO",
        "paused" => "PAUSADO",
        "completed" => "CONCLUÍDO",
        "archived" => "ARQUIVADO",
        _ => Status.ToUpperInvariant()
    };

    [JsonIgnore]
    public string StageLabel => Summary.CurrentStage switch
    {
        "capture" => "Captura",
        "review" => "Revisão",
        "dubbing" => "Dublagem",
        "complete" => "Finalizado",
        _ => "Captura"
    };

    [JsonIgnore]
    public string CoverUrl { get; set; } = "";
}

public sealed class ProjectProgress
{
    [JsonPropertyName("sessions")]
    public int Sessions { get; set; }

    [JsonPropertyName("dialogues")]
    public int Dialogues { get; set; }

    [JsonPropertyName("reviewed")]
    public int Reviewed { get; set; }

    [JsonPropertyName("characters")]
    public int Characters { get; set; }

    [JsonPropertyName("takes")]
    public int Takes { get; set; }

    [JsonPropertyName("approved_takes")]
    public int ApprovedTakes { get; set; }

    [JsonPropertyName("progress_percent")]
    public double ProgressPercent { get; set; }

    [JsonPropertyName("current_stage")]
    public string CurrentStage { get; set; } = "capture";

    [JsonPropertyName("stage_progress_percent")]
    public double StageProgressPercent { get; set; }

    [JsonPropertyName("last_activity")]
    public string? LastActivity { get; set; }
}

public sealed class ProjectListResponse
{
    [JsonPropertyName("projects")]
    public List<ProjectSummary> Projects { get; set; } = [];
}

public sealed class ProjectOpenResponse
{
    [JsonPropertyName("project")]
    public ProjectSummary Project { get; set; } = new();

    [JsonPropertyName("url")]
    public string Url { get; set; } = "";
}

public sealed class ApiHealth
{
    [JsonPropertyName("ok")]
    public bool Ok { get; set; }

    [JsonPropertyName("app")]
    public string App { get; set; } = "";

    [JsonPropertyName("capture_fps_target")]
    public int CaptureFpsTarget { get; set; }

    [JsonPropertyName("analysis_fps")]
    public int AnalysisFps { get; set; }
}

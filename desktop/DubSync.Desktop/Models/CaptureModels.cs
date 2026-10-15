using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace DubSync.Desktop.Models;

public sealed class CaptureSessionListResponse
{
    [JsonPropertyName("sessions")]
    public List<CaptureSession> Sessions { get; set; } = [];
}

public sealed class CaptureSession
{
    [JsonPropertyName("session_id")]
    public string SessionId { get; set; } = "";

    [JsonPropertyName("source_type")]
    public string SourceType { get; set; } = "";

    [JsonPropertyName("source_uri")]
    public string SourceUri { get; set; } = "";

    [JsonPropertyName("source_start")]
    public double SourceStart { get; set; }

    [JsonPropertyName("requested_duration")]
    public double RequestedDuration { get; set; }

    [JsonPropertyName("state")]
    public string State { get; set; } = "";

    [JsonPropertyName("language")]
    public string Language { get; set; } = "";

    [JsonPropertyName("current_source_time")]
    public double? CurrentSourceTime { get; set; }

    [JsonPropertyName("progress")]
    public CaptureProgress? Progress { get; set; }

    [JsonPropertyName("ready_for_calibration")]
    public bool ReadyForCalibration { get; set; }

    [JsonPropertyName("config_json")]
    public JsonElement Config { get; set; }

    [JsonIgnore]
    public string StateLabel => State switch
    {
        "queued" => "Na fila",
        "downloading" => "Baixando",
        "preparing" => "Preparando",
        "running" => "Analisando",
        "pausing" => "Pausando",
        "paused" => "Pausado · pronto para retomar",
        "stopping" => "Encerrando",
        "stopped" => "Interrompido",
        "complete" => "Concluído",
        "failed" => "Falhou",
        _ => string.IsNullOrWhiteSpace(State) ? "Desconhecido" : State
    };

    [JsonIgnore]
    public string TimeLabel => FormatTime(CurrentSourceTime ?? SourceStart);

    [JsonIgnore]
    public string DurationLabel => $"{RequestedDuration / 60:0.#} min";

    [JsonIgnore]
    public bool IsPaused => State == "paused";

    [JsonIgnore]
    public bool IsRunning => State is "queued" or "downloading" or "running" or "processing" or "pausing" or "stopping"
        || State == "preparing" && (!ReadyForCalibration || AnalysisStarted);

    [JsonIgnore]
    public bool CanContinue => State is "paused" or "stopped" or "failed";

    [JsonIgnore]
    public bool CanContinueYoutube => SourceType != "youtube" || State == "paused" ||
        (!AnalysisStarted && (State is "stopped" or "failed"));

    [JsonIgnore]
    public bool AnalysisStarted => Config.ValueKind == JsonValueKind.Object &&
        Config.TryGetProperty("analysis_started", out var started) && started.ValueKind == JsonValueKind.True;

    [JsonIgnore]
    public bool CanStartYoutubeAnalysis => SourceType == "youtube" && State == "preparing" &&
        ReadyForCalibration && !AnalysisStarted;

    [JsonIgnore]
    public bool IsComplete => State == "complete";

    private static string FormatTime(double? seconds)
    {
        if (seconds is null || !double.IsFinite(seconds.Value) || seconds < 0) return "—";
        var value = TimeSpan.FromSeconds(seconds.Value);
        return value.TotalHours >= 1 ? value.ToString(@"h\:mm\:ss", CultureInfo.InvariantCulture) : value.ToString(@"mm\:ss", CultureInfo.InvariantCulture);
    }
}

public sealed class CaptureProgress
{
    [JsonPropertyName("percent")]
    public double? Percent { get; set; }

    [JsonPropertyName("download_status")]
    public string DownloadStatus { get; set; } = "";

    [JsonPropertyName("stage")]
    public string Stage { get; set; } = "";

    [JsonPropertyName("activity_message")]
    public string ActivityMessage { get; set; } = "";

    [JsonPropertyName("current_source_time")]
    public double? CurrentSourceTime { get; set; }

    [JsonPropertyName("frames_seen")]
    public int FramesSeen { get; set; }

    [JsonPropertyName("frames_analyzed")]
    public int FramesAnalyzed { get; set; }

    [JsonPropertyName("ocr_calls")]
    public int OcrCalls { get; set; }

    [JsonPropertyName("ocr_failures")]
    public int OcrFailures { get; set; }

    [JsonPropertyName("events_created")]
    public int EventsCreated { get; set; }
}

public sealed class FilePreview
{
    [JsonPropertyName("image")]
    public string? Image { get; set; }

    [JsonPropertyName("video_duration")]
    public double VideoDuration { get; set; }

    [JsonPropertyName("width")]
    public int Width { get; set; }

    [JsonPropertyName("height")]
    public int Height { get; set; }
}

public sealed class RuntimeWindowListResponse
{
    [JsonPropertyName("windows")]
    public List<RuntimeWindow> Windows { get; set; } = [];
}

public sealed class RuntimeWindow
{
    [JsonPropertyName("hwnd")]
    public long Handle { get; set; }

    [JsonPropertyName("title")]
    public string Title { get; set; } = "";

    [JsonPropertyName("process_name")]
    public string ProcessName { get; set; } = "";

    [JsonPropertyName("width")]
    public int Width { get; set; }

    [JsonPropertyName("height")]
    public int Height { get; set; }

    [JsonPropertyName("foreground")]
    public bool IsForeground { get; set; }

    [JsonIgnore]
    public string DisplayLabel => $"{(IsForeground ? "●  " : "   ")}{Title} · {ProcessName} · {Width} × {Height}";
}

public sealed class RuntimePendingEventList
{
    [JsonPropertyName("events")]
    public List<RuntimePendingEvent> Events { get; set; } = [];
}

public sealed class RuntimePendingEvent
{
    [JsonPropertyName("event_id")]
    public string EventId { get; set; } = "";

    [JsonPropertyName("sequence")]
    public int Sequence { get; set; }

    [JsonPropertyName("start_time")]
    public double StartTime { get; set; }

    [JsonPropertyName("processed_text")]
    public string Text { get; set; } = "";

    [JsonPropertyName("character")]
    public string Character { get; set; } = "";

    [JsonIgnore]
    public string DisplayLabel
    {
        get
        {
            var time = TimeSpan.FromSeconds(Math.Max(0, StartTime)).ToString(@"mm\:ss", CultureInfo.InvariantCulture);
            return $"{time}  ·  {(!string.IsNullOrWhiteSpace(Character) ? Character + ": " : "")}{Text}";
        }
    }
}

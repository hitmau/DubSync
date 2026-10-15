using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace DubSync.Desktop.Models;

public sealed class EventListResponse
{
    [JsonPropertyName("events")]
    public List<ReviewEvent> Events { get; set; } = [];
}

public sealed class ReviewEvent
{
    [JsonPropertyName("event_id")]
    public string EventId { get; set; } = "";

    [JsonPropertyName("session_id")]
    public string SessionId { get; set; } = "";

    [JsonPropertyName("sequence")]
    public int Sequence { get; set; }

    [JsonPropertyName("start_time")]
    public double StartTime { get; set; }

    [JsonPropertyName("end_time")]
    public double? EndTime { get; set; }

    [JsonPropertyName("original_text")]
    public string OriginalText { get; set; } = "";

    [JsonPropertyName("processed_text")]
    public string? ProcessedText { get; set; }

    [JsonPropertyName("corrected_text")]
    public string? CorrectedText { get; set; }

    [JsonPropertyName("character")]
    public string? Character { get; set; }

    [JsonPropertyName("emotion")]
    public string? Emotion { get; set; }

    [JsonPropertyName("confidence")]
    public double Confidence { get; set; }

    [JsonPropertyName("review_status")]
    public string ReviewStatus { get; set; } = "pending";

    [JsonPropertyName("best_frame_path")]
    public string? BestFramePath { get; set; }

    [JsonPropertyName("crop_path")]
    public string? CropPath { get; set; }

    [JsonPropertyName("assets_json")]
    public JsonElement Assets { get; set; }

    [JsonPropertyName("ocr_observations_json")]
    public JsonElement OcrObservations { get; set; }

    [JsonPropertyName("occurrence_ids")]
    public List<string> OccurrenceIds { get; set; } = [];

    [JsonIgnore]
    public string TimeLabel => FormatTime(StartTime);

    [JsonIgnore]
    public string EndLabel => EndTime is null ? "—" : FormatTime(EndTime.Value);

    [JsonIgnore]
    public string TextForList => !string.IsNullOrWhiteSpace(CorrectedText)
        ? CorrectedText! : !string.IsNullOrWhiteSpace(ProcessedText) ? ProcessedText! : OriginalText;

    [JsonIgnore]
    public string CharacterLabel => string.IsNullOrWhiteSpace(Character) ? "—" : Character!;

    [JsonIgnore]
    public string StatusLabel => ReviewStatus switch
    {
        "pending" => "Pendente",
        "accepted" => "Confirmado",
        "corrected" => "Corrigido",
        "discarded" => "Descartado",
        "merged" => "Unido",
        _ => ReviewStatus
    };

    [JsonIgnore]
    public string ConfidenceLabel => double.IsFinite(Confidence) ? $"{Confidence:P0}" : "—";

    [JsonIgnore]
    public string OccurrenceLabel => OccurrenceIds.Count == 0 ? "—" : string.Join(", ", OccurrenceIds);

    private static string FormatTime(double seconds)
    {
        if (!double.IsFinite(seconds) || seconds < 0) return "—";
        var time = TimeSpan.FromSeconds(seconds);
        return time.TotalHours >= 1
            ? time.ToString(@"h\:mm\:ss", CultureInfo.InvariantCulture)
            : time.ToString(@"mm\:ss", CultureInfo.InvariantCulture);
    }
}

using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using DubSync.Desktop.Models;
using DubSync.Desktop.Services;

namespace DubSync.Desktop.Views;

public partial class ReviewView : UserControl, INotifyPropertyChanged
{
    private readonly DubSyncApiClient _api;
    private readonly ProjectSummary _project;
    private readonly ObservableCollection<ReviewEvent> _allEvents = [];
    private readonly ObservableCollection<ReviewEvent> _visibleEvents = [];
    private bool _loadingFilters;
    private bool _loaded;
    private bool _saving;
    private ReviewEvent? _selectedEvent;

    public event PropertyChangedEventHandler? PropertyChanged;
    public ObservableCollection<ReviewEvent> VisibleEvents => _visibleEvents;

    public ReviewEvent? SelectedEvent
    {
        get => _selectedEvent;
        private set
        {
            if (ReferenceEquals(_selectedEvent, value)) return;
            _selectedEvent = value;
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(SelectedEvent)));
        }
    }

    public ReviewView(DubSyncApiClient api, ProjectSummary project)
    {
        _api = api;
        _project = project;
        InitializeComponent();
        DataContext = this;
        ProjectLabel.Text = $"{project.Name} · {project.GameTitle}";
        EventsGrid.ItemsSource = _visibleEvents;
        _api.SetProjectScope(project.ProjectId);
    }

    private async void View_Loaded(object sender, RoutedEventArgs e)
    {
        if (_loaded) return;
        _loaded = true;
        await RefreshAsync();
    }

    private void View_Unloaded(object sender, RoutedEventArgs e) => _loaded = false;

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await RefreshAsync();

    private async Task RefreshAsync()
    {
        _loadingFilters = true;
        try
        {
            var sessions = await _api.GetSessionsAsync(_project.ProjectId);
            var selectedSessionId = (SessionFilter.SelectedItem as ComboBoxItem)?.Tag as string;
            SessionFilter.Items.Clear();
            SessionFilter.Items.Add(new ComboBoxItem { Content = "Todos os blocos", Tag = "all" });
            foreach (var session in sessions)
            {
                SessionFilter.Items.Add(new ComboBoxItem
                {
                    Content = $"{session.SourceStart:0}s · {session.StateLabel} · {session.SourceType}",
                    Tag = session.SessionId
                });
            }
            SessionFilter.SelectedItem = SessionFilter.Items.Cast<ComboBoxItem>()
                .FirstOrDefault(item => Equals(item.Tag as string, selectedSessionId)) ?? SessionFilter.Items[0];
            await LoadEventsAsync();
            ActionMessage.Text = "Falas sincronizadas com as ocorrências já salvas no projeto.";
            ActionMessage.Foreground = (System.Windows.Media.Brush)FindResource("MutedBrush");
        }
        catch (Exception exception) { SetMessage(exception.Message, true); }
        finally { _loadingFilters = false; }
    }

    private async Task LoadEventsAsync(string? preferredEventId = null)
    {
        var sessionId = (SessionFilter.SelectedItem as ComboBoxItem)?.Tag as string;
        if (sessionId == "all") sessionId = null;
        var status = (StatusFilter.SelectedItem as ComboBoxItem)?.Tag as string ?? "all";
        var events = await _api.GetEventsAsync(sessionId, status);
        var selectedId = preferredEventId ?? SelectedEvent?.EventId;
        _allEvents.Clear();
        foreach (var item in events.OrderBy(item => item.StartTime).ThenBy(item => item.Sequence)) _allEvents.Add(item);
        ApplySearchFilter();
        CountText.Text = $"{_visibleEvents.Count:N0} de {_allEvents.Count:N0} falas";
        EventsGrid.SelectedItem = _visibleEvents.FirstOrDefault(item => item.EventId == selectedId);
        if (EventsGrid.SelectedItem is null && _visibleEvents.Count > 0)
            EventsGrid.SelectedItem = _visibleEvents[0];
        SelectedEvent = EventsGrid.SelectedItem as ReviewEvent;
        await DisplaySelectedEventAsync();
    }

    private void Filter_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (!_loadingFilters && _loaded) _ = LoadEventsSafelyAsync();
    }

    private async Task LoadEventsSafelyAsync()
    {
        try { await LoadEventsAsync(); }
        catch (Exception exception) { SetMessage(exception.Message, true); }
    }

    private void Search_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (!_loaded) return;
        var selectedId = SelectedEvent?.EventId;
        ApplySearchFilter();
        CountText.Text = $"{_visibleEvents.Count:N0} de {_allEvents.Count:N0} falas";
        EventsGrid.SelectedItem = _visibleEvents.FirstOrDefault(item => item.EventId == selectedId)
            ?? _visibleEvents.FirstOrDefault();
        SelectedEvent = EventsGrid.SelectedItem as ReviewEvent;
        _ = DisplaySelectedEventAsync();
    }

    private void ApplySearchFilter()
    {
        var search = SearchInput.Text.Trim();
        _visibleEvents.Clear();
        foreach (var item in _allEvents)
        {
            if (search.Length > 0 && !item.TextForList.Contains(search, StringComparison.CurrentCultureIgnoreCase) &&
                !(item.Character?.Contains(search, StringComparison.CurrentCultureIgnoreCase) ?? false) &&
                !item.OccurrenceLabel.Contains(search, StringComparison.CurrentCultureIgnoreCase)) continue;
            _visibleEvents.Add(item);
        }
    }

    private async void Event_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        SelectedEvent = EventsGrid.SelectedItem as ReviewEvent;
        await DisplaySelectedEventAsync();
    }

    private async Task DisplaySelectedEventAsync()
    {
        var item = SelectedEvent;
        var enabled = item is not null;
        CorrectedTextBox.IsEnabled = CharacterInput.IsEnabled = EmotionInput.IsEnabled =
            StartTimeInput.IsEnabled = EndTimeInput.IsEnabled = enabled;
        RetryOcrButton.IsEnabled = enabled;
        if (item is null)
        {
            SelectedEventLabel.Text = "Selecione uma ocorrência";
            ConfidenceDetail.Text = "";
            OriginalTextBox.Text = CorrectedTextBox.Text = CharacterInput.Text = EmotionInput.Text = "";
            StartTimeInput.Text = EndTimeInput.Text = "";
            EvidenceImage.Source = null;
            EvidencePlaceholder.Visibility = Visibility.Visible;
            EvidenceMessage.Text = "Selecione uma fala para carregar a imagem salva.";
            return;
        }

        SelectedEventLabel.Text = $"Ocorrência {item.OccurrenceLabel} · {item.TimeLabel}–{item.EndLabel} · {item.StatusLabel}";
        ConfidenceDetail.Text = $"OCR {item.ConfidenceLabel}";
        OriginalTextBox.Text = item.OriginalText;
        CorrectedTextBox.Text = item.CorrectedText ?? item.ProcessedText ?? item.OriginalText;
        CharacterInput.Text = item.Character ?? "";
        EmotionInput.Text = item.Emotion ?? "";
        StartTimeInput.Text = item.StartTime.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture);
        EndTimeInput.Text = item.EndTime?.ToString("0.000", System.Globalization.CultureInfo.InvariantCulture) ?? "";
        await LoadEvidenceAsync(item);
    }

    private async Task LoadEvidenceAsync(ReviewEvent item)
    {
        var path = FindLatestRetryCrop(item.OcrObservations) ?? item.BestFramePath ?? item.CropPath;
        if (string.IsNullOrWhiteSpace(path) && item.Assets.ValueKind == JsonValueKind.Array)
        {
            foreach (var asset in item.Assets.EnumerateArray())
            {
                if (asset.ValueKind != JsonValueKind.Object || !asset.TryGetProperty("path", out var value)) continue;
                var kind = asset.TryGetProperty("kind", out var kindValue) ? kindValue.GetString() : "";
                if (kind is "best" or "start" or "crop" or "best_frame")
                {
                    path = value.GetString();
                    if (!string.IsNullOrWhiteSpace(path)) break;
                }
            }
        }
        if (string.IsNullOrWhiteSpace(path))
        {
            EvidenceImage.Source = null;
            EvidencePlaceholder.Visibility = Visibility.Visible;
            EvidenceMessage.Text = "Esta ocorrência não tem imagem salva.";
            return;
        }
        try
        {
            var bytes = await _api.GetEventAssetAsync(_project.ProjectId, item.SessionId, path);
            using var stream = new MemoryStream(bytes);
            var image = new BitmapImage();
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = stream;
            image.EndInit();
            image.Freeze();
            if (SelectedEvent?.EventId != item.EventId) return;
            EvidenceImage.Source = image;
            EvidencePlaceholder.Visibility = Visibility.Collapsed;
        }
        catch
        {
            if (SelectedEvent?.EventId != item.EventId) return;
            EvidenceImage.Source = null;
            EvidencePlaceholder.Visibility = Visibility.Visible;
            EvidenceMessage.Text = "A imagem de evidência não está disponível neste caminho.";
        }
    }

    private async void SaveCorrection_Click(object sender, RoutedEventArgs e) => await SaveSelectedAsync("correct");
    private async void Accept_Click(object sender, RoutedEventArgs e) => await SaveSelectedAsync("accept");
    private async void Discard_Click(object sender, RoutedEventArgs e) => await SaveSelectedAsync("discard");

    private async void RetryOcr_Click(object sender, RoutedEventArgs e)
    {
        var item = SelectedEvent;
        if (item is null || !TryReadNumber(StartTimeInput.Text, out var atTime)) return;
        RetryOcrButton.IsEnabled = false;
        try
        {
            var result = await _api.PostAsync(
                $"api/events/{Uri.EscapeDataString(item.EventId)}/retry-ocr", new { at_time = atTime });
            var processed = ReadJsonString(result, "processed_text");
            var confidence = ReadJsonDouble(result, "confidence");
            var speaker = result.TryGetProperty("speaker", out var speakerValue) ? speakerValue : default;
            var speakerConfigured = ReadJsonBool(speaker, "configured");
            var speakerCandidate = ReadJsonString(speaker, "candidate");
            var speakerRaw = ReadJsonString(speaker, "text");
            var speakerConfidence = ReadJsonDouble(speaker, "confidence");
            var speakerDisplay = !string.IsNullOrWhiteSpace(speakerCandidate) ? speakerCandidate
                : !string.IsNullOrWhiteSpace(speakerRaw) ? speakerRaw
                : speakerConfigured ? "Nenhum nome reconhecido com confiança suficiente."
                : "Área de personagem não configurada para esta ROI.";
            var info = $"Frame {FormatTime(ReadJsonDouble(result, "source_time"))} · texto {confidence:P0} · personagem " +
                       (speakerConfigured ? $"{speakerConfidence:P0}" : "sem ROI configurada") + ". Compare a nova leitura antes de aplicar.";

            var cropPath = FindRetryAsset(result, "ocr_retry_crop");
            if (!string.IsNullOrWhiteSpace(cropPath)) await LoadEvidenceAssetAsync(item, cropPath);

            if (string.IsNullOrWhiteSpace(processed) && string.IsNullOrWhiteSpace(speakerCandidate))
            {
                SetMessage("A tentativa foi salva no histórico, mas não encontrou texto ou personagem. Tente outro ponto do diálogo.", true);
                return;
            }

            var dialog = new OcrRetryWindow(
                item.TextForList,
                string.IsNullOrWhiteSpace(processed) ? "(Nenhum texto encontrado)" : processed,
                speakerDisplay,
                speakerConfigured,
                !string.IsNullOrWhiteSpace(speakerCandidate),
                info)
            {
                Owner = Window.GetWindow(this)
            };
            if (dialog.ShowDialog() != true)
            {
                SetMessage("Nova leitura e imagens guardadas no histórico; os dados atuais foram mantidos.", false);
                return;
            }

            var changes = new Dictionary<string, object?>();
            var correctedText = dialog.CorrectedText;
            if (!string.IsNullOrWhiteSpace(correctedText) &&
                !correctedText.StartsWith("(Nenhum texto", StringComparison.OrdinalIgnoreCase))
                changes["corrected_text"] = correctedText;
            if (!string.IsNullOrWhiteSpace(dialog.Character)) changes["character"] = dialog.Character;
            var action = changes.ContainsKey("corrected_text") ? "correct" : "update";
            await _api.ReviewEventAsync(item.EventId, action, changes);
            await LoadEventsAsync(item.EventId);
            SetMessage("Nova leitura aplicada. O OCR original e as imagens anteriores permanecem no histórico.", false);
        }
        catch (Exception exception) { SetMessage(exception.Message, true); }
        finally { RetryOcrButton.IsEnabled = SelectedEvent is not null; }
    }

    private async Task LoadEvidenceAssetAsync(ReviewEvent item, string path)
    {
        var bytes = await _api.GetEventAssetAsync(_project.ProjectId, item.SessionId, path);
        using var stream = new MemoryStream(bytes);
        var image = new BitmapImage();
        image.BeginInit();
        image.CacheOption = BitmapCacheOption.OnLoad;
        image.StreamSource = stream;
        image.EndInit();
        image.Freeze();
        if (SelectedEvent?.EventId != item.EventId) return;
        EvidenceImage.Source = image;
        EvidencePlaceholder.Visibility = Visibility.Collapsed;
    }

    private static string? FindRetryAsset(JsonElement result, string kind)
    {
        if (!result.TryGetProperty("image_assets", out var assets) || assets.ValueKind != JsonValueKind.Array) return null;
        foreach (var asset in assets.EnumerateArray())
        {
            if (ReadJsonString(asset, "kind") == kind) return ReadJsonString(asset, "path");
        }
        return null;
    }

    private static string? FindLatestRetryCrop(JsonElement observations)
    {
        if (observations.ValueKind != JsonValueKind.Array) return null;
        foreach (var observation in observations.EnumerateArray().Reverse())
        {
            if (!observation.TryGetProperty("image_assets", out var assets) || assets.ValueKind != JsonValueKind.Array) continue;
            foreach (var asset in assets.EnumerateArray())
            {
                if (ReadJsonString(asset, "kind") == "ocr_retry_crop") return ReadJsonString(asset, "path");
            }
        }
        return null;
    }

    private static string ReadJsonString(JsonElement value, string property) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(property, out var item) && item.ValueKind == JsonValueKind.String
            ? item.GetString() ?? "" : "";

    private static double ReadJsonDouble(JsonElement value, string property) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(property, out var item) && item.TryGetDouble(out var number)
            ? number : 0;

    private static bool ReadJsonBool(JsonElement value, string property) =>
        value.ValueKind == JsonValueKind.Object && value.TryGetProperty(property, out var item) && item.ValueKind == JsonValueKind.True;

    private async Task SaveSelectedAsync(string action)
    {
        var item = SelectedEvent;
        if (_saving || item is null) return;
        object changes = new { };
        if (action != "discard")
        {
            if (!TryReadNumber(StartTimeInput.Text, out var start) || start < 0)
            {
                SetMessage("Informe um início válido em segundos.", true);
                return;
            }
            double? end = null;
            if (!string.IsNullOrWhiteSpace(EndTimeInput.Text))
            {
                if (!TryReadNumber(EndTimeInput.Text, out var parsedEnd) || parsedEnd < start)
                {
                    SetMessage("O fim precisa ser um número igual ou posterior ao início.", true);
                    return;
                }
                end = parsedEnd;
            }
            var corrected = CorrectedTextBox.Text.Trim();
            if (action == "correct" && corrected.Length == 0)
            {
                SetMessage("Informe o texto corrigido antes de salvar.", true);
                return;
            }
            var values = new Dictionary<string, object?>
            {
                ["start_time"] = start,
                ["end_time"] = end,
                ["character"] = CharacterInput.Text.Trim(),
                ["emotion"] = EmotionInput.Text.Trim()
            };
            if (corrected.Length > 0) values["corrected_text"] = corrected;
            changes = values;
        }

        _saving = true;
        SetEditorEnabled(false);
        try
        {
            await _api.ReviewEventAsync(item.EventId, action, changes);
            SetMessage(action switch
            {
                "accept" => "Fala confirmada no projeto.",
                "discard" => "Fala descartada sem apagar o registro permanentemente.",
                _ => "Correção salva e sincronizada com as ocorrências do projeto."
            }, false);
            await LoadEventsAsync(item.EventId);
        }
        catch (Exception exception) { SetMessage(exception.Message, true); }
        finally
        {
            _saving = false;
            SetEditorEnabled(SelectedEvent is not null);
        }
    }

    private void SetEditorEnabled(bool enabled)
    {
        CorrectedTextBox.IsEnabled = CharacterInput.IsEnabled = EmotionInput.IsEnabled =
            StartTimeInput.IsEnabled = EndTimeInput.IsEnabled = enabled;
    }

    private void SetMessage(string message, bool isError)
    {
        ActionMessage.Text = message;
        ActionMessage.Foreground = (System.Windows.Media.Brush)FindResource(isError ? "AmberBrush" : "MutedBrush");
    }

    private static bool TryReadNumber(string text, out double value) =>
        double.TryParse(text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.CurrentCulture, out value)
        || double.TryParse(text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out value);

    private static string FormatTime(double seconds)
    {
        if (!double.IsFinite(seconds) || seconds < 0) return "—";
        var time = TimeSpan.FromSeconds(seconds);
        return time.TotalHours >= 1 ? time.ToString(@"h\:mm\:ss", System.Globalization.CultureInfo.InvariantCulture)
            : time.ToString(@"mm\:ss", System.Globalization.CultureInfo.InvariantCulture);
    }
}

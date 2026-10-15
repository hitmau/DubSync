using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using DubSync.Desktop.Models;
using DubSync.Desktop.Services;
using Microsoft.Win32;
using Rectangle = System.Windows.Shapes.Rectangle;

namespace DubSync.Desktop.Views;

public partial class CaptureView : UserControl, INotifyPropertyChanged
{
    private readonly DubSyncApiClient _api;
    private readonly ProjectSummary _project;
    private readonly ObservableCollection<CaptureSession> _sessions = [];
    private readonly ObservableCollection<RuntimeWindow> _runtimeWindows = [];
    private readonly ObservableCollection<RuntimePendingEvent> _runtimeEvents = [];
    private List<ReviewEvent> _playbackEvents = [];
    private readonly DispatcherTimer _pollTimer;
    private readonly DispatcherTimer _playerTimer;
    private readonly DispatcherTimer _runtimePollTimer;
    private readonly Rectangle _roiRectangle = new()
    {
        Stroke = new SolidColorBrush(Color.FromRgb(49, 205, 177)),
        StrokeThickness = 2,
        Fill = new SolidColorBrush(Color.FromArgb(45, 49, 205, 177)),
        Visibility = Visibility.Collapsed,
        IsHitTestVisible = false
    };
    private readonly Rectangle _roiResizeHandle = new()
    {
        Width = 12,
        Height = 12,
        Fill = new SolidColorBrush(Color.FromRgb(49, 205, 177)),
        Stroke = Brushes.White,
        StrokeThickness = 1,
        Visibility = Visibility.Collapsed,
        IsHitTestVisible = false
    };
    private bool _refreshing;
    private bool _loaded;
    private bool _mediaOpened;
    private bool _runtimeRefreshing;
    private bool _runtimeInitialized;
    private bool _isPlaying;
    private bool _isSeeking;
    private bool _isBusy;
    private bool _youtubeSourceSelected;
    private bool _realCaptureSelected;
    private bool _roiDragging;
    private RoiDragMode _roiDragMode;
    private Point _roiPointerStart;
    private Rect _roiOriginalRect;
    private Rect _roiCurrentRect;
    private double _naturalWidth;
    private double _naturalHeight;
    private string? _loadedVideoPath;
    private string? _previewSessionId;
    private string? _playbackEventsSessionId;
    private string? _runtimeSessionId;
    private RuntimeOverlayWindow? _runtimeOverlay;
    private CaptureSession? _selectedSession;
    private readonly Action<string>? _captureModeChanged;
    private string _projectLabel;

    public event PropertyChangedEventHandler? PropertyChanged;
    public string ProjectLabel { get => _projectLabel; private set => SetField(ref _projectLabel, value); }

    public CaptureView(DubSyncApiClient api, ProjectSummary project, string initialCaptureMode = "video",
        Action<string>? captureModeChanged = null)
    {
        _api = api;
        _project = project;
        _captureModeChanged = captureModeChanged;
        _projectLabel = $"{project.Name}  ·  {project.GameTitle}";
        InitializeComponent();
        DataContext = this;
        SessionList.ItemsSource = _sessions;
        GameWindowSelector.ItemsSource = _runtimeWindows;
        RuntimeEventList.ItemsSource = _runtimeEvents;
        RoiCanvas.Children.Add(_roiRectangle);
        RoiCanvas.Children.Add(_roiResizeHandle);
        LanguageInput.Text = string.IsNullOrWhiteSpace(project.CaptureLanguage) ? "en" : project.CaptureLanguage;
        RuntimeLanguageInput.Text = LanguageInput.Text;
        SetVideoSourceMode(youtube: false);
        SetCaptureModeVisuals(string.Equals(initialCaptureMode, "real", StringComparison.OrdinalIgnoreCase));
        _api.SetProjectScope(project.ProjectId);
        _pollTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2) };
        _pollTimer.Tick += async (_, _) =>
        {
            if (!_refreshing && _sessions.Any(session => session.IsRunning))
                await RefreshSessionsAsync(false);
            else if (!_sessions.Any(session => session.IsRunning))
                _pollTimer.Stop();
        };
        _playerTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(120) };
        _playerTimer.Tick += (_, _) => UpdatePlayerPosition();
        _runtimePollTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(1250) };
        _runtimePollTimer.Tick += async (_, _) => await RefreshRuntimeStatusAsync();
        PlaybackSpeed.SelectedIndex = 2;
        UpdateRoiOverlay();
        UpdateSourceActionAvailability();
    }

    private async void View_Loaded(object sender, RoutedEventArgs e)
    {
        _loaded = true;
        await RefreshSessionsAsync(true);
        if (_sessions.Any(session => session.IsRunning)) _pollTimer.Start();
        if (_realCaptureSelected) await InitializeRealCaptureAsync();
    }

    private void View_Unloaded(object sender, RoutedEventArgs e)
    {
        _loaded = false;
        _pollTimer.Stop();
        _playerTimer.Stop();
        _runtimePollTimer.Stop();
        VideoPlayer.Pause();
        _isPlaying = false;
    }

    private void ChooseFile_Click(object sender, RoutedEventArgs e)
    {
        SetVideoSourceMode(youtube: false);
        var picker = new OpenFileDialog
        {
            Title = "Escolher vídeo para captura",
            Filter = "Vídeos|*.mp4;*.mkv;*.mov;*.webm;*.avi;*.m4v|Todos os arquivos|*.*",
            CheckFileExists = true
        };
        if (picker.ShowDialog(Window.GetWindow(this)) == true)
        {
            FilePathInput.Text = picker.FileName;
            LoadVideoFile(picker.FileName);
            VideoDurationText.Text = "Arquivo selecionado. Carregando duração do vídeo…";
            ClearError();
        }
    }

    private void FilePathInput_TextChanged(object sender, TextChangedEventArgs e) => UpdateSourceActionAvailability();

    private void LocalSourceMode_Click(object sender, RoutedEventArgs e) => SetVideoSourceMode(youtube: false);

    private void YoutubeSourceMode_Click(object sender, RoutedEventArgs e) => SetVideoSourceMode(youtube: true);

    private void SetVideoSourceMode(bool youtube)
    {
        _youtubeSourceSelected = youtube;
        LocalSourcePanel.Visibility = youtube ? Visibility.Collapsed : Visibility.Visible;
        YoutubeSourcePanel.Visibility = youtube ? Visibility.Visible : Visibility.Collapsed;
        if (youtube)
        {
            YoutubeSourceModeButton.Style = (Style)FindResource("PrimaryButton");
            LocalSourceModeButton.ClearValue(StyleProperty);
            StartCaptureButton.Content = "▶  Iniciar análise do YouTube";
            if (_selectedSession is { SourceType: "youtube" })
                UpdateYoutubeStatus(_selectedSession);
        }
        else
        {
            LocalSourceModeButton.Style = (Style)FindResource("PrimaryButton");
            YoutubeSourceModeButton.ClearValue(StyleProperty);
            StartCaptureButton.Content = "▶  Iniciar bloco";
        }
        UpdateSourceActionAvailability();
    }

    private async void PrepareYoutube_Click(object sender, RoutedEventArgs e)
    {
        var url = YoutubeUrlInput.Text.Trim();
        if (url.Length < 12)
        {
            SetError("Cole o link completo do vídeo do YouTube.");
            return;
        }
        if (!TryReadNumber(StartInput.Text, out var start) || start < 0 ||
            !TryReadNumber(BlockMinutesInput.Text, out var minutes) || minutes <= 0 || minutes > 240)
        {
            SetError("Informe início válido e tamanho de bloco entre 0 e 240 minutos.");
            return;
        }
        var language = LanguageInput.Text.Trim();
        if (language.Length < 2 || language.Length > 16)
        {
            SetError("Informe um código de idioma como en, pt-BR ou es-MX.");
            return;
        }

        try
        {
            SetVideoSourceMode(youtube: true);
            SetBusy(true, "Preparando o vídeo do YouTube na biblioteca local…");
            YoutubeSourceStatusText.Text = "Download iniciado. A sessão ficará visível na lista enquanto o vídeo é preparado.";
            _previewSessionId = null;
            ResetVideoPreview("Aguardando o download do YouTube…");
            var response = await _api.PrepareYoutubeAsync(url, start, minutes * 60, language);
            var sessionId = response.TryGetProperty("session_id", out var idValue) ? idValue.GetString() : null;
            if (string.IsNullOrWhiteSpace(sessionId))
                throw new InvalidOperationException("A API não retornou o identificador da sessão do YouTube.");
            await RefreshSessionsAsync(false, sessionId);
            _pollTimer.Start();
            ClearError();
        }
        catch (Exception exception)
        {
            SetError(exception.Message);
            YoutubeSourceStatusText.Text = "Não foi possível preparar este vídeo.";
        }
        finally { SetBusy(false); }
    }

    private async void StopYoutube_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedSession is not { SourceType: "youtube" } session) return;
        var answer = MessageBox.Show(Window.GetWindow(this),
            "Encerrar esta sessão do YouTube? O download e os diálogos já salvos serão preservados, mas o bloco pode ficar incompleto.",
            "Encerrar sessão", MessageBoxButton.YesNo, MessageBoxImage.Warning);
        if (answer != MessageBoxResult.Yes) return;

        try
        {
            SetBusy(true, "Encerrando a sessão do YouTube…");
            await _api.PostEmptyAsync($"api/youtube/{Uri.EscapeDataString(session.SessionId)}/stop");
            await RefreshSessionsAsync(false, session.SessionId);
            SetStatus("Sessão encerrada. O vídeo baixado e os diálogos já salvos foram preservados.");
            ClearError();
            _pollTimer.Start();
        }
        catch (Exception exception) { SetError(exception.Message); }
        finally { SetBusy(false); }
    }

    private void LoadVideoFile(string path)
    {
        if (string.Equals(_loadedVideoPath, path, StringComparison.OrdinalIgnoreCase)) return;
        try
        {
            VideoPlayer.Stop();
            _mediaOpened = false;
            _isPlaying = false;
            _naturalWidth = _naturalHeight = 0;
            _loadedVideoPath = path;
            PreviewImage.Source = null;
            PreviewImage.Visibility = Visibility.Collapsed;
            PreviewPlaceholder.Visibility = Visibility.Visible;
            PreviewMetaText.Text = "Abrindo vídeo…";
            VideoPlayer.Source = new Uri(path, UriKind.Absolute);
            VideoPlayer.Visibility = Visibility.Visible;
            PlayPauseButton.IsEnabled = false;
            PlayerTimeline.IsEnabled = false;
            _playerTimer.Stop();
        }
        catch (Exception exception)
        {
            _loadedVideoPath = null;
            SetError($"Não foi possível abrir o vídeo no player nativo: {exception.Message}");
        }
    }

    private void VideoPlayer_MediaOpened(object sender, RoutedEventArgs e)
    {
        _mediaOpened = true;
        _naturalWidth = VideoPlayer.NaturalVideoWidth;
        _naturalHeight = VideoPlayer.NaturalVideoHeight;
        var duration = VideoPlayer.NaturalDuration.HasTimeSpan
            ? VideoPlayer.NaturalDuration.TimeSpan.TotalSeconds : 0;
        if (duration > 0)
        {
            PlayerTimeline.Maximum = duration;
            VideoDurationText.Text = $"Duração total: {FormatTime(duration)}. O bloco será limitado automaticamente ao fim do vídeo.";
        }
        PlayerTimeline.IsEnabled = duration > 0;
        PlayPauseButton.IsEnabled = true;
        PreviewImage.Visibility = Visibility.Collapsed;
        PreviewPlaceholder.Visibility = Visibility.Collapsed;
        VideoPlayer.Visibility = Visibility.Visible;
        if (TryReadNumber(StartInput.Text, out var start))
            VideoPlayer.Position = TimeSpan.FromSeconds(Math.Clamp(start, 0, Math.Max(0, duration)));
        ApplyPlaybackSpeed();
        UpdatePlayerPosition();
        UpdateRoiOverlay();
        _playerTimer.Start();
        ClearError();
    }

    private void VideoPlayer_MediaFailed(object sender, ExceptionRoutedEventArgs e)
    {
        _mediaOpened = false;
        _isPlaying = false;
        _playerTimer.Stop();
        PlayPauseButton.IsEnabled = false;
        PlayerTimeline.IsEnabled = false;
        VideoPlayer.Visibility = Visibility.Collapsed;
        PreviewImage.Visibility = PreviewImage.Source is null ? Visibility.Collapsed : Visibility.Visible;
        PreviewPlaceholder.Visibility = PreviewImage.Source is null ? Visibility.Visible : Visibility.Collapsed;
        SetError($"O Windows não conseguiu reproduzir este formato. A extração pelo serviço DubSync ainda pode funcionar. {e.ErrorException.Message}");
    }

    private void PlayPause_Click(object sender, RoutedEventArgs e)
    {
        if (!_mediaOpened) return;
        if (_isPlaying)
        {
            VideoPlayer.Pause();
            _isPlaying = false;
            PlayPauseButton.Content = "▶";
            PlayPauseButton.ToolTip = "Reproduzir";
        }
        else
        {
            VideoPlayer.Play();
            _isPlaying = true;
            PlayPauseButton.Content = "Ⅱ";
            PlayPauseButton.ToolTip = "Pausar";
            ApplyPlaybackSpeed();
        }
    }

    private void PlaybackSpeed_SelectionChanged(object sender, SelectionChangedEventArgs e) => ApplyPlaybackSpeed();

    private void ApplyPlaybackSpeed()
    {
        if (!_mediaOpened || PlaybackSpeed.SelectedItem is not ComboBoxItem item || item.Tag is null) return;
        if (double.TryParse(item.Tag.ToString(), System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var speed))
        {
            try { VideoPlayer.SpeedRatio = Math.Clamp(speed, 0.5, 5); }
            catch (Exception exception)
            {
                VideoPlayer.SpeedRatio = 1;
                PlaybackSpeed.SelectedIndex = 1;
                VideoDurationText.Text = $"Este codec não aceita {speed:0.#}× no player do Windows; mantive 1×. {exception.Message}";
            }
        }
    }

    private void PlayerTimeline_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (PlayerTimeline is null) return;
        PlayerTimeText.Text = $"{FormatTime(PlayerTimeline.Value)} / {FormatTime(PlayerTimeline.Maximum)}";
        UpdatePlaybackCaption(PlayerTimeline.Value);
    }

    private void PlayerTimeline_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e) => _isSeeking = true;

    private void PlayerTimeline_PreviewMouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        _isSeeking = false;
        if (_mediaOpened) VideoPlayer.Position = TimeSpan.FromSeconds(PlayerTimeline.Value);
    }

    private void UpdatePlayerPosition()
    {
        if (!_mediaOpened) return;
        if (!_isSeeking && PlayerTimeline.Maximum > 0)
            PlayerTimeline.Value = Math.Clamp(VideoPlayer.Position.TotalSeconds, 0, PlayerTimeline.Maximum);
        var currentTime = VideoPlayer.Position.TotalSeconds;
        PlayerTimeText.Text = $"{FormatTime(currentTime)} / {FormatTime(PlayerTimeline.Maximum)}";
        UpdatePlaybackCaption(currentTime);
    }

    private void UsePlayhead_Click(object sender, RoutedEventArgs e)
    {
        var seconds = _mediaOpened ? VideoPlayer.Position.TotalSeconds : PlayerTimeline.Value;
        StartInput.Text = seconds.ToString("0.###", System.Globalization.CultureInfo.CurrentCulture);
        if (_mediaOpened) VideoPlayer.Position = TimeSpan.FromSeconds(seconds);
    }

    private void RoiValues_TextChanged(object sender, TextChangedEventArgs e) => UpdateRoiOverlay();

    private Rect GetVideoViewport()
    {
        var width = FrameSurface.ActualWidth;
        var height = FrameSurface.ActualHeight;
        if (width <= 0 || height <= 0) return Rect.Empty;
        if (_naturalWidth <= 0 || _naturalHeight <= 0) return new Rect(0, 0, width, height);
        var scale = Math.Min(width / _naturalWidth, height / _naturalHeight);
        var videoWidth = _naturalWidth * scale;
        var videoHeight = _naturalHeight * scale;
        return new Rect((width - videoWidth) / 2, (height - videoHeight) / 2, videoWidth, videoHeight);
    }

    private void UpdateRoiOverlay()
    {
        if (RoiCanvas is null || RoiXInput is null || RoiYInput is null || RoiWidthInput is null || RoiHeightInput is null) return;
        var viewport = GetVideoViewport();
        if (viewport.IsEmpty || !TryReadNumber(RoiXInput.Text, out var x) || !TryReadNumber(RoiYInput.Text, out var y) ||
            !TryReadNumber(RoiWidthInput.Text, out var width) || !TryReadNumber(RoiHeightInput.Text, out var height) ||
            x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 100 || y + height > 100)
        {
            _roiRectangle.Visibility = _roiResizeHandle.Visibility = Visibility.Collapsed;
            return;
        }
        _roiCurrentRect = new Rect(viewport.X + viewport.Width * x / 100,
            viewport.Y + viewport.Height * y / 100,
            viewport.Width * width / 100,
            viewport.Height * height / 100);
        ShowRoiRect(_roiCurrentRect);
    }

    private void ShowRoiRect(Rect rectangle)
    {
        _roiCurrentRect = rectangle;
        _roiRectangle.Width = Math.Max(0, rectangle.Width);
        _roiRectangle.Height = Math.Max(0, rectangle.Height);
        Canvas.SetLeft(_roiRectangle, rectangle.X);
        Canvas.SetTop(_roiRectangle, rectangle.Y);
        _roiRectangle.Visibility = rectangle.Width > 1 && rectangle.Height > 1 ? Visibility.Visible : Visibility.Collapsed;
        Canvas.SetLeft(_roiResizeHandle, rectangle.Right - _roiResizeHandle.Width / 2);
        Canvas.SetTop(_roiResizeHandle, rectangle.Bottom - _roiResizeHandle.Height / 2);
        _roiResizeHandle.Visibility = _roiRectangle.Visibility;
    }

    private void RoiCanvas_SizeChanged(object sender, SizeChangedEventArgs e) => UpdateRoiOverlay();

    private void RoiCanvas_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        var viewport = GetVideoViewport();
        if (viewport.IsEmpty) return;
        var point = ClampToViewport(e.GetPosition(RoiCanvas), viewport);
        var handleBounds = new Rect(_roiCurrentRect.Right - 16, _roiCurrentRect.Bottom - 16, 24, 24);
        if (_roiRectangle.Visibility == Visibility.Visible && handleBounds.Contains(point))
            _roiDragMode = RoiDragMode.Resize;
        else if (_roiRectangle.Visibility == Visibility.Visible && _roiCurrentRect.Contains(point))
            _roiDragMode = RoiDragMode.Move;
        else
        {
            _roiDragMode = RoiDragMode.Draw;
            _roiOriginalRect = new Rect(point, new Size(0, 0));
            ShowRoiRect(_roiOriginalRect);
        }
        _roiPointerStart = point;
        if (_roiDragMode != RoiDragMode.Draw) _roiOriginalRect = _roiCurrentRect;
        _roiDragging = true;
        RoiCanvas.CaptureMouse();
        e.Handled = true;
    }

    private void RoiCanvas_MouseMove(object sender, MouseEventArgs e)
    {
        if (!_roiDragging || e.LeftButton != MouseButtonState.Pressed) return;
        var viewport = GetVideoViewport();
        if (viewport.IsEmpty) return;
        var point = ClampToViewport(e.GetPosition(RoiCanvas), viewport);
        Rect updated;
        if (_roiDragMode == RoiDragMode.Move)
        {
            var dx = point.X - _roiPointerStart.X;
            var dy = point.Y - _roiPointerStart.Y;
            var x = Math.Clamp(_roiOriginalRect.X + dx, viewport.Left, viewport.Right - _roiOriginalRect.Width);
            var y = Math.Clamp(_roiOriginalRect.Y + dy, viewport.Top, viewport.Bottom - _roiOriginalRect.Height);
            updated = new Rect(x, y, _roiOriginalRect.Width, _roiOriginalRect.Height);
        }
        else if (_roiDragMode == RoiDragMode.Resize)
        {
            var right = Math.Clamp(point.X, _roiOriginalRect.Left + 8, viewport.Right);
            var bottom = Math.Clamp(point.Y, _roiOriginalRect.Top + 8, viewport.Bottom);
            updated = new Rect(_roiOriginalRect.TopLeft, new Point(right, bottom));
        }
        else
        {
            updated = new Rect(_roiOriginalRect.TopLeft, point);
        }
        ShowRoiRect(updated);
    }

    private void RoiCanvas_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (!_roiDragging) return;
        _roiDragging = false;
        RoiCanvas.ReleaseMouseCapture();
        var viewport = GetVideoViewport();
        var rectangle = _roiCurrentRect;
        if (!viewport.IsEmpty && rectangle.Width >= 8 && rectangle.Height >= 8)
        {
            RoiXInput.Text = ((rectangle.Left - viewport.Left) / viewport.Width * 100).ToString("0.##", System.Globalization.CultureInfo.CurrentCulture);
            RoiYInput.Text = ((rectangle.Top - viewport.Top) / viewport.Height * 100).ToString("0.##", System.Globalization.CultureInfo.CurrentCulture);
            RoiWidthInput.Text = (rectangle.Width / viewport.Width * 100).ToString("0.##", System.Globalization.CultureInfo.CurrentCulture);
            RoiHeightInput.Text = (rectangle.Height / viewport.Height * 100).ToString("0.##", System.Globalization.CultureInfo.CurrentCulture);
        }
        else UpdateRoiOverlay();
        e.Handled = true;
    }

    private static Point ClampToViewport(Point point, Rect viewport) =>
        new(Math.Clamp(point.X, viewport.Left, viewport.Right), Math.Clamp(point.Y, viewport.Top, viewport.Bottom));

    private enum RoiDragMode { Draw, Move, Resize }

    private async void Preview_Click(object sender, RoutedEventArgs e) => await PreviewSourceAsync();

    private async Task PreviewSourceAsync()
    {
        if (_youtubeSourceSelected)
        {
            if (_selectedSession is not { SourceType: "youtube", ReadyForCalibration: true })
            {
                SetError("Prepare o vídeo do YouTube e aguarde o download terminar antes de pré-visualizar.");
                return;
            }
            await PreviewSessionSourceAsync(_selectedSession);
            return;
        }

        var path = FilePathInput.Text.Trim();
        if (path.Length == 0)
        {
            SetError("Escolha ou informe o caminho de um vídeo local.");
            return;
        }
        if (!TryReadNumber(StartInput.Text, out var start) || start < 0)
        {
            SetError("O início deve ser um número de segundos igual ou maior que zero.");
            return;
        }

        try
        {
            SetBusy(true, "Buscando o frame e a duração do vídeo…");
            LoadVideoFile(path);
            var preview = await _api.GetFilePreviewAsync(path, start);
            if (!_mediaOpened)
            {
                _naturalWidth = preview.Width;
                _naturalHeight = preview.Height;
            }
            SetPreview(preview.Image, $"{preview.Width} × {preview.Height} · início em {FormatTime(start)}");
            VideoDurationText.Text = $"Duração total: {FormatTime(preview.VideoDuration)}. O bloco será limitado automaticamente ao fim do vídeo.";
            if (_mediaOpened) VideoPlayer.Position = TimeSpan.FromSeconds(start);
            ClearError();
            UpdateRoiOverlay();
        }
        catch (Exception exception)
        {
            SetError(exception.Message);
        }
        finally { SetBusy(false); }
    }

    private async void StartCapture_Click(object sender, RoutedEventArgs e)
    {
        if (!_youtubeSourceSelected && string.IsNullOrWhiteSpace(FilePathInput.Text))
        {
            SetError("Escolha um vídeo antes de iniciar a análise.");
            return;
        }
        if (!TryReadNumber(StartInput.Text, out var start) || start < 0 ||
            !TryReadNumber(BlockMinutesInput.Text, out var minutes) || minutes <= 0 || minutes > 240)
        {
            SetError("Informe início válido e tamanho de bloco entre 0 e 240 minutos.");
            return;
        }
        if (!TryReadNumber(RoiXInput.Text, out var x) || !TryReadNumber(RoiYInput.Text, out var y) ||
            !TryReadNumber(RoiWidthInput.Text, out var width) || !TryReadNumber(RoiHeightInput.Text, out var height) ||
            x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 100.0001 || y + height > 100.0001)
        {
            SetError("A ROI precisa permanecer entre 0% e 100% do frame, com largura e altura maiores que zero.");
            return;
        }
        var language = LanguageInput.Text.Trim();
        if (language.Length < 2 || language.Length > 16)
        {
            SetError("Informe um código de idioma como en, pt-BR ou es-MX.");
            return;
        }

        try
        {
            SetBusy(true, "Iniciando o bloco…");
            var regions = new[]
            {
                new { x = x / 100, y = y / 100, width = width / 100, height = height / 100, name = "dialogue" }
            };
            System.Text.Json.JsonElement response;
            if (_youtubeSourceSelected)
            {
                if (_selectedSession is null || !_selectedSession.CanStartYoutubeAnalysis)
                    throw new InvalidOperationException("Prepare um vídeo do YouTube e aguarde o download antes de iniciar a análise.");
                if (Math.Abs(start - _selectedSession.SourceStart) > 0.001 ||
                    Math.Abs(minutes * 60 - _selectedSession.RequestedDuration) > 0.001)
                    throw new InvalidOperationException("O início ou o tamanho do bloco mudou depois do download. Clique em Preparar vídeo do YouTube novamente para aplicar os novos valores.");
                response = await _api.PostAsync(
                    $"api/youtube/{Uri.EscapeDataString(_selectedSession.SessionId)}/start",
                    new { screen_bbox = (object?)null, regions });
            }
            else
            {
                var body = new
                {
                    path = FilePathInput.Text.Trim(),
                    start,
                    duration = minutes * 60,
                    language,
                    regions
                };
                response = await _api.PostAsync("api/file/start", body);
            }
            var sessionId = response.TryGetProperty("session_id", out var idValue) ? idValue.GetString() : null;
            await RefreshSessionsAsync(false, sessionId);
            SetStatus(_youtubeSourceSelected
                ? "A análise do bloco do YouTube foi iniciada na sessão local."
                : "O bloco foi enviado ao processamento. A API continuará trabalhando em segundo plano.");
            ClearError();
            _pollTimer.Start();
        }
        catch (Exception exception)
        {
            SetError(exception.Message);
        }
        finally { SetBusy(false); }
    }

    private async void Pause_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedSession is null) return;
        try
        {
            var isYoutube = _selectedSession.SourceType == "youtube";
            var route = isYoutube ? "api/youtube" : "api/file";
            await _api.PostEmptyAsync($"{route}/{Uri.EscapeDataString(_selectedSession.SessionId)}/pause");
            SetStatus(isYoutube
                ? "Pausa solicitada. A fala atual do YouTube será concluída antes de pausar."
                : "Pausa solicitada. A leitura atual será concluída e o ponto seguro ficará salvo.");
            _pollTimer.Start();
            await RefreshSessionsAsync(false, _selectedSession.SessionId);
        }
        catch (Exception exception) { SetError(exception.Message); }
    }

    private async void Resume_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedSession is null) return;
        try
        {
            var sessionId = _selectedSession.SessionId;
            var route = _selectedSession.SourceType == "youtube" ? "api/youtube" : "api/file";
            await _api.PostEmptyAsync($"{route}/{Uri.EscapeDataString(sessionId)}/resume");
            SetStatus(_selectedSession.SourceType == "youtube"
                ? "O processamento do vídeo do YouTube está sendo retomado."
                : "O bloco está continuando a partir do ponto de pausa salvo.");
            _pollTimer.Start();
            await RefreshSessionsAsync(false, sessionId);
        }
        catch (Exception exception) { SetError(exception.Message); }
    }

    private async void NextBlock_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedSession is null || !TryReadNumber(BlockMinutesInput.Text, out var minutes) || minutes <= 0) return;
        try
        {
            string? sessionId;
            if (_selectedSession.SourceType == "youtube")
            {
                var previous = _selectedSession;
                var config = previous.Config;
                var url = ReadString(config, "url");
                if (string.IsNullOrWhiteSpace(url)) url = previous.SourceUri;
                var previousDuration = ReadDouble(config, "duration");
                if (previousDuration <= 0) previousDuration = previous.RequestedDuration;
                var nextStart = previous.SourceStart + previousDuration;
                var videoDuration = ReadDouble(config, "video_duration");
                if (videoDuration > 0 && nextStart >= videoDuration - 0.001)
                    throw new InvalidOperationException("A análise já alcançou o final do vídeo do YouTube.");

                SetVideoSourceMode(youtube: true);
                YoutubeUrlInput.Text = url;
                StartInput.Text = nextStart.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
                SetBusy(true, "Preparando o próximo bloco do YouTube…");
                var response = await _api.PrepareYoutubeAsync(url, nextStart, minutes * 60, previous.Language);
                sessionId = response.TryGetProperty("session_id", out var idValue) ? idValue.GetString() : null;
            }
            else
            {
                var response = await _api.PostAsync("api/file/next-block", new
                {
                    previous_session_id = _selectedSession.SessionId,
                    duration_seconds = minutes * 60
                });
                sessionId = response.TryGetProperty("session_id", out var idValue) ? idValue.GetString() : null;
            }
            SetStatus("O próximo bloco começou depois do ponto final do bloco selecionado.");
            await RefreshSessionsAsync(false, sessionId);
            _pollTimer.Start();
        }
        catch (Exception exception) { SetError(exception.Message); }
        finally { SetBusy(false); }
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e) => await RefreshSessionsAsync(true);

    private void LegacyCapture_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var address = new Uri(_api.BaseAddress, $"projects/{Uri.EscapeDataString(_project.ProjectId)}/capture");
            Process.Start(new ProcessStartInfo(address.AbsoluteUri) { UseShellExecute = true });
        }
        catch (Exception exception) { SetError($"Não foi possível abrir a captura clássica: {exception.Message}"); }
    }

    private async void RefreshGameWindows_Click(object sender, RoutedEventArgs e) => await RefreshRuntimeWindowsAsync();

    private async Task RefreshRuntimeWindowsAsync()
    {
        var selectedHandle = (GameWindowSelector.SelectedItem as RuntimeWindow)?.Handle;
        try
        {
            var windows = await _api.GetRuntimeWindowsAsync();
            _runtimeWindows.Clear();
            foreach (var window in windows) _runtimeWindows.Add(window);
            GameWindowSelector.SelectedItem = _runtimeWindows.FirstOrDefault(window => window.Handle == selectedHandle)
                ?? _runtimeWindows.FirstOrDefault(window => window.IsForeground)
                ?? _runtimeWindows.FirstOrDefault();
            RuntimeErrorText.Visibility = Visibility.Collapsed;
            if (_runtimeWindows.Count == 0)
                SetRuntimeError("Nenhuma janela visível foi encontrada. Abra o jogo em modo janela ou janela sem bordas e atualize a lista.");
        }
        catch (HttpRequestException exception) when ((int?)exception.StatusCode == 404)
        {
            SetRuntimeError("O serviço em execução ainda não oferece a Captura Real desta versão. Nenhuma sessão foi iniciada; atualize o serviço quando for seguro e tente novamente.");
        }
        catch (Exception exception) { SetRuntimeError(exception.Message); }
    }

    private async void StartRuntimeCapture_Click(object sender, RoutedEventArgs e)
    {
        if (GameWindowSelector.SelectedItem is not RuntimeWindow window)
        {
            SetRuntimeError("Selecione uma janela visível do jogo.");
            return;
        }
        if (!TryReadNumber(RoiXInput.Text, out var x) || !TryReadNumber(RoiYInput.Text, out var y) ||
            !TryReadNumber(RoiWidthInput.Text, out var width) || !TryReadNumber(RoiHeightInput.Text, out var height) ||
            x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 100.0001 || y + height > 100.0001)
        {
            SetRuntimeError("A ROI da legenda deve ficar entre 0% e 100% do jogo.");
            return;
        }
        var language = RuntimeLanguageInput.Text.Trim();
        if (language.Length < 2 || language.Length > 16)
        {
            SetRuntimeError("Informe um código de idioma como en, pt-BR ou es-MX.");
            return;
        }
        var fpsItem = RuntimeFpsSelector.SelectedItem as ComboBoxItem;
        var rates = fpsItem?.Tag?.ToString()?.Split('|');
        if (rates is not { Length: 2 } ||
            !double.TryParse(rates[0], System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var captureFps) ||
            !double.TryParse(rates[1], System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture, out var analysisFps))
        {
            SetRuntimeError("Selecione um perfil de desempenho.");
            return;
        }

        try
        {
            StartRuntimeButton.IsEnabled = false;
            RuntimeStateText.Text = "Preparando a janela e o overlay…";
            var capabilities = await _api.GetRuntimeCaptureCapabilitiesAsync();
            if (!capabilities.TryGetProperty("native_overlay", out var nativeOverlay) || nativeOverlay.ValueKind != JsonValueKind.True)
                throw new InvalidOperationException("O serviço conectado ainda não oferece o overlay nativo. Atualize o serviço DubSync quando for seguro e tente novamente.");
            if (!capabilities.TryGetProperty("windows_graphics_capture", out var graphicsCapture) || graphicsCapture.ValueKind != JsonValueKind.True)
                throw new InvalidOperationException("O serviço conectado ainda não tem Windows Graphics Capture instalado. Execute setup.ps1 e reinicie o serviço quando for seguro; nenhuma sessão foi iniciada.");
            var response = await _api.PostAsync("api/runtime-capture/start", new
            {
                hwnd = window.Handle,
                language,
                fps = analysisFps,
                capture_fps = captureFps,
                native_overlay = true,
                capture_backend = "windows_graphics_capture",
                regions = new[] { new { x = x / 100, y = y / 100, width = width / 100, height = height / 100, name = "dialogue", roi_id = "dialogue" } }
            });
            _runtimeSessionId = response.TryGetProperty("session_id", out var idValue) ? idValue.GetString() : null;
            if (string.IsNullOrWhiteSpace(_runtimeSessionId))
                throw new InvalidOperationException("A API não retornou o identificador da sessão.");
            _runtimeOverlay = RuntimeOverlayWindow.ShowForSession(_api, _runtimeSessionId, window.Handle);
            RuntimeErrorText.Visibility = Visibility.Collapsed;
            _runtimePollTimer.Start();
            await RefreshRuntimeStatusAsync();
        }
        catch (Exception exception)
        {
            if (!string.IsNullOrWhiteSpace(_runtimeSessionId))
            {
                try { await _api.PostEmptyAsync($"api/runtime-capture/{Uri.EscapeDataString(_runtimeSessionId)}/stop"); }
                catch { }
                _runtimeSessionId = null;
            }
            StartRuntimeButton.IsEnabled = true;
            SetRuntimeError(exception.Message);
        }
    }

    private async void CalibrateRuntime_Click(object sender, RoutedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(_runtimeSessionId))
        {
            SetRuntimeError("Inicie uma captura real antes de calibrar a ROI.");
            return;
        }

        var consent = MessageBox.Show(Window.GetWindow(this),
            "A calibração enviará até três frames recentes do jogo ao modelo de IA configurado no DubSync. " +
            "O modelo retorna uma sugestão de posição para a primeira ROI. Deseja autorizar o envio?",
            "Autorizar calibração por IA", MessageBoxButton.YesNo, MessageBoxImage.Warning);
        if (consent != MessageBoxResult.Yes) return;

        CalibrateRuntimeButton.IsEnabled = false;
        RuntimeStateText.Text = "Analisando até três frames para sugerir a ROI…";
        try
        {
            var sessionId = _runtimeSessionId;
            var result = await _api.PostAsync(
                $"api/runtime-capture/{Uri.EscapeDataString(sessionId)}/calibrate",
                new { consent_to_send_frames = true });
            var suggestion = result.TryGetProperty("suggested_region", out var suggestionValue)
                ? suggestionValue : default;
            if (suggestion.ValueKind != JsonValueKind.Object)
            {
                var notes = ReadString(result, "notes");
                var samples = ReadCalibrationSamples(result);
                MessageBox.Show(Window.GetWindow(this),
                    "A IA não encontrou uma posição nova para a legenda." +
                    (string.IsNullOrWhiteSpace(notes) ? "" : $"\n\nObservação: {notes}") +
                    (string.IsNullOrWhiteSpace(samples) ? "" : $"\n\nAmostras: {samples}"),
                    "Calibração concluída", MessageBoxButton.OK, MessageBoxImage.Information);
                return;
            }

            var x = ReadDouble(suggestion, "x");
            var y = ReadDouble(suggestion, "y");
            var width = ReadDouble(suggestion, "width");
            var height = ReadDouble(suggestion, "height");
            var notesText = ReadString(result, "notes");
            var sampleText = ReadCalibrationSamples(result);
            var detail = $"Posição sugerida: X {x:P1}, Y {y:P1}, largura {width:P1}, altura {height:P1}." +
                         (string.IsNullOrWhiteSpace(sampleText) ? "" : $"\n\nTextos vistos: {sampleText}") +
                         (string.IsNullOrWhiteSpace(notesText) ? "" : $"\n\nObservação: {notesText}");
            if (MessageBox.Show(Window.GetWindow(this),
                    $"A calibração terminou. Aplicar a sugestão somente à primeira ROI? As outras áreas serão mantidas.\n\n{detail}",
                    "Aplicar ROI sugerida", MessageBoxButton.YesNo, MessageBoxImage.Question) != MessageBoxResult.Yes)
                return;
            if (x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > 1.000001 || y + height > 1.000001)
                throw new InvalidOperationException("A sugestão da IA ficou fora dos limites da tela e não foi aplicada.");

            var status = await _api.GetRuntimeStatusAsync(sessionId);
            if (!status.TryGetProperty("regions", out var currentRegions) || currentRegions.ValueKind != JsonValueKind.Array || currentRegions.GetArrayLength() == 0)
                throw new InvalidOperationException("A sessão não retornou ROIs ativas para atualizar.");
            var regionsNode = System.Text.Json.Nodes.JsonNode.Parse(currentRegions.GetRawText())?.AsArray()
                ?? throw new InvalidOperationException("A lista de ROIs não pôde ser lida.");
            var firstRegion = regionsNode[0]?.AsObject()
                ?? throw new InvalidOperationException("A primeira ROI está vazia.");
            var oldX = firstRegion["x"]?.GetValue<double>() ?? 0;
            var oldY = firstRegion["y"]?.GetValue<double>() ?? 0;
            firstRegion["x"] = x;
            firstRegion["y"] = y;
            firstRegion["width"] = width;
            firstRegion["height"] = height;
            if (firstRegion["speaker_region"] is System.Text.Json.Nodes.JsonObject speakerRegion)
            {
                var speakerX = speakerRegion["x"]?.GetValue<double>() ?? oldX;
                var speakerY = speakerRegion["y"]?.GetValue<double>() ?? oldY;
                var speakerWidth = speakerRegion["width"]?.GetValue<double>() ?? 0;
                var speakerHeight = speakerRegion["height"]?.GetValue<double>() ?? 0;
                speakerRegion["x"] = Math.Clamp(speakerX + x - oldX, 0, Math.Max(0, 1 - speakerWidth));
                speakerRegion["y"] = Math.Clamp(speakerY + y - oldY, 0, Math.Max(0, 1 - speakerHeight));
            }

            await _api.PutAsync($"api/runtime-capture/{Uri.EscapeDataString(sessionId)}/regions", new { regions = regionsNode });
            RuntimeErrorText.Visibility = Visibility.Collapsed;
            MessageBox.Show(Window.GetWindow(this), "A sugestão foi aplicada à primeira ROI. A faixa sobre o jogo já exibe a nova posição.",
                "ROI atualizada", MessageBoxButton.OK, MessageBoxImage.Information);
        }
        catch (Exception exception)
        {
            SetRuntimeError(exception.Message);
            RuntimeStateText.Text = "Não foi possível concluir a calibração";
        }
        finally
        {
            await RefreshRuntimeStatusAsync();
        }
    }

    private static string ReadCalibrationSamples(JsonElement result)
    {
        if (!result.TryGetProperty("samples", out var samples) || samples.ValueKind != JsonValueKind.Array) return "";
        return string.Join(" · ", samples.EnumerateArray()
            .Select(sample => ReadString(sample, "text"))
            .Where(text => !string.IsNullOrWhiteSpace(text))
            .Take(3));
    }

    private async void PauseRuntimeCapture_Click(object sender, RoutedEventArgs e)
    {
        if (_runtimeSessionId is null) return;
        await ChangeRuntimeStateAsync("pause");
    }

    private async void ResumeRuntimeCapture_Click(object sender, RoutedEventArgs e)
    {
        if (_runtimeSessionId is null) return;
        await ChangeRuntimeStateAsync("resume");
    }

    private async Task ChangeRuntimeStateAsync(string action)
    {
        try
        {
            await _api.PostEmptyAsync($"api/runtime-capture/{Uri.EscapeDataString(_runtimeSessionId!)}/{action}");
            await RefreshRuntimeStatusAsync();
        }
        catch (Exception exception) { SetRuntimeError(exception.Message); }
    }

    private async void StopRuntimeCapture_Click(object sender, RoutedEventArgs e)
    {
        if (_runtimeSessionId is null) return;
        try
        {
            await _api.PostEmptyAsync($"api/runtime-capture/{Uri.EscapeDataString(_runtimeSessionId)}/stop");
            RuntimeStateText.Text = "Salvando a fala atual e encerrando…";
            _runtimePollTimer.Start();
            await RefreshRuntimeStatusAsync();
        }
        catch (Exception exception) { SetRuntimeError(exception.Message); }
    }

    private async Task RefreshRuntimeStatusAsync()
    {
        if (_runtimeRefreshing || string.IsNullOrWhiteSpace(_runtimeSessionId)) return;
        _runtimeRefreshing = true;
        try
        {
            var status = await _api.GetRuntimeStatusAsync(_runtimeSessionId);
            var state = ReadString(status, "state");
            var progress = status.TryGetProperty("progress", out var progressValue) ? progressValue : default;
            var stage = ReadString(progress, "activity_message");
            var frames = ReadInt(progress, "frames_analyzed");
            var ocr = ReadInt(progress, "ocr_calls");
            var events = ReadInt(progress, "events_created");
            var analysisFps = ReadDouble(status, "analysis_fps");
            if (analysisFps <= 0) analysisFps = ReadDouble(status, "fps");
            var captureFps = ReadDouble(status, "capture_fps");
            if (captureFps <= 0) captureFps = analysisFps;
            var title = status.TryGetProperty("window", out var window) ? ReadString(window, "title") : "";
            RuntimeStateText.Text = $"{RuntimeStateLabel(state)}{(string.IsNullOrWhiteSpace(title) ? "" : " · " + title)}";
            if (!string.IsNullOrWhiteSpace(stage)) RuntimeStatsText.Text = $"{stage}   ·   {frames:N0} frames   ·   {ocr:N0} OCR   ·   {events:N0} falas";
            else RuntimeStatsText.Text = $"{frames:N0} frames   ·   {ocr:N0} OCR   ·   {events:N0} falas";
            RuntimeFpsText.Text = captureFps > 0 && analysisFps > 0
                ? $"Alvo: Captura {captureFps:0.#} · Análise {analysisFps:0.#} FPS"
                : "Alvo: Captura — · Análise — FPS";
            var active = state is "preparing" or "running" or "paused" or "stopping";
            RuntimeProgress.IsIndeterminate = state is "preparing" or "running" or "stopping";
            RuntimeProgress.Value = active ? 0 : 100;
            PauseRuntimeButton.IsEnabled = state == "running";
            ResumeRuntimeButton.IsEnabled = state == "paused";
            StopRuntimeButton.IsEnabled = state is "preparing" or "running" or "paused";
            CalibrateRuntimeButton.IsEnabled = state is "running" or "paused";
            StartRuntimeButton.IsEnabled = !active;
            if (state is "complete" or "failed" or "stopped") _runtimePollTimer.Stop();
            else if (!_runtimePollTimer.IsEnabled) _runtimePollTimer.Start();

            var pending = await _api.GetRuntimePendingAsync(_runtimeSessionId);
            _runtimeEvents.Clear();
            foreach (var item in pending.Events) _runtimeEvents.Add(item);
            RuntimeErrorText.Visibility = Visibility.Collapsed;
        }
        catch (Exception exception)
        {
            if (RuntimeStateText is not null) RuntimeStateText.Text = "Sem resposta da sessão de captura";
            SetRuntimeError(exception.Message);
        }
        finally { _runtimeRefreshing = false; }
    }

    private static string RuntimeStateLabel(string state) => state switch
    {
        "preparing" => "Preparando captura…",
        "running" => "Capturando e analisando legendas",
        "paused" => "Captura pausada",
        "stopping" => "Salvando a fala atual…",
        "complete" => "Captura encerrada",
        "failed" => "Falha na captura",
        "stopped" => "Captura interrompida",
        _ => string.IsNullOrWhiteSpace(state) ? "Aguardando início" : state
    };

    private static string ReadString(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? "" : "";

    private static int ReadInt(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.TryGetInt32(out var number)
            ? number : 0;

    private static double ReadDouble(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.TryGetDouble(out var number)
            ? number : 0;

    private void SetRuntimeError(string message)
    {
        RuntimeErrorText.Text = message;
        RuntimeErrorText.Visibility = Visibility.Visible;
    }

    private async Task RefreshSessionsAsync(bool showFailure, string? preferSessionId = null)
    {
        if (_refreshing) return;
        _refreshing = true;
        try
        {
            var oldSelectedId = _selectedSession?.SessionId;
            var items = await _api.GetSessionsAsync(_project.ProjectId);
            var selectedId = preferSessionId ?? oldSelectedId;
            _sessions.Clear();
            foreach (var item in items) _sessions.Add(item);
            SessionSummaryText.Text = _sessions.Count == 0
                ? "Nenhum bloco foi capturado neste projeto ainda."
                : $"{_sessions.Count} sessão(ões) · dados carregados da API do projeto";

            _selectedSession = _sessions.FirstOrDefault(item => item.SessionId == selectedId) ?? _sessions.FirstOrDefault();
            SessionList.SelectedItem = _selectedSession;
            if (_selectedSession?.SessionId != oldSelectedId)
                ApplySessionSourceMode(_selectedSession);
            UpdateSelectedSession();
            if (_selectedSession is not null) await RefreshPlaybackEventsAsync(_selectedSession);
            else ClearPlaybackEvents();
            if (_selectedSession is not null)
            {
                var needsPreview = _selectedSession.SourceType == "youtube"
                    ? _selectedSession.ReadyForCalibration && _previewSessionId != _selectedSession.SessionId
                    : _previewSessionId != _selectedSession.SessionId || PreviewImage.Source is null;
                if (needsPreview) await PreviewSessionSourceAsync(_selectedSession);
            }
            if (_sessions.Any(session => session.IsRunning) && _loaded) _pollTimer.Start();
        }
        catch (Exception exception)
        {
            if (showFailure) SetError(exception.Message);
            SessionSummaryText.Text = "Não foi possível consultar as sessões do projeto.";
        }
        finally { _refreshing = false; }
    }

    private async void SessionList_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        var previousId = _selectedSession?.SessionId;
        _selectedSession = SessionList.SelectedItem as CaptureSession;
        UpdateSelectedSession();
        if (_refreshing) return;
        if (_selectedSession?.SessionId != previousId)
            ApplySessionSourceMode(_selectedSession);
        if (_selectedSession is not null)
        {
            await RefreshPlaybackEventsAsync(_selectedSession);
            await PreviewSessionSourceAsync(_selectedSession);
        }
        else ClearPlaybackEvents();
    }

    private void ApplySessionSourceMode(CaptureSession? session)
    {
        if (session is null) return;
        if (session.SourceType == "youtube")
        {
            SetVideoSourceMode(youtube: true);
            var url = ReadString(session.Config, "url");
            YoutubeUrlInput.Text = string.IsNullOrWhiteSpace(url) ? session.SourceUri : url;
            StartInput.Text = session.SourceStart.ToString("0.###", System.Globalization.CultureInfo.InvariantCulture);
            BlockMinutesInput.Text = (session.RequestedDuration / 60).ToString("0.##", System.Globalization.CultureInfo.InvariantCulture);
            LanguageInput.Text = session.Language;
        }
        else if (session.SourceType == "file")
        {
            SetVideoSourceMode(youtube: false);
            LanguageInput.Text = session.Language;
        }
    }

    private async Task RefreshPlaybackEventsAsync(CaptureSession session)
    {
        if (session.SourceType == "game")
        {
            ClearPlaybackEvents();
            return;
        }
        if (_playbackEventsSessionId == session.SessionId) return;

        _playbackEventsSessionId = session.SessionId;
        _playbackEvents = [];
        UpdatePlaybackCaption(_mediaOpened ? VideoPlayer.Position.TotalSeconds : PlayerTimeline.Value);
        try
        {
            var events = await _api.GetEventsAsync(session.SessionId);
            if (_selectedSession?.SessionId != session.SessionId) return;
            _playbackEvents = events
                .Where(item => item.ReviewStatus is not ("discarded" or "merged"))
                .OrderBy(item => item.StartTime)
                .ToList();
            UpdatePlaybackCaption(_mediaOpened ? VideoPlayer.Position.TotalSeconds : PlayerTimeline.Value);
        }
        catch
        {
            if (_selectedSession?.SessionId != session.SessionId) return;
            _playbackEventsSessionId = null;
            _playbackEvents = [];
            UpdatePlaybackCaption(_mediaOpened ? VideoPlayer.Position.TotalSeconds : PlayerTimeline.Value);
        }
    }

    private void ClearPlaybackEvents()
    {
        _playbackEventsSessionId = null;
        _playbackEvents = [];
        UpdatePlaybackCaption(_mediaOpened ? VideoPlayer.Position.TotalSeconds : PlayerTimeline.Value);
    }

    private void UpdatePlaybackCaption(double sourceTime)
    {
        if (PlaybackCaptionPanel is null) return;
        var sessionPath = _selectedSession is null ? null : GetSessionVideoPath(_selectedSession);
        if (string.IsNullOrWhiteSpace(sessionPath) || string.IsNullOrWhiteSpace(_loadedVideoPath) ||
            !Path.IsPathRooted(sessionPath) || !Path.IsPathRooted(_loadedVideoPath))
        {
            PlaybackCaptionPanel.Visibility = Visibility.Collapsed;
            return;
        }
        try
        {
            if (!string.Equals(Path.GetFullPath(sessionPath), Path.GetFullPath(_loadedVideoPath), StringComparison.OrdinalIgnoreCase))
            {
                PlaybackCaptionPanel.Visibility = Visibility.Collapsed;
                return;
            }
        }
        catch (ArgumentException)
        {
            PlaybackCaptionPanel.Visibility = Visibility.Collapsed;
            return;
        }

        ReviewEvent? active = null;
        for (var index = 0; index < _playbackEvents.Count; index++)
        {
            var item = _playbackEvents[index];
            var inferredEnd = item.EndTime ?? (index + 1 < _playbackEvents.Count
                ? _playbackEvents[index + 1].StartTime : double.PositiveInfinity);
            if (sourceTime >= item.StartTime && sourceTime <= inferredEnd && !string.IsNullOrWhiteSpace(item.TextForList))
                active = item;
        }

        if (active is null)
        {
            PlaybackCaptionPanel.Visibility = Visibility.Collapsed;
            return;
        }
        PlaybackCaptionSpeaker.Text = active.Character ?? "";
        PlaybackCaptionSpeaker.Visibility = string.IsNullOrWhiteSpace(active.Character) ? Visibility.Collapsed : Visibility.Visible;
        PlaybackCaptionText.Text = active.TextForList;
        PlaybackCaptionPanel.Visibility = Visibility.Visible;
    }

    private async Task PreviewSessionSourceAsync(CaptureSession session)
    {
        if (session.SourceType == "youtube" && !session.ReadyForCalibration)
        {
            if (_previewSessionId != session.SessionId)
                ResetVideoPreview(session.State is "downloading" or "queued"
                    ? "Baixando o vídeo do YouTube…"
                    : "Aguardando a preparação do vídeo do YouTube…");
            UpdateYoutubeStatus(session);
            return;
        }

        var path = GetSessionVideoPath(session);
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path))
        {
            if (_previewSessionId != session.SessionId)
                ResetVideoPreview(session.SourceType == "youtube" && (session.State is "downloading" or "queued")
                    ? "Baixando o vídeo do YouTube…"
                    : "O vídeo desta sessão ainda não está disponível.");
            if (session.SourceType == "youtube") UpdateYoutubeStatus(session);
            return;
        }

        FilePathInput.Text = path;
        LoadVideoFile(path);

        var seconds = session.Progress?.CurrentSourceTime ?? session.CurrentSourceTime ?? session.SourceStart;
        try
        {
            var preview = await _api.GetFilePreviewAsync(path, seconds);
            if (!_mediaOpened)
            {
                _naturalWidth = preview.Width;
                _naturalHeight = preview.Height;
            }
            SetPreview(preview.Image, $"{Path.GetFileName(path)} · {FormatTime(seconds)}");
            if (_mediaOpened) VideoPlayer.Position = TimeSpan.FromSeconds(seconds);
            _previewSessionId = session.SessionId;
            if (session.SourceType == "youtube") UpdateYoutubeStatus(session);
            UpdateRoiOverlay();
        }
        catch
        {
            // A sessão ainda aparece e pode ser retomada mesmo quando a prévia do arquivo não está disponível.
        }
    }

    private static string? GetSessionVideoPath(CaptureSession session)
    {
        if (session.Config.ValueKind != JsonValueKind.Object) return session.SourceUri;
        foreach (var key in new[] { "downloaded_path", "path" })
        {
            if (session.Config.TryGetProperty(key, out var value) && value.ValueKind == JsonValueKind.String &&
                !string.IsNullOrWhiteSpace(value.GetString())) return value.GetString();
        }
        return session.SourceUri;
    }

    private void UpdateSelectedSession()
    {
        var session = _selectedSession;
        if (session is null)
        {
            SelectedSessionText.Text = "Nenhum bloco selecionado";
            ProgressStatsText.Text = "";
            SessionProgress.Value = 0;
            PauseButton.IsEnabled = ResumeButton.IsEnabled = NextBlockButton.IsEnabled = false;
            UpdateSourceActionAvailability();
            return;
        }

        var current = session.Progress?.CurrentSourceTime ?? session.CurrentSourceTime ?? session.SourceStart;
        SelectedSessionText.Text = $"{session.StateLabel} · {FormatTime(current)}";
        var progress = session.Progress;
        ProgressStatsText.Text = progress is null ? "" : $"{progress.FramesAnalyzed:N0} quadros · {progress.OcrCalls:N0} OCR · {progress.EventsCreated:N0} falas";
        SessionProgress.Value = session.IsComplete ? 100 : session.RequestedDuration > 0
            ? Math.Clamp((current - session.SourceStart) / session.RequestedDuration * 100, 0, 100) : 0;
        PauseButton.IsEnabled = session.SourceType == "youtube"
            ? session.State == "processing"
            : session.IsRunning;
        ResumeButton.IsEnabled = session.CanContinue && session.CanContinueYoutube;
        ResumeButton.Content = session.SourceType == "youtube" && !session.AnalysisStarted
            ? "↻  Retomar download" : "↻  Continuar do ponto salvo";
        NextBlockButton.IsEnabled = session.IsComplete;
        if (session.SourceType == "youtube") UpdateYoutubeStatus(session);
        UpdateSourceActionAvailability();
    }

    private void UpdateYoutubeStatus(CaptureSession session)
    {
        if (session.State is "queued" or "downloading")
        {
            var percent = session.Progress?.Percent;
            YoutubeSourceStatusText.Text = percent is not null
                ? $"Baixando… {percent:0.#}% · o progresso fica salvo nesta sessão."
                : "Baixando o vídeo para a biblioteca local…";
            return;
        }

        if (session.CanStartYoutubeAnalysis)
        {
            var title = ReadString(session.Config, "video_title");
            if (string.IsNullOrWhiteSpace(title)) title = "Vídeo preparado";
            var width = ReadInt(session.Config, "video_width");
            var height = ReadInt(session.Config, "video_height");
            var dimensions = width > 0 && height > 0 ? $" · {width} × {height}" : "";
            YoutubeSourceStatusText.Text = $"{title}{dimensions} · pronto para pré-visualizar e analisar este bloco.";
            return;
        }

        YoutubeSourceStatusText.Text = session.State switch
        {
            "preparing" when session.AnalysisStarted => "Iniciando análise do bloco…",
            "complete" => "Extração concluída. Use Próximo bloco para continuar no mesmo vídeo.",
            "paused" => "Sessão pausada; o conteúdo baixado e o progresso foram preservados.",
            "failed" or "stopped" => session.Progress?.ActivityMessage is { Length: > 0 } detail
                ? detail : "Sessão interrompida. Use Retomar para continuar quando possível.",
            _ => session.Progress?.ActivityMessage is { Length: > 0 } activity
                ? activity : "Sessão do YouTube selecionada."
        };
    }

    private void SetPreview(string? dataUri, string description)
    {
        if (string.IsNullOrWhiteSpace(dataUri))
        {
            PreviewImage.Source = null;
            PreviewImage.Visibility = Visibility.Collapsed;
            PreviewPlaceholder.Visibility = Visibility.Visible;
            PreviewMetaText.Text = "Frame indisponível";
            return;
        }
        var comma = dataUri.IndexOf(',');
        var base64 = comma >= 0 ? dataUri[(comma + 1)..] : dataUri;
        var bytes = Convert.FromBase64String(base64);
        var image = new BitmapImage();
        using (var stream = new MemoryStream(bytes))
        {
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = stream;
            image.EndInit();
            image.Freeze();
        }
        PreviewImage.Source = image;
        PreviewImage.Visibility = _mediaOpened ? Visibility.Collapsed : Visibility.Visible;
        PreviewPlaceholder.Visibility = _mediaOpened || PreviewImage.Visibility == Visibility.Visible
            ? Visibility.Collapsed : Visibility.Visible;
        PreviewMetaText.Text = description;
        UpdateRoiOverlay();
    }

    private void ResetVideoPreview(string description)
    {
        VideoPlayer.Stop();
        VideoPlayer.Source = null;
        _mediaOpened = false;
        _isPlaying = false;
        _loadedVideoPath = null;
        _naturalWidth = _naturalHeight = 0;
        _previewSessionId = null;
        _playerTimer.Stop();
        PlayPauseButton.Content = "▶";
        PlayPauseButton.IsEnabled = false;
        PlayerTimeline.IsEnabled = false;
        PlayerTimeline.Value = 0;
        PlayerTimeline.Maximum = 1;
        PreviewImage.Source = null;
        PreviewImage.Visibility = Visibility.Collapsed;
        PreviewPlaceholder.Visibility = Visibility.Visible;
        PreviewMetaText.Text = description;
        PlaybackCaptionPanel.Visibility = Visibility.Collapsed;
        UpdateRoiOverlay();
    }

    private void SetBusy(bool busy, string? message = null)
    {
        _isBusy = busy;
        UpdateSourceActionAvailability();
        if (busy && message is not null) SetStatus(message);
    }

    private void UpdateSourceActionAvailability()
    {
        if (StartCaptureButton is null || PreviewSourceButton is null || PrepareYoutubeButton is null || StopYoutubeButton is null) return;

        var hasLocalPath = !string.IsNullOrWhiteSpace(FilePathInput.Text);
        var readyYoutube = _selectedSession is { SourceType: "youtube", CanStartYoutubeAnalysis: true };
        var previewableYoutube = _selectedSession is { SourceType: "youtube", ReadyForCalibration: true };
        StartCaptureButton.IsEnabled = !_isBusy && (_youtubeSourceSelected ? readyYoutube : hasLocalPath);
        PreviewSourceButton.IsEnabled = !_isBusy && (_youtubeSourceSelected ? previewableYoutube : hasLocalPath);
        PrepareYoutubeButton.IsEnabled = !_isBusy && _youtubeSourceSelected;
        StopYoutubeButton.IsEnabled = !_isBusy && _youtubeSourceSelected && _selectedSession is
            { SourceType: "youtube", State: "queued" or "downloading" or "preparing" or "running" or "processing" or "pausing" or "paused" };

        if (!_youtubeSourceSelected)
        {
            StartCaptureButton.Content = "▶  Iniciar bloco";
            return;
        }

        StartCaptureButton.Content = _selectedSession switch
        {
            { CanStartYoutubeAnalysis: true } => "▶  Iniciar análise do bloco",
            { State: "queued" or "downloading" } => "◌  Aguardando download",
            { State: "preparing", ReadyForCalibration: false } => "◌  Preparando vídeo",
            { AnalysisStarted: true, IsComplete: true } => "✓  Bloco concluído",
            { AnalysisStarted: true } => "◌  Análise em andamento",
            _ => "▶  Prepare o vídeo do YouTube"
        };
    }

    private void SetStatus(string message)
    {
        SelectedSessionText.Text = message;
    }

    private void SetError(string message)
    {
        ErrorText.Text = message;
        ErrorText.Visibility = Visibility.Visible;
    }

    private void ClearError() => ErrorText.Visibility = Visibility.Collapsed;

    private void VideoMode_Click(object sender, RoutedEventArgs e)
    {
        SetCaptureModeVisuals(realCapture: false);
        _captureModeChanged?.Invoke("video");
    }

    private async void RealMode_Click(object sender, RoutedEventArgs e) => await ShowRealCaptureAsync();

    public async Task ShowRealCaptureAsync()
    {
        SetCaptureModeVisuals(realCapture: true);
        _captureModeChanged?.Invoke("real");
        await InitializeRealCaptureAsync();
    }

    private void SetCaptureModeVisuals(bool realCapture)
    {
        _realCaptureSelected = realCapture;
        VideoPanel.Visibility = realCapture ? Visibility.Collapsed : Visibility.Visible;
        RealCapturePanel.Visibility = realCapture ? Visibility.Visible : Visibility.Collapsed;
        if (realCapture)
        {
            RealModeButton.Style = (Style)FindResource("PrimaryButton");
            VideoModeButton.ClearValue(StyleProperty);
        }
        else
        {
            VideoModeButton.Style = (Style)FindResource("PrimaryButton");
            RealModeButton.ClearValue(StyleProperty);
        }
    }

    private async Task InitializeRealCaptureAsync()
    {
        if (_runtimeInitialized) return;
        _runtimeInitialized = true;
        VideoPlayer.Pause();
        _isPlaying = false;
        PlayPauseButton.Content = "▶";
        await RefreshRuntimeWindowsAsync();
        if (string.IsNullOrWhiteSpace(_runtimeSessionId)) await RestoreRuntimeSessionAsync();
        if (!string.IsNullOrWhiteSpace(_runtimeSessionId))
        {
            _runtimePollTimer.Start();
            await RefreshRuntimeStatusAsync();
        }
    }

    private async Task RestoreRuntimeSessionAsync()
    {
        try
        {
            var previous = (await _api.GetSessionsAsync(_project.ProjectId)).FirstOrDefault(session =>
                session.SourceType == "game" && session.State is "preparing" or "running" or "paused" or "stopping");
            if (previous is null) return;
            try
            {
                var status = await _api.GetRuntimeStatusAsync(previous.SessionId);
                _runtimeSessionId = previous.SessionId;
                if (status.TryGetProperty("config_json", out var config) &&
                    string.Equals(ReadString(config, "runtime_overlay"), "native", StringComparison.OrdinalIgnoreCase) &&
                    config.TryGetProperty("hwnd", out var hwndValue) && hwndValue.TryGetInt64(out var hwnd))
                    _runtimeOverlay = RuntimeOverlayWindow.ShowForSession(_api, previous.SessionId, hwnd);
            }
            catch
            {
                RuntimeStateText.Text = "Sessão de jogo anterior salva; captura não está ativa para retomar.";
                RuntimeStatsText.Text = "As falas e imagens já salvas continuam no projeto. Selecione a janela para iniciar outra sessão.";
            }
        }
        catch (Exception exception) { SetRuntimeError(exception.Message); }
    }

    private static bool TryReadNumber(string text, out double value)
    {
        return double.TryParse(text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.CurrentCulture, out value)
            || double.TryParse(text, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out value);
    }

    private static string FormatTime(double seconds)
    {
        if (!double.IsFinite(seconds) || seconds < 0) return "—";
        var value = TimeSpan.FromSeconds(seconds);
        return value.TotalHours >= 1 ? value.ToString(@"h\:mm\:ss") : value.ToString(@"mm\:ss");
    }

    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        return true;
    }
}

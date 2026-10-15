using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;
using DubSync.Desktop.Models;
using DubSync.Desktop.Services;

namespace DubSync.Desktop.Views;

public partial class RuntimeOverlayWindow : Window
{
    private static readonly Dictionary<string, RuntimeOverlayWindow> LiveWindows = new(StringComparer.Ordinal);
    private const int GwlExStyle = -20;
    private const int WsExTransparent = 0x00000020;
    private const int WsExToolWindow = 0x00000080;
    private const int WsExNoActivate = 0x08000000;
    private const uint SwpNoActivate = 0x0010;
    private const uint SwpNoSize = 0x0001;
    private const uint SwpNoMove = 0x0002;
    private const uint SwpFrameChanged = 0x0020;
    private static readonly nint HwndTopmost = new(-1);

    private readonly DubSyncApiClient _api;
    private readonly string _sessionId;
    private readonly nint _gameHwnd;
    private readonly RuntimeOverlayToolbarWindow _toolbar;
    private readonly Queue<RuntimePendingEvent> _pendingSpeech = new();
    private readonly HashSet<string> _notifiedEvents = new(StringComparer.Ordinal);
    private readonly DispatcherTimer _followTimer;
    private readonly DispatcherTimer _refreshTimer;
    private JsonArray _regions = [];
    private int _selectedIndex = -1;
    private bool _selectedSpeaker;
    private bool _editing;
    private bool _saving;
    private bool _toolbarMoved;
    private bool _paused;
    private RuntimeSpeechReviewWindow? _speechPopup;
    private string? _drawMode;
    private Point _pointerStart;
    private Box _dragOrigin;
    private Box _previewBox;
    private bool _dragging;
    private bool _creating;
    private bool _closed;
    private double _clientWidth = 1;
    private double _clientHeight = 1;
    private double _scale = 1;
    private double _toolbarOffsetX = 12;
    private double _toolbarOffsetY = 46;

    public static RuntimeOverlayWindow ShowForSession(DubSyncApiClient api, string sessionId, long gameHwnd)
    {
        if (LiveWindows.TryGetValue(sessionId, out var current) && !current._closed)
        {
            if (!current.IsVisible) current.Show();
            return current;
        }
        var overlay = new RuntimeOverlayWindow(api, sessionId, gameHwnd);
        LiveWindows[sessionId] = overlay;
        overlay.Closed += (_, _) => LiveWindows.Remove(sessionId);
        overlay.Show();
        return overlay;
    }

    private readonly record struct Box(double X, double Y, double Width, double Height)
    {
        public double Right => X + Width;
        public double Bottom => Y + Height;
    }

    public RuntimeOverlayWindow(DubSyncApiClient api, string sessionId, long gameHwnd)
    {
        _api = api;
        _sessionId = sessionId;
        _gameHwnd = new IntPtr(gameHwnd);
        InitializeComponent();
        Owner = Application.Current?.MainWindow;
        OverlayCanvas.SizeChanged += (_, _) => RenderRegions();
        _toolbar = new RuntimeOverlayToolbarWindow(HandleToolbarCommand)
        {
            Owner = Application.Current?.MainWindow
        };
        _toolbar.LocationChanged += (_, _) =>
        {
            if (_closed || !_toolbar.IsVisible || !TryGetClientRect(out var rect, out _)) return;
            _toolbarMoved = true;
            _toolbarOffsetX = _toolbar.Left - rect.Left / _scale;
            _toolbarOffsetY = _toolbar.Top - rect.Top / _scale;
        };
        _followTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(120) };
        _followTimer.Tick += (_, _) => FollowGameWindow();
        _refreshTimer = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(1200) };
        _refreshTimer.Tick += async (_, _) => await RefreshRuntimeAsync();
        SourceInitialized += (_, _) => ApplyInputMode();
        Closed += (_, _) => CloseOwnedWindows();
        Loaded += async (_, _) =>
        {
            _followTimer.Start();
            _refreshTimer.Start();
            FollowGameWindow();
            await RefreshRuntimeAsync();
        };
    }

    private void CloseOwnedWindows()
    {
        _closed = true;
        _followTimer.Stop();
        _refreshTimer.Stop();
        _speechPopup?.Close();
        if (_toolbar.IsVisible) _toolbar.Close();
    }

    private void FollowGameWindow()
    {
        if (_closed) return;
        if (!TryGetClientRect(out var rect, out var dpi) || !IsGameOrDubSyncForeground())
        {
            Hide();
            _toolbar.Hide();
            _speechPopup?.Hide();
            return;
        }

        _scale = Math.Max(1, dpi) / 96.0;
        _clientWidth = rect.Width / _scale;
        _clientHeight = rect.Height / _scale;
        Left = rect.Left / _scale;
        Top = rect.Top / _scale;
        Width = Math.Max(1, _clientWidth);
        Height = Math.Max(1, _clientHeight);
        if (!IsVisible) Show();
        if (!_toolbarMoved)
        {
            _toolbarOffsetX = 12;
            _toolbarOffsetY = 46;
        }
        var toolbarLeft = Left + _toolbarOffsetX;
        var toolbarTop = Top + _toolbarOffsetY;
        if (Math.Abs(_toolbar.Left - toolbarLeft) > .5) _toolbar.Left = toolbarLeft;
        if (Math.Abs(_toolbar.Top - toolbarTop) > .5) _toolbar.Top = toolbarTop;
        if (!_toolbar.IsVisible) _toolbar.Show();
        if (_speechPopup is not null)
        {
            PositionSpeechPopup();
            if (!_speechPopup.IsVisible) _speechPopup.Show();
        }
        else TryShowPendingSpeech();
    }

    private bool TryGetClientRect(out PixelRect rect, out uint dpi)
    {
        rect = default;
        dpi = 96;
        if (!IsWindow(_gameHwnd) || IsIconic(_gameHwnd) || !GetClientRect(_gameHwnd, out var client)) return false;
        var topLeft = new NativePoint { X = client.Left, Y = client.Top };
        var bottomRight = new NativePoint { X = client.Right, Y = client.Bottom };
        if (!ClientToScreen(_gameHwnd, ref topLeft) || !ClientToScreen(_gameHwnd, ref bottomRight)) return false;
        var width = bottomRight.X - topLeft.X;
        var height = bottomRight.Y - topLeft.Y;
        if (width < 80 || height < 80) return false;
        dpi = GetDpiForWindow(_gameHwnd);
        if (dpi < 48) dpi = 96;
        rect = new PixelRect(topLeft.X, topLeft.Y, width, height);
        return true;
    }

    private bool IsGameOrDubSyncForeground()
    {
        var foreground = GetForegroundWindow();
        if (foreground == 0) return false;
        GetWindowThreadProcessId(foreground, out var processId);
        var ownProcessId = (uint)Process.GetCurrentProcess().Id;
        if (processId == GetProcessIdForWindow(_gameHwnd)) return true;
        if (processId != ownProcessId) return false;
        var mainHandle = Application.Current?.MainWindow is Window main
            ? new WindowInteropHelper(main).Handle : 0;
        return mainHandle == 0 || foreground != mainHandle;
    }

    private static uint GetProcessIdForWindow(nint hwnd)
    {
        GetWindowThreadProcessId(hwnd, out var pid);
        return pid;
    }

    private void ApplyInputMode()
    {
        var hwnd = new WindowInteropHelper(this).Handle;
        if (hwnd == 0) return;
        var style = GetWindowLongPtr(hwnd, GwlExStyle).ToInt64();
        style |= WsExToolWindow;
        if (_editing) style &= ~(long)(WsExTransparent | WsExNoActivate);
        else style |= WsExTransparent | WsExNoActivate;
        SetWindowLongPtr(hwnd, GwlExStyle, new nint(style));
        SetWindowPos(hwnd, HwndTopmost, 0, 0, 0, 0, SwpNoMove | SwpNoSize | SwpNoActivate | SwpFrameChanged);
    }

    private async Task RefreshRuntimeAsync()
    {
        if (_closed || _saving) return;
        try
        {
            var status = await _api.GetRuntimeStatusAsync(_sessionId);
            var state = String(status, "state");
            _paused = state == "paused";
            if (status.TryGetProperty("regions", out var regions) && regions.ValueKind == JsonValueKind.Array && !_dragging)
            {
                _regions = JsonNode.Parse(regions.GetRawText())?.AsArray() ?? [];
                _selectedIndex = Math.Clamp(_selectedIndex, -1, _regions.Count - 1);
            }
            UpdateToolbarStatus(state);
            RenderRegions();
            var pending = await _api.GetRuntimePendingAsync(_sessionId);
            foreach (var item in pending.Events.AsEnumerable().Reverse())
                if (_notifiedEvents.Add(item.EventId)) _pendingSpeech.Enqueue(item);
            TryShowPendingSpeech();
            if (state is "complete" or "failed" or "stopped")
            {
                _refreshTimer.Stop();
                _ = Dispatcher.BeginInvoke(new Action(Close));
            }
        }
        catch (Exception exception)
        {
            _toolbar.SetStatus("A API da sessão não respondeu: " + exception.Message, _editing,
                SelectedBool("locked"), SelectedBool("enabled"));
        }
    }

    private void TryShowPendingSpeech()
    {
        if (_closed || _speechPopup is not null || _pendingSpeech.Count == 0 || !IsVisible || !IsGameOrDubSyncForeground()) return;
        var item = _pendingSpeech.Dequeue();
        _speechPopup = new RuntimeSpeechReviewWindow(_api, item, () =>
        {
            _speechPopup = null;
            if (!_closed) Dispatcher.BeginInvoke(new Action(TryShowPendingSpeech));
        }) { Owner = this };
        PositionSpeechPopup();
        _speechPopup.Show();
    }

    private void PositionSpeechPopup()
    {
        if (_speechPopup is null) return;
        var left = Left + Math.Max(10, Width - _speechPopup.Width - 18);
        var top = Top + Math.Max(10, Height - _speechPopup.Height - 18);
        if (Math.Abs(_speechPopup.Left - left) > .5) _speechPopup.Left = left;
        if (Math.Abs(_speechPopup.Top - top) > .5) _speechPopup.Top = top;
    }

    private void UpdateToolbarStatus(string state)
    {
        var selected = SelectedRegion();
        var label = state switch
        {
            "running" => _editing ? "Capturando · ajuste de ROI" : "Capturando · clique passa para o jogo",
            "paused" => "Captura pausada",
            "preparing" => "Preparando captura",
            "stopping" => "Salvando evento e encerrando",
            _ => "Captura " + state
        };
        if (selected is not null) label += " · " + String(selected, "name");
        _toolbar.SetStatus(label, _editing, selected is null ? null : Bool(selected, "locked", false),
            selected is null ? null : Bool(selected, "enabled", true), _paused);
    }

    private void RenderRegions()
    {
        if (_closed || !OverlayCanvas.IsLoaded || OverlayCanvas.ActualWidth < 2 || OverlayCanvas.ActualHeight < 2) return;
        OverlayCanvas.Children.Clear();
        for (var index = 0; index < _regions.Count; index++)
        {
            if (_regions[index] is not JsonObject region || !Bool(region, "enabled", true)) continue;
            var box = ReadBox(region);
            var selected = index == _selectedIndex && !_selectedSpeaker;
            AddOutline(box, selected ? Color.FromRgb(58, 217, 191) : Color.FromRgb(81, 160, 212),
                selected ? 2.7 : 1.5, false, $"{(Bool(region, "locked", false) ? "▣ " : "")}{String(region, "name")}", index, false);
            if (Bool(region, "speaker_enabled", false) && region["speaker_region"] is JsonObject speaker)
            {
                var speakerBox = ReadBox(speaker);
                var speakerSelected = index == _selectedIndex && _selectedSpeaker;
                AddOutline(speakerBox, speakerSelected ? Color.FromRgb(255, 197, 92) : Color.FromRgb(255, 197, 92),
                    speakerSelected ? 2.7 : 1.7, true, "PERSONAGEM", index, true);
            }
        }
        if (_creating) AddOutline(_previewBox, Color.FromRgb(255, 197, 92), 2, true,
            _drawMode == "speaker" ? "PERSONAGEM" : "NOVA ROI", -1, _drawMode == "speaker");
    }

    private void AddOutline(Box box, Color color, double thickness, bool dashed, string label, int index, bool speaker)
    {
        var width = OverlayCanvas.ActualWidth;
        var height = OverlayCanvas.ActualHeight;
        var left = box.X * width;
        var top = box.Y * height;
        var rect = new Rectangle
        {
            Width = Math.Max(1, box.Width * width),
            Height = Math.Max(1, box.Height * height),
            Stroke = new SolidColorBrush(color),
            StrokeThickness = thickness,
            Fill = new SolidColorBrush(Color.FromArgb(24, color.R, color.G, color.B)),
            StrokeDashArray = dashed ? [5, 3] : null,
            IsHitTestVisible = false
        };
        Canvas.SetLeft(rect, left);
        Canvas.SetTop(rect, top);
        OverlayCanvas.Children.Add(rect);
        if (!string.IsNullOrWhiteSpace(label))
        {
            var tag = new Border
            {
                Background = new SolidColorBrush(Color.FromArgb(220, 16, 27, 41)),
                BorderBrush = new SolidColorBrush(color),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(3),
                Padding = new Thickness(5, 2, 5, 2),
                Child = new TextBlock { Text = label, FontSize = 11, FontWeight = FontWeights.SemiBold, Foreground = new SolidColorBrush(color) },
                IsHitTestVisible = false
            };
            Canvas.SetLeft(tag, Math.Clamp(left, 0, Math.Max(0, width - 180)));
            Canvas.SetTop(tag, Math.Max(0, top - 22));
            OverlayCanvas.Children.Add(tag);
        }
        if (_editing && index == _selectedIndex && speaker == _selectedSpeaker)
        {
            var handle = new Rectangle { Width = 10, Height = 10, Fill = new SolidColorBrush(color), Stroke = Brushes.White,
                StrokeThickness = 1, IsHitTestVisible = false };
            Canvas.SetLeft(handle, Math.Max(0, (box.Right * width) - 5));
            Canvas.SetTop(handle, Math.Max(0, (box.Bottom * height) - 5));
            OverlayCanvas.Children.Add(handle);
        }
    }

    private void OverlayCanvas_MouseDown(object sender, MouseButtonEventArgs e)
    {
        if (!_editing) return;
        var point = e.GetPosition(OverlayCanvas);
        _pointerStart = point;
        if (_drawMode is not null)
        {
            _creating = true;
            _previewBox = new Box(point.X / _clientWidth, point.Y / _clientHeight, 0, 0);
            OverlayCanvas.CaptureMouse();
            RenderRegions();
            e.Handled = true;
            return;
        }

        var hit = HitTest(point);
        if (hit.Index < 0) return;
        _selectedIndex = hit.Index;
        _selectedSpeaker = hit.Speaker;
        if (SelectedBool("locked") == true)
        {
            UpdateToolbarStatus("running");
            e.Handled = true;
            return;
        }
        _dragOrigin = hit.Speaker ? ReadBox(SelectedRegion()?["speaker_region"] as JsonObject) : ReadBox(SelectedRegion());
        _dragging = true;
        _creating = false;
        OverlayCanvas.CaptureMouse();
        RenderRegions();
        e.Handled = true;
    }

    private void Window_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key != Key.Escape || !_editing) return;
        _editing = false;
        _drawMode = null;
        _dragging = false;
        _creating = false;
        OverlayCanvas.ReleaseMouseCapture();
        ApplyInputMode();
        _toolbar.SetStatus("Ajuste de ROI encerrado", _editing, SelectedBool("locked"), SelectedBool("enabled"), _paused);
        RenderRegions();
        e.Handled = true;
    }

    private void OverlayCanvas_MouseMove(object sender, MouseEventArgs e)
    {
        if (!_editing || (! _dragging && !_creating)) return;
        var point = e.GetPosition(OverlayCanvas);
        if (_creating)
        {
            var left = Math.Min(_pointerStart.X, point.X) / _clientWidth;
            var top = Math.Min(_pointerStart.Y, point.Y) / _clientHeight;
            var right = Math.Max(_pointerStart.X, point.X) / _clientWidth;
            var bottom = Math.Max(_pointerStart.Y, point.Y) / _clientHeight;
            _previewBox = Clamp(new Box(left, top, right - left, bottom - top));
        }
        else if (_selectedIndex >= 0 && SelectedRegion() is JsonObject region)
        {
            var dx = (point.X - _pointerStart.X) / _clientWidth;
            var dy = (point.Y - _pointerStart.Y) / _clientHeight;
            var resize = Math.Abs(_pointerStart.X / _clientWidth - _dragOrigin.Right) < 0.025 &&
                         Math.Abs(_pointerStart.Y / _clientHeight - _dragOrigin.Bottom) < 0.025;
            var next = resize
                ? Clamp(_dragOrigin with { Width = _dragOrigin.Width + dx, Height = _dragOrigin.Height + dy })
                : Clamp(_dragOrigin with { X = _dragOrigin.X + dx, Y = _dragOrigin.Y + dy });
            if (_selectedSpeaker && region["speaker_region"] is JsonObject speaker)
                WriteBox(speaker, next);
            else
            {
                WriteBox(region, next);
                if (region["speaker_region"] is JsonObject speakerBounds)
                {
                    var old = _dragOrigin;
                    var speakerBox = ReadBox(speakerBounds);
                    WriteBox(speakerBounds, Clamp(speakerBox with { X = speakerBox.X + next.X - old.X, Y = speakerBox.Y + next.Y - old.Y }));
                }
            }
        }
        RenderRegions();
    }

    private async void OverlayCanvas_MouseUp(object sender, MouseButtonEventArgs e)
    {
        if (!_editing || (!_dragging && !_creating)) return;
        OverlayCanvas.ReleaseMouseCapture();
        if (_creating)
        {
            if (_previewBox.Width < 0.012 || _previewBox.Height < 0.012)
            {
                _toolbar.SetStatus("A área ficou pequena. Arraste um retângulo maior.", _editing, null, null);
            }
            else if (_drawMode == "speaker")
            {
                    if (SelectedRegion() is JsonObject region && !Bool(region, "locked", false))
                {
                    region["speaker_enabled"] = true;
                    region["speaker_region"] = BoxNode(_previewBox);
                    _selectedSpeaker = true;
                    try { await SaveRegionsAsync(); }
                    catch (Exception exception) { _toolbar.SetStatus("Falha ao salvar a área de personagem: " + exception.Message, _editing, SelectedBool("locked"), SelectedBool("enabled")); }
                }
                else _toolbar.SetStatus("Selecione uma ROI de diálogo antes de marcar o personagem.", _editing, null, null);
            }
            else
            {
                var index = 1;
                var names = _regions.OfType<JsonObject>().Select(item => String(item, "name")).ToHashSet(StringComparer.OrdinalIgnoreCase);
                while (names.Contains($"ROI {index}")) index++;
                _regions.Add(new JsonObject
                {
                    ["roi_id"] = Guid.NewGuid().ToString(), ["name"] = $"ROI {index}",
                    ["x"] = _previewBox.X, ["y"] = _previewBox.Y, ["width"] = _previewBox.Width, ["height"] = _previewBox.Height,
                    ["enabled"] = true, ["locked"] = false,
                    ["lifetime"] = new JsonObject { ["enabled"] = false, ["windows"] = new JsonArray() },
                    ["motion"] = new JsonObject { ["enabled"] = false, ["keyframes"] = new JsonArray() },
                    ["speaker_enabled"] = false, ["speaker_region"] = null,
                    ["character_detection"] = new JsonObject { ["enabled"] = true, ["prefix_before_colon"] = true, ["color_rules"] = new JsonArray() }
                });
                _selectedIndex = _regions.Count - 1;
                _selectedSpeaker = false;
                try { await SaveRegionsAsync(); }
                catch (Exception exception) { _toolbar.SetStatus("Falha ao salvar a nova ROI: " + exception.Message, _editing, SelectedBool("locked"), SelectedBool("enabled")); }
            }
            _drawMode = null;
            _creating = false;
        }
        else if (_dragging)
        {
            _dragging = false;
            try { await SaveRegionsAsync(); }
            catch (Exception exception) { _toolbar.SetStatus("Falha ao salvar as dimensões da ROI: " + exception.Message, _editing, SelectedBool("locked"), SelectedBool("enabled")); }
        }
        RenderRegions();
        e.Handled = true;
    }

    private (int Index, bool Speaker) HitTest(Point point)
    {
        var x = point.X / _clientWidth;
        var y = point.Y / _clientHeight;
        for (var index = _regions.Count - 1; index >= 0; index--)
        {
            if (_regions[index] is not JsonObject region || !Bool(region, "enabled", true)) continue;
            if (Bool(region, "speaker_enabled", false) && region["speaker_region"] is JsonObject speaker && Contains(ReadBox(speaker), x, y))
                return (index, true);
            if (Contains(ReadBox(region), x, y)) return (index, false);
        }
        return (-1, false);
    }

    private void HandleToolbarCommand(string command)
    {
        _ = HandleToolbarCommandAsync(command);
    }

    private async Task HandleToolbarCommandAsync(string command)
    {
        try
        {
            switch (command)
            {
                case "edit":
                    _editing = !_editing;
                    _drawMode = null;
                    ApplyInputMode();
                    _toolbar.SetStatus(_editing ? "Ajuste ativo · arraste para mover, canto inferior direito para redimensionar" : "Clique passa para o jogo", _editing, SelectedBool("locked"), SelectedBool("enabled"));
                    RenderRegions();
                    break;
                case "dialogue":
                    _editing = true; _drawMode = "dialogue"; ApplyInputMode();
                    _toolbar.SetStatus("Arraste sobre o jogo para desenhar uma nova ROI de diálogo.", true, null, null);
                    break;
                case "speaker":
                    if (SelectedRegion() is null) { _toolbar.SetStatus("Selecione uma ROI de diálogo primeiro.", _editing, null, null); break; }
                    _editing = true; _drawMode = "speaker"; ApplyInputMode();
                    _toolbar.SetStatus("Arraste sobre o jogo para marcar a área do personagem.", true, null, null);
                    break;
                case "duplicate": DuplicateSelected(); await SaveRegionsAsync(); break;
                case "rename": if (RenameSelected()) await SaveRegionsAsync(); break;
                case "lock": if (ToggleSelected("locked")) await SaveRegionsAsync(); break;
                case "hide": if (ToggleSelected("enabled")) await SaveRegionsAsync(); break;
                case "delete": if (DeleteSelected()) await SaveRegionsAsync(); break;
                case "orientation": _toolbar.ToggleOrientation(); _toolbarMoved = false; break;
                case "pause":
                    await _api.PostEmptyAsync($"api/runtime-capture/{Uri.EscapeDataString(_sessionId)}/{(_paused ? "resume" : "pause")}");
                    await RefreshRuntimeAsync();
                    break;
                case "stop":
                    await _api.PostEmptyAsync($"api/runtime-capture/{Uri.EscapeDataString(_sessionId)}/stop");
                    _toolbar.SetStatus("Salvando a fala atual antes de encerrar…", _editing, null, null);
                    break;
            }
        }
        catch (Exception exception)
        {
            _toolbar.SetStatus("Não foi possível aplicar a ação: " + exception.Message, _editing,
                SelectedBool("locked"), SelectedBool("enabled"));
        }
    }

    private void DuplicateSelected()
    {
        if (SelectedRegion() is not JsonObject region) return;
        var clone = (JsonObject)region.DeepClone();
        clone["roi_id"] = Guid.NewGuid().ToString();
        clone["name"] = String(region, "name") + " cópia";
        var box = ReadBox(region);
        var dx = box.X + 0.025 > 1 - box.Width ? -0.025 : 0.025;
        var dy = box.Y + 0.025 > 1 - box.Height ? -0.025 : 0.025;
        WriteBox(clone, Clamp(box with { X = box.X + dx, Y = box.Y + dy }));
        if (clone["speaker_region"] is JsonObject speaker)
        {
            var speakerBox = ReadBox(speaker);
            WriteBox(speaker, Clamp(speakerBox with { X = speakerBox.X + dx, Y = speakerBox.Y + dy }));
        }
        _regions.Add(clone);
        _selectedIndex = _regions.Count - 1;
        // Duplicating a speaker sub-area duplicates its owning ROI and its speaker area.
    }

    private bool RenameSelected()
    {
        var region = SelectedRegion();
        if (region is null) return false;
        var dialog = new Window
        {
            Title = "Renomear ROI", Width = 360, Height = 142, WindowStartupLocation = WindowStartupLocation.CenterOwner,
            ResizeMode = ResizeMode.NoResize, WindowStyle = WindowStyle.ToolWindow, Owner = _toolbar,
            Topmost = true,
            Background = new SolidColorBrush(Color.FromRgb(19, 30, 44)), Foreground = Brushes.White
        };
        var input = new TextBox { Text = String(region, "name"), Margin = new Thickness(12), Padding = new Thickness(7) };
        var save = new Button { Content = "Salvar nome", IsDefault = true, Margin = new Thickness(12, 0, 12, 10), Padding = new Thickness(8) };
        save.Click += (_, _) => { dialog.DialogResult = true; dialog.Close(); };
        var layout = new DockPanel();
        DockPanel.SetDock(save, Dock.Bottom);
        layout.Children.Add(save); layout.Children.Add(input);
        dialog.Content = layout;
        if (dialog.ShowDialog() == true && !string.IsNullOrWhiteSpace(input.Text))
        {
            var name = input.Text.Trim();
            region["name"] = name[..Math.Min(name.Length, 48)];
            return true;
        }
        return false;
    }

    private bool ToggleSelected(string key)
    {
        if (SelectedRegion() is not JsonObject region) return false;
        if (key == "enabled" && Bool(region, key, true) && !HasOtherEnabledRegion(_selectedIndex))
        {
            _toolbar.SetStatus("Mantenha ao menos uma ROI de diálogo visível para a captura.", _editing, SelectedBool("locked"), SelectedBool("enabled"), _paused);
            return false;
        }
        if (key == "enabled") region[key] = !Bool(region, key, true);
        else region[key] = !Bool(region, key, false);
        return true;
    }

    private bool DeleteSelected()
    {
        if (SelectedRegion() is not JsonObject region) return false;
        if (_selectedSpeaker)
        {
            region["speaker_enabled"] = false;
            region["speaker_region"] = null;
            _selectedSpeaker = false;
        }
        else
        {
            if (_regions.Count <= 1 || (Bool(region, "enabled", true) && !HasOtherEnabledRegion(_selectedIndex)))
            {
                _toolbar.SetStatus("Mantenha pelo menos uma ROI de diálogo visível.", _editing, null, null, _paused);
                return false;
            }
            _regions.RemoveAt(_selectedIndex);
            _selectedIndex = Math.Min(_selectedIndex, _regions.Count - 1);
        }
        return true;
    }

    private bool HasOtherEnabledRegion(int exceptIndex) => _regions
        .Select((item, index) => (item, index))
        .Any(pair => pair.index != exceptIndex && pair.item is JsonObject region && Bool(region, "enabled", true));

    private async Task SaveRegionsAsync()
    {
        if (_saving) return;
        _saving = true;
        try
        {
            var payload = new JsonArray(_regions.Select(item => item?.DeepClone()).ToArray());
            var response = await _api.PutAsync($"api/runtime-capture/{Uri.EscapeDataString(_sessionId)}/regions", new { regions = payload });
            if (response.TryGetProperty("regions", out var regions) && regions.ValueKind == JsonValueKind.Array)
                _regions = JsonNode.Parse(regions.GetRawText())?.AsArray() ?? payload;
            _toolbar.SetStatus("ROI salva · captura continua", _editing, SelectedBool("locked"), SelectedBool("enabled"));
        }
        finally
        {
            _saving = false;
            RenderRegions();
        }
    }

    private JsonObject? SelectedRegion() => _selectedIndex >= 0 && _selectedIndex < _regions.Count
        ? _regions[_selectedIndex] as JsonObject : null;

    private bool? SelectedBool(string key) => SelectedRegion() is JsonObject region ? Bool(region, key, key == "enabled") : null;

    private static Box ReadBox(JsonObject? region) => region is null ? new Box(0, 0, 0, 0) :
        new Box(Number(region, "x"), Number(region, "y"), Number(region, "width"), Number(region, "height"));

    private static void WriteBox(JsonObject target, Box box)
    {
        target["x"] = box.X; target["y"] = box.Y; target["width"] = box.Width; target["height"] = box.Height;
    }

    private static JsonObject BoxNode(Box box) => new() { ["x"] = box.X, ["y"] = box.Y, ["width"] = box.Width, ["height"] = box.Height };
    private static bool Contains(Box box, double x, double y) => x >= box.X && x <= box.Right && y >= box.Y && y <= box.Bottom;

    private static Box Clamp(Box box)
    {
        var width = Math.Clamp(box.Width, 0.01, 1.0);
        var height = Math.Clamp(box.Height, 0.01, 1.0);
        return new Box(Math.Clamp(box.X, 0, 1 - width), Math.Clamp(box.Y, 0, 1 - height), width, height);
    }

    private static bool Bool(JsonObject value, string key, bool fallback) => value[key] is JsonValue node && node.TryGetValue<bool>(out var result) ? result : fallback;
    private static double Number(JsonObject value, string key) => value[key] is JsonValue node && node.TryGetValue<double>(out var result) ? result : 0;
    private static string String(JsonElement value, string key) => value.TryGetProperty(key, out var node) && node.ValueKind == JsonValueKind.String ? node.GetString() ?? "" : "";
    private static string String(JsonObject value, string key) => value[key]?.GetValue<string>() ?? "";

    private readonly record struct PixelRect(int Left, int Top, int Width, int Height);
    [StructLayout(LayoutKind.Sequential)] private struct NativeRect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] private struct NativePoint { public int X, Y; }

    [DllImport("user32.dll")] private static extern bool IsWindow(nint hwnd);
    [DllImport("user32.dll")] private static extern bool IsIconic(nint hwnd);
    [DllImport("user32.dll")] private static extern bool GetClientRect(nint hwnd, out NativeRect rect);
    [DllImport("user32.dll")] private static extern bool ClientToScreen(nint hwnd, ref NativePoint point);
    [DllImport("user32.dll")] private static extern nint GetForegroundWindow();
    [DllImport("user32.dll")] private static extern uint GetWindowThreadProcessId(nint hwnd, out uint processId);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(nint hwnd);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern nint GetWindowLongPtr(nint hwnd, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] private static extern nint SetWindowLongPtr(nint hwnd, int index, nint value);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(nint hwnd, nint insertAfter, int x, int y, int cx, int cy, uint flags);
}

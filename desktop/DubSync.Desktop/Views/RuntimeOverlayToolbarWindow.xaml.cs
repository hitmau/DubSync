using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace DubSync.Desktop.Views;

public partial class RuntimeOverlayToolbarWindow : Window
{
    private readonly Action<string> _command;
    private readonly Dictionary<string, Button> _buttons = new();
    private bool _horizontal;

    public RuntimeOverlayToolbarWindow(Action<string> command)
    {
        _command = command;
        InitializeComponent();
        Grip.PreviewMouseLeftButtonDown += Grip_MouseDown;
        foreach (var (id, glyph, hint) in new[]
        {
            ("edit", "✎", "Ajustar ROIs"), ("dialogue", "+T", "Desenhar região de diálogo"),
            ("speaker", "+P", "Marcar região de personagem"), ("duplicate", "⧉", "Duplicar ROI selecionada"),
            ("rename", "Aa", "Renomear ROI selecionada"), ("lock", "L", "Bloquear / desbloquear seleção"),
            ("hide", "◉", "Mostrar / ocultar seleção"), ("delete", "×", "Remover ROI selecionada"),
            ("orientation", "↔", "Alternar orientação da barra"), ("pause", "Ⅱ", "Pausar / continuar captura"),
            ("stop", "■", "Encerrar captura")
        })
        {
            var button = new Button
            {
                Content = glyph,
                Tag = id,
                ToolTip = hint,
                Width = 40,
                Height = 36,
                Margin = new Thickness(2),
                Padding = new Thickness(2),
                FontSize = 14,
                FontWeight = FontWeights.SemiBold,
                Foreground = Brushes.White,
                Background = new SolidColorBrush(Color.FromRgb(37, 54, 75)),
                BorderBrush = new SolidColorBrush(Color.FromRgb(65, 97, 126)),
                BorderThickness = new Thickness(1),
                Cursor = Cursors.Hand
            };
            button.Click += (_, _) => _command(id);
            _buttons[id] = button;
        }
        RebuildButtons();
    }

    public void SetStatus(string text, bool editing, bool? locked, bool? enabled, bool paused = false)
    {
        StatusLabel.Text = text;
        if (_buttons.TryGetValue("edit", out var edit))
        {
            edit.Content = editing ? "✓" : "✎";
            edit.Background = editing ? new SolidColorBrush(Color.FromRgb(24, 141, 125)) :
                new SolidColorBrush(Color.FromRgb(37, 54, 75));
        }
        if (_buttons.TryGetValue("lock", out var lockButton)) lockButton.Content = locked == true ? "🔒" : "L";
        if (_buttons.TryGetValue("hide", out var hideButton)) hideButton.Content = enabled == false ? "◎" : "◉";
        if (_buttons.TryGetValue("pause", out var pauseButton)) pauseButton.Content = paused ? "▶" : "Ⅱ";
        ToolTip = text;
    }

    public void ToggleOrientation()
    {
        _horizontal = !_horizontal;
        RebuildButtons();
    }

    private void RebuildButtons()
    {
        ButtonStack.Children.Clear();
        ButtonStack.Orientation = _horizontal ? Orientation.Horizontal : Orientation.Vertical;
        DockPanel.SetDock(Grip, _horizontal ? Dock.Left : Dock.Top);
        DockPanel.SetDock(StatusLabel, _horizontal ? Dock.Right : Dock.Bottom);
        StatusLabel.Width = _horizontal ? 205 : 132;
        StatusLabel.Margin = _horizontal ? new Thickness(7, 2, 3, 2) : new Thickness(2, 5, 2, 2);
        Grip.Height = _horizontal ? 44 : 18;
        Grip.Width = _horizontal ? 18 : double.NaN;
        GripGlyph.Text = _horizontal ? "⋮" : "⋮⋮";
        foreach (var button in _buttons.Values) ButtonStack.Children.Add(button);
        Chrome.Padding = new Thickness(4);
        SizeToContent = SizeToContent.WidthAndHeight;
    }

    private void Grip_MouseDown(object sender, MouseButtonEventArgs e)
    {
        try { DragMove(); }
        catch (InvalidOperationException) { }
        e.Handled = true;
    }
}

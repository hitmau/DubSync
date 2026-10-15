using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using DubSync.Desktop.Models;
using DubSync.Desktop.Services;

namespace DubSync.Desktop.Views;

public sealed class RuntimeSpeechReviewWindow : Window
{
    private readonly DubSyncApiClient _api;
    private readonly RuntimePendingEvent _event;
    private readonly TextBox _text;
    private readonly TextBox _character;
    private readonly TextBlock _message;
    private readonly Action _onFinished;
    private bool _busy;

    public RuntimeSpeechReviewWindow(DubSyncApiClient api, RuntimePendingEvent item, Action onFinished)
    {
        _api = api;
        _event = item;
        _onFinished = onFinished;
        Title = "DubSync · legenda detectada";
        Width = 470;
        Height = 205;
        WindowStyle = WindowStyle.None;
        ResizeMode = ResizeMode.NoResize;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        Topmost = true;
        ShowInTaskbar = false;
        ShowActivated = false;
        Focusable = false;

        _text = new TextBox
        {
            Text = item.Text,
            AcceptsReturn = true,
            TextWrapping = TextWrapping.Wrap,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            MinHeight = 62,
            MaxHeight = 72,
            Padding = new Thickness(8),
            FontSize = 14,
            Foreground = Brushes.White,
            Background = new SolidColorBrush(Color.FromRgb(12, 22, 34)),
            BorderBrush = new SolidColorBrush(Color.FromRgb(64, 91, 120))
        };
        _character = new TextBox
        {
            Text = item.Character,
            Margin = new Thickness(0, 6, 0, 0),
            Padding = new Thickness(7, 4, 7, 4),
            FontSize = 11,
            Foreground = Brushes.White,
            Background = new SolidColorBrush(Color.FromRgb(12, 22, 34)),
            BorderBrush = new SolidColorBrush(Color.FromRgb(64, 91, 120))
        };
        _message = new TextBlock { Foreground = new SolidColorBrush(Color.FromRgb(250, 190, 90)), FontSize = 10,
            TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 5, 0, 0), Visibility = Visibility.Collapsed };

        var heading = new DockPanel { LastChildFill = true, Margin = new Thickness(0, 0, 0, 8) };
        var close = MakeButton("Depois", false);
        close.Click += (_, _) => Close();
        DockPanel.SetDock(close, Dock.Right);
        heading.Children.Add(close);
        heading.Children.Add(new TextBlock { Text = "Legenda detectada · confira sem sair do jogo", FontWeight = FontWeights.SemiBold,
            FontSize = 12, Foreground = new SolidColorBrush(Color.FromRgb(153, 226, 215)), VerticalAlignment = VerticalAlignment.Center });

        var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 8, 0, 0) };
        var accept = MakeButton("✓ Confirmar", true);
        accept.Click += async (_, _) => await CompleteAsync(false);
        var correct = MakeButton("✎ Salvar correção", false);
        correct.Click += async (_, _) => await CompleteAsync(true);
        buttons.Children.Add(accept);
        buttons.Children.Add(correct);
        var content = new StackPanel();
        content.Children.Add(heading);
        content.Children.Add(_text);
        content.Children.Add(new TextBlock { Text = "Personagem (opcional)", FontSize = 9,
            Foreground = new SolidColorBrush(Color.FromRgb(165, 185, 207)), Margin = new Thickness(1, 5, 0, 0) });
        content.Children.Add(_character);
        content.Children.Add(_message);
        content.Children.Add(buttons);
        Content = new Border
        {
            Background = new SolidColorBrush(Color.FromArgb(248, 18, 29, 44)),
            BorderBrush = new SolidColorBrush(Color.FromRgb(68, 104, 136)),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(9),
            Padding = new Thickness(12),
            Child = content,
            Effect = new System.Windows.Media.Effects.DropShadowEffect { BlurRadius = 16, ShadowDepth = 3, Opacity = .48 }
        };
        Closed += (_, _) => _onFinished();
    }

    private static Button MakeButton(string label, bool primary) => new()
    {
        Content = label,
        Padding = new Thickness(10, 6, 10, 6),
        Margin = new Thickness(5, 0, 0, 0),
        FontSize = 11,
        FontWeight = FontWeights.SemiBold,
        Foreground = Brushes.White,
        Background = new SolidColorBrush(primary ? Color.FromRgb(20, 135, 116) : Color.FromRgb(37, 54, 75)),
        BorderBrush = new SolidColorBrush(primary ? Color.FromRgb(72, 197, 170) : Color.FromRgb(65, 97, 126)),
        BorderThickness = new Thickness(1),
        Cursor = System.Windows.Input.Cursors.Hand
    };

    private async Task CompleteAsync(bool correct)
    {
        if (_busy) return;
        _busy = true;
        try
        {
            if (correct)
            {
                var text = _text.Text.Trim();
                if (text.Length == 0)
                {
                    _message.Text = "O texto da fala não pode ficar vazio.";
                    _message.Visibility = Visibility.Visible;
                    return;
                }
                var changes = new Dictionary<string, object?> { ["corrected_text"] = text };
                if (!string.IsNullOrWhiteSpace(_character.Text)) changes["character"] = _character.Text.Trim();
                await _api.ReviewEventAsync(_event.EventId, "correct", changes);
            }
            else await _api.ReviewEventAsync(_event.EventId, "accept");
            Close();
        }
        catch (Exception exception)
        {
            _message.Text = "Não foi possível salvar: " + exception.Message;
            _message.Visibility = Visibility.Visible;
        }
        finally { _busy = false; }
    }
}

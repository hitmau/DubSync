using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace DubSync.Desktop.Views;

public sealed class WorkspaceStatusView : UserControl
{
    public WorkspaceStatusView(string eyebrow, string title, string description, string detail)
    {
        Background = Brushes.Transparent;
        var body = new StackPanel { MaxWidth = 760, Margin = new Thickness(42, 40, 42, 40) };
        body.Children.Add(new TextBlock
        {
            Text = eyebrow.ToUpperInvariant(),
            Foreground = (Brush)Application.Current.Resources["TealBrush"],
            FontSize = 11,
            FontWeight = FontWeights.Bold,
        });
        body.Children.Add(new TextBlock
        {
            Text = title,
            FontSize = 30,
            FontWeight = FontWeights.Bold,
            Margin = new Thickness(0, 8, 0, 8),
            TextWrapping = TextWrapping.Wrap
        });
        body.Children.Add(new TextBlock
        {
            Text = description,
            Foreground = (Brush)Application.Current.Resources["MutedBrush"],
            FontSize = 15,
            TextWrapping = TextWrapping.Wrap
        });

        var card = new Border
        {
            Background = (Brush)Application.Current.Resources["SurfaceBrush"],
            BorderBrush = (Brush)Application.Current.Resources["BorderBrushSoft"],
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(14),
            Padding = new Thickness(19),
            Margin = new Thickness(0, 25, 0, 0)
        };
        card.Child = new TextBlock
        {
            Text = detail,
            Foreground = (Brush)Application.Current.Resources["MutedBrush"],
            FontSize = 13,
            TextWrapping = TextWrapping.Wrap
        };
        body.Children.Add(card);

        Content = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Content = body
        };
    }
}

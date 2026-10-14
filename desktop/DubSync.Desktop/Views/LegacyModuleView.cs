using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using DubSync.Desktop.Models;
using DubSync.Desktop.Services;

namespace DubSync.Desktop.Views;

public sealed class LegacyModuleView : UserControl
{
    public LegacyModuleView(string title, string module, string route, string detail,
        DubSyncApiClient api, ProjectSummary project)
    {
        Background = Brushes.Transparent;
        var body = new StackPanel { MaxWidth = 850, Margin = new Thickness(38, 34, 38, 34) };
        body.Children.Add(new TextBlock
        {
            Text = "MÓDULO EXISTENTE · ACESSO PRESERVADO",
            Foreground = (Brush)Application.Current.Resources["TealBrush"],
            FontSize = 10,
            FontWeight = FontWeights.Bold
        });
        body.Children.Add(new TextBlock
        {
            Text = title,
            FontSize = 28,
            FontWeight = FontWeights.Bold,
            Margin = new Thickness(0, 8, 0, 6),
            TextWrapping = TextWrapping.Wrap
        });
        body.Children.Add(new TextBlock
        {
            Text = $"{project.Name} · {project.GameTitle}",
            Foreground = (Brush)Application.Current.Resources["MutedBrush"],
            FontSize = 12
        });

        var card = new Border
        {
            Background = (Brush)Application.Current.Resources["SurfaceBrush"],
            BorderBrush = (Brush)Application.Current.Resources["BorderBrushSoft"],
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(14),
            Padding = new Thickness(22),
            Margin = new Thickness(0, 22, 0, 0)
        };
        var content = new StackPanel();
        content.Children.Add(new TextBlock
        {
            Text = $"{module} continua disponível no projeto atual.",
            FontSize = 17,
            FontWeight = FontWeights.SemiBold
        });
        content.Children.Add(new TextBlock
        {
            Text = detail,
            Foreground = (Brush)Application.Current.Resources["MutedBrush"],
            FontSize = 13,
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 8, 0, 18)
        });
        var open = new Button
        {
            Content = $"Abrir {module} atual  →",
            Style = (Style)Application.Current.Resources["PrimaryButton"],
            Padding = new Thickness(15, 9, 15, 9),
            HorizontalAlignment = HorizontalAlignment.Left
        };
        open.Click += (_, _) =>
        {
            try
            {
                var uri = new Uri(api.BaseAddress, $"projects/{Uri.EscapeDataString(project.ProjectId)}/{route}");
                Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true });
            }
            catch (Exception exception)
            {
                MessageBox.Show(exception.Message, $"Não foi possível abrir {module}", MessageBoxButton.OK, MessageBoxImage.Error);
            }
        };
        content.Children.Add(open);
        card.Child = content;
        body.Children.Add(card);
        Content = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Content = body };
    }
}

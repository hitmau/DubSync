using System.Windows;
using System.Windows.Controls;
using DubSync.Desktop.Models;

namespace DubSync.Desktop.Views;

public partial class ProjectHubView : UserControl
{
    public event EventHandler? RefreshRequested;
    public event EventHandler<ProjectSummary>? OpenProjectRequested;
    public event EventHandler? NewProjectRequested;

    public ProjectHubView() => InitializeComponent();

    private void Refresh_Click(object sender, RoutedEventArgs e) => RefreshRequested?.Invoke(this, EventArgs.Empty);
    private void NewProject_Click(object sender, RoutedEventArgs e) => NewProjectRequested?.Invoke(this, EventArgs.Empty);

    private void OpenProject_Click(object sender, RoutedEventArgs e)
    {
        if ((sender as FrameworkElement)?.Tag is ProjectSummary project)
            OpenProjectRequested?.Invoke(this, project);
    }
}

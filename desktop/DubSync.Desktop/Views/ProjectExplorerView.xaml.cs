using System.Windows;
using System.Windows.Controls;

namespace DubSync.Desktop.Views;

public partial class ProjectExplorerView : UserControl
{
    public event EventHandler? RefreshRequested;
    public ProjectExplorerView() => InitializeComponent();
    private void Refresh_Click(object sender, RoutedEventArgs e) => RefreshRequested?.Invoke(this, EventArgs.Empty);
}

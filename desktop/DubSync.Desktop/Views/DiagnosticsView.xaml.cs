using System.Windows;
using System.Windows.Controls;

namespace DubSync.Desktop.Views;

public partial class DiagnosticsView : UserControl
{
    public event EventHandler? RefreshRequested;
    public DiagnosticsView() => InitializeComponent();
    private void Refresh_Click(object sender, RoutedEventArgs e) => RefreshRequested?.Invoke(this, EventArgs.Empty);
}

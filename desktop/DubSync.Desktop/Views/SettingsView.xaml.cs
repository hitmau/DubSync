using System.Windows;
using System.Windows.Controls;

namespace DubSync.Desktop.Views;

public partial class SettingsView : UserControl
{
    public event EventHandler? SaveRequested;
    public SettingsView() => InitializeComponent();
    private void Save_Click(object sender, RoutedEventArgs e) => SaveRequested?.Invoke(this, EventArgs.Empty);
}

using System.Windows;

namespace DubSync.Desktop.Views;

public partial class OcrRetryWindow : Window
{
    public string CorrectedText => CandidateText.Text.Trim();
    public string? Character => ApplySpeakerCheck.IsChecked == true ? SpeakerText.Text.Trim() : null;

    public OcrRetryWindow(string previousText, string candidateText, string? speakerText,
        bool speakerConfigured, bool speakerHasCandidate, string info)
    {
        InitializeComponent();
        PreviousText.Text = previousText;
        CandidateText.Text = candidateText;
        SpeakerText.Text = speakerText ?? "";
        SpeakerText.Visibility = speakerConfigured ? Visibility.Visible : Visibility.Collapsed;
        ApplySpeakerCheck.Visibility = speakerConfigured && speakerHasCandidate ? Visibility.Visible : Visibility.Collapsed;
        ApplySpeakerCheck.IsChecked = speakerHasCandidate;
        InfoText.Text = info;
        if (!speakerConfigured) ValidationText.Text = "Área de personagem não configurada para esta ROI.";
        else if (speakerConfigured && !speakerHasCandidate) ValidationText.Text = "O nome do personagem não foi reconhecido com confiança suficiente.";
    }

    private void Keep_Click(object sender, RoutedEventArgs e) => DialogResult = false;

    private void Apply_Click(object sender, RoutedEventArgs e)
    {
        var text = CorrectedText;
        var usableText = text.Length > 0 && !text.StartsWith("(Nenhum texto", StringComparison.OrdinalIgnoreCase);
        if (!usableText && string.IsNullOrWhiteSpace(Character))
        {
            ValidationText.Text = "Não há texto nem personagem novo para aplicar.";
            return;
        }
        DialogResult = true;
    }
}

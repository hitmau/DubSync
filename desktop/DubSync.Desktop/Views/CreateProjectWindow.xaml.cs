using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media.Imaging;
using DubSync.Desktop.Services;
using Microsoft.Win32;

namespace DubSync.Desktop.Views;

public partial class CreateProjectWindow : Window
{
    private string? _coverData;

    public bool OpenAfterCreate => OpenAfterCreateInput.IsChecked == true;
    public ProjectCreateRequest? Request { get; private set; }

    public CreateProjectWindow() => InitializeComponent();

    private void ChooseCover_Click(object sender, RoutedEventArgs e)
    {
        var picker = new OpenFileDialog
        {
            Title = "Escolher capa do projeto",
            Filter = "Imagens|*.png;*.jpg;*.jpeg;*.webp|Todos os arquivos|*.*",
            CheckFileExists = true
        };
        if (picker.ShowDialog(this) != true) return;

        var file = new FileInfo(picker.FileName);
        if (file.Length > 8 * 1024 * 1024)
        {
            ShowError("A imagem da capa deve ter no máximo 8 MB.");
            return;
        }

        var bytes = File.ReadAllBytes(file.FullName);
        var extension = file.Extension.ToLowerInvariant();
        var mime = extension switch { ".png" => "image/png", ".webp" => "image/webp", _ => "image/jpeg" };
        _coverData = $"data:{mime};base64,{Convert.ToBase64String(bytes)}";
        var image = new BitmapImage();
        using (var stream = new MemoryStream(bytes))
        {
            image.BeginInit();
            image.CacheOption = BitmapCacheOption.OnLoad;
            image.StreamSource = stream;
            image.EndInit();
            image.Freeze();
        }
        CoverPreview.Source = image;
        CoverPreview.Visibility = Visibility.Visible;
        CoverHint.Visibility = Visibility.Collapsed;
        ErrorText.Visibility = Visibility.Collapsed;
    }

    private void Create_Click(object sender, RoutedEventArgs e)
    {
        var name = NameInput.Text.Trim();
        var gameTitle = GameTitleInput.Text.Trim();
        if (name.Length == 0 || gameTitle.Length == 0)
        {
            ShowError("Informe o nome do projeto e o título do jogo.");
            return;
        }

        var original = OriginalLanguageInput.Text.Trim();
        var capture = CaptureLanguageInput.Text.Trim();
        var dub = DubLanguageInput.Text.Trim();
        var languagePattern = new Regex("^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$");
        if (!languagePattern.IsMatch(original) || !languagePattern.IsMatch(capture) || !languagePattern.IsMatch(dub))
        {
            ShowError("Use códigos de idioma como en, pt-BR ou es-MX.");
            return;
        }

        var narrative = (NarrativeInput.SelectedItem as ComboBoxItem)?.Tag?.ToString() ?? "unknown";
        Request = new ProjectCreateRequest
        {
            Name = name,
            GameTitle = gameTitle,
            Slug = CreateSlug(name),
            GameVersion = GameVersionInput.Text.Trim(),
            Platform = PlatformInput.Text.Trim(),
            NarrativeType = narrative,
            OriginalLanguage = original,
            CaptureLanguage = capture,
            DubLanguage = dub,
            CoverData = _coverData
        };
        DialogResult = true;
    }

    private void ShowError(string message)
    {
        ErrorText.Text = message;
        ErrorText.Visibility = Visibility.Visible;
    }

    private static string CreateSlug(string value)
    {
        var normalized = value.Normalize(NormalizationForm.FormD);
        var builder = new StringBuilder();
        foreach (var character in normalized)
        {
            if (System.Globalization.CharUnicodeInfo.GetUnicodeCategory(character) == System.Globalization.UnicodeCategory.NonSpacingMark)
                continue;
            if (char.IsLetterOrDigit(character)) builder.Append(char.ToLowerInvariant(character));
            else if (builder.Length > 0 && builder[^1] != '-') builder.Append('-');
        }
        return builder.ToString().Trim('-');
    }
}

using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using AvalonDock.Core;
using AvalonDock.Layout;
using AvalonDock.Serializer.Json;
using DubSync.Desktop.Models;
using DubSync.Desktop.Services;
using DubSync.Desktop.Views;

namespace DubSync.Desktop;

public partial class MainWindow : Window, INotifyPropertyChanged
{
    private readonly DesktopPreferencesStore _preferencesStore = new();
    private readonly DesktopPreferences _preferences;
    private DubSyncApiClient _api;
    private JsonLayoutSerializer _layoutSerializer = null!;
    private string _defaultLayoutJson = "";
    private ProjectSummary? _selectedProject;
    private string _connectionStatus = "Conectando…";
    private string _healthSummary = "Aguardando serviço local";
    private string _apiPortInput;
    private string _uiScaleSelection;
    private bool _projectsLoaded;

    public event PropertyChangedEventHandler? PropertyChanged;
    public ObservableCollection<ProjectSummary> Projects { get; } = [];

    public ProjectSummary? SelectedProject
    {
        get => _selectedProject;
        set
        {
            if (!SetField(ref _selectedProject, value)) return;
            _preferences.ActiveProjectId = value?.ProjectId;
            _api?.SetProjectScope(value?.ProjectId);
            RaiseMany(nameof(SelectedProjectLabel));
            SavePreferencesQuietly();
        }
    }

    public string SelectedProjectLabel => SelectedProject?.Name ?? "Nenhum projeto";
    public string ApiPortInput { get => _apiPortInput; set => SetField(ref _apiPortInput, value); }
    public string UiScaleSelection { get => _uiScaleSelection; set => SetField(ref _uiScaleSelection, value); }
    public string ConnectionStatus { get => _connectionStatus; private set => SetField(ref _connectionStatus, value); }
    public string HealthSummary { get => _healthSummary; private set => SetField(ref _healthSummary, value); }
    public string ApiEndpoint => _api.Endpoint;
    public Brush ConnectionDotBrush => ConnectionStatus == "Conectado" ? (Brush)FindResource("GreenBrush") : (Brush)FindResource("AmberBrush");
    public Visibility ProjectsVisibility => Projects.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
    public Visibility ProjectsEmptyVisibility => Projects.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
    public string EmptyProjectsTitle => _projectsLoaded ? "Seu primeiro projeto começa aqui" : "Conecte-se ao DubSync";
    public string EmptyProjectsDescription => _projectsLoaded
        ? "Crie um espaço para organizar o jogo, suas falas, personagens e vozes."
        : "Inicie o serviço local do DubSync ou ajuste a porta em Configurações e tente novamente.";

    public MainWindow()
    {
        _preferences = _preferencesStore.Load();
        _apiPortInput = _preferences.ApiPort.ToString();
        _uiScaleSelection = _preferences.UiScale.ToString("0.##", System.Globalization.CultureInfo.InvariantCulture);
        _api = new DubSyncApiClient(_preferences.ApiPort);
        InitializeComponent();
        DataContext = this;
        InitializeLayoutPersistence();
        LoadDockContent(DockManager.Layout);
        _defaultLayoutJson = SerializeLayoutToString();
        ApplyUiScale();
        MarkNavigation(_preferences.ActivePage);
    }

    private void InitializeLayoutPersistence()
    {
        _layoutSerializer = new JsonLayoutSerializer(DockManager);
        _layoutSerializer.LayoutSerializationCallback += (_, args) =>
        {
            args.Content = CreateDockContent(args.Model.ContentId);
        };
    }

    private void LoadDockContent(ILayoutElement root)
    {
        foreach (var content in root.Descendents().OfType<LayoutContent>().ToArray())
            content.Content = CreateDockContent(content.ContentId);
    }

    private object CreateDockContent(string? contentId)
    {
        switch (contentId)
        {
            case "workspace":
                return CreateWorkspaceView(_preferences.ActivePage);
            case "project-explorer":
                var explorer = new ProjectExplorerView { DataContext = this };
                explorer.RefreshRequested += async (_, _) => await RefreshProjectsAsync();
                return explorer;
            case "project-properties":
                return new ProjectPropertiesView { DataContext = this };
            case "diagnostics":
                var diagnostics = new DiagnosticsView { DataContext = this };
                diagnostics.RefreshRequested += async (_, _) => await RefreshProjectsAsync();
                return diagnostics;
            default:
                return new WorkspaceStatusView("Painel", "Painel ainda não disponível", "Este painel foi salvo no layout, mas não pertence à configuração atual.", "Use Layouts → Restaurar layout padrão para voltar aos painéis do DubSync Desktop.");
        }
    }

    private UserControl CreateWorkspaceView(string page)
    {
        if (page == "projects")
        {
            var hub = new ProjectHubView { DataContext = this };
            hub.RefreshRequested += async (_, _) => await RefreshProjectsAsync();
            hub.OpenProjectRequested += async (_, project) => await OpenProjectAsync(project);
            hub.NewProjectRequested += async (_, _) => await CreateProjectAsync();
            return hub;
        }

        if (page == "settings")
        {
            var settings = new SettingsView { DataContext = this };
            settings.SaveRequested += async (_, _) => await SaveSettingsAsync();
            return settings;
        }

        if (page == "capture")
        {
            if (SelectedProject is null)
                return new WorkspaceStatusView("Captura · Fase 1", "Abra ou crie um projeto primeiro", "A captura precisa de um projeto ativo para separar sessões e evidências.", "Selecione um projeto no navegador à esquerda e volte a Captura.");
            return new CaptureView(_api, SelectedProject);
        }

        if (page == "review")
        {
            if (SelectedProject is null)
                return new WorkspaceStatusView("Revisão · Fase 1", "Abra ou crie um projeto primeiro", "A revisão precisa de um projeto ativo para mostrar suas ocorrências.", "Selecione um projeto no navegador à esquerda e volte a Revisão.");
            return new ReviewView(_api, SelectedProject);
        }

        if (page == "dubbing" && SelectedProject is { } dubbingProject)
            return new LegacyModuleView("Dublagem", "Dublagem", "dubbing",
                "Os personagens, referências de voz, takes, filas e operações em lote continuam ligados ao serviço e ao banco atuais durante a migração nativa.",
                _api, dubbingProject);

        if (page == "integration" && SelectedProject is { } integrationProject)
            return new LegacyModuleView("Integração", "Integração", "integration",
                "Perfis de jogo, plugins, publicação e configurações de runtime permanecem disponíveis no módulo atual enquanto esta área aguarda migração.",
                _api, integrationProject);

        if (page == "project-active" && SelectedProject is { } project)
        {
            return new WorkspaceStatusView("Projeto ativo", project.Name,
                $"{project.GameTitle} · {project.Summary.Dialogues} falas · {project.Summary.Characters} personagens",
                "A abertura foi registrada pela API local. A captura nativa está em migração; o fluxo Python existente e seu banco continuam disponíveis durante essa etapa.");
        }

        var pageInfo = page switch
        {
            "capture" => ("FASE 1 · CAPTURA", "Captura por vídeo e Captura Real", "O workspace nativo está preparado para receber os painéis da Fase 1.", "A próxima fatia migrará a captura por vídeo preservando a API, OCR, sessões e evidências atuais. A Captura Real será adicionada como uma modalidade separada."),
            "review" => ("FASE 1 · REVISÃO", "Revisão de falas", "A revisão nativa será ligada às mesmas ocorrências já armazenadas no DubSync.", "Este painel será migrado junto com Captura para manter texto corrigido, status, personagens e ocorrências sincronizados com o banco atual."),
            "dubbing" => ("FASE POSTERIOR", "Dublagem", "A migração desta etapa começa depois da estabilização de Captura.", "Personagens, perfis de voz, takes e geração em lote permanecem no DubSync atual durante esta fase."),
            "integration" => ("FASE POSTERIOR", "Integração", "Os adapters e a publicação serão migrados em uma etapa futura.", "Nenhum dado ou configuração do módulo atual é alterado pelo shell nativo."),
            "gameplay" => ("FASE FUTURA", "Hora da Jogatina", "O modo de acompanhamento ao jogar será desenvolvido após a Captura Real.", "A captura será seletiva por janela e ROI, com OCR condicionado a mudanças para reduzir o impacto no FPS."),
            _ => ("WORKSPACE", "DubSync Desktop", "Escolha uma área no menu superior.", "Os painéis podem ser reposicionados e o layout fica salvo no perfil do Windows.")
        };
        return new WorkspaceStatusView(pageInfo.Item1, pageInfo.Item2, pageInfo.Item3, pageInfo.Item4);
    }

    private async void Window_Loaded(object sender, RoutedEventArgs e)
    {
        try
        {
            if (File.Exists(_preferencesStore.LayoutPath))
            {
                try
                {
                    using var stream = File.OpenRead(_preferencesStore.LayoutPath);
                    _layoutSerializer.Deserialize(stream);
                }
                catch (Exception exception) when (exception is IOException or JsonException or InvalidOperationException)
                {
                    HealthSummary = "Layout inválido; usando a configuração padrão";
                    LoadDockContent(DockManager.Layout);
                }
            }
            await RefreshProjectsAsync();
            SetWorkspace(_preferences.ActivePage);
        }
        catch (Exception exception)
        {
            SetDisconnected(exception.Message);
        }
    }

    private async Task RefreshProjectsAsync()
    {
        try
        {
            var health = await _api.GetHealthAsync();
            var projects = await _api.GetProjectsAsync();
            Projects.Clear();
            foreach (var project in projects) Projects.Add(project);
            _projectsLoaded = true;

            var selectedId = _preferences.ActiveProjectId;
            SelectedProject = Projects.FirstOrDefault(project => project.ProjectId == selectedId)
                ?? (SelectedProject is null ? Projects.FirstOrDefault() : Projects.FirstOrDefault(project => project.ProjectId == SelectedProject.ProjectId));
            ConnectionStatus = "Conectado";
            HealthSummary = $"OCR {health.AnalysisFps} FPS · captura alvo {health.CaptureFpsTarget} FPS";
            RaiseMany(nameof(ConnectionDotBrush), nameof(ApiEndpoint), nameof(ProjectsVisibility), nameof(ProjectsEmptyVisibility), nameof(EmptyProjectsTitle), nameof(EmptyProjectsDescription));
        }
        catch (Exception exception)
        {
            SetDisconnected(exception.Message);
        }
    }

    private void SetDisconnected(string detail)
    {
        ConnectionStatus = "Desconectado";
        HealthSummary = string.IsNullOrWhiteSpace(detail) ? "Inicie o serviço DubSync" : detail;
        RaiseMany(nameof(ConnectionDotBrush), nameof(ApiEndpoint), nameof(ProjectsVisibility), nameof(ProjectsEmptyVisibility), nameof(EmptyProjectsTitle), nameof(EmptyProjectsDescription));
    }

    private async Task OpenProjectAsync(ProjectSummary project)
    {
        try
        {
            SelectedProject = await _api.OpenProjectAsync(project.ProjectId);
            SetWorkspace("project-active");
        }
        catch (Exception exception)
        {
            ShowError("Não foi possível abrir o projeto", exception.Message);
        }
    }

    private async Task CreateProjectAsync()
    {
        var dialog = new CreateProjectWindow { Owner = this };
        if (dialog.ShowDialog() != true || dialog.Request is null) return;
        try
        {
            var created = await _api.CreateProjectAsync(dialog.Request);
            await RefreshProjectsAsync();
            if (dialog.OpenAfterCreate)
                await OpenProjectAsync(created);
            else
                SelectedProject = Projects.FirstOrDefault(item => item.ProjectId == created.ProjectId) ?? created;
        }
        catch (Exception exception)
        {
            ShowError("Não foi possível criar o projeto", exception.Message);
        }
    }

    private async Task SaveSettingsAsync()
    {
        if (!int.TryParse(ApiPortInput, out var port) || port is < 1 or > 65535)
        {
            ShowError("Porta inválida", "Informe um número entre 1 e 65535.");
            return;
        }
        if (!double.TryParse(UiScaleSelection, System.Globalization.NumberStyles.Number,
                System.Globalization.CultureInfo.InvariantCulture, out var scale) || scale is < 0.8 or > 1.6)
        {
            ShowError("Escala inválida", "Escolha uma escala entre 80% e 160%.");
            return;
        }

        _preferences.ApiPort = port;
        _preferences.UiScale = scale;
        try
        {
            _preferencesStore.Save(_preferences);
            _api.Dispose();
            _api = new DubSyncApiClient(port);
            _api.SetProjectScope(SelectedProject?.ProjectId);
            ApplyUiScale();
            RaiseMany(nameof(ApiEndpoint));
            await RefreshProjectsAsync();
            MessageBox.Show(this, "Preferências salvas. A conexão local foi atualizada.", "DubSync Desktop", MessageBoxButton.OK, MessageBoxImage.Information);
        }
        catch (Exception exception)
        {
            ShowError("Não foi possível salvar as preferências", exception.Message);
        }
    }

    private void ApplyUiScale() => RootGrid.LayoutTransform = new ScaleTransform(_preferences.UiScale, _preferences.UiScale);

    private void SetWorkspace(string page)
    {
        if (page is not ("projects" or "settings" or "project-active" or "capture" or "review" or "dubbing" or "integration" or "gameplay"))
            page = "projects";
        _preferences.ActivePage = page;
        SavePreferencesQuietly();
        var document = DockManager.Layout.Descendents().OfType<LayoutDocument>().FirstOrDefault(item => item.ContentId == "workspace");
        if (document is null) return;
        document.Content = CreateWorkspaceView(page);
        document.Title = page switch
        {
            "projects" => "Projetos",
            "settings" => "Configurações",
            "project-active" => SelectedProject?.Name ?? "Projeto",
            "capture" => "Captura",
            "review" => "Revisão",
            "dubbing" => "Dublagem",
            "integration" => "Integração",
            "gameplay" => "Hora da Jogatina",
            _ => "Workspace"
        };
        document.IsSelected = true;
        MarkNavigation(page);
    }

    private void MarkNavigation(string page)
    {
        var mapping = new Dictionary<string, Button>
        {
            ["projects"] = ProjectsNav,
            ["project-active"] = ProjectsNav,
            ["capture"] = CaptureNav,
            ["review"] = ReviewNav,
            ["dubbing"] = DubbingNav,
            ["integration"] = IntegrationNav,
            ["gameplay"] = GameplayNav,
            ["settings"] = SettingsNav
        };
        foreach (var (key, button) in mapping)
        {
            var active = key == page || (page == "project-active" && key == "projects");
            button.Background = active ? new SolidColorBrush(Color.FromRgb(26, 72, 119)) : Brushes.Transparent;
            button.BorderBrush = active ? new SolidColorBrush(Color.FromRgb(46, 112, 178)) : Brushes.Transparent;
            button.FontWeight = active ? FontWeights.SemiBold : FontWeights.Normal;
        }
    }

    private void Navigate_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: string page }) SetWorkspace(page);
    }

    private void Layouts_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button button || button.ContextMenu is null) return;
        button.ContextMenu.PlacementTarget = button;
        button.ContextMenu.IsOpen = true;
    }

    private void SaveLayout_Click(object sender, RoutedEventArgs e) => SaveLayout();

    private void ResetLayout_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            _layoutSerializer.Deserialize(_defaultLayoutJson);
            SaveLayout();
        }
        catch (Exception exception)
        {
            ShowError("Não foi possível restaurar o layout padrão", exception.Message);
        }
    }

    private void ShowExplorer_Click(object sender, RoutedEventArgs e) => ShowAnchorable("project-explorer");
    private void ShowProperties_Click(object sender, RoutedEventArgs e) => ShowAnchorable("project-properties");
    private void ShowDiagnostics_Click(object sender, RoutedEventArgs e) => ShowAnchorable("diagnostics");

    private void ShowAnchorable(string contentId)
    {
        var panel = DockManager.Layout.Descendents().OfType<LayoutAnchorable>().FirstOrDefault(item => item.ContentId == contentId)
            ?? DockManager.Layout.Hidden.FirstOrDefault(item => item.ContentId == contentId);
        if (panel is null)
        {
            var title = contentId switch
            {
                "project-explorer" => "Navegador de projetos",
                "project-properties" => "Propriedades do projeto",
                "diagnostics" => "Conexão e atividade",
                _ => null
            };
            if (title is null) return;

            var horizontalRoot = DockManager.Layout.RootPanel.Descendents().OfType<LayoutPanel>()
                .FirstOrDefault(item => item.Orientation == Orientation.Horizontal);
            if (horizontalRoot is null) return;

            var pane = new LayoutAnchorablePane { DockWidth = new GridLength(contentId == "project-explorer" ? 250 : 285) };
            panel = new LayoutAnchorable
            {
                Title = title,
                ContentId = contentId,
                CanClose = true,
                CanHide = true,
                CanFloat = true,
                Content = CreateDockContent(contentId),
                IsSelected = true
            };
            pane.Children.Add(panel);
            horizontalRoot.Children.Add(pane);
        }
        panel.Show();
    }

    private string SerializeLayoutToString()
    {
        using var writer = new StringWriter();
        _layoutSerializer.Serialize(writer);
        return writer.ToString();
    }

    private void SaveLayout()
    {
        try
        {
            Directory.CreateDirectory(_preferencesStore.DirectoryPath);
            var temporaryPath = _preferencesStore.LayoutPath + ".tmp";
            using (var stream = File.Create(temporaryPath)) _layoutSerializer.Serialize(stream);
            File.Move(temporaryPath, _preferencesStore.LayoutPath, overwrite: true);
            HealthSummary = "Layout salvo no perfil do Windows";
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
        {
            ShowError("Não foi possível salvar o layout", exception.Message);
        }
    }

    private void Window_Closing(object? sender, CancelEventArgs e)
    {
        SaveLayout();
        SavePreferencesQuietly();
        _api.Dispose();
    }

    private void SavePreferencesQuietly()
    {
        try { _preferencesStore.Save(_preferences); }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void ShowError(string title, string message) =>
        MessageBox.Show(this, message, title, MessageBoxButton.OK, MessageBoxImage.Warning);

    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return false;
        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        if (propertyName == nameof(ConnectionStatus)) PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(ConnectionDotBrush)));
        return true;
    }

    private void RaiseMany(params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}

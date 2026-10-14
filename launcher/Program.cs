using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace DubSyncLauncher
{
    internal static class Program
    {
        internal static readonly string ProjectDirectory =
            Path.GetFullPath(Path.GetDirectoryName(Application.ExecutablePath));
        internal static readonly Icon AppIcon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        internal static readonly string InstanceId = MakeInstanceId();
        internal static readonly string StopEventName = @"Local\DubSyncStop-" + InstanceId;
        private static readonly string LauncherMutexName = @"Local\DubSyncLauncher-" + InstanceId;
        private static readonly string ShowEventName = @"Local\DubSyncShow-" + InstanceId;

        private static string MakeInstanceId()
        {
            using (SHA256 sha = SHA256.Create())
            {
                byte[] hash = sha.ComputeHash(Encoding.UTF8.GetBytes(ProjectDirectory.ToLowerInvariant()));
                StringBuilder value = new StringBuilder();
                for (int index = 0; index < 6; index++) value.Append(hash[index].ToString("x2"));
                return value.ToString();
            }
        }

        [STAThread]
        private static void Main(string[] args)
        {
            bool startup = Array.Exists(args, delegate(string arg) {
                return string.Equals(arg, "--startup", StringComparison.OrdinalIgnoreCase);
            });
            bool created;
            using (Mutex mutex = new Mutex(true, LauncherMutexName, out created))
            {
                if (!created)
                {
                    if (!startup)
                    {
                        try
                        {
                            using (EventWaitHandle show = EventWaitHandle.OpenExisting(ShowEventName))
                                show.Set();
                        }
                        catch (WaitHandleCannotBeOpenedException) { }
                    }
                    return;
                }
                try
                {
                    using (EventWaitHandle show = new EventWaitHandle(
                        false, EventResetMode.AutoReset, ShowEventName))
                    {
                        Application.EnableVisualStyles();
                        Application.SetCompatibleTextRenderingDefault(false);
                        Application.Run(new LauncherForm(startup, show));
                    }
                }
                finally { mutex.ReleaseMutex(); }
            }
        }
    }

    internal sealed class LauncherForm : Form
    {
        private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
        private const string RunValue = "DubSyncLauncher";
        private readonly string portFile = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "DubSync", "port.txt");
        private readonly string logFile = Path.Combine(Program.ProjectDirectory, ".data", "launcher.log");
        private readonly EventWaitHandle showEvent;
        private readonly NotifyIcon tray = new NotifyIcon();
        private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        private readonly Label status = new Label();
        private readonly Label detail = new Label();
        private readonly LinkLabel localAddress = new LinkLabel();
        private readonly LinkLabel lanAddress = new LinkLabel();
        private readonly NumericUpDown portInput = new NumericUpDown();
        private readonly Button applyPort = new Button();
        private readonly Button serviceToggle = new Button();
        private readonly Button restartService = new Button();
        private readonly CheckBox autoStart = new CheckBox();
        private Process serverProcess;
        private int port;
        private int startAttempts;
        private DateTime nextStart = DateTime.MinValue;
        private bool quitting;
        private bool serviceStoppedByUser;
        private bool serviceActionInProgress;
        private bool changingAutoStart;
        private readonly bool startHidden;
        private readonly object logLock = new object();

        internal LauncherForm(bool startup, EventWaitHandle show)
        {
            startHidden = startup;
            showEvent = show;
            port = LoadPort();
            InitializeWindow();
            InitializeTray();
            timer.Interval = 2000;
            timer.Tick += delegate { OnTimer(); };
            Shown += delegate
            {
                if (startHidden) { Hide(); ShowInTaskbar = false; Opacity = 1; }
                UpdateAddresses();
                StartBackend();
                timer.Start();
            };
            FormClosing += OnFormClosing;
        }

        private int LoadPort()
        {
            try
            {
                int saved;
                if (File.Exists(portFile) && int.TryParse(File.ReadAllText(portFile).Trim(), out saved)
                    && saved >= 1 && saved <= 65535) return saved;
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return 8766;
        }

        private void SavePort(int value)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(portFile));
            File.WriteAllText(portFile, value.ToString(), Encoding.ASCII);
        }

        private void InitializeWindow()
        {
            Text = "DubSync — controle";
            Icon = Program.AppIcon;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(470, 265);
            BackColor = Color.FromArgb(17, 28, 44);
            ForeColor = Color.White;
            Font = new Font("Segoe UI", 9F);
            if (startHidden) Opacity = 0;

            Label title = new Label();
            title.Text = "DubSync";
            title.Font = new Font("Segoe UI", 15F, FontStyle.Bold);
            title.SetBounds(18, 12, 145, 31);
            Controls.Add(title);

            status.Text = "Iniciando…";
            status.ForeColor = Color.FromArgb(242, 190, 71);
            status.TextAlign = ContentAlignment.MiddleRight;
            status.SetBounds(158, 17, 294, 24);
            Controls.Add(status);

            localAddress.LinkColor = Color.FromArgb(112, 174, 255);
            localAddress.ActiveLinkColor = Color.White;
            localAddress.AutoEllipsis = true;
            localAddress.SetBounds(19, 57, 430, 22);
            localAddress.LinkClicked += delegate { OpenBrowser(); };
            Controls.Add(localAddress);

            lanAddress.LinkColor = Color.FromArgb(112, 174, 255);
            lanAddress.ActiveLinkColor = Color.White;
            lanAddress.AutoEllipsis = true;
            lanAddress.SetBounds(19, 83, 430, 22);
            lanAddress.LinkClicked += delegate { OpenBrowser(LanUrl()); };
            Controls.Add(lanAddress);

            Label portLabel = new Label();
            portLabel.Text = "Porta";
            portLabel.SetBounds(19, 120, 43, 22);
            Controls.Add(portLabel);

            portInput.Minimum = 1;
            portInput.Maximum = 65535;
            portInput.Value = port;
            portInput.SetBounds(64, 116, 95, 24);
            Controls.Add(portInput);

            applyPort.Text = "Aplicar porta";
            applyPort.SetBounds(166, 114, 111, 28);
            applyPort.Click += delegate { ChangePort(); };
            Controls.Add(applyPort);

            serviceToggle.Text = "Parar";
            serviceToggle.SetBounds(19, 153, 112, 30);
            serviceToggle.Click += delegate { ToggleService(); };
            Controls.Add(serviceToggle);

            restartService.Text = "Reiniciar serviço";
            restartService.SetBounds(139, 153, 138, 30);
            restartService.Click += delegate { Restart(); };
            Controls.Add(restartService);

            autoStart.Text = "Iniciar com o Windows";
            autoStart.AutoSize = true;
            autoStart.BackColor = BackColor;
            autoStart.SetBounds(19, 197, 215, 24);
            autoStart.Checked = IsAutoStartEnabled();
            autoStart.CheckedChanged += OnAutoStartChanged;
            Controls.Add(autoStart);

            detail.ForeColor = Color.FromArgb(169, 186, 211);
            detail.AutoEllipsis = true;
            detail.SetBounds(19, 232, 325, 21);
            Controls.Add(detail);

            Button finish = new Button();
            finish.Text = "Finalizar";
            finish.SetBounds(356, 226, 95, 29);
            finish.Click += delegate { Finish(); };
            Controls.Add(finish);
            UpdateAddresses();
        }

        private void InitializeTray()
        {
            ContextMenuStrip menu = new ContextMenuStrip();
            menu.Items.Add("Mostrar status", null, delegate { ShowWindow(); });
            menu.Items.Add("Abrir DubSync", null, delegate { OpenBrowser(); });
            menu.Items.Add("Copiar endereço", null, delegate { CopyAddress(); });
            menu.Items.Add("Reiniciar servidor", null, delegate { Restart(); });
            menu.Items.Add(new ToolStripSeparator());
            menu.Items.Add("Finalizar", null, delegate { Finish(); });
            tray.Icon = Program.AppIcon;
            tray.Text = "DubSync - iniciando";
            tray.ContextMenuStrip = menu;
            tray.Visible = true;
            tray.DoubleClick += delegate { ShowWindow(); };
        }

        private void OnFormClosing(object sender, FormClosingEventArgs args)
        {
            AppendLog("Janela fechando: " + args.CloseReason + "; finalizar=" + quitting);
            if (!quitting && args.CloseReason != CloseReason.WindowsShutDown)
            {
                args.Cancel = true;
                Hide();
                ShowInTaskbar = false;
                AppendLog("Janela recolhida para a bandeja.");
                return;
            }
            timer.Stop();
            tray.Visible = false;
            tray.Dispose();
        }

        private void ShowWindow()
        {
            ShowInTaskbar = true;
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
            BringToFront();
        }

        private static string LanIp()
        {
            string fallback = null;
            foreach (NetworkInterface adapter in NetworkInterface.GetAllNetworkInterfaces())
            {
                if (adapter.OperationalStatus != OperationalStatus.Up
                    || adapter.NetworkInterfaceType == NetworkInterfaceType.Loopback
                    || adapter.NetworkInterfaceType == NetworkInterfaceType.Tunnel) continue;
                IPInterfaceProperties properties;
                try { properties = adapter.GetIPProperties(); }
                catch (NetworkInformationException) { continue; }
                bool hasGateway = false;
                foreach (GatewayIPAddressInformation gateway in properties.GatewayAddresses)
                    if (gateway.Address.AddressFamily == AddressFamily.InterNetwork
                        && !gateway.Address.Equals(IPAddress.Any)) hasGateway = true;
                foreach (UnicastIPAddressInformation address in properties.UnicastAddresses)
                {
                    IPAddress ip = address.Address;
                    if (ip.AddressFamily != AddressFamily.InterNetwork || IPAddress.IsLoopback(ip)
                        || ip.ToString().StartsWith("169.254.")) continue;
                    if (hasGateway) return ip.ToString();
                    if (fallback == null) fallback = ip.ToString();
                }
            }
            return fallback;
        }

        private string LocalUrl() { return "http://127.0.0.1:" + port + "/"; }
        private string LanUrl()
        {
            string ip = LanIp();
            return ip == null ? null : "http://" + ip + ":" + port + "/";
        }

        private void UpdateAddresses()
        {
            localAddress.Text = "Neste PC: " + LocalUrl();
            string lan = LanUrl();
            lanAddress.Text = lan == null ? "Rede local: endereço indisponível" : "Rede local: " + lan;
            lanAddress.Enabled = lan != null;
        }

        private void OpenBrowser()
        {
            OpenBrowser(LocalUrl());
        }

        private void OpenBrowser(string url)
        {
            if (string.IsNullOrEmpty(url)) return;
            try { Process.Start(url); }
            catch (Exception error) { MessageBox.Show(error.Message, "DubSync"); }
        }

        private void CopyAddress()
        {
            try { Clipboard.SetText(LanUrl() ?? LocalUrl()); }
            catch (Exception error) { MessageBox.Show(error.Message, "DubSync"); }
        }

        private bool ProbeHealth()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(LocalUrl() + "api/health");
                request.Proxy = null;
                request.Timeout = 850;
                request.ReadWriteTimeout = 850;
                using (WebResponse response = request.GetResponse())
                using (StreamReader reader = new StreamReader(response.GetResponseStream()))
                {
                    string body = reader.ReadToEnd();
                    return body.Contains("\"ok\":true") && body.Contains("\"app\":\"DubSync\"");
                }
            }
            catch (WebException) { return false; }
            catch (IOException) { return false; }
        }

        private bool PortIsFree(int candidate)
        {
            TcpListener listener = null;
            try
            {
                listener = new TcpListener(IPAddress.Any, candidate);
                listener.Start();
                return true;
            }
            catch (SocketException) { return false; }
            finally { if (listener != null) listener.Stop(); }
        }

        private void SetStatus(string text, bool running, string explanation)
        {
            status.Text = text;
            status.ForeColor = running ? Color.FromArgb(47, 202, 143)
                : Color.FromArgb(242, 190, 71);
            detail.Text = explanation;
            tray.Text = running ? "DubSync - executando" : "DubSync - " + text;
        }

        private void UpdateServiceButtons(bool running)
        {
            serviceToggle.Text = running ? "Parar" : "Iniciar";
            serviceToggle.Enabled = !serviceActionInProgress;
            restartService.Enabled = !serviceActionInProgress;
        }

        private void OnTimer()
        {
            if (showEvent.WaitOne(0)) ShowWindow();
            UpdateAddresses();
            if (serviceActionInProgress) return;
            if (ProbeHealth())
            {
                startAttempts = 0;
                SetStatus("Executando", true, "Fechar a janela mantém o app na bandeja.");
                serviceStoppedByUser = false;
                UpdateServiceButtons(true);
                return;
            }
            if (serverProcess != null && !serverProcess.HasExited)
            {
                SetStatus("Iniciando…", false, "Aguardando o servidor responder.");
                UpdateServiceButtons(true);
                return;
            }
            if (serviceStoppedByUser)
            {
                SetStatus("Parado", false, "O serviço foi parado. Clique em Iniciar quando quiser retomá-lo.");
                UpdateServiceButtons(false);
                return;
            }
            if (startAttempts >= 3)
            {
                SetStatus("Falha ao iniciar", false, "Confira .data\\launcher.log ou use Reiniciar.");
                UpdateServiceButtons(false);
                return;
            }
            if (DateTime.UtcNow >= nextStart) StartBackend();
        }

        private void AppendLog(string line)
        {
            if (string.IsNullOrWhiteSpace(line)) return;
            try
            {
                lock (logLock)
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(logFile));
                    FileInfo file = new FileInfo(logFile);
                    if (file.Exists && file.Length > 1024 * 1024) File.Delete(logFile);
                    File.AppendAllText(logFile, DateTime.Now.ToString("s") + " " + line + Environment.NewLine);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private void StartBackend()
        {
            if (quitting || ProbeHealth()) return;
            string python = Path.Combine(Program.ProjectDirectory, ".venv", "Scripts", "python.exe");
            if (!File.Exists(python))
            {
                startAttempts = 3;
                SetStatus("Ambiente ausente", false, "Execute .\\setup.ps1 na pasta Dupsync.");
                return;
            }
            if (!PortIsFree(port))
            {
                startAttempts = 3;
                SetStatus("Porta ocupada", false, "Escolha outra porta e clique em Aplicar porta.");
                return;
            }
            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = python;
                info.Arguments = "-X utf8 -u -m Dupsync.app.server --host 0.0.0.0 --port " + port;
                info.WorkingDirectory = Path.GetFullPath(Path.Combine(Program.ProjectDirectory, ".."));
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.WindowStyle = ProcessWindowStyle.Hidden;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                serverProcess = new Process();
                serverProcess.StartInfo = info;
                serverProcess.OutputDataReceived += delegate(object sender, DataReceivedEventArgs data) {
                    AppendLog(data.Data);
                };
                serverProcess.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs data) {
                    AppendLog(data.Data);
                };
                serverProcess.Start();
                serverProcess.BeginOutputReadLine();
                serverProcess.BeginErrorReadLine();
                startAttempts++;
                nextStart = DateTime.UtcNow.AddSeconds(5);
                SetStatus("Iniciando…", false, "Aguardando o servidor responder.");
                UpdateServiceButtons(true);
            }
            catch (Exception error)
            {
                AppendLog("Falha ao iniciar: " + error);
                startAttempts = 3;
                SetStatus("Falha ao iniciar", false, error.Message);
            }
        }

        private bool StopBackend()
        {
            bool signaled = false;
            try
            {
                using (EventWaitHandle stop = EventWaitHandle.OpenExisting(Program.StopEventName))
                {
                    stop.Set();
                    signaled = true;
                }
            }
            catch (WaitHandleCannotBeOpenedException) { }
            if (serverProcess != null)
            {
                try
                {
                    if (!serverProcess.HasExited && !serverProcess.WaitForExit(8000))
                    {
                        serverProcess.Kill();
                        serverProcess.WaitForExit(3000);
                    }
                }
                catch (InvalidOperationException) { }
            }
            if (signaled)
            {
                for (int attempt = 0; attempt < 24 && ProbeHealth(); attempt++)
                    Thread.Sleep(250);
            }
            return !ProbeHealth();
        }

        private void ToggleService()
        {
            bool running = ProbeHealth();
            if (!running && serverProcess != null)
            {
                try { running = !serverProcess.HasExited; }
                catch (InvalidOperationException) { }
            }
            if (running)
            {
                RunStopAction(false);
                return;
            }
            serviceStoppedByUser = false;
            startAttempts = 0;
            StartBackend();
        }

        private void Restart()
        {
            RunStopAction(true);
        }

        private void RunStopAction(bool restartAfterStop)
        {
            if (serviceActionInProgress) return;
            serviceActionInProgress = true;
            serviceToggle.Enabled = false;
            restartService.Enabled = false;
            SetStatus(restartAfterStop ? "Reiniciando…" : "Parando…", false,
                "Encerrando o servidor com segurança.");

            ThreadPool.QueueUserWorkItem(delegate
            {
                bool stopped = false;
                Exception failure = null;
                try { stopped = StopBackend(); }
                catch (Exception error) { failure = error; AppendLog("Falha ao parar serviço: " + error); }

                if (IsDisposed || !IsHandleCreated) return;
                BeginInvoke((MethodInvoker)delegate
                {
                    serviceActionInProgress = false;
                    if (!stopped)
                    {
                        serviceStoppedByUser = false;
                        string message = failure == null
                            ? "Não foi possível encerrar o servidor."
                            : "Falha ao encerrar o servidor: " + failure.Message;
                        SetStatus("Falha ao parar", false, message);
                        UpdateServiceButtons(ProbeHealth());
                        MessageBox.Show(message, "DubSync");
                        return;
                    }

                    if (serverProcess != null)
                    {
                        serverProcess.Dispose();
                        serverProcess = null;
                    }
                    startAttempts = 0;
                    if (restartAfterStop)
                    {
                        serviceStoppedByUser = false;
                        StartBackend();
                        SetStatus("Reiniciando…", false, "Aguardando o servidor responder.");
                        UpdateServiceButtons(true);
                    }
                    else
                    {
                        serviceStoppedByUser = true;
                        SetStatus("Parado", false, "O serviço foi parado. Clique em Iniciar quando quiser retomá-lo.");
                        UpdateServiceButtons(false);
                    }
                });
            });
        }

        private bool UpdateFirewall(int newPort)
        {
            string script = Path.Combine(Program.ProjectDirectory, "launcher", "firewall.ps1");
            if (!File.Exists(script))
            {
                MessageBox.Show("Arquivo launcher\\firewall.ps1 não encontrado.", "DubSync");
                return false;
            }
            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = "powershell.exe";
                info.Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script
                    + "\" -Port " + newPort;
                info.Verb = "runas";
                info.UseShellExecute = true;
                info.WindowStyle = ProcessWindowStyle.Hidden;
                using (Process admin = Process.Start(info))
                {
                    if (!admin.WaitForExit(45000))
                    {
                        MessageBox.Show("A alteração do firewall ainda está em andamento. Aguarde e tente novamente.", "DubSync");
                        return false;
                    }
                    if (admin.ExitCode != 0)
                    {
                        MessageBox.Show("O Windows não liberou a nova porta. A porta anterior foi mantida.", "DubSync");
                        return false;
                    }
                }
                return true;
            }
            catch (Win32Exception error)
            {
                if (error.NativeErrorCode != 1223)
                    MessageBox.Show(error.Message, "DubSync");
                return false;
            }
        }

        private void ChangePort()
        {
            int wanted = Decimal.ToInt32(portInput.Value);
            if (wanted == port) return;
            if (!PortIsFree(wanted))
            {
                MessageBox.Show("Esta porta já está em uso por outro programa.", "DubSync");
                portInput.Value = port;
                return;
            }
            applyPort.Enabled = false;
            serviceStoppedByUser = true;
            SetStatus("Alterando porta…", false, "O Windows solicitará acesso para ajustar o firewall.");
            try
            {
                if (!StopBackend())
                {
                    MessageBox.Show("Feche o servidor que foi iniciado fora do launcher antes de mudar a porta.", "DubSync");
                    return;
                }
                if (!UpdateFirewall(wanted)) return;
                SavePort(wanted);
                port = wanted;
                startAttempts = 0;
                UpdateAddresses();
            }
            catch (Exception error)
            {
                MessageBox.Show(error.Message, "DubSync");
            }
            finally
            {
                portInput.Value = port;
                applyPort.Enabled = true;
                startAttempts = 0;
                serviceStoppedByUser = false;
                StartBackend();
            }
        }

        private bool IsAutoStartEnabled()
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(RunKey, false))
                {
                    string value = key == null ? null : key.GetValue(RunValue) as string;
                    return value != null && value.IndexOf(Application.ExecutablePath,
                        StringComparison.OrdinalIgnoreCase) >= 0;
                }
            }
            catch (Exception) { return false; }
        }

        private void OnAutoStartChanged(object sender, EventArgs args)
        {
            if (changingAutoStart) return;
            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(RunKey))
                {
                    if (autoStart.Checked)
                        key.SetValue(RunValue, "\"" + Application.ExecutablePath + "\" --startup",
                            RegistryValueKind.String);
                    else key.DeleteValue(RunValue, false);
                }
            }
            catch (Exception error)
            {
                changingAutoStart = true;
                autoStart.Checked = !autoStart.Checked;
                changingAutoStart = false;
                MessageBox.Show(error.Message, "DubSync");
            }
        }

        private void Finish()
        {
            SetStatus("Finalizando…", false, "Encerrando o servidor.");
            if (!StopBackend())
            {
                MessageBox.Show("O servidor ainda está ativo. Feche o terminal que o iniciou.", "DubSync");
                return;
            }
            quitting = true;
            Close();
        }
    }
}

// Windows GUI entry point. All dependency/launch logic remains in bootstrap.py.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using System.Reflection;

[assembly: AssemblyTitle("统一启动器")]
[assembly: AssemblyDescription("统一启动器的一键环境准备与启动入口")]
[assembly: AssemblyProduct("统一刷课工具包")]

namespace ToolkitDesktop
{
    public sealed class LaunchCommand
    {
        public string FileName;
        public string Prefix;
        public LaunchCommand(string fileName, string prefix) { FileName = fileName; Prefix = prefix; }
    }

    public static class Entry
    {
        // Apply Windows command-line quoting, including trailing backslashes.
        public static string Quote(string value)
        {
            StringBuilder result = new StringBuilder("\"");
            int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') result.Append('\\', slashes * 2 + 1);
                else result.Append('\\', slashes);
                result.Append(c);
                slashes = 0;
            }
            result.Append('\\', slashes * 2);
            return result.Append('"').ToString();
        }

        public static LaunchCommand FindPython(string root, Func<LaunchCommand, bool> probe)
        {
            List<LaunchCommand> candidates = new List<LaunchCommand>();
            string local = Path.Combine(root, ".runtime", "venv", "Scripts", "python.exe");
            if (File.Exists(local)) candidates.Add(new LaunchCommand(local, ""));
            foreach (string version in new string[] { "3.13", "3.12", "3.11", "3" })
                candidates.Add(new LaunchCommand("py.exe", "-" + version + " "));
            candidates.Add(new LaunchCommand("python.exe", ""));
            candidates.Add(new LaunchCommand("python3.exe", ""));
            foreach (LaunchCommand command in candidates)
                if (probe(command)) return command;
            return null;
        }

        public static string BootstrapArguments(LaunchCommand command, string root, string[] args)
        {
            StringBuilder result = new StringBuilder(command.Prefix);
            result.Append(Quote(Path.Combine(root, "bootstrap.py")));
            if (args.Length == 0) result.Append(" --install");
            else foreach (string arg in args) result.Append(" ").Append(Quote(arg));
            return result.ToString();
        }

        private static bool Probe(LaunchCommand command)
        {
            try
            {
                ProcessStartInfo info = new ProcessStartInfo(command.FileName,
                    command.Prefix + "-c " + Quote("import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"));
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                using (Process process = Process.Start(info))
                {
                    if (!process.WaitForExit(10000)) { process.Kill(); return false; }
                    return process.ExitCode == 0;
                }
            }
            catch { return false; }
        }

        [STAThread]
        public static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            string root = AppDomain.CurrentDomain.BaseDirectory;
            if (!File.Exists(Path.Combine(root, "bootstrap.py")))
            {
                MessageBox.Show("未找到 bootstrap.py。请先解压完整运行包，并将统一启动器.exe 与 bootstrap.py 放在同一目录。",
                    "统一启动器", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            using (StartupWindow window = new StartupWindow(root, args))
            {
                Application.Run(window);
                return window.ExitCode;
            }
        }

        public sealed class StartupWindow : Form
        {
            private readonly string root;
            private readonly string[] args;
            private readonly TextBox output = new TextBox();
            private readonly Label status = new Label();
            private readonly ProgressBar progress = new ProgressBar();
            private Process current;
            private volatile bool running = true;
            private readonly object processGate = new object();
            public int ExitCode = 1;

            public StartupWindow(string root, string[] args)
            {
                this.root = root;
                this.args = args;
                Text = "统一启动器 · 启动准备";
                Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
                StartPosition = FormStartPosition.CenterScreen;
                MinimumSize = new Size(640, 360);
                Size = new Size(740, 460);
                Font = new Font("Microsoft YaHei UI", 9);
                status.Text = "正在检查运行环境，首次准备可能需要几分钟…";
                status.Dock = DockStyle.Top;
                status.Height = 46;
                status.Padding = new Padding(12);
                progress.Dock = DockStyle.Bottom;
                progress.Height = 8;
                progress.Style = ProgressBarStyle.Marquee;
                output.Dock = DockStyle.Fill;
                output.Multiline = true;
                output.ReadOnly = true;
                output.ScrollBars = ScrollBars.Both;
                output.WordWrap = false;
                output.Font = new Font("Consolas", 9);
                Controls.Add(output);
                Controls.Add(status);
                Controls.Add(progress);
                Shown += delegate { new Thread(Prepare) { IsBackground = true }.Start(); };
                FormClosing += ClosingWindow;
            }

            private void Append(string message)
            {
                if (String.IsNullOrEmpty(message) || IsDisposed) return;
                try { BeginInvoke((Action)delegate { output.AppendText(message + Environment.NewLine); }); }
                catch (InvalidOperationException) { }
            }

            private void Prepare()
            {
                try
                {
                    LaunchCommand command = FindPython(root, Probe);
                    if (command == null)
                        throw new InvalidOperationException("未找到 Python 3.11 或更高版本。请安装 Python 并加入 PATH，或安装官方 Python Launcher 后重试。");
                    Append("Python: " + command.FileName + " " + command.Prefix);
                    ProcessStartInfo info = new ProcessStartInfo(command.FileName, BootstrapArguments(command, root, args));
                    info.WorkingDirectory = root;
                    info.UseShellExecute = false;
                    info.CreateNoWindow = true;
                    info.RedirectStandardOutput = true;
                    info.RedirectStandardError = true;
                    info.StandardOutputEncoding = Encoding.UTF8;
                    info.StandardErrorEncoding = Encoding.UTF8;
                    info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                    info.EnvironmentVariables["PYTHONUNBUFFERED"] = "1";
                    using (Process process = new Process())
                    {
                        process.StartInfo = info;
                        process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { Append(e.Data); };
                        process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { Append(e.Data); };
                        lock (processGate)
                        {
                            if (!running) return;
                            process.Start();
                            current = process;
                        }
                        process.BeginOutputReadLine();
                        process.BeginErrorReadLine();
                        process.WaitForExit();
                        lock (processGate) { current = null; }
                        ExitCode = process.ExitCode;
                    }
                    if (ExitCode != 0) throw new InvalidOperationException("启动准备失败（返回码 " + ExitCode + "），请查看上方日志及安装目录 logs 文件夹。");
                    BeginInvoke((Action)delegate { running = false; Close(); });
                }
                catch (Exception error)
                {
                    Append(error.Message);
                    if (IsDisposed) return;
                    try { BeginInvoke((Action)delegate {
                        running = false;
                        progress.Style = ProgressBarStyle.Blocks;
                        status.Text = "启动未完成，请查看日志。处理后重新打开统一启动器.exe。";
                    }); } catch (InvalidOperationException) { }
                }
            }

            private void ClosingWindow(object sender, FormClosingEventArgs e)
            {
                if (!running) return;
                if (MessageBox.Show("正在准备运行环境，确定取消本次启动吗？", "统一启动器",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                { e.Cancel = true; return; }
                Process process;
                lock (processGate) { running = false; process = current; }
                if (process != null)
                {
                    try {
                        ProcessStartInfo info = new ProcessStartInfo("taskkill.exe", "/PID " + process.Id + " /T /F");
                        info.UseShellExecute = false;
                        info.CreateNoWindow = true;
                        using (Process kill = Process.Start(info)) kill.WaitForExit(5000);
                    } catch (InvalidOperationException) { }
                }
                running = false;
            }
        }
    }
}

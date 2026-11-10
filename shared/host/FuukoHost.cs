// 风子工作台共用窗口程序：用 WebView2 打开本机工作台页面，替代 Edge 应用模式。
//
//   FuukoHost.exe --url http://127.0.0.1:17800/ --id fuuko --title 风子工作台
//                 [--icon x.ico|x.png|x.jpg] [--profile <WebView2 数据目录>] [--width 1280 --height 860]
//   FuukoHost.exe --self-test --report r.json        # 不开窗口的自检
//   FuukoHost.exe --url ... --capture-dir <目录>      # 屏幕外打开，存 page.png / dom.json 后退出（验证用）
//
// 约定：
// - 只打开本机地址（127.0.0.1 / localhost 的 http）。顶层页面只能留在同一来源；
//   其他本机地址另开一个窗口，外部 http(s) 链接交给系统默认浏览器。
// - 每个 --id 只开一个窗口；重复打开时把已有窗口提到前台。
// - 窗口大小和位置记在 profile 目录的 window.json。
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace FuukoHost
{
    internal static class Program
    {
        internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 16000000 };
        internal static readonly string Home = AppDomain.CurrentDomain.BaseDirectory;
        internal static int ExitCode;

        [DllImport("shell32.dll", CharSet = CharSet.Unicode)] static extern int SetCurrentProcessExplicitAppUserModelID(string id);
        [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr handle);
        [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr handle, int command);
        [DllImport("user32.dll")] static extern bool IsIconic(IntPtr handle);
        [DllImport("user32.dll")] static extern uint GetDpiForSystem();

        internal static float SystemDpi()
        {
            try { return GetDpiForSystem(); } catch (EntryPointNotFoundException) { return 96f; }
        }

        [STAThread]
        static int Main(string[] arguments)
        {
            Options options = null;
            try
            {
                options = Options.Parse(arguments);
                if (options.SelfTest) { SelfTest.Run(options.Report); return 0; }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                SetCurrentProcessExplicitAppUserModelID("Fuuko.Workbench." + options.Id);
                bool created;
                using (var mutex = new Mutex(true, "Local\\FuukoHost." + options.Id, out created))
                {
                    if (!created && options.CaptureDirectory == null)
                    {
                        if (BringExistingToFront(options.Title)) return 0;
                        // 另一个窗口还在启动中：等它一下，避免同时开两个
                        if (mutex.WaitOne(8000)) created = true;
                        else return 0;
                    }
                    using (var form = new HostForm(options)) Application.Run(form);
                    if (created) mutex.ReleaseMutex();
                }
                return ExitCode;
            }
            catch (Exception error)
            {
                Log(error);
                if (options != null && !String.IsNullOrEmpty(options.Report))
                    WriteObject(options.Report, new { ok = false, error = error.Message });
                if (options == null || (!options.SelfTest && options.CaptureDirectory == null))
                    MessageBox.Show(error.Message, options != null ? options.Title : "风子工作台", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }

        static bool BringExistingToFront(string title)
        {
            int self = Process.GetCurrentProcess().Id;
            foreach (var process in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(Application.ExecutablePath)))
            {
                using (process)
                {
                    if (process.Id == self || process.MainWindowHandle == IntPtr.Zero) continue;
                    if (!String.Equals(process.MainWindowTitle, title, StringComparison.Ordinal)) continue;
                    if (IsIconic(process.MainWindowHandle)) ShowWindow(process.MainWindowHandle, 9);
                    SetForegroundWindow(process.MainWindowHandle);
                    return true;
                }
            }
            return false;
        }

        // 只接受本机 http 地址
        internal static bool IsLocal(Uri value)
        {
            return value != null && value.Scheme == Uri.UriSchemeHttp && value.UserInfo == ""
                && (value.Host == "127.0.0.1" || String.Equals(value.Host, "localhost", StringComparison.OrdinalIgnoreCase))
                && value.Port > 0;
        }

        internal static bool SameOrigin(Uri a, Uri b)
        {
            return a != null && b != null && a.Scheme == b.Scheme && String.Equals(a.Host, b.Host, StringComparison.OrdinalIgnoreCase) && a.Port == b.Port;
        }

        internal static string SafeId(string value)
        {
            var result = new StringBuilder();
            foreach (char c in value ?? "") if (Char.IsLetterOrDigit(c) || c == '-' || c == '_' || c == '.') result.Append(c);
            return result.Length == 0 ? "app" : result.ToString();
        }

        internal static Icon LoadIcon(string path)
        {
            try { return LoadIconUnchecked(path); }
            catch (Exception error) { Log(error); return null; }  // 图标坏了就用默认图标，不影响开窗
        }

        static Icon LoadIconUnchecked(string path)
        {
            if (String.IsNullOrEmpty(path) || !File.Exists(path)) return null;
            string ext = Path.GetExtension(path).ToLowerInvariant();
            if (ext == ".ico") return new Icon(path);
            if (ext == ".exe" || ext == ".dll") return Icon.ExtractAssociatedIcon(path);
            if (ext != ".png" && ext != ".jpg" && ext != ".jpeg" && ext != ".bmp") return null;
            using (var source = Image.FromFile(path))
            using (var square = new Bitmap(64, 64))
            using (var g = Graphics.FromImage(square))
            {
                // 居中裁成正方形
                int side = Math.Min(source.Width, source.Height);
                var crop = new Rectangle((source.Width - side) / 2, (source.Height - side) / 2, side, side);
                g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                g.DrawImage(source, new Rectangle(0, 0, 64, 64), crop, GraphicsUnit.Pixel);
                return Icon.FromHandle(square.GetHicon());
            }
        }

        internal static Dictionary<string, object> ReadObject(string path)
        {
            try { return Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path, Encoding.UTF8)) ?? new Dictionary<string, object>(); }
            catch (Exception) { return new Dictionary<string, object>(); }
        }

        internal static int IntValue(Dictionary<string, object> value, string key, int fallback)
        {
            object item; int result;
            return value.TryGetValue(key, out item) && item != null && Int32.TryParse(Convert.ToString(item), out result) ? result : fallback;
        }

        internal static void WriteObject(string path, object value)
        {
            if (String.IsNullOrEmpty(path)) return;
            path = Path.GetFullPath(path);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, Json.Serialize(value), new UTF8Encoding(false));
        }

        internal static void Log(Exception error)
        {
            try { File.AppendAllText(Path.Combine(Home, "host.log"), DateTime.Now.ToString("o") + " " + error + Environment.NewLine, Encoding.UTF8); }
            catch (Exception) { }
        }
    }

    internal sealed class Options
    {
        internal Uri Url;
        internal string Id = "", Title = "风子工作台", IconPath, Profile, Report, CaptureDirectory, Script;
        internal int Width = 1280, Height = 860, Settle = 1500;
        internal bool SelfTest;

        internal static Options Parse(string[] args)
        {
            var o = new Options();
            for (int i = 0; i < args.Length; i++)
            {
                string name = args[i];
                if (name == "--self-test") { o.SelfTest = true; continue; }
                if (i + 1 >= args.Length) throw new ArgumentException("缺少参数值：" + name);
                string value = args[++i];
                switch (name)
                {
                    case "--url": o.Url = new Uri(value); break;
                    case "--id": o.Id = Program.SafeId(value); break;
                    case "--title": o.Title = value; break;
                    case "--icon": o.IconPath = value; break;
                    case "--profile": o.Profile = Path.GetFullPath(value); break;
                    case "--width": o.Width = Math.Max(640, Math.Min(4000, Int32.Parse(value))); break;
                    case "--height": o.Height = Math.Max(480, Math.Min(3000, Int32.Parse(value))); break;
                    case "--report": o.Report = value; break;
                    case "--capture-dir": o.CaptureDirectory = Path.GetFullPath(value); break;
                    case "--script": o.Script = Path.GetFullPath(value); break;
                    case "--settle-ms": o.Settle = Math.Max(0, Math.Min(30000, Int32.Parse(value))); break;
                    default: throw new ArgumentException("未知参数：" + name);
                }
            }
            if (o.SelfTest) return o;
            if (o.Url == null) throw new ArgumentException("需要 --url");
            if (!Program.IsLocal(o.Url)) throw new ArgumentException("只能打开本机地址：" + o.Url);
            if (o.Id == "") o.Id = "port" + o.Url.Port;
            if (o.Profile == null)
            {
                string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                o.Profile = Path.Combine(local, "FuukoHost", o.Id);
            }
            return o;
        }
    }

    internal sealed class HostForm : Form
    {
        readonly Options options;
        readonly Label status = new Label();
        readonly Button retry = new Button();
        readonly CancellationTokenSource cancellation = new CancellationTokenSource();
        WebView2 web;
        bool initializing, captured;
        string WindowFile { get { return Path.Combine(options.Profile, "window.json"); } }

        internal HostForm(Options o)
        {
            options = o;
            Text = o.Title;
            MinimumSize = new Size(720, 520);
            BackColor = Color.FromArgb(246, 245, 241);
            Icon = Program.LoadIcon(o.IconPath) ?? Icon;
            LoadBounds();
            status.Dock = DockStyle.Fill; status.TextAlign = ContentAlignment.MiddleCenter;
            status.Font = new Font("Microsoft YaHei UI", 11); status.ForeColor = Color.FromArgb(48, 59, 69);
            status.Text = "正在打开 " + o.Title + "…"; Controls.Add(status);
            retry.Text = "重试"; retry.Dock = DockStyle.Bottom; retry.Height = 40; retry.Visible = false;
            retry.Click += async delegate { await Initialize(); }; Controls.Add(retry);
            if (o.CaptureDirectory != null)
            {
                ShowInTaskbar = false; StartPosition = FormStartPosition.Manual; Location = new Point(-20000, -20000);
                var timeout = new System.Windows.Forms.Timer { Interval = 60000 };
                timeout.Tick += delegate { timeout.Stop(); Fail(new TimeoutException("页面在 60 秒内没有加载完成。")); };
                timeout.Start();
            }
            Shown += async delegate { await Initialize(); };
            FormClosing += delegate { SaveBounds(); };
            FormClosed += delegate { cancellation.Cancel(); };
        }

        void LoadBounds()
        {
            // 默认尺寸按 96 DPI 给出，换算到当前屏幕缩放；已保存的位置本来就是物理像素
            float scale = Math.Max(1f, Program.SystemDpi() / 96f);
            Width = (int)(options.Width * scale); Height = (int)(options.Height * scale);
            var area = Screen.FromPoint(Cursor.Position).WorkingArea;
            Width = Math.Min(Width, area.Width); Height = Math.Min(Height, area.Height);
            StartPosition = FormStartPosition.CenterScreen;
            if (options.CaptureDirectory != null) return;
            var saved = Program.ReadObject(WindowFile);
            int w = Program.IntValue(saved, "w", 0), h = Program.IntValue(saved, "h", 0);
            int x = Program.IntValue(saved, "x", Int32.MinValue), y = Program.IntValue(saved, "y", Int32.MinValue);
            if (w < 640 || h < 480) return;
            var rect = new Rectangle(x, y, w, h);
            foreach (var screen in Screen.AllScreens)
            {
                // 只在窗口标题栏仍落在某块屏幕里时恢复位置（拔掉副屏后不会跑到屏外）
                if (screen.WorkingArea.Contains(new Point(x + 40, y + 10)))
                {
                    StartPosition = FormStartPosition.Manual; Bounds = rect;
                    if (Program.IntValue(saved, "max", 0) == 1) WindowState = FormWindowState.Maximized;
                    return;
                }
            }
            Width = w; Height = h;
        }

        void SaveBounds()
        {
            if (options.CaptureDirectory != null) return;
            try
            {
                var r = WindowState == FormWindowState.Normal ? Bounds : RestoreBounds;
                if (WindowState == FormWindowState.Minimized) return;
                Program.WriteObject(WindowFile, new { x = r.X, y = r.Y, w = r.Width, h = r.Height, max = WindowState == FormWindowState.Maximized ? 1 : 0 });
            }
            catch (Exception error) { Program.Log(error); }
        }

        async Task Initialize()
        {
            if (initializing) return;
            initializing = true; retry.Visible = false;
            try
            {
                if (web != null) { Controls.Remove(web); web.Dispose(); }
                web = new WebView2 { Dock = DockStyle.Fill, DefaultBackgroundColor = BackColor };
                Controls.Add(web); web.BringToFront();
                Directory.CreateDirectory(options.Profile);
                var environment = await CoreWebView2Environment.CreateAsync(null, options.Profile, null);
                if (IsDisposed) return;
                await web.EnsureCoreWebView2Async(environment);
                var core = web.CoreWebView2;
                core.Settings.IsStatusBarEnabled = false;
                core.Settings.AreHostObjectsAllowed = false;
                core.Settings.IsGeneralAutofillEnabled = false;
                core.Settings.IsPasswordAutosaveEnabled = false;
                core.NavigationStarting += OnNavigationStarting;
                core.NewWindowRequested += OnNewWindowRequested;
                core.NavigationCompleted += async delegate(object s, CoreWebView2NavigationCompletedEventArgs e)
                {
                    if (!e.IsSuccess && e.WebErrorStatus != CoreWebView2WebErrorStatus.OperationCanceled)
                    {
                        if (options.CaptureDirectory != null) { Fail(new InvalidOperationException("页面加载失败：" + e.WebErrorStatus)); return; }
                        status.Text = "页面打不开（" + e.WebErrorStatus + "）。服务可能已经退出，点重试再打开一次。";
                        status.BringToFront(); retry.Visible = true; retry.BringToFront();
                        return;
                    }
                    if (options.CaptureDirectory != null && !captured && e.IsSuccess) { captured = true; await CapturePage(); }
                };
                core.Navigate(options.Url.AbsoluteUri);
            }
            catch (OperationCanceledException) { }
            catch (WebView2RuntimeNotFoundException)
            {
                Fail(new InvalidOperationException("本机没有 WebView2 运行时。请安装 Microsoft Edge WebView2 Runtime 后再打开。"));
            }
            catch (Exception error) { Fail(error); }
            finally { initializing = false; }
        }

        void OnNavigationStarting(object sender, CoreWebView2NavigationStartingEventArgs e)
        {
            Uri target;
            if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out target)) { e.Cancel = true; return; }
            if (Program.SameOrigin(target, options.Url) || target.Scheme == "about" || target.Scheme == "data") return;
            e.Cancel = true;
            Hand(target);
        }

        void OnNewWindowRequested(object sender, CoreWebView2NewWindowRequestedEventArgs e)
        {
            e.Handled = true;
            Uri target;
            if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out target)) return;
            if (Program.SameOrigin(target, options.Url)) { web.CoreWebView2.Navigate(target.AbsoluteUri); return; }
            Hand(target);
        }

        // 同源以外的地址：本机工作台另开窗口，外部网页交给默认浏览器
        void Hand(Uri target)
        {
            try
            {
                if (Program.IsLocal(target))
                {
                    var args = "--url " + Quote(target.AbsoluteUri) + " --id port" + target.Port + " --title " + Quote(options.Title + " · " + target.Port)
                        + (String.IsNullOrEmpty(options.IconPath) ? "" : " --icon " + Quote(options.IconPath));
                    Process.Start(new ProcessStartInfo(Application.ExecutablePath, args) { UseShellExecute = false });
                }
                else if (target.Scheme == Uri.UriSchemeHttp || target.Scheme == Uri.UriSchemeHttps)
                {
                    Process.Start(new ProcessStartInfo(target.AbsoluteUri) { UseShellExecute = true });
                }
            }
            catch (Exception error) { Program.Log(error); }
        }

        internal static string Quote(string value)
        {
            var result = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value)
            {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') { result.Append('\\', slashes * 2 + 1); result.Append('"'); slashes = 0; continue; }
                result.Append('\\', slashes); slashes = 0; result.Append(c);
            }
            result.Append('\\', slashes * 2); result.Append('"'); return result.ToString();
        }

        async Task CapturePage()
        {
            try
            {
                await Task.Delay(options.Settle, cancellation.Token);
                Directory.CreateDirectory(options.CaptureDirectory);
                if (options.Script != null)
                {
                    string result = await web.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(options.Script, Encoding.UTF8));
                    File.WriteAllText(Path.Combine(options.CaptureDirectory, "script-result.json"), result, new UTF8Encoding(false));
                    await Task.Delay(600, cancellation.Token);
                }
                string dom = await web.CoreWebView2.ExecuteScriptAsync(
                    "(() => ({title:document.title,url:location.href,ready:document.readyState,text:document.body.innerText," +
                    "viewport:{width:innerWidth,height:innerHeight},overflowX:document.documentElement.scrollWidth>innerWidth}))()");
                File.WriteAllText(Path.Combine(options.CaptureDirectory, "dom.json"), dom, new UTF8Encoding(false));
                using (var output = File.Create(Path.Combine(options.CaptureDirectory, "page.png")))
                    await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, output);
                Program.WriteObject(Path.Combine(options.CaptureDirectory, "host.json"), new
                {
                    ok = true, title = Text, id = options.Id, url = web.Source.AbsoluteUri,
                    runtime = web.CoreWebView2.Environment.BrowserVersionString, profile = web.CoreWebView2.Environment.UserDataFolder,
                    icon_loaded = Icon != null && !String.IsNullOrEmpty(options.IconPath)
                });
                Close();
            }
            catch (OperationCanceledException) { }
            catch (Exception error) { Fail(error); }
        }

        void Fail(Exception error)
        {
            Program.Log(error); Program.ExitCode = 1;
            if (IsDisposed) return;
            if (options.CaptureDirectory != null)
            {
                Program.WriteObject(Path.Combine(options.CaptureDirectory, "error.json"), new { error = error.Message });
                Close(); return;
            }
            status.Text = error.Message; status.BringToFront(); retry.Visible = true; retry.BringToFront();
        }
    }

    internal static class SelfTest
    {
        internal static void Run(string report)
        {
            var checks = new List<string>();
            Action<bool, string> check = (ok, name) => { if (!ok) throw new Exception("自检失败：" + name); checks.Add(name); };
            check(Program.IsLocal(new Uri("http://127.0.0.1:17800/")), "127.0.0.1 accepted");
            check(Program.IsLocal(new Uri("http://localhost:3000/")), "localhost accepted");
            check(!Program.IsLocal(new Uri("https://127.0.0.1:17800/")), "https loopback rejected");
            check(!Program.IsLocal(new Uri("http://example.org/")), "external host rejected");
            check(!Program.IsLocal(new Uri("http://user@127.0.0.1:1/")), "userinfo rejected");
            check(Program.SameOrigin(new Uri("http://127.0.0.1:1/a"), new Uri("http://127.0.0.1:1/b?x")), "same origin");
            check(!Program.SameOrigin(new Uri("http://127.0.0.1:1/"), new Uri("http://127.0.0.1:2/")), "different port is other origin");
            check(Program.SafeId("a/b\\c..:d") == "abc..d" && Program.SafeId("") == "app", "id sanitized");
            check(HostForm.Quote("a b") == "\"a b\"" && HostForm.Quote("a\\") == "\"a\\\\\"" && HostForm.Quote("a\"b") == "\"a\\\"b\"", "argument quoting");
            bool rejected = false;
            try { Options.Parse(new[] { "--url", "http://example.org/" }); } catch (ArgumentException) { rejected = true; }
            check(rejected, "non-local --url rejected");
            var parsed = Options.Parse(new[] { "--url", "http://127.0.0.1:5000/" });
            check(parsed.Id == "port5000" && parsed.Profile.EndsWith(Path.Combine("FuukoHost", "port5000")), "default id and profile");
            check(Program.LoadIcon(Path.Combine(Program.Home, "no-such.svg")) == null, "missing icon ignored");
            Program.WriteObject(report, new { ok = true, checks = checks, webview2_runtime = CoreWebView2Environment.GetAvailableBrowserVersionString() });
        }
    }
}

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("eMaktab")]
[assembly: AssemblyProduct("eMaktab")]
[assembly: AssemblyVersion("1.2.6.0")]
[assembly: AssemblyFileVersion("1.2.6.0")]

namespace EMaktabWebsite {
    sealed class UserError : Exception {
        public readonly string Code;
        public UserError(string code, string message) : base(message) { Code=code; }
    }
    sealed class ServerInfo { public string Base, Instance, Version, Token; public int Pid; }
    static class Native {
        [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr window);
        [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr window, int command);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError=true)] public static extern bool SetInformationJobObject(IntPtr job, int kind, IntPtr information, uint length);
        [DllImport("kernel32.dll", SetLastError=true)] public static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr handle);
        [StructLayout(LayoutKind.Sequential)] public struct BasicLimits { public long ProcessTime, JobTime; public uint Flags; public UIntPtr MinWorking, MaxWorking; public uint ActiveProcesses; public UIntPtr Affinity; public uint Priority, Scheduling; }
        [StructLayout(LayoutKind.Sequential)] public struct Counters { public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes; }
        [StructLayout(LayoutKind.Sequential)] public struct Limits { public BasicLimits Basic; public Counters IO; public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory; }
    }
    sealed class ProcessGroup : IDisposable {
        IntPtr handle;
        public ProcessGroup() {
            handle=Native.CreateJobObject(IntPtr.Zero,null);
            var limits=new Native.Limits(); limits.Basic.Flags=0x2000;
            int size=Marshal.SizeOf(limits); IntPtr memory=Marshal.AllocHGlobal(size);
            try { Marshal.StructureToPtr(limits,memory,false); if(handle==IntPtr.Zero || !Native.SetInformationJobObject(handle,9,memory,(uint)size)) throw new UserError("START_GROUP", "Dasturni xavfsiz ishga tushirib bo'lmadi. Qayta ochib ko'ring."); }
            finally { Marshal.FreeHGlobal(memory); }
        }
        public void Add(Process process) {
            if(!Native.AssignProcessToJobObject(handle,process.Handle)) {
                try { process.Kill(); } catch { }
                throw new UserError("START_PROCESS", "Dasturni ishga tushirib bo'lmadi. Qayta urinib ko'ring.");
            }
        }
        public void Dispose() { if(handle!=IntPtr.Zero) { Native.CloseHandle(handle); handle=IntPtr.Zero; } }
    }
    sealed class LaunchForm : Form {
        readonly Label detail=new Label(); readonly ProgressBar progress=new ProgressBar();
        readonly CancellationTokenSource stop=new CancellationTokenSource();
        readonly Runner runner; bool finished;
        public LaunchForm(string report,bool portable) {
            runner=new Runner(report, stop.Token, UpdateStatus, BrowserReady, portable);
            Text="eMaktab"; ClientSize=new Size(490,190); FormBorderStyle=FormBorderStyle.FixedDialog;
            MaximizeBox=false; StartPosition=FormStartPosition.CenterScreen; BackColor=Color.FromArgb(244,245,239);
            try { Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
            var title=new Label {Text="eMaktab ochilmoqda",Font=new Font("Segoe UI",19,FontStyle.Bold),ForeColor=Color.FromArgb(16,44,37),AutoSize=true,Location=new Point(25,23)};
            detail.Text="Bir oz kuting..."; detail.Font=new Font("Segoe UI",11); detail.Location=new Point(27,78); detail.Size=new Size(435,45);
            progress.Location=new Point(28,140); progress.Size=new Size(432,7); progress.Style=ProgressBarStyle.Marquee;
            Controls.Add(title); Controls.Add(detail); Controls.Add(progress);
            Shown+=delegate {
                Task.Factory.StartNew(runner.Execute).ContinueWith(delegate(Task task) {
                    BeginInvoke((Action)delegate {
                        finished=true;
                        if(task.IsFaulted) {
                            var error=task.Exception.GetBaseException(); var friendly=error as UserError;
                            string code=friendly==null ? "START_UNKNOWN" : friendly.Code;
                            string message=friendly==null ? "eMaktab ochilmadi. Qayta urinib ko'ring. Muammo takrorlansa, dastur egasiga quyidagi kodni yuboring." : friendly.Message;
                            if(report==null && code!="CANCELLED") { Show(); ErrorDialog.ShowError(this,message,code); }
                            else { Runner.WriteFailure(report,code,error.GetType().Name); }
                            Environment.ExitCode=code=="CANCELLED" ? 0 : 1;
                        }
                        Close();
                    });
                });
            };
            FormClosing+=delegate(object sender,FormClosingEventArgs args) { if(!finished) { args.Cancel=true; stop.Cancel(); detail.Text="Yopilmoqda..."; } };
        }
        void UpdateStatus(string text) { if(!IsDisposed) BeginInvoke((Action)delegate { detail.Text=text; }); }
        void BrowserReady() { if(!IsDisposed) BeginInvoke((Action)delegate { Hide(); }); }
        public void BringForward() {
            if(IsDisposed) return;
            BeginInvoke((Action)delegate { if(!runner.FocusBrowser()) { Show(); Activate(); } });
        }
    }
    static class ErrorDialog {
        public static void ShowError(IWin32Window owner,string message,string code) {
            using(var dialog=new Form {Text="eMaktab",ClientSize=new Size(490,210),FormBorderStyle=FormBorderStyle.FixedDialog,StartPosition=FormStartPosition.CenterScreen,MaximizeBox=false,MinimizeBox=false}) {
                var text=new Label {Text=message+"\n\nYordam kodi: "+code,Location=new Point(24,22),Size=new Size(440,125),Font=new Font("Segoe UI",11)};
                var copy=new Button {Text="Kodni nusxalash",Location=new Point(175,158),Size=new Size(140,32)};
                copy.Click+=delegate { try { Clipboard.SetText("eMaktab 1.2.6 | "+code); copy.Text="Nusxalandi"; } catch {} };
                var close=new Button {Text="Yopish",Location=new Point(328,158),Size=new Size(132,32),DialogResult=DialogResult.OK};
                dialog.Controls.Add(text);dialog.Controls.Add(copy);dialog.Controls.Add(close);dialog.AcceptButton=close;dialog.CancelButton=close;dialog.ShowDialog(owner);
            }
        }
    }
    sealed class Runner {
        readonly string report,data,cache,runtime; readonly CancellationToken stop;
        readonly Action<string> status; readonly Action ready;
        Process browser,backend; ServerInfo owned; ProcessGroup group;
        readonly bool portable; Portable store; bool prepareFailed;
        static readonly JavaScriptSerializer Json=new JavaScriptSerializer();
        public Runner(string testReport,CancellationToken cancellation,Action<string> update,Action opened,bool keepAccountsInExe) {
            report=testReport;stop=cancellation;status=update;ready=opened;portable=keepAccountsInExe;
            string local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string testing=report==null ? null : Path.GetDirectoryName(Path.GetFullPath(report));
            data=testing==null ? Path.Combine(local,"eMaktabManager") : Path.Combine(testing,"data");
            cache=Path.Combine(testing==null ? Path.Combine(local,"eMaktabWebsiteRuntime") : Path.Combine(testing,"runtime"),"native-"+Payload.Hash.Substring(0,16));
            runtime=Path.Combine(data,"desktop.json");
        }
        static string Hash(string file) { using(var stream=File.OpenRead(file)) using(var digest=SHA256.Create()) return BitConverter.ToString(digest.ComputeHash(stream)).Replace("-","").ToLowerInvariant(); }
        void CheckCancel() { if(stop.IsCancellationRequested) throw new UserError("CANCELLED","Dastur yopildi."); }
        void Install() {
            status("Kerakli fayllar tayyorlanmoqda...");CheckCancel();Directory.CreateDirectory(cache);
            string manager=Path.Combine(cache,"manager.exe"), sqlite=Path.Combine(cache,"sqlite3.exe"), saver=Path.Combine(cache,"saver.exe");
            if(File.Exists(manager) && File.Exists(sqlite) && File.Exists(saver) && Hash(manager)==Payload.ManagerHash && Hash(sqlite)==Payload.SqliteHash && Hash(saver)==Payload.SaverHash) return;
            using(var source=Assembly.GetExecutingAssembly().GetManifestResourceStream("Payload")) {
                if(source==null) throw new UserError("FILE_INCOMPLETE","Fayl to'liq yuklanmagan. Uni qayta yuklab oling.");
                using(var zip=new ZipArchive(source,ZipArchiveMode.Read)) {
                    foreach(var pair in new [] {new [] {"manager.exe",Payload.ManagerHash},new [] {"sqlite3.exe",Payload.SqliteHash},new [] {"saver.exe",Payload.SaverHash}}) {
                        CheckCancel();var entry=zip.GetEntry(pair[0]); if(entry==null) throw new UserError("FILE_INCOMPLETE","Faylni qayta yuklab oling.");
                        string target=Path.Combine(cache,pair[0]), temp=target+".new";
                        using(var input=entry.Open()) using(var output=File.Create(temp)) input.CopyTo(output);
                        if(Hash(temp)!=pair[1]) {File.Delete(temp);throw new UserError("FILE_DAMAGED","Fayl buzilgan. Uni qayta yuklab oling.");}
                        if(File.Exists(target))File.Delete(target);File.Move(temp,target);
                    }
                }
            }
        }
        static Dictionary<string,object> Request(string url,string method,string token,string origin) {
            var request=(HttpWebRequest)WebRequest.Create(url);request.Proxy=null;request.Timeout=2500;request.ReadWriteTimeout=2500;request.Method=method;
            if(token!=null)request.Headers["X-Desktop-Token"]=token;
            if(method=="POST") {request.Headers["Origin"]=origin;request.Headers["X-Manager"]="1";request.ContentType="application/json";byte[] bytes=Encoding.UTF8.GetBytes("{}");request.ContentLength=bytes.Length;using(var output=request.GetRequestStream())output.Write(bytes,0,bytes.Length);}
            using(var response=request.GetResponse()) using(var input=new StreamReader(response.GetResponseStream()))return Json.Deserialize<Dictionary<string,object>>(input.ReadToEnd());
        }
        ServerInfo Current() {
            try {
                var item=Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(runtime));int port=Convert.ToInt32(item["port"]);
                if(port<1 || port>65535)return null;
                string url="http://127.0.0.1:"+port, token=(string)item["token"], instance=(string)item["instance"];
                var health=Request(url+"/api/desktop/health","GET",token,null);
                if((string)health["product"]!="eMaktab Manager" || (string)health["instance"]!=instance)return null;
                return new ServerInfo {Base=url,Token=token,Instance=instance,Version=(string)health["version"],Pid=Convert.ToInt32(item["pid"])};
            } catch {return null;}
        }
        void Quit(ServerInfo server) {
            Request(server.Base+"/api/desktop/quit","POST",null,server.Base);
            var watch=Stopwatch.StartNew();
            while(watch.Elapsed.TotalSeconds<40) {var live=Current();if(live==null || live.Instance!=server.Instance)return;Thread.Sleep(250);}
            throw new UserError("OLD_WINDOW","Oldingi eMaktab oynasini yoping va qayta urinib ko'ring.");
        }
        Process Start(string file,string arguments) {return Start(file,arguments,true);}
        Process Start(string file,string arguments,bool redirect) {
            // A windowed launcher has no valid std handles; children would inherit them and
            // Playwright's node spawn fails with WinError 6. Give every child real, drained pipes.
            var info=new ProcessStartInfo(file,arguments) {UseShellExecute=false,CreateNoWindow=true,WorkingDirectory=cache,RedirectStandardInput=redirect,RedirectStandardOutput=redirect,RedirectStandardError=redirect};
            info.EnvironmentVariables["EMAKTAB_DATA_DIR"]=data;
            var process=Process.Start(info);
            if(redirect) {
                process.OutputDataReceived+=delegate {};process.ErrorDataReceived+=delegate {};
                process.BeginOutputReadLine();process.BeginErrorReadLine();
            }
            group.Add(process);return process;
        }
        void DisableSchedule() {
            string database=Path.Combine(data,"manager.sqlite3");if(!File.Exists(database))return;
            var info=new ProcessStartInfo(Path.Combine(cache,"sqlite3.exe"),"-batch \""+database+"\" \".timeout 10000\" \"UPDATE settings SET enabled=0 WHERE id=1;\"") {UseShellExecute=false,CreateNoWindow=true,RedirectStandardError=true,RedirectStandardOutput=true};
            using(var process=Process.Start(info)) {group.Add(process);if(!process.WaitForExit(15000) || process.ExitCode!=0)throw new UserError("DATA_BUSY","Ma'lumotlar hozir band. Oldingi eMaktab oynasini yopib, qayta oching.");}
        }
        string Stylesheet() {
            var candidates=new List<string>();
            try {candidates.Add(Path.Combine(Path.GetDirectoryName(Process.GetProcessById(owned.Pid).MainModule.FileName),"static","style.css"));}catch{}
            foreach(int pid in new [] {backend.Id,owned.Pid}) {
                foreach(string folder in Directory.GetDirectories(Path.GetTempPath(),"onefile_"+pid+"_*"))candidates.Add(Path.Combine(folder,"static","style.css"));
            }
            foreach(string file in candidates) if(File.Exists(file))return file;
            throw new UserError("WEBSITE_FILES","Sayt fayllari topilmadi. Dasturni qayta ochib ko'ring.");
        }
        string BrowserPath() {
            string pf=Environment.GetEnvironmentVariable("ProgramFiles")??"", pf86=Environment.GetEnvironmentVariable("ProgramFiles(x86)")??"", local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            foreach(string path in new [] {Path.Combine(pf86,"Microsoft\\Edge\\Application\\msedge.exe"),Path.Combine(pf,"Microsoft\\Edge\\Application\\msedge.exe"),Path.Combine(pf,"Google\\Chrome\\Application\\chrome.exe"),Path.Combine(pf86,"Google\\Chrome\\Application\\chrome.exe"),Path.Combine(local,"Google\\Chrome\\Application\\chrome.exe")}) if(File.Exists(path))return path;
            string root=Path.Combine(data,"browser-cache");if(Directory.Exists(root))foreach(string file in Directory.EnumerateFiles(root,"chrome.exe",SearchOption.AllDirectories))return file;
            throw new UserError("BROWSER_FILES","Microsoft Edge yoki Google Chrome topilmadi. Birini o'rnating va dasturni qayta oching.");
        }
        public bool FocusBrowser() {
            try {if(browser==null || browser.HasExited)return false;browser.Refresh();IntPtr window=browser.MainWindowHandle;if(window==IntPtr.Zero)return false;Native.ShowWindowAsync(window,9);return Native.SetForegroundWindow(window);}catch{return false;}
        }
        public void Execute() {
            bool opened=false, closed=false;int outer=0,inner=0,chrome=0;string instance=null;
            try {
                group=new ProcessGroup();Install();CheckCancel();
                status("Oldingi oyna tekshirilmoqda...");var old=Current();if(old!=null)Quit(old);
                Directory.CreateDirectory(data);
                if(portable) {
                    store=new Portable(Application.ExecutablePath,data,Path.Combine(cache,"sqlite3.exe"));
                    try {store.Prepare(status);} catch(Exception error) {prepareFailed=true;store.Log("prepare failed: "+error.GetType().Name+" "+error.Message);}
                }
                DisableSchedule();CheckCancel();
                status("Mahalliy sayt ochilmoqda...");backend=Start(Path.Combine(cache,"manager.exe"),"--no-browser");outer=backend.Id;
                var watch=Stopwatch.StartNew();
                while(watch.Elapsed.TotalSeconds<100) {CheckCancel();owned=Current();if(owned!=null)break;if(backend.HasExited)throw new UserError("LOCAL_START","Mahalliy sayt ochilmadi. Dasturni qayta ochib ko'ring.");Thread.Sleep(250);}
                if(owned==null)throw new UserError("LOCAL_TIMEOUT","Dastur ochilishi cho'zildi. Qayta urinib ko'ring.");
                if(owned.Version!="1.0.1")throw new UserError("OLD_VERSION","Oldingi eMaktab oynasini yoping va yana oching.");
                inner=owned.Pid;instance=owned.Instance;
                File.AppendAllText(Stylesheet(),"\n.lower>article:first-child{display:none!important}.lower{grid-template-columns:1fr!important}\n",new UTF8Encoding(false));
                CheckCancel();status("Brauzer ochilmoqda...");
                string profile=Path.Combine(data,"website-native-ui-profile");
                string browserFile=BrowserPath();DateTime launched=DateTime.Now.AddSeconds(-3);
                browser=Start(browserFile,"--app="+owned.Base+"/ --user-data-dir=\""+profile+"\" --disable-background-mode --no-first-run --no-default-browser-check",false);chrome=browser.Id;
                // First start with a new profile can be slow, and Edge/Chrome may hand the window to a second process.
                watch.Restart();while(watch.Elapsed.TotalSeconds<90) {
                    CheckCancel();
                    if(browser.HasExited) {
                        Process alt=null;
                        foreach(var candidate in Process.GetProcessesByName(Path.GetFileNameWithoutExtension(browserFile))) {
                            try {if(alt==null && candidate.StartTime>=launched && candidate.MainWindowHandle!=IntPtr.Zero)alt=candidate;} catch {}
                        }
                        if(alt==null) {
                            if(watch.Elapsed.TotalSeconds>25)throw new UserError("BROWSER_START","Brauzer oynasi ochilmadi. Dasturni qayta ochib ko'ring.");
                            Thread.Sleep(250);continue;
                        }
                        browser=alt;chrome=alt.Id;try {group.Add(alt);} catch {}
                        opened=true;break;
                    }
                    browser.Refresh();if(browser.MainWindowHandle!=IntPtr.Zero){opened=true;break;}
                    Thread.Sleep(250);
                }
                if(!opened)throw new UserError("BROWSER_WINDOW","Brauzer oynasi ko'rinmadi. Qayta urinib ko'ring.");
                FocusBrowser();ready();
                if(report!=null) {
                    Thread.Sleep(2000);string style=owned.Base+"/assets/style.css";
                    var request=(HttpWebRequest)WebRequest.Create(style);request.Proxy=null;request.Timeout=5000;
                    using(var response=request.GetResponse())using(var reader=new StreamReader(response.GetResponseStream()))if(!reader.ReadToEnd().Contains(".lower>article:first-child{display:none!important}"))throw new Exception("Schedule UI was not hidden");
                    if(!browser.CloseMainWindow())throw new Exception("Actual browser window did not accept close");
                }
                int failures=0;
                while(!browser.HasExited) {CheckCancel();Thread.Sleep(500);if(Current()==null) {failures++;if(backend.HasExited){browser.CloseMainWindow();break;}if(failures>=3)throw new UserError("LOCAL_CONNECTION","Sayt bilan aloqa uzildi. Dasturni qayta ochib ko'ring.");}else failures=0;}
                closed=true;
            } finally {
                if(owned!=null) {var live=Current();if(live!=null && live.Instance==owned.Instance)try {Quit(live);}catch{}}
                if(browser!=null)try {if(!browser.HasExited)browser.CloseMainWindow();}catch{}
                if(group!=null)group.Dispose();
                if(backend!=null)try {backend.WaitForExit(5000);}catch{}
                if(browser!=null)try {browser.WaitForExit(5000);}catch{}
                if(portable && store!=null && !prepareFailed && backend!=null) {
                    try {store.Save(Path.Combine(cache,"saver.exe"));} catch(Exception error) {store.Log("save failed: "+error.GetType().Name+" "+error.Message);}
                }
            }
            if(report!=null) {
                if(Current()!=null)throw new Exception("Backend still running after browser close");
                File.WriteAllText(report,Json.Serialize(new {ok=true,browserOpened=opened,browserClosed=closed,serverStopped=true,backendVersion="1.0.1",backendPid=outer,serverPid=inner,browserPid=chrome,instance=instance,dataDirectory=data,cacheDirectory=cache}),new UTF8Encoding(false));
            }
        }
        public static void WriteFailure(string report,string code,string type) {try {File.WriteAllText(report,Json.Serialize(new {ok=false,code=code,errorType=type}));}catch{}}
    }
    static class Program {
        [STAThread] static void Main(string[] args) {
            Application.EnableVisualStyles();Application.SetCompatibleTextRenderingDefault(false);
            string report=null;bool portable=true;
            if(args.Length==2 && (args[0]=="--test-lifecycle" || args[0]=="--test-portable")) {report=Path.GetFullPath(args[1]);Directory.CreateDirectory(Path.GetDirectoryName(report));portable=args[0]=="--test-portable";}
            using(var mutex=new Mutex(false,"Local\\eMaktabWebsiteNative")) {
                bool owned=false;try {try {owned=mutex.WaitOne(0);}catch(AbandonedMutexException){owned=true;}
                    if(!owned) {try {using(var signal=EventWaitHandle.OpenExisting("Local\\eMaktabWebsiteNativeFocus"))signal.Set();}catch{}return;}
                    using(var signal=new EventWaitHandle(false,EventResetMode.AutoReset,"Local\\eMaktabWebsiteNativeFocus"))using(var form=new LaunchForm(report,portable)) {
                        var listener=ThreadPool.RegisterWaitForSingleObject(signal,delegate {form.BringForward();},null,Timeout.Infinite,false);
                        try {Application.Run(form);}finally{listener.Unregister(null);}
                    }
                } finally {if(owned)mutex.ReleaseMutex();}
            }
        }
    }
}

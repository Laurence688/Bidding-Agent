using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Windows.Forms;

// Built with the Windows .NET Framework compiler. No installed Python GUI toolkit.
internal static class Launcher {
    [StructLayout(LayoutKind.Sequential)] struct BasicLimits {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr MinimumWorkingSet, MaximumWorkingSet;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)] struct IoCounters {
        public ulong ReadOperations, WriteOperations, OtherOperations, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct ExtendedLimits {
        public BasicLimits Basic;
        public IoCounters Io;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, ref ExtendedLimits info, uint size);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);

    static string Identity(string root) {
        using (var sha=SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()))).Replace("-", "").Substring(0,24);
    }
    static string Quote(string value) { return "\"" + value.Replace("\"", "\\\"") + "\""; }

    [STAThread] static void Main() {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        string root=Path.GetFullPath(AppDomain.CurrentDomain.BaseDirectory).TrimEnd(Path.DirectorySeparatorChar);
        string id=Identity(root);
        bool owner;
        using (var mutex=new Mutex(true, "Local\\Laura-"+id, out owner)) {
            if (!owner) {
                try { using(var activate=EventWaitHandle.OpenExisting("Local\\Laura-Activate-"+id)) activate.Set(); }
                catch(WaitHandleCannotBeOpenedException) { }
                return;
            }
            try { Run(root,id); }
            catch(Exception exc) { MessageBox.Show(exc.Message,"Laura 启动提示",MessageBoxButtons.OK,MessageBoxIcon.Error); }
            finally { mutex.ReleaseMutex(); }
        }
    }

    static void Run(string root, string id) {
        if(File.Exists(Path.Combine(root,".laura-update.lock"))) {
            MessageBox.Show("Laura 正在更新，请等待完成后自动打开。若上次更新中断，请保留 .laura-backups 与更新记录并联系维护者处理。", "Laura 更新",MessageBoxButtons.OK,MessageBoxIcon.Information);
            return;
        }
        string python=Path.Combine(root,".venv","Scripts","python.exe");
        string bundledPython=Path.Combine(root,".runtimes","python","python.exe");
        bool bundled=File.Exists(bundledPython);
        if(bundled) python=bundledPython;
        string script=Path.Combine(root,"scripts","desktop_launcher.py");
        if(bundled && (!File.Exists(Path.Combine(root,"data","runtime","first-run.ready")) || !File.Exists(Path.Combine(root,"frontend","dist","index.html")))) {
            string setup=Path.Combine(root,"Setup.ps1");
            if(!File.Exists(setup)) throw new IOException("首次初始化文件缺失，请重新解压完整安装包。");
            var initialize=new ProcessStartInfo("powershell.exe","-NoProfile -ExecutionPolicy Bypass -File "+Quote(setup)+" -DownloadEmbedding") { WorkingDirectory=root, UseShellExecute=true };
            using(var installer=Process.Start(initialize)) {
                installer.WaitForExit();
                if(installer.ExitCode!=0) throw new IOException("首次初始化尚未完成。请检查网络后再次双击 Laura.exe；已下载的内容会保留。");
            }
        }
        if (!File.Exists(python) || !File.Exists(Path.Combine(root,"frontend","dist","index.html"))) {
            MessageBox.Show("请下载并解压 GitHub Release 中的免安装完整包，再双击 Laura.exe。\n\n源码包用户可双击“安装Laura.cmd”准备环境。", "Laura 首次使用", MessageBoxButtons.OK,MessageBoxIcon.Information);
            return;
        }
        if (!File.Exists(script)) throw new IOException("启动文件缺失，请重新解压完整项目。请勿单独移动 Laura.exe。");
        IntPtr job=CreateJobObject(IntPtr.Zero,null);
        if(job==IntPtr.Zero) throw new IOException("无法创建 Laura 进程容器。");
        Process child=null;
        using(var activate=new EventWaitHandle(false,EventResetMode.AutoReset,"Local\\Laura-Activate-"+id))
        using(var splash=new Form()) {
            try {
                var limits=new ExtendedLimits();
                limits.Basic.Flags=0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if(!SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(typeof(ExtendedLimits))))
                    throw new IOException("无法设置 Laura 进程清理策略。");
                splash.Text="Laura · 投标助手";
                splash.ClientSize=new Size(420,145);
                splash.StartPosition=FormStartPosition.CenterScreen;
                splash.FormBorderStyle=FormBorderStyle.FixedDialog;
                splash.MaximizeBox=false;
                splash.Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath);
                var label=new Label { Text="正在启动 Laura…", Dock=DockStyle.Fill, TextAlign=ContentAlignment.MiddleCenter, Font=new Font("Microsoft YaHei UI",12) };
                splash.Controls.Add(label);
                string failure=null;
                bool finished=false;
                object inputGuard=new object();
                var gate=new ManualResetEvent(false);
                bool pendingClose=false;
                Action<string> send=command=> {
                    lock(inputGuard) { child.StandardInput.WriteLine(command); child.StandardInput.Flush(); }
                };
                var info=new ProcessStartInfo(python,Quote(script)) { WorkingDirectory=root, UseShellExecute=false, CreateNoWindow=true, RedirectStandardInput=true, RedirectStandardOutput=true, RedirectStandardError=true, StandardOutputEncoding=Encoding.UTF8, StandardErrorEncoding=Encoding.UTF8 };
                info.EnvironmentVariables["PYTHONUTF8"]="1";
                info.EnvironmentVariables["PYTHONUNBUFFERED"]="1";
                info.EnvironmentVariables["PYTHONNOUSERSITE"]="1";
                info.EnvironmentVariables.Remove("PYTHONHOME");
                info.EnvironmentVariables.Remove("PYTHONPATH");
                child=new Process { StartInfo=info };
                child.OutputDataReceived+=(s,e)=> {
                    if(e.Data==null) return;
                    if(e.Data.StartsWith("LAURA_PID:")) {
                        try {
                            using(var actual=Process.GetProcessById(int.Parse(e.Data.Substring(10)))) {
                                if(!AssignProcessToJobObject(job,actual.Handle)) throw new IOException("无法隔离 Laura 后台进程，已取消启动。");
                            }
                            lock(inputGuard) {
                                send("START");
                                gate.Set();
                                if(pendingClose) send("CLOSE");
                            }
                        } catch(Exception exc) {
                            failure=exc.Message;
                            try { child.StandardInput.Close(); } catch(Exception) { }
                        }
                    }
                    if(e.Data.StartsWith("LAURA_OPEN:")) {
                        try {
                            Uri page;
                            if(!Uri.TryCreate(e.Data.Substring(11),UriKind.Absolute,out page) || page.Scheme!="http" || page.Host!="127.0.0.1" || page.UserInfo.Length!=0)
                                throw new IOException("本地网页地址无效。");
                            // This host is OUTSIDE the service Job: browsers are never killed.
                            Process.Start(new ProcessStartInfo(page.AbsoluteUri) { UseShellExecute=true });
                        } catch(Exception exc) {
                            failure="无法打开默认浏览器："+exc.Message;
                            try { send("CLOSE"); } catch(IOException) { }
                        }
                    }
                    if(e.Data.StartsWith("LAURA_UPDATE:")) {
                        string updateWork=null;
                        try {
                            string token=e.Data.Substring(13);
                            if(!System.Text.RegularExpressions.Regex.IsMatch(token,"^[a-f0-9]{32}$")) throw new IOException("更新请求无效。");
                            string work=Path.Combine(root,".laura-updates",token);
                            updateWork=work;
                            string staged=Path.Combine(work,"extract","Laura");
                            string updater=Path.Combine(staged,"scripts","apply_update.py");
                            string runtime=Path.Combine(staged,".runtimes","python","python.exe");
                            string request=Path.Combine(work,"request.json");
                            if(!File.Exists(updater)||!File.Exists(runtime)||!File.Exists(request)) throw new IOException("更新文件不完整，请重新检查更新。");
                            // The native host is outside the backend Job. The update helper
                            // must survive service shutdown and replacement of this executable.
                            var updateInfo=new ProcessStartInfo(runtime,"-I "+Quote(updater)+" --request "+Quote(request)) {WorkingDirectory=staged,UseShellExecute=false,CreateNoWindow=true};
                            updateInfo.EnvironmentVariables.Remove("PYTHONHOME");
                            updateInfo.EnvironmentVariables.Remove("PYTHONPATH");
                            updateInfo.EnvironmentVariables["PYTHONNOUSERSITE"]="1";
                            updateInfo.EnvironmentVariables["PYTHONUTF8"]="1";
                            updateInfo.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"]="1";
                            // A stale acknowledgement cannot confirm this helper instance.
                            string previousState=Path.Combine(work,"state.json");
                            if(File.Exists(previousState)) File.Delete(previousState);
                            using(var helper=Process.Start(updateInfo)) {
                                var until=DateTime.UtcNow.AddSeconds(90);
                                bool ready=false;
                                while(DateTime.UtcNow<until) {
                                    string stateFile=Path.Combine(work,"state.json");
                                    if(File.Exists(stateFile)) {
                                        string state=File.ReadAllText(stateFile,Encoding.UTF8);
                                        if(System.Text.RegularExpressions.Regex.IsMatch(state,"\"phase\"\\s*:\\s*\"waiting\"")) {ready=true;break;}
                                        if(System.Text.RegularExpressions.Regex.IsMatch(state,"\"status\"\\s*:\\s*\"failed\"")) break;
                                    }
                                    if(helper.HasExited) break;
                                    Thread.Sleep(250);
                                }
                                if(!ready) throw new IOException("更新组件未完成启动确认，原服务保持运行，请重新检查更新。");
                            }
                            send("UPDATE_READY");
                        } catch(Exception exc) {
                            failure="无法启动更新："+exc.Message;
                            if(updateWork!=null) {
                                try { File.WriteAllText(Path.Combine(updateWork,"state.json"),"{\"schema_version\":1,\"status\":\"failed\",\"phase\":\"failed\",\"code\":\"UPDATE_HELPER_START_FAILED\",\"message\":\"无法启动更新，请重新检查更新；原程序与资料保留\",\"installed\":false,\"original_preserved\":true}",new UTF8Encoding(false)); }
                                catch(IOException) { }
                            }
                            try { send("UPDATE_FAILED"); } catch(Exception) { }
                        }
                    }
                    if(e.Data.StartsWith("LAURA_ERROR:")) failure=e.Data.Substring(12);
                    if(splash.IsHandleCreated && !splash.IsDisposed) {
                        try { splash.BeginInvoke((Action)(()=> {
                            if(e.Data=="LAURA_READY") splash.Hide();
                            else if(e.Data.StartsWith("LAURA_STATUS:")) label.Text=e.Data.Substring(13);
                        })); } catch(InvalidOperationException) { }
                    }
                };
                var errors=new StringBuilder();
                child.ErrorDataReceived+=(s,e)=> { if(e.Data!=null && errors.Length<4000) errors.AppendLine(e.Data); };
                splash.Shown+=(s,e)=> {
                    try {
                        child.Start();
                        child.BeginOutputReadLine(); child.BeginErrorReadLine();
                        new Thread(()=> {
                            child.WaitForExit();
                            if(child.ExitCode!=0 && failure==null) failure="启动失败，请查看 data/logs/desktop-launcher.log。\n"+errors;
                            try { splash.BeginInvoke((Action)(()=>{ finished=true; splash.Close(); })); } catch(InvalidOperationException) { }
                        }) { IsBackground=true }.Start();
                        new Thread(()=> {
                            try {
                                while(!child.HasExited) {
                                    if(!gate.WaitOne(300)) continue;
                                    if(activate.WaitOne(300)) {
                                        try { send("FOCUS"); } catch(IOException) { break; }
                                    }
                                }
                            } catch(InvalidOperationException) { }
                        }) { IsBackground=true }.Start();
                    } catch(Exception exc) { failure=exc.Message; finished=true; splash.Close(); }
                };
                splash.FormClosing+=(s,e)=> {
                    if(!finished && child!=null) {
                        e.Cancel=true;
                        label.Text="正在停止 Laura 本地服务…";
                        lock(inputGuard) {
                            pendingClose=true;
                            try { if(gate.WaitOne(0)) send("CLOSE"); } catch(Exception) { }
                        }
                    }
                };
                Application.Run(splash);
                if(failure!=null) MessageBox.Show(failure,"Laura 启动提示",MessageBoxButtons.OK,MessageBoxIcon.Error);
            } finally {
                CloseHandle(job);
                if(child!=null) child.Dispose();
            }
        }
    }
}

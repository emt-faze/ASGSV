using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("ASGSV Launcher")]
[assembly: AssemblyDescription("Launch the existing local ASGSV dialogue generator")]
[assembly: AssemblyVersion("1.0.0.0")]

internal static class Launcher
{
    [STAThread]
    private static int Main()
    {
        Application.EnableVisualStyles();
        try
        {
            string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
            string root = Path.Combine(baseDirectory, "DG", "nte-dialogue-ocr");
            if (!Directory.Exists(root))
                root = Path.Combine(baseDirectory, "nte-dialogue-ocr");

            string script = Path.Combine(root, "launch.ps1");
            string entry = Path.Combine(root, "dialogue_generator_ui.py");
            if (!File.Exists(script) || !File.Exists(entry))
                throw new FileNotFoundException(
                    "\u672a\u627e\u5230 ASGSV \u7a0b\u5e8f\u3002\n\u8bf7\u5c06 ASGSV.exe \u653e\u5728 DG \u6587\u4ef6\u5939\u65c1\uff0c\u6216 DG \u6587\u4ef6\u5939\u5185\u3002\n\n" + baseDirectory);

            string powershell = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.System),
                "WindowsPowerShell", "v1.0", "powershell.exe");
            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = powershell,
                Arguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"" + script + "\" -Generate",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                RedirectStandardError = true,
                RedirectStandardOutput = true
            };

            StringBuilder errors = new StringBuilder();
            using (Process process = new Process())
            {
                process.StartInfo = start;
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (e.Data != null) errors.AppendLine(e.Data);
                };
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { };
                process.Start();
                process.BeginErrorReadLine();
                process.BeginOutputReadLine();
                process.WaitForExit();
                if (process.ExitCode != 0)
                    throw new InvalidOperationException(
                        "\u542f\u52a8\u5931\u8d25\uff0c\u8bf7\u68c0\u67e5 Python \u73af\u5883\u3002\n\n" + errors.ToString());
            }
            return 0;
        }
        catch (Exception error)
        {
            MessageBox.Show(error.Message, "ASGSV \u542f\u52a8\u5931\u8d25",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

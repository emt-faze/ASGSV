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
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string script = Path.Combine(root, @"scripts\launch_desktop.py");
            if (!File.Exists(script))
            {
                root = Path.Combine(root, "DG");
                script = Path.Combine(root, @"scripts\launch_desktop.py");
            }
            if (!File.Exists(script))
                throw new FileNotFoundException("ASGSV files were not found. Extract the full ZIP before launching.");

            string python = Path.Combine(root, @"runtime\python\python.exe");
            if (!File.Exists(python))
                throw new FileNotFoundException("Embedded Python was not found. Run the ASGSV installer first.");

            ProcessStartInfo start = new ProcessStartInfo
            {
                FileName = python,
                Arguments = "-I -X utf8 \"" + script + "\"",
                WorkingDirectory = Path.Combine(root, "nte-dialogue-ocr"),
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                StandardErrorEncoding = Encoding.UTF8,
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

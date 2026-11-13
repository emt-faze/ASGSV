param(
    [ValidateSet('Inspect', 'Invoke')][string]$Mode = 'Inspect',
    [Parameter(Mandatory=$true)][int]$LauncherPid,
    [Parameter(Mandatory=$true)][long]$WindowHandle
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class LauncherIdentity {
    [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr process, int flags, StringBuilder path, ref int size);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    public static string Path(int pid) {
        IntPtr h = OpenProcess(0x1000, false, pid);
        if(h == IntPtr.Zero) throw new InvalidOperationException("Cannot verify launcher process");
        try {
            int length = 32768; var path = new StringBuilder(length);
            if(!QueryFullProcessImageName(h, 0, path, ref length)) throw new InvalidOperationException("Cannot verify launcher path");
            return path.ToString();
        } finally { CloseHandle(h); }
    }
}
'@
$expectedPath = 'F:\Wuthering Waves\3.0.1.0\launcher_main.exe'
$expectedLabel = [string][char]0x8fdb + [char]0x5165 + [char]0x6e38 + [char]0x620f
if ([LauncherIdentity]::Path($LauncherPid) -ine $expectedPath) { throw 'Launcher path mismatch' }
$root = [Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
if ($null -eq $root -or $root.Current.ProcessId -ne $LauncherPid -or
    $root.Current.ClassName -notmatch '^HwndWrapper\[launcher_main;;[a-fA-F0-9-]{36}\]$') {
    throw 'Launcher window identity mismatch'
}
$condition = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty, $expectedLabel)
$matches = $root.FindAll([Windows.Automation.TreeScope]::Descendants, $condition)
$found = @()
foreach ($item in $matches) {
    $info = $item.Current
    $rect = $info.BoundingRectangle
    $found += [pscustomobject]@{name=$info.Name; type=$info.ControlType.ProgrammaticName;
        enabled=$info.IsEnabled; offscreen=$info.IsOffscreen;
        bounds=@($rect.X,$rect.Y,$rect.Width,$rect.Height)}
}
if ($Mode -eq 'Inspect') {
    ConvertTo-Json -InputObject @($found) -Depth 4
    exit 0
}
if ($matches.Count -ne 1) { throw 'Expected exactly one observed launch control; no action taken' }
$button = $matches.Item(0)
$info = $button.Current
if (-not $info.IsEnabled -or $info.IsOffscreen) { throw 'Launch control is not currently available' }
$windowRect = $root.Current.BoundingRectangle
$rect = $info.BoundingRectangle
$x = ($rect.X + $rect.Width / 2 - $windowRect.X) / $windowRect.Width
$y = ($rect.Y + $rect.Height / 2 - $windowRect.Y) / $windowRect.Height
if ($x -lt .6 -or $x -gt .97 -or $y -lt .68 -or $y -gt .96) { throw 'Launch control outside the verified button area' }
$pattern = $null
if (-not $button.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
    throw 'Launch button does not expose InvokePattern; no fallback input'
}
if ([LauncherIdentity]::Path($LauncherPid) -ine $expectedPath -or $button.Current.Name -ne $expectedLabel) {
    throw 'Launcher changed before invocation'
}
$pattern.Invoke()
Write-Output 'Invoked the official launcher button once; game startup still needs verification.'

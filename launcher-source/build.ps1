$ErrorActionPreference = 'Stop'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
$output = Join-Path (Split-Path -Parent $PSScriptRoot) 'ASGSV.exe'
$source = Join-Path $PSScriptRoot 'Launcher.cs'
$root = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $root 'assets\app_icon.ico'
if (-not (Test-Path -LiteralPath $icon -PathType Leaf)) {
    $icon = Join-Path $root 'DG\assets\app_icon.ico'
}
& $compiler "/win32icon:$icon" /nologo /target:winexe /platform:anycpu /optimize+ /reference:System.Windows.Forms.dll "/out:$output" $source
if ($LASTEXITCODE -ne 0) { throw 'Launcher compilation failed.' }
Get-Item -LiteralPath $output | Select-Object FullName, Length

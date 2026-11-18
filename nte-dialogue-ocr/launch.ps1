param([switch]$Admin, [switch]$AutoConnect, [switch]$Dub, [switch]$Generate, [string]$Project,
      [ValidateSet('nte', 'wuwa')][string]$Game)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Embedded Python is missing. Run the installer in the ASGSV root folder.'
}
$arguments = @('-I', '-X', 'utf8', ('"' + (Join-Path $root 'scripts\launch_desktop.py') + '"'))
if ($Generate) {
    if ($Project) { $arguments += @('--project', ('"' + $Project + '"')) }
    if ($Game) { $arguments += @('--game', $Game) }
} else {
    $arguments += '--ocr'
    if ($AutoConnect) { $arguments += '--autoconnect' }
    if ($Dub) { $arguments += '--dub' }
}
if ($Admin) {
    Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -Verb RunAs
} else {
    Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}

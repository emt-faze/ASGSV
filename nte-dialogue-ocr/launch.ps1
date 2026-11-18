param([switch]$Admin, [switch]$AutoConnect, [switch]$Dub, [switch]$Generate, [string]$Project,
      [ValidateSet('nte', 'wuwa')][string]$Game)
$ErrorActionPreference = 'Stop'
$ocrRoot = $PSScriptRoot
$root = Split-Path -Parent $ocrRoot
$portablePython = Join-Path $root 'runtime\python.exe'
$ocrPython = Join-Path $ocrRoot '.venv\Scripts\pythonw.exe'
if (Test-Path -LiteralPath $portablePython -PathType Leaf) {
    $ocrPython = Join-Path $root 'runtime\pythonw.exe'
    $settings = Join-Path $ocrRoot 'config.json'
    $voiceSettings = Join-Path $root 'wuwa-dubber\data\config.json'
    if (-not ((Test-Path -LiteralPath $settings -PathType Leaf) -and (Test-Path -LiteralPath $voiceSettings -PathType Leaf))) {
        $configure = Join-Path $root 'scripts\configure_generator.py'
        & $portablePython $configure --portable
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create ASGSV settings.' }
    }
}
if (-not (Test-Path -LiteralPath $ocrPython)) {
    $runtimeFile = Join-Path $ocrRoot 'runtime.json'
    if (Test-Path -LiteralPath $runtimeFile) {
        $runtime = Get-Content -LiteralPath $runtimeFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $ocrPython = [string]$runtime.pythonw_path
    }
}
if (-not (Test-Path -LiteralPath $ocrPython)) {
    $ocrPython = Join-Path (Split-Path -Parent $ocrRoot) 'wuwa-dubber\.venv\Scripts\pythonw.exe'
}
if (-not (Test-Path -LiteralPath $ocrPython)) {
    throw 'Python OCR runtime is missing. See the local usage instructions.'
}
$ocrScript = Join-Path $ocrRoot 'app.py'
if ($Generate) { $ocrScript = Join-Path $ocrRoot 'dialogue_generator_ui.py' }
$ocrArguments = @(('"' + $ocrScript + '"'))
if ($Generate) {
    if ($Project) { $ocrArguments += @('--project', ('"' + $Project + '"')) }
    if ($Game) { $ocrArguments += @('--game', $Game) }
} else {
    if ($AutoConnect) { $ocrArguments += '--autoconnect' }
    if ($Dub) { $ocrArguments += '--dub' }
}
if ($Admin) {
    Start-Process -FilePath $ocrPython -ArgumentList $ocrArguments -WorkingDirectory $ocrRoot -WindowStyle Hidden -Verb RunAs
} else {
    Start-Process -FilePath $ocrPython -ArgumentList $ocrArguments -WorkingDirectory $ocrRoot -WindowStyle Hidden
}

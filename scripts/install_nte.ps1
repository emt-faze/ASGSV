param([string]$PythonPath = "")
$ErrorActionPreference = 'Stop'
$PackageRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONNOUSERSITE = '1'
try {
    $Arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        (Join-Path $PSScriptRoot 'install_generator.ps1'))
    if ($PythonPath) { $Arguments += @('-PythonPath', $PythonPath) }
    & powershell.exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Application installation did not finish.' }
    $AppPython = Join-Path $PackageRoot 'nte-dialogue-ocr\.venv\Scripts\python.exe'
    & $AppPython -X utf8 (Join-Path $PackageRoot 'nte-dialogue-ocr\scripts\download_nte_reader.py')
    if ($LASTEXITCODE -ne 0) { throw 'NTE resource-reader preparation did not finish.' }
    Write-Host '异环配音已就绪。请双击“打开异环配音.bat”，在“任务库与配音准备”页提取台词。'
} catch {
    Write-Host ('安装未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

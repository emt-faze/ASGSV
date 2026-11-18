$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'runtime\python\python.exe'
& (Join-Path $PSScriptRoot 'install_generator.ps1')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -I -X utf8 (Join-Path $root 'nte-dialogue-ocr\scripts\download_nte_reader.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host '异环配音已就绪。请双击“打开异环配音.bat”。'

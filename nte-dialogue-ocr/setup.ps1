param([switch]$SkipModels)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $root 'scripts\install_generator.ps1') -SkipModels:$SkipModels
exit $LASTEXITCODE

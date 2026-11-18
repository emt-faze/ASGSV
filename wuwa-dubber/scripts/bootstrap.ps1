param([switch]$CoreOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if ($CoreOnly) {
    & (Join-Path $root 'scripts\setup_embedded_python.ps1')
} else {
    & (Join-Path $root 'scripts\install.ps1')
}
exit $LASTEXITCODE

param([switch]$SkipModels, [switch]$DependenciesOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root 'runtime\python\python.exe'
$ocr = Join-Path $root 'nte-dialogue-ocr'
try {
    & (Join-Path $PSScriptRoot 'setup_embedded_python.ps1')
    . (Join-Path $PSScriptRoot 'embedded_dependencies.ps1')
    Install-EmbeddedRequirements -Python $python -Requirements (Join-Path $ocr 'requirements.txt')
    Install-EmbeddedCppRuntime -Python $python
    & $python -I -m pip check
    if ($LASTEXITCODE -ne 0) { throw '依赖版本检查失败。' }
    if (-not $DependenciesOnly) {
        & $python -I -X utf8 (Join-Path $PSScriptRoot 'configure_generator.py') --portable
        if ($LASTEXITCODE -ne 0) { throw '初始化设置失败。' }
        if (-not $SkipModels) {
            & $python -I -X utf8 (Join-Path $ocr 'scripts\download_models.py')
            if ($LASTEXITCODE -ne 0) { throw 'OCR 模型准备失败，可以重新运行安装。' }
        }
    }
    Write-Host '台词工具环境已就绪。'
} catch {
    Write-Host ('安装未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

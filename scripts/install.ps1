param([switch]$WithModel, [switch]$ModelScope)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$modelRoot = Join-Path $root 'local-voice-model'
$python = Join-Path $root 'runtime\python\python.exe'
$configure = Join-Path $PSScriptRoot 'configure_install.py'

try {
    & (Join-Path $PSScriptRoot 'setup_embedded_python.ps1')
    . (Join-Path $PSScriptRoot 'embedded_dependencies.ps1')
    Install-EmbeddedRequirements -Python $python -Requirements (Join-Path $root 'wuwa-dubber\requirements.txt')
    Install-EmbeddedCppRuntime -Python $python
    & $python -I -m pip check
    if ($LASTEXITCODE -ne 0) { throw '依赖版本检查失败。' }
    if (-not $WithModel) {
        & $python -I -X utf8 $configure
        exit $LASTEXITCODE
    }

    # Model wheels pin different NumPy and ONNX Runtime versions than the desktop.
    # Stage them separately and promote only after imports and the CUDA check work.
    $modelSite = Join-Path (Split-Path -Parent $python) 'model-site'
    $staging = Join-Path (Split-Path -Parent $python) ('model-site-' + [guid]::NewGuid())
    $backup = $staging + '.previous'
    try {
        Install-EmbeddedRequirements -Python $python -Requirements (Join-Path $modelRoot 'requirements.txt') `
            -ExtraArguments @('--target', $staging, '--ignore-installed', '--no-binary=sox',
                              '--extra-index-url', 'https://download.pytorch.org/whl/cu128')
        & $python -I -c "import sys; sys.path.insert(0,sys.argv[1]); import torch; assert torch.cuda.is_available(), 'CUDA GPU unavailable'; print('GPU:',torch.cuda.get_device_name(0)); print(torch.ones(1,device='cuda'))" $staging
        if ($LASTEXITCODE -ne 0) { throw 'CUDA 验证失败，请检查 NVIDIA 驱动。' }
        if (Test-Path -LiteralPath $modelSite) { Move-Item -LiteralPath $modelSite -Destination $backup }
        try { Move-Item -LiteralPath $staging -Destination $modelSite } catch {
            if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $modelSite }
            throw
        }
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Recurse -Force }
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
    $arguments = @((Join-Path $modelRoot 'download_models.py'))
    if ($ModelScope) { $arguments += '--modelscope-fallback' }
    & $python -I -X utf8 @arguments
    if ($LASTEXITCODE -ne 0) { throw '模型下载未完成，可重试续传。' }
    $ffmpeg = & $python -I -c "import sys; sys.path.insert(0,sys.argv[1]); import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" $modelSite
    if ($LASTEXITCODE -ne 0 -or -not $ffmpeg) { throw 'FFmpeg 验证失败。' }
    & $python -I -X utf8 $configure --with-model --ffmpeg ([string]$ffmpeg)
    if ($LASTEXITCODE -ne 0) { throw '本地模型设置写入失败。' }
    Write-Host '角色配音环境已就绪。'
} catch {
    Write-Host ('安装未完成：' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

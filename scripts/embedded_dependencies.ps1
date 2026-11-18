function Install-EmbeddedRequirements {
    param([string]$Python, [string]$Requirements, [string[]]$ExtraArguments = @())

    foreach ($index in @('https://pypi.tuna.tsinghua.edu.cn/simple', 'https://pypi.org/simple')) {
        & $Python -I -X utf8 -m pip --isolated install --index-url $index --only-binary=:all: `
            --no-warn-script-location --disable-pip-version-check --retries 2 --timeout 45 `
            -r $Requirements @ExtraArguments
        if ($LASTEXITCODE -eq 0) { return }
        if ($index -ne 'https://pypi.org/simple') { Write-Host '镜像安装失败，改用 PyPI 官方源。' }
    }
    throw "依赖安装失败：$Requirements"
}

function Install-EmbeddedCppRuntime {
    param([string]$Python)

    # OpenCV and ONNX Runtime can be imported before Qt.
    $runtime = Split-Path -Parent $Python
    $qt = Join-Path $runtime 'Lib\site-packages\PySide6'
    foreach ($dll in @('msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll')) {
        $destination = Join-Path $runtime $dll
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
            Copy-Item -LiteralPath (Join-Path $qt $dll) -Destination $destination
        }
    }
}

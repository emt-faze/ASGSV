param([string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = 'Stop'
$runtime = Join-Path ([IO.Path]::GetFullPath($ProjectRoot)) 'runtime'
$homePath = Join-Path $runtime 'python'
$python = Join-Path $homePath 'python.exe'
$download = 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
New-Item -ItemType Directory -Path $runtime -Force | Out-Null

$deadline = [Diagnostics.Stopwatch]::StartNew()
do {
    try {
        $lock = [IO.File]::Open((Join-Path $runtime '.setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch [IO.IOException] {
        if ($deadline.Elapsed.TotalSeconds -ge 180) { throw }
        Start-Sleep -Milliseconds 300
    }
} until ($lock)

$zipPath = Join-Path ([IO.Path]::GetTempPath()) ('asgsv-python-' + [guid]::NewGuid() + '.zip')
$pipPath = Join-Path ([IO.Path]::GetTempPath()) ('asgsv-get-pip-' + [guid]::NewGuid() + '.py')
try {
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Write-Host 'Downloading Python 3.12.8 x64 from python.org...'
        Invoke-WebRequest -UseBasicParsing -Uri $download -OutFile $zipPath -TimeoutSec 120
        $hash = [Security.Cryptography.MD5]::Create()
        $stream = [IO.File]::OpenRead($zipPath)
        try { $digest = [BitConverter]::ToString($hash.ComputeHash($stream)).Replace('-', '') }
        finally { $stream.Dispose(); $hash.Dispose() }
        if ($digest -ine '1e86b04bc7d27c5c06edf8f617e1184a') {
            throw 'The Python archive does not match the checksum published by python.org.'
        }
        # Extract first so a failed extraction cannot leave python.exe as a false readiness signal.
        $staging = Join-Path $runtime ('embed-' + [guid]::NewGuid())
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $staging)
        New-Item -ItemType Directory -Path $homePath -Force | Out-Null
        foreach ($file in Get-ChildItem -LiteralPath $staging -File | Sort-Object { $_.Name -eq 'python.exe' }) {
            Move-Item -LiteralPath $file.FullName -Destination (Join-Path $homePath $file.Name) -Force
        }
        Remove-Item -LiteralPath $staging
    }
    $pth = Join-Path $homePath 'python312._pth'
    $paths = @('python312.zip', '.', '..\..', 'Lib\site-packages',
               '..\..\nte-dialogue-ocr', '..\..\wuwa-dubber\src', '..\..\scripts', 'import site')
    New-Item -ItemType Directory -Path (Join-Path $homePath 'Lib\site-packages') -Force | Out-Null
    if (-not (Test-Path -LiteralPath $pth) -or (Get-Content -LiteralPath $pth -Raw) -ne (($paths -join "`r`n") + "`r`n")) {
        $paths | Set-Content -LiteralPath $pth -Encoding Ascii
    }
    & $python -I -c "import sys; assert sys.version_info[:3] == (3,12,8) and sys.maxsize > 2**32"
    if ($LASTEXITCODE -ne 0) { throw "Expected Python 3.12.8 x64 at $python" }
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $python -I -m pip --version *> $null
        $needsPip = $LASTEXITCODE -ne 0
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($needsPip) {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $pipPath -TimeoutSec 60
        & $python -I $pipPath --no-warn-script-location --index-url 'https://pypi.org/simple'
        if ($LASTEXITCODE -ne 0) { throw 'pip setup failed.' }
    }
    & $python -I -m pip --version
    if ($LASTEXITCODE -ne 0) { throw 'pip verification failed.' }
} finally {
    foreach ($path in @($zipPath, $pipPath)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
    $lock.Dispose()
}

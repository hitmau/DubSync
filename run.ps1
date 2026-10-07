$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonPath)) { throw 'Execute .\setup.ps1 antes de iniciar o DubSync.' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js 22+ não foi encontrado. Execute setup.ps1 após instalar Node.js.' }
$nodeVersion = (& node --version) -replace '^v', ''
if ([version]$nodeVersion -lt [version]'22.0') { throw "yt-dlp requer Node.js 22+; versão encontrada: $nodeVersion." }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) { throw 'FFmpeg não foi encontrado no PATH.' }
if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) { throw 'ffprobe não foi encontrado no PATH.' }
$port = 8766
$portFile = Join-Path (Join-Path $env:APPDATA 'DubSync') 'port.txt'
if (Test-Path -LiteralPath $portFile) {
    $savedPort = 0
    if ([int]::TryParse((Get-Content -LiteralPath $portFile -Raw).Trim(), [ref]$savedPort) -and
        $savedPort -ge 1 -and $savedPort -le 65535) {
        $port = $savedPort
    }
}
& $pythonPath -m Dupsync.app.server --host 0.0.0.0 --port $port

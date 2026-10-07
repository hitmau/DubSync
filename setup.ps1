$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python Launcher (py) was not found. Install Python 3.13 x64 first.'
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw 'Node.js 22+ is required by yt-dlp for YouTube format verification. Install Node.js and run setup.ps1 again.'
}
$nodeVersion = (& node --version) -replace '^v', ''
if ([version]$nodeVersion -lt [version]'22.0') { throw "yt-dlp requires Node.js 22+; found $nodeVersion." }
py -3.13 -c "import sys, struct; assert sys.version_info >= (3,13); assert struct.calcsize('P') == 8"
if (-not (Test-Path .venv\Scripts\python.exe)) { py -3.13 -m venv .venv }
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $pythonPath -m pip install --upgrade pip
& $pythonPath -m pip install paddlepaddle==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
& $pythonPath -m pip install -r requirements.txt
& $pythonPath -c "import paddle; paddle.utils.run_check(); import cv2, fastapi, paddleocr, sqlalchemy, yt_dlp; from yt_dlp.version import __version__; print('Ambiente DubSync pronto · yt-dlp', __version__)"

$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    throw 'O compilador .NET Framework do Windows não foi encontrado.'
}
$source = Join-Path $PSScriptRoot 'Program.cs'
$icon = Join-Path $PSScriptRoot 'dubsync.ico'
if (-not (Test-Path -LiteralPath $icon)) {
    throw 'O ícone launcher\dubsync.ico não foi encontrado. Execute .venv\Scripts\python.exe launcher\generate_icon.py.'
}
$output = Join-Path $projectDir 'DubSyncLauncher.exe'
& $compiler /nologo /target:winexe /platform:anycpu /codepage:65001 `
    /reference:System.dll /reference:System.Core.dll `
    /reference:System.Drawing.dll /reference:System.Windows.Forms.dll `
    "/win32icon:$icon" `
    "/out:$output" $source
if ($LASTEXITCODE -ne 0) { throw 'A compilação do iniciador falhou.' }
$item = Get-Item -LiteralPath $output
Write-Host "Criado: $($item.FullName) ($([Math]::Round($item.Length / 1KB, 1)) KB)"

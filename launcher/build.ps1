$ErrorActionPreference = 'Stop'
$projectDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path -LiteralPath $vswhere) {
        $vsInstall = & $vswhere -latest -products '*' -property installationPath
        if ($vsInstall) {
            $compiler = Join-Path $vsInstall 'MSBuild\Current\Bin\Roslyn\csc.exe'
        }
    }
}
if (-not (Test-Path -LiteralPath $compiler)) {
    $vsRoot = Join-Path $env:ProgramFiles 'Microsoft Visual Studio'
    if (Test-Path -LiteralPath $vsRoot) {
        $compiler = Get-ChildItem -LiteralPath $vsRoot -Filter csc.exe -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match '\\MSBuild\\Current\\Bin\\Roslyn\\csc\.exe$' } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
}
if (-not $compiler -or -not (Test-Path -LiteralPath $compiler)) {
    throw 'Não foi encontrado o compilador do .NET Framework nem o Roslyn do Visual Studio.'
}
$source = Join-Path $PSScriptRoot 'Program.cs'
$icon = Join-Path $PSScriptRoot 'dubsync.ico'
if (-not (Test-Path -LiteralPath $icon)) {
    throw 'O ícone launcher\dubsync.ico não foi encontrado. Execute .venv\Scripts\python.exe launcher\generate_icon.py.'
}
$output = Join-Path $projectDir 'DubSyncLauncher.exe'
$runningMain = Get-Process -Name 'DubSyncLauncher' -ErrorAction SilentlyContinue |
    Where-Object {
        try { [string]::Equals($_.Path, $output, [StringComparison]::OrdinalIgnoreCase) }
        catch { $false }
    } | Select-Object -First 1
if ($runningMain) {
    $output = Join-Path $projectDir 'DubSyncLauncher.atualizado.exe'
    Write-Host 'DubSync Controle está aberto; compilando a atualização ao lado do executável em uso.'
}
& $compiler /nologo /target:winexe /platform:anycpu /codepage:65001 `
    /reference:System.dll /reference:System.Core.dll `
    /reference:System.Drawing.dll /reference:System.Windows.Forms.dll `
    "/win32icon:$icon" `
    "/out:$output" $source
if ($LASTEXITCODE -ne 0) { throw 'A compilação do iniciador falhou.' }
$item = Get-Item -LiteralPath $output
Write-Host "Criado: $($item.FullName) ($([Math]::Round($item.Length / 1KB, 1)) KB)"

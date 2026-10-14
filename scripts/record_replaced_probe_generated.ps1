param([string]$Manifest = '.data\replaced-probe-install.json')

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$manifestPath = (Resolve-Path (Join-Path $repoRoot $Manifest)).Path
$dataRoot = (Resolve-Path (Join-Path $repoRoot '.data')).Path
if (-not $manifestPath.StartsWith($dataRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Manifest must be inside this repository .data directory.'
}
if (Get-Process -Name Replaced -ErrorAction SilentlyContinue) { throw 'Close REPLACED before recording generated files.' }
$install = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($install.bepinex_preexisting) { throw 'This install did not create the BepInEx root; refusing to claim its files.' }
$gameRoot = (Resolve-Path $install.game_root).Path
$loaderRoot = Join-Path $gameRoot 'BepInEx'
if (-not (Test-Path -LiteralPath $loaderRoot -PathType Container)) { throw "BepInEx directory not found: $loaderRoot" }

$paths = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($relative in $install.files_added) { [void]$paths.Add([string]$relative) }
foreach ($file in Get-ChildItem -LiteralPath $loaderRoot -File -Recurse) {
    [void]$paths.Add([System.IO.Path]::GetRelativePath($gameRoot, $file.FullName))
}
$install.files_added = @($paths | Sort-Object)
$install.generated_scan_complete = $true
$install | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
Write-Output "Recorded $($paths.Count) installed and generated files for a safe rollback."

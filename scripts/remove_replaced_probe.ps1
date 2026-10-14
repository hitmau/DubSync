param([string]$Manifest = '.data\replaced-probe-install.json')

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$manifestPath = (Resolve-Path (Join-Path $repoRoot $Manifest)).Path
$dataRoot = (Resolve-Path (Join-Path $repoRoot '.data')).Path
if (-not $manifestPath.StartsWith($dataRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Rollback manifest must be inside this repository .data directory.'
}
$install = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$gameRoot = (Resolve-Path $install.game_root).Path
if (-not (Test-Path -LiteralPath (Join-Path $gameRoot 'Replaced.exe') -PathType Leaf)) {
    throw "The manifest game root no longer contains Replaced.exe: $gameRoot"
}
if ($install.bepinex_preexisting -or -not $install.generated_scan_complete) {
    throw 'Rollback is not armed: record the generated BepInEx files first, or preserve the pre-existing loader.'
}
$loaderRoot = Join-Path $gameRoot 'BepInEx'
$listed = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($relative in $install.files_added) { [void]$listed.Add([string]$relative) }
if (Test-Path -LiteralPath $loaderRoot -PathType Container) {
    $untracked = @(Get-ChildItem -LiteralPath $loaderRoot -File -Recurse | ForEach-Object {
        [System.IO.Path]::GetRelativePath($gameRoot, $_.FullName)
    } | Where-Object { -not $listed.Contains($_) })
    if ($untracked) { throw "Rollback stopped to preserve untracked BepInEx files: $($untracked -join ', ')" }
}
$candidateDirectories = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($relative in $install.files_added) {
    $target = [System.IO.Path]::GetFullPath((Join-Path $gameRoot $relative))
    if (-not $target.StartsWith($gameRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Manifest contains a path outside the game root: $relative"
    }
    if (Test-Path -LiteralPath $target -PathType Leaf) { Remove-Item -LiteralPath $target -Force }
    $parent = Split-Path -Parent $target
    while ($parent.StartsWith($gameRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        [void]$candidateDirectories.Add($parent)
        $parent = Split-Path -Parent $parent
    }
}
$orderedDirectories = @($candidateDirectories) | Sort-Object { $_.Length } -Descending
foreach ($directory in $orderedDirectories) {
    if (Test-Path -LiteralPath $directory -PathType Container) {
        if (-not (Get-ChildItem -LiteralPath $directory -Force | Select-Object -First 1)) {
            Remove-Item -LiteralPath $directory -Force
        }
    }
}
Remove-Item -LiteralPath $manifestPath -Force
Write-Output 'Removed only the loader and plugin files recorded by the rollback manifest.'

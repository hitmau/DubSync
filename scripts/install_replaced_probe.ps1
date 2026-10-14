param(
    [string]$GameRoot = 'I:\SteamLibrary\steamapps\common\REPLACED',
    [string]$LoaderStage = '.data\tools\bepinex-be788',
    [string]$PluginDll = 'integration\plugins\replaced-articy\runtime\bin\Release\net6.0\DubSync.ReplacedProbe.dll',
    [string]$ApiBaseUrl = 'http://127.0.0.1:8766',
    [string]$ProjectId = 'replaced'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$gamePath = (Resolve-Path $GameRoot).Path
$stagePath = (Resolve-Path (Join-Path $repoRoot $LoaderStage)).Path
$pluginPath = (Resolve-Path (Join-Path $repoRoot $PluginDll)).Path
$manifestPath = Join-Path $repoRoot '.data\replaced-probe-install.json'
$gameExe = Join-Path $gamePath 'Replaced.exe'
$loaderRoot = Join-Path $gamePath 'BepInEx'
if (-not (Test-Path -LiteralPath $gameExe -PathType Leaf)) { throw "Replaced.exe not found under $gamePath" }
if (Test-Path -LiteralPath $manifestPath) { throw "An install manifest already exists: $manifestPath" }
if (Test-Path -LiteralPath $loaderRoot) { throw "Refusing an existing BepInEx folder: $loaderRoot" }
if (Get-Process -Name Replaced -ErrorAction SilentlyContinue) { throw 'Close REPLACED before installing the loader.' }

$stageFiles = @(Get-ChildItem -LiteralPath $stagePath -File -Recurse)
if (-not $stageFiles) { throw "Loader stage is empty: $stagePath" }
$relativePaths = [System.Collections.Generic.List[string]]::new()
foreach ($file in $stageFiles) {
    $relative = [System.IO.Path]::GetRelativePath($stagePath, $file.FullName)
    $destination = Join-Path $gamePath $relative
    if (Test-Path -LiteralPath $destination) { throw "Refusing to overwrite existing game file: $destination" }
    $relativePaths.Add($relative)
}
$pluginRelative = 'BepInEx\plugins\DubSync.ReplacedProbe.dll'
$configRelative = 'BepInEx\config\DubSync.ReplacedProbe.json'
$pluginDestination = Join-Path $gamePath $pluginRelative
if (Test-Path -LiteralPath $pluginDestination) { throw "Refusing to overwrite existing plugin: $pluginDestination" }
$configDestination = Join-Path $gamePath $configRelative
if (Test-Path -LiteralPath $configDestination) { throw "Refusing to overwrite existing plugin config: $configDestination" }
$apiUri = $null
if (-not [Uri]::TryCreate($ApiBaseUrl, [UriKind]::Absolute, [ref]$apiUri) -or
    $apiUri.Scheme -ne 'http' -or $apiUri.Host -notin @('127.0.0.1','localhost','::1')) {
    throw 'The adapter endpoint must use HTTP on a loopback address.'
}
if ($ProjectId -notmatch '^[A-Za-z0-9_-]{1,48}$') { throw 'ProjectId is invalid.' }
$relativePaths.Add($pluginRelative)
$relativePaths.Add($configRelative)

$manifest = [ordered]@{
    installed_at = (Get-Date).ToString('o')
    game_root = $gamePath
    game_executable = $gameExe
    loader_root = $loaderRoot
    bepinex_preexisting = $false
    generated_scan_complete = $false
    plugin_id = 'com.dubsync.replaced.probe'
    loader_stage = $stagePath
    files_added = @($relativePaths)
}
New-Item -ItemType Directory -Path (Split-Path -Parent $manifestPath) -Force | Out-Null
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8

foreach ($file in $stageFiles) {
    $relative = [System.IO.Path]::GetRelativePath($stagePath, $file.FullName)
    $destination = Join-Path $gamePath $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination
}
New-Item -ItemType Directory -Path (Split-Path -Parent $pluginDestination) -Force | Out-Null
Copy-Item -LiteralPath $pluginPath -Destination $pluginDestination
$pluginConfig = @{api_base_url=$ApiBaseUrl.TrimEnd('/');project_id=$ProjectId} | ConvertTo-Json
New-Item -ItemType Directory -Path (Split-Path -Parent $configDestination) -Force | Out-Null
$pluginConfig | Set-Content -LiteralPath $configDestination -Encoding utf8

Write-Output "Installed $($relativePaths.Count) new files. Rollback manifest: $manifestPath"

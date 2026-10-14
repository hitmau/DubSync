param(
    [string]$GameRoot = 'I:\SteamLibrary\steamapps\common\REPLACED',
    [string]$PluginDll = 'integration\plugins\replaced-articy\runtime\bin\Release\net6.0\DubSync.ReplacedProbe.dll'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$gamePath = (Resolve-Path $GameRoot).Path
$pluginPath = (Resolve-Path (Join-Path $repoRoot $PluginDll)).Path
$manifestPath = Join-Path $repoRoot '.data\replaced-probe-install.json'
if (Get-Process -Name Replaced -ErrorAction SilentlyContinue) { throw 'Close REPLACED before updating the loaded plugin.' }
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'The local rollback manifest was not found.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ((Resolve-Path $manifest.game_root).Path -ne $gamePath) { throw 'The rollback manifest points to a different game folder.' }

$pluginRelative = 'BepInEx\plugins\DubSync.ReplacedProbe.dll'
$configRelative = 'BepInEx\config\DubSync.ReplacedProbe.json'
if ($pluginRelative -notin $manifest.files_added) { throw 'The plugin path is not owned by this install manifest.' }
$pluginDestination = Join-Path $gamePath $pluginRelative
if (-not (Test-Path -LiteralPath $pluginDestination -PathType Leaf)) { throw 'The installed plugin file is missing.' }
Copy-Item -LiteralPath $pluginPath -Destination $pluginDestination -Force

$configDestination = Join-Path $gamePath $configRelative
if (-not (Test-Path -LiteralPath $configDestination -PathType Leaf)) {
    @{api_base_url='http://127.0.0.1:8766';project_id='replaced'} |
        ConvertTo-Json | Set-Content -LiteralPath $configDestination -Encoding utf8
    $manifest.files_added = @($manifest.files_added) + @($configRelative)
    $manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
}
Write-Output "Updated the probe to the compiled build at $pluginPath."

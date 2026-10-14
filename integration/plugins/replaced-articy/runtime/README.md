# DubSync REPLACED Probe

The BepInEx IL2CPP plugin hooks `SadCatStudios.Dialogs.SubtitlesManager.InitLine` and `ReleaseLine`. At each subtitle start it looks for that dialogue's approved take in the active DubSync runtime package and plays the OGG through the game's FMOD runtime. It first uses a saved runtime trigger and falls back to a unique exact-text match. It does not require manually entering Articy IDs or regenerating an already approved voice.

The plugin resolves its game folder from the loaded DLL's location, so package and configuration lookup do not depend on the process working directory. At startup, `BepInEx/LogOutput.log` records both `game_root` and `process_cwd`; after loading it records the active package directory and number of approved takes.

## Build and update

The repository includes a local .NET SDK and the staged BepInEx references. From the repository root:

```powershell
& '.data\tools\dotnet\dotnet.exe' build integration\plugins\replaced-articy\runtime\DubSync.ReplacedProbe.csproj -c Release
```

Close REPLACED before replacing the DLL, then run:

```powershell
& '.data\tools\dotnet\dotnet.exe' build integration\plugins\replaced-articy\runtime\DubSync.ReplacedProbe.csproj -c Release
& '.\scripts\update_replaced_probe.ps1'
```

The update script refuses to copy while `Replaced.exe` is running and verifies the configured game folder against the local install manifest. Reopen the game after it reports success.

## Self-diagnosis

Open `I:\SteamLibrary\steamapps\common\REPLACED\BepInEx\LogOutput.log` and search for `DubSync did not start game audio` and the following `DubSync lookup details` line. The latter reports the package name, number of entries, trigger candidates, exact-text candidates, whether a speaker key was present, and normalized text length. It does not require changing IDs or deleting audio.

- `approved_take_not_found`: no unique approved package entry matched. Check that the character's take is approved and that the loaded package has the expected take count. If `exact_text_candidates=0`, publish a runtime package again before capturing another reproduction.
- `ambiguous_exact_text`: more than one dialogue still matches after speaker filtering. Correct the dialogue text/character or rely on the runtime trigger learned from a live event; do not guess an ID.
- `approved_audio_file_missing`: the manifest points to an OGG that is absent from that package. Republish the package from the existing approved take; voice generation is unnecessary.
- `runtime_package_not_installed`: no active package pointer or manifest was found under the logged `game_root`. Publish/deploy the package, then restart the game.

If the error repeats after a successful DLL update and package deployment, save the two log lines (the warning and its lookup details) plus the startup lines for `game_root` and `Loaded DubSync runtime package`. Those values are enough to tell whether the game loaded the wrong folder, the package is stale, or the dialogue text has no unique match.

## Install and rollback

Use `scripts/install_replaced_probe.ps1` to install the staged official BepInEx IL2CPP x64 loader and the probe into the configured game folder. The installer refuses an existing BepInEx folder or file conflicts and writes a local rollback manifest under `.data`. Run `scripts/record_replaced_probe_generated.ps1` after the first game launch and close; it adds loader-generated interop/config/log files to that manifest. Run `scripts/remove_replaced_probe.ps1` to remove only manifest-listed files; it stops if it sees untracked plugins/files under BepInEx. No original Steam game files are overwritten.

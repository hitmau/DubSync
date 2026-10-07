# REPLACED Articy adapter foundation

`integration/adapters/replaced_articy.py` converts observed `InitLine`, `ReleaseLine`, pause, resume, and scene callbacks to DubSync's game-independent event contract. It accepts `Articy ExternalId` as the trigger and does not substitute `ShortId`, speaker, text, or timestamps.

This is the adapter contract/state machine, not an installed REPLACED mod. The local game folder currently has `Replaced.exe`, `GameAssembly.dll`, and `UnityPlayer.dll`, but no BepInEx, MelonLoader, or Unity Doorstop files. A compatible IL2CPP loader and the live callback signatures must be confirmed before adding an executable hook.

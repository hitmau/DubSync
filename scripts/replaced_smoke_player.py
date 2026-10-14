"""Play a small REPLACED package from real matched callbacks for Phase 3 diagnosis.

The helper is deliberately external to the game. It validates the package audio,
starts at the end of the event log, and plays each mapped take at most once.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def measured_gain(audio: Path, target_peak_db: float) -> float:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is unavailable")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(audio), "-af", "volumedetect", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not measure {audio.name}")
    match = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", result.stderr)
    if not match:
        raise RuntimeError(f"No peak measurement for {audio.name}")
    return round(min(24.0, max(0.0, target_peak_db - float(match.group(1)))), 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_dir", type=Path)
    parser.add_argument("events_file", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--target-peak-db", type=float, default=-6.0)
    args = parser.parse_args()
    if not -18 <= args.target_peak_db <= -3:
        parser.error("--target-peak-db must be between -18 and -3 dBFS")

    package_dir = args.package_dir.resolve(strict=True)
    manifest = json.loads((package_dir / "runtime_manifest.json").read_text(encoding="utf-8"))
    entries = manifest.get("entries", [])
    if not 1 <= len(entries) <= 10 or manifest.get("game", {}).get("project_id") != "replaced":
        parser.error("A smoke package needs 1-10 REPLACED entries")
    ffplay = shutil.which("ffplay")
    if not ffplay:
        parser.error("ffplay is unavailable")

    lookup: dict[tuple[str, str], tuple[dict, Path, float]] = {}
    for entry in entries:
        key = entry["key"]
        trigger = key["runtime_trigger_type"], key["runtime_trigger_id"]
        if trigger[0] not in {"articy_external_id", "articy_object_id"} or trigger in lookup:
            parser.error("The package has an unsupported or duplicate trigger")
        audio = (package_dir / entry["audio"]["path"]).resolve(strict=True)
        if not audio.is_relative_to(package_dir) or not audio.is_file():
            parser.error("The package audio path is invalid")
        if hashlib.sha256(audio.read_bytes()).hexdigest() != entry["audio"]["sha256"]:
            parser.error(f"The audio hash does not match: {audio.name}")
        lookup[trigger] = entry, audio, measured_gain(audio, args.target_peak_db)

    events_file = args.events_file.resolve(strict=True)
    deadline = time.monotonic() + args.timeout
    seen: set[tuple[str, str]] = set()
    current: tuple[tuple[str, str], subprocess.Popen] | None = None
    print(f"READY entries={len(lookup)} package={manifest['package']['package_id']}", flush=True)
    for trigger, (entry, audio, gain) in lookup.items():
        print(f"TAKE trigger={trigger[0]}:{trigger[1]} take={entry['take_id']} audio={audio.name} gain_db={gain:g}", flush=True)

    with events_file.open("r", encoding="utf-8") as stream:
        stream.seek(0, 2)  # Never replay previous gameplay events.
        while time.monotonic() < deadline:
            if current and current[1].poll() is not None:
                print(f"PLAY_FINISHED trigger={current[0][1]} exit={current[1].returncode}", flush=True)
                if current[1].returncode != 0:
                    return 1
                current = None
                if len(seen) == len(lookup):
                    return 0
            line = stream.readline()
            if not line:
                time.sleep(0.05)
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_trigger = event.get("trigger") or {}
            trigger = raw_trigger.get("type"), raw_trigger.get("id")
            if trigger not in lookup:
                continue
            if event.get("event_type") == "dialogue_start" and event.get("resolution") == "matched" and trigger not in seen:
                if current and current[1].poll() is None:
                    current[1].terminate()
                    current[1].wait(timeout=5)
                    print(f"PLAY_INTERRUPTED trigger={current[0][1]}", flush=True)
                entry, audio, gain = lookup[trigger]
                command = [ffplay, "-nodisp", "-autoexit", "-loglevel", "error",
                           "-af", f"volume={gain:g}dB", "-i", str(audio)]
                options = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                player = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=options)
                current = trigger, player
                seen.add(trigger)
                print(f"PLAY_START event={event['event_id']} trigger={trigger[1]} take={entry['take_id']} pid={player.pid}", flush=True)
            elif event.get("event_type") == "dialogue_end" and current and current[0] == trigger:
                if current[1].poll() is None:
                    current[1].terminate()
                    current[1].wait(timeout=5)
                print(f"PLAY_END event={event['event_id']} trigger={trigger[1]} exit={current[1].returncode}", flush=True)
                current = None
                if len(seen) == len(lookup):
                    return 0

    if current and current[1].poll() is None:
        current[1].terminate()
        current[1].wait(timeout=5)
    print(f"TIMEOUT played={len(seen)}/{len(lookup)}", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Project-local sequence of captured blocks across consecutive video files."""

from __future__ import annotations

import os
import math
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def video_identity(session: dict) -> str:
    config = session.get("config_json") or {}
    video_id = str(config.get("video_id") or "").strip()
    url = str(config.get("url") or session.get("source_uri") or "")
    if not video_id and url:
        parsed = urlparse(url)
        video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        if not video_id and parsed.hostname in {"youtu.be", "www.youtu.be"}:
            video_id = parsed.path.strip("/").split("/")[0]
    if video_id:
        return f"youtube:{video_id}"
    path = config.get("downloaded_path") or config.get("path")
    if not path and session.get("source_type") == "file":
        path = session.get("source_uri")
    if path:
        try:
            return "file:" + os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))
        except (OSError, RuntimeError):
            return "file:" + os.path.normcase(os.path.abspath(str(path)))
    return f"session:{session.get('session_id', '')}"


def linked_video_chain(sessions: list[dict], session_id: str) -> list[dict]:
    """Follow explicit parent links; other captures of the same file stay separate."""
    by_id = {item["session_id"]: item for item in sessions}
    current = by_id.get(session_id)
    if current is None:
        return []
    ancestors, seen = [], set()
    while current and current["session_id"] not in seen:
        ancestors.append(current)
        seen.add(current["session_id"])
        current = by_id.get((current.get("config_json") or {}).get("previous_session_id"))
    chain = list(reversed(ancestors))
    current = chain[-1]
    while len(chain) < 5000:
        children = [item for item in sessions
                    if (item.get("config_json") or {}).get("previous_session_id") == current["session_id"]
                    and item["session_id"] not in seen]
        if not children:
            break
        current = max(children, key=lambda item: str(item.get("created_at") or ""))
        chain.append(current)
        seen.add(current["session_id"])
    return chain


def build_video_plan(sessions: list[dict], session_id: str) -> dict:
    chain = linked_video_chain(sessions, session_id)
    sectors: list[dict] = []
    offset = 0.0
    for position, item in enumerate(chain):
        config = item.get("config_json") or {}
        identity = video_identity(item)
        if not sectors or sectors[-1]["identity"] != identity:
            if sectors:
                offset += sectors[-1]["duration"]
            sectors.append({
                "index": len(sectors) + 1, "identity": identity,
                "path": config.get("downloaded_path") or config.get("path") or item.get("source_uri"),
                "duration": float(config.get("video_duration") or 0),
                "offset": offset, "first_session_id": item["session_id"],
                "block_duration_seconds": float(config.get("block_duration_requested") or 0),
                "planned": False,
            })
        else:
            sectors[-1]["duration"] = max(sectors[-1]["duration"], float(config.get("video_duration") or 0))
            sectors[-1]["block_duration_seconds"] = float(config.get("block_duration_requested") or 0) or sectors[-1]["block_duration_seconds"]
        item["project_start"] = offset + float(item.get("source_start") or 0)
        item["sector_index"] = len(sectors)
    if sectors:
        final_sector = sectors[-1]
        root = next(item for item in chain if item["session_id"] == final_sector["first_session_id"])
        pending = (root.get("config_json") or {}).get("planned_next_video") or {}
        if pending.get("path") and float(pending.get("duration") or 0) > 0:
            sectors.append({
                "index": len(sectors) + 1, "identity": "planned:" + str(pending["path"]),
                "path": pending["path"], "duration": float(pending["duration"]),
                "offset": final_sector["offset"] + final_sector["duration"],
                "first_session_id": None,
                "block_duration_seconds": float(pending.get("block_duration_seconds") or 1200),
                "planned": True,
            })
    total_duration = sum(item["duration"] for item in sectors)
    return {"sessions": chain, "sectors": sectors, "video_duration": total_duration}


def estimated_block_total(plan: dict, default_step: float = 1200) -> int:
    """Count captured blocks plus the remaining intervals in each known video."""
    total = 0
    for sector in plan["sectors"]:
        captured = [item for item in plan["sessions"] if item["sector_index"] == sector["index"]]
        total += len(captured)
        if captured:
            last = captured[-1]
            config = last.get("config_json") or {}
            cursor = float(last.get("source_start") or 0) + float(config.get("duration") or last.get("requested_duration") or last.get("duration") or 0)
            step = (float(sector["block_duration_seconds"] or 0)
                    or float(config.get("block_duration_requested") or 0) or default_step)
        else:
            cursor = 0.0
            step = float(sector["block_duration_seconds"] or 0) or default_step
        if sector["duration"] > cursor and step > 0:
            total += math.ceil((sector["duration"] - cursor - 1e-6) / step)
    return total

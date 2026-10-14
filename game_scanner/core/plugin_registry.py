from __future__ import annotations

import json
from pathlib import Path
import re


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_DIR = PROJECT_ROOT / "integration" / "plugins"


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def find_game_plugins(game_name: str) -> list[dict]:
    """Lê metadados declarativos dos plugins existentes, sem carregar seu código."""
    game_key = _key(game_name)
    if not game_key or not PLUGIN_DIR.is_dir():
        return []
    candidates = []
    for manifest_path in PLUGIN_DIR.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        aliases = [_key(item) for item in manifest.get("games", []) if isinstance(item, str)]
        matched = [alias for alias in aliases if alias and (game_key == alias or game_key.startswith(alias))]
        if not matched:
            continue
        candidates.append({
            "plugin_id": manifest.get("plugin_id"),
            "name": manifest.get("name", manifest_path.parent.name),
            "version": manifest.get("version"),
            "engines": manifest.get("engines", []),
            "capabilities": manifest.get("capabilities", []),
            "runtime_callback_observed": bool(manifest.get("runtime_callback_observed", False)),
            "runtime_hook_confirmed": bool(manifest.get("runtime_hook_confirmed", False)),
            "matched_game_alias": matched[0],
            "manifest_path": manifest_path.relative_to(PROJECT_ROOT).as_posix(),
            "source_scope": "manifesto do plugin existente; não é validação do scan atual",
        })
    return sorted(candidates, key=lambda item: (item.get("name") or "").casefold())


from __future__ import annotations

import re
from pathlib import Path, PurePosixPath


DATA_ROOT = Path(__import__("os").environ.get("DUBSYNC_DATA_DIR", "/data")).resolve()


def safe_data_path(relative: str, must_exist: bool = False) -> Path:
    value = str(relative or "").strip()
    if not value or "\\" in value or ":" in value:
        raise ValueError("O caminho deve ser relativo à pasta de dados.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("O caminho contém um segmento inválido.")
    resolved = (DATA_ROOT / Path(*path.parts)).resolve()
    if not resolved.is_relative_to(DATA_ROOT):
        raise ValueError("O caminho sai da raiz de dados.")
    if must_exist and not resolved.is_file():
        raise FileNotFoundError("Arquivo de áudio ausente no volume do DubSync.")
    return resolved


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or ""))
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("Identificador inválido.")
    return cleaned[:80]


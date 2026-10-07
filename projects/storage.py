from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_DIRECTORIES = (
    "sources", "capture", "frames", "crops", "characters", "dubbing/voices",
    "dubbing/audio", "dubbing/temp", "dubbing/exports", "dubbing/logs", "runtime",
)


class ProjectStorage:
    """Central resolver for new project-owned editorial assets."""

    def __init__(self, data_root: Path):
        self.data_root = data_root.resolve()

    def project_root(self, slug: str) -> Path:
        clean = str(slug or "").strip().lower()
        if not clean or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,95}", clean):
            raise ValueError("Slug inválido para resolver o armazenamento do projeto.")
        root = (self.data_root / "projects" / clean).resolve()
        if root.parent != (self.data_root / "projects").resolve():
            raise ValueError("O caminho do projeto saiu da pasta de armazenamento permitida.")
        return root

    def ensure_project(self, project: dict) -> Path:
        root = self.project_root(project["slug"])
        for relative in PROJECT_DIRECTORIES:
            (root / relative).mkdir(parents=True, exist_ok=True)
        self.write_manifest(project)
        return root

    def write_manifest(self, project: dict) -> None:
        root = self.project_root(project["slug"])
        root.mkdir(parents=True, exist_ok=True)
        path = root / "project.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(project, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        temporary.replace(path)

    def project_file(self, slug: str, relative: str) -> Path:
        root = self.project_root(slug)
        candidate = (root / relative).resolve()
        if root not in candidate.parents:
            raise ValueError("O arquivo saiu da pasta de armazenamento do projeto.")
        return candidate

    def capture_session_dir(self, slug: str, session_id: str) -> Path:
        if not re.fullmatch(r"[a-fA-F0-9-]{36}", str(session_id)):
            raise ValueError("Identificador inválido para a sessão de captura.")
        return self.project_file(slug, f"capture/sessions/{session_id}")

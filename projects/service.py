from __future__ import annotations

import base64
import binascii
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from ..database.models import (
    AudioTake, CaptureLink, DialogueLine, DialogueOccurrence, DubbingCharacter,
    DubbingProject, EventRecord, GameBuild, SessionRecord,
)
from ..database.store import DubSyncStore
from .storage import ProjectStorage


PROJECT_STATUSES = {"active", "paused", "completed", "archived"}
NARRATIVE_TYPES = {"linear", "branching", "unknown"}
LANGUAGES = {"en", "pt", "pt-BR", "es", "fr", "de", "ja", "ko", "zh", "it", "ru"}
MAX_COVER_BYTES = 8 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_value)).strip("-")[:96]


def _project_dict(row: DubbingProject) -> dict:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class ProjectError(ValueError):
    pass


class ProjectService:
    def __init__(self, store: DubSyncStore, data_root: Path):
        self.store = store
        self.storage = ProjectStorage(data_root)

    def list_projects(self, status: str = "all", query: str = "", sort: str = "activity") -> list[dict]:
        with self.store.Session() as db:
            statement = select(DubbingProject)
            if status != "all":
                statement = statement.where(DubbingProject.status == status)
            text_query = query.strip().casefold()
            rows = db.scalars(statement).all()
            items = [self._summary(db, row) for row in rows
                     if not text_query or text_query in " ".join((row.name, row.game_title, row.slug)).casefold()]
        if sort == "name":
            items.sort(key=lambda item: item["name"].casefold())
        elif sort == "created":
            items.sort(key=lambda item: item["created_at"] or "", reverse=True)
        elif sort == "progress":
            items.sort(key=lambda item: item["summary"]["progress_percent"], reverse=True)
        else:
            items.sort(key=lambda item: item["summary"]["last_activity"] or "", reverse=True)
        return items

    def get_project(self, project_id: str) -> dict | None:
        with self.store.Session() as db:
            row = db.get(DubbingProject, project_id)
            return self._summary(db, row) if row else None

    def open_project(self, project_id: str) -> dict:
        with self.store.Session.begin() as db:
            row = db.get(DubbingProject, project_id)
            if not row:
                raise KeyError(project_id)
            if row.status == "archived":
                raise ProjectError("Restaure este projeto antes de abri-lo.")
            row.last_opened_at = _now()
            project = _project_dict(row)
        self.storage.ensure_project(project)
        return self.get_project(project_id) or project

    def create_project(self, body: dict) -> dict:
        name = " ".join(str(body.get("name") or "").split())
        game_title = " ".join(str(body.get("game_title") or "").split())
        if not name or not game_title:
            raise ProjectError("Preencha o nome do projeto e o título do jogo.")
        slug = _slug(str(body.get("slug") or name))
        if not slug:
            raise ProjectError("O slug precisa ter pelo menos uma letra ou número.")
        narrative = str(body.get("narrative_type") or "unknown")
        if narrative not in NARRATIVE_TYPES:
            raise ProjectError("Escolha narrativa linear, branching ou unknown.")
        original = str(body.get("original_language") or "en")
        capture = str(body.get("capture_language") or original)
        dubbing = str(body.get("dub_language") or "pt-BR")
        if any(len(value) > 16 or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", value)
               for value in (original, capture, dubbing)):
            raise ProjectError("Use códigos de idioma como en, pt-BR ou es-MX.")
        now, project_id = _now(), str(uuid.uuid4())
        with self.store.Session.begin() as db:
            if db.scalar(select(DubbingProject.project_id).where(DubbingProject.slug == slug)):
                raise ProjectError("Este slug já está em uso. Escolha outro.")
            cover_path = self._save_cover(slug, body.get("cover_data"))
            row = DubbingProject(
                project_id=project_id, name=name[:160], slug=slug, game_title=game_title[:160],
                game_version=str(body.get("game_version") or "").strip()[:80] or None,
                source_language=capture[:16], original_language=original[:16], capture_language=capture[:16],
                dub_language=dubbing[:16], platform=str(body.get("platform") or "").strip()[:80],
                narrative_type=narrative, status="active", cover_path=cover_path,
                notes=str(body.get("notes") or "").strip()[:8000], created_at=now, updated_at=now,
                last_opened_at=None,
            )
            db.add(row)
            version = str(body.get("game_version") or "").strip()[:80]
            platform = str(body.get("platform") or "").strip()[:80]
            if version or platform:
                db.add(GameBuild(game_build_id=f"BLD_{uuid.uuid4().hex[:16].upper()}",
                                 project_id=project_id, version=version, platform=platform,
                                 notes="", is_active=True, created_at=now))
            project = _project_dict(row)
        self.storage.ensure_project(project)
        return self.get_project(project_id) or project

    def update_project(self, project_id: str, changes: dict) -> dict:
        allowed = {"name", "game_title", "game_version", "platform", "narrative_type", "original_language",
                   "capture_language", "dub_language", "notes"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if "narrative_type" in values and values["narrative_type"] not in NARRATIVE_TYPES:
            raise ProjectError("Escolha narrativa linear, branching ou unknown.")
        with self.store.Session.begin() as db:
            row = db.get(DubbingProject, project_id)
            if not row:
                raise KeyError(project_id)
            for key, value in values.items():
                if key in {"name", "game_title"} and not str(value or "").strip():
                    raise ProjectError("Nome do projeto e título do jogo não podem ficar vazios.")
                if key == "capture_language":
                    row.source_language = str(value)[:16]
                setattr(row, key, str(value).strip() if value is not None else "")
            if "cover_data" in changes:
                row.cover_path = self._save_cover(row.slug, changes.get("cover_data"))
            row.updated_at = _now()
            project = _project_dict(row)
        self.storage.write_manifest(project)
        return self.get_project(project_id) or project

    def set_status(self, project_id: str, status: str) -> dict:
        if status not in PROJECT_STATUSES:
            raise ProjectError("Status de projeto inválido.")
        with self.store.Session.begin() as db:
            row = db.get(DubbingProject, project_id)
            if not row:
                raise KeyError(project_id)
            if status == "archived":
                running = db.scalar(select(SessionRecord.session_id).where(
                    SessionRecord.project_id == project_id,
                    SessionRecord.state.in_(("queued", "downloading", "running", "processing", "paused", "stopping")),
                ).limit(1))
                if running:
                    raise ProjectError("Pause ou encerre a captura deste projeto antes de arquivá-lo.")
            row.status = status
            row.updated_at = _now()
            project = _project_dict(row)
        self.storage.write_manifest(project)
        return self.get_project(project_id) or project

    def _save_cover(self, slug: str, data_url: object) -> str | None:
        if not data_url:
            return None
        value = str(data_url)
        match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", value)
        if not match:
            raise ProjectError("A capa precisa ser PNG, JPG ou WebP.")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ProjectError("Não foi possível ler a imagem da capa.") from error
        if not raw or len(raw) > MAX_COVER_BYTES:
            raise ProjectError("A capa deve ter até 8 MB.")
        mime = match.group(1)
        signatures = {"png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
                      "jpeg": raw.startswith(b"\xff\xd8\xff"),
                      "webp": len(raw) > 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"}
        if not signatures[mime]:
            raise ProjectError("O conteúdo não corresponde ao formato declarado da capa.")
        suffix = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}[mime]
        path = self.storage.project_file(slug, f"assets/cover{suffix}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path.relative_to(self.storage.project_root(slug)).as_posix()

    def _summary(self, db, row: DubbingProject) -> dict:
        if not row:
            return {}
        project_id = row.project_id
        sessions = db.scalars(select(SessionRecord).where(SessionRecord.project_id == project_id)).all()
        session_ids = [session.session_id for session in sessions]
        event_count = db.scalar(select(func.count(EventRecord.event_id)).join(
            SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                SessionRecord.project_id == project_id, EventRecord.review_status != "merged")) or 0
        reviewed_count = db.scalar(select(func.count(EventRecord.event_id)).join(
            SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                SessionRecord.project_id == project_id,
                EventRecord.review_status.in_(("accepted", "corrected")))) or 0
        resolved_count = db.scalar(select(func.count(EventRecord.event_id)).join(
            SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                SessionRecord.project_id == project_id,
                EventRecord.review_status.in_(("accepted", "corrected", "discarded")))) or 0
        character_count = db.scalar(select(func.count(DubbingCharacter.character_id)).where(
            DubbingCharacter.project_id == project_id, DubbingCharacter.active.is_(True))) or 0
        take_count = db.scalar(select(func.count(AudioTake.take_id)).join(
            DialogueLine, DialogueLine.line_id == AudioTake.line_id).where(
                DialogueLine.project_id == project_id)) or 0
        approved_takes = db.scalar(select(func.count(AudioTake.take_id)).join(
            DialogueLine, DialogueLine.line_id == AudioTake.line_id).where(
                DialogueLine.project_id == project_id, AudioTake.status == "approved")) or 0
        analyzed_seconds = self._covered_seconds(sessions)
        total_lines = db.scalar(select(func.count(DialogueLine.line_id)).where(
            DialogueLine.project_id == project_id)) or 0
        approved_lines = db.scalar(select(func.count(func.distinct(AudioTake.line_id))).join(
            DialogueLine, DialogueLine.line_id == AudioTake.line_id).where(
                DialogueLine.project_id == project_id, AudioTake.status == "approved")) or 0
        capture_progress = round(
            sum(session.state == "complete" for session in sessions) * 100 / len(sessions), 1
        ) if sessions else 0.0
        review_progress = round(resolved_count * 100 / event_count, 1) if event_count else 0.0
        dubbing_progress = round(approved_lines * 100 / total_lines, 1) if total_lines else 0.0
        progress = round((capture_progress + review_progress + dubbing_progress) / 3, 1)
        active_capture_states = {"preparing", "queued", "downloading", "running", "paused", "processing", "stopping"}
        if any(session.state in active_capture_states for session in sessions) or not event_count:
            current_stage = "capture"
        elif resolved_count < event_count:
            current_stage = "review"
        elif not total_lines or approved_lines < total_lines:
            current_stage = "dubbing"
        else:
            current_stage = "complete"
        stage_progress = {
            "capture": capture_progress, "review": review_progress,
            "dubbing": dubbing_progress, "complete": 100.0,
        }[current_stage]
        activity = max((stamp for stamp in (
            row.last_opened_at or row.created_at,
            *(session.created_at for session in sessions),
        ) if stamp), default=row.updated_at)
        project = _project_dict(row)
        for key in ("created_at", "updated_at", "last_opened_at"):
            if project.get(key):
                project[key] = project[key].isoformat()
        project["summary"] = {
            "sessions": len(session_ids), "dialogues": int(event_count), "reviewed": int(reviewed_count),
            "characters": int(character_count), "takes": int(take_count), "approved_takes": int(approved_takes),
            "capture_progress_percent": capture_progress,
            "review_progress_percent": round(review_progress, 1),
            "resolved_dialogues": int(resolved_count),
            "dubbing_progress_percent": dubbing_progress,
            "progress_percent": progress,
            "current_stage": current_stage,
            "stage_progress_percent": stage_progress,
            "lines": int(total_lines), "analyzed_seconds": analyzed_seconds,
            "last_activity": activity.isoformat() if activity else None,
        }
        return project

    @staticmethod
    def _covered_seconds(sessions: list[SessionRecord]) -> int:
        intervals: dict[str, list[tuple[float, float]]] = {}
        for item in sessions:
            if item.state != "complete":
                continue
            uri = item.source_uri or item.session_id
            start = max(0.0, float(item.source_start or 0))
            duration = max(0.0, float(item.requested_duration or 0))
            if duration:
                intervals.setdefault(uri, []).append((start, start + duration))
        total = 0.0
        for ranges in intervals.values():
            ranges.sort()
            begin = end = None
            for start, stop in ranges:
                if begin is None:
                    begin, end = start, stop
                elif start <= end:
                    end = max(end, stop)
                else:
                    total += end - begin
                    begin, end = start, stop
            if begin is not None:
                total += end - begin
        return round(total)

from __future__ import annotations

import uuid
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import and_, case, create_engine, delete, func, inspect, or_, select, text
from sqlalchemy.orm import sessionmaker
from rapidfuzz.fuzz import ratio, token_set_ratio

from ..events.dedup import equivalent_observation
from ..events.normalize import normalize_text
from ..events.replacements import clean_replacement_rules
from ..projects.context import scoped_project_id
from .models import (
    AudioTake, AudioTakeReview, Base, CaptureLink, DialogueLine, DialogueOccurrence,
    CaptureTraversal, DubbingCharacter, DubbingProject, EventRecord, FineSweepRun,
    ProjectTextReplacementSettings, ReviewRecord, RoiCharacterRecord, RuntimeTestItem,
    SessionRecord, TimelineMarker, TtsJob,
)


ROI_CHARACTER_COLORS = (
    "#E8596E", "#F08C46", "#E8B93B", "#78C85A", "#27B985", "#29B9AE",
    "#32A9D4", "#4B8EE8", "#706BE8", "#9B68E8", "#C65BCB", "#E05BA4",
    "#FF725E", "#D3A12C", "#62BD80", "#55AFD0", "#788CE8", "#AC78D2",
    "#DE7692", "#99B440", "#26A5A1", "#E5842B", "#5875BD", "#BB5F67",
)


def _next_roi_character_color(used: set[str]) -> str:
    normalized = {str(color or "").upper() for color in used}
    available = [color for color in ROI_CHARACTER_COLORS if color not in normalized]
    if available:
        return secrets.choice(available)
    while True:
        color = f"#{secrets.randbelow(0x1000000):06X}"
        if color not in normalized:
            return color


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DubSyncStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False})
        self.Session = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._migrate_schema()
        self.sessions_root = path.parent / "sessions"

    def _migrate_schema(self) -> None:
        columns = {column["name"] for column in inspect(self.engine).get_columns("dialogue_events")}
        character_columns = {column["name"] for column in inspect(self.engine).get_columns("roi_characters")}
        with self.engine.begin() as connection:
            if "alias" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN alias VARCHAR(120) NOT NULL DEFAULT ''"))
            if "color" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN color VARCHAR(7) NOT NULL DEFAULT ''"))
            if "processed_text" not in columns:
                connection.execute(text("ALTER TABLE dialogue_events ADD COLUMN processed_text TEXT"))
            if "character_profile_id" not in columns:
                connection.execute(text("ALTER TABLE dialogue_events ADD COLUMN character_profile_id VARCHAR(36)"))
            if "region_id" not in columns:
                connection.execute(text("ALTER TABLE dialogue_events ADD COLUMN region_id VARCHAR(96)"))
            if "sample_mp3_path" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN sample_mp3_path TEXT"))
            if "sample_mp3_name" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN sample_mp3_name VARCHAR(260)"))
            if "sample_mp3_bytes" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN sample_mp3_bytes INTEGER NOT NULL DEFAULT 0"))
            if "photo_path" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN photo_path TEXT"))
            if "photo_name" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN photo_name VARCHAR(260)"))
            if "photo_mime" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN photo_mime VARCHAR(80)"))
            if "photo_bytes" not in character_columns:
                connection.execute(text("ALTER TABLE roi_characters ADD COLUMN photo_bytes INTEGER NOT NULL DEFAULT 0"))
            character_rows = connection.execute(text(
                "SELECT character_id, project_id, color FROM roi_characters ORDER BY project_id, created_at, name"
            )).mappings().all()
            used_colors: dict[str, set[str]] = {}
            for character in character_rows:
                project_id = str(character["project_id"] or "replaced")
                color = str(character["color"] or "").upper()
                if re.fullmatch(r"#[0-9A-F]{6}", color):
                    used_colors.setdefault(project_id, set()).add(color)
            for character in character_rows:
                project_id = str(character["project_id"] or "replaced")
                color = str(character["color"] or "").upper()
                if not re.fullmatch(r"#[0-9A-F]{6}", color):
                    project_colors = used_colors.setdefault(project_id, set())
                    color = _next_roi_character_color(project_colors)
                    project_colors.add(color)
                    connection.execute(text("UPDATE roi_characters SET color=:color WHERE character_id=:character_id"),
                                       {"color": color, "character_id": character["character_id"]})
            connection.execute(text("UPDATE fine_sweep_runs SET state='interrupted', "
                                    "error='Servidor reiniciado durante o pente fino.' "
                                    "WHERE state='running'"))
            current_version = int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())
            if current_version > 9:
                raise RuntimeError(f"Schema DubSync v{current_version} é mais novo que esta aplicação (v9).")
            if current_version < 2:
                tables = set(inspect(self.engine).get_table_names())
                required = {"capture_sessions", "dialogue_events", "review_history", "roi_characters",
                            "timeline_markers", "fine_sweep_runs"}
                if not required.issubset(tables):
                    missing = ", ".join(sorted(required - tables))
                    raise RuntimeError(f"Schema legado incompleto; tabelas ausentes: {missing}.")
                applied = {int(value) for value in connection.execute(
                    text("SELECT version FROM schema_migrations")).scalars().all()}
                now = utc_now()
                if 1 not in applied:
                    connection.execute(text(
                        "INSERT INTO schema_migrations(version, name, applied_at) "
                        "VALUES (1, 'fase1_legacy_baseline', :applied_at)"),
                        {"applied_at": now})
                if 2 not in applied:
                    connection.execute(text(
                        "INSERT INTO schema_migrations(version, name, applied_at) "
                        "VALUES (2, 'fase2_dubbing_additive', :applied_at)"),
                        {"applied_at": now})
                connection.exec_driver_sql("PRAGMA user_version=2")
                current_version = 2
            if current_version < 3:
                columns_by_table = {
                    table: {column["name"] for column in inspect(self.engine).get_columns(table)}
                    for table in ("characters", "voice_profiles", "audio_takes")
                }
                additions = {
                    "characters": {"default_voice_profile_id": "VARCHAR(48)"},
                    "voice_profiles": {
                        "preset_key": "VARCHAR(160)",
                        "generation_settings_json": "JSON NOT NULL DEFAULT '{}'",
                        "active": "BOOLEAN NOT NULL DEFAULT 1",
                    },
                    "audio_takes": {"generation_settings_json": "JSON NOT NULL DEFAULT '{}'"},
                }
                for table, fields in additions.items():
                    for name, definition in fields.items():
                        if name not in columns_by_table[table]:
                            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations(version, name, applied_at) "
                    "VALUES (3, 'omnivoice_local_profiles', :applied_at)"),
                    {"applied_at": utc_now()})
                connection.exec_driver_sql("PRAGMA user_version=3")
                current_version = 3
            if current_version < 4:
                additions = {
                    "capture_sessions": {"project_id": "VARCHAR(64) NOT NULL DEFAULT 'replaced'"},
                    "roi_characters": {"project_id": "VARCHAR(64) NOT NULL DEFAULT 'replaced'"},
                    "projects": {
                        "original_language": "VARCHAR(16) NOT NULL DEFAULT 'und'",
                        "capture_language": "VARCHAR(16) NOT NULL DEFAULT 'pt'",
                        "platform": "VARCHAR(80) NOT NULL DEFAULT ''",
                        "narrative_type": "VARCHAR(24) NOT NULL DEFAULT 'unknown'",
                        "status": "VARCHAR(24) NOT NULL DEFAULT 'active'",
                        "cover_path": "TEXT",
                        "notes": "TEXT NOT NULL DEFAULT ''",
                        "last_opened_at": "DATETIME",
                    },
                }
                for table, fields in additions.items():
                    existing = {column["name"] for column in inspect(self.engine).get_columns(table)}
                    for name, definition in fields.items():
                        if name not in existing:
                            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
                connection.execute(text(
                    "UPDATE projects SET capture_language=source_language "
                    "WHERE capture_language IS NULL OR capture_language=''"))
                connection.execute(text(
                    "INSERT OR IGNORE INTO projects "
                    "(project_id,name,slug,game_title,game_version,source_language,dub_language,created_at,updated_at,"
                    "original_language,capture_language,platform,narrative_type,status,cover_path,notes,last_opened_at) "
                    "VALUES ('replaced','REPLACED','replaced','REPLACED',NULL,'pt','pt-BR',:now,:now,'und','pt','','unknown',"
                    "'active',NULL,'',NULL)"), {"now": utc_now()})
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_capture_sessions_project_id ON capture_sessions(project_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_roi_characters_project_id ON roi_characters(project_id)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_status ON projects(status)"))
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) "
                    "VALUES (4,'fase0_project_hub',:applied_at)"), {"applied_at": utc_now()})
                connection.exec_driver_sql("PRAGMA user_version=4")
                current_version = 4
            if current_version < 5:
                job_columns = {column["name"] for column in inspect(self.engine).get_columns("tts_jobs")}
                if "batch_id" not in job_columns:
                    connection.execute(text("ALTER TABLE tts_jobs ADD COLUMN batch_id VARCHAR(48)"))
                if "batch_mode" not in job_columns:
                    connection.execute(text("ALTER TABLE tts_jobs ADD COLUMN batch_mode VARCHAR(20) NOT NULL DEFAULT 'individual'"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_tts_jobs_batch_id ON tts_jobs(batch_id)"))
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) "
                    "VALUES (5,'dubbing_batch_tracking',:applied_at)"), {"applied_at": utc_now()})
                connection.exec_driver_sql("PRAGMA user_version=5")
                current_version = 5
            if current_version < 6:
                columns = {column["name"] for column in inspect(self.engine).get_columns("voice_profiles")}
                if "preview_text" not in columns:
                    connection.execute(text("ALTER TABLE voice_profiles ADD COLUMN preview_text TEXT NOT NULL DEFAULT ''"))
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) "
                    "VALUES (6,'voice_profile_preview_text',:applied_at)"), {"applied_at": utc_now()})
                connection.exec_driver_sql("PRAGMA user_version=6")
                current_version = 6
            if current_version < 7:
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) "
                    "VALUES (7,'fase3_game_plugin_runtime_foundation',:applied_at)"), {"applied_at": utc_now()})
                connection.exec_driver_sql("PRAGMA user_version=7")
                current_version = 7
            if current_version < 8:
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) "
                    "VALUES (8,'persistent_voice_design_candidates',:applied_at)"), {"applied_at": utc_now()})
                connection.exec_driver_sql("PRAGMA user_version=8")
                current_version = 8
            if current_version < 9:
                profile_columns = {column["name"] for column in inspect(self.engine).get_columns("voice_profiles")}
                if "default_direction" not in profile_columns:
                    connection.execute(text(
                        "ALTER TABLE voice_profiles ADD COLUMN default_direction TEXT NOT NULL DEFAULT ''"))
                connection.execute(text(
                    "INSERT OR IGNORE INTO schema_migrations(version,name,applied_at) "
                    "VALUES (9,'voice_profile_default_direction',:applied_at)"), {"applied_at": utc_now()})
                connection.exec_driver_sql("PRAGMA user_version=9")

    def create_fine_sweep(self, session_id: str) -> dict:
        with self.Session.begin() as db:
            if db.get(SessionRecord, session_id) is None:
                raise KeyError(session_id)
            previous = db.scalar(select(FineSweepRun).where(
                FineSweepRun.session_id == session_id,
                FineSweepRun.state == "running").order_by(FineSweepRun.created_at.desc()))
            if previous:
                raise ValueError("Já existe um pente fino em andamento para este bloco.")
            row = FineSweepRun(run_id=str(uuid.uuid4()), session_id=session_id,
                               state="running", progress_percent=0, checked_frames=0,
                               ocr_calls=0, findings_json=[], created_at=utc_now())
            db.add(row)
            return {column.name: getattr(row, column.name) for column in FineSweepRun.__table__.columns}

    def update_fine_sweep(self, run_id: str, **changes) -> dict:
        with self.Session.begin() as db:
            row = db.get(FineSweepRun, run_id)
            if row is None:
                raise KeyError(run_id)
            for key in ("state", "progress_percent", "current_source_time", "checked_frames",
                        "ocr_calls", "findings_json", "error", "completed_at"):
                if key in changes:
                    setattr(row, key, changes[key])
            return {column.name: getattr(row, column.name) for column in FineSweepRun.__table__.columns}

    def latest_fine_sweep(self, session_id: str) -> dict | None:
        with self.Session() as db:
            row = db.scalar(select(FineSweepRun).where(FineSweepRun.session_id == session_id)
                            .order_by(FineSweepRun.created_at.desc()).limit(1))
            return ({column.name: getattr(row, column.name) for column in FineSweepRun.__table__.columns}
                    if row else None)

    def roi_characters(self, roi_id: str, active_only: bool = True) -> list[dict]:
        with self.Session() as db:
            statement = select(RoiCharacterRecord).where(RoiCharacterRecord.roi_id == roi_id)
            project_id = scoped_project_id()
            if project_id:
                statement = statement.where(RoiCharacterRecord.project_id == project_id)
            if active_only:
                statement = statement.where(RoiCharacterRecord.active.is_(True))
            rows = db.scalars(statement.order_by(RoiCharacterRecord.created_at, RoiCharacterRecord.name)).all()
            return [{column.name: getattr(row, column.name) for column in RoiCharacterRecord.__table__.columns}
                    for row in rows]

    def roi_character_colors(self) -> list[dict]:
        """Return only identity/color fields for active characters in this project."""
        with self.Session() as db:
            statement = select(RoiCharacterRecord).where(RoiCharacterRecord.active.is_(True))
            project_id = scoped_project_id()
            if project_id:
                statement = statement.where(RoiCharacterRecord.project_id == project_id)
            rows = db.scalars(statement.order_by(RoiCharacterRecord.created_at, RoiCharacterRecord.name)).all()
            return [{key: getattr(row, key) for key in ("character_id", "project_id", "roi_id", "name", "alias", "color")}
                    for row in rows]

    def ensure_roi_character(self, roi_id: str, name: str, notes: str) -> dict:
        """Return an existing normalized name or create a speaker OCR profile once."""
        clean_name = " ".join(str(name or "").split())[:120]
        if not roi_id or not normalize_text(clean_name):
            raise ValueError("Informe a ROI e o nome detectado para criar o personagem.")
        now = utc_now()
        with self.Session.begin() as db:
            row = self._ensure_roi_character_record(db, roi_id, clean_name, notes, now)
            return {column.name: getattr(row, column.name)
                    for column in RoiCharacterRecord.__table__.columns}

    @staticmethod
    def _ensure_roi_character_record(db, roi_id: str, name: str, notes: str,
                                     now: datetime | None = None,
                                     project_id: str | None = None) -> RoiCharacterRecord:
        clean_name = " ".join(str(name or "").split())[:120]
        normalized = normalize_text(clean_name)
        if not roi_id or not normalized:
            raise ValueError("Informe a ROI e o nome detectado para criar o personagem.")
        timestamp = now or utc_now()
        owning_project = project_id or scoped_project_id() or "replaced"
        profiles = db.scalars(select(RoiCharacterRecord).where(
            RoiCharacterRecord.roi_id == roi_id,
            RoiCharacterRecord.project_id == owning_project,
        )).all()
        matched = None
        for profile in profiles:
            existing_names = [normalize_text(profile.name), normalize_text(profile.alias or "")]
            scores = [max(ratio(normalized, existing_name), token_set_ratio(normalized, existing_name))
                      for existing_name in existing_names if existing_name]
            if scores and max(scores) >= 82:
                matched = profile
                break
        if matched:
            if not matched.active:
                matched.active = True
                matched.updated_at = timestamp
            return matched
        used_colors = set(db.scalars(select(RoiCharacterRecord.color).where(
            RoiCharacterRecord.project_id == owning_project,
        )).all())
        row = RoiCharacterRecord(
            character_id=str(uuid.uuid4()), project_id=owning_project,
            roi_id=roi_id, name=clean_name,
            color=_next_roi_character_color(used_colors), notes=str(notes or "")[:2000],
            active=True, created_at=timestamp, updated_at=timestamp,
        )
        db.add(row)
        db.flush()
        return row

    @staticmethod
    def _character_field_note(event: EventRecord) -> str:
        return ("Encontrado no campo Personagem (opcional) do "
                f"evento #{event.sequence}, em {float(event.start_time):.3f} s. "
                "Confira se o valor corresponde ao nome do personagem. "
                "  MP3 ou WAV de referência pode ser anexado manualmente depois.")

    @staticmethod
    def _clean_character_name(value: object) -> str:
        return " ".join(str(value or "").split())[:120]

    @classmethod
    def _is_character_name(cls, value: object) -> bool:
        normalized = normalize_text(cls._clean_character_name(value))
        return bool(normalized) and normalized not in {
            "desconhecido", "desconhecida", "unknown", "none", "null", "n a",
        }

    @staticmethod
    def _single_session_roi_id(db, session_id: str) -> str | None:
        session = db.get(SessionRecord, session_id)
        regions = (session.config_json or {}).get("regions", []) if session else []
        roi_ids = {str(item.get("roi_id") or "").strip()
                   for item in regions if isinstance(item, dict) and str(item.get("roi_id") or "").strip()}
        return next(iter(roi_ids)) if len(roi_ids) == 1 else None

    def sync_roi_characters_from_events(self, roi_id: str) -> dict:
        """Create missing ROI profiles from names already shown in event review fields."""
        if not roi_id:
            raise ValueError("Informe uma ROI para sincronizar os personagens.")
        now = utc_now()
        created = 0
        with self.Session.begin() as db:
            existing_profile_ids = set(db.scalars(select(RoiCharacterRecord.character_id).where(
                RoiCharacterRecord.roi_id == roi_id,
                RoiCharacterRecord.project_id == (scoped_project_id() or "replaced"),
            )).all())
            events = db.scalars(select(EventRecord).join(
                SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                SessionRecord.project_id == (scoped_project_id() or "replaced"),
                or_(EventRecord.region_id == roi_id, EventRecord.region_id.is_(None)),
                EventRecord.review_status != "merged",
                EventRecord.character.is_not(None),
            ).order_by(EventRecord.start_time, EventRecord.sequence)).all()
            for event in events:
                event_roi_id = event.region_id or self._single_session_roi_id(db, event.session_id)
                if event_roi_id != roi_id:
                    continue
                character_name = self._clean_character_name(event.character)
                if not self._is_character_name(character_name):
                    continue
                profile = self._ensure_roi_character_record(
                    db, roi_id, character_name, self._character_field_note(event), now,
                )
                effective_name = profile.alias or profile.name
                if profile.character_id not in existing_profile_ids:
                    created += 1
                    existing_profile_ids.add(profile.character_id)
                if (event.region_id != roi_id or event.character_profile_id != profile.character_id
                        or event.character != effective_name):
                    event.region_id = roi_id
                    event.character_profile_id = profile.character_id
                    event.character = effective_name
                    event.updated_at = now
            characters = db.scalars(select(RoiCharacterRecord).where(
                RoiCharacterRecord.roi_id == roi_id,
                RoiCharacterRecord.project_id == (scoped_project_id() or "replaced"),
                RoiCharacterRecord.active.is_(True),
            ).order_by(RoiCharacterRecord.created_at, RoiCharacterRecord.name)).all()
            items = [{column.name: getattr(row, column.name)
                      for column in RoiCharacterRecord.__table__.columns} for row in characters]
        return {"created": created, "characters": items}

    def save_roi_characters(self, roi_id: str, characters: list[dict]) -> list[dict]:
        now = utc_now()
        ids: set[str] = set()
        with self.Session.begin() as db:
            for item in characters:
                character_id = str(item["character_id"])
                ids.add(character_id)
                row = db.get(RoiCharacterRecord, character_id)
                project_id = scoped_project_id() or "replaced"
                if row and (row.roi_id != roi_id or row.project_id != project_id):
                    raise ValueError("Este personagem pertence a outra ROI.")
                if row is None:
                    requested_color = str(item.get("color") or "").strip().upper()
                    if requested_color and not re.fullmatch(r"#[0-9A-F]{6}", requested_color):
                        raise ValueError("A cor do personagem deve ser um hexadecimal como #58A6FF.")
                    used_colors = set(db.scalars(select(RoiCharacterRecord.color).where(
                        RoiCharacterRecord.project_id == project_id,
                    )).all())
                    row = RoiCharacterRecord(character_id=character_id, project_id=project_id, roi_id=roi_id,
                                             name=str(item["name"])[:120],
                                             alias=str(item.get("alias", "") or "").strip()[:120],
                                             color=requested_color or _next_roi_character_color(used_colors),
                                             notes=str(item.get("notes", ""))[:2000],
                                             active=True, created_at=now, updated_at=now)
                    db.add(row)
                    previous_name, previous_alias = "", ""
                else:
                    requested_color = str(item.get("color") or "").strip().upper()
                    if requested_color and not re.fullmatch(r"#[0-9A-F]{6}", requested_color):
                        raise ValueError("A cor do personagem deve ser um hexadecimal como #58A6FF.")
                    previous_name, previous_alias = row.name, row.alias or ""
                    row.name = str(item["name"])[:120]
                    row.alias = str(item.get("alias", "") or "").strip()[:120]
                    if requested_color:
                        row.color = requested_color
                    row.notes = str(item.get("notes", ""))[:2000]
                    row.active = True
                    row.updated_at = now
                    effective_name = row.alias or row.name
                    linked_events = db.scalars(select(EventRecord).join(
                        SessionRecord, SessionRecord.session_id == EventRecord.session_id,
                    ).where(
                        SessionRecord.project_id == project_id,
                        EventRecord.character_profile_id == row.character_id,
                    )).all()
                    for event in linked_events:
                        if event.character != effective_name:
                            event.character = effective_name
                            event.updated_at = now
                    dubbing_character = db.scalar(select(DubbingCharacter).where(
                        DubbingCharacter.project_id == project_id,
                        DubbingCharacter.legacy_character_profile_id == row.character_id,
                    ))
                    if dubbing_character:
                        dubbing_character.name = row.name[:120]
                        dubbing_character.display_name = effective_name[:120]
                        dubbing_character.active = True
                        dubbing_character.updated_at = now
            prior = db.scalars(select(RoiCharacterRecord).where(
                RoiCharacterRecord.roi_id == roi_id,
                RoiCharacterRecord.project_id == (scoped_project_id() or "replaced"),
            )).all()
            for row in prior:
                if row.character_id not in ids and row.active:
                    row.active = False
                    row.updated_at = now
                    dubbing_character = db.scalar(select(DubbingCharacter).where(
                        DubbingCharacter.project_id == row.project_id,
                        DubbingCharacter.legacy_character_profile_id == row.character_id,
                    ))
                    if dubbing_character:
                        dubbing_character.active = False
                        dubbing_character.updated_at = now
        return self.roi_characters(roi_id)

    def update_character_sample(self, character_id: str, path: str, filename: str, size: int) -> dict:
        with self.Session.begin() as db:
            row = db.get(RoiCharacterRecord, character_id)
            if not row or (scoped_project_id() and row.project_id != scoped_project_id()):
                raise KeyError(character_id)
            row.sample_mp4_path = path
            row.sample_mp4_name = filename[:260]
            row.sample_mp4_bytes = int(size)
            row.updated_at = utc_now()
            return {column.name: getattr(row, column.name) for column in RoiCharacterRecord.__table__.columns}

    def update_character_mp3(self, character_id: str, path: str, filename: str, size: int) -> dict:
        with self.Session.begin() as db:
            row = db.get(RoiCharacterRecord, character_id)
            if not row or (scoped_project_id() and row.project_id != scoped_project_id()):
                raise KeyError(character_id)
            row.sample_mp3_path = path
            row.sample_mp3_name = filename[:260]
            row.sample_mp3_bytes = int(size)
            row.updated_at = utc_now()
            return {column.name: getattr(row, column.name) for column in RoiCharacterRecord.__table__.columns}

    def update_character_photo(self, character_id: str, path: str, filename: str,
                               mime_type: str, size: int) -> dict:
        with self.Session.begin() as db:
            row = db.get(RoiCharacterRecord, character_id)
            if not row or (scoped_project_id() and row.project_id != scoped_project_id()):
                raise KeyError(character_id)
            row.photo_path = path
            row.photo_name = filename[:260]
            row.photo_mime = mime_type[:80]
            row.photo_bytes = int(size)
            row.updated_at = utc_now()
            return {column.name: getattr(row, column.name) for column in RoiCharacterRecord.__table__.columns}

    def roi_character(self, character_id: str) -> dict | None:
        with self.Session() as db:
            row = db.get(RoiCharacterRecord, character_id)
            if row and scoped_project_id() and row.project_id != scoped_project_id():
                row = None
            return ({column.name: getattr(row, column.name) for column in RoiCharacterRecord.__table__.columns}
                    if row else None)

    def create_session(self, session_id: str, source_type: str, source_uri: str, source_start: float,
                       duration: float, language: str, config: dict, state: str = "preparing") -> None:
        project_id = scoped_project_id() or "replaced"
        with self.Session.begin() as db:
            db.add(SessionRecord(session_id=session_id, source_type=source_type, source_uri=source_uri,
                                 project_id=project_id,
                                 source_start=source_start, requested_duration=duration, language=language,
                                 state=state, config_json=config, created_at=utc_now()))

    def set_session_state(self, session_id: str, state: str, error: str | None = None) -> None:
        with self.Session.begin() as db:
            row = db.get(SessionRecord, session_id)
            if row:
                row.state = state
                row.error = error
                if state in {"complete", "failed", "stopped"}:
                    row.completed_at = utc_now()
                else:
                    row.completed_at = None

    def update_session_config(self, session_id: str, config: dict) -> None:
        with self.Session.begin() as db:
            row = db.get(SessionRecord, session_id)
            if row:
                row.config_json = config

    def extend_session_duration(self, session_id: str, duration: float) -> None:
        """Grow an open-ended live session timeline without changing earlier capture times."""
        with self.Session.begin() as db:
            row = db.get(SessionRecord, session_id)
            if row and float(duration) > float(row.requested_duration or 0):
                row.requested_duration = float(duration)
                config = dict(row.config_json or {})
                config["duration"] = float(duration)
                row.config_json = config

    def session(self, session_id: str) -> dict | None:
        with self.Session() as db:
            statement = select(SessionRecord).where(SessionRecord.session_id == session_id)
            project_id = scoped_project_id()
            if project_id:
                statement = statement.where(SessionRecord.project_id == project_id)
            row = db.scalar(statement)
            if not row:
                return None
            return {column.name: getattr(row, column.name) for column in SessionRecord.__table__.columns}

    def sessions(self, limit: int = 25) -> list[dict]:
        with self.Session() as db:
            statement = select(SessionRecord)
            project_id = scoped_project_id()
            if project_id:
                statement = statement.where(SessionRecord.project_id == project_id)
            rows = db.scalars(statement.order_by(SessionRecord.created_at.desc()).limit(limit)).all()
            return [{column.name: getattr(row, column.name) for column in SessionRecord.__table__.columns} for row in rows]

    def project_text_replacements(self, project_id: str | None = None) -> list[dict[str, str]]:
        active_project_id = project_id or scoped_project_id()
        if not active_project_id:
            return []
        with self.Session() as db:
            row = db.get(ProjectTextReplacementSettings, active_project_id)
            return [dict(item) for item in (row.rules_json or [])] if row else []

    def save_project_text_replacements(self, rules: object,
                                       project_id: str | None = None) -> list[dict[str, str]]:
        active_project_id = project_id or scoped_project_id()
        if not active_project_id:
            raise ValueError("Abra um projeto antes de salvar as regras OCR.")
        cleaned = clean_replacement_rules(rules)
        with self.Session.begin() as db:
            if db.get(DubbingProject, active_project_id) is None:
                raise KeyError(active_project_id)
            row = db.get(ProjectTextReplacementSettings, active_project_id)
            if row is None:
                db.add(ProjectTextReplacementSettings(project_id=active_project_id,
                                                      rules_json=cleaned, updated_at=utc_now()))
            else:
                row.rules_json = cleaned
                row.updated_at = utc_now()
        return cleaned

    def session_text_replacements(self, session_id: str) -> list[dict[str, str]]:
        with self.Session() as db:
            session = db.get(SessionRecord, session_id)
            if session is None:
                return []
            row = db.get(ProjectTextReplacementSettings, session.project_id)
            return [dict(item) for item in (row.rules_json or [])] if row else []

    def session_asset_dir(self, session_id: str) -> Path:
        """Resolve a capture directory for a session, including legacy sessions in .data/sessions."""
        with self.Session() as db:
            row = db.get(SessionRecord, session_id)
        return self._session_asset_dir_for_row(row) if row else (self.sessions_root / session_id).resolve()

    def _session_asset_dir_for_row(self, row: SessionRecord) -> Path:
        asset_root = str((row.config_json or {}).get("asset_root") or "")
        if not asset_root:
            return (self.sessions_root / row.session_id).resolve()
        data_root = self.sessions_root.parent.resolve()
        candidate = (data_root / Path(asset_root)).resolve()
        if not candidate.is_relative_to((data_root / "projects").resolve()) or candidate.name != row.session_id:
            raise ValueError("O caminho de assets da sessão não pertence a um projeto.")
        return candidate

    def markers(self, session_id: str) -> list[dict]:
        with self.Session() as db:
            rows = db.scalars(select(TimelineMarker).where(
                TimelineMarker.session_id == session_id
            ).order_by(TimelineMarker.source_time, TimelineMarker.created_at)).all()
            return [{column.name: getattr(row, column.name) for column in TimelineMarker.__table__.columns}
                    for row in rows]

    def add_marker(self, session_id: str, source_time: float, kind: str, label: str) -> dict:
        marker = TimelineMarker(marker_id=str(uuid.uuid4()), session_id=session_id,
                                source_time=float(source_time), kind=kind, label=label[:160],
                                created_at=utc_now())
        with self.Session.begin() as db:
            session = db.get(SessionRecord, session_id)
            if not session or (scoped_project_id() and session.project_id != scoped_project_id()):
                raise KeyError(session_id)
            db.add(marker)
        return {column.name: getattr(marker, column.name) for column in TimelineMarker.__table__.columns}

    def delete_marker(self, marker_id: str) -> bool:
        with self.Session.begin() as db:
            marker = db.get(TimelineMarker, marker_id)
            if marker is None:
                return False
            db.delete(marker)
            return True

    def add_event(self, session_id: str, draft: dict, replace_scope: dict | None = None) -> tuple[str, bool]:
        now = utc_now()
        bbox = draft.get("bbox_json", draft.get("bbox", []))
        if replace_scope:
            self._replace_file_events(replace_scope, float(draft["start_time"]), tuple(bbox),
                                      draft.get("region_id"), session_id)
        with self.Session.begin() as db:
            session = db.get(SessionRecord, session_id)
            if session is None or (scoped_project_id() and session.project_id != scoped_project_id()):
                raise KeyError(session_id)
            profile_id = draft.get("character_profile_id")
            character_name = self._clean_character_name(draft.get("character"))
            region_id = draft.get("region_id")
            effective_character_name = character_name
            if region_id and self._is_character_name(character_name):
                note = ("Encontrado no campo Personagem (opcional) de um evento desta ROI, "
                        f"em {float(draft['start_time']):.3f} s. Confira se o valor corresponde "
                "ao nome do personagem. Um arquivo MP3 ou WAV de referência pode ser anexado manualmente depois.")
                profile = self._ensure_roi_character_record(db, region_id, character_name, note, now,
                                                            session.project_id)
                effective_character_name = profile.alias or profile.name
                draft["character"] = effective_character_name
                profile_id = profile.character_id
                draft["character_profile_id"] = profile_id
            elif not self._is_character_name(character_name):
                profile_id = None
                draft["character_profile_id"] = None
            existing = db.scalars(select(EventRecord).where(
                EventRecord.session_id == session_id,
                EventRecord.review_status.not_in(["discarded", "merged"]),
                EventRecord.merged_into.is_(None),
            ).order_by(EventRecord.start_time.desc()).limit(16)).all()
            for row in existing:
                gap = float(draft["start_time"]) - float(row.end_time or row.start_time)
                if equivalent_observation(draft.get("processed_text", draft["original_text"]),
                                          row.processed_text or row.original_text,
                                          tuple(bbox), tuple(row.bbox_json),
                                          draft["text_phash"], row.text_phash or "", gap):
                    if profile_id and row.region_id == region_id and (
                            row.character_profile_id == profile_id
                            or normalize_text(row.character or "") == normalize_text(effective_character_name)):
                        row.character_profile_id = profile_id
                        row.character = effective_character_name
                        row.updated_at = now
                    return row.event_id, True
            last = db.scalar(select(EventRecord.sequence).where(EventRecord.session_id == session_id)
                             .order_by(EventRecord.sequence.desc()).limit(1)) or 0
            event_id = str(uuid.uuid4())
            db.add(EventRecord(event_id=event_id, session_id=session_id, sequence=int(last) + 1,
                               segment_id=draft.get("segment_id", ""), start_time=float(draft["start_time"]),
                               end_time=None, original_text=draft["original_text"],
                               processed_text=draft.get("processed_text", draft["original_text"]),
                               character=draft.get("character"),
                               character_profile_id=profile_id,
                               region_id=draft.get("region_id"),
                               ocr_observations_json=draft.get("ocr_observations", []),
                               normalized_text=draft["normalized_text"], confidence=float(draft["confidence"]),
                               bbox_json=bbox, crop_path=draft.get("crop_path"),
                               start_frame_path=draft.get("start_frame_path"),
                               before_frame_path=draft.get("before_frame_path"),
                               best_frame_path=draft.get("best_frame_path"),
                               end_frame_path=draft.get("end_frame_path"),
                               assets_json=draft.get("assets", []),
                               processing_variants_json=draft.get("processing_variants", []),
                               text_phash=draft.get("text_phash"), context_phash=draft.get("context_phash"),
                               timing_quality=draft.get("timing_quality", "exact_pts"),
                               timing_error_ms=float(draft.get("timing_error_ms", 0)),
                               captured_at=draft.get("captured_at"), frame_id=int(draft.get("frame_id", 0)),
                               created_at=now, updated_at=now))
            return event_id, False

    def add_manual_event(self, session_id: str, text_value: str, start_time: float,
                        end_time: float, character: str | None = None,
                        assets: list[dict] | None = None, frame_id: int = 0,
                        ocr_observation: dict | None = None) -> dict:
        """Persist a reviewer-authored dialogue without presenting it as OCR output."""
        now = utc_now()
        with self.Session.begin() as db:
            session = db.get(SessionRecord, session_id)
            if session is None:
                raise KeyError(session_id)
            last = db.scalar(select(EventRecord.sequence).where(EventRecord.session_id == session_id)
                             .order_by(EventRecord.sequence.desc()).limit(1)) or 0
            event_id = str(uuid.uuid4())
            final_text = text_value.strip()
            evidence = dict(ocr_observation or {})
            original = str(evidence.get("raw_text") or final_text).strip()
            asset_list = assets or []
            for asset in evidence.get("image_assets") or []:
                if isinstance(asset, dict) and asset not in asset_list:
                    asset_list.append(asset)
            start_path = next((item["path"] for item in asset_list if item.get("kind") == "start"), None)
            confidence = float(evidence.get("confidence", 1.0)) if evidence else 1.0
            event_bbox = evidence.get("bbox") if isinstance(evidence.get("bbox"), list) else []
            event_frame_id = int(evidence.get("frame_id", frame_id)) if evidence else int(frame_id)
            timing_quality = str(evidence.get("timing_quality") or "exact_pts") if evidence else "manual"
            region_id = evidence.get("region_id") if evidence else None
            character_name = self._clean_character_name(character)
            character_profile_id = None
            if region_id and self._is_character_name(character_name):
                note = ("Associado ao criar manualmente um diálogo após OCR do nome do personagem em "
                        f"{float(evidence.get('source_time', start_time)):.3f} s.")
                profile = self._ensure_roi_character_record(
                    db, region_id, character_name, note, now, session.project_id,
                )
                character_name = profile.alias or profile.name
                character_profile_id = profile.character_id
            observation = ({
                "source": "manual_dialog_ocr",
                "text": original,
                "processed_text": str(evidence.get("processed_text") or final_text),
                "confidence": confidence,
                "source_time": evidence.get("source_time", start_time),
                "frame_id": event_frame_id,
                "timing_quality": timing_quality,
                "timing_error_ms": float(evidence.get("timing_error_ms") or 0),
                "bbox": event_bbox,
                "region_id": evidence.get("region_id"),
                "region_name": evidence.get("region_name"),
                "speaker": evidence.get("speaker"),
                "image_assets": evidence.get("image_assets") or [],
            } if evidence else {"source": "manual", "text": final_text, "confidence": None})
            row = EventRecord(
                event_id=event_id, session_id=session_id, sequence=int(last) + 1,
                segment_id="manual_ocr" if evidence else "manual",
                start_time=float(start_time), end_time=float(end_time),
                original_text=original,
                processed_text=final_text,
                ocr_observations_json=[observation],
                normalized_text=normalize_text(final_text), confidence=confidence, bbox_json=event_bbox,
                start_frame_path=start_path, best_frame_path=start_path,
                assets_json=asset_list, character=character_name or None,
                character_profile_id=character_profile_id, region_id=region_id,
                review_status="pending", truncated=False, timing_quality=timing_quality,
                timing_error_ms=float(evidence.get("timing_error_ms") or 0) if evidence else 0.0,
                frame_id=event_frame_id, created_at=now, updated_at=now,
            )
            db.add(row)
            db.add(ReviewRecord(event_id=event_id, action="manual_create",
                                details_json={"start_time": start_time, "end_time": end_time,
                                              "text": original}, created_at=now))
            return {column.name: getattr(row, column.name) for column in EventRecord.__table__.columns}

    @staticmethod
    def _same_box(first: tuple, second: tuple) -> bool:
        if len(first) < 4 or len(second) < 4:
            return True
        ax, ay, aw, ah = map(float, first[:4])
        bx, by, bw, bh = map(float, second[:4])
        intersection = max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0.0, min(ay + ah, by + bh) - max(ay, by))
        smaller = min(max(1.0, aw * ah), max(1.0, bw * bh))
        return intersection / smaller >= .18

    def _replace_file_events(self, scope: dict, start_time: float, bbox: tuple,
                             region_id: str | None, current_session_id: str) -> None:
        source_path = str(Path(scope["source_path"]).resolve(strict=False)).casefold()
        range_start, range_end = float(scope["start"]), float(scope["end"])
        removed_files: list[tuple[str, list[str]]] = []
        with self.Session.begin() as db:
            current_session = db.get(SessionRecord, current_session_id)
            if current_session is None:
                return
            sessions = db.scalars(select(SessionRecord).where(
                SessionRecord.project_id == current_session.project_id,
                SessionRecord.session_id != current_session_id,
            )).all()
            matching_sessions = []
            for session in sessions:
                config = session.config_json or {}
                candidate = config.get("downloaded_path") or config.get("path") or session.source_uri
                if not candidate:
                    continue
                if str(Path(candidate).resolve(strict=False)).casefold() == source_path:
                    matching_sessions.append(session.session_id)
            if not matching_sessions:
                return
            candidates = db.scalars(select(EventRecord).where(
                EventRecord.session_id.in_(matching_sessions),
                EventRecord.start_time >= range_start,
                EventRecord.start_time < range_end,
            )).all()
            matching_by_session: dict[str, list[EventRecord]] = {}
            for event in candidates:
                # A new OCR pass must not erase a reviewer-approved dialogue,
                # its correction, review history, or linked dubbing work.
                if event.review_status in {"accepted", "corrected"} or event.corrected_text:
                    continue
                if abs(float(event.start_time) - start_time) > 1.25:
                    continue
                if region_id and event.region_id and event.region_id != region_id:
                    continue
                if not region_id or not event.region_id:
                    if not self._same_box(tuple(event.bbox_json or ()), bbox):
                        continue
                matching_by_session.setdefault(event.session_id, []).append(event)
            selected = []
            for items in matching_by_session.values():
                nearest = min(abs(float(item.start_time) - start_time) for item in items)
                # Remove duplicate records from earlier reruns of this same occurrence,
                # while retaining a nearby but distinct line from that session.
                selected.extend(item for item in items
                                if abs(float(item.start_time) - start_time) <= nearest + 0.15)
            for event in selected:
                paths = []
                if event.crop_path:
                    paths.append(event.crop_path)
                for key in ("start_frame_path", "before_frame_path", "best_frame_path", "end_frame_path"):
                    if getattr(event, key):
                        paths.append(getattr(event, key))
                paths.extend(asset.get("path", "") for asset in (event.assets_json or []) if asset.get("path"))
                db.query(ReviewRecord).filter(ReviewRecord.event_id == event.event_id).delete(synchronize_session=False)
                db.delete(event)
                removed_files.append((event.session_id, paths))
        if removed_files:
            remaining = self.events()
            referenced: set[tuple[str, str]] = set()
            for event in remaining:
                references = [event.get(key) for key in ("crop_path", "start_frame_path", "before_frame_path", "best_frame_path", "end_frame_path")]
                references.extend(asset.get("path") for asset in (event.get("assets_json") or []))
                referenced.update((event["session_id"], value) for value in references if value)
            for old_session_id, paths in removed_files:
                session_dir = self.session_asset_dir(old_session_id)
                for relative in set(paths):
                    if (old_session_id, relative) in referenced:
                        continue
                    path = (session_dir / relative).resolve()
                    if path.is_relative_to(session_dir) and path.is_file():
                        path.unlink(missing_ok=True)

    def finish_event(self, event_id: str, end_time: float, truncated: bool = False,
                     ocr_update: dict | None = None) -> None:
        with self.Session.begin() as db:
            row = db.get(EventRecord, event_id)
            if row and row.merged_into is None:
                row.end_time = max(row.start_time, float(end_time))
                row.truncated = bool(truncated)
                if ocr_update:
                    for key in ("original_text", "processed_text", "normalized_text", "confidence", "bbox_json",
                                "ocr_observations_json", "text_phash", "crop_path",
                                "best_frame_path", "assets_json"):
                        if key in ocr_update:
                            setattr(row, key, ocr_update[key])
                row.updated_at = utc_now()

    def add_event_assets(self, event_id: str, assets: list[dict]) -> None:
        with self.Session.begin() as db:
            row = db.get(EventRecord, event_id)
            if row:
                row.assets_json = assets
                row.end_frame_path = next((item["path"] for item in assets if item["kind"] == "end"), None)
                row.updated_at = utc_now()

    def set_split_assets(self, first_id: str, second_id: str,
                         first_assets: list[dict], second_assets: list[dict],
                         second_crop_path: str) -> None:
        with self.Session.begin() as db:
            first = db.get(EventRecord, first_id)
            second = db.get(EventRecord, second_id)
            if not first or not second:
                raise KeyError(first_id if not first else second_id)
            first.assets_json = first_assets
            first.end_frame_path = next((item["path"] for item in first_assets if item["kind"] == "end"), None)
            second.assets_json = second_assets
            second.crop_path = second_crop_path
            second.start_frame_path = next((item["path"] for item in second_assets if item["kind"] == "start"), None)
            second.before_frame_path = next((item["path"] for item in second_assets if item["kind"] == "pre_250"), None)
            second.best_frame_path = next((item["path"] for item in second_assets if item["kind"] == "best"), None)
            second.end_frame_path = next((item["path"] for item in second_assets if item["kind"] == "end"), None)
            first.updated_at = second.updated_at = utc_now()

    def events(self, session_id: str | None = None, status: str | None = None,
               min_confidence: float | None = None, character: str | None = None,
               emotion: str | None = None) -> list[dict]:
        with self.Session() as db:
            statement = select(EventRecord).where(EventRecord.review_status != "merged")
            project_id = scoped_project_id()
            if project_id:
                statement = statement.join(SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                    SessionRecord.project_id == project_id)
            if session_id:
                statement = statement.where(EventRecord.session_id == session_id)
            if status == "low-confidence":
                statement = statement.where(EventRecord.review_status == "pending",
                                           EventRecord.confidence < 0.68)
            elif status:
                statement = statement.where(EventRecord.review_status == status)
            if min_confidence is not None:
                statement = statement.where(EventRecord.confidence >= min_confidence)
            if character == "__none__":
                statement = statement.where(or_(EventRecord.character.is_(None), EventRecord.character == ""))
            elif character is not None:
                statement = statement.where(EventRecord.character == character)
            if emotion == "__none__":
                statement = statement.where(or_(EventRecord.emotion.is_(None), EventRecord.emotion == ""))
            elif emotion is not None:
                statement = statement.where(EventRecord.emotion == emotion)
            rows = db.scalars(statement.order_by(EventRecord.start_time, EventRecord.sequence)).all()
            return [{column.name: getattr(row, column.name) for column in EventRecord.__table__.columns}
                    for row in rows]

    def event_occurrence_ids(self, event_ids: list[str]) -> dict[str, list[str]]:
        ids = list(dict.fromkeys(str(event_id) for event_id in event_ids if event_id))
        result = {event_id: [] for event_id in ids}
        if not ids:
            return result
        with self.Session() as db:
            # Keep the IN list below SQLite's bind-parameter limit for large projects.
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                rows = db.execute(
                    select(CaptureLink.event_id, DialogueOccurrence.occurrence_id)
                    .join(DialogueOccurrence, DialogueOccurrence.occurrence_id == CaptureLink.occurrence_id)
                    .where(CaptureLink.event_id.in_(chunk))
                    .order_by(CaptureLink.event_id, CaptureLink.created_at)
                ).all()
                for event_id, occurrence_id in rows:
                    values = result.setdefault(event_id, [])
                    if occurrence_id not in values:
                        values.append(occurrence_id)
        return result

    def event_filter_options(self, session_id: str | None = None) -> dict:
        with self.Session() as db:
            statement = select(EventRecord.character, EventRecord.emotion).where(
                EventRecord.review_status != "merged",
            )
            project_id = scoped_project_id()
            if project_id:
                statement = statement.join(SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                    SessionRecord.project_id == project_id)
            if session_id:
                statement = statement.where(EventRecord.session_id == session_id)
            rows = db.execute(statement).all()
        characters = sorted({str(character).strip() for character, _ in rows if character and str(character).strip()},
                            key=str.casefold)
        emotions = sorted({str(emotion).strip() for _, emotion in rows if emotion and str(emotion).strip()},
                          key=str.casefold)
        return {"characters": characters, "emotions": emotions,
                "has_unassigned_character": any(not character for character, _ in rows),
                "has_unassigned_emotion": any(not emotion for _, emotion in rows)}

    def event_status_summary(self, session_id: str | None = None) -> dict:
        """Count dialogue review states without depending on the current list filters."""
        green = func.coalesce(func.sum(case(
            (EventRecord.review_status.in_(("accepted", "corrected")), 1), else_=0)), 0)
        pending = func.coalesce(func.sum(case(
            ((EventRecord.review_status == "pending") & (EventRecord.confidence >= 0.68), 1), else_=0)), 0)
        low_confidence = func.coalesce(func.sum(case(
            ((EventRecord.review_status == "pending") & (EventRecord.confidence < 0.68), 1), else_=0)), 0)
        discarded = func.coalesce(func.sum(case(
            (EventRecord.review_status == "discarded", 1), else_=0)), 0)
        missing_character = func.coalesce(func.sum(case((and_(
            EventRecord.review_status != "discarded",
            or_(EventRecord.character.is_(None), func.trim(EventRecord.character) == ""),
        ), 1), else_=0)), 0)
        with self.Session() as db:
            statement = select(func.count(), green, pending, low_confidence, discarded,
                               missing_character).where(
                EventRecord.review_status != "merged")
            project_id = scoped_project_id()
            if project_id:
                statement = statement.join(SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                    SessionRecord.project_id == project_id)
            if session_id:
                statement = statement.where(EventRecord.session_id == session_id)
            total, green_count, pending_count, low_count, discarded_count, missing_character_count = \
                db.execute(statement).one()
            return {
                "total": int(total or 0),
                "green": int(green_count or 0),
                "pending": int(pending_count or 0),
                "low_confidence": int(low_count or 0),
                "discarded": int(discarded_count or 0),
                "missing_character": int(missing_character_count or 0),
            }

    @staticmethod
    def _replacement_rows(db):
        project_id = scoped_project_id()
        if not project_id:
            raise ValueError("Abra um projeto antes de substituir textos.")
        return db.scalars(select(EventRecord).join(
            SessionRecord, SessionRecord.session_id == EventRecord.session_id,
        ).where(
            SessionRecord.project_id == project_id,
            EventRecord.review_status.not_in(("discarded", "merged")),
            EventRecord.merged_into.is_(None),
        ).order_by(EventRecord.start_time, EventRecord.sequence)).all()

    @staticmethod
    def _replacement_text(event: EventRecord) -> str:
        if event.corrected_text is not None:
            return event.corrected_text
        return event.processed_text or event.original_text or ""

    def preview_project_text_replacement(self, find_text: str, replace_text: str) -> dict:
        if not find_text or not find_text.strip():
            raise ValueError("Informe o texto que deseja localizar.")
        matches = []
        occurrences = 0
        lines_by_id: dict[str, DialogueLine] = {}
        with self.Session() as db:
            rows = self._replacement_rows(db)
            for event in rows:
                before = self._replacement_text(event)
                count = before.count(find_text)
                after = before.replace(find_text, replace_text)
                if not count or after == before:
                    continue
                occurrences += count
                matches.append({
                    "event_id": event.event_id, "sequence": event.sequence,
                    "session_id": event.session_id, "start_time": event.start_time,
                    "before": before, "after": after, "occurrences": count,
                })
                links = db.scalars(select(CaptureLink).where(CaptureLink.event_id == event.event_id)).all()
                for link in links:
                    occurrence = db.get(DialogueOccurrence, link.occurrence_id)
                    line = db.get(DialogueLine, occurrence.line_id) if occurrence else None
                    if line and line.project_id == scoped_project_id() and find_text in (line.dub_text or ""):
                        lines_by_id[line.line_id] = line
            examples = matches[:8]
            return {
                "dialogues": len(matches), "occurrences": occurrences,
                "dubbing_lines": len(lines_by_id), "examples": examples,
                "total_dialogues": len(rows),
            }

    def replace_project_dialogue_text(self, find_text: str, replace_text: str) -> dict:
        if not find_text or not find_text.strip():
            raise ValueError("Informe o texto que deseja localizar.")
        now = utc_now()
        changed = 0
        occurrences = 0
        updated_lines: set[str] = set()
        with self.Session.begin() as db:
            rows = self._replacement_rows(db)
            for event in rows:
                before = self._replacement_text(event)
                count = before.count(find_text)
                after = before.replace(find_text, replace_text)
                if not count or after == before:
                    continue
                event.corrected_text = after
                event.review_status = "corrected"
                event.updated_at = now
                changed += 1
                occurrences += count
                db.add(ReviewRecord(
                    event_id=event.event_id, action="bulk_replace",
                    details_json={"find_text": find_text, "replace_text": replace_text,
                                  "occurrences": count}, created_at=now,
                ))
                links = db.scalars(select(CaptureLink).where(CaptureLink.event_id == event.event_id)).all()
                for link in links:
                    occurrence = db.get(DialogueOccurrence, link.occurrence_id)
                    line = db.get(DialogueLine, occurrence.line_id) if occurrence else None
                    if not line or line.project_id != scoped_project_id() or line.line_id in updated_lines:
                        continue
                    if find_text in (line.dub_text or ""):
                        line.dub_text = line.dub_text.replace(find_text, replace_text)
                        line.editorial_status = "corrected"
                        line.updated_at = now
                        updated_lines.add(line.line_id)
        return {"dialogues": changed, "occurrences": occurrences,
                "dubbing_lines": len(updated_lines)}

    def bulk_accept(self, event_ids: list[str]) -> int:
        unique_ids = list(dict.fromkeys(event_ids))
        if not unique_ids:
            raise ValueError("Selecione pelo menos um evento.")
        now = utc_now()
        with self.Session.begin() as db:
            rows = [db.get(EventRecord, event_id) for event_id in unique_ids]
            project_id = scoped_project_id()
            if project_id and any(row is not None and
                                  (session := db.get(SessionRecord, row.session_id)) is not None and
                                  session.project_id != project_id for row in rows):
                raise KeyError("dialogue event belongs to another project")
            if any(row is None for row in rows):
                missing = unique_ids[next(index for index, row in enumerate(rows) if row is None)]
                raise KeyError(missing)
            for row in rows:
                row.review_status = "accepted"
                row.updated_at = now
                db.add(ReviewRecord(event_id=row.event_id, action="bulk_accept",
                                    details_json={"source": "event_list"}, created_at=now))
        return len(unique_ids)

    def bulk_discard(self, event_ids: list[str]) -> int:
        unique_ids = list(dict.fromkeys(event_ids))
        if not unique_ids:
            raise ValueError("Selecione pelo menos um evento.")
        now = utc_now()
        with self.Session.begin() as db:
            rows = [db.get(EventRecord, event_id) for event_id in unique_ids]
            project_id = scoped_project_id()
            if project_id and any(row is not None and
                                  (session := db.get(SessionRecord, row.session_id)) is not None and
                                  session.project_id != project_id for row in rows):
                raise KeyError("dialogue event belongs to another project")
            if any(row is None for row in rows):
                missing = unique_ids[next(index for index, row in enumerate(rows) if row is None)]
                raise KeyError(missing)
            for row in rows:
                row.review_status = "discarded"
                row.updated_at = now
                db.add(ReviewRecord(event_id=row.event_id, action="bulk_discard",
                                    details_json={"source": "event_list"}, created_at=now))
        return len(unique_ids)

    def event(self, event_id: str) -> dict | None:
        with self.Session() as db:
            statement = select(EventRecord).where(EventRecord.event_id == event_id)
            project_id = scoped_project_id()
            if project_id:
                statement = statement.join(SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                    SessionRecord.project_id == project_id)
            row = db.scalar(statement)
            return ({column.name: getattr(row, column.name) for column in EventRecord.__table__.columns}
                    if row else None)

    @staticmethod
    def _event_asset_paths(row: EventRecord) -> set[str]:
        paths = {str(value) for value in (row.crop_path, row.start_frame_path, row.before_frame_path,
                                          row.best_frame_path, row.end_frame_path) if value}
        for asset in row.assets_json or []:
            if isinstance(asset, dict) and asset.get("path"):
                paths.add(str(asset["path"]))
        return paths

    def _remaining_event_asset_paths(self) -> set[Path]:
        with self.Session() as db:
            rows = db.scalars(select(EventRecord)).all()
            references = [(row.session_id, value) for row in rows
                          for value in self._event_asset_paths(row)]
        resolved: set[Path] = set()
        for session_id, value in references:
            try:
                raw = Path(value)
                session_root = self.session_asset_dir(session_id)
                candidate = raw.resolve(strict=False) if raw.is_absolute() else (session_root / raw).resolve(strict=False)
                resolved.add(candidate)
            except (OSError, RuntimeError, ValueError):
                continue
        return resolved

    def permanently_delete_event(self, event_id: str) -> dict:
        """Delete one dialogue and its now-unshared review/dubbing records and files."""
        event_paths: set[str] = set()
        take_paths: set[str] = set()
        session_id = ""
        session_root = self.sessions_root
        with self.Session.begin() as db:
            row = db.get(EventRecord, event_id)
            if not row:
                raise KeyError(event_id)
            session_id = row.session_id
            session = db.get(SessionRecord, session_id)
            if session:
                session_root = self._session_asset_dir_for_row(session)
            event_paths = self._event_asset_paths(row)

            # If the selected event was a merged result, restore its source entries as separate dialogs.
            merged_sources = db.scalars(select(EventRecord).where(EventRecord.merged_into == event_id)).all()
            for source in merged_sources:
                source.merged_into = None
                source.review_status = "pending"
                source.updated_at = utc_now()

            links = db.scalars(select(CaptureLink).where(CaptureLink.event_id == event_id)).all()
            occurrence_ids = list(dict.fromkeys(link.occurrence_id for link in links))
            db.execute(delete(CaptureLink).where(CaptureLink.event_id == event_id))

            for occurrence_id in occurrence_ids:
                still_linked = db.scalar(select(CaptureLink.capture_link_id).where(
                    CaptureLink.occurrence_id == occurrence_id,
                ).limit(1))
                if still_linked:
                    continue
                takes = db.scalars(select(AudioTake).where(
                    AudioTake.occurrence_id == occurrence_id,
                )).all()
                take_ids = [take.take_id for take in takes]
                jobs = db.scalars(select(TtsJob).where(TtsJob.take_id.in_(take_ids or [""]))).all()
                if any(job.status == "generating" for job in jobs):
                    raise ValueError("A fala tem um take em geração. Cancele ou aguarde a fila antes de excluí-la definitivamente.")
                for take in takes:
                    take_paths.update(path for path in (take.audio_master_path, take.audio_preview_path) if path)
                self._preserve_runtime_test_references(db, occurrence_id, take_ids)
                db.execute(delete(TtsJob).where(TtsJob.take_id.in_(take_ids or [""])))
                db.execute(delete(AudioTakeReview).where(AudioTakeReview.occurrence_id == occurrence_id))
                db.execute(delete(AudioTake).where(AudioTake.occurrence_id == occurrence_id))
                occurrence = db.get(DialogueOccurrence, occurrence_id)
                if occurrence:
                    line_id = occurrence.line_id
                    db.delete(occurrence)
                    remaining_occurrence = db.scalar(select(DialogueOccurrence.occurrence_id).where(
                        DialogueOccurrence.line_id == line_id,
                    ).limit(1))
                    if not remaining_occurrence:
                        line = db.get(DialogueLine, line_id)
                        if line:
                            db.delete(line)

            db.execute(delete(ReviewRecord).where(ReviewRecord.event_id == event_id))
            for run in db.scalars(select(FineSweepRun)).all():
                findings = run.findings_json or []
                filtered = [item for item in findings if not isinstance(item, dict)
                            or item.get("event_id") != event_id]
                if len(filtered) != len(findings):
                    run.findings_json = filtered
            db.delete(row)

        remaining_event_paths = self._remaining_event_asset_paths()
        for value in event_paths:
            try:
                raw = Path(value)
                path = raw.resolve(strict=False) if raw.is_absolute() else (session_root / raw).resolve(strict=False)
                if path.is_relative_to(session_root) and path not in remaining_event_paths:
                    path.unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError):
                continue

        with self.Session() as db:
            remaining_take_paths = {str(path) for take in db.scalars(select(AudioTake)).all()
                                    for path in (take.audio_master_path, take.audio_preview_path) if path}
        data_root = self.sessions_root.parent.resolve()
        for value in take_paths:
            try:
                relative = Path(value)
                path = relative.resolve(strict=False) if relative.is_absolute() else (data_root / relative).resolve(strict=False)
                if not path.is_relative_to(data_root):
                    continue
                normalized_value = path.relative_to(data_root).as_posix()
                if normalized_value not in remaining_take_paths:
                    path.unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError):
                continue
        return {"deleted": True, "event_id": event_id, "restored_merged_sources": len(merged_sources)}

    def permanently_delete_events(self, event_ids: list[str]) -> dict:
        """Atomically delete selected dialogues and their now-unshared dubbing data."""
        unique_ids = list(dict.fromkeys(str(value) for value in event_ids if value))
        if not unique_ids:
            raise ValueError("Selecione pelo menos um diálogo.")
        if len(unique_ids) > 5000:
            raise ValueError("A seleção excede o limite de 5.000 diálogos.")

        take_paths: set[str] = set()
        event_paths_by_session: dict[str, set[str]] = {}
        session_roots: dict[str, Path] = {}
        project_id = scoped_project_id()
        with self.Session.begin() as db:
            statement = select(EventRecord).where(EventRecord.event_id.in_(unique_ids))
            if project_id:
                statement = statement.join(SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                    SessionRecord.project_id == project_id,
                )
            rows = db.scalars(statement).all()
            rows_by_id = {row.event_id: row for row in rows}
            if len(rows_by_id) != len(unique_ids):
                missing = next((event_id for event_id in unique_ids if event_id not in rows_by_id), unique_ids[0])
                raise KeyError(missing)

            for row in rows:
                event_paths_by_session.setdefault(row.session_id, set()).update(self._event_asset_paths(row))
                session = db.get(SessionRecord, row.session_id)
                if session:
                    try:
                        session_roots[row.session_id] = self._session_asset_dir_for_row(session)
                    except (OSError, RuntimeError, ValueError):
                        session_roots[row.session_id] = self.sessions_root.resolve()
                else:
                    session_roots[row.session_id] = self.sessions_root.resolve()

            links = db.scalars(select(CaptureLink).where(CaptureLink.event_id.in_(unique_ids))).all()
            occurrence_ids = list(dict.fromkeys(link.occurrence_id for link in links))
            orphan_occurrence_ids = []
            for occurrence_id in occurrence_ids:
                outside_link = db.scalar(select(CaptureLink.capture_link_id).where(
                    CaptureLink.occurrence_id == occurrence_id,
                    CaptureLink.event_id.not_in(unique_ids),
                ).limit(1))
                if not outside_link:
                    orphan_occurrence_ids.append(occurrence_id)

            takes = db.scalars(select(AudioTake).where(
                AudioTake.occurrence_id.in_(orphan_occurrence_ids or [""]),
            )).all()
            take_ids = [take.take_id for take in takes]
            generating_job = db.scalar(select(TtsJob.job_id).where(
                TtsJob.take_id.in_(take_ids or [""]), TtsJob.status == "generating",
            ).limit(1))
            if generating_job:
                raise ValueError("Há uma dublagem em geração. Aguarde a fila antes de excluir os diálogos selecionados.")

            merged_sources = db.scalars(select(EventRecord).where(
                EventRecord.merged_into.in_(unique_ids),
                EventRecord.event_id.not_in(unique_ids),
            )).all()
            for source in merged_sources:
                source.merged_into = None
                source.review_status = "pending"
                source.updated_at = utc_now()
            for row in rows:
                if row.merged_into in unique_ids:
                    row.merged_into = None

            db.execute(delete(CaptureLink).where(CaptureLink.event_id.in_(unique_ids)))
            for occurrence_id in orphan_occurrence_ids:
                if db.scalar(select(CaptureLink.capture_link_id).where(
                    CaptureLink.occurrence_id == occurrence_id,
                ).limit(1)):
                    raise RuntimeError("As referências de captura mudaram durante a exclusão. Atualize e tente novamente.")

            current_takes = db.scalars(select(AudioTake).where(
                AudioTake.occurrence_id.in_(orphan_occurrence_ids or [""]),
            )).all()
            current_take_ids = [take.take_id for take in current_takes]
            take_paths = {path for take in current_takes
                          for path in (take.audio_master_path, take.audio_preview_path) if path}
            for occurrence_id in orphan_occurrence_ids:
                self._preserve_runtime_test_references(db, occurrence_id, current_take_ids)
            db.execute(delete(TtsJob).where(TtsJob.take_id.in_(current_take_ids or [""])))
            db.execute(delete(AudioTakeReview).where(or_(
                AudioTakeReview.occurrence_id.in_(orphan_occurrence_ids or [""]),
                AudioTakeReview.take_id.in_(current_take_ids or [""]),
            )))
            db.execute(delete(AudioTake).where(AudioTake.occurrence_id.in_(orphan_occurrence_ids or [""])))

            removed_line_ids = set()
            for occurrence_id in orphan_occurrence_ids:
                occurrence = db.get(DialogueOccurrence, occurrence_id)
                if occurrence:
                    removed_line_ids.add(occurrence.line_id)
                    db.delete(occurrence)
            for line_id in removed_line_ids:
                if not db.scalar(select(DialogueOccurrence.occurrence_id).where(
                    DialogueOccurrence.line_id == line_id,
                ).limit(1)):
                    line = db.get(DialogueLine, line_id)
                    if line:
                        db.delete(line)

            db.execute(delete(ReviewRecord).where(ReviewRecord.event_id.in_(unique_ids)))
            for run in db.scalars(select(FineSweepRun)).all():
                findings = run.findings_json or []
                filtered = [item for item in findings if not isinstance(item, dict)
                            or item.get("event_id") not in unique_ids]
                if len(filtered) != len(findings):
                    run.findings_json = filtered
            db.execute(delete(EventRecord).where(EventRecord.event_id.in_(unique_ids)))

            result = {
                "deleted": True,
                "deleted_events": len(unique_ids),
                "deleted_occurrences": len(orphan_occurrence_ids),
                "deleted_takes": len(current_takes),
                "preserved_shared_occurrences": len(occurrence_ids) - len(orphan_occurrence_ids),
                "restored_merged_sources": len(merged_sources),
            }
            if len(unique_ids) == 1:
                result["event_id"] = unique_ids[0]

        remaining_event_paths = self._remaining_event_asset_paths()
        for session_id, values in event_paths_by_session.items():
            session_root = session_roots.get(session_id, self.sessions_root.resolve())
            for value in values:
                try:
                    raw = Path(value)
                    path = raw.resolve(strict=False) if raw.is_absolute() else (session_root / raw).resolve(strict=False)
                    if path.is_relative_to(session_root) and path not in remaining_event_paths:
                        path.unlink(missing_ok=True)
                except (OSError, RuntimeError, ValueError):
                    continue

        with self.Session() as db:
            remaining_take_paths = {str(path) for take in db.scalars(select(AudioTake)).all()
                                    for path in (take.audio_master_path, take.audio_preview_path) if path}
        data_root = self.sessions_root.parent.resolve()
        for value in take_paths:
            try:
                relative = Path(value)
                path = relative.resolve(strict=False) if relative.is_absolute() else (data_root / relative).resolve(strict=False)
                if not path.is_relative_to(data_root):
                    continue
                if path.relative_to(data_root).as_posix() not in remaining_take_paths:
                    path.unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError):
                continue
        return result

    @staticmethod
    def _preserve_runtime_test_references(db, occurrence_id: str, take_ids: list[str]) -> None:
        take_id_set = set(take_ids)
        rows = db.scalars(select(RuntimeTestItem).where(or_(
            RuntimeTestItem.occurrence_id == occurrence_id,
            RuntimeTestItem.expected_take_id.in_(take_ids or [""]),
            RuntimeTestItem.played_take_id.in_(take_ids or [""]),
        ))).all()
        for row in rows:
            diagnostic = dict(row.diagnostic_json or {})
            removed = dict(diagnostic.get("removed_dialogue_references") or {})
            if row.occurrence_id == occurrence_id:
                removed["occurrence_id"] = row.occurrence_id
                row.occurrence_id = None
            if row.expected_take_id in take_id_set:
                removed["expected_take_id"] = row.expected_take_id
                row.expected_take_id = None
            if row.played_take_id in take_id_set:
                removed["played_take_id"] = row.played_take_id
                row.played_take_id = None
            diagnostic["removed_dialogue_references"] = removed
            row.diagnostic_json = diagnostic

    def delete_capture_session(self, session_id: str) -> dict:
        """Permanently delete one completed capture block, keeping shared dialogue data."""
        active_states = {"queued", "downloading", "preparing", "running", "processing", "paused", "stopping"}
        event_paths: set[str] = set()
        take_paths: set[str] = set()
        session_root = self.sessions_root.resolve()
        project_id = scoped_project_id()

        with self.Session.begin() as db:
            session = db.get(SessionRecord, session_id)
            if session is None or (project_id and session.project_id != project_id):
                raise KeyError(session_id)
            if session.state in active_states:
                raise ValueError("Este bloco ainda está em andamento. Aguarde o término ou pare a captura antes de excluí-lo.")

            children = db.scalars(select(SessionRecord).where(
                SessionRecord.project_id == session.project_id,
            )).all()
            direct_children = [item for item in children
                               if (item.config_json or {}).get("previous_session_id") == session_id]
            if any(item.state in active_states for item in direct_children):
                raise ValueError("Um bloco seguinte ainda está em andamento. Termine ou pare essa captura antes de excluir este bloco.")

            running_sweep = db.scalar(select(FineSweepRun.run_id).where(
                FineSweepRun.session_id == session_id, FineSweepRun.state == "running",
            ).limit(1))
            if running_sweep:
                raise ValueError("Aguarde o pente fino deste bloco terminar antes de excluí-lo.")

            try:
                session_root = self._session_asset_dir_for_row(session)
            except (OSError, RuntimeError, ValueError):
                session_root = self.sessions_root.resolve()

            events = db.scalars(select(EventRecord).where(
                EventRecord.session_id == session_id,
            )).all()
            event_ids = [event.event_id for event in events]
            event_id_set = set(event_ids)
            event_paths = {path for event in events for path in self._event_asset_paths(event)}
            links = db.scalars(select(CaptureLink).where(or_(
                CaptureLink.session_id == session_id,
                CaptureLink.event_id.in_(event_ids or [""]),
            ))).all()
            occurrence_ids = list(dict.fromkeys(link.occurrence_id for link in links))

            # Only data whose last capture link belongs to this block can be removed.
            orphan_occurrence_ids = []
            for occurrence_id in occurrence_ids:
                outside_link = db.scalar(select(CaptureLink.capture_link_id).where(
                    CaptureLink.occurrence_id == occurrence_id,
                    CaptureLink.session_id != session_id,
                    CaptureLink.event_id.not_in(event_ids or [""]),
                ).limit(1))
                if not outside_link:
                    orphan_occurrence_ids.append(occurrence_id)

            takes = db.scalars(select(AudioTake).where(
                AudioTake.occurrence_id.in_(orphan_occurrence_ids or [""]),
            )).all()
            take_ids = [take.take_id for take in takes]
            jobs = db.scalars(select(TtsJob).where(
                TtsJob.take_id.in_(take_ids or [""]),
            )).all()
            if any(job.status == "generating" for job in jobs):
                raise ValueError("Há uma dublagem em geração neste bloco. Aguarde a fila antes de excluí-lo.")

            parent_id = (session.config_json or {}).get("previous_session_id")
            for child in direct_children:
                config = dict(child.config_json or {})
                if parent_id:
                    config["previous_session_id"] = parent_id
                else:
                    config.pop("previous_session_id", None)
                child.config_json = config

            for event in events:
                if event.merged_into in event_id_set:
                    event.merged_into = None
                for source in db.scalars(select(EventRecord).where(
                    EventRecord.merged_into == event.event_id,
                    EventRecord.event_id.not_in(event_ids or [""]),
                )).all():
                    source.merged_into = None
                    source.review_status = "pending"
                    source.updated_at = utc_now()

            db.execute(delete(CaptureLink).where(or_(
                CaptureLink.session_id == session_id,
                CaptureLink.event_id.in_(event_ids or [""]),
            )))
            remaining_occurrence_ids = []
            for occurrence_id in orphan_occurrence_ids:
                still_linked = db.scalar(select(CaptureLink.capture_link_id).where(
                    CaptureLink.occurrence_id == occurrence_id,
                ).limit(1))
                if not still_linked:
                    remaining_occurrence_ids.append(occurrence_id)
            # Recheck after removing this block's links; a concurrent link may have appeared.
            if len(remaining_occurrence_ids) != len(orphan_occurrence_ids):
                raise RuntimeError("As referências de captura mudaram durante a exclusão. Atualize e tente novamente.")

            current_take_rows = db.scalars(select(AudioTake).where(
                AudioTake.occurrence_id.in_(remaining_occurrence_ids or [""]),
            )).all()
            current_take_ids = [take.take_id for take in current_take_rows]
            take_paths = {path for take in current_take_rows
                          for path in (take.audio_master_path, take.audio_preview_path) if path}
            removed_ready_takes = sum(take.status in {"generated", "publishing", "review", "ready", "approved"}
                                      for take in current_take_rows)
            for occurrence_id in remaining_occurrence_ids:
                self._preserve_runtime_test_references(db, occurrence_id, current_take_ids)
            db.execute(delete(TtsJob).where(TtsJob.take_id.in_(current_take_ids or [""])))
            db.execute(delete(AudioTakeReview).where(or_(
                AudioTakeReview.occurrence_id.in_(remaining_occurrence_ids or [""]),
                AudioTakeReview.take_id.in_(current_take_ids or [""]),
            )))
            db.execute(delete(AudioTake).where(AudioTake.occurrence_id.in_(remaining_occurrence_ids or [""])))

            removed_line_ids = set()
            for occurrence_id in remaining_occurrence_ids:
                occurrence = db.get(DialogueOccurrence, occurrence_id)
                if occurrence:
                    removed_line_ids.add(occurrence.line_id)
                    db.delete(occurrence)
            for line_id in removed_line_ids:
                if not db.scalar(select(DialogueOccurrence.occurrence_id).where(
                    DialogueOccurrence.line_id == line_id,
                ).limit(1)):
                    line = db.get(DialogueLine, line_id)
                    if line:
                        db.delete(line)

            db.execute(delete(ReviewRecord).where(ReviewRecord.event_id.in_(event_ids or [""])))
            db.execute(delete(EventRecord).where(EventRecord.event_id.in_(event_ids or [""])))
            db.execute(delete(FineSweepRun).where(FineSweepRun.session_id == session_id))
            db.execute(delete(TimelineMarker).where(TimelineMarker.session_id == session_id))
            db.execute(delete(CaptureTraversal).where(CaptureTraversal.session_id == session_id))
            db.delete(session)

            result = {
                "deleted": True,
                "session_id": session_id,
                "deleted_events": len(events),
                "deleted_occurrences": len(remaining_occurrence_ids),
                "deleted_takes": len(current_take_rows),
                "removed_ready_takes": int(removed_ready_takes),
                "preserved_shared_occurrences": len(occurrence_ids) - len(remaining_occurrence_ids),
                "relinked_following_blocks": len(direct_children),
            }

        remaining_event_paths = self._remaining_event_asset_paths()
        for value in event_paths:
            try:
                raw = Path(value)
                path = raw.resolve(strict=False) if raw.is_absolute() else (session_root / raw).resolve(strict=False)
                if path.is_relative_to(session_root) and path not in remaining_event_paths:
                    path.unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError):
                continue

        with self.Session() as db:
            remaining_take_paths = {str(path) for take in db.scalars(select(AudioTake)).all()
                                    for path in (take.audio_master_path, take.audio_preview_path) if path}
        data_root = self.sessions_root.parent.resolve()
        for value in take_paths:
            try:
                relative = Path(value)
                path = relative.resolve(strict=False) if relative.is_absolute() else (data_root / relative).resolve(strict=False)
                if not path.is_relative_to(data_root):
                    continue
                if path.relative_to(data_root).as_posix() not in remaining_take_paths:
                    path.unlink(missing_ok=True)
            except (OSError, RuntimeError, ValueError):
                continue
        return result

    def clear_session_events(self, session_id: str) -> int:
        """Remove only the selected block's events when its owner restarts it."""
        with self.Session() as db:
            session = db.get(SessionRecord, session_id)
            if session is None or (scoped_project_id() and session.project_id != scoped_project_id()):
                raise KeyError(session_id)
            event_ids = list(db.scalars(select(EventRecord.event_id).where(
                EventRecord.session_id == session_id,
            )).all())
            if event_ids and db.scalar(select(TtsJob.job_id).join(
                    AudioTake, AudioTake.take_id == TtsJob.take_id).join(
                    CaptureLink, CaptureLink.occurrence_id == AudioTake.occurrence_id).where(
                    CaptureLink.event_id.in_(event_ids), TtsJob.status == "generating").limit(1)):
                raise ValueError("Há uma dublagem em geração neste bloco. Aguarde ou cancele a fila antes de apagá-lo.")
        for event_id in event_ids:
            self.permanently_delete_event(event_id)
        return len(event_ids)

    def duplicate_event(self, event_id: str, start_time: float, end_time: float) -> dict:
        now = utc_now()
        with self.Session.begin() as db:
            source = db.get(EventRecord, event_id)
            if source is None:
                raise KeyError(event_id)
            if source.review_status == "merged":
                raise ValueError("Um evento unido não pode ser duplicado.")
            if end_time <= start_time:
                raise ValueError("O fim precisa ser posterior ao início.")
            sequence = (db.scalar(select(EventRecord.sequence).where(
                EventRecord.session_id == source.session_id).order_by(EventRecord.sequence.desc()).limit(1)) or 0) + 1
            new_id = str(uuid.uuid4())
            copied_text = source.corrected_text or source.processed_text or source.original_text
            reference_assets = [{**asset, "copied_from_event_id": event_id}
                                for asset in (source.assets_json or [])]
            row = EventRecord(
                event_id=new_id, session_id=source.session_id, sequence=sequence,
                segment_id="duplicate", start_time=start_time, end_time=end_time,
                original_text=copied_text, processed_text=copied_text,
                normalized_text=normalize_text(copied_text), corrected_text=None,
                ocr_observations_json=[{"source": "duplicate", "source_event_id": event_id,
                                        "text": copied_text, "source_time": start_time}],
                confidence=source.confidence, bbox_json=source.bbox_json,
                crop_path=source.crop_path, start_frame_path=source.start_frame_path,
                before_frame_path=source.before_frame_path, best_frame_path=source.best_frame_path,
                end_frame_path=source.end_frame_path, assets_json=reference_assets,
                processing_variants_json=[], text_phash=source.text_phash,
                context_phash=source.context_phash, character=source.character,
                character_profile_id=source.character_profile_id, region_id=source.region_id,
                emotion=source.emotion, review_status="pending", truncated=False,
                timing_quality="manual_duplicate", timing_error_ms=0.0,
                captured_at=None, frame_id=0, created_at=now, updated_at=now,
            )
            db.add(row)
            db.add(ReviewRecord(event_id=new_id, action="duplicate_from",
                                details_json={"source_event_id": event_id, "source_time": start_time},
                                created_at=now))
            return {column.name: getattr(row, column.name) for column in EventRecord.__table__.columns}

    def add_ocr_retry(self, event_id: str, observation: dict) -> dict:
        with self.Session.begin() as db:
            row = db.get(EventRecord, event_id)
            if row is None:
                raise KeyError(event_id)
            row.ocr_observations_json = [*(row.ocr_observations_json or []), observation]
            row.updated_at = utc_now()
            db.add(ReviewRecord(event_id=event_id, action="retry_ocr",
                                details_json={"source_time": observation.get("source_time"),
                                              "text": observation.get("text"),
                                              "confidence": observation.get("confidence")},
                                created_at=row.updated_at))
            return {column.name: getattr(row, column.name) for column in EventRecord.__table__.columns}

    def review(self, event_id: str, action: str, changes: dict | None = None) -> dict:
        changes = changes or {}
        with self.Session.begin() as db:
            row = db.get(EventRecord, event_id)
            if not row:
                raise KeyError(event_id)
            session = db.get(SessionRecord, row.session_id)
            project_id = session.project_id if session else (scoped_project_id() or "replaced")
            old_profile = None
            if "character" in changes:
                old_character_name = self._clean_character_name(row.character)
                old_roi_id = row.region_id or self._single_session_roi_id(db, row.session_id)
                if row.character_profile_id:
                    candidate = db.get(RoiCharacterRecord, row.character_profile_id)
                    if candidate and candidate.project_id == project_id:
                        old_profile = candidate
                if old_profile is None and old_roi_id and self._is_character_name(old_character_name):
                    # Older event rows may have a character name but no profile id.
                    # Resolve only an unambiguous exact name match in the same ROI.
                    matching_profiles = db.scalars(select(RoiCharacterRecord).where(
                        RoiCharacterRecord.project_id == project_id,
                        RoiCharacterRecord.roi_id == old_roi_id,
                    )).all()
                    exact_matches = [profile for profile in matching_profiles
                                     if normalize_text(profile.name) == normalize_text(old_character_name)
                                     or normalize_text(profile.alias or "") == normalize_text(old_character_name)]
                    if len(exact_matches) == 1:
                        old_profile = exact_matches[0]
            if action == "accept":
                row.review_status = "accepted"
            elif action == "discard":
                row.review_status = "discarded"
            if action in {"correct", "update", "accept"} and "corrected_text" in changes:
                row.corrected_text = changes.get("corrected_text", row.corrected_text)
                if action == "correct":
                    row.review_status = "corrected"
            for key in ("character", "emotion"):
                if key in changes:
                    setattr(row, key, str(changes[key])[:120] or None)
            if "character" in changes:
                character_name = self._clean_character_name(changes.get("character"))
                row.character = character_name or None
                event_roi_id = row.region_id or self._single_session_roi_id(db, row.session_id)
                if event_roi_id and self._is_character_name(character_name):
                    profile = self._ensure_roi_character_record(
                        db, event_roi_id, character_name, self._character_field_note(row), utc_now(),
                        project_id=project_id,
                    )
                    row.region_id = event_roi_id
                    row.character = profile.alias or profile.name
                    row.character_profile_id = profile.character_id
                else:
                    row.character_profile_id = None
                if old_profile and old_profile.character_id != row.character_profile_id:
                    other_events = db.scalars(select(EventRecord).join(
                        SessionRecord, SessionRecord.session_id == EventRecord.session_id,
                    ).where(
                        SessionRecord.project_id == project_id,
                        EventRecord.event_id != row.event_id,
                        EventRecord.merged_into.is_(None),
                        EventRecord.review_status != "merged",
                    )).all()
                    still_referenced = False
                    old_name_keys = {normalize_text(old_profile.name), normalize_text(old_profile.alias or "")}
                    for other in other_events:
                        if other.character_profile_id == old_profile.character_id:
                            still_referenced = True
                            break
                        # Preserve profiles referenced by legacy rows that have not
                        # yet received a character_profile_id during ROI sync.
                        if (other.character_profile_id is None
                                and normalize_text(self._clean_character_name(other.character)) in old_name_keys):
                            other_roi_id = other.region_id or self._single_session_roi_id(db, other.session_id)
                            if other_roi_id == old_profile.roi_id:
                                still_referenced = True
                                break
                    if not still_referenced:
                        old_profile.active = False
                        old_profile.updated_at = utc_now()
            for key in ("start_time", "end_time"):
                if key in changes and changes[key] is not None:
                    setattr(row, key, max(0.0, float(changes[key])))
            if "start_time" in changes or "end_time" in changes:
                # CaptureLink is the dubbing timeline's read model. Keep it in
                # lockstep with the event so timing edits in either screen are
                # immediately visible in the other one.
                links = db.scalars(select(CaptureLink).where(CaptureLink.event_id == row.event_id)).all()
                for link in links:
                    if "start_time" in changes and changes["start_time"] is not None:
                        link.source_start_time = row.start_time
                    if "end_time" in changes:
                        link.source_end_time = row.end_time
            if "bbox_json" in changes:
                row.bbox_json = [int(value) for value in changes["bbox_json"]]
            row.updated_at = utc_now()
            db.add(ReviewRecord(event_id=event_id, action=action, details_json=changes, created_at=utc_now()))
            return {column.name: getattr(row, column.name) for column in EventRecord.__table__.columns}

    def merge(self, event_ids: list[str], corrected_text: str | None = None) -> dict:
        if len(event_ids) < 2 or len(set(event_ids)) != len(event_ids):
            raise ValueError("Selecione pelo menos dois eventos distintos para unir.")
        now = utc_now()
        with self.Session.begin() as db:
            rows = [db.get(EventRecord, event_id) for event_id in event_ids]
            if any(row is None or row.merged_into or row.review_status == "discarded" for row in rows):
                raise ValueError("Há um evento inválido ou descartado na seleção.")
            if len({row.session_id for row in rows}) != 1:
                raise ValueError("Só é possível unir eventos da mesma sessão.")
            rows.sort(key=lambda row: row.start_time)
            if any(float(right.start_time) - float(left.end_time or left.start_time) > 30
                   for left, right in zip(rows, rows[1:])):
                raise ValueError("Só é possível unir eventos próximos no tempo.")
            primary = rows[0]
            primary.end_time = max(float(row.end_time or row.start_time) for row in rows)
            primary.corrected_text = corrected_text or " ".join(
                row.corrected_text or row.processed_text or row.original_text for row in rows)
            primary.processed_text = " ".join(row.processed_text or row.original_text for row in rows)
            primary.review_status = "corrected"
            primary.updated_at = now
            for row in rows[1:]:
                row.merged_into = primary.event_id
                row.review_status = "merged"
                row.updated_at = now
                db.add(ReviewRecord(event_id=row.event_id, action="merge", details_json={"merged_into": primary.event_id}, created_at=now))
            db.add(ReviewRecord(event_id=primary.event_id, action="merge", details_json={"merged_ids": event_ids}, created_at=now))
            return {column.name: getattr(primary, column.name) for column in EventRecord.__table__.columns}

    def split(self, event_id: str, split_time: float,
              first_assets: list[dict] | None = None,
              second_assets: list[dict] | None = None,
              second_crop_path: str | None = None,
              second_metadata: dict | None = None) -> tuple[dict, dict]:
        now = utc_now()
        with self.Session.begin() as db:
            row = db.get(EventRecord, event_id)
            if not row:
                raise KeyError(event_id)
            end_time = row.end_time or row.start_time
            if not row.start_time < split_time < end_time:
                raise ValueError("O ponto de divisão precisa ficar entre início e fim do evento.")
            next_sequence = (db.scalar(select(EventRecord.sequence).where(EventRecord.session_id == row.session_id)
                                       .order_by(EventRecord.sequence.desc()).limit(1)) or 0) + 1
            original_end = row.end_time
            original_truncated = row.truncated
            row.end_time = float(split_time)
            row.truncated = False
            row.review_status = "pending"
            if first_assets is not None:
                row.assets_json = first_assets
                row.end_frame_path = next((item["path"] for item in first_assets if item["kind"] == "end"), None)
            row.updated_at = now
            second_id = str(uuid.uuid4())
            second = EventRecord(event_id=second_id, session_id=row.session_id, sequence=int(next_sequence),
                                 segment_id=row.segment_id, start_time=float(split_time), end_time=original_end,
                                 original_text=row.original_text, ocr_observations_json=row.ocr_observations_json,
                                 processed_text=row.processed_text,
                                 normalized_text=row.normalized_text,
                                 corrected_text=row.corrected_text, confidence=row.confidence,
                                 bbox_json=row.bbox_json, crop_path=second_crop_path or row.crop_path,
                                 start_frame_path=next((item["path"] for item in (second_assets or []) if item["kind"] == "start"), row.start_frame_path),
                                 before_frame_path=next((item["path"] for item in (second_assets or []) if item["kind"] == "pre_250"), row.start_frame_path),
                                 best_frame_path=next((item["path"] for item in (second_assets or []) if item["kind"] == "best"), row.best_frame_path),
                                 end_frame_path=next((item["path"] for item in (second_assets or []) if item["kind"] == "end"), row.end_frame_path),
                                 assets_json=second_assets if second_assets is not None else row.assets_json,
                                 processing_variants_json=row.processing_variants_json,
                                 text_phash=row.text_phash, context_phash=row.context_phash,
                                 character=row.character, emotion=row.emotion, review_status="pending",
                                 character_profile_id=row.character_profile_id, region_id=row.region_id,
                                 truncated=bool(original_truncated), timing_quality=row.timing_quality,
                                 timing_error_ms=row.timing_error_ms, captured_at=row.captured_at,
                                 frame_id=row.frame_id, created_at=now, updated_at=now)
            for key, value in (second_metadata or {}).items():
                if key in {"segment_id", "frame_id", "timing_quality", "timing_error_ms", "captured_at",
                           "text_phash", "context_phash"}:
                    setattr(second, key, value)
            db.add(second)
            db.add(ReviewRecord(event_id=event_id, action="split", details_json={"split_time": split_time, "new_event_id": second_id}, created_at=now))
            db.add(ReviewRecord(event_id=second_id, action="split_from", details_json={"source_event_id": event_id, "split_time": split_time}, created_at=now))
            return ({column.name: getattr(row, column.name) for column in EventRecord.__table__.columns},
                    {column.name: getattr(second, column.name) for column in EventRecord.__table__.columns})

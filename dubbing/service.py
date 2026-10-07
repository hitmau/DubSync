from __future__ import annotations

import csv
import io
import os
import re
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select

from ..database.models import (
    AudioTake, AudioTakeReview, CaptureLink, CaptureTraversal, DialogueLine,
    DialogueOccurrence, DubbingCharacter, DubbingProject, DubbingRoute,
    DubbingScene, EventRecord, InterpretationPreset, RoiCharacterRecord,
    SceneEdge, SessionRecord, TtsJob, VoiceProfile, VoiceReferenceAsset,
)
from ..database.store import DubSyncStore, utc_now
from ..events.normalize import normalize_text
from ..projects.context import current_project_id
from .audio import data_path, normalize_reference, sha256_file, MAX_REFERENCE_BYTES


VOICE_EMOTIONS = {
    "normal": "Interpretação natural, clara e contida.",
    "tension": "Tom tenso e urgente, intensidade emocional moderada.",
    "fear": "Medo contido, respiração discretamente acelerada; evite caricatura.",
    "angry": "Voz firme e irritada, sem gritar de forma caricata.",
    "sad": "Tristeza contida, fala mais baixa e cuidadosa.",
    "joy": "Alegria espontânea e natural, sem exagero.",
    "surprise": "Surpresa breve e sincera, sem alongar a reação.",
}
DEFAULT_GENERATION_SETTINGS = {
    "duration": None, "denoise": True, "num_step": 32, "guidance_scale": 2.0,
    "t_shift": 0.1, "position_temperature": 5.0, "class_temperature": 0.0,
    "layer_penalty_factor": 5.0, "normalize_text": False,
    "preprocess_prompt": True, "postprocess_output": True,
    "pad_duration": 0.1, "fade_duration": 0.1,
    "audio_chunk_duration": 15.0, "audio_chunk_threshold": 30.0,
}


def normalize_generation_settings(value: object) -> dict:
    if not isinstance(value, dict):
        raise DubbingError("invalid_generation_settings", "As configurações OmniVoice precisam ser um objeto.")
    result = dict(DEFAULT_GENERATION_SETTINGS)
    result.update({key: value[key] for key in DEFAULT_GENERATION_SETTINGS if key in value})
    try:
        duration = result["duration"]
        result["duration"] = None if duration in (None, "") else float(duration)
        for key in ("denoise", "normalize_text", "preprocess_prompt", "postprocess_output"):
            result[key] = bool(result[key])
        result["num_step"] = int(result["num_step"])
        for key in ("guidance_scale", "t_shift", "position_temperature", "class_temperature",
                    "layer_penalty_factor", "pad_duration", "fade_duration",
                    "audio_chunk_duration", "audio_chunk_threshold"):
            result[key] = float(result[key])
    except (TypeError, ValueError) as error:
        raise DubbingError("invalid_generation_settings", "Verifique os valores de geração avançados.") from error
    if result["duration"] is not None and not 0.1 <= result["duration"] <= 60:
        raise DubbingError("invalid_duration", "A duração precisa ficar entre 0,1 e 60 segundos.")
    if result["num_step"] not in {16, 32}:
        raise DubbingError("invalid_num_step", "Escolha 16 ou 32 etapas de geração.")
    limits = {"guidance_scale": (0, 10), "t_shift": (0, 2), "position_temperature": (0, 10),
              "class_temperature": (0, 10), "layer_penalty_factor": (0, 10),
              "pad_duration": (0, 10), "fade_duration": (0, 10),
              "audio_chunk_duration": (0.5, 120), "audio_chunk_threshold": (0.5, 600)}
    for key, (minimum, maximum) in limits.items():
        if not minimum <= result[key] <= maximum:
            raise DubbingError("invalid_generation_settings", f"{key} precisa ficar entre {minimum} e {maximum}.")
    return result


class DubbingError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12].upper()}"


def json_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def model_dict(row) -> dict:
    return {column.name: json_value(getattr(row, column.name)) for column in row.__table__.columns}


class DubbingService:
    def __init__(self, store: DubSyncStore, data_root: Path):
        self.store = store
        self.data_root = data_root.resolve()

    @property
    def project_id(self) -> str:
        return current_project_id()

    @property
    def project_data_root(self) -> Path:
        with self.store.Session() as db:
            project = db.get(DubbingProject, self.project_id)
            slug = project.slug if project else self.project_id
        return self.data_root / "projects" / slug / "dubbing"

    @staticmethod
    def _refresh_existing_capture_line(line: DialogueLine, event: EventRecord, now: datetime) -> None:
        """Repair source-derived fields on imported lines without replacing custom dub text."""
        changed = False
        original = (event.original_text or event.processed_text or "").strip()
        processed = (event.processed_text or event.original_text or "").strip()
        dub_source = (event.corrected_text or event.processed_text or event.original_text or "").strip()
        if event.corrected_text and line.dub_text.strip() in {original, processed} and dub_source:
            line.dub_text = dub_source
            changed = True
        source_text = event.original_text or event.processed_text or ""
        if line.source_text.strip() in {original, processed} and line.source_text != source_text:
            line.source_text = source_text
            changed = True
        if line.editorial_status != event.review_status:
            line.editorial_status = event.review_status
            changed = True
        if changed:
            line.updated_at = now

    def initialize(self) -> dict:
        """Create the pilot project and a traceable, one-event-to-one-occurrence baseline."""
        now = utc_now()
        created_lines = 0
        created_characters = 0
        with self.store.Session.begin() as db:
            project = db.get(DubbingProject, self.project_id)
            sessions = db.scalars(select(SessionRecord).where(
                SessionRecord.project_id == self.project_id,
            ).order_by(SessionRecord.created_at)).all()
            source_language = sessions[-1].language if sessions else (project.capture_language if project else "pt")
            if project is None:
                project = DubbingProject(
                    project_id=self.project_id, name="REPLACED", slug="replaced", game_title="REPLACED",
                    game_version=None, source_language=source_language, capture_language=source_language,
                    dub_language="pt-BR",
                    created_at=now, updated_at=now,
                )
                db.add(project)

            character_by_legacy = {}
            legacy_characters = db.scalars(select(RoiCharacterRecord).where(
                RoiCharacterRecord.project_id == self.project_id,
            ).order_by(RoiCharacterRecord.created_at)).all()
            for legacy in legacy_characters:
                character = db.scalar(select(DubbingCharacter).where(
                    DubbingCharacter.legacy_character_profile_id == legacy.character_id,
                ))
                if character is None:
                    character = DubbingCharacter(
                        character_id=new_id("CHR"), project_id=self.project_id,
                        legacy_character_profile_id=legacy.character_id,
                        name=legacy.name[:120], display_name=(legacy.alias or legacy.name)[:120], aliases_json=[],
                        notes=legacy.notes or "Importado do cadastro de ROI; confira se o texto é realmente um personagem.",
                        name_verified=False, active=bool(legacy.active), created_at=legacy.created_at or now,
                        updated_at=legacy.updated_at or now,
                    )
                    db.add(character)
                    created_characters += 1
                else:
                    # The ROI registry owns a dubbing character's identity and lifecycle.
                    # Keep the linked record aligned while retaining its voice profiles and history.
                    character.name = legacy.name[:120]
                    character.display_name = (legacy.alias or legacy.name)[:120]
                    character.active = bool(legacy.active)
                    character.updated_at = now
                character_by_legacy[legacy.character_id] = character.character_id

            events = db.scalars(select(EventRecord).join(
                SessionRecord, SessionRecord.session_id == EventRecord.session_id).where(
                SessionRecord.project_id == self.project_id,
                EventRecord.review_status != "merged", EventRecord.merged_into.is_(None),
            ).order_by(EventRecord.created_at, EventRecord.sequence)).all()
            for event in events:
                existing_links = db.scalars(select(CaptureLink).where(
                    CaptureLink.event_id == event.event_id,
                )).all()
                if existing_links:
                    for link in existing_links:
                        occurrence = db.get(DialogueOccurrence, link.occurrence_id)
                        line = db.get(DialogueLine, occurrence.line_id) if occurrence else None
                        if line and line.project_id == self.project_id:
                            self._refresh_existing_capture_line(line, event, now)
                    continue
                session = db.get(SessionRecord, event.session_id)
                if session is None:
                    continue
                line_id, occurrence_id = new_id("LINE"), new_id("OCC")
                text_for_dub = event.corrected_text or event.processed_text or event.original_text
                target_ms = None
                if event.end_time is not None and event.end_time > event.start_time:
                    target_ms = round((event.end_time - event.start_time) * 1000)
                line = DialogueLine(
                    line_id=line_id, project_id=self.project_id,
                    character_id=character_by_legacy.get(event.character_profile_id),
                    source_language=session.language or source_language, dub_language=project.dub_language,
                    source_text=event.original_text or event.processed_text or "",
                    dub_text=text_for_dub or "", direction="", emotion=event.emotion,
                    emotion_intensity=0.7, editorial_status=event.review_status,
                    created_at=event.created_at or now, updated_at=event.updated_at or now,
                )
                occurrence = DialogueOccurrence(
                    occurrence_id=occurrence_id, line_id=line_id, scene_id=None, route_id=None,
                    local_sequence=event.sequence, condition_json={}, target_duration_ms=target_ms,
                    runtime_trigger_id=None, runtime_trigger_type=None,
                    status="unmapped", created_at=now,
                )
                link = CaptureLink(
                    capture_link_id=new_id("CAP"), occurrence_id=occurrence_id,
                    traversal_id=None, session_id=event.session_id, event_id=event.event_id,
                    source_start_time=event.start_time, source_end_time=event.end_time,
                    confidence=event.confidence, is_primary=True, created_at=now,
                )
                db.add_all([line, occurrence, link])
                created_lines += 1
        return {"lines_created": created_lines, "characters_imported": created_characters}

    def sync_reviewed_event_character(self, event_id: str, previous_profile_id: str | None,
                                      previous_character_name: str | None,
                                      previous_roi_id: str | None, previous_event: dict | None = None,
                                      sync_character: bool = True, sync_emotion: bool = False,
                                      sync_text: bool = False) -> None:
        """Keep linked Dubbing lines aligned with Capture edits, preserving intentional dub adaptations."""
        now = utc_now()
        with self.store.Session.begin() as db:
            event = db.get(EventRecord, event_id)
            if event is None:
                return
            session = db.get(SessionRecord, event.session_id)
            if session is None or session.project_id != self.project_id:
                return

            target_character = None
            legacy = None
            if sync_character:
                legacy = db.get(RoiCharacterRecord, event.character_profile_id) if event.character_profile_id else None
                if legacy and legacy.project_id != self.project_id:
                    legacy = None
                if legacy:
                    target_character = db.scalar(select(DubbingCharacter).where(
                        DubbingCharacter.project_id == self.project_id,
                        DubbingCharacter.legacy_character_profile_id == legacy.character_id,
                    ))
                    if target_character is None:
                        target_character = DubbingCharacter(
                            character_id=new_id("CHR"), project_id=self.project_id,
                            legacy_character_profile_id=legacy.character_id,
                            name=legacy.name[:120], display_name=(legacy.alias or legacy.name)[:120], aliases_json=[],
                            notes=legacy.notes or "Importado do cadastro de ROI; confira se o texto é realmente um personagem.",
                            name_verified=False, active=bool(legacy.active),
                            created_at=legacy.created_at or now, updated_at=now,
                        )
                        db.add(target_character)
                        db.flush()
                    else:
                        target_character.name = legacy.name[:120]
                        target_character.display_name = (legacy.alias or legacy.name)[:120]
                        target_character.active = bool(legacy.active)
                        target_character.updated_at = now

                old_profiles = []
                if previous_profile_id:
                    previous = db.get(RoiCharacterRecord, previous_profile_id)
                    if previous and previous.project_id == self.project_id:
                        old_profiles.append(previous)
                elif previous_character_name:
                    roi_id = previous_roi_id or self.store._single_session_roi_id(db, event.session_id)
                    if roi_id:
                        candidates = db.scalars(select(RoiCharacterRecord).where(
                            RoiCharacterRecord.project_id == self.project_id,
                            RoiCharacterRecord.roi_id == roi_id,
                        )).all()
                        old_key = normalize_text(previous_character_name)
                        old_profiles.extend(profile for profile in candidates
                                            if normalize_text(profile.name) == old_key
                                            or normalize_text(profile.alias or "") == old_key)

                for previous in old_profiles:
                    if previous.active or (legacy and previous.character_id == legacy.character_id):
                        continue
                    old_character = db.scalar(select(DubbingCharacter).where(
                        DubbingCharacter.project_id == self.project_id,
                        DubbingCharacter.legacy_character_profile_id == previous.character_id,
                    ))
                    if old_character:
                        old_character.active = False
                        old_character.updated_at = now

            previous_event = previous_event or {}
            previous_dub_text = (previous_event.get("corrected_text")
                                 or previous_event.get("processed_text")
                                 or previous_event.get("original_text") or "")
            current_dub_text = event.corrected_text or event.processed_text or event.original_text or ""
            previous_source_text = previous_event.get("original_text") or previous_event.get("processed_text") or ""
            current_source_text = event.original_text or event.processed_text or ""

            links = db.scalars(select(CaptureLink).where(CaptureLink.event_id == event_id)).all()
            for link in links:
                occurrence = db.get(DialogueOccurrence, link.occurrence_id)
                line = db.get(DialogueLine, occurrence.line_id) if occurrence else None
                if not line or line.project_id != self.project_id:
                    continue
                if previous_event:
                    if line.source_text.strip() == previous_source_text.strip():
                        line.source_text = current_source_text
                    # An explicit Capture text edit is authoritative for linked lines.
                    # When only review metadata changed, retain intentional Dubbing edits.
                    if sync_text or line.dub_text.strip() in {
                            previous_dub_text.strip(), previous_source_text.strip()}:
                        line.dub_text = current_dub_text
                    line.editorial_status = event.review_status
                if sync_character:
                    line.character_id = target_character.character_id if target_character else None
                if sync_emotion:
                    line.emotion = event.emotion
                line.updated_at = now

    def project(self) -> dict:
        with self.store.Session() as db:
            row = db.get(DubbingProject, self.project_id)
            return model_dict(row) if row else {}

    def sessions(self) -> list[dict]:
        with self.store.Session() as db:
            rows = db.scalars(select(SessionRecord).where(
                SessionRecord.project_id == self.project_id,
            ).order_by(SessionRecord.created_at.desc())).all()
            result = []
            for row in rows:
                count = db.scalar(select(func.count(CaptureLink.capture_link_id)).where(
                    CaptureLink.session_id == row.session_id,
                )) or 0
                result.append({
                    "session_id": row.session_id, "source_type": row.source_type, "state": row.state,
                    "source_start": row.source_start, "duration": row.requested_duration,
                    "language": row.language, "created_at": json_value(row.created_at), "line_count": int(count),
                })
            return result

    def overview(self, session_id: str | None = None) -> dict:
        with self.store.Session() as db:
            project = db.get(DubbingProject, self.project_id)
            sessions = self.sessions()
            chosen_session = session_id or next((item["session_id"] for item in sessions
                                                 if item["state"] == "complete"),
                                                sessions[0]["session_id"] if sessions else None)
            rows = db.execute(
                select(DialogueLine.editorial_status, func.count(DialogueLine.line_id))
                .join(DialogueOccurrence, DialogueOccurrence.line_id == DialogueLine.line_id)
                .join(CaptureLink, CaptureLink.occurrence_id == DialogueOccurrence.occurrence_id)
                .where(DialogueLine.project_id == self.project_id,
                       CaptureLink.session_id == chosen_session if chosen_session else True)
                .group_by(DialogueLine.editorial_status)
            ).all()
            editorial = Counter({str(status): int(count) for status, count in rows})
            counts = db.execute(
                select(AudioTake.status, func.count(AudioTake.take_id))
                .join(DialogueOccurrence, DialogueOccurrence.occurrence_id == AudioTake.occurrence_id)
                .join(CaptureLink, CaptureLink.occurrence_id == DialogueOccurrence.occurrence_id)
                .where(CaptureLink.session_id == chosen_session if chosen_session else True)
                .group_by(AudioTake.status)
            ).all()
            take_counts = Counter({str(status): int(count) for status, count in counts})
            missing_voice = db.execute(
                select(DialogueLine.character_id, func.count(DialogueLine.line_id))
                .join(DialogueOccurrence, DialogueOccurrence.line_id == DialogueLine.line_id)
                .join(CaptureLink, CaptureLink.occurrence_id == DialogueOccurrence.occurrence_id)
                .outerjoin(VoiceProfile, VoiceProfile.character_id == DialogueLine.character_id)
                .where(CaptureLink.session_id == chosen_session if chosen_session else True,
                       DialogueLine.editorial_status.in_(("accepted", "corrected")),
                       (DialogueLine.character_id.is_(None) | VoiceProfile.voice_profile_id.is_(None)))
                .group_by(DialogueLine.character_id)
            ).all()
            total = sum(editorial.values())
            eligible = editorial["accepted"] + editorial["corrected"]
            return {
                "project": model_dict(project) if project else {},
                "sessions": sessions,
                "session_id": chosen_session,
                "summary": {
                    "total": total, "eligible": eligible,
                    "pending": editorial["pending"], "discarded": editorial["discarded"],
                    "generated": take_counts["generated"] + take_counts["review"] + take_counts["approved"],
                    "review": take_counts["review"], "approved": take_counts["approved"],
                    "errors": take_counts["error"], "queued": take_counts["queued"] + take_counts["generating"],
                    "eligible_missing_voice": sum(int(value) for _, value in missing_voice),
                },
                "characters": self.characters(),
                "presets": self.presets(),
            }

    def characters(self) -> list[dict]:
        with self.store.Session() as db:
            rows = db.scalars(select(DubbingCharacter).where(
                DubbingCharacter.project_id == self.project_id,
            ).order_by(DubbingCharacter.active.desc(), DubbingCharacter.display_name)).all()
            result = []
            for character in rows:
                legacy = (db.get(RoiCharacterRecord, character.legacy_character_profile_id)
                          if character.legacy_character_profile_id else None)
                # Only characters registered in an active Capture ROI belong in Dubbing.
                # Orphaned legacy records are hidden, not deleted, to preserve voice and take history.
                if not legacy or not legacy.active or not character.active:
                    continue
                profiles = db.scalars(select(VoiceProfile).where(
                    VoiceProfile.character_id == character.character_id,
                ).order_by(VoiceProfile.updated_at.desc())).all()
                items = []
                for profile in profiles:
                    reference = db.get(VoiceReferenceAsset, profile.reference_asset_id) if profile.reference_asset_id else None
                    ready = self._profile_ready(profile, reference)
                    items.append({**model_dict(profile), "ready": ready,
                                  "reference": model_dict(reference) if reference else None})
                photo_version = (str(int(legacy.updated_at.timestamp() * 1000))
                                 if legacy and legacy.photo_name and legacy.updated_at else "")
                result.append({**model_dict(character),
                               "display_name": (legacy.alias if legacy and legacy.alias else character.display_name),
                               "roi_alias": legacy.alias if legacy else "",
                               "color": legacy.color if legacy else "",
                               "photo_url": (f"/api/roi-characters/{legacy.character_id}/photo?v={photo_version}"
                                             if legacy and legacy.photo_name else None),
                               "photo_name": legacy.photo_name if legacy else None,
                               "sample_audio_name": legacy.sample_mp3_name if legacy else None,
                               "sample_audio_available": bool(legacy and legacy.sample_mp3_path),
                               "sample_audio_bytes": legacy.sample_mp3_bytes if legacy else None,
                               "sample_mp3_name": legacy.sample_mp3_name if legacy else None,
                               "sample_mp3_bytes": legacy.sample_mp3_bytes if legacy else None,
                               "sample_mp3_available": bool(legacy and legacy.sample_mp3_path),
                               "legacy_character_profile_id": character.legacy_character_profile_id,
                               "roi_id": legacy.roi_id,
                               "voice_profiles": items,
                               "voice_status": "ready" if any(item["ready"] for item in items)
                               else "needs_review" if not character.name_verified else "no_voice"})
            return result

    def _roi_audio_path(self, db, character: DubbingCharacter | None, *, require_verified: bool = False) -> Path:
        if not character or character.project_id != self.project_id or not character.active:
            raise DubbingError("character_not_found", "Personagem ativo não encontrado.", 404)
        if require_verified and not character.name_verified:
            raise DubbingError("character_needs_review", "Confira o nome do personagem antes de usar o áudio anexado.", 409)
        legacy = (db.get(RoiCharacterRecord, character.legacy_character_profile_id)
                  if character.legacy_character_profile_id else None)
        if not legacy or not legacy.sample_mp3_path:
            raise DubbingError("roi_audio_missing", "Este personagem não tem referência MP3 ou WAV anexada no cadastro da ROI.", 404)
        source = Path(legacy.sample_mp3_path).resolve()
        if not source.is_relative_to(self.data_root):
            raise DubbingError("invalid_roi_audio", "O áudio anexado não está dentro dos dados locais do DubSync.", 400)
        if source.suffix.lower() not in {".mp3", ".wav"} or not source.is_file():
            raise DubbingError("roi_audio_missing", "O arquivo MP3 ou WAV anexado não está disponível.", 404)
        if source.stat().st_size <= 0 or source.stat().st_size > MAX_REFERENCE_BYTES:
            raise DubbingError("invalid_roi_audio_size", "O áudio deve ter até 50 MiB e não pode estar vazio.", 413)
        return source

    def roi_audio_path(self, character_id: str) -> Path:
        """Return a validated local ROI attachment for in-app playback."""
        with self.store.Session() as db:
            return self._roi_audio_path(db, db.get(DubbingCharacter, character_id))

    def import_roi_voice_reference(self, character_id: str, voice_profile_id: str | None = None,
                                   reference_text: str | None = None) -> dict:
        """Synchronize an ROI character's attached audio as a local clone reference."""
        with self.store.Session() as db:
            character = db.get(DubbingCharacter, character_id)
            source = self._roi_audio_path(db, character, require_verified=True)
            source_hash = sha256_file(source)
            profiles = db.scalars(select(VoiceProfile).where(
                VoiceProfile.character_id == character_id,
                VoiceProfile.voice_mode == "clone",
                VoiceProfile.active.is_(True),
            ).order_by(VoiceProfile.updated_at.desc())).all()
            inactive_target = False
            if voice_profile_id:
                target = db.get(VoiceProfile, voice_profile_id)
                if not target or target.character_id != character_id or target.voice_mode != "clone":
                    raise DubbingError("voice_profile_not_found", "Perfil de clonagem não encontrado para este personagem.", 404)
                profiles = [target] if target.active else []
                inactive_target = not target.active
            roi_reference_profile_id = None
            reusable_profile_id = None
            unchanged_profile_id = None
            unchanged_reference_id = None
            for profile in profiles:
                reference = (db.get(VoiceReferenceAsset, profile.reference_asset_id)
                             if profile.reference_asset_id else None)
                if reference and reference.source_type in {"roi_character_mp3", "roi_character_audio"}:
                    if f"source_sha256={source_hash}" in (reference.source_uri_or_note or ""):
                        unchanged_profile_id = profile.voice_profile_id
                        unchanged_reference_id = reference.reference_id
                        break
                    if roi_reference_profile_id is None:
                        roi_reference_profile_id = profile.voice_profile_id
                elif reference is None and reusable_profile_id is None:
                    reusable_profile_id = profile.voice_profile_id
                elif voice_profile_id:
                    reusable_profile_id = profile.voice_profile_id
            # Prefer updating the existing ROI-derived clone profile so a new
            # recording does not create a duplicate just because another empty
            # clone profile happens to be newer.
            replace_roi_reference = roi_reference_profile_id is not None
            profile_id = voice_profile_id or roi_reference_profile_id or reusable_profile_id
            display_name = character.display_name or character.name
            language = (db.get(DubbingProject, self.project_id).dub_language
                        if db.get(DubbingProject, self.project_id) else "pt-BR")

        if unchanged_profile_id:
            if reference_text is not None:
                with self.store.Session.begin() as db:
                    reference = db.get(VoiceReferenceAsset, unchanged_reference_id)
                    profile = db.get(VoiceProfile, unchanged_profile_id)
                    if reference and profile:
                        reference.reference_text = str(reference_text).strip()[:4000] or None
                        profile.reference_text = reference.reference_text
                        profile.updated_at = utc_now()
            return self.profile(unchanged_profile_id)
        if inactive_target and voice_profile_id:
            return self.profile(voice_profile_id)

        if not profile_id:
            profile = self.save_voice_profile({
                "character_id": character_id,
                "name": f"{display_name[:96]} · clonagem",
                "language": language,
                "voice_mode": "clone",
                "default_speed": 1.0,
                "default_instruct": "",
                "use_authorized": False,
            }, auto_sync_roi=False)
            profile_id = profile["voice_profile_id"]

        reference_id = new_id("REF")
        reference_dir = self.project_data_root / "voices" / profile_id / "refs"
        original = reference_dir / f"{reference_id}_roi{source.suffix.lower()}"
        normalized = reference_dir / f"{reference_id}.wav"
        reference_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, original)
            metadata = normalize_reference(original, normalized)
            if replace_roi_reference or (voice_profile_id is not None and profile_id is not None):
                with self.store.Session.begin() as db:
                    profile = db.get(VoiceProfile, profile_id)
                    if profile and profile.reference_asset_id:
                        profile.use_authorized = False
                        profile.status = "needs_authorization"
                        profile.updated_at = utc_now()
            self.add_reference(profile_id, original, normalized, metadata, reference_text, "roi_character_audio",
                               f"character_id={character_id}; source_file={source.name}; source_bytes={source.stat().st_size}; source_sha256={source_hash}")
        except Exception:
            original.unlink(missing_ok=True)
            normalized.unlink(missing_ok=True)
            raise
        return self.profile(profile_id)

    @staticmethod
    def _profile_ready(profile: VoiceProfile, reference: VoiceReferenceAsset | None) -> bool:
        if not profile.active or not profile.use_authorized:
            return False
        if profile.voice_mode == "design":
            # Design instructions can be auditioned, but cannot lock a repeatable
            # speaker identity for production takes.
            return False
        return bool(reference and reference.quality_status == "ready" and reference.normalized_path)

    def _line_rows(self, db, session_id: str | list[str] | None = None):
        statement = (
            select(DialogueLine, DialogueOccurrence, CaptureLink, EventRecord, SessionRecord, DubbingCharacter)
            .join(DialogueOccurrence, DialogueOccurrence.line_id == DialogueLine.line_id)
            .join(CaptureLink, CaptureLink.occurrence_id == DialogueOccurrence.occurrence_id)
            .join(EventRecord, EventRecord.event_id == CaptureLink.event_id)
            .join(SessionRecord, SessionRecord.session_id == CaptureLink.session_id)
            .outerjoin(DubbingCharacter, DubbingCharacter.character_id == DialogueLine.character_id)
            .where(DialogueLine.project_id == self.project_id)
            .order_by(CaptureLink.source_start_time, EventRecord.sequence)
        )
        if isinstance(session_id, (list, tuple, set)):
            statement = statement.where(CaptureLink.session_id.in_(list(session_id) or [""]))
        elif session_id:
            statement = statement.where(CaptureLink.session_id == session_id)
        return db.execute(statement).all()

    def _conflicts(self, rows) -> dict[str, list[str]]:
        grouped = defaultdict(list)
        for line, occurrence, link, event, session, character in rows:
            normalized = normalize_text(line.dub_text or line.source_text)
            if not normalized:
                continue
            source = session.source_uri.strip().casefold()
            if not source:
                continue
            grouped[(source, normalized)].append((occurrence.occurrence_id, link, session))
        result = defaultdict(list)
        for entries in grouped.values():
            entries.sort(key=lambda item: item[1].source_start_time)
            for index, (occurrence_id, link, session) in enumerate(entries):
                matches = [other_id for other_id, other_link, other_session in entries[index + 1:]
                           if other_session.session_id != session.session_id
                           and abs(other_link.source_start_time - link.source_start_time) <= 2.0]
                if matches:
                    result[occurrence_id].extend(matches)
                    for other_id in matches:
                        result[other_id].append(occurrence_id)
        return {key: sorted(set(value)) for key, value in result.items()}

    def list_lines(self, session_id: str | list[str] | None = None, status: str = "not-discarded",
                   character_id: str | None = None, query: str = "") -> list[dict]:
        with self.store.Session() as db:
            rows = self._line_rows(db, session_id)
            conflict_rows = self._line_rows(db) if session_id else rows
            conflicts = self._conflicts(conflict_rows)
            occurrence_ids = list({occurrence.occurrence_id for _, occurrence, *_ in rows})
            take_rows = db.scalars(select(AudioTake).where(
                AudioTake.occurrence_id.in_(occurrence_ids or [""]),
            ).order_by(AudioTake.version.desc())).all()
            takes_by_occurrence = defaultdict(list)
            for take in take_rows:
                takes_by_occurrence[take.occurrence_id].append(model_dict(take))
            profiles = db.scalars(select(VoiceProfile).order_by(VoiceProfile.updated_at.desc())).all()
            profiles_by_character = defaultdict(list)
            for profile in profiles:
                profiles_by_character[profile.character_id].append(profile)

            result = []
            term = normalize_text(query) if query else ""
            for line, occurrence, link, event, session, character in rows:
                takes = takes_by_occurrence[occurrence.occurrence_id]
                if status == "not-discarded" and line.editorial_status == "discarded":
                    continue
                if status == "eligible" and line.editorial_status not in {"accepted", "corrected"}:
                    continue
                if status == "pending" and line.editorial_status != "pending":
                    continue
                if status == "discarded" and line.editorial_status != "discarded":
                    continue
                if status == "without-take" and takes:
                    continue
                if status == "review" and not any(t["status"] == "review" for t in takes):
                    continue
                if status == "approved" and not any(t["status"] == "approved" for t in takes):
                    continue
                if status == "error" and not any(t["status"] == "error" for t in takes):
                    continue
                if character_id and line.character_id != character_id:
                    continue
                searchable = normalize_text(" ".join((line.source_text or "", line.dub_text or "",
                    character.display_name if character else event.character or "", occurrence.occurrence_id)))
                if term and term not in searchable:
                    continue
                profile_items = profiles_by_character.get(line.character_id, []) if line.character_id else []
                profile = None
                if character and character.default_voice_profile_id:
                    assigned = db.get(VoiceProfile, character.default_voice_profile_id)
                    if assigned and assigned.active:
                        profile = assigned
                if profile is None:
                    profile = next((item for item in profile_items if item.active), None)
                reference = db.get(VoiceReferenceAsset, profile.reference_asset_id) if profile and profile.reference_asset_id else None
                result.append({
                    "line_id": line.line_id, "occurrence_id": occurrence.occurrence_id,
                    "capture_link_id": link.capture_link_id, "session_id": session.session_id,
                    "source_start_time": link.source_start_time, "source_end_time": link.source_end_time,
                    "target_duration_ms": occurrence.target_duration_ms, "source_language": line.source_language,
                    "dub_language": line.dub_language, "source_text": line.source_text, "dub_text": line.dub_text,
                    "emotion": line.emotion, "emotion_intensity": line.emotion_intensity,
                    "direction": line.direction, "editorial_status": line.editorial_status,
                    "character_id": line.character_id,
                    "character_name": character.display_name if character else (event.character or "Sem personagem"),
                    "character_verified": bool(character and character.name_verified),
                    "voice_profile_id": profile.voice_profile_id if profile else None,
                    "voice_profile_name": profile.name if profile else None,
                    "voice_ready": bool(profile and self._profile_ready(profile, reference)),
                    "take_count": len(takes), "takes": takes,
                    "conflict_occurrence_ids": conflicts.get(occurrence.occurrence_id, []),
                    "event": model_dict(event),
                    "session": {"session_id": session.session_id, "state": session.state,
                                "source_start": session.source_start, "duration": session.requested_duration,
                                "created_at": json_value(session.created_at)},
                })
            return result

    def line_detail(self, line_id: str, occurrence_id: str | None = None) -> dict:
        rows = [item for item in self.list_lines(status="all") if item["line_id"] == line_id
                and (occurrence_id is None or item["occurrence_id"] == occurrence_id)]
        if not rows:
            raise DubbingError("line_not_found", "Fala/ocorrência não encontrada.", 404)
        primary = rows[0]
        primary["capture_evidence"] = [{
            "session_id": row["session_id"], "source_start_time": row["source_start_time"],
            "source_end_time": row["source_end_time"], "conflict_occurrence_ids": row["conflict_occurrence_ids"],
        } for row in rows]
        return primary

    def update_line(self, line_id: str, changes: dict) -> dict:
        allowed = {"dub_text", "character_id", "emotion", "emotion_intensity", "direction"}
        if set(changes) - allowed:
            raise DubbingError("invalid_fields", "A atualização contém campos não editáveis.")
        with self.store.Session.begin() as db:
            line = db.get(DialogueLine, line_id)
            if not line or line.project_id != self.project_id:
                raise DubbingError("line_not_found", "Fala não encontrada.", 404)
            if "dub_text" in changes:
                text = str(changes["dub_text"] or "").strip()
                if not text or len(text) > 4000:
                    raise DubbingError("invalid_text", "O texto de dublagem precisa ter de 1 a 4.000 caracteres.")
                line.dub_text = text
            if "character_id" in changes:
                character_id = changes["character_id"]
                if character_id is not None:
                    character = db.get(DubbingCharacter, str(character_id))
                    if not character or character.project_id != self.project_id or not character.active:
                        raise DubbingError("character_not_found", "Selecione um personagem ativo deste projeto.")
                line.character_id = character_id
            if "emotion" in changes:
                line.emotion = str(changes["emotion"] or "")[:80] or None
            if "emotion_intensity" in changes:
                value = float(changes["emotion_intensity"])
                if not 0 <= value <= 1:
                    raise DubbingError("invalid_intensity", "A intensidade deve ficar entre 0 e 1.")
                line.emotion_intensity = value
            if "direction" in changes:
                line.direction = str(changes["direction"] or "")[:2000]
            line.updated_at = utc_now()
        return self.line_detail(line_id)

    def save_character(self, body: dict, character_id: str | None = None) -> dict:
        if not character_id:
            raise DubbingError("roi_registry_only", "Cadastre personagens pela ROI na tela Captura.", 409)
        with self.store.Session.begin() as db:
            row = db.get(DubbingCharacter, character_id)
            if not row or row.project_id != self.project_id:
                raise DubbingError("character_not_found", "Personagem não encontrado.", 404)
            legacy = (db.get(RoiCharacterRecord, row.legacy_character_profile_id)
                      if row.legacy_character_profile_id else None)
            if not legacy or not legacy.active:
                raise DubbingError("character_not_found", "Este personagem não está ativo no cadastro da ROI.", 404)
            row.name = legacy.name[:120]
            row.display_name = (legacy.alias or legacy.name)[:120]
            row.name_verified = bool(body.get("name_verified", False))
            row.active = True
            row.updated_at = utc_now()
            saved_id = row.character_id
        return next(item for item in self.characters() if item["character_id"] == saved_id)

    def assign_character_voice(self, character_id: str, voice_profile_id: str | None) -> dict:
        with self.store.Session.begin() as db:
            character = db.get(DubbingCharacter, character_id)
            if not character or character.project_id != self.project_id or not character.active:
                raise DubbingError("character_not_found", "Personagem ativo não encontrado.", 404)
            if not character.name_verified:
                raise DubbingError("character_needs_review", "Confira o nome do personagem antes de associar uma voz.", 409)
            profile = db.get(VoiceProfile, voice_profile_id) if voice_profile_id else None
            if voice_profile_id and (not profile or not profile.active):
                raise DubbingError("voice_profile_not_found", "Escolha um perfil vocal ativo.", 404)
            if profile:
                owner = db.get(DubbingCharacter, profile.character_id)
                if not owner or owner.project_id != self.project_id:
                    raise DubbingError("voice_profile_not_found", "O perfil não pertence a este projeto.", 404)
            character.default_voice_profile_id = profile.voice_profile_id if profile else None
            character.updated_at = utc_now()
            reused_by = []
            if profile:
                reused_by = [row.display_name for row in db.scalars(select(DubbingCharacter).where(
                    DubbingCharacter.project_id == self.project_id,
                    DubbingCharacter.default_voice_profile_id == profile.voice_profile_id,
                    DubbingCharacter.character_id != character_id,
                    DubbingCharacter.active.is_(True),
                )).all()]
            assigned_id = profile.voice_profile_id if profile else None
        return {"character_id": character_id, "voice_profile_id": assigned_id,
                "already_used_by": reused_by}

    def save_voice_profile(self, body: dict, voice_profile_id: str | None = None,
                           *, auto_sync_roi: bool = True) -> dict:
        character_id = str(body.get("character_id") or "")
        name = str(body.get("name") or "").strip()
        mode = str(body.get("voice_mode") or "clone")
        language = str(body.get("language") or "pt-BR").strip()
        if mode not in {"clone", "design"}:
            raise DubbingError("invalid_voice_mode", "Escolha um modo de voz válido.")
        if not name or len(name) > 120:
            raise DubbingError("invalid_voice_name", "Informe um nome para o perfil de voz.")
        speed = float(body.get("default_speed", 1.0))
        if not 0.5 <= speed <= 2.0:
            raise DubbingError("invalid_speed", "A velocidade deve ficar entre 0,5× e 2×.")
        instruct = str(body.get("default_instruct") or "")[:2000]
        preset_key = str(body.get("preset_key") or "").strip()[:160] or None
        preview_text = str(body.get("preview_text") or "")[:1000]
        generation_settings = normalize_generation_settings(body.get("generation_settings", {}))
        authorized = bool(body.get("use_authorized", False))
        with self.store.Session.begin() as db:
            character = db.get(DubbingCharacter, character_id)
            if not character or character.project_id != self.project_id or not character.active:
                raise DubbingError("character_not_found", "Personagem ativo não encontrado.")
            if not character.name_verified:
                raise DubbingError("character_needs_review", "Confirme o nome do personagem antes de associar uma voz.", 409)
            profile = db.get(VoiceProfile, voice_profile_id) if voice_profile_id else None
            if voice_profile_id and (not profile or profile.character_id != character_id):
                raise DubbingError("voice_profile_not_found", "Perfil vocal não encontrado para este personagem.", 404)
            if profile is None:
                profile = VoiceProfile(
                    voice_profile_id=new_id("VP"), character_id=character_id, language=language,
                    name=name, provider="omnivoice", provider_voice_key=None, preset_key=preset_key, voice_mode=mode,
                    reference_asset_id=None, reference_text=None, preview_text=preview_text, default_speed=speed,
                    default_instruct=instruct, generation_settings_json=generation_settings,
                    use_authorized=authorized, active=True, status="missing_reference",
                    created_at=utc_now(), updated_at=utc_now(),
                )
                db.add(profile)
            else:
                profile.name, profile.language, profile.voice_mode = name, language, mode
                profile.default_speed, profile.default_instruct = speed, instruct
                profile.generation_settings_json = generation_settings
                profile.preset_key = preset_key
                profile.use_authorized = authorized
                profile.preview_text = preview_text
                profile.updated_at = utc_now()
            if not profile.active:
                profile.status = "inactive"
            elif mode == "design":
                profile.status = "needs_voice_candidate" if authorized and instruct else (
                    "needs_voice_attributes" if authorized else "needs_authorization")
            elif not authorized:
                profile.status = "needs_authorization"
            elif not profile.reference_asset_id:
                profile.status = "missing_reference"
            else:
                ref = db.get(VoiceReferenceAsset, profile.reference_asset_id)
                profile.status = "ready" if ref and ref.quality_status == "ready" else "needs_reference_review"
            saved_id = profile.voice_profile_id
            saved_active = profile.active
            official_candidate = profile.provider_voice_key == "dubsync:design-candidate"
        if mode == "clone" and saved_active and auto_sync_roi and not official_candidate:
            try:
                return self.import_roi_voice_reference(character_id, saved_id, body.get("reference_text"))
            except DubbingError as error:
                if error.code != "roi_audio_missing":
                    raise
        return self.profile(saved_id)

    def set_voice_profile_active(self, voice_profile_id: str, active: bool) -> dict:
        with self.store.Session.begin() as db:
            profile = db.get(VoiceProfile, voice_profile_id)
            if not profile:
                raise DubbingError("voice_profile_not_found", "Perfil vocal não encontrado.", 404)
            profile.active = bool(active)
            profile.updated_at = utc_now()
            reference = db.get(VoiceReferenceAsset, profile.reference_asset_id) if profile.reference_asset_id else None
            if not profile.active:
                profile.status = "inactive"
            elif profile.voice_mode == "design":
                profile.status = "needs_voice_candidate" if profile.use_authorized and profile.default_instruct else (
                    "needs_voice_attributes" if profile.use_authorized else "needs_authorization")
            elif not profile.use_authorized:
                profile.status = "needs_authorization"
            elif not reference:
                profile.status = "missing_reference"
            else:
                profile.status = "ready" if reference.quality_status == "ready" else "needs_reference_review"
        return self.profile(voice_profile_id)

    def clear_profile_reference(self, voice_profile_id: str) -> dict:
        with self.store.Session.begin() as db:
            profile = db.get(VoiceProfile, voice_profile_id)
            if not profile:
                raise DubbingError("voice_profile_not_found", "Perfil vocal não encontrado.", 404)
            profile.reference_asset_id = None
            profile.reference_text = None
            if profile.provider_voice_key == "dubsync:design-candidate":
                profile.provider_voice_key = None
                profile.voice_mode = "design"
            profile.status = ("inactive" if not profile.active else
                              "needs_voice_candidate" if profile.voice_mode == "design" and profile.use_authorized
                              and profile.default_instruct else "missing_reference")
            profile.updated_at = utc_now()
        return self.profile(voice_profile_id)

    def delete_voice_profile(self, voice_profile_id: str) -> dict:
        files_to_remove: list[Path] = []
        with self.store.Session.begin() as db:
            profile = db.get(VoiceProfile, voice_profile_id)
            if not profile:
                raise DubbingError("voice_profile_not_found", "Perfil vocal não encontrado.", 404)
            takes = int(db.scalar(select(func.count(AudioTake.take_id)).where(
                AudioTake.voice_profile_id == voice_profile_id)) or 0)
            if takes:
                profile.active = False
                profile.status = "inactive"
                profile.updated_at = utc_now()
                result = {"deleted": False, "deactivated": True, "take_count": takes}
            else:
                assets = db.scalars(select(VoiceReferenceAsset).where(
                    VoiceReferenceAsset.voice_profile_id == voice_profile_id)).all()
                for asset in assets:
                    files_to_remove.extend((data_path(self.data_root, asset.original_path),
                                            data_path(self.data_root, asset.normalized_path)))
                    db.delete(asset)
                for character in db.scalars(select(DubbingCharacter).where(
                    DubbingCharacter.default_voice_profile_id == voice_profile_id)).all():
                    character.default_voice_profile_id = None
                db.delete(profile)
                result = {"deleted": True, "deactivated": False, "take_count": 0}
        for path in set(files_to_remove):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return result

    def add_reference(self, voice_profile_id: str, original: Path, normalized: Path,
                      metadata: dict, reference_text: str | None, source_type: str,
                      source_note: str | None) -> dict:
        with self.store.Session.begin() as db:
            profile = db.get(VoiceProfile, voice_profile_id)
            if not profile or not profile.active:
                raise DubbingError("voice_profile_not_found", "Perfil de voz não encontrado.", 404)
            asset = VoiceReferenceAsset(
                reference_id=new_id("REF"), voice_profile_id=voice_profile_id,
                original_path=original.relative_to(self.data_root).as_posix(),
                normalized_path=normalized.relative_to(self.data_root).as_posix(),
                duration_ms=int(metadata["duration_ms"]), sample_rate=int(metadata["sample_rate"]),
                channels=int(metadata["channels"]), codec=str(metadata["codec"]),
                reference_text=(reference_text or "").strip()[:4000] or None,
                source_type=source_type, source_uri_or_note=(source_note or "")[:500] or None,
                quality_status=metadata["quality_status"], sha256=str(metadata["sha256"]), created_at=utc_now(),
            )
            db.add(asset)
            profile.reference_asset_id = asset.reference_id
            profile.reference_text = asset.reference_text
            if source_type != "design_candidate" and profile.provider_voice_key == "dubsync:design-candidate":
                profile.provider_voice_key = None
            profile.status = "ready" if profile.use_authorized and asset.quality_status == "ready" else (
                "needs_authorization" if not profile.use_authorized else "needs_reference_review")
            profile.updated_at = utc_now()
            result = model_dict(asset)
        return result

    def select_design_candidate(self, voice_profile_id: str, original: Path, normalized: Path,
                                metadata: dict, reference_text: str | None) -> dict:
        profile = self.profile(voice_profile_id)
        if not profile["active"] or not profile["use_authorized"]:
            raise DubbingError("profile_not_authorized", "Ative o perfil e confirme a autorização antes de escolher uma voz.", 409)
        if profile["voice_mode"] != "design" or not profile.get("default_instruct", "").strip():
            raise DubbingError("profile_not_design", "Salve os atributos no modo Gerar voz antes de selecionar um candidato.", 409)
        duration_ms = int(metadata.get("duration_ms") or 0)
        if not 3000 <= duration_ms <= 10000:
            raise DubbingError("candidate_duration_invalid", "A voz candidata precisa ter entre 3 e 10 segundos para servir como identidade estável.", 422)
        reference = self.add_reference(
            voice_profile_id, original, normalized, metadata, reference_text,
            "design_candidate", "Candidato OmniVoice local escolhido como voz oficial.",
        )
        with self.store.Session.begin() as db:
            saved = db.get(VoiceProfile, voice_profile_id)
            if not saved:
                raise DubbingError("voice_profile_not_found", "Perfil de voz não encontrado.", 404)
            saved.voice_mode = "clone"
            saved.provider_voice_key = "dubsync:design-candidate"
            saved.status = "ready" if saved.active and saved.use_authorized and metadata.get("quality_status") == "ready" else "needs_reference_review"
            saved.updated_at = utc_now()
        return {"reference": reference, "profile": self.profile(voice_profile_id)}

    def approve_reference(self, reference_id: str) -> dict:
        with self.store.Session.begin() as db:
            reference = db.get(VoiceReferenceAsset, reference_id)
            if not reference:
                raise DubbingError("reference_not_found", "Referência vocal não encontrada.", 404)
            reference.quality_status = "ready"
            profile = db.get(VoiceProfile, reference.voice_profile_id)
            if profile and profile.reference_asset_id == reference.reference_id:
                profile.status = ("inactive" if not profile.active else
                                  "ready" if profile.use_authorized else "needs_authorization")
                profile.updated_at = utc_now()
            result = model_dict(reference)
        return result

    def profile(self, profile_id: str) -> dict:
        with self.store.Session() as db:
            profile = db.get(VoiceProfile, profile_id)
            if not profile:
                raise DubbingError("voice_profile_not_found", "Perfil de voz não encontrado.", 404)
            ref = db.get(VoiceReferenceAsset, profile.reference_asset_id) if profile.reference_asset_id else None
            return {**model_dict(profile), "ready": self._profile_ready(profile, ref),
                    "reference": model_dict(ref) if ref else None,
                    "character": model_dict(db.get(DubbingCharacter, profile.character_id))}

    def create_take(self, line_id: str, occurrence_id: str, voice_profile_id: str | None,
                    instruct: str, speed: float | None, override: bool = False,
                    generation_settings: dict | None = None, batch_id: str | None = None,
                    batch_mode: str = "individual") -> dict:
        with self.store.Session.begin() as db:
            line = db.get(DialogueLine, line_id)
            occurrence = db.get(DialogueOccurrence, occurrence_id)
            if not line or not occurrence or occurrence.line_id != line_id:
                raise DubbingError("line_not_found", "Fala ou ocorrência não encontrada.", 404)
            if line.editorial_status not in {"accepted", "corrected"} and not override:
                raise DubbingError("editorial_review_required", "Esta fala ainda não foi aprovada na Fase 1. Marque a liberação manual para continuar.", 409)
            character = db.get(DubbingCharacter, line.character_id) if line.character_id else None
            if not character:
                raise DubbingError("character_required", "Associe um personagem estável à fala antes de gerar.", 409)
            if not character.name_verified:
                raise DubbingError("character_needs_review", "Confira e confirme o nome do personagem primeiro.", 409)
            profile_id = voice_profile_id or character.default_voice_profile_id or db.scalar(select(VoiceProfile.voice_profile_id).where(
                VoiceProfile.character_id == character.character_id,
                VoiceProfile.active.is_(True),
            ).order_by(VoiceProfile.updated_at.desc()).limit(1))
            profile = db.get(VoiceProfile, profile_id) if profile_id else None
            profile_owner = db.get(DubbingCharacter, profile.character_id) if profile else None
            if not profile or not profile.active or not profile_owner or profile_owner.project_id != self.project_id:
                raise DubbingError("voice_profile_required", "Configure um perfil de voz para este personagem.", 409)
            reference = db.get(VoiceReferenceAsset, profile.reference_asset_id) if profile.reference_asset_id else None
            if not self._profile_ready(profile, reference):
                raise DubbingError("voice_profile_not_ready", "Autorize o perfil, gere candidatos e escolha uma voz oficial antes de criar takes.", 409)
            if not line.dub_text.strip():
                raise DubbingError("empty_text", "Informe o texto de dublagem antes de gerar.")
            resolved_speed = float(speed if speed is not None else profile.default_speed)
            if not 0.5 <= resolved_speed <= 2.0:
                raise DubbingError("invalid_speed", "A velocidade deve ficar entre 0,5× e 2×.")
            line_instruct = str(instruct or "").strip()
            if profile.voice_mode == "design":
                normalized_instruct = ", ".join(part for part in (
                    profile.default_instruct.strip(), line_instruct or self.default_instruction(
                        line.emotion, line.emotion_intensity, line.dub_language,
                    ),
                ) if part)
            elif profile.provider_voice_key == "dubsync:design-candidate":
                normalized_instruct = line_instruct or self.default_instruction(
                    line.emotion, line.emotion_intensity, line.dub_language,
                )
            else:
                normalized_instruct = line_instruct or profile.default_instruct or self.default_instruction(
                    line.emotion, line.emotion_intensity, line.dub_language,
                )
            resolved_generation_settings = normalize_generation_settings(
                generation_settings if generation_settings else profile.generation_settings_json or {})
            version = (db.scalar(select(func.max(AudioTake.version)).where(
                AudioTake.occurrence_id == occurrence_id,
            )) or 0) + 1
            take_id, job_id = new_id("TAKE"), new_id("JOB")
            now = utc_now()
            take = AudioTake(
                take_id=take_id, line_id=line_id, occurrence_id=occurrence_id,
                voice_profile_id=profile.voice_profile_id, provider="omnivoice", provider_version=None,
                model_name="k2-fsa/OmniVoice", model_revision=None, text_used=line.dub_text,
                instruct_used=normalized_instruct, speed_used=resolved_speed,
                generation_settings_json=resolved_generation_settings,
                target_duration_ms=occurrence.target_duration_ms, actual_duration_ms=None,
                duration_delta_ms=None, duration_delta_percent=None, audio_master_path=None,
                audio_preview_path=None, status="queued", version=version, error_message=None,
                created_at=now, approved_at=None,
            )
            job = TtsJob(
                job_id=job_id, take_id=take_id, provider_job_id=None, status="queued", priority=0,
                attempt=0, max_attempts=3, queued_at=now, started_at=None, finished_at=None,
                error_message=None, worker_id=None, batch_id=batch_id, batch_mode=batch_mode,
            )
            db.add_all([take, job])
            return {"take": model_dict(take), "job": model_dict(job)}

    @staticmethod
    def _video_identity(session: dict) -> str:
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

    def _validate_video_session_ids(self, session_id: str, session_ids: list[str] | None) -> list[str]:
        ids = list(dict.fromkeys(str(value) for value in (session_ids or []) if value))
        if session_id not in ids:
            ids.insert(0, session_id)
        if len(ids) > 5000:
            raise DubbingError("video_batch_too_large", "O lote ultrapassou o limite de blocos do vídeo.")
        base = self.store.session(session_id)
        if not base:
            raise DubbingError("session_not_found", "A sessão selecionada não foi encontrada.", 404)
        identity = self._video_identity(base)
        for candidate_id in ids:
            candidate = self.store.session(candidate_id)
            if not candidate or self._video_identity(candidate) != identity:
                raise DubbingError("video_batch_session_mismatch",
                                   "O lote só pode incluir blocos do mesmo vídeo e projeto.")
        return ids

    def block_dubbing_progress(self, session_ids: list[str], character_id: str | None = None) -> dict[str, dict]:
        ids = list(dict.fromkeys(str(value) for value in session_ids if value))
        result = {session_id: {"eligible": 0, "finished": 0, "queued": 0,
                               "generating": 0, "errors": 0, "percent": 0}
                  for session_id in ids}
        if not ids:
            return result
        statement = (select(CaptureLink.session_id, DialogueOccurrence.occurrence_id,
                            DialogueLine.editorial_status, AudioTake.status)
                     .join(DialogueOccurrence, DialogueOccurrence.occurrence_id == CaptureLink.occurrence_id)
                     .join(DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id)
                     .outerjoin(AudioTake, AudioTake.occurrence_id == DialogueOccurrence.occurrence_id)
                     .where(CaptureLink.session_id.in_(ids),
                            DialogueLine.project_id == self.project_id,
                            DialogueLine.editorial_status.in_(("accepted", "corrected"))))
        if character_id:
            statement = statement.where(DialogueLine.character_id == character_id)
        occurrences: dict[tuple[str, str], set[str]] = defaultdict(set)
        with self.store.Session() as db:
            for session_key, occurrence_id, _editorial_status, take_status in db.execute(statement):
                if session_key in result:
                    occurrences[(session_key, occurrence_id)].add(str(take_status or ""))
        complete_statuses = {"review", "generated", "approved", "completed"}
        for (session_key, _occurrence_id), statuses in occurrences.items():
            progress = result[session_key]
            progress["eligible"] += 1
            if statuses & complete_statuses:
                progress["finished"] += 1
            elif "generating" in statuses:
                progress["generating"] += 1
            elif "queued" in statuses:
                progress["queued"] += 1
            elif "error" in statuses:
                progress["errors"] += 1
        for progress in result.values():
            progress["percent"] = round(progress["finished"] * 100 / progress["eligible"], 1) if progress["eligible"] else 0
        return result

    def enqueue_batch(self, session_id: str, ignore_missing: bool = False, mode: str = "block",
                      occurrence_ids: list[str] | None = None, character_id: str | None = None,
                      session_ids: list[str] | None = None) -> dict:
        if mode not in {"selected", "character", "block", "pending", "video_character"}:
            raise DubbingError("invalid_batch_mode", "Escolha um escopo válido para a geração em lote.")
        batch_sessions = ([session_id] if mode != "video_character" else
                          self._validate_video_session_ids(session_id, session_ids))
        all_lines = self.list_lines(session_id=batch_sessions if mode == "video_character" else session_id,
                                    status="all", character_id=character_id if mode == "video_character" else None)
        if mode == "video_character":
            unique_lines = {}
            for row in all_lines:
                unique_lines.setdefault(row["occurrence_id"], row)
            all_lines = list(unique_lines.values())
        eligible = [row for row in all_lines if row["editorial_status"] in {"accepted", "corrected"}]
        already_done = 0
        already_active = 0
        if mode == "video_character":
            candidates_before_status = [row for row in eligible if row["character_id"] == character_id]
            candidates = []
            for row in candidates_before_status:
                statuses = {str(take.get("status") or "") for take in row.get("takes", [])}
                if statuses & {"review", "generated", "approved", "completed"}:
                    already_done += 1
                elif statuses & {"queued", "generating"}:
                    already_active += 1
                else:
                    candidates.append(row)
        else:
            candidates = eligible
        if mode == "selected":
            wanted = set(occurrence_ids or [])
            if not wanted:
                raise DubbingError("batch_selection_empty", "Marque uma ou mais falas na lista antes de gerar as selecionadas.")
            candidates = [row for row in eligible if row["occurrence_id"] in wanted]
        elif mode == "character":
            if not character_id:
                raise DubbingError("batch_character_required", "Selecione um personagem no Take Studio antes de gerar por personagem.")
            candidates = [row for row in eligible if row["character_id"] == character_id]
        elif mode == "video_character" and not character_id:
            raise DubbingError("batch_character_required", "Selecione um personagem antes de gerar todos os blocos do vídeo.")
        elif mode == "pending":
            candidates = [row for row in eligible if not row["take_count"]]
        else:
            if mode != "video_character":
                candidates = eligible
        if not candidates:
            return {"requires_resolution": False, "eligible": 0, "missing": [], "ready": 0,
                    "queued": 0, "batch_id": None, "mode": mode,
                    "already_done": already_done, "already_active": already_active}
        missing = []
        ready = []
        for item in candidates:
            reason = None
            if not item["character_id"]:
                reason = "Sem personagem estável"
            elif not item["character_verified"]:
                reason = "Nome de personagem precisa ser conferido"
            elif not item["voice_profile_id"] or not item["voice_ready"]:
                reason = "Sem perfil vocal pronto"
            if reason:
                key = item["character_id"] or f"unassigned:{item['character_name']}"
                existing = next((value for value in missing if value["key"] == key), None)
                if existing:
                    existing["count"] += 1
                    if reason not in existing["reasons"]:
                        existing["reasons"].append(reason)
                else:
                    missing.append({"key": key, "character_id": item["character_id"],
                                    "name": item["character_name"], "count": 1, "reasons": [reason]})
            else:
                ready.append(item)
        if missing and not ignore_missing:
            return {"requires_resolution": True, "eligible": len(candidates), "missing": missing,
                    "ready": len(ready), "queued": 0, "already_done": already_done,
                    "already_active": already_active}
        queued = []
        batch_id = new_id("BATCH") if ready else None
        for item in ready:
            queued.append(self.create_take(
                item["line_id"], item["occurrence_id"], item["voice_profile_id"],
                self.default_instruction(item["emotion"], item["emotion_intensity"], item["dub_language"]),
                None, False, batch_id=batch_id, batch_mode=mode,
            ))
        return {"requires_resolution": False, "eligible": len(candidates), "missing": missing,
                "ready": len(ready), "queued": len(queued), "batch_id": batch_id, "mode": mode,
                "already_done": already_done, "already_active": already_active,
                "occurrence_ids": [item["take"]["occurrence_id"] for item in queued]}

    def _session_occurrence_ids(self, db, session_id: str | None) -> set[str] | None:
        if not session_id:
            return None
        return set(db.scalars(select(CaptureLink.occurrence_id).where(
            CaptureLink.session_id == session_id,
        )).all())

    def queue_snapshot(self, session_id: str | None = None, batch_id: str | None = None) -> dict:
        with self.store.Session() as db:
            occurrence_ids = self._session_occurrence_ids(db, session_id)
            statement = (select(TtsJob, AudioTake, DialogueLine, DialogueOccurrence)
                         .join(AudioTake, AudioTake.take_id == TtsJob.take_id)
                         .join(DialogueLine, DialogueLine.line_id == AudioTake.line_id)
                         .join(DialogueOccurrence, DialogueOccurrence.occurrence_id == AudioTake.occurrence_id)
                         .where(DialogueLine.project_id == self.project_id)
                         .order_by(TtsJob.queued_at.desc()))
            if occurrence_ids is not None:
                statement = statement.where(AudioTake.occurrence_id.in_(occurrence_ids or [""]))
            rows = db.execute(statement).all()
            requested_batch_rows = ([row for row in db.execute(
                select(TtsJob, AudioTake, DialogueLine, DialogueOccurrence)
                .join(AudioTake, AudioTake.take_id == TtsJob.take_id)
                .join(DialogueLine, DialogueLine.line_id == AudioTake.line_id)
                .join(DialogueOccurrence, DialogueOccurrence.occurrence_id == AudioTake.occurrence_id)
                .where(TtsJob.batch_id == batch_id, DialogueLine.project_id == self.project_id)
                .order_by(TtsJob.queued_at.desc())).all()] if batch_id else [])
            if batch_id:
                batch_rows = requested_batch_rows
                latest_batch_row = batch_rows[0] if batch_rows else None
            else:
                latest_batch_row = next((row for row in rows if row[0].batch_id), None)
                latest_batch_id = latest_batch_row[0].batch_id if latest_batch_row else None
                batch_rows = [row for row in rows if latest_batch_id and row[0].batch_id == latest_batch_id]
            if latest_batch_row and latest_batch_row[0].batch_mode == "video_character":
                rows = batch_rows
            active = [row for row in rows if row[0].status in {"queued", "generating", "error"}]
            counts = Counter(row[0].status for row in active)
            latest_batch_id = latest_batch_row[0].batch_id if latest_batch_row else None
            batch_counts = Counter(row[0].status for row in batch_rows)
            batch_total = len(batch_rows)
            batch_finished = sum(batch_counts[status] for status in ("completed", "error", "cancelled"))
            batch = ({
                "batch_id": latest_batch_id, "mode": latest_batch_row[0].batch_mode,
                "total": batch_total, "completed": batch_counts["completed"],
                "generating": batch_counts["generating"], "queued": batch_counts["queued"],
                "errors": batch_counts["error"], "cancelled": batch_counts["cancelled"],
                "finished": batch_finished,
                "percent": round(batch_finished * 100 / batch_total) if batch_total else 0,
                "occurrences": [{
                    "occurrence_id": occurrence.occurrence_id, "status": job.status,
                } for job, _take, _line, occurrence in batch_rows],
            } if latest_batch_id else None)
            visible_rows = active + [row for row in batch_rows if row not in active]
            visible_rows.sort(key=lambda row: row[0].queued_at, reverse=True)
            items = [{
                "job_id": job.job_id, "take_id": take.take_id, "status": job.status,
                "attempt": job.attempt, "max_attempts": job.max_attempts,
                "character_id": line.character_id, "line_id": line.line_id,
                "occurrence_id": occurrence.occurrence_id,
                "text": take.text_used, "error_message": job.error_message,
                "queued_at": json_value(job.queued_at),
                "batch_id": job.batch_id,
            } for job, take, line, occurrence in visible_rows[:30]]
            return {"counts": dict(counts), "items": items, "batch": batch}

    def retry_errors(self, session_id: str | None = None, batch_id: str | None = None) -> dict:
        with self.store.Session.begin() as db:
            occurrence_ids = self._session_occurrence_ids(db, session_id)
            statement = select(TtsJob).where(TtsJob.status == "error")
            if batch_id:
                statement = (statement.join(AudioTake, AudioTake.take_id == TtsJob.take_id)
                             .join(DialogueLine, DialogueLine.line_id == AudioTake.line_id)
                             .where(TtsJob.batch_id == batch_id, DialogueLine.project_id == self.project_id))
            elif occurrence_ids is not None:
                statement = statement.join(AudioTake, AudioTake.take_id == TtsJob.take_id).where(
                    AudioTake.occurrence_id.in_(occurrence_ids or [""]),
                )
            jobs = db.scalars(statement).all()
            for job in jobs:
                job.status = "queued"
                job.attempt = 0
                job.started_at = None
                job.finished_at = None
                job.error_message = None
                job.provider_job_id = None
                take = db.get(AudioTake, job.take_id)
                if take:
                    take.status = "queued"
                    take.error_message = None
            return {"retried": len(jobs)}

    def cancel_pending(self, session_id: str | None = None, batch_id: str | None = None) -> dict:
        with self.store.Session.begin() as db:
            occurrence_ids = self._session_occurrence_ids(db, session_id)
            statement = select(TtsJob).where(TtsJob.status == "queued")
            if batch_id:
                statement = (statement.join(AudioTake, AudioTake.take_id == TtsJob.take_id)
                             .join(DialogueLine, DialogueLine.line_id == AudioTake.line_id)
                             .where(TtsJob.batch_id == batch_id, DialogueLine.project_id == self.project_id))
            elif occurrence_ids is not None:
                statement = statement.join(AudioTake, AudioTake.take_id == TtsJob.take_id).where(
                    AudioTake.occurrence_id.in_(occurrence_ids or [""]),
                )
            jobs = db.scalars(statement).all()
            for job in jobs:
                job.status = "cancelled"
                job.finished_at = utc_now()
                job.error_message = "Cancelado pelo usuário antes da geração."
                take = db.get(AudioTake, job.take_id)
                if take:
                    take.status = "cancelled"
                    take.error_message = job.error_message
            return {"cancelled": len(jobs)}

    @staticmethod
    def default_instruction(emotion: str | None, intensity: float, language: str) -> str:
        # OmniVoice 0.2.x only accepts a fixed set of English/Chinese voice tags
        # in `instruct`; prose such as the former Portuguese emotion prompt raises
        # ValueError and causes every batch item to be retried three times. Language
        # and the ROI's cloned reference already provide the speaking locale/style.
        # Keep emotion/intensity in the dialogue record, but don't send unsupported
        # free-form instructions to the model.
        return ""

    def next_queued_job(self, worker_id: str) -> dict | None:
        with self.store.Session.begin() as db:
            job = db.scalar(select(TtsJob).where(TtsJob.status == "queued")
                            .order_by(TtsJob.priority.desc(), TtsJob.queued_at).limit(1))
            if not job:
                return None
            now = utc_now()
            job.status = "generating"
            job.started_at = now
            job.worker_id = worker_id
            job.attempt += 1
            take = db.get(AudioTake, job.take_id)
            if take:
                take.status = "generating"
                take.error_message = None
            occurrence = db.get(DialogueOccurrence, take.occurrence_id) if take else None
            line = db.get(DialogueLine, take.line_id) if take else None
            profile = db.get(VoiceProfile, take.voice_profile_id) if take else None
            reference = db.get(VoiceReferenceAsset, profile.reference_asset_id) if profile and profile.reference_asset_id else None
            return {
                "job": model_dict(job), "take": model_dict(take) if take else None,
                "line": model_dict(line) if line else None,
                "project_id": line.project_id if line else self.project_id,
                "occurrence": model_dict(occurrence) if occurrence else None,
                "profile": model_dict(profile) if profile else None,
                "reference": model_dict(reference) if reference else None,
                "reference_path": str(data_path(self.data_root, reference.normalized_path)) if reference else None,
            }

    def has_queued_jobs(self) -> bool:
        with self.store.Session() as db:
            return db.scalar(select(TtsJob.job_id).where(TtsJob.status == "queued").limit(1)) is not None

    def set_provider_job(self, job_id: str, provider_job_id: str):
        with self.store.Session.begin() as db:
            job = db.get(TtsJob, job_id)
            if job:
                job.provider_job_id = provider_job_id[:80]

    def complete_job(self, job_id: str, take_path: Path, duration_ms: int) -> dict:
        with self.store.Session.begin() as db:
            job = db.get(TtsJob, job_id)
            if not job:
                raise DubbingError("job_not_found", "Job de geração não encontrado.", 404)
            take = db.get(AudioTake, job.take_id)
            if not take:
                raise DubbingError("take_not_found", "Take associado não encontrado.", 404)
            target = take.target_duration_ms
            delta = duration_ms - target if target and target > 0 else None
            take.actual_duration_ms = int(duration_ms)
            take.duration_delta_ms = delta
            take.duration_delta_percent = round(delta * 100 / target, 2) if delta is not None else None
            take.audio_master_path = take_path.relative_to(self.data_root).as_posix()
            take.status = "review"
            take.error_message = None
            job.status = "completed"
            job.finished_at = utc_now()
            job.error_message = None
            result = model_dict(take)
        return result

    def fail_job(self, job_id: str, message: str, retry: bool = True) -> dict:
        safe_message = str(message or "Falha na geração.")[:1000]
        with self.store.Session.begin() as db:
            job = db.get(TtsJob, job_id)
            if not job:
                return {"status": "missing"}
            take = db.get(AudioTake, job.take_id)
            if retry and job.attempt < job.max_attempts:
                job.status = "queued"
                job.error_message = safe_message
                job.started_at = None
                if take:
                    take.status = "queued"
                    take.error_message = safe_message
            else:
                job.status = "error"
                job.finished_at = utc_now()
                job.error_message = safe_message
                if take:
                    take.status = "error"
                    take.error_message = safe_message
            return {"status": job.status, "attempt": job.attempt,
                    "max_attempts": job.max_attempts, "error": safe_message}

    def interrupt_jobs(self):
        with self.store.Session.begin() as db:
            jobs = db.scalars(select(TtsJob).where(TtsJob.status == "generating")).all()
            for job in jobs:
                job.status = "error"
                job.finished_at = utc_now()
                job.error_message = "Servidor reiniciado durante a geração; crie outro take para tentar novamente."
                take = db.get(AudioTake, job.take_id)
                if take:
                    take.status = "error"
                    take.error_message = job.error_message

    def take_detail(self, take_id: str) -> dict:
        with self.store.Session() as db:
            take = db.get(AudioTake, take_id)
            if not take:
                raise DubbingError("take_not_found", "Take não encontrado.", 404)
            job = db.scalar(select(TtsJob).where(TtsJob.take_id == take_id).order_by(TtsJob.queued_at.desc()))
            return {"take": model_dict(take), "job": model_dict(job) if job else None}

    def review_take(self, take_id: str, action: str, note: str = "") -> dict:
        if action not in {"approve", "reject"}:
            raise DubbingError("invalid_review_action", "Ação de revisão inválida.")
        with self.store.Session.begin() as db:
            take = db.get(AudioTake, take_id)
            if not take or take.status not in {"review", "generated", "approved", "rejected"}:
                raise DubbingError("take_not_reviewable", "Este take ainda não está disponível para revisão.", 409)
            now = utc_now()
            if action == "approve":
                previous = db.scalars(select(AudioTake).where(
                    AudioTake.occurrence_id == take.occurrence_id,
                    AudioTake.status == "approved", AudioTake.take_id != take.take_id,
                )).all()
                for item in previous:
                    old = item.status
                    item.status = "review"
                    db.add(AudioTakeReview(take_id=item.take_id, occurrence_id=item.occurrence_id,
                                           action="approval_replaced", previous_status=old,
                                           new_status="review", note="Outro take foi aprovado para esta ocorrência.",
                                           created_at=now))
                old = take.status
                take.status, take.approved_at = "approved", now
                action_name = "approved"
            else:
                old = take.status
                take.status, take.approved_at = "rejected", None
                action_name = "rejected"
            db.add(AudioTakeReview(take_id=take.take_id, occurrence_id=take.occurrence_id,
                                   action=action_name, previous_status=old, new_status=take.status,
                                   note=str(note or "")[:1000], created_at=now))
            return model_dict(take)

    def presets(self) -> list[dict]:
        with self.store.Session() as db:
            return [model_dict(row) for row in db.scalars(select(InterpretationPreset).where(
                InterpretationPreset.project_id == self.project_id,
            ).order_by(InterpretationPreset.name)).all()]

    def save_preset(self, body: dict) -> dict:
        name, emotion, instruct = (str(body.get(key) or "").strip() for key in ("name", "emotion", "instruct"))
        intensity = float(body.get("intensity", 0.7))
        if not name or not emotion or not instruct or not 0 <= intensity <= 1:
            raise DubbingError("invalid_preset", "Informe nome, emoção, instrução e intensidade de 0 a 1.")
        with self.store.Session.begin() as db:
            row = InterpretationPreset(
                preset_id=new_id("PRESET"), project_id=self.project_id, name=name[:120],
                emotion=emotion[:80], intensity=intensity, instruct=instruct[:2000], created_at=utc_now(),
            )
            db.add(row)
            return model_dict(row)

    def export_script(self, session_id: str | None = None, include_discarded: bool = False,
                      format_name: str = "json"):
        lines = self.list_lines(session_id=session_id, status="all")
        if not include_discarded:
            lines = [item for item in lines if item["editorial_status"] != "discarded"]
        conflicts = []
        for item in lines:
            if item["conflict_occurrence_ids"]:
                conflicts.append({"occurrence_id": item["occurrence_id"],
                                  "line_id": item["line_id"], "event_id": item["event"]["event_id"],
                                  "session_id": item["session_id"],
                                  "related_occurrences": item["conflict_occurrence_ids"],
                                  "resolution": "not_merged; human_review_required"})
        if format_name == "csv":
            output = io.StringIO(newline="")
            fields = ["line_id", "occurrence_id", "event_id", "session_id", "source_start_time",
                      "source_end_time", "source_text", "dub_text", "character_id", "character_name",
                      "emotion", "editorial_status", "confidence", "conflict_occurrence_ids"]
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for item in lines:
                writer.writerow({
                    "line_id": item["line_id"], "occurrence_id": item["occurrence_id"],
                    "event_id": item["event"]["event_id"], "session_id": item["session_id"],
                    "source_start_time": item["source_start_time"], "source_end_time": item["source_end_time"],
                    "source_text": item["source_text"], "dub_text": item["dub_text"],
                    "character_id": item["character_id"], "character_name": item["character_name"],
                    "emotion": item["emotion"], "editorial_status": item["editorial_status"],
                    "confidence": item["event"]["confidence"],
                    "conflict_occurrence_ids": ";".join(item["conflict_occurrence_ids"]),
                })
            return output.getvalue()
        return {
            "schema_version": 2,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "project": self.project(),
            "selection": {"session_id": session_id, "include_discarded": include_discarded,
                          "criterion": "one capture event to one occurrence; no text-only deduplication"},
            "line_count": len(lines), "conflicts": conflicts,
            "lines": [{
                "line_id": item["line_id"], "occurrence_id": item["occurrence_id"],
                "character_id": item["character_id"], "character_name": item["character_name"],
                "source_language": item["source_language"], "dub_language": item["dub_language"],
                "source_text": item["source_text"], "dub_text": item["dub_text"],
                "emotion": item["emotion"], "emotion_intensity": item["emotion_intensity"],
                "direction": item["direction"], "editorial_status": item["editorial_status"],
                "target_duration_ms": item["target_duration_ms"], "scene_id": None, "route_id": None,
                "condition": {}, "runtime_trigger_id": None, "runtime_trigger_type": None,
                "capture_evidence": [{
                    "session_id": item["session_id"], "event_id": item["event"]["event_id"],
                    "source_start_time": item["source_start_time"], "source_end_time": item["source_end_time"],
                    "ocr_original": item["event"]["original_text"],
                    "ocr_processed": item["event"]["processed_text"],
                    "corrected_text": item["event"]["corrected_text"],
                    "confidence": item["event"]["confidence"],
                    "crop_path": item["event"]["crop_path"],
                    "assets": item["event"]["assets_json"],
                }],
            } for item in lines],
        }

    def runtime_manifest(self) -> dict:
        # Keep the Phase 2 URL as the single public manifest contract while
        # Phase 3 adds game profile, trigger and package-readiness information.
        from ..integration.service import IntegrationService

        return IntegrationService(self.store, self.data_root).runtime_manifest(self.project_id)

    def context_for_job(self, job_id: str) -> dict | None:
        with self.store.Session() as db:
            job = db.get(TtsJob, job_id)
            if not job:
                return None
            take = db.get(AudioTake, job.take_id)
            if not take:
                return None
            line = db.get(DialogueLine, take.line_id)
            occurrence = db.get(DialogueOccurrence, take.occurrence_id)
            profile = db.get(VoiceProfile, take.voice_profile_id)
            reference = db.get(VoiceReferenceAsset, profile.reference_asset_id) if profile and profile.reference_asset_id else None
            return {"job": model_dict(job), "take": model_dict(take), "line": model_dict(line),
                    "occurrence": model_dict(occurrence), "profile": model_dict(profile),
                    "reference": model_dict(reference) if reference else None}

    def recover_reference_path(self, reference: dict | None) -> Path | None:
        return data_path(self.data_root, reference["normalized_path"]) if reference else None

    def audio_file(self, take_id: str) -> Path:
        with self.store.Session() as db:
            take = db.get(AudioTake, take_id)
            line = db.get(DialogueLine, take.line_id) if take else None
            if not take or not line or line.project_id != self.project_id or not take.audio_master_path:
                raise DubbingError("audio_not_ready", "O áudio deste take ainda não está disponível.", 404)
            path = data_path(self.data_root, take.audio_master_path)
            if not path.is_file():
                raise DubbingError("audio_missing", "O arquivo master não foi encontrado no volume do projeto.", 404)
            return path

    def project_audio_path(self, occurrence_id: str, take_id: str) -> Path:
        return self.project_data_root / "audio" / re.sub(r"[^A-Za-z0-9_-]", "_", occurrence_id) / f"{take_id}.wav"

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import threading
import unicodedata
import uuid
from collections import deque
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..database.models import (
    AudioTake, CaptureLink, DialogueLine, DialogueOccurrence, DubbingCharacter, DubbingProject, EventRecord, GameBuild,
    GamePluginBinding, GameProfile, IntegrationPlugin, RuntimePackage,
    RuntimeTestItem, RuntimeTestRun,
)
from ..database.store import DubSyncStore
from ..dubbing.audio import data_path, sha256_file
from ..projects.context import current_project_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16].upper()}"


MAX_RUNTIME_VOLUME_PERCENT = 100.0 * (10.0 ** (30.0 / 20.0))


def _row(row) -> dict | None:
    if row is None:
        return None
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return result


def _runtime_text_key(value: str | None, collapse_initials: bool = True) -> str:
    """Normalize dialogue text for conservative, language-agnostic exact matching."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value)).lower()
    characters = []
    for char in decomposed:
        if unicodedata.category(char).startswith("M"):
            continue
        characters.append(char if char.isalnum() else " ")
    tokens = " ".join("".join(characters).split()).split()
    if not collapse_initials:
        return " ".join(tokens)
    normalized = []
    index = 0
    while index < len(tokens):
        if len(tokens[index]) != 1 or not tokens[index].isalpha():
            normalized.append(tokens[index])
            index += 1
            continue
        end = index + 1
        while end < len(tokens) and len(tokens[end]) == 1 and tokens[end].isalpha():
            end += 1
        letter_run = tokens[index:end]
        normalized.append("".join(letter_run) if len(letter_run) >= 3 else " ".join(letter_run))
        index = end
    return " ".join(normalized)


def _runtime_text_key_variants(value: str | None) -> list[str]:
    """Keep exact variants for dotted acronyms used by game subtitles and OCR."""
    canonical = _runtime_text_key(value)
    if not canonical:
        return []
    variants = [canonical]
    tokens = canonical.split()
    for index, token in enumerate(tokens):
        if len(token) < 3 or not token.isalpha():
            continue
        expanded = list(tokens)
        expanded[index] = " ".join(token)
        variants.append(_runtime_text_key(" ".join(expanded), collapse_initials=False))
    return list(dict.fromkeys(variant for variant in variants if variant))


class IntegrationError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class IntegrationService:
    """Project-scoped Phase 3 domain operations. Game hooks are deliberately external."""

    BUILTINS = (
        {
            "plugin_id": "replaced.articy",
            "version": "0.1.0",
            "api_version": 1,
            "name": "REPLACED Articy Runtime",
            "description": "Plugin BepInEx IL2CPP para localizar takes aprovados pelo texto do diálogo e tocá-los no FMOD do jogo; correlações Articy salvas ficam como alternativa.",
            "engines_json": ["Unity IL2CPP"],
            "games_json": ["replaced"],
            "capabilities_json": ["dialogue_start", "dialogue_end", "automatic_text_match", "in_game_fmod_audio", "saved_trigger_fallback"],
            "origin": "builtin",
            "trust_status": "builtin",
            "status": "adapter_pending",
            "manifest_json": {
                "adapter_id": "replaced.articy",
                "trigger_types": ["articy_external_id", "articy_object_id"],
                "matching": {"strategy": "saved_trigger_then_exact_source_text_then_exact_dub_text",
                             "ambiguous": "skip_text_match",
                             "saved_trigger_correlations": "auto_learned_from_confirmed_playback"},
                "events": ["dialogue_start", "dialogue_end", "pause", "resume", "scene_changed", "bridge_connected", "bridge_disconnected", "error"],
                "entrypoint": "integration/plugins/replaced-articy/runtime/DubSync.ReplacedProbe.csproj",
                "runtime_hook_instrumented": True,
                "runtime_callback_observed": True,
                "runtime_hook_confirmed": False,
            },
        },
        {
            "plugin_id": "dubsync.visual-runtime",
            "version": "0.1.0",
            "api_version": 1,
            "name": "Visual Runtime",
            "description": "Fallback visual genérico planejado para jogos sem integração interna.",
            "engines_json": ["any"],
            "games_json": ["any"],
            "capabilities_json": ["visual_context", "ocr", "external_audio"],
            "origin": "builtin",
            "trust_status": "builtin",
            "status": "planned",
            "manifest_json": {"adapter_id": "dubsync.visual-runtime", "runtime_hook_confirmed": False},
        },
    )

    def __init__(self, store: DubSyncStore, data_root: Path):
        self.store = store
        self.data_root = data_root.resolve()
        self._runtime_event_lock = threading.Lock()
        self._auto_publish_lock = threading.Lock()
        self._auto_publish_timers: dict[str, threading.Timer] = {}
        self._auto_publish_revisions: dict[str, int] = {}
        self._auto_publish_running: set[str] = set()
        self._auto_publish_status: dict[str, dict] = {}

    @property
    def project_id(self) -> str:
        return current_project_id()

    def initialize(self) -> None:
        now = _now()
        with self.store.Session.begin() as db:
            for item in self.BUILTINS:
                existing = db.scalar(select(IntegrationPlugin).where(
                    IntegrationPlugin.plugin_id == item["plugin_id"],
                    IntegrationPlugin.version == item["version"],
                ))
                if existing is None:
                    db.add(IntegrationPlugin(
                        plugin_record_id=_id("PLG"), created_at=now, updated_at=now, **item,
                    ))
                elif item["plugin_id"] == "replaced.articy" and existing.origin == "builtin":
                    existing.name = item["name"]
                    existing.description = item["description"]
                    existing.engines_json = item["engines_json"]
                    existing.games_json = item["games_json"]
                    existing.capabilities_json = item["capabilities_json"]
                    existing.manifest_json = item["manifest_json"]
                    existing.updated_at = now
        self._reconcile_runtime_event_history()

    def _reconcile_runtime_event_history(self) -> None:
        """Recover safe, previously observed one-line gaps when the service starts."""
        with self.store.Session() as db:
            projects = db.execute(select(DubbingProject.project_id, DubbingProject.slug)).all()
        for project_id, slug in projects:
            event_path = data_path(self.data_root,
                f"projects/{self._safe_component(slug)}/runtime/runtime_events.jsonl")
            if not event_path.is_file():
                continue
            try:
                with event_path.open("r", encoding="utf-8") as stream:
                    lines = deque(stream, maxlen=2000)
                events = [json.loads(line) for line in lines if line.strip()]
            except (OSError, json.JSONDecodeError):
                continue
            starts = [item for item in events if item.get("event_type") == "dialogue_start"]
            for index in range(2, len(starts)):
                left, gap, right = starts[index - 2:index + 1]
                left_details = left.get("details") or {}
                gap_details = gap.get("details") or {}
                right_details = right.get("details") or {}
                if (left_details.get("match_mode") != "exact_text"
                        or left_details.get("playback_started") is not True
                        or gap_details.get("playback_started") is not False
                        or gap_details.get("reason") != "approved_take_not_found"
                        or right_details.get("match_mode") != "exact_text"
                        or right_details.get("playback_started") is not True):
                    continue
                self._infer_single_missing_trigger(
                    project_id, event_path, str(right.get("occurred_at") or ""),
                    str(right_details.get("matched_occurrence_id") or ""), recent_events=events,
                )

    def schedule_auto_publish(self, project_id: str | None = None, delay_seconds: float = 5.0) -> dict:
        project_id = project_id or self.project_id
        profile = self.profile(project_id).get("profile")
        if not profile or not profile.get("game_build_id") or not profile.get("binding"):
            return {"status": "not_ready", "message": "Configure o build e o plugin antes de sincronizar takes."}
        install_root = str(profile.get("install_root") or "").strip()
        plugin_dll = Path(install_root) / "BepInEx" / "plugins" / "DubSync.ReplacedProbe.dll" if install_root else None
        if not plugin_dll or not plugin_dll.is_file():
            return {"status": "not_ready", "message": "O plugin do REPLACED não está instalado na pasta configurada."}
        with self.store.Session.begin() as db:
            pending_takes = db.scalars(select(AudioTake).join(
                DialogueLine, DialogueLine.line_id == AudioTake.line_id,
            ).where(
                DialogueLine.project_id == project_id,
                AudioTake.status == "generated",
            )).all()
            for take in pending_takes:
                take.status = "publishing"
                take.error_message = None
        with self._auto_publish_lock:
            revision = self._auto_publish_revisions.get(project_id, 0) + 1
            self._auto_publish_revisions[project_id] = revision
            previous = self._auto_publish_timers.pop(project_id, None)
            if previous:
                previous.cancel()
            self._auto_publish_status[project_id] = {"status": "queued", "requested_at": _now().isoformat()}
            delay = max(5.0, min(float(delay_seconds), 120.0))
            timer = threading.Timer(delay, self._run_auto_publish, args=(project_id, revision))
            timer.daemon = True
            self._auto_publish_timers[project_id] = timer
            timer.start()
        return {"status": "queued", "message": "A sincronização automática com o jogo foi enfileirada."}

    def _run_auto_publish(self, project_id: str, revision: int) -> None:
        with self._auto_publish_lock:
            if self._auto_publish_revisions.get(project_id) != revision:
                return
            if project_id in self._auto_publish_running:
                timer = threading.Timer(1.0, self._run_auto_publish, args=(project_id, revision))
                timer.daemon = True
                self._auto_publish_timers[project_id] = timer
                timer.start()
                return
            self._auto_publish_timers.pop(project_id, None)
            self._auto_publish_running.add(project_id)
        pending_ids = []
        try:
            with self.store.Session() as db:
                pending_ids = list(db.scalars(select(AudioTake.take_id).join(
                    DialogueLine, DialogueLine.line_id == AudioTake.line_id,
                ).where(DialogueLine.project_id == project_id,
                        AudioTake.status == "publishing")).all())
            version = "auto-" + _now().strftime("%Y%m%d-%H%M%S")
            package = self.build_package(version, project_id=project_id, allow_empty=True)
            deployment = package.get("deployment") or {}
            deployed = deployment.get("status") == "deployed"
            included = set(package.get("take_ids") or [])
            missing = [take_id for take_id in pending_ids if take_id not in included]
            if missing:
                self._set_publication_status(project_id, missing, "publish_error",
                    "O áudio foi gerado, mas a fala não pôde ser associada com segurança ao texto do jogo.")
            if not deployed and pending_ids:
                self._set_publication_status(project_id, pending_ids, "publish_error",
                    f"Pacote criado, mas não ativado no jogo ({deployment.get('status', 'indisponível')}).")
            status = "deployed" if deployed and not missing else "partial" if deployed else deployment.get("status", "package_ready")
            outcome = {"status": status, "package_id": package["package_id"], "version": version,
                       "entry_count": package["entry_count"], "published_take_count": len(included),
                       "pending_take_count": len(missing) if deployed else len(pending_ids),
                       "finished_at": _now().isoformat()}
        except IntegrationError as error:
            self._set_publication_status(project_id, pending_ids, "publish_error", error.message)
            outcome = {"status": "failed", "code": error.code, "message": error.message,
                       "finished_at": _now().isoformat()}
        except Exception as error:
            self._set_publication_status(project_id, pending_ids, "publish_error",
                                         f"{type(error).__name__}: {error}"[:1000])
            outcome = {"status": "failed", "message": f"{type(error).__name__}: {error}"[:240],
                       "finished_at": _now().isoformat()}
        finally:
            with self._auto_publish_lock:
                self._auto_publish_running.discard(project_id)
                if self._auto_publish_revisions.get(project_id) == revision:
                    self._auto_publish_status[project_id] = outcome

    def _set_publication_status(self, project_id: str, take_ids: list[str] | set[str],
                                status: str, message: str | None = None) -> None:
        ids = list(dict.fromkeys(str(value) for value in take_ids if value))
        if not ids:
            return
        with self.store.Session.begin() as db:
            rows = db.scalars(select(AudioTake).join(
                DialogueLine, DialogueLine.line_id == AudioTake.line_id,
            ).where(DialogueLine.project_id == project_id,
                    AudioTake.take_id.in_(ids))).all()
            for take in rows:
                if status == "publish_error" and take.status != "publishing":
                    continue
                take.status = status
                take.error_message = str(message or "")[:1000] or None

    def _mark_package_takes_ready(self, project_id: str, occurrence_take_ids: dict[str, str]) -> None:
        if not occurrence_take_ids:
            return
        with self.store.Session.begin() as db:
            for occurrence_id, take_id in occurrence_take_ids.items():
                selected = db.scalar(select(AudioTake).join(
                    DialogueLine, DialogueLine.line_id == AudioTake.line_id,
                ).where(AudioTake.take_id == take_id, DialogueLine.project_id == project_id))
                if not selected:
                    continue
                selected.status = "ready"
                selected.error_message = None
                older = db.scalars(select(AudioTake).where(
                    AudioTake.occurrence_id == occurrence_id,
                    AudioTake.version < selected.version,
                    AudioTake.status.in_(("approved", "review", "generated", "publishing",
                                          "publish_error", "ready")),
                )).all()
                for take in older:
                    take.status = "superseded"
                    take.error_message = None

    def _project(self, db, project_id: str):
        project = db.get(DubbingProject, project_id)
        if project is None:
            raise IntegrationError("project_not_found", "Projeto não encontrado.", 404)
        return project

    def profile(self, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        with self.store.Session() as db:
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id,
                GameProfile.profile_key == "primary",
            ))
            if profile is None:
                return {"profile": None, "project_id": project_id}
            result = _row(profile)
            build = db.get(GameBuild, profile.game_build_id) if profile.game_build_id else None
            binding = db.scalar(select(GamePluginBinding).where(
                GamePluginBinding.game_profile_id == profile.game_profile_id,
                GamePluginBinding.status != "inactive",
            ).order_by(GamePluginBinding.updated_at.desc()))
            plugin = db.get(IntegrationPlugin, binding.plugin_record_id) if binding else None
            latest_package = db.scalar(select(RuntimePackage).where(
                RuntimePackage.game_profile_id == profile.game_profile_id,
            ).order_by(RuntimePackage.created_at.desc()))
            result["build"] = _row(build)
            result["binding"] = _row(binding)
            result["plugin"] = _row(plugin)
            result["latest_package"] = _row(latest_package)
            with self._auto_publish_lock:
                result["auto_publish_status"] = dict(self._auto_publish_status.get(project_id) or {})
            if result["latest_package"]:
                result["latest_package"]["deployment"] = self._runtime_deployment_status(
                    profile.install_root, result["latest_package"]["package_id"])
            installed_plugin = bool(profile.install_root and (Path(profile.install_root)
                / "BepInEx" / "plugins" / "DubSync.ReplacedProbe.dll").is_file())
            result["runtime_ready"] = bool(plugin and (plugin.status == "installed" or installed_plugin))
            result["runtime_installed"] = installed_plugin
            result["executable_exists"] = bool(profile.executable_path and Path(profile.executable_path).is_file())
            observed_events = self.runtime_events(project_id, limit=250)["events"]
            result["last_runtime_event"] = observed_events[0] if observed_events else None
            result["last_matched_event"] = next((event for event in observed_events
                if event.get("event_type") == "dialogue_start" and event.get("resolution") == "matched"
                and event.get("details", {}).get("playback_started") is True), None)
            result["last_resolved_event"] = next((event for event in observed_events
                if event.get("event_type") == "dialogue_start" and event.get("resolution") == "matched"
                and event.get("details", {}).get("matched_take_id")
                and event.get("details", {}).get("matched_occurrence_id")), None)
            return {"profile": result, "project_id": project_id}

    def set_runtime_volume(self, volume_percent: float, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        volume_percent = float(volume_percent)
        if not math.isfinite(volume_percent) or not 0 <= volume_percent <= MAX_RUNTIME_VOLUME_PERCENT:
            raise IntegrationError("invalid_volume", "O ganho do DubSync deve ficar entre mudo e +30 dB.")
        volume_percent = round(volume_percent, 6)
        with self.store.Session.begin() as db:
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id,
                GameProfile.profile_key == "primary",
            ))
            if profile is None:
                raise IntegrationError("profile_not_configured", "Configure o perfil do jogo antes de ajustar o volume.", 409)
            settings = dict(profile.audio_profile_json or {})
            settings["volume_percent"] = volume_percent
            profile.audio_profile_json = settings
            profile.updated_at = _now()
            install_root = profile.install_root
        runtime_status = self._write_runtime_volume(install_root, volume_percent)
        return {"volume_percent": volume_percent, "volume_runtime_status": runtime_status}

    def save_profile(self, body: dict, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        language = str(body.get("language") or "").strip()
        if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", language):
            raise IntegrationError("invalid_language", "Informe um idioma, por exemplo pt-BR.")
        engine = " ".join(str(body.get("engine") or "unknown").split())[:80] or "unknown"
        build_version = " ".join(str(body.get("build_version") or "").split())[:80]
        platform = " ".join(str(body.get("platform") or "").split())[:80]
        executable = str(body.get("executable_path") or "").strip()[:1000] or None
        install_root = str(body.get("install_root") or "").strip()[:1000] or None
        audio_format = str(body.get("audio_format") or "ogg").lower()
        if audio_format not in {"ogg", "wav"}:
            raise IntegrationError("invalid_audio_format", "O formato de pacote deve ser OGG ou WAV.")
        bitrate = int(body.get("bitrate_kbps") or 64)
        if bitrate < 32 or bitrate > 192:
            raise IntegrationError("invalid_bitrate", "A taxa OGG deve ficar entre 32 e 192 kbps.")
        volume_percent = float(body.get("volume_percent", 100))
        if not math.isfinite(volume_percent) or not 0 <= volume_percent <= MAX_RUNTIME_VOLUME_PERCENT:
            raise IntegrationError("invalid_volume", "O ganho do DubSync deve ficar entre mudo e +30 dB.")
        volume_percent = round(volume_percent, 6)
        now = _now()
        volume_runtime_status = "not_deployed"
        with self.store.Session.begin() as db:
            project = self._project(db, project_id)
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id,
                GameProfile.profile_key == "primary",
            ))
            build = None
            if build_version:
                build = db.scalar(select(GameBuild).where(
                    GameBuild.project_id == project_id,
                    GameBuild.version == build_version,
                    GameBuild.platform == platform,
                ).order_by(GameBuild.created_at.desc()))
                if build is None:
                    build = GameBuild(
                        game_build_id=_id("BLD"), project_id=project_id,
                        version=build_version, platform=platform, notes="",
                        is_active=True, created_at=now,
                    )
                    db.add(build)
                db.query(GameBuild).filter(
                    GameBuild.project_id == project_id,
                ).update({GameBuild.is_active: False})
                build.is_active = True
            elif profile and profile.game_build_id:
                build = db.get(GameBuild, profile.game_build_id)
            elif project.game_version:
                build = db.scalar(select(GameBuild).where(
                    GameBuild.project_id == project_id,
                    GameBuild.is_active.is_(True),
                ).order_by(GameBuild.created_at.desc()))
            settings = {"format": audio_format, "sample_rate": 24000, "channels": 1,
                        "bitrate_kbps": bitrate, "converter_policy": "ffmpeg-controlled-v1",
                        "volume_percent": volume_percent}
            if profile is None:
                profile = GameProfile(
                    game_profile_id=_id("GPR"), project_id=project_id,
                    profile_key="primary", name=project.game_title or project.name,
                    language=language, game_build_id=build.game_build_id if build else None,
                    engine=engine, executable_path=executable, install_root=install_root,
                    audio_profile_json=settings, status="configured", created_at=now, updated_at=now,
                )
                db.add(profile)
            else:
                previous_audio_settings = {
                    key: value for key, value in (profile.audio_profile_json or {}).items()
                    if key != "volume_percent"
                }
                previous_signature = (
                    profile.language, profile.game_build_id, profile.engine,
                    profile.executable_path, profile.install_root, previous_audio_settings,
                )
                profile.name = project.game_title or project.name
                profile.language = language
                profile.game_build_id = build.game_build_id if build else None
                profile.engine = engine
                profile.executable_path = executable
                profile.install_root = install_root
                profile.audio_profile_json = settings
                profile.status = "configured"
                profile.updated_at = now
                current_signature = (
                    language, build.game_build_id if build else None, engine,
                    executable, install_root,
                    {key: value for key, value in settings.items() if key != "volume_percent"},
                )
                if previous_signature != current_signature:
                    db.query(RuntimePackage).filter(
                        RuntimePackage.game_profile_id == profile.game_profile_id,
                        RuntimePackage.status == "ready",
                    ).update({RuntimePackage.status: "stale"})
            profile_id = profile.game_profile_id
        volume_runtime_status = self._write_runtime_volume(install_root, volume_percent)
        result = self.profile(project_id)
        result["volume_runtime_status"] = volume_runtime_status
        return result

    def plugins(self) -> dict:
        with self.store.Session() as db:
            items = db.scalars(select(IntegrationPlugin).order_by(
                IntegrationPlugin.name, IntegrationPlugin.version.desc(),
            )).all()
            return {"plugins": [_row(item) for item in items]}

    def register_plugin(self, manifest: dict) -> dict:
        if not isinstance(manifest, dict):
            raise IntegrationError("invalid_plugin_manifest", "O manifesto do plugin precisa ser um objeto JSON.")
        if len(json.dumps(manifest, ensure_ascii=False).encode("utf-8")) > 65536:
            raise IntegrationError("plugin_manifest_too_large", "O manifesto ultrapassa 64 KiB.")
        plugin_id = str(manifest.get("plugin_id") or "").strip().lower()
        version = str(manifest.get("version") or "").strip()
        name = " ".join(str(manifest.get("name") or "").split())[:160]
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,95}", plugin_id):
            raise IntegrationError("invalid_plugin_id", "plugin_id deve usar letras minúsculas, números, ponto, hífen ou sublinhado.")
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,47}", version) or not name:
            raise IntegrationError("invalid_plugin_manifest", "Informe versão e nome do plugin.")
        try:
            api_version = int(manifest.get("api_version", 1))
        except (TypeError, ValueError) as error:
            raise IntegrationError("invalid_plugin_api", "api_version precisa ser um número inteiro.") from error
        if api_version != 1:
            raise IntegrationError("unsupported_plugin_api", "Este DubSync aceita plugins com api_version 1.", 409)
        def string_list(key: str) -> list[str]:
            value = manifest.get(key, [])
            if not isinstance(value, list) or len(value) > 32:
                raise IntegrationError("invalid_plugin_manifest", f"{key} precisa ser uma lista com até 32 itens.")
            return [" ".join(str(item).split())[:96] for item in value if str(item).strip()]
        entrypoint = str(manifest.get("entrypoint") or "").strip()[:240] or None
        if entrypoint and ("\\" in entrypoint or ":" in entrypoint
                           or entrypoint.startswith("/")
                           or any(part in {"", ".", ".."} for part in entrypoint.split("/"))):
            raise IntegrationError("invalid_plugin_entrypoint", "entrypoint precisa ser um caminho relativo sem travessia de pastas.")
        safe_manifest = {
            "plugin_id": plugin_id, "version": version, "api_version": api_version,
            "name": name, "description": str(manifest.get("description") or "")[:1000],
            "engines": string_list("engines"), "games": string_list("games"),
            "capabilities": string_list("capabilities"),
            "entrypoint": entrypoint,
        }
        now = _now()
        with self.store.Session.begin() as db:
            row = db.scalar(select(IntegrationPlugin).where(
                IntegrationPlugin.plugin_id == plugin_id,
                IntegrationPlugin.version == version,
            ))
            if row is not None and row.origin == "builtin":
                raise IntegrationError("builtin_plugin_locked", "Um manifesto local não pode substituir um plugin nativo.", 409)
            values = {
                "api_version": api_version, "name": name,
                "description": safe_manifest["description"],
                "engines_json": safe_manifest["engines"], "games_json": safe_manifest["games"],
                "capabilities_json": safe_manifest["capabilities"], "manifest_json": safe_manifest,
                "origin": "local", "trust_status": "unverified", "status": "metadata_only",
                "updated_at": now,
            }
            if row is None:
                row = IntegrationPlugin(
                    plugin_record_id=_id("PLG"), plugin_id=plugin_id, version=version,
                    created_at=now, **values,
                )
                db.add(row)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            result = _row(row)
        return result

    def bind_plugin(self, plugin_record_id: str, config: dict | None = None,
                    project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        now = _now()
        with self.store.Session.begin() as db:
            self._project(db, project_id)
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id,
                GameProfile.profile_key == "primary",
            ))
            if profile is None:
                raise IntegrationError("profile_not_configured", "Salve a configuração do jogo antes de vincular um plugin.", 409)
            plugin = db.get(IntegrationPlugin, plugin_record_id)
            if plugin is None:
                raise IntegrationError("plugin_not_found", "Plugin não encontrado no catálogo.", 404)
            binding = db.scalar(select(GamePluginBinding).where(
                GamePluginBinding.game_profile_id == profile.game_profile_id,
                GamePluginBinding.status != "inactive",
            ).order_by(GamePluginBinding.updated_at.desc()))
            if binding is None:
                binding = GamePluginBinding(
                    binding_id=_id("BND"), game_profile_id=profile.game_profile_id,
                    plugin_record_id=plugin_record_id, config_json=config or {},
                    status="adapter_pending" if plugin.status != "installed" else "configured",
                    created_at=now, updated_at=now,
                )
                db.add(binding)
            elif binding.plugin_record_id == plugin_record_id:
                binding.plugin_record_id = plugin_record_id
                binding.config_json = config or {}
                binding.status = "adapter_pending" if plugin.status != "installed" else "configured"
                binding.updated_at = now
            else:
                db.query(GamePluginBinding).filter(
                    GamePluginBinding.game_profile_id == profile.game_profile_id,
                    GamePluginBinding.status != "inactive",
                ).update({GamePluginBinding.status: "inactive", GamePluginBinding.updated_at: now})
                binding = GamePluginBinding(
                    binding_id=_id("BND"), game_profile_id=profile.game_profile_id,
                    plugin_record_id=plugin_record_id, config_json=config or {},
                    status="adapter_pending" if plugin.status != "installed" else "configured",
                    created_at=now, updated_at=now,
                )
                db.add(binding)
                db.query(RuntimePackage).filter(
                    RuntimePackage.game_profile_id == profile.game_profile_id,
                    RuntimePackage.status == "ready",
                ).update({RuntimePackage.status: "stale"})
            profile.status = "configured"
            profile.updated_at = now
        return self.profile(project_id)

    def unbind_plugin(self, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        now = _now()
        with self.store.Session.begin() as db:
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id,
                GameProfile.profile_key == "primary",
            ))
            if profile is None:
                raise IntegrationError("profile_not_configured", "Perfil do jogo não encontrado.", 404)
            db.query(GamePluginBinding).filter(
                GamePluginBinding.game_profile_id == profile.game_profile_id,
                GamePluginBinding.status != "inactive",
            ).update({GamePluginBinding.status: "inactive", GamePluginBinding.updated_at: now})
            db.query(RuntimePackage).filter(
                RuntimePackage.game_profile_id == profile.game_profile_id,
                RuntimePackage.status == "ready",
            ).update({RuntimePackage.status: "stale"})
            profile.updated_at = now
        return self.profile(project_id)

    def mapping(self, project_id: str | None = None, limit: int = 250, offset: int = 0) -> dict:
        project_id = project_id or self.project_id
        limit = max(1, min(int(limit), 5000))
        offset = max(0, int(offset))
        with self.store.Session() as db:
            self._project(db, project_id)
            lines = db.scalars(select(DialogueLine).where(
                DialogueLine.project_id == project_id,
            )).all()
            line_by_id = {line.line_id: line for line in lines}
            characters = db.scalars(select(DubbingCharacter).where(
                DubbingCharacter.project_id == project_id,
            )).all()
            character_names = {item.character_id: item.display_name for item in characters}
            occurrences = db.scalars(select(DialogueOccurrence).join(
                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
            ).where(DialogueLine.project_id == project_id).order_by(
                DialogueOccurrence.created_at, DialogueOccurrence.occurrence_id,
            )).all()
            capture_texts_by_occurrence: dict[str, list[str]] = {}
            if occurrences:
                capture_rows = db.execute(select(CaptureLink.occurrence_id, EventRecord.corrected_text).join(
                    EventRecord, EventRecord.event_id == CaptureLink.event_id,
                ).where(
                    CaptureLink.occurrence_id.in_([item.occurrence_id for item in occurrences]),
                    CaptureLink.is_primary.is_(True),
                    EventRecord.review_status.in_(("accepted", "corrected")),
                    EventRecord.corrected_text.is_not(None),
                )).all()
                for occurrence_id, corrected_text in capture_rows:
                    if corrected_text:
                        capture_texts_by_occurrence.setdefault(occurrence_id, []).append(corrected_text)
            takes = db.scalars(select(AudioTake).where(
                AudioTake.status.in_(("approved", "ready", "generated", "publishing", "publish_error", "review")),
                AudioTake.occurrence_id.in_([item.occurrence_id for item in occurrences] or ["__none__"]),
            ).order_by(AudioTake.occurrence_id, AudioTake.version.desc())).all()
            take_by_occurrence = {}
            for take in takes:
                take_by_occurrence.setdefault(take.occurrence_id, take)
            rows = []
            for occurrence in occurrences:
                line = line_by_id.get(occurrence.line_id)
                take = take_by_occurrence.get(occurrence.occurrence_id)
                source_texts = ([line.source_text] if line else []) + capture_texts_by_occurrence.get(
                    occurrence.occurrence_id, [],
                )
                source_keys = list(dict.fromkeys(
                    key for text in source_texts for key in _runtime_text_key_variants(text)
                ))
                dub_keys = _runtime_text_key_variants(line.dub_text if line else "")
                rows.append({
                    "occurrence_id": occurrence.occurrence_id,
                    "line_id": occurrence.line_id,
                    "speaker": character_names.get(line.character_id) if line and line.character_id else None,
                    "speaker_key": _runtime_text_key(character_names.get(line.character_id) if line and line.character_id else None),
                    "source_text": line.source_text if line else "",
                    "dub_text": line.dub_text if line else "",
                    "source_match_keys": source_keys,
                    "dub_match_keys": dub_keys,
                    "auto_match_keys": list(dict.fromkeys((*source_keys, *dub_keys))),
                    "condition": occurrence.condition_json or {},
                    "runtime_trigger_type": occurrence.runtime_trigger_type,
                    "runtime_trigger_id": occurrence.runtime_trigger_id,
                    "status": occurrence.status,
                    "approved_take_id": take.take_id if take else None,
                    "audio_master_path": take.audio_master_path if take else None,
                    "duration_ms": take.actual_duration_ms if take else None,
                    "eligible": bool(take and take.audio_master_path and occurrence.runtime_trigger_id
                                      and occurrence.runtime_trigger_type),
                })
            occurrences_by_text_speaker: dict[tuple[str, str], set[str]] = {}
            source_occurrences_by_text_speaker: dict[tuple[str, str], set[str]] = {}
            dub_occurrences_by_text_speaker: dict[tuple[str, str], set[str]] = {}
            for row in rows:
                for key in row["auto_match_keys"]:
                    occurrences_by_text_speaker.setdefault((key, row["speaker_key"]), set()).add(row["occurrence_id"])
                for key in row["source_match_keys"]:
                    source_occurrences_by_text_speaker.setdefault((key, row["speaker_key"]), set()).add(row["occurrence_id"])
                for key in row["dub_match_keys"]:
                    dub_occurrences_by_text_speaker.setdefault((key, row["speaker_key"]), set()).add(row["occurrence_id"])
            for row in rows:
                all_keys = row["auto_match_keys"]
                row["auto_match_keys"] = [key for key in all_keys
                                           if len(occurrences_by_text_speaker[(key, row["speaker_key"])]) == 1]
                row["source_match_keys"] = [key for key in row["source_match_keys"]
                                             if len(source_occurrences_by_text_speaker[(key, row["speaker_key"])]) == 1]
                row["dub_match_keys"] = [key for key in row["dub_match_keys"]
                                          if len(dub_occurrences_by_text_speaker[(key, row["speaker_key"])]) == 1]
                row["auto_text_ambiguous"] = bool(row["approved_take_id"] and all_keys
                    and not row["auto_match_keys"] and not row["source_match_keys"] and not row["dub_match_keys"])
                row["auto_matchable"] = bool(row["approved_take_id"] and row["audio_master_path"]
                                             and (row["source_match_keys"] or row["dub_match_keys"]))
            total = len(rows)
            page = rows[offset:offset + limit]
            mapped = sum(1 for row in rows if row["runtime_trigger_id"] and row["runtime_trigger_type"])
            approved = sum(1 for row in rows if row["approved_take_id"])
            eligible = sum(1 for row in rows if row["eligible"])
            auto_eligible = sum(1 for row in rows if row["auto_matchable"])
            auto_ambiguous = sum(1 for row in rows if row["auto_text_ambiguous"])
            package_eligible = sum(1 for row in rows if row["approved_take_id"] and row["audio_master_path"]
                                   and (row["auto_matchable"] or row["eligible"]))
            return {"project_id": project_id, "total": total, "mapped_count": mapped,
                    "approved_count": approved, "eligible_count": eligible,
                    "auto_eligible_count": auto_eligible, "auto_ambiguous_count": auto_ambiguous,
                    "package_eligible_count": package_eligible,
                    "unmapped_count": total - mapped, "items": page,
                    "limit": limit, "offset": offset}

    def map_trigger(self, occurrence_id: str, trigger_type: str, trigger_id: str,
                    project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        trigger_type = str(trigger_type or "").strip().lower()
        trigger_id = str(trigger_id or "").strip()
        if trigger_type not in {"articy_external_id", "articy_object_id", "custom_event_id"}:
            raise IntegrationError("invalid_trigger_type", "Use um ID Articy observado no jogo.")
        if not trigger_id or len(trigger_id) > 160 or any(ord(char) < 32 for char in trigger_id):
            raise IntegrationError("invalid_trigger_id", "Informe um gatilho real de até 160 caracteres.")
        if trigger_type == "articy_object_id" and not (trigger_id.isascii() and trigger_id.isdecimal()
                                                         and 0 < int(trigger_id) <= 2**64 - 1
                                                         and str(int(trigger_id)) == trigger_id):
            raise IntegrationError("invalid_trigger_id", "ID Articy numerico invalido.")
        with self.store.Session.begin() as db:
            occurrence = db.scalar(select(DialogueOccurrence).join(
                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
            ).where(
                DialogueOccurrence.occurrence_id == occurrence_id,
                DialogueLine.project_id == project_id,
            ))
            if occurrence is None:
                raise IntegrationError("occurrence_not_found", "Ocorrência não encontrada neste projeto.", 404)
            conflict = db.scalar(select(DialogueOccurrence.occurrence_id).join(
                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
            ).where(
                DialogueLine.project_id == project_id,
                DialogueOccurrence.runtime_trigger_type == trigger_type,
                DialogueOccurrence.runtime_trigger_id == trigger_id,
                DialogueOccurrence.occurrence_id != occurrence_id,
            ).limit(1))
            if conflict:
                raise IntegrationError("trigger_conflict", "Este gatilho já está associado a outra ocorrência.", 409)
            occurrence.runtime_trigger_type = trigger_type
            occurrence.runtime_trigger_id = trigger_id
            occurrence.status = "mapped"
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id,
                GameProfile.profile_key == "primary",
            ))
            if profile:
                db.query(RuntimePackage).filter(
                    RuntimePackage.game_profile_id == profile.game_profile_id,
                    RuntimePackage.status == "ready",
                ).update({RuntimePackage.status: "stale"})
            result = {"occurrence_id": occurrence_id, "runtime_trigger_type": trigger_type,
                      "runtime_trigger_id": trigger_id, "status": occurrence.status}
        return result

    def unmap_trigger(self, occurrence_id: str, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        with self.store.Session.begin() as db:
            occurrence = db.scalar(select(DialogueOccurrence).join(
                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
            ).where(
                DialogueOccurrence.occurrence_id == occurrence_id,
                DialogueLine.project_id == project_id,
            ))
            if occurrence is None:
                raise IntegrationError("occurrence_not_found", "Ocorrência não encontrada neste projeto.", 404)
            occurrence.runtime_trigger_type = None
            occurrence.runtime_trigger_id = None
            occurrence.status = "unmapped"
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id,
                GameProfile.profile_key == "primary",
            ))
            if profile:
                db.query(RuntimePackage).filter(
                    RuntimePackage.game_profile_id == profile.game_profile_id,
                    RuntimePackage.status == "ready",
                ).update({RuntimePackage.status: "stale"})
            return {"occurrence_id": occurrence_id, "runtime_trigger_type": None,
                    "runtime_trigger_id": None, "status": "unmapped"}

    def record_runtime_event(self, body: dict, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        event_id = str(body.get("event_id") or "").strip()
        event_type = str(body.get("event_type") or "").strip()
        occurred_at = str(body.get("occurred_at") or "").strip()
        allowed_events = {"dialogue_start", "dialogue_end", "pause", "resume", "scene_changed", "error"}
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", event_id):
            raise IntegrationError("invalid_runtime_event", "event_id runtime inválido.")
        if event_type not in allowed_events or not occurred_at:
            raise IntegrationError("invalid_runtime_event", "Evento ou timestamp fora do contrato runtime.")
        try:
            datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise IntegrationError("invalid_runtime_event", "occurred_at precisa ser uma data ISO 8601.") from error
        try:
            payload_size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError) as error:
            raise IntegrationError("invalid_runtime_event", "O evento runtime não é JSON válido.") from error
        if payload_size > 32768:
            raise IntegrationError("runtime_event_too_large", "O evento runtime ultrapassa 32 KiB.")

        raw_trigger = body.get("trigger")
        trigger_type = trigger_id = None
        if raw_trigger is not None:
            if not isinstance(raw_trigger, dict):
                raise IntegrationError("invalid_runtime_trigger", "trigger precisa ser um objeto JSON.")
            trigger_type = str(raw_trigger.get("type") or "").strip().lower() or None
            trigger_id = str(raw_trigger.get("id") or "").strip() or None
            if trigger_type and trigger_type not in {"articy_external_id", "articy_object_id"}:
                raise IntegrationError("invalid_runtime_trigger", "O piloto REPLACED aceita IDs Articy observados.")
            if trigger_id and (len(trigger_id) > 160 or any(ord(char) < 32 for char in trigger_id)):
                raise IntegrationError("invalid_runtime_trigger", "ID Articy invalido.")
            if trigger_type == "articy_object_id" and trigger_id and not (
                trigger_id.isascii() and trigger_id.isdecimal()
                and 0 < int(trigger_id) <= 2**64 - 1 and str(int(trigger_id)) == trigger_id
            ):
                raise IntegrationError("invalid_runtime_trigger", "ID Articy numerico invalido.")
        details = body.get("details") if isinstance(body.get("details"), dict) else {}
        reported_occurrence_id = str(details.get("matched_occurrence_id") or "").strip()
        reported_take_id = str(details.get("matched_take_id") or "").strip()
        if event_type == "dialogue_start" and not (trigger_type in {"articy_external_id", "articy_object_id"} and trigger_id):
            if not (reported_occurrence_id and len(reported_occurrence_id) <= 48
                    and reported_take_id and len(reported_take_id) <= 48):
                raise IntegrationError("runtime_trigger_missing", "Sem ID do jogo, dialogue_start precisa comprovar uma correspondência aprovada por texto.")

        occurrence = take = None
        correlation_saved = False
        correlation_conflict = None
        match_mode = str(details.get("match_mode") or "").strip().lower()
        playback_started = details.get("playback_started") is True
        with self.store.Session.begin() as db:
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id,
                GameProfile.profile_key == "primary",
            ))
            if profile is None:
                raise IntegrationError("profile_not_configured", "Configure o perfil do jogo antes de receber eventos runtime.", 409)
            if trigger_id:
                occurrence = db.scalar(select(DialogueOccurrence).join(
                    DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
                ).where(
                    DialogueLine.project_id == project_id,
                    DialogueOccurrence.runtime_trigger_type == trigger_type,
                    DialogueOccurrence.runtime_trigger_id == trigger_id,
                ))
                if occurrence:
                    take = db.scalar(select(AudioTake).where(
                        AudioTake.occurrence_id == occurrence.occurrence_id,
                        AudioTake.status.in_(("ready", "approved")),
                    ).order_by(AudioTake.version.desc()))
            if event_type == "dialogue_start" and reported_occurrence_id and reported_take_id:
                reported_occurrence = db.scalar(select(DialogueOccurrence).join(
                    DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
                ).where(
                    DialogueLine.project_id == project_id,
                    DialogueOccurrence.occurrence_id == reported_occurrence_id,
                ))
                reported_take = db.scalar(select(AudioTake).where(
                    AudioTake.take_id == reported_take_id,
                    AudioTake.occurrence_id == reported_occurrence_id,
                    AudioTake.status.in_(("ready", "approved")),
                ))
                if reported_occurrence and reported_take:
                    if occurrence is not None and occurrence.occurrence_id != reported_occurrence.occurrence_id:
                        correlation_conflict = "saved_trigger_disagrees_with_text_match"
                    else:
                        occurrence, take = reported_occurrence, reported_take
                        if (trigger_type in {"articy_external_id", "articy_object_id"} and trigger_id
                                and playback_started and match_mode == "exact_text"):
                            existing_trigger = db.scalar(select(DialogueOccurrence).join(
                                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
                            ).where(
                                DialogueLine.project_id == project_id,
                                DialogueOccurrence.runtime_trigger_type == trigger_type,
                                DialogueOccurrence.runtime_trigger_id == trigger_id,
                                DialogueOccurrence.occurrence_id != reported_occurrence.occurrence_id,
                            ).limit(1))
                            if existing_trigger is not None:
                                correlation_conflict = "trigger_already_mapped_to_another_occurrence"
                            elif (reported_occurrence.runtime_trigger_type
                                  and reported_occurrence.runtime_trigger_id
                                  and (reported_occurrence.runtime_trigger_type != trigger_type
                                       or reported_occurrence.runtime_trigger_id != trigger_id)):
                                correlation_conflict = "occurrence_already_has_another_trigger"
                            elif not (reported_occurrence.runtime_trigger_type == trigger_type
                                      and reported_occurrence.runtime_trigger_id == trigger_id):
                                reported_occurrence.runtime_trigger_type = trigger_type
                                reported_occurrence.runtime_trigger_id = trigger_id
                                reported_occurrence.status = "mapped"
                                profile_for_package = db.scalar(select(GameProfile).where(
                                    GameProfile.project_id == project_id,
                                    GameProfile.profile_key == "primary",
                                ))
                                if profile_for_package:
                                    db.query(RuntimePackage).filter(
                                        RuntimePackage.game_profile_id == profile_for_package.game_profile_id,
                                        RuntimePackage.status == "ready",
                                    ).update({RuntimePackage.status: "stale"})
                                correlation_saved = True
            if event_type == "dialogue_start" and not trigger_id and occurrence is None:
                raise IntegrationError("runtime_text_match_invalid", "A correspondência por texto não apontou para um áudio ativo deste projeto.", 409)
            project = self._project(db, project_id)
            slug = project.slug

        package_refresh = self.schedule_auto_publish(project_id, delay_seconds=30) if correlation_saved else None

        # Keep the event inbox useful for hook diagnosis without persisting game subtitle text.
        stored_details = {key: str(details[key])[:240] for key in
                          ("callback", "reason", "code", "match_mode", "playback_error",
                           "match_key_type", "matched_take_id", "matched_occurrence_id")
                          if key in details and details[key] is not None}
        if isinstance(details.get("playback_started"), bool):
            stored_details["playback_started"] = details["playback_started"]
        if correlation_saved:
            stored_details["trigger_correlation"] = "saved_from_confirmed_exact_text_playback"
            stored_details["package_refresh"] = str((package_refresh or {}).get("status") or "not_scheduled")
        elif correlation_conflict:
            stored_details["trigger_correlation"] = correlation_conflict
        stored = {
            "event_id": event_id, "event_type": event_type, "occurred_at": occurred_at,
            "received_at": _now().isoformat(),
            "trigger": {"type": trigger_type, "id": trigger_id} if trigger_id else None,
            "details": stored_details,
            "resolution": "matched" if occurrence else ("unmapped" if trigger_id else "no_trigger"),
            "occurrence_id": occurrence.occurrence_id if occurrence else None,
            "line_id": occurrence.line_id if occurrence else None,
            "approved_take_id": take.take_id if take else None,
        }
        learned_correlations = []
        if correlation_saved and occurrence is not None and trigger_id:
            learned_correlations.append({"occurrence_id": occurrence.occurrence_id,
                                         "trigger_type": trigger_type, "trigger_id": trigger_id})
        event_path = data_path(self.data_root, f"projects/{self._safe_component(slug)}/runtime/runtime_events.jsonl")
        event_path.parent.mkdir(parents=True, exist_ok=True)
        with self._runtime_event_lock:
            if event_path.is_file() and event_path.stat().st_size > 4 * 1024 * 1024:
                with event_path.open("r", encoding="utf-8") as stream:
                    recent_lines = deque(stream, maxlen=4000)
                compacted = event_path.with_suffix(".tmp")
                with compacted.open("w", encoding="utf-8", newline="\n") as stream:
                    stream.writelines(recent_lines)
                compacted.replace(event_path)
            with event_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(stored, ensure_ascii=False, separators=(",", ":")) + "\n")
        if correlation_saved and occurrence is not None:
            inferred = self._infer_single_missing_trigger(project_id, event_path, occurred_at,
                                                           occurrence.occurrence_id)
            if inferred:
                stored["inferred_trigger_correlation"] = inferred
                learned_correlations.append(inferred)
        if learned_correlations:
            stored["learned_trigger_correlations"] = learned_correlations
        return stored

    def _infer_single_missing_trigger(self, project_id: str, event_path: Path,
                                      current_occurred_at: str, current_occurrence_id: str,
                                      recent_events: list[dict] | None = None) -> dict | None:
        """Learn one missing trigger only when exact-text anchors prove a single sequence gap."""
        if recent_events is None:
            if not event_path.is_file():
                return None
            try:
                with event_path.open("r", encoding="utf-8") as stream:
                    recent_events = [json.loads(line) for line in deque(stream, maxlen=250)
                                     if line.strip()]
            except (OSError, json.JSONDecodeError):
                return None
        recent = recent_events[-2000:]
        def event_time(item: dict) -> datetime:
            try:
                parsed = datetime.fromisoformat(str(item.get("occurred_at") or "").replace("Z", "+00:00"))
                return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return datetime.min.replace(tzinfo=timezone.utc)

        starts = sorted((item for item in recent if item.get("event_type") == "dialogue_start"),
                        key=event_time)
        current_index = next((index for index, item in enumerate(starts)
                              if item.get("occurred_at") == current_occurred_at
                              and (item.get("occurrence_id") == current_occurrence_id
                                   or item.get("details", {}).get("matched_occurrence_id") == current_occurrence_id)), None)
        if current_index is None or current_index < 2:
            return None
        left_event, gap_event, right_event = starts[current_index - 2:current_index + 1]
        left_details = left_event.get("details") or {}
        gap_details = gap_event.get("details") or {}
        right_details = right_event.get("details") or {}
        if not (left_details.get("match_mode") == "exact_text"
                and left_details.get("playback_started") is True
                and gap_details.get("playback_started") is False
                and gap_details.get("reason") == "approved_take_not_found"
                and right_details.get("match_mode") == "exact_text"
                and right_details.get("playback_started") is True):
            return None
        gap_trigger = gap_event.get("trigger") or {}
        trigger_type = str(gap_trigger.get("type") or "").strip().lower()
        trigger_id = str(gap_trigger.get("id") or "").strip()
        left_occurrence_id = str(left_details.get("matched_occurrence_id") or "").strip()
        right_occurrence_id = str(right_details.get("matched_occurrence_id") or "").strip()
        if (trigger_type not in {"articy_external_id", "articy_object_id"} or not trigger_id
                or not left_occurrence_id or not right_occurrence_id):
            return None
        try:
            left_time = datetime.fromisoformat(str(left_event.get("occurred_at")).replace("Z", "+00:00"))
            gap_time = datetime.fromisoformat(str(gap_event.get("occurred_at")).replace("Z", "+00:00"))
            right_time = datetime.fromisoformat(str(right_event.get("occurred_at")).replace("Z", "+00:00"))
            if not (left_time <= gap_time <= right_time) or (right_time - left_time).total_seconds() > 20:
                return None
        except (TypeError, ValueError):
            return None

        with self.store.Session.begin() as db:
            left = db.scalar(select(DialogueOccurrence).join(
                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
            ).where(DialogueLine.project_id == project_id,
                    DialogueOccurrence.occurrence_id == left_occurrence_id))
            right = db.scalar(select(DialogueOccurrence).join(
                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
            ).where(DialogueLine.project_id == project_id,
                    DialogueOccurrence.occurrence_id == right_occurrence_id))
            if (left is None or right is None or left.local_sequence is None
                    or right.local_sequence != left.local_sequence + 2):
                return None
            # Consecutive dialogue can alternate speakers. Sequence, scene and route
            # establish the missing occurrence; requiring the two anchors to share a
            # character would reject normal back-and-forth dialogue.
            candidates = db.scalars(select(DialogueOccurrence).join(
                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
            ).where(
                DialogueLine.project_id == project_id,
                DialogueOccurrence.local_sequence == left.local_sequence + 1,
                DialogueOccurrence.scene_id == left.scene_id,
                DialogueOccurrence.route_id == left.route_id,
                DialogueOccurrence.runtime_trigger_type.is_(None),
                DialogueOccurrence.runtime_trigger_id.is_(None),
                DialogueOccurrence.occurrence_id.in_(select(AudioTake.occurrence_id).where(
                    AudioTake.status.in_(("ready", "approved")),
                    AudioTake.audio_master_path.is_not(None),
                ).distinct()),
            )).all()
            candidates = [item for item in candidates if item.occurrence_id not in
                          {left.occurrence_id, right.occurrence_id}]
            if len(candidates) != 1:
                return None
            candidate = candidates[0]
            candidate_line = db.get(DialogueLine, candidate.line_id)
            active_take = db.scalar(select(AudioTake).where(
                AudioTake.occurrence_id == candidate.occurrence_id,
                AudioTake.status.in_(("ready", "approved")),
                AudioTake.audio_master_path.is_not(None),
            ).order_by(AudioTake.version.desc()))
            if candidate_line is None or active_take is None:
                return None
            conflict = db.scalar(select(DialogueOccurrence.occurrence_id).join(
                DialogueLine, DialogueLine.line_id == DialogueOccurrence.line_id,
            ).where(DialogueLine.project_id == project_id,
                    DialogueOccurrence.runtime_trigger_type == trigger_type,
                    DialogueOccurrence.runtime_trigger_id == trigger_id).limit(1))
            if conflict:
                return None
            candidate.runtime_trigger_type = trigger_type
            candidate.runtime_trigger_id = trigger_id
            candidate.status = "mapped"
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id, GameProfile.profile_key == "primary",
            ))
            if profile:
                db.query(RuntimePackage).filter(
                    RuntimePackage.game_profile_id == profile.game_profile_id,
                    RuntimePackage.status == "ready",
                ).update({RuntimePackage.status: "stale"})
        refresh = self.schedule_auto_publish(project_id, delay_seconds=30)
        return {"occurrence_id": candidate.occurrence_id, "trigger_type": trigger_type,
                "trigger_id": trigger_id, "package_refresh": refresh.get("status")}

    def runtime_events(self, project_id: str | None = None, limit: int = 50) -> dict:
        project_id = project_id or self.project_id
        limit = max(1, min(int(limit), 250))
        with self.store.Session() as db:
            project = self._project(db, project_id)
            slug = project.slug
        event_path = data_path(self.data_root, f"projects/{self._safe_component(slug)}/runtime/runtime_events.jsonl")
        if not event_path.is_file():
            return {"project_id": project_id, "events": []}
        events = deque(maxlen=limit)
        with event_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return {"project_id": project_id, "events": list(reversed(events))}

    def runtime_manifest(self, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        data = self.mapping(project_id, limit=5000)
        profile = self.profile(project_id).get("profile")
        entries = []
        for row in data["items"]:
            if not row["approved_take_id"]:
                continue
            entries.append({
                "occurrence_id": row["occurrence_id"], "line_id": row["line_id"],
                "take_id": row["approved_take_id"], "audio": row["audio_master_path"],
                "character_id": row["speaker"],
                "auto_match_keys": row["auto_match_keys"],
                "source_match_keys": row["source_match_keys"],
                "dub_match_keys": row["dub_match_keys"],
                "speaker_key": _runtime_text_key(row["speaker"]),
                "duration_ms": row["duration_ms"],
                "runtime_trigger_type": row["runtime_trigger_type"],
                "runtime_trigger_id": row["runtime_trigger_id"],
                "condition": row["condition"], "mapped": bool(row["runtime_trigger_id"] and row["runtime_trigger_type"]),
            })
        with self.store.Session() as db:
            project = _row(self._project(db, project_id))
        return {
            "schema_version": 3,
            "project": project,
            "game_profile": profile,
            "runtime": "saved_trigger_then_source_text_then_dub_text",
            "manifest_status": "ready_for_packaging" if data["auto_eligible_count"] or data["eligible_count"] else "needs_active_text_takes",
            "approved_occurrence_count": len(entries),
            "mapped_occurrence_count": data["mapped_count"],
            "eligible_occurrence_count": data["eligible_count"],
            "auto_matchable_occurrence_count": data["auto_eligible_count"],
            "ambiguous_text_occurrence_count": data["auto_ambiguous_count"],
            "unmapped_occurrence_count": data["unmapped_count"],
            "occurrences": entries,
        }

    @staticmethod
    def _safe_component(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
        if not safe or safe in {".", ".."}:
            raise IntegrationError("invalid_package_path", "O nome do pacote contém caracteres inválidos.")
        return safe[:96]

    def _deploy_runtime_package(self, package_dir: Path, install_root: str | None,
                                package_id: str, volume_percent: float = 100) -> dict:
        """Stage a package under our own BepInEx plugin directory and atomically activate it."""
        raw_root = str(install_root or "").strip()
        if not raw_root:
            return {"status": "game_folder_not_configured"}
        try:
            game_root = Path(raw_root).expanduser().resolve(strict=True)
            if not game_root.is_dir():
                return {"status": "game_folder_not_configured"}
            plugin_dll = game_root / "BepInEx" / "plugins" / "DubSync.ReplacedProbe.dll"
            if not plugin_dll.is_file():
                return {"status": "plugin_not_installed"}
            plugin_root = plugin_dll.parent / "DubSync.ReplacedProbe"
            packages_root = plugin_root / "packages"
            packages_root.mkdir(parents=True, exist_ok=True)
            safe_id = self._safe_component(package_id)
            destination = packages_root / safe_id
            staged = packages_root / f".{safe_id}.staging"
            if destination.exists() or staged.exists():
                return {"status": "package_folder_conflict"}
            shutil.copytree(package_dir, staged)
            staged.replace(destination)
            pointer = plugin_root / "active-package.json"
            pointer_tmp = plugin_root / ".active-package.tmp"
            pointer_tmp.write_text(json.dumps({"directory": safe_id,
                "volume_percent": round(max(0.0, min(MAX_RUNTIME_VOLUME_PERCENT, float(volume_percent))), 6)},
                ensure_ascii=False, allow_nan=False), encoding="utf-8")
            pointer_tmp.replace(pointer)
            return {"status": "deployed", "directory": str(destination), "active": True}
        except (OSError, ValueError) as error:
            return {"status": "deployment_failed", "detail": f"{type(error).__name__}: {error}"[:240]}

    @staticmethod
    def _write_runtime_volume(install_root: str | None, volume_percent: float) -> str:
        """Persist the DubSync channel gain beside the active package without rebuilding it."""
        if not install_root:
            return "game_folder_not_configured"
        plugin_root = Path(install_root) / "BepInEx" / "plugins" / "DubSync.ReplacedProbe"
        if not (plugin_root.parent / "DubSync.ReplacedProbe.dll").is_file():
            return "plugin_not_installed"
        pointer = plugin_root / "active-package.json"
        if not pointer.is_file():
            return "not_deployed"
        try:
            data = json.loads(pointer.read_text(encoding="utf-8-sig"))
            directory = data.get("directory") if isinstance(data, dict) else None
            if not isinstance(directory, str) or not directory or Path(directory).name != directory:
                return "deployment_status_unavailable"
            data["volume_percent"] = round(max(0.0, min(MAX_RUNTIME_VOLUME_PERCENT, float(volume_percent))), 6)
            temporary = plugin_root / f".active-package.{uuid.uuid4().hex}.tmp"
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(pointer)
            return "applied"
        except (OSError, ValueError, json.JSONDecodeError):
            return "deployment_status_unavailable"

    @staticmethod
    def _runtime_deployment_status(install_root: str | None, package_id: str) -> dict:
        if not install_root:
            return {"status": "game_folder_not_configured"}
        try:
            pointer = Path(install_root) / "BepInEx" / "plugins" / "DubSync.ReplacedProbe" / "active-package.json"
            if not pointer.is_file():
                return {"status": "not_deployed"}
            data = json.loads(pointer.read_text(encoding="utf-8-sig"))
            return {"status": "deployed" if data.get("directory") == package_id else "not_active",
                    "active": data.get("directory") == package_id}
        except (OSError, ValueError, json.JSONDecodeError):
            return {"status": "deployment_status_unavailable"}

    def _convert(self, source: Path, destination: Path, profile: dict, ffmpeg_version: str) -> None:
        audio = profile.get("audio_profile_json") or {}
        audio_format = audio.get("format", "ogg")
        rate = int(audio.get("sample_rate", 24000))
        channels = int(audio.get("channels", 1))
        bitrate = int(audio.get("bitrate_kbps", 64))
        fingerprint = hashlib.sha256(json.dumps({
            "master": sha256_file(source), "format": audio_format, "rate": rate,
            "channels": channels, "bitrate": bitrate, "converter": ffmpeg_version,
        }, sort_keys=True).encode("utf-8")).hexdigest()
        suffix = ".ogg" if audio_format == "ogg" else ".wav"
        cache = self.data_root / "runtime_audio_cache" / f"{fingerprint}{suffix}"
        cache.parent.mkdir(parents=True, exist_ok=True)
        if not cache.is_file():
            temp = cache.with_name(f".{fingerprint}.{uuid.uuid4().hex}.tmp{suffix}")
            command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                       "-map", "0:a:0", "-vn", "-ac", str(channels), "-ar", str(rate), "-map_metadata", "-1"]
            if audio_format == "ogg":
                command += ["-c:a", "libvorbis", "-b:a", f"{bitrate}k", str(temp)]
            else:
                command += ["-c:a", "pcm_s16le", str(temp)]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
                if not temp.is_file() or temp.stat().st_size <= 0:
                    raise IntegrationError("audio_conversion_failed", "O conversor não gerou um áudio válido.", 500)
            except IntegrationError:
                temp.unlink(missing_ok=True)
                raise
            except FileNotFoundError as error:
                temp.unlink(missing_ok=True)
                raise IntegrationError("ffmpeg_missing", "FFmpeg não foi encontrado no PATH.", 503) from error
            except subprocess.TimeoutExpired as error:
                temp.unlink(missing_ok=True)
                raise IntegrationError("audio_conversion_timeout", "A conversão ultrapassou 180 segundos.", 504) from error
            except subprocess.CalledProcessError as error:
                temp.unlink(missing_ok=True)
                detail = (error.stderr or "").strip().splitlines()
                raise IntegrationError("audio_conversion_failed", detail[-1][:240] if detail else "Falha ao converter o áudio.", 500) from error
            try:
                probe = subprocess.run([
                    "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                    "stream=codec_name,sample_rate,channels", "-of", "json", str(temp),
                ], check=True, capture_output=True, text=True, timeout=20)
                streams = json.loads(probe.stdout or "{}").get("streams") or []
                if not streams:
                    raise IntegrationError("audio_probe_failed", "FFprobe não encontrou uma faixa de áudio no derivado.", 500)
                stream = streams[0]
                expected_codec = "vorbis" if audio_format == "ogg" else "pcm_s16le"
                if (str(stream.get("codec_name")) != expected_codec
                        or int(stream.get("sample_rate") or 0) != rate
                        or int(stream.get("channels") or 0) != channels):
                    raise IntegrationError("audio_profile_mismatch", "O áudio derivado não corresponde ao perfil configurado.", 500)
                temp.replace(cache)
            except FileNotFoundError as error:
                temp.unlink(missing_ok=True)
                raise IntegrationError("ffprobe_missing", "FFprobe não foi encontrado no PATH.", 503) from error
            except subprocess.TimeoutExpired as error:
                temp.unlink(missing_ok=True)
                raise IntegrationError("audio_probe_timeout", "A validação do derivado ultrapassou 20 segundos.", 504) from error
            except subprocess.CalledProcessError as error:
                temp.unlink(missing_ok=True)
                detail = (error.stderr or "").strip().splitlines()
                raise IntegrationError("audio_probe_failed", detail[-1][:240] if detail else "Falha ao validar o áudio derivado.", 500) from error
            except json.JSONDecodeError as error:
                temp.unlink(missing_ok=True)
                raise IntegrationError("audio_probe_failed", "FFprobe retornou metadados inválidos.", 500) from error
            except IntegrationError:
                temp.unlink(missing_ok=True)
                raise
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache, destination)

    def build_package(self, package_version: str = "0.1.0", project_id: str | None = None,
                      allow_empty: bool = False) -> dict:
        project_id = project_id or self.project_id
        package_version = str(package_version or "0.1.0").strip()[:48]
        if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,47}", package_version):
            raise IntegrationError("invalid_package_version", "Versão do pacote inválida.")
        dashboard = self.profile(project_id)
        profile = dashboard.get("profile")
        if not profile:
            raise IntegrationError("profile_not_configured", "Configure o perfil do jogo antes de publicar.", 409)
        if not profile.get("game_build_id"):
            raise IntegrationError("build_required", "Informe e salve a build do jogo antes de gerar o pacote.", 409)
        binding = profile.get("binding")
        plugin = profile.get("plugin")
        if not binding or not plugin:
            raise IntegrationError("plugin_required", "Vincule um plugin ao perfil do jogo antes de publicar.", 409)
        mapping = self.mapping(project_id, limit=5000)
        eligible = [item for item in mapping["items"] if item["approved_take_id"]
                    and item["audio_master_path"]
                    and (item["auto_matchable"] or item["eligible"])]
        if not eligible and not allow_empty:
            raise IntegrationError("no_eligible_takes", "Não há takes aprovados com texto único para correspondência automática nem correlação manual salva.", 409)
        seen = set()
        for item in eligible:
            if not item["eligible"]:
                continue
            key = (item["runtime_trigger_type"], item["runtime_trigger_id"])
            if key in seen:
                raise IntegrationError("trigger_conflict", "Há gatilhos duplicados no mapeamento; corrija antes de publicar.", 409)
            seen.add(key)
        try:
            version = subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True,
                                     text=True, timeout=10).stdout.splitlines()[0]
        except FileNotFoundError as error:
            raise IntegrationError("ffmpeg_missing", "FFmpeg não foi encontrado no PATH.", 503) from error
        except subprocess.SubprocessError as error:
            raise IntegrationError("ffmpeg_unavailable", "Não foi possível identificar o conversor FFmpeg.", 503) from error
        package_id = _id("PKG")
        with self.store.Session() as db:
            project = self._project(db, project_id)
            build = db.get(GameBuild, profile["game_build_id"])
            plugin_record = db.get(IntegrationPlugin, plugin["plugin_record_id"])
            if not build or not plugin_record:
                raise IntegrationError("package_dependency_missing", "A build ou o plugin vinculado não está mais disponível.", 409)
            game_name = project.game_title or project.name
            language = profile["language"]
            slug = project.slug
            plugin_manifest = plugin_record.manifest_json or {}
            audio_profile = profile["audio_profile_json"] or {}
            package_game = {"project_id": project_id, "name": game_name, "build_id": build.game_build_id,
                            "build_version": build.version, "platform": build.platform,
                            "language": language, "engine": profile["engine"]}
            plugin_info = {"plugin_id": plugin_record.plugin_id, "version": plugin_record.version,
                           "api_version": plugin_record.api_version, "status": plugin_record.status,
                           "manifest": plugin_manifest}
            takes = {}
            for item in eligible:
                take = db.get(AudioTake, item["approved_take_id"])
                if not take or take.status not in {"approved", "ready", "generated", "publishing", "publish_error", "review"} or not take.audio_master_path:
                    raise IntegrationError("active_take_missing", "Um take selecionado não possui master de áudio acessível.", 409)
                takes[item["occurrence_id"]] = take
        root_relative = f"projects/{self._safe_component(slug)}/runtime"
        package_base = f"package_{self._safe_component(package_version)}_{package_id[-8:].lower()}"
        package_relative = f"{root_relative}/{package_base}"
        zip_relative = f"{root_relative}/{package_base}.zip"
        package_dir = data_path(self.data_root, package_relative)
        zip_path = data_path(self.data_root, zip_relative)
        temp_dir = package_dir.with_name(package_dir.name + ".tmp")
        temp_dir.mkdir(parents=True, exist_ok=False)
        entries = []
        try:
            for item in eligible:
                take = takes[item["occurrence_id"]]
                source = data_path(self.data_root, take.audio_master_path)
                if not source.is_file():
                    raise IntegrationError("master_missing", f"O master do take {take.take_id} não está acessível.", 409)
                suffix = ".ogg" if audio_profile.get("format", "ogg") == "ogg" else ".wav"
                relative_audio = f"audio/{self._safe_component(take.take_id)}{suffix}"
                packaged_audio = temp_dir / relative_audio
                self._convert(source, packaged_audio, profile, version)
                entry = {
                    "occurrence_id": item["occurrence_id"], "line_id": item["line_id"],
                    "take_id": take.take_id, "audio": {"path": relative_audio,
                    "duration_ms": take.actual_duration_ms, "format": audio_profile.get("format", "ogg"),
                    "sha256": sha256_file(packaged_audio)},
                    "auto_match_keys": item["auto_match_keys"] if item["auto_matchable"] else [],
                    "source_match_keys": item["source_match_keys"] if item["auto_matchable"] else [],
                    "dub_match_keys": item["dub_match_keys"] if item["auto_matchable"] else [],
                    "speaker_key": _runtime_text_key(item["speaker"]),
                }
                if item["runtime_trigger_type"] and item["runtime_trigger_id"]:
                    entry["key"] = {"game_build_id": build.game_build_id, "language": language,
                                    "runtime_trigger_type": item["runtime_trigger_type"],
                                    "runtime_trigger_id": item["runtime_trigger_id"]}
                entries.append(entry)
            manifest = {
                "schema_version": 2,
                "package": {"package_id": package_id, "version": package_version,
                            "created_at": _now().isoformat()},
                "game": package_game,
                "plugin": plugin_info,
                "audio": audio_profile,
                "matching": {"strategy": "saved_trigger_then_exact_source_text_then_exact_dub_text",
                             "ambiguous": "skip_text_match", "saved_trigger_correlations": "auto_learned_from_confirmed_playback"},
                "event_contract": ["dialogue_start", "dialogue_end", "pause", "resume", "scene_changed",
                                   "bridge_connected", "bridge_disconnected", "error"],
                "entries": entries,
            }
            manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            (temp_dir / "runtime_manifest.json").write_bytes(manifest_bytes)
            (temp_dir / "package.json").write_text(json.dumps({
                "package_id": package_id, "version": package_version, "plugin": plugin_info,
                "runtime_hook_confirmed": bool(plugin_manifest.get("runtime_hook_confirmed")),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            (temp_dir / "mapping_report.json").write_text(json.dumps({
                "total_occurrences": mapping["total"], "approved_takes": mapping["approved_count"],
                "mapped_occurrences": mapping["mapped_count"], "packaged_entries": len(entries),
                "excluded_without_approved_take": mapping["total"] - mapping["approved_count"],
                "auto_matchable_occurrences": mapping["auto_eligible_count"],
                "ambiguous_text_occurrences": mapping["auto_ambiguous_count"],
                "excluded_without_text_match_or_saved_trigger": mapping["approved_count"] - len(eligible),
                "entries": [{"occurrence_id": item["occurrence_id"], "take_id": item["approved_take_id"],
                             "trigger_id": item["runtime_trigger_id"],
                             "auto_match_keys": item["auto_match_keys"] if item["auto_matchable"] else [],
                             "source_match_keys": item["source_match_keys"] if item["auto_matchable"] else [],
                             "dub_match_keys": item["dub_match_keys"] if item["auto_matchable"] else [],
                             "packaged": True} for item in eligible],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            config_dir = temp_dir / "config"
            plugin_dir = temp_dir / "plugin"
            config_dir.mkdir()
            plugin_dir.mkdir()
            (config_dir / "runtime.json").write_text(json.dumps({
                "game_profile_id": profile["game_profile_id"], "language": language,
                "audio": audio_profile,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            (plugin_dir / "manifest.json").write_text(json.dumps(plugin_info, ensure_ascii=False, indent=2), encoding="utf-8")
            package_dir.parent.mkdir(parents=True, exist_ok=True)
            temp_dir.replace(package_dir)
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(package_dir.rglob("*")):
                    if file.is_file():
                        archive.write(file, file.relative_to(package_dir).as_posix())
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if package_dir.exists() and not zip_path.exists():
                shutil.rmtree(package_dir, ignore_errors=True)
            raise
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        with self.store.Session.begin() as db:
            db.add(RuntimePackage(
                package_id=package_id, game_profile_id=profile["game_profile_id"],
                binding_id=binding["binding_id"], package_version=package_version,
                package_path=zip_relative, manifest_sha256=manifest_hash,
                audio_format=audio_profile.get("format", "ogg"), mapped_count=len(entries),
                status="ready", created_at=_now(),
            ))
        deployment = self._deploy_runtime_package(package_dir, profile.get("install_root"), package_id,
            (profile.get("audio_profile_json") or {}).get("volume_percent", 100))
        package_take_ids = {item["occurrence_id"]: item["approved_take_id"] for item in eligible}
        if deployment.get("status") == "deployed":
            self._mark_package_takes_ready(project_id, package_take_ids)
        return {"package_id": package_id, "version": package_version, "path": zip_relative,
                "download_url": f"/api/integration/packages/{package_id}/download",
                "manifest_sha256": manifest_hash, "entry_count": len(entries), "deployment": deployment,
                "take_ids": list(package_take_ids.values()),
                "runtime_hook_confirmed": bool(plugin_manifest.get("runtime_hook_confirmed"))}

    def package_file(self, package_id: str, project_id: str | None = None) -> Path:
        project_id = project_id or self.project_id
        with self.store.Session() as db:
            row = db.scalar(select(RuntimePackage).join(
                GameProfile, GameProfile.game_profile_id == RuntimePackage.game_profile_id,
            ).where(RuntimePackage.package_id == package_id, GameProfile.project_id == project_id))
            if row is None:
                raise IntegrationError("package_not_found", "Pacote não encontrado.", 404)
            if row.status != "ready":
                raise IntegrationError("package_stale", "O pacote está desatualizado; gere uma nova versão antes de usá-lo.", 409)
            path = data_path(self.data_root, row.package_path)
            if not path.is_file():
                raise IntegrationError("package_file_missing", "O arquivo do pacote não está mais disponível.", 404)
            return path

    def start_test_run(self, package_id: str, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        now = _now()
        with self.store.Session.begin() as db:
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id, GameProfile.profile_key == "primary",
            ))
            package = db.scalar(select(RuntimePackage).join(
                GameProfile, GameProfile.game_profile_id == RuntimePackage.game_profile_id,
            ).where(RuntimePackage.package_id == package_id, GameProfile.project_id == project_id))
            if not profile or not package or package.status != "ready":
                raise IntegrationError("package_not_ready", "Escolha um pacote pronto deste perfil.", 409)
            zip_path = data_path(self.data_root, package.package_path)
            try:
                with zipfile.ZipFile(zip_path) as archive:
                    manifest = json.loads(archive.read("runtime_manifest.json"))
            except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as error:
                raise IntegrationError("package_manifest_invalid", "O manifesto do pacote não pôde ser lido.", 409) from error
            expected = [{"occurrence_id": item["occurrence_id"],
                         "trigger_id": (item.get("key") or {}).get("runtime_trigger_id"),
                         "take_id": item["take_id"],
                         "match_mode": "text" if (item.get("source_match_keys") or item.get("dub_match_keys")
                                                     or item.get("auto_match_keys")) else "saved_trigger"}
                        for item in manifest.get("entries", [])]
            run = RuntimeTestRun(
                test_run_id=_id("TST"), game_profile_id=profile.game_profile_id,
                package_id=package.package_id, status="running", expected_count=len(expected),
                result_count=0, summary_json={"expected": expected, "results": {}},
                started_at=now, finished_at=None,
            )
            db.add(run)
            return _row(run)

    def record_test_item(self, test_run_id: str, body: dict, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        occurrence_id = str(body.get("occurrence_id") or "").strip()
        received_trigger = str(body.get("received_trigger_id") or "").strip() or None
        played_take = str(body.get("played_take_id") or "").strip() or None
        content_matches = body.get("content_matches")
        scene_matches = body.get("scene_matches")
        event_type = str(body.get("event_type") or "dialogue_start").strip()
        if event_type not in {"dialogue_start", "dialogue_end", "pause", "resume", "scene_changed",
                              "bridge_connected", "bridge_disconnected", "error"}:
            raise IntegrationError("invalid_event_type", "Evento fora do contrato runtime.")
        latency = body.get("latency_ms")
        if latency is not None and (not isinstance(latency, int) or latency < 0 or latency > 600_000):
            raise IntegrationError("invalid_latency", "Latência inválida.")
        if content_matches is not None and not isinstance(content_matches, bool):
            raise IntegrationError("invalid_content_match", "A confirmação do conteúdo deve ser booleana.")
        if scene_matches is not None and not isinstance(scene_matches, bool):
            raise IntegrationError("invalid_scene_match", "A confirmação da fala no jogo deve ser booleana.")
        with self.store.Session.begin() as db:
            run = db.scalar(select(RuntimeTestRun).join(
                GameProfile, GameProfile.game_profile_id == RuntimeTestRun.game_profile_id,
            ).where(RuntimeTestRun.test_run_id == test_run_id, GameProfile.project_id == project_id))
            if run is None:
                raise IntegrationError("test_run_not_found", "Execução de teste não encontrada.", 404)
            if run.status != "running":
                raise IntegrationError("test_run_closed", "Esta execução já foi encerrada.", 409)
            summary = dict(run.summary_json or {})
            expected = next((item for item in summary.get("expected", [])
                             if item["occurrence_id"] == occurrence_id), None)
            if expected is None:
                raise IntegrationError("test_occurrence_unexpected", "A ocorrência não pertence ao pacote testado.", 409)
            previous = db.scalar(select(RuntimeTestItem.test_item_id).where(
                RuntimeTestItem.test_run_id == test_run_id,
                RuntimeTestItem.occurrence_id == occurrence_id,
                RuntimeTestItem.event_type == "dialogue_start",
            ).limit(1)) if event_type == "dialogue_start" else None
            result = "duplicate" if previous else "ok"
            if result != "duplicate" and received_trigger != expected["trigger_id"]:
                result = "wrong_trigger"
            if result == "ok" and event_type == "dialogue_start" and played_take != expected["take_id"]:
                result = "wrong_take"
            if result == "ok" and event_type == "dialogue_start" and content_matches is False:
                result = "wrong_content"
            if result == "ok" and event_type == "dialogue_start" and scene_matches is False:
                result = "wrong_mapping"
            item = RuntimeTestItem(
                test_item_id=_id("TIT"), test_run_id=test_run_id,
                occurrence_id=occurrence_id, expected_trigger_id=expected["trigger_id"],
                received_trigger_id=received_trigger, expected_take_id=expected["take_id"],
                played_take_id=played_take, event_type=event_type, result=result,
                latency_ms=latency, diagnostic_json={"source": "manual_entry",
                                                    "content_matches": content_matches,
                                                    "scene_matches": scene_matches}, created_at=_now(),
            )
            db.add(item)
            counts = dict(summary.get("results") or {})
            counts[result] = int(counts.get(result, 0)) + 1
            summary["results"] = counts
            run.summary_json = summary
            run.result_count += 1
            return _row(item)

    def finish_test_run(self, test_run_id: str, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        now = _now()
        with self.store.Session.begin() as db:
            run = db.scalar(select(RuntimeTestRun).join(
                GameProfile, GameProfile.game_profile_id == RuntimeTestRun.game_profile_id,
            ).where(RuntimeTestRun.test_run_id == test_run_id, GameProfile.project_id == project_id))
            if run is None:
                raise IntegrationError("test_run_not_found", "Execução de teste não encontrada.", 404)
            results = db.scalars(select(RuntimeTestItem).where(
                RuntimeTestItem.test_run_id == test_run_id,
            )).all()
            ok_count = sum(1 for item in results if item.result == "ok" and item.event_type == "dialogue_start")
            run.status = "passed" if run.expected_count >= 10 and ok_count >= 10 and not any(
                item.result in {"wrong_trigger", "wrong_take", "wrong_content", "wrong_mapping", "duplicate"} for item in results
            ) else "incomplete"
            run.finished_at = now
            summary = dict(run.summary_json or {})
            summary["acceptance_gate"] = {
                "required_dialogues": 10, "expected_dialogues": run.expected_count,
                "correct_starts": ok_count, "passed": run.status == "passed",
            }
            run.summary_json = summary
            return _row(run)

    def test_runs(self, project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        with self.store.Session() as db:
            profile = db.scalar(select(GameProfile).where(
                GameProfile.project_id == project_id, GameProfile.profile_key == "primary",
            ))
            if not profile:
                return {"test_runs": []}
            runs = db.scalars(select(RuntimeTestRun).where(
                RuntimeTestRun.game_profile_id == profile.game_profile_id,
            ).order_by(RuntimeTestRun.started_at.desc()).limit(20)).all()
            return {"test_runs": [_row(item) for item in runs]}

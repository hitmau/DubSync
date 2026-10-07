from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from ..database.models import (
    AudioTake, DialogueLine, DialogueOccurrence, DubbingCharacter, DubbingProject, GameBuild,
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


def _row(row) -> dict | None:
    if row is None:
        return None
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        result[column.name] = value.isoformat() if isinstance(value, datetime) else value
    return result


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
            "description": "Contrato inicial para o piloto Unity IL2CPP; o hook real depende da prova de conceito do loader.",
            "engines_json": ["Unity IL2CPP"],
            "games_json": ["replaced"],
            "capabilities_json": ["dialogue_start", "dialogue_end", "pause", "resume", "scene_changed", "external_audio"],
            "origin": "builtin",
            "trust_status": "builtin",
            "status": "adapter_pending",
            "manifest_json": {
                "adapter_id": "replaced.articy",
                "trigger_type": "articy_external_id",
                "events": ["dialogue_start", "dialogue_end", "pause", "resume", "scene_changed", "bridge_connected", "bridge_disconnected", "error"],
                "entrypoint": "integration/adapters/replaced_articy.py",
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
            result["runtime_ready"] = bool(plugin and plugin.status == "installed")
            result["executable_exists"] = bool(profile.executable_path and Path(profile.executable_path).is_file())
            return {"profile": result, "project_id": project_id}

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
        now = _now()
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
                        "bitrate_kbps": bitrate, "converter_policy": "ffmpeg-controlled-v1"}
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
                profile.name = project.game_title or project.name
                profile.language = language
                profile.game_build_id = build.game_build_id if build else None
                profile.engine = engine
                profile.executable_path = executable
                profile.install_root = install_root
                profile.audio_profile_json = settings
                profile.status = "configured"
                profile.updated_at = now
                db.query(RuntimePackage).filter(
                    RuntimePackage.game_profile_id == profile.game_profile_id,
                    RuntimePackage.status == "ready",
                ).update({RuntimePackage.status: "stale"})
            profile_id = profile.game_profile_id
        return self.profile(project_id)

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
            takes = db.scalars(select(AudioTake).where(
                AudioTake.status == "approved",
                AudioTake.occurrence_id.in_([item.occurrence_id for item in occurrences] or ["__none__"]),
            ).order_by(AudioTake.occurrence_id, AudioTake.version.desc())).all()
            take_by_occurrence = {}
            for take in takes:
                take_by_occurrence.setdefault(take.occurrence_id, take)
            rows = []
            for occurrence in occurrences:
                line = line_by_id.get(occurrence.line_id)
                take = take_by_occurrence.get(occurrence.occurrence_id)
                rows.append({
                    "occurrence_id": occurrence.occurrence_id,
                    "line_id": occurrence.line_id,
                    "speaker": character_names.get(line.character_id) if line and line.character_id else None,
                    "source_text": line.source_text if line else "",
                    "dub_text": line.dub_text if line else "",
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
            total = len(rows)
            page = rows[offset:offset + limit]
            mapped = sum(1 for row in rows if row["runtime_trigger_id"] and row["runtime_trigger_type"])
            approved = sum(1 for row in rows if row["approved_take_id"])
            eligible = sum(1 for row in rows if row["eligible"])
            return {"project_id": project_id, "total": total, "mapped_count": mapped,
                    "approved_count": approved, "eligible_count": eligible,
                    "unmapped_count": total - mapped, "items": page,
                    "limit": limit, "offset": offset}

    def map_trigger(self, occurrence_id: str, trigger_type: str, trigger_id: str,
                    project_id: str | None = None) -> dict:
        project_id = project_id or self.project_id
        trigger_type = str(trigger_type or "").strip().lower()
        trigger_id = str(trigger_id or "").strip()
        if trigger_type not in {"articy_external_id", "custom_event_id"}:
            raise IntegrationError("invalid_trigger_type", "Use articy_external_id no piloto REPLACED.")
        if not trigger_id or len(trigger_id) > 160 or any(ord(char) < 32 for char in trigger_id):
            raise IntegrationError("invalid_trigger_id", "Informe um gatilho real de até 160 caracteres.")
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
            "runtime": "not_implemented",
            "manifest_status": "ready_for_packaging" if data["eligible_count"] else "needs_mapping",
            "approved_occurrence_count": len(entries),
            "mapped_occurrence_count": data["mapped_count"],
            "eligible_occurrence_count": data["eligible_count"],
            "unmapped_occurrence_count": data["unmapped_count"],
            "occurrences": entries,
        }

    @staticmethod
    def _safe_component(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._")
        if not safe or safe in {".", ".."}:
            raise IntegrationError("invalid_package_path", "O nome do pacote contém caracteres inválidos.")
        return safe[:96]

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

    def build_package(self, package_version: str = "0.1.0", project_id: str | None = None) -> dict:
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
        mapping = self.mapping(project_id, limit=500)
        eligible = [item for item in mapping["items"] if item["eligible"]]
        if not eligible:
            raise IntegrationError("no_eligible_takes", "Não há takes aprovados associados a gatilhos reais para empacotar.", 409)
        seen = set()
        for item in eligible:
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
                if not take or take.status != "approved" or not take.audio_master_path:
                    raise IntegrationError("approved_take_missing", "Um take aprovado não possui master de áudio acessível.", 409)
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
                entries.append({
                    "key": {"game_build_id": build.game_build_id, "language": language,
                            "runtime_trigger_type": item["runtime_trigger_type"],
                            "runtime_trigger_id": item["runtime_trigger_id"]},
                    "occurrence_id": item["occurrence_id"], "line_id": item["line_id"],
                    "take_id": take.take_id, "audio": {"path": relative_audio,
                    "duration_ms": take.actual_duration_ms, "format": audio_profile.get("format", "ogg"),
                    "sha256": sha256_file(packaged_audio)},
                })
            manifest = {
                "schema_version": 1,
                "package": {"package_id": package_id, "version": package_version,
                            "created_at": _now().isoformat()},
                "game": package_game,
                "plugin": plugin_info,
                "audio": audio_profile,
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
                "excluded_without_trigger": mapping["approved_count"] - len(entries),
                "entries": [{"occurrence_id": item["occurrence_id"], "take_id": item["approved_take_id"],
                             "trigger_id": item["runtime_trigger_id"], "packaged": True} for item in eligible],
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
        return {"package_id": package_id, "version": package_version, "path": zip_relative,
                "download_url": f"/api/integration/packages/{package_id}/download",
                "manifest_sha256": manifest_hash, "entry_count": len(entries),
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
            expected = [{"occurrence_id": item["occurrence_id"], "trigger_id": item["key"]["runtime_trigger_id"],
                         "take_id": item["take_id"]} for item in manifest.get("entries", [])]
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
        event_type = str(body.get("event_type") or "dialogue_start").strip()
        if event_type not in {"dialogue_start", "dialogue_end", "pause", "resume", "scene_changed",
                              "bridge_connected", "bridge_disconnected", "error"}:
            raise IntegrationError("invalid_event_type", "Evento fora do contrato runtime.")
        latency = body.get("latency_ms")
        if latency is not None and (not isinstance(latency, int) or latency < 0 or latency > 600_000):
            raise IntegrationError("invalid_latency", "Latência inválida.")
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
            item = RuntimeTestItem(
                test_item_id=_id("TIT"), test_run_id=test_run_id,
                occurrence_id=occurrence_id, expected_trigger_id=expected["trigger_id"],
                received_trigger_id=received_trigger, expected_take_id=expected["take_id"],
                played_take_id=played_take, event_type=event_type, result=result,
                latency_ms=latency, diagnostic_json={"source": "manual_entry"}, created_at=_now(),
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
                item.result in {"wrong_trigger", "wrong_take", "duplicate"} for item in results
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

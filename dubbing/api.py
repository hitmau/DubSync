from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ..capture.session_manager import DATA_DIR
from ..app.settings import load_settings
from ..database.models import SessionRecord
from ..providers.omnivoice import OmniVoiceError, OmniVoiceProvider
from ..projects.context import current_project_id, reset_project_context, set_project_context
from .audio import ALLOWED_AUDIO_SUFFIXES, MAX_REFERENCE_BYTES, apply_voice_effect, data_path, extract_video_audio, normalize_reference, wav_duration_ms
from .service import DubbingError, DubbingService, new_id
from .voice_design import validate_voice_design


class LineChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dub_text: str | None = Field(default=None, max_length=4000)
    character_id: str | None = Field(default=None, max_length=48)
    emotion: str | None = Field(default=None, max_length=80)
    emotion_intensity: float | None = Field(default=None, ge=0, le=1)
    direction: str | None = Field(default=None, max_length=2000)


class CharacterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    notes: str = Field(default="", max_length=2000)
    name_verified: bool = False
    active: bool = True


class CharacterVoiceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    voice_profile_id: str | None = Field(default=None, max_length=48)


class GenerationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration: float | None = Field(default=None, ge=0.1, le=60)
    denoise: bool = True
    num_step: int = Field(default=32, ge=16, le=32)
    guidance_scale: float = Field(default=2, ge=0, le=10)
    t_shift: float = Field(default=0.1, ge=0, le=2)
    position_temperature: float = Field(default=5, ge=0, le=10)
    class_temperature: float = Field(default=0, ge=0, le=10)
    layer_penalty_factor: float = Field(default=5, ge=0, le=10)
    normalize_text: bool = False
    preprocess_prompt: bool = True
    postprocess_output: bool = True
    pad_duration: float = Field(default=0.1, ge=0, le=10)
    fade_duration: float = Field(default=0.1, ge=0, le=10)
    audio_chunk_duration: float = Field(default=15, ge=0.5, le=120)
    audio_chunk_threshold: float = Field(default=30, ge=0.5, le=600)


class VoiceProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character_id: str = Field(min_length=1, max_length=48)
    name: str = Field(min_length=1, max_length=120)
    language: str = Field(default="pt-BR", min_length=2, max_length=16)
    voice_mode: str = Field(default="clone", pattern=r"^(clone|design)$")
    default_speed: float = Field(default=1, ge=0.5, le=2)
    default_instruct: str = Field(default="", max_length=2000)
    default_direction: str = Field(default="", max_length=2000)
    preset_key: str | None = Field(default=None, max_length=160)
    reference_text: str | None = Field(default=None, max_length=4000)
    preview_text: str = Field(default="", max_length=1000)
    voice_effect: str = Field(default="none", pattern=r"^(none|raspy|robotic)$")
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)
    use_authorized: bool = False


class ProfileActivityBody(BaseModel):
    active: bool


class VoicePreviewBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    instruct: str = Field(default="", max_length=2000)
    speed: float = Field(default=1, ge=0.5, le=2)
    voice_effect: str = Field(default="none", pattern=r"^(none|raspy|robotic)$")
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)


class VoiceDesignCandidatesBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=1000)
    instruct: str = Field(min_length=1, max_length=2000)
    count: int = Field(default=3, ge=1, le=10)
    speed: float = Field(default=1, ge=0.5, le=2)
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)


class TakeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occurrence_id: str = Field(min_length=1, max_length=48)
    voice_profile_id: str | None = Field(default=None, max_length=48)
    instruct: str = Field(default="", max_length=2000)
    speed: float | None = Field(default=None, ge=0.5, le=2)
    manual_override: bool = False
    generation_settings: GenerationSettings = Field(default_factory=GenerationSettings)


class BatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=36)
    ignore_missing: bool = False
    mode: str = Field(default="block", pattern=r"^(selected|character|block|pending|video_character)$")
    occurrence_ids: list[str] = Field(default_factory=list, max_length=500)
    character_id: str | None = Field(default=None, max_length=48)
    session_ids: list[str] = Field(default_factory=list, max_length=5000)


class TakeReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern=r"^(approve|reject)$")
    note: str = Field(default="", max_length=1000)


class ReferenceExtractionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=36)
    voice_profile_id: str = Field(min_length=1, max_length=48)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    reference_text: str | None = Field(default=None, max_length=4000)


class ReferenceApprovalBody(BaseModel):
    approved: bool = True


class PresetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    emotion: str = Field(min_length=1, max_length=80)
    intensity: float = Field(default=0.7, ge=0, le=1)
    instruct: str = Field(min_length=1, max_length=2000)


def _raise_dubbing_error(error: DubbingError):
    raise HTTPException(error.status_code, {"code": error.code, "message": error.message}) from error


def _local_request(request: Request):
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}:
        raise HTTPException(403, {"code": "local_only", "message": "A área de dublagem aceita conexões somente deste computador."})


def _safe_reference_name(filename: str) -> str:
    suffix = Path(filename or "reference.wav").suffix.lower()
    if suffix not in ALLOWED_AUDIO_SUFFIXES:
        raise DubbingError("unsupported_audio", "Use WAV, MP3, M4A, AAC, FLAC, OGG ou OPUS.")
    return suffix


def create_router(service: DubbingService, worker, docker, auto_publish_callback=None) -> APIRouter:
    router = APIRouter(prefix="/api/dubbing", tags=["dubbing"], dependencies=[Depends(_local_request)])
    design_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dubsync-voice-design")

    def run_design_batch(project_id: str, voice_profile_id: str, batch_id: str) -> None:
        token = set_project_context(project_id)
        try:
            config = load_settings()["dubbing"]
            provider = OmniVoiceProvider(config["base_url"], config["timeout_seconds"])
            variations = (
                {"class_temperature": 0.35, "position_temperature": 3},
                {"class_temperature": 0.75, "position_temperature": 5},
                {"class_temperature": 1.15, "position_temperature": 7},
            )
            for candidate in service.design_candidates_in_batch(voice_profile_id, batch_id):
                temp_path = None
                try:
                    profile = service.profile(voice_profile_id)
                    take_id = new_id("TAKE")
                    output_relative = f"temp/omnivoice/previews/{take_id}.wav"
                    temp_path = service.data_root / "temp" / "omnivoice" / "previews" / f"{take_id}.wav"
                    settings = dict(candidate["generation_settings_json"] or {})
                    settings.update(variations[(candidate["candidate_index"] - 1) % len(variations)])
                    queued = provider.generate({
                        "take_id": take_id, "voice_profile_id": voice_profile_id,
                        "voice_mode": "design", "text": candidate["text_used"],
                        "language": profile["language"].split("-")[0].lower(),
                        "speed": candidate["speed_used"], "instruct": candidate["instruct_used"],
                        "generation_settings": settings, "output_relative_path": output_relative,
                    })
                    service.update_design_candidate(candidate["candidate_id"], status="generating",
                                                     take_id=take_id, provider_job_id=queued["job_id"])
                    deadline = time.monotonic() + max(60, min(1800, int(config["timeout_seconds"])))
                    while time.monotonic() < deadline:
                        job = provider.job(queued["job_id"], timeout=min(10, int(config["timeout_seconds"])))
                        if job.get("status") == "completed":
                            audio = provider.audio(take_id, timeout=min(180, int(config["timeout_seconds"])))
                            destination = data_path(service.data_root, candidate["audio_path"])
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_bytes(audio)
                            duration_ms = wav_duration_ms(destination)
                            service.update_design_candidate(candidate["candidate_id"], status="completed",
                                                             duration_ms=duration_ms, error_message=None)
                            break
                        if job.get("status") == "error":
                            raise OmniVoiceError(job.get("error") or "O OmniVoice não conseguiu gerar esta variação.",
                                                503, "candidate_generation_failed")
                        time.sleep(1)
                    else:
                        raise OmniVoiceError("O tempo para gerar este candidato foi excedido.", 504, "candidate_timeout")
                except Exception as error:
                    if temp_path:
                        temp_path.unlink(missing_ok=True)
                    try:
                        candidate_path = data_path(service.data_root, candidate["audio_path"])
                        candidate_path.unlink(missing_ok=True)
                    except (KeyError, ValueError, OSError):
                        pass
                    message = str(error)[:600] or "Falha ao gerar este candidato."
                    service.update_design_candidate(candidate["candidate_id"], status="error", error_message=message)
                    if isinstance(error, OmniVoiceError) and error.code == "provider_offline":
                        for pending in service.design_candidates_in_batch(voice_profile_id, batch_id):
                            if pending["status"] == "queued":
                                service.update_design_candidate(pending["candidate_id"], status="error",
                                                                 error_message="OmniVoice offline; inicie o serviço e gere novos candidatos.")
                        break
                finally:
                    if temp_path:
                        temp_path.unlink(missing_ok=True)
        finally:
            reset_project_context(token)

    @router.get("/overview")
    def overview(session_id: str | None = None):
        service.initialize()
        return service.overview(session_id)

    @router.get("/lines")
    def lines(session_id: str | None = None,
              status: str = Query(default="not-discarded", pattern=r"^(all|not-discarded|eligible|pending|discarded|without-take|review|approved|error)$"),
              character_id: str | None = None,
              q: str = Query(default="", max_length=200)):
        return {"items": service.list_lines(session_id, status, character_id, q)}

    @router.get("/lines/{line_id}")
    def get_line(line_id: str, occurrence_id: str | None = None):
        try:
            return service.line_detail(line_id, occurrence_id)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.put("/lines/{line_id}")
    def update_line(line_id: str, body: LineChanges):
        try:
            return service.update_line(line_id, body.model_dump(exclude_unset=True))
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/characters", status_code=201)
    def create_character(body: CharacterBody):
        try:
            return service.save_character(body.model_dump())
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.put("/characters/{character_id}")
    def update_character(character_id: str, body: CharacterBody):
        try:
            return service.save_character(body.model_dump(), character_id)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.put("/characters/{character_id}/voice-profile")
    def assign_character_voice(character_id: str, body: CharacterVoiceBody):
        try:
            return service.assign_character_voice(character_id, body.voice_profile_id)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.get("/characters/{character_id}/roi-audio")
    def play_roi_audio(character_id: str):
        try:
            path = service.roi_audio_path(character_id)
            media_type = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
            return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, no-store"})
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/characters/{character_id}/import-roi-mp3", include_in_schema=False)
    @router.post("/characters/{character_id}/import-roi-audio")
    def import_roi_audio(character_id: str):
        try:
            return service.import_roi_voice_reference(character_id)
        except DubbingError as error:
            _raise_dubbing_error(error)
        except ValueError as error:
            raise HTTPException(422, {"code": "invalid_audio", "message": str(error)}) from error
        except RuntimeError as error:
            raise HTTPException(422, {"code": "audio_processing_failed", "message": str(error)}) from error

    @router.post("/voice-profiles/{voice_profile_id}/sync-roi-audio")
    def sync_roi_audio_for_profile(voice_profile_id: str):
        try:
            profile = service.profile(voice_profile_id)
            return service.import_roi_voice_reference(
                profile["character_id"], voice_profile_id, profile.get("reference_text"),
            )
        except DubbingError as error:
            _raise_dubbing_error(error)
        except ValueError as error:
            raise HTTPException(422, {"code": "invalid_audio", "message": str(error)}) from error
        except RuntimeError as error:
            raise HTTPException(422, {"code": "audio_processing_failed", "message": str(error)}) from error

    @router.post("/voice-profiles", status_code=201)
    def create_voice_profile(body: VoiceProfileBody):
        try:
            return service.save_voice_profile(body.model_dump())
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.put("/voice-profiles/{voice_profile_id}")
    def update_voice_profile(voice_profile_id: str, body: VoiceProfileBody):
        try:
            if body.character_id != service.profile(voice_profile_id)["character_id"]:
                raise DubbingError("profile_character_mismatch", "O perfil não pode ser movido para outro personagem.")
            return service.save_voice_profile(body.model_dump(), voice_profile_id)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.put("/voice-profiles/{voice_profile_id}/active")
    def set_voice_profile_active(voice_profile_id: str, body: ProfileActivityBody):
        try:
            return service.set_voice_profile_active(voice_profile_id, body.active)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.delete("/voice-profiles/{voice_profile_id}")
    def delete_voice_profile(voice_profile_id: str):
        try:
            return service.delete_voice_profile(voice_profile_id)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.delete("/voice-profiles/{voice_profile_id}/reference")
    def clear_voice_profile_reference(voice_profile_id: str):
        try:
            return service.clear_profile_reference(voice_profile_id)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/voice-profiles/{voice_profile_id}/references", status_code=201)
    async def upload_reference(voice_profile_id: str, request: Request,
                               filename: str = Query(default="reference.wav", max_length=260),
                               reference_text: str | None = Query(default=None, max_length=4000)):
        try:
            suffix = _safe_reference_name(filename)
            length = int(request.headers.get("content-length") or 0)
            if length <= 0 or length > MAX_REFERENCE_BYTES:
                raise DubbingError("invalid_upload_size", "O arquivo deve ter até 50 MiB.", 413)
            payload = await request.body()
            if not payload or len(payload) > MAX_REFERENCE_BYTES:
                raise DubbingError("invalid_upload_size", "O arquivo deve ter até 50 MiB.", 413)
            profile = service.profile(voice_profile_id)
            ref_id = new_id("REF")
            ref_dir = service.project_data_root / "voices" / voice_profile_id / "refs"
            ref_dir.mkdir(parents=True, exist_ok=True)
            original = ref_dir / f"{ref_id}_original{suffix}"
            normalized = ref_dir / f"{ref_id}.wav"
            original.write_bytes(payload)
            metadata = await run_in_threadpool(normalize_reference, original, normalized)
            item = service.add_reference(original, normalized, metadata, reference_text,
                                          "uploaded", "Arquivo selecionado no Studio de Dublagem.")
            return {"reference": item, "profile": service.profile(voice_profile_id)}
        except DubbingError as error:
            _raise_dubbing_error(error)
        except ValueError as error:
            raise HTTPException(400, {"code": "invalid_audio", "message": str(error)}) from error
        except RuntimeError as error:
            raise HTTPException(422, {"code": "audio_processing_failed", "message": str(error)}) from error

    @router.post("/voice-profiles/{voice_profile_id}/generated-reference", status_code=201)
    async def select_generated_reference(voice_profile_id: str, request: Request,
                                         reference_text: str | None = Query(default=None, max_length=4000)):
        original = normalized = None
        try:
            length = int(request.headers.get("content-length") or 0)
            if length <= 0 or length > MAX_REFERENCE_BYTES:
                raise DubbingError("invalid_upload_size", "O candidato deve ter até 50 MiB.", 413)
            payload = await request.body()
            if not payload or len(payload) > MAX_REFERENCE_BYTES:
                raise DubbingError("invalid_upload_size", "O candidato deve ter até 50 MiB.", 413)
            profile = service.profile(voice_profile_id)
            if not profile["active"] or not profile["use_authorized"] or profile["voice_mode"] != "design":
                raise DubbingError("profile_not_design", "Salve e autorize um perfil no modo Gerar voz antes de selecionar o candidato.", 409)
            ref_id = new_id("REF")
            ref_dir = service.project_data_root / "voices" / voice_profile_id / "refs"
            ref_dir.mkdir(parents=True, exist_ok=True)
            original = ref_dir / f"{ref_id}_candidate.wav"
            normalized = ref_dir / f"{ref_id}.wav"
            original.write_bytes(payload)
            metadata = await run_in_threadpool(normalize_reference, original, normalized)
            selected = service.select_design_candidate(
                voice_profile_id, original, normalized, metadata, reference_text,
            )
            return selected
        except DubbingError as error:
            if original: original.unlink(missing_ok=True)
            if normalized: normalized.unlink(missing_ok=True)
            _raise_dubbing_error(error)
        except ValueError as error:
            if original: original.unlink(missing_ok=True)
            if normalized: normalized.unlink(missing_ok=True)
            raise HTTPException(400, {"code": "invalid_audio", "message": str(error)}) from error
        except RuntimeError as error:
            if original: original.unlink(missing_ok=True)
            if normalized: normalized.unlink(missing_ok=True)
            raise HTTPException(422, {"code": "audio_processing_failed", "message": str(error)}) from error

    @router.post("/voice-profiles/{voice_profile_id}/design-candidates", status_code=202)
    def enqueue_voice_design_candidates(voice_profile_id: str, body: VoiceDesignCandidatesBody):
        try:
            profile = service.profile(voice_profile_id)
            status = OmniVoiceProvider(load_settings()["dubbing"]["base_url"],
                                       load_settings()["dubbing"]["timeout_seconds"]).model_status()
            if not status.get("model_loaded"):
                raise OmniVoiceError("O modelo OmniVoice ainda está carregando.", 503, "model_loading")
            batch = service.create_design_candidates(
                voice_profile_id, body.text, body.instruct, body.count, body.speed,
                body.generation_settings.model_dump(exclude_none=True),
            )
            try:
                design_executor.submit(run_design_batch, current_project_id(), voice_profile_id, batch["batch_id"])
            except RuntimeError as error:
                for candidate in batch["items"]:
                    service.update_design_candidate(candidate["candidate_id"], status="error",
                                                     error_message="Não foi possível iniciar a fila local de candidatos.")
                raise HTTPException(503, "A fila local de candidatos não está disponível.") from error
            return {**batch, "profile": profile}
        except DubbingError as error:
            _raise_dubbing_error(error)
        except OmniVoiceError as error:
            raise HTTPException(error.status_code or 503,
                                {"code": error.code, "message": str(error)}) from error

    @router.get("/voice-profiles/{voice_profile_id}/design-candidates")
    def list_voice_design_candidates(voice_profile_id: str, limit: int = Query(default=100, ge=1, le=500)):
        try:
            return {"items": service.list_design_candidates(voice_profile_id, limit)}
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.get("/voice-profiles/{voice_profile_id}/design-candidates/{candidate_id}/audio")
    def play_voice_design_candidate(voice_profile_id: str, candidate_id: str):
        try:
            candidate = service.design_candidate(voice_profile_id, candidate_id)
            if candidate["status"] != "completed" or not candidate.get("audio_path"):
                raise DubbingError("voice_candidate_not_ready", "Este candidato ainda não tem áudio pronto.", 409)
            path = data_path(service.data_root, candidate["audio_path"])
            if not path.is_file():
                raise DubbingError("voice_candidate_audio_missing", "O arquivo deste candidato não está disponível.", 404)
            return FileResponse(path, media_type="audio/wav", filename=f"{candidate_id}.wav",
                                headers={"Cache-Control": "private, no-store"})
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/voice-design-candidates/cleanup-official")
    def cleanup_official_design_auditions():
        try:
            return service.cleanup_official_design_auditions()
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/voice-profiles/{voice_profile_id}/design-candidates/{candidate_id}/select", status_code=201)
    def select_persisted_design_candidate(voice_profile_id: str, candidate_id: str):
        original = normalized = None
        try:
            profile = service.profile(voice_profile_id)
            candidate = service.design_candidate(voice_profile_id, candidate_id)
            if candidate["status"] != "completed" or not candidate.get("audio_path"):
                raise DubbingError("voice_candidate_not_ready", "Aguarde este candidato ficar pronto antes de escolhê-lo.", 409)
            source = data_path(service.data_root, candidate["audio_path"])
            if not source.is_file():
                raise DubbingError("voice_candidate_audio_missing", "O áudio deste candidato não está disponível.", 404)
            ref_id = new_id("REF")
            ref_dir = service.project_data_root / "voices" / voice_profile_id / "refs"
            ref_dir.mkdir(parents=True, exist_ok=True)
            original = ref_dir / f"{ref_id}_candidate.wav"
            normalized = ref_dir / f"{ref_id}.wav"
            shutil.copyfile(source, original)
            metadata = normalize_reference(original, normalized)
            return service.select_design_candidate(
                voice_profile_id, original, normalized, metadata, candidate["text_used"],
                f"Candidato persistido {candidate_id} escolhido como voz oficial.",
            )
        except DubbingError as error:
            if original: original.unlink(missing_ok=True)
            if normalized: normalized.unlink(missing_ok=True)
            _raise_dubbing_error(error)
        except ValueError as error:
            if original: original.unlink(missing_ok=True)
            if normalized: normalized.unlink(missing_ok=True)
            raise HTTPException(400, {"code": "invalid_audio", "message": str(error)}) from error
        except RuntimeError as error:
            if original: original.unlink(missing_ok=True)
            if normalized: normalized.unlink(missing_ok=True)
            raise HTTPException(422, {"code": "audio_processing_failed", "message": str(error)}) from error

    @router.post("/voice-profiles/{voice_profile_id}/preview")
    async def preview_voice_profile(voice_profile_id: str, body: VoicePreviewBody):
        def synthesize() -> bytes:
            profile = service.profile(voice_profile_id)
            can_audition_design = bool(profile.get("active") and profile.get("use_authorized")
                                       and profile.get("voice_mode") == "design"
                                       and profile.get("default_instruct", "").strip())
            if not profile.get("ready") and not can_audition_design:
                raise DubbingError("voice_profile_not_ready", "Autorize o perfil e escolha uma voz oficial antes de testar.", 409)
            reference = profile.get("reference") or {}
            config = load_settings()["dubbing"]
            provider = OmniVoiceProvider(config["base_url"], config["timeout_seconds"])
            status = provider.model_status()
            if not status.get("model_loaded"):
                raise OmniVoiceError("O modelo OmniVoice ainda está carregando.", 503, "model_loading")
            take_id = new_id("TAKE")
            reference_path = None
            # Keep the profile in design mode; reuse its chosen generated audio
            # as a reference so previews match the production voice.
            use_official_voice = profile["voice_mode"] == "clone" or (
                profile.get("provider_voice_key") == "dubsync:design-candidate" and profile.get("ready")
            )
            if use_official_voice:
                reference_path = (service.data_root / str(reference.get("normalized_path") or "")).resolve()
                if not reference_path.is_relative_to(service.project_data_root.resolve()) or not reference_path.is_file():
                    raise DubbingError("voice_reference_missing", "O áudio de referência local não está disponível.", 404)
                provider.prepare_voice(
                    profile["voice_profile_id"],
                    reference_path.relative_to(service.data_root.resolve()).as_posix(),
                    reference.get("reference_text"), profile["language"],
                )
            output_relative = f"temp/omnivoice/previews/{take_id}.wav"
            queued = provider.generate({
                "take_id": take_id, "voice_profile_id": profile["voice_profile_id"],
                "voice_mode": "clone" if use_official_voice else "design", "text": body.text.strip(),
                "language": profile["language"].split("-")[0].lower(), "speed": body.speed,
                "instruct": "" if use_official_voice else validate_voice_design(
                    body.instruct.strip() or profile.get("default_instruct", "")
                ),
                "generation_settings": {
                    **(body.generation_settings.model_dump(exclude_none=True) or profile.get("generation_settings_json", {})),
                    **({"position_temperature": 0.0, "class_temperature": 0.0} if use_official_voice else {}),
                },
                "output_relative_path": output_relative,
            })
            deadline = time.monotonic() + max(60, min(600, int(config["timeout_seconds"])))
            try:
                while time.monotonic() < deadline:
                    job = provider.job(queued["job_id"], timeout=10)
                    if job.get("status") == "completed":
                        raw_audio = provider.audio(take_id, timeout=120)
                        return apply_voice_effect(raw_audio, body.voice_effect)
                    if job.get("status") == "error":
                        raise OmniVoiceError(job.get("error") or "A prévia OmniVoice falhou.", 503, "preview_failed")
                    time.sleep(1)
                raise OmniVoiceError("Tempo limite para gerar a prévia do perfil excedido.", 504, "preview_timeout")
            finally:
                (service.data_root / "temp" / "omnivoice" / "previews" / f"{take_id}.wav").unlink(missing_ok=True)

        try:
            audio = await run_in_threadpool(synthesize)
            return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
        except DubbingError as error:
            _raise_dubbing_error(error)
        except OmniVoiceError as error:
            raise HTTPException(error.status_code or 503, {"code": error.code, "message": str(error)}) from error
        except ValueError as error:
            raise HTTPException(422, {"code": "invalid_voice_design", "message": str(error)}) from error

    @router.post("/voice-references/extract", status_code=201)
    async def extract_reference(body: ReferenceExtractionBody):
        try:
            profile = service.profile(body.voice_profile_id)
            if body.end_time <= body.start_time or body.end_time - body.start_time > 60:
                raise DubbingError("invalid_range", "A extração deve durar até 60 segundos.")
            with service.store.Session() as db:
                session = db.get(SessionRecord, body.session_id)
                if not session:
                    raise DubbingError("session_not_found", "Sessão de captura não encontrada.", 404)
                config = session.config_json or {}
                video_value = config.get("downloaded_path") or config.get("path")
                if not video_value:
                    video_value = session.source_uri if session.source_type == "file" else ""
                video_path = Path(str(video_value)).expanduser().resolve() if video_value else None
            if not video_path or not video_path.is_file():
                raise DubbingError("video_unavailable", "O arquivo de vídeo desta sessão não está disponível.", 422)
            ref_id = new_id("REF")
            ref_dir = service.project_data_root / "voices" / body.voice_profile_id / "refs"
            source = service.project_data_root / "temp" / f"{ref_id}_extracted.wav"
            normalized = source.with_name(f"{source.stem}_normalized.wav")
            metadata = await run_in_threadpool(extract_video_audio, video_path, body.start_time,
                                               body.end_time, source)
            ref_dir.mkdir(parents=True, exist_ok=True)
            stored_source = ref_dir / source.name
            stored_normalized = ref_dir / normalized.name
            os.replace(source, stored_source)
            os.replace(normalized, stored_normalized)
            item = service.add_reference(
                stored_source, stored_normalized, metadata, body.reference_text, "game_reference",
                f"session_id={body.session_id}; {body.start_time:.3f}-{body.end_time:.3f}s",
            )
            return {"reference": item, "profile": service.profile(body.voice_profile_id)}
        except DubbingError as error:
            _raise_dubbing_error(error)
        except ValueError as error:
            raise HTTPException(400, {"code": "invalid_audio", "message": str(error)}) from error
        except RuntimeError as error:
            raise HTTPException(422, {"code": "audio_extraction_failed", "message": str(error)}) from error

    @router.post("/voice-references/{reference_id}/approve")
    def approve_reference(reference_id: str, body: ReferenceApprovalBody):
        if not body.approved:
            raise HTTPException(400, "A confirmação precisa ser verdadeira.")
        try:
            return service.approve_reference(reference_id)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/lines/{line_id}/takes", status_code=202)
    def create_take(line_id: str, body: TakeBody):
        try:
            return service.create_take(line_id, body.occurrence_id, body.voice_profile_id,
                                      body.instruct, body.speed, body.manual_override,
                                      body.generation_settings.model_dump(exclude_none=True))
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/batch", status_code=202)
    def enqueue_batch(body: BatchBody):
        try:
            return service.enqueue_batch(body.session_id, body.ignore_missing, body.mode,
                                         body.occurrence_ids, body.character_id, body.session_ids)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.get("/queue")
    def queue_state(session_id: str | None = None, batch_id: str | None = None):
        return {**service.queue_snapshot(session_id, batch_id), "paused": worker.is_paused()}

    @router.post("/batches/{batch_id}/publish")
    def publish_completed_batch(batch_id: str):
        try:
            result = service.publish_completed_batch(batch_id)
            if auto_publish_callback is None:
                service.set_take_publication_status(result["take_ids"], "publish_error",
                                                    "Sincronização automática com o jogo não configurada.")
                return {**result, "publication": {"status": "not_ready"}}
            publication = auto_publish_callback(service.project_id)
            if publication.get("status") != "queued":
                service.set_take_publication_status(result["take_ids"], "publish_error",
                    publication.get("message") or "Não foi possível enfileirar a sincronização com o jogo.")
            return {**result, "publication": publication}
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/queue/{action}")
    def queue_action(action: str, session_id: str | None = None, batch_id: str | None = None):
        if action == "pause":
            worker.pause()
            return {"paused": True}
        if action == "resume":
            worker.resume()
            return {"paused": False}
        if action == "retry-errors":
            return service.retry_errors(session_id, batch_id)
        if action == "cancel-pending":
            return service.cancel_pending(session_id, batch_id)
        raise HTTPException(404, "Ação da fila desconhecida.")

    @router.get("/takes/{take_id}")
    def take_detail(take_id: str):
        try:
            return service.take_detail(take_id)
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.post("/takes/{take_id}/review")
    def review_take(take_id: str, body: TakeReviewBody):
        try:
            result = service.review_take(take_id, body.action, body.note)
            if auto_publish_callback is not None and result.get("status") in {"approved", "rejected"}:
                try:
                    result["runtime_publish"] = auto_publish_callback(service.project_id)
                except Exception:
                    result["runtime_publish"] = {"status": "not_queued"}
            return result
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.get("/takes/{take_id}/audio")
    def take_audio(take_id: str):
        try:
            path = service.audio_file(take_id)
            return FileResponse(path, media_type="audio/wav", filename=f"{take_id}.wav",
                                content_disposition_type="inline")
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.get("/presets")
    def list_presets():
        return {"items": service.presets()}

    @router.post("/presets", status_code=201)
    def save_preset(body: PresetBody):
        try:
            return service.save_preset(body.model_dump())
        except DubbingError as error:
            _raise_dubbing_error(error)

    @router.get("/export")
    def export_script(session_id: str | None = None, include_discarded: bool = False,
                      format: str = Query(default="json", pattern=r"^(json|csv)$")):
        content = service.export_script(session_id, include_discarded, format)
        if format == "csv":
            return Response(content, media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": "attachment; filename=dubsync-roteiro.csv"})
        return JSONResponse(content, headers={"Content-Disposition": "attachment; filename=dubsync-roteiro.json"})

    @router.get("/runtime-manifest")
    def runtime_manifest():
        return service.runtime_manifest()

    @router.get("/provider")
    def provider_status():
        return docker.status()

    @router.post("/provider/{action}", status_code=202)
    def provider_action(action: str):
        try:
            if action == "start":
                return docker.start()
            if action == "stop":
                return docker.stop()
            if action == "restart":
                return docker.restart()
            raise HTTPException(404, "Ação do provider desconhecida.")
        except (RuntimeError, OmniVoiceError, OSError) as error:
            raise HTTPException(503, {"code": getattr(error, "code", "docker_error"),
                                      "message": str(error)}) from error

    return router

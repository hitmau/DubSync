from __future__ import annotations

import base64
import hashlib
import ipaddress
import importlib.util
import logging
import mimetypes
import os
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

import cv2
import mss
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.requests import Request

from ..capture.session_manager import (DATA_DIR, SESSION_DIR, VIDEO_LIBRARY_DIR, SessionManager,
                                      desktop_preview, file_preview)
from ..capture.video_plan import build_video_plan
from ..capture.file_source import FileVideoSource
from ..capture.youtube_source import YoutubeFrameSource
from ..database.store import DubSyncStore
from ..database.models import DubbingProject
from ..dubbing.api import create_router as create_dubbing_router
from ..dubbing.jobs import DubbingWorker
from ..dubbing.service import DubbingService
from ..integration.api import create_router as create_integration_router
from ..integration.service import IntegrationService
from ..detector.visual import Region, perceptual_hash
from ..events.fine_sweep import run_fine_sweep
from ..events.normalize import normalize_text
from ..events.replacements import MAX_REPLACEMENT_RULES, MAX_REPLACEMENT_TEXT_LENGTH, apply_text_replacements
from ..ocr.calibration import calibrate_video, test_connection
from ..ocr.engine import OcrEngine
from ..ocr.text_cleanup import strip_trailing_hud_artifacts
from .settings import load_settings, openai_api_key, public_settings, save_settings
from ..providers.docker import OmniVoiceDocker
from ..projects.context import current_project_id, reset_project_context, set_project_context
from ..projects.service import ProjectError, ProjectService
from ..projects.storage import ProjectStorage

APP_DIR = Path(__file__).resolve().parent
REVIEW_UI_DIR = APP_DIR.parent / "review_ui"
DATA_DIR.mkdir(parents=True, exist_ok=True)
store = DubSyncStore(DATA_DIR / "dubsync.sqlite3")
manager = SessionManager(store, settings_provider=load_settings)
dubbing_service = DubbingService(store, DATA_DIR)
dubbing_worker = DubbingWorker(dubbing_service)
dubbing_docker = OmniVoiceDocker(APP_DIR.parent, load_settings,
                                 project_data_root_provider=lambda: dubbing_service.project_data_root)
project_service = ProjectService(store, DATA_DIR)
project_storage = ProjectStorage(DATA_DIR)
integration_service = IntegrationService(store, DATA_DIR)
dubbing_worker.auto_publish_callback = integration_service.schedule_auto_publish


@asynccontextmanager
async def lifespan(_app: FastAPI):
    dubbing_service.initialize()
    integration_service.initialize()
    dubbing_worker.start()
    if load_settings()["dubbing"].get("auto_start_docker"):
        try:
            dubbing_docker.start()
        except Exception:
            logging.getLogger(__name__).exception("Não foi possível iniciar OmniVoice automaticamente.")
    try:
        yield
    finally:
        dubbing_worker.stop()


app = FastAPI(title="DubSync", version="0.2.0", lifespan=lifespan)
templates = Jinja2Templates(directory=str(REVIEW_UI_DIR / "templates"))
app.mount("/static", StaticFiles(directory=REVIEW_UI_DIR / "static"), name="static")
app.include_router(create_dubbing_router(dubbing_service, dubbing_worker, dubbing_docker,
                                         auto_publish_callback=integration_service.schedule_auto_publish))
app.include_router(create_integration_router(integration_service))


@app.middleware("http")
async def require_project_context(request: Request, call_next):
    path = request.url.path
    project_id = None
    if path.startswith("/api/"):
        if path.startswith("/api/projects") or path in {
            "/api/health", "/api/settings", "/api/settings/openai/test", "/api/local-picker",
        }:
            return await call_next(request)
        project_id = (request.headers.get("x-dubsync-project") or request.query_params.get("project_id") or "").strip()
        if not project_id:
            return JSONResponse({"detail": "Abra um projeto antes de usar este recurso."}, status_code=428)
    elif path.startswith("/assets/"):
        project_id = (request.query_params.get("project_id") or "").strip()
        if not project_id:
            return JSONResponse({"detail": "Abra um projeto antes de consultar assets."}, status_code=428)
    elif path.startswith("/projects/"):
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[1] not in {"new"}:
            project_id = parts[1]
    if project_id:
        with store.Session() as db:
            project = db.get(DubbingProject, project_id)
            if project is None:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "Projeto não encontrado."}, status_code=404)
                return RedirectResponse("/", status_code=303)
            if project.status == "archived" and path.startswith("/api/"):
                return JSONResponse({"detail": "Restaure este projeto antes de continuar."}, status_code=409)
        token = set_project_context(project_id)
        try:
            return await call_next(request)
        finally:
            reset_project_context(token)
    return await call_next(request)


class YoutubePrepare(BaseModel):
    url: str = Field(min_length=12, max_length=2048)
    start: float = Field(ge=0, le=172800)
    duration: float = Field(gt=0, le=14400)
    segment_duration_seconds: int = Field(default=60, ge=5, le=900)
    language: str = Field(default="en", min_length=2, max_length=16)


class YoutubeStart(BaseModel):
    screen_bbox: dict[str, int] | None = None
    regions: list[dict[str, Any]] = Field(default_factory=list)


class FileStart(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    start: float = Field(ge=0, le=172800)
    duration: float = Field(gt=0, le=14400)
    language: str = Field(default="en", min_length=2, max_length=16)
    regions: list[dict[str, Any]] = Field(default_factory=list)


class RoiCharacterInput(BaseModel):
    character_id: str = Field(min_length=1, max_length=36)
    name: str = Field(min_length=1, max_length=120)
    alias: str = Field(default="", max_length=120)
    color: str = Field(default="", max_length=7)
    notes: str = Field(default="", max_length=2000)


class RoiCharactersBody(BaseModel):
    characters: list[RoiCharacterInput] = Field(max_length=32)


class NextBlockBody(BaseModel):
    previous_session_id: str = Field(min_length=1, max_length=36)
    duration_seconds: float = Field(gt=0, le=14400)


class PlanNextVideoBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=36)
    path: str = Field(min_length=1, max_length=4096)
    block_duration_seconds: float = Field(gt=0, le=14400)


class NextVideoBody(BaseModel):
    previous_session_id: str = Field(min_length=1, max_length=36)


class EventChanges(BaseModel):
    corrected_text: str | None = Field(default=None, max_length=4000)
    start_time: float | None = Field(default=None, ge=0)
    end_time: float | None = Field(default=None, ge=0)
    bbox_json: list[int] | None = None
    character: str | None = Field(default=None, max_length=120)
    emotion: str | None = Field(default=None, max_length=120)


class ManualEventBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=36)
    text: str = Field(min_length=1, max_length=4000)
    start_time: float = Field(ge=0)
    end_time: float = Field(gt=0)
    character: str | None = Field(default=None, max_length=120)
    ocr_observation: dict[str, Any] | None = None


class MergeEvents(BaseModel):
    event_ids: list[str] = Field(min_length=2, max_length=12)
    corrected_text: str | None = Field(default=None, max_length=4000)


class BulkEventSelection(BaseModel):
    event_ids: list[str] = Field(min_length=1, max_length=5000)


class DialogueTextReplacement(BaseModel):
    find_text: str = Field(min_length=1, max_length=4000)
    replace_text: str = Field(default="", max_length=4000)


class ProjectTextReplacementRule(BaseModel):
    find_text: str = Field(default="", max_length=MAX_REPLACEMENT_TEXT_LENGTH)
    replace_text: str = Field(default="", max_length=MAX_REPLACEMENT_TEXT_LENGTH)


class ProjectTextReplacementsBody(BaseModel):
    rules: list[ProjectTextReplacementRule] = Field(default_factory=list, max_length=MAX_REPLACEMENT_RULES)


class SplitEvent(BaseModel):
    split_time: float = Field(ge=0)


class EventAtTime(BaseModel):
    at_time: float = Field(ge=0)


class SettingsBody(BaseModel):
    settings: dict[str, Any]


class ProjectCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    game_title: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=1, max_length=96)
    game_version: str | None = Field(default=None, max_length=80)
    platform: str = Field(default="", max_length=80)
    narrative_type: str = Field(default="unknown", max_length=24)
    original_language: str = Field(default="en", min_length=2, max_length=16)
    capture_language: str = Field(default="en", min_length=2, max_length=16)
    dub_language: str = Field(default="pt-BR", min_length=2, max_length=16)
    notes: str = Field(default="", max_length=8000)
    cover_data: str | None = Field(default=None, max_length=12_000_000)


class ProjectUpdateBody(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    game_title: str | None = Field(default=None, max_length=160)
    game_version: str | None = Field(default=None, max_length=80)
    platform: str | None = Field(default=None, max_length=80)
    narrative_type: str | None = Field(default=None, max_length=24)
    original_language: str | None = Field(default=None, max_length=16)
    capture_language: str | None = Field(default=None, max_length=16)
    dub_language: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=8000)
    cover_data: str | None = Field(default=None, max_length=12_000_000)


class ProjectStatusBody(BaseModel):
    status: str = Field(pattern=r"^(active|paused|completed|archived)$")


class CalibrationBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=36)
    consent_to_send_frames: bool = False
    start_time: float | None = Field(default=None, ge=0)
    duration_seconds: int | None = Field(default=None, ge=3, le=30)
    sample_count: int | None = Field(default=None, ge=3, le=10)


class CalibrationApplyBody(BaseModel):
    session_id: str = Field(min_length=1, max_length=36)
    settings: dict[str, Any]
    region: dict[str, Any] | None = None


class TimelineMarkerBody(BaseModel):
    source_time: float = Field(ge=0)
    kind: str = Field(default="audio_start", pattern="^audio_start$")
    label: str = Field(default="Início de áudio (futuro)", min_length=1, max_length=160)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "projects.html", {})


@app.get("/projects/{project_id}")
def project_root(project_id: str):
    return RedirectResponse(f"/projects/{project_id}/capture", status_code=307)


@app.get("/projects/{project_id}/{area}", response_class=HTMLResponse)
def project_workspace(request: Request, project_id: str, area: str):
    if area not in {"capture", "review", "dubbing", "settings", "integration"}:
        return RedirectResponse("/", status_code=303)
    project = project_service.get_project(project_id)
    if not project:
        return RedirectResponse("/", status_code=303)
    if project["status"] == "archived":
        return RedirectResponse("/", status_code=303)
    project = project_service.open_project(project_id)
    if area == "integration":
        projects = [item for item in project_service.list_projects(status="all", sort="name")
                    if item["status"] != "archived"]
        return templates.TemplateResponse(request, "integration.html", {"project": project, "projects": projects})
    return templates.TemplateResponse(request, "index.html", {"project": project, "initial_area": area})


@app.get("/api/projects")
def list_projects(status: str = Query(default="all", pattern=r"^(all|active|paused|completed|archived)$"),
                  q: str = Query(default="", max_length=120),
                  sort: str = Query(default="activity", pattern=r"^(activity|name|created|progress)$")):
    return {"projects": project_service.list_projects(status, q, sort)}


@app.post("/api/projects", status_code=201)
def create_project(body: ProjectCreateBody):
    try:
        return project_service.create_project(body.model_dump())
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        if "UNIQUE constraint failed: projects.slug" in str(exc):
            raise HTTPException(409, "Este slug já está em uso. Escolha outro.") from exc
        raise


@app.get("/api/projects/{project_id}/summary")
def get_project_summary(project_id: str):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projeto não encontrado.")
    return project["summary"]


@app.get("/api/projects/{project_id}/cover")
def get_project_cover(project_id: str):
    project = project_service.get_project(project_id)
    if not project or not project.get("cover_path"):
        raise HTTPException(404, "Este projeto ainda não tem capa.")
    try:
        path = project_storage.project_file(project["slug"], project["cover_path"])
    except ValueError as exc:
        raise HTTPException(400, "O caminho da capa é inválido.") from exc
    if not path.is_file():
        raise HTTPException(404, "O arquivo da capa não foi encontrado.")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projeto não encontrado.")
    return project


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdateBody):
    try:
        return project_service.update_project(project_id, body.model_dump(exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(404, "Projeto não encontrado.") from exc
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/open")
def open_project(project_id: str):
    try:
        project = project_service.open_project(project_id)
    except KeyError as exc:
        raise HTTPException(404, "Projeto não encontrado.") from exc
    except ProjectError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"project": project, "url": f"/projects/{project_id}/capture"}


@app.post("/api/projects/{project_id}/archive")
def archive_project(project_id: str):
    try:
        return project_service.set_status(project_id, "archived")
    except KeyError as exc:
        raise HTTPException(404, "Projeto não encontrado.") from exc
    except ProjectError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/projects/{project_id}/restore")
def restore_project(project_id: str):
    try:
        return project_service.set_status(project_id, "active")
    except KeyError as exc:
        raise HTTPException(404, "Projeto não encontrado.") from exc


@app.post("/api/projects/{project_id}/status")
def update_project_status(project_id: str, body: ProjectStatusBody):
    try:
        return project_service.set_status(project_id, body.status)
    except KeyError as exc:
        raise HTTPException(404, "Projeto não encontrado.") from exc
    except ProjectError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(APP_DIR.parent / "launcher" / "dubsync.ico", media_type="image/vnd.microsoft.icon")


@app.get("/api/health")
def health():
    import shutil
    return {"ok": True, "app": "DubSync", "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
            "node": shutil.which("node"),
            "downloader": importlib.util.find_spec("yt_dlp") is not None,
            "capture_fps_target": 30, "analysis_fps": 8,
            "persistent_file_pause": True}


@app.get("/api/settings")
def get_settings():
    return public_settings()


@app.put("/api/settings")
def update_settings(body: SettingsBody):
    return public_settings(save_settings(body.settings))


@app.post("/api/settings/openai/test")
def check_openai():
    key = openai_api_key()
    if not key:
        raise HTTPException(400, "OPENAI_API_KEY não configurada no arquivo local do DubSync.")
    try:
        return test_connection(key, load_settings()["calibration"]["model"])
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/settings/calibrate")
def calibrate_settings(body: CalibrationBody):
    if not body.consent_to_send_frames:
        raise HTTPException(400, "Confirme na interface o envio das imagens desta amostra para a OpenAI.")
    session = store.session(body.session_id)
    if not session:
        raise HTTPException(404, "Sessão não encontrada.")
    if session["state"] in {"queued", "downloading", "running", "processing", "paused", "stopping"}:
        raise HTTPException(409, "Pause ou encerre a análise antes de calibrar as configurações.")
    key = openai_api_key()
    if not key:
        raise HTTPException(400, "OPENAI_API_KEY não configurada no arquivo local do DubSync.")
    config = session.get("config_json") or {}
    video_path = config.get("downloaded_path") or config.get("path")
    if not video_path or not Path(video_path).is_file():
        raise HTTPException(400, "O arquivo de vídeo da sessão não está disponível.")
    start = float(body.start_time if body.start_time is not None else session["source_start"])
    duration = int(body.duration_seconds or load_settings()["calibration"]["duration_seconds"])
    count = int(body.sample_count or load_settings()["calibration"]["sample_count"])
    range_start = float(session["source_start"])
    range_end = range_start + float(session["requested_duration"])
    if start < range_start or start >= range_end:
        raise HTTPException(400, "O ponto de calibração precisa estar dentro do trecho desta sessão.")
    duration = min(duration, int(range_end - start))
    if duration < 3:
        raise HTTPException(400, "Restam menos de três segundos no trecho para calibrar.")
    settings = load_settings()
    try:
        return calibrate_video(Path(video_path), start, duration, count,
                               settings["calibration"]["model"], session["language"],
                               config.get("regions", []), settings, key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/settings/calibration/apply")
def apply_calibration(body: CalibrationApplyBody):
    session = store.session(body.session_id)
    if not session:
        raise HTTPException(404, "Sessão não encontrada.")
    if session["state"] in {"queued", "downloading", "running", "processing", "paused", "stopping"}:
        raise HTTPException(409, "Aguarde o fim da análise antes de aplicar a calibração.")
    config = dict(session.get("config_json") or {})
    regions = list(config.get("regions", []))
    if body.region:
        try:
            from ..capture.session_manager import validate_regions
            suggestion = validate_regions([body.region])[0]
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if regions:
            regions[0] = suggestion
        else:
            regions.append(suggestion)
        config["regions"] = regions
    proposed = dict(body.settings)
    proposed.setdefault("review", load_settings()["review"])
    clean = save_settings(proposed)
    if body.region:
        store.update_session_config(body.session_id, config)
    return {"settings": public_settings(clean),
            "regions": (store.session(body.session_id) or {}).get("config_json", {}).get("regions", [])}


@app.get("/api/desktop-preview")
def get_desktop_preview():
    try:
        return desktop_preview()
    except Exception as exc:
        raise HTTPException(500, f"Não foi possível ler a tela: {exc}") from exc


@app.get("/api/file-preview")
def get_file_preview(path: str = Query(min_length=1), start: float = Query(default=0, ge=0)):
    try:
        return file_preview(path, start)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"Falha ao carregar a prévia: {exc}") from exc


@app.post("/api/youtube/prepare")
def prepare_youtube(body: YoutubePrepare):
    try:
        return manager.prepare_youtube(body.url, body.start, body.duration, body.language,
                                       body.segment_duration_seconds)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.get("/api/youtube/coverage")
def youtube_coverage(url: str = Query(min_length=12, max_length=2048)):
    try:
        return manager.youtube_coverage(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/youtube/{session_id}/start")
def start_youtube(session_id: str, body: YoutubeStart):
    try:
        return manager.start_youtube(session_id, body.screen_bbox, body.regions)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada.") from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/youtube/{session_id}/pause")
def pause_youtube(session_id: str):
    try:
        return manager.pause(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada ou encerrada.") from exc


@app.post("/api/youtube/{session_id}/resume")
def resume_youtube(session_id: str):
    try:
        return manager.resume(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada ou encerrada.") from exc


@app.post("/api/youtube/{session_id}/stop")
def stop_youtube(session_id: str):
    try:
        return manager.stop(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada ou encerrada.") from exc


@app.post("/api/file/start")
def start_file(body: FileStart):
    try:
        return manager.start_file(body.path, body.start, body.duration, body.language, body.regions)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/file/{session_id}/resume")
def resume_file_session(session_id: str):
    try:
        return manager.resume_file(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/file/{session_id}/pause")
def pause_file_session(session_id: str):
    try:
        return manager.pause_file(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/file/{session_id}/restart")
def restart_file_session(session_id: str):
    try:
        return manager.restart_file_block(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/file/next-block")
def next_file_block(body: NextBlockBody):
    try:
        return manager.next_file_block(body.previous_session_id, body.duration_seconds)
    except KeyError as exc:
        raise HTTPException(404, "Sessão anterior não encontrada.") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/file/plan-next-video")
def plan_next_video(body: PlanNextVideoBody):
    try:
        return manager.plan_next_video(body.session_id, body.path, body.block_duration_seconds)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada neste projeto.") from exc
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/file/next-video")
def next_file_video(body: NextVideoBody):
    try:
        return manager.next_file_video(body.previous_session_id)
    except KeyError as exc:
        raise HTTPException(404, "Sessão anterior não encontrada neste projeto.") from exc
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/roi-characters")
def get_roi_characters(roi_id: str = Query(min_length=1, max_length=96)):
    characters = store.roi_characters(roi_id)
    return {"characters": [roi_character_payload(item) for item in characters]}


@app.get("/api/roi-characters/colors")
def get_roi_character_colors():
    return {"characters": store.roi_character_colors()}


def roi_character_payload(item: dict) -> dict:
    fields = ("character_id", "roi_id", "name", "alias", "color", "notes", "sample_mp4_name", "sample_mp4_bytes",
              "sample_mp3_name", "sample_mp3_bytes", "photo_name", "photo_bytes")
    result = {key: item.get(key) for key in fields}
    updated = item.get("updated_at")
    version = str(int(updated.timestamp() * 1000)) if hasattr(updated, "timestamp") else ""
    result["photo_url"] = (f"/api/roi-characters/{item['character_id']}/photo?v={version}&project_id={item.get('project_id') or current_project_id()}"
                           if item.get("photo_name") else None)
    return result


def _project_character_asset_dir(character_id: str, folder: str) -> Path:
    item = store.roi_character(character_id)
    if not item:
        raise HTTPException(404, "Personagem não encontrado neste projeto.")
    with store.Session() as db:
        project = db.get(DubbingProject, item.get("project_id"))
    if not project:
        raise HTTPException(404, "O projeto deste personagem não foi encontrado.")
    directory = project_storage.project_file(project.slug, f"characters/{character_id}/{folder}")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@app.post("/api/roi-characters/{roi_id}/sync-from-events")
def sync_roi_characters_from_events(roi_id: str):
    try:
        result = store.sync_roi_characters_from_events(roi_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"created": result["created"],
            "characters": [roi_character_payload(item) for item in result["characters"]]}


@app.put("/api/roi-characters/{roi_id}")
def save_roi_characters(roi_id: str, body: RoiCharactersBody):
    values = [item.model_dump() for item in body.characters]
    if len({item["character_id"] for item in values}) != len(values):
        raise HTTPException(400, "Cada personagem precisa ter um identificador único.")
    if any(not item["name"].strip() for item in values):
        raise HTTPException(400, "Informe o nome de cada personagem.")
    internal_names = [normalize_text(item["alias"].strip() or item["name"]) for item in values]
    if len(set(internal_names)) != len(internal_names):
        raise HTTPException(400, "Os aliases internos (ou nomes usados sem alias) devem ser únicos nesta ROI.")
    try:
        characters = store.save_roi_characters(roi_id, values)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"characters": [roi_character_payload(item) for item in characters]}


@app.get("/api/roi-characters/{character_id}/photo")
def get_roi_character_photo(character_id: str):
    item = store.roi_character(character_id)
    if not item or not item.get("photo_path"):
        raise HTTPException(404, "Este personagem ainda não tem foto.")
    path = Path(item["photo_path"])
    if not path.is_file():
        raise HTTPException(404, "O arquivo da foto do personagem não foi encontrado.")
    return FileResponse(path, media_type=item.get("photo_mime") or "application/octet-stream",
                        headers={"Cache-Control": "private, max-age=3600"})


@app.put("/api/roi-characters/{character_id}/photo")
async def upload_roi_character_photo(character_id: str, request: Request,
                                     x_file_name: str = Header(default="portrait.jpg"),
                                     content_length: int | None = Header(default=None)):
    if not store.roi_character(character_id):
        raise HTTPException(404, "Personagem não encontrado.")
    max_size = 10 * 1024 * 1024
    if content_length is not None and content_length > max_size:
        raise HTTPException(413, "A foto excede o limite de 10 MiB.")
    original_name = Path(unquote(x_file_name).replace("\\", "/")).name
    safe_name = "".join(char if char.isalnum() or char in " ._-()" else "_" for char in original_name).strip(" .")
    suffix = Path(safe_name).suffix.lower()
    mime_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
    if suffix not in mime_types:
        raise HTTPException(400, "Anexe uma imagem JPG, PNG ou WebP.")
    directory = _project_character_asset_dir(character_id, "portraits")
    destination = directory / f"{uuid.uuid4()}{suffix}"
    size = 0
    try:
        with destination.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(413, "A foto excede o limite de 10 MiB.")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "O arquivo enviado está vazio.")
        image = cv2.imread(str(destination))
        if image is None or image.size == 0:
            raise HTTPException(400, "O arquivo enviado não contém uma imagem JPG, PNG ou WebP válida.")
        if image.shape[0] * image.shape[1] > 40_000_000:
            raise HTTPException(400, "A foto precisa ter no máximo 40 megapixels.")
        item = store.update_character_photo(character_id, str(destination), safe_name, mime_types[suffix], size)
        return roi_character_payload(item)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


@app.put("/api/roi-characters/{character_id}/mp4")
async def upload_character_mp4(character_id: str, request: Request,
                               x_file_name: str = Header(default="sample.mp4"),
                               content_length: int | None = Header(default=None)):
    if not store.roi_character(character_id):
        raise HTTPException(404, "Personagem não encontrado.")
    if content_length is not None and content_length > 2 * 1024 * 1024 * 1024:
        raise HTTPException(413, "O MP4 excede o limite de 2 GiB.")
    original_name = Path(unquote(x_file_name).replace("\\", "/")).name
    safe_name = "".join(char if char.isalnum() or char in " ._-()" else "_" for char in original_name).strip(" .")
    if not safe_name.lower().endswith(".mp4"):
        raise HTTPException(400, "Anexe um arquivo MP4.")
    directory = _project_character_asset_dir(character_id, "video-samples")
    destination = directory / f"{uuid.uuid4()}.mp4"
    size = 0
    try:
        with destination.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 2 * 1024 * 1024 * 1024:
                    raise HTTPException(413, "O MP4 excede o limite de 2 GiB.")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "O arquivo enviado está vazio.")
        item = store.update_character_sample(character_id, str(destination), safe_name, size)
        return {"character_id": character_id, "sample_mp4_name": item["sample_mp4_name"],
                "sample_mp4_bytes": item["sample_mp4_bytes"]}
    except Exception:
        destination.unlink(missing_ok=True)
        raise


@app.put("/api/roi-characters/{character_id}/mp3", include_in_schema=False)
@app.put("/api/roi-characters/{character_id}/audio-reference")
async def upload_character_voice_reference(character_id: str, request: Request,
                                            x_file_name: str = Header(default="voice-reference.mp3"),
                                            content_length: int | None = Header(default=None)):
    if not store.roi_character(character_id):
        raise HTTPException(404, "Personagem não encontrado.")
    max_size = 2 * 1024 * 1024 * 1024
    if content_length is not None and content_length > max_size:
        raise HTTPException(413, "O áudio excede o limite de 2 GiB.")
    original_name = Path(unquote(x_file_name).replace("\\", "/")).name
    safe_name = "".join(char if char.isalnum() or char in " ._-()" else "_" for char in original_name).strip(" .")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".mp3", ".wav"}:
        raise HTTPException(400, "Anexe um arquivo MP3 ou WAV.")
    previous = store.roi_character(character_id) or {}
    previous_audio_path = Path(previous["sample_mp3_path"]).resolve() if previous.get("sample_mp3_path") else None
    directory = _project_character_asset_dir(character_id, "voice-references")
    destination = directory / f"{uuid.uuid4()}{suffix}"
    size = 0
    try:
        with destination.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_size:
                    raise HTTPException(413, "O áudio excede o limite de 2 GiB.")
                output.write(chunk)
        if size == 0:
            raise HTTPException(400, "O arquivo enviado está vazio.")
        item = store.update_character_mp3(character_id, str(destination), safe_name, size)
        if previous_audio_path and previous_audio_path != destination.resolve():
            try:
                if previous_audio_path.is_relative_to(directory.resolve()):
                    previous_audio_path.unlink(missing_ok=True)
            except OSError:
                # The database already points at the new recording; a locked old
                # file must not make the successful replacement appear to fail.
                pass
        return {"character_id": character_id, "sample_mp3_name": item["sample_mp3_name"],
                "sample_mp3_bytes": item["sample_mp3_bytes"],
                "sample_audio_name": item["sample_mp3_name"],
                "sample_audio_bytes": item["sample_mp3_bytes"]}
    except Exception:
        destination.unlink(missing_ok=True)
        raise


@app.get("/api/sessions")
def list_sessions():
    result = []
    for session in store.sessions():
        active = manager.status(session["session_id"])
        result.append(active or session)
    return {"sessions": result}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str):
    # Completed sessions are no longer held by the live capture manager.
    # Fall back to the project-scoped database so editing actions can reopen them.
    value = manager.status(session_id) or store.session(session_id)
    if not value:
        raise HTTPException(404, "Sessão não encontrada.")
    return value


@app.delete("/api/sessions/{session_id}/block")
def delete_capture_block(session_id: str):
    if not load_settings()["review"].get("permanent_discard", False):
        raise HTTPException(409, "Ative a exclusão definitiva em Configurações antes de apagar este bloco.")
    try:
        result = store.delete_capture_session(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Bloco não encontrado neste projeto.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result["deleted_occurrences"]:
        result["game_sync"] = integration_service.schedule_auto_publish(current_project_id())
    return result


@app.get("/api/sessions/{session_id}/fine-sweep")
def get_fine_sweep(session_id: str):
    if not store.session(session_id):
        raise HTTPException(404, "Sessão não encontrada.")
    return {"run": store.latest_fine_sweep(session_id)}


@app.post("/api/sessions/{session_id}/fine-sweep")
def start_fine_sweep(session_id: str):
    session = store.session(session_id)
    if not session:
        raise HTTPException(404, "Sessão não encontrada.")
    if session["state"] != "complete":
        raise HTTPException(409, "Conclua a análise do bloco antes do pente fino.")
    config = session.get("config_json") or {}
    if not (config.get("downloaded_path") or config.get("path")):
        raise HTTPException(400, "O vídeo local deste bloco não está disponível.")
    try:
        run = store.create_fine_sweep(session_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    worker = threading.Thread(target=run_fine_sweep,
                              args=(store, run["run_id"], session, load_settings()),
                              name=f"fine-sweep-{session_id[:8]}", daemon=True)
    worker.start()
    return {"run": run}


@app.get("/api/sessions/{session_id}/video-plan")
def get_video_plan(session_id: str, character_id: str | None = Query(default=None, max_length=48)):
    current = store.session(session_id)
    if not current:
        raise HTTPException(404, "Sessão não encontrada.")
    plan = build_video_plan(store.sessions(limit=100_000), session_id)
    planned_sessions = []
    for row in plan["sessions"]:
        live = manager.status(row["session_id"]) if row["state"] in {
            "queued", "downloading", "preparing", "running", "processing", "paused", "stopping"
        } else row
        config = live.get("config_json") or {}
        planned_sessions.append({
            "session_id": live["session_id"],
            "state": live["state"],
            "source_start": float(live["source_start"]),
            "requested_duration": float(live["requested_duration"]),
            "created_at": live.get("created_at"),
            "current_source_time": live.get("current_source_time"),
            "project_start": row["project_start"],
            "sector_index": row["sector_index"],
            "progress": live.get("progress") or live.get("stats") or {},
            "config_json": {key: config.get(key) for key in (
                "block_index", "previous_session_id", "block_duration_requested", "duration",
                "video_duration", "video_id", "url", "path", "downloaded_path", "start"
            ) if config.get(key) is not None},
        })
    dubbing_progress = dubbing_service.block_dubbing_progress(
        [item["session_id"] for item in planned_sessions], character_id,
    )
    for item in planned_sessions:
        item["dubbing_progress"] = dubbing_progress.get(item["session_id"], {})
    return {"video_duration": plan["video_duration"], "sessions": planned_sessions,
            "sectors": plan["sectors"]}


@app.get("/api/sessions/{session_id}/markers")
def list_timeline_markers(session_id: str):
    if not store.session(session_id):
        raise HTTPException(404, "Sessão não encontrada.")
    return {"markers": store.markers(session_id)}


@app.post("/api/sessions/{session_id}/markers")
def create_timeline_marker(session_id: str, body: TimelineMarkerBody):
    session = store.session(session_id)
    if not session:
        raise HTTPException(404, "Sessão não encontrada.")
    start = float(session["source_start"])
    end = start + float(session["requested_duration"])
    if not start <= body.source_time <= end:
        raise HTTPException(400, "O marcador precisa ficar dentro do trecho selecionado.")
    marker = store.add_marker(session_id, body.source_time, body.kind, body.label)
    return {"marker": marker}


@app.delete("/api/markers/{marker_id}")
def remove_timeline_marker(marker_id: str):
    if not store.delete_marker(marker_id):
        raise HTTPException(404, "Marcador não encontrado.")
    return {"deleted": True}


@app.get("/api/sessions/{session_id}/preview")
def get_session_preview(session_id: str):
    session = store.session(session_id)
    if not session:
        raise HTTPException(404, "Sessão não encontrada.")
    image = manager.preview_frame(session_id)
    if image is None:
        return {"image": None}
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise HTTPException(500, "Não foi possível gerar a prévia atual.")
    status = manager.status(session_id) or {}
    return {"image": "data:image/jpeg;base64," + base64.b64encode(buffer.tobytes()).decode("ascii"),
            "width": image.shape[1], "height": image.shape[0],
            "source_time": status.get("current_source_time"),
            "timing_quality": status.get("timing_quality"),
            "timing_error_ms": status.get("timing_error_ms")}


@app.get("/api/sessions/{session_id}/video")
def get_session_video(session_id: str, range_header: str | None = Header(default=None, alias="Range")):
    session = store.session(session_id)
    if not session:
        raise HTTPException(404, "Sessão não encontrada.")
    config = session.get("config_json") or {}
    video_path = config.get("downloaded_path") or config.get("path")
    if not video_path or not Path(video_path).is_file():
        raise HTTPException(404, "O vídeo local não está disponível nesta sessão.")
    path = Path(video_path)
    size = path.stat().st_size
    start, end, status = 0, max(0, size - 1), 200
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if range_header:
        if not range_header.startswith("bytes=") or "," in range_header:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        value = range_header[6:].strip()
        try:
            first, last = value.split("-", 1)
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            else:
                suffix_length = int(last)
                start = max(0, size - suffix_length)
                end = size - 1
        except (ValueError, TypeError):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if start < 0 or start >= size or end < start:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, size - 1)
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    length = end - start + 1
    headers["Content-Length"] = str(length)
    headers["Content-Type"] = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def stream_file():
        with path.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(stream_file(), status_code=status, headers=headers)


@app.post("/api/sessions/{session_id}/organize-video")
def organize_session_video(session_id: str):
    try:
        return manager.organize_video(session_id)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada.") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


class LocalPickerBody(BaseModel):
    kind: Literal["executable", "directory", "video"]
    initial_path: str = Field(default="", max_length=1000)


_local_picker_lock = threading.Lock()


@app.post("/api/local-picker")
def select_local_path(request: Request, body: LocalPickerBody):
    """Open a host-local Windows picker for paths the browser cannot disclose."""
    client_host = request.client.host if request.client else ""
    try:
        local_client = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        local_client = client_host == "localhost"
    if not local_client:
        raise HTTPException(403, "O seletor de arquivos só pode ser aberto no computador do DubSync.")
    if os.name != "nt":
        raise HTTPException(501, "A seleção local está disponível no Windows.")
    if not _local_picker_lock.acquire(blocking=False):
        raise HTTPException(409, "Outro seletor de arquivos já está aberto.")
    try:
        import tkinter as tk
        from tkinter import filedialog

        initial_dir = Path.home()
        if body.initial_path.strip():
            candidate = Path(body.initial_path.strip()).expanduser()
            if candidate.is_dir():
                initial_dir = candidate
            elif candidate.is_file():
                initial_dir = candidate.parent
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        try:
            if body.kind == "directory":
                selected = filedialog.askdirectory(
                    parent=root, title="Localizar pasta do jogo",
                    initialdir=str(initial_dir), mustexist=True,
                )
            else:
                title = "Localizar executável do jogo" if body.kind == "executable" else "Localizar vídeo"
                patterns = [("Executáveis Windows", "*.exe"), ("Todos os arquivos", "*.*")] if body.kind == "executable" else [
                    ("Vídeos MP4 ou WebM", "*.mp4 *.webm"), ("Todos os arquivos", "*.*"),
                ]
                selected = filedialog.askopenfilename(
                    parent=root, title=title, initialdir=str(initial_dir), filetypes=patterns,
                )
        finally:
            root.destroy()
    except Exception as exc:
        raise HTTPException(500, f"Não foi possível abrir o seletor local: {exc}") from exc
    finally:
        _local_picker_lock.release()
    if not selected:
        return {"cancelled": True, "path": None}
    path = Path(selected).expanduser().resolve()
    if body.kind == "directory":
        if not path.is_dir():
            raise HTTPException(400, "A pasta selecionada não está mais disponível.")
    else:
        allowed = {".exe"} if body.kind == "executable" else {".mp4", ".webm"}
        if path.suffix.lower() not in allowed or not path.is_file():
            raise HTTPException(400, "O arquivo selecionado não corresponde ao tipo solicitado.")
    return {"cancelled": False, "path": str(path)}


@app.post("/api/video-library/select-file")
def select_video_file():
    if os.name != "nt":
        raise HTTPException(501, "A seleção de arquivo local está disponível no Windows.")
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        try:
            selected = filedialog.askopenfilename(
                parent=root,
                title="Abrir arquivo de vídeo",
                initialdir=str(VIDEO_LIBRARY_DIR if VIDEO_LIBRARY_DIR.is_dir() else Path.home()),
                filetypes=[("Vídeos MP4 ou WebM", "*.mp4 *.webm"), ("MP4", "*.mp4"), ("WebM", "*.webm")],
            )
        finally:
            root.destroy()
    except Exception as exc:
        raise HTTPException(500, f"Não foi possível abrir o seletor de vídeos: {exc}") from exc
    if not selected:
        return {"cancelled": True, "path": None}
    path = Path(selected).expanduser().resolve()
    if path.suffix.lower() not in {".mp4", ".webm"}:
        raise HTTPException(400, "Selecione um arquivo .mp4 ou .webm.")
    if not path.is_file():
        raise HTTPException(400, "O arquivo de vídeo selecionado não está mais disponível.")
    return {"cancelled": False, "path": str(path)}


@app.get("/api/events")
def list_events(session_id: str | None = None, status: str | None = None,
                min_confidence: float | None = Query(default=None, ge=0, le=1),
                character: str | None = Query(default=None, max_length=120),
                emotion: str | None = Query(default=None, max_length=120)):
    events = store.events(session_id, status, min_confidence, character, emotion)
    occurrence_ids = store.event_occurrence_ids([event["event_id"] for event in events])
    for event in events:
        event["occurrence_ids"] = occurrence_ids.get(event["event_id"], [])
    return {"events": events}


@app.get("/api/events/summary")
def event_status_summary(session_id: str | None = None):
    return store.event_status_summary(session_id)


@app.get("/api/events/filter-options")
def event_filter_options(session_id: str | None = None):
    return store.event_filter_options(session_id)


@app.post("/api/events/bulk-accept")
def bulk_accept_events(body: BulkEventSelection):
    try:
        return {"confirmed": store.bulk_accept(body.event_ids)}
    except KeyError as exc:
        raise HTTPException(404, "Um dos eventos selecionados não existe.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/project/text-replacements")
def get_project_text_replacements():
    return {"rules": store.project_text_replacements()}


@app.put("/api/project/text-replacements")
def put_project_text_replacements(body: ProjectTextReplacementsBody):
    try:
        rules = store.save_project_text_replacements(
            [rule.model_dump() for rule in body.rules]
        )
        return {"rules": rules}
    except KeyError as exc:
        raise HTTPException(404, "Projeto não encontrado.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/events/replace/preview")
def preview_dialogue_text_replacement(body: DialogueTextReplacement):
    try:
        return store.preview_project_text_replacement(body.find_text, body.replace_text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/events/replace")
def replace_dialogue_text(body: DialogueTextReplacement):
    try:
        return store.replace_project_dialogue_text(body.find_text, body.replace_text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/events/bulk-discard")
def bulk_discard_events(body: BulkEventSelection):
    try:
        return {"removed": store.bulk_discard(body.event_ids)}
    except KeyError as exc:
        raise HTTPException(404, "Um dos eventos selecionados não existe.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/events/bulk-delete")
def permanently_delete_events(body: BulkEventSelection):
    if not load_settings()["review"].get("permanent_discard", False):
        raise HTTPException(409, "Ative a exclusão definitiva em Configurações antes de apagar estes diálogos.")
    try:
        return store.permanently_delete_events(body.event_ids)
    except KeyError as exc:
        raise HTTPException(404, "Um dos diálogos selecionados não existe neste projeto.") from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/events/manual")
def create_manual_event(body: ManualEventBody):
    text_value = body.text.strip()
    if not text_value:
        raise HTTPException(400, "Informe o texto do diálogo.")
    ocr_evidence = body.ocr_observation or {}
    speaker_evidence = ocr_evidence.get("speaker") or {}
    character = str(body.character or "").strip() or str(speaker_evidence.get("candidate") or "").strip()
    if not character:
        raise HTTPException(400, "Informe o personagem ou use o OCR na área de nome configurada.")
    session = store.session(body.session_id)
    if not session:
        raise HTTPException(404, "Sessão não encontrada.")
    range_start = float(session["source_start"])
    requested_duration = float(session["requested_duration"])
    range_end = range_start + requested_duration if requested_duration > 0 else float("inf")
    if body.start_time < range_start or body.start_time >= range_end:
        raise HTTPException(400, "O início precisa estar dentro do intervalo desta sessão.")
    if body.end_time <= body.start_time or body.end_time > range_end:
        raise HTTPException(400, "O fim precisa ser posterior ao início e permanecer no intervalo da sessão.")

    assets: list[dict] = []
    frame_id = 0
    config = session.get("config_json") or {}
    video_path = config.get("downloaded_path") or config.get("path")
    if video_path and Path(video_path).is_file():
        try:
            packet = FileVideoSource(video_path, body.session_id, 0, 0,
                                     segment_id="manual").preview_at(body.start_time)
            image_dir = store.session_asset_dir(body.session_id) / "images"
            image_dir.mkdir(parents=True, exist_ok=True)
            assets.append(_write_original_asset(image_dir, f"manual_{uuid.uuid4().hex[:10]}",
                                                "start", packet, body.start_time))
            frame_id = packet.frame_id
        except (OSError, RuntimeError, ValueError) as exc:
            # Manual transcription stays usable when an optional context frame cannot be read.
            logging.getLogger(__name__).warning("Não foi possível guardar o frame do evento manual: %s", exc)
    try:
        return store.add_manual_event(body.session_id, text_value, body.start_time, body.end_time,
                                      character, assets, frame_id,
                                      ocr_observation=body.ocr_observation)
    except KeyError as exc:
        raise HTTPException(404, "Sessão não encontrada.") from exc


@app.post("/api/sessions/{session_id}/ocr-at-time")
def recognize_manual_dialogue_at_time(session_id: str, body: EventAtTime):
    session = store.session(session_id)
    if not session:
        raise HTTPException(404, "Sessão não encontrada neste projeto.")
    start = float(session["source_start"])
    duration = float(session["requested_duration"])
    end = start + duration if duration > 0 else float("inf")
    if body.at_time < start or body.at_time >= end:
        raise HTTPException(400, "O horário do OCR precisa estar dentro do bloco selecionado.")
    config = session.get("config_json") or {}
    configured_regions = config.get("regions") or []
    active_regions = []
    for index, item in enumerate(configured_regions):
        if not item.get("enabled", True):
            continue
        region = Region.from_dict(item, index, start)
        if region.active_at(body.at_time):
            active_regions.append((item, region))
    if not active_regions:
        raise HTTPException(400, "Não há ROI de legenda ativa neste horário. Ative ou ajuste uma ROI na Captura.")
    source = None
    try:
        source = _event_source(session, session_id)
        packet = source.frame_near(body.at_time)
        if packet is None:
            raise ValueError("Não foi possível ler o frame no horário do playhead.")
        settings = load_settings()
        ocr_engine = OcrEngine(session["language"], settings)
        candidates = []
        for item, region in active_regions:
            x0, y0, x1, y1 = region.bounds(packet.image, packet.source_time)
            if x1 <= x0 or y1 <= y0:
                continue
            lines = ocr_engine.recognize(packet.image[y0:y1, x0:x1])
            raw = " ".join(line.text for line in lines).strip()
            if not raw:
                continue
            processed = (strip_trailing_hud_artifacts(raw) if
                         settings.get("ocr", {}).get("strip_trailing_hud_artifacts") else raw)
            processed = apply_text_replacements(
                processed, store.project_text_replacements(session.get("project_id"))
            )
            confidence = sum(line.confidence for line in lines) / len(lines) if lines else 0.0
            useful_characters = sum(character.isalnum() for character in processed)
            candidates.append({
                "raw_text": raw,
                "processed_text": processed or raw,
                "confidence": confidence,
                "source_time": packet.source_time,
                "frame_id": packet.frame_id,
                "timing_quality": packet.timing_quality,
                "timing_error_ms": packet.timing_error_ms,
                "bbox": [x0, y0, x1 - x0, y1 - y0],
                "region_id": region.roi_id or item.get("roi_id"),
                "region_name": region.name,
                "useful_characters": useful_characters,
                "_region": region,
            })
        if not candidates:
            speaker_area = next(((item, region) for item, region in active_regions
                                 if region.speaker_enabled and region.speaker_region), None)
            if not speaker_area:
                raise ValueError("O OCR não encontrou texto nas ROIs ativas neste frame. Ajuste o início da fala e tente novamente.")
            item, region = speaker_area
            x0, y0, x1, y1 = region.bounds(packet.image, packet.source_time)
            if x1 <= x0 or y1 <= y0:
                raise ValueError("A ROI ativa está fora do frame neste horário.")
            candidates.append({
                "raw_text": "", "processed_text": "", "confidence": 0.0,
                "source_time": packet.source_time, "frame_id": packet.frame_id,
                "timing_quality": packet.timing_quality,
                "timing_error_ms": packet.timing_error_ms,
                "bbox": [x0, y0, x1 - x0, y1 - y0],
                "region_id": region.roi_id or item.get("roi_id"),
                "region_name": region.name, "useful_characters": 0,
                "_region": region,
            })
        best = max(candidates, key=lambda item: (item["useful_characters"], item["confidence"]))
        best.pop("useful_characters", None)
        region = best.pop("_region", None)
        speaker_result = {"text": "", "confidence": 0.0, "candidate": None,
                          "profile_id": None, "bbox": None,
                          "configured": bool(region and region.speaker_enabled and region.speaker_region)}
        if speaker_result["configured"]:
            speaker_bounds = region.speaker_bounds(packet.image, packet.source_time)
            if speaker_bounds:
                sx0, sy0, sx1, sy1 = speaker_bounds
                if sx1 > sx0 and sy1 > sy0:
                    speaker_crop = packet.image[sy0:sy1, sx0:sx1]
                    try:
                        speaker_lines = ocr_engine.recognize(speaker_crop)
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "Falha no OCR do personagem em diálogo manual: %s", type(exc).__name__)
                        speaker_lines = []
                    speaker_raw = " ".join(line.text for line in speaker_lines).strip()
                    speaker_confidence = (sum(line.confidence for line in speaker_lines) /
                                          len(speaker_lines)) if speaker_lines else 0.0
                    speaker_result.update({"text": speaker_raw,
                                           "confidence": speaker_confidence,
                                           "bbox": [sx0, sy0, sx1 - sx0, sy1 - sy0]})
                    region_id = best.get("region_id")
                    if speaker_raw and speaker_confidence >= .45:
                        from rapidfuzz.fuzz import ratio, token_set_ratio
                        normalized_speaker = normalize_text(speaker_raw)
                        profiles = store.roi_characters(region_id) if region_id else []
                        matched = next((item for item in profiles
                                        if max(ratio(normalized_speaker, normalize_text(item["name"])),
                                               token_set_ratio(normalized_speaker, normalize_text(item["name"])),
                                               ratio(normalized_speaker, normalize_text(item.get("alias") or "")),
                                               token_set_ratio(normalized_speaker, normalize_text(item.get("alias") or ""))) >= 82), None)
                        speaker_result["candidate"] = ((matched.get("alias") or matched["name"])
                                                        if matched else speaker_raw)[:120]
                        speaker_result["profile_id"] = matched.get("character_id") if matched else None
                        speaker_result["matched"] = bool(matched)

        best["speaker"] = speaker_result
        return best
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if source:
            source.close()


@app.get("/api/events/{event_id}")
def get_event(event_id: str):
    event = store.event(event_id)
    if not event:
        raise HTTPException(404, "Evento não encontrado.")
    return event


@app.delete("/api/events/{event_id}")
def permanently_delete_event(event_id: str):
    if not load_settings()["review"].get("permanent_discard", False):
        raise HTTPException(409, "Ative a exclusão definitiva em Configurações antes de apagar este diálogo.")
    try:
        return store.permanently_delete_event(event_id)
    except KeyError as exc:
        raise HTTPException(404, "Diálogo não encontrado.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/events/{event_id}/duplicate")
def duplicate_event(event_id: str, body: EventAtTime):
    event = store.event(event_id)
    if not event:
        raise HTTPException(404, "Diálogo não encontrado.")
    session = store.session(event["session_id"])
    if not session:
        raise HTTPException(404, "Sessão do diálogo não encontrada.")
    if session["state"] in {"running", "processing", "preparing", "paused"}:
        raise HTTPException(409, "Aguarde esta análise terminar antes de duplicar o diálogo.")
    start = float(body.at_time)
    low = float(session["source_start"])
    high = low + float(session["requested_duration"])
    if start < low or start >= high - 0.001:
        raise HTTPException(400, "Posicione o playhead dentro do bloco analisado.")
    duration = max(0.1, float((event["end_time"] or event["start_time"] + 2) - event["start_time"]))
    end = min(high, start + duration)
    try:
        return store.duplicate_event(event_id, start, end)
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/events/{event_id}/retry-ocr")
def retry_event_ocr(event_id: str, body: EventAtTime):
    event = store.event(event_id)
    if not event:
        raise HTTPException(404, "Diálogo não encontrado.")
    if event["end_time"] is not None and not event["start_time"] <= body.at_time <= event["end_time"]:
        raise HTTPException(400, "Escolha um frame dentro do diálogo selecionado.")
    session = store.session(event["session_id"])
    if not session:
        raise HTTPException(404, "Sessão do diálogo não encontrada.")
    source = None
    try:
        source = _event_source(session, event["session_id"])
        packet = source.frame_near(body.at_time)
        if packet is None:
            raise ValueError("Não foi possível ler o frame escolhido.")
        configured_regions = (session.get("config_json") or {}).get("regions") or []
        region_data = next((item for item in configured_regions
                            if item.get("roi_id") == event.get("region_id")), None)
        if region_data is None:
            region_data = next((item for item in configured_regions if item.get("enabled", True)), None)
        if region_data:
            region = Region.from_dict(region_data, 0, float(session["source_start"]))
            x0, y0, x1, y1 = region.bounds(packet.image, packet.source_time)
        else:
            x, y, width, height = [int(value) for value in event["bbox_json"]]
            x0, y0 = max(0, x), max(0, y)
            x1 = min(packet.image.shape[1], x + width)
            y1 = min(packet.image.shape[0], y + height)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("A caixa do diálogo não está dentro do frame.")
        settings = load_settings()
        ocr_engine = OcrEngine(session["language"], settings)
        lines = ocr_engine.recognize(packet.image[y0:y1, x0:x1])
        raw = " ".join(line.text for line in lines).strip()
        processed = (strip_trailing_hud_artifacts(raw) if
                     settings.get("ocr", {}).get("strip_trailing_hud_artifacts") else raw)
        processed = apply_text_replacements(
            processed, store.project_text_replacements(session.get("project_id"))
        )
        confidence = sum(line.confidence for line in lines) / len(lines) if lines else 0.0
        session_root = (store.session_asset_dir(event["session_id"])).resolve()
        image_dir = session_root / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        token = f"retry_{event_id[:8]}_{uuid.uuid4().hex[:10]}"
        crop_path = image_dir / f"{token}_ocr_retry_crop.png"
        if not cv2.imwrite(str(crop_path), packet.image[y0:y1, x0:x1],
                           [cv2.IMWRITE_PNG_COMPRESSION, 2]):
            raise RuntimeError("Falha ao salvar o recorte da nova leitura OCR.")
        image_assets = [{
            "kind": "ocr_retry_crop",
            "path": crop_path.relative_to(session_root).as_posix(),
            "source_time": packet.source_time,
            "frame_id": packet.frame_id,
            "timing_quality": packet.timing_quality,
            "timing_error_ms": packet.timing_error_ms,
            "image_role": "ocr_retry_crop",
        }, _write_original_asset(image_dir, token, "ocr_retry_frame", packet,
                                 event["start_time"])]
        speaker_result = {"text": "", "confidence": 0.0, "candidate": None,
                          "profile_id": None, "bbox": None, "configured": False}
        if region_data and region.speaker_enabled and region.speaker_region:
            speaker_result["configured"] = True
            speaker_bounds = region.speaker_bounds(packet.image, packet.source_time)
            if speaker_bounds:
                sx0, sy0, sx1, sy1 = speaker_bounds
                if sx1 > sx0 and sy1 > sy0:
                    speaker_crop = packet.image[sy0:sy1, sx0:sx1]
                    try:
                        speaker_lines = ocr_engine.recognize(speaker_crop)
                    except Exception as exc:
                        speaker_result["error"] = type(exc).__name__
                        speaker_lines = []
                    speaker_raw = " ".join(line.text for line in speaker_lines).strip()
                    speaker_confidence = (sum(line.confidence for line in speaker_lines) /
                                          len(speaker_lines)) if speaker_lines else 0.0
                    speaker_result.update({"text": speaker_raw,
                                           "confidence": speaker_confidence,
                                           "bbox": [sx0, sy0, sx1 - sx0, sy1 - sy0]})
                    speaker_path = image_dir / f"{token}_speaker_crop.png"
                    if not cv2.imwrite(str(speaker_path), speaker_crop,
                                       [cv2.IMWRITE_PNG_COMPRESSION, 2]):
                        raise RuntimeError("Falha ao salvar o recorte OCR do personagem.")
                    image_assets.append({"kind": "ocr_retry_speaker_crop",
                                         "path": speaker_path.relative_to(session_root).as_posix(),
                                         "source_time": packet.source_time,
                                         "frame_id": packet.frame_id,
                                         "timing_quality": packet.timing_quality,
                                         "timing_error_ms": packet.timing_error_ms,
                                         "image_role": "ocr_retry_speaker_crop"})
                    if speaker_raw and speaker_confidence >= .45 and region.roi_id:
                        from rapidfuzz.fuzz import ratio, token_set_ratio
                        normalized_speaker = normalize_text(speaker_raw)
                        profiles = store.roi_characters(region.roi_id)
                        matched = next((item for item in profiles
                                        if max(ratio(normalized_speaker, normalize_text(item["name"])),
                                               token_set_ratio(normalized_speaker, normalize_text(item["name"])),
                                               ratio(normalized_speaker, normalize_text(item.get("alias") or "")),
                                               token_set_ratio(normalized_speaker, normalize_text(item.get("alias") or ""))) >= 82), None)
                        speaker_result["candidate"] = ((matched.get("alias") or matched["name"])
                                                        if matched else speaker_raw)[:120]
                        speaker_result["profile_id"] = matched.get("character_id") if matched else None
                        speaker_result["matched"] = bool(matched)
        observation = {"source": "manual_retry", "source_time": packet.source_time,
                       "text": raw, "processed_text": processed, "confidence": confidence,
                       "frame_id": packet.frame_id, "timing_quality": packet.timing_quality,
                       "bbox": [x0, y0, x1 - x0, y1 - y0],
                       "image_assets": image_assets,
                       "speaker": speaker_result,
                       "lines": [{"text": line.text, "confidence": line.confidence,
                                  "bbox": line.bbox} for line in lines]}
        store.add_ocr_retry(event_id, observation)
        return {"original_text": raw, "processed_text": processed, "confidence": confidence,
                "source_time": packet.source_time, "frame_id": packet.frame_id,
                "image_assets": image_assets, "lines": observation["lines"],
                "speaker": speaker_result,
                "applied": False}
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if source:
            source.close()


@app.post("/api/events/{event_id}/review/{action}")
def review_event(event_id: str, action: str, body: EventChanges | None = None):
    if action not in {"accept", "discard", "correct", "update"}:
        raise HTTPException(404, "Ação de revisão não reconhecida.")
    try:
        changes = (body.model_dump(exclude_unset=True) if body else {})
        if action == "correct" and not changes.get("corrected_text"):
            raise ValueError("Informe o texto corrigido.")
        existing = store.event(event_id)
        if not existing:
            raise KeyError(event_id)
        start = changes.get("start_time", existing["start_time"])
        end = changes.get("end_time", existing["end_time"])
        if start is not None and end is not None and float(end) < float(start):
            raise ValueError("O fim precisa ser posterior ou igual ao início.")
        result = store.review(event_id, action, changes)
        previous_corrected_text = existing.get("corrected_text")
        current_corrected_text = result.get("corrected_text")
        capture_text_changed = (
            "corrected_text" in changes
            and current_corrected_text != previous_corrected_text
        )
        dubbing_service.sync_reviewed_event_character(
            event_id,
            existing.get("character_profile_id"),
            existing.get("character"),
            existing.get("region_id"),
            previous_event=existing,
            sync_character="character" in changes,
            sync_emotion="emotion" in changes,
            sync_text=capture_text_changed,
        )
        return result
    except KeyError as exc:
        raise HTTPException(404, "Evento não encontrado.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/events/merge")
def merge_events(body: MergeEvents):
    records = [store.event(event_id) for event_id in body.event_ids]
    if any(event is None for event in records):
        raise HTTPException(404, "Um dos eventos selecionados não existe.")
    if len({event["session_id"] for event in records}) != 1:
        raise HTTPException(400, "Selecione eventos da mesma sessão.")
    try:
        return store.merge(body.event_ids, body.corrected_text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/events/{event_id}/split")
def split_event(event_id: str, body: SplitEvent):
    event = store.event(event_id)
    if not event:
        raise HTTPException(404, "Evento não encontrado.")
    session = store.session(event["session_id"])
    if not session:
        raise HTTPException(404, "Sessão do evento não encontrada.")
    if event["end_time"] is None or not event["start_time"] < body.split_time < event["end_time"]:
        raise HTTPException(400, "O ponto de divisão precisa ficar entre início e fim do evento.")
    source = None
    try:
        source = _event_source(session, event["session_id"])
        nearby_times = [body.split_time, body.split_time + 0.25]
        nearby_times.extend(body.split_time - offset for offset in (1.0, 0.5, 0.25)
                            if body.split_time - offset >= 0)
        nearby_packets = (source.frames_near_window(nearby_times) if isinstance(source, FileVideoSource)
                          else {point: source.frame_near(point) for point in nearby_times})
        split_packet = nearby_packets.get(body.split_time)
        if split_packet is None:
            raise ValueError("Não há frame gravado próximo ao ponto de divisão.")
        image_dir = store.session_asset_dir(event["session_id"]) / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        token = f"split_{event_id[:8]}_{uuid.uuid4().hex[:8]}"
        first_assets = [item for item in (event.get("assets_json") or [])
                        if item.get("kind") not in {"end", "post_250"}
                        and float(item.get("source_time", body.split_time)) <= body.split_time]
        split_asset = _write_original_asset(image_dir, token, "end", split_packet, event["start_time"])
        first_assets.append(split_asset)
        first_post = nearby_packets.get(body.split_time + 0.25)
        if first_post and abs(first_post.source_time - (body.split_time + 0.25)) <= 0.11:
            first_assets.append(_write_original_asset(image_dir, token, "post_250", first_post, event["start_time"]))

        second_assets: list[dict] = []
        for kind, target_time in (("pre_1000", body.split_time - 1.0),
                                  ("pre_500", body.split_time - 0.5),
                                  ("pre_250", body.split_time - 0.25)):
            if target_time < 0:
                continue
            packet = nearby_packets.get(target_time)
            if packet and abs(packet.source_time - target_time) <= 0.11:
                second_assets.append(_write_original_asset(image_dir, token, kind, packet, body.split_time))
        second_assets.append({**split_asset, "kind": "start",
                              "offset_ms": round((split_packet.source_time - body.split_time) * 1000)})
        original_end = float(event["end_time"])
        midpoint = body.split_time + (original_end - body.split_time) / 2
        best_packet = source.frame_near(midpoint)
        if best_packet:
            second_assets.append(_write_original_asset(image_dir, token, "best", best_packet, body.split_time))
        end_asset = _reusable_original_asset(event, "end", original_end, body.split_time)
        if end_asset:
            second_assets.append(end_asset)
        else:
            end_packet = source.frame_near(original_end)
            if end_packet:
                second_assets.append(_write_original_asset(image_dir, token, "end", end_packet, body.split_time))
        post_asset = _reusable_original_asset(event, "post_250", original_end + 0.25, body.split_time)
        if post_asset:
            second_assets.append(post_asset)
        else:
            post_packet = source.frame_near(original_end + 0.25)
            if post_packet and abs(post_packet.source_time - (original_end + 0.25)) <= 0.11:
                second_assets.append(_write_original_asset(image_dir, token, "post_250", post_packet, body.split_time))

        x, y, width, height = [int(value) for value in (event.get("bbox_json") or [0, 0, 0, 0])]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(split_packet.image.shape[1], x + width), min(split_packet.image.shape[0], y + height)
        crop = split_packet.image[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else None
        crop_path = image_dir / f"{token}_dialogue_original.png"
        if crop is None or crop.size == 0:
            previous_crop = store.session_asset_dir(event["session_id"]) / (event.get("crop_path") or "")
            if not previous_crop.is_file():
                raise ValueError("Não foi possível criar o recorte original do segundo evento.")
            crop_path.write_bytes(previous_crop.read_bytes())
        elif not cv2.imwrite(str(crop_path), crop, [cv2.IMWRITE_PNG_COMPRESSION, 2]):
            raise RuntimeError("Não foi possível gravar o recorte da divisão.")
        relative_crop = crop_path.relative_to(store.session_asset_dir(event["session_id"])).as_posix()
        metadata = {"segment_id": split_packet.segment_id, "frame_id": split_packet.frame_id,
                    "timing_quality": split_packet.timing_quality, "timing_error_ms": split_packet.timing_error_ms,
                    "captured_at": split_packet.captured_at.isoformat(),
                    "text_phash": perceptual_hash(crop if crop is not None and crop.size else split_packet.image),
                    "context_phash": perceptual_hash(split_packet.image)}
        first, second = store.split(event_id, body.split_time, first_assets, second_assets,
                                   relative_crop, metadata)
        return {"first": first, "second": second}
    except KeyError as exc:
        raise HTTPException(404, "Evento não encontrado.") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if source:
            source.close()


@app.get("/api/events/{event_id}/split-preview")
def split_preview(event_id: str, split_time: float = Query(ge=0)):
    event = store.event(event_id)
    if not event:
        raise HTTPException(404, "Evento não encontrado.")
    if event["end_time"] is None or not event["start_time"] < split_time < event["end_time"]:
        raise HTTPException(400, "O ponto de divisão precisa ficar dentro do intervalo do evento.")
    session = store.session(event["session_id"])
    if not session:
        raise HTTPException(404, "Sessão do evento não encontrada.")
    source = None
    try:
        source = _event_source(session, event["session_id"])
        packet = source.frame_near(split_time)
        if not packet:
            raise ValueError("Não há frame gravado próximo a esse tempo source_time.")
        ok, buffer = cv2.imencode(".png", packet.image, [cv2.IMWRITE_PNG_COMPRESSION, 2])
        if not ok:
            raise RuntimeError("Não foi possível gerar a prévia do frame.")
        import base64
        return {"image": "data:image/png;base64," + base64.b64encode(buffer).decode("ascii"),
                "source_time": packet.source_time, "captured_at": packet.captured_at,
                "timing_quality": packet.timing_quality, "timing_error_ms": packet.timing_error_ms}
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if source:
            source.close()


def _event_source(session: dict, source_id: str):
    config = session["config_json"]
    if session["source_type"] == "file":
        return FileVideoSource(config["path"], source_id, 0, 0)
    if config.get("downloaded_path"):
        return FileVideoSource(config["downloaded_path"], source_id, 0, 0,
                               segment_id="youtube-download-0001")
    if not config.get("screen_bbox"):
        raise ValueError("A sessão ainda não tem uma área de captura registrada.")
    return YoutubeFrameSource(store.session_asset_dir(source_id), source_id, config["url"], config["start"],
                              config["duration"], config["screen_bbox"],
                              config.get("segment_duration_seconds", 60))


def _reusable_original_asset(event: dict, kind: str, target_time: float,
                             new_event_start: float) -> dict | None:
    session_root = (store.session_asset_dir(event["session_id"])).resolve()
    for asset in event.get("assets_json") or []:
        if asset.get("kind") != kind or asset.get("image_role") not in (None, "original_full_frame"):
            continue
        try:
            source_time = float(asset["source_time"])
            relative = str(asset["path"])
        except (KeyError, TypeError, ValueError):
            continue
        path = (session_root / relative).resolve()
        if abs(source_time - target_time) <= 0.11 and path.is_relative_to(session_root) and path.is_file():
            return {**asset, "offset_ms": round((source_time - new_event_start) * 1000)}
    return None


def _write_original_asset(image_dir: Path, prefix: str, kind: str, packet, event_start: float) -> dict:
    path = image_dir / f"{prefix}_{kind}_original.png"
    if not cv2.imwrite(str(path), packet.image, [cv2.IMWRITE_PNG_COMPRESSION, 2]):
        raise RuntimeError(f"Falha ao preservar o frame {kind}.")
    return {"kind": kind, "path": path.relative_to(image_dir.parent).as_posix(),
            "source_time": packet.source_time,
            "offset_ms": round((packet.source_time - event_start) * 1000),
            "captured_at": packet.captured_at.isoformat(), "segment_id": packet.segment_id,
            "frame_id": packet.frame_id, "timing_quality": packet.timing_quality,
            "timing_error_ms": packet.timing_error_ms, "image_role": "original_full_frame",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


@app.get("/assets/{session_id}/{asset_path:path}")
def get_asset(session_id: str, asset_path: str):
    if not store.session(session_id):
        raise HTTPException(404, "Imagem não encontrada.")
    session_root = (store.session_asset_dir(session_id)).resolve()
    target = (session_root / asset_path).resolve()
    if not target.is_relative_to(session_root) or not target.is_file():
        raise HTTPException(404, "Imagem não encontrada.")
    return FileResponse(target)

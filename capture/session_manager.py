from __future__ import annotations

import base64
import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import cv2
import mss
import mss.tools
import numpy as np

from ..database.store import DubSyncStore
from ..database.models import DubbingProject
from ..detector.visual import Region
from ..events.processor import DialogueProcessor
from ..ocr.engine import OcrEngine
from .file_source import FileVideoSource, probe_video_duration
from .video_plan import linked_video_chain, video_identity
from .youtube_download import download_best_video, validate_youtube_url
from .youtube_source import YoutubeFrameSource, video_canvas_snapshot, youtube_watch_url
from ..projects.context import current_project_id
from ..projects.storage import ProjectStorage


DATA_DIR = Path(__file__).resolve().parents[1] / ".data"
SESSION_DIR = DATA_DIR / "sessions"
VIDEO_LIBRARY_DIR = Path(__file__).resolve().parents[1] / "videos"


def _library_filename(title: str, video_id: str, suffix: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", title).strip(" ._")
    safe = re.sub(r"\s+", " ", safe)[:120].rstrip(" ._") or "video"
    return f"{safe} [{video_id}]{suffix}"


def validate_regions(items: list[dict] | None) -> list[dict]:
    def box(value: dict, label: str) -> dict:
        try:
            result = {key: float(value[key]) for key in ("x", "y", "width", "height")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{label}: informe posição e tamanho numéricos.") from exc
        x, y, width, height = result.values()
        if not all(math.isfinite(n) for n in result.values()) or min(x, y) < 0 or min(width, height) <= 0 or x + width > 1.000001 or y + height > 1.000001:
            raise ValueError(f"{label} precisa estar entre 0% e 100% do frame.")
        return result

    def moment(entry: dict, key: str) -> float:
        try:
            number = float(entry[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Cada posição ou janela da ROI precisa de um tempo válido.") from exc
        if not math.isfinite(number) or number < 0:
            raise ValueError("Os tempos das ROIs precisam ser positivos e finitos.")
        return number

    items = items or [{"x": 0.08, "y": 0.63, "width": 0.84, "height": 0.32, "name": "dialogue"}]
    if len(items) > 8:
        raise ValueError("Use no máximo oito áreas de diálogo por sessão.")
    result: list[dict] = []
    for index, item in enumerate(items):
        spec = box(item, f"Área de diálogo {index + 1}")
        lifetime = item.get("lifetime") or {}
        if len(lifetime.get("windows", [])) > 64:
            raise ValueError("Use no máximo 64 janelas por ROI.")
        windows = []
        for window in lifetime.get("windows", [])[:64]:
            mode = window.get("mode", "absolute")
            if mode not in {"absolute", "relative"}:
                raise ValueError("Modo de tempo da ROI inválido.")
            start, end = moment(window, "start"), moment(window, "end")
            if end <= start:
                raise ValueError("O fim de cada janela da ROI deve ser posterior ao início.")
            windows.append({"mode": mode, "start": start, "end": end})
        if lifetime.get("enabled") and not windows:
            raise ValueError("ROI com tempo de vida ativo precisa de pelo menos uma janela.")
        motion = item.get("motion") or {}
        if len(motion.get("keyframes", [])) > 64:
            raise ValueError("Use no máximo 64 posições por ROI.")
        keyframes = []
        for point in motion.get("keyframes", [])[:64]:
            mode = point.get("mode", "absolute")
            if mode not in {"absolute", "relative"}:
                raise ValueError("Modo de tempo do movimento inválido.")
            keyframes.append({"mode": mode, "time": moment(point, "time"),
                              **box(point, "Posição da ROI em movimento")})
        if motion.get("enabled") and not keyframes:
            raise ValueError("ROI em movimento precisa de pelo menos uma posição.")
        speaker_enabled = bool(item.get("speaker_enabled", False))
        speaker_region = box(item["speaker_region"], "Área do nome do personagem") if item.get("speaker_region") else None
        if speaker_enabled and not speaker_region:
            raise ValueError("Marque a área onde aparece o nome do personagem.")
        character_detection = item.get("character_detection") or {}
        color_rules = []
        if len(character_detection.get("color_rules", [])) > 32:
            raise ValueError("Use no máximo 32 regras de cor por ROI.")
        for rule in character_detection.get("color_rules", [])[:32]:
            name = str(rule.get("name", "")).strip()[:120]
            color = str(rule.get("color", "")).strip()
            if not name or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                raise ValueError("Cada regra de cor precisa de personagem e cor hexadecimal válidos.")
            try:
                tolerance = float(rule.get("tolerance", 100))
            except (TypeError, ValueError) as exc:
                raise ValueError("A tolerância de cor deve ser um número entre 0 e 441.") from exc
            if not math.isfinite(tolerance) or not 0 <= tolerance <= 441:
                raise ValueError("A tolerância de cor deve ficar entre 0 e 441.")
            color_rules.append({"name": name, "color": color.upper(), "tolerance": tolerance})
        character_detection = {
            "enabled": bool(character_detection.get("enabled", False)),
            "prefix_before_colon": bool(character_detection.get("prefix_before_colon", True)),
            "color_rules": color_rules,
        }
        result.append({**spec, "name": str(item.get("name", f"area-{index + 1}"))[:48],
                       "roi_id": str(item.get("roi_id") or uuid.uuid4())[:96],
                       "enabled": bool(item.get("enabled", True)),
                       "locked": bool(item.get("locked", False)),
                       "lifetime": {"enabled": bool(lifetime.get("enabled")), "windows": windows},
                       "motion": {"enabled": bool(motion.get("enabled")), "keyframes": keyframes},
                       "speaker_enabled": speaker_enabled, "speaker_region": speaker_region,
                       "character_detection": character_detection})
    return result


def validate_duration(duration: float, start: float) -> None:
    if not math.isfinite(duration) or duration <= 0 or duration > 14400:
        raise ValueError("Informe uma duração maior que zero e de até quatro horas por bloco.")
    if not math.isfinite(start) or start < 0:
        raise ValueError("O início precisa ser zero ou maior.")


class YoutubeJob:
    def __init__(self, store: DubSyncStore, session_id: str, source_dir: Path,
                 source_config: dict, language: str, on_complete=None) -> None:
        self.store = store
        self.session_id = session_id
        self.source_dir = source_dir
        self.source_config = source_config
        self.language = language
        self.on_complete = on_complete
        self.ready = threading.Event()
        self.start_requested = threading.Event()
        self.pause_requested = threading.Event()
        self.resume_requested = threading.Event()
        self.resume_requested.set()
        self.stop_requested = threading.Event()
        self.worker: threading.Thread | None = None
        self.error: str | None = None
        self.detail = "Abrindo o player do YouTube."
        self.stats: dict | None = None
        self.frame_bbox: dict[str, int] | None = None
        self.regions: list[dict] = []
        self.page = None
        self.latest_frame = None
        self.progress: dict = {}
        self.live_metrics = {"frames_captured": 0, "current_source_time": source_config["start"],
                             "current_segment": 0, "effective_fps": 0.0, "timing_quality": "—",
                             "timing_error_ms": 0.0, "current_file": "—"}
        self._metrics_lock = threading.Lock()
        self._first_monotonic_ns: int | None = None
        self._previous_capture_ns: int | None = None
        self._active_capture_seconds = 0.0

    def launch(self) -> None:
        self.worker = threading.Thread(target=self._run, name=f"DubSync-{self.session_id[:8]}", daemon=True)
        self.worker.start()

    def _run(self) -> None:
        context = None
        playwright = None
        source = None
        try:
            from playwright.sync_api import sync_playwright
            self.source_dir.mkdir(parents=True, exist_ok=True)
            profile = DATA_DIR / "youtube-profiles" / self.session_id
            profile.mkdir(parents=True, exist_ok=True)
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=False,
                args=["--window-size=1366,850", "--window-position=0,0", "--autoplay-policy=no-user-gesture-required"],
                viewport={"width": 1366, "height": 768},
            )
            page = context.pages[0] if context.pages else context.new_page()
            self.page = page
            page.goto(youtube_watch_url(self.source_config["url"], self.source_config["start"]),
                      wait_until="domcontentloaded", timeout=90_000)
            self.detail = "Aguardando o player carregar. Se necessário, entre na sua conta na janela do YouTube."
            page.locator("video").first.wait_for(timeout=120_000)
            page.wait_for_function("""() => {const v=document.querySelector('video');
                return v&&v.readyState>=2&&v.videoWidth>0}
            """, timeout=60_000)
            page.evaluate("""({start}) => { const v=document.querySelector('video'); v.muted=true; v.pause(); v.currentTime=start; }""",
                          {"start": self.source_config["start"]})
            page.wait_for_function("""(start) => {const v=document.querySelector('video');
                return v&&!v.seeking&&v.readyState>=2&&Math.abs(v.currentTime-start)<1}
            """, arg=self.source_config["start"], timeout=60_000)
            sample_data = None
            for _ in range(60):
                sample_data = video_canvas_snapshot(page, allow_paused=True)
                if sample_data and sample_data.get("error"):
                    raise RuntimeError(f"O player do YouTube falhou: {sample_data['error']}")
                if sample_data and sample_data.get("jpeg") and \
                        abs(float(sample_data["time"]) - self.source_config["start"]) < 1:
                    break
                page.evaluate("""(start) => {const v=document.querySelector('video');
                    if(v && Math.abs(v.currentTime-start)>0.15) {v.pause(); v.currentTime=start;}
                }""", self.source_config["start"])
                time.sleep(0.25)
            else:
                raise RuntimeError("O player não forneceu a prévia visual no tempo solicitado.")
            sample = np.frombuffer(base64.b64decode(sample_data["jpeg"].split(",", 1)[1]), np.uint8)
            self.latest_frame = cv2.imdecode(sample, cv2.IMREAD_COLOR)
            if self.latest_frame is None:
                raise RuntimeError("Não foi possível obter a prévia do vídeo no Chromium.")
            self.detail = "Player pronto no tempo solicitado. Ajuste o recorte do vídeo e as ROIs na prévia."
            self.ready.set()
            preparation_deadline = time.monotonic() + 600
            while not self.start_requested.wait(timeout=0.5) and time.monotonic() < preparation_deadline:
                # YouTube may resume playback after a scripted pause. Keep the calibration still at source_start.
                page.evaluate("""(start) => {const v=document.querySelector('video');
                    if(v && (Math.abs(v.currentTime-start)>0.15 || !v.paused)) {
                        v.pause(); v.currentTime=start;
                    }}""", self.source_config["start"])
            if not self.start_requested.is_set():
                self.store.set_session_state(self.session_id, "stopped")
                self.detail = "A preparação foi encerrada por falta de seleção da área do vídeo."
                return
            if self.stop_requested.is_set():
                self.store.set_session_state(self.session_id, "stopped")
                return
            source = YoutubeFrameSource(
                session_dir=self.source_dir, source_id=self.session_id, url=self.source_config["url"],
                start=self.source_config["start"], duration=self.source_config["duration"],
                screen_bbox=self.frame_bbox or {}, context=context, page=page,
                block_seconds=self.source_config["segment_duration_seconds"],
                pause_requested=self.pause_requested, resume_requested=self.resume_requested,
                stop_requested=self.stop_requested,
            )
            source.on_status = self._source_status
            source.on_capture = self._on_capture
            self.store.set_session_state(self.session_id, "running")
            self.detail = f"Capturando quadros silenciosos em blocos de {self.source_config['segment_duration_seconds']} segundos."
            processor = DialogueProcessor(self.store, self.session_id, self.source_dir,
                                          [Region.from_dict(item, index, self.source_config["start"]) for index, item in enumerate(self.regions)],
                                          self.language, OcrEngine(self.language, self.source_config.get("processing_settings", {})),
                                          self._update_progress, self.source_config.get("processing_settings", {}))
            self.stats = processor.run(source)
            state = "stopped" if self.stop_requested.is_set() else "complete"
            self.store.set_session_state(self.session_id, state)
            self.detail = "Captura e análise concluídas." if state == "complete" else "Captura encerrada pelo usuário."
            (self.source_dir / "result.json").write_text(json.dumps(self.stats, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            self.detail = "A sessão encontrou um erro. Consulte o status para ver o motivo."
            self.store.set_session_state(self.session_id, "failed", self.error)
        finally:
            self.ready.set()
            self.page = None
            if source:
                source.close()
            if context:
                context.close()
            if playwright:
                playwright.stop()
            if self.on_complete:
                self.on_complete()


class YoutubeDownloadJob:
    """Downloads one video-only source, then analyzes its local presentation timestamps."""

    def __init__(self, store: DubSyncStore, session_id: str, source_dir: Path,
                 source_config: dict, language: str, on_complete=None) -> None:
        self.store = store
        self.session_id = session_id
        self.source_dir = source_dir
        self.source_config = source_config
        self.language = language
        self.on_complete = on_complete
        self.ready = threading.Event()
        self.start_requested = threading.Event()
        self.pause_requested = threading.Event()
        self.resume_requested = threading.Event()
        self.resume_requested.set()
        self.stop_requested = threading.Event()
        self.worker: threading.Thread | None = None
        self.error: str | None = None
        self.detail = "Aguardando o download do vídeo."
        self.stats: dict | None = None
        self.frame_bbox = None
        self.regions: list[dict] = []
        self.page = None
        self.latest_frame = None
        self.progress: dict = {}
        self.live_metrics = {"frames_captured": 0, "current_source_time": source_config["start"],
                             "current_segment": 0, "effective_fps": 0.0,
                             "timing_quality": "exact_pts", "timing_error_ms": 1.0,
                             "current_file": "—"}
        self._metrics_lock = threading.Lock()

    def launch(self) -> None:
        self.worker = threading.Thread(target=self._run, name=f"DubSyncDownload-{self.session_id[:8]}", daemon=True)
        self.worker.start()

    def _download_progress(self, data: dict) -> None:
        with self._metrics_lock:
            if data.get("status") == "finished":
                self.progress = {"download_status": "finished", "downloaded_bytes": data.get("total_bytes", 0),
                                 "total_bytes": data.get("total_bytes", 0), "percent": 100.0}
                self.live_metrics["current_file"] = Path(data.get("filename", "vídeo local")).name
                return
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            self.progress = {"download_status": data.get("status", "downloading"),
                             "downloaded_bytes": downloaded, "total_bytes": total,
                             "percent": round(downloaded * 100 / total, 1) if total else None,
                             "speed_bytes_per_second": data.get("speed"), "eta_seconds": data.get("eta")}
            self.live_metrics["current_file"] = Path(data.get("filename", "vídeo do YouTube")).name

    def _run(self) -> None:
        source = None
        try:
            self.source_dir.mkdir(parents=True, exist_ok=True)
            self.store.set_session_state(self.session_id, "downloading")
            reuse_path = (self.source_config.get("reuse_download_path")
                          or self.source_config.get("downloaded_path"))
            if reuse_path and Path(reuse_path).is_file():
                metadata = self.source_config.get("reuse_download_metadata") or {
                    "title": self.source_config.get("video_title", Path(reuse_path).stem),
                    "video_id": self.source_config.get("video_id"),
                    "duration": self.source_config.get("video_duration", 0),
                    "format_id": self.source_config.get("video_format_id"),
                    "video_codec": self.source_config.get("video_codec"),
                    "width": self.source_config.get("video_width"),
                    "height": self.source_config.get("video_height"),
                    "size_bytes": self.source_config.get("video_size_bytes", 0),
                    "audio_streams": 0,
                }
                result = {**metadata,
                          "path": str(Path(reuse_path).resolve()), "size_bytes": Path(reuse_path).stat().st_size,
                          "audio_streams": 0}
                self._download_progress({"status": "finished", "filename": result["path"],
                                         "total_bytes": result["size_bytes"]})
                self.detail = "Vídeo local já baixado; preparando esta outra faixa de análise."
            else:
                self.detail = "Baixando somente a faixa de vídeo, na melhor qualidade disponível."
                result = download_best_video(self.source_config["url"], VIDEO_LIBRARY_DIR,
                                             self._download_progress, self.stop_requested)
            if self.stop_requested.is_set():
                self.store.set_session_state(self.session_id, "stopped")
                self.detail = "Download cancelado."
                return
            video_duration = float(result.get("duration") or 0)
            start = float(self.source_config["start"])
            requested_duration = float(self.source_config["duration"])
            if video_duration and start >= video_duration:
                raise ValueError(f"O início {start:.3f}s fica depois do fim do vídeo ({video_duration:.3f}s).")
            duration = min(requested_duration, video_duration - start) if video_duration else requested_duration
            config = self.store.session(self.session_id)["config_json"]
            downloaded_at = datetime.now(timezone.utc).isoformat()
            config.update({"downloaded_path": result["path"], "video_library_dir": str(VIDEO_LIBRARY_DIR.resolve()),
                           "download_manifest": "download_manifest.json",
                           "library_manifest_path": str(Path(result["path"] + ".json")),
                           "video_title": result["title"], "video_id": result["video_id"],
                           "video_duration": video_duration, "video_duration_requested": requested_duration,
                           "duration": duration, "video_format_id": result["format_id"],
                           "video_codec": result["video_codec"], "video_width": result["width"],
                           "video_height": result["height"], "video_size_bytes": result["size_bytes"],
                           "download_video_only": True, "downloaded_at": downloaded_at,
                           "source_origin": {"provider": "youtube", "url": self.source_config["url"],
                                             "video_id": result["video_id"], "title": result["title"],
                                             "downloaded_at": downloaded_at},
                           "timing_source": "ffprobe best_effort_timestamp_time"})
            if self.source_config.get("reused_source_from_session"):
                config["reused_source_from_session"] = self.source_config["reused_source_from_session"]
            (self.source_dir / "download_manifest.json").write_text(
                json.dumps({**result, "requested_url": self.source_config["url"], "start": start,
                            "analysis_duration": duration, "audio_streams": 0,
                            "downloaded_at": downloaded_at, "library_path": result["path"]},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
            Path(result["path"] + ".json").write_text(
                json.dumps({**result, "requested_url": self.source_config["url"],
                            "audio_streams": 0, "downloaded_at": downloaded_at},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            self.store.update_session_config(self.session_id, config)
            self.source_config.update(config)
            source = FileVideoSource(result["path"], self.session_id, start, duration)
            self.latest_frame = source.preview_at(start).image
            self.detail = f"Download concluído: {result['width']}×{result['height']}. Ajuste as ROIs e inicie a análise."
            self.store.set_session_state(self.session_id, "preparing")
            self.ready.set()
            while not self.start_requested.wait(timeout=0.5):
                if self.stop_requested.is_set():
                    self.store.set_session_state(self.session_id, "stopped")
                    self.detail = "Sessão cancelada antes da análise. O vídeo baixado foi preservado."
                    return
            if self.stop_requested.is_set():
                self.store.set_session_state(self.session_id, "stopped")
                self.detail = "Análise cancelada antes do início. O vídeo baixado foi preservado."
                return
            self.regions = self.store.session(self.session_id)["config_json"].get("regions", [])
            if source:
                source.close()
                source = None
            current_config = self.store.session(self.session_id)["config_json"]
            analysis_path = current_config.get("downloaded_path") or result["path"]
            source = FileVideoSource(analysis_path, self.session_id, start, duration,
                                     segment_id="youtube-download-0001", stop_requested=self.stop_requested,
                                     pause_requested=self.pause_requested, resume_requested=self.resume_requested)
            self.store.set_session_state(self.session_id, "processing")
            self.detail = "Analisando o vídeo local; a linha do tempo segue os timestamps originais do vídeo."
            processor = DialogueProcessor(self.store, self.session_id, self.source_dir,
                                          [Region.from_dict(item, index, start) for index, item in enumerate(self.regions)],
                                          self.language, OcrEngine(self.language, self.source_config.get("processing_settings", {})),
                                          self._update_progress, self.source_config.get("processing_settings", {}))
            self.stats = processor.run(source)
            source = None
            state = "stopped" if self.stop_requested.is_set() else "complete"
            if state == "complete":
                completed_config = self.store.session(self.session_id)["config_json"]
                completed_config.update({"analysis_completed": True,
                                         "analysis_completed_at": datetime.now(timezone.utc).isoformat()})
                self.store.update_session_config(self.session_id, completed_config)
            self.store.set_session_state(self.session_id, state)
            self.detail = "Análise concluída." if state == "complete" else "Análise interrompida pelo usuário."
            (self.source_dir / "result.json").write_text(json.dumps(self.stats, ensure_ascii=False, indent=2),
                                                          encoding="utf-8")
        except InterruptedError as exc:
            state = "stopped" if self.stop_requested.is_set() else "failed"
            self.error = None if state == "stopped" else f"{type(exc).__name__}: {exc}"
            self.detail = "Download cancelado." if state == "stopped" else "O download foi interrompido."
            self.store.set_session_state(self.session_id, state, self.error)
        except Exception as exc:
            if self.stop_requested.is_set():
                self.error = None
                self.detail = "Download interrompido. O arquivo parcial foi preservado para retomar."
                self.store.set_session_state(self.session_id, "stopped")
            else:
                self.error = f"{type(exc).__name__}: {exc}"
                self.detail = "Não foi possível preparar ou analisar o vídeo. Consulte o erro da sessão."
                self.store.set_session_state(self.session_id, "failed", self.error)
        finally:
            self.ready.set()
            if source:
                source.close()
            if self.on_complete:
                self.on_complete()

    def _update_progress(self, progress: dict) -> None:
        self.progress = dict(progress)
        self.latest_frame = None
        if progress.get("current_source_time") is not None:
            self.live_metrics["current_source_time"] = float(progress["current_source_time"])
        if progress.get("frames_seen") is not None:
            self.live_metrics["frames_captured"] = int(progress["frames_seen"])
        self.live_metrics["timing_quality"] = "exact_pts"
        self.live_metrics["timing_error_ms"] = 1.0
        self.live_metrics["current_file"] = Path(self.source_config.get("downloaded_path", "vídeo")).name

    def _source_status(self, state: str, detail: str) -> None:
        self.detail = detail
        if state in {"running", "paused", "processing"}:
            self.store.set_session_state(self.session_id, state)

    def _on_capture(self, image, record: dict, count: int) -> None:
        mono = int(record["monotonic_ns"])
        with self._metrics_lock:
            self.live_metrics["frames_captured"] = int(self.live_metrics["frames_captured"]) + 1
            self.live_metrics["current_source_time"] = float(record["source_time"])
            self.live_metrics["current_segment"] = int(str(record.get("segment_id", "0")).split("-")[-1]) + 1
            self.live_metrics["timing_quality"] = record.get("timing_quality", "?")
            self.live_metrics["timing_error_ms"] = float(record.get("timing_error_ms", 0))
            self.live_metrics["current_file"] = record.get("file", "segmento de captura")
            if self._first_monotonic_ns is None:
                self._first_monotonic_ns = mono
            if self._previous_capture_ns is not None:
                gap = (mono - self._previous_capture_ns) / 1_000_000_000
                if 0 < gap < 0.5:
                    self._active_capture_seconds += gap
            self._previous_capture_ns = mono
            if self._active_capture_seconds > 0:
                self.live_metrics["effective_fps"] = (self.live_metrics["frames_captured"] - 1) / self._active_capture_seconds
            if count % 5 == 0:
                self.latest_frame = image.copy()

    def _update_progress(self, progress: dict) -> None:
        self.progress = dict(progress)

    def _source_status(self, state: str, detail: str) -> None:
        self.detail = detail
        if state in {"running", "paused", "processing"}:
            self.store.set_session_state(self.session_id, state)


class SessionManager:
    def __init__(self, store: DubSyncStore, settings_provider: Callable[[], dict] | None = None) -> None:
        self.store = store
        self.settings_provider = settings_provider or (lambda: {})
        self.jobs: dict[str, YoutubeJob | YoutubeDownloadJob] = {}
        self.file_progress: dict[str, dict] = {}
        self.file_pause_events: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.busy = False
        self._recover_interrupted_sessions()

    def _new_session_asset_dir(self, session_id: str, config: dict) -> Path:
        project_id = current_project_id()
        with self.store.Session() as db:
            project = db.get(DubbingProject, project_id)
            slug = project.slug if project else "replaced"
        relative = Path("projects") / slug / "capture" / "sessions" / session_id
        config["asset_root"] = relative.as_posix()
        directory = self.store.sessions_root.parent / relative
        directory.mkdir(parents=True, exist_ok=False)
        return directory

    def _recover_interrupted_sessions(self) -> None:
        # A downloaded video waiting for ROI calibration can recover without transferring it again.
        rows = self.store.sessions(limit=100_000)
        recovered = False
        for session in rows:
            config = session["config_json"] or {}
            source_path = config.get("downloaded_path")
            resumable_calibration = (session["state"] == "preparing"
                                     and config.get("mode") == "youtube_download"
                                     and not config.get("analysis_started")
                                     and source_path and Path(source_path).is_file())
            if resumable_calibration and not recovered:
                job = YoutubeDownloadJob(self.store, session["session_id"],
                                         self.store.session_asset_dir(session["session_id"]), config,
                                         session["language"], self._mark_idle)
                self.jobs[session["session_id"]] = job
                self.busy = True
                recovered = True
                job.launch()
                continue
            if (session["source_type"] == "file" and session["state"] == "paused"
                    and config.get("resume_source_time") is not None):
                continue
            if session["state"] in {"queued", "downloading", "preparing", "running", "processing", "pausing", "paused", "stopping"}:
                self.store.set_session_state(
                    session["session_id"], "stopped",
                    "Servidor reiniciado; os segmentos concluídos foram preservados.",
                )

    def _mark_idle(self) -> None:
        with self.lock:
            self.busy = False

    def prepare_youtube(self, url: str, start: float, duration: float, language: str,
                        segment_duration_seconds: int = 60) -> dict:
        video_id = validate_youtube_url(url)
        validate_duration(duration, start)
        if not 5 <= segment_duration_seconds <= 900:
            raise ValueError("A duração dos blocos precisa ficar entre 5 e 900 segundos.")
        session_id = str(uuid.uuid4())
        config = {"url": url, "start": float(start), "duration": float(duration),
                  "mode": "youtube_download", "video_only": True,
                  "segment_duration_seconds": segment_duration_seconds, "block_index": 1}
        with self.lock:
            if self.busy:
                raise ValueError("Encerre a sessão ativa antes de iniciar outra sessão.")
            existing_sessions = self.store.sessions(limit=100_000)
            for existing in existing_sessions:
                if existing["source_type"] != "youtube" or existing["language"] != language:
                    continue
                existing_config = existing["config_json"] or {}
                try:
                    existing_video_id = existing_config.get("video_id") or validate_youtube_url(
                        existing_config.get("url") or existing["source_uri"])
                except (TypeError, ValueError):
                    continue
                same_interval = (existing_video_id == video_id
                                 and abs(float(existing["source_start"]) - float(start)) < 0.001
                                 and abs(float(existing["requested_duration"]) - float(duration)) < 0.001)
                if not same_interval:
                    continue
                if existing["state"] == "complete" and existing_config.get("analysis_completed", True):
                    return {"session_id": existing["session_id"], "state": "complete", "reused": True}
                if (existing["state"] in {"failed", "stopped"}
                        and existing_config.get("mode") == "youtube_download"
                        and not existing_config.get("analysis_started")):
                    return {**self.resume(existing["session_id"]), "resumed": True}

            reusable = next((item for item in existing_sessions
                             if item["source_type"] == "youtube"
                             and (item["config_json"] or {}).get("video_id") == video_id
                             and (item["config_json"] or {}).get("downloaded_path")
                             and Path(item["config_json"]["downloaded_path"]).is_file()), None)
            if reusable:
                reusable_config = reusable["config_json"]
                config["reuse_download_path"] = reusable_config["downloaded_path"]
                config["reuse_download_metadata"] = {
                    "title": reusable_config.get("video_title", video_id), "video_id": video_id,
                    "duration": reusable_config.get("video_duration", 0),
                    "format_id": reusable_config.get("video_format_id"),
                    "video_codec": reusable_config.get("video_codec"),
                    "width": reusable_config.get("video_width"), "height": reusable_config.get("video_height"),
                    "size_bytes": reusable_config.get("video_size_bytes", 0), "audio_streams": 0,
                }
                config["reused_source_from_session"] = reusable["session_id"]
            self.busy = True
            try:
                source_dir = self._new_session_asset_dir(session_id, config)
                self.store.create_session(session_id, "youtube", url, float(start), float(duration), language, config)
            except Exception:
                self.busy = False
                raise
            job = YoutubeDownloadJob(self.store, session_id, source_dir, config, language, self._mark_idle)
            self.jobs[session_id] = job
        job.launch()
        return {"session_id": session_id, "state": "queued"}

    def start_youtube(self, session_id: str, screen_bbox: dict[str, int] | None,
                      regions: list[dict] | None) -> dict:
        job = self._job(session_id)
        if job.error:
            raise ValueError(job.error)
        if not job.ready.is_set():
            raise ValueError("O player ainda está carregando; tente novamente em alguns segundos.")
        if self.store.session(session_id)["state"] != "preparing":
            raise ValueError("Esta sessão já foi iniciada ou encerrada.")
        if not isinstance(job, YoutubeDownloadJob):
            raise ValueError("A sessão não usa o fluxo de download local do YouTube.")
        if not job.source_config.get("downloaded_path"):
            raise ValueError("Aguarde o download do vídeo terminar.")
        if job.latest_frame is None:
            raise ValueError("A prévia local do vídeo ainda não está disponível.")
        job.regions = validate_regions(regions)
        session = self.store.session(session_id)
        session["config_json"].update({"regions": job.regions,
                                      "capture_backend": "downloaded_video",
                                      "analysis_started": True,
                                      "video_only": True,
                                      "processing_settings": self.settings_provider(),
                                      "timing_source": "ffprobe best_effort_timestamp_time"})
        self.store.update_session_config(session_id, session["config_json"])
        job.source_config.update(session["config_json"])
        self.store.set_session_state(session_id, "preparing")
        job.start_requested.set()
        return {"session_id": session_id, "state": "starting", "path": session["config_json"]["downloaded_path"]}

    def start_file(self, path: str, start: float, duration: float, language: str,
                   regions: list[dict] | None, continuation: dict | None = None) -> dict:
        validate_duration(duration, start)
        video_path = Path(path).expanduser().resolve(strict=True)
        if not video_path.is_file():
            raise ValueError("Selecione um arquivo de vídeo local válido.")
        video_duration = probe_video_duration(video_path)
        if start >= video_duration:
            raise ValueError(f"O início {start:.3f}s está no fim do vídeo ({video_duration:.3f}s).")
        actual_duration = min(float(duration), video_duration - float(start))
        session_id = str(uuid.uuid4())
        region_list = validate_regions(regions)
        config = {"path": str(video_path), "mode": "file", "regions": region_list,
                  "start": float(start), "duration": actual_duration, "requested_duration": float(duration),
                  "video_duration": video_duration, "video_only": True,
                  "segment_duration_seconds": 60, "replace_existing_events": True,
                  "processing_settings": self.settings_provider()}
        if continuation:
            config.update(continuation)
        manifest = video_path.with_suffix(video_path.suffix + ".json")
        if manifest.is_file():
            try:
                metadata = json.loads(manifest.read_text(encoding="utf-8"))
                video_id = str(metadata.get("video_id") or "")
                requested_url = str(metadata.get("requested_url") or "")
                if video_id and requested_url:
                    resolution = metadata.get("resolution") or [None, None]
                    config.update({
                        "downloaded_path": str(video_path),
                        "video_library_dir": str(video_path.parent),
                        "library_manifest_path": str(manifest),
                        "video_title": metadata.get("title") or video_path.stem,
                        "video_id": video_id,
                        "video_duration": float(metadata.get("duration") or video_duration),
                        "video_codec": metadata.get("codec"),
                        "video_width": resolution[0],
                        "video_height": resolution[1],
                        "video_size_bytes": video_path.stat().st_size,
                        "downloaded_at": metadata.get("downloaded_at"),
                        "download_video_only": int(metadata.get("audio_streams", 0)) == 0,
                        "source_origin": {
                            "provider": "youtube",
                            "url": requested_url,
                            "video_id": video_id,
                            "title": metadata.get("title") or video_path.stem,
                            "downloaded_at": metadata.get("downloaded_at"),
                        },
                    })
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        with self.lock:
            if self.busy:
                raise ValueError("Encerre a sessão ativa antes de iniciar outra.")
            self.busy = True
            try:
                config.setdefault("block_index", 1)
                source_dir = self._new_session_asset_dir(session_id, config)
                self.store.create_session(session_id, "file", str(video_path), float(start), float(actual_duration),
                                          language, config, "queued")
            except Exception:
                self.busy = False
                raise
            worker = threading.Thread(target=self._run_file, args=(session_id, source_dir, config, language),
                                      name=f"DubSyncFile-{session_id[:8]}", daemon=True)
            self.file_pause_events[session_id] = threading.Event()
            self.file_progress[session_id] = {
                "stage": "queued", "activity_message": "Sessão na fila de processamento.",
                "frames_seen": 0, "frames_analyzed": 0, "ocr_calls": 0,
                "ocr_failures": 0, "events_created": 0, "events_deduplicated": 0,
                "recent_activity": [],
            }
        worker.start()
        return {"session_id": session_id, "state": "queued", "start": float(start),
                "duration": actual_duration, "requested_duration": float(duration),
                "video_duration": video_duration, "block_index": int(config.get("block_index", 1))}

    def next_file_block(self, previous_session_id: str, duration_seconds: float) -> dict:
        validate_duration(duration_seconds, 0)
        previous = self.store.session(previous_session_id)
        if not previous:
            raise KeyError(previous_session_id)
        if previous["state"] != "complete":
            raise ValueError("Aguarde a conclusão do bloco atual antes de continuar.")
        config = previous.get("config_json") or {}
        source_path = config.get("downloaded_path") or config.get("path")
        if not source_path or not Path(source_path).is_file():
            raise ValueError("O vídeo usado no bloco anterior não está disponível.")
        video_duration = float(config.get("video_duration") or probe_video_duration(source_path))
        start = float(previous["source_start"]) + float(config.get("duration") or previous["requested_duration"])
        remaining = max(0.0, video_duration - start)
        if remaining <= 0.001:
            raise ValueError("A análise já alcançou o final do vídeo.")
        actual = min(float(duration_seconds), remaining)
        block_index = int(config.get("block_index", 1)) + 1
        return self.start_file(
            source_path, start, actual, previous["language"], config.get("regions", []),
            continuation={"block_index": block_index, "previous_session_id": previous_session_id,
                          "sector_index": int(config.get("sector_index", 1)),
                          "block_duration_requested": float(duration_seconds),
                          "block_duration_clamped": actual < float(duration_seconds)},
        )

    def _sector_root(self, session_id: str) -> tuple[dict, list[dict]]:
        sessions = self.store.sessions(limit=100_000)
        chain = linked_video_chain(sessions, session_id)
        if not chain:
            raise KeyError(session_id)
        identity = video_identity(chain[-1])
        root = chain[-1]
        for item in reversed(chain[:-1]):
            if video_identity(item) != identity:
                break
            root = item
        return root, chain

    def plan_next_video(self, session_id: str, path: str, block_duration_seconds: float) -> dict:
        validate_duration(block_duration_seconds, 0)
        current = self.store.session(session_id)
        if not current:
            raise KeyError(session_id)
        if current["source_type"] not in {"file", "youtube"}:
            raise ValueError("Selecione uma captura de vídeo para planejar a continuação.")
        root, chain = self._sector_root(session_id)
        if root["state"] != "complete":
            raise ValueError("Conclua o primeiro bloco deste vídeo antes de planejar o próximo arquivo.")
        source_path = (current.get("config_json") or {}).get("downloaded_path") or (current.get("config_json") or {}).get("path")
        if not source_path:
            raise ValueError("O vídeo atual não está disponível como arquivo local.")
        next_path = Path(path).expanduser().resolve(strict=True)
        if not next_path.is_file():
            raise ValueError("Selecione um arquivo de vídeo local válido.")
        existing_paths = [((item.get("config_json") or {}).get("downloaded_path") or
                           (item.get("config_json") or {}).get("path")) for item in chain]
        if any(path_value and os.path.normcase(str(next_path)) == os.path.normcase(
                str(Path(path_value).expanduser().resolve(strict=False))) for path_value in existing_paths):
            raise ValueError("Este arquivo já está na sequência. Use a lista de vídeos para voltar a ele.")
        if any(video_identity(item) != video_identity(root) for item in chain[chain.index(root) + 1:]):
            raise ValueError("Este setor já foi continuado em outro vídeo.")
        duration = probe_video_duration(next_path)
        planned = {"path": str(next_path), "duration": duration,
                   "block_duration_seconds": float(block_duration_seconds),
                   "size": next_path.stat().st_size, "mtime_ns": next_path.stat().st_mtime_ns}
        config = dict(root.get("config_json") or {})
        config["planned_next_video"] = planned
        self.store.update_session_config(root["session_id"], config)
        last = chain[-1]
        last_config = last.get("config_json") or {}
        last_end = float(last["source_start"]) + float(last_config.get("duration") or last["requested_duration"])
        remaining = max(0.0, float(last_config.get("video_duration") or 0) - last_end)
        current_step = float(last_config.get("block_duration_requested") or 1200)
        first_index = int(last_config.get("block_index", 1)) + math.ceil(max(0, remaining - 1e-6) / current_step) + 1
        return {"planned_next_video": planned, "first_block_index": first_index}

    def next_file_video(self, previous_session_id: str) -> dict:
        previous = self.store.session(previous_session_id)
        if not previous:
            raise KeyError(previous_session_id)
        if previous["state"] != "complete":
            raise ValueError("Aguarde a conclusão do bloco atual antes de continuar.")
        config = previous.get("config_json") or {}
        end = float(previous["source_start"]) + float(config.get("duration") or previous["requested_duration"])
        if end < float(config.get("video_duration") or 0) - .001:
            raise ValueError("Ainda há trechos neste vídeo. Use Próximo bloco antes de mudar de arquivo.")
        root, chain = self._sector_root(previous_session_id)
        planned = (self.store.session(root["session_id"]).get("config_json") or {}).get("planned_next_video") or {}
        if not planned.get("path"):
            raise ValueError("Adicione o próximo vídeo antes de iniciar outro setor.")
        if any((item.get("config_json") or {}).get("previous_session_id") == previous_session_id
               for item in self.store.sessions(limit=100_000)):
            raise ValueError("O próximo bloco desta sessão já existe. Abra-o na linha de progresso.")
        next_path = Path(planned["path"]).expanduser().resolve(strict=True)
        if (next_path.stat().st_size != planned.get("size") or
                next_path.stat().st_mtime_ns != planned.get("mtime_ns")):
            raise ValueError("O próximo vídeo mudou desde o planejamento. Adicione o arquivo novamente.")
        duration = probe_video_duration(next_path)
        if abs(duration - float(planned["duration"])) > .1:
            raise ValueError("A duração do próximo vídeo mudou. Adicione o arquivo novamente.")
        step = float(planned["block_duration_seconds"])
        result = self.start_file(
            str(next_path), 0, min(step, duration), previous["language"], config.get("regions", []),
            continuation={"block_index": int(config.get("block_index", 1)) + 1,
                          "previous_session_id": previous_session_id,
                          "sector_index": int(config.get("sector_index", 1)) + 1,
                          "block_duration_requested": step},
        )
        return result

    def resume_file(self, session_id: str) -> dict:
        """Continue a paused block at its saved timestamp, or retry an empty failed block."""
        row = self.store.session(session_id)
        if not row:
            raise KeyError(session_id)
        if row["source_type"] != "file":
            raise ValueError("Esta sessão não é uma análise de arquivo local.")
        if row["state"] not in {"paused", "stopped", "failed"}:
            raise ValueError("Somente um bloco pausado, interrompido ou com falha pode ser retomado.")
        was_paused = row["state"] == "paused"
        if not was_paused and self.store.events(session_id):
            raise ValueError("Este bloco já tem diálogos salvos. Para evitar duplicações, a retomada automática só está disponível quando nenhum diálogo foi gravado.")

        config = dict(row.get("config_json") or {})
        source_path = config.get("downloaded_path") or config.get("path") or row["source_uri"]
        video_path = Path(source_path).expanduser().resolve(strict=True)
        if not video_path.is_file():
            raise ValueError("O arquivo de vídeo deste bloco não está disponível.")
        start = float(row["source_start"])
        duration = float(config.get("duration") or row["requested_duration"])
        validate_duration(duration, start)
        video_duration = float(config.get("video_duration") or probe_video_duration(video_path))
        if start >= video_duration:
            raise ValueError("O início salvo para este bloco está no fim do vídeo.")
        duration = min(duration, video_duration - start)
        resume_at = float(config.get("resume_source_time", start)) if was_paused else start
        if not math.isfinite(resume_at) or not start <= resume_at < start + duration:
            raise ValueError("O ponto salvo deste bloco não é válido.")
        if was_paused and (config.get("resume_video_size") != video_path.stat().st_size or
                           config.get("resume_video_mtime_ns") != video_path.stat().st_mtime_ns):
            raise ValueError("O arquivo de vídeo mudou desde a pausa. Restaure o arquivo original antes de continuar.")
        regions = validate_regions(config.get("regions") or [])
        config.update({"path": str(video_path), "start": start, "duration": duration,
                       "requested_duration": float(row["requested_duration"]),
                       "video_duration": video_duration, "regions": regions,
                       "replace_existing_events": True})
        config.setdefault("processing_settings", self.settings_provider())
        source_dir = self.store.session_asset_dir(session_id)
        source_dir.mkdir(parents=True, exist_ok=True)

        with self.lock:
            if self.busy:
                raise ValueError("Encerre a sessão ativa antes de retomar este bloco.")
            self.busy = True
            try:
                self.store.update_session_config(session_id, config)
                self.store.set_session_state(session_id, "queued")
                self.file_progress[session_id] = {
                    "stage": "queued", "current_source_time": resume_at,
                    "activity_message": (f"Retomando bloco em {resume_at:.3f}s."
                                         if was_paused else "Retomando bloco interrompido desde o início do intervalo salvo."),
                    **{key: int((config.get("analysis_totals") or {}).get(key, 0)) for key in
                       ("frames_seen", "frames_analyzed", "ocr_calls", "ocr_failures",
                        "events_created", "events_deduplicated")},
                    "recent_activity": [],
                }
                worker = threading.Thread(target=self._run_file,
                                          args=(session_id, source_dir, config, row["language"]),
                                          name=f"DubSyncFileResume-{session_id[:8]}", daemon=True)
                self.file_pause_events[session_id] = threading.Event()
            except Exception:
                self.busy = False
                raise
        worker.start()
        return {"session_id": session_id, "state": "queued", "start": start,
                "duration": duration, "block_index": int(config.get("block_index", 1)),
                "resumed": True, "resume_source_time": resume_at,
                "restarted_from_block_start": not was_paused}

    def pause_file(self, session_id: str) -> dict:
        row = self.store.session(session_id)
        if not row:
            raise KeyError(session_id)
        if row["source_type"] != "file" or row["state"] not in {"queued", "running", "pausing"}:
            raise ValueError("A pausa está disponível enquanto este bloco de arquivo é analisado.")
        with self.lock:
            pause_event = self.file_pause_events.get(session_id)
            if pause_event is None:
                raise ValueError("A análise não está ativa neste processo.")
            self.store.set_session_state(session_id, "pausing")
            self._update_file_progress(session_id, {"stage": "pausing", "activity_message":
                                       "Concluindo a leitura atual e salvando o ponto de pausa."})
            pause_event.set()
        return {"session_id": session_id, "state": "pausing"}

    def restart_file_block(self, session_id: str) -> dict:
        row = self.store.session(session_id)
        if not row:
            raise KeyError(session_id)
        if row["source_type"] != "file" or row["state"] != "paused":
            raise ValueError("Somente um bloco pausado pode ser apagado e reiniciado.")
        with self.lock:
            if self.busy:
                raise ValueError("Aguarde a análise ativa terminar antes de reiniciar este bloco.")
        removed = self.store.clear_session_events(session_id)
        config = dict(row.get("config_json") or {})
        for key in ("resume_source_time", "resume_video_size", "resume_video_mtime_ns", "analysis_totals"):
            config.pop(key, None)
        self.store.update_session_config(session_id, config)
        self.store.set_session_state(session_id, "stopped")
        result = self.resume_file(session_id)
        result["removed_events"] = removed
        return result

    def organize_video(self, session_id: str) -> dict:
        """Expose an older session's downloaded source in the project's easy-to-find video library."""
        row = self.store.session(session_id)
        if not row:
            raise KeyError(session_id)
        config = row["config_json"] or {}
        original = config.get("downloaded_path")
        if not original or not Path(original).is_file():
            raise ValueError("O arquivo baixado não está disponível nesta sessão.")
        VIDEO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        library_dir = VIDEO_LIBRARY_DIR.resolve()
        source = Path(original).resolve()
        if source.parent == library_dir:
            return {"path": str(source), "directory": str(library_dir), "reused": True}
        filename = _library_filename(config.get("video_title", source.stem),
                                    config.get("video_id", source.stem), source.suffix)
        target = library_dir / filename
        if target.exists() and not os.path.samefile(source, target):
            target = library_dir / _library_filename(config.get("video_title", source.stem),
                                                     f"{config.get('video_id', source.stem)}-{session_id[:8]}",
                                                     source.suffix)
        if not target.exists():
            try:
                os.link(source, target)
            except OSError as exc:
                raise ValueError(f"Não foi possível colocar o vídeo em {library_dir}: {exc}") from exc
        config.update({"downloaded_path": str(target), "video_library_dir": str(library_dir),
                       "library_manifest_path": str(target) + ".json"})
        config.setdefault("source_origin", {"provider": "youtube", "url": config.get("url"),
                                             "video_id": config.get("video_id"),
                                             "title": config.get("video_title"),
                                             "downloaded_at": config.get("downloaded_at")})
        self.store.update_session_config(session_id, config)
        with self.lock:
            job = self.jobs.get(session_id)
            if isinstance(job, YoutubeDownloadJob):
                job.source_config.update(config)
                with job._metrics_lock:
                    job.live_metrics["current_file"] = target.name
        session_dir = self.store.session_asset_dir(session_id)
        manifest = session_dir / "download_manifest.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            data.update({"library_path": str(target), "video_library_dir": str(library_dir)})
            manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        sidecar = {"title": config.get("video_title"), "video_id": config.get("video_id"),
                   "requested_url": config.get("url"), "path": str(target),
                   "resolution": [config.get("video_width"), config.get("video_height")],
                   "codec": config.get("video_codec"), "duration": config.get("video_duration"),
                   "size_bytes": target.stat().st_size, "audio_streams": 0,
                   "downloaded_at": config.get("downloaded_at")}
        Path(str(target) + ".json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(target), "directory": str(library_dir), "reused": False}

    def _run_file(self, session_id: str, source_dir: Path, config: dict, language: str) -> None:
        with self.lock:
            pause_event = self.file_pause_events[session_id]
        self.store.set_session_state(session_id, "pausing" if pause_event.is_set() else "running")
        source = None
        try:
            block_start = float(config["start"])
            block_end = block_start + float(config["duration"])
            scan_start = float(config.get("resume_source_time", block_start))
            counter_keys = ("frames_seen", "frames_analyzed", "ocr_calls", "ocr_failures",
                            "events_created", "events_deduplicated")
            baseline = dict(config.get("analysis_totals") or {})

            def report(progress: dict) -> None:
                current = dict(progress)
                for key in counter_keys:
                    if key in current:
                        current[key] = int(baseline.get(key, 0)) + int(current[key])
                if current.get("frames_total") is not None:
                    current["frames_total"] = int(baseline.get("frames_seen", 0)) + int(current["frames_total"])
                self._update_file_progress(session_id, current)

            self._update_file_progress(session_id, {
                "stage": "loading_ocr", "current_source_time": scan_start,
                "activity_message": "Carregando o mecanismo de OCR antes da leitura dos quadros.",
                **{key: int(baseline.get(key, 0)) for key in counter_keys},
            })
            source = FileVideoSource(config["path"], session_id, scan_start, block_end - scan_start,
                                     pause_requested=pause_event, stop_on_pause=True,
                                     progress_callback=report)
            processor = DialogueProcessor(self.store, session_id, source_dir,
                                          [Region.from_dict(item, index, config["start"]) for index, item in enumerate(config["regions"])],
                                          language, OcrEngine(language, config.get("processing_settings", {})),
                                          report,
                                          config.get("processing_settings", {}),
                                          replace_scope={"source_path": config["path"],
                                                         "start": config["start"],
                                                         "end": config["start"] + config["duration"]})
            stats = processor.run(source)
            stats = {**stats, **{key: int(baseline.get(key, 0)) + int(stats.get(key, 0))
                                 for key in counter_keys}}
            if source.paused:
                checkpoint = source.last_source_time
                resume_at = min(block_end - 0.000001,
                                max(scan_start, (checkpoint + 0.0005) if checkpoint is not None else scan_start))
                config.update({"resume_source_time": resume_at,
                               "resume_video_size": Path(config["path"]).stat().st_size,
                               "resume_video_mtime_ns": Path(config["path"]).stat().st_mtime_ns,
                               "analysis_totals": {key: stats[key] for key in counter_keys}})
                self.store.update_session_config(session_id, config)
                self.store.set_session_state(session_id, "paused")
                self._update_file_progress(session_id, {**stats, "stage": "paused",
                                                      "current_source_time": resume_at,
                                                      "activity_message": f"Bloco pausado em {resume_at:.3f}s. É seguro fechar o aplicativo."})
            else:
                for key in ("resume_source_time", "resume_video_size", "resume_video_mtime_ns"):
                    config.pop(key, None)
                self.store.update_session_config(session_id, config)
                self.store.set_session_state(session_id, "complete")
                self._update_file_progress(session_id, {**stats, "stage": "complete",
                                                      "current_source_time": block_end,
                                                      "activity_message": "Análise do bloco concluída."})
                (source_dir / "result.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            if source:
                source.close()
            self._update_file_progress(session_id, {
                "stage": "failed", "activity_message": f"Falha na análise: {type(exc).__name__}: {exc}",
            })
            self.store.set_session_state(session_id, "failed", f"{type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.file_pause_events.pop(session_id, None)
            self._mark_idle()

    def status(self, session_id: str) -> dict | None:
        row = self.store.session(session_id)
        if not row:
            return None
        with self.lock:
            job = self.jobs.get(session_id)
        row["detail"] = job.detail if job else row["state"]
        row["ready_for_calibration"] = bool(job and job.ready.is_set() and not job.error)
        if job and job.stats:
            row["stats"] = job.stats
        if job:
            row["progress"] = dict(job.progress)
            with job._metrics_lock:
                row.update(job.live_metrics)
        elif session_id in self.file_progress:
            row["progress"] = dict(self.file_progress[session_id])
            row["frames_captured"] = self.file_progress[session_id].get("frames_seen", 0)
            row["current_file"] = Path(row["source_uri"]).name
            row["current_source_time"] = self.file_progress[session_id].get("current_source_time", row["source_start"])
            row["current_segment"] = 1
            row["timing_quality"] = "exact_pts"
        elif row["source_type"] == "file" and row["state"] == "paused":
            config = row.get("config_json") or {}
            checkpoint = float(config.get("resume_source_time") or row["source_start"])
            row["progress"] = {"stage": "paused", "current_source_time": checkpoint,
                               "activity_message": f"Pausado em {checkpoint:.3f}s; pronto para continuar após reiniciar o PC.",
                               **dict(config.get("analysis_totals") or {})}
            row["frames_captured"] = row["progress"].get("frames_seen", 0)
            row["current_file"] = Path(row["source_uri"]).name
            row["current_source_time"] = checkpoint
            row["current_segment"] = 1
        return row

    def _update_file_progress(self, session_id: str, progress: dict) -> None:
        with self.lock:
            previous = self.file_progress.get(session_id, {})
            current = dict(progress)
            recent = list(previous.get("recent_activity", []))
            message = str(progress.get("activity_message") or "").strip()
            signature = (progress.get("stage"), message, progress.get("current_frame_id"),
                         progress.get("frames_indexed"), progress.get("frames_analyzed"))
            previous_signature = previous.get("_activity_signature")
            if message and signature != previous_signature:
                recent.append({
                    "at": datetime.now(timezone.utc).isoformat(),
                    "stage": progress.get("stage", ""),
                    "message": message,
                    "frame_id": progress.get("current_frame_id"),
                    "source_time": progress.get("current_source_time"),
                })
            current["recent_activity"] = recent[-10:]
            current["_activity_signature"] = signature
            self.file_progress[session_id] = current

    def preview_frame(self, session_id: str):
        with self.lock:
            job = self.jobs.get(session_id)
        if job and job.latest_frame is not None:
            return job.latest_frame
        session = self.store.session(session_id)
        video_path = (session or {}).get("config_json", {}).get("downloaded_path")
        if video_path and Path(video_path).is_file():
            source = FileVideoSource(video_path, session_id,
                                     float((session or {}).get("source_start") or 0), 1.0)
            try:
                source.open()
                packet = source.frame_near(float((session or {}).get("source_start") or 0))
                return packet.image if packet else None
            finally:
                source.close()
        return None

    def pause(self, session_id: str) -> dict:
        job = self._job(session_id)
        if isinstance(job, YoutubeDownloadJob) and self.store.session(session_id)["state"] != "processing":
            raise ValueError("A pausa fica disponível enquanto o vídeo baixado está sendo analisado.")
        job.pause_requested.set()
        job.resume_requested.clear()
        job.detail = "Pausa solicitada; o bloco de análise atual será concluído antes."
        self.store.set_session_state(session_id, "paused")
        return {"session_id": session_id, "state": "pause_after_block"}

    def resume(self, session_id: str) -> dict:
        job = self._job(session_id)
        row = self.store.session(session_id)
        if (isinstance(job, YoutubeDownloadJob) and row
                and row["state"] in {"failed", "stopped"}
                and job.source_config.get("mode") == "youtube_download"
                and not job.source_config.get("analysis_started")):
            with self.lock:
                if self.busy:
                    raise ValueError("Encerre a sessão ativa antes de retomar este download.")
                if job.worker and job.worker.is_alive():
                    raise ValueError("A interrupção ainda está sendo finalizada; tente novamente em alguns segundos.")
                self.busy = True
                job.stop_requested.clear()
                job.start_requested.clear()
                job.pause_requested.clear()
                job.resume_requested.set()
                job.ready.clear()
                job.error = None
                job.detail = "Retomando download; o arquivo parcial será aproveitado quando disponível."
                self.store.set_session_state(session_id, "queued")
                job.launch()
            return {"session_id": session_id, "state": "downloading", "resumed": True}
        job.pause_requested.clear()
        job.resume_requested.set()
        if isinstance(job, YoutubeDownloadJob):
            self.store.set_session_state(session_id, "processing")
        return {"session_id": session_id, "state": "resume_requested"}

    def stop(self, session_id: str) -> dict:
        job = self._job(session_id)
        job.stop_requested.set()
        job.start_requested.set()
        job.resume_requested.set()
        job.pause_requested.clear()
        self.store.set_session_state(session_id, "stopping")
        return {"session_id": session_id, "state": "stopping"}

    def _job(self, session_id: str) -> YoutubeJob:
        with self.lock:
            job = self.jobs.get(session_id)
            if not job:
                row = self.store.session(session_id)
                config = (row or {}).get("config_json") or {}
                if row and row["source_type"] == "youtube" and config.get("mode") == "youtube_download":
                    job = YoutubeDownloadJob(self.store, session_id, self.store.session_asset_dir(session_id),
                                             config, row["language"], self._mark_idle)
                    self.jobs[session_id] = job
        if not job:
            raise KeyError(session_id)
        return job

    def youtube_coverage(self, url: str) -> dict:
        video_id = validate_youtube_url(url)
        intervals = []
        for row in self.store.sessions(limit=100_000):
            if row["source_type"] != "youtube" or row["state"] != "complete":
                continue
            config = row["config_json"] or {}
            try:
                row_video_id = config.get("video_id") or validate_youtube_url(
                    config.get("url") or row["source_uri"])
            except (TypeError, ValueError):
                continue
            if row_video_id != video_id:
                continue
            start = float(row["source_start"])
            duration = float(config.get("duration") or row["requested_duration"])
            intervals.append({"session_id": row["session_id"], "start_time": start,
                              "end_time": start + duration,
                              "requested_end_time": start + float(row["requested_duration"]),
                              "events": len(self.store.events(row["session_id"])),
                              "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None})
        intervals.sort(key=lambda item: (item["start_time"], item["end_time"]))
        return {"video_id": video_id, "intervals": intervals}


def desktop_preview() -> dict:
    with mss.mss() as screen:
        monitor = dict(screen.monitors[0])
        image = screen.grab(monitor)
        png = mss.tools.to_png(image.rgb, image.size)
    return {"image": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
            "width": image.width, "height": image.height,
            "origin_left": monitor["left"], "origin_top": monitor["top"]}


def file_preview(path: str, start: float = 0) -> dict:
    video_duration = probe_video_duration(path)
    source = FileVideoSource(path, "preview", start, 1)
    source.open()
    try:
        packet = next(source.iter_frames(), None)
        if packet is None:
            raise ValueError("Não há frame no intervalo selecionado.")
        ok, content = cv2.imencode(".jpg", packet.image, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError("Falha ao gerar a prévia do vídeo.")
        return {"image": "data:image/jpeg;base64," + base64.b64encode(content.tobytes()).decode("ascii"),
                "width": packet.image.shape[1], "height": packet.image.shape[0],
                "video_duration": video_duration}
    finally:
        source.close()

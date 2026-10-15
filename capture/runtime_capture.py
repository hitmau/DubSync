from __future__ import annotations

import ctypes
import os
import threading
import time
import uuid
from collections import deque
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import mss
import numpy as np
import psutil
import cv2

from ..capture.models import FramePacket
from ..capture.session_manager import validate_regions
from ..database.store import DubSyncStore
from ..detector.visual import Region
from ..events.processor import DialogueProcessor
from ..ocr.engine import OcrEngine
from ..projects.context import reset_project_context, set_project_context
from ..projects.storage import ProjectStorage
from .runtime_windows import capture_window_client, client_window, visible_game_windows
from .windows_graphics_capture import (
    WindowsGraphicsCaptureStream,
    windows_graphics_capture_available,
)


class LiveScreenFrameSource:
    """Window-scoped live frame source. Pausing freezes the source clock."""

    def __init__(self, hwnd: int, regions: list[dict], fps: float = 5.0,
                 capture_backend: str = "legacy"):
        self.hwnd = int(hwnd)
        self.source_id = f"game-window-{self.hwnd}"
        self.is_live = True
        self.sample_fps = max(1.0, min(12.0, float(fps)))
        if capture_backend not in {"legacy", "windows_graphics_capture"}:
            raise ValueError("Backend de captura inválido.")
        self.capture_backend = capture_backend
        self.frames_read = 0
        self.frames_in_range = 0
        self.last_source_time = 0.0
        self.paused = False
        self._regions = list(regions)
        self._regions_lock = threading.RLock()
        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._clock_started = 0.0
        self._pause_started: float | None = None
        self._paused_seconds = 0.0
        self._recent: deque[FramePacket] = deque(maxlen=48)
        self._graphics_capture: WindowsGraphicsCaptureStream | None = None
        self._opened = False

    def open(self) -> None:
        if self._opened:
            return
        if os.name != "nt":
            raise RuntimeError("A Captura Real por janela do jogo está disponível no Windows.")
        if not client_window(self.hwnd):
            raise RuntimeError("A janela do jogo não está disponível. Traga o jogo para frente e tente novamente.")
        self._clock_started = time.monotonic()
        if self.capture_backend == "windows_graphics_capture":
            self._graphics_capture = WindowsGraphicsCaptureStream(self.hwnd, self.sample_fps)
            self._graphics_capture.start()
        self._opened = True

    def set_regions(self, regions: list[dict]) -> None:
        with self._regions_lock:
            self._regions = list(regions)

    def regions(self) -> list[dict]:
        with self._regions_lock:
            return [dict(item) for item in self._regions]

    def pause(self) -> None:
        if not self.paused:
            self._pause_started = time.monotonic()
            self.paused = True
            self._resume.clear()
            if self._graphics_capture:
                self._graphics_capture.set_paused(True)

    def resume(self) -> None:
        if self.paused:
            if self._pause_started is not None:
                self._paused_seconds += max(0.0, time.monotonic() - self._pause_started)
            self._pause_started = None
            self.paused = False
            self._resume.set()
            if self._graphics_capture:
                self._graphics_capture.set_paused(False)

    def stop(self) -> None:
        self._stop.set()
        self._resume.set()
        if self._graphics_capture:
            self._graphics_capture.stop()

    def _source_time(self) -> float:
        now = time.monotonic()
        paused = self._paused_seconds
        if self._pause_started is not None:
            paused += max(0.0, now - self._pause_started)
        return max(0.0, now - self._clock_started - paused)

    def capture_current_frame(self, screen=None) -> np.ndarray:
        if self.capture_backend == "windows_graphics_capture":
            if not self._graphics_capture:
                raise RuntimeError("A captura nativa ainda não foi iniciada.")
            image = self._graphics_capture.latest_frame()
            if image is None:
                raise RuntimeError("O Windows ainda não forneceu um frame da janela do jogo.")
            return self._client_area_frame(image)
        image = capture_window_client(self.hwnd)
        if image is not None:
            return image
        window = client_window(self.hwnd)
        if not window:
            raise RuntimeError("A janela do jogo foi fechada ou deixou de ser capturável.")
        if screen is None:
            with mss.mss() as desktop:
                shot = desktop.grab({key: int(window[key]) for key in ("left", "top", "width", "height")})
        else:
            shot = screen.grab({key: int(window[key]) for key in ("left", "top", "width", "height")})
        return np.asarray(shot, dtype=np.uint8)[:, :, :3].copy()

    def iter_frames(self):
        if self.capture_backend == "windows_graphics_capture":
            yield from self._iter_windows_graphics_capture()
            return
        with mss.mss() as screen:
            deadline = time.monotonic()
            while not self._stop.is_set():
                if not self._resume.wait(timeout=0.25):
                    continue
                if self._stop.is_set():
                    break
                window = client_window(self.hwnd)
                if not window:
                    raise RuntimeError("A janela do jogo foi fechada ou deixou de ser capturável.")
                rect = {key: int(window[key]) for key in ("left", "top", "width", "height")}
                if rect["width"] < 16 or rect["height"] < 16:
                    time.sleep(0.1)
                    continue
                image = self.capture_current_frame(screen)
                if image.shape[1] != rect["width"] or image.shape[0] != rect["height"]:
                    image = cv2.resize(image, (rect["width"], rect["height"]))
                now = time.monotonic()
                source_time = self._source_time()
                packet = FramePacket(
                    image=image, source_id=self.source_id, segment_id="live",
                    frame_id=self.frames_read + 1, source_time=source_time,
                    captured_at=datetime.now(timezone.utc), monotonic_ns=time.monotonic_ns(),
                    timing_quality="live_capture", timing_error_ms=0.0,
                )
                self.frames_read += 1
                self.last_source_time = source_time
                self._recent.append(packet)
                yield packet
                deadline = max(deadline + 1 / self.sample_fps, time.monotonic())
                self._stop.wait(max(0.0, deadline - time.monotonic()))

    def _iter_windows_graphics_capture(self):
        if not self._graphics_capture:
            raise RuntimeError("A captura nativa ainda não foi iniciada.")
        deadline = time.monotonic()
        while not self._stop.is_set():
            if not self._resume.wait(timeout=0.25):
                continue
            if self._stop.is_set():
                break
            window = client_window(self.hwnd)
            if not window:
                raise RuntimeError("A janela do jogo foi fechada ou deixou de ser capturável.")
            image = self._graphics_capture.latest_frame(copy=False)
            if image is None:
                self._graphics_capture.wait_for_frame(timeout=0.25)
                continue
            image = self._client_area_frame(image, window)
            source_time = self._source_time()
            packet = FramePacket(
                image=image, source_id=self.source_id, segment_id="live",
                frame_id=self.frames_read + 1, source_time=source_time,
                captured_at=datetime.now(timezone.utc), monotonic_ns=time.monotonic_ns(),
                timing_quality="live_capture", timing_error_ms=0.0,
            )
            self.frames_read += 1
            self.last_source_time = source_time
            self._recent.append(packet)
            yield packet
            deadline = max(deadline + 1 / self.sample_fps, time.monotonic())
            self._stop.wait(max(0.0, deadline - time.monotonic()))

    def _client_area_frame(self, image: np.ndarray, window: dict | None = None) -> np.ndarray:
        """Crop non-client borders only when the WGC frame matches the outer HWND bounds."""
        window = window or client_window(self.hwnd)
        if not window or os.name != "nt":
            return image
        rect = wintypes.RECT()
        try:
            if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(self.hwnd), ctypes.byref(rect)):
                return image
        except (AttributeError, OSError, ctypes.ArgumentError):
            return image
        outer_width = int(rect.right - rect.left)
        outer_height = int(rect.bottom - rect.top)
        if outer_width <= 0 or outer_height <= 0:
            return image
        scale_x = image.shape[1] / outer_width
        scale_y = image.shape[0] / outer_height
        # A client-only frame can also differ in scale from the client pixel size.
        # Only crop when both axes agree that this is the complete outer window.
        if abs(scale_x - scale_y) > max(scale_x, scale_y) * 0.03:
            return image
        client_width = int(window["width"])
        client_height = int(window["height"])
        if image.shape[1] <= client_width or image.shape[0] <= client_height:
            return image
        x0 = round((int(window["left"]) - int(rect.left)) * scale_x)
        y0 = round((int(window["top"]) - int(rect.top)) * scale_y)
        width = round(client_width * scale_x)
        height = round(client_height * scale_y)
        x1, y1 = min(image.shape[1], x0 + width), min(image.shape[0], y0 + height)
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            return image
        return image[y0:y1, x0:x1]

    def frame_near(self, source_time: float) -> FramePacket | None:
        if not self._recent:
            return None
        return min(self._recent, key=lambda item: abs(item.source_time - float(source_time)))

    def close(self) -> None:
        self.stop()
        self._recent.clear()


class RuntimeCaptureJob:
    def __init__(self, store: DubSyncStore, project_id: str, session_id: str,
                 hwnd: int, regions: list[dict], language: str,
                 session_dir: Path, settings: dict, on_complete: Callable | None = None):
        self.store = store
        self.project_id = project_id
        self.session_id = session_id
        self.hwnd = hwnd
        self.language = language
        self.session_dir = session_dir
        self.settings = settings
        self.source = LiveScreenFrameSource(hwnd, regions,
                                            settings.get("runtime_capture", {}).get("fps", 5),
                                            settings.get("runtime_capture", {}).get("capture_backend", "legacy"))
        self.processor: DialogueProcessor | None = None
        self.worker: threading.Thread | None = None
        self.progress: dict = {"stage": "preparing", "current_source_time": 0.0,
                               "frames_seen": 0, "frames_analyzed": 0, "ocr_calls": 0,
                               "events_created": 0, "activity_message": "Preparando a captura da janela do jogo."}
        self.error: str | None = None
        self.on_complete = on_complete
        self._lock = threading.RLock()
        self._next_duration_update = 0.0

    def launch(self) -> None:
        self.worker = threading.Thread(target=self._run, name=f"DubSyncGame-{self.session_id[:8]}", daemon=True)
        self.worker.start()

    def _report(self, progress: dict) -> None:
        with self._lock:
            self.progress = {**self.progress, **progress}
            self.progress.setdefault("current_source_time", self.source.last_source_time)
        current = float(self.progress.get("current_source_time") or 0.0)
        if current >= self._next_duration_update:
            self._next_duration_update = current + 5.0
            self.store.extend_session_duration(self.session_id, max(1.0, current + 1.0))

    def _run(self) -> None:
        token = set_project_context(self.project_id)
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            self.source.open()
            self.store.set_session_state(self.session_id, "running")
            regions = [Region.from_dict(item, index, 0.0)
                       for index, item in enumerate(self.source.regions())]
            self.processor = DialogueProcessor(
                self.store, self.session_id, self.session_dir, regions, self.language,
                OcrEngine(self.language, self.settings), self._report, self.settings,
                regions_provider=lambda: self.source.regions(),
            )
            result = self.processor.run(self.source)
            self.store.extend_session_duration(self.session_id, max(1.0, self.source.last_source_time))
            with self._lock:
                self.progress = {**self.progress, **result, "stage": "complete",
                                  "current_source_time": self.source.last_source_time,
                                  "activity_message": "Captura em tempo real encerrada; eventos e imagens salvos."}
            self.store.set_session_state(self.session_id, "complete")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            with self._lock:
                self.progress = {**self.progress, "stage": "failed", "activity_message": self.error}
            self.store.set_session_state(self.session_id, "failed", self.error)
        finally:
            self.source.close()
            reset_project_context(token)
            if self.on_complete:
                self.on_complete(self.session_id)

    def state(self) -> dict:
        with self._lock:
            return {"session_id": self.session_id,
                    "state": "stopping" if self.source._stop.is_set() and self.worker and self.worker.is_alive()
                    else ("paused" if self.source.paused and self.worker and self.worker.is_alive()
                          else ("running" if self.worker and self.worker.is_alive() else self.progress.get("stage", "stopped"))),
                    "progress": dict(self.progress), "error": self.error,
                    "window": client_window(self.hwnd), "regions": self.source.regions(),
                    "fps": self.source.sample_fps, "capture_backend": self.source.capture_backend}


class RuntimeCaptureManager:
    def __init__(self, store: DubSyncStore, data_dir: Path,
                 settings_provider: Callable[[], dict], project_storage: ProjectStorage,
                 on_complete: Callable | None = None):
        self.store = store
        self.data_dir = data_dir
        self.settings_provider = settings_provider
        self.project_storage = project_storage
        self.on_complete = on_complete
        self.jobs: dict[str, RuntimeCaptureJob] = {}
        self._lock = threading.RLock()

    def processes(self) -> list[dict]:
        own = {os.getpid()}
        try:
            own.update(child.pid for child in psutil.Process().children(recursive=True))
        except (psutil.Error, OSError):
            pass
        result = []
        for item in visible_game_windows(own):
            result.append(item)
        return result[:100]

    def start(self, project_id: str, hwnd: int, regions: list[dict], language: str,
              fps: float = 5.0, overlay_mode: str = "python",
              capture_backend: str = "legacy") -> dict:
        with self._lock:
            if any(job.worker and job.worker.is_alive() for job in self.jobs.values()):
                raise ValueError("Já existe uma Captura Real ativa. Encerre-a antes de iniciar outra.")
        window = client_window(hwnd)
        if not window:
            raise ValueError("Selecione uma janela de jogo aberta e visível.")
        pid = int(window["pid"])
        try:
            process = psutil.Process(pid)
            process_name = process.name()
        except psutil.Error as exc:
            raise ValueError("O processo do jogo não está mais disponível.") from exc
        cleaned_regions = validate_regions(regions)
        if not cleaned_regions:
            raise ValueError("Adicione ao menos uma ROI ativa antes de iniciar.")
        if not any(item.get("enabled", True) for item in cleaned_regions):
            raise ValueError("Mantenha ao menos uma ROI de diálogo ativa.")
        if overlay_mode not in {"python", "native"}:
            raise ValueError("Modo de overlay inválido.")
        if capture_backend not in {"legacy", "windows_graphics_capture"}:
            raise ValueError("Backend de captura inválido.")
        if capture_backend == "windows_graphics_capture" and not windows_graphics_capture_available():
            raise RuntimeError(
                "Windows Graphics Capture não está disponível neste serviço. "
                "Execute setup.ps1 e reinicie o serviço quando for seguro."
            )
        session_id = str(uuid.uuid4())
        with self.store.Session() as db:
            from ..database.models import DubbingProject
            project = db.get(DubbingProject, project_id)
            if not project:
                raise KeyError(project_id)
            slug = project.slug
        session_dir = self.project_storage.capture_session_dir(slug, session_id)
        config = {"mode": "game_live", "hwnd": int(hwnd), "pid": pid,
                  "process_name": process_name, "window_title": str(window.get("title", "")),
                  "regions": cleaned_regions, "duration": 0, "start": 0,
                  "asset_root": f"projects/{slug}/capture/sessions/{session_id}",
                  "video_only": True,
                  "runtime_overlay": overlay_mode,
                  "capture_backend": capture_backend,
                  "processing_settings": self.settings_provider(),
                  "runtime_capture": {"fps": max(1.0, min(12.0, float(fps))),
                                      "capture_backend": capture_backend}}
        self.store.create_session(session_id, "game", f"game://{pid}/{hwnd}", 0, 0,
                                  language, config, state="preparing")
        job = RuntimeCaptureJob(self.store, project_id, session_id, int(hwnd), cleaned_regions,
                                language, session_dir, {**config["processing_settings"],
                                                       "runtime_capture": config["runtime_capture"]},
                                on_complete=self._finished)
        with self._lock:
            self.jobs[session_id] = job
        job.launch()
        return {"session_id": session_id, "state": "preparing", "config_json": config}

    def _finished(self, session_id: str) -> None:
        # Keep the latest jobs queryable until a later start so the UI can render final status.
        if self.on_complete:
            self.on_complete(session_id)

    def get(self, session_id: str) -> RuntimeCaptureJob | None:
        with self._lock:
            return self.jobs.get(session_id)

    def pause(self, session_id: str) -> dict:
        job = self.get(session_id)
        if not job or not job.worker or not job.worker.is_alive():
            raise KeyError(session_id)
        job.source.pause()
        self.store.set_session_state(session_id, "paused")
        return job.state()

    def resume(self, session_id: str) -> dict:
        job = self.get(session_id)
        if not job or not job.worker or not job.worker.is_alive():
            raise KeyError(session_id)
        job.source.resume()
        self.store.set_session_state(session_id, "running")
        return job.state()

    def stop(self, session_id: str) -> dict:
        job = self.get(session_id)
        if not job or not job.worker or not job.worker.is_alive():
            raise KeyError(session_id)
        job.source.stop()
        self.store.set_session_state(session_id, "stopping")
        return job.state()

    def update_regions(self, session_id: str, regions: list[dict]) -> dict:
        job = self.get(session_id)
        if not job or not job.worker or not job.worker.is_alive():
            raise KeyError(session_id)
        cleaned = validate_regions(regions)
        if not cleaned:
            raise ValueError("Mantenha ao menos uma ROI ativa.")
        if not any(item.get("enabled", True) for item in cleaned):
            raise ValueError("Mantenha ao menos uma ROI de diálogo ativa.")
        job.source.set_regions(cleaned)
        session = self.store.session(session_id) or {}
        config = dict(session.get("config_json") or {})
        config["regions"] = cleaned
        self.store.update_session_config(session_id, config)
        return job.state()


def list_game_windows(excluded_pids: set[int] | None = None) -> list[dict]:
    return visible_game_windows(excluded_pids or set())

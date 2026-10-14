from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import cv2
import mss
import numpy as np
import psutil


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def find_game_process(executable: Path) -> psutil.Process | None:
    """Prefer an exact executable match; fall back to the unique matching name."""
    named = []
    for process in psutil.process_iter(["name", "exe", "create_time"]):
        try:
            info = process.info
            if (info.get("name") or "").casefold() != executable.name.casefold():
                continue
            path = info.get("exe")
            if path and _same_path(path, executable):
                return process
            if not path:
                named.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return named[0] if len(named) == 1 else None


def _window_for_pid(pid: int) -> dict | None:
    if os.name != "nt":
        return None
    user32 = ctypes.windll.user32
    windows = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _param):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width > 100 and height > 100:
            windows.append({"handle": int(hwnd), "title": title.value,
                            "left": rect.left, "top": rect.top, "width": width, "height": height})
        return True

    user32.EnumWindows(callback_type(visit), 0)
    if not windows:
        return None
    window = max(windows, key=lambda item: item["width"] * item["height"])
    foreground = user32.GetForegroundWindow()
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(foreground, ctypes.byref(owner))
    window["foreground"] = owner.value == pid
    return window


class RuntimeObserver:
    """Attach or launch, observe a visible game window, and save local evidence."""

    def __init__(self, executable: str | Path, output_root: str | Path,
                 static_report_id: str | None = None, ocr_language: str = "pt"):
        self.executable = Path(executable).expanduser().resolve(strict=True)
        if not self.executable.is_file() or self.executable.suffix.casefold() != ".exe":
            raise ValueError("Selecione o executável do jogo antes de observar.")
        self.output_root = Path(output_root).resolve()
        self.session_id = "OBS_" + uuid.uuid4().hex[:12].upper()
        self.session_dir = self.output_root / self.session_id
        self.report_path = self.session_dir / "observation.json"
        self.static_report_id = static_report_id
        self.ocr_language = ocr_language
        self.stop_event = threading.Event()
        self.capture_event = threading.Event()
        self._manual_capture_after = 0.0
        self.process: psutil.Process | None = None
        self._ocr = None
        self._last_text = ""
        self.report = {
            "schema_version": 1, "kind": "runtime_observation", "session_id": self.session_id,
            "static_report_id": static_report_id, "executable_path": str(self.executable),
            "started_at": _now(), "finished_at": None, "launch_mode": None,
            "pid": None, "process_start_time": None, "modules": [], "windows": [],
            "visual_observations": [], "warnings": [],
            "runtime_hook_confirmed": False,
            "note": "OCR de pixels visíveis não confirma callback ou ID interno de fala.",
        }

    def request_capture(self) -> None:
        # Clicking the scanner steals focus; allow time to return to the game.
        self._manual_capture_after = time.monotonic() + 3.0
        self.capture_event.set()

    def stop(self) -> None:
        self.stop_event.set()

    def _save(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.report_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.report_path)

    def _attach_or_launch(self, emit: Callable[[str, object], None]) -> psutil.Process:
        existing = find_game_process(self.executable)
        if existing:
            self.report["launch_mode"] = "attached"
            return existing
        subprocess.Popen([str(self.executable)], cwd=str(self.executable.parent),
                         close_fds=True)
        self.report["launch_mode"] = "launched"
        emit("status", "Jogo iniciado; aguardando o processo e a janela…")
        for _ in range(60):
            if self.stop_event.wait(0.5):
                raise RuntimeError("Observação cancelada antes de o jogo abrir.")
            process = find_game_process(self.executable)
            if process:
                return process
        raise RuntimeError("O jogo não apareceu na lista de processos em 30 segundos.")

    def _modules(self, process: psutil.Process) -> list[str]:
        try:
            paths = {entry.path for entry in process.memory_maps(grouped=True)
                     if entry.path and entry.path.casefold().endswith((".dll", ".exe"))}
            return sorted(paths, key=str.casefold)[:300]
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as error:
            self.report["warnings"].append(f"Módulos não acessíveis: {type(error).__name__}.")
            return []

    def _capture(self, window: dict, manual: bool, emit: Callable[[str, object], None]) -> None:
        if not window.get("foreground"):
            if manual:
                emit("status", "Traga a janela do jogo para frente e tente Registrar fala novamente.")
            return
        with mss.mss() as screen:
            shot = screen.grab({key: window[key] for key in ("left", "top", "width", "height")})
            image = np.asarray(shot)[:, :, :3].copy()
        # Dialogue placement differs by game: Hollow Knight shows NPC text at
        # the top and speaker names below. Inspect the entire visible window.
        text_lines = []
        try:
            if self._ocr is not False:
                if self._ocr is None:
                    from ocr.engine import OcrEngine
                    self._ocr = OcrEngine(self.ocr_language, {"preprocess": {"mode": "fast"}})
                text_lines = self._ocr.recognize(image)
        except Exception as error:
            self._ocr = False
            message = f"OCR indisponível: {type(error).__name__}: {error}"
            if message not in self.report["warnings"]:
                self.report["warnings"].append(message)
            emit("status", message[:160])
        text = " ".join(line.text for line in text_lines if line.confidence >= 0.45).strip()
        if not manual and (not text or text.casefold() == self._last_text.casefold()):
            return
        if not manual and len(self.report["visual_observations"]) >= 50:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"frame_{stamp}.jpg"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        frame_path = self.session_dir / filename
        if not cv2.imwrite(str(frame_path), image, [cv2.IMWRITE_JPEG_QUALITY, 82]):
            raise OSError("Não foi possível salvar o frame capturado.")
        observation = {"at": _now(), "source": "manual" if manual else "automatic_ocr",
                       "window_title": window["title"], "frame_path": str(frame_path),
                       "ocr_text_candidate": text,
                       "ocr_lines": [{"text": line.text, "confidence": round(line.confidence, 3),
                                      "bbox": list(line.bbox)}
                                     for line in text_lines[:30]]}
        self.report["visual_observations"].append(observation)
        self._last_text = text
        self._save()
        emit("observation", observation)

    def run(self, emit: Callable[[str, object], None]) -> Path:
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._save()
        try:
            self.process = self._attach_or_launch(emit)
            process = self.process
            self.report["pid"] = process.pid
            try:
                self.report["process_start_time"] = datetime.fromtimestamp(
                    process.create_time(), timezone.utc).isoformat()
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                pass
            self.report["modules"] = self._modules(process)
            self._save()
            emit("ready", {"pid": process.pid, "mode": self.report["launch_mode"],
                           "report_path": str(self.report_path)})
            last_window = None
            next_capture = time.monotonic() + 5
            while not self.stop_event.is_set() and process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                window = _window_for_pid(process.pid)
                if window and (last_window is None or window != last_window):
                    self.report["windows"].append({"at": _now(), **window})
                    self.report["windows"] = self.report["windows"][-100:]
                    self._save()
                    emit("window", window)
                    last_window = window
                manual = self.capture_event.is_set() and time.monotonic() >= self._manual_capture_after
                if manual:
                    self.capture_event.clear()
                due = time.monotonic() >= next_capture
                if window and (manual or due):
                    self._capture(window, manual, emit)
                    next_capture = time.monotonic() + 5
                self.stop_event.wait(0.5)
            emit("status", "Jogo fechado ou observação encerrada.")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as error:
            self.report["warnings"].append(f"Processo indisponível: {type(error).__name__}.")
            emit("status", "O processo do jogo deixou de estar acessível.")
        except Exception as error:
            self.report["warnings"].append(f"Observação interrompida: {type(error).__name__}: {error}")
            emit("error", str(error))
        finally:
            self.report["finished_at"] = _now()
            self._save()
            emit("end", str(self.report_path))
        return self.report_path

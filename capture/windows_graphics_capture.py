from __future__ import annotations

import importlib
import threading
import time
from typing import Any

import numpy as np


def _load_capture_type():
    try:
        module = importlib.import_module("windows_capture")
        return module.WindowsCapture
    except Exception as exc:
        raise RuntimeError(
            "A captura nativa do Windows não está instalada neste serviço. "
            "Execute setup.ps1 para instalar as dependências e reinicie o serviço quando a sessão atual puder ser encerrada."
        ) from exc


def windows_graphics_capture_available() -> bool:
    try:
        _load_capture_type()
        return True
    except RuntimeError:
        return False


class WindowsGraphicsCaptureStream:
    """Keeps only the newest HWND frame from Windows Graphics Capture."""

    def __init__(self, hwnd: int, fps: float = 5.0, capture_type=None):
        self.hwnd = int(hwnd)
        self.fps = max(1.0, min(12.0, float(fps)))
        self._capture_type = capture_type
        self._condition = threading.Condition()
        self._capture: Any = None
        self._control: Any = None
        self._latest: np.ndarray | None = None
        self._generation = 0
        self._last_copy_at = 0.0
        self._paused = False
        self._stopping = False
        self._closed = False
        self._error: Exception | None = None

    def start(self, timeout: float = 8.0) -> None:
        capture_type = self._capture_type or _load_capture_type()
        interval_ms = max(1, round(1000 / self.fps))
        capture = capture_type(
            cursor_capture=False,
            draw_border=False,
            secondary_window=False,
            minimum_update_interval=interval_ms,
            dirty_region=False,
            monitor_index=None,
            window_name=None,
            window_hwnd=self.hwnd,
        )
        self._capture = capture

        @capture.event
        def on_frame_arrived(frame, capture_control):
            now = time.monotonic()
            with self._condition:
                minimum_interval = 1.0 if self._paused else 1 / self.fps
                if self._stopping or now - self._last_copy_at < minimum_interval:
                    return
                self._last_copy_at = now
            try:
                convert = getattr(frame, "convert_to_bgr", None) or getattr(frame, "to_bgr", None)
                if convert is None:
                    raise RuntimeError("A versão instalada da captura nativa não fornece conversão BGR.")
                converted = convert()
                image = np.asarray(converted.frame_buffer, dtype=np.uint8).copy()
                if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
                    raise RuntimeError("O Windows Graphics Capture retornou um frame inválido.")
            except Exception as exc:
                with self._condition:
                    self._error = exc
                    self._condition.notify_all()
                try:
                    capture_control.stop()
                except Exception:
                    pass
                return
            with self._condition:
                if self._stopping:
                    return
                self._latest = image
                self._generation += 1
                self._condition.notify_all()

        @capture.event
        def on_closed():
            with self._condition:
                self._closed = True
                self._condition.notify_all()

        try:
            self._control = capture.start_free_threaded()
        except Exception as exc:
            self._fail_and_stop(exc)
            raise RuntimeError(f"Não foi possível iniciar Windows Graphics Capture: {exc}") from exc

        with self._condition:
            ready = self._condition.wait_for(
                lambda: self._latest is not None or self._error is not None or self._closed or self._stopping,
                timeout=max(0.1, float(timeout)),
            )
            error = self._error
            closed = self._closed
            has_frame = self._latest is not None
        if error:
            self.stop()
            raise RuntimeError(f"Falha ao obter frame nativo do jogo: {error}") from error
        if closed and not has_frame:
            self.stop()
            raise RuntimeError("A janela do jogo foi fechada antes de fornecer um frame.")
        if not ready or not has_frame:
            self.stop()
            raise RuntimeError("O Windows não forneceu um frame da janela do jogo no tempo esperado.")

    def _fail_and_stop(self, error: Exception) -> None:
        with self._condition:
            self._error = error
            self._stopping = True
            self._condition.notify_all()
        control = self._control
        if control:
            try:
                control.stop()
            except Exception:
                pass

    def latest_frame(self, copy: bool = True) -> np.ndarray | None:
        with self._condition:
            if self._error:
                raise RuntimeError(f"Falha na captura nativa: {self._error}") from self._error
            if self._closed and not self._stopping:
                raise RuntimeError("A janela do jogo foi fechada durante a captura.")
            image = self._latest
            if image is None:
                return None
            return image.copy() if copy else image

    def wait_for_frame(self, timeout: float) -> bool:
        with self._condition:
            self._condition.wait_for(
                lambda: self._latest is not None or self._error is not None or self._closed or self._stopping,
                timeout=max(0.0, float(timeout)),
            )
            if self._error:
                raise RuntimeError(f"Falha na captura nativa: {self._error}") from self._error
            if self._closed and not self._stopping:
                raise RuntimeError("A janela do jogo foi fechada durante a captura.")
            return self._latest is not None

    def set_paused(self, paused: bool) -> None:
        with self._condition:
            self._paused = bool(paused)
            if not paused:
                self._last_copy_at = 0.0

    def stop(self) -> None:
        with self._condition:
            if self._stopping:
                control = self._control
            else:
                self._stopping = True
                control = self._control
                self._condition.notify_all()
        if control:
            try:
                control.stop()
            except Exception:
                pass

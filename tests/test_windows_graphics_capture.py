from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Dupsync.capture.runtime_capture import LiveScreenFrameSource
from Dupsync.capture.windows_graphics_capture import WindowsGraphicsCaptureStream


class _FakeFrame:
    def __init__(self, pixels: np.ndarray):
        self.frame_buffer = pixels

    def convert_to_bgr(self):
        return self


class _FakeControl:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeCapture:
    def __init__(self, *, pixels: np.ndarray, **_kwargs):
        self.handlers = {}
        self.control = _FakeControl()
        self.pixels = pixels

    def event(self, handler):
        self.handlers[handler.__name__] = handler
        return handler

    def start_free_threaded(self):
        self.handlers["on_frame_arrived"](_FakeFrame(self.pixels), self.control)
        return self.control


class _FakeStream:
    def __init__(self, _hwnd, _fps):
        self.image = np.full((7, 11, 3), 91, dtype=np.uint8)
        self.paused = False
        self.stopped = False

    def start(self):
        pass

    def latest_frame(self, copy=True):
        return self.image.copy() if copy else self.image

    def wait_for_frame(self, _timeout):
        return True

    def set_paused(self, value):
        self.paused = value

    def stop(self):
        self.stopped = True


class WindowsGraphicsCaptureTests(unittest.TestCase):
    def test_latest_frame_is_copied_from_native_buffer_and_stops_cleanly(self):
        pixels = np.full((7, 11, 3), 27, dtype=np.uint8)
        capture = _FakeCapture(pixels=pixels)
        stream = WindowsGraphicsCaptureStream(1234, 5, capture_type=lambda **kwargs: capture)

        stream.start(timeout=0.2)
        snapshot = stream.latest_frame()
        pixels[:] = 0

        self.assertEqual(snapshot.shape, (7, 11, 3))
        self.assertTrue(np.all(snapshot == 27))
        stream.stop()
        self.assertTrue(capture.control.stopped)

    def test_capture_backend_is_explicit_and_keeps_legacy_default(self):
        legacy = LiveScreenFrameSource(10, [], fps=5)
        native = LiveScreenFrameSource(10, [], fps=5, capture_backend="windows_graphics_capture")

        self.assertEqual(legacy.capture_backend, "legacy")
        self.assertEqual(native.capture_backend, "windows_graphics_capture")
        with self.assertRaisesRegex(ValueError, "Backend de captura inválido"):
            LiveScreenFrameSource(10, [], capture_backend="unknown")

    @patch("Dupsync.capture.runtime_capture.os.name", "nt")
    @patch("Dupsync.capture.runtime_capture.client_window", return_value={
        "left": 0, "top": 0, "width": 11, "height": 7,
    })
    @patch("Dupsync.capture.runtime_capture.WindowsGraphicsCaptureStream", _FakeStream)
    @patch.object(LiveScreenFrameSource, "_client_area_frame", lambda self, image, window=None: image)
    def test_live_source_emits_native_frames_through_existing_packet_contract(self, _window):
        source = LiveScreenFrameSource(1234, [], fps=5, capture_backend="windows_graphics_capture")
        source.open()
        packet = next(source.iter_frames())

        self.assertEqual(packet.image.shape, (7, 11, 3))
        self.assertEqual(packet.source_id, "game-window-1234")
        self.assertEqual(packet.timing_quality, "live_capture")
        source.close()

    @patch("Dupsync.capture.windows_graphics_capture.importlib.import_module",
           side_effect=ModuleNotFoundError("windows_capture"))
    def test_missing_native_package_reports_setup_instructions(self, _import):
        stream = WindowsGraphicsCaptureStream(1234)

        with self.assertRaisesRegex(RuntimeError, "Execute setup.ps1"):
            stream.start(timeout=0.1)


if __name__ == "__main__":
    unittest.main()

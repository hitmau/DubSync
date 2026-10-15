from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Dupsync.capture.runtime_capture import LiveScreenFrameSource, live_capture_processing_settings
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
    def __init__(self, _hwnd, fps):
        self.fps = fps
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

    def test_native_stream_reports_observed_copy_rate(self):
        pixels = np.full((7, 11, 3), 27, dtype=np.uint8)
        capture = _FakeCapture(pixels=pixels)
        stream = WindowsGraphicsCaptureStream(1234, 60, capture_type=lambda **kwargs: capture)

        stream.start(timeout=0.2)
        time.sleep(0.05)
        capture.handlers["on_frame_arrived"](_FakeFrame(pixels), capture.control)

        self.assertGreater(stream.observed_fps, 0)
        stream.stop()

    def test_capture_backend_is_explicit_and_keeps_legacy_default(self):
        legacy = LiveScreenFrameSource(10, [], fps=5)
        native = LiveScreenFrameSource(10, [], fps=5, capture_backend="windows_graphics_capture")

        self.assertEqual(legacy.capture_backend, "legacy")
        self.assertEqual(native.capture_backend, "windows_graphics_capture")
        with self.assertRaisesRegex(ValueError, "Backend de captura inválido"):
            LiveScreenFrameSource(10, [], capture_backend="unknown")

    def test_capture_and_analysis_rates_are_independent_and_backward_compatible(self):
        source = LiveScreenFrameSource(
            10, [], fps=10, capture_backend="windows_graphics_capture", capture_fps=30,
        )

        self.assertEqual(source.sample_fps, 10)
        self.assertEqual(source.capture_fps, 30)
        legacy = LiveScreenFrameSource(10, [], fps=5)
        self.assertEqual(legacy.sample_fps, legacy.capture_fps)

    def test_live_capture_disables_periodic_ocr_without_mutating_global_settings(self):
        settings = {"detection": {"periodic_ocr_seconds": 1.25, "analysis_fps": 8},
                    "ocr": {"min_confidence": 0.4}}

        live_settings = live_capture_processing_settings(settings, analysis_fps=10)

        self.assertEqual(live_settings["detection"]["periodic_ocr_seconds"], 0)
        self.assertEqual(live_settings["detection"]["analysis_fps"], 10)
        self.assertEqual(live_settings["ocr"]["min_confidence"], 0.4)
        self.assertEqual(settings["detection"]["periodic_ocr_seconds"], 1.25)
        self.assertEqual(settings["detection"]["analysis_fps"], 8)

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
        self.assertEqual(source._graphics_capture.fps, 5)
        source.close()

    @patch("Dupsync.capture.runtime_capture.os.name", "nt")
    @patch("Dupsync.capture.runtime_capture.client_window", return_value={
        "left": 0, "top": 0, "width": 11, "height": 7,
    })
    @patch("Dupsync.capture.runtime_capture.WindowsGraphicsCaptureStream", _FakeStream)
    def test_native_stream_receives_capture_rate_separate_from_analysis_rate(self, _window):
        source = LiveScreenFrameSource(
            1234, [], fps=10, capture_backend="windows_graphics_capture", capture_fps=30,
        )

        source.open()

        self.assertEqual(source._graphics_capture.fps, 30)
        self.assertEqual(source.sample_fps, 10)
        source.close()

    @patch("Dupsync.capture.windows_graphics_capture.importlib.import_module",
           side_effect=ModuleNotFoundError("windows_capture"))
    def test_missing_native_package_reports_setup_instructions(self, _import):
        stream = WindowsGraphicsCaptureStream(1234)

        with self.assertRaisesRegex(RuntimeError, "Execute setup.ps1"):
            stream.start(timeout=0.1)


if __name__ == "__main__":
    unittest.main()

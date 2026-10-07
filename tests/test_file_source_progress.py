from __future__ import annotations

import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from Dupsync.capture.file_source import FileVideoSource


class _CaptureMetadata:
    def __init__(self, *_args, **_kwargs):
        self.released = False

    def isOpened(self):
        return True

    def get(self, property_id):
        if property_id == 5:  # cv2.CAP_PROP_FPS
            return 10.0
        if property_id == 7:  # cv2.CAP_PROP_FRAME_COUNT
            return 30
        return 0

    def release(self):
        self.released = True


class _Vp9Metadata(_CaptureMetadata):
    def get(self, property_id):
        if property_id == cv2.CAP_PROP_FOURCC:
            return cv2.VideoWriter_fourcc(*"VP90")
        return super().get(property_id)


class _IndexedCapture:
    def __init__(self, inaccurate_seek=False):
        self.index = 0
        self.reported_index = 0
        self.reads = 0
        self.grabs = 0
        self.inaccurate_seek = inaccurate_seek

    def isOpened(self):
        return True

    def set(self, property_id, value):
        if property_id == cv2.CAP_PROP_POS_FRAMES:
            self.reported_index = int(value)
            self.index = 0 if self.inaccurate_seek else int(value)
        return True

    def get(self, property_id):
        if property_id == cv2.CAP_PROP_POS_FRAMES:
            return self.reported_index
        if property_id == cv2.CAP_PROP_POS_MSEC:
            return max(0, self.index - 1) * 100
        return 0

    def read(self):
        if self.index >= 30:
            return False, None
        frame = np.full((2, 2, 3), self.index, dtype=np.uint8)
        self.index += 1
        self.reported_index += 1
        self.reads += 1
        return True, frame

    def grab(self):
        if self.index >= 30:
            return False
        self.index += 1
        self.reported_index += 1
        self.grabs += 1
        return True

    def release(self):
        pass


class _ProbeProcess:
    def __init__(self, *_args, **_kwargs):
        self.stdout = io.StringIO("".join(f"{index / 10:.3f}\n" for index in range(30)))
        self.killed = False

    def wait(self):
        return 0

    def kill(self):
        self.killed = True


class _SeekCapture:
    def __init__(self, *_args, **_kwargs):
        self.released = False
        self.seek_ms = None

    def isOpened(self):
        return True

    def set(self, property_id, value):
        if property_id == cv2.CAP_PROP_POS_MSEC:
            self.seek_ms = value
        return True

    def read(self):
        return True, np.zeros((2, 2, 3), dtype=np.uint8)

    def get(self, property_id):
        if property_id == cv2.CAP_PROP_POS_FRAMES:
            return 13
        if property_id == cv2.CAP_PROP_POS_MSEC:
            return 1250.0
        return 0

    def release(self):
        self.released = True


class _WindowCapture:
    instances = []

    def __init__(self, *_args, **_kwargs):
        self.times = [9.9, 10.0, 10.1, 10.2, 10.3, 10.4]
        self.index = 0
        self.reads = 0
        self.released = False
        self.instances.append(self)

    def isOpened(self):
        return True

    def set(self, property_id, value):
        if property_id == cv2.CAP_PROP_POS_MSEC:
            self.index = min(range(len(self.times)),
                             key=lambda index: abs(self.times[index] * 1000 - value))
        return True

    def read(self):
        if self.index >= len(self.times):
            return False, None
        self.reads += 1
        self.index += 1
        return True, np.zeros((2, 2, 3), dtype=np.uint8)

    def get(self, property_id):
        if property_id == cv2.CAP_PROP_FPS:
            return 10.0
        if property_id == cv2.CAP_PROP_POS_FRAMES:
            return self.index
        if property_id == cv2.CAP_PROP_POS_MSEC:
            return self.times[self.index - 1] * 1000
        return 0

    def release(self):
        self.released = True


class FileSourceProgressTests(unittest.TestCase):
    def test_persistent_pause_stops_at_next_frame_without_losing_position(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "synthetic.webm"
            video.write_bytes(b"test")
            pause = threading.Event()
            with patch("Dupsync.capture.file_source.cv2.VideoCapture",
                       side_effect=[_Vp9Metadata(), _IndexedCapture()]), \
                 patch("Dupsync.capture.file_source.ffmpeg_executable", return_value="ffprobe"), \
                 patch("Dupsync.capture.file_source.subprocess.Popen", side_effect=_ProbeProcess):
                source = FileVideoSource(video, "test", start=0, duration=3,
                                         pause_requested=pause, stop_on_pause=True)
                packets = source.iter_frames()
                first = next(packets)
                pause.set()
                self.assertEqual(list(packets), [])
                source.close()
            self.assertEqual(first.source_time, 0.0)
            self.assertTrue(source.paused)
            self.assertEqual(source.last_source_time, 0.0)

    def test_unreliable_packet_timestamps_fall_back_to_decoded_frame_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "synthetic.webm"
            video.write_bytes(b"test")
            commands = []

            def probe(command, **kwargs):
                commands.append(command)
                process = _ProbeProcess(command, **kwargs)
                if "packet=pts_time" in command:
                    process.stdout = io.StringIO("0.000\nN/A\n0.200\n")
                return process

            with patch("Dupsync.capture.file_source.cv2.VideoCapture",
                       side_effect=[_Vp9Metadata(), _IndexedCapture()]), \
                 patch("Dupsync.capture.file_source.ffmpeg_executable", return_value="ffprobe"), \
                 patch("Dupsync.capture.file_source.subprocess.Popen", side_effect=probe):
                source = FileVideoSource(video, "test", start=0, duration=1)
                source.open()
                source.close()
            self.assertIn("packet=pts_time", commands[0])
            self.assertIn("frame=best_effort_timestamp_time", commands[1])
            self.assertEqual(source.frames_in_range, 10)

    def test_sampling_keeps_exact_inspection_times_and_tracks_all_frames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "synthetic.webm"
            video.write_bytes(b"test")
            decoder = _IndexedCapture()
            with patch("Dupsync.capture.file_source.cv2.VideoCapture",
                       side_effect=[_Vp9Metadata(), decoder]), \
                 patch("Dupsync.capture.file_source.ffmpeg_executable", return_value="ffprobe"), \
                 patch("Dupsync.capture.file_source.subprocess.Popen", side_effect=_ProbeProcess):
                source = FileVideoSource(video, "test", start=0, duration=3)
                source.sample_fps = 2
                packets = list(source.iter_frames())
                source.close()
            self.assertEqual([packet.frame_id for packet in packets], [0, 5, 10, 15, 20, 25])
            self.assertEqual([packet.source_time for packet in packets], [0, 0.5, 1.0, 1.5, 2.0, 2.5])
            self.assertEqual(source.frames_read, 30)
            self.assertEqual(source.last_source_time, 2.9)
            self.assertEqual(decoder.reads, 6)
            self.assertEqual(decoder.grabs, 24)

    def test_vp9_indexes_packets_and_seeks_without_decoding_earlier_frames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "synthetic.webm"
            video.write_bytes(b"test")
            decoder = _IndexedCapture()
            commands = []

            def probe(command, **kwargs):
                commands.append(command)
                return _ProbeProcess(command, **kwargs)

            with patch("Dupsync.capture.file_source.cv2.VideoCapture",
                       side_effect=[_Vp9Metadata(), decoder]), \
                 patch("Dupsync.capture.file_source.ffmpeg_executable", return_value="ffprobe"), \
                 patch("Dupsync.capture.file_source.subprocess.Popen", side_effect=probe):
                source = FileVideoSource(video, "test", start=2, duration=0.5)
                packets = list(source.iter_frames())
                source.close()

            self.assertIn("packet=pts_time", commands[0])
            self.assertEqual(source.frames_in_range, 5)
            self.assertEqual([packet.frame_id for packet in packets], [20, 21, 22, 23, 24])
            self.assertEqual([packet.source_time for packet in packets], [2.0, 2.1, 2.2, 2.3, 2.4])
            self.assertEqual([int(packet.image[0, 0, 0]) for packet in packets], [20, 21, 22, 23, 24])
            self.assertLess(decoder.reads, 20)

    def test_inaccurate_seek_returns_to_sequential_decode_before_yielding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "synthetic.webm"
            video.write_bytes(b"test")
            inaccurate = _IndexedCapture(inaccurate_seek=True)
            sequential = _IndexedCapture()
            with patch("Dupsync.capture.file_source.cv2.VideoCapture",
                       side_effect=[_Vp9Metadata(), inaccurate, sequential]), \
                 patch("Dupsync.capture.file_source.ffmpeg_executable", return_value="ffprobe"), \
                 patch("Dupsync.capture.file_source.subprocess.Popen", side_effect=_ProbeProcess):
                source = FileVideoSource(video, "test", start=2, duration=0.5)
                packets = list(source.iter_frames())
                source.close()
            self.assertEqual(inaccurate.reads, 1)
            self.assertEqual([int(packet.image[0, 0, 0]) for packet in packets], [20, 21, 22, 23, 24])
            self.assertGreater(sequential.reads + sequential.grabs, 20)

    def test_nearby_review_frames_use_one_seek_and_nearest_pts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "synthetic.mp4"
            video.write_bytes(b"test")
            _WindowCapture.instances.clear()
            with patch("Dupsync.capture.file_source.cv2.VideoCapture", side_effect=_WindowCapture), \
                 patch("Dupsync.capture.file_source.ffmpeg_executable", side_effect=AssertionError("must not index")):
                packets = FileVideoSource(video, "test").frames_near_window([10.0, 10.12, 10.28])
            self.assertEqual(len(_WindowCapture.instances), 1)
            self.assertTrue(_WindowCapture.instances[0].released)
            self.assertEqual([round(packets[target].source_time, 2) for target in (10.0, 10.12, 10.28)],
                             [10.0, 10.1, 10.3])
            self.assertEqual(_WindowCapture.instances[0].reads, 4)

    def test_frame_near_without_open_seeks_directly_without_timestamp_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "synthetic.mp4"
            video.write_bytes(b"test")
            with patch("Dupsync.capture.file_source.cv2.VideoCapture", side_effect=_SeekCapture), \
                 patch("Dupsync.capture.file_source.ffmpeg_executable", side_effect=AssertionError("must not index")):
                packet = FileVideoSource(video, "test").frame_near(1.24)
                self.assertIsNotNone(packet)
                self.assertEqual(packet.source_time, 1.25)
                self.assertEqual(packet.timing_quality, "opencv_seek")
                self.assertAlmostEqual(packet.timing_error_ms, 10.0)

    def test_open_reports_timestamp_index_and_exact_frame_count_for_range(self):
        updates = []
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "synthetic.mp4"
            video.write_bytes(b"test")
            with patch("Dupsync.capture.file_source.cv2.VideoCapture", side_effect=_CaptureMetadata), \
                 patch("Dupsync.capture.file_source.ffmpeg_executable", return_value="ffprobe"), \
                 patch("Dupsync.capture.file_source.subprocess.Popen", side_effect=_ProbeProcess):
                source = FileVideoSource(video, "test", start=1, duration=1, progress_callback=updates.append)
                source.open()
                self.assertEqual(source.frames_in_range, 10)
                self.assertEqual(len(source._frames), 30)
                self.assertEqual(updates[0]["stage"], "indexing_timestamps")
                self.assertEqual(updates[0]["estimated_index_frames"], 20)
                self.assertEqual(updates[-1]["stage"], "analysis")
                self.assertEqual(updates[-1]["frames_total"], 10)
                self.assertEqual(updates[-1]["index_progress_percent"], 100.0)
                source.close()


if __name__ == "__main__":
    unittest.main()

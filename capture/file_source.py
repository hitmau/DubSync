from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from bisect import bisect_left
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from .models import FramePacket


def ffmpeg_executable(name: str = "ffmpeg") -> str:
    import shutil

    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} não foi encontrado no PATH.")
    return path


def probe_video_duration(path: str | Path) -> float:
    """Return container duration using ffprobe without opening or decoding audio."""
    result = subprocess.run(
        [ffmpeg_executable("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(Path(path).expanduser().resolve(strict=True))],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("O ffprobe não conseguiu determinar a duração do vídeo.") from exc
    if not np.isfinite(duration) or duration <= 0:
        raise RuntimeError("A duração do vídeo não é válida.")
    return duration


class FileVideoSource:
    """Reads video frames and presentation timestamps; never opens an audio stream."""

    def __init__(self, path: str | Path, source_id: str, start: float = 0, duration: float = 0,
                 segment_id: str = "file-0001", stop_requested: threading.Event | None = None,
                 pause_requested: threading.Event | None = None,
                 resume_requested: threading.Event | None = None, progress_callback=None,
                 stop_on_pause: bool = False) -> None:
        self.path = Path(path).expanduser().resolve(strict=True)
        if not self.path.is_file():
            raise ValueError("O caminho informado não é um arquivo.")
        self.source_id = source_id
        self.start = max(0.0, float(start))
        self.duration = max(0.0, float(duration))
        self.segment_id = segment_id
        self.stop_requested = stop_requested or threading.Event()
        self.pause_requested = pause_requested or threading.Event()
        self.resume_requested = resume_requested or threading.Event()
        self.resume_requested.set()
        self.progress_callback = progress_callback
        self.stop_on_pause = stop_on_pause
        self.paused = False
        self._frames: list[float] = []
        self._capture: cv2.VideoCapture | None = None
        self._segment_start = 0.0
        self.frames_in_range = 0
        self._decode_index = 0
        self._seek_needs_validation = False
        self._fps = 0.0
        self.sample_fps = 0
        self.frames_read = 0
        self.last_source_time = None

    def _report_progress(self, progress: dict) -> None:
        if self.progress_callback:
            try:
                self.progress_callback(progress)
            except Exception:
                pass

    def open(self) -> None:
        if self._capture:
            return
        metadata = cv2.VideoCapture(str(self.path))
        if not metadata.isOpened():
            metadata.release()
            raise RuntimeError("O OpenCV não conseguiu abrir o vídeo para ler seus metadados.")
        fps = float(metadata.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(metadata.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fourcc = int(metadata.get(cv2.CAP_PROP_FOURCC) or 0)
        codec = "".join(chr((fourcc >> (8 * shift)) & 255) for shift in range(4))
        metadata.release()
        self._fps = fps
        estimated_index_frames = max(1, round((self.start + self.duration) * fps)) if np.isfinite(fps) and fps > 0 and self.duration > 0 else frame_count
        if frame_count > 0 and estimated_index_frames > 0:
            estimated_index_frames = min(estimated_index_frames, frame_count)
        self._report_progress({"stage": "indexing_timestamps", "frames_indexed": 0,
                               "estimated_index_frames": estimated_index_frames or None,
                               "index_progress_percent": 0.0,
                               "activity_message": "Iniciando indexação dos timestamps do vídeo."})
        # VP9/WebM stores one displayed frame per packet in presentation order.
        # Reading packet timestamps avoids decoding every earlier 4K frame merely
        # to build an index. Other codecs keep the existing decoded-frame path.
        for entry in (["packet=pts_time", "frame=best_effort_timestamp_time"]
                      if codec == "VP90" else ["frame=best_effort_timestamp_time"]):
            probe = [ffmpeg_executable("ffprobe"), "-v", "error"]
            if self.duration > 0:
                probe.extend(["-read_intervals", f"0%+{self.start + self.duration:.6f}"])
            probe.extend(["-select_streams", "v:0", "-show_entries", entry,
                          "-of", "csv=p=0", str(self.path)])
            self._frames = []
            process = subprocess.Popen(probe, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, encoding="utf-8", errors="replace", bufsize=1)
            last_report = time.monotonic()
            invalid_timestamp = False
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    value = line.strip().split(",", 1)[0]
                    if not value:
                        continue
                    try:
                        pts = float(value)
                        if np.isfinite(pts):
                            self._frames.append(pts)
                        else:
                            invalid_timestamp = True
                    except ValueError:
                        invalid_timestamp = True
                        continue
                    now = time.monotonic()
                    if self._frames and (len(self._frames) % 128 == 0 or now - last_report >= 0.75):
                        indexed = len(self._frames)
                        percent = min(99.0, indexed / estimated_index_frames * 100) if estimated_index_frames else None
                        self._report_progress({"stage": "indexing_timestamps", "frames_indexed": indexed,
                                               "estimated_index_frames": estimated_index_frames or None,
                                               "index_progress_percent": percent, "index_source_time": pts,
                                               "current_source_time": max(0.0, pts - self._frames[0]),
                                               "activity_message": f"Indexando timestamps · {indexed:,} quadros"})
                        last_report = now
                return_code = process.wait()
            except Exception:
                process.kill()
                process.wait()
                raise
            if return_code and not entry.startswith("packet="):
                raise RuntimeError("O ffprobe falhou ao indexar os timestamps dos quadros de vídeo.")
            if (not return_code and not invalid_timestamp and self._frames and
                    all(right > left for left, right in zip(self._frames, self._frames[1:]))):
                break
            if entry.startswith("packet="):
                self._report_progress({"stage": "indexing_timestamps", "frames_indexed": 0,
                                       "activity_message": "Índice rápido indisponível; usando leitura de quadros."})
        if not self._frames:
            raise RuntimeError("O arquivo não contém frames de vídeo com timestamps legíveis.")
        self._segment_start = self._frames[0]
        end = self.start + self.duration if self.duration > 0 else float("inf")
        self.frames_in_range = sum(self.start <= pts - self._segment_start < end for pts in self._frames)
        self._report_progress({"stage": "opening_decoder", "frames_indexed": len(self._frames),
                               "estimated_index_frames": estimated_index_frames or None,
                               "index_progress_percent": 100.0, "frames_total": self.frames_in_range,
                               "current_source_time": self.start,
                               "activity_message": f"Índice pronto · {self.frames_in_range:,} quadros no intervalo; abrindo decodificador."})
        self._capture = cv2.VideoCapture(str(self.path))
        if not self._capture.isOpened():
            self._capture.release()
            self._capture = None
            raise RuntimeError("O OpenCV não conseguiu decodificar o vídeo.")
        self._decode_index = 0
        self._seek_needs_validation = False
        if self.start > 0:
            first_index = bisect_left(self._frames, self._segment_start + self.start - 0.0005)
            preroll = max(1, round(fps)) if np.isfinite(fps) and fps > 0 else 60
            seek_index = max(0, first_index - preroll)
            if seek_index:
                seek_ok = self._capture.set(cv2.CAP_PROP_POS_FRAMES, seek_index)
                position = float(self._capture.get(cv2.CAP_PROP_POS_FRAMES))
                if seek_ok and np.isfinite(position) and abs(position - seek_index) < 0.5:
                    self._decode_index = seek_index
                    self._seek_needs_validation = True
                else:
                    self._capture.release()
                    self._capture = cv2.VideoCapture(str(self.path))
                    if not self._capture.isOpened():
                        raise RuntimeError("O OpenCV não conseguiu reabrir o vídeo após a tentativa de busca.")
        self._report_progress({"stage": "analysis", "frames_indexed": len(self._frames),
                               "estimated_index_frames": estimated_index_frames or None,
                               "index_progress_percent": 100.0, "frames_total": self.frames_in_range,
                               "current_source_time": self.start,
                               "activity_message": "Índice concluído; iniciando a análise visual dos quadros."})

    def iter_frames(self) -> Iterator[FramePacket]:
        self.open()
        assert self._capture is not None
        end = self.start + self.duration if self.duration > 0 else float("inf")
        index = self._decode_index
        self.frames_read = 0
        self.last_source_time = None
        self.paused = False
        last_sample_time = None
        sample_interval = 1 / self.sample_fps if self.sample_fps > 0 else 0
        while not self.stop_requested.is_set():
            if self.pause_requested.is_set():
                if self.stop_on_pause:
                    self.paused = True
                    break
                self.resume_requested.wait(timeout=0.5)
                continue
            if index >= len(self._frames):
                break
            pts = self._frames[index]
            relative = pts - self._segment_start
            if relative >= end:
                break
            needs_image = (relative >= self.start and
                           (not sample_interval or last_sample_time is None or
                            relative - last_sample_time >= sample_interval - 1e-6))
            if needs_image or self._seek_needs_validation:
                ok, image = self._capture.read()
            else:
                ok = self._capture.grab()
                image = None
            if self._seek_needs_validation:
                actual_time = float(self._capture.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
                expected_time = pts
                tolerance = max(0.1, 2 / self._fps) if self._fps > 0 else 0.1
                self._seek_needs_validation = False
                if not ok or not np.isfinite(actual_time) or abs(actual_time - expected_time) > tolerance:
                    # Some OpenCV backends claim a successful seek but decode a
                    # different frame. Resume the original sequential route.
                    self._capture.release()
                    self._capture = cv2.VideoCapture(str(self.path))
                    if not self._capture.isOpened():
                        raise RuntimeError("O OpenCV não conseguiu reabrir o vídeo após uma busca imprecisa.")
                    index = 0
                    continue
            if not ok:
                break
            index += 1
            if relative < self.start:
                continue
            self.frames_read += 1
            self.last_source_time = relative
            if not needs_image:
                continue
            last_sample_time = relative
            yield FramePacket(image=image, source_id=self.source_id, segment_id=self.segment_id,
                              frame_id=index - 1, source_time=relative,
                              captured_at=datetime.now(timezone.utc), monotonic_ns=time.monotonic_ns(),
                              timing_quality="exact_pts", timing_error_ms=1.0)

    def frame_near(self, source_time: float) -> FramePacket | None:
        if not self._frames:
            # Review actions often need only a handful of frames from a large video.
            # Seeking directly avoids indexing every frame in the file just to split
            # one event; full indexing remains in use for analysis after open().
            try:
                return self.preview_at(source_time)
            except (OSError, RuntimeError, ValueError):
                return None
        target = self._segment_start + source_time
        index = min(range(len(self._frames)), key=lambda i: abs(self._frames[i] - target))
        cap = cv2.VideoCapture(str(self.path))
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, image = cap.read()
            if not ok:
                return None
            return FramePacket(image=image, source_id=self.source_id, segment_id=self.segment_id,
                               frame_id=index, source_time=self._frames[index] - self._segment_start,
                               captured_at=datetime.now(timezone.utc), monotonic_ns=time.monotonic_ns(),
                               timing_quality="exact_pts", timing_error_ms=1.0)
        finally:
            cap.release()

    def frames_near_window(self, source_times: list[float]) -> dict[float, FramePacket | None]:
        """Read nearby review frames with one seek and sequential video decoding."""
        targets = sorted(set(float(value) for value in source_times if value >= 0))
        if not targets:
            return {}
        if self._frames or targets[-1] - targets[0] > 2.0:
            return {target: self.frame_near(target) for target in targets}
        cap = cv2.VideoCapture(str(self.path))
        selected: dict[float, FramePacket | None] = {}
        try:
            if cap.isOpened() and cap.set(cv2.CAP_PROP_POS_MSEC, targets[0] * 1000.0):
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 60.0)
                if not np.isfinite(fps) or fps <= 0:
                    fps = 60.0
                max_reads = min(1000, max(120, int((targets[-1] - targets[0] + 2.0) * fps * 2)))
                previous: FramePacket | None = None
                target_index = 0
                for _ in range(max_reads):
                    ok, image = cap.read()
                    if not ok:
                        break
                    frame_time = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
                    if not np.isfinite(frame_time) or (frame_time <= 0 and targets[0] > 0):
                        break
                    if previous and frame_time < previous.source_time - 0.001:
                        break
                    packet = FramePacket(image=image, source_id=self.source_id,
                                         segment_id=self.segment_id,
                                         frame_id=max(0, int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1),
                                         source_time=frame_time,
                                         captured_at=datetime.now(timezone.utc),
                                         monotonic_ns=time.monotonic_ns(),
                                         timing_quality="opencv_seek")
                    while target_index < len(targets) and frame_time >= targets[target_index]:
                        target = targets[target_index]
                        nearest = (previous if previous and
                                   abs(previous.source_time - target) <= abs(frame_time - target)
                                   else packet)
                        selected[target] = replace(nearest, timing_error_ms=abs(nearest.source_time - target) * 1000)
                        target_index += 1
                    previous = packet
                    if target_index == len(targets):
                        break
        finally:
            cap.release()
        for target in targets:
            if target not in selected:
                selected[target] = self.frame_near(target)
        return selected

    def close(self) -> None:
        if self._capture:
            self._capture.release()
            self._capture = None

    def preview(self) -> tuple[np.ndarray, float, int, int]:
        cap = cv2.VideoCapture(str(self.path))
        try:
            ok, image = cap.read()
            if not ok:
                raise RuntimeError("Não foi possível extrair o primeiro frame do vídeo.")
            return image, float(cap.get(cv2.CAP_PROP_FPS)), int(image.shape[1]), int(image.shape[0])
        finally:
            cap.release()

    def preview_at(self, source_time: float) -> FramePacket:
        """Seek to one calibration image without indexing timestamps for the whole source."""
        requested_time = max(0.0, float(source_time))
        cap = cv2.VideoCapture(str(self.path))
        try:
            if not cap.isOpened():
                raise RuntimeError("O OpenCV não conseguiu abrir o vídeo para pré-visualização.")
            cap.set(cv2.CAP_PROP_POS_MSEC, requested_time * 1000.0)
            ok, image = cap.read()
            if not ok:
                raise RuntimeError(f"Não foi possível buscar o frame de prévia em {source_time:.3f}s.")
            frame_id = max(0, int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1)
            reported_time = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0) / 1000.0
            timestamp_available = np.isfinite(reported_time) and (reported_time > 0 or requested_time == 0)
            frame_time = reported_time if timestamp_available else requested_time
            return FramePacket(image=image, source_id=self.source_id, segment_id=self.segment_id,
                               frame_id=frame_id, source_time=frame_time,
                               captured_at=datetime.now(timezone.utc), monotonic_ns=time.monotonic_ns(),
                               timing_quality="opencv_seek" if timestamp_available else "preview_seek",
                               timing_error_ms=abs(frame_time - requested_time) * 1000.0
                               if timestamp_available else 1000.0)
        finally:
            cap.release()

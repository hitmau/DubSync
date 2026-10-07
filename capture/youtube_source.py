from __future__ import annotations

import json
import base64
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from urllib.parse import parse_qs, urlencode, urlparse

import cv2
import numpy as np

from .file_source import ffmpeg_executable
from .models import FramePacket


def youtube_watch_url(url: str, start: float) -> str:
    """Open one video at the requested content time, without playlist navigation."""
    parsed = urlparse(url)
    video_id = parsed.path.strip("/") if parsed.hostname == "youtu.be" else parse_qs(parsed.query).get("v", [""])[0]
    if not video_id or not all(char.isascii() and (char.isalnum() or char in "_-") for char in video_id):
        raise ValueError("O link do YouTube precisa identificar um vídeo.")
    return "https://www.youtube.com/watch?" + urlencode({"v": video_id, "t": f"{max(0, int(start))}s"})


def video_canvas_snapshot(page, allow_paused: bool = False) -> dict | None:
    """Read the decoded video pixels without browser chrome, overlays or desktop occlusion."""
    return page.evaluate("""(allowPaused) => {
        const v = document.querySelector('video');
        if (!v) return null;
        const result = {time:v.currentTime, paused:v.paused, seeking:v.seeking,
            ready:v.readyState, perf:performance.now(), width:v.videoWidth, height:v.videoHeight,
            error:v.error?.message||null, video_frame:window.__dubsyncLatestFrame||null,
            ended:v.ended, network_state:v.networkState,
            buffered:Array.from({length:v.buffered.length},(_,i)=>[v.buffered.start(i),v.buffered.end(i)]),
            player_error:document.querySelector('.ytp-error-content-wrap')?.innerText||null,
            play_error:window.__dubsyncPlayError||null,
            telemetry_supported:!!v.requestVideoFrameCallback};
        if (v.readyState >= 2 && v.videoWidth && v.videoHeight && (allowPaused || !v.paused)) {
            const c = window.__dubsyncCanvas || (window.__dubsyncCanvas = document.createElement('canvas'));
            if (c.width !== v.videoWidth) c.width = v.videoWidth;
            if (c.height !== v.videoHeight) c.height = v.videoHeight;
            c.getContext('2d').drawImage(v, 0, 0);
            result.jpeg = c.toDataURL('image/jpeg', 0.85);
        }
        return result;
    }""", allow_paused)


class YoutubeFrameSource:
    """Capture the controlled player's video element, independently of desktop occlusion."""

    def __init__(self, session_dir: Path, source_id: str, url: str, start: float, duration: float,
                 screen_bbox: dict[str, int], block_seconds: int = 60, target_fps: float = 30.0,
                 context=None, page=None, pause_requested: threading.Event | None = None,
                 resume_requested: threading.Event | None = None,
                 stop_requested: threading.Event | None = None) -> None:
        self.session_dir = session_dir
        self.source_id = source_id
        self.url = url
        self.start = max(0.0, start)
        self.duration = duration
        self.screen_bbox = {key: int(screen_bbox[key]) for key in ("left", "top", "width", "height")}
        self.block_seconds = max(5, block_seconds)
        self.target_fps = target_fps
        self.stop_requested = stop_requested or threading.Event()
        self.opened = False
        self.context = None
        self.page = None
        self._playwright = None
        self._owns_context = context is None
        self.context = context
        self.page = page
        self.pause_requested = pause_requested or threading.Event()
        self.resume_requested = resume_requested or threading.Event()
        self.resume_requested.set()
        self.on_status = None
        self.on_capture = None
        self._timeline: list[tuple[float, Path, dict]] = []
        self._video_size: tuple[int, int] | None = None

    def set_status(self, state: str, detail: str) -> None:
        if self.on_status:
            self.on_status(state, detail)

    def open(self) -> None:
        if self.opened:
            return
        if self.screen_bbox["width"] < 96 or self.screen_bbox["height"] < 96:
            raise ValueError("A prévia do vídeo precisa ter pelo menos 96×96 px.")
        from playwright.sync_api import sync_playwright
        if self.context is None:
            self._playwright = sync_playwright().start()
            profile = self.session_dir / "browser-profile"
            profile.mkdir(parents=True, exist_ok=True)
            self.context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile), headless=False,
                args=["--window-size=1366,850", "--window-position=0,0", "--autoplay-policy=no-user-gesture-required"],
                viewport={"width": 1366, "height": 768},
            )
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            self.page.goto(youtube_watch_url(self.url, self.start), wait_until="domcontentloaded", timeout=90_000)
            self.page.locator("video").first.wait_for(timeout=120_000)
        assert self.page is not None
        self.page.evaluate("""({start}) => {
            const video = document.querySelector('video');
            video.muted = true;
            video.pause();
            video.currentTime = start;
            if (!window.__dubsyncTelemetryInstalled && video.requestVideoFrameCallback) {
                window.__dubsyncTelemetryInstalled = true;
                window.__dubsyncLatestFrame = null;
                const observe = (now, metadata) => {
                    window.__dubsyncLatestFrame = {
                        callback_now: now,
                        media_time: metadata.mediaTime,
                        presentation_time: metadata.presentationTime,
                        expected_display_time: metadata.expectedDisplayTime,
                        presented_frames: metadata.presentedFrames,
                    };
                    video.requestVideoFrameCallback(observe);
                };
                video.requestVideoFrameCallback(observe);
            }
        }""", {"start": self.start})
        self.page.wait_for_function("""(start) => {
            const v = document.querySelector('video');
            return v && !v.seeking && v.readyState >= 2 && Math.abs(v.currentTime - start) < 1;
        }""", arg=self.start, timeout=45_000)
        self._video_size = tuple(self.page.evaluate("""() => {
            const v=document.querySelector('video'); return [v.videoWidth,v.videoHeight];
        }"""))
        if min(self._video_size) < 96:
            raise RuntimeError("O player não forneceu pixels do vídeo.")
        self._request_play()
        self.opened = True

    def _request_play(self, at: float | None = None) -> None:
        # A pending media play promise must not block the capture watchdog or Stop.
        self.page.evaluate("""(at) => {
            const v=document.querySelector('video');
            if (!v) throw new Error('Player ausente');
            v.muted=true;
            if (at !== null) {v.pause(); v.currentTime=at;}
            window.__dubsyncPlayError=null;
            v.play().catch(error => {window.__dubsyncPlayError=String(error);});
        }""", at)

    def _save_player_failure(self, player: dict, reason: str) -> None:
        diagnostic = {key:value for key,value in player.items() if key != 'jpeg'}
        diagnostic.update(reason=reason, observed_at=datetime.now(timezone.utc).isoformat())
        (self.session_dir / 'player_failure.json').write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')

    def iter_frames(self) -> Iterator[FramePacket]:
        self.open()
        assert self.page is not None
        deadline = self.start + self.duration if self.duration > 0 else float("inf")
        previous_time: float | None = None
        block = 0
        frame_global = 0
        while not self.stop_requested.is_set():
            target = self.session_dir / "segments" / f"{block:04d}"
            target.parent.mkdir(parents=True, exist_ok=True)
            manifest = target.with_suffix(".jsonl")
            video = target.with_suffix(".mp4")
            first_time: float | None = None
            last_time: float | None = None
            shape = (self.screen_bbox["width"], self.screen_bbox["height"])
            if shape[0] % 2 or shape[1] % 2:
                raise ValueError("Largura e altura da área selecionada precisam ser pares para codificação silenciosa.")
            ffmpeg_log = target.with_suffix(".ffmpeg.log").open("wb")
            encoder = subprocess.Popen(
                [ffmpeg_executable(), "-hide_banner", "-loglevel", "warning", "-y",
                 "-f", "rawvideo", "-pix_fmt", "bgr24", "-video_size", f"{shape[0]}x{shape[1]}",
                 "-framerate", str(self.target_fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
                 "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", str(video)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=ffmpeg_log, bufsize=shape[0] * shape[1] * 3 * 4,
            )
            started = time.monotonic()
            interval = 1.0 / self.target_fps
            capture_index = 0
            boundary_retries = 0
            reached_deadline = False
            try:
                with manifest.open("w", encoding="utf-8", newline="\n") as metadata:
                    last_progress_wall = time.monotonic()
                    last_player_time: float | None = None
                    while not self.stop_requested.is_set():
                        due = started + capture_index * interval
                        wait = due - time.monotonic()
                        if wait > 0:
                            time.sleep(min(wait, 0.02))
                            continue
                        before = time.monotonic_ns()
                        player = video_canvas_snapshot(self.page)
                        after_player = time.monotonic_ns()
                        if not player:
                            raise RuntimeError("O player do YouTube desapareceu durante a captura.")
                        if player["error"]:
                            self._save_player_failure(player, 'media_error')
                            raise RuntimeError(f"O player do YouTube falhou: {player['error']}")
                        if last_player_time is None or player["time"] > last_player_time + 0.01:
                            last_progress_wall = time.monotonic()
                        last_player_time = player["time"]
                        if time.monotonic() - last_progress_wall > 20:
                            self._save_player_failure(player, 'no_progress_20s')
                            raise RuntimeError("O vídeo não avançou por 20 segundos; verifique o player do YouTube.")
                        if player["paused"] or player["seeking"] or player["ready"] < 3:
                            time.sleep(0.06)
                            continue
                        if player["time"] >= deadline:
                            reached_deadline = True
                            self.page.evaluate("() => document.querySelector('video')?.pause()")
                            break
                        image = cv2.imdecode(np.frombuffer(base64.b64decode(player["jpeg"].split(",", 1)[1]), np.uint8),
                                             cv2.IMREAD_COLOR)
                        if image is None:
                            raise RuntimeError("O Chromium não devolveu um frame válido do vídeo.")
                        if (image.shape[1], image.shape[0]) != self._video_size:
                            image = cv2.resize(image, self._video_size, interpolation=cv2.INTER_AREA)
                        crop = self.screen_bbox
                        if crop["left"] + crop["width"] > image.shape[1] + 2 or crop["top"] + crop["height"] > image.shape[0] + 2:
                            raise RuntimeError("O recorte escolhido não cabe no vídeo do navegador.")
                        image = image[crop["top"]:crop["top"] + crop["height"],
                                      crop["left"]:crop["left"] + crop["width"]]
                        if (image.shape[1], image.shape[0]) != shape:
                            image = cv2.resize(image, shape, interpolation=cv2.INTER_AREA)
                        after = time.monotonic_ns()
                        estimate = (before + after) // 2
                        player_rtt_ms = (after_player - before) / 1_000_000
                        elapsed_after_player = max(0.0, (estimate - after_player) / 1_000_000_000)
                        telemetry = player.get("video_frame")
                        if telemetry and telemetry.get("media_time") is not None:
                            display_time = telemetry.get("expected_display_time", telemetry["callback_now"])
                            frame_age = max(0.0, (float(player["perf"]) - float(display_time)) / 1000.0)
                            callback_time = float(telemetry["media_time"]) + frame_age + elapsed_after_player
                            if abs(callback_time - float(player["time"])) <= 0.5:
                                source_time = callback_time
                                timing_quality = "request_video_frame_callback_estimate"
                                frame_number = int(telemetry.get("presented_frames", 0))
                                expected_display_time = telemetry.get("expected_display_time")
                            else:
                                source_time = float(player["time"]) + elapsed_after_player
                                timing_quality = "player_clock_after_seek"
                                frame_number = None
                                expected_display_time = None
                        else:
                            source_time = float(player["time"]) + elapsed_after_player
                            timing_quality = "player_clock_estimate"
                            frame_number = None
                            expected_display_time = None
                        if source_time < self.start - 1.0:
                            raise RuntimeError(f"O YouTube voltou a {source_time:.2f}s antes do início solicitado ({self.start:.2f}s).")
                        if previous_time is not None and first_time is None and abs(source_time - previous_time) > 0.75:
                            if boundary_retries >= 3:
                                raise RuntimeError("Não foi possível retomar o YouTube no limite do segmento anterior.")
                            boundary_retries += 1
                            self._request_play(previous_time)
                            continue
                        if last_time is not None and abs(source_time - last_time) > 2.0:
                            raise RuntimeError("O YouTube saltou na linha do tempo durante o bloco; a continuidade foi encerrada.")
                        record = {
                            "index": capture_index, "frame_id": frame_global,
                            "source_time": source_time, "captured_at": datetime.now(timezone.utc).isoformat(),
                            "monotonic_ns": estimate, "timing_quality": timing_quality,
                            "timing_error_ms": min(250.0, player_rtt_ms / 2 + 45.0),
                            "player_time": float(player["time"]), "video_frame_number": frame_number,
                            "video_frame_expected_display_time": expected_display_time,
                            "telemetry_supported": bool(player.get("telemetry_supported")),
                            "capture_backend": "video_canvas",
                            "video_native_width": int(player["width"]),
                            "video_native_height": int(player["height"]),
                            "dropped_interval_ms":
                                max(0.0, (estimate - (int(self._last_mono) if hasattr(self, "_last_mono") else estimate)) / 1_000_000 - 1000/self.target_fps),
                            "file": video.name,
                            "segment_id": f"youtube-{block:04d}",
                        }
                        encoder.stdin.write(image.tobytes())
                        metadata.write(json.dumps(record, ensure_ascii=False) + "\n")
                        capture_index += 1
                        frame_global += 1
                        self._last_mono = estimate
                        if self.on_capture:
                            self.on_capture(image, record, capture_index + 1)
                        first_time = source_time if first_time is None else first_time
                        last_time = source_time
                        if metadata.tell() % 32768 < 2048:
                            metadata.flush()
                        if first_time is not None and last_time - first_time >= self.block_seconds:
                            self.page.evaluate("() => document.querySelector('video')?.pause()")
                            break
            finally:
                try:
                    encoder.stdin.close()
                except (BrokenPipeError, OSError, AttributeError):
                    pass
                code = encoder.wait(timeout=60)
                ffmpeg_log.close()
            if code != 0:
                raise RuntimeError(f"FFmpeg encerrou com código {code}. Consulte {ffmpeg_log.name}.")
            if capture_index:
                samples = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
                self._timeline.extend((float(record["source_time"]), video, record) for record in samples)
                self.set_status("processing", f"Analisando o bloco {block + 1} ({capture_index} frames); depois o YouTube retoma.")
                capture = cv2.VideoCapture(str(video))
                try:
                    for record in samples:
                        ok, image = capture.read()
                        if not ok:
                            raise RuntimeError("Um frame de um segmento salvo não pôde ser reaberto.")
                        yield FramePacket(image=image, source_id=self.source_id,
                                          segment_id=record.get("segment_id", f"youtube-{block:04d}"),
                                          frame_id=record["frame_id"], source_time=record["source_time"],
                                          captured_at=datetime.fromisoformat(record["captured_at"]),
                                          monotonic_ns=record["monotonic_ns"],
                                          timing_quality=record["timing_quality"],
                                          timing_error_ms=record["timing_error_ms"])
                finally:
                    capture.release()
            previous_time = last_time if last_time is not None else previous_time
            block += 1
            if self.stop_requested.is_set() or reached_deadline or last_time is None or last_time >= deadline - 0.05:
                break
            # This executes only after the caller processed the complete stored block.
            if self.pause_requested.is_set():
                self.resume_requested.clear()
                self.set_status("paused", "Pausado entre blocos. Aguardando você retomar a captura.")
                while self.pause_requested.is_set() and not self.stop_requested.is_set():
                    self.resume_requested.wait(timeout=0.5)
            if self.stop_requested.is_set():
                break
            self.set_status("running", "Retomando o YouTube para o próximo bloco.")
            self._request_play(previous_time)

    def frame_near(self, source_time: float) -> FramePacket | None:
        # Only small per-frame metadata stays cached; screenshots live in the encoded chunks.
        best = min(self._timeline, key=lambda item: abs(item[0] - source_time), default=None)
        if best is None:
            for manifest in sorted((self.session_dir / "segments").glob("*.jsonl")):
                clip = manifest.with_suffix(".mp4")
                self._timeline.extend((float(record["source_time"]), clip, record)
                                      for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()
                                      for record in [json.loads(line)])
            best = min(self._timeline, key=lambda item: abs(item[0] - source_time), default=None)
        if best is None:
            return None
        timestamp, clip, record = best
        delta = abs(timestamp - source_time)
        cap = cv2.VideoCapture(str(clip))
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(record["index"]))
            ok, image = cap.read()
            if not ok:
                return None
        finally:
            cap.release()
        if delta > 0.3:
            return None
        return FramePacket(image=image, source_id=self.source_id,
                           segment_id=record.get("segment_id", clip.stem),
                           frame_id=record["frame_id"], source_time=record["source_time"],
                           captured_at=datetime.fromisoformat(record["captured_at"]),
                           monotonic_ns=record["monotonic_ns"], timing_quality=record["timing_quality"],
                           timing_error_ms=record["timing_error_ms"])

    def close(self) -> None:
        if self.context and self._owns_context:
            try:
                self.context.close()
            finally:
                self.context = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None
        self.opened = False

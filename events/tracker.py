from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from rapidfuzz.fuzz import ratio, token_set_ratio

from ..capture.models import FramePacket
from ..detector.visual import Region, perceptual_hash
from ..ocr.engine import OcrLine
from ..ocr.text_cleanup import strip_trailing_hud_artifacts
from .normalize import normalize_text


@dataclass(slots=True)
class Candidate:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]
    packet: FramePacket
    crop: np.ndarray
    text_hash: str
    raw_text: str | None = None
    stable_observations: int = 1
    best_confidence: float = 0.0
    best_packet: FramePacket | None = None
    ocr_observations: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class Active:
    candidate: Candidate
    last_seen: float
    miss_count: int = 0
    first_missing: float | None = None


class DialogueTracker:
    """EMPTY → CANDIDATE → ACTIVE → END state machine for one calibrated screen area."""

    def __init__(self, region: Region, stable_frames: int = 3, absence_frames: int = 2,
                 min_confidence: float = 0.30, strip_trailing_hud: bool = False) -> None:
        self.region = region
        self.stable_frames = stable_frames
        self.absence_frames = absence_frames
        self.min_confidence = float(min_confidence)
        self.strip_trailing_hud = bool(strip_trailing_hud)
        self.candidate: Candidate | None = None
        self.active: Active | None = None
        self.last_observation: tuple[str, float, tuple[int, int, int, int], str] | None = None

    def _lines(self, lines: list[OcrLine], offset_x: int, offset_y: int) -> tuple[str, float, tuple[int, int, int, int]]:
        if not lines:
            return "", 0.0, (0, 0, 0, 0)
        text = " ".join(line.text for line in lines).strip()
        confidence = sum(line.confidence for line in lines) / len(lines)
        x0 = min(line.bbox[0] for line in lines) + offset_x
        y0 = min(line.bbox[1] for line in lines) + offset_y
        x1 = max(line.bbox[2] for line in lines) + offset_x
        y1 = max(line.bbox[3] for line in lines) + offset_y
        return text, confidence, (x0, y0, x1 - x0, y1 - y0)

    def observe(self, packet: FramePacket, lines: list[OcrLine] | None,
                artifacts: dict[str, str] | None = None) -> list[dict]:
        x0, y0, x1, y1 = self.region.bounds(packet.image, packet.source_time)
        region_image = packet.image[y0:y1, x0:x1]
        if lines is None:
            if self.candidate is not None:
                self.candidate.stable_observations += 1
                if self.candidate.stable_observations >= self.stable_frames:
                    candidate = self.candidate
                    self.active = Active(candidate, packet.source_time)
                    self.candidate = None
                    return [self._start(candidate, {})]
            if self.active and self.last_observation is None:
                self.active.miss_count += 1
                if self.active.miss_count >= self.absence_frames:
                    return [self._finish(self.active.first_missing if self.active.first_missing is not None else packet.source_time)]
            return []
        raw_text, confidence, bbox = self._lines(lines, x0, y0)
        text = strip_trailing_hud_artifacts(raw_text) if self.strip_trailing_hud else raw_text
        normalized = normalize_text(text)
        if confidence < self.min_confidence or (len(normalized) < 2 and confidence < 0.95) or \
                (len(normalized) < 4 and confidence < 0.85):
            text = ""
            normalized = ""
        actions: list[dict] = []

        if not text:
            self.candidate = None
            if self.active:
                if self.active.first_missing is None:
                    self.active.first_missing = packet.source_time
                self.active.miss_count += 1
                if self.active.miss_count >= self.absence_frames:
                    actions.append(self._finish(self.active.first_missing))
            self.last_observation = None
            return actions

        text_crop = packet.image[max(0, bbox[1]):min(packet.image.shape[0], bbox[1] + bbox[3]),
                                 max(0, bbox[0]):min(packet.image.shape[1], bbox[0] + bbox[2])]
        if text_crop.size == 0:
            text_crop = region_image
        text_hash = perceptual_hash(text_crop)
        self.last_observation = (text, confidence, bbox, text_hash)
        observation = {"source_time": packet.source_time, "text": raw_text, "processed_text": text,
                       "confidence": confidence, "bbox": list(bbox)}

        if self.active:
            similarity = ratio(normalize_text(self.active.candidate.text), normalized)
            shared_words = token_set_ratio(normalize_text(self.active.candidate.text), normalized)
            if similarity >= 72 or shared_words >= 85:
                self.active.miss_count = 0
                self.active.first_missing = None
                self.active.last_seen = packet.source_time
                old = self.active.candidate
                old.ocr_observations.append(observation)
                if confidence >= old.best_confidence:
                    old.best_packet = packet
                    old.best_confidence = confidence
                if confidence >= old.best_confidence or len(normalized) >= len(normalize_text(old.text)):
                    old.text = text
                    old.raw_text = raw_text
                    old.confidence = confidence
                    old.bbox = bbox
                    old.crop = text_crop.copy()
                    old.text_hash = text_hash
                return actions
            actions.append(self._finish(packet.source_time))

        candidate = self.candidate
        if candidate and (
            ratio(normalize_text(candidate.text), normalized) >= 68
            or normalized.startswith(normalize_text(candidate.text))
            or normalize_text(candidate.text).startswith(normalized)
        ):
            candidate.stable_observations += 1
            if confidence >= candidate.best_confidence or len(normalized) >= len(normalize_text(candidate.text)):
                candidate.text = text
                candidate.raw_text = raw_text
                candidate.confidence = confidence
                candidate.bbox = bbox
                candidate.crop = text_crop.copy()
                candidate.text_hash = text_hash
                candidate.best_confidence = confidence
                candidate.best_packet = packet
            candidate.ocr_observations.append(observation)
        else:
            self.candidate = Candidate(text, confidence, bbox, packet, text_crop.copy(), text_hash,
                                       raw_text=raw_text,
                                       best_confidence=confidence, best_packet=packet,
                                       ocr_observations=[observation])
        candidate = self.candidate
        if candidate and candidate.stable_observations >= self.stable_frames:
            self.active = Active(candidate, packet.source_time)
            self.candidate = None
            actions.append(self._start(candidate, artifacts or {}))
        return actions

    def _start(self, candidate: Candidate, artifacts: dict[str, str]) -> dict:
        packet = candidate.packet
        return {
            "kind": "start", "start_time": packet.source_time, "end_time": None,
            "original_text": candidate.raw_text if candidate.raw_text is not None else candidate.text,
            "processed_text": candidate.text, "normalized_text": normalize_text(candidate.text),
            "confidence": candidate.confidence, "bbox": list(candidate.bbox),
            "region_name": self.region.name,
            "region_id": self.region.roi_id,
            "ocr_observations": list(candidate.ocr_observations),
            "crop_path": artifacts.get("crop_path"), "start_frame_path": artifacts.get("start_frame_path"),
            "before_frame_path": artifacts.get("before_frame_path"),
            "text_phash": candidate.text_hash, "context_phash": perceptual_hash(packet.image),
            "segment_id": packet.segment_id, "frame_id": packet.frame_id,
            "timing_quality": packet.timing_quality, "timing_error_ms": packet.timing_error_ms,
            "captured_at": packet.captured_at.isoformat(),
        }

    def _finish(self, end_time: float) -> dict:
        assert self.active is not None
        start = self.active.candidate.packet.source_time
        result = {"kind": "end", "end_time": max(start, end_time),
                  "start_time": start, "normalized_text": normalize_text(self.active.candidate.text),
                  "final_candidate": self.active.candidate}
        self.active = None
        return result

    def flush(self) -> list[dict]:
        if not self.active:
            self.candidate = None
            return []
        result = self._finish(self.active.last_seen)
        result["truncated"] = True
        self.candidate = None
        return [result]

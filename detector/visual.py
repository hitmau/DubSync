from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class Region:
    x: float
    y: float
    width: float
    height: float
    name: str = "dialogue"
    lifetime: dict[str, Any] | None = None
    motion: dict[str, Any] | None = None
    speaker_enabled: bool = False
    speaker_region: dict[str, float] | None = None
    roi_id: str = ""
    source_start: float = 0.0
    character_detection: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, value: dict, index: int = 0, source_start: float = 0.0) -> "Region":
        return cls(float(value.get("x", 0)), float(value.get("y", 0.64)),
                   float(value.get("width", 1)), float(value.get("height", 0.36)),
                   str(value.get("name", f"area-{index + 1}")),
                   value.get("lifetime"), value.get("motion"),
                   bool(value.get("speaker_enabled", False)), value.get("speaker_region"),
                   str(value.get("roi_id", "")),
                   float(source_start), value.get("character_detection"))

    def _time(self, entry: dict) -> float:
        return float(entry.get("time", 0) if "time" in entry else entry.get("start", 0)) + (self.source_start if entry.get("mode") == "relative" else 0)

    def active_at(self, source_time: float) -> bool:
        config = self.lifetime or {}
        if not config.get("enabled"):
            return True
        return any(self._time(window) <= source_time < float(window["end"]) + (self.source_start if window.get("mode") == "relative" else 0)
                   for window in config.get("windows", []))

    def geometry_at(self, source_time: float | None = None) -> tuple[float, float, float, float]:
        base = (self.x, self.y, self.width, self.height)
        if source_time is None or not (self.motion or {}).get("enabled"):
            return base
        points = sorted(((self._time(point), tuple(float(point[key]) for key in ("x", "y", "width", "height")))
                         for point in self.motion.get("keyframes", [])), key=lambda item: item[0])
        if not points:
            return base
        if source_time <= points[0][0]:
            return points[0][1]
        for (t0, a), (t1, b) in zip(points, points[1:]):
            if source_time <= t1:
                f = max(0.0, min(1.0, (source_time - t0) / max(1e-9, t1 - t0)))
                return tuple(v0 + (v1 - v0) * f for v0, v1 in zip(a, b))
        return points[-1][1]

    def bounds(self, image: np.ndarray, source_time: float | None = None) -> tuple[int, int, int, int]:
        height, width = image.shape[:2]
        x, y, w, h = self.geometry_at(source_time)
        x0 = max(0, min(width - 1, round(x * width)))
        y0 = max(0, min(height - 1, round(y * height)))
        x1 = max(x0 + 1, min(width, round((x + w) * width)))
        y1 = max(y0 + 1, min(height, round((y + h) * height)))
        return x0, y0, x1, y1

    def speaker_bounds(self, image: np.ndarray, source_time: float) -> tuple[int, int, int, int] | None:
        if not self.speaker_enabled or not self.speaker_region:
            return None
        sx, sy, _, _ = self.geometry_at(source_time)
        spec = self.speaker_region
        linked = Region(float(spec["x"]) + sx - self.x, float(spec["y"]) + sy - self.y,
                        float(spec["width"]), float(spec["height"]))
        return linked.bounds(image)


class VisualChangeDetector:
    """Cheap low-resolution gate used before any full text recognition."""

    def __init__(self, mean_threshold: float = 2.4, changed_pixel_fraction: float = 0.006) -> None:
        self.mean_threshold = float(mean_threshold)
        self.changed_pixel_fraction = float(changed_pixel_fraction)
        self._previous: dict[str, np.ndarray] = {}

    def reset(self, key: str) -> None:
        self._previous.pop(key, None)

    def changed(self, key: str, roi: np.ndarray) -> bool:
        grey = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
        small = cv2.resize(grey, (128, 64), interpolation=cv2.INTER_AREA)
        previous = self._previous.get(key)
        self._previous[key] = small
        if previous is None:
            return True
        delta = cv2.absdiff(small, previous)
        mean = float(delta.mean())
        changed_fraction = float(np.count_nonzero(delta > 24)) / delta.size
        return mean >= self.mean_threshold or changed_fraction >= self.changed_pixel_fraction


def perceptual_hash(image: np.ndarray) -> str:
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    small = cv2.resize(grey, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    transformed = cv2.dct(small)[:8, :8]
    flat = transformed.flatten()
    median = float(np.median(flat[1:]))
    return "".join(f"{byte:02x}" for byte in np.packbits(flat > median).tobytes())


def hash_distance(first: str | None, second: str | None) -> int:
    if not first or not second or len(first) != len(second):
        return 64
    return sum((int(a, 16) ^ int(b, 16)).bit_count() for a, b in zip(first, second))

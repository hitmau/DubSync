from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Protocol, runtime_checkable

import numpy as np


@dataclass(slots=True)
class FramePacket:
    image: np.ndarray
    source_id: str
    segment_id: str
    frame_id: int
    source_time: float
    captured_at: datetime
    monotonic_ns: int
    timing_quality: str = "exact_pts"
    timing_error_ms: float = 0.0


@runtime_checkable
class FrameSource(Protocol):
    source_id: str

    def open(self) -> None: ...
    def iter_frames(self) -> Iterator[FramePacket]: ...
    def frame_near(self, source_time: float) -> FramePacket | None: ...
    def close(self) -> None: ...

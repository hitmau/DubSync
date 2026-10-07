from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Dupsync.capture.session_manager import validate_regions
from Dupsync.detector.visual import Region
from Dupsync.events.processor import DialogueProcessor
from Dupsync.events.tracker import Active, Candidate
from Dupsync.capture.models import FramePacket
from Dupsync.database.store import DubSyncStore
from Dupsync.ocr.engine import OcrLine


class RoiFeaturesTests(unittest.TestCase):
    def test_roi_character_portrait_is_persisted_with_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = DubSyncStore(Path(directory) / "dubsync.sqlite3")
            try:
                store.save_roi_characters("roi-portrait", [{
                    "character_id": "portrait-character", "name": "Mara", "notes": "",
                }])
                photo_path = Path(directory) / "mara.png"
                item = store.update_character_photo("portrait-character", str(photo_path), "mara.png",
                                                    "image/png", 1234)
                self.assertEqual(item["photo_name"], "mara.png")
                self.assertEqual(item["photo_mime"], "image/png")
                self.assertEqual(item["photo_bytes"], 1234)
                saved = store.roi_characters("roi-portrait")[0]
                self.assertEqual(saved["photo_path"], str(photo_path))
            finally:
                store.engine.dispose()

    def test_relative_lifetime_uses_analysis_start(self) -> None:
        config = validate_regions([{
            "name": "dialogue", "x": .1, "y": .7, "width": .8, "height": .2,
            "lifetime": {"enabled": True, "windows": [
                {"mode": "relative", "start": 10, "end": 20},
                {"mode": "absolute", "start": 200, "end": 205},
            ]},
        }])[0]
        region = Region.from_dict(config, source_start=170)
        self.assertFalse(region.active_at(179.999))
        self.assertTrue(region.active_at(180))
        self.assertFalse(region.active_at(190))
        self.assertTrue(region.active_at(202))

    def test_motion_interpolates_position_and_size(self) -> None:
        config = validate_regions([{
            "x": .1, "y": .2, "width": .3, "height": .1,
            "motion": {"enabled": True, "keyframes": [
                {"mode": "relative", "time": 0, "x": .1, "y": .2, "width": .3, "height": .1},
                {"mode": "relative", "time": 10, "x": .3, "y": .4, "width": .5, "height": .2},
            ]},
        }])[0]
        region = Region.from_dict(config, source_start=170)
        for actual, expected in zip(region.geometry_at(175), (.2, .3, .4, .15)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(region.bounds(np.zeros((100, 200, 3), np.uint8), 175), (40, 30, 120, 45))

    def test_speaker_area_tracks_roi_movement(self) -> None:
        config = validate_regions([{
            "x": .1, "y": .2, "width": .3, "height": .2,
            "speaker_enabled": True,
            "speaker_region": {"x": .1, "y": .1, "width": .2, "height": .05},
            "motion": {"enabled": True, "keyframes": [
                {"mode": "absolute", "time": 0, "x": .2, "y": .3, "width": .3, "height": .2},
            ]},
        }])[0]
        region = Region.from_dict(config)
        self.assertEqual(region.speaker_bounds(np.zeros((100, 200, 3), np.uint8), 1), (40, 20, 80, 25))

    def test_invalid_windows_and_boxes_are_rejected(self) -> None:
        base = {"x": .1, "y": .7, "width": .8, "height": .2}
        for extra in (
            {"lifetime": {"enabled": True, "windows": []}},
            {"lifetime": {"enabled": True, "windows": [{"mode": "relative", "start": 10, "end": 3}]}},
            {"motion": {"enabled": True, "keyframes": []}},
            {"speaker_enabled": True},
            {"speaker_enabled": True, "speaker_region": {"x": .9, "y": .1, "width": .2, "height": .1}},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                validate_regions([{**base, **extra}])

    def test_processor_skips_ocr_outside_lifetime_and_uses_moving_box(self) -> None:
        config = validate_regions([{
            "x": .1, "y": .1, "width": .3, "height": .2,
            "lifetime": {"enabled": True, "windows": [{"mode": "absolute", "start": 10, "end": 12}]},
            "motion": {"enabled": True, "keyframes": [
                {"mode": "absolute", "time": 10, "x": .2, "y": .2, "width": .4, "height": .3},
            ]},
        }])[0]

        class RecordingOcr:
            def __init__(self) -> None:
                self.shapes: list[tuple[int, ...]] = []

            def recognize(self, crop: np.ndarray) -> list:
                self.shapes.append(crop.shape)
                return []

        ocr = RecordingOcr()
        processor = DialogueProcessor(None, "roi-test", Path("."), [Region.from_dict(config)], "en", ocr=ocr)
        for at in (9.0, 10.0, 12.1):
            frame = FramePacket(np.zeros((100, 200, 3), np.uint8), "test", "segment", int(at * 30),
                                at, datetime.now(timezone.utc), int(at * 1_000_000_000))
            processor._inspect(frame, None)
        self.assertEqual(ocr.shapes, [(30, 80, 3)])

    def test_speaker_ocr_is_saved_separately_with_original_crop(self) -> None:
        config = validate_regions([{
            "name": "dialogue", "x": .1, "y": .5, "width": .8, "height": .4,
            "speaker_enabled": True,
            "speaker_region": {"x": .1, "y": .1, "width": .3, "height": .12},
        }])[0]

        class Store:
            draft = None

            def add_event(self, session_id: str, draft: dict,
                          replace_scope: dict | None = None) -> tuple[str, bool]:
                self.draft = draft
                return "event-one", False

            def roi_characters(self, roi_id: str) -> list[dict]:
                return []

        class SpeakerOcr:
            def recognize(self, crop: np.ndarray) -> list[OcrLine]:
                return [OcrLine("MARA", .96, (0, 0, crop.shape[1], crop.shape[0]))]

        class Source:
            def __init__(self, frame: FramePacket) -> None:
                self.frame = frame

            def frame_near(self, source_time: float) -> FramePacket:
                return self.frame

        frame = FramePacket(np.zeros((100, 200, 3), np.uint8), "test", "segment", 30,
                            1.0, datetime.now(timezone.utc), 1_000_000_000)
        candidate = Candidate("Leave now.", .93, (20, 55, 100, 22), frame,
                              frame.image[55:77, 20:120].copy(), "0000000000000000")
        candidate.ocr_observations.append({"source_time": 1.0, "text": "Leave now.", "confidence": .93})
        store = Store()
        with tempfile.TemporaryDirectory() as directory:
            processor = DialogueProcessor(store, "test", Path(directory), [Region.from_dict(config)],
                                          "en", ocr=SpeakerOcr())
            tracker = processor.trackers["dialogue"]
            tracker.active = Active(candidate, 1.0)
            draft = tracker._start(candidate, {})
            self.assertEqual(processor._save_start(draft, Source(frame)), "event-one")
            self.assertEqual(store.draft["character"], "MARA")
            self.assertTrue(any(item.get("kind") == "speaker" for item in store.draft["ocr_observations"]))
            speaker_asset = next(item for item in store.draft["assets"] if item["kind"] == "speaker_crop_original")
            self.assertTrue((Path(directory) / speaker_asset["path"]).exists())


if __name__ == "__main__":
    unittest.main()

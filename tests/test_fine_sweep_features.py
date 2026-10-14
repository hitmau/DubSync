from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from Dupsync.database.store import DubSyncStore
from Dupsync.capture.models import FramePacket
from Dupsync.events.fine_sweep import compare_audit_observations, run_fine_sweep
from Dupsync.ocr.engine import OcrLine
from Dupsync.app import main as app_main


class _FakeOcrEngine:
    def __init__(self, *_args, **_kwargs):
        pass

    def open(self):
        pass

    def recognize(self, _image):
        return [OcrLine("Fuja agora!", .95, (0, 0, 30, 12))]


class _FakeSource:
    def frame_near(self, at_time):
        return FramePacket(np.zeros((90, 160, 3), dtype=np.uint8), "test", "file", 42,
                           at_time, datetime.now(timezone.utc), 1)

    def close(self):
        pass


class FineSweepFeatureTests(unittest.TestCase):
    def test_retry_ocr_also_reads_configured_speaker_region(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = DubSyncStore(Path(temporary) / "test.sqlite3")
            session_id = "99a73a68-6236-4f29-a115-a0fd4f730a20"
            regions = [{"roi_id": "roi", "name": "Dialogue", "x": .1, "y": .5,
                        "width": .8, "height": .4, "speaker_enabled": True,
                        "speaker_region": {"x": .2, "y": .2, "width": .3, "height": .12}}]
            store.create_session(session_id, "file", "local.mp4", 10, 30, "pt",
                                 {"regions": regions}, "complete")
            profile = store.ensure_roi_character("roi", "Warren", "Personagem já conhecido")
            event_id, _ = store.add_event(session_id, {"start_time": 12, "original_text": "Fuja agora!",
                    "processed_text": "Fuja agora!", "normalized_text": "fuja agora", "confidence": .8,
                    "bbox_json": [10, 50, 40, 15], "text_phash": "abc", "region_id": "roi"})
            store.finish_event(event_id, 15)

            class _DialogueAndSpeakerOcr:
                def __init__(self, *_args, **_kwargs):
                    self.calls = 0

                def recognize(self, _image):
                    self.calls += 1
                    text = "Fuja agora!" if self.calls == 1 else "Warren"
                    return [OcrLine(text, .95, (0, 0, 30, 12))]

            with patch.object(app_main, "store", store), \
                 patch.object(app_main, "SESSION_DIR", Path(temporary) / "sessions"), \
                 patch.object(app_main, "_event_source", return_value=_FakeSource()), \
                 patch.object(app_main, "OcrEngine", _DialogueAndSpeakerOcr), \
                 patch.object(app_main, "load_settings", return_value={"ocr": {}}):
                result = app_main.retry_event_ocr(event_id, app_main.EventAtTime(at_time=13))

            self.assertEqual(result["speaker"]["text"], "Warren")
            self.assertEqual(result["speaker"]["candidate"], "Warren")
            self.assertEqual(result["speaker"]["profile_id"], profile["character_id"])
            self.assertIn("ocr_retry_speaker_crop", [asset["kind"] for asset in result["image_assets"]])
            saved = store.event(event_id)
            self.assertEqual(saved["ocr_observations_json"][-1]["speaker"]["candidate"], "Warren")
            self.assertIsNone(saved["character"])
            store.engine.dispose()

    def test_retry_ocr_reads_configured_roi_and_appends_observation(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = DubSyncStore(Path(temporary) / "test.sqlite3")
            session_id = "99a73a68-6236-4f29-a115-a0fd4f730a20"
            regions = [{"roi_id": "roi", "name": "Dialogue", "x": .1, "y": .5,
                        "width": .8, "height": .4}]
            store.create_session(session_id, "file", "local.mp4", 10, 30, "pt",
                                 {"regions": regions}, "complete")
            event_id, _ = store.add_event(session_id, {"start_time": 12, "original_text": "Fuja agora.",
                    "processed_text": "Fuja agora.", "normalized_text": "fuja agora", "confidence": .8,
                    "bbox_json": [10, 50, 40, 15], "text_phash": "abc", "region_id": "roi"})
            store.finish_event(event_id, 15)
            store.review(event_id, "correct", {"corrected_text": "Fuja agora, por favor."})
            session_root = Path(temporary) / "sessions" / session_id
            with patch.object(app_main, "store", store), \
                 patch.object(app_main, "SESSION_DIR", Path(temporary) / "sessions"), \
                 patch.object(app_main, "_event_source", return_value=_FakeSource()), \
                 patch.object(app_main, "OcrEngine", _FakeOcrEngine), \
                 patch.object(app_main, "load_settings", return_value={"ocr": {}}):
                result = app_main.retry_event_ocr(event_id, app_main.EventAtTime(at_time=13))
            self.assertEqual(result["processed_text"], "Fuja agora!")
            self.assertEqual([asset["kind"] for asset in result["image_assets"]],
                             ["ocr_retry_crop", "ocr_retry_frame"])
            for asset in result["image_assets"]:
                self.assertTrue((session_root / asset["path"]).is_file())
            saved = store.event(event_id)
            self.assertEqual(saved["original_text"], "Fuja agora.")
            self.assertEqual(saved["corrected_text"], "Fuja agora, por favor.")
            self.assertEqual(saved["ocr_observations_json"][-1]["source"], "manual_retry")
            self.assertEqual(saved["ocr_observations_json"][-1]["bbox"], [16, 45, 128, 36])
            self.assertEqual(saved["ocr_observations_json"][-1]["image_assets"],
                             result["image_assets"])
            store.engine.dispose()

    def test_duplicate_starts_at_playhead_and_retry_keeps_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = DubSyncStore(Path(temporary) / "test.sqlite3")
            session_id = "99a73a68-6236-4f29-a115-a0fd4f730a20"
            store.create_session(session_id, "file", "local.mp4", 10, 30, "pt", {}, "complete")
            source = store.add_manual_event(session_id, "Saia daqui.", 12, 14, "Warren")
            duplicate = store.duplicate_event(source["event_id"], 21.25, 23.25)
            self.assertEqual(duplicate["start_time"], 21.25)
            self.assertEqual(duplicate["end_time"], 23.25)
            self.assertEqual(duplicate["original_text"], "Saia daqui.")
            self.assertEqual(duplicate["segment_id"], "duplicate")
            self.assertEqual(duplicate["review_status"], "pending")
            observation = {"source": "manual_retry", "source_time": 21.5,
                           "text": "Saia daqui!", "confidence": .91}
            store.add_ocr_retry(duplicate["event_id"], observation)
            saved = store.event(duplicate["event_id"])
            self.assertEqual(saved["original_text"], "Saia daqui.")
            self.assertEqual(saved["ocr_observations_json"][-1], observation)
            store.engine.dispose()

    def test_audit_flags_only_evidence_backed_divergence_and_stable_gap(self):
        events = [
            {"event_id": "a", "start_time": 10, "end_time": 12, "region_id": "roi",
             "review_status": "corrected", "original_text": "Abra a porta.",
             "processed_text": "Abra a porta.", "corrected_text": "Feche a janela."},
            {"event_id": "b", "start_time": 15, "end_time": 17, "region_id": "roi",
             "review_status": "accepted", "original_text": "Tudo certo.",
             "processed_text": "Tudo certo.", "corrected_text": None},
        ]
        observations = [
            {"source_time": 11, "region_id": "roi", "text": "Abra a porta.", "confidence": .95},
            {"source_time": 16, "region_id": "roi", "text": "Tudo certo.", "confidence": .94},
            {"source_time": 20, "region_id": "roi", "text": "Fuja agora!", "confidence": .86},
            {"source_time": 21, "region_id": "roi", "text": "Fuja agora!", "confidence": .87},
        ]
        findings, inconclusive = compare_audit_observations(events, observations)
        self.assertEqual(inconclusive, 0)
        self.assertEqual([item["kind"] for item in findings],
                         ["correction_divergence", "missing_dialogue"])
        self.assertEqual(findings[0]["event_id"], "a")
        self.assertIsNone(findings[1]["event_id"])

    def test_audit_run_persists_progress_and_findings(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "test.sqlite3"
            store = DubSyncStore(path)
            session_id = "99a73a68-6236-4f29-a115-a0fd4f730a20"
            store.create_session(session_id, "file", "local.mp4", 10, 30, "pt", {}, "complete")
            run = store.create_fine_sweep(session_id)
            with self.assertRaises(ValueError):
                store.create_fine_sweep(session_id)
            store.update_fine_sweep(run["run_id"], state="complete", progress_percent=100,
                                    findings_json=[{"kind": "missing_dialogue", "source_time": 20}])
            self.assertEqual(store.latest_fine_sweep(session_id)["findings_json"][0]["source_time"], 20)
            store.engine.dispose()

    def test_visual_sweep_completes_on_short_video_without_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "silent.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (160, 90))
            self.assertTrue(writer.isOpened())
            for _ in range(35):
                writer.write(np.zeros((90, 160, 3), dtype=np.uint8))
            writer.release()
            store = DubSyncStore(Path(temporary) / "test.sqlite3")
            session_id = "99a73a68-6236-4f29-a115-a0fd4f730a20"
            config = {"path": str(path), "regions": [{"roi_id": "roi", "name": "ROI",
                       "x": 0, "y": .5, "width": 1, "height": .5, "enabled": True}]}
            store.create_session(session_id, "file", str(path), 0, 3, "pt", config, "complete")
            run = store.create_fine_sweep(session_id)
            with patch("Dupsync.events.fine_sweep.OcrEngine", _FakeOcrEngine):
                run_fine_sweep(store, run["run_id"], store.session(session_id), {})
            finished = store.latest_fine_sweep(session_id)
            self.assertEqual(finished["state"], "complete", finished["error"])
            self.assertEqual(finished["progress_percent"], 100)
            self.assertGreater(finished["checked_frames"], 0)
            self.assertTrue(any(item["kind"] == "missing_dialogue"
                                for item in finished["findings_json"]))
            store.engine.dispose()


if __name__ == "__main__":
    unittest.main()

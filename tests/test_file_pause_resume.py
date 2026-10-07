from __future__ import annotations

import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from Dupsync.capture.session_manager import SessionManager
from Dupsync.database.store import DubSyncStore


def dialogue(at: float) -> dict:
    return {"start_time": at, "original_text": f"Fala {at}",
            "normalized_text": f"fala {at}", "confidence": .9,
            "bbox_json": [1, 1, 80, 20], "text_phash": "ab" * 8}


class FilePauseResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.video = root / "source.webm"
        self.video.write_bytes(b"synthetic video for session state tests")
        self.store = DubSyncStore(root / "test.sqlite3")
        self.session_id = str(uuid.uuid4())
        stat = self.video.stat()
        self.config = {"mode": "file", "path": str(self.video), "start": 900.0,
                       "duration": 1200.0, "video_duration": 2100.0, "block_index": 2,
                       "regions": [], "processing_settings": {},
                       "resume_source_time": 910.001,
                       "resume_video_size": stat.st_size,
                       "resume_video_mtime_ns": stat.st_mtime_ns,
                       "analysis_totals": {"frames_seen": 50, "frames_analyzed": 7,
                                           "ocr_calls": 3, "ocr_failures": 0,
                                           "events_created": 1, "events_deduplicated": 0}}
        self.store.create_session(self.session_id, "file", str(self.video), 900, 1200,
                                  "pt", dict(self.config), "paused")

    def tearDown(self):
        self.store.engine.dispose()
        self.temp.cleanup()

    def test_paused_block_survives_manager_restart_and_resumes_at_checkpoint(self):
        event_id, _ = self.store.add_event(self.session_id, dialogue(905))
        self.store.finish_event(event_id, 906)
        self.store.review(event_id, "accept")
        manager = SessionManager(self.store)
        self.assertEqual(self.store.session(self.session_id)["state"], "paused")
        self.assertAlmostEqual(manager.status(self.session_id)["current_source_time"], 910.001)
        self.assertEqual(manager.status(self.session_id)["progress"]["frames_seen"], 50)
        opened = []

        class Source:
            paused = False
            last_source_time = None

            def close(self):
                pass

        class Processor:
            def __init__(self, *_args, **_kwargs):
                pass

            def run(self, _source):
                return {"stage": "complete", "frames_seen": 1}

        def open_source(_path, _session, start, duration, **_kwargs):
            opened.append((start, duration))
            return Source()

        with patch("Dupsync.capture.session_manager.FileVideoSource", side_effect=open_source), \
             patch("Dupsync.capture.session_manager.DialogueProcessor", Processor), \
             patch("Dupsync.capture.session_manager.OcrEngine", return_value=object()):
            result = manager.resume_file(self.session_id)
            deadline = time.monotonic() + 5
            while self.store.session(self.session_id)["state"] != "complete" and time.monotonic() < deadline:
                time.sleep(.02)

        self.assertFalse(result["restarted_from_block_start"])
        self.assertEqual(opened, [(910.001, 1189.999)])
        self.assertEqual(self.store.session(self.session_id)["state"], "complete")
        self.assertIsNotNone(self.store.event(event_id))
        self.assertEqual(manager.status(self.session_id)["progress"]["frames_seen"], 51)

    def test_restart_removes_only_paused_block_events(self):
        other_session = str(uuid.uuid4())
        self.store.create_session(other_session, "file", str(self.video), 170, 730,
                                  "pt", {"mode": "file", "path": str(self.video)}, "complete")
        paused_id, _ = self.store.add_event(self.session_id, dialogue(905))
        other_id, _ = self.store.add_event(other_session, dialogue(180))
        self.store.finish_event(paused_id, 906)
        self.store.finish_event(other_id, 181)
        self.store.review(paused_id, "accept")
        self.store.review(other_id, "accept")
        manager = SessionManager(self.store)
        launched = threading.Event()
        launched_config = []

        def fake_run(_id, _dir, config, _language):
            launched_config.append(dict(config))
            manager._mark_idle()
            launched.set()

        with patch.object(manager, "_run_file", side_effect=fake_run):
            result = manager.restart_file_block(self.session_id)
            self.assertTrue(launched.wait(5))

        self.assertEqual(result["removed_events"], 1)
        self.assertTrue(result["restarted_from_block_start"])
        self.assertEqual(result["resume_source_time"], 900.0)
        self.assertIsNone(self.store.event(paused_id))
        self.assertIsNotNone(self.store.event(other_id))
        self.assertNotIn("resume_source_time", launched_config[0])
        self.assertNotIn("analysis_totals", launched_config[0])


if __name__ == "__main__":
    unittest.main()

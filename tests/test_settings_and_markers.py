from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Dupsync.app import settings as app_settings
from Dupsync.database.store import DubSyncStore


class SettingsTests(unittest.TestCase):
    def test_settings_are_bounded_and_atomically_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "settings.json"
            with patch.object(app_settings, "SETTINGS_PATH", target):
                saved = app_settings.save_settings({
                    "detection": {"analysis_fps": 99, "stable_observations": 0},
                    "preprocess": {"mode": "unknown", "contrast": 12},
                    "ocr": {"strip_trailing_hud_artifacts": True},
                    "calibration": {"duration_seconds": 60, "sample_count": 20},
                    "review": {"show_split_preview": False, "permanent_discard": True},
                })
                loaded = app_settings.load_settings()
            self.assertEqual(saved["detection"]["analysis_fps"], 15)
            self.assertEqual(saved["detection"]["stable_observations"], 1)
            self.assertEqual(saved["detection"]["change_threshold"], 2.4)
            self.assertEqual(saved["preprocess"]["mode"], "balanced")
            self.assertEqual(saved["preprocess"]["contrast"], 2.5)
            self.assertEqual(saved["preprocess"]["clahe"], True)
            self.assertEqual(loaded["calibration"]["duration_seconds"], 30)
            self.assertEqual(loaded["calibration"]["sample_count"], 10)
            self.assertTrue(loaded["ocr"]["strip_trailing_hud_artifacts"])
            self.assertFalse(saved["review"]["show_split_preview"])
            self.assertFalse(loaded["review"]["show_split_preview"])
            self.assertTrue(saved["review"]["permanent_discard"])
            self.assertTrue(loaded["review"]["permanent_discard"])


class TimelineMarkerTests(unittest.TestCase):
    def test_manual_audio_start_marker_is_persisted_and_deletable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DubSyncStore(Path(temporary) / "markers.sqlite3")
            session_id = "4a1df19e-4117-4fb0-88eb-bf248d2be13e"
            store.create_session(session_id, "file", "local.mp4", 20, 30, "en", {}, "complete")
            marker = store.add_marker(session_id, 24.125, "audio_start", "Início de áudio (futuro)")
            self.assertEqual(store.markers(session_id)[0]["source_time"], 24.125)
            self.assertEqual(store.markers(session_id)[0]["kind"], "audio_start")
            self.assertTrue(store.delete_marker(marker["marker_id"]))
            self.assertEqual(store.markers(session_id), [])
            store.engine.dispose()


class ManualEventTests(unittest.TestCase):
    def test_manual_dialogue_is_saved_as_a_distinct_non_ocr_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DubSyncStore(Path(temporary) / "manual.sqlite3")
            session_id = "99a73a68-6236-4f29-a115-a0fd4f730a20"
            store.create_session(session_id, "file", "local.mp4", 15, 30, "en", {}, "complete")

            first = store.add_manual_event(session_id, "The gate is open.", 21.25, 23.5, "Mara")
            second = store.add_manual_event(session_id, "The gate is open.", 21.25, 23.5)

            self.assertNotEqual(first["event_id"], second["event_id"])
            self.assertEqual(first["segment_id"], "manual")
            self.assertEqual(first["original_text"], "The gate is open.")
            self.assertEqual(first["normalized_text"], "the gate is open")
            self.assertEqual(first["ocr_observations_json"][0]["source"], "manual")
            self.assertEqual(first["timing_quality"], "manual")
            self.assertEqual(first["character"], "Mara")
            self.assertEqual(first["end_time"], 23.5)
            self.assertEqual(len(store.events(session_id)), 2)
            store.engine.dispose()


if __name__ == "__main__":
    unittest.main()

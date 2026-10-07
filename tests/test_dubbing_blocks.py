from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from Dupsync.database.store import DubSyncStore, utc_now
from Dupsync.database.models import DialogueLine, DialogueOccurrence, EventRecord, SessionRecord
from Dupsync.dubbing.service import DubbingError, DubbingService


class DubbingVideoBlockBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = DubSyncStore(self.root / "dubsync.sqlite3")
        self.service = DubbingService(self.store, self.root / "data")

    def tearDown(self) -> None:
        self.store.engine.dispose()
        self.temporary.cleanup()

    def test_schema_v6_database_can_be_reopened(self) -> None:
        database_path = self.root / "dubsync.sqlite3"
        self.store.engine.dispose()

        self.store = DubSyncStore(database_path)

        with self.store.engine.connect() as connection:
            version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            columns = {column["name"] for column in connection.exec_driver_sql(
                "PRAGMA table_info(voice_profiles)").mappings()}
        self.assertEqual(version, 6)
        self.assertIn("preview_text", columns)

    @staticmethod
    def _line(occurrence_id: str, character_id: str, takes: list[dict] | None = None,
              editorial_status: str = "accepted") -> dict:
        return {
            "line_id": f"LINE_{occurrence_id}", "occurrence_id": occurrence_id,
            "character_id": character_id, "editorial_status": editorial_status,
            "take_count": len(takes or []), "takes": takes or [],
            "character_verified": True, "voice_profile_id": "VOICE_1", "voice_ready": True,
            "character_name": "Warren", "emotion": "normal", "emotion_intensity": .7,
            "dub_language": "pt-BR",
        }

    def test_video_character_batch_covers_blocks_and_skips_completed_or_active_speech(self) -> None:
        video = self.root / "game.webm"
        sessions = {
            "block-1": {"session_id": "block-1", "source_type": "file", "source_uri": str(video),
                        "config_json": {"path": str(video)}, "project_id": "replaced"},
            "block-2": {"session_id": "block-2", "source_type": "file", "source_uri": str(video),
                        "config_json": {"downloaded_path": str(video)}, "project_id": "replaced"},
        }
        lines = [
            self._line("done", "char-1", [{"status": "review"}]),
            self._line("active", "char-1", [{"status": "queued"}]),
            self._line("error", "char-1", [{"status": "error"}]),
            self._line("new", "char-1"),
            self._line("other", "char-2"),
            self._line("pending", "char-1", editorial_status="pending"),
        ]
        with patch.object(self.store, "session", side_effect=lambda sid: sessions.get(sid)), \
             patch.object(self.service, "list_lines", return_value=lines) as list_lines, \
             patch.object(self.service, "create_take", side_effect=lambda *args, **kwargs: {"job": kwargs}) as create:
            result = self.service.enqueue_batch(
                "block-1", mode="video_character", character_id="char-1",
                session_ids=["block-1", "block-2"],
            )

        self.assertEqual(list_lines.call_args.kwargs["session_id"], ["block-1", "block-2"])
        self.assertEqual(result["mode"], "video_character")
        self.assertEqual(result["eligible"], 2)
        self.assertEqual(result["already_done"], 1)
        self.assertEqual(result["already_active"], 1)
        self.assertEqual(result["queued"], 2)
        self.assertEqual(create.call_count, 2)
        self.assertTrue(all(call.kwargs["batch_mode"] == "video_character" for call in create.call_args_list))

    def test_video_character_batch_rejects_sessions_from_another_source(self) -> None:
        one = self.root / "one.webm"
        two = self.root / "two.webm"
        sessions = {
            "block-1": {"session_id": "block-1", "source_type": "file", "source_uri": str(one),
                        "config_json": {"path": str(one)}, "project_id": "replaced"},
            "other-video": {"session_id": "other-video", "source_type": "file", "source_uri": str(two),
                            "config_json": {"path": str(two)}, "project_id": "replaced"},
        }
        with patch.object(self.store, "session", side_effect=lambda sid: sessions.get(sid)), \
             patch.object(self.service, "create_take") as create:
            with self.assertRaises(DubbingError) as context:
                self.service.enqueue_batch(
                    "block-1", mode="video_character", character_id="char-1",
                    session_ids=["block-1", "other-video"],
                )

        self.assertEqual(context.exception.code, "video_batch_session_mismatch")
        create.assert_not_called()

    def test_block_progress_counts_distinct_eligible_occurrences_and_take_states(self) -> None:
        database = MagicMock()
        database.execute.return_value = [
            ("block-1", "line-a", "accepted", "review"),
            ("block-1", "line-a", "accepted", "approved"),
            ("block-1", "line-b", "corrected", "queued"),
            ("block-2", "line-c", "accepted", None),
            ("block-2", "line-d", "accepted", "error"),
        ]
        session_scope = MagicMock()
        session_scope.__enter__.return_value = database
        session_scope.__exit__.return_value = False

        with patch.object(self.store, "Session", return_value=session_scope):
            progress = self.service.block_dubbing_progress(["block-1", "block-2"], "char-1")

        self.assertEqual(progress["block-1"], {
            "eligible": 2, "finished": 1, "queued": 1, "generating": 0, "errors": 0, "percent": 50.0,
        })
        self.assertEqual(progress["block-2"], {
            "eligible": 2, "finished": 0, "queued": 0, "generating": 0, "errors": 1, "percent": 0,
        })

    def test_event_occurrence_ids_returns_unique_linked_occurrences_for_each_event(self) -> None:
        database = MagicMock()
        database.execute.return_value.all.return_value = [
            ("event-1", "OCC_A1B2C3D4"),
            ("event-1", "OCC_A1B2C3D4"),
            ("event-1", "OCC_E5F6G7H8"),
            ("event-2", "OCC_I9J0K1L2"),
        ]
        session_scope = MagicMock()
        session_scope.__enter__.return_value = database
        session_scope.__exit__.return_value = False

        with patch.object(self.store, "Session", return_value=session_scope):
            result = self.store.event_occurrence_ids(["event-1", "event-2", "event-3", "event-1"])

        self.assertEqual(result, {
            "event-1": ["OCC_A1B2C3D4", "OCC_E5F6G7H8"],
            "event-2": ["OCC_I9J0K1L2"],
            "event-3": [],
        })

    def test_capture_correction_syncs_linked_dub_text_but_preserves_manual_adaptation(self) -> None:
        project_id = self.service.project_id
        previous_event = {
            "original_text": "OCR original", "processed_text": "Texto anterior",
            "corrected_text": None, "review_status": "accepted", "emotion": "normal",
        }
        event = SimpleNamespace(
            event_id="event-1", session_id="session-1", character_profile_id=None,
            original_text="OCR original", processed_text="Texto anterior",
            corrected_text="Texto corrigido", review_status="corrected", emotion="sad",
        )
        session = SimpleNamespace(project_id=project_id)
        generated_from_capture = SimpleNamespace(
            project_id=project_id, source_text="OCR original", dub_text="Texto anterior",
            editorial_status="accepted", character_id="character-1", emotion="normal", updated_at=None,
        )
        manually_adapted = SimpleNamespace(
            project_id=project_id, source_text="OCR original", dub_text="Adaptação manual para dublagem",
            editorial_status="accepted", character_id="character-1", emotion="normal", updated_at=None,
        )
        links = [SimpleNamespace(occurrence_id="occ-1"), SimpleNamespace(occurrence_id="occ-2")]
        occurrences = {"occ-1": SimpleNamespace(line_id="line-1"),
                       "occ-2": SimpleNamespace(line_id="line-2")}
        lines = {"line-1": generated_from_capture, "line-2": manually_adapted}
        database = MagicMock()
        database.get.side_effect = lambda model, key: {
            (EventRecord, "event-1"): event,
            (SessionRecord, "session-1"): session,
            (DialogueOccurrence, "occ-1"): occurrences["occ-1"],
            (DialogueOccurrence, "occ-2"): occurrences["occ-2"],
            (DialogueLine, "line-1"): lines["line-1"],
            (DialogueLine, "line-2"): lines["line-2"],
        }.get((model, key))
        database.scalars.return_value.all.return_value = links
        session_scope = MagicMock()
        session_scope.__enter__.return_value = database
        session_scope.__exit__.return_value = False

        with patch.object(self.store.Session, "begin", return_value=session_scope):
            self.service.sync_reviewed_event_character(
                "event-1", None, None, None, previous_event=previous_event,
                sync_character=False, sync_emotion=True,
            )

        self.assertEqual(generated_from_capture.dub_text, "Texto corrigido")
        self.assertEqual(generated_from_capture.editorial_status, "corrected")
        self.assertEqual(generated_from_capture.emotion, "sad")
        self.assertEqual(manually_adapted.dub_text, "Adaptação manual para dublagem")
        self.assertEqual(manually_adapted.editorial_status, "corrected")

    def test_initialize_reconciles_existing_capture_correction(self) -> None:
        event = SimpleNamespace(
            original_text="OCR original", processed_text="Texto antigo",
            corrected_text="Texto corrigido", review_status="corrected",
        )
        imported_line = SimpleNamespace(
            source_text="OCR original", dub_text="Texto antigo", editorial_status="accepted", updated_at=None,
        )
        adapted_line = SimpleNamespace(
            source_text="OCR original", dub_text="Adaptação manual", editorial_status="accepted", updated_at=None,
        )

        self.service._refresh_existing_capture_line(imported_line, event, utc_now())
        self.service._refresh_existing_capture_line(adapted_line, event, utc_now())

        self.assertEqual(imported_line.dub_text, "Texto corrigido")
        self.assertEqual(imported_line.editorial_status, "corrected")
        self.assertEqual(adapted_line.dub_text, "Adaptação manual")
        self.assertEqual(adapted_line.editorial_status, "corrected")


if __name__ == "__main__":
    unittest.main()

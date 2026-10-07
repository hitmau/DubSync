from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from Dupsync.database.models import (CaptureLink, DialogueLine, DialogueOccurrence,
                                     DubbingProject, ReviewRecord, SessionRecord)
from Dupsync.database.store import DubSyncStore
from Dupsync.projects.context import reset_project_context, set_project_context
from Dupsync.projects.service import ProjectError, ProjectService


TINY_PNG = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c636000020000050001a5f645400000000049454e44ae426082"
)).decode("ascii")


class ProjectHubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dubsync-projects-")
        self.root = Path(self.temp.name)
        self.store = DubSyncStore(self.root / "dubsync.sqlite3")
        self.service = ProjectService(self.store, self.root)

    def tearDown(self):
        self.store.engine.dispose()
        self.temp.cleanup()

    def create_project(self, **changes):
        body = {
            "name": "Aventura Nova", "game_title": "Aventura", "slug": "aventura-nova",
            "game_version": "1.0", "platform": "PC", "narrative_type": "branching",
            "original_language": "en", "capture_language": "pt-BR", "dub_language": "pt-BR",
        }
        body.update(changes)
        return self.service.create_project(body)

    def test_new_project_keeps_its_assets_and_capture_context_separate(self):
        project = self.create_project(cover_data=f"data:image/png;base64,{TINY_PNG}")
        project_root = self.root / "projects" / "aventura-nova"
        self.assertTrue((project_root / "project.json").is_file())
        self.assertTrue((project_root / "assets" / "cover.png").is_file())
        self.assertEqual(project["summary"]["dialogues"], 0)

        token = set_project_context(project["project_id"])
        try:
            self.store.create_session("12345678-1234-1234-1234-123456789012", "file",
                                      "game.mp4", 0, 30, "pt", {}, "complete")
            sessions = self.store.sessions()
            self.assertEqual([row["project_id"] for row in sessions], [project["project_id"]])
            self.assertEqual(self.store.events(), [])
        finally:
            reset_project_context(token)

        with self.store.Session() as db:
            session = db.scalar(select(SessionRecord).where(
                SessionRecord.session_id == "12345678-1234-1234-1234-123456789012"))
            self.assertEqual(session.project_id, project["project_id"])

    def test_archiving_is_blocked_during_project_capture_and_can_be_restored(self):
        project = self.create_project()
        token = set_project_context(project["project_id"])
        try:
            self.store.create_session("12345678-1234-1234-1234-123456789012", "file",
                                      "game.mp4", 0, 30, "pt", {}, "running")
        finally:
            reset_project_context(token)
        with self.assertRaises(ProjectError):
            self.service.set_status(project["project_id"], "archived")
        self.store.set_session_state("12345678-1234-1234-1234-123456789012", "complete")
        self.service.set_status(project["project_id"], "archived")
        self.assertEqual(self.service.get_project(project["project_id"])["status"], "archived")
        self.service.set_status(project["project_id"], "active")
        self.assertEqual(self.service.get_project(project["project_id"])["status"], "active")

    def test_project_catalog_starts_with_a_legacy_project(self):
        legacy = self.service.get_project("replaced")
        self.assertEqual(legacy["name"], "REPLACED")
        self.assertEqual(legacy["status"], "active")
        with self.assertRaises(Exception):
            self.create_project(slug="replaced")

    def test_project_progress_reports_current_stage_and_counts_discarded_dialogues_as_reviewed(self):
        project = self.create_project()
        token = set_project_context(project["project_id"])
        try:
            session_id = "12345678-1234-1234-1234-123456789012"
            self.store.create_session(session_id, "file", "game.mp4", 0, 120, "en", {}, "complete")
            first = self.store.add_manual_event(session_id, "First line", 1, 2)
            second = self.store.add_manual_event(session_id, "Second line", 3, 4)

            summary = self.service.get_project(project["project_id"])["summary"]
            self.assertEqual(summary["current_stage"], "review")
            self.assertEqual(summary["capture_progress_percent"], 100.0)
            self.assertEqual(summary["review_progress_percent"], 0.0)
            self.assertEqual(summary["progress_percent"], 33.3)

            self.store.bulk_accept([first["event_id"]])
            self.store.bulk_discard([second["event_id"]])
            summary = self.service.get_project(project["project_id"])["summary"]
            self.assertEqual(summary["resolved_dialogues"], 2)
            self.assertEqual(summary["review_progress_percent"], 100.0)
            self.assertEqual(summary["current_stage"], "dubbing")
            self.assertEqual(summary["progress_percent"], 66.7)
        finally:
            reset_project_context(token)

    def test_project_text_replacement_previews_and_updates_all_active_sessions(self):
        project = self.create_project()
        token = set_project_context(project["project_id"])
        try:
            first_session = "12345678-1234-1234-1234-123456789012"
            second_session = "22345678-1234-1234-1234-123456789012"
            self.store.create_session(first_session, "file", "game-a.mp4", 0, 30, "en", {}, "complete")
            self.store.create_session(second_session, "file", "game-b.mp4", 0, 30, "en", {}, "complete")
            first = self.store.add_manual_event(first_session, "The R.E.A.C.H. signal: R.E.A.C.H.", 1, 4)
            second = self.store.add_manual_event(second_session, "Ask R.E.A.C.H. for help.", 5, 7)
            discarded = self.store.add_manual_event(second_session, "Keep R.E.A.C.H. archived.", 8, 10)
            self.store.bulk_discard([discarded["event_id"]])

            now = datetime.now(timezone.utc)
            with self.store.Session.begin() as db:
                line = DialogueLine(
                    line_id="LINE-test-replace", project_id=project["project_id"],
                    source_language="en", dub_language="pt-BR", source_text=first["original_text"],
                    dub_text=first["original_text"], direction="", emotion=None,
                    emotion_intensity=.7, editorial_status="pending", created_at=now, updated_at=now,
                )
                occurrence = DialogueOccurrence(
                    occurrence_id="OCC-test-replace", line_id=line.line_id, scene_id=None,
                    route_id=None, local_sequence=1, condition_json={}, target_duration_ms=3000,
                    runtime_trigger_id=None, runtime_trigger_type=None, status="unmapped", created_at=now,
                )
                link = CaptureLink(
                    capture_link_id="CAP-test-replace", occurrence_id=occurrence.occurrence_id,
                    traversal_id=None, session_id=first_session, event_id=first["event_id"],
                    source_start_time=1, source_end_time=4, confidence=1, is_primary=True, created_at=now,
                )
                db.add_all([line, occurrence, link])

            preview = self.store.preview_project_text_replacement("R.E.A.C.H.", "Reach")
            self.assertEqual((preview["dialogues"], preview["occurrences"], preview["dubbing_lines"]), (2, 3, 1))
            result = self.store.replace_project_dialogue_text("R.E.A.C.H.", "Reach")
            self.assertEqual((result["dialogues"], result["occurrences"], result["dubbing_lines"]), (2, 3, 1))
            updated = self.store.event(first["event_id"])
            self.assertEqual(updated["corrected_text"], "The Reach signal: Reach")
            self.assertEqual(updated["original_text"], first["original_text"])
            self.assertEqual(updated["review_status"], "corrected")
            self.assertIsNone(self.store.event(discarded["event_id"])["corrected_text"])
            with self.store.Session() as db:
                saved_line = db.get(DialogueLine, line.line_id)
                actions = db.scalars(select(ReviewRecord.action).where(
                    ReviewRecord.event_id.in_((first["event_id"], second["event_id"])),
                    ReviewRecord.action == "bulk_replace",
                )).all()
                self.assertEqual(saved_line.dub_text, "The Reach signal: Reach")
                self.assertEqual(saved_line.source_text, first["original_text"])
                self.assertEqual(len(actions), 2)
        finally:
            reset_project_context(token)


if __name__ == "__main__":
    unittest.main()

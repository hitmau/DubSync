from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from Dupsync.database.store import DubSyncStore
from Dupsync.events.replacements import apply_text_replacements, clean_replacement_rules
from Dupsync.projects.context import reset_project_context, set_project_context
from Dupsync.projects.service import ProjectService


class TextReplacementTests(unittest.TestCase):
    def test_rules_replace_literal_text_case_insensitively_in_order(self) -> None:
        rules = clean_replacement_rules([
            {"find_text": "R.E.A.C.H", "replace_text": "Reach"},
            {"find_text": "maurici", "replace_text": "mauricio"},
        ])

        result = apply_text_replacements("R.E.A.C.H says maurici. r.e.a.c.h!", rules)

        self.assertEqual(result, "Reach says mauricio. Reach!")

    def test_empty_target_removes_match_and_blank_rows_are_ignored(self) -> None:
        rules = clean_replacement_rules([
            {"find_text": "HUD", "replace_text": ""},
            {"find_text": "", "replace_text": ""},
        ])

        self.assertEqual(apply_text_replacements("Line HUD", rules), "Line ")
        self.assertEqual(len(rules), 1)

    def test_duplicate_search_terms_are_rejected_case_insensitively(self) -> None:
        with self.assertRaisesRegex(ValueError, "repete"):
            clean_replacement_rules([
                {"find_text": "Reach", "replace_text": "Reach"},
                {"find_text": "REACH", "replace_text": "R.E.A.C.H"},
            ])

    def test_rules_persist_per_project_and_are_available_from_session(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dubsync-text-rules-") as temporary:
            root = Path(temporary)
            store = DubSyncStore(root / "dubsync.sqlite3")
            service = ProjectService(store, root)
            project = service.create_project({
                "name": "Aventura Nova", "game_title": "Aventura", "slug": "aventura-nova",
                "game_version": "1.0", "platform": "PC", "narrative_type": "branching",
                "original_language": "en", "capture_language": "en", "dub_language": "pt-BR",
            })
            session_id = "12345678-1234-1234-1234-123456789012"
            token = set_project_context(project["project_id"])
            try:
                store.create_session(session_id, "file", "game.mp4", 0, 60, "en", {}, "complete")
                saved = store.save_project_text_replacements([
                    {"find_text": "R.E.A.C.H", "replace_text": "Reach"},
                    {"find_text": "maurici", "replace_text": "mauricio"},
                ])
                self.assertEqual(store.project_text_replacements(), saved)
            finally:
                reset_project_context(token)

            self.assertEqual(store.session_text_replacements(session_id), saved)
            store.engine.dispose()


if __name__ == "__main__":
    unittest.main()

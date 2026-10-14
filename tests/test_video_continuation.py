"""A second file keeps the same project block sequence without altering old blocks."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Dupsync.capture.session_manager import SessionManager
from Dupsync.capture.video_plan import build_video_plan, estimated_block_total


def block(number: int, path: str, start: float, duration: float, video_duration: float,
          previous: str | None = None) -> dict:
    config = {"path": path, "duration": duration, "video_duration": video_duration,
              "block_index": number, "block_duration_requested": 1200,
              "regions": [{"roi_id": "dialogue"}]}
    if previous:
        config["previous_session_id"] = previous
    return {"session_id": f"block-{number}", "source_type": "file", "state": "complete",
            "source_uri": path, "source_start": start, "requested_duration": duration,
            "language": "pt", "created_at": f"2026-10-12T00:{number:02d}:00",
            "config_json": config}


class MemoryStore:
    def __init__(self, rows: list[dict]):
        self.rows = {row["session_id"]: copy.deepcopy(row) for row in rows}

    def session(self, session_id: str) -> dict | None:
        row = self.rows.get(session_id)
        return copy.deepcopy(row) if row else None

    def sessions(self, limit: int = 25) -> list[dict]:
        return [copy.deepcopy(row) for row in list(self.rows.values())[:limit]]

    def update_session_config(self, session_id: str, config: dict) -> None:
        self.rows[session_id]["config_json"] = copy.deepcopy(config)


class VideoContinuationTests(unittest.TestCase):
    def test_planning_from_first_block_predicts_eighth_for_next_video(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "part-1.mp4"
            second = Path(folder) / "part-2.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            store = MemoryStore([block(1, str(first), 0, 1200, 8400)])
            manager = object.__new__(SessionManager)
            manager.store = store
            with patch("Dupsync.capture.session_manager.probe_video_duration", return_value=15600):
                result = manager.plan_next_video("block-1", str(second), 1200)
            self.assertEqual(result["first_block_index"], 8)

    def test_second_video_expands_project_from_seven_to_twenty_blocks(self) -> None:
        rows = [block(index, "part-1.mp4", (index - 1) * 1200, 1200, 8400,
                      f"block-{index-1}" if index > 1 else None) for index in range(1, 8)]
        rows[0]["config_json"]["planned_next_video"] = {
            "path": "part-2.mp4", "duration": 15600, "block_duration_seconds": 1200}
        plan = build_video_plan(copy.deepcopy(rows), "block-1")
        self.assertEqual(estimated_block_total(plan), 20)
        self.assertEqual(plan["video_duration"], 24000)
        self.assertEqual([sector["offset"] for sector in plan["sectors"]], [0, 8400])
        rows[0]["config_json"]["planned_next_video"]["block_duration_seconds"] = 900
        different_length = build_video_plan(copy.deepcopy(rows), "block-1")
        self.assertEqual(estimated_block_total(different_length), 25)
        rows[0]["config_json"]["planned_next_video"]["block_duration_seconds"] = 1200

        rows.append(block(8, "part-2.mp4", 0, 1200, 15600, "block-7"))
        rows[-1]["config_json"]["sector_index"] = 2
        plan = build_video_plan(copy.deepcopy(rows), "block-1")
        self.assertEqual(estimated_block_total(plan), 20)
        self.assertEqual(plan["sessions"][-1]["project_start"], 8400)
        self.assertEqual(plan["sessions"][-1]["config_json"]["block_index"], 8)
        self.assertFalse(plan["sectors"][-1]["planned"])

    def test_planning_and_starting_second_file_preserves_previous_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder) / "part-1.mp4"
            second = Path(folder) / "part-2.mp4"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            original = block(7, str(first), 7200, 1200, 8400)
            store = MemoryStore([original])
            manager = object.__new__(SessionManager)
            manager.store = store
            started = []
            manager.start_file = lambda *args, **kwargs: (started.append((args, kwargs)) or {
                "session_id": "block-8", "start": 0, "duration": 1200, "block_index": 8})
            with patch("Dupsync.capture.session_manager.probe_video_duration", return_value=15600):
                planned = manager.plan_next_video("block-7", str(second), 900)
                self.assertEqual(planned["first_block_index"], 8)
                result = manager.next_file_video("block-7")
            self.assertEqual(result["block_index"], 8)
            self.assertEqual(started[0][0][:3], (str(second.resolve()), 0, 900))
            self.assertEqual(started[0][1]["continuation"]["previous_session_id"], "block-7")
            self.assertEqual(started[0][1]["continuation"]["sector_index"], 2)
            self.assertEqual(store.session("block-7")["state"], "complete")
            self.assertEqual(store.session("block-7")["config_json"]["duration"], 1200)
            with self.assertRaisesRegex(ValueError, "já está na sequência"):
                manager.plan_next_video("block-7", str(first), 1200)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import tempfile
import unittest
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Dupsync.capture.models import FramePacket
from Dupsync.capture.session_manager import SessionManager
from Dupsync.capture.youtube_source import youtube_watch_url
from Dupsync.database.store import DubSyncStore
from Dupsync.database.models import ReviewRecord
from Dupsync.detector.visual import Region
from Dupsync.events.processor import DialogueProcessor
from Dupsync.events.tracker import DialogueTracker
from Dupsync.ocr.engine import OcrLine
from Dupsync.ocr.text_cleanup import strip_trailing_hud_artifacts


def packet(at: float) -> FramePacket:
    return FramePacket(np.zeros((100, 240, 3), dtype=np.uint8), "test", "test-segment", int(at * 30),
                       at, datetime.now(timezone.utc), int(at * 1_000_000_000))


class DialogueTrackerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker = DialogueTracker(Region(0, 0, 1, 1, "dialogue"))

    def test_progressive_text_becomes_one_stable_event_and_ends_on_first_absence(self) -> None:
        drafts = []
        for at, text in ((1.0, "WE"), (1.1, "WE NEED"), (1.2, "WE NEED TO LEAVE.")):
            actions = self.tracker.observe(packet(at), [OcrLine(text, .94, (20, 65, 180, 22))])
            drafts.extend(action for action in actions if action["kind"] == "start")
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0]["original_text"], "WE NEED TO LEAVE.")
        self.assertEqual(drafts[0]["start_time"], 1.0)

        self.assertEqual(self.tracker.observe(packet(1.8), []), [])
        ended = self.tracker.observe(packet(1.9), [])
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["kind"], "end")
        self.assertEqual(ended[0]["end_time"], 1.8)

    def test_reordered_progressive_ocr_keeps_active_dialogue(self) -> None:
        first = "R.E.A.C.H. foi regi Warren,"
        for at in (1.0, 1.1, 1.2):
            self.tracker.observe(packet(at), [OcrLine(first, .85, (20, 65, 180, 22))])
        longer = "R.E.A.C.H. Warren, foi registrada uma hora"
        actions = self.tracker.observe(packet(1.3), [OcrLine(longer, .96, (20, 65, 210, 22))])
        self.assertEqual(actions, [])
        self.assertEqual(self.tracker.active.candidate.text, longer)
        self.tracker.observe(packet(2.0), [])
        ended = self.tracker.observe(packet(2.1), [])
        self.assertEqual(ended[0]["final_candidate"].text, longer)

    def test_very_low_confidence_character_does_not_start_event(self) -> None:
        for at, text, confidence in ((1.0, "定", .15), (1.1, "定", .56),
                                     (1.2, "jer", .74), (1.3, "定", .56)):
            self.assertEqual(self.tracker.observe(packet(at),
                             [OcrLine(text, confidence, (20, 65, 28, 78))]), [])
        self.assertIsNone(self.tracker.active)

    def test_hud_suffix_cleanup_keeps_raw_ocr_and_cleans_processed_text(self) -> None:
        self.assertEqual(strip_trailing_hud_artifacts("Escapar deve ser a nossa prioridade. ♫"),
                         "Escapar deve ser a nossa prioridade.")
        self.assertEqual(strip_trailing_hud_artifacts(
            "Você é de extrema importância. 5D"), "Você é de extrema importância.")
        self.assertEqual(strip_trailing_hud_artifacts("É mais tranquilo aqui. Sons, cheiros, luzes. >"),
                         "É mais tranquilo aqui. Sons, cheiros, luzes.")
        self.assertEqual(strip_trailing_hud_artifacts("A unidade R2 segue pela ponte!"),
                         "A unidade R2 segue pela ponte!")

        tracker = DialogueTracker(Region(0, 0, 1, 1, "dialogue"), stable_frames=1,
                                  strip_trailing_hud=True)
        raw = "Escapar deve ser a nossa prioridade. ♫"
        actions = tracker.observe(packet(1.0), [OcrLine(raw, .94, (20, 65, 180, 22))])
        event = next(action for action in actions if action["kind"] == "start")
        self.assertEqual(event["original_text"], raw)
        self.assertEqual(event["processed_text"], "Escapar deve ser a nossa prioridade.")
        self.assertEqual(event["ocr_observations"][0]["text"], raw)


class StoreSplitTests(unittest.TestCase):
    def test_reanalysis_preserves_reviewed_dialogues_and_only_replaces_pending(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "reanalysis.sqlite3")
            video = Path(temp) / "source.webm"
            video.write_bytes(b"video")
            old_session, new_session = str(uuid.uuid4()), str(uuid.uuid4())
            config = {"mode": "file", "path": str(video)}
            store.create_session(old_session, "file", str(video), 0, 10, "pt", config, "complete")
            store.create_session(new_session, "file", str(video), 0, 10, "pt", config, "running")
            old_ids = []
            for at in (2, 4, 6):
                event_id, _ = store.add_event(old_session, {
                    "start_time": at, "original_text": f"Fala {at}",
                    "normalized_text": f"fala {at}", "confidence": .9,
                    "bbox_json": [1, 1, 80, 20], "text_phash": f"{at:02x}" * 8,
                })
                store.finish_event(event_id, at + 1)
                old_ids.append(event_id)
            store.review(old_ids[0], "accept")
            store.review(old_ids[1], "correct", {"corrected_text": "Fala revisada"})

            scope = {"source_path": str(video), "start": 0, "end": 10}
            for at in (2.1, 4.1, 6.1):
                store.add_event(new_session, {
                    "start_time": at, "original_text": f"Nova fala {at}",
                    "normalized_text": f"nova fala {at}", "confidence": .9,
                    "bbox_json": [1, 1, 80, 20], "text_phash": f"{int(at):02x}" * 8,
                }, scope)

            self.assertEqual(store.event(old_ids[0])["review_status"], "accepted")
            self.assertEqual(store.event(old_ids[1])["corrected_text"], "Fala revisada")
            self.assertIsNone(store.event(old_ids[2]))
            store.engine.dispose()

    def test_accept_saves_review_fields_and_keeps_original_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "review.sqlite3")
            session_id = str(uuid.uuid4())
            store.create_session(session_id, "file", "local.mp4", 0, 15, "en", {"mode": "file"}, "complete")
            event_id, _ = store.add_event(session_id, {
                "start_time": 2.0, "original_text": "N4RA", "normalized_text": "n4ra",
                "confidence": .9, "bbox_json": [1, 2, 40, 12], "text_phash": "ab" * 8,
                "context_phash": "cd" * 8,
            })
            store.finish_event(event_id, 4.0)
            accepted = store.review(event_id, "accept", {"character": "Mara", "corrected_text": "Nara",
                                                         "emotion": "Medo", "start_time": 2.1, "end_time": 3.9})
            self.assertEqual(accepted["review_status"], "accepted")
            self.assertEqual(accepted["character"], "Mara")
            self.assertEqual(accepted["corrected_text"], "Nara")
            self.assertEqual(accepted["original_text"], "N4RA")
            self.assertEqual((accepted["start_time"], accepted["end_time"]), (2.1, 3.9))
            store.engine.dispose()

    def test_split_preserves_ocr_and_commits_new_visual_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "test.sqlite3")
            session_id = str(uuid.uuid4())
            store.create_session(session_id, "file", "local.mp4", 0, 15, "en", {"mode": "file"}, "complete")
            event_id, duplicate = store.add_event(session_id, {
                "start_time": 2.0, "original_text": "Stay close.", "normalized_text": "stay close",
                "confidence": .95, "bbox_json": [15, 60, 100, 20], "text_phash": "ab" * 8,
                "context_phash": "cd" * 8, "ocr_observations": [{"source_time": 2.1, "text": "Stay close."}],
                "assets": [{"kind": "start", "path": "images/start.png", "source_time": 2.0}],
            })
            self.assertFalse(duplicate)
            store.finish_event(event_id, 6.0)
            first_assets = [{"kind": "start", "path": "images/start.png", "source_time": 2.0},
                            {"kind": "end", "path": "images/split-end.png", "source_time": 4.0}]
            second_assets = [{"kind": "pre_250", "path": "images/split-before.png", "source_time": 3.75},
                             {"kind": "start", "path": "images/split-start.png", "source_time": 4.0}]
            first, second = store.split(event_id, 4.0, first_assets, second_assets,
                                        "images/split-crop.png", {"frame_id": 120, "segment_id": "file-1"})

            self.assertEqual(first["end_time"], 4.0)
            self.assertEqual(first["end_frame_path"], "images/split-end.png")
            self.assertEqual(second["start_time"], 4.0)
            self.assertEqual(second["end_time"], 6.0)
            self.assertEqual(second["crop_path"], "images/split-crop.png")
            self.assertEqual(second["before_frame_path"], "images/split-before.png")
            self.assertEqual(second["ocr_observations_json"], [{"source_time": 2.1, "text": "Stay close."}])
            self.assertEqual([item["event_id"] for item in store.events(session_id)], [event_id, second["event_id"]])
            store.engine.dispose()


class StoreConfidenceAndBulkActionTests(unittest.TestCase):
    def test_low_confidence_filter_and_bulk_discard_keep_review_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "events.sqlite3")
            session_id = str(uuid.uuid4())
            store.create_session(session_id, "file", "local.mp4", 0, 20, "en", {"mode": "file"}, "complete")

            def add(start: float, text: str, confidence: float) -> str:
                event_id, _ = store.add_event(session_id, {
                    "start_time": start, "original_text": text, "normalized_text": text.lower(),
                    "confidence": confidence, "bbox_json": [1, 1, 80, 20],
                    "text_phash": f"{int(start):02x}" * 8,
                })
                store.finish_event(event_id, start + 1)
                return event_id

            red = add(1, "Low confidence red", .55)
            normal = add(4, "Regular yellow", .88)
            self.assertEqual([row["event_id"] for row in store.events(session_id, "low-confidence")], [red])
            self.assertEqual(store.bulk_discard([red, normal, red]), 2)
            self.assertEqual(store.event(red)["review_status"], "discarded")
            self.assertEqual([row["event_id"] for row in store.events(session_id, "discarded")], [red, normal])
            with store.Session() as db:
                actions = db.scalars(select(ReviewRecord.action).where(
                    ReviewRecord.event_id == red, ReviewRecord.action == "bulk_discard"
                )).all()
            self.assertEqual(actions, ["bulk_discard"])
            store.engine.dispose()


class EventFiltersAndPermanentDeletionTests(unittest.TestCase):
    def test_character_and_emotion_filters_and_options(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "filters.sqlite3")
            session_id = str(uuid.uuid4())
            store.create_session(session_id, "file", "local.mp4", 0, 20, "en", {"mode": "file"}, "complete")
            event_ids = []
            for start, text in ((1, "Tagged"), (4, "Unassigned")):
                event_id, _ = store.add_event(session_id, {
                    "start_time": start, "original_text": text, "normalized_text": text.lower(),
                    "confidence": .9, "bbox_json": [1, 1, 80, 20], "text_phash": f"{start:02x}" * 8,
                })
                store.finish_event(event_id, start + 1)
                event_ids.append(event_id)
            store.review(event_ids[0], "update", {"character": "Mara", "emotion": "Fear"})

            self.assertEqual([item["event_id"] for item in store.events(session_id, character="Mara")], [event_ids[0]])
            self.assertEqual([item["event_id"] for item in store.events(session_id, emotion="Fear")], [event_ids[0]])
            self.assertEqual([item["event_id"] for item in store.events(session_id, character="__none__")], [event_ids[1]])
            self.assertEqual(store.event_filter_options(session_id), {
                "characters": ["Mara"], "emotions": ["Fear"],
                "has_unassigned_character": True, "has_unassigned_emotion": True,
            })
            store.engine.dispose()

    def test_permanent_delete_removes_review_history_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "delete.sqlite3")
            session_id = str(uuid.uuid4())
            store.create_session(session_id, "file", "local.mp4", 0, 20, "en", {"mode": "file"}, "complete")
            image = store.sessions_root / session_id / "images" / "shared.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"shared visual asset")
            event_id, _ = store.add_event(session_id, {
                "start_time": 1, "original_text": "Remove me", "normalized_text": "remove me",
                "confidence": .9, "bbox_json": [1, 1, 80, 20], "text_phash": "ab" * 8,
                "crop_path": "images/shared.png",
            })
            store.finish_event(event_id, 2)
            store.review(event_id, "accept")
            second_id, _ = store.add_event(session_id, {
                "start_time": 4, "original_text": "Keep me", "normalized_text": "keep me",
                "confidence": .9, "bbox_json": [1, 1, 80, 20], "text_phash": "cd" * 8,
                "crop_path": "images/shared.png",
            })
            store.finish_event(second_id, 5)

            result = store.permanently_delete_event(event_id)

            self.assertTrue(result["deleted"])
            self.assertIsNone(store.event(event_id))
            self.assertTrue(image.exists(), "a visual asset still referenced by another dialogue must be preserved")
            with store.Session() as db:
                self.assertEqual(db.scalars(select(ReviewRecord).where(ReviewRecord.event_id == event_id)).all(), [])
            store.permanently_delete_event(second_id)
            self.assertFalse(image.exists(), "an unreferenced visual asset must be deleted with its final dialogue")
            store.engine.dispose()

    def test_bulk_accept_changes_status_and_records_review_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "events.sqlite3")
            session_id = str(uuid.uuid4())
            store.create_session(session_id, "file", "local.mp4", 0, 20, "en", {"mode": "file"}, "complete")
            event_ids = []
            for start, text in ((1, "First line"), (4, "Second line")):
                event_id, _ = store.add_event(session_id, {
                    "start_time": start, "original_text": text, "normalized_text": text.lower(),
                    "confidence": .9, "bbox_json": [1, 1, 80, 20], "text_phash": f"{start:02x}" * 8,
                })
                store.finish_event(event_id, start + 1)
                event_ids.append(event_id)

            self.assertEqual(store.bulk_accept([event_ids[0], event_ids[1], event_ids[0]]), 2)
            self.assertEqual([store.event(event_id)["review_status"] for event_id in event_ids], ["accepted", "accepted"])
            with store.Session() as db:
                actions = db.scalars(select(ReviewRecord.action).where(
                    ReviewRecord.event_id.in_(event_ids), ReviewRecord.action == "bulk_accept"
                )).all()
            self.assertEqual(actions, ["bulk_accept", "bulk_accept"])
            store.engine.dispose()


class StoreMergeTests(unittest.TestCase):
    def test_events_from_different_sessions_cannot_be_merged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "test.sqlite3")
            first_session, second_session = str(uuid.uuid4()), str(uuid.uuid4())
            store.create_session(first_session, "file", "first.mp4", 0, 10, "en", {})
            store.create_session(second_session, "file", "second.mp4", 0, 10, "en", {})
            def add(session_id: str, text: str) -> str:
                event_id, _ = store.add_event(session_id, {
                    "start_time": 1.0, "original_text": text, "normalized_text": text.lower(),
                    "confidence": .9, "bbox_json": [1, 1, 80, 20], "text_phash": "12" * 8,
                })
                store.finish_event(event_id, 2.0)
                return event_id
            first = add(first_session, "First line")
            second = add(second_session, "Second line")
            with self.assertRaisesRegex(ValueError, "mesma sessão"):
                store.merge([first, second])
            self.assertEqual(len(store.events(first_session)), 1)
            self.assertEqual(len(store.events(second_session)), 1)
            store.engine.dispose()


class CaptureRegressionTests(unittest.TestCase):
    def test_processor_restarts_visual_detector_after_source_time_gap(self) -> None:
        class Source:
            def iter_frames(self):
                yield packet(1.0)
                yield packet(2.0)

            def close(self):
                pass

        class Ocr:
            def open(self):
                pass

        processor = DialogueProcessor(
            store=None,
            session_id="test",
            session_dir=Path("."),
            regions=[],
            language="en",
            ocr=Ocr(),
            settings={"detection": {"change_threshold": 3.1, "changed_pixel_fraction": 0.02}},
        )
        processor._end_all = lambda *_args, **_kwargs: None
        processor._fill_missing_timeline_frames = lambda _source: None

        result = processor.run(Source())

        self.assertEqual(result["frames_seen"], 2)
        self.assertEqual(result["current_source_time"], 2.0)

    def test_playlist_url_opens_single_video_at_requested_time(self) -> None:
        url = "https://www.youtube.com/watch?v=qPueFah9uXg&list=PL2TUrSmxPbczXGqXrhqWnzh8RGQc9MSG6"
        self.assertEqual(youtube_watch_url(url, 170),
                         "https://www.youtube.com/watch?v=qPueFah9uXg&t=170s")

    def test_second_ocr_event_keeps_frame_relative_box(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "events.sqlite3")
            session_id = str(uuid.uuid4())
            store.create_session(session_id, "youtube", "https://youtube.com/watch?v=test", 170, 20, "pt", {})
            for at, text, box in ((170.2, "Primeira fala", [12, 60, 130, 22]),
                                  (174.5, "Segunda fala", [14, 61, 145, 23])):
                event_id, duplicate = store.add_event(session_id, {
                    "start_time": at, "original_text": text, "normalized_text": text.lower(),
                    "confidence": .9, "bbox": box, "text_phash": "12" * 8,
                })
                self.assertFalse(duplicate)
                store.finish_event(event_id, at + 1)
            rows = store.events(session_id)
            self.assertEqual(len(rows), 2)
            self.assertEqual([row["bbox_json"] for row in rows],
                             [[12, 60, 130, 22], [14, 61, 145, 23]])
            store.engine.dispose()


class SessionRecoveryTests(unittest.TestCase):
    def test_restart_marks_lost_workers_stopped_and_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = DubSyncStore(Path(temp) / "test.sqlite3")
            interrupted, completed = str(uuid.uuid4()), str(uuid.uuid4())
            store.create_session(interrupted, "youtube", "https://youtube.com/watch?v=test", 0, 60,
                                 "en", {}, "running")
            store.create_session(completed, "file", "local.mp4", 0, 10, "en", {}, "complete")

            manager = SessionManager(store)

            stopped = store.session(interrupted)
            self.assertEqual(stopped["state"], "stopped")
            self.assertIn("Servidor reiniciado", stopped["error"])
            self.assertEqual(store.session(completed)["state"], "complete")
            self.assertFalse(manager.busy)
            store.engine.dispose()


    def test_ready_downloaded_session_recovers_for_roi_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DubSyncStore(root / "recovery.sqlite3")
            session_id = str(uuid.uuid4())
            source = root / "video.webm"
            source.write_bytes(b"video fixture")
            store.create_session(session_id, "youtube", "https://youtube.com/watch?v=test", 12, 30,
                                 "en", {"mode": "youtube_download", "downloaded_path": str(source),
                                       "start": 12.0, "duration": 30.0},
                                 "preparing")
            with patch("Dupsync.capture.session_manager.YoutubeDownloadJob.launch") as launch:
                manager = SessionManager(store)
            self.assertEqual(store.session(session_id)["state"], "preparing")
            self.assertTrue(manager.busy)
            self.assertIn(session_id, manager.jobs)
            launch.assert_called_once()
            store.engine.dispose()

    def test_old_youtube_download_is_hardlinked_into_project_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DubSyncStore(root / "library.sqlite3")
            session_id = str(uuid.uuid4())
            source_dir = root / "sessions" / session_id
            source_dir.mkdir(parents=True)
            source = source_dir / "qPueFah9uXg.webm"
            source.write_bytes(b"original video bytes")
            config = {"mode": "youtube_download", "url": "https://youtube.com/watch?v=qPueFah9uXg",
                      "downloaded_path": str(source), "video_id": "qPueFah9uXg",
                      "video_title": "REPLACED: Parte 1", "video_width": 3840,
                      "video_height": 2160, "video_codec": "vp9", "video_size_bytes": source.stat().st_size}
            store.create_session(session_id, "youtube", config["url"], 170, 730, "pt", config, "complete")
            library = root / "videos"
            with patch("Dupsync.capture.session_manager.VIDEO_LIBRARY_DIR", library):
                manager = SessionManager(store)
                location = manager.organize_video(session_id)
            target = Path(location["path"])
            self.assertTrue(target.is_file())
            self.assertTrue(source.samefile(target))
            self.assertIn("REPLACED_ Parte 1 [qPueFah9uXg]", target.name)
            saved = store.session(session_id)["config_json"]
            self.assertEqual(saved["downloaded_path"], str(target))
            self.assertEqual(saved["source_origin"]["provider"], "youtube")
            sidecar = json.loads(Path(str(target) + ".json").read_text(encoding="utf-8"))
            self.assertEqual(sidecar["audio_streams"], 0)
            store.engine.dispose()


class BlockContinuationTests(unittest.TestCase):
    def test_next_block_starts_at_previous_absolute_end_and_clamps_at_video_end(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = DubSyncStore(root / "blocks.sqlite3")
            previous_id = str(uuid.uuid4())
            video = root / "video.mp4"
            video.write_bytes(b"video fixture")
            store.create_session(previous_id, "file", str(video), 170, 730, "en", {
                "mode": "file", "path": str(video), "start": 170, "duration": 730,
                "video_duration": 1200, "block_index": 1, "regions": [],
            }, "complete")
            manager = SessionManager(store)
            response = {"session_id": str(uuid.uuid4()), "start": 900, "duration": 300,
                        "requested_duration": 300, "block_index": 2}
            with patch.object(manager, "start_file", return_value=response) as start_file:
                result = manager.next_file_block(previous_id, 1200)

            args = start_file.call_args.args
            continuation = start_file.call_args.kwargs["continuation"]
            self.assertEqual(args[1:4], (900, 300, "en"))
            self.assertEqual(continuation["block_index"], 2)
            self.assertEqual(continuation["previous_session_id"], previous_id)
            self.assertEqual(result["start"], 900)
            self.assertEqual(result["duration"], 300)
            self.assertEqual(result["block_index"], 2)
            store.engine.dispose()


if __name__ == "__main__":
    unittest.main()

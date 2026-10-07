from __future__ import annotations

import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Dupsync.database.store import DubSyncStore
from Dupsync.dubbing.service import DubbingService


class RoiCharacterDubbingBridgeTests(unittest.TestCase):
    def test_roi_character_upload_accepts_and_stores_wav(self) -> None:
        main = importlib.import_module("Dupsync.app.main")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = DubSyncStore(root / "dubsync.sqlite3")
            profile = store.ensure_roi_character("roi-main", "Mara", "Conferido")
            data_root = root / "data"
            sample_data = b"RIFF0000WAVE"

            class Request:
                async def stream(self):
                    yield sample_data

            try:
                with patch.object(main, "store", store), patch.object(main, "DATA_DIR", data_root):
                    uploaded = asyncio.run(main.upload_character_voice_reference(
                        profile["character_id"], Request(), "mara.wav", len(sample_data)))
                self.assertEqual(uploaded["sample_audio_name"], "mara.wav")
                self.assertEqual(uploaded["sample_audio_bytes"], len(sample_data))
                stored = Path(store.roi_character(profile["character_id"])["sample_mp3_path"])
                self.assertEqual(stored.suffix, ".wav")
                self.assertEqual(stored.read_bytes(), sample_data)
            finally:
                store.engine.dispose()

    def test_dubbing_character_exposes_photo_and_imports_mp3_or_wav_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = DubSyncStore(root / "dubsync.sqlite3")
            profile = store.ensure_roi_character("roi-main", "Mara", "Conferido")
            photo = root / "character_voice_samples" / "mara.jpg"
            sample = root / "character_voice_samples" / "mara.mp3"
            photo.parent.mkdir(parents=True, exist_ok=True)
            photo.write_bytes(b"photo")
            sample.write_bytes(b"fake-mp3")
            store.update_character_photo(profile["character_id"], str(photo), "mara.jpg", "image/jpeg", photo.stat().st_size)
            store.update_character_mp3(profile["character_id"], str(sample), "mara.mp3", sample.stat().st_size)

            service = DubbingService(store, root)
            service.initialize()
            character = next(item for item in service.characters() if item["legacy_character_profile_id"] == profile["character_id"])
            self.assertIn(f"/api/roi-characters/{profile['character_id']}/photo", character["photo_url"])
            self.assertEqual(character["sample_mp3_name"], "mara.mp3")
            self.assertTrue(character["sample_mp3_available"])
            self.assertEqual(character["sample_audio_name"], "mara.mp3")
            self.assertTrue(character["sample_audio_available"])

            service.save_character({
                "name": "Mara", "display_name": "Mara", "aliases": [], "notes": "",
                "name_verified": True, "active": True,
            }, character["character_id"])
            metadata = {"duration_ms": 5000, "sample_rate": 24000, "channels": 1,
                        "codec": "pcm_s16le", "quality_status": "ready", "sha256": "ab" * 32}
            with patch("Dupsync.dubbing.service.normalize_reference", return_value=metadata):
                imported = service.import_roi_voice_reference(character["character_id"])

            self.assertEqual(imported["voice_mode"], "clone")
            self.assertFalse(imported["use_authorized"])
            self.assertEqual(imported["reference"]["source_type"], "roi_character_audio")
            self.assertFalse(imported["ready"])

            # Import is idempotent for the same sample. A changed recording
            # replaces the ROI-derived reference and clears prior authorization.
            same_sample = service.import_roi_voice_reference(character["character_id"])
            self.assertEqual(same_sample["reference"]["reference_id"], imported["reference"]["reference_id"])
            service.approve_reference(imported["reference"]["reference_id"])
            service.save_voice_profile({
                "character_id": character["character_id"], "name": imported["name"],
                "language": imported["language"], "voice_mode": "clone",
                "use_authorized": True,
            }, imported["voice_profile_id"])
            wav_sample = root / "character_voice_samples" / "mara.wav"
            wav_sample.write_bytes(b"replacement-wav")
            store.update_character_mp3(profile["character_id"], str(wav_sample), "mara.wav", wav_sample.stat().st_size)
            with patch("Dupsync.dubbing.service.normalize_reference", return_value=metadata):
                replacement = service.import_roi_voice_reference(character["character_id"])
            self.assertEqual(replacement["voice_profile_id"], imported["voice_profile_id"])
            self.assertNotEqual(replacement["reference"]["reference_id"], imported["reference"]["reference_id"])
            self.assertEqual(Path(replacement["reference"]["original_path"]).suffix, ".wav")
            self.assertEqual(replacement["reference"]["source_type"], "roi_character_audio")
            self.assertFalse(replacement["use_authorized"])
            self.assertFalse(replacement["ready"])
            store.engine.dispose()


if __name__ == "__main__":
    unittest.main()

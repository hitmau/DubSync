from __future__ import annotations

import json
import hashlib
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from ..capture.models import FramePacket, FrameSource
from ..database.store import DubSyncStore
from ..detector.visual import Region, VisualChangeDetector
from ..ocr.engine import OcrEngine
from .normalize import normalize_text
from .replacements import apply_text_replacements
from .tracker import DialogueTracker


class DialogueProcessor:
    """Shared bounded-memory visual pipeline used by file and YouTube FrameSources."""

    def __init__(self, store: DubSyncStore, session_id: str, session_dir: Path,
                 regions: list[Region], language: str, ocr: OcrEngine | None = None,
                 progress_callback=None, settings: dict | None = None,
                 replace_scope: dict | None = None) -> None:
        self.store = store
        self.session_id = session_id
        self.session_dir = session_dir
        self.regions = regions
        self.settings = settings or {}
        self.replace_scope = replace_scope
        load_replacements = getattr(self.store, "session_text_replacements", None)
        self.text_replacement_rules = load_replacements(session_id) if load_replacements else []
        self.ocr = ocr or OcrEngine(language, self.settings)
        self.progress_callback = progress_callback
        detection = self.settings.get("detection", {})
        self.change_threshold = float(detection.get("change_threshold", 2.4))
        self.changed_pixel_fraction = float(detection.get("changed_pixel_fraction", 0.006))
        self.trackers = {region.name: DialogueTracker(
            region,
            int(detection.get("stable_observations", 3)),
            int(detection.get("absence_observations", 2)),
            float(self.settings.get("ocr", {}).get("min_confidence", 0.30)),
            bool(self.settings.get("ocr", {}).get("strip_trailing_hud_artifacts", False)),
        ) for region in regions}
        self.visual = VisualChangeDetector(
            self.change_threshold,
            self.changed_pixel_fraction,
        )
        self.last_ocr_at: dict[str, float] = {}
        self.recent: deque[FramePacket] = deque(maxlen=16)
        self.last_sample_time: float | None = None
        self.last_packet_time: float | None = None
        self.current_segment_id = ""
        self.frame_count = 0
        self.analysis_count = 0
        self.ocr_calls = 0
        self.ocr_failures = 0
        self.events_created = 0
        self.events_deduplicated = 0
        self.active_events: dict[str, str] = {}
        self.errors: list[str] = []
        self.inactive_regions: set[str] = set()
        self.last_ocr_text = ""
        self.last_ocr_confidence = 0.0
        self.last_ocr_region = ""
        self.last_ocr_source_time: float | None = None
        self.started_monotonic = 0.0

    def run(self, source: FrameSource) -> dict:
        self.started_monotonic = time.monotonic()
        if self.progress_callback:
            self.progress_callback({"stage": "loading_ocr", "activity_message": "Carregando o mecanismo de OCR."})
        self.ocr.open()
        if self.progress_callback:
            self.progress_callback({"stage": "indexing_timestamps", "activity_message": "OCR carregado; aguardando o índice temporal dos quadros."})
        analysis_fps = max(1, int(self.settings.get("detection", {}).get("analysis_fps", 8)))
        if hasattr(source, "sample_fps"):
            # The file reader can decode only the frames that this loop would
            # inspect. It still advances through every timestamp for continuity.
            source.sample_fps = analysis_fps
        try:
            for packet in source.iter_frames():
                self.frame_count = getattr(source, "frames_read", self.frame_count + 1)
                if self.last_packet_time is not None and (
                    packet.source_time < self.last_packet_time - 0.5 or packet.source_time - self.last_packet_time > 0.75
                ):
                    self._end_all(self.last_packet_time, truncated=True)
                    self.visual = VisualChangeDetector(
                        self.change_threshold,
                        self.changed_pixel_fraction,
                    )
                self.last_packet_time = packet.source_time
                self.current_segment_id = packet.segment_id
                sample_interval = 1 / analysis_fps
                if self.last_sample_time is not None and packet.source_time - self.last_sample_time < sample_interval - 1e-6:
                    continue
                self.last_sample_time = packet.source_time
                self.analysis_count += 1
                self.recent.append(packet)
                self._inspect(packet, source)
                if self.progress_callback and (self.analysis_count % 8 == 0):
                    elapsed = max(0.001, time.monotonic() - self.started_monotonic)
                    self.progress_callback({"stage": "analysis", "frames_seen": self.frame_count,
                                            "frames_total": getattr(source, "frames_in_range", 0) or None,
                                            "frames_analyzed": self.analysis_count,
                                            "ocr_calls": self.ocr_calls, "ocr_failures": self.ocr_failures,
                                            "events_created": self.events_created,
                                            "events_deduplicated": self.events_deduplicated,
                                            "current_source_time": packet.source_time,
                                            "current_frame_id": packet.frame_id,
                                            "current_region": self.last_ocr_region,
                                            "current_segment": packet.segment_id,
                                            "effective_fps": self.frame_count / elapsed,
                                            "last_ocr_text": self.last_ocr_text,
                                            "last_ocr_confidence": self.last_ocr_confidence,
                                            "last_ocr_source_time": self.last_ocr_source_time,
                                            "activity_message": (f"Frame {packet.frame_id:,} · {packet.source_time:.3f}s · "
                                                                  f"{self.analysis_count:,} quadros amostrados · "
                                                                  f"{self.ocr_calls:,} consultas OCR · "
                                                                  f"{self.events_created:,} diálogos")})
            self.frame_count = getattr(source, "frames_read", self.frame_count)
            self.last_packet_time = getattr(source, "last_source_time", None) or self.last_packet_time
            if self.progress_callback:
                self.progress_callback({"stage": "finalizing", "frames_seen": self.frame_count,
                                        "frames_analyzed": self.analysis_count, "ocr_calls": self.ocr_calls,
                                        "ocr_failures": self.ocr_failures, "events_created": self.events_created,
                                        "events_deduplicated": self.events_deduplicated,
                                        "current_source_time": self.last_packet_time,
                                        "current_frame_id": self.recent[-1].frame_id if self.recent else None,
                                        "activity_message": "Varredura concluída; finalizando eventos e imagens de contexto."})
            self._end_all(self.last_packet_time or 0.0, truncated=True)
        finally:
            source.close()
        paused = bool(getattr(source, "paused", False))
        if not paused:
            self._fill_missing_timeline_frames(source)
        result = {"stage": "paused" if paused else "complete", "frames_seen": self.frame_count, "frames_analyzed": self.analysis_count,
                "ocr_calls": self.ocr_calls,
                "ocr_failures": self.ocr_failures, "events_created": self.events_created,
                "events_deduplicated": self.events_deduplicated, "current_source_time": self.last_packet_time,
                "current_segment": self.current_segment_id,
                "activity_message": "Análise pausada; progresso salvo." if paused else "Análise visual concluída.",
                "errors": self.errors[:20]}
        if self.progress_callback:
            self.progress_callback(result)
        return result

    def _inspect(self, packet: FramePacket, source: FrameSource) -> None:
        for region in self.regions:
            tracker = self.trackers[region.name]
            if not region.active_at(packet.source_time):
                if region.name not in self.inactive_regions:
                    self._actions(region.name, tracker.flush(), packet, source)
                    self.visual.reset(region.name)
                    self.last_ocr_at.pop(region.name, None)
                    self.inactive_regions.add(region.name)
                continue
            self.inactive_regions.discard(region.name)
            x0, y0, x1, y1 = region.bounds(packet.image, packet.source_time)
            roi = packet.image[y0:y1, x0:x1]
            changed = self.visual.changed(region.name, roi)
            stale = packet.source_time - self.last_ocr_at.get(region.name, float("-inf")) >= \
                float(self.settings.get("detection", {}).get("periodic_ocr_seconds", 1.25))
            if not changed and not stale:
                actions = tracker.observe(packet, None)
                self._actions(region.name, actions, packet, source)
                continue
            try:
                lines = self.ocr.recognize(roi)
                self.ocr_calls += 1
                self.last_ocr_at[region.name] = packet.source_time
                self.last_ocr_region = region.name
                self.last_ocr_source_time = packet.source_time
                self.last_ocr_text = " ".join(line.text for line in lines).strip() or "Nenhum texto detectado"
                self.last_ocr_confidence = (sum(line.confidence for line in lines) / len(lines)) if lines else 0.0
            except Exception as exc:
                self.ocr_failures += 1
                self.last_ocr_region = region.name
                self.last_ocr_source_time = packet.source_time
                self.last_ocr_text = f"Falha: {type(exc).__name__}"
                self.last_ocr_confidence = 0.0
                if len(self.errors) < 100:
                    self.errors.append(f"OCR em {region.name} @ {packet.source_time:.3f}s: {type(exc).__name__}: {exc}")
                continue
            # Ench region hns nn independent coordinnte trnnsform; the stored event bbox is frnme-relntive.
            actions = tracker.observe(packet, lines)
            self._actions(region.name, actions, packet, source)

    def _actions(self, region_name: str, actions: list[dict], packet: FramePacket,
                 source: FrameSource | None = None) -> None:
        for action in actions:
            if action["kind"] == "start":
                event_id = self._save_start(action, source)
                self.active_events[region_name] = event_id
            elif action["kind"] == "end":
                event_id = self.active_events.pop(region_name, None)
                if event_id:
                    ocr_update = self._save_final_observation(event_id, action.get("final_candidate"))
                    self.store.finish_event(event_id, action["end_time"], action.get("truncated", False),
                                            ocr_update=ocr_update)
                    self._capture_terminal_frames(event_id, action["end_time"], source)

    def _save_final_observation(self, event_id: str, candidate) -> dict | None:
        if candidate is None:
            return None
        event = self.store.event(event_id)
        if not event:
            return None
        image_dir = self.session_dir / "images"
        prefix = Path(event.get("crop_path") or "image").stem.split("_dialogue_")[0]
        crop_path = image_dir / f"{prefix}_dialogue_final_original.png"
        if not cv2.imwrite(str(crop_path), candidate.crop, [cv2.IMWRITE_PNG_COMPRESSION, 2]):
            raise RuntimeError("Não foi possível preservar o melhor recorte OCR.")
        assets = list(event.get("assets_json") or [])
        for asset in assets:
            if asset.get("kind") == "best":
                asset["kind"] = "best_at_activation"
        original_crop = event.get("crop_path")
        if original_crop:
            assets.append({"kind": "crop_at_activation", "path": original_crop,
                           "image_role": "original_crop", "source_time": event["start_time"]})
        best_packet = candidate.best_packet or candidate.packet
        best_asset = self._write_original_frame(image_dir, prefix, "best_final", best_packet,
                                                event["start_time"])
        best_asset["kind"] = "best"
        assets.append(best_asset)
        speaker_observations = [item for item in event.get("ocr_observations_json", []) if item.get("kind") == "speaker"]
        processed_text = apply_text_replacements(candidate.text, self.text_replacement_rules)
        ocr_observations = self._aligned_observations(candidate.ocr_observations)
        return {"original_text": candidate.raw_text if candidate.raw_text is not None else candidate.text,
                "processed_text": processed_text, "normalized_text": normalize_text(processed_text),
                "confidence": candidate.confidence, "bbox_json": list(candidate.bbox),
                "ocr_observations_json": ocr_observations + speaker_observations,
                "text_phash": candidate.text_hash,
                "crop_path": crop_path.relative_to(self.session_dir).as_posix(),
                "best_frame_path": best_asset["path"], "assets_json": assets}

    def _save_start(self, draft: dict, source: FrameSource | None) -> str:
        event_token = str(uuid.uuid4())
        image_dir = self.session_dir / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        tracker = self.trackers.get(draft.get("region_name", ""))
        packet = tracker.active.candidate.packet if tracker and tracker.active else None
        if packet is None:
            # The current packet remnins nvnilnble in the cnpped frnme buffer.
            packet = next((item for item in reversed(self.recent)
                           if item.source_time <= draft["start_time"] + 1e-4), None)
        if packet is None:
            return ""
        tracker_candidate = tracker.active.candidate if tracker and tracker.active else None
        if tracker_candidate is None:
            return ""
        prefix = event_token
        start_time = draft["start_time"]
        start_frame = (source.frame_near(start_time) if source else None) or packet
        best_frame = tracker_candidate.best_packet or packet
        region = next((item for item in self.regions if item.name == draft.get("region_name")), None)
        if region and region.speaker_enabled:
            bounds = region.speaker_bounds(best_frame.image, best_frame.source_time)
            if bounds:
                x0, y0, x1, y1 = bounds
                speaker_crop = best_frame.image[y0:y1, x0:x1]
                try:
                    speaker_lines = self.ocr.recognize(speaker_crop)
                    self.ocr_calls += 1
                    speaker_text = " ".join(line.text for line in speaker_lines).strip()
                    speaker_confidence = sum(line.confidence for line in speaker_lines) / len(speaker_lines) if speaker_lines else 0
                    if speaker_text and speaker_confidence >= .45:
                        profiles = self.store.roi_characters(region.roi_id) if region.roi_id else []
                        from rapidfuzz.fuzz import ratio, token_set_ratio
                        normalized_speaker = normalize_text(speaker_text)
                        matched = next((item for item in profiles
                                        if max(ratio(normalized_speaker, normalize_text(item["name"])),
                                               token_set_ratio(normalized_speaker, normalize_text(item["name"]))) >= 82), None)
                        draft["character"] = (matched["name"] if matched else speaker_text)[:120]
                        if matched:
                            draft["character_profile_id"] = matched["character_id"]
                        if not matched and region.roi_id and normalized_speaker:
                            detected_at = f"{best_frame.source_time:.3f} s"
                            note = ("Criado automaticamente pelo OCR da área do nome em "
                                    f"{detected_at}. Revise a grafia e anexe manualmente um "
                                    "arquivo MP3 ou WAV de referência de voz quando estiver disponível.")
                            ensure_profile = getattr(self.store, "ensure_roi_character", None)
                            if ensure_profile:
                                matched = ensure_profile(region.roi_id, speaker_text, note)
                                draft["character"] = matched["name"][:120]
                                draft["character_profile_id"] = matched["character_id"]
                    draft["ocr_observations"].append({"kind": "speaker", "source_time": best_frame.source_time,
                                                       "text": speaker_text, "confidence": speaker_confidence,
                                                       "bbox": [x0, y0, x1-x0, y1-y0]})
                    speaker_path = image_dir / f"{event_token}_speaker_original.png"
                    cv2.imwrite(str(speaker_path), speaker_crop, [cv2.IMWRITE_PNG_COMPRESSION, 2])
                    draft.setdefault("assets", []).append({"kind": "speaker_crop_original",
                                                             "path": speaker_path.relative_to(self.session_dir).as_posix(),
                                                             "source_time": best_frame.source_time})
                except Exception as exc:
                    self.ocr_failures += 1
                    self.errors.append(f"OCR do personagem em {region.name}: {type(exc).__name__}: {exc}")
        draft["processed_text"] = apply_text_replacements(draft.get("processed_text", ""),
                                                           self.text_replacement_rules)
        draft["normalized_text"] = normalize_text(draft["processed_text"])
        draft["ocr_observations"] = self._aligned_observations(draft.get("ocr_observations") or [])
        crop_path = image_dir / f"{prefix}_dialogue_original.png"
        cv2.imwrite(str(crop_path), tracker_candidate.crop, [cv2.IMWRITE_PNG_COMPRESSION, 2])
        assets: list[dict] = list(draft.get("assets", []))
        for kind, offset in (("pre_1000", -1.0), ("pre_500", -0.5), ("pre_250", -0.25),
                             ("start", 0.0), ("best", None)):
            if kind == "best":
                candidate_packet = best_frame
            elif kind == "start":
                candidate_packet = start_frame
            elif source and start_time + offset >= 0:
                candidate_packet = source.frame_near(start_time + offset)
            else:
                candidate_packet = None
            if candidate_packet is None:
                continue
            if offset is not None and kind not in {"start", "best"} and \
                    abs(candidate_packet.source_time - (start_time + offset)) > 0.11:
                continue
            assets.append(self._write_original_frame(image_dir, prefix, kind, candidate_packet, start_time))
        start_path = next((item["path"] for item in assets if item["kind"] == "start"), None)
        before_path = next((item["path"] for item in assets if item["kind"] == "pre_250"), None)
        best_path = next((item["path"] for item in assets if item["kind"] == "best"), None)
        draft.update({
            "bbox_json": list(draft["bbox"]),
            "crop_path": crop_path.relative_to(self.session_dir).as_posix(),
            "start_frame_path": start_path,
            "before_frame_path": before_path,
            "best_frame_path": best_path,
            "assets": assets,
        })
        event_id, duplicate = self.store.add_event(self.session_id, draft, self.replace_scope)
        self.events_created += int(not duplicate)
        self.events_deduplicated += int(duplicate)
        return event_id

    def _aligned_observations(self, observations: list[dict]) -> list[dict]:
        aligned = []
        for observation in observations:
            item = dict(observation)
            if item.get("kind") != "speaker" and item.get("processed_text") is not None:
                item["processed_text"] = apply_text_replacements(item["processed_text"],
                                                                   self.text_replacement_rules)
            aligned.append(item)
        return aligned

    def _write_original_frame(self, image_dir: Path, prefix: str, kind: str,
                              packet: FramePacket, event_start: float) -> dict:
        path = image_dir / f"{prefix}_{kind}_original.png"
        if not path.exists():
            cv2.imwrite(str(path), packet.image, [cv2.IMWRITE_PNG_COMPRESSION, 2])
        return {"kind": kind, "path": path.relative_to(self.session_dir).as_posix(),
                "source_time": packet.source_time,
                "offset_ms": round((packet.source_time - event_start) * 1000),
                "captured_at": packet.captured_at.isoformat(), "segment_id": packet.segment_id,
                "frame_id": packet.frame_id, "timing_quality": packet.timing_quality,
                "timing_error_ms": packet.timing_error_ms, "image_role": "original_full_frame",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def _capture_terminal_frames(self, event_id: str, end_time: float,
                                 source: FrameSource | None) -> None:
        event = self.store.event(event_id)
        if not event or source is None:
            return
        prefix = Path(event.get("crop_path") or "image").stem.split("_dialogue_")[0]
        image_dir = self.session_dir / "images"
        assets = list(event.get("assets_json") or [])
        for kind, target_time in (("end", end_time), ("post_250", end_time + 0.25)):
            if any(item.get("kind") == kind for item in assets):
                continue
            packet = source.frame_near(target_time)
            if packet is None or (kind == "post_250" and abs(packet.source_time - target_time) > 0.11):
                continue
            assets.append(self._write_original_frame(image_dir, prefix, kind, packet, event["start_time"]))
        self.store.add_event_assets(event_id, sorted(assets, key=lambda item: item["source_time"]))

    def _fill_missing_timeline_frames(self, source: FrameSource) -> None:
        for event in self.store.events(self.session_id):
            if event["end_time"] is not None:
                self._capture_terminal_frames(event["event_id"], event["end_time"], source)

    def _end_all(self, end_time: float, truncated: bool) -> None:
        for name, tracker in self.trackers.items():
            for action in tracker.flush():
                event_id = self.active_events.pop(name, None)
                if event_id:
                    ocr_update = self._save_final_observation(event_id, action.get("final_candidate"))
                    self.store.finish_event(event_id, min(float(end_time), action["end_time"]), truncated,
                                            ocr_update=ocr_update)

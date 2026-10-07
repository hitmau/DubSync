"""Read-only visual audit of a completed block; never rewrites reviewed dialogue."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
from rapidfuzz.fuzz import ratio

from ..database.store import DubSyncStore
from ..detector.visual import Region, VisualChangeDetector
from ..ocr.engine import OcrEngine
from .normalize import normalize_text
from .replacements import apply_text_replacements


def _similarity(a: str, b: str) -> float:
    left, right = normalize_text(a), normalize_text(b)
    return ratio(left, right) if left and right else 0.0


def compare_audit_observations(events: list[dict], observations: list[dict]) -> tuple[list[dict], int]:
    """Return only actionable discrepancies and the count of inconclusive events."""
    active = [item for item in events if item.get("review_status") not in {"discarded", "merged"}]
    by_event: dict[str, list[dict]] = defaultdict(list)
    missing: list[dict] = []
    last_gap: dict[str, dict] = {}
    for observation in observations:
        time = float(observation["source_time"])
        matches = [item for item in active if item["start_time"] - .35 <= time <=
                   (item.get("end_time") or item["start_time"] + 1) + .35 and
                   (not item.get("region_id") or not observation.get("region_id") or
                    item["region_id"] == observation["region_id"])]
        if matches:
            for item in matches:
                by_event[item["event_id"]].append(observation)
            continue
        text = str(observation.get("text") or "").strip()
        if len(normalize_text(text)) < 5 or float(observation.get("confidence") or 0) < .65:
            continue
        region = str(observation.get("region_id") or "")
        previous = last_gap.get(region)
        if previous and 0 < time - previous["source_time"] <= 2.0 and \
                _similarity(previous["text"], text) >= 82 and \
                not any(abs(time - finding["source_time"]) < 2 for finding in missing):
            missing.append({"kind": "missing_dialogue", "event_id": None,
                            "source_time": previous["source_time"], "observed_text": text,
                            "confidence": observation["confidence"],
                            "reason": "Texto estável apareceu fora dos diálogos catalogados."})
        last_gap[region] = observation

    findings: list[dict] = []
    inconclusive = 0
    for item in active:
        samples = [sample for sample in by_event[item["event_id"]]
                   if str(sample.get("text") or "").strip() and
                   float(sample.get("confidence") or 0) >= .65]
        if not samples:
            inconclusive += 1
            continue
        original = item.get("processed_text") or item.get("original_text") or ""
        corrected = item.get("corrected_text") or ""
        best_original = max(_similarity(original, sample["text"]) for sample in samples)
        best_corrected = max((_similarity(corrected, sample["text"]) for sample in samples), default=0)
        if corrected and best_original >= 80 and best_corrected < 70:
            kind, reason = "correction_divergence", "A correção difere da nova leitura, que se parece com o OCR original."
            best = best_corrected
        elif max(best_original, best_corrected) < 68:
            kind, reason = "ocr_divergence", "A nova leitura difere dos textos salvos."
            best = max(best_original, best_corrected)
        else:
            continue
        candidate = max(samples, key=lambda sample: max(_similarity(original, sample["text"]),
                                                         _similarity(corrected, sample["text"])))
        findings.append({"kind": kind, "event_id": item["event_id"],
                         "source_time": item["start_time"], "observed_text": candidate["text"],
                         "reference_text": corrected or original, "similarity": round(best, 1),
                         "confidence": candidate["confidence"], "reason": reason})
    return sorted([*findings, *missing], key=lambda row: row["source_time"]), inconclusive


def run_fine_sweep(store: DubSyncStore, run_id: str, session: dict, settings: dict) -> None:
    """Sample the entire video block at 4 FPS and OCR stable/changed dialogue ROIs."""
    config = session.get("config_json") or {}
    replacement_rules = store.project_text_replacements(session.get("project_id"))
    path = Path(config.get("downloaded_path") or config.get("path") or "")
    cap = cv2.VideoCapture(str(path))
    try:
        if not path.is_file() or not cap.isOpened():
            raise RuntimeError("O arquivo de vídeo deste bloco não está disponível.")
        regions = [Region.from_dict(item, index, float(session["source_start"]))
                   for index, item in enumerate(config.get("regions") or []) if item.get("enabled", True)]
        if not regions:
            raise RuntimeError("Este bloco não tem ROI de diálogo configurada para o pente fino.")
        engine = OcrEngine(session["language"], settings)
        engine.open()
        detector = VisualChangeDetector(2.4, .006)
        begin = float(session["source_start"])
        end = begin + float(session["requested_duration"])
        cap.set(cv2.CAP_PROP_POS_MSEC, begin * 1000)
        last_sample = begin - 1
        missing_timestamps = 0
        last_ocr: dict[str, float] = {}
        observations: list[dict] = []
        checked = calls = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            at = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0) / 1000
            if at <= 0 and begin > 0:
                missing_timestamps += 1
                if missing_timestamps >= 100:
                    raise RuntimeError("O vídeo não forneceu timestamps confiáveis para o pente fino.")
            else:
                missing_timestamps = 0
            if at < begin - .05:
                continue
            if at >= end:
                break
            if at - last_sample < .25:
                continue
            last_sample = at
            checked += 1
            for region in regions:
                if not region.active_at(at):
                    continue
                x0, y0, x1, y1 = region.bounds(frame, at)
                crop = frame[y0:y1, x0:x1]
                if crop.size == 0:
                    continue
                changed = detector.changed(region.name, crop)
                if not changed and at - last_ocr.get(region.name, float("-inf")) < 1:
                    continue
                lines = engine.recognize(crop)
                calls += 1
                last_ocr[region.name] = at
                text = " ".join(line.text for line in lines).strip()
                if text:
                    observations.append({"source_time": at, "region_id": region.roi_id,
                                         "raw_text": text,
                                         "text": apply_text_replacements(text, replacement_rules),
                                         "confidence": sum(line.confidence for line in lines) / len(lines)})
            if checked % 40 == 0:
                store.update_fine_sweep(run_id, progress_percent=min(99.9, max(0, (at - begin) / (end - begin) * 100)),
                                        current_source_time=at, checked_frames=checked, ocr_calls=calls)
        findings, inconclusive = compare_audit_observations(store.events(session["session_id"]), observations)
        store.update_fine_sweep(run_id, state="complete", progress_percent=100,
                                current_source_time=end, checked_frames=checked, ocr_calls=calls,
                                findings_json=findings,
                                error=f"{inconclusive} diálogo(s) sem nova leitura conclusiva" if inconclusive else None,
                                completed_at=datetime.now(timezone.utc))
    except Exception as exc:
        store.update_fine_sweep(run_id, state="failed", error=f"{type(exc).__name__}: {exc}",
                                completed_at=datetime.now(timezone.utc))
    finally:
        cap.release()

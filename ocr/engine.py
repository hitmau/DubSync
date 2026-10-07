from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import cv2

from .preprocess import image_variants


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]


class OcrEngine:
    """Lazy PaddleOCR CPU wrapper. Orientation/document layout modules are disabled."""

    def __init__(self, language: str = "en", settings: dict | None = None) -> None:
        self.language = language
        self.settings = dict(settings or {})
        self._engine: Any = None

    def open(self) -> None:
        if self._engine is not None:
            return
        model_cache = Path(__file__).resolve().parents[1] / ".data" / "models"
        model_cache.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(model_cache))
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("PaddleOCR não está instalado. Execute .\\setup.ps1 neste diretório.") from exc
        self._engine = PaddleOCR(
            lang=self.language,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_det_limit_type="max",
            text_det_limit_side_len=int(self.settings.get("ocr", {}).get("detection_side_limit", 1280)),
        )

    def _recognize_one(self, image: np.ndarray, scale: float) -> list[OcrLine]:
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        predictions = self._engine.predict(image)
        lines: list[OcrLine] = []
        for prediction in predictions or []:
            payload = getattr(prediction, "json", None)
            if callable(payload):
                payload = payload()
            if not isinstance(payload, dict):
                continue
            data = payload.get("res", payload)
            texts = data.get("rec_texts", [])
            scores = data.get("rec_scores", [])
            boxes = data.get("rec_boxes", data.get("dt_polys", []))
            for index, text in enumerate(texts):
                clean = str(text).strip()
                if not clean:
                    continue
                try:
                    score = max(0.0, min(1.0, float(scores[index])))
                except (IndexError, TypeError, ValueError):
                    score = 0.0
                box = np.asarray(boxes[index], dtype=np.float32).reshape(-1, 2) if index < len(boxes) else np.zeros((0, 2))
                if box.size:
                    x0, y0 = box.min(axis=0) / scale
                    x1, y1 = box.max(axis=0) / scale
                else:
                    x0 = y0 = x1 = y1 = 0
                lines.append(OcrLine(clean, score, (int(x0), int(y0), int(x1), int(y1))))
        lines.sort(key=lambda line: (line.bbox[1], line.bbox[0]))
        return lines

    @staticmethod
    def _quality(lines: list[OcrLine]) -> float:
        if not lines:
            return -1.0
        text = " ".join(line.text for line in lines)
        useful = sum(character.isalnum() for character in text)
        printable = sum(character.isprintable() for character in text)
        words = [word for word in text.split() if sum(character.isalpha() for character in word) > 1]
        confidence = sum(line.confidence for line in lines) / len(lines)
        return confidence * 70 + min(len(text), 50) * 0.12 + useful / max(1, printable) * 7 + len(words) * 0.6

    def recognize(self, image: np.ndarray) -> list[OcrLine]:
        self.open()
        if image.size == 0:
            return []
        config = self.settings.get("preprocess", {})
        variants = image_variants(image, config)
        if not variants:
            return self._recognize_one(image, 1.0)

        selected = self._recognize_one(variants[0][1], variants[0][2])
        mode = config.get("mode", "balanced")
        fallback = float(config.get("fallback_confidence", 0.78))
        if mode == "fast" or (mode == "balanced" and selected and
                                sum(line.confidence for line in selected) / len(selected) >= fallback):
            return selected

        best, best_quality = selected, self._quality(selected)
        for _, variant, scale in variants[1:]:
            candidate = self._recognize_one(variant, scale)
            quality = self._quality(candidate)
            if quality > best_quality:
                best, best_quality = candidate, quality
        return best

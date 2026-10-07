from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from Dupsync.ocr.engine import OcrEngine


def main() -> None:
    image = np.full((300, 1600, 3), 238, dtype=np.uint8)
    cv2.putText(image, "HELLO WORLD", (70, 205), cv2.FONT_HERSHEY_DUPLEX, 4.0,
                (20, 20, 20), 8, cv2.LINE_AA)
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "ocr-smoke.png"
        if not cv2.imwrite(str(path), image):
            raise RuntimeError("Could not create the OCR smoke image.")
        engine = OcrEngine("en")
        lines = engine.recognize(cv2.imread(str(path)))
    print("OCR result:", [(line.text, round(line.confidence, 3)) for line in lines])
    if not any("HELLO" in line.text.upper() and "WORLD" in line.text.upper() for line in lines):
        raise SystemExit("PaddleOCR did not recognize the synthetic smoke-test caption.")


if __name__ == "__main__":
    main()

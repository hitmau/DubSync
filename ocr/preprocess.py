from __future__ import annotations

import cv2
import numpy as np


def image_variants(image: np.ndarray, config: dict) -> list[tuple[str, np.ndarray, float]]:
    """Build OCR-only views; callers keep the unmodified source frame separately."""
    mode = config.get("mode", "balanced")
    variants: list[tuple[str, np.ndarray, float]] = [("original", image, 1.0)]
    if mode == "fast":
        return variants

    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    contrast = float(config.get("contrast", 1.0))
    brightness = int(config.get("brightness", 0))
    if contrast != 1.0 or brightness:
        grey = cv2.convertScaleAbs(grey, alpha=contrast, beta=brightness)

    if config.get("clahe", True):
        enhanced = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(8, 8)).apply(grey)
        variants.append(("clahe", enhanced, 1.0))
    else:
        enhanced = grey
        if image.ndim == 3:
            variants.append(("gray", grey, 1.0))

    if config.get("smart_upscale", True) and min(grey.shape[:2]) < 150:
        scale = 2.0 if min(grey.shape[:2]) < 90 else 1.5
        enlarged = cv2.resize(enhanced, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        variants.append(("upscale", enlarged, scale))

    method = config.get("threshold", "none")
    if method != "none":
        source = enhanced
        if method == "otsu":
            blurred = cv2.GaussianBlur(source, (3, 3), 0)
            _, thresholded = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        elif method == "adaptive":
            thresholded = cv2.adaptiveThreshold(source, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                cv2.THRESH_BINARY, 31, 8)
        else:
            _, thresholded = cv2.threshold(source, int(config.get("threshold_value", 180)),
                                           255, cv2.THRESH_BINARY)
        morph = max(0, min(5, int(config.get("morphology", 0))))
        if morph:
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (morph, morph))
            thresholded = cv2.morphologyEx(thresholded, cv2.MORPH_CLOSE, kernel)
        variants.append((method, thresholded, 1.0))
        if mode == "thorough":
            variants.append((f"{method}_inverse", cv2.bitwise_not(thresholded), 1.0))

    if mode == "thorough" or config.get("smart_sharpen"):
        sharpened = cv2.addWeighted(enhanced, 1.8, cv2.GaussianBlur(enhanced, (0, 0), 1.2), -0.8, 0)
        variants.append(("sharpen", sharpened, 1.0))
        if mode == "thorough":
            adaptive = cv2.adaptiveThreshold(cv2.GaussianBlur(enhanced, (3, 3), 0), 255,
                                            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 8)
            variants.append(("adaptive", adaptive, 1.0))
            variants.append(("adaptive_inverse", cv2.bitwise_not(adaptive), 1.0))

    # Keep runtime bounded even if settings are edited by hand.
    unique: list[tuple[str, np.ndarray, float]] = []
    seen: set[tuple[int, int, int]] = set()
    for item in variants:
        signature = (item[1].shape[0], item[1].shape[1], int(item[1].mean()))
        if signature not in seen:
            unique.append(item)
            seen.add(signature)
    return unique[:6 if mode == "thorough" else 3]

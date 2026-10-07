from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock


APP_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = APP_ROOT / ".data" / "settings.json"
ENV_PATH = APP_ROOT / ".env.local"

DEFAULT_SETTINGS = {
    "detection": {
        "analysis_fps": 8,
        "change_threshold": 2.4,
        "changed_pixel_fraction": 0.006,
        "periodic_ocr_seconds": 1.25,
        "stable_observations": 3,
        "absence_observations": 2,
    },
    "preprocess": {
        "mode": "balanced",
        "contrast": 1.15,
        "brightness": 0,
        "clahe": True,
        "threshold": "otsu",
        "threshold_value": 180,
        "morphology": 1,
        "smart_upscale": True,
        "smart_sharpen": False,
        "fallback_confidence": 0.78,
    },
    "ocr": {
        "min_confidence": 0.30,
        "detection_side_limit": 1280,
        "strip_trailing_hud_artifacts": False,
    },
    "calibration": {
        "model": "gpt-5.4-mini",
        "duration_seconds": 10,
        "sample_count": 6,
    },
    "review": {
        "show_split_preview": True,
        "permanent_discard": False,
    },
    "dubbing": {
        "provider": "omnivoice",
        "base_url": "http://127.0.0.1:8015",
        "timeout_seconds": 600,
        "auto_start_docker": False,
        "master_format": "wav_24khz_mono",
    },
    "appearance": {
        "border_width": 1,
        "border_color": "#26364d",
    },
}

_LOCK = RLock()


def _bounded(value, low, high, cast):
    try:
        parsed = cast(value)
    except (TypeError, ValueError, OverflowError):
        parsed = cast(low)
    return max(low, min(high, parsed))


def sanitize_settings(value: dict | None) -> dict:
    result = deepcopy(DEFAULT_SETTINGS)
    if not isinstance(value, dict):
        return result
    detection = value.get("detection", {})
    if isinstance(detection, dict):
        result["detection"].update({
            "analysis_fps": int(_bounded(detection.get("analysis_fps", result["detection"]["analysis_fps"]), 2, 15, int)),
            "change_threshold": _bounded(detection.get("change_threshold", result["detection"]["change_threshold"]), 0.2, 20, float),
            "changed_pixel_fraction": _bounded(detection.get("changed_pixel_fraction", result["detection"]["changed_pixel_fraction"]), 0.001, 0.2, float),
            "periodic_ocr_seconds": _bounded(detection.get("periodic_ocr_seconds", result["detection"]["periodic_ocr_seconds"]), 0.25, 8, float),
            "stable_observations": int(_bounded(detection.get("stable_observations", result["detection"]["stable_observations"]), 1, 8, int)),
            "absence_observations": int(_bounded(detection.get("absence_observations", result["detection"]["absence_observations"]), 1, 8, int)),
        })
    preprocess = value.get("preprocess", {})
    if isinstance(preprocess, dict):
        result["preprocess"].update({
            "mode": preprocess.get("mode") if preprocess.get("mode") in {"fast", "balanced", "thorough"} else "balanced",
            "contrast": _bounded(preprocess.get("contrast", result["preprocess"]["contrast"]), 0.5, 2.5, float),
            "brightness": int(_bounded(preprocess.get("brightness", result["preprocess"]["brightness"]), -80, 80, int)),
            "clahe": bool(preprocess.get("clahe", True)),
            "threshold": preprocess.get("threshold") if preprocess.get("threshold") in {"none", "otsu", "adaptive", "manual"} else "none",
            "threshold_value": int(_bounded(preprocess.get("threshold_value", result["preprocess"]["threshold_value"]), 40, 245, int)),
            "morphology": int(_bounded(preprocess.get("morphology", result["preprocess"]["morphology"]), 0, 5, int)),
            "smart_upscale": bool(preprocess.get("smart_upscale", True)),
            "smart_sharpen": bool(preprocess.get("smart_sharpen", False)),
            "fallback_confidence": _bounded(preprocess.get("fallback_confidence", result["preprocess"]["fallback_confidence"]), 0.3, 0.98, float),
        })
    ocr = value.get("ocr", {})
    if isinstance(ocr, dict):
        result["ocr"].update({
            "min_confidence": _bounded(ocr.get("min_confidence", result["ocr"]["min_confidence"]), 0.05, 0.95, float),
            "detection_side_limit": int(_bounded(ocr.get("detection_side_limit", result["ocr"]["detection_side_limit"]), 640, 2560, int)),
            "strip_trailing_hud_artifacts": bool(ocr.get(
                "strip_trailing_hud_artifacts", result["ocr"]["strip_trailing_hud_artifacts"])),
        })
    calibration = value.get("calibration", {})
    if isinstance(calibration, dict):
        model = str(calibration.get("model", result["calibration"]["model"])).strip()
        result["calibration"].update({
            "model": model[:80] or DEFAULT_SETTINGS["calibration"]["model"],
            "duration_seconds": int(_bounded(calibration.get("duration_seconds", result["calibration"]["duration_seconds"]), 3, 30, int)),
            "sample_count": int(_bounded(calibration.get("sample_count", result["calibration"]["sample_count"]), 3, 10, int)),
        })
    review = value.get("review", {})
    if isinstance(review, dict):
        result["review"].update({
            "show_split_preview": bool(review.get("show_split_preview", True)),
            "permanent_discard": bool(review.get("permanent_discard", False)),
        })
    dubbing = value.get("dubbing", {})
    if isinstance(dubbing, dict):
        base_url = str(dubbing.get("base_url", result["dubbing"]["base_url"])).strip().rstrip("/")
        if base_url not in {"http://127.0.0.1:8015", "http://localhost:8015", "http://[::1]:8015"}:
            base_url = DEFAULT_SETTINGS["dubbing"]["base_url"]
        result["dubbing"].update({
            "provider": "omnivoice",
            "base_url": base_url,
            "timeout_seconds": int(_bounded(dubbing.get("timeout_seconds", result["dubbing"]["timeout_seconds"]), 30, 1800, int)),
            "auto_start_docker": bool(dubbing.get("auto_start_docker", False)),
            "master_format": "wav_24khz_mono",
        })
    appearance = value.get("appearance", {})
    if isinstance(appearance, dict):
        border_color = str(appearance.get("border_color", result["appearance"]["border_color"])).strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", border_color):
            border_color = DEFAULT_SETTINGS["appearance"]["border_color"]
        result["appearance"].update({
            "border_width": _bounded(appearance.get("border_width", result["appearance"]["border_width"]), 0.5, 4, float),
            "border_color": border_color.lower(),
        })
    return result


def load_settings() -> dict:
    with _LOCK:
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        return sanitize_settings(saved)


def save_settings(value: dict) -> dict:
    clean = sanitize_settings(value)
    with _LOCK:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=SETTINGS_PATH.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(clean, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, SETTINGS_PATH)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return clean


def openai_api_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key.startswith("sk-"):
        return key
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        match = re.match(r"^\s*OPENAI_API_KEY\s*=\s*(.*?)\s*$", line)
        if match:
            candidate = match.group(1).strip().strip("\"'")
            if candidate.startswith("sk-"):
                return candidate
    return None


def public_settings(value: dict | None = None) -> dict:
    return {**sanitize_settings(value or load_settings()),
            "openai": {"configured": bool(openai_api_key()), "env_file": ENV_PATH.name}}

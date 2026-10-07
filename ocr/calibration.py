from __future__ import annotations

import base64
import json
import statistics
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz.fuzz import ratio

from ..detector.visual import Region
from ..app.settings import sanitize_settings
from .text_cleanup import strip_trailing_hud_artifacts
from .engine import OcrEngine


RESPONSES_URL = "https://api.openai.com/v1/responses"
MODELS_URL = "https://api.openai.com/v1/models"


def _request_json(url: str, key: str, payload: dict | None = None, timeout: int = 90) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            message = ""
        raise RuntimeError(message or f"OpenAI retornou HTTP {exc.code}.") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Não foi possível conectar à OpenAI: {exc.reason}") from None


def test_connection(key: str, model: str | None = None) -> dict:
    response = _request_json(MODELS_URL, key, timeout=25)
    models = response.get("data", [])
    model_ids = {str(item.get("id", "")) for item in models if isinstance(item, dict)}
    return {"ok": True, "models_available": len(models), "selected_model": model,
            "selected_model_available": bool(model and model in model_ids)}


def _frames(path: Path, start: float, duration: int, sample_count: int) -> list[dict]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError("O DubSync não conseguiu abrir o vídeo para a amostragem visual.")
    result = []
    try:
        source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        source_duration = frame_count / source_fps if source_fps > 0 and frame_count > 0 else 0.0
        usable_duration = min(float(duration), max(0.0, source_duration - start)) if source_duration else float(duration)
        if usable_duration <= 0:
            raise ValueError("O ponto de amostragem fica depois do fim do vídeo.")
        for index in range(sample_count):
            offset = usable_duration * index / max(1, sample_count - 1)
            source_time = start + offset
            capture.set(cv2.CAP_PROP_POS_MSEC, source_time * 1000)
            ok, image = capture.read()
            if not ok or image is None or image.size == 0:
                continue
            height, width = image.shape[:2]
            scale = min(1.0, 1280 / max(width, height))
            if scale < 1.0:
                image = cv2.resize(image, (round(width * scale), round(height * scale)),
                                   interpolation=cv2.INTER_AREA)
            result.append({"id": index + 1, "source_time": round(source_time, 3),
                           "image": image, "source_width": width, "source_height": height})
    finally:
        capture.release()
    if len(result) < 3:
        raise ValueError("Foram lidos menos de três frames. Verifique o formato do vídeo ou tente outro trecho.")
    return result


def _data_url(image: np.ndarray) -> str:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError("Não foi possível preparar uma imagem para a calibração.")
    return "data:image/jpeg;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")


def _response_text(payload: dict) -> str:
    if payload.get("output_text"):
        return str(payload["output_text"])
    return "\n".join(content["text"] for item in payload.get("output", [])
                     for content in item.get("content", [])
                     if content.get("type") == "output_text" and content.get("text"))


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    if not text.startswith("{"):
        left, right = text.find("{"), text.rfind("}")
        if left >= 0 and right > left:
            text = text[left:right + 1]
    return json.loads(text)


def _ask_vision(key: str, model: str, frames: list[dict], regions: list[dict], settings: dict) -> dict:
    width = frames[0]["source_width"]
    height = frames[0]["source_height"]
    prompt = (
        "Você calibra OCR visual de diálogos de jogos. Analise os quadros na ordem. "
        "Transcreva somente diálogos do jogo realmente legíveis; ignore webcam, streamer, "
        "HUD, menus, créditos e textos decorativos. Não invente texto. Para cada quadro, "
        "marque has_dialogue e devolva o texto e, se possível, uma caixa normalizada "
        "[x,y,width,height] entre 0 e 1 ao redor das falas do jogo. Sugira ajustes modestos "
        "para o OCR local e a detecção. Considere a configuração atual informada. "
        "Retorne somente JSON neste formato: {\"samples\":[{\"id\":1,\"has_dialogue\":false,"
        "\"text\":\"\",\"roi\":[0.1,0.7,0.8,0.2]}],\"recommendation\":{"
        "\"contrast\":1.15,\"brightness\":0,\"clahe\":true,\"threshold\":\"otsu\","
        "\"threshold_value\":180,\"morphology\":1,\"smart_upscale\":true,"
        "\"smart_sharpen\":false,\"min_confidence\":0.3,\"analysis_fps\":8,"
        "\"change_threshold\":2.4,\"stable_observations\":3},\"notes\":\"\"}. "
        f"Tamanho original dos quadros: {width}x{height}. ROIs atuais: "
        f"{json.dumps(regions, ensure_ascii=False)}. Configuração atual: "
        f"{json.dumps(settings, ensure_ascii=False)}. Os quadros enviados foram reduzidos "
        "para no máximo 1280 pixels no lado maior."
    )
    if settings.get("ocr", {}).get("strip_trailing_hud_artifacts", False):
        prompt += (
            " A configuração de limpeza de sufixos HUD está ativa: símbolos ou pequenos "
            "fragmentos isolados no fim da fala podem ser ícones da barra/interface do jogo, "
            "e não pertencem ao diálogo. Ignore esses artefatos finais ao transcrever; preserve "
            "a pontuação legítima e nunca remova palavras do meio da fala."
        )
    content = [{"type": "input_text", "text": prompt}]
    for frame in frames:
        content.append({"type": "input_text", "text":
                        f"Amostra {frame['id']} no tempo {frame['source_time']:.3f}s"})
        content.append({"type": "input_image", "image_url": _data_url(frame["image"]),
                        "detail": "high"})
    response = _request_json(RESPONSES_URL, key, {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "max_output_tokens": 2400,
        "store": False,
    }, timeout=150)
    return _parse_json(_response_text(response))


def _proposed_region(analysis: dict, frames: list[dict], fallback: list[dict]) -> dict | None:
    boxes = []
    for item in analysis.get("samples", []):
        if not isinstance(item, dict) or not item.get("has_dialogue"):
            continue
        roi = item.get("roi")
        if not isinstance(roi, list) or len(roi) != 4:
            continue
        try:
            x, y, width, height = (float(value) for value in roi)
        except (TypeError, ValueError):
            continue
        x = max(0.0, min(0.98, x))
        y = max(0.0, min(0.98, y))
        width = max(0.04, min(1.0 - x, width))
        height = max(0.03, min(1.0 - y, height))
        if width >= 0.08 and height >= 0.03:
            boxes.append((x, y, width, height))
    if not boxes:
        return fallback[0] if fallback else None
    return {"x": statistics.median(box[0] for box in boxes),
            "y": statistics.median(box[1] for box in boxes),
            "width": statistics.median(box[2] for box in boxes),
            "height": statistics.median(box[3] for box in boxes),
            "name": "Diálogo (sugestão IA)", "enabled": True}


def _settings_recommendation(analysis: dict, current: dict) -> dict:
    recommendation = analysis.get("recommendation", {})
    if not isinstance(recommendation, dict):
        recommendation = {}
    result = {key: dict(value) for key, value in sanitize_settings(current).items()
              if isinstance(value, dict)}
    preprocess = result["preprocess"]
    for key in ("contrast", "brightness", "clahe", "threshold", "threshold_value",
                "morphology", "smart_upscale", "smart_sharpen"):
        if key in recommendation:
            preprocess[key] = recommendation[key]
    preprocess.setdefault("mode", "balanced")
    ocr = result["ocr"]
    if "min_confidence" in recommendation:
        ocr["min_confidence"] = recommendation["min_confidence"]
    detection = result["detection"]
    for key in ("analysis_fps", "change_threshold", "stable_observations"):
        if key in recommendation:
            detection[key] = recommendation[key]
    clean = sanitize_settings(result)
    # Keep expensive OCR fallback enabled only when confidence from the source is low.
    clean["preprocess"]["mode"] = "balanced"
    return clean


def _similarity_score(engine: OcrEngine, frames: list[dict], references: dict[int, str],
                      region: dict, settings: dict) -> float | None:
    pairs = [(frame, references.get(frame["id"], "").strip()) for frame in frames]
    pairs = [(frame, text) for frame, text in pairs if text]
    if not pairs:
        return None
    engine.settings = settings
    scores = []
    for frame, reference in pairs:
        x0, y0, x1, y1 = Region.from_dict(region).bounds(frame["image"])
        lines = engine.recognize(frame["image"][y0:y1, x0:x1])
        found = " ".join(line.text for line in lines)
        if settings.get("ocr", {}).get("strip_trailing_hud_artifacts", False):
            found = strip_trailing_hud_artifacts(found)
        scores.append(ratio(reference.casefold(), found.casefold()) / 100)
    return round(statistics.fmean(scores) * 100, 1) if scores else None


def calibrate_video(path: Path, start: float, duration: int, sample_count: int,
                    model: str, language: str, regions: list[dict], settings: dict,
                    key: str) -> dict:
    frames = _frames(path, start, duration, sample_count)
    analysis = _ask_vision(key, model, frames, regions, settings)
    samples = analysis.get("samples", [])
    references = {int(item.get("id", 0)): str(item.get("text", "")).strip()
                  for item in samples if isinstance(item, dict) and item.get("has_dialogue")}
    suggested_region = _proposed_region(analysis, frames, regions)
    suggested_settings = _settings_recommendation(analysis, settings)
    current_region = regions[0] if regions else suggested_region
    score = {"current": None, "suggested": None}
    if current_region:
        engine = OcrEngine(language, settings)
        engine.open()
        score["current"] = _similarity_score(engine, frames, references, current_region, settings)
        score["suggested"] = _similarity_score(engine, frames, references,
                                               suggested_region or current_region, suggested_settings)
    details = []
    for frame in frames:
        result = next((item for item in samples if isinstance(item, dict)
                       and int(item.get("id", 0)) == frame["id"]), {})
        details.append({"id": frame["id"], "source_time": frame["source_time"],
                        "has_dialogue": bool(result.get("has_dialogue")),
                        "text": str(result.get("text", ""))[:500]})
    return {"model": model, "sample_count": len(frames), "duration_seconds": duration,
            "samples": details, "suggested_region": suggested_region,
            "suggested_settings": suggested_settings, "similarity": score,
            "notes": str(analysis.get("notes", ""))[:1000]}

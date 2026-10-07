from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np
from playwright.sync_api import sync_playwright


def main() -> None:
    output = Path(__file__).resolve().parents[1] / ".data" / "ui-smoke-1920x1080.png"
    errors: list[str] = []
    session_id = "smoke-session"
    event = {
        "event_id": "smoke-event", "session_id": session_id, "sequence": 4,
        "segment_id": "youtube-0002", "start_time": 123.4, "end_time": 126.2,
        "original_text": "We need to get out of here.", "normalized_text": "we need to get out of here",
        "corrected_text": None, "confidence": 0.96, "bbox_json": [32, 120, 420, 160],
        "crop_path": "images/smoke_dialogue_original.png", "start_frame_path": None,
        "before_frame_path": None, "best_frame_path": None, "end_frame_path": None,
        "assets_json": [], "processing_variants_json": [], "text_phash": "a" * 16,
        "context_phash": "b" * 16, "character": None, "emotion": None,
        "review_status": "pending", "truncated": False,
        "timing_quality": "exact_pts", "timing_error_ms": 1,
        "captured_at": "2026-10-02T12:00:00+00:00", "frame_id": 120,
        "merged_into": None, "created_at": "2026-10-02T12:00:00+00:00",
        "updated_at": "2026-10-02T12:00:00+00:00",
    }
    completed = {
        "session_id": session_id, "source_type": "file", "source_uri": "C:/Videos/REPLACED.mp4",
        "source_start": 120, "requested_duration": 900, "language": "en", "state": "complete",
        "config_json": {"mode": "file", "path": "C:/Videos/REPLACED.mp4", "start": 120,
                        "duration": 900, "segment_duration_seconds": 60},
        "error": None, "created_at": "2026-10-02T12:00:00+00:00", "completed_at": None,
    }
    timeline_updates: list[dict] = []
    crop = np.zeros((100, 640, 3), dtype=np.uint8)
    for x in range(crop.shape[1]):
        crop[:, x] = (13 + x // 45, 24 + x // 55, 39 + x // 65)
    cv2.putText(crop, "We need to get out of here.", (30, 65), cv2.FONT_HERSHEY_DUPLEX,
                1.05, (238, 242, 249), 2, cv2.LINE_AA)
    ok, encoded_crop = cv2.imencode(".png", crop)
    assert ok

    with sync_playwright() as playwright:
        browser_root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
        executable = next(browser_root.glob("chromium-*/chrome-win64/chrome.exe"))
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))

        def mock_api(route):
            path = route.request.url.split("?", 1)[0]
            if path.endswith("/review/update"):
                changes = route.request.post_data_json
                timeline_updates.append(changes)
                event.update(changes)
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({**event, **changes}))
            elif path.endswith("/api/events"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"events": [event]}))
            elif path.endswith("/api/sessions"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"sessions": [completed]}))
            elif path.endswith("/api/sessions/" + session_id):
                route.fulfill(status=200, content_type="application/json", body=json.dumps(completed))
            elif path.endswith("/api/sessions/" + session_id + "/markers"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"markers": []}))
            else:
                route.continue_()

        page.route("**/api/events**", mock_api)
        page.route("**/api/sessions", mock_api)
        page.route("**/api/sessions/**", mock_api)
        page.route("**/assets/**", lambda route: route.fulfill(
            status=200, content_type="image/png", body=encoded_crop.tobytes()))
        response = page.goto("http://127.0.0.1:8766", wait_until="networkidle")
        assert response and response.status == 200
        page.wait_for_function("document.querySelector('#health').textContent.includes('FFmpeg')")
        for label in ("Fonte de Vídeo", "Pré-visualização / Captura", "Linha do Tempo de Eventos",
                      "Segmento e Processamento", "Detalhes do Evento Selecionado", "Revisão e Edição"):
            assert page.get_by_text(label, exact=True).count() > 0, label
        page.locator('.source-tab[data-mode="file"]').click()
        assert page.locator("#fileField").is_visible() and page.locator("#urlField").is_hidden()
        page.locator('.source-tab[data-mode="youtube"]').click()
        try:
            page.wait_for_selector(".event-row", timeout=5000)
        except Exception as exc:
            raise AssertionError({
                "page_errors": errors,
                "toast": page.locator("#toast").inner_text(),
                "session_error": page.locator("#sessionError").inner_text(),
                "event_rows": page.locator("#eventRows").inner_html(),
            }) from exc
        assert page.locator(".timeline-event").count() == 1
        page.locator(".event-row").click()
        assert "selected" in page.locator(".timeline-event").get_attribute("class")
        page.locator(".timeline-event").click()
        assert "selected" in page.locator(".event-row").get_attribute("class")

        block = page.locator(".timeline-event")
        bounds = block.bounding_box()
        assert bounds
        original_end = event["end_time"]
        page.mouse.move(bounds["x"] + bounds["width"] - 2, bounds["y"] + bounds["height"] / 2)
        page.mouse.down()
        page.mouse.move(bounds["x"] + bounds["width"] + 25, bounds["y"] + bounds["height"] / 2)
        page.mouse.up()
        page.wait_for_function("""() => document.querySelector('#reviewEnd').value !== '126.200'""")
        assert page.locator("#reviewEnd").input_value() != "126.200"
        assert timeline_updates and timeline_updates[-1]["end_time"] > original_end

        geometry = page.evaluate("""() => Object.fromEntries(['#sourcePanel','#capture','.right-column','#review'].map(s=>{
          const r=document.querySelector(s).getBoundingClientRect();return[s,{x:r.x,y:r.y,width:r.width,height:r.height}]
        }))""")
        assert geometry["#sourcePanel"]["x"] < geometry["#capture"]["x"] < geometry[".right-column"]["x"]
        assert geometry["#review"]["y"] > geometry["#capture"]["y"]
        assert geometry["#review"]["width"] > geometry[".right-column"]["width"] * 2
        assert page.locator(".grid-splitter").count() == 7
        page.screenshot(path=str(output), full_page=True)

        prep_id = "downloaded-session"
        prep_path = r"C:\DubSync\videos\REPLACED [sample].webm"
        prep_session = {
            "session_id": prep_id, "source_type": "youtube", "source_uri": "https://youtube.com/watch?v=sample",
            "source_start": 170, "requested_duration": 730, "language": "pt", "state": "preparing",
            "ready_for_calibration": True, "detail": "Download concluído.", "progress": {},
            "frames_captured": 0, "current_source_time": 170, "current_segment": 0,
            "effective_fps": 0, "timing_quality": "exact_pts", "timing_error_ms": 1,
            "current_file": "REPLACED [sample].webm",
            "config_json": {"mode": "youtube_download", "url": "https://youtube.com/watch?v=sample",
                            "start": 170, "duration": 730, "downloaded_path": prep_path,
                            "video_title": "REPLACED", "video_id": "sample", "video_width": 3840,
                            "video_height": 2160, "video_size_bytes": 1024,
                            "source_origin": {"provider": "youtube", "title": "REPLACED"}, "regions": []},
        }
        start_payloads: list[dict] = []
        prepare_calls: list[bool] = []
        recovery_page = browser.new_page(viewport={"width": 1920, "height": 1080})
        recovery_page.on("pageerror", lambda error: errors.append(str(error)))
        sample_frame = np.full((360, 640, 3), (19, 33, 49), dtype=np.uint8)
        ok, encoded_frame = cv2.imencode(".png", sample_frame)
        assert ok

        def recovery_api(route):
            path = route.request.url.split("?", 1)[0]
            if path.endswith("/api/events"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"events": []}))
            elif path.endswith("/api/sessions"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"sessions": [prep_session]}))
            elif path.endswith("/api/sessions/" + prep_id + "/organize-video"):
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"path": prep_path, "directory": r"C:\DubSync\videos", "reused": True}))
            elif path.endswith("/api/sessions/" + prep_id + "/preview"):
                image = __import__("base64").b64encode(encoded_frame.tobytes()).decode("ascii")
                route.fulfill(status=200, content_type="application/json",
                              body=json.dumps({"image": "data:image/png;base64," + image, "width": 640, "height": 360}))
            elif path.endswith("/api/sessions/" + prep_id):
                route.fulfill(status=200, content_type="application/json", body=json.dumps(prep_session))
            elif path.endswith("/api/sessions/" + prep_id + "/markers"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"markers": []}))
            elif path.endswith("/api/youtube/coverage"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"intervals": []}))
            elif path.endswith("/api/youtube/" + prep_id + "/start"):
                start_payloads.append(route.request.post_data_json)
                prep_session["state"] = "processing"
                prep_session["config_json"]["analysis_started"] = True
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"state": "starting"}))
            elif path.endswith("/api/sessions/" + prep_id + "/video"):
                route.fulfill(status=206, headers={"Content-Range": "bytes 0-3/4", "Accept-Ranges": "bytes"},
                              content_type="video/webm", body=b"webm")
            elif path.endswith("/api/youtube/prepare"):
                prepare_calls.append(True)
                route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "duplicated"}))
            else:
                route.continue_()

        recovery_page.route("**/api/**", recovery_api)
        recovery_page.goto("http://127.0.0.1:8766", wait_until="networkidle")
        recovery_page.wait_for_function("document.querySelector('#fileOrigin').classList.contains('hidden') === false")
        assert recovery_page.locator('.source-tab.active').get_attribute("data-mode") == "file"
        assert recovery_page.locator("#filePath").input_value() == prep_path
        assert "Baixado do YouTube" in recovery_page.locator("#fileOrigin").inner_text()
        assert recovery_page.locator("#sessionState").inner_text() == "Pronto para calibrar e iniciar"
        assert recovery_page.locator("#playButton").inner_text() == "▶ Iniciar primeiro bloco"
        recovery_page.evaluate("""() => { const frame = document.createElement('canvas'); frame.width = 640; frame.height = 360; preview = frame; }""")
        recovery_page.locator("#playButton").click()
        try:
            recovery_page.wait_for_function("document.querySelector('#sessionState').textContent === 'Analisando vídeo'", timeout=5000)
        except Exception as exc:
            raise AssertionError({"state": recovery_page.locator("#sessionState").inner_text(),
                                  "error": recovery_page.locator("#sessionError").inner_text(),
                                  "toast": recovery_page.locator("#toast").inner_text(),
                                  "start_payloads": start_payloads,
                                  "page_errors": errors}) from exc
        assert len(start_payloads) == 1 and len(start_payloads[0]["regions"]) == 1
        assert not prepare_calls
        browser.close()
    assert not errors, errors
    print(f"UI smoke passed at 1920x1080: timeline drag, downloaded-file handoff and ROI session start; {output}")


if __name__ == "__main__":
    main()

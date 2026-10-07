"""Verifica visibilidade dos detalhes de indexação, frames e OCR na tela."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".data" / "playwright-browsers"))


def main() -> None:
    errors: list[str] = []
    executable = next(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]).glob("chromium-*/chrome-win64/chrome.exe"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/sessions", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"sessions":[]}'))
        page.route("**/api/events**", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"events":[]}'))
        response = page.goto("http://127.0.0.1:8766/", wait_until="networkidle")
        assert response and response.status == 200
        page.wait_for_selector("#analysisActivity")

        indexing = page.evaluate("""() => {
          const session={state:'running',source_start:170,requested_duration:730,
            config_json:{duration:730},progress:{stage:'indexing_timestamps',frames_indexed:4200,
              estimated_index_frames:27000,index_progress_percent:15.55,frames_seen:0,
              frames_analyzed:0,ocr_calls:0,ocr_failures:0,events_created:0,
              recent_activity:[{at:'2026-10-03T12:00:00Z',message:'Indexando timestamps · 4.200 quadros'}]}};
          currentSession=session;sessionHydrated=true;mode='file';updatePlaybackButton();updateAnalysisActivity(session);
          updatePipeline(session);
          return{stage:$('#activityStage').textContent,progress:$('#activityProgressText').textContent,
            frame:$('#activityFrame').textContent,label:$('#activityFramesReadLabel').textContent,
            read:$('#activityFramesRead').textContent,total:$('#activityFramesTotal').textContent,
            button:$('#playButton').textContent,busy:$('#playButton').classList.contains('busy'),
            buttonColor:getComputedStyle($('#playButton')).backgroundColor,
            history:$('#activityHistory').innerText,pipelineState:$('#processing').dataset.state,
            pipelineStage:$('#processing').dataset.stage,
            spinner:getComputedStyle($('#pipelineSteps [data-step="frames"] i')).animationName,
            progressMotion:getComputedStyle($('#progressBar'),'::after').animationName};
        }""")
        assert indexing["stage"] == "Indexando timestamps", indexing
        assert "4.200 / ~27.000" in indexing["progress"], indexing
        assert indexing["label"] == "Indexados" and indexing["read"] == "4.200", indexing
        assert "OCR ainda não começou" in page.locator("#activityLastOcr").inner_text()
        assert indexing["busy"] and "Indexando timestamps" in indexing["button"], indexing
        assert indexing["buttonColor"] != "rgb(32, 169, 102)", indexing
        assert "4.200 quadros" in indexing["history"], indexing
        assert indexing["pipelineState"] == "running" and indexing["pipelineStage"] == "indexing_timestamps", indexing
        assert indexing["spinner"] == "pipeline-spin" and indexing["progressMotion"] == "processing-sweep", indexing

        analysis = page.evaluate("""() => {
          const session={state:'running',source_start:170,requested_duration:730,config_json:{duration:730},
            progress:{stage:'analysis',frames_seen:1800,frames_total:21900,frames_analyzed:480,
              current_frame_id:840,current_source_time:192.5,ocr_calls:47,ocr_failures:2,
              events_created:3,events_deduplicated:1,current_region:'ROI 1',
              last_ocr_text:'Precisamos sair daqui.',last_ocr_confidence:.94,last_ocr_source_time:191.2,
              activity_message:'Frame 841 · 192.500s · 480 quadros amostrados · 47 consultas OCR · 3 diálogos',
              recent_activity:[{at:'2026-10-03T12:00:01Z',message:'Frame 841 · 192.500s · 47 consultas OCR'}]}};
          updateAnalysisActivity(session);
          updatePipeline(session);
          return{stage:$('#activityStage').textContent,frame:$('#activityFrame').textContent,
            read:$('#activityFramesRead').textContent,total:$('#activityFramesTotal').textContent,
            sampled:$('#activityFramesAnalyzed').textContent,calls:$('#activityOcrCalls').textContent,
            events:$('#activityEvents').textContent,last:$('#activityLastOcr').textContent,
            progress:$('#activityProgressText').textContent,
            activeStep:$('#pipelineSteps [data-step="ocr"]').classList.contains('active'),
            meterMotion:getComputedStyle($('#activityMeterFill'),'::after').animationName};
        }""")
        assert analysis["stage"] == "Analisando frames" and analysis["frame"] == "#841", analysis
        assert (analysis["read"], analysis["total"], analysis["sampled"], analysis["calls"], analysis["events"]) == (
            "1.800", "21.900", "480", "47", "3"), analysis
        assert "Precisamos sair daqui." in analysis["last"] and "ROI 1" in analysis["last"], analysis
        assert "consultas OCR" in analysis["progress"], analysis
        assert analysis["activeStep"] and analysis["meterMotion"] == "processing-sweep", analysis

        fallback_history = page.evaluate("""() => {
          updateAnalysisActivity({session_id:'legacy-session',state:'running',source_start:170,
            config_json:{duration:730},progress:{frames_seen:2200,frames_analyzed:500,ocr_calls:51,
              events_created:4,current_source_time:198.2}});
          return $('#activityHistory').innerText;
        }""")
        assert "2.200 lidos" in fallback_history and "51 OCR" in fallback_history, fallback_history
        page.emulate_media(reduced_motion="reduce")
        assert page.locator("#pipelineSteps [data-step='frames'] i").evaluate(
            "node => getComputedStyle(node).animationName") == "none"
        assert not errors, errors
        browser.close()
    print("Analysis activity smoke passed: timestamp indexing, frame position, OCR counters and recent history")


if __name__ == "__main__":
    main()

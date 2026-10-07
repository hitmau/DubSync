"""Browser check for timeline selection, duplicate, OCR retry, and completion choices."""

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
        assert page.goto("http://127.0.0.1:8766/", wait_until="networkidle").status == 200
        page.evaluate("""() => {
          const session={session_id:'test-session',source_type:'file',source_start:0,requested_duration:30,
            state:'complete',language:'pt',config_json:{path:'test.avi',duration:30,video_duration:60,block_index:1}};
          currentSession=session;sessions=[session];activeSessionId=session.session_id;
          $('#sessionFilter').append(new Option('Teste','test-session'));$('#sessionFilter').value='test-session';
          events=[{event_id:'event-a',session_id:'test-session',sequence:1,start_time:10,end_time:18,
            original_text:'Saia daqui.',processed_text:'Saia daqui.',corrected_text:null,confidence:.9,
            review_status:'pending',segment_id:'file',bbox_json:[0,0,20,20],assets_json:[],
            normalized_text:'saia daqui',frame_id:0,timing_quality:'exact_pts',timing_error_ms:0}];
          selectedEventId='';window.__seeks=[];window.__calls=[];
          window.seekVideoTo=(value)=>window.__seeks.push(value);window.videoClock=()=>20;
          window.toast=()=>{};window.refreshEvents=async()=>{};
          window.api=async(path,options={})=>{
            window.__calls.push({path,body:options.body?JSON.parse(options.body):null});
            if(path.endsWith('/duplicate')){const copy={...events[0],event_id:'event-b',sequence:2,start_time:20,end_time:28,segment_id:'duplicate'};events.push(copy);return copy;}
            if(path.endsWith('/retry-ocr'))return{processed_text:'Fuja agora!',original_text:'Fuja agora!',confidence:.95,source_time:22};
            if(path.endsWith('/review/correct'))return{};
            if(path.endsWith('/fine-sweep')&&options.method==='POST')return{run:{run_id:'audit',session_id:'test-session',state:'running',progress_percent:0,findings_json:[]}};
            if(path.endsWith('/fine-sweep'))return{run:fineSweepRun};
            throw new Error(`Unexpected API ${path}`);
          };
          renderRows();renderTimeline();
        }""")
        toolbar = page.evaluate("""() => {const item=document.querySelector('.timeline-heading');
          return {available:item.clientWidth,used:item.scrollWidth};}""")
        assert toolbar["used"] <= toolbar["available"] + 3, toolbar
        point = page.locator(".timeline-dialogue-point").first
        point.click()
        assert page.evaluate("window.__seeks.length") == 0
        assert page.evaluate("selectedEventId") == "event-a"
        point.dblclick()
        assert page.evaluate("window.__seeks") == [10]
        page.evaluate("window.__seeks=[];selectedEventId='';renderTimeline()")
        block = page.locator(".timeline-event").first
        block.click()
        assert page.evaluate("window.__seeks.length") == 0
        block.dblclick()
        assert page.evaluate("window.__seeks") == [10]

        page.locator("#duplicateDialogue").click()
        page.wait_for_function("events.length === 2")
        duplicate_call = page.evaluate("window.__calls.find(item => item.path.endsWith('/duplicate'))")
        assert duplicate_call["body"] == {"at_time": 20}
        assert page.evaluate("selectedEventId") == "event-b"
        page.locator("#retryDialogueOcr").click()
        assert page.locator("#ocrRetryDialog").evaluate("node => node.open")
        assert page.locator("#ocrRetryCandidate").input_value() == "Fuja agora!"
        page.locator("#ocrRetryApply").click()
        page.wait_for_function("window.__calls.some(item => item.path.endsWith('/review/correct'))")
        assert page.evaluate("window.__calls.find(item => item.path.endsWith('/review/correct')).body.corrected_text") == "Fuja agora!"

        page.evaluate("""() => {blockAutomationMode='manual';localStorage.removeItem('dubsync.blockCompleteNotice.test-session');
          $('#nextSegmentButton').disabled=false;showBlockCompleteChoice(currentSession);}""")
        assert page.locator("#blockCompleteDialog").evaluate("node => node.open")
        assert "100%" in page.locator("#blockCompleteTitle").inner_text()
        page.locator("#blockCompleteSweep").click()
        page.wait_for_function("fineSweepRun?.state === 'running'")
        assert page.locator("#fineSweepStatus").is_visible()
        page.evaluate("""() => {clearInterval(fineSweepPollTimer);fineSweepPollTimer=0;
          fineSweepRun={...fineSweepRun,state:'complete',progress_percent:100,
            findings_json:[{kind:'ocr_divergence',event_id:'event-a',source_time:10,observed_text:'Outro texto'},
              {kind:'missing_dialogue',event_id:null,source_time:25,observed_text:'Fala ausente'}]};
          renderFineSweepStatus();renderTimeline();renderRows();}""")
        assert page.locator(".timeline-event.fine-sweep-divergence").count() == 1
        assert page.locator(".timeline-missing-point").count() == 1
        assert page.locator(".event-row.fine-sweep-divergence").count() == 1
        assert page.locator(".timeline-panel.audit-active").count() == 1
        assert not errors, errors
        browser.close()
    print("Timeline review workflow smoke passed")


if __name__ == "__main__":
    main()

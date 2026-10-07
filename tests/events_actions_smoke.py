"""Smoke tests for event filtering/selection, Delete behavior, and block auto-advance."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".data" / "playwright-browsers"))
PROJECT_ID = os.environ.get("DUBSYNC_SMOKE_PROJECT_ID", "replaced")


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
        response = page.goto(f"http://127.0.0.1:8766/projects/{PROJECT_ID}/capture", wait_until="networkidle")
        assert response and response.status == 200
        page.wait_for_selector("#blockAutomationMode", state="attached")

        page.locator('.source-tab[data-mode="file"]').click()
        assert page.locator("#blockAutomationField").is_visible()
        page.locator("#blockAutomationMode").select_option("automatic")
        assert page.evaluate("localStorage.getItem('dubsync.blockAutomation.v1')") == "automatic"

        page.evaluate("""() => {
          const makeEvent=(id,sequence,start)=>({event_id:id,session_id:'session-test',sequence,
            start_time:start,end_time:start+1,original_text:`Line ${sequence}`,processed_text:`Line ${sequence}`,
            corrected_text:null,confidence:.9,review_status:'pending',segment_id:'file',assets_json:[],
            normalized_text:`line ${sequence}`,frame_id:0,timing_quality:'exact_pts',timing_error_ms:0});
          events=[makeEvent('event-a',1,1),makeEvent('event-b',2,3)];selectedEventId='';renderRows();
          window.__bulkCalls=[];window.api=async(path,options)=>{window.__bulkCalls.push([path,JSON.parse(options.body)]);return path.endsWith('/bulk-accept')?{confirmed:2}:{removed:2};};
          window.refreshEvents=async()=>{};window.clearSelected=()=>{};window.renderRows=()=>{};window.renderTimeline=()=>{};window.toast=()=>{};
        }""")
        page.locator("#selectAllEvents").check()
        assert page.locator(".merge-check:checked").count() == 2
        assert page.locator("#bulkAcceptButton").is_enabled()
        assert page.locator("#bulkRemoveButton").is_enabled()
        page.locator("#bulkAcceptButton").click()
        page.wait_for_function("window.__bulkCalls.length === 1")
        assert page.evaluate("window.__bulkCalls[0][0]") == "/api/events/bulk-accept"
        assert page.evaluate("window.__bulkCalls[0][1].event_ids") == ["event-a", "event-b"]
        page.locator("#bulkRemoveButton").click()
        page.wait_for_function("window.__bulkCalls.length === 2")
        assert page.evaluate("window.__bulkCalls[1][0]") == "/api/events/bulk-discard"
        assert page.evaluate("window.__bulkCalls[1][1].event_ids") == ["event-a", "event-b"]

        page.evaluate("""() => {
          events=[{event_id:'delete-test',session_id:'session-test',sequence:3,start_time:5,end_time:6,
            original_text:'Delete me',processed_text:'Delete me',confidence:.9,review_status:'pending'}];
          selectedEventId='delete-test';window.__deleteCalls=[];window.__confirmCalls=0;
          window.__permanentDiscard=false;
          window.api=async(path)=>{if(path==='/api/settings')return{review:{permanent_discard:window.__permanentDiscard}};window.__deleteCalls.push(path);return{};};
          window.refreshEvents=async()=>{events=[];selectedEventId='';};window.clearSelected=()=>{};
          window.renderRows=()=>{};window.renderTimeline=()=>{};window.toast=()=>{};
          window.confirm=()=>{window.__confirmCalls++;return false;};
          const player=$('#videoPlayer');player.classList.remove('hidden');
          Object.defineProperty(player,'paused',{configurable:true,value:false});document.activeElement.blur();
        }""")
        page.locator("#selectAllEvents").focus()
        page.keyboard.press("Delete")
        page.wait_for_function("window.__deleteCalls.length === 1")
        page.wait_for_function("events.length === 0 && selectedEventId === ''")
        assert page.evaluate("window.__confirmCalls") == 0
        assert page.evaluate("window.__deleteCalls") == ["/api/events/delete-test/review/discard"]

        page.evaluate("""() => {
          events=[{event_id:'delete-test-2',session_id:'session-test',sequence:4,start_time:7,end_time:8,
            original_text:'Confirm me',processed_text:'Confirm me',confidence:.9,review_status:'pending'}];
          window.__permanentDiscard=true;
          window.api=async(path)=>{if(path==='/api/settings')return{review:{permanent_discard:window.__permanentDiscard}};window.__deleteCalls.push(path);return{};};
          selectedEventId='delete-test-2';window.confirm=()=>{window.__confirmCalls++;return false;};
          Object.defineProperty($('#videoPlayer'),'paused',{configurable:true,value:true});document.activeElement.blur();
        }""")
        page.keyboard.press("Delete")
        page.wait_for_function("window.__confirmCalls === 1")
        assert page.evaluate("window.__deleteCalls.length") == 1, page.evaluate("window.__deleteCalls")

        page.evaluate("""async () => {
          events=[{event_id:'confirm-test',session_id:'session-test',sequence:5,start_time:10,end_time:11,
            original_text:'Confirm me',processed_text:'Confirm me',confidence:.9,review_status:'pending'}];
          selectedEventId='confirm-test';window.__seekCalls=[];window.__refreshArgs=[];
          window.api=async()=>({});window.seekVideoTo=(time)=>window.__seekCalls.push(time);
          window.refreshEvents=async(...args)=>window.__refreshArgs.push(args);
          window.videoClock=()=>10.5;timelineRange={start:5,span:20,end:25};
          $('#reviewStart').value='10';$('#reviewEnd').value='11';
          await saveReview('accept');
        }""")
        assert page.evaluate("window.__refreshArgs") == [[True, True]]
        assert page.evaluate("window.__seekCalls") == []
        assert page.evaluate("timelineRange.start") == 5

        page.evaluate("""async () => {
          const remaining={event_id:'remaining',session_id:'session-test',sequence:6,start_time:30,end_time:31,
            original_text:'Remaining',processed_text:'Remaining',confidence:.9,review_status:'pending'};
          events=[remaining];window.__focusSelection=[];window.refreshEvents=async()=>{};
          window.selectEvent=(...args)=>window.__focusSelection.push(args);
          timelineViews={};timelineRange={start:45,span:30,end:75};
          await focusAfterRemoval(['removed'],'session-test',{sessionId:'session-test',start:45,span:30,playhead:61});
        }""")
        assert page.evaluate("timelineViews['session-test']") == {"start": 45, "span": 30}
        assert page.evaluate("window.__focusSelection") == [["remaining", False, False, True]]

        page.evaluate("""async () => {
          const previous='parent-session';window.__nextPayload=null;
          blockAutomationMode='automatic';autoNextStartedForSession='';preparePending=false;
          currentSession={session_id:previous,source_type:'file',state:'complete',source_start:0,requested_duration:600,
            config_json:{mode:'file',path:'video.mp4',duration:600,video_duration:3600}};
          overviewSessions=[];$('#nextBlockMinutes').value='20';
          window.loadVideoOverviewPlan=async()=>true;window.updateNextBlockControl=()=>{};window.updatePlaybackButton=()=>{};
          window.updateInitialRangeUi=()=>{};window.refreshSessions=async()=>{};window.refreshSession=async()=>{};
          window.showSourceTimeInPlayer=async()=>true;window.refreshEvents=async()=>{};window.renderVideoOverview=()=>{};
          window.api=async(path,options)=>{if(path==='/api/file/next-block'){window.__nextPayload=JSON.parse(options.body);return{session_id:'child-session',start:600,duration:1200,block_index:2};}throw new Error(`Unexpected API: ${path}`);};
          await maybeAutoStartNextBlock();
        }""")
        assert page.evaluate("window.__nextPayload") == {
            "previous_session_id": "parent-session", "duration_seconds": 1200
        }
        assert not errors, errors
        browser.close()
    print("Event action smoke passed: Delete matches the discard button, permanent deletion confirms, review preserves the cursor, and automatic block continuation")


if __name__ == "__main__":
    main()

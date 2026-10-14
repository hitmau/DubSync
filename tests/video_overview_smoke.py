"""Smoke de navegação entre blocos e do mapa temporal global do vídeo."""

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
        response = page.goto("http://127.0.0.1:8766/projects/replaced/capture", wait_until="networkidle")
        assert response and response.status == 200
        page.wait_for_selector("#videoOverview")

        values = page.evaluate("""async () => {
          const old={api,attachActiveSession,showSourceTimeInPlayer,refreshSession,refreshSessions,refreshEvents};
          const root={session_id:'block-1',state:'complete',source_start:170,requested_duration:730,
            created_at:'2026-10-01T00:00:00',config_json:{block_index:1,duration:730,video_duration:4500,
              path:'C:/Videos/replaced.mp4',block_duration_requested:1200}};
          const child={session_id:'block-2',state:'complete',source_start:900,requested_duration:1200,
            created_at:'2026-10-01T00:20:00',config_json:{block_index:2,duration:1200,video_duration:4500,
              previous_session_id:'block-1',block_duration_requested:1200,path:'C:/Videos/replaced.mp4'}};
          overviewSessions=[root,child];overviewSessionId='block-2';overviewVideoDuration=3300;
          sessions=[root,child];currentSession=child;activeSessionId='block-2';
          renderVideoOverview();
          const blocks=[...document.querySelectorAll('#overviewBlocks .overview-block')];
          const first=blocks[0],second=blocks[1],third=blocks[2],fourth=blocks[3];
          const snapshot={count:blocks.length,progress:$('#overviewProgressLabel').textContent,
            states:blocks.map(node=>node.className),firstLeft:first.style.left,firstWidth:first.style.width,
            lastEnd:Number.parseFloat(fourth.style.left)+Number.parseFloat(fourth.style.width),
            previousDisabled:$('#previousBlockButton').disabled};

          let opened=null,seeked=null,postedNext=false;
          api=async(url,options={})=>{
            if(url==='/api/sessions/block-1/video-plan')return{video_duration:4500,sessions:[root,child]};
            if(url==='/api/sessions/block-1')return root;
            if(url==='/api/sessions/block-2')return child;
            if(url==='/api/file/next-block'&&options.method==='POST')postedNext=true;
            return{};
          };
          attachActiveSession=async(session)=>{opened=session.session_id;currentSession=session;activeSessionId=session.session_id;};
          showSourceTimeInPlayer=async(time)=>{seeked=time;return true;};
          refreshSession=async()=>{};refreshSessions=async()=>{};refreshEvents=async()=>{};
          await goToPreviousBlock();
          const previous={opened,seeked};

          currentSession=root;activeSessionId='block-1';
          await beginNextBlock();clearInterval(pollTimer);
          const next={opened,seeked,postedNext};

          currentSession=root;activeSessionId='block-1';overviewSessions=[root];postedNext=false;
          api=async()=>{throw new Error('offline');};
          await beginNextBlock();clearInterval(pollTimer);
          const offline={postedNext};

          Object.assign(window,{api:old.api,attachActiveSession:old.attachActiveSession,
            showSourceTimeInPlayer:old.showSourceTimeInPlayer,refreshSession:old.refreshSession,
            refreshSessions:old.refreshSessions,refreshEvents:old.refreshEvents});
          return{snapshot,previous,next,offline};
        }""")
        snapshot = values["snapshot"]
        assert snapshot["count"] == 4, snapshot
        assert snapshot["progress"].startswith("50.0% · 2/4"), snapshot
        assert "complete" in snapshot["states"][0] and "complete" in snapshot["states"][1], snapshot
        assert "pending" in snapshot["states"][2] and "pending" in snapshot["states"][3], snapshot
        assert snapshot["previousDisabled"] is False, snapshot
        assert abs(snapshot["lastEnd"] - 100) < 0.02, snapshot
        assert values["previous"] == {"opened": "block-1", "seeked": 170}, values
        assert values["next"] == {"opened": "block-2", "seeked": 900, "postedNext": False}, values
        assert values["offline"] == {"postedNext": False}, values
        assert not errors, errors
        page.screenshot(path=str(ROOT / ".data" / "video-overview-smoke.png"))
        browser.close()
    print("Video overview smoke passed: full duration, planned blocks, progress, previous/next navigation")


if __name__ == "__main__":
    main()

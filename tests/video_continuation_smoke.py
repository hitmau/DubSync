"""Read-only browser smoke for the multi-video capture map and file selector."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".data" / "playwright-browsers"))


def main() -> None:
    executable = next(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]).glob("chromium-*/chrome-win64/chrome.exe"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        response = page.goto("http://127.0.0.1:8766/projects/replaced/capture", wait_until="networkidle")
        assert response and response.status == 200
        page.wait_for_selector("#videoSectorControls")
        result = page.evaluate("""async () => {
          clearInterval(pollTimer); clearInterval(sessionWatchTimer);
          const first = Array.from({length: 7}, (_, n) => ({
            session_id: `test-${n + 1}`, state: 'complete', source_type: 'file',
            source_start: n * 1200, requested_duration: 1200,
            config_json: {path:'C:/Videos/parte-1.mp4',video_duration:8400,duration:1200,
              block_index:n + 1,block_duration_requested:1200,
              ...(n ? {previous_session_id:`test-${n}`} : {})},
            sector_index:1,
          }));
          const sectors = [{index:1,path:'C:/Videos/parte-1.mp4',duration:8400,offset:0,
            block_duration_seconds:1200,first_session_id:'test-1',planned:false},
            {index:2,path:'C:/Videos/parte-2.mp4',duration:15600,offset:8400,
              block_duration_seconds:1200,first_session_id:null,planned:true}];
          sessions = first; overviewSessions = first; overviewSectors = sectors;
          overviewVideoDuration = 24000; currentSession = first[0]; activeSessionId = 'test-1';
          renderVideoOverview(); updateVideoSectorControls();
          const planned = {count:document.querySelectorAll('#overviewBlocks .overview-block').length,
            label:$('#overviewCurrentLabel').textContent,progress:$('#overviewProgressLabel').textContent,
            choices:[...$('#videoSectorSelect').options].map(item=>item.textContent)};
          const eighth = {session_id:'test-8',state:'complete',source_type:'file',source_start:0,
            requested_duration:1200,sector_index:2,config_json:{path:'C:/Videos/parte-2.mp4',
              video_duration:15600,duration:1200,block_index:8,sector_index:2,
              previous_session_id:'test-7',block_duration_requested:1200}};
          sessions = [...first,eighth]; overviewSessions = sessions;
          overviewSectors[1] = {...sectors[1],first_session_id:'test-8',planned:false};
          currentSession = eighth; activeSessionId = 'test-8';
          renderVideoOverview(); updateVideoSectorControls();
          const continued = {count:document.querySelectorAll('#overviewBlocks .overview-block').length,
            label:$('#overviewCurrentLabel').textContent,progress:$('#overviewProgressLabel').textContent,
            selected:$('#videoSectorSelect').value,
            previous:[...$('#videoSectorSelect').options].find(item=>item.value==='1')?.textContent};
          let reopened = '';
          api = async path => first[0];
          attachActiveSession = async session => {reopened=session.session_id;currentSession=session;};
          showSourceTimeInPlayer = async () => true;
          $('#videoSectorSelect').value='1'; await selectVideoSector();
          return {planned,continued,reopened};
        }""")
        assert result["planned"]["count"] == 20, result
        assert result["planned"]["progress"].startswith("35.0% · 7/20"), result
        assert "Bloco 1/20" in result["planned"]["label"], result
        assert result["continued"]["count"] == 20, result
        assert result["continued"]["selected"] == "2", result
        assert "Bloco 8/20" in result["continued"]["label"], result
        assert "parte-1.mp4" in result["continued"]["previous"], result
        assert result["reopened"] == "test-1", result
        page.locator("#sourcePanel").screenshot(path=str(ROOT / ".data" / "video-continuation-source.png"))
        browser.close()
    print("Multi-video capture smoke passed: 7/20, 8/20, and previous-file selector")


if __name__ == "__main__":
    main()

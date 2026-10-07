"""Smoke visual da configuração ROI e acompanhamento da timeline no Chromium local."""

from __future__ import annotations

import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".data" / "playwright-browsers"))


def main() -> None:
    executable = next(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]).glob("chromium-*/chrome-win64/chrome.exe"))
    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.set_default_timeout(8000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/sessions", lambda route: route.fulfill(status=200, content_type="application/json", body='{"sessions":[]}'))
        page.route("**/api/events**", lambda route: route.fulfill(status=200, content_type="application/json", body='{"events":[]}'))
        def roi_characters(route) -> None:
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"characters":[{"character_id":"mara","name":"Mara","photo_name":"mara.jpg","photo_url":"data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="},{"character_id":"nika","name":"Nika","photo_name":"nika.jpg","photo_url":"data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs="}]}',
            )

        page.route(re.compile(r".*/api/roi-characters(?:/.*|\?.*)?$"), roi_characters)
        response = page.goto("http://127.0.0.1:8766/", wait_until="networkidle")
        assert response and response.status == 200
        page.locator("#sourceStart").fill("00:02:50")
        page.locator("#sourceDuration").fill("00:15:00")
        interval, payload = page.evaluate("""async () => {
          const interval=getInitialRange(),oldApi=api,oldRefreshSessions=refreshSessions,oldRefreshSession=refreshSession;
          let payload=null;
          api=async(url,options={})=>{if(url==='/api/file/start'){payload=JSON.parse(options.body);return{session_id:'range-smoke',start:payload.start,duration:payload.duration,requested_duration:payload.duration};}return{};};
          refreshSessions=async()=>{};refreshSession=async()=>{};setMode('file');$('#filePath').value='C:/Videos/range-smoke.mp4';
          await beginFile();clearInterval(pollTimer);api=oldApi;refreshSessions=oldRefreshSessions;refreshSession=oldRefreshSession;
          preparePending=false;activeSessionId='';currentSession=null;updateInitialRangeUi(null);
          return[interval,payload];
        }""")
        assert interval == {"start": 170, "end": 900, "duration": 730}
        assert payload["start"] == 170 and payload["duration"] == 730
        assert "00:02:50 → 00:15:00" in page.locator("#initialRangeSummary").inner_text()
        bounds = page.evaluate("videoSourceBounds()")
        assert bounds["start"] == 170 and bounds["end"] == 900 and bounds["duration"] == 730
        page.evaluate("updateInitialRangeUi({state:'processing',source_start:170,requested_duration:730,config_json:{block_index:1,duration:730}})")
        assert page.locator("#sourceStart").is_disabled()
        page.evaluate("updateInitialRangeUi({state:'complete',source_start:170,requested_duration:730,config_json:{block_index:1,duration:730}})")
        assert not page.locator("#sourceStart").is_disabled()
        page.evaluate("updateInitialRangeUi({source_start:900,config_json:{block_index:2,duration:1200}})")
        assert page.locator("#initialRangeConfig").is_hidden()
        assert "Bloco 2: 00:15:00 → 00:35:00" in page.locator("#continuationRangeSummary").inner_text()
        page.evaluate("updateInitialRangeUi(null)")
        page.locator(".roi-settings-button").first.click()
        assert page.locator("#roiSettingsDialog").is_visible()
        page.locator('[data-roi-tab="speaker"]').click()
        page.locator("#roiCharacters .roi-character").nth(1).wait_for(state="visible")
        assert page.locator("#roiCharacterLoadStatus").is_hidden()
        assert ".wav" in (page.locator(".roi-character-file").first.get_attribute("accept") or "").lower()
        assert not page.locator("#roiDialogSave").is_disabled()
        page.locator('[data-roi-tab="lifetime"]').click()
        page.locator("#roiLifetimeEnabled").check()
        page.locator("#roiAddWindow").click()
        page.locator("#roiLifetimeRows .roi-start").fill("10")
        page.locator("#roiLifetimeRows .roi-end").fill("30")
        page.locator('[data-roi-tab="speaker"]').click()
        page.locator("#roiSpeakerEnabled").check()
        page.locator('[data-roi-tab="motion"]').click()
        page.locator("#roiMotionEnabled").check()
        page.locator("#roiAddPoint").click()
        page.locator("#roiMotionRows .roi-time").last.fill("20")
        page.screenshot(path=str(ROOT / ".data" / "roi-dialog-smoke.png"))
        page.locator("#roiDialogSave").click()
        page.locator("#roiSettingsDialog").wait_for(state="hidden")
        settings = page.evaluate("parseRegions()[0]")
        assert settings["lifetime"]["windows"][0] == {"mode": "relative", "start": 10, "end": 30}
        assert settings["speaker_enabled"] and settings["speaker_region"]
        assert settings["motion"]["enabled"] and len(settings["motion"]["keyframes"]) == 2

        page.evaluate("""() => {
          const frame=document.createElement('canvas');frame.width=640;frame.height=360;
          const context=frame.getContext('2d');context.fillStyle='#18304a';context.fillRect(0,0,640,360);
          drawImage({image:frame.toDataURL(),width:640,height:360},'file');
        }""")
        page.wait_for_function("preview?.width===640")
        page.locator(".roi-settings-button").first.click()
        page.locator('[data-roi-tab="speaker"]').click()
        page.locator("#roiMarkSpeaker").click()
        assert not page.locator("#roiSettingsDialog").is_visible()
        assert "interactive" in (page.locator("#previewCanvas").get_attribute("class") or "")
        box = page.locator("#previewCanvas").bounding_box()
        assert box
        page.mouse.move(box["x"] + box["width"] * .32, box["y"] + box["height"] * .36)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * .56, box["y"] + box["height"] * .43, steps=4)
        page.mouse.up()
        assert page.locator("#roiSettingsDialog").is_visible()
        marked = page.evaluate("parseRegions()[0].speaker_region")
        assert marked["width"] > .1 and marked["height"] > .05
        page.locator("#roiDialogSave").click()
        page.locator("#roiSettingsDialog").wait_for(state="hidden")

        page.evaluate("""() => {
          activeSessionId='smoke';
          sessions=[{session_id:'smoke', source_start:170, requested_duration:60, config_json:{start:170,duration:60}}];
          currentSession=sessions[0];
          events=[{event_id:'event-smoke',session_id:'smoke',sequence:7,start_time:180,end_time:184,
            original_text:'Precisamos sair daqui.',confidence:.94,review_status:'pending',region_id:'roi-smoke',character:'Mara',
            bbox_json:[1,2,3,4],assets_json:[],crop_path:null}];
          renderRows();renderTimeline();
        }""")
        assert "Precisamos sair daqui" in page.locator(".timeline-dialogue-point").get_attribute("title")
        page.locator(".timeline-dialogue-point").hover()
        assert "Precisamos sair daqui" in page.locator("#timelineTooltip").inner_text()
        page.locator("#followDialogue").click()
        page.evaluate("updateTimelinePlayback(180.05)")
        assert page.locator("#selectedEventLabel").inner_text().startswith("#7")
        assert page.locator("#followDialogue").get_attribute("aria-pressed") == "true"
        accepted: list[dict] = []

        def accept(route) -> None:
            accepted.append(route.request.post_data_json)
            route.fulfill(status=200, content_type="application/json", body="{}")

        page.route("**/api/events/event-smoke/review/accept", accept)
        assert page.locator("#reviewCharacter").input_value() == "Mara"
        page.locator("#reviewCharacterToggle").click()
        page.get_by_role("option", name="Nika").wait_for(state="visible")
        assert page.get_by_role("option", name="Mara").is_visible()
        assert page.locator("#characterOptions .character-option-avatar img").count() >= 2
        page.get_by_role("option", name="Nika").click()
        assert page.locator("#reviewCharacter").input_value() == "Nika"
        page.locator("#reviewCharacter").fill("Mara")
        page.get_by_role("option", name="Mara").wait_for(state="visible")
        assert page.get_by_role("option", name="Nika").count() == 0
        page.locator("#acceptButton").click()
        page.wait_for_timeout(100)
        assert accepted and accepted[0]["character"] == "Mara"
        assert page.locator(".roi-settings-button").first.get_attribute("title")
        assert not errors, errors
        browser.close()
    print("ROI UI smoke passed: lifetime, speaker, motion, dialogue tooltip, follow selection")


if __name__ == "__main__":
    main()

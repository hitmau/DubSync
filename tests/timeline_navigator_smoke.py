"""Verifica zoom, navegação pelo bloco completo e retorno da timeline ampliada."""

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
        page.wait_for_selector("#timelineNavigatorTrack")

        page.evaluate("""() => {
          const session={session_id:'timeline-test',state:'complete',source_type:'file',source_start:0,
            requested_duration:600,config_json:{duration:600,path:'C:/video.mp4'}};
          sessions=[session];currentSession=session;activeSessionId=session.session_id;
          const filter=$('#sessionFilter');filter.add(new Option('Test','timeline-test'));filter.value='timeline-test';
          events=[{event_id:'event-1',session_id:'timeline-test',sequence:1,start_time:490,end_time:500,
            original_text:'Um diálogo de teste.',confidence:.9,review_status:'pending'}];
          timelineViews={};renderTimeline();zoomTimeline(.25);
        }""")
        initial = page.evaluate("""() => ({start:timelineRange.start,span:timelineRange.span,
          thumbWidth:$('#timelineNavigatorWindow').getBoundingClientRect().width,
          trackWidth:$('#timelineNavigatorTrack').getBoundingClientRect().width,
          labels:[$('#timelineNavigatorStart').textContent,$('#timelineNavigatorEnd').textContent]})""")
        assert initial["start"] == 225 and initial["span"] == 150, initial
        assert 0 < initial["thumbWidth"] < initial["trackWidth"], initial
        assert initial["labels"] == ["00:00:00", "00:10:00"], initial

        track = page.locator("#timelineNavigatorTrack")
        track_box = track.bounding_box()
        assert track_box
        page.mouse.click(track_box["x"] + track_box["width"] * .8, track_box["y"] + track_box["height"] / 2)
        after_click = page.evaluate("timelineRange.start")
        assert abs(after_click - 405) < 2, after_click

        thumb_box = page.locator("#timelineNavigatorWindow").bounding_box()
        assert thumb_box
        page.mouse.move(thumb_box["x"] + thumb_box["width"] / 2, thumb_box["y"] + thumb_box["height"] / 2)
        page.mouse.down()
        page.mouse.move(track_box["x"] + track_box["width"] - thumb_box["width"] / 2,
                        track_box["y"] + track_box["height"] / 2, steps=5)
        page.mouse.up()
        after_drag = page.evaluate("timelineRange.start")
        assert abs(after_drag - 450) < 2, after_drag
        visible_range = page.locator("#timelineNavigatorWindow").get_attribute("aria-valuetext")
        assert "00:07:2" in visible_range, visible_range

        page.locator("#timelineZoomFit").click()
        fitted = page.evaluate("({start:timelineRange.start,span:timelineRange.span,disabled:$('#timelineNavigatorWindow').disabled})")
        assert fitted["start"] == 0 and fitted["span"] == 600 and fitted["disabled"], fitted
        assert not errors, errors
        browser.close()
    print("Timeline navigator smoke passed: proportional zoom thumb, full-block seek, drag, and fit")


if __name__ == "__main__":
    main()

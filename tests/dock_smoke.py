"""Smoke test do encaixe de painéis na interface local do DubSync."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / ".data" / "playwright-browsers"))


def main() -> None:
    errors: list[str] = []
    browser_root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    executable = next(browser_root.glob("chromium-*/chrome-win64/chrome.exe"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""() => {
          const key='dubsync.docks.v1';
          if(localStorage.getItem(key))return;
          const group=(id,weight=1)=>({panels:[id],active:id,weight});
          localStorage.setItem(key,JSON.stringify({zones:{
            left:[group('sourcePanel',1.6),group('sessionPanel',1)],
            center:[group('previewPanel',1.35),group('timelinePanel',.52),group('eventList',.88)],
            right:[group('processing',.66),group('eventDetailPanel',1)],
            bottom:[group('review')],float:[]},collapsed:[],homes:{}}));
        }""")
        page.route("**/api/sessions", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"sessions":[]}'))
        page.route("**/api/events**", lambda route: route.fulfill(
            status=200, content_type="application/json", body='{"events":[]}'))
        response = page.goto("http://127.0.0.1:8766/", wait_until="networkidle")
        assert response and response.status == 200
        page.wait_for_selector("#appGrid.dock-enabled")
        assert not errors, errors
        assert page.locator(".dock-group").count() == 9
        assert page.locator(".dock-collapse").count() == 9
        assert page.locator(".center-column > .dock-group #videoOverview").count() == 1
        assert page.locator("#dockBottom #review").count() == 1

        review = page.locator("#review")
        review_width_before = review.bounding_box()["width"]
        capture_height_before = page.locator("#capture").bounding_box()["height"]
        review.locator(".dock-collapse").click()
        page.wait_for_selector("#appGrid.bottom-compact")
        review_width_after = review.bounding_box()["width"]
        capture_height_after = page.locator("#capture").bounding_box()["height"]
        bottom_row_height = page.evaluate("parseFloat(getComputedStyle($('#appGrid')).gridTemplateRows.split(' ').at(-1))")
        assert abs(review_width_after - review_width_before) < 2, (review_width_before, review_width_after)
        assert capture_height_after > capture_height_before + 100, (capture_height_before, capture_height_after)
        assert bottom_row_height <= 50, bottom_row_height
        review.locator(".dock-collapse").click()
        page.wait_for_function("!document.querySelector('#appGrid').classList.contains('bottom-compact')")

        source = page.locator("#sourcePanel")
        source.locator(".dock-collapse").click()
        assert "dock-collapsed" in source.get_attribute("class")
        assert source.bounding_box()["height"] <= 34
        assert source.get_by_text("Fonte de Vídeo", exact=True).is_visible()
        source.locator(".dock-collapse").click()
        assert "dock-collapsed" not in source.get_attribute("class")

        source.locator(".dock-float-toggle").click()
        assert page.locator("#dockFloatLayer #sourcePanel").count() == 1
        floating = source.locator("xpath=..")
        float_before = floating.bounding_box()
        assert float_before
        header_box = source.locator(".panel-title").bounding_box()
        assert header_box
        page.mouse.move(header_box["x"] + 90, header_box["y"] + 15)
        page.mouse.down()
        page.mouse.move(header_box["x"] + 170, header_box["y"] + 75, steps=5)
        page.mouse.up()
        float_moved = floating.bounding_box()
        assert float_moved and float_moved["x"] > float_before["x"] + 50
        grip_box = floating.locator(".float-resize").bounding_box()
        assert grip_box
        page.mouse.move(grip_box["x"] + 8, grip_box["y"] + 8)
        page.mouse.down()
        page.mouse.move(grip_box["x"] + 68, grip_box["y"] + 48, steps=5)
        page.mouse.up()
        float_resized = floating.bounding_box()
        assert float_resized and float_resized["width"] > float_moved["width"] + 40
        page.screenshot(path=str(ROOT / ".data" / "dock-floating-1920x1080.png"))
        page.reload(wait_until="networkidle")
        page.wait_for_selector("#appGrid.dock-enabled")
        assert page.locator("#dockFloatLayer #sourcePanel").count() == 1
        assert page.locator("#sourcePanel").locator("xpath=..").bounding_box()["width"] > float_moved["width"] + 40
        page.locator("#sourcePanel .dock-float-toggle").click()
        assert page.locator(".left-column #sourcePanel").count() == 1

        timeline_header = page.locator("#timelinePanel .timeline-heading")
        preview = page.locator("#previewPanel")
        timeline_header.drag_to(preview, target_position={"x": 400, "y": 150})
        page.wait_for_function("document.querySelector('#timelinePanel').parentElement === document.querySelector('#previewPanel').parentElement")
        group = page.locator("#previewPanel").locator("xpath=..")
        assert group.locator(".dock-tab").count() == 2
        group.locator('.dock-tab[data-panel-id="previewPanel"]').click()
        assert preview.is_visible() and page.locator("#timelinePanel").is_hidden()
        group.locator('.dock-tab[data-panel-id="timelinePanel"]').click()
        assert page.locator("#timelinePanel").is_visible() and preview.is_hidden()
        page.screenshot(path=str(ROOT / ".data" / "dock-tabs-1920x1080.png"))

        processing = page.locator("#processing")
        processing_box = processing.bounding_box()
        assert processing_box
        group.locator('.dock-tab[data-panel-id="timelinePanel"]').drag_to(
            processing, target_position={"x": 140, "y": processing_box["height"] - 8})
        page.wait_for_function("document.querySelector('#timelinePanel').parentElement.parentElement.classList.contains('right-column')")
        assert page.locator(".right-column > .dock-group").count() == 3
        assert page.locator("#timelinePanel").locator("xpath=..").locator(".dock-tab").count() == 0
        before_height = processing.bounding_box()["height"]
        divider = page.locator(".right-column > .dock-divider").first
        divider_box = divider.bounding_box()
        assert divider_box
        x = divider_box["x"] + divider_box["width"] / 2
        y = divider_box["y"] + divider_box["height"] / 2
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x, y + 35, steps=5)
        page.mouse.up()
        assert processing.bounding_box()["height"] > before_height + 15

        review_header = page.locator("#review .review-dock-title")
        status = page.locator("#sessionPanel")
        status_box = status.bounding_box()
        assert status_box
        review_header.drag_to(status, target_position={"x": 100, "y": status_box["height"] - 7})
        page.wait_for_function("document.querySelector('#review').parentElement.parentElement.classList.contains('left-column')")
        assert page.locator("#appGrid.no-bottom").count() == 1
        page.locator("#review .dock-collapse").click()
        assert page.locator("#review").get_by_text("Revisão e Edição", exact=True).is_visible()
        page.locator("#review .dock-collapse").click()
        page.locator("#review .review-dock-title").drag_to(page.locator("#dockBottom"))
        page.wait_for_function("document.querySelector('#review').parentElement.parentElement.id === 'dockBottom'")
        assert page.locator("#appGrid.no-bottom").count() == 0
        page.locator("#sessionPanel .dock-collapse").click()

        page.reload(wait_until="networkidle")
        page.wait_for_selector("#appGrid.dock-enabled")
        assert page.locator(".right-column > .dock-group").count() == 3
        assert page.locator("#timelinePanel").is_visible()
        assert page.locator("#dockBottom #review").count() == 1
        assert "dock-collapsed" in page.locator("#sessionPanel").get_attribute("class")
        assert not errors, errors

        page.locator("#resetDockLayout").click()
        page.wait_for_selector("#appGrid.dock-enabled")
        assert page.locator("#previewPanel").locator("xpath=..").locator(".dock-tab").count() == 0
        assert page.locator(".center-column > .dock-group").count() == 4
        page.screenshot(path=str(ROOT / ".data" / "dock-smoke-1920x1080.png"))
        browser.close()
    print("Dock smoke passed: collapse, floating move/resize, tab docking, cross-column drag, persistence and reset")


if __name__ == "__main__":
    main()

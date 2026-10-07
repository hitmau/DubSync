"""Verifies split dialog timing, playhead selection, and midpoint fallback."""

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
        page.wait_for_selector("#splitButton")
        page.evaluate("""() => {
          events=[{event_id:'split-smoke',session_id:'session-test',sequence:1,start_time:181.6,
            end_time:191.867,original_text:'Two lines recognized as one',processed_text:'Two lines recognized as one',
            confidence:.9,review_status:'pending'}];selectedEventId='split-smoke';
          $('#splitButton').disabled=false;
          window.videoClock=()=>187.123;window.__splitCalls=[];window.__splitBody=null;window.__toastMessages=[];
          window.api=async(path,options)=>{
            window.__splitCalls.push(path);
            if(path.includes('/split-preview')){
              await new Promise(resolve=>setTimeout(resolve,350));
              const splitTime=Number(new URL('http://127.0.0.1'+path).searchParams.get('split_time'));
              return{image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4l8AAAAASUVORK5CYII=',source_time:splitTime,timing_quality:'exact_pts',timing_error_ms:0};
            }
            if(path.endsWith('/split')){window.__splitBody=JSON.parse(options.body);return{};}
            if(path==='/api/settings')return options?.method==='PUT'
              ? JSON.parse(options.body).settings : {review:{show_split_preview:true}};
            throw new Error(`Unexpected API: ${path}`);
          };
          window.refreshEvents=async()=>{};window.toast=(message)=>window.__toastMessages.push(message);
        }""")

        page.locator("#splitButton").click(force=True)
        assert page.locator("#splitDialog").evaluate("element => element.open"), "Modal deve abrir antes de buscar a prévia"
        assert page.locator("#splitTimeInput").input_value() == "187.123"
        assert "cursor está dentro" in page.locator("#splitReason").inner_text()
        assert page.locator("#confirmSplit").is_disabled()
        assert page.locator("#splitPreviewPlaceholder").is_visible()
        assert page.locator("#splitPreviewImage").is_hidden()
        page.wait_for_function("window.__splitCalls.some(path => path.includes('/split-preview'))")
        page.wait_for_function("$('#splitPreviewImage').naturalWidth > 0 && !$('#confirmSplit').disabled")
        assert page.locator("#splitPreviewImage").is_visible()
        assert "Frame mais próximo" in page.locator("#splitTimeLabel").inner_text()
        page.locator("#splitTimeInput").fill("188.25")
        page.locator("#confirmSplit").click()
        page.wait_for_function("window.__splitBody !== null")
        assert page.evaluate("window.__splitBody.split_time") == 188.25

        page.evaluate("""() => {window.videoClock=()=>200;$('#splitButton').click();} """)
        assert page.locator("#splitDialog").evaluate("element => element.open")
        assert abs(float(page.locator("#splitTimeInput").input_value()) - 186.7335) <= .001
        assert "fora deste diálogo" in page.locator("#splitReason").inner_text()
        assert "ponto médio" in page.locator("#splitReason").inner_text()
        page.locator("#cancelSplit").click()
        page.locator("[data-settings-open]").click()
        page.locator('.settings-tab[data-settings-tab="review"]').click()
        assert page.locator("#cfgSplitPreview").is_checked()
        page.locator("#cfgSplitPreview").uncheck()
        page.locator("#saveSettings").click()
        page.wait_for_function("showSplitPreview === false")
        page.locator("#closeSettings").click()
        page.evaluate("""() => {window.__splitCalls=[];
          window.videoClock=()=>187.123;$('#splitButton').click();}""")
        assert page.locator("#splitDialog").evaluate("element => element.open")
        assert page.locator("#splitDialog").evaluate("element => element.classList.contains('no-preview')")
        assert page.locator("#splitPreviewImage").is_hidden()
        assert page.locator("#previewSplit").is_hidden()
        assert page.locator("#confirmSplit").is_enabled()
        page.locator("#splitTimeInput").fill("188.25")
        assert page.locator("#confirmSplit").is_enabled()
        assert "Prévia desativada" in page.locator("#splitTimeLabel").inner_text()
        assert not page.evaluate("window.__splitCalls.some(path => path.includes('/split-preview'))")
        page.locator("#confirmSplit").click()
        page.wait_for_function("window.__splitBody !== null")
        assert page.evaluate("window.__splitBody.split_time") == 188.25
        assert not errors, errors
        browser.close()
    print("Split cursor smoke passed: playhead, midpoint, and direct split without preview")


if __name__ == "__main__":
    main()

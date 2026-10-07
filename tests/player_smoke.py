"""Live smoke for a downloaded YouTube session; requires the local app on port 8766."""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8766"


def main() -> None:
    with urllib.request.urlopen(f"{BASE_URL}/api/sessions", timeout=5) as response:
        sessions = json.load(response)["sessions"]
    session = next(item for item in sessions
                   if item["source_type"] == "youtube"
                   and item["config_json"].get("downloaded_path")
                   and item["state"] == "preparing")
    session_id = session["session_id"]
    errors: list[str] = []
    browser_root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    executable = next(browser_root.glob("chromium-*/chrome-win64/chrome.exe"))
    screenshot = Path(__file__).resolve().parents[1] / ".data" / "player-preview-smoke.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_function(
            """(id) => document.querySelector('#sessionFilter').value === id
                && document.querySelector('#fileOrigin').classList.contains('hidden') === false""",
            arg=session_id,
            timeout=30_000,
        )
        page.wait_for_function(
            """() => {const video=document.querySelector('#videoPlayer');
                return video.readyState >= 2 && !video.seeking && video.videoWidth > 0;}""",
            timeout=60_000,
        )
        video = page.locator("#videoPlayer")
        info = video.evaluate("v => ({currentTime:v.currentTime,duration:v.duration,width:v.videoWidth,height:v.videoHeight,muted:v.muted})")
        assert page.locator('.source-tab.active').get_attribute("data-mode") == "file"
        assert "/videos/" in page.locator("#filePath").input_value().replace("\\", "/").lower()
        assert "Baixado do YouTube" in page.locator("#fileOrigin").inner_text()
        assert page.locator("#sessionState").inner_text() == "Pronto para calibrar e iniciar"
        assert info["width"] == 3840 and info["height"] == 2160 and info["muted"]
        assert abs(info["currentTime"] - float(session["source_start"])) < 2

        start = info["currentTime"]
        page.locator("#previewPlay").click()
        page.wait_for_function("start => document.querySelector('#videoPlayer').currentTime > start + .4", arg=start)
        page.locator("#previewPause").click()
        page.evaluate("""() => {const input=document.querySelector('#seekSlider');input.value='180';
            input.dispatchEvent(new Event('input',{bubbles:true}));}""")
        page.wait_for_function("Math.abs(document.querySelector('#videoPlayer').currentTime - 180) < .5")
        page.screenshot(path=str(screenshot), full_page=True)
        final_time = video.evaluate("v => v.currentTime")
        browser.close()

    assert not errors, errors
    print(f"PASS session={session_id} mode=file video={info['width']}x{info['height']} duration={info['duration']:.2f}s")
    print(f"PASS player played, paused, sought to 180s (actual {final_time:.3f}s); screenshot={screenshot}")


if __name__ == "__main__":
    main()

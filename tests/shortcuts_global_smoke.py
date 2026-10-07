"""Confirma que atalhos de playback funcionam fora do foco da timeline."""

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
        page.wait_for_selector("#playButton")

        page.evaluate("""() => {
          const player=$('#videoPlayer'); player.classList.remove('hidden');
          window.__seekCalls=[]; window.seekVideoTo=(time)=>window.__seekCalls.push(time);
          window.videoClock=()=>42;
          window.__playCalls=0; window.togglePreviewPlayback=()=>window.__playCalls++;
          $('#pauseButton').disabled=false;
        }""")

        # Reproduce the report: click pause, then press the one-second forward key.
        page.locator("#pauseButton").click()
        page.keyboard.press("ArrowRight")
        assert page.evaluate("document.activeElement.id") == "pauseButton"
        assert page.evaluate("window.__seekCalls") == [43], page.evaluate("window.__seekCalls")

        # Playback shortcuts also work while a different toolbar button is focused.
        page.locator("#playButton").focus()
        page.keyboard.press("Space")
        assert page.evaluate("window.__playCalls") == 1

        # Text fields retain normal keyboard input/navigation behavior.
        page.locator("#sourceStart").focus()
        page.keyboard.press("ArrowRight")
        assert page.evaluate("window.__seekCalls") == [43]
        assert not errors, errors
        browser.close()
    print("Global shortcut smoke passed: focused toolbar buttons, seek and editable-field protection")


if __name__ == "__main__":
    main()

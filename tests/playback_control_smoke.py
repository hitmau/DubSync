"""Regression: a pending/rejected HTML video play must leave the watchdog usable."""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(ROOT / '.data' / 'playwright-browsers'))

from playwright.sync_api import sync_playwright
from Dupsync.capture.youtube_source import YoutubeFrameSource, video_canvas_snapshot

with tempfile.TemporaryDirectory() as temp, sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, executable_path=playwright.chromium.executable_path)
    try:
        page = browser.new_page()
        page.set_content('<video></video>')
        source = YoutubeFrameSource(Path(temp), 'test', '', 0, 10,
                                    dict(left=0, top=0, width=1280, height=720),
                                    context=browser, page=page)
        page.evaluate("() => {document.querySelector('video').play=()=>new Promise(()=>{});}")
        started = time.monotonic()
        source._request_play()
        assert time.monotonic() - started < 2, 'Pending play blocked Python'
        assert video_canvas_snapshot(page)['ready'] == 0
        page.evaluate("() => {document.querySelector('video').play=()=>Promise.reject(new Error('fixture'));}")
        source._request_play()
        assert 'fixture' in video_canvas_snapshot(page)['play_error']
        source.stop_requested.set()
        assert source.stop_requested.is_set()
        print('PASS: pending play returns; rejected play is observable; watchdog remains callable.')
    finally:
        browser.close()

"""
Keep the Streamlit Community Cloud app awake.

Community Cloud puts an app to sleep after 12 hours without traffic; the next
visitor then sees a "Zzzz... Yes, get this app back up!" page instead of the
calculator. This opens the app in a real headless browser (so Streamlit counts
it as a genuine visit) and, if it finds the sleep page, clicks the wake button.

Run by .github/workflows/keepalive.yml every 8 hours. Only needed while the
calculator lives on Community Cloud -- delete the workflow after moving to
PythonAnywhere / a VPS (those don't sleep).

Exit code 0 = the calculator was seen loaded; 1 = it never appeared.
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL") or "https://rfp-solutions-calculator.streamlit.app/?src=keepalive"
READY_TEXT = "NYMEX + Basis Calculator"
WAKE_PATTERN = re.compile(r"get this app back up", re.I)
DEADLINE_SECONDS = 180


def frames_with_text(page, text):
    return [f for f in page.frames if f.get_by_text(text).count() > 0]


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL, timeout=60_000)
        start, woke = time.time(), False
        while time.time() - start < DEADLINE_SECONDS:
            if frames_with_text(page, READY_TEXT):
                print(f"OK: calculator loaded after {time.time() - start:.0f}s (woke sleeping app: {woke})")
                browser.close()
                return 0
            for f in page.frames:
                btn = f.get_by_role("button", name=WAKE_PATTERN)
                if btn.count():
                    print("App was asleep -> clicking wake button")
                    btn.first.click()
                    woke = True
                    break
            page.wait_for_timeout(3000)
        print("FAILED: calculator never appeared")
        page.screenshot(path="keepalive_failure.png")
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())

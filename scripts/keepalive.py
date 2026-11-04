"""
Keep the Streamlit Community Cloud app awake.

Community Cloud puts an app to sleep after 12 hours without traffic; the next visitor then sees a
"Zzzz... Yes, get this app back up!" page instead of the calculator. This opens the app in a real
headless browser (so Streamlit counts it as a genuine visit) and, if it finds the sleep page, clicks
the wake button and waits for the app to boot.

Run by .github/workflows/keepalive.yml every 8 hours. Only needed while the calculator lives on
Community Cloud -- delete the workflow after moving to PythonAnywhere / a VPS (those don't sleep).

"The app is up" = ANY of the phrases in READY_TEXTS is visible (the calculator header, or the
members-only lock screen, or the email form). If the deadline passes, the script prints what the
browser actually saw and saves a screenshot, so the failure explains itself.

Exit code 0 = the app was seen loaded; 1 = it never appeared.
Env: APP_URL (override the address), KEEPALIVE_DEADLINE (seconds, default 300).
"""
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL") or "https://rfp-solutions-calculator.streamlit.app/?src=keepalive"
DEADLINE_SECONDS = int(os.environ.get("KEEPALIVE_DEADLINE") or 300)
READY_TEXTS = [
    "NYMEX + Basis Calculator",     # calculator header (always above the lock)
    "Enter your member password",   # lock screen (password mode)
    "Free for RFP list members",    # lock screen card
    "Unlock my price",              # email-form mode
]
SLEEP_PATTERN = re.compile(r"get this app back up|gone to sleep", re.I)
WAKE_BUTTON = re.compile(r"get this app back up", re.I)
RECLICK_AFTER_SECONDS = 90


def which_ready(page):
    for frame in page.frames:
        for text in READY_TEXTS:
            try:
                if frame.get_by_text(text).count() > 0:
                    return text
            except Exception:
                continue
    return None


def try_wake(page):
    """Click the wake button wherever it is (main page or an iframe). Returns True if clicked."""
    for frame in page.frames:
        try:
            button = frame.get_by_text(WAKE_BUTTON)
            if button.count() > 0:
                button.first.click(timeout=5000)
                return True
        except Exception:
            continue
    return False


def page_text(page):
    chunks = []
    for frame in page.frames:
        try:
            chunks.append(frame.locator("body").inner_text(timeout=2000))
        except Exception:
            continue
    return "\n".join(c for c in chunks if c).strip()


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        start = time.time()
        try:
            page.goto(URL, timeout=90_000, wait_until="domcontentloaded")
        except Exception as exc:  # slow/blocked first load: keep going, the loop below decides
            print(f"note: first page load raised: {exc}")

        woke_at = None
        while time.time() - start < DEADLINE_SECONDS:
            seen = which_ready(page)
            if seen:
                woke = "yes" if woke_at else "no"
                print(f"OK: app is up ('{seen}' visible) after {time.time() - start:.0f}s; had to wake it: {woke}")
                browser.close()
                return 0
            if woke_at is None or time.time() - woke_at > RECLICK_AFTER_SECONDS:
                if try_wake(page):
                    print(f"app was asleep -> clicked the wake button at {time.time() - start:.0f}s")
                    woke_at = time.time()
            page.wait_for_timeout(3000)

        print(f"FAILED: the app never appeared within {DEADLINE_SECONDS}s. This is what the browser saw:")
        print("-" * 60)
        print(page_text(page)[:1500] or "(the page had no visible text)")
        print("-" * 60)
        print("Page address:", page.url)
        page.screenshot(path="keepalive_failure.png", full_page=True)
        browser.close()
        return 1


if __name__ == "__main__":
    sys.exit(main())


# """
# Keep the Streamlit Community Cloud app awake.

# Community Cloud puts an app to sleep after 12 hours without traffic; the next
# visitor then sees a "Zzzz... Yes, get this app back up!" page instead of the
# calculator. This opens the app in a real headless browser (so Streamlit counts
# it as a genuine visit) and, if it finds the sleep page, clicks the wake button.

# Run by .github/workflows/keepalive.yml every 8 hours. Only needed while the
# calculator lives on Community Cloud -- delete the workflow after moving to
# PythonAnywhere / a VPS (those don't sleep).

# Exit code 0 = the calculator was seen loaded; 1 = it never appeared.
# """
# import os
# import re
# import sys
# import time

# from playwright.sync_api import sync_playwright

# URL = os.environ.get("APP_URL") or "https://rfp-solutions-calculator.streamlit.app/?src=keepalive"
# READY_TEXT = "NYMEX + Basis Calculator"
# WAKE_PATTERN = re.compile(r"get this app back up", re.I)
# DEADLINE_SECONDS = 180


# def frames_with_text(page, text):
#     return [f for f in page.frames if f.get_by_text(text).count() > 0]


# def main() -> int:
#     with sync_playwright() as p:
#         browser = p.chromium.launch()
#         page = browser.new_page(viewport={"width": 1280, "height": 900})
#         page.goto(URL, timeout=60_000)
#         start, woke = time.time(), False
#         while time.time() - start < DEADLINE_SECONDS:
#             if frames_with_text(page, READY_TEXT):
#                 print(f"OK: calculator loaded after {time.time() - start:.0f}s (woke sleeping app: {woke})")
#                 browser.close()
#                 return 0
#             for f in page.frames:
#                 btn = f.get_by_role("button", name=WAKE_PATTERN)
#                 if btn.count():
#                     print("App was asleep -> clicking wake button")
#                     btn.first.click()
#                     woke = True
#                     break
#             page.wait_for_timeout(3000)
#         print("FAILED: calculator never appeared")
#         page.screenshot(path="keepalive_failure.png")
#         browser.close()
#         return 1


# if __name__ == "__main__":
#     sys.exit(main())

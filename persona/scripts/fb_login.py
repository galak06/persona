"""
One-time Facebook login — saves session state for the scanner.
Opens Playwright Chromium, you log in manually, it detects login and saves.

Usage:
    python scripts/fb_login.py

The script polls for the c_user cookie (set after successful login + 2FA).
It also handles Facebook's "Save login info" dialog automatically.
"""

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from playwright.sync_api import sync_playwright

from lib.bootstrap import init_script
from lib.local_env import get_runtime_headless

settings, _ = init_script(__name__)

STATE_DIR = settings.paths.state_dir
STATE_FILE = STATE_DIR / "facebook_session.json"

MAX_WAIT_SEC = 600  # 10 minutes — plenty of time for login + 2FA


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=get_runtime_headless())
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        print("Opening Facebook...")
        print("Log in manually in the browser (including 2FA if prompted).")
        print("Session will be saved automatically once login completes.")
        print(f"Timeout: {MAX_WAIT_SEC // 60} minutes.\n")

        page.goto("https://www.facebook.com")

        elapsed = 0
        logged_in = False

        while elapsed < MAX_WAIT_SEC:
            time.sleep(3)
            elapsed += 3

            # Dismiss "Save login info" dialog if it appears
            try:
                save_dialog = page.locator(
                    "div[aria-label*='Save your login info'], "
                    "div[role='dialog']:has-text('Save your login info')"
                )
                if save_dialog.count() > 0:
                    # Click "Not Now" or "Continue" — either works
                    for label in ["Not Now", "Continue", "OK"]:
                        btn = page.locator(f"div[role='button']:has-text('{label}')")
                        if btn.count() > 0:
                            btn.first.click(timeout=2000)
                            time.sleep(2)
                            break
            except Exception:
                pass

            # Check for c_user cookie — definitive proof of login
            cookies = context.cookies()
            has_session = any(c["name"] == "c_user" for c in cookies)

            if has_session:
                # c_user cookie exists — user is authenticated.
                # Save session even if URL is still on a verification page,
                # since the cookie is what matters for future requests.
                print(f"  [{elapsed}s] c_user cookie found — login successful.")
                logged_in = True
                break

            # Progress indicator every 15 seconds
            if elapsed % 15 == 0:
                url = page.url.lower()
                if "login" in url or url.endswith("facebook.com/"):
                    status = "waiting for login..."
                elif "two_factor" in url or "two_step" in url:
                    status = "complete 2FA in the browser..."
                elif "checkpoint" in url:
                    status = "complete security check..."
                else:
                    status = f"on {url[:50]}..."
                print(f"  [{elapsed}s] {status}")

        if not logged_in:
            print(f"\nERROR: Login not detected within {MAX_WAIT_SEC // 60} minutes.")
            print("Make sure you completed login AND 2FA in the browser.")
            browser.close()
            return

        # Let cookies settle
        time.sleep(2)

        context.storage_state(path=str(STATE_FILE))
        browser.close()

        print(f"\nSession saved to: {STATE_FILE}")
        print("You can now run: python scripts/fb_scan.py")


if __name__ == "__main__":
    main()

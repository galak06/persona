"""
One-time Instagram login — saves session state for the scanner.
Opens Playwright Chromium, you log in manually, it detects login and saves.

Usage:
    python scripts/ig_login.py

The script polls for login by checking if the URL leaves the login page
and the sessionid cookie exists.
"""

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

STATE_DIR = settings.paths.state_dir
STATE_FILE = STATE_DIR / "instagram_session.json"

MAX_WAIT_SEC = 600  # 10 minutes for login + 2FA


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        print("Opening Instagram...")
        print("Log in manually in the browser (including 2FA if prompted).")
        print("Session will be saved automatically once login completes.")
        print(f"Timeout: {MAX_WAIT_SEC // 60} minutes.\n")

        page.goto("https://www.instagram.com/accounts/login/")

        elapsed = 0
        logged_in = False

        while elapsed < MAX_WAIT_SEC:
            time.sleep(3)
            elapsed += 3

            # Dismiss "Save login info" or "Turn on notifications" dialogs
            try:
                for label in ["Not Now", "Cancel", "Skip"]:
                    btn = page.locator(f"button:has-text('{label}')")
                    if btn.count() > 0:
                        btn.first.click(timeout=2000)
                        time.sleep(1)
                        break
            except Exception:
                pass

            # Check for sessionid cookie — definitive proof of login
            cookies = context.cookies()
            has_session = any(c["name"] == "sessionid" for c in cookies)

            if has_session:
                print(f"  [{elapsed}s] sessionid cookie found — login successful.")
                logged_in = True
                break

            # Progress indicator every 15 seconds
            if elapsed % 15 == 0:
                url = page.url.lower()
                if "login" in url:
                    status = "waiting for login..."
                elif "two_factor" in url or "challenge" in url:
                    status = "complete 2FA/challenge in the browser..."
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
        print("You can now run: python scripts/ig_scan.py")


if __name__ == "__main__":
    main()

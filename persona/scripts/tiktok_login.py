"""
One-time TikTok login — saves session state for the scout and publisher.
Opens Playwright Chromium, you log in manually, it detects login and saves.

Usage:
    python scripts/tiktok_login.py

The script polls for the sessionid cookie on .tiktok.com (set after successful
login). TikTok has aggressive bot detection and CAPTCHA — do NOT attempt to
auto-fill credentials. Complete the login manually in the browser window.
"""

import os
import time

from playwright.sync_api import sync_playwright

from lib.config import settings

STATE_DIR = settings.paths.state_dir
STATE_FILE = settings.paths.tiktok_session

MAX_WAIT_SEC = 300  # 5 minutes — captcha may appear; complete it before timeout
POLL_INTERVAL_SEC = 2


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("A browser window will open. Switch to it and log in.")
    print("Username: persona  |  Password: in your notes")
    print(f"You have {MAX_WAIT_SEC} seconds.")
    print("=" * 60 + "\n")

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
        page.goto("https://www.tiktok.com/login/phone-or-email/email")
        page.bring_to_front()

        elapsed = 0
        logged_in = False

        while elapsed < MAX_WAIT_SEC:
            time.sleep(POLL_INTERVAL_SEC)
            elapsed += POLL_INTERVAL_SEC

            url = page.url.lower()

            # Check URL first — if past the login page, user is logged in
            if "tiktok.com" in url and "login" not in url and "signup" not in url:
                print(f"  [{elapsed}s] Logged in! URL: {page.url[:80]}")
                logged_in = True
                break

            # Also check for post-login cookies
            cookies = context.cookies()
            has_session = any(
                c["name"] in ("sessionid", "sid_tt", "uid_tt", "uid_tt_ss", "sid_guard")
                and "tiktok.com" in c.get("domain", "")
                for c in cookies
            )
            if has_session:
                print(f"  [{elapsed}s] Session cookie found — login successful.")
                logged_in = True
                break

            # Progress indicator every 10 seconds
            if elapsed % 10 == 0:
                short_url = page.url[:70]
                print(f"  [{elapsed}s] {short_url}")

        if not logged_in:
            print(f"\nERROR: Login not detected within {MAX_WAIT_SEC} seconds.")
            cookies = context.cookies()
            tiktok_names = [c["name"] for c in cookies if "tiktok" in c.get("domain", "")]
            print(f"Cookie names found on tiktok.com: {tiktok_names}")
            print("Re-run and complete login before the timeout.")
            browser.close()
            return

        # Let cookies settle before writing
        time.sleep(2)

        context.storage_state(path=str(STATE_FILE))
        browser.close()

        print(f"\nSession saved to: {STATE_FILE}")
        print("You can now run: python scripts/tiktok_scout.py")


if __name__ == "__main__":
    main()

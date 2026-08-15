"""
Unified one-time login script — saves session state for scanners/publishers.
Opens Playwright Chromium, you log in manually, it detects login and saves.

Usage:
    python scripts/login.py fb
    python scripts/login.py ig
    python scripts/login.py tiktok
"""

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.bootstrap import init_script
from lib.local_env import get_runtime_headless


def do_login(
    platform: str,
    max_wait_sec: int,
    url: str,
    check_url: bool,
    cookie_names: list[str],
    state_file: Path,
    headless: bool = False,
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        print(f"Opening {platform.capitalize()}...")
        print("Log in manually in the browser (including 2FA/CAPTCHA if prompted).")
        print("Session will be saved automatically once login completes.")
        print(f"Timeout: {max_wait_sec // 60} minutes.\n")

        page.goto(url)
        page.bring_to_front()

        elapsed = 0
        logged_in = False
        poll_interval = 3 if platform != "tiktok" else 2

        while elapsed < max_wait_sec:
            time.sleep(poll_interval)
            elapsed += poll_interval

            # Dismiss common dialogs
            try:
                if platform == "facebook":
                    save_dialog = page.locator(
                        "div[aria-label*='Save your login info'], "
                        "div[role='dialog']:has-text('Save your login info')"
                    )
                    if save_dialog.count() > 0:
                        for label in ["Not Now", "Continue", "OK"]:
                            btn = page.locator(f"div[role='button']:has-text('{label}')")
                            if btn.count() > 0:
                                btn.first.click(timeout=2000)
                                time.sleep(2)
                                break
                elif platform == "instagram":
                    for label in ["Not Now", "Cancel", "Skip"]:
                        btn = page.locator(f"button:has-text('{label}')")
                        if btn.count() > 0:
                            btn.first.click(timeout=2000)
                            time.sleep(1)
                            break
            except Exception:
                pass

            curr_url = page.url.lower()

            if check_url and "tiktok.com" in curr_url and "login" not in curr_url and "signup" not in curr_url:
                print(f"  [{elapsed}s] Logged in! URL: {page.url[:80]}")
                logged_in = True
                break

            cookies = context.cookies()
            has_session = False
            if platform == "tiktok":
                has_session = any(
                    c["name"] in cookie_names and "tiktok.com" in c.get("domain", "")
                    for c in cookies
                )
            else:
                has_session = any(c["name"] in cookie_names for c in cookies)

            if has_session:
                print(f"  [{elapsed}s] Session cookie found — login successful.")
                logged_in = True
                break

            # Progress indicator
            if (platform != "tiktok" and elapsed % 15 == 0) or (platform == "tiktok" and elapsed % 10 == 0):
                short_url = page.url[:70]
                status = f"on {short_url}..."
                if platform == "facebook":
                    if "login" in curr_url or curr_url.endswith("facebook.com/"):
                        status = "waiting for login..."
                    elif "two_factor" in curr_url or "two_step" in curr_url:
                        status = "complete 2FA in the browser..."
                    elif "checkpoint" in curr_url:
                        status = "complete security check..."
                elif platform == "instagram":
                    if "login" in curr_url:
                        status = "waiting for login..."
                    elif "two_factor" in curr_url or "challenge" in curr_url:
                        status = "complete 2FA/challenge in the browser..."

                print(f"  [{elapsed}s] {status}")

        if not logged_in:
            print(f"\nERROR: Login not detected within {max_wait_sec} seconds.")
            browser.close()
            return

        # Let cookies settle before writing
        time.sleep(2)

        context.storage_state(path=str(state_file))
        browser.close()

        print(f"\nSession saved to: {state_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified login script for social platforms.")
    parser.add_argument("platform", choices=["fb", "ig", "tiktok"], help="The platform to log into.")
    args = parser.parse_args()

    settings, _ = init_script(__name__)

    if args.platform == "fb":
        do_login(
            platform="facebook",
            max_wait_sec=600,
            url="https://www.facebook.com",
            check_url=False,
            cookie_names=["c_user"],
            state_file=settings.paths.facebook_session,
            headless=get_runtime_headless(),
        )
    elif args.platform == "ig":
        do_login(
            platform="instagram",
            max_wait_sec=600,
            url="https://www.instagram.com/accounts/login/",
            check_url=False,
            cookie_names=["sessionid"],
            state_file=settings.paths.instagram_session,
            headless=get_runtime_headless(),
        )
    elif args.platform == "tiktok":
        do_login(
            platform="tiktok",
            max_wait_sec=300,
            url="https://www.tiktok.com/login/phone-or-email/email",
            check_url=True,
            cookie_names=["sessionid", "sid_tt", "uid_tt", "uid_tt_ss", "sid_guard"],
            state_file=settings.paths.tiktok_session,
            headless=False,
        )


if __name__ == "__main__":
    main()

"""Internal helpers for FacebookGroupAdapter.

Pure Playwright-page mutators extracted from facebook.py to keep that file
under 300 lines. No business logic — just DOM interaction.
"""
from __future__ import annotations

import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from lib.engagement.adapters.facebook_dom import OVERLAY_DISMISS_SELECTORS

_PW_ERRORS: tuple[type[BaseException], ...] = (PlaywrightError, PlaywrightTimeoutError)


def extract_post_id(url: str) -> str:
    """Extract post ID from a Facebook URL."""
    for segment in ("posts/", "permalink/"):
        if segment in url:
            part = url.split(segment)[-1]
            return part.split("/")[0].split("?")[0]
    return url.split("/")[-1].split("?")[0]


def dismiss_overlays(page: Page) -> None:
    """Dismiss group welcome popups, login prompts, and other overlays."""
    for sel in OVERLAY_DISMISS_SELECTORS:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click(timeout=1000)
                time.sleep(1)
                return
        except _PW_ERRORS:
            pass
    try:
        page.keyboard.press("Escape")
        time.sleep(1)
    except _PW_ERRORS:
        pass


def click_see_more(page: Page) -> None:
    """Click 'See more' buttons to expand truncated posts."""
    see_more = page.locator("div[role='button']:has-text('See more')")
    count = see_more.count()
    for i in range(min(count, 10)):
        try:
            see_more.nth(i).click(timeout=500)
        except _PW_ERRORS:
            pass
    if count > 0:
        time.sleep(1)


def switch_to_page_profile(page: Page, page_name: str) -> None:
    """Switch the active Facebook profile to the brand Page.

    Two-method sequence mirroring scripts/fb_scan.py lines 405-471:
      1. Direct switch button on the "your pages" listing.
      2. Profile-switcher in the account menu, including "See all profiles".
    Failure is silent — caller continues on personal profile if both fail.
    """
    page.goto(
        "https://www.facebook.com/pages/?category=your_pages",
        wait_until="domcontentloaded",
    )
    time.sleep(3)

    # Method 1: direct switch button on the "your pages" listing
    try:
        switch_btn = page.locator(
            f"a:has-text('{page_name}'), div[role='button']:has-text('Switch')"
        )
        if switch_btn.count() > 0:
            switch_btn.first.click(timeout=5000)
            time.sleep(3)
            return
    except _PW_ERRORS:
        pass

    # Method 2: profile-switcher in account menu
    try:
        menu = page.locator(
            "[aria-label='Your profile'], "
            "[aria-label='Account'], "
            "[aria-label='Account controls and settings']"
        )
        if menu.count() == 0:
            return
        menu.first.click(timeout=3000)
        time.sleep(2)
        profiles = page.locator(
            f"div[role='menuitem']:has-text('{page_name}'), "
            f"span:has-text('{page_name}')"
        )
        if profiles.count() > 0:
            profiles.first.click(timeout=3000)
            time.sleep(3)
            return
        see_all = page.locator("div[role='menuitem']:has-text('See all profiles')")
        if see_all.count() > 0:
            see_all.first.click(timeout=3000)
            time.sleep(2)
            pg = page.locator(f"span:has-text('{page_name}')")
            if pg.count() > 0:
                pg.first.click(timeout=3000)
                time.sleep(3)
    except _PW_ERRORS:
        pass

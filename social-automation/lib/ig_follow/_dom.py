"""Private DOM helpers shared by scout_followers, scout_engagers, follower.

The text patterns here are the only thing standing between a soft
rate-limit (which clears in hours) and an account-level action block
(which clears in days, sometimes weeks). If IG ships new block-dialog
copy, update _ACTION_BLOCKED_TEXTS first thing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .exceptions import IGActionBlockedError, IGUserNotFoundError

if TYPE_CHECKING:
    from playwright.sync_api import Page

ACTION_BLOCKED_TEXTS: tuple[str, ...] = (
    "Action Blocked",
    "Try Again Later",
    "We restrict certain activity",
    "Please wait a few minutes",
)

NOT_FOUND_TEXTS: tuple[str, ...] = (
    "Sorry, this page isn't available",
    "the link you followed may be broken",
)


def detect_action_block(page: Page) -> None:
    """Raise IGActionBlockedError if the page surfaces a block dialog.

    Cheap text-content scan over the current DOM. Should be called
    after every navigation, every modal open, and every follow click.
    Side effect: raises — no return.
    """
    try:
        body_text = page.locator("body").inner_text(timeout=1500)
    except Exception:
        return
    for needle in ACTION_BLOCKED_TEXTS:
        if needle in body_text:
            raise IGActionBlockedError(
                f"IG surfaced block UI: {needle!r}",
                context={"url": page.url},
            )


def detect_user_not_found(page: Page) -> None:
    """Raise IGUserNotFoundError if the profile URL 404'd.

    IG returns HTTP 200 on missing profiles with a "Sorry, this page
    isn't available" body. We detect by text rather than HTTP status.
    """
    try:
        body_text = page.locator("body").inner_text(timeout=1500)
    except Exception:
        return
    for needle in NOT_FOUND_TEXTS:
        if needle in body_text:
            raise IGUserNotFoundError(
                f"Profile not found: {page.url}",
                context={"url": page.url},
            )

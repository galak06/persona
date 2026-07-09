"""Perform the follow click on an Instagram profile.

Single responsibility: open the profile, find the Follow button, click
it, verify the click took effect, and report the result. Does NOT
mutate history — the caller decides whether to persist (which it should
only do on FollowOutcome.FOLLOWED or REQUESTED).

The follow button's CSS classnames rotate, but its text and aria-label
are stable enough to target. The post-click state ("Following" /
"Requested") is what we actually verify success against — never trust
that "the click happened" means "the follow happened."
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ._dom import detect_action_block, detect_user_not_found

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


class FollowOutcome(Enum):
    """Result of a follow attempt.

    FOLLOWED: public account, click succeeded, button now reads "Following".
    REQUESTED: private account, click succeeded, button now reads "Requested".
    ALREADY_FOLLOWING: button already read "Following" / "Requested" on load.
    NO_BUTTON: no Follow button found (could be us, blocked from following).
    """

    FOLLOWED = "followed"
    REQUESTED = "requested"
    ALREADY_FOLLOWING = "already_following"
    NO_BUTTON = "no_button"


@dataclass(frozen=True, slots=True)
class FollowResult:
    """Outcome of a single follow_user call.

    Attributes:
        handle: Lowercased target handle (mirrors caller input).
        outcome: One of the FollowOutcome enum values.
        button_state_before: Text of the action button on page load
            (e.g., "Follow", "Following", "Requested"). For debug logs.
        button_state_after: Text of the action button after the click,
            or None if the click was skipped.
    """

    handle: str
    outcome: FollowOutcome
    button_state_before: str
    button_state_after: str | None


# The aria-label / inner-text values IG uses for the follow button at
# its various states. We probe by inner text rather than classname.
_FOLLOW_LABELS: tuple[str, ...] = ("Follow", "Follow Back")
_ALREADY_LABELS: tuple[str, ...] = ("Following", "Requested")


def _find_action_button(page: Page) -> tuple[Locator | None, str]:
    """Locate the profile-header action button + return (locator, text).

    Returns (None, "") if no candidate button is visible. Probes by
    button role + visible text, the most stable signal we have.
    """
    for label in (*_ALREADY_LABELS, *_FOLLOW_LABELS):
        loc = page.get_by_role("button", name=label).first
        try:
            if loc.is_visible(timeout=1000):
                return loc, label
        except Exception:
            continue
    return None, ""


def follow_user(page: Page, handle: str) -> FollowResult:
    """Open profile and click Follow if the button is in a follow-able state.

    Args:
        page: Logged-in IG Playwright Page.
        handle: Target IG username (no @, any case).

    Returns:
        FollowResult — see FollowOutcome for the possible values. Does
        not raise on already-following or no-button (both are normal
        outcomes the caller should record / skip).

    Raises:
        IGActionBlockedError: A block dialog appeared at any step. Caller
            must abort the batch — do not call follow_user again in
            this run.
        IGUserNotFoundError: Target profile doesn't exist.
    """
    needle = handle.lower().lstrip("@")
    page.goto(f"https://www.instagram.com/{needle}/", wait_until="domcontentloaded")
    detect_user_not_found(page)
    detect_action_block(page)

    button, before = _find_action_button(page)
    if button is None or not before:
        return FollowResult(
            handle=needle,
            outcome=FollowOutcome.NO_BUTTON,
            button_state_before="",
            button_state_after=None,
        )

    if before in _ALREADY_LABELS:
        return FollowResult(
            handle=needle,
            outcome=FollowOutcome.ALREADY_FOLLOWING,
            button_state_before=before,
            button_state_after=before,
        )

    button.click(timeout=4000)
    page.wait_for_timeout(800)
    detect_action_block(page)

    # Re-probe — the same selector now matches the post-click state.
    _, after = _find_action_button(page)

    if after == "Following":
        outcome = FollowOutcome.FOLLOWED
    elif after == "Requested":
        outcome = FollowOutcome.REQUESTED
    else:
        # Click didn't transition the button — treat as no-op rather
        # than a successful follow. Could be a hidden challenge.
        outcome = FollowOutcome.NO_BUTTON

    return FollowResult(
        handle=needle,
        outcome=outcome,
        button_state_before=before,
        button_state_after=after or None,
    )

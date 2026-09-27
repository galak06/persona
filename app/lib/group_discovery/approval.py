"""Candidate display + join execution for fb-group-scout (no interactive prompts)."""

from __future__ import annotations

from collections.abc import Callable

from lib.group_discovery.fb_search import pace_between_joins, try_join
from lib.group_discovery.state import (
    append_to_tracker,
    log_error,
    log_join_request,
)


def print_candidate(i: int, g: dict) -> None:
    mc = f"{g['member_count']:,}" if g.get("member_count") else "unknown"
    comp = g.get("competitor_mentions", 0)
    comp_tag = f" · competitors: {comp}" if comp else ""
    print(f"\n #{i}  {g['name']}  [{g['privacy'].upper()}]{comp_tag}")
    print(
        f"      Members: {mc}  |  Score: {g['score']}  |  "
        f"{g['post_frequency'] or 'activity unknown'}"
    )
    print(f"      URL: {g['url']}")
    print(f'      Found via [{g.get("found_via_channel", "?")}]: "{g["found_via_query"]}"')
    if g.get("competitor_names"):
        print(f"      Mentions: {', '.join(g['competitor_names'])}")
    if g["description"]:
        print(f'      Description: "{g["description"][:120]}..."')


def get_user_approval(
    candidates: list[dict],
    budget: int,
    selection: str = "all",
) -> list[dict]:
    """Select which candidates to join from ``selection`` (NON-interactive).

    The interactive stdin approval prompt was removed — the scout auto-approves
    up to the daily cap. ``selection`` accepts 'all', 'none', or whitespace/
    comma-separated 1-based indices like '1 3 5'. Returns at most ``budget``.
    """
    print("\n" + "=" * 60)
    print(f"Facebook Group Scout — {len(candidates)} candidates (auto-approve)")
    print("=" * 60)
    for i, g in enumerate(candidates, 1):
        print_candidate(i, g)
    print("\n" + "-" * 60)
    response = (selection or "all").strip().lower()
    print(f"[approve {response!r}]")
    if response == "none":
        return []
    if response == "all":
        return candidates[:budget]
    approved: list[dict] = []
    for token in response.replace(",", " ").split():
        try:
            idx = int(token) - 1
            if 0 <= idx < len(candidates):
                approved.append(candidates[idx])
        except ValueError:
            pass
    return approved[:budget]


def _report(on_result: Callable[[dict, str], None] | None, group: dict, result: str) -> None:
    """Hand one join result to the observer; an observer bug never stops joins."""
    if on_result is None:
        return
    try:
        on_result(group, result)
    except Exception as e:
        print(f"     (join observer failed: {type(e).__name__})")


def send_join_requests(
    page,
    approved: list[dict],
    known: set[str],
    on_result: Callable[[dict, str], None] | None = None,
) -> int:
    """Execute the join flow for each approved group. Returns count sent.

    ``on_result`` (optional, read-only observer) receives each group with the
    ``try_join`` result, or ``"error"`` when the attempt raised.
    """
    sent = 0
    print(f"\nSending {len(approved)} join request(s)...\n")
    for i, group in enumerate(approved):
        is_last = i == len(approved) - 1
        print(f"  → {group['name']} [{group['privacy'].upper()}]")
        result = "error"
        try:
            result = try_join(page, group["url"])
            print(f"     Button result: {result}")
            if result.startswith("clicked"):
                status = "join_requested" if group["privacy"] == "private" else "joined"
                log_join_request(group, status)
                append_to_tracker(group, status)
                sent += 1
                known.add(group["url"].lower())
                label = (
                    "✅ Request sent (pending admin approval)"
                    if group["privacy"] == "private"
                    else "✅ Joined immediately"
                )
                print(f"     {label}")
            elif result == "already_joined":
                print("     SKIP: Already a member")
            elif result == "already_pending":
                print("     SKIP: Request already pending")
            else:
                log_error(f"JOIN_BUTTON_NOT_FOUND: {group['url']}")
                print("     WARNING: Join button not found — check manually")
        except Exception as e:
            print(f"     ERROR: {e}")
            log_error(f"JOIN_FAILED: {group['name']} — {e}")
            # A click that landed is still a join even if logging it failed.
            result = result if result.startswith("clicked") else "error"
        _report(on_result, group, result)
        if not is_last:
            delay = pace_between_joins(is_last=False)
            print(f"     Waited {delay:.0f}s before next request")
    return sent

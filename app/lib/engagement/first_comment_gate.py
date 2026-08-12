"""One-time human approval gate for the FIRST comment in each FB group.

The first-ever comment in a group is never posted inline. The scan skips
the comment (the LIKE step is unaffected — the post may still be liked),
flags the group exactly once, and sends one Telegram message asking the
user to approve it via the Groups UI page. Once the group row's
`first_comment_approved_at` is set, comments flow inline forever after.

Iterate-once caveat (intentional): gate-skipped posts are still marked
seen by the pipeline, so posts scanned BEFORE the approval are never
revisited — only posts discovered after approval get comments.

The gate holds no reference to `lib.groups_db`: all collaborators are
injected as plain callables, so tests and callers wire whatever store
they want (production: `groups_db.get_by_url` /
`groups_db.set_first_comment_flagged` and `notifier.send`).
"""

from __future__ import annotations

from collections.abc import Callable

from lib.engagement.adapter import Source
from lib.engagement.collaborators import Log
from lib.engagement.post import Post

SKIP_REASON = "first_comment_approval_pending"


class FirstCommentApprovalGate:
    """`CommentGate` backed by the `fb_groups` approval columns.

    `flag_group` must be write-once: it returns True only when THIS call
    transitioned the group to flagged, which is what keeps the Telegram
    notification to exactly one message per group, ever. An unknown group
    (no row) fails CLOSED: the comment is skipped without flag or notify.
    Under `dry_run` nothing is flagged or notified.
    """

    def __init__(
        self,
        *,
        get_group: Callable[[str], dict[str, object] | None],
        flag_group: Callable[[str, str], bool],
        notify: Callable[[str], object],
        now_iso: Callable[[], str],
        log: Log,
        dry_run: bool = False,
    ) -> None:
        self._get_group = get_group
        self._flag_group = flag_group
        self._notify = notify
        self._now_iso = now_iso
        self._log = log
        self._dry_run = dry_run
        # Per-run memo keyed by group URL: one DB read per group per scan.
        self._cache: dict[str, str | None] = {}

    def check(self, post: Post, source: Source) -> str | None:
        """None when the group's first comment is approved, else a skip reason."""
        del post  # the decision is per-group, not per-post
        url = source.url
        if url not in self._cache:
            self._cache[url] = self._check_group(url)
        return self._cache[url]

    def _check_group(self, group_url: str) -> str | None:
        """Uncached per-group decision; flags + notifies as a side effect."""
        row = self._get_group(group_url)
        if row is None:
            # Fail closed: a group we cannot resolve must not get a first
            # comment silently — but it is not flagged or notified either.
            self._log.warning(
                "first_comment_gate_unknown_group url=%s (comment skipped, not flagged)",
                group_url,
            )
            return SKIP_REASON
        if str(row.get("first_comment_approved_at") or ""):
            return None
        name = str(row.get("group_name") or group_url)
        if self._dry_run:
            self._log.info(
                "first_comment_gate_dry_run group=%s url=%s (would flag for approval)",
                name,
                group_url,
            )
            return SKIP_REASON
        if self._flag_group(group_url, self._now_iso()):
            # This call transitioned the flag -> the one and only notification.
            self._notify(
                f"fb-engager: first comment in group '{name}' needs one-time "
                f"approval — approve it on the Groups page. {group_url}"
            )
            self._log.info("first_comment_flagged group=%s url=%s (Telegram sent)", name, group_url)
        return SKIP_REASON

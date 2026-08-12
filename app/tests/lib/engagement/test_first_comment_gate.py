"""Unit tests for ``FirstCommentApprovalGate`` — plain fakes, no DB, no I/O.

The gate is pure dependency injection: ``get_group`` / ``flag_group`` /
``notify`` / ``now_iso`` are injected callables (production wires
``lib.groups_db`` and ``notifier.send``). These tests lock the contract:

  - approved group -> ``None`` (comment allowed);
  - pending group  -> ``SKIP_REASON`` + flag written + ONE notification;
  - ``flag_group`` returning False (an earlier run already flagged) ->
    NO notification — write-once flagging is what makes notify once-EVER;
  - unknown group  -> fail CLOSED: skip, no flag, no notify, warning log;
  - per-run cache  -> one ``get_group`` read per group per scan;
  - ``dry_run``    -> still skips, but neither flags nor notifies.

The gate's integration (wired inside ``run_fb_engager_scan``) is covered
in ``test_fb_engager_with_fake``; the pipeline-level "like still lands"
behavior in ``test_pipeline_record_publish``.
"""

from __future__ import annotations

from typing import Any

from lib.engagement.first_comment_gate import SKIP_REASON, FirstCommentApprovalGate
from tests.lib.engagement._pipeline_fakes import FakeLog, make_post, make_src

_NOW = "2026-08-11T12:00:00+00:00"


class _Harness:
    """A gate wired to recording fakes over a dict of group rows."""

    def __init__(
        self,
        rows: dict[str, dict[str, Any]],
        *,
        flag_returns: bool = True,
        dry_run: bool = False,
    ) -> None:
        self.rows = rows
        self.get_calls: list[str] = []
        self.flag_calls: list[tuple[str, str]] = []
        self.notifications: list[str] = []
        self.log = FakeLog()
        self._flag_returns = flag_returns
        self.gate = FirstCommentApprovalGate(
            get_group=self._get_group,
            flag_group=self._flag_group,
            notify=self.notifications.append,
            now_iso=lambda: _NOW,
            log=self.log,
            dry_run=dry_run,
        )

    def _get_group(self, url: str) -> dict[str, Any] | None:
        self.get_calls.append(url)
        return self.rows.get(url)

    def _flag_group(self, url: str, at_iso: str) -> bool:
        self.flag_calls.append((url, at_iso))
        return self._flag_returns

    def check(self, source_id: str = "g1", post_id: str = "p1") -> str | None:
        post = make_post(post_id, "food question?", platform="facebook")
        return self.gate.check(post, make_src(source_id))


def _approved(url: str) -> dict[str, Any]:
    return {
        "group_url": url,
        "group_name": "Warm Group",
        "first_comment_approved_at": "2026-07-01T00:00:00+00:00",
    }


def _pending(url: str, name: str = "New Group") -> dict[str, Any]:
    return {"group_url": url, "group_name": name, "first_comment_approved_at": ""}


# make_src("g1") -> url "https://x/g1"
_URL = "https://x/g1"


# --- approved -----------------------------------------------------------------


def test_approved_group_allows_the_comment() -> None:
    h = _Harness({_URL: _approved(_URL)})
    assert h.check() is None
    assert h.flag_calls == []
    assert h.notifications == []


# --- pending: flag + notify exactly once --------------------------------------


def test_pending_group_skips_flags_and_notifies() -> None:
    h = _Harness({_URL: _pending(_URL)})
    assert h.check() == SKIP_REASON
    assert h.flag_calls == [(_URL, _NOW)], "flag written with the gate's clock"
    assert len(h.notifications) == 1
    assert "New Group" in h.notifications[0], "the message names the group"
    assert _URL in h.notifications[0], "...and links it"
    assert any(
        msg.startswith("first_comment_flagged") for _lvl, msg in h.log.calls
    )


def test_already_flagged_group_does_not_notify_again() -> None:
    """`flag_group` returning False = an EARLIER run flagged it; the write-once
    transition is the only thing that may send the Telegram message."""
    h = _Harness({_URL: _pending(_URL)}, flag_returns=False)
    assert h.check() == SKIP_REASON
    assert h.flag_calls != [], "the write-once setter is still attempted"
    assert h.notifications == [], "a second notification was sent"


# --- unknown group fails closed -----------------------------------------------


def test_unknown_group_fails_closed_without_side_effects() -> None:
    h = _Harness({})
    assert h.check() == SKIP_REASON
    assert h.flag_calls == []
    assert h.notifications == []
    warning = next(
        (msg for lvl, msg in h.log.calls if lvl == "warning"), ""
    )
    assert warning.startswith("first_comment_gate_unknown_group")


# --- per-run cache ------------------------------------------------------------


def test_group_is_read_once_per_run_across_posts() -> None:
    """Two posts in the same group -> ONE `get_group` DB read, one flag, one
    notification; a different group gets its own read."""
    other_url = "https://x/g2"
    h = _Harness({_URL: _pending(_URL), other_url: _pending(other_url)})

    assert h.check(post_id="p1") == SKIP_REASON
    assert h.check(post_id="p2") == SKIP_REASON
    assert h.get_calls == [_URL], "the second post must hit the cache"
    assert len(h.flag_calls) == 1
    assert len(h.notifications) == 1

    assert h.check(source_id="g2", post_id="p3") == SKIP_REASON
    assert h.get_calls == [_URL, other_url]


def test_cache_also_holds_the_allow_decision() -> None:
    h = _Harness({_URL: _approved(_URL)})
    assert h.check(post_id="p1") is None
    assert h.check(post_id="p2") is None
    assert h.get_calls == [_URL]


# --- dry run ------------------------------------------------------------------


def test_dry_run_skips_without_flagging_or_notifying() -> None:
    h = _Harness({_URL: _pending(_URL)}, dry_run=True)
    assert h.check() == SKIP_REASON, "a dry run must still preview the skip"
    assert h.flag_calls == []
    assert h.notifications == []
    assert any(
        msg.startswith("first_comment_gate_dry_run") for _lvl, msg in h.log.calls
    )

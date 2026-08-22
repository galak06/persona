"""Reconciliation layer between this app's two dedup stores.

Extracted from `scripts/ig_engager.py` so any scanner can adopt iterate-once.
Both single-pass engagers use it today: `scripts/ig_engager.py` and
`scripts/fb_engager.py`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

from lib import comment_outbox, deduplication
from lib.comment_outbox import Platform as _ClaimPlatform
from lib.dedup_pg import Platform as _PgPlatform
from lib.dedup_pg import completed_entity_ids, record_done
from lib.deduplication import Platform as _DedupPlatform

_log = logging.getLogger(__name__)


class ScanDedup:
    """Dedup collaborator that reconciles this app's THREE dedup stores.

    They are genuinely separate stores, and the single-pass scan needs all of
    them:

      - ``lib.comment_outbox`` -> Postgres ``comment_claims``. A *reservation*
        taken before the comment is submitted, not a mark written after it.
        The only store that can refuse a duplicate comment (see
        ``is_duplicate``).
      - ``lib.deduplication`` -> ``.claude/state/dedup_cache.json``. The only
        store the pipeline's ``is_duplicate`` gate has ever read. Also what
        ``already_commented`` reads, so engagement marks must keep landing here.
      - ``lib.dedup_pg`` -> Postgres ``completed_tasks``. Durable, no file to
        rewrite.

    Iterate-once marks EVERY opened post (~285/run). Writing those to the JSON
    cache would grow it to ~17k entries over the 60-day TTL and rewrite the
    whole file once per post, so seen-marks go to Postgres under
    ``task_type="scan"``. That makes reconciliation mandatory: ``is_duplicate``
    must consult BOTH stores, because marking Postgres while reading only the
    JSON cache would make iterate-once a silent no-op.

    The Postgres side is read ONCE per platform, at the first ``is_duplicate``
    call, into an in-memory set: a scan checks ~570 posts inside a live browser
    session, and one SELECT per post is ~570 sequential round-trips. Newly
    marked ids are added to that set so it stays authoritative for the rest of
    the run.

    Postgres failures are handled ASYMMETRICALLY, and that asymmetry is the
    point:

      - the SEEN-set degrades OPEN. ``_fetch_seen_ids`` returns an empty set on
        failure, so a scan is a live browser session that keeps going and the
        worst outcome is re-visiting posts next run. That stays correct.
      - the CLAIM degrades CLOSED. ``_claims_ok`` latches False on the first
        error and ``claim_comment`` refuses everything afterwards, because the
        worst outcome there is a duplicate comment on a stranger's post —
        user-visible, and only fixable by hand.

    Liking and scanning therefore survive a database outage; commenting does
    not, and must not.
    """

    def __init__(self, worker_label: str, *, log: logging.Logger | None = None) -> None:
        self._worker_label = worker_label
        self._log = log or _log
        self._pg_failed = False
        self._seen_platform: str | None = None
        self._seen_ids: set[str] = set()
        self._claims_ok = True
        self._claim_platform: str | None = None
        self._claim_ids: set[str] = set()

    def is_duplicate(self, platform: str, post_id: str) -> bool:
        """True if we CLAIMED it (outbox), COMMENTED (JSON), or opened it (PG).

        The three checks are asked in that order, and only the first one can
        actually stop a duplicate comment:

          - **outbox claim** — pending OR posted, permanent, no TTL. The only
            store that existed *before* the comment was submitted, hence the
            only one that can answer for a comment whose fate is unknown:
            `lib/ig/comment_post.py:66-85` returns True the moment it clicks
            Post, and any later exception is flattened into a failure by
            `lib/engagement/adapters/instagram.py:237-238`, so "failed" really
            means "unknown". The claim row does not care what the first attempt
            reported — it was written before the click.
          - **JSON cache** — retained belt-and-braces, no longer load-bearing.
            Written only *after* a reported success, and fragile three ways: a
            60-day TTL, no flock and no atomic rename around the whole-file
            rewrite, and one entry per post — so `scripts/ig_like.py` marking
            `like` clobbers an earlier `comment` mark. Any of those silently
            reopens a post the outbox still owns.
          - **Postgres seen-set** — iterate-once. Says "we opened this and
            reached a terminal decision", not "we commented".

        Deliberately `already_commented`, NOT `deduplication.is_duplicate`.
        The presence-only check is True for *any* prior interaction, including
        a like — and single-pass likes before it comments. So a post that was
        liked and then failed to submit its comment would be a duplicate
        forever via its own like mark, defeating the retry that withholding
        the seen-mark exists to provide (see `PostOutcome.is_retryable`). That
        retry is now safe precisely because the claim, not the outcome, is what
        decides whether the retry may comment.

        Facebook gets the same semantics — single-pass `fb_engager.py`
        passes this class too (the retired two-stage `fb_scan.py` used the
        bare `deduplication` module, presence-only).
        """
        if post_id in self._claimed(platform):
            return True
        if deduplication.already_commented(cast(_DedupPlatform, platform), post_id):
            return True
        return post_id in self._prefetched(platform)

    def mark_engaged(
        self,
        platform: str,
        post_id: str,
        action: str,
        group_or_hashtag: str = "",
        status: str = "engaged",
    ) -> None:
        """Record a real action (like/comment) in the JSON cache, as before."""
        deduplication.mark_engaged(
            cast(_DedupPlatform, platform),
            post_id,
            action,
            group_or_hashtag,
            status,
        )

    def mark_seen(self, platform: str, post_id: str) -> None:
        """Iterate-once: record that this post was OPENED (Postgres only)."""
        self._write(
            lambda: record_done(
                "scan",
                cast(_PgPlatform, platform),
                post_id,
                worker_label=self._worker_label,
            )
        )
        # Keep the in-memory set authoritative even if the write failed: we
        # have visited this post, so nothing later in THIS run should re-open it.
        self._prefetched(platform).add(post_id)

    def claims_available(self) -> bool:
        """Can the outbox still record a reservation for us?

        Asked *before* the LLM draft, not after it: a Postgres outage should
        not be paid for with a drafting call whose comment the claim is then
        going to refuse anyway. Latches False for the rest of the run on the
        first outbox error and never recovers — see the class docstring for
        why this one degrades closed.
        """
        return self._claims_ok

    def claim_comment(
        self,
        platform: str,
        post_id: str,
        *,
        post_url: str = "",
        target_name: str = "",
        content: str = "",
    ) -> bool:
        """Reserve this post before commenting. False means: do not comment.

        False covers both refusals — someone already owns the post, and we
        could not reach the store that would have told us. They are the same
        answer at this seam, because in both cases we cannot prove a second
        comment would not land on top of a first.
        """
        if not self._claims_ok:
            return False
        try:
            granted = comment_outbox.claim(
                cast(_ClaimPlatform, platform),
                post_id,
                post_url=post_url,
                target_name=target_name,
                content=content,
                worker_label=self._worker_label,
            )
        except Exception:
            self._note_claim_failure()
            return False
        # Authoritative for the rest of this run whichever way the claim went:
        # granted means we own it, refused means someone else does. Either way
        # nothing later in this run may comment on it again.
        self._claimed(platform).add(post_id)
        return granted

    def settle_comment(self, platform: str, post_id: str) -> bool:
        """Mark our claim as actually posted. False if it could not be recorded.

        A claim we fail to settle stays `pending` and is the reconciler's
        problem, which is the safe direction: an unsettled claim still blocks a
        second comment.
        """
        if not self._claims_ok:
            return False
        try:
            return comment_outbox.settle(cast(_ClaimPlatform, platform), post_id)
        except Exception:
            self._note_claim_failure()
            return False

    def _claimed(self, platform: str) -> set[str]:
        """The set of already-claimed post ids, fetched once per platform."""
        if self._claim_platform != platform:
            self._claim_platform = platform
            self._claim_ids = self._fetch_claimed_ids(platform)
        return self._claim_ids

    def _fetch_claimed_ids(self, platform: str) -> set[str]:
        """One bulk read of the outbox; empty (and latched shut) if it fails."""
        try:
            return comment_outbox.claimed_post_ids(cast(_ClaimPlatform, platform))
        except Exception:
            self._note_claim_failure()
            self._note_pg_failure()
            return set()

    def _note_claim_failure(self) -> None:
        """Latch commenting shut, warning once, and keep scanning/liking."""
        if not self._claims_ok:
            return
        self._claims_ok = False
        self._log.warning(
            "comment_claims_unavailable — refusing every comment for the rest "
            "of this run: a claim we cannot record is a comment we cannot "
            "avoid repeating, and a repeat lands publicly on someone else's "
            "post (scanning and liking continue)",
            exc_info=True,
        )

    def _prefetched(self, platform: str) -> set[str]:
        """The set of already-opened post ids, fetched once per platform."""
        if self._seen_platform != platform:
            self._seen_platform = platform
            self._seen_ids = self._fetch_seen_ids(platform)
        return self._seen_ids

    def _fetch_seen_ids(self, platform: str) -> set[str]:
        """One bulk read of the Postgres seen-set; empty if the DB is down."""
        try:
            return completed_entity_ids("scan", cast(_PgPlatform, platform))
        except Exception:
            self._note_pg_failure()
            return set()

    def _write(self, fn: Callable[[], object]) -> None:
        """Run a Postgres dedup write, degrading if the DB is down."""
        try:
            fn()
        except Exception:
            self._note_pg_failure()

    def _note_pg_failure(self) -> None:
        """Warn once per run that the scan is running without Postgres."""
        if self._pg_failed:
            return
        self._pg_failed = True
        self._log.warning(
            "dedup_pg_unavailable — falling back to the JSON cache for "
            "the rest of this run (posts may be re-visited next run)",
            exc_info=True,
        )

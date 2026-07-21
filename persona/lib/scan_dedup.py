"""Reconciliation layer between this app's two dedup stores.

Extracted from `scripts/ig_scan.py` so any scanner can adopt iterate-once.
Instagram uses it today; `scripts/fb_scan.py` has no iterate-once yet and is
deliberately left unwired — this class is importable so it can be.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

import deduplication
from deduplication import Platform as _DedupPlatform
from lib.dedup_pg import Platform as _PgPlatform
from lib.dedup_pg import completed_entity_ids, record_done

_log = logging.getLogger(__name__)


class ScanDedup:
    """Dedup collaborator that reconciles this app's TWO dedup stores.

    They are genuinely separate stores, and the single-pass scan needs both:

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

    Postgres failures degrade rather than abort — a scan is a live browser
    session, and losing the DB mid-run should cost us re-visits next time, not
    the whole run.
    """

    def __init__(
        self, worker_label: str, *, log: logging.Logger | None = None
    ) -> None:
        self._worker_label = worker_label
        self._log = log or _log
        self._pg_failed = False
        self._seen_platform: str | None = None
        self._seen_ids: set[str] = set()

    def is_duplicate(self, platform: str, post_id: str) -> bool:
        """True if we already COMMENTED (JSON) or already opened it (Postgres).

        Deliberately `already_commented`, NOT `deduplication.is_duplicate`.
        The presence-only check is True for *any* prior interaction, including
        a like — and single-pass likes before it comments. So a post that was
        liked and then failed to submit its comment would be a duplicate
        forever via its own like mark, defeating the retry that withholding
        the seen-mark exists to provide (see `PostOutcome.is_retryable`).

        Asking "did we already comment?" makes the two stores complementary
        rather than overlapping:

          - commented successfully -> JSON says yes, never revisit.
          - opened and terminally rejected (pre-filtered, low score, agent
            declined, liked-but-below-comment-threshold) -> Postgres seen-mark
            says yes, never revisit.
          - comment submission FAILED -> neither fires (the seen-mark was
            withheld), so the next run opens it again. The like is a no-op
            second time round: `adapter.like` returns `skipped:already_liked`.

        Facebook is unaffected — `fb_scan.py` passes the bare `deduplication`
        module and keeps presence-only semantics.
        """
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

"""Post-extraction status, carried from an adapter to the scan report.

An adapter that scrapes post detail out of a page can fail in two ways that
look IDENTICAL from the pipeline's side, and telling them apart is the whole
reason this vocabulary exists:

- the scrape failed (the page evaluate threw, or came back with nothing
  usable) and the adapter substituted an empty caption, or
- the scrape succeeded against a post that genuinely carries no caption.

Both hand the pipeline a `Post` whose `text` is `""`, both score somewhere
around 0.10-0.25 -- below the candidate gate AND below the near-miss logging
floor -- so a total scrape regression and a run of genuinely irrelevant posts
produce the same output: zero candidates and not one log line. `ig-engager`
sat in exactly that state, undiagnosable from its own logs.

The adapter stamps one of these statuses into `Post.platform_extra`; the
pipeline's counters read it back and surface both totals in the run's
`ScanReport` (see `lib/engagement/scan_results.py`) and its summary.

Adapters that do not stamp anything are treated as `EXTRACTION_OK` -- absence
of the key means "this adapter does not report extraction health", not
"extraction failed".
"""

from __future__ import annotations

from lib.engagement.post import Post

#: `Post.platform_extra` key holding one of the statuses below.
EXTRACTION_STATUS_KEY = "extraction_status"

#: The scrape ran and produced a caption.
EXTRACTION_OK = "ok"
#: The scrape threw, or returned nothing usable. The caption is a substitute.
EXTRACTION_FAILED = "failed"
#: The scrape ran fine; this post simply has no caption.
EXTRACTION_EMPTY_CAPTION = "empty_caption"


def extraction_status(post: Post) -> str:
    """Return `post`'s extraction status, defaulting to `EXTRACTION_OK`."""
    raw = post.platform_extra.get(EXTRACTION_STATUS_KEY)
    if not raw:
        return EXTRACTION_OK
    return str(raw)

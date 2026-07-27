"""Repair ragged mid-sentence line breaks in an already-published WP post.

The stored markup of some recipe posts has literal newlines / ``<br />`` tags
inside ``<p>`` and ``<li>`` elements, which render as mid-sentence breaks. This
script reads the post's ``content.raw`` via the REST API, collapses whitespace
and ``<br>`` *only* inside the inner text of ``<p>`` / ``<li>`` elements (inline
tags like ``<strong>``/``<em>``/``<a>`` are preserved), and — only when
``--apply`` is passed — PUTs the cleaned content back. The exact wording is
kept; nothing but whitespace / ``<br>`` is touched.

  python -m scripts.repair_post_linebreaks                 # dry-run (default)
  python -m scripts.repair_post_linebreaks --post-id 3640  # dry-run a post
  python -m scripts.repair_post_linebreaks --apply         # write the fix back

WP / network credentials come from settings.local.json via load_local_env —
never inline secrets.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

# Path bridge: import recipe-publisher packages (publishers / generators) AND
# social-automation packages (lib.*) regardless of cwd. Same pattern as
# workers/__init__.py.
_RP = Path(__file__).resolve().parent.parent
_SA = _RP.parent
for _p in (str(_SA), str(_RP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import requests
from requests.auth import HTTPBasicAuth

from lib.local_env import load_local_env

logger = logging.getLogger("repair_post_linebreaks")

DEFAULT_POST_ID = 3640

# Match a whole <p ...>...</p> or <li ...>...</li> element (non-greedy inner).
# IGNORECASE so <P>/<LI> match; DOTALL so the inner may span newlines.
_ELEMENT_RE = re.compile(
    r"(?P<open><(?P<tag>p|li)\b[^>]*>)(?P<inner>.*?)(?P<close></(?P=tag)\s*>)",
    re.IGNORECASE | re.DOTALL,
)
_BR_RE = re.compile(r"\s*<br\s*/?>\s*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
# Count <br tags that live inside a <p>/<li> element (for dry-run reporting).
_BR_TAG_RE = re.compile(r"<br\b", re.IGNORECASE)


def _normalize_inner(inner: str) -> str:
    """Collapse <br> and whitespace inside one element's inner HTML.

    Inline tags (<strong>, <em>, <a> ...) are left untouched — only <br> and
    runs of whitespace between/around content are collapsed to single spaces.
    """
    collapsed = _BR_RE.sub(" ", inner)
    collapsed = _WS_RE.sub(" ", collapsed)
    return collapsed.strip()


def normalize_content(html: str) -> str:
    """Rewrite only the inner text of <p>/<li> elements; leave all else as-is.

    <script> (JSON-LD), <style> (CSS), <figure>, <img>, and the element tags
    themselves are never modified, and separate elements are never merged
    (each match is rewritten independently).
    """

    def _repl(match: re.Match[str]) -> str:
        cleaned = _normalize_inner(match.group("inner"))
        return f"{match.group('open')}{cleaned}{match.group('close')}"

    return _ELEMENT_RE.sub(_repl, html)


def _count_br_in_elements(html: str) -> int:
    """Count <br tags that appear inside <p>/<li> inner HTML only."""
    total = 0
    for match in _ELEMENT_RE.finditer(html):
        total += len(_BR_TAG_RE.findall(match.group("inner")))
    return total


def _first_changed_paragraph(before: str, after: str) -> tuple[str, str] | None:
    """Return (before_inner, after_inner) of the first <p> that changed."""
    p_re = re.compile(
        r"<p\b[^>]*>(?P<inner>.*?)</p\s*>", re.IGNORECASE | re.DOTALL
    )
    befores = [m.group("inner") for m in p_re.finditer(before)]
    afters = [m.group("inner") for m in p_re.finditer(after)]
    for b, a in zip(befores, afters):
        if b != a:
            return b, a
    return None


def _client_creds() -> tuple[str, HTTPBasicAuth]:
    """Base URL + HTTP Basic app-password auth, mirroring publishers.wordpress.

    Standardized on WP_URL / WP_USER / WP_APP_PASSWORD (populated by
    load_local_env from settings.local.json).
    """
    base = os.environ["WP_URL"].rstrip("/")
    auth = HTTPBasicAuth(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"])
    return base, auth


def _fetch_raw_content(base: str, auth: HTTPBasicAuth, post_id: int) -> str:
    """GET the post in edit context and return content.raw."""
    url = f"{base}/wp-json/wp/v2/posts/{post_id}"
    resp = requests.get(
        url, params={"context": "edit"}, auth=auth, timeout=30
    )
    if resp.status_code != 200:
        raise SystemExit(
            f"GET {url} failed: {resp.status_code} {resp.text[:300]}"
        )
    raw = (resp.json().get("content") or {}).get("raw")
    if not raw:
        raise SystemExit(
            f"Post {post_id} returned no content.raw — is the app password "
            "an admin/editor with edit-context access?"
        )
    return raw


def _put_content(
    base: str, auth: HTTPBasicAuth, post_id: int, cleaned: str
) -> None:
    """PUT the cleaned content back. Only called under --apply."""
    url = f"{base}/wp-json/wp/v2/posts/{post_id}"
    resp = requests.post(  # WP REST accepts POST for updates
        url, json={"content": cleaned}, auth=auth, timeout=30
    )
    if resp.status_code not in (200, 201):
        raise SystemExit(
            f"PUT {url} failed: {resp.status_code} {resp.text[:300]}"
        )
    link = (resp.json() or {}).get("link", "(no link in response)")
    logger.info("Updated post %s -> status %s, link %s",
                post_id, resp.status_code, link)


def _report_dry_run(post_id: int, before: str, after: str) -> None:
    """Print before/after stats and a side-by-side sample. No writes."""
    br_before = _count_br_in_elements(before)
    br_after = _count_br_in_elements(after)
    logger.info("DRY RUN for post %s (no write performed)", post_id)
    logger.info("  <br inside <p>/<li>: before=%d  after=%d",
                br_before, br_after)
    logger.info("  char count: before=%d  after=%d  delta=%d",
                len(before), len(after), len(after) - len(before))
    sample = _first_changed_paragraph(before, after)
    if sample is None:
        logger.info("  No <p> element changed — nothing to repair.")
        return
    b, a = sample
    logger.info("  First changed <p> (truncated ~300 chars):")
    logger.info("    BEFORE: %s", b.strip()[:300])
    logger.info("    AFTER : %s", a.strip()[:300])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--post-id", type=int, default=DEFAULT_POST_ID,
        help=f"WordPress post ID (default {DEFAULT_POST_ID})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview only (default behavior when --apply is absent).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write the cleaned content back via the REST API.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    args = parse_args(argv)

    load_local_env()
    base, auth = _client_creds()

    before = _fetch_raw_content(base, auth, args.post_id)
    after = normalize_content(before)

    # Dry-run is the default: only a *present* --apply triggers a write.
    if not args.apply:
        _report_dry_run(args.post_id, before, after)
        return 0

    if before == after:
        logger.info("Post %s already clean — nothing to write.", args.post_id)
        return 0
    _put_content(base, auth, args.post_id, after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

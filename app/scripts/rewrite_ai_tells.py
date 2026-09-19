"""Audit -- and optionally repair -- published posts that read as AI-written.

Two modes over the same deterministic scan (`lib.crew.ai_tells.scan_body`)
that now gates new drafts in `lib.crew.validate`:

  --audit (default)  Scan every published post, rank by blocking findings and
                     print the report. Read-only: no LLM call, no WP write.
  --rewrite          For the worst posts, replace the headings that name a
                     template slot ("FAQ", "Our Pick") with headings written
                     for that post, and PUT the result back.

`--rewrite` only ever touches heading TEXT, never body prose -- see
`lib.crew.ai_tells.rewrite` for why the measurements do not justify anything
broader. Every write is preceded by a full-content backup on disk, and
WordPress keeps its own revision, so a bad batch is recoverable two ways.

`--rewrite` implies a real WordPress mutation on LIVE published content and
therefore requires `--yes`; without it the script reports what it would do
and exits. Elementor-built posts are skipped outright (writing to their
`post_content` desynchronises the builder).

Credentials (`WP_URL`, `WP_USER`, `WP_APP_PASSWORD`) come from the brand env
exactly as `scripts/backfill_blog_product_blocks.py` loads them -- never
inline literals.

    python scripts/rewrite_ai_tells.py                       # audit all
    python scripts/rewrite_ai_tells.py --rewrite --limit 3   # preview 3
    python scripts/rewrite_ai_tells.py --rewrite --limit 3 --yes
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.crew.ai_tells import AiTellReport, scan_body
from lib.crew.ai_tells.rewrite import (
    HeadingRewrite,
    apply_heading_rewrites,
    propose_heading_rewrites,
    rewrites_as_json,
)
from lib.crew.products.blog_backfill import (
    WpPost,
    fetch_published_posts,
    is_elementor,
    write_backup,
)
from lib.llm_client import TextLLM, get_llm
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger

logger = get_logger(__name__)

# Clearing Elementor's edit-mode flag on a post we rewrite matches what the
# rest of this repo does when writing raw `post_content` (see the memory note
# on FIFU/Elementor meta): a stale flag makes the builder re-render its own
# cached copy and silently discard the edit.
_ELEMENTOR_CLEAR_META = {"_elementor_edit_mode": ""}


@dataclass(frozen=True)
class Scanned:
    """One post with its scan result."""

    post: WpPost
    report: AiTellReport

    @property
    def blocking_count(self) -> int:
        return len(self.report.blocking)

    @property
    def flagged_headings(self) -> list[str]:
        return [f.excerpt for f in self.report.blocking if f.rule == "template_heading"]


def _client() -> httpx.Client:
    base = os.environ["WP_URL"].rstrip("/")
    return httpx.Client(
        base_url=base,
        auth=(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
        timeout=60.0,
        headers={"User-Agent": "ai-tells-rewrite/0.1"},
    )


def scan_all(posts: list[WpPost]) -> list[Scanned]:
    """Scan every post, worst first."""
    scanned = [Scanned(post=p, report=scan_body(p.content)) for p in posts]
    return sorted(scanned, key=lambda s: s.blocking_count, reverse=True)


def print_audit(scanned: list[Scanned]) -> None:
    """The ranked report. Rules are summarised per post; the verbatim excerpt
    is printed so a heading can be found in the editor without a second pass."""
    clean = [s for s in scanned if s.report.passed]
    print(
        f"\nscanned {len(scanned)} published posts -- {len(clean)} clean, "
        f"{len(scanned) - len(clean)} with blocking findings\n"
    )
    for item in scanned:
        if item.report.passed:
            continue
        metrics = item.report.metrics
        print(f"post {item.post.id} | {item.post.title[:58]}")
        print(
            f"    {int(metrics['words'])} words | variation {metrics['variation']:.2f} "
            f"| cliches {metrics['cliche_rate']:.1f}/1k "
            f"| transitions {metrics['transition_rate']:.1f}/1k"
        )
        for finding in item.report.blocking:
            print(f"    [{finding.rule}] {finding.excerpt}")
        print()
    if clean:
        print("clean: " + ", ".join(str(s.post.id) for s in clean))


def rewrite_one(
    item: Scanned, *, client: httpx.Client, backup_dir: Path, llm: TextLLM, apply: bool
) -> str:
    """Repair one post's headings. Returns a status line for the tally."""
    if is_elementor(item.post.meta):
        return f"skip-elementor (post {item.post.id})"
    flagged = item.flagged_headings
    if not flagged:
        return f"skip-no-template-heading (post {item.post.id})"

    rewrites: list[HeadingRewrite] = propose_heading_rewrites(
        title=item.post.title, body_html=item.post.content, flagged=flagged, llm=llm
    )
    if not rewrites:
        return f"skip-no-usable-suggestion (post {item.post.id})"

    print(f"\npost {item.post.id} | {item.post.title[:58]}")
    for rewrite in rewrites:
        print(f"    {rewrite.old!r}  ->  {rewrite.new!r}")

    new_content = apply_heading_rewrites(item.post.content, rewrites)
    if new_content == item.post.content:
        return f"unchanged (post {item.post.id})"
    if not apply:
        return f"would-update (post {item.post.id}, {len(rewrites)} headings)"

    write_backup(backup_dir / f"{item.post.id}-{item.post.campaign_slug}.html", item.post.content)
    write_backup(
        backup_dir / f"{item.post.id}-{item.post.campaign_slug}.headings.json",
        rewrites_as_json(rewrites),
    )
    resp = client.post(
        f"/wp-json/wp/v2/posts/{item.post.id}",
        json={"content": new_content, "meta": _ELEMENTOR_CLEAR_META},
    )
    if resp.status_code >= 400:
        logger.error("ai_tells_rewrite_failed", post_id=item.post.id, status=resp.status_code)
        return f"FAILED {resp.status_code} (post {item.post.id}): {resp.text[:140]}"
    logger.info("ai_tells_rewrite_updated", post_id=item.post.id, headings=len(rewrites))
    return f"updated (post {item.post.id}, {len(rewrites)} headings)"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="propose heading replacements (audit-only without this flag)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="actually PUT the rewrites to WordPress; without it, preview only",
    )
    parser.add_argument("--limit", type=int, default=0, help="only touch the N worst posts")
    parser.add_argument("--post-id", type=int, action="append", help="restrict to these post ids")
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="where to write pre-update content backups (default: state/ai_tells_backups/<ts>)",
    )
    return parser.parse_args(argv)


def _resolve_backup_dir(raw: str | None) -> Path:
    if raw:
        return Path(raw)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path(os.environ.get("BRAND_DIR", ".")) / "state" / "ai_tells_backups" / stamp


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_local_env()
    load_brand_env_into_environ()

    with _client() as client:
        posts = fetch_published_posts(client)
        if args.post_id:
            wanted = set(args.post_id)
            posts = [p for p in posts if p.id in wanted]
        scanned = scan_all(posts)

        if not args.rewrite:
            print_audit(scanned)
            return 0

        targets = [s for s in scanned if s.flagged_headings]
        if args.limit:
            targets = targets[: args.limit]
        if not targets:
            print("nothing to rewrite -- no published post carries a template heading")
            return 0

        if not args.yes:
            print(
                f"PREVIEW ONLY -- {len(targets)} post(s) would be rewritten. "
                "Re-run with --yes to write to WordPress."
            )

        llm = get_llm()
        backup_dir = _resolve_backup_dir(args.backup_dir)
        statuses = [
            rewrite_one(item, client=client, backup_dir=backup_dir, llm=llm, apply=args.yes)
            for item in targets
        ]

    print("\n--- results ---")
    for status in statuses:
        print(f"  {status}")
    if args.yes:
        print(f"\nbackups: {backup_dir}")
    return 1 if any(s.startswith("FAILED") for s in statuses) else 0


if __name__ == "__main__":
    raise SystemExit(main())

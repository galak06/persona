"""CrewAI content strategist + writer -- drafts one full blog post from an
existing `content_ideas` row and writes the assembled HTML to disk for review.

Two sequential CrewAI/DeepSeek calls (strategist -> writer, see
`lib.crew.writer`), then local (no-network) JSON-LD injection and
`[AFFILIATE:key]` resolution against the brand's real product catalog.

Never posts to WordPress, never writes to `content_ideas` -- read-only
against the idea row. Costs real DeepSeek API calls on every run (both
stages), same caveat as `scripts/crewai_content_scout.py`: there is no free
preview mode, `--dry-run` only controls whether `--output` gets written to
disk, not whether the LLM calls happen.

Usage::

    python -m scripts.crewai_content_writer --dry-run
    python -m scripts.crewai_content_writer --idea-id 71f81336-b081-49c7-b911-8b90d8c7e068
    python -m scripts.crewai_content_writer --output /tmp/draft.html
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib.affiliate_resolver import AffiliateResolverError
from lib.crew.writer import (
    assemble_final_html,
    build_content_brief,
    select_idea,
    write_post_from_brief,
)
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger

logger = get_logger(__name__)

_REQUIRED_ENV_VARS = ("DEEPSEEK_API_KEY", "AMAZON_ASSOCIATES_TAG")


def _infer_brand_dir() -> Path:
    """Same convention as `scripts/crewai_content_scout.py::_infer_brand_dir`."""
    brand_dir_env = os.environ.get("BRAND_DIR")
    if brand_dir_env:
        return Path(brand_dir_env)
    brands_root = _ENGINE_ROOT / "brands"
    candidates = sorted(
        d for d in brands_root.glob("*") if d.is_dir() and not d.name.startswith((".", "_"))
    )
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit(
        "BRAND_DIR not set and brands/ doesn't have exactly one brand folder -- pass --brand-dir"
    )


def _check_required_env() -> list[str]:
    """Missing keys among `_REQUIRED_ENV_VARS`, checked after env loading so a
    clear error prints before any CrewAI/network call -- `AMAZON_ASSOCIATES_TAG`
    in particular would otherwise only fail at the very end, after two paid
    LLM calls have already run."""
    return [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name, "").strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brand-dir", type=Path, default=None, help="brand folder (default: $BRAND_DIR)"
    )
    parser.add_argument(
        "--idea-id",
        type=str,
        default=None,
        help="target a specific content_ideas row (default: highest-scored status='publish' row)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="where to write the assembled HTML (default: <brand-dir>/state/crew_drafts/<idea-id>.html)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the brief/draft but do not write --output to disk",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    brand_dir = (args.brand_dir or _infer_brand_dir()).resolve()
    load_brand_env_into_environ(brand_dir)
    load_local_env()

    config_path = brand_dir / "config.json"
    if not config_path.is_file():
        print(f"ERROR: no config.json under {brand_dir}", file=sys.stderr)
        return 1

    missing = _check_required_env()
    if missing:
        print(f"ERROR: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        print(
            "DEEPSEEK_API_KEY / AMAZON_ASSOCIATES_TAG -- see app/CLAUDE.md 'Credentials'.",
            file=sys.stderr,
        )
        return 1

    print(f"brand    : {brand_dir.name}")
    print(f"idea-id  : {args.idea_id or '(auto: highest-scored status=publish)'}")
    print("selecting idea...")

    idea = select_idea(brand_dir, idea_id=args.idea_id)
    if idea is None:
        print(
            "\nno matching content_ideas row found (check status='publish' exists, or "
            "--idea-id is correct)"
        )
        return 0
    print(f"idea     : [{idea.get('id')}] {idea.get('topic')}")

    print("\nrunning strategist crew (real DeepSeek call)...")
    brief = build_content_brief(brand_dir, idea)
    if brief is None:
        print("\nstrategist produced no structured brief (see logs) -- aborting")
        return 1
    print(f"brief    : {brief.model_dump_json(indent=2)}")

    print("\nrunning writer crew (real DeepSeek call)...")
    written_post = write_post_from_brief(brand_dir, brief)
    if written_post is None:
        print("\nwriter produced no structured post (see logs) -- aborting")
        return 1
    print(f"title    : {written_post.title}")
    print(f"words    : {written_post.word_count}")
    print(f"faq items: {len(written_post.faq_pairs)}")
    print(f"affiliate keys used: {written_post.affiliate_keys_used or '(none)'}")

    print("\ninjecting JSON-LD + resolving affiliate placeholders...")
    try:
        final_html = assemble_final_html(brand_dir, written_post)
    except AffiliateResolverError as exc:
        print(f"\nAFFILIATE RESOLUTION FAILED: {exc}", file=sys.stderr)
        print(
            "(brief and draft above are still valid -- only final HTML assembly failed)",
            file=sys.stderr,
        )
        return 1

    output_path = args.output or (brand_dir / "state" / "crew_drafts" / f"{idea.get('id')}.html")
    if args.dry_run:
        print(f"\n[DRY RUN] would write {len(final_html)} chars to {output_path}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_html, encoding="utf-8")
        print(f"\nwrote {len(final_html)} chars to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

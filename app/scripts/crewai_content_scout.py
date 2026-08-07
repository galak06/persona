"""CrewAI content-opportunity scout -- combines `lib.gsc_scout_scoring`'s real
GSC-grounded opportunities with live web-search discovery (Serper, via a
CrewAI agent on DeepSeek) into one deduped, ranked list of content ideas,
written to `content_ideas` (`lib.crew.run_crew_scout`).

Additive alongside `scripts/backfill_gsc_content.py`'s plain GSC scout, not a
replacement -- rows from each are tagged `"source": "gsc_scout"` /
`"source": "crewai_scout"` in `input` so they stay distinguishable. Run
`backfill_gsc_content.py` first (or at least once) so `published_content` has
real rows for this brand before this script's GSC-grounded half has anything
to work with; the web-search half works regardless.

Costs real DeepSeek + Serper API calls on every `--apply` (and every
`--dry-run`, since the agent still has to run to produce candidates to
preview) -- this is not a free, purely-local operation like the plain scout's
dry run.

Usage::

    python -m scripts.crewai_content_scout --dry-run
    python -m scripts.crewai_content_scout --apply
    python -m scripts.crewai_content_scout --apply --top-n 5 --brand-dir ../brands/otherbrand
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib.crew import run_crew_scout
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger

logger = get_logger(__name__)

_REQUIRED_ENV_VARS = ("DEEPSEEK_API_KEY", "SERPER_API_KEY")


def _infer_brand_dir() -> Path:
    """Same convention as `scripts/backfill_gsc_content.py::_infer_brand_dir` --
    reimplemented locally (not imported) so this script has no import-time
    dependency on that one; `$BRAND_DIR`, else the sole folder under `brands/`."""
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
    clear error prints before any CrewAI/network call rather than a buried
    stack trace mid-kickoff."""
    return [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name, "").strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--brand-dir", type=Path, default=None, help="brand folder (default: $BRAND_DIR)"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="print what would be written")
    group.add_argument("--apply", action="store_true", help="write the rows")
    parser.add_argument(
        "--top-n", type=int, default=10, help="max ideas to return/insert (default 10)"
    )
    parser.add_argument(
        "--auto-approve-threshold",
        type=float,
        default=85.0,
        help="priority_score at/above which a newly-inserted idea is auto-approved (default 85.0)",
    )
    parser.add_argument(
        "--disable-auto-approve",
        action="store_true",
        help="never auto-approve, regardless of --auto-approve-threshold",
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
            "SERPER_API_KEY / DEEPSEEK_API_KEY are engine-wide, not per-brand -- "
            "export them (see app/.env, app/CLAUDE.md 'Credentials') before running.",
            file=sys.stderr,
        )
        return 1

    dry_run = not args.apply
    auto_approve_threshold = None if args.disable_auto_approve else args.auto_approve_threshold
    print(f"brand    : {brand_dir.name}")
    print(f"mode     : {'DRY RUN' if dry_run else 'APPLY'}")
    print(f"top-n    : {args.top_n}")
    print(f"auto-approve threshold : {auto_approve_threshold if auto_approve_threshold is not None else 'disabled'}")
    print("running CrewAI scout (real DeepSeek + Serper calls)...")

    results = run_crew_scout(
        brand_dir, top_n=args.top_n, dry_run=dry_run, auto_approve_threshold=auto_approve_threshold
    )

    if not results:
        print(
            "\nno ideas returned (see logs -- either the crew kickoff failed, or it "
            "returned zero candidates)"
        )
        return 0

    inserted = sum(1 for _, idea_id in results if idea_id)
    skipped = len(results) - inserted
    print(f"\n{len(results)} candidate(s) scored, {inserted} inserted, {skipped} skipped/duplicate")
    for idea, idea_id in results:
        status = idea_id if idea_id else ("dry-run" if dry_run else "skipped (duplicate)")
        print(f"  [{status}] ({idea.opportunity_type}, score={idea.priority_score}) {idea.topic}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Instagram engagement pipeline — scan → like → comment in one shot.

Runs the two-stage IG interaction flow:
  1. ig_scan  — walk hashtags, score posts, like qualifying ones, queue
                high-scoring candidates for commenting
  2. ig_comment — drain the ig-comment queue: draft brand-voice comments,
                  post them via Playwright

Each stage honours its own daily rate limits and re-run guards, so running
this script twice in a day is safe — the second run skips stages that already
ran successfully.

Usage:
    python scripts/ig_pipeline.py               # full scan + comment
    python scripts/ig_pipeline.py --scan-only   # like + queue; no comments
    python scripts/ig_pipeline.py --comment-only # comment on already-queued posts
    python scripts/ig_pipeline.py --dry-run     # scan + draft; no likes/posts
    python scripts/ig_pipeline.py --force       # bypass daily re-run guards
    python scripts/ig_pipeline.py --health-check # verify session + config; exit
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PROJECT_ROOT / "scripts"


def _run(script: str, extra_args: list[str]) -> int:
    cmd = [sys.executable, str(SCRIPTS / script)] + extra_args
    print(f"\n{'='*60}", flush=True)
    print(f"▶  {' '.join(cmd)}", flush=True)
    print(f"{'='*60}", flush=True)
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--scan-only",    action="store_true", help="like + queue only; skip comments")
    p.add_argument("--comment-only", action="store_true", help="comment on already-queued posts")
    p.add_argument("--dry-run",      action="store_true", help="no likes or posts; print drafts")
    p.add_argument("--force",        action="store_true", help="bypass daily re-run guards")
    p.add_argument("--health-check", action="store_true", help="verify session + config; exit 0/1")
    args = p.parse_args()

    extra: list[str] = []
    if args.dry_run:
        extra.append("--dry-run")
    if args.force:
        extra.append("--force")

    if args.health_check:
        rc = _run("ig_like.py", ["--health-check"])
        if rc == 0:
            rc = _run("ig_comment.py", ["--health-check"])
        return rc

    rc = 0

    if not args.comment_only:
        print("\n🔍  Stage 1 — Scan hashtags + like + queue", flush=True)
        rc = _run("ig_scan.py", extra)
        if rc != 0:
            print(f"\n❌  Scan stage exited {rc} — skipping comments.", flush=True)
            return rc

    if not args.scan_only:
        print("\n💬  Stage 2 — Draft + post comments", flush=True)
        rc = _run("ig_comment.py", extra)

    return rc


if __name__ == "__main__":
    sys.exit(main())

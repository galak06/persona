"""Auto-trigger FB Groups distribution for the most recent published recipe.

Runs as a separate launchd cron (daily 17:30 IST) — at least 3+ hours after
the publish_prepared.py cron at 14:00 IST per CLAUDE.md publishing-coordination
rules.

Selection rule:
    Pick the most recent campaign in campaigns/published/ where
    metadata.state == "published" AND metadata.fb_groups_distributed_at is
    not yet set. If none → exit 0 cleanly (no Telegram noise).

On a hit:
    1. Subprocess `python scripts/fb_group_post.py --url <wp_url> --title <title>`
       — that script handles Telegram approval per group + Playwright posting.
       Inherits stdout/stderr to the cron log file.
    2. After the subprocess returns 0, mark the campaign as distributed by
       writing `fb_groups_distributed_at` into metadata.json.
    3. If subprocess returns non-zero → leave metadata untouched so the next
       run will retry (idempotent).

Usage:
    python scripts/distribute_fb_groups.py             # default cron behavior
    python scripts/distribute_fb_groups.py --seed <id> # force a specific campaign
    python scripts/distribute_fb_groups.py --dry-run   # show pick, don't subprocess
    python scripts/distribute_fb_groups.py --list      # show distribution state
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.local_env import load_local_env  # noqa: E402

import notifier  # noqa: E402

logger = logging.getLogger("distribute_fb_groups")

CAMPAIGNS_ROOT: Final[Path] = PROJECT_ROOT.parent / "campaigns"
PUBLISHED_ROOT: Final[Path] = CAMPAIGNS_ROOT / "published"


def _load_metadata(folder: Path) -> dict[str, Any]:
    p = folder / "metadata.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def _load_status(folder: Path) -> str:
    p = folder / "status.json"
    if not p.exists():
        return "unknown"
    try:
        return json.loads(p.read_text()).get("state", "unknown")
    except json.JSONDecodeError:
        return "unknown"


def _eligible_campaigns() -> list[Path]:
    """Sort by published_at desc — newest first."""
    if not PUBLISHED_ROOT.exists():
        return []
    rows: list[tuple[str, Path]] = []
    for folder in PUBLISHED_ROOT.iterdir():
        if not folder.is_dir():
            continue
        meta = _load_metadata(folder)
        status = _load_status(folder)
        if status != "published":
            continue
        if meta.get("fb_groups_distributed_at"):
            continue
        if not meta.get("wp_live_url") or not meta.get("title"):
            continue
        rows.append((meta.get("published_at", ""), folder))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [folder for _, folder in rows]


def _mark_distributed(folder: Path) -> None:
    meta_path = folder / "metadata.json"
    meta = _load_metadata(folder)
    meta["fb_groups_distributed_at"] = datetime.now(timezone.utc).isoformat()
    tmp = meta_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    tmp.replace(meta_path)


def _list_state() -> int:
    rows = []
    if PUBLISHED_ROOT.exists():
        for f in sorted(PUBLISHED_ROOT.iterdir()):
            if not f.is_dir():
                continue
            m = _load_metadata(f)
            distributed = m.get("fb_groups_distributed_at")
            rows.append((f.name, m.get("published_at", "?"), distributed))
    if not rows:
        print("(no published campaigns)")
        return 0
    print(f"{'seed_id':<48} {'published_at':<28} {'fb_groups_distributed_at':<28}")
    print("-" * 110)
    for sid, pub, dist in rows:
        print(f"{sid:<48} {pub[:24]:<28} {(dist or '—')[:24]:<28}")
    return 0


def _run(folder: Path, *, dry_run: bool) -> int:
    meta = _load_metadata(folder)
    seed_id = folder.name
    wp_url = meta["wp_live_url"]
    title = meta["title"]

    print(f"distributing: {seed_id}")
    print(f"  wp_url: {wp_url}")
    print(f"  title:  {title}")

    if dry_run:
        print("[dry-run] would invoke fb_group_post.py — exiting without subprocess")
        return 0

    notifier.send(
        f"📣 <b>FB Groups distribution starting</b>\n"
        f"<code>{seed_id}</code>\nWP: {wp_url}",
        silent=True,
    )

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "fb_group_post.py"),
        "--url",
        wp_url,
        "--title",
        title,
    ]
    logger.info("subprocess: %s", " ".join(cmd))
    # Run in foreground so we know the result. fb_group_post.py is interactive
    # (Telegram approvals per group) — its own timeouts handle stuck approvals.
    rc = subprocess.run(cmd, cwd=str(PROJECT_ROOT)).returncode
    if rc == 0:
        _mark_distributed(folder)
        print(f"✅ marked distributed: {seed_id}")
        notifier.send(
            f"✅ <b>FB Groups distribution complete</b>\n<code>{seed_id}</code>",
            silent=True,
        )
        return 0
    print(f"❌ fb_group_post.py exited {rc}; leaving metadata untouched for retry")
    notifier.send(
        f"⚠️ <b>FB Groups distribution failed</b> (exit {rc})\n"
        f"<code>{seed_id}</code> — will retry on next run",
        silent=False,
    )
    return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Auto-distribute the latest published recipe to FB groups")
    parser.add_argument("--seed", help="force a specific seed (skip eligibility filter)")
    parser.add_argument("--dry-run", action="store_true", help="show pick without subprocessing")
    parser.add_argument("--list", action="store_true", help="show distribution state for all published")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_local_env()

    if args.list:
        return _list_state()

    if args.seed:
        folder = PUBLISHED_ROOT / args.seed
        if not folder.exists():
            print(f"❌ no published campaign at {folder}")
            return 1
    else:
        eligible = _eligible_campaigns()
        if not eligible:
            print("(no eligible campaigns — exiting)")
            return 0
        folder = eligible[0]

    return _run(folder, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())

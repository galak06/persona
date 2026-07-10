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
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script

settings, log = init_script(__name__)

import notifier
from lib.local_env import get_brand_campaign, load_local_env

logger = logging.getLogger("distribute_fb_groups")

CAMPAIGNS_ROOT: Final[Path] = settings.paths.campaigns_dir
PUBLISHED_ROOT: Final[Path] = CAMPAIGNS_ROOT / "published"
REEL_FILENAME = "muxed.mp4"
REEL_THUMB_FILENAME = "featured.jpg"
# Mirror fb_group_post.EXIT_NO_POSTS — a clean run that landed zero posts.
# Distinct from rc=0 so we DON'T write reel_distributed_at on empty runs
# (Bug 2 fix: previously the marker fired on exit-0 regardless of posted count,
# permanently shadowing the campaign from the next eligible-pool scan).
EXIT_NO_POSTS = 22


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


def _eligible_campaigns(reel: bool = False) -> list[Path]:
    """Sort by published_at desc — newest first.

    Reel mode tracks `reel_distributed_at` separately so link-card and reel
    cross-posts don't shadow each other.
    """
    if not PUBLISHED_ROOT.exists():
        return []
    marker = "reel_distributed_at" if reel else "fb_groups_distributed_at"
    rows: list[tuple[str, Path]] = []
    for folder in PUBLISHED_ROOT.iterdir():
        if not folder.is_dir():
            continue
        meta = _load_metadata(folder)
        status = _load_status(folder)
        if status != "published":
            continue
        if meta.get(marker):
            continue
        if not meta.get("wp_live_url") or not meta.get("title"):
            continue
        if reel and not (folder / REEL_FILENAME).exists():
            continue
        rows.append((meta.get("published_at", ""), folder))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [folder for _, folder in rows]


def _mark_distributed(folder: Path, *, reel: bool = False) -> None:
    meta_path = folder / "metadata.json"
    meta = _load_metadata(folder)
    field = "reel_distributed_at" if reel else "fb_groups_distributed_at"
    meta[field] = datetime.now(UTC).isoformat()
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


def _detect_reel(folder: Path) -> tuple[Path | None, Path | None]:
    """Return (reel_path, thumbnail_path) if a published reel mp4 exists.

    Reel asset convention: `muxed.mp4` (audio-muxed reel) inside the campaign
    folder, with `featured.jpg` as the cover image. Both come from the
    recipe-publisher pipeline (prepare.py:227 + manifest fb_reel_video_id).
    """
    reel = folder / REEL_FILENAME
    if not reel.exists() or reel.stat().st_size == 0:
        return None, None
    thumb = folder / REEL_THUMB_FILENAME
    return reel, (thumb if thumb.exists() else None)


def _build_cmd(
    folder: Path,
    wp_url: str,
    title: str,
    *,
    reel: bool,
    extra_only: list[str] | None = None,
) -> list[str]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "fb_group_post.py"),
        "--url",
        wp_url,
        "--title",
        title,
    ]
    if reel:
        reel_path, thumb = _detect_reel(folder)
        if reel_path is None:
            raise RuntimeError(f"reel mode requested but {REEL_FILENAME} missing in {folder}")
        cmd += ["--reel-path", str(reel_path)]
        if thumb is not None:
            cmd += ["--reel-thumbnail", str(thumb)]
    if extra_only:
        for g in extra_only:
            cmd += ["--only", g]
    return cmd


def _run(folder: Path, *, dry_run: bool, reel: bool) -> int:
    meta = _load_metadata(folder)
    seed_id = folder.name
    wp_url = meta["wp_live_url"]
    title = meta["title"]

    print(f"distributing: {seed_id}")
    print(f"  wp_url: {wp_url}")
    print(f"  title:  {title}")
    print(f"  mode:   {'reel' if reel else 'link-card'}")

    if reel:
        reel_path, thumb = _detect_reel(folder)
        if reel_path is None:
            print(f"[skip] no {REEL_FILENAME} in {folder} — no reel to cross-post")
            return 0
        campaign = get_brand_campaign()
        cap = (campaign.get("group_crosspost") or {}).get("max_groups_per_post", 10)
        cats = (campaign.get("group_crosspost") or {}).get("reel_target_categories") or []
        print(f"  reel:   {reel_path.name} (thumb={thumb.name if thumb else 'none'})")
        print(f"  cap:    {cap} groups, categories={cats}")

    cmd = _build_cmd(folder, wp_url, title, reel=reel)
    if dry_run:
        print("[dry-run] subprocess command:")
        print("  " + " ".join(cmd))
        if reel:
            cmd_dry = [*cmd, "--dry-run"]
            print("[dry-run] invoking fb_group_post.py --dry-run for caption preview:")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(PROJECT_ROOT)
            return subprocess.run(cmd_dry, cwd=str(PROJECT_ROOT), env=env).returncode
        return 0

    notifier.send(
        f"<b>FB Groups distribution starting</b>\n"
        f"<code>{seed_id}</code>\nmode={('reel' if reel else 'link-card')}\nWP: {wp_url}",
        silent=True,
    )
    logger.info("subprocess: %s", " ".join(cmd))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    # Run in foreground so we know the result. fb_group_post.py is interactive
    # (Telegram approvals per group) — its own timeouts handle stuck approvals.
    rc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), env=env).returncode
    if rc == 0:
        _mark_distributed(folder, reel=reel)
        print(f"marked distributed ({'reel' if reel else 'link'}): {seed_id}")
        notifier.send(
            f"✅ <b>FB Groups distribution complete</b>\n<code>{seed_id}</code>",
            silent=True,
        )
        return 0
    if rc == EXIT_NO_POSTS:
        # Clean run, zero posts landed (rate-cap, all groups skipped, etc.).
        # Leave the marker unset so the next eligible-pool scan retries.
        print(f"fb_group_post.py landed 0 posts (rc={rc}); leaving metadata untouched for retry")
        notifier.send(
            f"ℹ️ <b>FB Groups distribution: 0 posts</b>\n"
            f"<code>{seed_id}</code> — clean run, will retry on next eligible scan",
            silent=True,
        )
        return rc
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
    parser.add_argument(
        "--reel",
        action="store_true",
        help="reel cross-post mode: attach muxed.mp4 instead of link card, filtered to brand.campaign.group_crosspost.reel_target_categories, capped at max_groups_per_post.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_local_env()

    if args.list:
        return _list_state()

    if args.seed:
        folder = PUBLISHED_ROOT / args.seed
        if not folder.exists():
            print(f"no published campaign at {folder}")
            return 1
    else:
        eligible = _eligible_campaigns(reel=args.reel)
        if not eligible:
            print("(no eligible campaigns — exiting)")
            return 0
        folder = eligible[0]

    return _run(folder, dry_run=args.dry_run, reel=args.reel)


if __name__ == "__main__":
    sys.exit(main())

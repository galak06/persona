"""Slow Google Trends refresher — runs daily via launchd.

Why separate: pytrends gets rate-limited at ~3-5 calls/min from any one IP.
The main keyword-research refresher needs to be fast (IG + FB only); this
script is allowed to take 20+ minutes to populate the trends cache without
blocking anyone.

Reads keywords from the latest backups/ideas_*.json, fetches US + CA trends
for each, sleeps 60s between calls. Writes to keyword_research_cache.json
so other skills can read trend data without ever hitting Google.

Runs daily ~4am Israel time via launchd (com.dogfoodandfun.refresh-trends).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

from local_env import load_local_env

load_local_env()

from keyword_research import get_google_trends


def find_latest_ideas_file() -> Path | None:
    """Pick the freshest ideas_YYYY-MM-DD.json from backups/."""
    files = sorted((settings.paths.backups_dir).glob("ideas_*.json"))
    return files[-1] if files else None


def main() -> int:
    ideas_path = find_latest_ideas_file()
    if not ideas_path:
        print("ERROR: no backups/ideas_*.json found")
        return 1

    print(f"Reading keywords from {ideas_path.name}")
    ideas = json.loads(ideas_path.read_text())
    keywords = sorted({i["Target_Keyword"] for i in ideas if i.get("Target_Keyword")})

    print(
        f"Refreshing trends for {len(keywords)} unique keywords × 2 geos = {len(keywords) * 2} calls"
    )
    print(f"Estimated runtime: ~{len(keywords) * 2 * 60 / 60:.0f} minutes (60s between calls)")

    success_count = 0
    rate_limit_count = 0

    for i, kw in enumerate(keywords):
        for geo in ("US", "CA"):
            print(f"\n[{i + 1}/{len(keywords)}] {geo}: {kw}")
            t0 = time.time()
            data = get_google_trends(kw, geo=geo)
            elapsed = time.time() - t0
            trend = data.get("trend", "?")
            interest = data.get("interest", 0)

            if trend in ("rising", "stable", "declining"):
                print(f"    ✓ interest={interest} trend={trend} ({elapsed:.1f}s)")
                success_count += 1
            elif trend == "error" and "429" in data.get("error", ""):
                print(f"    ✗ rate-limited ({elapsed:.1f}s) — waiting longer")
                rate_limit_count += 1
                time.sleep(120)  # extra cool-down on 429
            else:
                print(f"    ? trend={trend} ({elapsed:.1f}s) — {data.get('error', '')[:60]}")

            # Standard 60s gap between calls (skip on the very last call)
            if not (i == len(keywords) - 1 and geo == "CA"):
                time.sleep(60)

    print(f"\nDone — {success_count} succeeded, {rate_limit_count} rate-limited")
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

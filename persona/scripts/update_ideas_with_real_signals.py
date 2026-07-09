"""Re-score the pending content-ideator batch using real-world signals.

Inputs:
- backups/ideas_YYYY-MM-DD.json — prior cluster-aware scoring (auto-detected latest)
- .claude/state/tier1_research_*.json — web-research deltas (auto-detected latest)
- .claude/state/tier1_real_data_*.json — IG hashtag engagement (auto-detected latest)
- .claude/state/keyword_research_cache.json — Google Trends cache

Output:
- backups/ideas_YYYY-MM-DD-v3.json — re-scored batch + 5 new opportunities
- .claude/state/ideation_history.json — updated with this run
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

# Script is in social-automation/scripts/
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "social-automation" / "lib"))
from lib.bootstrap import init_script
settings, log = init_script(__name__)

def find_latest(pattern: str, search_dir: Path) -> Path | None:
    """Find the most recent file matching a glob pattern."""
    files = list(search_dir.glob(pattern))
    if not files:
        return None
    # Sort by name (assuming YYYY-MM-DD format in names) then modified time
    files.sort(key=lambda x: (x.name, x.stat().st_mtime), reverse=True)
    return files[0]

# Auto-detect inputs
PRIOR_IDEAS = find_latest("ideas_202[4-9]-*.json", settings.paths.backups_dir)
# Exclude -v3.json and -approved.json from being picked as the "prior" baseline
if PRIOR_IDEAS and ("-v3" in PRIOR_IDEAS.name or "-approved" in PRIOR_IDEAS.name):
    # Try to find one without the suffixes
    others = [f for f in settings.paths.backups_dir.glob("ideas_202[4-9]-*.json") 
              if "-v3" not in f.name and "-approved" not in f.name]
    if others:
        others.sort(key=lambda x: (x.name, x.stat().st_mtime), reverse=True)
        PRIOR_IDEAS = others[0]

TIER1_DELTAS = find_latest("tier1_research_*.json", ROOT / "social-automation" / ".claude" / "state")
IG_SIGNALS = find_latest("tier1_real_data_*.json", ROOT / "social-automation" / ".claude" / "state")
TRENDS_CACHE = ROOT / "social-automation" / ".claude" / "state" / "keyword_research_cache.json"
HISTORY = settings.paths.brand_dir / "state" / "ideation_history.json"

# Output path uses the same date as PRIOR_IDEAS if found
if PRIOR_IDEAS:
    date_match = re.search(r'(\d{4}-\d{2}-\d{2})', PRIOR_IDEAS.name)
    batch_date = date_match.group(1) if date_match else datetime.now(UTC).strftime("%Y-%m-%d")
    OUT_PATH = settings.paths.backups_dir / f"ideas_{batch_date}-v3.json"
else:
    OUT_PATH = settings.paths.backups_dir / f"ideas_{datetime.now(UTC).strftime('%Y-%m-%d')}-v3.json"

def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return default

def trend_signal_for(keyword: str, cache: dict) -> dict:
    """Read US + CA trend cache. Combine into a single delta."""
    us = cache.get(f"trends:{keyword.lower().strip()}_us", {}).get("data", {})
    ca = cache.get(f"trends:{keyword.lower().strip()}_ca", {}).get("data", {})

    trends = [us.get("trend"), ca.get("trend")]
    if "rising" in trends:
        return {"delta": 1, "reason": "trends rising in US or CA", "us": us, "ca": ca}
    if "declining" in trends and "rising" not in trends:
        return {"delta": -1, "reason": "trends declining", "us": us, "ca": ca}
    if any(t in ("stable",) for t in trends):
        return {"delta": 0, "reason": "trends stable", "us": us, "ca": ca}
    return {"delta": 0, "reason": "no trends data cached yet", "us": us, "ca": ca}

def ig_signal_for(idea_id: int, ig_data: dict) -> dict:
    """Pull this idea's IG engagement from tier1_real_data."""
    for entry in ig_data.get("ideas", []):
        if entry.get("id") == idea_id:
            ig = entry.get("instagram", {})
            sig = ig.get("engagement_signal", "")
            if sig == "high":
                return {"delta": 1, "reason": f"IG #{entry['ig_hashtag_used']} = {ig.get('avg_likes')} likes (high)"}
            if sig == "low":
                return {"delta": -1, "reason": f"IG #{entry['ig_hashtag_used']} = {ig.get('avg_likes')} likes (low)"}
            return {"delta": 0, "reason": f"IG signal: {sig or 'medium/none'}"}
    return {"delta": 0, "reason": "no IG data"}

def tier1_delta_for(idea_id: int, tier1: dict) -> dict:
    """Already-computed web-research delta from tier 1 verification run."""
    for entry in tier1.get("ideas", []):
        if entry.get("id") == idea_id:
            return {"delta": entry.get("delta", 0), "reason": entry.get("delta_reason", "")}
    return {"delta": 0, "reason": "no tier1 data"}

def main() -> int:
    print(f"--- Re-scoring Content Ideas ---")
    print(f"Baseline: {PRIOR_IDEAS.name if PRIOR_IDEAS else 'NONE'}")
    print(f"Research: {TIER1_DELTAS.name if TIER1_DELTAS else 'NONE'}")
    print(f"Signals:  {IG_SIGNALS.name if IG_SIGNALS else 'NONE'}")
    
    if not PRIOR_IDEAS or not PRIOR_IDEAS.exists():
        print(f"ERROR: No prior ideas batch found in {settings.paths.backups_dir}")
        return 1

    prior_raw = load_json(PRIOR_IDEAS, [])
    if isinstance(prior_raw, dict):
        prior = prior_raw.get("ideas", [])
        if not prior and prior_raw:
            prior = [prior_raw]
    else:
        prior = prior_raw

    tier1 = load_json(TIER1_DELTAS, {}) if TIER1_DELTAS else {}
    ig = load_json(IG_SIGNALS, {}) if IG_SIGNALS else {}
    trends_cache = load_json(TRENDS_CACHE, {})

    rescored = []
    for idea in prior:
        idea_id = idea.get("id", 0)
        prior_score = idea.get("score", 0)

        t1 = tier1_delta_for(idea_id, tier1)
        ig_sig = ig_signal_for(idea_id, ig)
        tr = trend_signal_for(idea.get("Target_Keyword", ""), trends_cache)

        new_score = prior_score + t1["delta"] + ig_sig["delta"] + tr["delta"]

        rescored.append({
            **idea,
            "score": new_score,
            "score_v3_breakdown": {
                "prior_score": prior_score,
                "tier1_research_delta": t1,
                "ig_engagement_delta": ig_sig,
                "trends_delta": tr,
                "new_score": new_score,
            },
        })

    rescored.sort(key=lambda x: x["score"], reverse=True)
    new_opps = tier1.get("new_opportunities", [])

    output = {
        "_schema": "ideas_with_real_signals_v3",
        "_generated_at": datetime.now(UTC).isoformat(),
        "_inputs": {
            "prior_ideas": PRIOR_IDEAS.name,
            "tier1_deltas": TIER1_DELTAS.name if TIER1_DELTAS else None,
            "ig_signals": IG_SIGNALS.name if IG_SIGNALS else None,
            "trends_cache": TRENDS_CACHE.name if TRENDS_CACHE.exists() else None,
        },
        "ideas": rescored,
        "new_opportunities": new_opps,
    }

    OUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"\nWrote → {OUT_PATH}")

    # Update history
    hist = load_json(HISTORY, {"schema_version": 1, "runs": []})
    hist["last_run"] = output["_generated_at"]
    hist["runs"].append({
        "run_id": f"{OUT_PATH.stem}-rescored",
        "timestamp": output["_generated_at"],
        "trigger": "automated re-scoring with live signals",
        "ideas_generated": len(rescored),
        "output_file": str(OUT_PATH.relative_to(ROOT)),
        "top_idea": rescored[0]["Topic"] if rescored else None
    })
    HISTORY.write_text(json.dumps(hist, indent=2))
    
    print("\n=== Final Ranking ===")
    for i in rescored[:5]:
        print(f"  [{i.get('score', 0):>2}] {i['Topic'][:60]}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

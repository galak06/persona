"""Worker: dogfood-content-ideator — generate Google-Search-grounded content ideas.

Calls Gemini (with google_search tool) for each category and stores ideas in
the ``content_ideas`` Supabase table. Deduplicates against existing topics.

Usage: python -m workers.worker_content_ideator [--apply] [--category health]
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Final

import httpx

# ---- path bootstrap so this runs both as a module and as a script ----
_rp_root = Path(__file__).resolve().parent.parent
_sa_root = _rp_root.parent
for _p in (_rp_root, _sa_root):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from lib import ideas_db  # noqa: E402  (after path fixup)
from lib.local_env import load_local_env  # noqa: E402

load_local_env()

_log = logging.getLogger("workers.content_ideator")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATEGORIES: Final[list[str]] = [
    "recipes",
    "health",
    "training",
    "nutrition",
    "gear-toys",
    "grooming",
    "breed-specific",
    "safety",
]

_GEMINI_MODEL: Final[str] = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_FUZZY_THRESHOLD: Final[float] = 0.85
_IDEAS_PER_CATEGORY: Final[int] = 5

_PROMPT_TEMPLATE: Final[str] = """\
You are a content strategist for your-brand.com, a US/Canada dog food and lifestyle blog featuring Nalla, a Golden Retriever.

Use Google Search to find current trending topics, seasonal signals, and popular questions (PAA) that dog owners in the US and Canada are searching for RIGHT NOW in the category: "{category}"

Generate exactly {n} content marketing ideas grounded in real search trends. Each idea should reflect something people are actively searching for or discussing.

Rules:
- Each idea must be specific and timely (backed by search signal, not generic)
- Target: dog owners in the US and Canada
- Nalla context: how Nalla relates to this topic (keep grounded — real Golden Retriever experiences)
- Return ONLY a JSON array, no markdown, no explanation

JSON format:
[
  {{
    "topic": "Short blog/social post title (max 70 chars)",
    "target_keyword": "primary SEO keyword people are searching",
    "nalla_context": "how Nalla relates to this topic (1 sentence, or null)",
    "post_goal": "educate | inspire | entertain | convert",
    "search_signal": "brief note on what trend/search signal this is based on"
  }}
]
"""

# ---------------------------------------------------------------------------
# Gemini helpers
# ---------------------------------------------------------------------------


def _call_gemini(category: str) -> list[dict[str, Any]]:
    """Call Gemini for one category. Returns parsed list of idea dicts."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in env")

    prompt = _PROMPT_TEMPLATE.format(n=_IDEAS_PER_CATEGORY, category=category)
    payload: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.8, "maxOutputTokens": 8192},
    }
    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    _log.info("gemini call: model=%s category=%s", _GEMINI_MODEL, category)

    r = httpx.post(url, params={"key": api_key}, json=payload, timeout=120.0)
    if r.status_code >= 400:
        raise RuntimeError(f"gemini HTTP {r.status_code}: {r.text[:400]}")

    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini returned no candidates: {data!r}")

    parts = cands[0].get("content", {}).get("parts", [])
    text_parts = [p["text"] for p in parts if isinstance(p, dict) and p.get("text")]
    if not text_parts:
        raise RuntimeError(f"gemini returned empty text; parts={parts!r}")
    # google_search often returns 2 parts (draft + grounded); try each independently
    for tp in text_parts:
        try:
            return _extract_ideas(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", tp))
        except (ValueError, json.JSONDecodeError):
            continue
    raise RuntimeError("no parseable JSON array found in response parts")


def _extract_ideas(text: str) -> list[dict[str, Any]]:
    """Strip code fences and extract the JSON array from Gemini's text reply."""
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"no JSON array in response: {text[:300]!r}")
    return json.loads(cleaned[start : end + 1])


def _is_duplicate(topic: str, existing: set[str]) -> bool:
    """Return True if topic is an exact or fuzzy match against existing topics."""
    lower = topic.lower()
    if lower in existing:
        return True
    return any(
        difflib.SequenceMatcher(None, lower, t).ratio() > _FUZZY_THRESHOLD
        for t in existing
    )


# ---------------------------------------------------------------------------
# Per-category processing
# ---------------------------------------------------------------------------


def _run_category(
    category: str,
    existing: set[str],
    *,
    brand_id: str | None,
    brand_name: str | None,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Run ideation for one category → (generated, inserted, skipped)."""
    try:
        raw_ideas = _call_gemini(category)
    except Exception as exc:
        _log.warning("category=%s gemini call failed: %s", category, exc)
        return 0, 0, 0

    generated = len(raw_ideas)
    inserted = 0
    skipped = 0

    for raw in raw_ideas:
        topic: str = str(raw.get("topic", "")).strip()
        if not topic:
            _log.warning("category=%s skipping idea with empty topic: %r", category, raw)
            skipped += 1
            continue

        if _is_duplicate(topic, existing):
            _log.info("category=%s skip duplicate: %s", category, topic)
            skipped += 1
            continue

        if dry_run:
            _log.info("category=%s [dry-run] would insert: %s", category, topic)
        else:
            idea: dict[str, Any] = {
                "category": category,
                "topic": topic,
                "target_keyword": raw.get("target_keyword"),
                "nalla_context": raw.get("nalla_context"),
                "post_goal": raw.get("post_goal"),
                "input": raw.get("search_signal"),
            }
            result_id = ideas_db.insert_idea(idea, brand_id=brand_id, brand_name=brand_name)
            if result_id is None:
                _log.warning(
                    "category=%s insert failed for topic: %s", category, topic
                )
                skipped += 1
                continue

        existing.add(topic.lower())
        inserted += 1

    _log.info(
        "category=%s generated=%d inserted=%d skipped=%d (duplicate)",
        category,
        generated,
        inserted,
        skipped,
    )
    return generated, inserted, skipped


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _resolve_brand_id() -> str:
    brand_dir = os.environ.get("BRAND_DIR")
    return Path(brand_dir).name if brand_dir else "persona"


def _resolve_brand_name() -> str | None:
    p = Path(__file__).resolve().parents[2] / ".claude" / "settings.local.json"
    env = json.loads(p.read_text(encoding="utf-8")).get("env", {}) if p.exists() else {}
    return env.get("BRAND_NAME") or env.get("SITE_NAME") or os.environ.get("BRAND_NAME") or os.environ.get("SITE_NAME")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate content marketing ideas via Gemini and store in Supabase"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log ideas without writing to DB (default when --apply is absent)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write ideas to content_ideas table",
    )
    parser.add_argument(
        "--category",
        choices=CATEGORIES,
        default=None,
        help="Run only this category (default: all 8 categories)",
    )
    args = parser.parse_args(argv)

    # dry-run is the safe default; --apply is required to write
    dry_run: bool = not args.apply

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    bid = _resolve_brand_id()
    brand_name = _resolve_brand_name()
    categories = [args.category] if args.category else CATEGORIES

    _log.info(
        "content-ideator start brand=%s categories=%d dry_run=%s",
        bid,
        len(categories),
        dry_run,
    )

    existing: set[str] = ideas_db.existing_topics(brand_id=bid)
    _log.info("loaded %d existing topics for dedup", len(existing))

    total_generated = 0
    total_inserted = 0
    total_skipped = 0

    for cat in categories:
        g, i, s = _run_category(cat, existing, brand_id=bid, brand_name=brand_name, dry_run=dry_run)
        total_generated += g
        total_inserted += i
        total_skipped += s

    _log.info(
        "content-ideator done — total generated=%d inserted=%d skipped=%d",
        total_generated,
        total_inserted,
        total_skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

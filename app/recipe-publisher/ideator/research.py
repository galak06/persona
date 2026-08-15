"""Research-driven candidate generation via Gemini + native google_search tool.

Single Gemini API call:
    - System prompt: the content-ideator skill's ``## LLM Prompt: research``
      section (brand persona + dog-recipe ideation rules), loaded via
      ``lib.skill_loader`` so the copy lives in SKILL.md, not in Python
    - User prompt: today's date, exclusion list, count target
    - Tool: google_search (Gemini grounds against live web results)
    - Output: JSON array of Candidate dicts, parsed from text response

We don't combine google_search with structured-output schema enforcement —
mixing both is brittle in current Gemini. Instead we ground via search, then
hard-parse JSON from the model's text reply with a strict validator pass.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import httpx

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent.parent

from lib.skill_loader import load_skill_prompt

logger = logging.getLogger(__name__)

_GEMINI_MODEL: Final[str] = os.getenv("GEMINI_RESEARCH_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


@dataclass(frozen=True)
class Candidate:
    title: str
    category: str
    why_now: str
    evidence: str
    seasonal_relevance: int  # 1-10
    search_demand_estimate: str  # "low" | "medium" | "high"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "category": self.category,
            "why_now": self.why_now,
            "evidence": self.evidence,
            "seasonal_relevance": self.seasonal_relevance,
            "search_demand_estimate": self.search_demand_estimate,
        }


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """Rendered content-ideator ``## LLM Prompt: research`` section, cached.

    Raises ``SkillPromptError`` (from ``lib.skill_loader``) on a missing or
    broken skill file — a deployment defect must abort loudly, never degrade
    into a promptless call.
    """
    return load_skill_prompt("content-ideator", section="research")


def _user_prompt(existing_titles: list[str], n: int) -> str:
    today = date.today().isoformat()
    excl = "\n".join(f"- {t}" for t in existing_titles) or "(none)"
    return f"""\
Today: {today}

EXCLUDED titles (already published, queued, or previously proposed):
{excl}

Use google_search to research current trends, then return EXACTLY {n} recipe
candidates as a JSON array. Output ONLY the JSON array, no preamble, no code
fences, no explanation. Schema per item:

{{
  "title": "Specific recipe title for blog post",
  "category": "one of the 8 allowed categories",
  "why_now": "specific demand signal — what trend / season / gap drove this",
  "evidence": "URL or named source",
  "seasonal_relevance": 7,
  "search_demand_estimate": "low" | "medium" | "high"
}}
"""


_JSON_ARRAY_RE: Final[re.Pattern[str]] = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)

# Belt-and-braces: even with the prompt saying "no URLs", Gemini sometimes
# leaks the Vertex grounding redirect into evidence. Strip them so Telegram
# never shows a clickable link that 404s.
_URL_RE: Final[re.Pattern[str]] = re.compile(r"https?://\S+")


def _strip_urls(text: str) -> str:
    cleaned = _URL_RE.sub("", text)
    # collapse double-whitespace + leading/trailing punctuation left behind
    return re.sub(r"\s{2,}", " ", cleaned).strip(" .,;:")


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    # Gemini sometimes wraps in ```json ... ```; strip code fences first.
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE)
    match = _JSON_ARRAY_RE.search(cleaned)
    if not match:
        raise ValueError(f"no JSON array found in response: {text[:300]!r}")
    return json.loads(match.group(0))


def _to_candidate(raw: dict[str, Any]) -> Candidate | None:
    try:
        return Candidate(
            title=str(raw["title"]).strip(),
            category=str(raw["category"]).strip(),
            why_now=_strip_urls(str(raw["why_now"])),
            evidence=_strip_urls(str(raw.get("evidence", ""))),
            seasonal_relevance=int(raw.get("seasonal_relevance", 5)),
            search_demand_estimate=str(raw.get("search_demand_estimate", "medium")).strip().lower(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("dropping malformed candidate %r: %s", raw, exc)
        return None


def research_candidates(existing_titles: list[str], *, n: int = 6) -> list[Candidate]:
    """Call Gemini with google_search tool. Returns up to n validated candidates."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in env")

    payload = {
        "systemInstruction": {"parts": [{"text": _system_prompt()}]},
        "contents": [{"role": "user", "parts": [{"text": _user_prompt(existing_titles, n)}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 4096},
    }

    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    logger.info(
        "gemini research call model=%s n=%d excluded=%d", _GEMINI_MODEL, n, len(existing_titles)
    )
    r = httpx.post(url, params={"key": api_key}, json=payload, timeout=180.0)
    if r.status_code >= 400:
        raise RuntimeError(f"gemini research HTTP {r.status_code}: {r.text[:500]}")

    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini returned no candidates: {data!r}")

    # Concatenate all text parts (sometimes split across multiple)
    parts = cands[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        raise RuntimeError(f"gemini returned empty text; parts={parts!r}")

    raw_candidates = _extract_json_array(text)
    validated = [c for c in (_to_candidate(r) for r in raw_candidates) if c is not None]
    logger.info("research returned %d/%d valid candidates", len(validated), len(raw_candidates))
    return validated

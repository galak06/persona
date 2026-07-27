"""LLM helpers shared by content workers — text generation and caption drafting."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from generators.recipe import Recipe

logger = logging.getLogger("workers.llm")

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
_GEMINI_MODEL = os.getenv("GEMINI_LYRICS_MODEL", "gemini-2.5-flash")
_ANTHROPIC_MODEL = os.getenv("RECIPE_MODEL", "claude-sonnet-4-6")


def _call_gemini_text(prompt: str) -> str:
    import httpx

    key = os.environ.get("GEMINI_API_KEY", "")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 1024,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    r = httpx.post(
        _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL),
        params={"key": key},
        json=payload,
        timeout=60.0,
    )
    r.raise_for_status()
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise RuntimeError(f"gemini returned no candidates: {data!r}")
    parts = cands[0].get("content", {}).get("parts", [])
    return "\n".join(p.get("text", "") for p in parts if "text" in p).strip()


def _call_anthropic_text(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=_ANTHROPIC_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()  # type: ignore[attr-defined]


def llm_text(prompt: str) -> str:
    """Call Gemini (preferred) or Anthropic. Raises if neither key is set."""
    if os.environ.get("GEMINI_API_KEY"):
        return _call_gemini_text(prompt)
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _call_anthropic_text(prompt)
    raise RuntimeError("no LLM key available (GEMINI_API_KEY / ANTHROPIC_API_KEY)")


def enforce_hashtag_limit(caption: str, max_tags: int = 8) -> str:
    """Trim IG caption to at most `max_tags` hashtags (brand rule: 6-8)."""
    import re

    all_tags = re.findall(r"#\w+", caption)
    if len(all_tags) <= max_tags:
        return caption
    remove = set(all_tags[max_tags:])
    trimmed = re.sub(
        r"#\w+",
        lambda m: m.group() if m.group() not in remove else "",
        caption,
    )
    trimmed = re.sub(r"\n[ \t]*\n[ \t]*\n", "\n\n", trimmed)
    return trimmed.strip()


_GENERIC_TRIGGERS = {"RECIPE", "FOOD", "TREAT", "CARD", "LINK", "HERE", "THIS", "NOW"}


def _extract_dm_trigger(ig_caption: str, title: str = "") -> str:
    """Return the DM trigger word from 'Comment WORD' in the IG caption.

    Falls back to the longest capitalizable word in the recipe title
    (skipping stop-words) if the caption trigger is missing or generic.
    """
    import re

    stop = {"AND", "THE", "FOR", "WITH", "FROM", "INTO", "A", "AN", "IN", "OF", "OR"}
    m = re.search(r"\bComment\s+([A-Z]{2,20})\b", ig_caption)
    candidate = m.group(1) if m else ""
    if candidate and candidate not in _GENERIC_TRIGGERS:
        return candidate
    # Derive from title: pick longest non-stop word
    words = [w.upper() for w in re.findall(r"[A-Za-z]{3,}", title)]
    words = [w for w in words if w not in stop and w not in _GENERIC_TRIGGERS]
    return max(words, key=len) if words else "BISCUITS"


def draft_fb_caption(recipe: Recipe) -> str:
    """Draft a Facebook caption following brand voice rules. Returns '' on failure."""
    trigger = _extract_dm_trigger(recipe.ig_caption, recipe.title)
    prompt = (
        f"Write a Facebook caption for a dog recipe post.\n"
        f"Recipe title: {recipe.title}\n"
        f"Caption context: {recipe.ig_caption[:300]}\n\n"
        "Rules (HARD — all must be satisfied):\n"
        "- 150-200 words total\n"
        "- Structure: Hook → Personal Nalla story (2-3 sentences) → Key insight "
        "(data-driven) → Engagement question\n"
        f"- Include this EXACT line before the final URL: "
        f"Comment {trigger} and I'll DM you the link — hear the full song + get the printable card!\n"
        "- End with exactly: 📝 Full breakdown: [post_url]\n"
        "- NO hashtags, maximum 1 emoji\n"
        "- Must mention Nalla at least once\n"
        "- Casual warm language: 'honestly', 'works great for us', 'we noticed'\n"
        "- NEVER use medical/clinical language or sales language\n"
        "- NEVER open with generic phrases like 'great post!'\n"
        "- Voice: Nalla's Dad — software engineer + dog owner, authentic, data-driven\n"
        "- Output ONLY the caption text, no preamble\n"
    )
    try:
        return llm_text(prompt)
    except Exception as exc:
        logger.warning("fb_caption draft failed (%s) — using empty string", exc)
        return ""


def draft_lyrics(recipe: Recipe) -> str:
    """Draft a 16-line verse-chorus song for the reel. Returns placeholder on failure."""
    prompt = (
        f"Write a complete, ~16-line rhyming song for a short dog recipe video reel.\n"
        f"Recipe title: {recipe.title}\n"
        f"Caption context: {recipe.ig_caption[:300]}\n\n"
        "Structure (REQUIRED — output ALL sections):\n"
        "  [Verse 1]  — 4 lines introducing the recipe\n"
        "  [Chorus]   — 4 lines, catchy, mention the recipe name\n"
        "  [Verse 2]  — 4 lines about the dog enjoying the treat\n"
        "  [Chorus]   — repeat the chorus exactly\n\n"
        "Rules:\n"
        "- Every line ≤10 words, AABB or ABAB rhyme scheme, cheerful dog-owner voice\n"
        "- IMPORTANT: write ALL 16 lines before stopping\n"
        "- Output ONLY the song with section labels, no preamble\n"
    )
    header = f"# {recipe.title} — Lyrics\n\n"
    note = "<!-- AUTO-DRAFTED — review/edit before feeding to your music tool. -->\n\n"
    try:
        return f"{header}{note}{llm_text(prompt)}\n"
    except Exception as exc:
        logger.warning("lyrics draft failed (%s) — using placeholder", exc)
        return f"{header}<!-- draft failed -->\n"

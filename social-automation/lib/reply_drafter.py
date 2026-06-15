"""Context-aware drafting of replies and comments using Gemini + site content.

Two entry points:

  draft_reply(our_comment, their_reply, their_author, …)
    — used by reply-follower when someone replies to a comment we posted.
      Pulls relevant site posts into the prompt so the response can naturally
      reference our own content ("we wrote this up after tracking it for a
      month") without URL-dropping.

  draft_comment(post_text, category, group_or_hashtag, …)
    — used by auto-drafter when a template match isn't available. Same voice
      + site-aware principles as reply drafting, but for first-touch comments
      on other people's posts.

Both paths:
  - Use Gemini 2.5 Flash (free tier, fast enough for realtime).
  - Pull from `data/site_content_cache.json` to ground the response in our
    actual content.
  - Pass the output through `comment_generator.validate_voice` so off-brand
    language never escapes the drafter.
  - Fall back to a conservative template if Gemini fails or the key isn't set.

Env: GEMINI_API_KEY. If missing, both entry points return the template
fallback so callers still get *something* without crashing.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

from comment_generator import validate_voice

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SITE_CACHE = _PROJECT_ROOT / "data" / "site_content_cache.json"
_GEMINI_MODEL = os.getenv("GEMINI_REPLY_MODEL", "gemini-2.5-flash")
_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_MAX_SITE_POSTS = 8  # trim site cache before sending to Gemini


@dataclass(frozen=True)
class SitePost:
    title: str
    url: str
    excerpt: str
    categories: list[str]
    tags: list[str]


def _load_site_posts() -> list[SitePost]:
    if not _SITE_CACHE.exists():
        return []
    try:
        raw = json.loads(_SITE_CACHE.read_text())
    except Exception:
        return []
    posts = raw.get("recent_posts") or raw.get("posts") or []
    out: list[SitePost] = []
    for p in posts[:_MAX_SITE_POSTS]:
        out.append(
            SitePost(
                title=p.get("title", ""),
                url=p.get("url", ""),
                excerpt=(p.get("excerpt") or "")[:260],
                categories=p.get("categories", []) or [],
                tags=p.get("tags", []) or [],
            )
        )
    return out


def _relevant_posts(needle: str, all_posts: list[SitePost], limit: int = 3) -> list[SitePost]:
    """Rank site posts by keyword overlap with `needle` (simple substring score)."""
    needle_lower = needle.lower()
    words = {w for w in needle_lower.split() if len(w) > 3}
    scored: list[tuple[int, SitePost]] = []
    for p in all_posts:
        hay = " ".join([p.title, p.excerpt, " ".join(p.categories), " ".join(p.tags)]).lower()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [p for _, p in scored[:limit]]


def _format_site_posts(posts: list[SitePost]) -> str:
    if not posts:
        return "(no obviously relevant site posts — respond without referencing site content)"
    lines = []
    for p in posts:
        tags = ", ".join(p.categories[:3] + p.tags[:3])
        lines.append(f"- {p.title} [{tags}] — {p.excerpt[:180]}")
    return "\n".join(lines)


def _call_gemini(prompt: str, *, max_tokens: int = 1200) -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.info("reply_drafter: GEMINI_API_KEY not set — falling back")
        return None
    url = _GEMINI_ENDPOINT.format(model=_GEMINI_MODEL)
    # gemini-2.5-flash defaults to "thinking" mode which consumes output
    # budget before writing any visible text. Disable it for these short
    # drafting tasks — thinking doesn't improve a 2-sentence reply.
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    try:
        r = httpx.post(url, params={"key": key}, json=payload, timeout=30.0)
        if r.status_code >= 400:
            logger.warning("gemini HTTP %s: %s", r.status_code, r.text[:200])
            return None
        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            return None
        parts = cands[0].get("content", {}).get("parts", [])
        for p in parts:
            text = (p.get("text") or "").strip()
            if text:
                return text
    except Exception as e:
        logger.warning("gemini call failed: %s", e)
    return None


_VOICE_RULES = """
BRAND VOICE — Nalla's Dad from dogfoodandfun.com:
- Warm, specific, slightly analytical; not salesy, not clinical
- Mention Nalla by name ONLY if it fits naturally — don't force
- No "check out our site" / "buy now" / "link in bio" / "I'm a vet" / medical claims
- No generic praise ("Great post!", "Love this!", "Amazing!")
- No emojis at the start; 0-1 emoji max total
- End with one specific question tied to what they said — not "what do you think?"
- 1-3 sentences, under 450 chars total
- NEVER invent facts about Nalla or us — no made-up diets, durations, ages, gear,
  or experiences ("we've fed raw for a year", "3 weeks to implement"). Only state
  things that are actually true (see NALLA FACTS if provided).
- When you have no true specific to share, stay first-person but general
  ("in our experience", "we've noticed with Nalla") and lead with genuine
  curiosity about THEIR experience instead of fabricating a story.
"""


def draft_reply(
    our_comment: str,
    their_reply: str,
    their_author: str,
    site_posts: list[SitePost] | None = None,
) -> str:
    """Draft a context-aware reply to someone replying to our comment."""
    posts = site_posts if site_posts is not None else _load_site_posts()
    combined = f"{our_comment}\n{their_reply}"
    relevant = _relevant_posts(combined, posts)
    _parts = (their_author or "").split()
    author_hint = _parts[0] if _parts else "there"

    prompt = f"""You are Nalla's Dad writing a reply on Facebook. Someone replied to your comment.

WHAT YOU ORIGINALLY COMMENTED:
"{our_comment}"

WHAT THEY REPLIED (from {their_author}):
"{their_reply}"

RELEVANT RECENT POSTS FROM YOUR SITE (reference naturally if useful, but do NOT paste URLs):
{_format_site_posts(relevant)}

{_VOICE_RULES}

Additional rules for REPLIES specifically:
- Acknowledge their point first, then add one concrete detail from our experience
- If a site post above is directly relevant, mention casually: "we wrote this up after tracking it for a month" (no URL — save URLs for DM)
- Use their first name ({author_hint}) at most once, and only if natural
- Keep it conversational — you're in a real thread, not broadcasting

Output ONLY the reply text. No preamble, no quotes."""

    text = _call_gemini(prompt, max_tokens=250)
    if text:
        text = _strip_meta_chrome(text)
        valid, _violations = validate_voice(text)
        if valid:
            return text
        logger.info("gemini reply failed voice validation — falling back")

    # Fallback: conservative template
    return (
        f"Good question, {author_hint} — we hit that same spot with Nalla early on. "
        f"If it helps, the thing that moved the needle for us was being stubbornly "
        f"consistent for about two weeks before switching anything else. "
        f"What are you seeing in the first few days?"
    )


def draft_comment(
    post_text: str,
    category: str,
    group_or_hashtag: str,
    site_posts: list[SitePost] | None = None,
) -> str:
    """Draft a context-aware first-touch comment on someone else's post."""
    posts = site_posts if site_posts is not None else _load_site_posts()
    relevant = _relevant_posts(post_text + " " + category, posts)

    prompt = f"""You are Nalla's Dad commenting on a Facebook post from someone else.

WHERE YOU'RE COMMENTING: {group_or_hashtag} (category: {category})

THEIR POST (verbatim):
"{post_text[:1200]}"

RELEVANT RECENT POSTS FROM YOUR SITE (reference naturally if useful, but do NOT paste URLs — even to dogfoodandfun.com):
{_format_site_posts(relevant)}

{_VOICE_RULES}

Additional rules for FIRST-TOUCH COMMENTS:
- This is the first time this person sees your voice — earn the follow
- Reference a concrete detail from THEIR post so they know you read it
- If a site post is directly relevant and adds real value, hint at it ("we tracked this for three months" not "we wrote a post about this")
- End with a specific question about their situation
- Never paste a URL; drives via profile click, not link

Output ONLY the comment text. No preamble, no quotes."""

    text = _call_gemini(prompt, max_tokens=300)
    if text:
        text = _strip_meta_chrome(text)
        valid, violations = validate_voice(text)
        if valid:
            return text
        logger.info("gemini comment failed voice validation %s — falling back", violations)
    return ""  # let caller decide (template or skip)


def _strip_meta_chrome(text: str) -> str:
    """Trim common model-added chrome: surrounding quotes, leading 'Reply:' etc."""
    text = text.strip()
    for pref in ("Reply:", "Comment:", "Response:", "Here's the reply:", "Here is the reply:"):
        if text.lower().startswith(pref.lower()):
            text = text[len(pref) :].lstrip()
    # Strip wrapping quotes if present
    if len(text) >= 2 and text[0] in '"“”' and text[-1] in '"“”':
        text = text[1:-1].strip()
    return text


# Re-export the private helpers so sibling modules (e.g. lib.draft_helper)
# can compose the same Gemini call + voice-rules prompt without duplicating
# the HTTP payload shape or the brand-voice text. The leading underscore is
# preserved to signal "internal — don't import from outside lib/".
__all__ = [
    "_VOICE_RULES",
    "SitePost",
    "_call_gemini",
    "draft_comment",
    "draft_reply",
]

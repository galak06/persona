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
  - Call whichever provider ``lib.llm_client`` selects (Gemini 2.5 Flash by
    default — free tier, fast enough for realtime).
  - Send the standing persona/voice rules as the SYSTEM prompt, loaded from
    the comment-composer skill's ``## LLM Prompt`` section via
    ``lib.skill_loader`` (a `MODE:` line in the user prompt selects the
    reply vs first-touch subsection). A broken skill file fails fast.
  - Pull from `data/site_content_cache.json` to ground the response in our
    actual content.
  - Pass the output through `comment_generator.validate_voice` so off-brand
    language never escapes the drafter.
  - Fall back to a conservative template if the call fails or no key is set.

Env: ``VOICE_PROVIDER`` picks the provider (default: whichever key is set,
Gemini first); ``GEMINI_API_KEY`` / ``ANTHROPIC_API_KEY`` supply the key. With
none set, both entry points return the template fallback so callers still get
*something* without crashing.

Provider selection and the HTTP/SDK transports (and their Langfuse tracing)
live in ``lib.llm_client``; this module only builds prompts and
post-processes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from lib.comment_generator import validate_voice
from lib.llm_client import LLMRequest, get_llm
from lib.skill_loader import load_skill_prompt

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SITE_CACHE = _PROJECT_ROOT / "data" / "site_content_cache.json"
_MAX_SITE_POSTS = 8  # trim site cache before sending to Gemini


@dataclass(frozen=True)
class SitePost:
    title: str
    url: str
    excerpt: str
    categories: list[str]
    tags: list[str]


def _llm_text(prompt: str, *, max_tokens: int, system: str | None = None) -> str | None:
    """This module's LLM seam: one plain-text completion from whichever
    provider ``lib.llm_client`` selects (Gemini by default). ``None`` on any
    failure, so both entry points take their template fallback.
    ``trace_name`` keeps the pre-provider-split Langfuse generation name.
    """
    return get_llm().complete(
        LLMRequest(user=prompt, system=system, max_tokens=max_tokens, trace_name="gemini-draft")
    )


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


@lru_cache(maxsize=1)
def _system_prompt() -> str:
    """Rendered comment-composer ``## LLM Prompt`` section, cached per process.

    Raises ``SkillPromptError`` (from ``lib.skill_loader``) on a missing or
    broken skill file — a deployment defect must abort loudly, never degrade
    into a promptless call.
    """
    return load_skill_prompt("comment-composer")


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
    system = _system_prompt()

    prompt = f"""MODE: reply

WHAT YOU ORIGINALLY COMMENTED:
"{our_comment}"

WHAT THEY REPLIED (from {their_author}):
"{their_reply}"

THEIR FIRST NAME: {author_hint}

RELEVANT RECENT POSTS FROM YOUR SITE (reference naturally if useful, but do NOT paste URLs):
{_format_site_posts(relevant)}

Output ONLY the reply text. No preamble, no quotes."""

    text = _llm_text(prompt, max_tokens=250, system=system)
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
    system = _system_prompt()

    prompt = f"""MODE: first-touch

WHERE YOU'RE COMMENTING: {group_or_hashtag} (category: {category})

THEIR POST (verbatim):
"{post_text[:1200]}"

RELEVANT RECENT POSTS FROM YOUR SITE (reference naturally if useful, but do NOT paste URLs — even to your-brand.com):
{_format_site_posts(relevant)}

Output ONLY the comment text. No preamble, no quotes."""

    text = _llm_text(prompt, max_tokens=300, system=system)
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


# Re-export the meta-chrome stripper so sibling modules (e.g. lib.draft_helper)
# can reuse it without duplicating it. The leading underscore is preserved to
# signal "internal — don't import from outside lib/". The LLM transport lives
# in lib.llm_client (and, for Gemini, lib.gemini_client), not here.
__all__ = [
    "SitePost",
    "_strip_meta_chrome",
    "draft_comment",
    "draft_reply",
]

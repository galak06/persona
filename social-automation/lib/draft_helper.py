"""Inline LLM drafting helper for engagement-comment scanners and commenters.

Two entry points, both in the Nalla's-Dad voice:
  - ``draft_comment_for_post``       — a 1-3 sentence reply. Used by the IG and
    WordPress scanners, which draft inline at scan time.
  - ``draft_short_comment_for_post`` — one tight sentence (~15-25 words) grounded
    in the specific post. Used by ``scripts/fb_comment.py``, which drafts at
    POST time so the comment reflects the live post text.

Both route the draft through ``lib.comment_generator.validate_voice`` with
``allow_own_url=False`` — engagement comments must never carry our URL — and
retry the LLM call **once** with a stricter prompt that names the first
attempt's voice violations. Both return ``""`` (empty string) on any failure
path so the caller can simply check truthiness and skip the item. Empty drafts
are converted to ``USER_SKIPPED`` (scanner path) or just skipped (commenter).

All failure paths emit a structured ``log.info`` (or ``log.warning`` on the
final voice-validation failure) so the engagement log + observability stack
can attribute drops.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from lib.comment_generator import validate_voice
from lib.reply_drafter import _VOICE_RULES, _call_gemini

log = logging.getLogger(__name__)

Platform = Literal["facebook", "instagram", "wordpress"]

_MAX_TOKENS = 400
_SHORT_MAX_TOKENS = 120


@lru_cache(maxsize=1)
def _nalla_facts() -> str:
    """Curated TRUE facts about Nalla/the brand, from
    ``${BRAND_DIR}/data/config/nalla_facts.md`` — injected so the model grounds
    every personal claim instead of fabricating diets/durations. Empty when the
    file is absent (the no-fabrication voice rule still applies)."""
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return ""
    try:
        return (Path(brand_dir) / "data" / "config" / "nalla_facts.md").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return ""


def _require_gemini_key(platform: str, group_or_hashtag: str | None) -> None:
    """Raise ``RuntimeError`` if the Gemini key is absent (callers catch it)."""
    if not os.environ.get("GEMINI_API_KEY"):
        error_msg = "GEMINI_API_KEY environment variable is not set"
        log.error(
            {
                "event": "draft_gemini_key_missing",
                "platform": platform,
                "group_or_hashtag": group_or_hashtag,
                "error": error_msg,
            }
        )
        raise RuntimeError(error_msg)


def draft_comment_for_post(
    *,
    platform: Platform,
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None = None,
    site_context: str | None = None,
) -> str:
    """Generate a 1-3 sentence Nalla's-Dad engagement comment for a post.

    Returns the validated draft text (stripped), or an empty string on a missing
    candidate or two voice-validation failures (one retry).
    """
    _require_gemini_key(platform, group_or_hashtag)
    prompt = _build_prompt(
        platform=platform,
        post_text=post_text,
        group_or_hashtag=group_or_hashtag,
        post_url=post_url,
        site_context=site_context,
        short=False,
    )
    return _draft_validated(
        prompt,
        platform=platform,
        group_or_hashtag=group_or_hashtag,
        max_tokens=_MAX_TOKENS,
    )


def draft_short_comment_for_post(
    *,
    platform: Platform,
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None = None,
) -> str:
    """Generate one tight (~15-25 word) reply grounded in the specific post.

    Used by the FB commenter at post time. Same voice validation + single retry
    as the long path; returns ``""`` on any failure so the caller can skip the
    item. Voice rules still require a trailing question, a specific detail, and
    a first-person claim, so the one sentence must carry all three.
    """
    _require_gemini_key(platform, group_or_hashtag)
    prompt = _build_prompt(
        platform=platform,
        post_text=post_text,
        group_or_hashtag=group_or_hashtag,
        post_url=post_url,
        site_context=None,
        short=True,
    )
    return _draft_validated(
        prompt,
        platform=platform,
        group_or_hashtag=group_or_hashtag,
        max_tokens=_SHORT_MAX_TOKENS,
    )


def _draft_validated(
    prompt: str,
    *,
    platform: str,
    group_or_hashtag: str | None,
    max_tokens: int,
) -> str:
    """Call Gemini, voice-validate, retry once naming the violations.

    Returns the cleaned draft, or ``""`` on no candidate text / two failures.
    """
    draft = _call_gemini(prompt, max_tokens=max_tokens)
    if not draft:
        log.info(
            {
                "event": "draft_gemini_returned_none",
                "platform": platform,
                "group_or_hashtag": group_or_hashtag,
            }
        )
        return ""

    valid, violations = validate_voice(draft, allow_own_url=False)
    if valid:
        cleaned = draft.strip()
        log.info(
            {"event": "draft_inline_ok", "platform": platform, "len": len(cleaned), "attempts": 1}
        )
        return cleaned

    # Retry once with a stricter prompt that calls out the violations.
    log.info({"event": "draft_voice_retry", "platform": platform, "violations": violations})
    retry_prompt = (
        f"{prompt}\n\nIMPORTANT: your previous draft failed brand-voice "
        f"validation. Avoid the following violations on this rewrite: "
        f"{'; '.join(violations)}"
    )
    retry_draft = _call_gemini(retry_prompt, max_tokens=max_tokens)
    if not retry_draft:
        log.info({"event": "draft_gemini_retry_returned_none", "platform": platform})
        return ""

    valid, violations = validate_voice(retry_draft, allow_own_url=False)
    if valid:
        cleaned = retry_draft.strip()
        log.info(
            {"event": "draft_inline_ok", "platform": platform, "len": len(cleaned), "attempts": 2}
        )
        return cleaned

    log.warning({"event": "draft_voice_fail_final", "platform": platform, "violations": violations})
    return ""


def _build_prompt(
    *,
    platform: Platform,
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None,
    site_context: str | None,
    short: bool,
) -> str:
    """Assemble the Gemini prompt: voice rules + context + instruction."""
    parts: list[str] = [_VOICE_RULES.strip()]
    facts = _nalla_facts()
    if facts:
        parts.append(
            "\nNALLA FACTS — the ONLY true details about Nalla/us you may state as "
            "ours. If the post's topic is NOT covered here, do not invent specifics; "
            "stay general and ask about THEIR experience.\n" + facts
        )
    parts.append(f"\nPLATFORM: {platform}")
    if group_or_hashtag:
        parts.append(f"GROUP/HASHTAG: {group_or_hashtag}")
    if post_url:
        parts.append(f"POST URL: {post_url}")
    parts.append(f"\nORIGINAL POST:\n{post_text.strip()}")
    if site_context:
        parts.append(
            f"\nRELEVANT SITE CONTENT (do NOT link unless natural):\n{site_context}"
        )
    if short:
        parts.append(
            "\nWrite ONE short sentence (15-25 words) replying to the post above. "
            "React to a SPECIFIC detail from THIS post, mention Nalla or our own "
            "experience, and end with a brief genuine question. No greeting, no "
            "generic opener, no salesy language, no medical claims, no links. "
            "Output ONLY the reply text — no preamble, no quotes."
        )
    else:
        parts.append(
            "\nWrite a single short reply (1-3 sentences). Personal, helpful, "
            "no salesy language, no medical claims, no links. Output ONLY the "
            "reply text — no preamble, no quotes."
        )
    return "\n".join(parts)

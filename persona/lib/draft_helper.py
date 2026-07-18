"""Inline LLM drafting helper for engagement-comment scanners and commenters.

Two entry points, both in the configured brand voice:
  - ``draft_comment_for_post``       — a 1-3 sentence reply. Used by the IG and
    WordPress scanners, which draft inline at scan time.
  - ``draft_short_comment_for_post`` — one tight sentence (~15-25 words) grounded
    in the specific post. Used by ``scripts/fb_comment.py``, which drafts at
    POST time so the comment reflects the live post text.

Both are agentic: the model first decides whether this specific post is
genuinely worth engaging with (``engage: true|false``) before drafting.
``engage`` is read fail-closed (``is not True`` → decline), so a schema hiccup
that returns a non-boolean can never post a comment the model meant to skip.
``engage: false`` means the model itself declined — that decision IS the
approval gate for outbound comments (there is no separate human-in-the-loop
step), and it flows through unchanged: both entry points still return ``""``
on decline, exactly like every other failure path below, so callers
(``lib/engagement/commenter.py``'s drain loop) need no changes at all.

A missing ``GEMINI_API_KEY`` is treated like any other upstream failure —
``_call_gemini_json`` returns ``None`` and the item is skipped with a logged
warning — rather than raising mid-batch and aborting the whole run.

Both route the draft through ``lib.comment_generator.validate_voice`` with
``allow_own_url=False`` — engagement comments must never carry our URL — and
retry the LLM call **once** with a stricter prompt that names the first
attempt's voice violations. Both return ``""`` (empty string) on any failure
path (missing candidate, agent decline, two voice-validation failures) so the
caller can simply check truthiness and skip the item. Empty drafts are
converted to ``USER_SKIPPED`` (scanner path) or just skipped (commenter).

All failure/decline paths emit a structured ``log.info`` (or ``log.warning``
on the final voice-validation failure) so the engagement log + observability
stack can attribute drops.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from lib.comment_generator import validate_voice
from lib.gemini_client import _call_gemini_json
from lib.reply_drafter import _VOICE_RULES, _strip_meta_chrome

log = logging.getLogger(__name__)

Platform = Literal["facebook", "instagram", "wordpress"]

_MAX_TOKENS = 500
_SHORT_MAX_TOKENS = 200

_ENGAGE_INSTRUCTIONS = """
Before drafting, decide whether THIS SPECIFIC post is genuinely worth
engaging with as our brand. Decline (engage=false) if:
- the post is generic/low-effort and a reply would feel like spam
- our brand has no authentic, specific angle on this exact post
- the post is from a competitor account
- replying here would feel repetitive or forced rather than genuine

Respond with ONLY a JSON object — no markdown fences, no preamble, no text
before or after it:
{"engage": true or false, "comment": "<the reply text, or \\"\\" if engage is false>", "reason": "<one short sentence explaining the decision>"}
"""


@lru_cache(maxsize=1)
def _nalla_facts() -> str:
    """Curated TRUE facts about the brand/mascot, loaded from
    ``${BRAND_DIR}/data/config/brand_facts.md`` (falls back to ``nalla_facts.md``
    for backwards compatibility). Injected so the model grounds every personal
    claim instead of fabricating details. Empty when the file is absent."""
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return ""
    base = Path(brand_dir) / "data" / "config"
    for name in ("brand_facts.md", "nalla_facts.md"):
        try:
            return (base / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return ""


def draft_comment_for_post(
    *,
    platform: Platform,
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None = None,
    site_context: str | None = None,
) -> str:
    """Generate a 1-3 sentence Nalla's-Dad engagement comment for a post.

    Returns the validated draft text (stripped), or an empty string if the
    agent declined to engage, the Gemini call failed (including a missing
    key), or after two voice-validation failures (one retry).
    """
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

    Used by the FB commenter at post time. Same agent decision + voice
    validation + single retry as the long path; returns ``""`` on decline or
    any failure (including a missing key) so the caller can skip the item.
    Voice rules still require a trailing question, a specific detail, and a
    first-person claim, so the one sentence must carry all three.
    """
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


_MAX_ATTEMPTS = 2


def _draft_validated(
    prompt: str,
    *,
    platform: str,
    group_or_hashtag: str | None,
    max_tokens: int,
) -> str:
    """Call Gemini for an engage/comment/reason decision, voice-validate the
    drafted comment, and retry once (naming the violations) if it fails.

    Returns the cleaned draft, or ``""`` on no response / agent decline / blank
    comment / two voice-validation failures. A decline or upstream failure is
    final — only a *voice* failure earns the retry.
    """
    current_prompt = prompt
    violations: list[str] = []
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        response = _call_gemini_json(current_prompt, max_tokens=max_tokens)
        draft = _engaged_comment(
            response, platform=platform, group_or_hashtag=group_or_hashtag, attempt=attempt
        )
        if not draft:
            return ""  # None / decline / blank — all logged in _engaged_comment
        valid, violations = validate_voice(draft, allow_own_url=False)
        if valid:
            log.info(
                {
                    "event": "draft_inline_ok",
                    "platform": platform,
                    "len": len(draft),
                    "attempt": attempt,
                }
            )
            return draft
        if attempt < _MAX_ATTEMPTS:
            log.info({"event": "draft_voice_retry", "platform": platform, "violations": violations})
            current_prompt = (
                f"{prompt}\n\nIMPORTANT: your previous draft failed brand-voice "
                f"validation. Avoid the following violations on this rewrite: "
                f"{'; '.join(violations)}"
            )

    log.warning({"event": "draft_voice_fail_final", "platform": platform, "violations": violations})
    return ""


def _engaged_comment(
    response: dict[str, Any] | None,
    *,
    platform: str,
    group_or_hashtag: str | None,
    attempt: int,
) -> str:
    """Extract the drafted comment from an agent response, or ``""`` if the
    call failed, the agent declined, or the comment was blank. Every outcome
    is logged (with the model's ``reason`` where present) so the cause of a
    skip is always attributable. ``engage`` is read fail-closed: only a literal
    ``True`` counts as engage, so a non-boolean (e.g. the string ``"false"``)
    can never post a comment the model meant to decline."""
    base = {"platform": platform, "group_or_hashtag": group_or_hashtag, "attempt": attempt}
    if response is None:
        log.info({"event": "draft_gemini_returned_none", **base})
        return ""
    if response.get("engage") is not True:
        log.info(
            {"event": "draft_agent_declined", **base, "reason": str(response.get("reason") or "")}
        )
        return ""
    comment = _strip_meta_chrome(str(response.get("comment") or ""))
    if not comment:
        # engage:true but no usable comment text — a malformed response, not a
        # deliberate decline; log it so this drop isn't silent/unattributable.
        log.warning(
            {
                "event": "draft_engaged_but_blank",
                **base,
                "reason": str(response.get("reason") or ""),
            }
        )
        return ""
    return comment


def _build_prompt(
    *,
    platform: Platform,
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None,
    site_context: str | None,
    short: bool,
) -> str:
    """Assemble the Gemini prompt: voice rules + context + engage/draft instruction."""
    parts: list[str] = [_VOICE_RULES.strip()]
    facts = _nalla_facts()
    if facts:
        parts.append(
            "\nBRAND FACTS — the ONLY true details about our brand/mascot you may "
            "state as ours. If the post's topic is NOT covered here, do not invent "
            "specifics; stay general and ask about THEIR experience.\n" + facts
        )
    parts.append(f"\nPLATFORM: {platform}")
    if group_or_hashtag:
        parts.append(f"GROUP/HASHTAG: {group_or_hashtag}")
    if post_url:
        parts.append(f"POST URL: {post_url}")
    parts.append(f"\nORIGINAL POST:\n{post_text.strip()}")
    if site_context:
        parts.append(f"\nRELEVANT SITE CONTENT (do NOT link unless natural):\n{site_context}")
    if short:
        parts.append(
            "\nIf you decide to engage, the reply should be ONE short sentence "
            "(15-25 words) replying to the post above. React to a SPECIFIC "
            "detail from THIS post, mention Nalla or our own experience, and "
            "end with a brief genuine question. No greeting, no generic "
            "opener, no salesy language, no medical claims, no links."
        )
    else:
        parts.append(
            "\nIf you decide to engage, the reply should be a single short "
            "reply (1-3 sentences). Personal, helpful, no salesy language, "
            "no medical claims, no links."
        )
    parts.append(_ENGAGE_INSTRUCTIONS.strip())
    return "\n".join(parts)

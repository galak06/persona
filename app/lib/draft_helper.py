"""Inline LLM drafting helper for engagement-comment scanners and commenters.

``SkillDrafter`` (built via ``for_skill``) is the only drafting path: it
binds a skill's ``## LLM Prompt`` section (``lib.skill_loader``) plus brand
facts as the SYSTEM prompt and sends only per-post context as the USER
prompt. Every wired flow — ``scripts/ig_comment.py``, ``scripts/ig_scan.py``,
``scripts/fb_comment.py``, ``scripts/wp_scan.py`` — binds its own skill, so
the flow's voice, engage-decision and length rules live in that SKILL.md and
never in Python. The system prompt is loaded eagerly so a broken SKILL.md
aborts at startup (fail-fast), while per-call failures below still degrade
to a per-item skip.

``draft_comment_for_post`` (1-3 sentence replies) and
``draft_short_comment_for_post`` (one tight reply, used by the FB commenter)
differ only in the token budget and whether site context is sent; the
requested LENGTH comes from the bound skill's own rule.

Drafting is agentic: the model first decides whether this specific post is
worth engaging with. ``engage`` is read fail-closed (``is not True`` →
decline) and IS the approval gate for outbound comments (no separate
human-in-the-loop step). Every entry point returns ``""`` on ANY failure or
decline path — missing key (``_llm_json`` → ``None``), agent
decline, blank comment, or two voice-validation failures — so callers
(``lib/engagement/commenter.py``'s drain loop) just check truthiness and
skip. Every draft routes through ``lib.comment_generator.validate_voice``
with ``allow_own_url=False`` and retries the LLM call **once** with a
stricter USER prompt naming the first attempt's violations (the system
prompt never changes between attempts). All failure/decline paths emit a
structured ``log.info``/``log.warning`` so drops stay attributable.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from lib import skill_loader
from lib.comment_generator import validate_voice
from lib.draft_prompts import ENGAGE_RESPONSE_SCHEMA, build_system_prompt, build_user_prompt
from lib.llm_client import LLMRequest, get_llm
from lib.reply_drafter import _strip_meta_chrome

log = logging.getLogger(__name__)

Platform = Literal["facebook", "instagram", "wordpress"]

# maxOutputTokens is a CEILING, not a target — length is bounded by the
# prompt. The budget must cover the comment PLUS `reason` PLUS the JSON
# scaffolding; too small and a long reply truncates to unparseable JSON.
#
# Raised 2026-07-29 for the Gemini 3.x family, which cannot disable thinking
# (see lib/gemini_client.py::_base_payload). Thinking tokens are drawn from
# this same ceiling BEFORE any visible text, so the previous 800/400 left the
# JSON envelope truncated mid-string — which surfaces as an empty draft and is
# then logged as `agent_declined_or_empty_draft`, i.e. a silent total failure
# that looks like the agent choosing not to engage.
_MAX_TOKENS = 2400
_SHORT_MAX_TOKENS = 1600

AGENT_DECLINED = "agent_declined"
DRAFT_FAILED = "draft_failed"
DRAFT_BLANK = "draft_blank"
VOICE_FAILED = "voice_validation_failed"


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


def _system_prompt(skill: str) -> str:
    """SYSTEM prompt for ``skill``: its rendered ``## LLM Prompt`` section
    plus the brand-facts grounding block. Raises ``SkillPromptError`` (from
    ``lib.skill_loader``) on any defect — surfaced eagerly at binder init."""
    return build_system_prompt(skill_loader.load_skill_prompt(skill), _nalla_facts())


class SkillDrafter:
    """Drafter bound to a skill's SKILL.md system prompt.

    `last_outcome` names why the most recent draft came back empty — one of
    AGENT_DECLINED, DRAFT_FAILED, DRAFT_BLANK or VOICE_FAILED, or None after a
    success. `lib/engagement/inline_comment.py` reads it so a skipped post is
    logged with its real cause: without it a retired model or a dead key is
    indistinguishable from the agent thoughtfully declining, which hid a 404ing
    model for a full scan run. Safe as instance state because a drafter is
    called once per post, synchronously, by a single-threaded scan loop.

    Instances satisfy ``lib.engagement.collaborators.Drafter``. The system
    prompt is loaded eagerly, in ``__init__`` — so a broken SKILL.md raises
    ``SkillPromptError`` the moment a drafter is constructed, never mid-drain.
    The four wired scripts construct theirs at module level, so for them that
    means the failure surfaces at import, before any work starts; a drafter
    built lazily would instead raise wherever it is constructed.

    Note the deliberate asymmetry with ``lib/reply_drafter.py`` and the
    ideator modules (``recipe-publisher/ideator/research.py``,
    ``enricher.py``): those load their prompt lazily (``lru_cache`` on first
    draft call), so a broken SKILL.md there surfaces mid-run instead.
    """

    def __init__(self, skill: str) -> None:
        self.skill = skill
        self._system = _system_prompt(skill)
        self.last_outcome: str | None = None

    def _run(
        self, prompt: str, *, platform: str, group_or_hashtag: str | None, max_tokens: int
    ) -> str:
        """Draft, recording why an empty result happened."""
        outcome: list[str] = []
        draft = _draft_validated(
            prompt,
            platform=platform,
            group_or_hashtag=group_or_hashtag,
            max_tokens=max_tokens,
            system=self._system,
            outcome=outcome,
        )
        self.last_outcome = outcome[0] if outcome else None
        return draft

    def draft_comment_for_post(
        self,
        *,
        platform: Platform,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str | None = None,
        site_context: str | None = None,
    ) -> str:
        """Draft a brand-voice engagement comment for a post.

        Returns the validated draft, or ``""`` on decline / upstream failure
        / two voice-validation failures (one retry). Length is whatever the
        bound skill's own rule asks for (typically 1-3 sentences).
        """
        prompt = build_user_prompt(
            platform=platform,
            post_text=post_text,
            group_or_hashtag=group_or_hashtag,
            post_url=post_url,
            site_context=site_context,
        )
        return self._run(
            prompt, platform=platform, group_or_hashtag=group_or_hashtag, max_tokens=_MAX_TOKENS
        )

    def draft_short_comment_for_post(
        self,
        *,
        platform: Platform,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str | None = None,
    ) -> str:
        """Draft one tight reply grounded in the specific post.

        Used by the FB commenter at post time: no site context and a smaller
        token budget. Same decision + validation + single-retry contract as
        ``draft_comment_for_post``; the ~15-25 word target comes from the
        bound skill's length rule.
        """
        prompt = build_user_prompt(
            platform=platform,
            post_text=post_text,
            group_or_hashtag=group_or_hashtag,
            post_url=post_url,
            site_context=None,
        )
        return self._run(
            prompt,
            platform=platform,
            group_or_hashtag=group_or_hashtag,
            max_tokens=_SHORT_MAX_TOKENS,
        )


def for_skill(skill: str) -> SkillDrafter:
    """Bind a drafter to ``skill``'s SKILL.md prompt.

    Fail-fast at construction: ``SkillDrafter.__init__`` loads the prompt
    eagerly, so a defective SKILL.md raises ``SkillPromptError`` here rather
    than on the first draft call. The four wired scripts call this at module
    level, so for them a broken skill aborts at import. Contrast
    ``lib/reply_drafter.py`` and the ideator modules, which deliberately load
    their prompt lazily (first draft call) and therefore fail mid-run.
    """
    return SkillDrafter(skill)


_MAX_ATTEMPTS = 2


def _llm_json(prompt: str, *, max_tokens: int, system: str | None = None) -> dict[str, Any] | None:
    """This module's LLM seam: one ``{engage, comment, reason}`` decision from
    whichever provider ``lib.llm_client`` selects (Gemini by default).

    Returns ``None`` on any upstream failure OR on a response missing
    ``engage`` — a malformed envelope is not a decision, and the caller treats
    both as "no draft". ``trace_name`` keeps the pre-provider-split Langfuse
    generation name so existing dashboards stay continuous.
    """
    response = get_llm().complete_json(
        LLMRequest(
            user=prompt,
            system=system,
            max_tokens=max_tokens,
            trace_name="gemini-engage-decision",
        ),
        response_schema=ENGAGE_RESPONSE_SCHEMA,
    )
    if response is None:
        return None
    if "engage" not in response:
        log.warning("llm json response missing 'engage' field: %s", str(response)[:200])
        return None
    return response


def _draft_validated(
    prompt: str,
    *,
    platform: str,
    group_or_hashtag: str | None,
    max_tokens: int,
    system: str | None = None,
    outcome: list[str] | None = None,
) -> str:
    """Call the LLM for an engage/comment/reason decision, voice-validate the
    drafted comment, and retry once (naming the violations) if it fails.

    ``system`` (the skill path's system prompt) is constant across attempts;
    the voice-retry suffix is appended to the USER prompt only. A decline or
    upstream failure is final — only a *voice* failure earns the retry.
    """
    current_prompt = prompt
    violations: list[str] = []
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        response = _llm_json(current_prompt, max_tokens=max_tokens, system=system)
        draft = _engaged_comment(
            response,
            platform=platform,
            group_or_hashtag=group_or_hashtag,
            attempt=attempt,
            outcome=outcome,
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
    if outcome is not None:
        outcome.append(VOICE_FAILED)
    return ""


def _engaged_comment(
    response: dict[str, Any] | None,
    *,
    platform: str,
    group_or_hashtag: str | None,
    attempt: int,
    outcome: list[str] | None = None,
) -> str:
    """Extract the drafted comment from an agent response, or ``""`` if the
    call failed, the agent declined, or the comment was blank. Every outcome
    is logged (with the model's ``reason`` where present) so skips stay
    attributable. ``engage`` is read fail-closed: only a literal ``True``
    engages, so a non-boolean can never post a declined comment."""
    base = {"platform": platform, "group_or_hashtag": group_or_hashtag, "attempt": attempt}

    def _record(tag: str) -> str:
        if outcome is not None:
            outcome.append(tag)
        return ""

    if response is None:
        log.info({"event": "draft_gemini_returned_none", **base})
        return _record(DRAFT_FAILED)
    if response.get("engage") is not True:
        log.info(
            {"event": "draft_agent_declined", **base, "reason": str(response.get("reason") or "")}
        )
        return _record(AGENT_DECLINED)
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
        return _record(DRAFT_BLANK)
    return comment

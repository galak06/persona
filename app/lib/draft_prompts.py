"""Prompt assembly for ``lib.draft_helper`` (extracted for file size).

The engagement drafting prompt is a system/user split:

  - ``build_system_prompt`` — the flow's skill file supplies the standing
    rules (brand voice, engage-decision bullets, the flow's length rule),
    rendered by ``lib.skill_loader`` from its ``## LLM Prompt`` section; this
    module only appends the brand-facts grounding block.
  - ``build_user_prompt`` — the per-call half: this post's context plus the
    JSON response envelope. Nothing standing is restated here; a rule that
    appears in both halves is drift waiting to happen.

The JSON response envelope deliberately stays here in Python (never in a
SKILL.md): its shape is owned by ``ENGAGE_RESPONSE_SCHEMA`` below — the two
are one contract and must not drift via a skill-file edit. The schema is
handed to whichever provider ``lib.llm_client`` selects (Gemini enforces it
natively via ``responseSchema``; Anthropic gets it in the prompt).
"""

from __future__ import annotations

from typing import Any, Final

ENGAGE_RESPONSE_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "engage": {"type": "boolean"},
        "comment": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["engage", "comment", "reason"],
}

_JSON_ENVELOPE = """
Respond with ONLY a JSON object — no markdown fences, no preamble, no text
before or after it:
{"engage": true or false, "comment": "<the reply text, or \\"\\" if engage is false>", "reason": "<one short sentence, 12 words max, explaining the decision>"}
"""

_BRAND_FACTS_WRAPPER = (
    "\nBRAND FACTS — the ONLY true details about our brand/mascot you may "
    "state as ours. If the post's topic is NOT covered here, do not invent "
    "specifics; stay general and ask about THEIR experience.\n"
)


def build_system_prompt(skill_prompt: str, facts: str) -> str:
    """SYSTEM prompt: the rendered SKILL.md section plus the brand-facts
    grounding block (omitted entirely when the brand has no facts file)."""
    if not facts:
        return skill_prompt
    return skill_prompt + "\n" + _BRAND_FACTS_WRAPPER + facts


def build_user_prompt(
    *,
    platform: str,
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None,
    site_context: str | None,
) -> str:
    """USER prompt: per-post context + the JSON response envelope.

    Voice, engage-decision and length rules are NOT repeated here — they
    arrive via the system prompt (the flow's SKILL.md), which is also where
    the per-flow length rule (short FB reply vs 1-3 sentence reply) lives.
    """
    parts = [f"\nPLATFORM: {platform}"]
    if group_or_hashtag:
        parts.append(f"GROUP/HASHTAG: {group_or_hashtag}")
    if post_url:
        parts.append(f"POST URL: {post_url}")
    parts.append(f"\nORIGINAL POST:\n{post_text.strip()}")
    if site_context:
        parts.append(f"\nRELEVANT SITE CONTENT (do NOT link unless natural):\n{site_context}")
    parts.append(_JSON_ENVELOPE.strip())
    return "\n".join(parts).lstrip("\n")

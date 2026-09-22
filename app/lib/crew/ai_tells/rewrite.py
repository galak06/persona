"""Heading repair for already-published posts.

Scope is deliberately narrow: this rewrites HEADINGS ONLY, never body prose.

That is what the measurements support. Scanning the 12 most recent live
dogfoodandfun posts, the ONLY blocking finding on any of them was
`template_heading` -- cliché rate topped out at 1.33/1000 against a 4.0
ceiling, sentence variation ran 0.44-0.87 against a 0.35 floor, and no post
tripped the transition, antithesis or cadence rules at all. Regenerating
whole articles to fix a defect that lives entirely in ~18 heading strings
would burn the posts' tuned prose, affiliate placements, internal links and
schema for no measured gain.

The replacement heading has to be about the post, which is a judgment, so
this is the one LLM-backed piece of `lib.crew.ai_tells`. Everything the model
returns is validated by re-running the deterministic scanner over it -- a
suggestion that is itself a template label is rejected and the heading is
left alone rather than swapped for an equally generic one.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass

from lib.crew.ai_tells.detect import _plain, _scan_headings
from lib.llm_client import LLMRequest, TextLLM
from lib.observability import get_logger

logger = get_logger(__name__)

_MAX_HEADING_CHARS = 70

_SYSTEM = (
    "You retitle sections of a published dog-care blog post. You return only "
    "JSON. You never invent claims; a heading may only describe content that "
    "is already in the section you were shown."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "headings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
                "required": ["old", "new"],
            },
        }
    },
    "required": ["headings"],
}


@dataclass(frozen=True)
class HeadingRewrite:
    """One accepted heading swap."""

    old: str
    new: str


def _section_excerpt(body: str, heading: str, *, limit: int = 700) -> str:
    """The prose immediately following `heading`, so the model retitles from
    what the section actually says rather than from the old label."""
    match = re.search(
        rf"<h([1-6])[^>]*>\s*{re.escape(heading)}\s*</h\1>(.*?)(?=<h[1-6]|$)",
        body,
        re.DOTALL | re.IGNORECASE,
    )
    return _plain(match.group(2))[:limit] if match else ""


def _prompt(title: str, flagged: list[str], body: str) -> str:
    sections = "\n\n".join(
        f"OLD HEADING: {h}\nSECTION TEXT: {_section_excerpt(body, h) or '(no text found)'}"
        for h in flagged
    )
    return f"""Post title: {title}

Each heading below names the slot it fills in a content template ("FAQ",
"Our Pick", "Related Reading") instead of describing what is in the section.
Every post on this site uses the same labels, which makes the whole site read
as machine-generated.

Rewrite each one so it describes THIS section of THIS post. Rules:
- Under {_MAX_HEADING_CHARS} characters.
- First-person, plain, specific to the post's subject. The voice is a dog
  owner who tests things on his own dog, not a brand publishing a resource.
- Never reuse a template label: no "FAQ", "Frequently Asked Questions",
  "Related Reading", "Our Pick", "Product Comparison Table", "Conclusion",
  "Summary", "Key Takeaways", "Final Thoughts", "Introduction".
- Describe only what the section text below actually contains. Do not promise
  content that is not there.
- Good: "What Owners Keep Asking Me About Bully Sticks" (was FAQ),
  "What I Feed Her Now" (was Our Pick), "More From the Kitchen" (was Related
  Reading).

{sections}

Return JSON: {{"headings": [{{"old": "...", "new": "..."}}]}} with one entry
per heading above, `old` copied verbatim."""


def _acceptable(old: str, new: str) -> bool:
    """A suggestion is only an improvement if it is non-empty, short enough,
    actually different, and would not itself be flagged by the scanner."""
    new = new.strip()
    if not new or new.lower() == old.strip().lower():
        return False
    if len(new) > _MAX_HEADING_CHARS:
        return False
    return not _scan_headings([new])


def propose_heading_rewrites(
    *, title: str, body_html: str, flagged: list[str], llm: TextLLM
) -> list[HeadingRewrite]:
    """Ask the model for a replacement per flagged heading, keeping only the
    suggestions that survive the scanner. Returns [] on any LLM failure --
    the caller then leaves the post untouched, which is the safe default for
    already-published content.
    """
    if not flagged:
        return []
    req = LLMRequest(
        user=_prompt(title, flagged, body_html),
        system=_SYSTEM,
        # Generous for a payload that is only a few short strings: on the
        # Gemini path `thinkingBudget` is deliberately left unset
        # (`lib.gemini_client._thinking_budget` -- the 3.x family rejects 0),
        # so reasoning tokens are drawn from this same ceiling. At 800 the
        # model spent the whole budget thinking and emitted a truncated
        # `{"headings":` that parsed as a failure on every post.
        max_tokens=4000,
        temperature=0.8,
        trace_name="ai-tells-heading-rewrite",
    )
    payload = llm.complete_json(req, response_schema=_SCHEMA)
    if not payload:
        logger.warning("ai_tells_rewrite_llm_failed", title=title)
        return []

    by_old = {h.strip().lower(): h for h in flagged}
    accepted: list[HeadingRewrite] = []
    for row in payload.get("headings", []):
        if not isinstance(row, dict):
            continue
        old = by_old.get(str(row.get("old", "")).strip().lower())
        new = str(row.get("new", "")).strip()
        if old and _acceptable(old, new):
            accepted.append(HeadingRewrite(old=old, new=new))
        else:
            logger.info("ai_tells_rewrite_suggestion_rejected", old=row.get("old"), new=new)
    return accepted


def apply_heading_rewrites(body_html: str, rewrites: list[HeadingRewrite]) -> str:
    """Swap each heading's TEXT in place, leaving its tag, attributes and the
    surrounding markup untouched.

    Only the heading element's inner text is replaced -- matching on the tag
    rather than doing a bare string replace, so a heading whose words also
    appear in body prose or in an internal-link anchor is not corrupted.
    """
    result = body_html
    for rewrite in rewrites:
        pattern = re.compile(
            rf"(<h([1-6])[^>]*>)\s*{re.escape(rewrite.old)}\s*(</h\2>)",
            re.IGNORECASE,
        )

        def _swap(match: re.Match[str], new: str = rewrite.new) -> str:
            # A function rather than a `\1...\3` template string: the
            # replacement carries the model's text, and a template would
            # interpret any backslash or `\g<...>` in it as a backreference.
            return f"{match.group(1)}{html_lib.escape(new)}{match.group(3)}"

        result = pattern.sub(_swap, result, count=1)
    return result


def rewrites_as_json(rewrites: list[HeadingRewrite]) -> str:
    """Compact record of what was changed, for the run log and the backup dir."""
    return json.dumps([{"old": r.old, "new": r.new} for r in rewrites], ensure_ascii=False)

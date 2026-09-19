"""Task-description ("prompt") builders for the strategist and writer agents.

Split out of `lib.crew.writer.context` purely for file-size discipline --
these two functions are long, formatted prompt strings, not data-gathering
logic. Mirrors `lib.crew.context.build_task_description`'s role for the
scout, one function per pipeline stage.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from lib.crew.reference_vocabulary import catch_all_clause
from lib.crew.writer.blueprint import _BLUEPRINT_SPEC, _HARD_RULES, _PROSE_RULES
from lib.crew.writer.context import internal_link_candidates_json
from lib.crew.writer.models import ContentBrief, InternalLinkCandidate


def _reference_category_section(categories: Sequence[str]) -> str:
    """The `reference_category` instructions -- empty string when the brand has
    no reference-photo library, so the strategist prompt stays byte-identical
    to what it was before this field existed."""
    if not categories:
        return ""
    listed = "\n".join(f"  - {label}" for label in categories)
    return f"""
## Reference-photo collection (`reference_category`)
The post's hero image is generated with a real photo the brand itself uploaded as its \
visual reference (the result is still a generated image, not a photograph of a real \
moment -- the title rule in step 1 still holds). The brand keeps several collections of \
those photos, one collection per kind of scene:
{listed}
Set `reference_category` to the ONE collection whose scenes best match the hero image \
this post calls for, copied verbatim from the list above. Pick it from the topic and \
your mascot_angle: a feeding post wants a feeding reference, a trail post wants an \
outdoor one. Those collections are the only ones that exist -- there is no "none of \
the above", and a name that is not on that list leaves the hero with no reference \
photo at all. So when nothing is a clean match, still pick the CLOSEST collection on \
the list; a near-miss reference is a real photo of this brand's own, which grounds \
the hero far better than none does.{catch_all_clause(categories)}
"""


def _focus_link_rule(focus_category: str) -> str:
    """The extra internal-link rule for a brand focused on one category.

    Empty string when there is no focus, so an unfocused brand's prompt is
    unchanged. The candidate list is already ordered same-category-first
    (`lib.crew.writer.context.rank_link_candidates`); this is what tells the
    model that the ordering means something.
    """
    if not focus_category.strip():
        return ""
    return (
        f' This brand is focused on "{focus_category.strip()}": prefer candidates whose '
        f"category matches it, so those posts link to each other and read as one body of "
        f"work rather than scattered pages. Only reach outside that category when no "
        f"in-category candidate is genuinely relevant -- relevance still wins over category."
    )


def build_strategist_task_description(
    *,
    idea: dict[str, Any],
    identity: str,
    voice: str,
    mascot_facts: str,
    link_candidates: list[InternalLinkCandidate],
    year: int,
    reference_categories: Sequence[str] = (),
    focus_category: str = "",
) -> str:
    """The strategist agent's full prompt: one content idea -> a `ContentBrief`."""
    return f"""You are turning ONE approved content idea into a structured content brief.

## Brand context
{identity}

## Brand voice (write the brief's angle in this voice; do not draft the post itself)
{voice or "(no voice guide available)"}

## Mascot facts (ONLY true things the mascot_angle may reference as fact)
{mascot_facts or "(no mascot facts file available -- keep mascot_angle general)"}

## The approved idea
Topic: {idea.get("topic", "")}
Target keyword: {idea.get("target_keyword", "")}
Category: {idea.get("category", "")}
Reasoning (why this idea was surfaced): {idea.get("persona_context") or idea.get("nalla_context", "")}

## Real internal-link candidates (choose from these ONLY -- never invent a URL)
{internal_link_candidates_json(link_candidates)}

## Your job
1. Propose a suggested_title including the year {year}. The hero image is AI-generated from a \
short text brief -- it is NOT a real photo of the brand's mascot or persona, and nothing \
verifies it actually depicts them. Never phrase the title as if it documents a specific \
personal/photographed moment (e.g. "Before Nalla and I Ran Properly", "How We Learned X \
Together") -- that claims the accompanying photo shows something it doesn't. Keep the title \
generic and topic/benefit-driven (e.g. "Canicross for Beginners: 5 Gear Mistakes to Avoid"); \
first-person mascot narrative belongs in the body's hook (a text claim, not a visual one), never \
in the title. Keep the title to 60 characters or fewer, INCLUDING the year -- Google truncates \
longer search-result titles (~555-600px, roughly 60 characters), so anything past that limit \
never displays in search results at all.
2. Propose an outline: 4-11 H2/H3 sections with a 1-line note on what each covers. The post \
opens with a hook and a reason the topic matters, carries the argument through its core \
sections, compares products if the topic warrants it, answers real reader questions, points at \
related posts, and closes on a recommendation -- but those are the JOBS each section does, not \
its heading and not a fixed running order. Two rules on the outline you propose:
   - Every heading must be about this post's subject. Never propose a heading that is (or \
starts with) a slot name -- "FAQ", "Frequently Asked Questions", "Related Reading", "Our Pick", \
"Product Comparison Table", "Hook", "Problem", "Introduction", "Conclusion", "Summary", "Key \
Takeaways", "Final Thoughts". A draft carrying one is rejected mechanically, so a heading like \
"What Owners Keep Asking Me About Bully Sticks" is required where "FAQ" would have gone.
   - Choose the section COUNT from the topic rather than defaulting to the middle of the \
range. A single focused comparison earns four sections; a season-long feeding experiment earns \
eleven. Every post landing on the same count is how a site starts looking machine-made.
3. Set primary_keyword to the idea's target keyword (or a close, better-targeted variant) and \
propose 3-6 secondary_keywords.
4. Choose 3-6 internal_link_candidates from the real list above whose topic is genuinely \
relevant to this idea -- if fewer than 3 are relevant, return only the relevant ones.\
{_focus_link_rule(focus_category)}
5. Propose 3-7 faq_questions this post should answer -- real questions a reader would search \
for, not generic filler. Pick the count from how many the topic really raises; do not land on \
the same number every post.
6. Write mascot_angle: 2-4 sentences on how the brand's real voice/mascot fits THIS specific \
topic, grounded in the idea's own reasoning above and the mascot facts above -- never a generic \
statement that could apply to any topic.
{_reference_category_section(reference_categories)}"""


def build_writer_task_description(
    *,
    brief: ContentBrief,
    identity: str,
    voice: str,
    mascot_facts: str,
    catalog_text: str,
    disclosure_text: str,
    persona: str,
    today: str,
    year: int,
) -> str:
    """The writer agent's full prompt: a `ContentBrief` -> a `WrittenPost`."""
    outline_lines = "\n".join(
        f"- [{section.level}] {section.heading} -- {section.notes}" for section in brief.outline
    )
    link_lines = "\n".join(f"- {c.title}: {c.url}" for c in brief.internal_link_candidates)
    faq_lines = "\n".join(f"- {q}" for q in brief.faq_questions)

    return f"""You are writing ONE full blog post from the brief below.

## Brand context
{identity}

## Brand voice (write in this voice throughout)
{voice or "(no voice guide available)"}

## Mascot facts (ONLY true things you may state as fact about the mascot)
{mascot_facts or "(no mascot facts file available -- keep mascot mentions general)"}

## Brief
Title: {brief.suggested_title}
Primary keyword: {brief.primary_keyword}
Secondary keywords: {", ".join(brief.secondary_keywords) or "(none)"}
Mascot angle: {brief.mascot_angle}

Outline:
{outline_lines or "(no outline provided -- use the blueprint below directly)"}

Internal link candidates (use ONLY these, never invent a URL):
{link_lines or "(none available -- omit the related-reading section)"}

FAQ questions to answer:
{faq_lines or "(none provided -- propose 3-6 yourself, grounded in the topic)"}

## Product catalog (use ONLY these [AFFILIATE:key] keys, or none)
{catalog_text}

{_BLUEPRINT_SPEC.format(persona=persona, today=today, disclosure=disclosure_text)}
{_PROSE_RULES}
{_HARD_RULES.format(year=year)}
"""

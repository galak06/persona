"""Pydantic output contracts for the CrewAI strategist/writer pipeline.

Two `Task(output_pydantic=...)` targets, one per pipeline stage (see
`lib.crew.writer.agent`):

  - `ContentBrief` -- the strategist's structured brief for one
    `content_ideas` row.
  - `WrittenPost` -- the writer's full HTML post body, assembled from that
    brief.

Same rationale as `lib.crew.models.ScoutOutput`: CrewAI's `output_pydantic`
requires a `BaseModel`, so nested list fields are wrapped in a top-level
model rather than returned bare.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OutlineSection(BaseModel):
    """One H2/H3 heading in the strategist's proposed post structure."""

    heading: str = Field(description="The section's heading text (no markdown/HTML markup).")
    level: str = Field(default="H2", description="'H2' or 'H3'.")
    notes: str = Field(description="One line on what this section should cover.")


class InternalLinkCandidate(BaseModel):
    """One real internal-link target -- title/url MUST come from the brand's
    real `site_content_cache.json`, never invented by the LLM (enforced
    separately in `lib.crew.writer.context.sanitize_internal_links`)."""

    title: str
    url: str


class ContentBrief(BaseModel):
    """The strategist agent's structured output for one content idea."""

    suggested_title: str = Field(description="Publishable title, including the current year.")
    outline: list[OutlineSection] = Field(default_factory=list)
    primary_keyword: str
    secondary_keywords: list[str] = Field(default_factory=list)
    internal_link_candidates: list[InternalLinkCandidate] = Field(default_factory=list)
    faq_questions: list[str] = Field(
        default_factory=list, description="3-6 real questions this post should answer."
    )
    mascot_angle: str = Field(
        description="How the brand's mascot/voice fits THIS specific topic -- grounded in the "
        "idea's own reasoning and the brand voice guide, not generic."
    )
    reference_category: str = Field(
        default="",
        description="Which of the brand's reference-photo collections best matches this "
        "scene -- copy one value verbatim from the list supplied in the task, or leave empty.",
    )


class FaqPair(BaseModel):
    """One FAQ question/answer pair as actually written (feeds both the
    visible H3 FAQ section and `lib.blog_jsonld.faq_jsonld`)."""

    question: str
    answer: str


class WrittenPost(BaseModel):
    """The writer agent's structured output: the full assembled post body."""

    title: str
    body_html: str = Field(description="Full post body as WordPress-ready HTML.")
    word_count: int = Field(description="Actual word count of body_html's visible text.")
    faq_pairs: list[FaqPair] = Field(default_factory=list)
    internal_links_used: list[InternalLinkCandidate] = Field(default_factory=list)
    affiliate_keys_used: list[str] = Field(
        default_factory=list,
        description="Product catalog keys referenced via [AFFILIATE:key] placeholders, if any.",
    )

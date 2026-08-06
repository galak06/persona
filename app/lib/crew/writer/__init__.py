"""CrewAI-based content strategist + writer pipeline.

Public surface:
    from lib.crew.writer import (
        ContentBrief, WrittenPost, ContentDraftResult,
        strategist_and_writer, select_idea,
        build_content_brief, write_post_from_brief, assemble_final_html,
    )

Takes one existing `content_ideas` row (`status='publish'`, picked by
`select_idea`) and produces a full, assembled WordPress-ready HTML post:
strategist stage -> `ContentBrief`, writer stage -> `WrittenPost`, then
`assemble_final_html` injects `lib.blog_jsonld` schema and resolves
`[AFFILIATE:key]` placeholders via `lib.affiliate_resolver.resolve_html`.

Never writes to `content_ideas`, WordPress, or anywhere else -- read-only
against the idea row, output is returned/printed for review. See
`lib.crew.writer.orchestrator` for the full pipeline, `lib.crew.writer.agent`
for CrewAI `Agent`/`Task` construction, `lib.crew.writer.context` /
`lib.crew.writer.prompts` for context/prompt building, and
`lib.crew.writer.models` for the structured output contracts.
"""

from lib.crew.writer.models import ContentBrief, WrittenPost
from lib.crew.writer.orchestrator import (
    ContentDraftResult,
    assemble_final_html,
    build_content_brief,
    select_idea,
    strategist_and_writer,
    write_post_from_brief,
)

__all__ = [
    "ContentBrief",
    "ContentDraftResult",
    "WrittenPost",
    "assemble_final_html",
    "build_content_brief",
    "select_idea",
    "strategist_and_writer",
    "write_post_from_brief",
]

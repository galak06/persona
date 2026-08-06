"""Task-description ("prompt") builder for the quality-editor agent.

Split out of `lib.crew.editor.agent` purely for file-size discipline, same
role `lib.crew.writer.prompts` plays for the strategist/writer pair.
"""

from __future__ import annotations

from lib.crew.writer.models import OutlineSection

_RUBRIC = """\
## Scoring rubric (100 points total, score holistically within each band)
1. Coherence & flow (0-25) -- does the post read as one continuous, logically \
ordered piece? Do sentences and sections connect, or does it feel stitched together?
2. On-brand voice (0-25) -- does it match the brand voice guide below throughout \
(persona, tone, first-person mascot framing)? Generic, voiceless corporate copy scores low.
3. Structural completeness (0-25) -- does the body actually cover every section in the \
outline below, in a reasonable order, with real content (not a placeholder or a one-line \
stub)? Missing or thin sections score low.
4. Absence of stray LLM artifacts (0-25) -- see the hard rule below. ANY instance found \
caps this sub-score at 0, regardless of how good the surrounding prose is.

## Hard rule: stray LLM artifacts (report in `stray_artifacts`, not just `issues`)
Flag ANY text that reads like the model thinking out loud instead of finished, \
publication-ready prose. This includes, but is not limited to:
- An unfinished self-correction mid-sentence, e.g. "...it only works when you're in \
Wi-Fi range? Actually, that's not accurate -- let me clarify in the review below."
- Meta-commentary about the writing process itself, e.g. "let me clarify", "to clarify \
further", "as I mentioned above I should note", "let me revise that".
- A visible internal question-then-answer to itself, e.g. "...right? Actually, no --".
- Any first-person reference to the act of writing/drafting/revising THIS post (not the \
brand persona's own dog-owner voice -- that's fine; this is specifically about the model \
narrating its own text generation).
Quote the EXACT offending sentence or clause verbatim in `stray_artifacts` -- do not \
paraphrase it, so a human can find and verify it in the source text.
"""


def build_editor_task_description(
    *,
    title: str,
    body_html: str,
    voice: str,
    outline: list[OutlineSection],
    min_score: float,
) -> str:
    """The editor agent's full prompt: one assembled post -> a `QualityVerdict`."""
    outline_lines = (
        "\n".join(f"- [{s.level}] {s.heading} -- {s.notes}" for s in outline)
        if outline
        else "(no outline available -- judge structural completeness against the "
        "standard blueprint: hook, problem statement, core content, FAQ, related "
        "reading, closing pick)"
    )
    return f"""You are reviewing ONE fully-assembled blog post before it becomes a \
WordPress draft. No human will read it after you -- your verdict is the only gate.

## Brand voice (the post should match this throughout)
{voice or "(no voice guide available -- judge voice consistency internally instead)"}

## Brief outline this post was supposed to follow
{outline_lines}

## Post title
{title}

## Post body (full HTML as written)
{body_html}

{_RUBRIC}

## Your job
Score the post 0-100 per the rubric above. Set `passed` to your own true/false \
judgment (score >= {min_score:.0f} AND no stray artifacts). List concrete `issues` for \
any coherence/voice/structure problems (empty list if none). List verbatim \
`stray_artifacts` for any instance of the hard rule above (empty list if none).
"""

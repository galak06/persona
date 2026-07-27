"""Markdown-structure-aware paragraph unwrapping.

LLM-generated voice prose (intro, Nalla's verdict, FAQ answers) is frequently
hard-wrapped at ~70 columns. python-markdown (with the "extra" / "sane_lists" /
"smarty" extensions) preserves a single newline inside a paragraph as a literal
``\\n`` inside the ``<p>``; WordPress ``wpautop`` then turns each of those into a
``<br />``, producing ragged mid-sentence line breaks in published posts.

``unwrap_paragraphs`` joins consecutive plain-prose lines back into a single
line (one space between them) WITHOUT touching markdown structure — blank-line
paragraph breaks, ATX headings, list items, blockquotes, tables, HTML blocks,
and fenced code blocks are all emitted verbatim.

Known limitation: wrapped *continuation* lines INSIDE a single list item are not
rejoined. A soft-wrapped second physical line of a ``- item`` would be treated
as its own prose run. Our seed lists are always one-line-per-item, so this case
does not arise in practice.
"""

from __future__ import annotations

import re

# Structure-line detectors. A line matching any of these is emitted as-is and
# flushes any pending prose run.
_HEADING_RE = re.compile(r"^#{1,6}\s")
_LIST_ITEM_RE = re.compile(r"^\s*([-*+]|\d+\.)\s")
_BLOCKQUOTE_RE = re.compile(r"^>")
_TABLE_RE = re.compile(r"^\|")
_HTML_BLOCK_RE = re.compile(r"^\s*<")
_FENCE_RE = re.compile(r"^\s*```")
_WS_RUN_RE = re.compile(r"\s+")


def _is_structure(line: str) -> bool:
    """True if the line is a markdown structure line (not joinable prose)."""
    if line.strip() == "":
        return True
    return bool(
        _HEADING_RE.match(line)
        or _LIST_ITEM_RE.match(line)
        or _BLOCKQUOTE_RE.match(line)
        or _TABLE_RE.match(line)
        or _HTML_BLOCK_RE.match(line)
    )


def unwrap_paragraphs(md: str) -> str:
    """Join hard-wrapped prose lines while preserving markdown structure.

    Consecutive plain-prose lines (not blank, heading, list item, blockquote,
    table, HTML block, or inside a fenced code block) are collapsed into a
    single line separated by one space. Everything else is emitted verbatim.
    """
    out: list[str] = []
    prose_run: list[str] = []
    in_fence = False

    def flush() -> None:
        if prose_run:
            collapsed = " ".join(
                _WS_RUN_RE.sub(" ", part.strip()) for part in prose_run
            )
            out.append(collapsed)
            prose_run.clear()

    for line in md.split("\n"):
        if _FENCE_RE.match(line):
            # Toggle fence state; the fence delimiter itself is structure.
            flush()
            in_fence = not in_fence
            out.append(line)
            continue

        if in_fence:
            # Never modify anything inside a fenced code block.
            out.append(line)
            continue

        if _is_structure(line):
            flush()
            out.append(line)
        else:
            prose_run.append(line)

    flush()
    return "\n".join(out)

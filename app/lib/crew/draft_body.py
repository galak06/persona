"""WordPress-presentation HTML composition for CrewAI-drafted posts.

Split out of `lib.crew.draft` purely for file-size discipline -- this is the
"how to build the theme-expected wrapped HTML" concern, independent of "how
to POST it to WordPress and record the result" (which stays in `draft.py`).
"""

from __future__ import annotations

import re
import unicodedata

_JSONLD_SCRIPT_MARKER = '<script type="application/ld+json">'


def slugify(title: str) -> str:
    """Lowercase, hyphenated slug for the uploaded media's filename.

    A local copy of the same small pattern several other modules in this
    repo already implement independently (`lib.groups_db.models.slugify`,
    `recipe-publisher/publishers/wordpress_ideas.py::_slugify`, ...) rather
    than a shared cross-module import.
    """
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", text).strip("-") or "post"


def escape_attr(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )


def split_body_and_jsonld(html: str) -> tuple[str, str]:
    """Split already-assembled HTML at the first JSON-LD `<script>` tag.

    `lib.crew.writer.orchestrator.assemble_final_html` always appends
    `lib.blog_jsonld.render_jsonld_blocks`' output at the very end, so
    everything from the first JSON-LD script tag onward is schema, never
    body prose -- returns `(body, "")` if there's no JSON-LD present at all.
    """
    idx = html.find(_JSONLD_SCRIPT_MARKER)
    if idx == -1:
        return html, ""
    return html[:idx].rstrip(), html[idx:].rstrip()


def build_wrapped_body(body_html: str, *, media_source_url: str, alt_text: str) -> str:
    """Wrap the body in the `dff-idea-body` container the live theme expects,
    with an inline hero `<figure>` before it whenever a real uploaded image
    exists (omitted entirely when the image step failed). Exact wrapper shape
    confirmed against a real live post (id 4114).

    Any trailing JSON-LD is deliberately kept OUTSIDE the wrapper div, as a
    sibling appended after its closing `</div>` -- nesting a `<script>` tag
    inside a div that `wpautop` auto-paragraphs produces stray/unbalanced
    HTML that collapses the theme's page grid (narrow content column, huge
    blank gap, sidebar flung to the right -- a real, reproduced bug on post
    4147). Matches the proven, working pattern in
    `recipe-publisher/publishers/wordpress.py::_compose_body`, which composes
    the wrapped body first and appends schema blocks after it, never inside.
    """
    content, jsonld_tail = split_body_and_jsonld(body_html)
    hero = ""
    if media_source_url:
        hero = (
            f'<figure><img src="{media_source_url}" '
            f'alt="{escape_attr(alt_text)}" class="wp-post-image"></figure>'
        )
    wrapped = (
        f'<div class="dff-idea-body" style="max-width:720px;margin:0 auto;">{hero}{content}</div>'
    )
    if jsonld_tail:
        return f"{wrapped}\n\n{jsonld_tail}"
    return wrapped

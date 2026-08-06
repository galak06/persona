"""Generalized (non-recipe) schema.org JSON-LD helpers for blog posts.

Generalizes the pattern in `recipe-publisher/publishers/wordpress.py`'s
`_recipe_jsonld()` / `_faq_jsonld()` (read-only reference -- that module is
recipe-specific, lives in a different subproject, and is not imported here)
for arbitrary, non-recipe blog posts: `Article`/`BlogPosting` schema instead
of `Recipe` schema, and an `faq_jsonld()` that takes plain question/answer
pairs instead of a `Recipe` dataclass's `.faq` list.

Pure, I/O-free -- no network calls, no file reads, nothing brand-specific
hardcoded (caller supplies author/publisher). Mirrors the recipe module's
`_jsonld_block()` helper as `jsonld_script_block()`.
"""

from __future__ import annotations

import json
from typing import Any

QaPair = tuple[str, str]


def article_jsonld(
    title: str,
    author: str,
    date_published: str,
    *,
    description: str | None = None,
    image_url: str | None = None,
    publisher_name: str | None = None,
    publisher_url: str | None = None,
    date_modified: str | None = None,
    article_type: str = "BlogPosting",
) -> dict[str, Any]:
    """Build a schema.org `Article`/`BlogPosting` JSON-LD dict.

    `title`/`author`/`date_published` are required -- Google's rich-result
    guidance for Article/BlogPosting treats these as the minimum for
    eligibility. `date_published` is an ISO 8601 date/datetime string;
    `date_modified` defaults to `date_published` when omitted (same
    convention `_recipe_jsonld` uses for `datePublished`).
    """
    schema: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": article_type,
        "headline": title,
        "author": {"@type": "Person", "name": author},
        "datePublished": date_published,
        "dateModified": date_modified or date_published,
    }
    if description:
        schema["description"] = description
    if image_url:
        schema["image"] = [image_url]
    if publisher_name:
        publisher: dict[str, Any] = {"@type": "Organization", "name": publisher_name}
        if publisher_url:
            publisher["url"] = publisher_url
        schema["publisher"] = publisher
    return schema


def faq_jsonld(qa_pairs: list[QaPair]) -> dict[str, Any] | None:
    """Emit `FAQPage` schema from arbitrary (question, answer) pairs.

    Returns `None` if there are no non-empty pairs -- same "conditional,
    optional injection" behavior as `_faq_jsonld()`, so callers can drop the
    section entirely rather than emit an empty `mainEntity` array. Pairs with
    a blank question or answer (after stripping) are silently dropped rather
    than raising, since a partially-blank pair is a caller data issue, not a
    reason to fail the whole post.
    """
    pairs = [
        (q.strip(), a.strip())
        for q, a in qa_pairs
        if isinstance(q, str) and isinstance(a, str) and q.strip() and a.strip()
    ]
    if not pairs:
        return None
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {"@type": "Answer", "text": answer},
            }
            for question, answer in pairs
        ],
    }


def jsonld_script_block(schema: dict[str, Any]) -> str:
    """Render one schema dict as an embeddable `<script type="application/ld+json">` block."""
    return f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>'


def render_jsonld_blocks(*schemas: dict[str, Any] | None) -> str:
    """Render multiple schema dicts as `<script>` blocks, skipping `None`s.

    Joined with a BLANK line (``\\n\\n``), not a single newline -- WordPress's
    `wpautop()` does not treat `<script>` as a recognized block-level tag
    (unlike `div`/`table`/`blockquote`), so a single newline lets it get
    swallowed into an auto-generated `<p>` (`</script></p>` in the rendered
    output -- a real, reproduced bug). Matches the proven, working separator
    convention in `recipe-publisher/publishers/wordpress.py::_compose_body()`
    (``"\\n\\n".join(schema_blocks)``) exactly.

    `None` entries are the expected shape of an optional block (e.g.
    `faq_jsonld()` returning `None` for a post with no FAQ pairs) -- callers
    pass them straight through instead of filtering first.
    """
    blocks = [jsonld_script_block(schema) for schema in schemas if schema is not None]
    return "\n\n".join(blocks)

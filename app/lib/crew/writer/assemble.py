"""Final-HTML assembly for a written post.

The last step before a draft becomes WordPress-ready: fix the writer's
invalid nesting, strip invented same-site links, inject `lib.blog_jsonld`
schema, resolve `[AFFILIATE:key]` placeholders into real Amazon URLs, and
enforce the FTC/Associates surface on every Amazon link in the result.

Split out of `orchestrator` -- which owns *running the crews* -- because this
is a separate job (turning a `WrittenPost` into publishable HTML) and the
combined module had grown past this repo's 300-line limit. Re-exported from
`lib.crew.writer`, so every existing import is unchanged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lib.affiliate_resolver import ProductEntry, resolve_html
from lib.blog_jsonld import article_jsonld, faq_jsonld, render_jsonld_blocks
from lib.crew.writer.context import (
    internal_link_candidates_from_cache,
    read_brand_config,
    strip_unapproved_internal_links,
    unwrap_lists_from_paragraphs,
)
from lib.crew.writer.models import WrittenPost
from lib.gsc_scout import load_site_content_cache


def assemble_final_html(
    brand_dir: Path,
    written_post: WrittenPost,
    *,
    catalog: dict[str, ProductEntry] | None = None,
    drop_unknown_affiliates: bool = False,
) -> str:
    """Unwrap any `<ul>`/`<ol>` the writer nested inside a `<p>` (invalid
    HTML5 that corrupts WordPress's rendering, see
    `lib.crew.writer.context.unwrap_lists_from_paragraphs`), strip any
    invented same-site link straight from the HTML body, inject JSON-LD
    (`lib.blog_jsonld`), resolve `[AFFILIATE:key]` placeholders
    (`lib.affiliate_resolver.resolve_html`, with this post's `ascsubtag`
    campaign id), and make every Amazon link compliant
    (`lib.crew.products.compliance.enforce_affiliate_compliance`).

    Deliberately does NOT catch `AffiliateResolverError` -- see module
    docstring. Callers that want a clean CLI message catch it themselves.

    `drop_unknown_affiliates` forwards to `resolve_html`: on, an invented
    catalog key costs one link instead of the whole draft. Off by default
    so this stays a caller's decision, not a silent library-wide loosening.
    """
    config = read_brand_config(brand_dir)
    site = config.get("site", {}) if isinstance(config, dict) else {}
    author = str(site.get("brand_persona") or "").strip() or "Staff Writer"
    publisher_name = str(site.get("name") or brand_dir.name)
    publisher_url = str(site.get("url") or "") or None
    today = datetime.now(UTC).date().isoformat()

    body_html = unwrap_lists_from_paragraphs(written_post.body_html)
    if publisher_url:
        link_candidates = internal_link_candidates_from_cache(load_site_content_cache(brand_dir))
        allowed_urls = {c.url for c in link_candidates}
        body_html = strip_unapproved_internal_links(
            body_html, site_url=publisher_url, allowed_urls=allowed_urls
        )

    article = article_jsonld(
        written_post.title,
        author,
        today,
        publisher_name=publisher_name,
        publisher_url=publisher_url,
    )
    faq = faq_jsonld([(pair.question, pair.answer) for pair in written_post.faq_pairs])
    jsonld_block = render_jsonld_blocks(article, faq)
    # Blank line, not a single "\n", before the JSON-LD -- wpautop() doesn't
    # recognize <script> as a block-level tag, so a single newline lets it
    # get swallowed into an auto-generated <p> (reproduced: "</script></p>"
    # in a real rendered post). Matches _compose_body()'s proven separator.
    body_with_schema = f"{body_html.rstrip()}\n\n{jsonld_block}\n"

    # Merged-pool default: the selector may pick recipe-catalog keys too.
    from lib.crew.products import (  # runtime import: avoids cycle
        blog_campaign_id,
        enforce_affiliate_compliance,
        load_candidate_pool,
        slug_for,
    )

    catalog = catalog if catalog is not None else load_candidate_pool(brand_dir)
    # Derived from the TITLE, never a WordPress `slug` field: WP reports
    # `slug: ""` for an unpublished draft and this pipeline only ever creates
    # drafts, which is how two live posts ended up with a dead
    # `ascsubtag=blog-`. `written_post.title` (not the brief's) is what
    # `create_wp_draft` posts, so it is what WP will slugify.
    slug = slug_for(written_post.title)
    resolved = resolve_html(
        body_with_schema,
        catalog=catalog,
        campaign_id=blog_campaign_id(slug),
        drop_unknown=drop_unknown_affiliates,
    )
    # Last word on compliance: `resolve_html` only substitutes URLs, so the
    # anchors the writer wrote around them still have no `rel`/`target`, and
    # an Amazon link the writer typed out in full carries no campaign id at
    # all. Idempotent, so the picks block's already-correct links are a no-op.
    return enforce_affiliate_compliance(resolved, slug)

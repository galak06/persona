"""WordPress-side helpers for the one-time blog product-block backfill.

Split out of `scripts/backfill_blog_product_blocks.py` purely for
file-size discipline: the script owns the run loop, the CLI and the HTTP
client; this module owns the pure(ish) pieces that are worth testing on
their own -- what a published post looks like, how posts are enumerated,
which posts are untouchable, and what gets rendered for each mode.

The Elementor guard lives here because it is the single most damaging
thing to get wrong: writing `post_content` on an Elementor-built post is
invisible on the front end (Elementor renders `_elementor_data` instead)
and can strand the real body. It is therefore deliberately conservative --
anything that isn't provably empty counts as Elementor.
"""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from lib.affiliate_resolver import ProductEntry, _has_disclosure
from lib.crew.products.block import (
    BLOG_HEADING,
    BLOG_INTRO,
    has_blog_block,
    insert_or_replace_blog_block,
    render_blog_block,
    strip_blog_block,
)
from lib.observability import get_logger
from lib.recipe_products import block_renderer as recipe_block

logger = get_logger(__name__)

#: Wording for a post that already carries a recipe block -- reuse the
#: recipe renderer's own intro so a refreshed recipe post reads unchanged.
RECIPE_HEADING = "Our Pick: Tools Used in This Recipe"
RECIPE_INTRO: str = recipe_block._INTRO

#: Built from the recipe renderer's PUBLIC markers rather than importing its
#: private compiled pattern.
_RECIPE_BLOCK_RE = re.compile(
    re.escape(recipe_block.BLOCK_MARKER_OPEN) + r".*?" + re.escape(recipe_block.BLOCK_MARKER_CLOSE),
    re.DOTALL,
)

MODE_INSERT = "insert"
MODE_REFRESH_BLOG = "refresh-blog"
MODE_REFRESH_RECIPE = "refresh-recipe"
MODE_SKIP_RECIPE = "skip-has-recipe-block"


@dataclass(frozen=True)
class WpPost:
    """One published post, as returned by `wp/v2/posts?context=edit`."""

    id: int
    slug: str
    title: str
    content: str
    meta: Mapping[str, Any]


def to_post(row: Mapping[str, Any]) -> WpPost:
    """One `wp/v2/posts` row -> `WpPost` (raw content, `context=edit`)."""
    content = row.get("content") or {}
    title = row.get("title") or {}
    return WpPost(
        id=int(row["id"]),
        slug=str(row.get("slug", "")),
        title=str(title.get("raw") or title.get("rendered") or ""),
        content=str(content.get("raw") or ""),
        meta=row.get("meta") or {},
    )


def fetch_posts_by_id(client: httpx.Client, post_ids: list[int]) -> list[WpPost]:
    """Specific POSTs by id, at ANY status -- drafts included.

    The sweep deliberately only sees `status=publish` (a backfill exists to
    fix posts already facing readers). Addressing a draft is the opposite
    case and has to be asked for explicitly, by id: a draft the writer just
    produced can be given its product block before it ever goes live, instead
    of being published bare and swept afterwards. Ids rather than a
    `status=draft` sweep so the blast radius is exactly what the caller named.
    """
    posts: list[WpPost] = []
    for post_id in post_ids:
        resp = client.get(f"/wp-json/wp/v2/posts/{post_id}", params={"context": "edit"})
        resp.raise_for_status()
        posts.append(to_post(resp.json()))
    logger.info("blog_backfill_posts_fetched_by_id", count=len(posts), ids=post_ids)
    return posts


def fetch_published_posts(client: httpx.Client, *, slug: str | None = None) -> list[WpPost]:
    """Every published POST -- never `/pages` -- following `X-WP-TotalPages`."""
    posts: list[WpPost] = []
    page = 1
    while True:
        params: dict[str, Any] = {
            "per_page": 100,
            "page": page,
            "status": "publish",
            "context": "edit",
        }
        if slug:
            params["slug"] = slug
        resp = client.get("/wp-json/wp/v2/posts", params=params)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        posts.extend(to_post(row) for row in rows)
        total_pages = int(resp.headers.get("X-WP-TotalPages") or 1)
        if page >= total_pages:
            break
        page += 1
    logger.info("blog_backfill_posts_fetched", count=len(posts))
    return posts


def is_elementor(meta: Mapping[str, Any]) -> bool:
    """True when the post is Elementor-built and must NOT be written to.

    Conservative by design: only an empty/whitespace `_elementor_data`
    (what `_ELEMENTOR_CLEAR_META` leaves behind) counts as "not Elementor".
    """
    data = meta.get("_elementor_data")
    if isinstance(data, str):
        if data.strip():
            return True
    elif data:
        return True
    return bool(meta.get("_elementor_edit_mode"))


def mode_for(content: str, *, include_recipe_blocks: bool) -> str:
    """Which block (if any) this post already has, hence what to do with it."""
    if recipe_block.has_block(content):
        return MODE_REFRESH_RECIPE if include_recipe_blocks else MODE_SKIP_RECIPE
    return MODE_REFRESH_BLOG if has_blog_block(content) else MODE_INSERT


def prose_without_blocks(content: str) -> str:
    """`content` with every generated picks block (blog AND recipe) removed."""
    return _RECIPE_BLOCK_RE.sub("", strip_blog_block(content))


def render_for_mode(
    post: WpPost, products: Sequence[ProductEntry], mode: str, tag: str
) -> tuple[str, str]:
    """`(block_html, new_post_content)` for `mode`.

    `refresh-recipe` renders the block wrapped in the RECIPE markers and
    replaces between them, so a recipe post never ends up with two blocks;
    it also keeps the recipe heading/intro so the post reads unchanged.

    The disclosure decision reads the post's OWN prose (any previously
    generated block stripped first), which is what makes a re-run converge
    on byte-identical content instead of toggling the disclosure on and off.
    """
    include_disclosure = not _has_disclosure(prose_without_blocks(post.content))
    if mode == MODE_REFRESH_RECIPE:
        block = render_blog_block(
            products,
            post.slug,
            associates_tag=tag,
            heading=RECIPE_HEADING,
            intro=RECIPE_INTRO,
            include_disclosure=include_disclosure,
            open_marker=recipe_block.BLOCK_MARKER_OPEN,
            close_marker=recipe_block.BLOCK_MARKER_CLOSE,
        )
        return block, recipe_block.insert_or_replace_block(post.content, block)
    block = render_blog_block(
        products,
        post.slug,
        associates_tag=tag,
        heading=BLOG_HEADING,
        intro=BLOG_INTRO,
        include_disclosure=include_disclosure,
    )
    return block, insert_or_replace_blog_block(post.content, block)


def write_backup(path: Path, content: str) -> None:
    """Atomic same-directory temp file + `os.replace` (see `lib.io.jsonio`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise

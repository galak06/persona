"""Update the /blog/ WordPress page with a self-contained HTML+CSS+JS block
that shows all non-recipe posts with category tabs and client-side search.

Posts are fetched server-side (authenticated) and embedded as static HTML,
so the page works even when the WP REST API is not publicly accessible.
Re-run this script whenever new blog posts are published.

Usage:
    python scripts/update_blog_page.py [--dry-run]

--dry-run: print the generated HTML to stdout, do NOT call the WP REST API.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TypedDict

# Make social-automation/ importable so lib.sessions can be resolved.
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.sessions.wp_client import wp_client  # noqa: E402

_SETTINGS_PATH = Path(__file__).parent.parent.parent / ".claude" / "settings.local.json"
_RECIPES_CATEGORY_ID = 41


class BlogPost(TypedDict):
    title: str
    excerpt: str
    link: str
    date: str
    image: str
    categories: list[int]


def load_credentials() -> None:
    """Read .claude/settings.local.json and export WP env vars."""
    raw = json.loads(_SETTINGS_PATH.read_text())
    env_dict: dict[str, str] = raw.get("env", {})
    for key in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        if key in env_dict:
            os.environ[key] = env_dict[key]


def _strip_tags(text: str) -> str:
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _parse_image(rendered: str) -> str:
    """Extract first image URL from JSON-LD schema or img src in rendered HTML."""
    m = re.search(r'"image"\s*:\s*\[?"(https?://[^"]+)"', rendered)
    if m:
        return m.group(1)
    m2 = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', rendered)
    return m2.group(1) if m2 else ""


def _format_date(date_str: str) -> str:
    """Parse ISO date string and return formatted date like 'May 12, 2026'."""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", ""))
        # %-d works on macOS/Linux for no-padding day; strip leading zero manually
        day = dt.day
        return dt.strftime(f"%b {day}, %Y")
    except (ValueError, AttributeError):
        return date_str


def find_blog_page_id() -> int:
    """Find the blog page ID via WP settings (page_for_posts) or slug fallback."""
    with wp_client() as client:
        resp = client.get("/wp-json/wp/v2/settings")
        if resp.status_code == 200:
            data = resp.json()
            page_for_posts = data.get("page_for_posts", 0)
            if page_for_posts and int(page_for_posts) > 0:
                pid = int(page_for_posts)
                print(f"Found blog page ID via settings: {pid}")
                return pid

        # Fallback: look for a page with slug 'blog'
        resp2 = client.get(
            "/wp-json/wp/v2/pages",
            params={"slug": "blog", "_fields": "id,title"},
        )
        if resp2.status_code == 200:
            pages = resp2.json()
            if pages:
                pid = int(pages[0]["id"])
                print(f"Found blog page ID via slug: {pid}")
                return pid

    raise RuntimeError("Could not find blog page ID from WP settings or slug 'blog'.")


def fetch_categories() -> dict[int, str]:
    """Fetch all non-empty categories excluding Recipes (id=41)."""
    categories: dict[int, str] = {}
    with wp_client() as client:
        resp = client.get(
            "/wp-json/wp/v2/categories",
            params={"_fields": "id,name,slug", "per_page": 100, "hide_empty": "true"},
        )
        resp.raise_for_status()
        for cat in resp.json():
            cat_id = int(cat["id"])
            if cat_id != _RECIPES_CATEGORY_ID:
                categories[cat_id] = html_lib.unescape(cat["name"])
    print(f"Found {len(categories)} non-recipe categories: {list(categories.values())}")
    return categories


def fetch_blog_posts() -> list[BlogPost]:
    """Fetch all published non-recipe posts using authenticated httpx client."""
    results: list[BlogPost] = []
    page = 1
    with wp_client() as client:
        while True:
            resp = client.get(
                "/wp-json/wp/v2/posts",
                params={
                    "categories_exclude": _RECIPES_CATEGORY_ID,
                    "per_page": 100,
                    "page": page,
                    "status": "publish",
                    "context": "edit",
                    "_fields": "id,title,excerpt,link,date,categories,meta,content",
                },
            )
            if resp.status_code == 400:
                break  # no more pages
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for post in batch:
                rendered = (post.get("content") or {}).get("rendered", "")
                # Prefer FIFU meta, fall back to JSON-LD / img parse
                image = (post.get("meta") or {}).get("fifu_image_url", "") or _parse_image(rendered)
                raw_excerpt = _strip_tags((post.get("excerpt") or {}).get("rendered", ""))
                excerpt = raw_excerpt[:130] + ("…" if len(raw_excerpt) > 130 else "")
                results.append(
                    BlogPost(
                        title=_strip_tags((post.get("title") or {}).get("rendered", "")),
                        excerpt=excerpt,
                        link=post.get("link", ""),
                        date=_format_date(post.get("date", "")),
                        image=image,
                        categories=post.get("categories", []),
                    )
                )
            if len(batch) < 100:
                break
            page += 1
    return results


def _render_card(post: BlogPost, categories: dict[int, str]) -> str:
    """Render a single blog post card as HTML."""
    title_esc = html_lib.escape(post["title"])
    title_lower = html_lib.escape(post["title"].lower())
    link = html_lib.escape(post["link"])
    date_esc = html_lib.escape(post["date"])
    excerpt_esc = html_lib.escape(post["excerpt"])

    # Build pipe-delimited category IDs for JS filtering: |12|5|
    cat_ids_str = "|".join(str(cid) for cid in post["categories"])
    data_cats = f"|{cat_ids_str}|" if cat_ids_str else "|"

    # Primary category name for badge
    primary_cat_name = ""
    for cid in post["categories"]:
        if cid in categories:
            primary_cat_name = categories[cid]
            break

    badge_html = (
        f'<span class="dff-cat-badge">{html_lib.escape(primary_cat_name)}</span>'
        if primary_cat_name
        else ""
    )

    if post["image"]:
        img_section = (
            f'<div class="dff-bcard-img" style="background-image:url(\'{html_lib.escape(post["image"])}\');">'
            f"{badge_html}"
            f"</div>"
        )
    else:
        img_section = (
            f'<div class="dff-bcard-no-img">\U0001F43E'
            f"{badge_html}"
            f"</div>"
        )

    return (
        f'<div class="dff-bcard" role="listitem"'
        f' data-href="{link}"'
        f' data-title="{title_lower}"'
        f' data-cats="{data_cats}">'
        f"{img_section}"
        f'<div class="dff-bcard-body">'
        f'<p class="dff-bcard-date">{date_esc}</p>'
        f'<div class="dff-bcard-title"><a href="{link}">{title_esc}</a></div>'
        f'<p class="dff-bcard-excerpt">{excerpt_esc}</p>'
        f'<a href="{link}" class="dff-bcard-cta">Get the Full Guide</a>'
        f"</div>"
        f"</div>"
    )


def build_html(posts: list[BlogPost], categories: dict[int, str]) -> str:
    """Return full page HTML with embedded static blog post cards and JS.

    Wrapped in a Gutenberg raw-HTML block to bypass wpautop.
    """
    cards_html = "\n".join(_render_card(p, categories) for p in posts)

    # Build category filter buttons
    cat_buttons = '\n      <button class="dff-cat active" data-cat="all">All</button>'
    for cat_id, cat_name in sorted(categories.items(), key=lambda x: x[1]):
        cat_name_esc = html_lib.escape(cat_name)
        cat_buttons += f'\n      <button class="dff-cat" data-cat="|{cat_id}|">{cat_name_esc}</button>'

    inner = f"""\
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,700;1,9..144,400&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">

<div id="dff-blog-index">

  <div class="dff-blog-intro">
    <p>Honest guides for dog owners — food, gear, health, and life with Nalla.</p>
  </div>

  <div class="dff-blog-controls">
    <div class="dff-cats" role="tablist">{cat_buttons}
    </div>
    <div class="dff-search-wrap">
      <input id="dff-blog-search" type="search" placeholder="Search posts…" autocomplete="off" />
    </div>
  </div>

  <div id="dff-blog-grid" role="list">
{cards_html}
  </div>

  <p id="dff-blog-empty" hidden>No posts found.</p>

</div>

<style>
  #dff-blog-index {{
    max-width: 1200px; margin: 0 auto; padding: 0 16px;
    font-family: 'DM Sans', sans-serif;
  }}
  .dff-blog-intro {{
    margin-bottom: 28px;
    font-size: 1.1rem; color: #555; font-style: italic;
  }}
  .dff-blog-controls {{
    display: flex; align-items: center; gap: 16px;
    flex-wrap: wrap; margin-bottom: 32px;
  }}
  .dff-cats {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .dff-cat {{
    border: none; cursor: pointer;
    background: #f0f0f0; color: #444;
    font-family: 'DM Sans', sans-serif; font-size: 0.85rem; font-weight: 500;
    padding: 6px 16px; border-radius: 20px;
    transition: background 0.2s, color 0.2s;
  }}
  .dff-cat:hover {{ background: #ffe0d8; color: #ff5f42; }}
  .dff-cat.active {{ background: var(--ast-global-color-0, #ff5f42); color: #fff; }}
  #dff-blog-search {{
    padding: 8px 14px; font-size: 0.9rem; font-family: 'DM Sans', sans-serif;
    border: 2px solid #e0e0e0; border-radius: 20px;
    outline: none; min-width: 200px; box-sizing: border-box;
    transition: border-color 0.2s;
  }}
  #dff-blog-search:focus {{ border-color: var(--ast-global-color-0, #ff5f42); }}
  #dff-blog-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 28px;
  }}
  @media (max-width: 768px) {{ #dff-blog-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
  @media (max-width: 480px) {{ #dff-blog-grid {{ grid-template-columns: 1fr; }} }}

  .dff-bcard {{
    background: #fff; border-radius: 12px; overflow: hidden;
    border: 1px solid #ebebeb;
    transition: transform 0.22s ease, box-shadow 0.22s ease;
    cursor: pointer;
  }}
  .dff-bcard:hover {{
    transform: translateY(-3px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.10);
  }}
  .dff-bcard[hidden] {{ display: none !important; }}
  .dff-bcard-img {{
    width: 100%; height: 200px;
    background-size: cover; background-position: center; background-repeat: no-repeat;
    background-color: #f5f5f5;
    position: relative;
  }}
  .dff-bcard-no-img {{
    width: 100%; height: 200px; background: #f5f5f5;
    display: flex; align-items: center; justify-content: center;
    font-size: 3rem; position: relative;
  }}
  .dff-cat-badge {{
    position: absolute; top: 12px; left: 12px;
    background: var(--ast-global-color-0, #ff5f42); color: #fff;
    font-family: 'DM Sans', sans-serif; font-size: 0.7rem; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase;
    padding: 3px 10px; border-radius: 20px;
    pointer-events: none;
  }}
  .dff-bcard-body {{ padding: 18px 18px 20px; }}
  .dff-bcard-date {{
    font-size: 0.78rem; color: #999; font-weight: 500;
    margin: 0 0 6px; text-transform: uppercase; letter-spacing: 0.04em;
  }}
  .dff-bcard-title {{
    font-family: 'Fraunces', serif; font-size: 1.2rem; font-weight: 700;
    line-height: 1.3; margin: 0 0 10px; color: #1a1a1a;
    transition: color 0.18s;
  }}
  .dff-bcard:hover .dff-bcard-title {{ color: var(--ast-global-color-0, #ff5f42); }}
  .dff-bcard-title a {{ text-decoration: none; color: inherit; }}
  .dff-bcard-excerpt {{
    font-size: 0.9rem; color: #666; line-height: 1.6;
    margin: 0 0 14px;
  }}
  .dff-bcard-cta {{
    display: inline-block;
    font-family: 'DM Sans', sans-serif; font-size: 0.85rem; font-weight: 600;
    color: #fff; background: var(--ast-global-color-0, #ff5f42);
    padding: 8px 18px; border-radius: 100px; text-decoration: none;
    transition: opacity 0.18s;
  }}
  .dff-bcard-cta:hover {{ opacity: 0.88; }}
  #dff-blog-empty {{ text-align: center; padding: 48px; color: #888; font-size: 1rem; }}
</style>

<script>
(function () {{
  var grid = document.getElementById('dff-blog-grid');
  var searchEl = document.getElementById('dff-blog-search');
  var emptyEl = document.getElementById('dff-blog-empty');
  var catBtns = Array.prototype.slice.call(document.querySelectorAll('.dff-cat'));
  if (!grid) return;

  var cards = Array.prototype.slice.call(grid.querySelectorAll('.dff-bcard'));
  var activeCat = 'all';
  var activeQuery = '';

  // Full-card click
  grid.addEventListener('click', function (e) {{
    var card = e.target.closest('.dff-bcard');
    if (card && e.target.tagName !== 'A') {{
      window.location.href = card.getAttribute('data-href');
    }}
  }});

  function applyFilter() {{
    var q = activeQuery.trim().toLowerCase();
    var visible = 0;
    cards.forEach(function (card) {{
      var catMatch = activeCat === 'all' || card.getAttribute('data-cats').indexOf(activeCat) !== -1;
      var textMatch = !q || card.getAttribute('data-title').indexOf(q) !== -1;
      var show = catMatch && textMatch;
      card.hidden = !show;
      if (show) visible++;
    }});
    emptyEl.hidden = visible > 0;
  }}

  catBtns.forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      catBtns.forEach(function (b) {{ b.classList.remove('active'); }});
      btn.classList.add('active');
      activeCat = btn.getAttribute('data-cat');
      applyFilter();
    }});
  }});

  searchEl.addEventListener('input', function () {{
    activeQuery = searchEl.value;
    applyFilter();
  }});
}})();
</script>"""

    return f"<!-- wp:html -->\n{inner}\n<!-- /wp:html -->"


def patch_page(page_id: int, html: str) -> str:
    """PATCH the blog page with the generated HTML. Returns live URL."""
    payload = {
        "content": html,
        "status": "publish",
        "meta": {
            "_elementor_edit_mode": "",
            "_elementor_template_type": "",
            "_elementor_version": "",
            "_elementor_data": "",
            "_elementor_css": "",
            "_elementor_page_assets": "",
        },
    }
    with wp_client() as client:
        resp = client.patch(f"/wp-json/wp/v2/pages/{page_id}", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"PATCH /pages/{page_id} failed: {resp.status_code} {resp.text[:400]}"
            )
        data = resp.json()
        url = data.get("link", f"(page ID {page_id})")
        print(f"Page updated with {len(html):,} bytes of HTML.")
        print(f"Live URL: {url}")
        return url


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy the /blog/ index page to WordPress."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated HTML to stdout without calling the WP REST API.",
    )
    args = parser.parse_args()

    load_credentials()

    if not args.dry_run:
        print("Finding blog page ID...")
        page_id = find_blog_page_id()

    print("Fetching categories from WordPress...")
    categories = fetch_categories()

    print("Fetching blog posts from WordPress...")
    posts = fetch_blog_posts()
    print(f"Found {len(posts)} published blog posts (excluding recipes).")

    html = build_html(posts, categories)

    if args.dry_run:
        print(html)
        sys.exit(0)

    patch_page(page_id, html)


if __name__ == "__main__":
    main()

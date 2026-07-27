"""Update the /recipes/ WordPress page (ID 3314) with a self-contained
HTML+CSS+JS block that shows ALL recipes with live client-side search.

Recipes are fetched server-side (authenticated) and embedded as static HTML,
so the page works even when the WP REST API is not publicly accessible.
Re-run this script whenever new recipes are published.

Usage:
    python scripts/update_recipes_page.py [--dry-run]

--dry-run: print the generated HTML to stdout, do NOT call the WP REST API.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import sys
from pathlib import Path
from typing import TypedDict

# Make social-automation/ importable so lib.sessions can be resolved.
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.sessions.wp_client import wp_client  # noqa: E402

_SETTINGS_PATH = Path(__file__).parent.parent.parent / ".claude" / "settings.local.json"
_PAGE_ID = 3314
_CATEGORY_ID = 41


class RecipeCard(TypedDict):
    title: str
    excerpt: str
    link: str
    image: str
    ingredients: list[str]


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
    # Handle both string form: "image": "https://..." and array form: "image": ["https://..."]
    m = re.search(r'"image"\s*:\s*\[?"(https?://[^"]+)"', rendered)
    return m.group(1) if m else ""


def _parse_ingredients(rendered: str) -> list[str]:
    m = re.search(r'"recipeIngredient"\s*:\s*(\[[\s\S]*?\])', rendered)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return []


def fetch_recipes() -> list[RecipeCard]:
    """Fetch all published posts in the recipes category using authenticated httpx client."""
    results: list[RecipeCard] = []
    page = 1
    with wp_client() as client:
        while True:
            resp = client.get(
                f"/wp-json/wp/v2/posts",
                params={
                    "categories": _CATEGORY_ID,
                    "per_page": 100,
                    "page": page,
                    "status": "publish",
                    "context": "edit",
                    "_fields": "id,title,excerpt,link,content,meta",
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
                # Prefer image from JSON-LD schema; fall back to FIFU meta
                image = _parse_image(rendered) or (post.get("meta") or {}).get("fifu_image_url", "")
                results.append(
                    RecipeCard(
                        title=_strip_tags((post.get("title") or {}).get("rendered", "")),
                        excerpt=_strip_tags((post.get("excerpt") or {}).get("rendered", ""))[:140],
                        link=post.get("link", ""),
                        image=image,
                        ingredients=_parse_ingredients(rendered),
                    )
                )
            if len(batch) < 100:
                break
            page += 1
    return results


def _render_card(recipe: RecipeCard) -> str:
    title_esc = html_lib.escape(recipe["title"])
    ingredients_esc = html_lib.escape("|".join(recipe["ingredients"]).lower())
    title_lower = html_lib.escape(recipe["title"].lower())

    # Use <div> as card container (block-level → wpautop won't mangle it).
    # Use background-image div for the image to prevent FIFU plugin from
    # intercepting <img> src attributes and overwriting alt/title.
    link = html_lib.escape(recipe["link"])
    img_html = (
        f'<div class="dff-card-img" role="img" aria-label="{title_esc}" '
        f'style="background-image:url(\'{html_lib.escape(recipe["image"])}\')"></div>'
        if recipe["image"]
        else '<div class="dff-card-no-img"><span>\U0001F43E</span></div>'
    )
    excerpt_html = (
        f'<p class="dff-excerpt">{html_lib.escape(recipe["excerpt"])}</p>'
        if recipe["excerpt"]
        else ""
    )

    return (
        f'<div class="dff-card" role="listitem" data-href="{link}" '
        f'data-title="{title_lower}" data-ingredients="{ingredients_esc}">'
        f"{img_html}"
        f'<div class="dff-card-body">'
        f'<h3><a href="{link}">{title_esc}</a></h3>'
        f"{excerpt_html}"
        f'<a class="dff-card-cta" href="{link}">Get This Recipe</a>'
        f"</div>"
        f"</div>"
    )


def build_html(recipes: list[RecipeCard]) -> str:
    """Return full page HTML with embedded static recipe cards and client-side search JS.

    Wrapped in a Gutenberg raw-HTML block so WordPress disables wpautop (which
    would otherwise insert stray </p> tags after <img> elements and break the card layout).
    """
    count = len(recipes)
    cards_html = "\n".join(_render_card(r) for r in recipes)

    inner = f"""\
<div id="dff-recipe-index">
  <div class="dff-search-wrap">
    <label for="dff-search" class="screen-reader-text">Search recipes</label>
    <input id="dff-search" type="search" placeholder="Search by name or ingredient…" autocomplete="off" />
    <p id="dff-count" aria-live="polite">{count} recipe{"s" if count != 1 else ""}</p>
  </div>
  <div id="dff-grid" role="list">
{cards_html}
  </div>
</div>

<style>
  #dff-recipe-index {{ max-width: 1200px; margin: 0 auto; padding: 0 16px; }}
  .dff-search-wrap {{ margin-bottom: 32px; }}
  #dff-search {{
    width: 100%; max-width: 480px; padding: 12px 16px;
    font-size: 1rem; border: 2px solid #e0e0e0; border-radius: 6px;
    outline: none; box-sizing: border-box;
  }}
  #dff-search:focus {{ border-color: var(--ast-global-color-0, #ff5f42); }}
  #dff-count {{ color: #666; font-size: 0.9rem; margin-top: 8px; }}
  #dff-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 24px;
  }}
  @media (max-width: 768px) {{ #dff-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
  @media (max-width: 480px) {{ #dff-grid {{ grid-template-columns: 1fr; }} }}
  .dff-card {{
    border-radius: 8px; overflow: hidden;
    border: 1px solid #e8e8e8;
    transition: box-shadow 0.2s, transform 0.2s;
    background: #fff; cursor: pointer;
  }}
  .dff-card:hover {{ box-shadow: 0 4px 16px rgba(0,0,0,0.12); transform: translateY(-2px); }}
  .dff-card[hidden] {{ display: none !important; }}
  .dff-card-body a {{ text-decoration: none; color: inherit; }}
  .dff-card-body a:hover {{ color: var(--ast-global-color-0, #ff5f42); }}
  .dff-card-img {{
    width: 100%; height: 200px; display: block;
    background-size: cover; background-position: center; background-repeat: no-repeat;
    background-color: #f5f5f5;
  }}
  .dff-card-no-img {{
    width: 100%; height: 200px; background: #f5f5f5;
    display: flex; align-items: center; justify-content: center;
  }}
  .dff-card-no-img span {{ font-size: 3rem; }}
  .dff-card-body {{ padding: 16px; }}
  .dff-card-body h3 {{ margin: 0 0 8px; font-size: 1.1rem; line-height: 1.3; color: #222; }}
  .dff-excerpt {{ margin: 0; font-size: 0.9rem; color: #555; line-height: 1.5; }}
  .dff-no-results {{ grid-column: 1/-1; text-align: center; padding: 40px; color: #888; }}
  .dff-card-cta {{
    display: inline-block; margin-top: 12px;
    padding: 8px 20px; background: var(--ast-global-color-0, #ff5f42);
    color: #fff !important; border-radius: 100px; font-size: 0.85rem; font-weight: 600;
    text-decoration: none !important; transition: opacity 0.15s;
  }}
  .dff-card-cta:hover {{ opacity: 0.87; color: #fff !important; }}
</style>

<script>
(function () {{
  var searchEl = document.getElementById('dff-search');
  var countEl = document.getElementById('dff-count');
  var grid = document.getElementById('dff-grid');
  var noResults = null;

  if (!searchEl || !grid) return;

  var cards = Array.prototype.slice.call(grid.querySelectorAll('.dff-card'));

  // Full-card click: navigate to href stored on the div.
  grid.addEventListener('click', function (e) {{
    var card = e.target.closest('.dff-card');
    if (card && e.target.tagName !== 'A') {{
      window.location.href = card.getAttribute('data-href');
    }}
  }});

  function filter(query) {{
    var q = query.trim().toLowerCase();
    var visible = 0;

    cards.forEach(function (card) {{
      var match = !q
        || card.getAttribute('data-title').indexOf(q) !== -1
        || card.getAttribute('data-ingredients').indexOf(q) !== -1;
      card.hidden = !match;
      if (match) visible++;
    }});

    countEl.textContent = visible + ' recipe' + (visible !== 1 ? 's' : '');

    if (noResults) noResults.remove();
    if (visible === 0 && q) {{
      noResults = document.createElement('p');
      noResults.className = 'dff-no-results';
      noResults.textContent = 'No recipes found for "' + query + '".';
      grid.appendChild(noResults);
    }}
  }}

  searchEl.addEventListener('input', function () {{ filter(searchEl.value); }});
}})();
</script>"""

    # Gutenberg raw-HTML block disables wpautop for the entire page,
    # preventing stray </p> tags from breaking card structure.
    return f"<!-- wp:html -->\n{inner}\n<!-- /wp:html -->"


def patch_page(html: str) -> None:
    """PATCH the /recipes/ page (ID 3314) with the generated HTML."""
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
        resp = client.patch(f"/wp-json/wp/v2/pages/{_PAGE_ID}", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"PATCH /pages/{_PAGE_ID} failed: {resp.status_code} {resp.text[:400]}"
            )
        data = resp.json()
        url = data.get("link", f"(page ID {_PAGE_ID})")
        print(f"Page updated with {len(html):,} bytes of HTML.")
        print(f"Live URL: {url}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy the /recipes/ index page to WordPress."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated HTML to stdout without calling the WP REST API.",
    )
    args = parser.parse_args()

    load_credentials()

    print("Fetching recipes from WordPress...")
    recipes = fetch_recipes()
    print(f"Found {len(recipes)} published recipes.")

    html = build_html(recipes)

    if args.dry_run:
        print(html)
        sys.exit(0)

    patch_page(html)


if __name__ == "__main__":
    main()

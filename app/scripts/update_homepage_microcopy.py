"""Modify Elementor JSON on the homepage (page 2101) to update button microcopy.

Changes applied:
  Hero CTA:       "Explore top picks"              → "See What Nalla Recommends"
  Category cards: "Browse →" × 4                   → "Explore X Guides/Tips"
  Blog grid:      read_more_button_text "Read More" → "Get the Full Guide"
  Blog grid:      show_load_more_text  "Load More"  → "Show More Posts"
  Newsletter:     <button>Subscribe</button>        → "Yes, Send Me Recipes!"
  Nalla strip:    shortcode CTA (JS patch)           → "See Everything Nalla Loves"

Usage:
    python scripts/update_homepage_microcopy.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from lib.sessions.wp_client import wp_client  # noqa: E402

_SETTINGS_PATH = Path(__file__).parent.parent.parent / ".claude" / "settings.local.json"
_PAGE_ID = 2101

# Browse → buttons appear in exactly this order in the Elementor tree
_CATEGORY_BROWSE_REPLACEMENTS = [
    "Explore Grooming Guides",
    "Explore Food & Diet Tips",
    "Explore Lifestyle Guides",
    "Explore Training Guides",
]

_NALLA_CTA_JS = """<script>
document.addEventListener('DOMContentLoaded',function(){
  document.querySelectorAll('a').forEach(function(a){
    if(a.textContent.trim()==='See all Nalla-Certified picks →'){
      a.textContent='See Everything Nalla Loves';
    }
  });
});
</script>
"""

# Injected as a new Elementor HTML widget appended to the page.
# Uses MutationObserver so it fires even if Elementor lazy-loads content.
_MICROCOPY_JS_WIDGET_HTML = """\
<script>
(function(){
  var MAP = {
    'Explore top picks': 'See What Nalla Recommends',
    'Browse →': ['Explore Grooming Guides','Explore Food & Diet Tips',
                     'Explore Lifestyle Guides','Explore Training Guides'],
    'Read More': 'Get the Full Guide',
    'Load More': 'Show More Posts',
    'See all Nalla-Certified picks →': 'See Everything Nalla Loves'
  };
  function applyAll(){
    var browseIdx = 0;
    document.querySelectorAll('.elementor-button-text, .eael-load-more-btn, a.eael-post-link').forEach(function(el){
      var t = el.textContent.trim();
      if(MAP[t] && !Array.isArray(MAP[t])){
        el.textContent = MAP[t];
      } else if(t === 'Browse →'){
        var rep = MAP['Browse →'];
        el.textContent = rep[Math.min(browseIdx, rep.length-1)];
        browseIdx++;
      }
    });
  }
  document.addEventListener('DOMContentLoaded', applyAll);
})();
</script>"""

_JS_WIDGET_ID = "dff-microcopy-js"


def load_credentials() -> None:
    raw = json.loads(_SETTINGS_PATH.read_text())
    for key in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"):
        if key in (env := raw.get("env", {})):
            os.environ[key] = env[key]


def _replace_buttons(node: object, browse_count: list[int]) -> int:
    """Recursively walk Elementor JSON and apply all button text replacements.
    Returns total number of replacements made."""
    changes = 0
    if isinstance(node, dict):
        settings = node.get("settings")
        if isinstance(settings, dict):
            # Hero / generic button widget
            if settings.get("text") == "Explore top picks":
                settings["text"] = "See What Nalla Recommends"
                changes += 1
            # Category "Browse →" buttons — replace in order
            if settings.get("text") == "Browse →":
                idx = browse_count[0]
                if idx < len(_CATEGORY_BROWSE_REPLACEMENTS):
                    settings["text"] = _CATEGORY_BROWSE_REPLACEMENTS[idx]
                    browse_count[0] += 1
                    changes += 1
            # EA Post Grid read-more / load-more
            if settings.get("read_more_button_text") == "Read More":
                settings["read_more_button_text"] = "Get the Full Guide"
                changes += 1
            if settings.get("show_load_more_text") == "Load More":
                settings["show_load_more_text"] = "Show More Posts"
                changes += 1
        for v in node.values():
            changes += _replace_buttons(v, browse_count)
    elif isinstance(node, list):
        for item in node:
            changes += _replace_buttons(item, browse_count)
    return changes


def _patch_newsletter_html(html: str) -> tuple[str, int]:
    """Replace Subscribe button text and success callback text in newsletter HTML block."""
    changes = 0
    if ">Subscribe<" in html:
        html = html.replace(">Subscribe<", ">Yes, Send Me Recipes!<", 1)
        changes += 1
    # JS callback after successful subscribe — revert label
    if "btn.text('Subscribe')" in html:
        html = html.replace("btn.text('Subscribe')", "btn.text('Yes, Send Me Recipes!')")
        changes += 1
    # Append Nalla CTA JS patch if not already present
    if "See Everything Nalla Loves" not in html:
        html = html.rstrip() + "\n" + _NALLA_CTA_JS
        changes += 1
    return html, changes


def _inject_microcopy_js_widget(data: list) -> int:
    """Append a custom HTML widget with client-side microcopy JS if not already present.
    The widget bypasses Elementor's rendering cache by running after DOM ready."""
    # Check if already injected
    for el in data:
        if isinstance(el, dict) and el.get("id") == _JS_WIDGET_ID:
            return 0  # already present

    widget = {
        "id": _JS_WIDGET_ID,
        "elType": "widget",
        "widgetType": "html",
        "settings": {"html": _MICROCOPY_JS_WIDGET_HTML},
        "elements": [],
        "isInner": False,
    }
    data.append(widget)
    return 1


def apply_microcopy(data: list) -> tuple[list, int]:
    """Apply all microcopy replacements to parsed Elementor JSON. Returns (data, total_changes)."""
    browse_count = [0]
    total = _replace_buttons(data, browse_count)

    # Newsletter block is a custom HTML widget at top-level index 5
    newsletter_block = data[5]["elements"][0]["settings"]
    if "html" in newsletter_block:
        patched, n = _patch_newsletter_html(newsletter_block["html"])
        newsletter_block["html"] = patched
        total += n

    # Inject client-side JS widget to handle Elementor rendering cache
    total += _inject_microcopy_js_widget(data)

    return data, total


def main() -> None:
    parser = argparse.ArgumentParser(description="Update homepage button microcopy.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_credentials()

    print("Fetching homepage Elementor data...")
    with wp_client() as client:
        resp = client.get(
            f"/wp-json/wp/v2/pages/{_PAGE_ID}",
            params={"context": "edit", "_fields": "meta"},
        )
        resp.raise_for_status()
        meta = resp.json().get("meta", {})
        el_json = meta.get("_elementor_data", "")
        if not el_json:
            print("ERROR: _elementor_data not found in page meta.")
            sys.exit(1)

        data = json.loads(el_json)
        data, total_changes = apply_microcopy(data)
        print(f"{total_changes} replacements made.")

        if args.dry_run:
            print(json.dumps(data, indent=2)[:2000])
            print("(dry-run — no changes written)")
            return

        patched_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        patch_resp = client.patch(
            f"/wp-json/wp/v2/pages/{_PAGE_ID}",
            json={"meta": {"_elementor_data": patched_json}},
        )
        if patch_resp.status_code >= 400:
            raise RuntimeError(
                f"PATCH failed: {patch_resp.status_code} {patch_resp.text[:400]}"
            )
        url = patch_resp.json().get("link", f"(page {_PAGE_ID})")
        print(f"Homepage updated. Live URL: {url}")


if __name__ == "__main__":
    main()

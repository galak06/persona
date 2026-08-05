"""Worker HTML — recipe card HTML builder.

For every recipe that has a hero image but no card HTML yet, render the
Jinja2 card template with all recipe parameters base64-embedded and write
the self-contained HTML to the campaign folder.

Poll predicate (idempotent — fires only after Worker E completes):
    image_created_at truthy  AND  card_html_created_at == ""

On success writes ``post_image_card.html`` to the campaign folder and sets
``card_html_path`` + ``card_html_created_at`` in the DB so the downstream
render worker can poll on ``card_html_created_at``.

    python -m workers.worker_html                    # dry-run plan
    python -m workers.worker_html --apply --limit 1  # build one
    python -m workers.worker_html --health-check     # check deps → 0/1
"""

from __future__ import annotations

import base64
import logging
import os
import sys
from pathlib import Path as _Path

_rp_root = _Path(__file__).resolve().parent.parent
if str(_rp_root) not in sys.path:
    sys.path.insert(0, str(_rp_root))

from jinja2 import Environment, FileSystemLoader, select_autoescape
from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import badge_path, brand_dir, campaign_folder

logger = logging.getLogger("workers.html")

_CARD_TEMPLATE = _Path(__file__).resolve().parent.parent / "templates" / "recipe_card" / "card.html"


def _wp_credentials() -> tuple[str, str]:
    """Return (wp_base_url, basic_auth_token) from settings.local.json."""
    import base64 as _b64
    import json

    settings_path = _Path(__file__).resolve().parents[3] / ".claude" / "settings.local.json"
    with settings_path.open() as f:
        env = json.load(f).get("env", {})
    wp_base = env.get("WP_URL", "").rstrip("/")
    token = _b64.b64encode(f"{env.get('WP_USER','')}:{env.get('WP_APP_PASSWORD','')}".encode()).decode()
    return wp_base, token


def _wp_get(url: str, token: str) -> dict:
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        return json.loads(resp.read())


def _fetch_wp_post(row: RecipeRow, dest: _Path) -> str | None:
    """Fetch WP post title + download featured image to dest.

    Returns the rendered post title (e.g. "Buddy's Cornmeal Crunchers") or None on failure.
    Image download is a best-effort side-effect.
    """
    import urllib.request

    if not row.wp_url:
        return None
    try:
        slug = row.wp_url.rstrip("/").rsplit("/", 1)[-1]
        wp_base, token = _wp_credentials()
        if not wp_base:
            return None

        post_api = f"{wp_base}/wp-json/wp/v2/posts?slug={slug}&_fields=title,featured_media"
        posts = _wp_get(post_api, token)
        if not posts:
            return None

        post = posts[0]
        wp_title: str | None = (post.get("title") or {}).get("rendered") or None

        media_id = post.get("featured_media")
        if media_id and not (dest.exists() and dest.stat().st_size > 0):
            try:
                media = _wp_get(f"{wp_base}/wp-json/wp/v2/media/{media_id}?_fields=source_url", token)
                img_url = media.get("source_url", "")
                if img_url:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    urllib.request.urlretrieve(img_url, dest)  # noqa: S310
                    logger.info("fetched WP image → %s", dest)
            except Exception as img_exc:
                logger.warning("image download failed for %s: %s", row.id, img_exc)

        return wp_title
    except Exception as exc:
        logger.warning("WP post fetch failed for %s: %s", row.id, exc)
        return None


def _targets(repo: RecipeRepository, seeds: list[str], limit: int) -> list[RecipeRow]:
    """Recipes with a WP post or hero image but no card HTML yet."""
    rows = [
        r for r in repo.list_recipes()
        if (r.image_created_at or r.wp_url) and not r.card_html_created_at
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.id)
    return rows[:limit] if limit else rows


_DOG_TIDBITS = [
    ("fact",  "A dog's nose print is as unique as a human fingerprint — no two are alike."),
    ("joke",  "Why did the dog sit in the shade? He didn't want to be a hot dog!"),
    ("fact",  "Dogs can smell about 100,000 times better than humans. Your leftovers have no secrets."),
    ("joke",  "What do you call a frozen dog treat? A pupsicle!"),
    ("fact",  "Dogs dream just like humans do. Scientists confirmed it — REM sleep and all."),
    ("joke",  "Why do dogs run in circles? Because it's too hard to run in squares!"),
    ("fact",  "The Basenji is the only dog that can't bark — it yodels instead."),
    ("joke",  "What do you call a dog magician? A labracadabrador!"),
    ("fact",  "Dogs sweat through their paws, not their skin."),
    ("joke",  "Why did the dog cross the road? To get to the barking lot!"),
    ("fact",  "A dog's heart beats up to 140 times per minute when excited — just like yours at treat time."),
    ("joke",  "What do you call a sleeping dog? A pawnapper!"),
    ("fact",  "Puppies are born blind, deaf, and toothless. Nalla was once that helpless too."),
    ("joke",  "Why don't dogs make good dancers? Because they have two left feet!"),
    ("fact",  "Dogs have three eyelids — the third keeps the eye moist and protected."),
]


def _pick_tidbit(recipe_id: str) -> dict[str, str]:
    idx = int.from_bytes(recipe_id.encode(), "little") % len(_DOG_TIDBITS)
    kind, text = _DOG_TIDBITS[idx]
    label = "🐾 Dog Fact" if kind == "fact" else "😄 Dog Joke"
    return {"label": label, "text": text}


def _make_qr_b64(url: str) -> str:
    import io

    import qrcode
    import qrcode.constants
    from qrcode.image.styledpil import StyledPilImage
    from qrcode.image.styles.moduledrawers import RoundedModuleDrawer

    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        fill_color="#B5651D",
        back_color="#FAF7F0",
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _fmt_time(minutes: int) -> str:
    if not minutes:
        return "30 min"
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60}h {minutes % 60:02d}min".replace(" 00min", "")


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    # ── hero image (local or fetched from WP) ──
    hero_path = campaign_folder(row) / "post_image.jpg"
    if not (hero_path.exists() and hero_path.stat().st_size > 0):
        _fetch_wp_post(row, hero_path)
    food_photo_b64: str | None = None
    if hero_path.exists() and hero_path.stat().st_size > 0:
        food_photo_b64 = base64.b64encode(hero_path.read_bytes()).decode()

    # ── badge ──
    badge_b64: str | None = None
    bp = badge_path()
    if bp:
        badge_b64 = base64.b64encode(_Path(bp).read_bytes()).decode()

    # ── ingredients (up to 7 lines) ──
    ingredients: list[str] = []
    raw = row.generated_content.get("ingredients") or []
    if not raw:
        from generators.seeds import load_seeds
        seed = next((s for s in load_seeds() if s.id == row.id), None)
        raw = seed.ingredients if seed else []
    for ing in raw[:7]:
        ingredients.append(str(ing))

    # ── meta chips ──
    total = row.total_minutes or row.prep_minutes or 0
    meta_chips = [
        {"label": f"⏱ {_fmt_time(total)}", "dark": False},
        {"label": f"🍽 {row.servings}" if row.servings else "🍽 3 servings", "dark": False},
        {"label": "🔥 Easy", "dark": False},
        {"label": "⭐ Nalla's Fave", "dark": True},
    ]

    # ── QR code ──
    qr_b64: str | None = None
    if row.wp_url:
        qr_b64 = _make_qr_b64(row.wp_url)

    # ── tidbit ──
    tidbit = _pick_tidbit(row.id)

    # ── render ──
    env = Environment(
        loader=FileSystemLoader(str(_CARD_TEMPLATE.parent)),
        autoescape=select_autoescape(["html"]),
    )
    html = env.get_template(_CARD_TEMPLATE.name).render(
        recipe_name=row.display_name or row.name,
        ingredients=ingredients,
        meta_chips=meta_chips,
        food_photo_b64=food_photo_b64,
        badge_b64=badge_b64,
        qr_b64=qr_b64,
        tidbit=tidbit,
    )

    # ── write ──
    folder = campaign_folder(row)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "post_image_card.html"
    out.write_text(html, encoding="utf-8")

    brand = brand_dir()
    rel = str(out.relative_to(brand))
    repo.set_card_html(row.id, rel)
    logger.info("card html → %s", out)
    return "html"


def _health() -> bool:
    """BRAND_DIR is set, badge file exists, jinja2 importable, template present."""
    bd = os.environ.get("BRAND_DIR")
    if not bd:
        logger.warning("BRAND_DIR not set")
        return False
    if not badge_path():
        logger.warning("badge not found at %s/images/badge.png", bd)
        return False
    try:
        import jinja2  # noqa: F401
    except ImportError:
        logger.warning("jinja2 not installed")
        return False
    if not _CARD_TEMPLATE.exists():
        logger.warning("card template missing: %s", _CARD_TEMPLATE)
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(
        run_worker(
            "html",
            targets_fn=_targets,
            do_one_fn=_do_one,
            health_fn=_health,
        )
    )

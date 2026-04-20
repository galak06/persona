# recipe-publisher

Generates a dog-friendly recipe in the Nalla's Dad voice and publishes it to WordPress and Instagram. Supports three IG formats:

- **Single image** — `publish_to_instagram()`
- **Carousel** — `publish_carousel_to_instagram()` (2-10 slides from `seeds/carousels/{id}.json`)
- **Reel** — `publish_reel_to_instagram()` (9:16 mp4 composed from 4 slides + Jamendo music bed, driven end-to-end by `social-automation/scripts/content_pipeline.py --stage reel --seed <id>`)

See `SKILL.md` for the invocation contract.

## Install

```bash
cd social-automation/recipe-publisher
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Required secrets

Injected from project secrets — skill does not read `.env` itself.

| Var | Purpose |
|---|---|
| `WP_URL` (or `WP_BASE_URL`) | e.g. `https://dogfoodandfun.com` — project convention takes precedence |
| `WP_USER` (or `WP_APP_PASSWORD_USER`) | WP username with Application Password |
| `WP_APP_PASSWORD` | the 24-char app password (keep spaces) |
| `IG_ACCOUNT_ID` (or `IG_USER_ID`) | numeric IG Business account ID |
| `FB_PAGE_TOKEN` (or `IG_GRAPH_ACCESS_TOKEN`) | long-lived Page token — IG uses the same FB token |
| `FB_APP_ID` / `FB_APP_SECRET` | required for auto token refresh |
| `GEMINI_API_KEY` | voice + Imagen / Nano Pro images |
| `ANTHROPIC_API_KEY` | alternative voice provider (if credits available) |
| `JAMENDO_CLIENT_ID` | Reel music bed (required only for `--stage reel`) |

Optional:

| Var | Default | Purpose |
|---|---|---|
| `VOICE_PROVIDER` | auto | `gemini` \| `anthropic` — force one voice backend |
| `RECIPE_MODEL` | `claude-sonnet-4-6` | override the Anthropic recipe-generation model |
| `GEMINI_VOICE_MODEL` | `gemini-2.5-flash` | override the Gemini voice model |
| `IMAGE_PROVIDER` | chain | `nano_pro` \| `imagen_fast` \| `imagen_standard` \| `pexels` \| `fallback` |
| `FALLBACK_IMAGE_URL` | — | used when `IMAGE_PROVIDER=fallback` |
| `RECIPE_REPORT_DIR` | `/mnt/dogfoodandfun` | where the daily report is written |
| `OVERLAY_HEADLINE_FONT` / `OVERLAY_SUBCOPY_FONT` | Mac system fonts | override carousel overlay fonts |
| `LOG_LEVEL` | `INFO` | |

## Run

```bash
# Dry-run with a specific topic (safe default)
python recipe_publisher.py --topic "Beef liver training treats"

# Actually publish
python recipe_publisher.py --topic "Beef liver training treats" --no-dry-run

# Weekly autonomous run (no topic → pulls next from state/ideas_queue.json)
python recipe_publisher.py --no-dry-run
```

## Test

```bash
pytest -q
```

Tests are hermetic — `respx` mocks every HTTP call. No network required.

## Wire into launchd (Mac)

Add a plist under `~/Library/LaunchAgents/com.dogfoodandfun.recipe-publisher.plist` that calls `run_with_watchdog.py recipe-publisher` on your Sunday 09:00 IST schedule. The watchdog already handles:
- environment injection from the project secrets
- timeout enforcement
- `last_run.json` rollup alongside the other scanners
- failure notification

## File tree

```
recipe-publisher/
├── SKILL.md                    # invocation contract
├── README.md                   # this file
├── requirements.txt
├── recipe_publisher.py         # orchestrator (entrypoint for single/carousel)
├── generators/
│   ├── recipe.py               # voice via Gemini or Anthropic tool_use → typed Recipe
│   ├── recipe_from_seed.py     # Anthropic tool schema + caption rules
│   ├── recipe_from_seed_gemini.py  # Gemini function-calling path
│   ├── seeds.py                # seed loader + topic matcher
│   ├── image.py                # Nano Pro → Imagen → Pexels → static fallback chain
│   ├── carousel.py             # 4-slide generator, applies follow badge + CTA ribbon
│   ├── text_overlay.py         # PIL text rendering (headline, subcopy, badge, ribbon)
│   ├── reel.py                 # 9:16 mp4 composition (ffmpeg xfade + audio mix)
│   ├── music.py                # Jamendo API client (search + download mp3)
│   └── narration.py            # Optional macOS `say` voiceover (swap for ElevenLabs/Gemini TTS)
├── publishers/
│   ├── wordpress.py            # WP REST + SureRank meta + Recipe JSON-LD + video upload
│   └── instagram.py            # single image / carousel / REELS — Graph API + token refresh
├── prompts/
│   ├── recipe_system.md        # Nalla's Dad voice (refine post-audit)
│   └── ig_caption.md           # IG caption rules (hook + bullets + CTA + hashtags)
├── seeds/
│   ├── seeds.json              # recipe seeds (ingredients, steps, tags)
│   └── carousels/              # per-seed slide configs (4 slides, 9:16 prompts + overlays)
├── state/
│   ├── last_run.json           # heartbeat
│   ├── published_recipes.json  # dedup cache
│   └── ideas_queue.json        # upcoming topics
├── tests/
│   ├── test_wordpress.py
│   ├── test_instagram.py       # covers single image + REELS paths
│   ├── test_recipe_validator.py # caption structure rules
│   ├── test_reel.py            # pad-to-reel + ffmpeg smoke test (gated by RUN_FFMPEG_TESTS=1)
│   └── test_text_overlay.py    # follow badge + CTA ribbon pixel checks
└── _audit/                     # drop audit artifacts here (gitignored)
```

## Conversion overlays (built in)

Every carousel / Reel ships with two conversion hooks so nothing manual is needed per post:

- **Slide 1 (hero) — follow badge**: rounded-pill `@dogfoodandfun` in the top-right. First impression brands the account ID.
- **Slide 4 (final) — CTA ribbon**: full-width terracotta bar at the bottom, `FULL RECIPE → DOGFOODANDFUN.COM`. Last thing a viewer reads before leaving.

Both rendered by `generators/text_overlay.py` (`apply_follow_badge`, `apply_site_cta_ribbon`) and auto-wired in `carousel.py` based on `slide.key`.

## IG caption rules (enforced)

Captions follow a strict structure — `recipe.py::_validate` hard-rejects anything that doesn't match:

1. Hook (first 125 chars, feed-truncation-safe)
2. Three bullet facts (lines starting with `•`)
3. Comment-gated CTA (`Comment RECIPE and I'll DM you the link.`) — keyword in UPPERCASE
4. One specific question
5. 8–12 hashtags including `#nallasdad` and `#dogfoodandfun`

Full rules in `prompts/ig_caption.md`.

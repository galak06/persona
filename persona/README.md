# Persona

Social media automation engine for [dogfoodandfun.com](https://dogfoodandfun.com). Runs engagement, content publishing, and moderation workflows under a brand voice — on Instagram, Facebook, WordPress, Pinterest, and TikTok.

Multi-brand, Docker-ready.

---

## Status

| Feature | Status |
|---|---|
| **IG Scanner + Comments** | ✅ Production — single-pass, Gemini agent |
| **FB Scanner + Comments** | ❌ Parked — needs single-pass rewrite (#46) |
| **Recipe → WP → Social pipeline** | ⚠️ Workers built, never run `--apply` live (#45) |
| **Pinterest** | ⚠️ Trial-blocked — awaiting API approval (#47) |
| **TikTok** | ⚠️ Scout only — publishing incomplete (#48) |
| **Content ideation** | ⚠️ Workers exist, not activated (#51) |
| **Monitoring / alerting** | ❌ None (#53) |

Tests: **767 pass**, 13 fail (FB, parked), 8 errors (postgres not running), 120 skipped.

---

## Architecture

### IG engagement (the working pipeline)

```
ig_scan.py ──single pass──▶ scan hashtags → score → like → agent draft → post
                                  │                    │
                                  ▼                    ▼
                           keyword heuristic    Gemini 2.5 Flash
                           (comment_generator)  {engage, comment, reason}
```

- **Entrypoint:** `scripts/ig_scan.py`
- **Core:** `lib/engagement/pipeline.py` (orchestration), `lib/engagement/post_processor.py` (one-post visit), `lib/engagement/inline_comment.py` (agent draft + post)
- **Agent:** Gemini 2.5 Flash via `lib/gemini_client.py` — one structured call, self-approves (no human gate). Temp 0.7, `thinkingBudget=0`.
- **Session:** Playwright browser session at `$BRAND_DIR/state/instagram_session.json`

### Recipe publishing pipeline (built, dormant)

```
recipes.db ──▶ worker_wp_pdf ──▶ worker_image ──▶ worker_reel ──▶ worker_publish
                  (WP+PDF)       (carousel slides)  (compose MP4)   (IG/FB/Pinterest)
```

Each worker polls the DB independently, idempotent. Workers at `recipe-publisher/workers/`.

### Stack

```
lib/           Core library (engagement, config, rate limiter, dedup, OAuth, Gemini client)
scripts/       CLI entrypoints (ig_scan, fb_scan, fb_group_scout, publish_prepared, …)
api/           FastAPI on :5001 (brands, recipes, engagements, schedule, OAuth)
frontend/      React UI on :3000 (dashboard, recipes, groups, schedule, published)
recipe-publisher/  Recipe → social content pipeline (workers, templates, generators)
tests/         767 passing (pytest)
```

---

## Quickstart

### 1. Clone and configure

```bash
git clone https://github.com/galak06/persona.git
cd persona

# Copy the example config for your brand
cp config.example.json dogfoodandfun/config.json
# Edit with your brand details

# Set required env vars (or use .claude/settings.local.json)
export BRAND_DIR=/Users/you/Projects/dogfoodandfun/dogfoodandfun
export DATABASE_URL=postgresql://persona:persona@localhost:5555/persona
```

### 2. Install dependencies

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 3. Start services

```bash
# API + worker + Redis + frontend
docker compose up

# Or locally without Docker:
./start.sh
```

Services:
- `api` → FastAPI on port 5001
- `worker` → cron-scheduled automation
- `redis` → task queue and rate limiter
- `frontend` → React UI on port 3000

### 4. Log in to platforms

Login must happen on your host machine with a visible browser (the Docker worker runs headless):

```bash
export PYTHONPATH="$(pwd)"
export BRAND_DIR="$(pwd)/dogfoodandfun"
export PLAYWRIGHT_HEADLESS=0    # forces a visible browser

python scripts/ig_login.py
python scripts/fb_login.py
```

Each opens Chromium. Log in (2FA, recaptcha — all fine), session saves to `$BRAND_DIR/state/{instagram,facebook}_session.json`. Verify:

```bash
python scripts/ig_scan.py --health-check
```

---

## Key Entrypoints

### Engagement

| Script | What it does |
|---|---|
| `scripts/ig_scan.py` | Single-pass IG: scan hashtags → score → like → agent comment |
| `scripts/fb_scan.py` | FB scan + queue (parked — needs single-pass rewrite) |
| `scripts/fb_comment.py` | FB comment drainer (parked) |
| `scripts/fb_group_scout.py` | Find + join new FB groups |
| `scripts/fb_group_post.py` | Publish link posts to joined FB groups |
| `scripts/wp_scan.py` | Moderate held WP comments |

### Publishing

| Script | What it does |
|---|---|
| `scripts/publish_prepared.py` | Publish recipe → IG reel / FB reel / FB page post / Pinterest |
| `recipe-publisher/workers/worker_wp_pdf.py` | Recipe → WP draft + PDF card |
| `recipe-publisher/workers/worker_image.py` | Recipe → carousel slides + reel frames |
| `recipe-publisher/workers/worker_reel.py` | Recipe → compose MP4 reel |
| `recipe-publisher/workers/worker_publish.py` | Recipe → publish to social platforms |

### Content

| Script | What it does |
|---|---|
| `scripts/daily_wp_draft.py` | Generate daily WP draft |
| `scripts/refresh_keyword_research.py` | Keyword clustering |
| `scripts/refresh_trends_only.py` | Trend signals refresh |

### Operations

| Script | What it does |
|---|---|
| `scripts/onboard_brand.py` | Provision a new brand |
| `scripts/regenerate_plists.py` | Rebuild launchd plists from profiles |
| `scripts/status.py` | System health overview |

---

## Environment Variables

Key variables (full list in `.claude/settings.local.json` or `.env`):

| Variable | Purpose |
|---|---|
| `BRAND_DIR` | Path to brand data directory |
| `DATABASE_URL` | Postgres connection (most tables) |
| `GEMINI_API_KEY` | Gemini API for comment drafting |
| `ANTHROPIC_API_KEY` | Claude API (optional, for content gen) |
| `WP_URL` / `WP_USER` / `WP_APP_PASSWORD` | WordPress REST API |
| `FB_APP_ID` / `FB_APP_SECRET` | Facebook OAuth app |
| `FB_PAGE_ID` / `FB_PAGE_TOKEN` | Facebook page for posting |
| `IG_ACCOUNT_ID` | Instagram business account |
| `PINTEREST_ACCESS_TOKEN` | Pinterest API |
| `TIKTOK_PRODUCTION_CLIENT_KEY` / `…_SECRET` | TikTok API |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Recipes + ideas + OAuth storage |

---

## Project Structure

```
persona/
├── api/                FastAPI routes (brands, recipes, engagements, schedule, OAuth)
├── lib/                Core library
│   ├── engagement/     Pipeline, post processor, inline comment, like step, queueing
│   ├── fb/             Facebook adapters (session, comment post)
│   ├── ig/             Instagram adapters
│   ├── gemini_client.py   Gemini API client (agentic drafting)
│   ├── reply_drafter.py   Voice rules + no-fabrication guardrail
│   ├── draft_helper.py    Shared draft→validate→retry core
│   └── groups_db/      FB groups SQLite DB
├── scripts/            CLI entrypoints (one per flow)
├── recipe-publisher/   Recipe → social content pipeline
│   ├── workers/        Independent DB-polling workers (A→B→C→D chain)
│   ├── templates/      Recipe card + page HTML templates
│   └── scripts/        One-off utilities (migration, backfill, repair)
├── frontend/           React dashboard (Vite)
├── tests/              900 tests (767 pass)
├── profiles/           Flow definitions (schedule, rate limits)
├── tools/              Profile builder, launchd plist generator
├── docker-compose.yml
└── start.sh
```

---

## License

MIT

# Getting Started with Persona

Five steps from zero to your first automated IG like + comment.

---

## Step 1 — Clone and install

```bash
git clone https://github.com/galak06/persona.git
cd persona/app
pip install -r requirements.txt
playwright install chromium
```

---

## Step 2 — Create your brand directory

This is where all your private data lives (config, sessions, state). It's
separate from the repo so it never gets committed.

```bash
mkdir -p ~/my-brand/data/config
mkdir -p ~/my-brand/state/sessions

cp config.example.json ~/my-brand/config.json
cp data/instagram_accounts.csv.example ~/my-brand/data/config/instagram_accounts.csv
cp data/config/brand_facts.example.md ~/my-brand/data/config/brand_facts.md
```

Edit `~/my-brand/config.json`:
- `site.name` — your brand name
- `site.url` — your website
- `site.mascot_name` — your mascot or persona name
- `site.brand_persona` — how you describe yourself (e.g. "Buddy's Dad")
- `social_channels.instagram.ig_username` — your IG handle (no @)

Edit `~/my-brand/data/config/instagram_accounts.csv`:
- Add the hashtags you want to monitor (see the example for column format)

Edit `~/my-brand/data/config/brand_facts.md`:
- Fill in TRUE facts about your brand/mascot — the AI uses these to ground comments

---

## Step 3 — Configure environment

```bash
cp .env.example .env
```

Required vars in `.env`:

| Variable | Where to get it |
|---|---|
| `BRAND_DIR` | Path to your brand directory, e.g. `/Users/yourname/my-brand` |
| `PERSONA_BRAND` | Short ID for Redis namespace, e.g. `mybrand` |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) — free tier works |
| `WP_URL` | Your WordPress site URL |
| `WP_USER` | Your WP username |
| `WP_APP_PASSWORD` | WP Admin → Users → Profile → Application Passwords |
| `TELEGRAM_BOT_TOKEN` | Optional — needed for approval notifications |
| `TELEGRAM_CHAT_ID` | Optional — your Telegram chat ID |
| `REDIS_URL` | Default: `redis://localhost:6379` |

---

## Step 4 — Log in to Instagram and Facebook

These scripts open a real browser window. Log in normally (including 2FA).
The session is saved and reused by all workers — you only do this once.

```bash
export BRAND_DIR=~/my-brand
python scripts/login.py ig   # saves to $BRAND_DIR/state/sessions/instagram_session.json
python scripts/login.py fb   # saves to $BRAND_DIR/state/sessions/facebook_session.json
```

Sessions last until Instagram/Facebook invalidates them (usually weeks to months).
Re-run the login scripts if workers start reporting session expired errors.

---

## Step 5 — Verify and run

```bash
# Health check — confirms session + config are valid
python scripts/ig_pipeline.py --health-check

# Dry run — walks hashtags, scores posts, prints comment drafts (no likes or posts)
python scripts/ig_pipeline.py --dry-run

# Live run — likes qualifying posts, queues candidates, posts comments
python scripts/ig_pipeline.py
```

---

## Running the full stack (API + UI)

```bash
docker compose up
# or locally:
./start.sh
```

Open `http://localhost:3000` — the approval UI where you review and approve
all queued comments before they go live.

---

## Daily automation (macOS launchd)

Generate launchd plist files for all workers:

```bash
python tools/launchd_plists.py --brand-dir ~/my-brand
```

This writes plists to `~/Library/LaunchAgents/` configured to run each worker
on its schedule. Load them with:

```bash
launchctl load ~/Library/LaunchAgents/com.persona.ig-pipeline.plist
```

---

## Troubleshooting

**Session expired** — re-run `login.py ig` or `login.py fb`

**No posts queued** — lower `content_analysis.relevance_threshold` in config.json
(try 0.50 instead of 0.70 to start, raise it once you see the queue filling)

**Comment drafts failing voice validation** — fill in `brand_facts.md` with
more true statements; the AI needs grounding to write specific comments

**Rate limit hit** — the daily caps in config.json are conservative by default;
Instagram's real limit is ~100 likes/day and ~20 comments/day, but starting
low avoids triggering their bot detection

---

## Keeping the stack current

Containers bake the code in at build time, so a running stack does not follow
your checkout. Two scripts cover this:

| | |
|---|---|
| `./scripts/rebuild.sh [services]` | Builds and swaps containers from the checkout **as it is**. Both compose files, `--force-recreate`, verifies the swap by container age. |
| `./scripts/sync.sh [services]` | Merges `origin/main` first, then calls `rebuild.sh`, then **proves** the running containers are on that commit. |

Use `sync.sh` for a deploy, `rebuild.sh` when you deliberately want to build
uncommitted local work.

```bash
cd app
./scripts/sync.sh --check      # is the stack current? exits non-zero if not
./scripts/sync.sh              # merge origin/main, rebuild, verify
./scripts/sync.sh --no-pull    # rebuild the checkout as-is, still verified
```

`--check` changes nothing and exits non-zero when stale, so it works as a
cron guard or health check.

### Why the proof step exists

`rebuild.sh` answers *"did the containers get swapped?"* — not *"is the running
code current?"* A container built from a checkout that is behind `main` passes
every check it makes.

That is not hypothetical. On 2026-09-01 all three app containers were rebuilt
successfully and kept serving code with a known credential leak, because the
checkout was four commits behind. `sync.sh` closes that gap by baking the
commit into the image as `PERSONA_GIT_SHA` and reading it back out of the
running container.

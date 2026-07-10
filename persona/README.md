# Persona

Open-source social media automation framework. Run automated engagement, content publishing, and moderation workflows under your own brand voice — on Facebook, Instagram, and WordPress.

Multi-brand, Docker-ready, and MCP-enabled.

---

## What It Does

Persona runs a set of workers that:

- Scan Facebook groups and Instagram hashtags for relevant posts
- Score and queue high-signal posts for brand-voice engagement
- Draft, validate, and Telegram-approve comments and replies before posting
- Publish recipe/content carousels to Instagram and Facebook
- Moderate WordPress comments — trash spam, queue the rest for approval
- Manage OAuth tokens and session auth for all platforms

All actions are rate-limited, logged, and require human approval before execution.

---

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌──────────────────┐
│   Workers   │───▶│  Redis Queue │───▶│  FastAPI (5001)  │
│  (cron/CLI) │    │              │    │  Approval API    │
└─────────────┘    └──────────────┘    └────────┬─────────┘
                                                │
                   ┌──────────────┐    ┌────────▼─────────┐
                   │   Telegram   │◀───│  Frontend (3000) │
                   │   Approval   │    │  React UI        │
                   └──────────────┘    └──────────────────┘
```

---

## Quickstart

### 1. Clone and configure

```bash
git clone https://github.com/galak06/persona.git
cd persona
cp .env.example .env
cp config.example.json $BRAND_DIR/config.json   # edit with your brand details
```

### 2. Set required env vars

```bash
export BRAND_DIR=/path/to/your/brand-dir   # brand data, state, and config live here
export PERSONA_BRAND=mybrand               # Redis namespace prefix
```

### 3. Run with Docker Compose

```bash
docker compose up
```

Services:
- `api` → FastAPI on port 5001
- `worker` → cron-scheduled automation workers
- `redis` → task queue and rate limiter
- `frontend` → React approval UI on port 3000

Optional OpenTelemetry tracing:
```bash
docker compose --profile tracing up
```

### 4. Run locally (without Docker)

```bash
uv sync          # or: pip install -r requirements.txt
./start.sh       # starts API + frontend dev server
```

---

## Authentication

### Facebook & Instagram — session cookies

Persona uses Playwright with saved browser sessions. Log in once manually, save the session, and workers reuse it.

```bash
python scripts/fb_login.py        # opens browser, saves session to $BRAND_DIR/state/sessions/
python scripts/ig_login.py
```

Session files are stored in `$BRAND_DIR/state/sessions/` and gitignored.

### Facebook — OAuth 2.0 (for page posting)

For publishing to a Facebook Page (not just groups), use the OAuth flow:

```bash
# Start the API, then visit:
http://localhost:5001/api/v1/oauth/facebook
```

This exchanges a short-lived token for a 60-day user token, then a non-expiring page token. Tokens are stored in Supabase (or JSON fallback in `$BRAND_DIR/state/oauth_tokens/`).

### WordPress — Application Password

Set in `.env`:

```
WP_URL=https://your-site.com
WP_USER=your-wp-username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

Generate the application password in WordPress under **Users → Profile → Application Passwords**. Used for uploading media and publishing posts via the REST API.

---

## Brand Configuration

All brand-specific values live in `$BRAND_DIR/config.json`. Copy and edit the example:

```json
{
  "site": {
    "name": "My Brand",
    "url": "https://mybrand.com",
    "mascot_name": "Buddy",
    "brand_persona": "Buddy's Dad"
  },
  "paths": {
    "data_dir": "/path/to/brand-dir/data"
  }
}
```

Multiple brands are supported — set `PERSONA_BRAND` and `BRAND_DIR` per brand. Redis keys are namespaced as `persona:<brand>:<worker>:tasks`.

---

## Workers

| Worker | What it does | Trigger |
|---|---|---|
| `site-analyzer` | Crawls brand RSS, caches recent posts for comment context | Daily |
| `fb-scanner` | Scans Facebook groups, scores posts, queues candidates | Daily |
| `ig-scanner` | Scans Instagram hashtags, likes + queues top posts | Daily |
| `wp-comment-handler` | Moderates held WP comments, queues for approval | Daily |
| `comment-composer` | Drafts brand-voice comments, validates, Telegram-approves, posts | Daily |
| `reply-follower` | Revisits our FB comments, drafts + posts threaded replies | On demand |
| `fb-group-scout` | Finds new groups to join, presents for approval | Monthly |
| `fb-group-publisher` | Publishes content to Facebook groups | On demand |
| `recipe-publisher` | Full recipe → carousel → IG/FB/Pinterest/WP pipeline | On demand |
| `content-ideator` | Generates content ideas from trending signals | Weekly |
| `auto-drafter` | Drafts engagement comments when no template matches | Daily |

---

## MCP Server

Persona exposes all API routes as MCP tools, letting Claude (or any MCP client) control the approval queue, trigger workers, and manage tokens.

```bash
python mcp_server.py                      # stdio (local)
python mcp_server.py --transport sse      # SSE (remote)
```

The `.mcp.json` file auto-registers it for Claude Code:

```json
{
  "mcpServers": {
    "persona": {
      "command": "python",
      "args": ["mcp_server.py"],
      "env": { "PERSONA_API_URL": "http://localhost:5001" }
    }
  }
}
```

Available tools: `list_pending`, `approve_item`, `reject_item`, `get_item`, `list_workers`, `trigger_worker`, `get_worker_log`, `get_activity`, `list_fb_groups`.

---

## Environment Variables

See `.env.example` for the full list. Key variables:

| Variable | Description |
|---|---|
| `BRAND_DIR` | Path to brand data directory |
| `PERSONA_BRAND` | Redis namespace prefix |
| `WP_URL` / `WP_USER` / `WP_APP_PASSWORD` | WordPress REST API credentials |
| `FB_APP_ID` / `FB_APP_SECRET` | Facebook OAuth app (for page posting) |
| `ANTHROPIC_API_KEY` | Claude API for content generation |
| `GEMINI_API_KEY` | Gemini API for comment drafting |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | Token + state storage (optional) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Approval notifications |
| `REDIS_URL` | Redis connection (default: `redis://localhost:6379`) |

---

## Project Structure

```
persona/
├── api/              # FastAPI routes (approval, OAuth, recipes, campaigns)
├── lib/              # Core libraries (config, task queue, rate limiter, OAuth)
├── workers/          # Scheduled worker entry points
├── scripts/          # One-off and utility scripts
├── recipe-publisher/ # Recipe → social content pipeline
├── frontend/         # React approval UI
├── tools/            # launchd plist generator, profile builder
├── mcp_server.py     # MCP server
├── docker-compose.yml
└── .env.example
```

---

## License

MIT

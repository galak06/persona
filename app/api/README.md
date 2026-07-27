# Approval API

Localhost FastAPI sidecar that exposes approval queues and orchestration endpoints for the web UI and scheduled workers. Provides routes for:

- Approving/rejecting/editing pending items (comments, blog posts, ideas, groups, campaigns)
- Monitoring engagement activity logs
- Listing and triggering scheduled workers
- Querying worker status and logs
- Managing Facebook groups tracker

No auth (binds to localhost only). The API runs on port 5001 by default and is started by `scripts/dev.py` during local development.

## Run

```bash
cd social-automation
python -m api.approval_api
# defaults: host=127.0.0.1 port=5001 (override via WEB_UI_HOST / WEB_UI_PORT)
```

## Route Inventory

### Core Approval Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/pending` | All items awaiting decision (comments, blog posts, groups, ideas, seeds, campaigns) |
| GET | `/api/v1/items/{item_id}` | Single item lookup |
| POST | `/api/v1/items/{item_id}/approve` | Approve an item (dispatches by type) |
| POST | `/api/v1/items/{item_id}/reject` | Reject an item (logs optional reason) |
| POST | `/api/v1/items/{item_id}/edit` | Approve with edited content (blog posts + comments) |

**Status codes:**
- `200` — success
- `404` — unknown item
- `409` — another channel already committed
- `422` — edit with no fields set

### Activity Logging

| Method | Path | Parameters | Purpose |
|--------|------|------------|---------|
| GET | `/api/v1/activity` | `limit` (1–500, default 50), `platform` (facebook/instagram/wordpress), `action` (string filter) | Tail of engagement_log.jsonl, most recent first |

### Configuration

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/config` | Site configuration (name, URL, persona, mascot) |
| GET | `/api/v1/health` | Liveness probe; returns `204 No Content` |

### Facebook Groups Management

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/facebook/groups` | List all FB groups (joined, pending, rejected, not_joined_yet) |
| PUT | `/api/v1/facebook/groups/{group_name}` | Update group status and posting_mode |

### Campaigns (`/api/v1/campaigns/`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/campaigns` | List all campaigns with totals and filters |
| GET | `/api/v1/campaigns/{name}` | Get campaign detail by name |
| POST | `/api/v1/campaigns/{name}/publish` | Trigger campaign publish workflow |

### Recipes (`/api/v1/`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/recipes` | List all recipe seeds with filters and analytics |
| GET | `/api/v1/recipes/analytics` | Recipe analytics (counts, statuses, trends) |
| GET | `/api/v1/recipes/{recipe_id}` | Single recipe detail |
| GET | `/api/v1/recipes/{recipe_id}/page` | Recipe page as HTML (Elementor template preview) |
| GET | `/api/v1/recipes/{recipe_id}/image-preview` | Recipe page with only the featured image visible (banner preview) |
| GET | `/api/v1/recipes/{recipe_id}/artifacts` | All generated artifacts for a recipe (carousels, Reels, media) |
| GET | `/api/v1/recipes/{recipe_id}/artifact` | Single artifact by name (query: `name=carousel.json`) |
| GET | `/api/v1/recipes/{recipe_id}/media-file` | Retrieve a single media file by name (query: `name=...`) |
| GET | `/api/v1/recipes/{recipe_id}/story-card` | Story card HTML (6-slide carousel view for Stories) |
| POST | `/api/v1/recipes/{recipe_id}/approve` | Approve a recipe for publishing |
| POST | `/api/v1/recipes/{recipe_id}/reject` | Reject a recipe |
| POST | `/api/v1/recipes/sync-publish` | Sync all approved recipes to publishing queue |

### Recipe Card Webhooks

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/webhooks/recipe-card` | WordPress Elementor hook: recipe card updates from WP post (async, returns 202) |

### Engagements (`/api/v1/`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/engagements` | All published posts + comments (tail of engagements.db) |

### Ideas (`/api/v1/`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/ideas` | All content ideas in queue |
| GET | `/api/v1/ideas/{idea_id}/slides` | All carousel slides for an idea (JSON array) |
| GET | `/api/v1/ideas/{idea_id}/slides/{n}` | Single slide by index (JSON or HTML) |
| PATCH | `/api/v1/ideas/{idea_id}/status` | Update idea status (pending → approved, etc.) |

### TikTok Candidates (`/api/v1/`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/tiktok-candidates` | List all TikTok scout candidates with status |
| PATCH | `/api/v1/tiktok-candidates/{handle}/status` | Update candidate status (followed, blocked, etc.) |

### Workers & Scheduling (`/api/v1/`)

| Method | Path | Parameters | Purpose |
|--------|------|------------|---------|
| GET | `/api/v1/workers` | — | List all scheduled workers with last-run status |
| GET | `/api/v1/workers/{label}/status` | — | Get status for a single worker |
| POST | `/api/v1/workers/{label}/trigger` | `count`, `force`, `recipe_ids`, `headless` | Fire a worker on demand (1–3 parallel instances) |
| GET | `/api/v1/workers/{label}/log` | `lines` (1–1000, default 200) | Tail the worker's log file |
| GET | `/api/v1/workers/{label}/artifact` | — | Fetch the JSON output artifact for a worker |

### Schedule & Health

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/schedule/missing` | List scheduled tasks not yet loaded in launchctl |

## Concurrency & Locking

All decision writes (approve/reject/edit) go through `fcntl.flock(LOCK_EX)` on the target queue file followed by `os.replace` for atomic visibility. The web UI and Telegram can hit the same item simultaneously; the loser gets `409 Conflict`.

## Item IDs

If a producer pre-stamps `id` or `hash`, that wins. Otherwise the id is `sha256(f"{platform}:{post_id}")[:12]`. The same derivation runs in `api.state.derive_item_id()`, so the web UI can rely on stable IDs across restarts.

## curl Examples

```bash
# Liveness
curl -i http://127.0.0.1:5001/api/v1/health

# All pending
curl -s http://127.0.0.1:5001/api/v1/pending | jq

# One item
curl -s http://127.0.0.1:5001/api/v1/items/abc123def456 | jq

# Approve as-is
curl -X POST http://127.0.0.1:5001/api/v1/items/abc123def456/approve

# Approve with overridden text
curl -X POST http://127.0.0.1:5001/api/v1/items/abc123def456/approve \
  -H 'content-type: application/json' \
  -d '{"text": "Edited reply with Nalla mention + question?"}'

# Approve blog post for FB only
curl -X POST 'http://127.0.0.1:5001/api/v1/items/blog_42/approve?channel=fb_only' \
  -H 'content-type: application/json' \
  -d '{"fb_caption": "Final FB copy"}'

# Reject with reason
curl -X POST http://127.0.0.1:5001/api/v1/items/abc123def456/reject \
  -H 'content-type: application/json' \
  -d '{"reason": "off-brand voice"}'

# Edit + approve
curl -X POST http://127.0.0.1:5001/api/v1/items/abc123def456/edit \
  -H 'content-type: application/json' \
  -d '{"text": "Rewritten with concrete Nalla detail."}'

# List workers
curl -s http://127.0.0.1:5001/api/v1/workers | jq

# Trigger a worker (3 instances)
curl -X POST 'http://127.0.0.1:5001/api/v1/workers/fb-scanner/trigger' \
  -H 'content-type: application/json' \
  -d '{"count": 3, "force": false}'

# List campaigns
curl -s http://127.0.0.1:5001/api/v1/campaigns | jq
```

## OpenAPI Schema

For exhaustive details on request/response shapes, consult the live OpenAPI schema at:

```
GET /openapi.json
GET /docs  (Swagger UI)
GET /redoc (ReDoc)
```

## Architecture

Routes are organized across multiple modules:

- **`approval_api.py`** — Main FastAPI app; routes for pending items, activity, config, health, workers, schedule, groups
- **`campaigns_api.py`** — Campaign list/detail/publish
- **`recipes_api.py`** — Recipe seeds, details, artifacts, analytics, webhooks
- **`engagements_api.py`** — Published content tracker
- **`ideas_api.py`** — Content ideas, slides, status updates
- **`tiktok_candidates_api.py`** — TikTok account scouts
- **`recipe_card_api.py`** — WordPress Elementor recipe card webhooks
- **`routes_helpers.py`** — Dispatch logic for decision endpoints
- **`schemas.py`** — Pydantic models for all request/response types
- **`state.py`** — Queue file read/write with locking
- **`schedule_config.py`** — Worker schedule definition and task lookup

Run locally via `./start.sh` (see main `README.md`) or manually start with `python -m api.approval_api`.

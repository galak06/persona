# Social Automation — Frontend

Minimal Vite + React 19 + TypeScript + Tailwind 4 SPA for reviewing and managing social media automation workflows. Provides a centralized dashboard for approvals, content scheduling, engagement tracking, and operational monitoring.

No auth (binds to a localhost-only FastAPI backend), no multi-tenant context — solo-deploy posture. The frontend communicates with the backend via `VITE_API_URL` (default: `http://127.0.0.1:5001/api/v1`).

## Running the Full Stack

The correct way to start the complete local environment is:

```bash
cd social-automation
./start.sh
```

`start.sh` runs `scripts/dev.py`, which orchestrates:

1. **Docker Phoenix** (OTel tracing) on port 6006 — optional, best-effort. If Docker isn't running or the compose file is missing, the startup emits a warning but continues.
2. **FastAPI Backend API** on port 5001 — uses the consolidated `BRAND_DIR` resolver, which:
   - Falls back to `../persona` (sibling directory to `social-automation/`) by default
   - Can be overridden by setting the `BRAND_DIR` environment variable explicitly
3. **Vite Frontend** on port 5173 — only started if `frontend/node_modules` already exists (you must run `npm install` manually first on a fresh checkout)

## Prerequisites

- Node 20+
- The FastAPI backend running (started by `start.sh`)
- Fresh checkout? Run `npm install` in `./frontend/` before starting

## Setup

```bash
npm install
cp .env.example .env.local   # then edit VITE_API_URL if needed
```

## Run

```bash
npm run dev        # http://localhost:5173 — proxies XHR to VITE_API_URL
npm run build      # → dist/ (tsc + vite build; now exits 0)
npm run preview    # serve the built bundle locally
npm run lint
```

## Regenerate API types

The `src/types/openapi.ts` file is auto-generated from the backend's `/openapi.json`. Regenerate whenever the backend Pydantic models change:

```bash
npm run gen:api:fetch    # curl http://127.0.0.1:5001/openapi.json
npm run gen:api          # openapi-typescript → src/types/openapi.ts
```

Commit both `openapi.json` and `src/types/openapi.ts`.

## Environment variables

| Var            | Default                            | Purpose                              |
| -------------- | ---------------------------------- | ------------------------------------ |
| `VITE_API_URL` | `http://127.0.0.1:5001/api/v1`     | Base URL for all backend XHR calls.  |

## Routes (App Router)

The SPA provides 11 main routes plus a 404 fallback:

| Route | Component | Purpose |
|-------|-----------|---------|
| `/` | — | Redirects to `/dashboard` |
| `/dashboard` | `Dashboard.tsx` | At-a-glance counts of pending items and key metrics |
| `/inbox` | `Inbox/index.tsx` | Actionable pending queue (comments, blog posts, groups, ideas, seeds, campaigns) |
| `/activity` | `Activity.tsx` | Tail of engagement_log.jsonl — all platform actions |
| `/campaigns` | `Campaigns.tsx` | Campaign management and publishing pipeline |
| `/ideas` | `Ideas.tsx` | Content idea generation queue and approval |
| `/groups` | `Groups.tsx` | Facebook groups tracker and status management |
| `/published` | `Published.tsx` | Archive of published content across platforms |
| `/recipes` | `Recipes.tsx` | Recipe seeds, lifecycle tracking, preview, and media |
| `/tiktok` | `TikTokCandidates.tsx` | TikTok scout candidates and account management |
| `/explorer` | `Explorer.tsx` | Content exploration and analytics tools |
| `/operations` | `Operations.tsx` | Worker status, schedule, health checks, and logs |
| `*` | `NotFound.tsx` | 404 fallback |

**Aliases:** `/flows` → Operations (health), `/schedule` → Operations (schedule), `/flow-guide` → Operations (audit)

## Layout

```
src/
  api/         # axios client + typed endpoint builders
  hooks/       # useApiQuery / useApiMutation
  components/
    ui/        # Alert / Spinner / LoadingState / EmptyState / IconBadge / ResponsiveCardRow
    layout/    # TopBar, SideNav (left sidebar with route groups)
  pages/
    Dashboard.tsx              # dashboard view
    Inbox/                     # pending queue (index.tsx + card components)
    Activity.tsx               # activity log viewer
    Campaigns.tsx              # campaign list and detail
    Ideas.tsx                  # idea queue and approval
    Groups.tsx                 # FB groups tracker
    Published.tsx              # published content archive
    Recipes.tsx                # recipe seed manager
    Recipes*.tsx               # recipe lifecycle, preview, media section
    TikTokCandidates.tsx       # TikTok candidates
    Explorer.tsx               # content explorer
    Operations.tsx             # operations hub
    Operations-related.tsx     # worker status, schedule, logs
    NotFound.tsx               # 404
  types/       # openapi.ts (auto-generated) — do not edit by hand
```

## BRAND_DIR Resolution

The backend uses a consolidated `BRAND_DIR` resolver in `lib/config.py::default_brand_dir()`. For local development:

- **Default:** expects a directory named after your brand (e.g., `persona`) as a sibling to `social-automation/` in your project root
- **Override:** set `BRAND_DIR` explicitly as an environment variable (used by production scripts and cron jobs)

The fallback resolver in `scripts/dev.py` (line 19–22) uses this same logic, so you can often omit the env var during local development.

## CI/CD

GitHub Actions runs a `frontend` job on every push/PR touching `social-automation/**`:

- **Type check & build** - `npm run build` (runs `tsc -b && vite build`; clean, exits 0)
- **Lint** - `npm run lint`

See `.github/workflows/ci.yml` for full coverage.

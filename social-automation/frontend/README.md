# Social Automation — Frontend

Minimal Vite + React 19 + TypeScript + Tailwind 4 SPA for reviewing
pending social-automation actions (FB / IG / WP posts & comments)
drafted by the backend agents.

Two routes:

- `/dashboard` — at-a-glance counts of pending items
- `/inbox` — the actionable queue (approve / skip / edit)

No auth (binds to a localhost-only FastAPI backend), no multi-tenant
context — solo-deploy posture.

## Prerequisites

- Node 20+
- The FastAPI backend running at `http://127.0.0.1:5001` (see
  `../api/`). The frontend does not start the backend.

## Setup

```bash
npm install
cp .env.example .env.local   # then edit VITE_API_URL if needed
```

## Run

```bash
npm run dev        # http://localhost:5173 — proxies XHR to VITE_API_URL
npm run build      # → dist/ (tsc + vite build)
npm run preview    # serve the built bundle locally
npm run lint
```

## Regenerate API types

The `src/types/openapi.ts` file is auto-generated from the backend's
`/openapi.json`. Regenerate whenever the backend Pydantic models
change:

```bash
npm run gen:api:fetch    # curl http://127.0.0.1:5001/openapi.json
npm run gen:api          # openapi-typescript → src/types/openapi.ts
```

Commit both `openapi.json` and `src/types/openapi.ts`.

## Environment variables

| Var            | Default                            | Purpose                              |
| -------------- | ---------------------------------- | ------------------------------------ |
| `VITE_API_URL` | `http://127.0.0.1:5001/api/v1`     | Base URL for all backend XHR calls.  |

## Layout

```
src/
  api/         # axios client + typed endpoint builders
  hooks/       # useApiQuery / useApiMutation
  components/
    ui/        # Alert / Spinner / LoadingState / EmptyState / IconBadge / ResponsiveCardRow
    layout/    # TopBar (two-tab nav: Dashboard | Inbox)
  pages/
    Dashboard.tsx   # at-a-glance pending counts
    Inbox/          # actionable pending queue
    NotFound.tsx
  types/       # openapi.ts (generated) — do not edit by hand
```

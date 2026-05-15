# Implementation Spec — Autonomous Comment Flow + Activity Log + Groups Approval

> **Audience:** Implementation agent (e.g., Gemini Code) executing this end-to-end. This document is self-contained — read it once, then implement.
>
> **Working directory:** `/Users/gilcohen/Projects/dogfoodandfun/social-automation/`
>
> **Stack:** Python 3.11+ (mypy `--strict`), FastAPI sidecar on `127.0.0.1:5001`, Vite 5 + React 19 + TypeScript strict + Tailwind 4 frontend on `127.0.0.1:5173`, JSON state files under `.claude/state/`.
>
> **Conventions to follow:**
> - 300-line file cap (split proactively).
> - Type hints on every function signature.
> - Pathlib over os.path. f-strings. snake_case Python, PascalCase classes, kebab-case CSS, camelCase TS.
> - Secrets read from `.claude/settings.local.json:env` dict via `lib.local_env.load_local_env()`. **Never inline literals.**
> - Atomic writes (tmp + os.replace) for any state-file mutation. `fcntl.flock` for cross-process safety.
> - Structured JSON logs (one dict per event).
> - `--dry-run` and `--health-check` flags on every new script.
> - mypy `--strict` clean on every new module.

---

## 1. Goal

Replace the human-gated engagement-comment flow with an autonomous one. The web UI's purpose for engagement comments becomes **reporting (read-only Activity feed)**, not gating.

| Surface | What it shows | Approval gate? |
|---|---|---|
| `/dashboard` | Pending blog posts, pending groups, today's activity counts, last-refreshed | n/a |
| `/activity` (NEW) | Chronological log from `logs/engagement_log.jsonl`: post → comment → link | No — read-only |
| `/inbox` (simplified) | Only `blog_post` pairs + `group_to_join` items | **Yes** |

Engagement comments **never appear in the Inbox**. They flow: scanner → inline Gemini draft → `comment_queue.json` with `status=approved, decided_by=auto, decided_at=now` → `comment_poster.py` cron drains and posts → `lib/engagement/log.py:log_engagement()` writes JSONL → `/activity` displays it.

---

## 2. Architecture

```
SCANNERS (every N hours via launchd)
    fb_scan.py:623     ig_scan.py:615     wp_scan.py:369
              │                │                │
              └────────────────┴────────────────┘
                              │
                              ▼  inline call, queue-append site
                lib/draft_helper.draft_comment_for_post()
                              │
                              ▼  Gemini via lib/reply_drafter._call_gemini
                       draft_comment string
                              │
                              ▼
              item appended to .claude/state/comment_queue.json
              with status=approved, decided_by=auto, decided_at=now
              (validate_voice gate inside draft_helper still fires)

POSTER CRON (every N minutes via launchd)
              comment_poster.py drains status=approved items
                              │
                              ▼
              POST via FB/IG (Playwright) or WP (REST)
                              │
                              ▼
              lib/engagement/log.py:log_engagement(...)
                              │
                              ▼
              logs/engagement_log.jsonl

WEB UI
              Frontend (Vite, 127.0.0.1:5173)
                              │
                              ▼
              FastAPI (127.0.0.1:5001)
                ├── GET /api/v1/pending      → blog_post + group_to_join only
                ├── GET /api/v1/activity     → tail of engagement_log.jsonl
                ├── POST /items/{id}/approve → dispatch by type
                └── POST /items/{id}/reject  → dispatch by type
                              │
                              ▼
              JSON state files in .claude/state/
              + lib/group_discovery for group joins
```

---

## 3. Phases

### Phase 1 — Backend overhaul

**Files:**
- Modify `api/approval_api.py`
- Modify `api/schemas.py`
- Create `lib/groups_queue.py` (~120 lines)
- Create `lib/activity_log.py` (~80 lines)

**Specific changes:**

#### 3.1 `api/schemas.py` — add types

Append the following Pydantic v2 models (discriminated union on `type`):

```python
class GroupItem(BaseModel):
    type: Literal["group_to_join"]
    id: str
    name: str
    url: str
    member_count: int | None = None
    score: float | None = None
    privacy: Literal["public", "private"] | None = None
    found_via_query: str | None = None
    competitor_mentions: int | None = None
    added_to_pending: str  # ISO-8601
    status: ItemStatus
    decided_by: DecidedBy
    decided_at: str | None = None
    created_at: str

class ActivityEntry(BaseModel):
    date: str
    timestamp: str
    action: Literal["comment", "like", "group_post", "reply", "own_reply", "page_post", "feed_post", "group_join"]
    platform: Literal["facebook", "instagram", "wordpress"]
    target_name: str | None = None
    target_url: str | None = None
    content: str | None = None  # truncated to 200 chars by writer
    reply_url: str | None = None  # populated when poster captures the URL (WP yes, FB/IG TODO)

class ActivityResponse(BaseModel):
    entries: list[ActivityEntry]
    total: int  # total entries in the file, before tail-limit
    as_of: str
```

Update `PendingItem` union to include `GroupItem`. Update `PendingResponse.counts` to include `groups_to_join` and **remove** `comments` (engagement comments don't appear). Discriminator stays `type`.

#### 3.2 `lib/groups_queue.py` — new file

State file: `.claude/state/pending_groups.json` (already populated by `lib/group_discovery/state.py:120 add_to_pending()`).

Required API:
```python
def read_pending_groups() -> list[GroupItem]: ...
def commit_group_decision(
    group_id: str,
    *,
    status: Literal["approved", "USER_SKIPPED"],
    decided_by: Literal["telegram", "web_ui", "auto"],
    decided_at: str,
) -> Literal["committed", "already_decided", "not_found"]: ...
def under_join_cap() -> tuple[bool, str]:
    """Returns (allowed, reason). Cap: 5/day, 15/week.
    Read counts from logs/engagement_log.jsonl filtered to action='group_join'."""
```

Use `fcntl.flock` + atomic tmp+rename for all writes. The producer-side schema in `pending_groups.json` may not have `id`/`status`/`decided_by`/`decided_at` yet — synthesize `id` from `sha256(url)[:12]` when reading; default `status="pending", decided_by=None, decided_at=None` if absent.

#### 3.3 `lib/activity_log.py` — new file

```python
def read_recent(
    *,
    limit: int = 50,
    platform: Literal["facebook", "instagram", "wordpress"] | None = None,
    action: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return (entries, total_in_file). entries are JSON-decoded dicts; the
    caller (FastAPI) converts to ActivityEntry. Tail-N read; do NOT load
    the whole file into memory for large files. Use a reverse-line iterator
    or seek-from-end approach. Reads engagement_log.jsonl."""
```

State file: `social-automation/logs/engagement_log.jsonl`. Cache the file's mtime and length so repeated `/activity` polls don't re-read on every request — invalidate cache when mtime changes.

#### 3.4 `api/approval_api.py` — endpoint changes

1. **Drop** the `APPROVAL_API_COMMENTS_ENABLED` flag and its handling (engagement comments no longer surface to UI).
2. `GET /api/v1/pending` — read `blog_post_queue.json` + `pending_groups.json` only. Skip `comment_queue.json`. Build counts dict with keys `blog_posts`, `groups_to_join`.
3. **NEW** `GET /api/v1/activity?limit=50&platform=&action=` — returns `ActivityResponse`. Default `limit=50`, max `500`.
4. `POST /api/v1/items/{id}/approve` — dispatch:
   - Find item across blog post and groups queues (and comment queue too if you want to support the LEGACY case, but **return 410 Gone** for items in `comment_queue.json` so the UI can't approve them).
   - `blog_post` → existing logic, respect `?channel=both|fb_only|ig_only` query.
   - `group_to_join` → call `lib.groups_queue.commit_group_decision(...)` with `decided_by="web_ui"`. If `under_join_cap()` returns `False`, respond 429 with reason. On commit, fire FastAPI `BackgroundTasks` to run `lib.group_discovery.approval.send_join_requests([group])`. Response includes `join_status: "queued"`.
5. `POST /api/v1/items/{id}/reject` — same dispatch logic; `status="USER_SKIPPED"`.
6. `POST /api/v1/items/{id}/edit` — only meaningful for blog_post (FB/IG caption edits). For group_to_join return 400 ("group items have no editable text"). For comments return 410.
7. CORS unchanged (`http://127.0.0.1:5173`, `http://localhost:5173`).

**Acceptance:** `mypy --strict api/ lib/groups_queue.py lib/activity_log.py` clean. `curl http://127.0.0.1:5001/api/v1/pending` returns valid JSON with `counts.blog_posts` + `counts.groups_to_join`. `curl http://127.0.0.1:5001/api/v1/activity?limit=10` returns 10 most recent JSONL entries decoded.

---

### Phase 2 — `lib/draft_helper.py` (inline LLM drafting)

**File to create:** `lib/draft_helper.py` (~120 lines)

```python
from __future__ import annotations
import logging
import os
from typing import Literal

from lib.reply_drafter import _call_gemini, _VOICE_RULES
from lib.comment_generator import validate_voice

log = logging.getLogger(__name__)


def draft_comment_for_post(
    *,
    platform: Literal["facebook", "instagram", "wordpress"],
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None = None,
    site_context: str | None = None,
) -> str:
    """Generate a Nalla's-Dad-voice engagement comment.
    Returns the validated draft text, or empty string on:
      - missing GEMINI_API_KEY
      - LLM failure
      - voice validation failing twice (retry once with stricter prompt)
    Caller is responsible for handling the empty case (e.g., skip the item)."""
    if not os.environ.get("GEMINI_API_KEY"):
        log.info({"event": "draft_gemini_key_missing", "platform": platform})
        return ""

    prompt = _build_prompt(platform, post_text, group_or_hashtag, post_url, site_context)
    draft = _call_gemini(prompt, max_tokens=400)
    if not draft:
        log.info({"event": "draft_gemini_returned_none", "platform": platform})
        return ""

    valid, violations = validate_voice(draft, allow_own_url=False)
    if valid:
        log.info({"event": "draft_inline_ok", "platform": platform, "len": len(draft)})
        return draft.strip()

    # Retry once with a stricter prompt mentioning specific violations.
    log.info({"event": "draft_voice_retry", "platform": platform, "violations": violations})
    retry_prompt = prompt + "\n\nIMPORTANT: avoid: " + "; ".join(violations)
    draft = _call_gemini(retry_prompt, max_tokens=400)
    if not draft:
        return ""
    valid, violations = validate_voice(draft, allow_own_url=False)
    if valid:
        return draft.strip()
    log.warning({"event": "draft_voice_fail_final", "platform": platform, "violations": violations})
    return ""


def _build_prompt(
    platform: Literal["facebook", "instagram", "wordpress"],
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None,
    site_context: str | None,
) -> str:
    parts = [
        _VOICE_RULES.strip(),
        f"\nPLATFORM: {platform}",
    ]
    if group_or_hashtag:
        parts.append(f"GROUP/HASHTAG: {group_or_hashtag}")
    if post_url:
        parts.append(f"POST URL: {post_url}")
    parts.append(f"\nORIGINAL POST:\n{post_text.strip()}")
    if site_context:
        parts.append(f"\nRELEVANT SITE CONTENT (do NOT link unless natural):\n{site_context}")
    parts.append(
        "\nWrite a single short reply (1-3 sentences). Personal, helpful, "
        "no salesy language, no medical claims, no links. Output ONLY the "
        "reply text — no preamble, no quotes."
    )
    return "\n".join(parts)
```

If `_call_gemini` and `_VOICE_RULES` are currently private (leading underscore), make them publicly accessible by re-exporting at the bottom of `lib/reply_drafter.py`:
```python
__all__ = [..., "_call_gemini", "_VOICE_RULES"]
```
Or rename them. Pick whichever is cleaner.

**Acceptance:** `mypy --strict lib/draft_helper.py` clean. `python -c "from lib.draft_helper import draft_comment_for_post; print(draft_comment_for_post(platform='facebook', post_text='Anyone tried homemade kibble?', group_or_hashtag='Dog Lovers'))"` returns non-empty text (assuming `GEMINI_API_KEY` is set) or empty string + log line (if not).

---

### Phase 3 — `comment_approver` auto-approve

**File to modify:** `scripts/comment_approver.py` (or wherever the engagement-comment approval gate lives — confirm by `grep -n "request_approval\|send_for_approval" scripts/`).

Find the call path that today does Telegram approval for engagement comments. Replace the `notifier.request_approval(...)` call with a direct queue-state commit:

```python
from lib.queue_state import commit_telegram_decision  # rename to commit_decision if you wish
from datetime import datetime, timezone

# OLD: result = notifier.request_approval(item_id=item["id"], queue_path=QUEUE_PATH, ...)
# NEW:
commit_telegram_decision(
    QUEUE_PATH,
    item["id"],
    status="approved",
    decided_by="auto",  # NEW value — see below
    decided_at=datetime.now(timezone.utc).isoformat(),
    text=item.get("draft_comment", ""),
)
```

**Type change required:** `DecidedBy` in `api/schemas.py` currently is `Literal["telegram", "web_ui"] | None`. Add `"auto"`: `Literal["telegram", "web_ui", "auto"] | None`. Update `lib/queue_state.py` + `api/state.py` to accept the new value.

**Skip Telegram for engagement comments**. Do NOT touch the blog-post or group-join Telegram paths (different code).

**Edge case:** if `item.get("draft_comment")` is empty (Gemini failed in scanner), commit with `status="USER_SKIPPED", decided_by="auto"` instead of `approved` so `comment_poster.py` skips it. Log `{"event": "auto_skip_empty_draft", "item_id": ...}`.

**Acceptance:** `mypy --strict scripts/comment_approver.py` clean. Telegram receives zero engagement-comment messages on the next run (verify by inspecting logs). Items appear in `comment_queue.json` with `status=approved, decided_by=auto`.

---

### Phase 4 — Frontend: Activity page + simplified Inbox + 3-tab nav

**Files to create:**
- `frontend/src/pages/Activity.tsx` (~180 lines)
- `frontend/src/pages/Inbox/GroupCard.tsx` (~140 lines)

**Files to modify:**
- `frontend/src/components/layout/TopBar.tsx` — add Activity NavLink between Dashboard and Inbox
- `frontend/src/App.tsx` — add `/activity` route → `<Activity />`
- `frontend/src/pages/Dashboard.tsx` — three cards (Pending blog posts | Pending groups | Last refreshed) + a small "Today's activity" mini-summary fetched from `/api/v1/activity` filtered to today
- `frontend/src/pages/Inbox/PendingTab.tsx` — drop CommentCard import + usage; add `case "group_to_join"` returning `<GroupCard>`
- `frontend/src/pages/Inbox/FlowTabs.tsx` + `shared.ts` — `FLOWS: ["all", "blog_posts", "groups_to_join"]`, add label `groups_to_join → "Groups to join"`, update `itemMatchesFlow` to recognize `group_to_join`
- `frontend/src/api/endpoints.ts` — add `activity(params: {limit?: number; platform?: string; action?: string}) -> Promise<ActivityResponse>`
- `frontend/src/types/openapi.ts` — add `GroupItem`, `ActivityEntry`, `ActivityResponse`; update `PendingItem` union

**Untouched dead code:**
- `frontend/src/pages/Inbox/CommentCard.tsx` (keep file, keep disabled-marker)

**`Activity.tsx` requirements:**
- `useApiQuery(endpoints.activity, { refetchInterval: 5000 })`
- Table columns: Time (relative, e.g. "3 min ago") | Platform (icon) | Action (colored chip: comment=cyan, like=pink, join=amber, page_post/feed_post=slate) | Target (clickable link to `target_url`) | Content (200-char preview) | Permalink (icon link if `reply_url` else "—")
- Filter chips above the table: All / Facebook / Instagram / WordPress (platform); All / Comments / Likes / Joins / Posts (action). Local React state; re-derive filtered rows client-side.
- Empty state: "No activity yet."
- Loading state: spinner. Error state: Alert.

**`GroupCard.tsx` requirements:**
- Header: group name (large), privacy badge (public=blue, private=amber), member count formatted (e.g., "12.3K members")
- Body: score (formatted as percentage), found-via query, competitor-mentions count if > 0
- Footer: two buttons — **Join** (primary, cyan) calls `endpoints.approve(id)`, **Skip** (secondary) calls `endpoints.reject(id)`
- Optimistic removal on click; 409 treated as success (race with manual stdin approval); show error inline on other failures
- If the API returns `429` (over join cap), show an inline warning: "5/day or 15/week join cap reached. Try again tomorrow."

**Acceptance:** `npm run build` PASS, `npm run lint` PASS, no `as any`, no `@ts-ignore`. Three top-nav tabs visible at `/`, `/activity`, `/inbox`. `/activity` shows the 88 existing JSONL entries.

---

### Phase 5 — Scanner inline drafting hooks

**Files to modify** (only the queue-append site, ~5-10 lines per file):
- `scripts/fb_scan.py:623`
- `scripts/ig_scan.py:615`
- `scripts/wp_scan.py:369`

**At each site**, just before the item is appended to `comment_queue.json`, call `draft_helper.draft_comment_for_post(...)`:

```python
from lib.draft_helper import draft_comment_for_post

# ... inside the per-post loop, before queue.append(item):
draft = draft_comment_for_post(
    platform="facebook",  # or "instagram" or "wordpress"
    post_text=post["text"],  # whatever field the scanner uses
    group_or_hashtag=group_name,  # or hashtag, or None
    post_url=post.get("permalink_url"),
    site_context=None,  # optional; leave None for v1
)
item["draft_comment"] = draft  # may be empty string on LLM fail
# Mark status now so comment_approver can stamp approved/auto
# (or let comment_approver do it — depends on the current pipeline shape)
```

For the **fb_scan** and **ig_scan** paths, the existing `comment_approver.py` will pick up these items and stamp them (per Phase 3). For **wp_scan**, follow the same pattern.

**Acceptance:** synthetic invocation: feed a hand-crafted post object through the modified scanner function with `--dry-run` style execution; verify `draft_comment` is populated in the resulting item (or empty + log on Gemini failure).

---

### Phase 6 — schedule.json + restart

**File to modify:** `social-automation/schedule.json`

Lines 32 (`dogfood-fb-scanner`), 58 (`dogfood-ig-scanner`), 84 (`dogfood-comment-composer`) currently have:
```json
{
  "name": "dogfood-fb-scanner",
  "disabled": true,
  "_disabled_reason": "comment flow retired 2026-05-15",
  ...
}
```

Change each to `"disabled": false` and remove the `_disabled_reason` field.

**Verify launchd:** `launchctl list | grep dogfoodandfun` — confirm the 4 plists (`com.dogfoodandfun.{fb-scanner,ig-scanner,comment-approver,comment-poster}.plist`) are loaded. They were loaded throughout the disable phase, so no `launchctl load` needed.

**Restart the API:**
```bash
lsof -ti tcp:5001 | xargs -r kill -9
sleep 1
cd /Users/gilcohen/Projects/dogfoodandfun/social-automation && nohup python -m api.approval_api > /tmp/approval_api.log 2>&1 &
sleep 2
curl -sf http://127.0.0.1:5001/api/v1/pending | python -m json.tool | head -20
```

The Vite dev server picks up frontend changes via HMR — no restart needed.

**Acceptance:** `/pending` returns only blog_post + group_to_join items; `/activity` returns recent log entries.

---

### Phase 7 — End-to-end verification

Run all checks. Report PASS/FAIL per check.

1. **mypy:**
   ```
   mypy --strict api/ lib/draft_helper.py lib/groups_queue.py lib/activity_log.py
   ```
   Must be clean.

2. **draft_helper smoke:**
   ```
   GEMINI_API_KEY=$(python -c 'from lib.local_env import load_local_env; load_local_env(); import os; print(os.environ.get("GEMINI_API_KEY","NOT_SET"))') \
   python -c "from lib.draft_helper import draft_comment_for_post; print(repr(draft_comment_for_post(platform='facebook', post_text='Anyone tried homemade kibble?', group_or_hashtag='Dog Lovers')))"
   ```
   Returns a non-empty string passing voice rules.

3. **API smoke:**
   ```
   curl -sf http://127.0.0.1:5001/api/v1/pending | python -c "import sys,json; d=json.load(sys.stdin); print(sorted(d['counts']), [i['type'] for i in d['items'][:3]])"
   curl -sf 'http://127.0.0.1:5001/api/v1/activity?limit=5' | python -c "import sys,json; d=json.load(sys.stdin); print('total:', d['total'], 'returned:', len(d['entries']))"
   ```
   First returns counts dict with `blog_posts` + `groups_to_join` keys (no `comments`), items list has no `type=='comment'`. Second returns 5 entries.

4. **Synthetic group approve:**
   - Inject a fake group into `.claude/state/pending_groups.json` (atomic write).
   - `curl -X POST http://127.0.0.1:5001/api/v1/items/<id>/approve` — expect 200 + `join_status=queued`.
   - Confirm `.claude/state/pending_groups.json` shows the item with `status=approved, decided_by=web_ui`.
   - **Cleanup:** remove the test group before finishing.

5. **Frontend:**
   ```
   cd frontend && npm run build 2>&1 | tail -5
   cd frontend && npm run lint 2>&1 | tail -5
   ```
   Both PASS.

6. **Browser smoke** (manual; document if can't run headless):
   - Open `http://127.0.0.1:5173/dashboard` → 3 cards.
   - Open `http://127.0.0.1:5173/activity` → table with ≥ 5 rows.
   - Open `http://127.0.0.1:5173/inbox` → 3 flow tabs.

7. **File-size cap audit:**
   ```
   wc -l api/*.py lib/draft_helper.py lib/groups_queue.py lib/activity_log.py \
         frontend/src/pages/Activity.tsx frontend/src/pages/Inbox/GroupCard.tsx
   ```
   Every new file under 300 lines.

---

## 4. Constraints — DO NOT

- DO NOT touch `lib/comment_generator.py:validate_voice` (already fixed previously; engagement comments still call it with default `allow_own_url=False`).
- DO NOT delete `frontend/src/pages/Inbox/CommentCard.tsx` (leave as dead code).
- DO NOT delete `scripts/ig_like.py` or `lib/ig_like_helpers.py` (leftover from prior phase; mark for cleanup).
- DO NOT modify launchd `.plist` files directly.
- DO NOT git commit, push, or create branches.
- DO NOT alter the Telegram approval flow for blog-post pairs or group joins.
- DO NOT introduce a database — JSON state files are the project's chosen primitive.
- DO NOT add a new auth/login system to the UI (localhost-only).
- DO NOT inline secrets — every API key from `.claude/settings.local.json:env`.

## 5. Constraints — DO

- DO use `fcntl.flock` for any cross-process file mutation.
- DO use atomic tmp+rename writes (the bug fixed in `api/state.py` previously: re-read AFTER acquiring flock, never trust a stale FD).
- DO log structured JSON dicts (one per event).
- DO keep every new file under 300 lines.
- DO add `--dry-run` and `--health-check` to any new script.
- DO check `under_join_cap()` before triggering a group join (5/day, 15/week).

## 6. Known follow-ups (NOT in scope)

- FB/IG Playwright posters do not capture the posted-comment URL today. Activity entries for those platforms will link to the **target post** instead of the **posted comment**. Enhance the posters with DOM-scrape of the new comment's permalink in a follow-up.
- The 65 existing items in `comment_queue.json` have empty `draft_comment`. The poster will auto-skip them (per Phase 3 logic). Optional: write a `scripts/backfill_drafts.py` that loops `pending` items with empty drafts and calls `draft_helper`.
- `scripts/ig_like.py` and `lib/ig_like_helpers.py` are dead code from a prior extraction; delete in cleanup.
- `lib/notifier.py` is 656 lines (over the 300 cap) — pre-existing tech debt; split in a follow-up refactor.

## 7. Verification gates (must pass before declaring done)

- [ ] `mypy --strict api/ lib/` clean (delta = 0 new errors vs main)
- [ ] `cd frontend && npm run build && npm run lint` PASS
- [ ] `/api/v1/pending` returns counts without `comments` key
- [ ] `/api/v1/activity?limit=10` returns 10 ActivityEntry rows
- [ ] `draft_helper.draft_comment_for_post(...)` smoke test returns non-empty validated text
- [ ] Synthetic group approve: API 200 + state file updated + background join task fired
- [ ] All new files under 300 lines
- [ ] No `any` / `@ts-ignore` / `@ts-expect-error` in new TypeScript

End of spec.

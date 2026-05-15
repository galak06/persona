# Approval API (Phase 1 scaffold)

Localhost FastAPI sidecar that exposes the Telegram approval queues
(`comment_queue.json`, `blog_post_queue.json`) so a web UI can decide in
parallel with Telegram. First channel to commit wins; the other gets `409`.

## Run

```bash
cd social-automation
python -m api.approval_api
# defaults: host=127.0.0.1 port=5001 (override via WEB_UI_HOST / WEB_UI_PORT)
```

## Endpoints

| Method | Path                                  | Purpose                                     |
| ------ | ------------------------------------- | ------------------------------------------- |
| GET    | `/api/v1/health`                      | Liveness — returns `204 No Content`         |
| GET    | `/api/v1/pending`                     | All items awaiting decision (both queues)   |
| GET    | `/api/v1/items/{item_id}`             | Single item lookup (404 if not found)       |
| POST   | `/api/v1/items/{item_id}/approve`     | Approve with optional override              |
| POST   | `/api/v1/items/{item_id}/reject`      | Mark `USER_SKIPPED`                         |
| POST   | `/api/v1/items/{item_id}/edit`        | Approve with edited body (commits `edited`) |

`409 Conflict` on any decision endpoint = another channel already committed.
`404` = unknown id. `422` = edit with no fields set.

## curl examples

```bash
# Liveness
curl -i http://127.0.0.1:5001/api/v1/health

# All pending
curl -s http://127.0.0.1:5001/api/v1/pending | jq

# One item
curl -s http://127.0.0.1:5001/api/v1/items/abc123def456 | jq

# Approve as-is
curl -X POST http://127.0.0.1:5001/api/v1/items/abc123def456/approve

# Approve with overridden comment text
curl -X POST http://127.0.0.1:5001/api/v1/items/abc123def456/approve \
  -H 'content-type: application/json' \
  -d '{"text": "Edited reply with Nalla mention + question?"}'

# Approve blog-post pair, FB only
curl -X POST 'http://127.0.0.1:5001/api/v1/items/blog_42/approve?channel=fb_only' \
  -H 'content-type: application/json' \
  -d '{"fb_caption": "Final FB copy"}'

# Reject
curl -X POST http://127.0.0.1:5001/api/v1/items/abc123def456/reject \
  -H 'content-type: application/json' \
  -d '{"reason": "off-brand voice"}'

# Edit + approve
curl -X POST http://127.0.0.1:5001/api/v1/items/abc123def456/edit \
  -H 'content-type: application/json' \
  -d '{"text": "Rewritten with concrete Nalla detail."}'
```

## Item IDs

If a producer pre-stamps `id` or `hash`, that wins. Otherwise the id is
`sha256(f"{platform}:{post_id}")[:12]`. The same derivation runs in
`api.state.derive_item_id`, so the web UI can rely on stable ids across
restarts.

## Concurrency

All writes go through `fcntl.flock(LOCK_EX)` on the target queue file
followed by `os.replace` for atomic visibility. Telegram and the web UI
can hit the same item simultaneously; the loser gets `409`.

## Not yet wired

- The Telegram side (`lib/notifier.py`, `scripts/comment_approver.py`,
  `scripts/content_pipeline.py`) is **not** updated yet — Phase 3 / 4.
  Until then this API can read everything, and can write decisions, but
  the producers won't notice. Use it for the UI build only.
- `blog_post_queue.json` is created on first write by `commit_decision`;
  Phase 4 will start producing items.

---
name: wp-comment-handler
description: >
  Moderate pending comments on {{brand.domain}} and post {{brand.persona}} replies.
  Pulls held comments from the WordPress REST API, auto-trashes obvious spam,
  queues the rest for Telegram approval, then approves + replies in one shot
  when the user confirms.
  Use when the user says "check wordpress comments", "moderate site comments",
  "reply to blog comments", "run wp comment scan", or "moderate pending
  comments".
---

# WordPress Comment Handler — {{brand.name}}

Three-stage pipeline, mirroring `fb-scanner` / `ig-scanner` / `comment-composer`:

1. **Scan** (`scripts/wp_scan.py`) — fetch held comments, trash spam, queue the rest.
2. **Compose** (Claude drafts a {{brand.mascot}}'s-Dad reply per pending item — see `## Drafting`).
3. **Approve + Post** (`scripts/comment_approver.py` + `scripts/comment_poster.py`) — Telegram confirms every reply; on approval, the visitor comment is published *and* the reply is posted under it.

State shared with FB/IG: `.claude/state/comment_queue.json`, `rate_limit_tracker.json`, `dedup_cache.json`, `last_run.json`, `engagement_log.jsonl`.

## How to Run

```bash
cd /Users/gilcohen/Projects/{{brand.name_lower}}/social-automation

# 1. Scan {{brand.domain}} for held comments.
python scripts/wp_scan.py                 # scans + queues
python scripts/wp_scan.py --dry-run       # prints what would happen

# 2. Draft replies for pending WP items (run the comment-composer skill).
#    The skill reads queue entries with platform=="wordpress" and status=="pending",
#    generates a reply per item, validates voice, saves draft_comment back.

# 3. Request Telegram approval for the drafts.
python scripts/comment_approver.py

# 4. Publish the approved replies.
python scripts/comment_poster.py
```

## Environment

`scripts/wp_scan.py` reads these from `.claude/settings.local.json`:

| Var | Used for |
|---|---|
| `WP_URL` | `https://{{brand.domain}}` |
| `WP_USER` | Application-password user (needs `moderate_comments` capability) |
| `WP_APP_PASSWORD` | Application password (spaces preserved) |

Never hardcode these — they're read from settings via the harness env.

## Auto-Trash Heuristic

`wp_scan.py` auto-trashes comments on any of:

- 3 or more `http(s)://` links in the body.
- Known spam keyword hit (viagra, casino, crypto wallet, payday loan, buy followers, etc. — see `_SPAM_KEYWORDS` in `wp_scan.py`).
- Author URL on a spam-associated TLD (`.xyz`, `.top`, `.loan`, `.click`, `.win`).

Trashing uses a non-force DELETE so false positives are recoverable from the WP admin UI (Comments → Trash).

Ultra-short comments, off-topic-but-polite comments, and everything else go to Telegram approval — cheaper to spend one approval message than to accidentally trash a real reader.

## Stale Cutoff

Comments older than **30 days** are skipped without a reply — the commenter has moved on, and replying reads as automated. Moderation itself (approve/reject) still happens via the WP admin UI for those.

## Drafting

When drafting replies for WP items in the comment-composer flow, use context that's different from the FB/IG prompt:

- **Voice is still {{brand.persona}}** — same voice rules (`validate_voice` in `lib/comment_generator.py`) apply: specificity ({{brand.mascot}}/number/brand/timeframe), personal experience, end with a question.
- **Context available on the queue item:**
  - `post_text` — the visitor's comment body
  - `parent_post_title` — the blog post they commented on
  - `author` — the commenter's display name (use their first name)
- **Don't include links** to {{brand.domain}} — reader is already on the site.
- **Length:** 80–300 chars (moderation replies are slightly longer than IG/FB because they can reference the post's content).

Example prompt shape:

```
You are {{brand.persona}} replying to a comment on your own blog post.

Post: "{parent_post_title}"
Commenter ({author}) said:
"{post_text}"

Write a warm, specific reply (80-300 chars). Address them by first name if
natural. Mention {{brand.mascot}} or a concrete detail. End with a genuine follow-up
question. No medical jargon, no generic openers, no links.
```

## Approval Behavior

Every WP item routes through Telegram — `comment_approver.py` now treats `platform == "wordpress"` the same as `platform == "instagram"` (always requires approval). The Telegram message shows:

- Commenter name + the visitor comment body
- The parent post title (so you know what thread you're in)
- The drafted reply
- A relevance score (informational — doesn't gate)

Reply `yes` to publish the visitor comment + post the reply; `skip` to leave both in moderation; `edit: <new text>` to tweak the reply before it goes out.

## Rate Limits

- **Replies posted per day:** 20 (`wordpress:comment` in `rate_limiter.py`).
- **Delay between replies:** 15–45s random.
- **Approving / trashing visitor comments is not rate-limited** — those are site-owner moderation actions, not third-party engagement.

## Failure Modes

| Symptom | Meaning | Fix |
|---|---|---|
| `WP_SCAN_CONFIG_ERROR: Missing WP env var` | Settings missing `WP_URL`/`WP_USER`/`WP_APP_PASSWORD` | Add to `.claude/settings.local.json` env |
| `approve failed: 403` | `WP_USER` lacks `moderate_comments` | Grant Editor+ role or use the admin account's app password |
| `reply failed: 401` | App password revoked | Regenerate in WP → Users → Profile → Application Passwords |
| Comment re-appears in next scan | Dedup cache reset or TTL expired | Expected after 60 days; re-processing is safe |

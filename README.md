# DogFoodAndFun — Social Media Automation

Claude CLI agents that engage with dog-related Facebook groups and Instagram hashtags on behalf of [dogfoodandfun.com](https://dogfoodandfun.com).

Brand persona: **Nalla's Dad** — authentic, value-first dog owner who shares personal experiences with his dog Nalla. Never sounds medical or salesy.

## Prerequisites

- Python 3.11+
- [Playwright](https://playwright.dev/python/) (browser automation)
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) with the following MCP servers:
  - **Claude in Chrome** — navigate, get_page_text, find, javascript_tool, form_input, computer (click)
  - **File System** — read, write, edit
  - **Bash** — run Python scripts
- Browser already logged into Facebook and Instagram (agents never enter credentials)

## Setup

```bash
cd social-automation

# 1. Install Python dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# 2. Create browser sessions (interactive — opens browser for manual login)
python scripts/fb_login.py
python scripts/ig_login.py

# 3. Set up Telegram notifications (optional)
mkdir -p .claude/state
cat > .claude/state/telegram_config.json << 'EOF'
{
  "bot_token": "YOUR_BOT_TOKEN",
  "chat_id": "YOUR_CHAT_ID"
}
EOF
```

## Session Files (gitignored)

Created by the login scripts above. Must exist before running any agent.

| File | Created by |
|------|-----------|
| `.claude/state/facebook_session.json` | `scripts/fb_login.py` |
| `.claude/state/instagram_session.json` | `scripts/ig_login.py` |
| `.claude/state/telegram_config.json` | Manual setup |

## Agents

| Skill | What it does | When |
|-------|-------------|------|
| `site-analyzer` | Crawl dogfoodandfun.com, build content cache | Run before scans |
| `fb-scanner` | Scan joined FB groups for relevant posts to engage with | Daily 8:30 AM |
| `ig-scanner` | Scan IG hashtags, queue posts to like/comment | Daily 2:30 PM |
| `comment-composer` | Draft, validate, and post queued comments | Daily 9:00 PM |
| `fb-group-scout` | Find and join new dog-related FB groups | 1st of month |
| `fb-group-publisher` | Publish blog posts to eligible FB groups | On demand |
| `activity-logger` | Log all actions to JSONL + Excel tracker | Called by agents |

## Rate Limits

These are hard limits — agents abort gracefully if exceeded.

```yaml
facebook:
  comments_per_day: 5
  group_visits_per_day: 6
  group_join_requests_per_week: 3
  delay_between_comments: 30-120s random
  delay_between_group_visits: 45-180s random

instagram:
  likes_per_day: 8
  comments_per_day: 2
  delay_between_likes: 10-45s random
  delay_between_comments: 120-180s random
```

## Relevance Scoring

Posts are scored before queueing. Threshold to engage: **0.75**.

| Signal | Weight |
|--------|--------|
| Dog food / recipe / nutrition / ingredients | +0.40 |
| GPS / running with dog / gear / canicross | +0.30 |
| Post is a question (Q&A format) | +0.20 |
| Mentions a brand reviewed on site | +0.20 |
| 5–50 comments (engaged but not viral) | +0.10 |
| Post from last 24 hours | +0.10 |
| 100+ comments (too crowded) | -0.30 |
| Post by a competitor account | -0.50 |

## Approval Gates

These actions always pause for user confirmation:

- First comment to any new group
- Any comment containing a URL to dogfoodandfun.com
- All Instagram comments
- Posts scoring 0.70–0.80 relevance (borderline)

## Comment Quality Gates

All must pass before a comment is posted:

1. Relevance score >= 0.75
2. Voice validation — no medical jargon, no salesy language, ends with a question
3. Not in dedup cache (no engagement with same post/group in last 60 days)
4. Daily rate limit not exceeded
5. User approval where required by gates above

## Brand Voice

**Do**: share personal experience with Nalla, ask genuine follow-up questions, reference specific details from their post, use casual warm language ("honestly", "works great for us")

**Don't**: use medical/clinical language, say "check out our website", use generic praise ("Great post!"), post the same template twice in the same group within 30 days, post a link without user approval

## Project Structure

```
social-automation/
├── .claude/
│   ├── settings.json       — Claude CLI project config
│   ├── skills/             — 7 agent skill definitions
│   └── state/              — Session cookies, dedup cache, rate counters (gitignored)
├── config.json             — Rate limits, scoring weights, voice rules
├── requirements.txt        — Python dependencies
├── scripts/                — fb_login, ig_login, fb_scan, ig_scan, fb_group_scout
├── lib/                    — comment_generator, deduplication, rate_limiter, notifier
├── data/                   — post_templates, brand_voice_guide, instagram_accounts
└── logs/                   — engagement_log.jsonl, audit_trail.json
```

## Error Handling

- **Rate limit hit** — abort gracefully, log to `logs/errors.log`, do not retry same day
- **Session expired** — log `SESSION_EXPIRED`, abort run
- **Post fails** — log error, mark as `FAILED` in dedup cache (not "engaged")

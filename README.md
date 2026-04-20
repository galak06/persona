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

## Skills

### Engagement (6)

Scan social platforms, score posts, queue comments, post approved ones.

| Skill | What it does | When |
|-------|-------------|------|
| `site-analyzer` | Crawls dogfoodandfun.com RSS + sitemap, caches recent posts so comments can reference live site content | Daily 3:00 PM Israel |
| `fb-scanner` | Visits joined FB dog groups, scores each post by relevance to the site's topics, queues high scorers for commenting | Daily 3:30 PM |
| `ig-scanner` | Scans IG hashtags, likes qualifying posts (≤8/day), queues top candidates for comments (≤2/day) | Daily 7:00 PM |
| `comment-composer` | Drafts Nalla's Dad-voice comments from the queue, validates against brand rules, sends to Telegram for approval, posts | Daily 10:00 PM |
| `reply-follower` | Revisits recent FB comments, scrapes replies, drafts + approves + posts conversational responses (threaded). Drives 10-30x more profile visits than the original comment | On demand |
| `fb-group-scout` | Searches FB for new dog-related groups (public + private), scores + shortlists for approval, sends join requests (≤3/week) | Monthly 1st |
| `fb-group-publisher` | Pushes a WP blog post into eligible FB groups with per-category tailored text, respects group rules | On demand |

### Content publishing (6)

Ideate → enrich → write → post to FB + IG (feed or Reel).

| Skill / stage | What it does | When |
|---|---|---|
| `content-ideator` | Generates 5–10 blog ideas from content gaps, trends, PAA, seasonal windows; appends to the Google Sheet | On demand |
| `content-enricher` | Enriches the next approved idea with SEO + social + demand research, sends a brief to Telegram for approval | On demand |
| `wp-post-creator` | Writes a full blog post in Nalla's Dad voice from the approved brief (data-driven, 5+ Nalla mentions), creates a WP draft | After brief approved |
| `fb-post-creator` | Facebook page post from the published WP post (150–200w, no hashtags) via Graph API | After WP published |
| `ig-post-creator` | Single IG feed post from the published WP post (caption + Pexels image + 6–8 hashtags) via Graph API | After WP published |
| `content_pipeline.py --stage reel --seed <id>` | End-to-end IG Reel: AI-rendered 9:16 slides with conversion overlays, instrumental music bed, Telegram approval, Reels API publish | On demand |

### Operations (3)

Metrics, backups, and action logging.

| Skill | What it does | When |
|---|---|---|
| `performance-tracker` | Pulls monthly engagement metrics from WP + FB + IG, ranks top content, writes a report with recommendations that feed back into ideator/enricher | Monthly 1st |
| `sheet-backup` | Backs up Google Sheet tabs + local state files as JSON with 90-day retention | Weekly Sunday |
| `activity-logger` | Logs every action (like, comment, join, post) to JSONL + updates the Excel tracker | Called by all skills |

### Reel pipeline details

The Reel stage is the newest capability — it stitches AI-generated 9:16 slides (with a corner follow badge on slide 1 and a full-width site CTA ribbon on slide 4) into an mp4 with crossfades, mixes a Jamendo instrumental bed, and publishes via the IG Graph Reels API after a Telegram preview + approval. Full flow: [`social-automation/README.md`](social-automation/README.md#ig-reel-pipeline-content_pipelinepy---stage-reel).

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

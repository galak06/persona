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

### Engagement (7)

Scan social platforms, score posts, queue comments, post approved ones.

| Skill | What it does | When |
|-------|-------------|------|
| `site-analyzer` | Crawls dogfoodandfun.com RSS + sitemap, caches recent posts so comments can reference live site content | Daily 3:00 PM Israel |
| `fb-scanner` | Visits joined FB dog groups, scores each post by relevance to the site's topics, queues high scorers for commenting | Daily 3:30 PM |
| `ig-scanner` | Scans IG hashtags, likes qualifying posts (≤8/day), queues top candidates for comments (≤2/day) | Daily 7:00 PM |
| `auto-drafter` | Fills in `draft_comment` on queued items via template matcher so the poster has something to approve — closes the gap between scanner and poster | Daily 8:00 PM |
| `comment-composer` / `comment-poster` | Drafts Nalla's Dad-voice comments from the queue (LLM for non-template items), Telegram-approves, posts via Playwright | Daily 10:00 PM |
| `reply-follower` | Revisits recent FB comments, scrapes replies, drafts + approves + posts threaded responses. Drives 10-30x more profile visits than the original comment | Daily 8:00 AM + 8:30 PM |
| `fb-group-scout` | Searches FB for new dog-related groups (public + private), scores + shortlists for approval, sends join requests (≤3/week) | Monthly 1st |

### Content publishing (6)

Ideate → enrich → write → post to FB + IG (feed or Reel).

| Skill / stage | What it does | When |
|---|---|---|
| `content-ideator` | Generates 5–10 blog ideas from content gaps, trends, PAA, seasonal windows; appends to the Google Sheet | Sunday 10:00 |
| `content-enricher` | Enriches the next approved idea with SEO + social + demand research, sends a brief to Telegram for approval | On demand |
| `wp-post-creator` | Writes a full blog post in Nalla's Dad voice from the approved brief (data-driven, 5+ Nalla mentions, `[AFFILIATE:*]` placeholders), creates a WP draft | After brief approved |
| `fb-post-creator` / `ig-post-creator` (`--stage publish`) | Facebook page post + single IG feed post from the published WP post (via Graph API) | Monday 15:00 |
| `--stage reel --seed <id>` | End-to-end IG Reel: AI 9:16 slides with conversion overlays, instrumental music bed, Telegram approval, Reels API publish | On demand |
| `--stage campaign --product <key> --reel-seed <id>` | **Amazon affiliate campaign** — product lookup, WP post with resolved affiliate links, Reel published to BOTH IG + FB with `ascsubtag` attribution, enforced `--min-gap-hours 72` | On demand |

### FB Groups management (5)

Group-level tooling that sits under `fb-group-publisher`.

| Skill / script | What it does | When |
|---|---|---|
| `fb_notification_scan.py` | Scans FB notifications for newly-approved group memberships, populates `data/groups_tracker.json` | Sunday 9:00 |
| `fb_group_enrich.py` | Per-group name / privacy / member-count scraper — fills in tracker metadata | On demand |
| `fb_groups_posting_scan.py` | Classifies `posting_mode` per group: `direct` / `admin_approval` / `admins_only` so publishers skip un-postable groups | On demand |
| `fb_group_post.py` | Post a WP blog link to eligible groups via composer automation; `--no-comment` + `--caption-override` flags; respects 3/day rate cap | On demand |
| `fb_group_note.py` | CLI for manual status/mode/note updates on tracker entries (`--mode blocked`, `--status pending_approval`, etc.) | On demand |
| `fb_pending_posts_check.py` | Revisits pending-approval posts in groups, detects when they clear the mod queue, reminds you to add the URL comment | On demand (weekly recommended) |

### Operations (3)

Metrics, backups, and action logging.

| Skill | What it does | When |
|---|---|---|
| `performance-tracker` | Pulls monthly engagement metrics from WP + FB + IG, ranks top content, writes a report with recommendations | Monthly 1st |
| `sheet-backup` | Backs up Google Sheet tabs + local state files as JSON with 90-day retention | Weekly Sunday |
| `activity-logger` | Logs every action (like, comment, join, post) to JSONL + updates the Excel tracker | Called by all skills |

### Reel pipeline (`--stage reel`)

AI-generated 9:16 slides → text overlays (corner follow badge on slide 1, site-CTA ribbon on slide 4) → instrumental music bed (Jamendo) → ffmpeg compose → Telegram preview → IG Reels publish. Full flow: [`social-automation/README.md`](social-automation/README.md#ig-reel-pipeline-content_pipelinepy---stage-reel).

### Affiliate campaign pipeline (`--stage campaign`)

The newest capability: a coordinated Amazon-affiliate push across WordPress, IG Reels, and FB Reels with per-campaign attribution.

**Flow:**
1. Look up product in `data/affiliate_products.json` (key → ASIN)
2. Build affiliate URL with `AMAZON_ASSOCIATES_TAG` + `ascsubtag=<campaign_id>` for per-campaign revenue tracking
3. Telegram kickoff approval
4. Reel prep via existing `--stage reel` pipeline (supports **product seeds** with inline captions so pre-written emotional copy bypasses LLM voice generation)
5. Telegram Reel preview approval
6. Publish to IG Reels (existing `publish_reel_to_instagram`)
7. Publish to FB Reels (new `publish_reel_to_facebook` — 3-phase upload, `media_type=REELS`)
8. Persist to `data/campaigns.json` (campaign_id, product, affiliate_url, both permalinks)

**Guardrails:**
- `affiliate_resolver.py` refuses to resolve `[AFFILIATE:*]` placeholders unless an FTC disclosure block is present
- `--min-gap-hours 72` (default) prevents back-to-back promotional Reels (IG+FB algos demote "promotional" accounts)
- Brand voice rules apply: IG caption stays URL-free (drives via bio); FB description embeds affiliate URL + WP link
- Amazon Associates tag lives in `.claude/settings.local.json` env, never in code

## Rate Limits

These are hard limits — agents abort gracefully if exceeded.

```yaml
facebook:
  comments_per_day: 5
  group_visits_per_day: 6
  group_posts_per_day: 3        # campaign/publisher share to groups
  group_join_requests_per_week: 3
  delay_between_comments: 30-120s random
  delay_between_group_visits: 45-180s random
  delay_between_group_posts: 60-180s random

instagram:
  likes_per_day: 8
  comments_per_day: 2
  delay_between_likes: 10-45s random
  delay_between_comments: 120-180s random

campaigns:
  min_gap_hours_between_reels: 72    # default for --stage campaign; bypass with --force
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
│   ├── settings.local.json — API credentials (env vars, gitignored)
│   ├── skills/             — 14 agent skill definitions (Claude Code slash-commands)
│   └── state/              — Session cookies, dedup cache, rate counters (gitignored)
├── config.json             — Rate limits, scoring weights, voice rules
├── requirements.txt        — Python dependencies
├── scripts/
│   ├── content_pipeline.py — main orchestrator: --stage ideate / enrich / publish / reel / campaign
│   ├── auto_drafter.py     — fills draft_comment on queued items
│   ├── reply_follower.py   — threaded replies to our FB comments
│   ├── fb_scan.py · ig_scan.py — scanners
│   ├── comment_poster.py   — posts approved queue items
│   ├── fb_group_post.py    — shares WP post to FB groups
│   ├── fb_group_note.py    — CLI for tracker note/mode updates
│   ├── fb_groups_posting_scan.py · fb_group_enrich.py — group classifier + enrichment
│   ├── fb_notification_scan.py — finds newly-approved group memberships
│   ├── fb_pending_posts_check.py — monitors pending-approval posts
│   └── fb_group_diagnose.py · fb_composer_diagnose.py — DOM diagnostic helpers
├── lib/
│   ├── notifier.py         — Telegram send / send_and_wait / send_video / request_approval
│   ├── comment_generator.py — templates + voice validator
│   ├── affiliate_resolver.py — [AFFILIATE:key] → Amazon URL with tag + ascsubtag
│   ├── thread_scraper.py   — FB DOM helpers for reply-follower
│   ├── rate_limiter.py · deduplication.py · logger.py
│   └── keyword_research.py · idea_learner.py
├── recipe-publisher/       — Reel + WP publish pipeline
│   ├── generators/         — recipe, carousel, reel (ffmpeg), music (Jamendo), narration
│   ├── publishers/         — wordpress, instagram (Reels + carousel), facebook (Reels)
│   ├── seeds/
│   │   ├── seeds.json      — recipe seeds
│   │   └── carousels/*.json — 4-slide configs; product seeds carry inline ig_caption
│   └── tests/              — 34+ pytest tests
├── data/
│   ├── groups_tracker.json    — FB groups we've joined + posting_mode + notes
│   ├── affiliate_products.json — Amazon ASIN catalog, keyed by slug
│   ├── campaigns.json        — active + historical affiliate campaigns
│   ├── post_templates.json · brand_voice_guide.md
│   └── site_content_cache.json
└── logs/                   — engagement_log.jsonl, cron_*.log, errors.log
```

## Required env (`.claude/settings.local.json`)

| Var | Purpose |
|---|---|
| `WP_URL` / `WP_USER` / `WP_APP_PASSWORD` | WordPress REST API |
| `FB_PAGE_ID` / `FB_PAGE_TOKEN` | FB Graph API (same token handles IG Reels + FB Reels) |
| `IG_ACCOUNT_ID` | Instagram Business ID |
| `FB_APP_ID` / `FB_APP_SECRET` | FB token refresh |
| `GEMINI_API_KEY` | Recipe voice + Imagen / Nano Pro image generation |
| `VOICE_PROVIDER` | Set to `gemini` to force the Gemini path over Anthropic |
| `JAMENDO_CLIENT_ID` | Reel music bed |
| `AMAZON_ASSOCIATES_TAG` | Affiliate attribution (e.g. `dogfoodfun01-20`) — required only for `--stage campaign` |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Approval gates (or set via `.claude/state/telegram_config.json`) |

## Error Handling

- **Rate limit hit** — abort gracefully, log to `logs/errors.log`, do not retry same day
- **Session expired** — log `SESSION_EXPIRED`, abort run
- **Post fails** — log error, mark as `FAILED` in dedup cache (not "engaged")

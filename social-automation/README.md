# DogFoodAndFun - Social Media Automation

Automated social media engagement system for [dogfoodandfun.com](https://dogfoodandfun.com).
Brand persona: **Nalla's Dad** - authentic, value-first dog owner who shares real experience.

## How It Works

Playwright-based agents scan Facebook groups and Instagram hashtags for posts relevant to dog food, GPS trackers, health, and training. High-scoring posts are queued for engagement. A sibling REST-based agent pulls held visitor comments from dogfoodandfun.com for moderation. Comments and replies are drafted in Nalla's Dad voice, validated against brand rules, sent to Telegram for approval, then posted automatically. All actions are rate-limited and logged.

## Skills

### Engagement Pipeline

| Skill | What it does | Schedule |
|---|---|---|
| `site-analyzer` | Crawls dogfoodandfun.com RSS feed, caches recent posts and keywords for comment context | Daily 3:00 PM |
| `fb-scanner` | Scans joined Facebook dog groups, scores posts by relevance, queues high-scoring ones | Daily 3:30 PM |
| `ig-scanner` | Scans Instagram hashtags, likes qualifying posts, queues top candidates for comments | Daily 7:00 PM |
| `wp-comment-handler` | Moderates held comments on dogfoodandfun.com — auto-trashes obvious spam, queues the rest for Telegram approval, approves + replies in one shot | Daily 9:00 PM |
| `comment-composer` | Drafts Nalla's Dad voice comments, validates tone, sends to Telegram for approval, posts | Daily 10:00 PM |
| `reply-follower` | Revisits recent FB comments, scrapes replies, drafts conversational responses, Telegram-approves, posts as threaded replies | On demand (`scripts/reply_follower.py`) |
| `activity-logger` | Logs every action (like, comment, join) to JSONL + Excel tracker | Called by all agents |

### Growth

| Skill | What it does | Schedule |
|---|---|---|
| `fb-group-scout` | Finds new dog-related Facebook groups (public + private), scores and presents for approval | 1st of month |
| `fb-group-publisher` | Publishes blog posts to relevant Facebook groups with tailored text per group category | On demand |

### Content Creation

| Skill | What it does | Schedule |
|---|---|---|
| `content-ideator` | Generates blog post ideas from content gaps, trends, and social discussions | On demand |
| `content-enricher` | Enriches ideas with SEO research, demand analysis, sends brief to Telegram for approval | On demand |
| `wp-post-creator` | Creates WordPress draft posts in Nalla's Dad voice from approved briefs | On demand |
| `fb-post-creator` | Publishes WordPress posts to Facebook page via Graph API | On demand |
| `ig-post-creator` | Publishes WordPress posts to Instagram with caption + hashtags via Graph API | On demand |
| `reel-publisher` | Generates a 9:16 IG Reel (AI slides + music bed), Telegram-approves, posts | On demand (`--stage reel --seed <id>`) |

### IG Reel pipeline (`content_pipeline.py --stage reel`)

End-to-end Reel publisher. One command goes from a seed id to a live IG Reel:

```bash
python scripts/content_pipeline.py --stage reel --seed pb-banana-biscuits
```

**Flow** (~3 min + the approval wait):

1. **Voice** — `generators/recipe.py::generate_recipe` runs Gemini against `prompts/recipe_system.md` + `prompts/ig_caption.md` to produce a compliant caption (hook → 3 bullet facts → `Comment RECIPE` CTA → question → branded hashtags). `_validate()` hard-rejects non-compliant captions.
2. **Slides** — `generators/carousel.py::generate_carousel_slides` renders 4 slides at 9:16 via Imagen / Nano Pro. Text overlays baked on with PIL. **Conversion hooks**: slide 1 gets an `@dogfoodandfun` follow badge in the corner; slide 4 gets a full-width `FULL RECIPE → DOGFOODANDFUN.COM` ribbon at the bottom.
3. **Music** — `generators/music.py::get_music_for_reel` hits the Jamendo API for an instrumental track (default tags: `acoustic+happy+upbeat`), picks one at random from the top 5, downloads the mp3.
4. **Compose** — `generators/reel.py::compose_reel` stitches slides into a 1080×1920 mp4 with 0.5s `xfade` crossfades, mixes the music bed (`apad`+`atrim` to video length), encodes H.264 yuv420p.
5. **Telegram preview** — `sendVideo` lands the mp4 in your chat, followed by a caption + `approve`/`skip` prompt that polls up to 12h.
6. **Publish** — `publishers/instagram.py::publish_reel_to_instagram` uploads the mp4 to WP media, creates a `media_type=REELS` container, polls status up to 5 min for Meta transcode, publishes, writes `last_ig_reel_post` to `.claude/state/publishing_timeline.json`.

Any failure routes through `skill_error` → Telegram push, exits 1. No partial publish.

**Required env** (in `.claude/settings.local.json` under `env`):

| Var | Purpose |
|---|---|
| `VOICE_PROVIDER` | `gemini` (skip the Anthropic default when out of credits) |
| `GEMINI_API_KEY` | voice + image generation |
| `JAMENDO_CLIENT_ID` | music bed |
| `IG_ACCOUNT_ID` / `FB_PAGE_TOKEN` | Reels publish |
| `WP_URL` / `WP_USER` / `WP_APP_PASSWORD` | video hosting |

**Available recipe seeds:** `pb-banana-biscuits`, `pumpkin-oat-biscuits`, `blueberry-yogurt-frozen-bites`, `chicken-bone-broth`, `sweet-potato-chews`, `turkey-rice-stew`. All 9:16.

**Product seeds** (used by `--stage campaign`): carry inline `ig_caption` + `title` in the carousel JSON so pre-written emotional copy bypasses LLM voice generation. Example: `fi-collar-gps-safety`.

### Affiliate campaign pipeline (`--stage campaign`)

A coordinated Amazon-affiliate push across WordPress, IG Reels, and FB Reels with per-campaign attribution.

```bash
python scripts/content_pipeline.py --stage campaign \
    --product fi-collar \
    --reel-seed fi-collar-gps-safety \
    --wp-url https://dogfoodandfun.com/dog-gps-tracker-comparison/
```

**Flow:**

1. **Product lookup** — key → ASIN via `data/affiliate_products.json`
2. **Affiliate URL build** — `https://www.amazon.com/dp/{ASIN}?tag={AMAZON_ASSOCIATES_TAG}&ascsubtag={campaign_id}` (subtag enables per-campaign revenue reporting in Associates dashboard)
3. **Gap guard** — refuses to launch if a Reel published <72h ago (default; override with `--force` or `--min-gap-hours 0`)
4. **Telegram kickoff approval** — preview of product + Reel seed + affiliate URL
5. **Reel prep** — same voice → slides → music → compose path as `--stage reel`; product seeds use inline captions (no LLM voice call)
6. **Telegram Reel preview approval**
7. **IG publish** via `publish_reel_to_instagram` (container → poll → publish)
8. **FB publish** via `publish_reel_to_facebook` (3-phase `video_reels` upload: start → transfer → finish+publish)
9. **Persist** — `data/campaigns.json` array entry with `campaign_id`, product, affiliate_url, both permalinks, start timestamp

**Related files:**

- `lib/affiliate_resolver.py` — `[AFFILIATE:key]` → Amazon URL. Refuses to resolve HTML missing a disclosure block (FTC requirement).
- `recipe-publisher/publishers/facebook.py::publish_reel_to_facebook` — FB Reels 3-phase upload
- `data/affiliate_products.json` — ASIN catalog (add new products here before referencing in WP posts)
- `data/campaigns.json` — state of record for all campaigns, past and present

**Required additional env:**

| Var | Purpose |
|---|---|
| `AMAZON_ASSOCIATES_TAG` | e.g. `dogfoodfun01-20`. Signup: https://affiliate-program.amazon.com |

### FB Groups management

Group-level tooling that sits under `fb-group-publisher`:

| Script | Purpose |
|---|---|
| `fb_notification_scan.py` | Scans FB notifications for newly-approved group memberships, populates `data/groups_tracker.json`. Scheduled Sunday 9:00. |
| `fb_group_enrich.py` | Per-group name / privacy / member-count scraper. Run after notification scan to fill in metadata. |
| `fb_groups_posting_scan.py` | Classifies each tracked group's `posting_mode`: `direct` (immediate post) / `admin_approval` (mod queue) / `admins_only` (blocked). Publishers skip non-`direct` groups. |
| `fb_group_post.py` | Posts a WP blog link to eligible groups. `--no-comment` skips the fragile auto-comment step; `--caption-override` bypasses the per-group template for custom captions. Respects `facebook:group_post` rate cap (3/day). |
| `fb_group_note.py` | CLI for manual status/mode/note updates: `--mode blocked` / `--status pending_approval` / `--note "..."` / `--list` to dump all tracker state. |
| `fb_pending_posts_check.py` | Revisits pending-approval posts, detects when they clear, prints + Telegram-pushes a "⏰ ADD FIRST COMMENT NOW" reminder with the permalink. |

**Tracker schema** (`data/groups_tracker.json`): array of entries with `group_name`, `group_url`, `status`, `posting_mode`, `member_count`, `privacy`, `last_post_at`, `last_post_status`, `last_post_caption`, `notes` (timestamped list).

### Operations

| Skill | What it does | Schedule |
|---|---|---|
| `performance-tracker` | Pulls engagement metrics across platforms, identifies top content, generates reports | On demand |
| `sheet-backup` | Backs up Google Sheet data and state files, 90-day retention | Weekly |

## Architecture

```
scripts/
  fb_scan.py            Playwright script - Facebook group scanner
  ig_scan.py            Playwright script - Instagram hashtag scanner
  wp_scan.py            httpx script - WordPress comment moderation scanner
  fb_group_scout.py     Playwright script - Facebook group finder
  comment_approver.py   Independent approver - Telegram approval flow
  comment_poster.py     Playwright + httpx - posts approved FB/IG comments and WP replies
  run_with_watchdog.py  Wrapper - detects stuck processes, alerts via Telegram
  status.py             Dashboard - schedule, rate limits, queue, errors

lib/
  comment_generator.py  Relevance scoring + voice validation
  rate_limiter.py       Daily hard limits per platform/action
  deduplication.py      60-day rolling cache to prevent re-engagement
  notifier.py           Telegram bot - notifications + comment approval flow
  logger.py             Timestamped unbuffered logging for monitoring

.claude/skills/         Skill definitions (Claude Code agent instructions)
.claude/state/          Runtime state (rate limits, dedup cache, queue, last run)
data/                   Templates, brand voice guide, site content cache
logs/                   Engagement log, error log, audit trail
```

## Rate Limits

| Platform | Action | Daily Limit |
|---|---|---|
| Facebook | Comments | 5 |
| Facebook | Group visits | 6 |
| Facebook | Join requests | 3/week |
| Instagram | Likes | 8 |
| Instagram | Comments | 2 |
| WordPress | Replies posted | 20 |

Random delays between all actions (30-180s) to reduce bot detection risk.

## Approval Gates

All comments go through Telegram before posting. Mandatory approval for:
- First comment in any new group
- Any comment containing a URL
- All Instagram comments
- All WordPress replies (they land on our own site under the Nalla's Dad byline)
- Borderline relevance scores (0.70-0.80)

WordPress moderation only: obvious spam (3+ links, known spam keywords, suspicious author TLDs) is auto-trashed before it ever hits the approval queue — moving to WP trash, not permanent delete, so false positives are recoverable from the admin UI.

## Setup

### Prerequisites

- Python 3.11+
- Playwright (`pip install playwright && playwright install chromium`)
- Browser logged into Facebook and Instagram (saved session cookies)
- Telegram bot for notifications (token in `.claude/state/telegram_config.json`)

### Install

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing
```

### Schedule (macOS launchd)

Agents run automatically via macOS LaunchAgents. No Claude Code session needed.

```bash
# Verify scheduled jobs
launchctl list | grep dogfoodandfun
```

### Run manually

```bash
python scripts/status.py                                          # dashboard
python scripts/run_with_watchdog.py scripts/fb_scan.py --timeout 180   # fb scanner
python scripts/run_with_watchdog.py scripts/ig_scan.py --timeout 180   # ig scanner
python scripts/wp_scan.py                                         # moderate site comments
python scripts/comment_approver.py                                # route queued items to Telegram
python scripts/comment_poster.py                                  # post approved FB/IG comments + WP replies
```

## CI/CD

GitHub Actions runs on every push to `social-automation/`:

- **Ruff** - lint + format check
- **Mypy** - type checking
- **Pytest** - 87 unit tests, 70% minimum coverage
- **Security scan** - checks for hardcoded credentials

```bash
# Run locally
ruff check lib/ scripts/ tests/
ruff format --check lib/ scripts/ tests/
pytest --cov=lib --cov-report=term-missing
```

## Monitoring

- **Telegram** - real-time notifications on skill start/finish/error/skip
- **Watchdog** - kills stuck Playwright processes, sends screenshot + alert
- **Status dashboard** - `python scripts/status.py`
- **Logs** - `logs/engagement_log.jsonl`, `logs/errors.log`

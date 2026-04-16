# DogFoodAndFun - Social Media Automation

Automated social media engagement system for [dogfoodandfun.com](https://dogfoodandfun.com).
Brand persona: **Nalla's Dad** - authentic, value-first dog owner who shares real experience.

## How It Works

Playwright-based agents scan Facebook groups and Instagram hashtags for posts relevant to dog food, GPS trackers, health, and training. High-scoring posts are queued for engagement. Comments are drafted in Nalla's Dad voice, validated against brand rules, sent to Telegram for approval, then posted automatically. All actions are rate-limited and logged.

## Skills

### Engagement Pipeline

| Skill | What it does | Schedule |
|---|---|---|
| `site-analyzer` | Crawls dogfoodandfun.com RSS feed, caches recent posts and keywords for comment context | Daily 3:00 PM |
| `fb-scanner` | Scans joined Facebook dog groups, scores posts by relevance, queues high-scoring ones | Daily 3:30 PM |
| `ig-scanner` | Scans Instagram hashtags, likes qualifying posts, queues top candidates for comments | Daily 7:00 PM |
| `comment-composer` | Drafts Nalla's Dad voice comments, validates tone, sends to Telegram for approval, posts | Daily 10:00 PM |
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
  fb_group_scout.py     Playwright script - Facebook group finder
  comment_poster.py     Playwright script - posts approved comments
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

Random delays between all actions (30-180s) to reduce bot detection risk.

## Approval Gates

All comments go through Telegram before posting. Mandatory approval for:
- First comment in any new group
- Any comment containing a URL
- All Instagram comments
- Borderline relevance scores (0.70-0.80)

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
python scripts/comment_poster.py                                  # post comments
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

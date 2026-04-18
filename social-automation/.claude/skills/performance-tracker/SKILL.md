---
name: performance-tracker
description: >
  Track content performance across WordPress, Facebook, and Instagram. Pulls
  engagement metrics, identifies top-performing content, detects trends, and
  generates monthly reports with actionable recommendations. Feeds insights
  back to content-enricher and content-ideator for data-driven planning.
  Use when: "how did my posts perform", "content report", "monthly metrics",
  "what's working", "engagement stats", "performance review", "analytics".
---

# Performance Tracker Skill

Tracks content performance across WordPress, Facebook, and Instagram. Pulls engagement metrics, identifies top-performing content, detects trends, and generates monthly reports with actionable recommendations.

## Key Context

- **Facebook Page ID**: 949003594968019
- **Instagram Business ID**: 17841480087869644
- **Credentials**: `.claude/state/social_api_config.json`
- **Engagement log**: `logs/engagement_log.jsonl`
- **Site**: dogfoodandfun.com

## Workflow

### Step 1 — Determine Report Period

Default: last 30 days. Can be overridden by user ("last week", "March report", etc.)
Calculate `start_date` and `end_date`.

### Step 2 — Gather Facebook Page Metrics

Use Facebook Graph API:

```python
import requests, json

config = json.load(open('.claude/state/social_api_config.json'))
fb = config['facebook']

# Get page posts with engagement
response = requests.get(
    f"https://graph.facebook.com/v23.0/{fb['page_id']}/posts",
    params={
        'fields': 'id,message,created_time,likes.summary(true),comments.summary(true),shares',
        'since': start_timestamp,
        'until': end_timestamp,
        'limit': 50,
        'access_token': fb['access_token']
    }
)

# For each post, extract:
# - post_id, message preview, created_time
# - likes count, comments count, shares count
# - engagement rate = (likes + comments + shares) / reach (if available)
```

Also get page-level insights:

```python
# Page impressions, reach, followers gained
insights_response = requests.get(
    f"https://graph.facebook.com/v23.0/{fb['page_id']}/insights",
    params={
        'metric': 'page_impressions,page_engaged_users,page_fans',
        'period': 'day',
        'since': start_timestamp,
        'until': end_timestamp,
        'access_token': fb['access_token']
    }
)
```

### Step 3 — Gather Instagram Metrics

```python
ig = config['instagram']

# Get recent media with insights
media_response = requests.get(
    f"https://graph.facebook.com/v23.0/{ig['business_account_id']}/media",
    params={
        'fields': 'id,caption,timestamp,like_count,comments_count,media_type,permalink',
        'limit': 50,
        'access_token': ig['access_token']
    }
)

# For each post within period:
# - likes, comments, saves (if available)
# - caption preview
# - media type (IMAGE, VIDEO, CAROUSEL)

# Profile insights
profile_response = requests.get(
    f"https://graph.facebook.com/v23.0/{ig['business_account_id']}",
    params={
        'fields': 'followers_count,media_count',
        'access_token': ig['access_token']
    }
)
```

### Step 4 — Cross-Reference with Publishing Log

Read `logs/engagement_log.jsonl` and match entries within the report period:
- Map `fb_post_id` → source WordPress post URL
- Map `ig_post_id` → source WordPress post URL
- Calculate: which blog post topics drove the most social engagement?
- Which categories perform best on which platform?

### Step 5 — Analyze Patterns

Generate insights:

**Top Performers:**
- Top 3 FB posts by engagement (likes + comments + shares)
- Top 3 IG posts by engagement (likes + comments)
- Which blog post categories drive the most social engagement?

**Trends:**
- Engagement trend (improving/declining/flat week-over-week)
- Best posting day of week
- Best posting time of day
- Follower growth rate

**Category Performance:**

```
Category          | FB Avg Eng. | IG Avg Eng. | Best Platform
Food & Diet       | 45          | 120         | Instagram
Lifestyle & Gear  | 62          | 85          | Facebook
Training          | 38          | 95          | Instagram
Grooming          | 55          | 70          | Facebook
```

**Hashtag Analysis (IG only):**
- Which hashtag sets correlate with higher engagement?
- Recommend hashtag adjustments for next month

### Step 6 — Generate Recommendations

Based on the data, produce 3-5 actionable recommendations:

- "Food & Diet posts get 2x engagement on Instagram — prioritize IG for food content"
- "Tuesday posts consistently outperform Friday posts — shift publishing schedule"
- "Posts with Nalla-specific stories get 40% more comments — increase personal anecdotes"
- "GPS tracker content has declining engagement — consider fresher angles or new products"
- "Hashtag #homemadedogfood drives 3x more reach than #dogfoodreview — swap in rotation"

### Step 7 — Save Performance Data

Save to `.claude/state/performance_history.json`:

```json
{
  "report_period": { "start": "...", "end": "..." },
  "generated_at": "ISO timestamp",
  "facebook": {
    "total_posts": 12,
    "total_engagement": 540,
    "avg_engagement_per_post": 45,
    "top_post": { "id": "...", "message": "...", "engagement": 120 },
    "follower_count": 1234,
    "follower_change": "+45"
  },
  "instagram": {
    "total_posts": 8,
    "total_engagement": 960,
    "avg_engagement_per_post": 120,
    "top_post": { "id": "...", "caption": "...", "engagement": 250 },
    "follower_count": 2345,
    "follower_change": "+120"
  },
  "category_performance": { ... },
  "recommendations": [ ... ],
  "hashtag_performance": { ... }
}
```

This file is consumed by `content-enricher` and `content-ideator` to prioritize high-performing categories.

### Step 8 — Send Report

**Option A: Telegram summary** (default)

```
📊 Monthly Performance Report — March 2026

📘 Facebook Page
Posts: 12 | Avg Engagement: 45 | Followers: 1,234 (+45)
Top: "Nalla's GPS tracker saved us on a forest trail..." (120 eng)

📸 Instagram
Posts: 8 | Avg Engagement: 120 | Followers: 2,345 (+120)
Top: "The real cost of fresh dog food..." (250 eng)

🏆 Best Category: Food & Diet (IG) | Lifestyle & Gear (FB)
📈 Trend: Engagement up 15% vs last month

Recommendations:
1. Prioritize food content on IG (2x engagement)
2. Shift FB posting to Tuesdays (best day)
3. Increase Nalla stories (40% more comments)

Full report saved to performance_history.json
```

**Option B: Full markdown report file** (if user requests)

Generate a detailed .md report and save to `logs/reports/performance_{year}_{month}.md`

## Scheduling

Recommended: Monthly on the 1st, 9am Israel time.
Can also run on-demand ("how did last week go?").

## Error Handling

- **FB/IG token expired** → report only from `engagement_log.jsonl` (partial data), flag token issue
- **API rate limit** → wait and retry (Graph API is generous for page insights)
- **No data for period** → report "No publishing activity" with recommendation to increase cadence
- **engagement_log.jsonl missing** → rely on API data only, warn about missing cross-reference

## Dependencies

- `.claude/state/social_api_config.json` (API credentials)
- `logs/engagement_log.jsonl` (publishing history)
- `lib/notifier.py` (Telegram)
- `data/content_rules.json` (categories for grouping)

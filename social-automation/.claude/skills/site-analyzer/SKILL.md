---
name: site-analyzer
description: >
  Crawl dogfoodandfun.com (or any site in config.json) and build a fresh content
  cache of recent posts, categories, and keywords. Run FIRST before fb-scanner,
  ig-scanner, or comment-composer to ensure comments reference current site content.
  Updates data/site_content_cache.json with post titles, summaries, URLs, tags.
  Use when the user says "analyze site", "sync site content", "update site cache",
  "run site analyzer", "refresh content cache", or automatically before any scan run.
---

# Site Analyzer — DogFoodAndFun

Pre-run step for all agents. Crawls the site, extracts recent posts + metadata,
and saves to `data/site_content_cache.json`. This lets comment-composer reference
actual current content (not stale templates) for natural, relevant comments.

---

## When to Run

Run automatically at the start of each agent session **if** the cache is older than
`config.json → content_analysis.site_cache_ttl_hours` (default: 12 hours).

```python
import json
from datetime import datetime, timedelta
from pathlib import Path

config = json.loads(Path('../config.json').read_text())
cache_file = Path('../data/site_content_cache.json')

cache_is_stale = True
if cache_file.exists():
    cache = json.loads(cache_file.read_text())
    cached_at = cache.get('cached_at')
    if cached_at:
        age = datetime.utcnow() - datetime.fromisoformat(cached_at.replace('Z', ''))
        ttl = timedelta(hours=config['content_analysis']['site_cache_ttl_hours'])
        cache_is_stale = age > ttl
        if not cache_is_stale:
            print(f"Site cache is fresh ({age} old). Skipping site analysis.")
            # Exit skill — cache is good

if not cache_is_stale:
    # No action needed — exit
    pass
else:
    print("Site cache is stale or missing. Running site analysis...")
```

---

## Step 1 — Read Config

```python
site_url = config['site']['url']
rss_url = config['site']['rss_feed']
persona = config['site']['brand_persona']
mascot = config['site']['mascot_name']
max_posts = config['content_analysis']['site_cache_max_posts']
keywords = config['content_analysis']['keywords']
```

---

## Step 2 — Fetch RSS Feed

Navigate to the RSS feed URL. This is the most reliable way to get recent posts.

```
{site_url}/feed/
```

Use `get_page_text` to read the XML. Extract from RSS:
- `<title>` — post title
- `<link>` — post URL
- `<description>` or `<content:encoded>` — post excerpt/content
- `<pubDate>` — publication date
- `<category>` — post categories/tags

Parse the XML to extract up to `max_posts` (50) most recent posts.

If RSS fails (404 or empty), fall back to:
- Fetch `{site_url}/sitemap.xml` and extract post URLs
- Or navigate to `{site_url}/blog/` and extract post links via JS

---

## Step 3 — Fetch Each Recent Post

For posts published in the last **30 days** (or top 20 if fewer recent), navigate to each URL and extract:

```
Fields to extract per post:
- title
- url
- published_date
- categories (list)
- tags (list)
- excerpt (first 300 chars of content)
- keywords_found (which config keywords appear in the post)
- word_count (approximate)
- has_product_review: true if post contains a product name from keywords.brands_reviewed
- reviewed_products: list of brand names mentioned
```

Use `get_page_text` on each post URL. No screenshots needed.

Keyword matching:
```python
def find_keywords(text: str, keywords_config: dict) -> dict:
    text_lower = text.lower()
    found = {}
    for category, kw_list in keywords_config.items():
        matched = [kw for kw in kw_list if kw in text_lower]
        if matched:
            found[category] = matched
    return found
```

---

## Step 4 — Analyze Social Media Profiles

### Facebook Page

Navigate to: `{config['social_channels']['facebook']['page_url']}`

Extract:
- Latest 5 post titles/excerpts and their engagement (likes + comments count)
- Page follower count
- Most recent post date

### Instagram Profile

Navigate to: `{config['social_channels']['instagram']['profile_url']}`

Extract:
- Latest 6 post captions (first 150 chars each)
- Follower count
- Most recent post date

---

## Step 5 — Build Content Summary

Synthesize what's been posted recently across all channels:

```python
content_summary = {
    "site": {
        "recent_topics": [],   # deduplicated list of topics covered in last 30 days
        "content_gaps": [],    # keywords in config with NO recent posts in last 30 days
        "most_recent_post": "", # title + date of most recent post
    },
    "facebook": {
        "last_post_date": "",
        "recent_post_previews": [],
        "follower_count": ""
    },
    "instagram": {
        "last_post_date": "",
        "recent_post_previews": [],
        "follower_count": ""
    }
}
```

Content gap detection:
```python
# Find keyword categories with no recent coverage
for category, kw_list in keywords.items():
    covered = any(
        category in post.get('keywords_found', {})
        for post in recent_posts
    )
    if not covered:
        content_summary['site']['content_gaps'].append(category)
```

---

## Step 6 — Save Cache

```python
cache = {
    "cached_at": datetime.utcnow().isoformat() + "Z",
    "site_url": site_url,
    "site_name": config['site']['name'],
    "recent_posts": [
        {
            "title": post["title"],
            "url": post["url"],
            "published_date": post["published_date"],
            "categories": post["categories"],
            "tags": post["tags"],
            "excerpt": post["excerpt"][:300],
            "keywords_found": post["keywords_found"],
            "reviewed_products": post.get("reviewed_products", []),
        }
        for post in recent_posts[:max_posts]
    ],
    "content_summary": content_summary,
    "social_media": {
        "facebook": content_summary["facebook"],
        "instagram": content_summary["instagram"],
    }
}

cache_file.write_text(json.dumps(cache, indent=2))
print(f"Site cache updated: {len(recent_posts)} posts, {len(content_summary['site']['content_gaps'])} content gaps found")
```

---

## Step 7 — Report

```
=== Site Analysis Complete ===
Site: dogfoodandfun.com
Cache updated: {timestamp}

Recent posts (last 30 days): {count}
  Latest: "{title}" ({date})
  Topics covered: food, GPS, health...

Content gaps (no recent posts):
  - training  ← opportunity
  - canicross ← opportunity

Facebook: {follower_count} followers | Last post: {date}
Instagram: {follower_count} followers | Last post: {date}

Cache saved to: data/site_content_cache.json
```

---

## Error Handling

- **RSS fetch fails** → fall back to sitemap → fall back to blog page HTML extraction
- **Individual post fetch fails** → skip that post, log URL to errors.log, continue
- **Social profile blocked (login required)** → skip social analysis, log "SOCIAL_PROFILE_BLOCKED", proceed with site-only cache
- **Cache write fails** → log error, continue with in-memory cache for current session

---

## Config Portability

To adapt this skill for another website:
1. Edit `config.json` — update `site.url`, `site.rss_feed`, `site.brand_persona`
2. Update `content_analysis.keywords` with relevant keyword categories
3. Update `social_channels` with the correct profile URLs
4. Everything else adapts automatically

No code changes needed for a different site — config.json is the single source of truth.

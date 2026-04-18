---
name: ig-post-creator
description: >
  Create and publish Instagram feed posts from published WordPress blog posts.
  Generates Nalla's Dad caption with 6-8 hashtags, fetches image via Pexels API,
  publishes via Instagram Graph API (container → publish flow), generates optional
  reel script. Use when: "post to instagram", "create IG post", "share on instagram",
  "publish to IG", "instagram post from blog".
---

# Instagram Post Creator Skill

Creates and publishes Instagram feed posts from published WordPress blog posts with auto-generated captions, image sourcing via Pexels, and optional reel script generation.

## Workflow

### Step 1 — Identify the Source Post

Identify the WordPress post to share:
- User provides WP post URL, OR
- Read Google Sheet "posts" tab → next "wp_published" row, OR
- Read `.claude/state/wp_posts_cache.json`

Fetch post content via Chrome browser or WP API.

### Step 2 — Load Config & Rules

Load required configuration files:
- Read `data/content_rules.json` → social_post_rules.instagram section
- Read `config.json` → Instagram config, rate limits
- Read `.claude/state/social_api_config.json`:

```json
{
  "instagram": {
    "business_account_id": "17841480087869644",
    "access_token": "YOUR_FB_PAGE_ACCESS_TOKEN"
  },
  "pexels": {
    "api_key": "YOUR_PEXELS_API_KEY"
  }
}
```

**Note:** Instagram Graph API uses the same Facebook Page access token.

### Step 2.5 — Check Publishing Timeline

Before generating content, check the publishing timeline to ensure proper spacing:

1. Read `.claude/state/publishing_timeline.json`
2. Check `last_fb_page_post` — if less than 4 hours ago, WAIT or abort
3. Check `last_ig_feed_post` — if less than 4 hours ago, WAIT
4. Instagram optimal posting times are 6-8pm EST / 1-3am Israel
5. After posting, update publishing_timeline.json with new timestamp

```python
import json
from datetime import datetime, timedelta
from pathlib import Path

timeline_file = Path('.claude/state/publishing_timeline.json')
timeline = json.loads(timeline_file.read_text()) if timeline_file.exists() else {}

last_fb = timeline.get('last_fb_page_post')
if last_fb:
    fb_age = datetime.utcnow() - datetime.fromisoformat(last_fb.replace('Z', ''))
    if fb_age < timedelta(hours=4):
        print(f"FB page post was {fb_age} ago — too recent. Wait at least 4h gap.")
        # Either wait or abort and schedule for later

last_ig = timeline.get('last_ig_feed_post')
if last_ig:
    ig_age = datetime.utcnow() - datetime.fromisoformat(last_ig.replace('Z', ''))
    if ig_age < timedelta(hours=4):
        print(f"IG post was {ig_age} ago — too recent.")
```

After successful publish, update timeline:
```python
timeline['last_ig_feed_post'] = datetime.utcnow().isoformat() + 'Z'
timeline_file.write_text(json.dumps(timeline, indent=2))
```

### Step 3 — Fetch Image via Pexels

Instagram REQUIRES an image. Fetch from Pexels:

```python
import requests

pexels_key = config['pexels']['api_key']

# Build search query from post keywords
keywords = extract_keywords(post_title, post_content)
search_query = ' '.join(keywords[:3])

response = requests.get(
    'https://api.pexels.com/v1/search',
    params={
        'per_page': 1,
        'orientation': 'landscape',
        'query': search_query
    },
    headers={'Authorization': pexels_key}
)

if response.status_code == 200 and response.json()['photos']:
    image_url = response.json()['photos'][0]['src']['large']
else:
    # Fallback: use WordPress featured image
    image_url = extract_featured_image(post_html)
```

**IMPORTANT:** The image_url MUST be publicly accessible (not behind auth). Pexels URLs work. WordPress featured images usually work if the site is public.

### Step 4 — Generate Instagram Caption

Follow content_rules.json instagram rules:

**Format:** Hook → Story snippet → Key takeaway → "Link in bio" → Hashtags

**Requirements:**
- 80-150 words for caption body
- 6-8 hashtags (NOT 20-30, this is a hard rule)
- Must include catchy first line (this shows in feed)
- Must include "Link in bio" CTA
- Must include engagement question
- No medical claims, no sales language

**Caption structure:**

```
[Hook — catchy first line, often a question or surprising statement]

[Story snippet — 2-3 sentences connecting to Nalla's experience]

[Key takeaway — main insight from the blog post]

[Engagement question]

🔗 Full guide in bio!

#hashtag1 #hashtag2 #hashtag3 #hashtag4 #hashtag5 #hashtag6
```

**Hashtag Selection:**
- 2-3 niche hashtags from config (e.g., #homemadedogfood, #dognutrition)
- 2-3 topic-specific hashtags (e.g., #dogGPStracker, #canicross)
- 1-2 broader hashtags (e.g., #doglife, #dogsofinstagram)
- NEVER exceed 8 hashtags total
- Trim excess hashtags if generated with more

### Step 5 — Generate Reel Script (Optional)

If user wants a reel:
- 30 seconds, 60-80 words
- Structure: HOOK (5s) → MAIN POINT (20s) → CTA (5s)
- Spoken, conversational tone
- Save to `.claude/state/reel_scripts/` for later recording

### Step 5.5 — Brand Voice Validation

Before sending to Telegram for approval, run automated quality checks:

1. Check against blocked terms from `data/content_rules.json` → quality_gates.brand_voice_review
2. Verify required elements are present (nalla_mention, engagement_question, link_in_bio_cta, hashtags_6_to_8)
3. Validate hashtag count (6-8 hashtags required)
4. If violations found → auto-fix and re-generate (max 2 retries)
5. If still failing → flag violations in the Telegram message

```python
import json

rules = json.load(open('data/content_rules.json'))
gates = rules['quality_gates']['brand_voice_review']

violations = []

# Check blocked medical terms
for term in gates['blocked_medical_terms']:
    if term.lower() in caption_text.lower():
        violations.append(f"Medical term: '{term}'")

# Check blocked salesy phrases
for phrase in gates['blocked_salesy_phrases']:
    if phrase.lower() in caption_text.lower():
        violations.append(f"Salesy phrase: '{phrase}'")

# Check blocked generic openers
for opener in gates['blocked_generic_openers']:
    if caption_text.lower().startswith(opener.lower()):
        violations.append(f"Generic opener: '{opener}'")

# Check required elements
required = gates['required_elements']['instagram']
if 'nalla_mention' in required and 'nalla' not in caption_text.lower():
    violations.append("Missing: Nalla mention")
if 'engagement_question' in required and '?' not in caption_text:
    violations.append("Missing: Engagement question")
if 'link_in_bio_cta' in required and 'link in bio' not in caption_text.lower():
    violations.append("Missing: Link in bio CTA")

# Validate hashtag count
hashtags = [word for word in caption_text.split() if word.startswith('#')]
if len(hashtags) > 8:
    violations.append(f"Too many hashtags: {len(hashtags)} (max 8)")
    # Auto-trim to first 8
if len(hashtags) < 6:
    violations.append(f"Too few hashtags: {len(hashtags)} (min 6)")

if violations:
    print(f"Brand voice violations found: {violations}")
    # Auto-fix: regenerate with explicit constraints
    # If still failing after 2 retries, flag in Telegram
```

### Step 6 — Telegram Approval

Request user approval via Telegram:

```
📸 Instagram Post Preview

Title: {post_title}
Caption:
---
{generated_caption}
---
Image: {image_url}
Hashtags: {count}
Word count: {word_count}

Reel Script: {yes/no}

Reply: approve / skip / edit:[your changes]
```

### Step 7 — Publish via Instagram Graph API

Instagram publishing is a 2-step process:

**Step 7a — Create Media Container:**

```python
ig_config = config['instagram']

# Create container
container_response = requests.post(
    f"https://graph.facebook.com/v23.0/{ig_config['business_account_id']}/media",
    data={
        'image_url': image_url,
        'caption': full_caption_with_hashtags,
        'access_token': ig_config['access_token']
    }
)

container_id = container_response.json()['id']
```

**Step 7b — Wait for Container to be Ready:**

```python
import time

for attempt in range(10):
    status_response = requests.get(
        f"https://graph.facebook.com/v23.0/{container_id}",
        params={
            'fields': 'status_code',
            'access_token': ig_config['access_token']
        }
    )
    status = status_response.json().get('status_code')
    if status == 'FINISHED':
        break
    elif status == 'ERROR':
        raise Exception(f"Container creation failed: {status_response.json()}")
    time.sleep(3)  # Wait 3 seconds between checks
```

**Step 7c — Publish:**

```python
publish_response = requests.post(
    f"https://graph.facebook.com/v23.0/{ig_config['business_account_id']}/media_publish",
    data={
        'creation_id': container_id,
        'access_token': ig_config['access_token']
    }
)

if publish_response.status_code == 200:
    ig_post_id = publish_response.json()['id']
    print(f"Published to Instagram: {ig_post_id}")
```

### Step 8 — Log & Update Status

Log to engagement log:

```python
log_entry = {
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "platform": "instagram",
    "action": "feed_post",
    "source_post": post_url,
    "ig_post_id": ig_post_id,
    "caption_length": len(caption_text.split()),
    "hashtag_count": hashtag_count,
    "image_source": "pexels",
    "pexels_query": search_query,
    "status": "SUCCESS"
}
```

Update Google Sheet status: if both FB and IG are done → "social_done", if only IG → "ig_posted"

Send Telegram success notification.

### Step 9 — Save Reel Script (if generated)

Save reel script to `.claude/state/reel_scripts/{slug}.json` for later recording.

## Rate Limits

- Maximum 2 feed posts per day (Instagram is stricter)
- Minimum 4 hour gap between posts
- Track in rate_limit_tracker.json

## Error Handling

- **IG token expired:** Log "IG_TOKEN_EXPIRED", notify user, abort
- **Container creation fails:** Log error with full response, retry once
- **Container stuck in PROCESSING:** Wait up to 30 seconds, then abort
- **Image URL not accessible:** Try WordPress featured image fallback → abort if both fail
- **Pexels API fails:** Fall back to WordPress featured image
- **Rate limit hit:** Log, schedule for next available slot

## Important Notes on Instagram Graph API

- The access_token is the SAME as the Facebook Page access token (Instagram is managed through Facebook)
- The image_url MUST be publicly accessible (no auth required to download)
- Container creation is async — you MUST poll for status before publishing
- Caption + hashtags go in a single 'caption' field
- Instagram does NOT support link previews like Facebook — the link goes in bio, not in the post

## Credential Config

Expects credentials in `.claude/state/social_api_config.json`:

```json
{
  "instagram": {
    "business_account_id": "17841480087869644",
    "access_token": "SAME_AS_FB_PAGE_ACCESS_TOKEN"
  },
  "pexels": {
    "api_key": "YOUR_PEXELS_API_KEY"
  }
}
```

## Activation Trigger

Use this skill when the user requests:
- "post to instagram"
- "create IG post"
- "share on instagram"
- "publish to IG"
- "instagram post from blog"

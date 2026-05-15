---
name: fb-post-creator
description: >
  Create and publish Facebook page posts from published WordPress blog posts.
  Generates {{brand.persona}} voice caption (150-200 words, no hashtags), fetches
  post image, publishes via Facebook Graph API, logs to engagement log.
  Use when: "post to facebook", "create FB post", "share on facebook",
  "publish to FB page", "facebook post from blog".
---

# Facebook Post Creator Skill

## Overview
This skill automates the creation and publication of Facebook page posts from WordPress blog posts, generating engaging captions in "{{brand.persona}}" voice and handling image management via the Facebook Graph API.

## Workflow

### Step 1 — Identify the Source Post
Either:
- User provides a WordPress post URL directly
- OR: Read Google Sheet "posts" tab, find next row with Status = "wp_published"
- OR: Read `.claude/state/wp_posts_cache.json` for recent published posts

For the selected post, fetch its content:
- Navigate to the post URL via Chrome browser
- Extract: title, full text content, categories, featured image URL
- OR: Fetch via WordPress REST API if credentials available

### Step 2 — Load Config & Rules
- Read `data/content_rules.json` → social_post_rules.facebook section
- Read `config.json` → social channels, rate limits
- Read `.claude/state/social_api_config.json` → Facebook Graph API credentials:
```json
{
  "facebook": {
    "page_id": "949003594968019",
    "access_token": "YOUR_FB_PAGE_ACCESS_TOKEN"
  }
}
```

### Step 2.5 — Check Publishing Timeline

Before generating content, check the publishing timeline to ensure proper spacing:

1. Read `.claude/state/publishing_timeline.json`
2. Check `last_ig_feed_post` — if less than 4 hours ago, WAIT or abort
3. Check `last_fb_page_post` — if less than 2 hours ago, WAIT
4. Facebook page posts should go out in the morning (8-10am EST / 3-5pm Israel)
5. After posting, update publishing_timeline.json with new timestamp

```python
import json
from datetime import datetime, timedelta
from pathlib import Path

timeline_file = Path('.claude/state/publishing_timeline.json')
timeline = json.loads(timeline_file.read_text()) if timeline_file.exists() else {}

last_ig = timeline.get('last_ig_feed_post')
if last_ig:
    ig_age = datetime.utcnow() - datetime.fromisoformat(last_ig.replace('Z', ''))
    if ig_age < timedelta(hours=4):
        print(f"IG post was {ig_age} ago — too recent. Wait at least 4h gap.")
        # Either wait or abort and schedule for later

last_fb = timeline.get('last_fb_page_post')
if last_fb:
    fb_age = datetime.utcnow() - datetime.fromisoformat(last_fb.replace('Z', ''))
    if fb_age < timedelta(hours=2):
        print(f"FB page post was {fb_age} ago — too recent.")
```

After successful publish, update timeline:
```python
timeline['last_fb_page_post'] = datetime.utcnow().isoformat() + 'Z'
timeline_file.write_text(json.dumps(timeline, indent=2))
```

### Step 3 — Generate Facebook Post Caption
Following content_rules.json facebook rules:

**Format:** Hook line → Personal story → Key insight → Question for community

**Requirements:**
- 150-200 words
- NO hashtags (Facebook algorithm deprioritizes them)
- Must include {{brand.mascot}} reference
- Must end with engagement question
- Hook in the FIRST LINE (this shows in feed preview)
- Maximum 1 emoji
- Never sound like an ad
- Never use medical/clinical language

**Template structure:**
```
[Hook — first line that grabs attention, often a question or bold statement]

[Personal story — 2-3 sentences about experience with {{brand.mascot}} related to the topic]

[Key insight — the main takeaway from the blog post, data-driven]

[Engagement question — genuine question to spark discussion]

📝 Full breakdown: [post URL]
```

### Step 4 — Extract/Fetch Image
Priority order for post image:
1. Featured image from the WordPress post (extract URL from post HTML)
2. If no featured image: search Pexels API for a relevant landscape image:

```python
import requests

pexels_config = json.load(open('.claude/state/social_api_config.json'))
api_key = pexels_config['pexels']['api_key']

# Extract keywords from post title/content
keywords = extract_keywords(post_title, post_content)
search_query = ' '.join(keywords[:3])

response = requests.get(
    'https://api.pexels.com/v1/search',
    params={'per_page': 1, 'orientation': 'landscape', 'query': search_query},
    headers={'Authorization': api_key}
)
image_url = response.json()['photos'][0]['src']['large']
```

### Step 4.5 — Brand Voice Validation

Before sending to Telegram for approval, run automated quality checks:

1. Check against blocked terms from `data/content_rules.json` → quality_gates.brand_voice_review
2. Verify required elements are present (nalla_mention, engagement_question, hook_first_line)
3. If violations found → auto-fix and re-generate (max 2 retries)
4. If still failing → flag violations in the Telegram message

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
required = gates['required_elements']['facebook']
if 'nalla_mention' in required and 'nalla' not in caption_text.lower():
    violations.append("Missing: {{brand.mascot}} mention")
if 'engagement_question' in required and '?' not in caption_text:
    violations.append("Missing: Engagement question")

if violations:
    print(f"Brand voice violations found: {violations}")
    # Auto-fix: regenerate with explicit constraints
    # If still failing after 2 retries, flag in Telegram
```

### Step 5 — Telegram Approval (auto-polling)
Send preview and automatically wait for reply using `send_and_wait()`:

```python
from notifier import send_and_wait

preview_msg = f"""📘 Facebook Post Preview\nTitle: {post_title}\nCaption:\n---\n{generated_caption}\n---\nWord count: {word_count}\n\nReply: approve · skip · edit [your changes]"""

# This automatically polls Telegram and returns when user replies
result = send_and_wait(preview_msg, timeout_hours=24)

if result["action"] == "approved":
    pass  # proceed to publish
elif result["action"] == "edited":
    generated_caption = result["edit_text"]
elif result["action"] in ("skipped", "timeout"):
    exit(0)
```

**IMPORTANT:** Always use `send_and_wait()` — never send and stop.

### Step 6 — Publish via Facebook Graph API
```python
import requests

fb_config = json.load(open('.claude/state/social_api_config.json'))['facebook']

# Post with photo
response = requests.post(
    f"https://graph.facebook.com/v23.0/{fb_config['page_id']}/photos",
    data={
        'url': image_url,
        'message': caption_text,
        'access_token': fb_config['access_token']
    }
)

if response.status_code == 200:
    post_id = response.json().get('id') or response.json().get('post_id')
    print(f"Published to Facebook: {post_id}")
else:
    print(f"Facebook publish failed: {response.json()}")
```

If no image available, post text-only:
```python
response = requests.post(
    f"https://graph.facebook.com/v23.0/{fb_config['page_id']}/feed",
    data={
        'message': caption_text,
        'link': post_url,
        'access_token': fb_config['access_token']
    }
)
```

### Step 7 — Log & Update Status
Log to engagement log:
```python
import json
from datetime import datetime

log_entry = {
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "platform": "facebook",
    "action": "page_post",
    "source_post": post_url,
    "fb_post_id": post_id,
    "caption_length": len(caption_text.split()),
    "image_source": "wordpress_featured" or "pexels",
    "status": "SUCCESS" or "FAILED"
}

with open('logs/engagement_log.jsonl', 'a') as f:
    f.write(json.dumps(log_entry) + '\n')
```

Update Google Sheet: if both FB and IG are done → Status = "social_done", if only FB → Status = "fb_posted"

Send Telegram success notification with link to the Facebook post.

### Step 8 — Also Post to Facebook Groups (Optional)
If user confirms, also trigger the existing `fb-group-publisher` skill to share to eligible groups. This is SEPARATE from the page post — groups get a different, shorter message and follow the existing group posting rules.

IMPORTANT: Wait at least 3 hours after the page post before triggering fb-group-publisher. Read `publishing_timeline.json` to check timing. Update `last_fb_group_share` after group posts.

## Rate Limits
- Maximum 3 page posts per day (Facebook recommends 1-2)
- Minimum 2 hour gap between posts
- Track in `.claude/state/rate_limit_tracker.json`

## Error Handling
- FB token expired → log "FB_TOKEN_EXPIRED", notify user, abort
- Image upload fails → retry once, then post text-only with link
- Rate limit hit → log, schedule for next available slot
- Post content too long → truncate to 200 words, log warning
- Approval timeout → mark as "pending_approval", retry next run

## Credential Configuration
Expects in `.claude/state/social_api_config.json`:
```json
{
  "facebook": {
    "page_id": "949003594968019",
    "access_token": "YOUR_LONG_LIVED_PAGE_ACCESS_TOKEN"
  },
  "pexels": {
    "api_key": "YOUR_PEXELS_API_KEY"
  }
}
```

## Implementation Notes
- Backup any API responses before processing
- Test with a draft post before publishing live
- Verify Facebook API credentials and page access after token renewal
- Monitor engagement metrics post-publication

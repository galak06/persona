---
name: wp-post-creator
description: >
  Create WordPress blog posts from approved content briefs. Generates full post
  following the DogFoodAndFun content blueprint (data-driven, Nalla's Dad voice,
  engineer persona), creates WP draft via REST API, notifies for review.
  Use when: "create post", "write blog post", "draft article", "make WP post",
  "publish to wordpress", "write the next post".
---

# wp-post-creator

Create WordPress blog posts from approved content briefs for DogFoodAndFun.com.

## What This Skill Does

Transforms approved content briefs into fully-formatted, SEO-optimized WordPress drafts. Generates posts following DogFoodAndFun's content blueprint—data-driven, written in Nalla's Dad voice with engineer perspective—then creates the draft via WordPress REST API and notifies for manual review before publication.

## When to Use

- "Create a WordPress post from the approved brief"
- "Draft the next blog article"
- "Write a post about [topic] and put it in WordPress"
- "Generate a blog post from the content plan"
- "Make a WP draft from the approved content"

## Prerequisites

Before running, ensure:
- At least one content brief with Status = "approved" exists
- `.claude/state/enrichment_cache.json` contains the brief data
- `.claude/state/social_api_config.json` has WordPress credentials
- `data/content_rules.json` defines post structure and requirements
- `data/site_content_cache.json` exists for internal linking
- WordPress user has "Editor" role minimum

## Workflow

### Step 1: Load Approved Brief

Read the enrichment cache and locate the most recent approved brief:

```python
import json

cache = json.load(open('.claude/state/enrichment_cache.json'))
approved_briefs = [b for b in cache if b.get('status') == 'approved']
brief = approved_briefs[-1]  # most recent
```

If cache is stale, fallback to Google Sheet "posts" tab, find next row with Status = "approved".

Load supporting data:
- `data/content_rules.json` — post structure, word count, tone guidelines
- `data/site_content_cache.json` — existing posts for internal linking

### Step 2: Generate Blog Post Content

Using the brief + content_rules.json, generate the full post.

**Post Structure:**

1. **Title** — Follow `title_patterns` from content_rules.json  
   Example: "Engineer's Data-Driven Guide: {Topic}"

2. **Byline** — "By Nalla's Dad / [current date]"

3. **Affiliate Disclosure** — Standard FTC compliance notice

4. **Hook** — Open with Nalla story (use Nalla_Context from brief row)  
   Personal, relatable, sets up the problem

5. **Problem Statement** — Why this topic matters (engineer perspective)

6. **Core Content** — H2/H3 structured sections  
   - 3+ concrete data points per section (prices, specs, measurements)
   - Engineer metaphors (spec sheet, diagnostic, system architecture)
   - 5-10 Nalla mentions with specific behaviors throughout
   - Practical advice grounded in experience

7. **Product Comparison Table** (if applicable)  
   - 3-6 products with name, price, specs, pros/cons
   - Honest assessment, no hype

8. **"Beyond" Section** — Alternatives and edge cases

9. **FAQ** — 3-6 items with schema.org FAQPage markup  
   ```json
   {
     "@context": "https://schema.org",
     "@type": "FAQPage",
     "mainEntity": [
       {
         "@type": "Question",
         "name": "...",
         "acceptedAnswer": {
           "@type": "Answer",
           "text": "..."
         }
       }
     ]
   }
   ```

10. **Related Reading** — 3-6 internal links to existing posts (from site_content_cache.json)

11. **"Our Pick"** — Recommendation if applicable

**Content Requirements:**

- **Word count:** 2,500–3,500 words
- **Internal links:** 3–6 (verified against site_content_cache.json)
- **Nalla mentions:** 5–10 with specific, believable behaviors
- **Data points:** At least 3 per major section
- **Year tag:** Include current year (2026) in title or section headers
- **Medical claims:** None
- **Sales language:** None—never "studies prove," always "in our experience," "we noticed"
- **Tone:** Conversational, expert, practical

**SEO Requirements:**

- Meta description: 120–160 characters
- Slug: kebab-case, keyword-rich
- Heading hierarchy: H1 → H2 → H3 (never skip levels)
- Image alt text: descriptive, include primary keyword

### Step 3: Format as WordPress HTML

Convert the post to WordPress-compatible HTML:

```html
<p><!-- Affiliate disclosure --></p>

<h2>Hook: [Nalla story]</h2>
<p>...</p>

<h2>Problem: [Why this matters]</h2>
<p>...</p>

<h2>The Data: [Core content]</h2>
<h3>Subsection 1</h3>
<p>...</p>

<table>
  <!-- Product comparison -->
</table>

<h2>FAQ</h2>
<script type="application/ld+json">
<!-- FAQPage schema -->
</script>

<h2>Related Reading</h2>
<ul>
  <li><a href="...">Internal link 1</a></li>
</ul>
```

- Use proper heading tags (h2, h3)
- Tables for product comparisons
- Bold/italic for emphasis
- Image placeholders: `<!-- [IMAGE] filename.png: descriptive alt text -->`
- Affiliate link placeholders: `[AFFILIATE:product_name]`

### Step 4: Create WordPress Draft via API

Use WordPress REST API to create a draft:

```python
import requests
import json

wp_config = json.load(open('.claude/state/social_api_config.json'))
wp_url = wp_config['wordpress']['api_url']
wp_user = wp_config['wordpress']['username']
wp_app_password = wp_config['wordpress']['app_password']

post_data = {
    "title": post_title,
    "content": post_html,
    "status": "draft",
    "excerpt": meta_description,
    "slug": post_slug,
    "categories": [category_id],
    "tags": [tag_ids],
}

response = requests.post(
    wp_url,
    json=post_data,
    auth=(wp_user, wp_app_password),
    headers={"Content-Type": "application/json"}
)

if response.status_code == 201:
    post_id = response.json()['id']
    post_link = response.json()['link']
    print(f"Draft created: {post_link}")
else:
    print(f"Error: {response.status_code}")
    print(response.json())
```

**Expected Success Response:**

```json
{
  "id": 1234,
  "title": "...",
  "slug": "...",
  "status": "draft",
  "link": "https://dogfoodandfun.com/?p=1234",
  "excerpt": "...",
  "content": {...}
}
```

### Step 5: Notify for Review

Send Telegram notification with post metadata and review checklist:

```
✅ Draft Created: "Engineer's Data-Driven Guide: Dog Food"

Preview: https://dogfoodandfun.com/wp-admin/post.php?post=1234&action=edit

📊 Stats:
  • Word count: 3,100
  • Products reviewed: 5
  • Internal links: 4
  • Data points: 18

⚠️ Before Publishing:
  [ ] Add featured image
  [ ] Replace [AFFILIATE:*] placeholders with real links
  [ ] Review all internal links (are they live?)
  [ ] Verify data accuracy
  [ ] Check heading hierarchy
  [ ] Proofread for tone & accuracy
```

### Step 6: Update Status & Cache

Update the Google Sheet brief row: Status → "wp_draft"

Save post metadata to `.claude/state/wp_posts_cache.json`:

```json
{
  "wp_posts": [
    {
      "post_id": 1234,
      "title": "Engineer's Data-Driven Guide: Dog Food",
      "slug": "engineers-guide-dog-food",
      "status": "draft",
      "created_at": "2026-04-16T14:30:00Z",
      "word_count": 3100,
      "category": "guides",
      "keywords": ["dog food", "nutrition", "2026"],
      "brief_id": "brief_abc123"
    }
  ]
}
```

### Step 7: Exit & Wait

This skill completes after creating the draft. The user reviews and publishes manually in WordPress.

When the user publishes, they update the sheet Status to "wp_published"—this triggers downstream social-post-creator skills.

## Credential Config

The skill expects `.claude/state/social_api_config.json`:

```json
{
  "wordpress": {
    "api_url": "https://dogfoodandfun.com/wp-json/wp/v2/posts",
    "username": "YOUR_WP_USERNAME",
    "app_password": "YOUR_WP_APP_PASSWORD"
  },
  "telegram": {
    "bot_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID"
  }
}
```

Credentials are sensitive—never commit these values.

## Error Handling

| Error | Action |
|-------|--------|
| **WP API auth fails** | Log "WP_AUTH_FAILED", notify user via Telegram, exit |
| **WP API error** | Log full error, save post HTML locally as backup, notify user |
| **Content exceeds word limit** | Trim sections proportionally, log warning, proceed |
| **No approved briefs found** | Log "NO_APPROVED_BRIEFS", exit gracefully with message |
| **Site cache missing** | Warn but proceed with enrichment data only (skip internal linking) |
| **Brief missing required fields** | Log which fields, exit with error message |

All errors should be reported via Telegram and logged to `.claude/logs/wp_post_creator.log`.

## Dependencies

- **Python:** `requests` library (HTTP to WordPress API)
- **Local:** `lib/notifier.py` (Telegram notifications)
- **Data files:**
  - `data/content_rules.json` — post structure, guidelines, word count targets
  - `data/site_content_cache.json` — list of published posts for linking
- **State files:**
  - `.claude/state/enrichment_cache.json` — approved briefs
  - `.claude/state/social_api_config.json` — WordPress + Telegram credentials
  - `.claude/state/wp_posts_cache.json` — post metadata (created/updated by this skill)

## Example Input (Approved Brief)

From `.claude/state/enrichment_cache.json`:

```json
{
  "brief_id": "b_12345",
  "title": "Engineer's Data-Driven Guide: Dog Food",
  "topic": "dog food nutrition",
  "status": "approved",
  "nalla_context": "Nalla is a 3-year-old Labrador who switches between kibble brands monthly",
  "keywords": ["dog food", "nutrition", "kibble"],
  "focus_areas": ["cost vs. quality", "ingredient sourcing", "vet opinions"],
  "target_audience": "dog owners who want to understand food labels"
}
```

## Example Output (WordPress Draft)

```
✅ Post created as draft
📄 Title: Engineer's Data-Driven Guide: Dog Food
🔗 Edit: https://dogfoodandfun.com/wp-admin/post.php?post=1234
📊 Word count: 3,100
💾 Saved to: .claude/state/wp_posts_cache.json
📢 Notified via Telegram
```

## Tips & Best Practices

1. **Always verify Nalla mentions** are specific (e.g., "Nalla's ears perk up when we open the Blue Buffalo bag") not generic
2. **Check internal links** actually exist in site_content_cache.json
3. **Use engineer metaphors** consistently—spec sheets, diagnostics, system thinking
4. **Data points** should be specific measurements or prices, not general statements
5. **Test WP credentials** before running to avoid partial failures
6. **Keep affiliate placeholders** clear so user can easily find them: `[AFFILIATE:product_name]`
7. **Meta description** should be under 160 characters—test length before posting

## Next Steps (After Publication)

Once the user publishes the post in WordPress:
1. User updates Google Sheet: Status → "wp_published"
2. `social-post-creator` skill is triggered
3. Social media posts are generated from the WP post
4. Scheduled posts go live on Twitter, LinkedIn, etc.

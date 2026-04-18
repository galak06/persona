---
name: content-enricher
description: >
  Enrich blog post ideas from the Google Sheet with SEO research, social media trends,
  and market demand analysis. Presents enriched brief for approval via Telegram.
  Use when: "enrich posts", "analyze ideas", "research topics", "check demand",
  "prepare content brief", "what should I write next".
---

# Content Enricher Skill

Transforms raw blog post ideas from the DogFoodAndFun content calendar into rich, actionable content briefs by layering SEO research, social media trend analysis, and market demand signals. Automates the research phase of content planning and routes approval requests via Telegram for quick editorial decision-making.

## When to Use

- User says "enrich the next post idea"
- User asks "what should I write about next?"
- User requests SEO research on a topic
- User wants to check market demand before writing
- User needs content gap analysis
- User is preparing a content brief for a writer

## Prerequisites

Ensure these files exist:
- `config.json` — site URL, brand keywords, social channels
- `data/content_rules.json` — post blueprint structure, voice guidelines, title patterns
- `data/site_content_cache.json` — recent published posts and keyword gaps
- Google Sheet "posts" tab active and accessible
- Telegram bot configured with user chat ID in environment

## Workflow

### Step 1 — Load Data Sources

1. **Read configuration:**
   - Load `config.json` to get site URL, brand keywords, target audience
   - Extract social media accounts and hashtag strategy

2. **Load content rules:**
   - Read `data/content_rules.json`
   - Store `post_blueprint` (H2 section templates)
   - Store `voice_rules` (tone, style, audience)
   - Extract `title_patterns` for suggested titles

3. **Load site content cache:**
   - Read `data/site_content_cache.json`
   - Index existing posts by keyword and category
   - Identify content gaps and overlap zones

4. **Open Google Sheet:**
   - Navigate to: https://docs.google.com/spreadsheets/d/1_GmIsHDd1y1hNSCx4S97l35UMFUNQX4NZSyTDlUxjsI/edit?gid=799238859
   - Use Chrome MCP to load the "posts" tab
   - Extract all rows with columns: `Category`, `Topic`, `Target_Keyword`, `Nalla_Context`, `Post_Goal`, `Status`, `Input`

### Step 2 — Select Next Idea

1. **Filter for ready ideas:**
   ```
   Status = "publish" AND Input = "1"
   ```

2. **Skip these statuses:**
   - `enriched` — already processed
   - `approved` — awaiting writer
   - `wp_draft` — in WordPress
   - `wp_published` — live
   - `social_done` — shared
   - `skipped` — editorial rejection

3. **Pick the first qualifying row** as the active idea to enrich

4. **Extract fields:**
   - `category`, `topic`, `target_keyword`, `nalla_context`, `post_goal`

### Step 3 — Enrich the Idea

#### 3.1 SEO Research

1. **Search for target keyword:**
   - Use web search for the `target_keyword`
   - Capture top 5 ranking articles (title, URL, domain authority visual)

2. **Extract competitor intel:**
   - List competing article titles
   - Note common H2 sections across top results
   - Identify author credentials and article freshness

3. **Analyze keyword difficulty:**
   - Assess how many authoritative domains (DA >50) rank
   - Rate difficulty: `high` (>20 DA50+ sites), `medium` (10-20), `low` (<10)

4. **Find People Also Ask questions:**
   - Extract 3-5 "People Also Ask" queries from SERP
   - These reveal user intent and content gaps

5. **Identify content gaps:**
   - What do top 5 competitors NOT cover?
   - What angle can DogFoodAndFun uniquely address?
   - Flag as "gap opportunity"

#### 3.2 Social Media Trends

1. **Instagram hashtag research:**
   - Search Instagram for top 5 hashtags related to topic
   - Extract engagement counts and recent post counts
   - Identify trending angles (if any viral threads exist)

2. **Facebook dog group discussions:**
   - Search Facebook dog groups for recent conversations
   - Extract common questions and pain points
   - Note sentiment (positive/concerns/neutral)

3. **Extract hot angles:**
   - What are dog owners actually asking about?
   - What problems are they experiencing?
   - What solutions are they seeking?

#### 3.3 Site Alignment

1. **Check existing content:**
   - Look up `target_keyword` in `site_content_cache.json`
   - If overlap exists → suggest differentiation angle
   - If gap exists → flag as high-priority opportunity

2. **Cross-reference rules:**
   - Ensure topic aligns with `content_rules.json` categories
   - Verify tone matches voice guidelines
   - Check if post_blueprint applies

3. **Assess content gap opportunity:**
   - Score 1-10 based on: search volume (if high), competitor gap (if clear), site alignment (if strong)
   - High score = proceed aggressively
   - Low score = suggest skip or repositioning

#### 3.4 Content Brief Generation

Using all gathered data, generate:

1. **Suggested Title:**
   - Follow `content_rules.json` `title_patterns`
   - Include target keyword naturally
   - Keep under 60 characters for SEO

2. **Suggested H2 Outline:**
   - 5-7 sections following `post_blueprint` structure from content_rules
   - Each section with brief purpose line
   - Example structure:
     ```
     1. Introduction + Nalla angle
     2. What is [topic]? (context)
     3. Key consideration #1
     4. Key consideration #2
     5. DogFoodAndFun recommendation
     6. Product roundup (if applicable)
     7. FAQ section (from People Also Ask)
     8. Conclusion
     ```

3. **Products to review:**
   - Identify 3-5 products relevant to topic
   - Include approximate price ranges
   - Mark if DogFoodAndFun has affiliate links

4. **Internal link opportunities:**
   - Suggest 3-5 existing DogFoodAndFun posts to link to
   - Include anchor text suggestions

5. **Keyword strategy:**
   - Primary keyword: `target_keyword`
   - Secondary keywords: 3-5 variations (extracted from People Also Ask + related searches)
   - LSI keywords: semantic variations

6. **Search volume signal:**
   - Based on competitor count and People Also Ask richness
   - Rate as: `high` (clear demand), `medium` (some demand), `low` (niche)

7. **Nalla angle:**
   - Specific dog behavior, story, or context to lead with
   - Blend `nalla_context` from sheet with SEO findings
   - Create hook for introduction

8. **Opportunity score:**
   - 1-10 scale
   - Factors: keyword difficulty inverse, gap size, social demand, site gap
   - 8-10 = pursue immediately
   - 5-7 = good, proceed
   - <5 = lower priority, consider skip

### Step 4 — Present for Approval

Send enriched brief to Telegram using notifier module:

```python
from lib.notifier import send, request_approval

brief_text = f"""
📝 Content Brief: {topic}

🎯 Category: {category}
🔑 Keyword: {target_keyword} ({keyword_difficulty})
⭐ Opportunity Score: {opportunity_score}/10

📌 Suggested Title:
{suggested_title}

📋 Content Outline:
{outline_formatted}

🛍️ Products to Review:
{products_formatted}

🔗 Internal Links:
{internal_links_formatted}

🐕 Nalla Angle:
{nalla_angle}

🏆 Top Competitors:
{competitor_summary}

📊 Keyword Data:
Primary: {primary_keyword}
Secondary: {secondary_keywords_list}
Search Volume Signal: {volume_signal}

Reply with:
✅ approve — proceed with content brief
⏭️ skip — move to next idea
✏️ edit [notes] — request changes before approval
"""

result = request_approval(
    brief_text,
    timeout_hours=24,
    fallback_file=f".claude/state/pending_approval_{topic_slug}.txt"
)

approval_response = result.get("response")  # "approve", "skip", "edit"
approval_notes = result.get("notes", "")
```

**Expected response format from Telegram:**
- `approve` — user approves brief as-is
- `skip` — user wants to skip this topic
- `edit [your feedback]` — user wants modifications

### Step 5 — Update Sheet Status

Based on approval response, update the Google Sheet:

1. **If "approve":**
   - Navigate to Google Sheet
   - Find the processed row
   - Update `Status` column to `"approved"`
   - Add timestamp to notes column (optional)
   - Log: "Content brief approved for [topic]"

2. **If "skip":**
   - Update `Status` column to `"skipped"`
   - Log: "Topic skipped: [topic]"

3. **If "edit":**
   - Store feedback in `approval_notes`
   - Re-run Steps 3-4 with new angle or constraints
   - Present revised brief back to Telegram
   - Wait for final approval before updating sheet

### Step 6 — Save Enrichment Data

Save the full enrichment data to `.claude/state/enrichment_cache.json`:

```json
{
  "topic": "string",
  "enriched_at": "ISO 8601 timestamp",
  "category": "string",
  "target_keyword": "string",
  "seo_data": {
    "keyword_difficulty": "high|medium|low",
    "top_competitors": [
      {
        "title": "string",
        "url": "string",
        "domain_authority_estimate": "number"
      }
    ],
    "people_also_ask": ["question 1", "question 2", ...],
    "content_gap_opportunity": "description of gap"
  },
  "social_trends": {
    "instagram_hashtags": [
      {
        "hashtag": "string",
        "post_count": "number",
        "engagement_signal": "high|medium|low"
      }
    ],
    "facebook_insights": "summary of group discussions",
    "hot_angles": ["angle 1", "angle 2", ...]
  },
  "content_brief": {
    "suggested_title": "string",
    "outline": "markdown H2 structure",
    "products": [
      {
        "name": "string",
        "price_range": "string",
        "relevance": "string"
      }
    ],
    "internal_links": [
      {
        "post_title": "string",
        "anchor_text": "string"
      }
    ],
    "primary_keyword": "string",
    "secondary_keywords": ["keyword 1", "keyword 2", ...],
    "search_volume_signal": "high|medium|low",
    "nalla_angle": "string",
    "opportunity_score": "number 1-10"
  },
  "approval_status": "pending|approved|skipped|edited",
  "approval_response": "user's telegram response",
  "approval_notes": "any edit requests or context"
}
```

Append to the cache (don't overwrite) to maintain enrichment history.

## Error Handling

| Scenario | Action |
|----------|--------|
| Google Sheet access fails | Log error with timestamp, retry on next run, continue gracefully |
| Web search unavailable | Proceed with site cache data only, add note "limited enrichment — search unavailable" |
| Telegram notification fails | Save brief to `.claude/state/pending_approval_{topic_slug}.txt`, log "TELEGRAM_FAILED", alert user in next briefing |
| No qualifying rows in sheet | Log "NO_IDEAS_TO_ENRICH", exit with message "All ideas are processing or completed" |
| Keyword has no SEO data | Use social trends + site gap analysis only, flag as "SEO data incomplete" in brief |
| Content rules file missing | Load default post blueprint, proceed with warning logged |

## Dependencies

- **Chrome MCP** — navigate Google Sheets, extract data
- **Web Search** — SEO research, social trend discovery
- **lib/notifier.py** — Telegram message sending, approval routing
- **config.json** — site settings, brand keywords, social channels
- **data/content_rules.json** — post templates, voice guidelines, title patterns
- **data/site_content_cache.json** — existing posts, keyword index
- **Python 3.8+** — local processing if running standalone
- **Telegram Bot Token** — stored in environment or `.env` file

## Output

✅ **Success state:**
- Content brief delivered to user via Telegram
- Enrichment data cached in `.claude/state/enrichment_cache.json`
- Google Sheet status updated (after approval)
- User ready to assign brief to writer

❌ **Failure state:**
- Error logged with timestamp
- Fallback files created (pending approval or error logs)
- User notified of blockers
- Skill ready to retry on next invocation

## Examples

**Example 1: Full enrichment flow**
```
User: "Enrich the next post idea"
→ Loads Sheet, finds "Best Dog Toys for Heavy Chewers"
→ Researches "heavy chewer dog toys" SEO
→ Extracts Instagram trends for #heavychewer
→ Generates brief with title, outline, products
→ Sends to Telegram for approval
→ User replies "approve"
→ Sheet updated to Status = "approved"
→ Brief cached and ready for writer
```

**Example 2: Skip and move to next**
```
User receives brief, replies "skip"
→ Sheet Status updated to "skipped"
→ Skill loops to next qualifying row
→ Automatically enriches second idea
→ Sends new brief to Telegram
```

**Example 3: Edit and refine**
```
User replies "edit focus on senior dogs with joint issues"
→ Skill re-enriches with new angle
→ Regenerates outline + products for senior dog focus
→ Sends revised brief back for approval
→ User confirms "approve"
→ Sheet updated, brief ready
```

## Notes

- Enrichment typically takes 5-10 minutes per topic (web searches + content generation)
- Telegram approval waits up to 24 hours; if no response, brief stays in pending state
- Content cache is updated on every enrichment run to track progress
- Skill is designed to batch-process multiple ideas if user requests (enrich next 3, for example)
- All enrichment data is preserved for writer reference and future optimization analysis

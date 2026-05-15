---
name: content-ideator
description: >
  Generate new blog post ideas for {{brand.domain}} and append them to the
  Google Sheet. Analyzes content gaps, trending topics, social media discussions,
  seasonal opportunities, and competitor content to produce 5-10 high-quality
  ideas per run. Use when: "generate ideas", "what should I write", "new topics",
  "fill the content calendar", "brainstorm posts", "need more ideas".
---

# Content Ideator Skill

## Purpose
Generate high-quality blog post ideas for {{brand.domain}} that match the engineer-led brand voice, leverage {{brand.mascot}}'s personal context, and fill identified content gaps. Automatically append approved ideas to the Google Sheet to keep the content calendar current.

## Key Context

### Site Profile
- **URL**: {{brand.domain}}
- **Persona**: {{brand.persona}} (software engineer + dog owner, based in Tel Aviv)
- **Dog**: {{brand.mascot}} (fluffy shepherd mix, ~25-50lbs)
- **Voice**: Data-driven, personal, engineer's perspective on dog care
- **Positioning**: Unique angle combining technical analysis with real-world dog ownership
- **Target audience**: Dog owners in **USA + Canada** (primary monetization market — affiliate revenue depends on US-available brands, USD pricing, US/CA shipping)

### Content Categories
1. **Grooming** — Bath, nails, ears, coat care, brushing
2. **Food & Diet** — Kibble, homemade recipes, nutrition, ingredients, raw diets, protein analysis
3. **Lifestyle & Gear** — Leashes, collars, toys, beds, GPS trackers, vests, equipment reviews
4. **Training** — Recall, commands, tricks, behavior modification, reactivity

### Google Sheet
- **URL**: https://docs.google.com/spreadsheets/d/1_GmIsHDd1y1hNSCx4S97l35UMFUNQX4NZSyTDlUxjsI/edit?gid=799238859
- **Tab**: "posts"
- **Columns**: Category | Topic | Target_Keyword | {{brand.mascot}}_Context | Post_Goal | Status | Input

## Workflow

### Step 0: Load Keyword Clusters (NEW)

Before anything else, load `data/keyword_clusters.json`:

- **If file exists and `last_updated` is within 30 days:** use it. Identify cluster pillar gaps — these are the highest-priority writing targets.
- **If file is missing OR `last_updated` is older than 30 days:** STOP. Tell the user: *"Run `keyword-cluster-mapper` first — clusters are missing or stale. Without them, new ideas form islands instead of topical networks."* Do not proceed with idea generation until clusters are fresh.
- **If file is brand-new (just created with empty clusters):** treat all categories as pillar-gap; prioritize broad pillar ideas in this batch.

Cluster awareness ensures every new idea lands in a defined topical network, with a clear pillar relationship.

### Step 1: Load Existing Data
Load these assets to understand current coverage:

1. **Google Sheet "posts" tab**
   - Open via Chrome browser
   - Extract all existing topics and keywords to avoid duplicates
   - Note topics with Status = "publish" (published posts)
   - Note topics with Status = "pending" (queued for publication)

2. **`data/site_content_cache.json`**
   - Recent published posts and their keywords
   - Content gaps identified by category
   - Keyword coverage analysis
   - Last updated timestamp

3. **`data/content_rules.json`**
   - idea_generation section with rules
   - idea_schema definition
   - Category keyword mappings
   - Diversity and quality rules

4. **`config.json`**
   - Target keywords and keyword categories
   - Brands reviewed previously
   - Niche definition and scope
   - Seasonal considerations

5. **`data/keyword_clusters.json`** (already loaded in Step 0)
   - Existing clusters with pillar status per cluster
   - Pillar gaps (clusters without a published pillar)
   - Spoke counts (clusters that need more spokes vs. saturated ones)

### Step 2: Identify Content Gaps

Cross-reference existing sheet ideas and published posts against all keyword categories:

```
Categories and their keyword coverage:

GROOMING
  - bath, baths, bathing, washing
  - nails, nail trim, nail care, pedicure
  - ears, ear cleaning, ear health, infection
  - coat, fur, shedding, matting, grooming
  - brush, brushing, de-shedding, furminator

FOOD & DIET
  - kibble, dry food, kibble brands
  - homemade, home-cooked, fresh dog food
  - recipe, recipes, meal plan
  - nutrition, nutritional, nutrient, AAFCO
  - ingredient, ingredients, analysis, labels
  - raw, BARF, raw feeding
  - diet, dietary, digestion
  - protein, fat, calorie, macros

LIFESTYLE & GEAR
  - leash, leashes, recall leash, long line
  - collar, harness, equipment, fit
  - toy, toys, enrichment, puzzle
  - bed, bedding, crate, pen
  - GPS, tracker, tracking, location
  - vest, jacket, sweater, rain, cold
  - travel, car, airline, transport

TRAINING
  - recall, come command, off-leash
  - command, commands, sit, stay, down
  - trick, tricks, fun, advanced
  - behavior, behavioral, modification
  - reactivity, reactant, fear, anxiety
  - puppy, socialization, foundation
  - obedience, discipline, consistency
```

For each keyword category:
- Count how many sheet ideas and published posts cover it
- Identify categories with zero coverage in last 60 days
- Identify individual keywords within categories with zero coverage
- Rank gaps by search volume (use keyword research tools if available)

### Step 3: Research Trending Topics

Execute research across multiple sources:

#### Instagram Trends
- Search hashtags relevant to each gap category (#homemadedogfood, #dognutrition, #canicross, #dogtraining, #doglove, etc.)
- **Filter to US/CA-located accounts** (location tags, USD pricing in captions, English-language)
- Identify posts with 1000+ likes or 100+ comments
- Note recurring pain points and questions in comments
- Document which content types get highest engagement (video, carousel, educational)

#### Facebook Group Discussions
- Search major dog owner groups for recent discussions — **US/Canada-based groups only**
- Look for recurring questions without good answers
- Note emotional triggers and common problems
- These indicate real search intent and content demand

#### Google "People Also Ask" (PAA)
- For each target keyword category, perform Google search **with `gl=us` and `gl=ca`** (not localized to IL — audience is US/CA, not Israel)
- Extract all PAA questions that appear
- These map directly to:
  - FAQ sections in posts
  - Standalone follow-up post ideas
  - Subheadings and content structure
- Example: search "homemade dog food" reveals "Is homemade dog food cheaper than kibble?" → potential post

#### Buyer-Intent Keywords (Commercial Search)

The site monetizes via affiliate links — every batch must surface keywords with **purchase intent**, not just informational queries:

| Pattern | Example | Why it converts |
|---|---|---|
| `best [X]` | "best GPS tracker for dogs 2026" | Comparison-shopper at decision stage |
| `[X] vs [Y]` | "Fi vs Tractive" | Picking between two products |
| `[X] review` | "Ollie dog food review" | Validating before purchase |
| `[X] price` / `[X] cost` / `is [X] worth it` | "is Fi collar worth it" | Cost justification |
| `where to buy [X]` | "where to buy Nom Nom" | Late-funnel |
| `[X] for [breed/use-case]` | "GPS tracker for shepherd mix" | Niche fit |

**US/CA-only signals to require:**
- USD pricing in any cost analysis
- US/CA-available brands (skip EU-only products)
- Reference US frameworks (AAFCO, FDA, FCC) — not EU/UK regulations
- Note seasonal timing in US climate zones (e.g. tick season starts March in southern US, May in northern US)

#### Seasonal & Timely Topics
- **Current date context** (April 2026):
  - Spring allergies and environmental triggers
  - Tick and flea season ramping up
  - Warming weather and exercise adjustments
  - Shedding season (peak for many breeds)
  - Spring grooming preparation

- **Upcoming events**:
  - Summer heat preparation (June-August)
  - Travel season planning (May-August)
  - Holiday safety topics (advance planning)
  - Back-to-school/routine changes (September)

#### Competitor Content Scan
- Identify top 5-10 ranking sites in niche (search "dog nutrition blog", "dog training guide", etc.)
- Document topics they cover
- Identify topics they DON'T cover
- Find angles they miss (engineer/data-driven perspective is differentiator)
- Note their keyword strategy

### Step 4: Generate Ideas

For each identified opportunity, create a complete idea matching the Google Sheet schema:

```json
{
  "Category": "Food & Diet",
  "Topic": "Fresh vs. Kibble: The Real Cost Per Nutrient",
  "Target_Keyword": "fresh dog food cost comparison",
  "{{brand.mascot}}_Context": "I ran the numbers in a spreadsheet. Fresh food costs us $4.20/day vs $1.80 for premium kibble, but the protein-per-dollar ratio tells a different story.",
  "Post_Goal": "Provide a cost-benefit analysis of fresh vs. kibble dog food using real price data and nutritional density metrics.",
  "Status": "publish",
  "Input": "1"
}
```

#### Quality Rules for Each Idea

1. **No Duplicates** — Topic must NOT overlap with existing sheet ideas, published posts, OR ideas in `.claude/state/ideation_history.json` (case-insensitive keyword check). The history file persists ideas even after they're trimmed from the sheet by sheet-backup cleanup.

2. **{{brand.mascot}} Context Required** — Each idea must include a specific personal angle from {{brand.mascot}}'s experience
   - Examples: "I noticed {{brand.mascot}}'s coat improved after..."
   - "We tried this approach and..."
   - "{{brand.mascot}}'s behavior changed when..."
   - Creates authenticity and differentiates from generic content

3. **Searchable Keywords** — Target_Keyword must:
   - Not be too niche (should have meaningful search volume)
   - Not be too broad (should be actionable and specific)
   - Match real search intent (use Google, SEO tools if available)
   - Be relevant to engineer persona (data terms OK: "analysis", "metrics", "comparison")

4. **Reader-Focused Goal** — Post_Goal must describe what READER gains, not what we write
   - Bad: "Write a post about dog food"
   - Good: "Learn how to evaluate kibble labels using AAFCO standards to make informed feeding decisions"

5. **Engineer/Data Angle** — Ideas should leverage:
   - Data analysis and metrics
   - Cost-benefit comparisons
   - Technical explanations
   - Systematic approaches
   - Research references

6. **Category Diversity** — In each batch of 5 ideas:
   - Max 2 consecutive ideas from same category
   - Aim for at least 1 idea per active category
   - Prevents over-saturation in one area

7. **Actionability** — Idea should be:
   - Feasible to research and write
   - Within {{brand.mascot}}'s experience or easily researched
   - Relevant to core audience (dog owners)
   - Completable in 1-2 weeks

8. **Buyer-intent slot (HARD requirement)** — Every batch of 5+ ideas MUST include:
   - At least **2 ideas with explicit buyer-intent keywords** (best/review/vs/price/worth it)
   - At least **1 product comparison** covering 3+ products with US/CA pricing
   - These convert affiliate clicks; informational-only batches starve revenue.

#### Idea Prioritization Scoring

Score each idea on these dimensions (max 15/15):

```
Fills cluster pillar gap (priority #1):    +3 points  ← NEW
Buyer-intent keyword (commercial search):  +2 points
Content Gap (no existing coverage):        +3 points
Trending on social (high engagement):      +2 points
PAA question (proven search intent):       +2 points
Seasonal relevance (timely/upcoming):      +1 point
Competitor gap (they miss the angle):      +1 point
Existing {{brand.mascot}} experience (can speak to):  +1 point
Orphan-spoke penalty (no pillar exists):   -2 points  ← NEW
```

**Pillar-gap bonus (+3) triggers when** the idea would become the published pillar for a cluster that currently has `pillar_gap: true` in `keyword_clusters.json`. Pillar posts unlock all their spokes — write them first.

**Orphan-spoke penalty (-2) triggers when** the idea is a narrow spoke (specific brand review, breed-specific guide) and its parent cluster has no published pillar AND no idea in this batch is creating that pillar. Exception: if the spoke is being added to a batch that also creates the pillar, no penalty applies.

**Buyer-intent keywords trigger when** the target keyword contains: "best", "review", "vs", "price", "cost", "worth it", "where to buy", "[brand] alternative", or "[product] for [breed/use-case]". These convert affiliate clicks at 5-10× the rate of informational queries — score them aggressively.

Examples:

- Fresh vs. Kibble cost analysis:
  - Content gap: +3 (no cost analysis exists)
  - {{brand.mascot}} experience: +1 (does it regularly)
  - Score: 7/10

- Counter-Surfing Behavior Training:
  - Content gap: +3 (no engineering-angle approach)
  - Trending: +2 (12K+ IG posts)
  - {{brand.mascot}} experience: +1 ({{brand.mascot}} does this)
  - Score: 8/10

- Spring Allergy Season Guide:
  - Seasonal: +1 (current month April)
  - PAA questions: +2 (Google shows 5+ related questions)
  - {{brand.mascot}} experience: +1 (seasonal allergies relevant)
  - Score: 5/10

Generate 5 ideas total, ranked by score descending.

### Step 4b: Apply Learned Preferences

Before presenting ideas, apply scoring adjustments learned from the user's past approvals/skips:

```python
from idea_learner import apply_adjustments, get_scoring_adjustments

# Check what we've learned
adj_data = get_scoring_adjustments()
if adj_data["status"] == "active":
    print(f"Applying learned preferences from {adj_data['based_on']}")
    for adj in adj_data["adjustments"]:
        print(f"  {adj['reason']}")

# Adjust each idea's score
for idea in ideas:
    original_score = idea["score"]
    adjusted_score, reasons = apply_adjustments(original_score, idea)
    idea["score"] = adjusted_score
    idea["score_adjustments"] = reasons
    if original_score != adjusted_score:
        print(f"  {idea['Topic']}: {original_score} → {adjusted_score} ({', '.join(reasons)})")

# Re-sort by adjusted score
ideas.sort(key=lambda x: x["score"], reverse=True)
```

### Step 5: Present for Approval

Format batch summary for Telegram notification:

```
💡 New Content Ideas Generated

Generated: [5] ideas | Month: [April 2026]
Sources: [X gap], [Y trending], [Z PAA], [W seasonal]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. [Food & Diet] Fresh vs. Kibble: The Real Cost Per Nutrient
   🔑 Keyword: fresh dog food cost comparison | 📊 Score: 8/10
   📌 Reason: Content gap (no cost analysis), {{brand.mascot}} experience

2. [Training] Counter-Surfing: An Engineer's Behavioral Analysis
   🔑 Keyword: stop dog counter surfing | 📊 Score: 8/10
   📌 Reason: Trending (12K+ IG), content gap, {{brand.mascot}} behavior

3. [Grooming] Spring Allergy Season: Data-Driven Itch Relief
   🔑 Keyword: dog spring allergies natural relief | 📊 Score: 6/10
   📌 Reason: Seasonal, PAA questions, {{brand.mascot}} relevance

... (remaining ideas)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ Actions:
• "approve all" → add all 8 ideas to sheet
• "approve 1,3,5,7" → add only those IDs
• "skip" → discard batch
• "edit 2" → modify idea 2, then re-approve
```

**IMPORTANT:** Use `send_and_wait()` from `lib/notifier.py` to send AND automatically poll for the reply. Do NOT just send and stop — the function blocks until the user replies in Telegram.

```python
from notifier import send_and_wait

result = send_and_wait(msg, timeout_hours=24)
# result["action"]: "approved" | "skipped" | "edited" | "timeout"
# result["reply_text"]: raw reply (e.g., "1,2" or "all" or "approve")

if result["action"] == "approved":
    reply = result["reply_text"].lower().strip()
    if reply == "all":
        approved_ids = {idea["id"] for idea in ideas}
    elif any(c.isdigit() for c in reply):
        approved_ids = {int(n.strip()) for n in reply.split(",") if n.strip().isdigit()}
    else:
        approved_ids = {idea["id"] for idea in ideas}  # default to all
elif result["action"] == "skipped":
    approved_ids = set()
else:
    approved_ids = set()
```

### Step 5b: Record Decisions for Learning

After receiving Telegram response, record each idea's outcome:

```python
from idea_learner import record_decision

for idea in ideas:
    if idea["id"] in approved_ids:
        record_decision(idea, "approved")
    elif idea["id"] in edited_ids:
        record_decision(idea, "edited", notes=edit_notes.get(idea["id"], ""))
    else:
        record_decision(idea, "skipped")

print(f"Recorded {len(ideas)} decisions for future learning")
```

This builds a preference profile over time. After 3+ decisions, future batches will:
- **Boost** categories and keywords the user consistently approves
- **Penalize** topics the user tends to skip
- **Set minimum score thresholds** based on approval patterns
- **Surface preferred angles** (comparison, cost analysis, protocol, etc.)

### Step 6: Append to Google Sheet

For each approved idea:

1. **Open Google Sheet** via Chrome
2. **Navigate to "posts" tab**
3. **Scroll to last row with data**
4. **Click first empty cell** in column A (Category column)
5. **Enter data row-by-row**:
   ```
   Category: [value] [TAB]
   Topic: [value] [TAB]
   Target_Keyword: [value] [TAB]
   {{brand.mascot}}_Context: [value] [TAB]
   Post_Goal: [value] [TAB]
   Status: publish [TAB]
   Input: 1
   ```
6. **Verify by reading back** — scroll to newly added row and confirm all fields match
7. **Repeat** for each approved idea

### Step 7: Update State

Save generation metadata to `.claude/state/ideation_history.json`:

```json
{
  "last_run": "2026-04-16T14:30:00Z",
  "ideas_generated": 8,
  "ideas_approved": 5,
  "sources": {
    "content_gaps": 3,
    "trending": 2,
    "paa_questions": 2,
    "seasonal": 1,
    "competitor_gaps": 0
  },
  "categories_covered": [
    "food_and_diet",
    "training",
    "grooming",
    "lifestyle_and_gear"
  ],
  "approved_ideas": [
    {
      "id": 1,
      "topic": "Fresh vs. Kibble: The Real Cost Per Nutrient",
      "category": "food_and_diet",
      "keyword": "fresh dog food cost comparison",
      "score": 8
    }
  ],
  "notes": "All ideas approved. 5 rows added to sheet."
}
```

## Scheduling

**Recommended Triggers:**
- Monthly on 1st of month (consistent cadence)
- When sheet has fewer than 5 rows with Status = "publish" + Input = "1" (demand-based)
- When content gaps identified in analysis (urgent gaps)

**Optimal Time:** Mornings (less resource contention)

## Error Handling

| Error | Recovery |
|-------|----------|
| Google Sheet not accessible | Save ideas to `backups/ideas_[date].json` for manual entry later |
| Web search fails | Generate ideas from content_gaps + config keywords only (note reduced quality in Telegram) |
| No gaps found | Focus on seasonal + trending + competitor angles as alternative sources |
| Sheet write fails | Format ideas as tab-separated text, send to Telegram for manual paste |
| Duplicate idea generated | Check against full history in ideation_history.json before approval |
| Low idea quality | Return to research phase, expand sources (check Reddit, newsletters, YouTube comments) |

## Dependencies

### Data Files
- `data/site_content_cache.json` — existing content inventory
- `data/content_rules.json` — idea schema, diversity rules, category keywords
- `config.json` — target keywords, brands, niche definition
- `.claude/state/ideation_history.json` — generation history and deduplication

### External Tools
- **Chrome MCP** — Google Sheet access and navigation
- **Web Search** — trend research and competitor analysis
- **Telegram API** — notification and approval workflow
- **JSON utilities** — data parsing and state persistence

### Generated Files
- `.claude/state/ideation_history.json` — updated after each run
- `backups/ideas_[YYYY-MM-DD].json` — backup of approved ideas
- Google Sheet "posts" tab — primary destination

## Tips for High-Quality Ideas

1. **Research Deeply** — Spend time in actual communities (Instagram, Facebook groups) where dog owners hang out. Real conversations reveal real needs.

2. **Leverage Data** — Use pricing tools, nutrition databases, search volume tools to ground ideas in facts, not guesses.

3. **Personal First** — Ideas where {{brand.mascot}} has direct experience score highest. Prioritize those.

4. **Keyword Research** — Before finalizing, verify target keywords have real search volume and reasonable competition.

5. **Seasonal Awareness** — April context matters (allergies, tick season, shedding). Upcoming 2-3 months matters too (travel planning, summer heat).

6. **Unique Angle** — Ask: "What would an engineer write about this that others miss?" Data analysis, systematic comparisons, infrastructure thinking.

7. **Batch Diversity** — Vary categories, formats, and topics within each generation batch. Monotony kills engagement.

## Example: Full Idea Generation Session

**Input:** "Generate new ideas, we're low on content"

**Process:**
1. Load sheet: 12 existing ideas (3 pending, 9 published)
2. Analyze gaps: Lifestyle/Gear category has ZERO recent posts
3. Research trends: GPS trackers trending on IG (+18%), TikTok "dog gear hauls" viral
4. PAA: "Best GPS tracker for dogs" → 8 related questions
5. Generate: 8 ideas, 3 in Lifestyle/Gear, 2 Food & Diet, 2 Training, 1 Grooming
6. Score: Top 2 ideas score 8/10 (gap + trending), 4 ideas score 6-7/10
7. Present: Telegram batch summary
8. Approval: User approves 5 ideas
9. Sheet: Add 5 rows to "posts" tab
10. State: Update ideation_history.json with metadata

**Output:** 5 new ideas in content calendar, ready for writing queue

---

*Last Updated: 2026-04-16*
*Version: 1.0*

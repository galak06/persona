---
name: tiktok-scout
description: Scout TikTok hashtag pages for follow candidates using the real Chrome browser (no bot detection). Scrapes creator handles from trending hashtags and saves them to the follow queue with deduplication.
trigger: "run tiktok scout", "scout tiktok", "find tiktok candidates", "tiktok-scout", "scrape tiktok hashtags", "find tiktok creators"
---

# TikTok Scout — {{brand.name}}

Scout TikTok hashtag pages using the user's real Chrome browser to discover creator handles for outreach and engagement. Navigates hashtag pages, scrapes creator handles, and saves them to the follow-queue database with automatic deduplication and daily ceiling enforcement (50 candidates/day).

---

## Role & Expertise

TikTok Scout Specialist with deep knowledge of:
- **TikTok Hashtag Architecture** — understanding hashtag discovery pages and creator discovery patterns
- **Browser Automation via MCP** — using `mcp__claude-in-chrome` to navigate without bot detection
- **Data Collection & Deduplication** — scraping, filtering, and persisting handles safely
- **Handle Validation** — filtering blocked/sensitive values, spaces, and malformed data
- **{{brand.name}} Audience Alignment** — focusing on dog-food, nutrition, pet lifestyle creators

---

## Core Principles

### 1. No Bot Detection
- Use the user's real Chrome browser logged in to TikTok
- Automated scrolling + JS extraction mimics organic browsing
- More results when logged in; Chrome must have active TikTok session
- If TikTok requires login, manual login happens outside this skill

### 2. Safe Deduplication
- Never scrape the same handle twice (already-seen check before save)
- Exclude {{brand.name}}'s own account (`dogfoodandfun`) automatically
- Daily ceiling of 50 candidates/day enforced in save operation
- All candidates timestamped with discovery time and source hashtag

### 3. Data Quality First
- Filter out blocked/sensitive values (Chrome extension returns `[BLOCKED: ...]` for suspicious content)
- Remove handles with spaces or leading `[` (malformed)
- Validate handle format (lowercase letters, numbers, underscores only)
- Record source hashtag for each handle (traceability + analytics)

### 4. Transparent Reporting
- Report total handles scraped across all hashtags
- Break down new vs already-seen candidates
- Display today's candidate count vs daily ceiling (50)
- Alert if ceiling reached (no more candidates saved that day)

---

## Hashtag Target List

These hashtags are scraped in order, based on audience relevance:

1. `dogfood` — Direct recipes/food focus
2. `homemadedogfood` — DIY cooking interest
3. `dogrecipes` — Recipe-specific creators
4. `doglifestyle` — Lifestyle + dog content
5. `petnutrition` — Nutrition expertise
6. `rawdogfood` — Raw-feeding community
7. `dogmom` — Emotional dog-parent connection
8. `dogdad` — Male dog-parent audience
9. `healthydogfood` — Health-conscious creators

**Search Order:** Process in this order for consistent results across runs.

---

## Workflow: Step-by-Step Execution

### Prerequisites

**Browser State:**
- Chrome must be open and logged into TikTok (if login required)
- TikTok tab does not need to be active (MCP manages tabs)
- No VPN or proxy blocking (must be same network as {{brand.name}} user)

**Python Environment:**
- `app/lib/tiktok_scout/` modules available (state.py, candidate.py)
- PYTHONPATH set to `app/` for imports
- Python 3.9+ with pathlib and json support

**Rate Limiting:**
- TikTok allows ~4 hashtag pages per minute without IP block
- Script enforces 2-second wait between scrolls and 4-second initial page load
- If 429 rate-limit error, wait 30 minutes and retry

---

### Step 1: Load Chrome MCP Tools

Call `ToolSearch` with this query:

```
select:mcp__claude-in-chrome__tabs_context_mcp,mcp__claude-in-chrome__navigate,mcp__claude-in-chrome__javascript_tool,mcp__claude-in-chrome__tabs_create_mcp
```

This loads 4 Chrome tools:
- `mcp__claude-in-chrome__tabs_context_mcp` — Get current tab state
- `mcp__claude-in-chrome__tabs_create_mcp` — Create new browser tab
- `mcp__claude-in-chrome__navigate` — Navigate to URL
- `mcp__claude-in-chrome__javascript_tool` — Execute JS on page

**Why:** Each hashtag page takes 10–15 seconds to load and scroll. Batching operations per tab saves context switches.

---

### Step 2: Prepare Chrome Session

Call `mcp__claude-in-chrome__tabs_context_mcp` (no parameters) to fetch current browser state.

**Expected output:**
```json
{
  "tabs": [
    {
      "tab_id": "tab_0",
      "url": "https://www.tiktok.com/@yourprofile",
      "title": "TikTok Profile"
    }
  ],
  "active_tab": "tab_0"
}
```

Then call `mcp__claude-in-chrome__tabs_create_mcp` with no parameters to create a fresh tab for scouting.

**Why:** Separate tab keeps TikTok session isolated from user's active browsing.

---

### Step 3: Scrape Each Hashtag

For each hashtag in the list above, run this sequence:

#### 3a. Navigate to Hashtag Page

Call `mcp__claude-in-chrome__navigate` with:
```json
{
  "url": "https://www.tiktok.com/tag/{hashtag}",
  "wait_for": "CSS selector for video feed",
  "timeout_ms": 15000
}
```

**Example for `dogfood`:**
```json
{
  "url": "https://www.tiktok.com/tag/dogfood",
  "wait_for": "div[data-testid='explore_feed']",
  "timeout_ms": 15000
}
```

**Why:** TikTok hashtag pages lazy-load content. Wait for feed to appear before scraping.

#### 3b. Scroll and Extract Handles

Call `mcp__claude-in-chrome__javascript_tool` with this JavaScript code:

```javascript
await new Promise(r => setTimeout(r, 4000));
for (let i = 0; i < 4; i++) {
  window.scrollTo(0, document.body.scrollHeight);
  await new Promise(r => setTimeout(r, 2000));
}
const links = [...document.querySelectorAll('a[href*="/@"]')];
[...new Set(links.map(l => {
  const m = l.href.match(/\/@([^/?]+)/);
  return m?.[1]?.toLowerCase();
}).filter(Boolean))]
```

**What this does:**
1. Wait 4 seconds for initial page load
2. Scroll to bottom 4 times (loads more creators), wait 2 seconds between each
3. Extract all creator links matching `/@{handle}` pattern
4. Deduplicate with `Set()`, convert to lowercase, return as array

**Expected output:**
```javascript
["creator1", "creator2", "anotherhandle", ...]
```

**Response Type:** Array of strings (handles only, no `@` symbol)

---

### Step 4: Accumulate and Filter Handles

As each hashtag returns results, add them to a running set:

```python
all_handles = set()
hashtag_map = {}  # Maps handle -> source hashtag

# For each hashtag result:
for handle in js_result:
    # Filter out blocked/sensitive values
    if "[BLOCKED" in handle:
        continue
    # Filter malformed handles
    if " " in handle or handle.startswith("["):
        continue
    # Exclude own account
    if handle == "dogfoodandfun":
        continue
    
    all_handles.add(handle)
    hashtag_map[handle] = current_hashtag  # Track source
```

**Why:** Deduplication at collection time saves database calls later. Mapping source hashtags enables analytics.

---

### Step 5: Save Candidates to Database

After all hashtags complete, call this Python script via Bash. Run from the `app/` directory:

```bash
cd /Users/gilcohen/Projects/persona/app && PYTHONPATH=. python3 - <<'PYEOF'
import json, sys
from datetime import UTC, datetime
from pathlib import Path
sys.path.insert(0, '.')
from lib.tiktok_scout.state import save_candidates, is_already_seen, candidates_today, DAILY_SCOUT_CEILING
from lib.tiktok_scout.candidate import TikTokCandidate

# HANDLES and HASHTAG_MAP replaced by Claude with actual data
handles = HANDLES_PLACEHOLDER
hashtag_map = HASHTAG_MAP_PLACEHOLDER

now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
new_candidates = []
skipped = 0
for handle in handles:
    if is_already_seen(handle):
        skipped += 1
        continue
    new_candidates.append(TikTokCandidate(
        handle=handle,
        display_name=handle,
        bio="",
        follower_count=0,
        source_hashtag=hashtag_map.get(handle, "unknown"),
        discovered_at=now,
    ))

save_candidates(new_candidates)
print(f"Saved {len(new_candidates)} new candidates ({skipped} already seen). Today: {candidates_today()}/{DAILY_SCOUT_CEILING}")
PYEOF
```

**Variables Claude fills in:**
- `HANDLES_PLACEHOLDER` → Python list literal, e.g., `["creator1", "creator2", "handle3"]`
- `HASHTAG_MAP_PLACEHOLDER` → Python dict literal, e.g., `{"creator1": "dogfood", "creator2": "dogrecipes"}`

**Expected output:**
```
Saved 12 new candidates (8 already seen). Today: 37/50
```

**Why:** Database save is idempotent; script logs what was new vs duplicate. Daily ceiling enforced by `save_candidates()`.

---

### Step 6: Report Results to User

Parse the script output and display:

**Format:**
```
✓ TikTok Scout Complete

Hashtags Scraped: 9
Total Handles Found: 20
New Candidates Saved: 12
Already Seen (Skipped): 8

Today's Progress: 37/50 candidates
Daily Ceiling: Reached 74% (13 slots remain)

Top Hashtags by Yield:
• dogfood: 5 new
• homemadedogfood: 4 new
• dogrecipes: 3 new
• doglifestyle: 0 new
• petnutrition: 0 new
• rawdogfood: 0 new
• dogmom: 0 new
• dogdad: 0 new
• healthydogfood: 0 new
```

**If ceiling is reached:**
```
⚠ Daily Ceiling Reached (50/50)

No additional candidates can be saved today.
Run again tomorrow after midnight UTC.
```

**If 0 new candidates:**
```
ℹ No New Candidates Found

This hashtag set has been saturated. 
Wait 7 days for new creators or request manual hashtag update.
```

---

## Important Constraints & Gotchas

### Chrome Logging In

**Requirement:** TikTok must be logged in in Chrome *before* you run this skill.

- **How to check:** Ask user to open TikTok in Chrome and verify they're logged in (profile icon visible top-right)
- **If not logged in:** Ask user to log in manually, then re-run skill
- **Why:** Logged-in sessions see 3x more results on hashtag pages vs anonymous browsing

**If Chrome session expires:**
- TikTok will redirect to login page
- Script will navigate to hashtag URL but encounter login form
- JS selector `a[href*="/@"]` will find 0 handles
- Report: "0 handles found — TikTok login expired. Please log in and retry."

### Blocked/Sensitive Handles

The Chrome extension sometimes returns handles wrapped in `[BLOCKED: ...]` for content flagged by TikTok's moderation system.

**Example:** `"[BLOCKED: unsuitable_handle]"`

**Filter rule:** Skip any handle matching `[BLOCKED*` regex.

```python
if "[BLOCKED" in handle:
    continue
```

**Why:** Blocked handles link to restricted accounts; following them breaks the outreach cadence.

### Malformed Handles

TikTok allows handles with:
- Lowercase letters and numbers
- Underscores `_`
- Periods `.`

Do NOT allow:
- Spaces (` `)
- Leading `[` (indicates extension error)
- Empty strings

**Filter:**
```python
if " " in handle or handle.startswith("[") or len(handle) == 0:
    continue
```

### Own Account Exclusion

Always exclude `dogfoodandfun` (the {{brand.name}} TikTok account):

```python
if handle == "dogfoodandfun":
    continue
```

**Why:** Following your own account is pointless; wastes daily quota.

### Daily Ceiling Enforcement

The database enforces a **50-candidate-per-day maximum** to avoid aggressive follow spam.

**Ceiling resets at UTC midnight** (2 PM ET / 1 PM CT / 12 PM MT / 11 AM PT).

If today's count hits 50:
- `save_candidates()` returns success but saves 0 new records
- Script output: `"Saved 0 new candidates (12 already seen). Today: 50/50"`
- User should retry tomorrow

**Why:** TikTok has informal follow limits; spreading adds over multiple days avoids blocks.

### Rate Limiting by TikTok

If you see a 429 error when navigating:
```
HTTP 429: Too Many Requests
```

**Recovery:**
1. Wait 30 minutes (TikTok's IP-level rate limit window)
2. Retry the skill run
3. If still 429, check if brand IP is in TikTok's allowlist (rare)

**Why:** TikTok blocks aggressive scrapers at the IP level after ~100 requests/minute.

---

## Workflow Summary Table

| Step | Tool | Input | Output | Time |
|------|------|-------|--------|------|
| 1 | ToolSearch | MCP tool names | 4 Chrome tools loaded | 2 sec |
| 2 | tabs_context_mcp | — | Current tab state | 1 sec |
| 2 | tabs_create_mcp | — | New tab ID | 1 sec |
| 3a | navigate | URL + selector | Page loaded | 10 sec |
| 3b | javascript_tool | JS scroll + extract | Array of handles | 12 sec |
| 3 (repeat 9x) | navigate + js | 9 hashtags | 9 handle arrays | 198 sec (~3 min) |
| 4 | Python set dedup | Handle arrays | Unique set + map | 2 sec |
| 5 | Python save | Candidates object | DB rows + report | 3 sec |
| **Total** | — | — | Report to user | ~215 sec (~3.5 min) |

---

## Error Handling

| Error | Cause | Recovery |
|-------|-------|----------|
| `Chrome not open` | User closed browser | Ask: "Is Chrome open? Please open it and re-run." |
| `No tabs found` | Chrome is open but isolated | Create new tab; if error persists, restart Chrome |
| `Login expired` | TikTok logged out | Direct to TikTok tab, ask user to log in, retry |
| `0 handles from all hashtags` | Hashtag pages not loading | Check: Is TikTok accessible? Try refreshing. If persists, 30-min wait (rate limit). |
| `[BLOCKED: ...]` handles in results | TikTok moderation flag | Filter automatically; report count of blocked handles filtered |
| `Daily ceiling hit (50/50)` | Already 50 candidates today | Report to user: "You've reached today's quota. Run again after midnight UTC." |
| `PYTHONPATH error` | Python can't find lib module | Verify `app/` in working dir. Retry from correct path. |
| `is_already_seen() fails` | SQLite DB locked | Wait 5 seconds, retry. If persists, other process using DB. |

---

## Success Criteria

A successful scout run demonstrates:

✓ All 9 hashtags navigated without errors
✓ At least 1 handle extracted per hashtag (or 0 due to login/rate limit, with explanation)
✓ `dogfoodandfun` account excluded
✓ Blocked/malformed handles filtered
✓ Database save completes with count of new + skipped
✓ Today's candidate count reported vs daily ceiling
✓ Script output matches pattern: `"Saved {N} new candidates ({M} already seen). Today: {X}/{CEILING}"`

---

## Optimization Tips

### Faster Scrapes
- **Parallel hashtags:** If Chrome MCP supports multiple tabs, scrape 3 hashtags simultaneously (halves time to ~2 min)
- **Reduced scroll depth:** Change loop from 4 scrolls to 2 if results plateau (saves 4 sec/hashtag)

### Better Results
- **Logged-in accounts have 3x yield:** Ensure Chrome TikTok session is fresh (not cookie-expired)
- **Off-peak hours:** TikTok returns fresher creators 12–4 AM UTC (fewer new posts = less cache refresh)
- **Randomize hashtag order:** Rotate order weekly to avoid TikTok algorithm targeting repeating patterns

### Storage
- **Candidate DB location:** `app/data/tiktok/candidates.json` (JSON store; can be backed up to `.cloudinary/tiktok_candidates_backup.json`)
- **Seen set:** Persisted in SQLite for fast O(1) lookups; never delete (only add)

---

## Dependencies

### Python Modules
- `lib.tiktok_scout.state` — `save_candidates()`, `is_already_seen()`, `candidates_today()`, `DAILY_SCOUT_CEILING`
- `lib.tiktok_scout.candidate` — `TikTokCandidate` dataclass
- `pathlib`, `json`, `datetime` — standard library

### External Services
- **TikTok.com** — Live internet access required
- **Chrome via MCP** — User's real browser + mcp__claude-in-chrome setup

### Data Files
- `app/data/tiktok/` — Directory for candidate storage (auto-created if missing)
- `app/lib/tiktok_scout/` — Core modules (must exist in codebase)

### Environment
- Python 3.9+
- Bash shell for Python script execution
- UTC timezone support for date calculations

---

## Invocation Patterns

Users will trigger this skill with:
- **"Run TikTok scout"** → Full 9-hashtag run
- **"Scout TikTok"** → Same (shorthand)
- **"Find TikTok candidates"** → Same (semantic)
- **"TikTok-scout"** → Same (hyphenated trigger)
- **"Scrape TikTok hashtags"** → Same (explicit)
- **"Find TikTok creators to follow"** → Same (outcome-focused)

When invoked, always confirm:
1. Is Chrome open and logged into TikTok?
2. Should we proceed with the 9 target hashtags?

If either answer is "no", guide the user to prepare Chrome first.

---

## Example Session: Full Scout Run

**User Input:** "Run TikTok scout"

**Agent Steps:**

1. **Confirm Prerequisites:**
   - "I'll scout TikTok hashtags for {{brand.name}}. First, confirm: Is Chrome open and logged into TikTok?"
   - User: "Yes, I'm logged in."

2. **Load Tools:**
   - Call ToolSearch to load 4 Chrome MCP tools ✓

3. **Prepare Session:**
   - Call tabs_context_mpc to get current state ✓
   - Call tabs_create_mcp to open fresh tab ✓

4. **Scrape 9 Hashtags:**
   - dogfood → 5 handles
   - homemadedogfood → 4 handles
   - dogrecipes → 3 handles
   - doglifestyle → 2 handles
   - petnutrition → 1 handle
   - rawdogfood → 2 handles
   - dogmom → 1 handle
   - dogdad → 0 handles (low-follower creators already seen)
   - healthydogfood → 2 handles
   - **Total:** 20 unique handles (2 filtered for "[BLOCKED]", 1 for spaces)

5. **Accumulate:**
   - Set: {creator1, creator2, ..., creator20}
   - Map: {creator1: "dogfood", creator2: "homemadedogfood", ...}

6. **Save to Database:**
   - Run Python script with actual handles
   - Output: `"Saved 12 new candidates (8 already seen). Today: 37/50"`

7. **Report to User:**
   ```
   ✓ TikTok Scout Complete
   
   Hashtags Scraped: 9
   Total Handles Found: 20
   New Candidates Saved: 12
   Already Seen (Skipped): 8
   
   Today's Progress: 37/50 candidates
   Daily Ceiling: 74% (13 slots remain)
   ```

**User:** "Great! Schedule this for daily runs."

**Agent:** "Scout is ready. I recommend running at 2 PM ET daily (off-peak UTC hours for fresher results). Set a cron job with: `0 14 * * * /path/to/tiktok_scout.sh`"

---

## File Structure

After running scout, this file tree exists:

```
app/
├── lib/
│   └── tiktok_scout/
│       ├── __init__.py
│       ├── candidate.py       # TikTokCandidate dataclass
│       ├── state.py           # Save/load/dedup logic
│       └── criteria.py        # Filter rules (if any)
├── data/
│   └── tiktok/
│       ├── candidates.json    # All candidates (append-only)
│       ├── seen.db            # SQLite: seen handles (dedup)
│       └── daily_log.json     # Each day's run summary
└── scripts/
    └── tiktok_scout.py        # Runnable script (calls this skill)
```

**Backup location:** `dogfoodandfun/data/tiktok/candidates_backup.json` (manual backup; not auto-updated)

---

## Quality Assurance

Before confirming a scout run is complete:

- [ ] All 9 hashtags attempted (report which ones, if any, failed)
- [ ] Handle count >0 or explanation (login, rate limit, network)
- [ ] `dogfoodandfun` not in results
- [ ] No `[BLOCKED`, spaces, or malformed handles in report
- [ ] Database save message printed with new + skipped counts
- [ ] Today's candidate count <= 50
- [ ] Report includes yield per hashtag (optional but helpful)

---

*Last Updated: 2026-06-30*
*Version: 1.0*

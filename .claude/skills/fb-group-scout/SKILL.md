---
name: fb-group-scout
description: >
  Search Facebook for new dog-related groups (public AND private) to join and expand
  {{brand.domain}} reach. Scores each group against selection criteria, presents a
  shortlist for approval, then sends join requests. Enforces a 3 joins/week pace to
  avoid FB flags. Use when the user says "scout facebook groups", "find new groups to
  join", "expand facebook reach", "run group scout", or "find dog groups on facebook".
---

# Facebook Group Scout — {{brand.name}}

Find new Facebook groups worth joining as {{brand.persona}}. Both public and private groups
are in scope — private groups often have higher engagement quality. All join requests
require user approval before sending. Max 3 join requests per week.

---

## Pre-flight Checks

```python
import sys, json, re
from pathlib import Path
from datetime import date, datetime, timedelta, timezone
sys.path.insert(0, '../lib')

# Re-run guard — skip if scout already ran successfully this week
# (weekly cadence, not daily — group scouting is a weekly task)
last_run_file = Path('../.claude/state/last_run.json')
last_run = json.loads(last_run_file.read_text()) if last_run_file.exists() else {}
scout_last = last_run.get('fb_group_scout', {})
scout_last_date = (scout_last.get('last_run_at') or '')[:10]

today = date.today()
days_since_last = 999
if scout_last_date:
    days_since_last = (today - date.fromisoformat(scout_last_date)).days

if days_since_last < 7 and scout_last.get('status') == 'success':
    print(f"SKIP: fb-group-scout already ran successfully {days_since_last} day(s) ago ({scout_last_date}).")
    print("Pass --force to override.")
    if '--force' not in sys.argv:
        exit(0)
    print("--force detected, re-running.\n")

# Check weekly join request pace (max 3/week to avoid FB flags)
log_file = Path('../logs/engagement_log.jsonl')
week_ago = (today - timedelta(days=7)).isoformat()
join_requests_this_week = 0
if log_file.exists():
    with log_file.open() as f:
        for line in f:
            try:
                entry = json.loads(line)
                if (entry.get('action') == 'group_join_request'
                        and entry.get('date', '') >= week_ago):
                    join_requests_this_week += 1
            except Exception:
                continue

JOIN_BUDGET = max(0, 3 - join_requests_this_week)
print(f"Join request budget this week: {JOIN_BUDGET}/3")
if JOIN_BUDGET == 0:
    print("ABORT: 3 group join requests already sent this week. Try again next week.")
    exit(0)
```

---

## Load Known Groups

Build a set of groups already joined or previously requested — never propose duplicates.

```python
# Load from tracker spreadsheet (facebook_groups_tracker.xlsx)
# Read the "Groups Database" sheet — extract group URLs and names already known
tracker_path = Path('../../../facebook_groups_tracker.xlsx')
known_groups = set()  # lowercase group names and URLs

# Use openpyxl or read via the xlsx skill
# Extract: group_url, group_name columns — add all to known_groups set
# Also add groups with status "join_requested" (pending approval already sent)

# Load from engagement log (join_request actions)
if log_file.exists():
    with log_file.open() as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get('action') == 'group_join_request':
                    known_groups.add(entry.get('target_url', '').lower())
                    known_groups.add(entry.get('target_name', '').lower())
            except Exception:
                continue

print(f"Already known groups: {len(known_groups)}")
```

---

## Search Queries

Run Facebook Group searches for each of these queries. Use the saved Facebook session
(`facebook_session.json`) via Playwright — same session used by fb-scanner.

```python
SESSION_FILE = Path('../.claude/state/facebook_session.json')
if not SESSION_FILE.exists():
    print("ERROR: No saved Facebook session. Run fb_login.py first.")
    exit(1)

SEARCH_QUERIES = [
    # Food / nutrition
    "homemade dog food",
    "raw dog food",
    "dog nutrition",
    "dog food recipes",
    "dog diet advice",
    # GPS / active lifestyle
    "running with dogs",
    "canicross",
    "GPS dog tracker",
    "dog hiking",
    # General dog owner communities
    "dog owners community",
    "dog lifestyle",
    "healthy dogs",
    "dog product reviews",
]
```

---

## Group Evaluation

For each group found in search results, extract and score against these criteria:

### Data to Extract Per Group

```python
group_data = {
    "name": "",           # group display name
    "url": "",            # full group URL
    "privacy": "",        # "public" or "private"
    "member_count": 0,    # integer
    "post_frequency": "", # "a few posts a day", "a few posts a week", etc.
    "description": "",    # group description (first 300 chars)
    "found_via_query": "",# which search query surfaced it
}
```

### Scoring Rubric (0–100)

| Signal | Points | Logic |
|---|---|---|
| **Niche keyword match** | 0–30 | group name/description contains food/nutrition (+15), GPS/active (+10), dog lifestyle (+5) — additive |
| **Member count** | 0–20 | 1K–10K (+20), 10K–50K (+15), 50K–150K (+10), <1K or >150K (+0) |
| **Activity level** | 0–20 | "a few posts a day" (+20), "a few posts a week" (+10), "a few posts a month" (+0) |
| **Private group bonus** | +10 | Private groups tend to have tighter community + more trust — add 10 pts |
| **Competitor admin penalty** | −40 | If group name or description mentions Tractive, Fi Collar, Whistle, Ollie, Nom Nom, Farmer's Dog as the group *brand* |
| **Already known** | Skip | Don't surface groups already in tracker or previously requested |

**Minimum score to surface: 40 points.**

```python
COMPETITOR_BRANDS = {
    "tractive", "fi collar", "ficollar", "whistle", "link akc",
    "ollie dog", "nom nom", "farmer's dog", "open farm",
}

def score_group(g: dict) -> int:
    score = 0
    text = (g["name"] + " " + g["description"]).lower()

    # Niche match
    if any(kw in text for kw in ["food", "nutrition", "recipe", "diet", "raw", "kibble"]):
        score += 15
    if any(kw in text for kw in ["gps", "tracker", "running", "canicross", "hike", "trail"]):
        score += 10
    if any(kw in text for kw in ["dog owner", "dog lifestyle", "dog product", "dog health"]):
        score += 5

    # Member count
    mc = g["member_count"]
    if 1_000 <= mc <= 10_000:
        score += 20
    elif 10_000 < mc <= 50_000:
        score += 15
    elif 50_000 < mc <= 150_000:
        score += 10

    # Activity
    freq = g["post_frequency"].lower()
    if "day" in freq:
        score += 20
    elif "week" in freq:
        score += 10

    # Private group bonus
    if g["privacy"] == "private":
        score += 10

    # Competitor penalty
    if any(brand in text for brand in COMPETITOR_BRANDS):
        score -= 40

    return max(0, score)
```

---

## Playwright Extraction Script

Use this JS to extract group cards from Facebook search results:

```javascript
() => {
    const cards = [];
    // Group cards on /search/groups/ page
    const links = Array.from(document.querySelectorAll('a[href*="/groups/"]'));
    const seen = new Set();

    for (const a of links) {
        const href = a.getAttribute('href') || '';
        const match = href.match(/\/groups\/([^/?]+)/);
        if (!match || match[1] === 'feed' || match[1] === 'discover') continue;
        const groupId = match[1];
        if (seen.has(groupId)) continue;
        seen.add(groupId);

        // Walk up to find the card container
        let card = a;
        for (let i = 0; i < 6; i++) {
            if (!card.parentElement) break;
            card = card.parentElement;
        }

        const text = card.innerText || '';
        const lines = text.split('\n').filter(l => l.trim());

        // Extract member count from text like "1.2K members" or "45,000 members"
        let memberText = '';
        let privacyText = '';
        let postFreq = '';
        for (const line of lines) {
            if (line.match(/member/i)) memberText = line;
            if (line.match(/public|private/i)) privacyText = line;
            if (line.match(/post/i)) postFreq = line;
        }

        cards.push({
            url: 'https://www.facebook.com' + href.split('?')[0],
            name: lines[0] || groupId,
            privacy: privacyText.toLowerCase().includes('private') ? 'private' : 'public',
            member_text: memberText,
            post_frequency: postFreq,
            description: lines.slice(3, 6).join(' '),
        });
    }
    return cards.slice(0, 20);
}
```

Parse `member_text` into integer:
```python
def parse_member_count(text: str) -> int:
    text = text.lower().replace(",", "")
    m = re.search(r"([\d.]+)\s*k", text)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r"([\d.]+)\s*m", text)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else 0
```

---

## Approval Shortlist

After scoring all found groups, present the **top candidates** (score ≥ 40, up to 10) for user approval:

```
=== Facebook Group Scout — Found {N} candidates ===

Showing top {min(N, 10)} groups, ranked by score:

──────────────────────────────────────────────────────
 #1  Dog Food Recipes & Nutrition Tips  [PRIVATE]
     Members: 8,400  |  Activity: A few posts a day  |  Score: 75
     URL: https://www.facebook.com/groups/...
     Found via: "homemade dog food"
     Description: "Share your favorite recipes and tips for feeding dogs a healthy diet..."
     → JOIN REQUEST? (yes/skip)

 #2  Canicross & Running Dogs  [PUBLIC]
     Members: 22,000  |  Activity: A few posts a week  |  Score: 55
     URL: https://www.facebook.com/groups/...
     Found via: "canicross"
     → JOIN REQUEST? (yes/skip)
──────────────────────────────────────────────────────

Join budget remaining: {JOIN_BUDGET}/3 this week
Type "yes" to approve all above, or respond with specific numbers (e.g. "1 3 5")
```

Wait for user response before sending any join requests.

---

## Sending Join Requests

For each approved group (up to `JOIN_BUDGET`):

**Public groups:** Click the "Join group" button — you are added immediately.

**Private groups:** Click the "Join group" or "Request to join" button — submits a
join request to group admins. This is intentional — private groups are worth requesting.

```javascript
// Find and click join/request button
() => {
    const btns = Array.from(document.querySelectorAll('[role="button"]'));
    const joinBtn = btns.find(b => {
        const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
        return t.includes('join') || t.includes('request');
    });
    if (joinBtn) {
        joinBtn.click();
        return 'clicked';
    }
    return 'not_found';
}
```

After clicking, verify the button state changed (should now show "Pending", "Joined",
or "Requested"). Log the outcome.

**Delay between join requests:** Wait 60–180 seconds between each request (random).
Never send multiple join requests back-to-back.

---

## Log Each Join Request

```python
from datetime import date, datetime, timezone
import json

log_entry = {
    "date": date.today().isoformat(),
    "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
    "platform": "facebook",
    "action": "group_join_request",
    "target_name": group["name"],
    "target_url": group["url"],
    "privacy": group["privacy"],   # "public" or "private"
    "member_count": group["member_count"],
    "score": group["score"],
    "found_via": group["found_via_query"],
    "status": "requested",         # "requested" | "joined" (public = immediate)
}

log_file = Path('../logs/engagement_log.jsonl')
log_file.parent.mkdir(parents=True, exist_ok=True)
with log_file.open('a') as f:
    f.write(json.dumps(log_entry) + '\n')
```

Also append a row to `facebook_groups_tracker.xlsx`:

| group_name | group_url | privacy | member_count | score | joined_date | status | found_via |
|---|---|---|---|---|---|---|---|
| Dog Food Recipes... | https://... | private | 8400 | 75 | 2026-04-15 | join_requested | homemade dog food |

---

## Update Last Run

```python
last_run['fb_group_scout'] = {
    'last_run_at': datetime.now(timezone.utc).isoformat(),
    'groups_found': groups_found,
    'groups_approved': groups_approved,
    'join_requests_sent': join_requests_sent,
    'status': 'success',
}
last_run_file.write_text(json.dumps(last_run, indent=2))
```

---

## Summary Report

```
=== Facebook Group Scout Complete ===
Queries searched:      {N}
Groups evaluated:      {total_evaluated}
Groups surfaced (≥40): {candidates}
Groups approved:       {approved}
Join requests sent:    {sent} (public: X joined immediately, private: Y pending)
Join budget remaining: {3 - total_this_week}/3 this week

Joined / Requested:
  ✅ [Group Name] (private, 8.4K members) — pending admin approval
  ✅ [Group Name] (public, 22K members) — joined immediately
```

---

## Rules Summary

| Rule | Value |
|---|---|
| Max join requests per week | 3 |
| Minimum group score | 40 / 100 |
| Min member count | 1,000 |
| Max member count | 150,000 |
| Private groups | ✅ Included — "Request to join" sent |
| Competitor-run groups | ❌ Skipped (−40 score penalty) |
| Already-joined groups | ❌ Skipped |
| Groups with pending request | ❌ Skipped |
| Delay between requests | 60–180 seconds (random) |
| User approval required | ✅ Always — no auto-join |

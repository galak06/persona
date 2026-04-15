---
name: fb-scanner
description: >
  Scan joined Facebook dog groups for posts worth engaging with. Score each post
  by relevance to dogfoodandfun.com content (food, GPS, health, running). Queue
  high-scoring posts for comment-composer. Enforce rate limits. Use when the user
  says "run facebook scan", "scan facebook groups", "find posts to comment on",
  "run fb scanner", or "run daily facebook scan".
---

# Facebook Group Scanner — DogFoodAndFun

Scan joined Facebook groups for high-relevance posts to engage with as Nalla's Dad.
Queue qualifying posts to `.claude/state/comment_queue.json` for comment-composer.

---

## Pre-flight Checks

Before scanning, run these checks:

```python
# Check rate limits
import sys
sys.path.insert(0, '../lib')
from rate_limiter import can_act, print_status, get_daily_status

# Check if we have any group_visit budget
if not can_act('facebook', 'group_visit'):
    print("ABORT: Daily group visit limit reached. Try again tomorrow.")
    exit(0)

print_status()
```

```python
# Load joined groups from Excel
import pandas as pd
df = pd.read_excel('../facebook_groups_tracker.xlsx', sheet_name='Groups Database')
joined = df[
    (df['Joined?'].str.contains('Joined', na=False)) &
    (~df['Self-Promo Allowed?'].str.contains('No', na=False))
][['Group Name', 'Facebook URL', 'Category']].dropna(subset=['Facebook URL'])

print(f"Groups to scan: {len(joined)}")
```

```python
# Load dedup cache
from deduplication import is_duplicate
```

---

## Load Last Run Timestamp

```python
import json
from pathlib import Path
from datetime import datetime

last_run_file = Path('../.claude/state/last_run.json')
last_run = {}
if last_run_file.exists():
    with last_run_file.open() as f:
        last_run = json.load(f)

fb_last_run = last_run.get('fb_scanner', {}).get('last_run_at')
print(f"Last FB scan: {fb_last_run or 'never'}")
```

---

## Scan Each Group

For each group in the joined list:

1. **Check rate limit** — if `group_visit` budget exhausted, stop and report what was scanned.
2. **Navigate to group feed** — go to the group URL.
3. **Wait random delay** — call `wait_random_delay('facebook', 'group_visit')`.
4. **Record visit** — call `record_action('facebook', 'group_visit')`.

### Extract Posts

Use JavaScript to extract post content from the group feed:

```javascript
// Extract post cards from Facebook group feed
const posts = [];
document.querySelectorAll('[data-pagelet*="GroupFeed"] [role="article"]').forEach(article => {
    const textEl = article.querySelector('[data-ad-preview="message"]') ||
                   article.querySelector('[dir="auto"]');
    const linkEl = article.querySelector('a[href*="/groups/"][href*="/posts/"]');
    const metaEl = article.querySelector('abbr[data-utime]') ||
                   article.querySelector('a[role="link"] span abbr');
    
    if (textEl && linkEl) {
        posts.push({
            text: textEl.innerText.substring(0, 800),
            url: linkEl.href,
            timestamp: metaEl?.title || '',
            commentCountEl: article.querySelector('[aria-label*="comment"]')?.textContent || '0'
        });
    }
});
posts.slice(0, 10); // return top 10
```

If JavaScript extraction returns empty, fall back to `get_page_text` and parse manually.

### Score Each Post

For each extracted post, call the relevance scorer. In the agent context, inline the scoring logic:

```
Scoring rules (sum the matching signals):

FOOD/NUTRITION (max 0.55):
+0.25 — post mentions: dog food, recipe, homemade, kibble, ingredients, diet, feeding, meal, grain
+0.15 — post mentions transition/troubleshooting: "kibble to homemade", "transition", "protein sensitivity",
         "digestive", "switching food", "upset stomach", "elimination diet", "picky eater"
         — these are high-hook posts; your templates are strongest here
+0.15 — post mentions ingredient specifics: protein rotation, calcium, omega, raw, BARF, batch cook

GPS/ACTIVE (max 0.30):
+0.30 — post mentions: GPS, tracker, running, canicross, trail, hiking, gear, fi, tractive, collar

ENGAGEMENT SIGNALS:
+0.20 — post is a question (contains "?") or is explicitly seeking advice
+0.20 — post mentions brand reviewed on dogfoodandfun.com: Fi, Tractive, Whistle, Ollie, Nom Nom,
         Farmer's Dog, Open Farm — comparison posts score even higher (both brands mentioned = +0.20)
+0.15 — post engagement rate: >0.5 comments/minute (hot post, early engagement window)
+0.10 — comment count between 5 and 50 (engaged but not flooded)
+0.10 — post timestamp is under 2 hours old (fresh window, comment visible longer)
+0.05 — post timestamp is 2-24 hours old (still viable)

PENALTIES:
-0.30 — comment count over 100 (too crowded, comment gets buried)
-0.50 — post is authored by a known brand/commercial account (check author username vs
         official accounts: fi, tractivepets, whistlepet, olliedog — not just mentions)
-0.20 — author has >50K followers (likely reseller or commercial account)
+0.10 — comparison post (mentions 2+ brands) — good engagement opportunity even if competitor brands

Post age calculation (use post timestamp from metaEl):
  minutes_old = (now - post_time_utc).seconds / 60
  comments_per_minute = comment_count / max(minutes_old, 1)

Threshold to queue: 0.75
Borderline (requires approval): 0.75–0.80
Auto-approve: ≥ 0.80
```

### Dedup Check

```python
from deduplication import is_duplicate
post_id = post_url.split('/posts/')[-1].split('/')[0].split('?')[0]
if is_duplicate('facebook', post_id):
    print(f"  SKIP (already engaged): {post_id}")
    continue
```

---

## Build Comment Queue

For each post that passes scoring + dedup check:

```python
import json
from pathlib import Path
from datetime import datetime

queue_file = Path('../.claude/state/comment_queue.json')
queue = []
if queue_file.exists():
    with queue_file.open() as f:
        queue = json.load(f)

# Determine category from group category field
category_map = {
    '🍖': 'food',
    '🏃': 'gps',
    '🏥': 'health',
    '🎾': 'training',
    '🐾': 'general',
}
category = next((v for k, v in category_map.items() if k in group_category), 'food')

queue.append({
    "platform": "facebook",
    "post_url": post_url,
    "post_id": post_id,
    "post_text": post_text[:600],
    "group_name": group_name,
    "group_url": group_url,
    "category": category,
    "relevance_score": score,
    "queued_at": datetime.utcnow().isoformat() + "Z",
    "status": "pending",
    "requires_approval": score < 0.80,  # borderline scores need approval
})

with queue_file.open('w') as f:
    json.dump(queue, f, indent=2)
```

---

## Update Last Run Timestamp

```python
last_run['fb_scanner'] = {
    'last_run_at': datetime.utcnow().isoformat() + "Z",
    'groups_scanned': groups_scanned_count,
    'posts_queued': posts_queued_count,
}
with last_run_file.open('w') as f:
    json.dump(last_run, f, indent=2)
```

---

## Summary Report

After scanning all groups, print:

```
=== Facebook Scan Complete ===
Groups scanned: X / Y (rate limit hit at Z)
Posts evaluated: N
Posts queued for comments: M
  - High confidence (score ≥ 0.85): A
  - Needs approval (0.75–0.84): B
Posts skipped — already engaged: K
Posts skipped — below threshold: J

Rate limit remaining today: X group visits, Y comments
```

---

## Error Handling

- **Session expired** → print "ABORT: Facebook session expired. Re-login and retry." Log to `../logs/errors.log`.
- **Group page 404** → skip group, log as "GROUP_UNAVAILABLE", continue.
- **JS extract returns empty** → try `get_page_text` fallback. If still empty, skip group after 1 retry.
- **Rate limit hit mid-scan** → stop gracefully, save queue as-is, print partial summary.

---

## Key Rules

- NEVER attempt to post a comment in this skill — that is comment-composer's job
- NEVER visit more than 10 groups per day (hard limit)
- NEVER engage with competitor account posts
- Always use `wait_random_delay` between group visits — no back-to-back navigation
- Facebook screenshots go black after scroll — use `get_page_text` + `find` for all content reading

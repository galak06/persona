---
name: comment-composer
description: >
  Draft, validate, and post comments from the comment queue. Reads pending items
  from .claude/state/comment_queue.json, drafts Nalla's Dad-voice comments using
  templates + Claude, validates voice rules, presents for approval (where required),
  then posts. Use when the user says "post queued comments", "run comment composer",
  "post pending comments", "draft comments", or "post tonight's comments".
---

# Comment Composer — DogFoodAndFun

Draft and post queued comments as Nalla's Dad. Voice validation required before
every post. Approval gates for first-post groups, links, and all Instagram content.

---

## Pre-flight Checks

```python
import sys, json
from pathlib import Path
from datetime import date, datetime, timedelta
sys.path.insert(0, '../lib')

# Re-run guard — skip if comment-composer already ran successfully today
last_run_file = Path('../.claude/state/last_run.json')
last_run = json.loads(last_run_file.read_text()) if last_run_file.exists() else {}
cc_last = last_run.get('comment_composer', {})
cc_last_date = (cc_last.get('last_run_at') or '')[:10]
if cc_last_date == date.today().isoformat() and cc_last.get('status') == 'success':
    print(f"SKIP: comment-composer already ran successfully today ({cc_last_date}).")
    print("Pass --force to override.")
    if '--force' not in sys.argv:
        exit(0)
    print("--force detected, re-running.\n")
```
from rate_limiter import can_act, get_daily_status, print_status
from deduplication import is_duplicate, mark_engaged
from comment_generator import validate_voice, generate_comment

# Build set of groups/hashtags we've previously engaged with
# (used by approval gate — skip manual approval if we have history there)
log_file = Path('../logs/engagement_log.jsonl')
previously_posted_groups = set()
template_usage = {}  # {group_name: {template_snippet: last_used_date}}
if log_file.exists():
    with log_file.open() as f:
        for line in f:
            try:
                entry = json.loads(line)
                if entry.get('action') in ['comment', 'like']:
                    previously_posted_groups.add(entry['target_name'])
                    # Track template usage per group for 30-day repeat prevention
                    if entry.get('action') == 'comment' and entry.get('content'):
                        grp = entry['target_name']
                        snippet = entry['content'][:40]  # first 40 chars as template key
                        if grp not in template_usage:
                            template_usage[grp] = {}
                        template_usage[grp][snippet] = entry.get('date', '')
            except Exception:
                continue
print(f"Previously engaged with {len(previously_posted_groups)} groups/hashtags")

# Validate and repair dedup cache if corrupted
try:
    from deduplication import is_duplicate, mark_engaged
    _ = is_duplicate('facebook', '_test_preflight_')  # test read
except Exception as e:
    print(f"WARNING: Dedup cache corrupted ({e}). Resetting.")
    dedup_file = Path('../.claude/state/dedup_cache.json')
    dedup_file.write_text('{}')

# Load site content cache for context (built by site-analyzer)
site_cache_file = Path('../data/site_content_cache.json')
site_cache = {}
if site_cache_file.exists():
    with site_cache_file.open() as f:
        site_cache = json.load(f)
    recent_posts = site_cache.get('recent_posts', [])
    print(f"Site cache: {len(recent_posts)} recent posts loaded ({site_cache.get('cached_at', 'unknown')})")
else:
    print("WARNING: No site content cache found. Run site-analyzer first for best results.")
    recent_posts = []

print_status()
```

---

## Load Comment Queue

```python
queue_file = Path('../.claude/state/comment_queue.json')
if not queue_file.exists():
    print("Queue is empty. Run fb-scanner or ig-scanner first.")
    exit(0)

with queue_file.open() as f:
    queue = json.load(f)

pending = [item for item in queue if item['status'] == 'pending']
print(f"\nPending comments: {len(pending)}")
```

---

## Process Each Queued Post

For each pending item, in order of relevance score (highest first):

### Step 1: Rate limit check

```python
platform = item['platform']
action = 'ig_comment' if platform == 'instagram' else 'comment'
if not can_act(platform, action):
    print(f"STOP: Daily {platform} comment limit reached.")
    break
```

### Step 2: Re-dedup check

```python
if is_duplicate(platform, item['post_id']):
    item['status'] = 'skipped_duplicate'
    continue
```

### Step 3: Load post page to verify it still exists

Navigate to the post URL. Use `get_page_text` to verify the post is still accessible.
If page returns 404 or login wall → mark status "POST_UNAVAILABLE", skip.

### Step 4: Generate comment draft

Pull the most relevant recent site posts from `site_cache` to potentially reference:

```python
# Find site posts matching the comment category
category = item['category']
relevant_site_posts = [
    p for p in recent_posts
    if category in p.get('categories', []) or category in p.get('tags', [])
][:3]
```

Generate comment using templates first, then Claude if no template matches:

```python
result = generate_comment(
    post_text=item['post_text'],
    category=item['category'],
    group_name=item.get('group_name') or item.get('hashtag', ''),
)

if result['method'] == 'needs_generation':
    # Use Claude directly with the provided prompt
    # The agent should use its own language model capability to draft:
    # - Reference the post content specifically
    # - Follow brand_voice_guide.md rules exactly
    # - Include a relevant tip from recent_site_posts if natural
    # - End with a question
    # Draft the comment using the result['prompt'] as your instruction
    draft = <GENERATE USING result['prompt'] + site context>
else:
    draft = result['comment']
```

### Step 5: Voice validation

```python
is_valid, violations = validate_voice(draft)

if not is_valid:
    print(f"\n⚠️ Voice validation failed:")
    for v in violations:
        print(f"  - {v}")
    print(f"\nDraft: {draft}")
    # Ask agent to revise — re-generate with violations explicitly called out
    # Retry once. If still fails, mark status "VALIDATION_FAILED" and skip.
```

### Step 6: Approval gate

Determine if this comment requires user approval:

```python
group_or_tag = item.get('group_name') or item.get('hashtag', '')
is_new_group = group_or_tag not in previously_posted_groups

# Check template reuse (30-day rule per group)
template_snippet = draft[:40]
last_used = template_usage.get(group_or_tag, {}).get(template_snippet)
template_reused_recently = False
if last_used:
    days_since = (datetime.utcnow().date() - datetime.fromisoformat(last_used).date()).days
    template_reused_recently = days_since < 30

# Skip own posts
if 'dogfoodandfun.com' in item.get('post_text', '').lower():
    item['status'] = 'skipped_own_post'
    continue

requires_approval = (
    item.get('requires_approval', False) or
    platform == 'instagram' or                          # ALL IG requires approval
    'dogfoodandfun.com' in draft.lower() or             # URL in comment
    is_new_group or                                     # first post to this group
    template_reused_recently                            # same template used < 30 days ago
)
```

If approval required → **PAUSE and show to user:**

```
=== Comment Approval Required ===

Platform: {platform}
Group/Hashtag: {group_name or hashtag}
Post preview: "{post_text[:200]}..."
Relevance score: {score}

Proposed comment:
---
{draft}
---

Approve? (yes/edit/skip)
```

Wait for user response. If "edit" → accept revised comment text. If "skip" → mark status "USER_SKIPPED".

### Step 7: Post the comment

**Facebook:**

1. Navigate to the post URL
2. Find the comment input:
```javascript
const commentBox = document.querySelector('[contenteditable="true"][data-lexical-editor="true"]') ||
                   document.querySelector('[placeholder*="Write a comment"]') ||
                   document.querySelector('[aria-label*="comment"]');
if (commentBox) {
    commentBox.focus();
    'found';
} else {
    'not_found';
}
```

3. Click the comment area to activate it
4. Use `form_input` or `javascript_tool` to type the text:
```javascript
document.execCommand('insertText', false, commentText);
```

5. Find and click the Post/Submit button:
```javascript
const submitBtn = Array.from(document.querySelectorAll('[role="button"]'))
    .find(b => b.getAttribute('aria-label') === 'Comment' ||
               b.textContent.trim() === 'Post');
if (submitBtn) submitBtn.click();
```

**Instagram:**

1. Navigate to post URL
2. Click the comment field (`[placeholder*="Add a comment"]`)
3. Type comment text
4. Click Post button

### Step 8: Record action + update dedup

```python
from rate_limiter import record_action, wait_random_delay
from deduplication import mark_engaged
import json

record_action(platform, action)
mark_engaged(platform, item['post_id'], 'comment', 
             item.get('group_name') or item.get('hashtag', ''))

item['status'] = 'posted'
item['posted_at'] = datetime.utcnow().isoformat() + "Z"
item['comment_text'] = draft

# Save updated queue
with queue_file.open('w') as f:
    json.dump(queue, f, indent=2)

# Wait before next action
wait_random_delay(platform, action)
```

### Step 9: Log to activity logger

Call `activity-logger` skill after each successful post.

---

## Update Last Run

```python
last_run['comment_composer'] = {
    'last_run_at': datetime.utcnow().isoformat() + "Z",
    'comments_posted': posted_count,
    'comments_skipped': skipped_count,
    'comments_pending_approval': approval_pending_count,
    'status': 'success',
}
last_run_file = Path('../.claude/state/last_run.json')
last_run_file.parent.mkdir(parents=True, exist_ok=True)
last_run_file.write_text(json.dumps(last_run, indent=2))
```

---

## Summary Report

```
=== Comment Composer Complete ===
Comments posted: X (FB: A, IG: B)
Comments awaiting approval: Y
Comments skipped: Z (duplicate: N, validation failed: M, unavailable: P)

Rate limits remaining today:
  Facebook comments: A/5
  Instagram comments: B/2

Posted:
  ✅ [Group Name] — "{comment[:80]}..."
  ✅ [Group Name] — "{comment[:80]}..."
```

---

## Error Handling

- **Comment box not found** → try scrolling to comments section, retry once. If still not found, mark "COMMENT_BOX_NOT_FOUND", log error.
- **Post button not found after typing** → try pressing Enter. If fails, mark "POST_FAILED".
- **Session expired mid-post** → log "SESSION_EXPIRED", abort remaining queue.
- **Validation fails twice** → mark "VALIDATION_FAILED", log the bad draft + violations.

---

## Key Rules

- NEVER post without passing voice validation (both passes failed = skip)
- NEVER post a Facebook comment with a URL without explicit user approval
- ALL Instagram comments require user approval before posting
- ALWAYS wait random delay between posts (never back-to-back)
- Max 5 FB comments/day, 2 IG comments/day — hard stops
- Reference site content naturally when relevant, never force it

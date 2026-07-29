---
name: ig-scanner
description: >
  Scan Instagram hashtags for posts relevant to {{brand.domain}} content.
  Like qualifying posts. Queue high-relevance posts for comments.
  Enforce Instagram rate limits (20 likes/day, 10 comments/day).
  Use when the user says "run instagram scan", "scan instagram hashtags",
  "scan ig", "run ig scanner", or "run daily instagram scan".
---

# Instagram Hashtag Scanner — {{brand.name}}

Scan priority hashtags for relevant posts, like them, and queue top candidates
for comment-composer. Rate limits: 20 likes/day, 10 comments/day (hard limits).

This skill runs via Playwright (same approach as fb-scanner). No Chrome MCP
required — works in `claude -p` and interactive mode.

---

## How to Run

Run the Playwright-based scanner script:

```bash
cd /Users/gilcohen/Projects/persona/app
python scripts/ig_scan.py
```

### First-time setup

If no saved Instagram session exists, the script will tell you. Run the login
script first — it opens a browser for you to log in manually:

```bash
python scripts/ig_login.py
```

After login, the session is saved to `.claude/state/instagram_session.json` and
reused for future scans.

---

## What the Script Does

1. **Pre-flight checks** — verifies saved session exists and daily like limit not hit
2. **Loads today's hashtags** — from `data/instagram_accounts.csv`, filtered by scan frequency
3. **Scans each hashtag page** — navigates to `/explore/tags/{hashtag}/`, extracts post links
4. **Evaluates each post** — opens post, reads caption/likes/comments/author
5. **Scores relevance** — uses `lib/comment_generator.score_relevance()` + IG-specific adjustments:
   - +0.15 if post has < 500 likes (real engagement possible)
   - -0.20 if post has > 5000 likes (we'd be lost in the noise)
6. **Likes qualifying posts** — clicks like button if score >= 0.75 and budget remains
7. **Queues top posts for comments** — score >= 0.85 AND post is a question, max 10/day
8. **Updates state** — dedup cache, rate limits, last run timestamp, comment queue

---

## After the Scan

Review the summary output. If posts were queued for comments, run comment-composer:

```bash
# Check what was queued
cat .claude/state/comment_queue.json | python -m json.tool

# Then run comment-composer to draft and approve comments
```

ALL Instagram comments require manual user approval — they are never posted automatically.

---

## Error Handling

- **Session expired** — script prints "Re-run: python scripts/ig_login.py" and exits
- **Hashtag blocked** — skipped, logged to `logs/errors.log`
- **Like button not found** — skipped, logged
- **Rate limit hit** — stops gracefully, saves partial results

---

## Key Rules

- ALL Instagram comments require user approval — never post without explicit confirmation
- Max 20 likes/day, max 10 comments/day — hard limits, stop immediately when reached
- Random delays (10–45s) between actions to reduce bot detection risk
- Never like competitor brand accounts' posts (Fi, Tractive, Whistle corporate accounts)
- Never interact with the same post twice within 60 days (dedup check)

---

## LLM Prompt

<!-- MACHINE-READ SECTION. Loaded by app/lib/skill_loader.py and sent verbatim
     (after {{brand.*}} rendering) as the LLM SYSTEM prompt for the ig-scanner
     flow (scripts/ig_scan.py). Nothing outside this section is sent to the
     model. Per-post context and the JSON response format are supplied by the
     Python flow at call time. -->

You are {{brand.persona}}, the voice behind {{brand.name}} ({{brand.domain}}), commenting on Instagram as a real person whose dog {{brand.mascot}} is part of the brand's story.

BRAND VOICE — authentic, warm, and specific to the brand persona:
- Warm, specific, slightly analytical; not salesy, not clinical
- Mention the brand mascot by name ONLY if it fits naturally — don't force
- No "check out our site" / "buy now" / "link in bio" / "I'm a vet" / medical claims
- No generic praise ("Great post!", "Love this!", "Amazing!")
- No emojis at the start; 0-1 emoji max total
- End with one specific question tied to what they said — not "what do you think?"
- 1-3 sentences, under 450 chars total
- NEVER invent facts — no made-up diets, durations, ages, gear,
  or experiences ("we've fed raw for a year", "3 weeks to implement"). Only state
  things that are actually true (see BRAND FACTS if provided).
- When you have no true specific to share, stay first-person but general
  ("in our experience", "we've noticed") and lead with genuine
  curiosity about THEIR experience instead of fabricating a story.

Before drafting, decide whether THIS SPECIFIC post is genuinely worth
engaging with as our brand. Decline (engage=false) if:
- the post is generic/low-effort and a reply would feel like spam
- our brand has no authentic, specific angle on this exact post
- the post is from a competitor account
- replying here would feel repetitive or forced rather than genuine

If you decide to engage, the reply should be a single short reply (1-3 sentences). Personal, helpful, no salesy language, no medical claims, no links.

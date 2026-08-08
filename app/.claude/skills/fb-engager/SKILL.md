---
name: fb-engager
description: >
  Scan joined Facebook groups (not hashtags) for posts relevant to
  {{brand.domain}} content. Like qualifying posts. Draft and post a short
  comment inline for posts that clear the auto-approve threshold — single
  pass, no separate queue/compose step. Enforce Facebook rate limits (5
  likes/day, 5 comments/day). Use when the user says "run facebook scan",
  "scan facebook groups", "run fb scanner", "run daily facebook scan",
  "run fb engager", or "scan fb groups and comment".
---

# Facebook Group Engager — {{brand.name}}

Scan joined Facebook groups for relevant posts, like them, and — for posts
that clear the auto-approve threshold — draft and post a short comment in
that same visit as {{brand.persona}}. Rate limits: 5 likes/day, 5
comments/day (hard limits — `facebook:` block, app/CLAUDE.md Rate Limits).

Single pass, like `ig-engager`: each post is opened once, scored, liked, and
(if it qualifies) commented on in that one visit — no Redis/JSON queue and
no separate `fb-comment` drain run. This differs from the older two-stage
`fb-scanner` → `fb-comment` split (scan-and-queue, then a separate drain
script); `fb-engager` folds both steps into one run via
`lib.engagement.pipeline.run_outbound_scan(..., inline_comment=True)`, using
`FacebookGroupAdapter`, which already implements the `comment()` method
(`SupportsComment`) that inline mode requires — the same mechanism
`scripts/ig_engager.py` already uses for Instagram.

---

## How to Run

```bash
cd /Users/gilcohen/Projects/persona/app
python scripts/fb_engager.py
```

Requires a saved Facebook session (`.claude/state/facebook_session.json`) —
the same session used by `fb-scanner`/`fb-comment`. If it's missing or
expired the run aborts with `SESSION_EXPIRED`; re-establish it the same way
those skills do.

---

## What the Script Does

1. **Pre-flight checks** — verifies the saved FB session and that today's
   `like` budget isn't already exhausted (`can_act('facebook', 'like')`).
2. **Loads joined groups** — from `lib.groups_db`, the same source
   `fb-scanner` and `fb-group-publisher` read (Self-Promo/eligibility
   filters applied the same way).
3. **Scans each group feed** — via `FacebookGroupAdapter`, extracting post
   text/url/author/comment-count/timestamp for each visible post.
4. **Scores relevance** — `lib.comment_generator.score_relevance()`, the
   same food/GPS/engagement signals `fb-scanner` uses.
5. **Likes qualifying posts** — if the post scores at or above the queue
   threshold (0.75) and today's like budget remains.
6. **Drafts + posts a comment inline** — for posts clearing the
   auto-approve threshold (>= 0.80, `content_analysis.scoring_weights`),
   calls the bound drafter (this skill's `## LLM Prompt` below) for an
   `{engage, comment, reason}` decision. `engage: true` posts the comment
   immediately via `post_comment_fb`; `engage: false` skips the post with
   `reason` logged. This decision IS the approval — matching
   `fb-comment`/`ig-comment`, there is no separate human-in-the-loop step.
7. **Updates state** — marks every opened post via `lib.scan_dedup` (so a
   re-run never re-opens it, independent of whether it was engaged),
   records rate-limit counters, and writes the last-run timestamp.

---

## Key Rules

- Max 5 likes/day, max 5 comments/day — hard limits, stop immediately when
  reached (`facebook:` block, app/CLAUDE.md Rate Limits).
- Never engage with a post from a known competitor or commercial account.
- Never re-open a post already scanned in a prior run.
- Random delays between group visits and between actions — no back-to-back
  navigation.
- The `engage`/`comment`/`reason` decision from the LLM Prompt below is the
  approval gate for the comment — no separate Telegram confirmation.

---

## LLM Prompt

<!-- MACHINE-READ SECTION. Loaded by app/lib/skill_loader.py and sent verbatim
     (after {{brand.*}} rendering) as the LLM SYSTEM prompt for the fb-engager
     flow (scripts/fb_engager.py). Nothing outside this section is sent to the
     model. Per-post context and the JSON response format are supplied by the
     Python flow at call time. -->

You are {{brand.persona}}, the voice behind {{brand.name}} ({{brand.domain}}), commenting in Facebook groups as a real person whose dog {{brand.mascot}} is part of the brand's story.

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

If you decide to engage, the reply should be ONE short sentence (15-25 words) replying to the post above. React to a SPECIFIC detail from THIS post, mention {{brand.mascot}} or our own experience, and end with a brief genuine question. No greeting, no generic opener, no salesy language, no medical claims, no links.

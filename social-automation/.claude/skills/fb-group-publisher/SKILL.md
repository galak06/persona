---
name: fb-group-publisher
description: >
  Publish a dogfoodandfun.com blog post to relevant Facebook groups. Use this skill
  whenever the user says "post to groups", "publish to Facebook groups", "share my post",
  "push to groups", or "distribute my article". The skill reads the groups tracker Excel
  file to check rules and join status, drafts tailored post text for each group category,
  verifies rules compliance, then posts via the browser — logging every post back to the
  Excel file. Always use this skill for any Facebook group posting task for dogfoodandfun.
---

# Facebook Group Publisher — Dog Food & Fun

Publish a blog post to the right Facebook groups, respecting each group's rules, and log every post.

## Data Source

Groups database and post log live at:
```
/dogfoodandfun/facebook_groups_tracker.xlsx
```
(relative to the user's selected workspace folder)

Sheets:
- **Groups Database** — all groups with rules, membership status, join status, category
- **Post Log** — history of every post (date, group, URL, status)
- **Post Templates** — reusable text templates per content category

## Workflow

### Step 1 — Identify the Post

Ask the user (or extract from context):
1. **Blog post URL** to share
2. **Post category** — GPS/Gear | Recipe/Food | Nutrition/Review | Training | General
3. **Target region** — USA/Canada (default) | Israel | Global

### Step 2 — Load Eligible Groups

Read `facebook_groups_tracker.xlsx` → Groups Database sheet.

Filter to groups where **all** of the following are true:
- Region Focus matches target (USA/Global for USA/Canada; 🇮🇱 Israel for Israel)
- Self-Promo Allowed? = ✅ Yes
- Links Allowed? = ✅ Yes
- Joined? ≠ ❌ Not Joined (skip unjoined private groups)
- Category matches the post type

Show the user the filtered list and confirm before posting.

**If a private group shows "❌ Not Joined":**
- Navigate to the group URL
- Click "Join group"
- Update the Excel: set Joined? → "⏳ Request Sent"
- Skip posting to it this session (come back once approved)

**If rules show ⚠️ Check (not yet audited):**
- Navigate to the group's `/about` page
- Read the rules section
- Update the Excel with the actual rules
- Proceed only if no self-promotion ban found

### Step 3 — Draft Post Text

Pick the matching template from the Post Templates sheet and fill in:
- `[RECIPE NAME]`, `[URL]`, `[TIME]`, `[PRODUCT]` etc.
- Always put the link in the **first comment** for large groups (>50K members) to avoid algorithmic suppression — mention this in the post body as "link in first comment 👇"
- For smaller groups (<50K), include the link directly in the post body

**Nalla's Dad voice reminders:**
- Lead with a relatable question or story
- Mention Nalla by name where natural
- End with an engagement hook (question for the community)
- Never sound like a medical/veterinary professional
- Keep it conversational, not salesy

### Step 4 — Post to Each Group

For each eligible group:
1. Navigate to `https://www.facebook.com/groups/{group_id}`
2. Click "Write something…" composer
3. Type the drafted post text
4. Confirm with user before clicking **Post** ("Ready to post to [Group Name]?")
5. Click Post
6. If posting as a Page: verify the composer shows "Dog Food and Fun" as the poster

### Step 5 — Log Each Post

After each successful post, update the **Post Log** sheet in the Excel:

| Column | Value |
|---|---|
| Date Posted | Today's date |
| Group Name | Group name |
| Post Title / Blog URL | Blog post URL |
| Post Text | First 100 chars of what was posted |
| Status | ✅ Posted / ⚠️ Pending Approval / ❌ Failed |
| Notes | Any relevant detail |

### Step 6 — Summary

After all groups are done, report:
- ✅ Posted to: [list]
- ⏳ Pending join approval: [list]
- ⚠️ Rules check needed: [list]
- ❌ Skipped (rules ban): [list]

---

## Key Rules Reference

| Group | Self-Promo | Links | Notes |
|---|---|---|---|
| Running with Dogs | ✅ | ✅ | Gear & tips welcome. Private — need approval. |
| Outside running walker dogs | ✅ | ✅ | No explicit rules. Post freely. |
| Running Dogs New | ✅ | ✅ | No explicit rules. Low activity. |
| Canicross For Beginners | ❌ | ❌ | Rule 3 bans self-promo. **DO NOT POST.** |
| Homemade dog food recipes | ⚠️ | ⚠️ | Audit rules before posting. |
| Healthy Homemade Dog Food Recipes | ⚠️ | ⚠️ | Audit rules before posting. |
| Dog Behavior and Training Tips | ⚠️ | ⚠️ | Audit rules before posting. |

Always check the Excel for the most up-to-date rules and join status — it's the source of truth.

---

## Post Text Best Practices

- **Big groups (>50K):** Put link in first comment, not the post body
- **Small groups (<50K):** Link in post body is fine
- **Israel groups:** Write in Hebrew or bilingual
- **GPS/Gear posts:** Lead with safety/trail angle, mention Nalla's real-world testing
- **Recipe posts:** Open with "Nalla went crazy for this" or similar
- **Training posts:** Start with a quick tip, link for full details
- **Frequency:** Max 2-3 posts per group per week to avoid spam flags

---

## Posting as Dog Food and Fun Page

When the post composer opens, verify it shows **"Dog Food and Fun"** as the author (not a personal profile). If it shows a personal profile, click the author dropdown and switch to the Dog Food and Fun page before posting.

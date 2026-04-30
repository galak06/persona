# Brand Review — Implementation Plan for Claude Code

Source of findings: `/marketing:brand-review` against `dogfoodandfun.com`, FB page, IG `dogfoodandfun`.
Target executor: Claude Code (CLI) with credentials already present in `social-automation/.claude/settings.local.json`.
Owner persona: "Nalla's Dad" — informative and relatable, never medical/clinical.
Market focus: US & Canada.

---

## 0. Goals

1. Close HIGH-severity compliance gaps: "Zero Affiliate Bias" claim (Amazon Associates IS active, tag `dogfoodfun01-20`), "best" superlative on FB, missing not-medical-advice disclaimer, undefined AAFCO, missing affiliate disclosure page.
2. Bridge the voice fracture: website is cold-analytical, FB/IG are warm-fuzzy — they must read as the same brand.
3. Establish the positioning gap identified in the competitive brief: **transparent methodology + single-engineer POV** — the anti-credentialing play against Dog Food Advisor / WDJ / PetMD.
4. Keep every change reversible. No irreversible actions without explicit `--apply`.

---

## 1. Execution environment

Claude Code runs from `/Users/gilcohen/Projects/dogfoodandfun/social-automation/` — Claude auto-hydrates `.claude/settings.local.json` env into the process environment. Use the existing vars directly via `os.environ`:

| Var | Used for |
|---|---|
| `WP_URL` | WordPress base URL |
| `WP_USER` | WP Application Password username (`claude_user`) |
| `WP_APP_PASSWORD` | WP REST Basic Auth |
| `FB_PAGE_ID` | Known page ID from existing social-automation |
| `FB_PAGE_TOKEN` | Page access token (Graph API) |
| `FB_USER_TOKEN` | User token, fallback if Page token is limited |
| `IG_ACCOUNT_ID` | IG Business account Graph ID |
| `AMAZON_ASSOCIATES_TAG` | Affiliate tag — confirmation affiliates are used |

**Do not create a new `.env` file. Do not duplicate secrets.** Read straight from the hydrated environment.

---

## 2. Surface inventory

| Surface | Tech | Notes |
|---|---|---|
| Website | WordPress + Astra theme | Hostinger hosting. SFTP via `claudeuser` is broken (writes to wrong dir). **REST-only via core `wp/v2/*` endpoints.** |
| Facebook Page | Meta Graph API v21+ | **ID mismatch to resolve** — see §4 |
| Instagram | Instagram Graph API | `IG_ACCOUNT_ID` present → implies Business/Creator already linked to a Page |

Pre-flight:
- `GET /wp-json/wp/v2/` returns 200 → core REST live
- `GET /wp-json/wp/v2/plugins` with Basic Auth → plugin inventory for footer-hook routing decision
- `GET /{FB_PAGE_ID}?fields=about&access_token=...` → confirms token + page identity
- `GET /{IG_ACCOUNT_ID}?fields=biography,username&access_token=...` → confirms IG access

---

## 3. Phase 0 — Bootstrap & safety

| Step | Action |
|---|---|
| 0.1 | Create `brand-fix/` at **`/Users/gilcohen/Projects/dogfoodandfun/brand-fix/`** — sibling to `social-automation/`, at repo root. **Do not run `git init`. No commits. Everything stays local files only.** |
| 0.2 | Grep `social-automation/` for existing Graph API + WP REST helpers: `rg -n "graph.facebook.com\|wp/v2\|biography\|wp-json" social-automation/`. Reuse what's there; don't rewrite. |
| 0.3 | `brand-fix/` structure: `patches/` (WP content diffs), `meta/` (FB/IG payloads), `backups/` (timestamped JSON snapshots), `scripts/`, `runs/` (per-invocation artifacts). |
| 0.4 | `Makefile` targets: `preflight`, `backup`, `dry-run`, `apply`, `rollback`, `verify`. |
| 0.5 | All mutating scripts default to dry-run. `--apply` flag required to commit. |
| 0.6 | **No git.** All history lives as filesystem artifacts: timestamped backups in `backups/`, dry-run diffs saved to `brand-fix/runs/YYYYMMDD-HHMMSS/diff.txt`. |

Backup before any mutation:
- `backups/wp-pages-YYYYMMDD.json` via `GET /wp-json/wp/v2/pages?per_page=100&context=edit`
- `backups/wp-posts-YYYYMMDD.json` same for posts
- `backups/meta-YYYYMMDD.json` current FB `about` + IG `biography`
- `backups/wp-options-YYYYMMDD.json` key theme_mods if reachable

---

## 4. Phase 0.5 — Resolve FB Page identity (BLOCKING)

Brand review pulled copy from `https://www.facebook.com/profile.php?id=61586923685573` (2 followers, "Nalla's Dad" bio). Existing `FB_PAGE_ID` in settings is a different ID.

Run both:
```
GET https://graph.facebook.com/v21.0/{FB_PAGE_ID}?fields=id,name,username,link,fan_count,about
GET https://graph.facebook.com/v21.0/61586923685573?fields=id,name,username,link,fan_count,about
```

Report both results side-by-side. Ask Gil which page is the active business page. Do not update any FB bio until this is resolved. If there are two pages, flag as a cleanup decision separate from this plan.

---

## 5. Phase 1 — Discovery (auto-run, read-only)

Autonomy rule: **any read-only call runs without asking.** Anything that writes, mutates, posts, or deletes requires explicit `--apply` + Gil's approval on the diff.

Run these without pausing, then produce a single consolidated report:

**A.** `GET /wp-json/wp/v2/plugins` — identify which are installed and REST-reachable, especially Astra Pro (`astra-addon`), Code Snippets (`code-snippets`), WPCode (`insert-headers-and-footers`). Decides the footer-disclaimer route.

**B.** `rg -n "graph.facebook.com|wp/v2|biography|wp-json" social-automation/` — inventory reusable Graph/WP helpers. Report which functions can be imported vs. what needs to be written fresh.

**C.** Locate any existing backup convention: `find social-automation/ -type d -name 'backups*' -o -name 'snapshots*'` + grep scripts for backup paths. If one exists, reuse it; otherwise `brand-fix/backups/`.

**D.** Phase 0.5 FB page identity — see §4.

**The only question that requires Gil's decision:** which FB page is the active business page. Present both responses side-by-side and ask. Nothing else.

---

## 6. Phase 2 — Affiliate compliance (HIGHEST PRIORITY)

Amazon Associates is confirmed active (`AMAZON_ASSOCIATES_TAG=dogfoodfun01-20`). Fix in this order:

| # | Action | Route |
|---|---|---|
| 2.1 | Create `/disclosure/` page with FTC-compliant Amazon Associates language | `POST /wp-json/wp/v2/pages` |
| 2.2 | Create `/methodology/` page — publish the scoring rubric, ingredient weights, cost-per-serving formula (positioning differentiator vs. DFA's opaque "proprietary" scoring) | `POST /wp-json/wp/v2/pages` |
| 2.3 | Rewrite hero trust badge "Zero Affiliate Bias" → "Open Methodology" (or "Independent Reviews" as fallback) | `PATCH /wp-json/wp/v2/pages/<home-id>` on `content.raw` |
| 2.4 | Add sitewide footer disclaimer linking to `/disclosure/` + `/methodology/` | Route decided after Phase 1 Q-A |
| 2.5 | Auto-inject per-post affiliate disclosure banner on any post containing `amzn.to`, `/dp/`, or `tag=dogfoodfun01-20` | Code snippet plugin if available, else manual WP filter registered through whatever route exists |

`/disclosure/` page copy (FTC-aligned, draft):

> **Disclosure**
>
> Dog Food & Fun participates in the Amazon Services LLC Associates Program, an affiliate advertising program designed to provide a means for sites to earn advertising fees by advertising and linking to Amazon.com. When you click a link to Amazon from this site and make a purchase, I may earn a small commission at no extra cost to you.
>
> This does not affect product selection or review conclusions. I choose products to review based on reader questions and my own research, not on commission rates. The scoring methodology is [public](/methodology/) — you can see exactly how every product is rated.
>
> **Not a veterinarian.** I'm a software engineer and dog owner. Nothing on this site is veterinary medical advice. Always consult your vet before changing your dog's diet or starting any new health routine.
>
> Contact: [add preferred contact].

---

## 7. Phase 3 — WordPress copy edits

Discovery first — do not assume where each string lives:

1. `GET /wp-json/wp/v2/pages?slug=home&context=edit` → locate homepage ID.
2. Parse `content.raw` for anchors: `Dog food reviews written like spec sheets`, `Zero Affiliate Bias`, `Join Our Community of Analytical Dog Owners`, `Engineer by Day`, `vet-heading`, `How We Review`.
3. If any anchor is absent, search `/wp-json/wp/v2/blocks` (reusable blocks), `/wp-json/wp/v2/widgets` (core 5.8+), or Astra theme_mods via `wp/v2/settings`.

Edits:

| # | Finding | Route | Final copy |
|---|---|---|---|
| 3.1 | Hero subhead | homepage `content.raw` | `Numbers, not adjectives. Nalla-approved.` |
| 3.2 | Trust badges | homepage `content.raw` | `50+ Brands Tested · Open Methodology · Nalla-Approved` |
| 3.3 | About H2 + opener | homepage `content.raw` | See §10 |
| 3.4 | Newsletter CTA (`vet-heading`) | homepage `content.raw` (inline, not a widget per scrape) | `Get the next review in your inbox. No hype, no fillers.` |
| 3.5 | "Food & Diet" card | homepage `content.raw` | Expand AAFCO on first use; see §10 |
| 3.6 | Not-medical-advice callout | reusable block (`POST /wp-json/wp/v2/blocks`) + inject at top of food-diet category posts | See §10 |

Diff protocol: before any PATCH, print unified diff of `content.raw` old → new and save to `brand-fix/runs/<timestamp>/diff.txt`. `--apply` required to commit.

---

## 8. Phase 4 — Meta (Facebook + Instagram)

Gated on Phase 0.5 resolution. Payloads in `brand-fix/meta/bios.yaml`:

```yaml
fb:
  about: |
    Hi, I'm Nalla's Dad! 👋 Engineer who reads dog food labels so you don't have to.
    Honest, data-driven reviews — no hype, no fillers. 🐕
    https://dogfoodandfun.com
ig:
  # max 150 chars — validate before send
  biography: |
    🐕 Nalla's Dad · Tel Aviv
    🧪 Engineer reading dog food labels so you don't have to
    📊 Reviews → dogfoodandfun.com
```

FB update: `POST https://graph.facebook.com/v21.0/{active_page_id}` with `about=...&access_token=$FB_PAGE_TOKEN`.
IG update: `POST https://graph.facebook.com/v21.0/{IG_ACCOUNT_ID}` with `biography=...&access_token=$FB_PAGE_TOKEN`.

Length validation:
```python
assert len(ig_bio) <= 150, f"IG bio too long: {len(ig_bio)} chars"
```

Rate limit: exponential backoff, 3 retries, base 2s.

---

## 9. Phase 5 — Verification

`brand-fix/scripts/verify.py` runs after `--apply`:

| Check | Method | Pass criteria |
|---|---|---|
| Homepage copy | `curl https://dogfoodandfun.com/` + regex | New strings present, old strings absent |
| Disclosure page live | `curl https://dogfoodandfun.com/disclosure/` | 200 + expected H1 |
| Methodology page live | `curl https://dogfoodandfun.com/methodology/` | 200 + expected H1 |
| Footer disclaimer | Fetch home + 1 post + 1 category | Selector present on all |
| FB bio | `GET /{active_page_id}?fields=about` | Equals `bios.yaml.fb.about` |
| IG bio | `GET /{IG_ACCOUNT_ID}?fields=biography` | Equals `bios.yaml.ig.biography` |
| Newsletter form | `POST /wp-admin/admin-ajax.php?action=vet_subscribe` with test email | 200 — ensures CSS/JS not broken by length change |
| Affiliate banner | Fetch 1 post containing `amzn.to` | Disclosure banner selector present |

Exit non-zero on any failure.

---

## 10. Final copy (source of truth)

### Hero
- Subhead: `Numbers, not adjectives. Nalla-approved.`
- Trust badges: `50+ Brands Tested · Open Methodology · Nalla-Approved`

### About section
- H2: `Engineer by Day, Label-Reader by Night`
- Body:
  > I'm not a vet — I'm a software engineer who got tired of vague pet food marketing. So I started reading ingredient labels like spec sheets and comparing cost-per-serving across brands. "Premium" isn't a data point. This is what I wish existed when I got Nalla. The [scoring methodology is public](/methodology/) — no black boxes.

### Newsletter CTA
- Headline: `Get the next review in your inbox. No hype, no fillers.`

### "Food & Diet" category card
- `We decode the labels — every AAFCO panel (the industry standard for pet food ingredient disclosure), every filler, every hidden carb load. Honest breakdowns of kibble, wet food, and treats.`

### Footer disclaimer (sitewide)
- `Dog Food & Fun is written by a dog owner, not a veterinarian. Nothing here is medical advice. [Disclosure](/disclosure/) · [Methodology](/methodology/)`

### Not-medical-advice reusable block (food-diet posts)
- `Heads up: I'm not a vet — I'm a software engineer and dog owner. This post shares what worked for Nalla and what the data shows. Always talk to your vet before changing your dog's diet or treating a health issue.`

### FB bio (post Phase 0.5)
```
Hi, I'm Nalla's Dad! 👋 Engineer who reads dog food labels so you don't have to.
Honest, data-driven reviews — no hype, no fillers. 🐕
https://dogfoodandfun.com
```

### IG bio (≤150 chars)
```
🐕 Nalla's Dad · Tel Aviv
🧪 Engineer reading dog food labels so you don't have to
📊 Reviews → dogfoodandfun.com
```

---

## 11. Rollback

`brand-fix/scripts/rollback.py --stamp YYYYMMDD`:
1. For each page in `backups/wp-pages-YYYYMMDD.json`, PATCH `content.raw` back.
2. For FB + IG, POST saved `about` / `biography` from `backups/meta-YYYYMMDD.json`.
3. For posts that got the affiliate banner injected, restore from `backups/wp-posts-YYYYMMDD.json`.

Idempotent — running twice is a no-op.

---

## 12. Order of operations (blast radius ascending)

1. Additive: `/disclosure/` + `/methodology/` pages + footer disclaimer (zero risk, compliance-critical, positioning-critical).
2. Hero trust badge: "Zero Affiliate Bias" → "Open Methodology" (single homepage PATCH, compliance + positioning).
3. Meta bios (fast, reversible, low visibility — gated on Phase 0.5).
4. About section copy (single page, reversible).
5. Hero subhead + newsletter CTA (homepage-visible, reversible).
6. Category card + AAFCO expansion.
7. Not-medical-advice callout — test on Canned Pumpkin post, roll out to food-diet category.
8. Affiliate banner auto-injection (content-type change, test on one post before wide).

---

## 13. Ground rules for Claude Code

1. Read this file end-to-end before touching anything.
2. Run `preflight` from `social-automation/` cwd — env already hydrated, no `.env` loading.
3. Abort if any pre-flight returns 4xx/5xx.
4. **Read-only discovery (§5 A–D) runs autonomously — do not ask per call.** Produce a single consolidated report. Stop only for the one decision: which FB page is active (Phase 0.5).
5. Run `scripts/backup.py` before any mutation. Backups are timestamped JSON files in `brand-fix/backups/` — **do not commit, do not touch git, everything stays local.**
6. Grep `social-automation/` for reusable clients before writing new ones (§3.2).
7. Execute phases in §12 order. Every mutating step:
   a. Print unified diff and save it to `brand-fix/runs/<timestamp>/diff.txt`.
   b. Require `--apply`; otherwise dry-run only.
8. Run `scripts/verify.py` after each `--apply`. On any failure, stop and report.
9. Per-phase artifacts (diffs, API responses, verify results) go to `brand-fix/runs/<timestamp>/`. No git commits.
10. If something is ambiguous, ask — do not guess.
11. **Read-only calls never need approval. Mutating calls always do.** When in doubt, print the diff and wait for `--apply`.

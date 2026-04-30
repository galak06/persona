---
name: recipe-publisher
description: Generates a single dog-friendly recipe in the Nalla's Dad voice and publishes it to WordPress + Instagram. Replaces the n8n recipes workflow. Invoke on the weekly cadence (Sun 09:00 IST) or on-demand with a specific topic.
version: 0.1.0
inputs:
  - name: topic
    required: false
    description: Explicit recipe idea, e.g. "Beef liver training treats". If omitted, pulls next from state/ideas_queue.json.
  - name: dry_run
    required: false
    default: true
    description: When true, generates + writes a draft markdown report to /mnt/dogfoodandfun/ but does NOT publish to WP or IG. Default true for safety.
  - name: skip_ig
    required: false
    default: false
    description: Publish to WP only. Useful when IG token is mid-refresh.
trigger:
  - launchd: weekly, Sun 09:00 IST (com.dogfoodandfun.recipe-publisher.plist)
  - manual: claude run recipe-publisher --topic "..." [--no-dry-run]
outputs:
  - WordPress post (status=publish or draft based on dry_run)
  - Instagram post (single image, carousel deferred to v1.1)
  - state/last_run.json updated with timestamp + result
  - state/published_recipes.json appended (dedup cache)
  - Report at /mnt/dogfoodandfun/recipe-publisher-report-YYYY-MM-DD.md (always)
secrets_required:
  - WP_URL
  - WP_USER
  - WP_APP_PASSWORD
  - IG_USER_ID
  - IG_GRAPH_ACCESS_TOKEN
  - ANTHROPIC_API_KEY
  - REPLICATE_API_TOKEN  # or IMAGE_PROVIDER_KEY for whichever provider
---

# recipe-publisher

Replaces the n8n "recipes" workflow with a Python skill that lives in-repo, runs from launchd alongside the other scanners (`fb_scanner`, `ig_scanner`, `comment_composer`, `site_analyzer`), and shares the same secrets + state conventions.

## When to invoke

- **Weekly autonomous run.** launchd fires `claude run recipe-publisher` at 09:00 IST every Sunday. Pulls the next topic from `state/ideas_queue.json`, dedups against `state/published_recipes.json`, generates, publishes, updates state.
- **On-demand.** `claude run recipe-publisher --topic "Beef liver training treats" --no-dry-run` to publish a specific recipe immediately.
- **Dry-run rehearsal.** Default. Writes the full recipe + IG caption to a markdown report so you can sanity-check voice before committing.

## Runtime flow

```
load_config + load_state
  → pick_topic (cli arg | ideas_queue.json | LLM brainstorm fallback)
  → dedup_check (against published_recipes.json)
  → generate_recipe (Claude via Anthropic SDK, prompts/recipe_system.md)
  → generate_image (Replicate by default, recipe.image_brief as prompt)
  → brand_review (optional, marketing:brand-review skill)
  → publishers.wordpress.publish (media upload → post create → SureRank meta → schema injected)
  → publishers.instagram.publish (container → publish, uses WP-hosted image URL)
  → write_state (last_run.json + published_recipes.json)
  → emit report (always, regardless of dry_run)
```

## Failure modes + how the skill handles them

| Failure | Behavior |
|---|---|
| ANTHROPIC_API_KEY missing | Hard fail before any external call; report explains which secret is missing. |
| WP returns 4xx on media upload | Abort before post create; report includes the error body. |
| WP post created but SureRank meta call fails | Post stays as draft; report flags for manual review. Avoids publishing with default `%post_content%` meta. |
| IG token expired | Skill attempts one refresh via long-lived token endpoint; if that fails, WP post still goes live, IG step is skipped, report flags it. |
| Image generation fails | Falls back to a configured default image; report flags it. |
| Recipe duplicates an existing published title | Skipped; report shows the conflict. |

All failures write to `last_run.json` with `status: "failed"` + `error` and write the report — never silent.

## Secrets

Read from environment at startup. The skill assumes Claude CLI (or launchd via `run_with_watchdog.py`) has injected them from project secrets — no `.env` parsing inside the skill itself.

```
WP_URL=https://dogfoodandfun.com
WP_USER=<wp username>
WP_APP_PASSWORD=<24-char app password>
IG_USER_ID=<numeric ig business account id>
IG_GRAPH_ACCESS_TOKEN=<long-lived page token>
ANTHROPIC_API_KEY=sk-ant-...
REPLICATE_API_TOKEN=r8_...
```

## State files

- `state/last_run.json` — heartbeat, same shape as the other scanners. Read by `run_with_watchdog.py` for the daily rollup.
- `state/published_recipes.json` — list of `{title, slug, wp_post_id, ig_media_id, published_at}` for dedup.
- `state/ideas_queue.json` — list of `{topic, notes, priority}` to pull from. Manually curated for now; v1.1 may add an LLM-suggester.

## What this skill is NOT

- Not a backfill tool. It only publishes new recipes; existing n8n-published recipes are left as-is. (See migration plan §2.5 — backfill is parked for v1.1.)
- Not a comment moderator. That's `comment_composer`.
- Not a site analyzer. That's `site_analyzer`.

## See also

- Migration plan: `/mnt/dogfoodandfun/recipe-publisher-migration-plan.md`
- Audit handoff: `/mnt/dogfoodandfun/recipe-audit-handoff.md`

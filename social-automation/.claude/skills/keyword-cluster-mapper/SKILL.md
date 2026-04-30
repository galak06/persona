---
name: keyword-cluster-mapper
description: >
  Group all DogFoodAndFun.com keyword targets into pillar+spoke topical clusters
  so posts form internal-link networks rather than disconnected islands. Reads
  keywords from the posts sheet and site content cache, clusters them by topical
  similarity, identifies pillar (broadest) and spoke (narrow) keywords per
  cluster, and outputs data/keyword_clusters.json. Use when the user says
  "map clusters", "build keyword clusters", "cluster keywords", "find pillar
  gaps", or before any large content-ideator batch.
---

# keyword-cluster-mapper

## Purpose

Group keyword targets into **pillar + spoke** clusters so posts form an internal-link network. This is the foundation for:

1. **Prioritizing what to write next** — pillar gaps surface first
2. **Internal-link enforcement** — every spoke must link to its pillar; every pillar must link to its spokes
3. **Auto-link-back-on-publish** — a future skill consumes this map for link-target candidates

Without clusters, content-ideator generates ideas in isolation. Posts become topical islands and Google never sees a coherent topical authority signal.

## When to Use

- **Before** any large `content-ideator` batch (so new ideas fill cluster gaps, not duplicate spokes)
- **After** bulk publishing (re-cluster to incorporate new content)
- **Monthly**, on the 1st (alongside `performance-tracker`)
- **On demand** when the user says "map clusters" or "find pillar gaps"

## Inputs

| Source | What |
|---|---|
| Google Sheet "posts" tab | All current ideas with their `Target_Keyword`, status |
| `data/site_content_cache.json` | Published posts with their keywords + slugs |
| `data/keyword_clusters.json` (existing) | Previous clustering — preserve cluster IDs across runs |

## Workflow

### Step 1: Load all keywords

Pull every keyword from:

- `posts` sheet (all statuses) → `{keyword, source: "sheet", row, status, slug: null}`
- `data/site_content_cache.json` → `{keyword, source: "published", slug, post_id, status: "wp_published"}`

Deduplicate (case-insensitive). Result: a flat list of `{keyword, source, slug, status}` entries.

### Step 2: Cluster topically

Group keywords by **semantic topical similarity** — not just word overlap:

- "best dog GPS tracker" + "Fi vs Tractive" + "where to buy Whistle" → same cluster (GPS trackers)
- "homemade dog food recipe" + "kibble vs fresh" + "AAFCO standards" → same cluster (dog food fundamentals)
- "stop dog counter surfing" + "leash reactivity training" → same cluster (behavior modification)

Heuristics:

- **Cluster size:** 4–12 keywords (smaller = thin coverage, larger = unfocused)
- **Single-membership:** a keyword belongs to exactly ONE cluster
- **Category-aligned:** clusters should map roughly to existing site categories (Grooming / Food & Diet / Lifestyle & Gear / Training)
- **Stable IDs:** if a cluster matches one from the previous `keyword_clusters.json`, reuse its `id` (e.g. `gps-trackers`) — don't churn IDs every run

### Step 3: Identify pillar + spokes per cluster

For each cluster, designate one keyword as **pillar** (the broadest, highest-volume hub) and the rest as **spokes** (narrow variants).

Pillar selection rules (apply in order):

1. **Already-published long-form post in the cluster** → that post is the pillar
2. **Otherwise:** the shortest, most generic keyword that captures the cluster's core topic
3. **If no pillar candidate is published yet:** mark the cluster `pillar_gap: true` and tag the chosen pillar's status as `PILLAR_GAP`

Spokes are everything else in the cluster, ordered by buyer-intent score (high → low).

### Step 4: Write `data/keyword_clusters.json`

Output schema:

```json
{
  "last_updated": "2026-04-28T14:30:00Z",
  "total_keywords": 47,
  "total_clusters": 8,
  "pillar_gaps": 3,
  "clusters": [
    {
      "id": "gps-trackers",
      "category": "Lifestyle & Gear",
      "pillar": {
        "keyword": "best GPS tracker for dogs 2026",
        "status": "wp_published",
        "slug": "best-gps-tracker-dogs-2026"
      },
      "spokes": [
        {"keyword": "Fi vs Tractive", "status": "approved", "slug": null},
        {"keyword": "Whistle review", "status": "wp_published", "slug": "whistle-review"}
      ],
      "pillar_gap": false
    },
    {
      "id": "homemade-food-fundamentals",
      "category": "Food & Diet",
      "pillar": {
        "keyword": "homemade dog food guide",
        "status": "PILLAR_GAP",
        "slug": null
      },
      "spokes": [
        {"keyword": "homemade dog food cost analysis", "status": "publish", "slug": null}
      ],
      "pillar_gap": true
    }
  ],
  "unclustered": [
    {"keyword": "weird outlier keyword", "source": "sheet", "row": 42}
  ]
}
```

### Step 5: Telegram pillar-gap report

Send a summary so the user can see what to write next:

```
🗺️ Keyword Clusters Updated

Total keywords: 47 | Clusters: 8 | Pillar gaps: 3

⚠️ Top priorities — write these pillars first:
1. [Food & Diet] "homemade dog food guide" — covers 6 spokes
2. [Training] "dog training fundamentals" — covers 5 spokes
3. [Grooming] "complete dog grooming guide" — covers 4 spokes

Healthy clusters (pillar published, spokes filling):
• [Lifestyle & Gear] gps-trackers (1 pillar + 7 spokes)
• [Food & Diet] kibble-reviews (1 pillar + 5 spokes)

Run `content-ideator` to draft missing pillars first.
```

Use `lib/notifier.send()` to deliver.

## Integration

**`content-ideator`** loads `data/keyword_clusters.json` at Step 0. If pillar gaps exist:
- +3 score bonus for ideas that would fill a pillar gap
- -2 penalty for spoke ideas whose pillar doesn't yet exist (unless the same batch creates the pillar)
- Skip cluster check if file is missing or `last_updated` is older than 30 days — flag the user to run this skill first

**`wp-post-creator`** loads the cluster for the brief being drafted. The post must:
- If it's a **spoke**: link to its pillar in the body and in "Related Reading"
- If it's a **pillar**: link to 2-3 of its highest-priority published spokes
- If the cluster has no pillar yet and this post IS the pillar: tag in `wp_posts_cache.json` so future spokes can find it

**Future `auto-link-back-on-publish`** consumes this map — when a new spoke publishes, candidate inbound-link sources are: (a) the pillar, (b) sibling spokes in the same cluster.

## Rules

- **Re-run triggers:** bulk content-ideator batch, sheet manual edits >5 rows, monthly cadence, or before content-enricher when clusters older than 30 days
- **Preserve published clusters:** never delete a cluster that contains published posts (preserves historical map)
- **Don't force-fit:** keywords that don't belong to any cluster go to `unclustered[]` — flag, don't shoehorn
- **Stable cluster IDs:** when a re-run finds an existing cluster by overlap, reuse its `id` so downstream consumers don't break

## Error Handling

| Error | Recovery |
|---|---|
| Sheet not accessible | Use `data/site_content_cache.json` only; flag stale data in Telegram report |
| No published posts in cache | All clusters are pillar-gap → user prioritizes pillar creation |
| Single-keyword cluster | Merge into closest larger cluster, or flag as standalone-pillar |
| Re-run produces wildly different clusters | Diff against previous `keyword_clusters.json`, surface in Telegram for review |

## Dependencies

- Google Sheet "posts" tab (read access)
- `data/site_content_cache.json` (must be fresh — run `site-analyzer` first if stale)
- `lib/notifier.py` for Telegram delivery
- Anthropic API access (Claude does the topical clustering reasoning when this skill is invoked inside Claude Code)

---

*Last Updated: 2026-04-28*
*Version: 1.0*

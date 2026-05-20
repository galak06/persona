# ADR 0001: Outbound engagement and inbound moderation are separate concepts

- Status: Accepted
- Date: 2026-05-20

## Context

Three scanners exist in `scripts/` with surface-level similarity (all draft -> queue): `fb_scan`, `ig_scan`, and `wp_scan`. An architecture review surfaced the suggestion to unify them under one module. A closer look shows two different concerns hiding behind that surface. FB and IG are outbound discovery — scrape third-party content from platforms we don't own, score it, engage inline (like-as-Page on IG today). WP is inbound moderation — REST-fetch comments on our own site that are already held for moderation, spam-filter, queue for the owner to reply. They differ in source (DOM scraping vs REST), auth (Playwright session vs HTTP Basic), rate-limit posture (FB/IG capped to avoid platform bans; WP uncapped because it's our own data), and queue record shape (post URL + score vs comment ID + author).

## Decision

Unify FB and IG under a new `OutboundEngagement` module with a platform-adapter seam (`OutboundAdapter`). Each adapter owns its session, selectors, like behavior, and pre-filters; the shared pipeline handles scoring, candidate-pool cherry-pick, and queue writes. Keep `wp_scan` separate as a distinct "inbound comment moderation" concept. A future rename (e.g. `inbound_moderation`) is possible but is not load-bearing for this decision.

## Consequences

- Future architecture reviews should NOT re-suggest unifying all three scanners; the split is intentional.
- Shared cross-cutting bits (`Dedup`, `draft_helper`, `comment_queue`) remain shared small modules, used by both outbound and inbound paths.
- `wp_scan` evolves independently — moderation rules, spam heuristics, and reply policy can change without touching OutboundEngagement.
- Adding a new outbound platform (Threads, Bluesky, Reddit discovery) means writing one adapter, not duplicating a 700-line scanner.
- Any future inbound source (Reddit moderation of our subreddit, Discord moderation of our server) goes alongside `wp_scan`, not inside OutboundEngagement.

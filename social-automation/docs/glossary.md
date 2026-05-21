# Architectural Glossary

Vocabulary for the social-automation engine. Brand-agnostic — applies to any brand that uses the engine. Brand-specific terms (voice, niche, identity) live in the per-brand CLAUDE.md.

## Engagement

**OutboundEngagement** — the pipeline that discovers third-party posts on platforms we don't own (FB groups, IG hashtags), scores them, optionally engages inline (like-as-Page), and cherry-picks the best-N candidates per day for the comment queue. Distinct from inbound moderation.

**Inbound moderation** — processing pending comments on owned properties (currently `scripts/wp_scan.py`, which reviews WordPress comments held for moderation). Not part of OutboundEngagement.

**OutboundAdapter** — the platform-specific seam inside OutboundEngagement. Today: `FacebookGroupAdapter`, `InstagramHashtagAdapter`. Each adapter owns its session/auth, DOM selectors, like action, and platform-specific pre-filters.

**EngagementPolicy** — single object holding all thresholds (candidate, approval) and daily quotas (comment, like) for OutboundEngagement. Loaded once from `config.json`. The one place behavior is tuned in production.

**Candidate pool** — posts that cleared the score gate during a scan, awaiting cherry-pick for the daily comment quota.

**Cherry-pick queueing** — at end of scan, sort candidates by score descending, take top-N where N = remaining daily comment budget. Applied uniformly to FB+IG by `run_outbound_scan` (since slice 3, 2026-05-21). Quota source: `EngagementPolicy.daily_comment_quota`.

# ADR 0002: OutboundEngagement cherry-picks top-N on both FB and IG

- Status: Accepted
- Date: 2026-05-21

## Context

After ADR 0001 extracted `OutboundAdapter` in slice 2, FB and IG retained divergent queueing strategies: FB queued every candidate that cleared the score threshold inline; IG collected all candidates and cherry-picked the top-2 by score at the end. This divergence was legacy drift, not designed difference. IG's conservative 2/day comment quota (from historically tight rate limits) made cherry-pick essential. FB never adopted it despite looser 5/day quota. Today, cherry-picking is strictly better when daily candidates exceed quota: you queue your best engagement opportunities, not the first N over threshold.

## Decision

Move cherry-pick logic from `scripts/ig_scan.py` into a new platform-agnostic pipeline `lib/engagement/pipeline.py::run_outbound_scan`. Both `scripts/fb_scan.py` and `scripts/ig_scan.py` call the pipeline. FB starts cherry-picking; IG continues via centralized code. Daily budget = `min(EngagementPolicy.daily_comment_quota[platform], quota - already_queued_today)`. Bump `daily_comment_quota["instagram"]` from 2 to 10 (previous cap was conservative; headroom exists under Instagram's rate limits).

## Consequences

- FB lower-scoring posts no longer queue when daily candidates exceed 5. Net: same daily volume, better signal-per-comment.
- IG queues up to 10/day (5x throughput), still under Instagram's Page rate ceiling and brand's published spec (now "8 likes/10 comments per day").
- Single code path for both platforms reduces drift risk in slice 4 (FB Page inline like) and slice 5 (module identity + path resolution).
- `lib/rate_limiter.py::DAILY_LIMITS` still hardcodes IG comments=2; governs comment_poster rate-limit reads, not OutboundEngagement. Slice 5 reconciles.
- All approval gates remain per CLAUDE.md: all IG comments require approval; all WP replies; all URL-containing comments; etc.

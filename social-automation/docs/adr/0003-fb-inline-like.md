# ADR 0003: FB Page-as-actor inline like during OutboundEngagement scans

- Status: Accepted
- Date: 2026-05-21

## Context

Slice 2 introduced `OutboundAdapter.like(post)` as part of the engagement protocol. `InstagramHashtagAdapter.like()` actively clicks the IG heart button; `FacebookGroupAdapter.like()` was left as a placeholder returning `LikeResult.skipped("not_supported")`. Grilling session feedback (via FB reactions-bar screenshot) confirmed that Page-as-actor likes boost post visibility in FB Group algorithms—a desired engagement signal symmetric with IG's inline hearts. The pipeline (slice 3) already gates likes by daily quota config; flipping FB's quota above zero activates this feature.

## Decision

Implement `FacebookGroupAdapter.like(post)` to click the 👍 thumbs-up button via Playwright JS payload `CLICK_LIKE_JS` from `facebook_dom.py`. The session runs as the DogFoodAndFun Page profile, so clicks register as Page likes. Default reaction is thumbs-up only; the popover (love/haha/wow/sad/angry) is not exposed. Implementation avoids hover events to prevent unintended popover triggers. Conservative daily cap: 5 FB Page likes per day, matching the FB comment cap, configurable via `EngagementPolicy.daily_like_quota["facebook"]` and `rate_limiter.DAILY_LIMITS["facebook:like"]`. Detection of `aria-pressed="true"` or label "Liked" / "Remove Like" triggers `LikeResult.skipped("already_liked")` for idempotency. English-only aria-labels suffice for USA+CA target market.

## Consequences

- **Algorithm boost:** FB Group posts scoring above the candidate threshold now receive a Page like during scans—visible engagement signal expected to modestly boost scanned posts' visibility within their groups.

- **Conservative initial cap:** 5/day allows safe ramp-up; once data confirms FB's tolerance (no shadow-bans, no throttling), the cap can increase via config-only edit in a future iteration, no new slice required.

- **Selector drift risk:** If FB renames the aria-label or restructures the action bar, `CLICK_LIKE_JS` may silently fail returning `{status: "failed", reason: "button_not_found"}`. Mitigation: structured logs track `likes_attempted` vs `likes_succeeded`. Drift > 10% triggers investigation.

- **Future reactions extensibility:** Supporting love/haha/wow/sad/angry is straightforward as a separate slice—add `like(post, reaction="love")` parameter to Protocol and adapters; current default is locked to 👍.

- **Localization:** If future brands target non-EN markets, the aria-label set in `CLICK_LIKE_JS` must expand. Document as a brand-onboarding checklist item.

- **Unchanged:** `scripts/ig_like.py` remains independent with its own DOM payloads; no impact from this slice.

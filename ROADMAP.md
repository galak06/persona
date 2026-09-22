# Roadmap

Honest status of every part of Persona. The [README](README.md) covers what
works today with one API key; this is everything else.

## Status

| Area | Status | Tracking |
|---|---|---|
| **Studio** — topic → carousel + caption + reel | ✅ Works, zero accounts needed | — |
| **IG engagement** — scan, like, agent-drafted comment | ✅ Production, single pass | — |
| **FB engagement** — single-pass scan + comment | ✅ Shipped | [#46](https://github.com/galak06/persona/issues/46) |
| **WordPress publishing** — draft → publish → social | ⚠️ Built, needs a live `--apply` run | [#45](https://github.com/galak06/persona/issues/45) |
| **Content ideation** — gaps, trends, enrichment | ⚠️ Workers exist, not activated | [#51](https://github.com/galak06/persona/issues/51) |
| **Pinterest** | ⚠️ Blocked on API Standard access | [#47](https://github.com/galak06/persona/issues/47) |
| **TikTok** | ⚠️ Scout only, publishing incomplete | [#48](https://github.com/galak06/persona/issues/48) |
| **Monitoring / alerting** | ❌ None | [#53](https://github.com/galak06/persona/issues/53) |
| **Frontend build** | ⚠️ `tsc` errors from stale generated types | [#49](https://github.com/galak06/persona/issues/49) |

CI status is on the badge in the README — it gates an explicit file list,
not the whole tree, so a green build means *those* files are clean.

## Known issues

- **Stale model pins.** Ten modules under `app/recipe-publisher/` still
  request `gemini-2.5-flash`, which Google has retired for new API keys.
  Those paths 404 until repinned. The live engager and the studio already
  use current models.
- **Meta tokens in query strings.** 31 call sites across the Facebook and
  Instagram publishers pass `access_token` as a URL parameter, which httpx
  writes to the request log. They should move to an `Authorization` header.
  `tests/test_no_url_credentials.py` pins the affected files so the set
  cannot grow while the fix is pending. LLM API keys are already header-only
  and enforced at zero.
- **Brand-specific strings in comments.** Around 140 references to the
  original brand remain in docstrings and comments across `app/lib/` and
  `app/api/`. They don't affect behaviour — the code reads its identity
  from config — but they're noise for a new reader.
- **macOS-only font defaults.** `generators/text_overlay.py` defaults to
  macOS system font paths. The studio resolves fonts portably before
  importing it; other callers may not.

## Planned

**Next** — lower the cost of the first real run:

- Split the observability stack out of the default `docker compose up`
  (`docker-compose.observability.yml` ships now; making it the default
  path is next)
- Make Postgres optional for single-brand use — SQLite is enough
- Lazy credential validation: fail at the moment a credential is needed,
  with a message naming the exact variable

**Later:**

- Article URL as input, not just a topic
- More slide layouts than the current four-slide shape
- Publish targets for the studio's output (right now it writes files and
  stops)

## Non-goals

- **Growth-hacking scale.** Rate limits are deliberately conservative.
- **Evading platform detection.** Not a feature, won't be accepted as a PR.
- **A hosted service.** Persona is self-hosted; there's no SaaS planned.

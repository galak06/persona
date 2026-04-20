# IG Caption Rules (used inline by the recipe tool schema)

> These rules are enforced in three places: (a) the `ig_caption` tool-schema description in `recipe_from_seed.py`, (b) the `### ig_caption` section of `recipe_system.md`, (c) the validator in `generators/recipe.py::_validate`. This file is the human-readable source of truth; keep them in sync.

## Why the structure matters

IG truncates at ~125 chars in feed and shows the rest only on tap. A caption that wastes those 125 chars earns zero follows and zero site visits. Every section below exists to drive one of two things: a **new follow** or a **site click**.

## Structure (in order — do not reorder)

1. **Hook** — first 125 chars. Feed-truncation-safe. One sentence that creates a curiosity gap or a specific promise. No hashtag, no emoji, no "POV:".
2. **Three bullet facts** — three lines, each starting with `•`. Concrete wins (time, macros, ingredient count, or behavior). Not opinions. Example:
   - `• 25 min total, 6 pantry ingredients`
   - `• xylitol-free peanut butter only — no substitutions`
   - `• Nalla's training currency for two weeks straight`
3. **Comment-gated CTA** — one line that earns the comment signal IG rewards. Exactly one of:
   - `Comment RECIPE and I'll DM you the link to the printable card.`
   - `Comment BAKE and I'll send you the grams + oven temp.`
   Pick the verb that fits the recipe; always uppercase the keyword.
4. **Question** — one specific, answerable question. Never "what do you think?" or "thoughts?". Ask about substitutions, a dog's reaction, a memory.
5. **Blank line, then hashtag block** — 8–12 tags, mixing broad / niche / branded. Must include `#nallasdad` and `#dogfoodandfun`.

## Hashtag mix (rough ratio)

- **Broad:** `#doglife` `#dogsofinstagram` `#dogsofinsta` `#puppylove`
- **Niche:** `#dogrecipes` `#homemadedogtreats` `#dogfood` `#doghealth` `#trainingtreats`
- **Branded (required):** `#nallasdad` `#dogfoodandfun`

Never exceed 12. Don't repeat hashtag sets verbatim across consecutive posts — rotate the broad + niche picks.

## Do not

- Do not start the caption with a hashtag, an emoji, or "POV:"
- Do not shill a product; this account links to full recipes, not affiliates
- Do not claim health outcomes. "Great for joints" → forbidden. "A chewy snack Nalla worked hard for" → fine
- Do not replace the comment-gated CTA with a bare "link in bio" — the comment keyword is what makes the CTA work

## Example (full caption)

```
Liver treats are the only training currency Nalla takes seriously — and once you bake your own, you'll never go back.

• 30 min total, 3 ingredients
• Bakes low and slow so they stay chewy, not crunchy
• Freezer-friendly for up to 6 months

Comment RECIPE and I'll DM you the link to the printable card.

What's the one treat your dog would sell you out for?

#doglife #dogrecipes #trainingtreats #homemadedogtreats #dogsofinstagram #puppylove #dogfood #nallasdad #dogfoodandfun
```

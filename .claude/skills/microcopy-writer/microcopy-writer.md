---
name: microcopy-writer
description: >
  Generate UX microcopy (CTAs, labels, help text, confirmations) for {{brand.domain}}.
  Produces 3 variations per element (Direct, Value-Driven, Brand Voice) following
  cognitive psychology + accessibility (WCAG 2.2, 6th-grade reading level).
  Use when: "write copy for", "CTA variations", "button text", "help text", 
  "form labels", "error messages", "microcopy", "what should this button say".
---

# Microcopy Writer — {{brand.name}}

Generate UX microcopy for forms, CTAs, help text, and confirmations. Each element
receives 3 variations: Direct (clarity-first), Value-Driven (benefit-focused), and
Brand Voice (warm dog-parent). All copy follows WCAG 2.2 accessibility guidelines
and 6th-grade reading level standards.

---

## Role & Expertise

Expert UX Writer and Microcopy Specialist with deep knowledge of:
- **Cognitive Psychology** — reducing decision friction, minimizing cognitive load
- **Conversion Rate Optimization (CRO)** — action-forward language, benefit-driven framing
- **WCAG 2.2 Accessibility** — inclusive language, no visual-only instructions, semantic clarity
- **{{brand.name}} Brand Voice** — warm, caring dog-parent tone; emotionally invested in pet health

---

## Core Principles

### 1. Clear Over Clever
- Prioritize clarity and usability over brand cleverness
- No jargon, metaphors, or passive voice
- Every user should understand the action without secondary research
- Example: "Save recipe" not "Bookmark your culinary treasure"

### 2. Contextual Tone
- Match copy to the user's exact emotional state at this point in their journey
- First-time user = reassuring tone
- Power user = efficient, direct tone
- Error state = supportive, solution-focused tone

### 3. Action-Oriented
- Strong, front-loaded verbs for interactive elements
- Active voice only: "Save recipe" not "Recipe can be saved"
- Command form for buttons (imperative): "Add to cart" not "Click to add"
- Exception: Help text can be more conversational

### 4. Inclusive & Accessible
- Write at 6th-grade reading level (8–12 years old)
- No instructions that rely solely on visual cues ("Click the green button")
- No gendered language
- Short sentences (avg 10–12 words)
- Define acronyms on first use
- High color contrast for readability (not style-only)

---

## {{brand.name}} Brand Context

- **Domain**: {{brand.domain}}
- **Audience**: North American dog owners (USA + Canada), non-technical, emotionally invested in pet health
- **Voice**: Warm, caring dog-parent; friendly but trustworthy
- **Mascot**: {{brand.mascot}} the dog
- **Reading Level**: 6th grade (8–12 years old)
- **Character Limits**: Soft max 40 chars per CTA (buttons, action labels)

### Voice Characteristics
- Conversational but authoritative
- Personal experience grounded ("We tried this with {{brand.mascot}}...")
- No corporate jargon or marketing fluff
- Specific, not generic (avoid "improve" — say "reduce itching")

---

## Output Format

For each UI element, produce **3 variations** as a structured table.

### Variation Structure

**Option A — Direct & Functional**
- Copy: [action-forward, minimal, clarity-first]
- Character Count: [exact count]
- Reasoning: [1 sentence — why this works for clarity]

**Option B — Benefit/Value-Driven**
- Copy: [what the user gains, outcome-focused]
- Character Count: [exact count]
- Reasoning: [1 sentence — what value is communicated]

**Option C — Brand/Conversational**
- Copy: [warm dog-parent voice, personal touch]
- Character Count: [exact count]
- Reasoning: [1 sentence — emotional tone and connection]

### Presentation Template

```
## [Page/Location]: [Element Name]

**Use case:** [When/where user sees this]
**Emotional state:** [User's mood/context at this point]
**Character limit:** [if specified]

| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Save recipe | 11 | Imperative verb, immediate clarity. |
| B (Benefit) | Save to your cookbook | 19 | Shows what user gains (personal collection). |
| C (Brand) | Save {{brand.mascot}}'s favorite | 27 | Personalizes to mascot, builds emotional connection. |

**Recommendation:** [Which variation to use and why]
**Accessibility notes:** [Readability level, contrast, WCAG compliance]
```

---

## Workflow

### Step 1: Clarify the UI Element(s)

If not provided, ask the user:
- **What page or location?** (e.g., recipe post, checkout form, confirmation screen)
- **What's the element?** (e.g., primary CTA, secondary button, form label, error message)
- **Is it a single element or multiple?** (If multiple, group by page for organized output)

Store for reference:
```json
{
  "page": "Recipe Post",
  "elements": [
    {"name": "Save Recipe", "type": "primary_button"},
    {"name": "Print Recipe", "type": "secondary_button"}
  ]
}
```

### Step 2: Determine User Emotional State

Ask (if not specified):
- **Emotional journey at this point?**
  - First interaction = cautious, exploring
  - Mid-funnel = engaged, hopeful
  - Conversion moment = ready, trusting
  - Error state = frustrated, needing reassurance
  - Post-purchase = satisfied, wanting to succeed

Example responses:
- "User just found a recipe they love — excited, trusting the ingredient list"
- "User is in checkout — confident but wants reassurance"
- "User hit an error — frustrated, needs helpful guidance"

### Step 3: Identify Character/Word Limits

Ask (if not specified):
- **Any character limit?** (button width, mobile constraints)
- **Word limit?** (label length, truncation concerns)
- **Special constraints?** (abbreviations OK? emojis? icons nearby?)

Default limits (soft):
- Button/CTA text: 40 chars (can go longer, readability suffers)
- Form labels: 25 chars (shorter = better UX)
- Help text: 150 chars (single paragraph, conversational)
- Error messages: 100 chars + actionable recovery (never blame user)

### Step 4: Generate 3 Variations

For each element, using the framework below:

#### Option A: Direct & Functional
**Goal:** Clarity-first, zero cognitive load. User understands action immediately.

Rules:
- Start with strong action verb (imperative): Save, Add, Download, Print, Submit
- No articles (a/the), no extra words
- 6th-grade reading level
- Active voice only
- Example: "Save recipe" (3 words, 11 chars)

**When to recommend A:**
- Power users who return regularly
- High-traffic pages (quick decisions)
- Mobile or space-constrained UI
- Form submits or confirmations

#### Option B: Benefit/Value-Driven
**Goal:** Show what the user gains, outcome-focused. Shifts from action to benefit.

Rules:
- Lead with outcome, not action: "[Benefit] by [action]" or "[Result] when [action]"
- Specific benefits, not generic ones
- 6th-grade reading level
- Active voice preferred but not rigid
- Examples:
  - "Save to your cookbook" (clarifies ownership + collection)
  - "Get healthy recipes" (outcome-focused)
  - "Share with your vet" (shows use case)

**When to recommend B:**
- New or uncertain users (builds confidence)
- Premium/advanced features (shows value)
- Mid-funnel engagement (convert interest to action)
- Exploratory flows

#### Option C: Brand/Conversational
**Goal:** Warm dog-parent voice, personal and trustworthy. Builds emotional connection.

Rules:
- Reference {{brand.mascot}} or personal experience when appropriate
- Conversational phrasing ("Let's save this" instead of "Save")
- Specific, grounded language (avoid "improve" — say "reduce {{brand.mascot}}'s itching")
- 6th-grade reading level (still accessible)
- Personality without cuteness
- Examples:
  - "Save {{brand.mascot}}'s favorite" (personalizes to dog)
  - "Let's make this recipe" (collaborative tone)
  - "Pin this for later" (casual friendliness)

**When to recommend C:**
- First-time users (builds trust and brand affinity)
- Emotional high-stakes moments (purchases, health decisions)
- Onboarding flows
- Community or personalized features
- Error messages (reassurance needed)

### Step 5: Validate Against WCAG 2.2 + Accessibility Standards

For each variation, check:

**Reading Level (Flesch-Kincaid 6th grade or lower)**
- Sentence length: avg 10–12 words (max 15)
- Word length: avg 5–6 characters
- Avoid: words with 3+ syllables unless necessary (e.g., "ingredient" is OK, "revolutionary" is not)

**Color Contrast & Visual Hierarchy**
- Don't rely on color alone to communicate action (e.g., "Click the green button" fails — use "Click Save")
- Distinguish primary from secondary buttons through text weight, size, position — not just color
- Text on button should have 4.5:1 contrast ratio (WCAG AA standard)

**Semantic Clarity**
- No abbreviations unless universally understood (FAQ OK, AAFCO needs definition on first use)
- No acronyms in buttons or labels (explain in help text)
- No instructions that rely solely on visual cues

**Inclusive Language**
- No gendered pronouns or assumptions
- No ableist language ("simply", "obviously", "just")
- No cultural assumptions ({{brand.name}} is USA + Canada, so avoid region-specific references)

**Mobile-Friendly**
- Buttons: min 44x44px tap target (text is readable inside)
- Text: no truncation on small screens (if it must truncate, add aria-label with full text)
- Labels: placed above input, not inline (accessibility + scannability)

### Step 6: Present Output

Format as a markdown table grouped by page/location:

```
## [Page Name]

### [Element Group]

**Context:** [Emotional state + Use case]
**Character limit:** [if specified]

| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| **A (Direct)** | Save recipe | 11 | Imperative verb + clarity. No ambiguity. |
| **B (Benefit)** | Save to your cookbook | 19 | Shows personal value (builds collection for later). |
| **C (Brand)** | Save {{brand.mascot}}'s favorite | 27 | Personalizes to {{brand.mascot}}, emotional connection. |

**Recommendation:** Use **B** for first-time visitors (builds understanding); **A** for power users/mobile.
**Accessibility:** ✓ 6th-grade reading level | ✓ No color-only instructions | ✓ Active voice

---
```

### Step 7: Provide Justification & Recommendations

For each element, add:
- **Recommended variation** + reasoning (which fits best for this use case)
- **Fallback option** if context changes (e.g., A if space is ultra-tight, C if onboarding)
- **Accessibility compliance** — confirm WCAG 2.2 (reading level, contrast, semantic clarity)
- **Testing suggestion** (if applicable) — which variation to A/B test first

---

## Common Microcopy Scenarios

### Scenario 1: Primary CTA (High Priority Action)

**Example:** "Save recipe" button on recipe post

**User emotional state:** Just found a recipe they love; excited but cautious
**Constraint:** Button max width = 40 chars

**Output:**
| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Save recipe | 11 | Imperative, immediate clarity. |
| B (Benefit) | Save to cookbook | 14 | Shows personal value (collection builds). |
| C (Brand) | Save {{brand.mascot}}'s favorite | 27 | Personalizes to mascot, builds emotional bond. |

**Recommendation:** **B** for first-time users (clarity + benefit combo); **A** for power users (speed).
**Accessibility:** ✓ All under 40 chars | ✓ 6th-grade reading | ✓ Action verb (no passivity)

---

### Scenario 2: Secondary Button (Optional Action)

**Example:** "Print recipe" link on recipe post

**User emotional state:** Engaged with recipe, deciding next step
**Constraint:** Mobile friendly (must read on small screens)

**Output:**
| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Print | 5 | Ultra-minimal; assumes user knows why they'd print. |
| B (Benefit) | Print recipe | 12 | Clarifies what gets printed (whole recipe, not just title). |
| C (Brand) | Print & bring to the kitchen | 24 | Implies real-world use, warm tone. |

**Recommendation:** **B** for general UX (clarity without being verbose); **C** for onboarding.
**Accessibility:** ✓ All readable on mobile | ✓ No abbreviations | ✓ Active voice

---

### Scenario 3: Form Label (Input Field)

**Example:** "Your dog's diet type" label on dietary preferences form

**User emotional state:** Setting up profile; thoughtful, wants accuracy
**Constraint:** Label max 25 chars for layout

**Output:**
| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Diet type | 9 | Minimal, clear. Users know what's asked. |
| B (Benefit) | What does {{brand.mascot}} eat? | 30 | Opens conversation, makes personalization clear. |
| C (Brand) | Tell us what {{brand.mascot}} loves | 29 | Warm, assumes {{brand.mascot}} has preferences. |

**Recommendation:** **A** if form is utilitarian/medical; **B** or **C** if onboarding/personalization.
**Accessibility:** ✓ Label placed above input | ✓ No required asterisk alone (pair with text) | ✓ Semantic clarity

---

### Scenario 4: Help Text (Inline Assistance)

**Example:** Help text below "Estimated delivery" field in cart

**User emotional state:** Checkout moment; wants reassurance
**Constraint:** Single paragraph, 100–150 chars

**Output:**
| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Standard shipping: 5–7 days. Express: 2–3 days. | 44 | Facts only. User can decide based on data. |
| B (Benefit) | Choose express shipping if {{brand.mascot}} is out of food. | 57 | Shows when to upgrade (use case driven). |
| C (Brand) | We ship fast — usually in 2–3 days. You'll have it soon. | 55 | Warm reassurance; eliminates anxiety. |

**Recommendation:** **C** for first-time shoppers (builds trust); **A** for returning users (speed).
**Accessibility:** ✓ Placed directly below field | ✓ Not color-only | ✓ Readable on mobile

---

### Scenario 5: Error Message (Recovery-Focused)

**Example:** "Email not recognized" error on login form

**User emotional state:** Frustrated, wants quick help
**Constraint:** Single line, max 100 chars (must fit on mobile)

**Output:**
| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Email not found. Sign up or try another email. | 46 | Fact + two paths forward. No blame. |
| B (Benefit) | Let's find your account. Sign up or try again. | 46 | Shifts from problem to solution (empowering). |
| C (Brand) | Oops! We didn't find that email. Want to sign up instead? | 57 | Warm acknowledgment; guides without blame. |

**Recommendation:** **C** for consumer apps (humanity builds trust); **A** for enterprise (clarity first).
**Accessibility:** ✓ Error color + icon + text (not color-only) | ✓ Actionable recovery steps | ✓ No blame language

---

### Scenario 6: Confirmation/Success Message (Positive Reinforcement)

**Example:** Confirmation after saving a recipe

**User emotional state:** Satisfied, reassured
**Constraint:** Toast notification (2–3 sec visibility)

**Output:**
| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Recipe saved | 12 | Immediate acknowledgment. Fast-moving visual. |
| B (Benefit) | Recipe added to your cookbook | 29 | Reinforces collection/ownership. |
| C (Brand) | {{brand.mascot}} will love this! Saved to your favorites. | 51 | Celebratory tone; builds emotional bond. |

**Recommendation:** **B** for notification toasts (balance clarity + reassurance); **C** for full-page confirmations (celebrate the moment).
**Accessibility:** ✓ Auto-disappearing (with skip link) | ✓ Announced to screen readers (aria-live) | ✓ Readable before fade

---

## Invocation Patterns

Users will invoke this skill with patterns like:
- **"Write copy for [element]"** → Get 3 variations for one element
- **"Give me CTA variations for [page]"** → Multiple elements on same page
- **"What should this button say?"** → Single element, immediate context provided
- **"Button text for confirming deletion"** → Scenario-specific (error handling, legal language)
- **"Help text for [form field]"** → Assistance language
- **"Error message when [user action fails]"** → Recovery-focused copy

When invoked without full context, follow the **Clarify → Determine State → Identify Limits → Generate → Validate → Present** workflow above.

---

## Tips for High-Quality Microcopy

1. **Read it aloud.** Conversational copy should sound natural when spoken. If you stumble, rewrite.

2. **Test with {{brand.mascot}} in mind.** Replace generic "it" with {{brand.mascot}}'s name. Does it feel personal?

3. **Use active voice everywhere.** Exception: help text can be slightly more conversational ("can be saved" is OK if tone is warm and brief).

4. **Specific beats generic.** "Reduce {{brand.mascot}}'s itching" beats "improve health". Specificity builds trust.

5. **Front-load the verb.** "Save recipe" works faster than "Recipe can be saved". Users scan buttons; they don't read.

6. **Avoid "please" and "thank you" in buttons.** They add formality without function. Use them in help text and confirmations (user-facing context matters).

7. **Test with 6th-graders (or Flesch-Kincaid tool).** If you can't read it aloud to a smart 11-year-old, rewrite.

8. **No acronyms or jargon.** Unless the user wrote it in their domain (e.g., vet writing "AAFCO" on a professional form), define it inline or assume they don't know it.

9. **Emotional precision.** Match tone exactly to emotional state. Excited = upbeat language. Frustrated = calm, solution-focused. Cautious = reassuring, specific.

10. **Watch character counts.** Mobile buttons truncate. Better to stay under 40 chars for buttons; form labels under 25. Test on real devices.

---

## Accessibility Checklist

Before finalizing any variation:

- [ ] **Reading level:** Flesch-Kincaid 6th grade or lower
- [ ] **Sentence length:** Avg 10–12 words (no sentence >15 words)
- [ ] **Word length:** Avg 5–6 characters (minimize 3+ syllable words)
- [ ] **Active voice:** Used unless specifically conversational context
- [ ] **No color-only instructions:** Text + icon/position, not "Click the green button"
- [ ] **No acronyms:** Spelled out on first use or avoided
- [ ] **No ableist language:** "Simply", "obviously", "just" removed
- [ ] **Semantic clarity:** Label placement (above field, not inline); error text paired with color + icon
- [ ] **Mobile-friendly:** Text readable on small screens; buttons min 44x44px tap target
- [ ] **Inclusive language:** No gendered assumptions; considers all users
- [ ] **WCAG 2.2 AA compliant:** 4.5:1 text contrast ratio met

---

## Dependencies

### Data Files
- {{brand.domain}} style guide or brand voice document (if available in `data/brand_voice.md`)
- {{brand.mascot}} personality notes (if available in `data/config/nalla_facts.md`)
- User-provided page mockups or Figma links (for context)

### External Tools
- Flesch-Kincaid reading level calculator (can be mental; ~6-letter-avg-word is 6th grade)
- Chrome DevTools (mobile view, contrast ratio checker)
- WCAG 2.2 checker or Lighthouse (for accessibility audit)

### Output Artifacts
- Markdown table (CLI-friendly, shareable)
- Optional: JSON export of variations for CMS/design tool import

---

## Error Handling

| Issue | Recovery |
|-------|----------|
| User doesn't specify emotional state | Ask: "What's the user feeling at this point?" (cautious, excited, frustrated, confident) |
| No character limits provided | Use soft defaults (CTA: 40 chars, label: 25 chars, help text: 150 chars) |
| Button text conflicts with existing UI | Ask for context (what does the "Submit" button already say?) and differentiate |
| Multiple pages/elements requested | Group by page in output; generate all variations in one pass |
| Reading level too high in generated copy | Rewrite: break sentences, swap long words, use active voice |
| Unclear emotional state | Offer default (first-time user = cautious; power user = efficient) and generate for both |

---

## Example Session: Full Microcopy Generation

**User Input:** "I need copy for the recipe post buttons. First-time user, excited about the recipe they found."

**Agent Steps:**

1. **Clarify:** "I see you want copy for recipe post buttons. Are you looking for:"
   - Save/bookmark button?
   - Print button?
   - Share button?
   - All of the above?"

2. **User:** "All three. Mobile layout."

3. **Determine state:** First-time user + excited = reassuring, clear, benefit-focused language.

4. **Identify limits:** Mobile layout = buttons must fit at 40 chars max.

5. **Generate:** (See output below)

**Output:**

```
## Recipe Post Buttons

### Save Button
**Context:** First-time user, excited about recipe, discovering personalization features
**Character limit:** 40 (mobile)

| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Save recipe | 11 | Imperative clarity. User knows exactly what happens. |
| B (Benefit) | Save to cookbook | 14 | Shows personal value (own collection building). |
| C (Brand) | Save {{brand.mascot}}'s favorite | 27 | Personalizes to {{brand.mascot}}, builds emotional bond. |

**Recommendation:** **B** for first-time discovery flow (clarity + benefit).

---

### Print Button
**Context:** Mid-recipe engagement, user wants offline access
**Character limit:** 40 (mobile)

| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Print | 5 | Ultra-minimal. Assumes user knows why. |
| B (Benefit) | Print recipe | 12 | Clarifies scope (whole recipe, not just title). |
| C (Brand) | Print for the kitchen | 19 | Suggests real-world use case (warm + practical). |

**Recommendation:** **B** for clarity without verbosity.

---

### Share Button
**Context:** User excited to share, discovering social connection
**Character limit:** 40 (mobile)

| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| A (Direct) | Share | 5 | Minimal. User knows they're sharing the recipe. |
| B (Benefit) | Share with friends | 17 | Shows social outcome (not just action). |
| C (Brand) | Share {{brand.mascot}}'s discover | 26 | Personalizes sharing (positions user as curator). |

**Recommendation:** **B** for first-time users (social motivation); **C** for second-time+ (brand building).

---

### Accessibility Summary
✓ All buttons under 40 chars
✓ 6th-grade reading level (no complex words)
✓ Action verbs (Save, Print, Share) — imperative form
✓ Mobile-friendly (short, scannable text)
✓ No color-only instructions (buttons labeled, not colored)
```

**User response:** "I love B for all three. But for share, let me A/B test with a phrasing that mentions the dog more."

**Agent:** "Got it. Here's a share variation that emphasizes {{brand.mascot}}:"

| Variation | Copy | Chars | Reasoning |
|-----------|------|-------|-----------|
| C (Brand) | Share {{brand.mascot}}'s fave | 22 | Shorter, casual, emphasizes personalization. |

**User:** "Perfect, that's the one."

---

*Last Updated: 2026-06-15*
*Version: 1.0*

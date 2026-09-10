# Plan: Cookbook planner features 1–5

**Goal:** Implement five planner upgrades on the cookbook stack (data in `mapzimus/cookbook`, UI in `ui/index.html` → deploy to `maxwellhowegis/cookbook/index.html`).

**Out of scope for this plan:** private/encrypted plan storage (#6 from the earlier list).

**Working style:** keep the app a single vanilla JS HTML file + JSON in this repo. Prefer small sequential PRs that each leave the app usable.

---

## Current constraints (read these first)

| Area | Today’s behavior | Why it matters |
| --- | --- | --- |
| Shopping + kitchen totals | `allSlugs().filter(unique)` then load each recipe once | Repeating Mon/Thu/Sat Stroganoff counts ingredients and cook time **once** |
| Meal kinds | `dinner`, `breakfast`, `lunch`, `dessert` | No way to mark “eat leftovers” without shopping again |
| Destructive actions | Clear / Suggest run immediately | Easy to wipe a shared household plan |
| Staples | Hard-coded `pantry: true` rows in the price table | Not personal; can’t say “we always have soy sauce” |
| Cooking | Recipe view is normal reading layout | Phone goes to sleep; no step checkoffs |

**Primary files**

- `ui/index.html` — almost all feature work
- `plan.json` — schema extensions (kinds, maybe pantry prefs)
- `save_recipe.py` — only if plan scrub / kind validation must understand new kinds
- `tests/` — extend for any Python-side validation; add a small headless JS smoke harness if we grow UI logic further

---

## Recommended order

```
1 Count repeats  →  2 Leftovers kind  →  3 Confirm dialogs  →  4 Pantry profile  →  5 Cooking-mode PWA
```

Rationale:

1. Fixes wrong math people already see; tiny, localized change; unblocks accurate leftovers shopping.
2. Builds on the new “planned occurrence” model from (1).
3. Independent UX safety; can ship anytime after (1) but cheaper once the week view is still simple.
4. Needs a shared prefs shape in `plan.json` (or sibling file); do after leftovers so meal model is stable.
5. Largest UX surface (manifest, CSS, wake lock, hash route); ship last so it doesn’t block the planner math fixes.

---

## Feature 1 — Count repeats (shopping qty + kitchen time)

### Problem
In `showWeek()` the code does roughly:

```js
Promise.all(allSlugs().filter(unique).map(loadRecipe))
// then sums timeInfo / recipeCost once per unique slug
// groupIngredients(full, uniqueSlugs)
```

If Stroganoff is planned 3 nights, onions and cook time should reflect **3×**, not 1×.

### Design

**Occurrence list, not unique list**

- Keep `allSlugs()` as the ordered list of every planned meal slug (including duplicates, excluding leftovers from Feature 2 once that exists).
- Load unique recipes into a map `slug → recipe` (still one network fetch each).
- For totals and shopping, iterate **occurrences**:

```text
occurrences = allSlugs()                    // ["beef-…", "pasta", "beef-…", ...]
bySlug = map of loaded recipes
for each slug in occurrences:
  r = bySlug[slug]
  prepTotal += timeInfo(r).prep
  cookTotal += timeInfo(r).cook
groupIngredientsFromOccurrences(occurrences, bySlug)
```

**`groupIngredients` change**

Today: `recipes.forEach((r, idx) => … slugs[idx])` with unique recipes.

Target: accept either:

- `groupIngredients(occurrences.map(s => bySlug[s]), occurrences)`, or
- a new helper `groupIngredientsN(bySlug, occurrences)` that for each occurrence pushes that recipe’s ingredient lines again.

Merging stays the same (same price-table key + last word). Quantities and prices naturally multiply because lines are pushed N times.

**Per-slot meta line**

Slot chips under each day should still show **one batch** of that recipe (prep/cook/cost for cooking it once), not N×. Only the **week stats** and **shopping list** use occurrence totals.

**Stats copy**

- Groceries: sum over occurrences (or sum of merged shopping list — same if leftovers excluded correctly later).
- Kitchen time: sum over occurrences.
- Optional subtitle: `3× Beef Stroganoff` is nice-to-have, not required for v1.

### Files / steps

1. Refactor the `Promise.all(allSlugs().filter(unique)…)` block in `showWeek` to load-by-unique, aggregate-by-occurrence.
2. Adjust `groupIngredients` call site (prefer not breaking the function signature if a thin wrapper is clearer).
3. Add a tiny pure-function unit test in JS **or** a documented fixture assertion in a headless smoke script:
   - Plan: same slug on 3 days, one onion each → shopping shows ~3 onions (or `3 × onion` after merge).
   - Kitchen time: 3 × single-recipe total.

### Acceptance

- [ ] Planning the same dinner 3 times triples that recipe’s contribution to grocery total and kitchen time.
- [ ] Shopping list merged lines show multiplied quantities.
- [ ] Each day’s slot meta still shows single-recipe time/cost.
- [ ] Empty / single-occurrence weeks unchanged.

### Risks

- Double-counting if someone later passes already-expanded recipe arrays into old call sites — grep for `groupIngredients` and `filter(unique)` when editing.
- Very large weeks (many extras) stay fine; still only ~7–14 meals.

---

## Feature 2 — Leftovers meal kind

### Problem
There’s no first-class way to say “Tuesday is leftover Monday Stroganoff” without either leaving the slot empty or planning the recipe again (and shopping twice).

### Design

**New kind:** `leftovers` (label: `Leftovers`).

**Meal shape** (extends existing `{ kind, slug }`):

```json
{ "kind": "leftovers", "slug": "beef-stroganoff", "fromDay": 0 }
```

- `slug` — which recipe you’re finishing (required for shopping exclusion + Open link).
- `fromDay` — optional 0–6 index of the day you’re leftover-ing from (for display: “Leftovers · Mon”).

**UI**

- “+ Add meal” chips gain **Leftovers** next to Breakfast / Lunch / Dessert.
- Choosing Leftovers:
  1. Pick source day that already has a non-leftovers meal with a slug, **or**
  2. Pick a recipe slug directly (fallback if the source day isn’t set).
- Row display: kind label `Leftovers`, select (or fixed text) showing recipe name, muted meta `from Mon` when `fromDay` set.
- Dinner stays the only required row; leftovers are extra meals (same as lunch today). Allow leftovers as the dinner row only if we want “Tue dinner = leftovers” — **yes, allow `kind: "leftovers"` to replace dinner** via the dinner `<select>` having a special option group, **or** simpler v1: leftovers only via “+ Add meal” and dinner can be cleared.  

**Recommended v1:** allow leftovers as an **extra meal** and also as **dinner** by adding a “Leftovers…” path in the dinner select (optgroup or secondary control). Simplest path that matches mental model: **dinner select includes recipes; a separate control “Mark as leftovers of …”** sets `kind` to leftovers and keeps slug. Even simpler for v1:

- Add meal chip → Leftovers → choose which earlier meal to reuse → pushes `{ kind:"leftovers", slug, fromDay }`.
- To make Tuesday’s only meal leftovers, clear dinner to empty and add leftovers, **or** set dinner’s kind to leftovers (normalizePlan must allow dinner-or-leftovers as the primary row).

**Normalize plan**

- Extend `KINDS` / `CODE_KIND` (`o` for leftovers in share links: `o:beef-stroganoff` or `o0:beef-stroganoff` with fromDay).
- `normalizePlan`: accept `leftovers`; primary row may be leftovers; still ensure one primary row per day.
- `validSlug` unchanged; leftovers with empty slug are invalid → treat as blank.

**Shopping + time (depends on Feature 1)**

- `allSlugsForShopping()` = occurrences whose `kind !== "leftovers"`.
- `allSlugsForKitchenTime()` = same (you’re not cooking again); slot meta for a leftovers row shows `Leftovers · no cook` / `🛒 $0`.
- Week grocery total ignores leftovers occurrences.

**Suggest a week**

- Never auto-fills leftovers.
- Optional later: if a long cook is suggested Mon, suggest leftovers Tue — out of scope for v1.

**Python scrub**

- `save_recipe.py` `scrub_plan` allowlist adds `leftovers`; preserve `fromDay` when 0–6 int.

### Files / steps

1. Data: update `KINDS`, hash encode/decode, `normalizePlan`, scrub_plan allowlist.
2. UI: add-meal chip + row rendering + meta.
3. Wire shopping/time to skip leftovers (on top of Feature 1 occurrence loop).
4. Fixture: Mon dinner slug A, Tue leftovers A → shopping equals one A; kitchen time equals one A.

### Acceptance

- [ ] Can add Leftovers linked to an earlier meal’s recipe.
- [ ] Leftovers do not add ingredients or cook time.
- [ ] Share link round-trips leftovers (`#week=…`).
- [ ] Deleting a recipe clears leftovers slots that pointed at it (existing scrub path).

### Risks

- fromDay can drift if user reorders/clears Mon after setting Tue leftovers — on render, if `fromDay` meal slug ≠ leftovers.slug, show a muted warning and keep slug as source of truth for shopping.
- Don’t let Suggest overwrite leftovers rows (only empty dinners).

---

## Feature 3 — Confirm before Clear week / Suggest a week

### Problem
Both buttons mutate the shared plan with one click.

### Design

**Clear week**

- If every slot is already empty → no-op (no dialog).
- Else `confirm("Clear all meals this week? The shopping list checkoffs stay; extras stay.")`.
- On OK: existing `normalizePlan([])` + save.

**Suggest a week**

- Count how many empty dinners will be filled: `n = plan.filter(d => !d[0].slug).length` (and not leftovers-primary if Feature 2 landed).
- If `n === 0` → banner or alert “Nothing to fill — every dinner is already set.”
- If `n > 0` → `confirm("Fill " + n + " empty dinner" + (n===1?"":"s") + " with random recipes? Meals you already picked stay put.")`.
- On OK: existing suggest loop.

**No new dependencies** — `window.confirm` matches Delete recipe. Optional upgrade later: in-page `<dialog>` for consistent styling.

### Files / steps

1. Wrap the two click handlers in `showWeek` (~lines with “Suggest a week” / “Clear week”).
2. Manual check: cancel leaves plan unchanged; OK matches old behavior.

### Acceptance

- [ ] Cancel on either dialog leaves `plan.json` / in-memory plan untouched.
- [ ] Suggest still only fills empty dinners.
- [ ] Clear still leaves checked + extras alone (document in confirm copy).

### Risks

- None meaningful; do this PR even alone if Feature 1/2 slip.

---

## Feature 4 — Household pantry profile

### Problem
“Staples you probably already have” is inferred only from the global price table’s `pantry: true` flag. Households differ.

### Design

**Store profile in `plan.json`** (same shared sync as the week — both phones see the same pantry):

```json
{
  "days": [ ... ],
  "checked": {},
  "extras": [],
  "pantry": ["soy sauce", "olive oil", "garlic", "butter"],
  "updatedAt": "..."
}
```

- `pantry`: array of normalized keys (prefer **price-table keys** when matched, else cleaned ingredient name).
- Keep localStorage mirror like other plan fields for locked devices.

**Classification**

When building a shopping row:

1. If override price set → not a free staple (current behavior).
2. Else if ingredient’s `priceKey` or label matches household `pantry` (case-insensitive, match on price key or last-word label) → treat as staple (`kind: "pantry"`, $0), **even if** the global table didn’t mark it pantry.
3. Else if global table says pantry → staple (current).
4. Else → buy list.

**UI**

- On the week view, under shopping list (or a small “Pantry” details block):
  - List current pantry tags as removable chips.
  - Input + Add: “We always have…”
  - On each shopping row, optional affordance: “Always have” adds that row’s `priceKey` to `pantry` (unlocked only).
- Persist via existing `queuePlanSave` / plan doc (extend `readPlanDoc`, `flushPlan` to merge `pantry` with the same delta style **or** replace-whole-array on edit — replace-whole is fine for v1 if edits are debounced with the plan).

**Conflict / merge**

- v1: last writer wins for the whole `pantry` array (same as `days`). Document it.
- Don’t need per-item deltas unless two people edit pantry at once often.

**Python**

- `scrub_plan` / `readPlanDoc` equivalent: preserve `pantry` as list of non-empty strings; cap length (e.g. 100) to avoid abuse.

### Files / steps

1. Extend plan schema + load/save in UI.
2. Hook staple detection in the shopping aggregation path.
3. Pantry editor UI + “Always have” on rows.
4. Seed empty `"pantry": []` in `plan.json` when scrubbing if missing.

### Acceptance

- [ ] Adding “soy sauce” to pantry moves matching shopping lines into staples ($0) on next render.
- [ ] Removing from pantry puts them back on the buy list.
- [ ] Unlocked save persists to `plan.json`; locked device keeps local copy.
- [ ] Global pantry flags still work when household list is empty.

### Risks

- Over-matching (“salt” matching everything) — match on **priceKey** first; only fall back to exact label match, not substring.
- Plan file grows — negligible.

---

## Feature 5 — Cooking-mode PWA

### Problem
Cooking from a phone needs big type, step checkoffs, and the screen staying awake.

### Design

**Route:** `#cook/<slug>` (and keep `#<slug>` as the normal recipe view).

Entry points:

- Recipe view: button **Cook mode**
- Week slot: **Cook →** next to Open when a slug is set

**Cooking UI (full-viewport within `#view`)**

- Large recipe title
- Ingredients collapsed by default (`<details>`), steps prominent
- Each step: checkbox + large text; checked state stored in `localStorage` key `cookbook-cook/<slug>` as `{ checked: { "0-1": true }, updatedAt }` (sectionIndex-stepIndex)
- Progress: “3 / 12 steps”
- Controls: Exit, Reset checks
- Minimal chrome: hide add-recipe form / tabs optional via CSS class `body.cook-mode`

**Wake Lock**

```js
navigator.wakeLock?.request('screen')
```

- Request on entering cook mode; re-request on `visibilitychange` when visible again.
- Release on exit / hash change away from cook.
- If unsupported, show quiet hint: “Keep the screen on in system settings if it sleeps.”

**PWA shell**

- Add `ui/manifest.webmanifest` (name “Cookbook”, `start_url` `/cookbook/`, `display: "standalone"`, theme colors matching light/dark if possible).
- Icons: reuse site favicon / apple-touch if available on maxwellhowegis; or add simple `ui/icon-192.png` / `512`.
- `ui/index.html` head: `<link rel="manifest" href="manifest.webmanifest">`, meta `mobile-web-app-capable`.
- Optional tiny service worker `ui/sw.js`: cache-first for `index.html` + last-viewed recipe JSON from Cache API — **v1 can skip SW** and only do manifest + wake lock + cook UI if time is tight. Recommend **manifest + cook UI + wake lock** as must-have; SW as stretch.

**Deploy note**

- Manifest/SW paths must work on `https://maxwellhowegis.com/cookbook/` (relative URLs).
- Headers: GitHub Pages already HTTPS; no special COOP needed for wake lock.

### Files / steps

1. CSS for `.cook` layout (big type, large tap targets, sticky progress).
2. `showCook(slug)` + routing in `route()`.
3. Wake Lock helper.
4. Manifest (+ icons); link from HTML.
5. Stretch: service worker registering only on `https:` and not on localhost file.

### Acceptance

- [ ] From a recipe, Cook mode shows steps with checkoffs that survive a refresh.
- [ ] Screen wake lock engages when the browser supports it.
- [ ] Exit returns to the normal recipe or week view.
- [ ] “Add to Home Screen” shows Cookbook when manifest is deployed (manual check on iOS/Android).

### Risks

- iOS Safari: wake lock support is newer — always degrade gracefully.
- SW caching stale recipes — if added, version the cache and don’t cache `plan.json` aggressively.

---

## Cross-cutting engineering

### Testing strategy

| Layer | What |
| --- | --- |
| Python | Extend `tests/test_save_recipe.py` for new kinds + pantry field scrubbing |
| UI logic | Prefer extracting pure helpers (`occurrencesForShopping(plan)`, `isStaple(key, pantry)`) into testable functions at the top of the script **or** a tiny `ui/planner.js` if the HTML is getting painful — only split if Feature 1–2 force it |
| Manual / headless | Chromium script against fake GitHub API (pattern already used in the null-fix work): fixtures for 3× repeat, leftovers, confirm cancel |

### PR breakdown (suggested)

| PR | Ships |
| --- | --- |
| A | Feature 1 — count repeats |
| B | Feature 2 — leftovers kind + scrub/link support |
| C | Feature 3 — confirms (can merge with A if tiny) |
| D | Feature 4 — pantry profile |
| E | Feature 5 — cook mode + manifest (+ optional SW) |

Each PR: update `ui/index.html`, sync note in `ui/README.md`, and after merge copy UI to `maxwellhowegis`.

### Deploy checklist (every UI PR)

1. Merge cookbook repo changes (`plan.json` schema / python scrub as needed).
2. Copy `ui/index.html` (+ manifest/icons/sw if any) to `maxwellhowegis/cookbook/`.
3. Smoke on phone: week view, one cook-mode entry, unlock/save once.

---

## Milestone sketch (effort, not calendar)

- **PR A:** localized refactor of week aggregation — small/medium
- **PR B:** schema + UI for leftovers — medium (most product decisions)
- **PR C:** two confirms — small
- **PR D:** pantry array + detection + chips — medium
- **PR E:** cook route + wake lock + manifest — medium; SW stretch

---

## Open decisions (resolve during Feature 2 / 4)

1. **Leftovers as dinner vs extra only?** Recommendation: allow as dinner primary row so Tue can be “just leftovers.”
2. **Pantry in `plan.json` vs `pantry.json`?** Recommendation: `plan.json` for shared household sync with one token/path.
3. **Service worker in v1?** Recommendation: no — cook UI + wake lock + manifest first.

When implementing, start with **PR A (count repeats)** unless product priority shifts to confirms-only for a quick win.

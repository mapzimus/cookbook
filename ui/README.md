# Front-end companion fix

Copy `ui/index.html` → `mapzimus/maxwellhowegis/cookbook/index.html`.

## What was wrong

1. **Empty plan slots were JSON `null`.** Careless stringification (`"" + null`, template text) and raw data views showed the word `null` instead of an empty meal.
2. **Deleting a recipe did not clear it from `plan.json`.** The week kept pointing at missing slugs; the UI blanked them in memory but could still feel “broken.”
3. **`el(..., { text: value })` passed nulls through to `textContent` / attributes without normalizing.**

## What the patched UI does

- Treats empty meal slugs as `""` (and rejects the strings `null` / `undefined` / `none` as slugs)
- Omits JSON `null` from commits via `pretty()`
- Explicitly selects the “nothing planned” option for empty slots
- Scrubs the shared plan when a recipe is deleted
- Persists a one-time scrub when loading a plan that still has nulls or deleted slugs
- Uses safe string helpers so labels never become the word `null`

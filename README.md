# Cookbook data

JSON recipes, the shared weekly plan, and the GitHub Action that saves a recipe from a URL.

The page at [maxwellhowegis.com/cookbook/](https://maxwellhowegis.com/cookbook/) reads and writes this repo.
The page itself lives in [`mapzimus/maxwellhowegis`](https://github.com/mapzimus/maxwellhowegis) at `cookbook/index.html` — that
is the only copy. Edit it there; there is nothing here to keep in sync with it.

## Layout

| Path | Purpose |
| --- | --- |
| `recipes/*.json` | One file per recipe |
| `index.json` | Lightweight list for the recipe grid |
| `plan.json` | Shared week + shopping checkoffs + extras |
| `prices.json` | Optional price overrides |
| `save_recipe.py` | Fetch schema.org Recipe JSON-LD and rebuild the index |

## Weekly plan

Each day is a list of meals. Dinner is always first. **Empty slots use `""`, never JSON `null`**, so the planner cannot surface the literal word `null`.

```json
{
  "days": [
    [{ "kind": "dinner", "slug": "beef-stroganoff" }],
    [{ "kind": "dinner", "slug": "" }]
  ],
  "checked": {},
  "extras": [],
  "pantry": { "have": [], "need": [] },
  "updatedAt": "2026-09-10T00:00:00Z"
}
```

`python3 save_recipe.py --rebuild-index` regenerates `index.json` and scrubs `plan.json` (drops deleted slugs, rewrites nulls to `""`).

## Save a recipe

```bash
RECIPE_URL='https://example.com/recipe' python3 save_recipe.py
# or
python3 save_recipe.py 'https://example.com/recipe'
```

Tests:

```bash
python3 -m unittest tests.test_save_recipe -v
```

Recipes carry a `category` (`meal`, `breakfast`, `appetizer`, `dessert`, `drink`), guessed on save from the
page's own `recipeCategory` and keywords. The planner only randomises dinners out of `meal`.

`pantry.have` is what the household always keeps in (never priced); `pantry.need` is the built-in staples it
does not keep in (priced as normal).

# Cookbook data

JSON recipes, the shared weekly plan, and the GitHub Action that saves a recipe from a URL.

The page at [maxwellhowegis.com/cookbook/](https://maxwellhowegis.com/cookbook/) reads and writes this repo.

## Layout

| Path | Purpose |
| --- | --- |
| `recipes/*.json` | One file per recipe |
| `index.json` | Lightweight list for the recipe grid |
| `plan.json` | Shared week + shopping checkoffs + extras |
| `prices.json` | Optional price overrides |
| `save_recipe.py` | Fetch schema.org Recipe JSON-LD and rebuild the index |
| `ui/index.html` | Hardened copy of the front-end (deploy to the site repo) |

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

## Front-end deploy note

The live UI still lives in `mapzimus/maxwellhowegis` at `cookbook/index.html`. This repo’s `ui/index.html` is the hardened version (empty slots as `""`, no `null` stringification, scrub plan on recipe delete). Copy it over when you next update the site:

```bash
cp ui/index.html ../maxwellhowegis/cookbook/index.html
```

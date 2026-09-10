#!/usr/bin/env python3
"""Save a recipe from a URL: read the schema.org/Recipe JSON-LD most recipe
sites embed, write recipes/<slug>.json, rebuild index.json.

Usage: RECIPE_URL=<url> python3 save_recipe.py   or   python3 save_recipe.py <url>
Standard library only.
"""

import datetime
import gzip
import html
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("COOKBOOK_DIR") or Path(__file__).resolve().parent)
RECIPES_DIR = ROOT / "recipes"
INDEX_FILE = ROOT / "index.json"
PLAN_FILE = ROOT / "plan.json"
INDEX_FIELDS = ("slug", "name", "site", "source", "image", "prepTime", "cookTime", "totalTime", "savedAt")
SLUG_RE = re.compile(r"^[a-z0-9-]+$")
RESERVED_SLUGS = frozenset({"null", "undefined", "none"})

# Look like a normal browser; several sites refuse the default Python agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# ---------------------------------------------------------------- helpers

def find_url(*texts):
    """First http(s) URL found in any of the given strings."""
    for text in texts:
        if not text:
            continue
        m = re.search(r"https?://[^\s<>()\[\]\"']+", text)
        if m:
            return m.group(0).rstrip(".,;:!?")
    return None


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, "replace")


def ld_json_blocks(page):
    """Yield every parseable <script type="application/ld+json"> payload."""
    pattern = r"<script[^>]*type\s*=\s*[\"']?application/ld\+json[\"']?[^>]*>(.*?)</script>"
    for m in re.finditer(pattern, page, re.S | re.I):
        text = m.group(1).strip()
        text = re.sub(r"^<!--|-->$", "", text).strip()
        text = re.sub(r"^//\s*<!\[CDATA\[|//\s*\]\]>$", "", text).strip()
        if not text:
            continue
        try:
            yield json.loads(text, strict=False)
        except json.JSONDecodeError:
            continue


def find_recipes(node):
    """Walk any JSON-LD structure (including @graph) for Recipe objects."""
    if isinstance(node, dict):
        types = node.get("@type")
        if not isinstance(types, list):
            types = [types]
        if "Recipe" in types:
            yield node
        for value in node.values():
            yield from find_recipes(value)
    elif isinstance(node, list):
        for value in node:
            yield from find_recipes(value)


def clean(value):
    """HTML fragment or entity-laden string -> plain single-line text."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = re.sub(r"<br\s*/?>|</p>|</li>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def first_image(image):
    if isinstance(image, str):
        return image.strip() or None
    if isinstance(image, list):
        for item in image:
            url = first_image(item)
            if url:
                return url
    if isinstance(image, dict):
        return first_image(image.get("url") or image.get("contentUrl"))
    return None


def person_names(author):
    if isinstance(author, str):
        return [clean(author)]
    if isinstance(author, dict):
        return [clean(author.get("name"))]
    if isinstance(author, list):
        names = []
        for item in author:
            names.extend(person_names(item))
        return [n for n in names if n]
    return []


def text_list(value):
    """Normalize a string / list-of-strings field to a list of clean strings."""
    if value is None:
        return []
    if isinstance(value, (str, int, float)):
        value = [value]
    out = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("text") or item.get("name")
        text = clean(item)
        if text:
            out.append(text)
    return out


def yield_text(value):
    """recipeYield can be "4", ["12", "12 cookies"], 4, ... Prefer the wordy one."""
    options = text_list(value)
    return max(options, key=len) if options else None


def duration_text(value):
    """ISO-8601 duration (PT1H30M) -> "1 hr 30 min". Non-ISO text passes through."""
    if not value or not isinstance(value, str):
        return None
    m = re.fullmatch(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
        r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?",
        value.strip().upper(),
    )
    if not m:
        return clean(value) or None
    _y, _mo, weeks, days, hours, minutes, seconds = m.groups()
    total = (
        int(weeks or 0) * 7 * 1440
        + int(days or 0) * 1440
        + int(hours or 0) * 60
        + int(minutes or 0)
        + round(float(seconds or 0) / 60)
    )
    if total <= 0:
        return None
    hours, minutes = divmod(total, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hr")
    if minutes:
        parts.append(f"{minutes} min")
    return " ".join(parts)


def parse_instructions(value):
    """recipeInstructions -> [{"name": section-or-omitted, "steps": [str, ...]}].

    Handles a plain string, a list of strings, a list of HowToStep objects,
    and HowToSection groups (each with its own itemListElement of steps).
    Section titles are omitted (not JSON null) when a recipe has no sections.
    """
    sections = []

    def add(section, text):
        text = clean(text)
        if not text:
            return
        section = section or ""
        if not sections or (sections[-1].get("name") or "") != section:
            entry = {"steps": []}
            if section:
                entry["name"] = section
            sections.append(entry)
        sections[-1]["steps"].append(text)

    def walk(node, section):
        if isinstance(node, str):
            for part in re.split(r"\n+", node):
                add(section, part)
        elif isinstance(node, list):
            for item in node:
                walk(item, section)
        elif isinstance(node, dict):
            kind = node.get("@type")
            children = node.get("itemListElement")
            if kind == "HowToSection" or (children and kind != "HowToStep"):
                walk(children or [], clean(node.get("name")) or section)
            elif kind == "HowToStep" and children and not node.get("text"):
                add(section, " ".join(text_list(children)))
            else:
                add(section, node.get("text") or node.get("name") or node.get("description"))

    walk(value, "")
    return sections


def slugify(text, fallback="recipe"):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = text[:80].strip("-") or fallback
    if slug in RESERVED_SLUGS:
        slug = fallback
    return slug


def valid_slug(value):
    """Return a safe recipe slug, or "" if missing/invalid (never the string 'null')."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or value in RESERVED_SLUGS or not SLUG_RE.fullmatch(value):
        return ""
    return value


def unique_slug(base, source):
    """Reuse the slug if it's the same source URL; otherwise avoid clobbering."""
    slug, n = base, 2
    while True:
        path = RECIPES_DIR / f"{slug}.json"
        if not path.exists():
            return slug
        try:
            if json.loads(path.read_text(encoding="utf-8")).get("source") == source:
                return slug
        except (OSError, ValueError):
            pass
        slug = f"{base}-{n}"
        n += 1


def drop_nulls(value):
    """Recursively drop None values so JSON never says null."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value if v is not None]
    return value


def dump_json(data):
    return json.dumps(drop_nulls(data), indent=1, ensure_ascii=False) + "\n"


def normalize(recipe, source):
    name = clean(recipe.get("name")) or clean(recipe.get("headline")) or "Untitled recipe"
    description = clean(recipe.get("description"))
    if len(description) > 300:
        description = description[:297].rstrip() + "…"
    host = urllib.parse.urlsplit(source).hostname or ""
    data = {
        "slug": unique_slug(slugify(name), source),
        "name": name,
        "source": source,
        "site": re.sub(r"^www\.", "", host) or None,
        "author": ", ".join(n for n in person_names(recipe.get("author")) if n) or None,
        "image": first_image(recipe.get("image")),
        "description": description or None,
        "yield": yield_text(recipe.get("recipeYield")),
        "prepTime": duration_text(recipe.get("prepTime")),
        "cookTime": duration_text(recipe.get("cookTime")),
        "totalTime": duration_text(recipe.get("totalTime")),
        "ingredients": text_list(recipe.get("recipeIngredient") or recipe.get("ingredients")),
        "instructions": parse_instructions(recipe.get("recipeInstructions")),
        "savedAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if not data["slug"] or data["slug"] in RESERVED_SLUGS:
        data["slug"] = unique_slug(slugify(name, fallback="recipe"), source)
    return data


def known_slugs():
    slugs = set()
    for path in RECIPES_DIR.glob("*.json"):
        try:
            slug = valid_slug(json.loads(path.read_text(encoding="utf-8")).get("slug") or path.stem)
        except (OSError, ValueError):
            slug = valid_slug(path.stem)
        if slug:
            slugs.add(slug)
    return slugs


def empty_plan_days():
    return [[{"kind": "dinner", "slug": ""}] for _ in range(7)]


def scrub_plan(known=None):
    """Keep plan.json aligned with recipes: drop deleted slugs, never write JSON null.

    Empty meal slots use "" instead of null so the weekly planner never surfaces the
    literal word "null" from raw data or careless stringification in the UI.
    """
    known = known if known is not None else known_slugs()
    if not PLAN_FILE.exists():
        return False
    try:
        doc = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    if not isinstance(doc, dict):
        doc = {}

    raw_days = doc.get("days")
    days = empty_plan_days()
    if isinstance(raw_days, list):
        for i in range(min(7, len(raw_days))):
            day = raw_days[i]
            meals = []
            if isinstance(day, list):
                for meal in day:
                    if not isinstance(meal, dict):
                        continue
                    kind = meal.get("kind") if meal.get("kind") in {"dinner", "breakfast", "lunch", "dessert"} else None
                    if not kind:
                        continue
                    slug = valid_slug(meal.get("slug"))
                    if slug and slug not in known:
                        slug = ""
                    meals.append({"kind": kind, "slug": slug})
            elif isinstance(day, str):
                slug = valid_slug(day)
                meals.append({"kind": "dinner", "slug": slug if slug in known else ""})
            dinner = next((m for m in meals if m["kind"] == "dinner"), {"kind": "dinner", "slug": ""})
            days[i] = [dinner] + [m for m in meals if m["kind"] != "dinner"]

    checked_in = doc.get("checked") if isinstance(doc.get("checked"), dict) else {}
    checked = {str(k): 1 for k, v in checked_in.items() if v}
    extras_in = doc.get("extras") if isinstance(doc.get("extras"), list) else []
    extras = [x for x in extras_in if isinstance(x, dict) and x.get("id") and isinstance(x.get("text"), str) and x["text"].strip()]

    cleaned = {
        "days": days,
        "checked": checked,
        "extras": extras,
        "updatedAt": doc.get("updatedAt")
        if isinstance(doc.get("updatedAt"), str) and doc.get("updatedAt")
        else datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    text = dump_json(cleaned)
    prev = PLAN_FILE.read_text(encoding="utf-8") if PLAN_FILE.exists() else None
    if prev == text:
        return False
    PLAN_FILE.write_text(text, encoding="utf-8")
    return True


def rebuild_index():
    items = []
    for path in sorted(RECIPES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        slug = valid_slug(data.get("slug") or path.stem)
        name = clean(data.get("name")) or slug or path.stem
        if not slug:
            continue
        entry = {k: data.get(k) for k in INDEX_FIELDS}
        entry["slug"] = slug
        entry["name"] = name
        items.append(drop_nulls(entry))
    items.sort(key=lambda item: item.get("savedAt") or "", reverse=True)
    INDEX_FILE.write_text(dump_json(items), encoding="utf-8")
    scrub_plan({item["slug"] for item in items})
    return len(items)


def set_output(**values):
    """Write step outputs for GitHub Actions (or print them when run locally)."""
    path = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{key}={str(value or '').replace(chr(10), ' ')}" for key, value in values.items()]
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))


def fail(message):
    print(f"FAILED: {message}", file=sys.stderr)
    set_output(slug="", name="", source="", error=message)


# ------------------------------------------------------------------- main

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--rebuild-index"] or argv == ["--scrub-plan"]:
        RECIPES_DIR.mkdir(parents=True, exist_ok=True)
        if not INDEX_FILE.exists():
            INDEX_FILE.write_text("[]\n", encoding="utf-8")
        count = rebuild_index()
        print(f"Index has {count} recipes; plan scrubbed.")
        return

    url = find_url(os.environ.get("RECIPE_URL"), " ".join(argv))
    if not url:
        return fail("No recipe URL given.")

    if not url.lower().startswith(("http://", "https://")):
        return fail("Recipe URL must start with http:// or https://.")

    try:
        page = fetch(url)
    except urllib.error.HTTPError as e:
        return fail(f"The site refused the request (HTTP {e.code}). Some sites block automated fetches.")
    except Exception as e:  # noqa: BLE001
        return fail(f"Couldn't fetch the page: {e}")

    recipes = [r for block in ld_json_blocks(page) for r in find_recipes(block)]
    if not recipes:
        return fail("No structured recipe data (schema.org Recipe) found on that page.")

    # If a page has several, keep the one with the most ingredients.
    recipe = max(
        recipes,
        key=lambda r: (
            len(text_list(r.get("recipeIngredient") or r.get("ingredients"))),
            len(parse_instructions(r.get("recipeInstructions"))),
        ),
    )
    data = normalize(recipe, url)
    if not data["ingredients"] and not data["instructions"]:
        return fail("Found a recipe entry on the page, but it had no ingredients or steps.")

    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    out = RECIPES_DIR / f"{data['slug']}.json"
    out.write_text(dump_json(data), encoding="utf-8")
    count = rebuild_index()
    print(f"Saved {out} ({len(data['ingredients'])} ingredients, "
          f"{sum(len(s['steps']) for s in data['instructions'])} steps); index has {count} recipes.")
    set_output(slug=data["slug"], name=data["name"], source=url, error="")


if __name__ == "__main__":
    main()

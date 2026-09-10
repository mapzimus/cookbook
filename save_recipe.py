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
        return names
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
    """recipeInstructions -> [{"name": section-or-None, "steps": [str, ...]}].

    Handles a plain string, a list of strings, a list of HowToStep objects,
    and HowToSection groups (each with its own itemListElement of steps).
    """
    sections = []

    def add(section, text):
        text = clean(text)
        if not text:
            return
        if not sections or sections[-1]["name"] != section:
            sections.append({"name": section, "steps": []})
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

    walk(value, None)
    return sections


def slugify(text, fallback="recipe"):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:80].strip("-") or fallback


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


def normalize(recipe, source):
    name = clean(recipe.get("name")) or clean(recipe.get("headline")) or "Untitled recipe"
    description = clean(recipe.get("description"))
    if len(description) > 300:
        description = description[:297].rstrip() + "…"
    host = urllib.parse.urlsplit(source).hostname or ""
    return {
        "slug": unique_slug(slugify(name), source),
        "name": name,
        "source": source,
        "site": re.sub(r"^www\.", "", host),
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


def rebuild_index():
    items = []
    for path in sorted(RECIPES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items.append({k: data.get(k) for k in ("slug", "name", "site", "source", "image", "prepTime", "cookTime", "totalTime", "savedAt")})
    items.sort(key=lambda item: item.get("savedAt") or "", reverse=True)
    INDEX_FILE.write_text(json.dumps(items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
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

def main():
    url = find_url(os.environ.get("RECIPE_URL"), " ".join(sys.argv[1:]))
    if not url:
        return fail("No recipe URL given.")

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
    recipe = max(recipes, key=lambda r: len(text_list(r.get("recipeIngredient"))))
    data = normalize(recipe, url)
    if not data["ingredients"] and not data["instructions"]:
        return fail("Found a recipe entry on the page, but it had no ingredients or steps.")

    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    out = RECIPES_DIR / f"{data['slug']}.json"
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    count = rebuild_index()
    print(f"Saved {out} ({len(data['ingredients'])} ingredients, "
          f"{sum(len(s['steps']) for s in data['instructions'])} steps); index has {count} recipes.")
    set_output(slug=data["slug"], name=data["name"], source=url, error="")


if __name__ == "__main__":
    main()

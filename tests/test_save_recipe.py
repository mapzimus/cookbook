#!/usr/bin/env python3
"""Tests for cookbook saver + plan scrubbing (stdlib unittest)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import save_recipe as sr


class DropNullsTests(unittest.TestCase):
    def test_omits_none_but_keeps_empty_string(self):
        self.assertEqual(
            sr.drop_nulls({"a": None, "b": "", "c": {"d": None, "e": 1}, "f": [None, "x"]}),
            {"b": "", "c": {"e": 1}, "f": ["x"]},
        )

    def test_dump_json_has_no_null_token(self):
        text = sr.dump_json({"name": "Soup", "author": None, "slug": "soup"})
        self.assertNotIn(": null", text)
        self.assertIn('"name": "Soup"', text)


class SlugTests(unittest.TestCase):
    def test_valid_slug_rejects_nullish_strings(self):
        for bad in (None, 1, "", "null", "undefined", "none", "Beef!", "A B"):
            self.assertEqual(sr.valid_slug(bad), "")
        self.assertEqual(sr.valid_slug("beef-stroganoff"), "beef-stroganoff")

    def test_slugify_avoids_reserved(self):
        self.assertNotEqual(sr.slugify("null"), "null")
        self.assertTrue(sr.slugify("Beef Stroganoff").startswith("beef"))


class InstructionsTests(unittest.TestCase):
    def test_plain_steps_omit_null_section_name(self):
        sections = sr.parse_instructions(["Chop onion", "Cook"])
        self.assertEqual(len(sections), 1)
        self.assertNotIn("name", sections[0])
        self.assertEqual(sections[0]["steps"], ["Chop onion", "Cook"])

    def test_howto_section_keeps_title(self):
        sections = sr.parse_instructions([
            {
                "@type": "HowToSection",
                "name": "Sauce",
                "itemListElement": [{"@type": "HowToStep", "text": "Simmer"}],
            }
        ])
        self.assertEqual(sections[0]["name"], "Sauce")
        self.assertEqual(sections[0]["steps"], ["Simmer"])


class PlanScrubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "recipes").mkdir()
        self.patches = [
            mock.patch.object(sr, "ROOT", self.root),
            mock.patch.object(sr, "RECIPES_DIR", self.root / "recipes"),
            mock.patch.object(sr, "INDEX_FILE", self.root / "index.json"),
            mock.patch.object(sr, "PLAN_FILE", self.root / "plan.json"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _write_recipe(self, slug, name=None):
        data = {
            "slug": slug,
            "name": name or slug.replace("-", " ").title(),
            "ingredients": ["1 onion"],
            "instructions": [{"steps": ["Cook"]}],
            "savedAt": "2026-09-09T00:00:00Z",
        }
        (sr.RECIPES_DIR / f"{slug}.json").write_text(sr.dump_json(data), encoding="utf-8")

    def test_scrub_replaces_json_null_and_deleted_slugs(self):
        self._write_recipe("beef-stroganoff")
        plan = {
            "days": [
                [{"kind": "dinner", "slug": None}],
                [{"kind": "dinner", "slug": "beef-stroganoff"}],
                [{"kind": "dinner", "slug": "ghost-recipe"}],
                [{"kind": "dinner", "slug": "null"}],
                [{"kind": "dinner", "slug": ""}],
                [{"kind": "dinner", "slug": ""}],
                [{"kind": "dinner", "slug": ""}],
            ],
            "checked": {"x": 1},
            "extras": [{"id": "1", "text": "milk"}],
            "updatedAt": "2026-09-10T00:00:00Z",
        }
        sr.PLAN_FILE.write_text(json.dumps(plan), encoding="utf-8")
        self.assertTrue(sr.scrub_plan())
        cleaned = json.loads(sr.PLAN_FILE.read_text(encoding="utf-8"))
        self.assertEqual(cleaned["days"][0][0]["slug"], "")
        self.assertEqual(cleaned["days"][1][0]["slug"], "beef-stroganoff")
        self.assertEqual(cleaned["days"][2][0]["slug"], "")
        self.assertEqual(cleaned["days"][3][0]["slug"], "")
        self.assertNotIn(": null", sr.PLAN_FILE.read_text(encoding="utf-8"))
        self.assertEqual(cleaned["extras"][0]["text"], "milk")

    def test_scrub_keeps_other_kind_and_drops_unknown_kinds(self):
        self._write_recipe("beef-stroganoff")
        plan = {
            "days": [
                [{"kind": "dinner", "slug": ""}, {"kind": "other", "slug": "beef-stroganoff"}, {"kind": "snack", "slug": "beef-stroganoff"}],
            ] + [[{"kind": "dinner", "slug": ""}] for _ in range(6)],
            "checked": {}, "extras": [], "updatedAt": "t",
        }
        sr.PLAN_FILE.write_text(json.dumps(plan), encoding="utf-8")
        self.assertTrue(sr.scrub_plan())
        monday = json.loads(sr.PLAN_FILE.read_text(encoding="utf-8"))["days"][0]
        self.assertEqual([m["kind"] for m in monday], ["dinner", "other"])
        self.assertEqual(monday[1]["slug"], "beef-stroganoff")

    def test_rebuild_index_scrubs_plan(self):
        self._write_recipe("pasta", "Creamy Pasta")
        sr.PLAN_FILE.write_text(
            json.dumps({"days": [[{"kind": "dinner", "slug": "missing"}]] + [[{"kind": "dinner", "slug": None}] for _ in range(6)],
                        "checked": {}, "extras": [], "updatedAt": "t"}),
            encoding="utf-8",
        )
        self.assertEqual(sr.rebuild_index(), 1)
        index = json.loads(sr.INDEX_FILE.read_text(encoding="utf-8"))
        self.assertEqual(index[0]["name"], "Creamy Pasta")
        self.assertNotIn(": null", sr.INDEX_FILE.read_text(encoding="utf-8"))
        plan = json.loads(sr.PLAN_FILE.read_text(encoding="utf-8"))
        self.assertTrue(all(m["slug"] == "" for day in plan["days"] for m in day))


if __name__ == "__main__":
    unittest.main()

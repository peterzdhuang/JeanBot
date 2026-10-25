import copy
from datetime import date, timedelta
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from quest_catalog import checksum, eligible, load_catalog, select_quest, validate_catalog


def snapshot(mediums=200):
    problems = [{"id": i, "slug": f"test-problem-{i}", "title": f"Test {i}",
                 "difficulty": "Medium" if i <= mediums else "Easy", "rank": i,
                 "url": f"https://leetcode.com/problems/test-problem-{i}/", "publicly_available": True}
                for i in range(1, 301)]
    return {"source_url": "https://example.org/test-only-list", "retrieved_at": "2026-10-24",
            "version": "test", "problems": problems, "checksum": checksum(problems)}


class CatalogTests(unittest.TestCase):
    def test_catalog_requires_source_identity_and_integrity(self):
        source = snapshot()
        self.assertEqual(len(validate_catalog(source)), 200)
        for change in (lambda x: x["problems"].pop(),
                       lambda x: x["problems"][0].update(slug="tampered"),
                       lambda x: x.update(source_url="")):
            invalid = copy.deepcopy(source)
            change(invalid)
            with self.assertRaises(ValueError):
                validate_catalog(invalid)

    def test_stable_medium_cycles_without_boundary_repeat(self):
        for size in (1, 2, 3, 200):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "catalog.json"
                path.write_text(json.dumps(snapshot(size)))
                with patch.dict("os.environ", {"TOP300_CATALOG": str(path)}):
                    start = date(2026, 1, 1)
                    picks = [select_quest(7, start + timedelta(days=i)) for i in range(size * 3)]
                    for offset in range(0, size * 3, size):
                        self.assertEqual(len({p["slug"] for p in picks[offset:offset + size]}), size)
                    if size > 1:
                        self.assertTrue(all(a["slug"] != b["slug"] for a, b in zip(picks, picks[1:])))
                    self.assertTrue(all(p["difficulty"] == "Medium" for p in picks))
                    self.assertEqual(picks[0], select_quest(7, start))

    def test_missing_catalog_is_explicit(self):
        with patch.dict("os.environ", {"TOP300_CATALOG": "/tmp/nonexistent-lcbot-test-catalog.json"}):
            with self.assertRaisesRegex(ValueError, "not configured"):
                select_quest(7, date(2026, 10, 24))

    def test_easy_requires_a_documented_medium_level_constraint(self):
        source = snapshot(1)
        easy = source["problems"][1]
        self.assertFalse(eligible(easy))
        easy.update(practice_difficulty="Medium", constraint="Use constant auxiliary space.",
                    constraint_reason="Reviewed two-pointer practice variant.",
                    constraint_source_url=easy["url"])
        source["checksum"] = checksum(source["problems"])
        self.assertEqual(len(validate_catalog(source)), 2)
        for field in ("constraint", "constraint_reason", "constraint_source_url"):
            invalid = copy.deepcopy(source)
            invalid["problems"][1].pop(field)
            invalid["checksum"] = checksum(invalid["problems"])
            with self.assertRaisesRegex(ValueError, "explicit constraint"):
                validate_catalog(invalid)

    def test_shipped_source_is_pinned_and_pool_contains_only_allowed_variants(self):
        source, pool = load_catalog(Path(__file__).with_name("top300.json"))
        self.assertEqual(len(source["problems"]), 300)
        self.assertIn(source["source_commit"], source["source_url"])
        self.assertEqual(len(pool), 184)
        self.assertEqual(sum(p["difficulty"] == "Medium" for p in pool), 183)
        variants = [p for p in pool if p["difficulty"] == "Easy"]
        self.assertEqual([p["slug"] for p in variants], ["backspace-string-compare"])
        self.assertIn("O(1)", variants[0]["constraint"])
        self.assertTrue(all(p.get("metadata_checked_at") for p in pool))
        self.assertNotIn("encode-and-decode-strings", {p["slug"] for p in pool})
        nodes = next(p for p in pool if p["slug"] == "count-complete-tree-nodes")
        self.assertEqual(nodes["difficulty"], "Medium")

    def test_selected_easy_retains_base_difficulty_and_constraint(self):
        source = snapshot(0)
        easy = source["problems"][0]
        easy.update(practice_difficulty="Medium", constraint="Use O(1) auxiliary space.",
                    constraint_reason="Medium-level practice assessment.", constraint_source_url=easy["url"])
        source["checksum"] = checksum(source["problems"])
        with patch("quest_catalog.load_catalog", return_value=(source, validate_catalog(source))):
            result = select_quest(7, date(2026, 10, 25))
        self.assertEqual(result["difficulty"], "Easy")
        self.assertEqual(result["practice_difficulty"], "Medium")
        self.assertEqual(result["constraint"], easy["constraint"])


class GridTests(unittest.TestCase):
    def test_png_encodes_counts_and_unknown_days_in_distinct_colors(self):
        from activity_grid import render_activity, GREENS, COLORS
        days = [{"date": "2026-10-24", "status": "active", "count": i} for i in range(5)]
        days += [{"date": "2026-10-25", "status": "unknown", "count": None},
                 {"date": "2026-10-26", "status": "future", "count": None}]
        with patch("activity_grid.activity_days", return_value=days):
            png = render_activity({}, date(2026, 10, 24))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        self.assertEqual((width, height), (42, 180))
        offset, compressed = 8, b""
        while offset < len(png):
            length = struct.unpack(">I", png[offset:offset + 4])[0]
            if png[offset + 4:offset + 8] == b"IDAT":
                compressed += png[offset + 8:offset + 8 + length]
            offset += 12 + length
        raw = zlib.decompress(compressed)
        colors = [COLORS["empty"], *GREENS, COLORS["unknown"], COLORS["future"]]
        for row, color in enumerate(colors):
            pixel_offset = (12 + row * 23) * (width * 3 + 1) + 1 + 12 * 3
            self.assertEqual(tuple(raw[pixel_offset:pixel_offset + 3]), color)


if __name__ == "__main__":
    unittest.main()

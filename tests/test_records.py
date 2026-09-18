"""Frontmatter parse/update, markers, index block, and id resolution."""

from __future__ import annotations

from harness import Base
from slate import records

SAMPLE = (
    "---\n"
    "slate: experiment\n"
    "status: planned\n"
    'title: "A quoted title"\n'
    "started:\n"
    "measures: a/x, b/y\n"
    "---\n"
    "# Body\n"
    "\n"
    "Line with trailing spaces   \n"
    "Unicode: σ and …\n"
    "No trailing newline at EOF"
)


class TestFrontmatter(Base):
    def test_read_fields(self):
        f = records.read_fields(SAMPLE)
        self.assertEqual(f["status"], "planned")
        self.assertEqual(f["title"], "A quoted title")  # quotes stripped
        self.assertEqual(f["started"], "")
        self.assertEqual(records.split_list(f["measures"]), ["a/x", "b/y"])

    def test_update_leaves_body_byte_identical(self):
        _, _, _, body_before = records._split(SAMPLE)
        updated = records.update_fields(
            SAMPLE, {"status": "running", "started": "2026-09-18T00:00:00Z"}
        )
        _, _, _, body_after = records._split(updated)
        self.assertEqual(body_before, body_after)
        self.assertEqual(records.get_field(updated, "status"), "running")
        self.assertEqual(records.get_field(updated, "started"), "2026-09-18T00:00:00Z")
        # Untouched keys keep their exact spelling.
        self.assertIn('title: "A quoted title"', updated)

    def test_update_appends_unknown_key(self):
        updated = records.update_fields(SAMPLE, {"outcome": "confirmed"})
        self.assertEqual(records.get_field(updated, "outcome"), "confirmed")
        _, _, _, body_before = records._split(SAMPLE)
        _, _, _, body_after = records._split(updated)
        self.assertEqual(body_before, body_after)

    def test_empty_value_written_without_trailing_space(self):
        updated = records.update_fields(SAMPLE, {"finished": ""})
        self.assertIn("\nfinished:\n", updated)


class TestMarkers(Base):
    def test_fill_and_after(self):
        self.assertTrue(records.has_fill("x <!-- slate:fill do this --> y"))
        self.assertFalse(records.has_fill("nothing here"))
        self.assertTrue(records.has_after("<!-- slate:after facts -->"))
        self.assertFalse(records.has_after("<!-- slate:fill -->"))


class TestIndexBlock(Base):
    def test_replaces_only_between_markers(self):
        text = "before\n<!-- slate:index -->\nOLD\n<!-- /slate:index -->\nafter\n"
        new, replaced = records.set_index_block(text, "NEW LINE")
        self.assertTrue(replaced)
        self.assertTrue(new.startswith("before\n"))
        self.assertTrue(new.endswith("after\n"))
        self.assertIn("<!-- slate:index -->\nNEW LINE\n<!-- /slate:index -->", new)
        self.assertNotIn("OLD", new)

    def test_no_block_reports_false(self):
        text = "no markers here\n"
        new, replaced = records.set_index_block(text, "x")
        self.assertFalse(replaced)
        self.assertEqual(new, text)


class TestIdResolution(Base):
    def test_exact(self):
        ids = ["arc/2026-09-18-foo", "arc/2026-09-18-bar"]
        self.assertEqual(records.resolve_id(ids, "arc/2026-09-18-foo"), "arc/2026-09-18-foo")

    def test_unique_suffix(self):
        ids = ["arc/2026-09-18-foo", "arc/2026-09-18-bar"]
        self.assertEqual(records.resolve_id(ids, "foo"), "arc/2026-09-18-foo")
        self.assertEqual(records.resolve_id(ids, "2026-09-18-bar"), "arc/2026-09-18-bar")

    def test_ambiguous(self):
        ids = ["a/2026-09-18-foo", "b/2026-09-18-foo"]
        with self.assertRaises(records.IdError):
            records.resolve_id(ids, "foo")

    def test_missing(self):
        with self.assertRaises(records.IdError):
            records.resolve_id(["a/x"], "zzz")

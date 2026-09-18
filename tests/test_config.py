"""KB discovery, the mini TOML reader, and tracker resolution."""

from __future__ import annotations

import os

from harness import Base
from slate import config


class TestDiscovery(Base):
    def test_own_dir(self):
        kb = self.make_kb()
        self.assertEqual(config.find_kb(kb), os.path.abspath(kb))

    def test_child_dir(self):
        kb = self.make_kb("kb")
        # Parent of the KB should discover the KB as a child.
        parent = os.path.dirname(kb)
        # Isolate: parent (self.tmp) contains only 'kb' plus what we add.
        self.assertEqual(config.find_kb(parent), os.path.abspath(kb))

    def test_walk_up_from_sibling_repo(self):
        kb = self.make_kb("kb")
        sibling = os.path.join(self.tmp, "repoA")
        os.makedirs(sibling)
        # From inside a sibling, walk up to the common parent and find the KB.
        self.assertEqual(config.find_kb(sibling), os.path.abspath(kb))

    def test_two_match_error(self):
        self.make_kb("kb1")
        self.make_kb("kb2")
        with self.assertRaises(config.KbAmbiguous) as ctx:
            config.find_kb(self.tmp)
        self.assertEqual(len(ctx.exception.matches), 2)

    def test_env_var(self):
        kb = self.make_kb("kb")
        elsewhere = os.path.join(self.tmp, "unrelated")
        os.makedirs(elsewhere)
        self.assertEqual(config.find_kb(elsewhere, env=kb), os.path.abspath(kb))

    def test_none_found(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        self.assertIsNone(config.find_kb(empty))

    def test_status_silent_when_none(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        r = self.cli_run("status", cwd=empty)
        self.assertEqual(r.code, 0)
        self.assertEqual(r.out, "")

    def test_other_command_exits_2_when_none(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        r = self.cli_run("where", cwd=empty)
        self.assertEqual(r.code, 2)
        self.assertIn("slate init", r.err)


class TestMiniToml(Base):
    def test_subset(self):
        text = (
            "# a comment\n"
            "[kb]\n"
            'name = "lang"   # trailing comment\n'
            'arcs = "arcs"\n'
            "readme_tree = true\n\n"
            "[repos]\n"
            'root = ".."\n'
            'include = ["a", "b", "c"]\n\n'
            "[experiment]\n"
            "max_hash_mb = 64\n"
        )
        data = config.mini_toml(text)
        self.assertEqual(data["kb"]["name"], "lang")
        self.assertTrue(data["kb"]["readme_tree"])
        self.assertEqual(data["repos"]["include"], ["a", "b", "c"])
        self.assertEqual(data["experiment"]["max_hash_mb"], 64)

    def test_hash_inside_quotes_kept(self):
        data = config.mini_toml('[kb]\nname = "a#b"\n')
        self.assertEqual(data["kb"]["name"], "a#b")

    def test_empty_array(self):
        data = config.mini_toml("[provenance]\ntools = []\n")
        self.assertEqual(data["provenance"]["tools"], [])


class TestTrackerResolution(Base):
    def test_explicit_none(self):
        kb = self.make_kb(tracker="none")
        self.assertEqual(self.cfg(kb).tracker_kind(), "none")

    def test_auto_without_beads_is_none(self):
        # tracker.dir defaults to KB/.. which has no .beads here.
        kb = self.make_kb(tracker="auto")
        self.assertEqual(self.cfg(kb).tracker_kind(), "none")

    def test_auto_with_beads_and_bd_is_bd(self):
        self.install_fake_bd()
        kb = self.make_kb(tracker="auto", tracker_dir=".")
        os.makedirs(os.path.join(kb, ".beads"))
        self.assertEqual(self.cfg(kb).tracker_kind(), "bd")

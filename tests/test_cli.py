"""CLI-level behaviour: init, where, and arc-epic creation through the tracker."""

from __future__ import annotations

import os

from harness import Base
from slate import records


class TestInit(Base):
    def test_init_writes_config_and_scaffolding(self):
        root = os.path.join(self.tmp, "newkb")
        os.makedirs(root)
        r = self.cli_run("init", "--kb", root, "--tracker", "none", "--repos-root", "..")
        self.assertEqual(r.code, 0)
        self.assertTrue(os.path.isfile(os.path.join(root, "slate.toml")))
        self.assertTrue(os.path.isfile(os.path.join(root, "arcs", "README.md")))
        gi = records.read(os.path.join(root, ".gitignore"))
        self.assertIn(".slate-private/", gi)
        self.assertIn("tracker: none", r.out)

    def test_init_does_not_overwrite(self):
        root = os.path.join(self.tmp, "newkb")
        os.makedirs(root)
        self.cli_run("init", "--kb", root, "--tracker", "none")
        # Hand-edit slate.toml, then re-init; the file must be preserved.
        with open(os.path.join(root, "slate.toml"), "a") as fh:
            fh.write("\n# hand edit\n")
        r = self.cli_run("init", "--kb", root, "--tracker", "none")
        self.assertEqual(r.code, 0)
        self.assertIn("# hand edit", records.read(os.path.join(root, "slate.toml")))


class TestWhere(Base):
    def test_where_json(self):
        kb = self.make_kb()
        r = self.cli_run("where", "--kb", kb, "--json")
        self.assertEqual(r.code, 0)
        self.assertIn('"tracker": "none"', r.out)

    def test_global_flags_accepted_before_subcommand(self):
        kb = self.make_kb()
        r = self.cli_run("--kb", kb, "--json", "where")
        self.assertEqual(r.code, 0)
        self.assertIn('"tracker": "none"', r.out)

    def test_where_prints_experimenter_and_commit_policy(self):
        kb = self.make_kb(
            extra_toml=(
                '\n[agent]\nexperimenter = "slate:runner"\n'
                '\n[commit]\npolicy = "auto"\n'
                'record_subject = "Record {title}"\n'
                'correct_subject = "Correct {title}"\n'
            )
        )
        r = self.cli_run("where", "--kb", kb)
        self.assertEqual(r.code, 0)
        self.assertIn("experimenter: slate:runner", r.out)
        self.assertIn("commit policy: auto", r.out)
        self.assertIn("Record {title}", r.out)
        self.assertIn("Correct {title}", r.out)


class TestArcEpicTracker(Base):
    def test_arc_new_creates_epic(self):
        self.install_fake_bd()
        kb = self.make_kb(tracker="auto", tracker_dir=".")
        os.makedirs(os.path.join(kb, ".beads"))
        r = self.cli_run("arc", "new", "arc", "--title", "T", "--directive", "D", "--kb", kb)
        self.assertEqual(r.code, 0)
        epic = records.get_field(
            records.read(os.path.join(kb, "arcs", "arc", "README.md")), "tracker"
        )
        self.assertTrue(epic.startswith("bd-"))
        self.assertIn("--type epic", self.bd_log_text())

    def test_exp_new_creates_child_task(self):
        self.install_fake_bd()
        kb = self.make_kb(tracker="auto", tracker_dir=".")
        os.makedirs(os.path.join(kb, ".beads"))
        self.cli_run("arc", "new", "arc", "--title", "T", "--directive", "D", "--kb", kb)
        r = self.cli_run("exp", "new", "arc", "x", "--title", "Exp", "--kb", kb)
        self.assertEqual(r.code, 0)
        log = self.bd_log_text()
        self.assertIn("--type task", log)
        self.assertIn("--labels slate-experiment", log)

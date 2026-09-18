"""index (only-between-markers) and status (line content + silence)."""

from __future__ import annotations

import json
import os

from harness import Base
from slate import arcs, decisions, handoffs, index, records, runs, tracker


class TestIndex(Base):
    def test_only_between_markers_changes(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        runs.new(cfg, trk, "arc", "x", "Exp title")
        handoffs.new(cfg, trk, "arc")

        arc_readme = os.path.join(kb, "arcs", "arc", "README.md")
        before = records.read(arc_readme)
        marker = "<!-- slate:index -->"
        end = "<!-- /slate:index -->"
        prefix_before = before[: before.index(marker) + len(marker)]
        suffix_before = before[before.index(end) :]

        index.rebuild(cfg)
        after = records.read(arc_readme)
        prefix_after = after[: after.index(marker) + len(marker)]
        suffix_after = after[after.index(end) :]

        self.assertEqual(prefix_before, prefix_after)
        self.assertEqual(suffix_before, suffix_after)
        self.assertIn("### Experiments", after)
        self.assertIn("2026-09-18-x", after)

    def test_arcs_table_rebuilt(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        index.rebuild(cfg)
        arcs_readme = records.read(os.path.join(kb, "arcs", "README.md"))
        self.assertIn("| arc |", arcs_readme)
        self.assertIn("| arc | active |", arcs_readme.replace("  ", " "))


class TestStatus(Base):
    def test_silent_when_nothing(self):
        kb = self.make_kb()
        r = self.cli_run("status", "--kb", kb)
        self.assertEqual(r.code, 0)
        self.assertEqual(r.out, "")

    def test_line_content(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        # An open handoff.
        handoffs.new(cfg, trk, "arc")
        # A running experiment with an outcome (finished, not harvested).
        eid, rec, _ = runs.new(cfg, trk, "arc", "x", "Exp")
        records.apply_update(os.path.join(rec, "README.md"), {"status": "running"})
        with open(os.path.join(rec, "outcome.json"), "w") as fh:
            json.dump({"exit_code": 0}, fh)
        # Two open decisions.
        decisions.ask(cfg, trk, "arc", "q1?")
        decisions.ask(cfg, trk, "arc", "q2?")

        r = self.cli_run("status", "--kb", kb)
        self.assertIn("open handoffs", r.out)
        self.assertIn("1 run finished, not harvested", r.out)
        self.assertIn("2 decisions waiting on the owner", r.out)
        self.assertIn("/slate:pickup", r.out)

    def test_hint_is_harvest_when_only_finished_runs(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        eid, rec, _ = runs.new(cfg, trk, "arc", "x", "Exp")
        records.apply_update(os.path.join(rec, "README.md"), {"status": "running"})
        with open(os.path.join(rec, "outcome.json"), "w") as fh:
            json.dump({"exit_code": 0}, fh)
        r = self.cli_run("status", "--kb", kb)
        self.assertIn("— /slate:harvest", r.out)

    def test_hint_is_decisions_when_only_decisions(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        decisions.ask(cfg, trk, "arc", "q1?")
        r = self.cli_run("status", "--kb", kb)
        self.assertIn("— /slate:decisions", r.out)

    def test_status_does_not_need_git(self):
        # repos_root points nowhere; status must still work (no git calls).
        kb = self.make_kb(repos_root="does-not-exist")
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        decisions.ask(cfg, trk, "arc", "q1?")
        r = self.cli_run("status", "--kb", kb)
        self.assertEqual(r.code, 0)
        self.assertIn("1 decisions waiting on the owner", r.out)

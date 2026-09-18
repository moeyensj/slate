"""promote (gates + README tree annotation) and stale (commit counting)."""

from __future__ import annotations

import json
import os

from harness import Base
from slate import arcs, knowledge, records, runs, tracker


class PromoteBase(Base):
    def make_exp(self, kb):
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        exp_id, rec, _ = runs.new(cfg, trk, "arc", "x", "Title")
        return cfg, exp_id, rec

    def write_note(self, kb, rel, body):
        path = os.path.join(kb, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(body)
        return path


class TestPromoteGates(PromoteBase):
    def test_refuses_without_experiment_mention(self):
        kb = self.make_kb()
        cfg, exp_id, _ = self.make_exp(kb)
        self.write_note(kb, "design/frames.md", "A note. As of 2026-09-18.\n")
        with self.assertRaises(records.GateError):
            knowledge.promote(cfg, exp_id, "design/frames.md", "the frames finding")

    def test_refuses_without_as_of(self):
        kb = self.make_kb()
        cfg, exp_id, _ = self.make_exp(kb)
        self.write_note(kb, "design/frames.md", f"Mentions {exp_id} but no stamp.\n")
        with self.assertRaises(records.GateError):
            knowledge.promote(cfg, exp_id, "design/frames.md", "the frames finding")

    def test_promotes_and_sets_promoted_to(self):
        kb = self.make_kb()
        cfg, exp_id, rec = self.make_exp(kb)
        self.write_note(
            kb, "design/frames.md", f"Based on {exp_id}. As of 2026-09-18 this holds.\n"
        )
        knowledge.promote(cfg, exp_id, "design/frames.md", "the frames finding")
        promoted = records.get_field(records.read(os.path.join(rec, "README.md")), "promoted_to")
        self.assertEqual(promoted, "design/frames.md")


class TestPromoteTree(PromoteBase):
    def _readme(self):
        return "# Knowledge base\n\n```\ndocs/\n  intro.md\ndesign/\n  frames.md\n  times.md\n```\n"

    def test_inserts_line_after_last_entry(self):
        kb = self.make_kb()
        cfg, exp_id, _ = self.make_exp(kb)
        with open(os.path.join(kb, "README.md"), "w") as fh:
            fh.write(self._readme())
        self.write_note(kb, "design/plane.md", f"From {exp_id}. As of 2026-09-18.\n")
        _eid, lines = knowledge.promote(cfg, exp_id, "design/plane.md", "the plane choice")
        self.assertTrue(any("README tree annotated" in ln for ln in lines))
        readme = records.read(os.path.join(kb, "README.md"))
        # New line sits after times.md, with the same 2-space indentation.
        self.assertIn("  times.md\n  plane.md  # the plane choice\n", readme)

    def test_by_hand_line_when_no_tree(self):
        kb = self.make_kb()
        cfg, exp_id, _ = self.make_exp(kb)
        with open(os.path.join(kb, "README.md"), "w") as fh:
            fh.write("# Knowledge base\n\nNo tree here.\n")
        self.write_note(kb, "design/plane.md", f"From {exp_id}. As of 2026-09-18.\n")
        _eid, lines = knowledge.promote(cfg, exp_id, "design/plane.md", "the plane choice")
        self.assertTrue(any("add by hand" in ln for ln in lines))
        self.assertTrue(any("plane.md  # the plane choice" in ln for ln in lines))


class TestStale(PromoteBase):
    def test_counts_commits_since_recorded_sha(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        sub = os.path.join(repo, "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "file.txt"), "w") as fh:
            fh.write("v0\n")
        self.git_commit(repo, "add", "-A")
        self.git_commit(repo, "commit", "-m", "add measured file")
        recorded = self.head(repo)
        # Two later commits touch the measured path; one touches something else.
        for i in (1, 2):
            with open(os.path.join(sub, "file.txt"), "w") as fh:
                fh.write(f"v{i}\n")
            self.git_commit(repo, "commit", "-am", f"touch measured {i}")
        with open(os.path.join(repo, "seed.txt"), "a") as fh:
            fh.write("unrelated\n")
        self.git_commit(repo, "commit", "-am", "unrelated change")

        cfg, exp_id, rec = self.make_exp(kb)
        records.apply_update(
            os.path.join(rec, "README.md"),
            {"promoted_to": "design/frames.md", "measures": "repoA/sub/file.txt"},
        )
        prov = {"repos": {"repoA": {"head": recorded}}, "time": "2026-09-01T00:00:00Z"}
        with open(os.path.join(rec, "provenance.json"), "w") as fh:
            json.dump(prov, fh)

        rows = knowledge.stale(cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["path"], "repoA/sub/file.txt")
        self.assertEqual(rows[0]["commits"], 2)

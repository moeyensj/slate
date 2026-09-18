"""handoff new (generated sections), check, private notes and supersede."""

from __future__ import annotations

import json
import os
import time

from harness import Base
from slate import arcs, decisions, handoffs, records, runs, tracker


class HandoffBase(Base):
    def arc(self, kb, slug="arc", tracker_obj=None):
        cfg = self.cfg(kb)
        trk = tracker_obj or tracker.build(cfg)
        arcs.new(cfg, trk, slug, "Arc title", "Do the thing.")
        return cfg, trk

    def make_running(self, cfg, slug="arc"):
        trk = tracker.build(cfg)
        eid, rec, _ = runs.new(cfg, trk, slug, "run1", "Running one")
        records.apply_update(
            os.path.join(rec, "README.md"), {"status": "running", "started": "2026-09-18T00:00:00Z"}
        )
        with open(os.path.join(rec, "run.json"), "w") as fh:
            json.dump(
                {
                    "pid": os.getpid(),
                    "host": "h",
                    "log": os.path.join(rec, "log.txt"),
                    "start_epoch": time.time() - 120,
                },
                fh,
            )
        return eid


class TestGeneratedSections(HandoffBase):
    def test_first_handoff_landed(self):
        kb = self.make_kb()
        cfg, _ = self.arc(kb)
        _hid, path = handoffs.new(cfg, tracker.build(cfg), "arc")
        text = records.read(path)
        self.assertIn("First handoff for this arc.", text)

    def test_landed_since_previous(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        cfg, _ = self.arc(kb)
        handoffs.new(cfg, tracker.build(cfg), "arc")  # note 1 records current sha
        with open(os.path.join(repo, "feature.txt"), "w") as fh:
            fh.write("x\n")
        self.git_commit(repo, "add", "-A")
        self.git_commit(repo, "commit", "-m", "New landed work")
        _hid, path2 = handoffs.new(cfg, tracker.build(cfg), "arc")  # note 2
        text = records.read(path2)
        self.assertIn("New landed work", text)
        self.assertIn("### repoA", text)

    def test_running_section(self):
        kb = self.make_kb()
        cfg, _ = self.arc(kb)
        eid = self.make_running(cfg)
        _hid, path = handoffs.new(cfg, tracker.build(cfg), "arc")
        text = records.read(path)
        self.assertIn(eid, text)
        self.assertIn("elapsed=", text)

    def test_running_nothing(self):
        kb = self.make_kb()
        cfg, _ = self.arc(kb)
        _hid, path = handoffs.new(cfg, tracker.build(cfg), "arc")
        running = handoffs._section(records.read(path), "Running now")
        self.assertEqual(running.strip(), "Nothing.")

    def test_decisions_section(self):
        kb = self.make_kb()
        cfg, trk = self.arc(kb)
        decisions.ask(cfg, trk, "arc", "An open question?", blocking=True)
        did, _ = decisions.ask(cfg, trk, "arc", "A settled question?")
        decisions.rule(cfg, trk, did, "owner", "Yes, do it.")
        _hid, path = handoffs.new(cfg, trk, "arc")
        text = records.read(path)
        self.assertIn("(blocking)", text)
        self.assertIn("Ruled, do not relitigate", text)
        self.assertIn("D-002", text)  # the ruled one appears in the recent-rulings list

    def test_tracker_none_says_no_tracker(self):
        kb = self.make_kb()
        cfg, _ = self.arc(kb)
        _hid, path = handoffs.new(cfg, tracker.build(cfg), "arc")
        self.assertIn("No tracker.", records.read(path))

    def test_supersedes_closes_previous(self):
        kb = self.make_kb()
        cfg, _ = self.arc(kb)
        hid1, path1 = handoffs.new(cfg, tracker.build(cfg), "arc")
        hid2, path2 = handoffs.new(cfg, tracker.build(cfg), "arc")
        self.assertEqual(records.get_field(records.read(path1), "status"), "closed")
        self.assertIn("superseded", records.get_field(records.read(path1), "closed"))
        self.assertEqual(records.get_field(records.read(path2), "supersedes"), hid1)


class TestTrackerSection(HandoffBase):
    def test_bd_lists_open_issues(self):
        self.install_fake_bd()
        kb = self.make_kb(tracker="auto", tracker_dir=".")
        os.makedirs(os.path.join(kb, ".beads"))
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        _hid, path = handoffs.new(cfg, trk, "arc")
        text = records.read(path)
        self.assertIn("bd-open1", text)
        self.assertIn("bd-prog1", text)


class TestCheckAndPrivate(HandoffBase):
    def test_check_fails_on_marker_then_passes(self):
        kb = self.make_kb()
        cfg, _ = self.arc(kb)
        hid, path = handoffs.new(cfg, tracker.build(cfg), "arc")
        ok, _ = handoffs.check(cfg, hid)
        self.assertFalse(ok)  # template still has slate:fill markers
        text = records.read(path).replace("<!-- slate:fill", "<!-- done")
        records.write(path, text)
        ok2, _ = handoffs.check(cfg, hid)
        self.assertTrue(ok2)

    def test_check_fails_on_too_many_lines(self):
        kb = self.make_kb(extra_toml="\n[handoff]\nmax_lines = 5\n")
        cfg, _ = self.arc(kb)
        hid, path = handoffs.new(cfg, tracker.build(cfg), "arc")
        # Remove fills so only the line-count check can fail.
        records.write(path, records.read(path).replace("<!-- slate:fill", "<!-- done"))
        ok, lines = handoffs.check(cfg, hid)
        self.assertFalse(ok)
        self.assertTrue(any("max_lines" in ln for ln in lines))

    def test_private_note_and_ignore(self):
        kb = self.make_kb()
        cfg, _ = self.arc(kb)
        _hid, path = handoffs.new(cfg, tracker.build(cfg), "arc", private=True)
        self.assertIn(os.path.join(".slate-private", "arc", "handoffs"), path)
        self.assertTrue(os.path.isfile(path))
        gi = records.read(os.path.join(kb, ".gitignore"))
        self.assertIn(".slate-private/", gi)

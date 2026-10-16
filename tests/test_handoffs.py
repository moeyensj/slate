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


class TestIssueIds(HandoffBase):
    def test_generated_lines_yield_their_id_whatever_its_shape(self):
        section = (
            "## Tracker\n\n"
            "- thor-rust-68kz [open] Fix the thing\n"
            "- thor-rust-4bls.3 [in_progress] A child issue\n"
            "- bd-open1 [open] a long-term pre-registration fix\n"
            "- my_proj-ab12 [open] an underscore in the prefix\n"
            "- THOR-9f3k [open] capitals in the prefix\n"
        )
        ids = handoffs._issue_ids(section)
        self.assertEqual(
            ids, ["THOR-9f3k", "bd-open1", "my_proj-ab12", "thor-rust-4bls.3", "thor-rust-68kz"]
        )
        self.assertNotIn("thor-rust", ids)  # the prefix alone is not an issue
        self.assertNotIn("pre-registration", ids)  # a title is not searched

    def test_hand_written_lines_are_searched_for_hyphenated_ids(self):
        # Only the whole generated shape is read as a generated line; a bullet
        # of any other shape, a placeholder id included, is searched instead.
        section = (
            "## Tracker\n\n"
            "see thor-rust-68kz and thor-rust-4bls.3, then bd-open1\n"
            "- See [bd-abc1](http://x) for the plan\n"
            "- Blocked [waiting on infra] thor-rust-9k2m\n"
            "- ? [open] an issue bd returned without an id\n"
        )
        self.assertEqual(
            handoffs._issue_ids(section),
            ["bd-abc1", "bd-open1", "thor-rust-4bls.3", "thor-rust-68kz", "thor-rust-9k2m"],
        )

    def test_pickup_reports_closed_hyphenated_ids(self):
        self.install_fake_bd()
        kb = self.make_kb(tracker="auto", tracker_dir=".")
        os.makedirs(os.path.join(kb, ".beads"))
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        hid, path = handoffs.new(cfg, trk, "arc")
        text = records.read(path)
        # Stand in for a tracker whose prefix carries a hyphen.
        generated = handoffs._section(text, "Tracker")
        records.write(
            path,
            text.replace(
                generated, "- thor-rust-68kz [open] Fix\n- thor-rust-4bls.3 [open] Child", 1
            ),
        )
        os.environ["FAKE_BD_CLOSED"] = "thor-rust-68kz,thor-rust-4bls.3"
        _code, lines = handoffs.pickup(cfg, trk, hid, dry=True)
        self.assertIn("closed since: thor-rust-68kz", lines)
        self.assertIn("closed since: thor-rust-4bls.3", lines)
        log = self.bd_log_text()
        self.assertIn("show thor-rust-68kz --json", log)
        self.assertNotIn("show thor-rust --json", log)  # never the bare prefix


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

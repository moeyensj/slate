"""pickup: per-repository state classification, exit codes, dry-run, claim warning."""

from __future__ import annotations

import os
import shutil

from harness import Base
from slate import arcs, handoffs, records, tracker, util


class PickupBase(Base):
    def setup_note(self, on_branch=None):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        if on_branch:
            self.git_commit(repo, "checkout", "-b", on_branch)
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        hid, path = handoffs.new(cfg, trk, "arc")
        return cfg, trk, repo, hid, path

    def state_line(self, lines, repo="repoA"):
        for ln in lines:
            if ln.startswith(repo + ":"):
                return ln.split(":", 1)[1].strip()
        return None


class TestStates(PickupBase):
    def test_same(self):
        cfg, trk, repo, hid, _ = self.setup_note()
        code, lines = handoffs.pickup(cfg, trk, hid, dry=True)
        self.assertEqual(self.state_line(lines), "same")
        self.assertEqual(code, 0)

    def test_advanced(self):
        cfg, trk, repo, hid, _ = self.setup_note()
        with open(os.path.join(repo, "more.txt"), "w") as fh:
            fh.write("x\n")
        self.git_commit(repo, "add", "-A")
        self.git_commit(repo, "commit", "-m", "more")
        code, lines = handoffs.pickup(cfg, trk, hid, dry=True)
        self.assertEqual(self.state_line(lines), "advanced 1")
        self.assertEqual(code, 0)

    def test_dirty_changed(self):
        cfg, trk, repo, hid, _ = self.setup_note()
        with open(os.path.join(repo, "seed.txt"), "a") as fh:
            fh.write("dirty\n")
        code, lines = handoffs.pickup(cfg, trk, hid, dry=True)
        self.assertEqual(self.state_line(lines), "dirty changed")
        self.assertEqual(code, 0)

    def test_diverged_exit1(self):
        cfg, trk, repo, hid, _ = self.setup_note()
        self.git_commit(repo, "commit", "--amend", "-m", "reworded")
        code, lines = handoffs.pickup(cfg, trk, hid, dry=True)
        self.assertEqual(self.state_line(lines), "diverged")
        self.assertEqual(code, 1)

    def test_branch_gone_exit1(self):
        cfg, trk, repo, hid, _ = self.setup_note(on_branch="feature")
        with open(os.path.join(repo, "f.txt"), "w") as fh:
            fh.write("f\n")
        self.git_commit(repo, "add", "-A")
        self.git_commit(repo, "commit", "-m", "feature work")
        # Re-record on the feature tip by making a fresh note, then delete the branch.
        hid2, _ = handoffs.new(cfg, trk, "arc")
        self.git_commit(repo, "checkout", "main")
        self.git_commit(repo, "branch", "-D", "feature")
        code, lines = handoffs.pickup(cfg, trk, hid2, dry=True)
        self.assertEqual(self.state_line(lines), "branch gone")
        self.assertEqual(code, 1)

    def test_branch_merged(self):
        cfg, trk, repo, hid, _ = self.setup_note(on_branch="feature")
        with open(os.path.join(repo, "f.txt"), "w") as fh:
            fh.write("f\n")
        self.git_commit(repo, "add", "-A")
        self.git_commit(repo, "commit", "-m", "feature work")
        hid2, _ = handoffs.new(cfg, trk, "arc")  # records feature tip F
        c0 = self.git(repo, "rev-parse", "main").stdout.strip()
        self.git_commit(repo, "checkout", "main")
        self.git_commit(repo, "merge", "--no-ff", "feature", "-m", "merge feature")
        self.git_commit(repo, "checkout", "-b", "other", c0)  # HEAD without F
        code, lines = handoffs.pickup(cfg, trk, hid2, dry=True)
        self.assertEqual(self.state_line(lines), "branch merged")
        self.assertEqual(code, 0)

    def test_missing_exit1(self):
        cfg, trk, repo, hid, _ = self.setup_note()
        shutil.rmtree(repo)
        code, lines = handoffs.pickup(cfg, trk, hid, dry=True)
        self.assertEqual(self.state_line(lines), "missing")
        self.assertEqual(code, 1)


class TestPickupBehavior(PickupBase):
    def test_dry_changes_nothing(self):
        cfg, trk, repo, hid, path = self.setup_note()
        before = records.read(path)
        handoffs.pickup(cfg, trk, hid, dry=True, session="s1")
        self.assertEqual(records.read(path), before)

    def test_non_dry_marks_picked_up_and_prints_path(self):
        cfg, trk, repo, hid, path = self.setup_note()
        code, lines = handoffs.pickup(cfg, trk, hid, dry=False, session="s1")
        self.assertEqual(records.get_field(records.read(path), "status"), "picked-up")
        self.assertEqual(lines[-1], path)

    def test_claim_warning_and_no_warning(self):
        cfg, trk, repo, hid, path = self.setup_note()
        # A recent pickup by a different session.
        stamp = f"{util.now_ts()} otherhost s-other"
        records.apply_update(path, {"picked_up": stamp})
        _code, lines = handoffs.pickup(cfg, trk, hid, dry=True, session="s-me")
        self.assertTrue(any("WARNING" in ln and "s-other" in ln for ln in lines))
        # Same session: no warning.
        _c2, lines2 = handoffs.pickup(cfg, trk, hid, dry=True, session="s-other")
        self.assertFalse(any("WARNING" in ln for ln in lines2))

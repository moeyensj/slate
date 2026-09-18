"""Snapshot: pushed detection, KB dirty-count filtering, hardware/provenance
sections, and the dirty-patch that recreates untracked files on apply."""

from __future__ import annotations

import os

from harness import Base
from slate import arcs, gitstate, tracker


class TestPushed(Base):
    def test_pushed_no_without_remote_yes_after_push(self):
        repo = os.path.join(self.tmp, "work")
        self.git_init(repo)
        self.assertFalse(gitstate.pushed(repo))  # no remote
        remote = os.path.join(self.tmp, ".remote.git")  # dot => excluded from covered repos
        os.makedirs(remote)
        self.git(remote, "init", "--bare")
        self.git_commit(repo, "remote", "add", "origin", remote)
        self.git_commit(repo, "push", "origin", "main")
        self.assertTrue(gitstate.pushed(repo))


class TestKbDirtyIgnore(Base):
    def test_kb_dirty_count_ignores_arcs_and_private(self):
        kb = self.make_kb()
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "seed")
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")  # writes under arcs/
        os.makedirs(os.path.join(kb, ".slate-private", "arc"), exist_ok=True)
        with open(os.path.join(kb, ".slate-private", "note.md"), "w") as fh:
            fh.write("private\n")
        snap = gitstate.snapshot(cfg)
        self.assertEqual(snap["repos"][cfg.name]["dirty"], 0)  # slate's own paths ignored
        # A change outside slate's paths is still counted.
        with open(os.path.join(kb, "NOTES.md"), "w") as fh:
            fh.write("x\n")
        snap2 = gitstate.snapshot(cfg)
        self.assertEqual(snap2["repos"][cfg.name]["dirty"], 1)


class TestUntrackedPatchRoundTrip(Base):
    def test_untracked_file_recreated_by_patch(self):
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)  # clean; seed.txt committed
        # The ONLY change is a new untracked file (the old code wrote an empty patch).
        newfile = os.path.join(repo, "newfile.txt")
        with open(newfile, "w") as fh:
            fh.write("brand new\ncontent\n")
        dest = os.path.join(self.tmp, "patches", "repoA.patch")
        info = gitstate.write_patch(repo, dest, 1024 * 1024, 256 * 1024)
        self.assertGreater(info["written"], 0)  # not empty
        self.assertEqual(info["embedded_untracked"], 1)
        # Apply to a clean clone and get the untracked file back.
        clone = os.path.join(self.tmp, "clone")
        self.git(repo, "clone", repo, clone)
        self.assertFalse(os.path.exists(os.path.join(clone, "newfile.txt")))
        r = self.git(clone, "apply", dest)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(clone, "newfile.txt")) as fh:
            self.assertEqual(fh.read(), "brand new\ncontent\n")

    def test_large_untracked_listed_not_embedded(self):
        repo = os.path.join(self.tmp, "repoB")
        self.git_init(repo)
        with open(os.path.join(repo, "big.dat"), "w") as fh:
            fh.write("z" * 5000)
        dest = os.path.join(self.tmp, "patches", "repoB.patch")
        info = gitstate.write_patch(repo, dest, 1024 * 1024, 1024)  # preserve cap 1 KB
        self.assertEqual(info["embedded_untracked"], 0)
        self.assertEqual(len(info["large_untracked"]), 1)
        self.assertEqual(info["large_untracked"][0]["path"], "big.dat")
        self.assertRegex(info["large_untracked"][0]["sha256"], r"^[0-9a-f]{64}$")


class TestSnapshotContents(Base):
    def test_hardware_and_env(self):
        kb = self.make_kb(extra_toml='\n[provenance]\nenv = ["SLATE_TEST_ENV"]\n')
        os.environ["SLATE_TEST_ENV"] = "hello"
        self.addCleanup(os.environ.pop, "SLATE_TEST_ENV", None)
        snap = gitstate.snapshot(self.cfg(kb))
        self.assertIn("hardware", snap)
        for key in ("cpu", "cores", "memory_bytes", "os"):
            self.assertIn(key, snap["hardware"])
        self.assertEqual(snap["env"]["SLATE_TEST_ENV"], "hello")

    def test_lockfiles_hashed(self):
        kb = self.make_kb(extra_toml='\n[provenance]\nlockfiles = ["*.lock"]\n')
        with open(os.path.join(self.tmp, "a.lock"), "w") as fh:
            fh.write("locked\n")
        snap = gitstate.snapshot(self.cfg(kb))
        paths = {lf["path"]: lf for lf in snap["lockfiles"]}
        self.assertIn("a.lock", paths)
        self.assertRegex(paths["a.lock"]["sha256"], r"^[0-9a-f]{64}$")


class TestCoveredRepos(Base):
    def test_bare_repository_is_not_covered_but_worktree_is(self):
        kb = self.make_kb()
        self.git_init(os.path.join(self.tmp, "code"))
        bare = os.path.join(self.tmp, "mirror.git")  # no dot: only bareness excludes it
        os.makedirs(bare)
        self.git(bare, "init", "--bare")
        names = [name for name, _ in self.cfg(kb).covered_repos()]
        self.assertIn("code", names)
        self.assertNotIn("mirror.git", names)


class TestPushedReason(Base):
    def test_unpushed_code_counts_but_unpushed_kb_does_not(self):
        kb = self.make_kb()
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "seed")  # the KB has no remote: not pushed
        self.git_init(os.path.join(self.tmp, "code"))  # no remote either
        self.cli_run("arc", "new", "arc", "--title", "A", "--directive", "D", cwd=kb)
        self.cli_run("exp", "new", "arc", "t", "--title", "T", "--hypothesis", "h", cwd=kb)
        out = self.cli_run("exp", "check", "t", cwd=kb).out
        self.assertIn("not pushed: code", out)
        self.assertNotIn("not pushed: kb", out)


class TestKbPatchLeavesOutSlateRecords(Base):
    def test_patch_has_the_stray_note_but_not_the_arc_records(self):
        kb = self.make_kb()
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "seed")
        cfg = self.cfg(kb)
        arcs.new(
            cfg, tracker.build(cfg), "arc", "Arc title", "Do the thing."
        )  # untracked, slate's own
        with open(os.path.join(kb, "NOTES.md"), "w") as fh:
            fh.write("stray\n")
        dest = os.path.join(self.tmp, "kb.patch")
        gitstate.write_patch(kb, dest, 1024 * 1024, 256 * 1024, cfg.kb_dirty_ignore())
        text = open(dest).read()
        self.assertIn("NOTES.md", text)
        self.assertNotIn("arcs/arc/README.md", text)
        # Without the ignore list the record is embedded: the filter is what excludes it.
        gitstate.write_patch(kb, dest, 1024 * 1024, 256 * 1024)
        self.assertIn("arcs/arc/README.md", open(dest).read())


class TestArcRepos(Base):
    def _workspace(self):
        kb = self.make_kb(extra_toml="")
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "seed")
        for name in ("alpha", "beta", "gamma"):
            self.git_init(os.path.join(self.tmp, name))
        return kb

    def test_arc_repos_override_the_default_and_the_kb_is_always_covered(self):
        kb = self._workspace()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "narrow", "Narrow", "Do it.", repos=["beta"])
        arcs.new(cfg, trk, "plain", "Plain", "Do it.")
        self.assertEqual([n for n, _ in cfg.covered_repos("narrow")], ["beta", cfg.name])
        plain = [n for n, _ in cfg.covered_repos("plain")]
        self.assertEqual(sorted(plain), sorted(["alpha", "beta", "gamma", cfg.name]))
        self.assertEqual(list(gitstate.snapshot(cfg, "narrow")["repos"]), ["beta", cfg.name])

    def test_named_default_list_still_covers_the_kb(self):
        kb = self.make_kb(extra_toml="")
        with open(os.path.join(kb, "slate.toml")) as fh:
            toml = fh.read().replace('root = ".."\n', 'root = ".."\ninclude = ["alpha"]\n')
        with open(os.path.join(kb, "slate.toml"), "w") as fh:
            fh.write(toml)
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "seed")
        self.git_init(os.path.join(self.tmp, "alpha"))
        cfg = self.cfg(kb)
        self.assertEqual([n for n, _ in cfg.covered_repos()], ["alpha", cfg.name])

    def test_union_sees_a_repo_only_one_arc_names(self):
        kb = self.make_kb(extra_toml="")
        with open(os.path.join(kb, "slate.toml")) as fh:
            toml = fh.read().replace('root = ".."\n', 'root = ".."\ninclude = ["alpha"]\n')
        with open(os.path.join(kb, "slate.toml"), "w") as fh:
            fh.write(toml)
        for name in ("alpha", "beta"):
            self.git_init(os.path.join(self.tmp, name))
        cfg = self.cfg(kb)
        arcs.new(cfg, tracker.build(cfg), "b", "B", "Do it.", repos=["beta"])
        self.assertNotIn("beta", [n for n, _ in cfg.covered_repos()])
        self.assertIn("beta", [n for n, _ in cfg.all_covered_repos()])


class TestMissingBinary(Base):
    def test_missing_configured_binary_is_reported_and_counts_against_the_verdict(self):
        extra = '\n[provenance]\nbinaries = ["/nonexistent/libengine.dylib"]\n'
        kb = self.make_kb(extra_toml=extra)
        self.cli_run("arc", "new", "arc", "--title", "A", "--directive", "D", cwd=kb)
        self.cli_run("exp", "new", "arc", "t", "--title", "T", "--hypothesis", "h", cwd=kb)
        out = self.cli_run("exp", "check", "t", cwd=kb).out
        self.assertIn("configured binary not found: /nonexistent/libengine.dylib", out)
        self.assertIn("reconstructable: no", out)

    def test_present_binary_adds_no_reason(self):
        import sys

        extra = f'\n[provenance]\nbinaries = ["{sys.executable}"]\n'
        kb = self.make_kb(extra_toml=extra)
        self.cli_run("arc", "new", "arc", "--title", "A", "--directive", "D", cwd=kb)
        self.cli_run("exp", "new", "arc", "t", "--title", "T", "--hypothesis", "h", cwd=kb)
        self.assertNotIn(
            "configured binary not found", self.cli_run("exp", "check", "t", cwd=kb).out
        )

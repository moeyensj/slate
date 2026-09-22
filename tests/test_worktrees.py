"""Linked worktrees named as repos: a `repos:` entry is a path relative to
`repos.root` (slashes allowed); its snapshot key is the entry as given, its
dirty patch file name is sanitised (`/` -> `__`), and a rerun reconstructs it
at `<tmp>/<entry>` so run.sh's `$SLATE_REPOS_ROOT/<entry>` resolves to it.
"""

from __future__ import annotations

import json
import os

from slate import gitstate, records, runs, scan
from test_rerun import RerunBase

ENTRY = ".claude/worktrees/wt"


class TestWorktreeRepo(RerunBase):
    def _worktree(self, content="v1\n"):
        main = os.path.join(self.tmp, "main")
        self.git_init(main, filename="code.txt", content=content)
        wt = os.path.join(self.tmp, ".claude", "worktrees", "wt")
        os.makedirs(os.path.dirname(wt), exist_ok=True)
        r = self.git_commit(main, "worktree", "add", "-b", "wtbranch", wt)
        self.assertEqual(r.returncode, 0, r.stderr)
        return main, wt

    def _push_worktree(self, wt):
        remote = os.path.join(self.tmp, ".remote.git")  # dotted => not auto-covered
        os.makedirs(remote)
        self.git(remote, "init", "--bare")
        self.git_commit(wt, "remote", "add", "origin", remote)
        self.git_commit(wt, "push", "origin", "HEAD:refs/heads/wtbranch")

    def test_snapshot_key_is_entry_and_patch_name_sanitised(self):
        kb = self.make_kb()
        _main, wt = self._worktree()
        with open(os.path.join(wt, "code.txt"), "a") as fh:
            fh.write("dirty\n")  # dirty so a patch is written
        cfg, eid, rec = self.exp(kb, "#!/bin/sh\necho hi > results/out.txt\n")
        records.apply_update(os.path.join(rec, "README.md"), {"repos": ENTRY})
        _ok, lines = runs.check(cfg, rec)
        # The snapshot key is the entry as given (slashes intact).
        snap = gitstate.snapshot(cfg, repos=runs.record_repos(cfg, rec))
        self.assertIn(ENTRY, snap["repos"])
        self.assertEqual(snap["repos"][ENTRY]["branch"], "wtbranch")
        # The patch is named with '/' -> '__'; no nested "dirty/.claude/..." tree.
        self.assertTrue(os.path.isfile(os.path.join(rec, "dirty", ".claude__worktrees__wt.patch")))
        self.assertFalse(os.path.exists(os.path.join(rec, "dirty", ".claude")))
        self.assertTrue(any("dirty: .claude/worktrees/wt" in ln for ln in lines), lines)

    def test_rerun_reconstructs_worktree_at_tmp_entry(self):
        kb = self.make_kb()
        _main, wt = self._worktree()
        self._push_worktree(wt)
        run_sh = f'#!/bin/sh\ncat "$SLATE_REPOS_ROOT/{ENTRY}/code.txt" > results/out.txt\n'
        cfg, eid, rec = self.exp(kb, run_sh)
        records.apply_update(os.path.join(rec, "README.md"), {"repos": ENTRY})
        runs.start(cfg, rec, detach=False)
        prov = runs._load_provenance(rec)
        self.assertEqual(prov["reconstructable"], "yes", prov.get("reconstructable_reasons"))
        patcher, created = self.mocked_tmp()
        with patcher:
            code, lines = runs.rerun(cfg, eid, keep=True)
        self.assertEqual(code, 0, "\n".join(lines))
        # The reconstructed tree sits at <tmp>/<entry> (slashes preserved).
        recon = os.path.join(created[0], ENTRY, "code.txt")
        self.assertTrue(os.path.isfile(recon), "\n".join(lines))
        with open(recon) as fh:
            self.assertEqual(fh.read(), "v1\n")
        rr = [e for e in scan.experiment_ids(cfg) if e.endswith("-rerun")][0]
        _id, rr_dir = scan.find_experiment(cfg, rr)
        result = json.load(open(os.path.join(rr_dir, "rerun.json")))
        self.assertEqual(result["reproduced"], "yes")


class TestNestedWorktreeIsNotDirt(RerunBase):
    def test_a_nested_worktree_neither_counts_as_dirty_nor_enters_the_patch(self):
        repo = os.path.join(self.tmp, "code")
        self.git_init(repo)
        self.git_commit(repo, "worktree", "add", "-q", ".claude/worktrees/wt", "-b", "wt")
        self.assertEqual(gitstate.dirty_count(repo), 0)  # the worktree is a repo, not dirt
        with open(os.path.join(repo, "loose.txt"), "w") as fh:
            fh.write("x\\n")
        self.assertEqual(gitstate.dirty_count(repo), 1)  # a real untracked file still counts
        dest = os.path.join(self.tmp, "code.patch")
        info = gitstate.write_patch(repo, dest, 1024 * 1024, 256 * 1024)
        self.assertEqual(info["embedded_untracked"], 1)
        self.assertNotIn(".claude/worktrees", open(dest).read())

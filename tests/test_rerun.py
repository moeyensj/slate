"""exp rerun: reconstruct the recorded state, run again, and compare outputs.

Reconstruction happens in a temp clone; the original working trees are never
touched. Covers identical / different / equivalent, the reconstructable-no
refusal and its --force override, the changed-input refusal (no override), the
dirty patch applied in the reconstructed tree, and temp-tree keep/remove.
"""

from __future__ import annotations

import json
import os
import re
import tempfile as _tf
from unittest import mock

from harness import Base
from slate import arcs, records, runs, scan, tracker


def _strip(path):
    text = records.read(path)
    text = re.sub(r"<!-- slate:fill.*?-->", "done", text, flags=re.DOTALL)
    text = re.sub(r"<!-- slate:after.*?-->", "done", text, flags=re.DOTALL)
    records.write(path, text)


class RerunBase(Base):
    def push(self, repo):
        remote = os.path.join(self.tmp, ".remote-" + os.path.basename(repo) + ".git")
        os.makedirs(remote)
        self.git(remote, "init", "--bare")
        self.git_commit(repo, "remote", "add", "origin", remote)
        self.git_commit(repo, "push", "origin", "main")

    def exp(self, kb, run_sh, outputs="results/out.txt\n", inputs="none\n", slug="x"):
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        if "a" not in [s for s, _ in scan.arc_dirs(cfg)]:
            arcs.new(cfg, trk, "a", "Arc", "Do it.")
        eid, rec, _ = runs.new(cfg, trk, "a", slug, "Title", hypothesis="h")
        _strip(os.path.join(rec, "README.md"))
        with open(os.path.join(rec, "run.sh"), "w") as fh:
            fh.write(run_sh)
        os.chmod(os.path.join(rec, "run.sh"), 0o755)
        with open(os.path.join(rec, "outputs.txt"), "w") as fh:
            fh.write(outputs)
        with open(os.path.join(rec, "inputs.txt"), "w") as fh:
            fh.write(inputs)
        return cfg, eid, rec

    def run_exp(self, *args, **kw):
        cfg, eid, rec = self.exp(*args, **kw)
        runs.start(cfg, rec, detach=False)
        return cfg, eid, rec

    def mocked_tmp(self):
        """Patch runs.tempfile.mkdtemp so the reconstructed tree lands in self.tmp."""
        created = []
        real_mkdtemp = _tf.mkdtemp  # capture before the patch replaces it

        def fake(prefix="x"):
            d = real_mkdtemp(prefix=prefix, dir=self.tmp)
            created.append(d)
            return d

        return mock.patch.object(runs.tempfile, "mkdtemp", fake), created


class TestReproduces(RerunBase):
    def test_identical(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        self.push(repo)
        cfg, eid, rec = self.run_exp(kb, "#!/bin/sh\necho fixed > results/out.txt\n")
        self.assertEqual(runs._load_provenance(rec)["reconstructable"], "yes")
        code, lines = runs.rerun(cfg, eid)
        self.assertEqual(code, 0, "\n".join(lines))
        self.assertTrue(any("reproduced: yes" in ln for ln in lines), lines)
        # A rerun record exists with rerun_of pointing back.
        rr = [e for e in scan.experiment_ids(cfg) if e.endswith("-rerun")]
        self.assertEqual(len(rr), 1)
        _id, rr_dir = scan.find_experiment(cfg, rr[0])
        result = json.load(open(os.path.join(rr_dir, "rerun.json")))
        self.assertEqual(result["reproduced"], "yes")
        self.assertEqual(result["outputs"][0]["verdict"], "identical")
        self.assertEqual(
            records.get_field(records.read(os.path.join(rr_dir, "README.md")), "rerun_of"), eid
        )

    def test_different(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        self.push(repo)
        # $$ (the shell pid) differs between the two separate run.sh processes.
        cfg, eid, rec = self.run_exp(kb, "#!/bin/sh\necho $$ > results/out.txt\n")
        code, lines = runs.rerun(cfg, eid)
        self.assertEqual(code, 0, "\n".join(lines))
        rr = [e for e in scan.experiment_ids(cfg) if e.endswith("-rerun")][0]
        _id, rr_dir = scan.find_experiment(cfg, rr)
        result = json.load(open(os.path.join(rr_dir, "rerun.json")))
        self.assertEqual(result["outputs"][0]["verdict"], "different")
        self.assertEqual(result["reproduced"], "no")

    def test_equivalent_via_compare_sh(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        self.push(repo)
        cfg, eid, rec = self.run_exp(kb, "#!/bin/sh\necho $$ > results/out.txt\n")
        # An executable compare.sh in the original record that accepts anything.
        cmp_path = os.path.join(rec, "compare.sh")
        with open(cmp_path, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(cmp_path, 0o755)
        code, lines = runs.rerun(cfg, eid)
        self.assertEqual(code, 0, "\n".join(lines))
        rr = [e for e in scan.experiment_ids(cfg) if e.endswith("-rerun")][0]
        _id, rr_dir = scan.find_experiment(cfg, rr)
        result = json.load(open(os.path.join(rr_dir, "rerun.json")))
        self.assertEqual(result["outputs"][0]["verdict"], "equivalent")
        self.assertEqual(result["reproduced"], "yes")


class TestRefusals(RerunBase):
    def test_reconstructable_no_refuses_then_force_overrides(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)  # no remote => not pushed => reconstructable: no
        cfg, eid, rec = self.run_exp(kb, "#!/bin/sh\necho fixed > results/out.txt\n")
        self.assertEqual(runs._load_provenance(rec)["reconstructable"], "no")
        code, lines = runs.rerun(cfg, eid, force=False)
        self.assertEqual(code, 1)
        self.assertTrue(any("reconstructable: no" in ln for ln in lines), lines)
        self.assertFalse(any(e.endswith("-rerun") for e in scan.experiment_ids(cfg)))
        # --force overrides and completes.
        code2, lines2 = runs.rerun(cfg, eid, force=True)
        self.assertEqual(code2, 0, "\n".join(lines2))
        self.assertTrue(any(e.endswith("-rerun") for e in scan.experiment_ids(cfg)))

    def test_changed_input_refuses_even_with_force(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        self.push(repo)
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as fh:
            fh.write("v0\n")
        cfg, eid, rec = self.run_exp(
            kb, "#!/bin/sh\necho fixed > results/out.txt\n", inputs=data + "\n"
        )
        # The input changes after the record: a rerun on different data is a new experiment.
        with open(data, "w") as fh:
            fh.write("v1 changed longer\n")
        for force in (False, True):
            code, lines = runs.rerun(cfg, eid, force=force)
            self.assertEqual(code, 1, "\n".join(lines))
            self.assertTrue(any("changed since the record" in ln for ln in lines), lines)
        self.assertFalse(any(e.endswith("-rerun") for e in scan.experiment_ids(cfg)))


class TestTreeIsolationAndTemp(RerunBase):
    def _dirty_repo_exp(self, kb):
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo, filename="code.txt", content="v1\n")
        self.push(repo)
        # Make it dirty: v1 -> v2, uncommitted. The dirty patch must recreate v2.
        with open(os.path.join(repo, "code.txt"), "w") as fh:
            fh.write("v2\n")
        run_sh = '#!/bin/sh\ncat "$SLATE_REPOS_ROOT/repoA/code.txt" > results/out.txt\n'
        cfg, eid, rec = self.run_exp(kb, run_sh)
        return cfg, eid, rec, repo

    def test_original_tree_untouched_and_dirty_patch_applied(self):
        kb = self.make_kb()
        cfg, eid, rec, repo = self._dirty_repo_exp(kb)
        before_status = self.git(repo, "status", "--porcelain").stdout
        before_head = self.head(repo)
        patcher, created = self.mocked_tmp()
        with patcher:
            code, lines = runs.rerun(cfg, eid, keep=True)
        self.assertEqual(code, 0, "\n".join(lines))
        # The original working tree is byte-for-byte unchanged.
        self.assertEqual(self.git(repo, "status", "--porcelain").stdout, before_status)
        self.assertEqual(self.head(repo), before_head)
        with open(os.path.join(repo, "code.txt")) as fh:
            self.assertEqual(fh.read(), "v2\n")  # still the dirty original
        # The reconstructed tree got the dirty patch applied (v1 -> v2).
        recon = os.path.join(created[0], "repoA", "code.txt")
        self.assertTrue(os.path.isfile(recon))
        with open(recon) as fh:
            self.assertEqual(fh.read(), "v2\n")
        # The reproduction was identical (v2 in both places).
        rr = [e for e in scan.experiment_ids(cfg) if e.endswith("-rerun")][0]
        _id, rr_dir = scan.find_experiment(cfg, rr)
        self.assertEqual(json.load(open(os.path.join(rr_dir, "rerun.json")))["reproduced"], "yes")

    def test_temp_tree_removed_without_keep_and_kept_with_keep(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        self.push(repo)
        cfg, eid, rec = self.run_exp(kb, "#!/bin/sh\necho fixed > results/out.txt\n")

        patcher, created = self.mocked_tmp()
        with patcher:
            runs.rerun(cfg, eid, keep=False)
        self.assertEqual(len(created), 1)
        self.assertFalse(os.path.exists(created[0]))  # removed

        patcher2, created2 = self.mocked_tmp()
        with patcher2:
            runs.rerun(cfg, eid, keep=True)
        self.assertTrue(os.path.isdir(created2[0]))  # kept

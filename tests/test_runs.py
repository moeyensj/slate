"""exp start (waiting + detached), harvest settlement, and plan_commit."""

from __future__ import annotations

import json
import os
import re
import time

from harness import Base
from slate import arcs, records, runs, tracker

MODIFY_INPUT = (
    "#!/bin/sh\nprintf 'changed and longer content\\n' > {}\necho out > results/out.txt\n"
)


def _json(path):
    with open(path) as fh:
        return json.load(fh)


def _strip_markers(path):
    text = records.read(path)
    text = re.sub(r"<!-- slate:fill.*?-->", "done", text, flags=re.DOTALL)
    text = re.sub(r"<!-- slate:after.*?-->", "done", text, flags=re.DOTALL)
    records.write(path, text)


class RunBase(Base):
    def build_exp(
        self, kb, run_sh, outputs="results/out.txt\n", arc="a", slug="x", inputs="none\n"
    ):
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, arc, "Arc title", "Do the thing.")
        exp_id, rec, _ = runs.new(cfg, trk, arc, slug, "Title", hypothesis="faster is better")
        _strip_markers(os.path.join(rec, "README.md"))
        with open(os.path.join(rec, "run.sh"), "w") as fh:
            fh.write(run_sh)
        os.chmod(os.path.join(rec, "run.sh"), 0o755)
        with open(os.path.join(rec, "outputs.txt"), "w") as fh:
            fh.write(outputs)
        with open(os.path.join(rec, "inputs.txt"), "w") as fh:
            fh.write(inputs)
        return cfg, exp_id, rec

    def wait_for(self, path, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(path):
                return True
            time.sleep(0.05)
        return False


class TestWaitingAndHarvest(RunBase):
    def test_done_when_exit0_and_outputs_fresh(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        code, _ = runs.start(cfg, rec, detach=False)
        self.assertEqual(code, 0)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "done"
        )
        outcome = _json(os.path.join(rec, "outcome.json"))
        self.assertEqual(outcome["status"], "done")
        self.assertEqual(outcome["exit_code"], 0)

    def test_failed_on_nonzero_exit(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\nexit 7\n")
        runs.start(cfg, rec, detach=False)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "failed"
        )

    def test_failed_on_missing_output(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho hi\n")  # never writes the output
        runs.start(cfg, rec, detach=False)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "failed"
        )

    def test_failed_on_empty_output(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\n: > results/out.txt\n")  # empty file
        runs.start(cfg, rec, detach=False)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "failed"
        )

    def test_failed_on_stale_output(self):
        kb = self.make_kb()
        # Pre-create the output; run.sh does not touch it, so it is older than started.
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho hi\n")
        pre = os.path.join(rec, "results", "out.txt")
        with open(pre, "w") as fh:
            fh.write("old\n")
        old = time.time() - 3600
        os.utime(pre, (old, old))
        runs.start(cfg, rec, detach=False)
        outcome = _json(os.path.join(rec, "outcome.json"))
        detail = outcome["outputs"][0]
        self.assertTrue(detail["files"][0]["stale"])
        self.assertEqual(outcome["status"], "failed")

    def test_lost_when_pid_dead_and_no_outcome(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho hi\n")
        records.apply_update(
            os.path.join(rec, "README.md"), {"status": "running", "started": "2026-09-18T00:00:00Z"}
        )
        with open(os.path.join(rec, "run.json"), "w") as fh:
            json.dump({"pid": 999999, "start_epoch": 1.0, "host": "h", "log": "x"}, fh)
        rows = runs.harvest(cfg, eid)
        self.assertTrue(any("lost" in r for r in rows), rows)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "lost"
        )


class TestDetach(RunBase):
    def test_detach_returns_before_run_and_settles_later(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\nsleep 1\necho data > results/out.txt\n")
        code, lines = runs.start(cfg, rec, detach=True)
        self.assertEqual(code, 0)
        self.assertTrue(any("detached" in ln for ln in lines))
        # It should not be finished yet.
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "running"
        )
        self.assertTrue(self.wait_for(os.path.join(rec, "outcome.json"), timeout=6.0))
        runs.harvest(cfg, eid)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "done"
        )


class TestPlanCommit(RunBase):
    def test_sha_when_committed_null_when_modified(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        # Commit the whole KB so the plan README is committed and unmodified.
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "plan")
        code, lines = runs.start(cfg, rec, detach=False)
        prov = _json(os.path.join(rec, "provenance.json"))
        self.assertIsNotNone(prov["plan_commit"])
        self.assertRegex(prov["plan_commit"], r"^[0-9a-f]{40}$")
        self.assertFalse(any("plan is not committed" in ln for ln in lines))

    def test_null_when_uncommitted(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "plan")
        # Modify the plan README after committing.
        with open(os.path.join(rec, "README.md"), "a") as fh:
            fh.write("\nmodified after commit\n")
        code, lines = runs.start(cfg, rec, detach=False)
        prov = _json(os.path.join(rec, "provenance.json"))
        self.assertIsNone(prov["plan_commit"])
        self.assertTrue(any("plan is not committed" in ln for ln in lines))


class TestStartProvenance(RunBase):
    def test_records_run_sh_hash_and_working_dir(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        runs.start(cfg, rec, detach=False)
        prov = _json(os.path.join(rec, "provenance.json"))
        self.assertRegex(prov["run_sh_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(prov["working_dir"], os.path.abspath(rec))


class TestInputMutation(RunBase):
    def test_input_mutated_during_run(self):
        kb = self.make_kb()
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as fh:
            fh.write("v0\n")
        cfg, eid, rec = self.build_exp(kb, MODIFY_INPUT.format(data), inputs=data + "\n")
        runs.start(cfg, rec, detach=False)
        outcome = _json(os.path.join(rec, "outcome.json"))
        self.assertIn(data, outcome["inputs_mutated"])
        prov = _json(os.path.join(rec, "provenance.json"))
        self.assertEqual(prov["reconstructable"], "no")
        self.assertTrue(
            any("input mutated during run" in r for r in prov["reconstructable_reasons"])
        )

    def test_unmodified_input_not_flagged(self):
        kb = self.make_kb()
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as fh:
            fh.write("v0\n")
        cfg, eid, rec = self.build_exp(
            kb, "#!/bin/sh\necho out > results/out.txt\n", inputs=data + "\n"
        )
        runs.start(cfg, rec, detach=False)
        outcome = _json(os.path.join(rec, "outcome.json"))
        self.assertEqual(outcome["inputs_mutated"], [])


class TestConclude(RunBase):
    def _done(self, kb):
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        runs.start(cfg, rec, detach=False)
        return cfg, tracker.build(cfg), eid, rec

    def test_requires_nonempty_conclusion(self):
        cfg, trk, eid, _ = self._done(self.make_kb())
        with self.assertRaises(records.GateError):
            runs.conclude(cfg, trk, eid, "confirmed", "")

    def test_rejects_overlong_conclusion(self):
        cfg, trk, eid, _ = self._done(self.make_kb())
        with self.assertRaises(records.GateError):
            runs.conclude(cfg, trk, eid, "confirmed", "x" * 301)

    def test_stores_outcome_and_conclusion(self):
        cfg, trk, eid, rec = self._done(self.make_kb())
        runs.conclude(cfg, trk, eid, "confirmed", "It was indeed faster.")
        text = records.read(os.path.join(rec, "README.md"))
        self.assertEqual(records.get_field(text, "outcome"), "confirmed")
        self.assertEqual(records.get_field(text, "conclusion"), "It was indeed faster.")

    def test_cli_missing_conclusion_is_usage_error(self):
        kb = self.make_kb()
        cfg, trk, eid, _ = self._done(kb)
        with self.assertRaises(SystemExit) as cm:
            self.cli_run("exp", "conclude", eid, "--outcome", "confirmed", "--kb", kb)
        self.assertEqual(cm.exception.code, 2)

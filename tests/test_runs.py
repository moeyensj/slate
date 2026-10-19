"""exp start (waiting + detached), harvest settlement, and plan_commit."""

from __future__ import annotations

import json
import os
import re
import time

from harness import Base
from slate import arcs, records, runs, tracker, util

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
            json.dump({"pid": 999999, "start_epoch": 1.0, "host": util.host(), "log": "x"}, fh)
        rows = runs.harvest(cfg, eid)
        self.assertTrue(any("lost" in r for r in rows), rows)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "lost"
        )

    def test_not_lost_from_another_host(self):
        # The pid lives on the host that started the run; from any other machine
        # that shares the knowledge base the record is reported, never rewritten.
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho hi\n")
        records.apply_update(
            os.path.join(rec, "README.md"), {"status": "running", "started": "2026-09-18T00:00:00Z"}
        )
        with open(os.path.join(rec, "run.json"), "w") as fh:
            json.dump({"pid": 999999, "start_epoch": 1.0, "host": "elsewhere", "log": "x"}, fh)
        before = records.read(os.path.join(rec, "README.md"))
        rows = runs.harvest(cfg, eid)
        self.assertEqual(len(rows), 1)
        self.assertIn(f"{eid}  running on elsewhere: cannot be checked from here", rows[0])
        self.assertIn("--host-gone", rows[0])  # the way out when that host is gone
        self.assertNotIn("lost", rows[0])
        self.assertEqual(records.read(os.path.join(rec, "README.md")), before)

    def test_lost_when_no_host_was_recorded(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho hi\n")
        records.apply_update(
            os.path.join(rec, "README.md"), {"status": "running", "started": "2026-09-18T00:00:00Z"}
        )
        with open(os.path.join(rec, "run.json"), "w") as fh:
            json.dump({"pid": 999999, "start_epoch": 1.0, "log": "x"}, fh)  # a record by hand
        rows = runs.harvest(cfg, eid)
        self.assertIn("lost", rows[0])

    def _finished_elsewhere(self, kb, inputs="none\n"):
        cfg, eid, rec = self.build_exp(
            kb, "#!/bin/sh\necho data > results/out.txt\n", inputs=inputs
        )
        runs.start(cfg, rec, detach=False)  # writes outcome.json and settles
        records.apply_update(os.path.join(rec, "README.md"), {"status": "running"})
        run_json = _json(os.path.join(rec, "run.json"))
        run_json["host"] = "elsewhere"
        with open(os.path.join(rec, "run.json"), "w") as fh:
            json.dump(run_json, fh)
        return cfg, eid, rec

    def test_left_alone_from_another_host_when_an_output_is_not_visible(self):
        # The knowledge base was pushed with the outcome but the outputs stayed
        # on the other machine: settling here would call the run failed.
        kb = self.make_kb()
        cfg, eid, rec = self._finished_elsewhere(kb)
        os.remove(os.path.join(rec, "results", "out.txt"))
        before = {
            name: records.read(os.path.join(rec, name))
            for name in ("README.md", "outcome.json", "provenance.json")
        }
        rows = runs.harvest(cfg, eid)
        self.assertIn(f"{eid}  finished on elsewhere: not every input and output", rows[0])
        for name, text in before.items():
            self.assertEqual(records.read(os.path.join(rec, name)), text)

    def test_left_alone_from_another_host_when_an_input_is_not_visible(self):
        # Settling here would record the absent input as mutated during the run.
        kb = self.make_kb()
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as fh:
            fh.write("v0\n")
        cfg, eid, rec = self._finished_elsewhere(kb, inputs=data + "\n")
        os.remove(data)
        rows = runs.harvest(cfg, eid)
        self.assertIn("finished on elsewhere", rows[0])
        prov = _json(os.path.join(rec, "provenance.json"))
        self.assertNotIn("input mutated during run: " + data, prov["reconstructable_reasons"])
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "running"
        )

    def test_left_alone_from_another_host_when_a_directory_output_is_empty_here(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(
            kb,
            "#!/bin/sh\nmkdir -p results/out\necho a > results/out/a.txt\n",
            outputs="results/out\n",
        )
        runs.start(cfg, rec, detach=False)
        records.apply_update(os.path.join(rec, "README.md"), {"status": "running"})
        run_json = _json(os.path.join(rec, "run.json"))
        run_json["host"] = "elsewhere"
        with open(os.path.join(rec, "run.json"), "w") as fh:
            json.dump(run_json, fh)
        os.remove(os.path.join(rec, "results", "out", "a.txt"))  # the directory synced, empty
        rows = runs.harvest(cfg, eid)
        self.assertIn("finished on elsewhere", rows[0])
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "running"
        )

    def test_host_gone_settles_a_run_without_an_outcome_as_lost(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho hi\n")
        records.apply_update(
            os.path.join(rec, "README.md"), {"status": "running", "started": "2026-09-18T00:00:00Z"}
        )
        with open(os.path.join(rec, "run.json"), "w") as fh:  # a live pid here means nothing
            json.dump({"pid": os.getpid(), "start_epoch": 1.0, "host": "elsewhere", "log": "x"}, fh)
        rows = runs.harvest(cfg, eid, host_gone=True)
        self.assertIn(f"{eid}  lost  (no outcome; settled here, elsewhere is gone)", rows[0])
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "lost"
        )

    def test_host_gone_settles_a_finished_run_against_what_is_visible(self):
        kb = self.make_kb()
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as fh:
            fh.write("v0\n")
        cfg, eid, rec = self._finished_elsewhere(kb, inputs=data + "\n")
        os.remove(data)  # the input stayed on the other machine
        r = self.cli_run("exp", "harvest", eid, "--host-gone", "--kb", kb)
        self.assertEqual(r.code, 0, r.err)
        self.assertIn("done", r.out)
        self.assertIn("inputs_unchecked=1", r.out)
        outcome = _json(os.path.join(rec, "outcome.json"))
        self.assertEqual(outcome["settled_away_from"], "elsewhere")
        self.assertEqual(outcome["inputs_unchecked"], [data])
        self.assertEqual(outcome["inputs_mutated"], [])  # unseen is not mutated
        prov = _json(os.path.join(rec, "provenance.json"))
        self.assertNotIn("input mutated during run: " + data, prov["reconstructable_reasons"])
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "done"
        )

    def test_host_gone_fails_a_finished_run_whose_output_is_missing(self):
        kb = self.make_kb()
        cfg, eid, rec = self._finished_elsewhere(kb)
        os.remove(os.path.join(rec, "results", "out.txt"))
        rows = runs.harvest(cfg, eid, host_gone=True)
        self.assertIn("failed", rows[0])
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "failed"
        )

    def test_settled_from_another_host_once_the_outcome_is_written(self):
        # A finished run is settled wherever its outputs can be read (a cluster's
        # login nodes share one file system under different host names).
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        runs.start(cfg, rec, detach=False)  # writes outcome.json and settles
        records.apply_update(os.path.join(rec, "README.md"), {"status": "running"})
        run_json = _json(os.path.join(rec, "run.json"))
        run_json["host"] = "elsewhere"
        with open(os.path.join(rec, "run.json"), "w") as fh:
            json.dump(run_json, fh)
        rows = runs.harvest(cfg, eid)
        self.assertIn("done", rows[0])
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "done"
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


class TestDirectoryOutput(RunBase):
    def _status(self, rec):
        return records.get_field(records.read(os.path.join(rec, "README.md")), "status")

    def test_directory_output_verified_file_by_file(self):
        kb = self.make_kb()
        run_sh = (
            "#!/bin/sh\nmkdir -p results/out/sub\n"
            "echo a > results/out/a.txt\necho b > results/out/sub/b.txt\n"
        )
        cfg, eid, rec = self.build_exp(kb, run_sh, outputs="results/out\n")
        runs.start(cfg, rec, detach=False)
        self.assertEqual(self._status(rec), "done")
        detail = _json(os.path.join(rec, "outcome.json"))["outputs"][0]
        self.assertEqual(detail["output"], "results/out")
        self.assertEqual(len(detail["files"]), 2)  # nested file included
        self.assertTrue(all(f.get("abspath") for f in detail["files"]))
        self.assertRegex(detail["tree_hash"], r"^[0-9a-f]{64}$")

    def test_empty_directory_output_fails(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(
            kb, "#!/bin/sh\nmkdir -p results/out\n", outputs="results/out\n"
        )
        runs.start(cfg, rec, detach=False)
        self.assertEqual(self._status(rec), "failed")  # a directory output needs >=1 file

    def test_empty_file_in_directory_output_fails(self):
        kb = self.make_kb()
        run_sh = (
            "#!/bin/sh\nmkdir -p results/out\necho ok > results/out/a.txt\n: > results/out/e.txt\n"
        )
        cfg, eid, rec = self.build_exp(kb, run_sh, outputs="results/out\n")
        runs.start(cfg, rec, detach=False)
        self.assertEqual(self._status(rec), "failed")

    def test_stale_file_in_directory_output_fails(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(
            kb,
            "#!/bin/sh\nmkdir -p results/out\necho fresh > results/out/new.txt\n",
            outputs="results/out\n",
        )
        os.makedirs(os.path.join(rec, "results", "out"), exist_ok=True)
        stale = os.path.join(rec, "results", "out", "old.txt")
        with open(stale, "w") as fh:
            fh.write("old\n")
        old = time.time() - 3600
        os.utime(stale, (old, old))
        runs.start(cfg, rec, detach=False)
        self.assertEqual(self._status(rec), "failed")


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

    def test_frontmatter_and_harvest_line_when_committed(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "plan")
        runs.start(cfg, rec, detach=False)
        sha = records.get_field(records.read(os.path.join(rec, "README.md")), "plan_commit")
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        rows = runs.harvest(cfg, eid)  # a settled experiment carries the plan line
        self.assertTrue(any(f"plan committed as {sha}" in r for r in rows), rows)

    def test_frontmatter_empty_and_harvest_line_when_uncommitted(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        runs.start(cfg, rec, detach=False)  # the KB is not committed
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "plan_commit"), ""
        )
        rows = runs.harvest(cfg, eid)
        self.assertTrue(any("plan was not committed before the run" in r for r in rows), rows)


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


class TestRecheckInputs(RunBase):
    def test_harvest_holds_a_touched_input_to_its_hash_whatever_the_cap(self):
        kb = self.make_kb()
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as fh:
            fh.write("v0\n")
        cfg, eid, rec = self.build_exp(
            kb, "#!/bin/sh\necho out > results/out.txt\n", inputs=data + "\n"
        )
        runs.start(cfg, rec, detach=True)  # recorded with a hash, under the default cap
        self.assertTrue(self.wait_for(os.path.join(rec, "outcome.json"), timeout=6.0))
        old = time.time() - 3600
        os.utime(data, (old, old))  # the mtime moved; the bytes did not
        low = self.cfg(kb)
        low.max_hash_mb = 0  # harvested under a cap that excludes the file
        rows = runs.harvest(low, eid)
        self.assertIn("done", rows[0])
        self.assertNotIn("inputs_mutated", rows[0])
        self.assertEqual(_json(os.path.join(rec, "outcome.json"))["inputs_mutated"], [])

    def _prov_for(self, kb, data):
        cfg, eid, rec = self.build_exp(
            kb, "#!/bin/sh\necho out > results/out.txt\n", inputs=data + "\n"
        )
        runs.check(cfg, rec)
        return runs._load_provenance(rec)

    def test_touched_input_with_the_same_bytes_is_not_mutated(self):
        kb = self.make_kb()
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as fh:
            fh.write("v0\n")
        prov = self._prov_for(kb, data)
        old = time.time() - 3600
        os.utime(data, (old, old))  # the mtime moved; the hash says the bytes did not
        self.assertEqual(runs._recheck_inputs(prov), [])
        with open(data, "w") as fh:
            fh.write("v1\n")  # same length, different bytes
        self.assertEqual(runs._recheck_inputs(prov), [data])

    def test_unhashed_input_counts_as_mutated_once_its_mtime_moves(self):
        kb = self.make_kb(extra_toml="\n[experiment]\npreserve_kb = 0\nmax_hash_mb = 0\n")
        data = os.path.join(self.tmp, "data.txt")
        with open(data, "w") as fh:
            fh.write("v0\n")
        prov = self._prov_for(kb, data)
        self.assertIsNone(prov["inputs"][0]["sha256"])
        self.assertEqual(runs._recheck_inputs(prov), [])
        old = time.time() - 3600
        os.utime(data, (old, old))  # nothing recorded can show the bytes are the same
        self.assertEqual(runs._recheck_inputs(prov), [data])


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

    def test_cli_prints_uncommitted_plan_line(self):
        kb = self.make_kb()
        cfg, trk, eid, _ = self._done(kb)  # KB not committed => plan not committed
        r = self.cli_run(
            "exp", "conclude", eid, "--outcome", "confirmed", "--conclusion", "done.", "--kb", kb
        )
        self.assertEqual(r.code, 0, r.err)
        self.assertIn("plan was not committed before the run", r.out)

    def test_cli_prints_committed_plan_line(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho data > results/out.txt\n")
        self.git(kb, "init", "-b", "main")
        self.git_commit(kb, "add", "-A")
        self.git_commit(kb, "commit", "-m", "plan")
        runs.start(cfg, rec, detach=False)
        sha = records.get_field(records.read(os.path.join(rec, "README.md")), "plan_commit")
        r = self.cli_run(
            "exp", "conclude", eid, "--outcome", "confirmed", "--conclusion", "ok.", "--kb", kb
        )
        self.assertIn(f"plan committed as {sha}", r.out)

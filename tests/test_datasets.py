"""Datasets: build -> MANIFEST + built, adopt, dataset: inputs, rebuildable,
list/show lineage, and verify. Datasets reuse the experiment run engine."""

from __future__ import annotations

import os
import re

from harness import Base
from slate import datasets, records, runs


def _strip(path):
    text = records.read(path)
    text = re.sub(r"<!-- slate:fill.*?-->", "done", text, flags=re.DOTALL)
    text = re.sub(r"<!-- slate:after.*?-->", "done", text, flags=re.DOTALL)
    records.write(path, text)


class DataBase(Base):
    def build_dataset(self, kb, slug, run_sh, outputs, inputs="none\n"):
        cfg = self.cfg(kb)
        dsid, rec = datasets.new(cfg, slug, "Title of " + slug)
        _strip(os.path.join(rec, "README.md"))
        with open(os.path.join(rec, "run.sh"), "w") as fh:
            fh.write(run_sh)
        os.chmod(os.path.join(rec, "run.sh"), 0o755)
        with open(os.path.join(rec, "outputs.txt"), "w") as fh:
            fh.write(outputs)
        with open(os.path.join(rec, "inputs.txt"), "w") as fh:
            fh.write(inputs)
        return cfg, dsid, rec

    def build_and_run(self, kb, slug="d1", run_sh=None, outputs="results/d.txt\n", inputs="none\n"):
        run_sh = run_sh or "#!/bin/sh\necho payload > results/d.txt\n"
        cfg, dsid, rec = self.build_dataset(kb, slug, run_sh, outputs, inputs)
        runs.start(cfg, rec, detach=False)
        return cfg, dsid, rec


class TestBuild(DataBase):
    def test_build_writes_manifest_and_sets_built(self):
        kb = self.make_kb()
        cfg, dsid, rec = self.build_and_run(kb)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "built"
        )
        man = datasets.read_manifest(rec)
        self.assertIsNotNone(man)
        self.assertEqual(len(man["files"]), 1)
        self.assertRegex(man["files"][0]["sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(man["tree_hash"], r"^[0-9a-f]{64}$")
        self.assertTrue(os.path.isabs(man["files"][0]["path"]))
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "rebuildable"), "yes"
        )


class TestAdopt(DataBase):
    def test_adopt_hashes_files_and_is_not_rebuildable(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        a = os.path.join(self.tmp, "raw_a.txt")
        b = os.path.join(self.tmp, "raw_b.txt")
        for p, body in ((a, "aaa"), (b, "bbbb")):
            with open(p, "w") as fh:
                fh.write(body)
        dsid, rec = datasets.adopt(cfg, "adopted", "Adopted set", [a, b])
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "built"
        )
        self.assertEqual(datasets.rebuildable(cfg, dsid), "no")
        man = datasets.read_manifest(rec)
        self.assertEqual(len(man["files"]), 2)
        sizes = {f["size"] for f in man["files"]}
        self.assertEqual(sizes, {3, 4})


class TestDatasetInput(DataBase):
    def _exp_with_dataset(self, kb, dataset_line, arc="a", slug="x"):
        from slate import arcs, scan, tracker

        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        if arc not in [s for s, _ in scan.arc_dirs(cfg)]:
            arcs.new(cfg, trk, arc, "Arc", "Do it.")
        eid, rec, _ = runs.new(cfg, trk, arc, slug, "Title", hypothesis="h")
        _strip(os.path.join(rec, "README.md"))
        with open(os.path.join(rec, "run.sh"), "w") as fh:
            fh.write("#!/bin/sh\necho o > results/out.txt\n")
        os.chmod(os.path.join(rec, "run.sh"), 0o755)
        with open(os.path.join(rec, "outputs.txt"), "w") as fh:
            fh.write("results/out.txt\n")
        with open(os.path.join(rec, "inputs.txt"), "w") as fh:
            fh.write(dataset_line)
        return cfg, eid, rec

    def test_pass_records_dataset_id_and_manifest_sha(self):
        kb = self.make_kb()
        cfg, dsid, drec = self.build_and_run(kb, slug="src")
        cfg, eid, rec = self._exp_with_dataset(kb, "dataset:src\n")
        ok, _lines = runs.check(cfg, rec)
        self.assertTrue(ok)
        prov = runs._load_provenance(rec)
        entry = [e for e in prov["inputs"] if e.get("kind") == "dataset"][0]
        self.assertEqual(entry["dataset"], "dataset:src")
        self.assertRegex(entry["manifest_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(entry["matches"])

    def test_fail_when_missing(self):
        kb = self.make_kb()
        cfg, eid, rec = self._exp_with_dataset(kb, "dataset:nope\n")
        ok, lines = runs.check(cfg, rec)
        self.assertFalse(ok)
        self.assertTrue(any("dataset input does not exist: nope" in ln for ln in lines), lines)

    def test_fail_when_not_built(self):
        kb = self.make_kb()
        cfg, dsid, drec = self.build_dataset(
            kb, "planned", "#!/bin/sh\necho x > results/d.txt\n", "results/d.txt\n"
        )
        # created but never started -> status planned
        cfg, eid, rec = self._exp_with_dataset(kb, "dataset:planned\n")
        ok, lines = runs.check(cfg, rec)
        self.assertFalse(ok)
        self.assertTrue(any("dataset input is not built: planned" in ln for ln in lines), lines)

    def test_manifest_mismatch_adds_verdict_reason(self):
        kb = self.make_kb()
        cfg, dsid, drec = self.build_and_run(kb, slug="src")
        # Change a file the dataset built, so it no longer matches its manifest.
        with open(os.path.join(drec, "results", "d.txt"), "w") as fh:
            fh.write("tampered and longer\n")
        cfg, eid, rec = self._exp_with_dataset(kb, "dataset:src\n")
        ok, _lines = runs.check(cfg, rec)
        self.assertTrue(ok)  # existence/built are fine; only the verdict degrades
        reasons = runs._load_provenance(rec)["reconstructable_reasons"]
        self.assertIn("dataset no longer matches its manifest: src", reasons)


class TestRebuildable(DataBase):
    def test_rebuildable_via_matching_parent(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        # An adopted parent (rebuildable: no) that still matches its manifest.
        raw = os.path.join(self.tmp, "raw.txt")
        with open(raw, "w") as fh:
            fh.write("seed")
        parent_id, _prec = datasets.adopt(cfg, "parent", "Parent", [raw])
        # A child with a recipe that declares the parent.
        cfg, child_id, crec = self.build_and_run(kb, slug="child", inputs="dataset:parent\n")
        self.assertEqual(datasets.rebuildable(cfg, "parent"), "no")
        self.assertEqual(datasets.rebuildable(cfg, "child"), "yes")

    def test_not_rebuildable_when_parent_diverges(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        raw = os.path.join(self.tmp, "raw.txt")
        with open(raw, "w") as fh:
            fh.write("seed")
        datasets.adopt(cfg, "parent", "Parent", [raw])
        cfg, child_id, crec = self.build_and_run(kb, slug="child", inputs="dataset:parent\n")
        self.assertEqual(datasets.rebuildable(cfg, "child"), "yes")
        # Break the parent's adopted file: it no longer matches its manifest and
        # is not rebuildable, so the child is no longer rebuildable either.
        with open(raw, "w") as fh:
            fh.write("tampered seed longer")
        self.assertEqual(datasets.rebuildable(cfg, "child"), "no")


class TestListShowVerify(DataBase):
    def test_list_columns(self):
        kb = self.make_kb()
        cfg, dsid, rec = self.build_and_run(kb, slug="d1")
        rows = datasets.listing(cfg)
        row = [r for r in rows if r["id"] == "dataset:d1"][0]
        self.assertEqual(row["status"], "built")
        self.assertEqual(row["rebuildable"], "yes")
        self.assertEqual(row["files"], 1)
        self.assertGreater(row["bytes"], 0)
        self.assertEqual(row["used_by"], 0)

    def test_show_lineage_up_and_down(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        raw = os.path.join(self.tmp, "raw.txt")
        with open(raw, "w") as fh:
            fh.write("seed")
        datasets.adopt(cfg, "parent", "Parent", [raw])
        self.build_and_run(kb, slug="child", inputs="dataset:parent\n")
        # An experiment that also uses the child dataset.
        TestDatasetInput._exp_with_dataset(self, kb, "dataset:child\n")

        child = datasets.show(cfg, "child")
        self.assertIn("dataset:parent", child["parents"])
        self.assertTrue(any(d.startswith("a/") for d in child["dependents"]), child["dependents"])

        parent = datasets.show(cfg, "parent")
        self.assertIn("dataset:child", parent["dependents"])
        self.assertEqual(parent["parents"], [])

    def test_verify_exit_1_on_change(self):
        kb = self.make_kb()
        cfg, dsid, rec = self.build_and_run(kb, slug="d1")
        ok, _diffs, _n, _b = datasets.verify(cfg, rec)
        self.assertTrue(ok)
        with open(os.path.join(rec, "results", "d.txt"), "w") as fh:
            fh.write("changed and longer\n")
        r = self.cli_run("data", "verify", "d1", "--kb", kb)
        self.assertEqual(r.code, 1)


class TestDataCliRoundtrip(DataBase):
    def test_new_and_list_via_cli(self):
        kb = self.make_kb()
        cfg, dsid, rec = self.build_and_run(kb, slug="viacli")
        r = self.cli_run("data", "list", "--kb", kb, "--json")
        self.assertEqual(r.code, 0)
        self.assertIn("dataset:viacli", r.out)

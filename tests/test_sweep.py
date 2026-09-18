"""sweep: a dry run deletes nothing; a confirmed sweep deletes only files that
pass every predicate. Each refusal path has a test proving the file survives a
confirmed sweep. cleanup.roots and out_root are always inside the test temp dir.
"""

from __future__ import annotations

import json
import os
import re

from harness import Base
from slate import arcs, datasets, records, runs, scan, tracker


def _strip(path):
    text = records.read(path)
    text = re.sub(r"<!-- slate:fill.*?-->", "done", text, flags=re.DOTALL)
    text = re.sub(r"<!-- slate:after.*?-->", "done", text, flags=re.DOTALL)
    records.write(path, text)


class SweepBase(Base):
    def setUp(self):
        super().setUp()
        self.root = os.path.join(self.tmp, "sweeproot")  # cleanup root, inside temp
        os.makedirs(self.root)
        self.outside = os.path.join(self.tmp, "outside")  # inside temp, outside the root
        os.makedirs(self.outside)

    def sweep_kb(self, max_hash_mb=None, name="kb"):
        extra = f'\n[experiment]\nout_root = "{self.root}/outs"\n'
        if max_hash_mb is not None:
            extra += f"max_hash_mb = {max_hash_mb}\n"
        extra += f'\n[cleanup]\nroots = ["{self.root}"]\n'
        return self.make_kb(name=name, extra_toml=extra)

    def _arc(self, cfg, trk, arc):
        if arc not in [s for s, _ in scan.arc_dirs(cfg)]:
            arcs.new(cfg, trk, arc, "Arc", "Do it.")

    def exp(self, kb, run_sh, outputs, inputs="none\n", arc="a", slug="x", hypothesis="h"):
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        self._arc(cfg, trk, arc)
        eid, rec, _ = runs.new(cfg, trk, arc, slug, "Title", hypothesis=hypothesis)
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

    def dataset(self, kb, slug, run_sh, outputs, inputs="none\n"):
        cfg = self.cfg(kb)
        dsid, rec = datasets.new(cfg, slug, "DS " + slug)
        _strip(os.path.join(rec, "README.md"))
        with open(os.path.join(rec, "run.sh"), "w") as fh:
            fh.write(run_sh)
        os.chmod(os.path.join(rec, "run.sh"), 0o755)
        with open(os.path.join(rec, "outputs.txt"), "w") as fh:
            fh.write(outputs)
        with open(os.path.join(rec, "inputs.txt"), "w") as fh:
            fh.write(inputs)
        runs.start(cfg, rec, detach=False)
        return cfg, dsid, rec

    def _find_item(self, plan, path):
        for rec in plan["records"]:
            for it in rec["items"]:
                if it["path"] == path:
                    return it
        return None


# --------------------------------------------------------------------------
# dry run never deletes; confirmed sweep deletes exactly the planned files
# --------------------------------------------------------------------------


class TestDryRunAndConfirm(SweepBase):
    def test_dry_run_deletes_nothing(self):
        kb = self.sweep_kb()
        cfg, eid, rec = self.run_exp(
            kb, '#!/bin/sh\necho payload > "$SLATE_OUT/a.txt"\n', "$SLATE_OUT/a.txt\n"
        )
        produced = os.path.join(self.root, "outs", eid, "a.txt")
        self.assertTrue(os.path.isfile(produced))
        runs.mark(cfg, eid, "outputs", "stale")
        plan = runs.sweep(cfg, confirm=False)  # dry run
        self.assertTrue(os.path.isfile(produced))  # nothing deleted
        self.assertEqual(self._find_item(plan, produced)["action"], "delete")

    def test_confirm_deletes_only_planned_and_writes_tombstone(self):
        kb = self.sweep_kb()
        # One output under the root (deletable), one outside it (kept).
        run_sh = '#!/bin/sh\necho aaa > "$SLATE_OUT/a.txt"\necho bbb > results/b.txt\n'
        cfg, eid, rec = self.run_exp(kb, run_sh, "$SLATE_OUT/a.txt\nresults/b.txt\n")
        under_root = os.path.join(self.root, "outs", eid, "a.txt")
        outside_root = os.path.join(rec, "results", "b.txt")
        self.assertTrue(os.path.isfile(under_root) and os.path.isfile(outside_root))
        runs.mark(cfg, eid, "outputs", "stale")
        runs.sweep(cfg, confirm=True)
        self.assertFalse(os.path.exists(under_root))  # under root: deleted
        self.assertTrue(os.path.isfile(outside_root))  # outside root: kept
        cleaned = json.load(open(os.path.join(rec, "cleaned.json")))
        self.assertEqual([r["path"] for r in cleaned["sweeps"][0]["removed"]], [under_root])
        text = records.read(os.path.join(rec, "README.md"))
        self.assertEqual(records.get_field(text, "marked"), "")  # mark cleared
        self.assertTrue(records.get_field(text, "cleaned").startswith(cleaned["sweeps"][0]["date"]))
        self.assertIn("outputs 1", records.get_field(text, "cleaned"))


# --------------------------------------------------------------------------
# every refusal path: the file SURVIVES a confirmed sweep
# --------------------------------------------------------------------------


class TestRefusalsSurvive(SweepBase):
    def test_outside_roots(self):
        kb = self.sweep_kb()
        cfg, eid, rec = self.run_exp(kb, "#!/bin/sh\necho x > results/o.txt\n", "results/o.txt\n")
        out = os.path.join(rec, "results", "o.txt")
        runs.mark(cfg, eid, "outputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(self._find_item(plan, out)["reason"], "outside cleanup roots")

    def test_git_tracked(self):
        # A covered repo is a direct child of the repos root; make the cleanup
        # root the repo itself so only the git-tracked predicate can block.
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)  # seed.txt tracked & committed
        kb = self.make_kb(
            extra_toml=(
                f'\n[experiment]\nout_root = "{self.root}/outs"\n\n[cleanup]\nroots = ["{repo}"]\n'
            )
        )
        cfg, eid, rec = self.run_exp(
            kb,
            "#!/bin/sh\necho x > results/out.txt\n",
            "results/out.txt\n",
            inputs=os.path.join(repo, "seed.txt") + "\n",
        )
        tracked = os.path.join(repo, "seed.txt")
        runs.mark(cfg, eid, "inputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(tracked))
        self.assertIn("git-tracked", self._find_item(plan, tracked)["reason"])

    def test_hash_mismatch(self):
        kb = self.sweep_kb()
        cfg, eid, rec = self.run_exp(
            kb, '#!/bin/sh\necho aaa > "$SLATE_OUT/a.txt"\n', "$SLATE_OUT/a.txt\n"
        )
        produced = os.path.join(self.root, "outs", eid, "a.txt")
        with open(produced, "w") as fh:  # tamper after harvest recorded the sha
            fh.write("changed since recorded, longer\n")
        runs.mark(cfg, eid, "outputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(produced))
        self.assertIn("hash changed", self._find_item(plan, produced)["reason"])

    def test_recorded_without_a_hash(self):
        kb = self.sweep_kb(max_hash_mb=0)  # every output is sized, not hashed
        cfg, eid, rec = self.run_exp(
            kb, '#!/bin/sh\necho aaa > "$SLATE_OUT/a.txt"\n', "$SLATE_OUT/a.txt\n"
        )
        produced = os.path.join(self.root, "outs", eid, "a.txt")
        runs.mark(cfg, eid, "outputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(produced))
        self.assertEqual(self._find_item(plan, produced)["reason"], "recorded without a hash")

    def test_input_shared_with_unmarked_experiment(self):
        kb = self.sweep_kb()
        shared = os.path.join(self.root, "shared.dat")
        with open(shared, "w") as fh:
            fh.write("shared payload")
        cfg, x, xrec = self.run_exp(
            kb,
            "#!/bin/sh\necho o > results/out.txt\n",
            "results/out.txt\n",
            inputs=shared + "\n",
            slug="x",
        )
        # A second, unmarked experiment declares the same input.
        self.exp(
            kb,
            "#!/bin/sh\necho o > results/out.txt\n",
            "results/out.txt\n",
            inputs=shared + "\n",
            slug="y",
        )
        runs.mark(cfg, x, "inputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(shared))
        self.assertIn("declared by a/", self._find_item(plan, shared)["reason"])

    def test_input_shared_with_unmarked_dataset(self):
        kb = self.sweep_kb()
        shared = os.path.join(self.root, "shared.dat")
        with open(shared, "w") as fh:
            fh.write("shared payload")
        # A dataset declares the file as a source input (and is unmarked).
        self.dataset(
            kb,
            "src",
            "#!/bin/sh\necho d > results/d.txt\n",
            "results/d.txt\n",
            inputs=shared + "\n",
        )
        cfg, x, xrec = self.run_exp(
            kb,
            "#!/bin/sh\necho o > results/out.txt\n",
            "results/out.txt\n",
            inputs=shared + "\n",
            slug="x",
        )
        runs.mark(cfg, x, "inputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(shared))
        self.assertIn("declared by dataset:src", self._find_item(plan, shared)["reason"])

    def test_dataset_input_through_experiment_mark(self):
        kb = self.sweep_kb()
        cfg, dsid, drec = self.dataset(
            kb, "foo", '#!/bin/sh\necho d > "$SLATE_OUT/d.txt"\n', "$SLATE_OUT/d.txt\n"
        )
        ds_file = datasets.read_manifest(drec)["files"][0]["path"]
        self.assertTrue(os.path.isfile(ds_file))
        cfg, x, xrec = self.run_exp(
            kb,
            "#!/bin/sh\necho o > results/out.txt\n",
            "results/out.txt\n",
            inputs="dataset:foo\n",
            slug="x",
        )
        runs.mark(cfg, x, "all", "r")
        runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(ds_file))  # the dataset's files are untouched

    def test_dataset_not_rebuildable(self):
        kb = self.sweep_kb()
        cfg = self.cfg(kb)
        raw = os.path.join(self.root, "adopted.dat")  # under the cleanup root
        with open(raw, "w") as fh:
            fh.write("adopted payload")
        dsid, rec = datasets.adopt(cfg, "ad", "Adopted", [raw])
        runs.mark(cfg, dsid, "outputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(raw))  # not rebuildable => nothing deleted
        self.assertTrue(
            any(
                it["reason"] == "dataset not rebuildable"
                for r in plan["records"]
                for it in r["items"]
            )
        )

    def test_dataset_used_by_unmarked_record(self):
        kb = self.sweep_kb()
        cfg, dsid, drec = self.dataset(
            kb, "foo", '#!/bin/sh\necho d > "$SLATE_OUT/d.txt"\n', "$SLATE_OUT/d.txt\n"
        )
        ds_file = datasets.read_manifest(drec)["files"][0]["path"]
        # An unmarked experiment uses the dataset.
        self.exp(
            kb,
            "#!/bin/sh\necho o > results/out.txt\n",
            "results/out.txt\n",
            inputs="dataset:foo\n",
            slug="x",
        )
        runs.mark(cfg, dsid, "outputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(ds_file))
        self.assertTrue(
            any(
                "used by unmarked record" in it["reason"]
                for r in plan["records"]
                for it in r["items"]
            )
        )

    def test_uri_reported_not_deleted(self):
        kb = self.sweep_kb()
        cfg, eid, rec = self.run_exp(
            kb,
            "#!/bin/sh\necho o > results/out.txt\n",
            "results/out.txt\nhttps://example.com/blob\n",
        )
        runs.mark(cfg, eid, "outputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertIn("remote", self._find_item(plan, "https://example.com/blob")["reason"])

    def test_symlink_under_root_pointing_outside_not_followed(self):
        kb = self.sweep_kb()
        target = os.path.join(self.outside, "target.txt")  # outside the cleanup root
        run_sh = f'#!/bin/sh\necho data > "{target}"\nln -sf "{target}" "$SLATE_OUT/link.txt"\n'
        cfg, eid, rec = self.run_exp(kb, run_sh, "$SLATE_OUT/link.txt\n")
        symlink = os.path.join(self.root, "outs", eid, "link.txt")
        self.assertTrue(os.path.islink(symlink))
        runs.mark(cfg, eid, "outputs", "r")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.islink(symlink))  # the symlink survives
        self.assertTrue(os.path.isfile(target))  # so does its target
        self.assertEqual(self._find_item(plan, symlink)["reason"], "outside cleanup roots")


# --------------------------------------------------------------------------
# directory handling: files one by one, empty dir removed, non-empty kept
# --------------------------------------------------------------------------


class TestDirectoryHandling(SweepBase):
    def _dir_exp(self, kb, dirpath):
        # A directory input recorded by check; run then mark --inputs.
        cfg, eid, rec = self.run_exp(
            kb,
            "#!/bin/sh\necho o > results/out.txt\n",
            "results/out.txt\n",
            inputs=dirpath + "\n",
        )
        return cfg, eid, rec

    def test_empty_dir_removed(self):
        kb = self.sweep_kb()
        d = os.path.join(self.root, "bundle")
        os.makedirs(d)
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(name + " body")
        cfg, eid, rec = self._dir_exp(kb, d)
        runs.mark(cfg, eid, "inputs", "r")
        runs.sweep(cfg, confirm=True)
        self.assertFalse(os.path.exists(os.path.join(d, "a.txt")))
        self.assertFalse(os.path.exists(os.path.join(d, "b.txt")))
        self.assertFalse(os.path.exists(d))  # emptied, so removed

    def test_non_empty_dir_kept_and_unlisted_file_survives(self):
        kb = self.sweep_kb()
        d = os.path.join(self.root, "bundle")
        os.makedirs(d)
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(name + " body")
        cfg, eid, rec = self._dir_exp(kb, d)
        # Add a file the record never listed, after the directory was recorded.
        extra = os.path.join(d, "c.txt")
        with open(extra, "w") as fh:
            fh.write("added later")
        runs.mark(cfg, eid, "inputs", "r")
        runs.sweep(cfg, confirm=True)
        self.assertFalse(os.path.exists(os.path.join(d, "a.txt")))  # listed: deleted
        self.assertFalse(os.path.exists(os.path.join(d, "b.txt")))
        self.assertTrue(os.path.isfile(extra))  # unlisted: kept
        self.assertTrue(os.path.isdir(d))  # non-empty: kept


# --------------------------------------------------------------------------
# a rebuildable, unused dataset IS swept (positive path)
# --------------------------------------------------------------------------


class TestDatasetPositive(SweepBase):
    def test_rebuildable_unused_dataset_files_deleted(self):
        kb = self.sweep_kb()
        cfg, dsid, drec = self.dataset(
            kb, "foo", '#!/bin/sh\necho d > "$SLATE_OUT/d.txt"\n', "$SLATE_OUT/d.txt\n"
        )
        ds_file = datasets.read_manifest(drec)["files"][0]["path"]
        self.assertTrue(os.path.isfile(ds_file))
        runs.mark(cfg, dsid, "outputs", "r")
        runs.sweep(cfg, confirm=True)
        self.assertFalse(os.path.exists(ds_file))  # rebuildable, unused => deleted
        self.assertEqual(
            records.get_field(records.read(os.path.join(drec, "README.md")), "marked"), ""
        )


class TestSharedOutputAndToken(SweepBase):
    def _two(self, kb):
        """Experiment A writes a file under the root; experiment B declares it as an input."""
        run_a = '#!/bin/sh\necho aaa > "$SLATE_OUT/a.txt"\n'
        cfg, a_id, a_rec = self.run_exp(kb, run_a, "$SLATE_OUT/a.txt\n", slug="a")
        produced = os.path.join(self.root, "outs", a_id, "a.txt")
        run_b = "#!/bin/sh\necho b > results/b.txt\n"
        cfg, b_id, _ = self.run_exp(kb, run_b, "results/b.txt\n", slug="b", inputs=produced + "\n")
        return cfg, a_id, b_id, produced

    def test_output_that_an_unmarked_record_reads_survives(self):
        kb = self.sweep_kb()
        cfg, a_id, b_id, produced = self._two(kb)
        runs.mark(cfg, a_id, "outputs", "stale")
        plan = runs.sweep(cfg, confirm=True)
        self.assertTrue(os.path.isfile(produced))
        self.assertIn("declared by", self._find_item(plan, produced)["reason"])

    def test_same_output_goes_once_its_reader_is_marked_too(self):
        kb = self.sweep_kb()
        cfg, a_id, b_id, produced = self._two(kb)
        runs.mark(cfg, a_id, "outputs", "stale")
        runs.mark(cfg, b_id, "all", "stale")
        runs.sweep(cfg, confirm=True)
        self.assertFalse(os.path.exists(produced))

    def test_token_gates_the_confirmed_sweep(self):
        kb = self.sweep_kb()
        cfg, a_id, _b, _p = self._two(kb)
        solo = '#!/bin/sh\necho ccc > "$SLATE_OUT/c.txt"\n'
        cfg, c_id, _ = self.run_exp(kb, solo, "$SLATE_OUT/c.txt\n", slug="c")
        target = os.path.join(self.root, "outs", c_id, "c.txt")
        runs.mark(cfg, c_id, "outputs", "stale")
        token = runs.sweep(cfg)["token"]
        refused = runs.sweep_confirmed(cfg, None, "000000000000")
        self.assertTrue(refused.get("refused"))
        self.assertTrue(os.path.isfile(target))  # wrong token: nothing deleted
        # A mark added after the dry run changes the plan, so the old token no longer fits.
        solo2 = '#!/bin/sh\necho ddd > "$SLATE_OUT/d.txt"\n'
        cfg, d_id, _ = self.run_exp(kb, solo2, "$SLATE_OUT/d.txt\n", slug="d")
        runs.mark(cfg, d_id, "outputs", "stale")
        self.assertTrue(runs.sweep_confirmed(cfg, None, token).get("refused"))
        self.assertTrue(os.path.isfile(target))
        done = runs.sweep_confirmed(cfg, None, runs.sweep(cfg)["token"])
        self.assertFalse(done.get("refused"))
        self.assertFalse(os.path.exists(target))

    def test_second_sweep_appends_to_the_tombstone(self):
        kb = self.sweep_kb()
        run_sh = '#!/bin/sh\necho aaa > "$SLATE_OUT/a.txt"\n'
        cfg, eid, rec = self.run_exp(kb, run_sh, "$SLATE_OUT/a.txt\n")
        runs.mark(cfg, eid, "outputs", "one")
        runs.sweep(cfg, confirm=True)
        runs.mark(cfg, eid, "outputs", "two")
        runs.sweep(cfg, confirm=True)
        cleaned = json.load(open(os.path.join(rec, "cleaned.json")))
        self.assertEqual(len(cleaned["sweeps"]), 2)

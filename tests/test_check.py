"""exp check: hard failures, soft reports, dirty patches and arm parity."""

from __future__ import annotations

import json
import os

from harness import Base
from slate import runs


def scaffold(
    record_dir,
    readme="ok\n",
    run_sh="#!/bin/sh\necho hi\n",
    outputs="results/x\n",
    inputs="none\n",
    hypothesis="faster is better",
):
    os.makedirs(os.path.join(record_dir, "results"), exist_ok=True)
    with open(os.path.join(record_dir, "README.md"), "w") as fh:
        fh.write(
            f"---\nslate: experiment\nstatus: planned\nhypothesis: {hypothesis}\n---\n" + readme
        )
    with open(os.path.join(record_dir, "run.sh"), "w") as fh:
        fh.write(run_sh)
    with open(os.path.join(record_dir, "outputs.txt"), "w") as fh:
        fh.write(outputs)
    with open(os.path.join(record_dir, "inputs.txt"), "w") as fh:
        fh.write(inputs)


class TestCheckHard(Base):
    def setUp(self):
        super().setUp()
        self.kb = self.make_kb()
        self.rec = os.path.join(self.kb, "arcs", "a", "experiments", "2026-09-18-x")

    def test_passes_when_filled(self):
        scaffold(self.rec)
        ok, lines = runs.check(self.cfg(self.kb), self.rec)
        self.assertTrue(ok, lines)

    def test_fails_on_fill_marker(self):
        scaffold(self.rec, readme="<!-- slate:fill do this -->\n")
        ok, lines = runs.check(self.cfg(self.kb), self.rec)
        self.assertFalse(ok)
        self.assertTrue(any("slate:fill" in ln for ln in lines))

    def test_fails_on_empty_run_sh(self):
        scaffold(self.rec, run_sh="#!/bin/sh\n# nothing to run\n")
        ok, lines = runs.check(self.cfg(self.kb), self.rec)
        self.assertFalse(ok)
        self.assertTrue(any("run.sh is empty" in ln for ln in lines))

    def test_fails_on_empty_outputs(self):
        scaffold(self.rec, outputs="\n# only a comment\n")
        ok, lines = runs.check(self.cfg(self.kb), self.rec)
        self.assertFalse(ok)
        self.assertTrue(any("outputs.txt is empty" in ln for ln in lines))

    def test_fails_on_empty_hypothesis(self):
        scaffold(self.rec, hypothesis="")
        ok, lines = runs.check(self.cfg(self.kb), self.rec)
        self.assertFalse(ok)
        self.assertTrue(any("hypothesis is empty" in ln for ln in lines))

    def test_fails_on_empty_inputs(self):
        scaffold(self.rec, inputs="\n# just a comment\n")
        ok, lines = runs.check(self.cfg(self.kb), self.rec)
        self.assertFalse(ok)
        self.assertTrue(any("inputs.txt is empty" in ln for ln in lines))

    def test_fails_on_missing_declared_input(self):
        scaffold(self.rec, inputs="/no/such/input/file.dat\n")
        ok, lines = runs.check(self.cfg(self.kb), self.rec)
        self.assertFalse(ok)
        self.assertTrue(any("declared input does not exist" in ln for ln in lines))

    def test_none_inputs_pass(self):
        scaffold(self.rec, inputs="none\n")
        ok, _lines = runs.check(self.cfg(self.kb), self.rec)
        self.assertTrue(ok)

    def test_preflight_pass_and_fail(self):
        kb_ok = self.make_kb(name="kbok", extra_toml='\n[experiment]\npreflight = ["true"]\n')
        rec = os.path.join(kb_ok, "arcs", "a", "experiments", "2026-09-18-x")
        scaffold(rec)
        ok, _ = runs.check(self.cfg(kb_ok), rec)
        self.assertTrue(ok)

        kb_bad = self.make_kb(name="kbbad", extra_toml='\n[experiment]\npreflight = ["false"]\n')
        rec2 = os.path.join(kb_bad, "arcs", "a", "experiments", "2026-09-18-x")
        scaffold(rec2)
        ok2, lines2 = runs.check(self.cfg(kb_bad), rec2)
        self.assertFalse(ok2)
        self.assertTrue(any("preflight" in ln for ln in lines2))


class TestCheckSoft(Base):
    def test_dirty_repo_writes_patch(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        # Make it dirty.
        with open(os.path.join(repo, "seed.txt"), "a") as fh:
            fh.write("dirty change\n")
        rec = os.path.join(kb, "arcs", "a", "experiments", "2026-09-18-x")
        scaffold(rec)
        ok, lines = runs.check(self.cfg(kb), rec)
        self.assertTrue(ok)
        self.assertTrue(any("dirty: repoA" in ln for ln in lines))
        patch = os.path.join(rec, "dirty", "repoA.patch")
        self.assertTrue(os.path.isfile(patch))
        self.assertGreater(os.path.getsize(patch), 0)

    def test_clean_repo_not_reported(self):
        kb = self.make_kb()
        repo = os.path.join(self.tmp, "repoClean")
        self.git_init(repo)
        rec = os.path.join(kb, "arcs", "a", "experiments", "2026-09-18-x")
        scaffold(rec)
        _ok, lines = runs.check(self.cfg(kb), rec)
        self.assertFalse(any("dirty: repoClean" in ln for ln in lines))

    def test_volatile_flagged_and_clean(self):
        kb = self.make_kb()
        rec = os.path.join(kb, "arcs", "a", "experiments", "2026-09-18-x")
        # A relative (non-volatile) output and an output under the fake volatile
        # prefix the harness configures (see harness.make_kb).
        scaffold(rec, outputs="results/x\n/slate-volatile-test/volatile_out\n")
        _ok, lines = runs.check(self.cfg(kb), rec)
        vol = [ln for ln in lines if ln.startswith("volatile output")]
        self.assertEqual(vol, ["volatile output: /slate-volatile-test/volatile_out"])


class TestArmParity(Base):
    """Arm parity is paired from a:/b: prefixed inputs (no config/a, config/b dirs)."""

    def setUp(self):
        super().setUp()
        self.kb = self.make_kb()
        self.rec = os.path.join(self.kb, "arcs", "a", "experiments", "2026-09-18-x")
        scaffold(self.rec)
        self.a = os.path.join(self.rec, "arm_a")
        self.b = os.path.join(self.rec, "arm_b")
        os.makedirs(self.a)
        os.makedirs(self.b)

    def _write(self, arm, name, text):
        with open(os.path.join(getattr(self, arm), name), "w") as fh:
            fh.write(text)

    def _set_inputs(self, lines):
        with open(os.path.join(self.rec, "inputs.txt"), "w") as fh:
            fh.write("\n".join(lines) + "\n")

    def test_toml_json_text_and_missing_file(self):
        # TOML: differing key + key only in a.
        self._write("a", "cfg.toml", "steps = 100\nonly_a = 1\n")
        self._write("b", "cfg.toml", "steps = 200\n")
        # JSON: differing key.
        self._write("a", "cfg.json", '{"mode": "fast"}')
        self._write("b", "cfg.json", '{"mode": "slow"}')
        # Plain text: differing line.
        self._write("a", "notes.txt", "line1\nAAA\n")
        self._write("b", "notes.txt", "line1\nBBB\n")
        # File only in a (no basename partner in b).
        self._write("a", "extra.txt", "x\n")
        self._set_inputs(
            [
                "a: arm_a/cfg.toml",
                "b: arm_b/cfg.toml",
                "a: arm_a/cfg.json",
                "b: arm_b/cfg.json",
                "a: arm_a/notes.txt",
                "b: arm_b/notes.txt",
                "a: arm_a/extra.txt",
            ]
        )
        rows = runs.arm_parity(self.rec)
        joined = "\n".join(rows)
        self.assertTrue(any("cfg.toml: key steps differs" in r for r in rows), joined)
        self.assertTrue(any("cfg.toml: key only_a only in a" in r for r in rows), joined)
        self.assertTrue(any("cfg.json: key mode differs" in r for r in rows), joined)
        self.assertTrue(any("notes.txt: line 2 differs" in r for r in rows), joined)
        self.assertTrue(any("file only in a: extra.txt" in r for r in rows), joined)

    def test_pairs_by_basename_regardless_of_order(self):
        # Same basename in different declared order still pairs.
        self._write("a", "cfg.toml", "steps = 1\n")
        self._write("b", "cfg.toml", "steps = 2\n")
        self._set_inputs(["b: arm_b/cfg.toml", "a: arm_a/cfg.toml"])
        rows = runs.arm_parity(self.rec)
        self.assertTrue(any("cfg.toml: key steps differs" in r for r in rows), rows)

    def test_no_arms_no_rows(self):
        self._set_inputs(["none"])
        self.assertEqual(runs.arm_parity(self.rec), [])


class InputsBase(Base):
    def prep(self, extra_toml=""):
        kb = self.make_kb(extra_toml=extra_toml)
        rec = os.path.join(kb, "arcs", "a", "experiments", "2026-09-18-x")
        scaffold(rec)
        return kb, rec

    def set_inputs(self, rec, *specs):
        with open(os.path.join(rec, "inputs.txt"), "w") as fh:
            fh.write("\n".join(specs) + "\n")

    def prov(self, rec):
        with open(os.path.join(rec, "provenance.json")) as fh:
            return json.load(fh)

    def by_input(self, rec):
        return {e["input"]: e for e in self.prov(rec)["inputs"]}


class TestInputKinds(InputsBase):
    def test_file_directory_uri_none(self):
        kb, rec = self.prep()
        f = os.path.join(self.tmp, "data.bin")
        with open(f, "wb") as fh:
            fh.write(b"hello world")
        d = os.path.join(self.tmp, "adir")
        os.makedirs(d)
        for name in ("one.txt", "two.txt"):
            with open(os.path.join(d, name), "w") as fh:
                fh.write(name)
        self.set_inputs(rec, f, d, "https://example.com/dataset")
        ok, _ = runs.check(self.cfg(kb), rec)
        self.assertTrue(ok)
        entries = self.by_input(rec)
        self.assertEqual(entries[f]["kind"], "file")
        self.assertRegex(entries[f]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(entries[d]["kind"], "directory")
        self.assertRegex(entries[d]["tree_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(len(entries[d]["files"]), 2)
        self.assertEqual(entries["https://example.com/dataset"]["kind"], "uri")

    def test_none_records_no_inputs(self):
        kb, rec = self.prep()
        self.set_inputs(rec, "none")
        runs.check(self.cfg(kb), rec)
        self.assertEqual(self.prov(rec)["inputs"], [])

    def test_directory_tree_hash_stable_under_file_order(self):
        # Two directories with identical content built in different order.
        d1 = os.path.join(self.tmp, "d1")
        d2 = os.path.join(self.tmp, "d2")
        os.makedirs(d1)
        os.makedirs(d2)
        for name, body in (("a.txt", "AAA"), ("b.txt", "BBB")):
            with open(os.path.join(d1, name), "w") as fh:
                fh.write(body)
        for name, body in (("b.txt", "BBB"), ("a.txt", "AAA")):  # reverse creation order
            with open(os.path.join(d2, name), "w") as fh:
                fh.write(body)
        self.assertEqual(runs._dir_tree(d1)[1], runs._dir_tree(d2)[1])


class TestRecoverableAndPreserved(InputsBase):
    def test_recoverable_git(self):
        kb, rec = self.prep()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)  # seed.txt tracked, committed, unmodified
        self.set_inputs(rec, os.path.join(repo, "seed.txt"))
        runs.check(self.cfg(kb), rec)
        entry = self.prov(rec)["inputs"][0]
        self.assertEqual(entry.get("recoverable"), "git")
        self.assertEqual(entry.get("repo"), "repoA")
        self.assertRegex(entry.get("blob"), r"^[0-9a-f]{40}$")
        self.assertNotIn("preserved", entry)  # recoverable, so not copied

    def test_preserved_small_and_too_large_flagged(self):
        kb, rec = self.prep(extra_toml="\n[experiment]\npreserve_kb = 1\nmax_hash_mb = 0\n")
        small = os.path.join(self.tmp, "small.txt")
        with open(small, "w") as fh:
            fh.write("x" * 100)  # <= 1 KB
        big = os.path.join(self.tmp, "big.txt")
        with open(big, "w") as fh:
            fh.write("y" * 5000)  # > 1 KB, and max_hash_mb=0 makes it unhashed
        self.set_inputs(rec, small, big)
        runs.check(self.cfg(kb), rec)
        entries = self.by_input(rec)
        self.assertIn("preserved", entries[small])
        self.assertTrue(os.path.isfile(os.path.join(rec, entries[small]["preserved"])))
        self.assertNotIn("preserved", entries[big])  # too large to preserve
        self.assertTrue(entries[big]["unhashed"])
        reasons = self.prov(rec)["reconstructable_reasons"]
        self.assertIn(f"input not reproducible: {big}", reasons)
        self.assertNotIn(f"input not reproducible: {small}", reasons)  # preserved => safe

    def test_hash_cache_hit_avoids_reread(self):
        kb, rec = self.prep(extra_toml="\n[experiment]\npreserve_kb = 0\n")
        f = os.path.join(self.tmp, "payload.txt")
        with open(f, "w") as fh:
            fh.write("payload contents")
        self.set_inputs(rec, f)
        cfg = self.cfg(kb)
        runs.check(cfg, rec)  # populates the cache
        first = self.by_input(rec)[f]["sha256"]
        # Make the file unreadable: a re-read would raise; a cache hit must not read.
        os.chmod(f, 0)
        self.addCleanup(os.chmod, f, 0o644)
        ok, _ = runs.check(cfg, rec)
        self.assertTrue(ok)
        self.assertEqual(self.by_input(rec)[f]["sha256"], first)


class TestVerdict(InputsBase):
    def _push(self, repo):
        remote = os.path.join(self.tmp, ".remote.git")  # dot => not a covered repo
        os.makedirs(remote)
        self.git(remote, "init", "--bare")
        self.git_commit(repo, "remote", "add", "origin", remote)
        self.git_commit(repo, "push", "origin", "main")

    def test_yes_when_pushed_clean_and_recoverable(self):
        kb, rec = self.prep()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        self._push(repo)
        self.set_inputs(rec, os.path.join(repo, "seed.txt"))
        runs.check(self.cfg(kb), rec)
        prov = self.prov(rec)
        self.assertEqual(prov["reconstructable"], "yes", prov["reconstructable_reasons"])

    def test_no_when_not_pushed(self):
        kb, rec = self.prep()
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)  # no remote => not pushed
        self.set_inputs(rec, "none")
        runs.check(self.cfg(kb), rec)
        prov = self.prov(rec)
        self.assertEqual(prov["reconstructable"], "no")
        self.assertIn("not pushed: repoA", prov["reconstructable_reasons"])

    def test_no_when_input_unhashed_and_unpreserved(self):
        kb, rec = self.prep(extra_toml="\n[experiment]\npreserve_kb = 0\nmax_hash_mb = 0\n")
        f = os.path.join(self.tmp, "orphan.dat")
        with open(f, "w") as fh:
            fh.write("data")
        self.set_inputs(rec, f)
        runs.check(self.cfg(kb), rec)
        prov = self.prov(rec)
        self.assertEqual(prov["reconstructable"], "no")
        self.assertIn(f"input not reproducible: {f}", prov["reconstructable_reasons"])

    def test_no_when_uncommitted_not_fully_preserved(self):
        kb, rec = self.prep(extra_toml="\n[experiment]\npreserve_kb = 1\n")
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        with open(os.path.join(repo, "huge_untracked.dat"), "w") as fh:
            fh.write("z" * 5000)  # untracked, larger than preserve_kb
        self.set_inputs(rec, "none")
        runs.check(self.cfg(kb), rec)
        reasons = self.prov(rec)["reconstructable_reasons"]
        self.assertIn("uncommitted changes not fully preserved: repoA", reasons)

    def test_no_when_volatile_input(self):
        # The volatile check is a prefix test on the declared spec and does not
        # require the file to exist; point it at the harness's fake volatile
        # prefix so the result does not depend on where the temp dir lives.
        kb, rec = self.prep()
        vol = "/slate-volatile-test/input.dat"
        self.set_inputs(rec, vol)
        runs.check(self.cfg(kb), rec)
        reasons = self.prov(rec)["reconstructable_reasons"]
        self.assertIn(f"volatile input: {vol}", reasons)


class TestPreserveIdempotent(Base):
    def test_same_content_is_preserved_once_and_different_content_gets_a_suffix(self):
        import tempfile

        from slate import runs

        record = tempfile.mkdtemp(dir=self.tmp)
        src = os.path.join(self.tmp, "cfg.txt")
        with open(src, "w") as fh:
            fh.write("one\n")
        first = runs._preserve(record, src)
        again = runs._preserve(record, src)  # a repeated check must not duplicate
        self.assertEqual(first, again)
        self.assertEqual(os.listdir(os.path.join(record, "preserved")), ["cfg.txt"])
        with open(src, "w") as fh:
            fh.write("two\n")
        other = runs._preserve(record, src)  # same name, new content: kept apart
        self.assertNotEqual(first, other)

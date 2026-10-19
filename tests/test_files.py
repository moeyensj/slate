"""exp files (states + shared-by count) and exp mark/unmark (warnings)."""

from __future__ import annotations

import json
import os
import re
import shutil

from harness import Base
from slate import arcs, records, runs, scan, tracker, util


def _strip(path):
    text = records.read(path)
    text = re.sub(r"<!-- slate:fill.*?-->", "done", text, flags=re.DOTALL)
    text = re.sub(r"<!-- slate:after.*?-->", "done", text, flags=re.DOTALL)
    records.write(path, text)


class FilesBase(Base):
    def build(self, kb, run_sh, inputs, outputs, arc="a", slug="x"):
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        if arc not in [s for s, _ in scan.arc_dirs(cfg)]:
            arcs.new(cfg, trk, arc, "Arc title", "Do the thing.")
        eid, rec, _ = runs.new(cfg, trk, arc, slug, "Title", hypothesis="faster is better")
        _strip(os.path.join(rec, "README.md"))
        with open(os.path.join(rec, "run.sh"), "w") as fh:
            fh.write(run_sh)
        os.chmod(os.path.join(rec, "run.sh"), 0o755)
        with open(os.path.join(rec, "inputs.txt"), "w") as fh:
            fh.write(inputs)
        with open(os.path.join(rec, "outputs.txt"), "w") as fh:
            fh.write(outputs)
        return cfg, eid, rec


class TestFilesStates(FilesBase):
    def test_states_and_shared_count(self):
        kb = self.make_kb()
        data1 = os.path.join(self.tmp, "data1.txt")
        with open(data1, "w") as fh:
            fh.write("one\n")
        data2 = os.path.join(self.tmp, "data2.txt")
        with open(data2, "w") as fh:
            fh.write("two\n")
        run_sh = (
            f"#!/bin/sh\nprintf 'CHANGED AND LONGER\\n' > {data2}\necho out > results/out.txt\n"
        )
        inputs = "\n".join([data1, data2, "https://example.com/x"]) + "\n"
        outputs = "results/out.txt\nresults/ghost.txt\n"
        cfg, eid, rec = self.build(kb, run_sh, inputs, outputs)
        runs.start(cfg, rec, detach=False)  # modifies data2, writes out.txt, no ghost.txt
        # A second experiment also declares data1 (drives the shared-by count).
        self.build(
            kb, "#!/bin/sh\necho o > results/out.txt\n", data1 + "\n", "results/out.txt\n", slug="y"
        )

        groups = runs.files_view(cfg, given=eid)
        rows = {(r["role"], r["path"]): r for r in groups[0]["rows"]}
        self.assertEqual(rows[("input", data1)]["state"], "present")
        self.assertEqual(rows[("input", data2)]["state"], "changed")
        self.assertEqual(rows[("input", "https://example.com/x")]["state"], "remote")
        self.assertEqual(rows[("output", "results/out.txt")]["state"], "present")
        self.assertEqual(rows[("output", "results/ghost.txt")]["state"], "missing")
        self.assertEqual(rows[("input", data1)]["shared"], 1)  # shared with exp y
        self.assertEqual(rows[("input", data2)]["shared"], 0)
        self.assertEqual(groups[0]["input_total"]["files"], 3)
        self.assertEqual(groups[0]["output_total"]["files"], 2)


class TestVerify(FilesBase):
    def _output_state(self, cfg, eid):
        rows = runs.files_view(cfg, given=eid, verify=True)[0]["rows"]
        return [r for r in rows if r["role"] == "output"][0]["state"]

    def test_directory_output_held_to_what_harvest_recorded(self):
        kb = self.make_kb()
        run_sh = (
            "#!/bin/sh\nmkdir -p results/out\n"
            "echo aaa > results/out/a.txt\necho bbb > results/out/b.txt\n"
        )
        cfg, eid, rec = self.build(kb, run_sh, "none\n", "results/out\n")
        runs.start(cfg, rec, detach=False)
        self.assertEqual(self._output_state(cfg, eid), "present")
        a = os.path.join(rec, "results", "out", "a.txt")
        with open(a, "w") as fh:
            fh.write("aab\n")  # same length: only the hash can tell
        self.assertEqual(self._output_state(cfg, eid), "changed")
        with open(a, "w") as fh:
            fh.write("aaa\n")
        self.assertEqual(self._output_state(cfg, eid), "present")
        with open(os.path.join(rec, "results", "out", "c.txt"), "w") as fh:
            fh.write("new\n")  # a file harvest never saw
        self.assertEqual(self._output_state(cfg, eid), "changed")

    def test_directory_output_verifies_after_the_knowledge_base_moved(self):
        kb = self.make_kb()
        run_sh = "#!/bin/sh\nmkdir -p results/out\necho aaa > results/out/a.txt\n"
        cfg, eid, rec = self.build(kb, run_sh, "none\n", "results/out\n")
        runs.start(cfg, rec, detach=False)
        moved = kb + "-moved"
        shutil.move(kb, moved)  # the record's files are byte-identical, elsewhere
        cfg2 = self.cfg(moved)
        self.assertEqual(self._output_state(cfg2, eid), "present")
        rec2 = os.path.join(moved, os.path.relpath(rec, kb))
        a = os.path.join(rec2, "results", "out", "a.txt")
        with open(a, "w") as fh:
            fh.write("aab\n")
        self.assertEqual(self._output_state(cfg2, eid), "changed")
        # A record written before the path within the directory was kept is
        # held to its tree hash, as before.
        with open(a, "w") as fh:
            fh.write("aaa\n")
        outcome_path = os.path.join(rec2, "outcome.json")
        with open(outcome_path) as fh:
            outcome = json.load(fh)
        for f in outcome["outputs"][0]["files"]:
            del f["relpath"]
        with open(outcome_path, "w") as fh:
            json.dump(outcome, fh)
        self.assertEqual(self._output_state(cfg2, eid), "present")
        with open(a, "w") as fh:
            fh.write("aab\n")
        self.assertEqual(self._output_state(cfg2, eid), "changed")

    def test_recorded_hash_is_checked_whatever_the_cap_is_now(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build(
            kb, "#!/bin/sh\necho o > results/out.txt\n", "none\n", "results/out.txt\n"
        )
        runs.start(cfg, rec, detach=False)  # hashed under the default cap
        low = self.cfg(kb)
        low.max_hash_mb = 0  # a cap lowered after the run must not read as a change
        self.assertEqual(self._output_state(low, eid), "present")
        with open(os.path.join(rec, "results", "out.txt"), "w") as fh:
            fh.write("O\n")  # same length
        self.assertEqual(self._output_state(low, eid), "changed")


class TestMark(FilesBase):
    def _exp(self, kb, slug="x"):
        return self.build(
            kb, "#!/bin/sh\necho o > results/out.txt\n", "none\n", "results/out.txt\n", slug=slug
        )

    def test_mark_shows_scope_and_deletes_nothing(self):
        kb = self.make_kb()
        cfg, eid, rec = self._exp(kb)
        runs.start(cfg, rec, detach=False)
        eid2, warnings = runs.mark(cfg, eid, "outputs", "results are stale")
        self.assertEqual(warnings, [])
        marked = records.get_field(records.read(os.path.join(rec, "README.md")), "marked")
        self.assertTrue(marked.startswith(util.today_str()))
        self.assertIn("outputs", marked)
        # nothing is deleted
        self.assertTrue(os.path.isfile(os.path.join(rec, "results", "out.txt")))
        # the mark appears on the output row, not the (nonexistent) input rows
        rows = runs.files_view(cfg, given=eid)[0]["rows"]
        out_row = [r for r in rows if r["role"] == "output"][0]
        self.assertEqual(out_row["mark"], "outputs")

    def test_unmark_clears(self):
        kb = self.make_kb()
        cfg, eid, rec = self._exp(kb)
        runs.mark(cfg, eid, "all", "reason")
        runs.unmark(cfg, eid)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "marked"), ""
        )

    def test_warns_when_promoted(self):
        kb = self.make_kb()
        cfg, eid, rec = self._exp(kb)
        records.apply_update(os.path.join(rec, "README.md"), {"promoted_to": "design/x.md"})
        _eid, warnings = runs.mark(cfg, eid, "all", "reason")
        self.assertTrue(any("promoted" in w for w in warnings), warnings)

    def test_warns_when_cited(self):
        kb = self.make_kb()
        cfg, eid, rec = self._exp(kb)
        with open(os.path.join(kb, "FINDINGS.md"), "w") as fh:
            fh.write(f"We rely on [exp:{eid}].\n")
        _eid, warnings = runs.mark(cfg, eid, "all", "reason")
        self.assertTrue(any("cited" in w for w in warnings), warnings)

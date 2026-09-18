"""The run environment: SLATE_* variables, $SLATE_OUT expansion, literal-root."""

from __future__ import annotations

import os
import re

from harness import Base
from slate import arcs, records, runs, scan, tracker


def _strip(path):
    text = records.read(path)
    text = re.sub(r"<!-- slate:fill.*?-->", "done", text, flags=re.DOTALL)
    text = re.sub(r"<!-- slate:after.*?-->", "done", text, flags=re.DOTALL)
    records.write(path, text)


class EnvBase(Base):
    def build_exp(self, kb, run_sh, outputs="results/out.txt\n", inputs="none\n", slug="x"):
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        if "a" not in [s for s, _ in scan.arc_dirs(cfg)]:
            arcs.new(cfg, trk, "a", "Arc title", "Do the thing.")
        eid, rec, _ = runs.new(cfg, trk, "a", slug, "Title", hypothesis="faster is better")
        _strip(os.path.join(rec, "README.md"))
        with open(os.path.join(rec, "run.sh"), "w") as fh:
            fh.write(run_sh)
        os.chmod(os.path.join(rec, "run.sh"), 0o755)
        with open(os.path.join(rec, "outputs.txt"), "w") as fh:
            fh.write(outputs)
        with open(os.path.join(rec, "inputs.txt"), "w") as fh:
            fh.write(inputs)
        return cfg, eid, rec


class TestRunEnvVars(EnvBase):
    def test_three_variables_visible_inside_run_sh(self):
        kb = self.make_kb()
        run_sh = (
            "#!/bin/sh\n"
            'printf "%s\\n%s\\n%s\\n" "$SLATE_RECORD" "$SLATE_REPOS_ROOT" "$SLATE_OUT" '
            "> results/env.txt\n"
        )
        cfg, eid, rec = self.build_exp(kb, run_sh, outputs="results/env.txt\n")
        code, _ = runs.start(cfg, rec, detach=False)
        self.assertEqual(code, 0)
        with open(os.path.join(rec, "results", "env.txt")) as fh:
            record_line, repos_line, out_line = [ln.strip() for ln in fh.read().splitlines()[:3]]
        self.assertEqual(record_line, os.path.abspath(rec))
        self.assertEqual(repos_line, cfg.repos_root_dir)
        self.assertEqual(out_line, os.path.abspath(os.path.join(rec, "results")))


class TestSlateOutExpansion(EnvBase):
    def test_expands_without_out_root_to_results(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(
            kb, '#!/bin/sh\necho hi > "$SLATE_OUT/data.txt"\n', outputs="$SLATE_OUT/data.txt\n"
        )
        code, _ = runs.start(cfg, rec, detach=False)
        self.assertEqual(code, 0)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "done"
        )
        self.assertTrue(os.path.isfile(os.path.join(rec, "results", "data.txt")))

    def test_expands_with_out_root_outside_the_record(self):
        out_root = os.path.join(self.tmp, "outs")
        kb = self.make_kb(extra_toml=f'\n[experiment]\nout_root = "{out_root}"\n')
        cfg, eid, rec = self.build_exp(
            kb, '#!/bin/sh\necho hi > "$SLATE_OUT/data.txt"\n', outputs="$SLATE_OUT/data.txt\n"
        )
        code, _ = runs.start(cfg, rec, detach=False)
        self.assertEqual(code, 0)
        self.assertEqual(
            records.get_field(records.read(os.path.join(rec, "README.md")), "status"), "done"
        )
        produced = os.path.join(out_root, eid, "data.txt")
        self.assertTrue(os.path.isfile(produced), produced)
        # It must NOT have landed in the record's own results/ directory.
        self.assertFalse(os.path.isfile(os.path.join(rec, "results", "data.txt")))


class TestLiteralRootWarning(EnvBase):
    def test_reports_literal_repos_root_without_failing(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        run_sh = f"#!/bin/sh\ncd {cfg.repos_root_dir}/somewhere\necho hi > results/out.txt\n"
        cfg, eid, rec = self.build_exp(kb, run_sh)
        ok, lines = runs.check(cfg, rec)
        self.assertTrue(ok)  # a report, never a hard failure
        self.assertTrue(
            any("literal repositories-root path in run.sh" in ln for ln in lines), lines
        )

    def test_no_warning_when_run_sh_is_relative(self):
        kb = self.make_kb()
        cfg, eid, rec = self.build_exp(kb, "#!/bin/sh\necho hi > results/out.txt\n")
        _ok, lines = runs.check(cfg, rec)
        self.assertFalse(any("literal repositories-root path" in ln for ln in lines), lines)

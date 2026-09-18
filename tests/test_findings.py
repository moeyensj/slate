"""findings table and findings check: citations, coverage, warnings, scope."""

from __future__ import annotations

import json
import os

from harness import Base
from slate import arcs, findings, records, runs, scan, tracker


class FindingsBase(Base):
    def concluded(
        self,
        cfg,
        trk,
        arc,
        slug,
        outcome="confirmed",
        conclusion="It held.",
        corrections="None.",
        marked="",
    ):
        if arc not in [s for s, _ in scan.arc_dirs(cfg)]:
            arcs.new(cfg, trk, arc, f"Arc {arc}", "Do the thing.")
        eid, rec, _ = runs.new(cfg, trk, arc, slug, f"Title {slug}", hypothesis=f"hyp {slug}")
        readme = os.path.join(rec, "README.md")
        updates = {"status": "done", "outcome": outcome, "conclusion": conclusion}
        if marked:
            updates["marked"] = marked
        records.apply_update(readme, updates)
        if corrections != "None.":
            text = records.read(readme).replace(
                "## Corrections\n\nNone.", f"## Corrections\n\n{corrections}", 1
            )
            records.write(readme, text)
        return eid, rec

    def write_file(self, kb, rel, body):
        path = os.path.join(kb, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(body)
        return path


class TestTable(FindingsBase):
    def test_lists_only_concluded(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        e1, _ = self.concluded(cfg, trk, "arc", "x", outcome="confirmed", conclusion="c1")
        e2, _ = self.concluded(cfg, trk, "arc", "y", outcome="refuted", conclusion="c2")
        runs.new(cfg, trk, "arc", "z", "Tz", hypothesis="h")  # planned, not concluded
        rows = findings.table(cfg)
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(set(by_id), {e1, e2})
        self.assertEqual(by_id[e1]["conclusion"], "c1")
        self.assertEqual(by_id[e2]["outcome"], "refuted")
        self.assertEqual(by_id[e1]["hypothesis"], "hyp x")


class TestCheck(FindingsBase):
    def test_unresolved_citation_fails(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        e1, _ = self.concluded(cfg, trk, "arc", "x")
        path = self.write_file(
            kb, "FINDINGS.md", f"We saw [exp:{e1}] and [exp:arc/2026-09-18-ghost].\n"
        )
        ok, lines = findings.check_file(cfg, path)
        self.assertFalse(ok)
        self.assertTrue(any("does not resolve" in ln for ln in lines), lines)

    def test_uncovered_concluded_experiment_fails(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        self.concluded(cfg, trk, "arc", "x")
        path = self.write_file(kb, "FINDINGS.md", "A summary that cites nothing.\n")
        ok, lines = findings.check_file(cfg, path)
        self.assertFalse(ok)
        self.assertTrue(any("neither cited nor synthesized" in ln for ln in lines), lines)

    def test_not_yet_synthesized_covers(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        e1, _ = self.concluded(cfg, trk, "arc", "x")
        path = self.write_file(
            kb,
            "FINDINGS.md",
            f"# Findings\n\nNothing synthesized yet.\n\n## Not yet synthesized\n\n- {e1}\n",
        )
        ok, lines = findings.check_file(cfg, path)
        self.assertTrue(ok, lines)

    def test_passes_when_cited(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        e1, _ = self.concluded(cfg, trk, "arc", "x")
        path = self.write_file(kb, "FINDINGS.md", f"The result [exp:{e1}] confirms it.\n")
        ok, lines = findings.check_file(cfg, path)
        self.assertTrue(ok, lines)

    def test_warns_inconclusive_corrections_and_marked(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        e1, _ = self.concluded(
            cfg,
            trk,
            "arc",
            "x",
            outcome="inconclusive",
            corrections="Superseded by a later run.",
            marked="2026-09-18 all cleanup",
        )
        path = self.write_file(kb, "FINDINGS.md", f"See [exp:{e1}].\n")
        ok, lines = findings.check_file(cfg, path)
        self.assertTrue(ok, lines)  # warnings never fail the check
        joined = "\n".join(lines)
        self.assertIn("inconclusive", joined)
        self.assertIn("corrections", joined)
        self.assertIn("marked for deletion", joined)

    def test_warns_aged_evidence(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        repo = os.path.join(self.tmp, "repoA")
        self.git_init(repo)
        sub = os.path.join(repo, "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "file.txt"), "w") as fh:
            fh.write("v0\n")
        self.git_commit(repo, "add", "-A")
        self.git_commit(repo, "commit", "-m", "measured file")
        recorded = self.head(repo)
        with open(os.path.join(sub, "file.txt"), "w") as fh:
            fh.write("v1\n")
        self.git_commit(repo, "commit", "-am", "moved on")

        e1, rec = self.concluded(cfg, trk, "arc", "x")
        records.apply_update(
            os.path.join(rec, "README.md"),
            {"promoted_to": "design/n.md", "measures": "repoA/sub/file.txt"},
        )
        with open(os.path.join(rec, "provenance.json"), "w") as fh:
            json.dump({"repos": {"repoA": {"head": recorded}}, "time": "2026-09-01T00:00:00Z"}, fh)
        path = self.write_file(kb, "FINDINGS.md", f"Based on [exp:{e1}].\n")
        ok, lines = findings.check_file(cfg, path)
        self.assertTrue(ok, lines)
        self.assertTrue(any("aged evidence" in ln for ln in lines), lines)

    def test_arc_scope_vs_kb_scope(self):
        kb = self.make_kb()
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        ea, _ = self.concluded(cfg, trk, "arcA", "x")
        self.concluded(cfg, trk, "arcB", "y")  # a concluded experiment in another arc

        # A FINDINGS.md inside arcA covers only arcA: arcB's experiment is out of scope.
        arc_path = self.write_file(
            kb, os.path.join("arcs", "arcA", "FINDINGS.md"), f"Arc note [exp:{ea}].\n"
        )
        ok_arc, _ = findings.check_file(cfg, arc_path)
        self.assertTrue(ok_arc)

        # The same content at the KB root covers every arc: arcB's experiment is uncovered.
        kb_path = self.write_file(kb, "FINDINGS.md", f"KB note [exp:{ea}].\n")
        ok_kb, lines = findings.check_file(cfg, kb_path)
        self.assertFalse(ok_kb)
        self.assertTrue(any("neither cited nor synthesized" in ln for ln in lines), lines)

"""Findings: the concluded-experiment table and the citation/coverage check.

A findings file is a Markdown summary that cites experiments as
``[exp:<full id>]``. A ``FINDINGS.md`` inside an arc folder covers that arc;
anywhere else in the knowledge base it covers every arc.
"""

from __future__ import annotations

import os
import re

from . import knowledge, records, scan

CITE_RE = re.compile(r"\[exp:([^\]]+)\]")


def _section(text, heading):
    """Return the body of a ``## heading`` section, or ''."""
    pat = re.compile(rf"(?m)^##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)", re.DOTALL)
    m = pat.search(text)
    return m.group(1).strip("\n") if m else ""


def concluded(cfg, arc=None):
    """Return concluded experiments (frontmatter ``outcome`` set) in scope."""
    out = []
    for eid, d in scan.experiment_dirs(cfg, arc):
        text = records.read(os.path.join(d, "README.md"))
        outcome = records.get_field(text, "outcome").strip()
        if not outcome:
            continue
        out.append(
            {
                "id": eid,
                "dir": d,
                "text": text,
                "outcome": outcome,
                "hypothesis": records.get_field(text, "hypothesis"),
                "conclusion": records.get_field(text, "conclusion"),
            }
        )
    return out


def table(cfg, arc=None):
    """Return one row per concluded experiment: id, outcome, hypothesis, conclusion."""
    return [
        {
            "id": e["id"],
            "outcome": e["outcome"],
            "hypothesis": e["hypothesis"],
            "conclusion": e["conclusion"],
        }
        for e in concluded(cfg, arc)
    ]


def citing_files(cfg, eid):
    """Return knowledge-base Markdown files that cite ``[exp:<eid>]``."""
    token = f"[exp:{eid}]"
    hits = []
    for root, dirs, names in os.walk(cfg.root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            try:
                if token in records.read(path):
                    hits.append(path)
            except OSError:
                continue
    return sorted(hits)


def _scope_arc(cfg, abspath):
    """Return the arc a findings file scopes to, or None for whole-KB scope."""
    arcs_dir = os.path.abspath(cfg.arcs_dir)
    rel = os.path.relpath(abspath, arcs_dir)
    if rel.startswith(".."):
        return None
    parts = rel.split(os.sep)
    if len(parts) >= 2 and parts[0] in scan.arc_ids(cfg):
        return parts[0]
    return None


def _aged_ids(cfg):
    """Experiment ids whose promoted, measured paths have moved on (from ``stale``)."""
    aged = set()
    for row in knowledge.stale(cfg):
        if row.get("commits"):
            aged.add(row["experiment"])
    return aged


def check_file(cfg, path):
    """Verify a findings file. Returns (ok, lines).

    Fails on an unresolved citation or a concluded experiment in scope that is
    neither cited nor listed under ``## Not yet synthesized``. Warns, without
    failing, on inconclusive / corrected / aged / marked cited experiments.
    """
    abspath = os.path.abspath(path)
    if not os.path.isfile(abspath):
        alt = os.path.join(cfg.root, path)
        if os.path.isfile(alt):
            abspath = os.path.abspath(alt)
        else:
            raise records.GateError(f"findings file not found: {path}")
    text = records.read(abspath)
    arc = _scope_arc(cfg, abspath)
    all_concluded = {e["id"]: e for e in concluded(cfg)}
    lines = []
    ok = True

    cited_ids = [m.group(1).strip() for m in CITE_RE.finditer(text)]
    for cid in cited_ids:
        if cid not in all_concluded:
            ok = False
            lines.append(f"FAIL: citation does not resolve to a concluded experiment: {cid}")

    not_synth = _section(text, "Not yet synthesized")
    for e in concluded(cfg, arc):
        eid = e["id"]
        leaf = eid.split("/", 1)[1]
        if f"[exp:{eid}]" in text:
            continue
        if eid in not_synth or leaf in not_synth:
            continue
        ok = False
        lines.append(f"FAIL: concluded experiment in scope is neither cited nor synthesized: {eid}")

    aged = _aged_ids(cfg)
    for cid in cited_ids:
        e = all_concluded.get(cid)
        if not e:
            continue
        if e["outcome"] == "inconclusive":
            lines.append(f"warn: cited experiment is inconclusive: {cid}")
        corrections = _section(e["text"], "Corrections").strip()
        if corrections and corrections != "None.":
            lines.append(f"warn: cited experiment has corrections: {cid}")
        if records.get_field(e["text"], "marked").strip():
            lines.append(f"warn: cited experiment is marked for deletion: {cid}")
        if cid in aged:
            lines.append(f"warn: cited experiment has aged evidence: {cid}")

    if ok:
        lines.append("findings check: passed")
    return ok, lines

"""Experiments: create, gate-check, start (with a detached supervisor), and
harvest, plus conclude/abandon/list, the files view and marking.

Inputs are referenced, never copied: ``inputs.txt`` names files, directories
or URIs (optionally with an ``a:``/``b:`` arm prefix) and ``exp check`` records
their provenance and a reconstructability verdict. The supervisor runs in its
own session so it outlives the caller; library code never sleeps in a loop.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys

from . import config as config_mod
from . import gitstate, records, scan, util
from .tracker import TrackerError

_GLOB_CHARS = ("*", "?", "[")


# --------------------------------------------------------------------------
# inputs.txt / outputs.txt
# --------------------------------------------------------------------------


def _nonblank_lines(text):
    return [s for s in (ln.strip() for ln in text.splitlines()) if s and not s.startswith("#")]


def read_outputs(record_dir):
    """Return declared outputs (one per line), skipping blanks and comments."""
    return _nonblank_lines(_safe_read(os.path.join(record_dir, "outputs.txt")))


def read_inputs(record_dir):
    """Return declared input specs (with any arm prefix), blanks/comments dropped."""
    return _nonblank_lines(_safe_read(os.path.join(record_dir, "inputs.txt")))


def _is_none(specs):
    """True when inputs.txt declares the single word ``none`` (a run with no inputs)."""
    return specs == ["none"]


def _is_uri(entry):
    return "://" in entry


def _is_glob(entry):
    return any(ch in entry for ch in _GLOB_CHARS)


def _parse_arm(line):
    """Split an optional ``a:``/``b:`` arm prefix. Returns (arm|None, spec)."""
    if len(line) >= 2 and line[0] in ("a", "b") and line[1] == ":":
        return line[0], line[2:].strip()
    return None, line


def _abspath(record_dir, spec):
    """Resolve a non-URI spec to an absolute path (relative to the record dir)."""
    return spec if os.path.isabs(spec) else os.path.abspath(os.path.join(record_dir, spec))


def _nonblank_noncomment(text):
    return bool(_nonblank_lines(text))


def _safe_read(path):
    try:
        return records.read(path)
    except OSError:
        return ""


# --------------------------------------------------------------------------
# hashing (one streaming helper) and the input hash cache
# --------------------------------------------------------------------------


def _sha256(path, cap_bytes):
    """Stream a file's sha256, or None when it is larger than ``cap_bytes``."""
    if os.path.getsize(path) > cap_bytes:
        return None
    return util.sha256_file(path)


class HashCache:
    """sha256 cache keyed by absolute path, size and mtime_ns.

    A cache hit reads nothing but ``os.stat`` so a large dataset is hashed once.
    """

    def __init__(self, cfg):
        self.path = os.path.join(cfg.root, scan.PRIVATE_DIR, "hash-cache.json")
        self.data = _read_json(self.path) or {}
        self.dirty = False

    def sha256(self, filepath, cap_bytes):
        """Return (sha_or_None, unhashed). Only ``os.stat`` runs on a cache hit."""
        key = os.path.abspath(filepath)
        st = os.stat(key)
        ent = self.data.get(key)
        if ent and ent.get("size") == st.st_size and ent.get("mtime_ns") == st.st_mtime_ns:
            return ent.get("sha256"), bool(ent.get("unhashed"))
        if st.st_size > cap_bytes:
            self._store(key, st, None, True)
            return None, True
        sha = util.sha256_file(key)
        self._store(key, st, sha, False)
        return sha, False

    def _store(self, key, st, sha, unhashed):
        self.data[key] = {
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "sha256": sha,
            "unhashed": unhashed,
        }
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        util.atomic_write(self.path, json.dumps(self.data, indent=2))


# --------------------------------------------------------------------------
# arm parity (paired from a:/b: inputs)
# --------------------------------------------------------------------------


def _flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flatten(v, key))
    else:
        out[prefix] = obj
    return out


def _load_structured(path):
    if path.endswith(".toml"):
        return _flatten(config_mod.load_toml(path))
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as fh:
            return _flatten(json.load(fh))
    return None


def _read_lines(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def _pair_by_basename(a_items, b_items):
    """Pair (spec, path) items by basename, then by order. Returns (pairs, only_a, only_b)."""
    by_base = {}
    for item in b_items:
        by_base.setdefault(os.path.basename(item[0]), []).append(item)
    pairs = []
    unmatched_a = []
    used = set()
    for item in a_items:
        bucket = by_base.get(os.path.basename(item[0]))
        if bucket:
            partner = bucket.pop(0)
            pairs.append((item, partner))
            used.add(id(partner))
        else:
            unmatched_a.append(item)
    remaining_b = [item for item in b_items if id(item) not in used]
    ordered = list(zip(unmatched_a, remaining_b))
    pairs.extend(ordered)
    n = len(ordered)
    only_a = [item[0] for item in unmatched_a[n:]]
    only_b = [item[0] for item in remaining_b[n:]]
    return pairs, only_a, only_b


def arm_parity(record_dir):
    """Compare the ``a:`` and ``b:`` inputs, paired by basename then order."""
    specs = read_inputs(record_dir)
    if _is_none(specs):
        return []
    a_items, b_items = [], []
    for line in specs:
        arm, spec = _parse_arm(line)
        if arm == "a":
            a_items.append((spec, _resolve_for_compare(record_dir, spec)))
        elif arm == "b":
            b_items.append((spec, _resolve_for_compare(record_dir, spec)))
    if not a_items and not b_items:
        return []
    pairs, only_a, only_b = _pair_by_basename(a_items, b_items)
    rows = []
    for spec in only_a:
        rows.append(f"file only in a: {os.path.basename(spec)}")
    for spec in only_b:
        rows.append(f"file only in b: {os.path.basename(spec)}")
    for (spec_a, path_a), (_spec_b, path_b) in pairs:
        rows.extend(_compare_file(path_a, path_b, os.path.basename(spec_a)))
    return rows


def _resolve_for_compare(record_dir, spec):
    if _is_uri(spec):
        return spec
    return _abspath(record_dir, spec)


def _compare_file(pa, pb, label):
    if not (os.path.isfile(pa) and os.path.isfile(pb)):
        return [f"{label}: cannot compare (not both present as files)"]
    sa = _load_structured(pa)
    sb = _load_structured(pb)
    rows = []
    if sa is not None and sb is not None:
        for key in sorted(set(sa) | set(sb)):
            if key not in sa:
                rows.append(f"{label}: key {key} only in b (={sb[key]!r})")
            elif key not in sb:
                rows.append(f"{label}: key {key} only in a (={sa[key]!r})")
            elif sa[key] != sb[key]:
                rows.append(f"{label}: key {key} differs a={sa[key]!r} b={sb[key]!r}")
        return rows
    la = _read_lines(pa)
    lb = _read_lines(pb)
    for i in range(max(len(la), len(lb))):
        va = la[i] if i < len(la) else None
        vb = lb[i] if i < len(lb) else None
        if va != vb:
            rows.append(f"{label}: line {i + 1} differs a={va!r} b={vb!r}")
    return rows


# --------------------------------------------------------------------------
# input provenance + reconstructability verdict
# --------------------------------------------------------------------------


def _dir_tree(base):
    """Return (per-file list, tree hash) for a directory, stable under walk order."""
    files = []
    for root, _dirs, names in os.walk(base):
        for name in names:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, base)
            try:
                sha = util.sha256_file(full)
                size = os.path.getsize(full)
            except OSError:
                sha, size = None, None
            files.append({"relpath": rel, "sha256": sha, "size": size})
    files.sort(key=lambda f: f["relpath"])
    import hashlib

    h = hashlib.sha256()
    for f in files:
        h.update("{}\0{}\n".format(f["relpath"], f["sha256"]).encode("utf-8"))
    return files, h.hexdigest()


def _recoverable_git(cfg, abspath):
    """Return {repo, path, blob} when ``abspath`` is tracked and unmodified, else None."""
    for name, path in cfg.covered_repos():
        repo_abs = os.path.abspath(path)
        rel = os.path.relpath(abspath, repo_abs)
        if rel.startswith(".."):
            continue
        rel = rel.replace(os.sep, "/")
        if gitstate.git(path, "ls-files", "--error-unmatch", "--", rel).returncode != 0:
            continue
        if gitstate.git(path, "status", "--porcelain", "--", rel).stdout.strip():
            continue  # modified since HEAD/index
        blob = gitstate.git(path, "rev-parse", "HEAD:" + rel).stdout.strip()
        if blob:
            return {"repo": name, "path": rel, "blob": blob}
    return None


def _resolve_inputs(cfg, record_dir, cache):
    """Resolve inputs.txt into provenance entries; copy small unrecoverable files.

    Returns (entries, reasons) where ``reasons`` count against the verdict.
    """
    specs = read_inputs(record_dir)
    if _is_none(specs):
        return [], []
    cap = cfg.max_hash_mb * 1024 * 1024
    preserve_cap = cfg.preserve_kb * 1024
    entries = []
    reasons = []
    for line in specs:
        arm, spec = _parse_arm(line)
        entry = {"input": spec}
        if arm:
            entry["arm"] = arm
        if _is_uri(spec):
            entry["kind"] = "uri"
            entry["uri"] = spec
            entries.append(entry)
            continue
        abspath = _abspath(record_dir, spec)
        entry["path"] = abspath
        if not os.path.exists(abspath):
            entry["kind"] = "missing"
            reasons.append(f"input missing: {spec}")
            entries.append(entry)
            continue
        if os.path.isdir(abspath):
            entry["kind"] = "directory"
            files, tree = _dir_tree(abspath)
            entry["files"] = files
            entry["tree_hash"] = tree
            entries.append(entry)  # a hashed directory is reproducible-by-record
            continue
        entry["kind"] = "file"
        st = os.stat(abspath)
        entry["size"] = st.st_size
        entry["mtime"] = st.st_mtime
        rec = _recoverable_git(cfg, abspath)
        if rec:
            entry["recoverable"] = "git"
            entry["repo"] = rec["repo"]
            entry["repo_path"] = rec["path"]
            entry["blob"] = rec["blob"]
        sha, unhashed = cache.sha256(abspath, cap)
        entry["sha256"] = sha
        entry["unhashed"] = unhashed
        preserved = None
        if not rec and cfg.preserve_kb > 0 and st.st_size <= preserve_cap:
            preserved = _preserve(record_dir, abspath)
            entry["preserved"] = preserved
        if not rec and preserved is None and sha is None:
            reasons.append(f"input not reproducible: {spec}")
        entries.append(entry)
    return entries, reasons


def _preserve(record_dir, abspath):
    """Copy an input into ``preserved/``; return the record-relative path."""
    dest_dir = os.path.join(record_dir, "preserved")
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(abspath.rstrip("/")) or "input"
    dest = os.path.join(dest_dir, base)
    i = 1
    digest = util.sha256_file(abspath)
    while os.path.exists(dest):
        if util.sha256_file(dest) == digest:  # a repeated check: already preserved
            return os.path.join("preserved", os.path.basename(dest))
        dest = os.path.join(dest_dir, f"{base}.{i}")  # same name, different content
        i += 1
    shutil.copy2(abspath, dest)
    return os.path.join("preserved", os.path.basename(dest))


# --------------------------------------------------------------------------
# exp check
# --------------------------------------------------------------------------


def _under_volatile(entry, record_dir, volatile):
    candidates = [entry]
    if not os.path.isabs(entry):
        candidates.append(_abspath(record_dir, entry))
    for cand in candidates:
        for prefix in volatile:
            if cand == prefix or cand.startswith(prefix.rstrip("/") + "/"):
                return True
    return False


def check(cfg, record_dir):
    """Run the pre-run gate. Returns (hard_ok, lines).

    Records input provenance and the reconstructability verdict in
    ``provenance.json`` (``exp start`` completes it with the snapshot). The
    verdict never fails the gate.
    """
    lines = []
    hard_ok = True
    readme = os.path.join(record_dir, "README.md")
    text = _safe_read(readme)

    if records.has_fill(text):
        hard_ok = False
        lines.append("FAIL: a slate:fill marker remains in README.md")

    if not records.get_field(text, "hypothesis").strip():
        hard_ok = False
        lines.append("FAIL: frontmatter hypothesis is empty")

    if not _nonblank_noncomment(_safe_read(os.path.join(record_dir, "run.sh"))):
        hard_ok = False
        lines.append("FAIL: run.sh is empty")

    if not read_outputs(record_dir):
        hard_ok = False
        lines.append("FAIL: outputs.txt is empty")

    specs = read_inputs(record_dir)
    if not specs:
        hard_ok = False
        lines.append("FAIL: inputs.txt is empty (declare inputs, or the single word 'none')")
    elif not _is_none(specs):
        for line in specs:
            _arm, spec = _parse_arm(line)
            if _is_uri(spec):
                continue
            if not os.path.exists(_abspath(record_dir, spec)):
                hard_ok = False
                lines.append(f"FAIL: declared input does not exist: {spec}")

    for cmd in cfg.preflight:
        p = subprocess.run(cmd, shell=True, cwd=record_dir, capture_output=True, text=True)
        if p.returncode != 0:
            hard_ok = False
            lines.append(f"FAIL: preflight nonzero ({p.returncode}): {cmd}")

    # Soft reports (never fail the gate) and the verdict.
    reasons = []
    for name, path in cfg.covered_repos():
        # The knowledge base holds the record, not the code under test: its
        # push state is in the snapshot but does not decide reconstructability.
        if not cfg.is_kb_path(path) and not gitstate.pushed(path):
            reasons.append(f"not pushed: {name}")
        ignore = cfg.kb_dirty_ignore() if cfg.is_kb_path(path) else None
        if gitstate.dirty_count(path, ignore) > 0:
            dest = os.path.join(record_dir, "dirty", f"{name}.patch")
            info = gitstate.write_patch(path, dest, 1024 * 1024, cfg.preserve_kb * 1024)
            count = gitstate.dirty_count(path, ignore)
            lines.append(
                "dirty: {} ({} files) -> dirty/{}.patch [{}B, {} untracked embedded]".format(
                    name, count, name, info["written"], info["embedded_untracked"]
                )
            )
            if info["over_cap"] or info["large_untracked"]:
                reasons.append(f"uncommitted changes not fully preserved: {name}")

    cache = HashCache(cfg)
    prov_inputs, input_reasons = _resolve_inputs(cfg, record_dir, cache)
    cache.save()
    reasons.extend(input_reasons)

    if not _is_none(specs):
        for line in specs:
            _arm, spec = _parse_arm(line)
            if not _is_uri(spec) and _under_volatile(spec, record_dir, cfg.volatile):
                lines.append(f"volatile input: {spec}")
                reasons.append(f"volatile input: {spec}")
    for entry in read_outputs(record_dir):
        if not _is_uri(entry) and _under_volatile(entry, record_dir, cfg.volatile):
            lines.append(f"volatile output: {entry}")
            reasons.append(f"volatile output: {entry}")

    for row in arm_parity(record_dir):
        lines.append("arm: " + row)

    verdict = "no" if reasons else "yes"
    lines.append(f"reconstructable: {verdict}")
    for reason in reasons:
        lines.append(f"  - {reason}")

    prov = _load_provenance(record_dir)
    prov["inputs"] = prov_inputs
    prov["reconstructable"] = verdict
    prov["reconstructable_reasons"] = reasons
    _write_provenance(record_dir, prov)

    return hard_ok, lines


# --------------------------------------------------------------------------
# exp new
# --------------------------------------------------------------------------


def new(cfg, tracker, arc, slug, title, serves=None, hypothesis=None):
    """Create an experiment record. Returns (id, record_dir, tracker_error)."""
    date = util.today_str()
    dirname = f"{date}-{slug}"
    record_dir = os.path.join(cfg.arc_dir(arc), "experiments", dirname)
    os.makedirs(os.path.join(record_dir, "results"), exist_ok=True)
    exp_id = f"{arc}/{dirname}"
    if serves is None:
        serves = scan.arc_directive(cfg, arc)
    body = util.render(
        util.load_template("experiment.md"),
        {
            "id": exp_id,
            "arc": arc,
            "title": title,
            "serves": serves,
            "hypothesis": hypothesis or "",
            "date": date,
            "tracker": "",
        },
    )
    records.write(os.path.join(record_dir, "README.md"), body)
    run_sh = os.path.join(record_dir, "run.sh")
    records.write(run_sh, "")
    os.chmod(run_sh, 0o755)
    records.write(os.path.join(record_dir, "inputs.txt"), "")
    records.write(os.path.join(record_dir, "outputs.txt"), "")

    tracker_err = None
    if tracker.enabled():
        parent = records.get_field(records.read(scan.arc_readme(cfg, arc)), "tracker")
        try:
            issue = tracker.create_child("task", title, parent, labels="slate-experiment")
            if issue:
                records.apply_update(os.path.join(record_dir, "README.md"), {"tracker": issue})
        except TrackerError as exc:
            tracker_err = str(exc)
    return exp_id, record_dir, tracker_err


# --------------------------------------------------------------------------
# provenance.json helpers
# --------------------------------------------------------------------------


def _load_provenance(record_dir):
    return _read_json(os.path.join(record_dir, "provenance.json")) or {}


def _write_provenance(record_dir, prov):
    util.atomic_write(os.path.join(record_dir, "provenance.json"), json.dumps(prov, indent=2))


# --------------------------------------------------------------------------
# exp start + supervisor
# --------------------------------------------------------------------------


def start(cfg, record_dir, detach=False):
    """Gate-check, snapshot, then launch the run under a detached supervisor.

    Returns (exit_code, lines). In waiting mode the run is harvested before
    returning.
    """
    lines = []
    hard_ok, check_lines = check(cfg, record_dir)
    lines.extend(check_lines)
    if not hard_ok:
        lines.append("check failed: not starting")
        return 1, lines

    snap = gitstate.snapshot(cfg)
    rel = os.path.relpath(os.path.join(record_dir, "README.md"), cfg.root)
    plan = gitstate.plan_commit(cfg.root, rel)
    prov = _load_provenance(record_dir)  # inputs + verdict recorded by check
    prov.update(snap)
    prov["plan_commit"] = plan
    run_sh = os.path.join(record_dir, "run.sh")
    try:
        prov["run_sh_sha256"] = util.sha256_file(run_sh)
    except OSError:
        prov["run_sh_sha256"] = None
    prov["working_dir"] = os.path.abspath(record_dir)
    _write_provenance(record_dir, prov)
    if plan is None:
        lines.append("plan is not committed: the prediction is not provably prior to the run")

    now = util.now_utc()
    readme = os.path.join(record_dir, "README.md")
    records.apply_update(readme, {"status": "running", "started": util.iso(now)})

    start_epoch = now.timestamp()
    lib_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        f"import sys; sys.path.insert(0, {lib_dir!r}); "
        f"from slate.runs import supervise_main; supervise_main({os.path.abspath(record_dir)!r})"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    run_json = {
        "pid": proc.pid,
        "host": util.host(),
        "log": os.path.join(os.path.abspath(record_dir), "log.txt"),
        "start": util.iso(now),
        "start_epoch": start_epoch,
    }
    util.atomic_write(os.path.join(record_dir, "run.json"), json.dumps(run_json, indent=2))

    if detach:
        lines.append(f"detached: pid {proc.pid}, log {run_json['log']}")
        return 0, lines

    proc.wait()
    exp_id = _record_id(cfg, record_dir)
    rows = harvest(cfg, exp_id)
    lines.extend(rows)
    return 0, lines


def supervise_main(record_dir):
    """Detached supervisor: run run.sh, capture output, write outcome.json."""
    record_dir = os.path.abspath(record_dir)
    run_sh = os.path.join(record_dir, "run.sh")
    log_path = os.path.join(record_dir, "log.txt")
    start = util.now_utc()
    with open(log_path, "wb") as log:
        try:
            proc = subprocess.Popen([run_sh], cwd=record_dir, stdout=log, stderr=subprocess.STDOUT)
        except OSError:
            proc = subprocess.Popen(
                ["/bin/sh", run_sh], cwd=record_dir, stdout=log, stderr=subprocess.STDOUT
            )
        exit_code = proc.wait()
    finished = util.now_utc()
    wall = (finished - start).total_seconds()
    outcome = {
        "exit_code": exit_code,
        "finished": util.iso(finished),
        "wall_seconds": round(wall, 3),
    }
    util.atomic_write(os.path.join(record_dir, "outcome.json"), json.dumps(outcome, indent=2))


# --------------------------------------------------------------------------
# harvest
# --------------------------------------------------------------------------


def _record_id(cfg, record_dir):
    for eid, d in scan.experiment_dirs(cfg):
        if os.path.abspath(d) == os.path.abspath(record_dir):
            return eid
    return None


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False
    return True


def verify_outputs(record_dir, start_epoch, cap_bytes):
    """Verify declared outputs. Returns (results, all_ok)."""
    results = []
    all_ok = True
    for entry in read_outputs(record_dir):
        if _is_uri(entry):
            results.append({"output": entry, "uri": True, "verified": None})
            continue
        matches = sorted(glob.glob(os.path.join(record_dir, entry)))
        detail = {"output": entry, "missing": not matches, "files": []}
        ok = bool(matches)
        for full in matches:
            try:
                st = os.stat(full)
            except OSError:
                ok = False
                continue
            empty = st.st_size == 0
            stale = st.st_mtime <= start_epoch
            if empty or stale:
                ok = False
            detail["files"].append(
                {
                    "path": os.path.relpath(full, record_dir),
                    "size": st.st_size,
                    "empty": empty,
                    "stale": stale,
                    "sha256": None if empty else _sha256(full, cap_bytes),
                }
            )
        detail["ok"] = ok
        if not ok:
            all_ok = False
        results.append(detail)
    return results, all_ok


def _recheck_inputs(cfg, prov):
    """Return the input specs whose bytes changed since ``exp start`` recorded them."""
    cap = cfg.max_hash_mb * 1024 * 1024
    mutated = []
    for entry in prov.get("inputs", []):
        if entry.get("kind") != "file":
            continue
        path = entry.get("path")
        try:
            st = os.stat(path)
        except OSError:
            mutated.append(entry["input"])
            continue
        rec_size = entry.get("size")
        if rec_size is not None and st.st_size != rec_size:
            mutated.append(entry["input"])
            continue
        rec_mtime = entry.get("mtime")
        if rec_mtime is not None and st.st_mtime != rec_mtime:
            rec_sha = entry.get("sha256")
            if rec_sha is None:
                mutated.append(entry["input"])
                continue
            new_sha = util.sha256_file(path) if st.st_size <= cap else None
            if new_sha != rec_sha:
                mutated.append(entry["input"])
    return mutated


def harvest(cfg, given=None):
    """Settle running experiments (or one). Returns printable rows."""
    if given:
        eid, record_dir = scan.find_experiment(cfg, given)
        targets = [(eid, record_dir)]
    else:
        targets = [
            (eid, d)
            for eid, d in scan.experiment_dirs(cfg)
            if scan.experiment_field(d, "status") == "running"
        ]
    return [_harvest_one(cfg, eid, record_dir) for eid, record_dir in targets]


def _harvest_one(cfg, eid, record_dir):
    outcome_path = os.path.join(record_dir, "outcome.json")
    run_json = _read_json(os.path.join(record_dir, "run.json")) or {}
    start_epoch = run_json.get("start_epoch")
    if start_epoch is None:
        started = scan.experiment_field(record_dir, "started")
        dt = util.parse_ts(started)
        start_epoch = dt.timestamp() if dt else 0.0
    readme = os.path.join(record_dir, "README.md")

    if os.path.isfile(outcome_path):
        outcome = _read_json(outcome_path) or {}
        results, all_ok = verify_outputs(record_dir, start_epoch, cfg.max_hash_mb * 1024 * 1024)
        outcome["outputs"] = results
        prov = _load_provenance(record_dir)
        mutated = _recheck_inputs(cfg, prov)
        outcome["inputs_mutated"] = mutated
        if mutated:
            reasons = prov.get("reconstructable_reasons", [])
            for spec in mutated:
                reason = f"input mutated during run: {spec}"
                if reason not in reasons:
                    reasons.append(reason)
            prov["reconstructable_reasons"] = reasons
            prov["reconstructable"] = "no"
            _write_provenance(record_dir, prov)
        exit0 = outcome.get("exit_code") == 0
        status = "done" if (exit0 and all_ok) else "failed"
        outcome["status"] = status
        util.atomic_write(outcome_path, json.dumps(outcome, indent=2))
        records.apply_update(
            readme, {"status": status, "finished": outcome.get("finished", util.now_ts())}
        )
        extra = f"  inputs_mutated={len(mutated)}" if mutated else ""
        return "{}  {}  exit={}  wall={}s{}".format(
            eid, status, outcome.get("exit_code"), outcome.get("wall_seconds"), extra
        )

    if not _pid_alive(run_json.get("pid")):
        records.apply_update(readme, {"status": "lost"})
        return "{}  lost  (pid {} dead, no outcome)".format(eid, run_json.get("pid"))

    elapsed = util.human_age(util.now_utc().timestamp() - start_epoch)
    last = util.tail(os.path.join(record_dir, "log.txt"), 3)
    tail = " | ".join(last) if last else "(no log yet)"
    return f"{eid}  running  elapsed={elapsed}  log: {tail}"


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# conclude / abandon / list
# --------------------------------------------------------------------------


def conclude(cfg, tracker, given, outcome, conclusion):
    """Set the outcome and conclusion after a settled run. Returns (id, tracker_error)."""
    eid, record_dir = scan.find_experiment(cfg, given)
    readme = os.path.join(record_dir, "README.md")
    text = records.read(readme)
    status = records.get_field(text, "status")
    if status not in ("done", "failed"):
        raise records.GateError(
            f"experiment {eid} is {status or 'planned'}; conclude needs done or failed"
        )
    if records.has_after(text):
        raise records.GateError(f"a slate:after marker remains in {eid}")
    conclusion = (conclusion or "").strip()
    if not conclusion:
        raise records.GateError("conclude requires a one-sentence --conclusion")
    if len(conclusion) > 300:
        raise records.GateError(
            f"conclusion is {len(conclusion)} characters; at most 300 are allowed"
        )
    records.apply_update(readme, {"outcome": outcome, "conclusion": conclusion})
    tracker_err = None
    if tracker.enabled():
        issue = records.get_field(text, "tracker")
        if issue:
            try:
                tracker.close(issue, outcome)
            except TrackerError as exc:
                tracker_err = str(exc)
    return eid, tracker_err


def abandon(cfg, given, reason):
    """Mark an experiment abandoned with a reason. Returns its id."""
    eid, record_dir = scan.find_experiment(cfg, given)
    readme = os.path.join(record_dir, "README.md")
    records.apply_update(
        readme,
        {
            "status": "abandoned",
            "abandoned": f"{util.now_ts()} {reason}",
        },
    )
    return eid


def listing(cfg, arc=None, status=None):
    """Return rows of (id, status, outcome, title) for experiments."""
    rows = []
    for eid, d in scan.experiment_dirs(cfg, arc):
        text = records.read(os.path.join(d, "README.md"))
        st = records.get_field(text, "status")
        if status and st != status:
            continue
        rows.append(
            {
                "id": eid,
                "status": st,
                "outcome": records.get_field(text, "outcome"),
                "title": records.get_field(text, "title"),
            }
        )
    return rows


# --------------------------------------------------------------------------
# files view + marking
# --------------------------------------------------------------------------


def _declared_paths(record_dir):
    """Yield (role, spec, key) for every declared input and output.

    ``key`` is the resolved absolute path (files/dirs) or the spec itself
    (URIs, globs) and is what the shared-by count is grouped on.
    """
    specs = read_inputs(record_dir)
    if not _is_none(specs):
        for line in specs:
            _arm, spec = _parse_arm(line)
            key = spec if _is_uri(spec) else _abspath(record_dir, spec)
            yield "input", spec, key
    for spec in read_outputs(record_dir):
        key = spec if (_is_uri(spec) or _is_glob(spec)) else _abspath(record_dir, spec)
        yield "output", spec, key


def _declaration_index(cfg):
    """Map each declared key to the set of experiment ids that declare it."""
    index = {}
    for eid, d in scan.experiment_dirs(cfg):
        for _role, _spec, key in _declared_paths(d):
            index.setdefault(key, set()).add(eid)
    return index


def _mark_scope(text):
    """Return the mark scope (inputs|outputs|all) or '' from the ``marked`` field."""
    marked = records.get_field(text, "marked").strip()
    if not marked:
        return ""
    parts = marked.split()
    return parts[1] if len(parts) >= 2 else ""


def _mark_for(scope, role):
    if scope == "all" or scope == role + "s":
        return scope
    return "-"


def _output_baseline(outcome, spec):
    for detail in outcome.get("outputs", []):
        if detail.get("output") == spec and detail.get("files"):
            return detail["files"][0]
    return None


def _file_state(cfg, record_dir, role, spec, prov_inputs, outcome, verify):
    """Return (state, size) for one declared file. State is present/changed/missing/remote."""
    if _is_uri(spec):
        return "remote", None
    path = _abspath(record_dir, spec)
    if role == "output" and _is_glob(spec):
        matches = glob.glob(path)
        if not matches:
            return "missing", 0
        return "present", sum(_safe_size(m) for m in matches)
    if not os.path.exists(path):
        return "missing", None
    size = _safe_size(path)
    if role == "input":
        baseline = prov_inputs.get(spec)
    else:
        baseline = _output_baseline(outcome, spec)
    if baseline is None:
        return "present", size
    cap = cfg.max_hash_mb * 1024 * 1024
    if verify and baseline.get("sha256") is not None:
        new_sha = util.sha256_file(path) if size is not None and size <= cap else None
        return ("changed" if new_sha != baseline.get("sha256") else "present"), size
    rec_size = baseline.get("size")
    if rec_size is not None and size != rec_size:
        return "changed", size
    rec_mtime = baseline.get("mtime")
    if rec_mtime is not None and os.stat(path).st_mtime != rec_mtime:
        return "changed", size
    return "present", size


def _safe_size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def files_view(cfg, given=None, arc=None, verify=False):
    """Return a list of per-experiment groups for the files view.

    Each group is ``{id, rows, input_total, output_total}`` where a row is
    ``{experiment, role, path, size, state, shared, mark}``.
    """
    index = _declaration_index(cfg)
    if given:
        eid, d = scan.find_experiment(cfg, given)
        targets = [(eid, d)]
    else:
        arc_slug = None
        if arc:
            arc_slug, _ = scan.find_arc(cfg, arc)
        targets = scan.experiment_dirs(cfg, arc_slug)
    groups = []
    for eid, d in targets:
        text = _safe_read(os.path.join(d, "README.md"))
        scope = _mark_scope(text)
        prov = _load_provenance(d)
        prov_inputs = {e.get("input"): e for e in prov.get("inputs", [])}
        outcome = _read_json(os.path.join(d, "outcome.json")) or {}
        rows = []
        in_files = in_bytes = out_files = out_bytes = 0
        for role, spec, key in _declared_paths(d):
            state, size = _file_state(cfg, d, role, spec, prov_inputs, outcome, verify)
            shared = len(index.get(key, set()) - {eid})
            rows.append(
                {
                    "experiment": eid,
                    "role": role,
                    "path": spec,
                    "size": size,
                    "state": state,
                    "shared": shared,
                    "mark": _mark_for(scope, role),
                }
            )
            if role == "input":
                in_files += 1
                in_bytes += size or 0
            else:
                out_files += 1
                out_bytes += size or 0
        groups.append(
            {
                "id": eid,
                "rows": rows,
                "input_total": {"files": in_files, "bytes": in_bytes},
                "output_total": {"files": out_files, "bytes": out_bytes},
            }
        )
    return groups


def mark(cfg, given, scope, reason):
    """Mark an experiment's files for deletion. Returns (id, warnings). Deletes nothing."""
    from . import findings

    eid, record_dir = scan.find_experiment(cfg, given)
    readme = os.path.join(record_dir, "README.md")
    stamp = f"{util.today_str()} {scope} {reason}"
    records.apply_update(readme, {"marked": stamp})
    warnings = []
    promoted = records.get_field(records.read(readme), "promoted_to").strip()
    if promoted:
        warnings.append(f"promoted into a note ({promoted}); its outputs are evidence")
    cited = findings.citing_files(cfg, eid)
    for path in cited:
        warnings.append(f"cited in findings file: {os.path.relpath(path, cfg.root)}")
    return eid, warnings


def unmark(cfg, given):
    """Clear an experiment's deletion mark. Returns its id."""
    eid, record_dir = scan.find_experiment(cfg, given)
    records.apply_update(os.path.join(record_dir, "README.md"), {"marked": ""})
    return eid

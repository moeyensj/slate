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
import tempfile

from . import config as config_mod
from . import gitstate, records, scan, util
from .tracker import TrackerError

_GLOB_CHARS = ("*", "?", "[")
_SLATE_OUT = "$SLATE_OUT"


# --------------------------------------------------------------------------
# record kind, the $SLATE_OUT directory, and output-spec expansion
# --------------------------------------------------------------------------


def record_kind(record_dir):
    """Return the record's kind (``experiment`` or ``dataset``) from its frontmatter."""
    text = _safe_read(os.path.join(record_dir, "README.md"))
    return records.get_field(text, "slate").strip() or "experiment"


def has_recipe(record_dir):
    """True when ``run.sh`` holds a real command (an adopted dataset has none)."""
    return _nonblank_noncomment(_safe_read(os.path.join(record_dir, "run.sh")))


def _record_id(cfg, record_dir):
    """The id (experiment or dataset) whose directory is ``record_dir``, or None."""
    target = os.path.abspath(record_dir)
    for rid, d in scan.record_dirs(cfg):
        if os.path.abspath(d) == target:
            return rid
    return None


def out_dir_for(cfg, record_dir, record_id=None):
    """The run's ``$SLATE_OUT`` directory.

    ``<out_root>/<record id>/`` when ``experiment.out_root`` is set, otherwise
    the record's own ``results/`` directory. Falls back to ``results/`` when the
    id cannot be resolved (a record not yet enumerable on disk).
    """
    if cfg.out_root_dir:
        rid = record_id if record_id is not None else _record_id(cfg, record_dir)
        if rid:
            return os.path.join(cfg.out_root_dir, rid)
    return os.path.join(record_dir, "results")


def _expand_out(record_dir, out_dir, spec):
    """Expand a leading ``$SLATE_OUT`` in an output spec; else resolve normally."""
    if spec == _SLATE_OUT:
        return out_dir
    if spec.startswith(_SLATE_OUT + "/"):
        return os.path.join(out_dir, spec[len(_SLATE_OUT) + 1 :])
    return _abspath(record_dir, spec)


def _is_dataset(spec):
    return spec.startswith(scan.DATASET_PREFIX)


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


def _record_arc(record_dir):
    """The arc a record belongs to ('' for a dataset, which has none)."""
    return records.get_field(_safe_read(os.path.join(record_dir, "README.md")), "arc") or ""


def _recoverable_git(cfg, abspath):
    """Return {repo, path, blob} when ``abspath`` is tracked and unmodified, else None."""
    for name, path in cfg.all_covered_repos():
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
        if _is_dataset(spec):
            _resolve_dataset_input(cfg, spec, entry, cache, reasons)
            entries.append(entry)
            continue
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


def _dataset_input_failure(cfg, spec):
    """Return a hard-failure message for a ``dataset:`` input, or None when usable."""
    from . import datasets

    slug = datasets.slug_of(spec)
    try:
        _did, ddir = datasets.find(cfg, scan.DATASET_PREFIX + slug)
    except records.IdError:
        return f"dataset input does not exist: {slug}"
    if datasets.status(ddir) != datasets.BUILT:
        return f"dataset input is not built: {slug}"
    return None


def _resolve_dataset_input(cfg, spec, entry, cache, reasons):
    """Record a ``dataset:<slug>`` input: its id, manifest hash and match state.

    Existence and ``built`` status are hard-checked in :func:`check`; here we
    only add the verdict reason when a built dataset no longer matches its
    manifest.
    """
    from . import datasets

    slug = datasets.slug_of(spec)
    dsid = scan.DATASET_PREFIX + slug
    entry["kind"] = "dataset"
    entry["dataset"] = dsid
    try:
        _did, ddir = datasets.find(cfg, dsid)
    except records.IdError:
        entry["dataset_state"] = "missing"
        return
    entry["manifest_sha256"] = datasets.manifest_sha256(ddir)
    if datasets.status(ddir) != datasets.BUILT:
        entry["dataset_state"] = "not-built"
        return
    ok, _diffs = datasets.matches_via_cache(cfg, ddir, cache)
    entry["dataset_state"] = "built"
    entry["matches"] = ok
    if not ok:
        reasons.append(f"dataset no longer matches its manifest: {slug}")


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
    kind = record_kind(record_dir)

    if records.has_fill(text):
        hard_ok = False
        lines.append("FAIL: a slate:fill marker remains in README.md")

    # A dataset record has no hypothesis; the claim under test is experiment-only.
    if kind == "experiment" and not records.get_field(text, "hypothesis").strip():
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
            if _is_dataset(spec):
                fail = _dataset_input_failure(cfg, spec)
                if fail:
                    hard_ok = False
                    lines.append("FAIL: " + fail)
                continue
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
    out_dir = out_dir_for(cfg, record_dir)

    # A run.sh that hard-codes the repositories-root path would execute the
    # present code on a rerun, not the recorded code. Reported, never failed.
    run_sh_text = _safe_read(os.path.join(record_dir, "run.sh"))
    if cfg.repos_root_dir and cfg.repos_root_dir in run_sh_text:
        lines.append(f"literal repositories-root path in run.sh: {cfg.repos_root_dir}")
    for binary in gitstate.binaries(cfg):
        if binary.get("error"):
            lines.append(f"configured binary not found: {binary['spec']}")
            reasons.append(f"configured binary not found: {binary['spec']}")
    for name, path in cfg.covered_repos(_record_arc(record_dir)):
        # The knowledge base holds the record, not the code under test: its
        # push state is in the snapshot but does not decide reconstructability.
        if not cfg.is_kb_path(path) and not gitstate.pushed(path):
            reasons.append(f"not pushed: {name}")
        ignore = cfg.kb_dirty_ignore() if cfg.is_kb_path(path) else None
        if gitstate.dirty_count(path, ignore) > 0:
            dest = os.path.join(record_dir, "dirty", f"{name}.patch")
            info = gitstate.write_patch(path, dest, 1024 * 1024, cfg.preserve_kb * 1024, ignore)
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
            if _is_uri(spec) or _is_dataset(spec):
                continue
            if _under_volatile(spec, record_dir, cfg.volatile):
                lines.append(f"volatile input: {spec}")
                reasons.append(f"volatile input: {spec}")
    for entry in read_outputs(record_dir):
        if _is_uri(entry):
            continue
        target = _expand_out(record_dir, out_dir, entry)
        if _under_volatile(target, record_dir, cfg.volatile):
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


def start(cfg, record_dir, detach=False, repos_root=None):
    """Gate-check, snapshot, then launch the run under a detached supervisor.

    Returns (exit_code, lines). In waiting mode the run is harvested before
    returning. ``repos_root`` overrides ``SLATE_REPOS_ROOT`` for the run (a
    ``rerun`` points it at the reconstructed tree).
    """
    lines = []
    hard_ok, check_lines = check(cfg, record_dir)
    lines.extend(check_lines)
    if not hard_ok:
        lines.append("check failed: not starting")
        return 1, lines

    snap = gitstate.snapshot(cfg, _record_arc(record_dir))
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

    # The run environment. The supervisor inherits it, and run.sh (spawned by
    # the supervisor) inherits it in turn, so run.sh sees these three variables
    # and refers to code and large outputs only through them.
    record_id = _record_id(cfg, record_dir)
    out_dir = os.path.abspath(out_dir_for(cfg, record_dir, record_id))
    os.makedirs(out_dir, exist_ok=True)
    repos_root_dir = os.path.abspath(repos_root) if repos_root is not None else cfg.repos_root_dir
    run_env = dict(os.environ)
    run_env["SLATE_RECORD"] = os.path.abspath(record_dir)
    run_env["SLATE_REPOS_ROOT"] = repos_root_dir
    run_env["SLATE_OUT"] = out_dir

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
        env=run_env,
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


def verify_outputs(record_dir, out_dir, start_epoch, cap_bytes):
    """Verify declared outputs. Returns (results, all_ok).

    A ``$SLATE_OUT`` prefix in an output line is expanded against ``out_dir``.
    Each recorded file carries both its record-relative ``path`` and its
    absolute ``abspath`` (used by the manifest, the files view and the sweep).
    """
    results = []
    all_ok = True
    for entry in read_outputs(record_dir):
        if _is_uri(entry):
            results.append({"output": entry, "uri": True, "verified": None})
            continue
        target = _expand_out(record_dir, out_dir, entry)
        matches = sorted(glob.glob(target))
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
                    "abspath": os.path.abspath(full),
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
    """Settle running experiments (or one record of either kind). Returns rows."""
    if given:
        eid, record_dir = scan.find_record(cfg, given)
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

    kind = record_kind(record_dir)
    out_dir = out_dir_for(cfg, record_dir, eid)

    if os.path.isfile(outcome_path):
        outcome = _read_json(outcome_path) or {}
        results, all_ok = verify_outputs(
            record_dir, out_dir, start_epoch, cfg.max_hash_mb * 1024 * 1024
        )
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
        verified = exit0 and all_ok
        # A dataset's success is `built`; an experiment's is `done`.
        if kind == "dataset":
            status = "built" if verified else "failed"
        else:
            status = "done" if verified else "failed"
        outcome["status"] = status
        util.atomic_write(outcome_path, json.dumps(outcome, indent=2))
        updates = {"status": status, "finished": outcome.get("finished", util.now_ts())}
        if kind == "dataset" and verified:
            from . import datasets

            datasets.write_manifest_from_outputs(record_dir, results)
            updates["rebuildable"] = datasets.rebuildable(cfg, eid)
        records.apply_update(readme, updates)
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


def _declared_paths(cfg, record_id, record_dir, out_dir):
    """Yield (role, spec, key) for every declared input and output.

    ``key`` groups the shared-by count: the dataset id for a ``dataset:``
    input, the spec itself for a URI, and otherwise the resolved absolute path
    (``$SLATE_OUT`` expanded for outputs).
    """
    from . import datasets

    specs = read_inputs(record_dir)
    if not _is_none(specs):
        for line in specs:
            _arm, spec = _parse_arm(line)
            if _is_dataset(spec):
                key = scan.DATASET_PREFIX + datasets.slug_of(spec)
            elif _is_uri(spec):
                key = spec
            else:
                key = _abspath(record_dir, spec)
            yield "input", spec, key
    for spec in read_outputs(record_dir):
        key = spec if _is_uri(spec) else _expand_out(record_dir, out_dir, spec)
        yield "output", spec, key


def _declaration_index(cfg):
    """Map each declared key to the set of record ids (either kind) that declare it."""
    index = {}
    for rid, d in scan.record_dirs(cfg):
        out_dir = out_dir_for(cfg, d, rid)
        for _role, _spec, key in _declared_paths(cfg, rid, d, out_dir):
            index.setdefault(key, set()).add(rid)
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


def _dataset_input_state(cfg, spec, verify):
    """Return (state, bytes) for a ``dataset:`` input: present/changed/missing."""
    from . import datasets

    dsid = scan.DATASET_PREFIX + datasets.slug_of(spec)
    try:
        _did, ddir = datasets.find(cfg, dsid)
    except records.IdError:
        return "missing", None
    man = datasets.read_manifest(ddir)
    if datasets.status(ddir) != datasets.BUILT or not man:
        return "missing", None
    total = sum((f.get("size") or 0) for f in man.get("files", []))
    ok = datasets.manifest_matches(cfg, ddir, rehash=verify)
    return ("present" if ok else "changed"), total


def _file_state(cfg, record_dir, role, spec, prov_inputs, outcome, verify, out_dir):
    """Return (state, size) for one declared file. State is present/changed/missing/remote."""
    if _is_uri(spec):
        return "remote", None
    if _is_dataset(spec):
        return _dataset_input_state(cfg, spec, verify)
    path = (
        _expand_out(record_dir, out_dir, spec) if role == "output" else _abspath(record_dir, spec)
    )
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
        eid, d = scan.find_record(cfg, given)
        targets = [(eid, d)]
    elif arc:
        arc_slug, _ = scan.find_arc(cfg, arc)
        targets = scan.experiment_dirs(cfg, arc_slug)
    else:
        targets = scan.record_dirs(cfg)
    groups = []
    for eid, d in targets:
        out_dir = out_dir_for(cfg, d, eid)
        text = _safe_read(os.path.join(d, "README.md"))
        scope = _mark_scope(text)
        prov = _load_provenance(d)
        prov_inputs = {e.get("input"): e for e in prov.get("inputs", [])}
        outcome = _read_json(os.path.join(d, "outcome.json")) or {}
        rows = []
        in_files = in_bytes = out_files = out_bytes = 0
        for role, spec, key in _declared_paths(cfg, eid, d, out_dir):
            state, size = _file_state(cfg, d, role, spec, prov_inputs, outcome, verify, out_dir)
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
    """Mark a record's files for deletion. Returns (id, warnings). Deletes nothing."""
    from . import datasets, findings

    rid, record_dir = scan.find_record(cfg, given)
    readme = os.path.join(record_dir, "README.md")
    stamp = f"{util.today_str()} {scope} {reason}"
    records.apply_update(readme, {"marked": stamp})
    warnings = []
    if record_kind(record_dir) == "dataset":
        if datasets.rebuildable(cfg, rid) != "yes":
            warnings.append("not rebuildable: its files cannot be regenerated")
        dirs = dict(scan.record_dirs(cfg))
        for user in sorted(datasets.usage_index(cfg).get(rid, set())):
            udir = dirs.get(user)
            if (
                udir
                and not records.get_field(
                    _safe_read(os.path.join(udir, "README.md")), "marked"
                ).strip()
            ):
                warnings.append(f"used by unmarked record: {user}")
    else:
        promoted = records.get_field(records.read(readme), "promoted_to").strip()
        if promoted:
            warnings.append(f"promoted into a note ({promoted}); its outputs are evidence")
        for path in findings.citing_files(cfg, rid):
            warnings.append(f"cited in findings file: {os.path.relpath(path, cfg.root)}")
    return rid, warnings


def unmark(cfg, given):
    """Clear a record's deletion mark. Returns its id."""
    rid, record_dir = scan.find_record(cfg, given)
    records.apply_update(os.path.join(record_dir, "README.md"), {"marked": ""})
    return rid


# --------------------------------------------------------------------------
# sweep: delete marked records' files, guarded by a conjunction of predicates
# --------------------------------------------------------------------------
#
# Every predicate below returns a *keep reason* string when it blocks deletion,
# and None when it permits it. A file is deleted only when every predicate
# permits it, so the dry-run table can print exactly why any file is kept. The
# plan is pure data; only ``_execute_sweep`` (called with confirm=True) unlinks.


def _blocked_outside_roots(cfg, abspath):
    """Keep unless the file's real path lies under a cleanup root or out_root."""
    real = os.path.realpath(abspath)
    for root in cfg.cleanup_root_dirs():
        if real == root or real.startswith(root.rstrip("/") + "/"):
            return None
    return "outside cleanup roots"


def _blocked_tracked(cfg, abspath):
    """Keep a file git tracks in any covered repository (git can recover it)."""
    for name, path in cfg.all_covered_repos():
        repo_abs = os.path.abspath(path)
        rel = os.path.relpath(abspath, repo_abs)
        if rel.startswith(".."):
            continue
        rel = rel.replace(os.sep, "/")
        if gitstate.git(path, "ls-files", "--error-unmatch", "--", rel).returncode == 0:
            return f"git-tracked in {name}"
    return None


def _blocked_hash(abspath, recorded_sha):
    """Keep unless the file's current sha256 equals the recorded one."""
    if recorded_sha is None:
        return "recorded without a hash"
    try:
        current = util.sha256_file(abspath)
    except OSError:
        return "unreadable"
    return None if current == recorded_sha else "hash changed since it was recorded"


def _protection_index(cfg):
    """Map an abspath key to a list of (record_id, role, mark_scope) that declare it."""
    index = {}
    for rid, d in scan.record_dirs(cfg):
        out_dir = out_dir_for(cfg, d, rid)
        scope = _mark_scope(_safe_read(os.path.join(d, "README.md")))
        for role, _spec, key in _declared_paths(cfg, rid, d, out_dir):
            index.setdefault(key, []).append((rid, role, scope))
    return index


def _blocked_shared(index, record_id, abspath):
    """Keep a file any *other* record still holds after this sweep.

    One run's output is often the next run's input, so the role under which
    the marked record declares the file does not matter. Another record
    protects the path when it is unmarked, or when its mark does not cover the
    role under which it declares the path. A record that declares a directory
    protects every file beneath it.
    """
    path = abspath
    while True:
        for rid, role, scope in index.get(path, []):
            if rid != record_id and (scope == "" or _mark_for(scope, role) == "-"):
                return f"declared by {rid}"
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def _consider_file(cfg, index, record_id, abspath, role, recorded_sha):
    """Decide one file. Returns {path, role, action, reason, size, sha256}."""
    size = _safe_size(abspath)
    reason = None
    if not os.path.exists(abspath):
        reason = "missing (nothing to delete)"
    if reason is None:
        reason = _blocked_outside_roots(cfg, abspath)
    if reason is None:
        reason = _blocked_tracked(cfg, abspath)
    if reason is None:
        reason = _blocked_hash(abspath, recorded_sha)
    if reason is None:
        reason = _blocked_shared(index, record_id, abspath)
    return {
        "path": abspath,
        "role": role,
        "action": "keep" if reason else "delete",
        "reason": reason or "",
        "size": size,
        "sha256": recorded_sha,
        "mtime_ns": _safe_mtime_ns(abspath),
    }


def _keep_item(spec, role, reason):
    return {
        "path": spec,
        "role": role,
        "action": "keep",
        "reason": reason,
        "size": None,
        "sha256": None,
    }


def _plan_directory(cfg, index, record_id, dir_abspath, entry, role, items, dirs):
    """Plan a declared directory: its recorded files one by one, never recursively."""
    listed = set()
    delete_all = True
    for f in entry.get("files", []):
        full = os.path.join(dir_abspath, f.get("relpath", ""))
        listed.add(os.path.realpath(full))
        item = _consider_file(cfg, index, record_id, full, role, f.get("sha256"))
        items.append(item)
        if item["action"] != "delete":
            delete_all = False
    present = set()
    for root, _d, names in os.walk(dir_abspath):
        for n in names:
            present.add(os.path.realpath(os.path.join(root, n)))
    unlisted = present - listed
    if unlisted:
        dirs.append(
            {
                "path": dir_abspath,
                "action": "keep",
                "reason": "holds files the record does not list",
            }
        )
    elif not delete_all:
        dirs.append(
            {"path": dir_abspath, "action": "keep", "reason": "not every listed file is deletable"}
        )
    else:
        dirs.append({"path": dir_abspath, "action": "rmdir", "reason": ""})


def _plan_output(cfg, index, record_id, record_dir, out_dir, spec, outcome, items):
    target = _expand_out(record_dir, out_dir, spec)
    recorded = {}
    for detail in outcome.get("outputs", []):
        if detail.get("output") == spec:
            for f in detail.get("files", []):
                ap = f.get("abspath") or os.path.normpath(
                    os.path.join(record_dir, f.get("path", ""))
                )
                recorded[os.path.realpath(ap)] = f.get("sha256")
    if _is_glob(spec):
        matches = sorted(glob.glob(target))
        if not matches:
            items.append(_keep_item(spec, "output", "no files match the glob"))
            return
    else:
        matches = [target]
    for full in matches:
        items.append(
            _consider_file(
                cfg, index, record_id, full, "output", recorded.get(os.path.realpath(full))
            )
        )


def _plan_experiment(cfg, index, rid, record_dir, scope, out_dir, prov, outcome, items, dirs):
    prov_inputs = {e.get("input"): e for e in prov.get("inputs", [])}
    if scope in ("all", "inputs"):
        specs = read_inputs(record_dir)
        if not _is_none(specs):
            for line in specs:
                _arm, spec = _parse_arm(line)
                if _is_dataset(spec):
                    items.append(
                        _keep_item(
                            spec, "input", "dataset input: not swept through an experiment mark"
                        )
                    )
                    continue
                if _is_uri(spec):
                    items.append(_keep_item(spec, "input", "remote: not deleted by slate"))
                    continue
                entry = prov_inputs.get(spec)
                abspath = _abspath(record_dir, spec)
                if entry and entry.get("kind") == "directory":
                    _plan_directory(cfg, index, rid, abspath, entry, "input", items, dirs)
                else:
                    recorded_sha = entry.get("sha256") if entry else None
                    items.append(_consider_file(cfg, index, rid, abspath, "input", recorded_sha))
    if scope in ("all", "outputs"):
        for spec in read_outputs(record_dir):
            if _is_uri(spec):
                items.append(_keep_item(spec, "output", "remote: not deleted by slate"))
                continue
            _plan_output(cfg, index, rid, record_dir, out_dir, spec, outcome, items)


def _plan_dataset(cfg, index, rid, record_dir, items, dirs):
    from . import datasets

    if datasets.rebuildable(cfg, rid) != "yes":
        items.append(_keep_item(rid, "output", "dataset not rebuildable"))
        return
    dirs_map = dict(scan.record_dirs(cfg))
    for user in sorted(datasets.usage_index(cfg).get(rid, set())):
        udir = dirs_map.get(user)
        if (
            udir
            and not records.get_field(_safe_read(os.path.join(udir, "README.md")), "marked").strip()
        ):
            items.append(_keep_item(rid, "output", f"used by unmarked record {user}"))
            return
    man = datasets.read_manifest(record_dir) or {}
    for f in man.get("files", []):
        items.append(_consider_file(cfg, index, rid, f.get("path"), "output", f.get("sha256")))


def _plan_record(cfg, index, rid, record_dir):
    kind = record_kind(record_dir)
    scope = _mark_scope(_safe_read(os.path.join(record_dir, "README.md")))
    items, dirs = [], []
    if kind == "dataset":
        _plan_dataset(cfg, index, rid, record_dir, items, dirs)
    else:
        out_dir = out_dir_for(cfg, record_dir, rid)
        prov = _load_provenance(record_dir)
        outcome = _read_json(os.path.join(record_dir, "outcome.json")) or {}
        _plan_experiment(cfg, index, rid, record_dir, scope, out_dir, prov, outcome, items, dirs)
    freed = sum((it["size"] or 0) for it in items if it["action"] == "delete")
    return {
        "id": rid,
        "kind": kind,
        "scope": scope,
        "items": items,
        "dirs": dirs,
        "freed_bytes": freed,
    }


def _marked_records(cfg, arc=None):
    out = []
    for rid, d in scan.record_dirs(cfg):
        marked = records.get_field(_safe_read(os.path.join(d, "README.md")), "marked").strip()
        if not marked:
            continue
        # --arc restricts to that arc's experiments; datasets are not arc-scoped.
        if arc and (record_kind(d) != "experiment" or not rid.startswith(arc + "/")):
            continue
        out.append((rid, d))
    return out


def sweep(cfg, arc=None, confirm=False):
    """Build the sweep plan (pure data); unlink only when ``confirm`` is True."""
    index = _protection_index(cfg)
    record_plans = [_plan_record(cfg, index, rid, d) for rid, d in _marked_records(cfg, arc)]
    plan = {"records": record_plans, "total_bytes": sum(r["freed_bytes"] for r in record_plans)}
    plan["token"] = _plan_token(plan)
    if confirm:
        _execute_sweep(cfg, plan)
    return plan


def _plan_token(plan):
    """A short digest of exactly which files the plan deletes.

    The dry run prints it and a confirmed sweep must present it, so what is
    deleted is what was shown: a mark added or a file changed in between
    yields a different plan, a different token, and no deletion.
    """
    doomed = sorted(
        f"{it['path']}\0{it['sha256']}"
        for rec in plan["records"]
        for it in rec["items"]
        if it["action"] == "delete"
    )
    return util.sha256_text("\n".join(doomed))[:12]


def sweep_confirmed(cfg, arc, token):
    """Execute the sweep only if today's plan is the plan the token was issued for."""
    plan = sweep(cfg, arc=arc, confirm=False)
    if token != plan["token"]:
        plan["refused"] = "the plan changed since the dry run (or the token is wrong)"
        return plan
    _execute_sweep(cfg, plan)
    return plan


def _safe_mtime_ns(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def _execute_sweep(cfg, plan):
    """Unlink every planned file one at a time; rmdir only empty declared dirs."""
    dirs_map = dict(scan.record_dirs(cfg))
    for rec in plan["records"]:
        record_dir = dirs_map.get(rec["id"])
        if not record_dir:
            continue
        removed = []
        rec["failed"] = []
        for it in rec["items"]:
            if it["action"] != "delete":
                continue
            # The hash was checked when the plan was built; a file touched
            # since then is no longer the file that was approved.
            if _safe_size(it["path"]) != it["size"] or _safe_mtime_ns(it["path"]) != it["mtime_ns"]:
                rec["failed"].append({"path": it["path"], "error": "changed since the plan"})
                continue
            try:
                os.unlink(it["path"])  # one file at a time; never shutil.rmtree
            except OSError as exc:
                rec["failed"].append({"path": it["path"], "error": str(exc)})
                continue
            removed.append({"path": it["path"], "size": it["size"], "sha256": it["sha256"]})
        for d in rec["dirs"]:
            if d["action"] == "rmdir":
                try:
                    os.rmdir(d["path"])  # fails unless empty; never recursive
                except OSError:
                    pass
        date = util.today_str()
        total_bytes = sum((r["size"] or 0) for r in removed)
        tombstone = os.path.join(record_dir, "cleaned.json")
        history = (_read_json(tombstone) or {}).get("sweeps", [])
        history.append({"date": date, "scope": rec["scope"], "removed": removed})
        util.atomic_write(tombstone, json.dumps({"sweeps": history}, indent=2))
        records.apply_update(
            os.path.join(record_dir, "README.md"),
            {"cleaned": f"{date} {rec['scope']} {len(removed)} {total_bytes}", "marked": ""},
        )


def format_sweep(plan, confirmed):
    """Render the sweep plan as printable lines (dry run or confirmed)."""
    lines = []
    if not plan["records"]:
        lines.append("sweep: no marked records")
        return lines
    verb = "removed" if confirmed else "would remove"
    for rec in plan["records"]:
        lines.append(f"# {rec['id']}  [{rec['scope']}]")
        for it in rec["items"]:
            size = "-" if it["size"] is None else str(it["size"])
            if it["action"] == "delete":
                lines.append(f"  DELETE {it['path']}  ({size}B)")
            else:
                lines.append(f"  keep   {it['path']}  — {it['reason']}")
        for d in rec["dirs"]:
            if d["action"] == "rmdir":
                lines.append(f"  RMDIR  {d['path']}  (if empty)")
            else:
                lines.append(f"  keep   {d['path']}/  — {d['reason']}")
        for bad in rec.get("failed", []):
            lines.append(f"  FAILED {bad['path']}  — {bad['error']} (not deleted)")
        lines.append(f"  {verb}: {rec['freed_bytes']}B")
    total = plan["total_bytes"]
    lines.append(f"total {'freed' if confirmed else 'to free'}: {total}B")
    if plan.get("refused"):
        lines.append(f"REFUSED: {plan['refused']}. Nothing deleted; run the dry run again.")
    elif not confirmed:
        lines.append(
            f"dry run: nothing deleted. To delete exactly these files: --confirm {plan['token']}"
        )
    return lines


# --------------------------------------------------------------------------
# exp rerun: reproduce a concluded experiment against its recorded state
# --------------------------------------------------------------------------


def _inputs_changed(cfg, prov):
    """Return the input specs that no longer match their recorded hash."""
    cap = cfg.max_hash_mb * 1024 * 1024
    changed = []
    for entry in prov.get("inputs", []):
        kind = entry.get("kind")
        if kind == "file":
            path = entry.get("path")
            try:
                st = os.stat(path)
            except OSError:
                changed.append(entry["input"])
                continue
            if entry.get("size") is not None and st.st_size != entry["size"]:
                changed.append(entry["input"])
                continue
            rec_sha = entry.get("sha256")
            if rec_sha is not None:
                cur = util.sha256_file(path) if st.st_size <= cap else None
                if cur != rec_sha:
                    changed.append(entry["input"])
        elif kind == "directory":
            base = entry.get("path")
            if not os.path.isdir(base):
                changed.append(entry["input"])
                continue
            _files, tree = _dir_tree(base)
            if entry.get("tree_hash") is not None and tree != entry["tree_hash"]:
                changed.append(entry["input"])
        elif kind == "dataset":
            from . import datasets

            try:
                _did, ddir = datasets.find(cfg, entry.get("dataset"))
            except records.IdError:
                changed.append(entry["input"])
                continue
            if datasets.manifest_sha256(ddir) != entry.get("manifest_sha256"):
                changed.append(entry["input"])
    return changed


def _reconstruct(cfg, record_dir, prov, tmp):
    """Rebuild each covered repo (except the KB) under ``tmp``. Returns (lines, ok)."""
    lines = []
    ok = True
    repos = prov.get("repos", {})
    for name, path in cfg.covered_repos(_record_arc(record_dir)):
        if cfg.is_kb_path(path):
            continue  # the KB holds the record, not the code under test
        rec = repos.get(name)
        if not rec or not rec.get("head"):
            lines.append(f"reconstruct: no recorded state for {name}")
            ok = False
            continue
        sha = rec["head"]
        dest = os.path.join(tmp, name)
        clone = gitstate.clone_shared(os.path.abspath(path), dest)
        if clone.returncode != 0:
            lines.append(f"reconstruct: clone failed for {name}")
            ok = False
            continue
        if gitstate.git(dest, "checkout", "--detach", sha).returncode != 0:
            lines.append(f"reconstruct: checkout {sha[:12]} failed for {name}")
            ok = False
            continue
        patch = os.path.join(record_dir, "dirty", f"{name}.patch")
        if os.path.isfile(patch):
            if gitstate.git(dest, "apply", os.path.abspath(patch)).returncode != 0:
                lines.append(f"reconstruct: dirty patch failed to apply for {name}")
                ok = False
                continue
            lines.append(f"reconstruct: {name} @ {sha[:12]} + dirty patch")
        else:
            lines.append(f"reconstruct: {name} @ {sha[:12]}")
    return lines, ok


def _build_rerun_readme(orig_text, new_id, arc, date, orig_id):
    """Plan text from the original record + fresh after-sections; frontmatter reset."""
    marker = "\n## Result"
    template = util.load_template("experiment.md")
    o_idx = orig_text.find(marker)
    t_idx = template.find(marker)
    if o_idx != -1 and t_idx != -1:
        text = orig_text[:o_idx] + template[t_idx:]
    else:
        text = orig_text
    return records.update_fields(
        text,
        {
            "id": new_id,
            "arc": arc,
            "created": date,
            "status": "planned",
            "started": "",
            "finished": "",
            "outcome": "",
            "conclusion": "",
            "promoted_to": "",
            "measures": "",
            "marked": "",
            "cleaned": "",
            "tracker": "",
            "rerun_of": orig_id,
        },
    )


def _make_rerun_record(cfg, eid, record_dir):
    arc, _, leaf = eid.partition("/")
    parts = leaf.split("-", 3)
    slug = parts[3] if len(parts) == 4 else leaf
    date = util.today_str()
    base = f"{date}-{slug}-rerun"
    exp_root = os.path.join(cfg.arc_dir(arc), "experiments")
    new_leaf = base
    n = 2
    while os.path.exists(os.path.join(exp_root, new_leaf)):
        new_leaf = f"{base}-{n}"
        n += 1
    new_dir = os.path.join(exp_root, new_leaf)
    os.makedirs(os.path.join(new_dir, "results"), exist_ok=True)
    new_id = f"{arc}/{new_leaf}"
    orig_text = records.read(os.path.join(record_dir, "README.md"))
    records.write(
        os.path.join(new_dir, "README.md"), _build_rerun_readme(orig_text, new_id, arc, date, eid)
    )
    for name in ("run.sh", "inputs.txt", "outputs.txt"):
        src = os.path.join(record_dir, name)
        dest = os.path.join(new_dir, name)
        if os.path.exists(src):
            shutil.copy2(src, dest)
        else:
            records.write(dest, "")
    os.chmod(os.path.join(new_dir, "run.sh"), 0o755)
    return new_id, new_dir


def _first_existing(detail):
    for f in detail.get("files", []):
        ap = f.get("abspath")
        if ap and os.path.exists(ap):
            return ap
    return None


def _reproduced_verdict(verdicts):
    comparable = [v for v in verdicts if v != "remote"]
    if not comparable:
        return "no"
    good = [v for v in comparable if v in ("identical", "equivalent")]
    if len(good) == len(comparable):
        return "yes"
    if not good:
        return "no"
    return "partly"


def _compare_rerun(cfg, orig_id, orig_dir, new_dir):
    orig_outcome = _read_json(os.path.join(orig_dir, "outcome.json")) or {}
    new_outcome = _read_json(os.path.join(new_dir, "outcome.json")) or {}
    orig_by = {d.get("output"): d for d in orig_outcome.get("outputs", [])}
    new_by = {d.get("output"): d for d in new_outcome.get("outputs", [])}
    compare_sh = os.path.join(orig_dir, "compare.sh")
    has_compare = os.path.isfile(compare_sh) and os.access(compare_sh, os.X_OK)
    rows = []
    verdicts = []
    for spec in read_outputs(new_dir):
        if _is_uri(spec):
            rows.append({"output": spec, "verdict": "remote"})
            verdicts.append("remote")
            continue
        o = orig_by.get(spec, {})
        n = new_by.get(spec, {})
        o_shas = sorted(f.get("sha256") for f in o.get("files", []) if f.get("sha256"))
        n_shas = sorted(f.get("sha256") for f in n.get("files", []) if f.get("sha256"))
        if o_shas and o_shas == n_shas:
            verdict = "identical"
        else:
            verdict = "different"
            if has_compare:
                of = _first_existing(o)
                nf = _first_existing(n)
                if of and nf:
                    cp = subprocess.run(
                        [compare_sh, of, nf], cwd=orig_dir, capture_output=True, text=True
                    )
                    if cp.returncode == 0:
                        verdict = "equivalent"
        rows.append(
            {
                "output": spec,
                "verdict": verdict,
                "original_sha256": o_shas[0] if o_shas else None,
                "new_sha256": n_shas[0] if n_shas else None,
            }
        )
        verdicts.append(verdict)
    return {"original": orig_id, "reproduced": _reproduced_verdict(verdicts), "outputs": rows}


def rerun(cfg, given, force=False, keep=False):
    """Reproduce a concluded experiment against its recorded state.

    Returns (code, lines): 0 on a completed rerun, 1 on a refusal.
    """
    eid, record_dir = scan.find_experiment(cfg, given)
    lines = []
    prov = _load_provenance(record_dir)

    if prov.get("reconstructable") == "no":
        reasons = prov.get("reconstructable_reasons", [])
        if not force:
            lines.append(f"refusing: {eid} is reconstructable: no (use --force to override)")
            lines.extend(f"  - {r}" for r in reasons)
            return 1, lines
        lines.append("forced despite reconstructable: no:")
        lines.extend(f"  - {r}" for r in reasons)

    changed = _inputs_changed(cfg, prov)
    if changed:
        lines.append(
            "refusing: a declared input changed since the record — that is a new experiment"
        )
        lines.extend(f"  - {c}" for c in changed)
        return 1, lines

    tmp = tempfile.mkdtemp(prefix="slate-rerun-")
    try:
        recon_lines, ok = _reconstruct(cfg, record_dir, prov, tmp)
        lines.extend(recon_lines)
        if not ok:
            lines.append("refusing: could not reconstruct the recorded state")
            return 1, lines
        new_id, new_dir = _make_rerun_record(cfg, eid, record_dir)
        lines.append(f"rerun record: {new_id}")
        _code, run_lines = start(cfg, new_dir, detach=False, repos_root=tmp)
        lines.extend(run_lines)
        result = _compare_rerun(cfg, eid, record_dir, new_dir)
        util.atomic_write(os.path.join(new_dir, "rerun.json"), json.dumps(result, indent=2))
        lines.append(f"reproduced: {result['reproduced']}")
        for row in result["outputs"]:
            lines.append("  {}: {}".format(row["output"], row["verdict"]))
        return 0, lines
    finally:
        if keep:
            lines.append(f"kept reconstructed tree: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)  # slate created this temp dir

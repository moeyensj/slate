"""Dataset records: a thin layer over the experiment run engine.

A dataset uses the *same* engine as an experiment — the ``check``, ``start``
and ``harvest`` in :mod:`slate.runs`, which behave slightly differently by
reading the record's kind from its frontmatter. This module adds only what is
dataset-specific and has no engine of its own: creation from the dataset
template, adopting files that already exist, the ``MANIFEST.json``, the
computed ``rebuildable`` flag, ``dataset:<slug>`` input resolution, and the
list/show/verify views.
"""

from __future__ import annotations

import hashlib
import json
import os

from . import records, runs, scan, util

PREFIX = scan.DATASET_PREFIX  # "dataset:"
BUILT = "built"


# --------------------------------------------------------------------------
# ids and resolution
# --------------------------------------------------------------------------


def is_spec(spec):
    return spec.startswith(PREFIX)


def slug_of(spec):
    """The bare slug of a ``dataset:<slug>`` spec or a full ``dataset:<slug>`` id."""
    return spec[len(PREFIX) :] if spec.startswith(PREFIX) else spec


def find(cfg, given):
    return scan.find_dataset(cfg, given)


def status(record_dir):
    return scan.record_field(record_dir, "status")


# --------------------------------------------------------------------------
# MANIFEST.json (paths are absolute so an adopted file anywhere is unambiguous)
# --------------------------------------------------------------------------


def manifest_path(record_dir):
    return os.path.join(record_dir, "MANIFEST.json")


def read_manifest(record_dir):
    return runs._read_json(manifest_path(record_dir))


def manifest_sha256(record_dir):
    try:
        return util.sha256_file(manifest_path(record_dir))
    except OSError:
        return None


def _tree_hash(files):
    h = hashlib.sha256()
    for f in files:
        h.update("{}\0{}\n".format(f["path"], f["sha256"]).encode("utf-8"))
    return h.hexdigest()


def write_manifest(record_dir, files):
    """Write ``MANIFEST.json`` for a list of ``{path, size, sha256}`` (absolute paths)."""
    files = sorted(files, key=lambda f: f["path"])
    data = {"files": files, "tree_hash": _tree_hash(files), "built": util.today_str()}
    util.atomic_write(manifest_path(record_dir), json.dumps(data, indent=2))
    return data


def write_manifest_from_outputs(record_dir, results):
    """Build a manifest from harvested output details (each file's abspath+sha256)."""
    files = []
    for detail in results:
        for f in detail.get("files", []):
            ap = f.get("abspath") or os.path.normpath(os.path.join(record_dir, f.get("path", "")))
            files.append({"path": ap, "size": f.get("size"), "sha256": f.get("sha256")})
    return write_manifest(record_dir, files)


# --------------------------------------------------------------------------
# verification (full rehash for `data verify`; cache-based for input checks)
# --------------------------------------------------------------------------


def verify(cfg, record_dir):
    """Rehash every manifest file. Returns (ok, differences, count, bytes)."""
    man = read_manifest(record_dir)
    if not man:
        return False, ["no manifest"], 0, 0
    cap = cfg.max_hash_mb * 1024 * 1024
    diffs = []
    total = 0
    files = man.get("files", [])
    for f in files:
        path = f["path"]
        total += f.get("size") or 0
        try:
            st = os.stat(path)
        except OSError:
            diffs.append(f"missing: {path}")
            continue
        if f.get("size") is not None and st.st_size != f["size"]:
            diffs.append(f"size changed: {path}")
            continue
        rec = f.get("sha256")
        if rec is None:
            continue  # recorded without a hash: cannot verify, so never a difference
        cur = util.sha256_file(path) if st.st_size <= cap else None
        if cur != rec:
            diffs.append(f"hash changed: {path}")
    return (not diffs), diffs, len(files), total


def matches_via_cache(cfg, record_dir, cache):
    """Cheap match against the manifest using the hash cache (size/mtime, rehash on miss)."""
    man = read_manifest(record_dir)
    if not man:
        return False, ["no manifest"]
    cap = cfg.max_hash_mb * 1024 * 1024
    diffs = []
    for f in man.get("files", []):
        path = f["path"]
        if not os.path.exists(path):
            diffs.append(f"missing: {path}")
            continue
        rec = f.get("sha256")
        if rec is None:
            continue
        cur, _unhashed = cache.sha256(path, cap)
        if cur != rec:
            diffs.append(f"hash changed: {path}")
    return (not diffs), diffs


def manifest_matches(cfg, record_dir, rehash=False):
    """Whether the files still match the manifest (full rehash when ``rehash``)."""
    if rehash:
        ok, _diffs, _n, _b = verify(cfg, record_dir)
        return ok
    man = read_manifest(record_dir)
    if not man:
        return False
    for f in man.get("files", []):
        try:
            st = os.stat(f["path"])
        except OSError:
            return False
        if f.get("size") is not None and st.st_size != f["size"]:
            return False
    return True


# --------------------------------------------------------------------------
# rebuildable (computed, never typed)
# --------------------------------------------------------------------------


def rebuildable(cfg, dataset_id, _seen=None):
    """`yes` when the record has a recipe, its verdict is yes, and every
    ``dataset:`` parent is itself rebuildable or still matches its manifest."""
    _seen = _seen or set()
    if dataset_id in _seen:
        return "no"  # cycle guard
    _seen = _seen | {dataset_id}
    try:
        _did, record_dir = find(cfg, dataset_id)
    except records.IdError:
        return "no"
    if not runs.has_recipe(record_dir):
        return "no"
    if runs._load_provenance(record_dir).get("reconstructable") != "yes":
        return "no"
    for line in runs.read_inputs(record_dir):
        _arm, spec = runs._parse_arm(line)
        if not is_spec(spec):
            continue
        parent_id = PREFIX + slug_of(spec)
        if rebuildable(cfg, parent_id, _seen) == "yes":
            continue
        try:
            _pid, pdir = find(cfg, parent_id)
        except records.IdError:
            return "no"
        ok, _diffs, _n, _b = verify(cfg, pdir)
        if not ok:
            return "no"
    return "yes"


# --------------------------------------------------------------------------
# usage index and lineage
# --------------------------------------------------------------------------


def usage_index(cfg):
    """Map a dataset id to the set of record ids that declare it as an input."""
    index = {}
    for rid, d in scan.record_dirs(cfg):
        for line in runs.read_inputs(d):
            _arm, spec = runs._parse_arm(line)
            if is_spec(spec):
                index.setdefault(PREFIX + slug_of(spec), set()).add(rid)
    return index


def parents(cfg, dataset_id, _seen=None):
    """The transitive list of ``dataset:`` ancestors, discovery order."""
    _seen = _seen or set()
    out = []
    try:
        _did, record_dir = find(cfg, dataset_id)
    except records.IdError:
        return out
    for line in runs.read_inputs(record_dir):
        _arm, spec = runs._parse_arm(line)
        if not is_spec(spec):
            continue
        pid = PREFIX + slug_of(spec)
        if pid in _seen:
            continue
        _seen.add(pid)
        out.append(pid)
        out.extend(parents(cfg, pid, _seen))
    return out


# --------------------------------------------------------------------------
# create / adopt
# --------------------------------------------------------------------------


def new(cfg, slug, title):
    """Create a planned dataset record from the template. Returns (id, dir)."""
    record_dir = cfg.dataset_dir(slug)
    if os.path.exists(record_dir):
        raise records.IdError(f"dataset already exists: {slug}")
    os.makedirs(os.path.join(record_dir, "results"), exist_ok=True)
    dsid = PREFIX + slug
    body = util.render(
        util.load_template("dataset.md"),
        {"id": dsid, "title": title, "date": util.today_str(), "tracker": ""},
    )
    records.write(os.path.join(record_dir, "README.md"), body)
    run_sh = os.path.join(record_dir, "run.sh")
    records.write(run_sh, "")
    os.chmod(run_sh, 0o755)
    records.write(os.path.join(record_dir, "inputs.txt"), "")
    records.write(os.path.join(record_dir, "outputs.txt"), "")
    return dsid, record_dir


def adopt(cfg, slug, title, paths):
    """Register an existing dataset: hash the files, mark it built, no recipe."""
    dsid, record_dir = new(cfg, slug, title)
    cap = cfg.max_hash_mb * 1024 * 1024
    files = []
    resolved = []
    for p in paths:
        ap = os.path.abspath(p)
        resolved.append(ap)
        st = os.stat(ap)
        sha = util.sha256_file(ap) if st.st_size <= cap else None
        files.append({"path": ap, "size": st.st_size, "sha256": sha})
    write_manifest(record_dir, files)
    # Record the adopted files as outputs so the files view and sweep see them.
    records.write(os.path.join(record_dir, "outputs.txt"), "".join(p + "\n" for p in resolved))
    records.write(os.path.join(record_dir, "inputs.txt"), "none\n")
    records.apply_update(
        os.path.join(record_dir, "README.md"),
        {"status": BUILT, "rebuildable": "no", "finished": util.now_ts()},
    )
    return dsid, record_dir


# --------------------------------------------------------------------------
# list / show / verify
# --------------------------------------------------------------------------


def listing(cfg):
    usage = usage_index(cfg)
    rows = []
    for did, d in scan.dataset_dirs(cfg):
        man = read_manifest(d) or {}
        files = man.get("files", [])
        rows.append(
            {
                "id": did,
                "status": status(d),
                "rebuildable": rebuildable(cfg, did),
                "files": len(files),
                "bytes": sum((f.get("size") or 0) for f in files),
                "used_by": len(usage.get(did, set())),
            }
        )
    return rows


def show(cfg, given):
    did, record_dir = find(cfg, given)
    return {
        "id": did,
        "status": status(record_dir),
        "rebuildable": rebuildable(cfg, did),
        "parents": parents(cfg, did),
        "dependents": sorted(usage_index(cfg).get(did, set())),
    }

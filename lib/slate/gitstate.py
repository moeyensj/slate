"""Read-only git helpers and repository snapshots.

Every git call goes through ``git -C <path>``; the process working directory
is never trusted. No history-changing command is ever run here.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
from collections import OrderedDict

from . import util


def git(repo: str, *args):
    """Run ``git -C repo <args>`` and return the completed process."""
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
    )


def clone_shared(src: str, dest: str):
    """Clone ``src`` into ``dest`` sharing objects, with no working tree checkout.

    ``--shared`` references the source's object store rather than copying it, so
    the clone is fast and read-only against the source; ``--no-checkout`` leaves
    the reconstruction (a later ``checkout`` of the recorded sha) to the caller.
    The source working tree is never modified.
    """
    return subprocess.run(
        ["git", "clone", "--shared", "--no-checkout", src, dest],
        capture_output=True,
        text=True,
    )


def is_repo(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    return git(path, "rev-parse", "--git-dir").returncode == 0


def head(repo: str) -> str:
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def branch(repo: str) -> str:
    """Current branch, or 'HEAD' when detached."""
    r = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if r.returncode == 0:
        return r.stdout.strip()
    return "HEAD"


def _porcelain_path(line: str) -> str:
    """Extract the path from a ``git status --porcelain`` line."""
    body = line[3:] if len(line) > 3 else line
    if " -> " in body:  # rename/copy: keep the destination
        body = body.split(" -> ", 1)[1]
    body = body.strip()
    if len(body) >= 2 and body[0] == '"' and body[-1] == '"':
        body = body[1:-1]
    return body


def dirty_count(repo: str, ignore_prefixes=None) -> int:
    """Count uncommitted entries; paths under ``ignore_prefixes`` are skipped."""
    r = git(repo, "status", "--porcelain")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if not ignore_prefixes:
        return len(lines)
    count = 0
    for ln in lines:
        path = _porcelain_path(ln)
        if any(path.startswith(p) for p in ignore_prefixes):
            continue
        count += 1
    return count


def pushed(repo: str) -> bool:
    """True when HEAD is contained in a remote-tracking branch (no network).

    ``False`` when there is no remote (nothing contains HEAD).
    """
    r = git(repo, "branch", "-r", "--contains", "HEAD")
    if r.returncode != 0:
        return False
    return bool(r.stdout.strip())


def upstream(repo: str):
    r = git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    return r.stdout.strip() if r.returncode == 0 else None


def ahead_behind(repo: str):
    """Return (ahead, behind) vs upstream, or (None, None) when there is none."""
    up = upstream(repo)
    if not up:
        return (None, None)
    r = git(repo, "rev-list", "--left-right", "--count", up + "...HEAD")
    if r.returncode != 0:
        return (None, None)
    parts = r.stdout.split()
    if len(parts) != 2:
        return (None, None)
    behind, ahead = int(parts[0]), int(parts[1])
    return (ahead, behind)


def is_ancestor(repo: str, a: str, b: str) -> bool:
    return git(repo, "merge-base", "--is-ancestor", a, b).returncode == 0


def obj_exists(repo: str, sha: str) -> bool:
    return git(repo, "cat-file", "-e", sha + "^{commit}").returncode == 0


def branch_exists(repo: str, name: str) -> bool:
    return git(repo, "rev-parse", "--verify", "--quiet", name).returncode == 0


def default_branch(repo: str):
    r = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    for name in ("main", "master"):
        if branch_exists(repo, name):
            return name
    return None


def count_range(repo: str, a: str, b: str, path: str = None):
    """Count commits in ``a..b`` (optionally touching ``path``); None on error."""
    args = ["rev-list", "--count", a + ".." + b]
    if path:
        args += ["--", path]
    r = git(repo, *args)
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip() or 0)
    except ValueError:
        return None


def subjects(repo: str, a: str, b: str, cap: int):
    """Return (capped subject list, total count) for commits in ``a..b``."""
    r = git(repo, "log", "--format=%s", a + ".." + b)
    if r.returncode != 0:
        return (None, None)
    lines = r.stdout.splitlines()
    return (lines[:cap], len(lines))


def snapshot(cfg) -> dict:
    """Build the snapshot recorded in provenance and handoff state blocks."""
    repos = OrderedDict()
    kb_ignore = cfg.kb_dirty_ignore()
    for name, path in cfg.covered_repos():
        ahead, behind = ahead_behind(path)
        ignore = kb_ignore if cfg.is_kb_path(path) else None
        repos[name] = {
            "path": os.path.abspath(path),
            "branch": branch(path),
            "head": head(path),
            "dirty": dirty_count(path, ignore),
            "ahead": ahead,
            "behind": behind,
            "pushed": pushed(path),
        }
    tools = []
    for cmd in cfg.provenance_tools:
        tools.append({"cmd": cmd, "line": _tool_line(cmd, cfg.repos_root_dir)})
    return {
        "repos": repos,
        "host": util.host(),
        "user": util.user(),
        "time": util.now_ts(),
        "hardware": util.hardware(),
        "tools": tools,
        "env": {var: os.environ.get(var, "") for var in cfg.provenance_env},
        "binaries": _binaries(cfg),
        "lockfiles": _lockfiles(cfg),
    }


def _binaries(cfg) -> list:
    """Record path, size and sha256 of each configured executable."""
    out = []
    for spec in cfg.provenance_binaries:
        resolved = spec
        if not os.path.isabs(resolved):
            found = shutil.which(resolved)
            if found:
                resolved = found
        entry = {"spec": spec, "path": resolved}
        try:
            entry["size"] = os.path.getsize(resolved)
            entry["sha256"] = util.sha256_file(resolved)
        except OSError:
            entry["size"] = None
            entry["sha256"] = None
            entry["error"] = "not found"
        out.append(entry)
    return out


def _lockfiles(cfg) -> list:
    """Record the sha256 of each file matching a configured glob under repos.root."""
    out = []
    root = cfg.repos_root_dir
    for pattern in cfg.provenance_lockfiles:
        for match in sorted(glob.glob(os.path.join(root, pattern))):
            try:
                sha = util.sha256_file(match)
            except OSError:
                sha = None
            out.append({"path": os.path.relpath(match, root), "sha256": sha})
    return out


def _tool_line(cmd: str, cwd: str) -> str:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=30)
    except Exception as exc:  # pragma: no cover - defensive
        return f"error: {exc}"
    out = (p.stdout or p.stderr).splitlines()
    return out[0] if out else ""


def plan_commit(kb_repo: str, rel_path: str):
    """The KB commit that last touched ``rel_path``, or None.

    None when the file is untracked (uncommitted) or has uncommitted
    modifications (modified since).
    """
    if git(kb_repo, "ls-files", "--error-unmatch", "--", rel_path).returncode != 0:
        return None  # untracked
    st = git(kb_repo, "status", "--porcelain", "--", rel_path)
    if st.stdout.strip():
        return None  # modified since
    r = git(kb_repo, "log", "-1", "--format=%H", "--", rel_path)
    sha = r.stdout.strip()
    return sha or None


def write_patch(
    repo: str, dest: str, cap_bytes: int, preserve_bytes: int, ignore_prefixes=None
) -> dict:
    """Write the repo's uncommitted state as a single applyable patch.

    The patch is ``git diff HEAD --binary`` followed by a ``--no-index`` diff
    for every untracked, unignored file no larger than ``preserve_bytes``, so
    that applying it to a clean checkout recreates those files too. Larger
    untracked files are not embedded but returned in ``large_untracked``. Over
    ``cap_bytes`` nothing is written. Paths under ``ignore_prefixes`` are left
    out: slate's own records are not part of the state a run depends on, and a
    record must not embed a copy of itself.

    Returns ``{"written", "over_cap", "large_untracked", "embedded_untracked"}``.
    """
    parts = []
    ignore = list(ignore_prefixes or [])
    excludes = [f":(exclude){p.rstrip('/')}" for p in ignore]
    diff = git(repo, "diff", "HEAD", "--binary", "--", ".", *excludes).stdout
    if diff:
        parts.append(diff)
    large = []
    embedded = 0
    others = git(repo, "ls-files", "--others", "--exclude-standard", "-z").stdout
    for rel in [x for x in others.split("\0") if x]:
        if any(rel.startswith(p) for p in ignore):
            continue
        full = os.path.join(repo, rel)
        try:
            size = os.path.getsize(full)
        except OSError:
            continue
        if size <= preserve_bytes:
            # --no-index exits 1 when the files differ (always, here); the diff
            # is on stdout regardless. It recreates the file on apply.
            nd = git(repo, "diff", "--no-index", "--binary", os.devnull, rel).stdout
            if nd:
                parts.append(nd)
                embedded += 1
        else:
            try:
                sha = util.sha256_file(full)
            except OSError:
                sha = None
            large.append({"path": rel, "size": size, "sha256": sha})
    body = "".join(parts)
    encoded = body.encode("utf-8", "replace")
    over_cap = len(encoded) > cap_bytes
    written = 0
    if body and not over_cap:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(encoded)
        written = len(encoded)
    return {
        "written": written,
        "over_cap": over_cap,
        "large_untracked": large,
        "embedded_untracked": embedded,
    }

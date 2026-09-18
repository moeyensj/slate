"""Handoff notes: generate the mechanical sections, check, list and pick up.

The generated ``slate-state`` block is the snapshot that ``pickup`` later
verifies against the present.
"""

from __future__ import annotations

import json
import os
import re

from . import decisions, gitstate, records, runs, scan, util
from .tracker import TrackerError

_STATE_RE = re.compile(r"```json slate-state\n(.*?)\n```", re.DOTALL)
_ISSUE_RE = re.compile(r"\b[a-z][a-z0-9]*-[0-9a-z]{4,}\b")
HARD_STATES = ("diverged", "branch gone", "missing")


def extract_state(text):
    """Return the parsed slate-state dict from a note, or None."""
    m = _STATE_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def _section(text, heading):
    """Return the body of a ``## heading`` section."""
    pat = re.compile(rf"(?m)^## {re.escape(heading)}\s*\n(.*?)(?=\n## |\Z)", re.DOTALL)
    m = pat.search(text)
    return m.group(1).strip("\n") if m else ""


# --------------------------------------------------------------------------
# Generated sections
# --------------------------------------------------------------------------


def _running_section(cfg, arc):
    rows = []
    for eid, d in scan.experiment_dirs(cfg, arc):
        if scan.experiment_field(d, "status") != "running":
            continue
        run_json = runs._read_json(os.path.join(d, "run.json")) or {}
        start_epoch = run_json.get("start_epoch")
        if start_epoch is None:
            dt = util.parse_ts(scan.experiment_field(d, "started"))
            start_epoch = dt.timestamp() if dt else None
        elapsed = util.human_age(util.now_utc().timestamp() - start_epoch) if start_epoch else "?"
        rows.append(
            f"- {eid}  host={run_json.get('host', '?')} pid={run_json.get('pid', '?')} "
            f"elapsed={elapsed} log={run_json.get('log', '?')}"
        )
    return "\n".join(rows) if rows else "Nothing."


def _landed_section(cfg, arc):
    prev = _previous_handoff(cfg, arc)
    if not prev:
        return "First handoff for this arc."
    state = extract_state(records.read(prev[1])) or {}
    prev_repos = state.get("repos", {})
    blocks = []
    for name, path in cfg.covered_repos():
        rec = prev_repos.get(name)
        if not rec:
            continue
        sha = rec.get("head")
        if not sha or not gitstate.obj_exists(path, sha):
            continue
        subjects, total = gitstate.subjects(path, sha, "HEAD", cfg.landed_cap)
        if not subjects:
            continue
        lines = [f"### {name}"] + [f"- {s}" for s in subjects]
        if total > len(subjects):
            lines.append(f"- ...and {total - len(subjects)} more")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "Nothing landed since the last handoff."


def _decisions_section(cfg, arc):
    open_entries = [e for e in decisions.queue(cfg, arc=arc) if e["fields"].get("status") == "open"]
    lines = []
    if open_entries:
        for e in open_entries:
            flag = " (blocking)" if e["fields"].get("blocking") == "yes" else ""
            lines.append("- {}{} {}".format(e["id"], flag, _truncate(e["question"], 80)))
    else:
        lines.append("No open decisions.")
    lines.append("")
    lines.append("Ruled, do not relitigate:")
    ruled = decisions.recent_rulings(cfg, arc, limit=5)
    if ruled:
        for e in ruled:
            lines.append("- {} {}".format(e["id"], _truncate(e["question"], 70)))
    else:
        lines.append("- (none yet)")
    return "\n".join(lines)


def _tracker_section(cfg, tracker, arc):
    if not tracker.enabled():
        return "No tracker."
    parent = records.get_field(records.read(scan.arc_readme(cfg, arc)), "tracker")
    try:
        issues = tracker.open_under(parent, cap=15)
    except TrackerError as exc:
        return f"tracker error: {exc}"
    if not issues:
        return "No open issues under the arc epic."
    lines = []
    for issue in issues:
        lines.append(
            "- {} [{}] {}".format(
                issue.get("id", "?"), issue.get("status", "?"), issue.get("title", "")
            )
        )
    return "\n".join(lines)


def _truncate(text, n):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# --------------------------------------------------------------------------
# handoff new
# --------------------------------------------------------------------------


def _previous_handoff(cfg, arc, before_id=None):
    """Newest existing handoff for an arc (optionally strictly before an id)."""
    files = scan.handoff_files(cfg, arc)
    if before_id:
        files = [(hid, p) for hid, p in files if hid < before_id]
    return files[-1] if files else None


def _newest_open(cfg, arc):
    open_notes = [
        (hid, p)
        for hid, p in scan.handoff_files(cfg, arc)
        if records.get_field(records.read(p), "status") == "open"
    ]
    return open_notes[-1] if open_notes else None


def _allocate_id(cfg, arc, date):
    same_day = [
        hid
        for hid, _ in scan.handoff_files(cfg, arc)
        if hid == f"{arc}/{date}" or hid.startswith(f"{arc}/{date}-")
    ]
    n = len(same_day) + 1
    return date if n == 1 else f"{date}-{n}"


def new(cfg, tracker, arc, private=False, session=None):
    """Write a handoff note. Returns (id, path). Supersedes/closes prior open note."""
    date = util.today_str()
    name = _allocate_id(cfg, arc, date)
    hid = f"{arc}/{name}"
    if private:
        hdir = os.path.join(cfg.root, scan.PRIVATE_DIR, arc, "handoffs")
        _ensure_private_ignored(cfg)
    else:
        hdir = os.path.join(cfg.arc_dir(arc), "handoffs")
    os.makedirs(hdir, exist_ok=True)
    path = os.path.join(hdir, name + ".md")

    prior_open = _newest_open(cfg, arc)
    supersedes = prior_open[0] if prior_open else ""

    snap = gitstate.snapshot(cfg)
    mapping = {
        "id": hid,
        "arc": arc,
        "host": util.host(),
        "session": session or "",
        "supersedes": supersedes,
        "timestamp": util.now_ts(),
        "arc_title": scan.arc_title(cfg, arc),
        "date": date,
        "directive": scan.arc_directive(cfg, arc),
        "running": _running_section(cfg, arc),
        "landed": _landed_section(cfg, arc),
        "decisions": _decisions_section(cfg, arc),
        "tracker_items": _tracker_section(cfg, tracker, arc),
        "state": json.dumps(snap, indent=2),
    }
    body = util.render(util.load_template("handoff.md"), mapping)
    records.write(path, body)

    if prior_open:
        records.apply_update(
            prior_open[1],
            {
                "status": "closed",
                "closed": f"{util.now_ts()} superseded",
            },
        )
    return hid, path


def _ensure_private_ignored(cfg):
    gi = os.path.join(cfg.root, ".gitignore")
    line = scan.PRIVATE_DIR + "/"
    existing = ""
    if os.path.isfile(gi):
        existing = records.read(gi)
        if any(item.strip() == line for item in existing.splitlines()):
            return
    with open(gi, "a", encoding="utf-8") as fh:
        if existing and not existing.endswith("\n"):
            fh.write("\n")
        fh.write(line + "\n")


# --------------------------------------------------------------------------
# check / close / list
# --------------------------------------------------------------------------


def check(cfg, given):
    """Return (ok, lines) for a handoff note."""
    hid, path = scan.find_handoff(cfg, given)
    text = records.read(path)
    lines = []
    ok = True
    if records.has_fill(text):
        ok = False
        lines.append("FAIL: a slate:fill marker remains")
    count = len(text.splitlines())
    if count > cfg.max_lines:
        ok = False
        lines.append(f"FAIL: {count} lines exceeds handoff.max_lines={cfg.max_lines}")
    if ok:
        lines.append(f"ok: {hid} ({count} lines)")
    return ok, lines


def close(cfg, given, reason=None):
    """Close a handoff note; return its id."""
    hid, path = scan.find_handoff(cfg, given)
    stamp = util.now_ts() + ((" " + reason) if reason else "")
    records.apply_update(path, {"status": "closed", "closed": stamp})
    return hid


def listing(cfg, show_all=False):
    """Return handoff rows (open or all), oldest first."""
    rows = []
    for hid, path in scan.handoff_files(cfg):
        text = records.read(path)
        status = records.get_field(text, "status")
        if not show_all and status != "open":
            continue
        created = records.get_field(text, "created")
        dt = util.parse_ts(created)
        age = util.human_age(util.now_utc().timestamp() - dt.timestamp()) if dt else "?"
        first_action = _section(text, "First action")
        first_line = ""
        for ln in first_action.splitlines():
            if ln.strip():
                first_line = ln.strip()
                break
        rows.append(
            {
                "id": hid,
                "age": age,
                "status": status,
                "created": created,
                "first_action": _truncate(first_line, 60),
            }
        )
    rows.sort(key=lambda r: (r["created"] or "", r["id"]))
    return rows


# --------------------------------------------------------------------------
# pickup
# --------------------------------------------------------------------------


def _repo_state(cfg, name, rec):
    path = rec.get("path") or os.path.join(cfg.repos_root_dir, name)
    if not gitstate.is_repo(path):
        return "missing"
    cur_head = gitstate.head(path)
    ignore = cfg.kb_dirty_ignore() if cfg.is_kb_path(path) else None
    cur_dirty = gitstate.dirty_count(path, ignore)
    rec_head = rec.get("head")
    rec_branch = rec.get("branch")
    rec_dirty = rec.get("dirty")
    if cur_head == rec_head:
        if rec_dirty is not None and cur_dirty != rec_dirty:
            return "dirty changed"
        return "same"
    if rec_head and gitstate.obj_exists(path, rec_head):
        if gitstate.is_ancestor(path, rec_head, cur_head):
            n = gitstate.count_range(path, rec_head, cur_head) or 0
            return f"advanced {n}"
        default = gitstate.default_branch(path)
        if default and gitstate.is_ancestor(path, rec_head, default):
            return "branch merged"
    if rec_branch and not gitstate.branch_exists(path, rec_branch):
        return "branch gone"
    return "diverged"


def _issue_ids(text):
    section = _section(text, "Tracker")
    return sorted(set(_ISSUE_RE.findall(section)))


def pickup(cfg, tracker, given, dry=False, session=None):
    """Verify a note against the present. Returns (exit_code, lines)."""
    hid, path = scan.find_handoff(cfg, given)
    text = records.read(path)
    state = extract_state(text)
    lines = []
    exit_code = 0

    # Claim warning: picked up recently by a different session.
    prior = records.get_field(text, "picked_up")
    if prior:
        parts = prior.split()
        dt = util.parse_ts(parts[0]) if parts else None
        prior_session = parts[2] if len(parts) >= 3 else ""
        if dt:
            hours = (util.now_utc().timestamp() - dt.timestamp()) / 3600.0
            if hours < cfg.claim_hours and prior_session and prior_session != (session or ""):
                lines.append(f"WARNING: picked up {hours:.1f}h ago by session {prior_session}")

    if not state:
        lines.append("WARNING: no slate-state block; repository check did not run")
    else:
        for name, rec in state.get("repos", {}).items():
            st = _repo_state(cfg, name, rec)
            lines.append(f"{name}: {st}")
            if st in HARD_STATES:
                exit_code = 1

    # Tracker issues named in the note that have closed since.
    ids = _issue_ids(text)
    if tracker.enabled() and ids:
        try:
            closed = tracker.closed_among(ids)
        except TrackerError as exc:
            lines.append(f"tracker error: {exc}")
            closed = []
        for cid in closed:
            lines.append(f"closed since: {cid}")

    # Harvest running experiments for the arc.
    arc = records.get_field(text, "arc")
    for row in _harvest_arc(cfg, arc):
        lines.append("harvest: " + row)

    if not dry:
        stamp = "{} {} {}".format(util.now_ts(), util.host(), session or "")
        records.apply_update(path, {"status": "picked-up", "picked_up": stamp})

    lines.append(path)
    return exit_code, lines


def _harvest_arc(cfg, arc):
    rows = []
    for eid, d in scan.experiment_dirs(cfg, arc):
        if scan.experiment_field(d, "status") == "running":
            rows.append(runs._harvest_one(cfg, eid, d))
    return rows

"""Arc creation, listing, timeline and close, plus shared per-arc counts."""

from __future__ import annotations

import os

from . import decisions, gitstate, records, scan, util
from .tracker import TrackerError


def open_handoffs(cfg, arc):
    return [
        (hid, p)
        for hid, p in scan.handoff_files(cfg, arc)
        if records.get_field(records.read(p), "status") == "open"
    ]


def running_experiments(cfg, arc):
    return [
        (eid, d)
        for eid, d in scan.experiment_dirs(cfg, arc)
        if scan.experiment_field(d, "status") == "running"
    ]


def open_decisions(cfg, arc):
    return [e for e in decisions.load_all(cfg, arc=arc) if e["fields"].get("status") == "open"]


def _default_by(cfg):
    r = gitstate.git(cfg.root, "config", "user.name")
    name = r.stdout.strip() if r.returncode == 0 else ""
    return name or "owner"


def new(cfg, tracker, slug, title, directive, by=None, epic=None):
    """Create an arc folder. Returns (slug, arc_dir, tracker_error)."""
    arc_dir = cfg.arc_dir(slug)
    if os.path.exists(arc_dir):
        raise records.IdError(f"arc already exists: {slug}")
    os.makedirs(os.path.join(arc_dir, "experiments"), exist_ok=True)
    os.makedirs(os.path.join(arc_dir, "handoffs"), exist_ok=True)
    date = util.today_str()
    by = by or _default_by(cfg)
    tracker_id = epic or ""
    mapping = {
        "id": slug,
        "title": title,
        "directive": directive,
        "by": by,
        "date": date,
        "tracker": tracker_id,
    }
    records.write(
        os.path.join(arc_dir, "README.md"), util.render(util.load_template("arc.md"), mapping)
    )
    records.write(
        os.path.join(arc_dir, "rulings.md"),
        util.render(util.load_template("rulings.md"), {"title": title}),
    )

    tracker_err = None
    if tracker.enabled() and not epic:
        try:
            issue = tracker.create_epic(title)
            if issue:
                records.apply_update(os.path.join(arc_dir, "README.md"), {"tracker": issue})
        except TrackerError as exc:
            tracker_err = str(exc)
    return slug, arc_dir, tracker_err


def listing(cfg):
    """Return one row per arc with status and open/running/open counts."""
    rows = []
    for slug, adir in scan.arc_dirs(cfg):
        text = records.read(os.path.join(adir, "README.md"))
        rows.append(
            {
                "id": slug,
                "status": records.get_field(text, "status"),
                "open_handoffs": len(open_handoffs(cfg, slug)),
                "running": len(running_experiments(cfg, slug)),
                "open_decisions": len(open_decisions(cfg, slug)),
            }
        )
    return rows


def timeline(cfg, slug):
    """Return date-ordered timeline items for one arc."""
    items = []
    for eid, d in scan.experiment_dirs(cfg, slug):
        text = records.read(os.path.join(d, "README.md"))
        date = records.get_field(text, "created") or eid.split("/")[1][:10]
        leaf = eid.split("/")[1]
        status = records.get_field(text, "status")
        title = records.get_field(text, "title")
        items.append((date, "experiment", f"{leaf} [{status}] {title}"))
    for hid, p in scan.handoff_files(cfg, slug):
        text = records.read(p)
        date = records.get_field(text, "created") or hid.split("/")[1][:10]
        leaf = hid.split("/")[1]
        status = records.get_field(text, "status")
        items.append((date, "handoff", f"{leaf} [{status}]"))
    for e in decisions.load_all(cfg, arc=slug, include_ruled=True):
        date = e["fields"].get("asked", "")
        status = e["fields"].get("status", "")
        items.append((date, "decision", f"{e['id']} [{status}] {_short(e['question'])}"))
    items.sort(key=lambda it: (it[0], it[1]))
    return items


def _short(text, n=60):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def close(cfg, slug):
    """Close an arc unless it has an open handoff or a running experiment.

    Returns (ok, message).
    """
    if open_handoffs(cfg, slug):
        return False, f"arc {slug} has an open handoff; close it first"
    if running_experiments(cfg, slug):
        return False, f"arc {slug} has a running experiment; harvest it first"
    records.apply_update(os.path.join(cfg.arc_dir(slug), "README.md"), {"status": "closed"})
    return True, f"closed {slug}"

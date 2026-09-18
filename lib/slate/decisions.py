"""The decision queue: parse, append and rule entries in ``rulings.md``.

An entry is a ``## D-NNN`` heading, a flat ``- key: value`` list, a
``**Question.**`` paragraph, and (once ruled) a ``**Ruling.**`` blockquote.
Entries are appended, never deleted, and rulings are stored verbatim.
"""

from __future__ import annotations

import os
import re
from collections import OrderedDict
from datetime import datetime

from . import records, scan, util
from .tracker import TrackerError

_HEADER = re.compile(r"^## (D-\d+)\s*$", re.M)


def _unquote(block: str) -> str:
    """Recover verbatim text from a blockquote."""
    out = []
    for line in block.split("\n"):
        if line.startswith("> "):
            out.append(line[2:])
        elif line == ">":
            out.append("")
        else:
            out.append(line)
    return "\n".join(out).strip("\n")


def _quote(text: str) -> str:
    """Render verbatim text as a blockquote, preserving blank lines."""
    lines = []
    for line in text.split("\n"):
        lines.append("> " + line if line else ">")
    return "\n".join(lines)


def parse(text: str):
    """Return (preamble, [entry dicts]). Each entry keeps its raw span."""
    matches = list(_HEADER.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    entries = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[start:end]
        entries.append(_parse_entry(m.group(1), block, start, end))
    return preamble, entries


def _parse_entry(entry_id: str, block: str, start: int, end: int) -> dict:
    fields: OrderedDict[str, str] = OrderedDict()
    for line in block.split("\n"):
        fm = re.match(r"^- (\w+):\s?(.*)$", line)
        if fm:
            fields[fm.group(1)] = fm.group(2).strip()
    question = ""
    qm = re.search(r"\*\*Question\.\*\*(.*?)(?=\n\*\*Ruling\.\*\*|\Z)", block, re.DOTALL)
    if qm:
        question = qm.group(1).strip()
    ruling = ""
    rm = re.search(r"\*\*Ruling\.\*\*\s*\n(.*)\Z", block, re.DOTALL)
    if rm:
        ruling = _unquote(rm.group(1).strip("\n"))
    return {
        "id": entry_id,
        "fields": fields,
        "question": question,
        "ruling": ruling,
        "span": (start, end),
        "block": block,
    }


def load_all(cfg, arc=None, include_ruled=False):
    """Return decision entries across arcs, each tagged with id/arc/path."""
    arcs = [arc] if arc else scan.arc_ids(cfg)
    out = []
    for slug in arcs:
        path = scan.rulings_path(cfg, slug)
        if not os.path.isfile(path):
            continue
        _, entries = parse(records.read(path))
        for entry in entries:
            if entry["fields"].get("status") != "ruled" or include_ruled:
                out.append(_tag(entry, slug, path))
    return out


def _tag(entry, slug, path):
    tagged = dict(entry)
    tagged["arc"] = slug
    tagged["path"] = path
    tagged["full_id"] = "{}/{}".format(slug, entry["id"])
    return tagged


def decision_ids(cfg):
    ids = []
    for slug in scan.arc_ids(cfg):
        path = scan.rulings_path(cfg, slug)
        if not os.path.isfile(path):
            continue
        _, entries = parse(records.read(path))
        for entry in entries:
            ids.append("{}/{}".format(slug, entry["id"]))
    return ids


def find(cfg, given):
    """Resolve a decision id (unique suffix) to a tagged entry."""
    resolved = records.resolve_id(decision_ids(cfg), given)
    slug, entry_id = resolved.rsplit("/", 1)
    path = scan.rulings_path(cfg, slug)
    _, entries = parse(records.read(path))
    for entry in entries:
        if entry["id"] == entry_id:
            return _tag(entry, slug, path)
    raise records.IdError(f"no decision matches {given!r}")


def _next_id(entries) -> str:
    highest = 0
    for entry in entries:
        num = int(entry["id"].split("-", 1)[1])
        highest = max(highest, num)
    return f"D-{highest + 1:03d}"


def ask(cfg, tracker, slug, text, blocking=False):
    """Append a new open decision. Returns (full_id, tracker_error_or_None)."""
    path = scan.rulings_path(cfg, slug)
    content = records.read(path)
    _, entries = parse(content)
    new_id = _next_id(entries)
    today = util.today_str()
    entry = (
        "\n## {}\n\n"
        "- status: open\n"
        "- blocking: {}\n"
        "- asked: {}\n"
        "- tracker: \n"
        "- ruled:\n\n"
        "**Question.** {}\n"
    ).format(new_id, "yes" if blocking else "no", today, text.strip())
    if not content.endswith("\n"):
        content += "\n"
    records.write(path, content + entry)

    tracker_err = None
    if tracker.enabled():
        arc_readme = records.read(scan.arc_readme(cfg, slug))
        parent = records.get_field(arc_readme, "tracker")
        try:
            issue = tracker.create_child("decision", text.strip()[:120], parent)
            if issue:
                _set_entry_tracker(path, new_id, issue)
        except TrackerError as exc:
            tracker_err = str(exc)
    return f"{slug}/{new_id}", tracker_err


def _entry_span(text, entry_id):
    _, entries = parse(text)
    for entry in entries:
        if entry["id"] == entry_id:
            return entry
    return None


def _set_entry_tracker(path, entry_id, issue):
    text = records.read(path)
    entry = _entry_span(text, entry_id)
    start, end = entry["span"]
    block = text[start:end]
    block = re.sub(r"(?m)^- tracker:.*$", "- tracker: " + issue, block, count=1)
    records.write(path, text[:start] + block + text[end:])


def rule(cfg, tracker, given, by, text):
    """Record a verbatim ruling. Returns (full_id, tracker_error_or_None)."""
    entry = find(cfg, given)
    path = entry["path"]
    content = records.read(path)
    fresh = _entry_span(content, entry["id"])
    start, end = fresh["span"]
    block = content[start:end]
    block = re.sub(r"(?m)^- status:.*$", "- status: ruled", block, count=1)
    ruled_line = f"- ruled: {util.today_str()} by {by}"
    block = re.sub(r"(?m)^- ruled:.*$", ruled_line, block, count=1)
    block = block.rstrip("\n")
    block += "\n\n**Ruling.**\n\n" + _quote(text) + "\n"
    new_content = content[:start] + block + "\n" + content[end:]
    records.write(path, new_content)

    tracker_err = None
    if tracker.enabled():
        issue = fresh["fields"].get("tracker", "")
        if issue:
            try:
                tracker.close(issue, text.strip())
            except TrackerError as exc:
                tracker_err = str(exc)
    return entry["full_id"], tracker_err


def queue(cfg, arc=None, include_all=False):
    """Return the ordered queue: blocking first, then oldest asked first."""
    entries = load_all(cfg, arc=arc, include_ruled=include_all)
    if not include_all:
        entries = [e for e in entries if e["fields"].get("status") == "open"]

    def sort_key(entry):
        blocking = entry["fields"].get("blocking", "no") == "yes"
        asked = entry["fields"].get("asked", "")
        return (0 if blocking else 1, asked, entry["full_id"])

    return sorted(entries, key=sort_key)


def recent_rulings(cfg, arc, limit=5):
    """Return the most recently ruled entries for an arc (newest first)."""
    ruled = [
        e
        for e in load_all(cfg, arc=arc, include_ruled=True)
        if e["fields"].get("status") == "ruled"
    ]
    ruled.sort(key=lambda e: (e["fields"].get("ruled", ""), e["full_id"]), reverse=True)
    return ruled[:limit]


def age_days(asked: str) -> int:
    """Whole days since an ``asked`` date string, best effort."""
    try:
        d = util.now_utc().date() - datetime.strptime(asked, "%Y-%m-%d").date()
        return d.days
    except (ValueError, TypeError):
        return 0

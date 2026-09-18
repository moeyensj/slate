"""Promote experiments into the knowledge base and flag stale evidence."""

from __future__ import annotations

import json
import os
import re

from . import gitstate, records, scan, util

_AS_OF = re.compile(r"As of\s+\d{4}-\d{2}-\d{2}")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INDENT_CHARS = set(" \t│├└─")


def promote(cfg, given, to_path, purpose):
    """Record a promotion and, when possible, annotate the README tree.

    Returns (exp_id, lines). Raises :class:`records.IdError` on a bad gate.
    """
    exp_id, record_dir = scan.find_experiment(cfg, given)
    note_abs = os.path.join(cfg.root, to_path)
    if not os.path.isfile(note_abs):
        raise records.GateError(f"note not found: {to_path}")
    note_text = records.read(note_abs)

    leaf = exp_id.split("/", 1)[1]
    if exp_id not in note_text and leaf not in note_text:
        raise records.GateError(f"note {to_path} does not mention experiment {exp_id}")
    if not _AS_OF.search(note_text):
        raise records.GateError(f"note {to_path} has no 'As of <date>' stamp")

    readme = os.path.join(record_dir, "README.md")
    existing = records.split_list(records.get_field(records.read(readme), "promoted_to"))
    if to_path not in existing:
        existing.append(to_path)
    records.apply_update(readme, {"promoted_to": ", ".join(existing)})

    lines = [f"promoted {exp_id} -> {to_path}"]
    tree_line = f"{os.path.basename(to_path)}  # {purpose}"
    if cfg.readme_tree:
        placed = _place_in_tree(cfg, os.path.dirname(to_path), tree_line)
        if placed:
            lines.append("README tree annotated")
        else:
            lines.append("could not place in README tree; add by hand:")
            lines.append(tree_line)
    else:
        lines.append("readme_tree off; add by hand:")
        lines.append(tree_line)
    return exp_id, lines


def _indent_width(line):
    i = 0
    for ch in line:
        if ch in _INDENT_CHARS:
            i += 1
        else:
            break
    return i


def _name_of(line):
    return line[_indent_width(line) :].rstrip()


def _place_in_tree(cfg, note_dir, tree_line):
    """Insert ``tree_line`` under the note's directory in the README tree."""
    readme = os.path.join(cfg.root, "README.md")
    if not os.path.isfile(readme):
        return False
    text = records.read(readme)
    target = os.path.basename(note_dir.rstrip("/")) if note_dir else ""
    if not target:
        return False

    for fence in _FENCE.finditer(text):
        block = fence.group(0)
        lines = block.split("\n")
        for i, line in enumerate(lines):
            name = _name_of(line).rstrip("/")
            if name != target:
                continue
            dir_indent = _indent_width(line)
            # Collect the contiguous run of deeper-indented children.
            last_child = None
            for j in range(i + 1, len(lines)):
                if lines[j].strip() in ("", "```"):
                    break
                if _indent_width(lines[j]) <= dir_indent:
                    break
                last_child = j
            if last_child is not None:
                prefix = lines[last_child][: _indent_width(lines[last_child])]
                insert_at = last_child + 1
            else:
                prefix = line[:dir_indent] + "  "
                insert_at = i + 1
            lines.insert(insert_at, prefix + tree_line)
            new_block = "\n".join(lines)
            records.write(readme, text[: fence.start()] + new_block + text[fence.end() :])
            return True
    return False


def stale(cfg):
    """Return rows for promoted experiments whose measured paths moved on."""
    rows = []
    repo_paths = dict(cfg.covered_repos())
    for exp_id, d in scan.experiment_dirs(cfg):
        text = records.read(os.path.join(d, "README.md"))
        promoted = records.get_field(text, "promoted_to")
        measures = records.split_list(records.get_field(text, "measures"))
        if not promoted or not measures:
            continue
        prov = _read_json(os.path.join(d, "provenance.json")) or {}
        prov_repos = prov.get("repos", {})
        prov_time = prov.get("time", "")
        dt = util.parse_ts(prov_time)
        age = util.human_age(util.now_utc().timestamp() - dt.timestamp()) if dt else "?"
        for measure in measures:
            repo, _, rel = measure.partition("/")
            path = repo_paths.get(repo) or os.path.join(cfg.repos_root_dir, repo)
            sha = (prov_repos.get(repo) or {}).get("head")
            count = None
            if sha and gitstate.is_repo(path):
                count = gitstate.count_range(path, sha, "HEAD", rel)
            rows.append(
                {
                    "note": promoted,
                    "experiment": exp_id,
                    "path": measure,
                    "commits": count if count is not None else 0,
                    "age": age,
                }
            )
    rows.sort(key=lambda r: r["commits"], reverse=True)
    return rows


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None

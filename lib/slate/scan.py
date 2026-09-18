"""Enumerate arcs, experiments, handoffs and decisions on disk.

These are pure file reads (no git, no tracker) so ``slate status`` can rely
on them and stay fast.
"""

from __future__ import annotations

import os

from . import records

PRIVATE_DIR = ".slate-private"


def arc_dirs(cfg):
    """Return ordered (slug, dir) for every arc under the arcs directory."""
    out = []
    base = cfg.arcs_dir
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for name in names:
        if name.startswith("."):
            continue
        d = os.path.join(base, name)
        readme = os.path.join(d, "README.md")
        if os.path.isfile(readme):
            out.append((name, d))
    return out


def arc_ids(cfg):
    return [slug for slug, _ in arc_dirs(cfg)]


def arc_readme(cfg, slug):
    return os.path.join(cfg.arc_dir(slug), "README.md")


def rulings_path(cfg, slug):
    return os.path.join(cfg.arc_dir(slug), "rulings.md")


def experiment_dirs(cfg, arc=None):
    """Return ordered (id, dir) experiments; ``id`` is ``<arc>/<dirname>``."""
    out = []
    arcs = [(arc, cfg.arc_dir(arc))] if arc else arc_dirs(cfg)
    for slug, adir in arcs:
        exp_root = os.path.join(adir, "experiments")
        try:
            names = sorted(os.listdir(exp_root))
        except OSError:
            continue
        for name in names:
            if name.startswith("."):
                continue
            d = os.path.join(exp_root, name)
            if os.path.isfile(os.path.join(d, "README.md")):
                out.append((f"{slug}/{name}", d))
    return out


def experiment_ids(cfg, arc=None):
    return [eid for eid, _ in experiment_dirs(cfg, arc)]


def _handoff_dirs(cfg, arc):
    dirs = [os.path.join(cfg.arc_dir(arc), "handoffs")]
    dirs.append(os.path.join(cfg.root, PRIVATE_DIR, arc, "handoffs"))
    return dirs


def handoff_files(cfg, arc=None):
    """Return ordered (id, path) handoffs (public and private); id is ``<arc>/<name>``."""
    out = []
    arcs = [arc] if arc else arc_ids(cfg)
    for slug in arcs:
        for hdir in _handoff_dirs(cfg, slug):
            try:
                names = sorted(os.listdir(hdir))
            except OSError:
                continue
            for name in names:
                if not name.endswith(".md") or name.startswith("."):
                    continue
                out.append((f"{slug}/{name[:-3]}", os.path.join(hdir, name)))
    out.sort(key=lambda pair: pair[0])
    return out


def handoff_ids(cfg, arc=None):
    return [hid for hid, _ in handoff_files(cfg, arc)]


def find_experiment(cfg, given):
    """Resolve an experiment id (unique suffix) to (id, dir)."""
    pairs = dict(experiment_dirs(cfg))
    resolved = records.resolve_id(pairs.keys(), given)
    return resolved, pairs[resolved]


def find_handoff(cfg, given):
    """Resolve a handoff id (unique suffix) to (id, path)."""
    pairs = dict(handoff_files(cfg))
    resolved = records.resolve_id(pairs.keys(), given)
    return resolved, pairs[resolved]


def find_arc(cfg, given):
    """Resolve an arc slug (unique suffix) to (slug, dir)."""
    pairs = dict(arc_dirs(cfg))
    resolved = records.resolve_id(pairs.keys(), given)
    return resolved, pairs[resolved]


def experiment_field(exp_dir, key, default=""):
    path = os.path.join(exp_dir, "README.md")
    try:
        return records.get_field(records.read(path), key, default)
    except OSError:
        return default


def arc_title(cfg, slug):
    try:
        return records.get_field(records.read(arc_readme(cfg, slug)), "title", slug)
    except OSError:
        return slug


def arc_directive(cfg, slug):
    """Extract the directive blockquote from an arc README."""
    try:
        text = records.read(arc_readme(cfg, slug))
    except OSError:
        return ""
    lines = text.split("\n")
    quote = []
    in_section = False
    for line in lines:
        if line.strip().startswith("## Directive"):
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            if line.startswith(">"):
                quote.append(line.lstrip(">").strip())
            elif quote and not line.strip():
                break
    return " ".join(q for q in quote if q).strip()

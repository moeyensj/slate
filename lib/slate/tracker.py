"""Tracker adapter with two implementations: ``bd`` and ``none``.

The ``bd`` implementation shells out with ``--json``/``--silent`` and runs in
``tracker.dir``. Callers must do their file work first and then call the
tracker inside a guard: a tracker failure raises :class:`TrackerError`, is
reported on one line, and never loses a record.
"""

from __future__ import annotations

import json
import subprocess

CLOSED_STATES = ("closed", "done", "resolved")


class TrackerError(Exception):
    """A tracker command failed; the caller keeps its file work and exits 0."""


class Tracker:
    """Small adapter over ``bd``; a no-op when ``kind`` is ``none``."""

    def __init__(self, kind: str, directory: str):
        self.kind = kind
        self.dir = directory

    def enabled(self) -> bool:
        return self.kind == "bd"

    def _bd(self, *args) -> str:
        try:
            p = subprocess.run(["bd", *args], cwd=self.dir, capture_output=True, text=True)
        except OSError as exc:
            raise TrackerError(f"bd not runnable: {exc}") from exc
        if p.returncode != 0:
            detail = p.stderr.strip() or p.stdout.strip() or f"exit {p.returncode}"
            raise TrackerError(f"bd {args[0] if args else ''}: {detail}")
        return p.stdout

    def create_epic(self, title: str):
        """Create an epic; return its id (or None when disabled)."""
        if not self.enabled():
            return None
        return self._bd("create", "--title", title, "--type", "epic", "--silent").strip()

    def create_child(self, kind: str, title: str, parent: str, labels=None):
        """Create a child issue of ``parent``; return its id (or None when disabled)."""
        if not self.enabled():
            return None
        args = ["create", "--title", title, "--type", kind, "--parent", parent]
        if labels:
            args += ["--labels", labels]
        args += ["--silent"]
        return self._bd(*args).strip()

    def close(self, issue_id: str, reason: str):
        """Close an issue with a reason. No-op when disabled or id is empty."""
        if not self.enabled() or not issue_id:
            return
        self._bd("close", issue_id, "--reason", reason)

    def open_under(self, parent: str, cap: int = 15):
        """List open/in-progress issues under ``parent`` (list of dicts)."""
        if not self.enabled() or not parent:
            return []
        out = self._bd("list", "--parent", parent, "--status", "open,in_progress", "--json")
        data = json.loads(out or "[]")
        if isinstance(data, dict):
            data = data.get("issues", [])
        return list(data)[:cap]

    def closed_among(self, ids):
        """Return which of ``ids`` are now closed."""
        closed = []
        if not self.enabled():
            return closed
        for issue_id in ids:
            try:
                out = self._bd("show", issue_id, "--json")
                data = json.loads(out)
            except (TrackerError, ValueError):
                continue
            if isinstance(data, list) and data:
                data = data[0]
            status = (data or {}).get("status", "")
            if status in CLOSED_STATES:
                closed.append(issue_id)
        return closed


def build(cfg) -> Tracker:
    """Construct the adapter from resolved config."""
    return Tracker(cfg.tracker_kind(), cfg.tracker_dir)

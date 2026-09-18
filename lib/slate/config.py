"""Knowledge-base discovery, ``slate.toml`` loading, and the resolved config.

Includes a tiny TOML reader used only when the standard-library ``tomllib``
is unavailable (Python < 3.11). The reader understands the subset that
``slate.toml`` uses: tables, strings, booleans, integers, arrays of strings
and ``#`` comments.
"""

from __future__ import annotations

import os
import shutil

SLATE_TOML = "slate.toml"


class KbError(Exception):
    """Raised when the knowledge base cannot be resolved unambiguously."""


class KbAmbiguous(KbError):
    """Two candidate knowledge bases were found at the same level."""

    def __init__(self, matches):
        self.matches = matches
        super().__init__("multiple slate.toml found: " + ", ".join(matches))


def _is_kb(path: str) -> bool:
    return os.path.isfile(os.path.join(path, SLATE_TOML))


def find_kb(cwd: str, kb_flag=None, env=None):
    """Resolve the knowledge-base root.

    Order: ``--kb`` flag, then ``SLATE_KB``, then a walk upward from ``cwd``
    looking at each level for ``./slate.toml`` and then ``./*/slate.toml``
    (one level of non-hidden children). Returns the absolute root path, or
    None when nothing is found. Raises :class:`KbAmbiguous` on a tie.
    """
    if kb_flag:
        root = os.path.abspath(kb_flag)
        return root if _is_kb(root) else None
    if env:
        root = os.path.abspath(env)
        return root if _is_kb(root) else None

    cur = os.path.abspath(cwd)
    while True:
        # A slate.toml in the level itself wins outright.
        if _is_kb(cur):
            return cur
        matches = []
        try:
            for name in sorted(os.listdir(cur)):
                if name.startswith("."):
                    continue
                child = os.path.join(cur, name)
                if os.path.isdir(child) and _is_kb(child):
                    matches.append(child)
        except OSError:
            pass
        if len(matches) > 1:
            raise KbAmbiguous(matches)
        if len(matches) == 1:
            return matches[0]
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


# --------------------------------------------------------------------------
# TOML reading
# --------------------------------------------------------------------------


def _parse_scalar(raw: str):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        items = []
        for part in _split_top(inner):
            part = part.strip()
            if part:
                items.append(_parse_scalar(part))
        return items
    if len(raw) >= 2 and raw[0] in "\"'" and raw[-1] == raw[0]:
        return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def _split_top(inner: str):
    """Split a bracket body on top-level commas, respecting quotes."""
    parts = []
    buf = []
    quote = None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment that is not inside a quote."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
    return line


def mini_toml(text: str) -> dict:
    """Parse the small TOML subset that slate.toml uses."""
    result: dict = {}
    table = result
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            table = result
            for part in name.split("."):
                part = part.strip()
                table = table.setdefault(part, {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        table[key.strip()] = _parse_scalar(value)
    return result


def load_toml(path: str) -> dict:
    """Load a TOML file, using tomllib when available and the mini reader otherwise."""
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:
        tomllib = None
    if tomllib is not None:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    with open(path, encoding="utf-8") as fh:
        return mini_toml(fh.read())


# --------------------------------------------------------------------------
# Resolved configuration
# --------------------------------------------------------------------------


class Config:
    """Resolved view of a knowledge base and its ``slate.toml``."""

    def __init__(self, root: str, data: dict):
        self.root = os.path.abspath(root)
        kb = data.get("kb", {}) or {}
        repos = data.get("repos", {}) or {}
        prov = data.get("provenance", {}) or {}
        tracker = data.get("tracker", {}) or {}
        agent = data.get("agent", {}) or {}
        commit = data.get("commit", {}) or {}
        exp = data.get("experiment", {}) or {}
        handoff = data.get("handoff", {}) or {}

        self.name = kb.get("name") or os.path.basename(self.root)
        self.arcs = kb.get("arcs", "arcs")
        self.readme_tree = bool(kb.get("readme_tree", True))

        self.repos_root = repos.get("root", "..")
        self.repos_include = repos.get("include", ["*"])

        self.provenance_tools = prov.get("tools", [])
        self.provenance_env = prov.get("env", [])
        self.provenance_binaries = prov.get("binaries", [])
        self.provenance_lockfiles = prov.get("lockfiles", [])

        self.tracker_kind_raw = tracker.get("kind", "auto")
        self.tracker_dir = os.path.abspath(os.path.join(self.root, tracker.get("dir", "..")))

        self.agent_experimenter = agent.get("experimenter", "slate:experimenter")

        self.commit_policy = commit.get("policy", "ask")
        self.record_subject = commit.get("record_subject", "Record {title}")
        self.correct_subject = commit.get("correct_subject", "Correct {title}")

        self.preflight = exp.get("preflight", [])
        self.volatile = exp.get("volatile", ["/tmp", "/private/tmp", "/var/tmp"])
        self.max_hash_mb = int(exp.get("max_hash_mb", 4096))
        self.preserve_kb = int(exp.get("preserve_kb", 256))

        self.max_lines = int(handoff.get("max_lines", 150))
        self.landed_cap = int(handoff.get("landed_cap", 15))
        self.claim_hours = int(handoff.get("claim_hours", 12))

    # -- derived paths --------------------------------------------------

    @property
    def arcs_dir(self) -> str:
        return os.path.join(self.root, self.arcs)

    @property
    def repos_root_dir(self) -> str:
        return os.path.abspath(os.path.join(self.root, self.repos_root))

    def arc_dir(self, slug: str) -> str:
        return os.path.join(self.arcs_dir, slug)

    def kb_dirty_ignore(self):
        """Repo-relative prefixes ignored in the KB's own dirty count.

        Writing a record (under the arcs directory) or a private handoff must
        not make every later snapshot and pickup report the KB as dirty.
        """
        from . import scan

        return [self.arcs.rstrip("/") + "/", scan.PRIVATE_DIR + "/"]

    def is_kb_path(self, path: str) -> bool:
        return os.path.abspath(path) == self.root

    # -- tracker --------------------------------------------------------

    def tracker_kind(self) -> str:
        """Resolve ``auto`` to ``bd`` or ``none``."""
        kind = self.tracker_kind_raw
        if kind == "auto":
            if shutil.which("bd") and os.path.isdir(os.path.join(self.tracker_dir, ".beads")):
                return "bd"
            return "none"
        return kind

    # -- covered repositories ------------------------------------------

    def covered_repos(self):
        """Return an ordered list of (name, path) covered by a snapshot."""
        from . import gitstate

        root = self.repos_root_dir
        pairs = []
        seen = set()
        include = self.repos_include
        if include == ["*"] or include == "*":
            names = []
            try:
                for name in sorted(os.listdir(root)):
                    if name.startswith("."):
                        continue
                    path = os.path.join(root, name)
                    # A working tree has a .git entry (a file, for a linked
                    # worktree); a bare repository has none and nothing to snapshot.
                    has_worktree = os.path.exists(os.path.join(path, ".git"))
                    if has_worktree and gitstate.is_repo(path):
                        names.append((name, path))
            except OSError:
                pass
            for name, path in names:
                pairs.append((name, path))
                seen.add(os.path.abspath(path))
            # Ensure the KB itself is covered when it is a repo.
            if gitstate.is_repo(self.root) and os.path.abspath(self.root) not in seen:
                pairs.append((self.name, self.root))
        else:
            for name in include:
                path = os.path.join(root, name)
                if gitstate.is_repo(path):
                    pairs.append((name, path))
        return pairs


def load(root: str) -> Config:
    """Load the config from a resolved knowledge-base root."""
    path = os.path.join(root, SLATE_TOML)
    data = load_toml(path) if os.path.isfile(path) else {}
    return Config(root, data)

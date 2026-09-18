"""Flat frontmatter parsing/updating, slate markers, and id resolution.

Frontmatter is a block of ``key: value`` lines between ``---`` markers at the
top of a record. Updating a key rewrites only that line and leaves the body
byte-identical.
"""

from __future__ import annotations

import re
from collections import OrderedDict

# Frontmatter is: opening delimiter line, content, closing delimiter line, body.
_FM_RE = re.compile(r"(---[^\n]*\n)(.*?\n)?(---[^\n]*\n)", re.DOTALL)

FILL_RE = re.compile(r"<!--\s*slate:fill\b")
AFTER_RE = re.compile(r"<!--\s*slate:after\b")
_INDEX_RE = re.compile(r"(<!--\s*slate:index\s*-->)(.*?)(<!--\s*/slate:index\s*-->)", re.DOTALL)


def _split(text: str):
    """Return (opening, fm_content, closing, body) or None when no frontmatter."""
    if not text.startswith("---"):
        return None
    m = _FM_RE.match(text)
    if not m:
        return None
    opening = m.group(1)
    content = m.group(2) or ""
    closing = m.group(3)
    body = text[m.end() :]
    return opening, content, closing, body


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    return value


def read_fields(text: str) -> OrderedDict[str, str]:
    """Parse the frontmatter into an ordered dict of stripped string values."""
    parts = _split(text)
    fields: OrderedDict[str, str] = OrderedDict()
    if not parts:
        return fields
    _, content, _, _ = parts
    for line in content.split("\n"):
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = _strip_quotes(value)
    return fields


def get_field(text: str, key: str, default: str = "") -> str:
    """Return a single frontmatter value, or ``default`` when absent."""
    return read_fields(text).get(key, default)


def split_list(value: str):
    """Split a comma-separated frontmatter list, dropping blanks."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _format_line(key: str, value: str) -> str:
    return (f"{key}: {value}").rstrip()


def update_fields(text: str, updates: dict) -> str:
    """Return ``text`` with the given frontmatter keys set.

    Existing keys are rewritten in place; unknown keys are appended to the end
    of the frontmatter block. The body is left byte-identical.
    """
    parts = _split(text)
    if not parts:
        raise ValueError("record has no frontmatter")
    opening, content, closing, body = parts
    # content ends with a trailing newline; keep the real lines only.
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    remaining = dict(updates)
    out = []
    for line in lines:
        if ":" in line:
            key = line.partition(":")[0].strip()
            if key in remaining:
                out.append(_format_line(key, remaining.pop(key)))
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(_format_line(key, value))
    new_content = "\n".join(out) + "\n"
    return opening + new_content + closing + body


def set_field(text: str, key: str, value: str) -> str:
    """Convenience wrapper to set one key."""
    return update_fields(text, {key: value})


def has_fill(text: str) -> bool:
    """True when an unwritten ``slate:fill`` marker remains."""
    return bool(FILL_RE.search(text))


def has_after(text: str) -> bool:
    """True when an unwritten ``slate:after`` marker remains."""
    return bool(AFTER_RE.search(text))


def set_index_block(text: str, body: str):
    """Replace the content of the ``slate:index`` block; nothing else changes.

    Returns (new_text, replaced) where ``replaced`` is False when no block
    was present.
    """
    replaced = [False]

    def _sub(m):
        replaced[0] = True
        inner = ("\n" + body + "\n") if body else "\n"
        return m.group(1) + inner + m.group(3)

    new_text = _INDEX_RE.sub(_sub, text)
    return new_text, replaced[0]


# --------------------------------------------------------------------------
# Record I/O helpers
# --------------------------------------------------------------------------


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def apply_update(path: str, updates: dict) -> None:
    """Read, update frontmatter fields, and write a record back."""
    write(path, update_fields(read(path), updates))


# --------------------------------------------------------------------------
# Id resolution (unique suffix)
# --------------------------------------------------------------------------


class IdError(Exception):
    """Raised when an id cannot be resolved to exactly one candidate (usage error)."""


class GateError(Exception):
    """Raised when a state gate fails (a check failed, exit 1)."""


def resolve_id(candidates, given: str):
    """Resolve ``given`` to one candidate: exact match, else unique suffix.

    Raises :class:`IdError` when nothing matches or the suffix is ambiguous.
    """
    candidates = list(candidates)
    if given in candidates:
        return given
    matches = [c for c in candidates if c.endswith(given)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise IdError(f"no record matches {given!r}")
    raise IdError("ambiguous id {!r} matches: {}".format(given, ", ".join(sorted(matches))))

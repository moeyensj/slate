"""Small shared helpers: time, host/user, atomic writes, templates, tails."""

from __future__ import annotations

import getpass
import hashlib
import os
import platform
import re
import socket
import subprocess
from datetime import datetime, timezone

TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Format an aware datetime as a compact UTC timestamp."""
    return dt.astimezone(timezone.utc).strftime(TS_FMT)


def now_ts() -> str:
    """Current UTC timestamp as a string."""
    return iso(now_utc())


def parse_ts(text: str):
    """Parse a timestamp written by :func:`iso`; return None on failure."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        # Be forgiving of ISO variants written by hand.
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None


def today_str() -> str:
    """Today's date as YYYY-MM-DD (UTC)."""
    return now_utc().date().isoformat()


def host() -> str:
    """Short hostname."""
    return socket.gethostname()


def user() -> str:
    """Current user name, best effort."""
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - getuser rarely fails
        return os.environ.get("USER", "unknown")


def sha256_file(path: str, chunk: int = 65536) -> str:
    """Stream a file through SHA-256 in fixed-size chunks; never slurps it whole."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _sysctl(name: str):
    """Read one ``sysctl -n`` value (macOS/BSD); None on any failure."""
    try:
        p = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def hardware() -> dict:
    """Best-effort hardware description; never raises.

    Uses only the standard library plus ``sysctl`` (macOS) or ``/proc``
    (Linux). Missing values are recorded as ``None`` rather than failing.
    """
    info = {"os": None, "cpu": None, "cores": None, "memory_bytes": None}
    try:
        info["os"] = platform.platform()
    except Exception:
        pass
    try:
        info["cores"] = os.cpu_count()
    except Exception:
        pass
    system = ""
    try:
        system = platform.system()
    except Exception:
        pass
    if system == "Darwin":
        info["cpu"] = _sysctl("machdep.cpu.brand_string")
        mem = _sysctl("hw.memsize")
        if mem and mem.isdigit():
            info["memory_bytes"] = int(mem)
    elif system == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.lower().startswith("model name"):
                        info["cpu"] = line.split(":", 1)[1].strip()
                        break
        except OSError:
            pass
        try:
            with open("/proc/meminfo", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal"):
                        kb = "".join(ch for ch in line if ch.isdigit())
                        if kb:
                            info["memory_bytes"] = int(kb) * 1024
                        break
        except OSError:
            pass
    if not info["cpu"]:
        try:
            info["cpu"] = platform.processor() or platform.machine() or None
        except Exception:
            pass
    return info


def human_age(seconds: float) -> str:
    """Render an elapsed span as a compact age like '6d', '3h', '12m', '4s'."""
    seconds = int(max(0, seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d"
    if seconds >= 3600:
        return f"{seconds // 3600}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def atomic_write(path: str, data: str) -> None:
    """Write text to ``path`` via a temp file and an atomic rename."""
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def tail(path: str, n: int) -> list:
    """Return the last ``n`` lines of a text file, or [] if it is missing."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    return lines[-n:] if n else lines


def templates_dir() -> str:
    """Locate the templates directory.

    The launcher sets ``SLATE_TEMPLATES``; otherwise fall back to the
    templates directory that sits next to this package's repository root.
    """
    override = os.environ.get("SLATE_TEMPLATES")
    if override:
        return override
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(repo, "templates")


def load_template(name: str) -> str:
    """Read a template file by basename."""
    with open(os.path.join(templates_dir(), name), encoding="utf-8") as fh:
        return fh.read()


_PLACEHOLDER = re.compile(r"{{(\w+)}}")


def render(text: str, mapping: dict) -> str:
    """Replace ``{{key}}`` placeholders; unknown keys become the empty string."""
    return _PLACEHOLDER.sub(lambda m: str(mapping.get(m.group(1), "")), text)

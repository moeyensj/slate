"""Shared test harness: temp knowledge bases, git repos, a fake bd, CLI runner."""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(REPO, "lib")
TEMPLATES = os.path.join(REPO, "templates")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from slate import config  # noqa: E402
from slate.cli import main  # noqa: E402

GIT_ENV = [
    "-c",
    "user.name=slate-test",
    "-c",
    "user.email=slate@test.local",
    "-c",
    "commit.gpgsign=false",
]

FAKE_BD = """#!/usr/bin/env python3
import sys, os, json
log = os.environ.get("FAKE_BD_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd == "create":
    ctr = os.path.join(os.environ["FAKE_BD_DIR"], "counter")
    n = 0
    try:
        n = int(open(ctr).read())
    except Exception:
        n = 0
    n += 1
    open(ctr, "w").write(str(n))
    print("bd-%03d" % n)
elif cmd == "list":
    print(json.dumps([
        {"id": "bd-open1", "status": "open", "title": "open one"},
        {"id": "bd-prog1", "status": "in_progress", "title": "prog one"},
    ]))
elif cmd == "show":
    issue = sys.argv[2]
    closed = [x for x in os.environ.get("FAKE_BD_CLOSED", "").split(",") if x]
    status = "closed" if issue in closed else "open"
    print(json.dumps({"id": issue, "status": status}))
elif cmd == "close":
    pass
sys.exit(0)
"""

FAKE_BD_FAIL = """#!/usr/bin/env python3
import sys, os
log = os.environ.get("FAKE_BD_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")
sys.stderr.write("bd: simulated failure\\n")
sys.exit(3)
"""


class Result:
    def __init__(self, code, out, err):
        self.code = code
        self.out = out
        self.err = err


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slate-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        os.environ["SLATE_TEMPLATES"] = TEMPLATES
        os.environ.pop("SLATE_KB", None)

    # -- knowledge base -------------------------------------------------

    def make_kb(
        self,
        name="kb",
        tracker="none",
        repos_root="..",
        arcs="arcs",
        tracker_dir=None,
        extra_toml="",
    ):
        root = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(root, arcs), exist_ok=True)
        toml = (
            "[kb]\n"
            f'arcs = "{arcs}"\n\n'
            "[repos]\n"
            f'root = "{repos_root}"\n\n'
            "[tracker]\n"
            f'kind = "{tracker}"\n'
        )
        if tracker_dir is not None:
            toml += f'dir = "{tracker_dir}"\n'
        # A deterministic volatile prefix that cannot match a temp dir. On Linux
        # tempfile lands under /tmp, which is in the library's default volatile
        # list, so tests that leave the default flag every path in the temp KB
        # as volatile. Tests point their volatile paths at this fake prefix.
        volatile_line = 'volatile = ["/slate-volatile-test"]\n'
        if "[experiment]" in extra_toml:
            extra_toml = extra_toml.replace("[experiment]\n", "[experiment]\n" + volatile_line, 1)
        else:
            if extra_toml and not extra_toml.endswith("\n"):
                extra_toml += "\n"
            extra_toml += "\n[experiment]\n" + volatile_line
        toml += extra_toml
        with open(os.path.join(root, "slate.toml"), "w") as fh:
            fh.write(toml)
        with open(os.path.join(root, arcs, "README.md"), "w") as fh:
            fh.write("# arcs\n\n<!-- slate:index -->\n<!-- /slate:index -->\n")
        return root

    def cfg(self, root):
        return config.load(root)

    # -- git ------------------------------------------------------------

    def git(self, repo, *args):
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)

    def git_commit(self, repo, *args):
        return subprocess.run(["git", "-C", repo, *GIT_ENV, *args], capture_output=True, text=True)

    def git_init(self, path, filename="seed.txt", content="seed\n"):
        os.makedirs(path, exist_ok=True)
        self.git(path, "init", "-b", "main")
        with open(os.path.join(path, filename), "w") as fh:
            fh.write(content)
        self.git_commit(path, "add", "-A")
        self.git_commit(path, "commit", "-m", "seed")
        return self.head(path)

    def head(self, repo):
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    # -- fake tracker ---------------------------------------------------

    def install_fake_bd(self, script=FAKE_BD):
        """Put a fake bd first on PATH; return its directory. Restores PATH on cleanup."""
        bindir = os.path.join(self.tmp, "fakebin")
        os.makedirs(bindir, exist_ok=True)
        path = os.path.join(bindir, "bd")
        with open(path, "w") as fh:
            fh.write(script)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        log = os.path.join(self.tmp, "bd.log")
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = bindir + os.pathsep + old_path
        os.environ["FAKE_BD_LOG"] = log
        os.environ["FAKE_BD_DIR"] = bindir

        def restore():
            os.environ["PATH"] = old_path
            os.environ.pop("FAKE_BD_LOG", None)
            os.environ.pop("FAKE_BD_DIR", None)
            os.environ.pop("FAKE_BD_CLOSED", None)

        self.addCleanup(restore)
        self.bd_log = log
        return bindir

    def bd_log_text(self):
        try:
            return open(self.bd_log).read()
        except OSError:
            return ""

    # -- CLI runner -----------------------------------------------------

    def cli_run(self, *argv, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        if cwd:
            os.chdir(cwd)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(list(argv))
        finally:
            os.chdir(old)
        return Result(code, out.getvalue(), err.getvalue())

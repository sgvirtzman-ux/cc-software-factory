"""Shared helpers for the factory's test suites.

The suites drive the real factory/bin scripts as subprocesses, against fakes:
a Jira Cloud server (fake_jira.py), a `gh` CLI (bin/gh), and local bare git
repos standing in for the code host. Every run works in fresh temp folders,
never in the repo.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
LIB = REPO / "factory" / "lib"
FAKE_BIN = TESTS / "bin"

sys.dont_write_bytecode = True  # importing factory/lib here must not write __pycache__ into the repo
BASE_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def workdir(name):
    """A fresh temp folder for one test run."""
    return Path(tempfile.mkdtemp(prefix=f"factory-{name}-"))


def minimal_path():
    """PATH with only what the factory needs (python, git, a shell), and so no `gh`
    unless it shares a folder with one of those."""
    dirs = [os.path.dirname(sys.executable)]
    for tool in ("python3", "git", "bash", "sh", "env"):
        found = shutil.which(tool)
        if found:
            dirs.append(os.path.dirname(found))
    return os.pathsep.join(dict.fromkeys(dirs))


class Checks:
    """Collects named checks and exits non-zero if any failed."""

    def __init__(self):
        self.failures = []

    def __call__(self, name, cond, detail=""):
        print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond and detail else ""))
        if not cond:
            self.failures.append(name)

    def finish(self):
        print(f"\n{len(self.failures)} failure(s)" if self.failures else "\nall passed")
        sys.exit(1 if self.failures else 0)

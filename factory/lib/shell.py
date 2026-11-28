"""Which bash runs the repos' checks scripts.

On macOS and Linux it's `bash` on PATH. On Windows it must be Git Bash, the
shell Claude Code itself uses there. Starting a bare `bash` on Windows finds
C:\\Windows\\System32\\bash.exe first, ahead of PATH: that's WSL's launcher,
which runs the checks inside a Linux VM without the machine's tools (dotnet,
node, ...). So on Windows, in order:

1. config.json `bash`, a path to bash.exe, when set;
2. CLAUDE_CODE_GIT_BASH_PATH, the variable Claude Code uses to find Git Bash;
3. Git for Windows' own bin/bash.exe, found from `git --exec-path`;
4. bash.exe on PATH, skipping System32 and WindowsApps (WSL's launchers).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


class ShellError(Exception):
    pass


def _is_wsl_launcher(path):
    parts = [p.lower() for p in Path(path).parts]
    return "system32" in parts or "windowsapps" in parts or "sysnative" in parts


def find_bash(config, platform=None, environ=None, which=None, git_exec_path=None):
    """The bash to run checks with. The keyword arguments exist for tests."""
    platform = platform or sys.platform
    environ = os.environ if environ is None else environ
    configured = config.get("bash")
    if configured:
        if not Path(configured).is_file():
            raise ShellError(f"config.json bash is {configured!r}, which doesn't exist")
        return str(configured)
    if not platform.startswith("win"):
        return "bash"
    candidates = []
    if environ.get("CLAUDE_CODE_GIT_BASH_PATH"):
        candidates.append(environ["CLAUDE_CODE_GIT_BASH_PATH"])
    exec_path = git_exec_path() if git_exec_path else _git_exec_path()
    if exec_path:
        # .../Git/mingw64/libexec/git-core → .../Git
        root = Path(exec_path).parents[2]
        candidates += [root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"]
    for candidate in candidates:
        if Path(candidate).is_file() and not _is_wsl_launcher(candidate):
            return str(candidate)
    found = (which or shutil.which)("bash")
    if found and not _is_wsl_launcher(found):
        return found
    raise ShellError("can't find Git Bash: set CLAUDE_CODE_GIT_BASH_PATH, or `bash` in "
                     "factory/config.json, to the path of Git's bash.exe (not System32's, which is WSL)")


def _git_exec_path():
    try:
        res = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return None
    return res.stdout.strip() if res.returncode == 0 else None


def describe(bash):
    """`uname -s` from that bash: MINGW64_NT-... for Git Bash, Linux under WSL."""
    try:
        res = subprocess.run([bash, "-c", "uname -s"], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ShellError(f"can't run {bash}: {e}")
    return res.stdout.strip()

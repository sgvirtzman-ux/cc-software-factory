"""Windows behaviour, simulated on macOS or Linux (it can't run real Windows):

- the scripts' launcher finds Python as python3, python or py, and skips a
  Microsoft Store python3 stub;
- job.json locking works through msvcrt when fcntl is missing;
- the gate picks Git Bash on Windows, never C:\\Windows\\System32\\bash.exe (WSL).
"""
import multiprocessing as mp
import os, subprocess, sys, time, types
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import BASE_ENV, LIB, REPO, Checks, workdir

check = Checks()

# ---- launcher ---------------------------------------------------------------
def launcher_cases():
    w = workdir("launcher")
    for d in ("only-python", "only-py", "stub-first", "none"):
        (w / d).mkdir()
    os.symlink(sys.executable, w / "only-python" / "python")
    os.symlink(sys.executable, w / "only-py" / "py")
    stub = w / "stub-first" / "python3"
    stub.write_text('#!/bin/sh\necho "Python was not found; run without arguments to install from the Microsoft Store" >&2\nexit 9009\n')
    stub.chmod(0o755)
    os.symlink(sys.executable, w / "stub-first" / "python")
    return w

w = launcher_cases()
job = str(REPO / "factory" / "bin" / "job")
for d, label in (("only-python", "only `python` on PATH"), ("only-py", "only `py` on PATH"),
                 ("stub-first", "a Store python3 stub ahead of a real python")):
    r = subprocess.run([job, "--help"], capture_output=True, text=True, env={"PATH": str(w / d), "PYTHONDONTWRITEBYTECODE": "1"})
    check(f"launcher: {label}", r.returncode == 0 and r.stdout.startswith("The only writer"), r.stdout + r.stderr)
r = subprocess.run([job, "--help"], capture_output=True, text=True, env={"PATH": str(w / "none"), "PYTHONDONTWRITEBYTECODE": "1"})
check("launcher: no Python at all gives a clear message",
      r.returncode == 1 and "needs Python 3.10 or later" in r.stderr, r.stderr)
r = subprocess.run([sys.executable, job, "--help"], capture_output=True, text=True, env=BASE_ENV)
check("launcher: running through Python directly still works", r.stdout.startswith("The only writer"), r.stderr)

# ---- msvcrt lock path -------------------------------------------------------
SRC = (REPO / "factory" / "bin" / "job").read_text()
BLOCK = SRC[SRC.index("try:  # job.json locks"):SRC.index("ROOT = ")]

def windows_lock():
    """job's lock block as Windows runs it: no fcntl, msvcrt present (backed by lockf here)."""
    fake = types.ModuleType("msvcrt"); fake.LK_LOCK = 1
    def locking(fd, mode, n):
        assert mode == fake.LK_LOCK and n == 1
        os.lockf(fd, os.F_LOCK, 0)
    fake.locking = locking
    sys.modules["fcntl"] = None  # `import fcntl` raises ImportError
    sys.modules["msvcrt"] = fake
    ns = {}
    exec(BLOCK, ns)
    assert "msvcrt" in ns and "fcntl" not in ns
    return ns["lock_file"]

def holder(path, ready, release):
    lock = windows_lock(); f = open(path, "w"); lock(f); ready.set(); release.wait()

def waiter(path, out):
    lock = windows_lock(); f = open(path, "w"); t = time.monotonic(); lock(f); out.put(time.monotonic() - t)

# ---- Git Bash finder ----------------------------------------------------------
def finder_cases():
    sys.path.insert(0, str(LIB))
    import shell
    s = workdir("bash-finder")
    for d in ("Windows/System32", "Git/bin", "Git/usr/bin", "Git/mingw64/libexec/git-core",
              "GitUsrOnly/usr/bin", "GitUsrOnly/mingw64/libexec/git-core"):
        (s / d).mkdir(parents=True)
    for f in ("Windows/System32/bash.exe", "Git/bin/bash.exe", "Git/usr/bin/bash.exe", "GitUsrOnly/usr/bin/bash.exe"):
        (s / f).touch()
    wsl, gitbash = str(s / "Windows/System32/bash.exe"), str(s / "Git/bin/bash.exe")
    git = lambda root: (lambda: str(s / root / "mingw64/libexec/git-core"))
    nogit = lambda: None
    win = dict(platform="win32")
    cases = [
        ("off Windows: plain bash", dict(config={}, platform="linux"), "bash"),
        ("CLAUDE_CODE_GIT_BASH_PATH wins", dict(config={}, environ={"CLAUDE_CODE_GIT_BASH_PATH": gitbash}, git_exec_path=nogit, which=lambda n: wsl, **win), gitbash),
        ("an env var pointing at System32 is ignored", dict(config={}, environ={"CLAUDE_CODE_GIT_BASH_PATH": wsl}, git_exec_path=git("Git"), which=lambda n: wsl, **win), gitbash),
        ("found from git --exec-path", dict(config={}, environ={}, git_exec_path=git("Git"), which=lambda n: wsl, **win), gitbash),
        ("Git with only usr/bin/bash.exe", dict(config={}, environ={}, git_exec_path=git("GitUsrOnly"), which=lambda n: wsl, **win), str(s / "GitUsrOnly/usr/bin/bash.exe")),
        ("Git's bash on PATH", dict(config={}, environ={}, git_exec_path=nogit, which=lambda n: gitbash, **win), gitbash),
        ("only WSL's bash: clear error", dict(config={}, environ={}, git_exec_path=nogit, which=lambda n: wsl, **win), shell.ShellError),
        ("config.json bash honored", dict(config={"bash": gitbash}, environ={}, git_exec_path=nogit, which=lambda n: wsl, **win), gitbash),
        ("config.json bash that doesn't exist: clear error", dict(config={"bash": str(s / "nope/bash.exe")}, platform="linux"), shell.ShellError),
    ]
    for name, kw, want in cases:
        try:
            got = shell.find_bash(**kw)
        except shell.ShellError:
            got = shell.ShellError
        check(f"bash finder: {name}", got == want, got)

if __name__ == "__main__":
    path = str(workdir("lock") / ".lock")
    ready, release, out = mp.Event(), mp.Event(), mp.Queue()
    a = mp.Process(target=holder, args=(path, ready, release)); a.start(); ready.wait()
    b = mp.Process(target=waiter, args=(path, out)); b.start()
    time.sleep(1.0)
    held = b.is_alive()
    release.set(); a.join(); waited = out.get(timeout=5); b.join()
    check("msvcrt lock: a second process waits for the first", held and waited >= 0.9, waited)
    finder_cases()
    check.finish()

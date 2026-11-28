"""Run every suite and report one verdict.

Usage: python3 tests/run_all.py [<git ref for the GitHub regression, default HEAD>]
"""
import os, subprocess, sys, time
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SUITES = ["test_jira.py", "test_codehost.py", "test_workspace.py", "test_windows.py", "test_dashboard.py", "test_github.py"]

failed = []
for suite in SUITES:
    args = [sys.executable, str(TESTS / suite)] + (sys.argv[1:2] if suite == "test_github.py" else [])
    start = time.monotonic()
    r = subprocess.run(args, capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    out = r.stdout + r.stderr
    passes = sum(l.startswith(("PASS ", "SAME ")) for l in out.splitlines())
    fails = [l for l in out.splitlines() if l.startswith(("FAIL ", "DIFF "))]
    ok = r.returncode == 0
    print(f"{'ok  ' if ok else 'FAIL'} {suite:<20} {passes:>3} checks  {time.monotonic() - start:5.1f}s")
    if not ok:
        failed.append(suite)
        print("\n".join("     " + l for l in (fails or out.strip().splitlines()[-15:])))
print(f"\n{len(failed)} suite(s) failed: {', '.join(failed)}" if failed else "\nall suites passed")
sys.exit(1 if failed else 0)

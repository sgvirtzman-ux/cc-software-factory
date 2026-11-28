# Tests

End-to-end suites for the factory's scripts. Each one runs the real
`factory/bin/*` scripts as subprocesses against fakes, in fresh temp folders:

- a Jira Cloud server (`fake_jira.py`), modeled on a real company-managed workflow;
- a `gh` CLI (`bin/gh`) that records every call;
- local bare git repos standing in for the code host, with a teammate's clone that
  merges the factory's branches.

Standard library only; no network. Run them on macOS or Linux (`test_windows.py`
simulates Windows; it can't run there).

```bash
python3 tests/run_all.py              # everything; GitHub regression against HEAD
python3 tests/run_all.py origin/main  # regression against another ref
python3 tests/test_workspace.py       # one suite, with each check printed
```

| Suite | Covers |
| --- | --- |
| `test_jira.py` | Jira tracker: queue, intake comments, reply detection, labels, ready → start → merged moves, rejects, new issues, ADF; non-ASCII issue text through Windows-style cp1252 pipes; a failed `create` leaves no job folder |
| `test_codehost.py` | Git mode (handoff; no-ff, rebase and squash merges) and GitHub mode (draft PR, escalate, merge) |
| `test_workspace.py` | Workspace mode: several repos, intake's `REPO`, label override, `branch_format`, CRLF checks scripts |
| `test_windows.py` | Python launcher, msvcrt locking, Git Bash vs WSL, all simulated |
| `test_dashboard.py` | `factory/bin/dashboard`: localhost only, already running, port taken |
| `test_github.py` | Regression: the GitHub Issues + PRs setup behaves exactly as at a git ref (default `HEAD`) |

Before committing a change to `factory/`, run `python3 tests/run_all.py`: every
suite must pass and the regression must report "all the same". A deliberate
behaviour change shows as a DIFF; say so in the PR.

Every factory process the suites start runs with `EncodingWarning` as an error
(`STRICT_ENCODING` in `support.py`), so a file read or write, or a text-mode
subprocess, that doesn't name its encoding fails the run. Windows defaults to
cp1252, which can't hold issue text like "→"; name `encoding="utf-8"` (and
`errors="replace"` for command output) every time.

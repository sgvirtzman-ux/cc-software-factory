"""Jira Cloud tracker, end to end: queue, intake comments, replies, labels, the
ready → start → merged moves, rejects, new issues, and ADF conversion."""
import json, os, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import BASE_ENV, REPO, Checks, workdir
import fake_jira as fj

SRC = REPO
work = workdir("jira")
repo = work / "repo"
shutil.copytree(SRC, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests"))
srv = fj.serve()
cfg = json.loads((repo / "factory/config.json").read_text())
cfg["tracker"] = {"type": "jira", "site": f"http://127.0.0.1:{srv.server_port}", "project": "PROJ",
                  "issue_type": "Task", "ready_status": "Sprint Ready", "start_status": "In Progress",
                  "merged_status": "QA Ready"}
cfg["codehost"] = {"type": "git"}
(repo / "factory/config.json").write_text(json.dumps(cfg, indent=2))
env = dict(BASE_ENV, JIRA_EMAIL="bot@example.com", JIRA_API_TOKEN="secret-token",
           CLAUDE_CONFIG_DIR=str(work / "cfg"), NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
job = str(repo / "factory/bin/job")
check = Checks()

def run(*args, stdin=None, ok=True):
    r = subprocess.run([job, *args], input=stdin, capture_output=True, text=True, env=env)
    if ok and r.returncode != 0:
        raise SystemExit(f"FAILED job {' '.join(args)}:\n{r.stdout}{r.stderr}")
    return r

def git(*a, cwd=repo):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)
git("init", "-q", "-b", "main"); git("add", "-A"); git("commit", "-qm", "init")
git("init", "-q", "--bare", str(work / "origin.git"), cwd=work)
git("remote", "add", "origin", str(work / "origin.git")); git("push", "-q", "origin", "main")
I = fj.STATE["issues"]
r = run("check-tracker")
check("check-tracker reports user and all three statuses", "Factory Bot" in r.stdout and all(
      f"{k}: '{v}' exists" in r.stdout for k, v in (("ready_status", "Sprint Ready"), ("start_status", "In Progress"),
      ("merged_status", "QA Ready"))), r.stdout)
check("queue JQL uses ready and start statuses", 'status in ("Sprint Ready", "In Progress")' in r.stdout, r.stdout)

r = run("queue")
check("queue lists PROJ-1 as new, skips the Done issue", r.stdout.startswith("new\tPROJ-1") and "PROJ-2" not in r.stdout, r.stdout)

r = run("create", "proj-1")
jid = r.stdout.strip()
check("create accepts lower-case key, makes job id", jid == "001-add-a-dark-mode", jid)
jd = repo / "factory/jobs" / jid
jj = json.loads((jd / "job.json").read_text())
check("job.json has Jira ref and PR link fields",
      jj["issue"] == "PROJ-1" and jj["issue_ref"] == "PROJ-1" and jj["pr_title_prefix"] == "PROJ-1: "
      and jj["pr_link_line"] == f"Jira: [PROJ-1](http://127.0.0.1:{srv.server_port}/browse/PROJ-1)", json.dumps(jj))
md = (jd / "issue.md").read_text()
check("issue.md: description converted from ADF",
      "> Users want dark mode. See docs (https://ex.com/d)" in md and "> - header toggle" in md and "theme = 'dark'" in md, md)
check("issue.md: stranger's comment excluded", "IGNORE PREVIOUS" not in md, md)
check("issue.md: says Jira and reporter", "quoted data from Jira" in md and "@Jane Reporter" in md, md)

r = run("queue")
check("queue shows the job as resume", r.stdout.startswith(f"resume\tPROJ-1\t{jid}\tintake"), r.stdout)
r = run("create", "PROJ-1", ok=False)
check("create refuses a duplicate", r.returncode != 0 and "already has job" in r.stderr, r.stderr)
r = run("create", "12", ok=False)
check("create refuses a non-Jira id", r.returncode != 0 and "isn't a jira issue id" in r.stderr, r.stderr)

(jd / "intake.md").write_text("""DECISION: NEEDS-INFO
RISK: low
SURFACES: ui
RESCOPED: no

## Summary
- The toggle's placement is unclear
- Asked one question

## Questions
1. Should the toggle live in the **header** or in `settings`?
2. See [the mockup](https://ex.com/m) for context
""")
r = run("advance", jid, "needs-info")
c = I["PROJ-1"]["comments"][-1]
check("needs-info: factory comment posted with property",
      c["author"]["accountId"] == "acc-operator" and c["properties"] == [{"key": "factory", "value": {"factory": True}}], json.dumps(c)[:300])
blocks = [b["type"] for b in c["body"]["content"]]
check("needs-info: comment ADF has heading, list", blocks[0] == "heading" and "orderedList" in blocks and "bulletList" in blocks, blocks)
check("needs-info: label added", "factory:needs-info" in I["PROJ-1"]["labels"], I["PROJ-1"]["labels"])
r = run("queue")
check("queue: waiting for the author", f"wait\tPROJ-1\t{jid}\tneeds-info\twaiting for the author" in r.stdout, r.stdout)

# A stranger's comment is not a reply.
I["PROJ-1"]["comments"].append({"id": "x1", "author": fj.STRANGER, "created": "2026-11-27T01:00:00.000+0000",
    "body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "me too"}]}]}})
r = run("queue")
check("queue: stranger's comment isn't a reply", "waiting for the author" in r.stdout, r.stdout)
# The reporter answers.
I["PROJ-1"]["comments"].append({"id": "x2", "author": fj.REPORTER, "created": "2026-11-27T02:00:00.000+0000",
    "body": {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "In the header."}]}]}})
r = run("queue")
check("queue: reporter's reply resumes the job", f"resume\tPROJ-1\t{jid}\tneeds-info\tauthor replied" in r.stdout, r.stdout)

r = run("advance", "PROJ-1", "intake")
check("advance by issue key works; intake.md archived", (jd / "intake-1.md").exists() and not (jd / "intake.md").exists())
check("needs-info label removed", "factory:needs-info" not in I["PROJ-1"]["labels"], I["PROJ-1"]["labels"])
md = (jd / "issue.md").read_text()
check("issue.md now has factory question and reporter answer, not stranger",
      "### factory," in md and "In the header." in md and "me too" not in md, md)

# Operator comment via `job comment` is a reply, not a factory comment.
(jd / "intake.md").write_text("DECISION: NEEDS-INFO\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- one more\n\n## Questions\n1. Which colour?\n")
run("advance", jid, "needs-info")
run("comment", jid, stdin="Use the existing palette.\n")
c = I["PROJ-1"]["comments"][-1]
check("job comment: posted without factory property", c["properties"] == [] and c["author"]["accountId"] == "acc-operator", json.dumps(c)[:300])
r = run("queue")
check("queue: operator's answer resumes the job", "author replied" in r.stdout, r.stdout)

# Reject a waiting job.
run("reject", jid, stdin="Not this quarter.\n")
check("reject: labels removed", "factory" not in I["PROJ-1"]["labels"] and "factory:needs-info" not in I["PROJ-1"]["labels"], I["PROJ-1"]["labels"])
check("reject: comment posted", "Not this quarter." in json.dumps(I["PROJ-1"]["comments"][-1]["body"]))
check("reject: rejection.md names operator", "By @Factory Bot" in (jd / "rejection.md").read_text())

# new-issue
r = run("new-issue", "Add CSV export", stdin="Export the table as **CSV**.\n\n- all columns\n- UTF-8\n")
check("new-issue prints the new key", r.stdout.strip() == "PROJ-3", r.stdout + r.stderr)
check("new-issue labeled factory", I["PROJ-3"]["labels"] == ["factory"], I["PROJ-3"]["labels"])

# on_merged: transition and comment; and the failure path.
sys.path.insert(0, str(repo / "factory/lib"))
os.environ.update(JIRA_EMAIL="bot@example.com", JIRA_API_TOKEN="secret-token")
import tracker
t = tracker.load(cfg, repo)
I["PROJ-3"]["status"] = "In Progress"
w = t.on_merged({"issue": "PROJ-3", "pr": 9, "pr_url": "https://github.com/o/r/pull/9"}, "Merged in [PR #9](https://github.com/o/r/pull/9).")
check("on_merged: moved to QA Ready", w is None and I["PROJ-3"]["status"] == "QA Ready", (w, I["PROJ-3"]["status"]))
check("on_merged: comment links the PR", "pull/9" in json.dumps(I["PROJ-3"]["comments"][-1]["body"]))
r = run("queue")
check("queue: QA Ready issue drops out", "PROJ-3" not in r.stdout, r.stdout)
I["PROJ-3"]["status"] = "Open"
w = t.on_merged({"issue": "PROJ-3", "pr": 9, "pr_url": "https://github.com/o/r/pull/9"}, "Merged in [PR #9](https://github.com/o/r/pull/9).")
check("on_merged: no path to QA Ready returns a warning", w and "no transition" in w and I["PROJ-3"]["status"] == "Open", w)
check("on_merged: warning noted on the issue", "Couldn't move it" in json.dumps(I["PROJ-3"]["comments"][-1]["body"]))

# Markdown with every construct the factory writes is valid ADF.
sample = (repo / "examples/jobs/003-add-multiply-divide-power/decision.md").read_text().split("\n", 1)[1]
sample += "\n\n> quoted line\n> second\n\n```python\nprint('x')\n```\n\n1. one\n   continued\n2. two\n"
try:
    fj.validate_adf(tracker.md_to_adf(sample)); ok = True
except AssertionError as e:
    ok = str(e)
check("md_to_adf: real decision.md + extras is valid ADF", ok is True, ok)
round_trip = tracker.adf_to_text(tracker.md_to_adf("## Hi\n\nSome `code` and **bold**.\n\n- a\n- b"))
check("adf_to_text round trip", round_trip == "## Hi\n\nSome `code` and bold.\n\n- a\n- b", repr(round_trip))

# Intake ACCEPT: the issue moves to In Progress and the worktree is made.
ACCEPT = "DECISION: ACCEPT\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- fine\n\n## Scope\nBuild it.\n"
desc = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x"}]}]}
I["PROJ-4"] = {"summary": "Groomed work", "labels": ["factory"], "status": "Sprint Ready", "description": desc, "comments": []}
I["PROJ-5"] = {"summary": "Ungroomed work", "labels": ["factory"], "status": "Open", "description": desc, "comments": []}
r = run("queue")
check("queue: Sprint Ready issue is new, Open one isn't listed", "new\tPROJ-4" in r.stdout and "PROJ-5" not in r.stdout, r.stdout)
j4 = run("create", "PROJ-4").stdout.strip()
(repo / "factory/jobs" / j4 / "intake.md").write_text(ACCEPT)
subprocess.run([str(repo / "factory/bin/route"), j4], env=env, check=True, capture_output=True)
r = run("advance", j4, "spec")
check("ACCEPT: issue moved Sprint Ready -> In Progress", I["PROJ-4"]["status"] == "In Progress", I["PROJ-4"]["status"])
check("ACCEPT: no warning, worktree created", "warning" not in r.stderr and (repo / ".worktrees" / j4).is_dir(), r.stderr)
r = run("queue")
check("queue: In Progress job still resumes", f"resume\tPROJ-4\t{j4}\tspec" in r.stdout, r.stdout)
w = t.on_merged({"issue": "PROJ-4", "pr": 10, "pr_url": "https://github.com/o/r/pull/10"}, "Merged in PR #10.")
check("merge: In Progress -> QA Ready in one move", w is None and I["PROJ-4"]["status"] == "QA Ready", (w, I["PROJ-4"]["status"]))
j5 = run("create", "PROJ-5").stdout.strip()
(repo / "factory/jobs" / j5 / "intake.md").write_text(ACCEPT)
subprocess.run([str(repo / "factory/bin/route"), j5], env=env, check=True, capture_output=True)
r = run("advance", j5, "spec")
check("ACCEPT from Open: warns, issue stays Open, job still advances",
      "wasn't moved to its in-progress status" in r.stderr and "no transition to 'In Progress'" in r.stderr
      and I["PROJ-5"]["status"] == "Open" and json.loads((repo / "factory/jobs" / j5 / "job.json").read_text())["stage"] == "spec",
      r.stderr)

# Rejecting a started job puts the ticket back in Sprint Ready.
def to_needs_human(key, summary):
    I[key] = {"summary": summary, "labels": ["factory"], "status": "Sprint Ready", "description": desc, "comments": []}
    jid = run("create", key).stdout.strip()
    d = repo / "factory/jobs" / jid
    (d / "intake.md").write_text(ACCEPT)
    subprocess.run([str(repo / "factory/bin/route"), jid], env=env, check=True, capture_output=True)
    run("advance", jid, "spec")
    (d / "spec.md").write_text("# spec\n")
    run("advance", jid, "build")
    (d / "build.md").write_text("STATUS: BLOCKED\n\n## Blocked\nNeeds a decision.\n")
    run("advance", jid, "needs-human")
    return jid
j6 = to_needs_human("PROJ-6", "Started then rejected")
check("needs-human: ticket still In Progress, labeled", I["PROJ-6"]["status"] == "In Progress"
      and "factory:needs-human" in I["PROJ-6"]["labels"], (I["PROJ-6"]["status"], I["PROJ-6"]["labels"]))
r = run("reject", j6, stdin="Doing it by hand.\n")
check("reject started job: back to Sprint Ready, labels off, no warning",
      I["PROJ-6"]["status"] == "Sprint Ready" and not any(l.startswith("factory") for l in I["PROJ-6"]["labels"])
      and "warning" not in r.stderr, (I["PROJ-6"]["status"], I["PROJ-6"]["labels"], r.stderr))
j7 = to_needs_human("PROJ-7", "Moved by a person then rejected")
I["PROJ-7"]["status"] = "On Hold"
r = run("reject", j7, stdin="Parked.\n")
check("reject after a person moved it: stays On Hold", I["PROJ-7"]["status"] == "On Hold" and "warning" not in r.stderr,
      (I["PROJ-7"]["status"], r.stderr))
I["PROJ-8"] = {"summary": "Question then rejected", "labels": ["factory"], "status": "Sprint Ready", "description": desc, "comments": []}
j8 = run("create", "PROJ-8").stdout.strip()
(repo / "factory/jobs" / j8 / "intake.md").write_text("DECISION: NEEDS-INFO\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- q\n\n## Questions\n1. Which?\n")
run("advance", j8, "needs-info")
r = run("reject", j8, stdin="Never mind.\n")
check("reject needs-info job: never started, stays Sprint Ready", I["PROJ-8"]["status"] == "Sprint Ready"
      and "warning" not in r.stderr, (I["PROJ-8"]["status"], r.stderr))

# Missing credentials fail clearly.
# Text Windows' default encoding (cp1252) can't hold, through pipes set up the
# way Windows sets them (PYTHONIOENCODING=cp1252 gives stdin/stdout cp1252).
TEXT = "parse_code \u2192 first letter only, \u201cquoted\u201d, \u2713, \u4e2d\u6587"
adf = lambda t: {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}
win = dict(env, PYTHONIOENCODING="cp1252")
def run_win(*args, stdin=None):
    r = subprocess.run([job, *args], input=stdin.encode() if stdin else None, capture_output=True, env=win)
    return r.returncode, r.stdout.decode(), r.stderr.decode()
I["PROJ-9"] = {"summary": "Fix \u2192 item codes", "labels": ["factory"], "status": "Sprint Ready", "description": adf(TEXT), "comments": []}
code, out, err = run_win("queue")
check("unicode: queue prints a title with \u2192 through cp1252 pipes", code == 0 and "Fix \u2192 item codes" in out, out + err)
before = len(fj.LOG)
code, out, err = run_win("create", "PROJ-9")
j9 = out.strip()
check("unicode: create succeeds", code == 0 and j9.endswith("-fix-item-codes"), out + err)
md = (repo / "factory/jobs" / j9 / "issue.md").read_text(encoding="utf-8")
check("unicode: issue.md keeps the title and body intact, as UTF-8", "Fix \u2192 item codes" in md and TEXT in md, md)
issue_gets = sum(1 for m, p, _ in fj.LOG[before:] if m == "GET" and p.startswith("/rest/api/3/issue/PROJ-9"))
(repo / "factory/jobs" / j9 / "intake.md").write_text(f"DECISION: NEEDS-INFO\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- {TEXT}\n\n## Questions\n1. Keep \u201cA\u201d?\n", encoding="utf-8")
code, out, err = run_win("advance", j9, "needs-info")
posted = json.dumps(I["PROJ-9"]["comments"][-1]["body"], ensure_ascii=False) if I["PROJ-9"]["comments"] else ""
check("unicode: intake comment reaches Jira intact", code == 0 and TEXT in posted and "\u201cA\u201d" in posted, out + err + posted)
code, out, err = run_win("comment", j9, stdin=f"Note: {TEXT}\n")
posted = json.dumps(I["PROJ-9"]["comments"][-1]["body"], ensure_ascii=False)
check("unicode: a comment read from stdin reaches Jira intact", code == 0 and TEXT in posted, out + err + posted)

# A create that fails half way leaves no job folder behind, so it can be rerun.
I["PROJ-10"] = {"summary": "Flaky fetch", "labels": ["factory"], "status": "Sprint Ready", "description": adf("x"), "comments": []}
folders = set((repo / "factory/jobs").iterdir())
fj.STATE["faults"] = [{"method": "GET", "prefix": "/rest/api/3/issue/PROJ-10", "skip": issue_gets - 1}]
r = run("create", "PROJ-10", ok=False)
check("failed create: an error, and no job folder left", r.returncode != 0 and set((repo / "factory/jobs").iterdir()) == folders
      and not fj.STATE["faults"], (r.stderr, sorted(p.name for p in set((repo / "factory/jobs").iterdir()) - folders)))
r = run("create", "PROJ-10", ok=False)
check("failed create: running it again works", r.returncode == 0 and (repo / "factory/jobs" / r.stdout.strip() / "job.json").exists(), r.stderr)

r = subprocess.run([job, "queue"], capture_output=True, text=True,
                   env={k: v for k, v in env.items() if k not in ("JIRA_API_TOKEN",)})
check("missing token: clear error", r.returncode == 1 and "set JIRA_EMAIL and JIRA_API_TOKEN" in r.stderr, r.stderr)
r = subprocess.run([job, "summary"], capture_output=True, text=True, env=env)
check("summary shows the Jira key", "PROJ-1" in r.stdout, r.stdout)

srv.shutdown()
check.finish()

"""Workspace mode: one factory beside several repos, each with its own remote and
checks; intake's REPO, the label override, branch_format, CRLF checks, a full
job in git mode with Jira, and splitting an issue that spans repos into tickets."""
import json, os, shutil, subprocess, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import BASE_ENV, LIB, REPO, Checks, minimal_path, workdir
import fake_jira as fj
sys.path.insert(0, str(LIB))
from tracker import adf_to_text

SRC = REPO
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
check = Checks()
def git(*a, cwd):
    r = subprocess.run(GIT + list(a), cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0: raise SystemExit(f"git {' '.join(a)} in {cwd}: {r.stderr}")
    return r.stdout

srv = fj.serve(); I = fj.STATE["issues"]; I.clear()
work = workdir("workspace")
wsroot = work / "clones"         # where the user clones everything; not a git repo
wsroot.mkdir()
for d in ("factory", ".claude"):
    shutil.copytree(SRC / d, wsroot / d, ignore=shutil.ignore_patterns("__pycache__", "jobs"))
REPOS = {"repo-a": "A.md", "repo-b": "B.md", "repo-c": "C.md"}
(wsroot / "factory/checks").mkdir()
for name, marker in REPOS.items():
    origin = work / "hosted" / f"{name}.git"
    git("init", "-q", "--bare", str(origin), cwd=work)
    r = wsroot / name; r.mkdir()
    git("init", "-q", cwd=r); (r / marker).write_text(name); (r / "README.md").write_text(name)
    git("add", "-A", cwd=r); git("commit", "-qm", "init", cwd=r)
    git("remote", "add", "origin", str(origin), cwd=r); git("push", "-q", "origin", "main", cwd=r)
    # Each repo's checks prove they ran in the right repo, and fail on a BAD file.
    (wsroot / f"factory/checks/{name}.sh").write_text(f"#!/usr/bin/env bash\nset -euo pipefail\ntest -f {marker}\nif [ -f BAD ]; then echo BAD-file; exit 1; fi\necho checked {name}\n")
crlf = wsroot / "factory/checks/repo-b.sh"; crlf.write_bytes(crlf.read_bytes().replace(b"\n", b"\r\n"))
cfg = json.loads((wsroot / "factory/config.json").read_text())
cfg["branch_format"] = "dev_{issue}_{slug}"
cfg["tracker"] = {"type": "jira", "site": f"http://127.0.0.1:{srv.server_port}", "project": "PROJ",
                  "ready_status": "Sprint Ready", "start_status": "In Progress", "merged_status": "QA Ready"}
cfg["codehost"] = {"type": "git"}
cfg["repos"] = {n: {"description": f"the {n} service",
                    "codehost": {"type": "git", "pr_url": f"https://dev.azure.com/example-org/Proj/_git/{n}/pullrequestcreate?sourceRef={{branch}}&targetRef={{base}}"}}
                for n in REPOS}
(wsroot / "factory/config.json").write_text(json.dumps(cfg, indent=2))
env = dict(BASE_ENV, JIRA_EMAIL="bot@example.com", JIRA_API_TOKEN="secret-token", NO_PROXY="127.0.0.1",
           no_proxy="127.0.0.1", CLAUDE_CONFIG_DIR=str(work / "cfg"), PATH=minimal_path())
def job(*a, stdin=None, ok=True):
    r = subprocess.run([str(wsroot / "factory/bin/job"), *a], input=stdin, capture_output=True, text=True, env=env)
    if ok and r.returncode != 0: raise SystemExit(f"FAILED job {' '.join(a)}:\n{r.stdout}{r.stderr}")
    return r
def gate(*a):
    return subprocess.run([str(wsroot / "factory/bin/gate"), *a], capture_output=True, text=True, env=env)
def route(jid):
    r = subprocess.run([str(wsroot / "factory/bin/route"), jid], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
def st(jid): return json.loads((wsroot / "factory/jobs" / jid / "job.json").read_text())
def jd(jid): return wsroot / "factory/jobs" / jid
desc = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x"}]}]}
def ticket(n, title, labels=("factory",)):
    I[f"PROJ-{n}"] = {"summary": title, "labels": list(labels), "status": "Sprint Ready", "description": desc, "comments": []}
def accept(jid, repo_line):
    hdr = "DECISION: ACCEPT\nRISK: low\nSURFACES: none\nRESCOPED: no\n" + (f"REPO: {repo_line}\n" if repo_line else "")
    (jd(jid) / "intake.md").write_text(hdr + "\n## Summary\n- ok\n\n## Scope\nDo it.\n")
    route(jid)
def build(jid, filename):
    j = st(jid); (jd(jid) / "spec.md").write_text(f"# Change {filename}\n"); job("advance", jid, "build")
    wt = Path(j["worktree"]); (wt / filename).write_text("x\n")
    git("add", filename, cwd=wt); git("commit", "-qm", f"add {filename}", cwd=wt); git("push", "-q", "-u", "origin", j["branch"], cwd=wt)
    (jd(jid) / "build.md").write_text(f"STATUS: READY\n\n## Summary\n- adds {filename}\n")
    job("advance", jid, "gate")

r = job("check-tracker")
check("check-tracker says which bash runs the checks", "checks run with: bash (Linux)" in r.stdout, r.stdout + r.stderr)
check("check-tracker covers every repo's origin and PR link",
      all(f"{n}: origin has main" in r.stdout and f"{n}: create-PR link: https://dev.azure.com/example-org/Proj/_git/{n}/" in r.stdout for n in REPOS),
      r.stdout + r.stderr)

ticket(1, "Change B")
j1 = job("create", "PROJ-1").stdout.strip()
check("branch_format gives the team's convention", st(j1)["branch"] == "dev_PROJ-1_change-b", st(j1)["branch"])
check("workspace job starts with no repo and no worktree", st(j1)["repo"] is None and st(j1)["worktree"] is None, st(j1))
r = job("header", j1)
check("header before intake: no repo yet, job folder present",
      f"Job: {j1}. Job folder: {jd(j1)}/. Workspace: {wsroot}." in r.stdout and "Repo: not chosen yet" in r.stdout
      and "Worktree" not in r.stdout and r.stdout.strip().endswith("Issue: PROJ-1."), r.stdout)
accept(j1, None)
r = job("advance", j1, "spec", ok=False)
check("ACCEPT without REPO is refused", r.returncode != 0 and "no REPO: line" in r.stderr, r.stderr)
accept(j1, "repo-z")
r = job("advance", j1, "spec", ok=False)
check("ACCEPT with an unknown REPO is refused, listing the repos", r.returncode != 0 and "repo-a, repo-b, repo-c" in r.stderr, r.stderr)
check("refusals left Jira alone (no comment, still Sprint Ready)", not I["PROJ-1"]["comments"] and I["PROJ-1"]["status"] == "Sprint Ready")
accept(j1, "repo-b")
job("advance", j1, "spec")
s = st(j1)
check("REPO: repo-b → job placed in repo-b, worktree under the workspace",
      s["repo"] == "repo-b" and s["worktree"] == str(wsroot / ".worktrees/repo-b/001") and (Path(s["worktree"]) / "B.md").exists(), s)
check("worktree is named after the job number; job folder and branch keep the full id",
      j1 == "001-change-b" and Path(s["worktree"]).name == "001" and jd(j1).is_dir() and s["branch"] == "dev_PROJ-1_change-b", (j1, s))
r = job("header", j1)
check("header after ACCEPT: repo, worktree, branch, base",
      f"Repo: repo-b at {wsroot / 'repo-b'}. Worktree: {s['worktree']}. Branch: {s['branch']}. Base: origin/main." in r.stdout, r.stdout)
check("intake comment names the repo", "repo: repo-b" in adf_to_text(I["PROJ-1"]["comments"][-1]["body"]))
check("branch exists in repo-b only", s["branch"] in git("branch", "--list", s["branch"], cwd=wsroot / "repo-b")
      and not git("branch", "--list", s["branch"], cwd=wsroot / "repo-a").strip())

build(j1, "feature-b.txt")
r = gate(j1, "--try")
check("gate --try runs repo-b's checks and writes nothing", r.returncode == 0 and "checked repo-b" in r.stdout
      and not (jd(j1) / f"round-{st(j1)['round']}" / "checks.md").exists(), r.stdout + r.stderr)
r = gate(j1)
check("repo-b's checks script really has CRLF endings", b"\r\n" in crlf.read_bytes())
check("gate PASS with repo-b's checks (CRLF script)", r.returncode == 0 and "GATE: PASS" in r.stdout
      and "checked repo-b" in (jd(j1) / "round-1/checks.log").read_text(), r.stdout + r.stderr)
job("advance", j1, "review"); (jd(j1) / "round-1/review-code.md").write_text("VERDICT: PASS\n"); job("sync", j1)
job("advance", j1, "approve"); (jd(j1) / "decision.md").write_text("APPROVE\n\n## Summary\n- good\n")
r = job("merge", j1)
c = adf_to_text(I["PROJ-1"]["comments"][-1]["body"])
check("handoff names the repo and links repo-b's create-PR page",
      st(j1)["stage"] == "handoff" and "in `repo-b`" in c and "_git/repo-b/pullrequestcreate" in c, c)
team = work / "team-b"; git("clone", "-q", str(work / "hosted/repo-b.git"), str(team), cwd=work)
git("merge", "-q", "--no-ff", "-m", "Merged PR", f"origin/{s['branch']}", cwd=team); git("push", "-q", "origin", "main", cwd=team)
job("queue")
check("merge in repo-b detected: merged, QA Ready, worktree gone",
      st(j1)["stage"] == "merged" and I["PROJ-1"]["status"] == "QA Ready" and not Path(s["worktree"]).exists(),
      (st(j1)["stage"], I["PROJ-1"]["status"]))

# A label fixes the repo, whatever intake says.
ticket(2, "Labeled for C", labels=("factory", "factory:repo-c"))
j2 = job("create", "PROJ-2").stdout.strip()
check("issue.md tells intake the label fixed the repo", "fixed to `repo-c`" in (jd(j2) / "issue.md").read_text())
accept(j2, "repo-a")
job("advance", j2, "spec")
check("label overrides intake's REPO", st(j2)["repo"] == "repo-c" and "/.worktrees/repo-c/" in st(j2)["worktree"], st(j2))

# Two jobs in two repos at once; the gate runs each repo's own checks.
ticket(3, "Bad change in A")
j3 = job("create", "PROJ-3").stdout.strip(); accept(j3, "repo-a"); job("advance", j3, "spec")
build(j3, "BAD")
r = gate(j3)
check("gate FAIL in repo-a from repo-a's checks", r.returncode == 1 and "GATE: FAIL" in r.stdout, r.stdout + r.stderr)
r = job("queue")
check("queue shows running jobs with their repos",
      f"resume\tPROJ-2\t{j2}\tspec\tround 0 in repo-c" in r.stdout and f"resume\tPROJ-3\t{j3}\tgate\tround 1 in repo-a" in r.stdout, r.stdout)
r = job("summary")
check("summary has a repo column", "repo" in r.stdout.splitlines()[0] and "repo-b" in r.stdout and "repo-c" in r.stdout, r.stdout)

# Nothing was written into the repos themselves.
dirty = {n: git("status", "--porcelain", "--untracked-files=all", cwd=wsroot / n).strip() for n in REPOS}
check("every repo's checkout stays clean (worktrees live outside them)", not any(dirty.values()), dirty)
check("no .worktrees inside any repo", not any((wsroot / n / ".worktrees").exists() for n in REPOS))

# A reject before intake placed the job: nothing to discard, no crash.
ticket(4, "Question first")
j4 = job("create", "PROJ-4").stdout.strip()
(jd(j4) / "intake.md").write_text("DECISION: NEEDS-INFO\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- q\n\n## Questions\n1. Which?\n")
job("advance", j4, "needs-info")
r = job("reject", j4, stdin="Nah.\n")
check("rejecting an unplaced workspace job works", st(j4)["stage"] == "rejected", r.stderr)

cfg3 = dict(cfg, branch_format="dev_{ticket}"); (wsroot / "factory/config.json").write_text(json.dumps(cfg3))
ticket(5, "Bad format"); r = job("create", "PROJ-5", ok=False)
check("unknown branch_format placeholder: clear error", r.returncode == 1 and "unknown placeholder" in r.stderr, r.stderr)
cfg3 = dict(cfg, branch_format="dev..{issue}"); (wsroot / "factory/config.json").write_text(json.dumps(cfg3))
r = job("create", "PROJ-5", ok=False)
check("invalid branch name from branch_format: clear error", r.returncode == 1 and "isn't a valid branch name" in r.stderr, r.stderr)
(wsroot / "factory/config.json").write_text(json.dumps(cfg, indent=2))

# GitHub Issues in a workspace needs to know whose issues.
cfg2 = dict(cfg, tracker={"type": "github"}); (wsroot / "factory/config.json").write_text(json.dumps(cfg2))
r = job("check-tracker", ok=False)
check("GitHub Issues in a workspace without tracker.repo: clear error", r.returncode == 1 and "tracker.repo" in r.stderr, r.stderr)
(wsroot / "factory/config.json").write_text(json.dumps(cfg, indent=2))

# An issue that spans repos: intake's `## Other repos` becomes one ticket per repo.
cfg4 = dict(cfg, concurrency=10); (wsroot / "factory/config.json").write_text(json.dumps(cfg4, indent=2))
fj.STATE["next"] = 100; fj.STATE["links"] = []
fj.STATE["create_status"] = "Grooming Ready"  # new tickets land here; it moves straight to Sprint Ready
def accept_split(jid, repo, other):
    (jd(jid) / "intake.md").write_text(f"DECISION: ACCEPT\nRISK: low\nSURFACES: none\nRESCOPED: yes\nREPO: {repo}\n\n"
                                       f"## Summary\n- ok\n\n## Scope\nThe API part.\n\n## Other repos\n{other}")
    route(jid)
ticket(20, "Export orders as CSV")
j20 = job("create", "PROJ-20").stdout.strip()
accept_split(j20, "repo-a", "### repo-z: Nope\nSomething.\n")
r = job("advance", j20, "spec", ok=False)
check("split: an unknown repo in Other repos is refused, listing the repos",
      r.returncode != 0 and "Other repos names repo-z" in r.stderr and "repo-a, repo-b, repo-c" in r.stderr, r.stderr)
accept_split(j20, "repo-a", "### repo-a: Same repo\nSomething.\n")
r = job("advance", j20, "spec", ok=False)
check("split: the job's own repo in Other repos is refused", r.returncode != 0 and "the repo this job builds in" in r.stderr, r.stderr)
accept_split(j20, "repo-a", "### repo-b: Empty\n")
r = job("advance", j20, "spec", ok=False)
check("split: a part with no scope is refused", r.returncode != 0 and "repo-b has no scope" in r.stderr, r.stderr)
check("split refusals filed nothing and left the issue alone",
      "PROJ-100" not in I and not I["PROJ-20"]["comments"] and st(j20)["stage"] == "intake")
accept_split(j20, "repo-a", "### repo-b: Add a CSV button to the orders page\nA button that downloads the export.\n\n"
                            "### `repo-c`: Email the CSV export nightly\nWAITS: yes\nA nightly job that mails the file.\n")
r = job("advance", j20, "spec")
s20 = st(j20)
check("split: the job itself still builds in repo-a", s20["stage"] == "spec" and s20["repo"] == "repo-a" and "warning" not in r.stderr, r.stderr)
check("split: one ticket per other repo, with intake's titles",
      I.get("PROJ-100", {}).get("summary") == "Add a CSV button to the orders page"
      and I.get("PROJ-101", {}).get("summary") == "Email the CSV export nightly", {k: I[k]["summary"] for k in I if k >= "PROJ-100"})
check("split: an independent part is queued and fixed to its repo", sorted(I["PROJ-100"]["labels"]) == ["factory", "factory:repo-b"], I["PROJ-100"]["labels"])
check("split: a part that waits isn't queued yet", I["PROJ-101"]["labels"] == ["factory:repo-c"], I["PROJ-101"]["labels"])
check("split: the queued ticket moves to ready_status; the waiting one stays put",
      I["PROJ-100"]["status"] == "Sprint Ready" and I["PROJ-101"]["status"] == "Grooming Ready",
      (I["PROJ-100"]["status"], I["PROJ-101"]["status"]))
d100 = adf_to_text(I["PROJ-100"]["description"])
check("split: the ticket names its parent, its scope and the original request",
      "Split from PROJ-20" in d100 and "`repo-b`" in d100 and "A button that downloads the export." in d100
      and "## Original request" in d100 and "WAITS" not in adf_to_text(I["PROJ-101"]["description"]), d100)
check("split: each ticket is linked to the parent in Jira",
      fj.STATE["links"] == [("Relates", "PROJ-100", "PROJ-20"), ("Relates", "PROJ-101", "PROJ-20")], fj.STATE["links"])
c = adf_to_text(I["PROJ-20"]["comments"][-1]["body"])
check("split: the parent's issue lists the new tickets",
      "split into other repos" in c and "`repo-b`: PROJ-100" in c and "`repo-c`: PROJ-101 (queued once this ticket merges)" in c, c)
check("split: job.json records the tickets",
      [(x["repo"], x["issue"], x["queued"]) for x in s20["split_issues"]] == [("repo-b", "PROJ-100", True), ("repo-c", "PROJ-101", False)], s20)
r = job("queue")
check("split: the queued ticket shows up as new work; the waiting one doesn't",
      "new\tPROJ-100" in r.stdout and "PROJ-101" not in r.stdout, r.stdout)

j100 = job("create", "PROJ-100").stdout.strip()
md = (jd(j100) / "issue.md").read_text()
check("split ticket's job knows its parent and its repo", st(j100)["split_from"] == "PROJ-20"
      and "fixed to `repo-b`" in md and "Leave `## Other repos` out" in md, md)
accept_split(j100, "repo-b", "### repo-a: Back again\nThe API part, again.\n")
job("advance", j100, "spec")
check("a split ticket's job never splits again", "PROJ-102" not in I and not st(j100).get("split_issues")
      and st(j100)["repo"] == "repo-b", st(j100))

job("merged", j20)
check("parent merged: the waiting ticket joins the queue", "factory" in I["PROJ-101"]["labels"]
      and "PROJ-20 merged" in adf_to_text(I["PROJ-101"]["comments"][-1]["body"]) and st(j20)["split_issues"][1]["queued"]
      and I["PROJ-101"]["status"] == "Sprint Ready", I["PROJ-101"])

# Filing problems never block the job: they're warnings, and named on the issue.
cfg5 = dict(cfg4, tracker=dict(cfg["tracker"], link_type="Nope")); (wsroot / "factory/config.json").write_text(json.dumps(cfg5, indent=2))
ticket(21, "Spans three repos")
j21 = job("create", "PROJ-21").stdout.strip()
accept_split(j21, "repo-c", "### repo-a: Part A\nA.\n\n### repo-b: Part B\nB.\n")
fj.STATE["faults"] = [{"method": "POST", "path": "/rest/api/3/issue", "skip": 0}]
fj.STATE["create_status"] = "Open"  # a workflow where Open can't jump to Sprint Ready
r = job("advance", j21, "spec")
c = adf_to_text(I["PROJ-21"]["comments"][-1]["body"])
check("split: a failed create, a bad link type and a blocked move are warnings; the job goes on",
      st(j21)["stage"] == "spec" and "no ticket filed for repo-a" in r.stderr and "wasn't linked" in r.stderr
      and "tracker.link_type" in r.stderr and "move it to Sprint Ready by hand" in r.stderr, r.stderr)
check("split: the issue says which part to file by hand",
      "`repo-a`: couldn't be filed, so file it by hand" in c and "`repo-b`: PROJ-102" in c
      and [x["repo"] for x in st(j21)["split_issues"]] == ["repo-b"], c)
(wsroot / "factory/config.json").write_text(json.dumps(cfg, indent=2))

srv.shutdown()
check.finish()

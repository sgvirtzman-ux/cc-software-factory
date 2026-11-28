"""Code hosts, end to end. Git mode with Jira: a local bare repo is the host and a
teammate's clone merges (no-ff, rebase, squash). GitHub mode with a fake `gh`:
the factory opens, escalates and merges the PR."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import FAKE_BIN, LIB, Checks, minimal_path, workdir
import fake_jira as fj
from pipeline import Factory
sys.path.insert(0, str(LIB))
from tracker import adf_to_text as adf_text

check = Checks()

# ================= git mode + Jira ==================
print("--- git mode + Jira")
srv = fj.serve()
I = fj.STATE["issues"]
PR_URL = "https://dev.azure.com/example-org/Proj/_git/app/pullrequestcreate?sourceRef={branch}&targetRef={base}"
def jira_git(cfg):
    cfg["tracker"] = {"type": "jira", "site": f"http://127.0.0.1:{srv.server_port}", "project": "PROJ",
                      "ready_status": "Sprint Ready", "start_status": "In Progress", "merged_status": "QA Ready"}
    cfg["codehost"] = {"type": "git", "pr_url": PR_URL}
f = Factory("git-mode", jira_git, {"JIRA_EMAIL": "bot@example.com", "JIRA_API_TOKEN": "secret-token",
                                   "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1", "PATH": minimal_path()})
I.clear()
desc = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "x"}]}]}
for n, title in [(11, "Greeting"), (12, "Rebased"), (13, "Squashed"), (14, "Feedback"), (15, "Rejected")]:
    I[f"PROJ-{n}"] = {"summary": title, "labels": ["factory"], "status": "Sprint Ready", "description": desc, "comments": []}

r = f.job("check-tracker")
check("check-tracker shows origin and the PR link (gh isn't even on PATH)",
      "origin: " in r.stdout and "origin has main" in r.stdout and "sourceRef=factory%2F000-example&targetRef=main" in r.stdout, r.stdout + r.stderr)

j11 = f.to_handoff_ready("PROJ-11", "Add a greeting", "greeting.txt")
s = f.state(j11)
check("build → gate in git mode: no PR, pushed head recorded", s["pr"] is None and s["pushed_head"], s)
f.team_commit_on_main("team1.txt")  # main moves on while the job is in review
r = f.job("merge", j11)
s = f.state(j11)
check("APPROVE → handoff", s["stage"] == "handoff" and "handoff" in r.stdout, r.stdout + r.stderr)
check("handoff: branch synced with the new main and pushed",
      f.remote_has(s["branch"]) and "team1.txt" in f.git("ls-tree", "-r", "--name-only", f"origin/{s['branch']}", cwd=f.team if f.git("fetch", "-q", "origin", cwd=f.team) == "" else f.team))
c = json.dumps(I["PROJ-11"]["comments"][-1]["body"])
check("handoff: Jira comment has heading, branch, create-PR link, title, QA Ready note",
      "ready for a pull request" in c and s["branch"] in c and f"sourceRef=factory%2F{j11}" in c
      and "PROJ-11: Add a greeting" in c and "moves this ticket to QA Ready" in c, c[:600])
check("handoff: label added, ticket still In Progress",
      "factory:ready-for-pr" in I["PROJ-11"]["labels"] and I["PROJ-11"]["status"] == "In Progress", I["PROJ-11"])
r = f.job("queue")
check("queue: handoff is a wait row, not merged yet", f"wait\tPROJ-11\t{j11}\thandoff\topen a PR from {s['branch']}" in r.stdout, r.stdout)
r = f.job("merged", j11, ok=False) if False else None
r = f.job("advance", j11, "merged", ok=False)
check("advance → merged refuses before the team merges", r.returncode != 0 and "isn't on origin/main" in r.stderr, r.stderr)

f.team_merge(s["branch"], "no-ff")
r = f.job("queue")
s = f.state(j11)
check("no-ff merge detected: job merged", s["stage"] == "merged", (s["stage"], r.stdout, r.stderr))
check("merged: Jira moved to QA Ready, label off, comment says merged into main",
      I["PROJ-11"]["status"] == "QA Ready" and "factory:ready-for-pr" not in I["PROJ-11"]["labels"]
      and "was merged into `main`. Moved to QA Ready." in adf_text(I["PROJ-11"]["comments"][-1]["body"]),
      (I["PROJ-11"]["status"], I["PROJ-11"]["labels"], adf_text(I["PROJ-11"]["comments"][-1]["body"])))
check("merged: worktree removed, remote branch deleted", not Path(s["worktree"]).exists() and not f.remote_has(s["branch"]))

j12 = f.to_handoff_ready("PROJ-12", "Rebase me", "rebased.txt")
f.commit_and_push(j12, "rebased2.txt", "second\n")  # a second commit, pushed before approval
f.job("merge", j12)
f.team_commit_on_main("team2.txt")
f.team_merge(f.state(j12)["branch"], "rebase")
f.job("queue")
check("rebase merge detected by patch: job merged, QA Ready",
      f.state(j12)["stage"] == "merged" and I["PROJ-12"]["status"] == "QA Ready", (f.state(j12)["stage"], I["PROJ-12"]["status"]))

j13 = f.to_handoff_ready("PROJ-13", "Squash me", "squashed.txt")
f.commit_and_push(j13, "squashed2.txt", "second\n")
f.job("merge", j13)
f.team_merge(f.state(j13)["branch"], "squash")
f.job("queue")
check("squash merge isn't auto-detected (stays handoff)", f.state(j13)["stage"] == "handoff", f.state(j13)["stage"])
r = f.job("merged", "PROJ-13")
check("job merged (by issue key) finishes it: merged, QA Ready",
      f.state(j13)["stage"] == "merged" and I["PROJ-13"]["status"] == "QA Ready", (r.stdout, r.stderr))

j14 = f.to_handoff_ready("PROJ-14", "Needs a tweak", "tweak.txt")
f.job("merge", j14)
r = f.job("feedback", j14, stdin="The PR reviewer wants the file renamed.\n")
s = f.state(j14)
check("feedback on handoff: back to build, round 2, label off",
      s["stage"] == "build" and s["round"] == 2 and "factory:ready-for-pr" not in I["PROJ-14"]["labels"], (s["stage"], s["round"]))
check("feedback file says it came after hand-off", "after the branch was handed off" in (f.dir(j14) / "feedback-1.md").read_text())

j15 = f.to_handoff_ready("PROJ-15", "Not wanted", "nope.txt")
f.job("merge", j15)
br = f.state(j15)["branch"]
r = f.job("reject", j15, stdin="We'll do it differently.\n")
check("reject on handoff: remote branch deleted, labels off, back to Sprint Ready",
      not f.remote_has(br) and not any(l.startswith("factory") for l in I["PROJ-15"]["labels"])
      and I["PROJ-15"]["status"] == "Sprint Ready", (f.remote_has(br), I["PROJ-15"]["labels"], I["PROJ-15"]["status"], r.stderr))
srv.shutdown()

# ================= github mode + GitHub Issues (unchanged behaviour) =================
print("--- github mode + GitHub Issues")
def gh_cfg(cfg):
    cfg["tracker"] = {"type": "github"}; cfg["codehost"] = {"type": "github"}
ghdir = workdir("fake-gh"); gstate = ghdir / "gh_state.json"; glog = ghdir / "gh_log.jsonl"
gstate.write_text(json.dumps({"issues": {n: {"title": t, "body": "b", "author": "alice", "labels": ["factory"],
    "state": "OPEN", "comments": []} for n, t in (("21", "Add subtract"), ("22", "Blocked one"))}}))
glog.write_text("")
g = Factory("github-mode", gh_cfg, {"PATH": f"{FAKE_BIN}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(gstate), "FAKE_GH_LOG": str(glog)})
j21 = g.to_handoff_ready("21", "Add a subtract function", "subtract.txt")
st = json.loads(gstate.read_text())
pr = st["prs"].get("100")
check("build → gate opens the draft PR (no longer the builder's job)",
      pr and pr["draft"] and pr["head"] == g.state(j21)["branch"] and pr["base"] == "main"
      and pr["title"] == "Add a subtract function" and pr["body"].startswith("Closes #21\n\n- adds subtract.txt")
      and g.state(j21)["pr"] == 100, pr)
r = g.job("merge", j21)
st = json.loads(gstate.read_text())
check("APPROVE → PR marked ready and merged with --merge, job merged",
      st["prs"]["100"]["state"] == "MERGED" and st["prs"]["100"]["method"] == "--merge" and g.state(j21)["stage"] == "merged",
      (st["prs"]["100"], r.stdout, r.stderr))
calls = [json.loads(l) for l in glog.read_text().splitlines()]
check("no issue comment for the merge on GitHub (Closes #n does it)",
      not any(c[:2] == ["issue", "close"] for c in calls), [c for c in calls if c[0] == "issue"])

# A PR the builder already opened (older builder prompt) is reused, not duplicated.
jid = g.job("create", "22").stdout.strip(); d = g.dir(jid)
(d / "intake.md").write_text("DECISION: ACCEPT\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- ok\n")
g.bin("route", jid); g.job("advance", jid, "spec"); (d / "spec.md").write_text("# Blocked one\n"); g.job("advance", jid, "build")
g.commit_and_push(jid, "b.txt", "b\n")
st = json.loads(gstate.read_text())
st["prs"]["200"] = {"head": g.state(jid)["branch"], "base": "main", "title": "old", "body": "", "draft": True,
                    "state": "OPEN", "comments": [], "url": "https://github.com/o/r/pull/200"}
gstate.write_text(json.dumps(st))
(d / "build.md").write_text("STATUS: READY\n\n## Summary\n- b\n")
g.job("advance", jid, "gate")
check("existing PR reused", g.state(jid)["pr"] == 200 and len(json.loads(gstate.read_text())["prs"]) == 2, g.state(jid)["pr"])
# Escalate: needs-human marks the PR ready and comments on it.
g.bin("gate", jid); g.job("advance", jid, "review")
(d / "round-1" / "review-code.md").write_text("VERDICT: PASS\n"); g.job("sync", jid); g.job("advance", jid, "approve")
(d / "decision.md").write_text("ESCALATE\n\n## Summary\n- needs a product call\n")
g.job("advance", jid, "needs-human")
st = json.loads(gstate.read_text())
check("ESCALATE: PR 200 ready, decision commented, issue labeled",
      not st["prs"]["200"]["draft"] and "needs a product call" in st["prs"]["200"]["comments"][-1]
      and "factory:needs-human" in st["issues"]["22"]["labels"], st["prs"]["200"])
r = g.job("queue")
check("queue: needs-human row points at the PR", f"merge PR #200, or give feedback" in r.stdout, r.stdout)

check.finish()

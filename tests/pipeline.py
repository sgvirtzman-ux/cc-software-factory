"""Helpers that drive a factory job through its stages the way the agents would."""
import json, os, shutil, subprocess
from pathlib import Path
from support import BASE_ENV, REPO, workdir

SRC = REPO
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]

class Factory:
    def __init__(self, name, config_patch, env_extra):
        self.work = workdir(name)
        self.repo = self.work / "repo"
        shutil.copytree(SRC, self.repo, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests"))
        (self.repo / "factory/checks.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\ntest -f README.md\n")
        cfg = json.loads((self.repo / "factory/config.json").read_text())
        config_patch(cfg)
        (self.repo / "factory/config.json").write_text(json.dumps(cfg, indent=2))
        self.cfg = cfg
        self.env = dict(BASE_ENV, CLAUDE_CONFIG_DIR=str(self.work / "cfg"), **env_extra)
        self.git("init", "-q"); self.git("add", "-A"); self.git("commit", "-qm", "init")
        self.origin = self.work / "origin.git"
        self.git("init", "-q", "--bare", str(self.origin), cwd=self.work)
        self.git("remote", "add", "origin", str(self.origin)); self.git("push", "-q", "origin", "main")
        self.team = self.work / "team"  # a teammate's clone, for merging on the "host"
        self.git("clone", "-q", str(self.origin), str(self.team), cwd=self.work)

    def git(self, *a, cwd=None):
        r = subprocess.run(GIT + list(a), cwd=cwd or self.repo, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"git {' '.join(a)} failed: {r.stderr}")
        return r.stdout

    def job(self, *a, stdin=None, ok=True):
        r = subprocess.run([str(self.repo / "factory/bin/job"), *a], input=stdin, capture_output=True,
                           text=True, env=self.env)
        if ok and r.returncode != 0:
            raise SystemExit(f"FAILED job {' '.join(a)}:\n{r.stdout}{r.stderr}")
        return r

    def bin(self, name, *a, ok=True):
        r = subprocess.run([str(self.repo / "factory/bin" / name), *a], capture_output=True, text=True, env=self.env)
        if ok and r.returncode != 0:
            raise SystemExit(f"FAILED {name} {' '.join(a)}:\n{r.stdout}{r.stderr}")
        return r

    def dir(self, jid): return self.repo / "factory/jobs" / jid
    def state(self, jid): return json.loads((self.dir(jid) / "job.json").read_text())

    def to_handoff_ready(self, issue, title, filename):
        """create → intake ACCEPT → spec → build (commit + push) → gate → review PASS → approve."""
        jid = self.job("create", issue).stdout.strip()
        d = self.dir(jid)
        (d / "intake.md").write_text("DECISION: ACCEPT\nRISK: low\nSURFACES: none\nRESCOPED: no\n\n## Summary\n- ok\n\n## Scope\nDo it.\n")
        self.bin("route", jid)
        self.job("advance", jid, "spec")
        (d / "spec.md").write_text(f"# {title}\n\n> Issue: x\n")
        self.job("advance", jid, "build")
        self.commit_and_push(jid, filename, "first\n")
        (d / "build.md").write_text(f"STATUS: READY\n\n# Build: {title}\n\n## Summary\n- adds {filename}\n\n## Log\n")
        self.job("advance", jid, "gate")
        g = self.bin("gate", jid, ok=False)
        assert "GATE: PASS" in g.stdout, g.stdout + g.stderr
        self.job("advance", jid, "review")
        (d / f"round-{self.state(jid)['round']}" / "review-code.md").write_text("VERDICT: PASS\n\n## Summary\n- fine\n")
        self.job("sync", jid)
        self.job("advance", jid, "approve")
        (d / "decision.md").write_text("APPROVE\n\n## Summary\n- meets the spec\n\n## Why\nGood.\n")
        return jid

    def commit_and_push(self, jid, filename, content):
        wt = Path(self.state(jid)["worktree"])
        (wt / filename).write_text(content)
        self.git("add", filename, cwd=wt); self.git("commit", "-qm", f"add {filename}", cwd=wt)
        self.git("push", "-q", "-u", "origin", self.state(jid)["branch"], cwd=wt)

    def team_commit_on_main(self, filename):
        self.git("pull", "-q", "origin", "main", cwd=self.team)
        (self.team / filename).write_text("team\n")
        self.git("add", filename, cwd=self.team); self.git("commit", "-qm", f"team {filename}", cwd=self.team)
        self.git("push", "-q", "origin", "main", cwd=self.team)

    def team_merge(self, branch, how):
        t = self.team
        self.git("fetch", "-q", "origin", cwd=t); self.git("checkout", "-q", "main", cwd=t)
        self.git("reset", "-q", "--hard", "origin/main", cwd=t)
        if how == "no-ff":
            self.git("merge", "-q", "--no-ff", "-m", f"Merged PR: {branch}", f"origin/{branch}", cwd=t)
        elif how == "rebase":
            self.git("checkout", "-q", "-B", "tmp", f"origin/{branch}", cwd=t)
            self.git("rebase", "-q", "origin/main", cwd=t)
            self.git("checkout", "-q", "main", cwd=t); self.git("merge", "-q", "--ff-only", "tmp", cwd=t)
        elif how == "squash":
            self.git("merge", "-q", "--squash", f"origin/{branch}", cwd=t)
            self.git("commit", "-qm", f"Squashed PR: {branch}", cwd=t)
        self.git("push", "-q", "origin", "main", cwd=t)

    def remote_has(self, branch):
        return bool(self.git("ls-remote", "--heads", str(self.origin), branch, cwd=self.work).strip())

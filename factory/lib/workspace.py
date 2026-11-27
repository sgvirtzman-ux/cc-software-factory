"""Where the factory's repos are: one repo, or a workspace of many.

Single repo (no `repos` in config.json): the factory lives inside the repo it
builds, as it always has. The repo is the folder that holds `factory/`, its
checks are `factory/checks.sh`, and worktrees go in `.worktrees/<job-id>`.

Workspace (`repos` in config.json): the factory lives in a folder that holds
several clones side by side, and builds in any of them. Each job targets one
repo, which intake picks (or a `factory:<repo>` label fixes). Each repo has
its own base branch, code host and checks (`factory/checks/<repo>.sh`), and
its worktrees go in the workspace's `.worktrees/<repo>/<job-id>`, outside the
repo, so no repo needs to know the factory exists.

Settings at the top level of config.json (`base_branch`, `codehost`,
`merge_method`) are the defaults each repo can override.
"""

import re
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class WorkspaceError(Exception):
    pass


class Repo:
    def __init__(self, name, path, base, codehost, merge_method, protected_paths, checks, worktrees, description=""):
        self.name = name
        self.path = Path(path)
        self.base = base
        self.codehost = codehost
        self.merge_method = merge_method
        self.protected_paths = protected_paths
        self.checks = Path(checks)
        self.worktrees = Path(worktrees)
        self.description = description

    def worktree(self, job_id):
        return self.worktrees / job_id


class Workspace:
    def __init__(self, config, root):
        self.root = Path(root)
        self.multi = "repos" in config
        defaults = {
            "base": config.get("base_branch", "main"),
            "codehost": config.get("codehost") or {"type": "github"},
            "merge_method": config.get("merge_method", "merge"),
        }
        if not self.multi:
            self.repos = {self.root.name: Repo(
                self.root.name, self.root, protected_paths=config.get("protected_paths", []),
                checks=self.root / "factory" / "checks.sh", worktrees=self.root / ".worktrees", **defaults)}
            return
        self.repos = {}
        for name, r in (config["repos"] or {}).items():
            if not NAME.match(name):
                raise WorkspaceError(f"config.json repos: {name!r} isn't a usable repo name")
            self.repos[name] = Repo(
                name, (self.root / r.get("path", name)).resolve(),
                base=r.get("base_branch", defaults["base"]),
                codehost=r.get("codehost", defaults["codehost"]),
                merge_method=r.get("merge_method", defaults["merge_method"]),
                # The factory's own files live outside every repo here, so
                # there's nothing to protect unless a repo says so.
                protected_paths=r.get("protected_paths", []),
                checks=self.root / r.get("checks", f"factory/checks/{name}.sh"),
                worktrees=self.root / ".worktrees" / name,
                description=r.get("description", ""))
        if not self.repos:
            raise WorkspaceError("config.json repos is empty")

    def get(self, name):
        if name not in self.repos:
            raise WorkspaceError(f"no repo {name!r} in config.json repos ({', '.join(self.repos)})")
        return self.repos[name]

    def for_job(self, job):
        """The job's repo. In a workspace it's chosen at intake; before that, None."""
        if not self.multi:
            return next(iter(self.repos.values()))
        return self.get(job["repo"]) if job.get("repo") else None

    def label_repo(self, labels, queue_label):
        """A repo fixed by a `<queue label>:<repo>` label, or None."""
        if not self.multi:
            return None
        chosen = [n for n in self.repos if f"{queue_label}:{n}" in labels]
        if len(chosen) > 1:
            raise WorkspaceError(f"more than one repo label: {', '.join(f'{queue_label}:{n}' for n in chosen)}")
        return chosen[0] if chosen else None

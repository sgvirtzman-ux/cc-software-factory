"""Code hosts for the factory: GitHub pull requests, or plain git.

Branches, commits, pushes and syncing with the base branch are plain git in
every mode. What differs is the end of a job:

- `github`: the factory opens a draft PR per job, marks it ready, comments on
  it when it escalates, and merges it with `gh pr merge` when the approver
  approves.
- `git`: no pull request API at all, so it works with any host (Azure DevOps,
  GitLab, Bitbucket, GitHub, a bare repo). On approval the factory pushes the
  branch, synced with the base and gated again, and hands it to the team, who
  open the PR and merge it with their usual reviews and policies. The factory
  notices the merge in git history.
"""

import json
import subprocess
from pathlib import Path
from urllib.parse import quote


class HostError(Exception):
    pass


def load(repo):
    """The code host for one repo (a workspace.Repo): its `codehost` setting."""
    kind = repo.codehost.get("type", "github")
    if kind == "github":
        return GitHub(repo)
    if kind == "git":
        return Git(repo)
    raise HostError(f"config.json codehost.type for {repo.name} is {kind!r}; use github or git")


def _run(cmd, cwd, check=True):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise HostError(f"`{' '.join(cmd)}` failed: {(res.stderr or res.stdout).strip()}")
    return res


class GitHub:
    kind = "github"

    def __init__(self, repo):
        self.repo = repo.path
        self.base = repo.base
        self.merge_method = repo.merge_method

    def _gh(self, *args):
        return _run(["gh", *args], self.repo).stdout

    def _gh_json(self, *args):
        return json.loads(self._gh(*args))

    def ensure_pr(self, job, title, body):
        """The job's open PR, opened as a draft if there isn't one: (number, url)."""
        prs = self._gh_json("pr", "list", "--head", job["branch"], "--state", "open", "--json", "number,url")
        if len(prs) > 1:
            raise HostError(f"expected at most one open PR from {job['branch']}, found {len(prs)}")
        if prs:
            return prs[0]["number"], prs[0]["url"]
        url = self._gh("pr", "create", "--draft", "--base", self.base, "--head", job["branch"],
                       "--title", title, "--body", body).strip().splitlines()[-1]
        return int(url.rstrip("/").rsplit("/", 1)[1]), url

    def is_merged(self, job):
        return bool(job.get("pr")) and self._gh_json("pr", "view", str(job["pr"]), "--json", "state")["state"] == "MERGED"

    def _ready(self, job):
        if self._gh_json("pr", "view", str(job["pr"]), "--json", "isDraft")["isDraft"]:
            self._gh("pr", "ready", str(job["pr"]))

    def escalate(self, job, body):
        """Mark the PR ready and put the decision on it. Returns True if posted."""
        if not job.get("pr"):
            return False
        self._ready(job)
        self._gh("pr", "comment", str(job["pr"]), "--body", f"<!-- factory -->\n{body}")
        return True

    def merge(self, job):
        self._ready(job)
        self._gh("pr", "merge", str(job["pr"]), f"--{self.merge_method}")

    def discard(self, job):
        if job.get("pr"):
            self._gh("pr", "close", str(job["pr"]))
            _run(["git", "push", "origin", "--delete", job["branch"]], self.repo, check=False)

    def merged_how(self, job):
        return f"Merged in [PR #{job['pr']}]({job['pr_url']})."

    def check(self):
        return [f"GitHub repo: {self._gh('repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner').strip()}"]


class Git:
    """Plain git: push the branch, let the team open and merge the PR."""

    kind = "git"

    def __init__(self, repo):
        self.repo = repo.path
        self.base = repo.base
        self.pr_url = repo.codehost.get("pr_url")
        self._fetched = False

    def ensure_pr(self, job, title, body):
        return None, None

    def _fetch(self):
        if not self._fetched:
            _run(["git", "fetch", "origin", self.base], self.repo)
            self._fetched = True

    def is_merged(self, job):
        """True when the job's last pushed commit is on the base branch.

        A merge commit or fast-forward keeps the commit itself, so ancestry
        settles it. A rebase merge rewrites the commits; `--cherry-mark` still
        finds them by their changes. A squash merge folds them into one new
        commit, which neither check sees: mark those with `job merged`.
        """
        head = job.get("pushed_head")
        if not head:
            return False  # nothing handed over yet
        self._fetch()
        upstream = f"origin/{self.base}"
        if _run(["git", "merge-base", "--is-ancestor", head, upstream], self.repo, check=False).returncode == 0:
            return True
        marks = _run(["git", "log", "--cherry-mark", "--right-only", "--no-merges", "--format=%m",
                      f"{upstream}...{head}"], self.repo, check=False)
        lines = marks.stdout.split() if marks.returncode == 0 else []
        return bool(lines) and all(m == "=" for m in lines)

    def escalate(self, job, body):
        return False  # no PR: the tracker gets the decision

    def merge(self, job):
        raise HostError("in git mode the team merges; the factory hands the branch over")

    def discard(self, job):
        if job.get("pushed_head"):
            _run(["git", "push", "origin", "--delete", job["branch"]], self.repo, check=False)

    def create_pr_link(self, job):
        if not self.pr_url:
            return None
        return self.pr_url.format(branch=quote(job["branch"], safe=""), base=quote(self.base, safe=""))

    def merged_how(self, job):
        return f"`{job['branch']}` was merged into `{self.base}`."

    def check(self):
        remote = _run(["git", "remote", "get-url", "origin"], self.repo).stdout.strip()
        heads = _run(["git", "ls-remote", "--heads", "origin", self.base], self.repo).stdout.strip()
        if not heads:
            raise HostError(f"origin ({remote}) has no branch {self.base}")
        lines = [f"origin: {remote}", f"origin has {self.base}: {heads.split()[0][:7]}"]
        if self.pr_url:
            lines.append("create-PR link: " + self.create_pr_link({"branch": "factory/000-example"}))
        return lines

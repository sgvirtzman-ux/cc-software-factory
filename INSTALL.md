# Install the software factory

You are a coding agent installing this factory into the user's project. Copy
what's generic, adapt what's repo-specific, verify it, and report. Work in the
project's root. Don't clone this repository.

The source is the folder the user pointed you at. On GitHub, read files from
`https://raw.githubusercontent.com/sgvirtzman-ux/cc-software-factory/main/<path>`.

First, look at the folder you're in. If it's a git repo, the factory builds
that repo: follow the steps as written. If it isn't, and it holds several git
clones side by side, it's a **workspace**: one factory builds in all of those
repos, picking the repo per job. Confirm with the user, then follow the same
steps with the changes in **Workspace install** at the end.

## 0. Choose the tracker and the code host, and check the project

Ask the user where the factory's issues live:

- **GitHub Issues** (the default): the repo's own issues.
- **Jira Cloud**: a Jira project. Ask for the site URL
  (`https://<name>.atlassian.net`), the project key, the issue type for issues
  the factory files (default `Task`), and three statuses from the team's
  workflow: where a ticket waits when it's ready for work (for example
  `Sprint Ready`), what the team moves it to when work starts (`In Progress`),
  and what it moves to when the work is done (`QA Ready`). Jira Data Center
  isn't supported.

Then ask how finished work should reach the default branch:

- **GitHub pull requests** (`github`, the default when `origin` is on GitHub):
  the factory opens a draft PR per job and merges it when the approver
  approves.
- **Plain git** (`git`, for Azure DevOps, GitLab, Bitbucket or any other host,
  or a GitHub repo where the team merges every PR itself): the factory pushes
  each finished branch and hands it to the team, who open the PR and merge it
  with their usual reviews and branch policies. Ask which merge type the team
  usually uses: a merge commit or a rebase merge is detected automatically,
  and a squash merge needs `factory/bin/job merged <id>`. Optionally ask for
  the host's "create pull request" URL, to put a one-click link on each
  hand-off (see `pr_url` below).

Stop and tell the user if any of these fail:

- It's a git repo with an `origin` the user can push to
  (`git push --dry-run origin HEAD` succeeds, or the user confirms they push
  to it).
- Its default branch exists on `origin` (`git ls-remote --heads origin <branch>`).
- With GitHub PRs or GitHub Issues: `gh auth status` shows a user who can push
  to the repo and manage its labels. In git mode with Jira, `gh` isn't needed.
- `claude --version` is 2.1.257 or later (the intake agent runs on Fable).
- Python 3.10 or later is on PATH as `python3`, `python` or `py` (Windows
  usually has only the last two). The scripts use only the standard library
  and find whichever works themselves, so don't add a `python3` alias or shim.
- On Windows: Claude Code runs commands in Git Bash, and so does the factory.
  The gate finds Git Bash itself (from `CLAUDE_CODE_GIT_BASH_PATH`, Git's
  install folder, or PATH), never `C:\Windows\System32\bash.exe`, which is
  WSL's and runs checks in a Linux VM without the machine's tools. If
  `factory/bin/job check-tracker` says it can't find Git Bash, or that checks
  would run in WSL, set `"bash"` in `factory/config.json` to the full path of
  Git's `bash.exe` (usually `C:/Program Files/Git/bin/bash.exe`).
  `git config --global core.longpaths` should print `true`. Worktrees nest
  deep (`.worktrees/<repo>/<job-id>/...`), and Windows' 260-character path
  limit otherwise makes `git worktree add` fail with "Filename too long". If
  it isn't set, ask the user before setting it.
- None of the files in step 1 already exist. If one does, show the user the
  clash and ask before overwriting.
- For Jira: `JIRA_EMAIL` and `JIRA_API_TOKEN` are set in the environment
  Claude Code runs in (see **Jira credentials** in step 2). Never ask the user
  to paste the token into the chat.

Warn the user, but carry on, if this one fails:

- `agent-browser --version` works. The builder and the UX and UI reviewers use
  the `agent-browser` CLI to look at pages. Without it they can't verify
  anything visual, so a project with a UI needs it installed before its first
  `ui` job. A project with no pages can skip it.

## 1. Copy

Copy these files to the same paths in the project, unchanged:

```
.claude/skills/factory/SKILL.md
.claude/agents/intake.md
.claude/agents/spec-writer.md
.claude/agents/builder.md
.claude/agents/reviewer-code.md
.claude/agents/reviewer-security.md
.claude/agents/reviewer-ux.md
.claude/agents/reviewer-ui.md
.claude/agents/approver.md
factory/config.json
factory/checks.sh
factory/bin/job
factory/bin/gate
factory/bin/route
factory/bin/dashboard
factory/lib/tracker.py
factory/lib/codehost.py
factory/lib/workspace.py
factory/lib/shell.py
factory/dashboard.html
```

```bash
SRC=https://raw.githubusercontent.com/sgvirtzman-ux/cc-software-factory/main
for f in <the paths above>; do
  mkdir -p "$(dirname "$f")" && curl -fsSL "$SRC/$f" -o "$f"
done
chmod +x factory/checks.sh factory/bin/job factory/bin/gate factory/bin/route factory/bin/dashboard
```

Don't copy `README.md`, `DESIGN.md`, `INSTALL.md`, `LICENSE`, `examples/`,
`tests/` or `docs/`. They describe the reference, not the user's project.

## 2. Adapt

**`factory/checks.sh`.** Replace the placeholder with the project's real
checks: install dependencies if a fresh worktree needs them, then lint,
typecheck, tests and build, whichever the project has. Find them in
`AGENTS.md`, `CLAUDE.md`, `package.json` scripts, `Makefile`, `pyproject.toml`
and CI config. The script runs with a job's worktree as its working directory.
It must be non-interactive, must not rewrite files (no `--fix`, no
`--update-snapshots`), and must exit non-zero on any failure. Keep
`set -euo pipefail`.

**`factory/config.json`.**

- `base_branch`: the default branch.
- `merge_method` (GitHub PRs only): `merge`, `squash` or `rebase`, one the
  repo allows
  (`gh api repos/{owner}/{repo} --jq '{allow_merge_commit, allow_squash_merge, allow_rebase_merge}'`).
- `codehost`: leave `{ "type": "github" }` for GitHub PRs. For plain git:

  ```json
  "codehost": {
    "type": "git",
    "pr_url": "https://dev.azure.com/<org>/<project>/_git/<repo>/pullrequestcreate?sourceRef={branch}&targetRef={base}"
  }
  ```

  `pr_url` is optional: a link to the host's "create pull request" page, with
  `{branch}` and `{base}` filled in for each job. Build it from
  `git remote get-url origin` rather than asking the user. On Azure DevOps the
  remote holds the organization, project and repo in one of these forms:

  - `https://<org>@dev.azure.com/<org>/<project>/_git/<repo>`
  - `https://<org>.visualstudio.com/<project>/_git/<repo>` (older; may include
    `DefaultCollection/` before the project)
  - `git@ssh.dev.azure.com:v3/<org>/<project>/<repo>`

  and the link is
  `https://dev.azure.com/<org>/<project>/_git/<repo>/pullrequestcreate?sourceRef={branch}&targetRef={base}`
  (keep any URL-encoding, such as `%20`, in the project or repo name). On
  GitLab it's
  `https://<host>/<group>/<repo>/-/merge_requests/new?merge_request[source_branch]={branch}&merge_request[target_branch]={base}`.
  Show the user the link `factory/bin/job check-tracker` prints and ask them to
  open it once. Leave `pr_url` out and the hand-off names the branch instead.
- `branch_format`: how job branches are named. The default is
  `factory/{id}` (`factory/004-dark-mode`). If the team has a convention, ask
  and match it, using `{id}` (the job id), `{issue}` (`12` or `PROJ-12`) and
  `{slug}` (the title's first words): for example `dev_{issue}_{slug}` gives
  `dev_PROJ-12_add-dark-mode-toggle`. Include `{id}` or `{issue}` so each job
  gets its own branch.
- `protected_paths`: keep the two factory entries and don't add the project's
  tests. Features legitimately change existing tests (a new required field
  changes every fixture), and a protected test blocks the job with no way
  through. The code reviewer catches weakened or special-cased tests instead.
  Add a path only when no job should ever change it. They are fnmatch
  patterns, so `*` also matches `/`.
- `ports`: move `base` if the project already uses ports 5100–5199.
- `dashboard_port`: move it from 8000 if something on this machine already
  uses that port.
- `tracker`: leave `{ "type": "github" }` for GitHub Issues. For Jira, replace
  it with:

  ```json
  "tracker": {
    "type": "jira",
    "site": "https://<name>.atlassian.net",
    "project": "<KEY>",
    "issue_type": "Task",
    "ready_status": "Sprint Ready",
    "start_status": "In Progress",
    "merged_status": "QA Ready"
  }
  ```

  - The queue is the project's issues labeled `factory` whose status is
    `ready_status` or `start_status`.
  - When intake accepts an issue, the factory moves it to `start_status`.
  - When its PR merges, the factory moves it to `merged_status` and comments
    with the PR link.
  - When you reject a job that waits on you, an issue still in `start_status`
    goes back to `ready_status`. One a person has moved elsewhere stays put.

  The factory makes one move at a time, so each move must be allowed directly
  in the workflow: `ready_status` → `start_status` → `merged_status`, and
  `start_status` → `ready_status` for rejections.

  If the Jira project's tickets span several repos, give each repo's install
  its own queue label, such as `"queue": "factory:<repo>"`, and tell the user
  to label each ticket for the repo that should build it. With the plain
  `factory` label, every repo's factory would pick up every ticket. Check
  them in the project's workflow, or with `factory/bin/job check-tracker` and a
  test issue. `ready_status` and `start_status` are optional. Without
  `ready_status` the queue is every labeled issue that isn't Done and isn't in
  `merged_status`. Without `start_status` the issue stays put until the merge.
  For any other queue, set `queue_jql` to the full query; it replaces the
  default, and it must still match issues in `start_status`, or the factory
  loses track of its own jobs. Keep the default labels: Jira accepts the colons
  in `factory:needs-info`.
- Leave `concurrency`, `max_rounds`, `routing`, `surface_reviewers` and
  `prices_usd_per_mtok` as they are unless the user asks.

**Jira credentials** (Jira only). The factory talks to Jira's REST API as the
account that owns an Atlassian API token. Every comment and label change shows
up as that account, so suggest a dedicated service account over a person's own.
The user creates the token at
<https://id.atlassian.com/manage-profile/security/api-tokens> and puts both
values in the environment Claude Code runs in, for example in
`.claude/settings.local.json`:

```json
{ "env": { "JIRA_EMAIL": "factory-bot@example.com", "JIRA_API_TOKEN": "<token>" } }
```

Check that `git check-ignore .claude/settings.local.json` prints the path; if it
doesn't, add `/.claude/settings.local.json` to `.gitignore`. Never put the
token in `factory/config.json`, `.claude/settings.json`, `AGENTS.md` or any
other committed file. Claude Code reads the env block when it starts, so
restart Claude Code after adding it.

**Agents.** The checklists are general on purpose. Tailor them to the stack
without changing their structure, file names, first-line formats or one-line
replies:

- `reviewer-security.md`: this project's hot spots (its auth, its data access
  rules, its payment and webhook code, where user input meets queries or HTML).
- `reviewer-ui.md`: where the design tokens, theme and shared components live.
- `reviewer-ux.md`: the kinds of users the app has.
- `approver.md`: what a human must always see in this project, added to the
  ESCALATE list.
- `intake.md`: one line under **Decide** saying what the project is for, so
  intake can tell what belongs.

**`AGENTS.md`** (or `CLAUDE.md`, whichever the project uses; create `AGENTS.md`
if neither exists). Every agent reads it. Make sure it says:

- how to set up a fresh checkout (install dependencies, env files to copy);
- how to start the dev server on a given port, if the project has one;
- the validation commands, matching `factory/checks.sh`.

Then add a short section:

```markdown
## Software factory

`/factory run` builds every open issue labeled `factory` (in <GitHub Issues |
the Jira project KEY>) through intake → spec → build → gate → review → approve,
one branch and PR per issue (<the factory merges on GitHub | the team opens and
merges the PR from the handed-off job branch>). State is in
`factory/jobs/` (gitignored); `factory/bin/job` is its only writer. Checks:
`factory/checks.sh`. Dashboard: `/factory run` starts it (`factory/bin/dashboard`
by hand), serving on 127.0.0.1 only; open http://127.0.0.1:8000/dashboard.html.

Each job works in its own git worktree, a full checkout of the repo at
`.worktrees/<job-id>` (gitignored). A tool run from the repo root that doesn't
read `.gitignore` sees those copies too; exclude `.worktrees/` in its config.
```

**`.gitignore`.** Append these two lines:

```gitignore
/factory/jobs/
/.worktrees/
```

`factory/jobs/` is runtime state (job metadata, specs, reviews, check logs)
local to the machine running the factory. Each job's worktree is a full
checkout in `.worktrees/<job-id>`. Neither belongs in the repo. Everything you
copied in step 1 does: the skill in `.claude/skills/factory/`, the agents in
`.claude/agents/` and the rest of `factory/` are part of the project and get
committed, so ignore only these two paths, never `.claude/` or `factory/`.

**Labels.** On Jira, skip this: Jira creates a label the first time it's used.
On GitHub, create them:

```bash
gh label create factory --color 5319E7 --description "Build this with the software factory"
gh label create factory:needs-info --color FBCA04 --description "The factory asked the author a question"
gh label create factory:needs-human --color D93F0B --description "The factory needs a human decision on the PR"
gh label create factory:ready-for-pr --color 0E8A16 --description "The factory's branch is ready for a pull request"
```

The last one is only used in git mode.

## 3. Verify

1. `factory/checks.sh` passes the way the gate will run it: in a fresh,
   detached worktree of `origin/<base_branch>`, not in the user's checkout.
   Their checkout may be on another branch or hold uncommitted work; never
   switch, stash or reset it.

   ```bash
   root=$(pwd)
   git fetch origin <base_branch>
   git worktree add --detach .worktrees/_install-check origin/<base_branch>
   (cd .worktrees/_install-check && bash "$root/factory/checks.sh")   # must exit 0
   git worktree remove --force .worktrees/_install-check
   ```

   If it fails, the factory can never pass a job: fix the script (not the
   project) or tell the user what's broken. A fresh checkout has no installed
   packages or build output, so the script has to restore what it needs. When a
   restore needs credentials this machine lacks (a private package feed, say),
   or a test needs a database, secrets or a running service, stop and ask the
   user. Never skip or weaken a check to get it passing.
2. `factory/bin/job check-tracker` succeeds. It checks the tracker (on Jira,
   that the configured statuses exist) and the code host (on GitHub, the repo;
   in git mode, that `origin` has the base branch, and the `pr_url` link it
   would post), and prints which bash runs the checks (on Windows it must be
   Git Bash: `MINGW64...`, not `Linux`). Then `factory/bin/job queue` runs
   without an error. On Windows this also proves the scripts start without a
   `python3` on PATH.
3. Each file in `.claude/agents/` has only these frontmatter keys: `name`,
   `description`, `tools`, `model`, and `hooks` on the builder. Claude Code
   ignores unknown keys without a warning, so a typo silently changes behaviour.
4. `python3 -m json.tool factory/config.json` succeeds.
5. `git check-ignore factory/jobs/x .worktrees/x` prints both paths, and
   `git status --porcelain --untracked-files=all` lists every file from step 1
   (the skill, the agents and `factory/`) as committable.

## 4. Hand over

Stay on the branch the user is on: don't create or check out another branch.
Commit the install there with a one-line message and show the user the diff.
Ask before pushing: the factory branches every job from `origin/<base_branch>`,
so the install must be on that branch there before the first run.

Then tell the user, in a few lines:

- Label one or two small issues `factory`, then run `/factory run` in Claude
  Code from the project root.
- On Jira: label a ticket `factory` once it's in `ready_status`. The factory
  moves it to `start_status` when it starts and to `merged_status` when its
  work merges, with a comment saying how. If the workflow refuses a move, the
  factory warns and leaves the ticket where it is. Anyone who can edit an issue
  can add the label, so the label only gates as well as the project's Edit
  Issues permission does.
- On GitHub, every job ends as a PR. The approver merges what it approves;
  the rest wait labeled `factory:needs-human`. Approve one by merging it, or
  tell the factory what to change with `/factory <anything>`.
- In git mode, every approved job ends as a branch labeled
  `factory:ready-for-pr` on the issue. Open a PR from it and merge it as
  usual; the next `/factory run` sees the merge and finishes the job. PR review
  comments go back to the factory with `/factory <anything>`. For a squash
  merge, tell the factory it's merged.
- Intake runs on Fable, once per issue. Depending on their plan, Fable can
  bill to usage credits rather than the plan's included usage.
- To start carefully, set `concurrency` to 1 in `factory/config.json` and read
  every PR until they trust it.

## Workspace install

A workspace is a folder that holds several repo clones, such as `~/src/acme/`
with `orders/`, `billing/` and `web/` in it. The factory lives in that folder,
beside the repos and not inside any of them, so installing it changes no repo
and needs no commit or push. Each job builds in one repo: intake picks it, or a
`factory:<repo>` label on the issue fixes it. Its worktree goes in the
workspace's `.worktrees/<repo>/<job-id>`. Change the steps above like this:

**0. Check.** Ask which of the clones the factory should build in; suggest the
ones the issue tracker's work touches rather than all of them. For each one,
check that it's a git repo and that the user can push to it, and find the
branch jobs should start from. That's its `origin`'s default branch
(`git -C <repo> ls-remote --symref origin HEAD` names it) unless the team
branches from another one, often `dev`: ask, and check that the branch exists
on `origin`. The workspace folder itself must not be a git repo. Don't switch
branches or touch uncommitted work in any clone.

**1. Copy.** Copy the same files into the workspace folder, except
`factory/checks.sh`: make one checks script per repo instead, at
`factory/checks/<repo>.sh`, starting from `factory/checks.sh`.

**2. Adapt.**

- Each `factory/checks/<repo>.sh` gets that repo's checks, found the same way
  as for `factory/checks.sh`. The gate runs it with the job's worktree, a full
  checkout of that repo, as the working directory.
- In `factory/config.json`, list the repos. The top-level `base_branch`,
  `codehost` and `merge_method` are defaults; set per repo what differs:

  ```json
  "codehost": { "type": "git" },
  "repos": {
    "orders": {
      "description": "Order service: carts, checkout, order history",
      "base_branch": "main",
      "codehost": { "type": "git", "pr_url": "https://dev.azure.com/<org>/<project>/_git/orders/pullrequestcreate?sourceRef={branch}&targetRef={base}" }
    },
    "billing": { "description": "...", "base_branch": "develop", "codehost": { ... } }
  }
  ```

  The key is the repo's folder name. Add `"path"` only when the folder name
  isn't a usable key. Write each `description` in one line from the repo's
  README and code: intake reads them to decide where a change belongs. Build
  each `pr_url` from that repo's own remote, as above. `protected_paths`
  defaults to none in a workspace, since the factory's own files live outside
  every repo; set it per repo only for paths no job should ever change.
- With GitHub Issues as the tracker, set `"tracker": {"type": "github",
  "repo": "<owner>/<name>"}` to say whose issues to read. Jira needs nothing
  extra. Don't use per-repo queue labels: one factory takes every `factory`
  issue and places it.
- **Jira credentials** go in the workspace's `.claude/settings.local.json`.
- **`AGENTS.md`.** Don't edit the repos' own files. Create `AGENTS.md` in the
  workspace folder instead. List each configured repo with its
  description, then, for any repo whose own `AGENTS.md` or `CLAUDE.md` doesn't
  already say so, how to set up a fresh checkout, start its dev server on a
  given port, and run the commands in its checks script. Every agent reads this
  file as well as the repo's own. Then add the **Software factory** section,
  saying it builds the listed repos and that worktrees live in
  `.worktrees/<repo>/<job-id>`.
- **`.gitignore`**: skip it; the workspace isn't a repo.

**3. Verify.** Do step 3.1 for each repo, with its own base branch and checks
script, from the workspace folder:

```bash
ws=$(pwd)
git -C <repo> fetch origin <base>
git -C <repo> worktree add --detach "$ws/.worktrees/<repo>/_install-check" origin/<base>
(cd "$ws/.worktrees/<repo>/_install-check" && bash "$ws/factory/checks/<repo>.sh")   # must exit 0
git -C <repo> worktree remove --force "$ws/.worktrees/<repo>/_install-check"
```

The same rules apply: never touch the user's checkouts, and stop and ask
rather than weaken a check. `factory/bin/job check-tracker` checks every repo
(its checkout, its checks script, its `origin` and its PR link). Skip step
3.5.

**4. Hand over.** There's nothing to commit or push. Tell the user to start
Claude Code in the workspace folder and run `/factory` there; that the factory
picks the repo per issue, and a `factory:<repo>` label fixes it; and that a
job builds in one repo, so an issue that spans repos is cut down to its main
repo, with the rest named in intake's comment.

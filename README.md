# Software Factory for Claude Code

This factory implementation is based on zazencodes' V2 factory at https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2.

A software factory that works through your issues, in GitHub Issues or Jira
Cloud, on code hosted anywhere git runs. Label an issue `factory`, run
`/factory run`, and agents decide whether it's worth building, write a spec,
build it, test it with your own checks, and review it. On GitHub the approver
opens and merges the pull request when it's sure and hands you the rest. On
any other host (Azure DevOps, GitLab, Bitbucket) it hands your team a
finished, reviewed branch to open a PR from, and notices when you merge it.

## Install

Open Claude Code in your project and paste:

```text
Install this software factory in this project from the reference repository:

https://github.com/sgvirtzman-ux/cc-software-factory/tree/main
```

Your agent reads [`INSTALL.md`](INSTALL.md), asks where your issues and your
code live (one repo, or a folder of several clones), copies the skill, agents, scripts and dashboard, fills
`factory/checks.sh` with your project's real test, lint, typecheck and build
commands, tailors the reviewers to your stack, creates the labels, and checks
that `checks.sh` passes on `main`.

You need Claude Code 2.1.257 or later, `python3` 3.10 or later, and a git
remote you can push to. For GitHub PRs or GitHub Issues you also need an
authenticated [`gh`](https://cli.github.com/); for Jira, an Atlassian API
token. If your project has pages, the builder and the UX and UI reviewers also
need the `agent-browser` CLI to look at them.

## The loop

```
issues labeled `factory`
        │
        ▼
intake (fable) ── NEEDS-INFO → asks the author on the issue, waits for a reply
        │         REJECT     → says why on the issue, removes the label
        ▼ ACCEPT, maybe rescoped; rates risk + surfaces
route (script)    picks reviewers and models from config.json
        ▼
spec-writer (opus) → spec.md
        ▼
builder (opus) → commits, pushes (GitHub: the factory opens a draft PR)
        ▼            ▲
gate (script) ─FAIL──┤  factory/checks.sh; no reviewer runs on a failing build
        ▼ PASS       │
reviewers ─CHANGES───┘  code (haiku | sonnet), + ux & ui on UI changes, + security
        ▼ all PASS
approver (opus) ── APPROVE  → sync with main, gate again, then
                │              GitHub: gh pr merge
                │              git:    push, hand the branch to your team for a PR;
                │                      the factory sees the merge in git history
                │              (Jira: the issue moves to QA Ready, or your status)
                └─ ESCALATE → issue labeled factory:needs-human, with the question
```

One Claude Code session orchestrates every job, `concurrency` at a time
(default 3). Each job gets its own git worktree and branch. All state lives in
`factory/jobs/`, so if the session dies, `/factory run` picks every job back up.

## Commands

```
/factory run          drain the queue: every open `factory` issue, `concurrency` at a time; also resumes
/factory run <n>      one issue now
/factory <anything>   talk to the factory: ask about jobs, give feedback, file a feature
```

You approve by merging the PR on GitHub. To change an escalated PR, tell the
factory what you want with `/factory`. To drop a job that waits on you or on
its author, tell the factory to reject it.

## What's in here

```
.claude/
  skills/factory/SKILL.md     the orchestrator: queue, concurrency, the pipeline
  agents/
    intake.md                 fable    worth building? at what scope? risk + surfaces
    spec-writer.md            opus     spec.md
    builder.md                opus     code, commits, the PR, build.md
    reviewer-code.md          routed   correctness, simplicity, tests
    reviewer-security.md      opus     only when intake flags `security`
    reviewer-ux.md            sonnet   only when intake flags `ui`
    reviewer-ui.md            haiku    only when intake flags `ui`
    approver.md               opus     APPROVE or ESCALATE
factory/
  config.json                 concurrency, rounds, labels, routing, protected paths, prices
  checks.sh                   your repo's checks (the installer fills it in)
  bin/job                     the only writer of job.json; refuses illegal moves; merges
  bin/gate                    runs checks.sh in a job's worktree → round-N/checks.md
  bin/route                   risk + surfaces → the job's reviewers and models
  bin/dashboard               serves dashboard.html on 127.0.0.1; /factory run starts it
  lib/tracker.py              GitHub Issues or Jira Cloud: the queue, comments, labels
  lib/codehost.py             GitHub PRs or plain git: how a job's change reaches main
  lib/workspace.py            one repo, or a workspace of several: where each job builds
  lib/shell.py                which bash runs the checks (Git Bash on Windows, never WSL)
  dashboard.html              the board
examples/jobs/                finished jobs from a real run
tests/                        end-to-end suites against fake Jira, gh and git hosts (python3 tests/run_all.py)
INSTALL.md                    instructions for the installing agent
DESIGN.md                     how it works and why
```

[`DESIGN.md`](DESIGN.md) explains each decision.

## One repo or a workspace of many

Install the factory in a repo and it builds that repo. Install it in a folder
that holds several clones, such as the folder you clone all your company's
repos into, and it builds in all of them from one session:

- `repos` in `factory/config.json` lists them, each with a one-line
  description and its own base branch, code host and checks script
  (`factory/checks/<repo>.sh`).
- Intake picks the repo for each issue by reading the descriptions and the
  code; a `factory:<repo>` label on the issue overrides it. A job builds in one
  repo. When an issue spans repos, intake keeps the main repo's part, and the
  factory files a ticket for each other repo, labeled `factory:<repo>` and
  linked to the original. Each ticket becomes its own job. A part that depends
  on the first job's change gets the `factory` label only once that job
  merges.
- Worktrees live in the workspace's `.worktrees/<repo>/<job-number>`, so nothing is
  installed into, committed to or changed in any repo. Jobs in different repos
  run side by side up to `concurrency`.

## Where code lives: GitHub or plain git

`codehost` in `factory/config.json` picks how a job ends. Branches, worktrees,
pushes and syncing with `main` are plain git in both modes.

- **GitHub** (`"type": "github"`): the factory opens a draft PR for each job,
  marks it ready, and merges it with `gh pr merge` when the approver approves.
- **Plain git** (`"type": "git"`): no hosting API at all, so it works on Azure
  DevOps, GitLab, Bitbucket or anything else. When the approver approves, the
  factory syncs the branch with `main`, runs the checks again, pushes it, and
  tells the issue it's ready for a pull request, with a one-click "create PR"
  link if you set `pr_url`. Your team opens the PR and merges it with your
  normal reviewers and branch policies. The factory checks git history for the
  merge: merge commits and rebase merges are detected automatically; for a
  squash merge, tell the factory it's merged (`job merged`). Review comments
  from the PR can go back to the factory as feedback.

## Where issues live: GitHub Issues or Jira

`tracker` in `factory/config.json` picks where issues live.

- **GitHub Issues**: a PR says `Closes #12`, so merging it closes the issue. In
  git mode the factory closes the issue itself when it sees the merge.
- **Jira Cloud**: the factory follows your workflow the way your team would.
  It picks up issues labeled `factory` in your ready status (for example
  `Sprint Ready`), moves each one to `In Progress` when intake accepts it, and
  to `QA Ready` when its work merges, with a comment saying how. If you
  reject a job after it started, the issue goes back to `Sprint Ready`. It
  reads and writes through the REST API with an API token, posts its decisions
  as comments, and puts the issue key in each PR's title (`PROJ-12: ...`) so a
  Jira integration for your code host can link them. Issue ids look like `PROJ-12` everywhere:
  `/factory run PROJ-12`.

## Dashboard

`/factory run` starts it for you and prints the address, normally
<http://127.0.0.1:8000/dashboard.html>. To start it yourself:

```bash
factory/bin/dashboard
```

It serves the `factory/` folder on 127.0.0.1 only, so job files never reach
your network; `dashboard_port` in `config.json` changes the port. Opened
straight from disk, the page can't read the jobs and says so. Each job is a card with its issue
and PR, a row of gate and review chips per round, its risk, cost and
time. Click a card to read every file the job wrote; each one opens with a
short summary of what happened in that step. To see the example
jobs, copy `examples/jobs/` to `factory/jobs/` first.

Cost is the API-equivalent price of each job's agents, read from Claude Code's
transcripts. On a subscription plan it shows where the spend goes, not what
you're billed.

## The example jobs

They come from a test run on a small private calculator repo, with
`concurrency: 2` and every agent on Haiku to keep it cheap.

- **001-add-a-subtract-function**: low risk, no UI, so only the code reviewer
  ran. Approved and merged through its PR.
- **002-show-a-history-of**: a UI change, so the UX and UI reviewers ran too.
  Merged.
- **003-add-multiply-divide-power**: intake cut an oversized "scientific
  calculator" issue down to three functions. The gate failed round 1 because
  the builder edited a protected test file, and the job went to a human after
  three rounds. That run listed `tests/test_calc.py` in `protected_paths`;
  this job is why `INSTALL.md` now says not to protect tests.
- **005-add-a-crypto-miner**: rejected at intake.

## Cost and safety

- It opens and merges real PRs and runs on your Claude plan. Start with
  `concurrency: 1` and read every PR until you trust it.
- Intake runs on Fable once per issue. Depending on your plan, Fable can bill
  to usage credits rather than your plan's included usage.
- Only issues labeled `factory` run. On GitHub, only people with triage or
  write access can add labels. On Jira, anyone who can edit an issue can, so
  check who has the project's Edit Issues permission before you turn it on.
  Issue text reaches the agents as quoted data, never as their instructions.
- The factory pushes only its jobs' own branches (`factory/<job-id>` unless
  you set `branch_format`) and never deploys. On GitHub it
  merges only through `gh pr merge`; in git mode it never merges at all.
- Agents never write to a live database (one with real or shared data), and
  never read one until you approve the exact queries, except read-only queries in environments your `AGENTS.md` lists
  as open, such as dev and test. Production and staging always need your
  approval. A job that needs that data stops and asks.

## License

[GPLv3](LICENSE). You can use, modify and share this code, and any version you
distribute must also be released under GPLv3.

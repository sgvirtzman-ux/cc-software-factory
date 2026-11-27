# Software Factory for Claude Code

A software factory that works through your issues, in GitHub Issues or Jira
Cloud. Label an issue `factory`, run `/factory run`, and agents decide whether it's worth building,
write a spec, build it, test it with your own checks, review it, and open a
pull request that closes the issue. The approver merges what it's sure of and
hands you the rest.

## Install

Open Claude Code in your project and paste:

```text
Install this software factory in this project from the reference repository:

https://github.com/sgvirtzman-ux/cc-software-factory/tree/main
```

Your agent reads [`INSTALL.md`](INSTALL.md), asks whether your issues live in
GitHub Issues or Jira Cloud, copies the skill, agents, scripts and dashboard, fills `factory/checks.sh` with your project's real test, lint,
typecheck and build commands, tailors the reviewers to your stack, creates the
labels, and checks that `checks.sh` passes on `main`.

You need Claude Code 2.1.257 or later, `python3` 3.10 or later, and an
authenticated [`gh`](https://cli.github.com/) that can push to the repo. If
your project has pages, the builder and the UX and UI reviewers also need the
`agent-browser` CLI to look at them. For Jira you also need an Atlassian API
token; the code and PRs stay on GitHub.

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
builder (opus) → commits, pushes, opens a draft PR linked to the issue
        ▼            ▲
gate (script) ─FAIL──┤  factory/checks.sh; no reviewer runs on a failing build
        ▼ PASS       │
reviewers ─CHANGES───┘  code (haiku | sonnet), + ux & ui on UI changes, + security
        ▼ all PASS
approver (opus) ── APPROVE  → sync with main, gate again, gh pr merge
                │              (Jira: the issue moves to QA Ready, or your status)
                └─ ESCALATE → PR ready for you, issue labeled factory:needs-human
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
  lib/tracker.py              GitHub Issues or Jira Cloud: the queue, comments, labels
  dashboard.html              the board
examples/jobs/                finished jobs from a real run
INSTALL.md                    instructions for the installing agent
DESIGN.md                     how it works and why
```

[`DESIGN.md`](DESIGN.md) explains each decision.

## GitHub Issues or Jira

`tracker` in `factory/config.json` picks where issues live. Either way the code
lives on GitHub: branches, PRs and merges go through `gh`.

- **GitHub Issues**: a PR says `Closes #12`, so merging it closes the issue.
- **Jira Cloud**: the factory follows your workflow the way your team would.
  It picks up issues labeled `factory` in your ready status (for example
  `Sprint Ready`), moves each one to `In Progress` when intake accepts it, and
  to `QA Ready` when its PR merges, with a comment linking the PR. If you
  reject a job after it started, the issue goes back to `Sprint Ready`. It reads
  and writes through the REST API with an API token, posts its decisions as
  comments, and titles each PR with the issue key (`PROJ-12: ...`) so Jira's
  GitHub integration can link them. Issue ids look like `PROJ-12` everywhere:
  `/factory run PROJ-12`.

## Dashboard

```bash
cd factory && python3 -m http.server
```

Open <http://localhost:8000/dashboard.html>. Each job is a card with its issue
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
- The factory pushes only `factory/*` branches, merges only through
  `gh pr merge`, and never deploys.

## License

[GPLv3](LICENSE). You can use, modify and share this code, and any version you
distribute must also be released under GPLv3.

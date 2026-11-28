# Design

How the factory works, and why it's built this way. The source is small
enough to read in one sitting: one skill, eight agents, three scripts, three
small modules (tracker, code host, workspace) and a config file.

## The idea

A software factory turns a queue of requests into merged code with a fixed
pipeline. Scripts handle the deterministic stages, such as running the tests,
moving state and merging. Agents do the judgment in between: whether a request
is worth building, what exactly to build, the code itself, and whether it's
good. Each stage leaves a file behind, so you can see what happened and why.
Every step's file opens with a `## Summary` of three to five bullets, so a human can
skim a job on the dashboard and read the detail only where it matters.

## Pieces

| Piece | File | Job |
|---|---|---|
| Queue | Issues labeled `factory`, in GitHub Issues or Jira Cloud | What to build. Anyone can file an issue; the label decides what runs. |
| Tracker | `factory/lib/tracker.py` | The only code that talks to the issue tracker: the queue, issue text, comments, labels, and the hand-off after a merge. |
| Code host | `factory/lib/codehost.py` | The only code that knows where the repo is hosted: GitHub PRs, or plain git with a hand-off to the team. |
| Workspace | `factory/lib/workspace.py` | Which repos the factory builds: the one it lives in, or several clones in the folder it lives in. |
| Orchestrator | `.claude/skills/factory/SKILL.md` | The session that runs `/factory`. Starts jobs, spawns agents, calls the scripts. Writes nothing itself. |
| Agents | `.claude/agents/*.md` | One file per role. Each reads files, writes exactly one file, and replies with one line. |
| State | `factory/jobs/<job-id>/` | `job.json` plus every agent's file. Gitignored, local to the machine running the factory. |
| Spine | `factory/bin/job`, `gate`, `route` | Deterministic. `job` is the only writer of `job.json` and refuses illegal moves. |
| Checks | `factory/checks.sh` | The repo's test, lint, typecheck and build commands. The only repo-specific script. |
| Exit | the PR | Every job ends as a PR that closes its issue. Merging it is the approval. |

## Pipeline

```
intake (fable)     → ACCEPT (scope possibly changed) | NEEDS-INFO | REJECT, posted as an issue comment
route (script)     → reviewers + models from risk/surfaces via config.json
worktree           → the job's branch (factory/<job-id> by default) from origin/main
spec-writer (opus) → spec.md
builder            → commits and pushes; on GitHub the factory opens the draft PR
gate (script)      → checks.sh; FAIL → builder gets the log, no reviewers spawned
reviewers (routed) → parallel, WAIT FOR ALL; CHANGES → builder → gate → reviewers (max rounds)
approver (opus)    → APPROVE: sync origin/main, gate, then gh pr merge (github)
                              or push and hand the branch to the team (git)
                     ESCALATE: PR ready, decision as PR comment, label factory:needs-human
```

`stage` in `job.json` is one of `intake`, `needs-info`, `spec`, `build`,
`gate`, `review`, `approve`, `merging`, `needs-human`, `merged`, `rejected`.
The legal moves are in `TRANSITIONS` at the top of `factory/bin/job`.

## Decisions

**Issues are the queue; you trigger the run.** `/factory run` drains every open
`factory` issue. Nothing runs in GitHub Actions or on a schedule: the factory
runs on your machine, on your Claude plan, when you start it. An issue can't
start a Claude Code session by itself, and keeping it local means your
worktrees, dev servers and dashboard are right there.

**The tracker is pluggable; the code host isn't.** Issues can live in GitHub
Issues or Jira Cloud, and `tracker` in `config.json` picks one. Everything the
factory needs from a tracker is a handful of calls in `factory/lib/tracker.py`:
list the queue, read an issue and its comments, comment, add and remove a
label, file an issue, and hand an issue off after its PR merges. Both adapters
return issues in one shape, so `factory/bin/job` doesn't know which it's
using. GitHub Issues closes an issue through the PR's `Closes #n`. On Jira
the factory walks the team's own workflow: it takes issues from
`ready_status`, moves them to `start_status` when intake accepts, and to
`merged_status` with a comment linking the PR when the PR merges, since a
merged change usually goes to QA rather than straight to Done. Rejecting a
started job puts the issue back in `ready_status`, unless a person has moved
it since. Each hand-off
is one direct move, and a move the workflow refuses is a warning, not a
failure: the job's state lives in `factory/jobs/`, not in Jira's status. Branches, PRs and merges stay
on GitHub with `gh` either way. The Jira adapter uses the REST API v3 with
Python's standard library, so there's still nothing to install. Jira's rich
text is Atlassian Document Format (ADF), so the adapter converts the Markdown
subset the agents write (headings, lists, quotes, code, bold, links) to ADF and
flattens ADF back to text for `issue.md`. Factory comments carry a comment
property, Jira's version of GitHub's hidden `<!-- factory -->` marker, so a
reply from the author can be told apart from the factory's own question.

**Git is the interface; a PR API is optional.** A pull request isn't a git
feature: every host has its own PR API. The factory needs one only to open and
merge PRs itself, so `codehost` makes it optional. In `github` mode it does,
through `gh`. In `git` mode it uses nothing but git, so it works on Azure
DevOps, GitLab, Bitbucket or a bare server. It needs no host token, and the
team's branch policies keep working because people open and complete the PR
exactly as they always have. The job ends in `handoff`: the branch is synced
with the base, gated again and pushed, the issue says it's ready, and the job
waits without holding a slot. The factory then watches git, not the host: a
merge commit or fast-forward contains the handed-off commit, which
`git merge-base --is-ancestor` settles exactly, and a rebase merge keeps each
commit's changes, which `git log --cherry-mark` matches. A squash merge
rewrites the change into one new commit that git can't trace back reliably,
so `job merged` marks it by hand. PR creation moved from the builder into
`factory/bin/job` for the same reason: no agent needs to know which host
you use.

**One factory can serve many repos.** Work tracked in one place (a Jira
project, say) often lands in several repos. Installing a factory per repo
would mean several queues fighting over one label, and an install in every
repo. Instead the factory can live in the folder that holds the clones, with
the repos listed in `config.json`. Each job still builds in exactly one repo:
intake chooses it, because it already reads the code to judge scope, and a
`factory:<repo>` label overrides it when a person knows better. The choice is
a `REPO:` line in `intake.md`, checked against the list before anything is
created. Worktrees go in the workspace's `.worktrees/<repo>/<job-number>`,
outside the repos, so the factory never writes to a repo except through its
own job branches. They're named after the job's number (`003`) rather than its
full id, because a workspace path is already deep and a long title's slug can
push a repo's build output past Windows' 260-character path limit. Each repo's checks script lives in the workspace too, so
no branch can edit the checks it's judged by. The agents are told their
worktree, repo and base in every prompt's header, which `job header` prints,
so they work the same in either layout. One repo per job keeps state, the gate
and merge detection simple. An issue that spans repos is cut to its main repo
and the rest is named, which is what intake already does with any issue too
big for one PR.

**One orchestrator for every job.** The session running `/factory run` starts up
to `concurrency` jobs and spawns every agent as a background subagent. There
are no per-job orchestrators to coordinate. Its context stays small because
agents reply with one line and the scripts print one line; the detail lives in
files the next agent reads. Claude Code allows 20 concurrent subagents per
session by default, which covers three jobs with four reviewers each.

**Fable at the door, once.** Intake is the only Fable call per job. It decides
whether the issue is worth building, rewrites the scope when the issue is too
big or vague, and asks the author when only they can answer. Its decision goes
on the issue as a comment; the issue body stays as written. Questions go to
the author before a spec exists, so the spec writer never stalls on one.

**Risk routes the work, a script does the routing.** Intake rates risk (`low`,
`medium`, `high`) and surfaces (`ui`, `security`). `factory/bin/route` looks
them up in `config.json`:

| | code review | ux | ui | security |
|---|---|---|---|---|
| low | Haiku | | | |
| medium | Sonnet | | | |
| high | Sonnet | | | |
| + `ui` surface | | Sonnet | Haiku | |
| + `security` surface | | | | Opus |

The builder always runs on Opus.

A low-risk change with no UI runs one Haiku reviewer. Routing is visible in
one file and changes without touching a prompt.

**Scripts decide whether it works.** The gate runs `checks.sh` before any
reviewer is spawned, and again after syncing with `main` before the merge.
A failing build goes straight back to the builder with the log. The gate runs
the main checkout's copy of `checks.sh`, so a branch can't edit the checks it
is judged by. It fails any branch that modifies or deletes a file under
`protected_paths`: the factory's own checks and config. Tests aren't
protected, since features have to update them; the code reviewer fails any
test that was weakened, skipped or special-cased to pass. The
builder also has a `Stop` hook in its frontmatter (Claude Code runs it as
`SubagentStop`) that runs the checks before it finishes,
as a nudge; the gate is still what decides.

**Agents judge what scripts can't.** Reviewers don't re-run the test suite;
they read the diff for correctness, scope, security, UX and visual fit.

**A PR per issue, merged through the host.** Merging locally breaks when the
main checkout is dirty or busy, and it would bypass branch policies. On GitHub
the factory merges with `gh pr merge`; in git mode your team merges through
its own PR. Either way branch protection, required checks and the PR history
all apply, and your main checkout is never touched. Before merging, the branch is synced with `main` and
gated again, because another job may have merged since it was tested.

**You approve by merging.** The approver approves what it would merge itself
and escalates the rest with a specific question; the issue is labeled
`factory:needs-human`. On GitHub an approved PR merges, and an escalated one is
ready for you: merge it to approve. In git mode every finished job is a branch
for your team's PR. Either way, tell the factory what to change with
`/factory <anything>`: your words go to the builder as `feedback-<k>.md` and
the job runs another round. To drop a job that waits on you or on its author,
tell the factory to reject it: the reason goes on the issue, its PR closes or
its branch is deleted, and the job moves to Done.

**State is files, so runs resume.** Every fact the orchestrator needs is in
`factory/jobs/`. If the session dies or compacts, a fresh `/factory run` reads
the queue and resumes every job from its stage.

**Cost is measured per job.** Claude Code writes each subagent's transcript,
with model and token counts per message, to
`~/.claude/projects/<project>/<session>/subagents/`. Every factory prompt starts
with the job's folder, so `factory/bin/job` sums the transcripts that name it
and prices them with `prices_usd_per_mtok`. The figure is the API-equivalent
cost of the job's agents; on a subscription it shows relative spend, not your
bill. The orchestrator's own tokens aren't attributed to a job. The
transcript format isn't a documented interface, so a Claude Code update could
break this.

## Guardrails

- Only issues labeled `factory` run, so the label is the trust boundary. On
  GitHub, labels need triage or write access. On Jira, the Edit Issues
  permission is enough to add one, so the boundary is only as tight as that
  permission. A Jira workflow status that only a restricted group can move
  issues into would be a tighter gate; it isn't built yet.
- Issue text reaches agents as quoted data in `issue.md`, with comments only
  from the issue's author (the reporter, on Jira) and the factory's operator.
  It is never part of a prompt. Intake, which reads it first, has no shell.
- Agents push only their job's own branch. On GitHub only `factory/bin/job
  merge` merges, through `gh pr merge`; in git mode the factory never merges.
  The factory never deploys.
- No agent writes to a live system (a database, API, queue or storage that
  real or shared data lives in), and none reads one without a human's approval
  of the exact queries, except read-only queries in the environments the
  install's `AGENTS.md` lists as open (often dev and test). Production,
  staging and unlisted environments always need approval. An agent that needs live data escalates with a live data request;
  the approval comes back as `/factory` feedback, saved in the job folder.
  The prompts say so, and INSTALL adds a Claude Code `ask` permission rule
  for each tool that reaches a live system, so a slip still stops at a prompt.
- One PR per issue; at most `max_rounds` build rounds per job before a human
  is asked.

## Origins

This is a modified version of ZazenCodes' "Software Factory for Claude Code
(v2)", from
<https://github.com/zazencodes/zazencodes-season-3/tree/main/src/software-factory-claude-code-v2>,
released under GPLv3. Modified in November 2026.

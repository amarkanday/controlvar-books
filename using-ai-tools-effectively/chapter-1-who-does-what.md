# Chapter 1 — What the agents do, and what people keep

At 01:25 UTC on 24 September 2026, the Release Please workflow ran for the first time in Controlvar's
public repository, and failed. Release Please is the bot that reads the commits on `main`, opens a pull
request that bumps the version and writes the changelog, and tags the release once that pull request
merges. The run's log, which GitHub still serves, carries this error:

```text
##[error]release-please failed: GitHub Actions is not permitted to create or approve pull requests. [...]
```

It ran again at 04:28, at 04:52 and at 05:29, and twice more that evening, and failed each time. Over a
little more than a day it ran nine times, once for each push to `main`, the merge of the new leak guard at
01:08 on 25 September among them. Six of those logs are still kept, and all six report the same error;
the other three have expired. For that whole day no release pull request could be opened, so nothing that
merged could be released.

Every file an agent could have edited was already right. Between the last failing run and the first
passing one, the only change under `.github/` was a one-line bump in `pr-qa-gate.yml`. The refusal came
from a setting of the GitHub organisation that owns the repository, and only an owner of that
organisation can change it. At 01:31 on 25 September, pull request
[#56](https://github.com/Controlvar/controlvar/pull/56) merged and wrote the finding into `CLAUDE.md`,
the instruction file the agents work from. At `09d815ea` it reads:

```text
# 📁 CLAUDE.md
Release-please also needs the ORGANISATION to allow it. If it fails with

    release-please failed: GitHub Actions is not permitted to create or
    approve pull requests

that is not a repository setting you can fix from here -- the repository API
answers `409 The organization does not allow GitHub Actions to create or
approve pull requests`. It is
`github.com/organizations/Controlvar/settings/actions` -> Workflow
permissions, and it needs an org owner.
```

At 03:22 the next run succeeded, and nineteen seconds after it started the bot opened its first release
pull request in the public repository, [#60](https://github.com/Controlvar/controlvar/pull/60), for
release 0.2.3. It merged at 04:21.

Nothing was lost that day except time, and the lesson is about where the problem sat. A change that
depends on a setting nobody in the session can reach looks, from inside the session, exactly like a
change with a bug in it. An agent asked to "make the release work" has a workflow file, a permissions
block and a token in reach, and none of them was the problem. This chapter is about drawing that line
before the work starts. It shows what AI agents do in Controlvar, measured from the history; where each
agent's job is written down; and which actions the repository reserves for a person, along with the two
mechanisms that back those rules with something stronger than prose.

## The work is in the history

Start with what you can count. Most commits in Controlvar's history end with trailers, the `Key: value`
lines after the message body. One of them, `Co-Authored-By`, names whoever wrote the change together with
its author. At `09d815ea`, 982 of the 1,310 commits carry a `Co-Authored-By` trailer that names
Claude. The spelling of the key varies from commit to commit, and the lower-case `Co-authored-by` is in
fact the commoner one. That makes no difference to the count, because git matches trailer keys without
regard to case; the first exercise shows both spellings giving 982.

`CLAUDE.md` asks for exactly that trailer. Its section on commits gives the shape an agent is to follow:

````text
# 📁 CLAUDE.md
4. Create detailed commit messages following the format:
   ```
   Brief summary (50 chars or less)

   Detailed explanation of changes:
   - What was changed
   - Why it was changed
   - Any technical details

   🤖 Generated with [Claude Code](https://claude.com/claude-code)

   Co-Authored-By: Claude <noreply@anthropic.com>
   ```
````

Two things in the template matter more than its layout. The footer line, "Generated with Claude Code",
also appears at the end of pull request descriptions, so a reader of the public tracker can see which
changes an agent wrote. And the template stops at the co-author line: it has no `Signed-off-by`, which
the repository's required `DCO` check refuses to do without. Chapter 2 returns to that gap. At
`09d815ea`, 945 commits carry a `Signed-off-by` line all the same.

So the record exists, and anyone can count it. What a trailer cannot tell you is what anyone checked
before the commit was made, or who decided that the change should exist at all. The rest of this book is
about those two things.

> **Note**
>
> **The instruction file is written for one tool and readable by any.** `CLAUDE.md` opens with "This file
> provides guidance to Claude Code (claude.ai/code) when working with code in this repository." The rest
> is plain prose and plain commands. Any tool that reads files can be pointed at it, and so can a new
> person on the project, who will find it the most current description of how the work is done.

## The roles are files

The agents are not described in a wiki or a chat history. Each role is a Markdown file in
`.claude/agents/`, checked into the repository and reviewed like code. There are thirteen. Each starts
with a short header between `---` lines and continues with a brief in prose. Here is the start of the
principal engineer's:

```text
# 📁 .claude/agents/principal-engineer.md
---
name: principal-engineer
description: Pressure-tests a PLAN's technical assumptions before any code is written — verifies every factual premise against the repository, finds the failure modes, and judges whether the verification strategy could actually fail. Runs after the drafting roles (software-architect, qa-engineer, ux-designer) and before engineering-manager's final verdict.
tools: Read, Glob, Grep, Bash
model: opus
---

You are the principal engineer for Controlvar. You review **plans, not code**,
and your single job is the one this codebase keeps getting wrong:

> **Is every factual claim in this plan actually true, and how do we know?**
```

`name` is how the role is called. `description` tells the calling session what the role is for and when
to use it, so it is written as an instruction to another model. `tools` limits what the agent may use,
and `model` chooses which model runs it. The five roles that work on plans run on `opus`; the other eight
run on `sonnet`. The pull request that added the first two reviewers, #89 (Chapter 3), gave the reason
for keeping them in the repository:

```text
They are checked in, so the process travels with the codebase rather than
living in one machine's memory.
```

That sentence is from the pull request's description on GitHub. It describes a property you can test: a
fresh clone carries the whole process with it, and a change to a role is a reviewed diff like any other.

The thirteen fall into three groups:

| Group | Roles | May use Write and Edit |
|---|---|---|
| Plan, before any code | `software-architect`, `qa-engineer`, `ux-designer`, `principal-engineer`, `engineering-manager` | No: Read, Glob, Grep, Bash |
| Build and review | `tester` and `auto-fixer`; `reviewer` | `tester` and `auto-fixer` yes; `reviewer` no |
| Specialists | `a11y-auditor`, `contract-tester`, `data-generator`, `scenario-runner`, `validator` | No (the scenario runner adds browser tools) |

Only two roles may edit files. The five planning roles work on plans, which exist before any code does,
and the three drafting roles among them end their briefs with the same sentence. The architect's adds a
second:

```text
# 📁 .claude/agents/software-architect.md
You are read-only: no file writes, no commits, no GitHub or AWS calls that
create anything. The author writes your draft into the plan.
```

The second sentence names who does the writing. A drafting agent hands its text back; the session that
called it, the author, merges the drafts into one plan and owns the result. Responsibility for the file on
disk stays in one place.

> **Warning**
>
> **A tool list is a guard rail, and a shell is a way around it.** Every role here keeps `Bash`, and a
> shell can write a file, make a commit or call an API. "You are read-only" is the rest of the rule, and
> it is prose. Treat the tool list as narrowing what an agent reaches for first, and still say in words
> what it must not do. Chapter 4 makes that sentence part of every brief.

Beside the roles, `.claude/commands/` holds slash commands: short instruction files for tasks that
recur, such as `/commit` and `/test-backend`. They are briefs too, for a task where a role would be too
much. Chapter 2 looks at one whose index has drifted from what it indexes.

## What people keep

The agents draft, check, build and review. Some things they never do on their own, and the repository
says which in three places, each closer to the moment of action than the one before.

The first is the plan. Every plan for work beyond a single-file change has nine headings (Chapter 3), and
one of them exists for this:

```text
# 📁 CLAUDE.md
    IRREVERSIBLE  what needs a human at the moment it happens
```

The second is the engineering manager, the reviewer who gives a plan its final verdict. Its brief asks
three questions about what a plan may not decide for itself:

```text
# 📁 .claude/agents/engineering-manager.md
**What is hard to reverse?** Force pushes, history rewrites, archiving,
publishing, deleting, anything outward-facing. These need to be named in the
plan with their undo path, and they need explicit human approval at the moment
they happen — not approval inherited from "the plan was approved".

**What is blocked on a human?** Org settings, credentials, disclosure
decisions, anything needing an account the author does not control. These must
be identified in the plan, not discovered mid-execution.

**Is anything being decided that is not the author's to decide?** Publishing
unfixed security issues, changing a public contract, spending money, deleting
someone's data. Escalate rather than approve.
```

Read the second question against the opening of this chapter. "Org settings" is its first example, and
"discovered mid-execution" is what happened to Release Please: nine runs found out, one after another,
what one line in a plan could have said in advance. The three questions sort a plan's steps into three
kinds. A step that cannot be undone needs a person to say yes when it is reached. A step that needs
someone else's account needs that person named, and the work arranged around waiting for them. A step
that settles something outside the change, such as a public contract or money, goes up to whoever owns
it.

Note the words "at the moment they happen". An approved plan is not permission to publish. The brief
gives no reason; ours is that a plan is approved days or hours before its last step, by a reviewer reading a description of that step, and the
world can change in between: a tag can already exist, or another change can have merged first. So the
approval is asked for again when the step is reached, by whoever is about to take it.

The third place is a rule for questions nobody is around to answer. When work stops on a question the
maintainer has not answered, `CLAUDE.md` sends it to two of the reviewing roles and goes with their
recommendation, and then limits what such an answer can do:

```text
# 📁 CLAUDE.md
Three rules keep this honest:

- **Record it.** The question, the recommendation and the reasoning go in
  `DECISIONS.md` alongside the founder's own decisions, marked as team-decided
  rather than founder-decided, so the distinction survives.
- **It does not extend to the irreversible.** A team recommendation cannot
  authorise publishing a package, creating infrastructure that bills, a force
  push, an announcement, or anything else on the IRREVERSIBLE list. Those wait
  for a human however long that takes.
- **Cap the rounds.** If reviewers reject a revision twice, stop revising.
  Work the uncontested parts and leave a written note. Iterating against
  reviewers with nobody to break a tie is how the fix-round spiral starts, and
  that is the failure this whole process exists to prevent.
```

The line these rules draw follows reversibility. Difficulty has nothing to do with it. An agent may
settle a hard question that can be undone, provided it writes down that it did and marks the decision as
the team's. An easy step that cannot be undone waits for a person "however long that takes". Publishing a
package takes one command, and the repository still holds it for a person.

> **Note**
>
> **Some of the record is not public.** `DECISIONS.md`, and the `drafts/` and `reviews/` folders that
> Chapter 4 describes, are kept beside the plans and are not in the public repository. The rules that
> refer to them are public, which is enough to adopt the process. If you do, keep your own decision log
> from the first day, with each entry marked by who decided it.

## Two mechanisms behind the rules

Prose can be ignored. Two mechanisms in the repository back the rules without depending on anyone reading
them.

The first is the branch protection for `main`, which `scripts/configure-repo.sh` writes. The script
builds the body it sends to GitHub from a list of twenty required checks:

```bash
# 📁 scripts/configure-repo.sh
print(json.dumps({
    "required_status_checks": {"strict": False, "contexts": checks},
    "enforce_admins": False,
    "required_pull_request_reviews": None,
    "restrictions": None,
    "required_linear_history": True,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "required_conversation_resolution": True,
}))
```

Read it field by field. `allow_force_pushes` and `allow_deletions` are off, so the history of `main`
cannot be rewritten or the branch removed through an ordinary push, by an agent or anyone else.
`required_pull_request_reviews` is `None`: the twenty checks gate the code, and no reviewer's approval is
required by GitHub, so who merges and when is a matter of the process in this book. And `enforce_admins`
is `False`, which means an administrator can merge past the checks. The script's own comment adds that
live protection may differ from this body and that `scripts/check_required_checks.sh` reports any
difference.

`CLAUDE.md` names the case where that last setting matters. The release pull request is opened by a bot,
and GitHub does not start workflows on its own token's events without approval:

```text
# 📁 CLAUDE.md
- **The release-please pull request's checks never run on their own.** Its
  `pull_request` runs exist but sit at `action_required`, waiting for a
  maintainer's approval like a fork's (measured on #1483: 15 runs, actor
  `github-actions[bot]`, all `action_required`), so it shows no check results
  and does not satisfy branch protection as it stands. It is not blocked on
  anything you can fix in the pull request; merge it with `--admin` after
  confirming the diff is only the eight files it owns: CHANGELOG.md, VERSION,
  `.release-please-manifest.json` and the five `extra-files` (see the next
  section).
```

So this merge, which skips the gate, comes with its own check, a diff of eight named files, and the
merge publishes a release. Both halves make it a step for a person at the moment it happens.

The second mechanism is the contributor guard, which Chapter 8 covers in full. A pull request from a fork
may change only files in the areas open to outside contributors, and `scripts/contributor_guard.py`
classifies every path. `CLAUDE.md` and everything under `.claude/` fall outside those areas, so the
instructions that steer the agents can be changed only by maintainers. The third exercise asks the guard
directly.

## Sorting a plan's steps

Here is a list of steps of the kind an agent might put in a plan for "cut the next release". It is
illustrative: no agent wrote it. Every command and file in it exists at `09d815ea`, and every step is one
that `CLAUDE.md` discusses.

```text
1. Wait for the release pull request; diff it against main.
2. Merge it: gh pr merge <number> --squash --admin
3. Build the images: gh workflow run release.yml -f tag=vX.Y.Z
4. Deploy the docs site: gh workflow run docs.yml --ref vX.Y.Z
5. If Release Please fails with "not permitted", turn on the organisation's
   Workflow permissions setting.
```

Take the steps in order and ask the engineering manager's questions of each. The first step reads and
changes nothing, so an agent may do it and report the diff. The second publishes a release and skips the
required checks; it belongs under IRREVERSIBLE, and a person takes it once the diff in step 1 shows only
the eight files.

The third step should not be in the plan at all, and you find that out by reading the workflows:

```bash
grep -n 'uses: ./.github/workflows/release.yml' .github/workflows/release-please.yml
grep -n 'Do not dispatch' CLAUDE.md
```

```text
57:    uses: ./.github/workflows/release.yml
1134:  release. **Do not dispatch `release.yml` after a release.** A second run
```

Release Please calls the release workflow itself when it creates a release, so a dispatch builds the
release a second time. Chapter 2 opens on the day that happened.

The fourth step is outward-facing, since it changes a public website. It looks easy to undo by deploying
the previous tag the same way (our inference from `.github/workflows/docs.yml`, whose deploy job runs on
any `v` tag without a `-` in it; the repository does not say so), and a plan should say so beside it; the undo path is what the engineering
manager's brief asks for with every irreversible step. The fifth step is the opening of this
chapter. It is no agent's to take, because it needs an account the author does not control, and it goes
under BLOCKED in the manager's verdict (Chapter 3) with the role that can do it: an owner of the
organisation. A plan that lists it there has turned a day of failed runs into one message to that
person.

## Try it

Each step needs a clone of Controlvar, git and Python 3; the fourth also needs the GitHub CLI, signed in.
The output below was taken at `09d815ea` on macOS.

1. Count the commits with an AI co-author, with both spellings of the trailer key, and all the commits:

   ```bash
   git log 09d815ea --format='%(trailers:key=Co-Authored-By,valueonly,separator=%x2C)' | grep -c 'noreply@anthropic.com'
   git log 09d815ea --format='%(trailers:key=co-authored-by,valueonly,separator=%x2C)' | grep -c 'noreply@anthropic.com'
   git rev-list --count 09d815ea
   ```

   ```text
   982
   982
   1310
   ```

   The two counts agree because git ignores case in trailer keys. On a newer `main` all three numbers are
   larger.

2. Ask your AI tool: "List the roles in `.claude/agents/` that may edit files, and the ones whose brief
   tells them they are read-only, with their file names." Then check its answer:

   ```bash
   grep -H -E '^tools:' .claude/agents/*.md
   grep -l "You are read-only" .claude/agents/*.md
   ```

   ```text
   .claude/agents/a11y-auditor.md:tools: Read, Bash, Glob, Grep
   ...
   .claude/agents/auto-fixer.md:tools: Read, Write, Edit, Bash, Glob, Grep
   ...
   .claude/agents/tester.md:tools: Read, Write, Edit, Bash, Glob, Grep
   ...
   .claude/agents/qa-engineer.md
   .claude/agents/software-architect.md
   .claude/agents/ux-designer.md
   ```

   Two roles list `Write`: `auto-fixer` and `tester`. Three briefs say "You are read-only". If your tool
   said the principal engineer's brief says it too, it guessed: that brief confines itself to reviewing
   plans and never uses those words.

3. Ask the contributor guard about the instruction files. It is plain Python and reads only the names:

   ```bash
   python3 -B -c 'import sys; sys.path.insert(0, "scripts"); import contributor_guard as g; [print(p, g.path_rule(p)) for p in ["CLAUDE.md", ".claude/agents/reviewer.md", "backend/app/core/scheduler.py"]]'
   ```

   ```text
   CLAUDE.md location
   .claude/agents/reviewer.md location
   backend/app/core/scheduler.py None
   ```

   `location` means a fork may not change the file; `None` means the path rule allows it.

4. Find the day the release bot was refused. This reads public data from GitHub and changes nothing:

   ```bash
   gh run list -R Controlvar/controlvar --workflow release-please.yml --limit 1000 \
     --json createdAt,conclusion --jq 'reverse | .[0:10] | .[] | [.createdAt, .conclusion] | @tsv'
   ```

   ```text
   2026-09-24T01:25:17Z	failure
   2026-09-24T04:28:30Z	failure
   ...
   2026-09-25T01:08:41Z	failure
   2026-09-25T01:31:10Z	failure
   2026-09-25T03:22:13Z	success
   ```

   Nine failures, then a success with no change to the release workflows in between. Run
   `grep -n 'needs an org owner' CLAUDE.md` to find the note the failures produced (line 1165).

5. Write down, for your own project, the three or four actions an agent must never take without asking
   you at that moment, and one setting only someone outside your team can change. Compare your list with
   the engineering manager's three questions.

## Summary

In Controlvar most commits are co-written by an agent, and the history says so in 982 trailers. The roles
those agents play are files in `.claude/agents/`, each with a tool list and a brief; the planning roles
have no write tools, and the three that draft are also told in a sentence that they are read-only. What
people keep is defined by reversibility: steps that
cannot be undone wait for a person at the moment they happen, steps that need someone else's account are
named in the plan before the work starts, and a team recommendation never stands in for either. Release
Please's nine failed runs show what the second rule saves. Chapter 2 turns to the file every agent reads
first, and to what happens when one of its instructions is wrong.

## Further reading

- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md): the
  same roles from a contributor's side, and what of the process applies to an outside pull request.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): the twenty required
  checks and how they are kept honest.
- [Platform Operations, Hour 10](../platform-operations/part-4-release-engineering.md): what "required"
  means for a check.

---

_Next: [Chapter 2 — Instructions that work](./chapter-2-instructions-that-work.md)._

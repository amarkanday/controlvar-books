# Chapter 1 — What the agents do, and what people keep

**Outcome:** you can say what AI agents do in Controlvar, find where that is written down, and list the
actions that always wait for a person.

## The work is in the history

Start with what you can measure. Most commits in Controlvar's history end with trailers, the
`Key: value` lines after the message. One of them, `Co-Authored-By`, names whoever wrote the change with
the author. At commit `09d815ea`, 982 of the 1,310 commits carry a `Co-Authored-By` trailer that names a
Claude model. Most merged pull requests on the public tracker carry a matching footer line in their
description.

`CLAUDE.md`, the instruction file at the root of the repository, asks for exactly that. Its section on
commits gives the shape:

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

So the record exists, and you can count it. What a trailer cannot tell you is what anyone checked before
the commit was made. The rest of this book is about that part.

> **By the Way.** `CLAUDE.md` opens with "This file provides guidance to Claude Code (claude.ai/code)
> when working with code in this repository." It is written for one tool, but it is plain prose. Any
> tool that reads files can be pointed at it, and so can a person.

## The roles are files

The agents are not described in a wiki. Each role is a Markdown file in `.claude/agents/`, checked into
the repository like code. There are thirteen. Each starts with a short header and then a brief in prose:

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

`name` is how the role is called. `description` says what it does and when to use it. `tools` limits what
it may use, and `model` names which model runs it. The pull request that added the first two reviewers
said why they are files: "They are checked in, so the process travels with the codebase rather than
living in one machine's memory."

The thirteen fall into three groups:

| Group | Roles | May use Write and Edit? |
|---|---|---|
| Plan, before any code | `software-architect`, `qa-engineer`, `ux-designer`, `principal-engineer`, `engineering-manager` | No: Read, Glob, Grep, Bash |
| Build and review | `tester` and `auto-fixer`; `reviewer` | `tester` and `auto-fixer` yes; `reviewer` no |
| Specialists | `a11y-auditor`, `contract-tester`, `data-generator`, `scenario-runner`, `validator` | No (the scenario runner adds browser tools) |

Only two roles may edit files. The five planning roles work on **plans, not code**, and the three
drafting roles end their briefs with the same sentence:

```text
# 📁 .claude/agents/software-architect.md
You are read-only: no file writes, no commits, no GitHub or AWS calls that
create anything. The author writes your draft into the plan.
```

> **Watch Out!** A tool list narrows what an agent reaches for; it is not a sandbox. Every role here
> keeps `Bash`, and a shell can write a file, make a commit or call an API. "You are read-only" is the
> rest of the rule, and it is prose. Treat a tool list as a guard rail and still say, in words, what the
> agent must not do. Chapter 4 makes that part of every brief.

Beside the roles, `.claude/commands/` holds slash commands: short instruction files for repeated tasks,
such as `/commit` and `/test-backend`. Chapter 2 looks at one that has drifted.

## What people keep

The agents draft, check, build and review. Some things they never do on their own, and the repository
says which, in three places.

**The plan names them.** Every plan has a heading for them (Chapter 3):

```text
# 📁 CLAUDE.md
    IRREVERSIBLE  what needs a human at the moment it happens
```

**The engineering manager checks them.** Its brief asks what is hard to undo, and says when approval
has to happen:

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

Note "at the moment they happen". An approved plan is not permission to publish. The approval is asked
for again when the step is reached.

**A blocked question has a rule.** When work stops on a question the maintainer has not answered,
`CLAUDE.md` does not want the agent to stall or to guess. It sends the question to two reviewers, and then
limits what their answer can do:

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

So the line is drawn by reversibility, not by difficulty. An agent may settle a hard question that can be
undone, as long as it writes down that it did. It may not take an easy step that cannot be undone.

> **By the Way.** `DECISIONS.md`, and the `drafts/` and `reviews/` folders Chapter 4 describes, are not in
> the public repository. The process keeps them beside the plans. If you adopt the process, keep your own.

### Two mechanisms behind the rules

Two settings in the repository back these rules with something stronger than prose.

The branch protection for `main` is written in `scripts/configure-repo.sh`. It lists twenty required
checks and refuses force pushes to `main`:

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

The checks gate the code. The payload requires no reviewer's approval (`required_pull_request_reviews`
is `None`), so who merges, and when, is a matter of process. The script's own comment adds that live
protection may differ from this body, and that a separate check reports any difference.

The second is the contributor guard (Chapter 8). A pull request from a fork may change only files in the
areas open to outside contributors. `CLAUDE.md` and everything under `.claude/` are outside them. The
instructions that steer the agents are changed only by maintainers.

## Try it

1. Count the commits with an AI co-author, and all the commits:

   ```bash
   git log 09d815ea --format='%(trailers:key=Co-Authored-By,valueonly,separator=%x2C)' | grep -c 'noreply@anthropic.com'
   git rev-list --count 09d815ea
   ```

   At that commit: `982` and `1310`. On a newer `main` both are larger.

2. Ask your AI tool: "Which roles in `.claude/agents/` may edit files, and which are told in their brief
   that they are read-only? Name the files." Then check its answer:

   ```bash
   grep -H -E '^(tools|model):' .claude/agents/*.md
   grep -l "You are read-only" .claude/agents/*.md
   ```

   Two roles list `Write`: `auto-fixer` and `tester`. Three briefs say "You are read-only":
   `qa-engineer`, `software-architect` and `ux-designer`. If your tool said the principal engineer's
   brief says it, it guessed: that brief limits itself to reviewing plans, but never uses those words.

3. Ask the contributor guard about the instruction files. It is plain Python and only reads the names:

   ```bash
   python3 -B -c 'import sys; sys.path.insert(0, "scripts"); import contributor_guard as g; [print(p, g.path_rule(p)) for p in ["CLAUDE.md", ".claude/agents/reviewer.md", "backend/app/core/scheduler.py"]]'
   ```

   It prints `location` for the first two (a fork may not change them) and `None` for the scheduler
   (it may).

4. Write down, for your own project, the three or four actions an agent must never take without asking
   you at that moment. Compare your list with the engineering manager's.

## Go deeper

- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md): the
  same roles from a contributor's side, and what of the process applies to an outside pull request.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): the twenty required
  checks and how they are kept honest.
- [Platform Operations, Hour 10](../platform-operations/part-4-release-engineering.md): what "required"
  means for a check.

---

_Next: [Chapter 2 — Instructions that work](./chapter-2-instructions-that-work.md)._

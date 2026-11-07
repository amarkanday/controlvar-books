# Chapter 10 — When not to use an agent

**Outcome:** you know which work Controlvar's repository gives to a script, a test or a person instead of
an agent, and what an agent's mistakes have cost here.

This chapter says only what the repository's own text supports. It does not compare tools or count
money: the repository does neither.

## When a script already answers

An agent asked a yes-or-no question about the codebase can always improvise a way to answer it. If a
tested check already answers it, the improvisation is a step backwards. The principal engineer's brief:

```text
# 📁 .claude/agents/principal-engineer.md
- **Is a tested gate already available?** If CI already answers the question,
  the plan should run that, not improvise a grep. Three improvised checks in
  one session here were each wrong in a different way.
```

The import convention is the small case. `/fix-imports` says "There is no script for this — it is one
grep", and gives the `grep`. A one-line command that is right every time beats a judgement that is right
most times.

The large case is a check you keep doing by hand, or keep asking an agent to do:

```text
# 📁 CLAUDE.md
If you find yourself running the same manual rehearsal in successive rounds of
work, it belongs in CI. That is where the transition tests in
`backend/tests/integration/database/` came from.
```

An agent can run a rehearsal as often as you like, and that is the trap. Each run is a fresh
improvisation with no memory of the last (Chapter 4). A test runs the same way every time and fails
where everyone can see it.

## When the work is a queue, not a task

`CLAUDE.md` has a section on Dependabot's pull requests, whose open count grew until people bulk-closed
them by hand, "at least three times". The fix was not better clean-ups. It was a workflow that merges the
safe ones itself:

```text
# 📁 CLAUDE.md
So before reaching for the limits again: **if the queue is growing, ask what
is supposed to drain it.** A rate limit on an undrained queue only changes how
fast it fills. The same reasoning applies to any bot-generated backlog.
```

An agent is a fast hand. Pointing a fast hand at a queue that nothing drains gives you the same queue
again tomorrow. Ask what should drain it, and build that.

## When it needs a person

Some steps are not the agent's to take, however clear the plan. Chapter 1 listed them: anything on the
IRREVERSIBLE list (publishing, a force push, infrastructure that bills, an announcement), and any decision
that is not the author's to make (a public contract, money, someone's data). Others need an account the
agent does not have. `CLAUDE.md` gives one, for the bot that opens release pull requests:

```text
# 📁 CLAUDE.md
that is not a repository setting you can fix from here -- the repository API
answers `409 The organization does not allow GitHub Actions to create or
approve pull requests`. It is
`github.com/organizations/Controlvar/settings/actions` -> Workflow
permissions, and it needs an org owner.
```

The useful thing an agent can do with such a step is name it early: the engineering manager's brief wants
it "identified in the plan, not discovered mid-execution".

And there is the tie. When reviewers reject a revision twice, the cap (Chapter 7) stops the agent and
hands the disagreement to a person in writing. Another agent cannot break that tie; it would only add a
third opinion.

## When the change is too small for the process

The full process, five roles and a nine-heading plan, is for "anything beyond a single-file change". Below
that it is overhead, and `CLAUDE.md` says so about its own spec section:

```text
# 📁 CLAUDE.md
A spec is not ceremony for a one-line fix. It is how a change whose behaviour
other things depend on gets its edges decided before they are discovered.
```

The habits still apply to a one-line fix: a test that fails without it, a claim backed by a command, a
NOT VERIFIED line. What it does not need is three drafts and two verdicts. The drafting roles are told the
same: a role with nothing to say "says so in one line".

## What an agent's mistakes cost here

The repository does not count tokens or hours. It records consequences, and they fall into three kinds:

- **Review rounds.** Three on the pull request that started the plan-first rule (Chapter 3), each finding
  a defect in the last round's fix. A stable fix that "shipped weeks later" because it was bundled with
  an unstable one (Chapter 7). And the general rule: "One command is cheaper than the review round that
  finds it."
- **Near misses.** A pull request that "twice nearly" shipped "a silent outage". A tamper test that sent
  a real AWS command, and failed only because that machine had no credentials (Chapter 8).
- **Things that cannot be taken back.** Sixteen commit messages in the published history with an absolute
  home path (Chapter 8). A push is publication, so the leak guard now runs before one.

Every rule in this book came from one of those three, which is the honest answer to "what does it cost?".
The cost of an agent's mistake is decided less by the agent than by how early something checks it.

> **By the Way.** The role files name a model each: `model: opus` for the five planning roles, `model:
> sonnet` for the reviewer, the tester, the auto-fixer and the five specialists. The files do not say why,
> so this book does not either.

## The book on one page

- Write instructions that run as written, name the one place, say what the other choice breaks, and carry
  their story. Then check them against the gates (Chapter 2).
- Plan before code, with the author's premises checked by someone else, and a NOT VERIFIED line that is
  never empty (Chapter 3).
- Brief every agent as if it remembers nothing, because it does not. Pass paths, not pastes. Name your weak
  point (Chapter 4).
- Accept no claim about behaviour without a command, where the code runs, with its exit status (Chapter 5).
- Break what a check guards and watch it fail, where the check runs (Chapter 6).
- Review the diff of the diffs, plant your own defects, stop at the merge bar (Chapter 7).
- Put rails under the judgement, for the day it fails (Chapter 8).
- Give repeatable checks to scripts, queues to automation, and irreversible steps to people (this chapter).

## Try it

1. See which model each role names:

   ```bash
   grep -H '^model:' .claude/agents/*.md
   ```

   Five `opus`, eight `sonnet`.

2. Look at `ls scripts/` and pick three scripts that answer a question an agent might otherwise
   improvise: for example `check_dco.py`, `contributor_guard.py` and `doc_claims.py`. For each, write the
   question it answers in one line.

3. For your own project, make three lists: work you will give to a script or a test, work that waits for
   you at the moment it happens, and work you will give to an agent. Put a reason beside each item. If the
   agent list has something irreversible on it, move it.

## Go deeper

- [Before You Change Controlvar](../before-you-change-controlvar/README.md): the whole guide, for the day
  your first change is your own.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): the required checks,
  tamper-tested gates and the one Deploy path.
- [Platform Operations, Hour 11](../platform-operations/part-4-release-engineering.md): from merge to
  production, where the deploy happens only when a person asks for it and approves it.

---

_Previous: [Chapter 9 — A worked example](./chapter-9-a-worked-example.md). Back to the
[contents](./README.md)._

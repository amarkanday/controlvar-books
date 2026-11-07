# Chapter 10 — When not to use an agent

At 16:00 UTC on 24 September 2026, a commit was made in Controlvar's history with the subject "ci(deps):
auto-merge Dependabot bumps that pass the gate". Its message is the history of a chore that kept coming
back. Dependabot, the bot that opens a pull request for each dependency update, had filled the queue to
between 12 and 30 open pull requests, and people had bulk-closed them by hand at least three times. Each
time, the response was to slow the bot down. One of those attempts, the message says, "added major ignores
on 18 of 19 ecosystems, staggered the days and cut open-pull-requests-limit 56 -> 37". The queue was back
to 12 within a day.

The commit then names the cause in capitals: "Volume was never the cause. NOTHING MERGED THEM." There was
no workflow that merged a bump, and the repository did not allow auto-merge, so every one of the 28 bumps
merged in the previous 60 days had been merged "by a human who happened to look". With nineteen ecosystems
updating weekly or monthly, the queue could only grow between clean-ups. The fix was a workflow,
`.github/workflows/dependabot-automerge.yml`, that turns on auto-merge for the safe updates and leaves
them to the twenty required checks; run against the twelve bumps then open, its logic would have merged
all twelve. (The numbers in that message, #297 for the commit and #280 for the earlier attempt, belong to
the repository's earlier, private home.)

The story is about a bot's queue, and its fix is a workflow with no agent in it. It belongs in this
chapter because an agent is the newest way to repeat the mistake. Ask one to sweep the queue every
morning, and it will do it faster and more patiently than a person, and the queue will be back the next
day exactly as before. A fast pair of hands is still a pair of hands. This chapter is about the work
Controlvar's repository keeps away from agents: work a script or a test already does better, work that is
a queue, work that needs a person, and work too small for the process. It ends with what an agent's
mistakes have cost here, as the repository records them, and with the book on one page.

## When the work is a queue

`CLAUDE.md` tells the Dependabot story in a section titled "Dependabot: the queue is a merge problem, not a
volume problem", and ends it with the general rule:

```text
# 📁 CLAUDE.md
So before reaching for the limits again: **if the queue is growing, ask what
is supposed to drain it.** A rate limit on an undrained queue only changes how
fast it fills. The same reasoning applies to any bot-generated backlog.
```

The repair is a short decision, small enough to read in full. Here is the heart of it:

```bash
# 📁 .github/workflows/dependabot-automerge.yml
          case "$UPDATE_TYPE" in
            version-update:semver-patch|version-update:semver-minor) ok=yes ;;
            version-update:semver-major)
              # Actions majors only -- see the header.
              if [ "$ECOSYSTEM" = "github_actions" ]; then ok=yes; else ok=no; fi ;;
            *) ok=no ;;
          esac
```

Patch and minor updates merge themselves once every required check is green. A major update merges itself
only for GitHub Actions, where, as `CLAUDE.md` puts it, "an Actions major is how GitHub forces you off a
retired runner runtime, and CI passing *is* the test for one". Every other major waits for a person,
because it is a migration. And `gh pr merge --auto`, the command the workflow runs, bypasses nothing:
branch protection still requires every check, and a failing bump stays open and red.

Notice what made this safe to automate. The decision is a rule, written down once, about a property the
bot reports (the update type). The judgement that remains, whether a major upgrade is worth doing, stays
with people. Neither half needed an agent: the rule needs no judgement at all, and the judgement is one
the project keeps for a person. When a backlog keeps growing, find the rule that should drain it and
write that down as code.

## When a script already answers

An agent asked a yes-or-no question about a codebase can always improvise a way to answer it, usually a
search. If a tested check already answers the question, the improvisation is a step backwards. The
principal engineer's brief asks this of every plan:

```text
# 📁 .claude/agents/principal-engineer.md
- **Is a tested gate already available?** If CI already answers the question,
  the plan should run that, not improvise a grep. Three improvised checks in
  one session here were each wrong in a different way.
```

The small case is a check that is one command. The `/fix-imports` slash command says, of the rule that
every import is rooted at `backend.app`, that there is no script for it because "it is one grep, and a hit
is the defect", and gives the command. A one-line command that is right every time beats a judgement that
is right most of the time, and it costs nothing to run again.

The large case is a check you keep doing by hand, or keep asking an agent to do. `CLAUDE.md` turns that
into a rule:

```text
# 📁 CLAUDE.md
If you find yourself running the same manual rehearsal in successive rounds of
work, it belongs in CI. That is where the transition tests in
`backend/tests/integration/database/` came from.
```

An agent can run a rehearsal as often as you like, and that is the trap. Each run is a fresh
improvisation by an agent that remembers nothing of the last one (Chapter 4), and its report is a claim
you then have to check (Chapter 5). A test runs the same way every time and fails where everyone can see
it.

## When it needs a person

Some steps are not an agent's to take, however good the plan. Chapter 1 drew the line by reversibility:
publishing, a force push, infrastructure that bills, an announcement, and any decision that is not the
author's to make, such as a public contract, money or someone's data. The engineering manager's brief
adds a second kind, the step that needs an account the author does not control, and asks that it "be
identified in the plan, not discovered mid-execution". `CLAUDE.md` gives a real one, from the release
process:

```text
# 📁 CLAUDE.md
that is not a repository setting you can fix from here -- the repository API
answers `409 The organization does not allow GitHub Actions to create or
approve pull requests`. It is
`github.com/organizations/Controlvar/settings/actions` -> Workflow
permissions, and it needs an org owner.
```

No amount of agent effort gets past a 409 from an organisation setting. What an agent can usefully do is
recognise the shape early and say, in the plan, that this step waits for a named person. Put that in the
brief (Chapter 4): when a step needs an account the agent does not have, the agent stops and reports it,
and does not look for another way in.

One more step waits for a person: the tie. `CLAUDE.md` caps the rounds (Chapter 7): "If reviewers reject a
revision twice, stop revising. Work the uncontested parts and leave a written note." Its reason is the
tie itself: "Iterating against reviewers with nobody to break a tie is how the fix-round spiral starts".
Put the note where a person will read it.

## When the change is too small for the process

The full process, five roles and a plan with nine headings, is for "anything beyond a single-file change".
Below that line it is overhead, and `CLAUDE.md` says so about its own spec section:

```text
# 📁 CLAUDE.md
A spec is not ceremony for a one-line fix. It is how a change whose behaviour
other things depend on gets its edges decided before they are discovered.
```

The habits still apply to a one-line fix: a test that fails without it, a claim backed by a command, a
NOT VERIFIED line. What a one-line fix does not need is three drafts and two verdicts. The drafting roles
are told the same thing from their side: a role with nothing to say for a change "says so in one line;
do not invent scope to fill a section". Every section written is a section someone has to review.

## What an agent's mistakes cost here

The repository counts neither tokens nor hours. What it records is consequences, in the stories behind its
rules, and they come in three sizes.

The smallest and commonest cost is a review round. Three rounds on one pull request, each finding a defect
in the previous round's fix, are why plans are now reviewed before code is written (Chapter 3). A stable
part of a change that "shipped weeks later" because it was bundled with an unstable one is why the
engineering manager defaults to splitting (Chapter 7). `CLAUDE.md` sums up the economics in one sentence:
"One command is cheaper than the review round that finds it."

The middle size is the near miss, the mistake that would have been expensive and was stopped late. The
pull request behind the plan-first rule "twice nearly" shipped "a silent outage", and review rounds caught
both. A tamper test sent a real AWS command and failed only because the machine had no credentials
(Chapter 8), which is luck. Each near miss became a rule, and the one stopped by luck became a rail.

The largest is the mistake that cannot be taken back. Sixteen commit messages in the published history
held an absolute home path (Chapter 8). A push is publication, so the leak guard now runs before a push and
again on every pull request. History can be rewritten only by a force push, which is itself on the list of
things that wait for a person.

Most of the rules in this book trace to one of those three. What an agent's mistake costs depends less on
the agent than on how early something checks it: a plan review costs less than a code review, and either
costs less than a published secret.

> **Note**
>
> **Two models, and no stated reason.** Each role file names a model. The five planning roles say `model:
> opus`; the reviewer, the tester, the auto-fixer and the five specialists say `model: sonnet`. None of the
> files says why, so this book does not guess. If you adopt the roles, decide your own split and write the
> reason down beside it.

## The book on one page

- Know which actions wait for a person, and write them down before the work starts (Chapter 1).
- Write instructions that run as written, name the one place, say what the other choice breaks, and carry
  their story; then hold them to the gates (Chapter 2).
- Plan before code, with every premise checked by running something, and a NOT VERIFIED section that is
  never empty (Chapter 3).
- Brief every agent as if it remembers nothing, because it does not; pass paths, not pastes, and name
  your own weak point (Chapter 4).
- Accept no claim about behaviour without the command, the place it ran and its exit status (Chapter 5).
- Break what a check guards and watch it fail, where the check runs (Chapter 6).
- Review the diff of the diffs, plant your own defects, and stop at the merge bar (Chapter 7).
- Put rails under the judgement, for the day the judgement fails (Chapter 8).
- List a change's promises, and ask for the test and the plant behind each (Chapter 9).
- Give repeatable checks to scripts, queues to rules, and irreversible steps to people (this chapter).

## Try it

Steps 1 to 3 need only a clone of Controlvar and a shell; the output was taken at `09d815ea` on macOS.

1. See which model each role names, and count them:

   ```bash
   grep -h '^model:' .claude/agents/*.md | sort | uniq -c
   ```

   ```text
      5 model: opus
      8 model: sonnet
   ```

2. Count the ecosystems Dependabot watches today, and the limits it runs under:

   ```bash
   grep -c 'package-ecosystem' .github/dependabot.yml
   grep -h 'open-pull-requests-limit' .github/dependabot.yml | sort | uniq -c
   ```

   ```text
   20
     10     open-pull-requests-limit: 1
      2     open-pull-requests-limit: 2
      8     open-pull-requests-limit: 3
   ```

   Twenty ecosystems, one more than when the auto-merge workflow was written, and none allowed more than
   three open pull requests at a time. Low limits are harmless once something drains the queue.

3. Find the line in the auto-merge workflow that makes the decision, and the command it runs:

   ```bash
   grep -n -F -e 'version-update:semver-patch|' -e 'gh pr merge' .github/workflows/dependabot-automerge.yml
   ```

   ```text
   99:            version-update:semver-patch|version-update:semver-minor) ok=yes ;;
   117:        run: gh pr merge --auto --squash "$PR"
   ```

   Two lines hold the whole policy for the common case. Read the `Explain why not` step below them to see
   what the workflow says when it leaves a bump for a person.

4. Look at `ls scripts/` and pick three scripts that answer a question an agent might otherwise
   improvise, for example `check_dco.py`, `contributor_guard.py` and `doc_claims.py`. For each, write the
   question it answers in one line. Then ask your AI tool the same three questions about the repository,
   and compare how it found each answer with what the script does.

5. For your own project, make three lists: work you will give to a script or a test, work that waits for
   you at the moment it happens, and work you will give to an agent. Put a reason beside each item. If the
   agent list has anything irreversible on it, move it. If any list has a queue on it, write down what
   drains the queue.

## Summary

An agent is a fast, capable pair of hands, and the Dependabot queue shows what fast hands do to a problem
that speed does not solve: they reset the counter, and the queue comes back. Controlvar keeps four kinds of
work away from agents. It gives repeatable checks to scripts and tests, because a test runs the same way
every time; it gives queues to a written rule; it keeps irreversible steps and ties for people; and it
spares one-line changes the full process. The cost of an agent's mistakes, as the repository records it,
falls in review rounds, near misses and things that cannot be undone, and the cheapest place to pay it is
usually the earliest check. That ends the book; the contents page has every chapter, and the other books in
this library show the code these habits were built around.

## Further reading

- [Before You Change Controlvar](../before-you-change-controlvar/README.md): the whole guide, for the day
  your first change is your own.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): the required checks,
  tamper-tested gates and the one Deploy path.
- [Platform Operations, Hour 11](../platform-operations/part-4-release-engineering.md): from merge to
  production, where the deploy happens only when a person asks for it and approves it.

---

_Previous: [Chapter 9 — A worked example](./chapter-9-a-worked-example.md). Back to the
[contents](./README.md)._

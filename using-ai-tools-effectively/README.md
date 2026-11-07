# Using AI Tools Effectively

### _Getting work you can trust from AI coding agents, taught through how Controlvar is built_

## Preface

At 01:08 UTC on 25 September 2026, the pull request that added Controlvar's leak guard merged, and it
brought with it a file the project had kept out of its public tree until then. `CLAUDE.md` is the
instruction file that the AI agents writing most of Controlvar read before they touch anything.
Development had just moved into the public repository, and the commit's message gives the reason the file
had to come too: "Development happens here, so the working agreement has to be here too." It arrived with
1,050 lines.

Much of it was not instruction in the usual sense. It was a record of what had gone wrong while agents did
the work: a pull request whose three review rounds each found a defect in the previous round's fix, a local
suite that stayed green where the bug could not appear, a pipe through `tail` that turned a killed test run
into a success, four searches that agreed a file was unused while a test still read it. Each story ended in
a rule. Later that day the project wrote down who checks the work as well. Pull request
[#89](https://github.com/Controlvar/controlvar/pull/89), merged at 20:53, added the five planning roles,
from `software-architect` to `engineering-manager`, as files in `.claude/agents/`, so that, in its
description's words, the process "travels with the codebase".

At commit `09d815ea`, the one this book is pinned to, 982 of the repository's 1,310 commits carry a
co-author trailer naming Claude. `CLAUDE.md` has grown to 1,308 lines through 28 commits, and
thirteen role files sit in `.claude/agents/`. Getting an agent to produce work is the easy part, and the
history shows plenty of it. Knowing whether the work is right is the hard part, and the history shows that
too: pull requests whose descriptions list the defects they planted and the things they did not verify,
and the few that went wrong anyway, with the rule each one left behind.

This book exists because that record is unusually complete and entirely public. You can read a rule, find
the incident that produced it, and run the command that shows it still holds, all in one clone. The habits
do not depend on one tool or one model. Where a chapter shows a concrete setup, it is the one in the
repository, so you can open it, run it and break it.

## Who this is for

You already use an AI coding tool and want its work to hold up in review and in production, or you are
about to start and would rather begin with habits than with luck. You may also be a contributor to
Controlvar who wants to know why its instruction files and its review process look the way they do. You
need to be able to read code in some language and to have run a test suite before. You do not need to have
read the library's other books; where a chapter leans on one, it names the lesson.

## What you need

Keep a clone of Controlvar open beside the book:

```bash
git clone https://github.com/Controlvar/controlvar.git
cd controlvar
```

You also need an AI coding tool of your own, any one that can read files and run commands in that clone.
Many exercises give your tool a short task and then ask you to check its answer by running something
yourself.

Most steps need only git, Python 3 and a shell. Steps that read runs, pull requests and issues on the
public tracker use the GitHub CLI, `gh`, signed in to any account. A few steps run a single test file and
need the virtual environment from
[Before You Change Controlvar, Chapter 2](../before-you-change-controlvar/chapter-2-run-it.md); Chapter 9's
test steps also need PostgreSQL, which `make db` starts with `docker compose`. Nothing in the book touches
real AWS or a production system, and nothing asks you to push.

## How to use this book

Read the ten chapters in order the first time, because each leans on the ones before it. After that, use
them as a checklist when you brief, review or merge an agent's work.

Each chapter opens with an incident from Controlvar's history, dated in UTC and traced to the pull request,
issue or commit it comes from, and then says what the chapter will give you. The argument is carried in
prose and backed by the repository's own files. A block whose first line is `# 📁 path` is copied from that
file at `09d815ea`; a line of `...`, or `[...]` at the end of a line, marks where the quote was trimmed.
Text quoted from a pull request description or a commit message carries no tag, and the sentence before it
says where it comes from.

Most chapters then check an illustrative piece of agent work line by line: a release plan, an edit to the
instruction file, a spec, a brief, a report, a tamper report, a proposed rail. Each is labelled as
illustrative and built only from commands and paths that exist at the pinned commit. Chapter 7
reconstructs a review and says so, and Chapter 9 follows one real change from issue to release instead.

Sidebars come in two kinds. A **Note** adds context, or corrects a lesson where the record is more precise
than the rule drawn from it. A **Warning** marks a trap. The house style also allows a **Tip**, though no
chapter in this edition needed one.

Every chapter ends with three sections. **Try it** is a numbered walkthrough on your clone: each command,
the output it printed at `09d815ea`, and what that output means. Steps for your AI tool say what to ask,
in quotes or in a sentence. A step this edition could not run says so, and where a pull request reports the result, the step
quotes it. **Summary** is one paragraph, and its last sentence points onward. **Further reading** points to
the lessons in the library's other books that cover the same ground.

## Chapters

1. **[What the agents do, and what people keep](./chapter-1-who-does-what.md)**. Release Please failed
   nine times in a day over a setting only an organisation owner could change. The chapter measures what
   agents do in the history, reads the thirteen role files, and draws the line the repository draws around
   steps that wait for a person.
2. **[Instructions that work](./chapter-2-instructions-that-work.md)**. Release 0.17.0 was built twice
   because `CLAUDE.md` said to dispatch a workflow that already ran by itself. What the file holds, what
   makes an instruction usable, how instructions go stale, and how to keep them true.
3. **[Plan before code](./chapter-3-plan-before-code.md)**. In pull request #89 an agent describes
   skipping the plan and walking into two traps the repository had already recorded. The drafting roles,
   the two reviewers, the plan's nine headings, and a spec written and checked against the code.
4. **[Briefing an agent](./chapter-4-briefing-an-agent.md)**. Two role files told every agent that read
   them to sign in with a body the API refuses, until #1053 ran what they said. Why each agent starts with
   nothing, what every brief must carry, and why a path beats a paste.
5. **[Checking an agent's claims](./chapter-5-checking-claims.md)**. The leak guard's own test passed in
   one CI job and failed in another. Four ways a real run misleads: the wrong place, a flag taken at its
   name, an empty search, and a hidden exit status.
6. **[Tamper-testing](./chapter-6-tamper-testing.md)**. Issue #470 found two access tests that ran as an
   administrator and could not fail. How to break what a check guards, choose a plant it can see, run it
   where the check runs, and tell a broken check from a bad probe.
7. **[Reviewing agent work](./chapter-7-reviewing-agent-work.md)**. In #1686 a reworded sentence, written
   to answer one of the smaller findings, broke a test in another directory. Where to aim each later round, what to
   ask of an agent's tests, and the merge bar and cap that end the rounds.
8. **[Safety rails](./chapter-8-safety-rails.md)**. A tamper test sent a real AWS command, and #178 put a
   fake `aws` in front of every test. The mechanisms that hold when an instruction is missed, the gaps each
   admits to, and two rules for the moment an agent proposes a cleverer fix.
9. **[A worked example](./chapter-9-a-worked-example.md)**. Pull request #1464, from issue to release in
   one day: a plan review that measured a real defect, an author's seven planted defects all caught, and a
   reviewer's plants that found three promises no test held.
10. **[When not to use an agent](./chapter-10-when-not-to-use-an-agent.md)**. The Dependabot queue came
    back after every clean-up until a workflow drained it. The work the repository keeps for scripts, for
    written rules and for people, what an agent's mistakes have cost, and the book on one page.

## Where this fits

```text
Core path:  Python → FastAPI → React/Next.js → Build a Platform
Specialize:                                    ├─ Experimentation Statistics
                                               ├─ SDK Engineering
                                               ├─ Platform Operations
                                               └─ Production Engineering
Contribute: Before You Change Controlvar: read it before your first pull request
Work with AI tools: Using AI Tools Effectively (this book): read it alongside any of the others
```

This book is about how the work is done, and it belongs to no one part of the product. Read it on its own,
or next to [Before You Change Controlvar](../before-you-change-controlvar/README.md), whose
[Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md) describes the same process from a
contributor's side.

_Turn to [Chapter 1](./chapter-1-who-does-what.md)._

---

## A note on accuracy

Every chapter is checked against Controlvar commit `09d815ea` on `main`, nine commits after release
0.43.2. That is newer than the release some of the other books are pinned to, so a path or a count here may
be ahead of them. Every `# 📁` block was copied from the file at that commit and compared with it line by
line, and the library's checks ([`tools/README.md`](../tools/README.md)) hold each cited path and each
identifier in a quoted block to that commit: `REF=09d815ea tools/check.sh using-ai-tools-effectively`.
Paths those checks do not cover, under `.claude/` and `tests/`, were checked by hand at the same commit.

The sources are public: the repository at that commit, its history, and its public issues, pull requests
and workflow runs, read with `git` and `gh`. Pull request numbers in commit subjects from before about
2026-09-24 belong to the repository's earlier, private home and mean something else on the public tracker;
the chapters say so where they quote one. Three of the workflow logs Chapter 1 counts have expired on
GitHub, and the chapter says so.

**What was run for this edition.** At that commit, in fresh clones, every command in the chapters and
their Try it steps that needs only git, Python 3, a shell and `gh`. That includes the trailer and role
counts, the contributor guard's classification of paths, the host allow-list rule from Chapter 3, the leak
guard over the tree, over a `git archive` copy with no `.git` and against a planted account id, the import
grep and its double-escaped copy against a planted import, the DCO check and the title rule, the pipe and
`rm -rf` exit statuses, the two heads of pull requests #1464 and #1686 and their diffs, and the `gh`
queries of runs, pull requests and issues. For Chapters 1 to 4 and 6 to 10, those
commands were then run a second time, independently, and the output compared with the text.

**What was not run for this edition.** Anything that needs Docker, a database, the project's virtual
environment or CI: the pytest steps (Chapter 6's smoke test, Chapter 8's test of the AWS rail, Chapter 9's
scheduler tests and planted defects), `scripts/core_build.sh`, `make lint` and `make test`. Results of that
kind, among them the tamper results of #470, #474, #1053 and #1686 and the probe in #178, are quoted from
the pull requests that report them, and the text says so. No AI tool was run either: the book describes how
the repository's files ask an agent to behave, and it does not measure how any tool behaves. Where a chapter
reconstructs or infers, as Chapter 7 does with a review that is not public and Chapter 9 with why one
planted defect survived, it says so.

**What the first edition ran for Chapter 9.** The edition before this one exported the pull request's two
heads with `git archive` and ran its tests against a scratch PostgreSQL 14. The new tests fail on the code
before the fix (3 failed, 1 passed); each of the three planted defects the review reported passes every
test on the first head (4 passed) and fails exactly one test on the second, with the failure texts the pull
request quotes; and the first of them fails the same test at `09d815ea`.

Code moves on. If a path here no longer exists on `main`, `git grep` the symbol; the habits outlast the
line numbers.

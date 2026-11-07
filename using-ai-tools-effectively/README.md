# Using AI Tools Effectively

### _Getting work you can trust from AI coding agents, taught through how Controlvar is built_

> **Preface.** An AI coding tool can read a codebase, write a change, run the tests and describe what it
> did, all in a few minutes. The hard part is not getting it to produce work. The hard part is knowing
> whether the work is right. **[Controlvar](https://github.com/Controlvar/controlvar)**, the open-source
> A/B-testing and feature-flag platform the rest of this library teaches with, is built mostly that way:
> at commit `09d815ea`, 982 of its 1,310 commits name an AI model as a co-author. Its repository also
> holds what the maintainers learned while doing it: an instruction file the agents work from, a set of
> agent roles that plan and review before code is written, rules for checking every claim an agent
> makes, and the stories of the failures that produced each rule. This book teaches those habits. They
> do not depend on one tool. Where it shows a concrete setup, it is the one in the repository, so you
> can open it, run it and break it.

---

## Who this is for

- Engineers who already use an AI coding tool and want its work to hold up in review and in production.
- Engineers who are about to start, and want to begin with habits rather than with luck.
- Contributors to Controlvar who want to know why its instruction files and review process look the way
  they do.

You need to read code in some language and to have run a test suite before. You do not need to have
read the other books. Where a chapter leans on one, it says which lesson to read.

## What you need

A clone of Controlvar, open beside this book:

```bash
git clone https://github.com/Controlvar/controlvar.git
cd controlvar
```

An AI coding tool of your own, any one that can read files and run commands in that clone. Most "Try it"
steps ask you to give your tool a short task and then check its answer yourself, by running something.

Most steps need only Python 3 and git. A few run single test files, which need the virtual environment
from [Before You Change Controlvar, Chapter 2](../before-you-change-controlvar/chapter-2-run-it.md).
Chapter 9's tests also need PostgreSQL, which `make db` starts. Nothing in this book touches real AWS or
any production system, and nothing in it asks you to push.

## How to use this book

There are ten short chapters. Read them in order the first time; after that, use them as a checklist.

Every chapter has the same shape:

1. **The idea**, in plain words.
2. **How Controlvar does it**: the real file, quoted. Blocks tagged `# 📁 path` are copied from
   Controlvar's repository.
3. **Try it**: steps to run on your clone with your own tool, and what you should see.
4. **Go deeper**: the lessons in the other books that cover the same ground.

Sidebars mark a **Watch Out!** (a trap people fall into) and a **By the Way** (context).

## Chapters

1. **[What the agents do, and what people keep](./chapter-1-who-does-what.md)**: the roles in
   `.claude/agents/`, the trailers in the history, and the actions that always wait for a person.
2. **[Instructions that work](./chapter-2-instructions-that-work.md)**: `CLAUDE.md` as the project's
   memory, what makes a written instruction useful, and how to keep it true.
3. **[Plan before code](./chapter-3-plan-before-code.md)**: three drafting roles, a principal engineer and
   an engineering manager; the plan's nine headings; and the review rounds that made it the rule.
4. **[Briefing an agent](./chapter-4-briefing-an-agent.md)**: what a good prompt carries, why paths beat
   pastes, and why every agent starts with no memory of the last round.
5. **[Checking an agent's claims](./chapter-5-checking-claims.md)**: verify where the code runs, a flag is
   not a behaviour, an empty search is not proof, and an exit status must not be hidden.
6. **[Tamper-testing](./chapter-6-tamper-testing.md)**: break the thing a check guards and watch it fail,
   where the check runs.
7. **[Reviewing agent work](./chapter-7-reviewing-agent-work.md)**: the diff of the diffs, a fresh
   reviewer each round, the merge bar and the cap on rounds.
8. **[Safety rails](./chapter-8-safety-rails.md)**: no test reaches real AWS, the leak guard, sign-offs
   and titles, the contributor guard, and two rules about clever fixes.
9. **[A worked example](./chapter-9-a-worked-example.md)**: one merged pull request, from the issue to
   the release, with the defect its first version let through and the check that found it.
10. **[When not to use an agent](./chapter-10-when-not-to-use-an-agent.md)**: what the repository keeps
    for scripts, for tests and for people, and what an agent's mistakes cost.

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

This book is about the way the work is done, not about any one part of the product. Read it on its own,
or next to [Before You Change Controlvar](../before-you-change-controlvar/README.md), whose
[Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md) describes the same process from a
contributor's side.

_Turn to [Chapter 1](./chapter-1-who-does-what.md)._

---

## A note on accuracy

Checked against Controlvar commit `09d815ea` on `main`, nine commits after release 0.43.2. That is newer
than the release some of the other books are pinned to, so a path or a count here may be ahead of them.
Every `# 📁` block was copied from the file at that commit, and the library's checks
([`tools/README.md`](../tools/README.md)) hold each cited path and each identifier in a quoted block to
that commit: `REF=09d815ea tools/check.sh using-ai-tools-effectively`. Paths those checks do not cover
(under `.claude/` and `tests/`) were checked by hand at the same commit.

The sources are public: the repository at that commit, its history, and its public issues and pull
requests. Pull request numbers in commit subjects from before about 2026-09-24 belong to the repository's
earlier, private home and mean something else on the public tracker; this book says so where it quotes
one.

**What was run.** At that commit, in a fresh clone: every command in the Try it steps that needs no
Docker. That includes the trailer count, the agent tool listing, the contributor-guard classification of
the instruction files, the import grep and its double-escaped copy against a planted import, the leak
guard on the tree and against a planted account id in a scratch repository, the planted `aws` call
inside a test, the DCO check on Chapter 9's commit, and the pipe and `rm -rf` exit statuses. For
Chapter 9, the pull request's two heads were exported with `git archive` and its tests run against a
scratch PostgreSQL 14: the new tests fail on the code before the fix (3 failed, 1 passed); each of the
three planted defects the review reported passes every test on the first head (4 passed) and fails
exactly one test on the second, with the failure texts the pull request quotes; and the first of them
fails the same test at `09d815ea`.

**What was not run.** Anything that needs Docker, a full `make test`, `make lint` and
`scripts/core_build.sh`; anything in CI; and any AI tool. The book describes how the repository's files
ask an agent to behave; it does not measure how any tool behaves.

Code moves on. If a path here no longer exists on `main`, `git grep` the symbol; the habits outlast the
line numbers.

# The Controlvar Books

### _Learn engineering, programming and the craft of shipping software through one real, open-source codebase_

> **Preface.** Most programming books teach with a to-do list, a shopping cart and a `print("Hello")`, and stop
> just before the parts that make real software hard: types that a framework reads at runtime, a database schema
> that has to change while the old code is still serving, a statistic that is wrong if you look at it too often, a
> client library that must give the same answer in sixteen languages, a deploy that reports healthy while it rejects
> every request. These books are different. Every concept is taught **twice**: once in general terms, and once in
> the real source of **[Controlvar](https://github.com/Controlvar/controlvar)**, an open-source A/B-testing and
> feature-flag platform you can clone, run and break on your own machine. You will meet the bugs that shaped it,
> read the code that fixed them, and run the exercises against the same code.

Controlvar is a FastAPI backend (Python 3.11, SQLAlchemy 2, Pydantic v2, PostgreSQL, Redis), a Next.js dashboard
(TypeScript, React), sixteen client SDKs that all speak one HTTP contract, optional `modules/` that turn the
**core** profile into the **full** one, signed container images, AWS infrastructure defined in CDK, and a
statistics engine (Fisher's exact test, sequential testing with mSPRT, Bayesian analysis, CUPED, multi-armed
bandits). At v0.36.4 that is about 100,000 lines of application Python, 54,000 lines of dashboard TypeScript,
48,000 lines of SDK code and more than 13,000 backend test functions: big enough to be real, small enough to read.

Controlvar was called Experimently before 0.35.0. You will still meet the old name in some identifiers, and the
books point it out where it matters.

---

## Who these books are for

- **Newcomers to a language or framework** (Python, FastAPI, TypeScript, React, Next.js) who want to learn it the way
  it is used in production, not in a toy.
- **Engineers who already code** and want the next layer: data modelling and migrations, access control, background
  jobs, caching, testing strategy, observability, release engineering, and the statistics behind experimentation.
- **Contributors to Controlvar** who want to understand why the code is shaped the way it is before changing it.

You need basic programming literacy in any language (variables, loops, functions). Each book says what else it
assumes.

## The library

Nine books in three groups, and a short guide for contributors. The **core path** takes you from zero to reading
and extending the whole system; the **specializations** go deep on the subsystems that make the product what it
is; **Before You Change Controlvar** is the one to read before your first pull request; and **Using AI Tools
Effectively** shows how to get work you can trust from AI coding agents.

**Core path, in order:**

1. **[Learn Python in 24 Hours](./learn-python-in-24-hours/README.md)**: Python from absolute zero to the modern,
   typed Python the backend is written in. Start here if you do not know Python.
2. **[Learn FastAPI in 24 Hours](./learn-fastapi-in-24-hours/README.md)**: the backend framework layer: FastAPI,
   Pydantic v2, dependency injection, SQLAlchemy 2, Alembic, the middleware stack, background schedulers, and
   testing with `TestClient`.
3. **[Learn TypeScript, React & Next.js in 24 Hours](./learn-react-nextjs-in-24-hours/README.md)**: TypeScript, React and
   Next.js through the real dashboard: the API client, authentication, routing, forms, the targeting-rule builder,
   charts, Jest and Playwright.
4. **[Build an Experimentation Platform in 28 Days](./build-a-platform-in-28-days/README.md)**: the whole architecture end to end:
   the assign, track and analyse loop, the data model, access by role, feature flags and the rules engine, the
   schedulers and their lock, automatic safety rollback, the statistics engine, the core/modules split, the SDK
   contract, testing and the required checks, signed containers and AWS. After this capstone you can read and
   extend the codebase.

**Specializations, after the core path, in any order:**

5. **[Experimentation Statistics](./experimentation-statistics/README.md)**: the mathematics the product is built
   on and how the code implements it: sample-ratio mismatch, Fisher's exact test, power, the peeking problem and the
   mSPRT confidence sequence, CUPED, Bayesian analysis, Thompson Sampling and UCB1, multiple-testing correction, and
   reproducible Monte Carlo.
6. **[SDK Engineering](./sdk-engineering/README.md)**: one HTTP contract, many SDKs: why the server decides, the
   consistent hash and its golden vectors, the anatomy of a client (transport, cache, retries), porting a client
   across languages without drift, and proving that every implementation agrees.
7. **[Platform Operations](./platform-operations/README.md)**: running it: Compose, the container entrypoint,
   bootstrap and seeds, the CDK stacks, secrets, structured logging, metrics and health probes, alarms, the deploy
   and rollback workflows, migrations as the only schema writer, and releases.
8. **[Production Engineering](./production-engineering/README.md)**: decisions, reliability and safe change:
   experiment design and the decision lifecycle, evaluating LLM features, retries and replicas, database evolution,
   security and privacy, retiring a flag, and a worked review of a change that crosses the API, storage, dashboard
   and SDKs.

**Before your first change, at any point:**

9. **[Before You Change Controlvar](./before-you-change-controlvar/README.md)**: a short guide for someone about
   to make a first change: the codebase map, running the stack, where each decision lives, the tests and gates a
   pull request must pass, how changes are planned and reviewed, one real fix from issue to release, and the
   changes whose effects land far from where they are made. It links to the lessons above that go deeper.

**Working with AI coding tools, alongside any of the others:**

10. **[Using AI Tools Effectively](./using-ai-tools-effectively/README.md)**: getting work you can trust from AI
    coding agents, taught through how Controlvar is built: the instruction file the agents work from, the roles
    that plan and review before any code is written, how to brief an agent, how to check its claims and
    tamper-test its checks, how to review its work round after round, the safety rails, one real pull request
    from issue to release, and the work that belongs to a script or a person instead.

## How every book works

Each book is a set of short lessons (an **Hour**, or a **Day** in the 28-day book), grouped into Parts. Every
lesson has the same rhythm, so you can pace yourself:

1. **What & why**: the concept, in general terms, and the problem it solves.
2. **In the wild** or **How Controlvar does it**: the real code, with the file path, and what to notice in it.
3. **Try It Yourself** (or **Build It**): a hands-on exercise with the commands and the result to expect.
4. **Common mistakes** and **Key takeaways**: the traps, and the three to five things to remember.

Every Part ends with a **Workshop**: questions and answers, a quiz, and exercises with worked solutions.

Conventions used in every book:

- Code blocks tagged `# 📁 path/to/file.py` (or `// 📁 path/to/file.ts`) are real Controlvar source, quoted from the
  release the books are checked against. Blocks with no path are illustrative.
- `# ✅` marks the idiom to use; `# ❌` marks the anti-pattern it replaces.
- Sidebars run throughout: **By the Way** (context), **Watch Out!** (a trap) and **Did You Know?** (a surprising
  fact).

## What you need

A clone of Controlvar at the release the books are checked against, open beside the book:

```bash
git clone https://github.com/Controlvar/controlvar.git
cd controlvar
git checkout v0.36.4
```

The exercises send you into it. Most need Python 3.11 and Node; the ones that run the whole stack need Docker with
Compose v2, and say so. To install and operate Controlvar itself, use its documentation at
[docs.controlvar.com](https://docs.controlvar.com/): these books teach with the product, they do not replace its
manual.

## A note on accuracy

Every book is checked against **Controlvar v0.36.4** (`79f8ed18`). Code moves on: if a path no longer exists in a
newer release, `git grep` the symbol; the ideas outlast the line numbers. Commits made before Controlvar's
development moved into its public repository (up to about 2026-09-24) carry the old private repository's pull
request numbers in their subjects, and those numbers mean something else on the public tracker, so the books cite
commit hashes for that period.

Six checks hold the books to the pinned release: banned terms, paths, quoted snippets, links, Try-It numbers and
secrets. Run them with `make check` when you change a book; see [`tools/README.md`](./tools/README.md). Five
must pass; the Try-It numbers check is not built yet and always reports "not implemented" (the numbers were
re-run by hand at v0.36.4).

Found something wrong or unclear? Open an issue with the book, the lesson and what you expected. Corrections are
welcome as pull requests; run `make check` and paste its summary.

## Licence

The books are © 2024-2026 The Controlvar authors and licensed under the
[Creative Commons Attribution 4.0 International licence](https://creativecommons.org/licenses/by/4.0/) (CC BY 4.0;
full text in [`LICENSE`](./LICENSE)). You may share and adapt them for any purpose, including commercially, as long
as you give credit ("The Controlvar Books, The Controlvar authors"), link to the licence and say what you changed.

Code quoted from Controlvar (the blocks tagged `📁`) keeps Controlvar's own licences: Apache-2.0 for the core, the
modules and the dashboard, MIT for the client SDKs under `sdk/`. See the
[Controlvar repository](https://github.com/Controlvar/controlvar) for its `LICENSE` and `NOTICE`.

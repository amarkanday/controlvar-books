# Production Engineering

### _The Controlvar Edition — Decisions, Reliability and Safe Change, in 12 Hours_

> **Preface.** Here are three true stories from one codebase. A user is assigned to an experiment by two
> API processes at the same instant, the database correctly refuses the second insert, and the user gets
> a generic 500, until someone writes the test that forces the interleaving no ordinary test can reproduce.
> An LLM judge fails to answer, the system quietly records 0.5, and every comparison drifts toward
> "average" without a single error in the logs. A compliance table carries a `retention_expires_at`
> column that looks like data retention and is read by nothing at all. None of these is a statistics
> problem or a syntax problem. Each is a gap between what something is *called* and what it *does*, and
> each was found, fixed and pinned by a test in Controlvar, the open-source A/B testing and feature-flag
> platform this library uses as its reference codebase.
>
> This book is about that gap. It is the one volume in the library that crosses the layers: it starts with
> measurement and the shape of a decision, moves through retries, schedulers, schema changes, security,
> privacy and retention, and ends with the discipline of changing a running system and proving you did
> it safely. It is not a survey. In every lesson you will read the lines of real source that implement
> (or fail to implement) the property, run small pieces of the backend yourself, and plant a defect to
> watch a guard fire. By the end you should be able to take a proposed change, say which surfaces it
> touches, which claims are implemented and which are only intended, what evidence supports each, and what
> you would do if you were wrong.

---

## Who this book is for & prerequisites

- Engineers changing the API, dashboard, tracking, schedulers, storage or SDK contract of a system like
  Controlvar, and engineers reviewing such changes.
- Experiment owners who need a defensible decision and a rollout-and-removal handoff.
- Engineers reviewing authorization, data flow, retention or operational failure behaviour.
- Prerequisites: the **core four** (Python, FastAPI, React/Next.js, and the Build a Platform book),
  especially the Build book's assignment-and-event loop, the API and the data model. Experienced engineers
  can jump to a lesson by task (see the table below) with a clone of the app open alongside. You need
  Python 3.11, a virtual environment with the app's `backend/requirements.txt`, and a clone of
  `Controlvar/controlvar` checked out at this book's pin, commit `09d815ea` (`main` after v0.43.2). No
  database, browser or cloud account is needed for the hands-on blocks, which run real pieces of the
  backend in isolation.

## How to use this book

There are 12 lessons in four Parts of three Hours each:

- **Part I — Experiment Design and Decisions** (Hours 1–3)
- **Part II — Reliable Systems** (Hours 4–6)
- **Part III — Security and Privacy** (Hours 7–9)
- **Part IV — Safe Change** (Hours 10–12)

Every lesson has the same rhythm:

1. **What & why**: the general idea first, then why it matters, and a real Controlvar story that makes it
   concrete.
2. **How Controlvar does it**: real excerpts from the source, each followed by what to notice.
3. **Try It Yourself**: exact commands, the expected output, and a reasoning exercise with a worked answer.
4. **Key takeaways**, and a **review artifact**: a document or result you could hand to a reviewer.

Along the way, sidebars flag a **By the Way** (context), a **Watch Out!** (a trap) or a **Did You Know?**
(a surprising fact). Each Part ends with a **Workshop**: questions and answers, a quiz, exercises and
solutions.

Two words are used with care throughout. **Implemented** means you can point at the line of code or the
test at the pin. **Recommended practice** means a procedure you must provide; the platform does not do it
for you. Illustrative scenarios and numbers are labelled as such. When a lesson says "nothing in the code
does X", that is a statement about what was searched for and not found, and the lesson says what the search
was.

> **Convention in this book.** Blocks tagged `# 📁 path` are real Controlvar source at the book's *pin*,
> commit `09d815ea`, copied from the file at the path; read them against a checkout of that commit. A
> `...` line marks lines left out. Blocks with no path are illustrative or are commands to type. A line of
> prose that starts "Expected result" is the output of running the block above it at the pin with the
> safety environment variables set; a notice about unset Cognito settings may also appear and can be
> ignored. Have the app clone open at `09d815ea`, and a terminal in its root with the virtual environment
> active. When a lesson says a fix "shipped in" a version, that release is the first tag that contains
> it. Controlvar was called Experimently before 0.35.0, so you will still meet the old name in some
> identifiers (the advisory-lock namespace `experimently.scheduler.`, for one). They were not renamed
> because renaming them would change behaviour.

## The one idea to anchor on

**A control is what the code enforces, not what it is called.**

Look at the three stories in the Preface again. A *unique index* is not a promise that the caller gets a
clean answer. A *fallback score* is not a measurement. A *retention column* is not a retention policy. The
same shape appears everywhere in this book: a scope named `read` that no route reads; a workspace that
groups people and separates no data; a 0% rollout that is not a kill switch; a rolled-back flag whose
SDK has not yet refreshed; a green test that could never have failed.

So every lesson asks the same four questions of whatever it studies:

1. **What does the name promise?** Say it in one sentence.
2. **What does the code actually enforce?** Find the line, the constraint or the test.
3. **What would prove the difference?** A planted defect, a forced interleaving, a grep with a stated
   scope.
4. **What do you do about the gap?** Close it, document it, or accept it with an owner.

When you can answer those four for your own system, you are doing production engineering.

---

## Table of Contents

### Part I — Experiment Design and Decisions _(Hours 1–3)_ → [`part-1-experiment-design.md`](./part-1-experiment-design.md)

- **Hour 1** — Measurement and experiment design: the user who bought three times (populations, assignment and exposure, converting users, attribution and power)
- **Hour 2** — A complete experiment: the ending nobody plans for (hypothesis, instrumentation, an inconclusive result, rollout and removal)
- **Hour 3** — LLM evaluation engineering: the 0.5 that meant nothing (datasets, judges, failure accounting, cost and promotion decisions)

### Part II — Reliable Systems _(Hours 4–6)_ → [`part-2-reliable-systems.md`](./part-2-reliable-systems.md)

- **Hour 4** — Assignments, transactions and retries: two requests, one user (ambiguous writes, the event id as an idempotency contract, batch results and retry policy)
- **Hour 5** — Schedulers, replicas and stale state: five loops, one lock (advisory locks, run history, work kept off the event loop, the cache that was deleted, SDK-held rulesets)
- **Hour 6** — Database evolution and performance: the previous release is still running (expand and contract, two heads, plans, connection budgets)

### Part III — Security and Privacy _(Hours 7–9)_ → [`part-3-security-and-privacy.md`](./part-3-security-and-privacy.md)

- **Hour 7** — Security engineering through the request path: the scope that checks the key you hold (scopes, roles, workspaces, threat modelling)
- **Hour 8** — Data inventories, identifiers and minimisation: what leaves in a response (allowlists, redaction, fixed error sentences)
- **Hour 9** — Retention, deletion and recovery: the column nobody reads (what actually runs, what a delete takes, exports, restore)

### Part IV — Safe Change _(Hours 10–12)_ → [`part-4-safe-change.md`](./part-4-safe-change.md)

- **Hour 10** — The feature-flag lifecycle: the kill switch that was not (status, targeting, rollback, how long "off" takes to arrive, archive, delete and retirement)
- **Hour 11** — Changing Controlvar safely: the owner's name (a real change across storage, API, dashboard, contract and profiles)
- **Hour 12** — A release review from evidence to decision: the test that could not fail (verified, failed, unverified; a capacity claim's method; recovery; handoff)

---

## Pick a lesson for your task

| I need to… | Read |
|---|---|
| Define a trustworthy metric or run an experiment | Part I, Hours 1–2 |
| Compare models or prompts | Part I, Hour 3 |
| Reason about retries, replicas or stale state | Part II, Hours 4–5 |
| Change a schema or investigate a slow analysis | Part II, Hour 6 |
| Review an endpoint's access boundary | Part III, Hour 7 |
| Minimise collection or verify retention and deletion scope | Part III, Hours 8–9 |
| Operate and retire a flag | Part IV, Hour 10 |
| Review a change across the product | Part IV, Hours 11–12 |

## Where this fits

```text
Core path:  Python → FastAPI → React/Next.js → Build a Platform
Specialize:                                    ├─ Experimentation Statistics
                                               ├─ SDK Engineering
                                               ├─ Platform Operations
                                               └─ Production Engineering (this)
```

_Turn to [Part I](./part-1-experiment-design.md)._

---

## A note on accuracy

Verified against Controlvar `main` at `09d815ea` (6 November 2026: the platform after v0.43.2, with the
JavaScript and Python SDKs at 0.3.0). The previous edition was verified against v0.36.4; the lessons that
changed most are Hour 4 (the event id), Hour 5 (tick work off the event loop), Hour 7 (a second enforced
scope, and custom grants), Hour 10 (how long "off" takes, and what archived and deleted flags answer) and
Hour 12 (the scale-evidence method).

**What was run.** Every "Try It Yourself" block that says it needs no database was run at the pin, from a
fresh clone, with `backend/requirements.txt` installed and the backend's database and cache addresses
pointed at a closed local port, and its output is the output shown. Existing tests named in the lessons
were run at the pin and passed: the SDK-scope and flag-mutation inventories with the API-key scope rules
(55), the flag-cache guard (4), the event-key vectors and `event_id` input rules (61), the Python SDK's
transport and client tests (217), the automatic-rollback default with the kill-switch numbers (51), the
scale method's unit tests (274), the exception-text guard with the OpenAPI snapshot check (37) and the
sample-ratio tests (34). The two planted-defect exercises (the flag-cache guard and the scope
inventory) were run and failed as described. Every line of every tagged excerpt was checked to occur in
its file at the pin.

**What was not run.** Anything that needs PostgreSQL or Redis (the scheduler-lock tests, the concurrent
first-assignment test, the duplicate-event integration tests including the two-connection claim, the
migration transition tests, the overload regression test, a real `EXPLAIN ANALYZE`), the scale harness
itself (it needs Docker, k6 and a hosted runner), the commands that assume the Quick Start stack, any paid
provider call, and anything that touches a cloud account. Those lessons read the test or the document
instead and say so. The kill-switch timings in Hour 10 are the app's documented defaults, read from its
page and pinned by its test, not timed against running clients. Two claims are searches rather than runs:
that nothing reads `retention_expires_at`, and that the results path has no attribution-window setting.
The lessons give the search so you can repeat it.

**How to re-check.** The library's gates check each tagged excerpt and path against the app at a pinned
reference: see [`tools/README.md`](../tools/README.md) for how to run them. This book was checked with
`REF=09d815ea tools/check.sh production-engineering`. If you read it against a later release, re-read the
excerpts and the "Expected result" blocks first: the stability notes, the limitations page and the
migration notes in the app's `docs/` are the places the behaviour described here is most likely to change.

# Production Engineering

### _The Experimently Edition — Decisions, Reliability and Safe Change, in 12 Hours_

> **Preface.** This is a **specialization** book. The core four teach you to read and extend
> Experimently; this one teaches you to review the decisions around a change: what the experiment
> measures, what retries and replicas guarantee, who may access data, and how to ship and retire
> behavior without overlooking old clients or retained copies. It connects the statistics, SDK and
> operations books through worked engineering problems rather than repeating their setup instructions.
>
> Verified against getexperimently/experimently at `07a430df5b54b0e5dd71ddca95071ced05e9e36c`
> (source review, 2026-10-23). Other books retain the reference commits named in their own READMEs.

---

## Who this book is for & prerequisites

- Engineers changing the API, dashboard, tracking, schedulers, storage or SDK contract.
- Experiment owners who need a defensible decision and a rollout/removal handoff.
- Engineers reviewing authorization, data flow, retention or operational failure behavior.
- Prerequisites: the **core four**, especially the Build book's assignment/event loop, API and data
  model. Experienced engineers can use the task routes below with a clone of the app open alongside.

## How to use this book

12 lessons in 4 Parts, following the library's rhythm: **What & why → How Experimently does it →
Try It Yourself → Key takeaways**, with a **Workshop** per Part containing Q&A, a quiz with answers
and an exercise with a worked solution. Each Hour identifies a concrete review artifact.
The hours organize study; allow additional time for exercises that require a local stack.

Source links are pinned to the reviewed app commit. “Implemented” describes that snapshot;
“recommended practice” describes a procedure, not an automatic platform feature. Worked data and
hypothetical changes are illustrative. No production operations, paid provider calls or live-stack
exercises were run for this edition. Use a disposable environment and the existing
[onboarding guide](../guides/onboarding.md) for exercises involving the app.

The book's [source manifest](../tools/production-engineering-sources.json) records each Part's
reference commit, review date and app source paths. After an implementation change, compare those
paths between revisions, review behavioral changes, and update the text, pinned links and manifest
together. Keep the older books' references unchanged unless you audit their content too. The optional
[docs tools](../tools/README.md) remain local checks; this book adds no CI/CD configuration.

To run the local docs checks for this book, set `EXPERIMENTLY_APP` to your app checkout as described
in the tools guide, then use the book's reviewed reference:

```bash
REF=07a430df5b54b0e5dd71ddca95071ced05e9e36c make check SCOPE=production-engineering
```

This scopes the app-dependent checks to this book and still checks links across the whole library.
It does not re-verify the older books against the newer commit.

> **The one idea to anchor on:** a production change is a set of claims about decisions, state,
> authority and recovery. Make each claim explicit, verify it at its boundary, and keep any missing
> evidence visible.

---

## Table of Contents

### Part I — Experiment Design and Decisions _(Hours 1–3)_ → [`part-1-experiment-design.md`](./part-1-experiment-design.md)

- **Hour 1** — Measurement: populations, assignment/exposure, converting users, attribution and power
- **Hour 2** — A complete experiment: hypothesis, instrumentation, an inconclusive result, rollout and removal
- **Hour 3** — LLM evaluation: datasets, judges, failure accounting, cost and promotion decisions

### Part II — Reliable Systems _(Hours 4–6)_ → [`part-2-reliable-systems.md`](./part-2-reliable-systems.md)

- **Hour 4** — Assignments, transaction boundaries, ambiguous writes and retry policies
- **Hour 5** — Scheduler coordination, replica failures, caches and SDK-held rulesets
- **Hour 6** — Schema evolution, mixed versions, backfills, query plans and connection budgets

### Part III — Security and Privacy _(Hours 7–9)_ → [`part-3-security-and-privacy.md`](./part-3-security-and-privacy.md)

- **Hour 7** — Request authorization, actual scope enforcement, workspace boundaries and threat modeling
- **Hour 8** — Data inventories, stable identifiers, payload minimization and downstream copies
- **Hour 9** — Retention mechanisms, deletion scope, exports and restore behavior

### Part IV — Safe Change _(Hours 10–12)_ → [`part-4-safe-change.md`](./part-4-safe-change.md)

- **Hour 10** — Feature-flag ownership, safe defaults, rollback, compatibility and retirement
- **Hour 11** — A worked owner-label change across API, storage, dashboard, SDKs and profiles
- **Hour 12** — A release review: evidence, unverified checks, recovery decisions and maintenance

---

## Pick a lesson for your task

| I need to… | Read |
|---|---|
| Define a trustworthy metric or run an experiment | Part I, Hours 1–2 |
| Compare models or prompts | Part I, Hour 3 |
| Reason about retries, replicas or stale state | Part II, Hours 4–5 |
| Change a schema or investigate a slow analysis | Part II, Hour 6 |
| Review an endpoint's access boundary | Part III, Hour 7 |
| Minimize collection or verify retention/deletion scope | Part III, Hours 8–9 |
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

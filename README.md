# experimently-docs

Internal Experimently documentation kept **out of the app repo**
(`amarkanday/experimentation-platform`) — onboarding, learning paths, troubleshooting and
usage guides for engineers joining the platform.

Experimently is an A/B-testing and feature-flag platform: a FastAPI backend (Python 3.11,
SQLAlchemy, Pydantic v2, PostgreSQL, Redis), a Next.js dashboard (TypeScript, React,
Tailwind), fifteen client SDKs that all speak one HTTP contract, AWS infrastructure defined in
CDK, and a statistics engine (frequentist, sequential/mSPRT, CUPED, Bayesian, multi-armed
bandits). ~66,000 lines of application Python, ~112,000 lines of backend tests (5,800+ test
functions), ~25,000 lines of dashboard TypeScript, ~38,000 lines of SDK code.

## Guides — read these first

- **[`guides/onboarding.md`](./guides/onboarding.md)** — your first day and first week: accounts,
  the clone-to-running-dashboard checklist, how tests and PRs work here, and which book to
  read next.
- **[`guides/usage.md`](./guides/usage.md)** — how to _use_ the platform: create an experiment,
  target it, ship a flag with a gradual rollout and a safety net, read the results, integrate an
  SDK.
- **[`guides/troubleshooting.md`](./guides/troubleshooting.md)** — symptom → cause → fix, for
  local setup, tests, the API, the dashboard, SDKs, schedulers and AWS.

## Learning path

A **seven-book learning path** that takes a new engineer from zero to extending the platform —
taught **through the real Experimently codebase**, in the O'Reilly/Sams "in N Hours/Days"
format. A **core path** of four books goes from absolute zero to understanding and extending
the whole system; then **three specializations** go deep on the subsystems that make the
product what it is.

**Core path — read in order:**

1. **[`learn-python-in-24-hours/`](./learn-python-in-24-hours/README.md)** — Python from
   absolute zero to the modern, typed Python this backend is written in (start here if you
   don't know Python).
2. **[`learn-fastapi-in-24-hours/`](./learn-fastapi-in-24-hours/README.md)** — the backend
   framework layer: FastAPI, Pydantic v2, dependency injection, SQLAlchemy 2, Alembic, the
   middleware stack, background schedulers, and testing with `TestClient`. Assumes the Python
   book.
3. **[`learn-react-nextjs-in-24-hours/`](./learn-react-nextjs-in-24-hours/README.md)** —
   TypeScript, React and Next.js from the ground up through the real dashboard: the API
   client, `AuthContext`, the pages router, forms, the targeting-rule builder, Recharts results,
   Jest and Playwright. The book that lets you _write_ screens, not just read them.
4. **[`build-a-platform-in-28-days/`](./build-a-platform-in-28-days/README.md)** — the **whole
   Experimently architecture** end-to-end: the assign → track → analyse loop, the data model,
   auth and RBAC, feature flags and the rules engine, the five background schedulers and their
   advisory lock, safety auto-rollback, the statistics engine, the SDK contract, testing, CI,
   containers, AWS. Assumes the three prior books. _After this capstone, you can read and
   extend the codebase._

**Specializations — after the core path, in any order:**

5. **[`experimentation-statistics/`](./experimentation-statistics/README.md)** — the maths the
   product is built on and how the code implements it: SRM, z-tests and Fisher, power, the
   peeking problem and mSPRT, CUPED, Beta-Binomial Bayesian analysis, Thompson Sampling and
   UCB1 bandits, multiple-testing correction, and reproducible Monte Carlo (seeded RNG).
6. **[`sdk-engineering/`](./sdk-engineering/README.md)** — one HTTP contract, fifteen SDKs:
   the four endpoints, why the server (not the SDK) buckets users, the MD5 consistent hash and
   its golden vectors, the anatomy of a client (transport, cache, 429 retry), the React SDK,
   porting to new languages, the live contract harness, and the ShopLab/StreamPulse demo apps.
7. **[`platform-operations/`](./platform-operations/README.md)** — running it: the compose
   stack and container entrypoint, bootstrap and seeds, the CDK stacks (VPC, Aurora, Redis,
   ECS Fargate + ALB + blue/green, Lambda + Kinesis + Firehose + OpenSearch, Glue),
   structured logging, Prometheus and health probes, alerting, the CI workflows, migrations
   and rollback.

## Conventions used across every book

- Code blocks tagged `# 📁 path/to/file.py` (or `// 📁 path/to/file.ts`) are real, current
  Experimently source, quoted from the repository. Blocks with no path are illustrative.
- `# ✅` marks the idiom we want; `# ❌` marks the anti-pattern it replaces.
- Sidebars — **By the Way**, **Watch Out!**, **Did You Know?** — run throughout.
- Every Part ends with a **Workshop** (Q&A, a quiz, exercises with worked solutions).
- You need a clone of `amarkanday/experimentation-platform` open alongside the books; the
  _Try It Yourself_ / _Build It_ exercises send you into it. Files move — if a path 404s,
  `git grep` the symbol. The _patterns_ outlast the line numbers.

Snapshot date for every quoted snippet: **2026-09-11** (`main` at PR #68 plus the P0 open-core
work: local auth, containers, the Community Edition compose stack).

> Private repository. Not for external distribution.

# Build an Experimentation Platform in 28 Days

### _The Controlvar Edition — How a Real A/B-Testing & Feature-Flag Platform Is Actually Built_

> **Preface.** You've learned [Python](../learn-python-in-24-hours/README.md),
> [FastAPI](../learn-fastapi-in-24-hours/README.md) and
> [React/Next.js](../learn-react-nextjs-in-24-hours/README.md). You can read the code. This
> book answers the bigger question: **how do all the pieces fit together to make a real
> platform?** It's a guided tour of the _entire_ Controlvar stack — a FastAPI backend that
> assigns users to variants and evaluates feature flags, a statistics engine that turns events
> into decisions, five background schedulers that keep experiments and rollouts moving, a
> Next.js dashboard, sixteen SDKs speaking one contract, and AWS infrastructure in CDK —
> explaining not just _what_ each technology is, but _how Controlvar uses it and why._ By
> Day 28 you'll understand the system well enough to extend it.

---

## Why 28 days (and not 7)

A todo app fits in a weekend. **Controlvar is a production system** — a core backend with
36 API endpoint modules, a set of optional modules beside it, five schedulers, a stats
engine, a Next.js dashboard, sixteen SDKs (13 of them run against a live backend in CI), and 20
required status checks on every pull request. Teaching it honestly takes four weeks, one theme
each:

- **Week 1 — The Core Loop.** Assign a user, track an event, compute a result. The domain
  model, deterministic hashing, conversion matching, and the statistics that turn counts into a
  verdict. _What the product fundamentally does._
- **Week 2 — The API and the Data.** The FastAPI application and its middleware stack,
  configuration and environments, authentication (local JWT, Cognito, API keys), RBAC, the
  SQLAlchemy models and their PostgreSQL schema, Alembic and the bootstrap. _Where requests go
  and where data lives._
- **Week 3 — The Hard Parts.** Feature-flag evaluation and gradual rollouts, the Enhanced
  Rules Engine and the targeting adapter, the five background schedulers and the advisory
  lock that keeps replicas from racing, safety monitoring with automatic rollback, the
  advanced statistics (sequential, CUPED, Bayesian, bandits), and the SDK contract, including
  local flag evaluation from `/api/v1/sdk/ruleset` in the JavaScript and Python SDKs (beta) and
  the warehouse module (beta). _The stuff that makes it special._
- **Week 4 — Production Engineering.** The testing pyramid (unit, integration, smoke, contract,
  E2E), the dashboard's architecture, the CI workflows, containers and compose, the AWS CDK
  stacks, deployment (signed public `core`/`full` images on GHCR, the production
  Compose file and the AWS deploy workflow), and observability. _What makes it
  shippable._

Each **Day** is a full chapter-length lesson (set aside ~2–3 hours) with the same rhythm:

1. **What & why** — the technology, the problem it solves, and the _architecture reasoning_
   (the tradeoffs, and the alternatives the team rejected).
2. **How Controlvar does it** — the real subsystem walked thoroughly, with file paths and
   verified `# 📁` code/config.
3. **Build It** — a concrete exercise (often: find/trace/modify something in the repo).
4. **Key takeaways**, then a **Q&A** that pressure-tests what you just read.

Sams-style sidebars — **By the Way**, **Watch Out!**, **Did You Know?** — run throughout.
Each **Week** ends with a **Capstone** that traces one real feature end-to-end, then a
**Workshop**: a quiz (with answers) and exercises (with worked solutions) to lock it in.

> **Convention.** `# 📁 path` = real Controlvar source at commit `09d815ea` (`main` after
> v0.43.2). Snippets are sometimes trimmed for teaching (a `...` marks what was left out, and a
> long signature may be joined onto one line); the file path lets you read the full version.
> You'll need a clone of the [`Controlvar/controlvar`](https://github.com/Controlvar/controlvar)
> repository open alongside this book, checked out at `09d815ea` if you want every line to
> match — the _Build It_ exercises send you into it. Paths are relative to the repository root.
> Controlvar was called Experimently before 0.35.0; you will still meet the old name in some
> identifiers (`experimently.token`, `EXPERIMENTLY_PROFILE`, the `experimentation` database
> schema), and the text says so where it matters.

---

## The whole system on one page

Before Day 1, here's the map. Keep it handy; every Day fills in one box.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  CLIENTS                                                                      │
│  Your app + an SDK (16 of them)            Dashboard (Next.js, :3100/:3000)   │
│   X-API-Key                                 Authorization: Bearer <JWT>       │
└──────────┬───────────────────────────────────────────┬───────────────────────┘
           │ POST /tracking/assign  POST /tracking/track │ /experiments /feature-flags
           │ GET  /feature-flags/evaluate/{key}          │ /results /admin ... /ws
           │ GET  /sdk/ruleset (local flag eval, beta)   │
┌──────────▼───────────────────────────────────────────▼───────────────────────┐
│  FASTAPI  backend/app/main.py                                                 │
│   middleware, outermost first: CORS → RequestID → Prometheus →                │
│     SecurityHeaders → TrustedHost → RateLimit → RelativeSlashRedirect →       │
│     RequestBodyLimit → UnhandledError                                         │
│   api/deps.py: get_db · get_current_user · get_api_key · RBAC                 │
│   api/v1/endpoints/*.py  →  services/*.py  →  models/*.py (SQLAlchemy)        │
│   + module routers from modules/ (full profile only)                          │
│   lifespan: 5 schedulers (experiment, rollout, metrics, safety, bandit)       │
│             each tick under a PostgreSQL advisory lock                        │
└──────┬────────────────────────┬──────────────────────────────┬───────────────┘
       │                        │                              │
┌──────▼──────────┐   ┌─────────▼──────────┐   ┌───────────────▼──────────────┐
│ POSTGRESQL      │   │ REDIS (optional)   │   │ AWS (optional)               │
│ schema          │   │ rate limits        │   │ ECS Fargate, Aurora, Cognito │
│ experimentation │   │ (in-memory         │   │ full profile adds: Kinesis → │
│ experiments,    │   │  fallback),        │   │ OpenSearch, DynamoDB         │
│ variants,       │   │ results cache      │   │ counters, Glue/Athena        │
│ assignments,    │   └────────────────────┘   └──────────────────────────────┘
│ events, flags…  │
└─────────────────┘
```

**One codebase, two profiles.** Everything under `backend/` and `frontend/` is the **core**
profile and works on its own. The **full** profile adds the optional modules under `modules/`
(workspaces, HIPAA, compliance reports, SSO, RBAC administration, the warehouse, integrations,
real-time counters, ETL and split-URL tests); `modules-manifest.txt` is the authority on what
belongs there, and `scripts/core_build.sh` proves the core still builds and passes its tests
with `modules/` deleted. `GET /api/v1/modules` reports which profile is serving. A full checkout
also has **two Alembic heads** (the core chain and the `modules` branch), so the commands are
`upgrade heads` and `stamp heads`, and the two heads are never merged. Both profiles are
Apache-2.0 (the SDKs are MIT).

The two arrows that matter most run through the middle box. **Assign** (an SDK asks "which
variant is user X in?") is decided on the server by `bucket_of(user_id, experiment.key)`: the
first four bytes of `MD5("{user_id}:{experiment.key}")` read as a little-endian unsigned
integer and divided by 2³², the same function every SDK implements, and stored as a sticky
`assignments` row. **Track** ("user X did `purchase`") writes an `events` row tagged with the
user's variant while the experiment is running. The results engine then counts **converting
users** — each assigned user who sent at least one matching event counts once — and, for a
revenue, count, duration or custom metric, takes the **mean per assigned user**, a user with no
matching event counting as 0. Everything else — flags, rollouts, safety, statistics,
dashboards — is built around those two rows.

---

## Table of Contents

### Week 1 — The Core Loop _(Days 1–7)_ → [`week-1-core-loop.md`](./week-1-core-loop.md)

1. The big picture: Controlvar's architecture and the "0% conversions" bug
2. The repo and the dev loop: venv, PostgreSQL, uvicorn, the dashboard
3. The domain model: experiments, variants, metrics, assignments, events
4. Assignment: deterministic hashing and sticky rows
5. Event tracking and conversion matching
6. Results: from counts to a verdict
7. **Capstone:** trace `assign → track → results` end-to-end

### Week 2 — The API and the Data _(Days 8–14)_ → [`week-2-api-and-data.md`](./week-2-api-and-data.md)

8. The FastAPI application: routers, the middleware stack, the lifespan
9. Configuration: `Settings`, environments, and the fail-closed validators
10. Authentication: local JWTs, Cognito, API keys, and the dev bypass
11. Authorization: RBAC, ownership, and the permission dependencies
12. The SQLAlchemy models and the PostgreSQL schema
13. Alembic migrations and the bootstrap
14. **Capstone:** add a field end-to-end (model → migration → schema → endpoint → dashboard)

### Week 3 — The Hard Parts _(Days 15–21)_ → [`week-3-hard-parts.md`](./week-3-hard-parts.md)

15. Feature flags: evaluation, rollout percentages, and `reason`
16. The Enhanced Rules Engine and the targeting adapter
17. Background schedulers and the advisory lock
18. Safety monitoring and automatic rollback
19. The statistics engine: sequential testing, CUPED, Bayesian, bandits
20. SDKs and the endpoint contract
21. **Capstone:** trace StreamPulse's crash spike to an automatic rollback

### Week 4 — Production Engineering _(Days 22–28)_ → [`week-4-production.md`](./week-4-production.md)

22. Testing strategy: the pyramid, the harness, and the hot-path lesson
23. The dashboard: Next.js architecture and the API client
24. CI/CD: the 20 required checks, the release flow, and the security scans
25. Containers and the compose stack
26. AWS: the CDK stacks, ECS Fargate, the one-path deploy, and the full-profile data stacks
27. Observability: structured logs, Prometheus, health probes, alerting
28. **Capstone:** the whole picture — you can build Controlvar

---

## Where this fits in the curriculum

| Book                                             | Teaches                      | You leave knowing                                 |
| ------------------------------------------------ | ---------------------------- | ------------------------------------------------- |
| 1. Learn Python in 24 Hours                      | the language                 | how to read the backend                           |
| 2. Learn FastAPI in 24 Hours                     | the framework layer          | how to read & trust the API and data code         |
| 3. Learn TypeScript, React & Next.js in 24 Hours | the dashboard framework      | how to write screens & hooks                      |
| **4. Build a Platform in 28 Days** _(this book)_ | **the architecture**         | **how the whole system works & how to extend it** |

After this capstone, three **specialization** books go deeper on the hardest subsystems:

- **[Experimentation Statistics](../experimentation-statistics/README.md)** — the maths behind
  Days 6 and 19, in depth: SRM, power, mSPRT, CUPED, Bayesian, bandits, multiple testing.
- **[SDK Engineering](../sdk-engineering/README.md)** — Day 20 in depth: the contract, the
  consistent hash, client anatomy, porting to new languages, the live harness.
- **[Platform Operations](../platform-operations/README.md)** — Days 25–27 in depth: compose,
  CDK, Fargate, Kinesis, observability, release engineering.

_Set aside four weeks. Open the repo. Turn to [Week 1](./week-1-core-loop.md). 🏗️_

## A note on accuracy

Verified against Controlvar `main` at `09d815ea` (after v0.43.2). Every `# 📁` excerpt was
copied from the file at that commit, and `tools/` in this repository checks that each cited path
exists and that every identifier in an excerpt occurs in its file. The numbers quoted (the 20
required checks, the 16 SDKs and the 13 that run live, the 2,242 local-evaluation cases, the nine
middleware layers, the test counts) were counted in that tree. The commands in the _Build It_
sections were read against the code, not run for this edition. Re-check the book yourself by
following `tools/README.md`.

## Related reading — Production Engineering

The Production Engineering specialization is pinned to the same Controlvar release.

- [Complete experiment walkthrough](../production-engineering/part-1-experiment-design.md#hour-2--a-complete-experiment-from-hypothesis-to-removal)
- [Distributed correctness](../production-engineering/part-2-reliable-systems.md#hour-4--assignments-transactions-and-retries)
- [Changing Controlvar safely](../production-engineering/part-4-safe-change.md) (Hour 11)

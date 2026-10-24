# Build an Experimentation Platform in 28 Days

### _The Experimently Architecture — How a Real A/B-Testing & Feature-Flag Platform Is Actually Built_

> **Preface.** This book explains how Experimently's components work together. It assumes the Python,
> FastAPI and React/Next.js material from the earlier books, then follows an experiment through
> assignment, event collection, analysis and the dashboard. Later chapters cover targeting,
> schedulers, SDKs, deployment and operations. Each week ends with a practical exercise that
> connects the components discussed in that week.

---

<a id="why-28-days-and-not-7"></a>

## How the book is organized

The book is divided into four weeks, each covering a different area of the platform. The order
starts with the experiment workflow, then adds request handling, advanced features and operations.
The reviewed system includes optional modules, five schedulers, sixteen SDKs and the application
checks discussed in the final week.

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
2. **How Experimently does it** — the real subsystem walked thoroughly, with file paths and
   verified `# 📁` code/config.
3. **Build It** — a concrete exercise (often: find/trace/modify something in the repo).
4. **Key takeaways**, then a **Q&A** that pressure-tests what you just read.

Sidebars provide additional context and point out limitations. Each week ends with a capstone
that traces a feature across components, followed by a workshop with questions, a quiz and worked
exercise solutions.

> **Convention.** `# 📁 path` = real Experimently source. Snippets are sometimes trimmed for
> teaching; the file path lets you read the full version. You'll need a clone of the
> [`getexperimently/experimently`](https://github.com/getexperimently/experimently) repository
> open alongside this book — the _Build It_ exercises send you into it. Paths are relative to
> the repository root.
>
> **Verified against** getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased
> changes, including three breaking ones: #747, #769, #770). This page and Week 1 were checked
> against that commit.

---

## The whole system on one page

The following diagram shows the main components and request paths. Refer to it when tracing the
individual services in the chapters.

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
│     UnhandledError                                                            │
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
user's variant. The results engine then counts **converting users**: each assigned user who
sent at least one matching event counts once. Everything else — flags,
rollouts, safety, statistics, dashboards — is built around those two rows.

---

## Table of Contents

### Week 1 — The Core Loop _(Days 1–7)_ → [`week-1-core-loop.md`](./week-1-core-loop.md)

1. The big picture: Experimently's architecture and the "0% conversions" bug
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
28. **Capstone:** the whole picture — you can build Experimently

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

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Complete experiment walkthrough](../production-engineering/part-1-experiment-design.md#hour-2--a-complete-experiment-from-hypothesis-to-removal)
- [Distributed correctness](../production-engineering/part-2-reliable-systems.md#hour-4--assignments-transactions-and-retries)
- [Changing Experimently safely](../production-engineering/part-4-safe-change.md#hour-11--changing-experimently-safely)

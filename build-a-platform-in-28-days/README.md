# Build an Experimentation Platform in 28 Days

### _The Experimently Architecture — How a Real A/B-Testing & Feature-Flag Platform Is Actually Built_

> **Preface.** You've learned [Python](../learn-python-in-24-hours/README.md),
> [FastAPI](../learn-fastapi-in-24-hours/README.md) and
> [React/Next.js](../learn-react-nextjs-in-24-hours/README.md). You can read the code. This
> book answers the bigger question: **how do all the pieces fit together to make a real
> platform?** It's a guided tour of the _entire_ Experimently stack — a FastAPI backend that
> assigns users to variants and evaluates feature flags, a statistics engine that turns events
> into decisions, five background schedulers that keep experiments and rollouts moving, a
> Next.js dashboard, fifteen SDKs speaking one contract, and AWS infrastructure in CDK —
> explaining not just _what_ each technology is, but _how Experimently uses it and why._ By
> Day 28 you'll understand the system well enough to extend it.

---

## Why 28 days (and not 7)

A todo app fits in a weekend. **Experimently is a production system** — ~66,000 lines of
application Python, 50+ API router modules, five schedulers, a stats engine, a 25,000-line
dashboard, fifteen SDKs, and a CI gate with five required jobs. Teaching it honestly takes four
weeks, one theme each:

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
  advanced statistics (sequential, CUPED, Bayesian, bandits), and the SDK contract. _The stuff
  that makes it special._
- **Week 4 — Production Engineering.** The testing pyramid (unit, integration, smoke, contract,
  E2E), the dashboard's architecture, the CI workflows, containers and compose, the AWS CDK
  stacks, and observability. _What makes it shippable._

Each **Day** is a full chapter-length lesson (set aside ~2–3 hours) with the same rhythm:

1. **What & why** — the technology, the problem it solves, and the _architecture reasoning_
   (the tradeoffs, and the alternatives the team rejected).
2. **How Experimently does it** — the real subsystem walked thoroughly, with file paths and
   verified `# 📁` code/config.
3. **Build It** — a concrete exercise (often: find/trace/modify something in the repo).
4. **Key takeaways**, then a **Q&A** that pressure-tests what you just read.

Sams-style sidebars — **By the Way**, **Watch Out!**, **Did You Know?** — run throughout.
Each **Week** ends with a **Capstone** that traces one real feature end-to-end, then a
**Workshop**: a quiz (with answers) and exercises (with worked solutions) to lock it in.

> **Convention.** `# 📁 path` = real Experimently source. Snippets are sometimes trimmed for
> teaching; the file path lets you read the full version. You'll need a clone of the
> `amarkanday/experimentation-platform` repo open alongside this book — the _Build It_
> exercises send you into it. Paths are relative to the repository root.

---

## The whole system on one page

Before Day 1, here's the map. Keep it handy; every Day fills in one box.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  CLIENTS                                                                      │
│  Your app + an SDK (15 languages)          Dashboard (Next.js, :3100/:3000)   │
│   X-API-Key                                 Authorization: Bearer <JWT>       │
└──────────┬───────────────────────────────────────────┬───────────────────────┘
           │ POST /tracking/assign  POST /tracking/track │ /experiments /feature-flags
           │ GET  /feature-flags/evaluate/{key}          │ /results /admin ... /ws
┌──────────▼───────────────────────────────────────────▼───────────────────────┐
│  FASTAPI  backend/app/main.py                                                 │
│   middleware: RequestID → Prometheus → RateLimit → SecurityHeaders → CORS     │
│   api/deps.py: get_db · get_current_user · get_api_key · RBAC                 │
│   api/v1/endpoints/*.py  →  services/*.py  →  models/*.py (SQLAlchemy)        │
│   lifespan: 5 schedulers (experiment, rollout, metrics, safety, bandit)       │
│             each tick under a PostgreSQL advisory lock                        │
└──────┬────────────────────────┬──────────────────────────────┬───────────────┘
       │                        │                              │
┌──────▼──────────┐   ┌─────────▼──────────┐   ┌───────────────▼──────────────┐
│ POSTGRESQL      │   │ REDIS              │   │ AWS (optional in CE)         │
│ schema          │   │ rate limits,       │   │ Lambda assignment / flags    │
│ experimentation │   │ results cache,     │   │ Kinesis → Lambda → OpenSearch│
│ experiments,    │   │ flag cache         │   │ DynamoDB counters, Cognito   │
│ variants,       │   └────────────────────┘   │ Glue/Athena data lake        │
│ assignments,    │                            └──────────────────────────────┘
│ events, flags…  │
└─────────────────┘
```

The two arrows that matter most run through the middle box. **Assign** (an SDK asks "which
variant is user X in?") is decided on the server by an MD5 hash of `user_id:experiment_id`,
stored as a sticky `assignments` row. **Track** ("user X did `purchase`") writes an `events`
row that the results engine later joins back to that assignment. Everything else — flags,
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
24. CI/CD: the PR QA gate, the release gate, and the security scan
25. Containers and the compose stack
26. AWS: the CDK stacks, ECS Fargate, Lambda, Kinesis, DynamoDB
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
  CDK, Fargate, Lambda/Kinesis, observability, release engineering.

_Set aside four weeks. Open the repo. Turn to [Week 1](./week-1-core-loop.md). 🏗️_

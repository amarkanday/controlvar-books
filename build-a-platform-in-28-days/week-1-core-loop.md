# Week 1 — The Core Loop

### Days 1–7 · Assign, Track, Analyse

[← Contents](./README.md) · [Week 2 →](./week-2-api-and-data.md)

This week is the product's heartbeat: how a user gets a variant, how what they do afterwards is
recorded, and how those two facts become a number a product manager can act on. By Day 7 you'll
trace one experiment from an SDK call all the way to a p-value on the dashboard.

You already know the language (the Python book) and the framework (the FastAPI book). This week
is about **architecture** — not "what is a SQLAlchemy model" but "why is an assignment a row in
PostgreSQL rather than a cookie, and what breaks if you change that." Every Day grounds its lesson
in real files you can open in the `amarkanday/experimentation-platform` repo. Read them alongside
the prose; the file paths marked `# 📁` are real source, quoted exactly, and the whole point is
that you can go verify them.

> **The one sentence to carry through Week 1.** _An experiment is two tables — `assignments`
> (who saw what) and `events` (what they did) — joined by `(experiment_id, user_id)`; assignment
> is a deterministic hash so the join is stable, tracking is append-only so the join is honest,
> and every statistic on the platform is a query over that join._ Once you see the join, the
> rest of the codebase is machinery for filling those two tables correctly and reading them
> rigorously.

A map of the week, so each piece has somewhere to land as it arrives:

| Day | Topic                            | The one thing it's really about                                  |
| --- | -------------------------------- | ---------------------------------------------------------------- |
| 1   | The big picture                  | The shape of the system, and the bug that shows why shape matters |
| 2   | The repo and the dev loop        | Getting from clone to a running API + dashboard, and staying there |
| 3   | The domain model                 | Five tables, and the constraints that make them trustworthy       |
| 4   | Assignment                       | `md5(user_id:experiment_id) % 100` and why the row is sticky      |
| 5   | Tracking & conversion matching   | Append-only events, and the one helper every analysis must use    |
| 6   | Results                          | From two `COUNT(*)` queries to a verdict, with honesty checks      |
| 7   | **Capstone:** end-to-end trace   | One experiment through every layer above                          |

Notice the arc: Days 1–2 set the stage (system shape, tooling), Days 3–6 are the four pillars
of the core loop (model, assign, track, analyse), and Day 7 walks one real experiment through
all four at once. Don't skip Day 1 even though it has little code — the mental map it builds is
what makes every later file make sense.

---

## Day 1 — The big picture: Experimently's architecture and the "0% conversions" bug

### What & why

Before any code, hold the shape of the system in your head. Experimently is a **client-server
platform with two kinds of client**:

- **Your application**, through an **SDK** (Python, JavaScript, React, Go, Java, Swift, Kotlin,
  Ruby, PHP, .NET, Elixir, Flutter, React Native, an edge runtime, an OpenFeature provider).
  It authenticates with an **API key** and asks three questions: _which variant is this user
  in?_, _is this flag on for this user?_, _record that this user did X._
- **The dashboard**, a Next.js app that product managers and engineers use to create
  experiments and flags, watch results, and administer the platform. It authenticates with a
  **bearer token** (a local JWT in the Community Edition, a Cognito token in the enterprise
  deployment).

Both talk HTTP to one **FastAPI** application. Behind it sit **PostgreSQL** (the system of
record: experiments, variants, assignments, events, flags, users) and **Redis** (rate-limit
counters, a results cache). In the AWS deployment there are also **Lambda** functions for the
lowest-latency paths, a **Kinesis → Lambda → OpenSearch** analytics pipeline, and **DynamoDB**
counters — but the Community Edition runs fully without them, and this week is about the core
that runs everywhere.

Data flows like this: an SDK calls `POST /api/v1/tracking/assign` → the API hashes the user
into a variant and writes an **`assignments`** row → the SDK later calls
`POST /api/v1/tracking/track` → the API writes an **`events`** row → the dashboard calls
`GET /api/v1/results/{experiment_id}` → the **results engine** joins the two tables per
variant, runs a statistical test, and returns a verdict. You saw the one-page diagram in the
[README](./README.md) — that's the whole book in one picture.

#### Why the server decides, and why PostgreSQL holds the answer

The "obvious" alternative is a fully client-side design: ship the experiment configuration to
the SDK, hash the user locally, never store an assignment at all. It's how several commercial
flag products work, and it is genuinely faster — no round trip. Experimently rejected it, and
the reasons are the ones that shape Week 1:

1. **One source of truth for "who saw what."** If fifteen SDKs each bucket locally, fifteen
   hash implementations must agree bit-for-bit forever, and any drift silently corrupts every
   experiment. When the server decides, there is exactly one implementation
   (`AssignmentService._hash_user_to_variant`) and one stored answer. The SDKs still ship a
   consistent-hash utility — with golden vectors that pin it across languages — but, as the
   SDK docs put it, _"nothing in the client uses it to decide a variant."_
2. **Eligibility is a server-side question.** Global holdouts, mutual-exclusion groups and
   targeting rules all need data the client doesn't have (other experiments, the holdout
   configuration, the rules themselves). Day 4 walks that gate; it only works because
   assignment is centralised.
3. **The analysis needs the row.** A p-value is computed from _counts of assigned users_ per
   variant. If assignment lived only in a client's memory, you'd have to infer it from
   exposure events — and users who were assigned but never fired an exposure vanish from the
   denominator. A stored `assignments` row makes the denominator exact.

The cost is real: every first-time assignment is a network call and a database write. The
platform pays it with caching in the SDKs (per user + key), a much higher rate limit on SDK
paths (Day 8), and — in the AWS deployment — a Lambda + DynamoDB fast path. Hold that
tradeoff; it recurs whenever you're tempted to "just cache it on the client."

#### The bug that justifies the week

Abstract architecture is forgettable. Here is a concrete bug that actually shipped in this
codebase, and it justifies the whole "two tables, one join" way of thinking.

In September 2026 the team built **ShopLab**, a demo storefront that runs five experiments
through the real React SDK and the public tracking API. The moment real SDK traffic flowed,
**every experiment showed 0% conversion**. Assignments were fine — users got variants, the
counts per variant looked right. Events were arriving — the `events` table filled up with
`purchase` rows. But the results engine reported zero conversions for every variant.

The cause was one string. The results engine — every analysis path in it — selected
conversions with `Event.event_type == "conversion"`. That convention came from an internal
helper, `EventService.track_conversion`, which does write `event_type="conversion"`. But the
public tracking API, and therefore every SDK, stores **the event's own name** in `event_type`:
a purchase is `event_type="purchase", event_name="purchase"`. The join between `assignments`
and `events` was correct; the _filter_ on the events side matched nothing. Two producers, two
conventions, one consumer that only knew about one of them.

The fix (PR #65) is a small module that is now mandatory on every analysis path:

```python
# 📁 backend/app/services/event_matching.py
def conversion_event_filter(event_name: Optional[str]):
    """
    SQLAlchemy criterion selecting the conversion events of one metric.

    With a metric ``event_name`` the criterion is
    ``Event.event_name == event_name AND event_type not an exposure``.
    Without one (experiment has no metric rows) it falls back to the legacy
    ``event_type == "conversion"`` convention.
    """
    if not event_name:
        return Event.event_type == EventType.CONVERSION.value
    return and_(
        Event.event_name == event_name,
        Event.event_type.notin_(EXPOSURE_EVENT_TYPES),
    )
```

Read that as a design note, not an apology. It names the exact seam where two producers meet
one consumer and commits to one rule: **a metric matches on `event_name`; `event_type` only
excludes exposures.** `CLAUDE.md` now says it in bold: _every analysis path must match
conversions with `event_matching.py`, never `Event.event_type == "conversion"`._ Day 5 is
about that seam. The lesson for Day 1 is broader: in this system, correctness lives in the
_join_ and the _filters_ between two tables, and the platform's most embarrassing bug was a
filter, not a formula.

> **Did You Know?** ShopLab surfaced four more platform bugs in the same PR: the bandit
> scheduler called a DynamoDB method that didn't exist (so bandits never learned), nothing
> started that scheduler at all, the rollout scheduler compared naive database timestamps with
> an aware `datetime.now()` and raised on every active schedule, and `CORS_ORIGINS` could not
> be set as the documented comma-separated string. None were caught by the 5,000+ existing
> tests, because those tests mocked the seams. You'll meet each bug on the Day that owns it;
> together they are the best argument in this book for driving the platform through its
> _public_ API in tests (Day 22).

### How Experimently does it

The top-level folders map directly onto the architecture:

```
experimentation-platform/
├── backend/
│   ├── app/
│   │   ├── main.py            # the FastAPI app, middleware, lifespan         ← Week 2
│   │   ├── api/
│   │   │   ├── api.py         # router aggregation (50+ routers)             ← Week 2
│   │   │   ├── deps.py        # get_db, auth, API keys, permissions          ← Week 2
│   │   │   └── v1/endpoints/  # one module per resource                     ← Weeks 1–3
│   │   ├── core/              # config, security, permissions, rules engine,
│   │   │                      # schedulers, scheduler_lock, stats_engine     ← Weeks 2–3
│   │   ├── services/          # business logic (assignment, analysis, ...)  ← Weeks 1–3
│   │   ├── models/            # SQLAlchemy tables                            ← Week 1–2
│   │   ├── schemas/           # Pydantic request/response models            ← Week 2
│   │   ├── middleware/        # rate limiter, security headers, logging     ← Week 2
│   │   └── db/                # session, alembic migrations, bootstrap      ← Week 2
│   ├── lambda/                # assignment / event_processor / flag Lambdas ← Week 4
│   ├── scripts/               # seeds (demo, shoplab, streampulse, sdk-contract)
│   └── tests/                 # unit / integration / smoke / contract        ← Week 4
├── frontend/                  # Next.js dashboard                            ← Week 4
├── sdk/                       # 15 SDKs                                      ← Week 3
├── demo/                      # ShopLab (:3200), StreamPulse (:3300)         ← Week 3
├── infrastructure/cdk/        # AWS CDK stacks (Python)                      ← Week 4
├── tests/sdk-contract/        # golden vectors + live contract harness       ← Week 4
├── docker-compose.yml         # postgres + redis + api + dashboard (+demo)   ← Week 4
└── CLAUDE.md                  # the team's working agreements
```

The key insight: **the backend is a single Python package, `backend.app`, and everything
imports it by that full path.** Not `app.models`, not a relative `..services`. The rule is in
`CLAUDE.md` and it isn't style — SQLAlchemy registers a class under the module path it was
imported from, and two spellings of the same module produce two distinct classes and a
"Class is not mapped" error at runtime. You'll see the rule enforced everywhere:

```python
# 📁 backend/app/services/assignment_service.py
from backend.app.core.targeting_adapter import (
    expand_context,
    normalise_targeting_rules,
)
from backend.app.models.experiment import Experiment, Variant, ExperimentStatus
from backend.app.models.assignment import Assignment
from backend.app.services.event_service import EventService
```

The second insight is the **layering**: `endpoints` parse and authenticate, `services` hold
the logic, `models` are the tables. An endpoint that talks directly to the ORM is a smell
(you'll find some — `tracking.py` queries `Experiment` by key before calling the service —
but the direction of dependency is always endpoint → service → model, never back).

> **By the Way.** There are roughly fifty router modules under `api/v1/endpoints/`, and most of
> them are _feature epics_ with an ID in a comment (`EP-021` sequential testing, `EP-033`
> compliance audit, `EP-057` workspaces …). `api/api.py` is the table of contents of the
> product: read the `include_router` calls top to bottom and you have the feature list. The
> core loop this week touches only `tracking.py`, `experiments.py`, `results.py` and
> `feature_flags.py`.

### Build It

Clone the repo and just _look_. Map each folder to a box in the README diagram. Don't read
code yet — build the mental map.

```bash
git clone https://github.com/amarkanday/experimentation-platform.git
cd experimentation-platform

# the four layers of the backend this week is about:
ls backend/app/api/v1/endpoints/ backend/app/services/ backend/app/models/ backend/app/schemas/

# the product's table of contents — every include_router is a feature:
grep -n "include_router" backend/app/api/api.py | wc -l
grep -n "include_router" backend/app/api/api.py | head -20

# prove the "one join" claim: every analysis path selects conversions the same way
git grep -n "conversion_event_filter\|any_conversion_event_filter" backend/app | cut -d: -f1 | sort -u
```

You should see the results engine, the results endpoint, the dimensional-analysis service, the
streaming service and the bandit scheduler all importing the same helper. That single
`git grep` is Day 1 in one line: many consumers, one rule for the events side of the join.

Then run the check that the team keeps as a guard against the import-path trap:

```bash
python standardize_metrics_imports.py --check
```

### Key takeaways

- Experimently = one FastAPI app + PostgreSQL (+ Redis), two kinds of client (SDK with an API
  key, dashboard with a bearer token), optional AWS fast paths.
- The core loop is **assign → track → analyse**, and it is two tables joined on
  `(experiment_id, user_id)`.
- The server decides assignments: one hash implementation, server-side eligibility, an exact
  denominator for the statistics.
- The "0% conversions" bug was a _filter_ on the events side of the join; the fix is one
  mandatory helper, `event_matching.py`.
- `backend.app` is the only import spelling; the folder structure _is_ the layering.

### Q&A

**Q. Why not let the SDK bucket users locally and skip the round trip? Wouldn't that be
faster?**
Faster, yes — and it's how some products work. But then "who saw what" would exist only in
client memory, eligibility (holdouts, mutual exclusion, targeting) would need the client to
know about every other experiment, and the analysis would have to reconstruct the denominator
from exposure events. Experimently pays one network call per _first_ assignment (SDKs cache
after that) to get one implementation, one stored answer and an exact sample size.

**Q. The events table has both `event_type` and `event_name`. Which one means what?**
For anything that came through the public API or an SDK: both hold the event's own name
(`"purchase"`). For the internal `track_conversion` helper: `event_type="conversion"` and
`event_name=<metric event name>`. Exposures are `event_type="exposure"`. So: match a metric
on `event_name`; use `event_type` only to exclude exposures. Never write
`event_type == "conversion"` in analysis code — Day 5 explains the helper that encodes this.

**Q. I only care about the dashboard. Do I need to understand the backend?**
Yes, because the dashboard is thin by design: it renders what `GET /results/{id}` returns
and posts what `POST /experiments/` accepts. Every column on the results page is a field the
results engine computed; every validation message on the "new experiment" form mirrors a
Pydantic validator on the server. You can't reason about the screens without knowing what
shapes they consume.

**Q. Where do the Lambda functions fit if the Community Edition doesn't use them?**
They are an alternative front door for the two hottest paths — assignment and flag evaluation
— backed by DynamoDB instead of PostgreSQL, deployed by the CDK stacks in the AWS edition.
They implement the same contract (Week 3, Day 20) and share the hashing rules. Week 4 covers
them; this week you can ignore them.

---

## Day 2 — The repo and the dev loop: venv, PostgreSQL, uvicorn, the dashboard

### What & why

A backend you can't run is a backend you can't learn. Today is about the **edit → run → see
it** loop for both halves of the system and the small number of environment facts that,
if wrong, produce the most confusing failures in the repo.

The backend needs three things: a **Python 3.11 virtual environment** with the pinned
dependencies, a **PostgreSQL** server on `localhost:5432`, and a handful of **environment
variables** that pick the schema and the auth mode. The dashboard needs Node 22 (`.nvmrc`) and a backend
to talk to. Redis is optional — the rate limiter and caches degrade gracefully without it.

#### Why a virtualenv, and why the tests care so much

The pinned dependency set (`backend/requirements.txt`) is exact — `fastapi==0.141.1`,
`pydantic==2.13.5`, `SQLAlchemy==2.0.52`, `alembic==1.19.2`, `scipy==1.17.1` — because several
of those libraries changed behaviour across minor versions in ways this codebase depends on
(FastAPI 0.141 changed how `app.routes` exposes included routers; the smoke tests had to
adapt). A global Python with different versions will _mostly_ work and then fail in a way that
looks like your bug. `CLAUDE.md` says it in capitals: **always activate the virtualenv first.**

#### Why the schema name is an environment variable

Every table lives in a PostgreSQL _schema_ (a namespace), not the default `public`. The
application uses `experimentation`; the test suite uses `test_experimentation`, in a
per-process database named `experimentation_test_<pid>`. The choice is made at import time
from `APP_ENV`/`TESTING`:

```python
# 📁 backend/app/core/database_config.py
def get_schema_name() -> str:
    """Get the schema name based on the environment."""
    # Check for truthy values, not just specific strings
    app_env = os.environ.get("APP_ENV", "")
    testing = os.environ.get("TESTING", "")

    # Only use test schema if explicitly set to test values
    if app_env == "test" or testing == "true":
        schema_name = "test_experimentation"
    else:
        schema_name = "experimentation"
```

This matters because SQLAlchemy bakes the schema into every table and foreign key at class
definition time (`ForeignKey(f"{get_schema_name()}.users.id")`). Change the variable after
the models are imported and nothing moves. That is why the test `conftest.py` sets
`APP_ENV=test` and `TESTING=true` **before** importing the app, and why running the app
against the test schema (or vice versa) produces "relation does not exist" errors that look
like a missing migration.

> **Watch Out!** The canonical environment variable is `ENVIRONMENT`
> (`development|test|staging|production`); `APP_ENV` is the legacy spelling and still honoured.
> `core/config.py` resolves them, warns when they disagree, and mirrors the legacy value back
> into `os.environ["APP_ENV"]` for the modules that still read it directly (schema selection,
> the rate limiter, the bandit scheduler). If you export `ENVIRONMENT=development` in your
> shell and then run the tests, the root `conftest.py` deliberately pops it so `APP_ENV=test`
> wins. Know both names; prefer `ENVIRONMENT` in anything new.

### How Experimently does it

The everyday commands, from the repository root:

```bash
# one-time
python3.11 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# PostgreSQL (and Redis) via the compose file — or your own local Postgres on 5432
docker compose up -d postgres redis

# create the schema from the models and stamp alembic head (fresh DB)
export POSTGRES_DB=experimentation POSTGRES_SCHEMA=experimentation
python -m backend.app.db.bootstrap

# run the API (from the repo root — the package is backend.app)
uvicorn backend.app.main:app --reload --port 8000

# run the dashboard
cd frontend && npm install && npm run dev      # http://localhost:3100
```

The repository's `Makefile` wraps the same steps — `make venv`, `make db` (Postgres + Redis
via compose), `make bootstrap`, `make dev` (API with reload), `make web` (dashboard),
`make test` — and its header states the one rule the whole toolchain rests on: _"everything
runs from the repository root; the backend is imported as `backend.app.*`."_

Three things in that sequence deserve a closer look.

**The bootstrap, not `alembic upgrade head`, for a fresh database.** The migration chain under
`backend/app/db/migrations/versions/` was autogenerated over many months against databases
that already had tables, and it cannot be replayed from zero (several revisions re-create
tables earlier revisions already created). The team adopted the Alembic cookbook's answer:

```python
# 📁 backend/app/db/bootstrap.py
"""
Bring a PostgreSQL database up to the current schema.

Two situations are handled:

* **Existing database** (has an ``alembic_version`` table): run
  ``alembic upgrade head`` so pending incremental migrations are applied.

* **Fresh database** (no ``alembic_version`` table): create the schema from
  the SQLAlchemy models with ``Base.metadata.create_all`` and then
  ``alembic stamp head``.
```

It also creates the first admin from `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD` when the
`users` table is empty, so a fresh Community Edition can sign in with `AUTH_PROVIDER=local`
without a seed script. The models are the source of truth for the schema — exactly as they are
for the test suite, which also uses `create_all`.

**`uvicorn backend.app.main:app` from the root.** The app imports itself as `backend.app.*`,
so the working directory must be the repo root (or `backend/` must be importable as a package
from wherever you are). The container image does the same: `WORKDIR /app`, then the same
uvicorn command.

**The dashboard's API base.** `frontend/src/services/api.ts` prefixes every request with
`NEXT_PUBLIC_API_URL`; when it's unset in development it falls back to `http://localhost:8000`,
so a fresh clone works against a local uvicorn. In the container image the variable is empty —
same-origin — and nginx proxies `/api/` to the API container.

```typescript
// 📁 frontend/src/services/api.ts
export function apiBase(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL;
  if (raw === undefined || raw === null) {
    return process.env.NODE_ENV === 'development' ? DEV_API_URL : '';
  }
  return raw.replace(/\/+$/, '');
}
```

#### The two ways to log in during development

With `AUTH_PROVIDER=local` (the default) the bootstrap's first admin can sign in at
`/login` with `FIRST_SUPERUSER`/`FIRST_SUPERUSER_PASSWORD` (compose defaults:
`admin@demo.com` / `Demo1234!`). For scripting against the API there is also a **dev bypass**:
`DEV_AUTH_BYPASS=true` serves every request as a synthetic `dev-admin` superuser — but only
when `ENVIRONMENT` is `development` or `test`; the settings validator refuses to start
otherwise (Day 10). Use the bypass for local curl sessions, never for anything reachable from
a network.

> **By the Way.** `./demo/setup-local.sh` does all of the above in one go and adds the two
> demo apps and their traffic simulators. It's the fastest way to see the platform _moving_ —
> dashboards ticking, bandits shifting weights, a rollout advancing — and it's what the
> StreamPulse capstone on Day 21 assumes you have running. `docker compose up -d --wait` is the
> containerised equivalent (postgres + redis + api + dashboard on :3000, seeded with a demo
> admin, three experiments and two flags).

### Build It

Get to a green health check and a logged-in dashboard, then prove the schema claim:

```bash
source venv/bin/activate
curl -s http://localhost:8000/health/ready | python -m json.tool
# → {"status": "ready", "checks": {"database": {"status": "healthy", ...}, "redis": ...}}

# the API docs — every router, every schema
open http://localhost:8000/api/v1/docs

# see the schema selection happen at import time
APP_ENV=test TESTING=true python -c "from backend.app.core.database_config import get_schema_name; print(get_schema_name())"
python -c "from backend.app.core.database_config import get_schema_name; print(get_schema_name())"
```

Then run the smallest meaningful slice of the test suite to confirm the harness works on your
machine (it creates and drops its own `experimentation_test_<pid>` database):

```bash
export APP_ENV=test TESTING=true
python -m pytest backend/tests/unit/core/test_scheduler_lock.py -p no:cov -q
```

### Key takeaways

- Backend = Python 3.11 venv + PostgreSQL on `localhost:5432` + a few env vars; Redis optional.
- The schema name is decided at import time from `APP_ENV`/`TESTING`; set env **before**
  importing the app.
- Fresh database → `python -m backend.app.db.bootstrap` (create_all + stamp); existing →
  it runs `alembic upgrade head` for you.
- Run uvicorn from the repo root as `backend.app.main:app`; the dashboard defaults to
  `http://localhost:8000` in dev.
- `ENVIRONMENT` is canonical, `APP_ENV` legacy; the dev bypass only exists in
  development/test.

### Q&A

**Q. I ran `alembic upgrade head` on an empty database and it failed halfway. Is the
migration chain broken?**
It's not replayable from zero, by history rather than by design — that's exactly why the
bootstrap exists. Use `python -m backend.app.db.bootstrap`. On an existing database it
_does_ run `alembic upgrade head`, so incremental migrations still work the normal way.

**Q. My tests can't find tables that definitely exist. I can see them in `psql`.**
Check the schema. Tests run in `test_experimentation` inside a per-process database; you're
probably looking at `experimentation`. Also check that `APP_ENV`/`TESTING` were set before
anything imported `backend.app` — the root conftest does that, but an ad-hoc script must too.

**Q. Do I need Redis locally?**
No. The rate limiter falls back to an in-memory sliding window, the results cache is
best-effort, and `/health/ready` only fails on Redis when `REDIS_REQUIRED=true`. You'll want it
when you're working on rate limits or caching specifically.

**Q. Why port 3100 for `npm run dev` when the container serves the dashboard on 3000?**
`next dev -p 3100` keeps the dev server clear of the compose stack's :3000, so you can run
both. The API's CORS defaults include 3000, 3001, 3100, 3200 (ShopLab), 3300 (StreamPulse).

---

## Day 3 — The domain model: experiments, variants, metrics, assignments, events

### What & why

Every experimentation platform has the same five nouns. Experimently's are five tables in the
`experimentation` schema, and today is about their _shape_ and, more importantly, their
_constraints_ — because in this system the database is where invariants are enforced, not
just where data is kept.

- An **experiment** is the unit of decision: a name, a stable `key` for the SDKs, a status
  (`draft → active → paused/completed → archived`), an owner, optional targeting rules and
  optional configuration for the advanced modes (sequential testing, CUPED, Bayesian, bandit,
  split-URL, mutual-exclusion group, workspace).
- A **variant** is one arm of the experiment: a name, `is_control`, a `traffic_allocation`
  percentage, and a free-form `configuration` JSON that the SDK hands back to your app
  (`{"button_color": "blue"}`).
- A **metric** says what to measure: an `event_name` to match, a `metric_type`
  (conversion/revenue/count/duration/custom), whether it's `is_primary`, and a
  `minimum_sample_size`.
- An **assignment** is the fact "user X is in variant V of experiment E" — one row per
  `(experiment, user)`, created once, never updated.
- An **event** is the fact "user X did `event_name` (with `value`) at time T", optionally tied
  to an experiment and/or a feature flag, and — if the user had an assignment when the event
  was tracked — denormalised with the `variant_id`.

#### Why constraints live in the database

You could enforce "traffic allocations sum to 100" or "one assignment per user per
experiment" in Python and hope every code path remembers. Experimently enforces the ones that
_must_ hold in PostgreSQL, where they hold regardless of which service, script, seed or
migration wrote the row:

- `CheckConstraint("traffic_allocation >= 0 AND traffic_allocation <= 100")` on variants.
- A **unique index** on `(experiment_id, user_id)` for assignments — the sticky guarantee.
- `CheckConstraint("end_date IS NULL OR start_date IS NULL OR end_date > start_date")` on
  experiments.
- A unique index on `(experiment_id, name)` for metrics.

The Pydantic layer (Week 2) enforces the _friendlier_ rules earlier — "at least one control",
"allocations sum to 100" — so the user gets a 422 with a message instead of a 500 from a
constraint violation. Two layers, deliberately: the schema layer for good errors, the database
for guarantees.

### How Experimently does it

Every table inherits two things from `models/base.py`: a UUID primary key with timestamps, and
a `to_dict()` helper.

```python
# 📁 backend/app/models/base.py
class BaseModel:
    """
    Abstract base model for common columns and methods.
    This is a mixin, not a table itself.
    """

    # Include common columns
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
```

UUIDs rather than serial integers is a deliberate choice: SDKs and dashboards pass ids around
in URLs and JSON, seeds and tests generate ids without coordinating with the database, and
nothing in the system ever needs ids to be small or ordered. (Note `datetime.utcnow` — naive
UTC timestamps. That choice has consequences; Day 17 has the story.)

The experiment table carries the whole feature history in its columns. Trimmed to the core:

```python
# 📁 backend/app/models/experiment.py
class Experiment(Base, BaseModel):
    """Experiment model for A/B testing."""

    __tablename__ = "experiments"

    name = Column(String(100), nullable=False)
    # Stable, human-readable identifier used by the SDK-facing tracking API
    # (`experiment_key`).  Generated from the name on create when not given.
    key = Column(String(100), unique=True, nullable=True, index=True)
    description = Column(Text)
    hypothesis = Column(Text)
    status = Column(
        SQLAEnum(ExperimentStatus),
        default=ExperimentStatus.DRAFT,
        nullable=False,
        index=True,
    )
    experiment_type = Column(
        SQLAEnum(ExperimentType),
        default=ExperimentType.A_B,
        nullable=False,
    )
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{get_schema_name()}.users.id", ondelete="SET NULL"),
    )
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    targeting_rules = Column(JSONB)  # For user segmentation
    ...
    # Issue #22: MAB optimization type
    optimization_type = Column(
        SQLAEnum(
            "fixed", "thompson_sampling", "ucb1", "epsilon_greedy",
            name="optimization_type_enum",
        ),
        nullable=False,
        default="fixed",
    )

    # EP-021: Sequential testing configuration
    sequential_testing_enabled = Column(Boolean, default=False, nullable=False)
    ...
    # Relationships
    owner = relationship("User", back_populates="experiments")
    variants = relationship(
        "Variant", back_populates="experiment", cascade="all, delete-orphan"
    )
    metric_definitions = relationship(
        "Metric", back_populates="experiment", cascade="all, delete-orphan"
    )
    assignments = relationship(
        "Assignment", back_populates="experiment", cascade="all, delete-orphan"
    )
```

Two things to notice. First, the **`key`** column: it is what the SDKs send
(`experiment_key: "hero_banner"`), it's unique, and it's generated from the name on create when
omitted (`"Button Color Test"` → `button-color-test-3f9a2c`). An experiment without a key is
invisible to the tracking API — which was another of the ShopLab bugs: the demo seed never set
it. Second, the **relationships with `cascade="all, delete-orphan"`**: deleting an experiment
deletes its variants, metrics and assignments through the ORM; the foreign keys
(`ondelete="CASCADE"`) do the same at the database level.

The variant and the assignment are small and exact:

```python
# 📁 backend/app/models/experiment.py
class Variant(Base, BaseModel):
    """Variant model for experiment variations."""

    __tablename__ = "variants"

    experiment_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(100), nullable=False)
    description = Column(Text)
    is_control = Column(Boolean, default=False)
    traffic_allocation = Column(Integer, default=50)  # Percentage of traffic
    configuration = Column(JSONB)  # Settings specific to this variant
```

```python
# 📁 backend/app/models/assignment.py
class Assignment(Base, BaseModel):
    """Assignment model for tracking user experiment assignments."""

    __tablename__ = "assignments"

    experiment_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="CASCADE"), nullable=False)
    variant_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.variants.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(255), nullable=False)  # External user identifier
    context = Column(JSONB)  # User attributes used for targeting

    @declared_attr
    def __table_args__(cls):
        schema_name = get_schema_name()
        return (
            # Composite unique constraint on experiment + user
            Index(
                f"{schema_name}_assignment_experiment_user",
                "experiment_id",
                "user_id",
                unique=True,
            ),
            # Index for querying all assignments for a user
            Index(f"{schema_name}_assignment_user", "user_id"),
            {"schema": schema_name},
        )
```

`user_id` is a `String(255)`, **not** a foreign key to `users`. That's the single most
important modelling decision in the file: the users of _your_ product (the ones being
experimented on) are not users of the _platform_ (the ones logging into the dashboard). An SDK
sends whatever stable identifier your app has — a visitor cookie, an account id, a device id —
and the platform never needs to know who they are. `traffic_allocation` is an integer
percentage 0–100 (the Lambda functions use a 0.0–1.0 fraction instead — a known unit
inconsistency noted in `CLAUDE.md`; keep it in mind when you read `backend/lambda/`).

The event table is the other side of the join:

```python
# 📁 backend/app/models/event.py
class Event(Base, BaseModel):
    """Event model for tracking user interactions and metric data."""

    __tablename__ = "events"

    event_type = Column(String(100), nullable=False, index=True)
    event_name = Column(String(255), nullable=True, index=True)
    user_id = Column(String(255), nullable=False, index=True)  # External user identifier
    experiment_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="SET NULL"), nullable=True)
    feature_flag_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.feature_flags.id", ondelete="SET NULL"), nullable=True)
    variant_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.variants.id", ondelete="SET NULL"), nullable=True)
    value = Column(Float)  # Numeric value if applicable
    event_metadata = Column(JSONB)  # Additional data
    created_at = Column(String, nullable=False, index=True)  # Timestamp for when the event was created
```

Note `ondelete="SET NULL"` (an event outlives its experiment — audit data is never cascaded
away), the denormalised `variant_id` (so the results engine can group events by variant
without a join back to `assignments`), and — a real wart — `created_at` as a **string**
column holding an ISO timestamp, overriding the mixin's `DateTime`. `EventService` serialises
every timestamp to ISO-8601 with an explicit `+00:00` so string comparison still sorts
correctly; you'll see `_to_iso_timestamp` on Day 5.

> **Did You Know?** The `metrics` table has a `lower_is_better` flag and an
> `event_value_path` (a JSON path into the event's metadata for revenue-style metrics), and
> the `events` table has `value` — but the primary results path today counts conversions
> (`COUNT(events)` per variant); continuous metrics go through Welch's t-test only in the
> enhanced analysis paths. When you read `calculate_metric_results` on Day 6 and see only
> conversion rates, that's not an omission in the book.

### Build It

Open the tables and read the constraints back from the database itself:

```bash
docker compose exec postgres psql -U postgres -d experimentation -c '\d experimentation.assignments'
docker compose exec postgres psql -U postgres -d experimentation -c '\d experimentation.variants'
```

Find the unique index on `(experiment_id, user_id)` and the check constraint on
`traffic_allocation`. Then prove the "not a foreign key" claim:

```bash
git grep -n "user_id = Column" backend/app/models/assignment.py backend/app/models/event.py
```

Finally, list every model the platform registers — `backend.app.models` imports every module
so standalone scripts can configure the mappers (that import list was itself one of the
ShopLab fixes):

```bash
git grep -n "^from \.\|^from backend.app.models" backend/app/models/__init__.py | wc -l
```

### Key takeaways

- Five nouns, five tables: experiments, variants, metrics, assignments, events — all UUID-keyed
  with `created_at`/`updated_at`.
- Invariants that _must_ hold are database constraints (unique `(experiment, user)`
  assignment, allocation 0–100, end after start); friendlier rules are Pydantic validators.
- `experiment.key` is the SDK's handle; no key, no tracking.
- `user_id` on assignments and events is an opaque external string, never a platform user.
- Events carry a denormalised `variant_id` and a string ISO `created_at`; they are never
  cascaded away.

### Q&A

**Q. Why is `Experiment.key` nullable if the SDKs can't work without it?**
History: the column was added after experiments existed. The service generates it on create,
the demo seeds backfill it, and the dashboard's "new experiment" form generates one from the
name as you type. Treat "an experiment with a null key" as data that predates the tracking
API.

**Q. What's in `Variant.configuration` and who reads it?**
Whatever your app needs to render that arm — `{"media": "video", "headline": "..."}` in
ShopLab's hero experiment. `POST /tracking/assign` returns it verbatim in the
`configuration` field, and the React SDK's `useExperiment` hands it to the component. The
platform never interprets it.

**Q. Can a user be in two variants of the same experiment?**
No — the unique index makes it impossible at the database level, and `assign_user` returns
the existing row before ever hashing. Reassignment exists (`AssignmentService.reassign_user`)
for admin overrides and deletes the old row first.

**Q. Why store `context` on the assignment?**
It's the targeting context the SDK sent at assignment time. It lets you audit _why_ a user
was eligible, and the dimensional-analysis breakdowns (Week 3) can segment by attributes that
were true at assignment rather than at conversion.

---

## Day 4 — Assignment: deterministic hashing and sticky rows

### What & why

"Which variant is this user in?" has to satisfy four properties at once, and the tension
between them is the whole design:

1. **Deterministic.** The same user must get the same variant every time, on every device,
   from every SDK, even before the platform has stored anything about them.
2. **Uniform and proportional.** Over many users, variants receive traffic in the configured
   proportions (50/50, 90/10, …) with no systematic bias.
3. **Sticky.** Once a user has been assigned, changing the traffic split, the targeting rules
   or the holdout must not move them — a user who saw the blue button keeps seeing it.
4. **Eligible.** New users must pass the gates — global holdout, mutual-exclusion group,
   targeting rules — before they are bucketed at all, and ineligible users must get a
   sensible default without polluting the experiment's data.

Property 1 rules out random assignment and rules in hashing: `hash(user_id, experiment_id)`
is a function, so it's the same everywhere. Property 3 rules out _only_ hashing — because the
inputs to eligibility change over time — and rules in a stored row that is consulted first.
Experimently does both: hash to decide, store to remember, and always prefer the stored row.

#### Why MD5, and why `% 100`

The hash is `MD5("{user_id}:{experiment_id}")` interpreted as an integer, reduced `% 100` to a
bucket in `[0, 100)`. MD5 is cryptographically broken and that is irrelevant here — it's used
as a fast, well-distributed, universally available hash (`usedforsecurity=False` tells Python
and its linters exactly that). What matters is that the same input produces the same 128 bits
in Python, Go, Java, Swift and JavaScript, which is why the cross-SDK consistent-hash utility
is _also_ MD5 (Day 20). The `experiment_id` in the input is the salt: the same user lands in
different buckets for different experiments, so being in "treatment" of one test says nothing
about another.

`% 100` gives a bucket the same granularity as `traffic_allocation` (an integer percentage).
Variants are laid out as a cumulative distribution — control `[0, 50)`, treatment `[50, 100)`
for a 50/50 split — and the bucket is looked up in it. Two consequences worth internalising:
changing a 50/50 split to 60/40 moves users in buckets 50–59 from treatment to control (the
sticky row is what protects them), and any allocation that doesn't sum to 100 leaves buckets
that fall through to the last variant.

### How Experimently does it

The whole decision is one method:

```python
# 📁 backend/app/services/assignment_service.py
    def _hash_user_to_variant(
        self, user_id: str, experiment: Experiment
    ) -> Union[str, UUID]:
        """
        Determine variant assignment using deterministic hashing.

        This ensures that the same user will always be assigned to the same variant
        for a given experiment, based on their user ID and the experiment ID.
        """
        if not experiment.variants:
            raise ValueError(f"Experiment {experiment.id} has no variants")

        # Create a hash using user ID and experiment ID
        hash_input = f"{user_id}:{experiment.id}"
        hash_value = int(
            hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest(), 16
        )

        # Get variants with their traffic allocations
        variants = experiment.variants

        # Create cumulative distribution based on traffic allocation
        total = 0
        distribution = []
        for variant in variants:
            total += variant.traffic_allocation
            distribution.append((total, variant.id))

        # Normalize hash to be within range [0, 100)
        bucket = hash_value % 100

        # Find the assigned variant
        for threshold, variant_id in distribution:
            if bucket < threshold:
                return variant_id

        # Fallback to last variant (should not happen if allocations sum to 100)
        return variants[-1].id if variants else None
```

Around it, `assign_user` implements the four properties in order — **sticky row first,
eligibility second, hash last**:

```python
# 📁 backend/app/services/assignment_service.py
    def assign_user(self, user_id, experiment_id, track_exposure=True,
                    override_variant_id=None, context=None) -> Dict[str, Any]:
        # Get experiment with variants
        experiment = (
            self.db.query(Experiment)
            .options(joinedload(Experiment.variants))
            .filter(Experiment.id == experiment_id)
            .first()
        )
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        # Check experiment status
        if experiment.status != ExperimentStatus.ACTIVE:
            raise ValueError(
                f"Cannot assign users to experiment with status: {experiment.status}"
            )

        # Check for existing assignment (sticky assignment)
        existing_assignment = (
            self.db.query(Assignment)
            .filter(Assignment.user_id == user_id, Assignment.experiment_id == experiment_id)
            .order_by(desc(Assignment.created_at))
            .first()
        )

        if existing_assignment:
            assignment_dict = self._assigned_result(user_id, experiment_id)
            if track_exposure:
                self.event_service.track_exposure(
                    user_id=user_id,
                    experiment_id=str(experiment_id),
                    variant_id=str(existing_assignment.variant_id),
                    properties=context,
                )
            return assignment_dict

        # Eligibility gate for new users: holdout -> mutual exclusion -> targeting
        eligibility = self.check_eligibility(user_id, experiment, context)
        if not eligibility["eligible"]:
            return self._ineligible_result(user_id, experiment, eligibility)

        # Determine variant assignment
        if override_variant_id:
            variant_id = override_variant_id
            ...
        else:
            # Use deterministic hashing to assign variant
            variant_id = self._hash_user_to_variant(user_id, experiment)

        # Create new assignment
        assignment = Assignment(user_id=user_id, experiment_id=experiment_id, variant_id=variant_id)
        self.db.add(assignment)
        self.db.commit()
        self.db.refresh(assignment)
        ...
        return self._assigned_result(user_id, experiment_id)
```

Read the order carefully because it encodes the policy. A returning user is served from the
row **before** eligibility is re-checked — so tightening a targeting rule mid-experiment
doesn't evict anyone (the docstring says it: _"existing (sticky) assignments always win and
are returned unchanged even if the user would no longer be eligible"_). A new user is gated
**before** hashing — so an ineligible user gets no row and no exposure event, and therefore
never appears in a denominator.

#### The eligibility gate

```python
# 📁 backend/app/services/assignment_service.py
    def check_eligibility(self, user_id, experiment, context=None) -> Dict[str, Any]:
        """
        Decide whether a *new* user may be assigned to ``experiment``.

        Checks run in order and the first failure wins:

        1. global holdout — an active ``GlobalHoldout`` that buckets the user
           (reason ``holdout``);
        2. mutual exclusion — the experiment belongs to a group and the group's
           consistent hashing selects a different experiment for the user
           (reason ``mutual_exclusion``);
        3. targeting — the experiment has targeting rules and the user context
           does not match them (reason ``targeting``).
        """
        # 1. Global holdout
        in_holdout, holdout_percentage, bucket = self.global_holdout_service.is_user_in_holdout(user_id)
        if in_holdout:
            return {"eligible": False, "reason": REASON_HOLDOUT, "detail": ...}

        # 2. Mutual exclusion group
        group_id = getattr(experiment, "mutual_exclusion_group_id", None)
        if group_id:
            if not self.mutual_exclusion_service.is_user_eligible_for_experiment(user_id, experiment.id):
                return {"eligible": False, "reason": REASON_MUTUAL_EXCLUSION, "detail": ...}

        # 3. Targeting rules
        if getattr(experiment, "targeting_rules", None):
            targeting_context = self._build_targeting_context(user_id, context)
            targeting = self._evaluate_experiment_targeting(experiment, targeting_context)
            if not targeting["eligible"]:
                return {"eligible": False, "reason": REASON_TARGETING, "detail": targeting.get("reason")}

        return {"eligible": True, "reason": REASON_ASSIGNED, "detail": None}
```

Each gate is its own hash with its own salt, and that's not incidental. The **global holdout**
hashes `user_id:<HOLDOUT_SALT>` to a bucket 0–99 and holds out users below the configured
percentage — a fixed slice of your audience that is in _no_ experiment, so you can measure
the cumulative effect of everything you've shipped. A **mutual-exclusion group** hashes
`user_id:<group salt>` to `[0, 1)` and carves the group's traffic into equal slots, one per
active experiment, so a user is in at most one of the "checkout" experiments. Because the
salts differ, the holdout bucket, the group slot and the variant bucket are independent —
being held out is uncorrelated with which variant you'd otherwise have got.

```python
# 📁 backend/app/services/global_holdout_service.py
    @staticmethod
    def _get_holdout_bucket_static(user_id: str) -> int:
        """Get holdout bucket for a user (0-99). Static version."""
        combined = f"{user_id}:{HOLDOUT_SALT}".encode("utf-8")
        hash_bytes = hashlib.md5(combined, usedforsecurity=False).digest()[:4]
        hash_int = struct.unpack("<I", hash_bytes)[0]
        return hash_int % 100
```

Notice this one takes the **first four bytes little-endian** rather than the whole digest as
a big integer. That is the cross-SDK convention (Day 20's `consistent_hash`); the variant
hash in `_hash_user_to_variant` uses the full 128-bit integer. Both are deterministic, both
are uniform — but they are _not_ the same function, and you can't use the SDK utility to
predict a variant. That's by design: the SDK hash is a utility for reproducing rollout
buckets offline, not an assignment algorithm.

#### The response the SDK sees

The endpoint turns the service's dict into a fixed shape, and — importantly — an ineligible
user gets HTTP **200**, the control variant, and `assigned: false`:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
        return VariantAssignmentResponse(
            experiment_key=request.experiment_key,
            user_id=request.user_id,
            variant_id=str(variant.id),
            variant_name=variant.name,
            is_control=bool(variant.is_control),
            configuration=variant.configuration,
            assigned=bool(assignment_data.get("assigned", True)),
            reason=str(assignment_data.get("reason") or "assigned"),
        )
```

Why 200 and not 4xx? Because from the app's point of view "this user isn't in the experiment"
is not an error — it's the normal case for most users of a targeted test, and the app should
render the default experience without logging a failure. The SDKs treat `variant_name` as
the answer and fall back to `"control"` only on transport errors.

> **Watch Out!** Multi-armed bandits change one line of this flow. For
> `optimization_type != "fixed"` the endpoint computes an `override_variant_id` from the
> current `BanditState.variant_weights` (hashing `user_id:experiment_id:bandit` into the
> cumulative _weights_ instead of the fixed allocations) and passes it to `assign_user`.
> Sticky rows still win, eligibility still runs, and the override is deterministic per user,
> so a bandit doesn't break any of the four properties — it just replaces the fixed
> distribution with a learned one. Day 19 covers how the weights are learned.

### Build It

Prove determinism and stickiness against a running API. Use the demo seed's API key (or
create one on the dashboard's Admin → API keys page):

```bash
API=http://localhost:8000/api/v1
KEY=$(cat demo/shoplab/.api_key 2>/dev/null || echo "<your key>")

# assign the same user three times: same variant every time
for i in 1 2 3; do
  curl -s -X POST $API/tracking/assign -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
    -d '{"experiment_key":"shoplab_hero","user_id":"user-42"}' | python -c 'import sys,json; d=json.load(sys.stdin); print(d["variant_name"], d["assigned"], d["reason"])'
done

# reproduce the bucket offline — the same formula as _hash_user_to_variant
python - <<'PY'
import hashlib
exp_id = input("experiment id (uuid): ").strip()
for uid in ["user-42", "user-43", "user-44"]:
    bucket = int(hashlib.md5(f"{uid}:{exp_id}".encode(), usedforsecurity=False).hexdigest(), 16) % 100
    print(uid, bucket)
PY
```

Then read the unit tests that pin the gate order — they are the specification:
`backend/tests/unit/services/test_assignment_service_eligibility.py`.

### Key takeaways

- Assignment = `md5(user_id:experiment_id)` → bucket `% 100` → cumulative
  `traffic_allocation` lookup. Deterministic, uniform, proportional.
- The stored `assignments` row is consulted first; sticky beats eligibility, eligibility
  beats hashing.
- Gates run holdout → mutual exclusion → targeting, each with its own salt so they're
  independent.
- Ineligible users get 200 + control + `assigned: false` + a `reason`; no row, no exposure.
- Bandits swap the fixed distribution for learned weights via a deterministic override.

### Q&A

**Q. If assignment is deterministic, why store it at all?**
Because the _inputs_ to the decision aren't constant: allocations change, targeting rules
change, holdouts are created, experiments join groups. The stored row freezes the answer at
the moment of first exposure, which is what makes the experiment analysable.

**Q. What happens to a user who was assigned before a targeting rule was tightened?**
Nothing — they keep their variant. `assign_user` returns the existing row before running
`check_eligibility`. If you truly need to evict them, that's a deliberate admin action
(`reassign_user` / deleting assignments), not something the platform does silently.

**Q. Does `context` affect the hash?**
No. `context` feeds targeting (eligibility) and is stored on the row for auditing; the hash
input is exactly `user_id:experiment_id`. Two users with the same id and different contexts
get the same variant.

**Q. The SDK ships a `consistent_hash` too. Can I use it to know a user's variant before
calling the API?**
No. It's a different function (first four bytes, little-endian, ÷ 2³²) meant for reproducing
_flag rollout_ buckets and for the cross-language golden-vector tests. Variant assignment
uses the full digest and the experiment's UUID, and it's gated by eligibility you can't
evaluate locally. Call `/tracking/assign`; the SDK caches the answer.

---

## Day 5 — Event tracking and conversion matching

### What & why

The second table. An **event** is an append-only fact — "user X did Y at time T, worth V" —
and the tracking API's job is to accept those facts from any SDK at high volume, attach the
identifiers the analysis will need, and never lose one. Three design decisions follow from
"append-only fact":

1. **Events are never updated.** There's no `PUT /events/{id}`. A wrong event is corrected by
   another event or by excluding it at analysis time, never by mutation — because the
   analysis has to be reproducible from the log.
2. **Events are resolved at write time.** The SDK sends an `experiment_key` (a human string);
   the API resolves it to an `experiment_id`, looks up the user's current assignment, and
   denormalises the `variant_id` onto the row. The results engine can then group by
   `variant_id` with no join. This is the write-once, read-many trade.
3. **Events are matched by name, not type.** This is the Day 1 bug, and today you'll see
   both sides of it in code.

#### `experiment_key` vs `feature_flag_key`, and the fan-out rule

An event can be tied to an experiment, a feature flag, or both. The public `/track` endpoint
takes keys; `/tracking/events` takes ids for server-side integrations that already hold them;
`/tracking/batch` takes up to 100 events at once and reports per-event failures without
failing the batch. And the SDKs add one rule of their own: an event tracked _without_ a key
**fans out** to every experiment the SDK has a cached assignment for and every flag it has
evaluated for that user — so `track('purchase')` from your checkout page counts for the hero
test, the sort bandit and the buy-button test simultaneously. That rule is what makes a
single metric definition (`event_name: "purchase"`) reusable across experiments.

### How Experimently does it

The request schema is permissive by design (an SDK should never fail to record an event
because of a shape quibble) but insists on the one thing the join needs:

```python
# 📁 backend/app/schemas/tracking.py
class EventRequest(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=100)
    event_name: Optional[str] = Field(None, max_length=255)
    user_id: str = Field(..., min_length=1, max_length=255)
    experiment_key: Optional[str] = Field(None, max_length=100)
    feature_flag_key: Optional[str] = Field(None, max_length=100)
    ...
    @model_validator(mode="after")
    def validate_experiment_or_feature_flag(self) -> "EventRequest":
        ...
```

The endpoint resolves keys to ids, finds the assignment, and hands a column-shaped
`EventCreate` to the service:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
    # Find experiment ID if specified
    experiment_id = None
    variant_id = None
    if request.experiment_key:
        experiment = db.query(Experiment).filter(Experiment.key == request.experiment_key).first()
        if experiment:
            experiment_id = experiment.id

            # Find variant if user has an assignment
            assignment = (
                db.query(Assignment)
                .filter(
                    Assignment.experiment_id == experiment_id,
                    Assignment.user_id == request.user_id,
                )
                .order_by(Assignment.created_at.desc())
                .first()
            )
            if assignment:
                variant_id = assignment.variant_id
    ...
    event_data = EventCreate(
        user_id=request.user_id,
        event_type=request.event_type,
        event_name=request.event_name or request.event_type,
        experiment_id=str(experiment_id) if experiment_id else None,
        feature_flag_id=str(feature_flag_id) if feature_flag_id else None,
        variant_id=str(variant_id) if variant_id else None,
        value=request.value,
        properties=request.metadata,
        timestamp=request.timestamp or datetime.now(timezone.utc),
    )
    event = EventService(db).track_event(event_data)
    record_event_tracked(str(event_data.event_type))
```

Look at `event_name=request.event_name or request.event_type`. The SDKs send
`event_type: "purchase"` (the tracking schema's `event_type` is free text — it is _not_ the
`EventType` enum), and the API copies it into `event_name`. So an SDK purchase is
`("purchase", "purchase")`. Now the other producer:

```python
# 📁 backend/app/services/event_service.py  (track_conversion, trimmed)
        return self.track_event({
            "event_type": EventType.CONVERSION.value,   # "conversion"
            "event_name": event_name,                   # the metric's event name
            ...
        })
```

Two producers, two conventions — `("purchase", "purchase")` and `("conversion", "purchase")`
— and one column they agree on: `event_name`. Hence the rule in `event_matching.py`, quoted
on Day 1: **match on `event_name`, use `event_type` only to exclude exposures.** The module
also exports a raw-SQL predicate for the few `text()` queries in the results engine:

```python
# 📁 backend/app/services/event_matching.py
#: ``event_type`` values that record that a user *saw* a variant, never a conversion.
EXPOSURE_EVENT_TYPES = (EventType.EXPOSURE.value, "experiment_exposure")

#: SQL fragment for raw ``text()`` queries; binds ``:event_name``.
CONVERSION_SQL_PREDICATE = (
    "event_name = :event_name "
    "AND event_type NOT IN ('exposure', 'experiment_exposure')"
)
```

The service itself is deliberately tolerant about _shapes_ — it accepts a Pydantic model or a
dict, schema field names or column names — and strict about _identity_:

```python
# 📁 backend/app/services/event_service.py
    @classmethod
    def build_event(cls, event_data: Union[EventCreate, Mapping[str, Any]]) -> Event:
        data = cls._as_dict(event_data)

        user_id = data.get("user_id")
        if not user_id:
            raise ValueError("user_id is required to track an event")

        experiment_id = _to_uuid(data.get("experiment_id"))
        feature_flag_id = _to_uuid(data.get("feature_flag_id"))
        if experiment_id is None and feature_flag_id is None:
            raise ValueError("Either experiment_id or feature_flag_id must be provided")

        metadata = next((data[k] for k in _METADATA_KEYS if data.get(k) is not None), None)
        timestamp = next((data[k] for k in _TIMESTAMP_KEYS if data.get(k)), None)

        return Event(
            event_type=data.get("event_type") or EventType.CUSTOM.value,
            event_name=data.get("event_name"),
            user_id=str(user_id),
            experiment_id=experiment_id,
            feature_flag_id=feature_flag_id,
            variant_id=_to_uuid(data.get("variant_id")),
            value=data.get("value"),
            event_metadata=metadata,
            created_at=_to_iso_timestamp(timestamp),
        )
```

`_to_iso_timestamp` is the answer to Day 3's string `created_at`: a naive datetime is
assumed UTC and made aware before `.isoformat()`, so every stored value looks like
`2026-09-11T14:03:22.118342+00:00` and sorts correctly as text.

#### Exposures

`assign_user` also writes an event — `track_exposure`, `event_type="exposure"` — every time a
user is assigned _or re-served from the sticky row_. Exposures are what the daily results
charts and the interaction-detection service use for "who actually saw the variant on day
N"; they are never conversions, which is exactly why `event_matching` excludes them by type.

> **By the Way.** The tracking endpoints also record two Prometheus counters —
> `events_tracked_total{event_type}` and `experiment_assignments_total{experiment_id,
> variant_id}` — through `backend/app/core/metrics.py`. Ineligible assignments are counted
> under their reason (`holdout`, `mutual_exclusion`, `targeting`) in the variant label, so
> you can see on a Grafana panel how much traffic your holdout is eating. Week 4 covers the
> metrics registry.

### Build It

Track an event for the user you assigned yesterday and watch the denormalisation happen:

```bash
curl -s -X POST $API/tracking/track -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"experiment_key":"shoplab_hero","user_id":"user-42","event_type":"purchase","value":49.0,"metadata":{"sku":"pack-01"}}' \
  | python -m json.tool
```

The response carries `experiment_id` and `variant_id` — resolved by the API, not sent by you.
Now look at the row and confirm the two-column convention:

```bash
docker compose exec postgres psql -U postgres -d experimentation -c \
  "select event_type, event_name, variant_id is not null as has_variant, created_at from experimentation.events where user_id='user-42' order by created_at desc limit 5"
```

You should see an `exposure` row from the assignment and a `purchase`/`purchase` row from the
track call. Finally, find every place that gets conversion matching right — and confirm
there's nowhere left that gets it wrong:

```bash
git grep -n 'event_type == "conversion"\|event_type=="conversion"' backend/app || echo "none — good"
```

### Key takeaways

- Events are append-only facts; correction is by addition or exclusion, never mutation.
- `/track` resolves `experiment_key` → `experiment_id` and denormalises the user's
  `variant_id` onto the row at write time.
- SDK events are `(event_type=name, event_name=name)`; the internal helper writes
  `("conversion", name)`; match on **`event_name`**, exclude exposures by `event_type`.
- Timestamps are stored as ISO-8601 strings with an explicit UTC offset.
- SDKs fan out key-less events to every cached assignment and evaluated flag.

### Q&A

**Q. Why does the tracking schema have both `properties` and `metadata`?**
Compatibility. `properties` is the name the endpoints and services use; `metadata` is what
some callers already sent. `EventService.build_event` accepts either (and the ORM column is
`event_metadata`, because `metadata` is reserved by SQLAlchemy's declarative base).

**Q. What if the user has no assignment when the event arrives?**
The event is stored with `variant_id = NULL`. It still counts toward flag metrics if a flag
key was given, and the results engine — which groups by `variant_id` — won't attribute it to
any arm. That's correct: a conversion by a user who was never assigned isn't evidence about
either variant.

**Q. Can I backfill events with a historical timestamp?**
Yes — `timestamp` is optional and defaults to server time; the seeds use it to build 30 days
of history. It's stored as sent (normalised to UTC), so daily breakdowns honour it.

**Q. Why is `/tracking/batch` limited to 100 events?**
Request body size (the app caps bodies at 1 MB) and failure isolation: each event is
validated and stored independently and the response reports `success_count`,
`failure_count` and per-event `errors`. The SDKs chunk larger batches client-side.

---

## Day 6 — Results: from counts to a verdict

### What & why

You have two tables. A result is, at its heart, four numbers per variant — assigned users,
converted users, a rate, and how sure you are that the rate differs from control — plus a
recommendation. Today is about how the platform gets from `COUNT(*)` to "ship treatment",
and about the **honesty checks** it runs on the way, because a results page that only shows
the flattering number is worse than no results page.

The primary path is deliberately simple statistics — a contingency table per variant vs
control, a test of whether the proportions differ, a confidence interval on each rate —
because simple statistics are the ones a product manager can reason about and an auditor can
reproduce. The advanced methods (sequential testing, CUPED, Bayesian, bandits) sit _beside_
the primary result as additional blocks, not instead of it. Day 19 and the
[statistics book](../experimentation-statistics/README.md) cover those; today is the spine.

#### Why Fisher, why a z-test, and which one when

For a conversion metric the natural test is a comparison of two proportions. Two tests
appear in the code: **Fisher's exact test**, which is exact for any sample size and is what
`calculate_metric_results` uses by default; and the **two-proportion z-test**, which the
enhanced path selects for large conversion samples (it's the same answer as Fisher at scale
and much cheaper to compute). For continuous metrics (revenue, duration) there's **Welch's
t-test**, which doesn't assume equal variances between arms. The selection rule is written
down, not left to the reader:

```python
# 📁 backend/app/services/analysis_service.py
    def select_statistical_test(self, metric_type: str, control_size: int, treatment_size: int) -> str:
        """Select the most appropriate statistical test.

        Returns 'fisher_exact' for small samples (n < 30), 'z_test' for
        conversion metrics with large samples, and 'welch_t_test' for
        continuous metrics (revenue, duration, count, custom).
        """
        if min(control_size, treatment_size) < 30:
            return "fisher_exact"
        if metric_type == "conversion":
            return "z_test"
        return "welch_t_test"
```

### How Experimently does it

The spine is `AnalysisService.calculate_metric_results`. Read it as four steps: count
assignments per variant, count conversions per variant (with the Day 5 helper), compute rates
and a test vs control, attach a confidence interval.

```python
# 📁 backend/app/services/analysis_service.py
    def calculate_metric_results(self, experiment: Experiment, metric: Metric) -> Dict[str, Any]:
        variant_ids = [str(v.id) for v in experiment.variants]

        # Find control variant
        control_variant = next((v for v in experiment.variants if v.is_control), None)
        if not control_variant:
            raise ValueError(f"Experiment {experiment.id} has no control variant")

        # Get assignment counts per variant
        assignments = {}
        for variant_id in variant_ids:
            count = (
                self.db.query(func.count(Assignment.id))
                .filter(Assignment.experiment_id == experiment.id, Assignment.variant_id == variant_id)
                .scalar() or 0
            )
            assignments[variant_id] = count

        # Get conversion counts per variant
        conversions = {}
        for variant_id in variant_ids:
            count = (
                self.db.query(func.count(Event.id))
                .filter(
                    Event.experiment_id == experiment.id,
                    Event.variant_id == variant_id,
                    conversion_event_filter(metric.event_name),
                )
                .scalar() or 0
            )
            conversions[variant_id] = count
        ...
                # Create contingency table
                contingency_table = [
                    [variant_conversions, variant_non_conversions],
                    [control_conversions, control_non_conversions],
                ]

                # Run Fisher's exact test
                try:
                    odds_ratio, p_value = stats.fisher_exact(contingency_table)
                    is_significant = p_value < 0.05  # Using 95% confidence level
```

The denominator is `COUNT(assignments)` — the stored rows from Day 4 — and the numerator is
`COUNT(events)` with the Day 5 filter. That is the join, written out. (One subtlety: it counts
_events_, not _distinct users_; a user who purchases twice counts twice. For a conversion
metric that's usually not what you want; the enhanced path and the streaming service use
distinct users. Know which number you're looking at.)

The confidence interval uses the normal approximation, `p ± 1.96·√(p(1−p)/n)`, clamped to
`[0, 100]`; `wilson_confidence_interval` is available for the small-sample case where the
normal interval misbehaves. Effect sizes (`cohens_h` for proportions, `cohens_d` for means)
are labelled negligible/small/medium/large so the dashboard can say "significant but
negligible" — the most useful sentence in experimentation.

#### The honesty checks

The endpoint that the dashboard calls, `GET /api/v1/results/{experiment_id}`, wraps the
computation with three things that stop it lying:

```python
# 📁 backend/app/api/v1/endpoints/results.py
@router.get("/{experiment_id}", response_model=ExperimentResultsResponse)
def get_experiment_results(
    experiment_id: UUID,
    confidence_level: float = Query(default=0.95, ge=0.80, le=0.99),
    correction_method: str = Query(default="none", pattern="^(none|bonferroni|benjamini_hochberg)$"),
    use_cache: bool = Query(default=True),
    breakdown: Optional[str] = Query(default=None, ...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ExperimentResultsResponse:
    ...
        # --- P0 statistical credibility: sample-ratio mismatch ---
        srm_response = _compute_srm(experiment_id, db)

        response = ExperimentResultsResponse(
            ...
            sample_size_adequate=result.get("sample_size_adequate", False),
            summary=summary_data,
            metrics=metrics_data,
            sequential_testing=result.get("sequential_testing"),
            breakdown=breakdown_response,
            bayesian_results=result.get("bayesian_results"),
            srm=srm_response,
        )
```

**Sample-ratio mismatch (SRM).** If you configured 50/50 and observe 60/40, the randomiser
is broken and every p-value below is meaningless. The check is a chi-square goodness-of-fit
of observed assignment counts against `traffic_allocation`, flagged at p < 0.001:

```python
# 📁 backend/app/services/srm_service.py
"""
An SRM is the single most common way an A/B test silently lies: the
randomiser was configured for, say, a 50/50 split but the observed
assignment counts are 60/40.  When that happens the groups are no longer
exchangeable and every downstream p-value is meaningless.
...
``warning`` is raised at p < 0.001 — the conventional SRM threshold, deliberately strict
because dashboards are polled many times.
"""
```

**Multiple-testing correction.** With several metrics, `correction_method=bonferroni|
benjamini_hochberg` adjusts the p-values; the primary metric is listed first, and — since the
ShopLab fix — a significant winner is only recommended as `SHIP_VARIANT` once _every_
variant reached the metric's `minimum_sample_size`.

**Caching with a status-aware TTL.** Results are cached in Redis for 5 minutes while an
experiment is running and 24 hours once it's completed; `use_cache=false` forces a
recomputation, and `POST /results/{id}/invalidate-cache` clears it. Best-effort: a Redis
outage means recomputation, never an error.

Then the response is **snapshotted** (`_record_results_snapshots`) into `analysis_snapshots`
for auditing, so a number a stakeholder saw last Tuesday can be recovered even after the
events table has grown.

> **Did You Know?** Every Monte Carlo computation on the platform — the Bayesian
> probability-to-be-best, expected loss, Thompson-sampling weights — is seeded from
> `blake2b(f"{experiment_id}|{YYYY-MM-DD}|{n_samples}")`, so two calls on the same day for the
> same experiment return byte-identical sample arrays. The results a dashboard shows, the
> numbers the API returns to a script, and the persisted snapshot all agree. `core/stats_engine.py`
> also carries an `ENGINE_VERSION` that's bumped whenever a change would alter a number a
> client might have persisted. Reproducibility is treated as a feature, not a nicety.

### Build It

Fetch results for a demo experiment, then reproduce the primary number by hand:

```bash
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@demo.com","password":"Demo1234!"}' | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

EXP=<experiment uuid from the dashboard URL>
curl -s "$API/results/$EXP?use_cache=false" -H "Authorization: Bearer $TOKEN" | python -m json.tool | head -80
```

Now the same numbers from SQL — this is the join, and it should match `sample_size` and
`conversions` per variant exactly:

```sql
-- docker compose exec postgres psql -U postgres -d experimentation
with m as (select event_name from experimentation.metrics where experiment_id = '<EXP>' and is_primary limit 1)
select v.name,
       (select count(*) from experimentation.assignments a where a.variant_id = v.id) as assigned,
       (select count(*) from experimentation.events e, m
         where e.experiment_id = v.experiment_id and e.variant_id = v.id
           and e.event_name = m.event_name
           and e.event_type not in ('exposure','experiment_exposure')) as conversions
from experimentation.variants v where v.experiment_id = '<EXP>';
```

Feed the four counts into `scipy.stats.fisher_exact` and compare the p-value with the API's.

### Key takeaways

- A result is `COUNT(assignments)` and `COUNT(events)` per variant, a proportion test vs
  control, and a confidence interval — the Day 3 join, computed.
- Test selection is explicit: Fisher for n < 30, z-test for large conversion samples, Welch
  for continuous metrics.
- Three honesty checks wrap every result: SRM (chi-square, p < 0.001), multiple-testing
  correction, and minimum-sample-size gating of the recommendation.
- Results are cached with a status-aware TTL and snapshotted for audit.
- Monte Carlo is seeded per `(experiment, day, n_samples)`; the engine has a version.

### Q&A

**Q. Fisher's exact test on a million users? Isn't that slow?**
`scipy.stats.fisher_exact` on a 2×2 table is fast even for large counts, and the enhanced
path switches to the z-test at n ≥ 30 anyway. The expensive part of a results request is the
counting queries, which is why the endpoint caches.

**Q. The dashboard says "significant" but the recommendation is still "keep running". Why?**
Minimum sample size. A metric declares `minimum_sample_size` (default 100), and the
`SHIP_VARIANT` recommendation requires every variant to have reached it. A p-value under 0.05
with 40 users a side is exactly the kind of result that reverses next week.

**Q. What does an SRM warning tell me to do?**
Stop trusting the result and go find the leak: a targeting rule that applies to one variant's
rendering path, an SDK version that fails to assign on one platform, a bot that only hits
one URL. The chi-square tells you the split is wrong; it can't tell you why.

**Q. Why cache results for 24 hours on a completed experiment but only 5 minutes on a running
one?**
A completed experiment's events don't change, so recomputing is waste; a running one accrues
data continuously and a 5-minute lag is the trade between freshness and load. Both TTLs are in
`results.py` and both are bypassed by `use_cache=false`.

---

## Day 7 — Capstone: trace `assign → track → results` end-to-end

### What & why

Days 3–6 each took one layer. Today you walk one experiment through all of them in order, from
the React SDK hook in ShopLab's home page to the `p_value` on the results dashboard, and you
name the file at every hop. If you can narrate this trace from memory, Week 1 has done its job.

The experiment is ShopLab's **hero A/B** (`shoplab_hero`): control shows an image hero,
treatment shows a video hero; the primary metric is `purchase`.

### The trace

**1. The storefront renders the hero.** ShopLab wraps its app in the React SDK provider once,
with an API key and a stable visitor id:

```tsx
// 📁 demo/shoplab/src/pages/_app.tsx
  const config = useMemo(() => ({ apiKey: API_KEY, baseUrl: API_URL, timeoutMs: 5000 }), []);
  const user = useMemo(() => (visitor ? { userId: visitor.id, attributes: visitor.attributes } : null), [visitor]);
  ...
        <ExperimentationProvider config={config} user={user}>
```

and the home page asks for its variant:

```tsx
// 📁 demo/shoplab/src/pages/index.tsx
  const hero = useExperiment(EXPERIMENT_KEYS.hero);
  const { media, headline, cta } = readHeroConfig(hero.configuration);
```

**2. The hook calls the API.** `useExperiment` → `client.assignExperiment(user, key)` →
`POST /api/v1/tracking/assign` with `{"experiment_key": "shoplab_hero", "user_id":
"<visitor>", "context": {...attributes}}` and the `X-API-Key` header. While the promise is
pending the hook returns the control defaults, so the page never flashes an undefined state.

**3. The API authenticates the key.** `deps.get_api_key` hashes the header with SHA-256,
looks up `APIKey.key` by hash, checks `is_valid`, loads the owning user. (Week 2, Day 10.)

**4. The endpoint finds the experiment by key and status.**

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
    experiment = (
        db.query(Experiment)
        .filter(Experiment.key == request.experiment_key, Experiment.status == ExperimentStatus.ACTIVE)
        .first()
    )
```

A draft or paused experiment is a 404 here — the SDK's `get_variant` swallows that and
returns `"control"`.

**5. `AssignmentService.assign_user` decides.** Sticky row? No (first visit). Holdout? The
demo has none. Mutual exclusion? `shoplab_hero` isn't in a group. Targeting rules? None on
this experiment. Hash: `md5("<visitor>:<experiment uuid>") % 100` → say `73` → treatment
(`[50, 100)`). Insert the `assignments` row; write an `exposure` event.

**6. The response reaches the hook.** `{"variant_name": "Video Hero", "is_control": false,
"configuration": {"media": "video", ...}, "assigned": true, "reason": "assigned"}`. The SDK
caches it per `(user, key)`; `readHeroConfig` picks `media = "video"`; the page re-renders
with the video hero. `experiment_assignments_total{variant_id=...}` ticks up in Prometheus.

**7. The visitor buys something.** The checkout page calls `track('purchase', {...})`. The
event has no explicit key, so the SDK fans it out to every cached assignment — including
`shoplab_hero` — and sends `POST /api/v1/tracking/track` (or a batch) with
`event_type: "purchase"`.

**8. The API stores the event.** `/track` resolves `shoplab_hero` → `experiment_id`, finds
the visitor's assignment → `variant_id`, builds an `EventCreate` with
`event_name = "purchase"` (copied from `event_type`), and `EventService.track_event` inserts
the row with an ISO timestamp.

**9. The dashboard asks for results.** The results page calls `GET /api/v1/results/<id>`
with the admin's bearer token. Cache miss (or `use_cache=false`) →
`AnalysisService.get_experiment_results` → for the primary metric,
`calculate_metric_results`: `COUNT(assignments)` per variant, `COUNT(events)` per variant
with `conversion_event_filter("purchase")`, Fisher's exact test vs control, normal-approx CI.

**10. The honesty checks run.** `_compute_srm` chi-squares the assignment counts against
50/50; `sample_size_adequate` checks every variant against `minimum_sample_size`; the summary
recommends `SHIP_VARIANT` only if both are satisfied and the p-value is under the threshold.

**11. The response is cached and snapshotted**, then rendered by
`frontend/src/components/results/` — the metric comparison table, the confidence-interval
chart, and (because `shoplab_hero` has sequential testing and Bayesian analysis enabled) the
extra blocks Day 19 explains.

### Build It

Run the trace yourself with the simulator off, so every row is yours:

```bash
./demo/setup-local.sh            # once; then stop the shoplab simulator process
open http://localhost:3200       # visit as a new visitor; note the hero you got
# buy something; then:
docker compose exec postgres psql -U postgres -d experimentation -c \
  "select e.event_type, e.event_name, v.name from experimentation.events e join experimentation.variants v on v.id = e.variant_id
    where e.experiment_id = (select id from experimentation.experiments where key='shoplab_hero') order by e.created_at desc limit 5"
open http://localhost:3100/results   # find the hero experiment; your purchase is one conversion
```

Then break it on purpose, to feel the seam: in a scratch script, insert an event for your
visitor with `event_type = "conversion"` and `event_name = "purchase"`, and another with
`event_type = "purchase"` and `event_name = "checkout"`. Refresh results with
`use_cache=false`. The first counts; the second doesn't. That is the whole of Day 5 in one
experiment.

### Key takeaways

- The hop list: SDK hook → `/tracking/assign` → `get_api_key` → experiment by key →
  `assign_user` (sticky → gates → hash) → row + exposure → SDK cache → app renders.
- Then: `track` (fan-out) → `/tracking/track` → key → id, assignment → `variant_id` →
  `EventService` → row.
- Then: `/results/{id}` → `calculate_metric_results` (two counts, Fisher, CI) → SRM +
  sample-size gates → cache + snapshot → dashboard.
- Every layer is a file you can open; every number is a query you can rerun.

---

## Week 1 Workshop

### Quiz

**1. What are the two inputs to the variant hash, and why is the experiment's id one of
them?**
`user_id` and `experiment.id`, joined with a colon. The id acts as a salt so a user's bucket
in one experiment is independent of their bucket in another; without it, every 50/50 test
would put the same users in "treatment".

**2. A user was assigned to treatment last week. Today you add a targeting rule they don't
match. What does `/tracking/assign` return for them?**
Their existing treatment assignment. The sticky row is consulted before eligibility;
`assigned: true, reason: "assigned"`.

**3. An SDK tracks `event_type: "signup"`. What are `event_type` and `event_name` on the
stored row, and which column does a metric with `event_name: "signup"` match on?**
Both are `"signup"` (`/track` copies `event_type` into `event_name` when the latter is
absent). The metric matches on `event_name`, via `conversion_event_filter("signup")`.

**4. Which three honesty checks wrap a results response?**
Sample-ratio mismatch (chi-square, warning at p < 0.001), multiple-testing correction
(Bonferroni / Benjamini-Hochberg), and the minimum-sample-size gate on the `SHIP_VARIANT`
recommendation.

**5. Why does an ineligible user get HTTP 200 rather than 403 or 404?**
Because not being in the experiment is the normal outcome, not an error: the app should
render the default experience. The response carries the control variant, `assigned: false`
and a `reason`, and nothing is recorded for the user.

**6. Why can't `alembic upgrade head` build a fresh database?**
The historical migration chain was autogenerated against databases that already had tables
and re-creates some of them; it isn't replayable from zero. `backend.app.db.bootstrap` does
`create_all` from the models and `alembic stamp head` instead.

### Exercises

**E1.1 — Predict the bucket.** Given an experiment with variants `[control 30, treatment_a 30,
treatment_b 40]` (in that order) and a user whose hash bucket is `59`, which variant do they
get? What if the allocations were `[30, 30, 30]` and the bucket is `95`?

**E1.2 — Find the gate.** Without running anything, list the exact order of checks
`assign_user` performs for a _new_ user, and name the constant returned as `reason` at each
failure.

**E1.3 — Write the filter.** A metric's `event_name` is `"add_to_cart"`. Write the SQLAlchemy
criterion that selects its conversions correctly, and explain why `Event.event_type ==
"add_to_cart"` would be wrong for events written by `EventService.track_conversion`.

**E1.4 — Spot the denominator.** `calculate_metric_results` counts `COUNT(Event.id)` for
conversions. Describe one situation where that overstates the conversion _rate_, and what
you'd count instead.

**E1.5 — Reproduce the p-value.** For a variant with 1,000 assigned / 52 converted against a
control with 1,000 / 40, compute the Fisher exact p-value and the two-proportion z-test
p-value in a Python shell using `scipy.stats`. Are they close? Which would
`select_statistical_test` pick?

### Solutions

**S1.1** — The cumulative thresholds are `[30, 60, 100]`; bucket `59 < 60` → `treatment_a`.
With `[30, 60, 90]` the bucket `95` matches no threshold and falls through to the last
variant, `treatment_b` — the "should not happen if allocations sum to 100" branch.
`ExperimentCreate` rejects such a configuration with a 422, which is why the fallback is
defensive rather than expected.

**S1.2** — (0) Not-found → `ValueError`; status not ACTIVE → `ValueError`. (1) Existing row →
return it (`assigned`). Then for new users: (2) global holdout → `REASON_HOLDOUT`
(`"holdout"`); (3) mutual-exclusion group, if the experiment has one →
`REASON_MUTUAL_EXCLUSION` (`"mutual_exclusion"`); (4) targeting rules, if any →
`REASON_TARGETING` (`"targeting"`). Eligible → `REASON_ASSIGNED` (`"assigned"`), then hash
(or bandit override) and insert.

**S1.3**

```python
from backend.app.services.event_matching import conversion_event_filter
criterion = conversion_event_filter("add_to_cart")
# == and_(Event.event_name == "add_to_cart", Event.event_type.notin_(("exposure", "experiment_exposure")))
```

`track_conversion` writes `event_type="conversion"` and `event_name="add_to_cart"`, so a
filter on `event_type == "add_to_cart"` would miss every event it produced — while an SDK
event has `event_type="add_to_cart"` and would be found. Only `event_name` is common to
both producers.

**S1.4** — A user who converts twice (two purchases) contributes two events to the numerator
but one assignment to the denominator, so a "rate" can exceed 100% in principle and is
inflated in practice for repeatable events. For a _conversion_ metric count distinct users
(`COUNT(DISTINCT Event.user_id)`), as the streaming results service does; keep the raw count
for _count_-type metrics where "events per user" is the quantity of interest.

**S1.5**

```python
from scipy import stats
import math
table = [[52, 948], [40, 960]]
_, p_fisher = stats.fisher_exact(table)
p_c, p_t = 40/1000, 52/1000
pooled = 92/2000
se = math.sqrt(pooled*(1-pooled)*(1/1000 + 1/1000))
z = (p_t - p_c)/se
p_z = 2*(1 - stats.norm.cdf(abs(z)))
print(round(p_fisher, 4), round(p_z, 4))   # ≈ 0.24 and ≈ 0.20 — same conclusion, not significant
```

Both sit well above 0.05. `select_statistical_test("conversion", 1000, 1000)` returns
`"z_test"` because both arms are ≥ 30; the primary path's `calculate_metric_results` would
still have used Fisher, which is why the two numbers can differ slightly on a dashboard that
shows both.

---

**Next:** [Week 2 — The API and the Data →](./week-2-api-and-data.md)

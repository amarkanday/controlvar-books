# Week 1 — The Core Loop

### Days 1–7 · Assign, Track, Analyse

[← Contents](./README.md) · [Week 2 →](./week-2-api-and-data.md)

This week follows the main experiment workflow: assign a variant, record user activity and
calculate results. The final exercise traces an SDK request through those steps to the result
shown in the dashboard.

The Python and FastAPI books cover the language and framework. Here the focus is on the design
choices connecting them: why assignments are persisted, which constraints events must satisfy,
and how analysis uses both. Read the referenced files in the
[`getexperimently/experimently`](https://github.com/getexperimently/experimently) repository
alongside the examples. Blocks marked `# 📁` quote source; omissions are marked with `...`.
This week was checked against `a4a6dbd2` (0.17.0 plus unreleased changes, including #747, #769
and #770).

> Assignments record the variant allocated to each user; events record subsequent activity.
> Analysis connects them by experiment and user, with the variant association checked when counting
> conversions. Persisted assignments keep allocations stable, and binary conversion metrics count
> converting users rather than event rows.

The week is organized as follows:

| Day | Topic                            | Focus                                  |
| --- | -------------------------------- | ---------------------------------------------------------------- |
| 1   | The big picture                  | The shape of the system, and the bug that shows why shape matters |
| 2   | The repo and the dev loop        | Getting from clone to a running API + dashboard, and staying there |
| 3   | The domain model                 | Five tables, and the constraints that make them trustworthy       |
| 4   | Assignment                       | `bucket_of(user_id, experiment.key)` and why the row is sticky    |
| 5   | Tracking & conversion matching   | Append-only events, and the one helper every analysis must use    |
| 6   | Results                          | From assigned and converting users to a verdict, with validity checks |
| 7   | **Capstone:** end-to-end trace   | One experiment through every layer above                          |

Days 1–2 introduce the system and development setup. Days 3–6 cover the model, assignment,
tracking and analysis. Day 7 combines them in a complete request trace. Read Day 1 before following
the individual services; it identifies the components and the data passed between them.

---

<a id="day-1--the-big-picture-experimentlys-architecture-and-the-0-conversions-bug"></a>

## Day 1 — Architecture and the experiment data flow

### What & why

Experimently is a client-server platform. Two types of client use its API:

- **Your application**, through one of **sixteen SDKs** (Python, JavaScript, React, React
  Native, Go, Java, iOS/Swift, Android/Kotlin, Ruby, PHP, .NET, Elixir, Flutter, an edge
  runtime, and two OpenFeature providers, one for JavaScript and one for Python; all at 0.1.0,
  beta). It authenticates with an **API key** and asks three questions: _which variant is this
  user in?_, _is this flag on for this user?_, _record that this user did X._
- **The dashboard**, a Next.js app that product managers and engineers use to create
  experiments and flags, watch results, and administer the platform. It authenticates with a
  **bearer token**: a local JWT with `AUTH_PROVIDER=local` (the default) or a Cognito token
  with `AUTH_PROVIDER=cognito`. The full profile adds SSO (OIDC) sign-in.

Both talk HTTP to one **FastAPI** application. Behind it sit **PostgreSQL** (the system of
record: experiments, variants, assignments, events, flags, users) and, optionally, **Redis**
(rate-limit counters, with an in-memory fallback, and a results cache). The same code ships in
two **profiles**: **core** (`backend/`, `frontend/`) and **full**, which adds the optional
modules under `modules/` (workspaces, SSO, the warehouse, the real-time counters, the ETL
pipeline and more). On AWS the API runs on ECS Fargate; a full checkout adds the
**Kinesis → OpenSearch** analytics stack, **DynamoDB** counters and a Glue/Athena data lake. The
core profile runs fully without any of that, and this week is about the core that runs
everywhere.

Data flows like this: an SDK calls `POST /api/v1/tracking/assign` → the API hashes the user
into a variant and writes an **`assignments`** row → the SDK later calls
`POST /api/v1/tracking/track` → the API writes an **`events`** row → the dashboard calls
`GET /api/v1/results/{experiment_id}` → the **results engine** counts assigned users and
converting users per variant, runs Fisher's exact test against control, and returns a
verdict. You saw the one-page diagram in the
[README](./README.md) — that's the whole book in one picture.

#### Why the server decides, and why PostgreSQL holds the answer

The "obvious" alternative is a fully client-side design: ship the experiment configuration to
the SDK, hash the user locally, never store an assignment at all. It's how several commercial
flag products work, and it is genuinely faster — no round trip. Experimently rejected it, and
the reasons are the ones that shape Week 1:

1. **One source of truth for "who saw what."** If sixteen SDKs each bucketed locally, sixteen
   hash implementations would have to agree bit-for-bit forever, and any drift would silently
   corrupt every experiment. When the server decides, there is one decision
   (`AssignmentService._hash_user_to_variant`) and one stored answer. The server's bucketing
   function is the same one the SDKs ship (`backend/app/core/consistent_hash.py`, pinned across
   languages by `tests/sdk-contract/golden-vectors.json`; Day 4 has the story of how they
   stopped disagreeing), but the server still makes the call and stores it.
2. **Eligibility is a server-side question.** Global holdouts, mutual-exclusion groups and
   targeting rules all need data the client doesn't have (other experiments, the holdout
   configuration, the rules themselves). Day 4 walks that gate; it only works because
   assignment is centralised.
3. **The analysis needs the row.** A p-value is computed from _counts of assigned users_ per
   variant. If assignment lived only in a client's memory, you'd have to infer it from
   exposure events — and users who were assigned but never fired an exposure vanish from the
   denominator. A stored `assignments` row makes the denominator exact.

The cost is real: every first-time assignment is a network call and a database write. The
platform pays it with caching in the SDKs (per user + key) and a much higher rate limit on SDK
paths (Day 8). Hold that tradeoff; it recurs whenever you're tempted to "just cache it on the
client."

> **By the Way.** Feature _flags_ are the exception. The JavaScript and Python SDKs can
> evaluate flags locally from `GET /api/v1/sdk/ruleset` with a key that has the `sdk:ruleset`
> scope (beta), and record those evaluations so safety monitoring keeps its denominator.
> Experiments are still assigned only by the server, for the three reasons above. Week 3 covers
> local evaluation.

<a id="the-bug-that-justifies-the-week"></a>

#### Case study: conversion events missing from analysis

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

The fix is a small module that is now mandatory on every analysis path:

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
> be set as the documented comma-separated string. None were caught by the 5,000-odd tests the
> repository had then, because those tests mocked the seams. You'll meet each bug on the Day that owns it;
> together they are the best argument in this book for driving the platform through its
> _public_ API in tests (Day 22).

### How Experimently does it

The top-level folders map directly onto the architecture:

```
experimently/
├── backend/                   # the core profile's backend
│   ├── app/
│   │   ├── main.py            # the FastAPI app, middleware, lifespan         ← Week 2
│   │   ├── api/
│   │   │   ├── api.py         # core router aggregation; modules mount theirs ← Week 2
│   │   │   ├── deps.py        # get_db, auth, API keys, permissions          ← Week 2
│   │   │   └── v1/endpoints/  # one module per resource                     ← Weeks 1–3
│   │   ├── core/              # config, security, permissions, rules engine,
│   │   │                      # schedulers, scheduler_lock, stats_engine     ← Weeks 2–3
│   │   ├── services/          # business logic (assignment, analysis, ...)  ← Weeks 1–3
│   │   ├── models/            # SQLAlchemy tables                            ← Week 1–2
│   │   ├── schemas/           # Pydantic request/response models            ← Week 2
│   │   ├── middleware/        # rate limiter, security headers, logging     ← Week 2
│   │   └── db/                # session, alembic migrations, bootstrap      ← Week 2
│   ├── lambda/                # event_processor, feature_flag_evaluation, shared:
│   │                          # no stack deploys them                        ← Week 4
│   ├── scripts/               # seeds (demo, shoplab, streampulse, sdk-contract)
│   └── tests/                 # unit / integration / smoke / contract        ← Week 4
├── frontend/                  # Next.js dashboard                            ← Week 4
├── modules/                   # the optional modules (full profile): the same
│                              # layout one level down                        ← Week 2
├── modules-manifest.txt       # the authority on what is a module
├── sdk/                       # 16 SDKs                                      ← Week 3
├── demo/                      # ShopLab (:3200), StreamPulse (:3300)         ← Week 3
├── infrastructure/cdk/        # AWS CDK stacks (Python)                      ← Week 4
├── deploy/compose/            # the production compose file                  ← Week 4
├── scripts/                   # core_build.sh, the lock and licence checks   ← Week 4
├── tests/sdk-contract/        # golden vectors + live contract harness       ← Week 4
├── docker-compose.yml         # postgres + redis + api + dashboard (+demo)   ← Week 4
└── CLAUDE.md                  # the team's working agreements
```

Everything under `backend/` must keep working with `modules/` deleted: that is the **core**
profile, and `scripts/core_build.sh` (`make core-build`) proves it by copying the tree,
deleting `modules/` and rebuilding and testing what is left. Nothing under `backend/` imports a
module; module routers and models register themselves through hooks
(`modules/backend/app/register.py`), and the profile is decided by whether `import modules`
succeeds.

The key insight: **the backend is a single Python package, `backend.app`, and everything
imports it by that full path.** Not `app.models`, not a relative `..services`. The rule is in
`CLAUDE.md` and it isn't style — SQLAlchemy registers a class under the module path it was
imported from, and two spellings of the same module produce two distinct classes and a
"Class is not mapped" error at runtime. You'll see the rule enforced everywhere:

```python
# 📁 backend/app/services/assignment_service.py
from backend.app.core.consistent_hash import bucket_of
from backend.app.core.log_once import EVALUATION_NOTES
from backend.app.core.targeting_adapter import (
    _is_dashboard_rules_shape,
    expand_context,
    normalise_targeting_rules,
)
from backend.app.models.assignment import Assignment
from backend.app.models.experiment import Experiment, ExperimentStatus, Variant
...
from backend.app.services.event_service import EventService
```

The second insight is the **layering**: `endpoints` parse and authenticate, `services` hold
the logic, `models` are the tables. An endpoint that talks directly to the ORM is a smell
(you'll find some — `tracking.py` queries `Experiment` by key before calling the service —
but the direction of dependency is always endpoint → service → model, never back).

> **By the Way.** The core's `api/api.py` makes 38 `include_router` calls, and many of the
> modules behind them are _feature epics_ with an ID in a comment (`EP-021` sequential testing,
> for one). Read those calls top to bottom and you have the core feature list. The optional
> features (workspaces, SSO, RBAC administration, HIPAA, compliance reports, the warehouse,
> ETL, integrations, real-time counters, split-URL tests) have their routers under
> `modules/backend/app/api/v1/endpoints/` and are mounted at start-up through
> `hooks.register_router()` only when the modules are present. The core loop this week
> touches only `tracking.py`, `experiments.py`, `results.py` and `feature_flags.py`.

### Build It

Clone the repo and just _look_. Map each folder to a box in the README diagram. Don't read
code yet — build the mental map.

```bash
git clone https://github.com/getexperimently/experimently.git
cd experimently

# the four layers of the backend this week is about:
ls backend/app/api/v1/endpoints/ backend/app/services/ backend/app/models/ backend/app/schemas/

# the core's table of contents — every include_router is a feature:
grep -n "include_router" backend/app/api/api.py | wc -l
grep -n "include_router" backend/app/api/api.py | head -20

# and the optional modules beside it:
ls modules/backend/app/api/v1/endpoints/

# prove the "one rule" claim: every analysis path counts conversions the same way
git grep -ln "conversion_event_filter\|count_converting_users" backend/app modules
```

You should see five files: `event_matching.py` itself, the results engine
(`analysis_service.py`), the results endpoint (`results.py`), the streaming results service
and the bandit scheduler. That single `git grep` is Day 1 in one line: many consumers, one rule
for the events side of the join.

Then run the check the team uses as a guard against the import-path trap. It prints nothing
when every import is rooted at `backend.app`:

```bash
grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'
```

### Key takeaways

- Experimently = one FastAPI app + PostgreSQL (+ optional Redis), two kinds of client (SDK
  with an API key, dashboard with a bearer token), two profiles (core, and full with
  `modules/`).
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

**Q. What is `backend/lambda/` for, then?**
Less than its name suggests. It holds `event_processor`, `feature_flag_evaluation` and
`shared`, and no stack deploys a handler from it. The experiment-assignment Lambda that used to
sit there was removed (#606): it was never deployed, and it did not enforce mutual exclusion or
the global holdout. Experiment assignment happens only in the API. (The CDK stacks do deploy a
few placeholder or inline functions of their own; Week 4 covers them.) This week you can
ignore the directory.

---

## Day 2 — The repo and the dev loop: venv, PostgreSQL, uvicorn, the dashboard

### What & why

A working local setup makes it easier to trace requests and test changes. This lesson sets up the
API and dashboard development loop, then explains the environment settings most likely to cause
local startup or connection failures.

The backend needs three things: a **Python 3.11 virtual environment** with the pinned
dependencies, a **PostgreSQL** server on `localhost:5432`, and a handful of **environment
variables** that pick the environment, the schema and the auth mode. The dashboard needs Node 22 (`.nvmrc`) and a backend
to talk to. Redis is optional — the rate limiter and caches degrade gracefully without it.

#### Why a virtualenv, and why the tests care so much

The pinned dependency set (`backend/requirements.txt`) is exact — FastAPI, Pydantic,
SQLAlchemy, Alembic and SciPy are each pinned to one version — because several of those libraries change behaviour across minor versions in ways this
codebase depends on. A global Python with different versions will _mostly_ work and then fail in a way that
looks like your bug. `CLAUDE.md` says it in capitals: **always activate the virtualenv first.**

#### Why the schema name is an environment variable

Every table lives in a PostgreSQL _schema_ (a namespace), not the default `public`. The
application uses `experimentation`; the test suite uses `test_experimentation`, in a
per-process database named `experimentation_test_<pid>`. The choice is made at import time:
`POSTGRES_SCHEMA` wins when it is set, and only when it is unset does `APP_ENV`/`TESTING`
decide:

```python
# 📁 backend/app/core/database_config.py
def get_schema_name() -> str:
    """Get the schema name based on the environment.
    ...
    """
    explicit = os.environ.get("POSTGRES_SCHEMA")
    if explicit:
        return explicit

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
the models are imported and nothing moves. That is why `backend/tests/conftest.py` sets
`POSTGRES_SCHEMA=test_experimentation` (and `APP_ENV=test`, `TESTING=true`) **before** its
first `backend.app` import, and why running the app
against the test schema (or vice versa) produces "relation does not exist" errors that look
like a missing migration.

> **Watch Out!** The canonical environment variable is `ENVIRONMENT`
> (`development|test|staging|production`); `APP_ENV` is the legacy spelling and still honoured.
> `core/config.py` resolves them, warns when they disagree, and mirrors the legacy value back
> into `os.environ["APP_ENV"]` for the modules that still read it directly (schema selection,
> the rate limiter, the bandit scheduler). If you export `ENVIRONMENT=development` in your
> shell and then run the tests, `backend/tests/conftest.py` deliberately pops it so
> `APP_ENV=test` wins. Know both names; prefer `ENVIRONMENT` in anything new.
>
> And set one of them. **The API refuses to serve when `ENVIRONMENT` (or the legacy `APP_ENV`)
> was never set**: the lifespan raises `EnvironmentNotSet` rather than guess, so a bare
> `uvicorn backend.app.main:app` stops at start-up. `make dev` sets `ENVIRONMENT=development`
> for you.

### How Experimently does it

The everyday commands, from the repository root:

```bash
# one-time
python3.11 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# PostgreSQL (and Redis) via the compose file — or your own local Postgres on 5432
docker compose up -d postgres redis

# create the schema from the models and stamp the alembic heads (fresh DB)
export POSTGRES_DB=experimentation POSTGRES_SCHEMA=experimentation
ENVIRONMENT=development python -m backend.app.db.bootstrap

# run the API (from the repo root — the package is backend.app)
ENVIRONMENT=development uvicorn backend.app.main:app --reload --port 8000

# run the dashboard
cd frontend && npm install && npm run dev      # http://localhost:3100
```

The repository's `Makefile` wraps the same steps — `make venv`, `make db` (Postgres + Redis
via compose), `make bootstrap`, `make dev` (API with reload, `ENVIRONMENT=development`),
`make web` (dashboard), `make test` — plus `make up` (the core stack in Docker),
`make test-sdk` (every SDK's hash against the golden vectors, Day 4) and `make core-build`
(prove the core profile stands alone). Its header states the one rule the whole toolchain rests on: _"everything
runs from the repository root; the backend is imported as `backend.app.*`."_

Three things in that sequence deserve a closer look.

**The bootstrap, not `alembic upgrade heads`, for a fresh database.** The core migration
chain under `backend/app/db/migrations/versions/` was autogenerated over many months against
databases that already had tables, and it cannot be replayed from zero (several revisions
re-create tables earlier revisions already created). The modules have their own branch,
`modules/backend/app/db/migrations/versions/`, so a full checkout has **two heads**. The team
adopted the Alembic cookbook's answer:

```python
# 📁 backend/app/db/bootstrap.py
"""
Bring a PostgreSQL database up to the current schema.

Two situations are handled:

* **Existing database** (has an ``alembic_version`` table): run
  ``alembic upgrade heads`` so pending incremental migrations are applied.

* **Fresh database** (no ``alembic_version`` table): create the schema from
  the SQLAlchemy models with ``Base.metadata.create_all`` and then
  ``alembic stamp heads``.

``heads``, plural: a full checkout has two -- the core chain and the
``modules`` branch under ``modules/backend/app/db/migrations`` (issue #89) --
and alembic refuses the singular ``head`` when more than one exists.
```

Two heads is the normal state, not a problem to fix: never `merge` them. A merge revision
would land in the core versions directory and point at a modules revision, and every core
checkout would then fail to load its migrations at all. Use `heads` (plural) with `upgrade`
and `stamp`, and name the head a new revision extends (`--head <core head id>` or
`--head modules@head`).

The bootstrap also creates the first admin from `FIRST_SUPERUSER` /
`FIRST_SUPERUSER_PASSWORD` when the `users` table is empty, so a fresh install can sign in with
`AUTH_PROVIDER=local` without a seed script. The settings defaults are
`admin@example.com` / `admin`; the compose file sets `admin@demo.com` / `Demo1234!`. A weak or
well-known first-admin password is accepted only when `ENVIRONMENT` was explicitly set to
`development` or `test`; anywhere else the bootstrap refuses to create the account. The
models are the source of truth for the schema — exactly as they are for the test suite, which
also uses `create_all`.

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
a network. (A local user changes their own password with `POST /api/v1/users/me/password`; the
full profile adds a third way in, SSO sign-in with OIDC.)

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
# → {"status": "healthy", "timestamp": "...", "profile": "core" or "full",
#    "version": "...", "environment": "development",
#    "checks": {"database": {...}, "redis": {...}, "disk": {...}, "modules": {...}}}
# (503 with "status": "unhealthy" when not ready; production omits the details)

# the API docs — every router, every schema
open http://localhost:8000/api/v1/docs

# see the schema selection happen at import time (POSTGRES_SCHEMA wins when set)
python -c "from backend.app.core.database_config import get_schema_name; print(get_schema_name())"
env -u POSTGRES_SCHEMA APP_ENV=test TESTING=true python -c "from backend.app.core.database_config import get_schema_name; print(get_schema_name())"
POSTGRES_SCHEMA=scratch python -c "from backend.app.core.database_config import get_schema_name; print(get_schema_name())"
# → experimentation, then test_experimentation, then scratch
```

Then run the smallest meaningful slice of the test suite to confirm the harness works on your
machine (it creates and drops its own `experimentation_test_<pid>` database):

```bash
export APP_ENV=test TESTING=true
python -m pytest backend/tests/unit/core/test_scheduler_lock.py -p no:cov -q
```

### Key takeaways

- Backend = Python 3.11 venv + PostgreSQL on `localhost:5432` + a few env vars; Redis optional.
- The schema name is decided at import time: `POSTGRES_SCHEMA` if set, otherwise from
  `APP_ENV`/`TESTING`; set env **before** importing the app.
- Fresh database → `python -m backend.app.db.bootstrap` (create_all + `stamp heads`);
  existing → it runs `alembic upgrade heads` for you. A full checkout has two heads; never
  merge them.
- Run uvicorn from the repo root as `backend.app.main:app`, with `ENVIRONMENT` set (or use
  `make dev`); the dashboard defaults to `http://localhost:8000` in dev.
- `ENVIRONMENT` is canonical, `APP_ENV` legacy, and one of them must be set; the dev bypass
  only exists in development/test.

### Q&A

**Q. I ran `alembic upgrade heads` on an empty database and it failed halfway. Is the
migration chain broken?**
It's not replayable from zero, by history rather than by design — that's exactly why the
bootstrap exists. Use `python -m backend.app.db.bootstrap`. On an existing database it
_does_ run `alembic upgrade heads`, so incremental migrations still work the normal way. (And
if you typed the singular `head` and got "Multiple head revisions are present", that is the
reminder to type `heads`, not a reason to merge.)

**Q. My tests can't find tables that definitely exist. I can see them in `psql`.**
Check the schema. Tests run in `test_experimentation` inside a per-process database; you're
probably looking at `experimentation`. Also check that `POSTGRES_SCHEMA` (or
`APP_ENV`/`TESTING`) was set before anything imported `backend.app` —
`backend/tests/conftest.py` does that, but an ad-hoc script must too.

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

The core workflow uses five tables in the `experimentation` schema. This lesson examines their
columns, relationships and constraints. The constraints are particularly important: they enforce
invariants even when several requests or processes write to the same database.

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
  `(experiment, user)`, created once and changed only by a deliberate admin reassignment.
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
- `ck_experiments_resume_only_when_paused`: an experiment may carry a `resume_at` time only
  while it is paused.

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
    events = relationship(
        "Event", back_populates="experiment", cascade="all, delete-orphan"
    )
```

Two things to notice. First, the **`key`** column: it is what the SDKs send
(`experiment_key: "hero_banner"`), it's unique, and it's generated from the name on create when
omitted (`"Button Color Test"` → `button-color-test-3f9a2c`). An experiment without a key is
invisible to the tracking API — which was another of the ShopLab bugs: the demo seed never set
it. Second, the **relationships with `cascade="all, delete-orphan"`**: deleting an experiment
through the ORM deletes its variants, metrics, assignments _and events_. In practice that is
rare, because the API deletes only **draft** experiments (anything else answers 400), and a
draft has no traffic.

The status itself is guarded the same way. It changes **only** through the lifecycle actions
`POST /api/v1/experiments/{id}/start`, `/pause`, `/complete` and `/archive`; a `PUT` that tries
to change the status or the schedule is refused with 400, and targeting rules are editable only
while the experiment is a draft or paused. A paused experiment may carry a `resume_at` time,
which the experiment scheduler acts on; leaving `paused` clears it.

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
percentage 0–100.

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
    created_at = Column(UTCTimestampString, nullable=False, index=True)
```

Note the foreign keys' `ondelete="SET NULL"` (a raw SQL delete of an experiment leaves its
events in place; an ORM delete, as above, cascades them), the denormalised `variant_id` (so
the results engine can find a variant's events without a join), and — a real wart —
`created_at` as a **string** column holding an ISO timestamp, overriding the mixin's
`DateTime`. The wart is contained by `UTCTimestampString`, a `TypeDecorator` that normalises
every bound value, on insert and in every comparison, to one canonical UTC ISO-8601 string
(any offset is converted; a naive time is taken as UTC), so string order is time order.
Stored event times were rewritten to that form when it was introduced.

> **Did You Know?** The `metrics` table has a `lower_is_better` flag and an
> `event_value_path` (a JSON path into the event's metadata for revenue-style metrics), and
> the `events` table has `value` — but `/results` analyses **every** metric as a proportion:
> the share of assigned users who sent at least one matching event, compared with Fisher's
> exact test. Revenue included. Mean metrics, analysed with Welch's t-test from sufficient
> statistics (`sufficient_stats_analysis.mean_metric_result`), are used only by the warehouse
> module (full profile, beta). When you read `calculate_metric_results` on Day 6 and see only
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

Finally, list every core model the platform registers. `register_core_models()` in
`backend/app/models/__init__.py` imports every module named in `CORE_MODEL_MODULES`, so
standalone scripts can configure the mappers (an import list like it was one of the ShopLab
fixes); the modules register their own models through a hook:

```bash
sed -n '/^CORE_MODEL_MODULES/,/^)/p' backend/app/models/__init__.py
```

### Key takeaways

- Five nouns, five tables: experiments, variants, metrics, assignments, events — all UUID-keyed
  with `created_at`/`updated_at`.
- Invariants that _must_ hold are database constraints (unique `(experiment, user)`
  assignment, allocation 0–100, end after start); friendlier rules are Pydantic validators.
- `experiment.key` is the SDK's handle; no key, no tracking.
- `user_id` on assignments and events is an opaque external string, never a platform user.
- Events carry a denormalised `variant_id` and a canonical UTC ISO string `created_at`.
- Status changes only through start/pause/complete/archive; only drafts can be deleted.

### Q&A

**Q. Why is `Experiment.key` nullable if the SDKs can't work without it?**
History: the column was added after experiments existed. The service generates one on create
when none is given (the name slugged, plus six hex characters), and the demo seeds set theirs
explicitly. Treat "an experiment with a null key" as data that predates the tracking
API.

**Q. What's in `Variant.configuration` and who reads it?**
Whatever your app needs to render that arm — `{"media": "video", "headline": "..."}` in
ShopLab's hero experiment. `POST /tracking/assign` returns it verbatim in the
`configuration` field, and the React SDK's `useExperiment` hands it to the component. The
platform never interprets it.

**Q. Can a user be in two variants of the same experiment?**
No — the unique index makes it impossible at the database level, and `assign_user` returns
the existing row before ever hashing. Reassignment exists (`AssignmentService.reassign_user`)
for admin overrides, and it updates the existing row in place, because the
`(experiment_id, user_id)` pair is unique.

**Q. Why store `context` on the assignment?**
It's the targeting context the SDK sent at assignment time. It lets you audit _why_ a user
was eligible, and the dimensional-analysis breakdowns (Week 3) can segment by attributes that
were true at assignment rather than at conversion.

---

## Day 4 — Assignment: deterministic hashing and sticky rows

### What & why

Assignment must satisfy four requirements. The implementation has to preserve them together:

1. **Deterministic.** The same user must get the same variant every time, on every device,
   from every SDK, even before the platform has stored anything about them.
2. **Uniform and proportional.** Over many users, variants receive traffic in the configured
   proportions (50/50, 90/10, …) with no systematic bias.
3. **Sticky.** Once a user has been assigned, changing the traffic split, the targeting rules
   or the holdout must not move them — a user who saw the blue button keeps seeing it.
4. **Eligible.** New users must pass the gates — global holdout, mutual-exclusion group,
   targeting rules — before they are bucketed at all, and ineligible users must get a
   sensible default without polluting the experiment's data.

Property 1 rules out random assignment and rules in hashing: `hash(user_id, experiment.key)`
is a function, so it's the same everywhere. Property 3 rules out _only_ hashing — because the
inputs to eligibility change over time — and rules in a stored row that is consulted first.
Experimently does both: hash to decide, store to remember, and always prefer the stored row.

#### The hash: first four bytes of MD5, little-endian, divided by 2³²

The bucket is `bucket_of(user_id, experiment.key)`, from `backend/app/core/consistent_hash.py`:

1. concatenate `"{user_id}:{key}"`, where `key` is the experiment's public **key**
   (`shoplab_hero_banner`), falling back to its UUID only when the key is empty;
2. UTF-8 encode it and take the MD5 digest (16 bytes);
3. read the **first four bytes** as a little-endian unsigned 32-bit integer;
4. divide by 2³² to get a position in `[0, 1)`, and multiply by 100 for an integer bucket in
   `[0, 100)`.

MD5 is cryptographically broken and that is irrelevant here — it's used as a fast,
well-distributed, universally available bucketing function (`usedforsecurity=False` tells
Python and its linters exactly that). What matters is that the same input produces the same
bytes in Python, Go, Java, Swift and JavaScript. The key in the input is the salt: the same
user lands in different buckets for different experiments, so being in "treatment" of one
test says nothing about another.

```python
# 📁 backend/app/core/consistent_hash.py
def hash_user(user_id: str, key: str) -> float:
    """A stable float in [0.0, 1.0) for *user_id* under *key*.
    ...
    >>> round(hash_user("user-123", "my-flag"), 16)
    0.6927449859213084
    """
    digest = hashlib.md5(
        f"{user_id}:{key}".encode("utf-8"), usedforsecurity=False
    ).digest()
    (uint32,) = struct.unpack_from("<I", digest[:4])
    return uint32 / HASH_DIVISOR


def bucket_of(user_id: str, key: str) -> int:
    """The same position as an integer in [0, 100).
    ...
    >>> bucket_of("user-123", "my-flag")
    69
    """
    return int(hash_user(user_id, key) * BUCKET_COUNT)
```

A bucket in `[0, 100)` has the same granularity as `traffic_allocation` (an integer
percentage). Variants are laid out as a cumulative distribution — control `[0, 50)`, treatment
`[50, 100)` for a 50/50 split — and the bucket is looked up in it. Two consequences worth
internalising: changing a 50/50 split to 60/40 moves users in buckets 50–59 from treatment to
control (the sticky row is what protects them), and any allocation that doesn't sum to 100
leaves buckets that fall through to the last variant.

#### Why this function: four hashes that disagreed

This was not always the function. Until September 2026 the assignment service had its own:
MD5 of `"{user_id}:{experiment.id}"` read as one 128-bit integer, `% 100`. Meanwhile the SDKs'
golden-vector contract specified the first-four-bytes form over the experiment's _key_, and
the holdout and the Lambda copy each had their own. Nothing compared them, so they drifted.
For `user-123` under `my-flag` the contract gives bucket **69**; the full-digest form gives
**79**. And the old form hashed the experiment's UUID, which no client has, so an SDK could not
have agreed with the server even in principle. The consequence is the one an experimentation
platform cannot have: a user counted in variant A by the API and variant B by an SDK, with no
error anywhere.

The fix (commit `b46e5259`) moved assignment onto the contract's function. It was safe to change
because assignment is sticky: only users with no row yet are bucketed by the new function, so
no existing assignment moved. Today `tests/sdk-contract/golden-vectors.json` pins the function,
`make test-sdk` checks each SDK's implementation against it, and the module's docstring keeps a
table of every _other_ bucketing hash in the platform, each deliberately its own:

| What                               | Hash                                                        |
| ---------------------------------- | ----------------------------------------------------------- |
| Experiment assignment              | `bucket_of(user, experiment.key)`: first 4 bytes LE ÷ 2³²   |
| Feature-flag rollout               | `MD5("{user}:{flag.key}")`, **whole** digest, `% 100`       |
| Global holdout / mutual exclusion  | `MD5("{user}:{salt}")`, first 4 bytes LE, `% 100` / ÷ 2³²   |
| Bandit routing                     | `MD5("{user}:{experiment.id}:bandit")`, whole digest, `% 10000` |

So the flag rollout function is the one that still reads the whole digest (bucket 79 for
`user-123`/`my-flag`), and it did not change. Do not assume "one hash everywhere": changing any
of these re-buckets users, so none is changed in passing.

### How Experimently does it

The whole decision is one method:

```python
# 📁 backend/app/services/assignment_service.py
    def _hash_user_to_variant(
        self, user_id: str, experiment: Experiment
    ) -> Union[str, UUID]:
        """Assign *user_id* to a variant, by the hash every SDK implements.
        ...
        """
        if not experiment.variants:
            raise ValueError(f"Experiment {experiment.id} has no variants")

        namespace = experiment.key or str(experiment.id)
        bucket = bucket_of(user_id, namespace)

        # Cumulative distribution over allocations expressed 0-100.
        cumulative = 0
        for variant in experiment.variants:
            cumulative += variant.traffic_allocation
            if bucket < cumulative:
                return variant.id

        # Allocations summing to less than 100 leave a tail; the last variant
        # takes it, which is what the previous implementation did too.
        return experiment.variants[-1].id
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
        2. mutual exclusion — the experiment belongs to a group and either the
           group's consistent hashing selects a different experiment for the
           user, or the user already holds an assignment in another experiment
           of the group that is not completed or archived (reason
           ``mutual_exclusion``);
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
`user_id:<group salt>` to `[0, 1)`, excludes users at or above the group's
`traffic_allocation`, and carves the rest into equal slots, one per active experiment, so a
user is in at most one of the "checkout" experiments. The hash is not the whole rule: a user
who already holds an assignment in another experiment of the group that is not completed or
archived (active, paused and even draft siblings all keep their users) is ineligible too,
which `has_sibling_assignment` checks. Because the
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

Notice this one also takes the **first four bytes little-endian**, like `bucket_of`, but
`% 100` over a fixed salt rather than ÷ 2³² over the experiment key. Same reading of the
digest, different input and different reduction: the holdout bucket and the variant bucket
are independent functions, which is exactly what you want.

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

Prove determinism and stickiness against a running API. You need an API key. The ShopLab
seed (`python backend/scripts/seed_shoplab.py`, which `./demo/setup-local.sh` runs for you)
creates one named `shoplab-storefront` and writes it to a gitignored `.api_key` file in
`demo/shoplab/`; the `KEY=` line below reads it. If you started the stack with compose instead, the demo key is
the `SHOPLAB_API_KEY` value in `docker-compose.yml` (a public demo key, for local use only), or
create your own on the dashboard's Admin → API keys page.

```bash
API=http://localhost:8000/api/v1
KEY=$(cat demo/shoplab/.api_key 2>/dev/null || echo "<your key>")

# assign the same user three times: same variant every time
for i in 1 2 3; do
  curl -s -X POST $API/tracking/assign -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
    -d '{"experiment_key":"shoplab_hero_banner","user_id":"user-42"}' | python -c 'import sys,json; d=json.load(sys.stdin); print(d["variant_name"], d["assigned"], d["reason"])'
done

# reproduce the bucket offline, with the server's own function
python - <<'PY'
from backend.app.core.consistent_hash import bucket_of
for uid in ["user-42", "user-43", "user-44"]:
    print(uid, bucket_of(uid, "shoplab_hero_banner"))
PY
# → user-42 97, user-43 15, user-44 90
```

With the hero test's 50/50 split (`control` first, then `video_hero`), bucket 97 is
`video_hero` and bucket 15 is `control` — for a user with no row yet. A user the seeded
history or the simulator already assigned keeps whatever their row says.

Then read the unit tests that pin the gate order — they are the specification:
`backend/tests/unit/services/test_assignment_service_eligibility.py`.

### Key takeaways

- Assignment = `bucket_of(user_id, experiment.key)` (first 4 bytes of MD5, little-endian,
  ÷ 2³², × 100) → cumulative `traffic_allocation` lookup. Deterministic, uniform,
  proportional, and the same function the SDKs implement.
- Flag rollout is a _different_ function (whole digest `% 100`); so are the holdout, the
  mutual-exclusion and the bandit hashes.
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
input is exactly `user_id:experiment.key`. Two requests with the same user id and different
contexts get the same variant.

**Q. The SDK ships a `consistent_hash` too. Can I use it to know a user's variant before
calling the API?**
It is the same function, so for a new, eligible user with a 0–100 allocation you can predict
the bucket. But you can't predict the _answer_: a returning user keeps whatever their row says,
a holdout, mutual-exclusion group or targeting rule may make them ineligible (control,
`assigned: false`), and a bandit experiment routes new users by learned weights instead. Call
`/tracking/assign`; the SDK caches the answer.

---

## Day 5 — Event tracking and conversion matching

### What & why

An event records user activity: who performed an action, when it occurred and, where applicable,
its numeric value. The tracking API accepts these records from SDKs and attaches identifiers used
by analysis. Treating events as append-only records leads to three design decisions:

1. **Events are never updated.** There's no `PUT /events/{id}`. A wrong event is corrected by
   another event or by excluding it at analysis time, never by mutation — because the
   analysis has to be reproducible from the log.
2. **Events are resolved at write time.** The SDK sends an `experiment_key` (a human string);
   the API resolves it to an `experiment_id`, looks up the user's current assignment, and
   denormalises the `variant_id` onto the row. The results engine can then find a variant's
   events by `variant_id` alone, with no SQL join back to `assignments`. This is the
   write-once, read-many trade.
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

`_to_iso_timestamp` turns whatever the caller sent into an ISO string, and the column type
from Day 3, `UTCTimestampString`, then normalises it to canonical UTC, so every stored value
looks like `2026-09-11T14:03:22.118342+00:00` and sorts correctly as text.

#### Exposures

`assign_user` also writes an event — `track_exposure`, `event_type="exposure"` — every time a
user is assigned _or re-served from the sticky row_. Exposures are what the daily results
charts and the interaction-detection service use for "who actually saw the variant on day
N"; they are never conversions, which is exactly why `event_matching` excludes them by type.

> **By the Way.** The tracking endpoints also record two Prometheus counters —
> `events_tracked_total{event_type}` and `experiment_assignments_total{experiment_id,
> variant_id}` — through `backend/app/core/metrics.py`. Ineligible assignments are counted
> under their reason (`holdout`, `mutual_exclusion`, `targeting`) in the variant label, so
> you can see on a Grafana panel how much traffic your holdout is eating. The `event_type`
> label is bounded: a type outside `KNOWN_EVENT_TYPES` (`purchase`, say) is counted as
> `custom`, so an SDK cannot blow up the metric's cardinality. Week 4 covers the metrics
> registry.
>
> **Watch Out!** When tracking fails unexpectedly, the API answers with a fixed message and the
> request ID (`failure_detail`), never the exception text; a 422 for a malformed event names the
> fields, never their values. Grep the API's logs for the request ID.

### Build It

Track an event for the user you assigned yesterday and watch the denormalisation happen:

```bash
curl -s -X POST $API/tracking/track -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"experiment_key":"shoplab_hero_banner","user_id":"user-42","event_type":"purchase","value":49.0,"metadata":{"sku":"pack-01"}}' \
  | python -m json.tool
```

The response carries `experiment_id` and `variant_id` — resolved by the API, not sent by you.
Now look at the row and confirm the two-column convention:

```bash
docker compose exec postgres psql -U postgres -d experimentation -c \
  "select event_type, event_name, variant_id is not null as has_variant, created_at from experimentation.events where user_id='user-42' order by created_at desc limit 5"
```

You should see an `exposure` row from the assignment and a `purchase`/`purchase` row from the
track call. Finally, confirm there's nowhere left that matches conversions by type:

```bash
git grep -n 'event_type == "conversion"\|event_type=="conversion"' backend/app modules
```

Exactly one line comes back, and it is the docstring in `event_matching.py` describing the
legacy fallback (`conversion_event_filter` with no metric `event_name`). The only code that
still compares `event_type` with `"conversion"` is that fallback, inside the helper.

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

Results combine assignment and event data. For each variant, the service reports assigned users,
converting users, a conversion rate and a statistical comparison with control. This lesson follows
that calculation and the checks used when producing a recommendation.

The primary path is deliberately simple statistics — a contingency table per variant vs
control, a test of whether the proportions differ, a confidence interval on each rate —
because simple statistics are the ones a product manager can reason about and an auditor can
reproduce. The advanced methods (sequential testing, CUPED, Bayesian, bandits) sit _beside_
the primary result as additional blocks, not instead of it. Day 19 and the
[statistics book](../experimentation-statistics/README.md) cover those; today is the spine.

#### Why Fisher's exact test, for every metric

For a conversion metric the natural question is whether two proportions differ, and `/results`
answers it one way: **Fisher's exact test** on a 2×2 table per treatment against control. It is
exact at any sample size, `scipy.stats.fisher_exact` is fast even on large counts, and one test
everywhere means one number a product manager can reason about and an auditor can reproduce.
Every metric on `/results` is analysed this way, as a proportion — the share of assigned users
with at least one matching event — whatever its `metric_type` (revenue included), and every
treatment's result reports `statistical_test_used: "fisher_exact"`.

> **Watch Out!** `AnalysisService` still defines `select_statistical_test`, `z_test_proportions`
> and `welch_t_test`, and you will find tests for them. Nothing on the results path calls them.
> Do not read the selection rule in `select_statistical_test` as what the dashboard does. Mean
> metrics (Welch's t-test from sufficient statistics) exist only in the warehouse module, in the
> full profile, as a beta.

### How Experimently does it

The spine is `AnalysisService.calculate_metric_results`. Read it as three steps: count assigned
users per variant, count **converting users** per variant (with the Day 5 helper), and hand the
counts to one function that computes rates, intervals and the test vs control.

```python
# 📁 backend/app/services/analysis_service.py
    def calculate_metric_results(
        self,
        experiment: Experiment,
        metric: Metric,
        confidence_level: float = 0.95,
    ) -> Dict[str, Any]:
        ...
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
                .filter(
                    Assignment.experiment_id == experiment.id,
                    Assignment.variant_id == variant_id,
                )
                .scalar()
                or 0
            )
            assignments[variant_id] = count

        # Converting users per variant (event_matching.py): a user with
        # several conversion events counts once, so conversions <= assignments.
        conversions = {}
        for variant_id in variant_ids:
            conversions[variant_id] = count_converting_users(
                self.db, experiment.id, variant_id, metric.event_name
            )

        # Rates, intervals and Fisher p-values from the counts
        results = binomial_variant_results(
            [
                (variant, assignments[str(variant.id)], conversions[str(variant.id)])
                for variant in experiment.variants
            ],
            confidence_level,
        )
```

The denominator is the number of `assignments` rows — the stored rows from Day 4. The
numerator is `count_converting_users` from `event_matching.py`: the users **assigned to that
variant** who sent at least one event matching the metric (by `event_name`, exposures
excluded) tagged with that variant. A user who purchases three times counts once, and an event
from a user with no assignment to that variant does not count, so the numerator can never
exceed the denominator. Every analysis path that reports `conversions` next to a
`sample_size` uses that one definition.

It also does it without a SQL join. Reading converting users from `events` and then looking
their assignments up by the unique `(experiment_id, user_id)` index, a bounded number of ids at
a time, replaced a join that PostgreSQL could run as a nested loop over a sequential scan on a
freshly seeded database (more than 8 seconds on 30,000 users in CI).

The test and the interval live in `sufficient_stats_analysis.binomial_variant_results`:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
    z = two_sided_z(confidence_level)
    alpha = 1.0 - confidence_level
    ...
            contingency_table = [
                [variant_conversions, variant_non_conversions],
                [control_conversions, control_non_conversions],
            ]

            try:
                odds_ratio, p_value = stats.fisher_exact(contingency_table)
                is_significant = p_value < alpha
```

The request's `confidence_level` (0.80–0.99, default 0.95) drives **both** the interval and
the significance decision: the interval is the normal approximation
`p ± z·√(p(1−p)/n)` with `z = norm.ppf(1 − (1 − confidence_level)/2)` (1.6449 at 0.90, 1.96 at
0.95), clamped to `[0, 100]`, and a treatment is significant when `p_value < 1 −
confidence_level`. Effect sizes (Cohen's h, `2·asin√p₁ − 2·asin√p₀`) are labelled
negligible/small/medium/large so the dashboard can say "significant but negligible" — the most
useful sentence in experimentation.

<a id="the-honesty-checks"></a>

#### Result validation checks

The dashboard calls `GET /api/v1/results/{experiment_id}`. The endpoint applies the following
checks around the statistical calculation:

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
of observed assignment counts against `traffic_allocation`, flagged at p < 0.001. It is
computed only for `fixed` experiments: a bandit allocates from learned weights, so its counts
are _supposed_ to drift from the configured split, and its `srm` is null:

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

SRM is reported **beside** the recommendation, not inside it: `_summarise_decision` looks at
the primary metric's winner and at the sample size only, so an SRM warning does not change
`recommendation`. Reading the `srm` block is your job.

**Multiple-testing correction.** With several metrics, `correction_method=bonferroni|
benjamini_hochberg` adjusts the p-values, and the dashboard labels each p-value "unadjusted" or
"adjusted (Bonferroni|BH)" and shows one significance decision. The primary metric is listed
first.

**The sample-size gate.** Since the ShopLab fix, a significant winner on the primary metric is
only recommended as `SHIP_VARIANT` once _every_ variant has reached the primary metric's
`minimum_sample_size`; before that the recommendation stays `CONTINUE_TESTING`. (If the
experiment has Bayesian analysis on, its own winner needs a probability-to-be-best of at least
0.975, a fixed threshold; Day 19 has the details.)

**Caching with a status-aware TTL.** Results are cached in Redis for 5 minutes while an
experiment is active and 24 hours in any other status (paused, draft, completed, archived);
`use_cache=false` forces a recomputation, and `POST /results/{id}/invalidate-cache`
(superusers only) clears it. Best-effort: a Redis outage means recomputation, never an
error.

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

Now the same numbers from SQL. This is the join written out, counting distinct users and
only events from users assigned to the variant the event is tagged with
(`event_matching.py` exports the same predicate as `CONVERTING_USERS_SQL` and
`CONVERTING_USERS_JOIN`). It should match `sample_size` and `conversions` per variant exactly:

```sql
-- docker compose exec postgres psql -U postgres -d experimentation
with m as (select event_name from experimentation.metrics where experiment_id = '<EXP>' and is_primary limit 1)
select v.name,
       (select count(*) from experimentation.assignments a where a.variant_id = v.id) as assigned,
       (select count(distinct e.user_id)
          from experimentation.events e
          join experimentation.assignments a
            on a.experiment_id = e.experiment_id and a.user_id = e.user_id and a.variant_id = e.variant_id
         where e.experiment_id = v.experiment_id and e.variant_id = v.id
           and e.event_name = (select event_name from m)
           and e.event_type not in ('exposure','experiment_exposure')) as conversions
from experimentation.variants v where v.experiment_id = '<EXP>';
```

Feed the four counts into `scipy.stats.fisher_exact` and compare the p-value with the API's.
Then change `count(distinct e.user_id)` to `count(*)` and see by how much counting events
would have overstated the primary metric.

### Key takeaways

- A result is assigned users and **converting users** per variant (`count_converting_users`:
  each assigned user once, at most), Fisher's exact test vs control, and a confidence interval
  — the Day 3 join, computed.
- One test on `/results`: Fisher's exact, for every metric, analysed as a proportion.
  `confidence_level` sets both the interval and the significance threshold.
- Honesty checks wrap every result: SRM (chi-square, p < 0.001, fixed experiments only,
  reported beside the recommendation), multiple-testing correction, and minimum-sample-size
  gating of the recommendation.
- Results are cached with a status-aware TTL and snapshotted for audit.
- Monte Carlo is seeded per `(experiment, day, n_samples)`; the engine has a version.

### Q&A

**Q. Fisher's exact test on a million users? Isn't that slow?**
`scipy.stats.fisher_exact` on a 2×2 table is fast even for large counts, and at scale its
p-value is close to a z-test's anyway. The expensive part of a results request is the counting
queries, which is why the endpoint caches.

**Q. The dashboard says "significant" but the recommendation is still "keep running". Why?**
Minimum sample size. A metric declares `minimum_sample_size` (default 100), and the
`SHIP_VARIANT` recommendation requires every variant to have reached the primary metric's. A p-value under 0.05
with 40 users a side is exactly the kind of result that reverses next week.

**Q. What does an SRM warning tell me to do?**
Stop trusting the result and go find the leak: a targeting rule that applies to one variant's
rendering path, an SDK version that fails to assign on one platform, a bot that only hits
one URL. The chi-square tells you the split is wrong; it can't tell you why.

**Q. Why cache results for 24 hours on a completed experiment but only 5 minutes on an active
one?**
An experiment that is not active takes no new assignments, so recomputing is mostly waste;
an active one accrues data continuously and a 5-minute lag is the trade between freshness and
load. The rule in `results.py` is "active: 5 minutes, any other status: 24 hours", so a paused
experiment gets the long TTL too. Both are bypassed by `use_cache=false`.

---

## Day 7 — Capstone: trace `assign → track → results` end-to-end

### What & why

This exercise traces one experiment across the layers covered in Days 3–6. Start at the React SDK
hook in ShopLab, follow the request through the API and storage services, and finish at the
`p_value` displayed in the results page. Identify the file responsible for each step.

The experiment is ShopLab's **hero A/B** (`shoplab_hero_banner`): variant `control` shows an
image hero, variant `video_hero` shows a video hero, 50/50. The primary metric is the hero's
call-to-action click (`event_name: "hero_cta_click"`); `purchase` is a secondary metric. Both
declare a `minimum_sample_size` of 2,000.

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
`POST /api/v1/tracking/assign` with `{"experiment_key": "shoplab_hero_banner", "user_id":
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
demo has none. Mutual exclusion? `shoplab_hero_banner` isn't in a group. Targeting rules?
None on this experiment. Hash: `bucket_of("<visitor>", "shoplab_hero_banner")` → say `73` →
`video_hero` (`[50, 100)`). Insert the `assignments` row; write an `exposure` event.

**6. The response reaches the hook.** `{"variant_name": "video_hero", "is_control": false,
"configuration": {"media": "video", ...}, "assigned": true, "reason": "assigned"}`. The SDK
caches it per `(user, key)`; `readHeroConfig` picks `media = "video"`; the page re-renders
with the video hero. `experiment_assignments_total{variant_id=...}` ticks up in Prometheus.

**7. The visitor clicks the hero.** The home page calls
`track('hero_cta_click', { variant: hero.variantName, media })`. The event has no explicit
key, so the SDK fans it out to every cached assignment — including `shoplab_hero_banner` — and
sends `POST /api/v1/tracking/track` (or a batch) with `event_type: "hero_cta_click"`. (If they
go on to buy, the checkout's `track('purchase', ...)` takes the same path and feeds the
secondary metric.)

**8. The API stores the event.** `/track` resolves `shoplab_hero_banner` → `experiment_id`,
finds the visitor's assignment → `variant_id`, builds an `EventCreate` with
`event_name = "hero_cta_click"` (copied from `event_type`), and `EventService.track_event`
inserts the row with a canonical UTC timestamp.

**9. The dashboard asks for results.** The results page calls `GET /api/v1/results/<id>`
with the admin's bearer token. Cache miss (or `use_cache=false`) →
`AnalysisService.get_experiment_results` → for each metric, primary first,
`calculate_metric_results`: assignments per variant, `count_converting_users(...,
"hero_cta_click")` per variant (each assigned visitor once, however many times they clicked),
Fisher's exact test vs control, a normal-approximation interval at the requested confidence
level.

**10. The validity checks run.** `_compute_srm` chi-squares the assignment counts against
50/50 and reports the result in `srm`; `sample_size_adequate` checks every variant against
the primary metric's `minimum_sample_size` (2,000 here); the summary recommends
`SHIP_VARIANT` only if the primary metric has a significant winner _and_ the sample size is
adequate. An SRM warning is shown beside the recommendation; it does not change it.

**11. The response is cached and snapshotted**, then rendered by
`frontend/src/components/results/` — the metric comparison table, the confidence-interval
chart, and (because `shoplab_hero_banner` has sequential testing and Bayesian analysis
enabled) the extra blocks Day 19 explains.

### Build It

Run the trace yourself with the simulator off, so every row is yours:

```bash
./demo/setup-local.sh            # once; then stop the shoplab simulator process
open http://localhost:3200       # visit as a new visitor; note the hero you got
# click the hero's call to action; then:
docker compose exec postgres psql -U postgres -d experimentation -c \
  "select e.event_type, e.event_name, v.name from experimentation.events e join experimentation.variants v on v.id = e.variant_id
    where e.experiment_id = (select id from experimentation.experiments where key='shoplab_hero_banner') order by e.created_at desc limit 5"
open http://localhost:3100/results   # find the hero experiment; your click is one converting user
```

Then break it on purpose, to feel the seam. Use a **fresh** visitor id that you assign through
`/tracking/assign` (so it has an assignment row and no events yet), then, in a scratch script,
insert two events for it, each carrying the hero experiment's `experiment_id` and the
visitor's assigned `variant_id`: one with `event_type = "conversion"` and
`event_name = "hero_cta_click"`, and one with `event_type = "hero_cta_click"` and
`event_name = "page_view"`. Refresh results with `use_cache=false`. The primary metric's
`conversions` for that variant goes up by exactly one: the first event matches on
`event_name`, the second does not. Now insert the first event again: the count does not move,
because the visitor already counts. And an event tagged with the _other_ variant's id does
not count at all, because the visitor is not assigned to it. That is Days 5 and 6 in one
experiment.

### Key takeaways

- The hop list: SDK hook → `/tracking/assign` → `get_api_key` → experiment by key →
  `assign_user` (sticky → gates → hash) → row + exposure → SDK cache → app renders.
- Then: `track` (fan-out) → `/tracking/track` → key → id, assignment → `variant_id` →
  `EventService` → row.
- Then: `/results/{id}` → `calculate_metric_results` (assigned users, converting users,
  Fisher, CI) → SRM beside, sample-size gate on the recommendation → cache + snapshot →
  dashboard.
- Every layer is a file you can open; every number is a query you can rerun.

---

## Week 1 Workshop

### Quiz

**1. What are the two inputs to the variant hash, and why is the experiment's key one of
them?**
`user_id` and `experiment.key` (the UUID only when the key is empty), joined with a colon. The
key acts as a salt so a user's bucket in one experiment is independent of their bucket in
another; without it, every 50/50 test would put the same users in "treatment". It is the
public key rather than the UUID so that an SDK, which only knows the key, computes the same
bucket.

**2. A user was assigned to treatment last week. Today you add a targeting rule they don't
match. What does `/tracking/assign` return for them?**
Their existing treatment assignment. The sticky row is consulted before eligibility;
`assigned: true, reason: "assigned"`.

**3. An SDK tracks `event_type: "signup"`. What are `event_type` and `event_name` on the
stored row, and which column does a metric with `event_name: "signup"` match on?**
Both are `"signup"` (`/track` copies `event_type` into `event_name` when the latter is
absent). The metric matches on `event_name`, via `conversion_event_filter("signup")`.

**4. Which three validity checks wrap a results response, and which of them can change the
recommendation?**
Sample-ratio mismatch (chi-square, warning at p < 0.001, for fixed-allocation experiments),
multiple-testing correction (Bonferroni / Benjamini-Hochberg), and the minimum-sample-size gate
on the `SHIP_VARIANT` recommendation. Only the sample-size gate (with the primary metric's
winner) decides the recommendation; SRM is reported beside it.

**5. Why does an ineligible user get HTTP 200 rather than 403 or 404?**
Because not being in the experiment is the normal outcome, not an error: the app should
render the default experience. The response carries the control variant, `assigned: false`
and a `reason`, and nothing is recorded for the user.

**6. Why can't `alembic upgrade heads` build a fresh database, and why `heads`?**
The historical core migration chain was autogenerated against databases that already had
tables and re-creates some of them; it isn't replayable from zero.
`backend.app.db.bootstrap` does `create_all` from the models and `alembic stamp heads`
instead. `heads`, plural, because a full checkout has two: the core chain and the `modules`
branch. They are never merged.

**7. For `user-123` under the key `my-flag`, the experiment-assignment hash gives bucket 69 and
the flag-rollout hash gives 79. Why do they differ?**
Assignment (`bucket_of`) reads the first four bytes of the MD5 digest as a little-endian
integer and divides by 2³²; flag rollout reads the whole 128-bit digest as one integer, `% 100`.
Same input, same digest, different reading of it.

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

**E1.4 — Spot the numerator.** `calculate_metric_results` counts conversions with
`count_converting_users`, not with a count of events. Describe two situations where counting
events would give a different number, and say why the platform also ignores events from users
who are not assigned to the variant the event is tagged with.

**E1.5 — Reproduce the p-value.** For a variant with 1,000 assigned / 52 converting users
against a control with 1,000 / 40, compute the Fisher exact p-value and the two-proportion
z-test p-value in a Python shell using `scipy.stats`. Are they close? Which one does `/results`
report, and is either significant at the default `confidence_level`? At 0.80?

**E1.6 — Reproduce a bucket.** Compute `bucket_of("user-123", "my-flag")` by hand from the MD5
digest (no import of `consistent_hash`), then compute the flag-rollout bucket for the same
input. Check your first answer against `tests/sdk-contract/golden-vectors.json`.

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

**S1.4** — (1) A user who converts twice (two purchases) would contribute two events to the
numerator but one assignment to the denominator, so a "rate" could exceed 100% in principle
and is inflated in practice for any repeatable event. (2) An event can be tagged with a
variant the user is not assigned to (a hand-inserted row, a backfill, a mis-tagged import);
counting it would credit an arm with a conversion by someone who never saw it. Counting users
assigned to the event's variant keeps the numerator a subset of the denominator, so the rate
is a real proportion and Fisher's 2×2 table is well-formed. That is why
`count_converting_users` is the one definition every analysis path uses.

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
print(round(p_fisher, 4), round(p_z, 4))   # 0.2402 and 0.2002 — same conclusion
```

`/results` reports the Fisher value, 0.2402, with `statistical_test_used: "fisher_exact"`; no
dashboard shows the z-test's. Neither is significant at the default 0.95 (threshold 0.05), and
neither is at 0.80 either (threshold 0.20, and 0.2402 > 0.20). The z-test's 0.2002 sits just
above that threshold, a reminder that the choice of test can matter at the margin, and one
reason the platform uses one test everywhere.

**S1.6**

```python
import hashlib, struct
digest = hashlib.md5(b"user-123:my-flag").digest()
(u32,) = struct.unpack("<I", digest[:4])
print(int(u32 / 2**32 * 100))                          # 69, as bucket_of gives
print(int.from_bytes(digest, "big") % 100)             # 79, the flag-rollout reading
```

The golden vector for `user-123` / `my-flag` is `0.6927449859213084`, which × 100 truncates
to 69.

---

**Next:** [Week 2 — The API and the Data →](./week-2-api-and-data.md)

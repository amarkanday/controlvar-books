# Troubleshooting Guide

### _Symptom → cause → fix, for Experimently_

[← experimently-docs](../README.md) · [Onboarding](./onboarding.md) · [Usage guide](./usage.md)

> Verified against `getexperimently/experimently` at `a4a6dbd2` (0.17.0 plus unreleased changes,
> including three breaking ones: #747, #769, #770).

Organised by where you are when it breaks. Each entry is a symptom you'd actually see, the
mechanism behind it (with the file that implements it), and the fix. When an entry says "by
design", the behaviour is intentional and the fix is to change what you're doing, not the
platform.

Quick index: [Local setup](#1-local-setup) · [Tests](#2-tests) · [Database & migrations](#3-database--migrations)
· [Auth & permissions](#4-auth--permissions) · [The tracking API & SDKs](#5-the-tracking-api--sdks)
· [Feature flags & targeting](#6-feature-flags--targeting) · [Results & statistics](#7-results--statistics)
· [Schedulers, safety, rollouts](#8-schedulers-safety--rollouts) · [The dashboard](#9-the-dashboard)
· [Containers & compose](#10-containers--compose) · [CI](#11-ci) · [AWS](#12-aws)

---

## 1. Local setup

**`ModuleNotFoundError: No module named 'backend'` / `No module named 'app'`**
You're not at the repository root, or you ran `uvicorn app.main:app`. Everything imports
`backend.app.*` and runs from the root: `uvicorn backend.app.main:app` (or `make dev`). If you
see `from app.models...` in code, that _is_ the bug — fix the import (see the metrics-import
rule below).

**The API refuses to start: no `ENVIRONMENT`**
By design: the API starts only with an explicit `ENVIRONMENT` (`make dev` sets
`ENVIRONMENT=development`). `TESTING` cannot be combined with a staging or production
environment, and the image refuses `ENVIRONMENT=test`.

**`ImportError` / version errors on startup (`fastapi`, `pydantic`, `sqlalchemy`)**
The venv isn't active or is stale. `source venv/bin/activate` (always), then
`pip install -r backend/requirements.txt`. The pins are exact for a reason — FastAPI 0.141.1,
Pydantic 2.13.5, SQLAlchemy 2.0.54. The images install the hashed lock
`backend/requirements/runtime.lock` instead; after changing a runtime pin, `make lock`.

**`sqlalchemy.exc.InvalidRequestError: Class ... is not mapped` or two classes with the same
name**
A model was imported by two different paths (`backend.app.models.metrics.metric` and
`app.models.metrics.metric`), so SQLAlchemy registered it twice. Fix the import to the
`backend.app` root; `grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'`
lists every offender (there is no script for this). Also make sure a new core model is listed
in `CORE_MODEL_MODULES` in `backend/app/models/__init__.py` (`register_core_models()` imports
them so mappers can resolve relationship strings by name); a module's models are registered
through `hooks.register_model_module()` in `modules/backend/app/register.py`.

**`relation "experimentation.experiments" does not exist`**
Either the schema was never created (run `python -m backend.app.db.bootstrap`) or you're
connected to the wrong schema: the app uses `experimentation`, tests use
`test_experimentation` in a per-process `experimentation_test_<pid>` database. The choice is
made at import time from `APP_ENV`/`TESTING` (`backend/app/core/database_config.py`) — set the
variables before importing the app.

**`ENVIRONMENT=... and APP_ENV=... disagree` warning**
Both are set and differ. `ENVIRONMENT` wins; remove `APP_ENV` from your shell/env file.
(`dev`/`prod` are accepted with a deprecation warning and canonicalised.)

**The API refuses to start: `SECRET_KEY must be at least 32 characters ...` /
`DEV_AUTH_BYPASS=true is not permitted when ENVIRONMENT='production'`**
By design — the settings validators fail closed in production
(`backend/app/core/config.py`), and production start-up lists **every** missing or refused
setting at once. Generate a real key
(`python -c "import secrets; print(secrets.token_hex(32))"`), and never set the bypass outside
`development`/`test`. The same guards cover `FIRST_SUPERUSER_PASSWORD`; the modules' secrets
(`AUDIT_HMAC_KEY`, `SSO_STATE_SECRET`) are checked by the start-up preflight rules.

**`400 Invalid host` (the dashboard explains a rejected Host)**
Staging and production answer only to the hosts they know. Set `PUBLIC_BASE_URL` (the URL
people type to open the dashboard; its host is then allowed) or `ALLOWED_HOSTS`. Accepted
patterns are `example.com` and `*.example.com`; `*example.com` (no dot) matches nothing and
is refused, and `*` is refused in staging and production.

**A setting I exported doesn't take effect**
`Settings` uses `extra="ignore"`, so a misspelled variable is silently dropped. Check the
exact name in `core/config.py` (case-sensitive), then verify in a shell:
`python -c "from backend.app.core.config import settings; print(settings.SAFETY_CHECK_INTERVAL_MINUTES)"`.

**`CORS_ORIGINS` isn't applied / the browser reports a CORS error**
`CORS_ORIGINS` is a comma-separated string (annotated `NoDecode`); `BACKEND_CORS_ORIGINS`
must be a JSON array. Dev defaults include 3000, 3001, 3100, 3200 and 3300; staging and
production have **no** localhost defaults, so set `CORS_ORIGINS` there (`*` is refused). If
the failing request is from a browser SDK, check the preflight: `allow_headers` must include
`X-API-Key` (it does in `main.py`; a custom deployment behind a proxy may strip it).

**Redis errors in the log**
Redis is optional. The rate limiter falls back to an in-memory sliding window, the results
cache is best-effort, and `/health/ready` only fails on Redis when `REDIS_REQUIRED=true`.
Start it (`make db`) if you're working on rate limits or caching.

**Port already in use (5432, 6379, 8000, 3000)**
Compose: set `POSTGRES_HOST_PORT`, `REDIS_HOST_PORT`, `API_HOST_PORT`, `FRONTEND_HOST_PORT` in
`.env`. Running the compose stack next to the dev database: `docker compose -p experimently
up -d` with `POSTGRES_HOST_PORT=5433 REDIS_HOST_PORT=6381` so the pytest database on 5432 is
untouched.

---

## 2. Tests

**`pytest` collects nothing or errors at conftest import**
Run from the repository root (pytest config is `[tool.pytest.ini_options]` in
`pyproject.toml`; `testpaths` covers `backend/tests` and `modules/backend/tests`). Activate the
venv. Export `APP_ENV=test TESTING=true POSTGRES_SCHEMA=test_experimentation` (the conftest
sets the first two too, but your shell may have exported `ENVIRONMENT=development` — the
conftest pops it).

**`could not connect to server: localhost:5432`**
DB-backed tests need PostgreSQL on `localhost:5432` (or set `POSTGRES_PORT`/`POSTGRES_SERVER`;
the conftest honours them). That includes much of the **unit** suite: many unit test files
take the `db_session` fixture. `make db`. On macOS the host must be `localhost`, not a
container name.

**`psycopg2.errors.IntegrityError: null value in column "hashed_password"`**
Every test user needs `hashed_password` set (the local auth path assumes it). The `client`
fixture's superuser has one; your own fixtures must too.

**Tests pass alone and fail in a full run (list pagination, `aws_client` CloudWatch)**
Cross-suite pollution. Known root causes: `patch.object(SomeSchema, "__new__")` permanently
breaks the class in CPython — use `patch.object(SomeSchema, "model_validate")` or a fixture;
and committed rows leak because the root `db_session` fixture deliberately does **not**
truncate (TRUNCATE deadlocked against async fixture overlap) — unit tests must delete what
they commit. CI runs unit and integration in separate sessions for this reason.

**`server closed the connection unexpectedly` cascades**
The production engine's pool was alive when the test database was dropped. The `test_db`
fixture disposes it first; if you built your own engine in a test, dispose it in teardown.

**Coverage report is empty**
Coverage is deliberately not in `addopts`. Ask explicitly:
`python -m pytest backend/tests/unit -p no:cov` for speed, or
`python -m pytest backend/tests --cov=backend.app --cov-report=term` for numbers (set
`COVERAGE_FILE` if two runs share a directory).

**`unrecognized arguments: --cov`**
`pytest-cov` isn't installed in that environment (the SDK contract CI job installs only
`pytest`). Pass `-o addopts=""` or install `pytest-cov`.

**A module test fails with `ModuleNotFoundError: modules` or is skipped**
Tests that need a module are marked `@pytest.mark.modules` and skipped in a core build. Run
the modules' suite with `make test-modules` in a full checkout (it needs
`modules/requirements.txt` and `modules/requirements-test.txt`, which `make venv` installs).

**`smoke/test_production.py` fails with connection errors**
Expected offline — it targets a live server via `SMOKE_TEST_API_URL`.

**A scheduler ran in the middle of my test / `test_trigger_aggregation` flakes in CI**
Fixed: the lifespan does not start schedulers when `settings.is_test`. If you construct the
app with a non-test settings object in a test, you'll re-enable them.

**`moto` raises `AttributeError: module has no attribute 'mock_aws'`**
moto is pinned at 4.2.14: use `mock_dynamodb`, `mock_cognitoidp`, etc. (`mock_aws` is 4.3+).

**Cognito auth tests error at setup with rate-limiter patches**
`backend/tests/integration/auth/` runs under `AUTH_PROVIDER=cognito` with moto and — in CI —
**no Postgres**. Put any DB-backed auth test in `backend/tests/integration/api/` instead.

**Lambda tests: `ImportError` or `tests.conftest` conflicts**
Each function's `tests/` defines its own `tests.conftest`; run them one directory at a time
(`for d in backend/lambda/{event_processor,feature_flag_evaluation,shared}; do
python -m pytest "$d" -p no:cov -q; done`), exactly as the PR gate does. (These handlers are
deployed by no stack; see `backend/lambda/README.md`.)

**A feature-flag permission test fails with "coroutine was never awaited"**
Feature-flag and report dependencies are `async`: `@pytest.mark.asyncio`, `async def`,
`await deps.can_create_feature_flag(...)`. Experiment dependencies are sync — no `await`.
Every route that changes a flag decides with `can_act_on_feature_flag(user, owner_id, action)`
in `backend/app/core/permissions.py`; `can_update_feature_flag` and its siblings in `deps.py`
are used by no route, so a test built on them tests nothing real.

**Route enumeration test breaks after a FastAPI upgrade**
FastAPI ≥ 0.141 puts lazy `_IncludedRouter` entries (no `.path`) in `app.routes`. Enumerate
via `effective_route_contexts` — see `backend/tests/smoke/test_wiring.py::_iter_http_routes`.

---

## 3. Database & migrations

**`alembic upgrade heads` fails on an empty database (`relation already exists` /
`does not exist`)**
The chain isn't replayable from zero (older revisions re-create tables). Use
`python -m backend.app.db.bootstrap`, which does `create_all` from the models and
`alembic stamp heads` on a fresh database, and `alembic upgrade heads` on an existing one.

**`Multiple head revisions are present`**
Not a fault. A full checkout has **two heads** on purpose — the core chain and the `modules`
branch — so that deleting `modules/` leaves a consistent core chain. You asked `upgrade` for the singular
`head`; use `python -m alembic -c backend/app/db/alembic.ini upgrade heads`. A new
revision names the head it extends: `--head <core head id>` (from `alembic heads`, the one
without `(modules)`) or `--head modules@head`. **Never `alembic merge` the two heads**: the
merge file lands in the core versions directory with a `down_revision` naming the modules
revision, and every core checkout then fails to load its migrations at all.

**`Can't locate revision identified by '...'`**
A migration's `down_revision` names a revision that doesn't exist — often a migration _name_
instead of its _id_. Open the file and set `down_revision` to the id of the head it extends
(`alembic heads`).

**Autogenerate proposes creating or dropping everything / indexes and enums I didn't touch**
The commonest cause: `POSTGRES_SCHEMA` does not match the schema the models were imported
with, so autogenerate compares one schema's models with another schema's tables. Export the
same `POSTGRES_SCHEMA` (`test_experimentation` in the test shell) before generating. Otherwise
it is drift between the model and a database built by an older path: keep only the change you
intended. `backend/tests/integration/database/test_autogenerate_is_empty.py` is the standing
check that a freshly bootstrapped database produces an empty migration.

**An older image refuses to start against a newer database**
By design: it says so rather than guess. Run the newer image, or undo the newer migration with
the documented downgrade. Never delete rows from `alembic_version` to make it start. See
`docs/self-hosting/migrations.md` in the app repo.

**An upgrade refuses because two accounts' emails differ only in letter case**
By design: email addresses are now unique whatever their case. Resolve the pair as described in
`docs/self-hosting/migrations.md`, then upgrade again.

**`ALTER TYPE ... ADD VALUE cannot run inside a transaction block`** / enum casing mismatch
PostgreSQL enums are the sharp edge. `ExperimentStatus` values are lower-case,
`FeatureFlagStatus` upper-case; a past migration added lower-case values to fix a mismatch.
For a new enum, match the Python enum's `.value` exactly, or prefer a `String` column with a
`CheckConstraint` if the set will change.

**`TypeError: can't compare offset-naive and offset-aware datetimes`**
The columns are `DateTime` without timezone, so SQLAlchemy returns naive values; new code
uses `datetime.now(timezone.utc)`. Normalise at the comparison — `_as_utc` in
`backend/app/core/rollout_scheduler.py` is the pattern — or use naive UTC
(`datetime.utcnow()`) when writing to those columns as the safety service does.

**Experiment ids aren't found by `/tracking/assign` even though the experiment exists**
It needs a `key` and `status == active`. Experiments created before the key column existed
may have `key = NULL`; the demo seeds backfill it, the service generates it on create.

**Deleting an experiment fails with a foreign-key error**
Events use `ondelete="SET NULL"` (audit data survives); variants/assignments cascade. If a
FK error appears, a newer table references experiments without an `ondelete` — add one.

---

## 4. Auth & permissions

**Every request returns 401 `Not authenticated` in development**
Either log in (`POST /api/v1/auth/login` with `admin@demo.com`/`Demo1234!` after the bootstrap
or demo seed) and send `Authorization: Bearer <token>`, or — for local scripting only — set
`DEV_AUTH_BYPASS=true` with `ENVIRONMENT=development`. Tests run fail-closed (bypass off, local
provider) by design.

**Login returns 401 for a user that definitely exists**
Same message for unknown address, wrong password and inactive account (no enumeration).
Check `is_active`, and that the user has a bcrypt `hashed_password` (Cognito-provisioned users
have none and can't use the local provider). The email itself matches in any letter case.

**The dashboard won't sign in under `AUTH_PROVIDER=cognito`**
By design at this release: under Cognito the dashboard does not sign in; use the API. Sign-up
and confirmation answer 404 unless `COGNITO_SELF_SIGNUP_ENABLED` is set, and both
`COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` are required. SSO sign-in needs
`AUTH_PROVIDER=local`.

**Login returns 423 `account temporarily locked`**
Ten failures in fifteen minutes (`LOCAL_AUTH_MAX_FAILED_ATTEMPTS`, `LOCAL_AUTH_LOCKOUT_MINUTES`).
Wait for `Retry-After`. The tracker is in-process memory — restarting the API clears it.

**429 on `/auth/login` while testing**
Login is limited to 10/min per IP (`RATE_LIMIT_CONFIG` in `middleware/rate_limiter.py`).
Slow down, or run with `APP_ENV=test` (limiter disabled) for local scripting.

**A token that worked yesterday is rejected**
Local JWTs live 12 hours (`LOCAL_AUTH_TOKEN_TTL_MINUTES`). Also: a token is bound to
`SECRET_KEY` and issuer `experimently-local`; changing the key invalidates every token.

**`X-API-Key` returns 401 `Invalid API Key`**
Keys are stored as SHA-256 hashes and shown in plaintext once at creation. Check
`is_active`/`expires_at` on the row, and that the owning user is active. The demo images bake
`eptk_demo_shoplab_…`/`eptk_demo_streampulse_…`; the seeds write real keys to
`demo/<app>/.api_key`.

**API key works on `/tracking/*` but 401s on `/experiments/`**
By design: dashboard endpoints depend on `get_current_active_user` (bearer); SDK endpoints
depend on `get_api_key`.

**403 creating an API key, or 403 on `/sdk/ruleset`**
Scopes are enforced. A key with the `sdk:ruleset` scope (server-side local evaluation) can be
created only by ADMIN, DEVELOPER or a superuser; ANALYST and VIEWER get 403 and no key is
written. `/sdk/ruleset` answers 403 to a key without that scope: create a new key with it.

**403 `You don't have permission to ...`**
Access is by role, not ownership (`core/permissions.py`). ADMIN and DEVELOPER may change and
delete **any** experiment (including schedule and delete) and **any** flag; ANALYST and VIEWER
change none, not even one they created. Superuser and `ADMIN` are different bits — Cognito
admin groups and the bootstrap's first admin set both; a local `ADMIN` user may not be a
superuser, and the admin area (`/admin/*`) checks only the superuser bit.

**Deleting an experiment answers 400**
It must be in `DRAFT` (a non-draft answers **400**; 403 now means only that your role cannot
delete), and `DELETE /experiments/{id}?experiment_key=<key-or-id>` requires the matching
key/id query parameter as a second factor. ADMIN and DEVELOPER may delete any draft.

**Experiment `PUT` answers 400 or 422**
400: the experiment's state refuses the change (for example, targeting can be changed only
while it is draft or paused). 422: the body sent `status` (status changes only through start,
pause, complete and archive) or `schedule` (use `PUT /experiments/{id}/schedule`).

**WebSocket closes immediately with code 4401**
The results stream authenticates with the subprotocol header
`Sec-WebSocket-Protocol: experimently.bearer, <access token>` (what a browser can send) or an
`Authorization: Bearer` header; a missing/invalid token closes with 4401 after the handshake.

---

## 5. The tracking API & SDKs

**`POST /tracking/assign` → 404 `Active experiment with key '...' not found`**
The experiment is `draft`/`paused`/`completed`, or has no `key`, or the key differs (keys are
generated from the name with a random suffix unless you set one). SDK convenience methods
(`get_variant`) swallow this and return `"control"`.

**Every user gets `assigned: false`**
Read `reason`: `holdout` (a global holdout is active), `mutual_exclusion` (the experiment's
group selected another experiment for the user), `targeting` (the context doesn't match the
rules). Ineligible users get HTTP 200 with the control variant and no row — by design.

**The same user gets different variants**
Check the `user_id` the SDK sends (a new anonymous id per page load will do this) and the
experiment **key** (assignment hashes `"{user_id}:{experiment_key}"`, falling back to the
experiment id only when the key is empty). The first result is stored — it cannot change for a
stable id.

**Changing traffic allocation didn't move anyone**
By design: sticky rows win. Existing users keep their variant; only new users see the new
split.

**Events arrive but results show 0 conversions**
The metric matches on `event_name`. SDK events store the event's own name in both
`event_type` and `event_name` (`"purchase"`, `"purchase"`); if your metric's `event_name` is
`"purchase_completed"` nothing matches. Also: the user must have an assignment _before_ the
event (the event's `variant_id` is resolved at write time), and the experiment must be
`active`. Never write `Event.event_type == "conversion"` in analysis code — use
`services/event_matching.py`.

**Key-less events don't count for my experiment**
SDKs fan out key-less `track` calls only to experiments/flags they have _cached_ for that
user in this process. Assign first (`useExperiment`/`get_variant`), then track — or pass
`experiment_key` explicitly.

**429 `Too Many Requests` from an SDK**
SDK paths share `SDK_RATE_LIMIT_PER_MINUTE` (6,000/min per IP); everything else is 300/min,
and all `/export/*` routes together 10/min per client. Honour `Retry-After` (the reference
clients retry once), batch events (100 per `/batch`), or raise the setting for a NAT that
legitimately fans out that much.

**`/tracking/batch` returns 200 with `failure_count > 0`**
Per-event validation; read `errors[]`. Common: an event with neither `experiment_key` nor
`feature_flag_key`; a `user_id` over 255 characters; malformed `metadata`.

**Two SDKs disagree on `consistent_hash`**
The function is MD5 of `"{user_id}:{key}"`, first four bytes little-endian, divided by
2³² (not 2³² − 1). Run `make test-sdk` (the cross-SDK golden vectors in
`tests/sdk-contract/golden-vectors.json`, then `hash_contract.py` over each SDK) and the SDK's
own golden-vector test. Remember the server assigns; an SDK's hash is a utility.

**An SDK's live contract smoke fails but its unit tests pass**
That's what the live harness is for: `python tests/sdk-contract/live/run_live_contract.py
--sdk <name> --strict` against a running API with `seed_sdk_contract.py` applied. Read the
JSON it prints; the SDK is calling an endpoint or shape the backend doesn't serve.

---

## 6. Feature flags & targeting

**Evaluate returns `enabled: false, reason: "inactive"`**
The flag exists but isn't `ACTIVE` (kill switch / archived / rolled back to 0). Not an error —
the client should treat it as off. Activate with `POST /feature-flags/{id}/activate`; an
archived flag must be unarchived first.

**Evaluate returns 404**
Unknown key. Only an unknown key is a 404.

**The flag is at 100% but a user gets `false` with `reason: "targeting_rule"`**
The user matched the rules and the rules object's `rollout_percentage` is below 100 (it
_replaces_ the global one for users who match). Inspect `targeting_rules.rollout_percentage`.

**My rule never matches**
Send the attributes as `context` (URL-encoded JSON on GET, or the POST body). Check the
attribute name against the aliasing: a top-level `country` answers `user.country`; nested
`{"app": {"version": "3.2.1"}}` answers `app.version`; explicit keys win. Check the operator:
dashboard names (`equals`, `semver_gte`, `is_null`) are mapped in
`core/targeting_adapter.py::_SIMPLE_OPERATOR_MAP`, and an unknown operator is refused when
saved. Only rules stored before that check can still hold one; those fall back to the global
rollout (logged once). `python -m backend.scripts.check_targeting_rules` lists them.

**`422` when saving targeting rules**
Rules are validated on create and update, and the message names the place
(`groups[0].conditions[0].operator: unknown operator`), never the value. Causes: an unknown
key at any level (top, group, condition); `groups` together with `rules`; nesting deeper than
10 levels or more than 1,000 nodes or list values; an unknown operator; a value the operator
cannot use (`"abc"` for `greater_than`); a `regex` RE2 refuses; a `rollout_percentage` that is
not an integer 0–100; and the legacy list shape.

**Flag create or update answers 422 `extra_forbidden` / `read_only`, or 409**
`extra_forbidden`: a field the flag does not have (the rules field is `targeting_rules`, not
`rules`). `read_only`: a `status` that disagrees with the flag's (turn it on or off with
`is_active`). 409: the key already exists. A key with capitals or spaces, and
`default_value: true`, answer 422.

**A rule with no conditions**
A group with no conditions is refused when saved. `null`, `{}` and `{"groups": []}` mean no
rules: the global percentage decides for everyone.

**Rules in the legacy list shape**
Refused when a flag is saved. A flag stored with them before still evaluates,
but only `eq`, `ne`, `gt`, `lt`, `contains` and `in` conditions match there; any other
condition matches no user. `check_targeting_rules` lists such flags.

**Flag cohorts reshuffled after a rename**
The rollout hash is on `user_id:flag.key`; renaming the key re-buckets everyone. The
dashboard warns; avoid renaming live flags.

**Flag evaluation is slow / `raw_metrics` is huge**
Every evaluation writes a `RawMetric` row (and latency) by design so safety can compute an
error rate. The metrics scheduler aggregates them every 15 minutes. Don't add work inside
`evaluate_flag_detailed`; for server-side code that evaluates often, the JS and Python SDKs'
local evaluation (beta) answers in-process and reports counts to `POST /tracking/evaluations`.

---

## 7. Results & statistics

**Results are stale after I tracked events**
Cached in Redis: 5 minutes while running, 24 hours once completed/paused. Pass
`use_cache=false` or `POST /results/{id}/invalidate-cache`.

**"Significant" but the recommendation is still "continue testing"**
Minimum sample size: `SHIP_VARIANT` requires every variant to reach the metric's
`minimum_sample_size` (default 100). By design.

**An SRM warning appears**
Observed assignment counts don't match `traffic_allocation` (chi-square p < 0.001). Stop
trusting the result and find the leak: a targeting rule or SDK version that fails to assign on
one platform, a bot hitting one path, a bandit (whose drift is expected — SRM warnings on
bandits are normal). The test can't tell you why.

**p-value on the dashboard differs from my `scipy` calculation**
Count the same thing first: every results endpoint counts **converting users** (a user with
two purchases counts once) over distinct assigned users, not events. `/results` then analyses
every metric as a proportion with Fisher's exact test on those counts; Welch's t-test for mean
metrics runs only in the warehouse module. Reproduce with `scipy.stats.fisher_exact` on the
2×2 table of converting and non-converting users, and use the same `confidence_level`.

**Bayesian / bandit numbers change between two calls the same day**
They shouldn't: Monte Carlo is seeded from `blake2b(experiment_id|YYYY-MM-DD|n_samples)`
(`core/stats_engine.py`). If they do, the inputs (counts) changed, or a caller passed an
explicit different `seed`/`n_samples`.

**The bandit never shifts weights**
Check the scheduler ran (`GET /scheduler/health`, name `bandit`), that
`optimization_type != "fixed"`, and that there are conversions matched through the primary
metric's `event_name` (the Postgres source; DynamoDB counters come only with the counters
module). Weights live in `BanditState.variant_weights`; `/tracking/assign` routes new users by
them, sticky rows still win.

**Sequential testing says `can_stop` but the primary test isn't significant**
Different questions: mSPRT's Λ crossed 1/α (an always-valid decision); the fixed-horizon
p-value hasn't crossed 0.05 yet. Both are shown so the trade is visible; act on the one your
experiment plan committed to.

**CUPED shows no variance reduction**
Expected today: CUPED is beta, and its covariate is not yet a pre-experiment metric, so it
reduces almost no variance. A better covariate choice does not change that until the
covariate comes from the pre-experiment period.

**`POST /results/{id}/post-stratification` answers 501**
By design: post-stratification is not available yet.

---

## 8. Schedulers, safety & rollouts

**A rollout schedule never advances**
In order: (1) the stage's `trigger_type` — `metric_based` is declared but not implemented,
`manual` never auto-advances; (2) the stage's `start_date` (UTC) hasn't passed; (3) the
scheduler ticks every `ROLLOUT_CHECK_INTERVAL_MINUTES` (15; demos use 1); (4)
`GET /scheduler/health` shows `rollout` runs as `failed` — read the error; (5) the schedule is
**paused** — a safety rollback pauses every active schedule of the flag, and a paused schedule
is never advanced.

**Scheduler runs are all `skipped`**
Another replica holds the advisory lock, or the database was unreachable when the lock was
attempted (`core/scheduler_lock.py` yields `False` and the tick skips). One replica _should_
run each tick; if all skip, check the database connection from the API's log.

**Two replicas both advanced a stage / rolled back twice**
Shouldn't happen (`pg_try_advisory_lock(hashtext("experimently.scheduler.<name>"))`). If it
did, one replica is on a build predating `run_locked_tick`, or the two point at different
databases.

**Safety never rolls back an unhealthy flag**
Check, in order: global `enable_automatic_rollbacks` (`GET /safety/settings`); the flag's
config `enabled` and its `metrics` thresholds; the flag is `ACTIVE` with
`rollout_percentage > 0`; the flag isn't archived, already off, or already at/below
`rollback_percentage` (all three are skipped); the safety scheduler is ticking
(`SAFETY_CHECK_INTERVAL_MINUTES`).

**Safety rolled back and now the flag is off for everyone, rule-matched users included**
`rollback_percentage` was 0: a rollback to 0 turns the flag **off** (`reason: inactive` for
everyone). A target of 1–100 lowers only the global percentage, and users who match the rules
keep the rules' percentage. Values outside 0–100 are refused (422).

**`error_rate` is 0 even though errors are logged**
The rate divides by evaluations in the same 15-minute window (`raw_metrics` of type
`flag_evaluation`). No evaluations → rate 0 by definition. Also confirm the error reports
carry the flag's key (`POST /tracking/errors` needs `feature_flag_key` or `experiment_key`).

**A metric in the safety check says `measured: false`**
The platform can't compute that metric (only `error_rate` and latency are measurable from
the platform's own tables). It's reported, never treated as a breach.

**The experiment scheduler activated an experiment I didn't intend to start**
A **draft** whose `start_date` passed is activated on the date. A **paused** experiment is
never resumed by `start_date`: it stays paused until it is started or a resume is scheduled
(`resume_at`). Clear `start_date` or use the schedule endpoint deliberately
(`PUT /experiments/{id}/schedule`, `DRAFT`/`PAUSED` only). The scheduler runs every
`EXPERIMENT_SCHEDULER_INTERVAL_MINUTES` (15).

**Slack/email notifications don't arrive**
`SLACK_ENABLED`/`EMAIL_ENABLED` plus a bot token / SendGrid key or SMTP settings; per-user
preferences under `/notifications`; the delivery log there shows attempts and failures.
Notification failures never fail the action that triggered them.

---

## 9. The dashboard

**`next dev` runs but every request fails**
No `NEXT_PUBLIC_API_URL` in development falls back to `http://localhost:8000` — is the API
up? In the container image the base is empty (same origin) and nginx proxies `/api/`.

**Login page loops / immediately redirects back**
`AuthContext` re-derives the user from `GET /auth/me` on mount; a 401 clears the token.
Check the API's `SECRET_KEY` didn't change (tokens are signed with it) and that the browser's
clock isn't wildly off (JWT `exp`). Under `AUTH_PROVIDER=cognito` the dashboard does not sign
in at all (section 4).

**`403` page for an admin user**
The admin area needs the **superuser** bit, not the `ADMIN` role. `RequireAuth` and the nav
check read `UserMe.role`/`is_superuser` from `/auth/me`; check both (a local `ADMIN` who isn't a
superuser cannot open `/admin/*`).

**Validation message from the server appears in the form**
By design: `validateForm` mirrors the server rules for instant feedback, and when they
disagree the server's Pydantic message (`variants: Traffic allocations must sum to 100% ...`)
is shown verbatim via `ApiError`.

**A 500 shows a fixed message and a request ID**
By design: unexpected errors never carry exception text. Grep the API log for the request ID.

**The new-experiment form doesn't offer split-URL or bandit, or sequential/Bayesian/CUPED**
By design: the guided setup and the `?advanced` form create A/B and multivariate experiments
only, and have no controls for the advanced analyses. Use the API.

**The new-experiment form lost my answers**
Answers survive a sign-in or key error, but not a page reload.

**Charts don't render in Jest**
Recharts is mocked (`src/__mocks__/recharts.tsx` via `jest.config.js`) so charts render as
inspectable stubs. Assert on the stub's props, not on SVG.

**`npm ci` fails: lock file out of sync**
`package-lock.json` must be regenerated whenever `package.json` changes (`npm install`, commit
the lock). This once turned every frontend CI job red.

**The `/docs` page links somewhere unexpected**
By design: `/docs` is a hub of links built by `docsUrl()` in `frontend/src/services/docs.ts`.
By default they go to the Markdown in the GitHub repository; with `NEXT_PUBLIC_DOCS_URL` set at
build time they go to a built MkDocs site instead (the marketing build points it at the published
one, [getexperimently.github.io/experimently](https://getexperimently.github.io/experimently/)).
The dashboard serves no markdown pages of its own.

**Playwright E2E fails locally**
It needs a seeded, running backend and dashboard (`./demo/setup-local.sh`), Node 22, and
`npx playwright install`. **Browser E2E** is a required check on every pull request, so fix it
before merging.

**Module pages (workspaces, warehouse, SSO) are missing, or module routes answer 404/501**
You are on the **core** profile. `GET /api/v1/modules` reports the profile and modules a
deployment serves; run the full profile to get them.

---

## 10. Containers & compose

**`docker compose up --wait` never becomes healthy**
`docker compose logs api --tail 100`. The entrypoint prints each step (wait → bootstrap →
seed → uvicorn) and stops on failure (`set -euo pipefail`). Usual causes: a `SECRET_KEY`
rejected by the validators, an unknown name in `SEED`, Postgres not ready within
`DB_WAIT_TIMEOUT` (120 s). The service healthcheck is `/health/ready` with a 300 s
`start_period` to cover first-run seeding.

**Seeds didn't run on restart**
By design: completed seeds are recorded in `seed_markers`. `SEED_FORCE=true` re-runs them;
`docker compose down -v` drops the volume and starts clean.

**Seeds don't run in staging or production**
By design: the demo seeds run only in development and test.

**The API container dies with exit code 132 on an Apple-silicon Mac**
`cryptography`'s OpenSSL picks CPU features the VM can't execute (SIGILL). The entrypoint
probes and sets `OPENSSL_armcap=0` automatically; if you bypassed the entrypoint, set it
yourself.

**The dashboard container serves the app but API calls 502**
nginx proxies `/api/`, `/ws/` and `/health*` to `API_UPSTREAM` (default `http://api:8000`).
Check the API service is healthy and on the same compose network.

**Demo simulators exit with `no /data/demo/shoplab/.api_key`**
The seed for that app didn't run: start the API with `SEED=demo,shoplab[,streampulse]`. The
seeds write the keys to the shared `demo_keys` volume the simulators read.

**I want the full profile (the optional modules)**
`EXPERIMENTLY_PROFILE=full docker compose up -d --build`. The Dockerfile targets are `core`
and `full`; the full image adds `modules/`. The full profile signs the audit log and needs
`AUDIT_HMAC_KEY`.

---

## 11. CI

**`DCO` is red**
A commit is not signed off. Sign off the branch with `git rebase --signoff main` and force-push
your branch (only your own branch). Future commits: `git commit -s`. The sign-off must match
the commit's author or committer.

**`conventional-title` is red**
The PR title is not `type(scope): description` with a known type. Edit the title; the check
re-runs. It is not a required check, but a title it rejects leaves your change out of the
changelog.

**`regression-guard` is red**
The PR is labelled `bug` and changes no test file. Add a test that fails on the old code, mark
it `@pytest.mark.regression`, and name it in the PR body.

**`lint` is red on `lint-imports`, or `core-build` is red**
Something under `backend/` imports `modules` (or a module import leaked into core). Move the
code behind the module registration hooks; `make core-build` reproduces the core leg locally.

**A test fails on the OpenAPI snapshot**
You changed the API; run `make openapi` and commit `docs/api/openapi-v1.stable.json`,
`docs/api/openapi-v1.full.json` and the frontend fixture.

**The requirements-lock check fails**
A pin in a requirements file is not in its lock at that version. Run `make lock` and commit
both locks.

**Doc Examples fails**
A shell block in the app's `docs/` no longer runs or no longer prints what it says. Reproduce
with `python scripts/doc_examples.py --check`.

**Unit Tests job dies at conftest import**
A dependency imported by the app but missing from `backend/requirements.txt` (this was
`semver` once). `pip install --dry-run -r backend/requirements.txt` locally; add the pin.

**Frontend job fails on `npm ci`**
Lock file out of sync — regenerate and commit `frontend/package-lock.json`.

**SDK Contract Tests: `unrecognized arguments`**
The job installs only `pytest`; it passes `-o addopts=""` for that reason. If you add a
plugin flag to `pyproject.toml`'s `addopts`, this job breaks.

**`Leak Guard` or a gitleaks step fails**
`Leak Guard` (`scripts/leak_guard.py`) is a required check: a push to this public repository
is publication, so it refuses secrets and false claims in tracked files. Remove the finding
rather than suppressing it. The workflows run the gitleaks **binary**, not `gitleaks-action`
(which needs a licence for an organisation and a pull-requests scope); copy that pattern into
any new workflow.

**Semgrep blocks on `sqlalchemy.text(...)`**
Use a bound parameter. When the identifier truly can't be one (a schema name in DDL), add
`# nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text`
with a justification, and prefer SQLAlchemy constructs (`CreateSchema`) where they exist.

**`curl … | grep -q` step fails intermittently**
`set -o pipefail` + curl exiting 23 on SIGPIPE when the page exceeds the pipe buffer. Fetch to
a file, then grep — the docker-smoke job does.

**Dependabot PRs pile up, or a grouped one is unresolvable**
Patch and minor updates (and GitHub Actions majors) merge themselves once every required check
is green; other majors are ignored. A bump that stays open is red for a reason: read its
failing check. Grouped patch-and-minor PRs can pin incompatible versions; fix pins on the
Dependabot branch, verify with `pip install --dry-run`, and `@dependabot rebase` a superseded
grouped PR to make it close and reopen fresh. If the queue keeps growing, ask what is supposed
to drain it.

**Docker Smoke: `expected 401 without credentials, got 200`**
The image was built with a bypass default or the compose env set `DEV_AUTH_BYPASS=true`. The
job asserts the auth gate on purpose; every image must be fail-closed.

---

## 12. AWS

How to deploy and roll back is in the deploying guide (`guides/deploying.md`) and the
[Operations book](../platform-operations/README.md). These are the symptoms.

**`cdk synth` exits: `AWS account id not set`**
Export `AWS_ACCOUNT_ID` (or let the CLI set `CDK_DEFAULT_ACCOUNT` from credentials). No
account id is hard-coded, by design.

**The Fargate stack fails: certificate / HTTPS listener**
`CERTIFICATE_ARN` must point at an ACM certificate for the HTTPS listener. `cdk synth` refuses
without it (a `ValueError` from `fargate_service_stack.py`), in every environment; there is no
HTTP-only fallback.

**A deployed Lambda returns a placeholder message**
By design. No stack deploys a handler from `backend/lambda/`; the assignment Lambda was
removed (#606). The stacks do deploy placeholder or inline functions: `DatabaseAccessLambda` in
the compute stack in every environment, and in a full checkout `AnalyticsLambda` (placeholder)
and `ETLTriggerLambda` (a real inline function). Point SDKs at the API, not at a Lambda.

**`synth` refuses a secret ARN, or tasks stop at start with a secrets error**
The task definitions read three Secrets Manager secrets an operator creates by hand under
`/<env>/experimentation/`: `jwt-secret`, `first-superuser-password` and, on the full profile,
`audit-hmac-key`. The CDK takes each by its **complete** ARN (`JWT_SECRET_ARN`,
`FIRST_SUPERUSER_PASSWORD_SECRET_ARN`, `AUDIT_HMAC_KEY_SECRET_ARN`), including the
six-character suffix Secrets Manager adds; a partial ARN is refused at synth, and the refusal
names the read-only command that prints the right value.

**`/health` is 200 but every real request answers 500 after `cdk deploy`**
The schema does not exist yet. On AWS only the deploy's migration task writes the schema; API
tasks never run migrations. `cdk deploy` alone creates no schema: run the **Deploy** workflow.

**The deploy stopped at the migration step**
By design: the migration task gates the deployment. Fix the migration and run **Deploy**
(`deploy.yml`, one workflow for staging and production, approval-gated) again; **Database
Migration** (`db-migrate.yml`) runs a migration on its own. It takes `heads` or a revision id,
and refuses relative steps (`-1`, `modules@-1`), `head` and `base`, because a full install has
two heads. Migrations are expand-only, so the previous image keeps working meanwhile. To undo a
migration, do it **before** rolling the API back.

**Rollback**
`rollback.yml` (**Rollback**) puts a previous, released revision of the API back into service
through CodeDeploy, and the dashboard's through a rolling deployment. It refuses when there is
nothing to roll back. It has not yet completed on real AWS, so treat the first rollback in an
environment with care and follow the app's `docs/deployment/rollback-runbook.md`. The schema is
not rolled back; for a bad migration, write a forward fix.

**`/metrics` returns 403 in staging**
Set `METRICS_TOKEN` and have the scraper send it (`Authorization: Bearer <token>`); without a
token the endpoint is open only in development/test.

**`/health/ready` is 503 but the process is fine**
PostgreSQL isn't answering (or Redis, if `REDIS_REQUIRED`). Liveness stays 200 on purpose so
the orchestrator takes the task out of the load balancer without restarting it. The error
text is scrubbed of anything containing "password".

---

### When none of this matches

1. Get the request id: every response carries `X-Request-ID` (and a 500 names it in its body);
   grep the API log for it (`LOG_FORMAT=json` outside development).
2. Check `/health/ready`, `/metrics` (`scheduler_last_success_timestamp`, `rate_limit_rejections_total`)
   and `GET /scheduler/health`.
3. Reproduce through the public API with `curl` before reading code — most "bugs" in this
   platform are a shape or a key mismatch at the boundary.
4. `git grep` the error string; the codebase's error messages are unique and searchable.
5. Read the PR description of the last change to that area — they're written as design notes.

## Related engineering field guides

These task-based guides use a newer, explicitly pinned app snapshot; this book or guide retains
its original reference commit. See the collection index for scope and verification details.

- [Distributed correctness](../engineering-guides/distributed-correctness.md)
- [Database evolution and performance](../engineering-guides/database-evolution.md)

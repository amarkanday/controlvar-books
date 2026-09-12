# Troubleshooting Guide

### _Symptom → cause → fix, for Experimently_

[← experimently-docs](../README.md) · [Onboarding](./onboarding.md) · [Usage guide](./usage.md)

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
`backend.app.*` and runs from the root: `uvicorn backend.app.main:app`. If you see
`from app.models...` in code, that _is_ the bug — fix the import (see the metrics-import rule
below).

**`ImportError` / version errors on startup (`fastapi`, `pydantic`, `sqlalchemy`)**
The venv isn't active or is stale. `source venv/bin/activate` (always), then
`pip install -r backend/requirements.txt`. The pins are exact for a reason — FastAPI 0.141,
Pydantic 2.13, SQLAlchemy 2.0.52.

**`sqlalchemy.exc.InvalidRequestError: Class ... is not mapped` or two classes with the same
name**
A model was imported by two different paths (`backend.app.models.metrics.metric` and
`app.models.metrics.metric`), so SQLAlchemy registered it twice. Fix the import to the fully
qualified form and run `python standardize_metrics_imports.py --check`. Also make sure any
new model is imported in `backend/app/models/__init__.py` (mappers resolve relationship
strings by name, and a never-imported class can't be found).

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
(`backend/app/core/config.py`). Generate a real key
(`python -c "import secrets; print(secrets.token_hex(32))"`), and never set the bypass outside
`development`/`test`. The same guards cover `FIRST_SUPERUSER_PASSWORD`, `AUDIT_HMAC_KEY` and
`SSO_STATE_SECRET`.

**A setting I exported doesn't take effect**
`Settings` uses `extra="ignore"`, so a misspelled variable is silently dropped. Check the
exact name in `core/config.py` (case-sensitive), then verify in a shell:
`python -c "from backend.app.core.config import settings; print(settings.SAFETY_CHECK_INTERVAL_MINUTES)"`.

**`CORS_ORIGINS` isn't applied / the browser reports a CORS error**
`CORS_ORIGINS` is a comma-separated string (annotated `NoDecode`); `BACKEND_CORS_ORIGINS`
must be a JSON array. Dev defaults include 3000, 3001, 3100, 3200 and 3300. If the failing
request is from a browser SDK, check the preflight: `allow_headers` must include `X-API-Key`
(it does in `main.py`; a custom deployment behind a proxy may strip it).

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
`pyproject.toml`, `testpaths = ["backend/tests"]`). Activate the venv. Export
`APP_ENV=test TESTING=true` (the conftest sets them too, but your shell may have exported
`ENVIRONMENT=development` — the conftest pops it).

**`could not connect to server: localhost:5432`**
DB-backed tests need PostgreSQL on `localhost:5432` (or set `POSTGRES_PORT`/`POSTGRES_SERVER`;
the conftest honours them). `make db`. On macOS the host must be `localhost`, not a container
name.

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

**`smoke/test_production.py` fails with connection errors**
Expected offline — it targets a live server via `SMOKE_TEST_API_URL`.

**A scheduler ran in the middle of my test / `test_trigger_aggregation` flakes in CI**
Fixed: the lifespan does not start schedulers when `settings.is_test`. If you construct the
app with a non-test settings object in a test, you'll re-enable them.

**`moto` raises `AttributeError: module has no attribute 'mock_aws'`**
moto is pinned at 4.2.2: use `mock_dynamodb`, `mock_cognitoidp`, etc. (`mock_aws` is 4.3+).

**Cognito auth tests error at setup with rate-limiter patches**
`backend/tests/integration/auth/` runs under `AUTH_PROVIDER=cognito` with moto and — in CI —
**no Postgres**. Put any DB-backed auth test in `backend/tests/integration/api/` instead.

**Lambda tests: `ImportError` or `tests.conftest` conflicts**
Each function's `tests/` defines its own `tests.conftest`; run them one directory at a time
(`for d in backend/lambda/{assignment,event_processor,feature_flag_evaluation,shared}; do
python -m pytest "$d" -p no:cov -q; done`), exactly as the PR gate does.

**A feature-flag permission test fails with "coroutine was never awaited"**
Feature-flag and report dependencies are `async`: `@pytest.mark.asyncio`, `async def`,
`await deps.can_update_feature_flag(...)`. Experiment dependencies are sync — no `await`.

**Route enumeration test breaks after a FastAPI upgrade**
FastAPI ≥ 0.141 puts lazy `_IncludedRouter` entries (no `.path`) in `app.routes`. Enumerate
via `effective_route_contexts` — see `backend/tests/smoke/test_wiring.py::_iter_http_routes`.

---

## 3. Database & migrations

**`alembic upgrade head` fails on an empty database (`relation already exists` /
`does not exist`)**
The chain isn't replayable from zero (older revisions re-create tables). Use
`python -m backend.app.db.bootstrap`, which does `create_all` from the models and
`alembic stamp head` on a fresh database, and `upgrade head` on an existing one.

**`Multiple head revisions are present`**
Two branches each added a migration. `python -m alembic -c backend/app/db/alembic.ini merge
-m "merge heads" <rev1> <rev2>`, then `upgrade head`.

**`Can't locate revision identified by '...'`**
A migration's `down_revision` names a revision that doesn't exist — often a migration _name_
instead of its _id_. Open the file and set `down_revision` to the current head's id
(`alembic history | head`).

**Autogenerate proposes dropping indexes / altering enums I didn't touch**
Drift between the model and a database built by an older path. Keep only the change you
intended in the migration; regenerate against a database that was `upgrade`d rather than
bootstrapped if you need a clean diff.

**`ALTER TYPE ... ADD VALUE cannot run inside a transaction block`** / enum casing mismatch
PostgreSQL enums are the sharp edge. `ExperimentStatus` values are lower-case,
`FeatureFlagStatus` upper-case; a past migration added lower-case values to fix a mismatch.
For a new enum, match the Python enum's `.value` exactly, or prefer a `String` column with a
`CheckConstraint` if the set will change.

**`TypeError: can't compare offset-naive and offset-aware datetimes`**
The columns are `DateTime` without timezone, so SQLAlchemy returns naive values; new code
uses `datetime.now(timezone.utc)`. Normalise at the comparison — `rollout_scheduler._as_utc`
is the pattern — or use naive UTC (`datetime.utcnow()`) when writing to those columns as the
safety service does.

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
have none and can't use the local provider).

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
depend on `get_api_key`. The `scopes` column exists but isn't enforced yet.

**403 `You don't have permission to ...`**
Role × resource × action in `core/permissions.py`: `VIEWER`/`ANALYST` can't create or modify
experiments and flags; `DEVELOPER` can; ownership lets a non-admin modify their own. Superuser
and `ADMIN` are different bits — Cognito admin groups and the bootstrap's first admin set both;
a local `ADMIN` user may not be a superuser, and `/admin/*` checks the superuser bit.

**A `DEVELOPER` can't delete an experiment**
It must be in `DRAFT`, and `DELETE /experiments/{id}?experiment_key=<key-or-id>` requires the
matching key/id query parameter as a second factor. Both are deliberate.

**WebSocket closes immediately with code 4401**
The results stream authenticates with `?token=<access token>` (browsers can't set headers on
the handshake) or an `Authorization: Bearer` header; a missing/invalid token closes with 4401
after the handshake.

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
experiment id (two experiments with similar names hash differently). Assignment is
`md5(user_id:experiment_id) % 100` and the first result is stored — it cannot change for a
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
SDK paths share `SDK_RATE_LIMIT_PER_MINUTE` (6,000/min per IP); everything else is 300/min.
Honour `Retry-After` (the reference clients retry once), batch events (100 per `/batch`), or
raise the setting for a NAT that legitimately fans out that much.

**`/tracking/batch` returns 200 with `failure_count > 0`**
Per-event validation; read `errors[]`. Common: an event with neither `experiment_key` nor
`feature_flag_key`; a `user_id` over 255 characters; malformed `metadata`.

**Two SDKs disagree on `consistent_hash`**
The function is MD5 of `"{user_id}:{key}"`, first four bytes little-endian, divided by
2³² (not 2³² − 1). Run `python -m pytest tests/sdk-contract/test_python_sdk.py -o addopts=""`
and the SDK's own golden-vector test. Remember the hash is a utility — no SDK buckets locally.

**An SDK's live contract smoke fails but its unit tests pass**
That's what the live harness is for: `python tests/sdk-contract/live/run_live_contract.py
--sdk <name> --strict` against a running API with `seed_sdk_contract.py` applied. Read the
JSON it prints; the SDK is calling an endpoint or shape the backend doesn't serve.

---

## 6. Feature flags & targeting

**Evaluate returns `enabled: false, reason: "inactive"`**
The flag exists but isn't `ACTIVE` (kill switch / archived). Not an error — the client should
treat it as off. Activate with `POST /feature-flags/{id}/activate`.

**Evaluate returns 404**
Unknown key. Only an unknown key is a 404.

**The flag is at 100% but a user gets `false` with `reason: "targeting_rule"`**
A rule matched with a percentage below 100 (or 0); the rule's percentage _replaces_ the
global one. Inspect `targeting_rules` and the rule's `rollout_percentage`.

**My rule never matches**
Send the attributes as `context` (URL-encoded JSON on GET, or the POST body). Check the
attribute name against the aliasing: a top-level `country` answers `user.country`; nested
`{"app": {"version": "3.2.1"}}` answers `app.version`; explicit keys win. Check the operator:
dashboard names (`equals`, `semver_gte`, `is_null`) are mapped in
`core/targeting_adapter.py::_SIMPLE_OPERATOR_MAP`; an unmapped operator makes the whole
rule set uninterpretable and the flag falls back to the global rollout.

**`422` when saving targeting rules**
`Condition`'s validator: `in`/`not_in`/`contains_all`/`contains_any` need a list value;
`between`/`geo_distance` need `additional_value`; `geo_distance` value must be
`[lat, lon]`; attribute names are alphanumeric with `_` and `.` only.

**A rule with no conditions matches everyone**
By design: an empty group is `True` ("just a percentage"). Add a condition or set the rule's
percentage to 0.

**Experiment targeting rules in the legacy list shape are ignored**
By design: list-shaped rules are supported for flags only (evaluated by the old in-service
path); experiments accept native or dashboard shapes.

**Flag cohorts reshuffled after a rename**
The rollout hash is on `user_id:flag.key`; renaming the key re-buckets everyone. The
dashboard warns; avoid renaming live flags.

**Flag evaluation is slow / `raw_metrics` is huge**
Every evaluation writes a `RawMetric` row (and latency) by design so safety can compute an
error rate. The metrics scheduler aggregates them every 15 minutes; the AWS edition moves the
hot path to Lambda. Don't add work inside `evaluate_flag_detailed`.

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
The primary path uses Fisher's exact test; the enhanced path uses a z-test at n ≥ 30 and
Welch's t-test for continuous metrics. Both are legitimate; they converge at scale. Also
check you're counting the same thing — `calculate_metric_results` counts _events_, the
streaming service counts _distinct users_.

**Conversion rate above 100%**
A repeatable event (a user purchasing twice) counted as events over assigned users. Use a
count-type metric for "events per user", or dedupe per user for a conversion metric.

**Bayesian / bandit numbers change between two calls the same day**
They shouldn't: Monte Carlo is seeded from `blake2b(experiment_id|YYYY-MM-DD|n_samples)`
(`core/stats_engine.py`). If they do, the inputs (counts) changed, or a caller passed an
explicit different `seed`/`n_samples`.

**The bandit never shifts weights**
Check the scheduler ran (`GET /scheduler/health`, name `bandit`), that
`optimization_type != "fixed"`, and that there are conversions matched through the primary
metric's `event_name` (the Postgres source; DynamoDB counters are only in the AWS edition).
Weights live in `BanditState.variant_weights`; `/tracking/assign` routes new users by them,
sticky rows still win.

**Sequential testing says `can_stop` but the primary test isn't significant**
Different questions: mSPRT's Λ crossed 1/α (an always-valid decision); the fixed-horizon
p-value hasn't crossed 0.05 yet. Both are shown so the trade is visible; act on the one your
experiment plan committed to.

**CUPED shows no variance reduction**
θ = Cov(Y, X)/Var(X) is 0 when the covariate has no variance or no correlation with the
outcome. Choose a pre-period covariate that predicts the metric (last-period value of the
same metric is the usual choice).

---

## 8. Schedulers, safety & rollouts

**A rollout schedule never advances**
In order: (1) the stage's `trigger_type` — `metric_based` is declared but not implemented,
`manual` never auto-advances; (2) the stage's `start_date` (UTC) hasn't passed; (3) the
scheduler ticks every `ROLLOUT_CHECK_INTERVAL_MINUTES` (15; demos use 1); (4)
`GET /scheduler/health` shows `rollout` runs as `failed` — read the error; (5) a safety
rollback cancels the schedule.

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
`rollout_percentage > 0`; the flag isn't already at/below `rollback_percentage` (the
scheduler deliberately skips those while the 15-minute window is hot); the safety scheduler
is ticking (`SAFETY_CHECK_INTERVAL_MINUTES`).

**`error_rate` is 0 even though errors are logged**
The rate divides by evaluations in the same 15-minute window (`raw_metrics` of type
`flag_evaluation`). No evaluations → rate 0 by definition. Also confirm the error reports
carry the flag's key (`POST /tracking/errors` needs `feature_flag_key` or `experiment_key`).

**A metric in the safety check says `measured: false`**
The platform can't compute that metric (only `error_rate` and latency are measurable from
the platform's own tables). It's reported, never treated as a breach.

**The experiment scheduler activated an experiment I didn't intend to start**
`start_date` passed and the experiment was `draft`/`paused`; the scheduler activates on the
date. Clear `start_date` or use the schedule endpoint deliberately
(`PUT /experiments/{id}/schedule`, `DRAFT`/`PAUSED` only).

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
clock isn't wildly off (JWT `exp`).

**`403` page for an admin user**
`RequireAuth` with `roles` and the nav check use `UserMe.role`/`is_superuser` from `/auth/me`.
Check both bits (a local `ADMIN` who isn't a superuser).

**Validation message from the server appears in the form**
By design: `validateForm` mirrors the server rules for instant feedback, and when they
disagree the server's Pydantic message (`variants: Traffic allocations must sum to 100% ...`)
is shown verbatim via `ApiError`.

**Charts don't render in Jest**
Recharts is mocked (`src/__mocks__/recharts.tsx` via `jest.config.js`) so charts render as
inspectable stubs. Assert on the stub's props, not on SVG.

**`npm ci` fails: lock file out of sync**
`package-lock.json` must be regenerated whenever `package.json` changes (`npm install`, commit
the lock). This once turned every frontend CI job red.

**The in-app `/docs/*` pages say "Documentation coming soon"**
The dashboard image is built from the repository root and copies `docs/` at build time; a
missing markdown file now fails the build. If you see the placeholder, the image was built
with the wrong context.

**Playwright E2E fails locally**
It needs a seeded, running backend and dashboard (`./demo/setup-local.sh`), Node 22, and
`npx playwright install`. E2E runs nightly, not in the PR gate.

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

**I want the enterprise image**
`EXPERIMENTLY_EDITION=ee docker compose build api` — the `ee` target copies `ee/` if present;
otherwise it's identical to `ce`.

---

## 11. CI

**Unit Tests job dies at conftest import**
A dependency imported by the app but missing from `backend/requirements.txt` (this was
`semver` once). `pip install --dry-run -r backend/requirements.txt` locally; add the pin.

**Frontend job fails on `npm ci`**
Lock file out of sync — regenerate and commit `frontend/package-lock.json`.

**SDK Contract Tests: `unrecognized arguments`**
The job installs only `pytest`; it passes `-o addopts=""` for that reason. If you add a
plugin flag to `pyproject.toml`'s `addopts`, this job breaks.

**gitleaks fails with an opaque API error on a PR**
The job needs `permissions: pull-requests: read` to list the PR's commits; the release gate
grants it — copy that block into any new workflow using gitleaks.

**Semgrep blocks on `sqlalchemy.text(...)`**
Use a bound parameter. When the identifier truly can't be one (a schema name in DDL), add
`# nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text`
with a justification, and prefer SQLAlchemy constructs (`CreateSchema`) where they exist.

**`curl … | grep -q` step fails intermittently**
`set -o pipefail` + curl exiting 23 on SIGPIPE when the page exceeds the pipe buffer. Fetch to
a file, then grep — the docker-smoke job does.

**A grouped Dependabot PR is unresolvable / goes DIRTY**
Grouped patch-and-minor PRs can pin incompatible versions (aws-cdk assets, locust → pytest 8,
scipy/statsmodels → numpy/pandas). Fix pins on the Dependabot branch, verify with
`pip install --dry-run`, and merge the big grouped PR before smaller ones; `@dependabot
rebase` on a superseded grouped PR makes it close and reopen fresh.

**Docker Smoke: `expected 401 without credentials, got 200`**
The image was built with a bypass default or the compose env set `DEV_AUTH_BYPASS=true`. The
job asserts the auth gate on purpose; the Community Edition must be fail-closed.

---

## 12. AWS

**`cdk synth` exits: `AWS account id not set`**
Export `AWS_ACCOUNT_ID` (or let the CLI set `CDK_DEFAULT_ACCOUNT` from credentials). No
account id is hard-coded, by design.

**The Fargate stack fails: certificate / HTTPS listener**
`CERTIFICATE_ARN` must point at an ACM certificate for the HTTPS listener; without it the
stack is only suitable for dev.

**The deployed Lambda returns `Assignment Lambda placeholder`**
The compute stack's Lambdas are inline placeholders; the real handlers in `backend/lambda/`
aren't packaged (`Code.from_asset` is the TODO). Don't point an SDK at API Gateway yet — and
when you do, reconcile the 0–1 vs 0–100 allocation convention first.

**Secrets not found at task start**
Secrets follow `/<env>/experimentation/<db-password|jwt-secret|redis-url>` in Secrets
Manager and must exist before the Fargate stack deploys; the task role has least-privilege
read on exactly those.

**Production migrations failed and the deploy stopped**
By design: `run-migrations` (a one-shot Fargate task running the bootstrap) gates the
blue/green deploy. Fix the migration, re-run `db-migrate.yml`, then re-dispatch
`deploy-prod.yml`. Because migrations are additive, the previous image keeps working meanwhile.

**Rollback**
`rollback.yml` re-points the ECS service at a previous task definition; the schema isn't
rolled back (additive migrations make that safe). For a bad migration, write a forward fix.

**`/metrics` returns 403 in staging**
Set `METRICS_TOKEN` and have the scraper send it (`Authorization: Bearer <token>` or
`?token=`); without a token the endpoint is open only in development/test.

**`/health/ready` is 503 but the process is fine**
PostgreSQL isn't answering (or Redis, if `REDIS_REQUIRED`). Liveness stays 200 on purpose so
the orchestrator takes the task out of the load balancer without restarting it. The error
text is scrubbed of anything containing "password".

---

### When none of this matches

1. Get the request id: every response carries `X-Request-ID`; grep the API log for it
   (`LOG_FORMAT=json` outside development).
2. Check `/health/ready`, `/metrics` (`scheduler_last_success_timestamp`, `rate_limit_rejections_total`)
   and `GET /scheduler/health`.
3. Reproduce through the public API with `curl` before reading code — most "bugs" in this
   platform are a shape or a key mismatch at the boundary.
4. `git grep` the error string; the codebase's error messages are unique and searchable.
5. Read the PR description of the last change to that area — they're written as design notes.

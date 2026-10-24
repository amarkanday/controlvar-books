# Part VI — Testing & Operating

### Hours 22–24

[← Part V](./part-5-beyond-requests.md) · [Contents](./README.md)

Hours 22 and 23 cover endpoint testing and database-backed integration tests. They explain the
difference between testing a response in isolation and exercising the collaborators that produce
it, including the repository's approach to per-process test databases. Hour 24 covers health checks
and summarizes the backend conventions used throughout the book.

---

## Hour 22 — `TestClient`, `dependency_overrides`, and the mocked-seam lesson

### What & why

FastAPI ships a **`TestClient`** (built on `httpx`) that calls your app in-process — no
server, no network — with the same `.get`/`.post` API you'd use against a URL. Combined
with **`app.dependency_overrides`**, it lets a test replace exactly the leaves it needs
(the session, the current user) while running everything else for real: routing,
middleware, validation, serialisation, the endpoint body, the service.

That "everything else for real" is the point, and it's where a test suite can quietly go
wrong. Consider the alternative — a test of `POST /tracking/track` that patches
`EventService.track_event` with a `MagicMock` and asserts it was called. Fast, isolated,
green. And it never notices that the endpoint builds an `EventCreate` with field names the
real service rejects.

#### The mocked-seam lesson

That's not hypothetical. In September 2026 the platform had 5,000+ green tests and a broken
public tracking API: the endpoint's `EventCreate` didn't match the service, the experiments
endpoints returned shapes their response models refused, the safety service read a table
nobody wrote to, and the bandit scheduler called a method that didn't exist on the DynamoDB
client. Each path had tests that **mocked the collaborator** and asserted on the mock.

The policy the team adopted after that audit, in one line: **mock what you don't own; drive
what you do.** External services (AWS via `moto`, Slack, SendGrid,
the LLM APIs) are mocked. Anything in `backend/app` is exercised for real — through
`TestClient`, against a real database — at least once per hot path. Unit tests with mocks are
still welcome for branch coverage, and they _name their integration twin_ in the docstring.

### In the wild

The `client` fixture is the harness's centrepiece. Trimmed to what matters:

```python
# 📁 backend/tests/conftest.py
@pytest.fixture
def client(db_session, monkeypatch):
    """Create a test client for the FastAPI application."""
    # Override the get_db dependency
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    # Mock Cognito auth service
    def mock_get_user_with_groups(*args, **kwargs):
        return {
            "username": "test_user",
            "attributes": {"sub": "sub-testuser", "email": "test@example.com"},
            "groups": ["admin-group"],
        }

    monkeypatch.setattr(
        "backend.app.services.auth_service.CognitoAuthService.get_user_with_groups",
        mock_get_user_with_groups,
    )

    # Mock security token decoder
    def mock_decode_token(*args, **kwargs):
        ...

    monkeypatch.setattr("backend.app.core.security.decode_token", mock_decode_token)

    # Get or create a superuser for authentication
    user = db_session.query(User).filter(User.email == "test@example.com").first()
    if not user:
        user = User(
            username="test_user",
            email="test@example.com",
            full_name="Test User",
            hashed_password="$2b$12$EixZaYVK1fsbw1ZfbX3OXePaWxn96p36WQoeG6Lruj3vjPGga31lW",
            is_active=True,
            is_superuser=True,
            role=UserRole.ADMIN,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

    # Create both sync and async return functions for user
    async def override_get_current_user():
        return user

    def override_get_current_active_user():
        return user

    def override_get_current_superuser():
        return user

    async def override_get_cache_control():
        return CacheControl(enabled=False, skip=True)

    def override_get_api_key():
        return user

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user
    app.dependency_overrides[deps.get_current_active_user] = (
        override_get_current_active_user
    )
    app.dependency_overrides[deps.get_current_superuser] = (
        override_get_current_superuser
    )
    app.dependency_overrides[deps.get_cache_control] = override_get_cache_control
    app.dependency_overrides[deps.get_api_key] = override_get_api_key

    # Create test client
    test_client = TestClient(app)

    yield test_client

    # Clean up
    app.dependency_overrides.clear()
```

Four things to notice. The overrides are keyed by the **function objects** the endpoints
reference: the fixture overrides `deps.get_db`, so it reaches exactly the routes that declare
`Depends(deps.get_db)`. A route that imported a different function object, even one with the
same name and body, would not be overridden and would open a real session against whatever
database the settings point at; that is one reason the house rule is to always depend on
`deps.get_db`. The client is built **without** `with`, so the app's lifespan never runs in
this fixture (no scheduler, no start-up checks). The API-key dependency is overridden too, so
SDK routes called through this client authenticate as the test user. The auth overrides
return a real `User` _row_ (with a real bcrypt hash, so nothing downstream trips on a
null), so permission checks run for real against a superuser. And `dependency_overrides.clear()`
in teardown — overrides are global on the app, and a leaked one poisons the next test.

A real test through the public API, with the database as the oracle:

```python
# 📁 backend/tests/integration/api/test_tracking_api.py
class TestAssignEligibility:
    """``/tracking/assign`` honours the global holdout, mutual exclusion groups
    and experiment targeting rules for *new* users; sticky users bypass all."""

    def test_holdout_users_get_control_and_are_not_recorded(
        self, admin_client, active_experiment, active_holdout, db_session
    ):
        inside, outside = _users_by_holdout_membership(10, 10)

        for user_id in inside:
            resp = admin_client.post(
                "/api/v1/tracking/assign",
                json={"experiment_key": active_experiment.key, "user_id": user_id},
            )
            data = _assert_unassigned(resp, active_experiment, "holdout")
            assert data["experiment_key"] == active_experiment.key
            assert data["user_id"] == user_id
            assert _assignment_rows(db_session, active_experiment, user_id) == []
            assert _event_rows(db_session, active_experiment, user_id) == []

            # Still no row after a second call: the response is not sticky.
            again = admin_client.post(
                "/api/v1/tracking/assign",
                json={"experiment_key": active_experiment.key, "user_id": user_id},
            )
            assert again.json()["assigned"] is False
            assert _assignment_rows(db_session, active_experiment, user_id) == []

        for user_id in outside:
            resp = admin_client.post(...)
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["assigned"] is True
            assert data["reason"] == "assigned"
            assert len(_assignment_rows(db_session, active_experiment, user_id)) == 1
```

Everything in that test is real except the credential: the request goes through the
middleware, the API-key dependency (overridden to the test user by `admin_client`), the
endpoint, `AssignmentService`, `GlobalHoldoutService`, and PostgreSQL — and the assertions
read the tables back. The helper `_users_by_holdout_membership` generates ids and sorts them
by the _real_ holdout hash, so the test can't pass by accident.

The fixture that builds the experiment shows the data-shape discipline the harness relies
on — unique suffixes instead of truncation:

```python
# 📁 backend/tests/integration/api/test_tracking_api.py
def active_experiment(db_session, make_experiment):
    """An ACTIVE experiment with a control and a treatment variant."""
    suffix = uuid.uuid4().hex[:8]
    experiment = make_experiment(
        name=f"Tracking API {suffix}",
        key=f"tracking-api-{suffix}",
        status=ExperimentStatus.ACTIVE,
    )
    for name, is_control in (("control", True), ("treatment", False)):
        db_session.add(Variant(experiment_id=experiment.id, name=name, description=f"{name} variant",
                               is_control=is_control, traffic_allocation=50, configuration={"color": name}))
```

And the unit-side twin, with mocks used for what they're good at — driving a branch that
would take real infrastructure to reach:

```python
# 📁 backend/tests/unit/services/test_assignment_service_eligibility.py
"""
Unit tests for the eligibility gate in ``AssignmentService.assign_user``.

New users are checked, in order, against the global holdout, the experiment's
mutual exclusion group and its targeting rules.  These tests use a MagicMock
session and stub the holdout / mutual-exclusion services so each branch can be
driven directly; the DB-backed behaviour lives in
``backend/tests/integration/api/test_tracking_api.py::TestAssignEligibility``.
"""
```

Some tests don't check behaviour at all; they check that a **rule can't be bypassed by
adding code**. Hour 12's rule is that every route that changes a flag calls
`can_act_on_feature_flag`. A rule like that fails silently: a new route that forgets the call
works, passes its own tests, and lets a VIEWER change flags. So a smoke test pins the exact
inventory of mutating routes and how each is guarded:

```python
# 📁 backend/tests/smoke/test_flag_change_routes_guarded.py
pytestmark = [pytest.mark.smoke, pytest.mark.regression]

PREFIXES = ("/api/v1/feature-flags", "/api/v1/rollout-schedules", "/api/v1/safety")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# How each route is guarded.
FLAG_RULE = "calls can_act_on_feature_flag"
ROLLOUT_RULE = "calls _require_update_on_flag (UPDATE on the schedule's flag)"
CREATE_GATE = "role-gated via Depends(can_create_feature_flag)"
...
INVENTORY = {
    ("POST", "/api/v1/feature-flags/"): CREATE_GATE,
    ...
    ("PUT", "/api/v1/feature-flags/{flag_id}"): FLAG_RULE,
    ("DELETE", "/api/v1/feature-flags/{flag_id}"): FLAG_RULE,
    ...
```

A new mutating route under those prefixes fails the suite until someone classifies it here,
which forces the question "what guards this?" at review time instead of after an incident.

Note the second marker in both `pytestmark` lists: **`regression`**. The house rule is that
every bug fix carries a `@pytest.mark.regression` test that fails on the old code. The marker
is declared in `pyproject.toml` (`"regression: guards a specific past bug"`), `pytest -m
regression` runs just those, and a CI job (`regression-guard`) fails a pull request labelled
`bug` that changes no test file.

> **Watch Out!** `with TestClient(app) as test_client:` runs the **lifespan**; a bare
> `TestClient(app)`, which is what the shared fixture builds, does not. A test that opts into
> the context-manager form gets the lifespan's start-up checks, and the scheduler guard: the
> lifespan skips the schedulers under `settings.is_test`, because before that guard such a test
> started five background loops against the test database. If you construct the app with
> non-test settings in a test and enter it as a context manager, you'll start them.

> **Did You Know?** The `-x` flag on the CI unit job (stop at first failure) plus
> `--tb=short` is a deliberate feedback-speed choice for a suite of several thousand tests
> (the app README counts 5,400+ backend tests); the nightly run drops `-x` so you see
> everything. Locally, `-p no:cov -q` is the fast form; coverage is
> explicit (`--cov=backend.app --cov-report=term`) and configured in `pyproject.toml`.

### Try It Yourself

Write the pair for `POST /feature-flags/{id}/activate`:

1. **API test** with the `client` fixture: create a flag via `POST /api/v1/feature-flags/`,
   `POST .../activate`, then `GET /api/v1/feature-flags/evaluate/{key}?user_id=u` via an API
   key (create one with `POST /api/v1/api-keys` and send `X-API-Key`). Assert `reason ==
   "rollout"` and that `db_session.query(FeatureFlag).get(id).status == FeatureFlagStatus.ACTIVE`.
2. **Unit test** of `FeatureFlagService.activate_feature_flag` with a `MagicMock` session:
   assert the status is set and `commit` was called once; name the API test in the docstring.

Run both: `python -m pytest backend/tests/integration/api/<your file> backend/tests/unit/services/<your file> -p no:cov -q`.

### Common mistakes

- **Mocking the service the endpoint calls.** You've tested that a mock was called.
- **Forgetting `dependency_overrides.clear()`.** The next test runs as your user.
- **Asserting only on the response.** The database is the oracle; read the row.
- **Reusing fixed names/keys across tests.** The session fixture doesn't truncate; use
  unique suffixes.

### Key takeaways

- `TestClient(app)` + `dependency_overrides` = real routing, middleware, validation,
  endpoint, service and database, with only the leaves swapped.
- Overrides are keyed by function object and must be cleared.
- Mock what you don't own; drive what you do; unit tests name their integration twin.
- Pin rules that new code could bypass (the flag-route inventory); every bug fix gets a
  `@pytest.mark.regression` test.
- Assert on rows, not just JSON; generate unique data.

---

## Hour 23 — DB-backed tests: the harness, per-process databases, async tests

### What & why

The database test harness provides an isolated database, a schema matching the models, a controlled
session lifetime and cleanup. Isolation prevents a test run from modifying the development database
or interfering with another process. The conftest documents how these resources are allocated.

1. **Environment before import.** The schema name is fixed when `backend.app.models` is
   imported (`get_schema_name()` reads `APP_ENV`/`TESTING`), so the conftest sets them at the
   top of the module — and pops `ENVIRONMENT`, because a developer's shell may export
   `ENVIRONMENT=development`, which would win.
2. **A database per process.** `experimentation_test_<pid>`, dropped and recreated at session
   start with autocommit DDL. Two `pytest` runs, or `pytest-xdist` workers, never collide.
3. **No truncation between tests.** `TRUNCATE … CASCADE` takes an `AccessExclusiveLock` on
   every table in the cascade set and deadlocked against async fixtures overlapping; instead
   every test uses UUIDs and unique suffixes, and the database is dropped next session.
4. **`expire_on_commit=False`.** The test session is bound to a `NullPool` engine; after a
   commit, an expired attribute would re-open a connection that may be gone.

### In the wild

The header that sets the process up:

```python
# 📁 backend/tests/conftest.py
# * APP_ENV=test / TESTING=true select TestSettings (ENVIRONMENT == "test")
#   and the test_experimentation schema.  ENVIRONMENT itself is deliberately
#   NOT exported: tests that construct ProdSettings()/DevSettings() directly
#   must keep their class default, and the settings singleton already resolves
#   APP_ENV=test to the canonical "test".
# * Auth runs exactly as the core profile ships: AUTH_PROVIDER=local and
#   the dev-admin bypass OFF (fail-closed).  ...
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("TESTING", "true")
# A developer shell may export ENVIRONMENT=development (see .env.example);
# ENVIRONMENT wins over APP_ENV, so drop it for the test process.
os.environ.pop("ENVIRONMENT", None)

from fastapi.testclient import TestClient
...
from backend.app.main import app
...
# Pin the auth mode on the singleton (see the header comment) regardless of
# what a developer's shell exports.
settings.AUTH_PROVIDER = "local"
settings.DEV_AUTH_BYPASS = False

# Each pytest process gets its own database named
# "experimentation_test_<pid>".
_PID = os.getpid()
_TEST_DB_NAME = f"experimentation_test_{_PID}"
# Honour the same POSTGRES_* variables that the app, CI workflows and
# scripts/run-backend-tests.sh use, so the compose test profile (port 5433)
# and GitHub Actions service containers both work without editing this file.
_DB_USER = os.environ.get("POSTGRES_USER", "postgres")
_DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "postgres")
_DB_HOST = os.environ.get("POSTGRES_SERVER") or os.environ.get("POSTGRES_HOST") or "localhost"
_DB_PORT = os.environ.get("POSTGRES_PORT", "5432")
```

The session fixture, with its reasoning in the docstring:

```python
# 📁 backend/tests/conftest.py
@pytest.fixture(scope="function")
def db_session(test_db):
    """Create a fresh database session for a test.

    The session is bound directly to the NullPool engine so that after each
    commit() the session acquires a fresh DBAPI connection on its next DML.

    expire_on_commit=False prevents SQLAlchemy from expiring all loaded
    objects after a commit().  Without this, attributes like
    user.is_superuser trigger a reload SELECT on the next access, which
    opens a new NullPool connection that may fail if the prior commit
    already closed the underlying DBAPI connection.

    We do NOT truncate tables in teardown.  TRUNCATE TABLE ... CASCADE holds
    AccessExclusiveLock on every table in the cascade set.  When pytest
    runs the teardown of one function-scoped fixture while setting up the
    next (which can happen when asyncio test fixtures overlap in the event
    loop), the TRUNCATE and the new test's INSERT deadlock against each
    other.  Since all test data uses UUID primary keys and unique per-test
    suffixes, there is no data contamination between tests.  The entire
    database is dropped and recreated at the start of every pytest session
    (in test_db), so accumulated rows are discarded after each full run.
    """
    Session = sessionmaker(bind=test_db, autocommit=False, autoflush=False, expire_on_commit=False)
    session = Session()

    # Set schema search path for the initial connection.
    # The engine-level "connect" event (registered in test_db) re-applies
    # search_path on every new NullPool connection checkout automatically.
    session.execute(text("SET search_path TO test_experimentation"))

    try:
        yield session
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        try:
            session.close()
        except Exception:
            pass
```

The `test_db` fixture (session-scoped) disposes the production engine's pool before dropping
the database — otherwise `pg_terminate_backend` kills pooled connections and the pool later
tries to roll them back, producing "server closed the connection unexpectedly" tracebacks
that cascade through subsequent tests. Read its docstring once; you'll recognise the
symptom forever.

Async tests, for the one async dependency a flag route uses — the create gate:

```python
# 📁 backend/tests/unit/api/test_permission_deps.py
    @pytest.mark.asyncio
    @patch("backend.app.api.deps.check_permission")
    async def test_can_create_feature_flag_without_permission(
        self, mock_check_permission, viewer_user
    ):
        """Test user without create permission cannot create a feature flag."""
        # Arrange
        mock_check_permission.return_value = False

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await can_create_feature_flag(viewer_user)

        assert exc_info.value.status_code == 403
        mock_check_permission.assert_called_with(
            viewer_user, ResourceType.FEATURE_FLAG, Action.CREATE
        )
```

`pytest-asyncio` (1.3 in `backend/requirements.txt`) runs `async def` tests on an event loop.
`pyproject.toml` sets `asyncio_mode = "auto"`, so every `async def` test is collected as an
asyncio test whether or not it carries `@pytest.mark.asyncio`; the marker you see on older
tests is harmless and no longer required. Sync code is called directly in plain `def` tests.
Mixing them — `await` on a sync function, or a sync call of an async one — is the error the
async/sync rule in `CLAUDE.md` exists to prevent.

And the sync rule every flag change goes through, `can_act_on_feature_flag`, is tested as a
plain function — no event loop, no database, no `Depends`:

```python
# 📁 backend/tests/unit/core/test_feature_flag_access_rule.py
pytestmark = [pytest.mark.unit, pytest.mark.regression]
...
def test_the_role_matrix_is_checked_before_ownership():
    """Owning a flag never lets a read-only role change it."""
    viewer = _user(UserRole.VIEWER)
    anything = {UserRole.VIEWER: {Action.UPDATE: Scope.ANY}}
    assert can_act_on_feature_flag(viewer, viewer.id, Action.UPDATE, anything) is False
```

Its integration twin drives the same rule through the HTTP routes with a non-superuser of
each role: `backend/tests/integration/api/test_feature_flag_ownership.py` for the per-flag
routes and `backend/tests/integration/api/test_flag_change_routes_access.py` for bulk-toggle
and rollout schedules.

`monkeypatch` for the seams you _don't_ own — here the Cognito service — and for settings
on the singleton:

```python
    monkeypatch.setattr("backend.app.services.auth_service.CognitoAuthService.get_user_with_groups", mock_get_user_with_groups)
    monkeypatch.setattr(settings, "SAFETY_CHECK_INTERVAL_MINUTES", 1)
```

`monkeypatch` reverts automatically at test end; `unittest.mock.patch` needs a context
manager or decorator. Both are fine; `patch.object(SomeClass, "__new__")` is not — it
permanently breaks the class's constructor slot in CPython, and it caused one of the
cross-suite pollution bugs the audit found.

> **Watch Out!** `backend/tests/integration/auth/` is a special package: its own conftest
> selects `AUTH_PROVIDER=cognito` and `moto`'s `mock_cognitoidp`, and its CI job has **no
> Postgres service**. Any auth test that needs a database goes in `integration/api/`. moto is
> pinned at 4.2.14 — still 4.x, so `mock_cognitoidp`, `mock_dynamodb`, not the 5.x `mock_aws`.

> **By the Way.** The Lambda functions have their own test suites, each defining
> `tests.conftest`, so they must run one directory at a time — CI loops over
> `backend/lambda/{event_processor,feature_flag_evaluation,shared}` with a separate `pytest`
> per directory. Running `pytest backend/lambda` in one go collides.

### Try It Yourself

Prove the isolation:

```bash
source venv/bin/activate && export APP_ENV=test TESTING=true
# two concurrent sessions, two databases
(python -m pytest backend/tests/unit/services/test_cuped_service.py -p no:cov -q &) ; python -m pytest backend/tests/unit/services/test_bayesian_service.py -p no:cov -q
docker compose exec postgres psql -U postgres -c "select datname from pg_database where datname like 'experimentation_test_%'"   # gone after the sessions end
```

Then write a fixture in the house style — `make_flag(**overrides)` that inserts a
`FeatureFlag` with a unique key and returns it — and use it in the API test from Hour 22.

### Common mistakes

- **Setting `APP_ENV` after importing the app** in an ad-hoc script — wrong schema.
- **Fixed keys/names across tests** — collisions on unique indexes because nothing
  truncates.
- **Reading attributes after commit** on a hand-made session without
  `expire_on_commit=False`.
- **`pytest backend/lambda`** as one run; **DB-backed tests under `integration/auth/`**.

### Key takeaways

- Env before import; `ENVIRONMENT` popped; `AUTH_PROVIDER=local`, bypass off (fail-closed).
- One database per process; no truncation; unique data; drop at next session.
- `db_session`: `NullPool` engine, `expire_on_commit=False`, `search_path` set, rollback +
  close in teardown.
- `async def` + `await` for async deps (`asyncio_mode = "auto"` makes the marker optional);
  `monkeypatch` for external seams and settings; never `patch.object(..., "__new__")`.

---

## Hour 24 — Observability and the production checklist: the Experimently FastAPI style guide

### What & why

The API exposes three operational signals for different consumers. This lesson explains their
purpose and then collects the backend conventions into a pull-request review checklist.

- **Structured logs** (`structlog`): JSON outside development (for CloudWatch Logs
  Insights), console in development; every line carries the request id, path and method
  bound by the request-id middleware (the outermost layer after CORS).
- **Prometheus `/metrics`**: HTTP latency histogram and counters, business counters
  (assignments, events, flag evaluations), rate-limit hits/rejections, scheduler ticks and
  last-success timestamps.
- **Health probes**: `/health/live` (process up; touches nothing), `/health/ready`
  (PostgreSQL answers; Redis reported, fatal only if `REDIS_REQUIRED`), `/health` (alias).

### In the wild

Logging is configured before anything else in `main.py`, and the request-id middleware
binds a `ContextVar` that a structlog processor injects into every event:

```python
# 📁 backend/app/main.py
# LOG_FORMAT=json  -> every line (structlog, stdlib, uvicorn) is one JSON object
# LOG_FORMAT=console -> coloured console output
# unset            -> console in development/test, JSON everywhere else
_log_format: str = "console"
if _monitoring_imports_ok:
    _log_format = str(getattr(settings, "LOG_FORMAT", "") or "").lower() or log_format_from_env(
        default="console" if is_development_or_test() else "json"
    )
_json_logs: bool = _log_format == "json"
if _monitoring_imports_ok:
    configure_logging(
        log_level=str(settings.LOG_LEVEL or "INFO").upper(),
        json_logs=_json_logs,
        service_name="experimentation-platform",
    )
```

```python
# 📁 backend/app/core/logger.py
_log_context: contextvars.ContextVar[dict] = contextvars.ContextVar("log_context", default={})


def bind_log_context(**kwargs: Any) -> None:
    """Merge *kwargs* into the current async-task log context."""
    current = _log_context.get().copy()
    current.update(kwargs)
    _log_context.set(current)


def _inject_context(logger: Any, method: str, event_dict: dict) -> dict:
    """structlog processor: inject per-request context into every log event."""
    event_dict.update(_log_context.get())
    return event_dict
```

Metrics are a registry of typed series with label-safe recorders; the middleware normalises
paths so UUIDs don't explode the label space:

```python
# 📁 backend/app/core/metrics.py
http_request_duration_seconds: Histogram = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint", "status_code"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

feature_flag_evaluations_total: Counter = Counter(
    "feature_flag_evaluations_total",
    "Total feature flag evaluations",
    ["flag_key", "result"],
)
```

The probes, with the asymmetry that matters for orchestrators spelled out:

```python
# 📁 backend/app/core/health.py
"""
``GET /health/live``
    Liveness: the process is up and the event loop answers. Never touches the
    database or Redis, so a dependency outage does not make orchestrators
    restart healthy processes. Used by the container ``HEALTHCHECK``.

``GET /health/ready``
    Readiness: PostgreSQL must answer ``SELECT 1``. Redis is checked and
    reported but only fails readiness when ``REDIS_REQUIRED=true`` (the
    application degrades gracefully without Redis: rate limiting falls back to
    memory, caching is skipped). Disk space below 1 GB is reported as ``low``.
    The profile the process runs (``core`` or ``full``) is reported as
    ``profile`` and in ``checks.modules``, and never gates readiness. Returns
    200 when ready, 503 otherwise.
...
"""
...


def _check_failed(check: str, exc: BaseException) -> Dict[str, Any]:
    """The result of a check that raised: the error's type, never its text.

    The text of a database, Redis or OS error names hosts, ports, users and
    paths, and the readiness probe is unauthenticated, so the response
    carries only the type; the full error goes to the server log.
    """
    logger.warning(
        "Readiness check %s failed (%s)", check, type(exc).__name__, exc_info=exc
    )
    return {"status": "unhealthy", "error": type(exc).__name__}
```

`_check_failed` is the kind of detail a style guide should call out. The readiness probe is
unauthenticated, and the text of a database or Redis error names hosts, ports, users — and
psycopg2 quotes the whole DSN, password included. So the database, Redis and disk checks put
only the exception's **type** in the response (`"error": "OperationalError"`) and send the
full error to the server log, where it belongs. The one check that does report a message,
the modules check, passes it through `_scrub` first: cut to one line of at most 200
characters **before** any pattern runs (so a huge message can't make the regexes slow),
then URL credentials, ARN account ids and `name=value` secrets are masked, and a line that
mentions a password is dropped whole. The response also carries `profile` (`core` or
`full`), which is how you tell, from outside, which build a process is running.

### The Experimently FastAPI style guide

Everything this book taught, as review checklist items. If a pull request violates one,
it's a comment; if it violates several, it's a conversation.

**Structure**

- One `APIRouter` per resource module; mounted in `api/api.py` with a prefix and a tag and a
  comment naming the epic.
- Endpoint → service → model. Endpoints parse, authenticate, delegate, serialise. Logic lives
  in services. Models are tables.
- Fully qualified imports: `from backend.app.<...> import <...>`. Never rooted at `app.` —
  `app.models.x` and `backend.app.models.x` are two different module objects, and SQLAlchemy
  then sees two classes for one table. (A relative `from .base import Base` inside
  `backend/app/models/` resolves to the same module and is fine.) A new core model is added to
  `CORE_MODEL_MODULES` in `backend/app/models/__init__.py`, which `register_core_models()`
  imports; a module's models are registered through the modules seam, never imported by core.

**Requests and responses**

- Inputs are declared (`Path`, `Query`, `Body`, `Depends`) with constraints and descriptions;
  nothing is parsed by hand except URL-encoded JSON in a query string, which raises the same
  422.
- Schemas are Pydantic v2 (`field_validator`, `model_validator`, `ConfigDict`). Request
  models refuse what they don't understand (the flag requests use `extra="forbid"`); a
  response model never refuses a stored row and has `from_attributes=True`; updates use
  `exclude_unset=True`.
- Invariants are on the schema (for the message) _and_ in the database (for the guarantee).
- Refuse with `HTTPException(status, detail, headers)`; `except HTTPException: raise` before
  any generic `except`, and the generic branch raises `unexpected_failure(...)`: a fixed
  sentence plus the request id, never the exception's text; 200 + `reason` for normal
  negatives, 404 for unknown, 422 for malformed, 423 + `Retry-After` for lockout.

**Dependencies and auth**

- `db: Session = Depends(deps.get_db)` in every endpoint; services receive the session;
  schedulers and scripts own theirs with `try/finally`.
- Dashboard endpoints depend on `get_current_active_user` (or `get_current_superuser`); SDK
  endpoints on `get_api_key`. Never both.
- Access is by **role**, never by who created the thing. Every route that changes a flag
  calls the sync `can_act_on_feature_flag(user, flag.owner_id, action)` inline; only create is
  an async `Depends(deps.can_create_feature_flag)`. Experiment routes use
  `get_experiment_read_access` / `get_experiment_change_access(experiment, user, action)`.
  A new flag-changing route is classified in `test_flag_change_routes_guarded.py`.
  Superuser and `ADMIN` are different bits.
- Security schemes use `auto_error=False`; the dependency decides what "missing" means.
- Never call `core.security.decode_token` for validation.

**Data**

- `class X(Base, BaseModel)`; schema in every FK string and `__table_args__`; schema-prefixed
  index names; `back_populates` on both sides; `ondelete` matching the cascade unless
  commented.
- `add → flush → commit → refresh` in services; no commits in endpoints; `synchronize_session=False`
  for bulk deletes; `joinedload` small collections, count large ones.
- `text()` only when the builder can't; bound values; the schema is the only interpolated
  identifier; `nosemgrep` + `nosec` with a justification.
- Migrations: a full checkout has **two heads** (the core chain and the `modules` branch).
  `upgrade heads`, plural; `revision --autogenerate --head <core head id>` (or
  `--head modules@head`), then read it; minimal, `down_revision` id, nullable/additive, schema
  helper. Never `alembic merge` the two heads. Fresh databases use the bootstrap.
- Conversions match through `services/event_matching.py`. Targeting rules pass through
  `core/targeting_adapter.py`. No fourth format.

**Beyond requests**

- Long-lived tasks start in the lifespan, skip under test, run ticks via `run_locked_tick`,
  offload heavy sync work with `asyncio.to_thread`, let `CancelledError` propagate.
- WebSockets authenticate at the handshake (the subprotocol, or a bearer header), accept-then-close
  with `4401`, release the auth session, use a session factory per snapshot.
- Redis is optional everywhere: fallback limiter, disabled cache, readiness unaffected unless
  `REDIS_REQUIRED`. Cache keys include every input; every `set` has a TTL.

**Configuration**

- One `settings` singleton; canonical `ENVIRONMENT`; validators fail closed in production;
  `NoDecode` for comma-separated lists; new settings declared after `ENVIRONMENT` if a
  validator reads it.

**Tests**

- Mock what you don't own; drive what you do. Hot paths have an API test through
  `TestClient` against a real database; unit tests name their integration twin.
- Env before import; unique data; `hashed_password` on test users; unit tests delete what
  they commit; DB-backed auth tests outside `integration/auth/`; Lambda suites one directory
  at a time; `async def` tests for async deps (`asyncio_mode = "auto"`).
- Every bug fix carries a `@pytest.mark.regression` test that fails on the old code.

**Operations**

- Every log line has a request id; every response echoes `X-Request-ID`.
- New metrics go in `core/metrics.py` with bounded label cardinality.
- `/health/live` never touches dependencies; `/health/ready` decides load-balancer
  membership; it reports an error's type, never its text.

**Lint and format**

- One tool: **ruff** (format, lint and import order; it replaced black, isort and flake8),
  configured in `[tool.ruff]` in `pyproject.toml`, over `backend/ modules/ scripts/`.
- `make format` rewrites in place; `make lint` runs everything the `lint` CI job runs:
  `ruff check` and `ruff format --check`, the import-linter boundary between core and the
  modules, `reuse lint` (every file carries a licence), the requirements-lock check, eslint and
  `tsc` for the dashboard, hadolint for the Dockerfiles and actionlint for the workflows.
- `mypy backend/app/` is available for local type checking, but no CI job runs it; it is not
  part of what a pull request must pass.
- A change is done when `make lint` is clean, the required pull-request checks are green and
  the review's findings are addressed.

### Try It Yourself

Review a real pull request against the guide. Pick the most recent merged PR
(`gh pr list --state merged --limit 1`), read its diff, and write down every item above it
touches — and whether it complied. Then run the three signals against your local API for one
request:

```bash
curl -si -H "X-Request-ID: style-guide-001" -H "Authorization: Bearer $TOKEN" $API/experiments/ | grep -i x-request-id
docker compose logs api | grep style-guide-001 | head -2
curl -s localhost:8000/metrics | grep -E "^http_request_duration_seconds_count.*experiments" | head -2
curl -s -o /dev/null -w "live=%{http_code} " localhost:8000/health/live; curl -s -o /dev/null -w "ready=%{http_code}\n" localhost:8000/health/ready
```

### Key takeaways

- Logs: structlog, JSON outside dev, request id via `ContextVar`; metrics: one registry,
  normalised labels; probes: live vs ready, scrubbed errors.
- The style guide is the book: structure, requests/responses, dependencies/auth, data,
  beyond-requests, configuration, tests, operations.
- Review against it; ship when the gates are green.

---

## Part VI Workshop

**Q1. Why is a test that mocks `EventService.track_event` insufficient for
`POST /tracking/track`?**
It proves the endpoint called _something_, not that the endpoint's `EventCreate` matches
what the real service accepts. The shape mismatch that broke the tracking API lived exactly
in that gap. Drive the endpoint through `TestClient` and read the `events` row.

**Q2. What does `with TestClient(app) as c:` do that `TestClient(app)` alone doesn't?**
Runs the lifespan (startup before, shutdown after). Under the test profile the lifespan
starts no schedulers; with non-test settings it would.

**Q3. Why does the harness pop `ENVIRONMENT` from the environment?**
`ENVIRONMENT` wins over `APP_ENV`; a developer's shell exporting `ENVIRONMENT=development`
would otherwise select `DevSettings` and the `experimentation` schema for the test process.

**Q4. Why no `TRUNCATE` between tests, and what discipline replaces it?**
`TRUNCATE … CASCADE` takes exclusive locks on every table in the cascade set and deadlocked
against overlapping async fixtures. Instead: UUID keys, unique per-test suffixes, unit tests
delete what they commit, and the database is dropped at the next session start.

**Q5. `/health/live` is 200, `/health/ready` is 503. What's wrong and what should the
orchestrator do?**
PostgreSQL isn't answering (or Redis with `REDIS_REQUIRED`). Remove the task from the load
balancer; don't restart it — liveness is fine and a restart wouldn't fix the database.

**Q6. A PR adds `db.commit()` inside an endpoint after calling a service. Style-guide
comment?**
Commits belong in services, next to the logic that defines the transaction boundary; the
endpoint may invalidate caches after the service returns, not commit.

**Quiz.** Name the three signals and the consumer of each.
→ Structured JSON logs → CloudWatch Logs Insights (and `grep` locally, by request id);
Prometheus `/metrics` → the scrape/alerting stack (`scheduler_last_success_timestamp`,
`rate_limit_rejections_total`); health probes → the container `HEALTHCHECK` (live) and the
load balancer / compose `--wait` (ready).

---

## Exercises

**E6.1 — The twin pair.** For `POST /feature-flags/{id}/deactivate`, write the API test
(create → activate → deactivate → evaluate returns `reason: inactive` with 200) and the unit
test (service sets `INACTIVE`, commits once), with the docstring cross-reference.

**E6.2 — A harness bug.** A new test creates a `User(username="tester", email=
"tester@example.com")` and passes alone but fails in the full run with a unique-constraint
error. Explain, and fix it in the house style.

**E6.3 — Role, not owner.** Write unit tests that pin "access is by role, never by who
created it": (a) `deps.get_experiment_change_access` refuses a VIEWER who **created** the
experiment, and admits a DEVELOPER who didn't; (b) `can_act_on_feature_flag` refuses an
ANALYST deleting a flag the analyst owns. Are these sync or async tests, and why?

**E6.4 — A metric.** Add a counter for safety rollbacks (labelled by flag key and trigger
type) to `core/metrics.py` with a recorder, and say where in the safety scheduler you'd call
it. What label would you refuse to add, and why?

**E6.5 — Review.** Here is a diff summary; list every style-guide item it violates:
_"Adds `GET /api/v1/reports/{id}/pdf`: imports `SessionLocal` and opens a session in the
handler; accepts either a bearer token or an API key; builds the PDF with data from
`db.execute(text(f"SELECT ... WHERE id = '{id}'"))`; returns the bytes in a dict
`{"pdf": base64}` with `response_model=Dict[str, Any]`; wraps the body in `try/except
Exception: return {"error": str(e)}`."_

---

## Solutions

**S6.1**

```python
# backend/tests/integration/api/test_<your_name>.py
def test_deactivated_flag_evaluates_inactive(client, api_key_header, db_session):
    """API twin of the unit test test_deactivate_sets_inactive."""
    key = f"lifecycle-{uuid.uuid4().hex[:8]}"
    created = client.post("/api/v1/feature-flags/", json={"key": key, "name": key, "rollout_percentage": 100}).json()
    assert client.post(f"/api/v1/feature-flags/{created['id']}/activate").status_code == 200
    assert client.post(f"/api/v1/feature-flags/{created['id']}/deactivate").status_code == 200
    resp = client.get(f"/api/v1/feature-flags/evaluate/{key}", params={"user_id": "u"}, headers=api_key_header)
    assert resp.status_code == 200 and resp.json() == {"key": key, "enabled": False, "config": None, "reason": "inactive"}
    assert db_session.get(FeatureFlag, uuid.UUID(created["id"])).status == FeatureFlagStatus.INACTIVE.value


# backend/tests/unit/services/test_<your_name>.py
@patch("backend.app.services.feature_flag_service.flag_to_read")
def test_deactivate_sets_inactive(_to_read):
    """Unit twin of the API test test_deactivated_flag_evaluates_inactive."""
    db = MagicMock()
    flag = MagicMock(status=FeatureFlagStatus.ACTIVE.value)
    FeatureFlagService(db).deactivate_feature_flag(flag)
    assert flag.status == FeatureFlagStatus.INACTIVE.value
    db.commit.assert_called_once()
```

(The service stores the status as its string value and returns `flag_to_read(flag)`, a
response model built from the row; patching `flag_to_read` keeps the unit test about the
transition and the commit, not about serialising a `MagicMock`.)

**S6.2** — The session fixture doesn't truncate, so a previous test in the same process (or
an earlier fixture) already inserted `tester@example.com`; the unique index on `users.email`
rejects the second. Fix: unique suffixes —
`suffix = uuid.uuid4().hex[:8]; User(username=f"tester-{suffix}", email=f"tester-{suffix}@example.com", hashed_password="x", ...)`
— and, if the test commits, delete the row in teardown.

**S6.3**

```python
from types import SimpleNamespace

from backend.app.api import deps
from backend.app.core.permissions import Action, can_act_on_feature_flag
from backend.app.models.user import UserRole

pytestmark = [pytest.mark.unit]


def _user(role):
    return SimpleNamespace(id=uuid.uuid4(), role=role, is_superuser=False)


def test_viewer_cannot_change_an_experiment_it_created():                    # sync
    viewer = _user(UserRole.VIEWER)
    experiment = SimpleNamespace(id=uuid.uuid4(), owner_id=viewer.id)
    with pytest.raises(HTTPException) as exc:
        deps.get_experiment_change_access(experiment, viewer, Action.UPDATE)
    assert exc.value.status_code == 403


def test_developer_may_change_an_experiment_someone_else_created():         # sync
    developer = _user(UserRole.DEVELOPER)
    experiment = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    assert deps.get_experiment_change_access(experiment, developer, Action.UPDATE) is experiment


def test_analyst_cannot_delete_a_flag_it_owns():                            # sync
    analyst = _user(UserRole.ANALYST)
    assert can_act_on_feature_flag(analyst, analyst.id, Action.DELETE) is False
```

All three are plain sync tests: `get_experiment_change_access` and `can_act_on_feature_flag`
are ordinary functions that the routes call inline after loading the row, so there is no
event loop and no `Depends` chain to run. Note the asymmetry in what they return: the
experiment check **raises** 403 itself, while the flag rule **returns a bool** and the route
turns `False` into the 403. Each test sets ownership the "wrong" way on purpose — the creator
refused, the stranger admitted — because a test where owner and role agree can't tell a
role rule from an ownership rule.

**S6.4**

```python
# core/metrics.py
safety_rollbacks_total: Counter = Counter(
    "safety_rollbacks_total", "Automatic and manual safety rollbacks", ["flag_key", "trigger_type"],
)

def record_safety_rollback(flag_key: str, trigger_type: str) -> None:
    safety_rollbacks_total.labels(flag_key=flag_key, trigger_type=trigger_type).inc()
```

Call it in `SafetyScheduler.check_feature_flags_safety` right after `rollback_result.success`
(and in the manual rollback endpoint with `trigger_type="manual"`). Refuse a `user_id` or
`reason` label: unbounded cardinality would create a new series per user or per message and
exhaust the registry — labels must be bounded sets (flag keys, trigger types).

**S6.5** — (1) `SessionLocal()` in a handler — must be `Depends(deps.get_db)`. (2) Accepting
bearer _or_ API key — one door per endpoint; reports are a dashboard resource →
`get_current_active_user` plus a role check. (3) An
f-string interpolating `id` into `text()` — SQL injection; bind it (`:id`), and use the
builder anyway. (4) `response_model=Dict[str, Any]` for a known shape, and base64 in JSON for
bytes — return a `StreamingResponse`/`Response(media_type="application/pdf")`. (5) `except
Exception: return {"error": ...}` — swallows `HTTPException`s into 200s with an error body;
use `except HTTPException: raise`, and `raise unexpected_failure(...)` for the rest (a fixed
sentence and the request id, never `str(e)`, which can carry a DSN or a query). (6) No test named;
a hot-ish path needs an API test. Six comments, one conversation.

---

_You've done the twenty-four hours. Take the [Build book](../build-a-platform-in-28-days/README.md)
next — it uses everything here to explain how the whole platform fits together. ⏱️_

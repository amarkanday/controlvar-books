# Week 2 — The API and the Data

### Days 8–14 · FastAPI, Configuration, Auth, RBAC, SQLAlchemy, Alembic

[← Week 1](./week-1-core-loop.md) · [Contents](./README.md) · [Week 3 →](./week-3-hard-parts.md)

Week 1 was _what_ the platform does. This week is the machinery every request passes through
on the way to doing it: the FastAPI application and its middleware onion, the settings object
that decides which environment you're in and refuses to start when it's unsafe, the three ways a
caller proves who they are, the role table that decides what they may do, the SQLAlchemy models
and the PostgreSQL schema they live in, and the migration story. By Day 14 you'll add a column
and watch it light up from the database to the dashboard.

> **The one sentence to carry through Week 2.** _A request enters through five middleware
> layers, is authenticated by one of three `deps` functions (bearer JWT, Cognito token, or API
> key), authorised by a role → resource → action table plus ownership, handled by a thin
> endpoint that delegates to a service, which reads and writes SQLAlchemy models inside one
> `Session` per request — and every one of those layers is configured by a single `Settings`
> object that fails closed in production._

| Day | Topic                               | The one thing it's really about                                     |
| --- | ----------------------------------- | ------------------------------------------------------------------- |
| 8   | The FastAPI application             | The onion of middleware, the router tree, and the lifespan          |
| 9   | Configuration                       | One `Settings` class, four environments, validators that refuse     |
| 10  | Authentication                      | Three credentials, one `get_current_user`, one `get_api_key`        |
| 11  | Authorization                       | Role × resource × action, plus ownership, in dependencies           |
| 12  | The models and the schema           | Declarative tables, relationships, JSONB, and the schema prefix     |
| 13  | Alembic and the bootstrap           | Incremental migrations for old databases, create_all for new ones   |
| 14  | **Capstone:** add a field end-to-end | Model → migration → schema → service → endpoint → dashboard       |

---

## Day 8 — The FastAPI application: routers, the middleware stack, the lifespan

### What & why

**FastAPI** is an ASGI web framework: you declare _path operations_ (`@router.get(...)`) as
Python functions whose parameters describe the request (path, query, body, headers,
dependencies), and the framework does the parsing, validation, OpenAPI generation and
serialisation. **Starlette** underneath provides the ASGI plumbing — middleware, routing,
WebSockets. **uvicorn** runs it.

Experimently uses three FastAPI features heavily, and today is about how they're arranged:

1. **Routers.** Each resource is an `APIRouter` in its own module; `api/api.py` mounts them
   all under `/api/v1` with a prefix and a tag. The tag becomes the section heading in the
   OpenAPI docs; the prefix becomes the URL.
2. **Middleware.** Cross-cutting behaviour — CORS, security headers, rate limiting, request
   IDs, Prometheus metrics — is a stack of ASGI middleware wrapped around the router. The
   order they're registered in is the opposite of the order they run, and the file says so.
3. **Lifespan.** Startup and shutdown are one async context manager that starts and stops the
   five background schedulers.

#### Why middleware order is stated, not assumed

Starlette applies middleware in **reverse registration order**: the first `add_middleware`
call becomes the innermost layer, the last becomes the outermost. That has consequences you
can reason about. The request-ID middleware is registered _last_ so it is the _outermost_
layer — meaning the request id is bound into the logging context before any other middleware
logs anything, and it's stamped on the response even if an inner layer short-circuits with a
429. The rate limiter sits inside it (so rejected requests still get an id and a metric) but
outside the router (so the handler never runs for a rejected request). CORS is registered
first, so it's innermost — closest to the handler — which is what you want: the CORS headers
apply to the real response.

### How Experimently does it

The app module reads top to bottom as configure → construct → wrap → mount:

```python
# 📁 backend/app/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background scheduler lifecycle for the FastAPI app.

    Under the test profile the schedulers are not started: a ``TestClient``
    used as a context manager would otherwise run real ticks against the test
    database in the middle of unrelated tests (they patch the same service
    functions, and the ticks open and drop connections). Scheduler behaviour
    is covered directly in ``backend/tests/unit/core/test_scheduler_*``.
    """
    if settings.is_test:
        logger.info("Test environment: background schedulers are not started")
        yield
        return

    logger.info("Starting experiment scheduler")
    await experiment_scheduler.start()

    logger.info("Starting rollout scheduler")
    await rollout_scheduler.start()

    logger.info("Starting metrics scheduler")
    await metrics_scheduler.start()

    logger.info("Starting safety monitoring scheduler")
    await safety_scheduler.start()

    logger.info("Starting bandit scheduler")
    await bandit_scheduler_runner.start()

    yield

    logger.info("Stopping experiment scheduler")
    await experiment_scheduler.stop()
    ...


# Create FastAPI application
app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.PROJECT_DESCRIPTION,
    version=settings.VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware stack
# Starlette applies middleware in *reverse* registration order so the first
# add_middleware call wraps the outermost layer.
# ---------------------------------------------------------------------------

# Add CORS middleware — restrict methods and headers to what the API actually needs
cors_origins = [str(origin) for origin in settings.BACKEND_CORS_ORIGINS]
if not cors_origins:
    # Plain comma-separated form (CORS_ORIGINS=http://a,http://b), see .env.example
    cors_origins = [origin for origin in settings.CORS_ORIGINS if origin]
if not cors_origins:
    # Fall back to dev defaults
    cors_origins = [
        "http://localhost:3100",
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3200",  # ShopLab demo storefront
        "http://localhost:3300",  # StreamPulse demo app
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)

# Security headers — lightweight and stateless
app.add_middleware(SecurityHeadersMiddleware)

# Rate limiter — disabled during tests to avoid interfering with test assertions
_rate_limit_enabled = not settings.is_test
app.add_middleware(RateLimitMiddleware, enabled=_rate_limit_enabled)

# Prometheus latency / request-count middleware (EP-013)
if _monitoring_imports_ok:
    app.add_middleware(PrometheusMetricsMiddleware)
    # X-Request-ID on every response + request_id/path/method bound into the
    # log context. Registered last so it is the outermost layer and every
    # log line from the middlewares above already carries the id.
    app.add_middleware(RequestIDMiddleware)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Health probes (/health/live, /health/ready, /health) and Prometheus /metrics
app.include_router(health_router)
```

(The comment above `add_middleware(CORSMiddleware)` is slightly misleading — as the second
comment says, the _last_ registration is outermost; CORS, registered first, is innermost.)
So a request passes: **RequestID → Prometheus → RateLimit → SecurityHeaders → CORS →
router**, and the response walks back out the same way.

Two details in the CORS block are worth a pause because both were once bugs. `allow_headers`
includes `X-API-Key` — without it, a browser-based SDK (ShopLab) gets a CORS preflight
failure that looks like an auth error. And `CORS_ORIGINS` exists _alongside_
`BACKEND_CORS_ORIGINS` because the latter is a `List[AnyHttpUrl]` that pydantic-settings
expects as a JSON array, while operators naturally write a comma-separated string; `CORS_ORIGINS`
is annotated `NoDecode` so the raw string reaches the validator intact (Day 9).

The router tree is assembled in one place:

```python
# 📁 backend/app/api/api.py
api_router_v1 = APIRouter()

api_router_v1.include_router(auth.router, prefix="/auth", tags=["Authentication"])
# P0 (open-core): user-owned API keys for SDK authentication
api_router_v1.include_router(api_keys.router, prefix="/api-keys", tags=["API Keys"])
api_router_v1.include_router(users.router, prefix="/users", tags=["Users"])
api_router_v1.include_router(experiments.router, prefix="/experiments", tags=["Experiments"])
api_router_v1.include_router(tracking.router, prefix="/tracking", tags=["Tracking"])
# Client-side error reports (POST /tracking/errors, /tracking/errors/batch) feed
# feature flag safety monitoring; same prefix and API-key auth as event tracking.
api_router_v1.include_router(client_errors.router, prefix="/tracking", tags=["Tracking"])
api_router_v1.include_router(feature_flags.router, prefix="/feature-flags", tags=["Feature Flags"])
...
# Main API router that includes versioned routers
api_router = APIRouter()
api_router.include_router(api_router_v1)
```

Note that two routers can share a prefix (`tracking` and `client_errors` both mount under
`/tracking`), and that some mount at `prefix=""` (bulk toggle, WebSocket results) because
their paths are already complete. The OpenAPI document at `/api/v1/openapi.json` is generated
from this tree — and `tags_metadata` at the bottom of the file gives each tag its description.

#### A path operation, anatomised

Every endpoint in the repo follows one shape. Here is the simplest lifecycle endpoint with
the parts labelled:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
@router.post(                                      # ① HTTP method + path (under the router prefix)
    "/{experiment_id}/start",
    response_model=ExperimentResponse,             # ② the response schema (serialised + documented)
    summary="Start experiment",
    response_description="Returns the started experiment",
)
async def start_experiment(
    experiment_id: UUID = Path(..., description="The ID of the experiment to start"),  # ③ path param, parsed
    db: Session = Depends(deps.get_db),                                                # ④ a DB session per request
    current_user: User = Depends(deps.get_current_active_user),                        # ⑤ authentication
    cache_control: Dict[str, Any] = Depends(deps.get_cache_control),                   # ⑥ optional Redis
) -> ExperimentResponse:
    try:
        experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
        if not experiment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found")

        # Check access permission
        experiment = deps.get_experiment_access(experiment, current_user)             # ⑦ authorisation

        experiment_service = ExperimentService(db)                                     # ⑧ the service
        ...
        started_experiment = experiment_service.start_experiment(experiment)
        ...
        return ExperimentResponse.model_validate(started_experiment)                   # ⑨ ORM/dict → schema
    except HTTPException:
        # Let deliberate 4xx responses through instead of wrapping them in a 500.
        raise
    except Exception as e:
        logger.error(f"Error starting experiment: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

Parameters ③–⑥ are all _declared_, never parsed by hand; FastAPI turns a malformed UUID into a
422 before the function runs. ⑦ is a plain function call here (experiment dependencies are
sync) whereas the feature-flag equivalents are `async` and used with `Depends` — the
distinction `CLAUDE.md` insists on. ⑨ is Pydantic v2's `model_validate` with
`from_attributes=True` on the schema, so an ORM object or a dict both serialise. And the
`except HTTPException: raise` is a pattern you'll see everywhere: it exists because a bare
`except Exception` would turn a deliberate 404 into a 500.

> **Watch Out!** FastAPI ≥ 0.141 changed how `app.routes` exposes included routers: entries
> can be lazy `_IncludedRouter` objects with no `.path`. Anything that enumerates routes —
> the wiring smoke test, a script listing endpoints — must go through
> `effective_route_contexts` (see `backend/tests/smoke/test_wiring.py::_iter_http_routes`).
> If you write `for route in app.routes: route.path`, it will break.

> **By the Way.** The docs URLs are deliberately custom: `docs_url=None` disables the default
> `/docs`, and three explicit handlers serve `/api/v1/openapi.json`, `/api/v1/docs` (Swagger)
> and `/api/v1/redoc` under the versioned prefix so they sit behind the same reverse-proxy
> path as the API. `/health/*` and `/metrics` are the only unversioned routes.

### Build It

Watch the middleware order from the outside:

```bash
# a 429 still gets a request id and rate-limit headers: RequestID is outside RateLimit
for i in $(seq 1 12); do curl -s -o /dev/null -w "%{http_code} " -X POST $API/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"x@y","password":"z"}'; done; echo
curl -si -X POST $API/auth/login -H 'Content-Type: application/json' -d '{"email":"x@y","password":"z"}' \
  | grep -i "x-request-id\|x-ratelimit\|retry-after\|HTTP/"
```

(`/auth/login` is limited to 10/min — Day 9 lists the table.) Then read the router tree as
the feature list and count the tags:

```bash
python - <<'PY'
import json, urllib.request
spec = json.load(urllib.request.urlopen("http://localhost:8000/api/v1/openapi.json"))
tags = {}
for path, ops in spec["paths"].items():
    for method, op in ops.items():
        for t in op.get("tags", ["untagged"]):
            tags[t] = tags.get(t, 0) + 1
for t, n in sorted(tags.items(), key=lambda kv: -kv[1]): print(f"{n:4d}  {t}")
PY
```

### Key takeaways

- One `FastAPI` app; routers per resource mounted in `api/api.py` under `/api/v1`; tags are
  the doc sections.
- Middleware runs RequestID → Prometheus → RateLimit → SecurityHeaders → CORS → handler;
  registration order is the reverse, and the file says why.
- The lifespan starts and stops the five schedulers.
- An endpoint declares its inputs (`Path`, `Query`, `Body`, `Depends`), authenticates via
  `deps`, delegates to a service, and returns a schema; `except HTTPException: raise` keeps
  4xx honest.
- `X-API-Key` in `allow_headers` and the `CORS_ORIGINS` string form both exist because they
  were once bugs.

### Q&A

**Q. Why is the rate limiter disabled under the test profile (`settings.is_test`)?**
Because tests hammer endpoints far faster than 300/min and the limiter would turn assertions
about business logic into assertions about throttling. The limiter's own tests construct
`RateLimitMiddleware(enabled=True)` explicitly and test `resolve_rate_limit` as a pure function.

**Q. What's the difference between `Depends(deps.get_db)` and importing `SessionLocal`?**
`get_db` yields one session per request and closes it in a `finally`; FastAPI runs the
`finally` after the response is sent. Importing `SessionLocal` directly (as the schedulers
do) means _you_ own the close. Endpoints must use the dependency — it's also what the test
harness overrides to inject a transactional test session.

**Q. Why do some endpoints do `db.query(Experiment)` themselves instead of calling the
service?**
Pragmatism accumulated over time; the direction of dependency (endpoint → service → model) is
the rule, and endpoint-level queries for _lookups_ are tolerated. New logic goes in the
service; new lookups should too.

**Q. The tracking endpoints are `async def` but call synchronous SQLAlchemy. Doesn't that
block the event loop?**
Yes, for the duration of the query — and FastAPI runs `async def` handlers on the loop. The
platform accepts that (queries are short and the pool is sized for it) and runs one uvicorn
worker per container, scaling containers rather than workers. A `def` handler would be run in
a threadpool automatically; several endpoints (results) are `def` for exactly that reason.
Be consistent within a module.

---

## Day 9 — Configuration: `Settings`, environments, and the fail-closed validators

### What & why

Configuration is where a platform decides what it _is_ in this process: which database, which
auth provider, which environment, which secrets. Experimently uses **pydantic-settings**: a
`Settings` class whose fields are read from environment variables (and an optional `.env`
file), type-coerced, and validated — with the validation doing real safety work.

Three ideas organise the module:

1. **One canonical environment name.** `ENVIRONMENT ∈ {development, test, staging,
   production}`. The legacy spellings (`dev`, `prod`) and the legacy variable (`APP_ENV`) are
   accepted with a `DeprecationWarning`, and the canonical value is mirrored back into
   `APP_ENV` for the modules that still read it.
2. **A subclass per environment**, selected at import time: `DevSettings`, `TestSettings`,
   `ProdSettings`. They differ only in defaults (test database name, dev CORS origins, cache
   on/off).
3. **Validators that refuse to start** when a dangerous default reaches production — a weak
   `SECRET_KEY`, the dev `AUDIT_HMAC_KEY`, the dev-admin bypass. The principle is _fail
   closed_: a misconfigured production deployment should not come up at all.

#### Why "fail closed" belongs in the settings object

You could check for weak secrets at the call site — in the JWT signer, in the audit signer.
But then every new secret needs a new check, and one forgotten check is a production
deployment signing tokens with `"default-secret-key-for-testing"`. Putting the checks on the
`Settings` fields means they run once, at import, before the app can serve a single request,
and they run for every code path that reads the setting. The `TESTING` escape hatch exists so
the test suite can construct `ProdSettings()` to _test_ the validators without real keys.

### How Experimently does it

The environment resolution and the base class:

```python
# 📁 backend/app/core/config.py
EnvironmentName = Literal["development", "test", "staging", "production"]

# Environments in which the dev-admin auth bypass may run at all.
BYPASS_ALLOWED_ENVIRONMENTS: tuple = ("development", "test")


def resolve_environment_from_process_env() -> str:
    """
    Determine the canonical environment name from the process environment.

    ``ENVIRONMENT`` wins.  The legacy ``APP_ENV`` variable is honoured as a
    fallback (with a DeprecationWarning).  When neither is set the platform
    runs as ``development``.
    """
    raw = os.environ.get("ENVIRONMENT")
    legacy = os.environ.get("APP_ENV")
    if raw:
        canonical = canonical_environment(raw)
        if legacy and canonical_environment_quiet(legacy) != canonical:
            logger.warning(
                "ENVIRONMENT=%r and APP_ENV=%r disagree; ENVIRONMENT wins. "
                "Remove APP_ENV from your configuration.",
                raw, legacy,
            )
        return canonical
    if legacy:
        ...
        return canonical_environment(legacy)
    return "development"


class Settings(BaseSettings):
    """Base settings class."""

    PROJECT_NAME: str = "Experimentation Platform"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: EnvironmentName = "development"
    DEBUG: bool = False
    SECRET_KEY: str = "default-secret-key-for-testing"  # Default for testing only
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # 1 hour

    AUTH_PROVIDER: Literal["local", "cognito"] = "local"
    DEV_AUTH_BYPASS: bool = False

    LOCAL_AUTH_TOKEN_TTL_MINUTES: int = 720  # 12 hours
    LOCAL_AUTH_MAX_FAILED_ATTEMPTS: int = 10
    LOCAL_AUTH_LOCKOUT_MINUTES: int = 15
    BACKEND_CORS_ORIGINS: List[AnyHttpUrl] = []
    # CORS_ORIGINS is a plain-string list version of BACKEND_CORS_ORIGINS that
    # can also be set via env var as a comma-separated string. NoDecode stops
    # pydantic-settings from JSON-decoding the raw value so the "before"
    # validator below receives it as-is.
    CORS_ORIGINS: Annotated[List[str], NoDecode] = []

    # Database settings
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "experimentation"
    POSTGRES_PORT: str = "5432"
    ...
    # Background scheduler cadences (minutes). SafetyScheduler checks flag
    # error-rate/latency thresholds and auto-rolls back; RolloutScheduler
    # advances time-based rollout stages. Demos set both to 1.
    SAFETY_CHECK_INTERVAL_MINUTES: int = 5
    ROLLOUT_CHECK_INTERVAL_MINUTES: int = 15

    # Per-IP ceiling for SDK-facing endpoints (/tracking/*, flag evaluation).
    SDK_RATE_LIMIT_PER_MINUTE: int = 6000

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore"  # Ignore unknown fields to catch typos
    )
```

The validators are the safety layer. Two of them, in full, because their shape is the shape
of every guard in the file:

```python
# 📁 backend/app/core/config.py
    @model_validator(mode="after")
    def forbid_dev_auth_bypass_outside_dev(self) -> "Settings":
        """
        Refuse to start with DEV_AUTH_BYPASS enabled in staging/production.

        The bypass serves every request as an ADMIN without credentials; the
        only environments where that is acceptable are ``development`` and
        ``test``.  This check is deliberately NOT relaxed by ``TESTING``.
        """
        if self.DEV_AUTH_BYPASS and self.ENVIRONMENT not in BYPASS_ALLOWED_ENVIRONMENTS:
            raise ValueError(
                f"DEV_AUTH_BYPASS=true is not permitted when ENVIRONMENT={self.ENVIRONMENT!r}. "
                "The dev-admin bypass may only be enabled in development or test."
            )
        return self

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str, info: ValidationInfo) -> str:
        environment = (info.data or {}).get("ENVIRONMENT", "development")
        is_testing = os.getenv("TESTING", "").lower() in ("1", "true", "yes")
        if not is_testing and environment == "production":
            weak_defaults = {
                "default-secret-key-for-testing", "secret", "changeme", "password", "supersecret",
            }
            if v in weak_defaults or len(v) < _MIN_SECRET_KEY_LENGTH:
                raise ValueError(
                    f"SECRET_KEY must be at least {_MIN_SECRET_KEY_LENGTH} characters "
                    "and must not be a well-known default value in production. "
                    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
        return v
```

Notice the asymmetry: the bypass guard is a `model_validator` (it needs two fields) and is
_not_ relaxed by `TESTING`, because there's no legitimate reason to construct a
production-with-bypass settings object even in a test. The secret-key guard is a
`field_validator` that reads `ENVIRONMENT` from `info.data` — which works only because
`ENVIRONMENT` is declared _above_ `SECRET_KEY` in the class; Pydantic validates fields in
declaration order. The same pattern guards `FIRST_SUPERUSER_PASSWORD`, `AUDIT_HMAC_KEY` (the
key that signs compliance audit records) and `SSO_STATE_SECRET`.

The connection strings are _assembled_ from parts by `mode="before"` validators so an
operator can set either `DATABASE_URI` or the five `POSTGRES_*` pieces:

```python
# 📁 backend/app/core/config.py
    @field_validator("SQLALCHEMY_DATABASE_URI", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: Optional[str], info: ValidationInfo) -> Any:
        """Assemble database connection string if not provided directly."""
        if isinstance(v, str):
            return v
        # If DATABASE_URI is set, use that
        database_uri = info.data.get("DATABASE_URI")
        if database_uri:
            return database_uri
        # Otherwise build from components
        return PostgresDsn.build(
            scheme="postgresql",
            username=info.data.get("POSTGRES_USER"),
            password=info.data.get("POSTGRES_PASSWORD"),
            host=info.data.get("POSTGRES_SERVER"),
            port=int(info.data.get("POSTGRES_PORT", 5432)),
            path=info.data.get('POSTGRES_DB', ''),
        )
```

And the selection at the bottom of the module is what every other file imports as
`settings`:

```python
# 📁 backend/app/core/config.py
_resolved_environment = resolve_environment_from_process_env()

# Mirror the legacy spelling back into APP_ENV for modules that still read it
# directly (schema selection, rate-limiter enablement, bandit scheduler ...).
os.environ.setdefault(
    "APP_ENV", _CANONICAL_TO_APP_ENV.get(_resolved_environment, _resolved_environment)
)

if _resolved_environment in ("production", "staging"):
    settings = ProdSettings(ENVIRONMENT=_resolved_environment)
elif _resolved_environment == "test":
    settings = TestSettings(ENVIRONMENT=_resolved_environment)
else:
    settings = DevSettings(ENVIRONMENT=_resolved_environment)
```

#### The rate-limit table lives next door

One piece of configuration is in the middleware module rather than `Settings`, because it's a
table of paths rather than a set of scalars:

```python
# 📁 backend/app/middleware/rate_limiter.py
# (max_requests, window_seconds)
RATE_LIMIT_CONFIG: Dict[str, Tuple[int, int]] = {
    # Authentication endpoints — strict limits to slow brute-force attempts
    "/api/v1/auth/token": (10, 60),
    "/api/v1/auth/login": (10, 60),
    "/api/v1/auth/signup": (5, 60),
    "/api/v1/auth/forgot-password": (5, 60),
    "/api/v1/auth/reset-password": (5, 60),
}

# Default rate limit for all other endpoints
DEFAULT_RATE_LIMIT: Tuple[int, int] = (300, 60)  # 300 req/min

SDK_PATH_PREFIXES: Tuple[str, ...] = (
    "/api/v1/tracking/",
    "/api/v1/feature-flags/evaluate/",
    "/api/v1/feature-flags/user/",
)
DEFAULT_SDK_RATE_LIMIT_PER_MINUTE = 6000


def resolve_rate_limit(path: str, sdk_limit_per_minute: int = DEFAULT_SDK_RATE_LIMIT_PER_MINUTE) -> Tuple[int, int]:
    """
    Return ``(max_requests, window_seconds)`` for a request path.

    Exact entries in ``RATE_LIMIT_CONFIG`` win, then SDK path prefixes, then
    ``DEFAULT_RATE_LIMIT``.
    """
    exact = RATE_LIMIT_CONFIG.get(path)
    if exact is not None:
        return exact
    if any(path.startswith(prefix) for prefix in SDK_PATH_PREFIXES):
        return (int(sdk_limit_per_minute), 60)
    return DEFAULT_RATE_LIMIT
```

The SDK tier exists because of ShopLab: a single server-side SDK, or an office behind one NAT,
legitimately sends thousands of assignments a minute from one IP, and `/tracking/batch` was
falling under the 300/min default. `SDK_RATE_LIMIT_PER_MINUTE` in `Settings` feeds
`sdk_limit_per_minute` — a scalar in `Settings`, a table in the module, and a pure function
joining them that the tests can call without a server.

> **Did You Know?** `extra="ignore"` in `model_config` means an unknown environment variable
> is silently dropped rather than rejected. The comment says "to catch typos", which is
> backwards — `extra="forbid"` would catch typos. The team chose `ignore` because the process
> environment in a container is full of unrelated variables; the cost is that
> `SAFTEY_CHECK_INTERVAL_MINUTES=1` does nothing and tells you nothing. Spell settings
> carefully, and check `settings.<NAME>` in a shell when a value seems not to take.

### Build It

Prove the guards refuse:

```bash
# production + weak secret: the process should not come up
ENVIRONMENT=production SECRET_KEY=changeme python -c "import backend.app.core.config" 2>&1 | tail -3

# production + dev bypass: refused even with TESTING set
ENVIRONMENT=production TESTING=true DEV_AUTH_BYPASS=true SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))') \
  python -c "import backend.app.core.config" 2>&1 | tail -2

# legacy spelling: accepted, with a warning
APP_ENV=prod SECRET_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))') FIRST_SUPERUSER_PASSWORD=Str0ngPassw0rd \
  AUDIT_HMAC_KEY=$(python -c 'import secrets;print(secrets.token_hex(32))') SSO_STATE_SECRET=$(python -c 'import secrets;print(secrets.token_hex(32))') \
  python -W always -c "from backend.app.core.config import settings; print(settings.ENVIRONMENT, settings.is_production)"
```

Then evaluate the rate-limit table as a function, the way its tests do:

```bash
python -c "
from backend.app.middleware.rate_limiter import resolve_rate_limit as r
for p in ['/api/v1/auth/login','/api/v1/tracking/batch','/api/v1/feature-flags/evaluate/x','/api/v1/experiments/']: print(p, r(p))"
```

### Key takeaways

- `ENVIRONMENT` is canonical; `APP_ENV`/`dev`/`prod` are accepted with warnings and mirrored
  back for legacy readers.
- One `Settings` base, three subclasses with different defaults, selected at import.
- Validators fail closed in production: weak `SECRET_KEY`, weak first-admin password, dev
  `AUDIT_HMAC_KEY`/`SSO_STATE_SECRET`, and the bypass outside dev/test.
- `CORS_ORIGINS` is `NoDecode` so a comma-separated string survives; `BACKEND_CORS_ORIGINS`
  wants JSON.
- Rate limits: strict on auth, 300/min default, `SDK_RATE_LIMIT_PER_MINUTE` (6000) on
  tracking and evaluate paths — resolved by a pure function.

### Q&A

**Q. Why keep both `DATABASE_URI` and `SQLALCHEMY_DATABASE_URI`?**
History and layering: `DATABASE_URI` is what operators and CI set; `SQLALCHEMY_DATABASE_URI`
is what `db/session.py` reads, and it falls back to `DATABASE_URI`, then to the components.
`db/session.py` additionally honours a raw `DATABASE_URI` environment variable first, which is
how the test harness points the engine at `experimentation_test_<pid>`.

**Q. Why does `TESTING` relax the secret validators but not the bypass validator?**
Tests need to construct `ProdSettings()` to test production _behaviour_ (HSTS headers, JSON
logs) without provisioning real secrets. There's no production behaviour that depends on the
bypass being on, so there's nothing to test by allowing it — and allowing it would be a
one-variable step from a catastrophic misconfiguration.

**Q. Where do the scheduler cadences live and why are they minutes?**
`SAFETY_CHECK_INTERVAL_MINUTES`, `ROLLOUT_CHECK_INTERVAL_MINUTES`,
`BANDIT_UPDATE_INTERVAL_MINUTES` in `Settings`; the experiment and metrics schedulers are
constructed with `interval_minutes=15`. Minutes because the work is batch-shaped and the
locks are advisory (Day 17); the demos set them to 1 so a rollout story plays out in a
demo's timescale.

---

## Day 10 — Authentication: local JWTs, Cognito, API keys, and the dev bypass

### What & why

Three kinds of caller, three credentials:

| Caller               | Credential                          | Resolved by                | Identity produced           |
| -------------------- | ----------------------------------- | -------------------------- | --------------------------- |
| Dashboard user       | `Authorization: Bearer <local JWT>` | `deps.get_current_user`    | a `User` row (by id)        |
| Dashboard user (EE)  | `Authorization: Bearer <Cognito>`   | `deps.get_current_user`    | a `User` row (by username)  |
| SDK / integration    | `X-API-Key: eptk_…`                 | `deps.get_api_key`         | the key's owning `User`     |

Plus one non-credential for development: the **dev bypass**, which makes
`get_current_user` return a synthetic `dev-admin` superuser without looking at any header —
only when `settings.dev_auth_bypass_active` (bypass on _and_ environment is dev/test).

#### Why two providers, and why the local one is the default

The platform was built on AWS Cognito: users sign up in a user pool, groups map to roles, the
API validates tokens remotely. That's the right shape for an enterprise deployment and the
wrong shape for a Community Edition that has to run with `docker compose up` and no AWS
account. So the P0 open-core work added `AUTH_PROVIDER=local`: email + bcrypt password
against `users.hashed_password`, issuing HS256 JWTs signed with `SECRET_KEY`. The Cognito
path is unchanged; the local path is the default; `get_current_user` dispatches on the
setting.

#### Why API keys are hashed, and why they're SHA-256 rather than bcrypt

An API key is looked up on _every_ SDK request. bcrypt is deliberately slow (12 rounds ≈
250 ms), which is right for a password checked once per login and ruinous at 6,000 requests a
minute. SHA-256 is fast and one-way, which is all a random 128-bit key needs (there's nothing
to brute-force). So keys are stored as their SHA-256 hex digest, the plaintext is shown once
at creation, and lookup is `APIKey.key == sha256(header)`.

### How Experimently does it

The three token functions in `core/security.py`:

```python
# 📁 backend/app/core/security.py
LOCAL_TOKEN_ISSUER = "experimently-local"
LOCAL_TOKEN_ALGORITHM = "HS256"

# OAuth2 password bearer scheme.  ``auto_error=False`` so that the absence of
# a token reaches ``deps.get_current_user``, which decides between the
# dev-admin bypass and a 401.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/token", auto_error=False)


def create_local_access_token(user: Any, *, expires_minutes: Optional[int] = None) -> str:
    ttl = expires_minutes if expires_minutes is not None else settings.LOCAL_AUTH_TOKEN_TTL_MINUTES
    now = datetime.now(timezone.utc)
    claims: Dict[str, Any] = {
        "sub": str(user.id),
        "email": user.email,
        "role": role_name(getattr(user, "role", None)),
        "iss": LOCAL_TOKEN_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, settings.SECRET_KEY, algorithm=LOCAL_TOKEN_ALGORITHM)


def decode_local_token(token: str) -> Dict[str, Any]:
    if not token or not isinstance(token, str):
        raise InvalidTokenError("Token missing")
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[LOCAL_TOKEN_ALGORITHM],
            issuer=LOCAL_TOKEN_ISSUER,
            options={"require": ["sub", "exp", "iat", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()
```

Three defensive choices are packed in there. `algorithms=[...]` pins HS256 so a token with
`alg: none` is rejected. `issuer=` pins `experimently-local` so a JWT from some other
service that happens to share the secret can't be replayed. `options={"require": [...]}`
makes `sub`/`exp`/`iat`/`iss` mandatory so a truncated token can't authenticate as nobody.

The dispatcher:

```python
# 📁 backend/app/api/deps.py
def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """
    Resolution order:

    1. Dev-admin bypass (``DEV_AUTH_BYPASS=true`` **and** ``ENVIRONMENT`` in
       development/test): returns the synthetic ``dev-admin`` superuser.
    2. No token: 401.
    3. ``AUTH_PROVIDER=local``: decode the HS256 JWT issued by
       ``/api/v1/auth/login`` and load the user by id (401 on any failure,
       400 if the account is inactive).
    4. ``AUTH_PROVIDER=cognito``: validate the token with Cognito and sync the
       user's role from its groups (unchanged legacy path).
    """
    if dev_auth_bypass_active():
        try:
            return _get_or_create_dev_user(db)
        except Exception as dev_err:
            raise HTTPException(status_code=500, detail=f"Dev auth error: {dev_err}")

    if not token:
        raise _credentials_exception("Not authenticated")

    if settings.AUTH_PROVIDER == "local":
        return _authenticate_local_token(token, db)

    try:
        # Get user details and groups from Cognito
        user_data = auth_service.get_user_with_groups(token)
        username = user_data.get("username")
        user = db.query(User).filter(User.username == username).first()
        cognito_groups = user_data.get("groups", [])
        role = map_cognito_groups_to_role(cognito_groups)
        is_superuser = should_be_superuser(cognito_groups)
        ...
        if not user:
            # Create user in database if not exists
            ...
        elif settings.SYNC_ROLES_ON_LOGIN:
            ...
        return user
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials", ...)
```

The Cognito branch does something the local branch doesn't: **just-in-time provisioning**.
A valid Cognito token for a user the database has never seen creates the `User` row, with a
role derived from Cognito groups (`Admins → admin`, `Developers → developer`, …; highest
privilege wins; membership in `COGNITO_ADMIN_GROUPS` also sets `is_superuser`). The local
branch has no such step — a local user exists because the bootstrap or an admin created them.

Login itself, for the local provider, adds a lockout:

```python
# 📁 backend/app/api/v1/endpoints/auth.py
def _issue_local_login(db: Session, email: str, password: str) -> LoginResponse:
    """Authenticate with the local provider and build the token response."""
    try:
        user = local_auth_service.authenticate(db, email, password)
    except AccountLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=("Too many failed login attempts; account temporarily locked. "
                    f"Retry in {exc.retry_after_seconds} seconds."),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_local_access_token(user)
    return LoginResponse(access_token=token, token_type="bearer",
                         expires_in=settings.LOCAL_AUTH_TOKEN_TTL_MINUTES * 60, user=user_to_me(user))
```

The same 401 message is used for an unknown address, a wrong password and an inactive
account, so the endpoint can't be used to enumerate users; ten failures in fifteen minutes
answer 423 with `Retry-After`. The tracker is in-process memory (per worker), which is one
more reason the platform runs one worker per container.

And the API-key path:

```python
# 📁 backend/app/api/deps.py
def get_api_key(db: Session = Depends(get_db), api_key_header: str = Depends(API_KEY_HEADER)) -> Optional[User]:
    if not api_key_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key missing",
                            headers={"WWW-Authenticate": "APIKey"})

    # Look up by hashed key only. API keys must never be stored as plaintext.
    api_key_hash = hash_api_key(api_key_header)
    api_key = db.query(APIKey).filter(APIKey.key == api_key_hash).first()
    if not api_key or not api_key.is_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key", ...)

    # Get associated user
    user = db.query(User).filter(User.id == api_key.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key", ...)

    return user
```

`is_valid` is `is_active and not is_expired`. Keys are minted by `APIKey.create_for_user`
(`eptk_` + 32 hex chars from `secrets.token_hex(16)`), which returns `(model, plaintext)` —
and `POST /api-keys` is the only place the plaintext is ever shown. The compose demo bakes
fixed keys into the demo images (`eptk_demo_shoplab_…`) so the storefront works without a
manual step; in every other case the key comes from the dashboard's Admin → API keys page.

> **Watch Out!** `core/security.py` also has a `decode_token()` that returns _constant stub
> claims_ and raises `RuntimeError` unless `TESTING` is set. It exists only so legacy tests can
> monkeypatch it. Never call it for real validation; the local provider uses
> `decode_local_token`, Cognito uses `CognitoAuthService`.

### Build It

Mint a key and use it; then break a token three ways:

```bash
# login → token → create an API key (plaintext shown once)
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@demo.com","password":"Demo1234!"}' | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s -X POST $API/api-keys -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"week2"}' | python -m json.tool

# inspect the JWT's claims without verifying (never do this in code — it's to *see* iss/exp/sub)
python -c "import jwt,sys; print(jwt.decode('$TOKEN', options={'verify_signature': False}))"

# tamper: change one character of the signature → 401
curl -s -o /dev/null -w "%{http_code}\n" $API/auth/me -H "Authorization: Bearer ${TOKEN%?}x"
# wrong scheme → 401
curl -s -o /dev/null -w "%{http_code}\n" $API/auth/me -H "Authorization: Token $TOKEN"
# bad api key → 401
curl -s -o /dev/null -w "%{http_code}\n" "$API/feature-flags/evaluate/anything?user_id=u" -H "X-API-Key: eptk_nope"
```

Then find the SHA-256 of your new key in the database — and note that the plaintext is
nowhere:

```bash
docker compose exec postgres psql -U postgres -d experimentation -c "select name, left(key, 12) as key_hash_prefix, is_active from experimentation.api_keys order by created_at desc limit 3"
```

### Key takeaways

- Three credentials: local JWT (HS256, `iss=experimently-local`, 12 h), Cognito token
  (validated remotely, JIT-provisioned, roles synced from groups), API key (SHA-256 hashed,
  `eptk_` prefix, shown once).
- `get_current_user` dispatches on `AUTH_PROVIDER` after checking the dev bypass; `get_api_key`
  is a separate dependency for SDK routes.
- Passwords are bcrypt (12 rounds); keys are SHA-256 — slow where brute force matters, fast
  where it doesn't.
- Login lockout is 10 failures / 15 min → 423 + `Retry-After`; the message never reveals
  which of address/password/status was wrong.
- The dev bypass exists only when `DEV_AUTH_BYPASS=true` _and_ `ENVIRONMENT ∈ {development,
  test}`; production refuses to start with it.

### Q&A

**Q. Why does `oauth2_scheme` have `auto_error=False`?**
So a missing `Authorization` header reaches `get_current_user` instead of being rejected by
FastAPI's security machinery. `get_current_user` needs to see "no token" to decide between the
dev bypass (return `dev-admin`) and a 401.

**Q. Can an API key call `/experiments/`?**
No. Those endpoints depend on `get_current_active_user` (bearer). SDK routes depend on
`get_api_key`. A key is scoped to the tracking, evaluate and error-reporting paths by
_which dependency the endpoint declares_, not by a scope string — the `scopes` column exists
but isn't enforced yet.

**Q. How do I log in on the dashboard with Cognito locally?**
You mostly don't; the local provider is the development default. The Cognito path is tested
with `moto`'s `mock_cognitoidp` in `backend/tests/integration/auth/` (its own conftest selects
`AUTH_PROVIDER=cognito`), and the `Cognito Integration Tests` workflow runs them in CI.

**Q. The token is stored in `localStorage` on the dashboard. Isn't that an XSS risk?**
It's a known trade. The dashboard is an internal tool behind login, the API sets a strict
`Content-Security-Policy: default-src 'none'`, and the token is short-lived. `AuthContext`
never trusts the stored token blindly — it re-derives the user from `/auth/me` on every
mount, so a revoked token can't resurrect a session.

---

## Day 11 — Authorization: RBAC, ownership, and the permission dependencies

### What & why

Authentication answered _who_. Authorization answers _may they_. Experimently's model has
two axes and a trump card:

1. **Role × resource × action.** Four roles (`ADMIN`, `DEVELOPER`, `ANALYST`, `VIEWER`), seven
   resource types (experiment, feature flag, user, role, permission, report, and — since the
   open-core work — `API_KEY`, which governs acting on _other_ users' keys), five actions, one
   static table.
2. **Ownership.** A resource has an `owner_id`; non-admins can modify what they own.
3. **Superuser** trumps everything — `check_permission` returns `True` before looking at the
   table.

The design choice is that the table is **code, not data**. Custom roles and direct grants
exist (the P2-A RBAC work under `/rbac`) for enterprise customers, but the base matrix is a
Python dict you can read in ten seconds and that can't be misconfigured by a migration.

#### Why "READ before UPDATE"

The dependencies check READ first, then UPDATE/DELETE. It's not just ordering pedantry: a
user who can't even read a resource should get "you can't view feature flags", not "you can't
update feature flags" — the second message confirms the resource exists and leaks which
operation was attempted. It's also the order that makes the ownership rule composable: "may
read, and (may update _or_ owns it)".

### How Experimently does it

The matrix:

```python
# 📁 backend/app/core/permissions.py
ROLE_PERMISSIONS: Dict[UserRole, Dict[ResourceType, List[Action]]] = {
    UserRole.ADMIN: {
        ResourceType.EXPERIMENT: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
        ResourceType.FEATURE_FLAG: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
        ResourceType.USER: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
        ...
    },
    UserRole.DEVELOPER: {
        ResourceType.EXPERIMENT: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
        ResourceType.FEATURE_FLAG: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
        ResourceType.USER: [Action.READ, Action.LIST],
        ...
        ResourceType.REPORT: [Action.READ, Action.LIST],
    },
    UserRole.ANALYST: {
        ResourceType.EXPERIMENT: [Action.READ, Action.LIST],
        ResourceType.FEATURE_FLAG: [Action.READ, Action.LIST],
        ...
        ResourceType.REPORT: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
    },
    UserRole.VIEWER: {
        ResourceType.EXPERIMENT: [Action.READ, Action.LIST],
        ResourceType.FEATURE_FLAG: [Action.READ, Action.LIST],
        ...
    },
}


def check_permission(user: Any, resource: ResourceType, action: Action) -> bool:
    # Superusers always have all permissions
    if hasattr(user, 'is_superuser') and user.is_superuser:
        return True

    # Get user's role
    try:
        role = getattr(user, 'role', UserRole.VIEWER)
    except (AttributeError, TypeError):
        role = UserRole.VIEWER

    # Check role permissions
    return has_permission(role, resource, action)


def check_ownership(user: Any, resource_obj: Any) -> bool:
    if hasattr(resource_obj, 'owner_id') and hasattr(user, 'id'):
        return str(resource_obj.owner_id) == str(user.id)
    return False
```

The interesting cell is `ANALYST × REPORT`: analysts can't touch experiments but have full
control of reports — the role is "reads everything, writes analyses". The `VIEWER` default in
`check_permission` is deliberate: an object with no `role` attribute (a mock, a malformed
row) gets the least privilege, not an exception.

The dependencies compose the two checks. The experiment version is a plain function called
from the endpoint; the feature-flag version is `async` and used with `Depends`:

```python
# 📁 backend/app/api/deps.py
def get_experiment_access(experiment, current_user: User = Depends(get_current_active_user)):
    # Superusers always have access
    if current_user.is_superuser:
        return experiment

    # Check if user has permission to read experiments
    if not check_permission(current_user, ResourceType.EXPERIMENT, Action.READ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=get_permission_error_message(ResourceType.EXPERIMENT, Action.READ))

    # Check ownership for non-admin users for modification actions
    if not check_permission(current_user, ResourceType.EXPERIMENT, Action.UPDATE) and not check_ownership(current_user, experiment):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You don't have permission to access this experiment")

    return experiment


async def get_feature_flag_access(
    feature_flag: FeatureFlag = Depends(get_feature_flag_by_key),
    current_user: User = Depends(get_current_user),
) -> FeatureFlag:
    """Check if user has access to the feature flag."""
    if current_user.is_superuser:
        return feature_flag
    if not check_permission(current_user, ResourceType.FEATURE_FLAG, Action.READ):
        raise HTTPException(...)
    if not check_permission(current_user, ResourceType.FEATURE_FLAG, Action.UPDATE) and not check_ownership(current_user, feature_flag):
        raise HTTPException(...)
    return feature_flag


async def can_update_feature_flag(
    feature_flag: FeatureFlag = Depends(get_feature_flag_access),
    current_user: User = Depends(get_current_user),
) -> bool:
    """Check if user can update a feature flag."""
    if not check_permission(current_user, ResourceType.FEATURE_FLAG, Action.UPDATE):
        raise HTTPException(...)
    return True
```

Read the second condition in `get_experiment_access` slowly: it fires only when the user
_can't_ update experiments by role **and** doesn't own this one. A developer passes on role; a
viewer passes only on ownership — and a viewer can't create experiments, so in practice a
viewer sees a 403 for any experiment-mutating call. The `can_update_feature_flag` dependency
chains on `get_feature_flag_access`, so READ (and the ownership rule) is checked before
UPDATE — the "READ before UPDATE" order is enforced by the dependency graph, not by
discipline.

#### The delete-experiment exception

`CLAUDE.md` calls it out, and it's a good lesson in dependency design. The natural thing would
be a `can_delete_experiment` dependency. But the experiment-fetching dependency it would chain
on (`get_experiment_by_key`) defaults to requiring `status == ACTIVE`, while delete requires
`DRAFT`. Rather than thread a `required_status` through the chain, the endpoint does its
checks inline:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
async def delete_experiment(
    experiment_id: UUID = Path(..., description="The ID of the experiment to delete"),
    experiment_key: str = Query(..., description="Key or ID of the experiment to delete (must match experiment_id)"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    cache_control: Dict[str, Any] = Depends(deps.get_cache_control),
) -> None:
```

Path id **and** a query `experiment_key` that must match — a deliberate two-factor "are you
sure" for a destructive operation — then permission, ownership and `DRAFT` checks in the body.

> **By the Way.** Superuser and `ADMIN` role are different bits. Cognito JIT provisioning sets
> both for members of `COGNITO_ADMIN_GROUPS`; the bootstrap's first user gets both; the local
> `users` table can have an `ADMIN` who isn't a superuser. `check_permission` short-circuits
> on `is_superuser`; `get_current_superuser` (used by `/admin/*` and safety settings) checks
> the bit only. When a "why can this admin not do X" ticket arrives, check both.

### Build It

Prove the matrix from a shell, then from the API with a viewer:

```bash
python -c "
from backend.app.core.permissions import has_permission, ResourceType as R, Action as A
from backend.app.models.user import UserRole as U
print('analyst report create', has_permission(U.ANALYST, R.REPORT, A.CREATE))
print('analyst experiment create', has_permission(U.ANALYST, R.EXPERIMENT, A.CREATE))
print('developer user delete', has_permission(U.DEVELOPER, R.USER, A.DELETE))"
```

Create a viewer on the Admin → Users page (or via `POST /users` as admin), log in as them, and
call `POST /experiments/` — expect 403 with the message from `get_permission_error_message`.
Then read the tests that pin the matrix: `backend/tests/unit/core/test_permissions*.py` and
`backend/tests/unit/api/test_feature_flag_permissions*.py` (the async ones use
`@pytest.mark.asyncio` and `await`).

### Key takeaways

- Four roles × six resources × five actions in one dict; superuser bypasses it; missing role
  → `VIEWER`.
- Ownership lets non-admins modify what they own; the composed rule is "may READ and (may
  UPDATE or owns)".
- READ is checked before UPDATE/DELETE, by the dependency graph for flags and by call order
  for experiments.
- Experiment deps are sync functions; feature-flag and report deps are `async` and
  `await`ed/`Depends`-ed — don't mix.
- `delete_experiment` checks inline (status conflict with the shared dependency) and demands
  id + key.

### Q&A

**Q. Where do custom roles and direct grants fit?**
`/api/v1/rbac` (P2-A) adds `custom_roles`, direct permission grants and an "effective
permissions" resolver on top of the static matrix. The matrix is the floor; custom roles
extend it. Most deployments never touch it.

**Q. Why is ownership compared as strings?**
`owner_id` is a UUID column and `user.id` is a UUID, but tests and JIT paths sometimes hand
over strings. `str(a) == str(b)` is robust to both and costs nothing.

**Q. An analyst can `READ` experiments but `list_experiments` says "regular users only see
their own". Which is it?**
Both: the permission says they may call the endpoint; the service's `get_experiments_by_owner`
scopes the _list_ to their own unless they're a superuser. Reading a specific experiment by
id goes through `get_experiment_access`, which allows READ by role. It's a known asymmetry.

**Q. Workspaces (EP-057) have their own roles. How do they interact with this?**
Workspace membership roles (`OWNER/ADMIN/DEVELOPER/ANALYST/VIEWER`) scope access _within_ a
workspace and are checked by the workspace service; experiments and flags carry a nullable
`workspace_id`. The global matrix still applies first.

---

## Day 12 — The SQLAlchemy models and the PostgreSQL schema

### What & why

Day 3 introduced the five core tables. Today is about the _mechanics_ that make every one of
the ~35 model classes behave the same way: the declarative base with a schema, the mixin that
adds ids and timestamps, foreign keys that must name the schema explicitly, `JSONB` for the
flexible parts, relationships with cascades, and the index/constraint tuple every model
returns from `__table_args__`.

#### Why an explicit schema, everywhere

PostgreSQL schemas let the app and the tests share one server without sharing tables. The
cost is that every reference must name the schema — SQLAlchemy doesn't propagate
`metadata.schema` into `ForeignKey("users.id")` strings — so the codebase writes
`ForeignKey(f"{get_schema_name()}.users.id")` and returns `{"schema": schema_name}` from
`__table_args__` on every model. It's verbose, and it's the price of a clean test database.

#### Why JSONB for `targeting_rules`, `configuration`, `metrics`

Some columns are genuinely relational (an assignment's `variant_id`) and some are documents
whose shape evolves faster than migrations: a variant's `configuration` is whatever your app
needs; a flag's `targeting_rules` have arrived in three shapes over the product's life (Day
16). `JSONB` stores them indexed and queryable without a migration per shape change; the
Pydantic layer validates the shape at the API boundary instead. The rule of thumb in this
codebase: **relational for anything you join or aggregate on, JSONB for configuration.**

### How Experimently does it

The base and the mixin:

```python
# 📁 backend/app/models/base.py
# Create metadata without schema
metadata = MetaData()

# Create base with metadata
Base = declarative_base(metadata=metadata)

# Set schema immediately
metadata.schema = get_schema_name()
for table in metadata.tables.values():
    table.schema = get_schema_name()


class BaseModel:
    """Abstract base model for common columns and methods. This is a mixin, not a table itself."""

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        """Convert model instance to dictionary."""
        result = {}
        for column in self.__table__.columns:
            value = getattr(self, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, uuid.UUID):
                value = str(value)
            result[column.name] = value
        return result
```

Every model is `class X(Base, BaseModel)`. The mixin comes second so `Base`'s metaclass wins,
and the columns are copied onto each subclass (SQLAlchemy handles plain-class mixins with
`Column` attributes by copying).

A model with relationships in both directions, a one-to-one, a check constraint and a
composite index — the feature flag is the best single example:

```python
# 📁 backend/app/models/feature_flag.py
class FeatureFlag(Base, BaseModel):
    """Feature flag model for feature toggles."""

    __tablename__ = "feature_flags"

    key = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    status = Column(Enum(FeatureFlagStatus), default=FeatureFlagStatus.INACTIVE, nullable=False, index=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.users.id", ondelete="SET NULL"))
    targeting_rules = Column(JSONB)  # Rules for flag enablement
    rollout_percentage = Column(Integer, default=0, nullable=False)  # Gradual rollout
    variants = Column(JSONB)  # For multivariate flags
    tags = Column(JSONB)  # For categorization
    workspace_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.workspaces.id", ondelete="SET NULL"), nullable=True, index=True)

    # Relationships
    owner = relationship("User", back_populates="feature_flags")
    overrides = relationship("FeatureFlagOverride", back_populates="feature_flag", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="feature_flag", cascade="all, delete-orphan")
    rollout_schedules = relationship("RolloutSchedule", back_populates="feature_flag", cascade="all, delete-orphan")
    raw_metrics = relationship("RawMetric", back_populates="feature_flag", cascade="all, delete-orphan")
    error_logs = relationship("ErrorLog", back_populates="feature_flag", cascade="all, delete-orphan")
    safety_config = relationship(
        "FeatureFlagSafetyConfig",
        back_populates="feature_flag",
        cascade="all, delete-orphan",
        uselist=False  # One-to-one relationship
    )

    @declared_attr
    def __table_args__(cls):
        schema_name = get_schema_name()
        return (
            # Composite index for owner + status
            Index(f"{schema_name}_feature_flag_owner_status", "owner_id", "status"),
            # Check that rollout percentage is between 0 and 100
            CheckConstraint("rollout_percentage >= 0 AND rollout_percentage <= 100", name="check_rollout_percentage"),
            {"schema": schema_name},
        )
```

Points to notice:

- **`relationship(..., back_populates=...)` on both sides.** Every relationship is declared on
  both models with matching `back_populates`, never `backref`. It's more typing and it means
  each model file is self-describing. `CLAUDE.md`'s "watch for duplicate SQLAlchemy
  relationship definitions" refers to the failure mode where a relationship is declared
  twice on one side (the `Variant.assignments` relationship in `experiment.py` _is_ declared
  twice — harmlessly, because both declarations are identical, but it's the wart the warning
  is about).
- **`ondelete` differs by intent.** `CASCADE` for children that mean nothing without the
  parent (variants, assignments, overrides). `SET NULL` for things that must survive — an
  event outlives its flag, a flag outlives its owner.
- **Index names are schema-prefixed** (`f"{schema_name}_feature_flag_owner_status"`) because
  index names are global within a PostgreSQL schema and the app and test schemas would
  otherwise collide when both live in one database during migration work.
- **Enums.** `Enum(FeatureFlagStatus)` creates a PostgreSQL enum type named after the class
  (`featureflagstatus`). Note `FeatureFlagStatus` values are upper-case (`"ACTIVE"`) while
  `ExperimentStatus` values are lower-case (`"active"`) — a historical mismatch that a
  migration (`fce74a1b62d3_update_enum_type.py`) papered over by adding lower-case values to
  the experiment enums. The flag service compares against both the member and its value for
  exactly this reason.

The session factory and the engine are module singletons:

```python
# 📁 backend/app/db/session.py
engine = create_engine(
    database_uri,
    pool_pre_ping=True,  # Test connections before using them
    pool_size=20,  # Increased pool size
    max_overflow=20,  # Increased overflow
    pool_recycle=300,  # Recycle connections after 5 minutes
    pool_timeout=30,  # Wait up to 30 seconds for a connection
    connect_args={
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    }
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

`pool_pre_ping` and the keepalives are there for Aurora: a connection that sat idle through a
failover is detected and replaced instead of raising on first use. `autoflush=False` means
queries don't implicitly flush pending inserts — services call `db.flush()` explicitly when
they need an id (as `create_experiment` does to get `experiment.id` before adding variants).

> **Did You Know?** The `users` model is the one place the mixin isn't enough: `User.__init__`
> pops `full_name` and `preferences` from kwargs before calling the ORM constructor, because
> older callers pass `full_name` while the table has `first_name`/`last_name`. And
> `hashed_password` is nullable for Cognito-provisioned users — but the test fixtures always
> set it, because a later `NOT NULL` assumption in local auth would otherwise fail. That's
> the origin of the `CLAUDE.md` line "test users must have a `hashed_password`".

### Build It

Generate the DDL for one table without a database — the fastest way to see what a model
really means:

```bash
python - <<'PY'
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
import backend.app.models  # registers every model
from backend.app.models.feature_flag import FeatureFlag
print(CreateTable(FeatureFlag.__table__).compile(dialect=postgresql.dialect()))
PY
```

Then count relationships and confirm every one has a partner:

```bash
git grep -n "relationship(" backend/app/models | wc -l
git grep -n "backref=" backend/app/models || echo "no backref — good, back_populates everywhere"
```

### Key takeaways

- `Base` + `BaseModel` mixin: UUID `id`, `created_at`, `updated_at`, `to_dict()` on every
  table.
- The schema is explicit in every `ForeignKey` string and every `__table_args__`; index names
  are schema-prefixed.
- Relationships use `back_populates` on both sides; `ondelete` is `CASCADE` for children,
  `SET NULL` for survivors.
- JSONB for configuration-shaped data; relational for anything joined or aggregated.
- The engine is tuned for Aurora (`pool_pre_ping`, keepalives, recycle); sessions are
  `autoflush=False`, so `flush()` is explicit.

### Q&A

**Q. Why `datetime.utcnow` (naive) rather than `datetime.now(timezone.utc)`?**
Legacy. The columns are `DateTime` without `timezone=True`, so PostgreSQL stores naive
timestamps and returns naive ones. It works as long as everyone treats them as UTC — and it
is exactly what bit the rollout scheduler, which compared a naive `stage.start_date` with an
aware `datetime.now(timezone.utc)` and raised `TypeError` on every tick. The fix normalises
at the comparison (`_as_utc`); a migration to `timestamptz` would be the real fix.

**Q. How do I add a relationship without the "not mapped" error?**
Declare it on both models with `back_populates`, use string class names, and make sure the
new model is imported in `backend/app/models/__init__.py` — mappers are configured lazily,
and a class that's never imported can't be resolved by name.

**Q. Do I ever write raw SQL?**
Rarely, and via `text()` with bound parameters. The results engine has a couple of `text()`
queries for daily aggregation (they use `CONVERSION_SQL_PREDICATE` from `event_matching`), and
the scheduler lock uses `pg_try_advisory_lock`. Never format identifiers into SQL strings;
`_ensure_schema` uses `CreateSchema` for exactly that reason.

---

## Day 13 — Alembic migrations and the bootstrap

### What & why

**Alembic** versions the database schema as a chain of Python revision scripts, each with an
`upgrade()` and a `downgrade()` and a `down_revision` pointer to its parent. `alembic upgrade
head` applies everything the database hasn't seen (tracked in an `alembic_version` table);
`alembic revision --autogenerate` diffs the models against the database and writes a script
for you to review.

Experimently's twist, introduced on Day 2, is that the chain is not replayable from an empty
database. So there are **two paths to a current schema**:

- **Existing database** → `alembic upgrade head` (incremental, the normal thing).
- **Fresh database** → `Base.metadata.create_all` from the models, then `alembic stamp head`
  so future incremental migrations apply cleanly.

`backend.app.db.bootstrap` chooses between them by checking for `alembic_version`. The
container entrypoint runs it when `RUN_MIGRATIONS=true`; CI's `db-migrate.yml` and the
production `run-migrations` job (a one-shot Fargate task) run it against real databases.

#### Why not fix the chain?

Because rewriting history for a database that already has ~25 revisions applied in
production is riskier than the cookbook pattern, and because the models are already the
source of truth for the test suite (which also uses `create_all`). The rule the team adopted:
**models are truth; migrations are the delta for databases that already exist.** Every new
migration must still be correct and reviewed — it's how production moves — but nobody should
expect the chain to build a database from nothing.

### How Experimently does it

`env.py` builds the URL from the same `POSTGRES_*` variables everything else uses and puts
the version table in the app schema:

```python
# 📁 backend/app/db/migrations/env.py
# Import your models
from backend.app.models import Base

# Set target_metadata to your SQLAlchemy models
target_metadata = Base.metadata

# Get schema from environment variable
schema = os.environ.get("POSTGRES_SCHEMA", "experimentation")
db_name = os.environ.get("POSTGRES_DB", "experimentation")

_db_user = os.environ.get("POSTGRES_USER", "postgres")
_db_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
_db_host = os.environ.get("POSTGRES_SERVER") or os.environ.get("POSTGRES_HOST") or "localhost"
_db_port = os.environ.get("POSTGRES_PORT", "5432")
postgres_url = f"postgresql://{_db_user}:{_db_password}@{_db_host}:{_db_port}/{db_name}"
config.set_main_option("sqlalchemy.url", postgres_url)
```

The bootstrap's decision:

```python
# 📁 backend/app/db/bootstrap.py
def has_alembic_version(engine: Engine, schema: str) -> bool:
    return inspect(engine).has_table("alembic_version", schema=schema)


def _point_models_at_schema(schema: str):
    """Import every model and point all tables at *schema*; return ``Base``."""
    # Importing the package registers all models on the shared metadata.
    import backend.app.models  # noqa: F401
    from backend.app.models.base import Base

    Base.metadata.schema = schema
    for table in Base.metadata.tables.values():
        table.schema = schema
    return Base


def create_from_models(engine: Engine, schema: str) -> None:
    """Create every table defined by the SQLAlchemy models inside *schema*."""
    Base = _point_models_at_schema(schema)
    Base.metadata.create_all(bind=engine)


def ensure_first_superuser(engine: Engine, schema: str) -> bool:
    """
    Create the ``FIRST_SUPERUSER`` administrator when no user exists yet.

    Returns True when a user was created.  Existing databases (any row in
    ``users``) are left untouched, so this never overrides seeded or
    real accounts; the seed scripts keep ``admin@demo.com`` / ``Demo1234!``.
    """
```

A hand-written migration in the house style — note the schema helper, the explicit revision
ids (never names), and the backwards-compatible nullable columns:

```python
# 📁 backend/app/db/migrations/versions/ep057_workspaces.py
"""EP-057: Add Multi-Tenant Team Workspaces tables

Revision ID: ep057_workspaces
Revises: ep046_llm_experiments
Create Date: 2026-03-07 00:00:00.000000

Adds nullable workspace_id foreign-key columns to:
  experiments    -- backwards-compatible; NULL = legacy/unscoped
  feature_flags  -- backwards-compatible; NULL = legacy/unscoped
"""

# Revision identifiers
revision: str = "ep057_workspaces"
down_revision: Union[str, None] = "ep046_llm_experiments"
branch_labels = None
depends_on = None


def _schema() -> str:
    """Return the current Alembic migration schema (falls back to 'public')."""
    ...
    return os.environ.get("POSTGRES_SCHEMA", schema)


def upgrade() -> None:
    schema = _schema()

    # ── 1. workspaces ─────────────────────────────────────────────────────────
    op.create_table(
        "workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False, unique=True),
        ...
```

The workflow the team follows, from `CLAUDE.md` and `backend/CLAUDE.md`, with the reasons
attached:

1. Change the model.
2. `python -m alembic -c backend/app/db/alembic.ini revision --autogenerate -m
   "description"` from the repository root (`alembic.ini` resolves `script_location`
   relative to itself, so the root is the only working directory you need — or use the
   `/migration` skill).
3. **Read the generated file.** Autogenerate is a diff, not a decision: it will happily drop
   an index it can't match by name, miss a `CheckConstraint`, or generate an enum change that
   needs `ALTER TYPE ... ADD VALUE` (which can't run inside a transaction). Check
   `down_revision` is the current head's _id_.
4. `python -m alembic -c backend/app/db/alembic.ini upgrade head` locally; run the tests
   (which use `create_all`, so they test the _model_; the migration is tested by applying it).
5. On "multiple heads" (two branches merged), `alembic merge -m "merge heads" rev1 rev2`.

> **Watch Out!** PostgreSQL enums are the sharpest edge. `ALTER TYPE x ADD VALUE` cannot run in
> a transaction block on older versions and can't be undone; renaming a member requires a
> new type and a column cast. The repo has a migration whose whole job was adding lower-case
> values to enums that had been created upper-case. When you add an `Enum(...)` column,
> decide the casing once, match the Python enum's `.value`, and prefer a `String` column with
> a `CheckConstraint` if the set is likely to change.

### Build It

Do a dry run of the whole loop on a scratch database:

```bash
# from the repository root
export POSTGRES_DB=scratch POSTGRES_SCHEMA=experimentation
docker compose exec postgres createdb -U postgres scratch
ALEMBIC="python -m alembic -c backend/app/db/alembic.ini"
python -m backend.app.db.bootstrap        # fresh → create_all + stamp
$ALEMBIC current                          # shows the head revision
$ALEMBIC history | head -5

# now the incremental path: add a throwaway column to a model, autogenerate, read, upgrade
$ALEMBIC revision --autogenerate -m "scratch add note"
$EDITOR backend/app/db/migrations/versions/*scratch_add_note.py   # read it; check down_revision
$ALEMBIC upgrade head
git checkout -- backend/app/models && rm backend/app/db/migrations/versions/*scratch_add_note.py   # discard
```

### Key takeaways

- Alembic versions schema changes as a chain; `env.py` uses the shared `POSTGRES_*`
  variables and a schema-qualified version table.
- Fresh database → bootstrap (`create_all` + `stamp head` + first admin); existing → `upgrade
  head`. The entrypoint runs the bootstrap when `RUN_MIGRATIONS=true`.
- Models are truth; migrations are the delta. Every generated migration is read before it's
  applied; `down_revision` is an id.
- Enums are the sharp edge; nullable columns are the backwards-compatible default.

### Q&A

**Q. If the tests use `create_all`, what tests the migrations?**
Applying them. `db-migrate.yml` runs the bootstrap against a real database; the production
deploy runs a one-shot migration task before switching traffic. A migration that diverges
from the model shows up as a diff the next time someone autogenerates — which is one reason
to autogenerate on a database that's been upgraded, not bootstrapped.

**Q. Can I downgrade?**
Each script has a `downgrade()`, and `alembic downgrade -1` works for the most recent ones.
Older revisions in the chain were not written with downgrade in mind. In practice the rollback
workflow (Week 4) rolls back the _container_, and schema changes are made backwards-compatible
(nullable columns, additive enums) so the old code runs against the new schema.

**Q. Where should the migration for a new table go — autogenerate or hand-write?**
Autogenerate to get the columns right, then hand-edit to the house style: docstring with the
epic id, `_schema()` helper, explicit index names, and `IF NOT EXISTS` where a rerun should be
safe.

---

## Day 14 — Capstone: add a field end-to-end

### What & why

The Week 2 capstone adds one field to the experiment — a free-text **`decision_notes`** column
where the team records why they shipped or stopped — and threads it from the database to the
dashboard. It's the smallest change that touches every layer in the week: model, migration,
Pydantic schemas, service, endpoint, TypeScript type, page. (The repo has a similar real
feature, `experiment_metadata`, managed by `POST /experiments/{id}/metadata`; this exercise
deliberately does the simpler column so you meet each layer bare.)

### The trace, as a build

**1. Model.** Add the column to `Experiment` — nullable, so existing rows and the old code
survive:

```python
# backend/app/models/experiment.py  (your change)
    # Free-form record of the ship/stop decision (Week 2 capstone)
    decision_notes = Column(Text, nullable=True)
```

**2. Migration.** Autogenerate, then read it:

```bash
export POSTGRES_DB=experimentation POSTGRES_SCHEMA=experimentation
python -m alembic -c backend/app/db/alembic.ini revision --autogenerate -m "add experiment decision_notes"
```

The generated `upgrade()` should be exactly one `op.add_column("experiments",
sa.Column("decision_notes", sa.Text(), nullable=True), schema="experimentation")` and the
`downgrade()` the matching `drop_column`. If autogenerate also proposes dropping an index or
altering an enum, that's drift unrelated to your change — leave it out of _this_ migration.
Check `down_revision` is the current head id. Apply it.

**3. Schemas.** Two places: the response model, so the field comes out; and the update model,
so it can go in. `ExperimentBase` feeds create; you probably don't want notes at creation.

```python
# backend/app/schemas/experiment.py  (your change)
class ExperimentUpdate(BaseModel):
    ...
    decision_notes: Optional[str] = Field(None, max_length=4000, description="Why the experiment was shipped or stopped")


class ExperimentResponse(...):
    ...
    decision_notes: Optional[str] = None
```

Because `ExperimentResponse` has `model_config = ConfigDict(from_attributes=True)`,
`model_validate(experiment)` picks the new attribute up from the ORM object automatically.

**4. Service.** `ExperimentService.update_experiment` copies updatable fields from the
schema onto the model. Find the field loop (or the explicit list) and make sure
`decision_notes` is included; `_experiment_to_dict` builds the response dict — add the key
there too, or the field will be `None` on the way out even though it's in the database.

**5. Endpoint.** Nothing to change: `PUT /experiments/{id}` already accepts
`ExperimentUpdate` and runs `get_experiment_access`. Confirm with the OpenAPI doc: the field
now appears in both schemas.

**6. Tests.** One unit test on the service (update then read back), one API test through
`TestClient` with the `client` fixture (`PUT` then `GET`, assert the field). The API test is
the one that would have caught a missing key in `_experiment_to_dict`.

**7. Dashboard type.** `frontend/src/types/experiments.ts` has the `Experiment` interface —
add `decision_notes?: string | null`. `ExperimentsService.update` already sends
`Partial<Experiment>`.

**8. Dashboard page.** On `frontend/src/pages/experiments/[id].tsx`, render the notes and a
textarea that calls `ExperimentsService.update(id, { decision_notes })`. Add a Jest test in
`frontend/src/tests/pages/experiment-detail.test.tsx` that mocks `global.fetch` and asserts
the PUT body — the existing tests there show the pattern.

**9. Verify end-to-end.** Run the API, the dashboard, edit the notes, then
`select decision_notes from experimentation.experiments where id = ...`.

### Build It

Do it. Then do the destructive half: `git stash` the model change but keep the migration
applied, restart the API, and hit `GET /experiments/{id}`. It works — the old code ignores the
new column — which is the concrete meaning of "backwards-compatible nullable column". Now
imagine the column had been `nullable=False` with no default, and the deploy had gone
migration-first: every insert from the old container fails until the new one is live. That's
why Day 26's deploy runs migrations _before_ the traffic switch and why the team's migrations
are additive.

### Key takeaways

- One field touches seven files across four layers; each layer has one job and the shapes
  must agree.
- Autogenerate the migration, read it, keep it minimal, check `down_revision`.
- `from_attributes=True` makes response schemas pick up ORM attributes; the service's dict
  builder does not — check both.
- Backwards-compatible schema changes (nullable, additive) are what make migrate-then-deploy
  safe.

---

## Week 2 Workshop

### Quiz

**1. In what order does a request pass through the middleware, and which layer is registered
first?**
RequestID → Prometheus → RateLimit → SecurityHeaders → CORS → router. CORS is registered
first (innermost); RequestID last (outermost).

**2. `ENVIRONMENT=prod` and `APP_ENV=dev` are both set. What environment does the app run in,
and what happens?**
`production` — `ENVIRONMENT` wins; `prod` is canonicalised with a `DeprecationWarning`, and a
warning logs that the two variables disagree. The settings validators then require real
secrets or refuse to start.

**3. A request carries both `Authorization: Bearer <valid JWT>` and `X-API-Key: <valid key>`
to `POST /tracking/assign`. Which identity is used?**
The API key's owner — the endpoint depends on `get_api_key`, not `get_current_user`. The
bearer header is ignored on that route.

**4. Why is `SECRET_KEY` validated only when `ENVIRONMENT == "production"` and `TESTING` is
unset, while the dev-bypass rule ignores `TESTING`?**
Tests legitimately construct `ProdSettings()` without real secrets to test production
behaviour; there is no legitimate production-with-bypass behaviour to test, so that guard has
no escape hatch.

**5. A `DEVELOPER` tries to `PUT` a feature flag they don't own. What happens?**
Allowed. `get_feature_flag_access` passes READ by role, and the ownership branch only fires
when the user _lacks_ UPDATE by role; developers have UPDATE on flags. A `VIEWER` would be
refused at the same branch.

**6. Why does `create_experiment` call `db.flush()` before adding variants?**
`SessionLocal` is `autoflush=False`, so the experiment's UUID isn't guaranteed to be
generated and usable as `experiment_id` on the variants until the insert is flushed.

### Exercises

**E2.1 — Order the onion.** Without looking, write the five `add_middleware` calls in
registration order, then the order a request traverses them. Explain why the rate limiter is
inside RequestID but outside the router.

**E2.2 — Write a guard.** Add a `field_validator` in the style of `validate_secret_key` for a
hypothetical `WEBHOOK_SIGNING_SECRET`, refusing `""`, `"changeme"` and anything under 32
characters in production, relaxed by `TESTING`.

**E2.3 — Compose a dependency.** Sketch an `async def can_archive_report(...)` that chains on
a `get_report_access` dependency and requires `Action.UPDATE` on `ResourceType.REPORT`.
Which roles pass?

**E2.4 — Model a table.** Define a `DecisionLog` model (who decided, which experiment, a
free-text reason, when) in the house style: mixin, schema-qualified FK, `back_populates` on
`Experiment`, a composite index on `(experiment_id, created_at)`, `ondelete="CASCADE"`.

**E2.5 — Read a migration.** For the `DecisionLog` table, write the `upgrade()` an
autogenerate would produce, then list two things you'd hand-edit before committing.

### Solutions

**S2.1** — Registration: `CORSMiddleware`, `SecurityHeadersMiddleware`,
`RateLimitMiddleware`, `PrometheusMetricsMiddleware`, `RequestIDMiddleware`. Traversal:
RequestID → Prometheus → RateLimit → SecurityHeaders → CORS → handler. Inside RequestID so a
429 still carries an `X-Request-ID` and is logged with it; outside the router so a rejected
request never reaches a handler (or a database session).

**S2.2**

```python
    WEBHOOK_SIGNING_SECRET: str = ""

    @field_validator("WEBHOOK_SIGNING_SECRET")
    @classmethod
    def validate_webhook_secret(cls, v: str, info: ValidationInfo) -> str:
        environment = (info.data or {}).get("ENVIRONMENT", "development")
        is_testing = os.getenv("TESTING", "").lower() in ("1", "true", "yes")
        if not is_testing and environment == "production":
            if v in {"", "changeme"} or len(v) < _MIN_SECRET_KEY_LENGTH:
                raise ValueError("WEBHOOK_SIGNING_SECRET must be at least 32 characters in production.")
        return v
```

Declare it _after_ `ENVIRONMENT` in the class so `info.data` contains it.

**S2.3**

```python
async def can_archive_report(
    report: Report = Depends(get_report_access),          # READ + ownership first
    current_user: User = Depends(get_current_user),
) -> bool:
    if not check_permission(current_user, ResourceType.REPORT, Action.UPDATE):
        raise HTTPException(status_code=403, detail=get_permission_error_message(ResourceType.REPORT, Action.UPDATE))
    return True
```

`ADMIN` and `ANALYST` pass (both have `UPDATE` on `REPORT`); `DEVELOPER` and `VIEWER` are
refused; superusers pass regardless.

**S2.4**

```python
class DecisionLog(Base, BaseModel):
    __tablename__ = "decision_logs"

    experiment_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="CASCADE"), nullable=False)
    decided_by = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.users.id", ondelete="SET NULL"))
    reason = Column(Text, nullable=False)

    experiment = relationship("Experiment", back_populates="decision_logs")

    @declared_attr
    def __table_args__(cls):
        schema_name = get_schema_name()
        return (
            Index(f"{schema_name}_decision_log_experiment_created", "experiment_id", "created_at"),
            {"schema": schema_name},
        )
```

Plus, on `Experiment`: `decision_logs = relationship("DecisionLog", back_populates="experiment",
cascade="all, delete-orphan")`, and an import in `backend/app/models/__init__.py`.

**S2.5** — `op.create_table("decision_logs", sa.Column("id", UUID(as_uuid=True),
primary_key=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at",
sa.DateTime(), nullable=False), sa.Column("experiment_id", UUID(as_uuid=True), nullable=False),
sa.Column("decided_by", UUID(as_uuid=True), nullable=True), sa.Column("reason", sa.Text(),
nullable=False), sa.ForeignKeyConstraint(["experiment_id"], ["experimentation.experiments.id"],
ondelete="CASCADE"), sa.ForeignKeyConstraint(["decided_by"], ["experimentation.users.id"],
ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"), schema="experimentation")` followed by
`op.create_index("experimentation_decision_log_experiment_created", "decision_logs",
["experiment_id", "created_at"], schema="experimentation")`. Hand-edits: replace the literal
`"experimentation"` with the `_schema()` helper so the migration works for any
`POSTGRES_SCHEMA`; add the epic/capstone docstring; and confirm `down_revision` is the current
head's id (autogenerate gets this right, but it's the check the team never skips).

---

**Next:** [Week 3 — The Hard Parts →](./week-3-hard-parts.md)

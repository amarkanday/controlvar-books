# Week 2 — The API and the Data

### Days 8–14 · FastAPI, Configuration, Auth, RBAC, SQLAlchemy, Alembic

[← Week 1](./week-1-core-loop.md) · [Contents](./README.md) · [Week 3 →](./week-3-hard-parts.md)

Week 1 was _what_ the platform does. This week is the machinery every request passes through
on the way to doing it: the FastAPI application and its middleware onion, the settings object
that decides which environment you're in and refuses to start when it's unsafe, the ways a
caller proves who they are, the role table that decides what they may do, the SQLAlchemy models
and the PostgreSQL schema they live in, and the migration story. By Day 14 you'll add a column
and watch it light up from the database to the dashboard.

> **The one sentence to carry through Week 2.** _A request enters through nine middleware
> layers (CORS outermost), is authenticated by one of the `deps` functions (bearer JWT, Cognito
> token, or API key), authorised by a role → resource → action table in which ownership plays
> no part, handled by a thin endpoint that delegates to a service, which reads and writes
> SQLAlchemy models inside one `Session` per request — and every one of those layers is
> configured by a single `Settings` object that fails closed in staging and production._

| Day | Topic                               | The one thing it's really about                                     |
| --- | ----------------------------------- | ------------------------------------------------------------------- |
| 8   | The FastAPI application             | The onion of middleware, the router tree and the modules seam      |
| 9   | Configuration                       | One `Settings` class, four environments, validators that refuse     |
| 10  | Authentication                      | Three credentials, one `get_current_user`, one `get_api_key`        |
| 11  | Authorization                       | Role × resource × action, by role, never by owner                  |
| 12  | The models and the schema           | Declarative tables, relationships, JSONB, and the schema prefix     |
| 13  | Alembic and the bootstrap           | Two heads, one schema writer, create_all for new databases          |
| 14  | **Capstone:** add a field end-to-end | Model → migration → schema → service → endpoint → dashboard       |

---

## Day 8 — The FastAPI application: routers, the middleware stack, the lifespan

### What & why

**FastAPI** is an ASGI web framework: you declare _path operations_ (`@router.get(...)`) as
Python functions whose parameters describe the request (path, query, body, headers,
dependencies), and the framework does the parsing, validation, OpenAPI generation and
serialisation. **Starlette** underneath provides the ASGI plumbing — middleware, routing,
WebSockets. **uvicorn** runs it.

Controlvar uses four FastAPI features heavily, and today is about how they're arranged:

1. **Routers.** Each resource is an `APIRouter` in its own module; `api/api.py` mounts them
   all under `/api/v1` with a prefix and a tag. The tag becomes the section heading in the
   OpenAPI docs; the prefix becomes the URL.
2. **Middleware.** Cross-cutting behaviour is a stack of nine ASGI middleware wrapped around
   the router: CORS, request IDs, Prometheus metrics, security headers, a trusted-host check,
   rate limiting, a trailing-slash redirect that keeps the client's origin, a request-body size
   limit, and an unhandled-error layer that turns a crash into a readable 500. The order they're registered
   in is the opposite of the order they run, and the file says so.
3. **Lifespan.** Startup and shutdown are one async context manager. It first refuses to serve
   when `ENVIRONMENT` was never set, then starts and stops the five background schedulers.
4. **The modules seam.** The optional modules (under `modules/`) add routers, models and
   OpenAPI tags through hooks. A core build has no `modules` package and still starts.

#### Why middleware order is stated, not assumed

`add_middleware` puts each new layer _around_ the ones already registered: the first call
becomes the innermost layer, the last becomes the outermost. That has consequences you can
reason about, and the codebase learned each of them the hard way:

- **CORS is registered last, so it is the outermost layer.** Every response any inner layer
  returns — a 429 from the rate limiter above all — then carries the `Access-Control-*`
  headers a browser needs before it will let the page read the status. With CORS innermost, a
  rate-limited login reached the dashboard as "Can't reach the API" (fixed in commit `2ca41c38`). A CORS preflight is
  answered by CORS itself and goes no further.
- **The request-ID layer sits just inside CORS**, so the id is bound into the logging context
  before the other layers log anything, and a 429 still carries `X-Request-ID`.
- **The rate limiter sits inside the security headers and the trusted-host check**, because it
  returns its 429 without calling the next layer: anything nested inside it never runs for a
  rejected request. Outside it, the 429 still gets `X-Content-Type-Options`, the CSP and (in
  production) HSTS, and a forged `Host` costs a header comparison rather than a rate-limit
  bucket.
- **The unhandled-error layer is registered first, so it is innermost.** Its 500 travels back
  out through every other layer and picks up CORS, the request id and the security headers on
  the way.
- **The body-limit layer is registered right after it**, so it is inside every other layer and
  its 413 picks up the same headers. It also has to stay inside each `BaseHTTPMiddleware` layer:
  registered outside one, the route reading the body is handed an `ExceptionGroup` instead of the
  `HTTPException`, and FastAPI answers a chunked body over the limit with 400 (the file says it
  measured this).

### How Controlvar does it

The app module reads top to bottom as configure → construct → wrap → mount. The lifespan:

```python
# 📁 backend/app/main.py
class EnvironmentNotSet(RuntimeError):
    """The API was started without ENVIRONMENT (see :func:`lifespan`)."""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage background scheduler lifecycle for the FastAPI app.
    ...
    First, it refuses to serve when ENVIRONMENT was never set: the settings
    then default to development, whose relaxed rules a deployment must choose
    rather than fall into. Importing the application (the OpenAPI dump, the
    release's version check) is unaffected; only serving it is refused.
    """
    if not settings.environment_explicit:
        logger.error(ENVIRONMENT_NOT_SET_MESSAGE)
        raise EnvironmentNotSet(ENVIRONMENT_NOT_SET_MESSAGE)

    if settings.is_test:
        logger.info("Test environment: background schedulers are not started")
        yield
        return

    logger.info("Starting experiment scheduler")
    experiment_scheduler.interval_minutes = (
        settings.EXPERIMENT_SCHEDULER_INTERVAL_MINUTES
    )
    await experiment_scheduler.start()

    logger.info("Starting rollout scheduler")
    await rollout_scheduler.start()
    ...
    logger.info("Starting bandit scheduler")
    await bandit_scheduler_runner.start()

    yield

    logger.info("Stopping experiment scheduler")
    await experiment_scheduler.stop()
    ...
```

Then the app itself, the validation-error handler, and the middleware stack. The comments are
the file's own, shortened; read the full ones in the source, because each records a defect:

```python
# 📁 backend/app/main.py
app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.PROJECT_DESCRIPTION,
    version=settings.VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
)

# A request that fails validation is answered with each error's type, loc, msg
# and ctx, and without the submitted values (#500).
app.add_exception_handler(RequestValidationError, request_validation_error_handler)

# `add_middleware` inserts at the FRONT of `app.user_middleware`, and Starlette
# builds the stack by wrapping in reverse of that list -- so the LAST
# registered call is the outermost layer, not the first.
cors_origins = settings.cors_allowed_origins
for _level, _message in cors_start_up_messages(settings):
    logger.log(_level, _message)

# An unhandled route exception becomes a 500 the browser can read (#72).
app.add_middleware(UnhandledErrorMiddleware)

# A request body larger than MAX_REQUEST_BODY_SIZE is answered 413, on every
# route: at once when its Content-Length says so, otherwise when the bytes read
# pass the limit.
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BODY_SIZE)

# Trailing-slash redirects keep the client's own origin (#86).
app.add_middleware(RelativeSlashRedirectMiddleware)

# Rate limiter — disabled during tests to avoid interfering with test assertions
_rate_limit_enabled = not settings.is_test
app.add_middleware(RateLimitMiddleware, enabled=_rate_limit_enabled)

# Trusted Host — refuse a request whose `Host` is not one of ours (#220).
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=settings.effective_allowed_hosts
)

# Security headers — lightweight and stateless.
app.add_middleware(SecurityHeadersMiddleware)

# Prometheus latency / request-count middleware (EP-013)
if _monitoring_imports_ok:
    app.add_middleware(PrometheusMetricsMiddleware)
    app.add_middleware(RequestIDMiddleware)

# CORS last, i.e. outermost -- see the note at the top of this block.
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    expose_headers=[
        "X-Request-ID",
        "Retry-After",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Window",
        # The audit log export's entry count, which the dashboard compares
        # with the rows it received before it saves the file.
        "X-Total-Count",
    ],
)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Health probes (/health/live, /health/ready, /health) and Prometheus /metrics
app.include_router(health_router)
```

So a request passes, outermost first: **CORS → RequestID → Prometheus → SecurityHeaders →
TrustedHost → RateLimit → RelativeSlashRedirect → RequestBodyLimit → UnhandledError → router**
(Starlette slips
its own `ExceptionMiddleware` in just before the router), and the response walks back out the
same way.

Four details in that block are worth a pause, because each was once a bug:

- **`allow_headers` includes `X-API-Key`.** Without it, a browser-based SDK (ShopLab) gets a
  CORS preflight failure that looks like an auth error.
- **`expose_headers` includes `Retry-After`.** It is what a browser lets the page _read_; without
  it the dashboard knows a login was refused but not for how long.
- **`allow_credentials=False`.** No first-party client sends cookies cross-origin: the dashboard
  authenticates with a bearer header, and SSO's state cookie rides a top-level navigation.
  With credentials off, a `*` entry can never be combined with them.
- **The origin list is a settings property**, `settings.cors_allowed_origins`, not an inline
  fallback in `main.py`. Day 9 shows how it is resolved; the short version is that staging and
  production allow only the origins you configure.

The **trusted-host** layer answers 400 to a request whose `Host` is not one the deployment
answers to. The allow-list is `ALLOWED_HOSTS` when set, otherwise the host of
`PUBLIC_BASE_URL`; `/health`, `/health/live`, `/health/ready` and `/metrics` are exempt, because
the load balancer health-checks with the target's own IP as `Host` and would otherwise fail
every probe. In development, with neither setting, the check is off.

The router tree is assembled in one place, in two halves — the core routers, then whatever the
modules registered:

```python
# 📁 backend/app/api/api.py
def register_core_routers(router: APIRouter) -> APIRouter:
    """Mount every core endpoint router on *router*; returns it.
    ...
    """
    router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
    # P0 (open-core): user-owned API keys for SDK authentication
    router.include_router(api_keys.router, prefix="/api-keys", tags=["API Keys"])
    router.include_router(users.router, prefix="/users", tags=["Users"])
    # Unauthenticated profile/modules probe for the dashboard chrome.
    router.include_router(modules.router, tags=["Modules"])
    router.include_router(
        experiments.router, prefix="/experiments", tags=["Experiments"]
    )
    router.include_router(tracking.router, prefix="/tracking", tags=["Tracking"])
    router.include_router(client_errors.router, prefix="/tracking", tags=["Tracking"])
    router.include_router(
        flag_evaluations.router, prefix="/tracking", tags=["Tracking"]
    )
    router.include_router(
        feature_flags.router, prefix="/feature-flags", tags=["Feature Flags"]
    )
    ...
    router.include_router(sdk.router, prefix="/sdk", tags=["SDK"])
    router.include_router(
        websocket_results.router, prefix="", tags=["WebSocket Results"]
    )

    return router


def build_v1_router() -> APIRouter:
    """Build the v1 router: core routers, then anything the seam added.
    ...
    """
    load_modules()
    router = APIRouter()
    register_core_routers(router)
    mount_module_routers(router)
    return router


# Create API router for v1
api_router_v1 = build_v1_router()

# Main API router that includes versioned routers
api_router = APIRouter()
api_router.include_router(api_router_v1)
```

Note that three routers share the `/tracking` prefix (event tracking, client error reports, and
the locally-evaluated flag counts at `POST /tracking/evaluations`), and that some mount at
`prefix=""` (bulk toggle, WebSocket results) because their paths are already complete. The
OpenAPI document at `/api/v1/openapi.json` is generated from this tree, and its tag
descriptions are `tags_metadata = CORE_TAGS_METADATA + hooks.extra_tags_metadata()`: the core
descriptions plus whatever the modules registered.

#### The modules seam

Controlvar is one codebase with two build profiles. **Core** is `backend/` alone; **full**
adds the optional modules under `modules/` (workspaces, HIPAA, compliance reports, SSO, custom
roles, the warehouse connectors, integrations, real-time counters, ETL and split-URL
routing). `modules-manifest.txt` is the authority on what is a module. The profile is decided
by one thing: whether `import modules` succeeds.

Nothing in `backend/` names a module endpoint. `modules/backend/app/register.py` calls
`hooks.register_router()` (and its siblings for models and tags), and `build_v1_router()` mounts
what was registered — `/rbac`, `/auth/sso`, `/workspaces`, `/hipaa`, `/warehouse/analysis` and
the rest arrive only that way. `main.py` repeats the `load_modules()` call so the entry point
names the seam, then calls `abort_if_modules_broken()`: a `modules` package that was _found_ but
failed to register is a full deployment that would serve less than it was deployed as, so
outside development and test the process refuses to start. The unauthenticated
`GET /api/v1/modules` reports `{"profile": "core"|"full", "modules": [...], "version": ...}`;
the dashboard reads it to decide which navigation to render.

The rule that keeps the seam honest: **everything under `backend/` must work with `modules/`
deleted.** `scripts/core_build.sh` builds exactly that tree and runs the suite in it, and an
import-linter contract refuses a core import of a module.

#### A path operation, anatomised

Every endpoint in the repo follows one shape. Here is a lifecycle endpoint with the parts
labelled:

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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )

        # READ and UPDATE on experiments; who created it is not considered
        experiment = deps.get_experiment_change_access(experiment, current_user)  # ⑦ authorisation

        experiment_service = ExperimentService(db)                                 # ⑧ the service
        ...
        before_audit = audit_snapshot(EntityType.EXPERIMENT, experiment)
        try:
            started_experiment = experiment_service.start_experiment(experiment)
        except EndDatePassedError as err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=END_DATE_PASSED
            ) from err

        record_experiment_change(                                                  # ⑪ the audit entry
            db, current_user, ActionType.EXPERIMENT_START, experiment_id, before_audit
        )
        ...
        return ExperimentResponse.model_validate(started_experiment)               # ⑨ ORM/dict → schema
    except HTTPException:
        # Let deliberate 4xx responses through instead of wrapping them in a 500.
        raise
    except Exception as e:
        raise unexpected_failure(                                                  # ⑩ a fixed sentence, never str(e)
            e,
            "Experiment start",
            "Could not start the experiment",
            db=db,
            logger=logger,
        )
```

Parameters ③–⑥ are all _declared_, never parsed by hand; FastAPI turns a malformed UUID into a
422 before the function runs. ⑦ is a plain function call (experiment dependencies are sync)
that admits the caller by role — Day 11. ⑨ is Pydantic v2's `model_validate` with
`from_attributes=True` on the schema, so an ORM object or a dict both serialise. And the
`except HTTPException: raise` is a pattern you'll see everywhere: it exists because a bare
`except Exception` would turn a deliberate 404 into a 500.

⑩ is the error contract. `unexpected_failure()` (in `backend/app/core/logger.py`) rolls the
session back, logs `"Experiment start failed (<exception type>)"` with the traceback under the
request's id, and returns an `HTTPException` whose detail is a fixed sentence plus that id:
`"Could not start the experiment (request ID: <id>)."`. The exception's own text never reaches
the client — it can carry SQL, a path, or what the caller sent. Two more layers complete the
contract: the `RequestValidationError` handler answers a 422 with each error's `type`, `loc`,
`msg` and `ctx` but not the submitted value, and `UnhandledErrorMiddleware` turns anything that
escapes a route into a 500 that still carries CORS and the request id.

⑪ is the audit trail. Before the change the endpoint takes `audit_snapshot` of the fields an entry
records for an experiment (key, name, status, the dates, `correction_method`,
`confidence_level`); after the change commits, `record_experiment_change` reads the row back and
writes one entry holding only what changed. The write is best-effort by design: "the change is
committed: reading it back must not turn it into a 500", so a failed audit write is logged,
counted in `audit_write_failures_total`, and does not undo the experiment change. Note the other
new branch: `start_experiment` raises `EndDatePassedError` (answered 400) when an experiment with
no start date has an end date that has already passed, so it is refused *before* anything is
written.

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

Print the stack the way Starlette will build it — `user_middleware` lists the outermost layer
first:

```bash
ENVIRONMENT=development python -c "
from backend.app.main import app
print(' -> '.join(m.cls.__name__ for m in app.user_middleware))" 2>/dev/null
# CORSMiddleware -> RequestIDMiddleware -> PrometheusMetricsMiddleware -> SecurityHeadersMiddleware
#   -> TrustedHostMiddleware -> RateLimitMiddleware -> RelativeSlashRedirectMiddleware
#   -> RequestBodyLimitMiddleware -> UnhandledErrorMiddleware
```

Then watch the order from the outside. With the stack running (`make dev`, or the compose
stack), exhaust the login limit from a browser origin:

```bash
# a 429 still gets a request id (RequestID is outside RateLimit) and CORS headers (CORS is outermost)
for i in $(seq 1 12); do curl -s -o /dev/null -w "%{http_code} " -X POST $API/auth/login \
  -H 'Origin: http://localhost:3000' -H 'Content-Type: application/json' -d '{"email":"x@y","password":"z"}'; done; echo
curl -si -X POST $API/auth/login -H 'Origin: http://localhost:3000' -H 'Content-Type: application/json' \
  -d '{"email":"x@y","password":"z"}' | grep -i "HTTP/\|x-request-id\|x-ratelimit\|retry-after\|access-control"
```

(`/auth/login` is limited to 10/min — Day 9 lists the table.) Now prove the lifespan guard: a
process with no `ENVIRONMENT` imports fine but will not serve.

```bash
env -u ENVIRONMENT -u APP_ENV python -c "
from fastapi.testclient import TestClient
from backend.app.main import app
with TestClient(app): pass" 2>&1 | tail -1
# backend.app.main.EnvironmentNotSet: ENVIRONMENT is not set: set it to development, test, staging or production before starting the API.
```

Finally, read the router tree as the feature list and count the tags:

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
curl -s http://localhost:8000/api/v1/modules   # which profile is this?
```

### Key takeaways

- One `FastAPI` app; core routers mounted by `register_core_routers()` under `/api/v1`, module
  routers added by the seam; tags are the doc sections.
- Nine middleware layers. A request runs CORS → RequestID → Prometheus → SecurityHeaders →
  TrustedHost → RateLimit → RelativeSlashRedirect → RequestBodyLimit → UnhandledError →
  handler; registration order is the reverse, and the file says why.
- The lifespan refuses to serve without an explicit `ENVIRONMENT`, then starts and stops the
  five schedulers (none under `test`).
- An endpoint declares its inputs, authenticates via `deps`, authorises by role, delegates to a
  service and returns a schema; `except HTTPException: raise` keeps 4xx honest, and
  `unexpected_failure()` keeps 500s from leaking the exception's text.
- The core must run with `modules/` deleted; a present-but-broken modules package stops a
  staging or production start.

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
worker per container (`WEB_CONCURRENCY` defaults to 1), scaling containers rather than
workers. A `def` handler would be run in a threadpool automatically; several endpoints are
`def` for exactly that reason. Be consistent within a module.

**Q. Why does a preflight not count against the login limit any more?**
CORS answers it before the request reaches the limiter, and the limiter itself also skips an
`OPTIONS` that carries `Access-Control-Request-Method`. Counting preflights spent half of the
ten-per-minute login budget on requests the user never made.

---

## Day 9 — Configuration: `Settings`, environments, and the fail-closed validators

### What & why

Configuration is where a platform decides what it _is_ in this process: which database, which
auth provider, which environment, which secrets, which hostname. Controlvar uses
**pydantic-settings**: a `Settings` class whose fields are read from environment variables
(and an optional `.env.dev` / `.env.test` / `.env.prod` file), type-coerced, and validated —
with the validation doing real safety work.

Four ideas organise the module:

1. **One canonical environment name, chosen on purpose.** `ENVIRONMENT ∈ {development, test,
   staging, production}`. The legacy spellings (`dev`, `prod`) and the legacy variable
   (`APP_ENV`) are accepted with a `DeprecationWarning`, and the canonical value is mirrored
   back into `APP_ENV` for the modules that still read it. If neither variable is set the
   settings object still builds as `development` — so a script can import it — but the API
   refuses to _serve_ (Day 8's lifespan), and the container refuses to start at all.
2. **A subclass per environment**, selected at import time: `DevSettings`, `TestSettings`,
   `ProdSettings` (used for staging too). They differ only in defaults (test database and
   schema, dev CORS origins, which dotenv file is read).
3. **Validators that refuse to start** when a dangerous value reaches staging or production —
   the two _hardened_ environments: a placeholder or short `SECRET_KEY`, a weak or demo
   `FIRST_SUPERUSER_PASSWORD`, the dev-admin bypass, no hostname to answer on, and the test
   runner's `TESTING` flag itself. The principle is _fail closed_: a misconfigured deployment
   should not come up at all.
4. **Module settings live with the modules.** The audit-log signing key (`AUDIT_HMAC_KEY`), the
   SSO, HIPAA and warehouse settings are on `ModulesSettings` in
   `modules/backend/app/settings.py`, validated by the modules' registration, so a core
   deployment never has to supply a module's secret to start.

#### Why "fail closed" belongs in the settings object

You could check for weak secrets at the call site — in the JWT signer, in the audit signer.
But then every new secret needs a new check, and one forgotten check is a production
deployment signing tokens with `"default-secret-key-for-testing"`. Putting the checks on the
`Settings` fields means they run once, at import, before the app can serve a single request,
and they run for every code path that reads the setting.

There is deliberately **no escape hatch**. An earlier version let `TESTING=true` relax the
secret checks so the test suite could build production settings without real keys; that is
one environment variable between a production deployment and its demo secrets. Today
`TESTING` relaxes nothing: combined with staging or production it is refused outright, before
any field is validated.

### How Controlvar does it

The environment resolution and the base class (an excerpt; the class has many more fields):

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
                raw,
                legacy,
            )
        return canonical
    if legacy:
        ...
        return canonical_environment(legacy)
    return "development"


class Settings(BaseSettings):
    """Base settings class."""

    PROJECT_NAME: str = "Controlvar"
    VERSION: str = Field(
        default_factory=get_version, validation_alias="EXPERIMENTLY_VERSION"
    )
    API_V1_STR: str = "/api/v1"
    ...
    ENVIRONMENT: EnvironmentName = "development"
    DEBUG: bool = False
    SECRET_KEY: str = "default-secret-key-for-testing"  # Default for testing only
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60  # 1 hour

    AUTH_PROVIDER: Literal["local", "cognito"] = "local"
    DEV_AUTH_BYPASS: bool = False

    LOCAL_AUTH_TOKEN_TTL_MINUTES: int = 720  # 12 hours
    LOCAL_AUTH_MAX_FAILED_ATTEMPTS: int = 10
    LOCAL_AUTH_LOCKOUT_MINUTES: int = 15

    PUBLIC_BASE_URL: Optional[str] = None
    ALLOWED_HOSTS: Annotated[List[str], NoDecode] = []
    BACKEND_CORS_ORIGINS: Annotated[List[str], NoDecode] = []
    CORS_ORIGINS: Annotated[List[str], NoDecode] = []
    DASHBOARD_ORIGINS: Annotated[List[str], NoDecode] = []

    # Database settings
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "experimentation"
    POSTGRES_PORT: str = "5432"
    ...
    SAFETY_CHECK_INTERVAL_MINUTES: int = 5
    ROLLOUT_CHECK_INTERVAL_MINUTES: int = 15
    EXPERIMENT_SCHEDULER_INTERVAL_MINUTES: int = Field(default=15, ge=1)

    SDK_RATE_LIMIT_PER_MINUTE: int = 6000
    ...
    COGNITO_SELF_SIGNUP_ENABLED: bool = False

    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",  # Ignore unknown fields to catch typos
        hide_input_in_errors=True,
    )
```

A few fields deserve a sentence each:

- **`VERSION`** comes from the repository's `VERSION` file (which release-please maintains). It
  is read through the alias `EXPERIMENTLY_VERSION` rather than the bare `VERSION` variable,
  which build scripts and CI steps export for their own purposes and which used to change what
  `/health` and the OpenAPI document reported.
- **`PUBLIC_BASE_URL`** is the URL people reach the service at, e.g.
  `https://controlvar.example.com`. Every absolute URL the app _emits_ — the OIDC
  `redirect_uri` above all — is built from it rather than from the request's `Host` and scheme,
  which a client controls. It is also where the trusted-host allow-list comes from when
  `ALLOWED_HOSTS` is unset (usually leave it unset).
- **`BACKEND_CORS_ORIGINS` and `CORS_ORIGINS`** are both plain-string lists annotated `NoDecode`,
  so a comma-separated value reaches the validator intact; either may also be a JSON array.
  `settings.cors_allowed_origins` takes the first of the two with a usable entry, normalises each
  entry to the form a browser's `Origin` header takes (`scheme://host[:port]`, no trailing
  slash), drops a malformed entry with an ERROR log instead of refusing to start, and adds every
  `DASHBOARD_ORIGINS` entry. With neither set, development and test fall back to the localhost
  dashboard and demo ports; **staging and production allow no other origin at all**.
- **`DASHBOARD_ORIGINS`** names the dashboard's origin when it is not served from
  `PUBLIC_BASE_URL` — where an SSO sign-in may send the browser back to.
- **`hide_input_in_errors=True`**: a refused configuration says which setting and why, never the
  value it was given. Secrets do not end up in a crash log.

The validators are the safety layer. Four of them, because their shapes are the shapes of every
guard in the file:

```python
# 📁 backend/app/core/config.py
def _hardening_required(info: ValidationInfo) -> bool:
    """Secrets must be real in staging/production.

    ``TESTING`` does not relax this: ``Settings`` refuses ``TESTING`` together
    with a staging or production environment before any field is validated
    (``refuse_testing_in_a_hardened_environment``).
    """
    environment = (info.data or {}).get("ENVIRONMENT", "development")
    return environment in HARDENED_ENVIRONMENTS
    ...

    @model_validator(mode="before")
    @classmethod
    def refuse_testing_in_a_hardened_environment(cls, data: Any) -> Any:
        ...
        refusal = testing_refusal(environment, os.environ.get("TESTING"))
        if refusal:
            raise ValueError(refusal)
        return data

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

    @model_validator(mode="after")
    def require_a_host_allow_list_when_hardened(self) -> "Settings":
        ...
        environment = getattr(self, "ENVIRONMENT", "development")
        if environment not in HARDENED_ENVIRONMENTS:
            return self
        if "*" in self.ALLOWED_HOSTS:
            raise ValueError(
                "ALLOWED_HOSTS=* defeats the check it configures and is refused "
                ...
            )
        if not self.effective_allowed_hosts:
            raise ValueError(
                "PUBLIC_BASE_URL is not set, so this deployment does not know "
                ...
            )
        return self

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str, info: ValidationInfo) -> str:
        ...
        if _hardening_required(info):
            if _secret_is_placeholder(v) or len(v) < _MIN_SECRET_KEY_LENGTH:
                raise ValueError(
                    f"SECRET_KEY must be at least {_MIN_SECRET_KEY_LENGTH} characters "
                    "and must not be a placeholder from the repository (docker-compose.yml, "
                    ".env.example or the class default) in staging/production. "
                    'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
                )
        return v
```

Read the four as a set:

- **`refuse_testing_in_a_hardened_environment`** is a `mode="before"` model validator so that it
  runs first and its message is the one you see, not whichever setting `TESTING` would otherwise
  have affected.
- **`forbid_dev_auth_bypass_outside_dev`** is an `after` model validator because it needs two
  fields at once.
- **`require_a_host_allow_list_when_hardened`** is the one thing a new deployment most often
  trips over: staging and production must know the hostname they answer on, and there is one
  setting that satisfies it, `PUBLIC_BASE_URL`. `ALLOWED_HOSTS=*` is refused there, and an
  entry that can never match (`*example.com`, missing the dot) is refused in every environment
  — it would otherwise refuse every request while the exempt health checks stayed green.
- **`validate_secret_key`** is a `field_validator` that reads `ENVIRONMENT` from `info.data`,
  which works only because `ENVIRONMENT` is declared _above_ `SECRET_KEY` in the class; Pydantic
  validates fields in declaration order. The placeholder list (`settings_rules.py`) includes
  every value committed in `docker-compose.yml` and `.env.example`, so a deployment cannot ship
  the repository's own example. `FIRST_SUPERUSER_PASSWORD` has the same shape, refusing
  anything shorter than 8 characters and the demo password.

The rules themselves (the placeholder list, the minimum lengths, the hardened environments, the
messages) live in `backend/app/core/settings_rules.py`, a module with no settings object behind
it. That is so the **container's start-up check** can apply the same rules: the first thing
`backend/docker-entrypoint.sh` runs is `python -m backend.app.core.preflight`, which lists
_every_ missing or unusable setting at once and exits 78 — instead of the settings class
refusing them one at a time across several restarts. The preflight also refuses a missing
`ENVIRONMENT` and `ENVIRONMENT=test` (the test runner's environment, not one the image runs in).

The connection strings are _assembled_ from parts by `mode="before"` validators so an
operator can set either `DATABASE_URI` or the five `POSTGRES_*` pieces:

```python
# 📁 backend/app/core/config.py
def _postgres_url(data: Dict[str, Any]) -> str:
    """The database URL from the POSTGRES_* fields, password percent-encoded.

    ``PostgresDsn.build`` does not encode, so a password containing ``#``,
    ``?``, ``%``, ``:`` or a space either broke the URL or refused to validate
    (#146). ``backend/app/db/url.py`` explains the encoding.
    """
    return postgres_url(
        user=data.get("POSTGRES_USER") or "",
        password=data.get("POSTGRES_PASSWORD") or "",
        host=data.get("POSTGRES_SERVER"),
        port=int(data.get("POSTGRES_PORT", 5432)),
        database=data.get("POSTGRES_DB", ""),
    )
    ...

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
        return _postgres_url(info.data)
```

Aurora generates passwords with any printable character in them; percent-encoding them is the
difference between a deployment that connects and one that fails to parse its own URL.

And the selection at the bottom of the module is what every other file imports as `settings`:

```python
# 📁 backend/app/core/config.py
_resolved_environment = resolve_environment_from_process_env()
# Read before the APP_ENV mirror below writes to the process environment, after
# which it would always look set.
_environment_explicit = environment_is_set_in_process_env()

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
settings._environment_explicit = _environment_explicit
```

`settings.environment_explicit` is what the lifespan checks. It also gates the bootstrap's first
administrator: a weak `FIRST_SUPERUSER_PASSWORD` is accepted only when `ENVIRONMENT` was _set_ to
development or test, never when development is merely the default.

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
    "/api/v1/users/me/password": (5, 60),
    "/api/v1/auth/sso/exchange": (10, 60),
    "/api/v1/auth/sso/login": (30, 60),
    "/api/v1/tracking/assign/batch": (60, 60),
    "/api/v1/audit-logs/export": (10, 60),
    "/api/v1/ai/design": (10, 60),
}

# Default rate limit for all other endpoints
DEFAULT_RATE_LIMIT: Tuple[int, int] = (300, 60)  # 300 req/min

SDK_PATH_PREFIXES: Tuple[str, ...] = (
    "/api/v1/tracking/",
    "/api/v1/feature-flags/evaluate/",
    "/api/v1/feature-flags/user/",
    "/api/v1/sdk/",
)
DEFAULT_SDK_RATE_LIMIT_PER_MINUTE = 6000

EXPORT_PATH_PREFIX = "/api/v1/export/"
EXPORT_RATE_LIMIT: Tuple[int, int] = (10, 60)  # 10 req/min, all exports together

AI_INTERPRET_PATH_PREFIX = "/api/v1/ai/interpret/"
AI_INTERPRET_RATE_LIMIT: Tuple[int, int] = (10, 60)  # 10 req/min, every id together


def resolve_rate_limit(
    path: str, sdk_limit_per_minute: int = DEFAULT_SDK_RATE_LIMIT_PER_MINUTE
) -> Tuple[int, int]:
    """
    Return ``(max_requests, window_seconds)`` for a request path.

    Exact entries in ``RATE_LIMIT_CONFIG`` win, then the export prefix, then
    the AI interpretation prefix, then SDK path prefixes, then
    ``DEFAULT_RATE_LIMIT``.
    """
    exact = RATE_LIMIT_CONFIG.get(path)
    if exact is not None:
        return exact
    if path.startswith(EXPORT_PATH_PREFIX):
        return EXPORT_RATE_LIMIT
    if path.startswith(AI_INTERPRET_PATH_PREFIX):
        return AI_INTERPRET_RATE_LIMIT
    if any(path.startswith(prefix) for prefix in SDK_PATH_PREFIXES):
        return (int(sdk_limit_per_minute), 60)
    return DEFAULT_RATE_LIMIT
```

The SDK tier exists because of ShopLab: a single server-side SDK, or an office behind one NAT,
legitimately sends thousands of assignments a minute from one IP, and `/tracking/batch` was
falling under the 300/min default. `/api/v1/sdk/` joined it when server-side SDKs began polling
the local-evaluation ruleset. The export tier is the opposite case: every export computes
results for every experiment it covers, so all export routes share **one** counter per client
(`rate_limit_key` keys them on the prefix, not the path). Four newer rows are the same idea, one
route at a time. `/tracking/assign/batch` gets 60 a minute, **not** the SDK tier's 6,000, because
one request assigns up to 1,000 users (6,000 requests would be 6 million users a minute from one
address). The audit-log export streams up to 50,000 entries while holding a database connection,
so it has a counter of its own. The two AI routes each call a language model and wait for it,
so `/ai/design` and every `/ai/interpret/<id>` (one shared budget, whatever the id) get 10 a
minute. A row for a path that is a prefix of other paths would shadow them; that is why the batch
route is an exact entry, and why the file's comment notes that a trailing slash is answered by
FastAPI's 307 redirect to the exact path. `SDK_RATE_LIMIT_PER_MINUTE` in
`Settings` feeds `sdk_limit_per_minute` — a scalar in `Settings`, a table in the module, and a
pure function joining them that the tests can call without a server.

Which _client_ a request is charged to matters as much as the limit. The limiter uses
`request.client.host` and nothing else. uvicorn runs with `--proxy-headers` and
`--forwarded-allow-ips` set to the private address ranges by default (`FORWARDED_ALLOW_IPS`
overrides it), so it has already resolved the real client from `X-Forwarded-For` before any
middleware runs. An earlier version read the header itself and took the _leftmost_ entry —
the one the client wrote — so a client that sent a fresh address on every request got a fresh
bucket every time, including against the login limit.

Where the counters live matters too. `RedisRateLimiter` counts in Redis (`INCR` + `EXPIRE`), and
when Redis fails it does not stop serving: it falls back to an in-memory sliding window **per
process**, so while it lasts a per-IP limit is multiplied by the number of API processes. The
fallback lasts `REDIS_RETRY_SECONDS` (30 s); inside that window no request touches Redis, and
the first request after it is the retry. The client is built with `retry=Retry(NoBackoff(), 0)`
because redis-py's default of ten retries with backoff turns one failure into 15 to 25 seconds
of blocking on the event loop, which a retry every 30 seconds would repeat. Each switch logs
once with a `rate_limiter` field (`per_process` or `redis`), and two metrics
(`rate_limit_redis_fallback_active`, `rate_limit_redis_fallbacks_total`) let an alert see it.
The fallback map also forgets keys whose window has emptied, so it does not grow for as long as
the outage lasts.

> **Did You Know?** `extra="ignore"` in `model_config` means an unknown environment variable
> is silently dropped rather than rejected. The comment says "to catch typos", which is
> backwards — `extra="forbid"` would catch typos. The team chose `ignore` because the process
> environment in a container is full of unrelated variables; the cost is that
> `SAFTEY_CHECK_INTERVAL_MINUTES=1` does nothing and tells you nothing. Spell settings
> carefully, and check `settings.<NAME>` in a shell when a value seems not to take.

### Build It

Prove the guards refuse. Run these from the repository root with the venv active, and with
`TESTING` unset in your shell (the test workflow exports it, and it would change the first
answer):

```bash
# production + placeholder secret: both secret fields are refused, in one error
env -u TESTING ENVIRONMENT=production SECRET_KEY=changeme \
  python -c "import backend.app.core.config" 2>&1 | grep -A1 "^SECRET_KEY\|^FIRST_SUPERUSER\|validation error"

# TESTING is refused outright in staging and production
ENVIRONMENT=production TESTING=true python -c "import backend.app.core.config" 2>&1 | grep "Value error"

# real secrets, but no PUBLIC_BASE_URL: the host allow-list guard
K=$(python -c 'import secrets;print(secrets.token_hex(32))')
env -u TESTING ENVIRONMENT=production SECRET_KEY=$K FIRST_SUPERUSER_PASSWORD=Str0ngPassw0rd \
  python -c "import backend.app.core.config" 2>&1 | grep "Value error"

# everything right except the dev bypass
env -u TESTING ENVIRONMENT=production SECRET_KEY=$K FIRST_SUPERUSER_PASSWORD=Str0ngPassw0rd \
  PUBLIC_BASE_URL=https://controlvar.example.com DEV_AUTH_BYPASS=true \
  python -c "import backend.app.core.config" 2>&1 | grep "Value error"

# legacy spelling: accepted, with deprecation warnings, and the allow-list derived from the URL
env -u TESTING -u ENVIRONMENT APP_ENV=prod SECRET_KEY=$K FIRST_SUPERUSER_PASSWORD=Str0ngPassw0rd \
  PUBLIC_BASE_URL=https://controlvar.example.com \
  python -W always -c "from backend.app.core.config import settings; print(settings.ENVIRONMENT, settings.is_production, settings.effective_allowed_hosts)"
# ... DeprecationWarning: ENVIRONMENT='prod' is deprecated; use ENVIRONMENT='production' instead.
# production True ['controlvar.example.com']
```

Then evaluate the rate-limit table as a function, the way its tests do:

```bash
ENVIRONMENT=development python -c "
from backend.app.middleware.rate_limiter import resolve_rate_limit as r
for p in ['/api/v1/auth/login','/api/v1/users/me/password','/api/v1/tracking/batch',
          '/api/v1/sdk/ruleset','/api/v1/export/experiments','/api/v1/experiments/']: print(p, r(p))"
# /api/v1/auth/login (10, 60) ... /api/v1/sdk/ruleset (6000, 60) ... /api/v1/export/experiments (10, 60) ...
```

### Key takeaways

- `ENVIRONMENT` is canonical and must be set: the API will not serve, and the container will
  not start, without it. `APP_ENV`/`dev`/`prod` are accepted with warnings and mirrored back for
  legacy readers.
- One `Settings` base, three subclasses with different defaults, selected at import.
- Validators fail closed in staging and production: placeholder `SECRET_KEY`, weak first-admin
  password, the bypass outside dev/test, no `PUBLIC_BASE_URL`/`ALLOWED_HOSTS`, and `TESTING`
  itself. Nothing relaxes them. Module secrets (`AUDIT_HMAC_KEY` and friends) are validated by
  the modules.
- The rules live in `settings_rules.py`, so the container's preflight lists every problem at
  once (exit 78) with exactly the settings' own rules.
- CORS origins are normalised, configured or empty in staging/production; rate limits are
  strict on auth, 300/min default, one shared 10/min budget for exports, 60/min for batch
  assignment, and `SDK_RATE_LIMIT_PER_MINUTE` (6000) on the other SDK paths — resolved by a pure function, charged to the
  client uvicorn resolved.

### Q&A

**Q. Why keep both `DATABASE_URI` and `SQLALCHEMY_DATABASE_URI`?**
History and layering: `DATABASE_URI` is what operators and CI set; `SQLALCHEMY_DATABASE_URI`
is what `db/session.py` reads, and it falls back to `DATABASE_URI`, then to the components.
`db/session.py` additionally honours a raw `DATABASE_URI` environment variable first, which is
how the test harness points the engine at `experimentation_test_<pid>`.

**Q. How do the tests build production settings, then?**
They don't need a relaxation: a test that wants production behaviour constructs `ProdSettings`
with real-looking values (a 64-character key, a strong password, a `PUBLIC_BASE_URL`), and a
test of the refusals constructs it without them and asserts the error. Both are what a real
deployment would see. There is no production behaviour that depends on the bypass being on,
so nothing in the suite ever asks for it outside development or test.

**Q. Where do the scheduler cadences live and why are they minutes?**
`SAFETY_CHECK_INTERVAL_MINUTES`, `ROLLOUT_CHECK_INTERVAL_MINUTES`,
`BANDIT_UPDATE_INTERVAL_MINUTES` and `EXPERIMENT_SCHEDULER_INTERVAL_MINUTES` (default 15,
refused below 1, which would spin the loop) in `Settings`; the lifespan applies the last one to
the experiment scheduler before it starts. The metrics scheduler keeps its constructor default
of 15. Minutes because the work is batch-shaped and the locks are advisory (Day 17); the demos
set them to 1 so a rollout story plays out in a demo's timescale.

**Q. A staging deployment's dashboard is on `app.example.com` and the API on
`api.example.com`. What do I set?**
`PUBLIC_BASE_URL=https://api.example.com` (the API's own URL, so its hostname is allowed) and
`DASHBOARD_ORIGINS=https://app.example.com` (added to the CORS allow-list and accepted as an SSO
return address). Without the second, the settings log a warning at start-up: SSO sign-ins from
the dashboard would be refused, and so would the browser's API calls.

---

## Day 10 — Authentication: local JWTs, Cognito, API keys, and the dev bypass

### What & why

Three kinds of caller, three credentials:

| Caller                     | Credential                          | Resolved by                | Identity produced                                        |
| -------------------------- | ----------------------------------- | -------------------------- | -------------------------------------------------------- |
| Dashboard user (local)     | `Authorization: Bearer <local JWT>` | `deps.get_current_user`    | a `User` row (by id)                                     |
| Dashboard user (Cognito)   | `Authorization: Bearer <Cognito>`   | `deps.get_current_user`    | a `User` row (by `external_id = "cognito:<sub>"`)        |
| SDK / integration          | `X-API-Key: eptk_…`                 | `deps.get_api_key`         | the key's owning `User`                                  |

Which of the first two a deployment uses is one setting, `AUTH_PROVIDER` (`local` or `cognito`).
Both are part of the core. A full build adds a third way into the dashboard: the `sso` module's
sign-in through an OpenID Connect provider (`/api/v1/auth/sso/login`, then
`/api/v1/auth/sso/exchange`, which mints the same local access token a password login does).

Plus one non-credential for development: the **dev bypass**, which makes
`get_current_user` return a synthetic `dev-admin` superuser without looking at any header —
only when `settings.dev_auth_bypass_active` (bypass on _and_ environment is dev/test).

#### Why two providers, and why the local one is the default

The platform was built on AWS Cognito: users sign in against a user pool, groups map to roles,
the API validates tokens with AWS. That's the right shape for a deployment that already lives
in AWS and the wrong shape for one that has to run with `docker compose up` and no AWS
account. So `AUTH_PROVIDER=local` was added and made the default: email + bcrypt password
against `users.hashed_password`, issuing HS256 JWTs signed with `SECRET_KEY`.
`get_current_user` dispatches on the setting.

The Cognito path was rewritten in 0.17.0 around one rule: **a Cognito identity is linked to an
account by its Cognito user ID, never by a name or an address.** A username or email that
happens to match an existing row is refused, not adopted — otherwise anyone who could register
`admin@yourcompany.com` in the pool would sign in as your administrator.

#### Why API keys are hashed, and why they're SHA-256 rather than bcrypt

An API key is looked up on _every_ SDK request. bcrypt is deliberately slow (12 rounds ≈
250 ms), which is right for a password checked once per login and ruinous at 6,000 requests a
minute. SHA-256 is fast and one-way, which is all a random 128-bit key needs (there's nothing
to brute-force). So keys are stored as their SHA-256 hex digest, the plaintext is shown once
at creation, and lookup is `APIKey.key == sha256(header)`.

### How Controlvar does it

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
`alg: none` is rejected. `issuer=` pins `experimently-local` (a name the rename to Controlvar left alone) so a JWT
from some other service that happens to share the secret can't be replayed. `options={"require": [...]}`
makes `sub`/`exp`/`iat`/`iss` mandatory so a truncated token can't authenticate as nobody.

The dispatcher:

```python
# 📁 backend/app/api/deps.py
def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """
    Get the current authenticated user from the provided bearer token.

    Resolution order:

    1. Dev-admin bypass (``DEV_AUTH_BYPASS=true`` **and** ``ENVIRONMENT`` in
       development/test): returns the synthetic ``dev-admin`` superuser.
    2. No token: 401.
    3. ``AUTH_PROVIDER=local``: decode the HS256 JWT issued by
       ``/api/v1/auth/login`` and load the user by id (401 on any failure,
       400 if the account is inactive).
    4. ``AUTH_PROVIDER=cognito``: validate the token with Cognito, then find
       the account linked to the Cognito user ID, or create it on a first
       sign-in (``services.cognito_accounts.resolve_cognito_user``). Every
       refusal is the same 401; its reason goes to the
       ``backend.app.auth.cognito_sign_in`` logger.
    ...
    """
    if dev_auth_bypass_active():
        try:
            return _get_or_create_dev_user(db)
        except Exception as dev_err:
            raise unexpected_failure(
                dev_err,
                "Dev user creation",
                "Could not sign in the development user",
                db=db,
            )

    if not token:
        raise _credentials_exception("Not authenticated")

    if settings.AUTH_PROVIDER == "local":
        return _authenticate_local_token(token, db)

    try:
        user_data = auth_service.get_user_with_groups(token)
    except CognitoTokenRefused as refusal:
        log_token_refused(refusal, "sign-in")
        raise _credentials_exception()
    except Exception as e:
        # The type only: the message can carry what the caller sent.
        logger.error(f"Cognito token not accepted: {type(e).__name__}")
        raise _credentials_exception()

    try:
        return resolve_cognito_user(db, user_data)
    except CognitoSignInRefused as refusal:
        db.rollback()
        log_sign_in_refused(refusal)
        raise _credentials_exception()
    except Exception as e:
        db.rollback()
        logger.error(f"Cognito sign-in failed: {type(e).__name__}")
        raise _credentials_exception()
```

Two things to notice. **Every refusal is the same 401** — a caller cannot tell an expired token
from an unlinked account from a missing email — and the _reason_ goes to a named logger
(`backend.app.auth.cognito_sign_in`) that an operator can read. And the exception's message is
never logged, only its type: the message can carry what the caller sent.

`resolve_cognito_user` (in `backend/app/services/cognito_accounts.py`) is a decision table,
evaluated in order and decided before anything is written:

- a row already has `external_id = "cognito:<sub>"` → that row; its role follows the token's
  groups when `SYNC_ROLES_ON_LOGIN` is on;
- a row with the same username exists but is not linked — it has a local password, or is linked
  to another identity, or is a legacy row with neither → **refused**;
- the identity has no email address, or another row holds the address in any letter case →
  **refused**;
- otherwise → create the row (**just-in-time provisioning**), role from the Cognito groups
  (`Admins → admin`, `Developers → developer`, …; membership in `COGNITO_ADMIN_GROUPS` also sets
  `is_superuser`).

So JIT provisioning creates an account only for an identity nobody has seen. An account that
existed before its first Cognito sign-in is used only once an administrator has linked it by
setting its `external_id`. The rest of the 0.17.0 Cognito contract lives in the token check
(`auth_service`): both `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` are required, a token
issued for another app client is refused, and self sign-up (`/auth/signup`, `/auth/confirm`)
answers 404 unless `COGNITO_SELF_SIGNUP_ENABLED=true` — and always under `local`.

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
answer 423 with `Retry-After`. A successful sign-in also writes a `user_login` audit entry that
records the provider and nothing more, and, like every audit write in this codebase, a failure
to write it is logged and does not fail the sign-in. The tracker is in-process memory (per worker), which is one
more reason the platform runs one worker per container. A signed-in user changes their own
password with `POST /api/v1/users/me/password`, which checks the current one, is limited to
5/min, and counts its failures toward the same lockout.

And the API-key path:

```python
# 📁 backend/app/api/deps.py
def get_api_key(
    db: Session = Depends(get_db), api_key_header: str = Depends(API_KEY_HEADER)
) -> Optional[User]:
    ...
    if not api_key_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key missing",
            headers={"WWW-Authenticate": "APIKey"},
        )

    # Look up by hashed key only. API keys must never be stored as plaintext.
    api_key_hash = hash_api_key(api_key_header)
    api_key = db.query(APIKey).filter(APIKey.key == api_key_hash).first()
    if not api_key or not api_key.is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "APIKey"},
        )

    # Get associated user
    user = db.query(User).filter(User.id == api_key.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "APIKey"},
        )

    # Issue #198: record the use, throttled inside update_last_used. A failure
    # here is bookkeeping, not authentication, so it never fails the request.
    try:
        api_key.update_last_used(db)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning(f"Could not record API key use for key {api_key.id}: {exc}")

    # Hold a connection for the rest of the request whether or not the use was recorded.
    db.connection()

    return user
```

`is_valid` is `is_active and not is_expired`. Keys are minted by `APIKey.create_for_user`
(`eptk_` + 32 hex chars from `secrets.token_hex(16)`), which returns `(model, plaintext)` —
and `POST /api-keys` is the only place the plaintext is ever shown. The compose demo bakes
fixed keys into the demo images (`eptk_demo_shoplab_…`) so the storefront works without a
manual step; in every other case the key comes from the dashboard's Admin → API keys page.

#### Key scopes: one is enforced

`api_keys.scopes` is a comma-separated string, and every reader goes through one parser,
`backend/app/core/api_key_scopes.py`: split on commas, strip, drop empties, then **exact,
case-sensitive** membership (`"read, sdk:ruleset "` grants `sdk:ruleset`; `"SDK:RULESET"` and
`"sdk:ruleset-ro"` do not). Exactly one scope name is enforced today, **`sdk:ruleset`**: it
admits a key to the server-side surface: `GET /api/v1/sdk/ruleset`,
`POST /api/v1/tracking/evaluations` and `POST /api/v1/tracking/assign/batch`. Every other scope name is a label.

```python
# 📁 backend/app/api/sdk_scope.py
def require_sdk_ruleset_key(
    db: Session = Depends(deps.get_db),
    api_key_header: str = Depends(deps.API_KEY_HEADER),
    _owner: User = Depends(deps.get_api_key),
) -> APIKey:
    """Return the presented ``APIKey`` row if it carries ``sdk:ruleset``."""
    ...
    refusal = scope_refusal(db, api_key)
    if refusal is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=refusal)
    return api_key
```

with the decision itself pulled out into a plain function, so another caller can ask the same
question without raising:

```python
# 📁 backend/app/api/sdk_scope.py
def scope_refusal(db: Session, api_key: APIKey) -> Optional[str]:
    """Why ``api_key`` may not act as a server-side key, or ``None`` if it may.

    The scope first (:data:`MISSING_SCOPE_DETAIL`), then its owner, read from
    the database by the key's own ``user_id`` (:data:`OWNER_ROLE_DETAIL`:
    missing, inactive, or unable to change feature flags).
    """
    if not has_scope(api_key.scopes, SDK_RULESET_SCOPE):
        return MISSING_SCOPE_DETAIL
    owner = db.query(User).filter(User.id == api_key.user_id).first()
    if (
        owner is None
        or not owner.is_active
        or not check_permission(owner, ResourceType.FEATURE_FLAG, Action.UPDATE)
    ):
        return OWNER_ROLE_DETAIL
    return None
```

It layers on `get_api_key` (401 for a missing or unknown key), then decides on the _presented_
key itself — another key of the same user carrying the scope grants nothing — and answers 403
without the scope. The ruleset is every flag's full targeting, so the scope works only while the
key's owner can change flags (ADMIN, DEVELOPER or a superuser), checked on every request so it
follows role changes; and `POST /api-keys` refuses to create an `sdk:ruleset` key for anyone
else. The same rule fits batch assignment, because ADMIN, DEVELOPER and superusers are exactly
who can change experiments too (a test pins the two role lists equal), and the 403 texts are
the same on every route.

The extracted `scope_refusal` has a second caller worth knowing about:
`presented_key_is_server_key` applies the same rules but refuses nothing, and the error-report
routes (`POST /tracking/errors` and `/errors/batch`) use it. They accept **any** valid key (neither route answers 403), but the key decides how the report
is stored and whether it counts: a server-side key (the `sdk:ruleset` scope, owner able to change
flags) stores `source = "server"` and the report counts toward the flag's error rate and so
toward automatic rollback (Day 18); any other key, such as the one in a browser or mobile app,
stores `"client"`, and safety monitoring does not count it. An app whose errors should count
sends them to its own server, which forwards them with a server-side key.

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

Now the scope. Your `week2` key has none, so the ruleset refuses it with 403; a key created
with the scope (as the admin, who can change flags) is admitted:

```bash
PLAIN=$(curl -s -X POST $API/api-keys -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"plain"}' | python -c 'import sys,json; print(json.load(sys.stdin)["key"])')
SCOPED=$(curl -s -X POST $API/api-keys -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"server","scopes":["sdk:ruleset"]}' | python -c 'import sys,json; print(json.load(sys.stdin)["key"])')
curl -s -o /dev/null -w "%{http_code}\n" $API/sdk/ruleset -H "X-API-Key: $PLAIN"    # 403
curl -s -o /dev/null -w "%{http_code}\n" $API/sdk/ruleset -H "X-API-Key: $SCOPED"   # 200
```

Then find the SHA-256 of your new key in the database — and note that the plaintext is
nowhere:

```bash
docker compose exec postgres psql -U postgres -d experimentation -c "select name, left(key, 12) as key_hash_prefix, scopes, is_active from experimentation.api_keys order by created_at desc limit 3"
```

### Key takeaways

- Three credentials: local JWT (HS256, `iss=experimently-local`, 12 h), Cognito token
  (validated with AWS, linked by `external_id = "cognito:<sub>"`, JIT-provisioned only for an
  identity nobody has seen), API key (SHA-256 hashed, `eptk_` prefix, shown once). The `sso`
  module adds OIDC sign-in to the dashboard in a full build.
- `get_current_user` dispatches on `AUTH_PROVIDER` after checking the dev bypass; every Cognito
  refusal is the same 401 with its reason in a named log. `get_api_key` is a separate
  dependency for SDK routes.
- Passwords are bcrypt (12 rounds); keys are SHA-256 — slow where brute force matters, fast
  where it doesn't.
- Login lockout is 10 failures / 15 min → 423 + `Retry-After`; the message never reveals
  which of address/password/status was wrong.
- One scope is enforced, `sdk:ruleset`, on the server-side SDK routes (the ruleset, the local
  evaluation counts, batch assignment), and only while the key's owner can change flags; it also
  decides whether an error report counts toward rollback. The dev bypass exists only when `DEV_AUTH_BYPASS=true` _and_
  `ENVIRONMENT ∈ {development, test}`; staging and production refuse to start with it.

### Q&A

**Q. Why does `oauth2_scheme` have `auto_error=False`?**
So a missing `Authorization` header reaches `get_current_user` instead of being rejected by
FastAPI's security machinery. `get_current_user` needs to see "no token" to decide between the
dev bypass (return `dev-admin`) and a 401.

**Q. Can an API key call `/experiments/`?**
No. Those endpoints depend on `get_current_active_user` (bearer). SDK routes depend on
`get_api_key`. A key reaches the tracking, evaluate and error-reporting paths because of _which
dependency the endpoint declares_, not because of a scope string; the one scope that is
enforced, `sdk:ruleset`, is an extra gate on top of that for the server-side SDK routes.

**Q. How do I log in on the dashboard with Cognito locally?**
You mostly don't; the local provider is the development default. The Cognito path is tested
with `moto`'s `mock_cognitoidp` in `backend/tests/integration/auth/` (its own conftest selects
`AUTH_PROVIDER=cognito`), and the `Cognito Integration Tests` workflow runs them in CI.

**Q. We moved from `local` to `cognito` and our existing users can't sign in. Why?**
Because their rows were never linked. A local account has a password, and a matching username
or email is deliberately not enough to adopt it. An administrator links each account by setting
its `external_id` to `cognito:<sub>` (the app's `docs/cognito_integration.md` has the steps);
until then the sign-in is refused with the usual 401 and the reason `local_password` in the
sign-in log.

**Q. The token is stored in `localStorage` on the dashboard. Isn't that an XSS risk?**
It's a known trade. The dashboard is an internal tool behind login, the API sets a strict
`Content-Security-Policy: default-src 'none'`, and the token is short-lived. `AuthContext`
never trusts the stored token blindly — it re-derives the user from `/auth/me` on every
mount, so a revoked token can't resurrect a session.

---

## Day 11 — Authorization: RBAC, ownership, and the permission dependencies

### What & why

Authentication answered _who_. Authorization answers _may they_. Controlvar's model is one
table and a trump card:

1. **Role × resource × action.** Four roles (`ADMIN`, `DEVELOPER`, `ANALYST`, `VIEWER`), seven
   resource types (experiment, feature flag, user, role, permission, report, and `API_KEY`,
   which governs acting on _other_ users' keys), five actions, one static table.
2. **Superuser** trumps everything — `check_permission` returns `True` before looking at the
   table.

What is _not_ in the model is the most important thing about it: **access is by role, not by
ownership.** For feature flags and for experiments alike:

- **ADMIN and DEVELOPER** read, create, change and delete _any_ flag and _any_ experiment —
  including scheduling, starting, pausing, completing, archiving and deleting one.
- **ANALYST and VIEWER** read any of them and change none — **not even one they created.**
- The creator is recorded as `owner_id` and shown in the dashboard. It is not an access rule.

The old rule was "may update by role, _or_ owns it". The second half gave write access to
roles the table says are read-only: a viewer or analyst who had created a flag or an experiment
— before a role change, say — could still change it, so the role no longer described what a
person could do. A deployment is single tenant, and it wants the team's roles to decide.
(Isolating teams from each other is the `workspaces` module's job, keyed on membership — not
on who happened to create a row.)

The table is **code, not data**. Custom roles and direct grants exist in the optional `rbac`
module (under `/api/v1/rbac`, in a full build), but the base matrix is a Python dict you can
read in ten seconds and that can't be misconfigured by a migration.

#### Why "READ before UPDATE"

The checks run READ first, then UPDATE/DELETE. A user who can't even read a resource should get
"you can't view experiments", not "you can't update experiments" — the second message confirms
the resource exists and leaks which operation was attempted. With all four roles holding READ
on experiments and flags this rarely fires today, but the order is kept because the table can
change and the message must stay right when it does.

### How Controlvar does it

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
        ResourceType.API_KEY: [Action.READ],
        ResourceType.EXPERIMENT: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
        ResourceType.FEATURE_FLAG: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
        ResourceType.USER: [Action.READ, Action.LIST],
        ...
        ResourceType.REPORT: [Action.READ, Action.LIST],
    },
    UserRole.ANALYST: {
        ResourceType.API_KEY: [Action.READ],
        ResourceType.EXPERIMENT: [Action.READ, Action.LIST],
        ResourceType.FEATURE_FLAG: [Action.READ, Action.LIST],
        ...
        ResourceType.REPORT: [Action.CREATE, Action.READ, Action.UPDATE, Action.DELETE, Action.LIST],
    },
    UserRole.VIEWER: {
        ResourceType.API_KEY: [Action.READ],
        ResourceType.EXPERIMENT: [Action.READ, Action.LIST],
        ResourceType.FEATURE_FLAG: [Action.READ, Action.LIST],
        ...
    },
}


def check_permission(user: Any, resource: ResourceType, action: Action) -> bool:
    # Superusers always have all permissions
    if hasattr(user, "is_superuser") and user.is_superuser:
        return True

    # Get user's role
    try:
        role = getattr(user, "role", UserRole.VIEWER)
    except (AttributeError, TypeError):
        # Default to Viewer role if there's any issue
        role = UserRole.VIEWER

    # Check role permissions
    return has_permission(role, resource, action)
```

The interesting cell is `ANALYST × REPORT`: analysts can't touch experiments but have full
control of reports — the role is "reads everything, writes analyses". The `VIEWER` default in
`check_permission` is deliberate: an object with no `role` attribute (a mock, a malformed
row) gets the least privilege, not an exception.

#### Feature flags: one decision

Every route that changes a flag asks one function:

```python
# 📁 backend/app/core/permissions.py
FEATURE_FLAG_SCOPE: Dict[UserRole, Dict[Action, Scope]] = {
    UserRole.ADMIN: {
        Action.READ: Scope.ANY,
        Action.UPDATE: Scope.ANY,
        Action.DELETE: Scope.ANY,
    },
    UserRole.DEVELOPER: {
        Action.READ: Scope.ANY,
        Action.UPDATE: Scope.ANY,
        Action.DELETE: Scope.ANY,
    },
    UserRole.ANALYST: {Action.READ: Scope.ANY},
    UserRole.VIEWER: {Action.READ: Scope.ANY},
}


def can_act_on_feature_flag(
    user: Any,
    owner_id: Optional[Any],
    action: Action,
    scope_table: Optional[Dict[UserRole, Dict[Action, Scope]]] = None,
) -> bool:
    """Whether *user* may take *action* on a feature flag owned by *owner_id*.

    The one decision for every per-flag route.  A superuser may always act.
    Otherwise the role must hold *action* on feature flags in ROLE_PERMISSIONS --
    that check comes first, so owning a flag never lets a read-only role change
    it -- and then the scope table decides whose flags the permission covers.
    ...
    """
    if getattr(user, "is_superuser", False):
        return True
    if not check_permission(user, ResourceType.FEATURE_FLAG, action):
        return False
    role = getattr(user, "role", UserRole.VIEWER)
    table = FEATURE_FLAG_SCOPE if scope_table is None else scope_table
    scope = table.get(role, {}).get(action, Scope.OWN)
    if scope is Scope.ANY:
        return True
    return owner_id is not None and str(owner_id) == str(getattr(user, "id", None))
```

The role matrix is checked **first**, so owning a flag never lets a read-only role change it.
Then `FEATURE_FLAG_SCOPE` says whose flags the permission covers — `ANY` for every role today.
The table exists for the day someone wants per-flag restrictions (a flag locked to its owner,
an approval step); that is where they would go, not into `ROLE_PERMISSIONS`. A route calls it
inline, against the flag it loaded:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
    flag = db.query(FeatureFlag).filter(FeatureFlag.id == flag_id).first()
    if not flag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Feature flag not found"
        )

    # Check access permission
    if not can_act_on_feature_flag(current_user, flag.owner_id, Action.UPDATE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to update this feature flag",
        )
```

The per-flag routes (get, update, delete, activate, deactivate, unarchive, toggle, enable,
disable), bulk toggle, and every rollout-schedule and stage change (against the schedule's flag)
all go through it. Creating a flag is gated by the async dependency `deps.can_create_feature_flag`.
What keeps this complete is a test, not discipline: `backend/tests/smoke/test_flag_change_routes_guarded.py`
pins the exact set of mutating routes under `/feature-flags`, `/rollout-schedules` and `/safety`,
with what guards each one, and a new mutating route fails it until someone classifies it.

> **Watch Out!** `deps.py` still contains `get_feature_flag_access`, `can_update_feature_flag` and
> `can_delete_feature_flag`, built on the old "may update _or_ owns" rule. **No route uses them.**
> Do not build on them, and do not read them as the mechanism.

#### Experiments: read access and change access

Experiment authorisation is two plain, synchronous functions, called from the endpoint once the
experiment is loaded:

```python
# 📁 backend/app/api/deps.py
def get_experiment_read_access(
    experiment: Union[Experiment, Dict[str, Any]],
    current_user: User = Depends(get_current_active_user),
) -> Union[Experiment, Dict[str, Any]]:
    """
    Admit a caller who may read this experiment and its results.

    A superuser, or a user whose role holds ``Action.READ`` on experiments.
    Who created the experiment is not considered.
    ...
    """
    if current_user.is_superuser:
        return experiment

    if not check_permission(current_user, ResourceType.EXPERIMENT, Action.READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=get_permission_error_message(ResourceType.EXPERIMENT, Action.READ),
        )

    return experiment


def get_experiment_change_access(
    experiment: Union[Experiment, Dict[str, Any]],
    current_user: User,
    action: Action = Action.UPDATE,
) -> Union[Experiment, Dict[str, Any]]:
    """
    Admit a caller who may change this experiment.

    A superuser, or a user whose role holds both ``Action.READ`` and
    ``action`` on experiments. Who created the experiment is not considered,
    so a role without ``action`` is refused on an experiment it created too.

    Used by update, schedule, start, pause, complete, archive and metadata,
    and by delete with ``Action.DELETE``.
    ...
    """
    get_experiment_read_access(experiment, current_user)

    if current_user.is_superuser:
        return experiment

    if not check_permission(current_user, ResourceType.EXPERIMENT, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=get_permission_error_message(ResourceType.EXPERIMENT, action),
        )

    return experiment
```

READ before the action, by construction: `get_experiment_change_access` calls
`get_experiment_read_access` first. Creating an experiment is a `check_permission(...,
Action.CREATE)` in the endpoint; listing is `Action.LIST`, which all four roles hold — and every
caller who may list sees **every** experiment, not just their own.

Authorisation is the first gate, not the only one. Once the role is admitted, the experiment's
**state** decides what may change, and a refusal there is a **400** that names the state:

- `PUT /experiments/{id}` changes a DRAFT experiment. On any other status a non-superuser is
  refused with 400 (`"Cannot update experiments in active status"`), except for targeting sent
  _on its own_ to a PAUSED experiment. Variants, metrics and dates change only in DRAFT.
- `status` never changes through `PUT`. Sending the current status is accepted (so a GET body
  can be sent back); any other value is a 422. Status changes go through
  `POST /experiments/{id}/start`, `/pause`, `/complete` and `/archive`, which carry each
  transition's checks. A `schedule` in a `PUT` body is a 422 too: scheduling is
  `PUT /experiments/{id}/schedule`.
- `DELETE /experiments/{id}` requires DELETE on experiments (403 otherwise) and a DRAFT
  experiment: anything else answers **400**, "Cannot delete experiments that are not in DRAFT
  status". Archive a running one instead.

The order is deliberate, and the route's own documentation states it: role first (403), state
second (400). A viewer is told they may not change experiments before they are told anything
about this experiment's state.

#### What happened to the delete-experiment exception

Delete used to be the odd one out: it did its checks inline, because the shared experiment
dependency demanded an ACTIVE experiment while delete demanded a DRAFT one. That conflict is
gone. Delete now goes through the same `get_experiment_change_access` as
every other change, with `Action.DELETE`:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
async def delete_experiment(
    experiment_id: UUID = Path(..., description="The ID of the experiment to delete"),
    experiment_key: str = Query(
        ...,
        description="Key or ID of the experiment to delete (must match experiment_id)",
    ),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    cache_control: Dict[str, Any] = Depends(deps.get_cache_control),
) -> None:
    ...
    experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
    if not experiment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
        )

    # Verify experiment_key matches experiment_id
    if str(experiment.id) != experiment_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="experiment_key does not match experiment_id",
        )

    # READ and DELETE on experiments; who created it is not considered
    experiment = deps.get_experiment_change_access(
        experiment, current_user, Action.DELETE
    )

    # Additional check for experiment status for non-draft experiments
    if experiment.status != ExperimentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete experiments that are not in DRAFT status",
        )
```

The one quirk left is the "are you sure" parameter: despite its description, `experiment_key`
must equal the experiment's **id** string, not its public key, or the request is a 400.

> **By the Way.** Superuser and `ADMIN` role are different bits. Cognito JIT provisioning sets
> both for members of `COGNITO_ADMIN_GROUPS`; the bootstrap's first user gets both; the local
> `users` table can have an `ADMIN` who isn't a superuser. `check_permission` short-circuits
> on `is_superuser`; `get_current_superuser` (used by every `/admin/*` route and the safety
> settings) checks the bit only, so the ADMIN role alone does not open the admin area. A
> superuser also passes the experiment state guard on `PUT`. When a "why can this admin not
> do X" ticket arrives, check both.

### Build It

Prove the matrix and the flag rule from a shell — ownership changes nothing:

```bash
ENVIRONMENT=development python -c "
import uuid
from types import SimpleNamespace as U
from backend.app.core.permissions import can_act_on_feature_flag, has_permission, ResourceType as R, Action as A
from backend.app.models.user import UserRole as Role
me = uuid.uuid4()
for role in Role:
    u = U(id=me, role=role, is_superuser=False)
    print(f'{role.value:10s} own flag: {can_act_on_feature_flag(u, me, A.UPDATE)!s:5s}  '
          f'someone else\'s: {can_act_on_feature_flag(u, uuid.uuid4(), A.UPDATE)}')
print('analyst report create', has_permission(Role.ANALYST, R.REPORT, A.CREATE))" 2>/dev/null
# admin      own flag: True   someone else's: True
# analyst    own flag: False  someone else's: False
# developer  own flag: True   someone else's: True
# viewer     own flag: False  someone else's: False
# analyst report create True
```

Then from the API. Create a viewer on the Admin → Users page (or via `POST /users` as admin),
log in as them, and call `POST /experiments/` — expect 403, "You don't have permission to create
experiments". As the admin, start an experiment and try to delete it — expect 400, then archive
it instead. Then read the tests that pin all of this:

- `backend/tests/unit/core/test_permissions.py` — the matrix;
- `backend/tests/unit/core/test_feature_flag_access_rule.py` — `can_act_on_feature_flag`,
  including `test_the_role_matrix_is_checked_before_ownership`;
- `backend/tests/integration/api/test_flag_change_routes_access.py` and
  `backend/tests/integration/api/test_experiment_access_by_role.py` — every role against the
  real routes, with non-superusers (superuser fixtures bypass every check, which is why they
  prove nothing here);
- `backend/tests/smoke/test_flag_change_routes_guarded.py` — the inventory of mutating flag
  routes.

### Key takeaways

- Four roles × seven resources × five actions in one dict; superuser bypasses it; missing role
  → `VIEWER`.
- Access is by role, never by ownership: ADMIN and DEVELOPER change any flag and any
  experiment; ANALYST and VIEWER change none, including their own. The owner is recorded and
  shown.
- Flags: `can_act_on_feature_flag(user, owner_id, action)` is the one decision, on every
  mutating route, pinned by a smoke test. Experiments: `get_experiment_read_access` and
  `get_experiment_change_access(experiment, user, action)`.
- Role first (403), then state (400): a non-draft update or delete is a 400; `status` changes only
  through start/pause/complete/archive.
- Experiment deps are sync functions; feature-flag create and report deps are `async` and
  `Depends`-ed — don't mix.

### Q&A

**Q. Where do custom roles and direct grants fit?**
In the optional `rbac` module: `/api/v1/rbac` adds custom roles, direct permission grants and an
"effective permissions" resolver on top of the static matrix, with its tables created by the
`modules` migration branch (Day 13). A core build has no `/rbac` at all. The matrix is the floor;
custom roles extend it. Most deployments never touch it.

**Q. Then why does `check_ownership` still exist?**
Only two helpers in `deps.py` still call it: the unused flag helpers above, and
`get_report_access`, which keeps the older "UPDATE by role _or_ owns it" shape for reports (and
which no route uses today either). It
compares `str(a) == str(b)` because `owner_id` is a UUID column and tests and some paths hand
over strings. Neither is part of the flag or experiment rule.

**Q. An analyst opens the experiments list. What do they see?**
Everything. All four roles hold `Action.LIST`, and the list is not filtered by owner. The
endpoint used to show non-superusers only the experiments they created, which hid the whole
platform from analysts — the role that exists to read it.

**Q. Workspaces have their own roles. How do they interact with this?**
Workspaces are an optional module. Its membership roles scope access _within_ a workspace and
are checked by the workspace service; experiments and flags carry a nullable `workspace_id`
that the core never filters on. In a core build there are no workspaces, and the global matrix
is the whole story.

---

## Day 12 — The SQLAlchemy models and the PostgreSQL schema

### What & why

Day 3 introduced the five core tables. Today is about the _mechanics_ that make every one of
the model classes (40 in the core, more in the modules) behave the same way: the declarative
base with a schema, the mixin that adds ids and timestamps, foreign keys that must name the
schema explicitly, `JSONB` for the flexible parts, relationships with cascades, the
index/constraint tuple every model returns from `__table_args__` — and the seam that keeps a
module's tables out of the core.

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
Pydantic layer validates the shape at the API boundary instead (and for flag targeting, refuses
unknown keys with a 422). The rule of thumb in this codebase: **relational for anything you
join or aggregate on, JSONB for configuration.**

### How Controlvar does it

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

A model with relationships in both directions, a one-to-one, a check constraint, a composite
index, a server default and the modules seam — the feature flag is the best single example:

```python
# 📁 backend/app/models/feature_flag.py
class FeatureFlag(Base, BaseModel):
    """Feature flag model for feature toggles."""

    __tablename__ = "feature_flags"

    key = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    status = Column(
        Enum(FeatureFlagStatus),
        default=FeatureFlagStatus.INACTIVE,
        nullable=False,
        index=True,
    )
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{get_schema_name()}.users.id", ondelete="SET NULL"),
    )
    targeting_rules = Column(JSONB)  # Rules for flag enablement
    rollout_percentage = Column(Integer, default=0, nullable=False)  # Gradual rollout
    default_value = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    variants = Column(JSONB)  # For multivariate flags
    tags = Column(JSONB)  # For categorization

    # The seam: a bare indexed UUID, not a ForeignKey. `workspaces` is the
    # workspaces module's table ...
    workspace_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # Relationships
    owner = relationship("User", back_populates="feature_flags")
    overrides = relationship(
        "FeatureFlagOverride",
        back_populates="feature_flag",
        cascade="all, delete-orphan",
    )
    # Deleting a flag keeps its events (#855). The events.feature_flag_id FK
    # is ON DELETE SET NULL; passive_deletes leaves that to the database, so
    # the ORM neither deletes the events nor loads them to clear the column.
    events = relationship("Event", back_populates="feature_flag", passive_deletes=True)
    reports = relationship(
        "Report", back_populates="feature_flag", cascade="all, delete"
    )
    ...
    safety_config = relationship(
        "FeatureFlagSafetyConfig",
        back_populates="feature_flag",
        cascade="all, delete-orphan",
        uselist=False,  # One-to-one relationship
    )

    @declared_attr
    def __table_args__(cls):
        schema_name = get_schema_name()
        return (
            # Composite index for owner + status
            Index(f"{schema_name}_feature_flag_owner_status", "owner_id", "status"),
            # Check that rollout percentage is between 0 and 100
            CheckConstraint(
                "rollout_percentage >= 0 AND rollout_percentage <= 100",
                name="check_rollout_percentage",
            ),
            {"schema": schema_name},
        )
```

Points to notice:

- **`relationship(..., back_populates=...)` on both sides.** Every relationship is declared on
  both models with matching `back_populates`, never `backref`. It's more typing and it means
  each model file is self-describing. `CLAUDE.md`'s "watch for duplicate SQLAlchemy
  relationship definitions" refers to the failure mode where a relationship is declared twice
  on one side; `Variant.assignments` once was, and is now declared once.
- **`ondelete` differs by intent.** `CASCADE` for children that mean nothing without the
  parent (variants, assignments, overrides). `SET NULL` for things that must survive — an
  event outlives its flag, a flag outlives its owner. For the flag-to-events side that intent
  was once contradicted by the ORM (`cascade="all, delete-orphan"` deleted a flag's events
  with it); the relationship is now `passive_deletes=True` with no cascade, so the database's
  `SET NULL` does the work and the events are kept. A relationship's cascade and a foreign
  key's `ondelete` are two separate mechanisms, and they have to agree.
- **`workspace_id` is a bare UUID, not a foreign key.** `workspaces` is the workspaces module's
  table; a `ForeignKey` to it here would be the one thing in the core ORM that reaches across
  the boundary, and importing the core without the module would raise
  `NoReferencedTableError`. The module's own model appends the constraint (with `use_alter`)
  when module models load. The core never reads the column.
- **`default_value` has a `server_default`.** The ORM `default=False` is enough for this
  release's inserts. The `server_default` is for the _previous_ release's image, which does not
  know the column: during a deploy, or after an image-only rollback, its inserts don't name it
  and the database fills it. Without it those inserts fail on `NOT NULL`. That is what
  "backwards-compatible" means in practice (Day 13).
- **Index names are schema-prefixed** (`f"{schema_name}_feature_flag_owner_status"`) because
  index names are global within a PostgreSQL schema and the app and test schemas would
  otherwise collide when both live in one database during migration work.
- **Enums.** `Enum(FeatureFlagStatus)` creates a PostgreSQL enum type named after the class
  (`featureflagstatus`). Note `FeatureFlagStatus` values are upper-case (`"ACTIVE"`) while
  `ExperimentStatus` values are lower-case (`"active"`) — a historical mismatch that a
  migration (`fce74a1b62d3_update_enum_type.py`) papered over by adding lower-case values to
  the experiment enums. The flag code compares statuses by name (`flag_status_name()`) for
  exactly this reason.
- **A model can refuse a write.** The module ends with a SQLAlchemy `set` listener on
  `FeatureFlag.status`, `_an_archived_flag_is_not_turned_on`, that raises if any ORM writer moves
  an archived flag straight to ACTIVE; the only way out of ARCHIVED is `/unarchive`, which makes
  it INACTIVE. (A Core `UPDATE` or raw SQL never reaches a listener, which is why the routes
  refuse first.)

A functional index is the other pattern worth stealing. Email addresses belong to one account
whatever their letter case, so `users` carries a unique index on `lower(email)`:

```python
# 📁 backend/app/models/user.py
#: An address belongs to one account whatever its letter case (#343).
EMAIL_LOWER_INDEX = "ix_users_email_lower"


def _email_lower_index(cls) -> Index:
    """The unique index on ``lower(email)``, built once per table.
    ...
    """
    table = cls.__dict__.get("__table__")
    if table is not None:
        return next(i for i in table.indexes if i.name == EMAIL_LOWER_INDEX)
    return Index(EMAIL_LOWER_INDEX, func.lower(cls.email), unique=True)
```

Registering `Bob@x.com` when `bob@x.com` exists answers 409 "Email already registered", and the
migration that added the index refuses to run while two existing accounts differ only in case —
naming the account ids (never the addresses) rather than picking a winner.

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

#### Registering models: the core list and the seam

Which models exist is decided in one place. `backend/app/models/__init__.py` imports the core
models only, and names them:

```python
# 📁 backend/app/models/__init__.py
def register_core_models() -> Any:
    """Register every core model on ``Base.metadata``; returns ``Base``.

    Idempotent, and safe to call before or after anything else has imported a
    model module.  Callers that build a schema — ``db/bootstrap.py``, alembic's
    ``env.py``, ``backend/tests/conftest.py`` — call this rather than importing
    the package for its side effect, so that what they depend on is explicit.

    This registers core models *only*.  The modules' models arrive through
    ``backend.app.modules_loader.load_modules()``, which imports whatever the
    modules package registered with ``hooks.register_model_module()``.
    """
    for module_name in CORE_MODEL_MODULES:
        importlib.import_module(f"{__name__}.{module_name}")
    return Base
```

A new **core** model goes into `CORE_MODEL_MODULES`; a **module's** model is registered by the
modules package with `hooks.register_model_module()`. Get it wrong in one direction and
`create_all` silently keeps producing a module's tables in a core build; a smoke test
(`backend/tests/smoke/test_core_model_registry.py`) checks, in a fresh interpreter, that it does
not.

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
ENVIRONMENT=development python - <<'PY' 2>/dev/null
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
from backend.app.models import register_core_models
register_core_models()               # the core models only: no workspaces table, so no FK on workspace_id
from backend.app.models.feature_flag import FeatureFlag
print(CreateTable(FeatureFlag.__table__).compile(dialect=postgresql.dialect()))
PY
```

Look for `default_value BOOLEAN DEFAULT false NOT NULL`, the `check_rollout_percentage`
constraint, the `owner_id` foreign key with `ON DELETE SET NULL` — and a `workspace_id UUID` with
no foreign key at all. Then count relationships and confirm every one has a partner:

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
- The core never holds a foreign key into a module's table; core models are listed in
  `CORE_MODEL_MODULES`, module models arrive through `hooks.register_model_module()`.
- A new non-null column gets a `server_default`, so the previous release can still insert.
- The engine is tuned for Aurora (`pool_pre_ping`, keepalives, recycle); sessions are
  `autoflush=False`, so `flush()` is explicit.

### Q&A

**Q. Why `datetime.utcnow` (naive) rather than `datetime.now(timezone.utc)`?**
Legacy. Most columns, including the mixin's timestamps, are `DateTime` without `timezone=True`,
so PostgreSQL stores naive timestamps and returns naive ones. It works as long as everyone
treats them as UTC — and it is exactly what bit the rollout scheduler, which once compared a
naive stage date with an aware `datetime.now(timezone.utc)` and raised `TypeError` on every
tick. Newer columns are timezone-aware (the rollout schedule's dates, an experiment's
`resume_at`, the scheduler-run records), event times are normalised to UTC on the way in, and
the comparisons still go through an `_as_utc` helper so a naive value from an older column
cannot break them.

**Q. How do I add a relationship without the "not mapped" error?**
Declare it on both models with `back_populates`, use string class names, and make sure the
new model's module is in `CORE_MODEL_MODULES` (core) or registered with
`hooks.register_model_module()` (a module) — mappers are configured lazily, and a class that's
never imported can't be resolved by name. `db/bootstrap.py`, alembic's `env.py` and the test
conftest all call `register_core_models()`, so a model missing from the list is missing from
all three.

**Q. Do I ever write raw SQL?**
Rarely, and via `text()` with bound parameters. The results engine has a couple of `text()`
queries for daily aggregation (they use `CONVERSION_SQL_PREDICATE` from `event_matching`), and
the scheduler lock uses `pg_try_advisory_lock`. Never format identifiers into SQL strings;
`_ensure_schema` uses `CreateSchema` for exactly that reason.

---

## Day 13 — Alembic migrations and the bootstrap

### What & why

**Alembic** versions the database schema as a graph of Python revision scripts, each with an
`upgrade()` and a `downgrade()` and a `down_revision` pointer to its parent. `alembic upgrade
heads` applies everything the database hasn't seen (tracked in an `alembic_version` table);
`alembic revision --autogenerate` diffs the models against the database and writes a script
for you to review.

Controlvar has two twists, and both are deliberate.

**Twist one: two heads.** The optional modules own some tables (custom roles, the warehouse
analysis runs, the workspace foreign keys), and a core build has no `modules/` directory at all.
So the revisions live on two branches:

- the **core chain**, in `backend/app/db/migrations/versions/`;
- the **`modules` branch**, in `modules/backend/app/db/migrations/versions/`
  (`modules_0001_rbac`, `modules_0002_warehouse_analysis`), which forks off the core revision
  `a7b8c9d0e1f2` and carries `branch_labels = ("modules",)`.

A full checkout therefore has **two heads**, and that is the normal state, not a problem to
fix. Delete `modules/` and you have a core checkout with one head and a consistent chain. Three
rules follow:

- **Always `heads`, plural**: `upgrade heads`, `stamp heads`. Alembic refuses the singular `head`
  when two exist ("Multiple head revisions are present"); in a core checkout `heads` names the
  one head just the same.
- **A new revision names the head it extends**: `revision --autogenerate --head <core head id>`
  for a core change (the id `alembic heads` prints), `--head modules@head` for a module's. A bare
  `--autogenerate` fails with "Multiple heads are present".
- **Never merge the two heads.** A merge revision lands in the _core_ versions directory with
  `down_revision` naming `modules_0001_rbac` — and every core checkout, which has no such file,
  then fails to load the script directory at all.

**Twist two: the chain is not replayable from an empty database** (Day 2). Its early revisions
were autogenerated against databases that already had tables, and several re-create tables an
earlier one created. So there are **two paths to a current schema**:

- **Existing database** → `alembic upgrade heads` (incremental, the normal thing).
- **Fresh database** → `Base.metadata.create_all` from the models, then `alembic stamp heads`
  so future incremental migrations apply cleanly.

`python -m backend.app.db.bootstrap` chooses between them by checking for `alembic_version`,
then creates the first administrator from `FIRST_SUPERUSER`/`FIRST_SUPERUSER_PASSWORD` if the
`users` table is empty. It runs under a PostgreSQL advisory lock, so several processes starting
at once queue rather than race.

#### Who writes the schema, where

| Where                 | What writes the schema                                                                 |
| --------------------- | -------------------------------------------------------------------------------------- |
| Docker Compose        | the one API container, at start-up (`RUN_MIGRATIONS=true`, the entrypoint's default)   |
| AWS (CDK)             | **only** the deploy's one-shot migration task (`python -m backend.app.db.bootstrap`), run by `deploy.yml` before traffic moves; the API tasks set `RUN_MIGRATIONS=false` |

On AWS that last row has a consequence worth knowing before it surprises you: `cdk deploy` on
its own creates no schema. A new environment answers `/health` with 200 and every real request
with 500 until the first run of the Deploy workflow. There is one schema writer for two
reasons: an _older_ image refuses to start against a schema a newer release has migrated, so if
every task migrated, a replacement or rollback task would exit instead of serving; and several
tasks starting at once would each try to migrate.

The manual path, for what a deploy does not cover (chiefly undoing one), is the **Database
Migration** workflow (`db-migrate.yml`): it runs `alembic current`, then one `upgrade heads` or
one `downgrade <revision id>`, using the image that is currently serving. It does not run the
bootstrap.

#### Why not fix the chain?

Because rewriting history for databases that already have the chain applied is riskier than the
cookbook pattern, and because the models are already the source of truth for the test suite
(which also uses `create_all`). The rule the team adopted: **models are truth; migrations are
the delta for databases that already exist.** Every new migration must still be correct and
reviewed — it's how production moves — but nobody should expect the chain to build a database
from nothing.

And every new migration must be **backwards-compatible**: expand, then contract. During a deploy
and after an image-only rollback the _previous_ release's code runs against the _new_ schema, so
a migration adds (a nullable column, a column with a `server_default`, a new table) and never
drops or renames something the previous release still uses. The drop waits for a later release.

### How Controlvar does it

`env.py` registers exactly the models the running profile owns, refuses a present-but-broken
modules package, and hands alembic the metadata as it reads on the resolved schema:

```python
# 📁 backend/app/db/migrations/env.py
from backend.app.db.schema import export_schema_name, metadata_for_schema
from backend.app.db.url import postgres_url as build_postgres_url

schema = export_schema_name()

from backend.app.models import register_core_models  # noqa: E402
from backend.app.modules_loader import require_modules_or_absent  # noqa: E402

Base = register_core_models()
# Strict on purpose: if the modules package is present but fails to register,
# target_metadata would hold only the core tables and autogenerate would
# propose dropping every module table. Better to refuse than to be quiet.
require_modules_or_absent()
...
target_metadata = metadata_for_schema(Base, schema)
...
from backend.app.db.bootstrap import (  # noqa: E402
    may_run_alembic,
    prune_redundant_revisions,
    reconcile_with_models,
)
...
postgres_url = build_postgres_url(
    user=_db_user,
    password=_db_password,
    host=_db_host,
    port=_db_port,
    database=db_name,
)
config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
```

Three details carry weight. The URL comes from the same `POSTGRES_*` variables everything else
uses, percent-encoded — and then `%` is doubled, because alembic's config is a `configparser`
that would otherwise read an encoded password as interpolation. `include_name`/`include_object`
filters (further down) keep every other schema, and the core chain's view of module tables, out
of autogenerate's comparison, so it does not propose dropping them. And the three imported
bootstrap functions run on **every** alembic command, whoever launched it: production runs raw
`alembic upgrade heads` through the migration task, so the profile-switch handling cannot live
only in the bootstrap.

The bootstrap's decision, inside its lock:

```python
# 📁 backend/app/db/bootstrap.py
def _bootstrap_schema(engine: Engine, schema: str) -> str:
    cfg = alembic_config()

    ensure_schema(engine, schema)

    if has_alembic_version(engine, schema):
        # Before anything reads the revision set: a row that another recorded
        # row descends from makes every alembic command fail outright.
        prune_redundant_revisions(engine, schema, cfg)
        # The same guard migrations/env.py applies, so that `alembic upgrade
        # heads` and this bootstrap behave identically on a database from the
        # other profile.
        if may_run_alembic(cfg, recorded_revisions(engine, schema), schema):
            logger.info(
                "alembic_version present in schema %s: running upgrade heads", schema
            )
            command.upgrade(cfg, "heads")
        result = "upgraded"
        reconcile_with_models(engine, schema)
    else:
        logger.info(
            "Fresh database: creating schema %s from models and stamping heads",
            schema,
        )
        create_from_models(engine, schema)
        # POSTGRES_SCHEMA already names `schema` -- alembic_sees_schema().
        command.stamp(cfg, "heads")
        result = "created"

    ensure_first_superuser(engine, schema)
    return result
```

The existing-database branch handles **profile switches** in both directions. A core database
met by a full build gets `upgrade heads`, then `reconcile_with_models` creates the module tables
the models declare and the database lacks (and the `use_alter` foreign keys `create_all` skips on
an existing table). A full database met by a core build records a revision no core tree has a
file for; `may_run_alembic` leaves those rows alone and skips the upgrade with a warning when
the core chain has nothing to apply, and refuses with a message naming the revisions when it
does. `ensure_first_superuser` raises `FirstSuperuserRefused` — one line, not a traceback — if
it would create an administrator with a weak password anywhere but an explicitly chosen
development or test environment.

A hand-edited core migration in the house style. Note the docstring that says why, the
named head it extends, the schema from the environment, the guarded add (a bootstrapped schema
already has the column, from the model), and the `server_default` that keeps the previous
release working:

```python
# 📁 backend/app/db/migrations/versions/a89544fb1075_feature_flags_store_a_default_value.py
"""Feature flags: a ``default_value`` column, false on every row
...
The server default is load-bearing beyond that first fill.  The previous
release's image does not know the column, so its INSERTs do not name it; during
a deploy, and after an image-only rollback (``rollback.yml``), the database
supplies false.  Without the default those INSERTs fail on ``NOT NULL``.
...
Core chain, extending the core head ``d12cbd384bbe`` (generated with
``revision --autogenerate --head d12cbd384bbe``, then edited).  Nothing here
touches a module table, so a full checkout still has exactly two heads: this
revision and ``modules_0002_warehouse_analysis``.
...
"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a89544fb1075"
down_revision: Union[str, None] = "d12cbd384bbe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The schema name comes from the deployment's own environment, never from a
# request.
_SCHEMA = os.environ.get("POSTGRES_SCHEMA", "experimentation")

_TABLE = "feature_flags"
_COLUMN = "default_value"


def _has_column() -> bool:
    columns = sa.inspect(op.get_bind()).get_columns(_TABLE, schema=_SCHEMA)
    return any(column["name"] == _COLUMN for column in columns)


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            _TABLE,
            sa.Column(
                _COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            schema=_SCHEMA,
        )


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN, schema=_SCHEMA)
```

And the first revision of the other branch, for comparison — a plain `down_revision` onto the
core branch point, and the label that makes `modules@head` mean something:

```python
# 📁 modules/backend/app/db/migrations/versions/modules_0001_rbac.py
revision: str = "modules_0001_rbac"
#: The branch point, not a base -- see "Why the branch point is a7b8c9d0e1f2".
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = ("modules",)
#: Deliberately None: a dependency edge would collapse the two heads into one
#: recorded row -- see the docstring.
depends_on: Union[str, Sequence[str], None] = None
```

The workflow, from `CLAUDE.md`, with the reasons attached:

1. Change the model (and, for a new core model, add its module to `CORE_MODEL_MODULES`).
2. `python -m alembic -c backend/app/db/alembic.ini heads` and note the core head id. Then
   `python -m alembic -c backend/app/db/alembic.ini revision --autogenerate --head <core head
   id> -m "description"` from the repository root (`--head modules@head` for a module's table;
   alembic puts the file beside the head it extends).
3. **Read the generated file.** Autogenerate is a diff, not a decision: it will happily drop
   an index it can't match by name, miss a `CheckConstraint`, or generate an enum change that
   needs `ALTER TYPE ... ADD VALUE` (which can't run inside a transaction). Check
   `down_revision` is the head's _id_, and that nothing drops or renames what the previous
   release uses.
4. `python -m alembic -c backend/app/db/alembic.ini upgrade heads` locally; run the tests
   (which use `create_all`, so they test the _model_), plus the migration tests below.
5. Two heads afterwards (one in a core checkout) is correct. If `alembic heads` prints three,
   two new revisions extend the same head — point your `down_revision` at the other one's id
   rather than joining them.

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
export ENVIRONMENT=development POSTGRES_DB=scratch POSTGRES_SCHEMA=experimentation
docker compose exec postgres createdb -U postgres scratch
ALEMBIC="python -m alembic -c backend/app/db/alembic.ini"
python -m backend.app.db.bootstrap        # fresh → create_all + stamp heads + first admin
$ALEMBIC heads                            # two lines in a full checkout, e.g.
                                          #   4c2c09a2b3e8 (head)
                                          #   modules_0002_warehouse_analysis (modules) (head)
$ALEMBIC current                          # the same two, as recorded in alembic_version
$ALEMBIC history | head -5

# now the incremental path: add a throwaway column to a model, autogenerate, read, upgrade
CORE_HEAD=$($ALEMBIC heads | grep -v '(modules)' | cut -d' ' -f1)
$ALEMBIC revision --autogenerate --head "$CORE_HEAD" -m "scratch add note"
$EDITOR backend/app/db/migrations/versions/*scratch_add_note.py   # read it; check down_revision
$ALEMBIC upgrade heads
git checkout -- backend/app/models && rm backend/app/db/migrations/versions/*scratch_add_note.py   # discard
docker compose exec postgres dropdb -U postgres scratch
```

Then try the two mistakes and read what they say: run `$ALEMBIC revision --autogenerate -m x`
with no `--head` ("Multiple heads are present"), and `$ALEMBIC upgrade` with the singular `head`
("Multiple head revisions are present"). Both are reminders, not problems to fix.

### Key takeaways

- A full checkout has two heads: the core chain and the `modules` branch. `upgrade heads` and
  `stamp heads`; a new revision names `--head <core head id>` or `--head modules@head`; the two
  heads are never merged.
- Fresh database → bootstrap (`create_all` + `stamp heads` + first admin); existing → `upgrade
  heads`, with profile switches handled on every alembic path. The bootstrap holds an advisory
  lock.
- One schema writer per environment: the container in Compose or the
  deploy's migration task on AWS (API tasks never migrate; `cdk deploy` alone leaves no schema).
- Models are truth; migrations are the delta, and every migration is expand-only so the
  previous release keeps working. Read every generated file; `down_revision` is an id.
- Enums are the sharp edge; nullable columns and `server_default`s are the backwards-compatible
  defaults.

### Q&A

**Q. If the tests use `create_all`, what tests the migrations?**
A suite of its own, in `backend/tests/integration/database/`. `test_autogenerate_is_empty.py`
asserts that autogenerate against a freshly bootstrapped database produces an _empty_
migration — so models and migrations cannot quietly diverge. `test_alembic_branch_revisions.py`
checks that a bare `--autogenerate` refuses and that `--head modules@head` puts the file under
`modules/`. `test_profile_transitions.py` and `test_profile_migrations.py` rehearse a core
database met by a full build and the reverse, and `test_newer_database_rollback.py` an older
image started against a schema a newer release migrated (it must refuse, and say so). Several migrations have a
test of their own (`test_users_email_lower_migration.py`,
`test_feature_flag_default_value_migration.py`). Do not relax these to make a change pass; they
are the contract.

**Q. Can I downgrade?**
Each script has a `downgrade()`, but downgrading is by **revision id**: `downgrade <id>` names the
revision to end at (the `down_revision` of the migration you are undoing). Relative steps
(`-1`), `head` and `base` are ambiguous with two heads, and the Database Migration workflow
refuses them. Older revisions in the chain were not written with downgrade in mind. In practice
the Rollback workflow (Week 4) rolls back the _image_, and schema changes are expand-only so the
old code runs against the new schema; a schema downgrade is the rare, deliberate exception.

**Q. Where should the migration for a new table go — autogenerate or hand-write?**
Autogenerate to get the columns right, then hand-edit to the house style: a docstring that says
why and which head it extends, the schema from `POSTGRES_SCHEMA`, explicit index names, and a
guard (create only when absent) wherever a bootstrapped schema already has the object from the
model.

**Q. The history has revisions with two parents. Isn't that a merge?**
Yes — old ones, joining branches _within_ the core chain from before the modules existed. They
are history and stay. The rule is narrower than "no merge revision ever": never join the core
head and the `modules` head, because the result lives in the core directory and points at a file
only a full checkout has.

---

## Day 14 — Capstone: add a field end-to-end

### What & why

The Week 2 capstone adds one field to the experiment — a free-text **`decision_notes`** column
where the team records why they shipped or stopped — and threads it from the database to the
dashboard. It's the smallest change that touches every layer in the week: model, migration,
Pydantic schemas, service, endpoint, permissions, TypeScript type, page — and the gates a real
pull request has to pass. (The repo has a similar real feature, `experiment_metadata`, a JSONB
column managed by `POST /experiments/{id}/metadata`; this exercise deliberately does a plain
column so you meet each layer bare.)

One thing from Day 11 shapes the design before you write a line. Notes are written when an
experiment is **completed** or **paused**, and `PUT /experiments/{id}` refuses any change to a
non-draft experiment from everyone but a superuser (400). So the notes cannot ride on
`ExperimentUpdate`. They get a small route of their own, modelled on the metadata route: role
checked, state not.

### The trace, as a build

**1. Model.** Add the column to `Experiment` — nullable, so existing rows and the previous
release's code survive:

```python
# backend/app/models/experiment.py  (your change)
    # Free-form record of the ship/stop decision (Week 2 capstone)
    decision_notes = Column(Text, nullable=True)
```

**2. Migration.** Name the head you extend, autogenerate, then read it:

```bash
export ENVIRONMENT=development POSTGRES_DB=experimentation POSTGRES_SCHEMA=experimentation
ALEMBIC="python -m alembic -c backend/app/db/alembic.ini"
$ALEMBIC heads                                  # the core head is the line without "(modules)"
$ALEMBIC revision --autogenerate --head <core head id> -m "add experiment decision_notes"
```

The generated `upgrade()` should be one `op.add_column("experiments", sa.Column("decision_notes",
sa.Text(), nullable=True), schema="experimentation")` and the `downgrade()` the matching
`drop_column`. If autogenerate also proposes dropping an index or altering an enum, that's
drift unrelated to your change — leave it out of _this_ migration. Then hand-edit it to the
house style of Day 13's `a89544fb1075`: the schema from `POSTGRES_SCHEMA` rather than a literal,
and the add guarded by a `_has_column()` check — a bootstrapped database already has the column
(from the model), and the transition tests replay the chain over exactly such a schema. Check
`down_revision` is the core head id. Apply it with `$ALEMBIC upgrade heads`.

**3. Schemas.** The response model, so the field comes out; and a small body model for the new
route. Leave `ExperimentUpdate` alone.

```python
# backend/app/schemas/experiment.py  (your change)
class ExperimentDecisionNotes(BaseModel):
    decision_notes: Optional[str] = Field(None, max_length=4000, description="Why the experiment was shipped or stopped")


class ExperimentResponse(BaseModel):
    ...
    decision_notes: Optional[str] = None
```

Because `ExperimentResponse` has `model_config = ConfigDict(from_attributes=True)`,
`model_validate(experiment)` picks the new attribute up from an ORM object automatically.

**4. Service.** `ExperimentService._experiment_to_dict` (reached through `to_response_dict`)
builds the response dict that several routes return — add the key there, or the field will be
`None` on the way out of those routes even though it's in the database.

**5. Endpoint.** Add the route beside `update_experiment_metadata` in
`backend/app/api/v1/endpoints/experiments.py`, and copy its shape:

```python
# backend/app/api/v1/endpoints/experiments.py  (your change)
@router.put("/{experiment_id}/decision-notes", response_model=ExperimentResponse)
async def update_decision_notes(
    experiment_id: UUID = Path(..., description="The ID of the experiment"),
    body: ExperimentDecisionNotes = Body(...),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    cache_control: Dict[str, Any] = Depends(deps.get_cache_control),
) -> ExperimentResponse:
    """Record why the experiment was shipped or stopped. Any status; UPDATE by role."""
    try:
        experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
        if not experiment:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found")
        # READ and UPDATE on experiments; who created it is not considered
        experiment = deps.get_experiment_change_access(experiment, current_user)
        experiment.decision_notes = body.decision_notes
        db.commit()
        db.refresh(experiment)
        await _invalidate_experiment_cache(cache_control, experiment_id)
        return ExperimentResponse.model_validate(experiment)
    except HTTPException:
        raise
    except Exception as e:
        raise unexpected_failure(e, "Decision notes update", "Could not save the decision notes", db=db, logger=logger)
```

Every piece is one you have met this week: the 404 before the authorisation, the role check
(an ANALYST or VIEWER gets 403 — including on an experiment they created), no state check
(deliberately), the cache invalidation the other routes do, the ORM object validated into the
response schema as `start_experiment` does, and the fixed failure sentence.

Two things the platform asks of a new route that this sketch does not do yet, because two
inventories pin them. Every route that acts on experiments must be **classified**
(`backend/tests/smoke/test_experiment_routes_classified.py` lists each one with the rule that
admits a caller: `CHANGE-UPDATE`, `ROLE-ONLY`, and so on, and fails until yours has a line), and
every mutating route must say whether it is **audited**
(`backend/tests/smoke/test_audit_inventory.py`: either the audit actions it writes, or a reason
it writes none). A real pull request would add the line to both and, since changing an
experiment's notes is a change a person made, would write the audit entry as the lifecycle
routes do, with `record_experiment_change(db, current_user, ActionType.EXPERIMENT_UPDATE,
experiment_id, before_audit, reason="decision_notes")`.

**6. The OpenAPI snapshot.** Your change alters the public API in two ways: a new route, and a
new field on `ExperimentResponse`, which stable operations return.
`backend/tests/smoke/test_openapi_snapshot.py` fails on any difference to a stable operation
until the snapshots are regenerated. Run `make openapi` and commit the three files it writes:
`docs/api/openapi-v1.stable.json`, `docs/api/openapi-v1.full.json` and
`frontend/src/tests/fixtures/openapi.json`. A new route that is not meant to be stable from day
one can be marked `openapi_extra={"x-stability": "beta"}` in its decorator.

**7. Tests.** One unit test on the service (set, then read back through `to_response_dict`), and
API tests through `TestClient` with **non-superuser** users of each role: a DEVELOPER writes notes
on a COMPLETED experiment (200, and `GET` returns them), a VIEWER is refused (403). A superuser
fixture would pass every check and prove nothing about the role rule. The API test is also the
one that would catch a missing key in `_experiment_to_dict`.

**8. Dashboard type and service.** `frontend/src/types/experiments.ts` has the `Experiment`
interface — add `decision_notes?: string | null`. Don't reach for `ExperimentsService.update`: it
sends `PUT /experiments/{id}`, which a completed experiment refuses. Add a method that calls the
new route, e.g. `updateDecisionNotes(id: string, notes: string | null)`.

**9. Dashboard page.** On `frontend/src/pages/experiments/[id].tsx`, render the notes and a
textarea that saves through the new method; hide the editor for roles that cannot change
experiments (the API refuses them anyway). Add a Jest test in
`frontend/src/tests/pages/experiment-detail.test.tsx` that routes `apiFetch` with `routedApi`
and asserts the URL and body with `calledWith` — the existing tests there show the pattern
(Hour 21 of the React book).

**10. Verify end-to-end.** Run the API and the dashboard, complete an experiment, edit its notes,
then `select decision_notes from experimentation.experiments where id = ...`.

**11. Open the pull request the way the repository expects.** The title is a conventional
commit, because `main` is squash-merged and the title becomes the release note:
`feat(experiments): record the decision behind an experiment`. The migration is expand-only (it
adds a nullable column), which the pull request template asks you to confirm. A
`@pytest.mark.regression` test is required for bug fixes; this is a feature, so it isn't.

### Build It

Do it. Then do the destructive half: `git stash` the model change but keep the migration
applied, restart the API, and hit `GET /experiments/{id}`. It works — the old code ignores the
new column — which is the concrete meaning of "backwards-compatible nullable column". Now
imagine the column had been `nullable=False` with no `server_default`, and the deploy had gone
migration-first: every insert from the old containers fails until the new ones are live, and
again after any image-only rollback. That's why the deploy (Week 4) runs the migration _before_
the traffic switch, why `feature_flags.default_value` carries a `server_default`, and why the
team's migrations are expand-only.

### Key takeaways

- One field touches a dozen files across five layers plus the API snapshot; each layer has one
  job and the shapes must agree.
- Name the head (`--head <core head id>`), autogenerate, read it, guard it, keep it minimal,
  check `down_revision`.
- Know the state rules before choosing the route: `PUT` changes only a DRAFT experiment, so data
  that belongs to a finished experiment needs a route whose rule is role-only.
- `from_attributes=True` makes response schemas pick up ORM attributes; the service's dict
  builder does not — check both.
- Test permissions with non-superusers of each role; regenerate and commit the OpenAPI
  snapshots; title the pull request as a conventional commit.

---

## Week 2 Workshop

### Quiz

**1. In what order does a request pass through the middleware, and which layer is registered
first?**
CORS → RequestID → Prometheus → SecurityHeaders → TrustedHost → RateLimit →
RelativeSlashRedirect → RequestBodyLimit → UnhandledError → router. `UnhandledErrorMiddleware` is
registered first (innermost); `CORSMiddleware` last (outermost).

**2. `ENVIRONMENT=prod` and `APP_ENV=dev` are both set. What environment does the app run in,
and what happens?**
`production` — `ENVIRONMENT` wins; `prod` is canonicalised with a `DeprecationWarning`, and a
warning logs that the two variables disagree. The settings validators then require a real
`SECRET_KEY` and first-administrator password and a `PUBLIC_BASE_URL` (or `ALLOWED_HOSTS`), and
refuse `TESTING` and the dev bypass — or the process does not start.

**3. A request carries both `Authorization: Bearer <valid JWT>` and `X-API-Key: <valid key>`
to `POST /tracking/assign`. Which identity is used?**
The API key's owner — the endpoint depends on `get_api_key`, not `get_current_user`. The
bearer header is ignored on that route.

**4. A production task definition sets `TESTING=true` by mistake. What happens?**
The settings refuse at import, before any field is validated, with "TESTING is for the test
runner and cannot be combined with ENVIRONMENT=production" (and the container's preflight lists
the same refusal first). Nothing is relaxed by `TESTING` in any environment; the old behaviour,
in which it relaxed the secret checks, was one variable away from a production deployment
running on demo secrets.

**5. A `DEVELOPER` tries to `PUT` a feature flag someone else created. A `VIEWER` tries to `PUT`
a flag they created themselves. What happens?**
The developer is allowed: `can_act_on_feature_flag(user, flag.owner_id, Action.UPDATE)` finds
UPDATE in the role matrix and scope `ANY`. The viewer is refused with 403: the role lacks UPDATE,
and that check comes first, so owning the flag is irrelevant.

**6. Why does `create_experiment` call `db.flush()` before adding variants?**
`SessionLocal` is `autoflush=False`, so the experiment's UUID isn't guaranteed to be
generated and usable as `experiment_id` on the variants until the insert is flushed.

**7. An `ADMIN` (not a superuser) calls `DELETE /experiments/{id}?experiment_key={id}` on an
ACTIVE experiment. A `VIEWER` makes the same call. What does each get?**
The admin gets 400, "Cannot delete experiments that are not in DRAFT status": the role holds
DELETE, so the state check runs and refuses. The viewer gets 403: the role check runs first and
the state is never consulted.

**8. You run `alembic upgrade heads` on a full checkout and `alembic heads` still prints two
lines. Is something wrong?**
No. The core chain and the `modules` branch are separate on purpose, so that a checkout with
`modules/` deleted has a consistent chain. Two heads is the steady state; they are never merged.

### Exercises

**E2.1 — Order the onion.** Without looking, write the nine `add_middleware` calls in
registration order, then the order a request traverses them. Explain why the rate limiter is
inside the security headers and the request id but outside the router, and why CORS must be the
outermost layer.

**E2.2 — Write a guard.** Add a `field_validator` in the style of `validate_secret_key` for a
hypothetical `WEBHOOK_SIGNING_SECRET`, refusing `""`, `"changeme"` and anything under 32
characters in staging and production. Then say what else must change for the container's
start-up check to report it too.

**E2.3 — Guard a new route.** You are adding `POST /feature-flags/{flag_id}/lock`. Write the
authorisation part of the handler, say which roles pass, and name the test that will fail in CI
until you do one more thing.

**E2.4 — Model a table.** Define a `DecisionLog` model (who decided, which experiment, a
free-text reason, when) in the house style: mixin, schema-qualified FK, `back_populates` on
`Experiment`, a composite index on `(experiment_id, created_at)`, `ondelete="CASCADE"`. Say how
the model gets registered.

**E2.5 — Read a migration.** For the `DecisionLog` table, write the command that generates the
migration, the `upgrade()` an autogenerate would produce, and two things you'd hand-edit before
committing.

### Solutions

**S2.1** — Registration: `UnhandledErrorMiddleware`, `RequestBodyLimitMiddleware`,
`RelativeSlashRedirectMiddleware`, `RateLimitMiddleware`, `TrustedHostMiddleware`, `SecurityHeadersMiddleware`,
`PrometheusMetricsMiddleware`, `RequestIDMiddleware`, `CORSMiddleware`. Traversal is the
reverse: CORS → RequestID → Prometheus → SecurityHeaders → TrustedHost → RateLimit →
RelativeSlashRedirect → RequestBodyLimit → UnhandledError → handler. The rate limiter returns its 429 without
calling the next layer, so everything that must decorate a 429 — the request id, the security
headers, CORS — has to be _outside_ it; and it is outside the router so a rejected request never
reaches a handler (or a database session). CORS is outermost because a browser will not let the
page read _any_ response, a 429 or a 500 included, that lacks the `Access-Control-*` headers; and
a preflight is answered there without spending a rate-limit slot.

**S2.2**

```python
    WEBHOOK_SIGNING_SECRET: str = ""

    @field_validator("WEBHOOK_SIGNING_SECRET")
    @classmethod
    def validate_webhook_secret(cls, v: str, info: ValidationInfo) -> str:
        if _hardening_required(info):
            if _secret_is_placeholder(v) or len(v) < _MIN_SECRET_KEY_LENGTH:
                raise ValueError(
                    "WEBHOOK_SIGNING_SECRET must be at least 32 characters and must not "
                    "be a placeholder in staging/production."
                )
        return v
```

Declare it _after_ `ENVIRONMENT` in the class so `info.data` contains it. `_secret_is_placeholder`
already covers `""` and `"changeme"`, and there is no `TESTING` branch: nothing relaxes a guard.
The container's preflight (`backend/app/core/preflight.py`) never imports the settings, so it
knows only the rules it is given: add the check there too, using the same `settings_rules`
helpers, or the container will report every other problem at once and this one only on the
next start.

**S2.3**

```python
    flag = db.query(FeatureFlag).filter(FeatureFlag.id == flag_id).first()
    if not flag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feature flag not found")
    if not can_act_on_feature_flag(current_user, flag.owner_id, Action.UPDATE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to update this feature flag",
        )
```

ADMIN and DEVELOPER pass on any flag, and so does any superuser; ANALYST and VIEWER are refused
with 403, on their own flags too. `owner_id` comes from the stored row, never from a response
dict. The test that fails is `backend/tests/smoke/test_flag_change_routes_guarded.py`
(`test_the_inventory_is_exact`): every mutating route under `/feature-flags` must be classified,
so add `("POST", "/api/v1/feature-flags/{flag_id}/lock"): FLAG_RULE` to its `INVENTORY`. (A
second smoke test, `test_audit_inventory.py`, will want the route classified too, as audited or not.)

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
cascade="all, delete-orphan")`. It is a core model, so its module goes into `CORE_MODEL_MODULES`
in `backend/app/models/__init__.py` (and an import there, like its neighbours); a module's model
would be registered with `hooks.register_model_module()` instead.

**S2.5** — The command: `python -m alembic -c backend/app/db/alembic.ini revision --autogenerate
--head <core head id> -m "add decision_logs"`, with the id from `alembic heads` (the line without
`(modules)`). The upgrade: `op.create_table("decision_logs", sa.Column("id", UUID(as_uuid=True),
primary_key=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at",
sa.DateTime(), nullable=False), sa.Column("experiment_id", UUID(as_uuid=True), nullable=False),
sa.Column("decided_by", UUID(as_uuid=True), nullable=True), sa.Column("reason", sa.Text(),
nullable=False), sa.ForeignKeyConstraint(["experiment_id"], ["experimentation.experiments.id"],
ondelete="CASCADE"), sa.ForeignKeyConstraint(["decided_by"], ["experimentation.users.id"],
ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"), schema="experimentation")` followed by
`op.create_index("experimentation_decision_log_experiment_created", "decision_logs",
["experiment_id", "created_at"], schema="experimentation")`. Hand-edits: replace the literal
`"experimentation"` with the schema read from `POSTGRES_SCHEMA`, so the migration works for any
schema; create the table only when it is absent, because a bootstrapped database already has it
from the model; write the docstring that says why and which head it extends; and confirm
`down_revision` is the core head's id. Afterwards `alembic heads` still prints two lines.

---

**Next:** [Week 3 — The Hard Parts →](./week-3-hard-parts.md)

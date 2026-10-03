# Part III — Observability

### Hours 7–9

[← Part II](./part-2-aws.md) · [Contents](./README.md) · [Part IV →](./part-4-release-engineering.md)

You can't operate what you can't see. This Part is the three ways the platform makes itself
visible: **logs** that are one JSON object per line with a request ID that survives from
nginx to the database (Hour 7); **health probes** that tell the truth to the right audience
and **metrics** that Prometheus can scrape without leaking them to the world (Hour 8); and
the **schedulers** — the platform's only long-running background work — with their lock,
their run history, their health endpoint, and the alarms and Slack/email notifications built
on all of it (Hour 9).

---

## Hour 7 — Logs and request IDs: structlog, JSON in production, `X-Request-ID` end to end

### What & why

A log line is only useful if a machine can parse it and a human can correlate it. The
platform's answer: **structlog** with a single root handler, so _every_ line the process
writes — structlog events, plain `logging.getLogger` calls from the codebase, uvicorn's
access log, SQLAlchemy's warnings — comes out as one JSON object when `LOG_FORMAT=json`
(containers, CloudWatch) or as coloured console text in development. And a **request ID**
bound to the async context at the top of every request, so every line logged while
handling it carries `request_id`, `path` and `method` without any code passing it along.

The request ID is minted as far upstream as possible: nginx sets `X-Request-ID` on its
proxy call (Hour 2), the API adopts an incoming one or generates a UUID4, and echoes it in
the response header. A user reporting "it failed at 14:02" can give you the header value
from their browser's network tab; you can go straight to the lines.

### How Experimently does it

The middleware — adopt or mint, bind, echo:

```python
# 📁 backend/app/middleware/request_id_middleware.py
class RequestIDMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that ensures every request has a unique ID."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id: str = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Bind to the current async context so all log lines in this request
        # automatically include request_id, path, and method.
        bind_log_context(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

`bind_log_context` writes to a `contextvars.ContextVar`, which is per-task in asyncio —
two concurrent requests in the same worker don't see each other's IDs. The logging
configuration reads it back through a processor:

```python
# 📁 backend/app/core/logger.py
def configure_logging(log_level: str = "INFO", json_logs: bool = True, service_name: str = "experimentation-platform", stream: Any = None) -> None:
    """Configure structlog **and** the standard library for the application.

    Both structlog loggers (``get_logger``) and plain ``logging.getLogger``
    loggers (the majority of the codebase, plus uvicorn, sqlalchemy, ...) are
    rendered by the same :class:`structlog.stdlib.ProcessorFormatter` on a
    single root handler, so with ``json_logs=True`` **every** line the process
    writes is one JSON object (``LOG_FORMAT=json`` in production/containers)
    and with ``json_logs=False`` every line is the coloured console format.
    """
    ...
    # Processors shared by structlog events and foreign (stdlib) records.
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        _inject_context,
        _add_service,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_logs:
        renderer: Any = structlog.processors.JSONRenderer()
        # Turn exc_info into a string field ("exception") for JSON output.
        pre_render: list = [structlog.processors.format_exc_info]
    else:
        renderer = structlog.dev.ConsoleRenderer()
        pre_render = []
    ...
    formatter = structlog.stdlib.ProcessorFormatter(
        # Processors applied to every record right before rendering.
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta] + pre_render + [renderer],
        # Processors applied to records that did NOT originate from structlog.
        foreign_pre_chain=shared_processors,
```

The key design is `foreign_pre_chain=shared_processors`: records from the standard library
(which is most of the codebase) go through the _same_ processors as structlog events, so
`_inject_context` adds `request_id` to a `logger.warning(...)` in `assignment_service.py`
exactly as it would to a structlog call. Nobody has to migrate to `get_logger()` to get
correlated logs.

The switch lives in `main.py` and reads `LOG_FORMAT`:

```python
# 📁 backend/app/main.py
# LOG_FORMAT=json  -> every line (structlog, stdlib, uvicorn) is one JSON object
# LOG_FORMAT=console -> coloured console output
```

with compose defaulting to `console` (`LOG_FORMAT: ${LOG_FORMAT:-console}`) and the Fargate
task definition not setting it at all. Unset, `LOG_FORMAT` is `None`, which means JSON in
staging and production and console everywhere else. A production line looks like:

```json
{"event": "Assigned user u-123 to variant 7c1… in experiment 3f2a…", "service": "experimentation-platform", "logger": "backend.app.services.assignment_service", "level": "info", "timestamp": "2026-09-11T14:02:07.312Z", "request_id": "5e0b4c2a-…", "path": "/api/v1/tracking/assign", "method": "POST"}
```

and the CloudWatch Logs Insights queries in `docs/monitoring/log-insights-queries.md` are
written against exactly those fields:

```text
# 📁 docs/monitoring/log-insights-queries.md
fields @timestamp, @message
| parse message "\"url\":\"*\"" as url
| parse message "\"error_type\":\"*\"" as error_type
| stats count(*) as error_count by url, error_type
| sort error_count desc
| limit 20
```

The rest of the chain: nginx sets the header from its own `$request_id`, the ALB in
production adds `X-Amzn-Trace-Id` (a different ID, also logged by the ALB access logs), and
the API's response header lets the dashboard's `ApiError` (Build book Day 23) surface the
ID in its error toast — so "it said request 5e0b…" is a thing a user can actually say.

> **By the Way** — `WEB_CONCURRENCY=1` matters here too. With one worker per container,
> "the container's log stream" _is_ "the process's log stream"; CloudWatch's `stream_prefix
> ="backend"` gives one stream per task, and a request ID identifies one task's lines
> unambiguously. Multi-worker containers interleave workers into one stream and lose that.

> **Watch Out!** `_safe_error(exc)` in the health module and the `format_exc_info`
> processor exist because exceptions can carry secrets — a database URL with a password, a
> Redis auth token in a connection error. The logging config renders exceptions as a
> string field, and the health endpoint scrubs its errors and hides details in production
> (Hour 8). If you add a new place that logs an exception with connection details, scrub
> it.

### Try It Yourself

```bash
LOG_FORMAT=json docker compose up -d --wait api
curl -s -D- -o /dev/null localhost:8000/health/live | grep -i x-request-id          # a UUID4 was minted
curl -s -D- -o /dev/null -H 'X-Request-ID: trace-me-123' localhost:8000/api/v1/experiments | grep -i x-request-id   # adopted
docker compose logs api --no-log-prefix | grep '"request_id": "trace-me-123"' | jq -c '{level, logger, event, path}'
docker compose logs api --no-log-prefix | tail -1 | jq .                              # every line is JSON
```

Then through nginx: `curl -s -D- -o /dev/null localhost:3000/api/v1/experiments | grep -i
x-request-id` — the ID came from nginx's `$request_id` (32 hex chars, not a UUID), and the
API adopted it.

### Key takeaways

- One root handler, structlog's `ProcessorFormatter` with `foreign_pre_chain`: every line
  from every logger is JSON in production (`LOG_FORMAT=json`) or console in development.
- `RequestIDMiddleware` adopts `X-Request-ID` or mints a UUID4, binds it (plus path and
  method) to the async context, echoes it in the response; nginx mints upstream.
- Log Insights queries are written against the JSON fields; one worker per task keeps
  streams unambiguous.
- Exceptions are rendered as strings and scrubbed where they could carry secrets.

---

## Hour 8 — Health and metrics: liveness vs readiness, `/metrics` and its token, what to graph

### What & why

A health endpoint has two audiences with opposite needs. The **container runtime** asks "is
the process alive?" and will _restart_ it on a no — so that check must never fail because a
dependency is down, or a Postgres blip would restart every healthy API task at once. The
**load balancer** asks "can this instance serve?" and will _stop routing to it_ on a no — so
that check should fail when the database is unreachable, because a task that can't reach
Postgres can't serve anything. Liveness and readiness are different questions, and the
platform answers them at different paths.

Metrics are the third audience: Prometheus (or the CloudWatch agent) scraping `/metrics`
for request counts, latencies, assignments, evaluations, cache hits, rate limits and
scheduler ticks. The scrape endpoint is unauthenticated by nature — so it's guarded by a
token outside development, and labels are normalised so a UUID in a path doesn't create a
metric series per experiment.

### How Experimently does it

The contract, from the module's docstring:

```python
# 📁 backend/app/core/health.py
"""
Health probes and the Prometheus scrape endpoint.

Routes (all unauthenticated, none in the OpenAPI schema):

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

``GET /health``
    Alias of ``/health/ready`` kept for the existing ALB/ECS/CDK wiring.

``GET /metrics``
    Prometheus text exposition. When ``METRICS_TOKEN`` is set the request must
    carry it (``Authorization: Bearer <token>`` or ``?token=<token>``);
    when it is not set the endpoint is open in development/test only and
    returns 403 elsewhere. ``METRICS_ENABLED=false`` disables it (404).
"""
```

Readiness — the checks, the rule, and the production redaction:

```python
# 📁 backend/app/core/health.py
def readiness_payload() -> tuple[Dict[str, Any], int]:
    """Run the readiness checks. Returns ``(body, http_status)``."""
    checks: Dict[str, Dict[str, Any]] = {
        "database": check_database(),
        "redis": check_redis(),
        "disk": check_disk(),
        "modules": check_modules(),
    }

    # The modules check is deliberately not part of ``ready``: see
    # :func:`check_modules`.
    ready = checks["database"]["status"] == "healthy"
    if checks["redis"]["status"] != "healthy" and redis_required():
        ready = False
    if checks["disk"]["status"] == "unhealthy":
        ready = False

    body: Dict[str, Any] = {
        "status": "healthy" if ready else "unhealthy",
        "timestamp": _timestamp(),
        # Which profile is serving, in every environment: an operator reading
        # a production probe should not have to call a route to find out.
        "profile": checks["modules"]["profile"],
    }

    if not is_production():
        # Details (errors, latencies, environment) only outside production.
        body["version"] = str(settings.VERSION)
        body["environment"] = environment_name()
        body["checks"] = checks
    else:
        body["checks"] = {name: {"status": data.get("status", "unknown")} for name, data in checks.items()}

    return body, (200 if ready else 503)
```

The fourth check, `modules`, reports which build profile is serving (`core`, or `full` when
the optional modules registered) and whether a found `modules` package failed to install.
It is **reported, never gated**: a degraded profile still serves every core route, and
outside development and test the API refuses to start in that state anyway. The top-level
`profile` field is in the body in every environment, production included, so "which build
is this?" is one unauthenticated `curl` away.

The database check uses a session from the _application pool_ — so it fails when the pool
is exhausted, which is a real "can't serve" condition, not just when Postgres is down. The
Redis check builds its client with the same factory the rest of the application uses, so it
honours `REDIS_SSL` (on AWS the ElastiCache group requires TLS) exactly as the real
connections do, and it uses one-second timeouts so a hung Redis can't make readiness itself
hang past the ALB's timeout:

```python
# 📁 backend/app/core/health.py
def check_redis() -> Dict[str, Any]:
    """``PING`` Redis using the connection settings."""
    try:
        from backend.app.core.redis_client import create_redis_client

        t0 = time.perf_counter()
        client = create_redis_client(socket_connect_timeout=1, socket_timeout=1)
        try:
            client.ping()
        finally:
            try:
                client.close()
            except Exception:  # pragma: no cover - best effort
                pass
```

Where each probe is consumed — the contract table:

| Path | Consumer | On failure |
|---|---|---|
| `/health/live` | Docker `HEALTHCHECK` (image) | container marked unhealthy → restart |
| `/health/ready` | compose healthcheck; `--wait` | `up --wait` blocks; dependents wait |
| `/health` | ALB target groups (blue & green); ECS container health check | task pulled from rotation; ECS replaces it after 3 failures |
| `/healthz` | dashboard container (nginx, static) | dashboard container restart |

The metrics guard — constant-time token compare, three states:

```python
# 📁 backend/app/core/health.py
def metrics_access(request: Request) -> Optional[JSONResponse]:
    """Return an error response when the scrape is not allowed, else ``None``."""
    if not metrics_enabled():
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    expected = metrics_token()
    if expected:
        presented = _presented_token(request)
        if not presented or not hmac.compare_digest(
            presented.encode("utf-8"), expected.encode("utf-8")
        ):
            return JSONResponse({"detail": "Invalid or missing metrics token"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        return None

    if is_development_or_test():
        return None

    return JSONResponse({"detail": "The /metrics endpoint requires METRICS_TOKEN outside development"}, status_code=403)
```

Fail closed: no token in production means 403, not open. The comparison is on **bytes**:
Starlette decodes headers as latin-1, and `hmac.compare_digest` raises `TypeError` on a
non-ASCII `str`, so comparing strings would turn a junk header into a 500 from an
unauthenticated endpoint instead of a 401. The same rule the config
validators apply to `SECRET_KEY` and `DEV_AUTH_BYPASS` (Build book Day 9).

What's exposed — the metric registry, with the platform-specific counters that make the
dashboards meaningful:

```python
# 📁 backend/app/core/metrics.py
http_requests_total: Counter = Counter(...)              # method, endpoint, status
http_request_duration_seconds: Histogram = Histogram(...)
experiment_assignments_total: Counter = Counter(...)     # experiment_id, variant_id
events_tracked_total: Counter = Counter(...)             # event_type (bucketed)
feature_flag_evaluations_total: Counter = Counter(...)   # flag_key, result
cache_hits_total / cache_misses_total: Counter           # cache_type
rate_limit_hits_total / rate_limit_rejections_total: Counter
active_experiments_gauge: Gauge
scheduler_last_success_timestamp: Gauge = Gauge("scheduler_last_success_timestamp", "Unix timestamp of the last successful tick of each background scheduler", ["name"])
scheduler_tick_duration_seconds: Histogram = Histogram(..., ["name"], buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 15.0, 60.0, 300.0])
scheduler_ticks_total: Counter = Counter("scheduler_ticks_total", "Background scheduler ticks by outcome (success, partial, failed, skipped)", ["name", "status"])
```

and the middleware that keeps HTTP labels bounded:

```python
# 📁 backend/app/middleware/prometheus_metrics_middleware.py
def normalize_path(path: str) -> str:
    """Replace dynamic path segments with ``{id}`` for stable metric labels.

    Two categories of dynamic segments are replaced:

    * UUID v4 (e.g. ``550e8400-e29b-41d4-a716-446655440000``)
    * Pure numeric IDs (e.g. ``42``)
    """
    path = _UUID_PATTERN.sub("/{id}", path)
    path = _NUMERIC_PATTERN.sub("/{id}", path)
    return path
```

Without that, `/api/v1/experiments/<uuid>/results` would be a new time series per
experiment — a cardinality explosion that takes Prometheus down. `event_type_label` does the
same for events (a bounded set of known types plus `other`). `experiment_assignments_total`
_does_ carry `experiment_id` — bounded by the number of experiments, which is a deliberate,
small cardinality.

**What to graph.** The platform ships two CloudWatch dashboards
(`infrastructure/cloudwatch/*.json`, deployed by `deploy-dashboards.sh`) and a CDK one in
the monitoring stack. Be aware that the CDK dashboard's widgets still graph API Gateway,
Lambda and DynamoDB metrics from the original architecture that nothing deployed publishes
today, so those panels stay empty; the alarms (Hour 9) were cleaned up, the dashboards were
not. The four graphs an on-call engineer actually looks at, in order:

1. `http_requests_total` by status, rate over 1 min — the 5xx line is the pager.
2. `http_request_duration_seconds` p99 by endpoint — `/tracking/assign` and
   `/feature-flags/evaluate/{id}` are the hot path; p99 > 500 ms is a problem.
3. `scheduler_last_success_timestamp{name}` — `time() − value` per scheduler; > 2 × the
   scheduler's interval means a stuck or crash-looping loop (Hour 9).
4. `rate_limit_rejections_total` rate — a spike is either an abusive client or an SDK
   misconfiguration (usually a server-side SDK with the cache disabled). The limits it
   counts are per client IP: SDK paths share `SDK_RATE_LIMIT_PER_MINUTE` (6,000/min by
   default), exports share 10/min, auth endpoints have strict per-route limits, and
   everything else gets 300/min (`resolve_rate_limit()` in
   `backend/app/middleware/rate_limiter.py`).

> **Watch Out!** `/health` is the alias the ALB uses, and it's _readiness_. If you ever
> change the ALB target group to `/health/live` "to stop flapping during a DB blip," you
> will route traffic to tasks that can't serve it. The flapping is the system working.
> Fix the blip, or raise `unhealthy_threshold_count`; don't lie to the load balancer.

> **Did You Know?** `check_disk` reports `low` under 1 GB of free space (and `unhealthy`
> only when it cannot read the disk at all) — `low` does not fail readiness, it is a
> warning in the body. Full disks are the classic way a healthy-looking process stops writing logs
> and audit rows. Fargate ephemeral storage is 20 GB by default and the image writes little,
> but a self-hosted VM with Postgres on the same disk is exactly where this check has earned
> its keep.

### Try It Yourself

```bash
curl -s localhost:8000/health/live | jq .
curl -s localhost:8000/health/ready | jq .checks                    # latencies visible (development)
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/metrics      # 200 in development
ENVIRONMENT=production docker compose up -d api                      # (with a real SECRET_KEY in .env)
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/metrics      # 403: token required outside development
METRICS_TOKEN=s3cret docker compose up -d api
curl -s -H 'Authorization: Bearer s3cret' localhost:8000/metrics | grep -E '^(http_requests_total|scheduler_last_success)' | head
docker compose stop postgres; curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/health/ready   # 503
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/health/live                                  # still 200
```

Then hit `/api/v1/experiments/<uuid>` a few times and confirm the metric label is
`/api/v1/experiments/{id}`, not the UUID.

### Key takeaways

- Liveness (`/health/live`, no dependencies) for the container runtime; readiness
  (`/health/ready` = `/health`, DB required, Redis if `REDIS_REQUIRED`, disk) for the ALB
  and compose; details redacted in production.
- `/metrics` fails closed: token required outside development (`hmac.compare_digest` on
  bytes), `METRICS_ENABLED=false` → 404.
- Readiness also reports `profile` (`core`/`full`) and a `modules` check, in every
  environment; neither gates readiness.
- Labels are normalised (`{id}`, bucketed event types) to keep cardinality bounded.
- Graph 5xx rate, hot-path p99, scheduler staleness, rate-limit rejections.

---

## Hour 9 — Schedulers and alerts: the advisory lock, run history, scheduler health, CloudWatch alarms, Slack/email

### What & why

Five background loops run inside every API process — experiment (start/end by schedule),
rollout (advance stages), metrics (aggregate; also purges analysis history), safety (error
rates → rollback), bandit (recompute weights). They are the only work that happens without a
request, which makes them both the most important thing to monitor and the easiest to lose
track of: a stuck scheduler produces no errors, just an absence.

Three guarantees, from one shared runner. **Single runner per tick**: with several API
tasks (two in staging, three in production, Hour 5), each tick must run once, not once per
task — a PostgreSQL advisory lock keyed
by the scheduler's name decides who. **Run history**: every executed tick is persisted, so
"when did the safety monitor last succeed?" is a query, not a guess. **Prometheus signal**:
the three scheduler metrics from Hour 8. On top of those sit the health endpoint, the
CloudWatch alarms, and the notifications the schedulers themselves send.

### How Experimently does it

The runner's contract:

```python
# 📁 backend/app/core/scheduler_tick.py
"""
Shared runner for background scheduler ticks.

All five schedulers (experiment, rollout, metrics, safety, bandit) run their
periodic work through :func:`run_locked_tick`, which gives every tick the same
three guarantees:

1. **Single runner per tick** — the work runs under the PostgreSQL advisory
   lock from :mod:`backend.app.core.scheduler_lock`; a replica that does not
   get the lock skips the tick.
2. **Run history** — every executed tick is persisted through
   :meth:`SchedulerHealthService.record_run` (``scheduler_runs`` table), which
   feeds ``GET /api/v1/scheduler/health``.
3. **Prometheus signal** — ``scheduler_last_success_timestamp{name}``,
   ``scheduler_tick_duration_seconds{name}`` and
   ``scheduler_ticks_total{name,status}`` from :mod:`backend.app.core.metrics`.
"""
```

The lock — non-blocking, session-scoped on a dedicated autocommit connection, released
explicitly, and _never_ a reason for the API to crash:

```python
# 📁 backend/app/core/scheduler_lock.py
* ``pg_try_advisory_lock(hashtext(name))`` is non-blocking: a replica that does
  not get the lock skips the tick instead of queueing behind the holder.
* The lock is session-scoped, so it is taken on a **dedicated connection**
  checked out from the engine for the duration of the ``with`` block and
  released with ``pg_advisory_unlock`` on exit (also on exceptions). ...
* The connection runs in AUTOCOMMIT mode: a session-level advisory lock does
  not need a transaction, and without autocommit the ``SELECT`` would leave the
  connection "idle in transaction" for the whole tick, which managed Postgres
  services kill after ``idle_in_transaction_session_timeout`` (silently
  releasing the lock mid-tick).
* ``async_scheduler_lock`` does the (blocking) acquire and release in a worker
  thread so a slow or unreachable database never stalls the event loop.
* ``hashtext`` is PostgreSQL's built-in text hash (``int4``), so every replica
  derives the same lock key from the same name without a shared registry.
* When the database is unreachable the context manager yields ``False`` and
  logs a warning — a scheduler must never crash the API because it could not
  coordinate; it simply skips the tick.
```

```python
# 📁 backend/app/core/scheduler_lock.py
LOCK_NAMESPACE = "experimently.scheduler."

def try_acquire(conn: Connection, name: str) -> bool:
    """Attempt to take the advisory lock for *name* on *conn* (non-blocking)."""
    row = conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": lock_name(name)}).scalar()
    return bool(row)
```

The AUTOCOMMIT note is the one that was learned the hard way: Aurora kills idle-in-transaction
sessions, and a killed session releases its advisory lock — so two replicas could end up
running the same tick, one of them half-way through. A dedicated autocommit connection has
no open transaction to be idle in.

The tick itself — skip → record → classify → re-raise:

```python
# 📁 backend/app/core/scheduler_tick.py
    async with async_scheduler_lock(name, engine=engine) as acquired:
        if not acquired:
            completed_at = datetime.now(timezone.utc)
            prom.record_scheduler_tick(name, STATUS_SKIPPED, time.perf_counter() - t0)
            logger.info("scheduler %s: lock held elsewhere, tick skipped", name)
            return TickOutcome(name=name, status=STATUS_SKIPPED, acquired=False, ...)

        error: Optional[str] = None
        exc_to_raise: Optional[Exception] = None
        result = TickResult()
        try:
            raw = await tick()
            result = TickResult.coerce(raw)
            if result.items_failed and result.items_processed:
                status = STATUS_PARTIAL
            elif result.items_failed and not result.items_processed:
                status = STATUS_FAILED
                error = f"{result.items_failed} item(s) failed"
            else:
                status = STATUS_SUCCESS
```

`skipped` is a normal, expected status on two of three replicas — it's recorded to
Prometheus (so the dashboard shows the lock working) but not to the run-history table (so
the history is of _work_, not of coordination). A tick that raises is recorded as `failed`
and then **re-raised**, so each scheduler loop keeps its own back-off; the runner doesn't
swallow.

Scheduler health — is it running (activity in the last 30 minutes), what was the last
status, how many consecutive failures:

```python
# 📁 backend/app/services/scheduler_health_service.py
_RUNNING_THRESHOLD_MINUTES = 30
...
    def get_health(self, db: Session, scheduler_name: SchedulerName) -> SchedulerHealthResponse:
        """
        Compute the current health state for a single scheduler.
        ...
        Returns:
            SchedulerHealthResponse with is_running, consecutive_failures,
            last_run_at, last_run_status, and average_duration_seconds.
        """
        ...
        # Determine if the scheduler is running based on recent activity.
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(minutes=_RUNNING_THRESHOLD_MINUTES)
        latest_completed = latest.completed_at
        if latest_completed and latest_completed.tzinfo is None:
            latest_completed = latest_completed.replace(tzinfo=timezone.utc)
```

(The `tzinfo is None` guards are the naive/aware datetime lesson from Build book Day 17,
applied here too — a naive timestamp read back from the database compared with an aware
`now()` would raise.) `GET /api/v1/scheduler/health` returns all five; the admin UI renders
it, and it's the thing to check first when "experiments aren't starting on schedule."

**Alarms.** Every CloudWatch alarm the stacks create publishes to one SNS topic,
`experimentation-alerts-<env>`, and that topic has exactly one email subscriber: the address
in **`ALARM_EMAIL`**, read by `app.py` at synth time and never written in code:

```python
# 📁 infrastructure/cdk/stacks/alarm_email.py
#: The environments that refuse to synthesise without ``ALARM_EMAIL``.
ALARM_EMAIL_REQUIRED = frozenset({"staging", "prod"})

#: The documentation domains (RFC 2606). Mail to them goes nowhere.
EXAMPLE_DOMAINS = ("example.com", "example.net", "example.org")


def resolve_alarm_email(value: str | None, env_name: str) -> str | None:
    """The address to subscribe, ``None`` for no subscription, or ``ValueError``."""
    if value is None or not value.strip():
        if env_name in ALARM_EMAIL_REQUIRED:
            raise _refuse(env_name, f"is required in {env_name} and is not set")
        return None
```

In staging and prod, synth refuses to run without it; in dev and demo, no value means no
subscription at all rather than a placeholder that mails nobody. Any value given is checked
with plain string rules (one `@`, a dotted domain, no whitespace, at most 254 characters),
and an address at `example.com`, `example.net` or `example.org` is refused — the topic's
only subscriber used to be the literal `alerts@example.com`, so every alarm, automatic
rollbacks included, told nobody. One step no code can do for you: **SNS mails the address a
confirmation link, and until the recipient confirms it no alarm reaches them.** Confirming
the subscription is part of standing up an environment, not an afterthought.

The monitoring stack's own alarms watch the data services, each built from a metric a
deployed resource actually publishes:

```python
# 📁 infrastructure/cdk/stacks/monitoring_stack.py
        aurora_cpu_alarm = cloudwatch.Alarm(
            self,
            "AuroraCpuAlarm",
            metric=cloudwatch.Metric(
                namespace="AWS/RDS",
                metric_name="CPUUtilization",
                dimensions_map={
                    "DBClusterIdentifier": aurora_cluster_identifier,
                    "Role": "WRITER",
                },
                statistic="Average",
                period=Duration.minutes(5),
            ),
            evaluation_periods=3,
            threshold=80,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_name="AuroraHighCPU" + suffix,
        )

        aurora_cpu_alarm.add_alarm_action(
            cloudwatch_actions.SnsAction(self.alerts_topic)
        )
```

Missing data is **breaching**: a writer that publishes no CPU metric is not a healthy one.
The same shape gives one `RedisHighCPU-00N-<env>` alarm per Redis node (engine CPU ≥ 80%),
and, in a full-profile deployment only, `KinesisProcessingDelay-<env>` on the events
stream's iterator age (≥ 5 minutes). The alarms that used to live here — API Gateway 5xx,
assignment-Lambda errors, DynamoDB throttling on a literal table name — watched resources
nothing deploys and sat in `INSUFFICIENT_DATA` for ever; they were removed.

The alarms that matter most for the API are in the Fargate stack, on the load balancer's
metrics, because that is where the API's traffic is:

| Alarm | Fires when | What it does |
|---|---|---|
| `experimentation-api-5xx-blue-<env>`, `…-green-<env>` | in a minute, ≥ 5 target 5xx **and** ≥ 5% of that target group's requests; 2 of 3 minutes | emails the topic **and** is in the CodeDeploy deployment group: while either is in ALARM, a deployment stops and rolls back (Part IV) |
| `experimentation-api-healthy-blue-<env>`, `…-green-<env>` | that target group has had no healthy target for 3 minutes | nothing on its own — between deployments the idle colour is empty, so one of the two is always in ALARM by design |
| `experimentation-api-no-healthy-task-<env>` (composite) | **both** healthy alarms are in ALARM | emails: every API request is being answered 503 by the load balancer |
| `experimentation-api-error-logs-<env>` | ≥ 10 log lines containing `ERROR` from the API's tasks in 5 minutes | emails |

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
API_5XX_EXPRESSION = "IF(FILL(e,0) >= 5, FILL(e,0)/r, 0)"
API_5XX_RATE_THRESHOLD = 0.05
```

Three details make these honest. The 5xx alarms count **target** 5xx only
(`HTTPCode_Target_5XX_Count`): the load balancer's own 502/503/504 have no target-group
dimension, which is exactly why the no-healthy-task composite exists — with no task behind
it, the ALB answers 503 itself and the 5xx alarms see nothing. An idle target group's
missing data is not breaching, so it never pages. And one alarm per colour, because
CodeDeploy swaps blue and green on every deployment: an alarm on one group would watch the
wrong one half the time. The thresholds are starting values, to be measured against real
traffic; and how the alarms behave during the hour after a traffic shift, or when one is
already in ALARM before a shift is approved, is the intended design but has not yet been
observed in a real account.

Mapped to the rollback runbook's decision tree (Hour 12): the 5xx-rate box is the 5xx
alarms, "crash loops" is the no-healthy-task composite. What is still **not** alarmed:
p99 latency (`TargetResponseTime`) and scheduler staleness — Exercise E3.4.

**Notifications** are the platform telling _people_ what it did. `NotificationService`
fans out to Slack (Block Kit), email (SendGrid, SMTP fallback) and webhooks, logs every
attempt to `notification_delivery_log`, and is wired into the schedulers that take
consequential actions:

```python
# 📁 backend/app/core/safety_scheduler.py
                                self._notification_service.notify_safety_rollback(
```

```python
# 📁 backend/app/core/rollout_scheduler.py
                self._notification_service.notify_rollout_advanced(
```

```python
# 📁 backend/app/services/notification_service.py
            record = NotificationDeliveryLog(
                event_type=event_type,
                channel=channel,
                recipient=recipient,
                subject=subject,
                status=status,
                error_message=error_message,
                payload=payload,
            )
```

Both are opt-in (`SLACK_ENABLED` + `SLACK_BOT_TOKEN`, `EMAIL_ENABLED` + SendGrid/SMTP
settings) and both degrade to a logged warning when the SDK or config is absent — a failed
Slack post must never fail a safety rollback. The StreamPulse rollout story (Build book
Day 21) is the demo of this loop end to end: rollout advances → Slack; error rate spikes →
safety rollback → Slack.

> **Watch Out!** `is_running` is "activity in the last 30 minutes." The default cadences
> are `SAFETY_CHECK_INTERVAL_MINUTES=5`, `BANDIT_UPDATE_INTERVAL_MINUTES=5`,
> `ROLLOUT_CHECK_INTERVAL_MINUTES=15` and `EXPERIMENT_SCHEDULER_INTERVAL_MINUTES=15` (the
> metrics scheduler also runs every 15), and the demo sets safety and rollout to 1 — so a
> healthy scheduler is always "running". Raise any interval above 30 and `is_running: false`
> becomes normal between ticks. `EXPERIMENT_SCHEDULER_INTERVAL_MINUTES` refuses a value below
> 1, because 0 would make the loop spin without sleeping. The Prometheus gauge
> (`time() − scheduler_last_success_timestamp`) with a per-scheduler threshold is the
> precise version.

> **Did You Know?** The experiment scheduler activates and completes each due experiment
> inside its own savepoint (`db.begin_nested()`). One row the database refuses is rolled back
> on its own, counted in `items_failed`, and the rest of the tick still goes through — the
> run is recorded as `partial` when others succeeded. Before that, one bad experiment undid the whole
> tick, and every other experiment due that minute waited for the next one.

> **By the Way** — `settings.is_test` skips the schedulers entirely in the test profile
> (Build book Day 8). That's why an integration test never sees a scheduler tick, why
> `test_scheduler_lock.py` tests the lock directly, and why a "works in tests, stuck in
> staging" scheduler bug is a category to keep in mind: the loops only run for real when the
> app runs for real.

### Try It Yourself

```bash
docker compose up -d --wait --scale api=3 api            # three replicas (ports will clash — drop the host port mapping first)
docker compose logs api | grep "tick skipped" | head      # two of three skip each tick
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/api/v1/scheduler/health | jq '.[] | {scheduler_name, is_running, last_run_status, consecutive_failures}'
docker compose exec postgres psql -U postgres -d experimentation -c \
  "SELECT scheduler_name, status, started_at, items_processed FROM experimentation.scheduler_runs ORDER BY started_at DESC LIMIT 10;"
curl -s -H 'Authorization: Bearer s3cret' localhost:8000/metrics | grep scheduler_ticks_total
# hold a lock by hand and watch every replica skip
docker compose exec postgres psql -U postgres -d experimentation -c "SELECT pg_advisory_lock(hashtext('experimently.scheduler.safety')); SELECT pg_sleep(120);" &
```

Then set `NOTIFICATION_WEBHOOK_URL` to a request bin and call
`POST /api/v1/scheduler/notify/test` as a superuser: it posts a test event to that webhook
and answers `{"status": "sent" | "failed" | "skipped", ...}` (`skipped` when no webhook URL
is configured). It tests the webhook channel only; Slack and email are exercised by a real
safety rollback or rollout advance.

### Key takeaways

- Five loops, one runner: `pg_try_advisory_lock(hashtext(name))` on a dedicated autocommit
  connection (Aurora kills idle-in-transaction sessions), skip when held, record `success
  / partial / failed` to `scheduler_runs`, three Prometheus metrics, re-raise on error.
- `GET /api/v1/scheduler/health`: running (30-minute window), last status, consecutive
  failures; the staleness gauge is the precise signal.
- Every alarm goes to `experimentation-alerts-<env>`, whose one subscriber is
  `ALARM_EMAIL` — required in staging and prod, placeholder domains refused, and silent
  until the recipient confirms the SNS subscription.
- Alarms that exist: Aurora writer CPU, Redis CPU per node, Kinesis lag (full profile), the
  API's per-colour 5xx alarms (which also roll a deployment back), the no-healthy-task
  composite, and the ERROR-log alarm. Still to add: p99 latency and scheduler staleness.
- Notifications (Slack/email/webhook) are opt-in, logged, and never allowed to fail the
  action they announce.

---

## Part III Workshop

**Q1. A `logger.warning` in `assignment_service.py` uses the standard library. Does it carry
`request_id` in production?**
Yes — `foreign_pre_chain=shared_processors` runs `_inject_context` on stdlib records too;
every line from every logger is JSON with the bound context.

**Q2. The ALB reports a task unhealthy during a 20-second Aurora failover. Is that a bug?**
No — `/health` is readiness and the task genuinely can't serve; it's pulled from rotation
and returns when `SELECT 1` succeeds. `/health/live` stayed 200, so nothing restarted.

**Q3. `/metrics` returns 403 in staging. What's missing?**
`METRICS_TOKEN`. Outside development/test the endpoint fails closed without one; set it and
scrape with `Authorization: Bearer`.

**Q4. Why is `scheduler_ticks_total{status="skipped"}` recorded but skipped ticks aren't in
`scheduler_runs`?**
Prometheus should show the lock working (two of three replicas skipping is health); the run
history is a record of work done, and would be two-thirds noise otherwise.

**Q5. Why must the lock connection be AUTOCOMMIT?**
A session-level advisory lock with an open transaction leaves the connection "idle in
transaction" for the whole tick; Aurora kills those sessions after a timeout, which releases
the lock mid-tick and lets a second replica start the same work.

**Q6. A safety rollback happened but no Slack message arrived. Did the rollback happen?**
Yes — notifications are best-effort and logged; check `notification_delivery_log` for the
`failed` row and its `error_message` (usually `SLACK_ENABLED` false or a bad token).

**Q7. Staging was deployed, an alarm fired, and nobody got an email. Name two causes.**
`ALARM_EMAIL`'s SNS subscription was never confirmed (SNS sends nothing to an unconfirmed
address), or the alarm was one of the per-colour `experimentation-api-healthy-*` alarms,
which have no action by design — only the `no-healthy-task` composite emails. (An unset
`ALARM_EMAIL` is not a cause in staging: synth refuses to run without it.)

**Quiz.** Which one line in `metrics.py` prevents a per-experiment time series for HTTP
requests, and which counter deliberately _does_ have one?
→ `normalize_path` (`/{id}`) in the middleware for `http_requests_total`;
`experiment_assignments_total{experiment_id, variant_id}` carries it on purpose, bounded by
the experiment count.

---

## Exercises

**E3.1 — Follow a request.** Make one `/tracking/assign` call through nginx with a known
`X-Request-ID`, then collect every log line with that ID (nginx access log, API JSON lines)
and the `assignments` row it created; write the four-line "trace" a support engineer would
paste.

**E3.2 — A new metric.** Add `srm_checks_total{result}` (ok / mismatch / skipped) to
`metrics.py`, record it in `srm_service`, expose it, and write the PromQL for "SRM mismatch
rate over 1 h."

**E3.3 — Readiness policy.** Argue for `REDIS_REQUIRED=true` vs `false` in a deployment
where the distributed rate limiter protects a public SDK endpoint behind an ALB with 3–10
tasks; state what breaks in each failure mode.

**E3.4 — The alarms still missing.** The Fargate stack already alarms on the API's 5xx
rate per target group, on no healthy task, and on ERROR log lines (Hour 9). Add what the
rollback runbook's decision tree still has no alarm for: `TargetResponseTime` p99 > 5 s for
3 of 5 minutes, on **each** target group (say why one alarm on the load balancer as a whole
would not do), wired to the existing topic with an ALARM action only; and a scheduler
staleness alarm on `scheduler_last_success_timestamp{name="safety"}` if the Prometheus
metrics are shipped to CloudWatch. State whether either belongs in the CodeDeploy
deployment group's `alarms=` and why.

**E3.5 — Scheduler post-mortem.** Given a `scheduler_runs` extract showing `safety` with
`failed` × 6 consecutive and `error = "database unavailable"` over 90 minutes while
`experiment` shows `success`, explain what was and wasn't affected (which rollbacks didn't
happen; whether experiments still started) and what the alarm should have been.

---

## Solutions

**S3.1**

```text
nginx   10.0.1.5 - "POST /api/v1/tracking/assign" 200 rid=trace-me-123
api     {"level":"info","logger":"backend.app.api.v1.endpoints.tracking","event":"assign …","request_id":"trace-me-123","path":"/api/v1/tracking/assign"}
api     {"level":"info","logger":"backend.app.services.assignment_service","event":"Assigned user u-9 to variant 7c1… in experiment 3f2a…","request_id":"trace-me-123"}
db      assignments: user_id=u-9 experiment_id=3f2a… variant_id=7c1… created_at=2026-09-11T14:02:07Z
```

**S3.2**

```python
srm_checks_total: Counter = Counter("srm_checks_total", "Sample-ratio-mismatch checks by result", ["result"])
# in srm_service after computing: srm_checks_total.labels(result="mismatch" if is_mismatch else "ok").inc()
# skipped (bandit / no assignments): srm_checks_total.labels(result="skipped").inc()
```

PromQL: `sum(rate(srm_checks_total{result="mismatch"}[1h])) / sum(rate(srm_checks_total{result!="skipped"}[1h]))`.

**S3.3** — `false`: a Redis outage leaves every task ready; rate limiting falls back to
_per-task_ memory, so the effective limit becomes N × 6,000/min per IP — degraded
protection, full availability. `true`: a Redis outage makes every task unready, the ALB has
no targets, and the platform is down until failover completes (~30–60 s) — full protection,
no availability. For a public SDK endpoint the first is almost always right; pair it with
an alarm on Redis health so the degraded window is known.

**S3.4** — sketch, inside the Fargate stack's loop over the colours, built by hand like the
5xx alarms (the target group that is not live is attached to no load balancer, so
`tg.metrics.*` refuses it at synth):

```python
latency = cloudwatch.Metric(
    namespace="AWS/ApplicationELB",
    metric_name="TargetResponseTime",
    dimensions_map={"LoadBalancer": self.alb.load_balancer_full_name, "TargetGroup": target_group.target_group_full_name},
    statistic="p99",
    period=Duration.seconds(60),
)
p99 = cloudwatch.Alarm(
    self, f"ApiP99{colour.capitalize()}",
    alarm_name=f"experimentation-api-p99-{colour}-{env_name}",
    metric=latency, threshold=5, evaluation_periods=5, datapoints_to_alarm=3,
    comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
    treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,  # the idle colour has no data
)
p99.add_alarm_action(cloudwatch_actions.SnsAction(alarm_topic))
```

Per colour for the same reason as the 5xx alarms: during a canary the replacement group
carries 10% of the traffic, and a whole-ALB p99 would dilute a slow release in the old
release's numbers. Missing data must be NOT_BREACHING or the idle colour would alarm all the
time. Staleness: a `MathExpression` of `TIME() - s` on the shipped gauge with a threshold of
2 × the safety interval (10 minutes at the default 5).

Whether to add them to the deployment group: every alarm in `alarms=` is polled for the
whole deployment and **stops and rolls back** any deploy while it is in ALARM, and the
deploy workflow refuses to start while one is firing. A p99 alarm there would make latency
a rollback criterion — defensible, but only after its threshold has been measured against
real traffic. The staleness alarm must not go there: a stuck scheduler is not evidence that
the new API release is bad.

**S3.5** — the safety monitor didn't run for 90 minutes: any flag whose error rate crossed
its threshold in that window was _not_ rolled back (check `error_logs` against
`feature_flag_safety_configs` for the window and do it by hand); experiments started/ended
on schedule (experiment scheduler succeeded — the lock connection and the tick's own
session are separate, and this failure was inside the safety tick's queries). The alarm
should have been `time() − scheduler_last_success_timestamp{name="safety"} > 10 min` (2 × the
default 5-minute interval), which would have fired after two missed ticks, not after someone
noticed — and nothing the stacks create today watches that, which is why E3.4 adds it.

---

**Next:** [Part IV — Release Engineering →](./part-4-release-engineering.md)

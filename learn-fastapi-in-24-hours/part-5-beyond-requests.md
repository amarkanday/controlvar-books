# Part V — Beyond Request/Response

### Hours 19–21

[← Part IV](./part-4-sqlalchemy.md) · [Contents](./README.md) · [Part VI →](./part-6-testing-and-operating.md)

Not everything is a request that gets a response. This Part covers the three things the
backend does that don't fit that shape: work on a timer with no request at all (Hour 19), long
-lived connections that push data to clients (Hour 20), and a fast shared store that survives
across requests and processes — and the discipline of degrading gracefully when it isn't
there (Hour 21). Each is where the `async` half of "async web framework" stops being a
formality.

---

## Hour 19 — Lifespan and `asyncio` background tasks (the schedulers)

### What & why

The **lifespan** is one `async` context manager passed to `FastAPI(lifespan=...)`: code
before `yield` runs at startup, code after runs at shutdown. It's the place to start
long-lived things — connection pools, caches, and here the five **background schedulers** —
and to stop them cleanly.

A scheduler is an `asyncio.Task`: `asyncio.create_task(coro)` schedules a coroutine on the
running event loop and returns a handle you can cancel. The loop it runs is the same one
serving requests, which has two consequences:

1. **A tick must not block.** Synchronous work (SQLAlchemy queries) inside an `async`
   scheduler blocks every request for its duration. The experiment/rollout/safety
   schedulers accept that because their ticks are a few short queries; the bandit runner —
   which does Monte Carlo — offloads with `asyncio.to_thread` so the loop stays free.
2. **Shutdown is cancellation.** `task.cancel()` raises `CancelledError` inside the coroutine
   at its next `await`; the loop must let it propagate (not swallow it), and anything holding
   a lock must release it on the way out.

#### Why in-process schedulers, and why they skip under test

A separate worker service would be cleaner; in-process tasks mean the Community Edition is
one container plus a database. The cost is that every replica starts every scheduler — solved
with the advisory lock (Build book, Day 17). And under the test profile the lifespan starts
**nothing**: a `TestClient` used as a context manager would otherwise run real ticks against
the test database in the middle of unrelated tests. The schedulers' behaviour is tested by
calling their tick methods directly.

### In the wild

The lifespan, with its test guard:

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
    ...
    logger.info("Starting bandit scheduler")
    await bandit_scheduler_runner.start()

    yield

    logger.info("Stopping experiment scheduler")
    await experiment_scheduler.stop()
    ...
```

A scheduler's skeleton — `start` creates the task, `stop` cancels and awaits it, the loop
runs one locked tick then sleeps:

```python
# 📁 backend/app/core/scheduler.py
class ExperimentScheduler:
    """Handles scheduled tasks for experiments."""

    def __init__(self, interval_minutes: int = 15):
        self.interval_minutes = interval_minutes
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self._notification_service = NotificationService()

    async def start(self):
        """Start the scheduler."""
        if self.is_running:
            logger.warning("Scheduler is already running")
            return

        self.is_running = True
        self.task = asyncio.create_task(self._run_scheduler())
        logger.info(f"Experiment scheduler started with {self.interval_minutes} minute interval")

    async def stop(self):
        """Stop the scheduler."""
        if not self.is_running:
            return

        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        logger.info("Experiment scheduler stopped")

    async def _run_scheduler(self):
        """Run the scheduler loop."""
        while self.is_running:
            try:
                # One tick under the advisory lock; records the run and
                # skips when another replica holds the lock.
                await run_locked_tick(SCHEDULER_NAME, self.process_scheduled_experiments)

                # Wait for the next interval
                await asyncio.sleep(self.interval_minutes * 60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in experiment scheduler: {str(e)}")
                # Wait a bit before trying again
                await asyncio.sleep(60)
```

Three asyncio details to copy: `except asyncio.CancelledError: break` lets shutdown end the
loop (a bare `except Exception` would _not_ catch it in Python ≥ 3.8, because
`CancelledError` is a `BaseException` — but being explicit documents intent); the generic
`except` logs and sleeps 60 s so one bad tick doesn't hot-loop; and `stop()` awaits the
cancelled task so the lifespan doesn't exit with a task still unwinding.

The bandit runner is the one that offloads its work — a synchronous scheduler with its own
session, run in a thread:

```python
# 📁 backend/app/core/bandit_scheduler.py
class BanditSchedulerRunner:
    """
    Asyncio wrapper that runs ``BanditScheduler.run_once()`` periodically.

    Each pass opens a fresh ``SessionLocal()`` session and executes the
    synchronous scheduler in a worker thread so the event loop is never
    blocked.  Errors are logged and the loop keeps going.
    """
    ...
    async def run_once(self) -> Dict[str, int]:
        """Execute a single scheduler pass in a worker thread."""
        result = await asyncio.to_thread(self._run_sync)
        self.run_count += 1
        self.last_result = result
        return result

    @staticmethod
    def _run_sync() -> Dict[str, int]:
        """Open a session, run the scheduler once, close the session."""
        from backend.app.db.session import SessionLocal

        db = SessionLocal()
        try:
            return BanditScheduler(db).run_once()
        finally:
            db.close()
```

And the shared runner that gives every tick the same three guarantees — lock, run record,
metrics — while letting cancellation through untouched:

```python
# 📁 backend/app/core/scheduler_tick.py
        try:
            raw = await tick()
            result = TickResult.coerce(raw)
            ...
        except asyncio.CancelledError:
            # Shutdown: release the lock (context manager) and propagate
            # without recording a bogus "failed" run.
            raise
        except Exception as exc:  # noqa: BLE001 - recorded then re-raised
            status = STATUS_FAILED
            error = str(exc) or exc.__class__.__name__
            exc_to_raise = exc
```

> **Watch Out!** `asyncio.sleep(interval)` _after_ the tick means "interval between the end
> of one tick and the start of the next," not "every N minutes on the clock." For due-based,
> idempotent work that's fine. For anything that must run at a wall-clock time, compute the
> next deadline and sleep until it.

> **By the Way.** FastAPI also has `BackgroundTasks` — per-request work run after the
> response is sent (`background_tasks.add_task(fn, ...)`). It's imported in
> `experiments.py` but the platform doesn't lean on it: post-response work that must survive
> a crashed worker belongs in a scheduler tick or a queue, not in the request's process.

### Try It Yourself

Run a scheduler tick by hand, and watch cancellation propagate:

```python
import asyncio
from backend.app.core.scheduler import ExperimentScheduler

async def main():
    s = ExperimentScheduler(interval_minutes=60)
    print(await s.process_scheduled_experiments())     # one tick, no lock, no loop
    await s.start()
    await asyncio.sleep(0.5)
    await s.stop()                                      # cancels; the loop's `break` runs
    print("stopped cleanly:", s.task is None)

asyncio.run(main())
```

Then read `backend/tests/unit/core/test_scheduler_tick.py` to see how the shared runner is
tested with a fake tick and a real advisory lock.

### Common mistakes

- **Doing slow sync work in an `async` tick** — the whole API stalls. `asyncio.to_thread`.
- **Swallowing `CancelledError`** with `except Exception` in older Python or a
  `except BaseException` — the task never ends and shutdown hangs.
- **Starting schedulers at import time** instead of in the lifespan — tests and scripts that
  import the app get background tasks they didn't ask for.
- **Sharing one `Session` across ticks.** Open one per tick, close in `finally`.

### Key takeaways

- The lifespan starts and stops long-lived tasks; under the test profile it starts none.
- A scheduler = `create_task` + a `while` loop of `run_locked_tick(tick)` + `sleep`;
  `stop()` cancels and awaits.
- Let `CancelledError` propagate; log-and-sleep on other errors.
- Offload CPU-heavy or long sync work with `asyncio.to_thread`; each tick owns its session.

---

## Hour 20 — WebSockets and Server-Sent Events

### What & why

Two ways to push data to a client over HTTP:

- **WebSocket** — a bidirectional, long-lived connection upgraded from HTTP. The results
  dashboard opens one per experiment and receives a fresh snapshot every 30 seconds without
  polling; it can also send `{"action": "refresh"}` to ask for one now.
- **Server-Sent Events (SSE)** — a one-way stream of `data: ...\n\n` chunks over a normal
  HTTP response with `text/event-stream`. Simpler than a WebSocket when the client only
  listens; the bulk-toggle audit stream uses it.

Both live on the event loop and both change three assumptions the request/response world
makes:

1. **Authentication happens once, at the handshake**, and browsers can't set headers on a
   WebSocket handshake — so the token has to travel another way (a subprotocol entry, a
   query parameter).
2. **A dependency-provided session must not be held for the connection's life.** A
   WebSocket that pins a pooled connection for an hour starves the pool.
3. **Errors are close codes, not status codes.** An unauthorised WebSocket is _accepted_ then
   closed with an application code (`4401`) so the client can read it; closing before
   accepting surfaces as an opaque 403 handshake failure.

### In the wild

The WebSocket endpoint — auth by three routes, early session release, a receive-with-timeout
loop that doubles as a periodic timer:

```python
# 📁 backend/app/api/v1/endpoints/websocket_results.py
WS_CLOSE_UNAUTHORIZED = 4401
WS_AUTH_SUBPROTOCOL = "experimently.bearer"
RESULTS_STREAM_INTERVAL_SECONDS = int(os.getenv("RESULTS_STREAM_INTERVAL_SECONDS", "30"))

# Module-level singleton so all requests share the same ConnectionManager state.
manager = ConnectionManager()


def _extract_ws_token(websocket: WebSocket, token: Optional[str]) -> Optional[str]:
    """Subprotocol first, then ``Authorization: Bearer``, then ``?token=``."""
    from_subprotocol = _token_from_subprotocol(websocket)
    if from_subprotocol:
        return from_subprotocol
    authorization = websocket.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    if token:
        return token
    return None


@router.websocket("/ws/experiments/{experiment_id}/results")
async def stream_results(
    websocket: WebSocket,
    experiment_id: str,
    token: Optional[str] = Query(None, description="Access token (alternative to the bearer header)"),
    db: Session = Depends(deps.get_db),
) -> None:
    from backend.app.db.session import SessionLocal

    # Sync SQLAlchemy work runs in the threadpool, like a sync HTTP dependency.
    subprotocol = _accepted_subprotocol(websocket)
    if not await run_in_threadpool(_authenticate_websocket, websocket, token, db):
        # Accept first so the client receives the application close code
        # (closing before accept surfaces as a bare HTTP 403 handshake error).
        await websocket.accept(subprotocol=subprotocol)
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="Unauthorized")
        return
    # The session was only needed for authentication; release its connection
    # now rather than holding it for the lifetime of the socket.
    db.close()

    await manager.connect(websocket, experiment_id, subprotocol=subprotocol)
    streaming_service = ResultsStreamingService(SessionLocal)

    try:
        # Send initial snapshot immediately on connection
        snapshot = await streaming_service.get_live_snapshot(experiment_id)
        await websocket.send_text(json.dumps(snapshot))

        # Main loop: wait for client messages, send periodic updates on timeout
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=float(RESULTS_STREAM_INTERVAL_SECONDS))
                try:
                    msg = json.loads(data)
                except json.JSONDecodeError:
                    msg = {}

                action = msg.get("action")
                if action == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
                elif action == "refresh":
                    snapshot = await streaming_service.get_live_snapshot(experiment_id)
                    await websocket.send_text(json.dumps(snapshot))
                ...
            except asyncio.TimeoutError:
                # No client message within the interval: push a periodic snapshot
                ...
```

The `asyncio.wait_for(receive_text(), timeout=30)` trick is worth stealing: one loop handles
both "the client said something" and "30 seconds passed, push an update," without a second
task. `run_in_threadpool` for the synchronous auth chain keeps the loop free during the
database lookup — the same reason `def` handlers are offloaded.

`ResultsStreamingService(SessionLocal)` receives the _factory_, not a session: it opens a
short-lived session per snapshot, which is the correct ownership for a connection that lives
for minutes.

The manager that tracks subscribers per experiment, with an `asyncio.Lock` (not a
`threading.Lock` — everything here is on one loop) and dead-connection pruning on broadcast:

```python
# 📁 backend/app/services/websocket_manager.py
class ConnectionManager:
    def __init__(self) -> None:
        # experiment_id -> set of active WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, experiment_id: str, subprotocol: Optional[str] = None) -> None:
        """Accept a new WebSocket connection and register it for the given experiment."""
        await websocket.accept(subprotocol=subprotocol)
        async with self._lock:
            self._connections[experiment_id].add(websocket)

    async def broadcast(self, experiment_id: str, message: dict) -> None:
        payload = json.dumps(message)
        dead: Set[WebSocket] = set()
        async with self._lock:
            conns = set(self._connections.get(experiment_id, set()))
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception as exc:
                dead.add(ws)
        for ws in dead:
            await self.disconnect(ws, experiment_id)
```

(Copying the set under the lock and sending outside it is the standard way to avoid holding
a lock across an `await` that might take a while.)

The SSE endpoint — an async generator wrapped in a `StreamingResponse`:

```python
# 📁 backend/app/api/v1/endpoints/bulk_toggle.py
async def stream_audit_logs(
    limit: int = Query(50, ge=1, le=100),
    entity_type: Optional[str] = Query(None),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
):
    """
    Returns recent audit log entries as Server-Sent Events.
    Streams up to `limit` entries (default 50, max 100) then closes.
    """

    async def event_generator():
        ...
        logs, _ = AuditService.get_audit_logs(db, limit=limit, entity_type=entity_type_enum, page=1)
        for i, log in enumerate(logs):
            event_data = json.dumps({"id": str(log.id), "timestamp": ..., "action_type": log.action_type, ...})
            yield f"data: {event_data}\n\n"
            await asyncio.sleep(0.01)
        yield 'data: {"event": "end"}\n\n'

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

`X-Accel-Buffering: no` tells nginx (the dashboard image's proxy) not to buffer the stream;
without it the client sees everything at once when the response ends. The wire format is
exactly `data: <json>\n\n` per event; a terminal `end` event lets the client close cleanly.

The client side of the WebSocket, for the shape of the contract:

```typescript
// 📁 frontend/src/hooks/useExperimentStream.ts
/**
 * Custom React hook that manages a WebSocket connection to the experiment
 * results streaming endpoint. Handles connection lifecycle, auto-reconnect,
 * and client-to-server message actions (ping, refresh).
 */
```

> **Watch Out!** A WebSocket endpoint is `@router.websocket(...)`, not `@router.get`; it
> receives a `WebSocket`, not a `Request`; `Depends` works (that's how `db` arrives) but
> security schemes that read headers won't see a browser's token — hence the hand-rolled
> extraction. And `HTTPException` raised after `accept()` doesn't produce an HTTP response;
> close with a code instead.

> **Did You Know?** The `experimently.bearer` subprotocol trick works because RFC 6455 lets a
> client offer a list of subprotocols in `Sec-WebSocket-Protocol`; the server must echo one it
> accepts. The dashboard offers `experimently.bearer, <token>`; the server reads the token as
> the second entry and echoes `experimently.bearer`. It keeps the token out of the URL (and
> out of access logs), which `?token=` can't.

### Try It Yourself

Talk to both streams from the shell:

```bash
pip install websockets >/dev/null 2>&1
python - <<PY
import asyncio, json, websockets
async def main():
    uri = "ws://localhost:8000/api/v1/ws/experiments/<EXPERIMENT_ID>/results?token=$TOKEN"
    async with websockets.connect(uri) as ws:
        print("snapshot:", json.loads(await ws.recv())["status"])
        await ws.send(json.dumps({"action": "ping"})); print(await ws.recv())
        await ws.send(json.dumps({"action": "refresh"})); print(json.loads(await ws.recv())["event"])
asyncio.run(main())
PY
# a bad token: accepted then closed with 4401
python -c "
import asyncio, websockets
async def m():
    try:
        async with websockets.connect('ws://localhost:8000/api/v1/ws/experiments/x/results?token=bad') as ws: await ws.recv()
    except websockets.ConnectionClosed as e: print(e.code, e.reason)
asyncio.run(m())"
# SSE
curl -sN "$API/feature-flags/audit-logs/stream?limit=3" -H "Authorization: Bearer $TOKEN" | head -8
```

### Common mistakes

- **Holding the request's `db` for the socket's lifetime.** Authenticate, `db.close()`, use a
  factory per snapshot.
- **Closing before `accept()`** on auth failure — the client gets a bare 403 and no reason.
- **Holding an `asyncio.Lock` across a slow `await send`.** Copy under the lock, send outside.
- **Forgetting `X-Accel-Buffering: no`** behind nginx — the SSE "stream" arrives all at once.

### Key takeaways

- WebSocket: `@router.websocket`, token via subprotocol / header / `?token=`, accept-then-close
  with `4401` on failure, release the auth session, `wait_for(receive, timeout)` as a timer.
- `ConnectionManager` groups sockets per experiment behind an `asyncio.Lock` and prunes dead
  ones on broadcast.
- SSE: an async generator → `StreamingResponse(media_type="text/event-stream")`, `data:
  ...\n\n` framing, `X-Accel-Buffering: no`.

---

## Hour 21 — Redis: caching and rate limiting with graceful fallback

### What & why

Redis is the one piece of shared, fast, ephemeral state the API uses: **rate-limit
counters** (so a limit is per IP across replicas), **the results cache** (so a dashboard
refresh doesn't recompute Fisher's test), and a **flag/experiment cache** hook in the
dependencies. The design principle, stated in `/health/ready`'s docstring and honoured
everywhere Redis is touched: **the application degrades gracefully without it.** Rate
limiting falls back to an in-memory window, caching is skipped, readiness reports Redis but
doesn't fail on it unless `REDIS_REQUIRED=true`.

That principle has a cost — a rate limit in the in-memory fallback is per _process_, not per
_fleet_ — and a benefit: a Redis outage never becomes an API outage.

#### Why fixed-window in Redis and sliding-window in memory

The Redis limiter is `INCR` + `EXPIRE`: atomic, one round trip, trivially shared across
replicas, and slightly lenient at window edges (a burst at 59 s and 61 s counts in two
windows). The in-memory fallback is a deque of timestamps per key — exact, but only for this
process. The choice matches the guarantees each store can cheaply give.

### In the wild

The Redis limiter with lazy connection and fallback on _any_ error:

```python
# 📁 backend/app/middleware/rate_limiter.py
class RedisRateLimiter:
    """
    Fixed-window rate limiter backed by Redis ``INCR`` + ``EXPIRE``.

    On any Redis error the limiter transparently falls back to the
    in-memory :class:`SlidingWindowRateLimiter` so that the application
    keeps serving traffic even when Redis is temporarily unreachable.
    """

    def _get_redis(self):
        """Lazy-connect to Redis on first call."""
        if self._redis_client is None and self._redis_available:
            try:
                import redis as redis_lib

                self._redis_client = redis_lib.Redis(
                    host=self._redis_host, port=self._redis_port, password=self._redis_password or None,
                    db=self._redis_db, socket_connect_timeout=2, socket_timeout=1, decode_responses=True,
                )
                # Test connectivity
                self._redis_client.ping()
                self._redis_available = True
            except Exception as exc:
                logger.warning("Rate limiter Redis unavailable, using in-memory fallback: %s", exc)
                self._redis_client = None
                self._redis_available = False
        return self._redis_client

    def is_allowed(self, key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
        client = self._get_redis()
        if client is None or not self._redis_available:
            return self._fallback.is_allowed(key, limit, window_seconds)

        redis_key = f"ratelimit:{key}"
        try:
            pipe = client.pipeline(transaction=True)
            pipe.incr(redis_key)
            pipe.ttl(redis_key)
            results = pipe.execute()

            current_count = results[0]
            ttl = results[1]

            # First request in this window — set expiry
            if ttl == -1:
                client.expire(redis_key, window_seconds)

            if current_count > limit:
                return False, 0

            remaining = limit - current_count
            return True, remaining

        except Exception as exc:
            logger.warning("Redis rate-limit error, falling back to in-memory: %s", exc)
            self._redis_available = False
            return self._fallback.is_allowed(key, limit, window_seconds)
```

Note the tight timeouts (`socket_connect_timeout=2`, `socket_timeout=1`): a slow Redis must
not slow every request, so the limiter would rather fall back than wait. The pipeline makes
`INCR` + `TTL` one round trip; `ttl == -1` means "no expiry yet" and marks the first hit of a
window.

The in-memory fallback, exact and thread-safe:

```python
# 📁 backend/app/middleware/rate_limiter.py
class SlidingWindowRateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: Dict[str, deque] = defaultdict(deque)

    def is_allowed(self, key: str, limit: int, window_seconds: int) -> Tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            window = self._windows[key]

            # Evict timestamps outside the current window
            while window and window[0] <= cutoff:
                window.popleft()

            count = len(window)
            if count >= limit:
                return False, 0

            window.append(now)
            return True, limit - count - 1
```

(`threading.Lock`, not `asyncio.Lock`, because the middleware may be invoked from threadpool
workers; `time.monotonic()` so a clock adjustment can't corrupt the window.)

The results cache: a `CacheService` that is _disabled_ when Redis isn't there, so callers
never handle connection errors:

```python
# 📁 backend/app/api/v1/endpoints/results.py
def _get_cache_service() -> CacheService:
    """
    Attempt to create a synchronous Redis-backed CacheService.

    Returns a disabled CacheService (no Redis client) if Redis is not
    reachable or not configured, so callers never have to handle
    connection errors explicitly.
    """
    try:
        import redis as redis_lib
        from backend.app.core.config import settings

        r = redis_lib.Redis(host=settings.REDIS_HOST, port=int(settings.REDIS_PORT), db=0)
        # Quick ping to verify the connection is alive.
        r.ping()
        return CacheService(redis_client=r)
    except Exception:
        return CacheService(redis_client=None)
```

```python
# 📁 backend/app/services/cache.py
class CacheService:
    """Service for handling caching operations."""

    def __init__(self, redis_client: Optional[Redis] = None):
        self.redis = redis_client
        self.enabled = bool(redis_client)

    def get(self, key: str) -> Optional[Any]:
        if not self.enabled:
            return None
        try:
            value = self.redis.get(key)
            return value.decode() if value else None
        except Exception as e:
            logger.error(f"Error getting value from cache: {e}")
            return None

    def set(self, key: str, value, expire: Optional[int] = None) -> bool:
        if not self.enabled:
            return False
        try:
            return bool(self.redis.set(key, value, ex=expire))
        except Exception as e:
            logger.error(f"Error setting value in cache: {e}")
            return False
```

and its use, with a **status-aware TTL** and a key that includes every parameter that changes
the answer:

```python
# 📁 backend/app/api/v1/endpoints/results.py
    cache_key = f"results:{experiment_id}:{confidence_level}:{correction_method}:{breakdown or ''}"
    ...
    # --- Cache write ---
    try:
        cache = _get_cache_service()
        running_statuses = {"running", "active", "RUNNING", "ACTIVE"}
        ttl = 300 if raw_status in running_statuses else 86400
        cache.set(cache_key, response.model_dump_json(), expire=ttl)
    except Exception:
        pass  # Cache write failure is non-fatal
```

The key rule is the important one: `confidence_level`, `correction_method` and `breakdown`
all change the result, so they're in the key; a cache keyed on the experiment alone would
serve a 95%-confidence result to a 99% request. Invalidation is explicit
(`POST /results/{id}/invalidate-cache`) and pattern-based on writes (the experiment endpoints
delete `experiments:{owner}:*` after a change).

> **Watch Out!** `_get_cache_service()` pings Redis on every call — a round trip per results
> request. It's acceptable at dashboard volumes and would not be on an SDK path; the SDK
> paths deliberately don't touch Redis except through the rate limiter's long-lived client.
> If you add caching to a hot path, hold one client, don't ping per request.

> **By the Way.** `deps.get_cache_control` is the older, async, Redis-pool-based cache hook
> that the experiment endpoints receive as `cache_control`. It's disabled unless
> `settings.CACHE_ENABLED` (true only in `ProdSettings`), and the harness overrides it to
> `CacheControl(enabled=False, skip=True)`. Two cache mechanisms in one codebase is a known
> wart; prefer the `CacheService` shape for new code.

### Try It Yourself

Watch the limiter and the cache with and without Redis:

```bash
curl -si $API/experiments/ -H "Authorization: Bearer $TOKEN" | grep -i x-ratelimit
docker compose exec redis redis-cli --scan --pattern 'ratelimit:*' | head -3
docker compose exec redis redis-cli --scan --pattern 'results:*' | head -3

docker compose stop redis
curl -si $API/experiments/ -H "Authorization: Bearer $TOKEN" | grep -i "HTTP/\|x-ratelimit"    # still 200, in-memory limiter
curl -s localhost:8000/health/ready | python -m json.tool | grep -A3 redis                     # reported, not fatal
docker compose start redis
```

Then read `resolve_rate_limit` and its tests
(`backend/tests/unit/middleware/test_rate_limiter*.py`) — a pure function tested without
Redis at all.

### Common mistakes

- **Letting a Redis error become an API error.** Every Redis touch is wrapped; the fallback
  is the feature.
- **A cache key that omits a parameter** that changes the answer. Serve-wrong-data bugs are
  worse than cache misses.
- **No TTL.** Every `set` here has an `ex=`; a key without expiry outlives the data it
  cached.
- **Per-request connections on hot paths.** Hold a client; set short timeouts.

### Key takeaways

- Redis backs rate limits (fixed window, `INCR`+`EXPIRE`) and the results cache; both fall
  back (in-memory limiter, disabled cache) rather than fail.
- Tight socket timeouts so a slow Redis can't slow the API.
- Cache keys include every parameter that changes the result; TTLs are status-aware; caches
  round-trip through the response model so stale shapes fail validation.
- `REDIS_REQUIRED=true` is the only way Redis affects readiness.

---

## Part V Workshop

**Q1. Why doesn't the lifespan start the schedulers under the test profile?**
A `TestClient` used as a context manager runs the lifespan; real ticks would then execute
against the test database mid-suite, patching and opening connections unpredictably.
Scheduler logic is tested by calling tick methods directly.

**Q2. What happens on `stop()` if a tick is mid-query?**
`task.cancel()` raises `CancelledError` at the tick's next `await`; `run_locked_tick`
re-raises it (releasing the advisory lock via the context manager) without recording a
failed run; the scheduler loop's `except CancelledError: break` ends the loop; `stop()`
awaits the task. A sync query in flight completes first — cancellation is cooperative.

**Q3. Why does the WebSocket handler `accept()` before closing an unauthorised connection?**
So the client receives the application close code (`4401`, "Unauthorized"). Closing before
accepting surfaces as a bare HTTP 403 during the handshake, with no reason a client can act
on.

**Q4. Three ways a token can reach the WebSocket endpoint — which is preferred and why?**
Subprotocol (`Sec-WebSocket-Protocol: experimently.bearer, <token>`), `Authorization: Bearer`
header, `?token=`. The subprotocol is preferred for browsers: they can't set headers on the
handshake, and a query parameter puts the token in URLs and access logs.

**Q5. Redis is down. What happens to (a) rate limits, (b) results caching, (c) `/health/ready`?**
(a) In-memory sliding window per process — limits still apply, per replica. (b) Disabled
`CacheService`; every request recomputes. (c) Redis reported as unhealthy; readiness still
200 unless `REDIS_REQUIRED=true`.

**Q6. Why is the results cache keyed on `confidence_level` and `correction_method`?**
Both change the computed numbers; a key on the experiment id alone would return one request's
result to a different request. The key must include every input that affects the output.

**Quiz.** `asyncio.wait_for(websocket.receive_text(), timeout=30)` — what are the two
outcomes and what does each mean for the loop?
→ It returns a message (handle `ping`/`refresh`) or raises `asyncio.TimeoutError` (30 s
passed with no client message → push a periodic snapshot). One loop, two triggers.

---

## Exercises

**E5.1 — A new scheduler.** Sketch a `RetentionScheduler` that deletes `raw_metrics` older
than 90 days once a day, using the house skeleton and `run_locked_tick`. What does its tick
return, and where would its cadence setting live?

**E5.2 — Offload.** The retention tick might delete millions of rows. Rewrite the tick to run
the delete in a thread (`asyncio.to_thread`) in batches of 10,000 with a commit per batch, and
explain why batching matters for the advisory lock's holder.

**E5.3 — SSE.** Write an SSE endpoint that streams the safety scheduler's last ten runs (from
`scheduler_runs`) and then a live `{"event": "tick"}` every time a new run appears, polling
every 5 seconds for up to 60 seconds. Include the headers that make it work behind nginx.

**E5.4 — Close codes.** A WebSocket client sends `{"action": "subscribe", "experiment_id":
"…"}` for an experiment the user may not read. Design the response: accept? close code?
message? Justify against how HTTP would have answered.

**E5.5 — Cache design.** The flag-evaluate endpoint is asked to cache evaluations in Redis.
Propose the key, the TTL, what must invalidate it, and one reason the team might refuse.

---

## Solutions

**S5.1**

```python
SCHEDULER_NAME = "retention"

class RetentionScheduler:
    def __init__(self, interval_minutes: Optional[int] = None):
        self.interval_minutes = interval_minutes or settings.RETENTION_CHECK_INTERVAL_MINUTES   # new Settings field, default 1440
        self.is_running = False
        self.task: Optional[asyncio.Task] = None

    async def start(self): ...   # as ExperimentScheduler
    async def stop(self): ...

    async def _run_scheduler(self):
        while self.is_running:
            try:
                await run_locked_tick(SCHEDULER_NAME, self.purge_old_metrics)
                await asyncio.sleep(self.interval_minutes * 60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("retention scheduler: %s", e)
                await asyncio.sleep(60)

    async def purge_old_metrics(self) -> Dict[str, int]:
        cutoff = datetime.utcnow() - timedelta(days=settings.RAW_METRICS_RETENTION_DAYS)
        db = SessionLocal()
        try:
            deleted = db.query(RawMetric).filter(RawMetric.timestamp < cutoff).delete(synchronize_session=False)
            db.commit()
            return {"items_processed": deleted, "items_failed": 0}
        except Exception:
            db.rollback(); raise
        finally:
            db.close()
```

The tick returns `{items_processed, items_failed}` for the run record; the cadence is a
`Settings` field like `SAFETY_CHECK_INTERVAL_MINUTES`. Add `"retention"` wherever
`SchedulerName` enumerates the five.

**S5.2**

```python
    async def purge_old_metrics(self) -> Dict[str, int]:
        return await asyncio.to_thread(self._purge_sync)

    def _purge_sync(self) -> Dict[str, int]:
        cutoff = datetime.utcnow() - timedelta(days=settings.RAW_METRICS_RETENTION_DAYS)
        total = 0
        db = SessionLocal()
        try:
            while True:
                ids = [r.id for r in db.query(RawMetric.id).filter(RawMetric.timestamp < cutoff).limit(10_000)]
                if not ids:
                    break
                db.query(RawMetric).filter(RawMetric.id.in_(ids)).delete(synchronize_session=False)
                db.commit()
                total += len(ids)
            return {"items_processed": total, "items_failed": 0}
        finally:
            db.close()
```

Batching keeps each transaction short (less lock contention on the table, less WAL per
commit) and — because the tick holds the `retention` advisory lock for its whole duration —
keeps the _tick_ bounded so a replica that skipped isn't waiting an hour for the next chance;
`to_thread` keeps the event loop serving requests during the deletes.

**S5.3**

```python
@router.get("/scheduler/safety/stream")
async def stream_safety_runs(current_user: User = Depends(deps.get_current_superuser)):
    async def gen():
        seen: Optional[datetime] = None
        db = SessionLocal()
        try:
            runs = db.query(SchedulerRun).filter_by(scheduler_name="safety").order_by(SchedulerRun.started_at.desc()).limit(10).all()
            for run in reversed(runs):
                yield f"data: {json.dumps({'status': run.status, 'started_at': run.started_at.isoformat()})}\n\n"
                seen = run.started_at
        finally:
            db.close()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            await asyncio.sleep(5)
            db = SessionLocal()
            try:
                latest = db.query(SchedulerRun).filter_by(scheduler_name="safety").order_by(SchedulerRun.started_at.desc()).first()
            finally:
                db.close()
            if latest and (seen is None or latest.started_at > seen):
                seen = latest.started_at
                yield f"data: {json.dumps({'event': 'tick', 'status': latest.status})}\n\n"
        yield 'data: {"event": "end"}\n\n'

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

(A session per poll, never held across the `sleep`.)

**S5.4** — Accept the socket (the handshake authenticated the _user_), then answer the
message with an error event rather than closing: `{"event": "error", "code": 403, "detail":
"You don't have permission to view experiments"}` — the same sentence
`get_permission_error_message` would produce over HTTP — and keep the connection open for
other subscriptions. Close with an application code only when the _connection_ is invalid
(bad token → `4401`), mirroring HTTP: 401 is about the credential, 403 is about one
resource. Using a close code for a per-message authorization failure would make the client
reconnect for every denied subscription.

**S5.5** — Key: `flag:{flag_key}:{user_id}:{sha256(sorted context json)}` — the context
changes the answer, so it must be in the key (hashed to bound its length). TTL: short, 60 s
or less — flag changes must propagate quickly (the SDK already caches for 300 s client-side).
Invalidate: on any `PUT`/`activate`/`deactivate`/rollback of that flag, delete
`flag:{flag_key}:*` (pattern delete via `scan_iter`). A reason to refuse: every evaluation
must still write a `RawMetric` (safety divides by evaluations), so a cache hit would have to
record metrics anyway — the query it saves is a single indexed row read, and the Redis round
trip plus the invalidation surface may cost more than it saves. The team moved the hot path
to Lambda + DynamoDB instead.

---

**Next:** [Part VI — Testing & Operating →](./part-6-testing-and-operating.md)

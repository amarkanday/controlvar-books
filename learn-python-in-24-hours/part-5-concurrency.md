# Part V — Concurrency

### Hours 18–20

[← Part IV](./part-4-idioms.md) · [Contents](./README.md) · [Part VI →](./part-6-in-practice.md)

Python runs one thing at a time per thread, and the interpreter's global lock means threads
don't run Python code in parallel. That sounds limiting and mostly isn't, because a web
backend spends its life _waiting_ — for the database, for Redis, for a client — and waiting
is what `async` and threads are for. This Part is the event loop and the `async`/`await`
syntax (Hour 18), the tasks that run alongside requests and how they're cancelled and
offloaded (Hour 19), and the threads and locks that still matter, plus the `ContextVar` that
keeps per-request state straight (Hour 20).

---

## Hour 18 — `async`/`await` and the event loop

### What & why

An **event loop** is a scheduler for **coroutines** — functions declared `async def` that
can pause at `await` and let something else run. A coroutine pauses only where it says
`await`; between awaits it runs to completion without interruption. That's cooperative
multitasking: no locks needed for data that's only touched between awaits, and no
parallelism — one coroutine runs at a time.

The rules, in the order people break them:

1. **Calling an `async def` doesn't run it** — it returns a coroutine object. You must
   `await` it (or schedule it as a task, Hour 19). A forgotten `await` is a no-op that
   emits a warning at garbage collection.
2. **`await` only works inside `async def`.** A synchronous function can't await; it has
   to be async itself or hand off to the loop (`asyncio.run(...)` at the top level).
3. **Blocking calls block the loop.** `time.sleep(5)`, a synchronous database query, a
   CPU-heavy computation inside a coroutine freezes _every_ coroutine for that long. The
   fixes are `await asyncio.sleep(5)`, an async driver, or offloading to a thread
   (`asyncio.to_thread`, `run_in_threadpool`).
4. **`async with` / `async for`** are the async versions of `with`/`for` for objects whose
   enter/exit or iteration awaits (an `asyncio.Lock`, an async generator).

FastAPI runs on the event loop. An `async def` endpoint runs _on_ it (so its synchronous
SQLAlchemy calls block briefly — the platform accepts this for short queries); a plain `def`
endpoint is run in a threadpool automatically so it can't block the loop. Both are correct;
the codebase's rule is _consistent per module_.

### In the wild

An `async def` endpoint that awaits nothing — it's async for consistency with its
neighbours, and its SQLAlchemy calls run on the loop:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
async def assign_user_to_experiment(
    request: AssignmentRequest = Body(..., description="Assignment request data"),
    db: Session = Depends(deps.get_db),
    api_key_info: Dict[str, Any] = Depends(deps.get_api_key),
) -> VariantAssignmentResponse:
    experiment = db.query(Experiment).filter(...).first()        # sync, blocks the loop briefly
```

A plain `def` endpoint — the results computation is heavier, so it's offloaded to the
threadpool by FastAPI:

```python
# 📁 backend/app/api/v1/endpoints/results.py
@router.get("/{experiment_id}", response_model=ExperimentResultsResponse)
def get_experiment_results(
    experiment_id: UUID,
    ...
) -> ExperimentResultsResponse:
```

An `async def` with real awaits — the safety tick awaits the service's async methods:

```python
# 📁 backend/app/core/safety_scheduler.py
    async def check_feature_flags_safety(self) -> Dict[str, int]:
        ...
            for feature_flag in active_flags:
                try:
                    config = await safety_service.async_get_feature_flag_safety_config(feature_flag.id)
                    if not config.enabled:
                        continue

                    safety_check = await safety_service.check_feature_flag_safety(feature_flag.id)
                    ...
                    rollback_result = await safety_service.async_rollback_feature_flag(...)
```

Each `await` is a point where another coroutine — a request handler, another scheduler —
may run. Between them the loop body runs uninterrupted.

`await asyncio.sleep`, never `time.sleep`, in the loop:

```python
# 📁 backend/app/core/scheduler.py
                await run_locked_tick(SCHEDULER_NAME, self.process_scheduled_experiments)

                # Wait for the next interval
                await asyncio.sleep(self.interval_minutes * 60)
```

`async with` on an asyncio lock, and the explicit hand-off of sync work to a thread when a
coroutine must call blocking code:

```python
# 📁 backend/app/services/websocket_manager.py
        async with self._lock:
            self._connections[experiment_id].add(websocket)
```

```python
# 📁 backend/app/api/v1/endpoints/websocket_results.py
    # Sync SQLAlchemy work runs in the threadpool, like a sync HTTP dependency.
    subprotocol = _accepted_subprotocol(websocket)
    if not await run_in_threadpool(_authenticate_websocket, websocket, token, db):
```

The async dependencies that must be awaited (or `Depends`-ed) — the `CLAUDE.md` rule in
code:

```python
# 📁 backend/app/api/deps.py
async def can_update_feature_flag(
    feature_flag: FeatureFlag = Depends(get_feature_flag_access),
    current_user: User = Depends(get_current_user),
) -> bool:
```

Called by hand, `deps.can_update_feature_flag(...)` returns a coroutine and checks nothing;
`await deps.can_update_feature_flag(...)` runs it. That's the "forgotten `await`" trap in
its most dangerous form — a permission check that silently passes.

`asyncio.run` at the top level of a script or a shell, to drive a coroutine from sync code:

```python
import asyncio
from backend.app.core.scheduler import ExperimentScheduler
print(asyncio.run(ExperimentScheduler().process_scheduled_experiments()))
```

> **Watch Out!** `async def` + synchronous SQLAlchemy is the codebase's accepted trade, not
> a recommendation. Under one uvicorn worker, a 50 ms query inside an `async def` handler is
> 50 ms during which no other request progresses. Keep queries short, prefer `def` for heavy
> handlers, and never put `time.sleep`, a large NumPy computation, or a slow external HTTP
> call inside a coroutine without offloading it.

> **Did You Know?** `asyncio.wait_for(coro, timeout)` wraps an await with a deadline and
> raises `asyncio.TimeoutError` — the WebSocket handler uses it on `receive_text()` so one
> loop both handles client messages and pushes a snapshot every 30 s. It's the async
> equivalent of a socket timeout.

### Try It Yourself

```python
>>> import asyncio, time
>>> async def fetch(name, delay):
...     await asyncio.sleep(delay)
...     return f"{name} done"
>>> fetch("a", 0.1)                       # a coroutine object — nothing ran
<coroutine object fetch at ...>
>>> asyncio.run(fetch("a", 0.1))
'a done'
>>> async def both():
...     t0 = time.perf_counter()
...     r = await asyncio.gather(fetch("a", 0.2), fetch("b", 0.2))   # concurrent: ~0.2 s, not 0.4
...     return r, round(time.perf_counter() - t0, 2)
>>> asyncio.run(both())
>>> async def blocking():
...     t0 = time.perf_counter()
...     await asyncio.gather(asyncio.to_thread(time.sleep, 0.2), fetch("b", 0.2))   # offloaded: still ~0.2 s
...     return round(time.perf_counter() - t0, 2)
>>> asyncio.run(blocking())
>>> async def forgot():
...     fetch("oops", 0)                  # RuntimeWarning: coroutine 'fetch' was never awaited
>>> asyncio.run(forgot())
```

Then count the two handler styles in the repo — `git grep -c "^async def " backend/app/api/v1/endpoints/results.py`
vs `git grep -c "^def " backend/app/api/v1/endpoints/results.py` — and note that the results
module is consistently `def`.

### Common mistakes

- **Forgetting `await`** — a coroutine that never runs; for a permission check, a silent
  pass.
- **`time.sleep` / blocking I/O in a coroutine** — freezes the loop.
- **`await` in a `def`** — `SyntaxError`; make it `async def` or use `asyncio.run` at the
  top level only.
- **Mixing styles within a module** — pick one per file.

### Key takeaways

- `async def` returns a coroutine; `await` runs it and yields to the loop; only awaits are
  switch points.
- Blocking calls block everyone; `await asyncio.sleep`, `to_thread`/`run_in_threadpool`.
- FastAPI: `async def` on the loop, `def` in a threadpool; consistent per module.
- Async dependencies must be awaited or `Depends`-ed; `asyncio.gather` for concurrency;
  `wait_for` for deadlines.

---

## Hour 19 — `asyncio` tasks, cancellation, and `to_thread`

### What & why

`await coro` runs a coroutine _now_ and waits. `asyncio.create_task(coro)` schedules it to
run _alongside_ whatever else the loop is doing and returns a **`Task`** — a handle you can
await later, cancel, or leave running. That's how five schedulers coexist with request
handling in one process.

Three facts about tasks that the scheduler code depends on:

1. **A task runs only while the loop runs**, which for FastAPI means "while the server is
   up." Tasks are created in the lifespan's startup and cancelled at shutdown.
2. **`task.cancel()` is a request, not a kill.** It arranges for `asyncio.CancelledError` to
   be raised inside the coroutine at its _next_ `await`. The coroutine can clean up
   (`finally`, context managers) and must let the exception propagate; swallowing it makes
   the task uncancellable.
3. **`await task` after cancelling** waits for the unwinding to finish and re-raises
   `CancelledError` — which the caller catches to confirm a clean stop.

`asyncio.to_thread(fn, *args)` runs a synchronous function in a worker thread and returns
an awaitable — the escape hatch for CPU-bound or blocking work inside a coroutine. It's
what the bandit runner uses for Monte Carlo and its database work.

### In the wild

The task lifecycle, start to stop:

```python
# 📁 backend/app/core/scheduler.py
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
                await run_locked_tick(SCHEDULER_NAME, self.process_scheduled_experiments)
                await asyncio.sleep(self.interval_minutes * 60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in experiment scheduler: {str(e)}")
                await asyncio.sleep(60)
```

Trace a shutdown: `stop()` sets the flag and calls `cancel()`; the task is almost always
parked in `asyncio.sleep(...)`, so `CancelledError` is raised there; the loop's `except
CancelledError: break` ends it; `await self.task` returns (the task finished by breaking, so
no re-raise — or re-raises if cancellation landed elsewhere, which the `except` absorbs).
The `except Exception` clause doesn't catch `CancelledError` (it's a `BaseException`), so
even without the explicit clause the task would end — but the explicit clause documents it.

The runner that keeps cancellation clean through a lock and a database write:

```python
# 📁 backend/app/core/scheduler_tick.py
    with scheduler_lock(name, engine=engine) as acquired:
        ...
        try:
            raw = await tick()
            ...
        except asyncio.CancelledError:
            # Shutdown: release the lock (context manager) and propagate
            # without recording a bogus "failed" run.
            raise
```

`raise` inside the `with` → the context manager's `finally` releases the advisory lock →
the exception continues up to the scheduler loop. No "failed" run is recorded for a
shutdown.

Offloading to a thread — the whole bandit pass, session included:

```python
# 📁 backend/app/core/bandit_scheduler.py
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

The session is opened _inside_ the thread and never crosses back — sessions aren't
thread-safe, so the boundary is the function call. `run_count` is updated on the loop after
the thread returns, so no lock is needed.

The lifespan as the owner of every task, and the shutdown order (stop in the same order as
start — the code doesn't depend on order, but symmetry is easy to verify):

```python
# 📁 backend/app/main.py
    await experiment_scheduler.start()
    ...
    await bandit_scheduler_runner.start()

    yield

    await experiment_scheduler.stop()
    ...
    await bandit_scheduler_runner.stop()
```

`asyncio.gather` for running several awaitables concurrently and collecting results, and
`asyncio.Lock` for the one shared structure the WebSocket manager mutates across
coroutines:

```python
# 📁 backend/app/services/websocket_manager.py
    def __init__(self) -> None:
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
```

Why a lock at all, if coroutines don't run in parallel? Because `connect` awaits
(`websocket.accept`) _between_ reading and writing the dict; another coroutine could run in
that gap. An `asyncio.Lock` makes the read-modify-write atomic across awaits. It's the
_awaits_, not threads, that create the race.

> **Watch Out!** A task whose exception nobody observes — created with `create_task` and
> never awaited — logs "Task exception was never retrieved" at garbage collection and dies
> silently. The scheduler loops catch everything and keep going precisely so the task never
> dies; if you write a fire-and-forget task, wrap its body in `try/except` with a log.

> **Did You Know?** `BaseHTTPMiddleware`, `run_in_threadpool` and `asyncio.to_thread` all use
> the same default thread pool (sized by `anyio`), so a burst of threadpool endpoints plus a
> bandit pass compete for workers. It's another reason the platform runs one worker per
> container and scales containers: the pool is per process.

### Try It Yourself

```python
>>> import asyncio
>>> async def loop(name, interval):
...     try:
...         while True:
...             print(name, "tick"); await asyncio.sleep(interval)
...     except asyncio.CancelledError:
...         print(name, "cancelled cleanly"); raise
...     finally:
...         print(name, "cleanup")
>>> async def main():
...     t = asyncio.create_task(loop("sched", 0.1))
...     await asyncio.sleep(0.35)
...     t.cancel()
...     try: await t
...     except asyncio.CancelledError: print("stopped")
>>> asyncio.run(main())

>>> import time
>>> def cpu(): return sum(i * i for i in range(2_000_000))
>>> async def offload():
...     t0 = time.perf_counter()
...     r, _ = await asyncio.gather(asyncio.to_thread(cpu), asyncio.sleep(0.1))
...     return round(time.perf_counter() - t0, 2)
>>> asyncio.run(offload())
```

Then run a real scheduler's `start()`/`stop()` (Hour 19 of the FastAPI book) and confirm
`task is None` afterwards; then read `BanditSchedulerRunner._run_scheduler` and find where
its sleep can be shortened by tests (`interval_seconds`).

### Common mistakes

- **Swallowing `CancelledError`** (`except BaseException: pass`) — an unstoppable task.
- **Sharing a `Session` between the loop and a thread.** Open it inside the thread.
- **Forgetting that awaits are switch points** — a read-modify-write across an `await`
  needs an `asyncio.Lock`.
- **Fire-and-forget tasks with no exception handling** — "exception never retrieved."

### Key takeaways

- `create_task` runs alongside; `cancel()` raises `CancelledError` at the next `await`;
  `await task` after cancelling confirms the stop; let the exception propagate.
- The lifespan owns tasks: start before `yield`, stop after.
- `to_thread` for blocking/CPU work; open sessions inside the thread; the pool is per
  process.
- `asyncio.Lock` guards data mutated across awaits; `gather` for concurrency.

---

## Hour 20 — Threads, locks, and `ContextVar`

### What & why

Threads still exist in an async backend, in three places: FastAPI's threadpool (plain `def`
endpoints and `run_in_threadpool`), `asyncio.to_thread`, and the middleware, which may be
entered from threadpool workers. Any object touched from more than one thread needs a
**`threading.Lock`** (or `RLock`, re-entrant, when a locked method calls another locked
method). The Python interpreter's GIL serialises bytecode, but a compound operation — check
a dict, then update it — is _not_ atomic; two threads can interleave between the check and
the update.

**`contextvars.ContextVar`** is the third concurrency primitive you'll use: a variable whose
value is _per task_ (and per thread). Set it in one request's coroutine and every `await`
downstream in that request sees it; another request's coroutine sees its own. It's how the
request id reaches every log line without being passed as a parameter.

### In the wild

A thread-safe LRU cache — `RLock` because `set` may need to evict and both paths lock:

```python
# 📁 backend/app/core/evaluation_cache.py
class EvaluationCache:
    """
    LRU cache for rule evaluation results.

    Provides:
    - TTL-based expiration
    - LRU eviction when size limit reached
    - Thread-safe operations
    ...
    """

    def __init__(self, max_size: int = 10000, default_ttl: float = 300.0):
        self.max_size = max_size
        self.default_ttl = default_ttl

        # Thread-safe LRU cache
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        ...

    @property
    def size(self) -> int:
        """Get current cache size."""
        with self._lock:
            return len(self._cache)

    def get(self, rule_id: str, user_context: Dict[str, Any]) -> Optional[bool]:
        key = self.generate_key(rule_id, user_context)

        with self._lock:
            if key in self._cache:
                entry = self._cache[key]

                # Check if expired
                if time.time() > entry.expires_at:
                    del self._cache[key]
                    self._misses += 1
                    return None

                # Mark as recently used
                self._cache.move_to_end(key)
                self._hits += 1
                return entry.result
            else:
                self._misses += 1
                return None
```

`OrderedDict` + `move_to_end` is the standard LRU: the most recently used key is at the
end; eviction pops from the front (`popitem(last=False)`). Everything that reads or writes
`_cache` — including the counters — is inside `with self._lock`, and the lock is held for
microseconds.

A lock around a `deque` window, plus `time.monotonic()`:

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

`deque` gives O(1) `popleft` (a list's `pop(0)` is O(n)); the evict-count-append sequence
is one critical section. The login-attempt tracker is the same shape with a sweep that caps
memory:

```python
# 📁 backend/app/services/local_auth_service.py
class LoginAttemptTracker:
    """
    Thread-safe rolling-window failure counter keyed by e-mail.

    ``max_attempts`` failures within ``window_seconds`` lock the key until the
    oldest failure ages out of the window.
    """

    def __init__(self, max_attempts: int, window_seconds: int, max_tracked: int = MAX_TRACKED_ADDRESSES, sweep_interval: float = SWEEP_INTERVAL_SECONDS) -> None:
        ...
        self._lock = threading.Lock()
        self._failures: Dict[str, Deque[float]] = {}
        ...

    def __len__(self) -> int:
        with self._lock:
            return len(self._failures)
```

(`__len__` makes `len(tracker)` work — a dunder method, locked like everything else.)

The `ContextVar`, set by middleware and read by a structlog processor:

```python
# 📁 backend/app/core/logger.py
_log_context: contextvars.ContextVar[dict] = contextvars.ContextVar("log_context", default={})


def bind_log_context(**kwargs: Any) -> None:
    """Merge *kwargs* into the current async-task log context.

    Later calls to this function for the same key will overwrite earlier ones.
    """
    current = _log_context.get().copy()
    current.update(kwargs)
    _log_context.set(current)


def _inject_context(logger: Any, method: str, event_dict: dict) -> dict:
    """structlog processor: inject per-request context into every log event."""
    event_dict.update(_log_context.get())
    return event_dict
```

```python
# 📁 backend/app/middleware/request_id_middleware.py
        bind_log_context(request_id=request_id, path=request.url.path, method=request.method)
        response: Response = await call_next(request)
```

`.copy()` before `.update()` — the default `{}` is _shared_ (a `ContextVar` default is one
object), and mutating it would leak one request's id into every other. Copying then
`set()`ting gives this task its own dict. Two concurrent requests, two contexts, no lock.

Where the GIL doesn't help — the reason locks exist even though "only one thread runs Python
at a time":

```python
# ❌ not atomic: check, then update — another thread can run between them
if key not in cache:
    cache[key] = compute()

# ✅ one critical section
with lock:
    if key not in cache:
        cache[key] = compute()
```

> **Watch Out!** An `asyncio.Lock` and a `threading.Lock` are different things. The
> former yields to the event loop while waiting and is only for coroutines on one loop; the
> latter blocks the OS thread and is for code that may run on threadpool workers. The
> WebSocket manager (coroutines only) uses `asyncio.Lock`; the caches and limiters (called
> from threadpool endpoints) use `threading`. Using `threading.Lock` in a coroutine blocks
> the whole loop while waiting; using `asyncio.Lock` across threads doesn't protect
> anything.

> **Did You Know?** `RLock` lets the same thread acquire twice — necessary when a locked
> method calls another locked method on the same object (`set` → `_evict` in the evaluation
> cache). With a plain `Lock`, that's a deadlock: the thread waits for a lock it already
> holds.

### Try It Yourself

```python
>>> import threading, time
>>> counter = {"n": 0}
>>> def bump(times):
...     for _ in range(times):
...         v = counter["n"]; time.sleep(0); counter["n"] = v + 1      # read, yield, write — a race
>>> threads = [threading.Thread(target=bump, args=(10_000,)) for _ in range(4)]
>>> [t.start() for t in threads]; [t.join() for t in threads]; counter["n"]     # < 40000
>>> counter["n"] = 0; lock = threading.Lock()
>>> def safe(times):
...     for _ in range(times):
...         with lock:
...             v = counter["n"]; time.sleep(0); counter["n"] = v + 1
>>> threads = [threading.Thread(target=safe, args=(10_000,)) for _ in range(4)]
>>> [t.start() for t in threads]; [t.join() for t in threads]; counter["n"]     # 40000

>>> import asyncio, contextvars
>>> req = contextvars.ContextVar("req", default="none")
>>> async def handle(rid):
...     req.set(rid); await asyncio.sleep(0.01); return req.get()
>>> asyncio.run(asyncio.gather(handle("a"), handle("b")))    # each task keeps its own
['a', 'b']
```

Then read `EvaluationCache.set` and trace how eviction (`popitem(last=False)`) runs under
the same `RLock` as `get`.

### Common mistakes

- **Check-then-act without a lock** on shared structures.
- **The wrong lock kind** for the context (threading in coroutines, asyncio across
  threads).
- **Mutating a `ContextVar`'s default object** — copy, then `set`.
- **Holding a lock while doing I/O.** Keep critical sections to memory operations.

### Key takeaways

- Threads appear via the threadpool and `to_thread`; compound operations on shared
  structures need `threading.Lock`/`RLock`; keep sections short; `deque` for windows;
  `monotonic` for time.
- `asyncio.Lock` for coroutines on one loop; `threading.Lock` for cross-thread code.
- `ContextVar` gives per-task state (the request id) without parameters or locks — copy the
  default before mutating.
- The GIL doesn't make check-then-act atomic.

---

## Part V Workshop

**Q1. `deps.can_update_feature_flag(feature_flag=f, current_user=u)` is called without
`await`. What happens?**
A coroutine object is created and discarded; the permission check never runs; Python
warns "coroutine … was never awaited" at garbage collection. The call silently passes.

**Q2. Why is a plain `def` endpoint safer for the results computation than `async def`?**
FastAPI runs `def` endpoints in a threadpool, so a slow synchronous computation doesn't
block the event loop; an `async def` with the same synchronous code freezes every other
coroutine while it runs.

**Q3. `stop()` calls `task.cancel()` then `await self.task`. Why both?**
`cancel()` only requests cancellation (raised at the task's next `await`); awaiting the task
waits for it to unwind — `finally` blocks, lock releases — and confirms it's finished.

**Q4. Why does the WebSocket manager need a lock if coroutines don't run in parallel?**
`connect` awaits `websocket.accept()` between reading and writing the connection dict;
another coroutine can run in that gap. The lock makes the read-modify-write atomic across
awaits.

**Q5. `threading.Lock` or `asyncio.Lock` for the evaluation cache, and why?**
`threading.RLock` — the cache is called from threadpool endpoints and possibly several
threads; an `asyncio.Lock` wouldn't protect across threads. `RLock` because `set` may call
another locked method.

**Q6. Two requests call `bind_log_context(request_id=...)`. Why don't they clobber each
other?**
The value lives in a `ContextVar`, which is per task; each request's coroutine has its own
context, and `.copy()` before `.update()` ensures the shared default is never mutated.

**Quiz.** What does `asyncio.to_thread(self._run_sync)` return, and what must `_run_sync`
never do?
→ An awaitable that resolves to `_run_sync`'s return value once the thread finishes.
`_run_sync` must never touch loop-owned objects (or share its `Session` back to the loop) —
it opens and closes its own session inside the thread.

---

## Exercises

**E5.1 — Concurrent fetches.** Write `async def snapshot_all(ids)` that awaits
`get_live_snapshot(id)` for every id concurrently with `asyncio.gather`, with a 5-second
overall deadline via `asyncio.wait_for`, returning partial results on timeout.

**E5.2 — A cancellable loop.** Write a `Heartbeat` class with `start()`/`stop()` in the
scheduler style that logs every `interval` seconds, cleans up in `finally`, and never
swallows `CancelledError`; show the shutdown sequence.

**E5.3 — Offload.** Convert `def compute_ptbb(posteriors, n_samples)` (CPU-heavy NumPy) into
something an `async def` endpoint can call without blocking the loop, and explain what
would happen without the conversion under one worker.

**E5.4 — A safe cache.** Implement `TTLCache.get_or_set(key, factory, ttl)` with a
`threading.Lock`, such that two threads asking for a missing key at once compute it only
once. What lock kind, and why?

**E5.5 — Context.** Write a `current_user_id: ContextVar[str | None]` set by a dependency
and read by a logging processor, and show why the value must not be set via mutation of the
default.

---

## Solutions

**S5.1**

```python
async def snapshot_all(ids, service, timeout=5.0):
    tasks = [asyncio.create_task(service.get_live_snapshot(i)) for i in ids]
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for t in pending:
        t.cancel()
    results = {}
    for t in done:
        try:
            snap = t.result()
            results[snap["experimentId"]] = snap
        except Exception as exc:
            logger.warning("snapshot failed: %s", exc)
    return results
```

(`asyncio.wait` with a timeout returns what finished; `wait_for` around a `gather` would
cancel everything on timeout and lose partial results.)

**S5.2**

```python
class Heartbeat:
    def __init__(self, interval: float):
        self.interval = interval
        self.task: Optional[asyncio.Task] = None

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    async def _run(self):
        try:
            while True:
                logger.info("heartbeat")
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            logger.info("heartbeat cancelled")
            raise
        finally:
            logger.info("heartbeat cleanup")
```

Shutdown: `stop()` → `cancel()` → `CancelledError` raised inside `sleep` → the `except` logs
and re-raises → `finally` runs → `await self.task` re-raises `CancelledError` → `stop()`
absorbs it → `task = None`.

**S5.3**

```python
async def compute_ptbb_async(posteriors, n_samples):
    return await asyncio.to_thread(compute_ptbb, posteriors, n_samples)
```

Without it, an `async def` endpoint calling `compute_ptbb` directly would run 10,000-sample
NumPy draws on the event loop; under one worker every other request (and every scheduler
tick) would stall for the duration — tens to hundreds of milliseconds per call, multiplied by
concurrent dashboards.

**S5.4**

```python
class TTLCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[str, Tuple[float, Any]] = {}

    def get_or_set(self, key, factory, ttl):
        now = time.monotonic()
        with self._lock:
            hit = self._data.get(key)
            if hit and hit[0] > now:
                return hit[1]
            value = factory()                       # computed under the lock: exactly once
            self._data[key] = (now + ttl, value)
            return value
```

A plain `threading.Lock` suffices (no re-entrancy: `factory` must not call back into the
cache). Computing under the lock guarantees single computation at the cost of serialising
misses — acceptable for cheap factories; for expensive ones, use a per-key lock or compute
outside and `setdefault`.

**S5.5**

```python
current_user_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_user_id", default=None)

def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    current_user_id.set(str(current_user.id))     # set, never mutate
    return current_user

def _inject_user(logger, method, event_dict):
    uid = current_user_id.get()
    if uid:
        event_dict["user_id"] = uid
    return event_dict
```

The default is a single shared object; if it were a mutable container and code did
`current_user_id.get()["id"] = ...`, every task would see the mutation. `set()` binds a new
value in _this_ task's context only — which is the whole point of a `ContextVar`.

---

**Next:** [Part VI — In Practice →](./part-6-in-practice.md)

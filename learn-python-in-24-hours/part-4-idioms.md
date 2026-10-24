# Part IV — Idioms

### Hours 14–17

[← Part III](./part-3-modern-python.md) · [Contents](./README.md) · [Part V →](./part-5-concurrency.md)

Four features that make Python code look like Python: decorators, which wrap functions with
behaviour (Hour 14); context managers, which guarantee cleanup (Hour 15); generators, which
produce values lazily and are the mechanism under half of FastAPI (Hour 16); and the
standard library's `datetime`, whose naive/aware split stopped every rollout in this platform
until one function was written (Hour 17). You've already been _using_ all four — `@router.get`,
`with scheduler_lock(...)`, `yield db`, `datetime.utcnow()` — this Part is where you learn
what they are.

---

## Hour 14 — Decorators

### What & why

A **decorator** is a function that takes a function and returns a function. The `@name`
syntax is sugar:

```python
@with_retry(max_retries=3)
def tick(): ...
# is exactly
def tick(): ...
tick = with_retry(max_retries=3)(tick)
```

Because functions are objects (Hour 4), a decorator can wrap the original in a new function
that does something before and after — retry, time, log, cache, register — and hand back
the wrapper under the original name. `functools.wraps` copies the original's name and
docstring onto the wrapper so `help()`, logging and FastAPI's introspection still see the
real function.

Decorators are how this codebase says "this function is a route" (`@router.post`), "this
method is a validator" (`@field_validator`), "this is a computed attribute" (`@property`),
"this is a test that runs on an event loop" (`@pytest.mark.asyncio`), "this generator is a
context manager" (`@contextmanager`), and "retry this on failure" (`@with_retry`). Reading
a decorator's definition once turns each of those from magic into a function call.

#### Decorators with arguments

`@with_retry(max_retries=3)` has parentheses, so there are _three_ layers: `with_retry(...)`
returns a decorator; the decorator takes `tick` and returns the wrapper; the wrapper runs
when `tick()` is called. `@router.get("/path")` is the same shape — `router.get("/path")`
returns a decorator that registers the function and returns it unchanged.

### In the wild

The codebase's own decorator, complete — a retry with exponential backoff:

```python
# 📁 backend/app/core/scheduler_retry.py
def with_retry(max_retries: int = 3, delay_seconds: int = 60, backoff: float = 2.0):
    """
    Decorator that retries a scheduler function on exception with exponential backoff.
    ...
    Usage:
        @with_retry(max_retries=3, delay_seconds=30)
        def run_experiment_scheduler(db):
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        wait = delay_seconds * (backoff ** attempt)
                        logger.warning("%s failed (attempt %d/%d), retrying in %.0fs: %s",
                                       func.__name__, attempt + 1, max_retries + 1, wait, e)
                        time.sleep(wait)
                    else:
                        logger.error("%s failed after %d attempts: %s", func.__name__, max_retries + 1, e)
            raise last_exception
        return wrapper
    return decorator
```

Read the three layers: `with_retry(...)` → `decorator` → `wrapper`. `*args, **kwargs` pass
through whatever the wrapped function takes; `return func(*args, **kwargs)` on success;
`@wraps(func)` keeps `func.__name__` so the log lines name the real function. (Note the
`time.sleep` — this is a _synchronous_ decorator for synchronous scheduler helpers; an
`async` version would need `async def wrapper` and `await asyncio.sleep`.)

A decorator that **registers** rather than wraps — FastAPI's route decorator returns the
function unchanged after recording it:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
@router.post(
    "/assign",
    response_model=VariantAssignmentResponse,
    summary="Assign user to experiment variant",
    response_description="Returns the variant assignment for the user",
)
async def assign_user_to_experiment(...) -> VariantAssignmentResponse:
```

Stacked decorators apply bottom-up — the inner one first. Pydantic's validator decorators
require a specific order, and the class-level decorators of the standard library compose:

```python
# 📁 backend/app/schemas/feature_flag.py
    @field_validator("key", check_fields=False)
    @classmethod
    def validate_key(cls, v: str) -> str:
```

`@classmethod` is applied first (making it a classmethod), then `@field_validator`
registers that classmethod. Reverse them and Pydantic raises at class creation.

`@property` and `@staticmethod` from Hour 10 are decorators too — descriptors that change
how attribute access works rather than wrapping a call. And `@dataclass` (Hour 11) is a
_class_ decorator: it takes a class and returns a class with generated methods.

`@contextmanager` and `@asynccontextmanager` (Hour 15) turn a generator into a context
manager:

```python
# 📁 backend/app/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
```

And `pytest`'s marker decorators, which attach metadata the test runner reads:

```python
@pytest.mark.asyncio
async def test_viewer_cannot_create_flag(...):
```

> **Watch Out!** A decorator without `@wraps` replaces the function's `__name__` with
> `"wrapper"`, its docstring with `None`, and — for FastAPI — its _signature_, so the
> framework can't see the parameters and the route silently accepts nothing. Always
> `@wraps(func)`. (FastAPI's own decorators return the original function, which is why they
> don't have this problem.)

> **Did You Know?** `functools.lru_cache` is a decorator that memoises a function by its
> arguments. `core/database_config.py` has a comment "REMOVED @lru_cache for development to
> avoid caching issues with environment variables" on `get_schema_name` — a cached
> function that reads the environment returns the _first_ environment it saw, which is
> exactly wrong for a test harness that sets variables before import. The team removed the
> cache and left the note.

### Try It Yourself

```python
>>> import time, functools
>>> def timed(func):
...     @functools.wraps(func)
...     def wrapper(*args, **kwargs):
...         t0 = time.perf_counter()
...         try:
...             return func(*args, **kwargs)
...         finally:
...             print(f"{func.__name__} took {(time.perf_counter() - t0) * 1000:.1f} ms")
...     return wrapper
>>> @timed
... def bucket(user_id, salt):
...     """Deterministic bucket."""
...     import hashlib
...     return int(hashlib.md5(f"{user_id}:{salt}".encode()).hexdigest(), 16) % 100
>>> bucket("u", "s"); bucket.__name__, bucket.__doc__          # preserved by wraps

>>> def retry(times):                                           # decorator with arguments
...     def decorator(func):
...         @functools.wraps(func)
...         def wrapper(*a, **kw):
...             for attempt in range(times):
...                 try: return func(*a, **kw)
...                 except ValueError as e:
...                     print("attempt", attempt + 1, "failed:", e)
...             raise RuntimeError("gave up")
...         return wrapper
...     return decorator
>>> calls = {"n": 0}
>>> @retry(times=3)
... def flaky():
...     calls["n"] += 1
...     if calls["n"] < 3: raise ValueError("not yet")
...     return "ok"
>>> flaky()
```

Then import `with_retry` from the real module, decorate a function that fails twice then
succeeds (use `delay_seconds=0`), and watch the warning lines name your function.

### Common mistakes

- **Forgetting `@wraps`** — name, docstring and signature lost.
- **Forgetting the outer layer** when the decorator takes arguments (`@retry` vs
  `@retry(3)`).
- **Wrong stacking order** (`@classmethod` above `@field_validator`).
- **A sync decorator on an async function** — `func(*args)` returns a coroutine that's
  never awaited; the wrapper needs to be `async` and `await`.

### Key takeaways

- `@d` is `f = d(f)`; with arguments, `@d(x)` is `f = d(x)(f)` — three layers.
- `@wraps(func)` preserves identity; `*args, **kwargs` pass through.
- Decorators wrap (`with_retry`), register (`@router.post`, `@field_validator`,
  `@pytest.mark`), or transform (`@property`, `@dataclass`, `@contextmanager`).
- Stacking applies bottom-up; sync wrappers don't work on async functions.

---

## Hour 15 — Context managers and `with`

### What & why

A **context manager** is an object with `__enter__` and `__exit__`; `with cm as x:` calls
`__enter__` (binding its return to `x`), runs the block, and calls `__exit__` _no matter
how the block ends_ — normally, by `return`, by `break`, or by an exception. It's `try/finally`
with the cleanup attached to the resource instead of repeated at every use site. Files,
locks, database connections and transactions are the classic cases.

`@contextmanager` (from `contextlib`) turns a **generator** into one: everything before
`yield` is `__enter__`, the yielded value is `x`, everything after is `__exit__`. Wrap the
`yield` in `try/finally` and the cleanup runs even when the block raises.
`@asynccontextmanager` is the same for `async with`.

FastAPI's `yield` dependencies (Hour 16) are context managers in spirit — `get_db` yields a
session and closes it in `finally` — and the lifespan _is_ one.

#### Why the advisory lock is a context manager

"Take the lock, do the tick, release the lock — even if the tick raises, even on shutdown"
is exactly the contract `with` guarantees. Written as a function with explicit
`try/finally`, every scheduler would have to repeat it and one would get it wrong.
`scheduler_lock(name)` yields `True`/`False` so the caller can decide to skip, and its
`finally` releases and closes the connection unconditionally.

### In the wild

The lock — a `@contextmanager` generator with a `try/finally` around the `yield`. The
defensive work (connect, try the lock, log, close) lives in two helpers, `_acquire` and
`_release`, so the context manager itself is the bare shape:

```python
# 📁 backend/app/core/scheduler_lock.py
@contextmanager
def scheduler_lock(name: str, engine: Optional[Engine] = None) -> Iterator[bool]:
    """
    Hold the scheduler advisory lock for *name* while the block runs.

    Yields ``True`` when this process acquired the lock (the caller should do
    the tick), ``False`` when another session holds it or the database could
    not be reached (the caller should skip the tick).
    ...
    """
    engine = engine or _default_engine()
    conn, acquired = _acquire(engine, name)
    try:
        yield acquired
    finally:
        _release(conn, name, acquired)
```

`_acquire` returns `(None, False)` instead of raising when the database can't be reached,
and `_release` closes the connection in its own `finally`. Why split them out? So the same
two functions can back an **async** version for the schedulers, which run on the event loop:

```python
# 📁 backend/app/core/scheduler_lock.py
@asynccontextmanager
async def async_scheduler_lock(
    name: str, engine: Optional[Engine] = None
) -> AsyncIterator[bool]:
    """
    Coroutine-friendly :func:`scheduler_lock`.

    Acquire and release run in a worker thread (``asyncio.to_thread``), so an
    unreachable database or a slow connection pool blocks only that thread,
    never the event loop serving HTTP requests.
    """
    engine = engine or _default_engine()
    conn, acquired = await asyncio.to_thread(_acquire, engine, name)
    try:
        yield acquired
    finally:
        await asyncio.to_thread(_release, conn, name, acquired)
```

`@asynccontextmanager` is `@contextmanager` for `async with`: the body may `await`, and so may
the cleanup in `finally`. The blocking database calls are pushed to a thread with
`asyncio.to_thread` (Hour 19), so a database that hangs blocks one worker thread, not every
request the process is serving.

And its use — the block runs under the lock, and everything after the `async with` runs after
release:

```python
# 📁 backend/app/core/scheduler_tick.py
    async with async_scheduler_lock(name, engine=engine) as acquired:
        if not acquired:
            ...
            return TickOutcome(...)          # return inside the block: __aexit__ still runs

        try:
            raw = await tick()
            ...
        except asyncio.CancelledError:
            raise                            # exception inside the block: __aexit__ still runs
        ...

    completed_at = datetime.now(timezone.utc)   # after release
```

`async with` for an asyncio lock, and the copy-then-release pattern:

```python
# 📁 backend/app/services/websocket_manager.py
        async with self._lock:
            conns = set(self._connections.get(experiment_id, set()))
        for ws in conns:
            try:
                await ws.send_text(payload)
```

`with` on a threading lock — held only for the dictionary work:

```python
# 📁 backend/app/core/evaluation_cache.py
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
```

SQLAlchemy's transactional context managers — `engine.begin()` commits on success and rolls
back on exception; `engine.connect()` closes:

```python
# 📁 backend/app/db/bootstrap.py
def ensure_schema(engine: Engine, schema: str) -> None:
    with engine.begin() as conn:
        conn.execute(CreateSchema(schema, if_not_exists=True))
```

```python
# 📁 backend/app/db/session.py
def _ensure_schema(schema_name: str) -> None:
    with engine.connect() as conn:
        conn.execute(CreateSchema(schema_name, if_not_exists=True))
        conn.execute(text("COMMIT"))
```

The test client _can_ be a context manager: `with TestClient(app) as client:` runs the app's
lifespan (startup and shutdown) around the block. The shared fixture deliberately does not
use it, so the lifespan — and the schedulers it starts — never run in the unit suite:

```python
# 📁 backend/tests/conftest.py
    # Create test client
    test_client = TestClient(app)

    yield test_client
```

And `pytest.raises` — a context manager that _expects_ an exception:

```python
    with pytest.raises(HTTPException) as exc:
        await deps.can_create_feature_flag(current_user=viewer)
    assert exc.value.status_code == 403
```

And `unittest.mock.patch` as a context manager, scoped to a block:

```python
with patch("backend.app.core.permissions.check_permission", return_value=True):
    with patch("backend.app.api.deps.get_current_active_user", return_value=mock_user):
        response = client.post(f"/api/v1/safety/feature-flags/{feature_flag_id}/rollback")
```

> **Watch Out!** A `@contextmanager` generator must `yield` **exactly once**. Yielding
> twice raises `RuntimeError: generator didn't stop`; returning without yielding raises
> `RuntimeError: generator didn't yield`; and an exception raised _before_ the `yield`
> propagates out of the `with` line itself, before the block runs. That last case is why
> `scheduler_lock`'s `_acquire` catches the connect failure and returns `False` — otherwise
> an unreachable database would crash the tick instead of skipping it.

> **Did You Know?** `with a() as x, b() as y:` opens two; Python 3.10 allows parentheses
> across lines. `contextlib.ExitStack` opens a dynamic number. And `contextlib.suppress(FileNotFoundError)`
> is the one-liner for "ignore this specific exception in this block" — better than
> `try/except: pass`.

### Try It Yourself

```python
>>> from contextlib import contextmanager
>>> @contextmanager
... def timed(label):
...     import time; t0 = time.perf_counter()
...     print("enter", label)
...     try:
...         yield label
...     finally:
...         print("exit", label, f"{(time.perf_counter() - t0) * 1000:.1f} ms")
>>> with timed("tick") as lbl:
...     print("working on", lbl)
>>> def early():
...     with timed("early"):
...         return "returned inside"       # exit still runs
>>> early()
>>> try:
...     with timed("boom"): raise ValueError("x")
... except ValueError: print("caught after exit")

>>> class Lock:
...     def __enter__(self): print("acquire"); return True
...     def __exit__(self, exc_type, exc, tb): print("release", exc_type.__name__ if exc_type else None); return False
>>> with Lock() as held: print("held:", held)
```

Then run the real lock twice, nested, against your database and watch the second yield
`False`:

```python
from backend.app.core.scheduler_lock import scheduler_lock
with scheduler_lock("scratch") as a:
    with scheduler_lock("scratch") as b:
        print(a, b)     # True False
```

With no database reachable you'll see `False False` and two logged warnings instead — the
"skip the tick" path, which is exactly what `_acquire` is for.

### Common mistakes

- **Cleanup outside `finally`** in a `@contextmanager` — skipped when the block raises.
- **Raising before the `yield`** without handling — the `with` statement itself raises and
  the block never runs.
- **Holding a lock across an `await` or a slow call.** Copy under the lock, work outside.
- **Opening a resource in `__init__`** and hoping someone closes it. Use `with`.

### Key takeaways

- `with cm as x:` = `__enter__` … block … `__exit__` (always, even on `return`/exception).
- `@contextmanager`: code before `yield` enters, after (in `finally`) exits; yield exactly
  once; `@asynccontextmanager` for `async with`.
- Locks, connections, transactions, `TestClient`, `pytest.raises`, `patch` — all `with`.
- `scheduler_lock` yields a boolean so the caller can skip; its `finally` always releases.
  `async_scheduler_lock` is the same shape for `async with`, with the blocking calls in
  `asyncio.to_thread`.

---

## Hour 16 — Iterators, generators, and `yield`

### What & why

Anything you can `for` over is an **iterable**; `iter(x)` gives an **iterator** with a
`__next__` that produces values until `StopIteration`. Lists, dicts, sets, strings, files,
`range`, `zip`, `enumerate` — all iterables. `next(it)` pulls one value; `next(it, default)`
returns `default` instead of raising when exhausted.

A **generator** is a function with `yield`: calling it returns an iterator that runs the
body lazily, pausing at each `yield` and resuming on the next `next()`. Nothing runs until
you ask; state is kept between values; memory is one value at a time. A **generator
expression** — `(w for _, w in weighted)`, parentheses instead of brackets — is a
comprehension that's lazy.

Three places `yield` shows up in this codebase that aren't "produce a sequence":

1. **FastAPI `yield` dependencies** — `get_db` yields the session once; code after the
   `yield` is cleanup.
2. **`@contextmanager`** — a generator whose single `yield` marks enter/exit (Hour 15).
3. **Streaming responses** — an async generator yielding SSE chunks, sent as they're
   produced.

### In the wild

The single-`yield` dependency:

```python
# 📁 backend/app/api/deps.py
async def get_db() -> Generator[Session, None, None]:
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()
```

`Generator[Session, None, None]` types it: yields `Session`, receives nothing, returns
nothing. FastAPI calls `next()` to get the session, runs the endpoint, then resumes the
generator so the `finally` runs.

A streaming async generator — each `yield` is one chunk on the wire:

```python
# 📁 backend/app/api/v1/endpoints/bulk_toggle.py
    async def event_generator():
        ...
        for i, log in enumerate(logs):
            event_data = json.dumps({...})
            yield f"data: {event_data}\n\n"
            await asyncio.sleep(0.01)
        yield 'data: {"event": "end"}\n\n'

    return StreamingResponse(event_generator(), media_type="text/event-stream", ...)
```

Generator expressions feeding `sum`, `all`, `any` — lazy, no intermediate list:

```python
# 📁 backend/app/services/bandit_routing.py
    total = sum(w for _, w in weighted)
```

```python
# 📁 backend/app/schemas/rollout_schedule.py
        if not all(a <= b for a, b in zip(stage_percentages, stage_percentages[1:])):
```

```python
# 📁 backend/app/services/assignment_service.py
            variant_valid = any(
                v.id == override_variant_id for v in experiment.variants
            )
```

`any(...)` stops at the first `True`; `all(...)` at the first `False` — short-circuiting
that a list comprehension can't do.

`next()` with a default — "the first item matching, or `None`" — the idiom for a lookup in
a small list:

```python
# 📁 backend/app/services/analysis_service.py
        # Find control variant
        control_variant = next((v for v in experiment.variants if v.is_control), None)
        if not control_variant:
            raise ValueError(f"Experiment {experiment.id} has no control variant")
```

```python
# 📁 backend/app/services/event_service.py
        metadata = next((data[k] for k in _METADATA_KEYS if data.get(k) is not None), None)
        timestamp = next((data[k] for k in _TIMESTAMP_KEYS if data.get(k)), None)
```

That second one is elegant: `_METADATA_KEYS = ("event_metadata", "properties", "metadata")`
is a priority order; `next(...)` returns the first key that's present, or `None`. Without
generators it's a five-line loop.

`enumerate` for index+value and `zip` for parallel walks, both lazy:

```python
# 📁 backend/app/services/feature_flag_service.py
            for i, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    continue
                rule_id = str(rule.get("id", f"rule_{i}"))
```

`iter()`/`next()` under the hood, and why exhausting a generator matters — a generator can
be consumed once:

```python
>>> gen = (x * 2 for x in [1, 2, 3])
>>> list(gen), list(gen)
([2, 4, 6], [])          # second time: empty
```

> **Watch Out!** A generator expression passed to `sum()` or `any()` is fine; a generator
> _stored in a variable_ and used twice is a bug (the second use is empty). If you need to
> iterate more than once, materialise a list. And a generator that's never iterated never
> runs — `event_generator()` in the SSE endpoint does nothing until `StreamingResponse`
> starts pulling from it.

> **Did You Know?** `yield` can also _receive_ a value (`x = yield`), which is the
> coroutine protocol `async`/`await` was built on. You'll never write that in this
> codebase — `async def` replaced it — but it's why `Generator[Session, None, None]` has
> three type parameters: yield type, send type, return type.

### Try It Yourself

```python
>>> def buckets(user_ids, salt):
...     import hashlib
...     for uid in user_ids:
...         yield uid, int(hashlib.md5(f"{uid}:{salt}".encode()).hexdigest(), 16) % 100
>>> g = buckets(["u1", "u2", "u3"], "hero"); next(g); list(g)      # one pulled, then the rest
>>> next(g, "exhausted")
>>> next((u for u, b in buckets(["u1", "u2", "u3"], "hero") if b < 50), None)
>>> sum(1 for _ in range(10)), any(b < 5 for _, b in buckets(["u1", "u2"], "hero"))
>>> keys = ("event_metadata", "properties", "metadata"); data = {"properties": {"a": 1}}
>>> next((data[k] for k in keys if data.get(k) is not None), None)
{'a': 1}
>>> from contextlib import contextmanager
>>> @contextmanager
... def session():
...     print("open"); yield "db"; print("close")     # cleanup after the single yield
>>> with session() as db: print("using", db)
```

Then read `backend/app/api/v1/endpoints/bulk_toggle.py::stream_audit_logs` and change
`limit=3` in a `curl -N` call to see chunks arrive one at a time.

### Common mistakes

- **Reusing an exhausted generator.** Materialise a list if you need it twice.
- **`next(gen)` without a default** on a possibly-empty sequence — `StopIteration` (which
  inside another generator becomes `RuntimeError`).
- **A list comprehension where `any`/`all`/`sum` would short-circuit or save memory.**
- **Cleanup after `yield` outside `finally`** in a dependency — skipped on error.

### Key takeaways

- Iterables → iterators → `next()`; generators run lazily and pause at `yield`; consume
  once.
- `(expr for x in xs)` feeds `sum`/`any`/`all`/`next` without a list; `next(gen, default)`
  is "first match or default."
- `yield` dependencies, `@contextmanager` and streaming responses are all "run to the
  `yield`, hand over, resume for cleanup."
- `enumerate`, `zip`, `range` are lazy iterables.

---

## Hour 17 — Dates and times: the naive/aware bug that stopped every rollout

### What & why

`datetime.datetime` comes in two kinds, and Python will not let you compare them:

- **Naive** — no timezone attached: `datetime.utcnow()`, `datetime(2026, 9, 11, 14, 0)`.
  "14:00" — in what zone? The object doesn't know; the _convention_ has to.
- **Aware** — carries a `tzinfo`: `datetime.now(timezone.utc)`, a parsed ISO string with
  `+00:00`. "14:00 UTC."

`naive < aware` raises `TypeError: can't compare offset-naive and offset-aware datetimes`.
Arithmetic between them raises too. Everything else — `timedelta`, `.isoformat()`,
`fromisoformat`, `.replace(tzinfo=...)`, `.astimezone(...)` — works on both, which is how
the two kinds spread through a codebase unnoticed until a comparison meets them.

#### The bug

The platform's columns are `DateTime` without `timezone=True` (Hour 14 of the FastAPI
book): PostgreSQL stores naive timestamps and SQLAlchemy returns naive `datetime`s. The
convention is "naive means UTC," and `BaseModel.created_at` defaults to
`datetime.utcnow()`. Newer code — correctly, by modern advice — uses
`datetime.now(timezone.utc)`, which is aware. The rollout scheduler compared a stage's
`start_date` (naive, from the database) with `current_time` (aware): `TypeError` on the
first active schedule, every tick, recorded as a failed run that nobody read. No rollout
ever advanced until commit `56c49e4f` added one function and used it at every comparison.

> The fix isn't "always use aware" or "always use naive" — the columns are what they are.
> It's **normalise at the comparison**, and know which kind every value is.

### In the wild

The normaliser and its docstring, which is the whole lesson:

```python
# 📁 backend/app/core/rollout_scheduler.py
def _as_utc(value: datetime) -> datetime:
    """
    Return ``value`` as a timezone-aware UTC datetime.

    ``rollout_stages`` timestamps are stored in ``timestamp without time zone``
    columns, so SQLAlchemy hands back naive datetimes while the scheduler
    works with ``datetime.now(timezone.utc)``; comparing the two raises
    ``TypeError``. Naive values are UTC by convention throughout the app.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
```

`.replace(tzinfo=...)` _labels_ a naive value (no conversion — right, because naive means
UTC here); `.astimezone(...)` _converts_ an aware value (right, because it might be in
another zone). The comparison that used to raise:

```python
# 📁 backend/app/core/rollout_scheduler.py
        if stage.trigger_type == TriggerType.TIME_BASED:
            if not stage.start_date:
                return True
            return _as_utc(stage.start_date) <= current_time
```

The other convention, used where the columns are naive and the code writes to them — stay
naive, consistently:

```python
# 📁 backend/app/services/safety_service.py
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=timeframe_minutes)
        ...
        in_window = (
            ErrorLog.feature_flag_id == feature_flag_id,
            ErrorLog.timestamp.between(start_time, end_time),
        )
        error_count = int(
            db.query(func.count(ErrorLog.id)).filter(*in_window).scalar() or 0
        )
```

(`in_window` is a tuple of SQL conditions, and `*in_window` unpacks it into `filter`'s
arguments — Hour 5's unpacking, used to share one filter between several queries.)

The event store goes the other way. `events.created_at` is a **text** column, so every time
window over events is a _string_ comparison — which orders correctly only if every value is
UTC and written in one shape. One function produces that shape:

```python
# 📁 backend/app/models/event.py
def normalize_event_timestamp(value: Any) -> str:
    """Return ``value`` as a UTC ISO-8601 string in one canonical format.
    ...
    Accepts a ``datetime`` or an ISO-8601 string (``Z`` or a numeric offset).
    Raises ``ValueError`` for anything else, so an unreadable timestamp is
    refused rather than stored and compared as text.
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        moment = datetime.fromisoformat(value.strip())
    else:
        raise ValueError("event timestamp must be a datetime or an ISO-8601 string")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()
```

Label the naive value, convert the aware one, then format: `2026-09-11T14:03:22.118342+00:00`.
A client that sends `2026-09-11T16:03:22+02:00` or `...Z` is stored as the same instant in the
same shape. (`fromisoformat` accepts a trailing `Z` since Python 3.11.)

And so that no writer can forget to call it, the column type calls it — a SQLAlchemy
`TypeDecorator` that runs on every value _bound_ to the column, whether it is being written
or compared against:

```python
# 📁 backend/app/models/event.py
class UTCTimestampString(TypeDecorator):
    """A ``String`` column whose bound values are normalised to UTC.
    ...
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Optional[str]:
        if value is None:
            return None
        return normalize_event_timestamp(value)
```

`Event.created_at >= boundary` binds `boundary` through this type, so a window edge given as
an aware `datetime` is compared in the same format as the stored strings. The event service's
own helper now just delegates (`_to_iso_timestamp` calls `normalize_event_timestamp`).

The client-error endpoint takes the third way out: it does not trust the client's clock at all.
Rows in `error_logs` are timed by the server on receipt, with
`datetime.now(timezone.utc).replace(tzinfo=None)` (naive UTC, because that column is naive),
and a `timestamp` the client sent is kept in the row's metadata as `client_timestamp`.

The daily results code builds aware range bounds from ISO
strings:

```python
# 📁 backend/app/services/analysis_service.py
                date_start = datetime.fromisoformat(f"{date}T00:00:00+00:00")
                date_end = datetime.fromisoformat(f"{date}T23:59:59+00:00")
```

A validator that compares with the clock — and a schedule that can be written in a local zone.
`ScheduleConfig` takes an IANA `time_zone` (default `"UTC"`); a date sent _without_ an offset is
read as wall-clock time in that zone and converted to UTC before anything compares it:

```python
# 📁 backend/app/schemas/experiment.py
    @field_validator("start_date")
    @classmethod
    def validate_start_date(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Validate that start_date is in the future if provided.

        A date without an offset is checked in ``validate_dates``, once it has
        been read in ``time_zone``.
        """
        if (
            v
            and v.tzinfo is not None
            and v < datetime.now(timezone.utc) - timedelta(minutes=10)
        ):  # Allow small buffer
            raise ValueError("Start date must be in the future")
        return v
    ...
    @model_validator(mode="after")
    def validate_dates(self) -> "ScheduleConfig":
        ...
        zone = _zone(self.time_zone)
        naive_start = self.start_date is not None and self.start_date.tzinfo is None
        for name in ("start_date", "end_date"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                # Only a field the request carried can be naive, so this
                # assignment does not mark an omitted field as set.
                setattr(self, name, value.replace(tzinfo=zone).astimezone(timezone.utc))

        if naive_start and self.start_date < datetime.now(timezone.utc) - timedelta(
            minutes=10
        ):  # Allow small buffer
            raise ValueError("Start date must be in the future")
        ...
```

Three time ideas in one schema: the field validator compares only values that are already
aware (`v.tzinfo is not None`); the model validator (which runs after every field is parsed)
_labels_ a naive date with a `zoneinfo.ZoneInfo` zone and _converts_ it to UTC; and only then
compares it with the clock. `_zone("UTC")` returns `timezone.utc`, so the default works even
on a machine with no zone database. (An earlier version compared a possibly naive `v` with
an aware "now" directly — a client sending `"2026-09-12T09:00:00"` got a `TypeError` inside
the validator, the rollout bug in miniature. The time-zone field was the fix.)

Reproducibility buckets by calendar day — `date` vs `datetime`, and UTC conversion before
taking the date:

```python
# 📁 backend/app/core/stats_engine.py
def as_of_bucket(as_of: Optional[Union[datetime, date]] = None) -> str:
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    if isinstance(as_of, datetime):
        if as_of.tzinfo is not None:
            as_of = as_of.astimezone(timezone.utc)
        return as_of.date().isoformat()
    return as_of.isoformat()
```

Monotonic time for durations and windows — never wall-clock, which can jump:

```python
# 📁 backend/app/core/scheduler_tick.py
    t0 = time.perf_counter()
    ...
    duration = time.perf_counter() - t0
```

```python
# 📁 backend/app/middleware/rate_limiter.py
        now = time.monotonic()
        cutoff = now - window_seconds
```

> **Watch Out!** `datetime.utcnow()` is deprecated in Python 3.12 (it's fine on 3.11, which
> this repo pins). When the interpreter is upgraded, the migration is
> `datetime.now(timezone.utc).replace(tzinfo=None)` where naive UTC is required by a column —
> not a blanket switch to aware values, which would reintroduce the comparison bug from the
> other side.

> **Did You Know?** ISO-8601 strings with offsets sort lexicographically in time order
> _as long as they're all UTC with the same precision_ — which is why `events.created_at`
> as a string column works at all, and why `normalize_event_timestamp` insists on `+00:00`.
> A single `Z`-suffixed or offset-shifted value would break the ordering — which is why the
> normalisation moved into the column type, where no writer can skip it.

### Try It Yourself

```python
>>> from datetime import datetime, timezone, timedelta
>>> naive = datetime.utcnow(); aware = datetime.now(timezone.utc)
>>> naive.tzinfo, aware.tzinfo
(None, datetime.timezone.utc)
>>> naive <= aware
TypeError: can't compare offset-naive and offset-aware datetimes
>>> def as_utc(v): return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)
>>> as_utc(naive) <= aware
True
>>> aware.isoformat(), naive.isoformat()          # note the +00:00 on one and not the other
>>> datetime.fromisoformat("2026-09-11T14:00:00+00:00").tzinfo
>>> (aware + timedelta(minutes=15)).isoformat()
>>> aware.date().isoformat()
>>> import time; t0 = time.perf_counter(); sum(range(10**6)); time.perf_counter() - t0
```

Then find the two conventions in the repo: `git grep -n "datetime.utcnow()" backend/app |
wc -l` and `git grep -n "datetime.now(timezone.utc)" backend/app | wc -l` — and for three
of each, say whether the value meets a database column (naive) or a computed timestamp
(aware).

### Common mistakes

- **Comparing or subtracting naive and aware** — normalise first (`_as_utc`).
- **`.replace(tzinfo=...)` on an aware value from another zone** — that relabels instead
  of converting; use `.astimezone`.
- **Wall-clock (`time.time()`) for durations and windows** — use `perf_counter`/`monotonic`.
- **Assuming a client's ISO string has an offset.** Validate, or normalise on receipt.

### Key takeaways

- Naive (`utcnow`, database columns) vs aware (`now(timezone.utc)`, parsed offsets); they
  can't be compared.
- Convention here: naive means UTC; normalise at every comparison with `_as_utc`; write
  naive to naive columns; ISO strings always carry `+00:00`.
- `replace(tzinfo=)` labels, `astimezone()` converts; `date()` after converting to UTC.
- `perf_counter`/`monotonic` for elapsed time; the bug that stopped rollouts was one
  comparison.

---

## Part IV Workshop

**Q1. What does `@with_retry(max_retries=3)` above `def tick()` expand to?**
`tick = with_retry(max_retries=3)(tick)` — the call returns a decorator, which wraps `tick`
and returns `wrapper` under the name `tick`.

**Q2. Why does `scheduler_lock` catch the connection error _before_ its `yield`?**
An exception before the `yield` in a `@contextmanager` generator surfaces as `RuntimeError:
generator didn't yield`; yielding `False` instead lets the caller skip the tick cleanly.

**Q3. `next((v for v in variants if v.is_control), None)` — what does it return when no
variant is control, and why is the second argument there?**
`None`. Without the default, `next()` on an exhausted generator raises `StopIteration`.

**Q4. A generator stored in `gen` was passed to `sum()`. Can you `list(gen)` afterwards?**
No — it's exhausted; `list(gen)` is `[]`. Materialise a list first if you need it twice.

**Q5. `stage.start_date` (from the database) `<= datetime.now(timezone.utc)` raised
`TypeError`. Why, and what's the fix?**
The column is naive, `now(timezone.utc)` is aware. `_as_utc(stage.start_date) <=
current_time` — label the naive value as UTC (`replace(tzinfo=timezone.utc)`) before
comparing.

**Q6. When is `.replace(tzinfo=timezone.utc)` wrong?**
On an aware value in another zone — it relabels 14:00 CET as 14:00 UTC instead of
converting to 13:00 UTC. Use `.astimezone(timezone.utc)` for aware values.

**Quiz.** Which of these are context managers in the codebase: `scheduler_lock(name)`,
`TestClient(app)`, `pytest.raises(X)`, `engine.begin()`, `get_db()`?
→ All but `get_db()`, which is a FastAPI `yield` dependency (the same enter/exit shape, but
driven by the framework, not by `with`).

---

## Exercises

**E4.1 — An async retry decorator.** Write `with_async_retry(max_retries, delay_seconds)`
that works on `async def` functions (awaits the call, `await asyncio.sleep` between
attempts), preserves the wrapped function's identity, and re-raises the last exception.

**E4.2 — A timing context manager.** Write `@contextmanager def timed(name)` that yields a
dict, records `elapsed_ms` into it on exit (even on exception), and logs it; show it used
around a scheduler tick call.

**E4.3 — First match.** Given `weights = {"a": {"weight": 0.2}, "b": 0.5, "c": None}` and
`order = ["c", "b", "a"]`, use a generator expression with `next(..., None)` to find the
first variant in `order` with a positive weight, reusing the tolerant parsing idea from
`bandit_weight`.

**E4.4 — Normalise.** Write `to_naive_utc(value)` and `to_aware_utc(value)` that accept a
naive datetime, an aware datetime, or an ISO string, and return the requested kind; test
with `"2026-09-11T14:00:00+02:00"`.

**E4.5 — The fixed bug.** An earlier `ScheduleConfig.validate_start_date` compared `v` with
`datetime.now(timezone.utc)` unconditionally. Describe the input that made it raise
`TypeError`, name the two changes the current `ScheduleConfig` makes so it can't, and write
`to_utc(value: datetime, zone_name: str) -> datetime` that reads a naive value in an IANA
zone and converts it to aware UTC (an aware value keeps its instant). Check it with
`datetime(2030, 9, 12, 9, 0)` in `"America/Los_Angeles"`.

---

## Solutions

**S4.1**

```python
import asyncio, functools, logging
logger = logging.getLogger(__name__)

def with_async_retry(max_retries: int = 3, delay_seconds: float = 1.0, backoff: float = 2.0):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last = exc
                    if attempt < max_retries:
                        wait = delay_seconds * (backoff ** attempt)
                        logger.warning("%s failed (attempt %d): %s; retrying in %.1fs", func.__name__, attempt + 1, exc, wait)
                        await asyncio.sleep(wait)
            raise last
        return wrapper
    return decorator
```

(`except CancelledError: raise` isn't strictly needed — `except Exception` doesn't catch it —
but stating it documents the intent.)

**S4.2**

```python
from contextlib import contextmanager
import time, logging
logger = logging.getLogger(__name__)

@contextmanager
def timed(name: str):
    info = {"name": name}
    t0 = time.perf_counter()
    try:
        yield info
    finally:
        info["elapsed_ms"] = (time.perf_counter() - t0) * 1000
        logger.info("%s took %.1f ms", name, info["elapsed_ms"])

# usage
with timed("safety tick") as t:
    result = await safety_scheduler.check_feature_flags_safety()
print(t["elapsed_ms"])
```

**S4.3**

```python
def weight_of(raw):
    if isinstance(raw, dict):
        raw = raw.get("weight", 0.0)
    try:
        w = float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return w if w > 0 else 0.0

weights = {"a": {"weight": 0.2}, "b": 0.5, "c": None}
order = ["c", "b", "a"]
first = next((v for v in order if weight_of(weights.get(v)) > 0), None)   # "b"
```

**S4.4**

```python
from datetime import datetime, timezone

def to_aware_utc(value) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def to_naive_utc(value) -> datetime:
    return to_aware_utc(value).replace(tzinfo=None)

to_aware_utc("2026-09-11T14:00:00+02:00")   # 2026-09-11 12:00:00+00:00 — converted
to_naive_utc("2026-09-11T14:00:00+02:00")   # 2026-09-11 12:00:00        — converted, then labelled off
```

**S4.5** — An ISO string with no offset (`"2026-09-12T09:00:00"`) parses to a naive `v`;
`v < datetime.now(timezone.utc)` then raised `TypeError` — not a `ValueError`, so Pydantic did
not turn it into a tidy validation error. The current schema (1) has the field validator
compare only aware values (`v.tzinfo is not None`), and (2) adds a `time_zone` field and a
`model_validator(mode="after")` that reads naive dates in that zone, converts them to UTC,
and only then compares with the clock.

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

def to_utc(value: datetime, zone_name: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(zone_name))   # label: wall-clock time there
    return value.astimezone(timezone.utc)                    # convert

to_utc(datetime(2030, 9, 12, 9, 0), "America/Los_Angeles")
# datetime.datetime(2030, 9, 12, 16, 0, tzinfo=datetime.timezone.utc)
```

Same label-then-convert pair as `_as_utc`, with the zone as a parameter instead of "UTC by
convention."

---

**Next:** [Part V — Concurrency →](./part-5-concurrency.md)

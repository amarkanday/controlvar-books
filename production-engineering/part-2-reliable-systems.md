# Part II — Reliable Systems

### Hours 4–6

[← Part 1](./part-1-experiment-design.md) · [Contents](./README.md) · [Part 3 →](./part-3-security-and-privacy.md)

A request can commit and still fail to reach the client. A scheduler can lose its database connection in
the middle of a tick. An SDK can hold yesterday's rules while the server has moved on. A migration can
succeed on your laptop and break the replica that is still running the previous release. None of these
is exotic; each has a line of Controlvar source that was written because it happened. This Part reads
those lines. Hours 4 and 5 are about *failure between two correct pieces*: the request and its response,
the lock and its holder. Hour 6 is about change: a schema, a query and a connection pool that must stay
correct while two versions of the application share one database.

Keep the app clone at v0.36.4 open. The Try It Yourself blocks run real pieces of the backend without a
database (set the safety variables from Hour 1 first). The ones that need PostgreSQL say so and point at
the existing test instead of asking you to start one.

---

<a id="hour-4--assignments-transactions-and-retries"></a>

## Hour 4 — Assignments, transactions and retries: two requests, one user

**Outcome:** distinguish assignment uniqueness, event storage and response delivery, explain what a retry
can repeat when a write commits before the client hears about it, and state which of those cases
Controlvar handles for you.

### What & why

#### An invariant is not a delivery guarantee

A database constraint such as "one row per `(experiment, user)`" is an **invariant**: it says what states
the data may be in. A **delivery guarantee** is a different kind of promise: it says what the *caller*
experiences when something goes wrong in between. Confusing them is the root of a whole class of
operational surprises. The constraint can be perfectly intact while one caller receives an error, a
timeout, or a response for a row another request wrote.

#### The ambiguous write

Imagine a request that commits, then the connection drops before the response arrives. The client sees a
timeout. It cannot know whether the write happened, so any retry may store the thing twice. This is the
**ambiguous write**, and no amount of cleverness on one side removes it. The only remedies are on the
protocol level: an idempotency key with an agreed lifetime, or a producer that records what it sent and
checks. Everything else is a policy about *how much duplication you will tolerate*.

#### A story: two first requests that crossed

Controlvar's assignment is sticky: the first call for a user stores a row, every later call returns it.
The service checks for a stored row, then inserts one: two statements. On an API that runs as several
processes, two *first* requests for the same user can land on different processes. Both look and find
nothing. Both insert. The unique index refuses the second, and, until issue #1026, that request answered
a generic HTTP 500, for a user who was perfectly assignable. The fix is a few lines you are about to
read. The more instructive part is the test's docstring: *one process cannot show the bug*, because an
async handler runs one request's database work at a time, so an in-process concurrency test passes on the
broken code. The test has to *force* the interleaving. A test that cannot fail is not evidence; Hour 12
returns to this.

> **Did You Know?** The same race is possible for any "check, then insert" pattern with a unique index.
> The robust answer is rarely a bigger lock; it is to let the database arbitrate (the insert is the
> check) and handle the refusal by reading the winner, which is exactly what Controlvar does.

### How Controlvar does it

#### Let the database arbitrate, then answer with the winner

```python
# 📁 backend/app/services/assignment_service.py
def _is_unique_violation(exc: IntegrityError) -> bool:
    """True when the database refused *exc*'s write as a duplicate.

    Decided from the driver's SQLSTATE, never from the message. Which index
    refused it is settled by looking the row up afterwards, not by naming the
    index: its name depends on how the schema was built.
    """
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    return code == _UNIQUE_VIOLATION
```

Notice that the code decides "was this a duplicate?" from the driver's SQLSTATE (`23505`), "never from
the message". Messages are text in a locale; codes are contract. Now the insert itself:

```python
# 📁 backend/app/services/assignment_service.py
self.db.add(assignment)
try:
    self.db.commit()
except IntegrityError as exc:
    # The check above and this insert are two statements, and another
    # API process can store this user's first assignment between them:
    # the unique index on (experiment_id, user_id) then refuses this
    # row (#1026). The user is assigned, so the answer is the stored
    # row, given as a sticky hit gives it. Any other refusal, or a
    # duplicate with no stored row to answer, is raised as before.
    self.db.rollback()
    stored = (
        self._stored_assignment(user_id, experiment_id)
        if _is_unique_violation(exc)
        else None
    )
    if stored is None:
        raise
    logger.info(
        "First assignment in experiment %s was stored by a concurrent "
        "request; answering the stored row",
        experiment_id,
    )
    if recorded:
        # The rollback undid this request's population row. The write
        # is idempotent, so the user is recorded once whichever
        # request stored the assignment.
        self._record_holdout_population(holdout, user_id, holdout_check[0])
        self.db.commit()
    return self._sticky_result(
        user_id, experiment_id, stored, track_exposure, context
    )
```

Read the order of events. The code adds the row and commits. If the unique index refuses it, it rolls
back, looks up the stored row, and only if (a) the refusal was a unique violation *and* (b) a stored row
exists does it answer with that row, "as a sticky hit gives it". Anything else is re-raised, and the test
file pins this too: a foreign-key refusal still answers the fixed 500 and stores nothing. The line
`if recorded:` handles a subtle consequence: the rollback undid the holdout-population row this request
wrote, so it writes it again (idempotently) so the user is recorded once whichever request won.

The test's docstring is the best short explanation of why this bug hid:

```python
# 📁 backend/tests/integration/api/test_tracking_assign_first_conflict.py
One process cannot show it: the async handler runs one request's database
work at a time, so an in-process concurrent test passes on the old code. The
tests below force the interleaving instead. A hook on
``GlobalHoldoutService.is_user_in_holdout`` -- the first call after the
existence check, before the holdout population row and the insert -- has a
second session (the "other process") store the user's assignment and commit,
then lets the request carry on into its own insert.

The other process stores the variant the hash would NOT pick, so an answer
carrying that variant can only have come from the stored row.
```

> **Watch Out!** Sticky assignment is a storage fact, not a promise about hashing. The deterministic
> bucketing function helps new allocations agree, but stored rows, holdouts, mutual exclusion, targeting
> and bandit weights all matter. A hash is not a substitute for a persisted assignment or a concurrency
> policy, and the service checks eligibility in a fixed order (global holdout, mutual exclusion group,
> targeting) *before* choosing a variant for a new user.

#### Events: no idempotency key, and an easy way to think there is one

Look at what an event request is allowed to contain:

```python
# 📁 backend/app/schemas/tracking.py
class EventRequest(StorableTextModel):
    """
    Model for tracking an event through the public tracking API.

    Experiments and feature flags are referenced by their human-readable keys
    (the SDKs do not know internal ids); the endpoint resolves them and picks
    up the user's variant assignment automatically.
    """

    event_type: str = Field(..., min_length=1, max_length=100)
    event_name: Optional[str] = Field(None, max_length=255)
    user_id: str = Field(..., min_length=1, max_length=255)
    experiment_key: Optional[str] = Field(None, max_length=100)
    feature_flag_key: Optional[str] = Field(None, max_length=100)
    value: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None
```

There is no client-supplied idempotency key. A retried `POST /api/v1/tracking/track` that already
committed stores another row. Controlvar protects its *binary conversion numerator* from that (a user who
converted counts once, Hour 1) but not raw storage, sums over `value`, billing side effects or any other
consumer that counts rows.

> **Watch Out!** Unknown fields in an event request are **silently ignored**, not rejected. If you add an
> `idempotency_key` to your payload hoping the server will deduplicate on it, the field is dropped on
> parse and nothing is deduplicated. The Try It Yourself below shows it. Do not infer a server feature
> from a payload that the server accepted.

If you need retry-safe ingestion you must design it: a durable producer ledger, or a real server-side
idempotency contract. That contract has requirements you can list (the key's namespace, what happens when
the same key arrives with a different payload, how long keys are remembered, which transaction records
them, and what a replay returns). These are **recommended design requirements**, not features of the
current API.

#### Batches report item by item

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
logger.exception("Tracking batch item failed (%s)", type(e).__name__)
db.rollback()
failure_count += 1
errors.append(
    {
        "index": index,
        "event_type": event_request.event_type,
        "user_id": event_request.user_id,
        "error": failure_detail("Could not store this event"),
    }
)
```

The batch endpoint accepts up to 100 events (the schema refuses more) and returns
`{success_count, failure_count, errors}` with the input `index` of each failure. HTTP 200 therefore does
**not** mean every event was accepted. Note the `db.rollback()` after an unexpected failure: one bad item
must not poison the session for the next. Keep the input-to-result mapping on the client; replaying the
whole batch repeats the items that succeeded.

Also notice what the 500-class path returns: a *fixed sentence with a request id* (`failure_detail`), not
the exception text. Hour 8 explains why.

#### What the SDK retries, and what it must not

The Python SDK retries exactly one thing:

```python
# 📁 sdk/python/controlvar/client.py
response = self._send(method, url, data)
if response.status == 429:
    wait = _retry_after_seconds(response)
    logger.debug("429 from %s %s; retrying once after %.1fs", method, path, wait)
    self._sleep(wait)
    response = self._send(method, url, data)
```

Only HTTP 429, and only once, sleeping for `Retry-After` (capped). It does not retry timeouts, and it does
not retry 5xx. That is a considered choice. A 429 is safe to retry because the rate limiter answers
*before the route runs*: the limiter returns its response without calling the rest of the application, so
nothing was stored. A timeout is the ambiguous case. The JavaScript SDK documents the same shape: one retry
after `Retry-After`, bounded by the request timeout.

```python
# 📁 backend/app/middleware/rate_limiter.py
return JSONResponse(
    status_code=429,
    content={
        "detail": "Too Many Requests. Please slow down and retry after a moment.",
    },
    headers={
        "Retry-After": str(window),
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Window": str(window),
    },
)
```

#### A retry policy is a table, not an attitude

| Failure | Recommended client reasoning |
|---|---|
| Validation or permission refusal (400, 401, 403, 404, 422) | Fix the request or credential; retrying does not repair it |
| Rate limit (429) | Honour `Retry-After`; bound the attempts; nothing was stored |
| Read timeout (a flag evaluation, a lookup) | A bounded retry is reasonable; record exhausted attempts |
| Assignment timeout | Ask again: assignment is sticky, and a concurrent first request is arbitrated by the database |
| Event write timeout | **Ambiguous.** The policy must account for a possible duplicate |
| Partial batch result | Inspect item errors; do not replay the whole batch |

Use exponential backoff with jitter where retrying makes sense, plus a **total deadline**. A timeout budget
belongs to the caller's operation, not to each attempt separately. A bounded queue or an explicit failure
is preferable to unbounded memory growth during an outage. (The table is recommended practice, apart from
the 429 and batch rows, which restate what the server and SDK do.)

### Try It Yourself

No database needed. First, the silent drop and the batch limit:

```bash
python - <<'EOF'
from pydantic import ValidationError
from backend.app.schemas.tracking import EventRequest, EventBatchRequest
e = EventRequest(event_type="purchase", user_id="u1", idempotency_key="abc")
print("idempotency_key kept?", "idempotency_key" in e.model_dump())
print(sorted(e.model_dump()))
try:
    EventBatchRequest(events=[{"event_type": "x", "user_id": "u"}] * 101)
except ValidationError as ex:
    print("101 events:", ex.errors()[0]["type"], ex.errors()[0]["ctx"])

from types import SimpleNamespace
from sqlalchemy.exc import IntegrityError
from backend.app.services.assignment_service import _is_unique_violation
dup = IntegrityError("INSERT", {}, SimpleNamespace(pgcode="23505"))
fk = IntegrityError("INSERT", {}, SimpleNamespace(pgcode="23503"))
print("duplicate:", _is_unique_violation(dup), " foreign key:", _is_unique_violation(fk))
EOF
```

Expected result:

```text
idempotency_key kept? False
['event_name', 'event_type', 'experiment_key', 'feature_flag_key', 'metadata', 'timestamp', 'user_id', 'value']
101 events: too_long {'field_type': 'List', 'max_length': 100, 'actual_length': 101}
duplicate: True  foreign key: False
```

Second, the 429 contract. In the app clone, run the SDK's tests for the transport (they use fake
transports and need no server):

```bash
python -m pytest sdk/python/tests/test_transport.py sdk/python/tests/test_client.py -p no:cov -q
```

All tests should pass; read `test_client.py` for the cases that prove "retry once on 429, raise on
anything else". Third (needs PostgreSQL, so read it instead of running it): open
`backend/tests/integration/api/test_tracking_assign_first_conflict.py` and find the hook on
`GlobalHoldoutService.is_user_in_holdout`. It is the only place in the file where a second session
stores the user's assignment, and it is placed so the request's own insert is the loser.

Now the reasoning exercise. A client times out after sending one conversion. The operator sees one
assignment row and assumes replaying the conversion cannot duplicate data. What does the assignment
invariant prove?

**Answer.** Only that the experiment/user pair has one durable assignment. The event may already have
committed, and a replay can add another event row. Binary converting-user counts and raw event storage have
different duplicate behaviour. Inspect the event write path and decide the recovery policy before
promising anything.

### Key takeaways

- A unique index is an invariant; whether callers get a clean answer when it fires is a separate, testable
  property. Controlvar's assignment answers a lost race with the stored row.
- Decide "duplicate?" from the SQLSTATE, not the message; re-raise everything else.
- There is no idempotency key on event ingestion, and unknown request fields are silently ignored.
- HTTP 200 on a batch is not success for every item; keep the index mapping.
- The SDK retries a 429 once because nothing was stored; a timeout is ambiguous and is not retried.

**Review artifact:** a request failure matrix showing the commit boundary, the observable client result and
the recovery action for reads, assignments, events and partial batches.

---

<a id="hour-5--schedulers-replicas-and-stale-state"></a>

## Hour 5 — Schedulers, replicas and stale state: five loops, one lock

**Outcome:** explain what a scheduler lock coordinates, how failed ticks become visible, why a cache is a
separate consistency domain, and why a server-side change is not instant for an SDK holding a ruleset.

### What & why

#### Background work multiplies by your replica count

Controlvar starts five background schedulers in the API process: experiment scheduling, rollout
advancement, metrics aggregation, safety monitoring and bandit weight refresh. Run three API replicas, or
one replica with several workers, and you now have *N copies of each loop* racing against one database.
Without coordination, N copies of "advance this rollout stage" can advance it N times, and N copies of
"roll back this flag" can write N rollback records.

#### What a lock buys you, and what it does not

A lock coordinates holders *while they hold it*. It is not permanent leadership, and it is not a **fencing
token**: if a holder loses its session, the lock disappears, but side effects that holder already started
(a notification, a webhook) do not. So a lock answers "who may start work now?" and not "did the external
effect happen exactly once?". The second question needs its own identifier and acknowledgement.

#### A story: the cache that was deleted rather than repaired

For a while Controlvar had an opt-in cache for feature-flag detail and list reads. Five writers outside the
flag routes changed flags without dropping its entries, so the detail and list served stale values for up
to an hour. The team (issue #630) did not add five more invalidations. They **deleted the cache** and
added a test that makes it hard to bring back. A cache is a promise that something else keeps it true. The
cheapest fix for a promise you cannot keep is to stop making it.

> **By the Way.** That does not make every cache wrong. Experiment results are cached (5 minutes while an
> experiment runs, 24 hours otherwise, with an endpoint to invalidate them), the SDKs cache per user and
> key for five minutes, and a local-evaluation SDK holds a whole ruleset. Each has its own authority, age
> and invalidation path; the lesson is to *write them down*, not to avoid caching.

### How Controlvar does it

#### The lock

```python
# 📁 backend/app/core/scheduler_lock.py
def try_acquire(conn: Connection, name: str) -> bool:
    """Attempt to take the advisory lock for *name* on *conn* (non-blocking)."""
    row = conn.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": lock_name(name)}
    ).scalar()
    return bool(row)
```

`pg_try_advisory_lock(hashtext(name))` is **non-blocking**: a replica that does not get the lock skips the
tick instead of queueing behind the holder. The lock key is derived from the name, so every replica
computes the same key without a registry. The module docstring explains the other decisions, and they are
worth reading as a list of things that go wrong:

```python
# 📁 backend/app/core/scheduler_lock.py
* The lock is session-scoped, so it is taken on a **dedicated connection**
  checked out from the engine for the duration of the ``with`` block and
  released with ``pg_advisory_unlock`` on exit (also on exceptions). Closing the
  connection would release it too, but an explicit unlock keeps pooled
  connections clean.
* The connection runs in AUTOCOMMIT mode: a session-level advisory lock does
  not need a transaction, and without autocommit the ``SELECT`` would leave the
  connection "idle in transaction" for the whole tick, which managed Postgres
  services kill after ``idle_in_transaction_session_timeout`` (silently
  releasing the lock mid-tick).
```

Two things to take from it. The lock lives on a **dedicated connection** for the duration of the tick,
so a pooled connection cannot be returned to the pool with the lock still held. And the connection runs in
**autocommit**, because otherwise the `SELECT` would leave it "idle in transaction" for the whole tick,
which managed PostgreSQL services kill after a timeout, "silently releasing the lock mid-tick". That is a
fencing problem in miniature: the lock vanished while the work continued.

When the database is unreachable the context manager yields `False` and logs a warning. A scheduler must
never crash the API because it could not coordinate; it skips the tick. The price is that scheduled work
may be delayed, which is why the next section matters.

> **Watch Out!** `async_scheduler_lock` does the blocking connect, acquire and release in a worker thread
> (`asyncio.to_thread`), so an unreachable database blocks only that thread and never the event loop that
> serves HTTP. If you write a new scheduler, call the async form from coroutines.

#### Make failures visible: run history and a success timestamp

All five schedulers run through one function. Read its docstring for the three guarantees, then its core:

```python
# 📁 backend/app/core/scheduler_tick.py
async with async_scheduler_lock(name, engine=engine) as acquired:
    if not acquired:
        completed_at = datetime.now(timezone.utc)
        prom.record_scheduler_tick(name, STATUS_SKIPPED, time.perf_counter() - t0)
        logger.info("scheduler %s: lock held elsewhere, tick skipped", name)
        return TickOutcome(
            name=name,
            status=STATUS_SKIPPED,
            acquired=False,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=completed_at.timestamp() - started_at.timestamp(),
            result=TickResult(),
        )

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
    except asyncio.CancelledError:
        # Shutdown: release the lock (context manager) and propagate
        # without recording a bogus "failed" run.
        raise
    except Exception as exc:
        status = STATUS_FAILED
        error = str(exc) or exc.__class__.__name__
        exc_to_raise = exc
```

If the lock is not acquired, the tick is recorded as `skipped` (not as an error) and the function
returns. If it is acquired, the outcome is `success`, `partial` or `failed` depending on items processed and
failed, and an exception is recorded as a failed run and then **re-raised** so each scheduler loop keeps its
own back-off. `CancelledError` (shutdown) releases the lock and propagates *without* recording a bogus
failure. The run record feeds `GET /api/v1/scheduler/health` and the run history, and Prometheus gets
`scheduler_last_success_timestamp{name}`, a tick duration and a tick counter.

That is the answer to "monitor last successful work, not merely whether a loop is alive". A loop can be
alive, ticking, skipping and failing forever. The metric to alert on is the *age of the last success*.

#### The cache that is gone, and the test that keeps it gone

```python
# 📁 backend/tests/smoke/test_no_flag_cache.py
"""Nothing caches a feature flag's detail or list (#630).

The opt-in flag cache (#100) was deleted rather than repaired: five writers
outside the flag routes changed flags without dropping its entries, so the
detail and list served stale values for up to an hour. Every flag read now
comes from the database. This pins that, across the core and the modules:

* no source file under ``backend/app`` or ``modules/backend/app`` writes or
  reads a ``feature_flag:``/``feature_flags:`` key (a quoted literal starting
  with either prefix -- f-strings and ``scan_iter`` patterns included);
* ``deps.get_cache_control`` -- the dependency that hands out the Redis
  client -- is referenced only by the files that still cache (the experiment
  routes and the admin cache routes) and by ``deps.py``, which defines it;
* no route under ``/api/v1/feature-flags`` depends on it.

No exception: ``admin.py``'s ``CACHE_NAMESPACES`` has no flag entry.
```

The docstring is a design record: what was deleted, why, and the three things the test pins (no source file
names a flag cache key, the Redis-handing dependency is referenced only by files that still cache, no flag
route depends on it). The note "no exception" is the important sentence in a gate: an allowlist with a
convenient entry is how a gate stops gating.

#### Server state versus SDK-held state

With local evaluation, a server-side SDK downloads the flag ruleset and answers in-process. That is a
cache with a refresh interval (default 30 seconds, minimum 5) and a documented behaviour when the refresh
fails: with a ruleset loaded, a 5xx, timeout or network error means **keep serving the old ruleset**;
a 401 or 403 (key revoked, scope removed, owner demoted) means **discard it and ask the server**; a 429
waits at least `Retry-After`.

> **Did You Know?** The documentation states the sharp edge plainly: "a flag turned off during an outage
> stays on in that process until the API is reachable again." The optional `maxStaleMs` /
> `max_stale_seconds` setting makes an old ruleset hand evaluation back to the server. The default is no
> limit. Choose it on purpose for flags that guard something dangerous.

For each cache in your own design, write the **authority**, the **key**, the **maximum age**, the
**refresh/error behaviour** and the **invalidation path**, then exercise both warm and cold. The shared
sentence for SDK-held state is: *a rollback at the API is not instantaneous invalidation of every
process.*

### Try It Yourself

Prove the skip path without a database by replacing the lock with one that is "held elsewhere":

```bash
python - <<'EOF'
import asyncio
from contextlib import asynccontextmanager
import backend.app.core.scheduler_tick as st
from backend.app.core.scheduler_lock import lock_name

@asynccontextmanager
async def held_elsewhere(name, engine=None):
    yield False

st.async_scheduler_lock = held_elsewhere

async def work():
    raise AssertionError("must not run")

out = asyncio.run(st.run_locked_tick("safety", work, record=False))
print(out.name, out.status, out.acquired)
print(lock_name("experiment"))
EOF
```

Expected result (the Cognito notice may also appear):

```text
safety skipped False
experimently.scheduler.experiment
```

The second line shows a rename that did not happen: the advisory-lock namespace still carries the old
product name because changing it would change the key every running replica agrees on.

Now run the guard test and plant a defect (do this in a scratch copy of the app clone, never in a clone
you care about):

```bash
python -m pytest backend/tests/smoke/test_no_flag_cache.py -p no:cov -q
```

It reports `4 passed`. Add a line containing the quoted text `"feature_flag:123"` to any file under
`backend/app` in your scratch copy, run the test again, and watch it fail naming the file. Remove the
line and watch it pass. A gate you have only seen pass is not a gate.

(The lock itself is tested against PostgreSQL in `backend/tests/unit/core/test_scheduler_lock.py`; those
tests need a database and are not run here.)

Now the reasoning exercise. An SDK sends a batch of ten events; six commit and four are rejected; the
client retries all ten. Separately, one scheduler loses database access during a tick. What can you
conclude?

**Answer.** The accepted six may now be stored twice; inspect per-item results and do not promise
exactly-once ingestion. Binary converting-user counts may be unchanged, but that says nothing about raw
event totals. The scheduler skips ticks while it cannot reach the database, and loss of the lock session
does not prove an external effect happened exactly once. Check the age of `scheduler_last_success_timestamp`,
the run history, durable state and external effect identifiers before deciding on recovery.

### Key takeaways

- N replicas means N copies of every loop; a non-blocking advisory lock lets one run each tick.
- A lock is not a fencing token. Autocommit and a dedicated connection keep it from vanishing mid-tick.
- Alert on the age of the last *successful* tick, not on process liveness.
- A deleted cache and a test that forbids it can be the right fix. Every remaining cache needs an
  authority, an age and an invalidation path.
- An SDK-held ruleset obeys its own refresh and failure rules; a server-side rollback reaches it later.

**Review artifact:** a failure matrix with before-commit, after-commit/before-response, concurrent
request, partial-batch and stale-cache cases, each with the observable result and recovery action.

---

<a id="hour-6--database-evolution-and-performance"></a>

## Hour 6 — Database evolution and performance: the previous release is still running

**Outcome:** propose a schema change that survives a rolling deployment, investigate a slow analysis query
without changing its meaning, and budget database connections across processes.

### What & why

#### Expand, then contract

During a rolling upgrade, release N-1 keeps serving against release N's schema after N's migration has
run. If N's migration drops or renames anything N-1 uses, the old workers break. So changes happen in two
releases: **expand** (add the new column or table, make the code tolerate both), then, in a later release,
after the supported rollback window closes, **contract** (drop the old one). A direct rename is a
contract and an expand in one step, and it is a bug waiting for the first slow deploy.

#### Migration success is only one compatibility check

A migration that applies to an empty database proves little. Draw the matrix:

| Combination | Required question |
|---|---|
| Previous application + expanded schema | Does the old code still read and write correctly? |
| New application + expanded schema | Can it handle rows that are not yet backfilled? |
| Old and new writers together | Do both preserve the same invariant? |
| Rollback to the previous application | Can it serve without undoing the schema? |
| Core and full profiles | Does each apply only its intended migration work? |

#### A story: events stored as text

`events.created_at` is a text column. Every time window, ordering and "earliest conversion" over events
compares strings, which sorts correctly only if every value is UTC and the same shape. Events stored before
the fix kept whatever the client sent: `+02:00`, `Z`, no offset. A reader could then see an event fall
outside the window it belonged in, or the wrong event become a user's "first conversion". The fix had two
parts: write every new value in one canonical UTC form, and a migration that rewrites the old ones. The
migration documentation's own warning is the lesson in rewriting data: *the downgrade cannot bring back
the offsets clients sent, because the offset was never recorded.* If you need the original text, snapshot
before upgrading.

### How Controlvar does it

#### The canonical form is written at the boundary

```python
# 📁 backend/app/models/event.py
def normalize_event_timestamp(value: Any) -> str:
    """Return ``value`` as a UTC ISO-8601 string in one canonical format.

    ``events.created_at`` is a string column, so every time window over events
    is a string comparison.  That only orders correctly when every value is in
    UTC and in the same shape, so every value is converted to UTC and written
    the way ``datetime.isoformat()`` writes an aware UTC datetime:
    ``YYYY-MM-DDTHH:MM:SS+00:00``, or ``YYYY-MM-DDTHH:MM:SS.ffffff+00:00``
    when there are microseconds.  Those two shapes still sort in time order
    ('+' sorts before '.', and a value with no fraction is the earlier one),
    and they are what the server has always written for its own timestamps,
    so rows written that way compare correctly with new ones.  A value with
    no offset is taken to be UTC already.

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

The function accepts a `datetime` or an ISO-8601 string, treats a value with no offset as UTC, and raises
`ValueError` for anything else, "so an unreadable timestamp is refused rather than stored and compared as
text". A column type decorator applies it at bind time, so every writer (the API, the event service, seed
scripts) gets it without remembering to call it. When the same rule must hold for every writer, put it
where all writers pass.

#### What a good migration says about the previous release

Read the opening of a recent, deliberately small core migration:

```python
# 📁 backend/app/db/migrations/versions/4c2c09a2b3e8_rollback_records_keep_without_a_safety_config.py
"""Safety rollback records: safety_config_id nullable, its foreign key ON DELETE SET NULL

Downgrade changes nothing: the column stays nullable and the key stays SET NULL.

A rollback no longer gives a flag that has no safety configuration of its own
an empty one, which took the flag off the global default thresholds (#1206).
The rollback record of such a flag has no configuration to point at, so:

* ``safety_rollback_records.safety_config_id`` drops NOT NULL;
* its foreign key to ``feature_flag_safety_configs.id`` becomes ON DELETE SET
  NULL (it was ON DELETE CASCADE), so deleting a configuration keeps the
  flag's rollback records and clears their link instead of deleting them.

Schema only: no row of any table is changed or deleted. The empty configurations
earlier releases added at a rollback stay where they are.

Each step runs only when it is needed, as ``37dcb2969766``'s do: a schema built
by ``db/bootstrap.py``'s ``create_all`` already has both (they are on the
model), and the transition tests rewind such a schema's ``alembic_version`` and
replay the chain from there. The foreign key is found by its column, not by its
name, and is created again under PostgreSQL's default name,
``safety_rollback_records_safety_config_id_fkey``, the name both
``create_safety_tables`` and ``create_all`` give it. The upgrade reads the
catalog, so it cannot be emitted as SQL (``--sql``).

The previous release runs against this schema unchanged: it writes a
configuration id on every record, which a nullable column accepts, and it
reads records whose id is NULL.

Core chain, extending the core head ``37dcb2969766`` (#440). Nothing here
touches a module table, so a full checkout still has exactly two heads: this
revision and ``modules_0002_warehouse_analysis``.

Revision ID: 4c2c09a2b3e8
Revises: 37dcb2969766
Create Date: 2026-10-13
```

Four habits to copy. It states **what it changes in plain words** (a column drops NOT NULL; a foreign key
becomes ON DELETE SET NULL). It states that it is **schema only** and no row is changed or deleted. It
says its steps run only when needed (so a schema built by `create_all` and one built by migrations both
reach the same state) and that the upgrade reads the catalog, so it cannot be emitted as SQL. And it
states the compatibility sentence: *"The previous release runs against this schema unchanged."* That
sentence is the expand rule applied.

#### Two heads is the normal state

A full checkout has two Alembic heads, the core chain and the `modules` branch, so the commands say
`heads`, never `head`. Check:

```bash
python -m alembic -c backend/app/db/alembic.ini heads
```

At v0.36.4 this prints two lines:

```text
4c2c09a2b3e8 (head)
modules_0002_warehouse_analysis (modules) (head)
```

Never run `alembic merge` to collapse them: the merge file would land in the core directory and reference a
revision that a core checkout does not have, so every core build would fail to load its migration
directory. The new revision names the head it extends (`--head <id>`).

> **Watch Out!** `alembic stamp` records a revision without running it. Stamping to "fix" a mismatch hides
> missing migrations. And never edit the `alembic_version` table to make an older image start: the app's
> own error message and tests (`test_newer_database_rollback.py`) exist because that advice used to be
> given, and it leaves the newer release's tables in place and unrecorded.

#### An older image against a newer database

Rolling back to an older image after a release that carried a migration leaves the database at a revision
the old image has no file for. Controlvar's answer is to refuse, loudly, before touching anything:

```python
# 📁 backend/app/db/bootstrap.py
def may_run_alembic(cfg: Config, recorded: set[str], schema: str) -> bool:
    """Whether alembic may be run against a schema recorded at *recorded*.

    The guard for a database recorded at a revision this build has no file
    for.  Two ways to get there:

    * a **full database opened by a core build** -- the reverse profile
      switch.  ``alembic_version`` then holds the modules head
      (``modules_0002_warehouse_analysis``, or ``modules_0001_rbac`` on a
      database an older full release migrated), which a core tree has no file
      for.  ``deploy.yml`` runs the migration task with
      the image it is deploying, so the first ``profile=core`` deploy after an
      environment's stacks are redeployed from a core checkout, onto a
      database a full release migrated, is exactly this;
    * a **newer database opened by an older build** -- a rollback to an older
      image after a release that added a migration.

    Raw alembic answers *every* command there with ``CommandError: Can't
    locate revision identified by ...`` before applying anything, and it fails
    the deploy job.

    This function is what makes every documented path answer the same way, so
    it is called from ``db/bootstrap.py`` *and* from ``migrations/env.py``, for
    an ``upgrade`` however it was launched.  Not for ``downgrade`` or ``stamp``:
    returning False means "succeed having done nothing", which is honest only
    where there is nothing left to do, and those two would then exit 0 with the
    schema and the version table untouched (``env.py``'s
    ``_SKIPPABLE_COMMANDS``).  The three answers:

    * nothing foreign recorded -> ``True``, run normally;
    * foreign rows but this build's own heads are all applied -> log a WARNING,
      return ``False``, leave the rows alone.  There is nothing to do, so
      "succeed having done nothing" is the honest answer -- not a traceback
      that stops a container;
    * foreign rows *and* this build has migrations of its own to apply ->
      ``RuntimeError`` naming the revisions, which of the two cases this is,
      and the way out (:func:`unresolvable_revisions_message`).  Refusing is
      the only safe answer: alembic cannot plan a path from a revision it
      cannot resolve.
    """
```

The three-way decision in the docstring is worth restating: nothing foreign recorded, run normally;
foreign rows but this build's own heads are all applied, warn and do nothing; foreign rows *and* pending
migrations, raise with a message that names the revisions and the way out. The two ways out are to run the
newer release that migrated the database, or restore the backup taken before the upgrade. On the AWS
deployment the API task sets `RUN_MIGRATIONS=false`: only the migration task the deploy workflow runs
before shifting traffic writes the schema, and an infrastructure test pins that value. Compose runs one
API container that migrates on start. Do not copy one environment's assumption into another.

> **By the Way.** The bootstrap serialises replicas with a *blocking* session-level advisory lock
> (`experimently.bootstrap.<schema>`): the first replica creates or upgrades the schema, the others wait
> and find nothing left to do. Compare the scheduler's non-blocking lock. Waiting is right for a one-time
> setup step and skipping is right for a recurring tick.

#### A worked, illustrative change

Suppose you want to record an acquisition channel with each assignment. This is an **illustrative** change;
the column does not exist. Expand: add a nullable column and define what null means. Make new readers
tolerate null. Do not require historical rows to have a value, and retain null rather than invent data if
the channel cannot be reconstructed. Backfill, if there is a trustworthy source, in bounded batches with a
stable cursor and a resume position, measuring lock time, write load and replica lag; zero remaining nulls
is not evidence the values are right. Move reads over only after mixed-writer behaviour is verified. Only in
a later release consider a stronger constraint. Dual writes need a declared authority and reconciliation;
they are not automatically consistent.

Plan application rollback separately from database recovery: prefer a backward-compatible schema so the old
application can keep serving. Some downgrades drop data and some rewrites cannot be undone. A restore from
backup may lose writes after the restore point; rehearse the disaster-recovery procedure and name the
data-loss window.

#### Preserve semantics while optimising

Hour 1 showed the measured reason the conversion counts avoid one big SQL join. Replacing that code with a
tidier join "may reintroduce a measured failure". To investigate a slow query, capture its SQL with
synthetic identifiers, inspect the plan against realistic row counts, compare estimated and actual rows,
loops and sort spills. `EXPLAIN ANALYZE` *executes* the statement, so on a write it would perform the
write; use read-only `SELECT`s. An index that helps one experiment/user lookup may not help a broad time-range
aggregation, and every index costs writes. Partitioning, pre-aggregation and archival are possible future
designs, not features implied by a large events table.

#### Connection budgets

The engine's pool is configured in one place:

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
        "connect_timeout": 10,  # Connection timeout in seconds
        "keepalives": 1,  # Enable keepalive
        "keepalives_idle": 30,  # Idle time before sending keepalive
        "keepalives_interval": 10,  # Interval between keepalives
        "keepalives_count": 5,  # Number of keepalive attempts
    },
)
```

Each worker process may hold up to `pool_size + max_overflow` = 40 connections, waiting up to 30 seconds
for one, and `WEB_CONCURRENCY` defaults to 1 worker per container. Budget it: three API replicas with two
workers each is 3 × 2 × 40 = **240** connections at peak, before the schedulers' dedicated lock connections
(one per tick attempt), migrations and anyone with administrative access. A pool size that looks small per
process can exhaust the database across a deployment, and adding replicas can make a database bottleneck
*worse*. Measure queue time, transaction duration and database saturation before raising any limit.

### Try It Yourself

Check the canonical-time rule on the offsets from the migration notes:

```bash
python - <<'EOF'
from backend.app.models.event import normalize_event_timestamp as n
for v in ["2026-10-01T01:00:00+02:00", "2026-10-01T01:00:00Z", "2026-10-01T01:00:00",
          "2026-10-01T01:00:00.250000-05:00"]:
    print(v, "->", n(v))
try:
    n("yesterday")
except ValueError as e:
    print("refused:", e)
EOF
python -m alembic -c backend/app/db/alembic.ini heads
```

Expected result:

```text
2026-10-01T01:00:00+02:00 -> 2026-09-30T23:00:00+00:00
2026-10-01T01:00:00Z -> 2026-10-01T01:00:00+00:00
2026-10-01T01:00:00 -> 2026-10-01T01:00:00+00:00
2026-10-01T01:00:00.250000-05:00 -> 2026-10-01T06:00:00.250000+00:00
refused: Invalid isoformat string: 'yesterday'
4c2c09a2b3e8 (head)
modules_0002_warehouse_analysis (modules) (head)
```

(Your Python version may word the last `ValueError` slightly differently.) The first line is the example
in the migration notes: `+02:00` becomes the same instant in UTC.

Exercise on paper: a patch renames a column, adds a non-null constraint and backfills the full events
table in the same transaction. It passes against an empty database. What should change?

**Answer.** Separate expansion, compatible application changes, a resumable bounded backfill and a later
contraction. Verify against real historical data and the overlap with old writers; measure lock and
runtime behaviour; add rollback and profile-compatibility evidence. An empty-database pass cannot reveal
blocked writes, incompatible old workers or the cost of updating millions of rows.

### Key takeaways

- Expand, then contract; the previous release keeps serving after the new migration runs.
- State the compatibility of the previous release in the migration itself, as a sentence a reviewer can check.
- Two heads is steady state; use `heads`, name the head you extend, never merge, never edit the version table.
- Preserve query semantics when optimising, and remember a measurement is why the code looks odd.
- Budget connections as replicas × workers × (pool + overflow), plus everything else that connects.

**Review artifact:** a compatibility matrix, measured migration and lock behaviour, a restartable backfill
design, before/after query plans with equal results, and an explicit recovery path. The existing migration
transition tests under `backend/tests/integration/database/` show the expected branch behaviour.

---

## Part II Workshop

### Q&A

**Why can a single durable assignment coexist with a failed first request?** Uniqueness governs stored
rows; the response can fail before the caller receives confirmation. Controlvar answers a lost race with the
stored row, but a network failure is still a failure.

**Does a scheduler advisory lock make external effects exactly once?** No. The session can be lost, and a
notification has its own commit and acknowledgement boundary.

**Why does the SDK retry a 429 but not a timeout?** A 429 is produced before the route runs, so nothing was
stored. A timeout may follow a commit.

**Why is "two heads" not an error?** The core chain and the module branch are separate on purpose so that
deleting `modules/` leaves a consistent core chain.

### Quiz

1. Is replaying an entire partially successful event batch necessarily safe?
2. Does a successful migration on an empty database demonstrate mixed-version compatibility?
3. Can revoking an API key recall a ruleset an SDK already downloaded?
4. What does a scheduler tick report when another replica holds the lock?
5. Name two reasons an exclusive "check, then insert" needs the database to arbitrate.

**Answers.** (1) No; the successful items can be stored twice. (2) No; test old and new writers and
historical rows against the expanded schema. (3) No; the process keeps serving the ruleset it holds until
its next refresh answers 401/403, so account for refresh and process-held state. (4) `skipped`, recorded
as a run but not as a failure. (5) Two processes can both pass the check before either inserts, and the
unique index is the only component that sees both writes.

### Exercises

1. **Failure matrix.** Build a table for `assign`, `track` and `batch` with columns: before commit, after
   commit/before response, concurrent duplicate, partial result. Fill in what the client sees and the safe
   recovery.
2. **Ledger design.** Sketch the producer-side ledger that would make event ingestion retry-safe without a
   server key: what is stored, when it is marked sent, and how a replay is detected.
3. **Cache inventory.** List five caches or held states in a system you run (Controlvar or otherwise) with
   authority, key, maximum age, error behaviour and invalidation path.
4. **Budget.** For 4 replicas, 3 workers each and the pool settings above, compute the peak connections and
   say what you would measure before changing `pool_size`.

### Solutions

1. For `assign`: before commit, nothing stored and a retry is safe; after commit/before response, a retry
   returns the stored row (sticky); concurrent duplicate, the loser answers with the winner's row; no
   partial result. For `track`: before commit, retry is safe; after commit/before response, a retry stores
   another row (ambiguous); concurrent duplicate, both store; for `batch`, a partial result needs the item
   index mapping and per-item retry.
2. Store (local id, payload hash, destination, state) before sending; mark sent only on a confirmed 2xx
   per item; on retry, resend only unconfirmed ids; accept that a crash between server commit and local
   mark can still duplicate once, and deduplicate downstream on the local id if the destination allows it.
3. A good inventory includes the SDK per-user assignment cache (authority: the server, key: user+key,
   age 300 s default, errors not cached, invalidation: `clear_cache()`), the results cache (5 min/24 h,
   invalidate endpoint) and a local-evaluation ruleset (30 s refresh, discard on 401/403).
4. 4 × 3 × 40 = 480 connections at peak, plus lock and admin connections. Measure queue wait (pool timeout
   is 30 s), transaction duration, and database saturation (active connections against the server's limit)
   before raising anything.

---

[← Part 1](./part-1-experiment-design.md) · [Contents](./README.md) · [Part 3 →](./part-3-security-and-privacy.md)

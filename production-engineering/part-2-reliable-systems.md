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

Keep the app clone at this book's pin, `09d815ea`, open. The Try It Yourself blocks run real pieces of the
backend without a database (set the safety variables from Hour 1 first). The ones that need PostgreSQL say
so and point at the existing test instead of asking you to start one.

---

<a id="hour-4--assignments-transactions-and-retries"></a>

## Hour 4 — Assignments, transactions and retries: two requests, one user

**Outcome:** distinguish assignment uniqueness, event storage and response delivery, explain what a retry
can repeat when a write commits before the client hears about it, read an idempotency contract as built,
and state which of those cases Controlvar handles for you and which it leaves to the sender.

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
checks. Everything else is a policy about *how much duplication you will tolerate*. Controlvar's event API
now offers the first remedy, and this Hour reads it line by line, because a key the server honours only
when the client sends it is a contract with two sides.

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

#### Events: an idempotency key the sender has to supply

Until v0.40.0 an event request had no key at all, and a retried `POST /api/v1/tracking/track` that had
already committed stored a second row. While every metric was analysed as a conversion, that mostly did
not show: a user who converted counts once however many rows they have (Hour 1). Once `/results` began
analysing revenue, count and duration metrics as a mean per assigned user (v0.38.0), every repeat added its
value again. Issue #1543
names it in its title: "a retried event is stored twice, so revenue and count metrics double-count it".
The regression test for the fix says what the old code got wrong, with numbers: "on the code before
#1543 the control revenue mean is 4.0 and the count mean 0.2, because the event sent again is stored
again." A test that states how it fails on the old code is one you can trust (Hour 12).

Here is what an event request may contain now:

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
    event_id: EventId = None
```

The last line is the whole client-facing change. `EventId`'s own comment is the input contract:

```python
# 📁 backend/app/schemas/tracking.py
#: The ``event_id`` an event sent to ``/tracking/track``, ``/tracking/batch``
#: or ``/tracking/events`` may carry (#1543). A JSON string of at most 255
#: characters, compared exactly (case-sensitive, not trimmed). ``""`` becomes
#: ``None``. Anything else -- a number, a longer string, and (through
#: ``StorableTextModel``) NUL or a lone surrogate -- is refused with 422.
```

Notice how much is decided in five lines: the type (a string; a number is refused, so no client library
can turn `1001` into `1001.0` on the way), the length, the comparison (exact, so `Order-1` and `order-1`
are two events), and what "no key" looks like (`null`, an absent field and `""` all mean none). Each of
those is a question two client libraries would otherwise answer differently.

> **Watch Out!** Unknown fields in an event request are still **silently ignored**, not rejected. If you
> send an `idempotency_key`, or `eventId` straight to the HTTP API, the field is dropped on parse and
> nothing is deduplicated. The field is `event_id`, and the Try It Yourself below shows the difference. Do
> not infer a server feature from a payload that the server accepted.

#### The key, computed in one place

Which events count as "the same"? The server computes one key per event, in one function every route
calls:

```python
# 📁 backend/app/services/event_dedup.py
The parts are length-prefixed and hashed, so the key has a fixed length
whatever the text (a 255-character ``event_id`` of four-byte characters
included) and no two different tuples of parts can encode to the same bytes:
``("ab", "c")`` and ``("a", "bc")`` differ, which a separator would not
guarantee.
...
def _encoded(part: str) -> bytes:
    """``<byte length>:<UTF-8 bytes>``: the length is ASCII decimal digits."""
    data = part.encode("utf-8")
    return str(len(data)).encode("ascii") + b":" + data
...
    digest = hashlib.sha256()
    for part in (
        _id_text(experiment_id),
        _id_text(feature_flag_id),
        event_name,
        event_id,
    ):
        digest.update(_encoded(part))
    return digest.hexdigest()
```

The key is the SHA-256 of four parts: the experiment and the flag the event resolved to when it was
received, its event name, and the `event_id`. The user is not hashed; it is the other half of the table's
primary key. Two decisions are worth stealing. **Length-prefix, don't join with a separator:** joining
`"ab"` and `"c"` with `|` is safe only until a value contains `|`; a length prefix cannot be forged by the
content. **Never recompute a stored key:** the module says a key is not recomputed "when a flag is
deleted", "when an event is re-tagged" or "when an experiment changes status", and its unit test pins the
bytes hashed for each vector, because changing the function would silently stop recognising every event
already stored. A hash that is stored is a schema, and it changes like one.

#### The claim, in the event's own transaction

```python
# 📁 backend/app/services/event_service.py
The claim is ``INSERT ... ON CONFLICT DO NOTHING RETURNING``. A claim
on a key that an uncommitted transaction holds waits for it: when that
one commits this is a duplicate, when it rolls back this one stores.
So a duplicate or a race raises nothing and logs nothing.
...
claimed = self.db.execute(
    pg_insert(keys)
    .values(user_id=event.user_id, dedup_key=key, stored_event_id=claim_id)
    .on_conflict_do_nothing(
        index_elements=[keys.c.user_id, keys.c.dedup_key]
    )
    .returning(keys.c.stored_event_id)
).scalar_one_or_none()
if claimed is not None:
    event.id = claimed
    self.db.add(event)
    self.db.commit()
    self.db.refresh(event)
    return event.id, True, event
stored_id = self.db.execute(
    select(keys.c.stored_event_id).where(
        keys.c.user_id == event.user_id, keys.c.dedup_key == key
    )
).scalar_one()
self.db.rollback()
return stored_id, False, None
```

Compare this with the assignment race earlier in this Hour. There the code inserted, caught the unique
violation and looked the winner up. Here the insert *is* the question: `ON CONFLICT DO NOTHING RETURNING`
answers "did I claim it?" without raising, and the claim and the event commit together, so a key never
exists for an event that was not stored. If two requests carry the same key at once, PostgreSQL makes the
second wait for the first transaction; it becomes a duplicate if the first commits and stores normally if
the first rolls back. The integration test for that case holds the first transaction open until it has
seen the second waiting, then checks both outcomes; it needs PostgreSQL and is not run here.

#### The contract, as built

The previous edition of this book listed the requirements an idempotency contract would have to answer
and called them design work for the future. Here is how the code answers each one at the pin:

| Requirement | What Controlvar does | Where it is decided |
|---|---|---|
| The key's namespace | One user; within the user, the experiment and flag the event resolved to at receipt, the event name (`/track` and `/batch` default it to the event type) and the `event_id` | `event_dedup.py`; the primary key `(user_id, dedup_key)` |
| The same key with a different payload | A duplicate: nothing is written, and the first event sent is kept. The later one's value, metadata and timestamp are not compared and not stored | `track_event_once`; the app's "How events count" guide |
| How long keys are remembered | As long as the row exists, and nothing purges them. A key outlives its event | `models/event_dedup_key.py` |
| Which transaction records them | The event's own: the claim and the insert commit together | `track_event_once` |
| What a replay returns | `/track` and `/events`: `200`, `"duplicate": true` and the stored event's `id`. `/batch` counts it in `success_count` and in `duplicate_count` | `_event_response`, `EventBatchResponse` |
| An event with no `event_id` | Stored every time, as before: "nothing compares content" | the regression tests |

> **Watch Out!** A duplicate's answer echoes *this* request. The `id` is the stored event's, but "every
> other field is this request's or this request's time". A client that resends a purchase with a corrected
> value and reads `value` from the answer sees its own correction, which the server did not store. Read
> `duplicate` first.

Two consequences follow from the namespace. The same `event_id` given to two different event names
(`checkout_completed` and `purchase`) is two events. And an SDK that fans one key-less event out to each
of a user's experiments and flags sends the same `event_id` on every entry, so the event counts once *in
each* experiment, which is what you want.

#### Who supplies the id

The server makes no id. The Python and JavaScript SDKs send the `event_id` you give them and make none
themselves; the app's guide says the Java SDK gives every event one, and no other SDK has the option yet.
The guide's advice is the right one for any idempotency key: make the id once, when the event happens,
from something your own system decides (an order id for a server-side event, a random UUID otherwise),
and send that same id every time. A new id per attempt is a new event per attempt.

So the honest summary is: **retries of events are safe exactly when the sender attaches a stable
`event_id`.** For senders that cannot, the old remedy still applies: a durable producer ledger that
records what it sent (Exercise 2 below).

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
`{success_count, failure_count, duplicate_count, errors}` with the input `index` of each failure. HTTP 200
therefore does **not** mean every event was accepted. Note the `db.rollback()` after an unexpected failure:
one bad item must not poison the session for the next. Items commit one at a time, so the same `event_id`
twice in one batch is stored once. Keep the input-to-result mapping on the client. Replaying the whole
batch repeats the items that succeeded unless every item carries its `event_id`; then the ones already
stored come back in `duplicate_count` and nothing is written for them. One malformed `event_id` (a number,
or more than 255 characters) refuses the whole batch with 422, before any item is stored, because the
request schema validates the list as one body.

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
after `Retry-After`, bounded by the request timeout. The event id did not change that choice: the SDK
still never retries a timeout for you. What it changed is that *your* code can, safely, by calling `track`
again with the same `event_id`; the Python SDK's `track_batch` says a chunk resent after a 429 carries "the
same ids".

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
| Event write timeout, with an `event_id` | Send it again with the **same** id: a stored first attempt answers `duplicate: true` |
| Event write timeout, without one | **Ambiguous.** The policy must account for a possible duplicate |
| Partial batch result | Inspect item errors; replay only the failed items, or the whole batch if every item carries its `event_id` |

Use exponential backoff with jitter where retrying makes sense, plus a **total deadline**. A timeout budget
belongs to the caller's operation, not to each attempt separately. A bounded queue or an explicit failure
is preferable to unbounded memory growth during an outage. (The table is recommended practice, apart from
the 429, `event_id` and batch rows, which restate what the server and SDK do.)

### Try It Yourself

No database needed. First, the silent drop, the event id's input rules, the key and the batch limit:

```bash
python - <<'EOF'
from pydantic import ValidationError
from backend.app.schemas.tracking import EventRequest, EventBatchRequest
e = EventRequest(event_type="purchase", user_id="u1", idempotency_key="abc")
print("idempotency_key kept?", "idempotency_key" in e.model_dump())
print(sorted(e.model_dump()))
print("empty event_id:", EventRequest(event_type="purchase", user_id="u1", event_id="").event_id)
try:
    EventRequest(event_type="purchase", user_id="u1", event_id="x" * 256)
except ValidationError as ex:
    print("256-character event_id:", ex.errors()[0]["type"])
try:
    EventBatchRequest(events=[{"event_type": "x", "user_id": "u"}] * 101)
except ValidationError as ex:
    print("101 events:", ex.errors()[0]["type"], ex.errors()[0]["ctx"])

from backend.app.services.event_dedup import dedup_key
print(len(dedup_key(None, None, "purchase", "order-1001")))
print("('ab','c') == ('a','bc')?", dedup_key(None, None, "ab", "c") == dedup_key(None, None, "a", "bc"))

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
['event_id', 'event_name', 'event_type', 'experiment_key', 'feature_flag_key', 'metadata', 'timestamp', 'user_id', 'value']
empty event_id: None
256-character event_id: string_too_long
101 events: too_long {'field_type': 'List', 'max_length': 100, 'actual_length': 101}
64
('ab','c') == ('a','bc')? False
duplicate: True  foreign key: False
```

The misnamed key is dropped while `event_id` is a field; `""` means no id; the key is always 64
characters; and the two tuples a separator would confuse get different keys. Now run the tests that pin the
key byte for byte and the input rules, and the SDK's tests for the 429 contract (all use fakes and need no
server):

```bash
python -m pytest backend/tests/unit/services/test_event_dedup.py backend/tests/unit/schemas/test_tracking_event_id.py -p no:cov -q
python -m pytest sdk/python/tests/test_transport.py sdk/python/tests/test_client.py -p no:cov -q
```

The first reports `61 passed` at the pin and the second `217 passed`; read `test_client.py` for the cases
that prove "retry once on 429, raise on anything else". Two more need PostgreSQL, so read them instead of
running them. In `backend/tests/integration/api/test_tracking_assign_first_conflict.py`, find the hook on
`GlobalHoldoutService.is_user_in_holdout`: it is the only place in the file where a second session stores
the user's assignment, and it is placed so the request's own insert is the loser. In
`backend/tests/integration/api/test_tracking_duplicate_events.py`, read
`test_r11_two_connections_claiming_one_key_at_once`: it holds the first claim's transaction open until it
has *seen* the second claim waiting on the key, the same "force the interleaving" idea.

Now the reasoning exercise. A client times out after sending one conversion with no `event_id`. The
operator sees one assignment row and assumes replaying the conversion cannot duplicate data. What does the
assignment invariant prove, and what would have changed the answer?

**Answer.** Only that the experiment/user pair has one durable assignment. The event may already have
committed, and a replay without an id adds another event row: the conversion rate is unchanged (the user
counts once), but a count metric counts it again and a revenue metric adds its value again. Had the client
sent an `event_id`, and the same one on the replay, the second request would answer `duplicate: true` and
write nothing. Events already stored without an id keep their repeats; the id protects only what is sent
with it.

### Key takeaways

- A unique index is an invariant; whether callers get a clean answer when it fires is a separate, testable
  property. Controlvar's assignment answers a lost race with the stored row.
- Decide "duplicate?" from the SQLSTATE, not the message; re-raise everything else.
- Events have an idempotency key, `event_id`, and it works only when the sender supplies it, stably, on
  every attempt. Unknown request fields are still silently ignored.
- The key's namespace, its lifetime, its transaction and its replay answer are each a decision; read them
  from the code, not from the field's name.
- HTTP 200 on a batch is not success for every item; keep the index mapping and read `duplicate_count`.
- The SDK retries a 429 once because nothing was stored; it never retries a timeout, and with an `event_id`
  your code can.

**Review artifact:** a request failure matrix showing the commit boundary, the observable client result and
the recovery action for reads, assignments, events and partial batches.

---


## Hour 5 — Schedulers, replicas and stale state: five loops, one lock

**Outcome:** explain what a scheduler lock coordinates, how failed ticks become visible, why background
work must stay off the event loop that serves requests, why a cache is a separate consistency domain, and
why a server-side change is not instant for an SDK holding a ruleset.

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

#### Keep blocking work off the event loop, and hold the lock until it stops

The schedulers live in the API process, and that process answers HTTP on **one event loop thread**. Any
synchronous call made on that thread, a SQLAlchemy query or an HTTP send, stops every request until it
returns. The issue that taught this was #1690, "The API freezes for seconds every 15 minutes while the
metrics scheduler runs on the event loop"; its fix shipped in v0.41.0, and the metrics scheduler's own
docstring now says why its body moved: "on the loop it would stop the API from answering anything else
until it finished." At the pin every tick body runs in a worker thread: the bandit scheduler hands its
body to `asyncio.to_thread`, and the other four use one helper:

```python
# 📁 backend/app/core/scheduler_tick.py
async def run_in_worker(work: Callable[[], Any], *, label: str) -> Any:
    """
    Run *work* in a worker thread; the event loop awaits it (#1690).

    For a tick whose body is synchronous (SQLAlchemy queries, HTTP sends): run
    on the event loop, it stops the API answering anything else until it
    finishes. *work* must be a plain callable. A coroutine function cannot be
    handed over as it is -- ``asyncio.to_thread`` on one only creates the
    coroutine and never runs it, and the tick is recorded as a success that
    did nothing -- so wrap it to create and run the coroutine inside the
    thread, on a private event loop: ``lambda: asyncio.run(body())``.
...
    worker = asyncio.get_running_loop().run_in_executor(
        None, functools.partial(contextvars.copy_context().run, work)
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        await _wait_for_worker(worker, label)
        raise
```

Two traps are named in that docstring, and both are worth remembering outside Python. The first is a
**success that did nothing**: hand a coroutine *function* to a thread and the thread creates the coroutine,
returns it unrun, and the tick is recorded as a success. A green run record is not evidence the work ran.
The second is in the part elided above: the helper "does not return before the thread does", even when the
tick is cancelled or the event loop is torn down, because `run_locked_tick` releases the advisory lock when
the tick returns, and "returning early would let another replica's tick run beside a thread that is still
running." That is the fencing problem from the start of this Hour again: a lock protects only the work that
finishes before it is released.

The run record itself was still written on the loop's thread until v0.43.2 moved it into a worker as
well. The comment beside the call says what that had cost:

```python
# 📁 backend/app/core/scheduler_tick.py
# In a worker thread, like the tick bodies (#1690): the write checks a
# connection out of the pool, and when every connection is held by a
# request waiting for this event loop, a checkout made on the loop's
# own thread waits the whole pool timeout and stops the API answering
# for that long -- and then the run record is lost as well.
```

Read it slowly, because it is a deadlock with a timeout. Every pooled connection is held by a request
that is waiting for the event loop; the loop's thread asks the pool for one more and waits for a connection
that only the loop could release. Nothing moves until the pool gives up. The comment arrived with the fix
for public issue #1756, whose title is what users saw: "Under sustained overload, the API stops answering
for about 30 seconds at a time". The pool timeout in `db/session.py` (Hour 6) is `pool_timeout=30`. A
setting chosen to protect one request, "wait up to 30 seconds for a connection", can become the length of
a pause for all of them, and only under sustained overload.

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
  routes, ``experiment_cache.py`` that drops an experiment's cached detail
  for the writers outside them, and the admin cache routes) and by
  ``deps.py``, which defines it;
* no route under ``/api/v1/feature-flags`` depends on it.

No exception: ``admin.py``'s ``CACHE_NAMESPACES`` has no flag entry.

The source scan walks the files that exist, so a core build (no ``modules/``)
checks the core; it reads no git state, so it holds in a non-repository copy.
```

The docstring is a design record: what was deleted, why, and the three things the test pins (no source file
names a flag cache key, the Redis-handing dependency is referenced only by files that still cache, no flag
route depends on it). The note "no exception" is the important sentence in a gate: an allowlist with a
convenient entry is how a gate stops gating. Notice the newest entry in the list of files that may still
cache, `experiment_cache.py`, which "drops an experiment's cached detail for the writers outside" the
experiment routes: the experiment cache was kept, so the problem that killed the flag cache (writers
elsewhere that forgot to invalidate) had to be solved for it in one shared place. And the last paragraph
is a gate that knows where it runs: it reads files rather than git, so it still works in the core build's
copy of the tree, which has no `.git`.

#### Server state versus SDK-held state

With local evaluation, a server-side SDK downloads the flag ruleset and answers in-process. That is a
cache with a refresh interval (default 30 seconds, minimum 5) and a documented behaviour when the refresh
fails: with a ruleset loaded, a 5xx, timeout or network error means **keep serving the old ruleset** and
retry with backoff of up to 5 minutes; a 401 or 403 (key revoked, scope removed, owner no longer allowed),
a 404 or a ruleset format the SDK does not know means **discard it and ask the server**; a 429 waits at
least `Retry-After`. Hour 10 turns these into "how long does turning a flag off take to arrive".

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

Now watch the "success that did nothing" from the `run_in_worker` docstring happen, and its fix:

```bash
python - <<'EOF'
import asyncio
from backend.app.core.scheduler_tick import run_in_worker

async def body():
    print("the body ran")

async def main():
    wrong = await run_in_worker(body, label="demo")
    print("handed the coroutine function itself:", type(wrong).__name__)
    wrong.close()
    await run_in_worker(lambda: asyncio.run(body()), label="demo")

asyncio.run(main())
EOF
```

Expected result:

```text
handed the coroutine function itself: coroutine
the body ran
```

The first call returned without error and without running anything: the thread called `body()`, got a
coroutine object back and handed it over unrun (`wrong.close()` only stops Python warning that it was
never awaited). Inside a scheduler that return value would have been recorded as a successful tick. The
second call creates and runs the coroutine inside the thread, on a private event loop, as the docstring
prescribes.

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

**Answer.** It depends on what the SDK sent. If each event carried an `event_id`, the six already stored
come back in `duplicate_count` and nothing is written for them. If not, the accepted six are now stored
twice: inspect per-item results and do not promise exactly-once ingestion. Converting-user counts may be
unchanged either way, but count and revenue means are not. The scheduler skips ticks while it cannot reach
the database, and loss of the lock session does not prove an external effect happened exactly once. Check
the age of `scheduler_last_success_timestamp`, the run history, durable state and external effect
identifiers before deciding on recovery.

### Key takeaways

- N replicas means N copies of every loop; a non-blocking advisory lock lets one run each tick.
- A lock is not a fencing token. Autocommit and a dedicated connection keep it from vanishing mid-tick.
- Blocking work in an async process runs in a worker thread, and a tick waits for its thread before it
  lets the lock go; a pool checkout on the loop's own thread can stall every request for the pool timeout.
- Alert on the age of the last *successful* tick, not on process liveness, and remember a recorded
  success is only as true as the work it recorded.
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
    refused rather than stored and compared as text.  That includes a value
    at the edge of the date range whose offset moves it past 0001-01-01 or
    9999-12-31 once converted to UTC (``datetime.astimezone`` raises
    ``OverflowError`` for it, #1684).
    """
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str):
        moment = datetime.fromisoformat(value.strip())
    else:
        raise ValueError("event timestamp must be a datetime or an ISO-8601 string")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return moment.astimezone(timezone.utc).isoformat()
    except OverflowError:
        raise ValueError(
            "event timestamp is outside the range that can be stored once it "
            "is converted to UTC (0001-01-01 to 9999-12-31)"
        ) from None
```

The function accepts a `datetime` or an ISO-8601 string, treats a value with no offset as UTC, and raises
`ValueError` for anything else, "so an unreadable timestamp is refused rather than stored and compared as
text". A column type decorator applies it at bind time, so every writer (the API, the event service, seed
scripts) gets it without remembering to call it. When the same rule must hold for every writer, put it
where all writers pass.

The `try` at the end is a later fix, and a good example of an edge nobody thinks of: `9999-12-31T23:00:00-05:00`
is a valid timestamp that parses, but converting it to UTC moves it into the year 10000, and Python raises
`OverflowError`, which is not a `ValueError`. Before v0.41.0 the tracking API answered that with a 500;
now the function turns it into the `ValueError` its contract promises, and the request is refused with 422.
A function that promises one exception type has to translate every other one its body can raise.

#### What a good migration says about the previous release

Read the opening of a deliberately small core migration:

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
sentence is the expand rule applied. (The heads it names were the heads when it was written; a migration's
docstring is a record of its own moment.)

The migration that created Hour 4's key table adds a fifth habit: it says what it **declined** to do, and
what the deploy window costs:

```python
# 📁 backend/app/db/migrations/versions/85dd4796bb8b_events_an_event_id_is_stored_once.py
**No foreign key to ``events``.** ``CREATE TABLE ... REFERENCES events`` takes
a lock on ``events`` and waits behind every open transaction writing to it;
without one, this revision takes no lock on ``events`` at all and completes
while the previous release keeps writing events
(``test_event_dedup_keys_migration.py`` pins that). Nothing here changes
``events``.

Pure expand: the previous release never reads or writes the table, so it
starts against this schema and stores events as before (an event it stores
during the deploy window is not deduplicated). Downgrade drops the table and
the keys in it; an upgrade afterwards starts from no keys.
```

A foreign key is the textbook choice, and here it would have been the wrong one: the lock it takes on the
busiest table in the system would queue behind every in-flight event write, and (because PostgreSQL queues
later lock requests behind a waiting one) every new write would queue behind it. The migration gives up referential integrity on purpose, says so, and has a test that
holds it to "takes no lock on `events`". And it is honest about the mixed-version window: while the
previous release is still serving, *its* event writes are not deduplicated. An expand migration is safe
for the old code; that does not mean the old code gets the new behaviour.

#### Two heads is the normal state

A full checkout has two Alembic heads, the core chain and the `modules` branch, so the commands say
`heads`, never `head`. Check:

```bash
python -m alembic -c backend/app/db/alembic.ini heads
```

At the pin this prints two lines:

```text
7b6b4b4fe246 (head)
modules_0004_rbac_activation (modules) (head)
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
      (``modules_0004_rbac_activation``, or an earlier modules revision --
      ``modules_0003_lower_is_better``, ``modules_0002_warehouse_analysis``,
      ``modules_0001_rbac`` -- on a database an older full release
      migrated), which a core tree has no file for.  ``deploy.yml`` runs the
      migration task with
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
*worse*. Measure queue time, transaction duration and database saturation before raising any limit. And
read `pool_timeout` as a worst case, not a safety margin: Hour 5 showed what a 30-second wait costs when
the code waiting is the event loop every request shares.

### Try It Yourself

Check the canonical-time rule on the offsets from the migration notes:

```bash
python - <<'EOF'
from backend.app.models.event import normalize_event_timestamp as n
for v in ["2026-10-01T01:00:00+02:00", "2026-10-01T01:00:00Z", "2026-10-01T01:00:00",
          "2026-10-01T01:00:00.250000-05:00"]:
    print(v, "->", n(v))
for bad in ("yesterday", "9999-12-31T23:00:00-05:00"):
    try:
        n(bad)
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
refused: event timestamp is outside the range that can be stored once it is converted to UTC (0001-01-01 to 9999-12-31)
7b6b4b4fe246 (head)
modules_0004_rbac_activation (modules) (head)
```

(Your Python version may word the first refusal slightly differently.) The first line is the example in
the migration notes: `+02:00` becomes the same instant in UTC. The last refusal is the edge from above:
a value that parses, and is refused only because its UTC instant cannot be stored.

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
stored. A timeout may follow a commit. An `event_id` makes a retry after a timeout safe for an event, but
the SDK still leaves that retry to your code.

**Why does the event key include the experiment and the flag?** So one event fanned out to each of a
user's experiments and flags counts once in each. The cost is that the "same" event resolved to a
different experiment is a different event.

**Why does a scheduler tick wait for its worker thread before returning?** Returning releases the
advisory lock, and another replica's tick could then run beside a thread that is still working.

**Why is "two heads" not an error?** The core chain and the module branch are separate on purpose so that
deleting `modules/` leaves a consistent core chain.

### Quiz

1. Is replaying an entire partially successful event batch necessarily safe?
2. Does a successful migration on an empty database demonstrate mixed-version compatibility?
3. Can revoking an API key recall a ruleset an SDK already downloaded?
4. What does a scheduler tick report when another replica holds the lock?
5. Name two reasons an exclusive "check, then insert" needs the database to arbitrate.
6. A client resends a purchase with the same `event_id` and a corrected value. What is stored, and what
   does the answer's `value` say?

**Answers.** (1) Only if every item carries its `event_id`; otherwise the successful items can be stored
twice. (2) No; test old and new writers and historical rows against the expanded schema. (3) No; the
process keeps serving the ruleset it holds until its next refresh answers 401/403, so account for refresh
and process-held state. (4) `skipped`, recorded as a run but not as a failure. (5) Two processes can both
pass the check before either inserts, and the unique index is the only component that sees both writes.
(6) The first value stays stored and nothing is written; the answer has `duplicate: true` and echoes the
corrected value from the request, which was not stored.

### Exercises

1. **Failure matrix.** Build a table for `assign`, `track` and `batch` with columns: before commit, after
   commit/before response, concurrent duplicate, partial result. Fill in what the client sees and the safe
   recovery, with and without an `event_id` for the two event routes.
2. **Ledger design.** Sketch the producer-side ledger that would make event ingestion retry-safe from an
   SDK with no `event_id` option: what is stored, when it is marked sent, and how a replay is detected.
3. **Cache inventory.** List five caches or held states in a system you run (Controlvar or otherwise) with
   authority, key, maximum age, error behaviour and invalidation path.
4. **Budget.** For 4 replicas, 3 workers each and the pool settings above, compute the peak connections and
   say what you would measure before changing `pool_size`.

### Solutions

1. For `assign`: before commit, nothing stored and a retry is safe; after commit/before response, a retry
   returns the stored row (sticky); concurrent duplicate, the loser answers with the winner's row; no
   partial result. For `track` without an `event_id`: before commit, retry is safe; after commit/before
   response, a retry stores another row (ambiguous); concurrent duplicate, both store. With an `event_id`:
   after commit/before response, a retry with the same id answers `duplicate: true` and stores nothing;
   a concurrent duplicate waits for the first claim and becomes a duplicate if it commits. For `batch`, a
   partial result needs the item index mapping and per-item retry, or a whole-batch replay when every item
   has its `event_id`.
2. Store (local id, payload hash, destination, state) before sending; mark sent only on a confirmed 2xx
   per item; on retry, resend only unconfirmed ids; accept that a crash between server commit and local
   mark can still duplicate once. Where the destination takes a key, send the local id as that key (for
   Controlvar, as the `event_id` through the HTTP API): the server then closes the window the ledger
   cannot.
3. A good inventory includes the SDK per-user assignment cache (authority: the server, key: user+key,
   age 300 s default, errors not cached, invalidation: `clear_cache()`), the results cache (5 min/24 h,
   invalidate endpoint), a local-evaluation ruleset (30 s refresh, discard on 401/403/404) and the
   server's event keys (authority: `event_dedup_keys`, no maximum age, never purged).
4. 4 × 3 × 40 = 480 connections at peak, plus lock and admin connections. Measure queue wait (pool timeout
   is 30 s), transaction duration, and database saturation (active connections against the server's limit)
   before raising anything.

---

[← Part 1](./part-1-experiment-design.md) · [Contents](./README.md) · [Part 3 →](./part-3-security-and-privacy.md)

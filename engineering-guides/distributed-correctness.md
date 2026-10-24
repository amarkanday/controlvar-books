# Distributed correctness: retries, replicas and stale state

[← Field guides](README.md) · Related: [Database evolution](database-evolution.md)

**Outcome:** explain what happens when a request or background tick fails halfway through, without
assuming exactly-once processing. Read this before changing assignments, tracking, schedulers or caches.

## Separate invariants from delivery guarantees

The [assignment model][assignment] has a unique index on experiment and user. That database invariant
prevents two durable assignment rows for the same pair. It does not prove that two concurrent first
requests both receive a successful response. Inspect [assignment service][service] and the calling
route for transaction ownership and error handling. Test concurrent entry points against PostgreSQL;
an in-memory mock cannot prove database conflict behavior.

Existing assignments are sticky on the ordinary assignment path. Deterministic bucketing helps new
allocations agree, but stored state, targeting, holdouts and bandit routing also matter. A hash function
is not a substitute for a persisted assignment or a concurrency policy.

## A successful write can have a failed response

Imagine an event is committed, then the connection closes before the client receives confirmation.
The client sees a timeout and cannot know whether the write happened. Retrying may store another row.
At this snapshot, the public [tracking schema][tracking-schema] does not offer a client-supplied
idempotency key with a deduplication guarantee. Do not infer event deduplication from assignment uniqueness.

For binary conversion, [event matching][matching] counts each assigned converting user once. That
protects that particular numerator from repeated conversion events. It does not deduplicate storage,
arbitrary sums, billing side effects, or another consumer that counts event rows.

If your integration needs retry-safe event ingestion, design a durable producer ledger or a genuine
server-side idempotency contract. Define the key's namespace, payload-conflict behavior, retention,
transaction boundary and replay response. Those are recommended design requirements; they are not
implemented by adding an arbitrary metadata field to the current API.

## Classify retries by operation

| Failure | Recommended client reasoning |
|---|---|
| Validation or permission refusal | Fix the request or credential; repeated retries do not repair it |
| Rate limit | Honor server retry guidance where provided and bound retry attempts |
| Read timeout | A bounded retry may be acceptable; record exhausted attempts |
| Assignment timeout | Check the stored assignment using the supported lookup path; test concurrency |
| Event write timeout | Outcome is ambiguous; retry policy must account for possible duplicates |
| Partial batch result | Inspect item errors; replaying the whole batch can repeat successful items |

Use exponential backoff with jitter where retries are appropriate, plus a total deadline. A timeout
budget belongs to the caller's operation, not to each attempt independently. A bounded queue or
explicit failure is preferable to unlimited memory growth during an outage.

The [tracking implementation][tracking] reports batch successes and failures per item. HTTP success
alone does not establish that every event was accepted. Retain the input-to-result index mapping and
decide which failures can be retried without losing track of already accepted events.

## Scheduler coordination is not permanent leadership

Every API worker can start the background schedulers. The [scheduler lock][lock] uses a PostgreSQL
session advisory lock for each scheduler name. Acquisition is nonblocking: a process that cannot
acquire skips that tick. A dedicated autocommit connection holds the lock, and release runs on exit.
The async wrapper moves blocking acquisition and release off the event loop.

When the database is unavailable, lock acquisition yields false and the tick is skipped. This keeps
coordination failure from crashing the API, but scheduled work may be delayed. Monitor last successful
work, not merely whether a loop is alive. A session lock coordinates holders while the session exists;
it is not a fencing token that prevents all side effects after a connection is lost. External effects,
such as notifications, still need their own duplicate-effect reasoning.

## Treat caches as separate consistency domains

Do not assume every feature flag is served from a shared cache. The [flag-cache guard][cache-test]
protects the removal of the old flag cache because multiple writers could leave it stale. That is
separate from rule-evaluation memoization, experiment caches and SDK-held rulesets.

In [local evaluation][local], an SDK answers for the ruleset version it holds. A rollback at the API
is not instantaneous invalidation of every remote process. For each cached item, write the authority,
key, maximum age, refresh/error behavior and invalidation path. Exercise both warm and cold caches.

## Exercise and worked answer

An SDK sends a batch of ten events. Six commit, four are rejected, and the client retries all ten.
Separately, one scheduler loses database access during a tick. What can you safely conclude?

**Answer:** accepted event storage may contain duplicates after replay; inspect per-item results and
do not promise exactly-once ingestion. Binary converting-user counts may still remain unchanged, but
that says nothing about raw event totals. The scheduler may skip future ticks, and loss of the lock
session does not prove an external effect happened once. Inspect durable state, tick outcomes and
external effect identifiers before deciding recovery.

**Review artifact:** a failure matrix with before-commit, after-commit/before-response, concurrent
request, partial-batch and stale-cache cases. Show the observable result and recovery action for each.

[assignment]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/assignment.py
[service]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/assignment_service.py
[tracking-schema]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/schemas/tracking.py
[tracking]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/v1/endpoints/tracking.py
[matching]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/event_matching.py
[lock]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/scheduler_lock.py
[cache-test]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/tests/smoke/test_no_flag_cache.py
[local]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk/local-evaluation.md

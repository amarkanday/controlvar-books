# Part II — Reliable Systems

### Hours 4–6

[← Part 1](./part-1-experiment-design.md) · [Contents](./README.md) · [Part 3 →](./part-3-security-and-privacy.md)

A request may commit before its response is delivered, a scheduler may lose its database connection,
and an SDK may hold an older ruleset than the server. Hours 4 and 5 examine these cases and their
recovery implications. Hour 6 covers schema changes and query performance during overlapping
application versions.

> **Verified against** getexperimently/experimently at `07a430df5b54b0e5dd71ddca95071ced05e9e36c`
> (source review, 2026-10-23). Recommended procedures and illustrative examples are identified
> in the lessons; live-stack exercises and paid provider calls were not run for this edition.

---

## Hour 4 — Assignments, transactions and retries

**Outcome:** distinguish assignment uniqueness, event storage and response delivery. Explain what
a retry can repeat when a write commits before the client receives its response.

### What & why

#### Separate invariants from delivery guarantees

The [assignment model][h4-assignment] has a unique index on experiment and user. That database invariant
prevents two durable assignment rows for the same pair. It does not prove that two concurrent first
requests both receive a successful response. Inspect [assignment service][h4-service] and the calling
route for transaction ownership and error handling. Test concurrent entry points against PostgreSQL;
an in-memory mock cannot prove database conflict behavior.

Existing assignments are sticky on the ordinary assignment path. Deterministic bucketing helps new
allocations agree, but stored state, targeting, holdouts and bandit routing also matter. A hash function
is not a substitute for a persisted assignment or a concurrency policy.

### How Experimently does it

#### A successful write can have a failed response

Imagine an event is committed, then the connection closes before the client receives confirmation.
The client sees a timeout and cannot know whether the write happened. Retrying may store another row.
At this snapshot, the public [tracking schema][h4-tracking-schema] does not offer a client-supplied
idempotency key with a deduplication guarantee. Do not infer event deduplication from assignment uniqueness.

For binary conversion, [event matching][h4-matching] counts each assigned converting user once. That
protects that particular numerator from repeated conversion events. It does not deduplicate storage,
arbitrary sums, billing side effects, or another consumer that counts event rows.

If your integration needs retry-safe event ingestion, design a durable producer ledger or a genuine
server-side idempotency contract. Define the key's namespace, payload-conflict behavior, retention,
transaction boundary and replay response. Those are recommended design requirements; they are not
implemented by adding an arbitrary metadata field to the current API.

#### Classify retries by operation

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

The [tracking implementation][h4-tracking] reports batch successes and failures per item. HTTP success
alone does not establish that every event was accepted. Retain the input-to-result index mapping and
decide which failures can be retried without losing track of already accepted events.

### Try It Yourself

A client times out after sending one conversion. The operator sees one assignment row and assumes
replaying the conversion cannot duplicate data. What does the assignment invariant actually prove?

**Answer:** only that the experiment/user pair has one durable assignment. The event may already
have committed, and replay can add another event. Binary converting-user counts and raw event
storage have different duplicate behavior. Inspect the event write path and recovery policy.

### Key takeaways

Assignment uniqueness is a storage invariant, not an exactly-once response guarantee. A retry
policy must distinguish known refusal from an ambiguous committed write.

**Review artifact:** a request failure matrix showing the commit boundary, observable client result
and recovery action for reads, assignments, events and partial batches.

---

## Hour 5 — Schedulers, replicas and stale state

**Outcome:** explain what a scheduler lock coordinates, how failed ticks become visible and why
server state can differ from an SDK-held ruleset after a change.

### What & why

#### Scheduler coordination is not permanent leadership

Every API worker can start the background schedulers. The [scheduler lock][h5-lock] uses a PostgreSQL
session advisory lock for each scheduler name. Acquisition is nonblocking: a process that cannot
acquire skips that tick. A dedicated autocommit connection holds the lock, and release runs on exit.
The async wrapper moves blocking acquisition and release off the event loop.

When the database is unavailable, lock acquisition yields false and the tick is skipped. This keeps
coordination failure from crashing the API, but scheduled work may be delayed. Monitor last successful
work, not merely whether a loop is alive. A session lock coordinates holders while the session exists;
it is not a fencing token that prevents all side effects after a connection is lost. External effects,
such as notifications, still need their own duplicate-effect reasoning.

### How Experimently does it

#### Treat caches as separate consistency domains

Do not assume every feature flag is served from a shared cache. The [flag-cache guard][h5-cache-test]
protects the removal of the old flag cache because multiple writers could leave it stale. That is
separate from rule-evaluation memoization, experiment caches and SDK-held rulesets.

In [local evaluation][h5-local], an SDK answers for the ruleset version it holds. A rollback at the API
is not instantaneous invalidation of every remote process. For each cached item, write the authority,
key, maximum age, refresh/error behavior and invalidation path. Exercise both warm and cold caches.

### Try It Yourself

An SDK sends a batch of ten events. Six commit, four are rejected, and the client retries all ten.
Separately, one scheduler loses database access during a tick. What can you safely conclude?

**Answer:** accepted event storage may contain duplicates after replay; inspect per-item results and
do not promise exactly-once ingestion. Binary converting-user counts may still remain unchanged, but
that says nothing about raw event totals. The scheduler may skip future ticks, and loss of the lock
session does not prove an external effect happened once. Inspect durable state, tick outcomes and
external effect identifiers before deciding recovery.

### Key takeaways

A scheduler lock coordinates a live database session; it does not fence every external effect.
A cache or downloaded ruleset has its own authority, age and recovery policy.

**Review artifact:** a failure matrix with before-commit, after-commit/before-response, concurrent
request, partial-batch and stale-cache cases. Show the observable result and recovery action for each.

---

## Hour 6 — Database evolution and performance

**Outcome:** propose a schema change that survives a rolling deployment, and investigate a slow
analysis query without changing its meaning. Use a disposable database and representative synthetic
data. Production migration commands remain in the canonical runbook, not in this lesson.

### What & why

#### Migration success is only one compatibility check

A new schema may need to support old API workers, new API workers, background schedulers and old
clients simultaneously. A successful upgrade against an empty database proves little about that
overlap. Start with a compatibility matrix:

| Combination | Required question |
|---|---|
| Previous application + expanded schema | Does the previous code still read and write correctly? |
| New application + expanded schema | Can it handle rows not yet backfilled? |
| Old and new writers together | Do both preserve the same invariant? |
| Rollback to previous application | Can it serve without undoing the schema? |
| Core and full profiles | Does each discover and apply only its intended migration work? |

Read the [migration guide][h6-migrations] before interpreting the revision graph. A full checkout has
multiple heads by design; use `upgrade heads` in the approved migration path. Never run `alembic merge`
merely to collapse the expected core/module branches. A profile switch is not equivalent to a normal
version upgrade, and stamping revisions does not execute missing migrations.

### How Experimently does it

#### Worked design: adding an assignment attribute

Suppose a proposed feature needs a normalized acquisition channel alongside assignments. This is
an **illustrative change**, not a claim that this column already exists.

First expand: add a nullable column with a migration that old code can ignore. Define what null
means and make new readers tolerate it. Add new-writer behavior without requiring historical rows
to be present. If channel cannot be reliably reconstructed, retain null rather than inventing data.

Next backfill, if there is a trustworthy source. Use bounded batches and a stable cursor rather
than loading every assignment into memory. Commit progress with an unambiguous resume position;
make retries safe. Measure lock time, write load and replica lag where applicable. Validate both
completion and meaning: zero remaining nulls is not evidence that the assigned channels are correct.

Then move reads to the new representation after verifying mixed-writer behavior. Only in a later
release, after the supported rollback window closes, consider a stronger constraint or removal of
old fields. A direct rename can break workers still serving the previous release. Dual writes need
a declared authority and reconciliation; they are not automatically consistent.

#### Deployment ownership and recovery

On the AWS deployment described in the [runbook][h6-migrations], API tasks set `RUN_MIGRATIONS=false`;
the migration task runs before traffic moves. Compose has a different startup path. Verify
the actual deployment's settings rather than copying one environment's assumptions into another.

Plan application rollback separately from database recovery. Prefer a backward-compatible schema
that lets the old application continue serving. Some downgrades drop data, and some transformations
cannot reconstruct previous values. Restoring a backup creates operational work and may lose writes
after the restore point; rehearse the established [disaster recovery procedure][h6-recovery] and name the
data-loss window. Do not edit the version table to make an older image accept an unknown revision.

#### Preserve semantics while optimizing

The [event model][h6-events] stores event time as a normalized UTC string; assignment timestamps and
event receipt timestamps have their own representations. Do not assume all time columns share a
database type or convert one ad hoc in a query. Use the existing normalization and migration rules.

For conversion results, inspect [event matching][h6-matching]. Its implementation deliberately avoids
one large join when reading assigned converting users: poor planner statistics on freshly seeded
data produced slow nested loops. It fetches converting users and verifies assignments in bounded
chunks. Replacing it with a visually simpler join may reintroduce a measured failure.

For a slow query, capture SQL and bound-value shapes using synthetic identifiers; inspect the plan
against realistic row counts and selectivity. Compare estimated and actual rows, scans, loop counts,
sort spills and buffers. `EXPLAIN ANALYZE` executes the statement, so use read-only SELECTs in the
exercise environment. On a write statement it would perform the write.

Index design follows predicates and access patterns. An index that helps one experiment/user lookup
may not help a broad time-range aggregation. Additional indexes also cost writes and storage. Check
the model and migrations before proposing a duplicate. Partitioning, pre-aggregation and archival
are possible future designs, not features implied by a large events table.

#### Connection budgets

Budget connections across API replicas × workers × pool capacity, background work, migrations and
administrative access. The [scheduler lock][h6-lock] holds a dedicated connection for a tick. A pool
size that looks small per process can exhaust the database across a deployment. Increasing API
replicas can therefore worsen a database bottleneck. Measure queue time, transaction duration and
database saturation before increasing limits.

### Try It Yourself

A patch renames a column, adds a non-null constraint and backfills the full events table in the same
transaction. It passes against an empty database. What should change?

**Answer:** separate expansion, compatible application changes, resumable bounded backfill and later
contraction. Verify actual historical data and the overlap with old writers. Measure the migration's
lock and runtime behavior. Add rollback and profile compatibility evidence. An empty-database pass
cannot reveal blocked writes, incompatible old workers or the cost of updating millions of rows.

### Key takeaways

Schema changes must tolerate historical rows and overlapping application versions. Preserve query
semantics, inspect realistic plans and budget connections across every process.

**Review artifact:** compatibility matrix, measured migration/lock behavior, restartable backfill
design, before/after query plans with equal results, and an explicit recovery path. Use the existing
[migration compatibility tests][h6-tests] to understand the expected branch behavior.

---

## Workshop

### Q&A

**Why can a single durable assignment coexist with a failed first request?** Uniqueness governs
stored rows; the response can fail before the caller receives confirmation.

**Does a scheduler advisory lock make external effects exactly once?** No. The session can be lost,
and a notification or other external effect has its own commit/acknowledgement boundary.

### Quiz

1. Is replaying an entire partially successful event batch necessarily safe?
2. Does a successful migration on an empty database demonstrate mixed-version compatibility?
3. Can revoking a key recall an already downloaded ruleset?

**Answers:** (1) No; successful items can be duplicated. (2) No; test old/new writers and historical
rows against the expanded schema. (3) No; account for refresh and process-held state.

### Exercise with worked solution

Propose evidence for a new event aggregation query that is faster on one developer database.

**Solution:** compare equal metric results on representative synthetic data, include repeated
conversions and unassigned users, inspect plans at realistic cardinalities, check fresh planner
statistics, and measure database/pool load. Keep the existing conversion definition and assess
mixed-version behavior if storage changes.

---

[← Part 1](./part-1-experiment-design.md) · [Contents](./README.md) · [Part 3 →](./part-3-security-and-privacy.md)

[h4-assignment]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/assignment.py
[h4-service]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/assignment_service.py
[h4-tracking-schema]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/schemas/tracking.py
[h4-tracking]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/v1/endpoints/tracking.py
[h4-matching]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/event_matching.py
[h4-lock]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/scheduler_lock.py
[h4-cache-test]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/tests/smoke/test_no_flag_cache.py
[h4-local]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk/local-evaluation.md
[h5-assignment]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/assignment.py
[h5-service]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/assignment_service.py
[h5-tracking-schema]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/schemas/tracking.py
[h5-tracking]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/v1/endpoints/tracking.py
[h5-matching]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/event_matching.py
[h5-lock]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/scheduler_lock.py
[h5-cache-test]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/tests/smoke/test_no_flag_cache.py
[h5-local]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk/local-evaluation.md
[h6-migrations]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/self-hosting/migrations.md
[h6-recovery]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/deployment/disaster-recovery.md
[h6-events]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/event.py
[h6-matching]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/event_matching.py
[h6-lock]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/scheduler_lock.py
[h6-tests]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/tests/integration/database/test_profile_migrations.py

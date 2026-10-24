# Database evolution and performance

[← Field guides](README.md) · Related: [Distributed correctness](distributed-correctness.md)

**Outcome:** propose a schema change that survives a rolling deployment, and investigate a slow
analysis query without changing its meaning. Use a disposable database and representative synthetic
data. Production migration commands remain in the canonical runbook, not in this lesson.

## Migration success is only one compatibility check

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

Read the [migration guide][migrations] before interpreting the revision graph. A full checkout has
multiple heads by design; use `upgrade heads` in the approved migration path. Never run `alembic merge`
merely to collapse the expected core/module branches. A profile switch is not equivalent to a normal
version upgrade, and stamping revisions does not execute missing migrations.

## Worked design: adding an assignment attribute

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

## Deployment ownership and recovery

On the AWS deployment described in the [runbook][migrations], API tasks set `RUN_MIGRATIONS=false`;
the migration task runs before traffic moves. Compose and Helm have different startup paths. Verify
the actual deployment's settings rather than copying one environment's assumptions into another.

Plan application rollback separately from database recovery. Prefer a backward-compatible schema
that lets the old application continue serving. Some downgrades drop data, and some transformations
cannot reconstruct previous values. Restoring a backup creates operational work and may lose writes
after the restore point; rehearse the established [disaster recovery procedure][recovery] and name the
data-loss window. Do not edit the version table to make an older image accept an unknown revision.

## Preserve semantics while optimizing

The [event model][events] stores event time as a normalized UTC string; assignment timestamps and
event receipt timestamps have their own representations. Do not assume all time columns share a
database type or convert one ad hoc in a query. Use the existing normalization and migration rules.

For conversion results, inspect [event matching][matching]. Its implementation deliberately avoids
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

## Connection budgets

Budget connections across API replicas × workers × pool capacity, background work, migrations and
administrative access. The [scheduler lock][lock] holds a dedicated connection for a tick. A pool
size that looks small per process can exhaust the database across a deployment. Increasing API
replicas can therefore worsen a database bottleneck. Measure queue time, transaction duration and
database saturation before increasing limits.

## Exercise and worked answer

A patch renames a column, adds a non-null constraint and backfills the full events table in the same
transaction. It passes against an empty database. What should change?

**Answer:** separate expansion, compatible application changes, resumable bounded backfill and later
contraction. Verify actual historical data and the overlap with old writers. Measure the migration's
lock and runtime behavior. Add rollback and profile compatibility evidence. An empty-database pass
cannot reveal blocked writes, incompatible old workers or the cost of updating millions of rows.

**Review artifact:** compatibility matrix, measured migration/lock behavior, restartable backfill
design, before/after query plans with equal results, and an explicit recovery path. Use the existing
[migration compatibility tests][tests] to understand the expected branch behavior.

[migrations]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/self-hosting/migrations.md
[recovery]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/deployment/disaster-recovery.md
[events]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/event.py
[matching]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/services/event_matching.py
[lock]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/scheduler_lock.py
[tests]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/tests/integration/database/test_profile_migrations.py

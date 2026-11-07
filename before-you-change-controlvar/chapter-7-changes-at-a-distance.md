# Chapter 7 — Changes that act at a distance

**Outcome:** you can recognise the five kinds of change whose effect lands somewhere no local test
looks, and make each one the way the repository expects.

## Why some changes are different

Most changes are local: you edit a function, its tests fail or pass, done. A few kinds of change act
somewhere else. A migration runs against a database that an older release is still serving from. A
change to the statistics moves numbers that clients have already saved. A change to the SDK contract
reaches code already installed in other people's apps. `CLAUDE.md` calls these "mechanisms that act at a
distance", and the rule for them is that their properties are pinned literally, so a silent change fails
loudly: "Do not relax those tests to make a change pass; they are the contract."

Here are the five you are most likely to meet.

## 1. The database schema: two heads, expand then contract

A full checkout has **two migration heads**: the core chain in `backend/app/db/migrations/versions/` and
the `modules` branch in `modules/backend/app/db/migrations/versions/`. They are separate on purpose, so
that deleting `modules/` leaves a consistent core chain. So:

- it is always `heads`, plural: `upgrade heads`, `stamp heads`. The singular fails with "Multiple head
  revisions are present";
- a new revision names the head it extends. `CONTRIBUTING.md` gives the commands for a core change:

  ```bash
  # 📁 CONTRIBUTING.md
  CORE_HEAD=$(python -m alembic -c backend/app/db/alembic.ini heads | grep -v '(modules)' | cut -d' ' -f1)
  python -m alembic -c backend/app/db/alembic.ini revision --autogenerate --head "$CORE_HEAD" -m "describe the change"
  ```

  For a module's change, the head is `modules@head`;
- **never merge the two heads.** The merge file would land in the core versions directory and point at
  a modules revision, and every core checkout, which has no modules revisions, would then fail to load
  its migrations at all.

The second rule is **expand, then contract**. A migration in release N must not drop or rename anything
release N-1's code uses. During a rolling upgrade, release N-1 keeps serving against release N's schema
after N's migration has run. So you add the new column, move the code over, and drop the old column in a
later release.

The flag value types are a worked example. Before release 0.40.0 every flag was boolean. The migration
that made room for string, number and JSON flags (`607c51a59bf5`) added three columns and kept the old
one:

```python
# 📁 backend/app/models/feature_flag.py
    default_value = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    ...
    value_type = Column(
        String(16),
        nullable=False,
        default="boolean",
        server_default=text("'boolean'"),
    )
    on_value = Column(JSONB(none_as_null=True), nullable=True)
    off_value = Column(JSONB(none_as_null=True), nullable=True)
```

`default_value` is deprecated and still there. Both `default_value` and `value_type` carry a
`server_default`, and the comments beside them call it "load-bearing": an image of the previous release
inserts rows without naming the column, and the database fills it in. The migration notes in
`docs/self-hosting/migrations.md` say, for each recent revision, what the previous release does against
the new schema, in the same words each time: "The previous release runs against this schema unchanged."

The pull request template asks you to confirm it: "No migration, or every migration here is expand-only".
And if an older image does meet a newer database (a rollback, say), it refuses to start, naming the
revision: "this database was migrated by a newer Controlvar release". Two tests pin this machinery:
`backend/tests/integration/database/test_autogenerate_is_empty.py` (a freshly bootstrapped database
produces an empty migration) and `backend/tests/integration/database/test_newer_database_rollback.py`.

> **Watch Out!** The migration guide says it in one line: "Do not edit `alembic_version` to make the
> older image start." `CLAUDE.md` adds that no message, and nothing else, may tell an operator to. If
> your change seems to need that advice, change the migration instead.

## 2. The numbers: `ENGINE_VERSION`

The statistics engine stamps its version on every saved results snapshot, on the bandit's state and on
each Bayesian and CUPED answer, and the version is part of the results cache key. It only means
something if it moves whenever the numbers move. The rule is written beside the constant:

```python
# 📁 backend/app/core/stats_engine.py
#: The rule: an engine-changing PR bumps ``ENGINE_VERSION`` to the next minor
#: and appends it to ``RELEASED_ENGINE_VERSIONS`` ("frozen on main", in
#: ``backend/tests/unit/services/test_sufficient_stats_fingerprint.py``) in the
#: same change.  A frozen version's entries are never edited, and no key is
#: ever added under one.  A PR is engine-changing when, for the same database
#: rows and request, it adds, removes or changes any number, decision (winner,
#: recommendation, Bayesian decision, ``can_stop``) or allocation weight that a
#: path stamping ``engine_version`` returns, caches or persists: results and
#: its snapshots, Bayesian, CUPED, sequential, and the bandit's state, history
#: and status.  A revert of an engine-changing PR bumps again.
ENGINE_VERSION: str = "1.8.0"
```

`backend/tests/unit/services/test_engine_fingerprint.py` runs a fixed dataset through every analysis
path, hashes the outputs, and compares the hash with the one pinned for the current version. When it
fails, the engine's output changed, and the docstring of that test lists every file to update. Both
OpenAPI snapshots carry the version too.

## 3. The wire: the SDK contract

The SDKs do not import the backend. They share the wire contract and the hash, and both are pinned by
files under `tests/sdk-contract/`:

- `golden-vectors.json` fixes the consistent hash from Chapter 3. Each SDK's own hash function is run
  against it. `CONTRIBUTING.md`: "If you change assignment hashing, you change all sixteen or none."
- `ruleset-vectors.json` is the corpus for the SDKs that evaluate flags locally (JavaScript and Python,
  beta). It is generated from the server, never written by hand. A change to the rules engine, the
  targeting adapter or flag evaluation that moves any answer fails
  `backend/tests/unit/services/test_sdk_ruleset_vectors.py` until you regenerate the file with
  `python -m backend.scripts.generate_ruleset_vectors`, and then the SDKs' own tests must agree with it.
- `published-sdks.json` pins the SDK releases already on npm and PyPI. They keep reading whatever the
  server sends, and a ruleset one of them cannot read is dropped whole: that SDK keeps answering from its
  last ruleset, so a later kill switch never reaches it, and nothing on the server says so.
  `tests/sdk-contract/test_published_ruleset.py` downloads each pinned release and feeds it the server's
  current ruleset (it needs network access and Node).

## 4. The public API's shape: the OpenAPI snapshots

`docs/api/openapi-v1.stable.json` (the core profile) and `docs/api/openapi-v1.full.json` (the full
profile) are committed. `backend/tests/smoke/test_openapi_snapshot.py` regenerates both and compares. A
stable route whose parameters, request body or responses change fails the smoke suite. A new or
still-changing route is marked beta on its decorator, which turns the failure into a warning:

```python
# 📁 backend/tests/smoke/test_openapi_snapshot.py
@router.get("/things", openapi_extra={"x-stability": "beta"})
```

When the change is intended, `make openapi` regenerates the snapshots (and the dashboard's URL-guard
fixture), and you commit them with the change. Every release also writes its version into them, so they
change in release pull requests too (Chapter 8).

## 5. The profile boundary

From Chapter 1: nothing in `backend/` may import `modules`, and `make core-build` proves the core stands
alone. A change that adds a core import of a module, or a test in a core file that needs a module
without the `@pytest.mark.modules` marker, passes in your full checkout and fails in the `core-build`
check. Before you trust a change that touches both, run `scripts/core_build.sh`.

## Try it

Use the test shell from Chapter 2 for each step.

1. Print the two heads:

   ```bash
   python -m alembic -c backend/app/db/alembic.ini heads
   ```

   Two lines: at the commit this guide was checked against, `7b6b4b4fe246 (head)` and
   `modules_0004_rbac_activation (modules) (head)`. The core head is the one without `(modules)`.

2. Break the hash and watch the contract catch it. In `backend/app/core/consistent_hash.py`, change
   `"<I"` (little-endian) to `">I"` (big-endian) in `hash_user`, then run:

   ```bash
   python -m pytest backend/tests/unit/core/test_consistent_hash.py -p no:cov -q
   ```

   `3 failed, 4 passed`: `test_every_golden_vector_matches`,
   `test_the_first_four_bytes_are_read_little_endian` and
   `test_the_whole_digest_modulo_100_is_a_different_answer`. Every number the broken function returns is
   still in range; only the vectors notice. Put it back with
   `git checkout -- backend/app/core/consistent_hash.py`.

3. Check that the ruleset corpus matches the server:

   ```bash
   python -m backend.scripts.generate_ruleset_vectors --check
   ```

   It ends with the file's path and `is current`. (A notice that the Cognito settings are unset may also
   appear; ignore it.)

4. Run the engine fingerprint and the OpenAPI snapshot comparison. Neither needs a database:

   ```bash
   python -m pytest backend/tests/unit/services/test_engine_fingerprint.py backend/tests/smoke/test_openapi_snapshot.py -p no:cov -q
   ```

## Go deeper

- [Learn FastAPI, Hour 18](../learn-fastapi-in-24-hours/part-4-sqlalchemy.md): Alembic, two heads,
  autogenerate and the bootstrap.
- [Build a Platform, Day 13](../build-a-platform-in-28-days/week-2-api-and-data.md): migrations and the
  bootstrap.
- [Production Engineering, Hour 6](../production-engineering/part-2-reliable-systems.md): database
  evolution while the previous release is still running.
- [Experimentation Statistics, Hour 12](../experimentation-statistics/part-4-many-comparisons.md):
  reproducibility, seeds and engine versions.
- [SDK Engineering, Hour 12](../sdk-engineering/part-4-proving-agreement.md): how to change a contract
  sixteen clients depend on.
- [Learn TypeScript, React & Next.js, Hour 23](../learn-react-nextjs-in-24-hours/part-6-production.md):
  the dashboard's OpenAPI URL guard.

---

_Previous: [Chapter 6 — A first change, start to finish](./chapter-6-a-first-change.md). Next:
[Chapter 8 — After the merge](./chapter-8-after-the-merge.md)._

# Chapter 4 — Tests and gates

**Outcome:** you know what a pull request must pass, how to run the part you touched, and which gates
read things other than code.

## What "passing" means here

`CONTRIBUTING.md` gives the short version: before you open a pull request, run `make lint && make test`.
`make test` runs the backend's unit, integration and smoke suites, the modules' own suite, and the
dashboard's tests, type check and production build. `make lint` is exactly what the `lint` CI job runs:
ruff, the import contracts that keep the core apart from the modules, the licence check, the
requirements-lock check, the old-name check, and eslint and `tsc` for the dashboard.

You will rarely run all of it while you work. Run the part you touched:

| You changed | Run |
|---|---|
| A backend service, route or model | the matching file under `backend/tests/unit/` or `backend/tests/integration/`, then `make test-unit` |
| Wiring: a route, an import, a schema | `backend/tests/smoke/` (the OpenAPI snapshots live here) |
| A module | `make test-modules` |
| The dashboard | `make test-frontend` |
| Hashing or an SDK | `make test-sdk`, and that SDK's own tests |
| Any Python | `make format`, then `make lint` |

## The harness protects you

The test harness has safety nets you will not notice until one fires. The repository's root
`conftest.py` loads four plugins into every pytest session started from the root:

```python
# 📁 conftest.py
pytest_plugins = [
    "backend.tests.no_real_aws",
    "backend.tests.benchmark_gate",
    "backend.tests.no_outbound_network",
    "backend.tests.shard",
]
```

- `no_real_aws` means **no test reaches real AWS**. Before collection, and again before every test, it
  replaces the AWS credentials with dummies, points the config files at files that do not exist (so
  `~/.aws` is never read), and puts a failing `aws` first on `PATH` that prints `NO-REAL-AWS`. It
  exists because a test whose guard was broken on purpose once sent a real AWS command; it failed only
  because that machine had no valid credentials.
- `benchmark_gate` skips a test marked `benchmark` (an absolute wall-clock check) unless
  `RUN_BENCHMARKS=1`.
- `no_outbound_network` fails a Lambda or showcase test that opens a connection to anything but
  loopback.
- `shard` does nothing unless CI asks it to run one slice of a session.

The markers are declared in `pyproject.toml`. These are the ones you will use:

```toml
# 📁 pyproject.toml
markers = [
    "unit: unit tests (fast, no external dependencies)",
    "integration: integration tests (require the database)",
    ...
    "regression: guards a specific past bug",
    "modules: needs the modules package; skipped in a core build",
    "smoke: wiring checks that must hold for any build of this tree",
    ...
]
```

## The required checks

A pull request into `main` runs many workflows. The ones branch protection requires are listed in one
place, `scripts/configure-repo.sh`, which is the script that sets the protection:

```bash
# 📁 scripts/configure-repo.sh
REQUIRED_CHECKS=(
    "Release Gate Summary"
    "Security Scan Summary"
    "Unit Tests"
    "Smoke Tests"
    "Frontend Tests"
    ...
    "regression-guard"
    ...
    "core-build"
    "full-build"
    ...
    "Leak Guard"
    "DCO"
)
```

There are twenty at the commit this guide was checked against. Two of them are about your pull request
rather than your code: `regression-guard` (a pull request labelled `bug` must change a test file) and
`DCO` (every commit is signed off). Chapter 6 meets both. Two more checks run on every pull request and
are not in that list: `conventional-title` (Chapter 8) and `contributor-guard` (Chapter 6).

## Gates that read more than code

Some gates do not test behaviour at all. They hold a document, a snapshot or a generated file to the
code, so that the two cannot drift apart. They are the ones newcomers trip on, because the failure
appears in a file you did not think you touched.

- **The claims registry.** Every user-facing sentence in a registered document under `docs/` has an
  entry in `docs/_claims/<document>.toml` that names what verifies it: a test, a journey, an example, or
  a stated reason it is not a product claim. `python3 scripts/doc_claims.py check` refuses a page and its
  registry that disagree. Edit a registered sentence and you edit its entry in the same pull request.
  The check runs inside Unit Tests, through `backend/tests/unit/docs/test_doc_claims.py`.
- **Runnable examples.** The shell blocks in the documentation are run in CI as written. A page you add
  with a shell block has to be tagged and enrolled: `python3 scripts/doc_examples.py --check`.
- **The OpenAPI snapshots.** `docs/api/openapi-v1.stable.json` (core) and `docs/api/openapi-v1.full.json`
  (full) are compared with what the app generates by `backend/tests/smoke/test_openapi_snapshot.py`.
  Changing the shape of a stable route fails it. Chapter 7.
- **Generated contracts.** `tests/sdk-contract/ruleset-vectors.json` must match what the server
  generates, and the statistics engine's output is fingerprinted per `ENGINE_VERSION`. Chapter 7.
- **The core build.** `scripts/core_build.sh` copies the tree into a directory with no `.git`, deletes
  `modules/`, then imports, bootstraps, tests and builds what is left. The `core-build` check runs it.

> **Watch Out!** `CLAUDE.md` puts it bluntly: "a green local suite is the weakest evidence available."
> Your virtualenv has every package, your tree has both profiles, your database is already migrated. CI,
> the container and production have none of those. A test that shells out to `git` passes everywhere
> except in the core build's copy, which has no `.git`.

## The rule for gates

When you add or change a gate, **break the thing it guards and watch it fail.** `CLAUDE.md` calls a
check that passes for the wrong reason the most frequent defect in the repository, and lists a cycle's
worth: a pattern matching a directory instead of the page it guarded, a CI input that selected nothing,
a test that stubbed out the thing it tested. A gate you have only seen pass is not a gate. The Try it
steps below, and in Chapters 6 and 7, all end with a planted defect for that reason.

## Try it

1. Print the required checks:

   ```bash
   sed -n '/^REQUIRED_CHECKS=(/,/^)/p' scripts/configure-repo.sh
   ```

2. Run one test file that needs no database, in the test shell from Chapter 2:

   ```bash
   python -m pytest backend/tests/unit/core/test_consistent_hash.py -p no:cov -q
   ```

   It ends with `7 passed`.

3. Run the claims check. It needs only Python 3.11:

   ```bash
   python3 scripts/doc_claims.py check
   echo "exit $?"
   ```

   It prints the documents in scope and the units registered, then `exit 0`.

4. Now plant a defect. In `docs/api/endpoints.md`, change "oldest first" to "newest first" in the
   sentence about the feature flags list, and run the check again. It exits 1 and names both halves of
   the problem: the page has an unregistered sentence, and the registry lists a sentence that is "no
   longer on docs/api/endpoints.md". Put the page back with `git checkout -- docs/api/endpoints.md`.

5. List what the core build would do, without doing it:

   ```bash
   scripts/core_build.sh --list-steps
   ```

   Thirteen steps, from `copy` and `strip` to `vectors` and `licences`. Nothing is touched.

## Go deeper

- [Build a Platform, Day 22](../build-a-platform-in-28-days/week-4-production.md): the testing strategy,
  the harness and the hot-path lesson.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): CI, the required
  checks and tamper-tested gates.
- [Learn FastAPI, Hours 22 and 23](../learn-fastapi-in-24-hours/part-6-testing-and-operating.md):
  `TestClient`, dependency overrides, and database-backed tests.
- [Learn Python, Hour 23](../learn-python-in-24-hours/part-6-in-practice.md): pytest fixtures,
  parametrize, monkeypatch and mocks.
- [Platform Operations, Hour 10](../platform-operations/part-4-release-engineering.md): the PR QA gate,
  the release gate, and what "required" means.

---

_Previous: [Chapter 3 — Where decisions live](./chapter-3-where-decisions-live.md). Next: [Chapter 5 — The
process](./chapter-5-the-process.md)._

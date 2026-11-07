# Chapter 1 — The map

**Outcome:** you can say which directory a change belongs in, which of the two profiles it touches, and
where its test goes.

## One repository, many parts

Controlvar is one repository. It holds the API, the dashboard, sixteen client SDKs, the optional modules,
the infrastructure code, two demo apps and the documentation. Each part has its own tools, but they
share one tree, one history and one set of checks.

The shortest accurate map is the one in `CONTRIBUTING.md`, under "Layer map":

```text
# 📁 CONTRIBUTING.md
backend/app/        FastAPI application: api/, core/, services/, models/, schemas/
backend/lambda/     Lambda handlers (event processor, flag evaluation), deployed by no stack
backend/scripts/    One-off and seed scripts
backend/tests/      unit/ integration/ smoke/ e2e/ contract/ performance/ realistic/
frontend/           Next.js dashboard (TypeScript)
sdk/<lang>/         16 client SDKs — independent packages, MIT licensed
modules/            The optional modules (workspaces, rbac, sso, hipaa, compliance,
                    warehouse, integrations, counters, etl, split_url) — Apache-2.0,
                    contributions welcome like anywhere else
infrastructure/     AWS CDK stacks
demo/               ShopLab and StreamPulse demo applications
docs/               All documentation, including the modules
tests/sdk-contract/ Cross-SDK golden-vector tests
```

A few things live outside that map, and you will meet them in your first week:

| Where | What it is |
|---|---|
| `scripts/` | Repository tooling: `core_build.sh`, `check_dco.py`, `doc_claims.py`, `contributor_guard.py`, `configure-repo.sh` and the other checks CI runs |
| `.github/workflows/` | CI: every check a pull request runs, and the release and deploy workflows |
| `tests/ground_truth/`, `tests/usecases/`, `tests/scale/`, `tests/mcp-contract/` | Suites that run against a running API: seeded traffic replayed and checked against an independent reference, use cases driven through the real SDKs, load runs on the shipped Compose stack, and the MCP server's contract |
| `deploy/compose/`, `charts/experimently/`, `infrastructure/cdk/` | The three ways to self-host: Compose on one host, Helm, and AWS CDK |
| `.claude/agents/` | The agent roles the maintainers work with, including the five that draft and review plans (Chapter 5) |
| `CLAUDE.md` | The maintainers' working notes: commands, conventions, the process, and the traps that cost a review round |
| `modules-manifest.txt` | The list of every path that belongs to the optional modules |

## Inside `backend/app`: a request goes down the layers

The backend is a FastAPI application, imported as the package `backend.app`. Most changes touch three or
four of its directories, in this order:

- `backend/app/api/` holds the routes. `backend/app/api/api.py` includes one router per resource, and
  each router lives in `backend/app/api/v1/endpoints/` (`feature_flags.py`, `experiments.py`,
  `tracking.py` and so on). A route reads the request, checks who is asking, and calls a service.
- `backend/app/schemas/` holds the Pydantic models that say what a request and a response look like.
- `backend/app/services/` holds the business logic: assignment, flag evaluation, results, safety
  monitoring. Chapter 3 is about the functions here.
- `backend/app/models/` holds the SQLAlchemy tables.

Beside them, `backend/app/core/` holds what everything shares (settings, permissions, the rules engine,
the hash, the schedulers), `backend/app/db/` the bootstrap and the migrations, `backend/app/crud/` the
generic list-and-get helpers, and `backend/app/middleware/` the request middleware.

Follow one request to see it. `GET /api/v1/feature-flags/evaluate/{flag_key}` is the route an SDK calls to
ask whether a flag is on for a user. The route is `evaluate_feature_flag` in
`backend/app/api/v1/endpoints/feature_flags.py`. It hands the work to `_evaluate_flag_by_key` in the same
file, which calls `FeatureFlagService.evaluate_flag_detailed` in
`backend/app/services/feature_flag_service.py`. The route knows HTTP; the service knows flags.

> **Watch Out!** Everything runs from the repository root. The entry point is `backend.app.main:app`,
> not `app.main:app` from inside `backend/`, and imports are fully qualified
> (`from backend.app.models.metrics.metric import RawMetric`, never `from app.models...`). Two import
> paths to one class make SQLAlchemy treat it as two mapped classes, and the failure appears far from
> the import.

## Two profiles, one tree

Controlvar ships in two profiles. The **core** profile is `backend/` and `frontend/`. The **full**
profile adds the optional modules under `modules/`, which mirror the same layout one level down
(`modules/backend/app`, `modules/frontend/src`). `modules-manifest.txt` is the authority on what
belongs there.

The rule that shapes every change: **nothing in `backend/` may import `modules`.** The core must build,
boot and pass its tests with `modules/` deleted. Three things enforce it, and Chapter 4 shows each:

- `lint-imports`, with the contracts in `pyproject.toml` (`[tool.importlinter]`);
- `backend/tests/smoke/test_core_boundary.py`, which scans the core for any import of, or string naming,
  a module path listed in `modules-manifest.txt`;
- `scripts/core_build.sh` (`make core-build`), which copies the tree, deletes `modules/` and runs the
  tests on what is left.

The modules plug into the core through registration hooks in `backend/app/core/` and the loader in
`backend/app/modules_loader.py`. The arrow points from `modules/` into `backend/`, never back. A running
API reports which profile it is in at `GET /api/v1/modules`.

> **By the Way.** Controlvar was called Experimently before 0.35.0. A few identifiers keep the old name
> on purpose, because something matches them against a stored copy: the Compose project name
> `experimently-selfhost` sets the volume names, so a new name would start an empty database.
> `scripts/legacy_name_keep.toml` lists every place the old name is kept and why, and
> `scripts/check_legacy_name.py`, part of `make lint`, fails on the old name anywhere else.

## Where your test goes

Tests are chosen by directory, not by marker. `CONTRIBUTING.md` says where each kind goes:

- `backend/tests/unit/` for a test of one piece of logic;
- `backend/tests/integration/` for a test that needs PostgreSQL;
- `backend/tests/smoke/` for a test that the app is wired correctly (routes, imports, snapshots);
- `modules/backend/tests/` for a module's own tests;
- `frontend/src/tests/` for the dashboard, and each SDK's own test directory for that SDK.

A test in a core file that needs a module installed is marked `@pytest.mark.modules`, and a core build
skips it. Markers such as `regression` exist and matter (Chapter 6), but the directory is what CI
selects on.

## Try it

1. List the backend's directories and name the layer each one is:

   ```bash
   ls backend/app
   ```

   You should see `api`, `core`, `crud`, `db`, `middleware`, `models`, `schemas`, `services` and `utils`,
   among others.

2. Find the flag evaluation routes:

   ```bash
   git grep -n '"/evaluate/{flag_key}"' -- backend/app/api
   ```

   Two lines, both in `backend/app/api/v1/endpoints/feature_flags.py`: a `GET` and a `POST`. Open the
   file at the first one and follow the call to `evaluate_flag_detailed` in the service.

3. Open `modules-manifest.txt` and read its header. It says who reads the file (the boundary test and
   the core build) and what each of them fails on.

4. Check the boundary rule holds on your clone:

   ```bash
   git grep -n -E '^\s*(from|import) modules' -- backend/app
   ```

   No output: nothing in the core application imports the modules.

## Go deeper

- [Build a Platform, Day 1](../build-a-platform-in-28-days/week-1-core-loop.md): the architecture on one
  page, and the bug that taught the team to count conversions in one place.
- [Build a Platform, Day 8](../build-a-platform-in-28-days/week-2-api-and-data.md): the FastAPI
  application, its routers, the middleware stack and the lifespan.
- [Learn Python, Hour 9](../learn-python-in-24-hours/part-3-modern-python.md): modules, packages and the
  `backend.app` import rule.
- [Learn TypeScript, React & Next.js, Hour 10](../learn-react-nextjs-in-24-hours/part-3-nextjs.md): the
  dashboard's pages router, where files are routes.

---

_Next: [Chapter 2 — Run it](./chapter-2-run-it.md)._

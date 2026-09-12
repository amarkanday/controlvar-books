# Week 4 — Production Engineering

### Days 22–28 · Testing, the Dashboard, CI/CD, Containers, AWS, Observability

[← Week 3](./week-3-hard-parts.md) · [Contents](./README.md)

Three weeks of _how it works_. This week is _how you know it works, and how it gets to
production_: the test pyramid and the lesson the team learned about mocked seams, the dashboard's
architecture, the CI gates a pull request must pass, the container images and the compose
stack, the AWS deployment in CDK, and the logs, metrics and alerts that tell you it's healthy.
Day 28 hands you the whole map and asks you to draw it from memory.

> **The one sentence to carry through Week 4.** _A change is real when it has passed the PR
> gate (unit + smoke + frontend + SDK contract + live contract + docker smoke), the release
> gate (backend + frontend + Bandit + gitleaks), and the security scan; when it ships as an
> immutable image through a blue/green deploy after a one-shot migration task; and when
> `/health/ready`, `/metrics` and the scheduler run history say it's alive — and every one of
> those gates exists because something once slipped through without it._

| Day | Topic                    | The one thing it's really about                                      |
| --- | ------------------------ | -------------------------------------------------------------------- |
| 22  | Testing strategy         | The pyramid, the harness, and why mocked seams hid the hot-path bugs |
| 23  | The dashboard            | A thin Next.js client: one fetch wrapper, one auth context, thin pages |
| 24  | CI/CD                    | Three workflows, eight required jobs, and what each one catches      |
| 25  | Containers & compose     | Two images, one entrypoint, profiles for demos and tools             |
| 26  | AWS                      | Fourteen CDK stacks, Fargate blue/green, Lambda + Kinesis, DynamoDB  |
| 27  | Observability            | Request IDs, JSON logs, Prometheus, health probes, alerting          |
| 28  | **Capstone:** the map    | Draw the whole system; explain every arrow                           |

---

## Day 22 — Testing strategy: the pyramid, the harness, and the hot-path lesson

### What & why

Experimently has ~112,000 lines of backend tests — more than the application — organised in
five layers, each answering a different question:

| Layer            | Where                          | Question it answers                                   | Runs in                               |
| ---------------- | ------------------------------ | ----------------------------------------------------- | ------------------------------------- |
| Unit             | `backend/tests/unit/`          | Does this function/service do what its contract says?  | PR gate (`-x`, stops on first failure) |
| Lambda unit      | `backend/lambda/*/tests/`      | Do the three Lambdas and `shared/` behave?             | PR gate (301 tests, one pytest per dir) |
| Integration      | `backend/tests/integration/`   | Do endpoints + services + DB agree end-to-end?         | Integration Tests workflow, nightly   |
| Smoke            | `backend/tests/smoke/`         | Is every router wired and every route reachable?       | PR gate                               |
| Contract         | `tests/sdk-contract/`          | Do all SDKs hash identically and speak the real API?   | PR gate (golden vectors + live)       |
| Frontend         | `frontend/src/tests/`          | Do pages, services, hooks, contexts behave?            | PR gate (Jest + build)                |
| E2E / visual / a11y | `frontend/e2e/`             | Does the whole thing work in a browser?                | Nightly (Playwright)                  |
| Performance      | `backend/tests/performance/`   | Is the rules engine still 125k ops/s?                  | Weekly (Locust + benchmarks)          |

#### The lesson: mocked seams hide broken hot paths

In September 2026, with 5,000+ tests green, the public tracking API had been broken for
weeks: `/tracking/track` built an `EventCreate` with field names the service didn't accept,
the experiments endpoints returned shapes the schema rejected, the safety service read a table
nobody wrote to, and the bandit scheduler called a method that didn't exist. Every one of
those paths had tests — tests that **mocked the service** the endpoint called, or **mocked the
DynamoDB client** the scheduler called, and asserted on the mock. The tests proved the code
called _something_; they didn't prove the something existed or agreed on shapes.

The team's response, visible across `fix/ci-green`, PR #64 and PR #65, is now policy in the
testing audit (`docs/testing/testing-audit-2026-09.md`):

1. **Drive hot paths through the public API against a real database** — `TestClient` +
   the `client` fixture + the per-process test database. The `TestAssignEligibility` class in
   `backend/tests/integration/api/test_tracking_api.py` is the model: it creates an experiment,
   calls `/tracking/assign`, and reads the `assignments` table.
2. **Wiring smoke tests** — `backend/tests/smoke/test_wiring.py` enumerates every route in
   the app and asserts it resolves, so an endpoint whose import fails can't ship.
3. **Live contract tests for SDKs** — run the real client against the real API in CI.
4. **A docker smoke job** — build both images, start the compose stack, log in, mint a key,
   assign a user. If the entrypoint, the bootstrap, the seed or nginx is wrong, this fails.

Mocks still have a place: pure logic (the rules engine, statistics), external services (AWS
via `moto`, Slack, SendGrid), and failure injection. The rule is _mock what you don't own,
drive what you do._

### How Experimently does it

The harness is the root `conftest.py`. Its four decisions shape every DB-backed test:

```python
# 📁 backend/tests/conftest.py
# * APP_ENV=test / TESTING=true select TestSettings (ENVIRONMENT == "test")
#   and the test_experimentation schema.  ENVIRONMENT itself is deliberately
#   NOT exported: tests that construct ProdSettings()/DevSettings() directly
#   must keep their class default, and the settings singleton already resolves
#   APP_ENV=test to the canonical "test".
# * Auth runs exactly as the Community Edition ships: AUTH_PROVIDER=local and
#   the dev-admin bypass OFF (fail-closed).  ...
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("TESTING", "true")
# A developer shell may export ENVIRONMENT=development (see .env.example);
# ENVIRONMENT wins over APP_ENV, so drop it for the test process.
os.environ.pop("ENVIRONMENT", None)

from backend.app.main import app
...
settings.AUTH_PROVIDER = "local"
settings.DEV_AUTH_BYPASS = False

# Each pytest process gets its own database named
# "experimentation_test_<pid>".  This prevents concurrent test sessions from
# sharing-and trampling-each other's database
_PID = os.getpid()
_TEST_DB_NAME = f"experimentation_test_{_PID}"
```

1. **Environment before import.** The schema name is fixed at import (Week 1, Day 2), so the
   variables are set at the top of the module, before `from backend.app.main import app`.
2. **A database per process**, created with `DROP DATABASE`/`CREATE DATABASE` in autocommit
   mode at session start, so parallel or concurrent runs can't collide.
3. **No truncation between tests.** `TRUNCATE ... CASCADE` deadlocked against async fixture
   overlap; instead every test uses UUIDs and unique suffixes, and the whole database is
   dropped at the next session start.
4. **`expire_on_commit=False`** on the test session, so reading `user.is_superuser` after a
   commit doesn't open a fresh NullPool connection.

The `client` fixture overrides `get_db` with the test session and the auth dependencies with
a superuser, then hands you a `TestClient`:

```python
# 📁 backend/tests/conftest.py
@pytest.fixture
def client(db_session, monkeypatch):
    """Create a test client for the FastAPI application."""
    # Override the get_db dependency
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    ...
    async def override_get_current_user():
        return user

    def override_get_current_active_user():
        return user
    ...
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user
    ...
```

`app.dependency_overrides` is FastAPI's seam for exactly this: the endpoint's `Depends(deps.get_db)`
resolves to the test session without touching the endpoint. (That's also why endpoints must
use `Depends(get_db)` rather than importing `SessionLocal` — Week 2, Day 8.)

A unit test in the house style — pure logic, `MagicMock` for the session, stubbed
collaborators, the gate order pinned:

```python
# 📁 backend/tests/unit/services/test_assignment_service_eligibility.py
"""
Unit tests for the eligibility gate in ``AssignmentService.assign_user``.

New users are checked, in order, against the global holdout, the experiment's
mutual exclusion group and its targeting rules.  These tests use a MagicMock
session and stub the holdout / mutual-exclusion services so each branch can be
driven directly; the DB-backed behaviour lives in
``backend/tests/integration/api/test_tracking_api.py::TestAssignEligibility``.
"""
```

Note the docstring's last sentence: the unit test _names its integration twin_. That pairing
— fast, mocked branch coverage next to one real end-to-end path — is the pattern the audit
asks for.

The everyday commands (always in the venv, always with the test env):

```bash
source venv/bin/activate
export APP_ENV=test TESTING=true
python -m pytest backend/tests/unit/ -p no:cov -q                 # ~3,700 tests, a few minutes
python -m pytest backend/tests/integration/ -p no:cov -q          # needs Postgres on localhost:5432
python -m pytest backend/tests/smoke/test_wiring.py -q            # every route resolves
python -m pytest backend/tests/unit/services/test_cuped_service.py -k theta -v   # one thing
cd frontend && npm test                                           # Jest, 550+ tests
```

> **Watch Out!** Three harness facts that cost people hours: (1) `pytest.ini`'s `addopts`
> carries coverage flags that silently produce no report in some venvs — pass `--cov` explicitly
> when you want numbers, and `-p no:cov` when you want speed. (2) Running unit and integration
> suites in _one_ pytest session surfaces ~10 order-dependent failures (list pagination, the
> CloudWatch client) that pass when the suites run separately; CI runs them separately.
> (3) `backend/tests/smoke/test_production.py` targets a live server via `SMOKE_TEST_API_URL`
> and fails offline — that's expected.

> **Did You Know?** `moto` is pinned at 4.2.2, which means `mock_dynamodb`, `mock_cognitoidp`
> and friends — not the `mock_aws` umbrella from 4.3+. The Cognito integration tests
> (`backend/tests/integration/auth/`) run their own package under `AUTH_PROVIDER=cognito` via
> a package-level conftest and are the only tests that exercise the Cognito branch of
> `get_current_user`.

### Build It

Write the pair. Pick `FeatureFlagService._evaluate_percentage_rollout` and:

1. A **unit** test that stubs nothing (it's pure): 0% → `False`, 100% → `True`, and a user
   whose bucket you compute offline lands on the right side of 50%.
2. An **integration** test with the `client` fixture: create a flag via `POST
   /feature-flags/`, activate it, set 50%, then `GET /feature-flags/evaluate/{key}` with an
   API key you mint via `POST /api-keys` — assert `enabled` matches your offline bucket and
   `reason == "rollout"`.

Run both, then run the wiring smoke test and read `_iter_http_routes` to see how it copes
with FastAPI 0.141's lazy routers.

### Key takeaways

- Eight layers, from unit to performance; the PR gate runs unit, Lambda, smoke, frontend,
  SDK contract, live contract and docker smoke.
- The 2026 lesson: mocked seams hid broken hot paths; drive what you own through the public
  API against a real database, mock what you don't own.
- The harness: env before import, a database per process, no truncation,
  `expire_on_commit=False`, `dependency_overrides` for `get_db` and auth.
- Unit tests name their integration twin.
- Know the three harness gotchas (coverage flags, cross-suite order, the live smoke test).

### Q&A

**Q. How do I test a scheduler tick?**
Call the tick method directly with a real test session (it opens `SessionLocal()` itself, so
point `DATABASE_URI` at the test DB — the harness does), or drive `run_locked_tick` with a
fake tick as Day 17's Build It did. The lifespan doesn't start schedulers under the test
profile, so `TestClient` never runs one behind your back.

**Q. How do I test a feature-flag permission dependency?**
`@pytest.mark.asyncio`, `async def`, and `await deps.can_update_feature_flag(...)` with a
mock user and flag — those deps are async. Experiment deps are sync: call them directly.

**Q. Where are the statistics tested for correctness, not just coverage?**
`backend/tests/unit/services/test_*_service.py` for the formulas (against hand-computed
values and `scipy`), `backend/tests/realistic/` for generated data with known effects, and the
`validator` agent in the autonomous QA framework (`/autotest`) for end-to-end checks that
p-values, CUPED variance reduction and bandit allocations are mathematically right.

**Q. What's the coverage?**
~83% backend lines (unit + integration + wiring smoke, fresh 2026-09-11); schemas 97%, models
95%, services/core ~81%. The weakest large files are `endpoints/experiments.py`,
`safety_service.py`, `assignment_service.py` and `event_service.py` — which are exactly the
hot paths the audit is pushing coverage toward.

---

## Day 23 — The dashboard: Next.js architecture and the API client

### What & why

The dashboard is a **Next.js 14 pages-router app in TypeScript with Tailwind**, deliberately
thin: it has no state library, no GraphQL, no server-side rendering of data. Every page reads
the API through one `fetch` wrapper, holds local state with hooks, and renders. The reason is
the same as the SDKs': the API is the product, and the dashboard is its first client.

Five pieces carry the architecture:

1. **`services/api.ts`** — one `apiFetch<T>()`: base URL, bearer token, JSON, an `ApiError`
   with the FastAPI `detail`, and a 401 → `/login?next=` redirect.
2. **`contexts/AuthContext.tsx`** — token in `localStorage`, user re-derived from `/auth/me`
   on every mount, cross-tab sync via the `storage` event, `hasRole()`.
3. **`_app.tsx` + `utils/routes.ts` + `RequireAuth`** — route classification (`bare`, `open`,
   `protected`) decides whether a page gets the shell and the auth gate.
4. **`services/*.ts`** — one object per resource (`ExperimentsService`, `FeatureFlagsService`,
   `ResultsService`, `AdminService`, `WorkspacesService`) with typed methods that mirror the
   API's routes one-to-one.
5. **`hooks/useApi.ts`** — `data/loading/error/refetch` around any fetcher, with a mounted
   guard.

### How Experimently does it

The fetch wrapper's contract is written at the top of the file:

```typescript
// 📁 frontend/src/services/api.ts
/**
 * Single HTTP client for the dashboard.
 *
 * - `apiBase()` prefixes every request with `NEXT_PUBLIC_API_URL` (default `""`,
 *   i.e. same-origin so the nginx `/api/` proxy works in the container image).
 * - `apiFetch()` adds `Authorization: Bearer <token>` from
 *   `localStorage["experimently.token"]`, sends/receives JSON and throws
 *   `ApiError { status, detail, code? }` for non-2xx responses.
 * - A 401 clears the stored token and redirects to `/login?next=<path>`
 *   (skipped on `/login` itself and for requests sent without auth).
 *
 * `apiFetch` always calls the global `fetch` at call time so tests can keep
 * mocking `global.fetch`.
 */
```

and its error type turns FastAPI's three `detail` shapes (string, object, validation list)
into one message:

```typescript
// 📁 frontend/src/services/api.ts
export function messageForDetail(status: number, detail: unknown): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation errors: [{loc, msg, type}, …]
    const parts = detail
      .map((item) => {
        if (item && typeof item === 'object') {
          const rec = item as Record<string, unknown>;
          const loc = Array.isArray(rec.loc) ? rec.loc.filter((p) => p !== 'body').join('.') : '';
          const msg = typeof rec.msg === 'string' ? rec.msg : '';
          return loc && msg ? `${loc}: ${msg}` : msg || loc;
        }
        return typeof item === 'string' ? item : '';
      })
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  ...
  return status ? `Request failed with status ${status}` : 'Request failed';
}
```

A 422 from `ExperimentCreate`'s "traffic allocations must sum to 100" arrives as
`variants: Traffic allocations must sum to 100% (currently 90%)` — the server's validator
message, not a generic error. That's the Week 2 "two layers of validation" from the client's
side: `validateForm` in `pages/experiments/new.tsx` mirrors the server rules for instant
feedback, and the server's message is shown verbatim when the two disagree.

Route classification and the gate:

```typescript
// 📁 frontend/src/utils/routes.ts
/** Routes rendered without the application shell. */
const BARE_ROUTES = new Set<string>(['/login', '/']);

/**
 * Routes inside the shell that do not require a session. The API is still the
 * enforcement point; these pages are either static or call public endpoints
 * (the power calculator).
 */
const OPEN_ROUTE_PREFIXES = ['/docs', '/power-calculator', '/404', '/500', '/_error'];

export function routeKind(pathname: string): RouteKind {
  if (BARE_ROUTES.has(pathname)) return 'bare';
  if (OPEN_ROUTE_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return 'open';
  }
  return 'protected';
}
```

```tsx
// 📁 frontend/src/pages/_app.tsx
export default function App({ Component, pageProps, router }: AppProps) {
  const kind = routeKind(router.pathname);

  let page = <Component {...pageProps} />;
  if (kind === "protected") {
    page = <RequireAuth>{page}</RequireAuth>;
  }
  if (kind !== "bare") {
    page = <AppShell>{page}</AppShell>;
  }

  return (
    <ErrorBoundary>
      ...
      <AuthProvider>
        <NotificationProvider>
          {page}
          <ToastContainer />
        </NotificationProvider>
      </AuthProvider>
    </ErrorBoundary>
  );
}
```

`RequireAuth`'s comment says the important thing — _"the API is the real enforcement"_ — the
gate is UX (spinner → redirect → inline 403 for the wrong role), never security. The nav in
`AppShell` hides "Admin" from non-admins the same way; the `/admin/*` endpoints still 403.

A page, in the house shape — services call, local state, render:

```typescript
// 📁 frontend/src/services/experiments.ts
export const ExperimentsService = {
  async list(params?: ExperimentListParams): Promise<ExperimentListResponse> {
    return apiFetch<ExperimentListResponse>(BASE, {
      query: { status_filter: params?.status, skip: params?.skip, limit: params?.limit, search: params?.search, sort_by: params?.sort_by, sort_order: params?.sort_order },
    });
  },
  async get(id: string): Promise<Experiment> { return apiFetch<Experiment>(`${BASE}/${id}`); },
  async create(data: CreateExperimentRequest): Promise<Experiment> { return apiFetch<Experiment>(BASE, { method: 'POST', json: data }); },
  // Lifecycle transitions — each returns the updated experiment.
  // start: draft|paused → active; pause: active → paused;
  // complete: active|paused → completed; archive: any non-archived → archived.
  async start(id: string): Promise<Experiment> { return apiFetch<Experiment>(`${BASE}/${id}/start`, { method: 'POST' }); },
  ...
};
```

The results page is the richest: `components/results/` has `ResultsDashboard`,
`MetricComparison`, `Visualizations` (Recharts confidence-interval and daily charts),
`Sequential` (the mSPRT trajectory) and `Breakdowns` (segment tables), plus
`LiveResultsPanel` driven by `hooks/useExperimentStream.ts`, a WebSocket hook with
auto-reconnect that authenticates with `?token=` because browsers can't set headers on a
WebSocket handshake. The targeting rule builder (`components/targeting/`) writes the
_dashboard_ rule shape from Week 3, Day 16 — `TargetingRuleBuilder → RuleGroupCard →
ConditionRow`, with the operator list in `utils/targeting.ts`.

Testing is Jest + React Testing Library + jsdom, with `global.fetch` mocked per test and
Recharts replaced by a mock (`src/__mocks__/recharts.tsx`, mapped in `jest.config.js`) so
charts render as inspectable stubs. The pages tests (`src/tests/pages/*.test.tsx`) are the
integration layer: render a page, mock the fetches it makes, assert the DOM and the request
bodies. Playwright covers E2E (28 specs), visual regression (8 snapshots) and axe-based
accessibility (10 audits), run nightly.

> **By the Way.** The container image is a **static export** (`output: 'export'` → `out/`)
> served by nginx-unprivileged on 8080, with `/api/`, `/ws/` and `/health*` proxied to
> `API_UPSTREAM` (default `http://api:8000`). That's why `NEXT_PUBLIC_API_URL` is empty in the
> image — same origin, no CORS — and why dynamic routes like `/experiments/[id]` need nginx
> location blocks, generated at build time from `.next/routes-manifest.json` by
> `scripts/nginx-routes.mjs`. The `/docs/*` pages are rendered at build time from the repo's
> `docs/` folder, which is why the build context is the repository root.

### Build It

Trace one page. Open `frontend/src/pages/feature-flags/[id].tsx` and list: which service
methods it calls, which `useApi` fetchers it declares, where the `FlagToggle` component sends
`activate`/`deactivate`, and what the targeting builder emits on save. Then run its test:

```bash
cd frontend
npx jest src/tests/pages/feature-flag-detail.test.tsx --watchAll=false
```

Read how the test mocks `global.fetch` and asserts the `PUT` body's `targeting_rules` — it's
the dashboard-shape rule from Day 16, checked at the client boundary.

### Key takeaways

- One `apiFetch`, one `ApiError` that understands FastAPI's `detail`, one `AuthContext` that
  never trusts the stored token.
- `routeKind` → `bare | open | protected`; `RequireAuth` is UX, the API enforces.
- Services mirror the API one-to-one; pages are thin; `useApi` wraps fetchers.
- Results = Recharts components over `/results/{id}` + a WebSocket live panel; targeting =
  the dashboard rule shape.
- Jest/RTL with mocked `fetch` and mocked Recharts; Playwright nightly; static export
  behind nginx in the image.

### Q&A

**Q. Why the pages router and not the app router?**
The dashboard predates the app router's stability, it's a static export (no server
components), and the pages tests are simpler with `router.pathname`. A migration to Next 16
and React 19 is held on a dedicated branch (Dependabot PRs #49–#51) for exactly this reason.

**Q. How does the dashboard know the user's role for the nav?**
`AuthContext.hasRole()` over the `UserMe` returned by `/auth/me` (`role`, `is_superuser`).
The nav items carry `roles: ['ADMIN', 'DEVELOPER']`; `RequireAuth` accepts a `roles` prop for
page-level gating.

**Q. Where's the OpenAPI type generation?**
There isn't one — types in `src/types/*.ts` are hand-written to match the schemas, and
`npm run openapi:dump` writes the live spec to `src/tests/fixtures/openapi.json` so a URL
guard test can catch a service path that no longer exists on the server.

**Q. Why `next dev -p 3100`?**
So the dev server doesn't collide with the compose stack's dashboard on 3000; both are in
the API's CORS defaults.

---

## Day 24 — CI/CD: the PR QA gate, the release gate, and the security scan

### What & why

Three GitHub Actions workflows run on every pull request to `main`, and branch protection
requires their always-on jobs. Each job exists because of a specific class of regression:

**`pr-qa-gate.yml`** (concurrency-cancelled per branch):

| Job                 | Catches                                                                                   |
| ------------------- | ----------------------------------------------------------------------------------------- |
| Unit Tests          | Logic regressions; runs `backend/tests/unit -x` against a Postgres service container, then the three Lambda test suites in separate pytest invocations (they each define `tests.conftest`) |
| Smoke Tests         | A router whose import fails, a route that no longer resolves (`test_wiring.py`)          |
| Frontend Tests      | Jest + `npm run build` — a page that doesn't compile can't ship                          |
| SDK Contract Tests  | A hash port that drifts from the golden vectors (Python + JS reference implementations)   |
| SDK Live Contract   | An SDK calling an endpoint the backend doesn't serve — seeds fixtures, starts uvicorn, runs each SDK's `contract_smoke` (Python, JS, OpenFeature ×2, edge, Go, Java, .NET, Ruby…) |
| Docker Smoke        | A broken image, entrypoint, bootstrap, seed or nginx proxy — builds both images, `docker compose up --wait`, checks `/health/ready`, asserts 401 without credentials, logs in, mints a key, assigns a user |

**`release-gate.yml`**: backend gate (full unit suite), frontend gate (tests + build),
security gate (`bandit -r backend/app -ll` for high-severity findings + gitleaks over the
PR's commits), and a summary job that fails if any gate failed.

**`security-scan.yml`**: Python (`pip-audit`/Bandit), frontend (`npm audit`), Semgrep,
secret detection, container scanning — on push, PR and a weekly schedule.

Then the deployment workflows, which are manual or main-only: `deploy-dev.yml` on push to
main, `deploy-prod.yml` by `workflow_dispatch` with a version input, `db-migrate.yml`,
`rollback.yml`, plus `nightly-qa.yml` (unit → integration + smoke, SDK contracts, frontend,
E2E, accessibility), `performance-tests.yml` (weekly Locust), `infrastructure-tests.yml` (CDK
Python tests) and `cognito-integration-tests.yml`.

#### Why the live contract and docker smoke are _required_

Because they are the two jobs that would have caught the September 2026 breakage. A unit
suite can be green with a broken `/tracking/track`; the live contract runs a real client
against it. A Dockerfile can build with a broken entrypoint; docker smoke starts it and logs
in. Both are slower than unit tests (the live contract installs four toolchains), which is why
they run as separate jobs in parallel rather than serially.

### How Experimently does it

The live-contract job's shape — services, environment, toolchains, seed, start, run:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
  sdk-live-contract:
    name: SDK Live Contract
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        ...
      redis:
        image: redis:7
        ...
    env:
      APP_ENV: development
      ENVIRONMENT: development
      POSTGRES_SERVER: localhost
      ...
      SECRET_KEY: ci-only-sdk-live-contract-secret-key-0123456789abcdef
      EXPERIMENTLY_API_URL: http://127.0.0.1:8000
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v7
        with:
          python-version: '3.11'
      - name: Set up Node.js
        uses: actions/setup-node@v7
        with:
          node-version: '22'
      - name: Set up Go
        uses: actions/setup-go@v5
        ...
      - name: Set up Java
        uses: actions/setup-java@v4
        ...
      - name: Set up .NET
        uses: actions/setup-dotnet@v4
        ...
      - name: Seed contract fixtures
        run: python backend/scripts/seed_sdk_contract.py
      - name: Start backend
        run: |
          nohup python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 > backend.log 2>&1 &
          for i in $(seq 1 60); do
            curl -sf http://127.0.0.1:8000/health > /dev/null && exit 0
            sleep 1
          done
```

And the docker smoke's assertions — note that it checks the **auth gate** (401 without
credentials) as a first-class property of a healthy stack:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
      - name: Start the stack
        run: docker compose -f docker-compose.yml up -d --wait --no-build postgres redis api frontend
      - name: API readiness and auth gate
        run: |
          curl -sf localhost:8000/health/ready -o ready.json
          grep -q '"status":"healthy"' ready.json
          curl -sf localhost:8000/health/live >/dev/null
          # /api/v1/experiments 307-redirects to the trailing-slash route; follow it.
          code=$(curl -sL -o /dev/null -w '%{http_code}' localhost:8000/api/v1/experiments)
          test "$code" = "401" || { echo "expected 401 without credentials, got $code"; exit 1; }
      - name: Login, create an API key, assign a user (sdk_contract_ab)
        ...
```

The production deploy is the sequence the whole week builds toward:

```
pre-deployment-checks → run-tests → build-and-push (ECR, immutable tag)
  → run-migrations (one-shot Fargate task: python -m backend.app.db.bootstrap)
  → deploy (register task definition with the new image; CodeDeploy blue/green; 10% bake; wait)
  → smoke-tests (against the live URL)
  → post-deployment (notify)
```

`rollback.yml` re-points the service at a previous task definition. Because migrations run
_before_ traffic shifts and are additive (Week 2, Day 14), the previous image is always
compatible with the current schema.

> **Watch Out!** Two CI facts that look like flakes and aren't. `pytest.ini`'s `addopts`
> adds `--cov`, so a job that installs only `pytest` (the SDK contract job) must pass
> `-o addopts=""` or it fails on an unknown option. And gitleaks on `pull_request` needs
> `pull-requests: read` permission to list the PR's commits — the release gate grants it
> explicitly; a copied workflow without it fails with an opaque API error.

### Build It

Run the PR gate locally, job by job, before you open a PR:

```bash
make test                                        # backend unit+integration+smoke, frontend tests+build
python -m pytest tests/sdk-contract/test_python_sdk.py -q -o addopts="" && node tests/sdk-contract/test_js_sdk.js
python backend/scripts/seed_sdk_contract.py && python tests/sdk-contract/live/run_live_contract.py --sdk python --sdk js --strict
docker compose up -d --build --wait postgres redis api frontend && curl -sf localhost:8000/health/ready
bandit -r backend/app -ll
```

Then read `.github/workflows/nightly-qa.yml` and list what runs nightly that _doesn't_ run on
PRs, and why (hint: time and browsers).

### Key takeaways

- PR gate: unit + Lambda, smoke, frontend, SDK contract (golden vectors), SDK live contract,
  docker smoke. Release gate: backend, frontend, Bandit + gitleaks. Security scan: Bandit,
  npm audit, Semgrep, secrets, containers.
- Live contract and docker smoke are required because they catch what mocks can't.
- Deploy: tests → immutable image → one-shot migration task → CodeDeploy blue/green with a
  bake → smoke → notify; rollback re-points the task definition.
- Nightly: integration, E2E, a11y, visual; weekly: performance.

### Q&A

**Q. Why is `-x` on the unit job?**
Fail fast: with 3,700 tests, stopping at the first failure gives feedback minutes sooner and
keeps the log readable. The nightly run doesn't use `-x`, so you get the full failure list
there.

**Q. The process memory says every PR needs a GitHub issue and a `/review`. Where's that
enforced?**
By convention and review, not CI: each work item gets an issue, the PR says `Closes #N`, and
`/code-review` runs before merge. It's in the team's working agreements (see the onboarding
guide).

**Q. What if a toolchain is missing on the CI runner?**
The live runner reports `SKIP` per SDK unless `--strict`; the CI job runs the SDKs whose
toolchain `ubuntu-latest` has (Android, Flutter, Elixir and iOS are unit-tested only).

**Q. How do I run the Cognito tests locally?**
`python -m pytest backend/tests/integration/auth/ -p no:cov -q` — the package's conftest
selects the Cognito provider and `moto` mocks the user pool. No AWS credentials needed.

---

## Day 25 — Containers and the compose stack

### What & why

Two images, one compose file, and an entrypoint that makes a fresh database usable without a
human. The design goal was the Community Edition promise: `docker compose up -d --wait` on a
laptop with nothing but Docker, and you can log in.

- **`experimently-api`** (`backend/Dockerfile`): a two-stage Python 3.11 image — builder
  installs the runtime requirement set into a venv; runtime copies the venv and `backend/`,
  runs as a non-root user, `HEALTHCHECK GET /health/live`, `ENTRYPOINT
  backend/docker-entrypoint.sh`, `CMD uvicorn backend.app.main:app --workers
  ${WEB_CONCURRENCY:-1}`. A `ce` target (default) and an `ee` target that additionally copies
  `ee/` when it exists.
- **`experimently-web`** (`frontend/Dockerfile`): builds the Next.js static export, generates
  nginx location blocks for dynamic routes, and serves with nginx-unprivileged on 8080,
  proxying `/api/`, `/ws/` and `/health*` to the API.

### How Experimently does it

The entrypoint's contract is in its header:

```bash
# 📁 backend/docker-entrypoint.sh
#   1. Wait for PostgreSQL (POSTGRES_SERVER/POSTGRES_PORT) to accept connections.
#   2. RUN_MIGRATIONS=true (default)  ->  python -m backend.app.db.bootstrap
#      (creates the schema on a fresh database, `alembic upgrade head` otherwise).
#   3. SEED=<comma list of demo,shoplab,streampulse,sdk-contract>  ->  run the
#      matching backend/scripts/seed_*.py once. Completed seeds are recorded in
#      the `seed_markers` table (backend/scripts/seed_markers.py) so a restart
#      of the container does not re-seed. SEED_FORCE=true re-runs them.
#   4. exec "$@" — by default uvicorn; `--workers ${WEB_CONCURRENCY:-1}` and the
#      proxy flags are appended when the command is uvicorn and the caller did
#      not pass them explicitly.
#
# Every step can be skipped: RUN_MIGRATIONS=false, SEED= (empty),
# WAIT_FOR_DB=false. The script runs with `set -euo pipefail`: a failed
# bootstrap or seed stops the container instead of starting a half-initialised
# API.
```

Two details are worth knowing before they bite. The **OpenSSL probe** at the top: on some
arm64 VMs (Docker Desktop on Apple M4) the `cryptography` wheel's OpenSSL selects CPU
features the guest can't execute and dies with SIGILL; the entrypoint probes once and sets
`OPENSSL_armcap=0` if so. And **`set -euo pipefail`**: a failed seed stops the container —
deliberately — so `docker compose up --wait` fails loudly instead of leaving you with an API
that has no experiments.

The compose file's header is the operator's cheat sheet:

```yaml
# 📁 docker-compose.yml
#   docker compose up -d --wait            # postgres + redis + api + dashboard
#   docker compose --profile demo up -d    # + ShopLab (:3200), StreamPulse (:3300)
#                                          #   and their traffic simulators
#   docker compose --profile tools up -d   # + pgAdmin (:5050)
#   docker compose --profile aws up -d     # + LocalStack (:4566)
#   docker compose down -v                 # stop and drop the volumes
#
# First start: the api container waits for Postgres, creates the schema
# (RUN_MIGRATIONS=true -> python -m backend.app.db.bootstrap) and applies the
# seeds in SEED (default `demo`: admin@demo.com / Demo1234!, three experiments,
# two flags). Seeds run once per database (seed_markers table).
```

and the `api` service shows every runtime knob with its default, which is the closest thing
to a configuration reference the platform has:

```yaml
# 📁 docker-compose.yml
  api:
    build:
      context: .
      dockerfile: backend/Dockerfile
      target: ${EXPERIMENTLY_EDITION:-ce}
    environment:
      ENVIRONMENT: ${ENVIRONMENT:-development}
      LOG_FORMAT: ${LOG_FORMAT:-console}          # json | console
      WEB_CONCURRENCY: ${WEB_CONCURRENCY:-1}       # 1 worker per container; scale containers instead
      AUTH_PROVIDER: ${AUTH_PROVIDER:-local}       # local | cognito
      DEV_AUTH_BYPASS: ${DEV_AUTH_BYPASS:-false}   # never true outside development/test
      SECRET_KEY: ${SECRET_KEY:-dev-only-change-me-9f1c7b2e4a6d8e0f1a2b3c4d5e6f7a8b}
      FIRST_SUPERUSER: ${FIRST_SUPERUSER:-admin@demo.com}
      FIRST_SUPERUSER_PASSWORD: ${FIRST_SUPERUSER_PASSWORD:-Demo1234!}
      POSTGRES_SERVER: postgres
      ...
      RUN_MIGRATIONS: ${RUN_MIGRATIONS:-true}
      SEED: ${SEED:-demo}
      REDIS_HOST: redis
      REDIS_REQUIRED: ${REDIS_REQUIRED:-false}
      CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000,http://localhost:3100,http://localhost:3200,http://localhost:3300}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    # Readiness (DB reachable) rather than the image's liveness check, so
    # `docker compose up --wait` returns only once bootstrap + seeds are done
    # and the API answers. start_period covers the first-run seeding.
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health/ready"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 300s
```

Note the healthcheck distinction: the _image_ checks liveness (process up), the _compose
service_ checks readiness (database answers) with a five-minute `start_period` so first-run
seeding doesn't count as unhealthy. The demo profile's simulators read the API keys the seeds
write to a shared `demo_keys` volume, and the demo images bake fixed keys
(`eptk_demo_shoplab_…`) so the storefronts work without a manual step.

> **Did You Know?** The API's dashboard proxy means the browser never sees the API's origin:
> `/api/v1/...` on `:3000` is nginx forwarding to `api:8000` inside the compose network. That's
> also why the dashboard image's `NEXT_PUBLIC_API_URL` build arg is empty — and why, if you
> build the image with an absolute URL, you re-enter CORS territory.

### Build It

Bring up the stack from nothing, then break the seed on purpose to see fail-loud:

```bash
docker compose down -v
docker compose up -d --wait                     # first run: bootstrap + demo seed (watch: docker compose logs -f api)
open http://localhost:3000                      # admin@demo.com / Demo1234!

# re-run with a bogus seed name: the entrypoint fails and --wait returns non-zero
docker compose down -v && SEED=demo,nope docker compose up -d --wait; echo "exit=$?"; docker compose logs api | tail -5
```

Then inspect the layers: `docker history experimently-api:ce | head`, and confirm the
runtime image has no compiler (`docker run --rm experimently-api:ce which gcc || echo none`).

### Key takeaways

- Two images: API (two-stage, non-root, liveness healthcheck, entrypoint) and web (static
  export + nginx proxy).
- The entrypoint waits for Postgres, bootstraps, seeds once (markers), then execs uvicorn —
  and stops the container if any step fails.
- Compose profiles: core, `demo` (+ShopLab/StreamPulse/simulators), `tools` (pgAdmin), `aws`
  (LocalStack); every knob is `${VAR:-default}`.
- Service readiness waits on `/health/ready` with a long `start_period`; the image's own
  check is `/health/live`.

### Q&A

**Q. How do I run the enterprise target?**
`EXPERIMENTLY_EDITION=ee docker compose build api` — the `ee` stage copies `ee/` when it
exists. Without that directory the build is identical to `ce`.

**Q. Where do I set production secrets for compose?**
A `.env` next to `docker-compose.yml` (compose reads it automatically) or the shell. Change
`SECRET_KEY`, `FIRST_SUPERUSER_PASSWORD` and `POSTGRES_PASSWORD` before exposing the stack; the
API refuses to start in `ENVIRONMENT=production` with the defaults (Week 2, Day 9).

**Q. Can I point the containerised API at LocalStack?**
`docker compose --profile aws up -d` and `AWS_ENDPOINT_URL=http://localstack:4566` on the API
service; DynamoDB counters, Kinesis and S3 paths then talk to LocalStack.

---

## Day 26 — AWS: the CDK stacks, ECS Fargate, Lambda, Kinesis, DynamoDB

### What & why

The AWS deployment is defined in **CDK (Python)** under `infrastructure/cdk/`: one `app.py`
that instantiates fourteen stacks per environment (`dev`, `staging`, `prod`, `demo`) with
explicit dependencies. The shape follows the boxes in the README diagram:

| Stack                          | Provides                                                                          |
| ------------------------------ | --------------------------------------------------------------------------------- |
| `authentication`               | Cognito user pool, groups (Admins/Developers/Analysts/Viewers)                    |
| `vpc`                          | VPC, subnets, security groups                                                     |
| `dynamodb` / `dynamodb-counters` | Tables for the Lambda fast path; the P2-B real-time counters table (atomic ADD) |
| `database` (enhanced)          | Aurora PostgreSQL, Multi-AZ, Secrets Manager password                             |
| `redis`                        | ElastiCache Redis                                                                 |
| `compute`                      | ECS cluster, Lambda role, Lambda function definitions                             |
| `api`                          | API Gateway in front of the Lambdas                                               |
| `analytics`                    | Kinesis stream → Firehose → S3 data lake; Lambda consumer → OpenSearch domain    |
| `monitoring`                   | CloudWatch dashboards, alarms (API 5xx, Lambda errors/duration, DynamoDB throttling, Aurora CPU, iterator age, Redis CPU, error rate, latency, active experiments) |
| `fargate`                      | ALB (HTTPS + redirect), blue/green target groups, ECS Fargate service with CodeDeploy, auto-scaling, secrets from Secrets Manager |
| `migrations`                   | One-shot Fargate task definition that runs the bootstrap                          |
| `glue-etl`                     | Glue jobs, crawler, Athena over the S3 data lake                                  |

Sizing is environment-aware (`demo` uses `db.t3.small`, `cache.t3.micro`, one Fargate task);
the account and region come **only** from the environment (`CDK_DEFAULT_ACCOUNT` /
`AWS_ACCOUNT_ID`), never hard-coded.

### How Experimently does it

The app wires stacks with explicit dependencies so `cdk deploy --all` orders them:

```python
# 📁 infrastructure/cdk/app.py
VALID_ENVIRONMENTS = ["dev", "staging", "prod", "demo"]
env_name = os.environ.get("ENVIRONMENT", "dev")
...
# Demo environment uses smaller instance sizing to reduce cost
is_demo = env_name == "demo"
db_instance_size = "db.t3.small" if is_demo else "db.t3.medium"
cache_node_type = "cache.t3.micro" if is_demo else "cache.t3.small"
fargate_desired_count = 1 if is_demo else 2

account = os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")
region = os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-west-2")
if not account:
    raise SystemExit("AWS account id not set: export AWS_ACCOUNT_ID ...")
env = Environment(account=account, region=region)

app = App()
auth_stack = AuthenticationStack(app, f"experimentation-auth-{env_name}", environment=env_name, env=env)
vpc_stack = VpcStack(app, f"experimentation-vpc-{env_name}", env=env)
...
database_stack = EnhancedDatabaseStack(app, f"experimentation-database-{env_name}", vpc=vpc_stack.vpc, environment=env_name, env=env)
database_stack.add_dependency(vpc_stack)
...
fargate_stack = FargateServiceStack(
    app, f"experimentation-fargate-{env_name}",
    vpc=vpc_stack.vpc, ecs_cluster=compute_stack.ecs_cluster, ecs_security_group=compute_stack.ecs_security_group,
    env_name=env_name, certificate_arn=certificate_arn, env=env,
)
fargate_stack.add_dependency(compute_stack)
fargate_stack.add_dependency(database_stack)
fargate_stack.add_dependency(redis_stack)

# Migration task stack: one-shot Fargate task for Alembic migrations
migration_stack = MigrationTaskStack(app, f"experimentation-migrations-{env_name}", ecs_cluster=compute_stack.ecs_cluster, env_name=env_name, env=env)
```

The Fargate stack reads secrets by convention and gives the task the least privilege it needs:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        # --- Secrets from Secrets Manager ---
        # These secrets must be created manually (or by another stack) before
        # deploying this stack. The secret paths follow the convention:
        #   /<env>/experimentation/<secret-name>
        db_secret = secretsmanager.Secret.from_secret_name_v2(self, "DbSecret", f"/{env_name}/experimentation/db-password")
        jwt_secret = secretsmanager.Secret.from_secret_name_v2(self, "JwtSecret", f"/{env_name}/experimentation/jwt-secret")
        redis_secret = secretsmanager.Secret.from_secret_name_v2(self, "RedisSecret", f"/{env_name}/experimentation/redis-url")

        # --- IAM Task Role ---
        # The task role is assumed by the application code running inside the
        # container. It has least-privilege access to only the secrets it needs.
        task_role = iam.Role(self, "ECSTaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"), ...)
```

`ProdSettings.get_db_url` (Week 2, Day 9) is the application side of that convention: when
`POSTGRES_PASSWORD` isn't injected, it fetches `/<env>/experimentation/db-password` from
Secrets Manager. The ECS task definition normally injects it as an environment variable, so
the fallback rarely runs.

The **analytics stack** is the event pipeline that the Community Edition doesn't have: a
Kinesis Data Stream receives events (the event-processor Lambda in `backend/lambda/
event_processor/` validates, enriches, aggregates, archives to S3 via Firehose, and updates
DynamoDB counters), a Firehose delivery stream lands raw events in the S3 data lake, and a
Lambda with a Kinesis event source indexes into an OpenSearch domain for real-time queries.
The **Glue ETL** stack crawls the lake and exposes it to Athena — the `/etl` endpoints in the
API drive those jobs.

The **monitoring stack** defines a dashboard and the alarms an operator pages on — API 5xx
rate, Lambda errors and duration, DynamoDB throttling, Aurora CPU, Kinesis iterator age, Redis
CPU, application error logs, high error rate, high latency, and an "unusually many active
experiments" sanity alarm.

> **Watch Out!** The `compute` stack's Lambda functions are **placeholders** — inline
> `def handler` bodies that return a static message, with a comment "in production, use
> `lambda_.Code.from_asset()` instead." The real handlers under `backend/lambda/` are tested in
> CI (301 tests) but are not packaged by this stack. If you're asked to "turn on the Lambda
> fast path", the work is `from_asset` packaging plus the DynamoDB tables the handlers expect
> — and reconciling the 0–1 vs 0–100 allocation convention before any SDK is pointed at API
> Gateway.

### Build It

Synthesize without deploying, and read what one stack becomes:

```bash
cd infrastructure/cdk
pip install -r requirements.txt
AWS_ACCOUNT_ID=123456789012 ENVIRONMENT=demo cdk synth experimentation-fargate-demo 2>/dev/null | head -80
AWS_ACCOUNT_ID=123456789012 ENVIRONMENT=demo cdk ls
```

Then run the infrastructure tests the CI job runs (`infrastructure-tests.yml`):

```bash
cd ../.. && python -m pytest infrastructure/tests -q -p no:cov
```

### Key takeaways

- Fourteen CDK stacks per environment, explicit dependencies, account/region from the
  environment only, demo sizing.
- Runtime: ECS Fargate behind an ALB with CodeDeploy blue/green and auto-scaling; Aurora
  Multi-AZ; ElastiCache; secrets under `/<env>/experimentation/*`.
- Fast path and analytics: Lambda (assignment, flags, event processor) + API Gateway,
  DynamoDB (tables + atomic counters), Kinesis → Firehose → S3, Kinesis → Lambda →
  OpenSearch, Glue/Athena.
- The compute stack's Lambdas are placeholders; the real handlers aren't packaged yet.
- Monitoring stack = CloudWatch dashboards + eleven alarms.

### Q&A

**Q. Do I need AWS to develop?**
No. The Community Edition (compose) runs without any AWS service; `moto` covers Cognito and
DynamoDB in tests; LocalStack (the `aws` profile) covers DynamoDB/Kinesis/S3 locally when
you're working on those paths.

**Q. Why CDK in Python when the app is Python but the dashboard is TypeScript?**
One language for backend and infrastructure keeps the CDK tests in the same pytest suite and
lets stacks import constants from the app when useful. The CDK jsii runtime still needs Node,
which is why the infrastructure-tests workflow installs it.

**Q. How is the demo environment deployed?**
`demo/setup-aws.sh` exports the account and `ENVIRONMENT=demo` and runs `cdk deploy --all`
with the smaller sizes. It's the same stacks, cheaper.

**Q. What does blue/green actually do for us?**
CodeDeploy stands up the new task set on the green target group, shifts 10% of traffic,
bakes, then shifts the rest; the old task set stays warm for the rollback window. A failed
health check during the bake automatically reverts. The deploy workflow waits on that.

---

## Day 27 — Observability: structured logs, Prometheus, health probes, alerting

### What & why

You can't operate what you can't see. Four signals, each with a specific consumer:

1. **Structured logs** (`structlog`) — JSON in staging/production for CloudWatch Logs
   Insights, coloured console in development; every line carries `request_id`, `path`,
   `method` bound by the outermost middleware.
2. **Prometheus metrics** on `/metrics` — HTTP latency/count, business counters (assignments,
   events, flag evaluations), cache hits, rate-limit hits/rejections, scheduler ticks and
   last-success timestamps, active experiments.
3. **Health probes** — `/health/live` (process up, touches nothing), `/health/ready` (database
   answers `SELECT 1`; Redis reported, fatal only if `REDIS_REQUIRED`), `/health` (alias for
   the existing ALB wiring).
4. **Alerting** — `NotificationService` → Slack and email for experiment lifecycle events,
   safety rollbacks and scheduler failures; a per-user preference model and a delivery log
   under `/notifications`; CloudWatch alarms for infrastructure.

### How Experimently does it

Logging is configured before anything else in `main.py`, and the request-ID middleware binds
context that every subsequent log line inherits:

```python
# 📁 backend/app/core/logger.py
_log_context: contextvars.ContextVar[dict] = contextvars.ContextVar("log_context", default={})

def bind_log_context(**kwargs: Any) -> None:
    """Merge *kwargs* into the current async-task log context."""
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
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id: str = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Bind to the current async context so all log lines in this request
        # automatically include request_id, path, and method.
        bind_log_context(request_id=request_id, path=request.url.path, method=request.method)

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

A `contextvars.ContextVar` is per-async-task, so concurrent requests don't leak ids into each
other's lines, and an upstream `X-Request-ID` (from the ALB or a caller) is honoured for
distributed tracing. The same id is echoed on the response, so a support ticket with a request
id maps to exactly the log lines that produced it.

Metrics are a module-level registry with label-safe helpers:

```python
# 📁 backend/app/core/metrics.py
http_request_duration_seconds: Histogram = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint", "status_code"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

experiment_assignments_total: Counter = Counter(
    "experiment_assignments_total", "Total experiment assignments", ["experiment_id", "variant_id"],
)

feature_flag_evaluations_total: Counter = Counter(
    "feature_flag_evaluations_total", "Total feature flag evaluations", ["flag_key", "result"],
)

rate_limit_rejections_total: Counter = Counter(
    "rate_limit_rejections_total", "Total requests rejected by the rate limiter (HTTP 429)", ["endpoint"],
)
```

plus the scheduler series Day 17 mentioned. `/metrics` is guarded: with `METRICS_TOKEN` set
it requires the token (bearer or `?token=`); without it, it's open only in development/test
and 403 elsewhere; `METRICS_ENABLED=false` makes it a 404.

Health probes are deliberately asymmetric:

```python
# 📁 backend/app/core/health.py
"""
``GET /health/live``
    Liveness: the process is up and the event loop answers. Never touches the
    database or Redis, so a dependency outage does not make orchestrators
    restart healthy processes. Used by the container ``HEALTHCHECK``.

``GET /health/ready``
    Readiness: PostgreSQL must answer ``SELECT 1``. Redis is checked and
    reported but only fails readiness when ``REDIS_REQUIRED=true`` (the
    application degrades gracefully without Redis: rate limiting falls back to
    memory, caching is skipped). Disk space below 1 GB is reported as ``low``.
    Returns 200 when ready, 503 otherwise.
"""
```

The distinction is the whole point: a database outage must take the service _out of the load
balancer_ (readiness fails) without making the orchestrator _restart_ every healthy process
(liveness still passes) — restarting wouldn't fix the database and would add a thundering
herd of reconnects when it comes back. `_safe_error` scrubs anything containing "password"
from the reported error so a misconfigured DSN can't leak credentials through the probe.

Alerting closes the loop from Week 3: `NotificationService.notify_safety_rollback` and
`notify_experiment_started/completed` fan out to `SlackNotifier` (bot token, default channel
`#platform-alerts`) and `EmailNotifier` (SendGrid or SMTP), gated by `SLACK_ENABLED` /
`EMAIL_ENABLED` and per-user preferences, with every delivery attempt logged. Scheduler
failures also post to `NOTIFICATION_WEBHOOK_URL`. The Admin → Notifications page manages
preferences and shows the delivery log.

> **By the Way.** The CloudWatch dashboards in `infrastructure/cloudwatch/` (API performance,
> system health) are JSON deployed by `deploy-dashboards.sh`, and the Logs Insights queries
> they rely on assume the JSON log format — one more reason `LOG_FORMAT` defaults to `json`
> outside development. The three log groups (`/experimentation-platform/api`, `/services`,
> `/errors`) are created by the commands in `CLAUDE.md`'s monitoring section.

### Build It

Correlate a request across all three signals:

```bash
# 1. a request with your own id
curl -s -H "X-Request-ID: week4-demo-001" -H "Authorization: Bearer $TOKEN" $API/experiments/ -o /dev/null -D - | grep -i x-request-id
# 2. the log line(s) carrying it (console format locally; JSON with LOG_FORMAT=json)
docker compose logs api 2>/dev/null | grep week4-demo-001 | head -3
# 3. the metric it incremented
curl -s http://localhost:8000/metrics | grep 'http_requests_total{.*experiments' | head -3

# probes behave differently when the database is down
docker compose stop postgres
curl -s -o /dev/null -w "live=%{http_code} " localhost:8000/health/live; curl -s -o /dev/null -w "ready=%{http_code}\n" localhost:8000/health/ready
docker compose start postgres
```

Then read `backend/app/core/health.py::check_redis` and confirm that without
`REDIS_REQUIRED` a stopped Redis is reported but doesn't fail readiness.

### Key takeaways

- Logs: structlog, JSON outside development, `request_id`/`path`/`method` bound per request
  via a `ContextVar`; `X-Request-ID` honoured and echoed.
- Metrics: one Prometheus registry; HTTP histogram, business counters, rate-limit and
  scheduler series; `/metrics` token-guarded outside dev/test.
- Probes: live never touches dependencies; ready requires Postgres, reports Redis; `/health`
  is an alias.
- Alerts: Slack/email via `NotificationService` with preferences and a delivery log;
  CloudWatch alarms for infrastructure.

### Q&A

**Q. Why not OpenTelemetry tracing?**
Not yet. The request id gives log correlation, Prometheus gives latency distributions, and
the platform's request paths are shallow (one service, one database). Tracing becomes worth
its cost when the Lambda/Kinesis paths are in production use.

**Q. Where do I look first when the dashboard says results are stale?**
`GET /scheduler/health` and the Admin → Scheduler page for failed or skipped ticks; then
`scheduler_last_success_timestamp{name="metrics"}` on `/metrics`; then the API logs filtered by
`scheduler`. Results themselves are cached 5 min while running — `use_cache=false` first.

**Q. Can `/metrics` be scraped in production?**
Yes, with `METRICS_TOKEN` set and the scraper sending it as a bearer token (or `?token=`).
Without a token the endpoint returns 403 outside development/test.

**Q. How do I add a metric?**
Declare it in `core/metrics.py`, add a `record_*` helper that normalises labels, call the
helper from the code path, and keep label cardinality bounded (experiment and flag ids are
acceptable; user ids are not).

---

## Day 28 — Capstone: the whole picture — you can build Experimently

### What & why

Twenty-seven days, four weeks, one system. Today you draw it from memory and explain every
arrow — the test of whether the architecture is in your head rather than in this book.

### The map, with every day's box filled in

```
                          ┌─────────────────────────────────────────────────────┐
  YOUR APP + SDK          │  DASHBOARD (Next.js static export behind nginx)      │
  (15 languages; Day 20)  │  api.ts → AuthContext → RequireAuth → pages (Day 23) │
  X-API-Key               │  Authorization: Bearer <local JWT | Cognito>         │
        │                 └────────────────────────┬─────────────────────────────┘
        │ assign / evaluate / track                │ experiments / flags / results / admin / ws
        ▼                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│ FASTAPI  main.py (Day 8)                                                          │
│  RequestID → Prometheus → RateLimit (auth 10/min · SDK 6000/min · 300/min) →     │
│  SecurityHeaders → CORS → api/api.py routers                                      │
│  deps.py: get_db · get_current_user (bypass → local JWT → Cognito) · get_api_key  │
│           (Day 10) · RBAC role×resource×action + ownership (Day 11)              │
│  Settings: ENVIRONMENT, fail-closed validators (Day 9)                            │
│                                                                                   │
│  tracking.py ──► AssignmentService (Day 4): sticky row → holdout → MEG →         │
│                  targeting (rules engine + adapter, Day 16) → md5 % 100 → row     │
│              ──► EventService (Day 5): resolve key → variant_id → event row        │
│  feature_flags.py ──► FeatureFlagService (Day 15): inactive? → rule % → md5 % 100 │
│                       → RawMetric + ErrorLog                                       │
│  results.py ──► AnalysisService (Day 6): COUNT(assignments), COUNT(events) via    │
│                 event_matching → Fisher/z/Welch → CI → SRM, correction, min n     │
│                 + sequential / CUPED / Bayesian / bandit blocks (Day 19)          │
│                                                                                   │
│  lifespan (Day 8) → 5 schedulers, each tick via run_locked_tick (Day 17):         │
│    experiment · rollout · metrics · safety (error_rate → rollback, Day 18) ·      │
│    bandit (DynamoDB → Postgres → BanditState → priors, Day 19)                    │
│  /health/live /health/ready /metrics (Day 27)                                     │
└───────┬──────────────────────────────┬───────────────────────────┬────────────────┘
        ▼                              ▼                           ▼
┌──────────────────┐   ┌─────────────────────┐   ┌──────────────────────────────────┐
│ POSTGRESQL       │   │ REDIS               │   │ AWS (CDK, Day 26)                │
│ schema           │   │ rate-limit counters │   │ ECS Fargate + ALB blue/green     │
│ experimentation  │   │ results cache       │   │ Aurora · ElastiCache · Cognito   │
│ (models Day 3/12,│   │ (fallback: memory)  │   │ Lambda assign/flags/events       │
│  alembic +       │   └─────────────────────┘   │ DynamoDB tables + counters       │
│  bootstrap Day 13)                             │ Kinesis → Firehose → S3 → Glue   │
└──────────────────┘                             │ Kinesis → Lambda → OpenSearch    │
                                                 │ CloudWatch dashboards + alarms   │
   TESTS (Day 22): unit · lambda · integration · └──────────────────────────────────┘
   smoke · sdk contract · live contract · docker smoke · e2e · perf
   CI (Day 24): pr-qa-gate · release-gate · security-scan · deploy-prod · rollback
   CONTAINERS (Day 25): experimently-api (entrypoint: wait → bootstrap → seed → uvicorn)
                        experimently-web (static export + nginx proxy) · compose profiles
```

### Build It — the final exercise

Close this book. On a whiteboard or a blank file, draw the map above from memory. Then, for
each of these five arrows, write the file that implements it and the one row or line it
produces:

1. SDK → `/tracking/assign` → `assignments` row.
2. SDK → `/tracking/track` → `events` row with `variant_id`.
3. Dashboard → `/results/{id}` → `p_value` (and the three honesty checks).
4. Client error → `/tracking/errors` → safety tick → `rollout_percentage` change →
   `safety_rollback_records` row.
5. Pull request → PR gate → release gate → image → migration task → blue/green → smoke.

If you can do that without opening the repo, you can build Experimently. If you can't, the
day whose arrow you missed is the day to reread — that's what the table of contents is for.

### Where to go next

- **[Experimentation Statistics](../experimentation-statistics/README.md)** if you'll work on
  results, sequential testing, CUPED, Bayesian or bandits.
- **[SDK Engineering](../sdk-engineering/README.md)** if you'll touch any of the fifteen SDKs,
  the contract harness or the demo apps.
- **[Platform Operations](../platform-operations/README.md)** if you'll run it — compose, CDK,
  Fargate, observability, releases.
- The **[usage guide](../guides/usage.md)** to see the product as a product manager does, and
  the **[troubleshooting guide](../guides/troubleshooting.md)** for the day something breaks.

---

## Week 4 Workshop

### Quiz

**1. Name the six jobs in the PR QA gate and the regression class each catches.**
Unit Tests (logic, incl. the three Lambda suites), Smoke Tests (route wiring), Frontend Tests
(Jest + build), SDK Contract Tests (hash golden vectors), SDK Live Contract (SDKs vs the real
API), Docker Smoke (images, entrypoint, bootstrap, seed, nginx, auth gate).

**2. Why did 5,000 green tests not catch a broken `/tracking/track`?**
The endpoint's tests mocked the service and asserted on the mock; the shape mismatch between
endpoint and service was never exercised. Hot paths must be driven through the public API
against a real database.

**3. `/health/live` returns 200 and `/health/ready` returns 503. What's happening and what
should the orchestrator do?**
The process is up but PostgreSQL isn't answering (or Redis, if `REDIS_REQUIRED`). Take the
instance out of the load balancer (readiness) but do not restart it (liveness) — restarting
won't fix the database.

**4. In what order does the production deploy run, and why do migrations come where they do?**
Checks → tests → build and push the image → run the one-shot migration task → CodeDeploy
blue/green (10% bake) → smoke tests → notify. Migrations run before traffic shifts because
they're additive/nullable, so the old image keeps working against the new schema and a
rollback of the image needs no schema rollback.

**5. Where does a request id come from, where does it go, and what carries it between log
lines?**
From the `X-Request-ID` header if present, else a fresh UUID4, in the outermost middleware;
it's echoed on the response and bound into a `contextvars.ContextVar` that a structlog
processor injects into every event in that async task.

**6. What are the CDK compute stack's Lambdas today?**
Inline placeholders returning a static message; the real handlers under `backend/lambda/`
are tested in CI but not packaged by the stack (`Code.from_asset` is the TODO).

### Exercises

**E4.1 — Write the twin.** For `SafetyService.get_error_metrics`, sketch a unit test that
seeds `ErrorLog` and `RawMetric` rows in a test session and asserts `error_rate`, and name the
integration test that would drive it through `POST /safety/feature-flags/{id}/check`.

**E4.2 — Classify routes.** Using `routeKind`, classify `/login`, `/`, `/docs/sdk/python`,
`/power-calculator`, `/experiments/[id]`, `/admin/safety`, and state which get the shell and
which get `RequireAuth`.

**E4.3 — Add a gate.** Propose a new required PR-gate job that would have caught the "nothing
starts the bandit scheduler" bug, and say what it asserts and how long it should take.

**E4.4 — Compose a profile.** Write a compose service for a hypothetical `docs` container that
serves this documentation repo on `:3400`, in the file's style (profile, healthcheck,
`${VAR:-default}` port).

**E4.5 — Alarm design.** Choose one Prometheus series from `core/metrics.py` and one
CloudWatch alarm from the monitoring stack, and describe the operator action each should
trigger and the false-positive you'd guard against.

### Solutions

**S4.1**

```python
def test_error_rate_divides_errors_by_evaluations(db_session, feature_flag):
    now = datetime.utcnow()
    for _ in range(4):
        db_session.add(ErrorLog(feature_flag_id=feature_flag.id, error_type="crash", message="x", timestamp=now))
    db_session.add(RawMetric(feature_flag_id=feature_flag.id, metric_type="flag_evaluation", count=80, value=1, timestamp=now))
    db_session.commit()
    metrics = SafetyService(db_session).get_error_metrics(db_session, feature_flag.id)
    assert metrics["error_count"] == 4 and metrics["total_evaluations"] == 80
    assert metrics["error_rate"] == pytest.approx(0.05)
```

Twin: an integration test in `backend/tests/integration/api/test_safety_api.py` that creates
the flag and config through the API, posts errors to `/tracking/errors`, loops
`/feature-flags/evaluate/{key}`, then asserts `is_healthy` from `POST
/safety/feature-flags/{id}/check` — the whole Day 18 path with no mocks.

**S4.2** — `/login` → bare (no shell, no gate). `/` → bare. `/docs/sdk/python` → open (shell,
no gate). `/power-calculator` → open (shell, no gate; calls public endpoints).
`/experiments/[id]` → protected (shell + `RequireAuth`). `/admin/safety` → protected (shell +
`RequireAuth`; the page additionally passes `roles` so non-admins see the inline 403).

**S4.3** — A "Lifespan smoke" job: start uvicorn with `ENVIRONMENT=development` against the
service Postgres, wait for `/health/ready`, then assert `GET /scheduler/health` lists all five
scheduler names with a recent run (or `curl /metrics | grep scheduler_ticks_total{name="bandit"`).
It asserts the lifespan _started_ each scheduler and that each completed one locked tick.
Under a minute; it could live inside the existing Docker Smoke job as one more step, which is
cheaper than a new job.

**S4.4**

```yaml
  docs:
    profiles: ["docs"]
    image: nginx:alpine
    volumes:
      - ../experimently-docs:/usr/share/nginx/html:ro
    ports:
      - "${DOCS_HOST_PORT:-3400}:80"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1/README.md"]
      interval: 10s
      timeout: 5s
      retries: 6
    restart: unless-stopped
```

(Serving raw markdown; a real docs site would add a static generator in a build stage, the
way `frontend/Dockerfile` does for `/docs/*`.)

**S4.5** — `scheduler_last_success_timestamp{name="safety"}`: alert when `time() − value >
3 × SAFETY_CHECK_INTERVAL_MINUTES × 60`. Action: check `/scheduler/health` for failed ticks,
the API logs for the exception, and the advisory lock (a stuck holder). False positive to
guard: a replica that only ever _skips_ because another replica holds the lock still
publishes no successes — so alert on `max()` across replicas, not per instance. CloudWatch
`api_5xx_alarm`: action is to look at the request ids in the error log group and the most
recent deploy; false positive to guard: a burst of 5xx from a single misbehaving client
(check `rate_limit_rejections_total` and the client ip first).

---

_You've built it. Turn to a [specialization](./README.md#where-this-fits-in-the-curriculum). 🏗️_

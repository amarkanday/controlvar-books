# Week 4 — Production Engineering

### Days 22–28 · Testing, the Dashboard, CI/CD, Containers, AWS, Observability

[← Week 3](./week-3-hard-parts.md) · [Contents](./README.md)

> Verified against getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased changes, including
> three breaking ones: #747, #769, #770).

Three weeks of _how it works_. This week is _how you know it works, and how it gets to
production_: the test pyramid and the lesson the team learned about mocked seams, the dashboard's
architecture, the checks a pull request must pass, the container images and the two compose
files, the AWS deployment in CDK, and the logs, metrics and alerts that tell you it's healthy.
Day 28 hands you the whole map and asks you to draw it from memory.

> **The one sentence to carry through Week 4.** _A change is real when it has passed the 20
> checks branch protection requires on `main`; when it ships as a signed, immutable image from a
> release tag, through one Deploy workflow that refuses before it changes anything, runs the
> migration task as the only schema writer, and shifts traffic blue/green under 5xx alarms that
> roll it back; and when `/health/ready`, `/metrics` and the scheduler run history say it's
> alive. Every one of those gates exists because something once slipped through without it, and
> every one has been broken on purpose to prove it fires._

| Day | Topic                    | The one thing it's really about                                                      |
| --- | ------------------------ | ------------------------------------------------------------------------------------ |
| 22  | Testing strategy         | The pyramid, the harness, two profiles, and why mocked seams hid the hot-path bugs   |
| 23  | The dashboard            | A thin Next.js client: one fetch wrapper, one auth context, a modules seam           |
| 24  | CI/CD                    | 20 required checks, tamper-tested gates, and one Deploy path for every environment   |
| 25  | Containers & compose     | Two images in two profiles, one entrypoint, and three ways to self-host              |
| 26  | AWS                      | Nine core CDK stacks (twelve with modules), Fargate blue/green, one schema writer    |
| 27  | Observability            | Request IDs, JSON logs, Prometheus, health probes, alerting                          |
| 28  | **Capstone:** the map    | Draw the whole system; explain every arrow                                           |

---

## Day 22 — Testing strategy: the pyramid, the harness, and the hot-path lesson

### What & why

Experimently has more test code than application code. The app's README puts it at **5,400+
backend tests and 640+ dashboard tests**, and the optional modules carry their own suite on top
(the Module Tests job's comment counts 1,629). Each layer answers a different question:

| Layer               | Where                                    | Question it answers                                         | Runs in                                                  |
| ------------------- | ---------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------- |
| Unit                | `backend/tests/unit/`                    | Does this function/service do what its contract says?       | **Unit Tests** (required; `-x`, stops on first failure)  |
| Modules             | `modules/backend/tests/`                 | Do the optional modules behave?                             | **Module Tests** (required)                              |
| Lambda unit         | `backend/lambda/*/`                      | Do the two Lambda handlers and `shared/` behave?            | Inside Unit Tests (240 collected, one pytest per dir)    |
| Integration         | `backend/tests/integration/`             | Do endpoints + services + DB agree end-to-end?              | **integration-tests** (required, every PR and push)      |
| Database transitions | `backend/tests/integration/database/`   | Does a migration survive a profile change or a newer DB?    | integration-tests                                        |
| Smoke               | `backend/tests/smoke/`                   | Is every router wired, every flag change route guarded?     | **Smoke Tests**, and **Base Requirements Only**          |
| Contract            | `tests/sdk-contract/`, `backend/tests/contract/` | Does each SDK hash identically and speak the real API? | **SDK Contract Tests** + **SDK Live Contract** (required) |
| Profile builds      | `scripts/core_build.sh`                  | Does each profile stand alone?                              | **core-build** / **full-build** (required)               |
| Frontend            | `frontend/src/tests/`                    | Do pages, services, hooks, contexts behave?                 | **Frontend Tests** (required; Jest + tsc + build)        |
| Browser             | `frontend/tests/e2e/`                    | Does the whole thing work in a browser?                     | **Browser E2E** on every PR; a11y and visual nightly     |
| Performance         | `backend/tests/performance/`             | Is the rules engine still as fast as it was?                | `performance-tests.yml` (scheduled or by hand)           |

The two Lambda handlers under `backend/lambda/` (`event_processor`, `feature_flag_evaluation`)
are tested, but no stack deploys them (Day 26).

#### The lesson: mocked seams hide broken hot paths

In September 2026, with thousands of tests green, the public tracking API had been broken for
weeks: `/tracking/track` built an `EventCreate` with field names the service didn't accept,
the experiments endpoints returned shapes the schema rejected, the safety service read a table
nobody wrote to, and the bandit scheduler called a method that didn't exist. Every one of
those paths had tests — tests that **mocked the service** the endpoint called, or **mocked the
DynamoDB client** the scheduler called, and asserted on the mock. The tests proved the code
called _something_; they didn't prove the something existed or agreed on shapes.

The response is now the project's testing policy (`docs/development/testing-guide.md`, and the
"Verifying a change" section of `CLAUDE.md`):

1. **Drive hot paths through the public API against a real database** — `TestClient` +
   the `client` fixture + the per-process test database. The `TestAssignEligibility` class in
   `backend/tests/integration/api/test_tracking_api.py` is the model: it creates an experiment,
   calls `/tracking/assign`, and reads the `assignments` table.
2. **Wiring smoke tests** — `backend/tests/smoke/test_wiring.py` enumerates every route in
   the app and asserts it resolves, so an endpoint whose import fails can't ship.
3. **Live contract tests for SDKs** — run the real client against the real API in CI.
4. **A docker smoke job** — build the images, start the compose stack, log in, mint a key,
   assign a user. If the entrypoint, the bootstrap, the seed or nginx is wrong, this fails.

Mocks still have a place: pure logic (the rules engine, statistics), external services (AWS
via `moto`, Slack, SendGrid), and failure injection. The rule is _mock what you don't own,
drive what you do._

#### Two profiles, one suite

One codebase builds two profiles: **core** (`backend/` only) and **full** (adds the optional
modules under `modules/`; `modules-manifest.txt` is the authority on what is a module). The
profile is decided by whether `import modules` succeeds, so the suite has to work both ways:

- Tests that need a module carry the `modules` marker. In a core build they are skipped; if
  `modules/` is present but fails to load, pytest **exits 3** instead of skipping them, because
  a broken module silently skipped looks exactly like a core build.
- `scripts/core_build.sh` copies the tree, deletes `modules/` (for `--profile core`), and runs the
  sequence on the copy: the **core-build** and **full-build** checks.
- **Base Requirements Only** runs the smoke suite in an interpreter that has only
  `backend/requirements.txt`, the environment no developer venv reproduces.
- The import boundary is enforced by import-linter (`make lint`) and
  `backend/tests/smoke/test_core_boundary.py`.

#### Pin the mechanisms that act at a distance

Some properties are non-local: alembic's revision graph (a full checkout has **two heads**, the
core chain and the `modules` branch), what autogenerate would propose, what an older image does
against a newer schema. `backend/tests/integration/database/` asserts them literally:
`test_alembic_branch_revisions.py` (the exact head set), `test_autogenerate_is_empty.py` (an
autogenerate against a freshly bootstrapped database must produce an empty migration),
`test_profile_transitions.py` (a core database met by a full image and the reverse), and
`test_newer_database_rollback.py`. A silent reorder fails loudly with a diff instead of
quietly proposing `op.drop_table` against production.

### How Experimently does it

The harness is the root `conftest.py`. Its decisions shape every DB-backed test:

```python
# 📁 backend/tests/conftest.py
# * APP_ENV=test / TESTING=true select TestSettings (ENVIRONMENT == "test")
#   and the test_experimentation schema.  ENVIRONMENT itself is deliberately
#   NOT exported: tests that construct ProdSettings()/DevSettings() directly
#   must keep their class default, and the settings singleton already resolves
#   APP_ENV=test to the canonical "test".
# * Auth runs exactly as the core profile ships: AUTH_PROVIDER=local and
#   the dev-admin bypass OFF (fail-closed).  ...
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("TESTING", "true")
# A developer shell may export ENVIRONMENT=development (see .env.example);
# ENVIRONMENT wins over APP_ENV, so drop it for the test process.
os.environ.pop("ENVIRONMENT", None)
...
os.environ["POSTGRES_SCHEMA"] = "test_experimentation"

from backend.app.main import app
...
def pytest_collection_modifyitems(config, items):
    """Skip `modules`-marked tests when the modules did not load.
    ...
    """
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
   variables — including `POSTGRES_SCHEMA`, which is _set_, not defaulted — are set at the top of
   the module, before `from backend.app.main import app`.
2. **A database per process**, created with `DROP DATABASE`/`CREATE DATABASE` in autocommit
   mode at session start, so parallel or concurrent runs can't collide.
3. **No truncation between tests.** `TRUNCATE ... CASCADE` deadlocked against async fixture
   overlap; instead every test uses UUIDs and unique suffixes, and the whole database is
   dropped at the next session start.
4. **`expire_on_commit=False`** on the test session, so reading `user.is_superuser` after a
   commit doesn't open a fresh NullPool connection.
5. **The modules seam.** `pytest_collection_modifyitems` skips `modules`-marked tests in a core
   build and exits 3 when the modules are present but broken.

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
— fast, mocked branch coverage next to one real end-to-end path — is the pattern the policy
asks for.

**Every bug fix carries a regression test.** Mark it `@pytest.mark.regression` (the marker is
declared in `pyproject.toml`: "guards a specific past bug") and make sure it fails on the old
code. The required **regression-guard** check fails a pull request labelled `bug` that changes
no test file.

The everyday commands (always in the venv, always with the test env):

```bash
source venv/bin/activate
export APP_ENV=test TESTING=true
python -m pytest backend/tests/unit/ -p no:cov -q                 # the unit suite
python -m pytest backend/tests/integration/ -p no:cov -q          # needs Postgres on localhost:5432
python -m pytest backend/tests/smoke/test_wiring.py -q            # every route resolves
python -m pytest backend/tests/unit/services/test_cuped_service.py -k theta -v   # one thing
python -m pytest -m regression -p no:cov -q                       # the regression tests
make test-modules                                                 # the modules' own suite
cd frontend && npm test                                           # Jest
```

> **Watch Out!** Three harness facts that cost people hours: (1) pytest is configured in one
> place — `[tool.pytest.ini_options]` in `pyproject.toml` (the old `pytest.ini` files are gone)
> — and coverage is deliberately **not** in `addopts` because it slowed every run and silently
> produced no report under some plugin combinations; ask for it explicitly with
> `--cov=backend.app --cov-report=term` (or `make test-coverage`). (2) The unit and integration
> suites run as separate sessions in CI; run them separately locally too. (3)
> `backend/tests/smoke/test_production.py` targets a live server via `SMOKE_TEST_API_URL`; without
> that variable the module is skipped, not failed.

> **Did You Know?** `moto` is pinned at 4.2.14, which means `mock_dynamodb`, `mock_cognitoidp`
> and friends — the `mock_aws` umbrella arrived in moto 5.0. The Cognito integration tests
> (`backend/tests/integration/auth/`) run their own package under `AUTH_PROVIDER=cognito` via
> a package-level conftest and are the only tests that exercise the Cognito branch of
> `get_current_user`.

### Build It

Write the pair. Pick `FeatureFlagService._evaluate_percentage_rollout` and:

1. A **unit** test that stubs nothing (it's pure): 0% → `False`, 100% → `True`, and a user
   whose bucket you compute offline (`int(md5(f"{user}:{flag.key}").hexdigest(), 16) % 100`,
   the flag rollout bucket from Week 3) lands on the right side of 50%.
2. An **integration** test with the `client` fixture: create a flag via `POST
   /feature-flags/`, activate it, set 50%, then `GET /feature-flags/evaluate/{key}` with an
   API key you mint via `POST /api-keys` — assert `enabled` matches your offline bucket and
   `reason == "rollout"`.

Run both, then run the wiring smoke test and read `_iter_http_routes` to see how it copes
with FastAPI 0.141's lazy routers. Finally, tamper-test your own test: change the bucket
arithmetic in a scratch copy of the service and watch the unit test fail. A test you have only
ever seen pass has not yet shown it can fail.

### Key takeaways

- Layers from unit to performance; eleven of the 20 required checks come from the PR QA gate
  alone (Day 24).
- The 2026 lesson: mocked seams hid broken hot paths; drive what you own through the public
  API against a real database, mock what you don't own.
- The harness: env before import, a database per process, no truncation,
  `expire_on_commit=False`, `dependency_overrides` for `get_db` and auth, the modules seam.
- Two profiles: the `modules` marker, `core_build.sh`, Base Requirements Only, the import
  boundary. Non-local mechanisms (alembic heads, autogenerate) are pinned literally.
- Unit tests name their integration twin; every bug fix carries a `regression` test.

### Q&A

**Q. How do I test a scheduler tick?**
Call the tick method directly with a real test session (it opens `SessionLocal()` itself, so
point `DATABASE_URI` at the test DB — the harness does), or drive `run_locked_tick` with a
fake tick as Day 17's Build It did. The lifespan doesn't start schedulers under the test
profile, so `TestClient` never runs one behind your back.

**Q. How do I test a feature-flag permission?**
Access to a flag is by role, not ownership: ADMIN and DEVELOPER may change any flag, ANALYST
and VIEWER none, even one they own. The one decision is
`can_act_on_feature_flag(user, owner_id, action)` in `backend/app/core/permissions.py` (a plain
function: call it directly). Test it with a **non-superuser** of each role — superusers bypass
every check. Create is gated by the async `deps.can_create_feature_flag` (`@pytest.mark.asyncio`,
`await`). `backend/tests/smoke/test_flag_change_routes_guarded.py` pins the exact set of mutating
flag routes, so a new one fails until it is classified.

**Q. Where are the statistics tested for correctness, not just coverage?**
`backend/tests/unit/services/test_*_service.py` for the formulas (against hand-computed
values and `scipy`), `backend/tests/realistic/` for generated data with known effects, and the
`validator` agent in the autonomous QA framework (`/autotest`) for end-to-end checks that
p-values, CUPED variance reduction and bandit allocations are mathematically right.

**Q. What's the coverage?**
Measure it rather than trust a number in a book: `make test-coverage` runs the backend unit and
integration suites with `--cov=backend.app --cov-report=term-missing`. The hot paths
(`endpoints/experiments.py`, `safety_service.py`, `assignment_service.py`, `event_service.py`)
are where integration coverage matters most.

---

## Day 23 — The dashboard: Next.js architecture and the API client

### What & why

The dashboard is a **Next.js 16 / React 19 pages-router app in TypeScript with Tailwind**,
deliberately thin: it has no state library, no GraphQL, no server-side rendering of data. Every
page reads the API through one `fetch` wrapper, holds local state with hooks, and renders. The
reason is the same as the SDKs': the API is the product, and the dashboard is its first client.

Six pieces carry the architecture:

1. **`services/api.ts`** — one `apiFetch<T>()`: base URL, bearer token, JSON, an `ApiError`
   with the FastAPI `detail`, and a 401 → `/login?next=` redirect.
2. **`contexts/AuthContext.tsx`** — token in `localStorage`, user re-derived from `/auth/me`
   on every mount, cross-tab sync via the `storage` event, `hasRole()`.
3. **`_app.tsx` + `utils/routes.ts` + `RequireAuth`** — route classification (`bare`, `open`,
   `protected`) decides whether a page gets the shell and the auth gate.
4. **`services/*.ts`** — one object per resource (`ExperimentsService`, `FeatureFlagsService`,
   `ResultsService`, `AdminService`, …) with typed methods that mirror the API's routes
   one-to-one. Core `frontend/src/services/` holds admin, api, docs, experiments, featureFlags,
   modules and results.
5. **`hooks/useApi.ts`** — `data/loading/error/refetch` around any fetcher, with a mounted
   guard.
6. **The modules seam** — `contexts/ModulesContext.tsx` (`ModulesProvider`) asks the API which
   optional modules it runs, and module pages and services live under `modules/frontend/src`,
   imported through the `@modules/*` alias (`frontend/modules-alias.js`). The workspaces service,
   for example, is `modules/frontend/src/services/workspaces.ts`. A core build type-checks against
   `tsconfig.core.json`, and the Frontend Tests job fails if a core build carries a module route.

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

(Under `next dev` with `NEXT_PUBLIC_API_URL` unset, `apiBase()` falls back to
`http://localhost:8000`; production builds never do.) Its error type turns FastAPI's three
`detail` shapes (string, object, validation list) into one message:

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
/**
 * Routes rendered without the application shell. `/sso/complete` is one so
 * that it is never `protected`: RequireAuth would bounce an anonymous visitor
 * to `/login?next=`, carrying the SSO hand-off fragment along (C2b).
 */
const BARE_ROUTES = new Set<string>(['/login', '/', '/sso/complete']);

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
      <Head>
        ...
      </Head>
      <ModulesProvider>
        <AuthProvider>
          <NotificationProvider>
            {page}
            <ToastContainer />
          </NotificationProvider>
        </AuthProvider>
      </ModulesProvider>
    </ErrorBoundary>
  );
}
```

`ModulesProvider` sits outside `AuthProvider`: which modules the API runs is known before
anyone signs in, so the login page can offer SSO when the SSO module is present. That sign-in
(`modules/frontend/src/components/sso/SsoSignIn.tsx`) is OpenID Connect; the identity provider
hands back to `/sso/complete` (`modules/frontend/src/pages/sso/complete.tsx`), which is why that
route is `bare`. SAML is not available.

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
`Sequential` (the mSPRT trajectory), `Breakdowns` (segment tables) and `shared/`. The live
panel is `components/experiments/LiveResultsPanel.tsx`, driven by
`hooks/useExperimentStream.ts`, a WebSocket hook with auto-reconnect. Browsers cannot set
headers on a WebSocket handshake, but they can offer subprotocols, so the token rides as
`Sec-WebSocket-Protocol: experimently.bearer, <token>` and the backend echoes
`experimently.bearer` on accept. The token never goes in the URL, because URLs end up in access
logs. (A non-browser client may send an ordinary `Authorization: Bearer` header instead.) The
targeting rule builder (`components/targeting/`) writes the _dashboard_ rule shape from Week 3,
Day 16 — `TargetingRuleBuilder → RuleGroupCard → ConditionRow`, with the operator list in
`utils/targeting.ts`.

Testing is Jest + React Testing Library + jsdom, with `global.fetch` mocked per test and
Recharts replaced by a mock (`src/__mocks__/recharts.tsx`, mapped in `jest.config.js`) so
charts render as inspectable stubs. The pages tests (`src/tests/pages/*.test.tsx`) are the
integration layer: render a page, mock the fetches it makes, assert the DOM and the request
bodies. Playwright lives in `frontend/tests/e2e/`: five fail-hard journeys (login, experiment
lifecycle, feature flag, RBAC, admin panel) and an SSO sign-in spec run on **every pull
request** in the required **Browser E2E** job, against a real API and the static export served
the way the nginx image serves it; the SSO steps use a fake OIDC provider, on the same origin
and on a different one. Accessibility (axe) and visual regression run nightly.

> **By the Way.** The container image is a **static export** (`output: 'export'` → `out/`)
> served by nginx-unprivileged on 8080, with `/api/`, `/ws/` and `/health*` proxied to
> `API_UPSTREAM` (default `http://api:8000`). That's why `NEXT_PUBLIC_API_URL` is empty in the
> image — same origin, no CORS — and why dynamic routes like `/experiments/[id]` need nginx
> location blocks, generated at build time from `.next/routes-manifest.json` by
> `frontend/scripts/nginx-routes.mjs`. On AWS the load balancer, not nginx, sends `/api/*`,
> `/health`, `/health/*` and `/metrics` to the API, and the dashboard image's `API_UPSTREAM` is
> deliberately `http://127.0.0.1:1`, so a stray `/api` request gets a 502 instead of being
> forwarded somewhere. The build context is the repository root because the full-profile
> build copies `modules/frontend` in. The dashboard's `/docs` page links to the published
> documentation site (`NEXT_PUBLIC_DOCS_URL`, empty meaning the published site); it renders
> nothing from `docs/` itself.

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
- `routeKind` → `bare | open | protected`; `/sso/complete` is bare; `RequireAuth` is UX, the
  API enforces.
- Services mirror the API one-to-one; pages are thin; `useApi` wraps fetchers; module code comes
  in through `ModulesProvider` and the `@modules/*` alias.
- Results = Recharts components over `/results/{id}` + a WebSocket live panel authenticated by
  subprotocol; targeting = the dashboard rule shape.
- Jest/RTL with mocked `fetch` and mocked Recharts; Playwright journeys on every PR; static
  export behind nginx in the image.

### Q&A

**Q. Why the pages router and not the app router?**
The dashboard is a static export (no server components), and the pages tests are simpler with
`router.pathname`. The move to Next 16 and React 19 landed and kept the pages router, by choice.

**Q. How does the dashboard know the user's role for the nav?**
`AuthContext.hasRole()` over the `UserMe` returned by `/auth/me` (`role`, `is_superuser`).
The nav items carry `roles: ['ADMIN', 'DEVELOPER']`; `RequireAuth` accepts a `roles` prop for
page-level gating.

**Q. Where's the OpenAPI type generation?**
There isn't one — types in `src/types/*.ts` are hand-written to match the schemas.
`npm run openapi:dump` runs `python -m backend.scripts.dump_openapi --profile full` (no live
server) into `src/tests/fixtures/openapi.json`, so a URL guard test can catch a service path
that no longer exists on the server; `make openapi` regenerates that file and the core and full
snapshots under `docs/api/`.

**Q. Why `next dev -p 3100`?**
So the dev server doesn't collide with the compose stack's dashboard on 3000; both are in
the API's CORS defaults.

---

<a id="day-24--cicd-the-pr-qa-gate-the-release-gate-and-the-security-scan"></a>

## Day 24 — CI/CD: 20 required checks, tamper-tested gates, one Deploy path

### What & why

Branch protection on `main` requires **20 status checks** (read with
`gh api repos/getexperimently/experimently/branches/main/protection/required_status_checks`;
`strict` is off). They come from about a dozen workflows, and each exists because of a specific
class of regression:

| Check                                        | Workflow                    | Catches                                                                 |
| -------------------------------------------- | --------------------------- | ----------------------------------------------------------------------- |
| Unit Tests                                   | `pr-qa-gate.yml`            | Logic regressions; then the Lambda handlers' own suites                 |
| Module Tests                                 | `pr-qa-gate.yml`            | A broken optional module, in minutes rather than an hour                |
| Base Requirements Only                       | `pr-qa-gate.yml`            | A module import that only works because a developer venv has everything |
| Smoke Tests                                  | `pr-qa-gate.yml`            | A router whose import fails, a route that no longer resolves            |
| Frontend Tests                               | `pr-qa-gate.yml`            | Jest, `tsc`, the build, the core tsconfig, a module route in a core build |
| SDK Contract Tests                           | `pr-qa-gate.yml`            | An SDK whose own hash drifts from the golden vectors                    |
| core-build, full-build                       | `pr-qa-gate.yml`            | A profile that does not stand alone (`scripts/core_build.sh`)           |
| SDK Live Contract / sdk-live-contract (core) | `pr-qa-gate.yml` → `_platform.yml` | An SDK calling an endpoint the backend doesn't serve             |
| Browser E2E                                  | `pr-qa-gate.yml`            | A dashboard journey or the SSO sign-in broken in a real browser         |
| Docker Smoke                                 | `pr-qa-gate.yml`            | A broken image, entrypoint, bootstrap, seed, nginx proxy or start-up check |
| integration-tests                            | `integration-tests.yml`     | Endpoints, services and the database disagreeing                        |
| Release Gate Summary                         | `release-gate.yml`          | Backend (unit + smoke), frontend, Bandit, gitleaks                      |
| Security Scan Summary                        | `security-scan.yml`         | pip-audit/Bandit, npm audit, Semgrep, secrets, containers, dependency review |
| lint                                         | `lint.yml`                  | ruff, import-linter, reuse, the requirements locks, eslint, tsc, hadolint, actionlint |
| CDK Stack Tests (Python)                     | `infrastructure-tests.yml`  | A stack that will not synthesise, or one whose wiring a test pins       |
| SDK Unit Tests                               | `sdk-unit-tests.yml`        | An SDK's own unit tests                                                 |
| regression-guard                             | `regression-guard.yml`      | A pull request labelled `bug` that changes no test                      |
| DCO                                          | `dco.yml`                   | A commit without a `Signed-off-by` line                                 |
| Leak Guard                                   | `leak-guard.yml`            | A secret or a false claim about to be published                         |

`conventional-title` runs on every pull request too, but it is **not** required: a prose title
can merge, and then it silently drops out of the changelog (see "Releasing" below). Neither is
Doc Examples (`doc-examples.yml`), which runs the examples in the app's own documentation. CodeQL
has a workflow of its own.

**`pr-qa-gate.yml`** has eleven jobs: _Detect docs-only change_, Unit Tests, Module Tests, Base
Requirements Only, Smoke Tests, Frontend Tests, SDK Contract Tests, the `profile-build` matrix
(core-build and full-build), SDK Live Contract, Browser E2E and Docker Smoke. Its concurrency
group is keyed on the pull request **number**, because two forks' `patch-1` branches share a
branch name and used to cancel each other's runs.

The first job runs `scripts/classify_changes.py`. On a documentation-only change the heavy jobs
are **skipped** rather than not triggered: branch protection needs all 20 checks to report, and
a skipped check is accepted where a missing one blocks the pull request for ever. Two checks are
named by an expanded or called name (`core-build`/`full-build`, and the live contract's
`SDK Live Contract / sdk-live-contract (core)`), and a job skipped before it expands never
reports under that name, so those two always run and condition every step instead. A
classifier that crashes runs everything.

The release gate (`release-gate.yml`) also runs on every push to `main`, so the commit a release
tag points at carries a Release Gate result, which the Deploy workflow checks. Its backend gate
is `./scripts/run-backend-tests.sh release` against `docker-compose.test.yml`; Bandit scans
`backend/app` and, in a full checkout, `modules/backend/app`; gitleaks runs as the binary, not
the GitHub Action, which needs a licence for an organisation.

#### How a gate is trusted

The most frequent defect in this repository is not broken code but a check that passes for
the wrong reason: a `find` matching a directory instead of the file it guarded, a CI input that
selected nothing, a skipped test that looks exactly like a passing one. So the rule is: **when
you add or change a gate, break the thing it guards and watch it fail.** A gate you have only
ever seen pass is not a gate. You can see the rule in the workflows themselves:
`scripts/check_junit_skips.py` fails the Unit Tests job if a workflow or statistical-gate test
was _skipped_, and `hash_contract.py`'s own self-tests prove the comparison rejects a wrong
answer before any SDK is compared.

#### Why the live contract and docker smoke are _required_

Because they are the two jobs that would have caught the September 2026 breakage. A unit
suite can be green with a broken `/tracking/track`; the live contract runs a real client
against it. A Dockerfile can build with a broken entrypoint; docker smoke starts it and logs
in. Both are slower than unit tests (the live contract installs several toolchains), which is
why they run as separate jobs in parallel rather than serially.

### How Experimently does it

The live contract is a caller of the reusable workflow `_platform.yml`, which owns the services,
toolchains and the boot sequence:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
  sdk-live-contract:
    needs: changes
    if: ${{ !cancelled() }}
    name: SDK Live Contract
    uses: ./.github/workflows/_platform.yml
    with:
      skip: ${{ needs.changes.outputs.docs_only == 'true' }}
      profile: core
      seed: sdk-contract
      suite: sdk-live-contract
      sdks: >-
        python openfeature-python js openfeature edge react react-native
        go java android ruby php dotnet
```

That is 13 of the 16 SDKs (Android through its JVM variant). Flutter, Elixir and iOS have no
toolchain on the runner and are covered by their unit tests (the required SDK Unit Tests check,
plus a nightly heavy tier).

The SDK Contract Tests job no longer uses reference ports: `tests/sdk-contract/hash_contract.py`
runs **each SDK's own hash function** (python, js, edge, react-native, go) against
`tests/sdk-contract/golden-vectors.json`, after its self-tests.

And the docker smoke's assertions — note that it checks the **auth gate** (401 without
credentials) as a first-class property of a healthy stack:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
      - name: Start the stack
        run: docker compose -f docker-compose.yml up -d --wait --no-build postgres redis api frontend
      ...
      - name: API readiness and auth gate
        run: |
          curl -sf localhost:8000/health/ready -o ready.json
          cat ready.json
          grep -q '"status":"healthy"' ready.json
          curl -sf localhost:8000/health/live >/dev/null
          # /api/v1/experiments 307-redirects to the trailing-slash route; follow it.
          code=$(curl -sL -o /dev/null -w '%{http_code}' localhost:8000/api/v1/experiments)
          test "$code" = "401" || { echo "expected 401 without credentials, got $code"; exit 1; }
      - name: Login, create an API key, assign a user (sdk_contract_ab)
        ...
```

Before it starts the stack, Docker Smoke also builds the full image, checks that the module
dependencies are in the full image and only there, boots the full image with production
settings, checks that production start-up lists **every** missing setting at once, checks the
image labels a release asserts, and checks the "created the first administrator" log line.

#### Releasing

The pull request title is the release note. `main` is squash-merged, so the title becomes the
commit subject that release-please parses: it must be a conventional commit,
`type(scope)!: description`, with `type` one of `feat fix perf deps docs build ci refactor test
chore revert style`. A breaking change takes `!` and a `BREAKING CHANGE:` footer; before 1.0 it
bumps the **minor** version (`bump-minor-pre-major`). Release-please opens a release pull request,
and when that merges it calls `release.yml` itself (as a reusable workflow), which builds,
signs and publishes the images and attaches the Helm chart to the GitHub release. Nobody
needs to dispatch `release.yml` by hand, and doing so rebuilds and re-points the release's image tags; it is dispatched only to
recover a failed run, after `git ls-remote --tags origin vX.Y.Z` shows the tag. The documentation
site is deployed separately, by dispatching `docs.yml` on the tag.

#### The Deploy workflow

Nothing deploys on a push. **`deploy.yml`** ("Deploy") is the one path for every environment —
staging and production are the same workflow with a different input — dispatched by hand with
a release tag:

```yaml
# 📁 .github/workflows/deploy.yml
on:
  workflow_dispatch:
    inputs:
      environment:
        ...
        options: [staging, prod]
      version:
        description: 'Release tag to deploy, e.g. v1.2.3 (branch names and commit SHAs are refused)'
        ...
      profile:
        ...
        default: full
        options: [full, core]
      accept_unsigned_audit_log:
        ...
      override_alarms:
        ...
```

Each environment is its own AWS account. The deploy job assumes that environment's role through
GitHub's OIDC token (no long-lived AWS key is stored) and refuses if the role turns out to be in
a different account. Its shape, in two jobs:

```
preflight  (no AWS credential)
  dispatch ref is main · profile acknowledged (core needs accept_unsigned_audit_log)
  · version is a release tag on main · the Release Gate passed on that commit
deploy     (the one environment-bound job: one approval, before anything in AWS changes)
  refuse:  wrong AWS account · stacks not deployed · profile/secrets mismatch
           · a referenced secret missing · ECR repositories missing
           · an earlier deployment still active · an API 5xx alarm in ALARM
  image:   build and push once to ECR, resolve the digest (the dashboard's too)
  refuse:  an API alarm firing (checked again, before the migration)
  migrate: snapshot the database · run the migration task (the only schema writer)
  API:     register the task definition · refuse a revision that would migrate on start
           · CodeDeploy blue/green: 10% canary for 5 minutes, then the rest, under the
             5xx alarms · smoke test through the public URL
  web:     dashboard rolling deploy · smoke test through the public URL
  recover: named branches for an unapproved shift, an alarm rollback, a half-done deploy
  report:  run summary · notify
```

The canary is 10% for 5 minutes at the reference commit; the project has decided to lengthen it
to 15 minutes before production. What judges the canary is the two API 5xx alarms (one per
target group), not a health check: while either is in ALARM, CodeDeploy stops the deployment and
rolls it back by itself. A release that answers wrongly with a 2xx still reaches 100%, which is
what the smoke tests are for. The old task set is kept for an hour after the shift. A
`override_alarms` input is the break-glass for fixing forward while an alarm fires; that one
deployment is then watched by nothing, and the run name says so.

**`rollback.yml`** ("Rollback") puts a previous released API revision back through CodeDeploy
(and, if you name one, a previous dashboard revision through its rolling deploy). The deploy's
run summary prints the line to paste. It refuses when there is nothing to roll back, and when the
deployment in flight is CodeDeploy's own rollback. An older revision can serve the newer schema
because **API tasks never migrate** (`RUN_MIGRATIONS=false`; Day 25 and Day 26) and every
migration is backward-compatible (expand, then contract). The forward deploy and the alarm
rollback have run on staging; the Rollback workflow has not yet completed a real run end to end,
so treat the first rollback in an environment as a rehearsal and do it in staging first. The
procedure around it is `docs/deployment/rollback-runbook.md`. A third workflow, `db-migrate.yml`
("Database Migration"), runs the migration task on its own.

> **Watch Out!** Three CI facts that look like flakes and aren't. The SDK contract job
> installs only `pytest` and passes `-o addopts=""` defensively, so the job does not depend on
> what the root pytest configuration adds. `curl … | grep -q` under `set -o pipefail` fails for
> any response larger than the pipe buffer (curl exits 23 on SIGPIPE) — the docker-smoke job
> fetches to a file first for exactly that reason. And the gitleaks **GitHub Action** needs a
> paid licence on an organisation and `pull-requests: read` to list a PR's commits; the gates run
> the gitleaks **binary** instead, which needs neither.

### Build It

Run the gate locally, job by job, before you open a PR:

```bash
make test                                        # backend unit+integration+smoke, modules, frontend tests+tsc+build
make test-sdk                                    # SDK hash self-tests, then each SDK's own hash vs the golden vectors
python backend/scripts/seed_sdk_contract.py && python tests/sdk-contract/live/run_live_contract.py --sdk python --sdk js --strict
docker compose up -d --build --wait postgres redis api frontend && curl -sf localhost:8000/health/ready
make lint                                        # everything the lint job runs
make core-build                                  # the core profile, standalone
bandit -r backend/app modules/backend/app -ll
```

Then tamper-test one gate: in a scratch branch, change one value in
`tests/sdk-contract/golden-vectors.json`, run `make test-sdk`, and watch it fail. Finally, read
`.github/workflows/nightly-qa.yml` and list what runs nightly that _doesn't_ run on PRs, and why
(hint: time and browsers).

### Key takeaways

- 20 required checks: eleven from the PR QA gate, plus integration-tests, the release gate and
  security scan summaries, lint, CDK Stack Tests, SDK Unit Tests, regression-guard, DCO and
  Leak Guard. `conventional-title` reports but is not required.
- Docs-only changes skip the heavy jobs, but every required check still reports.
- Gates are trusted only after they have been seen to fail on a planted defect.
- A release is a conventional-commit title → release-please → `release.yml`, called by the
  release itself.
- Deploy: preflight with no credentials → one approval → refusals → image by digest → snapshot →
  migration task → API revision that does not migrate → blue/green canary under 5xx alarms →
  smoke → dashboard → notify. Rollback puts a previous API revision back through CodeDeploy.

### Q&A

**Q. Why is `-x` on the unit job?**
Fail fast: with thousands of tests, stopping at the first failure gives feedback minutes sooner
and keeps the log readable. The nightly run doesn't use `-x`, so you get the full failure list
there.

**Q. The team's working agreements say every PR needs a GitHub issue and a `/review`. Where's
that enforced?**
The issue and the review are convention, not CI: each work item gets an issue, the PR says
`Closes #N`, and `/code-review` runs before merge. CI does enforce the sign-off on every commit
(`DCO`), a test change on any PR labelled `bug` (`regression-guard`), and the secret and
false-claim scan (`Leak Guard`, `scripts/leak_guard.py`, on every push and pull request).
`conventional-title` checks the title but is not required.

**Q. What if a toolchain is missing on the CI runner?**
The live runner reports `SKIP` per SDK unless `--strict`; the CI job runs the 13 SDKs whose
toolchain the runner has (Android through the JVM). Flutter, Elixir and iOS are covered by
their unit tests in the required SDK Unit Tests check.

**Q. How do I run the Cognito tests locally?**
`python -m pytest backend/tests/integration/auth/ -p no:cov -q` — the package's conftest
selects the Cognito provider and `moto` mocks the user pool. No AWS credentials needed.

---

## Day 25 — Containers, the compose stacks, and the Helm chart

### What & why

Two images, each in two profiles, one entrypoint, and three ways to run them yourself. The
design goal: `docker compose up -d --wait` on a laptop with nothing but Docker, and you can log
in — and the same images, unchanged, in production.

- **`experimently-api`** (`backend/Dockerfile`): a multi-stage Python 3.11 image — the builder
  installs the runtime requirement set into a venv; the runtime copies the venv and `backend/`,
  runs as a non-root user, `HEALTHCHECK GET /health/live`, `ENTRYPOINT
  backend/docker-entrypoint.sh`. Two targets: **`core`** (the default, `backend/` only) and
  **`full`** (adds `modules/` and the module-only dependencies from `modules/requirements.lock`).
- **`experimently-web`** (`frontend/Dockerfile`): builds the Next.js static export, generates
  nginx location blocks for dynamic routes, and serves with nginx-unprivileged on 8080,
  proxying `/api/`, `/ws/` and `/health*` to the API. It too builds `core` or `full`
  (`EXPERIMENTLY_PROFILE`; full copies `modules/frontend`).

Releases publish all four to GitHub Container Registry, public and signed:
`ghcr.io/getexperimently/experimently:{core,full}-X.Y.Z` and
`ghcr.io/getexperimently/experimently-web:{core,full}-X.Y.Z` (plus floating `:core`/`:full` for
final releases). Each is signed with cosign keyless and carries an SPDX SBOM attestation; images
are amd64 only, and a secret scan runs on each before it is pushed.

### How Experimently does it

The image's contract is in the Dockerfile header:

```dockerfile
# 📁 backend/Dockerfile
#   docker build -f backend/Dockerfile -t experimently-api:core .
#   docker build -f backend/Dockerfile --target full -t experimently-api:full .
#
# Targets:
#   core  (default) the core profile — backend/ only
#   full  the full profile — backend/ plus modules/ and the module-only
#         dependencies (modules/requirements.lock). Needs a full checkout: on
#         a core one, which has no modules/ directory, it produces an image
#         with an empty /app/modules that refuses to start outside development.
```

The literal `CMD` is `uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`; the entrypoint
appends `--workers ${WEB_CONCURRENCY:-1}` and the proxy flags. Its contract is in its header:

```bash
# 📁 backend/docker-entrypoint.sh
#   0. python -m backend.app.core.preflight: the settings check. Every problem
#      it finds is printed in one list and the container exits 78 (EX_CONFIG)
#      before anything else runs. It always requires ENVIRONMENT (or the
#      legacy APP_ENV), and refuses `test`, which is the test runner's; in
#      staging/production it also requires the secrets,
#      PUBLIC_BASE_URL and POSTGRES_SERVER, and refuses DATABASE_URL/_URI.
#   1. Wait for PostgreSQL (POSTGRES_SERVER/POSTGRES_PORT) to accept connections.
#   2. RUN_MIGRATIONS=true (default)  ->  python -m backend.app.db.bootstrap
#      (creates the schema on a fresh database, `alembic upgrade heads` otherwise).
#   3. SEED=<comma list of demo,shoplab,streampulse,sdk-contract>  ->  run the
#      matching backend/scripts/seed_*.py once. Completed seeds are recorded in
#      the `seed_markers` table (backend/scripts/seed_markers.py) so a restart
#      of the container does not re-seed. SEED_FORCE=true re-runs them.
#      demo, shoplab and streampulse are development-only: they are refused,
#      before step 1, unless ENVIRONMENT is development or test.
#   4. exec "$@" — by default uvicorn; `--workers ${WEB_CONCURRENCY:-1}` and the
#      proxy flags are appended when the command is uvicorn and the caller did
#      not pass them explicitly.
#
# Steps 1-3 can be skipped (step 0 cannot): RUN_MIGRATIONS=false, SEED= (empty),
# WAIT_FOR_DB=false. The script runs with `set -euo pipefail`: a failed
# bootstrap or seed stops the container instead of starting a half-initialised
# API.
```

**Who runs step 2** depends on where the image runs. With compose there is one API container
and it migrates on start. On AWS the API task sets `RUN_MIGRATIONS=false` and `SEED=` empty,
and the Deploy workflow's migration task is the only thing that writes the schema: a bootstrap
refuses a database a newer release migrated, so an older revision started by a replacement, a
scale-out or a rollback would otherwise exit instead of serving. In the Helm chart the bootstrap
runs in an init container, not in the serving container.

Two more details are worth knowing before they bite. The **OpenSSL probe** right after the
preflight: on some
arm64 VMs (Docker Desktop on Apple M4) the `cryptography` wheel's OpenSSL selects CPU
features the guest can't execute and dies with SIGILL; the entrypoint probes once and sets
`OPENSSL_armcap=0` if so. And **`set -euo pipefail`**: a failed seed stops the container —
deliberately — so `docker compose up --wait` fails loudly instead of leaving you with an API
that has no experiments.

The development compose file's header is the operator's cheat sheet:

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

and the `api` service shows every runtime knob with its default:

```yaml
# 📁 docker-compose.yml
  api:
    build:
      context: .
      dockerfile: backend/Dockerfile
      target: ${EXPERIMENTLY_PROFILE:-core}
    image: *api-image
    environment:
      ENVIRONMENT: ${ENVIRONMENT:-development}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      LOG_FORMAT: ${LOG_FORMAT:-console}          # json | console
      WEB_CONCURRENCY: ${WEB_CONCURRENCY:-1}       # 1 worker per container; scale containers instead
      AUTH_PROVIDER: ${AUTH_PROVIDER:-local}       # local | cognito
      DEV_AUTH_BYPASS: ${DEV_AUTH_BYPASS:-false}   # never true outside development/test
      SECRET_KEY: ${SECRET_KEY:-dev-only-change-me-9f1c7b2e4a6d8e0f1a2b3c4d5e6f7a8b}
      FIRST_SUPERUSER: ${FIRST_SUPERUSER:-admin@demo.com}
      FIRST_SUPERUSER_PASSWORD: ${FIRST_SUPERUSER_PASSWORD:-Demo1234!}
      POSTGRES_SERVER: postgres
      ...
      POSTGRES_SCHEMA: ${POSTGRES_SCHEMA:-experimentation}
      RUN_MIGRATIONS: ${RUN_MIGRATIONS:-true}
      SEED: ${SEED-demo}                    # an explicit empty SEED= applies none
      SEED_FORCE: ${SEED_FORCE:-false}
      REDIS_HOST: redis
      ...
      REDIS_REQUIRED: ${REDIS_REQUIRED:-false}
      CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000,http://localhost:3100,http://localhost:3200,http://localhost:3300}
      METRICS_TOKEN: ${METRICS_TOKEN:-}
    ...
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

The image tag follows the profile (`*api-image` is
`experimently-api:${EXPERIMENTLY_IMAGE_TAG:-${EXPERIMENTLY_PROFILE:-core}}`). Note the
healthcheck distinction: the _image_ checks liveness (process up), the _compose service_ checks
readiness (database answers) with a five-minute `start_period` so first-run seeding doesn't
count as unhealthy. The demo profile's simulators read the API keys the seeds write to a shared
`demo_keys` volume, and the demo images bake fixed keys (`eptk_demo_shoplab_…`) so the
storefronts work without a manual step.

#### Three ways to self-host

1. **`docker-compose.yml`** at the root: the development stack above, built from a checkout,
   with demo seeds and profiles.
2. **`deploy/compose/compose.yml`**: the production compose file. It runs the **published**
   images at a literal version, never builds, ships no demo services or seeded demo users, and
   listens on the host only for the dashboard (put your TLS reverse proxy in front).
   `./generate-env.sh` writes a `.env` with generated secrets once; `SECRET_KEY`,
   `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD`, `POSTGRES_PASSWORD` and `PUBLIC_BASE_URL` are
   required, and `AUDIT_HMAC_KEY` too on the full profile. CI tests it (`compose-production.yml`).
3. **The Helm chart**, `charts/experimently` (bootstrap in an init container; tested by
   `chart.yml` and `chart-kind.yml`). Today the chart is a `.tgz` release asset only;
   the project has decided it will ALSO be an OCI package at
   `oci://ghcr.io/getexperimently/charts/experimently`; the first push and making it public are
   founder-only and not done.

The fourth path, AWS, is tomorrow.

> **Did You Know?** The dashboard proxy means the browser never sees the API's origin:
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
docker compose down -v && SEED=demo,nope docker compose up -d --wait; echo "exit=$?"; docker compose logs api > api.log; tail -5 api.log

# the entrypoint with production settings it does not have: step 0 refuses (exit 78)
docker compose run --rm -e ENVIRONMENT=production -e SEED= api true; echo "exit=$?"
# and a demo seed outside development: refused before anything else runs (exit 78)
docker compose run --rm -e ENVIRONMENT=production api true; echo "exit=$?"
```

The first of the last two runs prints the preflight's list of every problem it found, in one go,
instead of one refusal per restart; the second never reaches the preflight, because the demo
seed refusal comes first. Then inspect the layers: `docker history experimently-api:core`, and
confirm the runtime image has no compiler (`docker run --rm --entrypoint sh experimently-api:core
-c 'command -v gcc || echo none'`; `--entrypoint` skips the preflight, which would otherwise
refuse a container with no `ENVIRONMENT`).

### Key takeaways

- Two images, each `core` or `full`: API (multi-stage, non-root, liveness healthcheck,
  entrypoint) and web (static export + nginx proxy). Releases publish all four, signed, with SBOM
  attestations, to public GHCR.
- The entrypoint runs the preflight (exit 78 with every problem listed), waits for Postgres,
  bootstraps (`upgrade heads`), seeds once (demo seeds development-only), then execs uvicorn —
  and stops the container if any step fails.
- Only one thing writes the schema: the compose API container, the Helm init container, or on AWS
  the migration task. AWS API tasks run with `RUN_MIGRATIONS=false`.
- Development compose has profiles `demo`, `tools`, `aws`; production compose runs published
  images; the Helm chart is a `.tgz` release asset today.

### Q&A

**Q. How do I run the full profile?**
From a full checkout (one with `modules/`): `EXPERIMENTLY_PROFILE=full docker compose build`
builds the full API and dashboard images, and `EXPERIMENTLY_PROFILE=full docker compose up -d
--wait` runs them. `GET /api/v1/modules` and `/health/ready`'s `profile` report what you got.

**Q. Where do I set production secrets for compose?**
Don't use the development file for production. Use `deploy/compose/`: run `./generate-env.sh`
once, review the `.env` it writes, and `docker compose up -d --wait`. Compose refuses to start
while a required setting is empty, and the API's preflight refuses production defaults
(Week 2, Day 9).

**Q. Can I point the containerised API at LocalStack?**
`docker compose --profile aws up -d` and `AWS_ENDPOINT_URL=http://localstack:4566` on the API
service; DynamoDB counters, Kinesis and S3 paths then talk to LocalStack.

---

## Day 26 — AWS: the CDK stacks, ECS Fargate, CodeDeploy, the migration task

### What & why

The AWS deployment is defined in **CDK (Python)** under `infrastructure/cdk/`: one `app.py`
that instantiates **nine stacks** per environment (`dev`, `staging`, `prod`, `demo`), plus
**three** from the modules in a full checkout, with explicit dependencies. Stacks are named
`experimentation-<name>-<env>`. There is no API Gateway.

| Stack                 | Provides                                                                                     |
| --------------------- | -------------------------------------------------------------------------------------------- |
| `auth`                | Cognito user pool (self-sign-up disabled)                                                    |
| `vpc`                 | VPC, subnets, security groups; two NAT gateways in prod, one elsewhere                       |
| `dynamodb`            | DynamoDB tables; the assignments table is used by nothing today (the table that matters is the counters module's) |
| `database`            | Aurora PostgreSQL: writer plus reader in prod, a single writer elsewhere; generated credentials in Secrets Manager; backups kept 35 days in prod and staging, 1 day elsewhere |
| `redis`               | ElastiCache Redis (TLS)                                                                      |
| `compute`             | The ECS cluster and its security group, and one placeholder function (below)                 |
| `monitoring`          | CloudWatch dashboards, the alarm topic, `AuroraHighCPU` and per-node `RedisHighCPU-NNN`      |
| `fargate`             | ALB (HTTPS + redirect) with path routing, the API as a blue/green ECS service under CodeDeploy, the **dashboard as its own ECS service**, auto-scaling, the API's 5xx, healthy-host and ERROR-log alarms |
| `migrations`          | The one-shot Fargate task definition that runs `python -m backend.app.db.bootstrap`          |
| `dynamodb-counters` (module) | The real-time counters table (atomic ADD) the API task is given                       |
| `analytics` (module)  | Kinesis stream → Firehose → S3 data lake; OpenSearch                                         |
| `glue-etl` (module)   | Glue jobs, crawler, Athena over the data lake                                                |

The module stacks live under `modules/infrastructure/cdk/stacks/`; `app.py` loads them when that
directory exists, and `EXPERIMENTLY_PROFILE` (`core` or `full`) overrides the choice in either
direction.

**Lambda.** No stack deploys a handler from `backend/lambda/`; the assignment Lambda was removed
(#606). The stacks do deploy placeholder or inline functions: `DatabaseAccessLambda` in the
compute stack in every environment, and in a full checkout `AnalyticsLambda` (placeholder) and
`ETLTriggerLambda` (a real inline function). The hot paths — assignment, flag evaluation,
tracking — are served by the API on Fargate.

One ALB, one public origin: the HTTPS listener sends `/api/*`, `/health`, `/health/*` and
`/metrics` to the API's live target group and everything else to the dashboard service, which
deploys by an ordinary ECS rolling deployment with the circuit breaker on.

The account and region come **only** from the environment (`CDK_DEFAULT_ACCOUNT` /
`AWS_ACCOUNT_ID`), never hard-coded. The API runs 3 tasks in prod, 2 in staging and dev, 1 in
demo; staging gets one small Redis node.

### How Experimently does it

The app wires stacks with explicit dependencies so `cdk deploy --all` orders them:

```python
# 📁 infrastructure/cdk/app.py
VALID_ENVIRONMENTS = ["dev", "staging", "prod", "demo"]
env_name = os.environ.get("ENVIRONMENT", "dev")
...
API_DESIRED_COUNT = {"dev": 2, "staging": 2, "prod": 3, "demo": 1}
DASHBOARD_DESIRED_COUNT = {"dev": 1, "staging": 1, "prod": 2, "demo": 1}

account = os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")
region = os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-west-2")
if not account:
    raise SystemExit(
        "AWS account id not set: export AWS_ACCOUNT_ID (or let the CDK CLI set "
        "CDK_DEFAULT_ACCOUNT from your credentials) before synthesizing."
    )
env = Environment(account=account, region=region)

secret_arns = resolve_secret_arns(
    os.environ,
    env_name=env_name,
    account=account,
    region=region,
    include_modules=ENABLE_MODULE_STACKS,
)
...
fargate_stack = FargateServiceStack(
    app,
    f"experimentation-fargate-{env_name}",
    vpc=vpc_stack.vpc,
    ecs_cluster=compute_stack.ecs_cluster,
    ecs_security_group=compute_stack.ecs_security_group,
    env_name=env_name,
    certificate_arn=certificate_arn,
    public_base_url=public_base_url,
    include_modules=ENABLE_MODULE_STACKS,
    secret_arns=secret_arns,
    api_desired_count=API_DESIRED_COUNT[env_name],
    dashboard_desired_count=DASHBOARD_DESIRED_COUNT[env_name],
    db_host=database_stack.writer_host,
    db_credentials=database_stack.db_credentials,
    db_security_group=database_stack.rds_security_group,
    redis_host=redis_stack.primary_host,
    redis_port=redis_stack.primary_port,
    alarm_topic=monitoring_stack.alerts_topic,
    env=env,
)
fargate_stack.add_dependency(compute_stack)
fargate_stack.add_dependency(monitoring_stack)
fargate_stack.add_dependency(database_stack)
fargate_stack.add_dependency(redis_stack)
...
migration_stack = MigrationTaskStack(
    app,
    f"experimentation-migrations-{env_name}",
    ...
)
migration_stack.add_dependency(fargate_stack)
migration_stack.add_dependency(database_stack)
```

**Secrets are imported by complete ARN.** The operator creates them by hand under
`/<env>/experimentation/<name>` and supplies each complete ARN as a synth input:
`JWT_SECRET_ARN` and `FIRST_SUPERUSER_PASSWORD_SECRET_ARN` always, and
`AUDIT_HMAC_KEY_SECRET_ARN` on the full profile. Importing by name rendered a partial ARN that
Secrets Manager does not resolve, so no task would have started. There is no database password
secret to create: the credentials come from the secret Aurora generated. There is no Redis
secret either: the host and port come from the Redis stack.

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        jwt_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "JwtSecret", secret_arns["JWT_SECRET_ARN"]
        )
        ...
        superuser_secret = secretsmanager.Secret.from_secret_complete_arn(
            self,
            "SuperuserPasswordSecret",
            secret_arns["FIRST_SUPERUSER_PASSWORD_SECRET_ARN"],
        )
        ...
        audit_secret = None
        if include_modules:
            audit_secret = secretsmanager.Secret.from_secret_complete_arn(
                self,
                "AuditHmacSecret",
                secret_arns["AUDIT_HMAC_KEY_SECRET_ARN"],
            )
            required_secrets.append(audit_secret)
```

**The API never writes the schema.** The API task definition pins it:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
                # Consequence: `cdk deploy` alone leaves a new environment
                # without a schema (`/health` answers 200, real requests 500)
                # until the first Deploy runs the migration task.
                # SEED is empty for the same reason: nothing but a migration
                # task writes to this database at start-up.
                # infrastructure/tests/test_api_task_does_not_migrate.py pins
                # both values exactly.
                "RUN_MIGRATIONS": "false",
                "SEED": "",
```

and the Deploy workflow refuses an API revision that would migrate on start
(`scripts/refuse_migrating_api_revision.py`). Read that comment twice: **after `cdk deploy`
alone, `/health` is green and every real request fails** until the first Deploy runs the
migration task.

**Blue/green, judged by alarms.** The deployment group:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
            deployment_config=codedeploy.EcsDeploymentConfig.CANARY_10_PERCENT_5_MINUTES,
            role=codedeploy_role,
            alarms=self.api_5xx_alarms,
            ignore_poll_alarms_failure=False,
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
                deployment_in_alarm=True,
            ),
```

The 5xx alarms (one per target group, blue and green) are what stop and roll back a deployment.
The approval wait is 30 minutes (the Deploy workflow approves the shift itself), and the old task
set is kept for an hour (`termination_wait_time`).

**Monitoring.** Every alarm watches a metric something deployed actually publishes; the old
alarms on an API Gateway, on Lambdas no stack deployed and on log groups nothing wrote to were
removed. The monitoring stack has `AuroraHighCPU`, per-node `RedisHighCPU-NNN` and, with the etl
module, `KinesisProcessingDelay`; the Fargate stack has the API's 5xx, healthy-host and "no
healthy task" alarms and an ERROR-log alarm on `/ecs/experimentation-backend-<env>`. All of them
email through one SNS topic to `ALARM_EMAIL`, which staging and prod require.

**The modules' event pipeline.** In a full checkout the analytics stack runs a Kinesis Data
Stream, a Firehose delivery stream into the S3 data lake and an OpenSearch domain; its consumer
Lambda is a placeholder. The Glue ETL stack crawls the lake and exposes it to Athena; the `/etl`
routes that drive it are a module router, registered by `modules/backend/app/register.py`.

> **Watch Out!** The `compute` stack's `DatabaseAccessLambda` is a placeholder, not a fast path,
> and the handlers under `backend/lambda/` (event processor, flag evaluation) are tested in CI
> but deployed by nothing (`backend/lambda/README.md`: "No stack deploys it"). Don't point an SDK
> at a Lambda URL; the API on Fargate is the only serving path.

### Build It

Synthesize without deploying, and read what one stack becomes. Synth needs the same inputs a
real deployment does — the public URL, a certificate and the complete secret ARNs — and checks
each ARN's shape against the stack's account and region. Placeholders with the right shape are
enough to synthesise:

```bash
cd infrastructure/cdk
pip install -r requirements.txt
export AWS_ACCOUNT_ID=123456789012 ENVIRONMENT=demo EXPERIMENTLY_PROFILE=core
export PUBLIC_BASE_URL=https://demo.example.com
export CERTIFICATE_ARN="arn:aws:acm:us-west-2:${AWS_ACCOUNT_ID}:certificate/00000000-0000-0000-0000-000000000000"
export JWT_SECRET_ARN="arn:aws:secretsmanager:us-west-2:${AWS_ACCOUNT_ID}:secret:/demo/experimentation/jwt-secret-AbC123"
export FIRST_SUPERUSER_PASSWORD_SECRET_ARN="arn:aws:secretsmanager:us-west-2:${AWS_ACCOUNT_ID}:secret:/demo/experimentation/first-superuser-password-XyZ789"
cdk ls                                                  # nine stacks; drop EXPERIMENTLY_PROFILE in a full checkout for twelve
cdk synth experimentation-fargate-demo > fargate.yaml   # then search it for DeploymentGroup and RUN_MIGRATIONS
```

Unset one of the ARNs and run `cdk ls` again: the refusal names the missing variable and how to
read its value. Then run the infrastructure tests the CI job runs (`infrastructure-tests.yml`):

```bash
cd ../.. && python -m pytest infrastructure/tests -q -p no:cov
```

### Key takeaways

- Nine core CDK stacks per environment, three more from the modules, explicit dependencies,
  account/region from the environment only, no API Gateway.
- Runtime: the API on ECS Fargate behind an ALB with CodeDeploy blue/green (10% for 5 minutes),
  rolled back by its 5xx alarms; the dashboard as its own rolling ECS service behind the same
  ALB; Aurora (35-day backups in prod and staging); ElastiCache.
- Secrets by complete ARN as synth inputs; database credentials from Aurora's own secret; no
  Redis secret.
- The migration task is the only schema writer; `cdk deploy` alone leaves no schema.
- No stack deploys a handler from `backend/lambda/`; the placeholder and inline functions are
  `DatabaseAccessLambda`, and with modules `AnalyticsLambda` and `ETLTriggerLambda`.
- Every alarm emails `ALARM_EMAIL` through one SNS topic.

### Q&A

**Q. Do I need AWS to develop?**
No. The compose stack runs without any AWS service; `moto` covers Cognito and DynamoDB in tests;
LocalStack (the `aws` profile) covers DynamoDB/Kinesis/S3 locally when you're working on those
paths.

**Q. Why CDK in Python when the app is Python but the dashboard is TypeScript?**
One language for backend and infrastructure keeps the CDK tests in the same pytest suite and
lets stacks import constants from the app when useful. The CDK jsii runtime still needs Node,
which is why the infrastructure-tests workflow installs it.

**Q. How is an environment deployed?**
Two steps, and the order matters. First the operator creates the ECR repositories and the
secrets by hand and runs `cdk deploy --all` with the synth inputs above — that creates the
infrastructure but no schema. Then the **Deploy** workflow, dispatched with a release tag, builds
the image, runs the migration task and shifts traffic. `docs/self-hosting/cdk.md` in the app
repository is the step-by-step. The `demo` environment is the same stacks with one API task.

**Q. What does blue/green actually do for us?**
CodeDeploy stands up the new task set on the green target group. Once the shift is approved, 10%
of traffic goes to it for 5 minutes, then the rest; the old task set stays an hour. The
automatic rollback is driven by the API's 5xx alarms (on both target groups), not by a health
check, and the alarms need a few minutes of data, so a broken release is often caught after the
shift. A release that is wrong but answers 2xx goes to 100%: that is what the smoke tests and
your own canary judgement are for.

---

## Day 27 — Observability: structured logs, Prometheus, health probes, alerting

### What & why

You can't operate what you can't see. Four signals, each with a specific consumer:

1. **Structured logs** (`structlog`) — JSON in staging/production for CloudWatch Logs
   Insights, coloured console in development; every line carries `request_id`, `path`,
   `method` bound by the request-ID middleware.
2. **Prometheus metrics** on `/metrics` — HTTP latency/count, business counters (assignments,
   events, flag evaluations), cache hits, rate-limit hits/rejections, scheduler ticks and
   last-success timestamps, active experiments.
3. **Health probes** — `/health/live` (process up, touches nothing), `/health/ready` (database
   answers `SELECT 1`; Redis reported, fatal only if `REDIS_REQUIRED`), `/health` (alias for
   the existing ALB wiring).
4. **Alerting** — `NotificationService` → Slack and email for experiment lifecycle events,
   rollout advances, safety rollbacks and scheduler failures; a per-user preference model and a
   delivery log under `/notifications`; CloudWatch alarms emailing `ALARM_EMAIL` for
   infrastructure.

The middleware order matters for all of it. Registered last means outermost, so from the
outside in: CORS → RequestID → Prometheus → SecurityHeaders → TrustedHost → RateLimit →
RelativeSlashRedirect → UnhandledError → the routers. CORS is outermost so that even an error
response carries the CORS headers a dashboard on another origin needs to read it.

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
id maps to exactly the log lines that produced it — including for a crash. A route's unhandled
exception is turned into a plain 500 by `UnhandledErrorMiddleware`, the innermost layer, so the
response passes back out through the request-ID, CORS and security-header layers instead of
leaving from Starlette's outermost error handler with none of them.

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
    The profile the process runs (``core`` or ``full``) is reported as
    ``profile`` and in ``checks.modules``, and never gates readiness. Returns
    200 when ready, 503 otherwise.
"""
```

The distinction is the whole point: a database outage must take the service _out of the load
balancer_ (readiness fails) without making the orchestrator _restart_ every healthy process
(liveness still passes) — restarting wouldn't fix the database and would add a thundering
herd of reconnects when it comes back. The probe is unauthenticated, so what it reports is
scrubbed: the checks report only an error's type, and the one free-text failure (the modules
check) goes through `_scrub`, which keeps one line of at most 200 characters, strips URL
credentials, ARN account ids and named secret assignments, and drops a message that still
mentions a password.

Alerting closes the loop from Week 3: `NotificationService.notify_safety_rollback`,
`notify_experiment_started`, `notify_experiment_ended` and `notify_rollout_advanced` fan out to
`SlackNotifier` (bot token, default channel `#platform-alerts`) and `EmailNotifier` (SendGrid or
SMTP), gated by `SLACK_ENABLED` / `EMAIL_ENABLED` and per-user preferences, with every delivery
attempt logged. Scheduler failures also post to `NOTIFICATION_WEBHOOK_URL`. The Admin →
Notifications page manages preferences and shows the delivery log.

On AWS the infrastructure alarms (Day 26) email `ALARM_EMAIL` through one SNS topic, and the
Deploy workflow reads the same alarms: it refuses to start, and refuses again before migrating,
while an API 5xx alarm is in ALARM (`scripts/refuse_alarm_active.py`), with the `override_alarms`
break-glass for fixing forward.

> **By the Way.** The CloudWatch dashboards in `infrastructure/cloudwatch/` (API performance,
> system health) are JSON deployed by `deploy-dashboards.sh`, and the Logs Insights queries
> they rely on assume the JSON log format — one more reason `LOG_FORMAT` left unset resolves to
> `json` in staging and production and to `console` elsewhere. On AWS the API tasks log to
> `/ecs/experimentation-backend-<env>`, which is where the Fargate stack's ERROR-log alarm
> filters.

### Build It

Correlate a request across all three signals:

```bash
# 1. a request with your own id
curl -s -H "X-Request-ID: week4-demo-001" -H "Authorization: Bearer $TOKEN" $API/experiments/ -o /dev/null -D - > headers.txt; grep -i x-request-id headers.txt
# 2. the log line(s) carrying it (console format locally; JSON with LOG_FORMAT=json)
docker compose logs api > api.log 2>/dev/null; grep week4-demo-001 api.log
# 3. the metric it incremented
curl -s http://localhost:8000/metrics > metrics.txt; grep 'http_requests_total{.*experiments' metrics.txt

# probes behave differently when the database is down
docker compose stop postgres
curl -s -o /dev/null -w "live=%{http_code} " localhost:8000/health/live; curl -s -o /dev/null -w "ready=%{http_code}\n" localhost:8000/health/ready
docker compose start postgres
```

Then read `backend/app/core/health.py::check_redis` and confirm that without
`REDIS_REQUIRED` a stopped Redis is reported but doesn't fail readiness.

### Key takeaways

- Logs: structlog, JSON outside development, `request_id`/`path`/`method` bound per request
  via a `ContextVar`; `X-Request-ID` honoured and echoed, even on an unhandled 500.
- Middleware outer to inner: CORS → RequestID → Prometheus → SecurityHeaders → TrustedHost →
  RateLimit → RelativeSlashRedirect → UnhandledError.
- Metrics: one Prometheus registry; HTTP histogram, business counters, rate-limit and
  scheduler series; `/metrics` token-guarded outside dev/test.
- Probes: live never touches dependencies; ready requires Postgres, reports Redis and the
  profile; `/health` is an alias; what the probe reports is scrubbed.
- Alerts: Slack/email via `NotificationService` with preferences and a delivery log;
  CloudWatch alarms to `ALARM_EMAIL`, which the Deploy workflow also obeys.

### Q&A

**Q. Why not OpenTelemetry tracing?**
Not yet. The request id gives log correlation, Prometheus gives latency distributions, and
the platform's request paths are shallow (one service, one database). Tracing becomes worth
its cost when there is a second service on the request path.

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
  (16 SDKs; Day 20)       │  api.ts → AuthContext → RequireAuth → pages (Day 23) │
  X-API-Key               │  ModulesProvider · Bearer <local JWT | Cognito | SSO>│
        │                 └────────────────────────┬─────────────────────────────┘
        │ assign / evaluate / track                │ experiments / flags / results / admin / ws
        ▼                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│ FASTAPI  main.py (Day 8)                                                          │
│  CORS → RequestID → Prometheus → SecurityHeaders → TrustedHost → RateLimit       │
│  (auth 10/min · SDK 6000/min · 300/min) → RelativeSlashRedirect →                │
│  UnhandledError → api/api.py routers (+ module routers in the full profile)       │
│  deps.py: get_db · get_current_user (bypass → local JWT → Cognito) · get_api_key  │
│           (Day 10) · access by role, owner recorded but not a rule (Day 11)       │
│  Settings: ENVIRONMENT, fail-closed validators, preflight (Day 9)                 │
│                                                                                   │
│  tracking.py ──► AssignmentService (Day 4): sticky row → holdout → MEG →         │
│                  targeting (rules engine + adapter, Day 16) →                     │
│                  bucket_of(user, experiment.key) → row                            │
│              ──► EventService (Day 5): resolve key → variant_id → event row        │
│  feature_flags.py ──► FeatureFlagService (Day 15): inactive? → rule % →          │
│                       md5(user:flag.key) % 100 → RawMetric + ErrorLog             │
│  results.py ──► AnalysisService (Day 6): distinct assigned users, converting      │
│                 users via event_matching → Fisher's exact on proportions → CI →   │
│                 SRM, correction, min n (Welch only in the warehouse module)       │
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
│ schema           │   │ rate-limit counters │   │ 9 core stacks (+3 with modules)  │
│ experimentation  │   │ results cache       │   │ ALB: /api,/health,/metrics → API │
│ (models Day 3/12,│   │ (fallback: memory)  │   │   blue/green under 5xx alarms;   │
│  alembic: two    │   └─────────────────────┘   │   everything else → dashboard    │
│  heads; bootstrap│                             │ migration task (only writer)     │
│  Day 13)         │                             │ Aurora · ElastiCache · Cognito   │
└──────────────────┘                             │ modules: counters table, Kinesis │
                                                 │   → Firehose → S3 → Glue, Search │
                                                 │ CloudWatch alarms → ALARM_EMAIL  │
                                                 └──────────────────────────────────┘
   TESTS (Day 22): unit · modules · lambda · integration · db transitions · smoke ·
   sdk contract · live contract · core/full builds · frontend · browser e2e · perf
   CI (Day 24): 20 required checks · release-please → release.yml · deploy.yml · rollback.yml
   CONTAINERS (Day 25): experimently-api {core,full} (entrypoint: preflight → wait →
                        bootstrap where it is the writer → seed → uvicorn)
                        experimently-web {core,full} (static export + nginx proxy)
                        dev compose · production compose · Helm chart
```

### Build It — the final exercise

Close this book. On a whiteboard or a blank file, draw the map above from memory. Then, for
each of these five arrows, write the file that implements it and the one row or line it
produces:

1. SDK → `/tracking/assign` → `assignments` row.
2. SDK → `/tracking/track` → `events` row with `variant_id`.
3. Dashboard → `/results/{id}` → `p_value` (and the three honesty checks).
4. Client error → `/tracking/errors` → safety tick → `rollout_percentage` change (at 0% the flag
   is also turned off for everyone and its rollout schedule paused) →
   `safety_rollback_records` row.
5. Pull request → 20 required checks → release → signed image → Deploy: migration task →
   blue/green → smoke.

If you can do that without opening the repo, you can build Experimently. If you can't, the
day whose arrow you missed is the day to reread — that's what the table of contents is for.

### Where to go next

- **[Experimentation Statistics](../experimentation-statistics/README.md)** if you'll work on
  results, sequential testing, CUPED, Bayesian or bandits.
- **[SDK Engineering](../sdk-engineering/README.md)** if you'll touch any of the 16 SDKs,
  the contract harness or the demo apps.
- **[Platform Operations](../platform-operations/README.md)** if you'll run it — compose, CDK,
  Fargate, observability, releases.
- The **[usage guide](../guides/usage.md)** to see the product as a product manager does, and
  the **[troubleshooting guide](../guides/troubleshooting.md)** for the day something breaks.

---

## Week 4 Workshop

### Quiz

**1. Name the jobs in the PR QA gate and the regression class each catches.**
Detect docs-only change (skips the heavy jobs on a documentation change, while every required
check still reports), Unit Tests (logic, then the Lambda handler suites), Module Tests (the
optional modules), Base Requirements Only (a module import that needs more than
`backend/requirements.txt`), Smoke Tests (route wiring), Frontend Tests (Jest, tsc, build, the
core build carries no module route), SDK Contract Tests (each SDK's own hash vs the golden
vectors), core-build/full-build (each profile stands alone), SDK Live Contract (13 SDKs vs the
real API), Browser E2E (journeys and SSO in a real browser), Docker Smoke (images, entrypoint,
start-up checks, bootstrap, seed, nginx, auth gate). With integration-tests, the release gate and
security scan summaries, lint, CDK Stack Tests, SDK Unit Tests, regression-guard, DCO and Leak
Guard, that is the 20 required checks.

**2. Why did thousands of green tests not catch a broken `/tracking/track`?**
The endpoint's tests mocked the service and asserted on the mock; the shape mismatch between
endpoint and service was never exercised. Hot paths must be driven through the public API
against a real database.

**3. `/health/live` returns 200 and `/health/ready` returns 503. What's happening and what
should the orchestrator do?**
The process is up but PostgreSQL isn't answering (or Redis, if `REDIS_REQUIRED`). Take the
instance out of the load balancer (readiness) but do not restart it (liveness) — restarting
won't fix the database.

**4. In what order does a deploy run, and why do migrations come where they do?**
Preflight with no AWS credential (main, profile, a release tag on main, the Release Gate passed)
→ one approval → refusals (account, stacks, secrets, ECR, an active deployment, an API alarm
firing) → image built once, by digest → alarms checked again → database snapshot → the migration
task → an API revision that does not migrate on start → CodeDeploy blue/green, 10% for 5 minutes
under the 5xx alarms → API smoke → dashboard rolling deploy and smoke → notify. Migrations run
before traffic shifts and are backward-compatible, and API tasks never migrate
(`RUN_MIGRATIONS=false`), so the old revision keeps serving against the new schema and a rollback
of the API needs no schema rollback.

**5. Where does a request id come from, where does it go, and what carries it between log
lines?**
From the `X-Request-ID` header if present, else a fresh UUID4, in the request-ID middleware
(just inside CORS); it's echoed on the response and bound into a `contextvars.ContextVar` that a
structlog processor injects into every event in that async task.

**6. What Lambda functions do the CDK stacks deploy today?**
No stack deploys a handler from `backend/lambda/`; the assignment Lambda was removed (#606). The
stacks do deploy placeholder or inline functions: `DatabaseAccessLambda` in the compute stack in
every environment, and in a full checkout `AnalyticsLambda` (placeholder) and `ETLTriggerLambda`
(a real inline function). The serving path is the API on Fargate.

### Exercises

**E4.1 — Write the twin.** For `SafetyService.get_error_metrics`, sketch a unit test that
seeds `ErrorLog` and `RawMetric` rows in a test session and asserts `error_rate`, and name the
integration test that would drive it through `GET /safety/feature-flags/{id}/check`.

**E4.2 — Classify routes.** Using `routeKind`, classify `/login`, `/`, `/sso/complete`, `/docs`,
`/power-calculator`, `/experiments/[id]`, `/admin/safety`, and state which get the shell and
which get `RequireAuth`.

**E4.3 — Add a gate.** Propose a new required PR-gate job that would have caught the "nothing
starts the bandit scheduler" bug, say what it asserts and how long it should take, and describe
the planted defect that proves it fires.

**E4.4 — Compose a profile.** Write a compose service for a hypothetical `docs` container that
serves this documentation repo on `:3400`, in the file's style (profile, healthcheck,
`${VAR:-default}` port).

**E4.5 — Alarm design.** Choose one Prometheus series from `core/metrics.py` and one
CloudWatch alarm from the Fargate or monitoring stack, and describe the operator action each
should trigger and the false-positive you'd guard against.

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
`/feature-flags/evaluate/{key}`, then asserts `is_healthy` from `GET
/safety/feature-flags/{id}/check` — the whole Day 18 path with no mocks.

**S4.2** — `/login` → bare (no shell, no gate). `/` → bare. `/sso/complete` → bare (an anonymous
visitor must not be bounced to `/login` mid-hand-off). `/docs` → open (shell, no gate; it links to
the published documentation site). `/power-calculator` → open (shell, no gate; calls public
endpoints). `/experiments/[id]` → protected (shell + `RequireAuth`). `/admin/safety` → protected
(shell + `RequireAuth`; the page additionally passes `roles` so non-admins see the inline 403).

**S4.3** — A "Lifespan smoke" job: start uvicorn with `ENVIRONMENT=development` against the
service Postgres, wait for `/health/ready`, then assert `GET /scheduler/health` lists all five
scheduler names with a recent run (or `curl /metrics | grep scheduler_ticks_total{name="bandit"`).
It asserts the lifespan _started_ each scheduler and that each completed one locked tick.
Under a minute; it could live inside the existing Docker Smoke job as one more step, which is
cheaper than a new job. Tamper test: comment out the bandit scheduler's start in a scratch
branch and watch the step fail; a step only ever seen green proves nothing.

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

(Serving raw markdown; a real docs site would add a static site generator in a build stage, the
way `frontend/Dockerfile` builds the dashboard's static export.)

**S4.5** — `scheduler_last_success_timestamp{name="safety"}`: alert when `time() − value >
3 × SAFETY_CHECK_INTERVAL_MINUTES × 60`. Action: check `/scheduler/health` for failed ticks,
the API logs for the exception, and the advisory lock (a stuck holder). False positive to
guard: a replica that only ever _skips_ because another replica holds the lock still
publishes no successes — so alert on `max()` across replicas, not per instance. The API's 5xx
alarms (one per target group, in the Fargate stack): during a deployment they roll it back by
themselves, so the operator's action is to read the run summary of the most recent Deploy and
the request ids in `/ecs/experimentation-backend-<env>`; outside a deployment, look at those
request ids first. False positive to guard: a burst of 5xx from a single misbehaving client
(check `rate_limit_rejections_total` and the client ip first) — which would also block the next
Deploy until the alarm clears.

---

_You've built it. Turn to a [specialization](./README.md#where-this-fits-in-the-curriculum). 🏗️_

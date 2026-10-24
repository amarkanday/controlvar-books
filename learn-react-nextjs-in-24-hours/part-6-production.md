# Part VI — Production

### Hours 21–24

[← Part V](./part-5-ui.md) · [Contents](./README.md)

The final Part covers testing and deployment of the dashboard. Hour 21 uses Jest and React Testing
Library for unit and page tests. Hour 22 covers Playwright journeys and the visual and accessibility
suites. Hour 23 follows the production build through the container and nginx, including the API URL
guard. Hour 24 collects the dashboard's coding conventions for use during review.

---

## Hour 21 — Jest + React Testing Library: pages, hooks, services

### What & why

The dashboard's tests — "640+ dashboard tests" by the app README's count — run in **Jest**
with **jsdom** (a DOM in Node) and **React
Testing Library** (RTL), whose philosophy shapes every test here: _test what the user sees
and does_. Render a component, find elements the way a user would (by role, by text, by
label — by test id only when nothing semantic exists), interact with `fireEvent`/`userEvent`,
and assert on the DOM with `jest-dom` matchers (`toBeInTheDocument`, `toHaveTextContent`,
`toHaveAttribute`).

Three layers, three mocking shapes:

| Layer     | What's real                          | What's mocked                                 | Example file                           |
| --------- | ------------------------------------ | --------------------------------------------- | -------------------------------------- |
| Page      | the page, its components, hooks      | `apiFetch` (routed), `useRouter`, `useAuth`, `next/head` | `tests/pages/experiment-detail.test.tsx` |
| Hook      | the hook                             | `global.fetch` or a fake `WebSocket`          | `tests/hooks/useApi.test.ts`           |
| Service   | the service and `apiFetch`           | `global.fetch`                                | `tests/services/api.test.ts`           |

The rule mirrors the backend's: **mock the boundary you don't own (`fetch`, the router),
drive what you do.** A page test that mocked `ExperimentsService` would prove the page
called a method; mocking `apiFetch` with routes proves the page asked for the right URL
with the right method and body.

### In the wild

The page-test scaffolding — a routed `apiFetch` mock, a router stub, a head passthrough:

```tsx
// 📁 frontend/src/tests/pages/helpers/apiMock.tsx
/**
 * Shared scaffolding for page tests: a routed `apiFetch` mock, a `next/router`
 * stub and the `next/head` passthrough. Each test file still declares the
 * `jest.mock(...)` calls itself (jest hoists them per file); this module only
 * provides the implementations.
 */
export type Handler = (path: string, options: ApiFetchOptions) => unknown | Promise<unknown>;

export interface Route {
  method?: string;
  /** Exact path (query string stripped) or a RegExp. */
  path: string | RegExp;
  handler: Handler;
}

export function apiError(status: number, detail: string): ApiError {
  return new ApiError({ status, detail });
}

/** Build an `apiFetch` implementation that dispatches on method + path. */
export function routedApi(routes: Route[]) {
  return jest.fn(async (rawPath: string, options: ApiFetchOptions = {}) => {
    const method = (options.method ?? 'GET').toUpperCase();
    const path = rawPath.split('?')[0];
    const route = routes.find((r) => {
      const methodOk = (r.method ?? 'GET').toUpperCase() === method;
      const pathOk = typeof r.path === 'string' ? r.path === path : r.path.test(path);
      return methodOk && pathOk;
    });
    if (!route) {
      throw apiError(404, `No mock route for ${method} ${path}`);
    }
    return route.handler(rawPath, options);
  });
}
```

A miss throws a 404 `ApiError` — so a page that calls an unexpected URL fails loudly with the
URL in the message, instead of getting `undefined` and rendering something plausible.

A page test, top to bottom — the `jest.mock` calls (hoisted), a stateful install, and
assertions by test id and text:

```tsx
// 📁 frontend/src/tests/pages/experiment-detail.test.tsx
jest.mock('@/services/api', () => ({
  ...jest.requireActual('@/services/api'),
  apiFetch: jest.fn(),
}));

const mockRouter = makeRouter({ pathname: '/experiments/[id]', query: { id: 'exp-1' } });
jest.mock('next/router', () => ({ useRouter: () => mockRouter }));

jest.mock('next/head', () => {
  const Head = ({ children }: { children: React.ReactNode }) => <>{children}</>;
  Head.displayName = 'MockHead';
  return Head;
});

const mockUseAuth = jest.fn();
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => mockUseAuth() }));

const mockedApiFetch = apiFetch as jest.MockedFunction<typeof apiFetch>;
...
function install(current: Experiment) {
  let state = current;
  const transition = (status: Experiment['status']) => () => {
    state = { ...state, status };
    return state;
  };
  mockedApiFetch.mockImplementation(
    routedApi([
      { path: '/api/v1/experiments/exp-1', handler: () => state },
      { method: 'POST', path: '/api/v1/experiments/exp-1/start', handler: transition('active') },
      { method: 'POST', path: '/api/v1/experiments/exp-1/pause', handler: transition('paused') },
      { method: 'POST', path: '/api/v1/experiments/exp-1/complete', handler: transition('completed') },
      { method: 'POST', path: '/api/v1/experiments/exp-1/archive', handler: transition('archived') },
    ]) as unknown as typeof apiFetch,
  );
}

const calledWith = (method: string, path: string) =>
  mockedApiFetch.mock.calls.some(([p, o]) => p === path && ((o?.method ?? 'GET').toUpperCase() === method));

beforeEach(() => {
  mockedApiFetch.mockReset();
  mockUseAuth.mockReturnValue({
    user: { id: 'user-1', email: 'admin@demo.com', username: 'admin', role: 'ADMIN' },
    status: 'authenticated',
  });
});

describe('ACTIONS_BY_STATUS', () => {
  it('mirrors the backend lifecycle guards', () => {
    expect(ACTIONS_BY_STATUS.draft).toEqual(['start']);
    expect(ACTIONS_BY_STATUS.active).toEqual(['pause', 'complete']);
    ...
  });
});

describe('ExperimentDetailPage — rendering', () => {
  it('shows the header, variants, metrics and owner', async () => {
    install(experiment());
    render(<ExperimentDetailPage />);

    expect(screen.getByTestId('experiment-loading')).toBeInTheDocument();
    expect(await screen.findByTestId('experiment-detail')).toBeInTheDocument();

    expect(screen.getByTestId('experiment-name')).toHaveTextContent('Checkout button colour');
    ...
    const rows = within(screen.getByTestId('variants-table')).getAllByTestId('variant-row');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('Control');
```

Read the three RTL verbs: `getBy` (must exist now), `findBy` (await it appearing — the
async fetch resolving), `within(container)` (scope a query). `jest.requireActual` keeps the
real `ApiError`, `messageForDetail` etc. while replacing only `apiFetch`. `calledWith` reads
`mock.calls` to assert _which_ request the page made.

A service/`apiFetch` test — `global.fetch` replaced, the redirect observed through the
`navigation` seam instead of jsdom's read-only `location`:

```tsx
// 📁 frontend/src/tests/services/api.test.ts
const mockFetch = jest.fn();
global.fetch = mockFetch;

/** Await a promise that is expected to reject and return the ApiError. */
async function rejection(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (err) {
    return err as ApiError;
  }
  throw new Error('expected promise to reject');
}

let assignSpy: jest.SpyInstance;

beforeEach(() => {
  mockFetch.mockReset();
  localStorage.clear();
  delete process.env.NEXT_PUBLIC_API_URL;
  delete process.env.NEXT_PUBLIC_WS_URL;
  window.history.replaceState({}, '', '/experiments?page=2');
  assignSpy = jest.spyOn(navigation, 'assign').mockImplementation(() => {});
});
```

That `navigation` object exists in `api.ts` _because_ jsdom won't let a test replace
`window.location.assign` — a small indirection in production code that makes the 401 redirect
testable. `beforeEach` resets everything a previous test could have touched (`fetch`,
`localStorage`, env, history).

The config, and the setup file that registers `jest-dom`'s matchers:

```js
// 📁 frontend/jest.config.js
const full = modulesTreeAvailable();
const moduleTargets = full
  ? [path.join(MODULES_DIR, '$1'), path.join(MODULES_STUB_DIR, '$1')]
  : [path.join(MODULES_STUB_DIR, '$1')];

module.exports = {
  testEnvironment: 'jsdom',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  roots: full ? ['<rootDir>', MODULES_DIR] : ['<rootDir>'],
  modulePaths: ['<rootDir>/node_modules'],
  transform: {
    '^.+\\.(ts|tsx)$': ['ts-jest', {
      tsconfig: {
        jsx: 'react-jsx',
        module: 'commonjs',
      },
    }],
  },
  moduleNameMapper: {
    // `@modules/…` first: it is the more specific pattern of the two.
    '^@modules/(.*)$': moduleTargets,
    '^@/(.*)$': '<rootDir>/src/$1',
    '\\.(css|less|scss|png|jpg|jpeg|gif|svg)$': 'identity-obj-proxy',
    '^recharts$': '<rootDir>/src/__mocks__/recharts.tsx',
  },
  testMatch: ['**/*.test.ts', '**/*.test.tsx'],
  testPathIgnorePatterns: [
    '/node_modules/',
    '/.next/',
    '/out/',
    ...
  ],
  ...
  collectCoverageFrom: [
    'src/components/**/*.{ts,tsx}',
    'src/services/**/*.{ts,tsx}',
    'src/hooks/**/*.{ts,tsx}',
    'src/contexts/**/*.{ts,tsx}',
    '!src/**/*.d.ts',
  ],
};
```

The top half is the modules seam (Hour 10) applied to Jest. `full` comes from the same
`modulesTreeAvailable()` the Next config uses. In a full run, `roots` adds the modules tree, so
the module tests that live beside the module code are collected, and an `@modules/...` import
maps to the modules tree first and the stub second. In a core run (`modules/` deleted, or
`EXPERIMENTLY_PROFILE=core npx jest`) only the stub is reachable and the modules tree is
ignored. `modulePaths` lets files outside the package find `react` in the dashboard's own
`node_modules`.
One known limit is written into the file: coverage does not include the modules tree.

```ts
// 📁 frontend/jest.setup.ts
import '@testing-library/jest-dom';
```

The commands: `npm test` (`jest --watchAll=false`), `npx jest <path> -t "<name>"` for one,
`npm run test:coverage` for numbers (the report covers `frontend/src` only, by the limit
above; this book quotes no percentage).

> **Watch Out!** `jest.mock(...)` calls are **hoisted** above imports by the transformer, so
> they must not reference variables declared with `const` above them in a way that runs at
> hoist time — the codebase's pattern is `const mockUseAuth = jest.fn()` then
> `jest.mock('...', () => ({ useAuth: () => mockUseAuth() }))`: the factory _closes over_
> `mockUseAuth` lazily, which is allowed because the name begins with `mock`.

> **Did You Know?** The test for `ACTIONS_BY_STATUS` is a pure data test on a page's named
> export — the cheapest possible guard that the client's lifecycle table still mirrors the
> backend's guards. When someone changes the state machine on the server, this is the test
> that reminds them to change the table.

### Try It Yourself

Write a page test for `/feature-flags/[id]` that: installs a routed mock for `GET` and
`POST .../deactivate`, renders, awaits the detail, clicks the toggle (`getByRole('switch')`),
and asserts `calledWith('POST', '/api/v1/feature-flags/<id>/deactivate')` and that the switch
now has `aria-checked="false"`. Then run the existing one and compare:

```bash
cd frontend && npx jest src/tests/pages/feature-flag-detail.test.tsx --watchAll=false
```

### Common mistakes

- **Mocking a service** instead of `apiFetch`/`fetch` — you've tested a mock.
- **`getBy` for something that appears after a fetch** — use `findBy`.
- **Querying by class name or DOM structure** — role/text first, test id second.
- **Forgetting `mockReset` in `beforeEach`** — call history leaks between tests.

### Key takeaways

- Jest + jsdom + RTL; assert what the user sees; `getBy`/`findBy`/`within`; `jest-dom`
  matchers.
- Pages mock `apiFetch` with `routedApi` (misses throw), plus `useRouter`, `useAuth`,
  `next/head`; services and `apiFetch` mock `global.fetch`.
- `navigation.assign` exists to make the 401 redirect testable.
- `jest.mock` is hoisted; `mock`-prefixed names may be referenced from factories.

---

## Hour 22 — Playwright: browser journeys, visual regression, accessibility

### What & why

Jest proves components behave with a mocked API. **Playwright** proves the whole thing
works in a real browser against a real backend: log in through the real `/login`, create an
experiment through the real guided setup, start it, read results, archive it. The specs live
under `frontend/tests/e2e/`, and `playwright.config.ts` sorts them into three **projects** by
file name:

- **`journeys`** — the five `*.journey.spec.ts` click paths: `login`, `experiment-lifecycle`,
  `feature-flag`, `admin-panel`, `rbac`. Serial, asserting on `data-testid`s. These are the
  **required `Browser E2E` check on every pull request**: CI builds the static export, starts a
  real seeded backend and runs `npx playwright test --project=journeys`. A journey that fails
  twice (one retry absorbs network jitter) fails the PR.
- **`sso`** — `sso-sign-in.sso.spec.ts`, signing in with single sign-on from the dashboard. It
  needs its own API process and a fake identity provider, which the same `Browser E2E` job
  starts as extra steps.
- **`extended`** — everything else: visual regression (`visual-regression.spec.ts`,
  screenshots compared to committed snapshots) and accessibility (`accessibility.spec.ts`,
  `@axe-core/playwright` scans of five pages that fail on critical or serious WCAG 2.1 AA
  violations). These run **nightly**, not in the PR gate: screenshots and full-page audits are
  slower and noisier than a click path.

The split is the point: the journeys are few, fail-hard and fast enough to block a merge; the
rest are broad and informative. The `scenario-runner` agent in `.claude/agents/` drives the
same dashboard to walk scenarios.

#### Page objects and fixtures

Specs don't touch selectors directly. A **page object** (`ExperimentsPage`) wraps a page's
locators and actions; a **fixture** (`adminPage`, `viewerPage`) logs in as a role through
the real login page and hands the spec an authenticated `Page`. Change a `data-testid` and
one page object changes, not thirty specs.

### In the wild

The config — projects by file name, serial, one worker, artefacts on failure, and a web
server chosen for you:

```typescript
// 📁 frontend/playwright.config.ts
import { API_URL, BASE_URL, IS_EXTERNAL_BASE_URL, PORT } from "./tests/e2e/env";
...
const use = {
  baseURL: BASE_URL,
  headless: HEADLESS,
  screenshot: "only-on-failure" as const,
  video: "retain-on-failure" as const,
  trace: "retain-on-failure" as const,
  actionTimeout: 15_000,
  navigationTimeout: 30_000,
};

export default defineConfig({
  testDir: "./tests/e2e",
  // Journeys walk one backend through real state transitions; serialise them.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  // One retry absorbs network jitter in CI; a journey that fails twice fails the PR.
  retries: process.env.CI ? 1 : 0,
  ...
  projects: [
    {
      name: "journeys",
      testMatch: /\.journey\.spec\.ts$/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "sso",
      testMatch: /\.sso\.spec\.ts$/,
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "extended",
      testMatch: /\.spec\.ts$/,
      testIgnore: [/\.journey\.spec\.ts$/, /\.sso\.spec\.ts$/],
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer,
  outputDir: "playwright-results",
});
```

`BASE_URL`, `API_URL` and `PORT` come from `tests/e2e/env.ts`, shared with the auth fixture
(which builds its own browser contexts and so cannot read the config's `baseURL`). The
defaults match the local stack — dashboard on 3100, API on 8000 — and four environment
variables change them: `PLAYWRIGHT_BASE_URL`, `PLAYWRIGHT_API_URL`, `PLAYWRIGHT_PORT` and
`PLAYWRIGHT_WEB_SERVER`. The last picks the web server, `auto` by default: reuse a dashboard
already answering on the base URL; otherwise, if the dashboard's `out/` holds a static export, serve it
with `tests/e2e/static-server.mjs`, which proxies `/api` the way nginx does in the image (so
the browser sends no CORS preflight); otherwise run `npm run dev`. `trace: "retain-on-failure"`
keeps a trace for every failing test, not only for retries — in CI the first failure is the
one you most want to read.

The auth fixture — real credentials from the demo seed, real login, no bypass:

```typescript
// 📁 frontend/tests/e2e/fixtures/auth.fixture.ts
/**
 * Test credentials for each role.
 *
 * These are the accounts created by `backend/scripts/seed_demo_data.py`
 * (also `SEED=demo` in docker compose). All share the password `Demo1234!`.
 * The backend must run with `AUTH_PROVIDER=local` and `DEV_AUTH_BYPASS=false`
 * so that `/login` is a real login.
 */
export const TEST_USERS = {
  admin: {
    username: "admin@demo.com",
    password: "Demo1234!",
    role: "ADMIN",
    roleLabel: "Admin",
  },
  developer: {
    username: "dev@demo.com",
    ...
  },
  ...
} as const;

/**
 * Log in through the real `/login` page and wait until the app has navigated
 * away from it. Fails loudly if the credentials are rejected — there is no
 * "dev mode skips login" tolerance. ...
```

A journey — serial, timestamped data so reruns don't collide, page objects, assertions on
test ids and on the _absence_ of things (no raw UUIDs in the list):

```typescript
// 📁 frontend/tests/e2e/experiment-lifecycle.journey.spec.ts
/**
 * Journey 2 — experiment lifecycle.
 *
 * The core click-path an evaluator walks in their first ten minutes:
 * list → guided setup at `/experiments/new` → detail → start → pause → resume → complete →
 * results → archive. It runs against a real backend with the demo admin, and
 * every step asserts on `data-testid`s rendered by `src/pages/experiments/`
 * and `src/pages/results/`.
 *
 * Serial: the tests share one experiment, created by the third test. A failure
 * stops the chain rather than letting later steps pass vacuously.
 */
test.describe("Journey: experiment lifecycle", () => {
  test.describe.configure({ mode: "serial" });

  const STAMP = Date.now();
  const EXPERIMENT_NAME = `E2E Lifecycle ${STAMP}`;
  const EXPERIMENT_KEY = `e2e_lifecycle_${STAMP}`;
  let experimentId = "";

  test("the list renders the seeded experiments", async ({ adminPage }) => {
    const experiments = new ExperimentsPage(adminPage);
    await experiments.goto();

    await expect(experiments.createButton).toBeVisible();
    await expect(experiments.createButton).toHaveAttribute("href", "/experiments/new");
    await expect(experiments.listError).toHaveCount(0);
    ...
    await expect(experiments.experimentList).not.toContainText(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/,
    );
    ...
  });

  test("guided setup refuses an allocation that does not add up to 100%", async ({ adminPage }) => {
    const experiments = new ExperimentsPage(adminPage);
    await experiments.gotoGuided();
    ...
    await experiments.variantAllocationInputs.nth(1).fill("30");
    await experiments.nextButton.click();

    await expect(experiments.stepError).toContainText("add up to 100%");
    await expect(adminPage).toHaveURL(/\/experiments\/new\?step=variants$/);
```

The second test is Hour 15 from the outside: Next is refused on the Variants step, the
message is the client mirror's ("add up to 100%"), and the URL still says `?step=variants`.
The page object (`tests/e2e/pages/experiments.page.ts`) owns the wizard's test ids —
`wizard-next`, `wizard-back`, `step-error`, `wizard-step-heading` and the rest — so a renamed
id changes one file.

`test.describe.configure({ mode: "serial" })` — the tests share `experimentId`, so they run
in order and stop at the first failure. `Date.now()` in names and keys — the same "unique
data, no truncation" discipline as the backend's test harness. (The key uses underscores,
like `generateKey`'s output.)

The accessibility suite — dynamic import so a missing dependency skips rather than crashes:

```typescript
// 📁 frontend/tests/e2e/accessibility.spec.ts
/**
 * Accessibility tests using @axe-core/playwright for WCAG 2.1 AA compliance.
 *
 * These tests scan pages for accessibility violations and fail on
 * critical or serious issues. Moderate/minor issues are reported as warnings.
 */

// Dynamic import to gracefully handle missing dependency
async function getAxeBuilder() {
  try {
    const { default: AxeBuilder } = await import("@axe-core/playwright");
    return AxeBuilder;
  } catch {
    return null;
  }
}
```

Running them:

```bash
./demo/setup-local.sh                                              # from the repo root: a seeded backend + the dashboard on :3100
cd frontend
npx playwright install                                             # once
npx playwright test --project=journeys                             # the PR gate's journeys
npx playwright test --project=journeys experiment-lifecycle        # one journey
npm run test:e2e:headed                                            # everything, watching the browser
npm run test:e2e:ui                                                # Playwright's UI mode
npm run test:visual                                                # compare screenshots
npm run test:visual:update                                         # accept new snapshots (review the diff!)
npm run test:a11y                                                  # axe audits
npx playwright show-report
```

> **Watch Out!** Next's route announcer renders a `role="alert"` region on navigation, so
> `page.getByRole('alert')` on the login page matches two elements and Playwright's strict
> mode throws. The login error carries `data-testid="login-error"` for this reason; the
> `LoginPage` page object uses it.

> **Did You Know?** `npm run test:visual:update` rewrites the committed screenshots. A PR
> that touches snapshots should show the before/after images in review — Playwright's HTML
> report renders the diff. Updating snapshots to make a red test green without looking is
> how a regression ships with a "fix" in the commit message.

### Try It Yourself

Add a step to `experiment-lifecycle.journey.spec.ts` (in a scratch copy): after "start", open
the results page via the page object and assert `getByTestId('results-dashboard')` (or the
loading skeleton's test id, then the dashboard) is visible. Run just that journey headed:

```bash
npx playwright test --project=journeys experiment-lifecycle --headed
```

### Common mistakes

- **Selectors in specs** instead of page objects.
- **Fixed names** across runs — collisions on unique keys.
- **A slow or flaky check in the `journeys` project** — it blocks every pull request; broad
  scans belong in `extended`.
- **`getByRole('alert')` on pages with the route announcer.**
- **Blindly updating visual snapshots.**

### Key takeaways

- Playwright: real browser, real backend, real login via fixtures; serial specs with
  timestamped data; page objects own selectors.
- Three projects: `journeys` (five `*.journey.spec.ts`) and `sso` are the required `Browser E2E`
  PR check; `extended` (visual regression, accessibility) runs nightly.
- `tests/e2e/env.ts` holds `BASE_URL` (3100 default) and `API_URL`; `PLAYWRIGHT_WEB_SERVER`
  picks reuse, static export or dev server; traces kept on failure; HTML report.

---

## Hour 23 — Build, Docker, nginx, and the OpenAPI URL guard

### What & why

Shipping the dashboard is four steps: **type-check and test**, **build a static export**,
**package it behind nginx**, and **prove the paths it calls still exist**. The first three
you've met in pieces; the fourth is the dashboard's answer to "the backend renamed a route
and nothing noticed until a user clicked."

The **URL guard** (`tests/services/url-literals.test.ts`) scans the source for every string
starting with `/api/v1/`, normalises dynamic segments, and checks each against
`src/tests/fixtures/openapi.json` — a dump of the backend's OpenAPI document, taken from a
full-profile build. A service path the backend doesn't serve fails the Jest suite. The fixture
is regenerated with `npm run openapi:dump` (which runs `python -m backend.scripts.dump_openapi
--profile full` from the repository root); `make openapi` does that and also rewrites the two
published API snapshots, `docs/api/openapi-v1.stable.json` (core) and
`docs/api/openapi-v1.full.json`.

### In the wild

The guard's contract:

```typescript
// 📁 frontend/src/tests/services/url-literals.test.ts
/**
 * Guard: every `/api/v1/...` URL the dashboard calls must exist on the backend.
 *
 * Scans the dashboard sources (services, components, pages, hooks, contexts —
 * everything but tests and mocks) for string and template literals starting
 * with `/api/v1/`, expands `${CONST}` prefixes declared as
 * `const X = '/api/v1/...'` in the same file, normalises `${expr}` / `{name}`
 * segments to `{param}` and checks each against the paths (and, when the call
 * names one, the HTTP method) in `src/tests/fixtures/openapi.json`.
 *
 * Regenerate the fixture after changing backend routes:
 *   npm run openapi:dump   (= python -m backend.scripts.dump_openapi from the repo root)
 *
 * ## Profiles
 *
 * The dump is taken from a full-profile build and carries 59 paths that only
 * the modules serve. A core backend serves the rest, so the rule "every URL
 * literal exists in the dump" needs a profile, not a single document. Rather
 * than keeping two dumps in sync, one dump is kept and
 * `openapi.module-paths.json` names the modules' subset:
 *
 *   EXPERIMENTLY_PROFILE=core  →  those 59 paths are removed from the
 *                                 document, and only the core tree
 *                                 (`frontend/src`, minus anything the manifest
 *                                 still lists there) is scanned.
 *   anything else (default)    →  the whole document, and both trees:
 *                                 `frontend/src` and `modules/frontend/src`.
 ...
 */
```

The profile matters here too. In a full run the guard scans both `frontend/src` and
`modules/frontend/src` against the whole document; in a core run it removes the modules'
paths from the document and scans only the core tree, so a core page that calls a module
endpoint fails. (The comment's count of module paths is a snapshot; the list itself is
`openapi.module-paths.json`.)

A sibling guard does the same for documentation links. `src/tests/services/docs-links.test.ts`
scans the sources for every `docsUrl('...')` literal (Hour 10) and fails if it does not name a
file under the app repository's `docs/`. A rename in `docs/` that would leave the dashboard
pointing at a 404 fails the dashboard's test suite — and, because a pull request that changes
only `docs/` runs no Node, a Python twin of the same rule runs on those
(`backend/tests/unit/docs/test_dashboard_docs_links.py`). Same shape as the URL guard: scan
the literals, resolve each, assert the target is real.

It understands the codebase's own idiom — `const BASE = '/api/v1/experiments'` then
`` `${BASE}/${id}/start` `` — and matches `${id}` against the spec's `{experiment_id}`. That's
why services write paths as literals and template literals from a `BASE` constant, never
assembled dynamically: the guard has to be able to read them.

The scripts:

```json
// 📁 frontend/package.json
  "scripts": {
    "dev": "next dev -p 3100",
    "build": "next build",
    "start": "next start",
    "lint": "cd .. && eslint --config frontend/eslint.config.mjs --no-error-on-unmatched-pattern --max-warnings 0 frontend/src modules/frontend/src",
    "test": "jest --watchAll=false",
    "test:coverage": "jest --watchAll=false --coverage",
    "test:e2e": "playwright test",
    "test:e2e:headed": "playwright test --headed",
    "test:e2e:ui": "playwright test --ui",
    "test:visual": "playwright test visual-regression",
    "test:visual:update": "playwright test visual-regression --update-snapshots",
    "test:a11y": "playwright test accessibility",
    "openapi:dump": "cd .. && python -m backend.scripts.dump_openapi --profile full frontend/src/tests/fixtures/openapi.json",
    "build:marketing": "NEXT_PUBLIC_SITE_MODE=marketing NEXT_PUBLIC_DOCS_URL=https://getexperimently.github.io/experimently next build && NEXT_PUBLIC_SITE_MODE=marketing node scripts/prune-marketing-site.mjs out"
  },
```

Next 16 removed `next lint`, so `lint` runs ESLint 9 directly with the **flat config** in
`frontend/eslint.config.mjs` (one exported array of config objects, no `.eslintrc`). It runs
from the repository root so it can lint `modules/frontend/src` too, and `--max-warnings 0`
makes a warning fail it. `build:marketing` is the public-site build from Hour 13.

`next build` type-checks and writes the static export to `out/` because of `output: 'export'`;
it no longer lints. CI splits the work:

- the **`lint`** job runs `npm run lint` and `npx tsc --noEmit`;
- the **`Frontend Tests`** job runs `npm ci`, `npm test` and `npm run build` — and then the same
  three things **as the core profile**: `npx tsc --noEmit -p tsconfig.core.json`,
  `EXPERIMENTLY_PROFILE=core npx jest`, and `EXPERIMENTLY_PROFILE=core npm run build`, failing if
  the core bundle mentions a module route. Without those steps every check would resolve
  `@modules/*` to the real modules first, and a core page that leaned on module code would ship
  through a green CI.

A page that doesn't compile, lint or pass in either profile can't merge.

The image — a stage that picks the modules tree by profile, a build stage with assertions,
and nginx-unprivileged:

```dockerfile
# 📁 frontend/Dockerfile
ARG NODE_VERSION=22
ARG EXPERIMENTLY_PROFILE=core

FROM node:${NODE_VERSION}-alpine AS modules-tree-full
COPY frontend/tsconfig.core.json modules*/frontend /modules/frontend/
RUN rm -f /modules/frontend/tsconfig.core.json

FROM node:${NODE_VERSION}-alpine AS modules-tree-core
RUN mkdir -p /modules/frontend
...
FROM modules-tree-${EXPERIMENTLY_PROFILE} AS modules-tree

FROM node:${NODE_VERSION}-alpine AS builder

ARG EXPERIMENTLY_PROFILE
WORKDIR /app/frontend
...
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# modules-alias.js looks for ../modules/frontend/src relative to the frontend dir.
COPY --from=modules-tree /modules/frontend/ /app/modules/frontend/
...
RUN npm run build \
    && test -f .next/routes-manifest.json \
    && node scripts/nginx-routes.mjs --out /app/nginx/routes.conf \
    && test -d out \
    ...
    && if [ "$EXPERIMENTLY_PROFILE" = "core" ]; then \
         ! grep -rlE '/api/v1/workspaces|/api/v1/rbac/' out/_next/static >/dev/null \
         && grep -rl 'Separate teams into workspaces' out/_next/static >/dev/null; \
       else \
         test -d /app/modules/frontend/src \
         && grep -rlE '/api/v1/rbac/' out/_next/static >/dev/null; \
       fi

FROM nginxinc/nginx-unprivileged:1.31-alpine AS runtime
...
LABEL ... \
      io.experimently.profile="${EXPERIMENTLY_PROFILE}"
...
USER root
RUN apk upgrade --no-cache
```

`FROM modules-tree-${EXPERIMENTLY_PROFILE}` picks a stage by name, because `COPY` has no
conditional: a core build gets an empty directory and never reads `modules/` (a core checkout
has none). The odd `modules*/frontend` glob paired with an always-present file keeps the full
stage harmless on builders that build every stage.

Every `&& test ...` is an assertion: the manifest exists, the routes were generated, the export
exists, and **the bundle matches the profile**. A core bundle must not mention a module API
path and must contain the stub page's own text; a full bundle must contain a module path. The
positive half matters as much as the negative: without it, a page that vanished entirely would
also pass "contains no module path". (It greps for a string, not a chunk filename, because
Turbopack emits flat content-hashed chunks.) The runtime stage takes Alpine's security fixes at
build time (`apk upgrade`) and labels the image with its profile. A build that half-succeeds
fails.

The compose wiring and the PR-gate docker smoke that boots it, logs in and mints a key
(Build book, Day 24). Node is pinned to 22 in `.nvmrc`, the Dockerfile and CI together.

> **Watch Out!** `package-lock.json` must be regenerated whenever `package.json` changes,
> and committed. `npm ci` (used by CI and the Dockerfile) refuses an out-of-sync lock — this
> once turned every frontend job red after a dependency was added without running `npm
> install`.

> **Did You Know?** The URL guard is the dashboard's equivalent of the backend's wiring
> smoke test: neither proves behaviour, both prove _connection_ — every route mounted, every
> path called exists. Together with the live SDK contract they're the "did we wire it"
> layer of the test pyramid.

### Try It Yourself

Break the guard on purpose: change `BASE` in `services/experiments.ts` to
`/api/v1/experimentz`, run `npx jest src/tests/services/url-literals.test.ts --watchAll=false`,
read the failure (it names the file, line and path), revert. Then regenerate the fixture
against your running API and confirm the suite is green:

```bash
cd frontend && npm run openapi:dump && npx jest src/tests/services/url-literals.test.ts --watchAll=false
```

Finally, build the image from the repo root and check the nginx routes it generated:

```bash
docker build -f frontend/Dockerfile -t experimently-web:scratch . && docker run --rm experimently-web:scratch cat /etc/nginx/experimently-routes.conf | head
```

### Common mistakes

- **Assembling API paths dynamically** (`'/api/v1/' + resource + '/' + id`) — invisible to
  the guard.
- **Editing `package.json` without `npm install`** — CI's `npm ci` fails.
- **Building with `frontend/` as the Docker context** — the Dockerfile's paths are relative to
  the repository root.
- **Testing only the full profile** — run `tsc -p tsconfig.core.json` and
  `EXPERIMENTLY_PROFILE=core npx jest` before claiming a core page works.
- **Forgetting `npm run openapi:dump`** after a backend route change — the guard fails until
  the fixture catches up.

### Key takeaways

- Ship = ESLint (flat config) + `tsc` + Jest + `next build` (static export), each also as the
  core profile, + Docker (root context, profile stage, nginx, routes script, bundle-matches-
  profile assertion) + docker smoke.
- The URL guard checks every `/api/v1/` literal against the OpenAPI fixture; write paths as
  `BASE` + template literals; regenerate with `npm run openapi:dump`.
- Lock file in sync; Node 22 everywhere; build assertions in the Dockerfile.

---

## Hour 24 — Putting it together: the Experimently dashboard style guide

### What & why

Twenty-three hours of conventions as a review checklist. The mechanical half is enforced —
`strict` TypeScript, ESLint 9's flat config with the React hooks rules (`npm run lint`), Jest
in both profiles, the URL and docs-link guards, `next build`, and the Playwright journeys; the
judgement half is below.

### The style guide

**TypeScript**

- `strict`; no `any` (`unknown` + narrowing); `as` only after a check or for the API
  contract; `Optional`/`| null` matching the schema; `Record<Union, T>` for lookup tables.
- Types mirror `backend/app/schemas` field for field, snake_case included; exact unions
  where the client branches, `| string` where it only displays; JSON blobs are
  `Record<string, unknown>`.
- Defensive parsers (`Record<string, unknown>` → typed) at every edge the dashboard doesn't
  control (WebSocket messages, SDK configuration).

**React**

- Function components; controlled primitives (`value`/`onChange`); thin pages; feature
  components with one prop where possible.
- Never mutate state: spread/`map`/`filter`; functional updates when the next depends on the
  previous; derive during render; `useState<T | null>(null)` typed.
- Every async effect has a `cancelled` flag or mounted ref in cleanup; `void load()` for
  async loaders; subscriptions add/remove in effect/cleanup; guard `window` for SSR.
- Hooks at the top level only; `useCallback`/`useMemo` for stable identities;
  `eslint-disable` for `exhaustive-deps` only with a comment.
- Context via `createContext<T | undefined>` + provider + a `useX()` that throws (or returns a
  documented safe default, like `useModules()`); memoise values on hot providers; tests mock
  the hook.
- `useReducer` over a pure state module when several fields change together or more than one
  view edits the same state.

**Next.js**

- Pages under `src/pages/` are thin: route + `<PageTitle>` + feature component; guard the
  un-hydrated `query`; `typeof rawId === 'string'`.
- `routeKind` decides shell and gate; new routes are protected by default; `RequireAuth`
  is UX, the API enforces; redirects via `replace` after `isReady` through `safeNextPath`.
- `apiBase()`/`wsBase()` for every URL; same-origin in the image; `NEXT_PUBLIC_*` is
  build-time.
- Core or module? A core page is a file under `src/pages/`; a module page lives under
  `modules/frontend/src/pages/`, has a stub under `src/modules-stub/pages/`, and is a one-line
  `@modules/...` re-export in `src/pages/`. Module nav goes in `MODULE_NAV_ITEMS`.
- Docs are links built with `docsUrl()`, never rendered in-app.

**Data**

- One `apiFetch<T>()`; `ApiError` with `detail: unknown`; 401 clears the token and
  redirects (unless told not to).
- One service per resource; methods map 1:1 to routes; `BASE` + template literals; typed
  with mirror interfaces; no React, no state; `|| undefined` to drop empty optionals.
- `useApi(fetcher, [deps])` for reads; lifecycle actions set pending state, use inline
  confirmation, and adopt the server's response as new state.
- Forms: controlled inputs over one reducer, a `validateForm` that mirrors the server (exported
  for tests), annotated payloads, `e.preventDefault()`, a ref-guarded `isSubmitting`; failures
  mapped by status (`describeCreateError`), server messages shown otherwise; never a 401
  redirect that throws away answers.
- The rule builder's state is the API shape; `rulesToJson` strips client ids; operator
  unions mirror the backend adapter.

**Access**

- The UI is never more permissive than the API. Experiments and flags are by role, not
  ownership (`canCreateExperiment`, `canChangeExperiment`); the admin area is superuser-only
  (`withAdminGuard`, nav `superuser: true`); module links show only when the module is
  installed.
- A hidden or refused control says why on screen (`experiment-role-note`,
  `create-not-allowed`, the 403 view's "requires ...").

**UI**

- Tailwind utilities; complete class strings only; arrays joined for conditionals; named
  constants per page; `data-state`/`data-*` for state; `focus-visible` rings; `sr-only`,
  roles, `aria-*`; skeletons with `animate-pulse`.
- Charts: derive data from typed props; one `Bar` per variant; hex colours; a height on the
  container; mocked in Jest.

**Tests**

- Jest + RTL: query by role/text, then test id; `findBy` for async; `within` to scope;
  `jest-dom` matchers.
- Pages mock `apiFetch` with `routedApi` (misses throw), `useRouter`, `useAuth`, `next/head`;
  hooks and services mock `global.fetch`/`WebSocket`; `mockReset` in `beforeEach`.
- Playwright: the `journeys` project is the required `Browser E2E` PR check, `extended`
  (visual, accessibility) runs nightly; fixtures log in for real; page objects own selectors;
  timestamped data; serial specs; snapshots updated only with a reviewed diff.
- The URL guard passes; `npm run openapi:dump` after backend route changes.

**Shipping**

- `npm run lint`, `npx tsc --noEmit`, `npm test` and `npm run build` green, and the core-profile
  trio (`tsc -p tsconfig.core.json`, `EXPERIMENTLY_PROFILE=core` Jest and build); lock file in
  sync; Node 22; the image builds from the repo root for the profile it claims; docker smoke
  passes.

### Try It Yourself

Review the most recent merged frontend PR (`gh pr list --state merged --limit 5 --search
frontend`) against the guide, section by section, and write down each item it touches and
whether it complied. Then run the whole local gate once:

```bash
cd frontend && npx tsc --noEmit && npm run lint && npm test && npm run build
npx tsc --noEmit -p tsconfig.core.json && EXPERIMENTLY_PROFILE=core npx jest --watchAll=false
```

### Key takeaways

- The guide is the book; the tooling enforces the mechanical half.
- Every item traces to a pattern in `frontend/src` and most to a bug the dashboard actually
  had.
- Review against it; ship when the gate is green.

---

## Part VI Workshop

**Q1. Why does a page test mock `apiFetch` rather than `ExperimentsService`?**
Mocking the service proves the page called a method; mocking `apiFetch` with routes proves
the page asked for the right URL with the right method and body — and a wrong URL fails
loudly with a 404 from `routedApi`.

**Q2. `getByTestId('experiment-detail')` fails right after `render()`. Why, and what's the
fix?**
The page shows the loading state until the (mocked) fetch resolves; use
`await screen.findByTestId('experiment-detail')`.

**Q3. Why does `api.ts` export a `navigation` object?**
jsdom's `window.location.assign` can't be replaced in tests; `navigation.assign` is a
production-code seam the 401-redirect test spies on.

**Q4. What must be true of the backend for the Playwright auth fixture to work?**
`AUTH_PROVIDER=local`, `DEV_AUTH_BYPASS=false`, and the demo seed applied (the four
`*@demo.com` accounts with `Demo1234!`) — the fixture logs in through the real `/login`.

**Q5. A backend route is renamed. Which frontend test fails, and how do you make it pass
correctly?**
The URL guard (`url-literals.test.ts`) — after `npm run openapi:dump` refreshes the fixture,
the service path that no longer exists is reported by file and line; update the service (not
the fixture by hand).

**Q6. Why is `npm ci` used in CI and the Dockerfile rather than `npm install`?**
It installs exactly the lock file and fails if `package.json` and the lock disagree —
catching a forgotten `npm install` instead of silently resolving new versions.

**Q7. Which Playwright tests can block a merge, and which cannot?**
The `journeys` project (the five `*.journey.spec.ts` files) and the `sso` project run in the
required `Browser E2E` check on every pull request. The `extended` project — visual regression
and accessibility — runs nightly and reports; it does not gate a merge.

**Q8. Why does CI build and test the dashboard a second time with `EXPERIMENTLY_PROFILE=core`?**
Every other step resolves `@modules/*` to the real modules first, so a core page that imports
module-only code would compile and pass. The core pass resolves only to the stub tree, which
is what a core build actually has.

**Quiz.** Which two Playwright suites should never be "fixed" by updating an artefact
without looking, and what's the artefact?
→ Visual regression (committed screenshots — review the diff) and accessibility (there's no
artefact to update; fix the markup).

---

## Exercises

**E6.1 — A hook test.** Write a Jest test for `useApi` that renders a component using it
with a fetcher resolving after 50 ms, asserts `loading` then `data`, then calls `refetch` and
asserts the fetcher was called twice.

**E6.2 — A routed page test.** Write the test for `/experiments/new` submitting a valid
single-page form: set up the router mock so the page renders the form at all, install
`routedApi` with `POST /api/v1/experiments` returning `{ id: 'exp-9' }`, fill the name,
submit, and assert the POST body's `variants` sum to 100, that the create was sent with
`redirectOn401: false`, and that `mockRouter.push` was called with `/experiments/exp-9`.

**E6.3 — A page object.** Sketch `FlagsPage` for Playwright with locators for the create
button, the rows, a row's toggle (`role="switch"`), and a `toggleFirst()` action.

**E6.4 — Guard-proof paths.** Rewrite this service so the URL guard can read it:

```typescript
const resource = 'rollout-schedules';
export const RolloutService = { list: () => apiFetch('/api/v1/' + resource + '/') };
```

**E6.5 — Review.** List every style-guide item this component violates:

```tsx
export default function Row(props: any) {
  const [flag, setFlag] = useState(props.flag);
  useEffect(async () => { const f = await fetch('/api/v1/feature-flags/' + props.id).then((r) => r.json()); setFlag(f); }, []);
  return <div className={`bg-${flag.enabled ? 'green' : 'slate'}-100`} onClick={props.onToggle()}>{flag.key}</div>;
}
```

---

## Solutions

**S6.1**

```tsx
function Probe({ fetcher }: { fetcher: () => Promise<string> }) {
  const { data, loading, refetch } = useApi(fetcher, []);
  return (<div><span data-testid="state">{loading ? 'loading' : data}</span><button onClick={refetch}>again</button></div>);
}

it('loads, then refetches', async () => {
  const fetcher = jest.fn(() => new Promise<string>((r) => setTimeout(() => r('ok'), 50)));
  render(<Probe fetcher={fetcher} />);
  expect(screen.getByTestId('state')).toHaveTextContent('loading');
  expect(await screen.findByText('ok')).toBeInTheDocument();
  fireEvent.click(screen.getByText('again'));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
});
```

**S6.2**

```tsx
// The page renders only a loading marker until the router is ready, and the
// form exists only in the `?advanced` view; the default view is the wizard.
const mockRouter = makeRouter({
  pathname: '/experiments/new',
  asPath: '/experiments/new?advanced',
  query: { advanced: '' },        // makeRouter defaults isReady: true and push resolving true
});
jest.mock('next/router', () => ({ useRouter: () => mockRouter }));

it('creates an experiment and navigates to it', async () => {
  let body: CreateExperimentRequest | undefined;
  mockedApiFetch.mockImplementation(routedApi([
    { method: 'POST', path: '/api/v1/experiments', handler: (_p, o) => { body = o.json as CreateExperimentRequest; return { id: 'exp-9' }; } },
  ]) as unknown as typeof apiFetch);
  render(<NewExperimentPage />);
  fireEvent.change(screen.getByTestId('experiment-name'), { target: { value: 'Hero test' } });
  fireEvent.submit(screen.getByTestId('new-experiment-form'));
  await waitFor(() => expect(mockRouter.push).toHaveBeenCalledWith('/experiments/exp-9'));
  expect(body!.variants.reduce((s, v) => s + v.traffic_allocation, 0)).toBe(100);
  expect(mockedApiFetch.mock.calls[0][1]).toEqual(expect.objectContaining({ redirectOn401: false }));
});
```

Three things the old version of this test (a single-page form at `/experiments/new`) did
not need. **`isReady: true`**: without it the page renders only `new-experiment-loading`.
**`query: { advanced: '' }`**: without it the page renders the guided wizard, which has no
`<form>` and no `new-experiment-form`. **A `push` that resolves**: the page awaits
`router.push` and falls back to a hard navigation when it returns `false`; `makeRouter`'s
`push` resolves `true`. The route path is `/api/v1/experiments` because `routedApi` strips
only the query string; match the literal the service uses — `BASE` without a trailing slash.
The real tests are `src/tests/pages/experiment-new*.test.tsx` (one file per view and concern)
and, for the pure parts, `src/tests/components/experiment-form-state.test.ts`.

**S6.3**

```typescript
export class FlagsPage {
  constructor(private page: Page) {}
  readonly createButton = () => this.page.getByTestId('create-flag');
  readonly rows = () => this.page.getByTestId('flag-row');
  readonly toggleOf = (row: Locator) => row.getByRole('switch');
  async goto() { await this.page.goto('/feature-flags'); }
  async toggleFirst() {
    const first = this.rows().first();
    await this.toggleOf(first).click();
    return first;
  }
}
```

**S6.4**

```typescript
const BASE = '/api/v1/rollout-schedules';
export const RolloutService = {
  list: () => apiFetch<RolloutScheduleListResponse>(`${BASE}/`),
  get: (id: string) => apiFetch<RolloutSchedule>(`${BASE}/${id}`),
};
```

The guard expands `${BASE}` from a `const BASE = '/api/v1/...'` in the same file and
normalises `${id}` to a parameter; string concatenation with a `resource` variable is
opaque to it.

**S6.5** — `props: any` (type the props); copying `props.flag` into state (controlled vs
uncontrolled — either render from the prop or own the state, not both); `useEffect(async …)`
(returns a promise; no cancelled flag; `[]` ignores `props.id`); raw `fetch` with a
concatenated path (use a service + `apiFetch`; the URL guard can't read it; no auth header);
`` `bg-${…}-100` `` (purged by Tailwind — complete class strings); `onClick={props.onToggle()}`
(called during render — pass the function); a clickable `div` with no role/keyboard
handling (use a `button` or the `FlagToggle` switch); no `data-testid`; a `default export`
for a non-page component (named exports for components). Nine comments — a rewrite, not a
review.

---

_You've done the twenty-four hours. Take the [Build book](../build-a-platform-in-28-days/README.md)
next — Day 23 is the dashboard seen from the whole platform's point of view. ⏱️_

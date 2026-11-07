# Part III — Next.js

### Hours 10–13

[← Part II](./part-2-react.md) · [Contents](./README.md) · [Part IV →](./part-4-data-and-forms.md)

Next.js is the framework around React that gives the dashboard routing, a build, a dev server
and a production artefact. Four hours: files are routes (Hour 10); `_app.tsx`, the shell, and
how a route's _kind_ decides what wraps it (Hour 11); the client-side auth gate, `useRouter`,
and dynamic routes (Hour 12); and the environment/base-URL contract plus the static export
that ships behind nginx (Hour 13). The dashboard runs **Next.js 16 with React 19** and uses the
**pages router** (`src/pages/`), not the app router — deliberately, and this Part explains why.
Since Next 16, `next build` bundles with **Turbopack** by default;
`next build --webpack` still selects webpack, and the config keeps both working (below).

---

## Hour 10 — The pages router: files are routes

### What & why

In the pages router, **the filesystem is the routing table**: `src/pages/experiments/index.tsx`
is `/experiments`, `src/pages/experiments/[id].tsx` is `/experiments/:id`. A page is a file with
a **default export** component; everything else exported from it is just a module export (the
tests import `ACTIONS_BY_STATUS` and `validateForm` from page files that way).

Two special files: `_app.tsx` wraps every page (Hour 11), and `_document.tsx` customises the
HTML shell that every page is rendered into. The dashboard's `_document.tsx` exists for one
attribute — `<Html lang="en">`, so a screen reader reads the page in the right language
(WCAG 2.1 success criterion 3.1.1); titles and meta tags stay in `_app.tsx` and `PageTitle`.
Navigation between pages is client-side via `next/link` (`<Link href="/experiments">`), which
prefetches and swaps without a full reload; `next/router`'s `useRouter()` gives the current
path, query and programmatic `push`/`replace`.

#### Why the pages router, and why a static export

The dashboard is a **client-rendered application**: every page fetches from the API in the
browser with the user's bearer token. There's no server-side data fetching, no server
components, no per-request rendering — so the app router's main features aren't used, and
`output: 'export'` turns the whole thing into static HTML + JS that nginx can serve. No page
uses `getStaticProps` or `getServerSideProps`. Pages router + static export is the simplest
thing that fits, and simple is what a dashboard for an API should be.

### In the wild

The route table, as files:

```
frontend/src/pages/
├── _app.tsx                 # wraps every page (providers, shell, auth gate)
├── _document.tsx            # the HTML shell: <Html lang="en">
├── index.tsx                # /
├── login.tsx                # /login
├── power-calculator.tsx     # /power-calculator (public)
├── privacy.tsx              # /privacy (the public site's privacy notice)
├── account/
│   ├── api-keys.tsx         # /account/api-keys (any signed-in user's own API keys)
│   └── password.tsx         # /account/password (a local user changes their own password)
├── docs/
│   └── index.tsx            # /docs — a hub of links to the documentation
├── experiments/
│   ├── index.tsx            # /experiments
│   ├── new.tsx              # /experiments/new (guided setup; ?advanced = one page)
│   ├── holdouts.tsx         # /experiments/holdouts (the global holdout)
│   ├── exclusion-groups/
│   │   ├── index.tsx        # /experiments/exclusion-groups
│   │   └── [id].tsx         # /experiments/exclusion-groups/:id
│   └── [id].tsx             # /experiments/:id
├── feature-flags/
│   ├── index.tsx · new.tsx · [id].tsx
├── segments/
│   ├── index.tsx · new.tsx · [id].tsx
├── results/
│   ├── index.tsx · [id].tsx
├── admin/
│   ├── index.tsx · users.tsx · api-keys.tsx · roles.tsx · safety.tsx · scheduler.tsx · audit.tsx · notifications.tsx
│
│   # module routes: one-line re-exports of @modules/pages/... (see below)
├── sso/
│   └── complete.tsx         # /sso/complete — the SSO hand-off
├── warehouse/
│   ├── index.tsx · connections/new.tsx · connections/[id].tsx · sources/new.tsx · sources/[id].tsx
└── workspaces/
    ├── index.tsx · new.tsx · [id]/ · invites/
```

A page: a default export that reads the dynamic segment and hands it to a feature component:

```tsx
// 📁 frontend/src/pages/results/[id].tsx
import React from 'react';
import { useRouter } from 'next/router';
import { ResultsDashboard } from '@/components/results/ResultsDashboard/ResultsDashboard';
import { PageTitle } from '@/components/PageTitle';

export default function ResultDetailPage() {
  const { query } = useRouter();
  const id = query.id as string | undefined;

  if (!id) {
    return (
      <div className="flex-1 flex items-center justify-center bg-slate-50">
        <PageTitle title="Results" />
        <div className="animate-pulse text-slate-400">Loading...</div>
      </div>
    );
  }

  return (
    <div className="flex-1 bg-slate-50">
      <PageTitle title="Results" />
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <ResultsDashboard experimentId={id} />
      </div>
    </div>
  );
}
```

Thin: the page is routing + a title + one component. (`query.id as string | undefined` is an
`as` that Hour 4 would prefer as a `typeof` narrow; the `[id].tsx` experiment page does it
the stricter way.) On the first render of a static export, `query` is `{}` until the router
hydrates — hence the `if (!id)` loading branch, which every dynamic page has.

The newest experiment pages show the router's precedence rule at work. `/experiments/holdouts`
and `/experiments/exclusion-groups` sit beside `/experiments/[id]`. Next's router matches a
predefined segment before a dynamic one, so `holdouts` is never read as an experiment id; the
nginx blocks generated for the image (Hour 13) keep the same order, because their
`try_files $uri $uri.html ...` tries the exported static page before falling back to
`[id].html`. The thinnest page of all is one of the newest:

```tsx
// 📁 frontend/src/pages/account/api-keys.tsx
/**
 * Your own API keys, for every signed-in user. There is no admin guard: the
 * route is protected like every dashboard page (`routeKind`), and the API
 * scopes each call to the caller's own keys.
 */
export default function OwnApiKeysPage() {
  return (
    <div className="flex-1 bg-slate-50">
      <PageTitle title="Your API keys" />
      <OwnApiKeys />
    </div>
  );
}
```

Its comment states the access rule, and the rule is the API's: every role may hold keys of its
own, so the page needs no gate beyond being `protected` (Hour 11). Hour 15 opens `OwnApiKeys`.

#### Module routes: the `@modules/*` seam

Some pages belong to optional **modules** — workspaces, the data warehouse, SSO sign-in,
custom roles. Controlvar ships as one codebase with two build profiles: **core** (the
dashboard under `frontend/` alone) and **full** (adds the modules' dashboard code under
`modules/frontend/src/`, beside `frontend/`, not inside it). A core checkout has no `modules/`
directory at all, yet the route files above must still compile. So a module route is a
one-line re-export:

```tsx
// 📁 frontend/src/pages/warehouse/index.tsx
/**
 * Module route — warehouse connections and sources (#312, beta).
 *
 * Thin re-export so this file compiles in both profiles: `@modules/*` resolves
 * to `modules/frontend/src/` when the modules tree is present and to
 * `src/modules-stub/` (a "module not installed" notice) when it is not. See
 * `next.config.js`.
 */
export { default } from '@modules/pages/warehouse/index';
```

`frontend/modules-alias.js` owns the rule and every toolchain applies it — `tsconfig.json`
`paths` (Hour 1), the Next config below, Jest's `moduleNameMapper` (Hour 21) and the
Dockerfile (Hour 23):

```js
// 📁 frontend/modules-alias.js
/** True when the modules tree should be used (the full profile). */
function modulesTreeAvailable(env = process.env) {
  if (String(env.EXPERIMENTLY_PROFILE || '').toLowerCase() === 'core') return false;
  return fs.existsSync(MODULES_DIR);
}

/** Directories `@modules/*` resolves against, in order. */
function modulesAliasTargets(env = process.env) {
  return modulesTreeAvailable(env) ? [MODULES_DIR, MODULES_STUB_DIR] : [MODULES_STUB_DIR];
}
```

Full means "`EXPERIMENTLY_PROFILE` is not `core` **and** `modules/frontend/src` exists". With
the tree present, that import is the real page under `modules/frontend/src/pages/`; without it, it is
`frontend/src/modules-stub/pages/warehouse/index.tsx`, a notice that the module is not
installed. The stub tree mirrors every path the core code imports from `@modules/*`, which
is what lets `tsc -p tsconfig.core.json` and a core build prove the core dashboard stands
alone. When you add a page, decide first: core page (a normal file under `src/pages/`) or
module page (the real page under `modules/frontend/src/pages/`, a stub under
`src/modules-stub/pages/`, and a re-export here).

#### `/docs`: links out, not rendered in

`/docs` used to render the repository's markdown inside the dashboard. It no longer does: the
markdown renderer and its catch-all route are gone, and `src/pages/docs/index.tsx` is a static
hub of links. Every link is built by one function:

```tsx
// 📁 frontend/src/pages/docs/index.tsx
import { DOCS_BRANCH, DOCS_REPO, docsUrl } from '@/services/docs';
...
      { label: 'Quick Start', href: docsUrl('getting-started/quick-start'), desc: 'Run the stack and create your first experiment' },
```

```typescript
// 📁 frontend/src/services/docs.ts
/** A self-hosted built site, if one is configured. Empty means "use the repository". */
export const DOCS_SITE = (process.env.NEXT_PUBLIC_DOCS_URL || '').replace(/\/+$/, '');
```

`docsUrl('getting-started/quick-start')` names a file under the app repository's `docs/`
(without `.md`). By default it becomes a GitHub URL for that markdown file, which cannot be
missing for whatever version you run: the published site at docs.controlvar.com is built from
release tags only, and the file's own header comment records why the default is the repository
(it once was the site, before the site had ever been deployed, and every link answered 404).
Set `NEXT_PUBLIC_DOCS_URL` at build time to the origin of a built MkDocs site and the links
take that site's directory-style URLs instead (the marketing build sets it to the published
docs site). A Jest guard, `docs-links.test.ts`,
fails if any `docsUrl('...')` literal names a page that does not exist under `docs/` (Hour 23).

Client-side navigation and programmatic routing:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
import Link from 'next/link';
...
            <Link href="/experiments" className="text-slate-500 hover:text-slate-700 text-sm">
              &larr; Experiments
            </Link>
```

```tsx
// 📁 frontend/src/pages/experiments/new.tsx
  const router = useRouter();
  ...
      const created = await ExperimentsService.create(payload, { redirectOn401: false });
      createdId = created.id;
  ...
    const target = `/experiments/${createdId}`;
    let moved = false;
    try {
      moved = (await router.push(target)) !== false;
    } catch {
      moved = false;
    }
    if (!moved) navigateHard(target);
```

`router.push` resolves to `false` when the navigation does not happen; the page then falls
back to a full page load (`navigateHard`) rather than leaving the user on a form whose
experiment already exists (Hour 15).

`next.config.js` decides the artefact. Three lines are the original contract; the rest wire
the modules seam into both bundlers:

```js
// 📁 frontend/next.config.js
const full = modulesTreeAvailable();

module.exports = {
  reactStrictMode: true,
  output: 'export',
  images: {
    unoptimized: true,
  },
  typescript: {
    tsconfigPath: full ? 'tsconfig.json' : 'tsconfig.core.json',
  },
  experimental: {
    externalDir: true,
  },
  turbopack: {
    root: REPO_ROOT,
    resolveAlias: modulesAliasTurbopack(),
  },
  // `next build --webpack`. Kept in step with the Turbopack alias above by
  // src/tests/modules-alias.test.ts, which asserts both spell the same rule.
  webpack: (config) => {
    ...
      '@modules': modulesAliasTargets(),
    ...
  },
  // Security headers are applied at the serving layer (nginx/edge/CDN) for
  // static export deployments.
};
```

`reactStrictMode` (double-invoked effects in dev — Hour 7's cleanup test), `output:
'export'` (static HTML into `out/`), `images.unoptimized` (no image server in a static
export), and a comment that says where security headers live instead. Then the seam:

- `typescript.tsconfigPath` points a core build at `tsconfig.core.json`, because Next also
  derives aliases from `tsconfig.json`'s `paths` and would otherwise quietly resolve
  `@modules/*` back to the real module.
- `turbopack.root` is the repository root, because Turbopack resolves nothing above its root
  and the modules tree is a sibling of `frontend/`; `turbopack.resolveAlias` is the alias in
  Turbopack's spelling (`'@modules/*'` with `/*` targets).
- `experimental.externalDir` and the `webpack()` function do the same for
  `next build --webpack`. The webpack function also adds the profile to webpack's cache key:
  without it, a full build after a core build reused cached stub pages.

> **Watch Out!** A page file's _default_ export is the page; a _named_ export (like
> `generateKey` or `ACTIONS_BY_STATUS`) is fine for tests but must not be a React component
> Next.js would try to treat as a page. And a file under `src/pages/` that isn't a page
> (a helper) becomes a broken route — put helpers under `components/`, `utils/` or `hooks/`.

> **Did You Know?** `next dev -p 3100 -H 127.0.0.1` — the dashboard's dev script uses 3100 so
> it can run beside the compose stack's `:3000`, and binds to the loopback address so the dev
> server is not offered to your whole network. Both origins are in the API's CORS defaults. The
> demo apps take 3200 and 3300 for the same reason.

### Try It Yourself

Add a throwaway page `src/pages/scratch.tsx` with a default export that renders
`useRouter().pathname` and a `<Link href="/experiments">`; open `/scratch`; click the link
and watch the URL change without a reload (the network tab shows no document request). Then
rename it to `src/pages/scratch/[name].tsx`, visit `/scratch/hello`, and print `query.name`.
Delete it after.

Then look at the seam: open `frontend/src/pages/workspaces/index.tsx` and
`frontend/src/modules-stub/pages/workspaces/index.tsx`, and run
`EXPERIMENTLY_PROFILE=core npm run dev`. Visit `/workspaces`: you get the stub's "not
installed" page even if `modules/` is in your checkout. Stop it and run plain `npm run dev`
in a full checkout to see the real page.

### Common mistakes

- **Helpers under `src/pages/`** — they become routes.
- **Reading `query.id` without a loading guard** — it's empty on the first static render.
- **`<a href>` instead of `<Link>`** for internal navigation — full reload, lost state.
- **`fs`/`path` in page code** — the export runs in the browser; there is no build-time data
  route left to put them in.
- **Importing a module's code by a relative path** (`../../modules/frontend/...`) instead of
  `@modules/...` — it compiles in a full checkout and breaks the core build.

### Key takeaways

- `src/pages/**` is the route table; default export = page; `[id]` is dynamic; `_app.tsx`
  wraps all; `_document.tsx` sets `lang="en"`.
- Pages are thin: route + title + feature component; dynamic pages guard the
  not-yet-hydrated `query`.
- Client-rendered app + `output: 'export'`; `/docs` is a link hub built with `docsUrl()`.
- Module routes are one-line re-exports of `@modules/pages/...`; the alias resolves to
  `modules/frontend/src/` (full) or `src/modules-stub/` (core), in Turbopack and webpack alike.
- `<Link>` and `router.push` for navigation; `next dev -p 3100 -H 127.0.0.1`.

---

## Hour 11 — `_app.tsx`, the shell, and route kinds

### What & why

`_app.tsx` is the one component that wraps every page. It's where providers are mounted
(Hour 9), where the global CSS is imported, where `<Head>` sets the title and favicon, and —
in this dashboard — where a route's **kind** decides whether the page gets the application
shell and the auth gate.

Three kinds, decided by a pure function of the route pattern:

| Kind        | Shell (`AppShell`) | Auth gate (`RequireAuth`) | Routes                                                  |
| ----------- | ------------------ | ------------------------- | ------------------------------------------------------- |
| `bare`      | no                 | no                        | `/login`, `/`, `/sso/complete`                          |
| `open`      | yes                | no                        | `/docs/*`, `/power-calculator`, `/privacy`, `/404`, `/500`, `/_error` |
| `protected` | yes                | yes                       | everything else                                         |

#### Why a classifier and not per-page wrappers

Every protected page would otherwise repeat `<AppShell><RequireAuth>…</RequireAuth></AppShell>`,
and one would forget the gate. A single `routeKind(pathname)` in `utils/routes.ts` is
testable in isolation (it is), reviewable in one place, and — because it works on the route
_pattern_ (`/experiments/[id]`), not the concrete path — it doesn't need to know about ids.

### In the wild

The classifier:

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
 * (the power calculator). `/privacy` is the public site's privacy notice.
 */
const OPEN_ROUTE_PREFIXES = ['/docs', '/power-calculator', '/privacy', '/404', '/500', '/_error'];

export type RouteKind = 'bare' | 'open' | 'protected';

/**
 * Classify a Next.js route pattern (`router.pathname`, e.g. `/experiments/[id]`).
 */
export function routeKind(pathname: string): RouteKind {
  if (BARE_ROUTES.has(pathname)) return 'bare';
  if (OPEN_ROUTE_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
    return 'open';
  }
  return 'protected';
}
```

"Protected by default" — a new page is gated unless it's explicitly listed as open. That's
the fail-closed choice, mirrored from the backend's settings. `/sso/complete` shows why the
exceptions need a reason written beside them: it is where a single sign-on provider hands the
browser back, by definition before the dashboard has a session, so gating it would send the
user to `/login` and lose the hand-off.

The app component, composing the wrappers from the kind:

```tsx
// 📁 frontend/src/pages/_app.tsx
import "@/styles/globals.css";
import type { AppProps } from "next/app";
import Head from "next/head";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AuthProvider } from "@/contexts/AuthContext";
import { NotificationProvider } from "@/contexts/NotificationContext";
import { ModulesProvider } from "@/contexts/ModulesContext";
import { EffectivePermissionsProvider } from "@/contexts/EffectivePermissionsContext";
import { ToastContainer } from "@/components/ToastContainer";
import { AppShell } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { routeKind } from "@/utils/routes";

export default function App({ Component, pageProps, router }: AppProps) {
  const kind = routeKind(router.pathname);
  // The page's path, without its query or hash: a navigation to another page
  // asks for the signed-in user's effective permissions again (#891), a
  // filter written into the query string does not.
  const navigationKey = (router.asPath ?? "").split(/[?#]/)[0];

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
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
        <title>Controlvar</title>
      </Head>
      <ModulesProvider>
        <AuthProvider>
          <EffectivePermissionsProvider navigationKey={navigationKey}>
            <NotificationProvider>
              {page}
              <ToastContainer />
            </NotificationProvider>
          </EffectivePermissionsProvider>
        </AuthProvider>
      </ModulesProvider>
    </ErrorBoundary>
  );
}
```

Read the nesting from outside in: an `ErrorBoundary` (a class component that catches render
errors and shows a fallback instead of a blank page), `<Head>` (title/favicon — pages
override the title with `<PageTitle>`), the modules, auth, effective-permissions and notification
providers, then the page wrapped by its kind, then the toasts rendered once at the root so they
overlay everything. `page` is built up by reassignment — a JSX value is just a value.

`navigationKey` is the one piece of routing the providers see. `_app` is rendered again on every
client-side navigation, so the path it computes changes exactly when the user moves to another page;
the effective-permissions provider (Hour 9) lists it as a dependency and asks for the user's map
again. The comment records the second half of the decision: the query string and the hash are cut
off, so a filter or a wizard step written into the URL (`?step=details`, Hour 15) does not send a
request.

The shell — nav items with access gating, and the active-link rule:

```tsx
// 📁 frontend/src/components/AppShell.tsx
export const NAV_ITEMS: NavItem[] = [
  { label: 'Experiments', href: '/experiments', testId: 'nav-experiments' },
  { label: 'Feature Flags', href: '/feature-flags', testId: 'nav-feature-flags' },
  { label: 'Segments', href: '/segments', testId: 'nav-segments' },
  // Every role: the audit API answers each one, narrowed to their own entries
  // for anyone who may not read the audit log. `roles` also keeps it off
  // anonymous renders.
  { label: 'Audit Log', href: '/admin/audit', testId: 'nav-audit-log', roles: AUDIT_LOG_ROLES },
  { label: 'Admin', href: '/admin', testId: 'nav-admin', superuser: true },
  { label: 'Docs', href: '/docs', testId: 'nav-docs' },
];
...
export function isNavActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
...
  const marketing = isMarketingSite();
  const visibleNav = NAV_ITEMS.filter((item) => {
    if (item.superuser) return user !== null && user.is_superuser === true;
    if (item.roles) return user !== null && item.roles.includes(user.role);
    return true;
  });
```

"Admin" is gated on **`superuser: true`, not on a role**. Every endpoint under
`/api/v1/admin` requires a superuser account, and `role` and `is_superuser` are independent
columns on the user: an ADMIN-role user who is not a superuser gets 403 from every admin
endpoint. An earlier version showed the item to `roles: ['ADMIN', 'DEVELOPER']`; a DEVELOPER
saw it, clicked, and met a page of 403s. The rule the nav follows is the one the API applies.
(`marketing` is the public-site build of the same code, Hour 13, which has no API. The filter no
longer special-cases it: a few lines further down, the marketing build returns a different header
altogether, `SiteHeader`, the public homepage's own, so none of these items is rendered there.)

Modules add a second, collapsed group:

```tsx
// 📁 frontend/src/components/AppShell.tsx
export const MODULE_NAV_ITEMS: (NavItem & { module: string })[] = [
  {
    label: 'Workspaces',
    href: '/workspaces',
    testId: 'nav-workspaces',
    module: MODULES.WORKSPACES,
  },
  {
    // The warehouse API refuses VIEWER every connection and source route, so
    // the link is not offered to one. A superuser counts as ADMIN there.
    label: 'Warehouse',
    href: '/warehouse',
    testId: 'nav-warehouse',
    module: MODULES.WAREHOUSE,
    roles: ['ADMIN', 'DEVELOPER', 'ANALYST'],
  },
];
```

These links appear under a "More" disclosure only when `useModules()` (Hour 9's
`ModulesProvider`) reports the module installed; in the core profile "More" carries a
"Modules" link to the guide instead (not while the probe is still in flight, and not after a
failed one, because the dashboard has no grounds to claim "this is the core profile" when all it
knows is that the API did not answer). "More" also holds the signed-in user's own pages: "Your
API keys" (`/account/api-keys`) for everyone, and "Change password" (`/account/password`) only for a
local-sign-in user, because under any other provider that route answers 404. Both are kept out of
the header row: an earlier "Change password" there pushed the header past its row width and
wrapped the other items. The primary nav is core chrome and never hard-links a route the core
profile does not serve.

Nav gating is a convenience, not a control; the endpoints return 403 regardless.
`isNavActive` is exported for its test, and it's the same prefix logic as `routeKind`; because
`/admin/audit` matches both "Audit Log" and "Admin" by prefix, `activeNavHref` picks the longest
matching href, so only the more specific item is marked current there. "Audit Log" is a second
example of a nav rule copied from the API rather than invented: it carries `roles: AUDIT_LOG_ROLES`
(all four roles) because the audit API answers every signed-in user, narrowed to their own entries
for anyone who may not read the whole log (that is: not a superuser, not ADMIN or ANALYST, and no
`audit_log: read` grant; `readsAllAuditLogs` in `frontend/src/components/admin/audit/access.ts`
mirrors it, for the sentence the page shows). The shell also renders a skip link (`href="#main-content"`) and `aria-current="page"` on the
active item — accessibility that the Playwright a11y suite checks.

`<PageTitle>` — the per-page title, because `<Head>` in `_app` sets only the default:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
      <PageTitle title={experiment.name} />
```

> **Watch Out!** `router.pathname` is the _pattern_ (`/experiments/[id]`); `router.asPath` is
> the _actual URL_ (`/experiments/3f9a…`). `routeKind` and `isNavActive` want the pattern;
> `RequireAuth`'s `next=` redirect wants the real path — and on a static export `asPath` is
> also the pattern until the router is ready, which is why `RequireAuth` waits for
> `router.isReady` (Hour 12).

> **Did You Know?** `ErrorBoundary` is one of the very few **class components** in the
> codebase: React has no hook for `componentDidCatch`, so error boundaries are still classes.
> Everything else is a function component.

### Try It Yourself

Write the tests for `routeKind` in your head, then run the real ones:

```bash
cd frontend && npx jest src/tests -t "routeKind" --watchAll=false
```

Then add a page at `src/pages/scratch-open.tsx` and confirm it's gated (you're redirected
to `/login` when logged out); add `'/scratch-open'` to `OPEN_ROUTE_PREFIXES` and confirm it
isn't. Revert both.

### Common mistakes

- **Wrapping pages individually** with the shell/gate instead of via the classifier.
- **Gating a nav item on a different rule from the API's** — a role list for an area the API
  gates on `is_superuser`, or no gate at all. The page still 403s on the API, but the user is
  sent to a dead end.
- **Putting a module route in `NAV_ITEMS`** — it belongs in `MODULE_NAV_ITEMS`, shown only when
  the module is installed.
- **Using `asPath` where `pathname` is meant** (or vice versa).
- **Providers inside pages** — mount them once in `_app`.

### Key takeaways

- `_app.tsx` mounts providers, global CSS, `<Head>`, the error boundary, and wraps the page
  by its kind.
- `routeKind(pathname)` → `bare | open | protected`, protected by default; `AppShell` for
  non-bare, `RequireAuth` for protected.
- Nav gating mirrors the API (Admin: superuser; module links: installed, and a role where the
  module API wants one) and is UX; the API enforces.
- `ModulesProvider` is outermost; a failed `/api/v1/modules` probe means "core".
- `pathname` is the pattern, `asPath` the real path.

---

## Hour 12 — `RequireAuth`, `useRouter`, and dynamic routes

### What & why

`RequireAuth` is the client-side gate for protected pages. It reads `useAuth()`'s three-state
status and does one of four things: show a spinner (`loading`), redirect to
`/login?next=<current path>` (`anonymous`), show an inline 403 (not a superuser when the page
asks for one, or the wrong role), or render the children. Its comment says the important thing — _"the API is the real enforcement"_ — the
gate exists so users see a login page instead of a wall of 401 errors, not to protect data.

`useRouter()` provides `pathname`, `asPath`, `query`, `isReady`, `push`, `replace`. Two
subtleties matter on a static export:

- **`isReady`** — on the first client render, `query` is `{}` and `asPath` is the pattern;
  once the router hydrates, `isReady` flips and both are real. Anything that depends on them
  waits.
- **`replace` vs `push`** — `replace` doesn't add a history entry, so a redirect to `/login`
  doesn't leave a "back" button that bounces the user straight back.

### In the wild

The gate, complete:

```tsx
// 📁 frontend/src/components/RequireAuth.tsx
export interface RequireAuthProps {
  children: ReactNode;
  /** When set, the user must hold one of these roles. */
  roles?: Role[];
  /**
   * When true, the user must be a superuser -- the same flag the admin API
   * enforces. `role` and `is_superuser` are independent columns, so a role
   * check cannot stand in for it (#84).
   */
  superuser?: boolean;
  /** Where the "Go back" link on the 403 view points. Default `/experiments`. */
  fallbackPath?: string;
  /** Custom element rendered while the session is being resolved. */
  loading?: ReactNode;
}

/**
 * Client-side gate for protected pages (the API is the real enforcement).
 *
 * - `loading`    → spinner
 * - `anonymous`  → `router.replace('/login?next=<current path>')`, or, after
 *                  a logout in this tab, to the logout's own sign-in path
 *                  (`afterLogoutPath`, plain `/login` unless it named a `next`)
 * - wrong role   → inline 403 view (`data-testid="require-auth-forbidden"`)
 */
export function RequireAuth({ children, roles, superuser, fallbackPath = '/experiments', loading }: RequireAuthProps) {
  const { status, user, afterLogoutPath } = useAuth();
  const router = useRouter();

  const { isReady, asPath } = router;

  useEffect(() => {
    if (status !== 'anonymous') return;
    // On a static export `asPath` is the route pattern (`/experiments/[id]`)
    // until the router is ready; wait so `next=` carries the real URL.
    if (!isReady) return;
    // After a logout, this page belonged to the person who signed out: as
    // `next` it took the next person to sign in there (#1006). The logout's
    // path is also where its caller navigates, so the two cannot disagree.
    if (afterLogoutPath) {
      void router.replace(afterLogoutPath);
      return;
    }
    const next = safeNextPath(asPath, '/');
    const target =
      next === '/' || next.startsWith(LOGIN_PATH)
        ? LOGIN_PATH
        : `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
    void router.replace(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, isReady, asPath, afterLogoutPath]);

  if (status === 'loading' || status === 'anonymous' || !user) {
    return (
      <>
        {loading ?? (
          <div
            data-testid="require-auth-loading"
            className="flex items-center justify-center min-h-[40vh]"
            role="status"
            aria-live="polite"
          >
            <div className="inline-block w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
            <span className="sr-only">Loading…</span>
          </div>
        )}
      </>
    );
  }

  // One 403 view, two gates. `requirement` is the only difference, so the
  // page says which condition the account failed rather than a generic refusal.
  const forbidden = (requirement: string) => (
    <div
      data-testid="require-auth-forbidden"
      className="flex flex-col items-center justify-center min-h-[40vh] gap-4 px-4 text-center"
    >
      <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">403</p>
      <p className="text-slate-700 text-lg">You do not have permission to access this page.</p>
      <p className="text-sm text-slate-500">
        Signed in as <span className="font-medium">{user.email}</span> ({user.role}). This page
        requires {requirement}.
      </p>
      <Link href={fallbackPath} className="text-blue-600 hover:underline" aria-label="Go to Home">
        Go to Home
      </Link>
    </div>
  );

  // Superuser first: it is what the admin API actually enforces, so failing it
  // is the more specific answer when a page asks for both.
  if (superuser && user.is_superuser !== true) {
    return forbidden('a superuser account');
  }

  if (roles && roles.length > 0 && !roles.includes(user.role)) {
    return forbidden(roles.join(' or '));
  }

  return <>{children}</>;
}
```

The redirect is a side effect, so it's in an effect (never in render); it waits for
`isReady`; it uses `replace`; after a logout it goes where the logout said (Hour 9's
`afterLogoutPath`), so a shared browser never carries one person's page into the next person's
sign-in; and otherwise the `next=` value goes through `safeNextPath`, which refuses anything that
isn't a same-origin absolute path — so a crafted `/login?next=//evil.com` can't bounce a user
off-site after login:

```typescript
// 📁 frontend/src/services/api.ts
/** Only allow same-origin, absolute-path redirect targets (no `//evil.com`). */
export function safeNextPath(next: string | null | undefined, fallback = '/'): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) {
    return fallback;
  }
  return next;
}
```

The same helper is used by `apiFetch`'s 401 handling, and the login page uses it to decide
where to go after a successful login. One function, three call sites, one security
property.

A dynamic route reading its parameter the strict way:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const router = useRouter();
  const { user } = useAuth();
  const { effective, loading: permissionsLoading } = useEffectivePermissions();
  const rawId = router.query.id;
  const id = typeof rawId === 'string' ? rawId : undefined;
```

And a gated page. Every admin page wraps itself with a higher-order component that closes
the loop with the nav's `superuser: true`:

```tsx
// 📁 frontend/src/pages/admin/users.tsx
export default withAdminGuard(UserManagementPage);
```

```tsx
// 📁 frontend/src/components/admin/withAdminGuard.tsx
export function withAdminGuard<P extends object>(
  Component: React.ComponentType<P>,
  options: AdminGuardOptions = {}
): React.FC<P> {
  const { requiredRole, roles, superuser = true, fallbackPath = '/experiments' } = options;
  ...
  const GuardedComponent: React.FC<P> = (props) => (
    <RequireAuth roles={allowed} superuser={superuser} fallbackPath={fallbackPath}>
      <Component {...props} />
    </RequireAuth>
  );
```

`superuser` defaults to **true**, because every endpoint under `/api/v1/admin` depends on
`get_current_superuser`. A signed-in ADMIN without the superuser flag sees "This page requires
a superuser account." — the same answer the API would give, before any request is made. The
`roles` prop is still there for pages that are gated by role, and a page that asks for both
reports the superuser failure first, because it is the one the API enforces.

The login page reads `next` back out and navigates after login:

```tsx
// 📁 frontend/src/pages/login.tsx
export const DEFAULT_AFTER_LOGIN = '/experiments';
...
  const { login, status, sessionError } = useAuth();
  ...
  const nextPath = safeNextPath(firstQueryValue(router.query.next), DEFAULT_AFTER_LOGIN);
  ...
      await login(trimmedEmail, password);
      await router.replace(nextPath);
```

`firstQueryValue` takes the first element when `next` was repeated (`string | string[]`, Hour
4). The login page also does three newer things:

- it shows `sessionError` from the auth context (Hour 9), so a sign-out caused by a 5xx or an
  unreachable API is explained, not silent;
- it maps a `400` whose detail is `Invalid host header` to `wrongHostMessage()`, which names
  the address the dashboard was opened on and the server settings that fix it
  (`PUBLIC_BASE_URL`, or `ALLOWED_HOSTS` if set);
- it imports `SsoSignIn` through the `@modules/...` alias — the SSO module's button in
  the full profile, a stub that renders nothing in core (Hour 10's seam, used for a component
  rather than a page).

> **Watch Out!** Redirecting during render (`if (anonymous) router.replace(...)` outside an
> effect) triggers "Cannot update a component while rendering a different component" and
> can loop. Side effects — including navigation — belong in effects or handlers.

> **Did You Know?** The 403 view is rendered _inline_ rather than by redirect so the user
> keeps the URL (they may have followed a link they were told to open), sees who they're
> signed in as, and has a way out. Tests find it by `data-testid="require-auth-forbidden"`.

### Try It Yourself

Log out, type `/experiments/<some id>` into the address bar, and watch the URL become
`/login?next=%2Fexperiments%2F<id>`; log in and confirm you land back on the experiment. (A typed
URL is a full page load, which starts a fresh `AuthProvider` with no `afterLogoutPath`.) Now press
**Log out** while that experiment is on screen: the URL becomes plain `/login`, with no `next=`.
Then
try `/login?next=//example.com` and confirm you land on `/experiments`, not off-site. Finally,
log in as a `VIEWER` and open `/admin` — the inline 403, not a redirect. Do the same as an
ADMIN-role user who is not a superuser: the 403 now says "requires a superuser account".

### Common mistakes

- **Navigation in render.** Effects/handlers only.
- **Trusting `next=`** without `safeNextPath` — an open redirect.
- **Reading `query` before `isReady`** on a static export.
- **`push` for redirects** — leaves a bounce loop behind the back button.

### Key takeaways

- `RequireAuth`: spinner → redirect (`replace`, after `isReady`, to the logout's own path or
  via `safeNextPath`) → inline 403 (`superuser` first, then `roles`) → children. UX, not
  enforcement.
- Admin pages are `export default withAdminGuard(Page)`, superuser-only by default, because the
  admin API is.
- `useRouter`: `pathname` (pattern), `asPath` (real), `query`, `isReady`, `push`/`replace`.
- Dynamic routes narrow `query.id` with `typeof`; guard the first render.
- One `safeNextPath` for every redirect target.

---

## Hour 13 — Environment, `apiBase()`, and the static export behind nginx

### What & why

A static export has no server, so "where is the API?" must be decided at **build time** or
by **the serving layer**. The dashboard supports both:

- **Development**: `next dev` with no `NEXT_PUBLIC_API_URL` → the client uses
  `http://localhost:8000` so a fresh clone works against a local uvicorn.
- **Container**: the image is built with `NEXT_PUBLIC_API_URL=""` → **same origin** — the
  browser calls `/api/v1/...` on the dashboard's own host, and **nginx proxies** `/api/`,
  `/ws/` and `/health*` to the API container. No CORS, no second origin, one URL to give
  users.
- **Split hosting** (dashboard on one domain, API on another): build with an absolute
  `NEXT_PUBLIC_API_URL`, and the API's `CORS_ORIGINS` must include the dashboard's origin.

`NEXT_PUBLIC_*` variables are inlined into the bundle at build time — which is why they're
build _args_ in the Dockerfile, not runtime environment, and why a change needs a rebuild.

### In the wild

The base-URL contract:

```typescript
// 📁 frontend/src/services/api.ts
/** Where `next dev` finds a locally started backend when nothing is configured. */
export const DEV_API_URL = 'http://localhost:8000';

/**
 * API origin without a trailing slash. Empty string means same-origin (the
 * container image's nginx proxies `/api/` to the backend). `next dev` with no
 * `NEXT_PUBLIC_API_URL` falls back to `http://localhost:8000` so a fresh clone
 * works against `uvicorn --port 8000`; production builds never do.
 */
export function apiBase(): string {
  const raw = process.env.NEXT_PUBLIC_API_URL;
  if (raw === undefined || raw === null) {
    return process.env.NODE_ENV === 'development' ? DEV_API_URL : '';
  }
  return raw.replace(/\/+$/, '');
}

/**
 * WebSocket origin (`ws://host` / `wss://host`). Uses `NEXT_PUBLIC_WS_URL` when
 * set, otherwise derives it from `apiBase()` or, for same-origin, from
 * `window.location`.
 */
export function wsBase(): string {
  const explicit = process.env.NEXT_PUBLIC_WS_URL;
  if (explicit) return explicit.replace(/\/+$/, '');
  const base = apiBase();
  if (base) return base.replace(/^http/i, 'ws');
  if (typeof window !== 'undefined' && window.location) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}`;
  }
  return 'ws://localhost:8000';
}
```

"Production builds never do" — the dev fallback is gated on `NODE_ENV === 'development'`, so
a production bundle with a missing variable is same-origin, not "some laptop's port 8000."
`wsBase` derives `ws://`/`wss://` from the same setting, or from `window.location` for the
same-origin case.

The `apiFetch` defence for the classic same-origin misconfiguration — nginx serving the SPA
shell for `/api/...` when the proxy isn't wired:

```typescript
// 📁 frontend/src/services/api.ts
  const contentType =
    headers && typeof headers.get === 'function' ? headers.get('content-type') : null;
  if (contentType && !/json/i.test(contentType)) {
    // A non-JSON 2xx usually means a proxy served the SPA shell instead of the API.
    const text = typeof response.text === 'function' ? await response.text().catch(() => '') : '';
    if (!text.trim()) return undefined as T;
    throw new ApiError({
      status,
      detail: `Expected a JSON response from ${path} but received ${contentType}`,
    });
  }
```

and a message that names the fix when the API is unreachable:

```typescript
// 📁 frontend/src/services/api.ts
export function unreachableMessage(): string {
  const base = apiBase();
  const where =
    base || (typeof window !== 'undefined' && window.location ? window.location.origin : 'the API');
  const message =
    `Can't reach the API at ${where}. Check that the backend is running and that ` +
    "this dashboard's origin is listed in CORS_ORIGINS.";
  return process.env.NODE_ENV === 'development' ? `${message} See docs → Quick start.` : message;
}
```

The browser reports "backend down" and "backend up but no CORS header for this origin" the
same way (a failed `fetch`), so the message names both causes. The pointer to the quick-start
guide is added only in development, where the reader is the person running the stack. It is
exported because the login page and `sessionErrorMessage` (Hour 9) use the same words.

The image, whose header is the deployment contract:

```dockerfile
# 📁 frontend/Dockerfile
#   docker build -f frontend/Dockerfile -t experimently-web:core .
#   docker build -f frontend/Dockerfile -t experimently-web:full \
#       --build-arg EXPERIMENTLY_PROFILE=full .
#
# The build context is the REPOSITORY ROOT (like backend/Dockerfile): the
# modules' dashboard code lives in modules/frontend/, next to frontend/, not
# inside it. (The documentation is no longer rendered in-app, so docs/ is not
# copied. The dashboard's docs links go to the Markdown under docs/ on GitHub,
# unless NEXT_PUBLIC_DOCS_URL below names a built MkDocs site.)
#
# EXPERIMENTLY_PROFILE selects what goes in:
#   core (default)  never reads modules/frontend/ — a core checkout, which has
#                   no modules/ at all, builds unchanged — and the `@modules/*`
#                   alias resolves to the stub tree (frontend/modules-alias.js).
#   full            copies modules/frontend/ into the image and builds the
#                   full-profile dashboard; the build fails if the tree is
#                   missing.
...
# Stage 1 builds the Next.js static export (output: 'export' -> out/) and
# generates the nginx location blocks for the dynamic routes from
# .next/routes-manifest.json. Stage 2 serves it with nginx-unprivileged on
# port 8080 and proxies /api/, /ws/ and /health* to API_UPSTREAM (default
# http://api:8000), so the browser talks to a single origin and the API base
# baked into the bundle can stay empty (NEXT_PUBLIC_API_URL="").
...
# Relative API base by default: nginx proxies /api/ to the backend. Pass an
# absolute URL only when the dashboard is served from a different origin.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
# Where the dashboard's docs links go (frontend/src/services/docs.ts). Empty,
# the default here, means the Markdown under docs/ on GitHub's main branch. Set
# it to the origin of a built MkDocs site (the published one, or one this
# deployment hosts) to link there instead; only `npm run build:marketing` sets it.
ARG NEXT_PUBLIC_DOCS_URL=""
ENV NEXT_PUBLIC_DOCS_URL=${NEXT_PUBLIC_DOCS_URL}

RUN npm run build \
    && test -f .next/routes-manifest.json \
    && node scripts/nginx-routes.mjs --out /app/nginx/routes.conf \
    && test -d out \
    ...
```

(The Dockerfile's comment and `docs.ts` now agree: empty means the GitHub repository, and only
the marketing build points at a built site. Two old-name leftovers are worth recognising when you
read it: the local tags in the header comment, `experimently-web:core`, are just examples of
`-t`, since the published images are `ghcr.io/controlvar/controlvar-web` and
`ghcr.io/controlvar/controlvar`; and the build argument is still `EXPERIMENTLY_PROFILE`, because
the rename left identifiers like that alone.) The profile is a build argument with `core` as the default, so a plain
`docker build` works on a checkout with no `modules/` at all. Hour 23 covers the stages that
pick the modules tree and the check that the bundle matches the profile.

And the script that makes dynamic routes work on nginx — the part of "static export" that
isn't obvious:

```js
// 📁 frontend/scripts/nginx-routes.mjs
/**
 * Generate nginx `location` blocks for the dynamic routes of the static export.
 *
 * `next build` with `output: 'export'` writes one HTML file per page, including
 * the dynamic ones (`out/experiments/[id].html`, `out/results/[id].html`, ...).
 * nginx knows nothing about Next's routing, so `/results/abc` would 404 (or, with
 * a naive `try_files ... /index.html` fallback, render the wrong page). This script
 * reads `.next/routes-manifest.json` — the routing table Next itself uses — and
 * emits one regex `location` per dynamic route that serves the matching
 * `[param].html` file, while still letting a static page with the same prefix win
 * (`/experiments/new` -> `experiments/new.html`, not `[id].html`).
 */
```

The compose service wires the two pieces: an empty build arg, and the upstream for nginx:

```yaml
# 📁 docker-compose.yml
  frontend:
    build:
      context: .
      dockerfile: frontend/Dockerfile
      args:
        EXPERIMENTLY_PROFILE: ${EXPERIMENTLY_PROFILE:-core}
        # Relative: the dashboard calls /api/... on its own origin and nginx
        # proxies to the api container.
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-}
    image: *web-image
    environment:
      API_UPSTREAM: http://api:8000
      # connect-src of the CSP; add the API origin when NEXT_PUBLIC_API_URL is absolute.
      CSP_CONNECT_SRC: "${CSP_CONNECT_SRC:-'self' ws: wss:}"
    ports:
      - "127.0.0.1:${FRONTEND_HOST_PORT:-3000}:8080"
```

(The port is published on `127.0.0.1` only: the development stack is for your laptop, not your
network.)

> **Watch Out!** `NEXT_PUBLIC_*` is baked in at build time. Setting it as a _runtime_
> environment variable on the container does nothing; the bundle already has its value. To
> point a built image at a different API origin you rebuild — or keep it same-origin and
> change nginx's `API_UPSTREAM`, which _is_ runtime. That's the strongest argument for the
> same-origin design.

> **Did You Know?** One build-time variable decides *which site* the code is.
> `NEXT_PUBLIC_SITE_MODE` defaults to `platform` (the dashboard a self-hoster runs);
> `npm run build:marketing` sets it to `marketing` for the public website, which has no API
> behind it. In that mode the shell shows the public site's own header instead of the dashboard's
> (`SiteHeader`: Docs, Power calculator and Source, and no sign-in button, because there is
> nothing to sign in to; `frontend/src/utils/site-mode.ts` explains why a dead sign-in button "reads
> as a broken product"), and `frontend/scripts/prune-marketing-site.mjs` then deletes the
> dashboard routes from `out/` — refusing to run unless the mode is `marketing`, and refusing
> when it matched nothing, so it can never quietly delete the dashboard from a platform build.

> **Did You Know?** The three CORS-related things you'll meet are one design decision seen
> from three sides: the API's `CORS_ORIGINS` includes 3000/3100/3200/3300 for the dev
> servers; the container image avoids CORS entirely by being same-origin; and the
> `X-API-Key` header is in the API's `allow_headers` for the browser SDKs that _do_ cross
> origins (ShopLab on 3200 → API on 8000).

### Try It Yourself

```bash
cd frontend
NEXT_PUBLIC_API_URL="" npm run build && ls out | head          # the static export
node ./scripts/nginx-routes.mjs --check | head -20               # the generated location blocks
grep -rl 'http://localhost:8000' out/_next/static || echo "not baked in: production build"
NEXT_PUBLIC_API_URL="https://api.example.com" npm run build && grep -rl "api.example.com" out/_next/static
```

Then run the compose stack and prove the proxy: `curl -si localhost:3000/health/ready`
(nginx → api; the API mounts its health probes at the root, not under `/api/v1`) vs
`curl -si localhost:3000/healthz` (nginx's own).

### Common mistakes

- **Expecting `NEXT_PUBLIC_API_URL` to work at runtime.** Build arg; rebuild.
- **An absolute API URL in the image without adding the dashboard's origin to
  `CORS_ORIGINS`.**
- **A `try_files … /index.html` fallback** for dynamic routes — the wrong page renders.
- **Building the dashboard image with `frontend/` as the context** — the Dockerfile copies
  `frontend/...` and `modules*/frontend` paths relative to the repository root, so the build
  fails; and a full build needs `modules/frontend/` beside `frontend/`.

### Key takeaways

- `apiBase()`: dev → `localhost:8000`; unset in production → same origin; explicit → as
  given. `wsBase()` follows.
- The image is a static export behind nginx-unprivileged on 8080; nginx proxies `/api/`,
  `/ws/`, `/health*` to `API_UPSTREAM`; dynamic routes get generated `location` blocks.
- `NEXT_PUBLIC_*` is baked at build; the build context is the repo root (for
  `modules/frontend/` and `frontend/tsconfig.core.json`); `EXPERIMENTLY_PROFILE` (default
  `core`) picks the profile.
- `apiFetch` detects the SPA-shell-for-API misconfiguration and names the fix when
  unreachable.

---

## Part III Workshop

**Q1. What does `src/pages/feature-flags/[id].tsx` handle, and what's `router.query.id` on
the first static render?**
`/feature-flags/<anything>`; `undefined` (the query is `{}` until the router hydrates), which
is why every dynamic page has a loading branch.

**Q2. Which routes are `open`, which are `bare`, and what does each mean?**
`open`: `/docs/*`, `/power-calculator`, `/privacy`, `/404`, `/500`, `/_error` — rendered inside the shell
but without `RequireAuth`. `bare`: `/login`, `/` and `/sso/complete` — no shell and no gate
(`/sso/complete` must never be gated, or the SSO hand-off is lost on the way to `/login`). New
routes are `protected` by default.

**Q3. Why does `RequireAuth` wait for `router.isReady` before redirecting?**
On a static export `asPath` is the route pattern (`/experiments/[id]`) until the router is
ready; waiting makes `next=` carry the real URL so login returns the user to the right
page.

**Q4. What stops `/login?next=//evil.com` from redirecting off-site?**
`safeNextPath`, which accepts only same-origin absolute paths (starting with `/`, not `//`
or `/\`) and falls back otherwise — used by `RequireAuth`, `apiFetch`'s 401 handling and the
login page.

**Q5. `NEXT_PUBLIC_API_URL=""` in the image — how does the browser reach the API?**
Same origin: requests go to `/api/v1/...` on the dashboard's host, and nginx proxies
`/api/` to `API_UPSTREAM` (`http://api:8000`). No CORS involved.

**Q6. Why is the dashboard's Docker build context the repository root?**
The modules' dashboard code lives in `modules/frontend/`, beside `frontend/`, and a full-profile
image must copy it; the core stage also needs `frontend/tsconfig.core.json` as a file that is
always present. (It used to be for `docs/`, which the dashboard rendered at build time; it no
longer renders docs and does not copy `docs/`.)

**Q7. A DEVELOPER-role user reports that "Admin" is missing from their nav. Bug?**
No. The Admin item is `superuser: true`, matching the admin API, which requires a superuser
account for every endpoint. Role and superuser are independent; make the account a superuser
if it should administer the instance.

**Quiz.** `router.pathname` vs `router.asPath` for `/experiments/3f9a`?
→ `pathname` is `/experiments/[id]` (the pattern); `asPath` is `/experiments/3f9a` (the
real path, once `isReady`).

---

## Exercises

**E3.1 — A new protected page.** Add `/rollouts/[id]` that reads the id strictly, shows a
loading branch, and renders a `RolloutDetail` component. What, if anything, must change in
`routes.ts`?

**E3.2 — An open page.** Add `/status` — a public page showing `/health/ready` — and make it
`open`. Which two files change, and why must the API call use `auth: false`?

**E3.3 — Narrow a guard.** The admin safety page is `export default withAdminGuard(SafetyDashboard)`.
Change it so that only a superuser who **also** holds the `ADMIN` role can see it, with the
"Go back" link pointing at `/admin`. Show the new export, the `RequireAuth` it renders, and say
whether the nav entry changes.

**E3.4 — Base URL matrix.** For each deployment — `next dev` with nothing set, the compose
image, a Vercel-hosted dashboard with the API at `api.example.com` — state `apiBase()`'s
value, `wsBase()`'s value, and whether `CORS_ORIGINS` needs the dashboard's origin.

**E3.5 — Debug it.** `curl localhost:3000/api/v1/experiments/` returns HTML with `<title>
Controlvar</title>` and status 200. What's happening, how does `apiFetch` surface it, and
what's the fix?

---

## Solutions

**S3.1**

```tsx
// src/pages/rollouts/[id].tsx
export default function RolloutPage() {
  const router = useRouter();
  const rawId = router.query.id;
  const id = typeof rawId === 'string' ? rawId : undefined;
  if (!id) return <div data-testid="rollout-loading">Loading…</div>;
  return (<div className="flex-1 bg-slate-50"><PageTitle title="Rollout" /><RolloutDetail id={id} /></div>);
}
```

Nothing in `routes.ts`: `/rollouts/[id]` isn't bare or open, so it's protected by default.

**S3.2** — `src/pages/status.tsx` (the page) and `src/utils/routes.ts` (add `'/status'` to
`OPEN_ROUTE_PREFIXES`). The call must be `apiFetch('/health/ready', { auth: false,
redirectOn401: false })`: there's no session on an open page, and `/health/ready` isn't
authenticated — with `auth: true` a stale token would still be sent (harmless) but a 401
elsewhere could trigger the redirect.

**S3.3**

```tsx
// src/pages/admin/safety.tsx
export default withAdminGuard(SafetyDashboard, { roles: ['ADMIN'], fallbackPath: '/admin' });

// which renders:
<RequireAuth roles={['ADMIN']} superuser={true} fallbackPath="/admin">
  <SafetyDashboard />
</RequireAuth>
```

`superuser` stays at its default, `true`, and `roles` narrows further. A superuser with the
DEVELOPER role now sees "This page requires ADMIN."; a non-superuser ADMIN still sees "requires
a superuser account", because the superuser check runs first. The nav entry does not change:
`{ label: 'Admin', href: '/admin', testId: 'nav-admin', superuser: true }` opens the area, and
the other admin pages are still open to every superuser.

**S3.4** — `next dev`, nothing set: `apiBase()` = `http://localhost:8000`, `wsBase()` =
`ws://localhost:8000`, CORS needs `http://localhost:3100` (it's in the defaults). Compose
image: `""` (same origin), `ws://<host>` from `window.location`, no CORS. Vercel + external
API: `https://api.example.com`, `wss://api.example.com`, and `CORS_ORIGINS` must include the
Vercel origin.

**S3.5** — nginx is serving the SPA shell for an `/api/` path: the proxy location isn't
configured (wrong `API_UPSTREAM`, missing `include` of the routes/proxy conf, or the request
hit a static host with a `try_files` fallback). `apiFetch` checks the `content-type` of a 2xx
and throws `ApiError` with `Expected a JSON response from /api/v1/experiments/ but received
text/html`. Fix the proxy (`API_UPSTREAM=http://api:8000`, the `/api/` location) or build with
an absolute `NEXT_PUBLIC_API_URL` plus CORS.

---

**Next:** [Part IV — Data & Forms →](./part-4-data-and-forms.md)

# Part III — Next.js

### Hours 10–13

[← Part II](./part-2-react.md) · [Contents](./README.md) · [Part IV →](./part-4-data-and-forms.md)

Next.js is the framework around React that gives the dashboard routing, a build, a dev server
and a production artefact. Four hours: files are routes (Hour 10); `_app.tsx`, the shell, and
how a route's _kind_ decides what wraps it (Hour 11); the client-side auth gate, `useRouter`,
and dynamic routes (Hour 12); and the environment/base-URL contract plus the static export
that ships behind nginx (Hour 13). The dashboard uses the **pages router** (`src/pages/`), not
the app router — deliberately, and this Part explains why.

---

## Hour 10 — The pages router: files are routes

### What & why

In the pages router, **the filesystem is the routing table**: `src/pages/experiments/index.tsx`
is `/experiments`, `src/pages/experiments/[id].tsx` is `/experiments/:id`,
`src/pages/docs/[...slug].tsx` is `/docs/*`. A page is a file with a **default export**
component; everything else exported from it is just a module export (the tests import
`ACTIONS_BY_STATUS` and `validateForm` from page files that way).

Two special files: `_app.tsx` wraps every page (Hour 11), and `_document.tsx` (absent here)
would customise the HTML shell. Navigation between pages is client-side via `next/link`
(`<Link href="/experiments">`), which prefetches and swaps without a full reload;
`next/router`'s `useRouter()` gives the current path, query and programmatic `push`/`replace`.

#### Why the pages router, and why a static export

The dashboard is a **client-rendered application**: every page fetches from the API in the
browser with the user's bearer token. There's no server-side data fetching, no server
components, no per-request rendering — so the app router's main features aren't used, and
`output: 'export'` turns the whole thing into static HTML + JS that nginx can serve. The
one exception, `/docs/[...slug]`, uses `getStaticProps` to render markdown _at build time_
from the repository's `docs/` folder. Pages router + static export is the simplest thing
that fits, and simple is what a dashboard for an API should be.

### In the wild

The route table, as files:

```
frontend/src/pages/
├── _app.tsx                 # wraps every page (providers, shell, auth gate)
├── index.tsx                # /
├── login.tsx                # /login
├── power-calculator.tsx     # /power-calculator (public)
├── experiments/
│   ├── index.tsx            # /experiments
│   ├── new.tsx              # /experiments/new
│   └── [id].tsx             # /experiments/:id
├── feature-flags/
│   ├── index.tsx · new.tsx · [id].tsx
├── results/
│   ├── index.tsx · [id].tsx
├── admin/
│   ├── index.tsx · users.tsx · api-keys.tsx · roles.tsx · safety.tsx · scheduler.tsx · audit.tsx · notifications.tsx
├── workspaces/
│   ├── index.tsx · new.tsx · [id]/ · invites/
└── docs/
    ├── index.tsx
    └── [...slug].tsx        # /docs/* — markdown rendered at build time
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

The build-time page — the one non-client-rendered route:

```tsx
// 📁 frontend/src/pages/docs/[...slug].tsx
import fs from 'fs';
import path from 'path';
import { GetStaticPaths, GetStaticProps } from 'next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const SLUG_TO_FILE: Record<string, string> = {
  // Getting Started
  'quick-start':                   'getting-started/quick-start.md',
  'concepts':                      'getting-started/concepts.md',
  ...
  // SDKs
  'sdks/javascript':               'sdk/javascript.md',
  'sdks/python':                   'sdk-guide.md',
  ...
};
```

`getStaticPaths` enumerates the slugs; `getStaticProps` reads `../docs/<file>` with `fs` —
which only works at build time in Node, never in the browser — and passes the markdown as a
prop. That's why the dashboard's Docker build context is the repository root: the image
needs `docs/` next to `frontend/`, and a missing file fails the build rather than shipping
"Documentation coming soon."

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
// 📁 frontend/src/pages/experiments/new.tsx (shape)
  const router = useRouter();
  ...
      const created = await ExperimentsService.create(payload);
      void router.push(`/experiments/${created.id}`);
```

`next.config.js` is four lines that decide the artefact:

```js
// 📁 frontend/next.config.js
module.exports = {
  reactStrictMode: true,
  output: 'export',
  images: {
    unoptimized: true,
  },
  // Security headers are applied at the serving layer (nginx/edge/CDN) for
  // static export deployments.
};
```

`reactStrictMode` (double-invoked effects in dev — Hour 7's cleanup test), `output:
'export'` (static HTML into `out/`), `images.unoptimized` (no image server in a static
export), and a comment that says where security headers live instead.

> **Watch Out!** A page file's _default_ export is the page; a _named_ export (like
> `generateKey` or `ACTIONS_BY_STATUS`) is fine for tests but must not be a React component
> Next.js would try to treat as a page. And a file under `src/pages/` that isn't a page
> (a helper) becomes a broken route — put helpers under `components/`, `utils/` or `hooks/`.

> **Did You Know?** `next dev -p 3100` — the dashboard's dev script uses 3100 so it can run
> beside the compose stack's `:3000`. Both origins are in the API's CORS defaults. The
> demo apps take 3200 and 3300 for the same reason.

### Try It Yourself

Add a throwaway page `src/pages/scratch.tsx` with a default export that renders
`useRouter().pathname` and a `<Link href="/experiments">`; open `/scratch`; click the link
and watch the URL change without a reload (the network tab shows no document request). Then
rename it to `src/pages/scratch/[name].tsx`, visit `/scratch/hello`, and print `query.name`.
Delete it after.

### Common mistakes

- **Helpers under `src/pages/`** — they become routes.
- **Reading `query.id` without a loading guard** — it's empty on the first static render.
- **`<a href>` instead of `<Link>`** for internal navigation — full reload, lost state.
- **`fs`/`path` in a client component** — only in `getStaticProps`/`getStaticPaths`.

### Key takeaways

- `src/pages/**` is the route table; default export = page; `[id]` and `[...slug]` are
  dynamic; `_app.tsx` wraps all.
- Pages are thin: route + title + feature component; dynamic pages guard the
  not-yet-hydrated `query`.
- Client-rendered app + `output: 'export'`; only `/docs/*` renders at build time from
  `docs/`.
- `<Link>` and `router.push` for navigation; `next dev -p 3100`.

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
| `bare`      | no                 | no                        | `/login`, `/`                                           |
| `open`      | yes                | no                        | `/docs/*`, `/power-calculator`, `/404`, `/500`, `/_error` |
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
/** Routes rendered without the application shell. */
const BARE_ROUTES = new Set<string>(['/login', '/']);

/**
 * Routes inside the shell that do not require a session. The API is still the
 * enforcement point; these pages are either static or call public endpoints
 * (the power calculator).
 */
const OPEN_ROUTE_PREFIXES = ['/docs', '/power-calculator', '/404', '/500', '/_error'];

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
the fail-closed choice, mirrored from the backend's settings.

The app component, composing the wrappers from the kind:

```tsx
// 📁 frontend/src/pages/_app.tsx
import "@/styles/globals.css";
import type { AppProps } from "next/app";
import Head from "next/head";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AuthProvider } from "@/contexts/AuthContext";
import { NotificationProvider } from "@/contexts/NotificationContext";
import { ToastContainer } from "@/components/ToastContainer";
import { AppShell } from "@/components/AppShell";
import { RequireAuth } from "@/components/RequireAuth";
import { routeKind } from "@/utils/routes";

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
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
        <title>Experimently</title>
      </Head>
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

Read the nesting from outside in: an `ErrorBoundary` (a class component that catches render
errors and shows a fallback instead of a blank page), `<Head>` (title/favicon — pages
override the title with `<PageTitle>`), the auth and notification providers, then the page
wrapped by its kind, then the toasts rendered once at the root so they overlay everything.
`page` is built up by reassignment — a JSX value is just a value.

The shell — nav items with role gating, and the active-link rule:

```tsx
// 📁 frontend/src/components/AppShell.tsx (nav table)
const NAV_ITEMS: NavItem[] = [
  { label: 'Experiments', href: '/experiments', testId: 'nav-experiments' },
  { label: 'Feature Flags', href: '/feature-flags', testId: 'nav-feature-flags' },
  { label: 'Admin', href: '/admin', testId: 'nav-admin', roles: ['ADMIN', 'DEVELOPER'] },
  { label: 'Docs', href: '/docs', testId: 'nav-docs' },
];

export function isNavActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}
```

The nav hides "Admin" from analysts and viewers with `hasRole` — a convenience, not a
control; the `/admin/*` endpoints return 403 regardless. `isNavActive` is exported for its
test, and it's the same prefix logic as `routeKind`. The shell also renders a skip link
(`href="#main-content"`) and `aria-current="page"` on the active item — accessibility that
the Playwright a11y suite checks.

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
- **Adding a nav item without a `roles` entry** for an admin-only page — the page still 403s
  on the API, but the link is confusing.
- **Using `asPath` where `pathname` is meant** (or vice versa).
- **Providers inside pages** — mount them once in `_app`.

### Key takeaways

- `_app.tsx` mounts providers, global CSS, `<Head>`, the error boundary, and wraps the page
  by its kind.
- `routeKind(pathname)` → `bare | open | protected`, protected by default; `AppShell` for
  non-bare, `RequireAuth` for protected.
- Nav gating by role is UX; the API enforces.
- `pathname` is the pattern, `asPath` the real path.

---

## Hour 12 — `RequireAuth`, `useRouter`, and dynamic routes

### What & why

`RequireAuth` is the client-side gate for protected pages. It reads `useAuth()`'s three-state
status and does one of four things: show a spinner (`loading`), redirect to
`/login?next=<current path>` (`anonymous`), show an inline 403 (`wrong role`), or render the
children. Its comment says the important thing — _"the API is the real enforcement"_ — the
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
  /** Where the "Go back" link on the 403 view points. Default `/experiments`. */
  fallbackPath?: string;
  /** Custom element rendered while the session is being resolved. */
  loading?: ReactNode;
}

/**
 * Client-side gate for protected pages (the API is the real enforcement).
 *
 * - `loading`    → spinner
 * - `anonymous`  → `router.replace('/login?next=<current path>')`
 * - wrong role   → inline 403 view (`data-testid="require-auth-forbidden"`)
 */
export function RequireAuth({ children, roles, fallbackPath = '/experiments', loading }: RequireAuthProps) {
  const { status, user } = useAuth();
  const router = useRouter();

  const { isReady, asPath } = router;

  useEffect(() => {
    if (status !== 'anonymous') return;
    // On a static export `asPath` is the route pattern (`/experiments/[id]`)
    // until the router is ready; wait so `next=` carries the real URL.
    if (!isReady) return;
    const next = safeNextPath(asPath, '/');
    const target =
      next === '/' || next.startsWith(LOGIN_PATH)
        ? LOGIN_PATH
        : `${LOGIN_PATH}?next=${encodeURIComponent(next)}`;
    void router.replace(target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, isReady, asPath]);

  if (status === 'loading' || status === 'anonymous' || !user) {
    return (
      <>
        {loading ?? (
          <div data-testid="require-auth-loading" className="flex items-center justify-center min-h-[40vh]" role="status" aria-live="polite">
            <div className="inline-block w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
            <span className="sr-only">Loading…</span>
          </div>
        )}
      </>
    );
  }

  if (roles && roles.length > 0 && !roles.includes(user.role)) {
    return (
      <div data-testid="require-auth-forbidden" className="flex flex-col items-center justify-center min-h-[40vh] gap-4 px-4 text-center">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">403</p>
        <p className="text-slate-700 text-lg">You do not have permission to access this page.</p>
        <p className="text-sm text-slate-500">
          Signed in as <span className="font-medium">{user.email}</span> ({user.role}). This page
          requires {roles.join(' or ')}.
        </p>
        <Link href={fallbackPath} className="text-blue-600 hover:underline" aria-label="Go to Home">
          Go to Home
        </Link>
      </div>
    );
  }

  return <>{children}</>;
}
```

The redirect is a side effect, so it's in an effect (never in render); it waits for
`isReady`; it uses `replace`; and the `next=` value goes through `safeNextPath`, which
refuses anything that isn't a same-origin absolute path — so a crafted
`/login?next=//evil.com` can't bounce a user off-site after login:

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
  const rawId = router.query.id;
  const id = typeof rawId === 'string' ? rawId : undefined;
```

And a role-gated page — the `roles` prop closes the loop with the nav's `roles` entries:

```tsx
// (shape used by admin pages)
<RequireAuth roles={['ADMIN', 'DEVELOPER']}>
  <AdminUsersPage />
</RequireAuth>
```

The login page reads `next` back out and navigates after login:

```tsx
// 📁 frontend/src/pages/login.tsx (shape)
  const { login } = useAuth();
  const router = useRouter();
  ...
      await login(email, password);
      const next = safeNextPath(typeof router.query.next === 'string' ? router.query.next : null, '/experiments');
      void router.replace(next);
```

> **Watch Out!** Redirecting during render (`if (anonymous) router.replace(...)` outside an
> effect) triggers "Cannot update a component while rendering a different component" and
> can loop. Side effects — including navigation — belong in effects or handlers.

> **Did You Know?** The 403 view is rendered _inline_ rather than by redirect so the user
> keeps the URL (they may have followed a link they were told to open), sees who they're
> signed in as, and has a way out. Tests find it by `data-testid="require-auth-forbidden"`.

### Try It Yourself

Log out, open `/experiments/<some id>`, and watch the URL become
`/login?next=%2Fexperiments%2F<id>`; log in and confirm you land back on the experiment. Then
try `/login?next=//example.com` and confirm you land on `/experiments`, not off-site. Finally,
log in as a `VIEWER` and open `/admin` — the inline 403, not a redirect.

### Common mistakes

- **Navigation in render.** Effects/handlers only.
- **Trusting `next=`** without `safeNextPath` — an open redirect.
- **Reading `query` before `isReady`** on a static export.
- **`push` for redirects** — leaves a bounce loop behind the back button.

### Key takeaways

- `RequireAuth`: spinner → redirect (`replace`, after `isReady`, via `safeNextPath`) → inline
  403 by `roles` → children. UX, not enforcement.
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
  const contentType = headers && typeof headers.get === 'function' ? headers.get('content-type') : null;
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
function unreachableMessage(): string {
  const base = apiBase();
  const where = base || (typeof window !== 'undefined' && window.location ? window.location.origin : 'the API');
  return `Can't reach the API at ${where}. Is the backend running? See docs → Quick start.`;
}
```

The image, whose header is the deployment contract:

```dockerfile
# 📁 frontend/Dockerfile
# The build context is the REPOSITORY ROOT (like backend/Dockerfile): the
# in-app documentation pages (/docs/*) are rendered at build time from the
# markdown under docs/, which lives next to frontend/, not inside it.
#
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
RUN npm run build \
    && test -f .next/routes-manifest.json \
    && node scripts/nginx-routes.mjs --out /app/nginx/routes.conf \
    && test -d out
```

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
      context: ./frontend
      dockerfile: Dockerfile
      args:
        # Relative: the dashboard calls /api/... on its own origin and nginx
        # proxies to the api container.
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-}
    image: *web-image
    environment:
      API_UPSTREAM: http://api:8000
    ports:
      - "${FRONTEND_HOST_PORT:-3000}:8080"
```

> **Watch Out!** `NEXT_PUBLIC_*` is baked in at build time. Setting it as a _runtime_
> environment variable on the container does nothing; the bundle already has its value. To
> point a built image at a different API origin you rebuild — or keep it same-origin and
> change nginx's `API_UPSTREAM`, which _is_ runtime. That's the strongest argument for the
> same-origin design.

> **Did You Know?** The three CORS-related things you'll meet are one design decision seen
> from three sides: the API's `CORS_ORIGINS` includes 3000/3100/3200/3300 for the dev
> servers; the container image avoids CORS entirely by being same-origin; and the
> `X-API-Key` header is in the API's `allow_headers` for the browser SDKs that _do_ cross
> origins (ShopLab on 3200 → API on 8000).

### Try It Yourself

```bash
cd frontend
NEXT_PUBLIC_API_URL="" npm run build && ls out | head          # the static export
node scripts/nginx-routes.mjs --check | head -20                 # the generated location blocks
grep -o 'http://localhost:8000' out/_next/static/chunks/*.js | head -1 || echo "not baked in: production build"
NEXT_PUBLIC_API_URL="https://api.example.com" npm run build && grep -l "api.example.com" out/_next/static/chunks/*.js | head -1
```

Then run the compose stack and prove the proxy: `curl -si localhost:3000/api/v1/health/ready`
(nginx → api) vs `curl -si localhost:3000/healthz` (nginx's own).

### Common mistakes

- **Expecting `NEXT_PUBLIC_API_URL` to work at runtime.** Build arg; rebuild.
- **An absolute API URL in the image without adding the dashboard's origin to
  `CORS_ORIGINS`.**
- **A `try_files … /index.html` fallback** for dynamic routes — the wrong page renders.
- **Building the dashboard image with `frontend/` as the context** — the docs pages have no
  markdown and the build fails.

### Key takeaways

- `apiBase()`: dev → `localhost:8000`; unset in production → same origin; explicit → as
  given. `wsBase()` follows.
- The image is a static export behind nginx-unprivileged on 8080; nginx proxies `/api/`,
  `/ws/`, `/health*` to `API_UPSTREAM`; dynamic routes get generated `location` blocks.
- `NEXT_PUBLIC_*` is baked at build; the build context is the repo root (for `docs/`).
- `apiFetch` detects the SPA-shell-for-API misconfiguration and names the fix when
  unreachable.

---

## Part III Workshop

**Q1. What does `src/pages/feature-flags/[id].tsx` handle, and what's `router.query.id` on
the first static render?**
`/feature-flags/<anything>`; `undefined` (the query is `{}` until the router hydrates), which
is why every dynamic page has a loading branch.

**Q2. Which routes are `open`, and what does `open` mean?**
`/docs/*`, `/power-calculator`, `/404`, `/500`, `/_error`: rendered inside the shell but
without `RequireAuth`. New routes are `protected` by default.

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
`/docs/[...slug].tsx` reads `../docs/*.md` at build time via `getStaticProps`; the markdown
lives next to `frontend/`, so the context must include both.

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

**E3.3 — Role gate.** Wrap the admin safety page so only `ADMIN` can see it, with the "Go
back" link pointing at `/admin`. Show the `RequireAuth` usage and the matching nav entry.

**E3.4 — Base URL matrix.** For each deployment — `next dev` with nothing set, the compose
image, a Vercel-hosted dashboard with the API at `api.example.com` — state `apiBase()`'s
value, `wsBase()`'s value, and whether `CORS_ORIGINS` needs the dashboard's origin.

**E3.5 — Debug it.** `curl localhost:3000/api/v1/experiments/` returns HTML with `<title>
Experimently</title>` and status 200. What's happening, how does `apiFetch` surface it, and
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
export default function SafetyPage() {
  return (
    <RequireAuth roles={['ADMIN']} fallbackPath="/admin">
      <SafetySettingsForm />
    </RequireAuth>
  );
}
// AppShell NAV_ITEMS: { label: 'Admin', href: '/admin', testId: 'nav-admin', roles: ['ADMIN', 'DEVELOPER'] }
```

(The nav entry stays broad — developers can see other admin pages — while this page narrows
to `ADMIN`.)

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

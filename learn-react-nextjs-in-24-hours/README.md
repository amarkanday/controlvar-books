# Learn TypeScript, React & Next.js in 24 Hours

### _The Controlvar Edition — Teach Yourself the Dashboard Stack Using a Real Codebase_

> **Preface.** Most React tutorials build a counter and a todo list and stop before the parts
> a real dashboard is made of — a typed API client that handles a 401, an auth context that
> never trusts a stored token, forms that mirror server validation, a WebSocket hook with
> reconnect, charts that render in tests, a rule builder that emits the exact JSON a backend
> expects. This book is different. Every concept is taught **twice**: once in the abstract,
> and once with a real snippet pulled from the **Controlvar** dashboard (a Next.js 16
> pages-router app on React 19, built with Turbopack; strict TypeScript; about 2,600 Jest test
> cases counted from `it(` and `test(` calls under `frontend/src` and `modules/frontend/src`;
> Playwright browser journeys that gate every pull request, plus a nightly accessibility
> suite). By Hour 24 you'll be able to _write_ a page in this codebase, not just
> read one — and you'll have met the same rule twice in two voices: the client's "Variant
> allocations must add up to 100%" and the server's "Traffic allocations must sum to 100%"
> (Hour 15).

---

## Who this book is for

- **Backend engineers** on Controlvar who need to add or change a dashboard page and want
  to do it in the house style.
- **Developers new to React or TypeScript** who know some JavaScript and keep bouncing off
  hooks, generics, and "why does my effect run twice."
- **Reviewers** who want one reference for the dashboard's conventions — one fetch wrapper,
  services mirroring the API, thin pages, mocked `fetch` in tests — so review is about
  substance.

You need: working JavaScript (functions, objects, arrays, promises, ES modules). You do
**not** need prior TypeScript, React or Next.js.

## How to use this book

It's structured as **24 one-hour lessons**, grouped into six Parts. Each Hour follows the
same rhythm so you can pace yourself:

1. **What & why** — the concept and the problem it solves.
2. **In the wild** — the real Controlvar code that uses it (file path included so you can
   open it).
3. **Try It Yourself** — a short exercise in the [TypeScript Playground](https://www.typescriptlang.org/play),
   a scratch component, or the running dashboard.
4. **Common mistakes** and **Key takeaways**.

Each Part ends with a **Workshop** (Q&A + quiz + exercises with solutions).

> **Convention in this book.** Code blocks tagged `// 📁 path/to/file.ts` are excerpts of real
> Controlvar source at the reference commit below — mostly under `frontend/`, some under
> `modules/frontend/`, `backend/` or `demo/`; `...` marks lines left out. Blocks with no path
> are illustrative. `// ✅` marks the
> idiom we want; `// ❌` marks the anti-pattern it replaces. Have the dashboard running
> (`make web` → `http://localhost:3100`) against a local API.

---

## The Controlvar Dashboard Creed (read this first)

Seven rules drive almost every decision in `frontend/src`. You'll learn the machinery behind
each over the next 24 hours:

1. **The API is the product; the dashboard is its first client.** Pages render what the API
   returns and post what it accepts. Types mirror backend schemas field for field; nothing is
   renamed client-side. → Hours 4, 14
2. **One fetch wrapper.** Every request goes through `apiFetch<T>()`: base URL, bearer token,
   JSON, `ApiError`, the 401 redirect. Tests mock `global.fetch` or `apiFetch`, never
   individual services. → Hour 13
3. **Never trust the stored token.** `AuthContext` re-derives the user from `/auth/me` on
   every mount; `RequireAuth` is UX, the API enforces. → Hours 9, 12
4. **Thin pages, typed services, small hooks.** A page holds local state and calls a
   service; a service is one object whose methods map to routes; `useApi` wraps a fetcher.
   → Hours 10, 14
5. **Mirror server validation for feedback; show the server's message when they
   disagree — except where the status alone says what happened, and then say it in the
   page's own words with the fix.** → Hour 15
6. **Never more permissive than the API.** Offer what the user's role (or superuser flag,
   effective permissions, or installed modules) allows, mirror the API's rule exactly, and say
   why when a control is missing. → Hours 9, 11, 12, 16
7. **`data-testid` on anything a test or a scenario runner needs to find.** Tests query by
   role and text first, by test id when structure would otherwise leak into the test. →
   Hours 21, 22

---

## Table of Contents

### Part I — TypeScript Essentials _(Hours 1–4)_ → [`part-1-typescript.md`](./part-1-typescript.md)

- **Hour 1** — Why TypeScript? The field that wasn't there
- **Hour 2** — Types, interfaces, and literal unions (`ExperimentStatus`)
- **Hour 3** — Generics and `unknown`: `apiFetch<T>` and `ApiError.detail`
- **Hour 4** — Narrowing, optional chaining, and mirroring the backend schema

### Part II — React Fundamentals _(Hours 5–9)_ → [`part-2-react.md`](./part-2-react.md)

- **Hour 5** — Components and JSX
- **Hour 6** — Props and state (`useState`)
- **Hour 7** — Effects and cleanup (`useEffect`, the cancelled flag)
- **Hour 8** — Custom hooks and the rules of hooks (`useApi`, `useForm`)
- **Hour 9** — Context (`AuthContext`, `NotificationContext`)

### Part III — Next.js _(Hours 10–13)_ → [`part-3-nextjs.md`](./part-3-nextjs.md)

- **Hour 10** — The pages router: files are routes
- **Hour 11** — `_app.tsx`, the shell, and route kinds
- **Hour 12** — `RequireAuth`, `useRouter`, and dynamic routes
- **Hour 13** — Environment, `apiBase()`, and the static export behind nginx

### Part IV — Data & Forms _(Hours 14–17)_ → [`part-4-data-and-forms.md`](./part-4-data-and-forms.md)

- **Hour 14** — The services layer: one object per resource
- **Hour 15** — Forms: one reducer, two views, server messages
- **Hour 16** — Lists, pagination, and lifecycle actions
- **Hour 17** — WebSockets in React: `useExperimentStream`

### Part V — UI _(Hours 18–20)_ → [`part-5-ui.md`](./part-5-ui.md)

- **Hour 18** — Tailwind and the design tokens
- **Hour 19** — The targeting rule builder: state shaped like the API
- **Hour 20** — Charts with Recharts (and mocking them)

### Part VI — Production _(Hours 21–24)_ → [`part-6-production.md`](./part-6-production.md)

- **Hour 21** — Jest + React Testing Library: pages, hooks, services
- **Hour 22** — Playwright: browser journeys, visual regression, accessibility
- **Hour 23** — Build, Docker, nginx, and the OpenAPI URL guard
- **Hour 24** — Putting it together: the Controlvar dashboard style guide

---

## A note on accuracy

Verified against Controlvar `main` at `09d815ea` (after v0.43.2). Controlvar was called Experimently
before 0.35.0, and you will still meet the old name in some identifiers: the `experimently.token` and
`experimently.bearer` strings, the `EXPERIMENTLY_PROFILE` build argument, the `experimently-web` image name
in the Dockerfile's own comments. Those are quoted as the code spells them. Every `// 📁` excerpt was
copied from the file at that commit, and the tools in this repository's `tools/` directory check that the
paths exist and that every identifier in an excerpt occurs in its file. I did not run the dashboard, Jest
or Playwright to produce the output shown in the exercises: where a Try It Yourself shows a result, it
comes from reading the code at that commit. Files move; if a path 404s, `git grep` the symbol in both dashboard trees (e.g.
`git grep -n "export function apiFetch" -- frontend/src modules/frontend/src`). The _patterns_ outlast the
line numbers. To re-check the book yourself, follow `tools/README.md`.

_Now turn to [Part I](./part-1-typescript.md) and start the clock. ⏱️_

## Related reading — Production Engineering

The Production Engineering specialization is pinned to the same Controlvar release and goes further into
operating what this book builds.

- [Feature-flag lifecycle](../production-engineering/part-4-safe-change.md#hour-10--feature-flag-lifecycle-create-operate-and-retire)
- [Changing Controlvar safely](../production-engineering/part-4-safe-change.md) (Hour 11)

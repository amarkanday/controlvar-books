# Learn TypeScript, React & Next.js in 24 Hours

### _The Experimently Edition — Teach Yourself the Dashboard Stack Using a Real Codebase_

> **Preface.** Most React tutorials build a counter and a todo list and stop before the parts
> a real dashboard is made of — a typed API client that handles a 401, an auth context that
> never trusts a stored token, forms that mirror server validation, a WebSocket hook with
> reconnect, charts that render in tests, a rule builder that emits the exact JSON a backend
> expects. This book is different. Every concept is taught **twice**: once in the abstract,
> and once with a real snippet pulled from the **Experimently** dashboard (a Next.js 14 pages-
> router app, ~25,000 lines of strict TypeScript, 550+ Jest tests, Playwright E2E, visual and
> accessibility suites). By Hour 24 you'll be able to _write_ a page in this codebase, not just
> read one — and you'll have met the `Traffic allocations must sum to 100%` message twice: once
> in the client's validator and once, verbatim, from the server (Hour 14).

---

## Who this book is for

- **Backend engineers** on Experimently who need to add or change a dashboard page and want
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
2. **In the wild** — the real Experimently code that uses it (file path included so you can
   open it).
3. **Try It Yourself** — a short exercise in the [TypeScript Playground](https://www.typescriptlang.org/play),
   a scratch component, or the running dashboard.
4. **Common mistakes** and **Key takeaways**.

Each Part ends with a **Workshop** (Q&A + quiz + exercises with solutions).

> **Convention in this book.** Code blocks tagged `// 📁 path/to/file.ts` are real, current
> Experimently source under `frontend/`. Blocks with no path are illustrative. `// ✅` marks the
> idiom we want; `// ❌` marks the anti-pattern it replaces. Have the dashboard running
> (`make web` → `http://localhost:3100`) against a local API.

---

## The Experimently Dashboard Creed (read this first)

Six rules drive almost every decision in `frontend/src`. You'll learn the machinery behind
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
   disagree.** → Hour 15
6. **`data-testid` on anything a test or a scenario runner needs to find.** Tests query by
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
- **Hour 15** — Forms: local state, client validation, server messages
- **Hour 16** — Lists, pagination, and lifecycle actions
- **Hour 17** — WebSockets in React: `useExperimentStream`

### Part V — UI _(Hours 18–20)_ → [`part-5-ui.md`](./part-5-ui.md)

- **Hour 18** — Tailwind and the design tokens
- **Hour 19** — The targeting rule builder: state shaped like the API
- **Hour 20** — Charts with Recharts (and mocking them)

### Part VI — Production _(Hours 21–24)_ → [`part-6-production.md`](./part-6-production.md)

- **Hour 21** — Jest + React Testing Library: pages, hooks, services
- **Hour 22** — Playwright: E2E, visual regression, accessibility
- **Hour 23** — Build, Docker, nginx, and the OpenAPI URL guard
- **Hour 24** — Putting it together: the Experimently dashboard style guide

---

## A note on accuracy

The snippets here were lifted from the repository on 2026-09-11. Files move; if a path 404s,
`git grep` the symbol (e.g. `git grep -n "export function apiFetch" frontend/src`). The
_patterns_ outlast the line numbers.

_Now turn to [Part I](./part-1-typescript.md) and start the clock. ⏱️_

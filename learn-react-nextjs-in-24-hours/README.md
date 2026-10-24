# Learn TypeScript, React & Next.js in 24 Hours

### _The Experimently Edition — Teach Yourself the Dashboard Stack Using a Real Codebase_

> **Preface.** This book covers the TypeScript, React and Next.js features used by Experimently's dashboard.
> It starts with language and component fundamentals, then examines routing, authentication,
> API services, forms, targeting rules, charts and WebSocket updates. The final lessons cover
> tests and the production build. The reviewed application uses Next.js 16, React 19 and the
> pages router; examples follow that structure.

---

## Who this book is for

This book is for developers adding dashboard features, backend engineers learning the frontend
stack and reviewers working with its conventions. It assumes JavaScript functions, objects,
arrays, promises and ES modules. Prior TypeScript, React or Next.js experience is not required.

## How to use this book

The 24 lessons are grouped into six Parts. Each lesson introduces a concept, examines its use
in the repository and provides an exercise:

1. **What & why** — the concept and the problem it solves.
2. **In the wild** — the real Experimently code that uses it (file path included so you can
   open it).
3. **Try It Yourself** — a short exercise in the [TypeScript Playground](https://www.typescriptlang.org/play),
   a scratch component, or the running dashboard.
4. **Common mistakes** and **Key takeaways**.

Each Part ends with a workshop containing questions, a quiz and exercises with solutions.

> **Convention in this book.** Code blocks tagged `// 📁 path/to/file.ts` are excerpts of real
> Experimently source at the reference commit below — mostly under `frontend/`, some under
> `modules/frontend/`, `backend/` or `demo/`; `...` marks lines left out. Blocks with no path
> are illustrative. `// ✅` marks the
> idiom we want; `// ❌` marks the anti-pattern it replaces. Have the dashboard running
> (`make web` → `http://localhost:3100`) against a local API.

---

<a id="the-experimently-dashboard-creed-read-this-first"></a>

## Dashboard conventions used in this book

The dashboard follows these conventions for API access, state, validation and tests. Use the
lesson references when reviewing an unfamiliar part of the frontend:

1. **Keep client types aligned with the API.** Pages submit the API's request fields and render its
   response fields, including their names. See Hours 4 and 14.
2. **Use the shared fetch wrapper.** `apiFetch<T>()` handles the base URL, bearer token, JSON,
   `ApiError` and 401 redirect. Tests replace `global.fetch` or `apiFetch` rather than individual
   services. See Hour 13.
3. **Validate the stored session through the API.** `AuthContext` obtains the user from `/auth/me`
   on mount. `RequireAuth` handles navigation; the API enforces authorization. See Hours 9 and 12.
4. **Separate pages, services and hooks.** Pages manage view state, services map operations to API
   routes, and hooks encapsulate reusable behavior such as `useApi`. See Hours 10 and 14.
5. **Use client validation for timely feedback.** Preserve meaningful server errors, and explain
   status-based failures in the page's context when the status supplies the relevant information.
   See Hour 15.
6. **Match controls to server permissions and installed modules.** Do not offer an action the API
   will refuse; explain unavailable controls where useful. See Hours 11, 12 and 16.
7. **Prefer accessible test queries.** Query by role and text first. Use `data-testid` where a test
   or scenario runner otherwise needs to depend on fragile structure. See Hours 21 and 22.

---

## Table of Contents

### Part I — TypeScript Essentials _(Hours 1–4)_ → [`part-1-typescript.md`](./part-1-typescript.md)

- **Hour 1** — TypeScript and API response checks
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
- **Hour 24** — Putting it together: the Experimently dashboard style guide

---

## A note on accuracy

Verified against getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased changes,
including three breaking ones: #747, #769, #770). Of those, #747 shows up here: targeting
rules with an unknown key are now refused with a 422 (Hour 19). Files move; if a path 404s,
`git grep` the symbol in both dashboard trees (e.g.
`git grep -n "export function apiFetch" -- frontend/src modules/frontend/src`). The
_patterns_ outlast the line numbers.

_Now turn to [Part I](./part-1-typescript.md) and start the clock. ⏱️_

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Feature-flag lifecycle](../production-engineering/part-4-safe-change.md#hour-10--feature-flag-lifecycle-create-operate-and-retire)
- [Changing Experimently safely](../production-engineering/part-4-safe-change.md#hour-11--changing-experimently-safely)

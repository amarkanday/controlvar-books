# Learn FastAPI in 24 Hours

### _The Experimently Edition — Teach Yourself FastAPI, Pydantic v2, SQLAlchemy 2 and Alembic Using a Real Codebase_

> **Preface.** Most FastAPI tutorials stop at `@app.get("/items/{id}")` and a `BaseModel` with
> two fields. This book is different. Every concept is taught **twice**: once in the abstract,
> and once with a real snippet pulled from the **Experimently** backend (a FastAPI + SQLAlchemy +
> PostgreSQL experimentation platform: about 70,000 lines of typed Python in the core
> `backend/app`, and 5,400+ backend tests by the app README's count). By Hour 24 you won't
> just _know_ FastAPI — you'll know how a production team uses it to run an API with
> thirty-seven core routers (plus the optional modules' routers, mounted through a seam),
> several authentication paths, five background schedulers, and a test harness that spins up
> a database per process. And you'll have met the 422 that saved a bad
> experiment (Hour 1) and the shape mismatch that 5,000 green tests didn't catch (Hour 22).

---

## Who this book is for

- **Python developers new to FastAPI** joining Experimently (or any FastAPI codebase) who want
  to go from "I know Python" to "I can add an endpoint with tests" fast.
- **Developers coming from Flask, Django or Express** who keep bouncing off dependency
  injection, Pydantic validators and async/sync rules because tutorials never show _why_ a
  production app reaches for them.
- **Reviewers** who want one reference for the conventions this backend enforces —
  fully-qualified imports, `Depends(get_db)`, access by role through one rule per resource,
  the `except HTTPException: raise` idiom — so review is about substance.

You need: working Python (functions, classes, decorators, `async`/`await`, type hints). You do
**not** need prior FastAPI, Pydantic or SQLAlchemy.

> **🆕 New to Python?** Start with the companion prerequisite —
> [**Learn Python in 24 Hours — The Experimently Edition**](../learn-python-in-24-hours/README.md).
> It begins from absolute zero and ends exactly where this book starts.

## How to use this book

It's structured as **24 one-hour lessons**, grouped into six Parts. Each Hour follows the
same rhythm so you can pace yourself:

1. **What & why** — the concept and the problem it solves.
2. **In the wild** — the real Experimently code that uses it (file path included so you can
   open it).
3. **Try It Yourself** — a short exercise against the running API or in a scratch file.
4. **Common mistakes** and **Key takeaways** — the 3–5 things to remember.

Each Part ends with a **Workshop** (Q&A + quiz + exercises with solutions) to lock it in.

> **Convention in this book.** Code blocks tagged `# 📁 path/to/file.py` are real, current
> Experimently source. Blocks with no path are illustrative. `# ✅` marks the idiom we want;
> `# ❌` marks the anti-pattern it replaces. You'll want the API running
> (`make dev`, see the [onboarding guide](../guides/onboarding.md)) — many exercises are `curl`
> against `http://localhost:8000`.

---

## The Experimently Backend Creed (read this first)

Six rules drive almost every backend decision in this repo. You'll learn the machinery behind
each over the next 24 hours; keep them in view:

1. **Declare, don't parse.** Inputs are typed parameters (`Path`, `Query`, `Body`, `Depends`);
   FastAPI turns a bad UUID into a 422 before your function runs. → Hours 3, 5
2. **Two layers of validation.** Pydantic validators give users a 422 with a message; database
   constraints give the system a guarantee. Never rely on only one. → Hours 6, 14
3. **`Depends(get_db)`, never `SessionLocal()`, in an endpoint.** One session per request,
   closed after the response — and the seam the test harness overrides. → Hours 9, 10, 22
4. **Endpoints are thin; services hold logic; models are tables.** The dependency direction is
   endpoint → service → model, never back. → Hours 2, 16
5. **Async where it's async, sync where it's sync.** The access rules routes actually call
   are sync — `can_act_on_feature_flag` for every flag change, `get_experiment_change_access`
   for experiments — and only flag create is an async `Depends(deps.can_create_feature_flag)`;
   synchronous SQLAlchemy in an `async def` blocks the loop, so keep handlers short and
   consistent per module. → Hours 12, 19
6. **Fail closed.** Settings validators refuse to start production with a weak secret or the
   dev bypass; `except HTTPException: raise` keeps a deliberate 404 from becoming a 500;
   access is decided by role, and a role without the action is refused even on what it
   created. → Hours 4, 8, 12

---

## Table of Contents

### Part I — Foundations _(Hours 1–4)_ → [`part-1-foundations.md`](./part-1-foundations.md)

- **Hour 1** — Why FastAPI? The 422 that saved a bad experiment
- **Hour 2** — The app, routers, and the OpenAPI document
- **Hour 3** — Declaring inputs: `Path`, `Query`, `Body`, headers
- **Hour 4** — Responses: `response_model`, status codes, `HTTPException`

### Part II — Pydantic v2 _(Hours 5–8)_ → [`part-2-pydantic.md`](./part-2-pydantic.md)

- **Hour 5** — `BaseModel` and `Field`: constraints as documentation
- **Hour 6** — `field_validator` and `model_validator`: the two validation layers
- **Hour 7** — `ConfigDict`, `from_attributes`, and serialisation
- **Hour 8** — `pydantic-settings`: the `Settings` object that fails closed

### Part III — Dependency Injection & Auth _(Hours 9–13)_ → [`part-3-dependencies-and-auth.md`](./part-3-dependencies-and-auth.md)

- **Hour 9** — `Depends`: the mechanism (yield, caching, overrides)
- **Hour 10** — The session dependency: `get_db`
- **Hour 11** — Authentication dependencies: bearer, API key, `auto_error=False`
- **Hour 12** — Authorization: one decision per resource, by role
- **Hour 13** — Middleware: the ASGI onion and why order is stated

### Part IV — SQLAlchemy 2 & PostgreSQL _(Hours 14–18)_ → [`part-4-sqlalchemy.md`](./part-4-sqlalchemy.md)

- **Hour 14** — Declarative models, columns, the schema, mixins
- **Hour 15** — Relationships, cascades, `back_populates`, `joinedload`
- **Hour 16** — Sessions, transactions, `flush`/`commit`/`refresh`, the pool
- **Hour 17** — Queries: filters, aggregates, JSONB, `text()` done safely
- **Hour 18** — Alembic: two heads, autogenerate, the bootstrap

### Part V — Beyond Request/Response _(Hours 19–21)_ → [`part-5-beyond-requests.md`](./part-5-beyond-requests.md)

- **Hour 19** — Lifespan and `asyncio` background tasks (the schedulers)
- **Hour 20** — WebSockets and Server-Sent Events
- **Hour 21** — Redis: caching and rate limiting with graceful fallback

### Part VI — Testing & Operating _(Hours 22–24)_ → [`part-6-testing-and-operating.md`](./part-6-testing-and-operating.md)

- **Hour 22** — `TestClient`, `dependency_overrides`, and the mocked-seam lesson
- **Hour 23** — DB-backed tests: the harness, per-process databases, async tests
- **Hour 24** — Observability and the production checklist: the Experimently FastAPI style guide

---

## A note on accuracy

The snippets here are verified against getexperimently/experimently at `a4a6dbd2` (0.17.0
plus unreleased changes, including three breaking ones: #747, #769, #770). Files move; if a
path 404s, `git grep` the symbol (e.g. `git grep -n "def get_current_user"`). The _patterns_
outlast the line numbers.

_Now turn to [Part I](./part-1-foundations.md) and start the clock. ⏱️_

## Related engineering field guides

These task-based guides use a newer, explicitly pinned app snapshot; this book or guide retains
its original reference commit. See the collection index for scope and verification details.

- [Security engineering](../engineering-guides/security-engineering.md)
- [Database evolution and performance](../engineering-guides/database-evolution.md)

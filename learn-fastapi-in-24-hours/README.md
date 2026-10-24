# Learn FastAPI in 24 Hours

### _The Experimently Edition — Teach Yourself FastAPI, Pydantic v2, SQLAlchemy 2 and Alembic Using a Real Codebase_

> **Preface.** This book introduces FastAPI and the libraries used with it in Experimently: Pydantic v2,
> SQLAlchemy 2 and Alembic. The lessons follow a request from input validation through dependencies,
> authentication, database access and response serialization. Later chapters cover background work,
> WebSockets, caching and tests. Examples include both small demonstrations and excerpts from the
> backend, so you can compare a framework feature with its use in an application.

---

## Who this book is for

This book is for Python developers learning FastAPI, engineers joining the backend team and
reviewers working with its endpoint conventions. It assumes familiarity with functions, classes,
decorators, type hints and `async`/`await`. It does not require prior FastAPI, Pydantic or SQLAlchemy
experience.

If those Python topics are unfamiliar, begin with
[Learn Python in 24 Hours](../learn-python-in-24-hours/README.md).

## How to use this book

The 24 lessons are grouped into six Parts. Each lesson introduces a concept, examines its use
in the repository and provides an exercise:

1. **What & why** — the concept and the problem it solves.
2. **In the wild** — the real Experimently code that uses it (file path included so you can
   open it).
3. **Try It Yourself** — a short exercise against the running API or in a scratch file.
4. **Common mistakes** and **Key takeaways** — the 3–5 things to remember.

Each Part ends with a workshop containing questions, a quiz and exercises with solutions.

> **Convention in this book.** Code blocks tagged `# 📁 path/to/file.py` are real, current
> Experimently source. Blocks with no path are illustrative. `# ✅` marks the idiom we want;
> `# ❌` marks the anti-pattern it replaces. You'll want the API running
> (`make dev`, see the [onboarding guide](../guides/onboarding.md)) — many exercises are `curl`
> against `http://localhost:8000`.

---

<a id="the-experimently-backend-creed-read-this-first"></a>

## Backend conventions used in this book

The backend follows these conventions for request handling, persistence and error handling.
The referenced lessons explain their rationale and application:

1. **Declare inputs through FastAPI.** Use typed `Path`, `Query`, `Body` and dependency parameters
   so invalid declared inputs, such as malformed UUIDs, are rejected before the handler runs.
   See Hours 3 and 5.
2. **Validate at both application and database boundaries.** Pydantic provides request errors;
   database constraints protect persisted invariants. See Hours 6 and 14.
3. **Obtain the endpoint session through `Depends(get_db)`.** This establishes the request lifetime
   and provides the override used by tests. Avoid opening an independent `SessionLocal()` in the
   endpoint. See Hours 9, 10 and 22.
4. **Keep the dependency direction from endpoint to service to model.** Put business behavior in
   services and persistence definitions in models. See Hours 2 and 16.
5. **Account for synchronous work in async handlers.** The permission helpers used by routes are
   synchronous except for the flag-create dependency. Synchronous SQLAlchemy calls in `async def`
   block the event loop; follow the module's established conventions and review blocking work.
   See Hours 12 and 19.
6. **Preserve deliberate failures.** Settings reject unsafe production configuration, access checks
   reject disallowed actions, and `except HTTPException: raise` preserves intended HTTP errors.
   See Hours 4, 8 and 12.

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

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Security engineering](../production-engineering/part-3-security-and-privacy.md#hour-7--security-engineering-through-the-request-path)
- [Database evolution and performance](../production-engineering/part-2-reliable-systems.md#hour-6--database-evolution-and-performance)

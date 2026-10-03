# Learn Python in 24 Hours

### _The Experimently Edition — Teach Yourself Modern, Typed Python From Zero Using a Real Codebase_

> **Preface.** Most Python tutorials teach the language with `print("Hello")` and a
> shopping-cart class, then stop before the parts a production codebase actually runs on —
> type hints, dataclasses, generators, context managers, `async`, threads, hashing, testing.
> This book is different. Every concept is taught **twice**: once in the abstract, and once
> with a real snippet pulled from the **Experimently** backend (an A/B-testing and
> feature-flag platform: about 70,000 lines of type-hinted Python 3.11 in `backend/app`, some
> 16,000 tests). By Hour 24 you
> won't just _know_ Python — you'll know how a disciplined team uses it to run a service
> where a single naive `datetime` once stopped every rollout in production (you'll meet that
> bug in Hour 17), and where a method that didn't exist silently ran for months (Hour 1).

---

## Who this book is for

- **Engineers new to Python** joining Experimently (or any modern Python backend) who want
  to go from zero to "I can read and extend this repo" fast.
- **Developers from other languages** (TypeScript, Java, Go) who keep bouncing off Python's
  idioms — comprehensions, decorators, `yield`, `with`, `async` — because tutorials never
  show _why_ you'd reach for them.
- **Reviewers** who want one reference for the Python conventions this codebase enforces,
  so code review is about substance, not style.

You need: basic programming literacy in any language (variables, loops, functions). You do
**not** need prior Python.

## How to use this book

It's structured as **24 one-hour lessons**, grouped into six Parts. Each Hour follows the
same rhythm so you can pace yourself:

1. **What & why** — the concept and the problem it solves.
2. **In the wild** — the real Experimently code that uses it (file path included so you can
   open it).
3. **Try It Yourself** — a short exercise in a Python shell or a scratch file.
4. **Common mistakes** and **Key takeaways** — the 3–5 things to remember.

Each Part ends with a **Workshop** (Q&A + quiz + exercises with solutions) to lock it in.

> **Convention in this book.** Code blocks tagged `# 📁 path/to/file.py` are real, current
> Experimently source. Blocks with no path are illustrative. `# ✅` marks the idiom we want;
> `# ❌` marks the anti-pattern it replaces. Have a Python 3.11 shell open — with the
> repository's virtualenv active (`source venv/bin/activate`) you can import any module you
> read about and poke at it.

---

## The Experimently Python Creed (read this first)

Six rules drive almost every Python decision in this repo. You'll learn the machinery behind
each over the next 24 hours; keep them in view as your North Star:

1. **Python 3.11, type hints on every signature.** Not decoration — the hints are how
   Pydantic and FastAPI know what a request means, and what `mypy` reads when you run it.
   (`mypy` is configured in `pyproject.toml` but no CI gate runs it; ruff and the tests are
   what CI enforces.) → Hours 12, 24
2. **Fully qualified imports, always.** `from backend.app.models.metrics.metric import RawMetric`
   — never `from app.…`, never relative. Two spellings of one module produce two classes and
   a runtime error. → Hour 9
3. **Explicit over clever.** A cumulative-distribution loop beats a one-line trick; a
   dataclass beats a tuple of unnamed things; a named constant beats a magic string
   (`REASON_HOLDOUT = "holdout"`). → Hours 5, 11
4. **Determinism is a feature.** Hashes, not random; seeded RNGs, not `random.random()`;
   naive UTC everywhere a column is naive. → Hours 17, 21
5. **Never swallow what you didn't expect.** Catch specific exceptions; re-raise what you
   can't handle; let `CancelledError` through. → Hours 13, 19
6. **Test the real thing.** Mocks for what you don't own; the real function, the real
   database, for what you do. → Hour 23

---

## Table of Contents

### Part I — Getting Started _(Hours 1–4)_ → [`part-1-getting-started.md`](./part-1-getting-started.md)

- **Hour 1** — Why Python? The method that didn't exist
- **Hour 2** — Values, types and variables (and the 0–1 vs 0–100 trap)
- **Hour 3** — Control flow: `if`, `for`, `while`, truthiness
- **Hour 4** — Functions: arguments, defaults, return values, docstrings

### Part II — Data Structures _(Hours 5–8)_ → [`part-2-data-structures.md`](./part-2-data-structures.md)

- **Hour 5** — Lists and tuples (the cumulative distribution)
- **Hour 6** — Dictionaries (JSON's twin; `get`, `setdefault`, iteration)
- **Hour 7** — Sets, `frozenset`, and comprehensions
- **Hour 8** — Strings, f-strings, and regular expressions

### Part III — Modern Python _(Hours 9–13)_ → [`part-3-modern-python.md`](./part-3-modern-python.md)

- **Hour 9** — Modules, packages, and the `backend.app` import rule
- **Hour 10** — Classes: `__init__`, methods, `@property`, `@staticmethod`, `@classmethod`
- **Hour 11** — Dataclasses and Enums
- **Hour 12** — Type hints: `Optional`, `Union`, generics, `Literal`, and `mypy`
- **Hour 13** — Exceptions: raising, catching, chaining, and the re-raise idiom

### Part IV — Idioms _(Hours 14–17)_ → [`part-4-idioms.md`](./part-4-idioms.md)

- **Hour 14** — Decorators
- **Hour 15** — Context managers and `with`
- **Hour 16** — Iterators, generators, and `yield`
- **Hour 17** — Dates and times: the naive/aware bug that stopped every rollout

### Part V — Concurrency _(Hours 18–20)_ → [`part-5-concurrency.md`](./part-5-concurrency.md)

- **Hour 18** — `async`/`await` and the event loop
- **Hour 19** — `asyncio` tasks, cancellation, and `to_thread`
- **Hour 20** — Threads, locks, and `ContextVar`

### Part VI — In Practice _(Hours 21–24)_ → [`part-6-in-practice.md`](./part-6-in-practice.md)

- **Hour 21** — `hashlib`, `struct`, `secrets`: determinism and randomness done right
- **Hour 22** — NumPy and SciPy: arrays, RNGs, and a statistical test
- **Hour 23** — `pytest`: fixtures, parametrize, `monkeypatch`, mocks, async tests
- **Hour 24** — Putting it together: the Experimently Python style guide

---

## A note on accuracy

Verified against getexperimently/experimently at `2fe9da1e` (2026-10-03; 0.17.0 plus
unreleased changes). Snippets tagged `# 📁` were re-checked against that commit; where a
snippet leaves lines out it says so with `...`. Counts were measured there: lines with
`wc -l` over `backend/app` (69,840; `modules/backend/app` adds 21,532), tests with
`pytest --collect-only -q` (16,302: 13,227 under `backend/tests`, 3,075 under
`modules/backend/tests`). Files move; if a path 404s, `git grep` the symbol (e.g.
`git grep -n "def _hash_user_to_variant"`). The _patterns_ outlast the line numbers.

_Now turn to [Part I](./part-1-getting-started.md) and start the clock. ⏱️_

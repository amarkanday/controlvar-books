# Learn Python in 24 Hours

### _The Experimently Edition — Teach Yourself Modern, Typed Python From Zero Using a Real Codebase_

> **Preface.** This book introduces Python through examples from Experimently's backend. It begins with
> values, control flow and functions, then covers the modules, types, resource management and
> concurrency patterns used by the application. The final lessons cover numerical libraries,
> testing and coding conventions. Short examples introduce each language feature before the
> corresponding repository code is examined.

---

## Who this book is for

This book is for developers new to Python, engineers joining the backend team and reviewers who
need to understand the repository's Python conventions. It assumes familiarity with variables,
loops and functions in another language. Prior Python experience is not required.

## How to use this book

The 24 lessons are grouped into six Parts. Each lesson introduces a concept, examines its use
in the repository and provides an exercise:

1. **What & why** — the concept and the problem it solves.
2. **In the wild** — the real Experimently code that uses it (file path included so you can
   open it).
3. **Try It Yourself** — a short exercise in a Python shell or a scratch file.
4. **Common mistakes** and **Key takeaways** — the 3–5 things to remember.

Each Part ends with a workshop containing questions, a quiz and exercises with solutions.

> **Convention in this book.** Code blocks tagged `# 📁 path/to/file.py` are real, current
> Experimently source. Blocks with no path are illustrative. `# ✅` marks the idiom we want;
> `# ❌` marks the anti-pattern it replaces. Have a Python 3.11 shell open — with the
> repository's virtualenv active (`source venv/bin/activate`) you can import any module you
> read about and poke at it.

---

<a id="the-experimently-python-creed-read-this-first"></a>

## Python conventions used in this book

The following conventions recur throughout the backend. The lesson references explain their
implementation and the cases where extra care is required:

1. **Use Python 3.11 and annotate function signatures.** Pydantic and FastAPI use annotations
   at validation boundaries. `mypy` is configured but not run by a CI gate at the reviewed
   revision; CI enforces Ruff and tests. See Hours 12 and 24.
2. **Use fully qualified imports.** For example,
   `from backend.app.models.metrics.metric import RawMetric`. Loading a module through inconsistent
   paths can create distinct class objects and runtime failures. See Hour 9.
3. **Prefer explicit data structures and operations.** Named dataclass fields and constants such as
   `REASON_HOLDOUT = "holdout"` make interfaces easier to inspect than unnamed tuples or repeated
   string literals. See Hours 5 and 11.
4. **Preserve reproducibility and time conventions.** Use the specified hash functions and seeded
   random generators, and handle naive UTC columns consistently. See Hours 17 and 21.
5. **Handle exceptions at a boundary that can act on them.** Catch expected failures, preserve
   unexpected errors and allow cancellation to propagate. See Hours 13 and 19.
6. **Test owned behavior with its actual implementation.** Use real functions and database paths
   where the behavior depends on them; substitute external systems where appropriate. See Hour 23.

---

## Table of Contents

### Part I — Getting Started _(Hours 1–4)_ → [`part-1-getting-started.md`](./part-1-getting-started.md)

- **Hour 1** — Python in the backend
- **Hour 2** — Values, types, variables and units
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
- **Hour 17** — Dates and times: naive and timezone-aware values

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

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Production Engineering — task routes after the Python foundations](../production-engineering/README.md)

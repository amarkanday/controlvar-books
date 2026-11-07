# Part I — Getting Started

### Hours 1–4

[← Back to Contents](./README.md)

This Part is Python from nothing: what the language is and why a platform is written in it
(Hour 1), the values it computes with and the one unit mismatch that lives in this repo
(Hour 2), how a program decides and repeats (Hour 3), and how work is packaged into functions
(Hour 4). Open a shell — `python` with the repository's virtualenv active — and type every
example. Python is a language you learn at the prompt.

---

## Hour 1 — Why Python? The method that didn't exist

### What & why

Python is a **high-level, dynamically typed, interpreted** language. Three words, three
consequences:

- **High-level**: you write `total += variant.traffic_allocation` and never think about
  memory, pointers or integer widths. The language manages objects for you.
- **Dynamically typed**: a variable is a name bound to an object; the _object_ has a type,
  the name doesn't. `x = 1` then `x = "one"` is legal. This is the source of Python's speed
  of writing and its most characteristic bug class — which the type-hint discipline this
  codebase enforces (Hour 12) exists to tame.
- **Interpreted**: there's no separate compile step. `python script.py` reads, compiles to
  bytecode in memory, and runs. The feedback loop is seconds.

Why does a production experimentation platform choose it? Because the backend's job is
_glue with judgement_: parse requests, query a database, run a statistical test, decide.
Python has the best ecosystem in the world for the "judgement" part — NumPy, SciPy, pandas —
and a mature web stack (FastAPI, SQLAlchemy) for the glue. The costs — it's slower than Go
or Java per line, and dynamic typing lets mistakes wait — are real, and the platform pays
them deliberately: hot paths are short database reads, heavy maths is NumPy (which is C
underneath), and functions are type-annotated so a checker like `mypy` can catch what the
interpreter won't. (Annotations are the convention, not a gate: `mypy` is configured in
`pyproject.toml` but no CI job runs it. Hour 12.)

#### The method that didn't exist

Here is the dynamic-typing cost, concretely, from this codebase. The multi-armed bandit
scheduler reads per-variant counts from a DynamoDB counter service:

```python
# ❌ what shipped (reconstructed)
counters = self.counter_service.get_counters(experiment_id)
```

`DynamoDBCounterService` had no method called `get_counters`. It had `get_experiment_counters`.
In a statically typed language that line fails to compile. In Python it's a perfectly valid
attribute access that raises `AttributeError` — _at runtime, when reached_. The call was
inside a `try` whose `except` logged and fell through to "no data, use zero priors." So for
months the bandit scheduler ran, logged a warning nobody read, and never learned anything.
Every bandit experiment behaved like a fixed split. The tests were green because they mocked
the counter service and the mock cheerfully answered `get_counters`.

The fix (commit `56c49e4f`) renamed the call, added a PostgreSQL fallback, and — the part that matters
for this Hour — the team's testing policy now requires driving such paths against the real
collaborator. But the deeper lesson is the thesis of this book:

> Python will let you write almost anything. The discipline — type hints a checker like `mypy`
> can read, tests against real objects, explicit exceptions — is what makes "almost anything"
> safe. (In this repository `mypy` is configured but no CI gate runs it; the tests are the
> enforcement. Hour 12 says more.)

### In the wild

The corrected call, and the type hints that would have made the mistake visible to `mypy`:

```python
# 📁 backend/app/core/bandit_scheduler.py
    def _stats_from_dynamodb(
        self, experiment_id: UUID, variant_ids: List[str]
    ) -> Optional[Dict[str, VariantStats]]:
        """Read counters from DynamoDB; ``None`` when the service is unavailable."""
        try:
            # Inside the try: the provider resolves the class at each call and
            # can raise (an import that fails mid-process); that is a reason to
            # fall through to PostgreSQL, not to abandon the tick.
            counter_service_cls = self._load_counter_service_class()
            if counter_service_cls is None:
                return None
            counter_service = counter_service_cls()
            counters = counter_service.get_experiment_counters(str(experiment_id))
        ...
```

Read the signature: `experiment_id: UUID`, `variant_ids: List[str]`, returns
`Optional[Dict[str, VariantStats]]`. Those annotations don't change what runs — Python
ignores them at runtime — but `mypy` reads them, and when a variable's type is known, a call
to a method it doesn't have is a type error at your desk. (Here the counter service is part
of the optional modules, so the core looks its class up at run time and gets back a plain
`type` — exactly the situation where a checker can no longer help and a test against the
real class has to.)

### Running Python

Three ways you'll run it in this repo:

```bash
source venv/bin/activate                 # ALWAYS first: the pinned interpreter + packages
python --version                         # Python 3.11.x
python                                   # the interactive shell (REPL) — try things here
python -c "print(2 ** 10)"               # one line
python -m backend.app.db.bootstrap       # run a module as a script, from the repo root
python -m pytest backend/tests/unit -q   # run a tool that's installed in the venv
```

`-m` means "run this _module_ (by import path) as a script," which is why it works from the
repository root without `cd`: `backend.app.db.bootstrap` is resolved the same way an
`import` would resolve it.

> **By the Way.** The **virtualenv** (`venv/`) is a private copy of the interpreter plus the
> exact package versions in `backend/requirements.txt` (`fastapi==0.141.1`, `pydantic==2.13.5`,
> `SQLAlchemy==2.0.54`…). Activating it puts that `python` first on your `PATH`. Every
> "works on my machine" story in this repo's history ends with someone running the system
> Python. `CLAUDE.md` says it in capitals: activate the venv first.

### Try It Yourself

```python
>>> 2 + 2
4
>>> "hero" + "_banner"
'hero_banner'
>>> type(50), type(0.5), type("50"), type(True), type(None)
(<class 'int'>, <class 'float'>, <class 'str'>, <class 'bool'>, <class 'NoneType'>)
>>> x = 50
>>> x = "fifty"          # legal: the *name* has no type
>>> class Svc: pass
>>> Svc().get_counters   # the bug, in one line
AttributeError: 'Svc' object has no attribute 'get_counters'
```

Then, with the venv active, import something real and look at it:

```python
>>> from backend.app.services.assignment_service import AssignmentService
>>> AssignmentService.__doc__[:120]
>>> help(AssignmentService._hash_user_to_variant)   # q to quit
```

### Common mistakes

- **Running the system Python.** `which python` should point into `venv/`.
- **Expecting a typo to fail early.** It fails when reached, at runtime; that's what `mypy`
  and real-object tests are for.
- **Treating annotations as enforcement.** `x: int = "no"` runs. Annotations are for tools
  and readers (and for Pydantic, which _does_ enforce them at its boundary).

### Key takeaways

- Python is high-level, dynamically typed, interpreted — fast to write, and mistakes wait
  until runtime.
- The platform chose it for the scientific ecosystem and the web stack, and tames the cost
  with type hints (readable by `mypy`) and tests against real objects.
- `get_counters` is the canonical "valid Python, wrong program" story.
- Always the venv; `python -m module` from the repo root.

---

## Hour 2 — Values, types and variables (and the 0–1 vs 0–100 trap)

### What & why

Everything in Python is an **object** with a type. The built-in scalar types you'll use
constantly:

| Type    | Examples                     | Notes                                                              |
| ------- | ---------------------------- | ------------------------------------------------------------------ |
| `int`   | `50`, `-1`, `10_000`         | Arbitrary precision — no overflow; `10_000` underscores are legal    |
| `float` | `0.05`, `1.96`, `4523.7`     | IEEE double; `0.1 + 0.2 != 0.3`                                     |
| `str`   | `"hero_banner"`, `'US'`      | Immutable Unicode text; quotes interchangeable                      |
| `bool`  | `True`, `False`              | A subclass of `int` — `True + True == 2`                            |
| `None`  | `None`                       | The single "no value" object; compare with `is`, never `==`         |

A **variable** is a name bound to an object by `=`. Rebinding is free; the old object is
garbage-collected when nothing refers to it. Names are snake_case by convention
(`rollout_percentage`), constants SCREAMING_CASE (`HOLDOUT_SALT`), and a leading underscore
marks "private by convention" (`_hash_user_to_variant`) — Python has no enforced privacy.

Operators are what you'd expect, plus a few worth learning on day one: `//` (floor division),
`%` (modulo — the bucket operator in this codebase), `**` (power), and `is`/`is not` for
identity.

#### The 0–1 vs 0–100 trap

Types tell you _what kind_ of value, not _what unit_. `traffic_allocation` is an `int` in
the API and the database, meaning a **percentage 0–100**; the Lambda functions under
`backend/lambda/` use a `float` **fraction 0.0–1.0** for the same concept. Both are numbers;
neither type system objects; a value of `50` passed where `0.5` was expected is "everyone in
the experiment," and `0.5` where `50` was expected is "nobody." Nothing in the code reconciles
the two; it's the Python version of a lesson every typed language teaches:
**a number's type is not its meaning.** When you read a percentage in this codebase, check
the docstring or the constraint (`ge=0, le=100`) before you do arithmetic with it.

### In the wild

Integer percentages, a modulo bucket, and comparison — bucketing is scalar arithmetic. Here
is the feature-flag rollout function:

```python
# 📁 backend/app/services/feature_flag_service.py
        # Create a hash using user ID and flag key for deterministic assignment
        hash_input = f"{user_id}:{flag.key}"
        hash_value = int(
            hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest(), 16
        )

        # Get bucket (0-99)
        bucket = hash_value % 100

        # User is in the rollout if their bucket is less than the percentage
        return bucket < percentage
```

`int(hex_string, 16)` parses a hexadecimal string into an arbitrary-precision integer —
MD5's 128 bits become one Python `int` with no overflow — and `% 100` reduces it to
`0..99`.

Experiment assignment uses a _different_ function, the one every SDK implements. It reads
only the first four bytes of the digest, turns them into a float in `[0.0, 1.0)`, and only
then scales to a percentage bucket:

```python
# 📁 backend/app/core/consistent_hash.py
def bucket_of(user_id: str, key: str) -> int:
    """The same position as an integer in [0, 100).

    For allocations expressed as percentages. Derived from :func:`hash_user`
    rather than computed separately, so the two can never disagree about which
    side of a boundary a user falls on.

    >>> bucket_of("user-123", "my-flag")
    69
    """
    return int(hash_user(user_id, key) * BUCKET_COUNT)
```

A fraction (`hash_user`) and a percentage (`bucket_of`) side by side, converted in one
explicit place. For the same input, `user-123`/`my-flag`, the rollout function above gives
bucket 79 and `bucket_of` gives 69: two valid-looking numbers from two readings of one
digest (Hour 21 explains why). Compare with the Lambda's fraction:

```python
# 📁 backend/lambda/shared/consistent_hash.py
    MAX_HASH_VALUE = 0xFFFFFFFF  # 2^32 - 1
    ...
        traffic_hash = self._hash(user_id, f"{hash_salt}_traffic")
        traffic_value = traffic_hash / self.MAX_HASH_VALUE

        # Check if user is in traffic allocation
        if traffic_value >= traffic_allocation:
```

`0xFFFFFFFF` is an integer literal in hex; `/` is _true_ division and always yields a
`float`; the comparison is against a 0.0–1.0 fraction. Same idea, different unit, different
type — and no error if you mix them.

`None` and the `is` idiom:

```python
# 📁 backend/app/services/feature_flag_service.py
        # Use flag's percentage if not specified
        if percentage is None:
            percentage = flag.rollout_percentage or 0
```

Two idioms in two lines: `is None` (identity, the only correct way to test for `None`) and
`x or 0` (a default when `x` is "falsy" — Hour 3 explains why that's `None` _and_ `0`, and
why it's fine here).

Booleans and the `bool` subclass gotcha, guarded explicitly:

```python
# 📁 backend/app/core/safety_scheduler.py
def _rollback_target_percentage(config: Any) -> int:
    ...
    value = getattr(config, "rollback_percentage", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    clamped = max(0, min(100, int(value)))
    if not 0 <= value <= 100:
        logger.warning(...)   # abridged: names the flag and the stored value
    return clamped
```

`isinstance(True, int)` is `True` — so a config where someone wrote `rollback_percentage:
true` would otherwise become `1%`. The explicit `isinstance(value, bool)` check comes first.
`max(0, min(100, x))` is the idiom for clamping.

> **Did You Know?** Python integers are arbitrary precision, so the flag rollout can do
> `int(md5_hex, 16)` — a 39-digit number — and `blake2b` seeds are masked to 63 bits with
> `& ((1 << 63) - 1)` not because they'd overflow Python, but because they must fit a
> PostgreSQL `BIGINT` and NumPy's seed. The mask is about _other_ systems' limits.

### Try It Yourself

```python
>>> 7 / 2, 7 // 2, 7 % 2, 2 ** 10
(3.5, 3, 1, 1024)
>>> 0.1 + 0.2 == 0.3
False
>>> int("ff", 16), hex(255), 0xFF
(255, '0xff', 255)
>>> True + True, isinstance(True, int)
(2, True)
>>> x = None; x is None, x == None
(True, True)          # both True here — but `==` can be overridden by a class; `is` cannot
>>> max(0, min(100, 250)), max(0, min(100, -3))
(100, 0)
>>> import hashlib
>>> int(hashlib.md5(b"user-42:exp-1").hexdigest(), 16) % 100      # the flag-rollout reading
54
>>> from backend.app.core.consistent_hash import bucket_of          # venv, repo root
>>> bucket_of("user-42", "exp-1")                                   # the experiment reading
99
```

Now the trap: compute `50 / 100` and `0.5 * 100`, and decide which of the two unit
conventions each of these lines assumes: `if bucket < percentage` and `if traffic_value >=
traffic_allocation`.

### Common mistakes

- **`x == None`** — works but is wrong (`==` is overridable); `x is None`. The one exception
  is a SQLAlchemy filter: `Model.column == None` is _meant_ to be overridden — it builds
  `IS NULL` — while `Model.column is None` is plain Python `False` and silently matches no
  rows. That is why `pyproject.toml` tells ruff to ignore `E711`/`E712`.
- **Assuming `/` gives an int.** It never does; `//` floors.
- **Trusting a number's type for its unit.** Read the docstring; clamp with `max/min`.
- **Forgetting `bool` is an `int`** when checking `isinstance(x, int)`.

### Key takeaways

- Five scalar types; names are bound to objects; rebinding is free.
- `%` is the bucket operator; `int(s, 16)` parses hex into an unbounded integer; `/` is
  always float.
- `is None`, never `== None`; `x or default` for falsy defaults.
- A type is not a unit: 0–100 in the API, 0.0–1.0 in the Lambdas.

---

## Hour 3 — Control flow: `if`, `for`, `while`, truthiness

### What & why

Python's control flow is indentation-delimited: a colon opens a block, and the block is
whatever's indented beneath it (four spaces, enforced by `ruff format`). There are no braces and no
`end`. That makes structure visible at a glance and makes a misindented line a logic
change, not a syntax error — worth knowing when you paste code.

- `if` / `elif` / `else` — chained conditions; the first true branch runs.
- `for x in iterable:` — iterates _values_, not indices. `for i, x in enumerate(xs)` when
  you need both; `for a, b in zip(xs, ys)` to walk two sequences together.
- `while cond:` — loop until false; `break` exits, `continue` skips to the next iteration.
- `for ... else:` — the `else` runs if the loop finished _without_ `break` (rare, but the
  fallthrough in `_hash_user_to_variant` is exactly this shape written with a `return`).

**Truthiness** is the rule that makes `if x:` idiomatic: `None`, `False`, `0`, `0.0`, `""`,
`[]`, `{}`, `set()` are falsy; everything else is truthy. Powerful, and the source of the
"zero trap": `if percentage:` is `False` for a legitimate `0%`.

### In the wild

The order of checks in `assign_user` is control flow as policy — sticky first, then gates,
then hash. Note the early `return`s: each branch that decides exits, so the code reads top
to bottom without nesting:

```python
# 📁 backend/app/services/assignment_service.py
        if not experiment:
            raise ValueError(f"Experiment {experiment_id} not found")

        # Check experiment status
        if experiment.status != ExperimentStatus.ACTIVE:
            raise ValueError(f"Cannot assign users to experiment with status: {experiment.status}")

        # Check for existing assignment (sticky assignment)
        existing_assignment = self._stored_assignment(user_id, experiment_id)

        if existing_assignment:
            logger.debug(f"Using existing assignment in experiment {experiment_id}")
            return self._sticky_result(
                user_id, experiment_id, existing_assignment, track_exposure, context
            )

        # Eligibility gate for new users: holdout -> mutual exclusion -> targeting
        eligibility = self.check_eligibility(
            user_id, experiment, context, holdout_check=holdout_check
        )
        if not eligibility["eligible"]:
            ...
            result = self._ineligible_result(user_id, experiment, eligibility)
            ...
            return result
```

`if existing_assignment:` is truthiness on an ORM object (truthy) or `None` (falsy). `if not
eligibility["eligible"]:` is truthiness on a `bool`. Both are idiomatic because the falsy
case is exactly the "absent/false" case you mean.

A `for` over a running cumulative sum, with a guard before it and a fallthrough after it:

```python
# 📁 backend/app/services/assignment_service.py
        if not experiment.variants:
            raise ValueError(f"Experiment {experiment.id} has no variants")

        namespace = experiment.key or str(experiment.id)
        bucket = bucket_of(user_id, namespace)

        # Cumulative distribution over allocations expressed 0-100.
        cumulative = 0
        for variant in experiment.variants:
            cumulative += variant.traffic_allocation
            if bucket < cumulative:
                return variant.id

        # Allocations summing to less than 100 leave a tail; the last variant
        # takes it, which is what the previous implementation did too.
        return experiment.variants[-1].id
```

`if not experiment.variants:` is truthiness on a list: an empty list is falsy, so the guard
raises before the loop could ever fall through with nothing to return. The `for` keeps a
running total and returns the first variant whose cumulative share is above the bucket. The
line after the loop is the `for ... else` idea without the keyword: it runs only if no
`return` fired. And `experiment.key or str(experiment.id)` is the falsy-default idiom from
Hour 2: an experiment with no public `key` (`None` or `""`) falls back to its UUID.

`zip` to compare neighbours — "non-decreasing" in one expression:

```python
# 📁 backend/app/schemas/rollout_schedule.py
        # Check that the targets do not decrease in stage_order, whatever
        # order the list is in: the scheduler starts stages by stage_order
        # (#1460). A stable sort, so stages that share an order (refused
        # below) are compared in list order.
        stage_percentages = [
            stage.target_percentage
            for stage in sorted(self.stages, key=lambda stage: stage.stage_order)
        ]
        if not all(a <= b for a, b in zip(stage_percentages, stage_percentages[1:])):
            raise ValueError("Stage percentages must be non-decreasing in stage_order")
```

`zip(xs, xs[1:])` pairs each element with its successor; `all(...)` is `True` if every pair
satisfies the condition. (The inner expression is a generator — Hour 16.)

The interesting part is the question the loop can't answer for you: neighbours _in which
order_? A rollout request carries its stages as a list, and each stage also carries its own
`stage_order`. Until v0.40.0 the check compared the stages in list order, which is not the
order the scheduler runs them in. So `[{stage_order: 2, target_percentage: 10},
{stage_order: 1, target_percentage: 50}]` passed (10, then 50, looks non-decreasing) and
then ran backwards: 50%, then 10% (#1460). The fix is one `sorted(..., key=...)` (Hour 5)
before the walk. A comparison of neighbours is only as right as the order you put them in,
and the order that matters is the one the _consumer_ uses, not the one the data arrived in.

A `while` with `break`, and the zero trap avoided on purpose:

```python
# 📁 backend/app/services/feature_flag_service.py
        # If percentage is 0 or 100, short-circuit
        if percentage <= 0:
            return False
        if percentage >= 100:
            return True
```

Written as `if not percentage:` this would be _correct by accident_ for `0` and _wrong_ for
`None` (which `is None` handled two lines earlier). Explicit comparisons say what they mean.

```python
# 📁 backend/tests/integration/api/test_tracking_api.py
def _users_by_holdout_membership(holdout, count_in: int, count_out: int):
    """Fresh user ids split by ``holdout``'s own bucket (its ``hash_salt``).

    Each holdout buckets with its own salt since #445, so the split is the
    holdout's, not the legacy constant's.
    """
    inside, outside = [], []
    while len(inside) < count_in or len(outside) < count_out:
        user_id = _user()
        bucket = holdout_bucket(user_id, holdout.hash_salt)
        if bucket < holdout.holdout_percentage:
            if len(inside) < count_in:
                inside.append(user_id)
        elif len(outside) < count_out:
            outside.append(user_id)
    return inside, outside
```

A `while` that runs until both lists are full — generating ids and sorting them by the real
hash, salted the way that holdout salts it. `inside, outside = [], []` is tuple assignment; `return inside, outside` returns a
tuple the caller unpacks.

> **Watch Out!** `x or default` is the falsy-default idiom, and it's a bug whenever `0`
> (or `""`, or `[]`) is a legitimate value. `flag.rollout_percentage or 0` is safe only
> because the default _is_ zero. For "use the default only when missing," write
> `x if x is not None else default`. The TypeScript Part of the [React book](../learn-react-nextjs-in-24-hours/README.md) calls the same trap `||` vs `??`.

> **By the Way.** Python 3.10 added `match`/`case` (structural pattern matching). This
> codebase, which supports 3.11, barely uses it — `if`/`elif` chains over enums are the house
> style, because they read the same to everyone. Don't introduce `match` for a two-way
> branch.

### Try It Yourself

```python
>>> for i, name in enumerate(["control", "treatment"]): print(i, name)
>>> for a, b in zip([10, 50, 100], [50, 100]): print(a, b)      # zip stops at the shorter
>>> bool(0), bool(""), bool([]), bool(None), bool("0"), bool([0])
(False, False, False, False, True, True)
>>> pct = 0
>>> "on" if pct else "off"          # the zero trap
'off'
>>> pct if pct is not None else 5    # the fix
0
>>> total = 0; dist = []
>>> for alloc, vid in [(30, "a"), (30, "b"), (40, "c")]:
...     total += alloc; dist.append((total, vid))
>>> dist
[(30, 'a'), (60, 'b'), (100, 'c')]
>>> next(vid for threshold, vid in dist if 59 < threshold)
'b'
```

Then read `check_eligibility` in `assignment_service.py` and count the early returns; rewrite
one branch in your head as nested `if`/`else` and notice how much worse it reads.

### Common mistakes

- **Mixing tabs and spaces** or copying code with a different indent — a logic change with
  no error. `ruff format` (`make format`) normalises the indentation; run it.
- **`if x:` when `0` is valid.** Compare explicitly.
- **Modifying a list while iterating over it.** Build a new one (Hour 7's comprehensions).
- **`for i in range(len(xs))`** to get elements — use `for x in xs` or `enumerate`.

### Key takeaways

- Blocks by indentation; `if/elif/else`, `for x in`, `while`, `break/continue`; early
  `return`s over nesting.
- `enumerate` for indices, `zip` for parallel walks, `all`/`any` for "every"/"some".
- Truthiness: `None/False/0/""/[]/{}` are falsy — great for `if obj:`, a trap for `0`.
- `A if cond else B` is the ternary.

---

## Hour 4 — Functions: arguments, defaults, return values, docstrings

### What & why

`def name(params):` defines a function; the body is a block; `return` sends a value back
(`None` if you don't). Python's argument model is richer than most languages', and this
codebase uses all of it:

- **Positional and keyword arguments**: `f(1, 2)` or `f(a=1, b=2)`; callers in this repo
  prefer keywords for anything with more than two arguments (`self.event_service.track_exposure(user_id=..., experiment_id=..., ...)`).
- **Defaults**: `def f(x, ttl=300)`. Defaults are evaluated **once**, at definition — the
  famous mutable-default trap (`def f(xs=[])` shares one list across calls). Use `None` and
  create inside.
- **Keyword-only parameters**: everything after a bare `*` must be passed by name —
  `def create_local_access_token(user, *, expires_minutes=None)`. It stops a caller from
  accidentally passing a positional in the wrong slot.
- **`*args` and `**kwargs`**: collect extra positionals into a tuple and extra keywords into
  a dict. `Experiment(**obj_data)` _unpacks_ a dict into keyword arguments — the ORM
  constructor idiom.
- **Multiple return values** are a tuple: `return inside, outside`; the caller unpacks.
- **Docstrings**: a string literal as the first statement, in Google style here — `Args:`,
  `Returns:`, `Raises:`. They're what `help()` shows and what a reviewer reads first.

Functions are **objects**: you can pass them around (`Depends(get_db)` passes the function,
not its result), store them in dicts, and decorate them (Hour 14).

### In the wild

A method with every kind of parameter and a Google-style docstring:

```python
# 📁 backend/app/services/assignment_service.py
    def assign_user(
        self,
        user_id: str,
        experiment_id: Union[str, UUID],
        track_exposure: bool = True,
        override_variant_id: Optional[Union[str, UUID]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Assign a user to an experiment variant.

        New users go through the eligibility checks (global holdout, mutual
        exclusion group, targeting rules — in that order) before a variant is
        chosen.  Ineligible users get no ``Assignment`` row and no exposure
        event; ...

        Args:
            user_id: ID of the user to assign
            experiment_id: ID of the experiment
            track_exposure: Whether to track an exposure event
            override_variant_id: Optional variant ID to force assignment
            context: Optional context data for targeting

        Returns:
            Dictionary containing the assignment data plus ``assigned`` and
            ``reason``

        Raises:
            ValueError: If the experiment is not active or has issues
        """
```

Two required parameters, three optional with defaults — and the defaults for the
containers are `None`, not `{}`, because of the mutable-default trap. Callers use keywords:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
        assignment_data = assignment_service.assign_user(
            user_id=request.user_id,
            experiment_id=str(experiment.id),
            context=request.context,
            override_variant_id=override_variant_id,
        )
```

A keyword-only parameter, marked by the bare `*`:

```python
# 📁 backend/app/core/security.py
def create_local_access_token(user: Any, *, expires_minutes: Optional[int] = None) -> str:
    ttl = expires_minutes if expires_minutes is not None else settings.LOCAL_AUTH_TOKEN_TTL_MINUTES
```

`create_local_access_token(user, 60)` is a `TypeError`; you must write
`expires_minutes=60`. And note the correct "default only when missing" — `if expires_minutes
is not None` — because `0` minutes, while silly, is not "missing."

`**kwargs` unpacking into a constructor, and a small pure function with a one-line docstring:

```python
# 📁 backend/app/services/experiment_service.py
        obj_data["owner_id"] = str(user_id)

        # Create experiment
        experiment = Experiment(**obj_data)
```

```python
# 📁 backend/app/services/experiment_service.py
    @staticmethod
    def generate_key(name: str) -> str:
        """Derive a unique, URL/SDK-safe experiment key from a name.

        Lower-cases the name, replaces runs of non-alphanumerics with "-", and
        appends a short random suffix so two experiments with the same name
        never collide.  Example: "Button Color Test" -> "button-color-test-3f9a2c".
        """
        import re as _re
        import uuid as _uuid

        slug = _re.sub(r"[^a-z0-9]+", "-", (name or "experiment").lower()).strip("-")[:80]
        return f"{slug or 'experiment'}-{_uuid.uuid4().hex[:6]}"
```

(`@staticmethod` — Hour 10 — means it needs no `self`; it's a plain function that lives on
the class for organisation. `(name or "experiment")` is the falsy default, correct here
because an empty name _should_ fall back.)

Multiple returns as a tuple, unpacked by the caller:

```python
# 📁 backend/app/services/global_holdout_service.py
    def is_user_in_holdout(
        self, user_id: str, holdout=_LOOK_UP
    ) -> Tuple[bool, int, int]:
        ...
        return (is_in, holdout.holdout_percentage, bucket)
```

```python
# 📁 backend/app/services/assignment_service.py
        if holdout_check is None:
            holdout_check = self.global_holdout_service.is_user_in_holdout(user_id)
        in_holdout, holdout_percentage, bucket = holdout_check
```

A function that _takes_ a function — the scheduler runner receives the tick to call:

```python
# 📁 backend/app/core/scheduler_tick.py
async def run_locked_tick(
    name: str,
    tick: Callable[[], Awaitable[Any]],
    *,
    engine: Optional[Engine] = None,
    record: bool = True,
) -> TickOutcome:
```

```python
# 📁 backend/app/core/scheduler.py
                await run_locked_tick(SCHEDULER_NAME, self.process_scheduled_experiments)
```

`self.process_scheduled_experiments` — no parentheses — is the bound method _as an object_;
`run_locked_tick` calls it later. Adding parentheses would call it immediately and pass the
result, which is the single most common mistake with callbacks.

> **Watch Out!** `def f(items=[])` creates the list once; every call that doesn't pass
> `items` shares it, and an `append` in one call is visible in the next. The house pattern is
> `items: Optional[List[str]] = None` then `items = items or []` (or `[] if items is None
> else items`) in the body. Pydantic models are exempt — they copy defaults per instance.

> **Did You Know?** A function's parameters can be introspected — that's how FastAPI turns
> a signature into an API. `inspect.signature(assign_user)` lists every parameter, its
> default and its annotation; FastAPI reads exactly that to decide what's a path parameter,
> a query parameter or a body. Hour 14 (decorators) and the FastAPI book build on it.

### Try It Yourself

```python
>>> def bucket_for(user_id, salt, *, buckets=100):
...     """Deterministic bucket in [0, buckets)."""
...     import hashlib
...     return int(hashlib.md5(f"{user_id}:{salt}".encode()).hexdigest(), 16) % buckets
>>> bucket_for("user-42", "hero_banner")
>>> bucket_for("user-42", "hero_banner", 10)          # TypeError: keyword-only
>>> bucket_for("user-42", "hero_banner", buckets=10)
>>> help(bucket_for)

>>> def bad(xs=[]): xs.append(1); return xs
>>> bad(), bad()          # ([1, 1], [1, 1]) — shared default
>>> def good(xs=None): xs = [] if xs is None else xs; xs.append(1); return xs
>>> good(), good()        # ([1], [1])

>>> kwargs = {"name": "x", "traffic_allocation": 50}
>>> def variant(name, traffic_allocation=50, is_control=False): return (name, traffic_allocation, is_control)
>>> variant(**kwargs)
>>> callbacks = [len, str.upper]; [f("abc") for f in callbacks]
```

Then open `ExperimentService.generate_key` and call it with `"Button Color Test"`, `""` and
`"   "`; explain each result from the docstring.

### Common mistakes

- **Mutable default arguments.** `None` + create inside.
- **Positional arguments to long signatures.** Keywords; use `*` to force them.
- **`callback()` instead of `callback`** when passing a function.
- **No docstring, or a docstring that repeats the signature.** Say what it _does_ and what
  it _raises_.

### Key takeaways

- `def`, positional/keyword/keyword-only (`*`) parameters, defaults evaluated once,
  `*args`/`**kwargs`, tuple returns.
- Callers use keywords; containers default to `None`; `**dict` unpacks into constructors.
- Functions are objects — pass them, don't call them, when something else will call them.
- Google-style docstrings: `Args`/`Returns`/`Raises`.

---

## Part I Workshop

**Q1. Why did the `get_counters` bug run for months without an error?**
Python resolves attribute access at runtime; the `AttributeError` was raised inside a
`try` whose `except` logged and fell through to a default. The tests mocked the collaborator,
so the mock answered the misspelled method. Type hints + `mypy` and tests against the real
object are the two defences.

**Q2. What's the difference between `50` and `0.5` as a traffic allocation in this repo?**
The API and database use `int` percentages 0–100; the Lambda functions use `float`
fractions 0.0–1.0. Same concept, different unit, no type error if mixed — check the docstring.

**Q3. Which of these are falsy: `0`, `"0"`, `[]`, `[0]`, `None`, `0.0`?**
`0`, `[]`, `None`, `0.0`. `"0"` (a non-empty string) and `[0]` (a non-empty list) are truthy.

**Q4. When is `x or default` wrong, and what replaces it?**
When `0`, `""` or `[]` is a legitimate value of `x`. Use `x if x is not None else default`.

**Q5. What does the bare `*` in `def f(user, *, expires_minutes=None)` do?**
Makes every parameter after it keyword-only: `f(user, 60)` is a `TypeError`;
`f(user, expires_minutes=60)` is required.

**Q6. `run_locked_tick(SCHEDULER_NAME, self.process_scheduled_experiments)` — why no
parentheses?**
The function object is passed for the runner to call under the lock. With parentheses it
would run immediately (without the lock) and pass its _result_.

**Quiz.** What does `int("ff", 16) % 100` evaluate to, and why is `int(..., 16)` safe for a
32-hex-character MD5 digest?
→ `255 % 100 == 55`. Python integers are arbitrary precision, so a 128-bit value parses
without overflow.

---

## Exercises

**E1.1 — Bucket math.** Write `bucket(user_id: str, salt: str) -> int` returning the
0–99 bucket the platform's feature-flag rollout uses (MD5 of `f"{user_id}:{salt}"`, parsed as
the whole hex digest, `% 100`; experiment assignment uses a different reading, Hour 21),
and `in_rollout(user_id, salt, percentage) -> bool` with the 0 and 100 short-circuits.

**E1.2 — Clamp and coerce.** Write `as_percentage(value) -> int` that returns 0 for `None`,
booleans and non-numbers, otherwise the value clamped to 0–100 as an `int` — the behaviour
of `_rollback_target_percentage`.

**E1.3 — Distribution.** Given `allocations = [("control", 30), ("a", 30), ("b", 40)]`,
build the cumulative `distribution` list, then write `pick(bucket)` that returns the
variant name for a bucket, falling through to the last variant.

**E1.4 — Non-decreasing.** Write `non_decreasing(xs) -> bool` using `zip` and `all`, and
show it on `[5, 25, 50, 100]` and `[5, 50, 25]`. Then take
`stages = [{"stage_order": 2, "target": 10}, {"stage_order": 1, "target": 50}]` and show that
the targets pass in list order and fail in `stage_order`.

**E1.5 — Keyword-only.** Write `track(user_id, event_name, *, value=None, properties=None)`
that returns a dict with only the keys that were provided, using the `None` default
correctly so `value=0` is kept.

---

## Solutions

**S1.1**

```python
import hashlib

def bucket(user_id: str, salt: str) -> int:
    digest = hashlib.md5(f"{user_id}:{salt}".encode(), usedforsecurity=False).hexdigest()
    return int(digest, 16) % 100

def in_rollout(user_id: str, salt: str, percentage: int) -> bool:
    if percentage <= 0:
        return False
    if percentage >= 100:
        return True
    return bucket(user_id, salt) < percentage
```

**S1.2**

```python
def as_percentage(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, min(100, int(value)))
```

The `bool` check must come first because `isinstance(True, int)` is `True`.

**S1.3**

```python
allocations = [("control", 30), ("a", 30), ("b", 40)]
total, distribution = 0, []
for name, alloc in allocations:
    total += alloc
    distribution.append((total, name))
# [(30, 'control'), (60, 'a'), (100, 'b')]

def pick(bucket: int) -> str:
    for threshold, name in distribution:
        if bucket < threshold:
            return name
    return distribution[-1][1]
```

**S1.4**

```python
def non_decreasing(xs) -> bool:
    return all(a <= b for a, b in zip(xs, xs[1:]))

non_decreasing([5, 25, 50, 100])   # True
non_decreasing([5, 50, 25])        # False

stages = [{"stage_order": 2, "target": 10}, {"stage_order": 1, "target": 50}]
non_decreasing([s["target"] for s in stages])                                          # True: list order
non_decreasing([s["target"] for s in sorted(stages, key=lambda s: s["stage_order"])])  # False: run order
```

The second pair is the rollout bug from Hour 3: the same data, two orders, two answers.

**S1.5**

```python
def track(user_id: str, event_name: str, *, value=None, properties=None) -> dict:
    event = {"user_id": user_id, "event_name": event_name}
    if value is not None:          # keeps value=0
        event["value"] = value
    if properties is not None:
        event["properties"] = properties
    return event

track("u", "purchase", value=0)          # {'user_id': 'u', 'event_name': 'purchase', 'value': 0}
track("u", "purchase", 0)                # TypeError — keyword-only
```

---

**Next:** [Part II — Data Structures →](./part-2-data-structures.md)

# Part II — Data Structures

### Hours 5–8

[← Part I](./part-1-getting-started.md) · [Contents](./README.md) · [Part III →](./part-3-modern-python.md)

Python has four built-in containers and one text type, and a production backend is mostly
moving data between them and a database. This Part is lists and tuples — ordered sequences,
mutable and not (Hour 5); dictionaries — the shape of every JSON payload and every JSONB
column (Hour 6); sets and the comprehension syntax that builds any container in one
expression (Hour 7); and strings, f-strings and the regular expressions that turn a name into
a key (Hour 8). Keep typing in the shell.

---

## Hour 5 — Lists and tuples (the cumulative distribution)

### What & why

A **list** is an ordered, mutable sequence: `[10, 50, 100]`. Index from 0, negative indices
count from the end (`xs[-1]` is the last), slices make copies (`xs[1:]`, `xs[:80]`).
`append`, `extend`, `pop`, `insert`, `sort()` (in place) vs `sorted()` (new list),
`reversed`, `len`, `in`.

A **tuple** is an ordered, _immutable_ sequence: `(total, variant.id)`. Tuples are for
fixed-shape records — a pair, a triple — where the position _means_ something; lists are for
homogeneous collections of any length. Because tuples are immutable they can be dictionary
keys and set members; lists can't.

**Unpacking** is the feature that makes tuples pleasant: `threshold, variant_id = pair`;
`inside, outside = f()`; `for a, b in zip(...)`; `first, *rest = xs`.

#### Why the distribution is a list of tuples

The variant hash needs "for each variant, the cumulative threshold and the id" — a fixed
2-shape per element, any number of elements. A list of `(int, UUID)` tuples says that
exactly. A dict `{threshold: id}` would lose ordering guarantees you'd have to re-sort, and
would break if two variants had the same threshold (a 0% variant). Lists of tuples are the
house shape for "ordered records"; dataclasses (Hour 11) take over when the record grows
beyond two or three fields.

### In the wild

Building and walking the distribution:

```python
# 📁 backend/app/services/assignment_service.py
        # Create cumulative distribution based on traffic allocation
        total = 0
        distribution = []
        for variant in variants:
            total += variant.traffic_allocation
            distribution.append((total, variant.id))

        # Normalize hash to be within range [0, 100)
        bucket = hash_value % 100

        # Find the assigned variant
        for threshold, variant_id in distribution:
            if bucket < threshold:
                return variant_id

        # Fallback to last variant (should not happen if allocations sum to 100)
        return variants[-1].id if variants else None
```

`variants[-1]` — the last element, the fallthrough. The bandit version sorts first so the
walk is reproducible, and builds pairs with a comprehension (Hour 7) before filtering:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
    # Stable ordering so the cumulative walk is reproducible across sessions.
    variants = sorted(experiment.variants or [], key=lambda v: str(v.id))
    weighted = [
        (variant.id, _bandit_weight(raw_weights.get(str(variant.id))))
        for variant in variants
    ]
    weighted = [(vid, w) for vid, w in weighted if w > 0.0]
    total = sum(w for _, w in weighted)
    if not weighted or total <= 0.0:
        return None
    ...
    cumulative = 0.0
    for variant_id, weight in weighted:
        cumulative += weight / total
        if bucket < cumulative:
            return variant_id
    # Floating-point tail: bucket landed at/after the last threshold.
    return weighted[-1][0]
```

Three list idioms in ten lines: `sorted(xs, key=lambda v: ...)` for a stable order by a
derived key; `experiment.variants or []` so a `None` relationship becomes an empty list; `_`
as the conventional name for a value you unpack but don't use (`for _, w in weighted`).
`weighted[-1][0]` indexes the last tuple, then its first field.

Slicing to bound a string, and tuple returns:

```python
# 📁 backend/app/services/experiment_service.py
        slug = _re.sub(r"[^a-z0-9]+", "-", (name or "experiment").lower()).strip("-")[:80]
```

```python
# 📁 backend/app/services/global_holdout_service.py
    def is_user_in_holdout(self, user_id: str) -> Tuple[bool, int, int]:
        ...
        return (is_in, holdout.holdout_percentage, bucket)
```

A `Tuple[bool, int, int]` return type says "three things, in this order, with these
meanings" — the caller unpacks all three by position. (When a tuple return grows past three
fields, this codebase switches to a dataclass: `MSPRTResult`, `SRMResult`.)

A list used as a stack of results, with `sum`, `max`, `min` and `sorted` over it:

```python
# 📁 backend/app/schemas/rollout_schedule.py
        stage_percentages = [stage.target_percentage for stage in self.stages]
        if not all(a <= b for a, b in zip(stage_percentages, stage_percentages[1:])):
            raise ValueError("Stage percentages must be non-decreasing")

        # Check that max stage percentage doesn't exceed the schedule max
        if max(stage_percentages) > self.max_percentage:
            raise ValueError(f"Stage percentages cannot exceed the maximum of {self.max_percentage}%")

        # Check that stage orders are correct
        stage_orders = [stage.stage_order for stage in self.stages]
        if sorted(stage_orders) != list(range(min(stage_orders), max(stage_orders) + 1)):
            raise ValueError("Stage orders must be sequential without gaps")
```

`list(range(lo, hi + 1))` builds `[lo, lo+1, ..., hi]`; comparing it to `sorted(orders)`
checks "consecutive, no gaps, no duplicates" in one line.

> **Watch Out!** `xs.sort()` sorts in place and returns `None`; `sorted(xs)` returns a new
> list. `result = xs.sort()` is a classic bug. Likewise `xs.append(x)` returns `None` —
> `xs = xs.append(x)` loses the list.

> **Did You Know?** A one-element tuple needs a trailing comma: `(1,)`. `(1)` is just `1`.
> The `__table_args__` tuples in the models end with a dict — `(Index(...), {"schema":
> schema_name})` — and a model with _only_ the schema dict must still write `({"schema":
> ...},)` with the comma, or SQLAlchemy sees a dict, not a tuple.

### Try It Yourself

```python
>>> xs = [10, 50, 100]
>>> xs[0], xs[-1], xs[1:], xs[:-1]
(10, 100, [50, 100], [10, 50])
>>> xs.append(120); xs
>>> ys = sorted([(0.4, "b"), (0.6, "a")], key=lambda p: p[1]); ys
>>> first, *rest = [1, 2, 3]; first, rest
(1, [2, 3])
>>> a, b = b, a if False else (1, 2)      # tuple swap idiom: a, b = b, a
>>> t = (1,); type(t), type((1))
(<class 'tuple'>, <class 'int'>)
>>> [(t, name) for t, name in [(30, "control"), (60, "a")] if t > 40]
>>> weighted = [("a", 0.0), ("b", 0.7), ("c", 0.3)]
>>> live = [(v, w) for v, w in weighted if w > 0]; sum(w for _, w in live)
```

Then build the cumulative distribution for `[("control", 25), ("a", 25), ("b", 50)]` and
find which variant buckets 24, 25 and 99 land in.

### Common mistakes

- **`xs = xs.sort()` / `xs = xs.append(x)`** — both return `None`.
- **Mutating a list you're iterating.** Build a new one.
- **Using a list where a tuple's immutability matters** (dict keys, "this shape is fixed").
- **Forgetting the comma** in a one-element tuple.

### Key takeaways

- Lists: ordered, mutable, homogeneous; index/slice/`append`/`sorted(key=)`; `[-1]` is
  last.
- Tuples: ordered, immutable records; unpack them; `_` for ignored fields; `(x,)` for one.
- The cumulative distribution is a list of `(threshold, id)` tuples walked in order.
- `sorted(...)` new vs `.sort()` in place; `range(lo, hi + 1)` for consecutive checks.

---

## Hour 6 — Dictionaries (JSON's twin; `get`, `setdefault`, iteration)

### What & why

A **dict** maps keys to values: `{"enabled": True, "reason": "rollout"}`. Keys must be
hashable (strings, numbers, tuples — not lists); insertion order is preserved (since 3.7);
lookup is O(1). Every JSON object a request carries becomes a dict; every JSONB column
(`targeting_rules`, `configuration`, `event_metadata`) is a dict; every service in this
codebase returns dicts to its endpoint.

The five operations you'll use daily:

- `d[key]` — raises `KeyError` if missing. Use when absence is a bug.
- `d.get(key, default)` — returns `default` (or `None`) if missing. Use when absence is
  normal.
- `d.setdefault(key, default)` — returns the existing value, or sets and returns `default`.
  The idiom for "add only if not already there."
- `key in d` — membership on keys.
- `for k, v in d.items()` — iterate pairs; `.keys()`, `.values()`; `sorted(d)` sorts keys.

Plus `dict(other)` / `{**a, **b}` for copies and merges, `d.pop(key, default)`, and
`collections.defaultdict(list)` for "a dict whose missing keys start as an empty list."

#### Why dicts, not objects, at the service boundary

Services return dicts (`_experiment_to_dict`, `_assigned_result`) and endpoints validate
them through Pydantic response models. The dict is the lowest common shape: it serialises
to JSON with no help, it's what the ORM's `to_dict()` produces, it's what a script or a
test can assert on with `==`, and Pydantic's `model_validate` accepts it. The cost —
key typos are runtime errors — is why the response model exists: a dict missing a required
key fails validation loudly at the boundary.

### In the wild

`.get` with a default, and `.setdefault` to add without overwriting — the whole context
aliasing scheme is these two calls:

```python
# 📁 backend/app/core/targeting_adapter.py
def expand_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not context or not isinstance(context, dict):
        return {}

    flat: Dict[str, Any] = {}
    _flatten_into(flat, context, prefix="")

    # Forward aliases: top-level key -> <prefix>.key (nested dicts are already
    # reachable through their dotted keys, so they are not aliased themselves)
    for key, value in list(flat.items()):
        if "." in key or isinstance(value, dict):
            continue
        for prefix in CONTEXT_ALIAS_PREFIXES:
            flat.setdefault(f"{prefix}.{key}", value)

    # Reverse aliases: <prefix>.key -> key
    for key, value in list(flat.items()):
        head, sep, tail = key.partition(".")
        if sep and head in CONTEXT_ALIAS_PREFIXES and tail and "." not in tail:
            flat.setdefault(tail, value)

    return flat
```

Two details are the whole lesson. `setdefault` is what makes "explicit keys always win over
aliases" true — an alias never overwrites a key the SDK sent. And `for key, value in
list(flat.items())` copies the items into a list _before_ iterating, because the loop
mutates `flat`; iterating a dict while adding keys raises `RuntimeError: dictionary changed
size during iteration`.

The recursive flatten that produces dotted keys:

```python
# 📁 backend/app/core/targeting_adapter.py
def _flatten_into(out: Dict[str, Any], value: Dict[str, Any], prefix: str) -> None:
    for key, item in value.items():
        dotted = f"{prefix}{key}"
        out[dotted] = item
        if isinstance(item, dict):
            _flatten_into(out, item, prefix=f"{dotted}.")
```

`out[dotted] = item` — assignment creates or overwrites. A dict passed into a function is
passed by reference, so `_flatten_into` fills the caller's dict in place and returns
`None`; the type hint `-> None` says so.

`.get` chains with defaults for tolerant readers of untrusted JSON:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
def _bandit_weight(raw: Any) -> float:
    """Extract a non-negative weight from a BanditState.variant_weights entry.

    The scheduler stores ``{"weight": float, "successes": ..., "pulls": ...}``
    per variant; plain numeric values are accepted as well.
    """
    if isinstance(raw, dict):
        raw = raw.get("weight", 0.0)
    try:
        weight = float(raw or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return weight if weight > 0.0 else 0.0
```

```python
# 📁 backend/app/api/v1/endpoints/results.py
        # Build metrics list (may be keyed as metrics_results or metrics)
        metrics_data = result.get("metrics") or result.get("metrics_results") or []
```

Building a dict from another with a comprehension (Hour 7) and merging with `**`:

```python
# 📁 backend/app/core/bandit_scheduler.py
        # Build the raw dict expected by BanditService
        variant_data: Dict[str, Dict] = {
            vid: {
                "successes": vs.successes,
                "failures": vs.failures,
                "pulls": vs.pulls,
                "total_reward": vs.total_reward,
            }
            for vid, vs in variant_stats.items()
        }
```

```python
# 📁 backend/app/core/logger.py
def bind_log_context(**kwargs: Any) -> None:
    """Merge *kwargs* into the current async-task log context."""
    current = _log_context.get().copy()
    current.update(kwargs)
    _log_context.set(current)
```

`.copy()` then `.update()` — never mutate a dict you got from somewhere else (here a
`ContextVar` shared by the request). `{**current, **kwargs}` is the expression form.

`defaultdict` for grouping:

```python
# 📁 backend/app/services/websocket_manager.py
        # experiment_id -> set of active WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = defaultdict(set)
        ...
        async with self._lock:
            self._connections[experiment_id].add(websocket)
```

`defaultdict(set)` means `self._connections[new_key]` creates an empty set on first access
instead of raising — no `if key not in d: d[key] = set()` dance.

`pop` with a default, for consuming keys out of a dict you're about to unpack into a
constructor:

```python
# 📁 backend/app/services/experiment_service.py
        # Extract nested objects
        variants_data = obj_data.pop("variants", [])
        metrics_data = obj_data.pop("metrics", [])
```

> **Watch Out!** `d.get("key")` returns `None` for a missing key _and_ for a key whose value
> is `None`. If you need to tell them apart, use `"key" in d`. And `d["key"]` on a JSON
> payload from a client is a `KeyError` → 500; validate the shape with Pydantic first, or
> `.get` with an explicit default.

> **Did You Know?** Iteration order is insertion order, and the codebase relies on it in
> places — `expand_context`'s forward-alias pass sees explicit keys before the reverse pass
> adds bare ones. That's guaranteed by the language since 3.7, so it's fine to rely on, but
> say so in a comment when the order matters.

### Try It Yourself

```python
>>> ctx = {"country": "US", "app": {"version": "3.2.1"}}
>>> ctx["country"], ctx.get("tier"), ctx.get("tier", "free")
('US', None, 'free')
>>> ctx.setdefault("country", "DE"); ctx["country"]     # unchanged: explicit wins
'US'
>>> flat = {}
>>> def flatten(out, d, prefix=""):
...     for k, v in d.items():
...         out[f"{prefix}{k}"] = v
...         if isinstance(v, dict): flatten(out, v, f"{prefix}{k}.")
>>> flatten(flat, ctx); flat
>>> for k in list(flat):            # copy before mutating
...     if "." not in k and not isinstance(flat[k], dict): flat.setdefault(f"user.{k}", flat[k])
>>> sorted(flat)
>>> from collections import defaultdict
>>> by_variant = defaultdict(list)
>>> for user, variant in [("u1", "a"), ("u2", "b"), ("u3", "a")]: by_variant[variant].append(user)
>>> dict(by_variant)
>>> {**{"a": 1, "b": 2}, **{"b": 3}}
{'a': 1, 'b': 3}
```

Then call `expand_context({"os": "android", "device": {"os": "ios"}})` from the real module
and explain why `device.os` is `"ios"` and `os` is `"android"`.

### Common mistakes

- **`d[key]` on untrusted input.** `KeyError`; use `.get` or validate first.
- **Mutating a dict while iterating it.** Iterate `list(d.items())`.
- **Mutating a dict you were handed** (a `ContextVar`'s value, a caller's argument).
  Copy first.
- **Using `.get` to distinguish missing from `None`.** Use `in`.

### Key takeaways

- Dicts are JSON's twin: hashable keys, insertion order, O(1) lookup.
- `[]` when absence is a bug; `.get(default)` when it's normal; `setdefault` to add without
  overwriting; `in` to test; `.items()` to iterate.
- `list(d.items())` before mutating in a loop; `.copy()`/`{**a, **b}` before changing a
  shared dict.
- `defaultdict` for grouping; `pop(key, default)` to consume.

---

## Hour 7 — Sets, `frozenset`, and comprehensions

### What & why

A **set** is an unordered collection of unique hashable values: `{"exposure",
"experiment_exposure"}`. Membership (`in`) is O(1), and the algebra — `|` union, `&`
intersection, `-` difference — is the natural way to express "is this operator one of the
numeric ones" or "which of these keys are missing." A **frozenset** is an immutable set: it
can be a dict key, a set member, or a module-level constant that nobody can accidentally
`add` to.

**Comprehensions** build a container from an iterable in one expression:

- list: `[stage.target_percentage for stage in self.stages]`
- set: `{t for ops in paths.values() for op in ops for t in op.get("tags", [])}`
- dict: `{vid: VariantStats(variant_id=vid) for vid in variant_ids}`
- generator (no brackets, lazy): `sum(w for _, w in weighted)` — Hour 16

with an optional filter: `[x for x in xs if x > 0]`. They replace the
"create-empty-then-append-in-a-loop" pattern for the common case, and they're faster and
clearer — up to a point. The house rule: one `for`, one `if`, one line; anything more is a
loop.

### In the wild

Frozensets as constants — operator classes the adapter needs to coerce differently:

```python
# 📁 backend/app/core/targeting_adapter.py
#: Every dashboard operator the adapter understands.
DASHBOARD_OPERATORS = frozenset(_SIMPLE_OPERATOR_MAP) | frozenset(_SEMVER_OPERATOR_MAP)

_NUMERIC_OPERATORS = frozenset(
    {
        OperatorType.GREATER_THAN,
        OperatorType.GREATER_THAN_OR_EQUAL,
        OperatorType.LESS_THAN,
        OperatorType.LESS_THAN_OR_EQUAL,
    }
)
_LIST_OPERATORS = frozenset(
    {
        OperatorType.IN,
        OperatorType.NOT_IN,
        OperatorType.CONTAINS_ALL,
        OperatorType.CONTAINS_ANY,
    }
)
```

`frozenset(_SIMPLE_OPERATOR_MAP)` takes a dict's _keys_; `|` unions two frozensets into one.
Downstream, `if operator in _NUMERIC_OPERATORS:` is one hash lookup, and the constant can't
be mutated by a stray `.add()` in a test.

A tuple used as a "set" for the `NOT IN` filter — tuples are fine for tiny fixed
collections where SQLAlchemy wants a sequence:

```python
# 📁 backend/app/services/event_matching.py
#: ``event_type`` values that record that a user *saw* a variant, never a conversion.
EXPOSURE_EVENT_TYPES = (EventType.EXPOSURE.value, "experiment_exposure")
```

A set comprehension to collect distinct reasons, in a test:

```python
# 📁 backend/tests/integration/api/test_tracking_api.py
        reasons = {
            admin_client.post(
                "/api/v1/tracking/assign",
                json={"experiment_key": active_experiment.key, "user_id": user_id},
            ).json()["reason"]
            for user_id in inside
        }
        assert reasons == {"holdout"}
```

Forty requests, one set, one assertion: every reason was `"holdout"`. That's the cleanest
way to assert "all the same" in Python.

A dict comprehension for the zero-prior fallback, and a set for dead connections:

```python
# 📁 backend/app/core/bandit_scheduler.py
        # No data at all — use zero-count priors
        return {vid: VariantStats(variant_id=vid) for vid in variant_ids}
```

```python
# 📁 backend/app/services/websocket_manager.py
        dead: Set[WebSocket] = set()
        async with self._lock:
            conns = set(self._connections.get(experiment_id, set()))
        for ws in conns:
            try:
                await ws.send_text(payload)
            except Exception as exc:
                dead.add(ws)
        for ws in dead:
            await self.disconnect(ws, experiment_id)
```

`set(self._connections.get(...))` makes a **copy** under the lock so the loop can send
without holding it — and so `disconnect` can mutate the original while the copy is being
iterated.

Set algebra for validation — "which required attributes are missing from the context":

```python
# (illustrative — the shape used by RulesEvaluationService.validate_user_context)
required = {c.attribute for c in conditions}
missing = required - set(user_context)
if missing:
    return AttributeValidationResult(is_valid=False, error_message=f"Missing attributes: {sorted(missing)}")
```

`set(user_context)` is the dict's keys as a set; `required - present` is the difference;
`sorted(missing)` turns the unordered set into a deterministic list for the message.

> **Watch Out!** `{}` is an empty **dict**, not an empty set. `set()` is the empty set. And
> sets are unordered — `list(a_set)` has no guaranteed order, so anything that hashes or
> prints a set's contents should `sorted()` it first (as the evaluation cache does with
> `json.dumps(..., sort_keys=True)`).

> **By the Way.** Nested comprehensions read left to right like nested loops: `{t for ops in
> spec["paths"].values() for op in ops.values() for t in op.get("tags", [])}` is `for ops:
> for op: for t: add t`. Two levels is the readability ceiling in this codebase; three is a
> loop with a name.

### Try It Yourself

```python
>>> exposures = {"exposure", "experiment_exposure"}
>>> "purchase" in exposures, "exposure" in exposures
(False, True)
>>> numeric = frozenset({"gt", "gte", "lt", "lte"}); listy = frozenset({"in", "not_in"})
>>> numeric | listy, numeric & {"gt", "in"}, listy - {"in"}
>>> numeric.add("eq")            # AttributeError: frozenset is immutable
>>> required = {"country", "tier", "os"}; present = {"country": "US", "os": "ios"}
>>> sorted(required - set(present))
['tier']
>>> [p for p in [30, 30, 40] if p > 30]
>>> {name: i for i, name in enumerate(["control", "a", "b"])}
>>> {len(s) for s in ["a", "bb", "cc"]}
{1, 2}
>>> type({}), type(set())
(<class 'dict'>, <class 'set'>)
```

Then rewrite this loop as a comprehension and decide whether the result is clearer:

```python
weighted = []
for variant in variants:
    w = weights.get(str(variant.id), 0.0)
    if w > 0:
        weighted.append((variant.id, w))
```

### Common mistakes

- **`{}` for an empty set.** `set()`.
- **Relying on set order.** `sorted()` before printing, hashing or asserting equality with a
  list.
- **Putting a list in a set** (`TypeError: unhashable`). Use a tuple.
- **Comprehensions with side effects** or three nesting levels. Write the loop.

### Key takeaways

- Sets: unique, unordered, O(1) membership, algebra with `| & -`; `frozenset` for constants.
- Comprehensions build lists/sets/dicts in one expression; one `for`, one `if`, one line.
- Copy a shared set before iterating it; `sorted()` a set before anything order-sensitive.
- A set comprehension over responses is the idiom for "all the same."

---

## Hour 8 — Strings, f-strings, and regular expressions

### What & why

A **str** is an immutable sequence of Unicode characters. Every operation returns a new
string: `.lower()`, `.strip("-")`, `.split(",")`, `", ".join(parts)`, `.startswith(prefix)`,
`.partition(".")`, slicing `s[:80]`. Because strings are immutable they're hashable — dict
keys, set members — and safe to share.

**f-strings** (`f"{user_id}:{experiment.id}"`) are the only string formatting you'll write:
expressions in braces, evaluated at runtime, with an optional format spec (`{p:.2%}`,
`{n:4d}`) and a debug form (`{x!r}` for `repr`). The two older forms — `%` and `.format` —
appear only in logging calls, where `logger.info("... %s", value)` defers formatting until
the line is actually emitted.

**Bytes vs str** is the boundary that trips people: hashing, networking and files work on
`bytes`; `"text".encode()` gives UTF-8 bytes; `data.decode()` gives text back. `hashlib.md5`
takes bytes, so every hash input in this codebase is `.encode()`d first.

**Regular expressions** (`re`) are for _matching and transforming_ text by pattern: `re.match`
(at the start), `re.search` (anywhere), `re.sub` (replace), with raw strings (`r"..."`) so
backslashes survive. The codebase uses them sparingly and deliberately — key validation,
slug generation, path normalisation.

### In the wild

The hash input, encoded; the salt convention; and `usedforsecurity=False`:

```python
# 📁 backend/app/services/feature_flag_service.py
        # Create a hash using user ID and flag key for deterministic assignment
        hash_input = f"{user_id}:{flag.key}"
        hash_value = int(hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest(), 16)
```

`f"{a}:{b}"` builds the string; `.encode()` makes bytes; `.hexdigest()` returns a 32-character
hex _string_; `int(..., 16)` parses it. Four representations of one value in one line —
text, bytes, hex text, integer.

Slug generation — `re.sub`, chained string methods, a slice, and a nested f-string default:

```python
# 📁 backend/app/services/experiment_service.py
        slug = _re.sub(r"[^a-z0-9]+", "-", (name or "experiment").lower()).strip("-")[:80]
        return f"{slug or 'experiment'}-{_uuid.uuid4().hex[:6]}"
```

Read the pipeline: lower-case → replace every run of non-`[a-z0-9]` with one `-` → strip
leading/trailing `-` → cap at 80 chars → append a 6-hex-char suffix. `"Button Color Test"`
becomes `button-color-test-3f9a2c`.

Key validation with `re.match` and a message better than the raw pattern:

```python
# 📁 backend/app/schemas/feature_flag.py
    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", v):
            raise ValueError("Key must be lowercase alphanumeric characters, hyphens, or underscores only")
        return v
```

`re.match` anchors at the start; the `$` anchors the end; `[a-z0-9_-]*` is "zero or more of
these." A compiled pattern at module level for something used on every request:

```python
# 📁 backend/app/middleware/prometheus_metrics_middleware.py
# Matches UUID v4 segments in URL paths
_UUID_PATTERN: re.Pattern = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
# Matches purely-numeric path segments (e.g. /users/42)
_NUMERIC_PATTERN: re.Pattern = re.compile(r"/\d+")


def normalize_path(path: str) -> str:
    """Replace dynamic path segments with ``{id}`` for stable metric labels."""
    path = _UUID_PATTERN.sub("/{id}", path)
    path = _NUMERIC_PATTERN.sub("/{id}", path)
    return path
```

`re.compile` once, `.sub` many times. `{8}` is "exactly eight"; `\d+` is "one or more
digits."

`partition` for a one-split with a guarantee of three parts (unlike `split`, which returns
one or two):

```python
# 📁 backend/app/api/deps.py
    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer":
        raise HTTPException(...)
```

```python
# 📁 backend/app/core/targeting_adapter.py
        head, sep, tail = key.partition(".")
        if sep and head in CONTEXT_ALIAS_PREFIXES and tail and "." not in tail:
            flat.setdefault(tail, value)
```

`"user.country".partition(".")` → `("user", ".", "country")`; `"country".partition(".")` →
`("country", "", "")`. Testing `sep` tells you whether the separator was present at all.

`split` + `strip` for comma-separated config, with empties dropped:

```python
# 📁 backend/app/core/config.py
        if isinstance(v, str) and v:
            return [i.strip() for i in v.split(",") if i.strip()]
```

`startswith` with a tuple of prefixes, and `join` to build a message:

```python
# 📁 backend/app/middleware/rate_limiter.py
    if any(path.startswith(prefix) for prefix in SDK_PATH_PREFIXES):
        return (int(sdk_limit_per_minute), 60)
```

Logging's deferred `%s` formatting — not an f-string, on purpose:

```python
# 📁 backend/app/core/scheduler_lock.py
            logger.warning("scheduler_lock(%s): could not acquire (database unavailable): %s", name, exc)
```

If the log level would drop the line, the formatting never happens; with an f-string it
always would. It also lets structured-log processors see the arguments separately.

> **Watch Out!** Building SQL or shell commands with f-strings is how injection happens.
> The one f-string that touches SQL in this codebase interpolates a schema name from a
> fixed internal source and carries a `nosemgrep` comment explaining that; every _value_ is
> a bound parameter. If you find yourself writing `f"... WHERE id = '{id}'"`, stop.

> **Did You Know?** `str.format` and `%` are still the right tools in two places: logging
> (`%s`, deferred) and templates where the format string comes from _data_ (a message
> template in a notification config). Everywhere else, f-strings.

### Try It Yourself

```python
>>> f"{'user-42'}:{'hero_banner'}".encode()
b'user-42:hero_banner'
>>> b'abc'.decode(), "café".encode("utf-8"), len("café"), len("café".encode())
>>> import re
>>> re.sub(r"[^a-z0-9]+", "-", "Button Color Test!!".lower()).strip("-")[:80]
'button-color-test'
>>> bool(re.match(r"^[a-z0-9][a-z0-9_-]*$", "search_v2")), bool(re.match(r"^[a-z0-9][a-z0-9_-]*$", "Search v2"))
(True, False)
>>> "Bearer abc".partition(" "), "abc".partition(" ")
(('Bearer', ' ', 'abc'), ('abc', '', ''))
>>> [s.strip() for s in "http://a, http://b,,".split(",") if s.strip()]
['http://a', 'http://b']
>>> p = 0.05123; f"{p:.2%} {p!r} {42:04d} {'x':>5}"
'5.12% 0.05123 0042     x'
>>> "/api/v1/experiments/550e8400-e29b-41d4-a716-446655440000/results".replace("550e8400-e29b-41d4-a716-446655440000", "{id}")
```

Then run `normalize_path` from the real module on `/api/v1/users/42` and
`/api/v1/experiments/<a uuid>/results`, and explain why the UUID pattern runs before the
numeric one.

### Common mistakes

- **Hashing a `str`** — `hashlib.md5("x")` is a `TypeError`; `.encode()` first.
- **`s.replace(...)` without assigning** — strings are immutable; the result is a new
  string.
- **Regex without a raw string** — `"\d"` is a deprecation warning today and a bug
  tomorrow; write `r"\d"`.
- **f-strings in `logger.*` calls** and in anything that becomes SQL.

### Key takeaways

- Strings are immutable Unicode; methods return new strings; `.encode()`/`.decode()` cross
  the bytes boundary (hashing needs bytes).
- f-strings everywhere except logging (`%s`, deferred) and data-driven templates.
- `partition` for one guaranteed three-way split; `split`+`strip`+filter for CSV config.
- `re.compile` once, `.sub`/`.match` many; raw strings; anchors; never build SQL with
  f-strings.

---

## Part II Workshop

**Q1. Why is the variant distribution a list of tuples rather than a dict keyed by
threshold?**
Order is essential (the walk must be in allocation order) and thresholds can repeat (a 0%
variant). A list of `(threshold, id)` tuples keeps order and allows duplicates; a dict would
silently drop one.

**Q2. What does `flat.setdefault("user.country", value)` do if `"user.country"` was sent
explicitly by the SDK?**
Nothing — `setdefault` returns the existing value and doesn't overwrite. That's how
"explicit keys win over aliases" is implemented.

**Q3. Why `for key, value in list(flat.items())` rather than `flat.items()`?**
The loop body adds keys to `flat`; iterating a dict while its size changes raises
`RuntimeError`. `list(...)` snapshots the items first.

**Q4. `{}` or `set()` for an empty set? `{"a"} | {"b"}`?**
`set()` — `{}` is a dict. The union is `{"a", "b"}`.

**Q5. `logger.info(f"Assigned {user_id}")` vs `logger.info("Assigned %s", user_id)` — which
does the codebase prefer and why?**
The `%s` form: formatting is deferred until the line is emitted (skipped entirely below the
log level) and structured-log processors receive the argument separately.

**Q6. What's wrong with `hashlib.md5(f"{user_id}:{key}")`?**
Hashes take bytes; a `str` is a `TypeError`. `.encode()` first — and pass
`usedforsecurity=False` to say it's a bucket hash, not a security primitive.

**Quiz.** `"a.b.c".partition(".")` and `"a.b.c".split(".", 1)`?
→ `("a", ".", "b.c")` and `["a", "b.c"]`. `partition` always returns three parts (empty
strings when the separator is absent); `split` returns one or two.

---

## Exercises

**E2.1 — Distribution as data.** Write `cumulative(allocations: list[tuple[str, int]]) ->
list[tuple[int, str]]` and `pick(distribution, bucket) -> str` (fallthrough to last), then
prove with a set comprehension that buckets 0–99 over `[("c", 30), ("a", 30), ("b", 40)]`
produce exactly `{"c", "a", "b"}` and count each.

**E2.2 — Context expansion.** Implement `expand(context) -> dict` that flattens nested dicts
to dotted keys and adds `user.`/`device.`/`app.` aliases for top-level scalars, with explicit
keys winning — matching the real `expand_context` on `{"country": "US", "user": {"country":
"DE"}, "app": {"version": "3.2.1"}}`.

**E2.3 — Grouping.** Given `[("u1", "a", 1), ("u2", "b", 0), ("u3", "a", 1), ("u4", "a", 0)]`
as `(user, variant, converted)`, compute `{variant: (assigned, conversions)}` with a
`defaultdict`, then the conversion rate per variant as a dict comprehension.

**E2.4 — Keys and slugs.** Write `is_valid_flag_key(key) -> bool` (lowercase alphanumeric
start, then `[a-z0-9_-]`) and `slugify(name) -> str` (the platform's rule without the random
suffix), and test both on `"Search v2"`, `"search_v2"`, `"--Weird  Name--"`.

**E2.5 — Path normalisation.** Write `normalize(path)` that replaces UUID and numeric
segments with `{id}` using compiled patterns, and explain what goes wrong if the numeric
pattern runs first on `/experiments/123e4567-e89b-12d3-a456-426614174000`.

---

## Solutions

**S2.1**

```python
def cumulative(allocations):
    total, dist = 0, []
    for name, alloc in allocations:
        total += alloc
        dist.append((total, name))
    return dist

def pick(distribution, bucket):
    for threshold, name in distribution:
        if bucket < threshold:
            return name
    return distribution[-1][1]

dist = cumulative([("c", 30), ("a", 30), ("b", 40)])
assert {pick(dist, b) for b in range(100)} == {"c", "a", "b"}
from collections import Counter
Counter(pick(dist, b) for b in range(100))          # Counter({'b': 40, 'c': 30, 'a': 30})
```

**S2.2**

```python
PREFIXES = ("user", "device", "app")

def _flatten(out, d, prefix=""):
    for k, v in d.items():
        out[f"{prefix}{k}"] = v
        if isinstance(v, dict):
            _flatten(out, v, f"{prefix}{k}.")

def expand(context):
    flat = {}
    _flatten(flat, context or {})
    for k, v in list(flat.items()):
        if "." in k or isinstance(v, dict):
            continue
        for p in PREFIXES:
            flat.setdefault(f"{p}.{k}", v)
    for k, v in list(flat.items()):
        head, sep, tail = k.partition(".")
        if sep and head in PREFIXES and tail and "." not in tail:
            flat.setdefault(tail, v)
    return flat

e = expand({"country": "US", "user": {"country": "DE"}, "app": {"version": "3.2.1"}})
assert e["user.country"] == "DE"      # explicit nested key beats the alias
assert e["country"] == "US"
assert e["app.version"] == "3.2.1" and e["version"] == "3.2.1"
```

**S2.3**

```python
from collections import defaultdict
rows = [("u1", "a", 1), ("u2", "b", 0), ("u3", "a", 1), ("u4", "a", 0)]
agg = defaultdict(lambda: [0, 0])
for _, variant, converted in rows:
    agg[variant][0] += 1
    agg[variant][1] += converted
counts = {v: tuple(c) for v, c in agg.items()}          # {'a': (3, 2), 'b': (1, 0)}
rates = {v: conv / n for v, (n, conv) in counts.items()}  # {'a': 0.666..., 'b': 0.0}
```

**S2.4**

```python
import re
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

def is_valid_flag_key(key: str) -> bool:
    return bool(KEY_RE.match(key))

def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "experiment").lower()).strip("-")[:80] or "experiment"

is_valid_flag_key("Search v2"), is_valid_flag_key("search_v2")   # (False, True)
slugify("Search v2"), slugify("--Weird  Name--")                  # ('search-v2', 'weird-name')
```

**S2.5**

```python
import re
UUID_RE = re.compile(r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
NUM_RE = re.compile(r"/\d+")

def normalize(path: str) -> str:
    path = UUID_RE.sub("/{id}", path)
    return NUM_RE.sub("/{id}", path)
```

If `NUM_RE` ran first, the leading digits of the UUID segment (`/123`) would be replaced
with `/{id}`, leaving `/{id}e4567-e89b-...`, which the UUID pattern then fails to match —
the label would be a mangled, high-cardinality string. UUIDs first, numbers second.

---

**Next:** [Part III — Modern Python →](./part-3-modern-python.md)

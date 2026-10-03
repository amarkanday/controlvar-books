# Part III — Modern Python

### Hours 9–13

[← Part II](./part-2-data-structures.md) · [Contents](./README.md) · [Part IV →](./part-4-idioms.md)

Parts I and II were the language a script is written in. This Part is the language a
_codebase_ is written in: how some 70,000 lines of `backend/app` are split into modules and why the import spelling
matters (Hour 9), classes and the three kinds of method (Hour 10), the two lightweight ways
to give data a shape — dataclasses and enums (Hour 11), the type-hint vocabulary that
Pydantic enforces and `mypy` can check (Hour 12), and how errors are raised, caught, chained and — most
importantly — _not_ swallowed (Hour 13).

---

## Hour 9 — Modules, packages, and the `backend.app` import rule

### What & why

A **module** is a `.py` file; a **package** is a directory with an `__init__.py`. `import
x.y.z` runs `z.py` once (the first time), caches the resulting module object in
`sys.modules["x.y.z"]`, and binds the name. `from x.y import z` does the same and binds just
`z`. Everything at module top level — constants, classes, `settings = DevSettings(...)`,
`router = APIRouter()` — runs at import time, exactly once per interpreter.

Two consequences shape this codebase:

1. **Module-level state is a singleton.** `settings`, `engine`, `SessionLocal`, the five
   scheduler instances, the Prometheus registry — each is created once because its module is
   imported once. That's convenient and it's why tests set attributes on `settings` rather
   than re-importing.
2. **The same file imported under two names is two modules.** Python caches by the _string_
   you imported. `backend.app.models.metrics.metric` and `app.models.metrics.metric` (with
   `backend/` on `sys.path`) are two entries in `sys.modules`, two executions of the file,
   and two distinct `RawMetric` classes. SQLAlchemy registers a class per table; two classes
   for one table is `Class ... is not mapped` at runtime.

Hence the rule, in `CLAUDE.md` in bold: **always the fully qualified path from the
repository root.** `from backend.app.models.metrics.metric import RawMetric, MetricType`.
Never `from app.…`, never relative `from ..services import x`, never re-exports in
`__init__.py` that give a class a second address.

### In the wild

The rule, applied — every import in a service names the full path:

```python
# 📁 backend/app/services/assignment_service.py
from backend.app.core.consistent_hash import bucket_of
from backend.app.core.log_once import EVALUATION_NOTES
from backend.app.core.targeting_adapter import (
    _is_dashboard_rules_shape,
    expand_context,
    normalise_targeting_rules,
)
from backend.app.models.assignment import Assignment
from backend.app.models.experiment import Experiment, ExperimentStatus, Variant
from backend.app.schemas.targeting_rule import TargetingRules
from backend.app.services.event_service import EventService
from backend.app.services.global_holdout_service import GlobalHoldoutService
from backend.app.services.mutual_exclusion_service import MutualExclusionService
from backend.app.services.rules_evaluation_service import RulesEvaluationService
```

The alphabetical order is not taste: ruff's `I` rules (they replaced `isort`) sort every
import block, and `make lint` fails on one that isn't.

The one place relative imports survive, and why it's harmless: models importing their own
package's `base`. A relative import is resolved against the importing module's _own_ full
name, so inside `backend.app.models.experiment`, `.base` is `backend.app.models.base` — the
same module object, not a second address. The defect is an import _rooted at `app`_, not a
relative one. New code still writes the full path.

```python
# 📁 backend/app/models/experiment.py
from backend.app.core.database_config import get_schema_name

from .base import Base, BaseModel
```

The package `__init__` is the **core model registry**: it exists so a standalone script can
configure every mapper, and it names the model modules in one tuple instead of relying on
import side effects (abridged):

```python
# 📁 backend/app/models/__init__.py
import importlib
from typing import Any

from .analysis_snapshot import AnalysisKind, AnalysisSnapshot
from .api_key import APIKey
from .assignment import Assignment
...
from .metrics.metric import (
    MetricType as MetricsMetricType,
)
...
CORE_MODEL_MODULES = (
    "analysis_snapshot",
    "api_key",
    "assignment",
    ...
    "user",
)


# `Any`, not `DeclarativeMeta`: `declarative_base()` is the legacy API and its
# stubs return Any, so a narrower annotation only produces a no-any-return.
def register_core_models() -> Any:
    """Register every core model on ``Base.metadata``; returns ``Base``.
    ...
    """
    for module_name in CORE_MODEL_MODULES:
        importlib.import_module(f"{__name__}.{module_name}")
    return Base
```

`importlib.import_module` is `import` as a function call: it takes the module's name as a
string, so a loop over a tuple of names can import them all. The bootstrap, alembic's
`env.py` and the test `conftest.py` call `register_core_models()` rather than importing the
package for its side effect, so what they depend on is written down. A new core model goes
into `CORE_MODEL_MODULES`. Note `MetricType as MetricsMetricType` — two modules define a
`MetricType` (the experiment metric kind and the raw-metric kind), and `as` renames one to
avoid a clash. The names the package imports are for mapper configuration, not for importing
from (`CLAUDE.md`: avoid re-exports; import from the defining module).

The optional modules under `modules/` (present in a full checkout, absent in a core one)
register _their_ models from outside: `modules/backend/app/register.py` calls
`hooks.register_model_module()`. The arrow points one way — `modules/` imports `backend/`,
never the reverse — because the core has to build and pass its tests with `modules/` deleted.

Module-level singletons, created at import:

```python
# 📁 backend/app/core/safety_scheduler.py
# Create a global instance of the safety scheduler
safety_scheduler = SafetyScheduler()
```

```python
# 📁 backend/app/api/v1/endpoints/websocket_results.py
# Module-level singleton so all requests share the same ConnectionManager state.
manager = ConnectionManager()
```

A **local import** inside a function — used deliberately to break an import cycle or defer a
heavy dependency until needed:

```python
# 📁 backend/app/core/scheduler_tick.py
def _persist_run(
    name: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    result: TickResult,
    error: Optional[str],
) -> None:
    """Best-effort write of the run record; never raises."""
    try:
        from backend.app.db.session import SessionLocal
        from backend.app.services.scheduler_health_service import SchedulerHealthService
        ...
```

`scheduler_tick` is imported by every scheduler, and `db.session` imports `config`, which
… — importing at call time sidesteps a cycle at module load. Local imports are a smell in
general and a tool in particular; when you see one in this repo, there's a cycle or a
laziness reason nearby.

Checking the rule is one command, run from the repository root:

```bash
grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'
```

No output means the rule holds; any hit is the defect. (There is no dedicated script or test
for this one rule.) The _package_ boundary is enforced by a tool: `lint-imports`, configured
by the `[tool.importlinter]` contracts in `pyproject.toml` and run by `make lint` and the
`lint` CI job, fails if anything in `backend.app` imports the `modules` package or
`backend.tests`.

> **Watch Out!** `__name__ == "__main__"` is how a module knows it's being run as a script
> rather than imported. `python -m backend.app.db.bootstrap` sets `__name__` to
> `"__main__"` for that module; anything under the guard runs; the same file imported by a
> test doesn't run it. Put script behaviour under the guard, never at top level.

> **Did You Know?** `sys.path[0]` is the directory of the script you ran (or `""` for the
> REPL, meaning the current directory). That's why everything runs from the repository root:
> with the root on `sys.path`, `backend` is importable as a top-level package. Run from
> `backend/` and `import backend` fails — or worse, `import app` succeeds and you've created
> the second address.

### Try It Yourself

```python
>>> import sys
>>> import backend.app.core.config as c1
>>> from backend.app.core import config as c2
>>> c1 is c2                                  # one module object
True
>>> "backend.app.core.config" in sys.modules
True
>>> from backend.app.models.metrics.metric import RawMetric as A
>>> import backend.app.models  # the package imports everything once
>>> from backend.app.models.metrics.metric import RawMetric as B
>>> A is B
True
```

Now simulate the trap without breaking anything: `sys.path.insert(0, "backend")` then
`from app.models.metrics.metric import RawMetric as C` — and compare `C is A`. Remove the
path entry afterwards (`sys.path.pop(0)`) and never do this in real code. On the current
tree you won't even get as far as comparing: the second import of the models package
re-declares its tables on the shared metadata and raises
`InvalidRequestError: Table '...' is already defined for this MetaData instance` — the same
defect, failing early. Finally, run the `grep` above and confirm it prints nothing.

### Common mistakes

- **`from app.models…` or relative imports** in new code.
- **Re-exporting classes in `__init__.py`** and importing from the package — a second
  address.
- **Import-time side effects** (starting a scheduler, opening a connection) outside the
  lifespan or a `__main__` guard.
- **Running from the wrong directory.** Root, always.

### Key takeaways

- A module runs once per interpreter, cached by its import string; top-level names are
  singletons.
- Two spellings → two modules → two classes → SQLAlchemy errors. Fully qualified imports
  from the repository root, always.
- `backend/app/models/__init__.py` is the core model registry: `CORE_MODEL_MODULES` plus
  `register_core_models()`. Module models register through `hooks.register_model_module()`;
  `lint-imports` keeps `backend.app` from importing `modules`.
- Local imports break cycles; `__main__` guards script behaviour; run from the root.

---

## Hour 10 — Classes: `__init__`, methods, `@property`, `@staticmethod`, `@classmethod`

### What & why

A **class** bundles state and behaviour. `__init__(self, ...)` initialises an instance;
`self` is the instance, passed automatically to every regular method. Attributes are set by
assignment (`self.db = db`) — there's no declaration step — and are public unless prefixed
with `_` (convention: internal) or `__` (name-mangled: rarely used here).

Three method flavours, and when this codebase uses each:

| Decorator        | First argument | Use when                                                                 | Example                            |
| ---------------- | -------------- | ------------------------------------------------------------------------ | ---------------------------------- |
| (none)           | `self`         | The method needs instance state                                           | `AssignmentService.assign_user`    |
| `@staticmethod`  | —              | A pure helper that belongs with the class for organisation               | `ExperimentService.generate_key`   |
| `@classmethod`   | `cls`          | A factory or a helper that needs the class (subclass-aware constructors) | `APIKey.create_for_user`, `TickResult.coerce` |
| `@property`      | `self`         | A computed attribute read without parentheses                            | `APIKey.is_valid`                  |

**Inheritance** (`class Experiment(Base, BaseModel)`) composes behaviour from parents; in
this codebase it's used for ORM models (two bases), Pydantic schemas (`Base → Create →
Update`), settings (`Settings → DevSettings`), and exceptions. Multiple inheritance is
_mixins_ — small classes that contribute attributes — not deep hierarchies.

#### Why services are classes with a session

Every service is `class XService: def __init__(self, db: Session)`. It's not for
polymorphism; it's for **dependency injection by constructor**: the session (and composed
services) are given once, every method uses `self.db`, and a test can construct the service
with a `MagicMock` session. A module of functions taking `db` as the first parameter would
work too; the class groups them and makes the composition (`self.event_service =
EventService(db)`) explicit.

### In the wild

A service: constructor injection, composition, and a private helper:

```python
# 📁 backend/app/services/assignment_service.py
class AssignmentService:
    """
    Service for managing user assignments to experiment variants.
    ...
    """

    def __init__(self, db: Session):
        """Initialize with a database session."""
        self.db = db
        self.event_service = EventService(db)
        self.rules_evaluation_service = RulesEvaluationService()
        self.global_holdout_service = GlobalHoldoutService(db)
        self.mutual_exclusion_service = MutualExclusionService(db)

    def assign_user(self, user_id, experiment_id, ...) -> Dict[str, Any]:
        ...
            variant_id = self._hash_user_to_variant(user_id, experiment)
        ...

    def _hash_user_to_variant(self, user_id: str, experiment: Experiment) -> Union[str, UUID]:
        ...
```

`_hash_user_to_variant` takes `self` but doesn't use instance state — it could be a
`@staticmethod`. It isn't, and that's fine: the leading underscore says "internal," and
tests reach it as `AssignmentService(db)._hash_user_to_variant(...)`.

`@property` for a computed boolean, and a `@classmethod` factory that returns two things:

```python
# 📁 backend/app/models/api_key.py
class APIKey(Base, BaseModel):
    ...
    @property
    def is_expired(self) -> bool:
        """Check if the API key is expired."""
        if not self.expires_at:
            return False
        return self.expires_at < datetime.utcnow()

    @property
    def is_valid(self) -> bool:
        """Check if the API key is valid (active and not expired)."""
        return self.is_active and not self.is_expired

    @classmethod
    def create_for_user(cls, db_session, user_id, name, description=None, scopes=None, expires_at=None):
        """Create a new API key for a user and return (model, plaintext_key)."""
        from backend.app.core.security import hash_api_key

        plaintext_key = generate_api_key()
        key_hash = hash_api_key(plaintext_key)
        api_key = cls(
            user_id=user_id, key=key_hash, name=name, description=description, scopes=scopes, expires_at=expires_at,
        )
        db_session.add(api_key)
        db_session.commit()
        db_session.refresh(api_key)
        return api_key, plaintext_key
```

`api_key.is_valid` reads like an attribute and computes on access — the caller in `deps`
writes `if not api_key or not api_key.is_valid`. The factory uses `cls(...)` rather than
`APIKey(...)` so a subclass would construct itself; it's also the only place the plaintext
key exists, which is a design decision expressed as "the factory returns a tuple."

`@staticmethod` for pure helpers grouped on the class, and `@classmethod` for a coercing
constructor:

```python
# 📁 backend/app/services/cuped_service.py
class CupedService:
    """Service for CUPED and Winsorization variance-reduction methods."""

    @staticmethod
    def compute_theta(control_Y: np.ndarray, control_X: np.ndarray) -> float:
        ...
```

```python
# 📁 backend/app/core/scheduler_tick.py
@dataclass
class TickResult:
    """What a scheduler tick reports back."""

    items_processed: int = 0
    items_failed: int = 0
    metadata: dict = field(default_factory=dict)

    @classmethod
    def coerce(cls, value: Any) -> "TickResult":
        if isinstance(value, TickResult):
            return value
        if isinstance(value, Mapping):
            return cls(
                items_processed=int(value.get("items_processed", 0) or 0),
                items_failed=int(value.get("items_failed", 0) or 0),
                metadata=dict(value.get("metadata") or {}),
            )
        return cls()
```

`TickResult.coerce(anything)` accepts a `TickResult`, a dict, or garbage, and always
returns a `TickResult` — the shape every scheduler's return value is normalised to.

Inheritance as mixins (ORM) and as defaults (settings); `super().__init__` to extend a
constructor:

```python
# 📁 backend/app/models/user.py
class User(Base, BaseModel):
    ...
    def __init__(self, **kwargs):
        """Initialize a user with proper handling of full_name and preferences."""
        # Extract full_name if present and not None
        full_name = kwargs.pop('full_name', None)
        ...
        # Initialize using parent class
        super().__init__(**kwargs)
```

```python
# 📁 backend/app/core/config.py
class TestSettings(Settings):
    """Test environment settings."""

    ENVIRONMENT: EnvironmentName = "test"
    ...
    POSTGRES_DB: str = "experimentation_test"
    POSTGRES_SCHEMA: str = "test_experimentation"
```

`__repr__` so objects print usefully in logs and test failures:

```python
# 📁 backend/app/models/assignment.py
    def __repr__(self):
        return f"<Assignment user={self.user_id} experiment={self.experiment_id} variant={self.variant_id}>"
```

> **Watch Out!** A class attribute is shared by every instance; an instance attribute is
> per object. `class Cache: entries = {}` shares one dict across all caches — the class-level
> version of the mutable-default trap. Initialise mutable state in `__init__`
> (`self._cache = OrderedDict()`), as `EvaluationCache` and `RuleCompiler` do. Pydantic
> models and dataclasses are exempt (they copy per instance).

> **Did You Know?** Python has no interfaces, and this codebase doesn't miss them much —
> "anything with `.send(method, url, headers, data, timeout)`" is a `Transport` in the Python
> SDK, and `FakeTransport` in tests satisfies it by having the method (duck typing). Where a
> contract needs to be _stated_, `typing.Protocol` or an `abc.ABC` with `@abstractmethod`
> exists. The SDK's `Transport` is a `Protocol` (`class Transport(Protocol)` in
> `sdk/python/experimentation/transport.py`): it _states_ the `send` signature for type
> checkers, yet `FakeTransport` never inherits from it — matching the method is enough. That
> is structural typing, the checked form of duck typing.

### Try It Yourself

```python
>>> class Bucketer:
...     """Deterministic bucketing for one salt."""
...     BUCKETS = 100                      # class attribute (shared, immutable — fine)
...     def __init__(self, salt: str):
...         self.salt = salt
...         self._calls = 0                # instance state
...     def bucket(self, user_id: str) -> int:
...         import hashlib
...         self._calls += 1
...         return int(hashlib.md5(f"{user_id}:{self.salt}".encode()).hexdigest(), 16) % self.BUCKETS
...     @property
...     def calls(self) -> int: return self._calls
...     @staticmethod
...     def normalise(user_id: str) -> str: return user_id.strip().lower()
...     @classmethod
...     def for_flag(cls, key: str) -> "Bucketer": return cls(salt=key)
...     def __repr__(self): return f"<Bucketer salt={self.salt!r} calls={self._calls}>"
>>> b = Bucketer.for_flag("search_v2"); b.bucket("user-42"); b.calls; b
```

Then, in the real code: `APIKey.is_valid` is a property — what happens if you write
`api_key.is_valid()`? Try it on an instance from the database.

### Common mistakes

- **Forgetting `self`** in a method signature — `TypeError: takes 0 positional arguments but
  1 was given`.
- **Calling a property with parentheses** — `'bool' object is not callable`.
- **Mutable class attributes** as shared state.
- **Deep inheritance** where composition (a service holding another service) reads better.

### Key takeaways

- `__init__` sets instance state; `self` is implicit; `_name` is "internal by convention."
- `@staticmethod` (no state), `@classmethod` (factory, `cls(...)`), `@property` (computed
  attribute).
- Services inject the session by constructor and compose other services; mixins contribute
  columns; `super().__init__` extends constructors.
- `__repr__` on anything that appears in logs or assertions.

---

## Hour 11 — Dataclasses and Enums

### What & why

Two decorators/base classes give data a shape with almost no code:

**`@dataclass`** generates `__init__`, `__repr__` and `__eq__` from annotated class
attributes. Use it for records that are _yours_ and _internal_: results of a computation,
outcomes of a tick, per-variant stats. Defaults are per-field; mutable defaults use
`field(default_factory=list)`. `frozen=True` makes instances immutable (hashable). Compared
with a dict, a dataclass has named, typed fields that `mypy` checks and that can't be
misspelled; compared with a Pydantic model, it does no validation and no JSON — right for
internal data, wrong at an API boundary.

**`Enum`** gives a closed set of named constants. `class Status(str, Enum)` — inheriting
from `str` too — makes members _also_ strings, so `Status.ACTIVE == "active"`, JSON
serialisation works, and Pydantic accepts either the member or its value. Members have
`.name` (`"ACTIVE"`) and `.value` (`"active"`); `Status("active")` looks one up by value,
`Status["ACTIVE"]` by name.

#### Why both exist next to Pydantic

The boundary uses Pydantic (validation, JSON). The interior uses dataclasses (cheap,
typed, no validation cost on every construction). The results engine computes an
`MSPRTResult` dataclass and the endpoint copies it into a Pydantic response model. Enums
sit in both worlds: the ORM's `ExperimentStatus` (plain `enum.Enum`, stored by name in a
PostgreSQL enum type) and the schema's `ExperimentStatus` (`str, Enum`, serialised by
value) are _separate classes_ with the same values, converted in the service.

### In the wild

A dataclass with a default factory and a coercing classmethod:

```python
# 📁 backend/app/core/scheduler_tick.py
@dataclass
class TickResult:
    """What a scheduler tick reports back."""

    items_processed: int = 0
    items_failed: int = 0
    metadata: dict = field(default_factory=dict)


@dataclass
class TickOutcome:
    """Summary of one call to :func:`run_locked_tick`."""

    name: str
    status: str
    acquired: bool
    started_at: datetime
    completed_at: datetime
    duration_seconds: float
    result: TickResult
    error: Optional[str] = None

    @property
    def ran(self) -> bool:
        return self.acquired
```

Required fields first, defaulted fields after — a dataclass enforces that ordering. A
dataclass with a `to_dict` for the boundary and a property doing arithmetic:

```python
# 📁 backend/app/services/srm_service.py
@dataclass
class SRMResult:
    """Outcome of a sample-ratio-mismatch test.
    ...
    """

    chi2: float
    p_value: float
    warning: bool
    expected: Dict[str, float] = field(default_factory=dict)
    observed: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Plain-dict form used by the API schema and JSON snapshots."""
        return {"chi2": self.chi2, "p_value": self.p_value, "warning": self.warning,
                "expected": dict(self.expected), "observed": dict(self.observed)}
```

```python
# 📁 backend/app/services/bandit_service.py
@dataclass
class VariantStats:
    """Accumulated statistics for a single variant arm."""

    variant_id: str
    successes: int = 0
    failures: int = 0
    pulls: int = 0
    total_reward: float = 0.0

    @property
    def mean_reward(self) -> float:
        """Average reward per pull (0.0 when no pulls recorded)."""
        return self.total_reward / self.pulls if self.pulls > 0 else 0.0

    @property
    def conversion_rate(self) -> float:
        """Empirical conversion rate = successes / pulls (0.0 when no pulls)."""
        return self.successes / self.pulls if self.pulls > 0 else 0.0
```

`VariantStats(variant_id="a")` — every other field defaults — is the zero prior the
scheduler falls back to; the properties guard the division by zero.

Enums, in both flavours, and the role hierarchy expressed as a list of members:

```python
# 📁 backend/app/models/experiment.py
class ExperimentStatus(enum.Enum):
    """Experiment status enum."""

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"
```

```python
# 📁 backend/app/models/user.py
class UserRole(str, enum.Enum):
    """User roles in the system."""
    ADMIN = "admin"
    ANALYST = "analyst"
    DEVELOPER = "developer"
    VIEWER = "viewer"
```

```python
# 📁 backend/app/core/cognito.py
    # Define role hierarchy (highest privilege first)
    role_hierarchy = [UserRole.ADMIN, UserRole.DEVELOPER, UserRole.ANALYST, UserRole.VIEWER]
    ...
    # Return highest privilege role if any matches found
    for role in role_hierarchy:
        if role in matching_roles:
            return role
```

Looking members up by name _or_ by value — the service resolves whatever string a client
sent by iterating the enum, and returns `None` instead of raising on a miss:

```python
# 📁 backend/app/services/experiment_service.py
    if isinstance(value, enum_cls):
        return value
    if value is None:
        return None

    text = str(getattr(value, "value", value)).strip().lower()
    if not text:
        return None

    for member in enum_cls:
        if str(member.value).lower() == text or member.name.lower() == text:
            return member
    return None
```

`for member in enum_cls` — iterating an enum class yields its members in definition order;
each has `.name` (`"DRAFT"`) and `.value` (`"draft"`). `resolve_experiment_status(value)`
wraps this, and the caller decides what an unknown status means:

```python
# 📁 backend/app/services/experiment_service.py
        if "status" in obj_data and isinstance(obj_data["status"], str):
            resolved_status = resolve_experiment_status(obj_data["status"])
            if resolved_status is None:
                logger.warning(
                    "Unknown experiment status %r; defaulting to DRAFT",
                    obj_data["status"],
                )
                resolved_status = ExperimentStatus.DRAFT
            obj_data["status"] = resolved_status
```

On create an unknown value becomes `DRAFT` with a warning; on update it is dropped and the
status is left unchanged. Returning `None` instead of raising lets each caller choose.

And the `str, Enum` payoff — comparing a member to a raw string works, which the flag
service relies on because some code paths store the value rather than the member:

```python
# 📁 backend/app/services/feature_flag_service.py
            if flag.status not in (FeatureFlagStatus.ACTIVE, FeatureFlagStatus.ACTIVE.value):
```

> **Watch Out!** `ExperimentStatus["ACTIVE"]` (by name) and `ExperimentStatus("active")`
> (by value) are different lookups, and both raise on a miss (`KeyError` vs `ValueError`).
> The service avoids both by iterating the members itself and returning `None`. And a plain `enum.Enum` member is _not_ equal to its value —
> `ExperimentStatus.ACTIVE == "active"` is `False` — which is why the ORM enums and the
> schema enums must be converted at the boundary, never compared across.

> **Did You Know?** `@dataclass(frozen=True)` makes a hashable, immutable record — suitable
> as a dict key or a set member. `CacheKey` in `core/evaluation_cache.py` does this by hand
> (`__hash__`/`__eq__` on a precomputed digest) because it needs a custom hash of a sorted
> JSON dump; a frozen dataclass over `(rule_id, context_json)` would be the shorter
> modern spelling.

### Try It Yourself

```python
>>> from dataclasses import dataclass, field, asdict
>>> @dataclass
... class Arm:
...     variant_id: str
...     successes: int = 0
...     pulls: int = 0
...     tags: list = field(default_factory=list)
...     @property
...     def rate(self): return self.successes / self.pulls if self.pulls else 0.0
>>> a = Arm("control", successes=3, pulls=10); a, a.rate, asdict(a)
>>> Arm("x") == Arm("x"), Arm("x").tags is Arm("x").tags      # (True, False) — per-instance defaults

>>> from enum import Enum
>>> class Status(str, Enum):
...     DRAFT = "draft"; ACTIVE = "active"
>>> Status.ACTIVE == "active", Status("active"), Status["ACTIVE"], Status.ACTIVE.name, Status.ACTIVE.value
>>> import json; json.dumps({"s": Status.ACTIVE})                # works because it's a str
>>> from backend.app.models.experiment import ExperimentStatus
>>> ExperimentStatus.ACTIVE == "active"                           # False — plain Enum
>>> [m.value for m in ExperimentStatus]
```

### Common mistakes

- **A mutable default on a dataclass field** (`tags: list = []`) — a `ValueError` at class
  creation; use `field(default_factory=list)`.
- **Required fields after defaulted ones** — a `TypeError` at class creation.
- **Comparing an ORM `Enum` member to a string.** Convert at the boundary.
- **Using a dataclass where validation is needed.** That's a Pydantic model.

### Key takeaways

- `@dataclass` → `__init__`/`__repr__`/`__eq__` for free; `field(default_factory=...)` for
  mutables; `frozen=True` for hashable records; properties for derived values.
- Dataclasses inside, Pydantic at the boundary, `to_dict()` between.
- `class X(str, Enum)` members are strings (JSON-safe, comparable); plain `Enum` members
  aren't; look up by `X["NAME"]` or `X("value")` and catch both errors.
- The ORM enums and schema enums are separate classes with equal values.

---

## Hour 12 — Type hints: `Optional`, `Union`, generics, `Literal`, and `mypy`

### What & why

A **type hint** annotates a name with the type it's meant to hold: `user_id: str`,
`def f(x: int) -> bool`. Python ignores them at runtime. Three tools don't:

- **`mypy`** checks that calls match signatures — the tool that would have caught
  `get_counters`. It is configured in `pyproject.toml` (`[tool.mypy]`) and installed in the
  venv, but you run it yourself: no CI job, `make` target or pre-commit hook runs it.
- **Pydantic** turns annotations into runtime validation on models.
- **FastAPI** turns endpoint annotations into request parsing and documentation.

The vocabulary, from `typing` (and, since 3.10, the built-in forms in parentheses):

| Hint                          | Meaning                                                 | Modern form              |
| ----------------------------- | ------------------------------------------------------- | ------------------------ |
| `Optional[X]`                 | `X` or `None`                                           | `X \| None`              |
| `Union[X, Y]`                 | `X` or `Y`                                              | `X \| Y`                 |
| `List[X]`, `Dict[K, V]`, `Set[X]`, `Tuple[A, B]` | typed containers                     | `list[X]`, `dict[K, V]`… |
| `Any`                         | opt out of checking                                     |                          |
| `Callable[[A], R]`            | a function taking `A` returning `R`                     |                          |
| `Awaitable[R]`, `Generator`, `Iterator` | async/iteration protocols                     |                          |
| `Literal["a", "b"]`           | exactly one of these values                             |                          |
| `TypeVar` + `Generic[T]`      | a class parameterised by a type                         |                          |
| `Annotated[X, meta]`          | `X` with attached metadata (Pydantic reads it)          |                          |

This codebase uses the `typing` capitalised forms (`Optional`, `Dict`, `List`) because
parts of it predate 3.10; both are accepted; match the file you're in.

#### What a hint buys you here

Read `Optional[Dict[str, VariantStats]]` on `_stats_from_dynamodb`: the caller learns "may
be `None` (unavailable) or a dict keyed by variant id," and `mypy` will flag
`stats["x"]` without a `None` check. Read `Literal["local", "cognito"]` on
`AUTH_PROVIDER`: Pydantic refuses `"okta"` at startup. Read `Callable[[], Awaitable[Any]]`
on `run_locked_tick`'s `tick`: it must be a zero-argument coroutine function — pass a bound
method, not its result.

### In the wild

`Optional`, `Union`, containers and a documented return shape:

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
```

`Union[str, UUID]` says "we accept either" — and the body does `str(experiment_id)` where
it matters. `-> Dict[str, Any]` is honest (the service returns dicts) and weak (`Any` values);
the endpoint's response model is where the strong type lives.

`Literal` and `Annotated` on settings — hints that Pydantic _enforces_:

```python
# 📁 backend/app/core/config.py
EnvironmentName = Literal["development", "test", "staging", "production"]
...
    ENVIRONMENT: EnvironmentName = "development"
    AUTH_PROVIDER: Literal["local", "cognito"] = "local"
    LOG_FORMAT: Optional[Literal["json", "console"]] = None
    CORS_ORIGINS: Annotated[List[str], NoDecode] = []
```

A type alias (`EnvironmentName = Literal[...]`) names a type for reuse; `Annotated[List[str],
NoDecode]` keeps the type and attaches a marker pydantic-settings reads.

`Callable`/`Awaitable` for a function parameter, and a `Generic` container:

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
# 📁 backend/app/core/pagination.py
T = TypeVar("T")


class Paginator(Generic[T]):
    """
    A utility class for handling pagination in API endpoints.
    ...
    """

    def __init__(self, skip: int = 0, limit: int = 100):
        self.skip = max(0, skip)
        self.limit = max(1, min(limit, 500))  # Ensure limit is between 1 and 500

    def get_paginated_response(self, items: List[T], total: int) -> Dict[str, Any]:
```

`Paginator[Experiment]` tells the checker that `items` is a `List[Experiment]` for that
paginator — the same generic mechanism as the SDK's `UserKeyCache[Assignment]`.

Forward references and `from __future__ import annotations` — a method returning its own
class:

```python
# 📁 backend/app/core/scheduler_tick.py
from __future__ import annotations
...
    @classmethod
    def coerce(cls, value: Any) -> "TickResult":
```

With the `__future__` import, every annotation is stored as a string and evaluated lazily,
so `-> TickResult` (no quotes) would also work; the quotes are belt-and-braces. Without the
import, a class can't name itself in its own body except as a string.

Typed containers that document the data flow of a whole algorithm:

```python
# 📁 backend/app/core/bandit_scheduler.py
    def get_variant_stats_from_counters(
        self,
        experiment_id: UUID,
        variant_ids: List[str],
        experiment: Optional[Experiment] = None,
    ) -> Dict[str, VariantStats]:
```

`Any`, used deliberately for "we accept JSON of unknown shape here," with a comment where it
matters:

```python
# 📁 backend/app/schemas/feature_flag.py
    targeting_rules: Optional[Any] = Field(
        None, description=TARGETING_RULES_DESCRIPTION
    )
```

(Three stored shapes; the adapter normalises; `Any` is the honest type. Everywhere else,
`Any` in a signature is a review comment.)

> **Watch Out!** `Optional[X]` means "or `None`" — it does **not** mean "has a default."
> `def f(x: Optional[int])` still requires `x` to be passed. Write `x: Optional[int] = None`
> for optional-with-default. Pydantic v2 is strict about this too: `Optional[str]` without
> `= None` is a _required_ field that may be null.

> **Did You Know?** `mypy` is _not_ a gate here, and the reason is a number: at the
> reference commit `mypy backend/app/` reports 931 errors in 140 files. The `[tool.mypy]`
> settings are strict on paper (`disallow_untyped_defs = true`), the code is not there yet,
> and a gate that fails on every pull request would be switched off. What the codebase does
> keep is the habit: where the ORM's dynamic attributes defeat the checker, an ignore names
> its error code (`return dynamo_stats  # type: ignore[return-value]` in the bandit
> scheduler). Ignore the specific thing, say which.

### Try It Yourself

```python
>>> from typing import Optional, Union, Dict, List, Literal, Callable
>>> def bucket(user_id: str, salt: str, buckets: int = 100) -> int: ...
>>> bucket.__annotations__
{'user_id': <class 'str'>, 'salt': <class 'str'>, 'buckets': <class 'int'>, 'return': <class 'int'>}
>>> def maybe(x: Optional[int]) -> int: return x + 1      # mypy: unsupported operand for None
>>> def safe(x: Optional[int]) -> int: return (x or 0) + 1  # narrowed
```

Write a small file `scratch_types.py`:

```python
from typing import Optional

class Counters:
    def get_experiment_counters(self, experiment_id: str) -> dict: return {}

def stats(c: Counters, experiment_id: str) -> Optional[dict]:
    return c.get_counters(experiment_id)      # the bug from Hour 1
```

and run `mypy scratch_types.py` from the repository root (so it picks up the `[tool.mypy]`
settings) — among its two errors is `"Counters" has no attribute "get_counters"
[attr-defined]`. That's the whole argument for hints in one command. Then try a real file:
`mypy backend/app/core/scheduler_tick.py`. It reports nothing in `scheduler_tick.py` itself,
but mypy follows imports, so it prints a few hundred errors from the modules it pulled in —
the 931 above, seen from one corner.

### Common mistakes

- **`Optional[X]` as "optional parameter."** Add `= None`.
- **`Any` to make `mypy` quiet.** Fix the type or ignore the specific error code.
- **`List` vs `list`, `Dict` vs `dict`** mixed in one file. Match the file.
- **Returning `Dict[str, Any]` from a service and then trusting the keys.** The response
  model is where trust comes from.

### Key takeaways

- Hints are for `mypy`, Pydantic and FastAPI; the interpreter ignores them.
- `Optional`/`Union`/typed containers/`Callable`/`Literal`/`Generic`/`Annotated` — the
  vocabulary the codebase uses; `typing` forms, matching the file.
- `Optional[X]` ≠ default; `Any` is a confession; `# type: ignore[code]` names its reason.
- `mypy` on `get_counters` would have caught it — if someone ran it. It is configured, not
  gated; tests are what the CI enforces.

---

## Hour 13 — Exceptions: raising, catching, chaining, and the re-raise idiom

### What & why

An **exception** unwinds the call stack until something catches it. `raise ValueError("…")`
throws; `try:` / `except ValueError as e:` catches; `else:` runs if nothing was raised;
`finally:` always runs (cleanup). Built-ins you'll meet: `ValueError` (bad value),
`TypeError` (wrong type), `KeyError`/`AttributeError` (missing), `RuntimeError`,
`asyncio.CancelledError` (a `BaseException` since 3.8 — it does _not_ inherit from
`Exception`).

Four disciplines separate exception handling that helps from exception handling that hides:

1. **Catch specifically.** `except (InvalidTokenError, ValueError, TypeError)` says what you
   expect. `except Exception` is a last resort that must log, and it must come _after_ any
   re-raise of exceptions you don't own.
2. **Re-raise what you can't handle.** The `except HTTPException: raise` idiom: an
   exception that already means "respond with this status" must pass through a generic
   handler untouched. Likewise `except asyncio.CancelledError: raise` in the scheduler
   runner.
3. **Chain with `raise … from exc`.** When you translate a low-level error into a domain
   error, keep the cause; tracebacks show both.
4. **Define domain exceptions.** `InvalidTokenError(ValueError)`, `AccountLockedError`,
   `InvalidCredentialsError` — small classes that let callers catch the _meaning_, not the
   mechanism.

#### The exception that hid the bug

Hour 1's `get_counters` survived because of `except Exception: logger.warning(...); return
None`. That handler was _correct_ for its purpose (DynamoDB unavailable → fall back) and
_catastrophic_ for a typo: it converted a programming error into a silent degradation. The
lesson isn't "never catch broadly" — the fallback is a feature — it's "a broad catch must be
loud (log at warning, count a metric) and must be tested with the real collaborator."

### In the wild

Domain exceptions — small, named, sometimes carrying data:

```python
# 📁 backend/app/core/security.py
class InvalidTokenError(ValueError):
    """Raised when a local access token cannot be decoded or fails validation."""
```

```python
# 📁 backend/app/services/local_auth_service.py
class AccountLockedError(Exception):
    """Raised when the address has exceeded the failed-login threshold."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(
            f"Too many failed login attempts; retry in {self.retry_after_seconds}s"
        )


class InvalidCredentialsError(Exception):
    """Raised for unknown e-mail, wrong password, or an inactive account."""
```

Translating with a chain — a library error becomes a domain error, cause preserved:

```python
# 📁 backend/app/core/security.py
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[LOCAL_TOKEN_ALGORITHM],
                          issuer=LOCAL_TOKEN_ISSUER, options={"require": ["sub", "exp", "iat", "iss"]})
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
```

Catching a tuple of specific exceptions and mapping each meaning to a response:

```python
# 📁 backend/app/api/deps.py
    try:
        claims = decode_local_token(token)
        user_id = UUID(str(claims.get("sub")))
    except (InvalidTokenError, ValueError, TypeError) as exc:
        logger.debug(f"Local token rejected: {exc}")
        raise _credentials_exception()
```

```python
# 📁 backend/app/api/v1/endpoints/auth.py
    try:
        user = local_auth_service.authenticate(db, email, password)
    except AccountLockedError as exc:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=..., headers={"Retry-After": str(exc.retry_after_seconds)})
    except InvalidCredentialsError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=INVALID_CREDENTIALS_DETAIL, ...)
```

The re-raise idiom, and `finally` for cleanup:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
    except HTTPException:
        # Let deliberate 4xx responses through instead of wrapping them in a 500.
        raise
    except Exception as e:
        raise unexpected_failure(
            e,
            "Experiment start",
            "Could not start the experiment",
            db=db,
            logger=logger,
        )
```

`unexpected_failure` (in `backend/app/core/logger.py`) is the house answer to "something I
didn't plan for went wrong in an endpoint." It rolls the session back, logs the exception
_type_ and full traceback under the request's ID, and returns an `HTTPException` whose
`detail` is a fixed sentence plus that ID — never `str(e)`:

```python
# 📁 backend/app/core/logger.py
    log = logger if logger is not None else logging.getLogger(__name__)
    if db is not None:
        try:
            db.rollback()
        except Exception:
            log.warning("Rollback after a failed %s also failed", operation)
    # What logger.exception() logs, from outside the except block: ERROR,
    # with the traceback.
    log.error("%s failed (%s)", operation, type(exc).__name__, exc_info=exc)
    return HTTPException(status_code=status_code, detail=failure_detail(sentence))
```

Why not `detail=str(e)`? An exception's text is written for the developer, not the caller: a
database error repeats the SQL and its parameters, a driver error can name a host. Putting it
in a response body hands internals to whoever sent the request. The caller gets
`"Could not start the experiment (request ID: <id>)."`; the operator finds the traceback in
the log by that ID. Note also that the function _returns_ the exception and the caller
writes `raise` — so the `raise` is visible at the call site and type checkers know the
branch ends there. A failed rollback is logged, not raised, so it can't replace the original
error.

The scheduler isolates each row in its own **savepoint** (a nested transaction), so one row
the database refuses is rolled back alone and the rest of the tick carries on:

```python
# 📁 backend/app/core/scheduler.py
            for experiment in experiments_to_activate:
                experiment_id = experiment.id
                savepoint = db.begin_nested()
                try:
                    ...
                    db.flush()
                    savepoint.commit()
                except Exception:
                    savepoint.rollback()
                    failed_count += 1
                    logger.exception("Error activating experiment %s", experiment_id)
                    continue
                activated_count += 1
        ...
        except Exception as e:
            failed_count += 1
            logger.error(f"Error processing scheduled experiments: {e!s}")
        finally:
            db.close()
```

`continue` inside the `except` skips to the next row; `logger.exception` logs at ERROR with the
traceback. The outer `except` counts the failure (the tick reports `items_failed`), and
`finally` closes the session whatever happened.

The scheduler runner, which records a failure _and_ re-raises — and lets cancellation
through without recording anything:

```python
# 📁 backend/app/core/scheduler_tick.py
        try:
            raw = await tick()
            ...
        except asyncio.CancelledError:
            # Shutdown: release the lock (context manager) and propagate
            # without recording a bogus "failed" run.
            raise
        except Exception as exc:
            status = STATUS_FAILED
            error = str(exc) or exc.__class__.__name__
            exc_to_raise = exc
    ...
    if exc_to_raise is not None:
        raise exc_to_raise
```

The broad catch is acceptable because nothing is swallowed: the failure is recorded, then
re-raised. (Older versions carried `# noqa: BLE001`, the "blind except" lint code. Ruff does
not select that rule here, and its `RUF100` check removes `noqa` comments that suppress
nothing, so the comment went.) `str(exc) or exc.__class__.__name__` handles exceptions with
empty messages; this text goes to the scheduler's run record, not to an HTTP caller.

A broad catch that's _correct_ — a best-effort side path that must never fail the main one:

```python
# 📁 backend/app/services/feature_flag_service.py
        finally:
            ...
            try:
                MetricsService.record_flag_evaluation(...)
                ...
            except Exception as metrics_error:
                # Don't let metrics collection errors affect flag evaluation
                # The type only: a database error's text repeats the row's
                # parameters, the user id among them.
                logger.error(
                    f"Failed to record metrics: {type(metrics_error).__name__}"
                )
```

Even the log line logs only `type(metrics_error).__name__`, for the reason the comment gives.

and the `else` clause, rarely seen but exact — run this only if the `try` didn't raise:

```python
# (illustrative — the shape for "commit only if the work succeeded")
try:
    result = compute()
except ValueError as exc:
    logger.warning("bad input: %s", exc)
else:
    db.commit()
finally:
    db.close()
```

> **Watch Out!** `except Exception:` does not catch `asyncio.CancelledError`,
> `KeyboardInterrupt` or `SystemExit` (they derive from `BaseException`). That's usually what
> you want. `except BaseException:` or a bare `except:` catches them and will make a
> scheduler impossible to stop. Never write a bare `except:`.

> **Did You Know?** Exceptions are how Pydantic and FastAPI communicate: a validator raises
> `ValueError`, Pydantic wraps it in `ValidationError`, FastAPI's handler turns that into a
> 422. Raising `HTTPException` inside a validator short-circuits none of that — it's just
> another exception wrapped into a confusing `ValidationError`. Raise `ValueError` in
> validators.

### Try It Yourself

```python
>>> class InvalidTokenError(ValueError): pass
>>> def decode(token):
...     try:
...         return {"sub": token.split(".")[1]}
...     except IndexError as exc:
...         raise InvalidTokenError("malformed token") from exc
>>> decode("abc")             # note the chained traceback: "The above exception was the direct cause..."
>>> try: decode("abc")
... except (InvalidTokenError, TypeError) as e: print(type(e).__name__, e, "| cause:", repr(e.__cause__))
>>> def risky(n):
...     try:
...         if n == 0: raise ZeroDivisionError("nope")
...         return 10 / n
...     except ZeroDivisionError:
...         raise                       # re-raise unchanged
...     finally:
...         print("cleanup runs regardless")
>>> risky(2); risky(0)
>>> import asyncio
>>> issubclass(asyncio.CancelledError, Exception), issubclass(asyncio.CancelledError, BaseException)
(False, True)
```

Then find every `except Exception` in `backend/app/api/v1/endpoints/experiments.py` and
check that each is preceded by `except HTTPException: raise`.

### Common mistakes

- **Bare `except:`** — catches `KeyboardInterrupt` and cancellation.
- **`except Exception` before `except HTTPException`** (or with no re-raise) — 4xx becomes
  500.
- **Swallowing silently** (`except Exception: pass`). At minimum, log at warning.
- **Losing the cause** — use `raise New(...) from exc`.

### Key takeaways

- `try/except/else/finally`; catch specific types in tuples; `finally` for cleanup.
- Re-raise what you can't handle (`HTTPException`, `CancelledError`); chain with `from`.
- Domain exceptions (`InvalidTokenError`, `AccountLockedError`) carry meaning and data.
- A broad catch is a feature only when it's loud and tested against the real collaborator.

---

## Part III Workshop

**Q1. Why does importing the same file as `backend.app.models.metrics.metric` and
`app.models.metrics.metric` break SQLAlchemy?**
Modules are cached by import string; two strings mean two module objects and two
`RawMetric` classes mapped to one table — "Class is not mapped" at query time.

**Q2. When would you choose `@classmethod` over `@staticmethod`?**
When the method constructs or refers to the class (`cls(...)`) — a factory like
`APIKey.create_for_user` or a coercer like `TickResult.coerce`. A `@staticmethod` is a pure
helper that happens to live on the class.

**Q3. What does `class UserRole(str, enum.Enum)` give you that `class
ExperimentStatus(enum.Enum)` doesn't?**
Members that are also strings: `UserRole.ADMIN == "admin"` is `True`, `json.dumps` works,
and Pydantic accepts either. Plain `Enum` members compare unequal to their values.

**Q4. `def f(x: Optional[int])` — is `x` optional?**
No; it's required and may be `None`. `def f(x: Optional[int] = None)` is optional.

**Q5. Why must `except HTTPException: raise` come before `except Exception`?**
`HTTPException` is an `Exception`; without the earlier clause the generic handler would
catch a deliberate 404 or 409 and turn it into `unexpected_failure`'s 500 — wrong status,
and the caller loses the reason the code meant to give.

**Q6. Does `except Exception` catch `asyncio.CancelledError`?**
No (3.8+): it derives from `BaseException`. A bare `except:` or `except BaseException:`
would — and would make a scheduler unstoppable.

**Quiz.** Which is a dataclass and which is a Pydantic model, and why: `TickResult`,
`ExperimentCreate`, `SRMResult`, `Settings`?
→ `TickResult` and `SRMResult` are dataclasses (internal records, no validation);
`ExperimentCreate` and `Settings` are Pydantic (boundary/config, validated).

---

## Exercises

**E3.1 — Import audit.** Write a shell one-liner (or a tiny Python script) that finds any
`from app.` or relative `from ..` imports under `backend/app/` and reports them; explain what
each found line would break.

**E3.2 — A service class.** Write `HoldoutService` with `__init__(self, db)`, a
`@staticmethod bucket(user_id) -> int` (MD5 first four bytes little-endian `% 100`), a
`@property active_percentage` that reads a row via `self.db`, and `is_in(user_id) -> bool`.

**E3.3 — Records.** Define a `@dataclass class RollbackDecision` (flag key, previous and
target percentage, reason, `metadata` with a factory) with a property `delta`, and a `class
TriggerType(str, Enum)` with `AUTOMATIC`/`MANUAL`. Show `TriggerType("manual")` and
`TriggerType["MANUAL"]`.

**E3.4 — Annotate.** Add complete type hints to:

```python
def stats(counters, experiment_id, variant_ids, experiment=None):
    ...  # returns {variant_id: VariantStats} or None when the counter service is down
def run(tick, *, record=True):
    ...  # tick is a zero-arg coroutine function; returns a TickOutcome
```

**E3.5 — Handle it.** Write an endpoint body that calls `service.rollback(flag_id,
percentage)`, which may raise `FlagNotFound`, `RollbackNotAllowed(reason)` or anything
else, and maps them to 404, 409 (with the reason) and a logged 500 — letting `HTTPException`
through and never catching cancellation.

---

## Solutions

**S3.1**

```bash
git grep -nE "^\s*from (app\.|\.\.)" backend/app || echo "clean"
```

A `from app.` line creates a second module object for the same file (two classes per
table); a `from ..` relative import works only when the package is imported by one name and
breaks the moment the module is run or imported another way. The repository's own check
for the first kind is `grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'`
(no output means clean).

**S3.2**

```python
class HoldoutService:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def bucket(user_id: str) -> int:
        digest = hashlib.md5(f"{user_id}:{HOLDOUT_SALT}".encode(), usedforsecurity=False).digest()[:4]
        return struct.unpack("<I", digest)[0] % 100

    @property
    def active_percentage(self) -> int:
        row = self.db.query(GlobalHoldout).filter(GlobalHoldout.is_active == True).first()
        return row.holdout_percentage if row else 0

    def is_in(self, user_id: str) -> bool:
        return self.bucket(user_id) < self.active_percentage
```

**S3.3**

```python
from dataclasses import dataclass, field
from enum import Enum

class TriggerType(str, Enum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"

@dataclass
class RollbackDecision:
    flag_key: str
    previous_percentage: int
    target_percentage: int
    reason: str
    metadata: dict = field(default_factory=dict)

    @property
    def delta(self) -> int:
        return self.target_percentage - self.previous_percentage

TriggerType("manual") is TriggerType.MANUAL, TriggerType["MANUAL"] is TriggerType.MANUAL   # (True, True)
```

**S3.4**

```python
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import UUID

def stats(
    counters: DynamoDBCounterService,
    experiment_id: UUID,
    variant_ids: List[str],
    experiment: Optional[Experiment] = None,
) -> Optional[Dict[str, VariantStats]]: ...

async def run(tick: Callable[[], Awaitable[Any]], *, record: bool = True) -> TickOutcome: ...
```

(`run` must be `async` to `await tick()`; the hint on `tick` is what makes passing
`self.method()` instead of `self.method` a `mypy` error.)

**S3.5**

```python
    try:
        result = service.rollback(flag_id, percentage)
    except HTTPException:
        raise
    except FlagNotFound:
        raise HTTPException(status_code=404, detail="Feature flag not found")
    except RollbackNotAllowed as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        raise unexpected_failure(
            exc, "Flag rollback", "Could not roll back the feature flag", db=db, logger=logger
        )
    return result
```

`except Exception` doesn't catch `asyncio.CancelledError` (a `BaseException`), so
cancellation passes through untouched; `HTTPException` is re-raised first so a deliberate
refusal inside the service isn't downgraded. The 500 goes through `unexpected_failure`, so
the session is rolled back, the traceback is logged under the request ID, and the response
carries a fixed sentence rather than the exception's text. (The 409 deliberately returns
`str(exc)`: `RollbackNotAllowed` is _your_ exception, and its message is written for the
caller.)

---

**Next:** [Part IV — Idioms →](./part-4-idioms.md)

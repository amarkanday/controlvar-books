# Part II — Pydantic v2

### Hours 5–8

[← Part I](./part-1-foundations.md) · [Contents](./README.md) · [Part III →](./part-3-dependencies-and-auth.md)

Pydantic is the half of FastAPI that decides what a request _means_. This Part is the model
layer from the ground up: fields and their constraints (Hour 5), the two kinds of validator
and where each belongs (Hour 6), the config knobs and the round trip between ORM objects,
models and JSON (Hour 7), and `pydantic-settings`, which turns the same machinery on the
process environment and refuses to start a misconfigured production (Hour 8). Keep a Python
shell open — `from backend.app.schemas.experiment import ExperimentCreate` and try things.

> **Version note.** This codebase is **Pydantic v2** (2.13) throughout: `field_validator` /
> `model_validator` / `ConfigDict` / `model_dump` / `model_validate`. The v1 names
> (`@validator`, `class Config`, `.dict()`, `.parse_obj()`) still exist as deprecated shims and
> are banned in review. `CLAUDE.md` lists the substitutions.

---

## Hour 5 — `BaseModel` and `Field`: constraints as documentation

### What & why

A Pydantic **model** is a class whose annotated attributes are its fields. Constructing it
validates: types are checked and _coerced_ where sensible (`"42"` → `42` for an `int` field
in lax mode; a string → `UUID`; an ISO string → `datetime`), missing required fields raise,
unknown fields are ignored by default, and the result is a plain object with typed
attributes.

`Field(...)` attaches metadata to a field: a default (or `...` for required), numeric bounds
(`ge`, `gt`, `le`, `lt`), string bounds (`min_length`, `max_length`, `pattern`), a
`description` for the docs, and `examples`. Two things make `Field` more than decoration in
a FastAPI app: the constraints are **enforced** (a 422), and they are **published** (the
OpenAPI schema shows `minimum: 0, maximum: 100`).

#### Why the codebase's schemas are layered `Base → Create → Update → Response`

Almost every resource has four models: a `Base` with the fields a client may set, a `Create`
that adds what's required at creation, an `Update` where everything is optional, and a
`Response` (or `InDB`) that adds server-owned fields (`id`, `created_at`) and reads from ORM
objects. It's inheritance used for _shape reuse_, and the layering answers three questions
at once: what may I send? what must I send? what will I get back?

### In the wild

The metric model shows constraints, defaults and documentation on one class:

```python
# 📁 backend/app/schemas/experiment.py
class MetricBase(BaseModel):
    """Base model for metric data."""

    name: str = Field(..., min_length=1, max_length=100, description="Metric name")
    description: Optional[str] = Field(None, description="Metric description")
    event_name: str = Field(..., min_length=1, max_length=100, description="Event name to track")
    metric_type: MetricType = Field(MetricType.CONVERSION, description="Type of metric")
    is_primary: bool = Field(False, description="Whether this is the primary metric")
    aggregation_method: str = Field("average", description="How to aggregate the metric")
    minimum_sample_size: int = Field(100, ge=10, description="Minimum sample size for statistical significance")
    expected_effect: Optional[float] = Field(None, description="Expected effect size for power calculations")
    event_value_path: Optional[str] = Field(None, description="JSON path to extract value from event payload")
    lower_is_better: bool = Field(False, description="Whether lower values are better")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Purchase Conversion",
                "event_name": "purchase_completed",
                "metric_type": "conversion",
                "is_primary": True,
                ...
            }
        }
    )
```

Read the signals: `...` marks required; `Optional[str] = Field(None, ...)` marks nullable
and optional; `metric_type: MetricType` means the field accepts only the enum's values
(`"conversion"`, `"revenue"`, …) and a typo is a 422 listing the allowed values;
`ge=10` refuses a sample size that would make the statistics meaningless. The
`json_schema_extra` example is what the docs page shows in the request body box.

The `Base → Create → Update` layering, for flags:

```python
# 📁 backend/app/schemas/feature_flag.py
class FeatureFlagBase(BaseModel):
    """Base model for feature flag data."""
    key: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)
    is_active: bool = True
    rollout_percentage: Optional[int] = Field(None, ge=0, le=100)
    targeting_rules: Optional[Any] = None
    default_value: Any = False
    tags: Optional[List[str]] = None
    model_config = ConfigDict(from_attributes=True)
    ...


class FeatureFlagCreate(FeatureFlagBase):
    """Model for creating a new feature flag."""
    pass


class FeatureFlagUpdate(FeatureFlagBase):
    """Model for updating a feature flag."""
    key: Optional[str] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None
    default_value: Optional[Any] = None


class FeatureFlagInDBBase(FeatureFlagBase):
    """Base model for feature flags in DB."""
    id: UUID
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
```

`FeatureFlagUpdate` overrides the required fields as `Optional[...] = None` so a `PUT` may
send only what changed. The endpoint then uses `model_dump(exclude_unset=True)` (Hour 7) to
tell "not sent" from "sent as null".

A pattern constraint that becomes a documented regex and a friendlier message than the raw
`pattern` error:

```python
# 📁 backend/app/schemas/experiment.py
class ExperimentBase(BaseModel):
    """Base model for experiment data."""

    name: str = Field(..., min_length=1, max_length=100, description="Experiment name")
    key: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        description="Stable identifier used by SDKs (generated from the name when omitted)",
    )
```

> **Did You Know?** Enums are the most common source of "it worked in the shell and 422'd
> from the dashboard." `MetricType` in the schema is `str, Enum` with lower-case values
> (`"conversion"`); the dashboard sends the value, the docs show the value, and a client that
> sends the _name_ (`"CONVERSION"`) gets a 422 listing `'conversion', 'revenue', ...`. The ORM
> enums (`models/experiment.py`) are separate classes with the same values, converted in the
> service.

### Try It Yourself

```python
# python — with the venv active
from backend.app.schemas.experiment import MetricBase, ExperimentBase
MetricBase(name="Purchase", event_name="purchase")                        # defaults fill in
MetricBase(name="Purchase", event_name="purchase", minimum_sample_size=5)   # ValidationError: ge=10
MetricBase(name="Purchase", event_name="purchase", metric_type="CONVERSION")  # ValidationError: enum
ExperimentBase(name="x", key="has space")                                  # ValidationError: pattern
ExperimentBase(name="x").key                                              # None — generated later by the service
MetricBase.model_json_schema()["properties"]["minimum_sample_size"]         # {'minimum': 10, 'default': 100, ...}
```

Then find a field in `backend/app/schemas/rollout_schedule.py` with a `ge`/`le` pair and
check that `POST /rollout-schedules` documents the same bounds.

### Common mistakes

- **`Optional[str]` without a default.** In v2 that's still _required_ (it may be null but
  must be present). Write `Optional[str] = None` (or `Field(None, ...)`) for optional.
- **Constraints in the handler instead of `Field`.** A bound on the field is enforced _and_
  documented; a check in the handler is neither, and it's a different error shape.
- **`Any` where a model would do.** `targeting_rules: Optional[Any]` is a deliberate
  exception (three stored shapes, normalised by the adapter); most JSON columns deserve a
  model.
- **Mutable defaults** (`tags: List[str] = []`). Pydantic copies defaults per instance so it's
  safe here, unlike plain dataclasses — but `None` + a validator is clearer about intent.

### Key takeaways

- A model validates on construction: types coerced, required enforced, unknowns ignored.
- `Field(default | ..., ge/le/min_length/max_length/pattern, description)` is enforced and
  published.
- Layer `Base → Create → Update (all optional) → Response/InDB (server fields,
  `from_attributes`)`.
- Enum fields accept values, not names; the schema enums mirror the ORM enums by value.

---

## Hour 6 — `field_validator` and `model_validator`: the two validation layers

### What & why

Constraints on a field cover "is this value acceptable on its own." Two decorators cover the
rest:

- **`@field_validator("name")`** — a classmethod that receives one field's value (after
  type coercion by default; `mode="before"` to see the raw input) and returns the value to
  use or raises `ValueError`. Use it for normalisation (lower-case a key, parse a JSON
  string) and single-field rules the built-in constraints can't express.
- **`@model_validator(mode="after")`** — an instance method that runs after all fields are
  set and can look at several at once. Use it for cross-field rules: allocations sum to 100,
  end after start, "either `experiment_id` or `feature_flag_id`".

Both raise `ValueError` (FastAPI turns it into a 422 with the message in `msg`), and both run
for _every_ construction of the model — the dashboard's request, a script, a test — which is
the point.

#### Why the schema layer and the database layer both validate

The `Variant` table has `CheckConstraint("traffic_allocation >= 0 AND traffic_allocation <=
100")`; the schema has `Field(50, ge=0, le=100)` and the sum-to-100 rule. Two layers on
purpose. The schema gives a **user** a 422 with a sentence; the constraint gives the
**system** a guarantee that holds for every writer including seeds and migrations. Remove
the schema rule and users get a 500 from an `IntegrityError`; remove the constraint and one
service path that forgets the schema corrupts the table.

### In the wild

A field validator that **normalises** (accept a JSON string where an object is expected):

```python
# 📁 backend/app/schemas/tracking.py
class EventBase(BaseModel):
    ...
    properties: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("properties", "metadata", mode="before")
    @classmethod
    def parse_json_string(cls, v: Any) -> Any:
        """Accept a JSON-encoded string for the free-form attribute fields."""
        if isinstance(v, str):
            import json

            try:
                return json.loads(v) if v.strip() else None
            except json.JSONDecodeError as exc:
                raise ValueError(f"must be a JSON object: {exc}") from exc
        return v

    @model_validator(mode="after")
    def validate_experiment_or_feature_flag(self) -> "EventBase":
        """Validate that either experiment_id or feature_flag_id is provided."""
        if not self.experiment_id and not self.feature_flag_id:
            raise ValueError("Either experiment_id or feature_flag_id must be provided")
        return self
```

Three details to copy: one validator can guard **several fields** (`"properties",
"metadata"`); `mode="before"` runs on the raw input so a string can be turned into a dict
_before_ the `Dict[str, Any]` type check; and `raise ... from exc` keeps the JSON error as the
cause. The model validator returns `self` — mandatory in `mode="after"`.

A field validator that **enforces a format** with a better message than `pattern` gives:

```python
# 📁 backend/app/schemas/feature_flag.py
    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        """Validate feature flag key format.
        Keys must be lowercase alphanumeric with hyphens or underscores,
        matching the pattern ^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$.
        """
        import re
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", v):
            raise ValueError(
                "Key must be lowercase alphanumeric characters, hyphens, or underscores only"
            )
        return v
```

A model validator with **three cross-field rules** and clear messages for each:

```python
# 📁 backend/app/schemas/rollout_schedule.py
    @model_validator(mode="after")
    def validate_stages(self) -> "RolloutScheduleCreate":
        """Validate stages in the schedule."""
        if not self.stages:
            raise ValueError("At least one stage is required")

        # Check that stages have increasing percentages
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

        return self
```

And a validator that reaches for the **clock** — with a tolerance, because clients' clocks
drift:

```python
# 📁 backend/app/schemas/experiment.py
class ScheduleConfig(BaseModel):
    ...
    @field_validator("start_date")
    @classmethod
    def validate_start_date(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Validate that start_date is in the future if provided."""
        if v and v < datetime.now(timezone.utc) - timedelta(minutes=10):  # Allow small buffer
            raise ValueError("Start date must be in the future")
        return v

    @model_validator(mode="after")
    def validate_dates(self) -> "ScheduleConfig":
        """Validate date relationships."""
        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")

        # Minimum duration check
        if self.start_date and self.end_date:
            min_duration = timedelta(hours=1)
            if self.end_date - self.start_date < min_duration:
                raise ValueError(f"Experiment must run for at least {min_duration}")

        return self
```

(`v < datetime.now(timezone.utc)` only works because the field is parsed as an aware
datetime from an ISO string with an offset. A naive input would raise `TypeError` inside the
validator — which Pydantic reports as a 500-ish `ValidationError` of a different kind. The
Python book's datetime hour has the full story.)

> **Watch Out!** Validators run in **declaration order**, and a `field_validator` sees
> `info.data` containing only the fields declared _above_ it. `Settings.validate_secret_key`
> reads `ENVIRONMENT` from `info.data` and works only because `ENVIRONMENT` is declared first.
> Reorder the class and the guard silently stops guarding. Cross-field logic is safer in a
> `model_validator(mode="after")`, which sees everything.

> **By the Way.** The old `@validator("end_date")` with a `values` dict — you'll still find
> one in `schemas/tracking.py::ExperimentMetrics` — is v1 style. It works through the shim but
> `values` is a plain dict of previously validated fields and the decorator is deprecated. New
> code uses `field_validator` + `ValidationInfo` or a `model_validator`.

### Try It Yourself

```python
from backend.app.schemas.tracking import EventRequest
EventRequest(event_type="purchase", user_id="u", experiment_key="k", metadata='{"sku": "a"}').metadata   # parsed
EventRequest(event_type="purchase", user_id="u", experiment_key="k", metadata='{not json')             # ValidationError, your message
EventRequest(event_type="purchase", user_id="u")                                                      # ValidationError: either/or

from backend.app.schemas.rollout_schedule import RolloutScheduleCreate
# build a schedule with stages 10 → 50 → 25 and read the message; then fix the order and gap the stage_order
```

Then write a validator: add (in a scratch subclass, not the repo) a `model_validator` to
`ExperimentCreate` that requires exactly one `is_primary` metric, and confirm the API's
`validateForm` on the dashboard already mirrors that rule client-side.

### Common mistakes

- **Forgetting `@classmethod` under `@field_validator`** (or the reverse order of the two
  decorators). Pydantic raises at class-definition time.
- **Not returning `self` from an `after` model validator** — the model becomes `None`.
- **Raising `HTTPException` in a validator.** Raise `ValueError`; FastAPI maps it to 422.
  `HTTPException` inside a validator is swallowed into a `ValidationError` with a confusing
  message.
- **Reading `info.data` for a field declared later.** It isn't there yet; use a model
  validator.
- **Validating what a DB constraint should guarantee, and only there.** Do both.

### Key takeaways

- `field_validator`: one field, normalise or refuse; `mode="before"` for raw input; can cover
  several fields.
- `model_validator(mode="after")`: cross-field rules; return `self`.
- Raise `ValueError` with the sentence you want the user to read.
- Declaration order matters for `info.data`; validators run for every construction.
- Schema validation for messages, database constraints for guarantees — both.

---

## Hour 7 — `ConfigDict`, `from_attributes`, and serialisation

### What & why

A model's behaviour is tuned by `model_config = ConfigDict(...)`. The settings you'll meet
here:

| Setting                  | Effect                                                                    | Used for                          |
| ------------------------ | ------------------------------------------------------------------------- | --------------------------------- |
| `from_attributes=True`   | `Model.model_validate(obj)` reads attributes, not just dict keys          | ORM object → response schema      |
| `extra="ignore" / "forbid"` | drop / reject unknown fields                                           | `Settings` ignores; strict bodies could forbid |
| `json_schema_extra`      | inject examples into the OpenAPI schema                                    | docs request bodies               |
| `populate_by_name`       | accept both alias and field name                                           | camelCase clients                 |
| `use_enum_values`        | store enum values instead of members                                       | when a plain string is wanted     |

And the round trip:

- **In:** JSON → `Model(**data)` / `Model.model_validate(data)`; an ORM object →
  `Model.model_validate(orm_obj)` (needs `from_attributes`).
- **Out:** `model.model_dump()` → dict (with `exclude_unset=True`, `exclude_none=True`,
  `mode="json"` to get JSON-safe types); `model.model_dump_json()` → string;
  `Model.model_validate_json(text)` → model. FastAPI's `jsonable_encoder` turns a model or
  anything nested (UUIDs, datetimes, enums) into JSON-safe Python.

#### Why `from_attributes` is the seam between ORM and API

The endpoint's last line is often `ExperimentResponse.model_validate(started_experiment)`.
That works because `ExperimentResponse` has `from_attributes=True`: Pydantic reads
`experiment.name`, `experiment.variants` (a list of ORM objects, validated into
`VariantResponse`s), `experiment.status` (an enum, coerced to its value because the field is
`str`) — and _drops_ `experiment.assignments`, `experiment.events` and every other
relationship the schema doesn't name. The response schema is therefore also the **allow-list**
of what leaves the server.

### In the wild

The response model — note the `str` typing of enum columns and the nested models:

```python
# 📁 backend/app/schemas/experiment.py
class ExperimentResponse(BaseModel):
    """Model for experiment response data."""

    id: UUID4
    name: str
    key: Optional[str] = None
    description: Optional[str] = None
    hypothesis: Optional[str] = None
    experiment_type: str
    status: str
    targeting_rules: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    owner_id: UUID4
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    variants: List[VariantResponse]
    metrics: List[MetricResponse]
    ...
    model_config = ConfigDict(from_attributes=True)
```

The service uses `jsonable_encoder` with `exclude_unset=True` to turn a `Create` model into
column-ready data — so an omitted optional field doesn't overwrite a database default with
`None`:

```python
# 📁 backend/app/services/experiment_service.py
    def create_experiment(self, obj_in: Union[ExperimentCreate, Dict[str, Any]], user_id: UUID) -> Dict[str, Any]:
        # Extract data from the schema, excluding unset values
        obj_data = jsonable_encoder(obj_in, exclude_unset=True)

        # Extract nested objects
        variants_data = obj_data.pop("variants", [])
        metrics_data = obj_data.pop("metrics", [])
        ...
        experiment = Experiment(**obj_data)
```

`EventService` accepts _either_ a model or a dict, which is the flexible-input idiom for
services called from endpoints (models) and scripts (dicts):

```python
# 📁 backend/app/services/event_service.py
    @staticmethod
    def _as_dict(event_data: Union[EventCreate, Mapping[str, Any]]) -> Dict[str, Any]:
        """Accept either a Pydantic model or a plain mapping."""
        if hasattr(event_data, "model_dump"):
            return event_data.model_dump()
        return dict(event_data)
```

Caching a response is a JSON round trip through the model, which is what keeps the cache
honest — a cached blob that no longer matches the schema fails validation rather than
returning stale shapes:

```python
# 📁 backend/app/api/v1/endpoints/results.py
    if use_cache:
        try:
            cache = _get_cache_service()
            cached = cache.get(cache_key)
            if cached:
                return ExperimentResultsResponse.model_validate_json(cached)
        except Exception:
            pass  # Cache miss is acceptable
    ...
        cache.set(cache_key, response.model_dump_json(), expire=ttl)
```

`json_schema_extra` for a documented example on a request model:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "device-42",
                "context": {"os": "iOS", "os_version": "17.4.0", "region": "US", "tier": "premium"},
            }
        }
    )
```

> **Watch Out!** `model_dump()` keeps Python types (`UUID`, `datetime`, `Enum`); `model_dump(mode="json")`
> and `jsonable_encoder` convert them. Putting a `model_dump()` result straight into
> `json.dumps` raises `TypeError: Object of type UUID is not JSON serializable`. The Python
> SDK's `_json_default` exists for exactly the datetime case.

> **Did You Know?** `to_dict()` on the ORM mixin (`models/base.py`) is the pre-Pydantic way
> of serialising — it walks `__table__.columns` and stringifies UUIDs and datetimes. Services
> that predate `from_attributes` (like `_experiment_to_dict`) build dicts by hand; the
> endpoint then validates the dict through the response model. Both paths end at the schema,
> which is what matters.

### Try It Yourself

```python
from backend.app.schemas.experiment import ExperimentResponse, ExperimentUpdate
from backend.app.db.session import SessionLocal
from backend.app.models.experiment import Experiment
db = SessionLocal(); exp = db.query(Experiment).first()
resp = ExperimentResponse.model_validate(exp)             # from_attributes
sorted(resp.model_dump().keys())                           # the allow-list: no 'assignments', no 'events'
type(resp.model_dump()["id"]), type(resp.model_dump(mode="json")["id"])   # UUID vs str
ExperimentUpdate(name="x").model_dump(exclude_unset=True)   # {'name': 'x'} — omitted fields absent
ExperimentUpdate(name="x", description=None).model_dump(exclude_unset=True)  # description present as None
ExperimentResponse.model_validate_json(resp.model_dump_json()) == resp        # round trip
```

### Common mistakes

- **`model_validate(orm_obj)` without `from_attributes=True`** → "Input should be a valid
  dictionary". Set it on the response model.
- **Using `model_dump()` instead of `exclude_unset=True` for PATCH/PUT** — every omitted field
  overwrites the row with `None`.
- **Returning the ORM object with a response model that names a relationship not loaded** —
  a lazy load per row (Hour 15), or a `DetachedInstanceError` after the session closed.
- **`json.dumps(model.model_dump())`** — use `model_dump_json()` or `mode="json"`.

### Key takeaways

- `ConfigDict(from_attributes=True)` lets a response schema read an ORM object; the schema is
  the allow-list of what leaves.
- `model_dump(exclude_unset=True)` distinguishes "not sent" from "sent null" — essential for
  updates.
- `model_dump_json` / `model_validate_json` make caches self-validating.
- `jsonable_encoder` (or `mode="json"`) for JSON-safe types.

---

## Hour 8 — `pydantic-settings`: the `Settings` object that fails closed

### What & why

`pydantic-settings` applies the same machinery to configuration: a `BaseSettings` subclass
whose fields are populated from environment variables (and an optional `.env` file), typed,
defaulted and — crucially — **validated**. The result is one object, `settings`, imported
everywhere, that answers "which database, which auth provider, which environment" and
refuses to exist when the answers are dangerous.

Three ideas in Experimently's `core/config.py` that go beyond "read an env var":

1. **A canonical environment name** with legacy aliases accepted and warned about.
2. **Subclasses per environment** (`DevSettings`, `TestSettings`, `ProdSettings`) differing
   only in defaults, selected at import from the canonical name.
3. **Validators that fail closed** — weak secrets and the dev bypass are refused in
   production at construction time, so a misconfigured deployment never serves a request.

### In the wild

The base class and the two settings that trip newcomers — `NoDecode` and `extra="ignore"`:

```python
# 📁 backend/app/core/config.py
class Settings(BaseSettings):
    """Base settings class."""

    PROJECT_NAME: str = "Experimentation Platform"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: EnvironmentName = "development"
    DEBUG: bool = False
    SECRET_KEY: str = "default-secret-key-for-testing"  # Default for testing only
    ...
    AUTH_PROVIDER: Literal["local", "cognito"] = "local"
    DEV_AUTH_BYPASS: bool = False
    ...
    BACKEND_CORS_ORIGINS: List[AnyHttpUrl] = []
    # CORS_ORIGINS is a plain-string list version of BACKEND_CORS_ORIGINS that
    # can also be set via env var as a comma-separated string. NoDecode stops
    # pydantic-settings from JSON-decoding the raw value so the "before"
    # validator below receives it as-is.
    CORS_ORIGINS: Annotated[List[str], NoDecode] = []
    ...
    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore"  # Ignore unknown fields to catch typos
    )
```

`NoDecode` is the fix for a real bug: pydantic-settings JSON-decodes the raw environment
value of any `List` field _before_ validators run, so `CORS_ORIGINS=http://a,http://b` failed
to decode and the setting was silently empty. With `NoDecode`, the `mode="before"` validator
receives the string and splits it:

```python
# 📁 backend/app/core/config.py
    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins_plain(cls, v: Union[str, List[str]]) -> List[str]:
        """Parse plain CORS origins list from comma-separated string or list."""
        if isinstance(v, str) and v.strip().startswith("["):
            import json

            v = json.loads(v)
        if isinstance(v, str) and v:
            return [i.strip() for i in v.split(",") if i.strip()]
        if isinstance(v, list):
            return v
        return []
```

The fail-closed guard, in both flavours:

```python
# 📁 backend/app/core/config.py
    @model_validator(mode="after")
    def forbid_dev_auth_bypass_outside_dev(self) -> "Settings":
        """
        Refuse to start with DEV_AUTH_BYPASS enabled in staging/production.
        ...
        This check is deliberately NOT relaxed by ``TESTING``.
        """
        if self.DEV_AUTH_BYPASS and self.ENVIRONMENT not in BYPASS_ALLOWED_ENVIRONMENTS:
            raise ValueError(
                f"DEV_AUTH_BYPASS=true is not permitted when ENVIRONMENT={self.ENVIRONMENT!r}. "
                "The dev-admin bypass may only be enabled in development or test."
            )
        return self

    @field_validator("AUDIT_HMAC_KEY")
    @classmethod
    def validate_audit_hmac_key(cls, v: str, info: ValidationInfo) -> str:
        environment = (info.data or {}).get("ENVIRONMENT", "development")
        is_testing = os.getenv("TESTING", "").lower() in ("1", "true", "yes")
        if not is_testing and environment == "production":
            weak_defaults = {"dev-audit-key-change-in-production", "", "changeme"}
            if v in weak_defaults or len(v) < _MIN_SECRET_KEY_LENGTH:
                raise ValueError(
                    f"AUDIT_HMAC_KEY must be at least {_MIN_SECRET_KEY_LENGTH} characters "
                    "and must not be the dev default in production. "
                    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
        return v
```

The assembly validators (`mode="before"`) that let operators set either a whole DSN or its
parts, and the per-environment subclasses:

```python
# 📁 backend/app/core/config.py
    @field_validator("REDIS_URI", mode="before")
    @classmethod
    def assemble_redis_connection(cls, v: Optional[str], info: ValidationInfo) -> Any:
        """Assemble Redis connection string if not provided directly."""
        if isinstance(v, str):
            return v

        host = info.data.get("REDIS_HOST", "localhost")
        port = int(info.data.get("REDIS_PORT", 6379))
        password = info.data.get("REDIS_PASSWORD")

        if password:
            return f"redis://:{password}@{host}:{port}"
        return f"redis://{host}:{port}"


class TestSettings(Settings):
    """Test environment settings."""

    ENVIRONMENT: EnvironmentName = "test"
    ENV: str = "test"
    TESTING: bool = True
    LOG_LEVEL: str = "DEBUG"
    ...
    POSTGRES_DB: str = "experimentation_test"
    POSTGRES_SCHEMA: str = "test_experimentation"
    CACHE_ENABLED: bool = False

    model_config = SettingsConfigDict(env_file=".env.test", case_sensitive=True, extra="ignore")
```

And the module-level selection that makes `from backend.app.core.config import settings` the
same object everywhere:

```python
# 📁 backend/app/core/config.py
_resolved_environment = resolve_environment_from_process_env()

if _resolved_environment in ("production", "staging"):
    settings = ProdSettings(ENVIRONMENT=_resolved_environment)
elif _resolved_environment == "test":
    settings = TestSettings(ENVIRONMENT=_resolved_environment)
else:
    settings = DevSettings(ENVIRONMENT=_resolved_environment)
```

Because it's a module singleton created at import, tests that need a different provider set
attributes on it (`settings.AUTH_PROVIDER = "local"` in the root conftest) rather than
re-importing; and code that reads `settings.X` at _call_ time (like `dev_auth_bypass_active`)
sees the change, while code that captured the value at import doesn't.

> **Watch Out!** `TESTING=true` relaxes the secret validators so the suite can construct
> `ProdSettings()`. When you verify a guard by hand, clear it: `env -u TESTING -u APP_ENV
> ENVIRONMENT=production SECRET_KEY=changeme python -c "import backend.app.core.config"`.
> The dev-bypass guard is the exception — it ignores `TESTING` on purpose.

> **By the Way.** `extra="ignore"` is why a misspelled variable does nothing and says
> nothing. The comment claims it "catches typos"; it's the opposite. The trade is accepted
> because container environments are full of unrelated variables that `extra="forbid"` would
> reject. Spell settings carefully; check `settings.<NAME>` when a value seems ignored.

### Try It Yourself

```bash
# the settings object, and which subclass you got
python -c "from backend.app.core.config import settings; print(type(settings).__name__, settings.ENVIRONMENT, settings.AUTH_PROVIDER)"
APP_ENV=test python -c "from backend.app.core.config import settings; print(type(settings).__name__, settings.POSTGRES_SCHEMA)"
# the comma-separated CORS list survives NoDecode
CORS_ORIGINS="http://a:1,http://b:2" python -c "from backend.app.core.config import settings; print(settings.CORS_ORIGINS)"
# the guards, with TESTING cleared
env -u TESTING -u APP_ENV ENVIRONMENT=production SECRET_KEY=changeme python -c "import backend.app.core.config" 2>&1 | tail -2
```

Then add a hypothetical `WEBHOOK_SIGNING_SECRET` with a fail-closed validator in a scratch
subclass and prove it refuses in production and passes in development.

### Common mistakes

- **A `List` setting from a comma-separated env var without `NoDecode`** — silently empty.
- **Reading `info.data["X"]` for a field declared below** — not there; declare guards after
  the fields they depend on, or use a model validator.
- **Re-importing `config` in a test to "reset" settings** — it's a singleton; set attributes
  or construct a subclass explicitly.
- **Relying on `TESTING` to verify a production guard** — it relaxes most of them.

### Key takeaways

- `BaseSettings` reads typed, validated config from the environment; one singleton
  `settings` is selected per canonical environment at import.
- `NoDecode` + a `before` validator for comma-separated lists; `before` validators assemble
  DSNs from parts.
- Validators fail closed in production; the bypass guard ignores `TESTING`, the secret guards
  don't.
- `extra="ignore"` drops typos silently — verify values in a shell.

---

## Part II Workshop

**Q1. Why is `Optional[int] = Field(None, ge=0, le=100)` different from `int = Field(0, ge=0,
le=100)` on an `Update` model?**
The first is "may be omitted, and may be null" — the update endpoint can tell "not sent"
from "sent" with `exclude_unset`. The second always has a value, so an omitted field would
overwrite the row with 0.

**Q2. A `field_validator` on `end_date` wants to compare with `start_date`. What must be
true, and what's the safer choice?**
`start_date` must be declared above `end_date` so it's in `info.data`. A
`model_validator(mode="after")` sees both regardless of order — safer.

**Q3. `ExperimentResponse.model_validate(experiment)` returns a model without
`experiment.assignments`. Why, and why is that good?**
The schema doesn't declare `assignments`; `from_attributes` reads only declared fields. The
response schema is the allow-list of what leaves the server.

**Q4. `json.dumps(resp.model_dump())` raises. Fix?**
`resp.model_dump_json()` or `resp.model_dump(mode="json")` (or `jsonable_encoder(resp)`)
— `model_dump()` keeps `UUID`/`datetime` objects.

**Q5. Why does `CORS_ORIGINS` need `NoDecode` but `HIPAA_ALLOWED_REGIONS: List[str]`
doesn't?**
Both are lists; the difference is how operators set them. `HIPAA_ALLOWED_REGIONS` is expected
as JSON (`["us-east-1"]`), which pydantic-settings decodes fine. `CORS_ORIGINS` is documented
as comma-separated, which JSON decoding rejects — `NoDecode` hands the raw string to the
validator.

**Q6. Which guard in `Settings` ignores `TESTING`, and why?**
`forbid_dev_auth_bypass_outside_dev`. There's no production behaviour that depends on the
bypass being on, so there's nothing to test by allowing it — and allowing it is one variable
away from serving every request as admin.

**Quiz.** A client sends `{"metric_type": "CONVERSION"}`. What happens?
→ 422: `MetricType` accepts values (`"conversion"`), not names; the error lists the allowed
values.

---

## Exercises

**E2.1 — Layer a resource.** Write `DecisionLogBase`, `DecisionLogCreate`, `DecisionLogUpdate`
and `DecisionLogResponse` for a decision log (`experiment_id: UUID`, `reason: str` 1–4000
chars, `decided_at: Optional[datetime]`), following the house layering and config.

**E2.2 — Normalise then refuse.** Add a `field_validator` to `DecisionLogBase.reason` that
strips whitespace and refuses a reason shorter than 10 characters _after_ stripping, with a
message a user would understand.

**E2.3 — Cross-field.** Add a `model_validator` to a `RolloutStageCreate`-like model that
requires `start_date` when `trigger_type == "time_based"` and forbids it for `"manual"`.

**E2.4 — Update semantics.** Given `DecisionLogUpdate(reason="ok")` and
`DecisionLogUpdate(reason="ok", decided_at=None)`, show what `model_dump(exclude_unset=True)`
returns for each and explain what a `PUT` handler should do differently with them.

**E2.5 — A setting with a guard.** Add `RESULTS_CACHE_TTL_SECONDS: int = 300` with a validator
that refuses values under 30 in production (relaxed by `TESTING`), and explain why a
`field_validator` suffices here where the bypass rule needed a `model_validator`.

---

## Solutions

**S2.1**

```python
class DecisionLogBase(BaseModel):
    experiment_id: UUID
    reason: str = Field(..., min_length=1, max_length=4000, description="Why the decision was made")
    decided_at: Optional[datetime] = Field(None, description="When the decision was made (UTC)")

    model_config = ConfigDict(from_attributes=True)


class DecisionLogCreate(DecisionLogBase):
    pass


class DecisionLogUpdate(BaseModel):
    reason: Optional[str] = Field(None, min_length=1, max_length=4000)
    decided_at: Optional[datetime] = None


class DecisionLogResponse(DecisionLogBase):
    id: UUID
    decided_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
```

(`Update` inherits from `BaseModel`, not `Base`, so `experiment_id` isn't updatable and every
field is optional.)

**S2.2**

```python
    @field_validator("reason")
    @classmethod
    def normalise_reason(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 10:
            raise ValueError("Please give a reason of at least 10 characters so the decision is understandable later")
        return cleaned
```

Note the order: the `min_length=1` on `Field` runs first (on the raw value), then the
validator strips and applies the stricter rule.

**S2.3**

```python
    @model_validator(mode="after")
    def validate_trigger_dates(self) -> "RolloutStageCreate":
        if self.trigger_type == "time_based" and self.start_date is None:
            raise ValueError("time_based stages require a start_date")
        if self.trigger_type == "manual" and self.start_date is not None:
            raise ValueError("manual stages are advanced by hand and must not set a start_date")
        return self
```

**S2.4** — First: `{"reason": "ok"}`. Second: `{"reason": "ok", "decided_at": None}`. The
handler should apply exactly the keys present: for the first, change only `reason`; for the
second, change `reason` _and_ clear `decided_at` — the client explicitly sent null. Using
`model_dump()` without `exclude_unset` would clear `decided_at` in both cases, silently
destroying data on every partial update.

**S2.5**

```python
    RESULTS_CACHE_TTL_SECONDS: int = 300

    @field_validator("RESULTS_CACHE_TTL_SECONDS")
    @classmethod
    def validate_results_ttl(cls, v: int, info: ValidationInfo) -> int:
        environment = (info.data or {}).get("ENVIRONMENT", "development")
        is_testing = os.getenv("TESTING", "").lower() in ("1", "true", "yes")
        if not is_testing and environment == "production" and v < 30:
            raise ValueError("RESULTS_CACHE_TTL_SECONDS must be at least 30 in production")
        return v
```

A `field_validator` suffices because the rule concerns one field plus `ENVIRONMENT`, which is
declared above it and therefore available in `info.data`. The bypass rule needed a
`model_validator` for clarity and because it must run regardless of `TESTING` and read two
fields as a unit — and, being `mode="after"`, it can't be defeated by declaration order.

---

**Next:** [Part III — Dependency Injection & Auth →](./part-3-dependencies-and-auth.md)

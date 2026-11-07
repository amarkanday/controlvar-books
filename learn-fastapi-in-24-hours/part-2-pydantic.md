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
unknown fields are ignored by default (`extra="ignore"`; a model can choose `"forbid"`
instead, and this codebase's flag requests do — see below), and the result is a plain object
with typed attributes.

`Field(...)` attaches metadata to a field: a default (or `...` for required), numeric bounds
(`ge`, `gt`, `le`, `lt`), string bounds (`min_length`, `max_length`, `pattern`), a
`description` for the docs, and `examples`. Two things make `Field` more than decoration in
a FastAPI app: the constraints are **enforced** (a 422), and they are **published** (the
OpenAPI schema shows `minimum: 0, maximum: 100`).

#### Why most of the codebase's schemas are layered `Base → Create → Update → Response`

Most resources have four models: a `Base` with the fields a client may set, a `Create` that
adds what's required at creation, an `Update` where everything is optional, and a `Response`
that adds server-owned fields (`id`, `created_at`) and reads from ORM objects. It's
inheritance used for _shape reuse_, and the layering answers three questions at once: what may
I send? what must I send? what will I get back? The experiment schemas follow it
(`ExperimentBase → ExperimentCreate`, a separate `ExperimentUpdate`, `ExperimentResponse`).

Feature flags show where the layering stops being enough. Their request contract is strict —
an unknown field is refused, an explicit `null` for a required column is refused — and a
response model that inherited those rules would refuse a stored row and turn a read into a
500. So the flag schemas split **requests** from the **response** completely, and that file is
the better lesson in Pydantic.

### In the wild

The metric model shows constraints, defaults and documentation on one class:

```python
# 📁 backend/app/schemas/experiment.py
class MetricBase(BaseModel):
    """Base model for metric data."""

    name: str = Field(..., min_length=1, max_length=100, description="Metric name")
    description: Optional[str] = Field(None, description="Metric description")
    event_name: str = Field(
        ..., min_length=1, max_length=100, description="Event name to track"
    )
    metric_type: MetricType = Field(
        MetricType.CONVERSION, description=METRIC_TYPE_DESCRIPTION
    )
    is_primary: bool = Field(False, description="Whether this is the primary metric")
    aggregation_method: str = Field(
        "average", max_length=50, description=AGGREGATION_METHOD_DESCRIPTION
    )
    minimum_sample_size: int = Field(
        100,
        ge=10,
        le=INT32_MAX,
        description="Minimum sample size for statistical significance",
    )
    expected_effect: Optional[float] = Field(
        None,
        description=(
            "The minimum detectable effect the results page plans the sample "
            "for, when this is the primary metric: a relative change as a "
            "fraction (0.10 means 5% -> 5.5%). Used only when above 0 and "
            "below 1; otherwise the plan uses 0.05."
        ),
    )
    event_value_path: Optional[str] = Field(
        None,
        max_length=100,
        description=EVENT_VALUE_PATH_DESCRIPTION,
    )
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
`ge=10` refuses a sample size that would make the statistics meaningless, and
`le=INT32_MAX` (2³¹ − 1, a module constant) refuses one the database column could not hold:
without an upper bound, a huge integer passes validation and fails at the `INSERT` as a 500
instead of a 422 naming the field. `max_length` on a string does the same job for a
`VARCHAR`. A schema's constraints should mirror the column's limits, not just the business
rules. The
`json_schema_extra` example is what the docs page shows in the request body box.

Three of the descriptions are not strings at all but names. They are module constants, written
once at the top of the file:

```python
# 📁 backend/app/schemas/experiment.py
#: What a metric's type means to the analysis (#439).  The same words on the
#: request (``MetricBase``) and the response (``MetricResponse``).
METRIC_TYPE_DESCRIPTION = (
    "What the metric measures.  conversion: the share of assigned users who "
    "sent event_name.  count: the number of event_name events per assigned "
    "user; their value is not read.  revenue, duration and custom: the total "
    "value of a user's event_name events, per assigned user.  In each mean a "
    "user with none counts as 0."
)

#: ``aggregation_method`` and ``event_value_path`` are accepted, stored and
#: returned, and no analysis reads them (#439).  Their descriptions say so.
AGGREGATION_METHOD_DESCRIPTION = (
    "Not read: the analysis does not use it, and how a metric is analysed "
    "follows from metric_type (see analysis in the results).  Stored and "
    "returned as sent."
)
```

Two lessons in one move. A description is part of the published contract — it is what an
integrator reads in `/api/v1/docs` and in the OpenAPI document — so the request model and the
response model (`MetricResponse` uses the same three constants) must say the same thing, and a
constant is how you make them unable to drift. And a description must be **true**:
`aggregation_method` used to read "How to aggregate the metric", which promised behaviour no
code had. When a field is accepted but ignored, say so in the field's own words; a reader who
trusts the docs should never be surprised by the code.

The flag request models share a private base, `_FeatureFlagRequest`, and nothing else:

```python
# 📁 backend/app/schemas/feature_flag.py
class _FeatureFlagRequest(BaseModel):
    """What a create and an update have in common.  Never a response base."""

    model_config = ConfigDict(extra="forbid")

    # Read-only: accepted so a GET body can be sent back, and ignored.
    id: Any = Field(
        None, description="Read-only; ignored.", json_schema_extra=_READ_ONLY
    )
    owner_id: Any = Field(
        None, description="Read-only; ignored.", json_schema_extra=_READ_ONLY
    )
    ...


class FeatureFlagCreate(_FeatureFlagRequest):
    """The body of a flag create. A field not listed here answers 422."""

    key: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=2000)
    is_active: bool = Field(
        False, description="Create the flag on. Left out, the flag starts off."
    )
    rollout_percentage: int = Field(0, ge=0, le=100)
    ...
    default_value: StrictBool = Field(
        False, description=DEFAULT_VALUE_REQUEST_DESCRIPTION
    )
    value_type: ValueType = Field(BOOLEAN, description=VALUE_TYPE_DESCRIPTION)
    on_value: Any = Field(
        None, description=ON_VALUE_DESCRIPTION + VALUE_LIMITS_DESCRIPTION
    )
    off_value: Any = Field(
        None, description=OFF_VALUE_DESCRIPTION + VALUE_LIMITS_DESCRIPTION
    )
    tags: Optional[List[str]] = None
    ...


class FeatureFlagUpdate(_FeatureFlagRequest):
    ...
    # The NOT_NULL_FIELDS default to None only to mean "not sent": pydantic
    # does not validate a default, while an explicit null reaches refuse_null.
    key: str = Field(None, min_length=1, max_length=100)
    name: str = Field(None, min_length=1, max_length=100)
    ...
```

Five decisions to read in it:

- **`extra="forbid"`.** A misspelt field (`"rollout_percent": 50`) is a 422, not a silently
  ignored key and a flag that did not change. On a public API that is the friendlier
  behaviour: the client learns at once.
- **Read-only fields are declared, not forbidden.** `id`, `owner_id`, `created_at`,
  `updated_at` and `status` appear on the request models (marked `readOnly` in the OpenAPI
  schema) so a client can `GET` a flag, change one field and `PUT` the whole body back. The
  service drops them; a `status` that differs from the stored one is refused, with a message
  pointing at `is_active`.
- **A `Literal` for a closed set.** `value_type: ValueType` is
  `Literal["boolean", "string", "number", "json"]`, so `"list"` is a 422 naming the four
  values. What the flag serves is `on_value` to the users it is on for and `off_value` to
  everyone else; both are typed `Any` because their JSON type depends on `value_type`, which
  is a rule across fields — a model validator's job (Hour 6).
- **`StrictBool` on a deprecated field.** `default_value` accepts only a real JSON boolean —
  `"false"` is a 422, where plain `bool` would coerce it — and a validator then accepts only
  `false` (a boolean flag whose off-value is `true` would keep serving `true` after being
  turned off). It is deprecated in favour of `off_value` rather than removed, and under
  `extra="forbid"` that difference matters: a removed field would turn every client that
  still sends `"default_value": false` into a 422.
- **`FeatureFlagUpdate` types `key: str = Field(None, ...)`, not `Optional[str]`.** Pydantic
  does not validate a default, so leaving `key` out still means "not sent"; but an explicit
  `"key": null` is validated, and a `before` validator refuses it (Hour 6). The column is
  `NOT NULL`, so `null` can never be a valid value.

The response is a separate model that **shares no base** with the requests:

```python
# 📁 backend/app/schemas/feature_flag.py
class FeatureFlagRead(BaseModel):
    """A feature flag, as every flag route returns it.
    ...
    """
    ...
    model_config = ConfigDict(
        from_attributes=True, json_schema_serialization_defaults_required=True
    )

    id: UUID
    key: str
    name: str
    description: Optional[str] = None
    status: Literal["active", "inactive", "archived"] = Field(
        ..., description="The flag's status, lower-cased."
    )
    is_active: bool = Field(..., description='True exactly when status is "active".')
    ...
    value_type: str = Field(..., description=VALUE_TYPE_DESCRIPTION)
    on_value: Any = Field(..., description=ON_VALUE_DESCRIPTION)
    off_value: Any = Field(..., description=OFF_VALUE_DESCRIPTION)
    ...

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        """Read a stored row (or a mapping), lower-case ``status``, derive
        ``is_active`` from it, and read a boolean flag's two values."""
        ...
```

It holds types only — no key pattern, no length limit — because a response model that refuses
a stored row turns a read into a 500. Compare `value_type` on the two sides: the request types
it `ValueType`, the four-value `Literal`, and the response types it plain `str`. The comment in
the class gives the reason: an older image must still answer a row of a type a later release
added, so a value this release has never heard of is read, not refused. Strict on the way in,
tolerant on the way out. Its one piece of logic is a `mode="before"` model validator that reads
the ORM row (or a dict), lower-cases `status` and derives `is_active` from it, so the two can
never disagree, and gives a boolean flag (whose two value columns are NULL) its `true` and
`false`. Every flag route answers with it through one function, `flag_to_read(flag)`.

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
MetricBase.model_json_schema()["properties"]["minimum_sample_size"]         # {'default': 100, 'maximum': 2147483647, 'minimum': 10, ...}

from backend.app.schemas.feature_flag import FeatureFlagCreate, FeatureFlagUpdate
FeatureFlagCreate(key="k", name="n", colour="red")             # ValidationError: extra_forbidden
FeatureFlagCreate(key="k", name="n", default_value="false")    # ValidationError: bool_type (StrictBool)
FeatureFlagUpdate(name=None)                                   # ValidationError: name cannot be null
FeatureFlagUpdate(description=None).model_dump(exclude_unset=True)   # {'description': None}: clears it
FeatureFlagCreate(key="k", name="n", value_type="list")        # ValidationError: literal_error, the four types listed
```

(The `maximum` is `INT32_MAX`: the column is a 32-bit integer, so a larger value is a 422
rather than a database error.)

Then find a field in `backend/app/schemas/rollout_schedule.py` with a `ge`/`le` pair and
check that `POST /rollout-schedules` documents the same bounds.

### Common mistakes

- **`Optional[str]` without a default.** In v2 that's still _required_ (it may be null but
  must be present). Write `Optional[str] = None` (or `Field(None, ...)`) for optional.
- **Constraints in the handler instead of `Field`.** A bound on the field is enforced _and_
  documented; a check in the handler is neither, and it's a different error shape.
- **`Any` where a model would do.** `targeting_rules: Optional[Any]` is a deliberate
  exception (the dashboard's rule-builder shape and the native rules shape, both normalised by
  the targeting adapter) — but `Any` does not mean unchecked: a field validator runs the rules through the
  same validation the evaluator uses, so rules it would not apply as written (an unknown key or
  operator, a list of rules) answer 422 naming the place, e.g.
  `groups[0].conditions[1].operator: unknown operator`. Most JSON columns deserve a model.
- **A response model with request rules.** Inherit `extra="forbid"`, a pattern or a
  `max_length` into a response and the first stored row that breaks it is a 500 on read.
  Keep response models to types.
- **Mutable defaults** (`tags: List[str] = []`). Pydantic copies defaults per instance so it's
  safe here, unlike plain dataclasses — but `None` + a validator is clearer about intent.

### Key takeaways

- A model validates on construction: types coerced, required enforced, unknowns ignored by
  default — or refused with `extra="forbid"`, as the flag requests do.
- `Field(default | ..., ge/le/min_length/max_length/pattern, description)` is enforced and
  published.
- Layer `Base → Create → Update (all optional) → Response (server fields,
  `from_attributes`)` — and when requests are strict, give the response no shared base at all
  (`FeatureFlagRead`).
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
class EventFields(BaseModel):
    ...
    properties: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @field_validator("properties", "metadata", mode="before")
    @classmethod
    def parse_json_string(cls, v: Any, info: ValidationInfo) -> Any:
        """Accept a JSON-encoded string for the free-form attribute fields.
        ...
        """
        if isinstance(v, str):
            import json

            try:
                decoded = json.loads(v) if v.strip() else None
            except json.JSONDecodeError as exc:
                raise ValueError(f"must be a JSON object: {exc}") from exc
            except RecursionError:
                # Nested past the parser's own limit: far past the depth
                # limit, so the same refusal as any other over-deep value.
                raise ValueError(
                    f"{info.field_name} must not be nested more than "
                    f"{EVENT_METADATA_MAX_DEPTH} levels deep"
                ) from None
            ...
            return decoded
        return v
...


class EventBase(EventFields):
    """Base model for event data."""

    @model_validator(mode="after")
    def validate_experiment_or_feature_flag(self) -> "EventBase":
        """Validate that either experiment_id or feature_flag_id is provided."""
        if not self.experiment_id and not self.feature_flag_id:
            raise ValueError("Either experiment_id or feature_flag_id must be provided")
        return self
```

Four details to copy: one validator can guard **several fields** (`"properties",
"metadata"`); `mode="before"` runs on the raw input so a string can be turned into a dict
_before_ the `Dict[str, Any]` type check; `raise ... from exc` keeps the JSON error as the
cause; and the optional second parameter, `info: ValidationInfo`, tells a validator shared by
several fields which one it is checking (`info.field_name`), so its message can name it. The
`RecursionError` branch is a reminder that `json.loads` has limits of its own: a string nested
deeply enough makes the parser itself give up, and that is turned into the same 422 as any
other value nested too deep. The model validator returns `self` — mandatory in `mode="after"`. Note the split: the
field-level parsing lives on `EventFields` and the "names an experiment or a flag" rule on
its subclass `EventBase`, because one caller (`UntaggedEventCreate`, an event that names
neither) needs the fields without the rule. Put a rule on the narrowest class that needs it.

A field validator that **enforces a format** with a better message than `pattern` gives, and
one that **refuses an explicit null** — both on the flag requests' shared base, which declares
neither field itself:

```python
# 📁 backend/app/schemas/feature_flag.py
    @field_validator(*NOT_NULL_FIELDS, mode="before", check_fields=False)
    @classmethod
    def refuse_null(cls, value: Any, info: ValidationInfo) -> Any:
        """Refuse an explicit null for a field stored in a NOT NULL column.

        A ``before`` validator does not run for a field left out: pydantic does
        not validate a default, so omission still means "not sent".
        """
        if value is None:
            raise PydanticCustomError(
                "null_not_allowed", f"{info.field_name} cannot be null"
            )
        return value

    @field_validator("key", check_fields=False)
    @classmethod
    def validate_key(cls, v: str) -> str:
        """Keys are lowercase letters, digits, hyphens and underscores, and
        start with a letter or a digit."""
        import re

        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", v):
            raise ValueError(
                "Key must be lowercase alphanumeric characters, hyphens, or underscores only"
            )
        return v
```

`check_fields=False` lets a validator on a base class name fields that only the subclasses
declare (without it Pydantic refuses, at class definition, a validator for a field it cannot
see). `refuse_null` is `mode="before"` so it sees the raw `None` before the `str` type check
would turn it into a generic "Input should be a valid string". And it raises
`PydanticCustomError("null_not_allowed", ...)` rather than `ValueError`: a custom error sets
the error's `type` (what a client can branch on) and its message exactly, without the
`"Value error, "` prefix a `ValueError` gets.

The flag's values take that idea all the way. Whether `on_value` and `off_value` are acceptable
depends on `value_type` — a string flag needs two strings, a boolean flag `true` and `false` —
so the rule is cross-field and belongs in a model validator. But a model validator's
`ValueError` is reported at the model (`loc: ["body"]`), and a client fixing a form wants to know
_which_ field. `FeatureFlagCreate` gets both:

```python
# 📁 backend/app/schemas/feature_flag.py
    @model_validator(mode="after")
    def values_fit_the_type(self) -> "FeatureFlagCreate":
        """The two values follow the value rules for ``value_type``.

        A string, number or json flag sends both, of its type and within the
        limits, and different; a boolean flag sends neither, or ``true`` and
        ``false``.  Each refusal is at its own field.
        """
        sent = {
            field: getattr(self, field)
            for field in VALUE_FIELDS
            if field in self.model_fields_set
        }
        errors = write_errors(self.value_type, sent, None)
        if errors:
            raise value_errors_to_raise(type(self).__name__, errors)
        return self
```

The rules themselves live in one plain module, `backend/app/core/flag_values.py`, with no
Pydantic model in it at all. Each rule returns a `PydanticCustomError` rather than raising:

```python
# 📁 backend/app/core/flag_values.py
    if value_type == BOOLEAN:
        if value is (field == "on_value"):
            return None
        return PydanticCustomError("boolean_values", BOOLEAN_VALUES)
    if not _of_type(value_type, value):
        return PydanticCustomError(
            "value_type_mismatch",
            f"{field} must be {_TYPE_WORDS.get(value_type, value_type)}, "
            f"because value_type is {value_type}",
        )
```

and the schema turns the `(field, error)` pairs into one `ValidationError` whose errors each sit
at their own place:

```python
# 📁 backend/app/schemas/feature_flag.py
def value_errors_to_raise(
    title: str, errors: List[Any], prefix: tuple = ()
) -> ValidationError:
    ...
    return ValidationError.from_exception_data(
        title,
        [
            InitErrorDetails(type=error, loc=(*prefix, field), input=None)
            for field, error in errors
        ],
    )
```

Three design decisions are worth stealing:

- **The rules are not in the model.** An update is checked against the _stored_ flag's type,
  which the request model cannot see, so `FeatureFlagService` calls the same `write_errors`
  with the stored values; the evaluate route reads a stored row through the same module. One
  module, three callers, no way for them to disagree.
- **Built errors, not raised ones.** `ValidationError.from_exception_data` with
  `InitErrorDetails` is how you raise several precise errors at once from one validator. Over
  HTTP a string flag sent with only `on_value` answers
  `{"type": "value_required", "loc": ["body", "off_value"], ...}`, and a number flag sent
  `"on_value": "1"` answers `value_type_mismatch` at `["body", "on_value"]`. `input=None` keeps
  the submitted value out of the error, as the 422 handler from Hour 1 does.
- **`model_fields_set`.** "Sent" means present in the request, not "not `None`". A field left
  out is not type-checked; on a create it is then reported as required (for a string, number or
  json flag), and on an update the stored value stands in for it.

The database holds the same rule as a `CHECK` constraint (Hour 14): the schema gives a client a
422 naming the field, and the constraint gives the system its guarantee.

A model validator with **three cross-field rules** and clear messages for each:

```python
# 📁 backend/app/schemas/rollout_schedule.py
    @model_validator(mode="after")
    def validate_stages(self) -> "RolloutScheduleCreate":
        """Validate stages in the schedule."""
        if not self.stages:
            raise ValueError("At least one stage is required")

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

        # Check that max stage percentage doesn't exceed the schedule max
        if max(stage_percentages) > self.max_percentage:
            raise ValueError(
                f"Stage percentages cannot exceed the maximum of {self.max_percentage}%"
            )

        # Check that stage orders are sequential without gaps: n distinct
        # orders whose highest is n - 1 above their lowest.
        stage_orders = sorted(stage.stage_order for stage in self.stages)
        if (
            len(set(stage_orders)) != len(stage_orders)
            or stage_orders[-1] - stage_orders[0] != len(stage_orders) - 1
        ):
            raise ValueError("Stage orders must be sequential without gaps")

        return self
```

The first rule carries the best bug in this Part. It used to read the targets **in list
order** — `[stage.target_percentage for stage in self.stages]` — while the scheduler starts
stages **by `stage_order`**. So a request listing `{stage_order: 2, target: 10}` before
`{stage_order: 1, target: 50}` passed (10 then 50, non-decreasing) and then ran 50% followed by
10%: exactly the decrease the rule exists to refuse (#1460). A validator has to judge the data
in the order its _consumer_ will use it, not in the order the request happened to be written.
Sorting by `stage_order` first is the fix here, and the message now says which order it means.
The same change made the service apply the rule to every later stage change too, because a
rule checked only on create is checked once.

The last rule counts rather than builds: `n` orders are consecutive exactly when they are all
different and the highest is `n - 1` above the lowest. (An earlier version compared
`sorted(orders)` with `list(range(lo, hi + 1))`, which allocates a list as long as the span
between the smallest and largest order. The replacement answers the same way and costs
nothing.) The field itself is capped too: `stage_order` is `le=MAX_STAGE_ORDER` (1000), so a
request carrying an order of a few billion is a 422 naming the field, not a 500 from the
database. Same lesson as `le=INT32_MAX` above: validate the shape of the column, not only the
business rule.

And a validator that reaches for the **clock** — with a tolerance, because clients' clocks
drift:

```python
# 📁 backend/app/schemas/experiment.py
class ScheduleConfig(BaseModel):
    ...
    time_zone: str = Field(
        "UTC",
        max_length=64,
        ...
    )
    ...
    @field_validator("start_date")
    @classmethod
    def validate_start_date(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Validate that start_date is in the future if provided.

        A date without an offset is checked in ``validate_dates``, once it has
        been read in ``time_zone``.
        """
        if (
            v
            and v.tzinfo is not None
            and v < datetime.now(timezone.utc) - timedelta(minutes=10)
        ):  # Allow small buffer
            raise ValueError("Start date must be in the future")
        return v
    ...
    @model_validator(mode="after")
    def validate_dates(self) -> "ScheduleConfig":
        """Read offset-less dates in ``time_zone``, then check them.
        ...
        """
        zone = _zone(self.time_zone)
        naive_start = self.start_date is not None and self.start_date.tzinfo is None
        for name in ("start_date", "end_date"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                # Only a field the request carried can be naive, so this
                # assignment does not mark an omitted field as set.
                setattr(self, name, value.replace(tzinfo=zone).astimezone(timezone.utc))

        if naive_start and self.start_date < datetime.now(timezone.utc) - timedelta(
            minutes=10
        ):  # Allow small buffer
            raise ValueError("Start date must be in the future")

        if self.start_date and self.end_date and self.end_date <= self.start_date:
            raise ValueError("End date must be after start date")
        ...
        return self
```

Comparing a naive `datetime` with an aware one raises `TypeError`, so the field validator
checks only dates that arrived with an offset (`Z`, `+02:00`). A date without one is wall-clock
time in `time_zone` (an IANA name such as `America/Los_Angeles`, default `UTC`): the model
validator — which sees `time_zone` regardless of declaration order — attaches the zone,
converts to UTC, and only then compares. `"2030-01-01T09:00:00"` with
`time_zone="America/Los_Angeles"` comes out as 17:00 UTC. The Python book's datetime hour has
the full story.

> **Watch Out!** Validators run in **declaration order**, and a `field_validator` sees
> `info.data` containing only the fields declared _above_ it. `Settings.validate_secret_key`
> reads `ENVIRONMENT` from `info.data` and works only because `ENVIRONMENT` is declared first.
> Reorder the class and the guard silently stops guarding. Cross-field logic is safer in a
> `model_validator(mode="after")`, which sees everything.

> **By the Way.** The v1 `@validator("end_date")` with a `values` dict is gone from
> `backend/app` (`git grep -n "@validator(" backend/app` finds nothing). When you port one,
> changing the decorator is not enough: under `@field_validator` the second argument is a
> `ValidationInfo`, not a dict, so `"start_date" in values` raises `TypeError`. Read
> `info.data` instead, or move the rule to a `model_validator`.

### Try It Yourself

```python
from backend.app.schemas.tracking import EventCreate, EventRequest
EventCreate(event_name="purchase", user_id="u", experiment_id="e", metadata='{"sku": "a"}').metadata   # {'sku': 'a'}: parsed
EventCreate(event_name="purchase", user_id="u", experiment_id="e", metadata='{not json')             # ValidationError: "must be a JSON object: ..."
EventRequest(event_type="purchase", user_id="u")                                                    # valid: neither key is required (the endpoint stores it as history)
EventCreate(event_name="purchase", user_id="u")                                                      # ValidationError: Either experiment_id or feature_flag_id must be provided
EventRequest(event_type="purchase", user_id="u", experiment_key="k", metadata='{"sku": "a"}')        # ValidationError: dict_type

from backend.app.schemas.rollout_schedule import RolloutScheduleCreate
# build a schedule with stages 10 → 50 → 25 and read the message; then fix the order and gap the stage_order
# then list {stage_order: 2, target_percentage: 10} before {stage_order: 1, target_percentage: 50}:
# rising in the list, falling in stage_order → "Stage percentages must be non-decreasing in stage_order"

from backend.app.schemas.feature_flag import FeatureFlagCreate
FeatureFlagCreate(key="k", name="n", value_type="string", on_value="a")                  # value_required at ('off_value',)
FeatureFlagCreate(key="k", name="n", value_type="string", on_value="a", off_value="a")   # values_equal at ('off_value',)
FeatureFlagCreate(key="k", name="n", value_type="number", on_value=True, off_value=0)    # value_type_mismatch: a bool is not a number
```

`EventCreate` inherits `parse_json_string` from `EventFields` (through `EventBase`); the public tracking API's
`EventRequest` is a separate model (on `StorableTextModel`, Hour 3) without it, so it takes
`metadata` only as a JSON object — the last line is a 422 over HTTP. Same field name,
different contract: always check which model a route actually uses.

Then write a validator: add (in a scratch subclass, not the repo) a `model_validator` to
`ExperimentCreate` that requires exactly one `is_primary` metric, and send a two-primary body
to `POST /experiments/` to see what the API does today.

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
- **Checking a list in the order it arrived** when its consumer reads it in another order
  (`stage_order`). Sort the way the consumer will.

### Key takeaways

- `field_validator`: one field, normalise or refuse; `mode="before"` for raw input; can cover
  several fields.
- `model_validator(mode="after")`: cross-field rules; return `self`.
- Raise `ValueError` with the sentence you want the user to read; raise
  `PydanticCustomError` for a stable `type` a client can branch on; build a `ValidationError`
  from `InitErrorDetails` to put a model validator's errors at their own fields.
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
| `extra="ignore" / "forbid"` | drop / reject unknown fields                                           | `Settings` ignores; flag requests forbid |
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
    # Null once the creator's account has been removed (the column is
    # ON DELETE SET NULL): such an experiment still reads and lists, with
    # `owner_id: null`.
    owner_id: Optional[UUID4] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    resume_at: Optional[datetime] = Field(
        None,
        description=(
            "When a PAUSED experiment is scheduled to resume, set by PUT "
            "/experiments/{id}/schedule on a paused experiment. Null when no "
            "resume is scheduled; any change of status clears it."
        ),
    )
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
    def create_experiment(
        self,
        obj_in: Union[ExperimentCreate, Dict[str, Any]],
        user_id: UUID,
    ) -> Dict[str, Any]:
        ...
        # Extract data from the schema, excluding unset values
        obj_data = jsonable_encoder(obj_in, exclude_unset=True)

        # Extract nested objects
        variants_data = obj_data.pop("variants", [])
        metrics_data = _with_stored_metric_types(obj_data.pop("metrics", None) or [])
        ...
        experiment = Experiment(**obj_data)
```

`EventService` accepts _either_ a model or a dict, which is the flexible-input idiom for
services called from endpoints (models) and scripts (dicts):

```python
# 📁 backend/app/services/event_service.py
    @staticmethod
    def _as_dict(
        event_data: Union[EventCreate, UntaggedEventCreate, Mapping[str, Any]],
    ) -> Dict[str, Any]:
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

Three ideas in Controlvar's `core/config.py` that go beyond "read an env var":

1. **A canonical environment name** with legacy aliases accepted and warned about.
2. **Subclasses per environment** (`DevSettings`, `TestSettings`, `ProdSettings`) differing
   only in defaults, selected at import from the canonical name.
3. **Validators that fail closed** — weak secrets and the dev bypass are refused in staging
   and production at construction time, so a misconfigured deployment never serves a request.
   And the refusal never repeats the value it refused (`hide_input_in_errors=True`), because a
   settings error goes to a log, and the value may be a secret.

### In the wild

The base class and the two settings that trip newcomers — `NoDecode` and `extra="ignore"`:

```python
# 📁 backend/app/core/config.py
class Settings(BaseSettings):
    """Base settings class."""

    PROJECT_NAME: str = "Controlvar"
    ...
    # The alias is the point.  As a plain field this read the bare ``VERSION``
    # environment variable -- about the most generic name there is, exported by
    # build scripts, base images and CI steps everywhere, ...
    VERSION: str = Field(
        default_factory=get_version, validation_alias="EXPERIMENTLY_VERSION"
    )
    API_V1_STR: str = "/api/v1"
    ...
    ENVIRONMENT: EnvironmentName = "development"
    ...
    ALLOWED_HOSTS: Annotated[List[str], NoDecode] = []

    # Plain strings, normalised by `cors_allowed_origins`: as `AnyHttpUrl`
    # every entry rendered with a trailing slash that no browser `Origin`
    # carries, so this setting matched nothing (#126), and one malformed entry
    # failed settings validation and stopped the API starting.
    BACKEND_CORS_ORIGINS: Annotated[List[str], NoDecode] = []
    # CORS_ORIGINS is a plain-string list version of BACKEND_CORS_ORIGINS that
    # can also be set via env var as a comma-separated string. NoDecode stops
    # pydantic-settings from JSON-decoding the raw value so the "before"
    # validator below receives it as-is.
    CORS_ORIGINS: Annotated[List[str], NoDecode] = []
    ...
    DASHBOARD_ORIGINS: Annotated[List[str], NoDecode] = []
    ...
    model_config = SettingsConfigDict(
        case_sensitive=True,
        extra="ignore",  # Ignore unknown fields to catch typos
        # A refused configuration says which setting and why, never the
        # values it was given.
        hide_input_in_errors=True,
    )
```

Two fields here carry a lesson each. `VERSION` is read from the repository's `VERSION` file by
a `default_factory`, and its environment variable is `EXPERIMENTLY_VERSION` via
`validation_alias` (the variable keeps the product's old name; Controlvar was called
Experimently before 0.35.0): a field named `VERSION` would otherwise be overridden by any process that
happens to export the most generic variable name there is, and the API would report the wrong
version. And `BACKEND_CORS_ORIGINS` used to be `List[AnyHttpUrl]`, which renders every entry
with a trailing slash — `http://localhost:3000/` — that no browser `Origin` header carries, so
the setting matched nothing. A precise type is not always the right type: the comparison it
feeds decides.

The URL-shaped lists — `ALLOWED_HOSTS`, `BACKEND_CORS_ORIGINS`, `CORS_ORIGINS`,
`DASHBOARD_ORIGINS` — are all `NoDecode`, and the hosts are derived where they can be:
leave `ALLOWED_HOSTS` empty and the allow-list is `PUBLIC_BASE_URL`'s host, a default that
cannot point at the wrong name.

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

The fail-closed guards, in three flavours. First a `mode="before"` **model** validator that
refuses a combination outright, before any field is validated:

```python
# 📁 backend/app/core/config.py
    @model_validator(mode="before")
    @classmethod
    def refuse_testing_in_a_hardened_environment(cls, data: Any) -> Any:
        """``TESTING`` belongs to the test runner, never to staging or production.
        ...
        """
        environment: Any = None
        if isinstance(data, dict):
            environment = data.get("ENVIRONMENT")
        if environment is None:
            environment = cls.model_fields["ENVIRONMENT"].default
        if isinstance(environment, str):
            environment = canonical_environment_quiet(environment)
        # The rule and its message are settings_rules', which the container's
        # start-up check applies too.
        refusal = testing_refusal(environment, os.environ.get("TESTING"))
        if refusal:
            raise ValueError(refusal)
        return data
```

`TESTING=true` is what the test suite sets. In a staging or production process it can only be a
mistake, so rather than let it change what those environments require, the settings refuse to
exist. Being `mode="before"`, its message is the one an operator sees — not a confusing error
from whichever setting `TESTING` would otherwise have affected.

Then an `after` model validator for a rule over two fields, and a field validator for a
secret:

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

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str, info: ValidationInfo) -> str:
        ...
        if _hardening_required(info):
            if _secret_is_placeholder(v) or len(v) < _MIN_SECRET_KEY_LENGTH:
                raise ValueError(
                    f"SECRET_KEY must be at least {_MIN_SECRET_KEY_LENGTH} characters "
                    "and must not be a placeholder from the repository (docker-compose.yml, "
                    ".env.example or the class default) in staging/production. "
                    'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
                )
        return v
```

with the one helper every secret guard asks:

```python
# 📁 backend/app/core/config.py
def _hardening_required(info: ValidationInfo) -> bool:
    """Secrets must be real in staging/production.

    ``TESTING`` does not relax this: ``Settings`` refuses ``TESTING`` together
    with a staging or production environment before any field is validated
    (``refuse_testing_in_a_hardened_environment``).
    """
    environment = (info.data or {}).get("ENVIRONMENT", "development")
    return environment in HARDENED_ENVIRONMENTS
```

`HARDENED_ENVIRONMENTS` is `("staging", "production")`. Asking one helper instead of writing
`environment == "production"` at each guard is what keeps staging hardened along with
production, and makes an unrecognised environment name fail closed rather than match no
`==` test. `validate_superuser_password` (`FIRST_SUPERUSER_PASSWORD`, which refuses the demo
password and anything under 8 characters) has the same shape. The settings of the optional
modules — the audit-log signing key, SSO, HIPAA, the warehouse connectors — are not on this
class at all: they live on `ModulesSettings` in `modules/backend/app/settings.py`, so a core
deployment never has to supply a module's secret to start.

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
    LOG_LEVEL: str = (
        "INFO"  # set LOG_LEVEL=DEBUG explicitly; DEBUG makes every library chatty
    )
    ...
    POSTGRES_DB: str = "experimentation_test"
    POSTGRES_SCHEMA: str = "test_experimentation"
    CACHE_ENABLED: bool = False
    ...
    model_config = SettingsConfigDict(
        env_file=ENV_FILES["test"],
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )
```

Note that a subclass's `model_config` replaces the parent's keys only where it names them —
but each subclass here restates `hide_input_in_errors=True` anyway, so nobody has to know that
rule to be safe.

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

> **Watch Out!** `TESTING` relaxes nothing. Combined with `ENVIRONMENT=staging` or
> `production` it is refused outright ("TESTING is for the test runner and cannot be combined
> with ENVIRONMENT=production"), so a test that wants to exercise a production guard
> constructs `ProdSettings(...)` with explicit values, and a hand check clears it first:
> `env -u TESTING -u APP_ENV ENVIRONMENT=production SECRET_KEY=changeme python -c "import
> backend.app.core.config"`.

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
# the guards, with TESTING cleared: SECRET_KEY and FIRST_SUPERUSER_PASSWORD refused, no values echoed
env -u TESTING -u APP_ENV ENVIRONMENT=staging SECRET_KEY=changeme python -c "import backend.app.core.config" > guard.txt 2>&1; grep -A1 "validation error" guard.txt
# TESTING is not an escape hatch: refused before any field is checked
env -u APP_ENV TESTING=true ENVIRONMENT=production python -c "import backend.app.core.config" > guard.txt 2>&1; grep "TESTING is" guard.txt
```

Then add a hypothetical `WEBHOOK_SIGNING_SECRET` with a fail-closed validator in a scratch
subclass and prove it refuses in production and passes in development.

### Common mistakes

- **A `List` setting from a comma-separated env var without `NoDecode`** — silently empty.
- **Reading `info.data["X"]` for a field declared below** — not there; declare guards after
  the fields they depend on, or use a model validator.
- **Re-importing `config` in a test to "reset" settings** — it's a singleton; set attributes
  or construct a subclass explicitly.
- **Reading `TESTING` (or comparing `ENVIRONMENT == "production"`) inside a guard.** Ask
  `_hardening_required(info)`; staging is hardened too, and `TESTING` is refused there anyway.
- **Echoing the value in a settings error.** `hide_input_in_errors=True` keeps Pydantic from
  doing it; do not put it back in your own message.

### Key takeaways

- `BaseSettings` reads typed, validated config from the environment; one singleton
  `settings` is selected per canonical environment at import.
- `NoDecode` + a `before` validator for comma-separated lists; `before` validators assemble
  DSNs from parts.
- Validators fail closed in staging and production (`_hardening_required`); `TESTING`
  relaxes nothing and is refused in those environments.
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

**Q5. Why does `CORS_ORIGINS` need `NoDecode` but `COGNITO_ADMIN_GROUPS: List[str]`
doesn't?**
Both are lists; the difference is how operators set them. `COGNITO_ADMIN_GROUPS` is expected
as JSON (`["Admins"]`), which pydantic-settings decodes fine (and `Admins,Ops` fails to parse).
`CORS_ORIGINS` is documented as comma-separated, which JSON decoding rejects — `NoDecode`
hands the raw string to the validator.

**Q6. What does `TESTING=true` change about the guards in a production process, and why?**
Nothing gets relaxed: `refuse_testing_in_a_hardened_environment`, a `mode="before"` model
validator, refuses `TESTING` together with staging or production before any field is checked.
`TESTING` exists for the test runner; in a deployment it can only be a mistake, and if it
relaxed the secret guards it would be one variable away from a production API signing tokens
with the class-default key. The dev-bypass guard and the secret guards never consult it.

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
that refuses values under 30 in the hardened environments, in the house style, and explain
why a `field_validator` suffices here where the bypass rule needed a `model_validator`.

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
        if _hardening_required(info) and v < 30:
            raise ValueError("RESULTS_CACHE_TTL_SECONDS must be at least 30 in staging/production")
        return v
```

`_hardening_required` covers staging as well as production, and nothing reads `TESTING`
(it is refused in those environments before this runs). A `field_validator` suffices because
the rule concerns one field plus `ENVIRONMENT`, which is declared above it and therefore
available in `info.data`. The bypass rule reads two fields as a unit, and as a `mode="after"`
`model_validator` it can't be defeated by declaration order.

---

**Next:** [Part III — Dependency Injection & Auth →](./part-3-dependencies-and-auth.md)

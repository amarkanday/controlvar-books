# Part IV — SQLAlchemy 2 & PostgreSQL

### Hours 14–18

[← Part III](./part-3-dependencies-and-auth.md) · [Contents](./README.md) · [Part V →](./part-5-beyond-requests.md)

SQLAlchemy is the layer between Python objects and PostgreSQL rows, and it's the layer where
"it works on my laptop" most often diverges from "it works under load and in tests." This Part
covers the declarative models and the schema convention that lets tests and the app share one
server (Hour 14), relationships and what cascades when (Hour 15), the session's lifecycle and
the pool behind it (Hour 16), how the codebase queries — including JSONB and the rare raw SQL
(Hour 17) — and how the schema evolves with Alembic and the bootstrap (Hour 18). Keep `psql`
open next to Python; the fastest way to understand an ORM is to watch the SQL it emits.

> **Version note.** SQLAlchemy 2.0.52. The codebase uses the **legacy `Query` API**
> (`db.query(Model).filter(...)`) throughout rather than 2.0's `select()`; both work in 2.x,
> and consistency wins. New code should match the surrounding module.

---

## Hour 14 — Declarative models, columns, the schema, mixins

### What & why

A **declarative model** is a Python class that maps to a table: `__tablename__`, `Column`s
with types and constraints, relationships to other models, and a `__table_args__` tuple for
indexes, check constraints and — crucially here — the **schema**. Every model in
Experimently inherits from two things: `Base` (the declarative base whose `MetaData`
collects every table) and `BaseModel`, a mixin that contributes a UUID primary key,
`created_at`/`updated_at`, and a `to_dict()` helper.

#### Why UUIDs, why a mixin, why an explicit schema

**UUID primary keys** (`UUID(as_uuid=True)`, default `uuid.uuid4`) mean any process — a
seed, a test, a Lambda — can mint an id without asking the database, ids are safe in URLs,
and nothing depends on ordering. **The mixin** means every table has the same three columns
without copy-paste, so `to_dict()` and the audit tooling can rely on them. **The explicit
schema** — `{"schema": get_schema_name()}` in every `__table_args__` and in every
`ForeignKey` string — is what lets the app use `experimentation` and the tests use
`test_experimentation` in the same PostgreSQL server; SQLAlchemy does not propagate
`metadata.schema` into foreign-key target strings, so the codebase writes them out.

### In the wild

The base and the mixin:

```python
# 📁 backend/app/models/base.py
# Create metadata without schema
metadata = MetaData()

# Create base with metadata
Base = declarative_base(metadata=metadata)

# Set schema immediately
metadata.schema = get_schema_name()
for table in metadata.tables.values():
    table.schema = get_schema_name()


class BaseModel:
    """
    Abstract base model for common columns and methods.
    This is a mixin, not a table itself.
    """

    # Include common columns
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self):
        """Convert model instance to dictionary."""
        result = {}
        for column in self.__table__.columns:
            value = getattr(self, column.name)
            if isinstance(value, datetime):
                value = value.isoformat()
            elif isinstance(value, uuid.UUID):
                value = str(value)
            result[column.name] = value
        return result
```

(`default=datetime.utcnow` — naive UTC. `onupdate` runs on every `UPDATE` the ORM issues.
Both are Python-side defaults, not database defaults: a row inserted by raw SQL gets
neither.)

A complete model with every kind of column, a schema-qualified foreign key, an index, a check
constraint and the schema in `__table_args__`:

```python
# 📁 backend/app/models/feature_flag.py
class FeatureFlag(Base, BaseModel):
    """Feature flag model for feature toggles."""

    __tablename__ = "feature_flags"

    key = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    status = Column(
        Enum(FeatureFlagStatus),
        default=FeatureFlagStatus.INACTIVE,
        nullable=False,
        index=True,
    )
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{get_schema_name()}.users.id", ondelete="SET NULL"),
    )
    targeting_rules = Column(JSONB)  # Rules for flag enablement
    rollout_percentage = Column(Integer, default=0, nullable=False)  # Gradual rollout
    variants = Column(JSONB)  # For multivariate flags
    tags = Column(JSONB)  # For categorization
    ...
    @declared_attr
    def __table_args__(cls):
        schema_name = get_schema_name()
        return (
            # Composite index for owner + status
            Index(f"{schema_name}_feature_flag_owner_status", "owner_id", "status"),
            # Check that rollout percentage is between 0 and 100
            CheckConstraint(
                "rollout_percentage >= 0 AND rollout_percentage <= 100",
                name="check_rollout_percentage",
            ),
            {"schema": schema_name},
        )

    def __repr__(self):
        return f"<FeatureFlag {self.key}>"
```

Column types worth knowing: `String(n)` (varchar with a length the database enforces),
`Text` (unbounded), `Integer`, `Float`, `Boolean`, `DateTime` (naive here),
`Enum(PyEnum)` (a PostgreSQL enum type named after the class, lower-cased), and the
PostgreSQL-specific `UUID(as_uuid=True)` and `JSONB`. `index=True` creates a single-column
index; `unique=True` a unique constraint; composite indexes go in `__table_args__` with a
schema-prefixed name because index names are global within a schema.

`@declared_attr` on `__table_args__` is necessary because `get_schema_name()` is evaluated
_when the class is built_, and the decorator makes it a per-class computation rather than a
shared class attribute — which matters when the same model module is imported under two
schema settings in one process (the bootstrap's `_point_models_at_schema` repoints tables
afterwards for exactly that reason).

An association table — the many-to-many for users and roles — declared as a `Table` rather
than a class, with the schema passed explicitly:

```python
# 📁 backend/app/models/user.py
user_role_association = Table(
    "user_role_association",
    Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey(f"{_get_users_table_name()}.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", UUID(as_uuid=True), ForeignKey(f"{_get_roles_table_name()}.id", ondelete="CASCADE"), primary_key=True),
    schema=get_schema_name(),
)
```

> **Watch Out!** The `Event` model overrides the mixin's `created_at` with a **`String`**
> column holding an ISO timestamp. That's a historical wart, not a pattern — `EventService`
> normalises every timestamp to `2026-09-11T14:03:22.118342+00:00` so string comparison
> still sorts correctly, and the daily results code parses it back. Don't copy it; do know it
> when you query `events` by date.

> **By the Way.** PostgreSQL enum casing bit this codebase once: `ExperimentStatus` values
> are lower-case (`"draft"`), `FeatureFlagStatus` upper-case (`"ACTIVE"`), and a migration
> (`fce74a1b62d3_update_enum_type.py`) exists solely to add lower-case values to enums that
> had been created upper-case. `Enum(PyEnum)` stores the member _name_ by default unless
> `values_callable` says otherwise, which is why the flag service compares against both the
> member and its `.value`. For a new enum column, prefer `String` + `CheckConstraint` if the
> set might change.

### Try It Yourself

Emit the DDL for a model and compare it with the live table:

```bash
python - <<'PY'
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
import backend.app.models
from backend.app.models.assignment import Assignment
print(CreateTable(Assignment.__table__).compile(dialect=postgresql.dialect()))
for idx in Assignment.__table__.indexes: print("INDEX", idx.name, [c.name for c in idx.columns], "unique" if idx.unique else "")
PY
docker compose exec postgres psql -U postgres -d experimentation -c '\d experimentation.assignments'
```

Then confirm the schema convention: `git grep -c '{"schema": schema_name}' backend/app/models`
and `git grep -n 'ForeignKey("' backend/app/models` (there should be none without the
schema prefix).

### Common mistakes

- **`ForeignKey("users.id")` without the schema prefix.** Resolves against `public` and fails
  at `create_all` with "could not find table".
- **A new model not imported in `backend/app/models/__init__.py`.** Relationship strings
  can't be resolved; scripts that import only some models get "not mapped".
- **Two import paths for one model.** Two classes, one table — `Class is not mapped`
  errors. Fully qualified imports only.
- **Mutable Python defaults on JSONB columns** (`default={}`). SQLAlchemy calls callables
  per row but shares literal dicts; use `default=dict` or leave nullable.

### Key takeaways

- `class X(Base, BaseModel)`: UUID `id`, naive-UTC `created_at`/`updated_at`, `to_dict()`.
- Every FK string and every `__table_args__` names the schema; index names are
  schema-prefixed; `@declared_attr` makes `__table_args__` per-class.
- `String(n)`/`Text`/`Enum`/`UUID(as_uuid=True)`/`JSONB`; constraints as `CheckConstraint`,
  `unique`, `Index`.
- Enum casing and the `events.created_at` string are known warts — work with them, don't
  copy them.

---

## Hour 15 — Relationships, cascades, `back_populates`, `joinedload`

### What & why

A `relationship()` gives a model an attribute that loads related rows through a foreign key:
`experiment.variants` (one-to-many), `variant.experiment` (many-to-one), `flag.safety_config`
(one-to-one with `uselist=False`), `user.roles` (many-to-many through an association table).
Three decisions come with every relationship:

1. **How is the other side declared?** Experimently uses `back_populates` on _both_ models,
   never `backref`. It's more typing and it makes each model file self-describing: you can
   read `Variant` and know it has an `experiment` without opening `Experiment`.
2. **What happens on delete?** Two layers again: the ORM's `cascade="all, delete-orphan"`
   deletes children when the parent is deleted _through the session_; the database's
   `ondelete="CASCADE"`/`"SET NULL"` on the foreign key does it for any writer. They should
   agree.
3. **When is it loaded?** By default, lazily — the first access issues a `SELECT`. For a list
   endpoint that will touch `experiment.variants` for every row, that's N+1 queries;
   `options(joinedload(Experiment.variants))` fetches them in the same query.

### In the wild

Both sides of a one-to-many, with matching `back_populates`:

```python
# 📁 backend/app/models/experiment.py
class Experiment(Base, BaseModel):
    ...
    variants = relationship("Variant", back_populates="experiment", cascade="all, delete-orphan")
    metric_definitions = relationship("Metric", back_populates="experiment", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="experiment", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="experiment", cascade="all, delete-orphan")


class Variant(Base, BaseModel):
    ...
    experiment_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="CASCADE"), nullable=False)
    ...
    experiment = relationship("Experiment", back_populates="variants")
    assignments = relationship("Assignment", back_populates="variant", cascade="all, delete-orphan")
```

String class names (`"Variant"`) rather than the class itself avoid circular imports — the
registry resolves them when mappers are configured, which is why every model must be
imported somewhere before the first query (`backend/app/models/__init__.py` does it).

The two delete layers, and where they _disagree_ on purpose: an `Event` outlives its
experiment at the database level (`SET NULL`) even though the ORM relationship would cascade.
Deleting through the session removes the events; a database-level delete keeps them with a
null `experiment_id`. Audit data survives the path that operators use (SQL), and the ORM
path is the one tests exercise:

```python
# 📁 backend/app/models/event.py
    experiment_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="SET NULL"), nullable=True)
```

A one-to-one:

```python
# 📁 backend/app/models/feature_flag.py
    safety_config = relationship(
        "FeatureFlagSafetyConfig",
        back_populates="feature_flag",
        cascade="all, delete-orphan",
        uselist=False  # One-to-one relationship
    )
```

Eager loading where the code knows it will touch children:

```python
# 📁 backend/app/services/assignment_service.py
        experiment = (
            self.db.query(Experiment)
            .options(joinedload(Experiment.variants))
            .filter(Experiment.id == experiment_id)
            .first()
        )
```

```python
# 📁 backend/app/services/experiment_service.py
        query = self.db.query(Experiment).options(
            joinedload(Experiment.variants), joinedload(Experiment.metric_definitions)
        )
```

`assign_user` needs every variant to build the cumulative distribution; one `LEFT OUTER
JOIN` beats a second query. The list endpoint touches variants and metrics for every row it
serialises; two `joinedload`s turn 1 + 2N queries into one.

> **Watch Out!** `joinedload` on a one-to-many multiplies rows (one per child) and
> SQLAlchemy de-duplicates parents in memory — fine for a page of 100 experiments with a
> handful of variants each, but `joinedload` on `Experiment.events` would pull every event
> into the join. For large collections use `selectinload` (a second `IN` query) or don't load
> them at all — count them (Hour 17).

> **Did You Know?** `Variant.assignments` is declared twice in `models/experiment.py` with
> identical arguments. It's harmless (the second definition replaces the first) and it's the
> "duplicate SQLAlchemy relationship definitions" warning in `CLAUDE.md`. A _non_-identical
> duplicate would silently change behaviour — which is why reviewers grep for it.

### Try It Yourself

Watch lazy loading happen, then make it not:

```python
import logging; logging.basicConfig(); logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)
from sqlalchemy.orm import joinedload
from backend.app.db.session import SessionLocal
from backend.app.models.experiment import Experiment
db = SessionLocal()
exps = db.query(Experiment).limit(3).all()          # one SELECT
[len(e.variants) for e in exps]                      # three more SELECTs — lazy, N+1
exps = db.query(Experiment).options(joinedload(Experiment.variants)).limit(3).all()   # one SELECT with a JOIN
[len(e.variants) for e in exps]                      # no more queries
```

Then delete an experiment through the ORM in a transaction you roll back, and count its
variants before and after: `db.delete(exp); db.flush(); db.query(Variant).filter_by(experiment_id=exp.id).count(); db.rollback()`.

### Common mistakes

- **`backref`** — works, but hides the reverse attribute from the other model's file. House
  style is `back_populates` both sides.
- **Cascades that disagree with `ondelete`** without a reason. Match them, or comment why
  (events).
- **Accessing a relationship after the session closed** (`DetachedInstanceError`). Serialise
  inside the request; `joinedload` what the response needs.
- **`joinedload` on a large collection.** Use `selectinload` or aggregate queries.

### Key takeaways

- `relationship("Other", back_populates="attr")` on both models; string names resolve at
  mapper configuration.
- ORM `cascade` + database `ondelete`: usually matching (`CASCADE` for children), `SET NULL`
  where rows must survive.
- Default loading is lazy → N+1; `joinedload` for small collections you'll touch,
  `selectinload` for large ones, counts instead of loads when you only need numbers.

---

## Hour 16 — Sessions, transactions, `flush`/`commit`/`refresh`, the pool

### What & why

A `Session` is a unit of work: it holds the objects you've loaded or added, tracks changes,
and turns them into SQL inside a transaction. Four verbs cover 95% of what services do:

| Verb            | Does                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------- |
| `db.add(obj)`   | Registers a new object; no SQL yet                                                                |
| `db.flush()`    | Emits pending `INSERT`/`UPDATE`/`DELETE` inside the current transaction; generated ids become available; nothing is committed |
| `db.commit()`   | Flushes, then `COMMIT`s; by default expires loaded objects so the next attribute access re-reads  |
| `db.refresh(obj)` | Re-`SELECT`s one object (server defaults, `onupdate` values)                                    |
| `db.rollback()` | Abandons the transaction; the session is usable again                                              |

Because `SessionLocal` is `autoflush=False`, a query does **not** flush pending changes
first. That's why `create_experiment` calls `flush()` before creating variants: it needs
`experiment.id` (generated by the Python default on flush) to set `variant.experiment_id`.

#### Why the engine's pool settings look the way they do

The engine is the process-wide connection pool. Twenty pooled connections plus twenty
overflow, recycled every five minutes, pre-pinged before use, with TCP keepalives — because
production runs behind an Aurora endpoint that can fail over, and a pooled connection that
silently died would otherwise be handed to a request and fail on first use. Tests use a
`NullPool` engine (a fresh connection per checkout) so the per-process database can be
dropped without pooled connections holding it open.

### In the wild

The canonical create — add, flush for the id, add children, commit, refresh, return:

```python
# 📁 backend/app/services/experiment_service.py
        # Create experiment
        experiment = Experiment(**obj_data)
        if not experiment.key:
            experiment.key = self.generate_key(experiment.name)
        self.db.add(experiment)
        self.db.flush()  # Flush to get the experiment ID

        # Create variants
        for variant_data in variants_data:
            variant = Variant(**variant_data, experiment_id=experiment.id)
            self.db.add(variant)

        # Create metrics
        for metric_data in metrics_data:
            ...
            metric = Metric(**metric_data, experiment_id=experiment.id)
            self.db.add(metric)

        self.db.commit()
        self.db.refresh(experiment)

        logger.info(f"Created experiment {experiment.id}: {experiment.name}")
        return self._experiment_to_dict(experiment)
```

Everything between the first `add` and the `commit` is one transaction: if a metric
violates the unique `(experiment_id, name)` index, the `commit` raises, nothing is written,
and the endpoint's `except` turns it into a 400.

A small write with the same shape:

```python
# 📁 backend/app/services/assignment_service.py
        # Create new assignment
        assignment = Assignment(user_id=user_id, experiment_id=experiment_id, variant_id=variant_id)

        self.db.add(assignment)
        self.db.commit()
        self.db.refresh(assignment)
```

A bulk delete with `synchronize_session=False` — the ORM doesn't try to find and expire the
deleted objects in memory, which is right when nothing in the session references them:

```python
# 📁 backend/app/services/assignment_service.py
        # Delete assignments
        delete_query = self.db.query(Assignment).filter(Assignment.experiment_id == experiment_id)
        delete_query.delete(synchronize_session=False)

        self.db.commit()
```

A no-request session with explicit error handling — the scheduler's tick:

```python
# 📁 backend/app/core/scheduler.py
        db = SessionLocal()
        try:
            ...
            db.commit()
        except Exception as e:
            logger.error(f"Error processing scheduled experiments: {str(e)}")
            db.rollback()
        finally:
            db.close()
```

And a helper that shows why `refresh` and `commit` are separate concerns — the API key's
`update_last_used` writes a server-side `func.now()` and commits immediately:

```python
# 📁 backend/app/models/api_key.py
    def update_last_used(self, db_session) -> None:
        """Update the last_used_at timestamp."""
        self.last_used_at = func.now()
        db_session.add(self)
        db_session.commit()
```

The engine, once more, with the reasons in the comments:

```python
# 📁 backend/app/db/session.py
engine = create_engine(
    database_uri,
    pool_pre_ping=True,  # Test connections before using them
    pool_size=20,  # Increased pool size
    max_overflow=20,  # Increased overflow
    pool_recycle=300,  # Recycle connections after 5 minutes
    pool_timeout=30,  # Wait up to 30 seconds for a connection
    connect_args={"connect_timeout": 10, "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 5},
)
```

> **Watch Out!** After `commit()`, every loaded object is **expired**: the next attribute
> access issues a `SELECT`. In a request that's harmless (the session is still open). In a
> test session it can be fatal — the connection may already be gone under `NullPool` — which
> is why the harness creates its session with `expire_on_commit=False`. If you see a
> `SELECT` you didn't expect right after a commit, that's expiry.

> **By the Way.** `db.commit()` in a **service** is the house convention; endpoints don't
> commit. That keeps transaction boundaries next to the logic that defines them and lets a
> service be reused from a scheduler or a script with the same semantics. The one thing an
> endpoint _does_ do after a service commits is invalidate caches (`cache_control.redis.delete(...)`).

### Try It Yourself

Feel `flush` vs `commit` and the pool:

```python
from backend.app.db.session import SessionLocal, engine
from backend.app.models.experiment import Experiment
from backend.app.models.user import User
db = SessionLocal()
owner = db.query(User).first()
e = Experiment(name="flush demo", owner_id=owner.id)
db.add(e); print(e.id)              # a UUID already? (Python default runs at flush — try before and after)
db.flush(); print(e.id)
print(engine.pool.status())
db.rollback()                        # gone; nothing committed
print(db.query(Experiment).filter_by(name="flush demo").count())   # 0
db.close(); print(engine.pool.status())
```

Then read `backend/tests/conftest.py::test_db` and find where the production engine's pool
is disposed before the test database is dropped — and why.

### Common mistakes

- **Reading `obj.id` before `flush()`** and getting `None` (or a Python-generated UUID that
  hasn't been inserted — fine for the value, wrong if you then query for the row).
- **Committing inside a loop.** One transaction per logical operation; commit once.
- **Catching an `IntegrityError` and continuing without `rollback()`.** The session is in a
  failed state; every subsequent statement raises `PendingRollbackError`.
- **Long-lived sessions across requests or threads.** One per request/tick; never shared.

### Key takeaways

- `add` → `flush` (ids, still uncommitted) → `commit` (durable, objects expire) → `refresh`
  (re-read one).
- `autoflush=False`: flush explicitly when you need generated ids before a query.
- Services commit; endpoints don't; schedulers own `try/commit/except/rollback/finally/close`.
- The pool is tuned for Aurora (`pool_pre_ping`, recycle, keepalives); tests use `NullPool`
  and `expire_on_commit=False`.

---

## Hour 17 — Queries: filters, aggregates, JSONB, `text()` done safely

### What & why

The `Query` API is a builder: `db.query(Model)` then `.filter(...)`, `.options(...)`,
`.order_by(...)`, `.offset().limit()`, and finally `.first()`, `.all()`, `.scalar()` or
`.count()`. Filters are column expressions — `Experiment.status == ExperimentStatus.ACTIVE`,
`Experiment.name.ilike("%hero%")`, `Assignment.user_id.in_([...])` — combined with `and_`,
`or_`, `not_`. Aggregates come from `sqlalchemy.func`: `func.count(Assignment.id)`,
`func.sum(RawMetric.count)`, `func.coalesce(...)`.

Three query habits distinguish this codebase:

1. **Count, don't load.** The results engine needs _numbers_ per variant, so it runs
   `query(func.count(...)).filter(...).scalar()` rather than loading rows.
2. **JSONB is queryable.** `Experiment.tags.contains([search])` uses the `@>` containment
   operator; raw SQL uses `jsonb_extract_path_text` and `?` for key existence.
3. **`text()` is rare, bound, and marked.** Where a query needs SQL the builder can't express
   (dynamic JSON paths), it's a `text()` with `:params`, the schema name is the only
   interpolated identifier, and the line carries a `nosemgrep` marker with the justification.

### In the wild

A list query with optional filters, search across columns _and_ a JSONB array, dynamic sort
and pagination:

```python
# 📁 backend/app/services/experiment_service.py
        query = self.db.query(Experiment).options(
            joinedload(Experiment.variants), joinedload(Experiment.metric_definitions)
        )

        if status:
            status_enum = resolve_experiment_status(status)
            if status_enum is None:
                logger.warning("Unknown experiment status filter %r; returning no rows", status)
                return []
            query = query.filter(Experiment.status == status_enum)

        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                or_(
                    Experiment.name.ilike(search_pattern),
                    Experiment.description.ilike(search_pattern),
                    Experiment.tags.contains([search]),  # For JSON array search
                )
            )

        # Apply sorting
        if sort_by and hasattr(Experiment, sort_by):
            sort_field = getattr(Experiment, sort_by)
            if sort_order and sort_order.lower() == "asc":
                query = query.order_by(sort_field.asc())
            else:
                query = query.order_by(sort_field.desc())

        experiments = query.offset(skip).limit(limit).all()
```

(`hasattr(Experiment, sort_by)` is the guard that keeps a user-supplied sort field from
becoming an attribute error — and it's why the endpoint documents the allowed values.)

The core counting queries — the Week 1 join, as SQLAlchemy:

```python
# 📁 backend/app/services/analysis_service.py
        # Get assignment counts per variant
        assignments = {}
        for variant_id in variant_ids:
            count = (
                self.db.query(func.count(Assignment.id))
                .filter(Assignment.experiment_id == experiment.id, Assignment.variant_id == variant_id)
                .scalar() or 0
            )
            assignments[variant_id] = count

        # Get conversion counts per variant
        conversions = {}
        for variant_id in variant_ids:
            count = (
                self.db.query(func.count(Event.id))
                .filter(
                    Event.experiment_id == experiment.id,
                    Event.variant_id == variant_id,
                    conversion_event_filter(metric.event_name),
                )
                .scalar() or 0
            )
            conversions[variant_id] = count
```

`conversion_event_filter` returns a **criterion object** — a reusable piece of `WHERE` — which
is how one rule is shared by every analysis path:

```python
# 📁 backend/app/services/event_matching.py
def conversion_event_filter(event_name: Optional[str]):
    if not event_name:
        return Event.event_type == EventType.CONVERSION.value
    return and_(
        Event.event_name == event_name,
        Event.event_type.notin_(EXPOSURE_EVENT_TYPES),
    )
```

A `coalesce`d sum for the safety error rate (so "no rows" is 0, not `None`):

```python
# 📁 backend/app/services/safety_service.py
        total_evaluations = (
            db.query(func.coalesce(func.sum(RawMetric.count), 0))
            .filter(
                RawMetric.feature_flag_id == feature_flag_id,
                RawMetric.metric_type == MetricType.FLAG_EVALUATION.value,
                RawMetric.timestamp.between(start_time, end_time),
            )
            .scalar()
            or 0
        )
```

And the `text()` case — a dynamic JSON path that the builder can't express, done the way
Semgrep and Bandit accept:

```python
# 📁 backend/app/services/analysis_service.py
        # Get segment values from event metadata.
        # `schema` comes from get_schema_name(), which only ever returns one of
        # two literal identifiers; all request-derived values are bound params.
        schema = get_schema_name()
        segment_values_query = text(  # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
            f"""
            SELECT DISTINCT jsonb_extract_path_text(event_metadata, :segment_key) as segment_value
            FROM {schema}.events
            WHERE experiment_id = :experiment_id
            AND event_metadata ? :segment_key
            AND jsonb_extract_path_text(event_metadata, :segment_key) IS NOT NULL
            AND jsonb_extract_path_text(event_metadata, :segment_key) != ''
        """  # nosec B608 - schema is a fixed config identifier, not user input
        )

        segment_values_result = self.db.execute(
            segment_values_query,
            {"segment_key": segment_by, "experiment_id": str(experiment_id)},
        )
```

Read the two comments as the rule: **identifiers** (the schema) may be interpolated only when
they come from a fixed internal source; **values** (`segment_by`, the experiment id) are
always bound parameters. The `nosemgrep`/`nosec` markers carry the justification so the
security scan (Week 4 of the Build book) passes on purpose, not by accident.

The advisory lock is the other `text()` in the codebase — bound parameter, no identifiers:

```python
# 📁 backend/app/core/scheduler_lock.py
    row = conn.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": lock_name(name)}
    ).scalar()
```

> **Watch Out!** `.count()` on a `Query` wraps your query in `SELECT count(*) FROM (...)`;
> `query(func.count(Model.id)).scalar()` is the direct form. Both are fine; the direct form
> is what the codebase uses for per-variant counts and it composes with `filter()` cleanly.
> Never load `.all()` to `len()` it.

> **Did You Know?** `Experiment.tags.contains([search])` compiles to `tags @> '["search"]'`
> — PostgreSQL's JSONB containment, which uses the GIN index if one exists. `.has_key("k")`
> compiles to `?`, and `Column["key"].astext` extracts a text value. Most JSONB queries you'll
> need are expressible without `text()`; the dynamic-path case above is the exception.

### Try It Yourself

Print the SQL for a builder query and for a `text()` query with bound parameters:

```python
from sqlalchemy.dialects import postgresql
from backend.app.db.session import SessionLocal
from backend.app.models.experiment import Experiment
from backend.app.models.event import Event
from backend.app.services.event_matching import conversion_event_filter
from sqlalchemy import func
db = SessionLocal()
q = db.query(func.count(Event.id)).filter(Event.experiment_id == None, conversion_event_filter("purchase"))
print(q.statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
q2 = db.query(Experiment).filter(Experiment.tags.contains(["checkout"]))
print(q2.statement.compile(dialect=postgresql.dialect()))
```

Then write the query for "distinct users who converted per variant" (the dedup Day 6 of the
Build book mentions) — `func.count(func.distinct(Event.user_id))` — and compare its count
with the event count for a demo experiment.

### Common mistakes

- **`==` against a Python enum member when the column stores a string** (or vice versa).
  Match the column's type; the flag service checks both for the legacy enum case.
- **Filtering on `None` with `== None`** — SQLAlchemy translates it to `IS NULL`, and
  linters complain; `.is_(None)` is explicit.
- **Interpolating a request value into `text()`.** Bound parameters only; the scan blocks it
  for a reason.
- **Loading rows to count them.** `func.count(...).scalar()`.

### Key takeaways

- Build with `query().filter().options().order_by().offset().limit()`; finish with
  `first/all/scalar/count`.
- Reusable criteria (`conversion_event_filter`) keep one rule in one place.
- Aggregates via `func`; `coalesce` so empty sets are 0.
- JSONB: `.contains([...])` (`@>`), `.has_key`, `["k"].astext`; `text()` only for dynamic
  paths, with bound values and marked, justified identifier interpolation.

---

## Hour 18 — Alembic: revisions, autogenerate, the bootstrap

### What & why

**Alembic** versions the schema as a chain of revision scripts under
`backend/app/db/migrations/versions/`. Each has a `revision` id, a `down_revision` pointing to
its parent, `upgrade()` and `downgrade()`. The database records which revision it's at in
`alembic_version` (schema-qualified here). `upgrade head` applies what's missing;
`revision --autogenerate` diffs the models' metadata against the database and drafts a script.

Experimently's twist, met in the Build book: the chain **can't build a fresh database**
(older autogenerated revisions re-create tables), so a fresh database is built by
`backend.app.db.bootstrap` — `Base.metadata.create_all` from the models, then `alembic stamp
head` — and only _existing_ databases run `upgrade head`. Models are truth; migrations are
the delta.

### In the wild

`env.py` wires Alembic to the same `POSTGRES_*` variables everything else uses and puts the
version table in the app schema:

```python
# 📁 backend/app/db/migrations/env.py
# Import your models
from backend.app.models import Base

# Set target_metadata to your SQLAlchemy models
target_metadata = Base.metadata

# Get schema from environment variable
schema = os.environ.get("POSTGRES_SCHEMA", "experimentation")
db_name = os.environ.get("POSTGRES_DB", "experimentation")

_db_user = os.environ.get("POSTGRES_USER", "postgres")
_db_password = os.environ.get("POSTGRES_PASSWORD", "postgres")
_db_host = os.environ.get("POSTGRES_SERVER") or os.environ.get("POSTGRES_HOST") or "localhost"
_db_port = os.environ.get("POSTGRES_PORT", "5432")
postgres_url = f"postgresql://{_db_user}:{_db_password}@{_db_host}:{_db_port}/{db_name}"
config.set_main_option("sqlalchemy.url", postgres_url)
...
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, version_table_schema=schema)
```

The bootstrap's decision and its two branches:

```python
# 📁 backend/app/db/bootstrap.py
def has_alembic_version(engine: Engine, schema: str) -> bool:
    return inspect(engine).has_table("alembic_version", schema=schema)


def create_from_models(engine: Engine, schema: str) -> None:
    """Create every table defined by the SQLAlchemy models inside *schema*."""
    Base = _point_models_at_schema(schema)
    Base.metadata.create_all(bind=engine)
```

A hand-finished migration in the house style — docstring with the epic, a schema helper,
explicit revision ids, backwards-compatible nullable columns:

```python
# 📁 backend/app/db/migrations/versions/ep057_workspaces.py
"""EP-057: Add Multi-Tenant Team Workspaces tables

Revision ID: ep057_workspaces
Revises: ep046_llm_experiments
Create Date: 2026-03-07 00:00:00.000000
...
Adds nullable workspace_id foreign-key columns to:
  experiments    -- backwards-compatible; NULL = legacy/unscoped
  feature_flags  -- backwards-compatible; NULL = legacy/unscoped
"""

# Revision identifiers
revision: str = "ep057_workspaces"
down_revision: Union[str, None] = "ep046_llm_experiments"
branch_labels = None
depends_on = None


def _schema() -> str:
    """Return the current Alembic migration schema (falls back to 'public')."""
    ...
    return os.environ.get("POSTGRES_SCHEMA", schema)


def upgrade() -> None:
    schema = _schema()

    # ── 1. workspaces ─────────────────────────────────────────────────────────
    op.create_table(
        "workspaces",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False, unique=True),
        ...
        schema=schema,
    )
```

The workflow, from the repository root:

```bash
export POSTGRES_DB=experimentation POSTGRES_SCHEMA=experimentation
ALEMBIC="python -m alembic -c backend/app/db/alembic.ini"

$ALEMBIC current                                        # where is this database?
$ALEMBIC history | head                                 # the chain
$ALEMBIC revision --autogenerate -m "add experiment decision_notes"
$EDITOR backend/app/db/migrations/versions/*decision_notes.py    # READ IT
$ALEMBIC upgrade head
$ALEMBIC merge -m "merge heads" <rev1> <rev2>           # only when two branches both added a revision
python -m backend.app.db.bootstrap                      # fresh DB: create_all + stamp; existing: upgrade head
```

What to check in an autogenerated file, in order: (1) `down_revision` is the current head's
**id** (never a slug or filename); (2) the `upgrade()` contains _only_ your change — drop
proposals for indexes or enum alterations you didn't touch are drift, not your migration;
(3) new columns are nullable or defaulted so the previous image keeps working during a
migrate-then-deploy; (4) the schema is taken from a helper, not hard-coded; (5) the
`downgrade()` is at least plausible.

> **Watch Out!** PostgreSQL enums: `ALTER TYPE ... ADD VALUE` can't run inside a transaction
> on older versions and can't be undone; removing or renaming a value needs a new type and a
> column cast. When autogenerate emits an enum change, stop and decide whether a `String` +
> `CheckConstraint` is the better column.

> **By the Way.** The test suite never runs migrations — it uses `create_all` from the models
> — so a migration is tested by _applying_ it: locally, in `db-migrate.yml`, and by the
> production `run-migrations` Fargate task that gates the blue/green deploy. If a migration
> diverges from the models, the next autogenerate on an `upgrade`d database shows the diff.

### Try It Yourself

Run the loop against a scratch database (the Build book's Day 13 exercise), then inspect the
version table and the chain:

```bash
docker compose exec postgres psql -U postgres -d experimentation -c "select * from experimentation.alembic_version"
python -m alembic -c backend/app/db/alembic.ini heads
ls backend/app/db/migrations/versions | wc -l
```

Then find the two revisions the bootstrap docstring blames for the non-replayable chain
(`git grep -n "re-create" backend/app/db/bootstrap.py`) and open one.

### Common mistakes

- **`alembic upgrade head` on an empty database.** Use the bootstrap.
- **Committing an autogenerated migration unread.** Drift, enum changes and wrong
  `down_revision`s all come from this.
- **A `NOT NULL` column without a default** on a live table — the deploy's old image fails
  every insert until the new image is live.
- **Running alembic from `backend/`** with the root-relative `-c` path (or vice versa). From
  the root: `-c backend/app/db/alembic.ini`.

### Key takeaways

- Alembic: revision chain, `alembic_version` in the app schema, `env.py` on the shared
  `POSTGRES_*` variables.
- Fresh database → bootstrap (`create_all` + `stamp head` + first admin); existing → `upgrade
  head`.
- Autogenerate, then read: `down_revision` id, minimal diff, nullable/additive, schema
  helper.
- Migrations are tested by applying them; models are truth.

---

## Part IV Workshop

**Q1. Why does every `ForeignKey` in the models spell out the schema?**
SQLAlchemy doesn't propagate `metadata.schema` into foreign-key target strings; without the
prefix the reference resolves against `public` and `create_all` fails. The explicit schema
is the price of running the app and the tests on one PostgreSQL server.

**Q2. `experiment.events` has `cascade="all, delete-orphan"` but the FK is `ondelete="SET
NULL"`. Which applies when?**
The ORM cascade applies to `db.delete(experiment)` through a session (events deleted). The
database rule applies to any other writer (`DELETE FROM experiments` in SQL: events kept with
a null `experiment_id`). Audit data survives the operator path.

**Q3. Why does `create_experiment` call `flush()` before adding variants?**
`SessionLocal` is `autoflush=False`, so the experiment's `INSERT` — and the id the variants
reference — isn't emitted until `flush()`; without it `experiment.id` is a Python-generated
UUID with no row yet, and a later query in the same transaction wouldn't see it.

**Q4. When is `text()` acceptable, and what must be true of it?**
When the builder can't express the SQL (a dynamic JSONB path). Values must be bound
parameters; the only interpolated identifier is the schema from `get_schema_name()`; the
line carries `nosemgrep`/`nosec` with the justification.

**Q5. What does the bootstrap do on a database that already has `alembic_version`?**
`alembic upgrade head` — the incremental path. Only a database _without_ the version table
gets `create_all` + `stamp head`.

**Q6. A list endpoint serialises `variants` for each of 100 experiments. Without
`joinedload`, how many queries?**
101 (1 for the page, 1 per experiment on first access of `.variants`) — N+1. With
`joinedload(Experiment.variants)`, one.

**Quiz.** After `db.commit()`, `experiment.name` triggers a `SELECT`. Why, and how does the
test harness avoid it?
→ Commit expires loaded objects (`expire_on_commit=True` by default); the harness builds its
session with `expire_on_commit=False` so `NullPool` connections aren't reopened by accident.

---

## Exercises

**E4.1 — Model it.** Write a `DecisionLog` model (experiment FK with `CASCADE`, `decided_by`
user FK with `SET NULL`, `reason: Text`, composite index on `(experiment_id, created_at)`),
plus the `back_populates` line on `Experiment`.

**E4.2 — Load it right.** Write the query for "the 20 most recent decision logs for an
experiment, with their deciding user loaded" without an N+1, and the query for "how many
decision logs per experiment across all experiments" without loading rows.

**E4.3 — Transaction shape.** Write `DecisionLogService.record(experiment_id, user_id,
reason)` in the house style: add, flush, commit, refresh, return a dict; and say what
happens to the row if `refresh` raises.

**E4.4 — JSONB.** Write the builder query for "experiments whose `tags` contain both
`checkout` and `ui`" and the query for "events whose `event_metadata` has a key `platform`",
without `text()`.

**E4.5 — Migration review.** Autogenerate produces, for E4.1, an `upgrade()` that also
contains `op.drop_index("experimentation_experiment_status_dates", ...)`. What do you do, and
why?

---

## Solutions

**S4.1**

```python
class DecisionLog(Base, BaseModel):
    __tablename__ = "decision_logs"

    experiment_id = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="CASCADE"), nullable=False)
    decided_by = Column(UUID(as_uuid=True), ForeignKey(f"{get_schema_name()}.users.id", ondelete="SET NULL"), nullable=True)
    reason = Column(Text, nullable=False)

    experiment = relationship("Experiment", back_populates="decision_logs")
    decider = relationship("User")

    @declared_attr
    def __table_args__(cls):
        schema_name = get_schema_name()
        return (
            Index(f"{schema_name}_decision_log_experiment_created", "experiment_id", "created_at"),
            {"schema": schema_name},
        )

# on Experiment:
    decision_logs = relationship("DecisionLog", back_populates="experiment", cascade="all, delete-orphan")
```

Plus an import in `backend/app/models/__init__.py`.

**S4.2**

```python
recent = (
    db.query(DecisionLog)
    .options(joinedload(DecisionLog.decider))
    .filter(DecisionLog.experiment_id == experiment_id)
    .order_by(DecisionLog.created_at.desc())
    .limit(20)
    .all()
)

per_experiment = (
    db.query(DecisionLog.experiment_id, func.count(DecisionLog.id))
    .group_by(DecisionLog.experiment_id)
    .all()
)   # list of (experiment_id, count) tuples — no ORM objects loaded
```

**S4.3**

```python
class DecisionLogService:
    def __init__(self, db: Session):
        self.db = db

    def record(self, experiment_id: UUID, user_id: UUID, reason: str) -> Dict[str, Any]:
        log = DecisionLog(experiment_id=experiment_id, decided_by=user_id, reason=reason)
        self.db.add(log)
        self.db.flush()       # id available; FK violations surface here
        self.db.commit()
        self.db.refresh(log)
        return log.to_dict()
```

If `refresh` raises (connection lost), the row is already committed — `commit()` succeeded —
so the data is durable; the caller sees an exception and the endpoint returns a 500. That's
acceptable for a log; for an operation where the response must reflect the row, catch and
re-query.

**S4.4**

```python
both_tags = db.query(Experiment).filter(Experiment.tags.contains(["checkout", "ui"]))      # tags @> '["checkout","ui"]'
has_platform = db.query(Event).filter(Event.event_metadata.has_key("platform"))              # event_metadata ? 'platform'
```

**S4.5** — Remove the `drop_index` from _this_ migration. It's drift: the model declares that
index (`Experiment.__table_args__`), so autogenerate proposing to drop it means the database
you diffed against lacks it (built by an older path) or the index name differs. Keep the
migration to exactly the `decision_logs` change, and investigate the drift separately —
either fix the database (create the index) or the model (rename), then confirm a fresh
autogenerate against an `upgrade`d database is empty.

---

**Next:** [Part V — Beyond Request/Response →](./part-5-beyond-requests.md)

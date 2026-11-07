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

> **Version note.** SQLAlchemy 2.0.54. The codebase uses the **legacy `Query` API**
> (`db.query(Model).filter(...)`) throughout rather than 2.0's `select()`; both work in 2.x,
> and consistency wins. New code should match the surrounding module.

---

## Hour 14 — Declarative models, columns, the schema, mixins

### What & why

A **declarative model** is a Python class that maps to a table: `__tablename__`, `Column`s
with types and constraints, relationships to other models, and a `__table_args__` tuple for
indexes, check constraints and — crucially here — the **schema**. Every model in
Controlvar inherits from two things: `Base` (the declarative base whose `MetaData`
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
    ...
    default_value = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # The flag's value type (boolean, string, number or json) and the two values
    # a string, number or json flag serves: ``on_value`` to the users it is on
    # for, ``off_value`` to everyone else (#1497).  A boolean flag keeps both
    # NULL.  ``check_flag_values`` in ``__table_args__`` holds the combinations;
    # the API writes them through ``backend.app.core.flag_values``'s rules and
    # the evaluate route serves them.  ``server_default`` is load-bearing, as
    # for ``default_value``.  ``none_as_null``: Python ``None``
    # is stored as SQL NULL, not as JSON ``null``, which the check refuses.
    value_type = Column(
        String(16),
        nullable=False,
        default="boolean",
        server_default=text("'boolean'"),
    )
    on_value = Column(JSONB(none_as_null=True), nullable=True)
    off_value = Column(JSONB(none_as_null=True), nullable=True)
    variants = Column(JSONB)  # For multivariate flags
    tags = Column(JSONB)  # For categorization
    ...
    workspace_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
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
            CheckConstraint(FLAG_VALUES_CHECK_SQL, name=FLAG_VALUES_CHECK),
            {"schema": schema_name},
        )

    def __repr__(self):
        return f"<FeatureFlag {self.key}>"
```

Four things in that model teach more than their size. `default_value` and `value_type` have
a `server_default` as well as a Python `default`, and the comments in the file say why: during a
rolling deploy the *previous* release's image is still inserting rows without naming the
column, and only a database-side default fills it. `JSONB(none_as_null=True)` settles an
ambiguity every JSON column has — is Python `None` SQL `NULL` or the JSON value `null`? — in
favour of SQL `NULL`, which is what the check constraint expects for a boolean flag.
And `check_flag_values` is Hour 6's value rules again, as SQL (`FLAG_VALUES_CHECK_SQL` at the
top of the file: `value_type = 'boolean' AND on_value IS NULL AND off_value IS NULL`, or two
values whose `jsonb_typeof` matches the type and which differ): the schema answers a client
with a 422 naming the field, the constraint guarantees the table for every writer. Finally,
`workspace_id` is a bare indexed UUID with no `ForeignKey`, because the `workspaces` table
belongs to an optional module and a core model must import without it; the module attaches
the constraint from its own side when it is loaded.

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

> **Watch Out!** The `Event` model overrides the mixin's `created_at` with a **string**
> column holding an ISO timestamp. That's a historical wart, not a pattern. What keeps it
> sortable is a `TypeDecorator`, so no caller can forget:
>
> ```python
> # 📁 backend/app/models/event.py
> class UTCTimestampString(TypeDecorator):
>     impl = String
>     cache_ok = True
>
>     def process_bind_param(self, value: Any, dialect: Any) -> Optional[str]:
>         if value is None:
>             return None
>         return normalize_event_timestamp(value)
> ...
>     created_at = Column(UTCTimestampString, nullable=False, index=True)
> ```
>
> `process_bind_param` runs for every value *bound* through the column type — every write,
> and every comparison: `Event.created_at >= some_datetime` binds `some_datetime` through it
> too, so both sides are UTC strings in the same shape (`2026-09-11T14:03:22.118342+00:00`).
> Values read back are returned as stored. Don't copy the string column; do copy the idea of
> putting a normalisation in the type rather than in every service that touches it.

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
- **A new core model not listed in `CORE_MODEL_MODULES`** (`backend/app/models/__init__.py`).
  `register_core_models()` imports every module in that tuple, and the bootstrap, alembic's
  `env.py` and the test conftest call it; a model missing from it is missing from the schema
  they build, and relationship strings to it can't be resolved. A *module's* model is never
  listed there: the modules package registers it with `hooks.register_model_module()`, and
  `modules_loader.load_modules()` imports it, so a core checkout (no `modules/` directory)
  never sees it.
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

1. **How is the other side declared?** Controlvar uses `back_populates` on _both_ models,
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
imported somewhere before the first query. For core models that is
`register_core_models()` in `backend/app/models/__init__.py`, which imports every module
named in `CORE_MODEL_MODULES`; a module's models arrive through
`hooks.register_model_module()` when the modules package is loaded.

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
        uselist=False,  # One-to-one relationship
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

> **Did You Know?** A relationship declared twice in one class body is legal Python: the
> second assignment simply replaces the first. If the two differ, behaviour silently changes —
> which is why the project's contributor notes warn about duplicate relationship definitions
> and reviewers grep for them (`git grep -n "assignments = relationship" backend/app/models/experiment.py`
> shows one per class today).

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

One more verb matters when part of a transaction may fail and you want to keep the rest:
`db.begin_nested()` opens a **savepoint**. Used as a context manager, it releases the
savepoint on success and rolls back to it on an exception, so one failed attempt does not
poison the outer transaction.

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
        if experiment.key:
            # A key the caller chose: a conflict is theirs to hear about, so
            # it propagates unchanged (the endpoint answers 409 naming it).
            self.db.add(experiment)
            self.db.flush()  # Flush to get the experiment ID
        else:
            self._insert_with_generated_key(experiment)

        # Create variants
        for variant_data in variants_data:
            variant = Variant(**variant_data, experiment_id=experiment.id)
            self.db.add(variant)

        # Create metrics
        for metric_data in metrics_data:
            metric = Metric(**metric_data, experiment_id=experiment.id)
            self.db.add(metric)

        self.db.commit()
        self.db.refresh(experiment)

        logger.info(f"Created experiment {experiment.id}: {experiment.name}")
        return self._experiment_to_dict(experiment)
```

Everything between the first `add` and the `commit` is one transaction: if a metric
violates the unique `(experiment_id, name)` index, the `commit` raises and nothing is written.
The endpoint catches `(IntegrityError, DataError)`, rolls back so the session stays usable,
and answers **409** naming the key when the conflict is on a key the caller chose, or **400**
with a fixed sentence otherwise (the driver's text goes to the server log, never to the
client).

A generated key can also collide (it ends in a short random suffix). That case is retried
inside a savepoint, so a conflict rolls back only the attempt, not the session:

```python
# 📁 backend/app/services/experiment_service.py
    def _insert_with_generated_key(self, experiment: Experiment) -> None:
        for attempt in range(1, KEY_GENERATION_ATTEMPTS + 1):
            experiment.key = self.generate_key(experiment.name)
            try:
                with self.db.begin_nested():
                    self.db.add(experiment)
                    self.db.flush()  # Flush to get the experiment ID
                return
            except IntegrityError as exc:
                if attempt == KEY_GENERATION_ATTEMPTS or not (
                    is_experiment_key_conflict(exc)
                ):
                    raise
                ...
```

Without the savepoint, the first `IntegrityError` would leave the whole session in a failed
transaction and every retry would fail with it.

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

A no-request session with a savepoint per row — the experiment scheduler's tick, which
activates every experiment that is due:

```python
# 📁 backend/app/core/scheduler.py
            for experiment in experiments_to_activate:
                experiment_id = experiment.id
                savepoint = db.begin_nested()
                try:
                    ...
                    savepoint.commit()
                except Exception:
                    savepoint.rollback()
                    failed_count += 1
                    logger.exception("Error activating experiment %s", experiment_id)
                    continue
                ...
            # Commit all activation changes before checking for experiments to complete
            if activated_count > 0:
                db.commit()
                ...
        except Exception as e:
            failed_count += 1
            logger.error(f"Error processing scheduled experiments: {e!s}")
        finally:
            db.close()
```

The savepoint is what makes a batch job forgiving. One experiment the database refuses is rolled
back on its own and counted as failed; the others are still committed together by the
`db.commit()` after the loop. The outer `except` is for a failure outside any row (the query
itself), and `finally: db.close()` ends whatever transaction is left and returns the connection.
This whole method runs in a worker thread, not on the event loop (Hour 19).

And a write on the hottest path in the app, done as a race-safe Core `UPDATE` rather than
"load, set, commit". `get_api_key` calls it on every authenticated SDK request, so it writes at
most about once per key per resolution window, and repeats the staleness test in the
`WHERE` clause so two workers that race past the in-memory check change nothing:

```python
# 📁 backend/app/models/api_key.py
    def update_last_used(self, db_session, now: Optional[datetime] = None) -> bool:
        if now is None:
            # Naive UTC, like expires_at and is_expired.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
        cutoff = now - LAST_USED_RESOLUTION
        if self.last_used_at is not None and self.last_used_at > cutoff:
            return False

        cls = type(self)
        result = db_session.execute(
            update(cls)
            .where(
                cls.id == self.id,
                or_(cls.last_used_at.is_(None), cls.last_used_at <= cutoff),
            )
            .values(last_used_at=now, updated_at=cls.updated_at)
            .execution_options(synchronize_session=False)
        )
        db_session.commit()
        return bool(result.rowcount)
```

Three details are worth stealing: the conditional `WHERE` makes the write idempotent under
concurrency; `updated_at=cls.updated_at` pins the column to itself so the mixin's `onupdate`
doesn't record "using a key" as "editing it"; and `result.rowcount` tells the caller whether
a row was actually written.

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

> **By the Way.** `db.commit()` in a **service** is the house convention; endpoints mostly
> don't commit. That keeps transaction boundaries next to the logic that defines them and lets
> a service be reused from a scheduler or a script with the same semantics. The exceptions
> are deliberate: `create_experiment` writes a compliance-audit row after the service has
> committed, and commits that itself (the audit service only flushes). And after a service
> commits, endpoints invalidate caches.

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
- Services commit; endpoints don't; schedulers own their session (`try/…/finally: close()`), with
  a savepoint per row so one bad row is rolled back alone.
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

1. **Count, don't load.** The results engine needs _numbers_ per variant, so it asks the
   database for counts (`func.count`, or a `GROUP BY` that returns one row per user) rather
   than loading whole rows.
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

Look hard at the sort. `hasattr(Experiment, sort_by)` is *not* a whitelist: a mapped class
has plenty of attributes that are not sortable columns (`metadata`, `registry`, relationships
such as `variants`), and `hasattr` is true for all of them. When a parameter can only take a
handful of values, say so in its type and let FastAPI refuse anything else with a 422 before
the handler runs:

```python
from typing import Literal

SortField = Literal["created_at", "updated_at", "name", "status"]

def list_experiments(sort_by: SortField = Query("created_at"), ...):
    sort_field = getattr(Experiment, sort_by)   # only ever one of the four columns
```

That is the general rule for any user-supplied identifier — a column name, a sort key, a
table: map it through a closed set (a `Literal`, an `Enum`, or a dict from allowed names to
columns), never through `getattr` on a class guarded by `hasattr`.

The core counting queries — the Week 1 join, as SQLAlchemy. The denominator is assignments
per variant; the numerator is **converting users**, not conversion events:

```python
# 📁 backend/app/services/analysis_service.py
        # Get assignment counts per variant
        assignments = {}
        for variant_id in variant_ids:
            count = (
                self.db.query(func.count(Assignment.id))
                .filter(
                    Assignment.experiment_id == experiment.id,
                    Assignment.variant_id == variant_id,
                )
                .scalar()
                or 0
            )
            assignments[variant_id] = count

        # Converting users per variant (event_matching.py): a user with
        # several conversion events counts once, so conversions <= assignments.
        conversions = {}
        for variant_id in variant_ids:
            conversions[variant_id] = count_converting_users(
                self.db, experiment.id, variant_id, metric.event_name
            )
```

`count_converting_users` groups the matching events by user and keeps only users who are
actually assigned to the variant the event is tagged with:

```python
# 📁 backend/app/services/event_matching.py
def _converters(
    db: Session, experiment_id: Any, variant_id: Any, criterion: Any
) -> List[Tuple[str, Any]]:
    rows = (
        db.query(Event.user_id, func.min(Event.created_at))
        .filter(
            Event.experiment_id == experiment_id,
            Event.variant_id == variant_id,
            criterion,
        )
        .group_by(Event.user_id)
        .all()
    )
    return [(str(user_id), first) for user_id, first in rows]
```

A user who purchased three times counts once, and an event from a user with no assignment to
that variant does not count, so a conversion rate can never exceed 100%. Counting
`func.count(Event.id)` would answer a different question ("how many purchase events"), and
the rates built on it would be wrong.

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
> is what the codebase uses for per-variant assignment counts and it composes with `filter()`
> cleanly. Never load whole model rows to `len()` them; when you need distinct users, select
> just the user column with `GROUP BY` (as `_converters` does) or count
> `func.distinct(...)`.

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

Then, for a demo experiment, compare `func.count(Event.id)` with
`func.count(func.distinct(Event.user_id))` over the same `conversion_event_filter` — events
versus users — and then with `count_converting_users(db, exp.id, variant_id, "purchase")`,
which also drops users not assigned to the variant. The last number is the one `/results`
reports.

### Common mistakes

- **`==` against a Python enum member when the column stores a string** (or vice versa).
  Match the column's type; the flag service checks both for the legacy enum case.
- **Filtering on `None` with `== None`** — SQLAlchemy translates it to `IS NULL`, and
  linters complain; `.is_(None)` is explicit.
- **Interpolating a request value into `text()`.** Bound parameters only; the scan blocks it
  for a reason.
- **Loading rows to count them.** `func.count(...).scalar()`.
- **Counting events when you mean users.** A conversion rate's numerator is converting users
  (`count_converting_users`), never `func.count(Event.id)`.
- **Turning a user-supplied name into an attribute with `getattr`.** Declare the allowed
  values (`Literal`/`Enum`) on the parameter instead.

### Key takeaways

- Build with `query().filter().options().order_by().offset().limit()`; finish with
  `first/all/scalar/count`.
- Reusable criteria (`conversion_event_filter`) keep one rule in one place.
- Aggregates via `func`; `coalesce` so empty sets are 0.
- JSONB: `.contains([...])` (`@>`), `.has_key`, `["k"].astext`; `text()` only for dynamic
  paths, with bound values and marked, justified identifier interpolation.

---

## Hour 18 — Alembic: two heads, autogenerate, the bootstrap

### What & why

**Alembic** versions the schema as a graph of revision scripts. Each has a `revision` id, a
`down_revision` pointing to its parent, `upgrade()` and `downgrade()`. The database records
the revisions it is at in an `alembic_version` table (schema-qualified here). `upgrade`
applies what's missing; `revision --autogenerate` diffs the models' metadata against the
database and drafts a script.

Controlvar's graph has a shape most tutorials never show you: **two heads**.

- The **core chain** lives in `backend/app/db/migrations/versions/`.
- The **`modules` branch** lives in `modules/backend/app/db/migrations/versions/`. Its first
  revision, `modules_0001_rbac`, has `branch_labels = ("modules",)` and a `down_revision` on
  the core chain, so the branch forks off a real core revision and grows on its own.

`alembic.ini` lists both directories under `version_locations`, and alembic skips a listed
directory that does not exist. So a **full** checkout (with `modules/`) has two heads, and a
**core** checkout (no `modules/`) has one. That separation is the point: deleting `modules/`
leaves a consistent core chain behind.

Three consequences follow, and they are the whole of this Hour's day-to-day practice:

1. **Upgrade with `heads`, plural.** `upgrade heads` applies every branch. The singular form
   names one head, and with two present alembic refuses: *"Multiple head revisions are present
   for given argument 'head'; please specify a specific target revision, '<branchname>@head'
   to narrow to a specific head, or 'heads' for all heads"*. On a core checkout `heads` names
   the one head just the same, so `heads` is always right.
2. **A new revision names the head it extends.** A bare `revision --autogenerate` fails with
   *"Multiple heads are present; please specify the head revision on which the new revision
   should be based, or perform a merge."* Pass `--head <core head id>` for a core change, or
   `--head modules@head` for a module's; alembic writes the file beside the head it extends,
   so a modules revision lands in `modules/backend/app/db/migrations/versions/`.
3. **Never merge the two heads.** Alembic's own error message suggests a merge. Ignore it
   here (see the Watch Out below).

The second twist, met in the Build book: the historical chain **can't build a fresh
database** (several old autogenerated revisions re-create tables earlier ones created). So a
fresh database is built by `python -m backend.app.db.bootstrap` — the schema is created from
the models, then every head is stamped — and only _existing_ databases run `upgrade heads`.
Models are truth; migrations are the delta.

### In the wild

`env.py` builds the database URL from the same `POSTGRES_*` variables everything else uses,
and it has to be careful about it:

```python
# 📁 backend/app/db/migrations/env.py
from backend.app.db.schema import export_schema_name, metadata_for_schema
from backend.app.db.url import postgres_url as build_postgres_url

schema = export_schema_name()
...
from backend.app.models import register_core_models  # noqa: E402
from backend.app.modules_loader import require_modules_or_absent  # noqa: E402

Base = register_core_models()
# Strict on purpose: if the modules package is present but fails to register,
# target_metadata would hold only the core tables and autogenerate would
# propose dropping every module table. Better to refuse than to be quiet.
require_modules_or_absent()
...
target_metadata = metadata_for_schema(Base, schema)
...
postgres_url = build_postgres_url(
    user=_db_user,
    password=_db_password,
    host=_db_host,
    port=_db_port,
    database=db_name,
)
config.set_main_option("sqlalchemy.url", postgres_url.replace("%", "%%"))
```

Four lessons hide in those lines:

- **`build_postgres_url` percent-encodes the user and password.** A password containing `@`,
  `/` or `#` would otherwise break the URL. Then `.replace("%", "%%")` escapes the result
  *once more*, for this path only: alembic's config is a `configparser`, which reads `%` as
  interpolation, so an encoded `%23` would be refused. `%%` is configparser's own escape and
  is undone when the value is read back.
- **The models are registered explicitly** (`register_core_models()`, then the modules' own
  models through the seam), and a present-but-broken `modules` package is refused — because
  autogenerate compares against whatever is in `target_metadata`, and a missing table there
  is a proposed `drop_table`.
- **`metadata_for_schema(Base, schema)`** hands alembic the metadata as it reads on the
  schema this run resolved, so the metadata autogenerate compares and the schema it reflects
  are the same string by construction.
- **The comparison is scoped.** `context.configure(...)` passes `version_table_schema=schema`
  (the version table lives in the app schema), `include_schemas=True` (reflect that schema,
  not `public`), an `include_name` filter that keeps every *other* schema out, and an
  `include_object` filter that keeps the module tables out of a core-chain autogenerate.

The bootstrap's decision and its two branches:

```python
# 📁 backend/app/db/bootstrap.py
    if has_alembic_version(engine, schema):
        ...
        if may_run_alembic(cfg, recorded_revisions(engine, schema), schema):
            logger.info(
                "alembic_version present in schema %s: running upgrade heads", schema
            )
            command.upgrade(cfg, "heads")
        result = "upgraded"
        reconcile_with_models(engine, schema)
    else:
        logger.info(
            "Fresh database: creating schema %s from models and stamping heads",
            schema,
        )
        create_from_models(engine, schema)
        # POSTGRES_SCHEMA already names `schema` -- alembic_sees_schema().
        command.stamp(cfg, "heads")
        result = "created"

    ensure_first_superuser(engine, schema)
```

It runs under a PostgreSQL advisory lock, so several replicas starting at once queue rather
than race, and it creates the first administrator from `FIRST_SUPERUSER` /
`FIRST_SUPERUSER_PASSWORD` when the `users` table is empty. `reconcile_with_models` is what
makes a profile switch (a core database met by a full build) land on the same schema a fresh
full bootstrap builds; it creates missing **module** tables only, never a core table whose
migration somebody forgot to write.

A hand-finished migration in the house style — a docstring that says what and why, explicit
revision ids, the schema from the environment, and an **expand** step that the previous
release's image survives:

```python
# 📁 backend/app/db/migrations/versions/a89544fb1075_feature_flags_store_a_default_value.py
"""Feature flags: a ``default_value`` column, false on every row
...
The server default is load-bearing beyond that first fill.  The previous
release's image does not know the column, so its INSERTs do not name it; during
a deploy, and after an image-only rollback (``rollback.yml``), the database
supplies false.  Without the default those INSERTs fail on ``NOT NULL``.
...
Core chain, extending the core head ``d12cbd384bbe`` (generated with
``revision --autogenerate --head d12cbd384bbe``, then edited).  Nothing here
touches a module table, so a full checkout still has exactly two heads: this
revision and ``modules_0002_warehouse_analysis``.
...
"""
...
# revision identifiers, used by Alembic.
revision: str = "a89544fb1075"
down_revision: Union[str, None] = "d12cbd384bbe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The schema name comes from the deployment's own environment, never from a
# request.
_SCHEMA = os.environ.get("POSTGRES_SCHEMA", "experimentation")
...
def upgrade() -> None:
    if not _has_column():
        op.add_column(
            _TABLE,
            sa.Column(
                _COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            schema=_SCHEMA,
        )


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN, schema=_SCHEMA)
```

Note the `--head d12cbd384bbe` in its docstring, the guard that makes it a no-op on a schema
the bootstrap already built from the models, and the closing sentence: after this revision a
full checkout *still* has exactly two heads.

And the other branch's root, which is what makes the second head:

```python
# 📁 modules/backend/app/db/migrations/versions/modules_0001_rbac.py
# revision identifiers, used by Alembic.
revision: str = "modules_0001_rbac"
...
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = ("modules",)
...
depends_on: Union[str, Sequence[str], None] = None
```

The workflow, from the repository root:

```bash
export POSTGRES_DB=experimentation POSTGRES_SCHEMA=experimentation
ALEMBIC="python -m alembic -c backend/app/db/alembic.ini"

$ALEMBIC heads                                          # two in a full checkout: the core head and modules
$ALEMBIC current                                        # where is this database?
$ALEMBIC history | head                                 # the graph
$ALEMBIC revision --autogenerate --head <core head id> -m "add experiment decision_notes"
$ALEMBIC revision --autogenerate --head modules@head -m "..."   # a module's change instead
$EDITOR backend/app/db/migrations/versions/*decision_notes.py    # READ IT
$ALEMBIC upgrade heads
python -m backend.app.db.bootstrap                      # fresh DB: create from models + stamp heads; existing: upgrade heads
```

What to check in an autogenerated file, in order: (1) `down_revision` is the **id** of the
head you named (never a slug or filename), and the file sits beside it; (2) the `upgrade()`
contains _only_ your change — drop proposals for indexes or enum alterations you didn't touch
are drift, not your migration; (3) it is **backward-compatible**: new columns are nullable or
have a `server_default`, so the previous image keeps working during a deploy and after an
image-only rollback (expand now; contract — drop the old column — in a later release, once
nothing reads it); (4) the schema comes from the environment or a helper, not hard-coded;
(5) the `downgrade()` is at least plausible; (6) `alembic heads` still prints exactly two.

> **Watch Out!** Two heads is the normal state of a full checkout, not something to fix.
> Never run `alembic merge` on them, however the error message phrases it. The merge file
> would land in the **core** versions directory with `down_revision = (<core head>,
> 'modules_0001_rbac')`, and every core checkout — which has no `modules_0001_rbac` — would
> then fail to load the script directory at all. Use `upgrade heads`, and `--head` on a new
> revision.

> **Watch Out!** PostgreSQL enums: `ALTER TYPE ... ADD VALUE` can't run inside a transaction
> on older versions and can't be undone; removing or renaming a value needs a new type and a
> column cast. When autogenerate emits an enum change, stop and decide whether a `String` +
> `CheckConstraint` is the better column.

> **By the Way.** The unit tests build their schema with `create_all` from the models, but
> migrations are not left untested. `backend/tests/integration/database/` holds the
> transition tests — `test_profile_transitions.py` (a core database met by a full image and
> the reverse), `test_modules_0002_transitions.py`, `test_newer_database_rollback.py`,
> `test_alembic_branch_revisions.py` and one test per data-bearing migration such as
> `test_feature_flag_default_value_migration.py` — and `test_autogenerate_is_empty.py`, which
> bootstraps a fresh database for both profiles and asserts that `revision --autogenerate`
> against it proposes **nothing**. Any operation it proposes is either a migration somebody
> forgot to write or a comparison pointed at the wrong schema — and the second kind shows up
> as `op.drop_table`. On AWS, only the deploy's migration task writes the schema; the API
> tasks run with `RUN_MIGRATIONS=false`.

### Try It Yourself

Look at the graph and the version table:

```bash
python -m alembic -c backend/app/db/alembic.ini heads
python -m alembic -c backend/app/db/alembic.ini branches
docker compose exec postgres psql -U postgres -d experimentation -c "select * from experimentation.alembic_version"
ls backend/app/db/migrations/versions modules/backend/app/db/migrations/versions
```

`heads` prints two lines in a full checkout — the core head (`7b6b4b4fe246` at the commit this
book was checked against), and `modules_0004_rbac_activation (modules) (head)` — and
`alembic_version` holds one row per head. Compare the `down_revision` of `modules_0001_rbac` with `alembic history` to find the
core revision the branch forks from. Finally, find the revisions the
bootstrap docstring blames for the non-replayable chain
(`git grep -n "re-create" backend/app/db/bootstrap.py`).

### Common mistakes

- **`upgrade` against an empty database.** The historical chain can't replay from zero; use
  the bootstrap.
- **The singular `head`.** It works only by accident on a core checkout; `heads` always works.
- **A bare `revision --autogenerate`.** Name the head: `--head <core head id>` or
  `--head modules@head`.
- **Merging the heads.** Breaks every core checkout. Two heads is correct.
- **Committing an autogenerated migration unread.** Drift, enum changes and wrong
  `down_revision`s all come from this.
- **A `NOT NULL` column without a `server_default`** on a live table — the deploy's old image
  fails every insert until the new image is live, and again after an image-only rollback.
- **Running alembic from `backend/`** with the root-relative `-c` path (or vice versa). From
  the root: `-c backend/app/db/alembic.ini`.

### Key takeaways

- Two heads in a full checkout: the core chain and the `modules` branch
  (`branch_labels = ("modules",)`), in two `version_locations`. One in a core checkout.
- `upgrade heads`; `revision --autogenerate --head <core head id>` or `--head modules@head`;
  never merge.
- Fresh database → `python -m backend.app.db.bootstrap` (create from models + `stamp heads` +
  first admin); existing → `upgrade heads`.
- `env.py`: percent-encoded URL with `%%` for configparser, `metadata_for_schema`, scoped
  comparison (`include_schemas`, `include_name`, `include_object`).
- Every migration is backward-compatible (expand, then contract). Autogenerate against a fresh
  bootstrap must be empty, and the transition tests pin it.

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
`upgrade heads` — the incremental path, every branch (then it reconciles any missing module
tables). Only a database _without_ the version table is created from the models and gets
`stamp heads`. Plural both times: a full checkout has two heads, and alembic refuses the
singular `head` when there is more than one.

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

**E4.6 — Ship the migration.** Write the commands, from the repository root, that generate
and apply the core migration for E4.1 on a full checkout, and say what `alembic heads` should
print afterwards.

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

Plus, in `backend/app/models/__init__.py`, `"decision_log"` added to `CORE_MODEL_MODULES`
(it's a core model), so `register_core_models()` — and with it the bootstrap, `env.py` and the
test harness — sees the table.

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
autogenerate against an `upgrade`d database is empty — the same property
`test_autogenerate_is_empty.py` pins against a freshly bootstrapped one.

**S4.6**

```bash
ALEMBIC="python -m alembic -c backend/app/db/alembic.ini"
$ALEMBIC heads                     # note the core head id (the line without "(modules)")
$ALEMBIC revision --autogenerate --head <core head id> -m "add decision_logs"
$EDITOR backend/app/db/migrations/versions/*add_decision_logs.py   # read it: down_revision = the core head id
$ALEMBIC upgrade heads
$ALEMBIC heads                     # still two: the new revision and modules_0004_rbac_activation
```

`decision_logs` is a core table, so the revision extends the core head, not `modules@head`,
and lands in `backend/app/db/migrations/versions/`. `heads` still prints two lines; if it
prints three, the revision was based on the wrong parent. Never resolve that with a merge —
delete the file and regenerate it with the right `--head`.

---

**Next:** [Part V — Beyond Request/Response →](./part-5-beyond-requests.md)

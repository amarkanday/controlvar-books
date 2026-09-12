# Part III — Dependency Injection & Auth

### Hours 9–13

[← Part II](./part-2-pydantic.md) · [Contents](./README.md) · [Part IV →](./part-4-sqlalchemy.md)

`Depends` is the idea that separates FastAPI from "Flask with type hints." This Part starts
with the mechanism itself (Hour 9), then follows the three dependencies every Experimently
endpoint uses: the database session (Hour 10), the identity of the caller (Hour 11), and what
the caller may do (Hour 12). Hour 13 steps outside the dependency graph to the middleware that
wraps every request before any dependency runs. By the end you'll be able to read
`backend/app/api/deps.py` top to bottom and know why each function is shaped the way it is.

---

## Hour 9 — `Depends`: the mechanism (yield, caching, overrides)

### What & why

A **dependency** is a callable that FastAPI runs _before_ your path operation and whose
return value it injects as a parameter. `db: Session = Depends(get_db)` means "call `get_db`
(resolving _its_ parameters the same way), and pass the result as `db`." Dependencies can
depend on dependencies, so an endpoint's parameter list is really the root of a small tree
that FastAPI resolves per request.

Five properties make the mechanism worth understanding rather than cargo-culting:

1. **Dependencies are declared like any parameter** — path, query and body params of the
   dependency are pulled from the request too. That's how `get_experiment_by_key` reads
   `experiment_key` from the path without the endpoint mentioning it.
2. **`yield` dependencies run cleanup after the response.** `get_db` yields a session and
   closes it in `finally` — FastAPI runs that `finally` after the response is sent.
3. **A dependency is cached per request.** If `get_db` appears three times in a tree, it runs
   once and the same `Session` is injected everywhere. (Disable with
   `Depends(f, use_cache=False)`.)
4. **Async and sync mix.** An `async def` dependency runs on the event loop; a `def`
   dependency runs in a threadpool. FastAPI awaits or offloads as needed.
5. **They're overridable.** `app.dependency_overrides[get_db] = fake_get_db` swaps a
   dependency for the whole app — the seam the test harness uses.

#### Why dependency injection and not decorators or globals

The alternative for "every endpoint needs a session and a user" is a decorator
(`@login_required`) that stuffs things into a request object, or a global session. Both hide
what an endpoint needs. With `Depends`, the parameter list _is_ the requirements list, the
docs know which endpoints need auth (a lock icon), and a test can replace exactly one
requirement without monkeypatching modules.

### In the wild

The simplest dependency in the codebase — a `yield` dependency with cleanup:

```python
# 📁 backend/app/api/deps.py
async def get_db() -> Generator[Session, None, None]:
    """
    Get database session.

    Yields:
        Session: Database session
    """
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()
```

A dependency that reads a **path parameter** and takes an **optional argument with a default**:

```python
# 📁 backend/app/api/deps.py
def get_experiment_by_key(
    experiment_key: str,
    db: Session = Depends(get_db),
    required_status: Optional[ExperimentStatus] = None
) -> Experiment:
    """
    Get experiment by key or ID.
    """
    experiment = None

    # First try to lookup by ID (UUID)
    try:
        from uuid import UUID
        experiment_id = UUID(experiment_key)
        experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
    except (ValueError, TypeError):
        # Not a valid UUID, try the original lookup method
        pass

    # If not found by ID, try the original lookup method
    if not experiment:
        experiment = db.query(Experiment).filter(getattr(Experiment, "key", None) == experiment_key).first()

    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    # Check for required status if specified
    if required_status is not None and hasattr(experiment, 'status') and experiment.status != required_status:
        raise HTTPException(status_code=400, detail=f"Experiment not in {required_status.value} status")
    # Default check for active status (only if required_status is not specified)
    elif required_status is None and hasattr(experiment, 'status') and experiment.status != ExperimentStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Inactive experiment")

    return experiment
```

Two lessons hide in it. `experiment_key: str` with no default becomes a **query parameter**
when this is used as `Depends(get_experiment_by_key)` on a route without `{experiment_key}`
in its path — FastAPI resolves dependency parameters exactly as it would an endpoint's. And
`required_status` with a default `None` is _also_ a query parameter from FastAPI's point of
view; the endpoint that wants a different status can't pass it through `Depends` — which is
why `delete_experiment` does its checks inline (`CLAUDE.md` calls this out; Hour 12 returns
to it).

A dependency **chain** — the flag access check depends on the flag lookup, which depends on
the session:

```python
# 📁 backend/app/api/deps.py
async def get_feature_flag_by_key(key: str, db: Session = Depends(get_db)) -> FeatureFlag:
    """Get a feature flag by key."""
    feature_flag = db.query(FeatureFlag).filter(FeatureFlag.key == key).first()
    if not feature_flag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feature flag with key {key} not found")
    return feature_flag


async def get_feature_flag_access(
    feature_flag: FeatureFlag = Depends(get_feature_flag_by_key),
    current_user: User = Depends(get_current_user),
) -> FeatureFlag:
    ...
```

When an endpoint declares `Depends(get_feature_flag_access)`, FastAPI resolves `get_db` once
(cached), runs `get_feature_flag_by_key` with the `key` path parameter, runs
`get_current_user` with the bearer token, then `get_feature_flag_access` with both results.
The endpoint's own `db: Session = Depends(get_db)` gets the _same_ session — one per request.

The override, from the test harness:

```python
# 📁 backend/tests/conftest.py
    # Override the get_db dependency
    def override_get_db():
        try:
            yield db_session
        finally:
            pass
    ...
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[deps.get_current_user] = override_get_current_user
    app.dependency_overrides[deps.get_current_active_user] = override_get_current_active_user
```

The endpoint code doesn't change; the tree's leaves do. Note the override `finally: pass` —
the test session is owned by the fixture, not the request.

> **Did You Know?** `Depends()` with no argument uses the parameter's type annotation as the
> dependency — `commons: CommonParams = Depends()` calls `CommonParams(...)` with the
> request's parameters. The codebase uses `Paginator` (`core/pagination.py`) that way in some
> list endpoints: `paginator: Paginator = Depends()` pulls `skip`/`limit` from the query
> string and clamps `limit` to 500 in the constructor.

### Try It Yourself

Build a tiny tree in a scratch app and watch caching and cleanup:

```python
# scratch_deps.py — uvicorn scratch_deps:app --port 8010
from fastapi import Depends, FastAPI
calls = {"session": 0}

def get_session():
    calls["session"] += 1
    print("open session", calls["session"])
    try:
        yield f"session-{calls['session']}"
    finally:
        print("close session")

def get_user(session: str = Depends(get_session)):
    return {"user": "alice", "via": session}

app = FastAPI()

@app.get("/")
def root(session: str = Depends(get_session), user: dict = Depends(get_user)):
    return {"session": session, "user": user}          # same session object twice: one open, one close
```

`curl localhost:8010/` — one "open session", one "close session" (after the response), and
`user.via == session`. Then add `use_cache=False` to one of the `Depends(get_session)` and
watch it open twice.

### Common mistakes

- **Doing work after `yield` that must succeed.** Code after `yield` runs after the response
  is sent; it can't change the status code. Commit inside the handler, not in the dependency's
  cleanup.
- **Expecting a dependency parameter to be "passed" by the endpoint.** Dependency
  parameters come from the _request_ (or other dependencies), never from the endpoint's
  arguments.
- **Assuming a dependency runs per use.** It's cached per request; a counter or a "fresh"
  object needs `use_cache=False`.
- **Overriding the wrong object.** `dependency_overrides` is keyed by the exact function
  object the endpoint referenced (`deps.get_current_user`), not by name.

### Key takeaways

- `Depends(f)` runs `f` before the endpoint, resolving `f`'s parameters from the request and
  other dependencies; results are cached per request.
- `yield` dependencies clean up after the response.
- Trees compose: lookup → access check → endpoint, one session throughout.
- `app.dependency_overrides` swaps leaves for tests without touching endpoints.

---

## Hour 10 — The session dependency: `get_db`

### What & why

Every request that touches PostgreSQL gets **one SQLAlchemy `Session`**, opened by `get_db`
and closed after the response. That single rule has three consequences that shape how the
backend is written:

1. **Transactions are per request.** A handler that calls a service that does `db.commit()`
   commits; a handler that raises after that commit can't undo it. Services commit at
   well-defined points (the end of `create_experiment`, after inserting an assignment) and
   endpoints don't wrap them in their own transactions.
2. **Objects are alive only while the session is.** An ORM object returned from a handler is
   serialised _before_ `finally: db.close()` runs (FastAPI serialises the return value, then
   runs cleanup), which is why `ExperimentResponse.model_validate(experiment)` in the handler
   works and a lazy load after the response wouldn't.
3. **The session is the test seam.** The harness overrides `get_db` with a session bound to
   the per-process test database; endpoints that bypass `get_db` (importing `SessionLocal`)
   silently write to the _real_ database in tests.

#### Why `SessionLocal` exists at all, then

For code with no request: the five schedulers, the bootstrap, seeds, scripts. They own their
sessions explicitly — `db = SessionLocal(); try: ... finally: db.close()` — because there's no
dependency tree to do it for them.

### In the wild

The factory and the engine it binds to:

```python
# 📁 backend/app/db/session.py
engine = create_engine(
    database_uri,
    pool_pre_ping=True,  # Test connections before using them
    pool_size=20,  # Increased pool size
    max_overflow=20,  # Increased overflow
    pool_recycle=300,  # Recycle connections after 5 minutes
    pool_timeout=30,  # Wait up to 30 seconds for a connection
    connect_args={
        "connect_timeout": 10,  # Connection timeout in seconds
        "keepalives": 1,  # Enable keepalive
        "keepalives_idle": 30,  # Idle time before sending keepalive
        "keepalives_interval": 10,  # Interval between keepalives
        "keepalives_count": 5,  # Number of keepalive attempts
    }
)

# Session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
```

`autoflush=False` is the setting that matters most for service code: a query does _not_
flush pending inserts first, so a service that needs a freshly generated id must
`db.flush()` explicitly (Hour 16). The pool settings are for Aurora behind a load balancer —
`pool_pre_ping` detects a connection that died during a failover before handing it to a
request.

A service takes the session in its constructor and uses it for everything — never opening its
own:

```python
# 📁 backend/app/services/assignment_service.py
class AssignmentService:
    def __init__(self, db: Session):
        """Initialize with a database session."""
        self.db = db
        self.event_service = EventService(db)
        self.rules_evaluation_service = RulesEvaluationService()
        self.global_holdout_service = GlobalHoldoutService(db)
        self.mutual_exclusion_service = MutualExclusionService(db)
```

Composition passes the _same_ session down: `AssignmentService(db)` builds `EventService(db)`,
so an assignment and its exposure event are written in one transaction, and the test's
override session sees both.

The schedulers, with no request, own theirs:

```python
# 📁 backend/app/core/scheduler.py
        # Use a new database session for this task
        db = SessionLocal()
        try:
            current_time = datetime.now(timezone.utc)
            experiments_to_activate = db.query(Experiment).filter(...).all()
            ...
            db.commit()
        except Exception as e:
            logger.error(f"Error processing scheduled experiments: {str(e)}")
            db.rollback()
        finally:
            db.close()
```

And the WebSocket handler shows the subtle case — a dependency-provided session used only
for authentication, then released early so a long-lived socket doesn't pin a pooled
connection:

```python
# 📁 backend/app/api/v1/endpoints/websocket_results.py
    if not await run_in_threadpool(_authenticate_websocket, websocket, token, db):
        await websocket.accept(subprotocol=subprotocol)
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED, reason="Unauthorized")
        return
    # The session was only needed for authentication; release its connection
    # now rather than holding it for the lifetime of the socket.
    db.close()
```

> **Watch Out!** `get_db` is declared `async def` but yields a _synchronous_ session; the
> queries inside an `async def` handler therefore block the event loop for their duration.
> The platform accepts this (short queries, one worker per container, containers scale out).
> A `def` handler is run in a threadpool automatically, which is why `results.py`'s endpoints
> are plain `def`. Whichever you choose, be consistent within a module and never `await`
> inside a `def`.

> **By the Way.** In tests the override session is created with `expire_on_commit=False`.
> Without it, reading `user.is_superuser` after a `commit()` would trigger a refresh
> `SELECT` on a `NullPool` connection that may already be gone — the "server closed the
> connection unexpectedly" cascade the conftest docstring describes.

### Try It Yourself

Prove the one-session-per-request rule and the cleanup timing:

```bash
# how many endpoints declare the dependency vs import the factory directly?
git grep -n "Depends(deps.get_db)\|Depends(get_db)" backend/app/api | wc -l
git grep -n "SessionLocal()" backend/app/api backend/app/services | head      # should be rare in these layers
git grep -n "SessionLocal()" backend/app/core | head                            # schedulers: expected
```

Then, in the scratch app from Hour 9, replace the fake session with a real `SessionLocal()`
and `print(engine.pool.status())` before and after a request to watch a connection check out
and back in.

### Common mistakes

- **`SessionLocal()` inside an endpoint or a service method.** Bypasses the request
  transaction and the test override; use the injected `db`.
- **Committing in a `yield` dependency's cleanup.** Too late to report failure; commit in
  the service.
- **Returning ORM objects and accessing lazy relationships later** (in a background task,
  after the response). The session is closed; you'll get `DetachedInstanceError`.
- **Sharing a session across threads/tasks.** Sessions aren't thread-safe; the schedulers
  each open their own.

### Key takeaways

- One `Session` per request from `get_db`; services receive it, never open their own;
  cleanup after the response.
- `autoflush=False` → explicit `flush()` when you need a generated id.
- No-request code (schedulers, bootstrap, scripts) owns its session with `try/finally`.
- The session is the test seam; bypassing `get_db` means writing to the real database in
  tests.

---

## Hour 11 — Authentication dependencies: bearer, API key, `auto_error=False`

### What & why

Authentication is a dependency that turns a credential into a `User` — or raises 401. FastAPI
provides **security schemes** (`OAuth2PasswordBearer`, `APIKeyHeader`, `HTTPBearer`) that
extract the credential from the request _and_ register themselves in the OpenAPI document,
so the docs show which endpoints need which credential and the "Authorize" button works.

Experimently has two schemes and one dispatcher:

- `oauth2_scheme = OAuth2PasswordBearer(tokenUrl=".../auth/token", auto_error=False)` reads
  `Authorization: Bearer <token>`; `get_current_user` resolves it (dev bypass → local JWT →
  Cognito).
- `API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)` reads the SDK header;
  `get_api_key` resolves it to the key's owner.

#### Why `auto_error=False`

By default a scheme raises 401 itself when the credential is missing. Both of Experimently's
schemes set `auto_error=False` so a _missing_ credential reaches the dependency, which decides
what missing means: for the bearer scheme, "is the dev bypass active?" before "401"; for the
API-key scheme, a 401 with a `WWW-Authenticate: APIKey` header the SDKs recognise. The
scheme extracts; the dependency decides.

### In the wild

The scheme, with the reason in its comment:

```python
# 📁 backend/app/core/security.py
# OAuth2 password bearer scheme.  ``auto_error=False`` so that the absence of
# a token reaches ``deps.get_current_user``, which decides between the
# dev-admin bypass and a 401.  The tokenUrl keeps Swagger's "Authorize" button
# working with both providers (the /auth/token form accepts email+password
# when AUTH_PROVIDER=local).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/token", auto_error=False)
```

The dispatcher, trimmed to its decision structure:

```python
# 📁 backend/app/api/deps.py
def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """
    Resolution order:

    1. Dev-admin bypass (``DEV_AUTH_BYPASS=true`` **and** ``ENVIRONMENT`` in
       development/test): returns the synthetic ``dev-admin`` superuser.
    2. No token: 401.
    3. ``AUTH_PROVIDER=local``: decode the HS256 JWT issued by
       ``/api/v1/auth/login`` and load the user by id (401 on any failure,
       400 if the account is inactive).
    4. ``AUTH_PROVIDER=cognito``: validate the token with Cognito and sync the
       user's role from its groups (unchanged legacy path).
    """
    if dev_auth_bypass_active():
        try:
            return _get_or_create_dev_user(db)
        except Exception as dev_err:
            raise HTTPException(status_code=500, detail=f"Dev auth error: {dev_err}")

    if not token:
        raise _credentials_exception("Not authenticated")

    if settings.AUTH_PROVIDER == "local":
        return _authenticate_local_token(token, db)

    try:
        user_data = auth_service.get_user_with_groups(token)
        ...
        return user
    except Exception as e:
        logger.error(f"Authentication error: {str(e)}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials",
                            headers={"WWW-Authenticate": "Bearer"})
```

Note `token: Optional[str]` — the scheme returns `None` when the header is absent because of
`auto_error=False`. The local branch is a pure function of the token and the database:

```python
# 📁 backend/app/api/deps.py
def _authenticate_local_token(token: str, db: Session) -> User:
    """
    Resolve a locally issued JWT to its ``User`` row.

    401 for any decode/lookup failure, 400 when the account is inactive.
    """
    try:
        claims = decode_local_token(token)
        user_id = UUID(str(claims.get("sub")))
    except (InvalidTokenError, ValueError, TypeError) as exc:
        logger.debug(f"Local token rejected: {exc}")
        raise _credentials_exception()

    try:
        user = db.query(User).filter(User.id == user_id).first()
    except Exception as exc:  # pragma: no cover - defensive; DB errors are not auth errors
        logger.error(f"User lookup failed during local token auth: {exc}")
        raise _credentials_exception()

    if user is None:
        raise _credentials_exception()
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user
```

and `decode_local_token` pins the algorithm, the issuer and the required claims — the three
checks that separate "a JWT library call" from "authentication":

```python
# 📁 backend/app/core/security.py
def decode_local_token(token: str) -> Dict[str, Any]:
    if not token or not isinstance(token, str):
        raise InvalidTokenError("Token missing")
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[LOCAL_TOKEN_ALGORITHM],
            issuer=LOCAL_TOKEN_ISSUER,
            options={"require": ["sub", "exp", "iat", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
```

Two thin dependencies stack on `get_current_user` and are what most endpoints actually use:

```python
# 📁 backend/app/api/deps.py
def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def get_current_superuser(current_user: User = Depends(get_current_active_user)) -> User:
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return current_user
```

The API-key path is separate on purpose: SDK endpoints declare `Depends(deps.get_api_key)`
and never accept a bearer; dashboard endpoints declare `get_current_active_user` and never
accept a key:

```python
# 📁 backend/app/api/deps.py
def get_api_key(db: Session = Depends(get_db), api_key_header: str = Depends(API_KEY_HEADER)) -> Optional[User]:
    if not api_key_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key missing",
                            headers={"WWW-Authenticate": "APIKey"})

    # Look up by hashed key only. API keys must never be stored as plaintext.
    api_key_hash = hash_api_key(api_key_header)
    api_key = db.query(APIKey).filter(APIKey.key == api_key_hash).first()
    if not api_key or not api_key.is_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key",
                            headers={"WWW-Authenticate": "APIKey"})

    user = db.query(User).filter(User.id == api_key.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key",
                            headers={"WWW-Authenticate": "APIKey"})

    return user
```

The dev bypass is the one place a dependency returns an identity without a credential, and
it's guarded twice — here at call time and in `Settings` at startup:

```python
# 📁 backend/app/api/deps.py
def dev_auth_bypass_active() -> bool:
    """
    True only when the dev-admin bypass is enabled *and* permitted.

    Both conditions are read from ``settings`` at call time so tests can
    toggle them, and both must hold: ``DEV_AUTH_BYPASS`` must be exactly
    ``True`` and ``ENVIRONMENT`` must be ``development`` or ``test``.  No
    other environment variable (``TESTING``, ``DEBUG`` ...) unlocks it.
    """
    return (
        settings.DEV_AUTH_BYPASS is True
        and settings.ENVIRONMENT in BYPASS_ALLOWED_ENVIRONMENTS
    )
```

> **Watch Out!** `core/security.py::decode_token` returns constant stub claims and raises
> unless `TESTING` is set. It exists so legacy tests can monkeypatch it. Never call it for
> validation; use `decode_local_token` (local) or `CognitoAuthService` (Cognito). If you see
> `decode_token` in new code, that's a review comment.

> **Did You Know?** The WebSocket endpoint can't use `Depends(oauth2_scheme)` because
> browsers can't set headers on a WebSocket handshake. It extracts the token from a
> `Sec-WebSocket-Protocol` entry (`experimently.bearer, <token>`), a bearer header, or
> `?token=`, and then calls the _same_ `deps.get_current_user(token=..., db=...)` directly —
> dependencies are plain functions, so you can call them by hand when the framework can't.

### Try It Yourself

Send each credential to the wrong door and read the `WWW-Authenticate` header:

```bash
curl -si $API/experiments/ | grep -i "HTTP/\|www-authenticate"                 # no credential → 401 Bearer
curl -si $API/experiments/ -H "X-API-Key: $KEY" | grep -i "HTTP/\|www-authenticate"   # key on a bearer route → 401
curl -si -X POST $API/tracking/assign -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"experiment_key":"x","user_id":"u"}' | grep -i "HTTP/\|www-authenticate"   # bearer on a key route → 401 APIKey
```

Then decode your token's claims without verifying (to _see_ `iss`, `sub`, `exp`; never do
this in code): `python -c "import jwt; print(jwt.decode('$TOKEN', options={'verify_signature': False}))"`.

### Common mistakes

- **`auto_error=True` (the default) on a scheme where "missing" needs a decision.** The
  scheme 401s before your dependency runs.
- **Accepting both bearer and API key on one endpoint "for convenience."** The two carry
  different trust and different rate limits; keep the doors separate.
- **Verifying a JWT without pinning `algorithms` and `issuer`.** `alg: none` and cross-service
  replay are the classic failures.
- **Storing API keys in plaintext.** Hash with SHA-256 (fast, one-way); bcrypt is for
  passwords (deliberately slow).

### Key takeaways

- Security schemes extract credentials and document themselves; dependencies decide.
- `auto_error=False` lets "missing" reach `get_current_user` (bypass or 401) and
  `get_api_key` (401 with `APIKey`).
- `get_current_user`: bypass → no token → local JWT (pinned alg/issuer/claims) → Cognito
  (JIT-provisioned).
- `get_current_active_user` / `get_current_superuser` stack on it; `get_api_key` is a separate
  door for SDKs.
- Dependencies are functions — the WebSocket handler calls them by hand.

---

## Hour 12 — Authorization dependencies: composing checks, sync vs async

### What & why

Authorization dependencies take an authenticated user and a resource and decide _may they_.
In this codebase they compose three primitives from `core/permissions.py`:

- `check_permission(user, ResourceType, Action)` — superuser shortcut, then the role matrix;
- `check_ownership(user, obj)` — `obj.owner_id == user.id`;
- `get_permission_error_message(...)` — the sentence the 403 carries.

Two shapes appear, and `CLAUDE.md` is emphatic about not mixing them:

- **Experiment checks are synchronous functions** called from the endpoint body:
  `experiment = deps.get_experiment_access(experiment, current_user)`.
- **Feature-flag and report checks are `async` dependencies** declared with `Depends`:
  `_: bool = Depends(deps.can_update_feature_flag)`.

Both work; both are tested differently (`await` in async tests); the codebase's rule is to
follow the existing shape per resource so the tests and the dependency graph stay coherent.

#### Why READ before UPDATE, in the graph

`can_update_feature_flag` depends on `get_feature_flag_access` (which checks READ and
ownership), so the order READ → UPDATE is enforced by the dependency tree, not by discipline.
The user who can't read gets "you can't view feature flags", never "you can't update" — a
message that would confirm the resource exists.

### In the wild

The sync experiment check and the async flag chain, side by side:

```python
# 📁 backend/app/api/deps.py
def get_experiment_access(
    experiment: Union[Experiment, Dict[str, Any]],
    current_user: User = Depends(get_current_active_user)
) -> Union[Experiment, Dict[str, Any]]:
    """
    Called directly from endpoints (positional `experiment` arg). For the
    dependency-injection variant that fetches the experiment from a path
    parameter, callers should use `Depends(get_experiment_by_key)` and pass
    the resolved experiment to this function.
    """
    # Superusers always have access
    if current_user.is_superuser:
        return experiment

    # Check if user has permission to read experiments
    if not check_permission(current_user, ResourceType.EXPERIMENT, Action.READ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=get_permission_error_message(ResourceType.EXPERIMENT, Action.READ))

    # Check ownership for non-admin users for modification actions
    if not check_permission(current_user, ResourceType.EXPERIMENT, Action.UPDATE) and not check_ownership(current_user, experiment):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="You don't have permission to access this experiment")

    return experiment
```

```python
# 📁 backend/app/api/deps.py
async def can_create_feature_flag(current_user: User = Depends(get_current_user)) -> bool:
    """Check if user can create a feature flag."""
    if not check_permission(current_user, ResourceType.FEATURE_FLAG, Action.CREATE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=get_permission_error_message(ResourceType.FEATURE_FLAG, Action.CREATE))
    return True


async def can_update_feature_flag(
    feature_flag: FeatureFlag = Depends(get_feature_flag_access),
    current_user: User = Depends(get_current_user),
) -> bool:
    """Check if user can update a feature flag."""
    if not check_permission(current_user, ResourceType.FEATURE_FLAG, Action.UPDATE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail=get_permission_error_message(ResourceType.FEATURE_FLAG, Action.UPDATE))
    return True
```

The primitives they compose:

```python
# 📁 backend/app/core/permissions.py
def check_permission(user: Any, resource: ResourceType, action: Action) -> bool:
    # Superusers always have all permissions
    if hasattr(user, 'is_superuser') and user.is_superuser:
        return True

    # Get user's role
    try:
        role = getattr(user, 'role', UserRole.VIEWER)
    except (AttributeError, TypeError):
        # Default to Viewer role if there's any issue
        role = UserRole.VIEWER

    # Check role permissions
    return has_permission(role, resource, action)


def check_ownership(user: Any, resource_obj: Any) -> bool:
    if hasattr(resource_obj, 'owner_id') and hasattr(user, 'id'):
        return str(resource_obj.owner_id) == str(user.id)
    return False
```

Both are deliberately tolerant of mocks (`hasattr`, `getattr` with a `VIEWER` default,
string comparison of ids) — a test can pass a `MagicMock` user, and a malformed row degrades
to least privilege rather than raising.

The endpoint that _can't_ use a dependency — because the shared lookup enforces `ACTIVE` and
delete requires `DRAFT` — and does the checks inline, with a second factor:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
async def delete_experiment(
    experiment_id: UUID = Path(..., description="The ID of the experiment to delete"),
    experiment_key: str = Query(..., description="Key or ID of the experiment to delete (must match experiment_id)"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    cache_control: Dict[str, Any] = Depends(deps.get_cache_control),
) -> None:
```

> **Watch Out!** Mixing the shapes is the classic review catch: `await
> deps.get_experiment_access(...)` raises `TypeError: object Experiment can't be used in
> 'await' expression`; forgetting `await` on an async dep called by hand returns a coroutine
> that's never run (the check silently passes). In endpoints, use `Depends(...)` for the async
> ones and a direct call for the sync ones; in tests, `@pytest.mark.asyncio` + `await` for the
> async ones.

> **By the Way.** `ResourceType.API_KEY` was added in the open-core work: every user manages
> their _own_ keys freely, and the resource governs acting on _other_ users' keys (listing all,
> deleting someone else's), which only `ADMIN` may do. It's a small example of the matrix
> growing by adding a row, not by special-casing an endpoint.

### Try It Yourself

Read the graph for one flag endpoint, then break it:

```bash
grep -n "Depends(deps.can_update_feature_flag)\|Depends(deps.get_feature_flag_access)" backend/app/api/v1/endpoints/feature_flags.py | head
```

In a Python shell, run the async check with mocks the way the tests do:

```python
import asyncio
from unittest.mock import MagicMock
from backend.app.api import deps
from backend.app.models.user import UserRole
viewer = MagicMock(is_superuser=False, role=UserRole.VIEWER, id="u1")
flag = MagicMock(owner_id="someone-else")
try:
    asyncio.run(deps.can_update_feature_flag(feature_flag=flag, current_user=viewer))
except Exception as e:
    print(type(e).__name__, e.status_code, e.detail)      # HTTPException 403 ...
dev = MagicMock(is_superuser=False, role=UserRole.DEVELOPER, id="u2")
print(asyncio.run(deps.can_update_feature_flag(feature_flag=flag, current_user=dev)))   # True
```

(Calling the async dep by hand bypasses `get_feature_flag_access`; to see READ-before-UPDATE,
call `get_feature_flag_access` first with the viewer and watch the READ message.)

### Common mistakes

- **Checking UPDATE before READ**, leaking existence and giving the wrong message.
- **A new resource type checked with ad-hoc `if user.role == ...`** instead of the matrix —
  now there are two sources of truth.
- **Using the shared lookup dependency for an endpoint with a different status
  requirement** (the delete case) — thread the requirement or check inline, as the repo does.
- **Assuming `ADMIN` role ⇒ superuser.** They're different bits; `get_current_superuser`
  checks only the bit.

### Key takeaways

- Authorization = `check_permission` (superuser, then role × resource × action) +
  `check_ownership`, composed in dependencies.
- Experiment checks are sync functions; flag/report checks are `async` `Depends` — follow the
  existing shape per resource.
- The dependency tree enforces READ before UPDATE for flags.
- When a shared dependency's assumptions don't fit (delete requires `DRAFT`), check inline
  and say why.

---

## Hour 13 — Middleware: the ASGI onion and why order is stated

### What & why

Middleware wraps the whole application: every request passes through it on the way in and
every response on the way out, before any router or dependency is involved. In Starlette the
easy form is `BaseHTTPMiddleware` — subclass it, implement `async def dispatch(self, request,
call_next)`, do something before `await call_next(request)`, something after, return the
response.

The one fact everybody gets wrong at least once: **`add_middleware` wraps the current app,
so the last one added is the outermost.** Registration order and execution order are
reversed. `main.py` says so in a comment and then registers in an order chosen for a reason:

```
registration:  CORS → SecurityHeaders → RateLimit → Prometheus → RequestID
execution:     RequestID → Prometheus → RateLimit → SecurityHeaders → CORS → router
```

- **RequestID outermost**: the id is bound into the log context before anything logs, and
  stamped on every response — including a 429 from the rate limiter inside it.
- **Prometheus** next: it times everything below it, rejections included.
- **RateLimit** above the router: a rejected request never reaches a handler or opens a
  session.
- **SecurityHeaders**, **CORS** innermost: they decorate the real response.

#### Why not put cross-cutting logic in dependencies

A dependency runs only for endpoints that declare it and only after routing. A request id
must exist for 404s too; rate limiting must reject before routing; CORS preflights are
`OPTIONS` requests no endpoint declares. Middleware is for "every request"; dependencies are
for "this endpoint needs".

### In the wild

The smallest one — a header in, a header out, and a `ContextVar` bound for the logger:

```python
# 📁 backend/app/middleware/request_id_middleware.py
class RequestIDMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that ensures every request has a unique ID."""

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id: str = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Bind to the current async context so all log lines in this request
        # automatically include request_id, path, and method.
        bind_log_context(request_id=request_id, path=request.url.path, method=request.method)

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

Timing with **label normalisation** — the detail that keeps Prometheus from exploding on
UUIDs in paths:

```python
# 📁 backend/app/middleware/prometheus_metrics_middleware.py
# Matches UUID v4 segments in URL paths
_UUID_PATTERN: re.Pattern = re.compile(
    r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE,
)
# Matches purely-numeric path segments (e.g. /users/42)
_NUMERIC_PATTERN: re.Pattern = re.compile(r"/\d+")


def normalize_path(path: str) -> str:
    """Replace dynamic path segments with ``{id}`` for stable metric labels."""
    path = _UUID_PATTERN.sub("/{id}", path)
    path = _NUMERIC_PATTERN.sub("/{id}", path)
    return path


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start_time: float = time.time()
        response: Response = await call_next(request)
        duration: float = time.time() - start_time
        endpoint: str = normalize_path(request.url.path)
        record_request(method=request.method, endpoint=endpoint, status_code=response.status_code, duration=duration)
        return response
```

A middleware that **short-circuits** — returns its own response without calling `call_next`:

```python
# 📁 backend/app/middleware/rate_limiter.py
class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, enabled: bool = True) -> None:
        super().__init__(app)
        self._enabled = enabled
        ...

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._enabled:
            return await call_next(request)

        path = request.url.path
        client_ip = _get_client_ip(request)

        # Determine the applicable limit for this path
        limit, window = resolve_rate_limit(path, self._sdk_limit)

        rate_key = f"{client_ip}:{path}"
        allowed, remaining = self._limiter.is_allowed(rate_key, limit, window)
        ...
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests. Please slow down and retry after a moment."},
                headers={"Retry-After": str(window), "X-RateLimit-Limit": str(limit),
                         "X-RateLimit-Remaining": "0", "X-RateLimit-Window": str(window)},
            )

        response = await call_next(request)

        # Attach informational rate-limit headers to successful responses
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Window"] = str(window)

        return response
```

Notice it's constructed with an argument — `app.add_middleware(RateLimitMiddleware,
enabled=_rate_limit_enabled)` — and that the 429 body uses the same `{"detail": ...}` shape
as `HTTPException`, so clients don't need a special case. The response headers on success
let SDKs back off _before_ they're rejected.

The security-headers middleware is pure decoration with one environment-aware line:

```python
# 📁 backend/app/middleware/security_middleware.py
        # HSTS — only in non-dev environments to avoid local HTTPS issues.
        # "dev" is the legacy spelling of "development" (see core/config.py).
        if settings.ENVIRONMENT not in ("development", "dev"):
            response.headers["Strict-Transport-Security"] = ("max-age=31536000; includeSubDomains; preload")

        # Content Security Policy — strict for an API-only service
        # No scripts, no styles, no frames; only direct API responses
        response.headers["Content-Security-Policy"] = ("default-src 'none'; frame-ancestors 'none'")
```

And CORS, which is Starlette's own, configured to exactly what the API needs — including the
header the browser SDKs send:

```python
# 📁 backend/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)
```

`expose_headers` is the part people forget: a browser script can only read response headers
the server exposes, so without it the React SDK couldn't see `X-RateLimit-Remaining`.

> **Watch Out!** `BaseHTTPMiddleware` buffers streaming responses in older Starlette versions
> and has known interactions with `BackgroundTasks` and `contextvars`; the request-id
> middleware's `ContextVar` works because the binding happens inside `dispatch` on the same
> task that runs the endpoint. For high-throughput paths a pure ASGI middleware
> (`async def __call__(self, scope, receive, send)`) is lighter; the repo uses
> `BaseHTTPMiddleware` everywhere for readability.

> **Did You Know?** `main.py` imports `LoggingMiddleware`, `ErrorMiddleware` and
> `MetricsMiddleware` but registers none of them — they're the pre-EP-013 generation,
> superseded by structlog + `RequestIDMiddleware` + `PrometheusMetricsMiddleware`. The imports
> are dead code kept for tests that reference them; don't wire them back in without reading
> `middleware/error_middleware.py` first (it talks to CloudWatch).

### Try It Yourself

See the onion from outside:

```bash
# every response: request id (outermost), rate-limit headers (inside), security headers (inner)
curl -si $API/experiments/ -H "Authorization: Bearer $TOKEN" | grep -iE "x-request-id|x-ratelimit|x-content-type|content-security|strict-transport"
# a 429 still carries X-Request-ID (RequestID wraps RateLimit) but no security headers were added by an inner layer?
# (they are — SecurityHeaders is *inside* RateLimit, so a 429 returned by RateLimit skips it. Check:)
for i in $(seq 1 12); do curl -s -o /dev/null -X POST $API/auth/login -H 'Content-Type: application/json' -d '{"email":"a@b","password":"c"}'; done
curl -si -X POST $API/auth/login -H 'Content-Type: application/json' -d '{"email":"a@b","password":"c"}' | grep -iE "HTTP/|x-request-id|x-content-type|retry-after"
```

That last check is the most instructive thing in this Hour: a 429 has an `X-Request-ID`
(outer layer) and no `X-Content-Type-Options` (inner layer never ran). Order is observable.

### Common mistakes

- **Assuming registration order is execution order.** It's reversed; the file's comment is
  there because someone got it wrong.
- **Doing per-endpoint logic in middleware** (checking a role by path prefix). That's what
  dependencies are for.
- **Forgetting `expose_headers`** for headers a browser must read.
- **Short-circuiting with a body shape different from `{"detail": ...}`.**

### Key takeaways

- Middleware wraps everything; `dispatch(request, call_next)`; the last registered is the
  outermost.
- Experimently's order — RequestID → Prometheus → RateLimit → SecurityHeaders → CORS → router
  — is chosen so ids and metrics cover rejections and the router never sees rejected requests.
- Normalise labels (UUIDs → `{id}`) before recording metrics.
- Short-circuit responses use the same `{"detail": ...}` shape; success responses carry
  informational rate-limit headers; CORS must `expose_headers` for browsers to read them.

---

## Part III Workshop

**Q1. An endpoint declares `db: Session = Depends(get_db)` and also
`flag: FeatureFlag = Depends(get_feature_flag_access)`. How many sessions are opened?**
One. `get_db` is cached per request; the chain under `get_feature_flag_access` and the
endpoint's own parameter receive the same session.

**Q2. Why is `get_experiment_by_key`'s `required_status` effectively unusable through
`Depends`?**
Dependency parameters are resolved from the request, so `required_status` becomes an
optional query parameter — an endpoint can't pass a value to it. That's why
`delete_experiment` checks `DRAFT` inline.

**Q3. What does `auto_error=False` change for a request with no `Authorization` header?**
The scheme returns `None` instead of raising 401, so `get_current_user` runs and can return
the dev-bypass user (development/test only) or raise its own 401.

**Q4. A `VIEWER` calls `PUT /feature-flags/{key}` on a flag they own. Result?**
403 from `get_feature_flag_access`'s ownership branch? No — ownership _passes_ for their own
flag, so `get_feature_flag_access` returns the flag; then `can_update_feature_flag` checks
UPDATE by role, which `VIEWER` lacks → 403 "You don't have permission to update feature
flags". Ownership grants access to read/modify _through_ the access check, but the explicit
UPDATE dependency still requires the role.

**Q5. In what order do the five middleware run for a request, and which layer produces a
429?**
RequestID → Prometheus → RateLimit → SecurityHeaders → CORS → router. The 429 comes from
RateLimit, so it carries an `X-Request-ID` (outer) and is timed by Prometheus, but never gets
the security headers (inner).

**Q6. Why does the WebSocket handler call `deps.get_current_user(token=..., db=...)`
directly?**
Browsers can't set headers on a WebSocket handshake, so `Depends(oauth2_scheme)` can't
extract the token; the handler extracts it itself (subprotocol, header, or `?token=`) and
calls the dependency as a plain function.

**Quiz.** `app.dependency_overrides[deps.get_db] = fake` is set, but an endpoint imports
`from backend.app.db.session import get_db` and declares `Depends(get_db)`. Is it overridden?
→ Only if it's the same function object. `deps.get_db` and `db.session.get_db` are two
different functions in this repo; endpoints must use `deps.get_db` (the harness overrides
both to be safe — check `conftest.py`).

---

## Exercises

**E3.1 — A yield dependency.** Write `get_audit_context()` that yields a dict
`{"request_id": ..., "started": ...}` and logs the elapsed time in `finally`. Where does the
elapsed-time log line end up relative to the response — before or after the client sees it?

**E3.2 — A parameterised lookup.** Write an async dependency `get_workspace_by_slug(slug: str,
db = Depends(get_db))` that 404s when missing, and an access dependency
`get_workspace_access` that chains on it and on `get_current_user`, allowing superusers,
members, or ADMINs.

**E3.3 — Sync vs async.** Given `def get_experiment_access(...)` and `async def
can_update_feature_flag(...)`, write the two lines that use each correctly inside an `async
def` endpoint, and the two test-function signatures that exercise each.

**E3.4 — Order the onion.** A new `TenantMiddleware` must read a `X-Workspace` header and
bind `workspace_id` into the log context, and must apply to rate-limited (429) responses'
logs too. Where do you register it, and why?

**E3.5 — A scoped API key.** Sketch a `get_api_key_with_scope(scope: str)` dependency
_factory_ that returns a dependency requiring the key's `scopes` to include `scope`, and
show its use on `POST /tracking/errors`.

---

## Solutions

**S3.1**

```python
async def get_audit_context(request: Request) -> Generator[dict, None, None]:
    ctx = {"request_id": request.headers.get("X-Request-ID"), "started": time.perf_counter()}
    try:
        yield ctx
    finally:
        logger.info("request finished", extra={"elapsed_ms": (time.perf_counter() - ctx["started"]) * 1000})
```

The `finally` runs **after** the response has been sent — cleanup of `yield` dependencies is
post-response — so the log line is written after the client already has its bytes. That's
fine for logging and wrong for anything that must influence the status code.

**S3.2**

```python
async def get_workspace_by_slug(slug: str, db: Session = Depends(get_db)) -> Workspace:
    workspace = db.query(Workspace).filter(Workspace.slug == slug).first()
    if not workspace:
        raise HTTPException(status_code=404, detail=f"Workspace '{slug}' not found")
    return workspace


async def get_workspace_access(
    workspace: Workspace = Depends(get_workspace_by_slug),
    current_user: User = Depends(get_current_user),
) -> Workspace:
    if current_user.is_superuser or current_user.role == UserRole.ADMIN:
        return workspace
    if any(m.user_id == current_user.id for m in workspace.members):
        return workspace
    raise HTTPException(status_code=403, detail="You are not a member of this workspace")
```

`slug` becomes the path parameter when the route is `/workspaces/{slug}/...`.

**S3.3**

```python
# inside an async def endpoint
experiment = deps.get_experiment_access(experiment, current_user)          # sync: plain call
# on the decorator's parameter list
_: bool = Depends(deps.can_update_feature_flag)                              # async: Depends

# tests
def test_experiment_access_allows_owner(...):                                # sync test
    assert deps.get_experiment_access(experiment, owner) is experiment

@pytest.mark.asyncio
async def test_can_update_flag_rejects_viewer(...):                          # async test
    with pytest.raises(HTTPException):
        await deps.can_update_feature_flag(feature_flag=flag, current_user=viewer)
```

**S3.4** — Register it **last** (after `RequestIDMiddleware`) so it is the outermost layer, or
at least after `RateLimitMiddleware` so it wraps the limiter. Being outside RateLimit means
`dispatch` runs before the limiter can short-circuit, so `workspace_id` is bound into the
log context even for a 429. Register it before RequestID (i.e. inside it) if you want the
request id already bound when the tenant middleware logs — in practice: register it
immediately after RateLimit and before Prometheus/RequestID, giving RequestID → Prometheus →
Tenant → RateLimit → … .

**S3.5**

```python
def get_api_key_with_scope(scope: str):
    def dependency(user: User = Depends(get_api_key), api_key_header: str = Depends(API_KEY_HEADER)) -> User:
        api_key = db_lookup(api_key_header)   # or thread the APIKey row through get_api_key
        scopes = set((api_key.scopes or "").split(","))
        if scope not in scopes:
            raise HTTPException(status_code=403, detail=f"API key lacks the '{scope}' scope")
        return user
    return dependency


@router.post("/errors")
async def report_client_error(..., user: User = Depends(get_api_key_with_scope("errors:write"))):
    ...
```

A factory returning a closure is the standard way to parameterise a dependency, since
dependency parameters can't be passed from the endpoint. (In the real repo `get_api_key`
returns the `User`, not the `APIKey`; a scoped variant would change it to return both.)

---

**Next:** [Part IV — SQLAlchemy 2 & PostgreSQL →](./part-4-sqlalchemy.md)

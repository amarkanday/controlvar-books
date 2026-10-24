# Part III — Dependency Injection & Auth

### Hours 9–13

[← Part II](./part-2-pydantic.md) · [Contents](./README.md) · [Part IV →](./part-4-sqlalchemy.md)

FastAPI dependencies supply the resources and checks an endpoint needs. Hour 9 explains `Depends`.
Hours 10–12 trace database sessions, caller authentication and authorization through the dependencies
used by Experimently. Hour 13 covers middleware, which wraps request handling outside that dependency
graph. Read these lessons alongside `backend/app/api/deps.py`.

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

A dependency that reads a **path parameter** and takes an **optional argument with a default**
(it is used by no route today; it is here for the shape, not as a pattern to copy):

```python
# 📁 backend/app/api/deps.py
def get_experiment_by_key(
    experiment_key: str,
    db: Session = Depends(get_db),
    required_status: Optional[ExperimentStatus] = None,
) -> Experiment:
    """
    Get experiment by key or ID.

    Args:
        experiment_key (str): Experiment key or ID
        db (Session): Database session
        required_status (Optional[ExperimentStatus]): If provided, the experiment must have this status

    Returns:
        Experiment: Experiment with the given key or ID

    Raises:
        HTTPException: If experiment not found or has incorrect status
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
        experiment = (
            db.query(Experiment)
            .filter(getattr(Experiment, "key", None) == experiment_key)
            .first()
        )

    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")

    # Check for required status if specified
    if (
        required_status is not None
        and hasattr(experiment, "status")
        and experiment.status != required_status
    ):
        raise HTTPException(
            status_code=400, detail=f"Experiment not in {required_status.value} status"
        )
    # Default check for active status (only if required_status is not specified)
    elif (
        required_status is None
        and hasattr(experiment, "status")
        and experiment.status != ExperimentStatus.ACTIVE
    ):
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

A dependency **chain** — the scoped-key check that guards the server-side SDK ruleset depends
on the API-key check, which depends on the session and on the `X-API-Key` header:

```python
# 📁 backend/app/api/sdk_scope.py
def require_sdk_ruleset_key(
    db: Session = Depends(deps.get_db),
    api_key_header: str = Depends(deps.API_KEY_HEADER),
    _owner: User = Depends(deps.get_api_key),
) -> APIKey:
    """Return the presented ``APIKey`` row if it carries ``sdk:ruleset``."""
    ...
    if not has_scope(api_key.scopes, SDK_RULESET_SCOPE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=MISSING_SCOPE_DETAIL
        )
    ...
    return api_key
```

```python
# 📁 backend/app/api/v1/endpoints/sdk.py
def get_ruleset(
    request: Request,
    db: Session = Depends(deps.get_db),
    api_key: APIKey = Depends(require_sdk_ruleset_key),
) -> Response:
```

When `GET /api/v1/sdk/ruleset` arrives, FastAPI resolves `get_db` once (cached), reads the
header through `deps.API_KEY_HEADER` (an `APIKeyHeader` security scheme is itself a
dependency), runs `deps.get_api_key` — which also depends on `get_db` and the header, both
already cached — and only then `require_sdk_ruleset_key` with all three results. The
endpoint's own `db: Session = Depends(deps.get_db)` gets the _same_ session — one per request.
The `_owner` parameter is never read: it is there so that `get_api_key`'s 401 for a missing or
unknown key happens first, before the scope check can answer 403.

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
            logger.error(f"Error processing scheduled experiments: {e!s}")
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
    4. ``AUTH_PROVIDER=cognito``: validate the token with Cognito, then find
       the account linked to the Cognito user ID, or create it on a first
       sign-in (``services.cognito_accounts.resolve_cognito_user``). Every
       refusal is the same 401; its reason goes to the
       ``backend.app.auth.cognito_sign_in`` logger.
    """
    if dev_auth_bypass_active():
        try:
            return _get_or_create_dev_user(db)
        except Exception as dev_err:
            raise unexpected_failure(
                dev_err,
                "Dev user creation",
                "Could not sign in the development user",
                db=db,
            )

    if not token:
        raise _credentials_exception("Not authenticated")

    if settings.AUTH_PROVIDER == "local":
        return _authenticate_local_token(token, db)

    try:
        user_data = auth_service.get_user_with_groups(token)
    except CognitoTokenRefused as refusal:
        log_token_refused(refusal, "sign-in")
        raise _credentials_exception()
    ...
    try:
        return resolve_cognito_user(db, user_data)
    except CognitoSignInRefused as refusal:
        db.rollback()
        log_sign_in_refused(refusal)
        raise _credentials_exception()
    ...
```

Two details are worth copying. Every Cognito refusal is the _same_ `_credentials_exception()`
— a caller learns "401", never which check failed; the reason goes to a logger an operator can
read. And the one 500 here (the dev user could not be created) goes through
`unexpected_failure`, the same helper the endpoints use (Hour 4): a fixed sentence and a
request ID in the response, the exception in the log, never the exception text in the body.

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
    except (
        Exception
    ) as exc:  # pragma: no cover - defensive; DB errors are not auth errors
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

    try:
        api_key.update_last_used(db)
    except SQLAlchemyError as exc:
        db.rollback()
        logger.warning(f"Could not record API key use for key {api_key.id}: {exc}")

    return user
```

The `update_last_used` call records when the key was last seen (throttled inside the model
method; Hour 16 shows how). A failure there is bookkeeping, not authentication, so it is
caught, rolled back and logged — it never turns a valid key into an error.

The dev bypass is the one place a dependency returns an identity without a credential, and
it's guarded twice — in `Settings` at startup (a validator refuses `DEV_AUTH_BYPASS=true`
outside development and test) and here at call time, by asking a `Settings` property that
holds the rule:

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
    # ``is True`` also guards against a mocked settings object, whose
    # attribute would be a truthy MagicMock.
    return getattr(settings, "dev_auth_bypass_active", False) is True
```

The rule itself lives on `Settings` (`dev_auth_bypass_active` is a property there), so the
start-up validator and this call-time check cannot drift apart; `is True` makes a
`MagicMock`-ed settings object in a test answer `False` rather than "truthy".

> **Watch Out!** `core/security.py::decode_token` returns constant stub claims and raises
> unless `TESTING` is set. It exists so legacy tests can monkeypatch it. Never call it for
> validation; use `decode_local_token` (local) or `CognitoAuthService` (Cognito). If you see
> `decode_token` in new code, that's a review comment.

> **Did You Know?** The WebSocket endpoint can't use `Depends(oauth2_scheme)` because
> browsers can't set headers on a WebSocket handshake. It extracts the token from a
> `Sec-WebSocket-Protocol` entry (`experimently.bearer, <token>`) or, for a non-browser
> client, a bearer header, and then calls the _same_ `deps.get_current_user(token=..., db=...)` directly —
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
  (the account linked to the Cognito user ID, created on first sign-in; every refusal the
  same 401).
- `get_current_active_user` / `get_current_superuser` stack on it; `get_api_key` is a separate
  door for SDKs.
- Dependencies are functions — the WebSocket handler calls them by hand.

---

## Hour 12 — Authorization: one decision per resource, by role

### What & why

Authentication (Hour 11) answers _who is this_. Authorization answers _may they do this to
that_. In Experimently the answer comes from the caller's **role**, never from who created
the thing:

- **ADMIN** and **DEVELOPER** read, create, change and delete any experiment and any feature
  flag.
- **ANALYST** and **VIEWER** read them and change none — including the ones they created.
- A **superuser** may do anything. That is a separate bit on the user, not the ADMIN role
  (an ADMIN who is not a superuser cannot open the admin area, whose routes depend on
  `get_current_superuser`).

The creator is still recorded (`owner_id`) and shown in the dashboard. It is just not an
access rule. Everything is built from one primitive in `core/permissions.py`:

- `check_permission(user, ResourceType, Action)` — superuser shortcut, then the role matrix
  `ROLE_PERMISSIONS[role][resource]`;
- `get_permission_error_message(...)` — the sentence a 403 carries ("You don't have
  permission to update experiments").

On top of it sit exactly two decisions that routes use, one per resource:

- **Experiments:** `deps.get_experiment_read_access(experiment, user)` and
  `deps.get_experiment_change_access(experiment, user, action=Action.UPDATE)`. Plain,
  synchronous functions the endpoint _calls_ once it has loaded the experiment.
- **Feature flags:** `can_act_on_feature_flag(user, flag.owner_id, action)`, a plain function
  returning `bool`, which every per-flag route calls inline before it
  touches the row. The one exception is _create_ — there is no flag yet — which is gated by
  the async dependency `Depends(deps.can_create_feature_flag)`.

> **Watch Out!** `deps.py` also contains `get_feature_flag_access`, `can_update_feature_flag`
> and `can_delete_feature_flag`, async dependencies that mix in an ownership test
> (`check_ownership`). **No route uses them.** They are left-overs from before access became
> role-only; do not copy them, and do not build a new route on them. If you read an older
> tutorial that chains `Depends(can_update_feature_flag)`, that is the pattern this codebase
> moved away from.

#### Why a function call and not `Depends`

The check needs the _loaded_ resource: you cannot decide "may this user change this
experiment?" before you know the experiment exists. The routes therefore do it in a fixed
order in the body — **load (404) → decide (403) → validate state (400) → act** — with the
check as a one-line call. A `Depends` chain could express the same order (a lookup
dependency, then an access dependency), but it is harder to read when the lookup differs by
route (by id, by key, through a rollout schedule to its flag), and the codebase standardised
on the explicit call.

#### Why READ before the action

`get_experiment_change_access` first calls `get_experiment_read_access`. A role that may not
even _read_ experiments gets "You don't have permission to view experiments", never "...to
update experiments" — the second message would confirm there is something to update. Order
the checks from the weakest permission to the strongest.

### In the wild

The two experiment checks, verbatim apart from the docstrings:

```python
# 📁 backend/app/api/deps.py
def get_experiment_read_access(
    experiment: Union[Experiment, Dict[str, Any]],
    current_user: User = Depends(get_current_active_user),
) -> Union[Experiment, Dict[str, Any]]:
    """
    Admit a caller who may read this experiment and its results.

    A superuser, or a user whose role holds ``Action.READ`` on experiments.
    Who created the experiment is not considered.
    """
    if current_user.is_superuser:
        return experiment

    if not check_permission(current_user, ResourceType.EXPERIMENT, Action.READ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=get_permission_error_message(ResourceType.EXPERIMENT, Action.READ),
        )

    return experiment


def get_experiment_change_access(
    experiment: Union[Experiment, Dict[str, Any]],
    current_user: User,
    action: Action = Action.UPDATE,
) -> Union[Experiment, Dict[str, Any]]:
    """
    Admit a caller who may change this experiment.

    A superuser, or a user whose role holds both ``Action.READ`` and
    ``action`` on experiments. Who created the experiment is not considered,
    so a role without ``action`` is refused on an experiment it created too.
    """
    get_experiment_read_access(experiment, current_user)

    if current_user.is_superuser:
        return experiment

    if not check_permission(current_user, ResourceType.EXPERIMENT, action):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=get_permission_error_message(ResourceType.EXPERIMENT, action),
        )

    return experiment
```

Both are called with the experiment already loaded. Update, schedule, start, pause, complete,
archive and metadata pass no `action` (UPDATE); delete passes `Action.DELETE`:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
        experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
        if not experiment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Experiment not found"
            )

        # READ and UPDATE on experiments; who created it is not considered
        experiment = deps.get_experiment_change_access(experiment, current_user)
```

The flag decision. It is the only place that says whose flags a role may act on:

```python
# 📁 backend/app/core/permissions.py
FEATURE_FLAG_SCOPE: Dict[UserRole, Dict[Action, Scope]] = {
    UserRole.ADMIN: {
        Action.READ: Scope.ANY,
        Action.UPDATE: Scope.ANY,
        Action.DELETE: Scope.ANY,
    },
    UserRole.DEVELOPER: {
        Action.READ: Scope.ANY,
        Action.UPDATE: Scope.ANY,
        Action.DELETE: Scope.ANY,
    },
    UserRole.ANALYST: {Action.READ: Scope.ANY},
    UserRole.VIEWER: {Action.READ: Scope.ANY},
}


def can_act_on_feature_flag(
    user: Any,
    owner_id: Optional[Any],
    action: Action,
    scope_table: Optional[Dict[UserRole, Dict[Action, Scope]]] = None,
) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    if not check_permission(user, ResourceType.FEATURE_FLAG, action):
        return False
    role = getattr(user, "role", UserRole.VIEWER)
    table = FEATURE_FLAG_SCOPE if scope_table is None else scope_table
    scope = table.get(role, {}).get(action, Scope.OWN)
    if scope is Scope.ANY:
        return True
    return owner_id is not None and str(owner_id) == str(getattr(user, "id", None))
```

Read it in order. The role matrix comes **first**, so owning a flag can never let a read-only
role change it. Only then does `FEATURE_FLAG_SCOPE` say _whose_ flags the permission covers —
and today every entry is `Scope.ANY`. The `Scope.OWN` branch is deliberately kept as the
lever for a future per-flag restriction (locking a flag to its owner, approvals): that would
be a change to this table, not to twenty routes. The `scope_table` parameter exists so tests
can exercise that branch.

And a route calling it — load, decide, then act:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
    flag = db.query(FeatureFlag).filter(FeatureFlag.id == flag_id).first()
    if not flag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Feature flag not found"
        )

    # Check access permission
    if not can_act_on_feature_flag(current_user, flag.owner_id, Action.UPDATE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to update this feature flag",
        )
```

The same call guards delete (`Action.DELETE`), the read (`Action.READ`), activate,
deactivate, toggle, enable, disable, unarchive, every flag in a bulk toggle, and every rollout
schedule and stage change (`Action.UPDATE` on the schedule's flag). Create is the async
dependency, because there is no flag to pass:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
async def create_feature_flag(
    feature_flag_in: FeatureFlagCreate = Body(
        ..., description="Feature flag data to create"
    ),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    _skip_cache: None = Depends(_skip_cache_ignored),
    _: bool = Depends(deps.can_create_feature_flag),  # Use the permission dependency
) -> FeatureFlagRead:
```

The matrix primitive underneath everything:

```python
# 📁 backend/app/core/permissions.py
def check_permission(user: Any, resource: ResourceType, action: Action) -> bool:
    # Superusers always have all permissions
    if hasattr(user, "is_superuser") and user.is_superuser:
        return True

    # Get user's role
    try:
        role = getattr(user, "role", UserRole.VIEWER)
    except (AttributeError, TypeError):
        # Default to Viewer role if there's any issue
        role = UserRole.VIEWER

    # Check role permissions
    return has_permission(role, resource, action)
```

It is deliberately tolerant of mocks (`hasattr`, `getattr` with a `VIEWER` default) — a test
can pass a `SimpleNamespace` user, and a malformed row degrades to least privilege rather
than raising.

**A rule is only as good as the routes that call it.** A new flag route that forgets
`can_act_on_feature_flag` is open to every signed-in user. So a smoke test pins the exact set
of mutating routes under `/feature-flags`, `/rollout-schedules` and `/safety`, and what guards
each:

```python
# 📁 backend/tests/smoke/test_flag_change_routes_guarded.py
FLAG_RULE = "calls can_act_on_feature_flag"
ROLLOUT_RULE = "calls _require_update_on_flag (UPDATE on the schedule's flag)"
CREATE_GATE = "role-gated via Depends(can_create_feature_flag)"
SUPERUSER = "exempt: superuser-only (Depends(get_current_superuser))"
API_KEY = "exempt: an API-key evaluation that changes nothing"

INVENTORY = {
    ("POST", "/api/v1/feature-flags/"): CREATE_GATE,
    ...
    ("PUT", "/api/v1/feature-flags/{flag_id}"): FLAG_RULE,
    ("DELETE", "/api/v1/feature-flags/{flag_id}"): FLAG_RULE,
    ...
}
```

Add a `POST /feature-flags/{flag_id}/something` and that test fails until you classify the
route — the rule is enforced by a gate, not by memory.

The delete endpoint shows the order with one more step. It loads the experiment by id,
requires a second copy of the id in the query string (`experiment_key` must equal the id, a
guard against deleting the wrong row from a stale URL), then decides with `Action.DELETE`,
and only then checks the state:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
    # READ and DELETE on experiments; who created it is not considered
    experiment = deps.get_experiment_change_access(
        experiment, current_user, Action.DELETE
    )

    # Additional check for experiment status for non-draft experiments
    if experiment.status != ExperimentStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete experiments that are not in DRAFT status",
        )
```

Authorization before state is deliberate: a caller who may not delete gets 403 whatever the
experiment's status, so the 400 never tells an unauthorised caller what state the experiment
is in.

> **Watch Out!** The two shapes are not interchangeable. The experiment checks and
> `can_act_on_feature_flag` are plain `def` — `await deps.get_experiment_change_access(...)`
> raises `TypeError: object Experiment can't be used in 'await' expression`.
> `can_create_feature_flag` is `async def`: it belongs in `Depends(...)`, and if you call it by
> hand in a test you must `await` it, or you get a coroutine that never runs and the check
> silently "passes". `can_act_on_feature_flag` returns `False` rather than raising — forget
> the `if not ...: raise` and nothing is refused.

> **By the Way.** `ResourceType.API_KEY` is the same idea for SDK keys: every user manages
> their _own_ keys without consulting it, and the resource governs acting on _other_ users'
> keys (listing all, deleting someone else's), which only `ADMIN` may do. The matrix grows by
> adding a row, not by special-casing an endpoint.

### Try It Yourself

Find every caller of the two decisions:

```bash
git grep -n "can_act_on_feature_flag(" backend/app/api        # every per-flag route, bulk toggle, rollout schedules
git grep -n "get_experiment_change_access\|get_experiment_read_access" backend/app/api/v1/endpoints/experiments.py
git grep -n "can_update_feature_flag\|get_feature_flag_access" backend/app/api/v1/endpoints   # nothing: no route uses them
```

Then exercise the rule in a Python shell, with plain objects the way the unit tests do:

```python
from types import SimpleNamespace
from uuid import uuid4
from backend.app.core.permissions import Action, Scope, can_act_on_feature_flag
from backend.app.models.user import UserRole

viewer = SimpleNamespace(id=uuid4(), role=UserRole.VIEWER, is_superuser=False)
dev = SimpleNamespace(id=uuid4(), role=UserRole.DEVELOPER, is_superuser=False)

print(can_act_on_feature_flag(viewer, viewer.id, Action.UPDATE))   # False: owning it changes nothing
print(can_act_on_feature_flag(viewer, uuid4(), Action.READ))       # True: every role reads any flag
print(can_act_on_feature_flag(dev, uuid4(), Action.DELETE))        # True: any flag, not just their own

# the lever for a future per-flag lock, through the injected table:
own_only = {UserRole.DEVELOPER: {Action.UPDATE: Scope.OWN}}
print(can_act_on_feature_flag(dev, uuid4(), Action.UPDATE, own_only))   # False
print(can_act_on_feature_flag(dev, dev.id, Action.UPDATE, own_only))    # True
```

The same cases, and the experiment ones, are pinned in
`backend/tests/unit/core/test_feature_flag_access_rule.py`,
`backend/tests/unit/core/test_permissions.py` and
`backend/tests/integration/api/test_experiment_access_by_role.py`.

### Common mistakes

- **Deciding by ownership.** `if flag.owner_id == user.id` grants a VIEWER what their role
  refuses, and refuses a DEVELOPER what theirs grants. Ask the one decision.
- **Checking the action before READ**, leaking existence and giving the wrong message.
- **Checking state before access.** A 400 "not in DRAFT" to someone who may not delete tells
  them the state.
- **Forgetting the `raise`.** `can_act_on_feature_flag` returns a `bool`; it refuses nothing
  on its own.
- **A new resource type checked with ad-hoc `if user.role == ...`** instead of the matrix —
  now there are two sources of truth.
- **Assuming `ADMIN` role ⇒ superuser.** They're different bits; `get_current_superuser`
  checks only the bit.

### Key takeaways

- Access is by role, never by who created the resource; the creator is recorded and shown,
  not consulted.
- Experiments: `get_experiment_read_access` / `get_experiment_change_access(..., action)`,
  sync, called with the loaded experiment. Flags: `can_act_on_feature_flag(user,
  flag.owner_id, action)`, sync, called inline by every flag route; only create is an async
  `Depends(deps.can_create_feature_flag)`.
- Order in the route body: load (404) → READ → the action (403) → state (400) → act.
- The unused async flag helpers in `deps.py` are not a pattern to follow.
- A smoke test pins every flag-mutating route to its guard, so a new route cannot ship
  unguarded.

---

## Hour 13 — Middleware: the ASGI onion and why order is stated

### What & why

Middleware wraps the whole application: every request passes through it on the way in and
every response on the way out, before any router or dependency is involved. In Starlette the
easy form is `BaseHTTPMiddleware` — subclass it, implement `async def dispatch(self, request,
call_next)`, do something before `await call_next(request)`, something after, return the
response. The lighter form is a **pure ASGI** class: `async def __call__(self, scope,
receive, send)`, wrapping the next app directly.

The one fact everybody gets wrong at least once: **`add_middleware` wraps the current app,
so the last one added is the outermost.** Registration order and execution order are
reversed. `main.py` registers eight layers, in an order it explains in comments:

```
registration:  UnhandledError → RelativeSlashRedirect → RateLimit → TrustedHost
               → SecurityHeaders → Prometheus → RequestID → CORS
execution:     CORS → RequestID → Prometheus → SecurityHeaders → TrustedHost
               → RateLimit → RelativeSlashRedirect → UnhandledError → router
```

(Prometheus and RequestID are registered only when the monitoring packages import, which they
do in every build the project ships.)

- **CORS outermost.** Any response a layer inside it returns — a 429 from the limiter
  especially — passes back out through CORS and gets the `Access-Control-*` headers. Without
  them a browser will not let the dashboard read the status at all, so a rate-limited login
  used to surface as "Can't reach the API". A preflight is answered here and goes no
  further, so it never spends a request from the login limit (the trade: a preflight carries
  no request id and no security headers, and appears in no histogram).
- **RequestID** next: the id is bound into the log context before anything below it logs, and
  stamped on every response — including a 429 and a 400 from a refused `Host`.
- **Prometheus**: it times everything below it, rejections included.
- **SecurityHeaders outside the limiter**: the limiter returns its 429 without calling
  `call_next`, so a layer _inside_ it never runs. The 429 is the response a hostile client is
  most likely to see, so it must carry `X-Content-Type-Options`, CSP and HSTS too.
- **TrustedHost**: refuses a request whose `Host` is not one of ours with a 400. Outside the
  limiter, so a forged `Host` costs a header comparison rather than a rate-limit bucket and a
  database session; inside the security headers, so its 400 carries them. The health probes
  are exempt (a load balancer probes with the target's own IP as `Host`).
- **RateLimit** above the router: a rejected request never reaches a handler or opens a
  session.
- **RelativeSlashRedirect**: rewrites Starlette's trailing-slash 307 so its `Location` is
  relative and the browser keeps the origin (and the scheme) it was already using.
- **UnhandledError innermost**: a route's unhandled exception becomes a plain 500 _here_, so
  it travels back out through every layer above and carries CORS, the request id and the
  security headers. Left to Starlette, the 500 would be sent by its own outermost
  `ServerErrorMiddleware`, outside all of them, and a dashboard on another origin could not
  read it.

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
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not self._enabled:
            return await call_next(request)

        if request.method == "OPTIONS" and "access-control-request-method" in (
            request.headers
        ):
            return await call_next(request)

        path = request.url.path
        client_ip = _get_client_ip(request)

        # Determine the applicable limit for this path
        limit, window = resolve_rate_limit(path, self._sdk_limit)

        rate_key = rate_limit_key(client_ip, path)
        allowed, remaining = self._limiter.is_allowed(rate_key, limit, window)
        ...
        if not allowed:
            ...
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too Many Requests. Please slow down and retry after a moment.",
                },
                headers={
                    "Retry-After": str(window),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Window": str(window),
                },
            )

        response = await call_next(request)

        # Attach informational rate-limit headers to successful responses
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Window"] = str(window)

        return response
```

It is constructed with an argument — `app.add_middleware(RateLimitMiddleware,
enabled=_rate_limit_enabled)`, off under the test runner — and the 429 body uses the same
`{"detail": ...}` shape as `HTTPException`, so clients don't need a special case. The
response headers on success let SDKs back off _before_ they're rejected. The preflight skip
asks for `Access-Control-Request-Method`, not just `OPTIONS`: skipping on the method alone
would let any client exempt itself from the limit by choosing a verb.

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

The innermost layer is a **pure ASGI** middleware — no `Request` object, no `call_next`, just
the three ASGI callables, and a wrapped `send` that notices whether the response has started:

```python
# 📁 backend/app/middleware/unhandled_error_middleware.py
class UnhandledErrorMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if not started:
                response = PlainTextResponse("Internal Server Error", status_code=500)
                await response(scope, receive, send)
            raise
```

It sends the same 500 Starlette would, but from the inside, and then re-raises so the server
still logs the traceback. (Registering `@app.exception_handler(Exception)` does not achieve
this: Starlette hands a handler for `Exception` to that same outermost layer.)

And CORS, Starlette's own, configured to exactly what the API needs — registered last:

```python
# 📁 backend/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    # No first-party client sends credentials cross-origin: the dashboard
    # authenticates with a bearer header, and SSO's state cookie travels on a
    # top-level navigation. Off, a `*` entry can never be combined with them.
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    expose_headers=[
        "X-Request-ID",
        "Retry-After",
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Window",
    ],
)
```

`expose_headers` is the part people forget: a browser script can only read response headers
the server exposes. Without `Retry-After` in the list the dashboard could see that a login was
rate-limited but not for how long. `allow_credentials=False` is deliberate: the dashboard
sends a bearer header, not cookies, so cross-origin responses carry no
`Access-Control-Allow-Credentials`.

> **Watch Out!** `BaseHTTPMiddleware` has known interactions with `BackgroundTasks` and
> `contextvars`; the request-id middleware's `ContextVar` works because the binding happens
> inside `dispatch` on the same task that runs the endpoint. Most of the stack uses
> `BaseHTTPMiddleware` for readability; the two layers that need to see raw ASGI messages —
> `UnhandledErrorMiddleware` and `RelativeSlashRedirectMiddleware` — are pure ASGI.

> **Did You Know?** `backend/app/middleware/` still holds `logging_middleware.py`,
> `error_middleware.py` and `metrics_middleware.py`. `main.py` neither imports nor registers
> them: structlog with `RequestIDMiddleware`, and `PrometheusMetricsMiddleware`, replaced
> them. Read `main.py`'s registration block, not the directory listing, to know what runs.

### Try It Yourself

See the onion from outside (`$API` is `http://localhost:8000/api/v1`):

```bash
# every response: CORS (outermost), request id, rate-limit headers, security headers
curl -si $API/experiments/ -H "Authorization: Bearer $TOKEN" -H "Origin: http://localhost:3000" \
  | grep -iE "access-control|x-request-id|x-ratelimit|x-content-type|content-security"
# the login limit is 10 per minute per IP: requests 11 and 12 inside the minute are refused
for i in $(seq 1 11); do curl -s -o /dev/null -X POST $API/auth/login -H 'Content-Type: application/json' -d '{"email":"a@b.c","password":"c"}'; done
curl -si -X POST $API/auth/login -H 'Content-Type: application/json' -H "Origin: http://localhost:3000" \
  -d '{"email":"a@b.c","password":"c"}' | grep -iE "HTTP/|access-control|x-request-id|x-content-type|retry-after"
```

That last response is the most instructive thing in this Hour: a 429 that carries an
`X-Request-ID`, an `X-Content-Type-Options` and an `Access-Control-Allow-Origin` — three
layers outside the limiter decorating a response the limiter produced without ever calling
the router. Order is observable. (Wait a minute before Hour 11's Try-It again: the same limit
applies.)

### Common mistakes

- **Assuming registration order is execution order.** It's reversed; the file's comment is
  there because someone got it wrong — CORS was once registered first and a 429 never reached
  it.
- **Putting a decorating layer inside a short-circuiting one.** Anything nested inside the
  limiter never sees its 429.
- **Doing per-endpoint logic in middleware** (checking a role by path prefix). That's what
  dependencies are for.
- **Forgetting `expose_headers`** for headers a browser must read.
- **Short-circuiting with a body shape different from `{"detail": ...}`.**

### Key takeaways

- Middleware wraps everything; `dispatch(request, call_next)` or pure ASGI `__call__(scope,
  receive, send)`; the last registered is the outermost.
- Experimently's eight layers, outermost first: CORS → RequestID → Prometheus →
  SecurityHeaders → TrustedHost → RateLimit → RelativeSlashRedirect → UnhandledError →
  router. Every refusal and every 500 passes back out through CORS, the request id and the
  security headers.
- Normalise labels (UUIDs → `{id}`) before recording metrics.
- Short-circuit responses use the same `{"detail": ...}` shape; success responses carry
  informational rate-limit headers; CORS must `expose_headers` for browsers to read them.

---

## Part III Workshop

**Q1. `GET /sdk/ruleset` declares `db: Session = Depends(deps.get_db)` and also
`api_key: APIKey = Depends(require_sdk_ruleset_key)`. How many sessions are opened?**
One. `get_db` is cached per request; `require_sdk_ruleset_key`, the `deps.get_api_key` it
depends on, and the endpoint's own parameter all receive the same session.

**Q2. Why is `get_experiment_by_key`'s `required_status` effectively unusable through
`Depends`?**
Dependency parameters are resolved from the request, so `required_status` becomes an
optional query parameter — an endpoint can't pass a value to it. That's why
`delete_experiment` checks `DRAFT` inline.

**Q3. What does `auto_error=False` change for a request with no `Authorization` header?**
The scheme returns `None` instead of raising 401, so `get_current_user` runs and can return
the dev-bypass user (development/test only) or raise its own 401.

**Q4. A `VIEWER` calls `PUT /feature-flags/{flag_id}` on a flag they created. Result?**
403 "Not enough permissions to update this feature flag". The route loads the flag (it
exists, so no 404), then asks `can_act_on_feature_flag(user, flag.owner_id, Action.UPDATE)`.
The role matrix is checked first and gives `VIEWER` no UPDATE on flags, so it returns `False`
before ownership is ever looked at. A `DEVELOPER` making the same call on a flag someone else
created succeeds: access is by role, not by who created the flag.

**Q5. In what order do the eight middleware run for a request, and which headers does a 429
carry?**
CORS → RequestID → Prometheus → SecurityHeaders → TrustedHost → RateLimit →
RelativeSlashRedirect → UnhandledError → router. The 429 comes from RateLimit, which does not
call the layers inside it. Everything outside it still runs on the way out, so the 429 carries
the security headers, an `X-Request-ID`, the CORS headers (for an allowed `Origin`) and its own
`Retry-After`/`X-RateLimit-*`, and Prometheus times it.

**Q6. Why does the WebSocket handler call `deps.get_current_user(token=..., db=...)`
directly?**
Browsers can't set headers on a WebSocket handshake, so `Depends(oauth2_scheme)` can't
extract the token; the handler extracts it itself (the subprotocol, or a bearer header) and
calls the dependency as a plain function.

**Quiz.** `app.dependency_overrides[deps.get_db] = fake` is set, but an endpoint imports
`from backend.app.db.session import get_db` and declares `Depends(get_db)`. Is it overridden?
→ No. Overrides are keyed by the function object, and `deps.get_db` and
`db.session.get_db` are two different functions in this repo. The harness overrides only
`deps.get_db` (check `conftest.py`), so an endpoint must declare `Depends(deps.get_db)` to be
testable against the per-process test database.

---

## Exercises

**E3.1 — A yield dependency.** Write `get_audit_context()` that yields a dict
`{"request_id": ..., "started": ...}` and logs the elapsed time in `finally`. Where does the
elapsed-time log line end up relative to the response — before or after the client sees it?

**E3.2 — A parameterised lookup.** Write an async dependency `get_workspace_by_slug(slug: str,
db = Depends(get_db))` that 404s when missing, and an access dependency
`get_workspace_access` that chains on it and on `get_current_user`, allowing superusers,
members, or ADMINs.

**E3.3 — Sync vs async.** Given `def get_experiment_change_access(...)` (sync, called with
the loaded experiment) and `async def can_create_feature_flag(...)` (an async dependency),
write the lines that use each correctly in an `async def` endpoint, and a test for each: a
DEVELOPER who did not create an experiment may change it, and a VIEWER is refused flag
creation.

**E3.4 — Order the onion.** A new `TenantMiddleware` must read a `X-Workspace` header and
bind `workspace_id` into the log context, and must apply to rate-limited (429) responses'
logs too. Where do you register it, and why? Which layer must stay outermost?

**E3.5 — A scoped API key.** The codebase has one scoped-key dependency,
`require_sdk_ruleset_key` (Hour 9). Generalise it: sketch a `require_scope(scope: str)`
dependency _factory_ that returns a dependency requiring the presented key's `scopes` to
include `scope`, and show its use on `POST /tracking/errors`.

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
# inside an async def endpoint, after loading the experiment
experiment = deps.get_experiment_change_access(experiment, current_user)   # sync: plain call, no await
# on the endpoint's parameter list
_: bool = Depends(deps.can_create_feature_flag)                             # async: Depends

# tests
def test_developer_changes_an_experiment_they_did_not_create():             # sync test
    dev = SimpleNamespace(id=uuid4(), role=UserRole.DEVELOPER, is_superuser=False)
    experiment = SimpleNamespace(owner_id=uuid4())                           # someone else's
    assert deps.get_experiment_change_access(experiment, dev) is experiment

async def test_viewer_cannot_create_a_flag():                                # async test
    viewer = SimpleNamespace(id=uuid4(), role=UserRole.VIEWER, is_superuser=False)
    with pytest.raises(HTTPException) as exc:
        await deps.can_create_feature_flag(current_user=viewer)
    assert exc.value.status_code == 403
```

The repository sets `asyncio_mode = "auto"`, so an `async def` test runs without
`@pytest.mark.asyncio` (adding it is harmless). Forgetting `await` is the bug to watch for:
the coroutine is created, never run, and the `raises` block fails for the wrong reason.

**S3.4** — It must be **outside** `RateLimitMiddleware` (registered after it), so its
`dispatch` runs before the limiter can short-circuit and `workspace_id` is bound even for a
429. It should be **inside** `RequestIDMiddleware` (registered before it), so the request id
is already bound when the tenant middleware logs. And **CORS stays outermost** — registered
last — or a response produced outside CORS would lose the `Access-Control-*` headers. In
practice: register it right after `SecurityHeadersMiddleware` and before Prometheus,
giving CORS → RequestID → Prometheus → Tenant → SecurityHeaders → TrustedHost → RateLimit →
… .

**S3.5**

```python
def require_scope(scope: str):
    def dependency(
        db: Session = Depends(deps.get_db),
        api_key_header: str = Depends(deps.API_KEY_HEADER),
        _owner: User = Depends(deps.get_api_key),          # 401 for a missing/unknown key, first
    ) -> APIKey:
        api_key = db.query(APIKey).filter(APIKey.key == hash_api_key(api_key_header)).first()
        if api_key is None or not has_scope(api_key.scopes, scope):
            raise HTTPException(status_code=403, detail=f"This API key does not have the '{scope}' scope.")
        return api_key
    return dependency


@router.post("/errors")
async def report_client_error(..., api_key: APIKey = Depends(require_scope("errors:write"))):
    ...
```

A factory returning a closure is the standard way to parameterise a dependency, since
dependency parameters can't be passed from the endpoint. Two details are copied from
`require_sdk_ruleset_key`: the decision is made on the **presented** key (re-hashed from the
header), not on "some key of this user", because `get_api_key` returns the `User`, and
another key of the same user carrying the scope must grant nothing; and scope membership is
`has_scope` from `backend/app/core/api_key_scopes.py` (exact, case-sensitive), not a substring
test. The real dependency adds one more rule — the key's owner must still be allowed to
change flags — which a generic factory would take as a second parameter.

---

**Next:** [Part IV — SQLAlchemy 2 & PostgreSQL →](./part-4-sqlalchemy.md)

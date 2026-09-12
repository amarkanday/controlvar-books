# Part I — Foundations

### Hours 1–4

[← Back to Contents](./README.md)

This Part lays the bedrock the other twenty hours build on: _why_ a Python team reaches for a
framework that validates before it runs (Hour 1), how an application is assembled from routers
and documented for free (Hour 2), how inputs are _declared_ rather than parsed (Hour 3), and how
responses are shaped, coded and refused (Hour 4). Have the API running (`make dev`) and a
second terminal open — FastAPI is a framework you learn by watching the 422s, and those only
appear when you send requests.

---

## Hour 1 — Why FastAPI? The 422 that saved a bad experiment

### What & why

FastAPI is a Python web framework built on three ideas: **type hints are the API contract**,
**validation happens before your code runs**, and **the documentation is generated from the
same declarations**. You write a function whose parameters say what the request must contain;
the framework parses, validates, converts, documents and serialises. The value proposition is
one sentence: **a request that reaches your function is already well-formed.**

The phrase "already well-formed" is load-bearing. In a framework that hands you a raw request,
every handler begins with defensive code — is the id a UUID? is `limit` an integer, and is it
between 1 and 500? does the body have `variants`, and is it a list? — and every handler does
it slightly differently, and one of them forgets. In FastAPI those checks are _declarations_:
`experiment_id: UUID`, `limit: int = Query(100, ge=1, le=500)`, `experiment_in:
ExperimentCreate`. A request that fails them gets a **422 Unprocessable Entity** with a
machine-readable list of exactly what was wrong, and your function is never called.

It's built on **Starlette** (the ASGI toolkit: routing, middleware, WebSockets) and
**Pydantic** (the data-validation library; Part II is about it). It's `async`-native but
happy to run synchronous functions in a threadpool. And because every input and output is a
typed declaration, it emits an **OpenAPI** document — the interactive docs at
`/api/v1/docs` are generated, not written.

#### The 422 that saved a bad experiment

Abstract benefits are forgettable. Here's a concrete one from this codebase.

An experiment's variants carry `traffic_allocation` percentages, and the assignment hash lays
them out as a cumulative distribution over buckets 0–99 (`AssignmentService._hash_user_to_variant`).
If the percentages sum to 90, buckets 90–99 fall through to "the last variant" — silently
over-allocating one arm by 10%, corrupting the experiment's balance, and producing an SRM
warning weeks later that nobody can explain. The request that creates an experiment is where
that mistake enters the system, and it's refused there:

```python
# 📁 backend/app/schemas/experiment.py
class ExperimentCreate(ExperimentBase):
    ...
    @model_validator(mode="after")
    def validate_variants(self):
        """Validate that there is at least one control variant."""
        if not any(variant.is_control for variant in self.variants):
            raise ValueError("At least one variant must be marked as control")

        # Check that traffic allocations sum to 100
        total_allocation = sum(variant.traffic_allocation for variant in self.variants)
        if total_allocation != 100:
            raise ValueError(
                f"Traffic allocations must sum to 100% (currently {total_allocation}%)"
            )
```

Send a 60/30 split and you get, before any database is touched:

```json
{"detail": [{"type": "value_error", "loc": ["body"], "msg": "Value error, Traffic allocations must sum to 100% (currently 90%)", ...}]}
```

The dashboard shows that message verbatim; the experiment is never created; the SRM warning
never happens. That is the whole framework in one example: the invariant is _declared once_
on the schema, enforced for every caller (dashboard, script, SDK, test), and documented in the
OpenAPI schema automatically. The lesson the Experimently team drew, and the thesis of this
book:

> **Validation is architecture.** Every invariant you can state on a request model is a bug
> class you never debug in production.

### In the wild

The endpoint that owns that schema is almost embarrassingly small, because the framework did
the work:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
@router.post(
    "/",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create experiment",
    response_description="Returns the created experiment",
)
async def create_experiment(
    experiment_in: ExperimentCreate = Body(..., description="Experiment creation data"),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    cache_control: Dict[str, Any] = Depends(deps.get_cache_control),
) -> ExperimentResponse:
```

Four parameters, four declarations: a validated body, a database session, an authenticated
user, an optional cache handle. By the time the first line of the body runs, the request has
been parsed, the schema validated (including the 100% rule), the bearer token resolved to a
`User`, and a `Session` opened. If any of those fail, the function doesn't run — a 422, a 401,
or a 500 with a request id.

### A second way to feel the gap

Consider the same endpoint in a "raw request" style:

```python
# ❌ what FastAPI saves you from writing (and getting subtly wrong)
async def create_experiment(request):
    body = await request.json()                      # may raise; may not be a dict
    variants = body.get("variants") or []            # may not be a list
    total = 0
    for v in variants:
        total += int(v.get("traffic_allocation", 0)) # may not be int-able; negative?
    if total != 100:
        return JSONResponse({"error": "bad allocation"}, status_code=400)   # a different shape from every other error
    ...
```

Every line is a decision someone else's handler will make differently. The declared version
makes those decisions once, in the schema, and the error shape is the same for the whole API.

### Try It Yourself

With the API running and a token (`make dev`; login as in the
[usage guide](../guides/usage.md#1-signing-in-and-roles)):

```bash
API=http://localhost:8000/api/v1
# a 422 for a bad UUID — your function never ran
curl -s $API/experiments/not-a-uuid -H "Authorization: Bearer $TOKEN" | python -m json.tool
# a 422 for the 90% split — the validator's message, verbatim
curl -s -X POST $API/experiments/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "name": "bad split", "variants": [{"name":"a","is_control":true,"traffic_allocation":60},{"name":"b","traffic_allocation":30}],
  "metrics": [{"name":"m","event_name":"purchase"}]}' | python -m json.tool
```

Then open `http://localhost:8000/api/v1/docs`, find `POST /api/v1/experiments/`, and notice
that the schema shown there — down to `traffic_allocation` being 0–100 — is generated from
the declarations you just tripped.

### Common mistakes

- **Thinking 422 is "your API is broken."** It's the framework doing its job: the request was
  malformed, and the `detail` list says how. Read `loc` and `msg`.
- **Validating in the handler what the schema could validate.** If it's an invariant of the
  input, it belongs on the model (Part II).
- **Returning ad-hoc error shapes.** `HTTPException(status_code, detail)` gives every error
  the same `{"detail": ...}` shape the dashboard's `ApiError` understands (Hour 4).

### Key takeaways

- FastAPI = Starlette (ASGI) + Pydantic (validation) + your type hints as the contract.
- Requests are validated **before** your function runs; failures are 422s with a structured
  `detail`.
- The OpenAPI docs are generated from the same declarations.
- The 90%-allocation validator is the canonical "declare the invariant once" story.

---

## Hour 2 — The app, routers, and the OpenAPI document

### What & why

A FastAPI application is one `FastAPI()` instance with **routers** mounted on it. An
`APIRouter` is a group of path operations with a shared prefix and tags; you define one per
resource in its own module and mount them all in one place. The result is that the mount
file reads as the API's table of contents, each resource module is self-contained, and the
OpenAPI document groups operations by tag.

Three structural facts about the app object:

- **`app = FastAPI(...)`** takes the title/version/description that head the OpenAPI doc, an
  `openapi_url`, and a `lifespan` (Hour 19).
- **`app.include_router(router, prefix=..., tags=[...])`** mounts a router; routers can include
  other routers, so `/api/v1` is itself a router wrapping the version's routers.
- **`app.add_middleware(...)`** wraps the whole app in cross-cutting layers (Hour 13).

#### Why one mount file

You could mount each router from its own module (`from .experiments import router;
app.include_router(router)` scattered around). Experimently mounts all fifty in `api/api.py`,
in one list, each with a comment naming its epic. The payoff: a new engineer reads one file
and knows every feature; a reviewer sees a new endpoint's prefix and tag in the diff; and the
docs' section order is deliberate.

### In the wild

The version router and a slice of its mounts:

```python
# 📁 backend/app/api/api.py
from fastapi import APIRouter

from backend.app.api.v1.endpoints import (
    auth, api_keys, users, experiments, tracking, client_errors, feature_flags, admin,
    assignments, events, results, rollout_schedules, metrics, safety, audit_logs, export,
    ...
)

# Create API router for v1
api_router_v1 = APIRouter()

# Include endpoint routers with appropriate prefixes and tags
api_router_v1.include_router(auth.router, prefix="/auth", tags=["Authentication"])
# P0 (open-core): user-owned API keys for SDK authentication
api_router_v1.include_router(api_keys.router, prefix="/api-keys", tags=["API Keys"])
api_router_v1.include_router(users.router, prefix="/users", tags=["Users"])
api_router_v1.include_router(experiments.router, prefix="/experiments", tags=["Experiments"])
api_router_v1.include_router(tracking.router, prefix="/tracking", tags=["Tracking"])
# Client-side error reports (POST /tracking/errors, /tracking/errors/batch) feed
# feature flag safety monitoring; same prefix and API-key auth as event tracking.
api_router_v1.include_router(client_errors.router, prefix="/tracking", tags=["Tracking"])
...
# Main API router that includes versioned routers
api_router = APIRouter()
api_router.include_router(api_router_v1)
```

and the app that mounts it under the version prefix:

```python
# 📁 backend/app/main.py
app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.PROJECT_DESCRIPTION,
    version=settings.VERSION,
    docs_url=None,
    redoc_url=None,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)
...
# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Health probes (/health/live, /health/ready, /health) and Prometheus /metrics
app.include_router(health_router)
```

A resource module declares its router once at the top, optionally with router-level defaults
(here: documented error responses shared by every operation in the module):

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
# Create router with tag for documentation grouping
router = APIRouter(
    tags=["Experiments"],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Authentication failed",
            "content": {"application/json": {"example": {"error": {"status_code": 401, "message": "Could not validate credentials"}}}},
        },
        ...
    },
)
```

Two prefixes can share a path: `tracking` and `client_errors` both mount at `/tracking`, so
`POST /tracking/track` and `POST /tracking/errors` live in different modules with the same
authentication (`get_api_key`) and the same tag. And a router can mount with `prefix=""` when
its paths are already complete (`bulk_toggle`, `websocket_results`).

#### The OpenAPI document

`docs_url=None` disables the default `/docs`; three explicit handlers serve the doc under the
versioned prefix so they sit behind the same proxy path as the API:

```python
# 📁 backend/app/main.py
@app.get("/api/v1/openapi.json", include_in_schema=False)
async def get_openapi_schema():
    return get_openapi(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description=settings.PROJECT_DESCRIPTION,
        routes=app.routes,
    )


@app.get("/api/v1/docs", include_in_schema=False)
async def get_swagger_docs():
    return get_swagger_ui_html(openapi_url="/api/v1/openapi.json", title=f"{settings.PROJECT_NAME} - Swagger UI")
```

`include_in_schema=False` keeps the doc endpoints out of the doc. Everything else — every
path, parameter, schema, example and tag description (`tags_metadata` at the bottom of
`api.py`) — comes from declarations. The dashboard's `npm run openapi:dump` writes the live
spec to a fixture so a test can catch a service path that no longer exists on the server.

> **Watch Out!** FastAPI ≥ 0.141 changed `app.routes`: included routers can appear as lazy
> `_IncludedRouter` entries with no `.path`. Code that enumerates routes — a wiring test, a
> script listing endpoints — must use `effective_route_contexts`; see
> `backend/tests/smoke/test_wiring.py::_iter_http_routes`. `get_openapi(routes=app.routes)`
> handles it internally.

> **By the Way.** A trailing slash matters. `POST /api/v1/experiments/` (the route) and
> `POST /api/v1/experiments` (no slash) are different paths; Starlette answers the latter with
> a 307 redirect to the former. The CI docker-smoke job follows redirects (`curl -L`) for
> exactly this reason, and the dashboard's service layer uses the slashed form.

### Try It Yourself

Read the API as its table of contents, then as its OpenAPI document:

```bash
grep -c "include_router" backend/app/api/api.py
grep -n 'prefix="/tracking"' backend/app/api/api.py          # two routers, one prefix

python - <<'PY'
import json, urllib.request
spec = json.load(urllib.request.urlopen("http://localhost:8000/api/v1/openapi.json"))
print(spec["info"]["title"], spec["info"]["version"], len(spec["paths"]), "paths")
print(sorted({t for ops in spec["paths"].values() for op in ops.values() for t in op.get("tags", [])})[:12])
PY
```

Then create a scratch router in a throwaway file to feel the mechanics (don't commit it):

```python
# scratch_router.py — run: uvicorn scratch_router:app --port 8010
from fastapi import APIRouter, FastAPI

experiments = APIRouter()

@experiments.get("/")
def list_experiments():
    return [{"key": "hero_banner"}]

v1 = APIRouter()
v1.include_router(experiments, prefix="/experiments", tags=["Experiments"])

app = FastAPI(title="scratch")
app.include_router(v1, prefix="/api/v1")
# → GET http://localhost:8010/api/v1/experiments/  and  /docs
```

### Common mistakes

- **Mounting a router from inside its own module** (importing `app` there) — a circular
  import waiting to happen. Routers are declared in resource modules and mounted in `api.py`.
- **Forgetting the prefix is applied by the mount, not the router.** Paths inside
  `experiments.py` are `"/"`, `"/{experiment_id}"`, not `"/experiments/..."`.
- **Two operations on the same method + path in different routers.** The first mounted wins
  silently; the wiring smoke test is your safety net.

### Key takeaways

- One `FastAPI()`; one `APIRouter` per resource; all mounted in `api/api.py` with a prefix and
  a tag — the API's table of contents.
- Routers nest (`/api/v1` wraps the version's routers); prefixes can be shared or empty.
- The OpenAPI document is generated from declarations and served under the version prefix.
- Enumerate routes via `effective_route_contexts` on FastAPI ≥ 0.141.

---

## Hour 3 — Declaring inputs: `Path`, `Query`, `Body`, headers

### What & why

A path operation's parameters _are_ its request contract. FastAPI decides where each
parameter comes from by its declaration:

| Declaration                                     | Source                          | Example in the repo                              |
| ----------------------------------------------- | ------------------------------- | ------------------------------------------------ |
| Named in the path template, e.g. `{flag_key}`   | path segment                    | `flag_key: str = Path(...)`                       |
| Simple type, not in the path                    | query string                    | `user_id: str = Query(...)`                       |
| A Pydantic model                                | JSON body                       | `request: AssignmentRequest = Body(...)`          |
| `Header(...)`, `Cookie(...)`                    | headers / cookies               | `APIKeyHeader(name="X-API-Key")` (a dependency)   |
| `Depends(...)`                                  | a dependency function (Hour 9)  | `db: Session = Depends(deps.get_db)`              |
| `Request`, `WebSocket`, `BackgroundTasks`       | the framework                   | `request: Request` in middleware and rate limits  |

The explicit forms `Path(...)`, `Query(...)`, `Body(...)` add **constraints and
documentation**: a default (or `...` for required), `ge`/`le`/`gt`/`lt` for numbers,
`min_length`/`max_length`/`pattern` for strings, `description` for the docs. The constraint
becomes a 422 _and_ a line in the OpenAPI doc, from one declaration.

#### Why constraints belong on the declaration

`limit: int = Query(100, ge=1, le=500)` says three things at once: the default is 100, the
caller can't ask for 0, and the caller can't ask for 10,000 rows. A handler that instead
clamps `limit` silently teaches callers nothing and hides a bug in a client that thinks it
asked for 10,000. Refusing at the boundary is friendlier than pretending.

### In the wild

A GET with every kind of parameter:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
@router.get("/", response_model=ExperimentListResponse, summary="List experiments", ...)
async def list_experiments(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
    cache_control: Dict[str, Any] = Depends(deps.get_cache_control),
    status_filter: Optional[str] = Query(
        None,
        description="Filter by experiment status (draft, active, paused, completed, archived)",
    ),
    skip: int = Query(0, ge=0, description="Number of records to skip for pagination"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of records to return"),
    search: Optional[str] = Query(None, description="Search term to filter experiment names and descriptions"),
    sort_by: Optional[str] = Query("created_at", description="Field to sort by (created_at, updated_at, name, status)"),
    sort_order: Optional[str] = Query("desc", description="Sort order (asc, desc)"),
) -> ExperimentListResponse:
```

A path parameter with a type that does validation for free — a malformed UUID is a 422 —
and a required query parameter next to an optional one:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
@router.get("/evaluate/{flag_key}", response_model=Dict[str, Any], summary="Evaluate feature flag for a user", ...)
async def evaluate_feature_flag(
    flag_key: str = Path(..., description="The key of the feature flag to evaluate"),
    user_id: str = Query(..., description="ID of the user to evaluate the flag for"),
    context: Optional[str] = Query(None, description=CONTEXT_QUERY_DESCRIPTION),
    db: Session = Depends(deps.get_db),
    api_key_info: Dict[str, Any] = Depends(deps.get_api_key),
) -> Dict[str, Any]:
    parsed_context = _parse_context_param(context)
    return _evaluate_flag_by_key(db, flag_key, user_id, parsed_context)
```

Note `context` is a `str` — URL-encoded JSON — and the endpoint parses it itself:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
def _parse_context_param(context: Optional[str]) -> Optional[Dict[str, Any]]:
    """Decode the ``context`` query parameter; 422 when it is not a JSON object."""
    if context is None or context == "":
        return None
    try:
        parsed = json.loads(context)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Query parameter 'context' must be a URL-encoded JSON object")
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Query parameter 'context' must be a JSON object")
    return parsed
```

That's the exception that proves the rule: a query string can't carry a nested object, so the
GET takes a string and decodes it — raising the _same_ 422 the framework would — while a
sibling `POST /evaluate/{flag_key}` takes a proper body for contexts too large for a URL:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
class FlagEvaluationRequest(BaseModel):
    """Body for ``POST /feature-flags/evaluate/{flag_key}``."""

    user_id: str = Field(..., min_length=1, description="ID of the user to evaluate the flag for")
    context: Optional[Dict[str, Any]] = Field(None, description="Targeting context (user/device/app attributes)")
```

A body parameter, and how a header becomes a dependency rather than a `Header(...)`:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
async def assign_user_to_experiment(
    request: AssignmentRequest = Body(..., description="Assignment request data"),
    db: Session = Depends(deps.get_db),
    api_key_info: Dict[str, Any] = Depends(deps.get_api_key),
) -> VariantAssignmentResponse:
```

```python
# 📁 backend/app/api/deps.py
# API key header extraction
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key(db: Session = Depends(get_db), api_key_header: str = Depends(API_KEY_HEADER)) -> Optional[User]:
    if not api_key_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key missing",
                            headers={"WWW-Authenticate": "APIKey"})
    ...
```

`APIKeyHeader` is a FastAPI _security scheme_: it reads the header like `Header(...)` would,
but it also registers itself in the OpenAPI doc so the "Authorize" button works and every
operation that depends on it shows a lock icon. Hour 11 is about these.

> **Did You Know?** The first parameter of `assign_user_to_experiment` is named `request`,
> which shadows nothing but is easy to confuse with Starlette's `Request`. It's the
> `AssignmentRequest` body. If you need the raw request (for the client ip, say), declare
> `req: Request` — the framework injects it by type.

### Try It Yourself

Trip each source with a deliberately wrong request:

```bash
# path: bad UUID → 422 with loc ["path","experiment_id"]
curl -s $API/experiments/xyz -H "Authorization: Bearer $TOKEN" | python -c 'import sys,json; print(json.load(sys.stdin)["detail"][0]["loc"])'
# query: limit out of range → 422 with loc ["query","limit"]
curl -s "$API/experiments/?limit=9999" -H "Authorization: Bearer $TOKEN" | python -c 'import sys,json; print(json.load(sys.stdin)["detail"][0]["loc"], json.load(open("/dev/stdin")) if False else "")'
# body: missing user_id → 422 with loc ["body","user_id"]
curl -s -X POST $API/tracking/assign -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"experiment_key":"x"}' | python -m json.tool
# query-string JSON: not an object → the hand-written 422
curl -s "$API/feature-flags/evaluate/anything?user_id=u&context=%5B1%5D" -H "X-API-Key: $KEY"
```

Then write the declaration for a hypothetical `GET /experiments/{id}/events?since=<iso
datetime>&limit=<1..1000>&event_name=<optional>` and check that FastAPI would document and
validate each part without a line of parsing.

### Common mistakes

- **Parsing inside the function what a type would parse.** `experiment_id: str` then
  `UUID(experiment_id)` in the body is a 500 waiting to happen; declare `UUID`.
- **A Pydantic model as a query parameter.** Models are bodies. For structured query data use
  a string you decode (as `context` does) or a POST.
- **Forgetting `...` means required.** `Query(None)` is optional; `Query(...)` is required.
  With a plain default (`limit: int = 100`) the parameter is optional with that default.
- **Using `Header("X-API-Key")` for auth.** Use a security scheme (`APIKeyHeader`,
  `OAuth2PasswordBearer`) so the docs and the lock icons work.

### Key takeaways

- Where a parameter comes from is decided by its declaration: path template, simple type →
  query, model → body, `Depends` → dependency.
- `Path`/`Query`/`Body(...)` carry constraints (`ge`, `le`, `min_length`, `pattern`) and
  descriptions that become both 422s and docs.
- Query strings can't carry objects; decode a JSON string (raising 422 yourself) or offer a
  POST.
- Headers used for auth are security schemes, wrapped in dependencies.

---

## Hour 4 — Responses: `response_model`, status codes, `HTTPException`

### What & why

The response side has the same philosophy as the request side: **declare the shape, let the
framework enforce and document it.**

- **`response_model=Schema`** on the decorator: the return value is validated against and
  serialised through the schema — extra attributes are dropped, types coerced, and the doc
  shows the exact shape. Returning an ORM object works when the schema has
  `from_attributes=True` (Hour 7).
- **`status_code=...`** sets the success status (201 for create, 204 for delete).
- **`HTTPException(status_code, detail, headers)`** is _the_ way to refuse: it produces the
  uniform `{"detail": ...}` body every client understands, plus optional headers
  (`WWW-Authenticate`, `Retry-After`).
- **`responses={...}`** on the decorator documents the error responses.

#### Why `HTTPException` and the `except HTTPException: raise` idiom

A handler that wraps everything in `try/except Exception` to return a tidy 500 has a
problem: `HTTPException` _is_ an `Exception`, so a deliberate 404 raised inside the `try` is
caught and turned into a 500. The idiom that fixes it appears dozens of times in this repo,
and it's worth understanding rather than copying:

```python
    try:
        ...
        raise HTTPException(status_code=404, detail="Experiment not found")
        ...
    except HTTPException:
        # Let deliberate 4xx responses through instead of wrapping them in a 500.
        raise
    except Exception as e:
        logger.error(f"Error starting experiment: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
```

The first `except` re-raises what the framework already knows how to render; the second
converts everything else into a logged 500. Without the first clause, a `404` becomes a
`500` with `detail="404: Experiment not found"` — wrong status, wrong message, and a
dashboard that shows "Request failed" instead of "Experiment not found".

#### Why status codes carry meaning here

The platform uses status codes as part of the contract, not decoration. `POST /tracking/assign`
returns **200** for an ineligible user (with `assigned: false` and a `reason`) because "not in
the experiment" is a normal outcome, and **404** only when the experiment isn't active or
doesn't exist — so an SDK can distinguish "render the default" from "log a problem". Login
returns **423** with `Retry-After` for a locked account. A flag that's been killed evaluates
to **200** `enabled: false, reason: inactive`, never 404. Get these wrong and every SDK's error
handling changes.

### In the wild

A create with a 201, a delete with a 204 and documented errors, and a refusal with headers:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
@router.post(
    "/",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create experiment",
    response_description="Returns the created experiment",
)
```

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
@router.delete(
    "/{experiment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete experiment",
    response_description="No content, experiment successfully deleted",
    responses={
        status.HTTP_204_NO_CONTENT: {"description": "Experiment successfully deleted"},
        status.HTTP_403_FORBIDDEN: {"description": "Not enough permissions to delete this experiment", ...},
        status.HTTP_404_NOT_FOUND: {"description": "Experiment not found", ...},
        status.HTTP_400_BAD_REQUEST: {"description": "Invalid experiment status", ...},
    },
)
async def delete_experiment(...) -> None:
```

```python
# 📁 backend/app/api/v1/endpoints/auth.py
    except AccountLockedError as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                "Too many failed login attempts; account temporarily locked. "
                f"Retry in {exc.retry_after_seconds} seconds."
            ),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        )
```

The results endpoint shows a response model doing real work: the service returns a dict, the
endpoint builds a typed response — and a serialisation failure is a 500 with a clear message
rather than a half-rendered JSON:

```python
# 📁 backend/app/api/v1/endpoints/results.py
        response = ExperimentResultsResponse(
            experiment_id=result.get("experiment_id", str(experiment_id)),
            experiment_name=result.get("experiment_name", ""),
            status=raw_status,
            ...
            srm=srm_response,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to serialise results: {exc}")
```

And the SDK-facing tracking endpoint mapping domain errors to codes deliberately —
`ValueError` from the service means the caller's input was wrong (400), anything else is a
500:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"Error assigning user to experiment: {str(e)}")
```

The dashboard's client turns all of these into one `ApiError` because they all share the
`{"detail": ...}` shape — a string, an object, or Pydantic's validation list:

```typescript
// 📁 frontend/src/services/api.ts
/**
 * Error thrown for every failed request. `detail` is the parsed `{"detail": …}`
 * body from FastAPI (string, object or validation list). When the backend
 * returns a typed error such as `{"detail": {"code": "feature_not_licensed"}}`
 * the code is exposed as `code`. `status === 0` means the API was unreachable.
 */
```

> **Watch Out!** Returning a dict that doesn't match `response_model` is a **500** at
> serialisation time, not a 422 — and it happens after your code ran and committed. The
> results endpoint's explicit construction plus `except` is the defensive version. When you
> change a schema, run the API test that hits the endpoint; a unit test on the service won't
> see it.

> **By the Way.** `status_code=204` with a body is a protocol violation; the delete handler
> returns `None`. And `response_model=Dict[str, Any]` (as the evaluate endpoint uses) is
> legitimate when the shape is genuinely dynamic — but it documents nothing, so prefer a model
> whenever the keys are known.

### Try It Yourself

Observe the codes the SDKs depend on:

```bash
# 200 with assigned:false — not an error
curl -s -o /dev/null -w "%{http_code}\n" -X POST $API/tracking/assign -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"experiment_key":"<a targeted experiment>","user_id":"nobody"}'
# 404 — experiment not active / unknown
curl -s -w "\n%{http_code}\n" -X POST $API/tracking/assign -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"experiment_key":"does-not-exist","user_id":"u"}'
# 423 + Retry-After — lock an address on purpose (11 bad logins)
for i in $(seq 1 11); do curl -s -o /dev/null -X POST $API/auth/login -H 'Content-Type: application/json' -d '{"email":"lockme@example.com","password":"x"}'; done
curl -si -X POST $API/auth/login -H 'Content-Type: application/json' -d '{"email":"lockme@example.com","password":"x"}' | grep -i "HTTP/\|retry-after"
```

Then find the idiom: `git grep -n "except HTTPException" backend/app/api | wc -l`.

### Common mistakes

- **`except Exception` without `except HTTPException: raise` first.** Deliberate 4xx become
  500s.
- **A 404 for "not eligible" / "flag off".** Those are 200s with a reason; clients must be
  able to tell "default experience" from "something's wrong".
- **Returning ORM objects from a schema without `from_attributes`.** Hour 7.
- **Hand-built error bodies.** Use `HTTPException` so `detail` is uniform.

### Key takeaways

- `response_model` validates and documents the output; `status_code` sets the success code.
- `HTTPException(status, detail, headers)` is the one way to refuse; `except HTTPException:
  raise` protects it from generic handlers.
- Status codes are contract: 200 + `reason` for normal negatives, 404 for unknown, 423 +
  `Retry-After` for lockout, 422 for malformed.
- A `response_model` mismatch is a 500 at serialisation — test endpoints, not just services.

---

## Part I Workshop

**Q1. Why did the 60/30 experiment never reach the database?**
`ExperimentCreate.validate_variants` (a `model_validator`) raised `ValueError`, which FastAPI
rendered as a 422 before the endpoint ran. The invariant is declared once on the request
model and enforced for every caller.

**Q2. Where is every router mounted, and what two things does the mount add?**
`backend/app/api/api.py`; the mount adds the URL `prefix` and the OpenAPI `tags`.

**Q3. A parameter is declared `user_id: str = Query(...)`. Where does it come from and is it
required?**
The query string; required (`...`). `Query(None)` would make it optional.

**Q4. Why does `POST /tracking/assign` return 200 for a user in the holdout?**
Because not being in the experiment is a normal outcome; the response carries the control
variant, `assigned: false` and `reason: "holdout"`. 404 is reserved for an unknown or inactive
experiment so SDKs can distinguish the cases.

**Q5. What goes wrong if you remove `except HTTPException: raise` from `start_experiment`?**
The 404/400/403 raised inside the `try` are caught by `except Exception` and re-raised as
500 with the original message embedded in `detail` — wrong status, and the dashboard shows a
generic failure.

**Q6. A handler returns a dict missing a required field of its `response_model`. Status?**
500 at serialisation time (a `ResponseValidationError`), after the handler's side effects.

**Quiz.** Which of these is validated by FastAPI before the function runs, and which by your
code: (a) `experiment_id: UUID` in the path, (b) `context` as URL-encoded JSON, (c)
`traffic_allocation` sums to 100?
→ (a) framework (type conversion); (b) your code (`_parse_context_param`, raising the same
422); (c) framework, via the Pydantic validator on the body model.

---

## Exercises

**E1.1 — Declare it.** Write the signature (decorator + parameters, no body) for
`GET /api/v1/experiments/{experiment_id}/assignments` that takes an optional `variant_id`
(UUID), `skip`/`limit` with the same bounds as `list_experiments`, requires a bearer user,
and returns a list of an `AssignmentResponse` model.

**E1.2 — Mount it.** Show the one line in `api/api.py` that would mount a new
`decision_logs.router` under `/decision-logs` with tag `"Decision Logs"`, and the resulting
full path of its `@router.get("/{experiment_id}")`.

**E1.3 — Refuse correctly.** A service raises `ValueError("Experiment has no control
variant")` and `PermissionError` for ownership. Write the `try/except` in an endpoint that
maps them to 400 and 403 respectively, lets deliberate `HTTPException`s through, and turns
anything else into a logged 500.

**E1.4 — Read a 422.** Given this response, say which declaration failed and what the client
should change:

```json
{"detail":[{"type":"less_than_equal","loc":["query","limit"],"msg":"Input should be less than or equal to 500","input":"9999","ctx":{"le":500}}]}
```

**E1.5 — Status design.** For a new `POST /feature-flags/{id}/overrides` endpoint, choose
status codes for: created; flag not found; override already exists for the user; caller
lacks UPDATE permission; malformed body. Justify each in one clause.

---

## Solutions

**S1.1**

```python
@router.get("/{experiment_id}/assignments", response_model=List[AssignmentResponse], summary="List assignments")
async def list_experiment_assignments(
    experiment_id: UUID = Path(..., description="Experiment id"),
    variant_id: Optional[UUID] = Query(None, description="Restrict to one variant"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_active_user),
) -> List[AssignmentResponse]:
    ...
```

**S1.2** — `api_router_v1.include_router(decision_logs.router, prefix="/decision-logs",
tags=["Decision Logs"])`; the full path becomes
`GET /api/v1/decision-logs/{experiment_id}` (the `/api/v1` prefix is added when `main.py`
mounts `api_router`).

**S1.3**

```python
    try:
        result = service.do_thing(...)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e) or "Not enough permissions")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error")
```

Order matters: `HTTPException` first so a deliberate refusal inside `do_thing`'s callers isn't
downgraded; specific domain errors next; the catch-all last (and don't leak `str(e)` of an
unknown exception to clients in production — log it, return a generic message).

**S1.4** — The `limit` query parameter, declared `Query(100, ge=1, le=500)`, received `9999`.
The client should request ≤ 500 per page and paginate with `skip`.

**S1.5** — 201 Created (a new resource); 404 Not Found (the flag doesn't exist — the path
resource is missing); 409 Conflict (the unique `(flag, user)` index would be violated — the
request is well-formed but conflicts with state); 403 Forbidden (authenticated but not
permitted — via `can_update_feature_flag`); 422 Unprocessable Entity (Pydantic refuses the
body before the handler runs).

---

**Next:** [Part II — Pydantic v2 →](./part-2-pydantic.md)

# Part III — Security and Privacy

### Hours 7–9

[← Part 2](./part-2-reliable-systems.md) · [Contents](./README.md) · [Part 4 →](./part-4-safe-change.md)

The most common security mistake in a codebase this size is not a missing check. It is a check that
protects less than its name suggests: a scope called `read` that restricts nothing, a "workspace" that
groups people without separating their data, a column called `retention_expires_at` that no job reads.
This Part teaches you to find the distance between what a control is *called* and what it *does*, by
tracing a request through the code (Hour 7), following data to every place it is copied (Hour 8), and
asking what actually removes it (Hour 9).

Two ground rules. First, this is engineering practice, not certification or legal advice: nothing here
interprets a regulation. Second, each lesson separates **implemented** controls (you can point at the line
or the test) from **recommended practice** (a procedure you must provide). The distinction is the point.
Keep the app clone at v0.36.4 open; the Try It Yourself blocks use the same environment variables as
Part I and run without a database.

---

<a id="hour-7--security-engineering-through-the-request-path"></a>

## Hour 7 — Security engineering through the request path: the scope that checks the key you hold

**Outcome:** review a route's real access boundary, and produce tests that prove both permitted and refused
behaviour, including that a refused mutation leaves no trace.

### What & why

#### Authentication is only the beginning of the decision

Trace a request through six questions: how is the credential extracted, who does it identify, is that
account currently allowed to act, which permission does the route demand, which object does the request
name, and what does the mutation do? "The route has an authentication dependency" answers only the second.
It does not say that *this actor* may do *this action* to *this object*, and it says nothing about what
happens to the object when the answer is no.

Credentials also differ in kind. A user's access token is for people at a keyboard and expires. An API key
is for programs, lasts until deleted, and (at v0.36.4) authenticates only on the endpoints an SDK calls:
the app's key documentation says a key on a management route is refused with `401` and the same body as a
request with no login at all. Do not accept either credential everywhere "because both resolve to a user".

#### A story: the scope that checks the key you hold

API keys carry a comma-separated `scopes` string. Several writers fill it, and the seeds and demos write
literals like `"read,write"`. A tempting design would check whether the key's *owner* holds some key with
the scope. Controlvar's `sdk:ruleset` check deliberately does not: it re-hashes the `X-API-Key` header and
reads **that one row**, so "another key of the same user carrying the scope grants nothing". And the scope
works only while the key's owner can change feature flags, checked on every request, so demoting the owner
takes effect on the next call. The story is the shape of a good boundary: decide on the credential
*presented*, and re-derive the owner's authority every time.

> **Did You Know?** Of all the scope strings a key can carry, exactly one is enforced. The source says so
> in a comment: `sdk:ruleset` is "the one scope name the platform enforces". `read` and `write` are stored,
> parsed and ignored by routes. A label is not least privilege unless a route checks it.

### How Controlvar does it

#### One scope, enforced on the presented key

```python
# 📁 backend/app/api/sdk_scope.py
def scope_refusal(db: Session, api_key: APIKey) -> Optional[str]:
    """Why ``api_key`` may not act as a server-side key, or ``None`` if it may.

    The scope first (:data:`MISSING_SCOPE_DETAIL`), then its owner, read from
    the database by the key's own ``user_id`` (:data:`OWNER_ROLE_DETAIL`:
    missing, inactive, or unable to change feature flags).
    """
    if not has_scope(api_key.scopes, SDK_RULESET_SCOPE):
        return MISSING_SCOPE_DETAIL
    owner = db.query(User).filter(User.id == api_key.user_id).first()
    if (
        owner is None
        or not owner.is_active
        or not check_permission(owner, ResourceType.FEATURE_FLAG, Action.UPDATE)
    ):
        return OWNER_ROLE_DETAIL
    return None
```

Read the order: the scope first, then the owner (loaded from the database by the key's own `user_id`),
who must exist, be active and be able to update feature flags. The module docstring lists the refusals:

```python
# 📁 backend/app/api/sdk_scope.py
Refusals: no or unknown key -> 401 (from ``get_api_key``); a valid key without
the scope -> 403; a scoped key whose owner cannot change feature flags -> 403.
The two 403 texts are the same on every route.
```

401 means "we do not know who you are"; 403 means "we know, and the answer is no". The two 403 texts are
identical on every route, so a caller cannot use the wording to learn which routes exist or which check
failed beyond the documented reason. Now how the scope string is read:

```python
# 📁 backend/app/core/api_key_scopes.py
Membership (:func:`has_scope`) is exact and case-sensitive on the parsed
entries: ``"sdk:ruleset"`` is granted by ``"read, sdk:ruleset "`` but not by
``"SDK:RULESET"``, ``"xsdk:ruleset"`` or ``"sdk:ruleset-ro"``. There is no
prefix, wildcard or substring matching.
```

Exact and case-sensitive after split, strip and dropping empties. Boring on purpose. Hold that thought
for the "Prefer the dumbest thing that works" principle: every clever alternative (prefix matching,
wildcards) is a future bypass.

#### Pin the set of protected routes, and the walk that finds them

A dependency protects only the routes that declare it. So a test pins the exact set:

```python
# 📁 backend/tests/smoke/test_sdk_scoped_routes.py
SCOPED_ROUTES = {
    ("GET", "/api/v1/sdk/ruleset"),
    ("POST", "/api/v1/tracking/evaluations"),
    ("POST", "/api/v1/tracking/assign/batch"),
}
```

A new route that starts requiring the scope, or an existing one that stops, fails
`test_the_scoped_routes_are_exactly_these` until the set is edited deliberately. Notice the next test:

```python
# 📁 backend/tests/smoke/test_sdk_scoped_routes.py
def test_the_walk_sees_the_routes():
    """Guard the guard: an empty walk would make the inventory vacuous."""
    assert len(list(_contexts(app))) > 100
```

"Guard the guard: an empty walk would make the inventory vacuous." The route inventory reads routes through
`effective_route_contexts` because FastAPI keeps `include_router` lazy; a naive walk of `app.routes` finds
almost nothing and would let a set-equality test pass on emptiness. Its sibling for flag mutations says
the same in its docstring: it exists because "two of the routes that change flags once called nothing at
all".

#### Access to a flag is by role, and the decision is one function

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
```

```python
# 📁 backend/app/core/permissions.py
def can_act_on_feature_flag(
    user: Any,
    owner_id: Optional[Any],
    action: Action,
    scope_table: Optional[Dict[UserRole, Dict[Action, Scope]]] = None,
) -> bool:
    """Whether *user* may take *action* on a feature flag owned by *owner_id*.

    The one decision for every per-flag route.  A superuser may always act.
    Otherwise the role must hold *action* on feature flags in ROLE_PERMISSIONS --
    that check comes first, so owning a flag never lets a read-only role change
    it -- and then the scope table decides whose flags the permission covers.

    *owner_id* comes from the stored flag, never from a response dict (which
    renders a missing owner as the string ``"None"``).  *scope_table* exists for
    tests; callers leave it at the default.
    """
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

Read the docstring in the full source: the role matrix is checked *first*, "so owning a flag never lets a
read-only role change it", and only then does the scope table decide whose flags the permission covers.
Today every entry is `ANY`: ADMIN and DEVELOPER can change any flag, ANALYST and VIEWER can read any flag
and change none, even their own. The creator is recorded as `owner_id` and shown, but ownership is not an
access rule. A per-flag lock or approval would be a new entry in that table, and a smoke test pins the exact
set of mutating routes that must call this function.

> **Watch Out!** In tests, "admin" fixtures are often superusers, which bypass every check. A permission
> test written with a superuser proves nothing. Test with a non-superuser of each role, and create the
> resource through the API when ownership matters, since fixtures that write `owner_id` directly hide
> create bugs.

#### Workspaces are teams, not tenants

The workspaces module's own overview says it directly: workspaces "do **not** decide which experiments,
feature flags or API keys a user can see or change." Experiments and flags are not created inside a
workspace, and membership plays no part when they are listed, read or changed; that is decided by the
user's platform role across the whole installation. The recommended answer when teams must not see each
other's experiments and flags is **a separate installation per team**. Do not describe a shared instance
as isolated tenants because it has a workspace dropdown. If you need isolation, specify the resource
ownership model and enforce it at every read, write, export and background-job path *before* you promise it.

#### A convenience that is a hole elsewhere gets two locks

Controlvar has a development shortcut: a synthetic admin that skips sign-in. Look at how narrowly it is
gated:

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

Both conditions must hold (the switch is exactly `True` *and* the environment is `development` or `test`);
"no other environment variable (`TESTING`, `DEBUG` ...) unlocks it"; and `is True` rather than truthiness so
that a mocked settings object (whose attribute would be a truthy `MagicMock`) cannot turn it on. When you
must write a convenience that would be a vulnerability in production, give it two independent locks and
test that each alone does not open it.

> **By the Way.** Error-report routes (`POST /api/v1/tracking/errors`) accept *every* valid API key, but
> the key's authority decides something else: whether the report counts toward automatic rollback.
> `presented_key_is_server_key` applies the same two rules as the scope check without refusing anything.
> A browser key can report errors; only a server-side key can trigger a rollback with them.

#### The ruleset is the sensitive artefact

The flag ruleset a server-side SDK downloads contains **every flag's targeting rules with their values**:
user ids, email domains, plan names. Keep the scoped key on a server. The JavaScript SDK refuses
`evaluation: 'local'` in a browser for that reason. A browser or mobile app's embedded key can be extracted,
so treat that client as untrusted and give it only what the public SDK routes expose; a server-side
integration or an application proxy can narrow that further, which is a recommended architecture, not an
automatic platform boundary. Revoking a key stops future authenticated downloads; it does not recall
rules already in another process. Record the expected refresh and shutdown behaviour rather than promising
instant erasure.

#### An actor/action matrix for a new route

| Scenario | Assertion to make |
|---|---|
| Missing, expired or invalid credential | Fails before protected data is returned (401) |
| Valid user, insufficient permission | Refused (403) and **no mutation occurs** |
| API key on a user-management route | Credential type does not bypass management authentication |
| Valid key without `sdk:ruleset` on a scoped route | 403; no ruleset, no batch side effect |
| Another key of the same owner has the scope | The presented, unscoped key still fails |
| Scoped key whose owner was demoted | Next scoped request is refused |
| Actor changes a resource identifier | Authorisation is evaluated for the requested object |
| Core profile receives a module-only URL | Absence is handled without importing module code |

Assert database state after a refused mutation: a 403 after a commit is a broken boundary. For reads,
assert that bodies and error details do not leak protected fields. For exports, test the endpoint; hiding a
button proves nothing.

### Try It Yourself

Run the scope parsing and the permission decision, with no database:

```bash
python - <<'EOF'
from types import SimpleNamespace
from backend.app.core.api_key_scopes import parse_scopes, has_scope
print(parse_scopes("read, sdk:ruleset ,"))
for stored in ("read, sdk:ruleset ", "read,write", "SDK:RULESET", "sdk:ruleset-ro"):
    print(repr(stored), has_scope(stored, "sdk:ruleset"))

from backend.app.core.permissions import can_act_on_feature_flag, Action
from backend.app.models.user import UserRole
owner = "11111111-1111-1111-1111-111111111111"
viewer_owner = SimpleNamespace(id=owner, role=UserRole.VIEWER, is_superuser=False)
developer = SimpleNamespace(id="22222222-2222-2222-2222-222222222222", role=UserRole.DEVELOPER, is_superuser=False)
print("viewer who owns the flag may update:", can_act_on_feature_flag(viewer_owner, owner, Action.UPDATE))
print("developer who does not own it may update:", can_act_on_feature_flag(developer, owner, Action.UPDATE))
EOF
python -m pytest backend/tests/smoke/test_sdk_scoped_routes.py backend/tests/smoke/test_flag_change_routes_guarded.py backend/tests/unit/core/test_api_key_scopes.py -p no:cov -q
```

Expected result (the pytest run adds its per-test lines; its summary is the last line):

```text
['read', 'sdk:ruleset']
'read, sdk:ruleset ' True
'read,write' False
'SDK:RULESET' False
'sdk:ruleset-ro' False
viewer who owns the flag may update: False
developer who does not own it may update: True
======================== 55 passed, 3 warnings ========================
```

Now tamper with the gate. In your **scratch** clone, open `backend/app/api/v1/endpoints/tracking.py` and
add `Depends(require_sdk_ruleset_key)` (importing it from `backend.app.api.sdk_scope`) to the
`track_event` route's parameters, then run `test_sdk_scoped_routes.py` again. It fails, and the assertion
output lists `('POST', '/api/v1/tracking/track')` under "Extra items in the left set". Revert the edit and it passes. A gate you have only watched pass is
not a gate.

Reasoning exercise. A developer adds `scopes=["read"]` to a browser key and calls it read-only. The same
user's second key has `sdk:ruleset`. What should the reviewer challenge?

**Answer.** `read` is not enforced by any route; it is stored text. Inspect every endpoint the key can
reach rather than inferring restrictions from a label. The browser key must fail the scoped routes on its
own regardless of the second key (the scope check reads the presented row). Prove refusal with integration
tests, check owner demotion, and never ship a scoped ruleset key to a browser. If the product needs a truly
restricted public key, that is a new enforced contract with tests, not documentation.

### Key takeaways

- Authentication names the caller; the route must still check the action and the object.
- Decide on the credential *presented*, and recompute the owner's authority on every request.
- Exactly one scope is enforced. A stored scope string that no route reads is a label.
- Pin the set of protected routes, and test that your inventory walk cannot be empty.
- A workspace groups people; it is not a tenant boundary. Run separate installations for separate teams.

**Review artifact:** an actor/action matrix, one threat flow, and tests proving denied requests leave
protected data and database state untouched.

---

<a id="hour-8--data-inventories-identifiers-and-minimization"></a>

## Hour 8 — Data inventories, identifiers and minimisation: what leaves in a response

**Outcome:** trace the copies an integration creates, reduce payloads without changing the experiment's
unit of assignment, and tell an allowlist from a denylist when data leaves in a response, a log or an audit
row. This is an engineering lesson, not a statement of legal compliance.

### What & why

#### Draw the data flow before you collect

Start at the browser, mobile app or application server, then follow assignment, event collection, storage,
analysis, exports, logs and backups. For LLM features add provider requests and responses, evaluation
records and judge calls. Each destination can create a separately retained copy, and "it is just JSON" does
not make a field harmless: open-ended metadata is where addresses and free text accumulate.

#### Allowlists beat denylists

To keep a secret out of an output, you can list what to hide (a denylist) or list what to show (an
allowlist). A denylist fails *open* for anything you forgot; an allowlist fails *closed*. Controlvar has
examples of both in one codebase, which makes it a good place to learn the difference.

#### A story: the response that echoed secrets

The integrations module stores per-integration configuration, including credentials. Until issue #968 its
list and read responses returned the stored values. The fix is instructive because of how it is built: a
response now carries only seven **named** connection settings, and every other key is withheld, with its
*name* listed in a `stored_secrets` array so a client knows something is stored without seeing the value.
The comment in the source gives the design principle in one sentence: this is "a list of what to show, not
of what to hide, because `encrypted_config` accepts any key: a key nobody listed here is withheld."

> **Watch Out!** The platform's audit-log redactor is the other kind: it hides values whose *key name*
> contains one of a fixed list of words. A field named `email` or `card_number` passes through unchanged.
> The Try It Yourself shows it. A denylist keyed on names is a convenience, not a guarantee.

### How Controlvar does it

#### The inventory, field by field

The assignment stores the external user id and a JSONB `context` used for targeting:

```python
# 📁 backend/app/models/assignment.py
user_id = Column(String(255), nullable=False)  # External user identifier
context = Column(JSONB)  # User attributes used for targeting
```

The event stores identifiers, the event name, a numeric value, free-form metadata and two timestamps:

```python
# 📁 backend/app/models/event.py
value = Column(Float)  # Numeric value if applicable
event_metadata = Column(JSONB)  # Additional data
```

The LLM evaluation record stores the prompt that was sent and the model's answer, as text:

```python
# 📁 backend/app/models/llm_experiment.py
input_variables = Column(JSONB, default=dict)
rendered_prompt = Column(Text, default="")
model_response = Column(Text, default="")
```

Segments made from a list of user ids store those ids in a members table (`segment_members`), a
second copy of identifiers, and the flag ruleset (Hour 7) is a third. Add the derived results, exports,
caches and logs and the picture is: **a user id typed once appears in several places**.

Recommended inventory columns, for every field: purpose, owner, source, destination, readers, retention,
deletion mechanism and verification evidence. Include derived results and exports, not only base tables.

| Destination | What to examine | Engineering question |
|---|---|---|
| Assignment context | Plan, region, targeting attributes | Can the rule work with a coarser category? |
| Event metadata | Page paths, identifiers, free text | Can query strings and customer text be omitted? |
| Ruleset download | Targeting conditions and their values | Which servers receive a complete copy? |
| Logs | Failure details, request metadata | Is the value redacted before it is emitted? |
| Exports | Downloaded results | Who owns access and expiry after download? |
| LLM provider | Prompt, response, judge input | Does the approved data flow allow this transfer? |
| Backups | Historic database state | What happens if a restore reintroduces removed data? |

#### Responses and errors: say the same fixed thing, with an id

Unplanned failures answer a fixed sentence with the request id; the error's own text goes only to the
server log. A test walks the source to enforce it:

```python
# 📁 backend/tests/smoke/test_no_exception_text_in_responses.py
"""No route puts an exception's text in a response.

A failure nobody planned for answers a fixed sentence with the request ID
(``backend.app.core.logger.unexpected_failure`` / ``failure_detail``); the
error's own text goes to the server log only.  This walks the AST of
```

Why does this belong in a privacy lesson? Because an exception message from a database driver can contain
the value that failed to insert, and a validation error can echo what you sent. The request schemas share a
rule for the same reason: refusing text the database cannot store (`NUL` and lone surrogates) answers 422
with a message that names the *field* and "never the value".

#### The audit redactor, as written

```python
# 📁 backend/app/services/audit_log_service.py
# Fields whose values contain sensitive data and should be redacted before logging.
_SENSITIVE_FIELDS = {
    "password",
    "hashed_password",
    "token",
    "api_key",
    "secret",
    "access_token",
    "refresh_token",
}


def _redact_sensitive(data: Optional[Dict]) -> Optional[Dict]:
    """Replace sensitive field values with [REDACTED].

    Performs case-insensitive substring matching so that fields like
    'hashed_password', 'ACCESS_TOKEN', etc. are also caught.
    Recurses into nested dicts.
    """
    if data is None:
        return None
    result = {}
    for k, v in data.items():
        if any(s in k.lower() for s in _SENSITIVE_FIELDS):
            result[k] = "[REDACTED]"
        elif isinstance(v, dict):
            result[k] = _redact_sensitive(v)
        else:
            result[k] = v
    return result
```

It matches substrings case-insensitively and recurses into nested dictionaries, so `hashed_password`,
`ACCESS_TOKEN` and `Access_Token` are caught. It cannot know that `email` is sensitive in *your* data, and
it should not pretend to. If you add fields to an audited object, add the field to your inventory and decide
whether it belongs in an audit snapshot at all.

#### Choose identifiers deliberately

Prefer a stable, opaque application identifier over an email or phone number, and keep the lookup in the
application system that already owns identity. A hash of an email is not anonymous: it stays linkable and
guessable. Scope identifiers to the intended environment or population while preserving the stable unit the
experiment needs. And do not rotate identifiers casually during a running study: changing the effective
`user_id` changes allocation and splits a person's history. Minimisation and statistical consistency need a
shared design. Use the smallest targeting context that implements the rule ("paid tier", not the account
document), and keep tokens, query strings and stack traces out of metadata and event names.

> **By the Way.** One deployment is one environment: flags, experiments and API keys carry no environment
> field. If you want production identifiers kept out of staging, that is a separate deployment with its own
> keys, which is also the cleanest scoping of identifiers you can get.

### Try It Yourself

See an allowlist and a denylist side by side, with no database:

```bash
python - <<'EOF'
from backend.app.services.audit_log_service import _redact_sensitive
print(_redact_sensitive({"name": "x", "api_key": "k",
                         "nested": {"Access_Token": "t", "plan": "pro"},
                         "email": "a@b.c", "card_number": "4242"}))
from backend.app.services.analysis_snapshot_service import purge_expired_history
print(purge_expired_history(retention_days=0))
EOF
```

Expected result:

```text
{'name': 'x', 'api_key': '[REDACTED]', 'nested': {'Access_Token': '[REDACTED]', 'plan': 'pro'}, 'email': 'a@b.c', 'card_number': '4242'}
{'analysis_snapshots': 0, 'bandit_state_history': 0}
```

`email` and `card_number` are not redacted; `api_key` and `Access_Token` are. (The second line previews
Hour 9: a retention of zero disables that purge and deletes nothing.)

Design exercise. The integration sends a customer email, a full account JSON and the checkout URL for a
rule that only tests whether the shopper has a paid plan. Design a smaller payload without changing the
unit of assignment.

**Answer.** Send the stable opaque shopper id and the one attribute the rule needs (`plan: "paid"`).
Remove the email, unrelated account fields and URL query data. Verify the reduced context still produces
the same targeting decision (the targeting adapter evaluates attributes you send; it does not look anything
up). Keep the id-to-person mapping in its authorised source system.

### Key takeaways

- A user id typed once is copied into the assignment, events, segments, rulesets, exports and logs.
- Prefer allowlists. Controlvar's integration responses show only named keys; its audit redactor hides by
  key-name substring and misses everything not named.
- Failures answer a fixed sentence with a request id; exception text goes to the log only, and a test
  enforces it.
- Opaque ids reduce exposed content while staying linkable; they are not anonymity.

**Review artifact:** a purpose/field/destination inventory and before/after synthetic payloads, with an
owner for each retained field.

---

<a id="hour-9--retention-deletion-and-recovery"></a>

## Hour 9 — Retention, deletion and recovery: the column nobody reads

**Outcome:** verify which mechanism enforces a retention policy, describe the real scope of deletion and
export, and plan restore behaviour. This lesson does not interpret jurisdictional requirements.

### What & why

#### A retention policy needs something that runs

A policy document, a retention setting or a `retention_expires_at` column does not remove anything. For each
class of data, find **the job that enforces retention**, its failure signal, the clock it uses and what it
excludes. If you cannot find the job, you have a label.

Also distinguish identities. A dashboard login is a *platform user*. The `user_id` strings in assignments
and events are your application's **external subjects**. Deleting one does nothing to the other. Foreign-key
behaviour decides what a delete does to related rows (cascade, detach or block), and you must read it from
the models rather than assume.

#### A story: the column nobody reads

Controlvar's compliance audit events carry a `retention_expires_at`, stamped at write time from a setting
(365 days for the SOC 2 profile, 730 for ISO 27001) and documented as the time the event "may be
archived/deleted". It is a good column. But a search of the source at v0.36.4 for anything that *reads*
it to delete or archive finds only the model, the schema, the writer and tests. The app's limitations page
says it plainly: "A retention policy, or a tool that erases one person's data: neither exists. Nothing
deletes events, assignments or audit records by age." The only retention that actually runs is a purge of
the platform's own analysis history. That candour is a model for your own documentation: say what runs.

> **Did You Know?** The one retention job is piggybacked on another scheduler's tick: the metrics
> scheduler calls `purge_expired_history()` each time it runs, so its cadence is the metrics scheduler's,
> and a failed purge is logged and reported as zero rather than raised. A retention job that cannot fail
> loudly needs its own alert on "rows older than the window still exist".

### How Controlvar does it

#### The one job that runs

```python
# 📁 backend/app/services/analysis_snapshot_service.py
def purge_expired_history(
    db: Optional[Session] = None,
    retention_days: Optional[int] = None,
) -> Dict[str, int]:
    """
    Delete analysis history older than the retention window.

    Covers ``analysis_snapshots`` (one row per experiment, kind and day from
    the request path) and ``bandit_state_history`` (one row per arm per
    scheduler tick, so the larger of the two). ``retention_days <= 0``
    disables the purge.

    Returns ``{"analysis_snapshots": n, "bandit_state_history": m}``; never
    raises — a failed purge is logged and reported as zero.
    """
    from backend.app.core.config import settings

    days = (
        settings.ANALYSIS_HISTORY_RETENTION_DAYS
        if retention_days is None
        else retention_days
    )
    deleted = {"analysis_snapshots": 0, "bandit_state_history": 0}
    if days <= 0:
        return deleted
```

Read the docstring as a specification: it deletes analysis snapshots and bandit-state history older than
the window (`ANALYSIS_HISTORY_RETENTION_DAYS`, default 90), `retention_days <= 0` disables the purge, and it
"never raises". The last property is a trade-off: it protects the scheduler, and it hides a failure behind a
log line.

> **Watch Out!** `retention_days <= 0` disables the purge. Setting `ANALYSIS_HISTORY_RETENTION_DAYS=0` to
> "keep everything" also stops the cleanup of bandit-state history, which holds one row per arm per
> scheduler tick and is the larger of the two tables the job covers.

#### The stamp that no job consumes

```python
# 📁 backend/app/services/audit_log_service.py
def _get_retention_expiry(standard: str = "soc2") -> datetime:
    """Compute retention expiry date based on compliance standard.

    Args:
        standard: "soc2" (default, 12 months) or "iso27001" (24 months).

    Returns:
        UTC datetime marking when this event may be archived/deleted.
    """
    if standard == "iso27001":
        days = settings.AUDIT_RETENTION_DAYS_ISO27001
    else:
        days = settings.AUDIT_RETENTION_DAYS_SOC2
    return datetime.now(timezone.utc) + timedelta(days=days)
```

Compare the two. One has a doing verb (`delete`), a window, a scheduler and a log. The other computes a
date. Both belong in a design; only one is retention. The app's README says the same of the HIPAA module's PHI access records, which are stamped with a
six-year retention marker: "nothing deletes them".

#### What a delete actually takes with it

Foreign keys decide. Assignments cascade with their experiment; events detach:

```python
# 📁 backend/app/models/assignment.py
experiment_id = Column(
    UUID(as_uuid=True),
    ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="CASCADE"),
    nullable=False,
)
```

```python
# 📁 backend/app/models/event.py
experiment_id = Column(
    UUID(as_uuid=True),
    ForeignKey(f"{get_schema_name()}.experiments.id", ondelete="SET NULL"),
    nullable=True,
)
```

So, by the declared foreign keys, deleting an experiment (allowed only for a DRAFT) removes its assignments
while events that carried its id would survive with the tag nulled. (I read the declarations; I did not
exercise the delete against a database.) Deleting a flag is explicit about it:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
Deleting a feature flag has the following effects:
- The feature flag and its related data are permanently removed, except
  its events: those are kept, with `feature_flag_id` set to null
```

The flag and its related data are permanently removed *except its events*, which "are kept, with
`feature_flag_id` set to null". Removing a platform user's account removes their API keys but keeps the
segments and rollout schedules they created, ownerless, and an experiment whose creator is gone shows "No
owner" (Hour 11). None of those is an erasure of an external subject's events, and none was meant to be.

Because the app does not provide a subject-erasure tool, any manual workflow is yours. The recommended
shape: establish the identifier mapping and authorised scope through your organisation's process; inventory
assignment rows, events, context, segment memberships, LLM records, derived datasets, exports, caches, logs
and backups that may hold it; decide which are in scope and which have independent retention duties;
rehearse in a disposable dataset with before-and-after counts and integrity checks; keep a protected
evidence record that does not reproduce the sensitive payload. Do not publish ad hoc production `DELETE`
statements as a universal recipe: relationships, retention duties and analysis requirements differ.

#### Exports are copies with their own lifecycle

```python
# 📁 backend/app/api/v1/endpoints/export.py
def _require_summary_scope(scope: ExportScope) -> None:
    """Refuse a scope other than ``summary``: no other scope is available."""
    if scope != ExportScope.SUMMARY:
        raise HTTPException(
            status_code=422,
            detail=(
                f"scope={scope.value} is not supported; "
                "the export is available with scope=summary only"
            ),
        )
```

Export endpoints take a user's access token, may be used by every role, and share one rate counter of 10
requests a minute per client address. Only the `summary` scope exists: asking for `events` or `assignments`
answers 422. So an export contains results and experiment-level rows, not raw events. The file still lands
on someone's laptop, where no platform job will ever expire it. Define access, storage and expiry for the
export itself.

#### Restore reintroduces what you removed

Backups are the other half. On the AWS deployment described in the app's disaster-recovery plan, the
database has continuous point-in-time backup with a retention that the CDK sets (35 days in production and
staging, 1 day elsewhere), plus a manual snapshot taken before every deploy's migration. A restore returns
the database to that moment, **including data you deleted since**. Define how approved removals are
re-applied after a recovery *before* the restored service is exposed, document backup expiry and access
controls, and do not claim immediate physical deletion from every historical copy unless storage and
recovery really support it.

#### Local-evaluation copies

A server-side SDK holding a ruleset has a copy of every flag's targeting values (Hour 7). Revoking its key
stops future downloads; it does not undo the last one. Restrict where the key lives and account for
process-held copies in your retention story.

### Try It Yourself

Search the source for a *consumer* of `retention_expires_at`. From the app clone:

```bash
bash -c 'grep -rn "retention_expires_at" backend modules --include="*.py" | grep -v "migrations" | grep -v "/tests/"'
```

Expected result: three lines, the model's column, the schema's field and the one writer. Nothing compares
the column with the clock or deletes by it.

```text
backend/app/models/compliance_audit_event.py:94:    retention_expires_at = Column(DateTime(timezone=True), nullable=True)
backend/app/schemas/compliance_audit.py:102:    retention_expires_at: Optional[datetime] = Field(None)
backend/app/services/audit_log_service.py:158:            retention_expires_at=_get_retention_expiry(retention_standard),
```

A grep is weak evidence of absence (a consumer could build the name at runtime), so say what you did and
did not check. Now find the one retention job that does run:

```bash
grep -n "purge_expired_history" backend/app/core/metrics_scheduler.py
```

Expected result: two lines, the import and the call, which together are the whole list of consumers of that job.

```text
104:                purge_expired_history,
107:            purge_expired_history()
```

If a later release adds a deletion job, a fourth line or a `delete` will appear in the first search, and
this lesson should be rewritten.

Reasoning exercise. A team hashes customer emails, sends full checkout URLs as metadata, deletes a
dashboard account and declares all customer data erased. What is missing?

**Answer.** Hashed emails can remain identifiable; URLs may contain personal data or tokens; platform
accounts and external experiment subjects are different identities; and the delete did not touch linked
assignments, events, segment memberships, LLM records, exports, rulesets or backups. Verify the real
removal and retention mechanisms, define restore handling and record the remaining copies with owners
instead of claiming a platform-wide erasure operation that does not exist.

### Key takeaways

- Retention is a running job with a window, a clock and a failure signal. A date column is not one.
- At v0.36.4 the only retention that runs is the analysis-history purge; nothing deletes events,
  assignments or audit records by age, and no tool erases one subject's data.
- Read the foreign keys: assignments cascade, events detach, a deleted flag keeps its events.
- An export, a downloaded ruleset and a restored backup outlive the live database change.

**Review artifact:** a field-level data map, a minimised payload example, retention-enforcement evidence
and a deletion-and-restore scope statement with named owners.

---

## Part III Workshop

### Q&A

**Why is a workspace not automatically a tenant boundary?** It groups users and gives them workspace roles on
the workspace routes only. Experiments and flags are decided by platform role across the whole installation.

**Why is an expiry column not sufficient retention evidence?** Because something must read it, delete or
archive on a schedule, signal failure and handle restore. Here nothing does.

**Why does the scope check re-hash the header instead of using the authenticated user?** So that another key
of the same owner cannot lend its scope to the key actually presented.

**Why return a fixed sentence with a request id on a 500?** The exception text may contain the data that
failed; the id lets the operator find the real error in the server log.

### Quiz

1. Does a stored `read` scope make every route read-only?
2. Is a hashed email necessarily anonymous?
3. Does deleting a platform login remove an external shopper's events?
4. An allowlist and a denylist both protect a secret. Which fails closed?
5. Name two things that survive a backup restore that you deleted yesterday.

**Answers.** (1) No; only `sdk:ruleset` is enforced. (2) No; it can remain linkable and guessable. (3) No;
platform users and external subjects are separate identities. (4) The allowlist. (5) The deleted rows
themselves in the restored database, and any export or downloaded ruleset taken earlier.

### Exercises

1. **Matrix.** Write the actor/action matrix for `GET /api/v1/sdk/ruleset` with columns for credential,
   expected status and what must be asserted afterwards.
2. **Inventory.** Build the inventory table (purpose, owner, destination, readers, retention, deletion,
   evidence) for one integration you know, including exports and logs.
3. **Retention audit.** For each of assignments, events, audit events, analysis snapshots, flags and API
   keys, state the mechanism that removes it at v0.36.4, or "none".
4. **Redaction.** Propose a change to the audit redactor that fails closed, and list what it would break.

### Solutions

1. No key: 401. Valid key without the scope: 403, no ruleset in the body. Scoped key whose owner is a
   VIEWER or inactive: 403. Scoped key and a capable owner: 200. After each refusal, assert nothing was
   recorded as a ruleset download.
2. A good answer lists `user_id` at least in assignments, events, segment members, rulesets, exports and
   logs, and marks the retention and deletion columns "none" where the platform provides no mechanism.
3. Assignments: cascade with their experiment (a DRAFT experiment may be deleted); events: none by age,
   detached when their experiment or flag is deleted; audit events: none (the date column is not read);
   analysis snapshots and bandit history: the purge after `ANALYSIS_HISTORY_RETENTION_DAYS`; flags: deleted
   by explicit request (events kept); API keys: deleted with their user or explicitly.
4. Replace the denylist by an allowlist of fields an audit snapshot may contain per entity type, failing
   closed on a field it has not been told about. It would break any audit view that relied on seeing
   unclassified fields, and every new audited field would need classification (which is the point).

---

[← Part 2](./part-2-reliable-systems.md) · [Contents](./README.md) · [Part 4 →](./part-4-safe-change.md)

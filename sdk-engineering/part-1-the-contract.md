# Part I — The Contract

### Hours 1–3

[Contents](./README.md) · [Part II →](./part-2-building-a-client.md)

Before a line of client code, the agreement. This Part is the contract every Experimently SDK
implements: the four endpoints and the one rule that governs them (Hour 1); the hash that
every SDK still exports and the golden vectors that pin it, together with the story of why
it's no longer used to decide anything (Hour 2); and the semantics that make fifteen
implementations behave as one — what's cached, what fans out, and what happens when the
network is gone (Hour 3).

---

## Hour 1 — Four endpoints and one rule: the server decides

### What & why

An experimentation SDK has to answer three questions for product code: _which variant is this
user in?_, _is this flag on for this user?_, and _record that this user did X_. Everything
else is convenience. Experimently's contract answers them with four HTTP endpoints,
authenticated with an API key, addressing experiments and flags by their public **keys**:

| Purpose | Method and path | Body / query | Response |
|---|---|---|---|
| Assign a user (sticky) | `POST /api/v1/tracking/assign` | `{experiment_key, user_id, context?}` | `{experiment_key, user_id, variant_id, variant_name, is_control, configuration, assigned, reason?}` |
| Evaluate a flag | `GET /api/v1/feature-flags/evaluate/{flag_key}?user_id=…&context=<url-encoded JSON>` | — | `{key, enabled, config, reason}` |
| Track one event | `POST /api/v1/tracking/track` | `{event_type, event_name?, user_id, experiment_key? \| feature_flag_key?, value?, metadata?, timestamp?}` | stored event |
| Track up to 100 | `POST /api/v1/tracking/batch` | `{events: [...]}` | `{success_count, failure_count, errors}` |

(Two more exist — `GET /feature-flags/user/{user_id}` for all of a user's flags and `GET
/tracking/assignments/{user_id}` for a user's assignments — but they're reads for debugging
and dashboards, not part of the hot path.)

The **one rule**: _the server decides_. An SDK never buckets a user itself. It asks, caches
the answer, and reports events. Three reasons, in order of importance:

1. **Correctness of the science.** Assignment is `md5("{user_id}:{experiment.id}") % 100`
   against the variants' cumulative allocation — but only after the sticky-row check, the
   global holdout, the mutual exclusion group and the targeting gate (Build book Day 4). A
   client that hashes locally can reproduce the arithmetic but not the gates, and it can't
   know about a paused experiment, a changed allocation, or a bandit's current weights. Two
   clients disagreeing about a user's variant is an SRM you'll never find.
2. **One place to fix bugs.** When the `0% conversions` bug was traced to events being
   matched on `event_type` (Build book Day 1), the fix was one file on the server. Had the
   matching logic lived in fifteen SDKs, it would have been fifteen releases.
3. **Configuration is data, not code.** The dashboard's rule editor writes targeting rules;
   the server's `targeting_adapter` evaluates them in three shapes; the SDK sends `context`
   and reads `reason`. Adding an operator to the rules engine changes nothing client-side.

### How Experimently does it

The assign endpoint, with the eligibility semantics spelled out in its docstring:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
async def assign_user_to_experiment(
    request: AssignmentRequest = Body(..., description="Assignment request data"),
    db: Session = Depends(deps.get_db),
    api_key_info: Dict[str, Any] = Depends(deps.get_api_key),
) -> VariantAssignmentResponse:
    """
    Assign a user to an experiment variant.
    ...
    New users are first checked for eligibility: the global holdout, the
    experiment's mutual exclusion group and its targeting rules (evaluated
    against ``context``; top-level keys also answer ``user.<key>`` /
    ``device.<key>`` / ``app.<key>``).  Ineligible users receive HTTP 200 with
    the control variant, ``assigned: false`` and ``reason`` set to
    ``holdout`` | ``mutual_exclusion`` | ``targeting``; nothing is recorded
    for them.  Existing assignments are always returned as-is.

    **Authentication**: Requires a valid API key in the X-API-Key header.
    ...
    Raises:
        HTTPException 401: If the API key is invalid
        HTTPException 404: If the experiment doesn't exist or is not active
    """
    # Get experiment by key
    experiment = (
        db.query(Experiment)
        .filter(
            Experiment.key == request.experiment_key,
            Experiment.status == ExperimentStatus.ACTIVE,
        )
        .first()
    )

    if not experiment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Active experiment with key '{request.experiment_key}' not found",
        )
```

Two contract details that every SDK must honour: an ineligible user is **200, not an
error** (the SDK returns control and doesn't treat it as a failure), and a non-ACTIVE
experiment is **404** (the SDK returns its default variant and _does_ log it). Both are
decisions a local bucketing client could never have made.

Flag evaluation, with the same "inactive is not an error" rule:

```python
# 📁 backend/app/api/v1/endpoints/feature_flags.py
def _evaluate_flag_by_key(db: Session, flag_key: str, user_id: str, context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Shared body of the GET and POST evaluate endpoints.

    A flag that exists but is not ACTIVE (disabled with the kill switch,
    archived, ...) evaluates to ``enabled: false`` with ``reason: "inactive"``
    rather than 404: the client should treat it as "off", not as an error.
    Only an unknown key is a 404.
    """
    db_flag = db.query(FeatureFlag).filter(FeatureFlag.key == flag_key).first()
    if not db_flag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Feature flag with key '{flag_key}' not found")

    feature_flag_service = FeatureFlagService(db)
    evaluation = feature_flag_service.evaluate_flag_detailed(db_flag, user_id, context)
    record_flag_evaluation(flag_key, "enabled" if evaluation["enabled"] else "disabled")

    # ``config`` has always been ``None`` here (the flag dict has no ``value``);
    # kept for backwards compatibility with existing SDKs.
    return {
        "key": flag_key,
        "enabled": evaluation["enabled"],
        "config": None,
        "reason": evaluation["reason"],
    }
```

`record_flag_evaluation` is the line that makes the safety monitor's denominator (Build book
Day 18): every SDK evaluation increments the count that client-reported errors are divided
by. An SDK that cached forever would starve the safety monitor of its baseline — one more
reason the cache has a TTL (Hour 3).

The API key dependency — hashed lookup, user must be active:

```python
# 📁 backend/app/api/deps.py
API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
...
def get_api_key(db: Session = Depends(get_db), api_key_header: str = Depends(API_KEY_HEADER)) -> Optional[User]:
    if not api_key_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key missing", headers={"WWW-Authenticate": "APIKey"})

    # Look up by hashed key only. API keys must never be stored as plaintext.
    api_key_hash = hash_api_key(api_key_header)
    api_key = db.query(APIKey).filter(APIKey.key == api_key_hash).first()
    if not api_key or not api_key.is_valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key", headers={"WWW-Authenticate": "APIKey"})
```

And the tracking side of the contract — the event is resolved to a `variant_id` **at write
time** from the user's assignment row, so the SDK never sends a variant:

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
    if request.experiment_key:
        experiment = db.query(Experiment).filter(Experiment.key == request.experiment_key).first()

        if experiment:
            experiment_id = experiment.id

            # Find variant if user has an assignment
            assignment = (
                db.query(Assignment)
                .filter(Assignment.experiment_id == experiment_id, Assignment.user_id == request.user_id)
                .order_by(Assignment.created_at.desc())
                .first()
            )

            if assignment:
                variant_id = assignment.variant_id
```

That's why the contract's `track` body has `experiment_key` and `user_id` but no
`variant_name`: a client that reported "user X converted in treatment" could be wrong (stale
cache, a bug, a malicious caller); the server's own assignment row can't be.

> **By the Way** — the contract's rate limit is per IP and shared across the SDK paths:
> `SDK_RATE_LIMIT_PER_MINUTE` (default 6,000/min) covers `/tracking/*`,
> `/feature-flags/evaluate/*` and `/feature-flags/user/*`. A server-side SDK behind a single
> NAT can hit it; that's what the 429 handling in Hour 3 is for, and what the cache is for
> before that.

> **Watch Out!** `event_type` vs `event_name`. The SDKs default `event_type` to the
> `event_name` you pass (`"purchase"`), and analysis matches conversions on `event_name`
> (`event_matching.conversion_event_filter`). A client that sent `event_type: "conversion"`
> and left `event_name` empty would produce events that never match a metric. The contract
> asks for `event_name`; the SDKs fill both.

### Try It Yourself

With the stack up and a key from `python backend/scripts/seed_sdk_contract.py` (written to
`tests/sdk-contract/live/.api_key`):

```bash
export K=$(cat tests/sdk-contract/live/.api_key)
# 1. assign (sticky): run it twice, same variant
curl -s -X POST localhost:8000/api/v1/tracking/assign -H "X-API-Key: $K" -H 'Content-Type: application/json' \
  -d '{"experiment_key":"sdk_contract_ab","user_id":"u-hour1","context":{"country":"US"}}' | jq .
# 2. evaluate the flag (100% rollout → enabled, reason "rollout")
curl -s "localhost:8000/api/v1/feature-flags/evaluate/sdk_contract_flag?user_id=u-hour1" -H "X-API-Key: $K" | jq .
# 3. track with a key, then batch
curl -s -X POST localhost:8000/api/v1/tracking/track -H "X-API-Key: $K" -H 'Content-Type: application/json' \
  -d '{"event_type":"purchase","event_name":"purchase","user_id":"u-hour1","experiment_key":"sdk_contract_ab","value":12.5}' | jq .variant_id
# 4. the three non-happy paths
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/api/v1/tracking/assign -H 'Content-Type: application/json' -d '{"experiment_key":"sdk_contract_ab","user_id":"x"}'   # 401
curl -s -X POST localhost:8000/api/v1/tracking/assign -H "X-API-Key: $K" -H 'Content-Type: application/json' -d '{"experiment_key":"nope","user_id":"x"}' | jq .detail             # 404
curl -s "localhost:8000/api/v1/feature-flags/evaluate/nope?user_id=x" -H "X-API-Key: $K" | jq .detail                                                                          # 404
```

Then pause the experiment in the dashboard and repeat step 1: 404 — the SDK's "return
default variant" path.

### Key takeaways

- Four endpoints: assign, evaluate, track, batch — API key in `X-API-Key`, keys not IDs.
- The server decides: gates, stickiness, bandit weights and `variant_id` resolution all live
  in one place; a local bucketer can only be wrong.
- Ineligible = 200 + `assigned:false`; inactive flag = 200 + `reason:"inactive"`; unknown or
  non-ACTIVE experiment = 404. SDKs must distinguish these.
- Evaluations feed the safety monitor's denominator; events match on `event_name`.

---

## Hour 2 — The consistent hash: what it's for, what it's no longer for, and the golden vectors

### What & why

Every Experimently SDK exports a function — `consistent_hash` / `consistentHash` /
`ConsistentHash.compute` / `hashUser` — that maps `(user_id, key)` to a float in `[0, 1)`:

1. Concatenate `"{user_id}:{key}"`, UTF-8 encode.
2. MD5 digest (16 bytes).
3. Read the **first 4 bytes as a little-endian unsigned 32-bit integer**.
4. Divide by **2³²** (4,294,967,296 — _not_ 2³² − 1).

Until September 2026 that function _was_ the SDK: a client fetched a flag's rollout
percentage and decided `hash < percentage/100` locally, and several SDKs assigned experiment
variants the same way. The rewiring (PR #67) moved every decision to the server. The hash
stayed, for two reasons: **compatibility** (integrations had built on it, and offline tooling
uses it to reproduce buckets), and **parity testing** — it's a small, pure, language-neutral
function that's easy to get subtly wrong (endianness, the divisor, signed vs unsigned), so it
makes a perfect canary for "did this port get the details right?"

Hence the **golden vectors**: a JSON file of `(user_id, flag_key) → md5_hex, expected_hash`
pairs, plus rollout inclusion vectors at 3/5/50/100%, that every SDK's unit tests check.

### How Experimently does it

The vectors, with the algorithm written into the file so no reader needs the docs:

```json
// 📁 tests/sdk-contract/golden-vectors.json
{
  "description": "Cross-SDK contract test vectors for consistent hashing. All SDKs MUST produce identical results for these inputs.",
  "algorithm": {
    "name": "MD5 consistent hash",
    "steps": [
      "1. Concatenate: '{userId}:{flagKey}'",
      "2. UTF-8 encode the string",
      "3. Compute MD5 digest (16 bytes)",
      "4. Read first 4 bytes as LITTLE-ENDIAN unsigned 32-bit integer",
      "5. Divide by 2^32 (4294967296) to normalize to [0.0, 1.0)"
    ],
    "divisor": 4294967296
  },
  "hash_vectors": [
    {
      "user_id": "user-123",
      "flag_key": "my-flag",
      "md5_hex": "43bc57b1e81dec71c5242122ac05170f",
      "expected_hash": 0.6927449859213084,
      "description": "Primary cross-SDK verification vector"
    },
    {
      "user_id": "alice",
      "flag_key": "dark-mode",
      "md5_hex": "f2150f094734044dddc127979b83d1b7",
      "expected_hash": 0.0353864398784935,
      "description": "Low hash value — included in 5%+ rollouts"
    },
```

The reference implementation, with the "utility only" warning at the top:

```python
# 📁 sdk/python/experimentation/hashing.py
"""Cross-SDK MD5 consistent hash.

This is exported as a utility so that ``{user_id}:{flag_key}`` buckets can be
reproduced offline (and verified against ``tests/sdk-contract/golden-vectors.json``).
Nothing in the client uses it to decide a variant: assignment and flag
evaluation are always made by the server.
"""
_HASH_DIVISOR = 4294967296  # 2 ** 32


def _digest(user_id: str, flag_key: str) -> bytes:
    combined = f"{user_id}:{flag_key}".encode("utf-8")
    return hashlib.md5(combined, usedforsecurity=False).digest()  # noqa: S324


def consistent_hash(user_id: str, flag_key: str) -> float:
    """Return the bucket for ``(user_id, flag_key)`` as a float in ``[0.0, 1.0)``."""
    (value,) = struct.unpack("<I", _digest(user_id, flag_key)[:4])
    return value / _HASH_DIVISOR
```

Three ports, three ways of saying "little-endian uint32":

```ts
// 📁 sdk/js/src/hash.ts
/** 2^32 — the divisor shared by every SDK (NOT 2^32 - 1). */
const HASH_DIVISOR = 4294967296;

export function consistentHash(userId: string, flagKey: string): number {
  const digest = md5Bytes(`${userId}:${flagKey}`);
  const uint32 = new DataView(digest.buffer).getUint32(0, true);
  return uint32 / HASH_DIVISOR;
}
```

```go
// 📁 sdk/go/hash.go
const hashDivisor = float64(0x100000000)

func ConsistentHash(userID, key string) float64 {
	input := userID + ":" + key
	digest := md5.Sum([]byte(input)) //nolint:gosec
	v := binary.LittleEndian.Uint32(digest[:4])
	return float64(v) / hashDivisor
}
```

```java
// 📁 sdk/java/core/src/main/java/com/experimentationplatform/sdk/ConsistentHash.java
            // Little-endian unsigned 32-bit integer from the first 4 bytes
            // (Python: struct.unpack('<I', digest[:4])[0]).
            long hash = ((bytes[0] & 0xFFL))
                      | ((bytes[1] & 0xFFL) << 8)
                      | ((bytes[2] & 0xFFL) << 16)
                      | ((bytes[3] & 0xFFL) << 24);
            return hash / (double) HASH_DIVISOR;
```

The Java port shows why the vectors exist: Java has no unsigned types, so each byte must be
masked (`& 0xFFL`) before shifting, and the accumulator must be a `long`. Get one mask wrong
and the hash is right for ~half of all inputs — exactly the kind of bug a handful of vectors
catches and a "does it look random?" test never would. The JS SDK carries its own
`md5.ts` (182 lines) because browsers have no synchronous MD5 in the platform, and `crypto`
isn't available at the edge; the Swift SDK uses CommonCrypto's `CC_MD5`.

The golden-vector tests are the offline half of the contract suite — the `SDK Contract Tests`
job in the PR gate:

```python
# 📁 tests/sdk-contract/test_python_sdk.py
class TestRolloutVectors:
    """Validate rollout inclusion/exclusion matches golden vectors."""

    @pytest.mark.parametrize("vector", load_golden_vectors()["rollout_vectors"], ids=lambda v: f"rollout_{v['user_id']}:{v['flag_key']}")
    def test_rollout_inclusion(self, vector):
        h = compute_hash(vector["user_id"], vector["flag_key"])

        if "rollout_3_percent" in vector:
            included = h < 0.03
            assert included == vector["rollout_3_percent"], (...)
```

**What the server actually uses.** Here's the thing the rewiring made explicit: the server's
_own_ hashes are different functions. Experiment assignment is `md5("{user_id}:{experiment.id}")`
as a 128-bit integer `% 100` (the experiment's UUID, not its key); flag rollout is
`md5("{user_id}:{flag.key}")` `% 100 < rollout_percentage`:

```python
# 📁 backend/app/services/assignment_service.py
        # Create a hash using user ID and experiment ID
        hash_input = f"{user_id}:{experiment.id}"
        hash_value = int(
            hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest(), 16
        )
```

```python
# 📁 backend/app/services/feature_flag_service.py
        bucket = hash_value % 100
```

So an SDK's `consistent_hash(user, flag_key) < 0.05` and the server's `% 100 < 5` are
_correlated but not identical_ buckets (first-4-bytes-LE ÷ 2³² vs full-digest mod 100). When
SDKs decided locally, a user could be in the 5% on the client and out of it on the server —
and the dashboard's counts (server-side) disagreed with what users saw. That's the concrete
harm behind "the server decides."

> **Did You Know?** The Lambda assignment service (Hour 9) has a _third_ implementation:
> `md5("{user_id}:{experiment_key}_variant")[:4]` little-endian ÷ (2³² − 1) — the SDK's
> byte order with a suffixed salt and an off-by-one divisor, walked against the variants'
> cumulative allocation. It agrees with neither the SDK hash nor the backend's. The drift is
> documented, not fixed; Hour 9 is about living with it.

### Try It Yourself

```bash
cd /path/to/experimentation-platform
python -m pytest tests/sdk-contract/test_python_sdk.py -q -o addopts=""
node tests/sdk-contract/test_js_sdk.js
python - <<'EOF'
import hashlib, struct, json
v = json.load(open("tests/sdk-contract/golden-vectors.json"))["hash_vectors"][0]
d = hashlib.md5(f"{v['user_id']}:{v['flag_key']}".encode()).digest()
print(d.hex() == v["md5_hex"], struct.unpack("<I", d[:4])[0] / 2**32 == v["expected_hash"])
print("big-endian would give", struct.unpack(">I", d[:4])[0] / 2**32)      # the classic port bug
print("server flag bucket   ", int(d.hex(), 16) % 100)                     # a different function
EOF
```

Then write the hash in a language you know that isn't in `sdk/` (bash with `md5sum` and
`xxd` works) and check it against three vectors.

### Key takeaways

- The hash: `md5("{user}:{key}")`, first 4 bytes little-endian, ÷ 2³². Exported everywhere,
  used for decisions nowhere.
- Golden vectors pin `md5_hex`, `expected_hash` and rollout inclusion; they run offline in
  the PR gate and catch endianness/signedness/divisor bugs a port is prone to.
- The server's own buckets (`% 100` on the full digest, UUID-salted for experiments) are
  different functions — the reason local bucketing was retired.

---

## Hour 3 — Caching, fan-out and failure policy: the semantics every SDK shares

### What & why

If the server decides, an SDK's design questions are all about the _in-between_: how long to
believe an answer, what to do while waiting, what to send when the caller didn't say which
experiment an event belongs to, and what to do when the server is unreachable. The rewiring
fixed one answer to each, in the SDK guide, and every port implements it:

**Cache.** Successful assignments and evaluations are cached **per user + key** for
`cache_ttl` (default 300 s; the edge SDK 60 s). **Failures are never cached** — a transient
error must not pin a user to "control" for five minutes. Concurrent calls for the same user +
key share one in-flight request (where the language makes that natural).

**Fan-out.** `track(user, event)` with an `experiment_key` or `feature_flag_key` sends one
`/track`. With **no key**, the SDK sends one `/batch` containing one entry per cached
assignment and one per cached evaluated flag for that user — "attribute this event to
everything this client has shown this user." If nothing is cached, **nothing is sent**.

**Failure policy.** The "detailed" methods (`get_assignment`, `get_feature_flag`,
`get_all_flags`) **raise** a typed error carrying the HTTP status; the convenience wrappers
(`get_variant`, `is_feature_enabled`) **swallow** it and return a safe default (`"control"`,
`False`); `track` / `track_batch` **never raise**. A 429 is retried **once** after
`Retry-After` (capped).

Why these particular answers? The cache TTL trades staleness for load: five minutes means a
paused experiment keeps serving its variant for at most five minutes, and a server-side SDK
handling 10k requests/minute makes ~1 assign call per user per 5 minutes instead of 10k. Never
caching failures is the difference between "the API blipped" and "half our users were in
control for the afternoon." The fan-out rule exists because product code that fires
`track("purchase")` from a checkout page usually has no idea which of six experiments touched
the funnel — but the SDK does, because it served them. And raise-vs-swallow gives the
integrator a choice: "I need to know" (`get_assignment`, handle the error) or "just give me
something safe" (`get_variant`).

### How Experimently does it

The Python cache — one lock, per-user dict of per-key `(expires_at, value)`; expired entries
are dropped on read:

```python
# 📁 sdk/python/experimentation/cache.py
"""Thread-safe per-user, per-key TTL cache. Only successful results are ever stored."""

class UserKeyCache(Generic[T]):
    """``user_id -> key -> (expires_at, value)`` guarded by a single lock."""

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self._users: Dict[str, Dict[str, Tuple[float, T]]] = {}

    def get(self, user_id: str, key: str) -> Optional[T]:
        with self._lock:
            by_key = self._users.get(user_id)
            if not by_key:
                return None
            entry = by_key.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if _now() >= expires_at:
                del by_key[key]
                return None
            return value
    ...
    def entries(self, user_id: str) -> List[Tuple[str, T]]:
        """All live (unexpired) entries for a user, in insertion order."""
```

`entries(user_id)` is the fan-out's data source. The client keeps two of these — one for
assignments, one for flags — and `track` reads both:

```python
# 📁 sdk/python/experimentation/client.py
    def track(self, user_id, event_name, event_value=None, properties=None, experiment_key=None, feature_flag_key=None, event_type=None, timestamp=None) -> bool:
        """Record an event. Never raises.

        * With ``experiment_key`` and/or ``feature_flag_key``: one
          ``POST /api/v1/tracking/track``.
        * Without a key: one ``POST /api/v1/tracking/batch`` (chunked at 100)
          containing one entry per cached assignment (``experiment_key``) plus
          one per cached evaluated flag (``feature_flag_key``) for this user.
          If nothing is cached for the user, nothing is sent and ``False`` is
          returned.

        ``event_type`` defaults to ``event_name``. Returns ``True`` only when
        the server accepted the event(s).
        """
        try:
            base = self._event_body(user_id, event_name, event_value, properties, event_type, timestamp)
            if experiment_key or feature_flag_key:
                body = dict(base)
                if experiment_key:
                    body["experiment_key"] = experiment_key
                if feature_flag_key:
                    body["feature_flag_key"] = feature_flag_key
                self._request("POST", "/api/v1/tracking/track", body=body)
                return True

            events: List[Dict[str, Any]] = [
                dict(base, experiment_key=assignment.experiment_key)
                for assignment in self.cached_assignments(user_id)
            ]
            events.extend(dict(base, feature_flag_key=flag.key) for flag in self.cached_flags(user_id))
            if not events:
                logger.debug("track(%s, %s): nothing cached for user; nothing sent", user_id, event_name)
                return False
            return self.track_batch(events).ok
        except Exception as exc:  # noqa: BLE001 - fire-and-forget
            logger.warning("track(%s, %s) failed: %s", user_id, event_name, exc)
            return False
```

The same rule in Go, where the cache is a single LRU and entries carry their user so
`Range` can find them:

```go
// 📁 sdk/go/client.go
	// Track records an analytics event. With ExperimentKey or FeatureFlagKey set
	// it sends one POST /api/v1/tracking/track. Without a key it fans the event
	// out via POST /api/v1/tracking/batch to every experiment the user was
	// assigned to and every flag evaluated for the user by this client (from the
	// cache); if nothing is cached, nothing is sent. Track never panics; the
	// returned error is informational and safe to ignore.
	Track(ctx context.Context, event *TrackEvent) error
```

Raise vs swallow, side by side:

```python
# 📁 sdk/python/experimentation/client.py
    def get_variant(self, experiment_key: str, user_id: str, user_attributes=None) -> str:
        """``get_assignment(...).variant_name``, or ``default_variant`` on any failure."""
        try:
            return self.get_assignment(experiment_key, user_id, user_attributes).variant_name
        except ExperimentationError as exc:
            logger.warning("get_variant(%s, %s) failed: %s", experiment_key, user_id, exc)
            return self.default_variant
    ...
    def is_feature_enabled(self, flag_key: str, user_id: str, user_attributes=None) -> bool:
        """``get_feature_flag(...).enabled``, or ``False`` on any failure."""
        try:
            return self.get_feature_flag(flag_key, user_id, user_attributes).enabled
        except ExperimentationError as exc:
            logger.warning("is_feature_enabled(%s, %s) failed: %s", flag_key, user_id, exc)
            return False
```

and the error type that makes "raise" useful — status and body attached, `None` status for
network failures:

```python
# 📁 sdk/python/experimentation/types.py
class ExperimentationError(Exception):
    """Raised by ``get_assignment``, ``get_feature_flag``, ``get_all_flags`` and
    ``get_assignments`` when the API call fails.

    Attributes:
        status: HTTP status code of the failed response, or ``None`` for
            network errors and timeouts.
        body: Raw response body (text) when a response was received.
    """
```

The 429 policy — one retry, `Retry-After` honoured and capped:

```python
# 📁 sdk/python/experimentation/client.py
#: Upper bound honoured for a ``Retry-After`` header on 429.
MAX_RETRY_AFTER_SECONDS = 5.0
#: Wait used when a 429 carries no (parseable) ``Retry-After`` header.
DEFAULT_RETRY_AFTER_SECONDS = 1.0
...
        response = self._send(method, url, data)
        if response.status == 429:
            wait = _retry_after_seconds(response)
            logger.debug("429 from %s %s; retrying once after %.1fs", method, path, wait)
            self._sleep(wait)
            response = self._send(method, url, data)
```

Why once and not exponential backoff with jitter? Because the client is on a request path
(a page render, an API handler) with a 5-second budget; a second 429 means the caller should
get its default and move on, and the cache will absorb the retry storm. A background event
pipeline would make a different choice; this SDK is not one.

> **Watch Out!** The cache is keyed by user + key, **not** by attributes. If you change a
> user's attributes (they upgrade to `plan: pro`) and evaluate a flag whose targeting rule
> depends on it, you'll get the cached pre-upgrade answer for up to five minutes. The docs
> say it in every SDK: "attributes are assumed stable per user; call `clear_cache()` after
> changing them." The React provider does this for you by re-keying on the attributes'
> JSON (Hour 6).

> **By the Way** — "nothing cached → nothing sent" surprises people. A server-side SDK that's
> just been restarted has an empty cache; the first `track("purchase")` without a key sends
> nothing and returns `False`. It's the right behaviour — the SDK genuinely doesn't know what
> the user was shown — and the fix is to pass `experiment_key` when you know it, or to
> assign/evaluate before tracking in the same process. The contract smoke (Hour 11) tests
> exactly this ordering.

### Try It Yourself

Offline, with the fake transport (Hour 10 has the full tour):

```python
>>> from experimentation import ExperimentationClient
>>> from experimentation.testing import FakeTransport, make_response
>>> t = FakeTransport().route("POST", "/api/v1/tracking/assign", json={"experiment_key": "exp", "user_id": "u", "variant_name": "treatment", "is_control": False, "variant_id": "v1", "configuration": None})
>>> t.route("GET", "/api/v1/feature-flags/evaluate/f", json={"key": "f", "enabled": True, "config": None, "reason": "rollout"})
>>> t.route("POST", "/api/v1/tracking/batch", json={"success_count": 2, "failure_count": 0, "errors": []})
>>> c = ExperimentationClient("http://test", "key", transport=t)
>>> c.track("u", "purchase")                       # nothing cached → False, and no request
False
>>> len(t.requests)
0
>>> c.get_variant("exp", "u"), c.is_feature_enabled("f", "u")
('treatment', True)
>>> c.get_variant("exp", "u"); len(t.requests)     # cached: still 2 requests
2
>>> c.track("u", "purchase")                       # fans out: one /batch with 2 events
True
>>> t.last.path, [e.get("experiment_key") or e.get("feature_flag_key") for e in t.last.json()["events"]]
('/api/v1/tracking/batch', ['exp', 'f'])
>>> t.route("POST", "/api/v1/tracking/assign", status=500, json={"detail": "boom"})
>>> c.clear_cache(); c.get_variant("exp", "u")     # swallowed → default
'control'
>>> c.get_assignment("exp", "u")                   # raised → status 500
Traceback (most recent call last): ...
experimentation.types.ExperimentationError: POST /api/v1/tracking/assign failed with HTTP 500
>>> c.cached_assignments("u")                      # failure not cached
[]
```

### Key takeaways

- Cache per user + key, TTL 300 s (edge 60 s), successes only, expired-on-read; attributes
  are assumed stable (clear the cache when they change).
- Fan-out: keyed events → `/track`; key-less → one `/batch` over cached assignments + flags;
  nothing cached → nothing sent.
- Detailed methods raise a typed error with `status`; convenience wrappers return
  `"control"` / `False`; `track` never raises; 429 retried once after `Retry-After`.
- These semantics, not the language, are the SDK. Part III is about keeping them identical.

---

## Part I Workshop

**Q1. A product engineer wants to compute the variant client-side "to save a round trip."
What's the one-paragraph answer?**
The server's assignment isn't just a hash: it's sticky rows, holdout, mutual exclusion,
targeting, bandit weights and the experiment's status. A local hash reproduces none of them
and uses a different bucket function anyway. Use the cache: one round trip per user per five
minutes.

**Q2. `POST /tracking/assign` returns 200 with `assigned: false, reason: "targeting"`. Is that
an error the SDK should surface?**
No. The user is ineligible and gets control; the SDK returns the assignment normally (with
`is_control: true`) and logs nothing. Only 4xx/5xx are errors.

**Q3. Why does the `track` body carry no variant?**
The server resolves `variant_id` from the user's assignment row at write time; a client-sent
variant could be stale or wrong. The SDK sends `user_id` and `experiment_key`.

**Q4. A port's hash passes the "primary" vector but fails `alice:dark-mode`. What's the likely
bug?**
Signedness. A byte ≥ 0x80 in the first four is sign-extended (Java without `& 0xFF`, or a
signed `int32` read) — it only shows on inputs whose digest has a high bit set.

**Q5. A server-side SDK restarts and immediately handles a checkout that calls
`track("purchase")` with no key. What happens?**
Nothing is sent (`False`): the cache is empty so there's nothing to fan out to. Pass the
experiment key, or assign before tracking.

**Q6. Should a 429 be retried with exponential backoff?**
Not here: the client is on a request path with a ~5 s budget. One retry after `Retry-After`
(capped at 5 s), then the default. The cache limits how often 429s happen at all.

**Quiz.** A flag is disabled with the kill switch. What does the SDK return, and is it cached?
→ `enabled: false, reason: "inactive"` — a successful evaluation, so yes, it's cached for
the TTL. Re-enabling the flag takes up to five minutes to reach cached users.

---

## Exercises

**E1.1 — Contract by hand.** Using only `curl` and the seeded fixtures, produce a sticky
assignment, a flag evaluation, a keyed track, and a key-less batch that references both — and
show with SQL that the tracked event's `variant_id` matches the assignment row.

**E1.2 — Hash port.** Implement the consistent hash in bash (`printf | md5sum`, then `xxd`
and arithmetic) and validate against all `hash_vectors`. Then break it (big-endian) and see
which vectors fail.

**E1.3 — Cache semantics.** With `FakeTransport`, demonstrate: (a) a 404 on assign is not
cached and the next call hits the transport; (b) an evaluation with `reason: "inactive"` _is_
cached; (c) after `cache_ttl_seconds=0.1` and a `sleep(0.2)`, the next call refetches.

**E1.4 — Fan-out limits.** Cache 150 flags for one user and call `track` without a key;
count the batch requests and their sizes.

**E1.5 — Rate limit.** Route `/tracking/assign` to a 429 with `Retry-After: 2`, then to 200
via the FIFO queue; show one sleep of 2 s and a successful assignment. Then `Retry-After:
60` → the wait is capped at 5 s.

---

## Solutions

**S1.1** — after the four `curl`s from Hour 1's Try It Yourself:

```sql
SELECT e.event_name, e.variant_id, a.variant_id AS assigned
FROM experimentation.events e
JOIN experimentation.assignments a ON a.user_id = e.user_id AND a.experiment_id = e.experiment_id
WHERE e.user_id = 'u-hour1' ORDER BY e.created_at DESC LIMIT 3;
```

The two columns match on every row — the server wrote `variant_id` from the assignment.

**S1.2**

```bash
h() { local hex; hex=$(printf '%s:%s' "$1" "$2" | md5sum | cut -c1-8)
     local le=$(echo "$hex" | sed 's/\(..\)\(..\)\(..\)\(..\)/\4\3\2\1/')
     python -c "print(int('$le', 16) / 2**32)"; }
h user-123 my-flag     # 0.6927449859213084
```

Drop the byte-swap `sed` and you get big-endian: the "primary" vector fails immediately
(0.264… instead of 0.692…); every vector fails, because MD5's first and fourth bytes are
never equal by accident.

**S1.3**

```python
t = FakeTransport(); c = ExperimentationClient("http://t", "k", transport=t, cache_ttl_seconds=0.1)
t.route("POST", "/api/v1/tracking/assign", status=404, json={"detail": "nope"})
c.get_variant("e", "u"); c.get_variant("e", "u"); assert len(t.requests) == 2          # (a)
t.route("GET", "/api/v1/feature-flags/evaluate/f", json={"key": "f", "enabled": False, "config": None, "reason": "inactive"})
c.is_feature_enabled("f", "u"); c.is_feature_enabled("f", "u"); assert len(t.requests) == 3   # (b) cached
import time; time.sleep(0.2); c.is_feature_enabled("f", "u"); assert len(t.requests) == 4     # (c) expired
```

**S1.4** — 150 cached flags → `track` builds 150 events → `track_batch` chunks at
`BATCH_LIMIT = 100` → two `/batch` requests of 100 and 50; `BatchResult` sums both.

**S1.5**

```python
t = FakeTransport(); c = ExperimentationClient("http://t", "k", transport=t)
slept = []; c._sleep = slept.append
t.respond(429, headers={"Retry-After": "2"}); t.respond(200, json={"variant_name": "control", "is_control": True})
c.get_variant("e", "u"); assert slept == [2.0] and len(t.requests) == 2
t.respond(429, headers={"Retry-After": "60"}); t.respond(200, json={"variant_name": "control", "is_control": True})
c.clear_cache(); c.get_variant("e", "u"); assert slept[-1] == 5.0
```

---

**Next:** [Part II — Building a Client →](./part-2-building-a-client.md)

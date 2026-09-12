# Week 3 — The Hard Parts

### Days 15–21 · Flags, the Rules Engine, Schedulers, Safety, Statistics, SDKs

[← Week 2](./week-2-api-and-data.md) · [Contents](./README.md) · [Week 4 →](./week-4-production.md)

Weeks 1 and 2 were the request/response spine. This week is everything that makes Experimently
more than a CRUD app over five tables: flags that turn on for 5% of users and then 25%, targeting
rules with twenty operators and three storage shapes, five background loops that must not race
across replicas, a safety net that rolls a flag back when the crash rate spikes, a statistics
engine that lets you peek at results without lying to yourself, and fifteen SDKs that all speak
one contract. By Day 21 you'll narrate a crash spike turning into an automatic rollback.

> **The one sentence to carry through Week 3.** _Everything "clever" in the platform is a
> deterministic function over the same two tables plus a small amount of persisted state — a
> flag decision is `md5(user_id:flag.key) % 100 < percentage`, a rule match is a pure function
> of a normalised context, a scheduler tick is `SELECT … WHERE due` under an advisory lock, a
> rollback is a threshold over the last 15 minutes of error logs, a bandit weight is a seeded
> Monte Carlo over conversion counts — and the cleverness is in getting the inputs right, not
> in the arithmetic._

| Day | Topic                              | The one thing it's really about                                     |
| --- | ---------------------------------- | ------------------------------------------------------------------- |
| 15  | Feature flags                      | `enabled` + `reason`, and a hash on `flag.key` instead of an id     |
| 16  | The rules engine & adapter         | Three storage shapes, one engine, and `expand_context`'s aliases    |
| 17  | Schedulers & the advisory lock     | Five loops, one `run_locked_tick`, and the naive-datetime bug       |
| 18  | Safety & auto-rollback             | Error logs ÷ evaluations over 15 minutes, then `rollback_percentage` |
| 19  | The statistics engine              | Sequential, CUPED, Bayesian, bandits — as blocks beside the result  |
| 20  | SDKs & the contract                | Four endpoints, server-decides, MD5 golden vectors, fan-out         |
| 21  | **Capstone:** StreamPulse rollback | A crash spike through errors → safety → scheduler → flag → SDK      |

---

## Day 15 — Feature flags: evaluation, rollout percentages, and `reason`

### What & why

An experiment asks "which arm?"; a **feature flag** asks "on or off for this user?" — and the
answer has to be explainable, because the person debugging "why don't I see the new player?"
needs to know whether it was the kill switch, a targeting rule, or the rollout dice. So the
flag service returns not just `enabled` but a **`reason`** — `targeting_rule`, `rollout`,
`inactive` or `error` — and the SDKs surface it.

Three properties shape the design:

1. **A flag is keyed by its `key`, and so is its hash.** `md5("{user_id}:{flag.key}") % 100 <
   rollout_percentage`. Using the key rather than the UUID means the bucket is stable across
   environments that share flag keys (staging and production seeded from the same config), and
   it's reproducible from an SDK's consistent-hash utility for debugging.
2. **Gradual rollout is monotone by construction.** A user in bucket 7 is in the 5% rollout,
   the 25% rollout, the 50% rollout and the 100% rollout — moving the percentage up never
   takes the feature away from anyone who has it, and moving it down (a rollback) removes it
   from the most recently added users first. There's no per-user state; the percentage _is_
   the state.
3. **Inactive is "off", not "not found".** A flag that exists but is `INACTIVE` (the kill
   switch) evaluates to `enabled: false, reason: "inactive"` with HTTP 200. Only an unknown key
   is a 404. The client should treat a killed feature as off, never as an error to log.

### How Experimently does it

The endpoint is the same shape as `/tracking/assign` — API key, key lookup, service call —
with a GET and a POST variant (the POST exists for contexts too large for a query string):

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
    return {"key": flag_key, "enabled": evaluation["enabled"], "config": None, "reason": evaluation["reason"]}
```

The `context` query parameter is URL-encoded JSON (the SDKs send `user.attributes` this way;
`_parse_context_param` returns a 422 if it isn't a JSON object). The service does the
decision:

```python
# 📁 backend/app/services/feature_flag_service.py
    def evaluate_flag_detailed(self, flag: FeatureFlag, user_id: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Evaluate a feature flag for a user and explain the outcome.
        ...
        Returns:
            ``{"enabled": bool, "reason": "targeting_rule" | "rollout" | "inactive" | "error", "rule_id": Optional[str]}``
        """
        start_time = time.time()
        targeting_rule_id: Optional[str] = None
        result = False
        reason = REASON_ROLLOUT
        error = None

        try:
            # If flag is not active, return False
            # Handle both enum member (FeatureFlagStatus.ACTIVE) and string value ("ACTIVE")
            if flag.status not in (FeatureFlagStatus.ACTIVE, FeatureFlagStatus.ACTIVE.value):
                result = False
                reason = REASON_INACTIVE
            else:
                matched = self._match_targeting_rule(flag, user_id, context or {})
                if matched is not None:
                    targeting_rule_id, rule_percentage = matched
                    reason = REASON_TARGETING_RULE
                    result = self._evaluate_percentage_rollout(flag, user_id, rule_percentage)
                else:
                    # No rules, no match, or an uninterpretable shape: global rollout
                    reason = REASON_ROLLOUT
                    result = self._evaluate_percentage_rollout(flag, user_id)

        except Exception as e:
            error = str(e)
            logger.error(f"Error evaluating flag {flag.key} for user {user_id}: {error}")
            # Default behavior on error is to return False
            result = False
            reason = REASON_ERROR

        finally:
            latency_ms = (time.time() - start_time) * 1000
            try:
                MetricsService.record_flag_evaluation(
                    db=self.db, feature_flag_id=flag.id, user_id=user_id, value=result,
                    targeting_rule_id=targeting_rule_id, latency_ms=latency_ms,
                    metadata={"context": context},
                )
                if error:
                    MetricsService.log_error(db=self.db, data=ErrorLogCreate(
                        error_type="flag_evaluation_error", feature_flag_id=flag.id,
                        user_id=user_id, message=error, request_data={"context": context}))
            except Exception as metrics_error:
                # Don't let metrics collection errors affect flag evaluation
                logger.error(f"Failed to record metrics: {str(metrics_error)}")

        return {"enabled": result, "reason": reason, "rule_id": targeting_rule_id}
```

Two orderings matter. First, **inactive beats everything** — the kill switch is checked
before any rule. Second, **a matching rule's percentage replaces the global percentage**, it
doesn't multiply it: a rule "`employee equals true` → 100%" turns the flag on for every
employee regardless of the global 5%, and a rule "`os equals android` → 0%" turns it off for
Android even at 100%. The rule decides _which_ percentage applies; the same hash then decides
_whether this user is inside it_:

```python
# 📁 backend/app/services/feature_flag_service.py
    def _evaluate_percentage_rollout(self, flag: FeatureFlag, user_id: str, percentage: Optional[int] = None) -> bool:
        # Use flag's percentage if not specified
        if percentage is None:
            percentage = flag.rollout_percentage or 0

        # If percentage is 0 or 100, short-circuit
        if percentage <= 0:
            return False
        if percentage >= 100:
            return True

        # Create a hash using user ID and flag key for deterministic assignment
        hash_input = f"{user_id}:{flag.key}"
        hash_value = int(hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest(), 16)

        # Get bucket (0-99)
        bucket = hash_value % 100

        # User is in the rollout if their bucket is less than the percentage
        return bucket < percentage
```

Because the rule path and the global path share one hash on `user_id:flag.key`, a rule at
25% selects _exactly_ the same users as the global rollout at 25% — which is what makes "add
a rule for the fixed app version at 100%, keep everyone else at 25%" behave intuitively: the
25% cohort doesn't reshuffle when a rule is added.

The `finally` block is the other half of the story: **every evaluation is recorded** as a
`RawMetric` row of type `flag_evaluation` with its latency, and every evaluation that raised
becomes an `ErrorLog` row. Those two tables are what safety monitoring (Day 18) divides to get
an error rate. Evaluation is the only path that writes them from the server side; clients add
their own errors through `POST /tracking/errors`.

> **Watch Out!** The `finally` writes to the database on every evaluation. At SDK volumes that
> is a real write load — `RawMetric` is the platform's highest-cardinality table — and it's the
> reason the metrics scheduler (Day 17) aggregates raw metrics into `aggregated_metrics` on a
> 15-minute cadence. In the AWS deployment, the flag-evaluation Lambda takes the hot path off
> PostgreSQL entirely. If you add work to `evaluate_flag_detailed`, remember it runs per
> request, per user, per flag.

The lifecycle around evaluation is small: `POST /feature-flags/{id}/activate` and
`/deactivate` (the kill switch), `PUT` to change `rollout_percentage` or `targeting_rules`,
`POST /feature-flags/bulk-toggle` for many flags at once (with an SSE audit stream), and
**rollout schedules** — a `RolloutSchedule` with ordered `RolloutStage`s (5% → 25% → 50% →
100%), each `time_based`, `metric_based` or `manual`, advanced by the rollout scheduler or by
`POST /rollout-schedules/stages/{id}/advance`. Percentages must be non-decreasing across
stages; active schedules can't have stages deleted.

### Build It

Watch `reason` change as you flip the levers on one flag (the demo seed's search flag, or one
you create):

```bash
FLAG=shoplab_search_v2   # any active flag key
U=user-42
ev() { curl -s "$API/feature-flags/evaluate/$FLAG?user_id=$U$1" -H "X-API-Key: $KEY"; echo; }

ev                                     # → rollout: depends on the bucket
python -c "import hashlib; print(int(hashlib.md5(b'$U:$FLAG').hexdigest(),16) % 100)"   # the bucket, offline

# as admin: set 100% → enabled by rollout; 0% → disabled by rollout; deactivate → inactive
FID=<flag uuid>
curl -s -X PUT $API/feature-flags/$FID -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"rollout_percentage":100}' >/dev/null; ev
curl -s -X POST $API/feature-flags/$FID/deactivate -H "Authorization: Bearer $TOKEN" >/dev/null; ev
curl -s -X POST $API/feature-flags/$FID/activate -H "Authorization: Bearer $TOKEN" >/dev/null

# a rule: employees always on (dashboard shape — Day 16), then evaluate with and without the attribute
curl -s -X PUT $API/feature-flags/$FID -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"rollout_percentage":0,"targeting_rules":{"logical_operator":"AND","groups":[{"logical_operator":"AND","conditions":[{"attribute":"employee","operator":"equals","value":true}]}],"rollout_percentage":100}}' >/dev/null
ev "&context=%7B%22employee%22%3Atrue%7D"     # → targeting_rule, enabled
ev                                             # → rollout, disabled (0%)
```

Then confirm the evaluations landed as raw metrics:

```bash
docker compose exec postgres psql -U postgres -d experimentation -c \
  "select metric_type, count(*), round(avg(value)::numeric,2) as avg_value from experimentation.raw_metrics where feature_flag_id='$FID' group by 1"
```

### Key takeaways

- A flag decision is `inactive?` → first matching rule's percentage, else global percentage →
  `md5(user_id:flag.key) % 100 < percentage`; the response carries `reason`.
- Rules pick the percentage; the shared hash picks the users — so rule cohorts and global
  cohorts coincide at equal percentages.
- Rollout up is additive, rollout down removes the newest users first; there's no per-user
  state.
- Every evaluation writes a `RawMetric` (with latency); every failure writes an `ErrorLog`;
  safety divides them.
- Kill switch = `deactivate` → `inactive` with 200; unknown key → 404.

### Q&A

**Q. Why hash on `flag.key` for flags but `experiment.id` for experiments?**
Flags are configuration that's promoted between environments by key (the same `player_v2`
in staging and production); an experiment is a one-off analysed where it ran. Hashing on the
key gives flags stable cohorts across environments; hashing on the id gives experiments
independence even when two are named alike. It also means renaming a flag's key reshuffles
its cohort — the dashboard warns about that.

**Q. Can a flag return a value other than a boolean?**
The model has a `variants` JSONB column for multivariate flags and the response has a
`config` field, but `config` is always `None` today and the evaluate path is boolean. The
React SDK's `useVariant` returns a variant _string_ only because it's built on
`useFeatureFlag`'s value. Treat flags as booleans; use an experiment when you need arms.

**Q. What's a `FeatureFlagOverride`?**
A per-user forced value (`value` JSONB, optional `reason` and `expires_at`) with a unique
index on `(flag, user)`. It's for "turn it on for this customer" support cases; it isn't
consulted by `evaluate_flag_detailed` today — check the override endpoints before relying on
it.

**Q. My flag is at 100% but `reason` says `targeting_rule` and `enabled` is false.**
A rule matched with a percentage below 100 (or 0). The rule's percentage _replaces_ the
global one. Look at the flag's `targeting_rules` and at `rule_id` in the service's return
value; the dashboard's rule editor shows each rule's percentage.

---

## Day 16 — The Enhanced Rules Engine and the targeting adapter

### What & why

Targeting is "does this user's context match this rule?" — and both halves of that sentence
turned out to be harder than they look. The **rule** side grew from a handful of operators to
twenty-plus (semantic versions, geo-distance, time windows, JSON paths, array operations)
because real targeting needs them: "Android 12 users on app version ≥ 3.2.1 within 50 km of
Berlin during business hours." The **context** side grew because SDKs disagree about shape:
one sends `{"country": "US"}`, another `{"user": {"country": "US"}}`, and the dashboard's rule
editor writes `user.country`.

Two modules split the problem. `core/rules_engine.py` (EP-001) is a pure evaluator over one
canonical rule shape (`TargetingRules` → `TargetingRule` → `RuleGroup` → `Condition`) with all
the operators, compiled-rule caching and result caching. `core/targeting_adapter.py` is the
seam that turns whatever is stored into that shape and whatever the SDK sent into the flat
context the engine expects. `CLAUDE.md` is blunt about the rule: _use these helpers anywhere
rules are evaluated; do not add a fourth format._

#### Why three formats exist, and why they're not unified

Because they were written at different times for different editors:

- the **legacy list** shape `[{"type": "user_id"|"context", ...}]`, evaluated by
  `FeatureFlagService._evaluate_rule` with six operators — still in production data;
- the **native** shape `{"version", "rules": [{"id", "rule": {...}, "rollout_percentage",
  "priority"}], "default_rule"}` that the engine and the experiment targeting path use;
- the **dashboard** shape `{"logical_operator", "groups": [{"logical_operator", "conditions":
  [...]}]}` written by the React rule builder, with operators named `equals`, `semver_gte`,
  `is_null`.

A migration to one shape would have to rewrite production JSONB and every test fixture. The
adapter is cheaper and — importantly — makes the _behaviour_ identical: a dashboard rule and
a native rule with the same conditions match the same users.

### How Experimently does it

The canonical shape, in Pydantic:

```python
# 📁 backend/app/schemas/targeting_rule.py
class OperatorType(str, Enum):
    """Operators for condition evaluation."""
    EQUALS = "eq"
    NOT_EQUALS = "neq"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BEFORE = "before"  # Date comparison
    AFTER = "after"  # Date comparison
    BETWEEN = "between"  # Date or number range
    CONTAINS_ALL = "contains_all"  # Array contains all elements
    CONTAINS_ANY = "contains_any"  # Array contains any element
    MATCH_REGEX = "match_regex"  # String matches regex pattern
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    # Advanced operators for custom attributes
    SEMANTIC_VERSION = "semantic_version"  # Compare semantic versions (e.g., 1.2.3)
    GEO_DISTANCE = "geo_distance"  # Distance from a geographic point
    TIME_WINDOW = "time_window"  # Within a time window (e.g., business hours)
    PERCENTAGE_BUCKET = "percentage_bucket"  # User falls within percentage bucket
    JSON_PATH = "json_path"  # Extract and compare JSON path value
    ARRAY_LENGTH = "array_length"  # Compare array length


class Condition(BaseModel):
    """A single condition to evaluate against a user attribute."""
    attribute: str = Field(..., description="User attribute to evaluate")
    operator: OperatorType = Field(..., description="Operator to apply")
    value: Any = Field(..., description="Value to compare against")
    additional_value: Optional[Any] = Field(None, description="Additional value for operators that need two values (e.g., between)")
    ...
    @model_validator(mode='after')
    def validate_operator_value_compatibility(self):
        """Validate that operator and value are compatible."""
        ...
        if operator in [OperatorType.IN, OperatorType.NOT_IN, OperatorType.CONTAINS_ALL, OperatorType.CONTAINS_ANY]:
            if not isinstance(value, (list, tuple, set)):
                raise ValueError(f"Operator {operator} requires a collection (list/array) as value")
        if operator in [OperatorType.BETWEEN, OperatorType.GEO_DISTANCE]:
            if additional_value is None:
                raise ValueError(f"Operator {operator} requires additional_value")
```

The validator is why a malformed rule is a 422 at `PUT /feature-flags/{id}` rather than a
silent non-match at evaluation time. The engine walks groups recursively with `and`/`or`/`not`
and applies operators through one dispatch function:

```python
# 📁 backend/app/core/rules_engine.py
def evaluate_rule_group(rule_group: RuleGroup, user_context: UserContext) -> bool:
    operator = rule_group.operator

    # Evaluate conditions
    condition_results = [evaluate_condition(condition, user_context) for condition in rule_group.conditions]

    # Evaluate nested groups
    if rule_group.groups:
        group_results = [evaluate_rule_group(group, user_context) for group in rule_group.groups]
    else:
        group_results = []

    all_results = condition_results + group_results

    # If there are no conditions or groups, return True
    if not all_results:
        return True

    if operator == LogicalOperator.AND:
        return all(all_results)
    elif operator == LogicalOperator.OR:
        return any(all_results)
    elif operator == LogicalOperator.NOT:
        if len(all_results) == 1:
            return not all_results[0]
        else:
            return not all(all_results)
    ...
```

(An empty group is `True` — "no conditions" means "everyone". That's the right default for a
rule with a percentage and no conditions, and a trap if you build a rule editor that emits an
empty group by accident.)

The adapter's two public functions are the ones you'll call. `normalise_targeting_rules`
turns stored JSON into `TargetingRules` (or `None` for the legacy list, which the flag service
still evaluates itself); `expand_context` flattens and aliases the SDK's context:

```python
# 📁 backend/app/core/targeting_adapter.py
def expand_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Flatten an SDK context into the lookup dict the rules engine expects.

    The result contains:

    * every raw key as given (nested dicts are kept as values too);
    * a dotted key for every nested dict entry, recursively
      (``{"app": {"version": "3.2.1"}}`` also answers ``app.version``);
    * ``user.<k>``, ``device.<k>`` and ``app.<k>`` aliases for every top-level
      key (``{"country": "US"}`` also answers ``user.country``; ``{"os_version":
      "17.4.0"}`` also answers ``device.os_version``);
    * the bare key for dotted keys under those prefixes (``user.country`` in
      the context also answers ``country``).

    Explicit keys always win over aliases, so ``{"country": "US", "user":
    {"country": "DE"}}`` resolves ``user.country`` to ``"DE"``.
    """
    if not context or not isinstance(context, dict):
        return {}

    flat: Dict[str, Any] = {}
    _flatten_into(flat, context, prefix="")

    # Forward aliases: top-level key -> <prefix>.key
    for key, value in list(flat.items()):
        if "." in key or isinstance(value, dict):
            continue
        for prefix in CONTEXT_ALIAS_PREFIXES:
            flat.setdefault(f"{prefix}.{key}", value)

    # Reverse aliases: <prefix>.key -> key
    for key, value in list(flat.items()):
        head, sep, tail = key.partition(".")
        if sep and head in CONTEXT_ALIAS_PREFIXES and tail and "." not in tail:
            flat.setdefault(tail, value)

    return flat
```

`setdefault` is doing the "explicit keys win" work: aliases never overwrite a key the SDK
actually sent. And the dashboard operator names are mapped by a table, with semver handled
specially because the engine's `SEMANTIC_VERSION` operator takes the comparison as a
sub-operator:

```python
# 📁 backend/app/core/targeting_adapter.py
_SIMPLE_OPERATOR_MAP: Dict[str, OperatorType] = {
    "equals": OperatorType.EQUALS,
    "not_equals": OperatorType.NOT_EQUALS,
    ...
    "regex": OperatorType.MATCH_REGEX,
    "is_null": OperatorType.IS_NULL,
    "is_not_null": OperatorType.IS_NOT_NULL,
    "geo_within_radius": OperatorType.GEO_DISTANCE,
    "time_window": OperatorType.TIME_WINDOW,
    "array_contains": OperatorType.CONTAINS_ALL,
    "array_intersects": OperatorType.CONTAINS_ANY,
}

_SEMVER_OPERATOR_MAP: Dict[str, str] = {
    "semver_eq": "eq", "semver_gt": "gt", "semver_lt": "lt", "semver_gte": "gte", "semver_lte": "lte",
}
```

Finally, `match_targeting_rule` finds the first rule by priority **without** applying the
engine's own rollout hashing — because the flag service wants to bucket with `user_id:flag.key`
(Day 15), not with the engine's `user_id:rule.id`:

```python
# 📁 backend/app/core/targeting_adapter.py
def match_targeting_rule(rules: Optional[TargetingRules], context: Dict[str, Any]) -> Optional[TargetingRule]:
    """
    Return the first rule (by priority) whose conditions match ``context``.

    Unlike :func:`backend.app.core.rules_engine.evaluate_targeting_rules` this
    does **not** apply the rule's ``rollout_percentage``: the caller buckets the
    user itself (the flag service hashes ``user_id:flag.key`` so a rule at N%
    selects the same users as the global rollout at N%). When nothing matches
    the ``default_rule`` is returned, mirroring the engine.
    """
```

Experiments use the same machinery from `AssignmentService._evaluate_experiment_targeting`,
which parses native _or_ dashboard shapes (never the legacy list — "list-shaped experiment
targeting rules are not supported") and runs them through `RulesEvaluationService`, the
service wrapper that adds compilation caching, an LRU result cache with TTL, and P50/P95/P99
latency metrics.

> **Did You Know?** The engine's own rollout hash — `should_include_in_rollout` — salts with
> `rule.id` and picks the user identifier from the context by trying `user_id`, `id`,
> `email`, `username`, `device_id`, `client_id`, `session_id` in that order
> (`get_stable_user_id`), falling back to a hash of the whole sorted context. That last
> fallback is "better than nothing" but not stable across contexts, which is one more reason
> the flag and assignment services bucket with an explicit `user_id` and only borrow the
> engine for matching.

### Build It

Feed the same rule in two shapes through the adapter and confirm they match the same
context, then watch the aliasing:

```bash
python - <<'PY'
from backend.app.core.targeting_adapter import normalise_targeting_rules, expand_context, match_targeting_rule
dashboard = {"logical_operator": "AND", "groups": [{"logical_operator": "AND",
             "conditions": [{"attribute": "app.version", "operator": "semver_gte", "value": "3.2.1"},
                            {"attribute": "user.os", "operator": "equals", "value": "android"}]}],
             "rollout_percentage": 100}
native = {"version": "1.0", "rules": [{"id": "r1", "priority": 1, "rollout_percentage": 100,
          "rule": {"operator": "and", "conditions": [
              {"attribute": "app.version", "operator": "semantic_version", "value": "3.2.1", "additional_value": "gte"},
              {"attribute": "os", "operator": "eq", "value": "android"}]}}]}
ctx = expand_context({"os": "android", "app": {"version": "3.4.0"}})
print(sorted(k for k in ctx))            # note user.os / device.os / app.os aliases and app.version
for name, raw in (("dashboard", dashboard), ("native", native)):
    rules = normalise_targeting_rules(raw)
    print(name, "→", getattr(match_targeting_rule(rules, ctx), "id", None))
print("legacy list →", normalise_targeting_rules([{"type": "context", "conditions": []}]))
PY
```

Then benchmark the engine the way the performance suite does, to feel the "125k
evaluations/second" claim:

```bash
python -m pytest backend/tests/performance -k "rules" -p no:cov -q 2>/dev/null | tail -5
```

### Key takeaways

- One canonical rule shape (`TargetingRules → TargetingRule → RuleGroup → Condition`), 20+
  operators, validated by Pydantic at write time.
- Three stored shapes; `normalise_targeting_rules` converts native and dashboard, returns
  `None` for the legacy list so the flag service's old evaluator handles it. Never add a
  fourth.
- `expand_context` flattens nested dicts to dotted keys and aliases top-level keys under
  `user.`/`device.`/`app.`; explicit keys win.
- `match_targeting_rule` matches without bucketing so the caller can hash on its own salt.
- `RulesEvaluationService` wraps the engine with compilation and result caches plus latency
  metrics.

### Q&A

**Q. Where do I add a new operator?**
Three places: `OperatorType` in `schemas/targeting_rule.py` (plus the compatibility
validator if it needs a collection or an `additional_value`), the dispatch in
`rules_engine.apply_operator`, and — if the dashboard should offer it — the
`_SIMPLE_OPERATOR_MAP` in the adapter and the frontend's operator list under
`frontend/src/utils/targeting.ts`. Then tests in `backend/tests/unit/core/test_rules_engine*.py`.

**Q. Why is an empty rule group `True`?**
So a rule that is "just a percentage" (no conditions) applies to everyone. If you want "match
nobody", use a condition that can't match, or set the rule's percentage to 0.

**Q. Do targeting rules on experiments use percentages?**
The experiment path evaluates rules for _eligibility_ only; the rule's `rollout_percentage`
isn't the experiment's traffic split (that's the variants' `traffic_allocation`). A rule at
50% on an experiment would make half of the matching users ineligible — usually not what you
want; leave experiment rules at 100.

**Q. How do `semver_gte` values get validated?**
`_semver_value` in the adapter normalises `"3.2"` to `"3.2.0"` and rejects strings the
`semver` library can't parse; the engine's `SEMANTIC_VERSION` operator compares with
`semver.compare`. A context value that isn't a valid version fails the condition (no
exception).

---

## Day 17 — Background schedulers and the advisory lock

### What & why

Some work isn't triggered by a request: an experiment scheduled to start at midnight, a
rollout stage due at 09:00, raw metrics to aggregate, safety thresholds to check, bandit
weights to refresh. Experimently runs **five schedulers** as `asyncio` tasks inside the API
process, started by the lifespan on Day 8:

| Scheduler    | Module                       | Cadence (default)                 | Tick does                                             |
| ------------ | ---------------------------- | --------------------------------- | ----------------------------------------------------- |
| experiment   | `core/scheduler.py`          | 15 min                            | activate/complete experiments by `start_date`/`end_date`; Bayesian stop |
| rollout      | `core/rollout_scheduler.py`  | `ROLLOUT_CHECK_INTERVAL_MINUTES` (15) | advance time-based rollout stages                  |
| metrics      | `core/metrics_scheduler.py`  | 15 min                            | aggregate `raw_metrics` → `aggregated_metrics`       |
| safety       | `core/safety_scheduler.py`   | `SAFETY_CHECK_INTERVAL_MINUTES` (5) | check thresholds, auto-rollback                    |
| bandit       | `core/bandit_scheduler.py`   | `BANDIT_UPDATE_INTERVAL_MINUTES` (5) | recompute `BanditState.variant_weights`            |

#### Why in-process, and why that forces a lock

A separate worker service (Celery, a cron container) would be cleaner in the abstract. The
team chose in-process tasks because the Community Edition must be one container plus a
database, and because each tick is small (a few queries). The cost arrives the moment you run
two API replicas — or two uvicorn workers: **every replica starts every scheduler**, and two
copies of "advance the rollout stage" racing against one database will advance it twice, or
roll a flag back twice, or double-count a bandit update.

The fix is a **PostgreSQL advisory lock**, taken per scheduler name, non-blocking: a replica
that doesn't get the lock _skips_ the tick rather than queueing. No Redis, no leader election,
no shared registry — the database every replica already talks to is the coordinator.

### How Experimently does it

All five loops share one runner:

```python
# 📁 backend/app/core/scheduler_tick.py
"""
All five schedulers (experiment, rollout, metrics, safety, bandit) run their
periodic work through :func:`run_locked_tick`, which gives every tick the same
three guarantees:

1. **Single runner per tick** — the work runs under the PostgreSQL advisory
   lock from :mod:`backend.app.core.scheduler_lock`; a replica that does not
   get the lock skips the tick.
2. **Run history** — every executed tick is persisted through
   :meth:`SchedulerHealthService.record_run` (``scheduler_runs`` table), which
   feeds ``GET /api/v1/scheduler/health``.
3. **Prometheus signal** — ``scheduler_last_success_timestamp{name}``,
   ``scheduler_tick_duration_seconds{name}`` and
   ``scheduler_ticks_total{name,status}`` from :mod:`backend.app.core.metrics`.
"""

async def run_locked_tick(name: str, tick: Callable[[], Awaitable[Any]], *, engine=None, record=True) -> TickOutcome:
    started_at = datetime.now(timezone.utc)
    t0 = time.perf_counter()

    with scheduler_lock(name, engine=engine) as acquired:
        if not acquired:
            completed_at = datetime.now(timezone.utc)
            prom.record_scheduler_tick(name, STATUS_SKIPPED, time.perf_counter() - t0)
            logger.info("scheduler %s: lock held elsewhere, tick skipped", name)
            return TickOutcome(name=name, status=STATUS_SKIPPED, acquired=False, ...)

        error: Optional[str] = None
        exc_to_raise: Optional[Exception] = None
        result = TickResult()
        try:
            raw = await tick()
            result = TickResult.coerce(raw)
            if result.items_failed and result.items_processed:
                status = STATUS_PARTIAL
            elif result.items_failed and not result.items_processed:
                status = STATUS_FAILED
                error = f"{result.items_failed} item(s) failed"
            else:
                status = STATUS_SUCCESS
        except asyncio.CancelledError:
            # Shutdown: release the lock (context manager) and propagate
            # without recording a bogus "failed" run.
            raise
        except Exception as exc:  # noqa: BLE001 - recorded then re-raised
            status = STATUS_FAILED
            error = str(exc) or exc.__class__.__name__
            exc_to_raise = exc

    completed_at = datetime.now(timezone.utc)
    duration = time.perf_counter() - t0

    prom.record_scheduler_tick(name, status, duration)
    if status == STATUS_SUCCESS:
        prom.record_scheduler_success(name)

    if record:
        _persist_run(name, started_at, completed_at, status, result, error)

    if exc_to_raise is not None:
        raise exc_to_raise
    ...
```

The lock itself is small and careful:

```python
# 📁 backend/app/core/scheduler_lock.py
# Namespace prefix so scheduler locks cannot collide with other advisory-lock
# users of the same database (e.g. alembic, application code).
LOCK_NAMESPACE = "experimently.scheduler."


def try_acquire(conn: Connection, name: str) -> bool:
    """Attempt to take the advisory lock for *name* on *conn* (non-blocking)."""
    row = conn.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": lock_name(name)}
    ).scalar()
    return bool(row)


@contextmanager
def scheduler_lock(name: str, engine: Optional[Engine] = None) -> Iterator[bool]:
    engine = engine or _default_engine()
    conn: Optional[Connection] = None
    acquired = False
    try:
        try:
            conn = engine.connect()
            acquired = try_acquire(conn, name)
        except Exception as exc:
            logger.warning("scheduler_lock(%s): could not acquire (database unavailable): %s", name, exc)
            acquired = False
        yield acquired
    finally:
        if conn is not None:
            try:
                if acquired:
                    release(conn, name)
            except Exception as exc:
                logger.warning("scheduler_lock(%s): release failed: %s", name, exc)
            finally:
                conn.close()
```

Three details carry the design. `pg_try_advisory_lock` (not `pg_advisory_lock`) is
_non-blocking_ — the loser skips. The lock is _session-scoped_, so it's taken on a dedicated
connection checked out for the block and explicitly released (closing the connection would
release it too, but explicit keeps pooled connections clean). And `hashtext(name)` is
PostgreSQL's own text hash, so every replica derives the same 32-bit key from the same name
with nothing to share. If the database is down, the manager yields `False` and the scheduler
skips — a scheduler must never crash the API because it couldn't coordinate.

Each scheduler is the same skeleton around one tick method:

```python
# 📁 backend/app/core/scheduler.py
class ExperimentScheduler:
    def __init__(self, interval_minutes: int = 15):
        self.interval_minutes = interval_minutes
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self._notification_service = NotificationService()

    async def start(self):
        if self.is_running:
            logger.warning("Scheduler is already running")
            return
        self.is_running = True
        self.task = asyncio.create_task(self._run_scheduler())

    async def _run_scheduler(self):
        """Run the scheduler loop."""
        while self.is_running:
            try:
                # One tick under the advisory lock; records the run and
                # skips when another replica holds the lock.
                await run_locked_tick(SCHEDULER_NAME, self.process_scheduled_experiments)
                await asyncio.sleep(self.interval_minutes * 60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in experiment scheduler: {str(e)}")
                # Wait a bit before trying again
                await asyncio.sleep(60)
```

The tick opens its **own** `SessionLocal()` (no request, no `get_db`), does its queries,
commits, and returns `{"items_processed", "items_failed", "metadata"}` for the run record.
`asyncio.sleep` between ticks means the interval is "after the previous tick finished", not
"every N minutes on the clock" — fine for work that is idempotent and due-based.

#### The naive-datetime bug

The rollout scheduler's tick compares a stage's `start_date` to now. `rollout_stages`
timestamps live in `timestamp without time zone` columns, so SQLAlchemy returns naive
datetimes; the scheduler works in `datetime.now(timezone.utc)`. Python refuses to compare
naive with aware — `TypeError: can't compare offset-naive and offset-aware datetimes` — and
until PR #65 every tick with an active schedule raised on the first comparison. The rollout
never advanced; the run record said `failed`; nobody was looking at run records. The fix is
one normaliser applied at every comparison:

```python
# 📁 backend/app/core/rollout_scheduler.py
def _as_utc(value: datetime) -> datetime:
    """
    Return ``value`` as a timezone-aware UTC datetime.

    ``rollout_stages`` timestamps are stored in ``timestamp without time zone``
    columns, so SQLAlchemy hands back naive datetimes while the scheduler
    works with ``datetime.now(timezone.utc)``; comparing the two raises
    ``TypeError``. Naive values are UTC by convention throughout the app.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
```

and the eligibility check that uses it:

```python
# 📁 backend/app/core/rollout_scheduler.py
    def _is_stage_eligible_for_activation(self, stage: RolloutStage, current_time: datetime) -> bool:
        if stage.status != RolloutStageStatus.PENDING:
            return False

        if stage.trigger_type == TriggerType.TIME_BASED:
            if not stage.start_date:
                return True
            return _as_utc(stage.start_date) <= current_time

        elif stage.trigger_type == TriggerType.METRIC_BASED:
            logger.info(f"Metric-based activation for stage {stage.id} not yet implemented")
            return False

        elif stage.trigger_type == TriggerType.MANUAL:
            # Manual stages are only activated manually, never by the scheduler
            return False
        return False
```

The same PR fixed a second logic bug in the same tick: the scheduler activated the _next_
stage as soon as the previous one completed, ignoring the next stage's own `start_date`.
Both bugs lived in code with unit tests — tests that mocked the stage objects with aware
datetimes and never ran a tick against a real row. Day 22 draws the lesson.

> **By the Way.** Metric-based triggers are declared in the enum, accepted by the API and
> _not implemented_ by the scheduler (it logs and returns `False`). A schedule with a
> `metric_based` stage will sit at that stage until someone advances it manually. If you're
> asked why a rollout "stopped", check the trigger type before checking the lock.

### Build It

Watch the lock work, then watch it skip:

```bash
# two tick runners against the same lock name: exactly one acquires
python - <<'PY'
import asyncio
from backend.app.core.scheduler_tick import run_locked_tick
from backend.app.core.scheduler_lock import scheduler_lock
from backend.app.db.session import engine

async def slow_tick():
    await asyncio.sleep(2)
    return {"items_processed": 1}

async def main():
    with scheduler_lock("experiment", engine=engine) as held:      # hold it from "another replica"
        print("held elsewhere:", held)
        out = await run_locked_tick("experiment", slow_tick, engine=engine, record=False)
        print("tick status while held:", out.status, "acquired:", out.acquired)
    out = await run_locked_tick("experiment", slow_tick, engine=engine, record=False)
    print("tick status after release:", out.status, out.result.items_processed)
asyncio.run(main())
PY

# the run history the dashboard's Admin → Scheduler page reads
curl -s $API/scheduler/health -H "Authorization: Bearer $TOKEN" | python -m json.tool | head -40
```

Then reproduce the naive/aware bug in three lines, so you recognise the traceback forever:

```bash
python -c "
from datetime import datetime, timezone
naive = datetime.utcnow(); aware = datetime.now(timezone.utc)
print(naive <= aware)"
```

### Key takeaways

- Five in-process `asyncio` schedulers, started by the lifespan, each a `while` loop of
  `run_locked_tick(name, tick)` + `sleep(interval)`.
- `run_locked_tick` = advisory lock (`pg_try_advisory_lock(hashtext(name))`, non-blocking,
  dedicated connection) + run record in `scheduler_runs` + Prometheus counters/histogram.
- A replica that doesn't get the lock skips; a database outage skips; a tick exception is
  recorded then re-raised to the loop's own backoff.
- Ticks open their own `SessionLocal()` and return `{items_processed, items_failed, metadata}`.
- Naive DB datetimes vs aware `now()` raised on every rollout tick until `_as_utc`; treat
  every stored timestamp as naive UTC and normalise at comparisons.

### Q&A

**Q. Why not one scheduler with five jobs?**
Independence. A slow metrics aggregation must not delay a safety check, and the lock is per
name so the five can run concurrently on different replicas. The shared runner gives them
identical guarantees without coupling their cadences.

**Q. What happens on shutdown mid-tick?**
`stop()` cancels the task; `run_locked_tick` catches `CancelledError`, lets the context
manager release the lock, and re-raises without recording a bogus failed run. An in-flight
database transaction is rolled back by the session close in the tick's `finally`.

**Q. Two workers in one container (`WEB_CONCURRENCY=2`) — does the lock still work?**
Yes; it's a database lock, so any two processes anywhere coordinate. The platform still
documents one worker per container because other per-process state (the login lockout
tracker, the in-memory rate-limit fallback) isn't shared.

**Q. Where do I see that a tick ran, or failed?**
`GET /api/v1/scheduler/health` (and the run history endpoints under `/scheduler`), backed by
`scheduler_runs`; the Admin → Scheduler dashboard page; and the Prometheus series
`scheduler_ticks_total{name,status}` / `scheduler_last_success_timestamp{name}` on
`/metrics`. Alert on the latter going stale (Week 4).

---

## Day 18 — Safety monitoring and automatic rollback

### What & why

A gradual rollout is a bet that the new code is fine; safety monitoring is the stop-loss. The
platform watches each active flag's **error rate** (and latency) over a trailing window,
compares it to per-flag thresholds, and — if the global setting allows — rolls the flag back
to a configured percentage automatically, recording why and notifying Slack/email.

The design decisions:

1. **Errors come from both sides.** Server-side evaluation failures are logged by
   `evaluate_flag_detailed`'s `finally` (Day 15). Client-side errors — a crash in the redesigned
   screen, a failed request behind the flag — arrive through `POST /api/v1/tracking/errors`
   (and `/batch`) with the API key, and become `ErrorLog` rows attached to the flag. Safety
   treats them identically.
2. **Rate, not count.** `error_rate = errors / evaluations` over the last 15 minutes. A flag
   at 5% with three crashes and 3,000 evaluations is fine; the same three crashes over 30
   evaluations is a 10% rate. Dividing by evaluations (from `raw_metrics`) is what makes the
   threshold meaningful at any rollout percentage.
3. **Roll back to a percentage, not to zero.** Each flag's safety config has a
   `rollback_percentage`; StreamPulse's player flag rolls back to 5% (the internal cohort),
   not off, so engineers keep seeing it while the crash is investigated.
4. **Don't roll back twice.** The error window stays "hot" for up to 15 minutes after a
   rollback; the scheduler checks `feature_flag.rollout_percentage <= target_percentage` and
   skips a flag that's already at or below its target, rather than writing a record and
   sending a notification every tick.

### How Experimently does it

Three tables: `safety_settings` (global — `enable_automatic_rollbacks`, default metric
thresholds), `feature_flag_safety_configs` (per flag — `enabled`, a `metrics` JSONB of
`{name: {warning_threshold, critical_threshold, comparison_type}}`, `rollback_percentage`),
and `safety_rollback_records` (history — trigger type/reason, previous and target
percentage, success, who). A flag with no config gets a default with `id = DEFAULT_CONFIG_ID`
(the nil UUID), never a 404.

The measurement:

```python
# 📁 backend/app/services/safety_service.py
    def get_error_metrics(self, db: Session, feature_flag_id: UUID, timeframe_minutes: int = 15) -> Dict[str, Any]:
        """Error counts and error rate for a flag over the last ``timeframe_minutes``."""
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=timeframe_minutes)

        error_logs = (
            db.query(ErrorLog)
            .filter(ErrorLog.feature_flag_id == feature_flag_id, ErrorLog.timestamp.between(start_time, end_time))
            .all()
        )

        total_evaluations = (
            db.query(func.coalesce(func.sum(RawMetric.count), 0))
            .filter(
                RawMetric.feature_flag_id == feature_flag_id,
                RawMetric.metric_type == MetricType.FLAG_EVALUATION.value,
                RawMetric.timestamp.between(start_time, end_time),
            )
            .scalar() or 0
        )

        error_count = len(error_logs)
        error_rate = 0.0 if total_evaluations == 0 else error_count / total_evaluations
        return {"error_count": error_count, "total_evaluations": int(total_evaluations), "error_rate": error_rate, ...}
```

(`datetime.utcnow()` — naive — because `ErrorLog.timestamp` and `RawMetric.timestamp` are
naive columns; this is the convention Day 17's `_as_utc` exists to reconcile. The client-error
endpoint converts an aware client timestamp to naive UTC with `_naive_utc` for the same
reason.)

The check compares every configured metric to its critical threshold:

```python
# 📁 backend/app/services/safety_service.py
    async def check_feature_flag_safety(self, feature_flag_id: UUID) -> SafetyCheckResponse:
        feature_flag = self._require_flag(feature_flag_id)
        config = await self.get_feature_flag_safety_config(feature_flag_id)

        if not config.enabled:
            return SafetyCheckResponse(feature_flag_id=feature_flag_id, is_healthy=True, metrics=[], ...)

        statuses: List[MetricStatus] = []
        unavailable: List[str] = []
        is_healthy = True

        for name, raw_threshold in (config.metrics or {}).items():
            threshold = _as_threshold(raw_threshold)
            value = self._get_metric_value(feature_flag_id, name)

            if value is None:
                unavailable.append(name)
                value = 0.0
                critical = warning = False
            else:
                critical = _breaches(value, threshold.critical_threshold, threshold.comparison_type)
                warning = _breaches(value, threshold.warning_threshold, threshold.comparison_type)

            if critical:
                is_healthy = False
            ...
```

A metric the platform can't measure is reported as `measured: false` with value 0 rather
than treated as a breach — a missing signal must not roll a flag back. And the scheduler
closes the loop (quoted in full because its guards are the product's promise):

```python
# 📁 backend/app/core/safety_scheduler.py
                    # If auto-rollback is enabled and safety check fails, trigger rollback
                    settings = await safety_service.async_get_safety_settings()

                    if settings.enable_automatic_rollbacks and not safety_check.is_healthy:
                        # Find what metric triggered the rollback
                        trigger_reason = "Automatic rollback due to safety issues"
                        for metric in safety_check.metrics:
                            if not metric.is_healthy:
                                trigger_reason = f"Automatic rollback due to {metric.name} exceeding threshold ({metric.current_value} > {metric.threshold})"
                                break

                        # Roll back to the percentage configured for this flag
                        # (e.g. back to the internal 5% stage), not always to 0.
                        target_percentage = _rollback_target_percentage(config)

                        if feature_flag.rollout_percentage <= target_percentage:
                            # Already rolled back; the error window is still hot
                            # from before the rollback. Do not write another
                            # record or send another notification every cycle.
                            continue

                        # Execute rollback
                        rollback_result = await safety_service.async_rollback_feature_flag(
                            feature_flag_id=feature_flag.id,
                            percentage=target_percentage,
                            reason=trigger_reason,
                            trigger_type=RollbackTriggerType.AUTOMATIC,
                        )

                        if rollback_result.success:
                            rollback_count += 1
                            try:
                                self._notification_service.notify_safety_rollback(
                                    feature_flag_id=str(feature_flag.id),
                                    feature_flag_name=feature_flag.key,
                                    reason=trigger_reason,
                                )
                            except Exception as exc:
                                logger.warning("Notification failed (non-critical): %s", exc)
```

The manual controls mirror the automatic path: `POST /safety/feature-flags/{id}/check` runs
the same check on demand, `POST /safety/feature-flags/{id}/rollback` executes a manual
rollback (recorded with `trigger_type = MANUAL` and the user), and `GET/PUT /safety/settings`
flips the global switch. Rollback also **cancels any active rollout schedule** for the flag,
so the scheduler doesn't advance it back up on the next tick.

> **Did You Know?** The safety service was rewritten in PR #64 because the original read
> error metrics from a table nobody wrote to, so `is_healthy` was always `True`. The rewrite
> chose `error_logs` and `raw_metrics` precisely because they are the tables the evaluation
> path already fills — no new producer needed. The StreamPulse demo's whole rollout story
> exists to keep that path exercised end-to-end.

### Build It

Trigger a rollback without waiting for a real crash: report errors through the public API,
then run the check on demand.

```bash
FLAG_KEY=streampulse_player_v2   # or any active flag with a safety config and rollout > rollback_percentage
# make sure there are evaluations in the window (the simulator does this; or loop the evaluate call)
for i in $(seq 1 50); do curl -s "$API/feature-flags/evaluate/$FLAG_KEY?user_id=u$i" -H "X-API-Key: $KEY" >/dev/null; done
# report client errors — a crash on Android 12
for i in $(seq 1 10); do curl -s -X POST $API/tracking/errors -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d "{\"feature_flag_key\":\"$FLAG_KEY\",\"user_id\":\"u$i\",\"error_type\":\"crash\",\"message\":\"NullPointerException in PlayerV2\",\"metadata\":{\"os\":\"android\",\"os_version\":\"12\"}}" >/dev/null; done

FID=<flag uuid>
curl -s -X POST $API/safety/feature-flags/$FID/check -H "Authorization: Bearer $TOKEN" | python -m json.tool
# 10 errors / 50 evaluations = 0.2 — above a 0.05 critical threshold → is_healthy: false
```

With `enable_automatic_rollbacks: true` and `SAFETY_CHECK_INTERVAL_MINUTES=1`, the next tick
rolls it back; watch `GET /safety/feature-flags/$FID/rollbacks` and the Admin → Safety page.
Then read `_rollback_target_percentage` and confirm the "already rolled back" guard by
running the check twice.

### Key takeaways

- `error_rate = ErrorLog count ÷ Σ RawMetric.count(flag_evaluation)` over 15 minutes; errors
  come from server evaluation failures _and_ `POST /tracking/errors`.
- Per-flag config: `enabled`, `metrics` thresholds (warning/critical/comparison),
  `rollback_percentage`; global `enable_automatic_rollbacks`.
- The safety scheduler checks every active flag with rollout > 0, rolls back to the target
  percentage, records a `SafetyRollbackRecord`, notifies, cancels the rollout schedule — and
  skips flags already at/below target.
- Unmeasurable metrics are reported, not breached.
- Timestamps in these tables are naive UTC.

### Q&A

**Q. Why 15 minutes?**
Long enough to have evaluations to divide by at a 5% rollout, short enough that a crash spike
is caught within a couple of scheduler ticks. It's a parameter of `get_error_metrics`; the
check uses the default.

**Q. What stops one bad client from rolling back a flag for everyone?**
Nothing in the platform — deliberately. A crash that affects 20% of evaluations _is_ a
reason to roll back. The mitigation is the threshold (set it relative to your baseline error
rate) and the fact that rollback goes to `rollback_percentage`, not off. Error reports are
also rate-limited under the SDK tier.

**Q. Does rollback affect experiments?**
No; safety is a flag feature. An experiment's kill switch is `POST /experiments/{id}/pause`.

**Q. Where does the notification go?**
`NotificationService` → `SlackNotifier` (bot token + channel) and `EmailNotifier`
(SendGrid or SMTP), governed by `SLACK_ENABLED`/`EMAIL_ENABLED` and per-user preferences under
`/notifications`; every delivery is logged. Week 4, Day 27.

---

## Day 19 — The statistics engine: sequential testing, CUPED, Bayesian, bandits

### What & why

Day 6's primary result is a fixed-horizon test: decide a sample size, wait, look once. Real
teams look every day, want an answer sooner, have noisy metrics, prefer "probability B beats A"
to a p-value, and sometimes want the platform to _shift traffic_ toward the winner while the
test runs. Four methods answer those four wishes, and each is a **block beside the primary
result**, configured per experiment and computed by its own service:

| Wish                              | Method                       | Service                                  | Enabled by                                   |
| --------------------------------- | ---------------------------- | ---------------------------------------- | -------------------------------------------- |
| Look every day without inflating α | Sequential testing (mSPRT)  | `services/sequential_testing_service.py` | `sequential_testing_enabled` + config        |
| Sharper estimate from noisy data  | CUPED variance reduction     | `services/cuped_service.py`              | `variance_reduction_config`                  |
| "P(B > A)" and expected loss      | Bayesian (Beta-Binomial)     | `services/bayesian_service.py`           | `bayesian_enabled` + `bayesian_config`       |
| Shift traffic to the winner       | Multi-armed bandit           | `services/bandit_service.py` + scheduler | `optimization_type != "fixed"`               |

The [statistics book](../experimentation-statistics/README.md) derives each; today is the
_shape_ of each in the codebase and how it plugs into the loop.

### How Experimently does it

**Sequential testing.** The peeking problem: if you test at α = 0.05 every day, the chance of
_ever_ seeing p < 0.05 under the null climbs far above 5%. The mixture SPRT produces a
likelihood ratio Λ that you may inspect at any time; you stop when Λ ≥ 1/α, and
`min(1, 1/Λ)` is an always-valid p-value.

```python
# 📁 backend/app/services/sequential_testing_service.py
    def compute_msprt(self, control_successes, control_total, treatment_successes, treatment_total,
                      tau_squared: float = 0.001, alpha: float = 0.05) -> MSPRTResult:
        boundary = 1.0 / alpha
        ...
        p_c = control_successes / control_total
        p_t = treatment_successes / treatment_total
        delta = p_t - p_c

        # Pooled variance of the difference in proportions
        V_n = p_c * (1 - p_c) / control_total + p_t * (1 - p_t) / treatment_total
        ...
        Z_n = delta / math.sqrt(V_n)

        # mSPRT lambda: mixture likelihood ratio with Gaussian(0, tau^2) prior
        # Lambda_n = sqrt(V_n / (V_n + tau^2)) * exp(tau^2 * Z_n^2 / (2*(V_n + tau^2)))
        ratio = V_n / (V_n + tau_squared)
        lambda_ratio = math.sqrt(ratio) * math.exp(tau_squared * Z_n ** 2 / (2.0 * (V_n + tau_squared)))

        can_stop = lambda_ratio >= boundary
        always_valid_p = min(1.0, 1.0 / lambda_ratio)
        ...
```

`GET /results/{id}/sequential` returns Λ, the always-valid p, the confidence sequence, the
evidence trajectory and a `can_stop` recommendation; the experiment scheduler's tick honours a
Bayesian stop decision, and the dashboard's Sequential tab plots the trajectory against the
boundary.

**CUPED.** Variance reduction using a pre-experiment covariate X (last month's spend, say):
`Y_cuped = Y − θ(X − E[X])` with `θ = Cov(Y, X)/Var(X)` estimated on control. Same mean,
smaller variance, tighter intervals — often equivalent to 30–50% more users.

```python
# 📁 backend/app/services/cuped_service.py
    @staticmethod
    def compute_theta(control_Y: np.ndarray, control_X: np.ndarray) -> float:
        """Compute the OLS coefficient for CUPED adjustment.

        θ = Cov(Y, X) / Var(X)

        When X has zero variance (constant covariate) θ is set to 0.0
        to indicate that no adjustment should be applied.
        """
        X = np.asarray(control_X, dtype=float)
        Y = np.asarray(control_Y, dtype=float)

        var_x = np.var(X, ddof=1) if len(X) > 1 else 0.0
        if var_x == 0.0:
            return 0.0

        cov_matrix = np.cov(Y, X, ddof=1)
        cov_yx = cov_matrix[0, 1]
        return float(cov_yx / var_x)
```

Plus winsorization (clip the top/bottom percentile) for heavy-tailed revenue. Exposed at
`GET /results/{id}/cuped`; the covariate comes from `variance_reduction_config` and is read
from event metadata.

**Bayesian.** Each arm's conversion rate gets a `Beta(α, β)` posterior — conjugate to the
binomial, so the update is addition — and the questions "P(this arm is best)" and "expected
loss if I ship this arm" are answered by Monte Carlo over the posteriors:

```python
# 📁 backend/app/services/bayesian_service.py
def compute_posterior(prior: Dict[str, float], observations: Dict[str, int]) -> Dict[str, float]:
    ...
    # Conjugate Beta-Binomial update:
    #   alpha_posterior = alpha_prior + conversions
    #   beta_posterior  = beta_prior  + non_conversions
    alpha_posterior = alpha_prior + conversions
    beta_posterior = beta_prior + non_conversions
    return {"alpha": alpha_posterior, "beta": beta_posterior}


def compute_probability_to_be_best(posteriors, n_samples: int = DEFAULT_N_SAMPLES, seed: Optional[int] = None) -> List[float]:
    ...
    # Draw samples from each posterior (shape: [n_variants, n_samples])
    samples = _draw_posterior_samples(posteriors, n_samples, seed)

    # For each sample, find which variant is the winner
    winner = np.argmax(samples, axis=0)  # shape: [n_samples]

    return [float(np.mean(winner == i)) for i in range(len(posteriors))]
```

The `seed` is the Day 6 `derive_seed(experiment_id, day, n_samples)`, so the block is
reproducible; a Bayes factor (Savage–Dickey) and a stopping rule (`should_stop`) round it out.
`bayesian_results` rides inside the main results response when `bayesian_enabled`.

**Bandits.** For `optimization_type ∈ {thompson_sampling, ucb1, epsilon_greedy}` the bandit
scheduler recomputes per-variant weights every `BANDIT_UPDATE_INTERVAL_MINUTES` and stores
them in `BanditState.variant_weights`; `/tracking/assign` routes _new_ users by those weights
(Day 4). Thompson Sampling is the Bayesian block used as a policy:

```python
# 📁 backend/app/services/bandit_service.py
class ThompsonSampling:
    PRIOR_ALPHA: float = 1.0   # uninformative prior
    PRIOR_BETA: float = 1.0
    N_SAMPLES: int = 10_000    # samples used to estimate selection probabilities

    @staticmethod
    def sample(alpha: List[float], beta: List[float], n_samples: int = 10_000, seed: Optional[int] = None) -> List[float]:
        n_variants = len(alpha)
        if n_variants == 1:
            return [1.0]

        # Draw n_samples from each Beta posterior: shape (n_samples, n_variants)
        rng = make_rng(ThompsonSampling.resolve_seed(seed, n_samples))
        draws = rng.beta(alpha, beta, size=(n_samples, n_variants))

        # For each sample pick the arm with the maximum draw
        winners = np.argmax(draws, axis=1)

        # Count wins per arm and normalise
        counts = np.bincount(winners, minlength=n_variants).astype(float)
        probabilities = (counts / n_samples).tolist()
        return probabilities
```

The scheduler's input is the interesting engineering. Where do "pulls" and "successes" come
from? In order: DynamoDB real-time counters (the AWS fast path), then **PostgreSQL —
assignments per variant and conversions matched through the primary metric's `event_name`**
(the Day 5 helper again), then the previously persisted `BanditState`, then zero priors:

```python
# 📁 backend/app/core/bandit_scheduler.py
    def get_variant_stats_from_counters(self, experiment_id, variant_ids, experiment=None) -> Dict[str, VariantStats]:
        """
        Sources are tried in order: DynamoDB counters, PostgreSQL
        (assignments + conversion events), the persisted BanditState, and
        finally zero-count priors.  A source is skipped when it raises or
        when no variant has recorded a pull.
        """
        stats = self._stats_from_dynamodb(experiment_id, variant_ids)
        if _has_pulls(stats):
            return stats

        stats = self._stats_from_postgres(experiment_id, variant_ids, experiment)
        if _has_pulls(stats):
            return stats

        stats = self._stats_from_bandit_state(experiment_id, variant_ids)
        if stats is not None:
            return stats

        # No data at all — use zero-count priors
        return {vid: VariantStats(variant_id=vid) for vid in variant_ids}
```

This is the code that was broken for months: the first source called a DynamoDB method that
didn't exist, the exception was swallowed, and every refresh fell through to zero priors —
bandits that "ran" but never learned. And nothing started the scheduler until PR #65 wired
`bandit_scheduler_runner` into the lifespan. The Postgres fallback is what makes bandits work
in the Community Edition at all.

> **Watch Out!** The bandit changes _who gets what_ while the experiment runs, which breaks
> the fixed-allocation assumptions of the Day 6 statistics (and SRM will, correctly, warn —
> the observed split is _supposed_ to drift from the configured one). A bandit experiment is
> for _optimising_ during the run, not for a clean causal estimate; the results page shows
> counts and the bandit's own regret estimate, and the "recommendation" should be read with
> that in mind.

### Build It

Fetch each block for an experiment that has it enabled (ShopLab's hero experiment has
sequential + Bayesian; the product-sort experiment is a Thompson bandit), then reproduce one
number:

```bash
EXP=<shoplab_hero uuid>
curl -s "$API/results/$EXP/sequential" -H "Authorization: Bearer $TOKEN" | python -m json.tool | head -40
curl -s "$API/results/$EXP/bayesian"   -H "Authorization: Bearer $TOKEN" | python -m json.tool | head -40

# reproduce probability-to-be-best from the counts, with the same seed the API used today
python - <<'PY'
from backend.app.services.bayesian_service import compute_posterior, compute_probability_to_be_best
from backend.app.core.stats_engine import derive_seed, as_of_bucket
exp = input("experiment uuid: ").strip()
c = compute_posterior({"alpha": 1, "beta": 1}, {"conversions": int(input("control conv: ")), "total": int(input("control n: "))})
t = compute_posterior({"alpha": 1, "beta": 1}, {"conversions": int(input("treatment conv: ")), "total": int(input("treatment n: "))})
seed = derive_seed(exp, as_of_bucket(), 10_000)
print(compute_probability_to_be_best([c, t], n_samples=10_000, seed=seed))
PY

BANDIT=<shoplab product sort uuid>
curl -s "$API/bandit/$BANDIT/state" -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

### Key takeaways

- Four advanced methods, four services, each a block beside the primary result, each
  configured on the experiment row.
- mSPRT: monitor Λ, stop at 1/α, `min(1, 1/Λ)` is an always-valid p-value.
- CUPED: `θ = Cov(Y,X)/Var(X)` on control; adjusted Y has the same mean and less variance.
- Bayesian: Beta-Binomial posteriors, Monte Carlo PtBB and expected loss, seeded per
  `(experiment, day, n)`.
- Bandits: Thompson/UCB1/ε-greedy weights recomputed by the scheduler from DynamoDB →
  Postgres (via `event_matching`) → `BanditState` → priors; `/assign` routes new users by them.

### Q&A

**Q. Which should I enable by default?**
Sequential testing, if you'll look at results before the planned end (everyone does). CUPED
if you have a pre-period covariate. Bayesian if your stakeholders think in probabilities.
Bandits only when the goal is to _exploit_ during the run, not to _learn_ a clean effect.

**Q. Does enabling sequential testing change the primary result?**
No. The primary block is still the fixed-horizon test; the sequential block is additional,
with its own `can_stop`. The dashboard shows both so the trade is visible.

**Q. Why `tau_squared = 0.001` by default?**
It's the variance of the mixing prior on the effect size; smaller values give more power for
small effects at the cost of slower stopping for large ones. It's in
`SequentialTestingConfigInput` and adjustable per experiment.

**Q. How does `epsilon_greedy` fit a seeded design if it's not stochastic?**
`BanditService.is_stochastic` says only Thompson draws random numbers; UCB1 and ε-greedy
weights are deterministic functions of the counts (ε-greedy's exploration share is an even
split, not a coin flip), so they record `NULL` seed provenance.

---

## Day 20 — SDKs and the endpoint contract

### What & why

Fifteen SDKs is a lot of code to keep honest. The way Experimently does it is to make the SDK
_small_ — a thin HTTP client with a cache — and to make the **contract** the thing that's
specified, tested and shared. `docs/sdk-guide.md` states it in one table:

| Purpose                                 | Method and path                                                                      | Response                                                                          |
| --------------------------------------- | ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| Assign a user to an experiment (sticky) | `POST /api/v1/tracking/assign` `{experiment_key, user_id, context?}`                 | `{experiment_key, user_id, variant_id, variant_name, is_control, configuration}`  |
| Evaluate a flag                         | `GET /api/v1/feature-flags/evaluate/{flag_key}?user_id=…&context=<url-encoded JSON>` | `{key, enabled, config, reason}`                                                  |
| All flags for a user                    | `GET /api/v1/feature-flags/user/{user_id}`                                           | `{flag_key: boolean, …}`                                                          |
| Track one event                         | `POST /api/v1/tracking/track`                                                        | stored event                                                                      |
| Track up to 100 events                  | `POST /api/v1/tracking/batch` `{events: [...]}`                                      | `{success_count, failure_count, errors}`                                          |

and three rules: **the server decides** (no local bucketing), **cache per user + key** (never
cache failures), and **key-less events fan out** to every cached assignment and evaluated
flag for that user.

#### Why "the server decides" had to be re-established

Until September 2026 the React SDK bucketed locally, called endpoints that didn't exist, and
sent a user id in a custom header the API ignored — it "worked" in unit tests that mocked
`fetch`. The rewiring (PR #67) replaced every SDK's client with a port of one reference
design and added a **live contract harness** that runs each SDK's `contract_smoke` against a
real backend in CI. The consistent-hash utility survived as an _export_, pinned across
languages by golden vectors, but no SDK uses it to decide anything.

### How Experimently does it

The reference client is the Python one. Its request core is small enough to read whole:

```python
# 📁 sdk/python/experimentation/client.py
    def _request(self, method: str, path: str, *, body=None, query=None) -> Any:
        """Perform one API call and return the decoded JSON body (``None`` if empty).

        Retries exactly once on 429, sleeping for ``Retry-After`` (capped at
        :data:`MAX_RETRY_AFTER_SECONDS`). Raises :class:`ExperimentationError`
        for any non-2xx response, network failure or undecodable body.
        """
        url = self.api_url + path
        if query:
            url += "?" + urlencode(query, quote_via=quote)
        data = None if body is None else json.dumps(body, default=_json_default).encode("utf-8")

        response = self._send(method, url, data)
        if response.status == 429:
            wait = _retry_after_seconds(response)
            self._sleep(wait)
            response = self._send(method, url, data)

        if response.status < 200 or response.status >= 300:
            raise ExperimentationError(f"{method} {path} failed with HTTP {response.status}", status=response.status, body=response.text())
        ...

    def get_assignment(self, experiment_key: str, user_id: str, user_attributes=None) -> Assignment:
        cached = self._assignments.get(user_id, experiment_key)
        if cached is not None:
            return cached

        body: Dict[str, Any] = {"experiment_key": experiment_key, "user_id": user_id}
        if user_attributes is not None:
            body["context"] = dict(user_attributes)
        data = self._request("POST", "/api/v1/tracking/assign", body=body)
        ...
        self._assignments.set(user_id, experiment_key, assignment)
        return assignment

    def get_variant(self, experiment_key: str, user_id: str, user_attributes=None) -> str:
        """``get_assignment(...).variant_name``, or ``default_variant`` on any failure."""
        try:
            return self.get_assignment(experiment_key, user_id, user_attributes).variant_name
        except ExperimentationError as exc:
            logger.warning("get_variant(%s, %s) failed: %s", experiment_key, user_id, exc)
            return self.default_variant
```

Two API surfaces on purpose: `get_assignment` _raises_ (for callers who need to know), and
`get_variant` _swallows_ and returns `"control"` (for callers rendering a page). `track` never
raises. A `Transport` interface (`UrllibTransport` by default, `FakeTransport` in tests)
keeps the client dependency-free and unit-testable without a server.

The cross-SDK hash, pinned by `tests/sdk-contract/golden-vectors.json`, is the same
first-four-bytes little-endian function you met in the holdout service:

```python
# 📁 sdk/python/experimentation/hashing.py
def consistent_hash(user_id: str, flag_key: str) -> float:
    """Return the bucket for ``(user_id, flag_key)`` as a float in ``[0.0, 1.0)``."""
    (value,) = struct.unpack("<I", _digest(user_id, flag_key)[:4])
    return value / _HASH_DIVISOR
```

```go
// 📁 sdk/go/hash.go
func ConsistentHash(userID, key string) float64 {
	input := userID + ":" + key
	digest := md5.Sum([]byte(input)) //nolint:gosec
	v := binary.LittleEndian.Uint32(digest[:4])
	return float64(v) / hashDivisor
}
```

```typescript
// 📁 sdk/js/src/hash.ts
/** 2^32 — the divisor shared by every SDK (NOT 2^32 - 1). */
const HASH_DIVISOR = 4294967296;

export function consistentHash(userId: string, flagKey: string): number {
  const digest = md5Bytes(`${userId}:${flagKey}`);
  const uint32 = new DataView(digest.buffer).getUint32(0, true);
  return uint32 / HASH_DIVISOR;
}
```

(The JS SDK ships its own MD5 so it has zero runtime dependencies in the browser. The `NOT
2^32 − 1` comment marks the exact off-by-one that once made two SDKs disagree on the last
bucket.)

The React SDK wraps the same client in a provider and hooks:

```typescript
// 📁 sdk/react/src/hooks/useExperiment.ts
export function useExperiment(experimentKey: string): ExperimentAssignment {
  const { client, user } = useExperimentation();
  const [state, setState] = useState<ExperimentAssignment>(() =>
    defaultAssignment(experimentKey, { loading: true })
  );

  useEffect(() => {
    let cancelled = false;
    setState(defaultAssignment(experimentKey, { loading: true }));

    client
      .assignExperiment(user, experimentKey)
      .then(assignment => {
        if (!cancelled) setState(assignment);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const error = err instanceof Error ? err : new Error(String(err));
          setState(defaultAssignment(experimentKey, { error }));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [client, user, experimentKey]);

  return state;
}
```

Control defaults while loading, cancellation on unmount, re-assignment when the user or key
changes (safe because the server is sticky). `useFeatureFlag`, `useMultipleFlags`,
`useTrackEvent`, `withExperimentation` and a `ServerClient` for SSR complete the surface;
`ExperimentationProvider` memoises the client on config primitives so an inline config object
doesn't recreate it every render.

The harness that keeps all of this true:

```
tests/sdk-contract/
├── golden-vectors.json           # {user_id, flag_key} → expected hash, for every SDK's unit tests
├── test_python_sdk.py            # reference implementation vs vectors      (PR gate: "SDK Contract Tests")
├── test_js_sdk.js
└── live/run_live_contract.py     # every SDK's contract_smoke vs a real API  (PR gate: "SDK Live Contract")
```

The live runner seeds `sdk_contract_ab` / `sdk_contract_flag`, starts the API, runs each SDK's
smoke (Python, JS, Go, Java, Swift, Ruby, PHP, .NET, edge, OpenFeature…) and checks the JSON
it prints: a sticky assignment, `enabled: true`, one keyed event, one key-less event that
fanned out. Toolchains missing on the runner are `SKIP` unless `--strict`. The status table in
`docs/sdk-guide.md` records which SDKs have actually _run_ against a backend — three
(Android, Flutter, Elixir) were rewired by inspection and never executed; the docs say so.

> **By the Way.** The Lambda functions in `backend/lambda/` are a _second server-side
> implementation_ of the assign and evaluate halves of the contract (with DynamoDB storage
> and a MurmurHash-style hasher in `shared/consistent_hash.py`). They predate the rewiring and
> use `traffic_allocation` as a 0.0–1.0 fraction where the API uses 0–100. They pass their
> own 301 tests in the PR gate, but an SDK pointed at API Gateway instead of the FastAPI app
> would get the same _shape_ and potentially different _buckets_. Week 4 covers them; know
> that they exist when someone asks "why did staging bucket me differently?".

### Build It

Run the offline vectors and one live smoke:

```bash
python -m pytest tests/sdk-contract/test_python_sdk.py -q -o addopts=""
node tests/sdk-contract/test_js_sdk.js

# live: seed, then run just the Python and JS SDKs strictly
python backend/scripts/seed_sdk_contract.py
python tests/sdk-contract/live/run_live_contract.py --sdk python --sdk js --strict
```

Then write the smallest possible client yourself, in a language the repo doesn't have (or in
`bash` with `curl`): assign, evaluate, track keyed, track key-less-with-fan-out. Compare your
fan-out list to what `sdk/python/experimentation/client.py::track` does.

### Key takeaways

- Four endpoints + `X-API-Key`; the server decides; cache per user + key; failures never
  cached; key-less events fan out.
- Reference client: Python (`Transport` seam, one 429 retry honouring `Retry-After`,
  raise-vs-swallow API pairs); React adds provider + hooks with control defaults.
- `consistent_hash` = MD5 first 4 bytes LE ÷ 2³², pinned by golden vectors in every language;
  a utility, not a decision.
- The live contract harness runs each SDK's smoke against a real API in the PR gate; the
  status table is honest about which have run.
- The Lambdas are a second implementation with a different hasher and unit convention.

### Q&A

**Q. Why not generate all fifteen SDKs from the OpenAPI spec?**
Generated clients expose every endpoint (including admin ones an SDK must never call), don't
encode the fan-out and caching rules, and produce idiomatic code in none of the languages.
Fifteen small hand-written ports of one reference design, checked by one harness, was
judged cheaper to maintain than fifteen generated ones plus wrappers.

**Q. What does the SDK cache, and for how long?**
Successful assignments and flag evaluations, keyed by `(user_id, key)`, for
`cache_ttl_seconds` (300 s default). Attributes are assumed stable per user — change them
and call `clear_cache()`. The React SDK also de-duplicates in-flight requests.

**Q. Can I use OpenFeature?**
Yes: `sdk/openfeature` (JS) and `sdk/openfeature-python` are providers over the same client,
and `/api/v1/openfeature` exposes the provider endpoints. Same contract underneath.

**Q. My SDK gets 429s under load. What should it do?**
Honour `Retry-After` (capped at 5 s in the reference client), batch events (100 per
`/tracking/batch`), and raise the server's `SDK_RATE_LIMIT_PER_MINUTE` if one IP legitimately
fans out that much traffic.

---

## Day 21 — Capstone: trace StreamPulse's crash spike to an automatic rollback

### What & why

StreamPulse is the second demo app: a simulated mobile video app with a device picker, a
"Player v2" feature flag, targeting by OS version and region and tier, a mutual-exclusion
group, a kill switch, and a scripted seven-step **rollout story** in which a crash spike on
Android 12 triggers a safety rollback. Today you narrate steps 1–4 through every module of
this week.

```
# 📁 demo/streampulse/simulator/rollout_story.py
  1  Internal + 5%      show the flag at 5% with the ``employee equals true`` rule
  2  25%                advance the rollout schedule to stage 2 → flag at 25%
  3  Incident           run traffic.py --incident android12 for 90 s, then the safety check
  4  Rollback           wait for the safety scheduler to roll back to 5% (or roll back by hand
                        when automatic rollbacks are off)
  5  Fix shipped        add ``app_version semver_gte 3.2.1 → 100%`` next to the employee rule
  6  50%                advance to stage 3 (after the 15-minute error window has cleared)
  7  100%               advance to stage 4, complete the schedule, remove the rules
```

### The trace

**Step 1 — Internal + 5%.** The seed created flag `streampulse_player_v2` at
`rollout_percentage = 5` with a dashboard-shape rule `employee equals true → 100%`, a
four-stage rollout schedule (5 → 25 → 50 → 100, time-based), and a safety config
(`error_rate` critical at 5%, `rollback_percentage = 5`). The simulator drives devices
through the React SDK's `useFeatureFlag('streampulse_player_v2')` with `context = {os,
os_version, region, tier, employee, app_version}`. For an employee device: the adapter
normalises the rule (Day 16), `expand_context` makes `employee` answer `user.employee`,
`match_targeting_rule` returns the rule, `_evaluate_percentage_rollout(flag, user, 100)` →
`True`, `reason: targeting_rule`. For everyone else: no match → global 5% →
`md5(device:streampulse_player_v2) % 100 < 5`, `reason: rollout`. Every evaluation writes a
`raw_metrics` row (Day 15).

**Step 2 — 25%.** The story advances stage 2 via `POST /rollout-schedules/stages/{id}/advance`
(or the rollout scheduler would, when `start_date` passes — `_as_utc` makes that comparison
safe, Day 17). `_activate_stage` sets `rollout_percentage = 25`. Buckets 5–24 turn on;
buckets 0–4 keep it (monotone rollout).

**Step 3 — Incident.** `traffic.py --incident android12` makes Android-12 devices in the
rollout report crashes: `POST /tracking/errors/batch` with the API key,
`feature_flag_key: streampulse_player_v2`, `error_type: "crash"`. Each becomes an `ErrorLog`
row on the flag. Meanwhile evaluations continue, so `raw_metrics` keeps counting.

**Step 4 — Rollback.** The safety scheduler ticks (every minute in the demo). Under the
`safety` advisory lock (Day 17): for each active flag with rollout > 0, load the config
(enabled), `check_feature_flag_safety` → `get_error_metrics` divides the last 15 minutes'
`ErrorLog` count by Σ `raw_metrics.count` → say 0.12 > 0.05 critical → `is_healthy: false`
(Day 18). Global `enable_automatic_rollbacks` is true; target is
`_rollback_target_percentage(config) = 5`; the flag is at 25 > 5, so: `execute_rollback` sets
`rollout_percentage = 5`, cancels the rollout schedule, writes a `SafetyRollbackRecord`
(`trigger_type = AUTOMATIC`, reason "error_rate exceeding threshold (0.12 > 0.05)"), and
`notify_safety_rollback` posts to Slack/email. The run record says `success` with
`metadata.rollbacks = 1`. Next tick: the window is still hot, but the flag is at 5 ≤ 5 → skip.

**What the devices see.** The React SDK's cache TTL expires (or the app re-evaluates on
foreground); `GET /feature-flags/evaluate/...` now returns `enabled: false, reason: rollout`
for buckets 5–24 and `true` for 0–4 and employees. Player v2 is back to the internal cohort
within a TTL.

**Steps 5–7** are the recovery: a second rule `app_version semver_gte 3.2.1 → 100%` (the
adapter's `_SEMVER_OPERATOR_MAP`), a wait for the error window to clear (`HEALTHY_WAIT_S =
16 min`), and manual advances to 50% and 100%.

### Build It

Run it:

```bash
SAFETY_CHECK_INTERVAL_MINUTES=1 ROLLOUT_CHECK_INTERVAL_MINUTES=1 ./demo/setup-local.sh
python demo/streampulse/simulator/rollout_story.py --step 1
python demo/streampulse/simulator/rollout_story.py --step 2
python demo/streampulse/simulator/rollout_story.py --step 3     # 90 s of crashes, then a check
python demo/streampulse/simulator/rollout_story.py --step 4     # waits for the scheduler
```

While step 4 waits, watch three places at once: the Admin → Safety page, the Admin →
Scheduler page (the `safety` runs), and
`select trigger_type, trigger_reason, previous_percentage, target_percentage from
experimentation.safety_rollback_records order by created_at desc limit 1`. Then run the
StreamPulse app at `:3300`, pick an Android 12 device with a bucket in 5–24, and see Player v1.

### Key takeaways

- Rule (Day 16) → percentage (Day 15) → evaluation writes metrics (Day 15) → client errors
  (Day 18) → scheduler tick under lock (Day 17) → threshold → rollback to
  `rollback_percentage` → notification → SDK cache expiry (Day 20).
- Every step is a row you can query and an endpoint you can call by hand.
- The demo's cadences (1-minute schedulers, 15-minute window) are the production
  parameters, just compressed.

---

## Week 3 Workshop

### Quiz

**1. A flag is at 30% globally with a rule `tier equals "premium" → 10%`. A premium user in
bucket 20 asks. Enabled? Reason?**
Disabled, `targeting_rule`. The rule matches, so its 10% replaces the global 30%; bucket
20 ≥ 10. The same user would be enabled without the rule.

**2. Which stored rule shapes does `normalise_targeting_rules` convert, and what happens to the
third?**
Native (`{"rules": [...]}`) and dashboard (`{"logical_operator", "groups": [...]}`) become
`TargetingRules`. The legacy list returns `None` and `FeatureFlagService._evaluate_rule`
evaluates it with the old six operators.

**3. Two API replicas each run the safety scheduler. How many rollbacks happen for one
unhealthy flag in one tick window?**
One. Both call `run_locked_tick("safety", …)`; `pg_try_advisory_lock` succeeds on one, the
other records a `skipped` tick. Even if both somehow ran, the second would hit the
"already at/below target" guard.

**4. What two tables does `error_rate` divide, over what window, and where do the numerator's
rows come from?**
`error_logs` ÷ Σ `raw_metrics.count` where `metric_type = flag_evaluation`, over the last 15
minutes. Numerator rows come from evaluation exceptions (server) and
`POST /tracking/errors[/batch]` (clients).

**5. Why is the mSPRT p-value "always valid"?**
Because Λ is a test martingale: the probability that it _ever_ exceeds 1/α under the null is
at most α, so stopping whenever `min(1, 1/Λ) < α` controls the false-positive rate regardless
of how often you look.

**6. The bandit scheduler reads stats from four sources. Name them in order, and say which one
made bandits work in the Community Edition.**
DynamoDB counters → PostgreSQL (assignments + conversions via the primary metric's
`event_name`) → persisted `BanditState` → zero priors. The PostgreSQL source.

### Exercises

**E3.1 — Bucket arithmetic.** For a flag rolled out 5 → 25 → 5 (rollback) → 50, list which
buckets had the feature at each stage, and name the property that guarantees no user was
"flickered" on–off–on during the 5 → 25 → 5 sequence except those in 5–24.

**E3.2 — Context aliasing.** Given the SDK context `{"country": "DE", "device": {"os":
"android"}, "user.tier": "premium"}`, list every key `expand_context` produces. Which of
`user.country`, `os`, `device.os`, `tier`, `app.tier` resolve, and to what?

**E3.3 — Lock semantics.** Explain what `scheduler_lock` yields when (a) another session
holds the lock, (b) the database is unreachable, (c) the tick raises. For (c), what is
recorded and what is propagated?

**E3.4 — Threshold design.** A flag has a baseline error rate of 0.8% and 400 evaluations
per 15 minutes at 5%. Propose warning and critical thresholds and a `rollback_percentage`,
and explain what would happen at 100% rollout with the same thresholds.

**E3.5 — Contract port.** Sketch (in pseudocode or a language of your choice) `track(user,
event, {experimentKey?, flagKey?})` for a new SDK, including the fan-out rule and the
"never raises" guarantee.

### Solutions

**S3.1** — 5%: buckets 0–4. 25%: 0–24. Rollback to 5%: 0–4 (buckets 5–24 lose it). 50%:
0–49. Users in 0–4 had the feature throughout; 5–24 went on–off–on, which is the intended
effect of a rollback; 25–49 saw it only at the end. The guarantee is that inclusion is
`bucket < percentage` with a fixed per-user bucket — monotone in the percentage — so the
_only_ way a user loses the feature is a percentage decrease, and the set that loses it is
exactly the highest buckets.

**S3.2** — Flatten: `country`, `device` (the dict), `device.os`, `user.tier`. Forward
aliases for the scalar top-level keys: `user.country`, `device.country`, `app.country`
(from `country`); `user.tier` is already dotted and is skipped by the forward pass. Reverse
aliases: `device.os` → `os`; `user.tier` → `tier`; `user.country` was just added so
`country` already exists. Resolutions: `user.country = "DE"`, `os = "android"`,
`device.os = "android"`, `tier = "premium"`, `app.tier` → not present (aliases are only
generated from top-level scalars, and `user.tier` was explicit-dotted, so no `app.` alias
exists). Explicit keys win, so nothing sent is overwritten.

**S3.3** — (a) Yields `False`; `run_locked_tick` records a `skipped` Prometheus tick, logs
"lock held elsewhere", and returns `acquired=False` without running the tick. (b) Yields
`False` too, with a warning "could not acquire (database unavailable)"; same skip. (c) The
lock is released by the context manager's `finally`; the run is recorded as `failed` with
the exception's message in `scheduler_runs`; `scheduler_ticks_total{status="failed"}` is
incremented; then the exception is **re-raised** so the scheduler's own loop logs it and
sleeps 60 s before the next attempt. Only `CancelledError` (shutdown) is propagated without
recording.

**S3.4** — Warning at 2% (≈2.5× baseline), critical at 4% (5× baseline) with
`comparison_type = greater_than`; `rollback_percentage = 5` (keep the internal cohort). At
5% and 400 evaluations, 4% is 16 errors in 15 minutes — a real signal, not noise. At 100%
the denominator is ~8,000 per window, so the same 4% is 320 errors; the _rate_ threshold
scales correctly, which is the point of dividing by evaluations. A _count_ threshold would
have to be re-tuned at every stage.

**S3.5**

```
track(user, eventName, opts = {}):
    body = {event_type: eventName, user_id: user.id, value: opts.value, metadata: opts.metadata,
            timestamp: now()}
    targets = []
    if opts.experimentKey: targets.append({experiment_key: opts.experimentKey})
    if opts.flagKey:       targets.append({feature_flag_key: opts.flagKey})
    if targets is empty:                                   # fan-out rule
        for key in cache.assignmentsFor(user.id): targets.append({experiment_key: key})
        for key in cache.flagsFor(user.id):       targets.append({feature_flag_key: key})
    if targets is empty: log("nothing to attach"); return   # never raises
    events = [merge(body, t) for t in targets]
    try:
        if len(events) == 1: POST /api/v1/tracking/track  events[0]
        else: for chunk in chunks(events, 100): POST /api/v1/tracking/batch {events: chunk}
    except any: log(warning); return                        # never raises
```

The reference implementation additionally honours one 429 retry and reports
`{success_count, failure_count}` from batch responses to the caller's logger.

---

**Next:** [Week 4 — Production Engineering →](./week-4-production.md)

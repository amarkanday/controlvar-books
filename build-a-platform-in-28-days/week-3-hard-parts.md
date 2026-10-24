# Week 3 — The Hard Parts

### Days 15–21 · Flags, the Rules Engine, Schedulers, Safety, Statistics, SDKs

[← Week 2](./week-2-api-and-data.md) · [Contents](./README.md) · [Week 4 →](./week-4-production.md)

Weeks 1 and 2 were the request/response spine. This week is everything that makes Controlvar
more than a CRUD app over five tables: flags that turn on for 5% of users and then 25%, targeting
rules with twenty operators and three storage shapes, five background loops that must not race
across replicas, a safety net that rolls a flag back when the crash rate spikes, a statistics
engine that lets you peek at results without lying to yourself, and sixteen SDKs that all speak
one contract. By Day 21 you'll narrate a crash spike turning into an automatic rollback.

> **Which code this chapter reads.** Every quoted file was checked against Controlvar v0.36.4
> (`79f8ed18`). Three things in it are newer than the first edition of this chapter and are
> covered where they belong: flag and experiment targeting rules refuse unknown keys at every
> level and can name **segments** (Day 16), a ruleset that cannot be evaluated is abandoned
> rather than guessed at (Days 15 and 16), and error reports count toward automatic rollback only
> when a server-side key sent them (Day 18). The edge bootstrap and OpenFeature flag listings
> are gone (Day 20).

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
| 18  | Safety & auto-rollback             | Error logs ÷ evaluations over 15 minutes; 0% turns the flag off     |
| 19  | The statistics engine              | Fisher first; sequential, CUPED, Bayesian, bandits beside it        |
| 20  | SDKs & the contract                | Six endpoints, server decides (local evaluation is the beta exception), golden vectors, fan-out |
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
   switch) or `ARCHIVED` evaluates to `enabled: false, reason: "inactive"` with HTTP 200. Only an
   unknown key is a 404. The client should treat a killed feature as off, never as an error to
   log.

### How Controlvar does it

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
    def evaluate_flag_detailed(
        self,
        flag: FeatureFlag,
        user_id: str,
        context: Optional[Dict[str, Any]] = None,
        memberships: Optional[SegmentMemberships] = None,
    ) -> Dict[str, Any]:
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
                matched = self._match_targeting_rule(
                    flag, user_id, context or {}, memberships
                )
                if matched is not None:
                    targeting_rule_id, rule_percentage = matched
                    reason = REASON_TARGETING_RULE
                    result = self._evaluate_percentage_rollout(flag, user_id, rule_percentage)
                else:
                    # No rules, no match, or an uninterpretable shape: global rollout
                    reason = REASON_ROLLOUT
                    result = self._evaluate_percentage_rollout(flag, user_id)

        except PatternUnevaluable as exc:
            # Abandon the whole ruleset: falling through to the global rollout
            # would serve the flag to users a rule was written to leave out.
            report_unevaluable(exc, f"flag:{flag.key}")
            targeting_rule_id = None
            result = False
            reason = REASON_ERROR

        except TimeWindowUnreadable:
            # The same for a time_window the engine cannot read (#1145),
            # already reported by the rules engine. No error-log row: the
            # rule, not the flag's behaviour, is at fault.
            targeting_rule_id = None
            result = False
            reason = REASON_ERROR

        except SegmentMembershipUnavailable as exc:
            # The same: membership of a referenced segment is unknown, so the
            # ruleset is abandoned. No error-log row: the safety monitor
            # counts those, and a segment archived or a database blip is not
            # the flag misbehaving.
            if memberships is None:
                log_unavailable(f"flag:{flag.key}", exc.segment_ids)
            targeting_rule_id = None
            result = False
            reason = REASON_ERROR

        except Exception as e:
            error = str(e)
            # The type only: the exception's text can repeat a user's value.
            logger.error(f"Error evaluating flag {flag.key}: {type(e).__name__}")
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
                logger.error(f"Failed to record metrics: {type(metrics_error).__name__}")

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
an ordinary exception becomes an `ErrorLog` row. Those two tables are what safety monitoring
(Day 18) divides to get an error rate. Two other writers feed them: clients add their own
errors through `POST /tracking/errors`, and a server-side SDK that evaluates flags locally
(Day 20, beta) reports its evaluation counts through `POST /tracking/evaluations`, so the
denominator still counts evaluations the API never saw.

The `except` branches differ on purpose, and the first three are one idea in three costumes. A
condition the engine **cannot evaluate** abandons the whole ruleset, and the flag is off with
`reason: "error"`: neither the global rollout nor a `default_rule` is consulted, and **no
`ErrorLog` row is written**, so the safety monitor does not count it, because it is the rule and
not the flag's behaviour that is at fault. Falling through to the global rollout would serve the
flag to users a rule was written to exclude. The three kinds are:

- `PatternUnevaluable`: RE2 refuses the pattern, the value is longer than 256 characters or not
  valid UTF-8, or (since the class grew an `OUT_OF_RANGE` reason) a number is too large to
  compare or a timestamp too large for any date;
- `TimeWindowUnreadable`: a `time_window` value with none of the keys that say when it is open
  (an old window written with `start` and `end` was silently open at every moment), with an
  unknown timezone, `days` outside 0-6, or a `start_time` without its `end_time`;
- `SegmentMembershipUnavailable`: a rule references a segment whose membership cannot be decided
  (Day 16). Answering "not a member" would make `not_in_segment`, or a `NOT` group, match
  everyone, so the ruleset is refused instead.

Any other exception is the generic branch: off, `reason: "error"`, and an `ErrorLog` row (the log
line names only the exception type, because the text can repeat a user's value).

> **Watch Out!** The `finally` writes to the database on every evaluation. At SDK volumes that
> is a real write load — `RawMetric` is the platform's highest-cardinality table — and it's the
> reason the metrics scheduler (Day 17) aggregates raw metrics into `aggregated_metrics` on a
> 15-minute cadence. There is no second evaluation service to take the load: no stack deploys
> the Lambda code under `backend/lambda/` (Day 20). The lever that exists is local evaluation
> (Day 20, beta), which answers flags inside a server-side SDK and reports only counts. If you
> add work to `evaluate_flag_detailed`, remember it runs per request, per user, per flag.

The lifecycle around evaluation is small, and since 0.17.0 it has a strict contract:

- **Create.** `POST /feature-flags` makes a flag that is **off** unless the body sends
  `"is_active": true`. A field the API does not read answers 422 (`extra="forbid"`), an explicit
  `null` on `key`, `name`, `is_active`, `rollout_percentage` or `default_value` answers 422,
  `status` is read-only (send `is_active` instead), `default_value` accepts only `false` for now,
  and a duplicate key answers 409. A GET body can be sent back unchanged, unless its
  `targeting_rules` are ones a save now refuses (Day 16).
- **On and off.** `POST /feature-flags/{id}/activate` and `/deactivate` (the kill switch), and
  `PUT` with `is_active`. An **archived** flag refuses every request that would turn it on with
  400 ("unarchive it before turning it on"); `POST /feature-flags/{id}/unarchive` (beta) brings
  it back as INACTIVE, never ACTIVE, so turning it on stays a separate step.
- **Change.** `PUT` changes `rollout_percentage` or `targeting_rules`;
  `POST /feature-flags/bulk-toggle` changes many flags at once (a plain POST; the SSE audit
  stream is a separate route, `GET /audit-logs/stream`).
- **Rollout schedules.** A `RolloutSchedule` with ordered `RolloutStage`s (5% → 25% → 50% →
  100%), each `time_based`, `metric_based` or `manual`, advanced by the rollout scheduler or by
  `POST /rollout-schedules/stages/{id}/advance`. Percentages must be non-decreasing across
  stages; active schedules can't have stages deleted. A schedule can be **paused**, by hand or
  by a safety rollback (Day 18); a paused schedule is never advanced, by a tick or by hand,
  until `POST /rollout-schedules/{id}/activate` resumes it.

Who may do any of this is a role question, not an ownership one: ADMIN and DEVELOPER may
create, change and delete any flag; ANALYST and VIEWER may read flags and change none, even one
they created (Week 2, Day 11).

### Build It

Watch `reason` change as you flip the levers on one flag (the ShopLab seed's search flag,
`shoplab_new_search`, at 10%; or one you create, remembering that a new flag is off unless you
send `"is_active": true`):

```bash
FLAG=shoplab_new_search   # any active flag key
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

# rules are validated on save: a misspelt key answers 422 with the place, not a silent non-match
curl -s -X PUT $API/feature-flags/$FID -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"targeting_rules":{"logical_operator":"AND","groups":[{"logical_operator":"AND","conditions":[{"attribute":"employee","operator":"equal","value":true}]}]}}'
                                               # → 422 … groups[0].conditions[0].operator: unknown operator
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
- Every evaluation writes a `RawMetric` (with latency); every ordinary failure writes an
  `ErrorLog`; safety divides them. An unevaluable pattern condition turns the flag off with
  `reason: error` and writes no `ErrorLog`.
- Kill switch = `deactivate` → `inactive` with 200; archived → `inactive` too, and refuses to
  turn on until unarchived; unknown key → 404.
- A new flag is off unless created with `"is_active": true`; unknown fields answer 422.

### Q&A

**Q. Experiments hash on their key too. Is it the same function?**
Same input shape, different reading of the digest. Both hash `"{user_id}:{key}"` with MD5, and
both use the public key (an experiment falls back to its UUID only when `key` is empty), so
cohorts are stable across environments that share keys. But the flag rollout reads the **whole**
128-bit digest `% 100`, while experiment assignment uses `bucket_of` in
`backend/app/core/consistent_hash.py`: the **first four bytes** little-endian, divided by 2³²,
the function the SDK golden vectors pin (Day 20). For `user-123` / `my-flag` the flag rollout
gives bucket 79 and `bucket_of` gives 69. Do not "unify" them in passing: either change
re-buckets every user. Renaming a flag's key (the API allows it) reshuffles its cohort for the
same reason.

**Q. Can a flag return a value other than a boolean?**
No. The model still has a `variants` JSONB column, but flag responses no longer carry
`variants`, `metrics`, `rules` or `last_evaluated` (0.17.0), the evaluate response's `config` is
always `None`, and the new `default_value` field accepts only `false` for now. The React SDK's
`useVariant` returns a variant _string_ only because it's built on `useFeatureFlag`'s value.
Treat flags as booleans; use an experiment when you need arms.

**Q. What's a `FeatureFlagOverride`?**
An unused table. The model (a per-user forced `value`, optional `reason` and `expires_at`,
unique on `(flag, user)`) is defined and registered, but no route reads or writes it and
`evaluate_flag_detailed` never consults it. For "turn it on for this customer", add a targeting
rule on the user's id or an attribute.

**Q. My flag is at 100% but `reason` says `targeting_rule` and `enabled` is false.**
A rule matched with a percentage below 100 (or 0). The rule's percentage _replaces_ the
global one. Look at the flag's `targeting_rules` and at `rule_id` in the service's return
value; the dashboard's rule editor shows each rule's percentage.

**Q. `reason` says `error`, but there is nothing in `error_logs`.**
Look for a `regex` condition. Patterns are evaluated with RE2: `\w`, `\d`, `\s` and `\b` are
ASCII-only, `$` matches only at the very end, and lookaround and backreferences are refused. A
refused pattern, or a context value over 256 characters, makes the condition unevaluable, and the
flag is then off for that user with `reason: error` and no error-log row.
`python -m backend.scripts.check_regex_rules` lists the stored patterns RE2 refuses or may now
match differently (it reads and never writes).

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
  `FeatureFlagService._evaluate_rule` — still evaluated when it is already stored, but **refused
  on write** since 0.17.0 ("a list of rules is not supported; use the groups shape"), and a
  legacy `context` condition matches only through one of its six operators (`eq`, `ne`, `gt`,
  `lt`, `contains`, `in`); any other operator, or none, no longer matches;
- the **native** shape `{"version", "rules": [{"id", "rule": {...}, "rollout_percentage",
  "priority"}], "default_rule"}` that the engine and the experiment targeting path use;
- the **dashboard** shape `{"logical_operator", "groups": [{"logical_operator", "conditions":
  [...]}]}` written by the React rule builder, with operators named `equals`, `semver_gte`,
  `is_null`.

A migration to one shape would have to rewrite production JSONB and every test fixture. The
adapter is cheaper and — importantly — makes the _behaviour_ identical: a dashboard rule and
a native rule with the same conditions match the same users.

### How Controlvar does it

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
    # Segment membership (#440). The condition's attribute is ``segment`` and
    # its value a segment id; membership is decided by the server before the
    # rules are evaluated (``rules_engine.segment_condition_holds``), never by
    # an attribute in the context.
    IN_SEGMENT = "in_segment"
    NOT_IN_SEGMENT = "not_in_segment"


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

This model validator is one layer of the check a flag's rules get on save, not the whole of
it. Before 0.17.0 a flag stored whatever JSON it was sent, and a rule the evaluator could not
read simply never matched. Now `FeatureFlagCreate` and `FeatureFlagUpdate` run
`validate_flag_targeting` from the adapter (their `checked_targeting_rules` validator), and a
rule the evaluator would not apply as written answers **422 with a fixed message naming the
place and the reason**, never the submitted value:
`groups[0].conditions[0].operator: unknown operator`, `rules[0].rule: unknown key`,
`targeting rules: a list of rules is not supported; use the groups shape`. It refuses an
unknown key at every level, native groups nested more than 10 levels deep, a native value
with more than 1,000 rules, groups and conditions (and dashboard-shape flag rules with more than
1,000 groups and conditions in all), list operators with more than 1,000 items, and
a rule `rollout_percentage` that is not an integer from 0 to 100. Experiments get the same
checks through `validate_experiment_targeting`. One consequence for clients: a GET body whose
stored rules a save would now refuse cannot be PUT back unchanged.

The engine walks groups recursively with `and`/`or`/`not` and applies operators through one
dispatch function:

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

The adapter's two read-side functions are the ones you'll call most.
`normalise_targeting_rules(raw, owner=...)` turns stored JSON into `TargetingRules` (or `None`
for the legacy list, which the flag service still evaluates itself, and for anything it cannot
convert; it never raises, and logs an unconvertible shape once per `owner`, such as
`flag:<key>`). `expand_context` flattens and aliases the SDK's context:

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

    :class:`backend.app.core.pattern_match.PatternUnevaluable` propagates
    unchanged: when a pattern condition cannot be evaluated the caller abandons
    the whole ruleset, and neither a later rule nor ``default_rule`` applies.
    """
```

#### Segments: a condition whose answer is not in the context

Everything above decides from attributes the SDK sent. A **segment** is the other kind of
audience: a named, reusable group you maintain once (`/api/v1/segments`, the dashboard's
Segments pages) and reference from many flags and experiments. A segment is either a set of
**rules** (users whose attributes match them) or an **id list** (users whose `user_id` is stored
in `segment_members`; the kind is fixed at creation). A condition names it as
`{"attribute": "segment", "operator": "in_segment" | "not_in_segment", "value": "<segment id>"}`.

The design rule is stated in the schema's own comment: membership is "decided by the server
before the rules are evaluated, never by an attribute in the context". `segment_membership.py`
resolves, for one user, membership of every segment a ruleset references, in at most two queries
(one for the segments, one for the id lists among them), and `with_memberships` puts the answer
into the evaluation context under the key `$segments`, **after removing whatever the request sent
under that name**. A `$` cannot appear in a rule attribute, so no rule can read the key directly,
and a client cannot forge its own membership by sending a context value. The engine answers a
segment condition with `segment_condition_holds`, before and instead of any attribute lookup.

The failure mode decides the design. If a referenced segment is unknown, not active, has stored
rules that are no longer valid, or the database failed, its membership is *unavailable*. The
engine raises `SegmentMembershipUnavailable` and the whole ruleset is abandoned (Day 15): the flag
answers `reason: error`, an experiment treats the user as ineligible. Answering "not a member"
instead would make `not_in_segment` (or a `NOT` group) match exactly the users whose membership
nobody knows. Write time is stricter than evaluation time, so this should be rare: the routes
that save rules check that every referenced segment exists and is active
(`segment_reference_problem`), and the adapter refuses a segment condition whose attribute is not
`segment`, whose value is not the canonical text of a UUID, that sits in a native `default_rule`
(which is returned without evaluating its conditions, so the condition would mean "every user"),
or a ruleset naming more than ten distinct segments. A segment's own rules cannot refer to
another segment.

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

You should see the aliases (`user.os`, `device.os`, `app.os`, and `version` from
`app.version`), `dashboard → dashboard`, `native → r1` and `legacy list → None`. Then ask the
write-time validator what it thinks of the legacy shape:

```bash
python -c "
from backend.app.core.targeting_adapter import validate_flag_targeting
validate_flag_targeting([{'type': 'context', 'conditions': []}])"
# → TargetingRulesError: targeting rules: a list of rules is not supported; use the groups shape
```

Then run the engine's benchmarks. The latency ceilings are marked `benchmark` and skipped
unless `RUN_BENCHMARKS=1`, because an absolute number measures the machine as much as the code;
the cost tests in the same file (how many conditions an evaluation looks at) always run:

```bash
RUN_BENCHMARKS=1 python -m pytest backend/tests/unit/core/test_rules_performance_benchmarks.py \
  -p no:cov -q > /tmp/rules-bench.txt 2>&1; echo "exit $?"; cat /tmp/rules-bench.txt
```

(Redirect to a file rather than piping through `tail`: a pipe replaces pytest's exit status
with `tail`'s, and a failed run then reads as success.)

### Key takeaways

- One canonical rule shape (`TargetingRules → TargetingRule → RuleGroup → Condition`), 20+
  operators.
- Flag rules are validated on save by `validate_flag_targeting`: 422 with a fixed
  `<path>: <reason>` message; unknown keys refused at every level; the legacy list refused.
- Three stored shapes; `normalise_targeting_rules` converts native and dashboard, returns
  `None` for the legacy list so the flag service's old evaluator handles rows already stored.
  Never add a fourth.
- `expand_context` flattens nested dicts to dotted keys and aliases top-level keys under
  `user.`/`device.`/`app.`; explicit keys win.
- `match_targeting_rule` matches without bucketing so the caller can hash on its own salt.
- `RulesEvaluationService` wraps the engine with compilation and result caches plus latency
  metrics.

### Q&A

**Q. Where do I add a new operator?**
More places than it used to be, because three things now check the operator list:

1. `OperatorType` in `schemas/targeting_rule.py` (plus the compatibility validator if it needs
   a collection or an `additional_value`) and the dispatch in `rules_engine.apply_operator`.
2. If the dashboard should offer it: `_SIMPLE_OPERATOR_MAP` in the adapter (with
   `_SEMVER_OPERATOR_MAP` and `_SEGMENT_OPERATOR_MAP`, which together feed
   `DASHBOARD_OPERATORS`), the list the write-time validator accepts (anything else is
   `unknown operator`); and the frontend's `OPERATORS_BY_TYPE` in
   `frontend/src/types/targeting.ts` (the flag pages read it through `FLAG_OPERATOR_OPTIONS` in
   `frontend/src/utils/targeting.ts`).
3. Local evaluation (Day 20): `tests/sdk-contract/ruleset-vectors.json` carries the
   `local_operators` an SDK may evaluate itself. It is generated, never edited: run
   `python -m backend.scripts.generate_ruleset_vectors`, or
   `backend/tests/unit/services/test_sdk_ruleset_vectors.py` fails. An operator not on that list
   makes the flag server-evaluated, which is safe.

Then tests in `backend/tests/unit/core/test_rules_engine*.py`.

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
weights to refresh. Controlvar runs **five schedulers** as `asyncio` tasks inside the API
process, started by the lifespan on Day 8:

| Scheduler    | Module                       | Cadence (default)                 | Tick does                                             |
| ------------ | ---------------------------- | --------------------------------- | ----------------------------------------------------- |
| experiment   | `core/scheduler.py`          | `EXPERIMENT_SCHEDULER_INTERVAL_MINUTES` (15, at least 1) | start DRAFTs whose `start_date` passed, resume PAUSED ones whose `resume_at` passed, complete ACTIVE ones past `end_date` |
| rollout      | `core/rollout_scheduler.py`  | `ROLLOUT_CHECK_INTERVAL_MINUTES` (15) | advance time-based rollout stages                  |
| metrics      | `core/metrics_scheduler.py`  | 15 min                            | aggregate `raw_metrics` → `aggregated_metrics`       |
| safety       | `core/safety_scheduler.py`   | `SAFETY_CHECK_INTERVAL_MINUTES` (5) | check thresholds, auto-rollback                    |
| bandit       | `core/bandit_scheduler.py`   | `BANDIT_UPDATE_INTERVAL_MINUTES` (5) | recompute `BanditState.variant_weights`            |

#### Why in-process, and why that forces a lock

A separate worker service (Celery, a cron container) would be cleaner in the abstract. The
team chose in-process tasks because the smallest deployment (the core profile in compose) must
be one API container plus a database, and because each tick is small (a few queries). The cost arrives the moment you run
two API replicas — or two uvicorn workers: **every replica starts every scheduler**, and two
copies of "advance the rollout stage" racing against one database will advance it twice, or
roll a flag back twice, or double-count a bandit update.

The fix is a **PostgreSQL advisory lock**, taken per scheduler name, non-blocking: a replica
that doesn't get the lock _skips_ the tick rather than queueing. No Redis, no leader election,
no shared registry — the database every replica already talks to is the coordinator.

### How Controlvar does it

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

    async with async_scheduler_lock(name, engine=engine) as acquired:
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


def _acquire(engine: Engine, name: str) -> Tuple[Optional[Connection], bool]:
    """Check out a dedicated autocommit connection and try to take the lock."""
    try:
        conn = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    except Exception as exc:
        logger.warning("scheduler_lock(%s): could not connect (database unavailable): %s", name, exc)
        return None, False
    try:
        acquired = try_acquire(conn, name)
    except Exception as exc:
        logger.warning("scheduler_lock(%s): could not acquire (database unavailable): %s", name, exc)
        conn.close()
        return None, False
    ...
    return conn, acquired


@asynccontextmanager
async def async_scheduler_lock(name: str, engine: Optional[Engine] = None) -> AsyncIterator[bool]:
    engine = engine or _default_engine()
    conn, acquired = await asyncio.to_thread(_acquire, engine, name)
    try:
        yield acquired
    finally:
        await asyncio.to_thread(_release, conn, name, acquired)
```

Five details carry the design. `pg_try_advisory_lock` (not `pg_advisory_lock`) is
_non-blocking_ — the loser skips. The lock is _session-scoped_, so it's taken on a dedicated
connection checked out for the block and explicitly released by `_release` (closing the
connection would release it too, but explicit keeps pooled connections clean). That connection
runs in **AUTOCOMMIT**: a session lock needs no transaction, and without autocommit the
`SELECT` would leave the connection "idle in transaction" for the whole tick, which managed
PostgreSQL services kill after `idle_in_transaction_session_timeout`, silently releasing the
lock mid-tick. `hashtext(name)` is PostgreSQL's own text hash, so every replica derives the same
32-bit key from the same name with nothing to share. And the scheduler's async variant does the
blocking acquire and release in a worker thread (`asyncio.to_thread`), so a slow or
unreachable database stalls that thread, never the event loop serving HTTP requests. If the
database is down, the manager yields `False` and the scheduler skips — a scheduler must never
crash the API because it couldn't coordinate. (The synchronous `scheduler_lock` context
manager wraps the same `_acquire`/`_release` pair for code that is not a coroutine.)

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
"every N minutes on the clock" — fine for work that is idempotent and due-based. The experiment
scheduler is built at import with the class default of 15; the lifespan sets its
`interval_minutes` from `EXPERIMENT_SCHEDULER_INTERVAL_MINUTES` before starting it (a value
below 1 is refused at start-up, because 0 would spin the loop without sleeping).

#### What a tick does, and what it must not undo

Four rules have been added to the ticks since the skeleton was written; the first three each came after a bug:

- **One item's failure is that item's failure.** The experiment tick flushes each activation
  and completion inside its own savepoint (`db.begin_nested()`), and the rollout tick processes
  each schedule inside its own savepoint and commits it on its own. A row the database refuses
  is rolled back alone and counted in `items_failed`; the rest of the tick still happens, and the
  run is recorded as `partial` rather than undone.
- **A paused rollout is never advanced.** The rollout tick reads the ACTIVE schedules without a
  lock, so a schedule may have been paused since. Before changing anything it locks the flag
  row, then re-reads the schedule from the database and locks it (`_lock_active_schedule`: flag
  first, then schedule, the order every other writer takes them in). A schedule that is no
  longer ACTIVE raises `ScheduleNoLongerActive`; its savepoint is rolled back and it is counted
  in `metadata.schedules_skipped`, not as a failure. A manual
  `POST /rollout-schedules/stages/{id}/advance` makes the same check and refuses a paused
  schedule.
- **A pause lasts until someone ends it.** The experiment tick activates a `DRAFT` whose
  `start_date` has passed, or a `PAUSED` experiment whose **`resume_at`** has passed, and nothing
  else. A paused experiment's `start_date` is when it first started and is never a reason to
  activate it. (Before this, every paused experiment was re-activated on the next tick, and the
  scheduler ticks once at start-up, so a deploy undid every pause.) `PUT
  /experiments/{id}/schedule` on a PAUSED experiment stores its `start_date` as `resume_at`
  (null cancels the resume), and any change of status clears `resume_at`.

- **A transition a person did not make still leaves a record.** The experiment tick writes an
  audit entry for every scheduled start, resume and completion, and the rollout tick for every
  stage it starts (a `feature_flag_update` whose reason reads "rollout schedule stage started"),
  each with a system actor instead of a user. The two are deliberately different about failure.
  The experiment tick writes its entry in a savepoint of its own inside the row's savepoint, so a
  failed audit write costs only the entry: one ERROR names the exception type, and the
  transition still commits. The rollout tick writes its entry in the *same* transaction as the
  change to the flag, so if the entry cannot be written, the stage start fails with it and
  neither is kept: the flag's rollout does not widen unrecorded, and the next tick tries again.
  Which way to fail is a decision about which record you would rather lose, and the code states
  its answer in each place.

And one thing a tick deliberately does not do: an experiment's Bayesian decision is a
**recommendation only**. Nothing in the experiment tick stops an experiment on it.

#### The naive-datetime bug

The rollout scheduler's tick compares a stage's `start_date` to now. `rollout_stages`
timestamps live in `timestamp without time zone` columns, so SQLAlchemy returns naive
datetimes; the scheduler works in `datetime.now(timezone.utc)`. Python refuses to compare
naive with aware — `TypeError: can't compare offset-naive and offset-aware datetimes` — and
until the fix below every tick with an active schedule raised on the first comparison. The rollout
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

(The fix is older than the public repository, so it has no public pull-request number.)

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

The same change fixed a second logic bug in the same tick: the scheduler activated the _next_
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
# (needs the compose PostgreSQL running; the sync scheduler_lock below holds the lock on
#  its own connection, which is what "another replica" looks like to run_locked_tick)
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
curl -s $API/scheduler/health -H "Authorization: Bearer $TOKEN" | python -m json.tool
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
  dedicated AUTOCOMMIT connection, acquired off the event loop) + run record in
  `scheduler_runs` + Prometheus counters/histogram.
- A replica that doesn't get the lock skips; a database outage skips; a tick exception is
  recorded then re-raised to the loop's own backoff.
- Ticks open their own `SessionLocal()` and return `{items_processed, items_failed, metadata}`;
  one failing item is rolled back in its own savepoint and the rest of the tick stands.
- A paused rollout schedule is never advanced; a paused experiment resumes only at a scheduled
  `resume_at`; a Bayesian decision never stops an experiment.
- Naive DB datetimes vs aware `now()` raised on every rollout tick until `_as_utc`; the
  `rollout_stages`, `error_logs` and `raw_metrics` timestamps are naive UTC, so normalise at
  comparisons. (Stored event times are no longer naive: since 0.17.0 they are UTC with an
  offset.)

### Q&A

**Q. Why not one scheduler with five jobs?**
Independence. A slow metrics aggregation must not delay a safety check, and the lock is per
name so the five can run concurrently on different replicas. The shared runner gives them
identical guarantees without coupling their cadences.

**Q. There is a `with_retry` decorator in `core/scheduler_retry.py`. Which scheduler uses it?**
None. The module exists, but every loop does its own simple backoff: after an exception it logs
and sleeps 60 seconds before the next tick.

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
platform watches every **active** flag's error rate (and latency) over a trailing window,
compares it to per-flag thresholds, and — if the global setting allows — rolls the flag back
automatically, records why, notifies Slack/email, and **pauses** the flag's rollout schedule so
nothing raises it again behind your back.

The design decisions:

1. **Errors come from both sides, but only a trusted key's reports count.** Server-side
   evaluation failures are logged by `evaluate_flag_detailed`'s `finally` (Day 15). Client-side
   errors — a crash in the redesigned screen, a failed request behind the flag — arrive through
   `POST /api/v1/tracking/errors` (and `/batch`, up to 100 reports) with an API key, and become
   `ErrorLog` rows attached to the flag. Whether a report *counts* depends on the key that sent
   it (Day 10): a **server-side key** (the `sdk:ruleset` scope, owner able to change flags)
   stores the report with `source: "server"` and it counts like a failed evaluation; any other
   key, such as the one shipped in a browser or mobile app, stores it as `"client"`: it is kept
   and shown, but safety monitoring does not count it (`uncounted_error_count`). An app whose
   errors should be able to trigger a rollback sends them to its own server, which forwards them
   with a server-side key. One more kind of failure is deliberately _not_ counted: an
   unevaluable ruleset (Day 15) writes no `ErrorLog` row.
2. **Rate, not count.** `error_rate = errors / evaluations` over the last 15 minutes. A flag at
   5% with three crashes and 3,000 evaluations is fine; the same three crashes over 30
   evaluations is a 10% rate. Dividing by evaluations (from `raw_metrics`) is what makes the
   threshold meaningful at any rollout percentage. Evaluations a server-side SDK made locally
   (Day 20, beta) are reported through `POST /api/v1/tracking/evaluations` and land in the same
   denominator.
3. **Two kinds of rollback, chosen by the target.** Each flag's safety config has a
   `rollback_percentage` from 0 to 100 (anything else answers 422).
   - A target from **1 to 100 lowers the global rollout** to it, and only that. Users matched by
     a targeting rule keep their rule's percentage. StreamPulse's player flag rolls back to 5%,
     so engineers (the `employee` rule) and the first 5% of buckets keep seeing it while the
     crash is investigated.
   - A target of **0 turns the flag off**: its status becomes INACTIVE and its global percentage
     0, so every user gets `enabled: false, reason: "inactive"`, rule-matched users included.
4. **A rollback pauses the schedule.** Every ACTIVE rollout schedule of the flag is set to
   PAUSED in the same transaction, so the rollout scheduler cannot raise the percentage again on
   its next tick. Resuming is a person's decision (`POST /rollout-schedules/{id}/activate`).
5. **Don't roll back twice.** The error window stays "hot" for up to 15 minutes after a
   rollback. One function, `rollback_change`, decides whether a rollback would change anything;
   when it would not, the scheduler writes no record and sends no notification.
6. **Check every active flag.** The scheduler checks every ACTIVE flag whatever its global
   percentage, because a flag at 0% may still serve the users its rules match.

### How Controlvar does it

Three tables: `safety_settings` (global — `enable_automatic_rollbacks`, default metric
thresholds), `feature_flag_safety_configs` (per flag — `enabled`, a `metrics` JSONB of
`{name: {warning_threshold, critical_threshold, comparison_type}}`, `rollback_percentage`),
and `safety_rollback_records` (history — trigger type and reason, previous and target
percentage, success, and `executed_by_user_id` for a manual rollback). A flag with no config gets
a default with `id = DEFAULT_CONFIG_ID` (the nil UUID), never a 404.

**The measurement** is counted by the database. Nothing is loaded row by row:

```python
# 📁 backend/app/services/safety_service.py
    def get_error_metrics(self, db: Session, feature_flag_id: UUID, timeframe_minutes: int = 15) -> Dict[str, Any]:
        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=timeframe_minutes)

        if not db.query(FeatureFlag).filter(FeatureFlag.id == feature_flag_id).first():
            raise ValueError(f"Feature flag {feature_flag_id} does not exist")

        in_window = (
            ErrorLog.feature_flag_id == feature_flag_id,
            ErrorLog.timestamp.between(start_time, end_time),
        )
        counted = counted_error_filter()
        uncounted = uncounted_error_filter()
        error_count, uncounted_error_count = (
            int(n or 0)
            for n in db.query(
                func.count(ErrorLog.id).filter(counted),
                func.count(ErrorLog.id).filter(uncounted),
            )
            .filter(*in_window)
            .one()
        )
        ...   # error_types: COUNT ... GROUP BY error_type, the 100 most frequent counted ones

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

        error_rate = 0.0 if total_evaluations == 0 else error_count / total_evaluations
```

(`datetime.utcnow()` — naive — because `ErrorLog.timestamp` and `RawMetric.timestamp` are naive
columns; this is the convention Day 17's `_as_utc` exists to reconcile.) `counted_error_filter()` is
written as `IS DISTINCT FROM 'client'`, which matters: rows with no `source` at all (a flag
evaluation that failed on the server, or an old row with no `request_data`) must count, and a
plain `!=` would drop every row where the value is NULL. Two consequences are
worth knowing. A flag with errors but no recorded evaluations has an `error_rate` of 0.0, so the
check's `details` also carry `error_count` and `total_evaluations`, and the dashboard shows that
state. And the client cannot place an error in the window: `POST /tracking/errors` times each
row by the server's clock on receipt, keeps any `timestamp` the client sent in the row's metadata
as `client_timestamp`, and bounds `stack_trace` and `metadata` to 8 KiB each before storing them.

**The check** compares every configured metric to its critical threshold:

```python
# 📁 backend/app/services/safety_service.py
    async def check_feature_flag_safety(self, feature_flag_id: UUID) -> SafetyCheckResponse:
        feature_flag = self._require_flag(feature_flag_id)
        config = await self.get_feature_flag_safety_config(feature_flag_id)
        window = self._error_window(feature_flag_id)

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

A metric the platform can't measure is reported as `measured: false` with value 0 rather than
treated as a breach — a missing signal must not roll a flag back. The metrics it can measure are
`error_rate`, `error_count`, `total_evaluations` and the latency family (`latency`,
`avg_latency`, `p95_latency`, `max_latency`, `min_latency`).

**The decision** of what a rollback would do is one small function, shared by the rollback
itself, the scheduler and `should_rollback`:

```python
# 📁 backend/app/services/safety_service.py
def rollback_change(flag: Any, target: int) -> Optional[RollbackChange]:
    if flag_status_name(flag.status) != FeatureFlagStatus.ACTIVE.value:
        return None
    if target == 0:
        return "deactivate"
    if (flag.rollout_percentage or 0) > target:
        return "lower"
    return None
```

A flag that is not ACTIVE (INACTIVE or ARCHIVED) is never written. A target of 0 always means
"deactivate", whatever the global percentage. A target above 0 means "lower" only when the global
percentage is above it.

**The scheduler** closes the loop (quoted at length because its guards are the product's
promise):

```python
# 📁 backend/app/core/safety_scheduler.py
                    # If auto-rollback is enabled and safety check fails, trigger rollback
                    settings = await safety_service.async_get_safety_settings()

                    if settings.enable_automatic_rollbacks and not safety_check.is_healthy:
                        # Find what metric triggered the rollback
                        trigger_reason = "Automatic rollback due to safety issues"
                        failing = next(
                            (m for m in safety_check.metrics if not m.is_healthy),
                            None,
                        )
                        if failing is not None:
                            trigger_reason = (
                                f"Automatic rollback due to {failing.name} "
                                f"{describe_breach(failing)}"
                            )

                        # Roll back to the percentage configured for this flag
                        # (e.g. back to the internal 5% stage), not always to 0.
                        target_percentage = _rollback_target_percentage(config)

                        if rollback_change(feature_flag, target_percentage) is None:
                            # Already rolled back; the error window is still hot
                            # from before the rollback. Do not write another
                            # record or send another notification every cycle.
                            ...
                            continue

                        # Execute rollback
                        rollback_result = (
                            await safety_service.async_rollback_feature_flag(
                                feature_flag_id=feature_flag.id,
                                percentage=target_percentage,
                                reason=trigger_reason,
                                trigger_type=RollbackTriggerType.AUTOMATIC,
                            )
                        )
```

The reason is worded by `describe_breach`, in the metric's own unit and by the threshold's
comparison: one error in fourteen evaluations against `0.05` reads "above threshold (7.143% >
5%)", forty `total_evaluations` against a `less_than` floor of a hundred reads "below
threshold (40 < 100)". The same words go into the Slack and email alert (with the measured value
and threshold as separate fields), so the alert, the rollback record and the manual
`should_rollback` path cannot describe one breach three ways.

It runs over `FeatureFlag.status == ACTIVE`, with no filter on the percentage.
`_rollback_target_percentage` reads `rollback_percentage` from the config; a value stored before
the 0–100 rule existed is clamped with a warning naming the flag, and anything missing or
non-numeric rolls back to 0.

**The rollback** is one transaction, `execute_rollback`, and its order is the point:

```python
# 📁 backend/app/services/safety_service.py
            feature_flag = (
                db.query(FeatureFlag)
                .filter(FeatureFlag.id == feature_flag_id)
                .with_for_update()
                .first()
            )
            ...
            change = rollback_change(feature_flag, target_percentage)
            if change is None:
                message = _no_rollback_message(feature_flag, target_percentage)
                db.rollback()
                return RollbackResponse(success=False, ...)

            deactivated = change == "deactivate"
            if deactivated:
                feature_flag.status = transition(feature_flag.status, FlagVerb.OFF)
                feature_flag.rollout_percentage = 0
            else:
                feature_flag.rollout_percentage = target_percentage
            ...
            # The flag row is locked above; the schedules are locked after it,
            # the order every other writer takes them in.
            schedules = (
                db.query(RolloutSchedule)
                .filter(
                    RolloutSchedule.feature_flag_id == feature_flag_id,
                    RolloutSchedule.status == RolloutScheduleStatus.ACTIVE,
                )
                .order_by(RolloutSchedule.id)
                .with_for_update()
                .all()
            )
            for schedule in schedules:
                schedule.status = RolloutScheduleStatus.PAUSED
                db.add(schedule)
            paused_schedules = [str(schedule.id) for schedule in schedules]
            ...
            db.add(record)
            # The audit entry rides in a savepoint: if it fails, only it is
            # lost and the rollback below still commits (#221).
            AuditService.record_in_savepoint(
                db,
                actor=audit_actor,
                action=ActionType.SAFETY_ROLLBACK,
                entity_type=EntityType.FEATURE_FLAG,
                entity_id=feature_flag_id,
                entity_name=feature_flag.name or feature_flag.key,
                after={
                    "trigger_type": trigger_name,
                    "previous_percentage": previous_percentage,
                    "new_percentage": target_percentage,
                    "deactivated": deactivated,
                    "paused_schedules": paused_schedules,
                },
                reason=reason,
            )
            db.commit()
```

The flag row is locked first, then its schedules — the same order the rollout scheduler takes
them in (Day 17), so the two can never deadlock, and a tick that was waiting for the flag lock
re-reads the schedule, finds it PAUSED and skips it. The paused schedule ids come back in
`details.paused_schedules`, next to `details.deactivated`. Nothing commits until the record is
written, and a failure rolls everything back and answers `success: false`; it never raises.
Two newer details. The rollback **reads** the flag's safety configuration and never creates one:
it used to add a default configuration just to have an id to put on the record, which quietly
turned a flag that was on the global defaults into one with a configuration of its own. Now a
flag without one stays on the defaults, and `safety_rollback_records.safety_config_id` is NULL
for it (the column is nullable, and deleting a configuration keeps its records, setting the
column to NULL). And the rollback writes an audit entry (`safety_rollback` on the flag, naming
the calling user for a manual rollback and the safety monitor for an automatic one) in a
savepoint of the same transaction, so a failed entry costs only the entry: the rollback is the
promise, and the audit trail is not allowed to block it.

**The routes** mirror the automatic path. There are exactly six:

| Route | Who | Does |
| --- | --- | --- |
| `GET /api/v1/safety/settings` | any signed-in user | global settings |
| `POST /api/v1/safety/settings` | superuser | change them (e.g. `enable_automatic_rollbacks`) |
| `GET /api/v1/safety/feature-flags/{id}/config` | any signed-in user | the flag's config, or the default |
| `POST /api/v1/safety/feature-flags/{id}/config` | superuser | set thresholds and `rollback_percentage` |
| `GET /api/v1/safety/feature-flags/{id}/check` | any signed-in user | run the check now; never rolls back |
| `POST /api/v1/safety/feature-flags/{id}/rollback?percentage=&reason=` | superuser | a manual rollback |

The manual rollback is recorded with `trigger_type = manual` and the user who ran it. Its
`percentage` defaults to **0**, so a bare call turns the flag off; a value outside 0–100 answers
422. There is no route that lists rollback history: read `safety_rollback_records`.

> **Watch Out!** A rollback to 1–100% touches only the global rollout. A flag that serves users
> **only** through targeting rules (global 0%) is never rolled back by a non-zero target —
> `rollback_change` returns `None`, because the global percentage is not above it — and a
> partial rollback never reduces the users a rule matches. If a flag's risk lives in its rules,
> give it `rollback_percentage: 0`, which turns it off for everyone.

> **Did You Know?** The safety service was rewritten (before the repository was public) because
> the original read error metrics from a table nobody wrote to, so `is_healthy` was always
> `True`. The rewrite chose `error_logs` and `raw_metrics` precisely because they are the tables
> the evaluation path already fills — no new producer needed. The StreamPulse demo's whole
> rollout story exists to keep that path exercised end-to-end.

### Build It

Trigger a rollback without waiting for a real crash: report errors through the public API, run
the check on demand, then roll back by hand and by the scheduler. Use the StreamPulse player flag
after `python demo/streampulse/simulator/rollout_story.py --step 2` has taken it to 25% (its
config rolls back to 5%, critical `error_rate` 0.05), or any active flag whose rollout is above
its `rollback_percentage`.

```bash
FLAG_KEY=streampulse_player_v2
FID=<flag uuid>
# evaluations in the window (the simulator does this; or loop the evaluate call)
for i in $(seq 1 50); do curl -s "$API/feature-flags/evaluate/$FLAG_KEY?user_id=u$i" -H "X-API-Key: $KEY" >/dev/null; done
# crash reports must come from a SERVER-SIDE key to count (Day 10): the seed writes one for the
# demo, scope sdk:ruleset, to the .api_key_server file beside the app. With the ordinary app key
# they are stored as "client" reports and the check shows them only as uncounted_error_count.
SP=demo/streampulse
SERVER_KEY=$(cat $SP/.api_key_server)
for i in $(seq 1 10); do curl -s -X POST $API/tracking/errors -H "X-API-Key: $SERVER_KEY" -H 'Content-Type: application/json' \
  -d "{\"feature_flag_key\":\"$FLAG_KEY\",\"user_id\":\"u$i\",\"error_type\":\"crash\",\"message\":\"NullPointerException in PlayerV2\",\"metadata\":{\"os\":\"android\",\"os_version\":\"12\"}}" >/dev/null; done

# the check is a GET (POST answers 405) and never changes anything
curl -s $API/safety/feature-flags/$FID/check -H "Authorization: Bearer $TOKEN" | python -m json.tool
# with no other traffic: 10 errors / 50 evaluations = 0.2 > 0.05 → is_healthy: false
```

Now roll it back by hand, twice, as a superuser. The second call is the "don't roll back twice"
guard answering:

```bash
curl -s -X POST "$API/safety/feature-flags/$FID/rollback?percentage=5&reason=build-it" -H "Authorization: Bearer $TOKEN" | python -m json.tool
# → success: true, "rolled back from 25% to 5%", details.paused_schedules: [<schedule id>]
curl -s -X POST "$API/safety/feature-flags/$FID/rollback?percentage=5&reason=build-it" -H "Authorization: Bearer $TOKEN" | python -m json.tool
# → success: false, "... is already at 5% (target 5%); no rollback needed"
```

Evaluate as an employee (`context={"employee": true}`) and as an ordinary user in a bucket
between 5 and 24: the employee still gets the flag through the rule, the ordinary user no longer
does. Then try `percentage=0` and evaluate again: every user, employee included, now gets
`reason: inactive`. To undo, turn the flag back on (`POST /feature-flags/$FID/activate`), set the
percentage you want, and resume the schedule with `POST /rollout-schedules/<id>/activate`.

For the automatic path, start the backend with `SAFETY_CHECK_INTERVAL_MINUTES=1`, set
`enable_automatic_rollbacks: true` (`POST /safety/settings`), repeat the errors, and watch the
next `safety` run in `GET /scheduler/health` and the newest row of `safety_rollback_records`
(`trigger_type` is `automatic`, `executed_by_user_id` is empty).

### Key takeaways

- `error_rate = COUNT(error_logs) ÷ Σ raw_metrics.count(flag_evaluation)` over 15 minutes,
  counted by the database; errors come from server evaluation failures _and_
  `POST /tracking/errors`, timed by the server; locally evaluated counts join the denominator.
- Per-flag config: `enabled`, `metrics` thresholds (warning/critical/comparison),
  `rollback_percentage` (0–100); global `enable_automatic_rollbacks`.
- The scheduler checks every ACTIVE flag. `rollback_change` decides: target 0 turns the flag off
  for everyone; target 1–100 lowers the global rollout only; nothing to change means no record
  and no notification.
- A rollback locks the flag, then its schedules, pauses every ACTIVE schedule, and writes a
  `SafetyRollbackRecord`, all in one transaction.
- The check is a GET and never rolls back; settings and config are written with POST; manual
  rollback is superuser-only and defaults to 0%.
- Unmeasurable metrics are reported, not breached. Timestamps in these tables are naive UTC.

### Q&A

**Q. Why 15 minutes?**
Long enough to have evaluations to divide by at a 5% rollout, short enough that a crash spike is
caught within a couple of scheduler ticks. It's a parameter of `get_error_metrics`; the check
uses the default. It is also why StreamPulse waits 16 minutes before widening again (Day 21).

**Q. What stops one bad client from rolling back a flag for everyone?**
Two things. Reports sent with an ordinary (browser or mobile) key are stored but not counted
(`uncounted_error_count`), so only a server-side key, or the platform's own evaluation failures,
can move the error rate. And what does count is a rate over a window, so one client's noise is
diluted by everyone else's evaluations. A crash that affects 20% of evaluations _is_ a reason to
roll back. The remaining mitigations are the threshold (set it relative to your baseline error
rate), a non-zero `rollback_percentage` (which lowers the rollout instead of turning the flag
off), and the SDK rate limit that error reports share.

**Q. How do I resume after a rollback?**
Two separate decisions. If the rollback was to 0%, the flag is INACTIVE: turn it on and set its
percentage. Either way its rollout schedule is PAUSED: resume it with
`POST /rollout-schedules/{id}/activate`. Wait for the 15-minute window to clear first, or the
next tick sees the old errors and rolls it back again.

**Q. Does rollback affect experiments?**
No; safety is a flag feature. An experiment's kill switch is `POST /experiments/{id}/pause`, and
a paused experiment stays paused until someone starts it or a resume they scheduled comes due
(Day 17).

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

| Wish                              | Method                       | Service                                  | Enabled by                                   | Status |
| --------------------------------- | ---------------------------- | ---------------------------------------- | -------------------------------------------- | ------ |
| Look every day without inflating α | Sequential testing (mSPRT)  | `services/sequential_testing_service.py` | `sequential_testing_enabled` + config        | beta   |
| Sharper estimate from noisy data  | CUPED variance reduction     | `sufficient_stats_analysis.py` (`cuped_metric_result`) | `variance_reduction_config`      | numbers GA, route beta |
| "P(B > A)" and expected loss      | Bayesian (Beta-Binomial)     | `services/bayesian_service.py`           | `bayesian_enabled` + `bayesian_config`       |        |
| Shift traffic to the winner       | Multi-armed bandit           | `services/bandit_service.py` + scheduler | `optimization_type != "fixed"`               |        |

"Beta" here is about the _numbers_, not the route: the response carries
`analysis_status` (`ga` or `beta`) and, when it is beta, an `analysis_notice` saying which part is
not computed yet, both read from one table, `backend/app/core/analysis_status.py`. The table is
about the *numbers*, not the route's shape (a route's `x-stability: beta` is a separate label
about whether its request and response may change). Today sequential, interactions and the
holdout comparison are `beta`; CUPED and the warehouse proportion and mean results are `ga`.
Read the notice before you read the number.

The [statistics book](../experimentation-statistics/README.md) derives each; today is the
_shape_ of each in the codebase and how it plugs into the loop.

### How Controlvar does it

**The primary result first**, because every block sits beside it. `GET /results/{id}` counts,
per variant, the distinct users assigned and the **users who converted** (a user with ten
purchase events is one conversion; `count_converting_users` in
`backend/app/services/event_matching.py`), and analyses **every metric as a proportion** with
**Fisher's exact test** on the 2×2 table of converting and non-converting users
(`binomial_metric_result` in `backend/app/services/sufficient_stats_analysis.py`;
`statistical_test_used: "fisher_exact"`). The confidence level (0.80 to 0.99; a request's
`confidence_level` if it sends one, otherwise the experiment's stored value, 0.95 unless set
otherwise) sets both the significance threshold and the interval width. Welch's t-test for
means exists only in the optional warehouse module (below); it is not what `/results` runs.

**Sequential testing (beta).** The peeking problem: if you test at α = 0.05 every day, the
chance of _ever_ seeing p < 0.05 under the null climbs far above 5%. The mixture SPRT produces a
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

        # Agresti-Caffo variance of the difference (#854): positive for any
        # counts with n > 0, and the same V the confidence sequence uses.
        V_n = self.difference_variance(
            control_successes, control_total, treatment_successes, treatment_total
        )

        Z_n = delta / math.sqrt(V_n)

        # mSPRT lambda: mixture likelihood ratio with Gaussian(0, tau^2) prior
        # Lambda_n = sqrt(V_n / (V_n + tau^2)) * exp(tau^2 * Z_n^2 / (2*(V_n + tau^2)))
        #
        # Computed in log space and capped at the largest finite double before
        # exp: an overwhelming difference (|Z| above about 38 once V is much
        # smaller than tau^2) would otherwise not fit in a float. The cap cuts
        # nothing that was finite before, and the capped value is still above
        # 1/alpha for any usable alpha, so it can stop.
        ratio = V_n / (V_n + tau_squared)
        log_lambda = 0.5 * math.log(ratio) + tau_squared * Z_n**2 / (
            2.0 * (V_n + tau_squared)
        )
        lambda_ratio = math.exp(min(log_lambda, _LOG_FLOAT_MAX))

        can_stop = lambda_ratio >= boundary
        always_valid_p = min(1.0, 1.0 / lambda_ratio)
        ...
```

Two details of that function are lessons in numerical honesty. The variance is the
**Agresti-Caffo** one (add one success and one failure to each arm before computing it):
the plug-in `p(1−p)/n` is zero for an arm with no conversions yet, which made the early
interval too narrow and stopped A/A experiments far more often than α at unequal splits; the
adjusted version is positive for any counts, and the confidence sequence uses the same V, so
zero lies outside the interval exactly when `can_stop` is true. And Λ is computed in **log
space** and capped at the largest finite double: with an overwhelming difference the exponential
would not fit in a float, and a response with `Infinity` in it is not JSON.

`GET /results/{id}/sequential` returns Λ, the always-valid p, the confidence sequence, the
evidence trajectory and a recommendation (`stop_for_effect` or `continue`). The same analysis is
now also embedded in the main results response (as its `sequential_testing` block) whenever the
experiment has it enabled, so the dashboard does not need a second call. Three facts about it:

- **α is honoured.** The significance level is the `alpha` query parameter (above 0, at most
  0.2), else the experiment's `sequential_testing_config.alpha`, else 0.05, and the boundary is
  `1/alpha`. A stored value outside (0, 0.2] is not used silently: 0.05 is used and the notice
  says so.
- **The confidence sequence is the inverted mSPRT**, so it narrows as data arrives and agrees
  with the stop decision: when `can_stop` is true the sequence excludes zero.
- **There is no planned-looks table.** `alpha_spending` is always an empty list. The
  O'Brien-Fleming and Pocock boundaries this service once reported did not hold their stated
  level, and nothing counted the looks they were indexed by.

The successes and totals it is fed are the same converting users and assigned users as the
primary result. And nothing acts on `can_stop` by itself: the dashboard's Sequential tab plots
the trajectory against the boundary, and stopping is a person's decision.

**CUPED.** The idea: variance reduction using a pre-experiment covariate X, here "did this user
already do the thing before the experiment?". The outcome Y is whether the user converted (the
same converters `/results` counts); the adjusted estimate is `Y − θ(X − E[X])` for a slope θ, with
the same mean and a smaller variance, so a tighter interval. Since #217 both inputs are real:

- **X is the user's own history.** For each assigned user, did they send the covariate event
  (the event of `covariate_metric_id`, or each metric's own event when none is named) in the
  `covariate_lookback_days` (1 to 90, default 7) *before their assignment*? Two guards keep that
  honest. The window is per user, ending at that user's own assignment time, and an event at the
  moment of assignment is not history. And the event must also have been **received by the server
  before the user was assigned** (`events.updated_at < assigned_at`): an event with an old
  `timestamp` that was uploaded afterwards is history sent after the user was assigned, and it
  is not read for that user. That is why `Event.updated_at` in Day 3 is "received at" and has no
  `onupdate`.
- **The estimate comes from sums.** For each metric, `cuped_metric_result` fits
  `y ~ C(arm) + x` by ordinary least squares from per-arm sums alone (`n`, `Σy`, `Σx`, `Σy²`,
  `Σx²`, `Σxy`), so a caller with those sums from somewhere else, such as a warehouse, gets the
  same numbers:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
def cuped_metric_result(
    arms: Sequence[CupedSums],
    confidence_level: float,
    correction_method: str,
    *,
    metric: Any,
    adjust: bool = True,
) -> List[Dict[str, Any]]:
    """One metric's CUPED comparisons, every treatment against the control.

    The estimate is the regression ``y ~ C(arm) + x`` fitted by ordinary least
    squares, computed from sums alone, so a caller with the sums from
    somewhere else (a warehouse) gets the same numbers:

    * ``theta`` is the pooled within-arm slope ``sum_k Sxy_k / sum_k Sxx_k``
      over **every** arm, the ``x`` coefficient of that regression; 0 when
      ``adjust`` is false or when ``x`` does not vary within any arm (for a
      0/1 covariate, coverage of exactly 0 or 100%);
    * each arm's adjusted mean is ``ybar_k - theta * (xbar_k - xbar)``, centred
      on the grand mean ``xbar`` so it stays on the outcome's scale;
    ...
```

Read what that says about θ: it is the *pooled within-arm* slope over every arm (not "estimated
on control"), and each arm is re-centred on the grand mean of X. The docstring also says two
things worth believing: `variance_reduction_pct` **can be negative** (an arm whose own slope
differs can end up with a larger variance than without adjustment), and adding an arm changes the
other arms' adjusted effects, because they share θ. A comparison that cannot be computed is listed
with an `unavailable_reason` instead of numbers (`fewer_than_2_units`, `no_variation`,
`no_residual_variation`, `not_a_proportion_metric`, `no_control_variant`, and so on).
`variance_reduction_config.method` selects: `none` (θ exactly 0, the unadjusted effect),
`cuped`, `cuped_plus` (computed as `cuped`; each row says what was computed), or `winsorization`
(clipping, for mean metrics). The numbers are labelled `ga` in `analysis_status`; the route
`GET /results/{id}/cuped` itself is still marked `x-stability: beta`, because its response may
still change shape (mean metrics, #439). A covariate that is the same for everyone in every arm
(nobody did the event, or everybody did) gives θ = 0 and an honest "no adjustment" rather than a
manufactured saving.

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


def compute_probability_to_be_best(posteriors, n_samples: int = DEFAULT_N_SAMPLES, seed: Optional[int] = None,
                                   *, samples: Optional[np.ndarray] = None) -> List[float]:
    ...
    # Draw samples from each posterior (shape: [n_variants, n_samples])
    if samples is None:
        samples = _draw_posterior_samples(posteriors, n_samples, seed)

    return _ptbb_from_samples(samples)
```

The results API draws `DEFAULT_N_SAMPLES` = 100,000 per variant with the seed
`derive_seed(experiment_id, UTC day, n_samples)` (Day 6), and echoes `seed` and `n_samples` in
the response, so the block is reproducible; `samples=` lets one draw serve the probability to be
best, the expected loss and the stopping rule. A Bayes factor (Savage–Dickey) and a stopping rule
(`should_stop`) round it out, and the rule is stricter than it used to be: **`STOP_WINNER` needs
the leading variant's probability to be best to reach 0.975** (`PROB_BEST_THRESHOLD`) **and** its
expected loss to be below the configured `loss_threshold`. An expected-loss threshold alone was
crossed by two _identical_ arms once there was modest traffic at a low base rate; under an A/A
test the leader's probability to be best is roughly uniform on [0.5, 1], so 0.975 puts a
single-look false winner near 5%. The value was chosen by a 2,000-simulation A/A run per cell.
`bayesian_results` rides inside the main results response when `bayesian_enabled`, and
`GET /results/{id}/bayesian` returns the same block on its own. The decision is a recommendation:
no scheduler stops an experiment on it (Day 17).

**Bandits.** For `optimization_type ∈ {thompson_sampling, ucb1, epsilon_greedy}` the bandit
scheduler recomputes per-variant weights every `BANDIT_UPDATE_INTERVAL_MINUTES` and stores
them in `BanditState.variant_weights`; `/tracking/assign` routes _new_ users by those weights
(Day 4), and existing assignments are always kept. Thompson Sampling is the Bayesian block used
as a policy:

```python
# 📁 backend/app/services/bandit_service.py
class ThompsonSampling:
    PRIOR_ALPHA: float = 1.0  # uninformative prior
    PRIOR_BETA: float = 1.0
    N_SAMPLES: int = 10_000  # samples used to estimate selection probabilities

    @staticmethod
    def sample(alpha: List[float], beta: List[float], n_samples: int = 10_000, seed: Optional[int] = None) -> List[float]:
        n_variants = len(alpha)
        if n_variants == 1:
            return [1.0]

        # Draw n_samples from each Beta posterior: shape (n_samples, n_variants)
        rng = make_rng(ThompsonSampling.resolve_seed(seed, n_samples, alpha, beta))
        draws = rng.beta(alpha, beta, size=(n_samples, n_variants))

        # For each sample pick the arm with the maximum draw
        winners = np.argmax(draws, axis=1)
        ...
```

The scheduler's input is the interesting engineering. Where do "pulls" and "successes" come
from? PostgreSQL is the complete record — pulls are assignment rows per variant, successes are
**converting users** per variant matched through the primary metric's `event_name`, counted
exactly as `/results` counts them (the Day 5 helper again). DynamoDB real-time counters, the
optional `counters` module, are preferred only while they keep up with it; then the previously
persisted `BanditState`; then zero priors:

```python
# 📁 backend/app/core/bandit_scheduler.py
        dynamo_stats = self._stats_from_dynamodb(experiment_id, variant_ids)
        pg_stats = self._stats_from_postgres(experiment_id, variant_ids, experiment)

        if _has_pulls(dynamo_stats):
            if _at_least_as_complete(dynamo_stats, pg_stats):
                return dynamo_stats  # type: ignore[return-value]
            logger.warning(
                "BanditScheduler: DynamoDB counters for experiment %s have fewer "
                "pulls or successes than PostgreSQL; using PostgreSQL",
                experiment_id,
            )

        if _has_pulls(pg_stats):
            return pg_stats  # type: ignore[return-value]

        stats = self._stats_from_bandit_state(experiment_id, variant_ids)
        if stats is not None:
            return stats

        # No data at all — use zero-count priors
        return {vid: VariantStats(variant_id=vid) for vid in variant_ids}
```

Two bugs shaped this function. For months the first source called a DynamoDB method that didn't
exist, the exception was swallowed, and every refresh fell through to zero priors — bandits that
"ran" but never learned — and nothing started the scheduler until it was wired into the
lifespan. Later, a DynamoDB count that had fallen behind replaced the complete PostgreSQL count;
the `_at_least_as_complete` comparison is the fix. The PostgreSQL source is what makes bandits
work in a core deployment, which has no counters module at all.

> **Watch Out!** The bandit changes _who gets what_ while the experiment runs, which breaks
> the fixed-allocation assumptions of the Day 6 statistics (and SRM will, correctly, warn —
> the observed split is _supposed_ to drift from the configured one). A bandit experiment is
> for _optimising_ during the run, not for a clean causal estimate; read its "recommendation"
> with that in mind.

**What is not there yet.** Two analyses you may meet in the API are deliberately not
available, and say so rather than return made-up numbers:

- **Post-stratification.** `POST /results/{id}/post-stratification` answers **501** for every
  experiment: no per-user stratum data exists to stratify on. (`POST /results/{id}/fdr-correction`,
  Benjamini-Hochberg over a set of p-values, beside it, does work.)
- **Means from your own warehouse.** The optional `warehouse` module (full profile only, every
  route beta, under `/api/v1/warehouse/analysis`) runs an experiment's metrics in your Snowflake,
  BigQuery or Athena account and analyses proportions and means from the per-variant counts and
  sums it returns; that is where Welch's t lives. At the reference commit **no connector is
  enabled**: the connector code ships, but creating a connection answers 422
  `connector_disabled` until each connector has passed a check against a real warehouse account,
  and no operator setting overrides that.

### Build It

Fetch each block for an experiment that has it enabled (ShopLab's `shoplab_hero_banner` has
sequential + Bayesian; `shoplab_plp_sort` is a Thompson bandit), then reproduce one number:

```bash
EXP=<shoplab_hero_banner uuid>
curl -s "$API/results/$EXP/sequential" -H "Authorization: Bearer $TOKEN" > seq.json
python -c "import json; d=json.load(open('seq.json')); print(d['analysis_status'], d['msprt_result']['boundary'], d['alpha_spending'])"
curl -s "$API/results/$EXP/sequential?alpha=0.1" -H "Authorization: Bearer $TOKEN" > seq10.json   # boundary 10
curl -s "$API/results/$EXP/bayesian" -H "Authorization: Bearer $TOKEN" > bayes.json

# reproduce probability-to-be-best from the posteriors, seed and n_samples the API echoed
python - <<'PY'
import json
from backend.app.services.bayesian_service import compute_probability_to_be_best
d = json.load(open("bayes.json"))
posteriors = [{"alpha": v["posterior"]["alpha"], "beta": v["posterior"]["beta"]} for v in d["variant_results"]]
print("api:  ", [v["probability_to_be_best"] for v in d["variant_results"]])
print("local:", compute_probability_to_be_best(posteriors, n_samples=d["n_samples"], seed=d["seed"]))
print("decision:", d["decision"])
PY

BANDIT=<shoplab_plp_sort uuid>
curl -s "$API/bandit/$BANDIT" -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

The two probability lists should match exactly: same posteriors, same seed, same number of
draws. Change `n_samples` to 10,000 and they no longer do, which is why the API echoes it. Then
check the posteriors themselves against the primary result: with the default Beta(1, 1) prior,
each variant's `alpha − 1` is its converting **users**, not its conversion events.

### Key takeaways

- The primary result: converting users ÷ assigned users per variant, Fisher's exact test for
  every metric, at the requested `confidence_level`.
- Four advanced methods, four services, each a block beside the primary result, each
  configured on the experiment row; a `beta` analysis (sequential) says why in
  `analysis_notice`.
- mSPRT: monitor Λ, stop at 1/α with α as requested; `min(1, 1/Λ)` is an always-valid p-value;
  the confidence sequence agrees with the decision; no alpha-spending table.
- CUPED: Y is the user's conversion, X is whether they did the event in their own window before
  assignment (and the server had received it by then); θ is the pooled within-arm OLS slope, from
  per-arm sums; the saving can be negative, and a covariate that does not vary gives θ = 0.
- Bayesian: Beta-Binomial posteriors, Monte Carlo PtBB and expected loss, seeded per
  `(experiment, day, n)`; `STOP_WINNER` needs P(best) ≥ 0.975 and a small expected loss.
- Bandits: PostgreSQL converting users unless DynamoDB counters are at least as complete, then
  `BanditState`, then priors; `/assign` routes new users by the weights.
- Post-stratification answers 501; means and Welch's t are warehouse-module only, and no
  warehouse connector is enabled yet.

### Q&A

**Q. Which should I enable by default?**
Sequential testing, if you'll look at results before the planned end (everyone does). Bayesian
if your stakeholders think in probabilities. Bandits only when the goal is to _exploit_ during
the run, not to _learn_ a clean effect. Turn CUPED on when users usually have history before they
are assigned (a repeat-purchase metric); with a brand-new audience the covariate is zero for
almost everyone, θ is 0, and it changes nothing.

**Q. Does enabling sequential testing change the primary result?**
No. The primary block is still the fixed-horizon Fisher test at the requested confidence level;
the sequential block is additional, with its own `can_stop`. The dashboard shows both so the
trade is visible.

**Q. Why `tau_squared = 0.001` by default?**
It's the variance of the mixing prior on the effect size; smaller values give more power for
small effects at the cost of slower stopping for large ones. It's in
`SequentialTestingConfigInput` and adjustable per experiment.

**Q. How does `epsilon_greedy` fit a seeded design if it's not stochastic?**
`BanditService.is_stochastic` says only Thompson draws random numbers; UCB1 and ε-greedy
weights are deterministic functions of the counts (ε-greedy's exploration share is an even
split, not a coin flip), so they record `NULL` seed provenance.

**Q. My revenue metric shows a conversion rate, not a mean. Is that a bug?**
No, it is the design at the reference commit: `/results` treats every metric as a proportion
of users who converted. Analysing a mean needs per-user sums, which the platform computes only
from a warehouse in the beta warehouse module, and no warehouse connector is enabled yet.

---

## Day 20 — SDKs and the endpoint contract

### What & why

Sixteen SDKs is a lot of code to keep honest (13 of the 16 run against a live backend in CI).
The way Controlvar does it is to make the SDK _small_ — a thin HTTP client with a cache — and
to make the **contract** the thing that's specified, tested and shared. `docs/sdk-guide.md`
states it in one table:

| Purpose                                 | Method and path                                                                      | Response                                                                          |
| --------------------------------------- | ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| Assign a user to an experiment (sticky) | `POST /api/v1/tracking/assign` `{experiment_key, user_id, context?}`                 | `{experiment_key, user_id, variant_id, variant_name, is_control, configuration, assigned, reason}`  |
| Evaluate a flag                         | `GET /api/v1/feature-flags/evaluate/{flag_key}?user_id=…&context=<url-encoded JSON>` | `{key, enabled, config, reason}`                                                  |
| All flags for a user                    | `GET /api/v1/feature-flags/user/{user_id}`                                           | `{flag_key: boolean, …}`                                                          |
| Track one event                         | `POST /api/v1/tracking/track`                                                        | stored event                                                                      |
| Track up to 100 events                  | `POST /api/v1/tracking/batch` `{events: [...]}`                                      | `{success_count, failure_count, errors}`                                          |
| A user's assignments                    | `GET /api/v1/tracking/assignments/{user_id}`                                         | list                                                                              |

`assigned` and `reason` matter more than they look. A user who is not eligible — in the global
holdout, excluded by a mutual-exclusion group, or not matched by the experiment's targeting —
still gets HTTP 200 with the **control** variant, so the app renders the default experience, but
with `assigned: false` and `reason` `holdout`, `mutual_exclusion` or `targeting`; no assignment
row and no exposure are recorded, so the SDK should send no events for that experiment. The
Python SDK's `Assignment` carries both fields (and leaves them `None` against an older server
that does not send them, rather than guessing `False`).

And three rules: **the server decides** (with one opt-in, beta exception for flags, below),
**cache per user + key** (never cache failures), and **key-less events fan out** to every
cached assignment and evaluated flag for that user.

#### Why "the server decides" had to be re-established

Until September 2026 the React SDK bucketed locally, called endpoints that didn't exist, and
sent a user id in a custom header the API ignored — it "worked" in unit tests that mocked
`fetch`. The rewiring replaced every SDK's client with a port of one reference design and
added a **live contract harness** that runs each SDK's `contract_smoke` against a real backend
in CI. The consistent-hash utility survived as an _export_, pinned across languages by golden
vectors, but no SDK uses it to decide anything.

### How Controlvar does it

The reference client is the Python one. Its request core is small enough to read whole:

```python
# 📁 sdk/python/controlvar/client.py
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
            logger.debug("429 from %s %s; retrying once after %.1fs", method, path, wait)
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

The cross-SDK hash, pinned by `tests/sdk-contract/golden-vectors.json`, is the
first-four-bytes little-endian function the server assigns experiment variants with
(`bucket_of(user_id, experiment.key)` in `backend/app/core/consistent_hash.py`, Day 4). It is
**not** the flag rollout function of Day 15, which reads the whole digest `% 100`. Every SDK
exports it; none uses it to decide anything:

```python
# 📁 sdk/python/controlvar/hashing.py
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
bucket. Its doc comment now also says, in so many words, that this is not the server's flag
bucketing — `int(md5(...), 16) % 100`, named `md5-mod100-v1` — which only local evaluation
reproduces.)

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
├── golden-vectors.json        # {user_id, flag_key} → expected hash and rollout inclusions
├── hash_contract.py           # runs each SDK's OWN exported hash against the vectors  (PR gate: "SDK Contract Tests")
├── harness/                   # one thin harness per SDK: python, js, edge, react-native, go
├── test_hash_contract.py      # the comparison's own tests
├── ruleset-vectors.json       # generated corpus for local evaluation (below)
└── live/run_live_contract.py  # every SDK's contract_smoke vs a running API  (PR gate: "SDK Live Contract")
```

`hash_contract.py` calls each SDK's real exported function through its harness — not a copy of
the algorithm — and does every comparison itself; `--list` names every other `sdk/` directory
and why it is not run there (iOS and Android run their own hash tests in their unit jobs).

The live runner does **not** start anything: it needs a backend already running and seeded
with `python backend/scripts/seed_sdk_contract.py` (the `sdk_contract_ab` experiment, the
`sdk_contract_flag` flag, and two API keys, one with the `sdk:ruleset` scope). It first checks
`GET /api/v1/sdk/ruleset` once (403 for the plain key, the ruleset for the scoped key, 304 when
the ETag is sent back), then runs each SDK's smoke (Python, JS, Go, Java, Swift, Ruby, PHP,
.NET, edge, OpenFeature…) and checks the JSON it prints: a sticky assignment, `enabled: true`,
one keyed event, one key-less event that fanned out. Last, for the SDKs with a local mode, it
runs `local_eval_smoke` and reads the database to confirm the safety denominator grew by exactly
the evaluations made locally. Toolchains missing on the runner are `SKIP` unless `--strict`.
The status table in `docs/sdk-guide.md` records which SDKs have actually _run_ against a
backend: three (iOS, Flutter, Elixir) are marked "not yet" there, with only their unit tests
running nightly; the guide says so rather than rounding up.

#### When the SDK decides: local evaluation (beta)

"The server decides" has one opt-in exception, for **flags** and for **server-side** code only.
The JavaScript SDK (`evaluation: 'local'`) and the Python SDK (`evaluation="local"`) can download
the flag ruleset from `GET /api/v1/sdk/ruleset`, refresh it in the background (ETag, 304 when
unchanged), and answer flags in-process with the server's own bucketing (`md5-mod100-v1`, the
Day 15 function), with no request per evaluation. Four rules keep it honest:

- **The key is special.** Downloading the ruleset needs an API key with the `sdk:ruleset`
  scope, which only a user who can change flags (ADMIN or DEVELOPER) can create, and which works
  only while its owner still can. The ruleset holds every flag's rules and the values in them,
  so the JavaScript SDK refuses local mode in a browser.
- **When unsure, ask.** A local answer must be exactly what `GET /feature-flags/evaluate/{key}`
  would say for that ruleset version. When the SDK cannot be sure — an operator outside the
  local list, the legacy rule shape, a number or string outside the local domain — it makes the
  same request it makes in server mode, and the result says `source: server`.
- **The promise is tested, not asserted.** `tests/sdk-contract/ruleset-vectors.json` is
  generated by running the server's own evaluator (2,191 cases); each SDK must answer the 1,294
  marked `must_local` locally with exactly the server's answer and send every other case to the
  server.
- **Safety keeps its denominator.** The SDK reports how many evaluations it made, per flag, to
  `POST /api/v1/tracking/evaluations` (beta, same scoped key), and those counts join the
  `raw_metrics` sum that Day 18 divides errors by.

Experiments are never evaluated locally: assignment is sticky and recorded by the server, so
`get_assignment` is always a request.

> **By the Way.** `backend/lambda/` still holds an event processor and a flag-evaluation
> function, with their own salted hasher in `shared/consistent_hash.py` (MD5, first four bytes,
> though its docstring still says "MurmurHash3-like"). No stack deploys a handler from
> `backend/lambda/`; the assignment Lambda was removed (#606), because it did not apply mutual
> exclusion or the global holdout. The stacks do deploy placeholder or inline functions:
> `DatabaseAccessLambda` in the compute stack in every environment, and in a full checkout
> `AnalyticsLambda` (placeholder) and `ETLTriggerLambda` (a real inline function). So there is
> one server-side implementation of the contract, the FastAPI app, and an SDK that buckets
> differently from it is a bug, not a second deployment.

### Build It

Run the offline vectors and one live smoke:

```bash
# offline: each SDK's own exported hash against the golden vectors
python tests/sdk-contract/hash_contract.py --list
python tests/sdk-contract/hash_contract.py python js edge react-native go   # js/edge: build them first
python -m pytest tests/sdk-contract -q -o addopts=""

# live: start the API (make db && make dev), seed it, then run the Python and JS SDKs strictly
python backend/scripts/seed_sdk_contract.py
python tests/sdk-contract/live/run_live_contract.py --sdk python --sdk js --strict
```

Watch the runner's first lines (the ruleset check: 403, ruleset, 304) and its last ones (the
local-evaluation smoke and the denominator it read back).

Then write the smallest possible client yourself, in a language the repo doesn't have (or in
`bash` with `curl`): assign, evaluate, track keyed, track key-less-with-fan-out. Compare your
fan-out list to what `sdk/python/controlvar/client.py::track` does.

### Key takeaways

- Six endpoints + `X-API-Key`; the server decides; cache per user + key; failures never
  cached; key-less events fan out; an ineligible user gets control with `assigned: false` and a
  `reason`.
- Local evaluation (JS and Python, beta, server-side, `sdk:ruleset` key) reproduces the server's
  `md5-mod100-v1` flag bucketing from `/sdk/ruleset`, asks the server when unsure, and reports
  counts to `/tracking/evaluations`. Experiments are always server-assigned.
- Reference client: Python (`Transport` seam, one 429 retry honouring `Retry-After`,
  raise-vs-swallow API pairs); React adds provider + hooks with control defaults.
- `consistent_hash` = MD5 first 4 bytes LE ÷ 2³², pinned by golden vectors and checked by
  `hash_contract.py` against each SDK's own export; the server's experiment-assignment function,
  and in the SDKs a utility, not a decision.
- The live contract harness runs each SDK's smoke against a real API in the PR gate; the
  status table is honest about which have run.
- No stack deploys the `backend/lambda/` handlers, and the assignment Lambda is gone; the
  FastAPI app is the one server-side implementation.
- The SDKs are versioned on their own and called beta (0.1.x); the JavaScript, Python and Go
  SDKs install from a registry, the rest from a clone of the repository.

### Q&A

**Q. Why not generate all sixteen SDKs from the OpenAPI spec?**
Generated clients expose every endpoint (including admin ones an SDK must never call), don't
encode the fan-out and caching rules, and produce idiomatic code in none of the languages.
Sixteen small hand-written ports of one reference design, checked by one harness, was
judged cheaper to maintain than sixteen generated ones plus wrappers.

**Q. Can I `pip install` or `npm install` them?**
Three of them, yes: `npm install @controlvar/js-sdk`, `pip install controlvar` and
`go get github.com/controlvar/controlvar/sdk/go`, at 0.1.x and called beta, like the OpenFeature
providers that wrap them. (The Python package is `controlvar`; it was `experimently` before the
rename, and the SDK README says to uninstall the old name first.) Every other SDK installs from a
clone of the repository, and `docs/sdk-guide.md` says how. To work on one, `pip install -e
./sdk/python`, or build `sdk/js` with `npm ci && npm run build`.

**Q. What does the SDK cache, and for how long?**
Successful assignments and flag evaluations, keyed by `(user_id, key)`, for
`cache_ttl_seconds` (300 s default). Attributes are assumed stable per user — change them
and call `clear_cache()`.

**Q. Can I use OpenFeature?**
Yes: `sdk/openfeature` (JS) and `sdk/openfeature-python` are providers over the same client,
and they evaluate through the ordinary `GET /api/v1/feature-flags/evaluate/{key}`. There are no
OpenFeature-specific server routes any more: the evaluate and bulk-evaluate routes were removed
in 0.11.0, and `GET /api/v1/openfeature/flags` and `GET /api/v1/edge/bootstrap` were removed in
the release after 0.17.0 (they answer 404; the edge SDK, like the others, calls the evaluate and
`/feature-flags/user/{user_id}` routes). Same
contract underneath.

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
  3  Incident           run traffic.py --incident android12 for 90 s, then the safety check;
                        while the check is still healthy, run it again (at most 5 rounds)
  4  Rollback           wait for the safety scheduler to roll back to 5% (or roll back by hand
                        when automatic rollbacks are off); the rollback pauses the schedule
  5  Fix shipped        add ``app_version semver_gte 3.2.1 → 100%`` next to the employee rule
  6  50%                resume the paused schedule and advance to stage 3 (after the
                        15-minute error window has cleared)
  7  100%               advance to stage 4, complete the schedule, remove the rules
```

### The trace

**Step 1 — Internal + 5%.** The seed created flag `streampulse_player_v2` at
`rollout_percentage = 5` with a dashboard-shape rule `employee equals true → 100%`, a
four-stage rollout schedule (5 → 25 → 50 → 100, time-based), and a safety config
(`error_rate` warning at 2%, critical at 5%, `rollback_percentage = 5`). The simulator,
`demo/streampulse/simulator/traffic.py`, is stdlib Python that pretends to be thousands of
devices calling the public API directly with the app's API key: per session it evaluates the
flags (`GET /feature-flags/evaluate/{key}?user_id=…&context=…`) with `context = {os,
os_version, region, tier, employee, app_version}`, assigns the experiments
(`/tracking/assign`), and sends events through `/tracking/batch`. (The StreamPulse web app at
`:3300` is the part that uses the React SDK's `useFeatureFlag`.) For an employee device: the adapter
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
rollout report crashes: `POST /tracking/errors/batch` with the **StreamPulse server key**
(the `.api_key_server` file the seed writes beside the app, scope `sdk:ruleset`, the way a StreamPulse server would
forward its devices' crash reports; Day 10 and Day 18 say why only such a key counts),
`feature_flag_key: streampulse_player_v2`, `error_type: "crash"`. Each becomes an `ErrorLog`
row on the flag, stored with `source: "server"`. Meanwhile evaluations continue, so
`raw_metrics` keeps counting. The steady traffic that `setup-local.sh` runs beside the incident
dilutes the crash share, so the story's step 3 runs the incident again, up to five rounds,
until the check turns critical.

**Step 4 — Rollback.** The safety scheduler ticks (every minute in the demo). Under the
`safety` advisory lock (Day 17): for every ACTIVE flag, load the config (enabled),
`check_feature_flag_safety` → `get_error_metrics` counts the last 15 minutes' `ErrorLog` rows
and divides by Σ `raw_metrics.count` → say 0.12 > 0.05 critical → `is_healthy: false`
(Day 18). Global `enable_automatic_rollbacks` is true; the target is
`_rollback_target_percentage(config) = 5`; `rollback_change(flag, 5)` is `"lower"` because the
flag is at 25 > 5. So `execute_rollback` locks the flag, sets `rollout_percentage = 5` (the flag
stays ACTIVE, and the employee rule keeps its 100%), locks and **pauses** the rollout schedule,
writes a `SafetyRollbackRecord` (`trigger_type` `automatic`, reason "Automatic rollback due to
error_rate above threshold (12% > 5%)"), and commits once; then `notify_safety_rollback`
posts to Slack/email. The run record says `success` with `metadata.rollbacks = 1`. Next tick:
the window is still hot, but `rollback_change(flag at 5, 5)` is `None` → no record, no
notification. And the rollout scheduler, which would otherwise move the flag on to the next
stage when it came due, now finds the schedule PAUSED and leaves it alone.

**What the devices see.** The simulator evaluates on every session, so its next evaluations
return `enabled: false, reason: rollout` for buckets 5–24 and `true` for 0–4 and (with
`reason: targeting_rule`) for employees. The web app's React SDK sees the same answer once its
cached evaluation expires. Player v2 is back to the internal cohort.

**Steps 5–7** are the recovery: a second rule `app_version semver_gte 3.2.1 → 100%` (the
adapter's `_SEMVER_OPERATOR_MAP`), a wait for the error window to clear (`HEALTHY_WAIT_S =
16 min`), then **resume the paused schedule** (`POST /rollout-schedules/{id}/activate`) and
advance it to stage 3 (50%) and stage 4 (100%). Advancing without the resume is refused: a
paused schedule's stages cannot be advanced.

### Build It

Run it:

```bash
SAFETY_CHECK_INTERVAL_MINUTES=1 ROLLOUT_CHECK_INTERVAL_MINUTES=1 ./demo/setup-local.sh
python demo/streampulse/simulator/rollout_story.py --step 1
python demo/streampulse/simulator/rollout_story.py --step 2
python demo/streampulse/simulator/rollout_story.py --step 3     # 90 s of crashes, then a check
python demo/streampulse/simulator/rollout_story.py --step 4     # waits for the scheduler
```

While step 4 waits, watch three places at once: the flag's page in the dashboard (rollout
5%, schedule paused), the Admin → Scheduler page (the `safety` runs), and
`select trigger_type, trigger_reason, previous_percentage, target_percentage from
experimentation.safety_rollback_records order by created_at desc limit 1`. Then run the
StreamPulse app at `:3300`, pick an Android 12 device with a bucket in 5–24, and see Player v1;
pick the "Internal tester" preset and still see Player v2. Run `--step 6` and watch the story
resume the schedule before it advances.

### Key takeaways

- Rule (Day 16) → percentage (Day 15) → evaluation writes metrics (Day 15) → client errors
  (Day 18) → scheduler tick under lock (Day 17) → threshold → rollback to
  `rollback_percentage` (global only; rules keep theirs) → schedule paused → notification →
  next evaluation (Day 20) → a person resumes the schedule.
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
evaluates it, if it is already stored, with the old six operators (any other operator does not
match). A save can no longer store it: `validate_flag_targeting` refuses a list with 422.

**3. Two API replicas each run the safety scheduler. How many rollbacks happen for one
unhealthy flag in one tick window?**
One. Both call `run_locked_tick("safety", …)`; `pg_try_advisory_lock` succeeds on one, the
other records a `skipped` tick. The advisory lock is what guarantees "one". The row lock
`execute_rollback` takes on the flag (`FOR UPDATE`) serialises two writers, but it is not, by
itself, that guarantee.

**4. What two tables does `error_rate` divide, over what window, and where do the numerator's
rows come from?**
`COUNT(error_logs)` ÷ Σ `raw_metrics.count` where `metric_type = flag_evaluation`, over the
last 15 minutes. Numerator rows come from ordinary evaluation exceptions (server; an unevaluable
pattern writes none) and `POST /tracking/errors[/batch]` (clients, timed by the server on
receipt). Locally evaluated counts from `POST /tracking/evaluations` join the denominator.

**5. Why is the mSPRT p-value "always valid"?**
Because Λ is a test martingale: the probability that it _ever_ exceeds 1/α under the null is
at most α, so stopping whenever `min(1, 1/Λ) < α` controls the false-positive rate regardless
of how often you look.

**6. The bandit scheduler reads stats from four sources. Name them in order of preference, and
say which one makes bandits work in a core deployment.**
DynamoDB counters (the optional `counters` module), but only when they are at least as complete
as PostgreSQL for every variant → PostgreSQL (assignment rows + converting users via the primary
metric's `event_name`) → persisted `BanditState` → zero priors. The PostgreSQL source: a core
deployment has no counters module.

**7. A flag sits at 0% globally, with a rule `employee equals true → 100%`. Its error rate
breaches the critical threshold, automatic rollbacks are on, and its `rollback_percentage` is 5.
What does the next safety tick do? What if `rollback_percentage` were 0?**
With 5: nothing. `rollback_change` returns `None` because the global percentage (0) is not above
the target (5), so no rollback, no record, no notification, and employees keep the flag. With
0: `rollback_change` returns `"deactivate"`; the flag becomes INACTIVE at 0%, every user
(employees included) gets `reason: inactive`, and its active rollout schedules are paused.

### Exercises

**E3.1 — Bucket arithmetic.** For a flag rolled out 5 → 25 → 5 (rollback) → 50, list which
buckets had the feature at each stage, and name the property that guarantees no user was
"flickered" on–off–on during the 5 → 25 → 5 sequence except those in 5–24. What has to happen
between the rollback and the 50% stage, and why can't the scheduler do it?

**E3.2 — Context aliasing.** Given the SDK context `{"country": "DE", "device": {"os":
"android"}, "user.tier": "premium"}`, list every key `expand_context` produces. Which of
`user.country`, `os`, `device.os`, `tier`, `app.tier` resolve, and to what?

**E3.3 — Lock semantics.** Explain what `async_scheduler_lock` yields when (a) another
session holds the lock, (b) the database is unreachable, (c) the tick raises. For (c), what is
recorded and what is propagated?

**E3.4 — Threshold design.** A flag has a baseline error rate of 0.8% and 400 evaluations
per 15 minutes at 5%. Propose warning and critical thresholds and a `rollback_percentage`,
and explain what would happen at 100% rollout with the same thresholds. When would you choose
`rollback_percentage: 0` instead?

**E3.5 — Contract port.** Sketch (in pseudocode or a language of your choice) `track(user,
event, {experimentKey?, flagKey?})` for a new SDK, including the fan-out rule and the
"never raises" guarantee.

### Solutions

**S3.1** — 5%: buckets 0–4. 25%: 0–24. Rollback to 5%: 0–4 (buckets 5–24 lose it). 50%:
0–49. Users in 0–4 had the feature throughout; 5–24 went on–off–on, which is the intended
effect of a rollback; 25–49 saw it only at the end. The guarantee is that inclusion is
`bucket < percentage` with a fixed per-user bucket — monotone in the percentage — so the
_only_ way a user loses the feature is a percentage decrease, and the set that loses it is
exactly the highest buckets. Between the rollback and 50%, a person must resume the
schedule (`POST /rollout-schedules/{id}/activate`), ideally after the 15-minute error window has
cleared: the rollback paused it, and the rollout scheduler never advances a paused schedule.

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
`False` too, with a warning: "could not connect (database unavailable)" when no connection can
be opened, or "could not acquire (database unavailable)" when the connection opened but the
lock query failed; same skip, and the event loop never blocks, because both happen in a worker
thread. (c) The lock is released by the context manager's `finally`; the run is recorded as
`failed` with the exception's message in `scheduler_runs`;
`scheduler_ticks_total{status="failed"}` is incremented; then the exception is **re-raised** so
the scheduler's own loop logs it and sleeps 60 s before the next attempt. Only
`CancelledError` (shutdown) is propagated without recording. (An exception inside one
experiment or schedule never gets this far: its savepoint is rolled back, it is counted in
`items_failed`, and the run is `partial`.)

**S3.4** — Warning at 2% (≈2.5× baseline), critical at 4% (5× baseline) with
`comparison_type = greater_than`; `rollback_percentage = 5` (keep the internal cohort). At
5% and 400 evaluations, 4% is 16 errors in 15 minutes — a real signal, not noise. At 100%
the denominator is ~8,000 per window, so the same 4% is 320 errors; the _rate_ threshold
scales correctly, which is the point of dividing by evaluations. A _count_ threshold would
have to be re-tuned at every stage. Choose `rollback_percentage: 0` when the risk lives in the
flag's targeting rules or the failure must stop for everyone: a non-zero target lowers only the
global rollout, so users matched by a rule keep the flag, and a flag at 0% globally is never
rolled back at all; 0 turns the flag off for every user.

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
`{success_count, failure_count}` from batch responses to the caller's logger. (The "nothing to
attach" branch is the client's choice, not the server's requirement: `/tracking/track` now stores
a key-less event as history, tagged with no experiment or flag, so it counts in no results. The
reference client sends nothing rather than fill the event log with events that can never be
analysed.)

---

**Next:** [Week 4 — Production Engineering →](./week-4-production.md)

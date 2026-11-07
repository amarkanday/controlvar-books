# Chapter 3 — Where decisions live

**Outcome:** for the questions the product answers most often, you know the one function that answers
each, and why a change goes there and nowhere else.

## One question, one function

An experimentation platform answers a few questions all day long. Which variant does this user get? Is
this event a conversion? May this person change this flag? Each answer feeds a number someone will make
a decision on, so two parts of the code must never answer the same question differently.

Many of the bugs that shaped Controlvar were exactly that: two places answering one question, each
correctly by its own lights. The longest story is told at the top of `backend/app/core/consistent_hash.py`.
There were four functions that put users in buckets, and nothing compared them. For one user and one
key, the SDK contract put the user in bucket 69 and the server's assignment put them in bucket 79. The
result was a user counted in one variant by the API and another by an SDK, "silently, with no error
anywhere".

So the codebase keeps one function per decision. Here are the ones you are most likely to touch:

| The question | The one place | Where |
|---|---|---|
| May this user change this flag? | `can_act_on_feature_flag` | `backend/app/core/permissions.py` |
| Which variant does this user get, and are they eligible at all? | `AssignmentService.assign_user` | `backend/app/services/assignment_service.py` |
| Which bucket is this user in for an experiment? | `hash_user`, `bucket_of` | `backend/app/core/consistent_hash.py` |
| Is this event a conversion for this metric? | `conversion_event_filter`, `count_converting_users` | `backend/app/services/event_matching.py` |
| Do these targeting rules match this user? | `normalise_targeting_rules`, `expand_context` | `backend/app/core/targeting_adapter.py` |
| Is this flag on for this user, and why? | `FeatureFlagService.evaluate_flag_detailed` | `backend/app/services/feature_flag_service.py` |
| What do the results say? | `binomial_metric_result`, `per_user_mean_metric_result` | `backend/app/services/sufficient_stats_analysis.py` |

Three of them, up close.

## Is this event a conversion?

Producers disagree on an event's type: the tracking API and every SDK write the event's own name
(`"purchase"`), while `EventService.track_conversion` writes `"conversion"`. So a metric is matched on its
event name, and only exposure rows are left out:

```python
# 📁 backend/app/services/event_matching.py
def conversion_event_filter(event_name: Optional[str]):
    """
    SQLAlchemy criterion selecting the conversion events of one metric.

    With a metric ``event_name`` the criterion is
    ``Event.event_name == event_name AND event_type not an exposure``.
    Without one (experiment has no metric rows) it falls back to the legacy
    ``event_type == "conversion"`` convention.
    """
    if not event_name:
        return Event.event_type == EventType.CONVERSION.value
    return and_(
        Event.event_name == event_name,
        Event.event_type.notin_(EXPOSURE_EVENT_TYPES),
    )
```

The module's docstring states the rule: every analysis path (the results engine, the streaming results,
the bandit scheduler, the segment breakdowns) must use these helpers, "so the same event is counted the
same way everywhere". A new analysis that writes its own `event_type == "conversion"` filter is a bug,
even if its tests pass.

## Which bucket is this user in?

The experiment-assignment hash is five steps, and every SDK implements the same five:

```python
# 📁 backend/app/core/consistent_hash.py
def hash_user(user_id: str, key: str) -> float:
    ...
    digest = hashlib.md5(
        f"{user_id}:{key}".encode("utf-8"), usedforsecurity=False
    ).digest()
    (uint32,) = struct.unpack_from("<I", digest[:4])
    return uint32 / HASH_DIVISOR
```

Join `"{user_id}:{key}"`, encode it as UTF-8, take the MD5 digest, read its first four bytes as a
little-endian unsigned integer, divide by 2^32. The docstring says which step is the trap: "Step 4 is
the one that is easy to get wrong and impossible to notice: every other reading of the digest produces
a valid-looking number in the right range." `tests/sdk-contract/golden-vectors.json` pins the answers,
and Chapter 7 shows the test that fails when step 4 changes.

> **Watch Out!** This is not the only hash in the platform. Flag rollouts use their own reading of the
> digest, and the holdout, the mutual-exclusion groups and the bandit each have theirs. The same module
> lists them all, and says why none is changed in passing: "Changing any of these re-buckets users."

## May this user change this flag?

Access to a flag is by role, not by ownership. The creator is recorded as `owner_id`, and shown, but
owning a flag gives no extra power:

```python
# 📁 backend/app/core/permissions.py
    if getattr(user, "is_superuser", False):
        return True
    ...
    if not has_permission(role, ResourceType.FEATURE_FLAG, action):
        return _pair(ResourceType.FEATURE_FLAG, action) in granted_permissions(user)
    table = FEATURE_FLAG_SCOPE if scope_table is None else scope_table
    scope = table.get(role, {}).get(action, Scope.OWN)
    if scope is Scope.ANY:
        return True
    return owner_id is not None and str(owner_id) == str(getattr(user, "id", None))
```

A superuser may always act. If the user's built-in role holds the action, the scope table decides whose
flags it covers, and today every entry there is "any flag". If the role does not hold it, only a custom
role or a direct grant (the `rbac` module, full profile) can admit the user. Every route that changes a
flag calls this function, and `backend/tests/smoke/test_flag_change_routes_guarded.py` pins the exact
set of those routes: a new route that changes flags fails that test until someone says what guards it.

## How to change a decision

1. **Change it in its one place.** If you find yourself writing a second version beside the first ("just
   for this endpoint"), stop. That is how the hash story started.
2. **Find every caller.** `git grep` the function's name. Each caller now gets your change, whether its
   author expected it or not.
3. **Find the test that pins the decision**, and expect it to fail. A pinned decision that does not fail
   when you change it was not pinned.
4. **If you find a second place answering the same question**, that is worth an issue of its own.
   `CLAUDE.md` says it about the shapes targeting rules are stored in: "do not add a fourth format".

## Try it

1. Compute a bucket by hand. This needs only Python, no virtualenv:

   ```bash
   python3 -c "from backend.app.core.consistent_hash import hash_user, bucket_of; print(hash_user('user-123', 'my-flag'), bucket_of('user-123', 'my-flag'))"
   python3 -c "import hashlib; print(int(hashlib.md5(b'user-123:my-flag').hexdigest(), 16) % 100)"
   ```

   The first prints `0.6927449859213084 69`. The second, the whole digest read modulo 100, prints `79`:
   the same input and the same MD5, a different bucket.

2. Ask the permission function about each role, with the virtualenv active:

   ```bash
   python -c "
   from types import SimpleNamespace
   from backend.app.core.permissions import Action, can_act_on_feature_flag
   from backend.app.models.user import UserRole
   for role in UserRole:
       user = SimpleNamespace(is_superuser=False, role=role, id='u-1')
       print(role.value, can_act_on_feature_flag(user, 'u-1', Action.UPDATE))
   "
   ```

   It prints `admin True`, `analyst False`, `developer True`, `viewer False`. Each user owns the flag
   (`'u-1'` is both the owner and the user), and the analyst and the viewer still may not change it.

3. List the callers of the permission decision, then read the docstring of the test that pins them:

   ```bash
   git grep -n "can_act_on_feature_flag(" -- backend/app
   sed -n 1,12p backend/tests/smoke/test_flag_change_routes_guarded.py
   ```

4. Check the conversion rule holds:

   ```bash
   git grep -n 'event_type == "conversion"' -- backend/app
   ```

   One hit, and it is the sentence in `event_matching.py`'s own docstring that describes the fallback.

## Go deeper

- [Build a Platform, Day 4](../build-a-platform-in-28-days/week-1-core-loop.md): assignment,
  deterministic hashing and sticky rows.
- [Build a Platform, Day 5](../build-a-platform-in-28-days/week-1-core-loop.md): event tracking and
  conversion matching.
- [Build a Platform, Day 11](../build-a-platform-in-28-days/week-2-api-and-data.md): access by role, and
  the permission dependencies.
- [Build a Platform, Days 15 and 16](../build-a-platform-in-28-days/week-3-hard-parts.md): flag
  evaluation, the rules engine and the targeting adapter.
- [SDK Engineering, Hour 2](../sdk-engineering/part-1-the-contract.md): the consistent hash and its
  golden vectors.
- [SDK Engineering, Hour 9](../sdk-engineering/part-3-sixteen-sdks.md): the case study of the four hashes
  and how the drift was closed.
- [Learn FastAPI, Hour 12](../learn-fastapi-in-24-hours/part-3-dependencies-and-auth.md): one decision
  per resource, by role.

---

_Previous: [Chapter 2 — Run it](./chapter-2-run-it.md). Next: [Chapter 4 — Tests and
gates](./chapter-4-tests-and-gates.md)._

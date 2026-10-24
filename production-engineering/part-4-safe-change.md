# Part IV — Safe Change

### Hours 10–12

[← Part 3](./part-3-security-and-privacy.md) · [Contents](./README.md)

The last Part is about the thing every earlier Part was preparing for: changing a running system without
hurting the people using it. Hour 10 follows a feature flag from creation to removal and takes apart the
most dangerous control in the product, the one everybody believes is a kill switch. Hour 11 takes a real
change that Controlvar shipped, an owner's name on the experiment page, and walks it through every layer it
touched, including the layer where it went quietly wrong. Hour 12 closes the loop: what a reviewer should
demand as evidence for a change like that, how to record what you could *not* check, and who owns the
cleanup.

As before, keep the app clone at v0.36.4 open. The Try It Yourself blocks run real pieces of the backend
with the safety variables from Part I and need no database.

---

<a id="hour-10--feature-flag-lifecycle-create-operate-and-retire"></a>

## Hour 10 — The feature-flag lifecycle: the kill switch that was not

**Outcome:** ship a flag with an owner, a tested failure behaviour and a removal path, and state exactly
what each control (global percentage, targeting rules, server rollback, client refresh) does and does not
turn off.

### What & why

#### Classify the flag before you build it

A **release flag** exposes new code gradually. An **experiment flag** supports a measured comparison. An
**operational kill switch** protects a risky integration. A **configuration flag** may live for years.
They differ in default, owner, evidence and expected lifetime, and mixing them is how a temporary release
flag becomes a permanent undocumented dependency. Write down which one you are creating, who owns it, the
safe fallback, which services consume it, the payload shape, and the date you will remove it or review it.

At v0.36.4 Controlvar records a key, name, description, status (`INACTIVE`, `ACTIVE` or `ARCHIVED`), an
`owner_id`, targeting rules, a rollout percentage, a `default_value` (always `false`) and tags. It has
**no expiry or review-date field, no stale-flag detection and no code-reference discovery**. The
metadata that makes a flag retire is *yours to keep*, in your ticket tracker or your repository. Name keys
for a capability, not a ticket; never reuse a retired key for a different meaning while old clients may
still ask for it; and never flip the sense of a boolean from "new checkout" to "disable checkout" in place.

#### A story: the kill switch that was not

Picture an incident (this scenario is illustrative): an on-call engineer sees errors spiking on a new
checkout flag and sets its rollout percentage to 0. The errors continue. Why? Because the flag also has a targeting rule that enables all paid
accounts, and **a user matched by a targeting rule is decided by that rule's own percentage**, not the
global one. Setting the global percentage to zero removed the free-tier users and left every paying
customer, the highest-value ones, on the broken path.

Controlvar's safety rollback is built around exactly this trap (issues #629 and #729 settled its contract).
The rollback code, the monitor and the "should we
roll back" decision now share one rule, `rollback_change`, whose docstring says why: a target of 0 means
*turn the flag off*, "whatever the global percentage, so a flag that serves only users matched by a
targeting rule (global 0%) can still be rolled back". A rollback to a non-zero target lowers only the
global percentage. Both pause the flag's active rollout schedules so a stage cannot raise it again.
Knowing which of these two actions you want *before* the incident is the whole lesson.

> **Watch Out!** "Set rollout to 0%" is not a kill switch if the flag has targeting rules. The real off
> switch is the flag's **status**: an inactive flag evaluates to `enabled: false` with
> `reason: "inactive"` for everyone, targeting-rule matches included. Document the status change in the
> runbook, and rehearse it.

### How Controlvar does it

#### Evaluation order, in code

```python
# 📁 backend/app/services/feature_flag_service.py
if flag.status not in (
    FeatureFlagStatus.ACTIVE,
    FeatureFlagStatus.ACTIVE.value,
):
    result = False
    reason = REASON_INACTIVE
else:
    matched = self._match_targeting_rule(
        flag, user_id, context or {}, memberships
    )
    if matched is not None:
        targeting_rule_id, rule_percentage = matched
        reason = REASON_TARGETING_RULE
        result = self._evaluate_percentage_rollout(
            flag, user_id, rule_percentage
        )
    else:
        # No rules, no match, or an uninterpretable shape: global rollout
        reason = REASON_ROLLOUT
        result = self._evaluate_percentage_rollout(flag, user_id)
```

Read it as a decision list. Not active: off, reason `inactive`. Active: look for a matching targeting rule;
if one matches, its percentage decides (`targeting_rule`). Otherwise the global percentage decides
(`rollout`). A fourth outcome, `error`, exists for rule sets the engine cannot evaluate (an unsafe pattern
or an unreadable time window): the *whole* ruleset is abandoned and the flag is off, with no error-log row
written, so the safety monitor does not count it. A flag that quietly serves nobody because its rules are
broken is a failure mode your tests should include.

The application interprets the result. The SDKs' `isFeatureEnabled` returns `false` when the call itself
fails, so a flag your code cannot reach behaves as off. That is a *safe default*, and only you can decide
whether "off" is safe for your feature; for a kill switch guarding something that must stay on, it is not.

#### The one rule for rollbacks

```python
# 📁 backend/app/services/safety_service.py
def rollback_change(flag: Any, target: int) -> Optional[RollbackChange]:
    """What a rollback to *target* percent would do to *flag*, or ``None``.

    The one rule the rollback itself, the safety monitor and
    ``should_rollback`` share (#629):

    * a flag that is not ACTIVE (INACTIVE or ARCHIVED, enum or string) --
      ``None``: it already serves no one, and an archived flag is never
      written (#631);
    * a target of 0 -- ``"deactivate"``, whatever the global percentage, so a
      flag that serves only users matched by a targeting rule (global 0%) can
      still be rolled back;
    * a target above 0 -- ``"lower"`` when the global percentage is above it,
      otherwise ``None``.
    """
    if flag_status_name(flag.status) != FeatureFlagStatus.ACTIVE.value:
        return None
    if target == 0:
        return "deactivate"
    if (flag.rollout_percentage or 0) > target:
        return "lower"
    return None
```

Note the first branch: a flag that is not `ACTIVE`, including an archived one, gets
`None`: "it already serves no one, and an archived flag is never written". The docstring of
`execute_rollback` states the rest of the contract:

```python
# 📁 backend/app/services/safety_service.py
Roll the flag back and record it, in one transaction (#629).

What changes is ``rollback_change(flag, target_percentage)``:

* ``"deactivate"`` (target 0): the flag turns off --
  ``transition(status, OFF)``, so INACTIVE -- and its global
  percentage goes to 0. Every user then gets ``enabled: false`` with
  ``reason: "inactive"``, users matched by a targeting rule included;
* ``"lower"`` (target 1-100): the global percentage drops to the
  target; users matched by a targeting rule keep their rule's
  percentage;
* ``None``: nothing is written and ``success`` is false, with a
  message naming the reason (archived, already off, already at or
  below the target).

Both changes pause every ACTIVE rollout schedule of the flag, so a
stage cannot raise the percentage again; the ids are in
``details["paused_schedules"]``. The flag row is locked first and
```

#### Safety monitoring is periodic, opt-in and only as good as its data

Read the three facts together. The monitor runs every `SAFETY_CHECK_INTERVAL_MINUTES` (default 5) and looks
at the last 15 minutes. **Automatic rollbacks are off by default**, and a test pins that in every place a
settings row can come from:

```python
# 📁 backend/tests/unit/services/test_automatic_rollbacks_default_off.py
"""Automatic rollbacks stay off until an administrator turns them on (#629).

A safety rollback to 0% now turns a flag off for every user, and the monitor
looks at every ACTIVE flag. So the global switch that lets the monitor act
without a person, ``enable_automatic_rollbacks``, must keep defaulting to
False in each place a settings row can come from: the column default, the
request schema's default, and the row the service creates on first read.
"""
```

And an error only counts if it arrives from the right place: error reports sent with a browser or mobile
key are stored and shown as `uncounted_error_count` but **do not count toward the error rate or a
rollback**; only reports from a server-side key (`sdk:ruleset` with a capable owner) do. Error rate needs a
numerator (reports) and a denominator (evaluations); latency needs its own source; and a metric name the
monitor does not know is accepted and reported healthy. So validate a configured threshold by producing
known observations, not by reading the configuration. Rehearse in a disposable stack: breach a threshold,
confirm the flag's status, the schedule's state, representative users inside and outside each targeting
rule, the rollback record and the notification, and measure how long a local-evaluation SDK takes to
notice.

> **By the Way.** Rollout schedules advance on their own timer (`ROLLOUT_CHECK_INTERVAL_MINUTES`, default
> 15). That is why a rollback pauses them: otherwise the scheduler, doing its job, would raise the
> percentage you just lowered.

#### Archive, delete and the guard at the lowest layer

An archived flag is retired: "it is never served, and nothing turns it back on by accident". Every request
that would turn it on answers 400 with a fixed sentence and leaves it archived; the only way back is
`POST /api/v1/feature-flags/{id}/unarchive`, which makes it inactive, and the dashboard has no archive
action yet. The interesting part is *where* the rule lives:

```python
# 📁 backend/app/models/feature_flag.py
@event.listens_for(FeatureFlag.status, "set", active_history=True)
def _an_archived_flag_is_not_turned_on(target, value, oldvalue, initiator):
    """Refuse ARCHIVED -> ACTIVE for every ORM writer of ``status`` (#631).

    Old and new are compared by name, so the enum and its string value are the
    same status. A previous value that was never loaded or set (a flag being
    constructed, as the seed scripts do) is not a change. ``active_history``
    loads the stored value when the instance has been expired, so a writer that
    assigns after a commit is still seen.

    A Core ``UPDATE`` or raw SQL never reaches this listener.
    """
    if oldvalue is NO_VALUE or oldvalue is NEVER_SET or oldvalue is None:
        return
    if (
        flag_status_name(oldvalue) == FeatureFlagStatus.ARCHIVED.value
        and flag_status_name(value) == FeatureFlagStatus.ACTIVE.value
    ):
        raise ArchivedFlagError()
```

The routes refuse first, through a shared transition function, but a SQLAlchemy listener on the model also
refuses `ARCHIVED -> ACTIVE` for any other code that assigns `status` through the ORM. Read the last line
of its docstring: "A Core `UPDATE` or raw SQL never reaches this listener." Defence in depth names what it
does not cover.

Archive, disable and delete are three different operations. Archive hides a flag from service without
losing its history. Delete is permanent: it removes the flag, its safety configuration, its evaluation
metrics and its error logs, and keeps the *events* tagged with it (with `feature_flag_id` set to null).
Which should you use before a code release has reached every client? Neither. Remove the code first.

#### Retire in the right order

After the observation window, decide: operational control (give it a review cadence and keep testing its
off branch, since an unexercised kill switch fails exactly when needed) or cleanup work. For cleanup,
search backend, frontend, SDK examples, demos, deployment configuration and every maintained client version
for the key. Replace the branch with the chosen behaviour, stop obsolete evaluation and event calls, deploy
the code, **then** archive or delete the flag. Old mobile clients may require the flag to exist far longer
than web code. Preserve the decision and rollout evidence under your retention policy, and do not reuse the
key until your compatibility policy allows it, if ever.

### Try It Yourself

Reproduce the story with the real evaluator and rollback rule. The first part builds a flag at 0% with a
rule that enables paid plans:

```bash
python - <<'EOF'
from types import SimpleNamespace
from unittest.mock import MagicMock
from backend.app.models.feature_flag import FeatureFlagStatus
from backend.app.services.feature_flag_service import FeatureFlagService
from backend.app.services.safety_service import rollback_change

rules = {"logical_operator": "AND", "groups": [{"logical_operator": "AND", "conditions": [
    {"attribute": "plan", "operator": "equals", "value": "paid"}]}]}

def flag(status, pct):
    return SimpleNamespace(id="00000000-0000-0000-0000-000000000001", key="new-checkout",
                           status=status, rollout_percentage=pct, targeting_rules=rules,
                           default_value=False, owner_id=None)

svc = FeatureFlagService(MagicMock())
for status in (FeatureFlagStatus.ACTIVE, FeatureFlagStatus.INACTIVE):
    for ctx in ({"plan": "paid"}, {"plan": "free"}):
        r = svc.evaluate_flag_detailed(flag(status, 0), "user-1", ctx)
        print(status.value, 0, ctx, "->", r["enabled"], r["reason"])

F = lambda status, pct: SimpleNamespace(status=status, rollout_percentage=pct)
for status, pct, target in [("ACTIVE", 50, 0), ("ACTIVE", 50, 10), ("ACTIVE", 5, 10),
                            ("INACTIVE", 50, 0), ("ARCHIVED", 50, 0)]:
    print("rollback", status, pct, "->", target, ":", rollback_change(F(status, pct), target))
EOF
python -m pytest backend/tests/unit/services/test_automatic_rollbacks_default_off.py -p no:cov -q
```

Expected result (pytest adds its per-test lines):

```text
ACTIVE 0 {'plan': 'paid'} -> True targeting_rule
ACTIVE 0 {'plan': 'free'} -> False rollout
INACTIVE 0 {'plan': 'paid'} -> False inactive
INACTIVE 0 {'plan': 'free'} -> False inactive
rollback ACTIVE 50 -> 0 : deactivate
rollback ACTIVE 50 -> 10 : lower
rollback ACTIVE 5 -> 10 : None
rollback INACTIVE 50 -> 0 : None
rollback ARCHIVED 50 -> 0 : None
```

The first two lines are the story: an active flag at 0% still serves paid users. The third and fourth are
the real off switch. A `lower` target changes only the global percentage, and a target above the current
percentage changes nothing (`None`): the monitor will not write a rollback record every cycle.

Now the runbook exercise. A flag has a 20% global rollout and a rule enabling all paid accounts. An
incident responder sets rollout to 0%, but paid accounts still get the feature. What should the runbook
say?

**Answer.** Matching targeting enables users independently of the global percentage. For a full stop,
use the supported inactive/rollback-to-zero action (and know that a non-zero rollback target only lowers
the global percentage). Check application defaults and SDK refresh delay (a local-evaluation SDK learns at
its next successful refresh, 30 seconds by default, and keeps the old ruleset if it cannot reach the API).
Rehearse with both a paid and a non-paid user.

### Key takeaways

- A flag needs an owner, a failure behaviour and a retirement date, and Controlvar keeps none of the last
  two for you.
- Global percentage, targeting rules, status, server rollback and client refresh are separate controls.
  Only an inactive flag is off for everyone.
- Safety rollback is periodic, off by default, and counts only errors from server-side keys.
- Remove the code, then retire the flag. Delete is permanent but keeps the events.

**Review artifact:** a flag record, branch and failure tests, rollback rehearsal evidence, a consumer
inventory and a removal or long-term review date.

---


## Hour 11 — Changing Controlvar safely: the owner's name

**Outcome:** take one behaviour change across API, storage, dashboard, SDKs and profiles without missing a
dependent surface, using a change Controlvar actually shipped as the worked example, then apply the same
map to a hypothetical editable label.

### What & why

#### A change is a list of surfaces

A small feature is rarely small. "Show who owns an experiment" touches a database column, a response
schema, the permission model, a dashboard page, the generated API contract, any typed client that parses
the response, the docs, and the mixed-version window during deploy. The skill is not writing the field; it
is *enumerating what the field touches* and giving each surface an explicit answer, including "this one is
unaffected, because...".

#### Ownership is an attribute, not an access rule

Before designing anything, decide what the new thing *is*. A cosmetic label and a permission-bearing owner
are different contracts. In Controlvar, the creator is recorded as `owner_id` and shown, but access is by
role, never by ownership (Hour 7). So a change that displays the owner must not accidentally imply that
owners have powers.

#### A story: what the page does when the owner is gone

The experiment detail page used to show an owner as a truncated UUID. Issue #921 replaced that with the
owner's display name, which sounds like one line of UI. Two real-world facts made it a design problem.
First, an experiment's creator can be deleted. The `owner_id` foreign key is `ON DELETE SET NULL`, so the
experiment survives with no owner (issue #935 made the response schema accept that: `owner_id` became
nullable instead of failing to serialise). Second, names are personal data: an email must never leak into a
field labelled a name. The implementation answers both, and its comment says how.

> **Did You Know?** The dashboard's test for this case is named for the situation, not the code:
> *shows "No owner" when the creator's account was removed*. Naming a test for the real-world event it
> guards makes it findable by someone who has never seen the code.

### How Controlvar does it

#### Storage: nullable, set null on delete

```python
# 📁 backend/app/models/experiment.py
owner_id = Column(
    UUID(as_uuid=True),
    ForeignKey(f"{get_schema_name()}.users.id", ondelete="SET NULL"),
)
```

The experiment declares its owner as a user foreign key with `ON DELETE SET NULL` (the flag model does the
same). Deleting a user keeps the thing they created and detaches the link. Because the column is nullable and
the response schema allows it, **a reader must tolerate a missing owner**.

#### API: one route, one new field, no cache

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
def _owner_name(db: Session, owner_id: Optional[UUID]) -> Optional[str]:
    """The name ``GET /experiments/{experiment_id}`` gives the owner (#921).

    The owner's full name when they have one, else their username when it
    contains no ``@``, else ``None``; never their email. Nothing refuses a
    username that is an email address, so one shaped like an address is not
    shown. ``None`` too when the experiment has no owner, or the account is
    gone by the time it is looked up. Read on every request, from the
    validated ``owner_id``, and never cached.
    """
    if owner_id is None:
        return None
    owner = db.get(User, owner_id)
    if owner is None:
        return None
    full_name = (owner.full_name or "").strip()
    if full_name:
        return full_name
    username = (owner.username or "").strip()
    if username and "@" not in username:
        return username
    return None
```

Walk the contract written in the docstring: the owner's full name when they have one, else their username
**when it contains no `@`**, else `None`; "never their email". The reason is a data fact stated in the
comment: "nothing refuses a username that is an email address, so one shaped like an address is not shown".
It is also `None` when the experiment has no owner or the account vanished between reading the experiment
and looking the owner up, and it is "read on every request, from the validated `owner_id`, and never
cached". Put those four sentences next to your own design for a field that names a person.

The schema says where the field lives:

```python
# 📁 backend/app/schemas/experiment.py
class ExperimentDetailResponse(ExperimentResponse):
    """The answer of ``GET /api/v1/experiments/{experiment_id}`` (#921).

    ``ExperimentResponse`` plus the owner's display name. Only that read
    carries it: the list and every other experiment route answer with
    ``ExperimentResponse``.
    """

    owner_name: Optional[str] = Field(
        None,
        description=(
            "The display name of the user who created the experiment: their "
            "full name, else their username when it contains no '@', else "
            "null. Never their email. Null when the experiment has no owner. "
            "Looked up on every read."
        ),
    )
```

Only `GET /api/v1/experiments/{experiment_id}` answers with `owner_name`. The list and every other
experiment route answer with the base `ExperimentResponse`. That is a deliberate narrowing: a name lookup
per row on a list endpoint is an N+1 query waiting to happen.

#### Partial updates: omitted is not null

When you add an *editable* field, the hardest bug is in the update path. The experiment update code works on
**presence**, not value:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
fields_sent = experiment_in.model_fields_set
sends_targeting = "targeting_rules" in fields_sent
```

`model_fields_set` is Pydantic's record of which fields the client actually sent, so a request that omits a
field is different from one that sends it as `null`, and "re-sending the stored value is an edit". The
update then builds its data with `model_dump(exclude_unset=True)`. You will see the difference in the Try It
Yourself. A form that submits `owner_label: null` for a field it did not display, against a server that
treats `null` as "clear", silently erases data.

#### Dashboard: keep what you read

The frontend test file documents a cross-layer subtlety:

```tsx
# 📁 frontend/src/tests/pages/experiment-detail.test.tsx
// Only GET /experiments/{id} names the owner. As on the server, the
// lifecycle routes and the edit and targeting saves here answer with an
// experiment that has no `owner_name` key, so the page has to keep the
// name it read.
```

Only the detail route carries `owner_name`; the lifecycle routes (`start`, `pause`, ...) answer with an
experiment that has no such key. A page that replaced its state with each response would lose the owner's
name the moment you pressed Start. The comment ends: "so the page has to keep the name it read". The
backend's narrowing and the frontend's memory are one design, written in two places.

> **Watch Out!** Test the transition, not just the state. The test installs a fake server that answers
> `/start` without `owner_name` and then checks the name is still on the page. A snapshot of the page after
> loading would have passed on the broken code.

#### The contract: a snapshot you review, not regenerate blindly

Controlvar pins its public API with checked-in OpenAPI snapshots:

```makefile
# 📁 Makefile
openapi: ## Regenerate the OpenAPI fixtures: URL guard, core and full snapshots
	$(PY) -m backend.scripts.dump_openapi --profile full frontend/src/tests/fixtures/openapi.json
	$(PY) -m backend.scripts.dump_openapi --profile core --full docs/api/openapi-v1.stable.json
	$(PY) -m backend.scripts.dump_openapi --profile full --full docs/api/openapi-v1.full.json
```

and a smoke test regenerates the live documents in a fresh interpreter and compares them:

```python
# 📁 backend/tests/smoke/test_openapi_snapshot.py
Rules
-----
* A path/method in the snapshot that is **stable** (no ``x-stability``, or
  any value other than ``beta``) must still exist and its operation object --
  parameters, request body, responses, and every component schema those
  reference, transitively -- must be identical.  Any difference fails.
```

Every route is **stable by default**; a route marked `x-stability: beta` may change with a warning
instead. A stable operation's parameters, request body, responses and every component schema they reference
must be identical. So an additive field is a *snapshot change that needs a decision*. The app's stability
page keeps a table of every stable operation that changed deliberately, with the issue and a plain-language
description. The owner's name has a row there: the response schema becomes `ExperimentDetailResponse`, and
"the OpenAPI document changes only in this operation's response schema". Regenerating a snapshot is not a
proof of compatibility; reading its diff is the review.

> **By the Way.** The stability page also records the opposite kind of entry: a change that alters
> behaviour without altering the OpenAPI document (the sample-ratio check turning a recommendation into
> `INCONCLUSIVE`, Hour 1). `x-stability` is a promise about the *shape* of a route; an analysis response's
> `analysis_status` (`ga` or `beta`) is a separate promise about its *numbers*. A stable shape and
> production-ready numbers are different promises.

#### Profiles and consumers

Does the change belong to core or to a module? The core must work with `modules/` absent: a
module import hidden behind a dashboard condition does not make a backend dependency safe, and
`test_core_boundary.py` walks the AST to enforce it. The SDKs: an endpoint no SDK calls needs no mechanical
edit to all sixteen SDKs; a shared assignment or tracking change does need a deliberate compatibility review
across consumers, and the SDK contract fixtures and live harness have different coverage (see the SDK
Engineering book).

Another real example of expand-then-contract is in the flag model: the `default_value` column carries a
`server_default`, and the comment says why it is load-bearing, in one clause: "the previous release's image
inserts rows without naming this column, and the database fills it". An older writer meeting a new
`NOT NULL` column is exactly the mixed-version case from Hour 6.

#### A hypothetical to practise on: an editable label

Now apply the map yourself. Suppose users ask for an optional, human-editable **owner label** (free text,
at most 80 characters) on experiments. It is **hypothetical**; there is no `owner_label` field at v0.36.4.
Decide first what it is (cosmetic, not an authorisation boundary) and who may change it (the same role that
may update an experiment). Then fill the surface map:

| Surface | Questions for the owner-label example |
|---|---|
| Persistence | Nullable column, migration, historical rows, old writers |
| API schemas | Create, update and read shapes; omission versus explicit null |
| Service and routes | Existing permission checks; audit behaviour |
| Dashboard | Create, edit, display, clear; empty state; safe rendering (not HTML) |
| SDKs | Do typed models reject unknown fields? |
| Exports | Included on purpose, or outside the contract? |
| Core and full | Core behaviour with no import of optional modules |
| Docs and examples | How users set, clear and interpret it |
| Deployment | Mixed-version operation and rollback |

### Try It Yourself

Run the real name rule and the real presence rule:

```bash
python - <<'EOF'
from types import SimpleNamespace
from unittest.mock import MagicMock
from backend.app.api.v1.endpoints.experiments import _owner_name
from backend.app.schemas.experiment import ExperimentUpdate

def db_with(user):
    db = MagicMock()
    db.get.return_value = user
    return db

U = lambda full, name: SimpleNamespace(full_name=full, username=name)
print(_owner_name(db_with(U("Jane Doe", "jdoe")), "x"))
print(_owner_name(db_with(U("", "jdoe")), "x"))
print(_owner_name(db_with(U("", "jane@example.com")), "x"))
print(_owner_name(db_with(None), "x"))
print(_owner_name(db_with(U("A", "b")), None))

omitted = ExperimentUpdate(name="n")
explicit_null = ExperimentUpdate(name="n", description=None)
print(sorted(omitted.model_fields_set), sorted(explicit_null.model_fields_set))
EOF
```

Expected result:

```text
Jane Doe
jdoe
None
None
None
['name'] ['description', 'name']
```

The third line is the privacy rule (an email-shaped username is not shown); the fifth is the "no owner" case.
The last line is the whole difference between omitting a field and sending `null`.

Now the review exercise. The label appears correctly on a new experiment. The edit form always submits
`owner_label: null` when the field is not displayed, and a new serializer requires the label on old rows.
Is the feature complete?

**Answer.** No. The hidden-form submission can erase a value unintentionally; old rows can fail to
serialise. Preserve omitted fields, define explicit clearing, tolerate historical nulls, test both client
versions, and review authorisation and response snapshots before claiming compatibility.

### Key takeaways

- Enumerate the surfaces a change touches; each gets an explicit answer, including "unaffected, because...".
- Ownership is an attribute. Access stays by role.
- A nullable foreign key with `SET NULL` means every reader must tolerate "no owner".
- Presence (`model_fields_set`) distinguishes omitted from null; test the transition as well as the state.
- A stable-by-default API contract turns an additive field into a decision recorded in a table.

**Review artifact:** an impact map identifying affected surfaces, unaffected surfaces with a reason, and
areas still needing verification, with the relevant check results attached.

---


## Hour 12 — A release review from evidence to decision: the test that could not fail

**Outcome:** assemble a review packet for a change, with a third status for every check, "unverified", and
a recovery decision for each boundary. The packet is a recommended review procedure, not an automatic gate.

### What & why

#### A claim ties to evidence, and evidence ties to an environment

A reviewer's job is to separate three statements that sound alike: "the code does X", "a test shows X" and
"X was observed where the code runs". The distance between the second and third is where the expensive
failures live. A green local suite is the *weakest* evidence available, because the developer environment
has every package installed, both profiles present and a migrated database. CI, a container and production
have none of those properties.

#### A story: the test that could not fail

You met it in Hour 4: the double-assignment bug that an in-process concurrency test could not reproduce,
because the async handler never interleaves two requests' database work. A test that passes on the broken
code is not evidence; it is decoration. The remedy was a test that *forces* the interleaving with a hook and
asserts an answer that could only come from the stored row. The generalisation is the most useful sentence
in this book: **before you trust a check, break the thing it guards and watch it fail.** You did that with
the flag-cache guard in Hour 5 and the scope inventory in Hour 7.

> **Did You Know?** Controlvar's own checks guard themselves. The scope inventory has a test that fails if
> the route walk is empty; the flag-mutation inventory has a test (`test_app_routes_alone_would_see_nothing`)
> that proves the naive way of reading routes would see none of the routes it is supposed to check. A gate
> is only as strong as the proof that its probe can see.

### How Controlvar does it

#### The evidence statuses

Use three: **verified** (link the result, the environment and the revision), **failed** (fix it, or explain
a deliberate scope decision; never edit the report to green) and **unverified** (a check you could not start,
even if its source looks correct). Controlvar's pull request template asks for exactly this shape:

```text
# 📁 .github/pull_request_template.md
## How it was verified

<!-- The commands you actually ran and what they printed. For example:
     `make test-unit` → 4185 passed
     `npx playwright test --project=journeys` → 5 passed
     manual: logged in, created an experiment, saw it on /experiments -->

- [ ] Tests run locally and green
- [ ] `make lint` clean (`ruff check`, `ruff format --check`, `eslint`, `tsc`)

## Regression test
```

"The commands you actually ran and what they printed" is the sentence to adopt. And for risk:

```text
# 📁 .github/pull_request_template.md
## Risk and rollback

<!-- Anything that touches auth, migrations, the SDK contract or a public API:
     say what breaks if this is wrong and how to undo it. -->

- [ ] No migration, or every migration here is expand-only: it drops or renames
      nothing the previous release's code uses (that waits for a later release)
```

The checkbox that matters is the last: *no migration, or every migration is expand-only: it drops or
renames nothing the previous release's code uses (that waits for a later release)*. That is Hour 6 as a
review question.

> **By the Way.** The same template asks, under "Regression test", for the name of a test that fails on the
> old code for anything labelled `bug`, and says how it fails in one line. A fix without a test that would
> have caught the bug is a fix that comes back.

#### Prove a profile in the tree it ships

Core and full are different trees. The script that proves the core profile does not run against your
checkout; it copies the tracked files into a temporary directory, deletes `modules/` and works there:

```bash
# 📁 scripts/core_build.sh
# The script NEVER touches the checkout it is run from.  It copies the working
# tree (every file `git ls-files` knows about that is not ignored — so no venv,
# no node_modules, no .env, no build output) into a temporary directory and
# works there.  On success the copy is removed; on failure it is kept and its
```

The profile is decided by whether `import modules` succeeds; nothing under `backend/` reads an environment
variable for it. Verify what runs where the thing runs: the core build, the container and the transition
tests exist because "my venv has everything" is true of no deployment. The boundary itself is a test:

```python
# 📁 backend/tests/smoke/test_core_boundary.py
Every test here is a hard gate.  The modules live under ``modules/`` (issue
#89) and nothing under ``backend/`` reaches them: core code goes through the
seam in ``core/hooks.py`` and ``core/optional_modules.py``, and a core test
file that exercises a module's behaviour does so through the API or the
hooks, never by importing from the ``modules`` package (those tests live in
``modules/backend/tests``).  Do not weaken an assertion to make a test pass —
the only legitimate way to green is to cut the crossing.
```

"Do not weaken an assertion to make a test pass; the only legitimate way to green is to cut the crossing."
Copy that sentence into your own gates.

#### Recovery is a decision, not a hope

For each boundary, the review asks a recovery question. For a database change: can the previous application
still serve against the new schema? The transition test for an older image meeting a newer database says
what to expect:

```python
# 📁 backend/tests/integration/database/test_newer_database_rollback.py
"""An older build meeting a database a newer release migrated: a rollback.
...
N cannot resolve N+1's revision, so it must refuse -- exit non-zero, before
alembic touches anything -- and say what happened: *a newer release migrated
this database; run that release or restore the pre-upgrade backup*.  It used to
say "a database built by the full profile being opened by a core build ...
delete those rows from alembic_version", which was the wrong diagnosis and
harmful advice: deleting the row leaves N+1's table in place and unrecorded,
the next N run re-applies migrations over it, and rolling forward to N+1 again
dies on ``DuplicateTable``.
```

and why the old advice was harmful ("delete those rows from alembic_version"): it leaves the newer release's
table in place and unrecorded, so a later roll-forward dies on a duplicate table. For flags: how long until
each client sees the rollback (server state, then SDK refresh). For data: which copies outlive the release
(Hour 9). For a database *restore*, name the data-loss window. Use the established runbooks; do not invent
a universal downgrade command.

#### Separate the operational decision from the statistical one

A flag may safely expose a change that has not shown business benefit. A favourable experiment does not
make an incompatible migration safe. If the feature changes measurement, preserve the unit, the event
definition, the correction and the reproducibility settings in its decision record (Hours 1–2). For a
reversible rollout, name the owner, initial population, observation window, signal sources, the stop
action and how you verify after stopping, including local SDK refresh.

#### Maintenance belongs in the handoff

Name who will remove the temporary flag once supported clients no longer need it, who will exercise the
long-lived controls, and who updates references when behaviour changes. Retain the decision evidence under
your data policy.

| Boundary | Evidence for a change like the owner's name | Recovery question |
|---|---|---|
| Request and permission | Create, update, clear and a denied mutation | Does refusal leave state unchanged? |
| Database and versions | Old rows and old/new writers | Can the previous application still serve? |
| API and consumers | Reviewed OpenAPI diff and affected typed clients | Which clients remain deployed? |
| Flags and cached state | Enabled, disabled and failure-path rehearsal | How long until each client sees a rollback? |
| Profiles | Core and full impact; the import boundary | Does core run with module code absent? |
| Data flow | Minimised label, log and export scope | Which copies outlive the release? |
| Documentation | Updated behaviour, commands and source reference | Can another engineer reproduce the review? |

### Try It Yourself

Do the full tamper cycle on a gate yourself, in a **scratch copy** of the app clone you can throw away.
Pick the flag-cache guard from Hour 5:

```bash
python -m pytest backend/tests/smoke/test_no_flag_cache.py -p no:cov -q
```

It reports `4 passed`. Now plant the defect:

```bash
echo 'KEY = "feature_flag:123"' >> backend/app/core/consistent_hash.py
python -m pytest backend/tests/smoke/test_no_flag_cache.py -p no:cov -q
git checkout backend/app/core/consistent_hash.py
python -m pytest backend/tests/smoke/test_no_flag_cache.py -p no:cov -q
```

The first run after the edit reports `1 failed, 3 passed` and the failure names
`backend/app/core/consistent_hash.py` and the planted line. The `git checkout` removes the defect, and the
last run is back to `4 passed`. (Do this in a throwaway clone; the checkout discards uncommitted changes to that file.) Write one sentence in your own words for what that proves
(the gate fires on the planted defect) and one for what it does not (it says nothing about a cache key
spelled in some way the pattern does not match).

Now write a review outcome. The owner-label patch passes its new serializer test. There is no test for
explicit null, the previous API version has not been exercised against the expanded schema, and the rollout
plan says only "turn off if problems". What is the review outcome?

**Worked solution.** The serializer behaviour is *verified* in its stated environment. Explicit clearing
and mixed-version compatibility are *unverified*, so the patch is not release-ready on this evidence alone.
Add focused create, update, clear, historical-row and mixed-writer checks. Define the rollout owner, signals
(what you will watch and from which data source), the stop action (flag status, not just percentage) and how
you will verify clients after stopping. Record the results. Keep review and deployment approval distinct.

### Key takeaways

- Three statuses, always: verified, failed, unverified. A check you could not start is unverified.
- Evidence belongs to an environment; the weakest is the one that has everything installed.
- Break the thing a gate guards and watch it fail, and say what that does not prove.
- Every boundary has a recovery question, and "turn it off" is an answer only if you can say which control
  and how you will confirm it worked.
- The handoff names the cleanup owner.

**Review artifact:** a filled evidence table, a deployment-and-rollback handoff and a cleanup owner, with
failed and unverified rows stated explicitly.

---

## Part IV Workshop

### Q&A

**Why can 0% rollout leave users enabled?** Targeting rules decide matching users by the rule's own
percentage, not the global one. Only an inactive flag is off for everyone.

**Why review generated OpenAPI changes?** The generation command updates fixtures; it does not decide
whether a change to a stable contract is acceptable.

**Why does a rollback pause rollout schedules?** Otherwise the scheduler, doing its normal job, would raise
the percentage you just lowered.

**Why is `owner_name` only on the detail route?** A per-row lookup on a list is an N+1 query, and the
detail route can afford one.

### Quiz

1. Is a cosmetic owner label an authorisation model?
2. Does an omitted update field mean the same as an explicit `null`?
3. Can a missing check be counted as a passing check?
4. Which action takes an ACTIVE flag at 0% with a paid-plan rule off for everyone?
5. After a rollback to 10%, who is still enabled that a rollback to 0 would have disabled?

**Answers.** (1) No. (2) Not necessarily: omission preserves state under a partial-update contract, while
null may explicitly clear. (3) No; record it as unverified. (4) Making it inactive (the rollback-to-zero
action does exactly that). (5) Users matched by a targeting rule, who keep their rule's percentage.

### Exercises

1. **Runbook.** Write the three-step incident runbook for a misbehaving flag: identify, stop, verify. Name
   the control and the verification for each.
2. **Impact map.** Fill the surface table for the hypothetical owner label with a one-line answer per row,
   marking any row you cannot answer from the code.
3. **Contract review.** Describe what you would put in the pull request for a change that adds a response
   field to a stable operation.
4. **Packet.** Assemble the evidence table for a real change you have made, with at least one row
   "unverified".

### Solutions

1. Identify: check the flag's status, global percentage, targeting rules and the safety check endpoint. Stop:
   make the flag inactive (or roll back to 0%), pausing active rollout schedules. Verify: evaluate for a
   user inside and outside each rule, check the rollback record, and confirm every SDK mode's refresh
   (server mode immediately, local mode at the next successful refresh).
2. Persistence: nullable column plus migration; API: optional on create/update/read; dashboard: edit and
   clear, rendered as text; SDKs: unaffected unless typed models reject unknown fields; exports: decide
   explicitly; core/full: core; deployment: expand-only. Mark "SDKs" and "exports" unknown until you read
   them.
3. State the trigger and the resulting behaviour; list the stable operations whose schema changed and why;
   include the regenerated snapshot diff; state the mixed-version behaviour; give the verification
   commands you ran and their output; list what you did not run.
4. A good packet names the environment for each verified row and treats "I read the source" as
   unverified.

---

[← Part 3](./part-3-security-and-privacy.md) · [Contents](./README.md)

# Part I — Experiment Design and Decisions

### Hours 1–3

[Contents](./README.md) · [Part II →](./part-2-reliable-systems.md)

Most of the bugs that make an experiment lie are not in the statistics. They are in the five minutes
before the statistics: which users count, which events count, what the denominator is, and what you
promised yourself you would do with the answer. This Part is about that work. Hour 1 builds a metric
contract and follows it through the code that computes the numbers. Hour 2 carries one experiment
from hypothesis to removal, including the ending nobody plans for (no winner). Hour 3 applies the same
discipline to a different measuring instrument, an LLM judge, whose failures look like data.

Work through it with a clone of the app checked out at the v0.36.4 tag next to this book and a Python
virtual environment with `backend/requirements.txt` installed (the Try It Yourself blocks run small
pieces of the real backend with no database). Exercises that need the full stack say so and use the
app's Quick Start; none of them is required to follow the argument.

---

<a id="hour-1--measurement-and-experiment-design"></a>

## Hour 1 — Measurement and experiment design: the user who bought three times

**Outcome:** write a metric contract that another engineer can implement without guessing what counts
as a user, an exposure or a conversion, and read the Controlvar code that enforces each line of it.

### What & why

#### Start with the decision, not the metric

"Make checkout better" is not a testable hypothesis. "Ship a clearer checkout summary if it raises the
probability that an eligible shopper completes a purchase, while checkout errors stay below an agreed
threshold" is one. Notice what the second sentence commits you to before any traffic arrives: a
**population** (eligible shoppers), a **treatment** (the summary), a **primary metric** (completing a
purchase), a **guardrail** (errors), and a **decision rule** (ship if, otherwise not). Write those down
first. Everything later in this book is easier when they exist, and nearly every argument about a
result is really an argument about one of them that was never written down.

#### The unit of randomisation is a promise about independence

Choose the **randomisation unit** deliberately. If one shopper can see both variants through two
devices, the experiment is partly comparing devices. If treatment affects a whole account, assigning
account members independently contaminates control. The unit is what the statistics assume is
independent; renaming a column does not make repeated observations independent. Controlvar's assignment
takes an opaque `user_id` string from you. It does not know whether that string is a person, an account
or a device, so the promise is yours to keep.

#### Three different facts: assignment, exposure, conversion

- **Assignment** records the allocation: this user belongs to this variant, permanently.
- **Exposure** records that the experience was actually encountered.
- **Conversion** records that the outcome event happened.

Collapse them and you get a whole family of silent errors: an exposure-based denominator that depends
on how often a page renders, a conversion count that rises because users retry, an "unassigned" user
whose purchase is counted for a variant they never saw. A good metric contract names which fact each
number comes from.

#### A story: the user who bought three times

Controlvar's own source records how this goes wrong. Its tracking API stores the event's *own name* in
`event_type` (`"purchase"`), while an older internal path stored the literal `"conversion"`. Analyses
that filtered on `event_type == "conversion"` found nothing and reported 0% conversions for experiments
that were converting fine. Fixing that was one file on the server, because every analysis path now
shares one matching rule. A second, quieter defect lived in the same file: a user who purchased three
times could contribute three conversions to a numerator whose denominator counted *users*. The fix
(issue #233) defines the numerator as **converting users**, and the code comment promises the
consequence: "the numerator can never exceed the denominator." That sentence is a metric contract
written as a code comment, and it is the shape you are about to write yourself.

> **Did You Know?** A rate above 100% is a free bug detector. If a conversion rate on a results page
> can exceed 100%, numerator and denominator are counting different things (events against users).
> Controlvar's `count_converting_users` exists so that cannot happen: it counts distinct assigned
> users, and an event from a user with no assignment to that variant is dropped.

### How Controlvar does it

#### The matching rule lives in one file

Read the module docstring of `event_matching.py` first. It states which producers disagree, what the
rule is, and why every analysis path must use it:

```python
# 📁 backend/app/services/event_matching.py
Producers disagree on ``Event.event_type``:

* ``EventService.track_conversion`` writes ``event_type="conversion"`` and
  ``event_name=<metric event name>``.
* The public tracking API (``POST /api/v1/tracking/track``/``batch``) and every
  SDK write the event's own name in both columns (``event_type="purchase"``,
  ``event_name="purchase"``) — ``event_type`` is free text there.

A metric is therefore matched on ``event_name`` only; ``event_type`` is used
just to exclude exposure rows.  Every analysis path (results engine, streaming
results, bandit scheduler, segment breakdowns) must use these helpers so the
same event is counted the same way everywhere.

A binary conversion metric counts **converting users**, not conversion events:
a user assigned to a variant who sent one or more matching conversion events
counts once (#233).  ``count_converting_users`` is that one definition, and
every analysis path that reports ``conversions`` next to a ``sample_size`` of
assigned users must use it (or, in raw SQL, ``CONVERTING_USERS_SQL``), so the
numerator can never exceed the denominator.
```

Notice three things. The rule is stated as *why*, not just *what*. The list of consumers ("results
engine, streaming results, bandit scheduler, segment breakdowns") is the blast radius of changing it.
And the issue number is a pointer to the story, not decoration: when you change matching you are
editing something five analyses depend on.

The filter itself is small:

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
```

A metric is matched on `event_name`; `event_type` is used only to **exclude exposure rows**, so an
exposure can never be mistaken for a conversion. With no metric name it falls back to the legacy
`event_type == "conversion"` convention. That fallback is a compatibility decision, and a reminder that
a metric with no `event_name` silently changes meaning.

> **By the Way.** Controlvar was called Experimently before 0.35.0, and you will still meet the old
> name in some identifiers. In `scheduler_lock.py` the advisory-lock namespace is still
> `experimently.scheduler.`; renaming it would change the lock key every running replica agrees on, so
> it is left alone on purpose.

#### Numerator: converting users, with assignment checked in Python

```python
# 📁 backend/app/services/event_matching.py
def count_converting_users(
    db: Session,
    experiment_id: Any,
    variant_id: Any,
    event_name: Optional[str],
) -> int:
    """
    Number of users assigned to ``variant_id`` with at least one conversion.

    This is the numerator of every conversion rate the analysis endpoints
    report: a user who purchased three times counts once, and an event from a
    user with no assignment to that variant does not count, so the result is
    never larger than the variant's assignment count.
    """
    return len(_assigned_converters(db, experiment_id, variant_id, event_name))
```

`count_converting_users` reads the converting users from `events`, then keeps only those with an
assignment to the *same variant* the event is tagged with. Why not a SQL join between `events` and
`assignments`? The helper's docstring answers with a measurement:

```python
# 📁 backend/app/services/event_matching.py
def _assigned_pairs(
    db: Session, experiment_id: Any, user_ids: Iterable[str]
) -> Set[Tuple[str, str]]:
    """
    ``(variant_id, user_id)`` of the assignments these users have in the experiment.

    The conversion counts below never join ``events`` to ``assignments`` in
    SQL.  Without planner statistics -- a freshly seeded database, before
    autovacuum has analysed it -- PostgreSQL can run that join as a nested
    loop over a sequential scan, which took more than 8 s on 30,000 users in
    CI and blocked the live-results stream (#233).  Instead the converting
    users are read from ``events`` alone, and their assignments are looked up
    here by the unique ``(experiment_id, user_id)`` index, a bounded number of
    ids at a time.
    """
```

On a freshly seeded database, before PostgreSQL had gathered planner statistics, the join ran as a
nested loop over a sequential scan: more than eight seconds on 30,000 users. The code reads converters
from `events` alone and then looks assignments up by the unique `(experiment_id, user_id)` index, in
chunks of 10,000 ids. A tidier-looking join would reintroduce a measured failure. Keep that sentence in
mind whenever you "simplify" a query; Hour 6 returns to it.

The unique index is the denominator's anchor. It is declared on the model, so a second assignment row
for one user in one experiment is impossible:

```python
# 📁 backend/app/models/assignment.py
# Composite unique constraint on experiment + user
Index(
    f"{schema_name}_assignment_experiment_user",
    "experiment_id",
    "user_id",
    unique=True,
),
```

#### The denominator is assignments, and the platform tells you when it is wrong

The results denominator is the variant's assignment count. How do you know the assignment counts
themselves are trustworthy? Controlvar runs a **sample-ratio mismatch** (SRM) check: a chi-square
goodness-of-fit test of observed assignment counts against the configured variant weights.

```python
# 📁 backend/app/services/srm_service.py
SRM_P_VALUE_THRESHOLD: float = 0.001

#: The only ``Experiment.optimization_type`` the SRM test applies to.  Every
#: other value (``thompson_sampling``, ``ucb1``, ``epsilon_greedy``) is an
#: adaptive allocation driven by ``BanditState.variant_weights``, for which
#: ``traffic_allocation`` is not the expected split.
SRM_SUPPORTED_OPTIMIZATION_TYPE: str = "fixed"
```

The threshold is p < 0.001, deliberately strict "because dashboards are polled many times". Do not
translate that into "fires once in a thousand refreshes": repeated looks at a growing dataset are
correlated, so the false-alarm arithmetic is not that simple. Do take the design choice seriously: a
check that cries wolf teaches people to ignore it. Two more details are worth your attention. The test
returns `None` for bandit experiments (their split is *supposed* to drift away from the configured
weights), and the file's own docstring admits a known limitation: an allocation edited while the
experiment runs is still tested against the current allocation, because nothing records when it
changed. Passing SRM does not prove exchangeability either; it only says the observed split is
unsurprising.

What does the platform do when the check fails?

```python
# 📁 backend/app/services/srm_service.py
def recommendation_under_srm(
    summary: Mapping[str, Any], srm: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """The summary's recommendation, given the sample-ratio check.

    When ``srm`` is present and its ``warning`` is set, the randomisation is
    broken and no recommendation from the numbers stands, not even
    ``CONTINUE_TESTING`` (more data does not repair it): the answer is
    ``INCONCLUSIVE`` with :data:`SRM_RECOMMENDATION_REASON`.  ``has_winner``
    and ``winning_variant_id`` are left alone; they describe significance.

    A ``None`` check (a bandit, fewer than two variants, no assignments, or
    a check that raised) changes nothing.

    Returns the ``recommendation`` and ``recommendation_reason`` to apply.
    """
    current = {
        "recommendation": summary.get("recommendation"),
        "recommendation_reason": summary.get("recommendation_reason"),
    }
    if srm is None or srm.get("warning") is not True:
        return current
    return {
        "recommendation": SRM_RECOMMENDATION,
        "recommendation_reason": SRM_RECOMMENDATION_REASON.format(
            p_value=float(srm["p_value"])
        ),
    }
```

The recommendation becomes `INCONCLUSIVE`, **including** where it would have been `CONTINUE_TESTING`,
because more data does not repair broken randomisation. `has_winner` is left alone (it describes
significance, not integrity). That separation, "is it significant" against "can we trust the
comparison", is the single most useful habit in this Part.

#### Attribution windows: none, so write one in your plan

Ask the code a question your metric contract probably contains: *within how many days of assignment
must the conversion occur?* Nothing in `event_matching.py` takes a window. A matching conversion counts
whenever it arrives, as long as the user is assigned to that variant. If your business horizon is "seven
days after assignment", that is a **recommended analysis requirement** of your plan, not something a
configuration field enforces. The honest thing is to say so in the contract and, if the window matters,
analyse separately. A label in a plan does not change which rows the server counts.

#### Time: the one place a window does exist

CUPED, the pre-experiment variance reduction, has a lookback window, and its rule about *time* is a
model of careful thinking:

```python
# 📁 backend/app/services/event_matching.py
def covariate_user_ids(
    db: Session,
    assigned_at: Mapping[str, datetime],
    event_name: str,
    lookback_days: int,
) -> Tuple[Set[str], int]:
    """
    The users with a pre-assignment event of ``event_name``, for CUPED (#217).

    A user counts when at least one of their events matching
    ``conversion_event_filter(event_name)``, with any tag or none:

    * happened in ``[assigned_at - lookback_days, assigned_at)``: an event at
      the moment of assignment is not history;
    * and was stored by the server before ``assigned_at``
      (``events.updated_at < assigned_at``): history sent after the user was
      assigned is not read for that user.

    ``assigned_at`` maps each user id to their assignment's ``created_at``.
    Both it and ``events.updated_at`` are naive UTC (``timestamp without time
    zone``, written from ``utcnow``), and they are compared in Python as they
    are, never through an aware value or the database session's time zone.
    Comparing a naive value with an aware one raises ``TypeError``.

    One query per ``COVARIATE_LOOKUP_CHUNK`` users reads ``events`` alone (no
    SQL join to ``assignments``, as ``_assigned_pairs`` explains), bounded by
    the earliest window start and the latest assignment; each user's own
    window is applied here.  The bounds are compared through
    ``UTCTimestampString``, the format the column stores.

    Returns:
        The user ids, and how many matching rows were skipped because their
        stored ``created_at`` could not be read.
    """
...
if assigned - lookback <= happened < assigned and received_at < assigned:
    found.add(user_id)
```

A user's history counts only if the event **happened** in `[assigned_at - lookback, assigned_at)` *and*
the server **stored** it before the assignment (`events.updated_at < assigned_at`). Backdating an event
you submit afterwards does not turn it into a valid pre-treatment covariate. Distinguish event time from
receipt time in your own contracts for the same reason.

> **Watch Out!** Every metric is analysed as a conversion. The app's limitations page is explicit: a
> metric's type is stored, but its events' `value` is not analysed. A "revenue" metric tells you whether
> each user sent the event, not how much they spent. If your decision is about revenue, plan a separate
> analysis of the stored values and say so in the contract.

#### Power: plan the sample before you look

The platform computes a two-proportion sample size with the standard Fleiss formula and exposes it in
the dashboard's power calculator and through the API. The results response also carries a
`sample_size_adequate` flag, computed against the primary metric's `minimum_sample_size` (100 when the
metric sets none):

```python
# 📁 backend/app/services/analysis_service.py
def _sample_size_adequate(
    experiment: Experiment, metrics_results: List[Dict[str, Any]]
) -> bool:
    """True when every variant meets the primary metric's minimum sample size."""
    if not metrics_results:
        return False
    primary = next((m for m in experiment.metric_definitions if m.is_primary), None)
    minimum = int(getattr(primary, "minimum_sample_size", None) or 100)
    raw = next(
        (
            r
            for r in metrics_results
            if primary and r["metric_id"] == str(primary.id)
        ),
        metrics_results[0],
    )
    return all(v["sample_size"] >= minimum for v in raw["variant_results"])
```

Read the flag for what it is: a floor that stops the platform recommending a ship decision from a tiny
sample, not evidence that your experiment is *powered for your effect*. The recommendation code makes the
intent explicit: before the floor is met, a significant winner is reported but the recommendation stays
`CONTINUE_TESTING` ("early peeks inflate false positives"). Choose fixed-horizon or sequential inference
before launch, correct for multiple treatments, and remember that the stored `correction_method` and
`confidence_level` are fixed once the experiment leaves draft (Hour 2 shows the code that does it).

### Try It Yourself

Nothing here needs a database. From your app clone, with the virtual environment active and the safety
variables in place (so nothing reaches a real service):

```bash
export POSTGRES_SERVER=127.0.0.1 POSTGRES_PORT=1 REDIS_HOST=127.0.0.1 REDIS_PORT=1 \
  REDIS_URL=redis://127.0.0.1:1 ENVIRONMENT=test TESTING=true
python - <<'EOF'
from backend.app.services.srm_service import compute_srm
for observed in ({"a": 5000, "b": 5200}, {"a": 4800, "b": 5200}):
    r = compute_srm(observed, {"a": 50, "b": 50})
    print(observed, "chi2=%.3f" % r.chi2, "p=%.4g" % r.p_value, "warning=%s" % r.warning)

from backend.app.services.power_calculator_service import sample_size_two_proportions
print(sample_size_two_proportions(0.05, 0.055, 0.05, 0.80, True))
EOF
```

Expected result (a notice about unset Cognito settings may also print; ignore it):

```text
{'a': 5000, 'b': 5200} chi2=3.922 p=0.04767 warning=False
{'a': 4800, 'b': 5200} chi2=16.000 p=6.334e-05 warning=True
31234
```

The first split is a 4% imbalance at 10,200 users and would be flagged by a 0.05 rule, but not by this
platform's 0.001 threshold. The second is an 8% imbalance and trips it. The last line is the number the
power-analysis page documents for a 5% baseline and a 10% relative effect: 31,234 users per variant.

Now the reconciliation exercise. Control has 80 assignments and 16 matching events from 8 assigned users.
Treatment has 80 assignments, 12 events from 10 assigned users, plus 4 events from users with no
assignment. What is the binary lift?

**Answer.** Control is 8/80 = 10%; treatment is 10/80 = 12.5%. The difference is 2.5 percentage points
(25% relative lift). The unassigned users do not enter the numerator, and counting event rows would
wrongly suggest control wins (16 against 12). Before shipping you still need the SRM check, the planned
runtime and sample, uncertainty and multiplicity handling, guardrail results and the decision rule.

If you have the app's Quick Start stack running, `GET /api/v1/results/{experiment_id}` returns `srm`,
`sample_size_adequate` and the recommendation for a real experiment; the app's API documentation lists
the response fields.

### Key takeaways

- A metric contract fixes the unit, the event, the denominator and the horizon *before* traffic arrives.
- Controlvar's numerator is **converting users**, matched by event name and restricted to users assigned
  to the event's variant; there is no attribution window, so write yours into the plan.
- SRM (p < 0.001) protects the denominator; when it fires, the recommendation is `INCONCLUSIVE`
  whatever the p-value says.
- Time has two clocks, the event's and the server's. CUPED honours both; so should your contract.

**Review artifact:** save the metric contract, a synthetic-cohort reconciliation and the analysis plan
with the experiment. Another engineer should be able to explain every denominator before launch.

---

<a id="hour-2--a-complete-experiment-from-hypothesis-to-removal"></a>

## Hour 2 — A complete experiment: the ending nobody plans for

**Outcome:** produce a defensible decision and an operational handoff, including the cases where you do
not ship. The worked checkout experiment is illustrative; none of its numbers is a production result.

### What & why

#### Experiments end three ways, and only one is a win

An experiment can end in a ship, a no-ship or an **inconclusive** decision. Teams plan for the first and
improvise the others, which is how "not significant" becomes "no effect" and a missed deadline becomes a
launch. A complete experiment is a short sequence of commitments: write the plan, rehearse the
instrumentation, start only when the owner signs off, monitor *integrity and harm separately from
success*, decide under the rule you wrote, ship with a separate rollout plan, and remove the branch.

#### Integrity, harm and benefit are three different questions

Integrity asks whether the comparison is valid (assignment balance, collection loss, schema errors).
Harm asks whether users are being hurt (guardrails). Benefit asks whether the treatment is better. A
safety stop protects users and is not evidence that treatment loses; a favourable p-value does not excuse
a guardrail breach; and a broken comparison cannot be labelled a winner. Keep three columns in your
notes.

> **By the Way.** "Not significant" does not establish equivalence. An interval that includes both a
> useful benefit and material harm says you did not learn enough, not that the two arms are the same.

### How Controlvar does it

#### The platform locks the plan once you start

"Decide the method before you look" is easy to say and hard to enforce on yourself. Controlvar enforces
part of it. Once an experiment leaves draft, nobody, a superuser included, can change how its results
are judged or what is being measured:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
# For non-draft experiments, prevent updates to restricted fields.
# This runs after the superuser passes the guard above, so it binds
# every role: once an experiment leaves draft, nobody changes how its
# results are judged (#580). It works on presence, so re-sending the
# stored value is refused too.
if experiment.status != ExperimentStatus.DRAFT:
    restricted_fields = [
        "variants",
        "metrics",
        "start_date",
        "end_date",
        "correction_method",
        "confidence_level",
    ]
    for field in restricted_fields:
        if field in update_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot update {field} for experiments in {experiment.status.value} status",
            )
```

The comment is the contract: "once an experiment leaves draft, nobody changes how its results are
judged." The guard works on **presence**, so re-sending the stored value is refused too. Variants,
metrics, dates, `correction_method` and `confidence_level` are frozen. Targeting is the deliberate
exception: it may be edited while the experiment is a draft or paused, and a paused experiment accepts
targeting only on its own. Changing who is eligible mid-run changes which comparison the accumulated data
represents, so treat that edit as a protocol deviation and record it, even though the platform allows it.

Status changes only go through `/start`, `/pause`, `/complete` and `/archive`, each carrying its own
checks; a `PUT` that tries to set a different status is refused. Completing is allowed only from `ACTIVE`
or `PAUSED`:

```python
# 📁 backend/app/api/v1/endpoints/experiments.py
# Check if experiment is in a valid state to be completed
if experiment.status not in [ExperimentStatus.ACTIVE, ExperimentStatus.PAUSED]:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Cannot complete experiment with status: {experiment.status.value}",
    )
```

Completing stops new assignments and fixes the end date. It does **not** change any application's
rendered experience: your code decides what clients do after the decision. Verify the client behaviour
and keep the decision record.

#### The recommendation is an input, not an order

The results summary names one of a handful of recommendations. Read the code that chooses among them so
you know what each means:

```python
# 📁 backend/app/services/analysis_service.py
if not sample_size_adequate:
    return {
        "has_winner": True,
        "winning_variant_id": winner_id,
        "recommendation": "CONTINUE_TESTING",
        "recommendation_reason": (
            f"{reason} Not every variant has reached the minimum sample size yet, "
            "so keep the experiment running before shipping."
        ),
    }
return {
    "has_winner": True,
    "winning_variant_id": winner_id,
    "recommendation": "SHIP_VARIANT",
    "recommendation_reason": reason,
}
```

A significant winner gets `SHIP_VARIANT` only once every variant has reached the minimum sample size.
Elsewhere in the same function, a result where every treatment is significantly *worse* becomes
`KEEP_CONTROL`, and everything else is `CONTINUE_TESTING` with a reason that counts assigned users. SRM
overrides all of them with `INCONCLUSIVE`. An API recommendation is input to a decision; it is not an
instruction to change production.

> **Watch Out!** The experiment's stored `correction_method` defaults to Benjamini-Hochberg at 0.95. With
> two or more treatments, a treatment whose unadjusted `p_value` is 0.035 can be `is_significant: false`
> because its `adjusted_p_value` is 0.071. Exports show the unadjusted p-value next to the corrected
> verdict, so a spreadsheet reader can think the verdict is wrong. It is not; read both columns.

#### Monitor harm separately, and know which control you are pulling

Two controls exist and they are not the same. Pausing an experiment stops enrolment of new users into it.
A feature flag's kill switch changes what the flag serves. If your integration serves the winning
experience through a separate flag, an incident may need both. Hour 10 covers the flag side in depth.

#### Not everything is on the dashboard

Before you hand a runbook to an on-call engineer, check where each action lives. The app's
"dashboard and API" page lists, per capability, whether a screen exists. At v0.36.4, creating an
experiment, starting, pausing and completing it, and editing a draft's name, description and hypothesis
are dashboard actions; changing a draft's variants or metrics, scheduling start and end dates, creating or
advancing rollout schedules, and downloading results as CSV are API only. A runbook that says "click
Pause" for something that only exists as an API call will fail at 3 a.m.

#### Rehearse with a demo you can break

The ShopLab demo storefront in the app runs real experiments through the React SDK: a hero banner A/B
test, a bandit-driven product sort, a multivariate buy button, a checkout flow and a flag-driven search
rollout. Use it as a learning fixture. Create a separate experiment for your own exercise rather than
altering the demo's configuration, use synthetic shoppers and a non-production key, and record, for a
known cohort, the assigned user ids, the exposures and the conversions, including one repeated
conversion, one unassigned user and one ineligible user. Reconcile expected converting users against the
results page.

#### Ship behind a flag with an owner and a way out

If the decision permits shipping, implement the chosen experience behind a flag with a named owner, a safe
default and a removal date (Hour 10 covers what Controlvar does and does not enforce there). Roll out in
stages and keep an operator available for the risk window. Complete the experiment with the supported
lifecycle operation after deciding what clients will do; keep its configuration and decision record, then
remove the losing branch. Historical analysis may still need the events and configuration: application
cleanup does not authorise deleting history.

### Try It Yourself

Run the real recommendation function against hand-made inputs, with the same environment variables as
Hour 1:

```bash
python - <<'EOF'
from types import SimpleNamespace as NS
from backend.app.services.analysis_service import AnalysisService as A

def variant(vid, name, ctrl, sig, pct, n):
    return {"variant_id": vid, "variant_name": name, "is_control": ctrl, "is_significant": sig,
            "relative_improvement_pct": pct, "p_value": 0.01, "adjusted_p_value": 0.01, "sample_size": n}

metric = lambda variants, win: [{"is_primary": True, "metric_name": "checkout_completed",
                                 "winning_variant_id": win, "variants": variants}]
exp = NS()
win = metric([variant("c","control",True,False,None,500), variant("t","treat",False,True,12.0,500)], "t")
print(A._summarise_decision(exp, win, True)["recommendation"])
print(A._summarise_decision(exp, win, False)["recommendation"])
lose = metric([variant("c","control",True,False,None,500), variant("t","treat",False,True,-9.0,500)], None)
print(A._summarise_decision(exp, lose, True)["recommendation"])
flat = metric([variant("c","control",True,False,None,500), variant("t","treat",False,False,1.0,500)], None)
print(A._summarise_decision(exp, flat, True)["recommendation"])
EOF
```

Expected result:

```text
SHIP_VARIANT
CONTINUE_TESTING
KEEP_CONTROL
CONTINUE_TESTING
```

The second line is the interesting one: the same winning data, but with the sample floor unmet, is not
recommended for shipping.

Now the decision exercise. At the planned stopping point the observed lift is positive but the interval
spans a useful benefit and material harm. A product deadline is tomorrow. What goes in the decision
record?

**Answer.** "Inconclusive under the planned analysis; retain control." Include the uncertainty, the
guardrail evidence, the business cost of waiting and the options for a better-powered follow-up. If the
business ships for independent reasons, record that as a separate decision; do not rewrite the experiment
as proof of benefit. Any release still gets an owner and a rollback policy.

### Key takeaways

- Plan for all three endings. "Inconclusive" is a legitimate, recordable result.
- The platform freezes variants, metrics, dates, correction and confidence level once a draft starts;
  the plan you wrote is the plan that is judged.
- Integrity (SRM), harm (guardrails) and benefit (significance) are separate columns in your decision.
- A recommendation is an input; completing an experiment changes no application behaviour by itself.

**Review artifact:** one experiment plan, one instrumentation reconciliation, a decision record and a
rollout-and-removal handoff, each naming its owner and the evidence needed to pass its next stage.

---

<a id="hour-3--llm-evaluation-engineering"></a>

## Hour 3 — LLM evaluation engineering: the 0.5 that meant nothing

**Outcome:** compare model or prompt configurations using a reproducible dataset, calibrated scoring and
explicit failure accounting, and read exactly how Controlvar's LLM evaluation records failures, picks a
winner and prices a call.

### What & why

#### Offline evaluation and online experimentation answer different questions

An offline dataset asks how configurations perform on a *known task distribution*. An online randomised
experiment asks how deploying them changes outcomes for *eligible users*. A dataset can under-represent
production traffic, and a judge can reward prose that does not help customers. Use offline evaluation to
reject obvious regressions, then validate the business effect online. This lesson is about Controlvar's
evaluation workflow; it says nothing about current provider availability or prices, which you must check
separately before any paid call.

#### An LLM judge is a measuring instrument, and instruments fail

A judge is a model reading another model's answer and returning a number. Like any instrument it needs
calibration against human labels, tests for order and verbosity bias, adversarial cases (a response that
tells the judge to ignore the rubric), and a clear policy for what a *failed reading* means. The most
dangerous design is the one that substitutes a plausible number when the instrument fails: a 0.5 on a 0–1
scale looks like "average quality" and quietly drags every comparison toward the middle.

#### A story: the 0.5 that meant nothing

Controlvar used to do exactly that. When a judge call failed, or its reply held no usable score, the
service stored a fallback of 0.5, and the analysis then counted it as a real score. Worse, a reply like
"I give it 1 out of 5" had been read as a score of 1.0. Issue #1192 replaced all of it: a failed or
unparseable judge reply now stores **no score** (`None`), and the reason is recorded (`call_failed` or
`no_score`). Scores stored by the earlier version remain and cannot be told from a real 0.5, which is why
the documentation tells you to re-run the judge on such an experiment. A separate fix (issue #1214) made
the winner be chosen on one metric for every variant, so one variant's business-metric mean is never
compared with another variant's judge mean. The lesson is the same in both: *a number you cannot
distinguish from a failure is a defect*.

> **Did You Know?** A 2024-era default judge model id was once baked into a function signature, which
> meant the configured default was silently ignored by any caller passing arguments positionally. The
> function now takes `None` and falls back to `settings.LLM_DEFAULT_JUDGE_MODEL`. Hard-coded model
> identifiers age quickly; configuration is the right home for them.

### How Controlvar does it

#### How a judge reply is read

```python
# 📁 backend/app/services/llm_analytics_service.py
def _parse_judge_response(raw: str) -> Tuple[Optional[float], str, Optional[str]]:
    """
    Read the judge's score from its reply.

    A score is read only from a JSON object: the whole reply, or, when the
    whole reply is not a JSON object, the span from its first ``{`` to its
    last ``}`` (which covers a fenced block or a sentence around the
    object). The object's ``score`` must be a JSON number, or a string
    holding one, that is finite and from 0 to 1. Then the result is
    ``(score, reasoning, None)``.

    Anything else is ``(None, <fixed text and the reply's start>,
    "no_score")``: no object, a missing, null, boolean or non-numeric
    ``score``, NaN or infinity, or a value outside 0 to 1, which is not
    clamped. No number is taken from free text: "I give it 1 out of 5" used
    to score 1.0, and a missing score 0.5 (#1192).

    Linear in the length of the reply: two scans for the braces and at most
    two JSON parses.
    """
```

The strictness is the point. A score is read only from a JSON object (the whole reply, or the span from
the first `{` to the last `}`), it must be a finite number from 0 to 1, and it is *not* clamped if it is
outside. No number is ever taken from free text. The docstring also states a cost bound ("linear in the
length of the reply: two scans for the braces and at most two JSON parses"), which matters for untrusted
text: a judge reads whatever the model under test wrote, and that text must be treated as untrusted
content.

#### One metric decides the winner

```python
# 📁 backend/app/services/llm_analytics_service.py
def _choose_winner_metric(
    bm_by_variant: Dict[str, List[float]],
    ae_by_variant: Dict[str, List[float]],
) -> Optional[WinnerMetric]:
    """
    The one metric every variant is compared on (#1214).

    ``"business_metric"`` when every variant has at least one business-metric
    value; otherwise ``"auto_eval"`` when at least one variant has a judge
    score; otherwise None. The winner, and every variant's p-value and effect
    size, use this metric, so a variant's business-metric mean is never
    compared with another variant's judge mean.
    """
    if bm_by_variant and all(bm_by_variant.values()):
        return "business_metric"
    if any(ae_by_variant.values()):
        return "auto_eval"
    return None
```

If every variant has at least one business-metric value, the comparison uses the business metric.
Otherwise, if at least one variant has a judge score, it uses the judge score. Otherwise there is no
winner metric. The winner, and each variant's p-value and effect size, all use that one metric. The
results also report, per variant, how many evaluations are **unscored** (`n_auto_eval_unscored`), and
leave those out of the mean, interval, p-value and effect size. So the denominator problem has an
explicit counter; use it.

> **Watch Out!** The winner label is a summary of means, not an approval. It does not apply your
> cost or latency guardrails, does not prove practical significance, and a mean latency says nothing
> about a p95 objective. Treat it like the `recommendation` of Hour 2: an input.

#### Missing price is not zero price

```python
# 📁 backend/app/core/llm_pricing.py
def estimate_cost(
    provider: str, model_name: str, input_tokens: int, output_tokens: int
) -> float:
    """
    Estimate the cost of an LLM call in USD.

    Returns 0.0 when the provider/model is not in the table, and **logs a
    warning** naming it. The silence was the problem: an experiment on a model
    nobody had added -- which was every current Claude model until this change
    -- recorded a cost of exactly $0.00 in the LLM experiment record, which
    reads as "free" rather than as "unknown". 0.0 is kept as the return so a
    missing price never fails a request mid-experiment; the log is what makes
    it findable.

    Rates are Anthropic/OpenAI/... first-party list prices. A deployment
    routing Claude through Amazon Bedrock or Vertex AI pays that partner's
    rates instead, and this table does not model them.
    """
    provider_costs = COST_PER_1K_TOKENS.get(provider, {})
    model_costs = provider_costs.get(model_name, {})
    if not model_costs:
        logger.warning(
            "No cost entry for provider=%s model=%s; reporting $0.00. "
            "Add it to COST_PER_1K_TOKENS.",
            provider,
            model_name,
        )
        return 0.0
    input_cost = (input_tokens / 1000.0) * model_costs.get("input", 0.0)
    output_cost = (output_tokens / 1000.0) * model_costs.get("output", 0.0)
    return round(input_cost + output_cost, 8)
```

For a model not in the table, `estimate_cost` returns `0.0` **and logs a warning** naming the model. The
source explains why it still returns zero: so a missing price never fails a request mid-experiment, with
the log "what makes it findable". Read that carefully. The cost in the experiment record is then an
estimate from a built-in table of first-party list prices, not your bill. A deployment that routes
through a cloud partner pays that partner's rates, which the table does not model.

#### Online assignment is deterministic; offline replay is yours to build

The LLM experiment service assigns users to variants by hashing. The key and the arithmetic differ from
experiment assignment and from flag rollout on purpose-built historical reasons:

```python
# 📁 backend/app/services/llm_experiment_service.py
def _hash_bucket(experiment_id: str, user_id: str) -> float:
    """
    Map ``experiment_id:user_id`` to a float in [0, 1).

    Reduces the full 128-bit MD5 digest modulo 10000 and divides by
    10000, matching the other assignment hashers in the platform.

    Note: an earlier version reduced only the first 16 bits of the digest
    modulo 10000.  Because 65536 is not a multiple of 10000, buckets
    0-5535 were 7/6 as likely as buckets 5536-9999, which skewed a
    nominal 33/33/34 split to roughly 36/34/30.  Using the whole digest
    makes the bias negligible (2**128 mod 10000 is a rounding error).
    """
    key = f"{experiment_id}:{user_id}"
    digest = hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()
    bucket = int(digest, 16) % 10000
    return bucket / 10000.0
```

The docstring records a real defect: an earlier version reduced only the first 16 bits of the digest
modulo 10000, and because 65536 is not a multiple of 10000 a nominal 33/33/34 split came out near
36/34/30. A case-paired offline replay (every dataset case sent to *both* configurations) needs its own
harness; calling the user assignment function does not guarantee every case sees both arms. If outputs
come from the same case or user they are not independent: preserve identifiers for a paired or clustered
analysis outside the platform when its Welch's-t-based comparison does not match your design.

> **By the Way.** Controlvar has several bucketing hashes, each its own function. The shared
> experiment-assignment hash is the one the SDKs implement and the golden vectors pin. Flag rollout and
> the LLM service use different ones. The file `consistent_hash.py` lists every site, and says why none
> is changed in passing: changing any of them re-buckets users.

#### What to keep in a manifest

The platform stores variants with provider, model, prompt template, temperature and token limit, but
no dataset registry or replay engine. Keep a versioned, access-controlled manifest outside it: dataset
hash and case ids, slices, rubric, prompt and system prompt, model identifier and route, sampling
settings, judge configuration, and the failure policy you chose *before* seeing results. A provider alias
may change under an unchanged configuration; capture request time and returned metadata, and do not
store customer text in ordinary source control. Setting temperature to zero does not make a model
byte-for-byte reproducible.

### Try It Yourself

Run the real parsing and pricing code with no network:

```bash
python - <<'EOF'
import logging, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stdout, format="%(levelname)s %(message)s")
from backend.app.core.llm_pricing import estimate_cost
print(estimate_cost("anthropic", "claude-sonnet-5", 1000, 1000))
print(estimate_cost("anthropic", "my-new-model", 1000, 1000))
from backend.app.services.llm_analytics_service import LLMEvaluationAnalyticsService as S
for raw in ['{"score": 0.8, "reasoning": "ok"}', 'I give it 1 out of 5',
            '{"reasoning": "no score"}', '{"score": 1.7}',
            'Sure! {"score": "0.4", "reasoning": "x"}']:
    score, _, error = S._parse_judge_response(raw)
    print(repr(raw), "->", score, error)
EOF
```

Expected result (the first number depends on the price table at v0.36.4, and the Cognito notice may appear among the lines):

```text
0.012
WARNING No cost entry for provider=anthropic model=my-new-model; reporting $0.00. Add it to COST_PER_1K_TOKENS.
0.0
'{"score": 0.8, "reasoning": "ok"}' -> 0.8 None
'I give it 1 out of 5' -> None no_score
'{"reasoning": "no score"}' -> None no_score
'{"score": 1.7}' -> None no_score
'Sure! {"score": "0.4", "reasoning": "x"}' -> 0.4 None
```

Now a decision exercise. Replay 100 cases per configuration. Configuration A produces 95 valid responses
with mean valid-response score 0.80; B produces 100 with mean 0.78. Under a predeclared
failure-as-zero rule, who wins?

**Answer.** A's overall mean is 0.95 × 0.80 = 0.76 and B's is 0.78, so B. Reporting only 0.80 against 0.78
hides A's five failures. Another policy (say, treating a safety refusal differently) is acceptable only if
it was written before you saw which model benefits. Track attempted cases, provider failures, timeouts,
invalid outputs, valid scored cases and judge failures independently, and count retries in cost and latency.

### Key takeaways

- A failed judge reading must not look like a score. Controlvar stores `None` and counts unscored
  evaluations per variant; scores stored before the fix are indistinguishable from a real 0.5.
- The winner is chosen on one metric for every variant; it is a summary, never an approval.
- Missing price returns `$0.00` with a warning, so "free" can mean "unknown". The table is a list price
  estimate, not billing.
- Offline replay, pairing and clustering are your design; the platform gives you the denominators to
  build it on.

**Review artifact:** dataset and configuration manifest, calibrated rubric, failure table, slice analysis
and a promotion decision stating which checks were automated and which were done separately.

---

## Part I Workshop

### Q&A

**Why not make a shipping decision from raw event totals?** The binary metric counts assigned converting
users. Repeated events and unassigned senders change event totals without changing the intended outcome,
and Controlvar's `count_converting_users` drops both on purpose.

**If the SRM check passes, is the randomisation proven fine?** No. It says the observed split is
unsurprising at p ≥ 0.001. It does not detect every kind of non-exchangeability, and it is skipped for
bandit experiments.

**Can I change an experiment's correction method after launch?** No. `correction_method` and
`confidence_level` are among the fields refused on any non-draft experiment, including for a superuser.

**Does an LLM winner label authorise deployment?** No. Verify failures, the scoring design, practical
effect, uncertainty and operational guardrails separately.

### Quiz

1. What is the difference between percentage points and relative lift?
2. Does an inconclusive experiment establish equivalence?
3. Why does a failed judge reply store `None` rather than 0.5?
4. A conversion rate on a results page reads 112%. Name the first thing you check.
5. Which two clocks does CUPED's history rule use, and why?

**Answers.** (1) Percentage points subtract rates; relative lift divides the difference by the baseline.
(2) No; the interval may still include useful benefit and material harm. (3) A fallback number is
indistinguishable from a measurement; `None` plus a recorded reason keeps failures out of the mean and
visible in a counter. (4) Whether numerator and denominator count the same thing (events against users).
(5) The event's own time (inside the lookback window before assignment) and the server's receipt time
(stored before assignment), so that late-submitted history is not treated as pre-treatment.

### Exercises

1. **Contract.** Write the nine-row metric contract (population, unit, primary event, outcome, denominator,
   horizon, exclusions, guardrails, decision) for an experiment in a product you know. Mark each row
   "enforced by the platform", "recommended practice" or "unknown".
2. **Reconcile.** Using the arithmetic from Hour 1, build a small table of assignments and events for two
   variants that includes a repeated conversion, an unassigned sender and an exposure-only user, and
   compute the binary rates by hand. State which of the three rows the platform would drop.
3. **Decision record.** Write a one-page record for an inconclusive checkout test with a deadline
   tomorrow. Include what a follow-up study would need.
4. **Failure table.** For a 200-case evaluation of two prompts, design the failure table (attempted,
   provider failures, timeouts, invalid outputs, valid scored, judge failures) and decide the missing-data
   policy before inventing numbers.

### Solutions

1. A good answer marks "denominator: assigned users in the variant" and "outcome: at least one matching
   conversion" as enforced, "horizon: seven days" as recommended practice (no window exists in the code),
   and anything about revenue values as unknown or out of scope, since every metric is analysed as a
   conversion.
2. If control has 100 assignments and 20 matching events from 10 assigned users, and treatment has 100
   assignments and 18 events from 12 assigned users, the binary rates are 10% and 12%, a two-point
   difference, where event rows would suggest control is better. The platform drops the repeated events
   (counting users once), the unassigned sender (no assignment to that variant) and the exposure-only row
   (exposure types are excluded from conversion matching).
3. Record the planned stopping point and uncertainty, label the result inconclusive, retain control under
   the stated rule, and describe any independent business decision separately. A later release still needs
   an owner, guardrails and rollback evidence.
4. Whatever rule you pick must be written first. An illustrative policy: provider failures and timeouts
   count as zero quality and are reported separately; judge failures are excluded from the judged mean and
   counted per variant (as `n_auto_eval_unscored` does); retried calls count toward cost.

---

[Contents](./README.md) · [Part II →](./part-2-reliable-systems.md)

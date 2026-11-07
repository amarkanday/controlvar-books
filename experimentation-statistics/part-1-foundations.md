# Part I — Foundations

### Hours 1–3

[← Back to Contents](./README.md)

Three hours on the bedrock. A conversion-rate comparison stands on three things: users who were
really allocated at random, a test that suits the data, and enough observations to see an effect
worth acting on. Why randomisation is the whole point and how the platform checks that it actually
happened (Hour 1); the tests and intervals behind a conversion-rate or revenue-per-user comparison
and the labels the dashboard puts on them (Hour 2); and power — the number that tells you how many users a decision
needs and the gate that stops a lucky early result from shipping (Hour 3). Keep a Python shell
with `scipy` open; every formula here is one line away from a number.

---

## Hour 1 — Randomisation, exchangeability, and the sample-ratio mismatch check

### What & why

Picture a checkout test whose dashboard says "+8%, p = 0.01", and a week later the lift is gone.
Nobody lied; the allocation had quietly gone wrong — say a redirect only the treatment page
triggered, so users who bounced before it loaded were never counted in that arm — and the p-value
was computed, correctly, on two groups that were no longer comparable. Controlvar's answer is a
check that sits above every tab of the results page and, since a failed check overrides the
recommendation, can stop the page telling you to ship. This hour is about that check and the idea
behind it.

An A/B test is a **randomised controlled experiment**: users are assigned to variants by a
process that has nothing to do with them — a deterministic hash of their id, which also keeps
each user's variant stable — so the groups are, in expectation, identical in every way except the
treatment. That property is **exchangeability**, and it is what turns a difference in conversion
rates into evidence about the treatment rather than about who happened to land in which group.

Everything downstream assumes it. The z-test assumes the two samples are draws from the same
population under the null. The confidence interval assumes the only systematic difference is the
variant. If exchangeability fails — because a routing bug sends iOS users to control, because a
bot hammers one URL, because collection loses events for one arm, because a bandit deliberately
routes more users to the better arm — the p-value is still computed, still small or large, and
means nothing.

You can't observe exchangeability directly. You can observe its most common _symptom_: a
**sample-ratio mismatch (SRM)**. The experiment was configured 50/50; the assignment counts are
6,043 vs 5,712, which is 51.4% of 11,755 users in one arm. A chi-square goodness-of-fit test says
how surprising that is under a working randomiser. If it is very surprising (p < 0.001), the
randomiser is not doing what you think, and nothing else on the page can be trusted until you
know why. Be precise about what the check proves, though: passing it does **not** prove
exchangeability. It addresses allocation balance, which is only one requirement; a perfectly
balanced split can still hide a biased one.

#### Why p < 0.001, not 0.05

Because the dashboard is read constantly, and an SRM warning that cried wolf would be ignored.
The platform uses a far stricter threshold than the 0.05 you use for outcome tests, so that a
warning is rare and always worth a human's time. (You will sometimes read that a 0.05 threshold
"fires on one refresh in twenty". That is not right: refreshes of the same experiment re-read
mostly the same data, so they are correlated, and the false-alarm rate is not a fixed number per
refresh. The honest reason for 0.001 is the cost of a false alarm and the convention in the
experimentation literature, not a per-refresh calculation.)

### How Controlvar does it

The test is a pure function of counts and allocations:

```python
# 📁 backend/app/services/srm_service.py
"""
Sample-ratio mismatch (SRM) detection.

An SRM is the single most common way an A/B test silently lies: the
randomiser was configured for, say, a 50/50 split but the observed
assignment counts are 60/40.  When that happens the groups are no longer
exchangeable and every downstream p-value is meaningless.

The check is a Pearson chi-square goodness-of-fit test of the observed
per-variant assignment counts against the counts expected from the
variants' configured ``traffic_allocation`` (stored as an integer
percentage 0-100 on ``Variant``; allocations that do not sum to 100 are
normalised).  ``warning`` is raised at p < 0.001 — the conventional SRM
threshold, deliberately strict because dashboards are polled many times.
"""
#: p-value below which the observed split is flagged as a mismatch.
SRM_P_VALUE_THRESHOLD: float = 0.001


def compute_srm(observed: Mapping[str, int], allocations: Mapping[str, float], threshold: float = SRM_P_VALUE_THRESHOLD) -> Optional[SRMResult]:
    keys = [str(k) for k, a in allocations.items() if a is not None and float(a) > 0.0]
    if len(keys) < 2:
        return None
    alloc = {str(k): float(a) for k, a in allocations.items() if str(k) in keys}
    alloc_total = sum(alloc.values())
    if alloc_total <= 0.0:
        return None
    obs = {k: int(observed.get(k, 0) or 0) for k in keys}
    total = sum(obs.values())
    if total <= 0:
        return None
    expected = {k: total * alloc[k] / alloc_total for k in keys}
    statistic = sum((obs[k] - expected[k]) ** 2 / expected[k] for k in keys)
    dof = len(keys) - 1
    p_value = float(chi2_dist.sf(statistic, dof))
    p_value = min(1.0, max(0.0, p_value))
    return SRMResult(chi2=float(statistic), p_value=p_value, warning=p_value < threshold, expected=expected, observed=obs)
```

Pearson's statistic is Σ (O − E)² / E over the variants, with k − 1 degrees of freedom;
`chi2.sf` (the survival function, 1 − CDF) is the upper-tail probability. Variants with 0%
allocation are excluded — no assignments are _expected_ for them, and dividing by an
expected count of zero is undefined.

Two design decisions in the wrapper matter. The check **never fails a results request** —
`sample_ratio_check` logs and returns `None` on any error, because an SRM check that broke the
page would be worse than no check. And it **only runs for fixed-allocation experiments**:

```python
# 📁 backend/app/services/srm_service.py
def _is_fixed_allocation(optimization_type: Any) -> bool:
    """True when an experiment splits traffic by ``traffic_allocation``.

    ``None`` (never set / legacy row) counts as fixed: those experiments are
    served the static split.  Anything else is a bandit.
    """
```

A bandit _intends_ to drift from the configured split (Hour 9), so an SRM warning on a
bandit would be noise; `compute_srm_for_experiment` returns `None` for it. (That also means
bandit results are _never_ exchangeability-guaranteed — which is exactly the trade Hour 9
explains.) One limitation is written into the module docstring and worth repeating: if someone
edits a running experiment's `traffic_allocation`, the counts mix the old and the new split and
the check tests them against the current one, because nothing records when an allocation changed.

The check is run by `AnalysisService.get_experiment_results`, not by the route, so `/results`,
the data export and the report all give the same answer. The route only serialises the block:

```python
# 📁 backend/app/api/v1/endpoints/results.py
        # --- P0 statistical credibility: sample-ratio mismatch ---
        srm_response = _srm_block(experiment_id, result.get("srm"))
```

and it rides on the `/results` response as `srm`. A failed check does more than add a block. When
`warning` is set, `recommendation_under_srm` replaces the summary's recommendation:

```python
# 📁 backend/app/services/srm_service.py
SRM_RECOMMENDATION: str = "INCONCLUSIVE"
```

The reason string reads "Sample-ratio mismatch: the observed assignment split does not match the
configured traffic allocation (p = ...), so these results cannot be trusted until the cause is
found." Not even `CONTINUE_TESTING` survives, because more data does not repair a broken
randomiser. `has_winner` and `winning_variant_id` are left alone, because they describe
significance, not trust. Like the check, the rule fails open: if it
raises, the engine's own recommendation stands.

The core dashboard renders the check, above the tabs because a mismatch qualifies every one of
them (`SrmNotice.tsx`). A pass is one quiet line ("Sample ratio check passed"); a warning is a
region with the observed and expected count per variant, the chi-square and the p-value. The
"Leading" chip and the Bayesian panel carry a qualifier under a warning. A `null` `srm` — a
bandit, no assignments yet, a check that failed — shows nothing, and absence is not a pass.

What an SRM tells you to _do_: nothing statistical. Go find the leak. The usual suspects, in
order: a targeting rule or SDK version that fails to assign on one platform (users drop out of
one arm); a redirect or rendering path that only some variants trigger (assignments happen
in different places); a bot that hits one URL; a data pipeline that loses events for one
variant. The chi-square tells you the split is wrong; the fix is always in the plumbing.

> **By the Way.** Exchangeability is also why the eligibility gates in `assign_user` run
> _before_ hashing, and why an ineligible user gets no assignment row. If a targeting rule
> excluded users _after_ assignment (say, by dropping treatment-arm users who don't match),
> the surviving groups would no longer be exchangeable — the rule would have selected on a
> user attribute in one arm only. Gate first, then randomise.

> **Did You Know?** The other classic exchangeability killer is **sticky assignment plus a
> changed split**. Move a 50/50 test to 70/30 mid-run and new users follow the new split
> while old users keep their variant; the assignment counts no longer match either
> configuration, the SRM fires, and it's right to — the two arms now have different
> _tenure_ distributions. The SRM module's own docstring lists an edit of a running
> experiment's `traffic_allocation` as a known limitation, for this reason: nothing records when an
> allocation changed, so the counts are tested against the current split. The safe move is a new
> experiment rather than a re-split.

### Try It Yourself

```python
>>> from backend.app.services.srm_service import compute_srm
>>> compute_srm({"control": 5000, "treatment": 5000}, {"control": 50, "treatment": 50}).p_value        # ≈ 1.0
>>> r = compute_srm({"control": 6043, "treatment": 5712}, {"control": 50, "treatment": 50}); r.chi2, r.p_value, r.warning
(9.32..., 0.002266..., False)
>>> compute_srm({"control": 5100, "treatment": 4900}, {"control": 50, "treatment": 50}).p_value        # ≈ 0.0455 — not a warning at 0.001
>>> compute_srm({"a": 100}, {"a": 100})                                                                 # None: one arm, undefined
>>> compute_srm({"a": 7000, "b": 3000}, {"a": 70, "b": 30}).warning                                     # False: matches the configured split
```

Then simulate the false-alarm rate: draw 10,000 fair 50/50 splits of 10,000 users with
`numpy.random.default_rng(0).binomial(10000, 0.5, 10000)` and count how many would warn at
0.05 vs 0.001 (519 vs 7 of the 10,000).

### Key takeaways

- Randomisation → exchangeability → every statistic's licence to speak. Break it and the
  numbers are arithmetic on a lie.
- SRM = Pearson chi-square of observed assignment counts vs `traffic_allocation`, k − 1 dof,
  warning at p < 0.001 (deliberately strict, so a warning is rare and means something).
- Best-effort (fails open), never fails the request, skipped for bandits, excludes 0% arms.
- It is on the `/results` response (`srm`) and in a notice above the dashboard's tabs; a warning
  also turns the recommendation into `INCONCLUSIVE`.
- An SRM warning is a plumbing bug to find, not a statistic to adjust.

---

## Hour 2 — Proportions and means: Fisher's exact test, Welch's t-test, the z-test, intervals, effect size

### What & why

A conversion metric is a **proportion**: `converting users / assigned users` per variant. A
revenue, count, duration or custom metric is a **mean per assigned user**: every assigned user
contributes one number (what they spent, how many times they did the thing, how long they spent
doing it), and a user who did nothing contributes 0. Comparing two proportions is the oldest
question in statistics and comparing two means the next oldest, and there are several standard
tools for each. This is what the platform uses each one for:

| Question                                | `/results`, conversion metric                  | `/results`, revenue / count / duration / custom | `?breakdown=` segments              |
| --------------------------------------- | ---------------------------------------------- | ----------------------------------------------- | ----------------------------------- |
| Do the arms differ? (p-value)           | **Fisher's exact test**, at every n            | **Welch's t-test**                              | pooled **two-proportion z-test**    |
| How precise is each arm? (CI)           | normal approximation at the analysis level     | t-interval at the analysis level                | **Wilson** interval, at the corrected α |
| How precise is the difference? (CI)     | **Newcombe** on the difference, **MOVER-R** on the lift | **Welch** on the difference, **Fieller** on the lift | —                         |
| How big is the difference? (effect)     | **Cohen's h**, labelled                        | **Cohen's d**, labelled                         | —                                   |

(A breakdown of a revenue, count, duration or custom metric reports the share of users who sent
its event in each segment, not its mean: see Part IV Hour 11.)

**Fisher's exact test** computes the exact probability of the observed 2×2 table (and all
more extreme tables) under the null of equal rates — no approximation, correct at any size,
and cheap on a 2×2 table. The **z-test** approximates the difference in rates as normal with
a pooled standard error — accurate once counts are large, and what the breakdown service
uses for its per-segment p-values (Part IV Hour 11).

A **confidence interval** is the range of true rates consistent with the data at a
confidence level; the normal approximation `p̂ ± z·√(p̂(1−p̂)/n)` is fine in the middle and
misbehaves near 0 or 1 (it can go negative). **Wilson's** interval fixes that by solving the
inequality properly — it stays in [0, 1] and has better coverage for rare events.

**Effect size** answers "is it _big_?" separately from "is it _real_?". **Cohen's h** =
2·arcsin√p₂ − 2·arcsin√p₁ puts a difference in proportions on a scale where 0.2/0.5/0.8 mean
small/medium/large regardless of the base rate. A significant-but-negligible result is the
most common outcome of a well-powered test, and the label is what lets the results page say
so. **Cohen's d**, the difference of two means over √((s_c² + s_t²)/2), does the same job for
a mean, with the same cut-offs.

**Welch's t-test** compares two means without assuming the arms share a variance:
t = (ȳ_t − ȳ_c) / √(s_t²/n_t + s_c²/n_c), on the Welch–Satterthwaite degrees of freedom. On
revenue the variances are rarely equal — a treatment that turns a few more users into big
spenders changes the spread as well as the mean — so the pooled-variance t-test's assumption is
the wrong default.

An interval on each arm says how precisely that arm is known. A decision needs to know how
precisely the **difference** is known, and that is its own interval; so is the relative lift.
The "+30%" a product manager quotes is a ratio, and its uncertainty is not the difference's
interval divided by the control's rate.

### How Controlvar does it

#### Two analyses, one module

`/results` does its statistics on counts and per-user values, not on rows. For each metric it
counts, per variant, the users assigned and the users who converted, and then the metric's type
decides which function in `backend/app/services/sufficient_stats_analysis.py` gets them:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
MEAN_METRIC_TYPES = ("revenue", "count", "duration", "custom")
```

```python
# 📁 backend/app/services/analysis_service.py  (abridged)
        alpha = 1.0 - confidence_level
        metrics = [
            (
                self._per_user_mean_result(
                    experiment, metric, raw, alpha, correction_method
                )
                if metric_type_name(metric) in MEAN_METRIC_TYPES
                else binomial_metric_result(
                    ...
                    alpha,
                    correction_method,
                    metric=metric,
                )
            )
            for metric, raw in zip(metric_definitions, metrics_results)
        ]
```

A new `MetricType` member fails `test_value_metrics_per_user.py` until someone classifies it on
one side or the other, so no metric type reaches `/results` unanalysed by accident. Each metric in
the response says which analysis it got in its `analysis` field: `"mean"`, or the schema's
default, `"proportion"`.

A conversion metric goes to `binomial_metric_result`. Everything `/results` reports about its
variants follows from `(n, n_converted)`:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
def two_sided_z(confidence_level: float) -> float:
    """The two-sided normal critical value for ``confidence_level``.

    ``norm.ppf(1 - (1 - level) / 2)``: 1.6449 at 0.90, 1.9600 at 0.95,
    2.5758 at 0.99.
    ...
    """
```

```python
# 📁 backend/app/services/sufficient_stats_analysis.py  (abridged)
def binomial_variant_results(variants: Sequence[BinomialCounts], confidence_level: float = 0.95) -> List[Dict[str, Any]]:
    z = two_sided_z(confidence_level)
    alpha = 1.0 - confidence_level
    ...
            contingency_table = [
                [variant_conversions, variant_non_conversions],
                [control_conversions, control_non_conversions],
            ]

            try:
                odds_ratio, p_value = stats.fisher_exact(contingency_table)
                is_significant = p_value < alpha
    ...
        # Confidence interval using the normal approximation
        if assignments[variant_id] > 0:
            proportion = rates[variant_id] / 100  # Convert percentage to proportion
            se = math.sqrt((proportion * (1 - proportion)) / assignments[variant_id])

            ci_lower = max(0, (proportion - z * se) * 100)
            ci_upper = min(100, (proportion + z * se) * 100)
```

Three things to read off it:

- **Fisher for every treatment of every conversion metric, at every n.** There is no switch to
  a z-test for large samples: `statistical_test_used` is `"fisher_exact"` on every treatment of
  a conversion metric. (The previous edition of this book said the same of _every_ metric,
  revenue included, which was true until engine 1.5.0; "Means per assigned user" below is what
  replaced it.)
- **The interval and the significance decision use the same level.** The level sets both `z`
  (via `two_sided_z`) and `alpha = 1 − level`. Where the level comes from changed: each
  experiment now stores a `confidence_level` (0.80 to 0.99, default 0.95) and a
  `correction_method` (default `benjamini_hochberg`), locked once the experiment leaves
  draft, and `/results` uses them unless the request names its own `?confidence_level=` or
  `?correction_method=`. Choose 0.90 and the interval narrows to ±1.645·se and significance is
  p < 0.10. (Before the fix for the hard-coded-1.96 bug the interval ignored whatever level
  you asked for; that fix shipped in engine version 1.2.0, and the engine is now at 1.8.0.
  Part IV Hour 12 lists what each version moved.)
- **The clamp.** `max(0, …)` and `min(100, …)` are the tell that the normal interval can
  leave [0, 1] near the edges.

`binomial_metric_result` wraps that per-variant table into the metric result: it applies
the multiple-comparison correction across the treatments (Part IV Hour 10), decides
`is_significant` on the adjusted p when there is one, and adds Cohen's h with its label and
the **observed power** — `NormalIndPower` at the result's α for the observed h. Treat that
power figure with care: post-hoc power is a function of the p-value, so it tells you nothing
the p-value didn't; plan power before the experiment (Hour 3).

Because the function reads no database, any caller with the same counts gets byte-identical
numbers — which is how warehouse runs (Part III, "Analysing from sufficient statistics") and
`/results` agree. `test_binomial_metric_result_characterisation.py` pins it to the output
`/results` produced before it was extracted, and `test_sufficient_stats_fingerprint.py` pins
its output per engine version.

#### Means per assigned user

A revenue, count, duration or custom metric goes to `per_user_mean_metric_result`.
`AnalysisService` first reads one value for each assigned user who has one: for a count metric,
the number of the metric's events they sent (`event_counts`); for the others, the total of those
events' `value` (`value_totals`). The events are the ones `count_converting_users` would count —
matching the metric's event name, tagged with the user's variant, from a user assigned to it. A
value that is not a number, is infinite or is larger than 10¹² in size (`VALUE_BOUND`) adds
nothing to its user's total, and the user still counts as converting. Every other assigned user
is 0. Then:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py  (abridged)
    users = sum(n for _, n, _, _ in arms)
    k = (
        math.fsum(value for _, _, _, values in arms for value in values) / users
        if users
        else 0.0
    )
    sums: List[MeanSums] = []
    means: Dict[str, float] = {}
    for variant, n, _, values in arms:
        zeros = n - len(values)
        mean = math.fsum(values) / n if n else 0.0
        means[str(variant.id)] = mean
        sum_d = math.fsum([value - k for value in values] + [-k * zeros])
        ...
    compared = [row for row in sums if row[1] >= 2]
    ...
            result = mean_metric_result(
                k, compared, alpha, correction_method, metric=metric
            )
            welch = _welch_differences(compared, alpha)
```

Three things in it deserve a pause.

- **The zeros are users.** `zeros = n - len(values)` puts every non-buyer into the mean. A
  treatment that sells to more people at a smaller basket can raise revenue per assigned user
  while lowering revenue per buyer, and only the first answers "did this change make us money?"
- **The sums are centred** on the grand mean `k` before anything is compared, for the reason
  Part III's warehouse section gives: Σy² − (Σy)²/n cancels badly when the values sit far from
  zero.
- **The test is the warehouse's.** `mean_metric_result` runs
  `ttest_ind_from_stats(..., equal_var=False)` on the centred means, so `/results` and a
  warehouse run of the same per-user values give the same p-value. Each arm's interval is
  `mean ± t(1 − α/2, n − 1)·s/√n`; the effect size is Cohen's d.

`statistical_test_used` is `"welch_t_test"`, `analysis` is `"mean"`, and `conversions` is still
the number of users who converted, so a revenue metric's row says both how many users bought and
how much the average assigned user spent. An arm with fewer than 2 users is reported with its mean
and no comparison.

> **Watch Out!** Welch's t-test leans on the difference of the means being close to normal, and
> on zero-inflated, right-skewed revenue in a **small** arm it is not.
> `backend/tests/unit/stats_validation/test_false_winner_unequal_split.py` runs 2,000 A/A
> experiments — both arms drawn from one distribution: 5% of users buy, a buyer orders again
> with probability 0.3, each order is lognormal(3, 1) — through `per_user_mean_metric_result` and
> the recommendation, exactly as `/results` does. With 3,000 users in each arm, 46 of the 2,000
> are recommended `SHIP_VARIANT`, inside the 29–74 band that α = 0.05 allows in one direction.
> With 600 in the control and 5,400 in the treatment, **150** are: 7.5% where 2.5% is the
> promise, and the arm wrongly named is the larger one every time. The mechanism is in the small
> arm. Its mean and its standard deviation rise and fall together (one big spender lifts both; a
> correlation above 0.8 over 2,000 draws of a 600-user arm), so a control that drew low also
> looks precise, and the larger treatment is then called significantly higher. The product's own
> limitations page states it (#1736), and no lopsided split size is known at which revenue meets
> α. Keep revenue experiments evenly split, and treat a revenue win from a lopsided split as a
> hypothesis.

#### The difference, the lift and the impact

Since engine 1.8.0 (#1496) every treatment of every metric on `/results` carries five more
fields, all null on the control (a warehouse run's mean metrics do not carry them; Part III):

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
DIFFERENCE_FIELDS = (
    "absolute_difference",
    "absolute_difference_ci",
    "relative_improvement_pct_ci",
    "incremental_impact",
    "incremental_impact_ci",
)
```

One construction covers both analyses. Each arm gets a point estimate and two-sided limits at
the response's level, and the intervals are built from those limits by **MOVER** (the method of
variance estimates recovery). For a proportion the arm limits are Wilson's (`wilson_limits`), and
the difference's interval is Newcombe's hybrid score interval:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
def newcombe_interval(treatment: ArmLimits, control: ArmLimits) -> Tuple[float, float]:
    """The MOVER interval of ``treatment.mean - control.mean`` from each arm's
    limits: Newcombe's hybrid score interval when the limits are Wilson's.

    ``d - sqrt((m_t - l_t)^2 + (u_c - m_c)^2)`` to
    ``d + sqrt((u_t - m_t)^2 + (m_c - l_c)^2)``, with ``d = m_t - m_c``.
    """
    d = treatment.mean - control.mean
    return (
        d - math.hypot(treatment.mean - treatment.lower, control.upper - control.mean),
        d + math.hypot(treatment.upper - treatment.mean, control.mean - control.lower),
    )
```

Read the lower bound: the difference could be smaller than `d` if the treatment's true rate is
lower than its estimate (by up to `m_t − l_t`) or the control's higher (by up to `u_c − m_c`),
and the two distances add in quadrature. Wilson limits are asymmetric near 0, so this interval
is too, which a symmetric `d ± z·se` is not. For a mean the arm limits are `mean ± c·se` with
Welch's critical value `c`, and the difference's interval is Welch's own. The **relative**
change's interval (`relative_change_interval`) is the MOVER-R interval of the ratio on the same
limits — Fieller's interval, for a mean — and because the ratio's bound is 1 exactly where the
difference's bound is 0, the three intervals always agree on whether 0 is inside. For a
proportion the ratio is taken over values of 0 and above, so the lift's lower end is never below
−100%; it is null only when an arm has no users or the control has no conversions. The
**incremental impact** is the difference, and its interval, times the treatment's own assigned
users: a plain number, with no currency and no projection to other traffic or a later period.

> **Watch Out!** None of these intervals is adjusted for multiple comparisons, and
> `is_significant` is decided by the test, not by an interval. With two or more treatments under
> a correction an interval can exclude 0 on a treatment that is not significant. For a
> proportion near the threshold the interval and Fisher's test can also disagree, either way:
> 128 of 1,000 against 100 of 1,000 has p = 0.057 while its interval runs from +0.0001 to
> +0.056, and 3 of 50 against 18 of 100 has p = 0.049 while its interval reaches +0.00003. Take
> the verdict from `is_significant` and the size from the interval.

#### The winner has a direction

Some metrics are better when they fall: checkout errors, latency, uninstalls. A metric
definition's `lower_is_better` decides which side of the control a winner must be on (engine
1.5.0, #1377):

```python
# 📁 backend/app/services/sufficient_stats_analysis.py  (in binomial_metric_result)
    lower_is_better = metric_lower_is_better(metric)
    if lower_is_better:
        winners = [
            v
            for v in results
            if not v["is_control"]
            and v["is_significant"]
            and (v["relative_improvement_pct"] or 0) < 0
        ]
        winner = min(winners, key=lambda v: v["mean"]) if winners else None
```

`mean_metric_result` has the same branch on the centred means. `relative_improvement_pct` stays
the signed change whatever the direction (−42.3% is the good news on an error metric), and the
recommendation's sentence says "it fell by 42.3%, and lower is better for this metric" rather
than printing a negative improvement. `KEEP_CONTROL` flips with it: on a lower-is-better metric
it means every treatment is significantly _higher_. Only `True` counts: a definition without the
attribute, or with a stored NULL, is higher-is-better (`metric_lower_is_better`). The Bayesian
decision and the bandit read the same flag (Part III, Hours 8 and 9).

#### The textbook helpers

`AnalysisService` still carries reference implementations of the other tools —
`z_test_proportions`, `wilson_confidence_interval`, `cohens_h`, `welch_t_test` and a
`select_statistical_test` that would pick between them by sample size and metric type. **No
route calls them.** They are worth reading because they are the formulas, and the Try It
uses them, but do not mistake them for what `/results` runs. The pooled z-test, with the
formula in the docstring:

```python
# 📁 backend/app/services/analysis_service.py
    def z_test_proportions(self, control_conversions, control_size, treatment_conversions, treatment_size) -> float:
        """Two-proportion z-test (pooled). Returns the two-tailed p-value.

        Formula:
            pooled_p = (x_c + x_t) / (n_c + n_t)
            se = sqrt(pooled_p * (1 - pooled_p) * (1/n_c + 1/n_t))
            z = (p_t - p_c) / se
            p = 2 * (1 - Phi(|z|))
        """
        if control_size <= 0 or treatment_size <= 0:
            return 1.0

        p_c = control_conversions / control_size
        p_t = treatment_conversions / treatment_size
        total = control_size + treatment_size
        pooled_p = (control_conversions + treatment_conversions) / total

        se = math.sqrt(pooled_p * (1.0 - pooled_p) * (1.0 / control_size + 1.0 / treatment_size))
        if se == 0.0:
            return 1.0

        z = (p_t - p_c) / se
        p_value = 2.0 * (1.0 - float(stats.norm.cdf(abs(z))))
        return min(1.0, max(0.0, p_value))
```

"Pooled" because under the null both arms share one rate, so the standard error is
estimated from the combined proportion. `2 * (1 - Φ(|z|))` is the two-tailed p-value: the
probability of a difference at least this large in _either_ direction.

The Wilson interval:

```python
# 📁 backend/app/services/analysis_service.py
    def wilson_confidence_interval(self, successes: int, total: int, confidence_level: float = 0.95) -> Tuple[float, float]:
        """Wilson score confidence interval for a proportion.

        Preferred over the normal approximation because it stays within
        [0, 1] and has better coverage for extreme proportions (p near 0 or 1).
        """
        if total <= 0:
            return (0.0, 1.0)
        z = float(stats.norm.ppf((1.0 + confidence_level) / 2.0))
        z2 = z * z
        n = float(total)
        p_hat = successes / n
        center = (p_hat + z2 / (2.0 * n)) / (1.0 + z2 / n)
        margin = (z * math.sqrt(p_hat * (1.0 - p_hat) / n + z2 / (4.0 * n * n)) / (1.0 + z2 / n))
        lower = max(0.0, center - margin)
        upper = min(1.0, center + margin)
        return (lower, upper)
```

The breakdown service has its own copies of the pooled z-test and the Wilson interval
(`DimensionalAnalysisService._two_prop_z_test`, `_wilson_ci`), and those **are** live: every
segment's p-value is a z-test and every segment's interval is Wilson at the
Bonferroni-corrected α (Part IV Hour 11).

Effect size, with the labels the results carry:

```python
# 📁 backend/app/services/analysis_service.py
    def cohens_h(self, p1: float, p2: float) -> Tuple[float, str]:
        """Cohen's h effect size for two proportions.

        h = 2 * arcsin(sqrt(p2)) - 2 * arcsin(sqrt(p1))
        ...
        Labels (EP-016): negligible < 0.2, small < 0.5, medium < 0.8, large >= 0.8
        """
```

`binomial_metric_result` computes the same h inline and labels it with the same thresholds
(`effect_size_label`). The arcsine transform is why a 4% → 5% lift and a 40% → 41% lift get
_different_ effect sizes even though both are "one point": the first is a 25% relative
change at a rate where the variance is small; the second is a 2.5% relative change. Cohen's
h sees that; a raw difference doesn't.

**Welch's t-test** — which doesn't assume equal variances between arms, the right default
for a continuous metric — runs in two places, and both are `mean_metric_result`: `/results`,
for revenue, count, duration and custom metrics (through `per_user_mean_metric_result`, above),
and **warehouse mean metrics** (full profile; Part III). `AnalysisService.welch_t_test`, the
textbook helper, is on neither path.

> **Watch Out!** A conversion is a **converting user**, not an event. Every analysis path —
> `/results`, the summary, `/daily`, `/sequential`, `/bayesian`, breakdowns, the bandit
> scheduler's PostgreSQL fallback — counts a user once if they are assigned to the variant
> and have at least one event that matches the metric's event name and is tagged with their
> variant (`count_converting_users` in `backend/app/services/event_matching.py`). A user who
> purchased three times counts once; an event from a user with no assignment to that variant
> does not count; so conversions can never exceed assignments, and the rate is a true
> proportion. `/daily` reports each user on the day of their _first_ conversion, so its
> cumulative series ends at the `/results` count. "Purchases per user" is a **count** metric
> and "revenue per user" a revenue metric, and `/results` analyses both as a mean per assigned
> user from the same matched events. Only `/results`' own metric rows do, though: the
> sequential, Bayesian and sample-size analyses, `/daily`, the Live tab and the breakdowns
> analyse a revenue, count, duration or custom metric as the share of users who sent its event
> (`docs/getting-started/limitations.md`).

> **Did You Know?** A p-value is _not_ "the probability the null is true," and 1 − p is
> _not_ "the probability the treatment is better." It's the probability of data at least
> this extreme _if_ the null were true. The number a product manager actually wants —
> P(treatment > control) — is the Bayesian probability-to-be-best (Part III Hour 8). The
> API returns it (`bayesian_results` on `/results`, and `GET /results/{id}/bayesian`), and the
> Overview tab shows it as "Chance to be best".

### Try It Yourself

```python
>>> from scipy import stats
>>> import math
>>> from backend.app.services.analysis_service import AnalysisService
>>> svc = AnalysisService(db=None)
>>> stats.fisher_exact([[52, 948], [40, 960]]).pvalue    # treatment 5.2%, control 4.0%: what /results reports
0.2401...
>>> svc.z_test_proportions(40, 1000, 52, 1000)           # the z-test on the same table
0.2002...
>>> svc.wilson_confidence_interval(52, 1000), svc.wilson_confidence_interval(1, 20)
((0.0399, 0.0676), (0.0089, 0.2361))                     # rounded
>>> p = 1/20; (p - 1.96*math.sqrt(p*(1-p)/20), p + 1.96*math.sqrt(p*(1-p)/20))   # normal: negative lower bound
>>> svc.cohens_h(0.04, 0.05), svc.cohens_h(0.40, 0.41)   # same "one point", different h
((0.0483, 'negligible'), (0.0204, 'negligible'))         # rounded
>>> svc.z_test_proportions(400, 10000, 520, 10000)       # ten times the data
5.11e-05
```

Now the engine itself, at two confidence levels:

```python
>>> from backend.app.services.sufficient_stats_analysis import binomial_metric_result, BinomialVariant
>>> class M: id = "m"; name = "conversion"; metric_type = "conversion"; is_primary = True
>>> C, T = BinomialVariant("c", "control", True), BinomialVariant("t", "treatment", False)
>>> for alpha in (0.05, 0.10):
...     t = binomial_metric_result([(C, 1000, 40), (T, 1000, 52)], alpha, "none", metric=M)["variants"][1]
...     print(alpha, [round(x, 4) for x in t["confidence_interval"]], round(t["p_value"], 4), t["is_significant"], round(t["power"], 3), t["statistical_test_used"])
0.05 [0.0382, 0.0658] 0.2402 False 0.25 fisher_exact
0.1 [0.0405, 0.0635] 0.2402 False 0.361 fisher_exact
```

Same p-value (Fisher doesn't depend on the level), a narrower interval and a higher observed
power at 0.10. Now the difference fields on the same counts, then the same 40 and 52 buyers as a
revenue metric, each with a basket:

```python
>>> t = binomial_metric_result([(C, 1000, 40), (T, 1000, 52)], 0.05, "none", metric=M)["variants"][1]
>>> round(t["absolute_difference"], 4), [round(x, 4) for x in t["absolute_difference_ci"]], [round(x, 1) for x in t["relative_improvement_pct_ci"]]
(0.012, [-0.0065, 0.0308], [-13.0, 94.0])
>>> round(t["incremental_impact"], 1), [round(x, 1) for x in t["incremental_impact_ci"]]
(12.0, [-6.5, 30.8])
>>> import numpy as np
>>> from backend.app.services.sufficient_stats_analysis import per_user_mean_metric_result
>>> class R: id = "r"; name = "revenue"; metric_type = "revenue"; is_primary = True
>>> rng = np.random.default_rng(6)
>>> spend_c, spend_t = rng.lognormal(3, 1, 40).round(2), rng.lognormal(3, 1, 52).round(2)   # each buyer's total
>>> r = per_user_mean_metric_result([(C, 1000, 40, spend_c.tolist()), (T, 1000, 52, spend_t.tolist())], 0.05, "none", metric=R)
>>> c, t = r["variants"]; r["analysis"], t["statistical_test_used"], t["conversions"]
('mean', 'welch_t_test', 52)
>>> round(c["mean"], 4), round(t["mean"], 4), round(t["relative_improvement_pct"], 1), round(t["p_value"], 4)
(1.4449, 1.4947, 3.4, 0.9037)
>>> round(t["absolute_difference"], 4), [round(x, 4) for x in t["absolute_difference_ci"]], [round(x, 1) for x in t["relative_improvement_pct_ci"]]
(0.0498, [-0.7572, 0.8568], [-41.9, 88.5])
>>> w = stats.ttest_ind(np.r_[spend_t, np.zeros(948)], np.r_[spend_c, np.zeros(960)], equal_var=False)   # every user, 0 for a non-buyer
>>> round(float(w.pvalue), 4), [round(float(x), 4) for x in w.confidence_interval()]
(0.9037, [-0.7572, 0.8568])
```

Thirty per cent more buyers is a 3.4% lift in revenue per assigned user here, at p = 0.90, with a
lift interval from −42% to +89%: the baskets, not the buyers, carry the variance. scipy's Welch
test on all 2,000 per-user values, zeros included, gives the platform's p-value and interval to
four decimals. On the conversion metric the impact reads as a sentence: about 12 more converting
users, somewhere between 6.5 fewer and 30.8 more. And the direction:

```python
>>> class E: id = "e"; name = "checkout_error"; metric_type = "conversion"; is_primary = True; lower_is_better = True
>>> e = binomial_metric_result([(C, 1000, 52), (T, 1000, 30)], 0.05, "none", metric=E)   # errors: 5.2% → 3.0%
>>> e["winning_variant_id"], round(e["variants"][1]["p_value"], 4), round(e["variants"][1]["relative_improvement_pct"], 1)
('t', 0.0174, -42.3)
>>> AnalysisService._summarise_decision(None, [e])["recommendation"]      # the experiment argument is not read
'SHIP_VARIANT'
>>> E.lower_is_better = False; h = binomial_metric_result([(C, 1000, 52), (T, 1000, 30)], 0.05, "none", metric=E)
>>> h["winning_variant_id"] is None, AnalysisService._summarise_decision(None, [h])["recommendation"]
(True, 'KEEP_CONTROL')
```

The same counts, the opposite recommendation. Then take a demo experiment's counts from the
results API and reproduce `p_value`, `confidence_interval`, `relative_improvement_pct` and
`absolute_difference_ci` for one variant of a conversion metric by passing its `sample_size` and
`conversions` to `binomial_metric_result` with `alpha = 1 − confidence_level`.

### Key takeaways

- `/results` routes each metric by its type (`MEAN_METRIC_TYPES`). A conversion metric is a
  proportion from `(assigned, converting users)` through `binomial_metric_result`: Fisher's
  exact test always, a normal interval per arm, Cohen's h, observed power. A revenue, count,
  duration or custom metric is a mean per assigned user, zeros included, through
  `per_user_mean_metric_result`: Welch's t on centred sums (the warehouse's own function),
  a t-interval per arm, Cohen's d. Both decide significance at `1 − confidence_level`.
- Every treatment carries its difference (Newcombe for a proportion, Welch for a mean), its
  lift's interval (MOVER-R, Fieller for a mean) and the incremental impact. None is adjusted for
  multiple comparisons, and `is_significant` comes from the test, not the interval.
- `lower_is_better` decides which side of the control a winner is on; `relative_improvement_pct`
  stays the signed change.
- The pooled z-test is used only in `?breakdown=` segments, whose intervals are Wilson's;
  Wilson's limits also build Newcombe's interval. `select_statistical_test` and the other
  `AnalysisService` helpers have no caller.
- Cohen's h (arcsine scale) and d, with negligible/small/medium/large labels, separate "real"
  from "big."
- Conversions are converting users, each counted once; a rate never exceeds 1.
- On zero-inflated revenue a lopsided split names a false winner about three times as often as
  α allows: keep revenue experiments evenly split.

---

## Hour 3 — Power and sample size: what "minimum sample size" protects you from

### What & why

A test has two ways to be wrong. A **Type I error** (false positive) is declaring a
difference that isn't there; the significance level α caps it at 5% per test when α = 0.05 and
the test's assumptions hold. A **Type II error** (false negative) is missing a difference that
is real; **power** = 1 − β is the probability of detecting a real effect of a given size. Power
is what you _buy_ with sample size: for a baseline rate p₁, a minimum detectable effect (MDE),
α and desired power, the required users per arm is a closed-form number.

The reason it matters operationally: **a significant result from an underpowered test is
more likely to be wrong, and more likely to overstate the effect.** With 100 users an arm, a
p < 0.05 outcome is dominated by noise, and the ones that cross the line are the lucky
extremes — which is exactly why a decision made on them regresses next week. The platform's
answer is a **gate**: `SHIP_VARIANT` is recommended only when every variant has reached the
metric's `minimum_sample_size`, whatever the p-value says.

#### The formula

For two proportions, the classic (Fleiss) sample size per arm is

```
n = [ z_{α/2} · √(2·p̄(1−p̄)) + z_power · √(p₁(1−p₁) + p₂(1−p₂)) ]²  /  (p₂ − p₁)²
```

where p₂ = p₁·(1 + MDE) (relative) or p₁ + MDE (absolute), p̄ is their mean, and `z_{α/2}`,
`z_power` are normal quantiles (1.96 and 0.84 for α = 0.05, power = 0.80). Read the
denominator: halve the detectable effect and you need _four times_ the users.

### How Controlvar does it

One function does the planning everywhere the platform plans: `sample_size_two_proportions`
in `power_calculator_service.py`. The results page's Sample Size tab, the guided setup's
estimate and the power calculator all call it, and `compute_power` beside it is its exact
inverse (the power at a given n):

```python
# 📁 backend/app/services/power_calculator_service.py  (docstring abridged)
def sample_size_two_proportions(p1: float, p2: float, alpha: float, power: float, two_tailed: bool) -> int:
    """
    Required sample size per group for a two-proportions z-test.

    The variance under the null hypothesis is pooled (both groups at the
    mean rate ``p_bar``); the variance under the alternative is unpooled
    (each group at its own rate). Formula (Fleiss, 2003):

        n = [z_alpha * sqrt(2 * p_bar * (1 - p_bar))
             + z_power * sqrt(p1*(1-p1) + p2*(1-p2))]^2
            / (p2 - p1)^2
    ...
    It applies no multiple-comparison correction: a caller that wants one
    passes an already-corrected ``alpha``.
    """
```

`PowerCalculatorService._sample_size_proportions(p1, p2, alpha, power, two_tailed)` is a thin
wrapper over it, which is why the Try It below still uses that name. The calculator itself —
relative MDE, Bonferroni for more than two variants (`alpha / (n_variants − 1)`), one- or
two-tailed, plus MDE-from-n, runtime estimates and power curves — is `POST /power/*`, and
needs no login.

`AnalysisService.calculate_required_sample_size(baseline, absolute_mde, alpha, power)` is the
same Fleiss formula with an _absolute_ MDE. No route calls it; it is handy in a shell, and
for the same absolute difference it gives the same n.

The gate — the metric's `minimum_sample_size` (default 100, `ge=10`, `le=INT32_MAX`) on the
schema, and the recommendation logic that honours it:

```python
# 📁 backend/app/schemas/experiment.py
    minimum_sample_size: int = Field(100, ge=10, le=INT32_MAX, description="Minimum sample size for statistical significance")
```

```python
# 📁 backend/app/services/analysis_service.py
    @staticmethod
    def _sample_size_adequate(experiment: Experiment, metrics_results: List[Dict[str, Any]]) -> bool:
        ...
```

and `_summarise_decision`, which only recommends `SHIP_VARIANT` when the primary metric is
significant _and_ every variant has reached the minimum; short of the minimum a significant
winner is reported (`has_winner`) but the recommendation stays `CONTINUE_TESTING`. (A failed SRM
check, Hour 1, can still override either with `INCONCLUSIVE`.)

`GET /results/{id}/sample-size` is the planner behind the dashboard's Sample Size tab:

- **Baseline**: the `baseline_conversion_rate` query parameter if sent, otherwise the
  **control variant's observed rate** on the primary metric (converting users / assigned).
- **`mde`** is **relative** (0.05 means 12% → 12.6%). Omit it and the planner uses the primary
  metric's saved `expected_effect` when that is above 0 and below 1, otherwise 0.05;
  `mde_source` (`request`, `experiment` or `default`) says which it used.
- **`confidence_level`** (0.80–0.99; omitted, it is the experiment's stored level, 0.95 unless
  its creator chose otherwise) and **`power_target`** (0.50–0.99, default 0.80).
- **`correction_method`** (omitted, the experiment's stored method, `benjamini_hochberg`
  unless chosen otherwise, as the results use): `bonferroni` and `benjamini_hochberg` plan
  each comparison at α / (variants − 1); `none` plans at α. The response reports the `alpha`
  and `comparisons` it planned with.
- `current_sample_size_per_variant` is the **smallest** variant's assignment count.
  `days_to_significance` and `projected_completion_date` are always `null`.
- When it cannot plan, `required_sample_size_per_variant` is `null` and
  `unavailable_reason` says why: `no_metric`, `no_control_data`, `no_control_conversions`,
  `rate_at_boundary`, `effect_out_of_range` or `effect_too_small`. A sent baseline that the
  MDE pushes to 100% or more, or a size that is not finite, is refused with 422.
- `guide_only_reasons` lists why a fixed sample size is only a guide for this experiment:
  `adaptive_allocation` (a bandit), `unequal_allocation`, `sequential_testing`, `bayesian`.
- It plans a **proportion** whatever the primary metric's type. For a revenue primary metric
  the baseline is the share of users who sent its event, not its mean per user, so the plan is
  for "did they buy", not "how much". To plan a mean, call `POST /power/sample-size` with
  `metric_type: "mean"` and a `baseline_std`; `POST /power/mde` plans a proportion only.

The dashboard's meter shows `current / required` and turns red below 80%.

> **Watch Out!** `minimum_sample_size` defaults to **100**, which is far below what most
> conversion tests need (a 4% baseline with a 10% relative MDE at 80% power wants 39,475
> per arm). The default keeps demos and small internal tests from being permanently
> "inconclusive"; a real experiment should set it from the planner. The gate only protects
> you if the number in it is honest, and the default does not establish adequate power.

> **Did You Know?** Power calculations assume you'll look **once**, at the planned n. If
> you'll look every day — and everyone does — use the sequential analysis (Part II), whose
> mSPRT stays valid at every look. (Pre-registered alpha-spending looks are the other
> textbook fix; the platform does not compute them — Part II Hour 6 says why.) A
> fixed-horizon power calculation plus daily peeking is the single most common way
> experimentation programs fool themselves.

### Try It Yourself

```python
>>> from backend.app.services.analysis_service import AnalysisService
>>> svc = AnalysisService(db=None)
>>> svc.calculate_required_sample_size(0.04, 0.004)          # 4% → 4.4%: 10% relative MDE
39475
>>> svc.calculate_required_sample_size(0.04, 0.008)          # 20% relative: ~a quarter of the users
10317
>>> svc.calculate_required_sample_size(0.04, 0.004, power=0.9)
52846
>>> from backend.app.services.power_calculator_service import PowerCalculatorService
>>> pc = PowerCalculatorService()
>>> pc._sample_size_proportions(0.04, 0.044, 0.05, 0.80, True), pc._sample_size_proportions(0.04, 0.044, 0.05 / 2, 0.80, True)   # two treatments → Bonferroni
(39475, 47805)
```

Then feel underpowering. Simulate 2,000 experiments per row below, each analysed with a pooled
two-proportion z-test. Run the three calls in this order on one generator, because the numbers
depend on the draw order:

```python
>>> import numpy as np
>>> from scipy import stats
>>> rng = np.random.default_rng(1)
>>> def reject_rate(n, p_c, p_t, runs=2000):
...     a, b = rng.binomial(n, p_c, runs), rng.binomial(n, p_t, runs)
...     pooled = (a + b) / (2 * n)
...     se = np.sqrt(pooled * (1 - pooled) * 2 / n)
...     z = (b / n - a / n) / np.where(se == 0, 1, se)
...     return (2 * stats.norm.sf(abs(z)) < 0.05).mean()
>>> reject_rate(100, 0.04, 0.04), reject_rate(100, 0.04, 0.044), reject_rate(39475, 0.04, 0.044)
(0.0465, 0.042, 0.8005)
```

With **no true effect** the z-test rejects in 0.0465 of runs at n = 100 (a little under 5%: at
this size it is slightly conservative). With a _true_ 10% lift it detects it in 0.042 — no better
than chance, which is the point. At the n the formula gave you (39,475) it detects it in 0.80.

### Key takeaways

- α caps false positives; power = 1 − β is bought with sample size; halving the MDE
  quadruples n.
- One Fleiss function, `sample_size_two_proportions`, behind the Sample Size tab, the guided
  setup and the power calculator; corrections are applied by passing a smaller α.
- `/sample-size` plans from the control's observed rate with a relative MDE (default 5%),
  says when it cannot (`unavailable_reason`) and when its answer is only a guide
  (`guide_only_reasons`). It plans a proportion even for a revenue metric;
  `POST /power/sample-size` with `metric_type: "mean"` plans a mean.
- `minimum_sample_size` on the metric gates `SHIP_VARIANT`; its default (100) is a
  placeholder — set it from the planner.
- Fixed-horizon power assumes one look; daily peeking needs Part II.

---

## Part I Workshop

**Q1. Why is the SRM threshold 0.001 rather than 0.05?**
A warning has to be rare and meaningful, or people learn to ignore it. 0.001 is the
conventional, deliberately strict threshold. (The tempting answer, "at 0.05 a healthy
experiment false-alarms on one refresh in twenty", is wrong: refreshes are correlated.)

**Q2. An SRM warning appears. What's the first thing to check, and what's the wrong
response?**
The plumbing: a targeting rule or SDK path that fails to assign on one platform, a bot on one
URL, a lost-events pipeline. The wrong response is any statistical adjustment — the groups
aren't exchangeable, so no test is valid.

**Q3. Which test produces the `p_value` on a `/results` metric, and does it change with
sample size or metric type?**
Not with sample size; with metric type, yes. A conversion metric gets Fisher's exact test at
every n (`statistical_test_used: "fisher_exact"`): a 2×2 Fisher is cheap and exact at any size.
A revenue, count, duration or custom metric gets Welch's t-test on its mean per assigned user
(`"welch_t_test"`), the same function a warehouse mean run uses. The pooled z-test appears only
in `?breakdown=` segments. `select_statistical_test`, which would choose by sample size, exists
but nothing calls it.

**Q4. Why can the `/results` confidence interval need `max(0, ...)`, and what sets its
width?**
The normal approximation `p ± z·se` can extend below 0 (or above 1) for rates near the
edges; Wilson's interval doesn't. `z` comes from the request's `confidence_level` through
`two_sided_z` (1.645 at 0.90, 1.96 at 0.95, 2.576 at 0.99), the same level that sets the
significance threshold.

**Q5. Two results: 4% → 5% and 40% → 41%. Same absolute lift; which has the larger Cohen's
h and why does it matter?**
4% → 5%: the arcsine transform weights differences by their variance, and a 25% relative
change at a low rate is a bigger effect than a 2.5% relative change at a high rate. The label
(negligible/small/…) tells the reader whether a significant result is worth shipping.

**Q6. What does `minimum_sample_size` gate, and what happens if it's left at the default?**
The `SHIP_VARIANT` recommendation (every variant must reach it). At 100 it barely gates
anything; a real experiment sets it from the planner (`/sample-size` or the power calculator).

**Q7. A revenue experiment split 10/90 recommends `SHIP_VARIANT` for the 90% arm at p = 0.03.
What do you check before believing it?**
The split, first. On zero-inflated, right-skewed revenue a lopsided split names the larger arm
a false winner far more often than α says (150 of 2,000 A/A
experiments at 600/5,400, against about 50 expected). Look at `absolute_difference_ci` and the
lift interval for the size, rerun with an even split if the decision matters, and do not take
the p-value at face value.

**Q8. An error-rate metric falls from 5.2% to 3.0% and `relative_improvement_pct` is −42.3.
Why is that the good news, and what makes the platform treat it as a win?**
The metric's `lower_is_better` is true, so the winner must be significantly _below_ the
control; the signed change stays signed. With the flag left false, the same counts recommend
`KEEP_CONTROL`.

**Quiz.** Halve the MDE. Sample size changes by what factor?
→ ×4 — n scales with 1/(p₂ − p₁)².

---

## Exercises

**E1.1 — SRM by hand.** For observed counts `{control: 4,850, a: 5,150}` at a 50/50 split,
compute Pearson's statistic and the p-value with `scipy.stats.chi2.sf`, then confirm with
`compute_srm`. Would it warn?

**E1.2 — Two tests, one table.** For `[[62, 938], [45, 955]]`, compute Fisher's p and the
pooled z-test p; explain any difference.

**E1.3 — Intervals near the edge.** For 2 conversions in 40, compute the normal
approximation interval and the Wilson interval; state which is usable and why.

**E1.4 — Effect size.** Compute Cohen's h and its label for (0.10, 0.12), (0.30, 0.32),
(0.02, 0.05). Which is "significant but negligible" territory?

**E1.5 — Plan a test.** Baseline 3%, you want to detect a 15% relative lift with 80% power
at α = 0.05, three treatments plus control, 8,000 eligible users a day. Compute users per
arm with Bonferroni and estimate the run time.

**E1.6 — A false winner on revenue.** Using the battery in
`backend/tests/unit/stats_validation/test_false_winner_unequal_split.py`, count how many of
2,000 A/A revenue experiments are recommended `SHIP_VARIANT` and `KEEP_CONTROL` at 600/5,400
and at 3,000/3,000 users, and compare each count with the 29–74 band.

---

## Solutions

**S1.1**

```python
from scipy.stats import chi2
obs, exp = [4850, 5150], [5000, 5000]
stat = sum((o - e) ** 2 / e for o, e in zip(obs, exp))   # 9.0
chi2.sf(stat, 1)                                          # ≈ 0.0027
```

`compute_srm({"control": 4850, "a": 5150}, {"control": 50, "a": 50})` gives the same;
`warning` is `False` (0.0027 > 0.001) — suspicious but below the alarm bar; keep watching.

**S1.2** — Fisher ≈ 0.111; pooled z-test ≈ 0.091. Both above 0.05; the difference is the
z-test's normal approximation vs Fisher's exact enumeration (Fisher is the more
conservative), and the two converge as n grows. `/results` would report the Fisher value.

**S1.3** — Normal: p̂ = 0.05, se = 0.0345, interval ≈ (−0.018, 0.118) — a negative lower
bound for a proportion, which is meaningless (`/results` clamps it to 0). Wilson ≈
(0.014, 0.165) — inside [0, 1], asymmetric, usable.

**S1.4** — (0.10, 0.12): h ≈ 0.064, negligible. (0.30, 0.32): h ≈ 0.043, negligible.
(0.02, 0.05): h ≈ 0.167, still negligible by the 0.2 cut-off but the largest of the three.
With enough users all three can be significant; the label says none of them is _big_ —
which is the normal outcome of a well-powered test on a modest change.

**S1.5** — p₁ = 0.03, p₂ = 0.0345; three comparisons → α = 0.05/3 ≈ 0.0167; `_sample_size_proportions(0.03,
0.0345, 0.05 / 3, 0.80, True)` = 32,270 per arm (24,193 without Bonferroni — the correction
costs a third more users). Four arms → ~129,000 users; at 8,000/day that's ~16 days — plus a
full weekly cycle if day-of-week effects matter, so plan three weeks. On the platform, the
Sample Size tab with `correction_method=bonferroni` and `mde=0.15` plans exactly this
(α / (4 − 1) per comparison).

**S1.6**

```python
from backend.tests.unit.stats_validation.test_false_winner_unequal_split import recommendations
recommendations(600, 5_400, 1_736_001), recommendations(3_000, 3_000, 1_736_002)   # ((150, 12), (46, 45))
```

At 600/5,400 the treatment is recommended in 150 of 2,000 (7.5%, above the band's top of 74)
and the control in 12; at 3,000/3,000, 46 and 45, both inside the band. The band is the central
99.9% of Binomial(2,000, 0.025), one direction at α = 0.05. Each battery is one seeded generator,
so the counts are the same on every run; the test asserts the band before the exact counts, so a
change in numpy's random stream and a change in the method fail differently.

---

**Next:** [Part II — Peeking Safely →](./part-2-sequential.md)

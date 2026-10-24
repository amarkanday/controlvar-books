# Part I — Foundations

### Hours 1–3

[← Back to Contents](./README.md)

A conversion-rate comparison depends on three things: a valid allocation of users, a suitable
statistical test, and enough observations to detect an effect worth acting on. This Part covers
those requirements in order. Hour 1 explains randomisation and the sample-ratio mismatch check.
Hour 2 covers tests and confidence intervals for conversion rates. Hour 3 introduces power and
sample-size planning, including the platform's minimum-sample-size check.

The examples use SciPy. Run them in a Python shell as you work through the calculations.

---

## Hour 1 — Randomisation, exchangeability, and the sample-ratio mismatch check

### What & why

An A/B test assigns units, usually users, to variants through a randomisation procedure. With
valid assignment and no other systematic differences introduced by the experiment, the groups are
comparable in expectation before treatment. This property supports interpreting an outcome
difference as a treatment effect. The implementation uses a deterministic hash of the user's ID
to produce stable allocations.

The statistical comparison depends on that design. A routing defect that sends a particular user
population to one arm, uneven collection loss or an adaptive allocation policy can change the
sampling process. Calculating a p-value does not detect or correct every such problem. Check the
assignment and collection path before interpreting the test.

A sample-ratio mismatch (SRM) check compares observed allocation counts with the configured split.
For example, a 50/50 experiment with counts of 6,043 and 5,712 has assigned 51.4% of 11,755 users
to one arm. A chi-square goodness-of-fit test measures the evidence against the expected ratio.
A result below the platform's 0.001 threshold warrants investigation. Passing this check does not
prove exchangeability; it addresses allocation balance, which is only one requirement.

#### Why p < 0.001, not 0.05

The platform uses a stricter threshold for SRM warnings than the 0.05 level often used for outcome
tests. This reduces warnings caused by ordinary allocation variation. Repeated dashboard refreshes
are correlated observations, so the threshold should not be interpreted as a guarantee about a
fixed number of false alarms per refresh. Investigate a warning in the context of the allocation
and collection process.

### How Experimently does it

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
it's best-effort, returning `None` on any error — because an SRM check that broke the page
would be worse than no check. And it **only runs for fixed-allocation experiments**:

```python
# 📁 backend/app/services/srm_service.py
def _is_fixed_allocation(optimization_type: Any) -> bool:
    """True when an experiment splits traffic by ``traffic_allocation``.

    ``None`` (never set / legacy row) counts as fixed: those experiments are
    served the static split.  Anything else is a bandit.
    """
```

A bandit _intends_ to drift from the configured split (Hour 9), so an SRM warning on a
bandit would be noise; the results endpoint skips it. (That also means bandit results are
_never_ exchangeability-guaranteed — which is exactly the trade Hour 9 explains.)

The result rides on the `/results` response as `srm`:

```python
# 📁 backend/app/api/v1/endpoints/results.py
        # --- P0 statistical credibility: sample-ratio mismatch ---
        srm_response = _compute_srm(experiment_id, db)
```

The core dashboard does **not** render it: no results component reads `srm`, so today an SRM
is visible only to someone reading the API response (or, in the full profile, the warehouse
run results view, which shows the SRM of a warehouse run). Until the results page shows it,
make "check `srm.warning`" part of reading any result you intend to act on.

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
> _tenure_ distributions. The platform's advice: create a new experiment rather than
> re-split a running one.

### Try It Yourself

```python
>>> from backend.app.services.srm_service import compute_srm
>>> compute_srm({"control": 5000, "treatment": 5000}, {"control": 50, "treatment": 50}).p_value        # ≈ 1.0
>>> r = compute_srm({"control": 6043, "treatment": 5712}, {"control": 50, "treatment": 50}); r.chi2, r.p_value, r.warning
(9.32..., 0.00227..., False)
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
  warning at p < 0.001 (strict because polled).
- Best-effort, never fails the request, skipped for bandits, excludes 0% arms.
- It is on the `/results` response (`srm`), not on the core dashboard — read it yourself.
- An SRM warning is a plumbing bug to find, not a statistic to adjust.

---

## Hour 2 — Proportions: Fisher's exact test, the z-test, confidence intervals, effect size

### What & why

For a binary conversion metric, each variant's rate is `converting users / assigned users`.
Several statistical tools can compare these proportions. The platform uses them as follows:

| Question                            | `/results` (every metric)                    | `?breakdown=` segments              |
| ----------------------------------- | -------------------------------------------- | ----------------------------------- |
| Do the rates differ? (p-value)      | **Fisher's exact test**, at every n          | pooled **two-proportion z-test**    |
| How precise is each rate? (CI)      | normal approximation at the requested level  | **Wilson** interval, at the corrected α |
| How big is the difference? (effect) | **Cohen's h**, labelled                      | —                                   |

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
so.

### How Experimently does it

#### One counts-based engine

`/results` does its statistics on counts, not on rows. For each metric it counts, per
variant, two numbers — the users assigned and the users who converted — and hands those
counts to one function, `binomial_metric_result`, in
`backend/app/services/sufficient_stats_analysis.py`. Everything `/results` reports about a
variant follows from `(n, n_converted)`:

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

- **Fisher for every variant of every metric, at every n.** There is no switch to a z-test
  for large samples and no switch to Welch's t for revenue: `statistical_test_used` is
  `"fisher_exact"` on every treatment `/results` returns, whatever the metric's
  `metric_type`. A "revenue" metric on `/results` is analysed as "did the user produce a
  matching event" — a proportion.
- **The interval and the significance decision use the same level.** `confidence_level` on
  the request (default 0.95) sets both `z` (via `two_sided_z`) and `alpha = 1 − level`. Ask
  for 0.90 and the interval narrows to ±1.645·se and significance is p < 0.10. (Before
  issue #454 was fixed the interval was hard-coded to 1.96 whatever you asked for; that fix
  is part of engine version 1.2.0.)
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
for a continuous metric — is used by the results engine in exactly one place: **warehouse
mean metrics** (`mean_metric_result`, full profile; Part III). `/results` never runs it.

> **Watch Out!** The numerator is **converting users**, not events. Every analysis path —
> `/results`, the summary, `/daily`, `/sequential`, `/bayesian`, breakdowns, the bandit
> scheduler's PostgreSQL fallback — counts a user once if they are assigned to the variant
> and have at least one event that matches the metric's event name and is tagged with their
> variant (`count_converting_users` in `backend/app/services/event_matching.py`). A user who
> purchased three times counts once; an event from a user with no assignment to that variant
> does not count; so conversions can never exceed assignments, and the rate is a true
> proportion. `/daily` reports each user on the day of their _first_ conversion, so its
> cumulative series ends at the `/results` count. If you need "purchases per user", that is
> a mean metric, and only a warehouse run analyses means.

> **Did You Know?** A p-value is _not_ "the probability the null is true," and 1 − p is
> _not_ "the probability the treatment is better." It's the probability of data at least
> this extreme _if_ the null were true. The number a product manager actually wants —
> P(treatment > control) — is the Bayesian probability-to-be-best (Part III Hour 8). The
> API returns it (`bayesian_results` on `/results`, and `GET /results/{id}/bayesian`); the
> dashboard does not show it.

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
power at 0.10. Then take a demo experiment's counts from the results API and reproduce
`p_value`, `confidence_interval` and `relative_improvement_pct` for one variant by passing
its `sample_size` and `conversions` to `binomial_metric_result` with
`alpha = 1 − confidence_level`.

### Key takeaways

- `/results` analyses every metric as a proportion from `(assigned, converting users)` with
  one function, `binomial_metric_result`: Fisher's exact test always; normal interval at the
  requested `confidence_level`; significance at `1 − confidence_level`; Cohen's h; observed
  power.
- The pooled z-test and the Wilson interval are used only in `?breakdown=` segments; Welch's
  t only for warehouse mean metrics. `select_statistical_test` and the other `AnalysisService`
  helpers have no caller.
- Cohen's h (arcsine scale) with negligible/small/medium/large labels separates "real"
  from "big."
- Conversions are converting users, each counted once; a rate never exceeds 1.

---

## Hour 3 — Power and sample size: what "minimum sample size" protects you from

### What & why

A Type I error is a false positive: rejecting the null hypothesis when it is true. The
significance level α controls this error rate under the test's assumptions. A Type II error is
failing to detect a specified real difference. Power, 1 − β, is the probability of detecting that
effect. Sample-size planning uses the baseline rate p₁, minimum detectable effect (MDE), α and
target power to estimate the observations needed per arm.

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

### How Experimently does it

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

and `_summarise_decision`, which — since the ShopLab fix — only recommends `SHIP_VARIANT`
when the primary metric is significant _and_ every variant has reached the minimum.

`GET /results/{id}/sample-size` is the planner behind the dashboard's Sample Size tab:

- **Baseline**: the `baseline_conversion_rate` query parameter if sent, otherwise the
  **control variant's observed rate** on the primary metric (converting users / assigned).
- **`mde`** is **relative** (0.05 means 12% → 12.6%), default **0.05**. The metric's
  `expected_effect` is not read.
- **`confidence_level`** (0.80–0.99, default 0.95) and **`power_target`** (0.50–0.99,
  default 0.80).
- **`correction_method`** (`none` by default): `bonferroni` or `benjamini_hochberg` plan
  each comparison at α / (variants − 1); the response reports the `alpha` and `comparisons`
  it planned with.
- `current_sample_size_per_variant` is the **smallest** variant's assignment count.
  `days_to_significance` and `projected_completion_date` are always `null`.
- When it cannot plan, `required_sample_size_per_variant` is `null` and
  `unavailable_reason` says why: `no_metric`, `no_control_data`, `no_control_conversions`,
  `rate_at_boundary`, `effect_out_of_range` or `effect_too_small`. A sent baseline that the
  MDE pushes to 100% or more, or a size that is not finite, is refused with 422.
- `guide_only_reasons` lists why a fixed sample size is only a guide for this experiment:
  `adaptive_allocation` (a bandit), `unequal_allocation`, `sequential_testing`, `bayesian`.

The dashboard's meter shows `current / required` and turns red below 80%.

> **Watch Out!** `minimum_sample_size` defaults to 100. That is enough for small demos but is
> usually too small for a planned conversion-rate test. For example, the planner requires 39,475
> users per arm for a 4% baseline, a 10% relative MDE and 80% power. Set the experiment's threshold
> from its planning assumptions; the default does not establish adequate power.

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

Then feel underpowering: with `default_rng(1)`, simulate 2,000 experiments of 100 users
per arm with **no true effect** (both rates 4%), run the z-test on each, and count how many
have p < 0.05 (0.0385 — at this size the z-test is a little conservative). Now simulate
2,000 with a _true_ 10% lift at n = 100 and count how many detect it — 0.047, no better
than chance. Repeat at the n the formula gave you (39,475): 0.80.

### Key takeaways

- α caps false positives; power = 1 − β is bought with sample size; halving the MDE
  quadruples n.
- One Fleiss function, `sample_size_two_proportions`, behind the Sample Size tab, the guided
  setup and the power calculator; corrections are applied by passing a smaller α.
- `/sample-size` plans from the control's observed rate with a relative MDE (default 5%),
  says when it cannot (`unavailable_reason`) and when its answer is only a guide
  (`guide_only_reasons`).
- `minimum_sample_size` on the metric gates `SHIP_VARIANT`; its default (100) is a
  placeholder — set it from the planner.
- Fixed-horizon power assumes one look; daily peeking needs Part II.

---

## Part I Workshop

**Q1. Why is the SRM threshold 0.001 rather than 0.05?**
The dashboard is polled many times; at 0.05 a healthy experiment would false-alarm on one
in twenty refreshes. 0.001 keeps the warning rare and meaningful.

**Q2. An SRM warning appears. What's the first thing to check, and what's the wrong
response?**
The plumbing: a targeting rule or SDK path that fails to assign on one platform, a bot on one
URL, a lost-events pipeline. The wrong response is any statistical adjustment — the groups
aren't exchangeable, so no test is valid.

**Q3. Which test produces the `p_value` on a `/results` metric, and does it change with
sample size or metric type?**
Fisher's exact test, always: every metric is analysed as converting users over assigned
users, at every n, and `statistical_test_used` is `fisher_exact`. A 2×2 Fisher is cheap and
exact at any size. The pooled z-test appears only in `?breakdown=` segments, and Welch's t
only for warehouse mean metrics. `select_statistical_test` exists but nothing calls it.

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

---

**Next:** [Part II — Peeking Safely →](./part-2-sequential.md)

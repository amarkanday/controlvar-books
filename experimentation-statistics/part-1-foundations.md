# Part I — Foundations

### Hours 1–3

[← Back to Contents](./README.md)

Three hours on the bedrock. Why randomisation is the whole point and how the platform
checks that it actually happened (Hour 1); the tests and intervals behind a conversion-rate
comparison and the labels the dashboard puts on them (Hour 2); and power — the number that
tells you how many users a decision needs and the gate that stops a lucky early result from
shipping (Hour 3). Keep a Python shell with `scipy` open; every formula here is one line away
from a number.

---

## Hour 1 — Randomisation, exchangeability, and the sample-ratio mismatch check

### What & why

An A/B test is a **randomised controlled experiment**: users are assigned to variants by a
process that has nothing to do with them — a hash of their id — so the groups are, in
expectation, identical in every way except the treatment. That property is
**exchangeability**, and it's what turns a difference in conversion rates into evidence about
the treatment rather than about who happened to be in which group.

Everything downstream assumes it. The z-test assumes the two samples are draws from the
same population under the null. The confidence interval assumes the only systematic
difference is the variant. If exchangeability fails — because a bug assigns iOS users to
control, because a bot hammers one URL, because a bandit deliberately routes better users
to the better arm — the p-value is still computed, still small or large, and means nothing.

You can't observe exchangeability directly. You can observe its most common _symptom_: a
**sample-ratio mismatch (SRM)**. The experiment was configured 50/50; the assignment counts
are 6,043 vs 5,712. Under a working randomiser, that's a Bernoulli coin flipped 11,755
times landing on one side 51.4% of the time — a chi-square goodness-of-fit test says how
surprising that is. If it's very surprising (p < 0.001), the randomiser isn't doing what you
think, and nothing else on the page can be trusted until you know why.

#### Why p < 0.001, not 0.05

Because the dashboard is polled constantly. An SRM check that fires at p < 0.05 would raise
a false alarm on one in twenty refreshes of a perfectly healthy experiment. The conventional
threshold in industry (Microsoft, Booking, Optimizely all use ~0.001) is deliberately
strict: an SRM warning should be rare and should always mean something.

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

The result rides on the response and the dashboard renders it above everything else:

```python
# 📁 backend/app/api/v1/endpoints/results.py
        # --- P0 statistical credibility: sample-ratio mismatch ---
        srm_response = _compute_srm(experiment_id, db)
```

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
>>> compute_srm({"control": 5100, "treatment": 4900}, {"control": 50, "treatment": 50}).p_value        # ≈ 0.046 — not a warning at 0.001
>>> compute_srm({"a": 100}, {"a": 100})                                                                 # None: one arm, undefined
>>> compute_srm({"a": 7000, "b": 3000}, {"a": 70, "b": 30}).warning                                     # False: matches the configured split
```

Then simulate the false-alarm rate: draw 10,000 fair 50/50 splits of 10,000 users with
`numpy.random.default_rng(0).binomial(10000, 0.5, 10000)` and count how many would warn at
0.05 vs 0.001.

### Key takeaways

- Randomisation → exchangeability → every statistic's licence to speak. Break it and the
  numbers are arithmetic on a lie.
- SRM = Pearson chi-square of observed assignment counts vs `traffic_allocation`, k − 1 dof,
  warning at p < 0.001 (strict because polled).
- Best-effort, never fails the request, skipped for bandits, excludes 0% arms.
- An SRM warning is a plumbing bug to find, not a statistic to adjust.

---

## Hour 2 — Proportions: the z-test, Fisher's exact test, confidence intervals, effect size

### What & why

A conversion metric is a **proportion**: `conversions / assigned` per variant. Comparing two
proportions is the oldest question in statistics, and the platform uses two tests and two
intervals, choosing by sample size:

| Question                              | Small n (< 30 per arm)        | Large n                          |
| ------------------------------------- | ----------------------------- | -------------------------------- |
| Do the rates differ? (p-value)        | **Fisher's exact test**       | **two-proportion z-test** (pooled) |
| How precise is each rate? (CI)        | **Wilson** score interval     | normal approximation (`p ± 1.96·se`) |
| How big is the difference? (effect)   | **Cohen's h**, labelled       | same                             |

**Fisher's exact test** computes the exact probability of the observed 2×2 table (and all
more extreme tables) under the null of equal rates — no approximation, correct at any size,
expensive only for huge counts. The **z-test** approximates the difference in rates as
normal with a pooled standard error — cheap and accurate once counts are large. The
results engine's primary path uses Fisher always (it's cheap enough on a 2×2 table); the
enhanced path picks by `select_statistical_test`.

A **confidence interval** is the range of true rates consistent with the data at a
confidence level; the normal approximation `p̂ ± z·√(p̂(1−p̂)/n)` is fine in the middle and
misbehaves near 0 or 1 (it can go negative). **Wilson's** interval fixes that by solving the
inequality properly — it stays in [0, 1] and has better coverage for rare events.

**Effect size** answers "is it _big_?" separately from "is it _real_?". **Cohen's h** =
2·arcsin√p₂ − 2·arcsin√p₁ puts a difference in proportions on a scale where 0.2/0.5/0.8 mean
small/medium/large regardless of the base rate. A significant-but-negligible result is the
most common outcome of a well-powered test, and the label is what lets the dashboard say so.

### How Experimently does it

The selection rule:

```python
# 📁 backend/app/services/analysis_service.py
    def select_statistical_test(self, metric_type: str, control_size: int, treatment_size: int) -> str:
        """Select the most appropriate statistical test.

        Returns 'fisher_exact' for small samples (n < 30), 'z_test' for
        conversion metrics with large samples, and 'welch_t_test' for
        continuous metrics (revenue, duration, count, custom).
        """
        if min(control_size, treatment_size) < 30:
            return "fisher_exact"
        if metric_type == "conversion":
            return "z_test"
        return "welch_t_test"
```

Fisher on the primary path — the contingency table is `[[treatment conv, treatment
non-conv], [control conv, control non-conv]]`, significance at 0.05, relative improvement vs
control:

```python
# 📁 backend/app/services/analysis_service.py
                # Create contingency table
                contingency_table = [
                    [variant_conversions, variant_non_conversions],
                    [control_conversions, control_non_conversions],
                ]

                # Run Fisher's exact test
                try:
                    odds_ratio, p_value = stats.fisher_exact(contingency_table)
                    is_significant = p_value < 0.05  # Using 95% confidence level

                    # Calculate relative improvement
                    if rates[str(control_variant.id)] > 0:
                        relative_improvement = ((rates[variant_id] - rates[str(control_variant.id)]) / rates[str(control_variant.id)]) * 100
```

The pooled two-proportion z-test, with the formula in the docstring:

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
probability of a difference at least this large in _either_ direction. Every p-value is
clamped to `[0, 1]` before it leaves.

The two intervals:

```python
# 📁 backend/app/services/analysis_service.py
            # Calculate confidence interval using normal approximation
            if assignments[variant_id] > 0:
                proportion = rates[variant_id] / 100  # Convert percentage to proportion
                z = 1.96  # For 95% confidence level

                # Standard error of proportion
                se = math.sqrt((proportion * (1 - proportion)) / assignments[variant_id])

                # Confidence interval
                ci_lower = max(0, (proportion - z * se) * 100)
                ci_upper = min(100, (proportion + z * se) * 100)
```

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

(The primary path's normal interval hard-codes `z = 1.96`; the endpoint's
`confidence_level` parameter flows into the enhanced path's Wilson interval via
`stats.norm.ppf`. The clamp `max(0, ...)` on the normal interval is the tell that it can go
negative.)

Effect size, with the labels the dashboard shows:

```python
# 📁 backend/app/services/analysis_service.py
    def cohens_h(self, p1: float, p2: float) -> Tuple[float, str]:
        """Cohen's h effect size for two proportions.

        h = 2 * arcsin(sqrt(p2)) - 2 * arcsin(sqrt(p1))

        Positive h means p2 > p1 (treatment outperforms control when called
        as cohens_h(p_control, p_treatment)).

        Labels (EP-016): negligible < 0.2, small < 0.5, medium < 0.8, large >= 0.8
        """
        phi1 = 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, p1))))
        phi2 = 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, p2))))
        h = phi2 - phi1
        label = self._effect_size_label(abs(h))
        return (h, label)

    @staticmethod
    def _effect_size_label(abs_effect: float) -> str:
        """Map absolute effect size to a human-readable label (EP-016 thresholds)."""
        if abs_effect < 0.2:
            return "negligible"
        elif abs_effect < 0.5:
            return "small"
        elif abs_effect < 0.8:
            return "medium"
        else:
            return "large"
```

The arcsine transform is why a 4% → 5% lift and a 40% → 41% lift get _different_ effect
sizes even though both are "one point": the first is a 25% relative change at a rate where
the variance is small; the second is a 2.5% relative change. Cohen's h sees that; a raw
difference doesn't.

For continuous metrics (revenue, duration), Welch's t-test doesn't assume equal variances
between arms — the right default when a treatment changes not just the mean but the spread:

```python
# 📁 backend/app/services/analysis_service.py
    def welch_t_test(self, control_values: List[float], treatment_values: List[float]) -> float:
        """Welch's t-test for two independent groups (unequal variances).

        Returns the two-tailed p-value. Returns NaN when the within-group
        variance is zero (identical data), which callers should handle.
        """
        result = stats.ttest_ind(control_values, treatment_values, equal_var=False)
        return float(result.pvalue)
```

> **Watch Out!** The primary path's conversion count is `COUNT(events)`, not
> `COUNT(DISTINCT user_id)`. For a repeatable event (two purchases by one user) the
> "proportion" is really events-per-assigned-user and can exceed 1. The streaming results
> service counts distinct users; when the two disagree, that's why. For a conversion metric,
> distinct users is the right denominator match; for a count metric, events is.

> **Did You Know?** A p-value is _not_ "the probability the null is true," and 1 − p is
> _not_ "the probability the treatment is better." It's the probability of data at least
> this extreme _if_ the null were true. The number a product manager actually wants —
> P(treatment > control) — is the Bayesian block's probability-to-be-best (Hour 8). The
> dashboard shows both for exactly this reason.

### Try It Yourself

```python
>>> from scipy import stats
>>> import math
>>> from backend.app.services.analysis_service import AnalysisService
>>> svc = AnalysisService(db=None)
>>> table = [[52, 948], [40, 960]]                       # treatment 5.2%, control 4.0%
>>> stats.fisher_exact(table)
>>> svc.z_test_proportions(40, 1000, 52, 1000)           # ≈ 0.20
>>> svc.wilson_confidence_interval(52, 1000), svc.wilson_confidence_interval(1, 20)
>>> p = 1/20; (p - 1.96*math.sqrt(p*(1-p)/20), p + 1.96*math.sqrt(p*(1-p)/20))   # normal: negative lower bound
>>> svc.cohens_h(0.04, 0.05), svc.cohens_h(0.40, 0.41)   # same "one point", different h
>>> svc.z_test_proportions(400, 10000, 520, 10000)       # ten times the data: ≈ 0.00006
```

Then take a demo experiment's counts from the results API and reproduce `p_value`,
`confidence_interval` and `relative_improvement` for one variant to three decimals.

### Key takeaways

- Proportions: Fisher (exact, small n) or pooled z-test (large n); Welch's t for continuous
  metrics; two-tailed; clamped.
- Intervals: normal approximation on the primary path (can go negative — clamped), Wilson
  where extreme rates matter.
- Cohen's h (arcsine scale) with negligible/small/medium/large labels separates "real"
  from "big."
- Count events vs distinct users — know which denominator you're reading.

---

## Hour 3 — Power and sample size: what "minimum sample size" protects you from

### What & why

A test has two ways to be wrong. A **Type I error** (false positive) is declaring a
difference that isn't there; α = 0.05 caps it at 5% per test. A **Type II error** (false
negative) is missing a difference that is; **power** = 1 − β is the probability of
detecting a real effect of a given size. Power is what you _buy_ with sample size: for a
baseline rate p₁, a minimum detectable effect (MDE), α and desired power, the required
users per arm is a closed-form number.

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

The results engine's version — absolute MDE, used by the sample-size meter:

```python
# 📁 backend/app/services/analysis_service.py
    def calculate_required_sample_size(self, baseline_rate: float, minimum_detectable_effect: float, alpha: float = 0.05, power: float = 0.80) -> int:
        """Calculate the required sample size per variant (two-proportion z-test).

        Uses the two-sided formula from EP-016:
            n = (z_alpha * sqrt(2*p_bar*(1-p_bar)) + z_beta * sqrt(p1*(1-p1) + p2*(1-p2)))^2
                / (p2 - p1)^2
        """
        p1 = baseline_rate
        p2 = p1 + minimum_detectable_effect
        p2 = min(max(p2, 1e-9), 1.0 - 1e-9)
        p_bar = (p1 + p2) / 2.0
        z_alpha = float(stats.norm.ppf(1.0 - alpha / 2.0))
        z_beta = float(stats.norm.ppf(power))
        numerator = (z_alpha * math.sqrt(2.0 * p_bar * (1.0 - p_bar)) + z_beta * math.sqrt(p1 * (1.0 - p1) + p2 * (1.0 - p2))) ** 2
        denominator = (p2 - p1) ** 2
        if denominator == 0.0:
            return 1
        n = math.ceil(numerator / denominator)
        return max(1, n)
```

The power calculator's version — relative MDE, Bonferroni for multiple variants, one- or
two-tailed, plus MDE-from-n, runtime estimates and power curves (`POST /power/*`, no login):

```python
# 📁 backend/app/services/power_calculator_service.py
    def compute_sample_size(self, baseline_rate: float, minimum_detectable_effect: float, alpha: float = 0.05, power: float = 0.80,
                            n_variants: int = 2, two_tailed: bool = True, metric_type: str = "proportion", ...) -> SampleSizeResult:
        """
        Compute the required sample size per variant.
        ...
        n_variants:
            Total number of variants including control (>= 2).
            When > 2, Bonferroni correction is applied (alpha / (n_variants - 1)).
        two_tailed:
            Use a two-tailed test (recommended; reduces required power by ~20%
            for one-tailed).
        """
    ...
    @staticmethod
    def _sample_size_proportions(p1: float, p2: float, alpha: float, power: float, two_tailed: bool) -> int:
        """
        Compute the required sample size per group for a two-proportions
        z-test using the exact unpooled formula.

        Formula (Fleiss, 2003):
            n = [z_alpha * sqrt(2 * p_bar * (1 - p_bar))
                 + z_power * sqrt(p1*(1-p1) + p2*(1-p2))]^2
                / (p2 - p1)^2
        """
        z_alpha = norm.ppf(1 - alpha / (2 if two_tailed else 1))
        z_power = norm.ppf(power)
        pooled = (p1 + p2) / 2
        delta = abs(p2 - p1)
        if delta == 0:
            raise ValueError("p1 and p2 must differ (delta cannot be zero)")
        n = (z_alpha * math.sqrt(2 * pooled * (1 - pooled)) + z_power * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / delta ** 2
        return math.ceil(n)
```

Bonferroni for `n_variants > 2` (Hour 10): three treatments against one control is three
comparisons, so α is split three ways and the required n rises.

The gate — the metric's `minimum_sample_size` (default 100, `ge=10`) on the schema, and the
recommendation logic that honours it:

```python
# 📁 backend/app/schemas/experiment.py
    minimum_sample_size: int = Field(100, ge=10, description="Minimum sample size for statistical significance")
```

```python
# 📁 backend/app/services/analysis_service.py
    @staticmethod
    def _sample_size_adequate(experiment: Experiment, metrics_results: List[Dict[str, Any]]) -> bool:
        ...
```

and `_summarise_decision`, which — since the ShopLab fix — only recommends `SHIP_VARIANT`
when the primary metric is significant _and_ every variant has reached the minimum. The
dashboard's sample-size meter shows `current / required` and "days to significance" at the
current rate, and turns red below 80%.

The results endpoint's `/sample-size` companion computes the requirement from the observed
baseline and the metric's `expected_effect` (or a supplied `mde`):

```python
# 📁 backend/app/api/v1/endpoints/results.py
@router.get("/{experiment_id}/sample-size", response_model=SampleSizeResult)
def get_sample_size_status(...)
```

> **Watch Out!** `minimum_sample_size` defaults to **100**, which is far below what most
> conversion tests need (a 4% baseline with a 10% relative MDE at 80% power wants ~40,000
> per arm). The default keeps demos and small internal tests from being permanently
> "inconclusive"; a real experiment should set it from the power calculator. The gate only
> protects you if the number in it is honest.

> **Did You Know?** Power calculations assume you'll look **once**, at the planned n. If
> you'll look every day — and everyone does — either use the sequential methods (Part II),
> which spend α across looks, or pre-register the planned looks with alpha spending. A
> fixed-horizon power calculation plus daily peeking is the single most common way
> experimentation programs fool themselves; the platform offers both fixes and the dashboard
> shows them side by side.

### Try It Yourself

```python
>>> from backend.app.services.analysis_service import AnalysisService
>>> svc = AnalysisService(db=None)
>>> svc.calculate_required_sample_size(0.04, 0.004)          # 4% → 4.4%: ~10% relative MDE
>>> svc.calculate_required_sample_size(0.04, 0.008)          # 20% relative: ~a quarter of the users
>>> svc.calculate_required_sample_size(0.04, 0.004, power=0.9)
>>> from backend.app.services.power_calculator_service import PowerCalculatorService
>>> pc = PowerCalculatorService()
>>> pc._sample_size_proportions(0.04, 0.044, 0.05, 0.80, True), pc._sample_size_proportions(0.04, 0.044, 0.05 / 2, 0.80, True)   # two treatments → Bonferroni
```

Then feel underpowering: with `default_rng(1)`, simulate 2,000 experiments of 100 users
per arm with **no true effect** (both rates 4%), run the z-test on each, and count how many
have p < 0.05 (≈ 5% — the α you paid for). Now simulate 2,000 with a _true_ 10% lift at n =
100 and count how many detect it — that's your power, and it's tiny. Repeat at the n the
formula gave you.

### Key takeaways

- α caps false positives; power = 1 − β is bought with sample size; halving the MDE
  quadruples n.
- Fleiss formula per arm; Bonferroni across treatments; the power calculator exposes it
  (`/power/sample-size`, `/power/mde`).
- `minimum_sample_size` on the metric gates `SHIP_VARIANT`; its default (100) is a
  placeholder — set it from the calculator.
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

**Q3. When does the enhanced path use Fisher's exact test, and why not always?**
When either arm has fewer than 30 users. Fisher is exact at any size but the z-test is
cheaper and equivalent at scale; the primary path uses Fisher regardless because a 2×2 is
cheap.

**Q4. Why can the primary path's confidence interval need `max(0, ...)`?**
The normal approximation `p ± 1.96·se` can extend below 0 (or above 1) for rates near the
edges; Wilson's interval doesn't.

**Q5. Two results: 4% → 5% and 40% → 41%. Same absolute lift; which has the larger Cohen's
h and why does it matter?**
4% → 5%: the arcsine transform weights differences by their variance, and a 25% relative
change at a low rate is a bigger effect than a 2.5% relative change at a high rate. The label
(negligible/small/…) tells the reader whether a significant result is worth shipping.

**Q6. What does `minimum_sample_size` gate, and what happens if it's left at the default?**
The `SHIP_VARIANT` recommendation (every variant must reach it). At 100 it barely gates
anything; a real experiment sets it from the power calculator.

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

**S1.2** — Fisher ≈ 0.11; z-test ≈ 0.10. Both above 0.05; the small difference is the
z-test's normal approximation vs Fisher's exact enumeration, which converge as n grows.

**S1.3** — Normal: p̂ = 0.05, se = 0.0345, interval ≈ (−0.018, 0.118) — a negative lower
bound for a proportion, which is meaningless (the primary path clamps it to 0). Wilson ≈
(0.014, 0.165) — inside [0, 1], asymmetric, usable.

**S1.4** — (0.10, 0.12): h ≈ 0.064, negligible. (0.30, 0.32): h ≈ 0.043, negligible.
(0.02, 0.05): h ≈ 0.164, still negligible by the 0.2 cut-off but the largest of the three.
With enough users all three can be significant; the label says none of them is _big_ —
which is the normal outcome of a well-powered test on a modest change.

**S1.5** — p₁ = 0.03, p₂ = 0.0345; three comparisons → α = 0.05/3 ≈ 0.0167; `_sample_size_proportions(0.03,
0.0345, 0.0167, 0.80, True)` ≈ 33,000 per arm (≈ 29,000 without Bonferroni). Four arms →
~132,000 users; at 8,000/day that's ~17 days — plus a full weekly cycle if day-of-week
effects matter, so plan three weeks.

---

**Next:** [Part II — Peeking Safely →](./part-2-sequential.md)

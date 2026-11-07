# Part II — Peeking Safely

### Hours 4–6

[← Part I](./part-1-foundations.md) · [Contents](./README.md) · [Part III →](./part-3-sharper-estimates.md)

Everyone looks at results before the planned end. This Part is about why that quietly
destroys the guarantees of Part I (Hour 4), the one method the platform uses to make looking
safe — the mixture sequential probability ratio test (mSPRT), an evidence ratio you may inspect
at any moment (Hour 5) — and its companion, a confidence sequence that is the same test turned
inside out, so the interval and the stop decision can never disagree (Hour 6). Hour 6 also
covers the alpha-spending boundaries the platform used to report and no longer does, and
why. By the end you'll know what `can_stop`, `always_valid_p_value`, `evidence_strength`,
`recommended_action`, `at_risk` and the `beta` notice on the Sequential tab actually promise.

> **Status.** The sequential analysis is **beta**: `GET /results/{id}/sequential` answers
> `analysis_status: "beta"` and an `analysis_notice` saying that the stop/continue decision
> is the mSPRT alone and that `alpha_spending` is always empty (issue #232). The mSPRT, its
> always-valid p-value and the confidence sequence are computed exactly as this Part
> describes; "beta" is about what is *not* there yet (a planned-looks mode).

---

## Hour 4 — The peeking problem: why looking every day inflates false positives

### What & why

A fixed-horizon test promises: _if there's no effect, a p-value below 0.05 happens 5% of the
time_. The promise holds for **one** look at the pre-planned sample size. Look every day
and stop the first time p dips below 0.05, and the promise breaks: under the null, the
running p-value wanders, and over 20 daily looks the chance that it dips below 0.05 _at
least once_ is not 5% but about 25% (the Try It below measures 0.247). With unlimited looks
it is certain — a random walk crosses any fixed line eventually.

This isn't a subtle effect. It's the single most common way an experimentation program
"finds" wins that don't replicate, and it happens without anyone doing anything that feels
wrong: they just checked the dashboard. Note where the fault lies: each individual p-value is
calculated correctly; it is the _stopping rule_ that changed the question.

Three ways out:

1. **Don't peek** (or peek and don't act). Culturally fragile.
2. **Pre-plan the looks and spend α across them** — group sequential designs with alpha-
   spending functions (O'Brien–Fleming, Pocock). Valid for the planned schedule, _if_ the
   boundaries are computed properly and the looks are counted. **The platform does not
   offer this.** It used to report a boundary table; the table did not hold its stated
   level and nothing counted the looks, so it was removed (Hour 6).
3. **Use a test that's valid at every look** — a sequential test whose error guarantee holds
   for any stopping rule. The mixture SPRT (Hour 5) is the platform's only sequential
   method, and its `always_valid_p_value` is the number to act on.

#### What "always valid" means

A p-value process p_t is _always valid_ if, under the null, P(∃t: p_t ≤ α) ≤ α. Read it
slowly: the probability that the p-value _ever_ crosses α — at any time, under any peeking
schedule you like — is at most α. That's a strictly stronger guarantee than the fixed-horizon
one, and it's achieved by making the p-value _more conservative_ (larger) than the classical
one at any single look. You pay a bit of power at a fixed n for the freedom to look whenever.

### How Controlvar does it

The experiment opts in with `sequential_testing_enabled: true` and a configuration,
validated on the schema:

```python
# 📁 backend/app/schemas/experiment.py  (descriptions abridged)
class SequentialTestingConfigInput(BaseModel):
    """Configuration for enabling sequential testing on an experiment."""

    method: str = Field(default="msprt", pattern="^(msprt|always_valid)$",
        description="Sequential testing method: 'msprt'. 'always_valid' is accepted as an alias of it; the analysis reports 'msprt'.")
    alpha: float = Field(default=0.05, gt=0.0, le=0.2,
        description="Significance level of the mSPRT, above 0 and at most 0.2; the stopping boundary is 1/alpha.")
    tau_squared: float = Field(default=0.001, gt=0.0, le=1.0, description="mSPRT mixing parameter.")
    spending_function: str = Field(default="obrien_fleming", pattern="^(obrien_fleming|pocock)$",
        description="Alpha spending function. Accepted and stored, but no alpha-spending table is computed yet.")
    planned_looks: int = Field(default=10, ge=1, le=100,
        description="Number of planned interim analyses. Accepted and stored, but no alpha-spending table is computed yet.")
```

Four things to read off it:

- **`alpha`** is the one significance knob, from just above 0 to 0.2, default 0.05. The
  stopping boundary is `1/alpha` (20 at the default, 10 at 0.10). A request can override it
  for one read: `GET /results/{id}/sequential?alpha=0.1` (same range). A stored value
  outside (0, 0.2] — which the experiments API cannot write, but some other path could —
  is not used silently: the route falls back to 0.05 and says so in the notice.
- **`method: "always_valid"` is an alias**, not a second method. It is accepted so old
  configurations still validate; the analysis runs the mSPRT, reports `method: "msprt"`,
  and appends a sentence saying so to `analysis_notice`.
- **`spending_function` and `planned_looks` are stored and ignored.** They are kept in the
  schema so existing configurations stay valid; nothing reads them.
- **`tau_squared`** is the mixing variance of Hour 5.

The enums still name more than the analysis uses:

```python
# 📁 backend/app/services/sequential_testing_service.py
class SequentialTestingMethod(str, Enum):
    MSPRT = "msprt"
    ALWAYS_VALID = "always_valid"


class SpendingFunction(str, Enum):
    OBRIEN_FLEMING = "obrien_fleming"
    POCOCK = "pocock"


class EvidenceStrength(str, Enum):
    STRONG_FOR_EFFECT = "strong_for_effect"
    MODERATE_FOR_EFFECT = "moderate_for_effect"
    INCONCLUSIVE = "inconclusive"
    MODERATE_FOR_NULL = "moderate_for_null"
    STRONG_FOR_NULL = "strong_for_null"
```

`SpendingFunction` survives only so the stored strings have a name; no code consumes it.

The sequential block is _additional_: the primary fixed-horizon result stays on the results
page, and the Sequential tab shows the mSPRT verdict. That's deliberate — the two answer
different questions, and showing both makes the peeking trade visible rather than hidden.

> **Watch Out!** The peeking problem applies to the **primary** p-value on the results
> page. If sequential testing isn't enabled and you refresh daily, the primary p-value is the
> classical one and its line means less every day you look. Enable sequential testing on any
> experiment you'll monitor — which is every experiment — and act on the Sequential tab's
> verdict.

> **Did You Know?** The mSPRT was popularised for A/B testing by Optimizely's "Stats Engine"
> (Johari, Pekelis, Walsh, 2015/2017) precisely because their customers were peeking and
> their fixed-horizon p-values were producing false wins at rates that embarrassed everyone.
> The platform's implementation follows that paper's Gaussian-mixture form.

### Try It Yourself

Simulate peeking under the null and watch the false-positive rate climb:

```python
import numpy as np
from scipy import stats
rng = np.random.default_rng(0)
def ever_significant(n_looks=20, per_look=500, p=0.04):
    c = t = 0; nc = nt = 0
    for _ in range(n_looks):
        c += rng.binomial(per_look, p); t += rng.binomial(per_look, p); nc += per_look; nt += per_look
        pooled = (c + t) / (nc + nt); se = (pooled * (1 - pooled) * (1/nc + 1/nt)) ** 0.5
        if se and 2 * (1 - stats.norm.cdf(abs((t/nt - c/nc) / se))) < 0.05:
            return True
    return False
sum(ever_significant() for _ in range(2000)) / 2000      # 0.247, not 0.05
```

Then change `n_looks` to 1 and confirm ≈ 0.05 (0.049 continuing the same generator).

### Key takeaways

- A fixed-horizon p-value's 5% promise holds for one planned look; 20 daily peeks with
  stop-at-first-p<0.05 inflate it to about 25%.
- Always-valid: P(ever p_t ≤ α) ≤ α under the null, for any stopping rule; more
  conservative per look, valid forever.
- The platform has one sequential method, the mSPRT, at a configurable `alpha` (≤ 0.2,
  default 0.05, overridable per request). `always_valid` is an alias for it;
  `spending_function` and `planned_looks` are stored and ignored. The route is beta.

---

## Hour 5 — The mixture SPRT: an evidence ratio you can watch continuously

### What & why

The **sequential probability ratio test** (Wald, 1945) accumulates a likelihood ratio —
how much more likely the data are under the alternative than under the null — and stops when
it crosses a boundary. The trouble for A/B tests is that the "alternative" needs a specific
effect size, and you don't know it. The **mixture** SPRT (mSPRT) fixes that by averaging the
likelihood ratio over a prior distribution of effect sizes — a Gaussian centred at zero with
variance τ² — so no single alternative has to be chosen.

For a difference in proportions δ̂ = p_t − p_c with variance V_n (below, the Agresti–Caffo
variance), the mixture likelihood ratio has a closed form:

```
Λ_n = √( V_n / (V_n + τ²) ) · exp( τ² · Z_n² / (2 · (V_n + τ²)) ),   Z_n = δ̂ / √V_n
```

Two properties make it a test you can watch:

1. **Λ_n is a nonnegative martingale under the null** (its expected next value is its
   current value), so by Ville's inequality P(∃n: Λ_n ≥ 1/α) ≤ α. Stop when Λ_n ≥ 1/α and
   you've controlled the false-positive rate _at every look_.
2. **`min(1, 1/Λ_n)` is an always-valid p-value.** It never goes below α before Λ crosses
   1/α, and once it does you may stop.

τ² is the one shape knob: the variance of the prior on effect sizes. Small τ² concentrates
the mixture near zero — more power for small effects, slower to declare large ones; large τ²
the reverse. `0.001` (a standard deviation of ~3 percentage points on a difference of
proportions) is the default for conversion metrics.

### How Controlvar does it

The core computation — quoted in full because the formula in the comment _is_ the method:

```python
# 📁 backend/app/services/sequential_testing_service.py  (docstring abridged)
    def compute_msprt(self, control_successes: int, control_total: int, treatment_successes: int, treatment_total: int,
                      tau_squared: float = 0.001, alpha: float = 0.05) -> MSPRTResult:
        """
        Compute the mixture Sequential Probability Ratio Test (mSPRT).
        ...
        """
        boundary = 1.0 / alpha

        # Edge cases: insufficient data
        if control_total == 0 or treatment_total == 0:
            return MSPRTResult(lambda_ratio=1.0, always_valid_p_value=1.0, can_stop=False,
                               evidence_strength=EvidenceStrength.INCONCLUSIVE, boundary=boundary)

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
        ratio = V_n / (V_n + tau_squared)
        log_lambda = 0.5 * math.log(ratio) + tau_squared * Z_n**2 / (2.0 * (V_n + tau_squared))
        lambda_ratio = math.exp(min(log_lambda, _LOG_FLOAT_MAX))

        can_stop = lambda_ratio >= boundary
        always_valid_p = min(1.0, 1.0 / lambda_ratio)

        evidence_strength = self._classify_evidence(lambda_ratio, boundary)

        return MSPRTResult(lambda_ratio=lambda_ratio, always_valid_p_value=always_valid_p, can_stop=can_stop,
                           evidence_strength=evidence_strength, boundary=boundary)
```

Two details in that code deserve a pause, because both exist to stop the method misbehaving.

**The variance is Agresti–Caffo, not the plug-in.** The obvious V is `p_c(1−p_c)/n_c + p_t(1−p_t)/n_t`.
It is wrong exactly where experiments are fragile: with a small, low-rate arm it is too small, and
with zero conversions in an arm it is _zero_. A V that is too small makes Z too large: in a
simulation of 500 A/A experiments (9:1 split, 1% rate, 40 looks of 500 users; E2.6 reproduces
it) the plug-in version told 25.6% of them to stop for an effect that was not there, against 3.8%
with the variance the platform now uses. The fix was `difference_variance`: add one success and one failure to each arm, `p̃ = (x + 1)/(n + 2)`,
and take `p̃_c(1−p̃_c)/(n_c + 2) + p̃_t(1−p̃_t)/(n_t + 2)`. It is positive for any counts with n > 0,
so the "degenerate case" branch the method used to need is gone: two arms with no conversions are
now a (strong) piece of evidence for the null rather than an error. The centre of everything
stays the _observed_ difference; only the variance is adjusted.

**Λ is computed in log space and capped.** For an overwhelming difference the exponential does
not fit in a float. The code works with `log_lambda` and caps it at the log of the largest finite
double, so the ratio always serialises as a finite JSON number. The cap cuts nothing that was
finite before, and the capped value is still above `1/alpha` for any usable `alpha`, so it can
still stop.

Read the shape of Λ. With no difference (`Z_n = 0`) it's `√(V_n/(V_n+τ²)) < 1` — evidence
slightly _for_ the null, because the mixture spreads probability over alternatives that
predicted a difference and none appeared. As |Z_n| grows the exponential dominates and Λ
climbs. As n grows, V_n shrinks and the prefactor shrinks too, so Λ can also fall below 1 —
evidence _for_ the null accumulates as a persistent zero difference becomes informative.
That two-sidedness is what `evidence_strength` classifies:

```python
# 📁 backend/app/services/sequential_testing_service.py
    @staticmethod
    def _classify_evidence(lambda_ratio: float, boundary: float) -> EvidenceStrength:
        """
        Classify the strength of evidence from a lambda ratio.

        Uses a calibration inspired by Jeffreys' scale for Bayes factors:
        - lambda >= boundary        -> strong for effect
        - lambda >= boundary / 3    -> moderate for effect
        - lambda <= 1 / boundary    -> strong for null
        - lambda <= 3 / boundary    -> moderate for null
        - otherwise                 -> inconclusive
        """
```

#### What the route actually feeds it

`GET /results/{id}/sequential` (404 unless the experiment has `sequential_testing_enabled`)
reads **current totals**, not a history:

- the **control** and the **first non-control variant** only (the one created first, so the
  same arm on every call) — a third or fourth arm is not analysed by this route;
- on the experiment's **primary metric** (else its first metric);
- `*_total` = assignment rows for the variant; `*_successes` = **converting users**, counted
  exactly as `/results` counts them (`count_converting_users`, Part I Hour 2), whatever the
  metric's type. A revenue primary metric is tested here as the share of users who sent its
  event, not as its mean per user, so the Sequential tab and the metric's `/results` row can
  answer different questions about the same metric.

The same function builds the `sequential_testing` block embedded in `GET /results/{id}` when
sequential testing is enabled, so the two answers cannot disagree. `run_sequential_analysis`
assembles the block: the mSPRT result, the confidence
sequence (Hour 6), an evidence "trajectory", the long-running-risk estimate and a
recommended action. The trajectory is a **single point** — the current totals:

```python
# 📁 backend/app/services/sequential_testing_service.py
        # 4. Evidence trajectory (single point for current data)
        evidence_trajectory = [
            EvidencePoint(
                sample_size=total_sample,
                lambda_ratio=msprt_result.lambda_ratio,
                always_valid_p_value=msprt_result.always_valid_p_value,
                can_stop=msprt_result.can_stop,
            )
        ]

        # 5. Determine recommended action (mSPRT alone)
        recommended_action = self._determine_action(msprt_result=msprt_result)
```

`compute_evidence_trajectory`, which recomputes Λ over a list of cumulative looks, still
exists as a library function (the Try It and E2.2 use it), but the route does not call it.
The dashboard's `EvidenceRatioChart` therefore plots one point against the boundary line, on
a **linear** axis.

#### The decision: `stop_for_effect` or `continue`, and an advisory `at_risk`

```python
# 📁 backend/app/services/sequential_testing_service.py
    @staticmethod
    def _determine_action(msprt_result: MSPRTResult) -> str:
        """
        Determine the recommended action from the mSPRT result.

        - mSPRT crosses its boundary (Lambda >= 1/alpha) -> ``stop_for_effect``
        - otherwise -> ``continue``

        ``stop_for_futility`` stays in the response's value set but is not
        emitted: the mSPRT has no futility boundary, and an experiment that
        runs long is reported through the advisory ``at_risk`` flag instead.
        """
        if msprt_result.can_stop:
            return "stop_for_effect"
        return "continue"
```

`recommended_action` is exactly `stop_for_effect` when `can_stop` is true and `continue`
otherwise. There is no futility stop and no `extend`. Separately, `estimate_long_running_risk`
sets the top-level boolean **`at_risk`** (also inside `long_running_risk.is_at_risk`) when
the experiment has run more than 1.5× its expected duration, or has used at least half its
expected duration with less than half its required sample. The route reads
`expected_duration_days` (default 30) and `required_sample_size` (default 10,000) from the
stored config. It is **advice**, never a stopping rule: "running long is not evidence of no
effect", as its own recommendation text says.

> **Watch Out!** `can_stop` is a statement about the _false-positive_ guarantee: you may
> stop and declare an effect with P(false positive) ≤ α. It says nothing about the effect's
> _size_ — a Λ that crosses 20 on a huge sample can correspond to a negligible lift
> (Part I's effect size still applies). And there is no futility rule: Λ falling toward
> "strong for null" is reported in `evidence_strength`, but `recommended_action` stays
> `continue`. Deciding to give up on an experiment is a human call, informed by
> `evidence_strength` and `at_risk`.

> **Did You Know?** Λ's boundary `1/α` is exactly 20 at α = 0.05. The interesting region
> spans from about 0.05 (strong for the null) to 20 (stop for the effect), and the classical
> "p = 0.05" moment is _not_ where Λ = 20 — at any given look Λ = 20 is harder to reach than
> the classical test's threshold. That's the price of validity.

### Try It Yourself

Recompute Λ and the always-valid p by hand, then watch how τ² and α change the verdict:

```python
>>> import math
>>> from backend.app.services.sequential_testing_service import SequentialTestingService
>>> svc = SequentialTestingService()
>>> r = svc.compute_msprt(400, 10000, 520, 10000); r.lambda_ratio, r.always_valid_p_value, r.can_stop, r.evidence_strength
(315.22..., 0.00317..., True, <EvidenceStrength.STRONG_FOR_EFFECT: 'strong_for_effect'>)
>>> p_c, p_t = 0.04, 0.052; V = svc.difference_variance(400, 10000, 520, 10000); Z = (p_t-p_c)/math.sqrt(V); tau2 = 0.001
>>> math.sqrt(V/(V+tau2)) * math.exp(tau2*Z**2/(2*(V+tau2)))                      # the same Λ
315.22...
>>> [round(svc.compute_msprt(400, 10000, 520, 10000, tau_squared=t).lambda_ratio, 1) for t in (0.0001, 0.001, 0.01)]
[531.8, 315.2, 106.7]
>>> svc.compute_msprt(400, 10000, 520, 10000, alpha=0.10).boundary             # 1/alpha
10.0
>>> svc.compute_msprt(0, 5000, 0, 5000).evidence_strength     # no conversions anywhere: evidence for the null
<EvidenceStrength.STRONG_FOR_NULL: 'strong_for_null'>
>>> svc.compute_msprt(0, 0, 0, 5000).evidence_strength        # an empty arm: nothing to say
<EvidenceStrength.INCONCLUSIVE: 'inconclusive'>
>>> a = svc.run_sequential_analysis(400, 10000, 520, 10000, {"alpha": 0.05, "actual_days": 10, "expected_days": 30, "required_sample_size": 10000})
>>> a.recommended_action, a.at_risk, a.alpha_spending, len(a.evidence_trajectory)
('stop_for_effect', False, [], 1)
```

Compare the classical two-sided p for the same counts (≈ 0.00005 from Part I Hour 2) with the
always-valid p of 0.0032 — larger, on purpose.

### Key takeaways

- mSPRT: Λ_n = √(V/(V+τ²)) · exp(τ²Z²/(2(V+τ²))), a martingale under the null; stop when Λ ≥
  1/α; `min(1, 1/Λ)` is the always-valid p-value.
- τ² is the prior variance on effect sizes (default 0.001); α sets the boundary 1/α.
- The route analyses current totals of converting users for the control and the first
  treatment only; the "trajectory" is one point.
- `recommended_action` is `stop_for_effect` (Λ ≥ 1/α) or `continue` — nothing else is
  emitted; `at_risk` is advisory.

---

## Hour 6 — Always-valid confidence sequences, and why there are no alpha-spending boundaries

### What & why

A **confidence sequence** is to a confidence interval what an always-valid p-value is to a
classical one: a sequence of intervals (CS_t) such that P(∃t: true δ ∉ CS_t) ≤ α. You may
read it at any time and it's still a (1 − α) interval.

The cleanest way to build one is to **invert the test you already have**. The mSPRT tests
"δ = 0". Run the same mixture test against every other candidate δ₀ instead, and keep the
δ₀ values it would _not_ reject: Λ(δ₀) < 1/α. That set is an interval centred on δ̂, and with
the variance V of the difference (the Agresti–Caffo one of Hour 5) and the mixing variance τ² its
half-width has a closed form:

```
half-width = √( V(V + τ²)/τ² · ( 2·ln(1/α) + ln((V + τ²)/V) ) )
```

Two properties follow directly, and both are the point:

1. **It agrees with the stop decision.** 0 lies outside the sequence exactly when Λ(0) ≥ 1/α,
   i.e. exactly when `can_stop` is true. The interval and the verdict cannot contradict each
   other.
2. **It keeps narrowing.** The half-width falls roughly like √(V·log(1/V)) — a little slower
   than a classical interval's √V, which is the price of validity at every look.

### How Controlvar does it

```python
# 📁 backend/app/services/sequential_testing_service.py  (docstring abridged)
    def compute_always_valid_ci(self, control_successes, control_total, treatment_successes, treatment_total,
                                alpha: float = 0.05, tau_squared: float = 0.001) -> ConfidenceSequence:
        """
        Compute an always-valid confidence interval (confidence sequence).

        The interval is the inversion of the same normal-mixture mSPRT that
        ``compute_msprt`` runs (Johari et al. 2017; Howard et al. 2021) ...
        Because V and tau^2 are the ones ``compute_msprt`` uses, 0 lies
        outside this interval exactly when Lambda >= 1/alpha, i.e. exactly
        when ``can_stop`` is true. ...
        """
        sample_size = control_total + treatment_total
        unbounded = ConfidenceSequence(lower=-1.0, upper=1.0, width=2.0, sample_size=sample_size)

        if control_total == 0 or treatment_total == 0:
            return unbounded

        p_c = control_successes / control_total
        p_t = treatment_successes / treatment_total
        delta_hat = p_t - p_c

        V_n = self.difference_variance(
            control_successes, control_total, treatment_successes, treatment_total
        )

        margin = self.confidence_sequence_half_width(V_n, tau_squared, alpha)

        lower = max(delta_hat - margin, -1.0)
        upper = min(delta_hat + margin, 1.0)
        width = upper - lower

        return ConfidenceSequence(lower=lower, upper=upper, width=width, sample_size=sample_size)

    @staticmethod
    def confidence_sequence_half_width(variance: float, tau_squared: float, alpha: float) -> float:
        """Half-width of the normal-mixture confidence sequence, for V > 0.

        sqrt( V (V + tau^2) / tau^2 * (2 ln(1/alpha) + ln((V + tau^2) / V)) ).
        """
        v_plus_tau = variance + tau_squared
        return math.sqrt(variance * v_plus_tau / tau_squared
                         * (2.0 * math.log(1.0 / alpha) + math.log(v_plus_tau / variance)))
```

The result is intersected with [−1, 1], the range a difference of proportions can take, and
it is exactly [−1, 1] only when an arm is empty — the same case in which `compute_msprt` reports
Λ = 1. (An arm with all 0s or all 1s used to have no finite interval, because the plug-in variance
was 0. With the Agresti–Caffo variance it gets one: two arms of 5,000 with no conversions give
±0.0011 around 0 and cannot stop.) The route passes the same `alpha` and `tau_squared` to
both, so the agreement holds at whatever level you asked for.

> **Watch Out!** If you read an older copy of this book, it gave the sequence as
> δ̂ ± √(V + τ²)·√(2·ln(1/α)). That was the platform's formula before issue #231 was fixed.
> It never narrowed below about ±0.078 at τ² = 0.001 however much data arrived, and it could
> contain 0 while `can_stop` said stop. The inverted form above replaced it, in engine
> version 1.2.0, and the switch to the Agresti–Caffo variance came with engine version 1.4.0.
> The engine is at 1.8.0 now; none of 1.5.0–1.8.0 touched the sequential analysis (Part IV
> Hour 12). Both changes move numbers a client may have stored, which is what the engine
> version is for.

`backend/tests/unit/stats_validation/test_sequential_confidence_sequence.py` holds the
replacement to its two properties: agreement with `can_stop` and narrowing as n grows.

#### Alpha spending: the theory, and why the platform does not compute it

**Alpha spending** is for teams that _pre-plan_ their looks — "we'll analyse at 25%, 50%,
75% and 100% of the planned sample." A spending function says how much of the total α may be
"spent" by information fraction t, and the boundaries are set so the cumulative
false-positive probability across the planned looks is α. **O'Brien–Fleming** boundaries
have the shape c/√t — very strict early, near-classical at the end. **Pocock** uses one
constant boundary c at every look, so each look is stricter than 1.96.

The catch is in "set so the cumulative probability is α": the constant c has to be solved
for the number of looks, because the looks are correlated. For two-sided α = 0.05:

| Design (K looks)  | Correct constant c | What the naive shortcut uses        | Shortcut's real error rate |
| ----------------- | ------------------ | ----------------------------------- | -------------------------- |
| OBF, K = 4        | 2.024              | 1.96 (boundaries 1.96/√t)           | ~5.8% — too liberal        |
| OBF, K = 10       | ~2.09              | 1.96                                | ~6.9% — too liberal        |
| Pocock, K = 4     | 2.361              | z at α/K = 2.498                    | ~3.5% — too strict         |
| Pocock, K = 10    | 2.555              | z at α/K = 2.807                    | ~2.5% — too strict         |

(The error rates are 400,000-path simulations of a Brownian motion observed at K equally
spaced looks; E2.4 reproduces them.)

The platform used to return an `alpha_spending` table built from exactly those shortcuts,
and it was indexed by a look number that nothing counted — the route has no record of how
many times anyone has looked. Issue #232 removed it: `compute_alpha_spending`,
`_obrien_fleming_spending` and `_pocock_spending` no longer exist, `alpha_spending` is
always `[]` in the response (the field stays so the response shape is stable), and the
dashboard's spending table only renders when the list is non-empty — which is never. The
stop/continue decision is the mSPRT alone, which stays valid however often anyone looks.

> **Watch Out!** Alpha spending is only valid for the **planned** schedule. Ten planned
> looks with OBF boundaries, then an eleventh unplanned look at the classical 1.96, is the
> peeking problem again. If you can't commit to a schedule and a system that counts the
> looks, use the mSPRT — its guarantee doesn't care when or how often you look.

#### What the Sequential tab shows

`SequentialMonitor` renders the beta notice (`AnalysisNotice`, from `analysis_status` and
`analysis_notice`), the recommendation (`EarlyStoppingBanner`, which uses the sign of the
confidence sequence to say whether a stop means the change is better or worse for the metric,
when it can tell), the `at_risk` warning, the evidence-ratio chart (the one current point against
the boundary), and the confidence sequence. That is the whole
promise: a stop/continue verdict whose false-positive rate is at most α at every look, an
interval that agrees with it, and advice when the experiment is dragging.

### Try It Yourself

```python
>>> import math
>>> from backend.app.services.sequential_testing_service import SequentialTestingService
>>> svc = SequentialTestingService()
>>> cs = svc.compute_always_valid_ci(400, 10000, 520, 10000); round(cs.lower, 4), round(cs.upper, 4), round(cs.width, 4)
(0.0022, 0.0218, 0.0195)
>>> V = 0.04*0.96/1e4 + 0.052*0.948/1e4; round(2 * 1.96 * math.sqrt(V), 4)    # classical width for comparison
0.0116
>>> [round(svc.compute_always_valid_ci(int(0.04*n), n, int(0.052*n), n).width, 4) for n in (1_000, 10_000, 100_000, 1_000_000)]
[0.0574, 0.0195, 0.0068, 0.0023]
>>> # agreement: 0 outside the sequence exactly when can_stop
>>> all(svc.compute_msprt(c, 10000, 520, 10000).can_stop == (lambda s: s.lower > 0 or s.upper < 0)(svc.compute_always_valid_ci(c, 10000, 520, 10000)) for c in range(300, 460, 2))
True
>>> hasattr(svc, "compute_alpha_spending")
False
```

Then fetch `/results/{id}/sequential` for a demo experiment with sequential testing enabled
and match `msprt_result` and `confidence_sequence` to these calls on the counts that
`/results` reports for the control and the first treatment. Read `analysis_notice` and
confirm `alpha_spending` is `[]`.

### Key takeaways

- Confidence sequence = the inverted mSPRT: δ̂ ± √(V(V+τ²)/τ² · (2 ln(1/α) + ln((V+τ²)/V))),
  clipped to [−1, 1]; [−1, 1] with no data. 0 is outside it exactly when `can_stop`.
- It narrows as data arrives (≈ 0.0195 wide at 10,000 per arm in the example, 0.0023 at a
  million), always somewhat wider than the classical interval.
- Alpha spending (OBF, Pocock) is theory here: the platform computes no boundaries,
  `alpha_spending` is always `[]`, and `spending_function`/`planned_looks` are ignored.
- The Sequential tab shows the beta notice, the mSPRT verdict, `at_risk`, the one-point
  evidence chart and the sequence.

---

## Part II Workshop

**Q1. You check a fixed-horizon test daily for 20 days and stop at the first p < 0.05. Under
the null, roughly how often do you stop?**
About 25% of the time, not 5%. The 5% guarantee is for one planned look; a wandering
p-value crosses a fixed line far more often than once.

**Q2. State the always-valid guarantee in one inequality.**
P(∃t: p_t ≤ α) ≤ α under the null — the p-value process crosses α at most with probability
α, whatever the stopping rule.

**Q3. What does Λ_n equal when the observed difference is zero, and why isn't it 1?**
`√(V_n/(V_n+τ²)) < 1` — the mixture put prior mass on non-zero effects that predicted a
difference; observing none is mild evidence for the null.

**Q4. Why is `always_valid_p_value` larger than the classical p at the same counts?**
Validity at every look costs power at any single look; `1/Λ` is more conservative than the
classical tail probability by construction (Λ must reach 1/α, a higher bar than a single
z = 1.96 crossing).

**Q5. An experiment's config says `method: "always_valid"`, `spending_function: "pocock"`,
`planned_looks: 4`. What does `/sequential` compute?**
The mSPRT at the configured `alpha`, with `method: "msprt"` in the response and a notice that
`always_valid` is an alias. The spending function and planned looks are stored and ignored;
`alpha_spending` is `[]`.

**Q6. The confidence sequence contains 0. What is `recommended_action`?**
`continue`. 0 is inside the sequence exactly when Λ < 1/α, i.e. when `can_stop` is false —
the two are the same test.

**Q7. The response says `at_risk: true`. Should you stop for futility?**
Not on that alone. `at_risk` says the experiment is running long or collecting slowly; it is
not evidence of no effect, and the platform never emits `stop_for_futility`. Look at
`evidence_strength` and the effect size you could still detect, then decide.

**Quiz.** τ² = 0.001 vs 0.01 — which reaches the boundary first for a _small_ true effect?
→ 0.001: a tighter mixture around zero gives more power for small effects.

---

## Exercises

**E2.1 — Λ by hand.** For control 300/7,500 and treatment 360/7,500, compute V_n, Z_n and
Λ at τ² = 0.001; state `can_stop` and the always-valid p; compare with `compute_msprt`.

**E2.2 — Trajectory.** Using daily cumulative counts you generate with a true 10% lift on a
4% base (500 users/arm/day, `default_rng(3)`), compute Λ each day for 30 days and report the
first day `can_stop` is true; repeat with no true effect. Then repeat each case 500 times and
report how often it stops within 30 days.

**E2.3 — Sequence vs interval.** For the counts in E2.1, compute the confidence sequence and
the classical 95% interval; state the ratio of widths and whether 0 is inside the sequence.

**E2.4 — Boundaries the platform does not compute.** For four planned looks at α = 0.05,
compute the naive OBF boundaries `1.96/√t` and the α/K Pocock boundary, then simulate their
real false-positive rates. For an observed z of 2.5 at look 2, say whether each design
(naive and correct) stops.

**E2.5 — Policy.** Write the two-sentence guidance you'd put on the Sequential tab for a
product manager who asks "so can I ship?" when `recommended_action` is `stop_for_effect`, the
effect label is "negligible," and the sample-size meter is at 60%.

**E2.6 — Why the variance matters.** Simulate 500 A/A experiments (both arms 1%, control
gets 450 users and treatment 50 at each of 40 looks, `default_rng(0)`). Count how many ever have
`can_stop` true under `compute_msprt`, and how many would under the plug-in variance
`p_c(1−p_c)/n_c + p_t(1−p_t)/n_t` with the same formula for Λ.

---

## Solutions

**S2.1** — p_c = 0.04, p_t = 0.048, δ = 0.008; V_n = 0.04·0.96/7500 + 0.048·0.952/7500 ≈
1.121e−5 with the plug-in variance, 1.124e−5 with the Agresti–Caffo variance the platform uses
(`svc.difference_variance(300, 7500, 360, 7500)`); take the latter. Z_n ≈ 2.39; ratio =
V/(V+τ²) ≈ 0.01112; Λ ≈ √0.01112 · exp(0.001·5.69/(2·0.0010112)) ≈ 0.1054 · exp(2.813) ≈ 1.76.
`can_stop` False (1.76 < 20); always-valid p ≈ 0.568 — inconclusive, while the classical
two-sided p ≈ 0.017 would have "passed." That gap is Hour 4 in one number.

**S2.2**

```python
import numpy as np
from backend.app.services.sequential_testing_service import SequentialTestingService
svc = SequentialTestingService()
def first_stop(rng, lift):
    c = t = 0
    for day in range(1, 31):
        c += rng.binomial(500, 0.04); t += rng.binomial(500, 0.04 * (1 + lift))
        if svc.compute_msprt(c, 500 * day, t, 500 * day).can_stop:
            return day
    return None
rng = np.random.default_rng(3); first_stop(rng, 0.10), first_stop(rng, 0.0)      # (None, None)
rng = np.random.default_rng(42); runs = [first_stop(rng, 0.10) for _ in range(500)]
sum(d is not None for d in runs) / 500                                              # 0.11
rng = np.random.default_rng(43); sum(first_stop(rng, 0.0) is not None for _ in range(1000)) / 1000   # 0.009
```

With `default_rng(3)` neither case stops. Across 500 runs a 10% lift on a 4% base stops
within 30 days only about 11% of the time (median day 13 among those that do): at 15,000
users per arm the effect is too small for this test to resolve in a month. A 30% lift
(`default_rng(44)`, 500 runs) stops in 96% of runs (median day 11). With no effect it stopped
in 0.9% of 1,000 runs — inside the
5% the guarantee allows, and well under it because the guarantee holds for unlimited looks
and this run took only 30.

**S2.3** — Sequence: (−0.0029, 0.0189), width ≈ 0.0218. Classical: 1.96·√(1.121e−5)·2 ≈
0.0131. Ratio ≈ 1.66. 0 is inside the sequence, consistent with S2.1's `can_stop` False.
The sequence is wider than the classical interval, but by a factor that shrinks only slowly
with n rather than one that never closes — the inverted form keeps narrowing as data arrives.

**S2.4**

```python
import numpy as np
from scipy import stats
K = 4; t = np.arange(1, K + 1) / K; z = stats.norm.ppf(0.975)
naive_obf = z / np.sqrt(t)                         # [3.92, 2.77, 2.26, 1.96]
naive_pocock = stats.norm.ppf(1 - 0.05 / K / 2)    # 2.50 at every look
rng = np.random.default_rng(0); Z = np.cumsum(rng.standard_normal((400_000, K)), axis=1) / np.sqrt(np.arange(1, K + 1))
np.mean((np.abs(Z) >= naive_obf).any(axis=1)), np.mean((np.abs(Z) >= naive_pocock).any(axis=1))   # ≈ 0.058, ≈ 0.035
np.mean((np.abs(Z) >= 2.024 / np.sqrt(t)).any(axis=1)), np.mean((np.abs(Z) >= 2.361).any(axis=1))  # ≈ 0.050, ≈ 0.050
```

Naive OBF: boundaries 3.92, 2.77, 2.26, 1.96 — z = 2.5 at look 2 does **not** stop
(2.5 < 2.77), but the design's real error rate is about 5.8%. Correct OBF (c = 2.024):
4.05, 2.86, 2.34, 2.02 — no stop. Naive Pocock (α/4): 2.50 at every look — z = 2.5 is right
at the boundary and its real error rate is about 3.5%. Correct Pocock: 2.361 — stops. Same
data, four designs, different decisions — and two of them do not even hold the level they
claim, which is why the platform removed its table rather than keep it.

**S2.5** — "Sequential testing says the difference is real (we can stop without inflating
the false-positive rate), but the effect is negligible in size and we've seen only 60% of
the users the plan calls for, so the estimate of _how big_ it is will move. Ship it if a
small win is worth the change; if you need to know the size, keep running until the
sample-size meter is green."

**S2.6**

```python
import math, numpy as np
from backend.app.services.sequential_testing_service import SequentialTestingService
svc = SequentialTestingService()
def plug_in_stops(c, nc, t, nt, tau=0.001, alpha=0.05):
    pc, pt = c / nc, t / nt; V = pc * (1 - pc) / nc + pt * (1 - pt) / nt
    if V <= 0: return False
    Z = (pt - pc) / math.sqrt(V)
    return math.sqrt(V / (V + tau)) * math.exp(tau * Z * Z / (2 * (V + tau))) >= 1 / alpha
rng = np.random.default_rng(0); old = new = 0
for _ in range(500):
    c = t = nc = nt = 0; o = n = False
    for look in range(40):
        c += rng.binomial(450, 0.01); t += rng.binomial(50, 0.01); nc += 450; nt += 50
        o = o or plug_in_stops(c, nc, t, nt); n = n or svc.compute_msprt(c, nc, t, nt).can_stop
    old += o; new += n
old / 500, new / 500                                     # (0.256, 0.038)
```

The plug-in version stops 25.6% of experiments that have no effect, the Agresti–Caffo version
3.8%. With a small arm and a low rate, the early looks see zero or one conversion in the small
arm, the plug-in variance collapses, and a tiny observed difference looks enormous.

---

**Next:** [Part III — Sharper Estimates →](./part-3-sharper-estimates.md)

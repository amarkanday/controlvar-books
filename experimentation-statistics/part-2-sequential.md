# Part II — Peeking Safely

### Hours 4–6

[← Part I](./part-1-foundations.md) · [Contents](./README.md) · [Part III →](./part-3-sharper-estimates.md)

Everyone looks at results before the planned end. This Part is about why that quietly
destroys the guarantees of Part I (Hour 4), the method the platform uses to make looking
safe — the mixture sequential probability ratio test, an evidence ratio you may inspect at
any moment (Hour 5), and its two companions: confidence intervals that stay valid under
continuous monitoring and the alpha-spending boundaries for teams that pre-plan their looks
(Hour 6). By the end you'll know what `can_stop`, `always_valid_p_value` and
`evidence_strength` on the Sequential tab actually promise.

---

## Hour 4 — The peeking problem: why looking every day inflates false positives

### What & why

A fixed-horizon test promises: _if there's no effect, a p-value below 0.05 happens 5% of the
time_. The promise holds for **one** look at the pre-planned sample size. Look every day
and stop the first time p dips below 0.05, and the promise breaks: under the null, the
running p-value wanders, and over 20 daily looks the chance that it dips below 0.05 _at
least once_ is not 5% but something like 20–30%. Over unlimited looks it's certain — a random
walk crosses any fixed line eventually.

This isn't a subtle effect. It's the single most common way an experimentation program
"finds" wins that don't replicate, and it happens without anyone doing anything that feels
wrong: they just checked the dashboard.

Three ways out, and the platform implements two of them:

1. **Don't peek** (or peek and don't act). Culturally fragile.
2. **Pre-plan the looks and spend α across them** — group sequential designs with alpha-
   spending functions (O'Brien–Fleming, Pocock). Valid for the planned schedule, and the
   platform computes the boundaries (Hour 6).
3. **Use a test that's valid at every look** — a sequential test whose error guarantee holds
   for any stopping rule. The mixture SPRT (Hour 5) is the platform's default, and its
   `always_valid_p_value` is the number to act on.

#### What "always valid" means

A p-value process p_t is _always valid_ if, under the null, P(∃t: p_t ≤ α) ≤ α. Read it
slowly: the probability that the p-value _ever_ crosses α — at any time, under any peeking
schedule you like — is at most α. That's a strictly stronger guarantee than the fixed-horizon
one, and it's achieved by making the p-value _more conservative_ (larger) than the classical
one at any single look. You pay a bit of power at a fixed n for the freedom to look whenever.

### How Experimently does it

The experiment opts in with a method and its parameters, validated on the schema:

```python
# 📁 backend/app/schemas/experiment.py
class SequentialTestingConfigInput(BaseModel):
    """Configuration for enabling sequential testing on an experiment."""

    method: str = Field(default="msprt", pattern="^(msprt|always_valid)$", description="Sequential testing method: 'msprt' or 'always_valid'.")
    tau_squared: float = Field(default=0.001, gt=0.0, le=1.0, description="mSPRT mixing parameter.")
    spending_function: str = Field(default="obrien_fleming", pattern="^(obrien_fleming|pocock)$", description="Alpha spending function.")
    planned_looks: int = Field(default=10, ge=1, le=100, description="Number of planned interim analyses.")
```

and the enums that name what the service can classify:

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

The sequential block is _additional_: the primary fixed-horizon result stays on the page,
and the Sequential tab shows the trajectory. That's deliberate — the two answer different
questions, and showing both makes the peeking trade visible rather than hidden.

> **Watch Out!** The peeking problem applies to the **primary** p-value on the results
> page. If sequential testing isn't enabled and you refresh daily, the primary p-value is the
> classical one and its 0.05 line means less every day you look. Enable sequential testing
> on any experiment you'll monitor — which is every experiment — and act on the Sequential
> tab's verdict.

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
sum(ever_significant() for _ in range(2000)) / 2000      # ≈ 0.2–0.3, not 0.05
```

Then change `n_looks` to 1 and confirm ≈ 0.05.

### Key takeaways

- A fixed-horizon p-value's 5% promise holds for one planned look; daily peeking and
  stopping at the first p < 0.05 inflates false positives to 20–30%+.
- Always-valid: P(ever p_t ≤ α) ≤ α under the null, for any stopping rule; more
  conservative per look, valid forever.
- The platform offers mSPRT (default) and always-valid CIs, plus alpha-spending
  boundaries for pre-planned looks; the sequential block sits beside the primary result.

---

## Hour 5 — The mixture SPRT: an evidence ratio you can watch continuously

### What & why

The **sequential probability ratio test** (Wald, 1945) accumulates a likelihood ratio —
how much more likely the data are under the alternative than under the null — and stops when
it crosses a boundary. The trouble for A/B tests is that the "alternative" needs a specific
effect size, and you don't know it. The **mixture** SPRT (mSPRT) fixes that by averaging the
likelihood ratio over a prior distribution of effect sizes — a Gaussian centred at zero with
variance τ² — so no single alternative has to be chosen.

For a difference in proportions δ̂ = p_t − p_c with variance V_n, the mixture likelihood ratio
has a closed form:

```
Λ_n = √( V_n / (V_n + τ²) ) · exp( τ² · Z_n² / (2 · (V_n + τ²)) ),   Z_n = δ̂ / √V_n
```

Two properties make it a test you can watch:

1. **Λ_n is a nonnegative martingale under the null** (its expected next value is its
   current value), so by Ville's inequality P(∃n: Λ_n ≥ 1/α) ≤ α. Stop when Λ_n ≥ 1/α and
   you've controlled the false-positive rate _at every look_.
2. **`min(1, 1/Λ_n)` is an always-valid p-value.** It never goes below α before Λ crosses
   1/α, and once it does you may stop.

τ² is the one knob: the variance of the prior on effect sizes. Small τ² concentrates the
mixture near zero — more power for small effects, slower to declare large ones; large τ²
the reverse. `0.001` (a standard deviation of ~3 percentage points on a difference of
proportions) is a sensible default for conversion metrics.

### How Experimently does it

The core computation — quoted in full because the formula in the comment _is_ the method:

```python
# 📁 backend/app/services/sequential_testing_service.py
    def compute_msprt(self, control_successes: int, control_total: int, treatment_successes: int, treatment_total: int,
                      tau_squared: float = 0.001, alpha: float = 0.05) -> MSPRTResult:
        """
        Compute the mixture Sequential Probability Ratio Test (mSPRT).

        The mSPRT uses a Gaussian mixture over possible effect sizes to
        produce an evidence ratio (lambda) that can be monitored continuously.
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

        # Pooled variance of the difference in proportions
        V_n = p_c * (1 - p_c) / control_total + p_t * (1 - p_t) / treatment_total

        if V_n <= 0:
            # Degenerate case (all 0s or all 1s in both groups)
            return MSPRTResult(lambda_ratio=1.0, always_valid_p_value=1.0, can_stop=False,
                               evidence_strength=EvidenceStrength.INCONCLUSIVE, boundary=boundary)

        Z_n = delta / math.sqrt(V_n)

        # mSPRT lambda: mixture likelihood ratio with Gaussian(0, tau^2) prior
        # Lambda_n = sqrt(V_n / (V_n + tau^2)) * exp(tau^2 * Z_n^2 / (2*(V_n + tau^2)))
        ratio = V_n / (V_n + tau_squared)
        lambda_ratio = math.sqrt(ratio) * math.exp(tau_squared * Z_n ** 2 / (2.0 * (V_n + tau_squared)))

        can_stop = lambda_ratio >= boundary
        always_valid_p = min(1.0, 1.0 / lambda_ratio)

        evidence_strength = self._classify_evidence(lambda_ratio, boundary)

        return MSPRTResult(lambda_ratio=lambda_ratio, always_valid_p_value=always_valid_p, can_stop=can_stop,
                           evidence_strength=evidence_strength, boundary=boundary)
```

Read the shape of Λ. With no data (`Z_n = 0`) it's `√(V_n/(V_n+τ²)) < 1` — evidence slightly
_for_ the null, because the mixture spreads probability over alternatives that predicted a
difference and none appeared. As |Z_n| grows the exponential dominates and Λ climbs. As n
grows, V_n shrinks and the prefactor shrinks too, so Λ can also fall below 1 — evidence
_for_ the null accumulates as a persistent zero difference becomes informative. That
two-sidedness is what `evidence_strength` classifies:

```python
# 📁 backend/app/services/sequential_testing_service.py
    def _classify_evidence(self, lambda_ratio: float, boundary: float) -> EvidenceStrength:
        """Classify the strength of evidence given the lambda ratio and boundary."""
        ...
```

— strong for the effect above the boundary, moderate as it approaches, inconclusive near 1,
and moderate/strong for the null as Λ falls well below 1.

`run_sequential_analysis` assembles the block the endpoint returns — the current mSPRT
result, the confidence sequence (Hour 6), the trajectory over looks, a long-running-risk
estimate and a recommended action — and `compute_evidence_trajectory` recomputes Λ at each
cumulative look so the dashboard can plot it against the boundary:

```python
# 📁 backend/app/services/sequential_testing_service.py
    def compute_evidence_trajectory(self, control_successes_over_time: List[int], control_totals_over_time: List[int],
                                    treatment_successes_over_time: List[int], treatment_totals_over_time: List[int],
                                    tau_squared: float = 0.001, alpha: float = 0.05) -> List[EvidencePoint]:
        """
        Compute the evidence trajectory over sequential data looks.

        Takes parallel lists of cumulative data at each look and returns
        ...
```

The endpoint (`GET /results/{id}/sequential`) reads the cumulative counts from the two
tables — the Week 1 join, per day — and the dashboard's `SequentialMonitor` draws Λ against
`boundary` with the `can_stop` moment marked.

> **Watch Out!** `can_stop` is a statement about the _false-positive_ guarantee: you may
> stop and declare an effect with P(false positive) ≤ α. It says nothing about the effect's
> _size_ — a Λ that crosses 20 early on a huge sample can correspond to a negligible lift
> (Hour 2's effect size still applies), and nothing about _futility_ — Λ falling toward
> "strong for null" is the signal to stop for that reason, and it's slower to arrive.

> **Did You Know?** Λ's boundary `1/α` is exactly 20 at α = 0.05. The dashboard plots Λ on a
> log scale for that reason: the interesting region spans from 0.1 (evidence for the null)
> to 20 (stop for the effect), and the classical "p = 0.05" moment is where Λ = 20 — which
> at any given look is _harder_ to reach than the classical test's threshold. That's the
> price of validity.

### Try It Yourself

Recompute Λ and the always-valid p by hand, then watch how τ² changes the verdict:

```python
>>> import math
>>> from scipy import stats
>>> from backend.app.services.sequential_testing_service import SequentialTestingService
>>> svc = SequentialTestingService()
>>> r = svc.compute_msprt(400, 10000, 520, 10000); r.lambda_ratio, r.always_valid_p_value, r.can_stop, r.evidence_strength
>>> p_c, p_t = 0.04, 0.052; V = p_c*(1-p_c)/1e4 + p_t*(1-p_t)/1e4; Z = (p_t-p_c)/math.sqrt(V); tau2 = 0.001
>>> math.sqrt(V/(V+tau2)) * math.exp(tau2*Z**2/(2*(V+tau2)))                      # the same Λ
>>> svc.z_test_proportions if hasattr(svc, "z_test_proportions") else None
>>> [round(svc.compute_msprt(400, 10000, 520, 10000, tau_squared=t).lambda_ratio, 1) for t in (0.0001, 0.001, 0.01)]
>>> svc.compute_msprt(0, 5000, 0, 5000).evidence_strength     # degenerate: inconclusive
```

Compare the classical two-sided p for the same counts (≈ 0.00006 from Hour 2) with the
always-valid p — larger, on purpose.

### Key takeaways

- mSPRT: Λ_n = √(V/(V+τ²)) · exp(τ²Z²/(2(V+τ²))), a martingale under the null; stop when Λ ≥
  1/α; `min(1, 1/Λ)` is the always-valid p-value.
- τ² is the prior variance on effect sizes; 0.001 is the default; smaller favours small
  effects.
- Λ moves both ways: above the boundary → stop for effect; well below 1 → evidence for the
  null.
- `can_stop` controls false positives at every look; it doesn't judge effect size or
  futility.

---

## Hour 6 — Always-valid confidence sequences and alpha-spending boundaries

### What & why

Two companions to the mSPRT, for two different teams.

A **confidence sequence** is to a confidence interval what an always-valid p-value is to a
classical one: a sequence of intervals (CS_t) such that P(∃t: true δ ∉ CS_t) ≤ α. You may
read it at any time and it's still a 95% interval. The mSPRT's mixture gives a simple
approximate form: the margin is ρ·√(2·ln(1/α)) where ρ = √(V_n + τ²) is the effective
standard deviation under the mixture. It's wider than the classical interval at any single
look — again, the price of validity — and it shrinks as V_n does.

**Alpha spending** is for teams that _pre-plan_ their looks — "we'll analyse at 25%, 50%,
75% and 100% of the planned sample." A spending function α*(t) says how much of the total α
may be "spent" by information fraction t; the boundary at each look is set so the
cumulative false-positive probability across looks stays at α. **O'Brien–Fleming** spends
almost nothing early (very strict boundaries, so early stops need overwhelming evidence) and
nearly all of it at the end; **Pocock** spends equally at every look (the same z boundary
throughout, which makes each individual look stricter than 1.96). O'Brien–Fleming is the
usual choice because it preserves most of the power for the final analysis while permitting
an early stop for a very large effect.

### How Experimently does it

The confidence sequence:

```python
# 📁 backend/app/services/sequential_testing_service.py
    def compute_always_valid_ci(self, control_successes, control_total, treatment_successes, treatment_total,
                                alpha: float = 0.05, tau_squared: float = 0.001) -> ConfidenceSequence:
        """
        Compute an always-valid confidence interval (confidence sequence).

        Uses the mSPRT mixing parameter to construct a CI that remains
        valid under continuous monitoring.
        """
        sample_size = control_total + treatment_total

        if control_total == 0 or treatment_total == 0:
            return ConfidenceSequence(lower=-1.0, upper=1.0, width=2.0, sample_size=sample_size)

        p_c = control_successes / control_total
        p_t = treatment_successes / treatment_total
        delta_hat = p_t - p_c

        V_n = p_c * (1 - p_c) / control_total + p_t * (1 - p_t) / treatment_total

        # rho = sqrt(V_n + tau^2), the effective standard deviation under the
        # mixture distribution
        rho = math.sqrt(V_n + tau_squared)

        # Approximate margin: rho * sqrt(2 * log(1/alpha))
        margin = rho * math.sqrt(2.0 * math.log(1.0 / alpha))

        lower = delta_hat - margin
        upper = delta_hat + margin
        width = upper - lower

        return ConfidenceSequence(lower=lower, upper=upper, width=width, sample_size=sample_size)
```

`√(2·ln(20)) ≈ 2.45` versus the classical `1.96` — and ρ ≥ √V_n — so the sequence is
always wider. With no data, the interval is `[-1, 1]`: "we know nothing," honestly.

The two spending functions, with their formulas:

```python
# 📁 backend/app/services/sequential_testing_service.py
    @staticmethod
    def _obrien_fleming_spending(current_look: int, planned_looks: int, alpha: float) -> List[AlphaSpendingBoundary]:
        """
        Compute O'Brien-Fleming alpha spending boundaries.

        The OBF spending function is:
            alpha*(t) = 2 * (1 - Phi(z_{alpha/2} / sqrt(t)))

        where t = i / planned_looks is the information fraction at look i.
        The incremental spend at each look is alpha*(t_i) - alpha*(t_{i-1}).
        """
        z_alpha_half = stats.norm.ppf(1 - alpha / 2.0)
        boundaries: List[AlphaSpendingBoundary] = []

        for i in range(1, current_look + 1):
            t_i = i / planned_looks
            # Cumulative alpha spent through look i
            cumulative_alpha = 2.0 * (1.0 - stats.norm.cdf(z_alpha_half / math.sqrt(t_i)))
            # Ensure we don't exceed the total alpha budget
            cumulative_alpha = min(cumulative_alpha, alpha)

            boundary_z = z_alpha_half / math.sqrt(t_i)
            boundary_p = 2.0 * (1.0 - stats.norm.cdf(boundary_z))

            boundaries.append(AlphaSpendingBoundary(look_number=i, cumulative_alpha=cumulative_alpha,
                                                    boundary_z=boundary_z, boundary_p=boundary_p))

        return boundaries

    @staticmethod
    def _pocock_spending(current_look: int, planned_looks: int, alpha: float) -> List[AlphaSpendingBoundary]:
        """
        Compute Pocock alpha spending boundaries.

        Pocock uses equal alpha spending at each look:
            incremental_alpha = alpha / planned_looks

        All boundaries use the same z-value.
        """
        incremental_alpha = alpha / planned_looks
        # The z-boundary is the same at every look (Pocock property)
        boundary_z = stats.norm.ppf(1.0 - incremental_alpha / 2.0)
        boundary_p = 2.0 * (1.0 - stats.norm.cdf(boundary_z))
        ...
```

Read O'Brien–Fleming's boundary: `z_{α/2} / √t`. At the first of ten looks (t = 0.1) that's
`1.96 / 0.316 ≈ 6.2` — you'd need a six-sigma difference to stop; at the last (t = 1) it's
1.96, the classical threshold. Pocock at ten looks: `α/10 = 0.005` per look, `z ≈ 2.81` at
every look — easier to stop early than OBF, but the final look is also stricter than 1.96.

The endpoint exposes both alongside the mSPRT so a team that pre-registered looks can read
its boundaries, and `_determine_action` folds Λ, the sequence and the long-running-risk
estimate into one recommendation (`stop_for_effect`, `stop_for_futility`, `continue`,
`extend`):

```python
# 📁 backend/app/services/sequential_testing_service.py
    def estimate_long_running_risk(self, ...) -> LongRunningRisk:
        ...
    @staticmethod
    def _determine_action(...)
```

The long-running-risk estimate is the pragmatic companion to futility: an experiment whose
Λ has sat near 1 for weeks while the sequence's width has stopped shrinking meaningfully is
unlikely to resolve, and the platform says so rather than letting it run forever.

> **Watch Out!** Alpha spending is only valid for the **planned** schedule. Ten planned
> looks with OBF boundaries, then an eleventh unplanned look at the classical 1.96, is the
> peeking problem again. If you can't commit to a schedule, use the mSPRT — its guarantee
> doesn't care when or how often you look.

> **Did You Know?** The interplay between the two methods is deliberate: a team can
> _pre-register_ "we'll act at the OBF boundaries" for governance, and _also_ watch the mSPRT
> daily for early signals. Both control α; they answer to different audiences (a review
> board vs a product manager). The Sequential tab shows the trajectory, the boundary, the
> sequence and the spending table together.

### Try It Yourself

```python
>>> from backend.app.services.sequential_testing_service import SequentialTestingService, SpendingFunction
>>> svc = SequentialTestingService()
>>> cs = svc.compute_always_valid_ci(400, 10000, 520, 10000); (round(cs.lower, 4), round(cs.upper, 4)), cs.width
>>> import math; 1.96 * math.sqrt(0.04*0.96/1e4 + 0.052*0.948/1e4) * 2      # classical width for comparison
>>> [(b.look_number, round(b.boundary_z, 2), round(b.cumulative_alpha, 4)) for b in svc.compute_alpha_spending(10, 10)]
>>> [(b.look_number, round(b.boundary_z, 2)) for b in svc.compute_alpha_spending(10, 10, spending_function=SpendingFunction.POCOCK)][:3]
```

Then fetch `/results/{id}/sequential` for a demo experiment with sequential testing enabled
and match `confidence_sequence` and `alpha_spending` to these calls.

### Key takeaways

- Confidence sequence: δ̂ ± ρ·√(2·ln(1/α)), ρ = √(V_n + τ²); valid at every look; wider than
  the classical interval; `[-1, 1]` with no data.
- Alpha spending for pre-planned looks: OBF (`z_{α/2}/√t`, strict early, classical at the
  end) or Pocock (equal spend, one z throughout); valid only for the planned schedule.
- The Sequential tab shows Λ vs boundary, the sequence, the spending table and a
  recommended action including futility/long-running risk.

---

## Part II Workshop

**Q1. You check a fixed-horizon test daily for 20 days and stop at the first p < 0.05. Under
the null, roughly how often do you stop?**
Around 20–30% of the time, not 5%. The 5% guarantee is for one planned look; a wandering
p-value crosses a fixed line far more often than once.

**Q2. State the always-valid guarantee in one inequality.**
P(∃t: p_t ≤ α) ≤ α under the null — the p-value process crosses α at most with probability
α, whatever the stopping rule.

**Q3. What does Λ_n equal with no data, and why isn't it 1?**
`√(V_n/(V_n+τ²)) < 1` — the mixture put prior mass on non-zero effects that predicted a
difference; observing none is mild evidence for the null.

**Q4. Why is `always_valid_p_value` larger than the classical p at the same counts?**
Validity at every look costs power at any single look; `1/Λ` is more conservative than the
classical tail probability by construction (Λ must reach 1/α, a higher bar than a single
z = 1.96 crossing).

**Q5. OBF vs Pocock at ten looks: which lets you stop earlier on a moderate effect, and
which preserves more power for the final look?**
Pocock stops earlier (z ≈ 2.81 at every look vs OBF's ≈ 6.2 at look 1); OBF preserves more
final-look power (its last boundary is the classical 1.96; Pocock's stays at 2.81).

**Q6. A team plans ten OBF looks, takes them, then peeks an eleventh time at 1.96. What
happened?**
The spending schedule was violated — the eleventh look is an unplanned classical test on top
of a fully spent α, and the false-positive guarantee no longer holds. The mSPRT's guarantee
would have.

**Quiz.** τ² = 0.001 vs 0.01 — which reaches the boundary first for a _small_ true effect?
→ 0.001: a tighter mixture around zero gives more power for small effects.

---

## Exercises

**E2.1 — Λ by hand.** For control 300/7,500 and treatment 360/7,500, compute V_n, Z_n and
Λ at τ² = 0.001; state `can_stop` and the always-valid p; compare with `compute_msprt`.

**E2.2 — Trajectory.** Using daily cumulative counts you generate with a true 10% lift on a
4% base (500 users/arm/day, `default_rng(3)`), compute Λ each day for 30 days and report the
first day `can_stop` is true; repeat with no true effect and report whether it ever stops.

**E2.3 — Sequence vs interval.** For the counts in E2.1, compute the confidence sequence and
the classical 95% interval; state the ratio of widths.

**E2.4 — Boundaries.** Compute OBF and Pocock boundaries for four planned looks at α = 0.05;
for an observed z of 2.5 at look 2, say whether each design stops.

**E2.5 — Policy.** Write the two-sentence guidance you'd put on the Sequential tab for a
product manager who asks "so can I ship?" when `can_stop` is true, the effect label is
"negligible," and the sample-size meter is at 60%.

---

## Solutions

**S2.1** — p_c = 0.04, p_t = 0.048, δ = 0.008; V_n = 0.04·0.96/7500 + 0.048·0.952/7500 ≈
1.121e−5; Z_n ≈ 2.39; ratio = V/(V+τ²) ≈ 0.01109; Λ ≈ √0.01109 · exp(0.001·5.71/(2·0.0010112))
≈ 0.1053 · exp(2.824) ≈ 1.77. `can_stop` False (1.77 < 20); always-valid p ≈ 0.56 —
inconclusive, while the classical two-sided p ≈ 0.017 would have "passed." That gap is Hour 4
in one number.

**S2.2**

```python
import numpy as np
from backend.app.services.sequential_testing_service import SequentialTestingService
svc = SequentialTestingService(); rng = np.random.default_rng(3)
def first_stop(lift):
    c = t = 0
    for day in range(1, 31):
        c += rng.binomial(500, 0.04); t += rng.binomial(500, 0.04 * (1 + lift))
        if svc.compute_msprt(c, 500 * day, t, 500 * day).can_stop:
            return day
    return None
first_stop(0.10), first_stop(0.0)
```

With a 10% lift the stop typically arrives in the second half of the month at this traffic
(the effect is small — expect `None` on some seeds, i.e. more than 30 days needed); with no
effect it should be `None` — and across many seeds it will be non-`None` at most ~5% of the
time.

**S2.3** — Sequence: ρ = √(1.121e−5 + 0.001) ≈ 0.03180; margin = 0.03180·√(2·ln 20) ≈ 0.0779;
width ≈ 0.156. Classical: 1.96·√(1.121e−5)·2 ≈ 0.0131. Ratio ≈ 12 — the sequence is far
wider at this n because τ² dominates V_n; it tightens toward the classical width only as V_n
approaches τ² (i.e. at much smaller n) — which is the honest statement that with a τ² of
0.001 the sequence is designed for effects of a few points, not tenths of a point.

**S2.4** — OBF (t = 0.25, 0.5, 0.75, 1): boundaries ≈ 3.92, 2.77, 2.26, 1.96 — z = 2.5 at
look 2 does **not** stop (2.5 < 2.77). Pocock: α/4 = 0.0125 per look → z ≈ 2.50 at every
look — z = 2.5 at look 2 is right at the boundary (stops if ≥). Same data, different
pre-registered designs, different decisions — which is why the design is chosen before the
data.

**S2.5** — "Sequential testing says the difference is real (we can stop without inflating
the false-positive rate), but the effect is negligible in size and we've seen only 60% of
the users the plan calls for, so the estimate of _how big_ it is will move. Ship it if a
small win is worth the change; if you need to know the size, keep running until the
sample-size meter is green."

---

**Next:** [Part III — Sharper Estimates →](./part-3-sharper-estimates.md)

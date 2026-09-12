# Part III — Sharper Estimates

### Hours 7–9

[← Part II](./part-2-sequential.md) · [Contents](./README.md) · [Part IV →](./part-4-many-comparisons.md)

Part I asked "is there a difference?"; Part II asked "when may I look?". This Part asks
three sharper questions. Can pre-experiment data make the same users tell you more — CUPED
(Hour 7)? Can we answer the question the product manager actually asked, "what's the
probability B is better, and what do I lose if I'm wrong?" — the Bayesian block (Hour 8)? And
can the experiment _optimise while it runs_, sending more users to the better arm — bandits,
and what that costs in inference (Hour 9)?

---

## Hour 7 — CUPED: variance reduction from pre-experiment data

### What & why

The width of every interval and the size of every sample-size requirement is driven by one
thing: the **variance** of the metric. Users differ enormously in how much they spend, how
often they visit, how likely they are to convert — and most of that variation has nothing
to do with the treatment. If you knew, for each user, what they were _going to do anyway_,
you could subtract it and compare only the residual.

**CUPED** (Controlled-experiment Using Pre-Experiment Data; Deng, Xu, Kohavi & Walker, 2013)
does exactly that with a covariate X measured _before_ the experiment — last month's spend,
last month's visit count, the same metric in the pre-period. Because X was measured before
assignment, it's independent of treatment, so subtracting a multiple of it can't bias the
effect; it can only reduce the noise. The adjustment is

```
Y_cuped = Y − θ · (X − E[X]),     θ = Cov(Y, X) / Var(X)
```

— the OLS slope of Y on X. The adjusted metric has the same mean as Y (because we subtract a
zero-mean quantity) and variance `Var(Y)·(1 − ρ²)` where ρ is the correlation between Y and
X. A covariate with ρ = 0.5 removes 25% of the variance; ρ = 0.7 removes half — equivalent
to _doubling_ the sample size, for free.

**Winsorization** is the blunt companion for heavy-tailed metrics like revenue: clip the top
percentile so a single $10,000 order doesn't dominate the variance. It changes the estimand
slightly (you're now measuring "revenue capped at the 99th percentile") in exchange for a
usable interval.

### How Experimently does it

The primitives — θ from the control group, applied to both:

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

        # Using np.cov with ddof=1 (sample covariance) for consistency
        cov_matrix = np.cov(Y, X, ddof=1)
        cov_yx = cov_matrix[0, 1]
        return float(cov_yx / var_x)

    @staticmethod
    def apply_cuped(Y: np.ndarray, X: np.ndarray, theta: float, E_X: float) -> np.ndarray:
        """Apply the CUPED transformation to a set of observations.

        Y_cuped = Y - θ * (X - E[X])
        """
        Y = np.asarray(Y, dtype=float)
        X = np.asarray(X, dtype=float)
        return Y - theta * (X - E_X)
```

Estimating θ on **control only** is a deliberate conservative choice: the treatment might
change the Y–X relationship, and a θ fit on pooled data could then leak a little treatment
effect into the adjustment. Control-only θ can't. `E[X]` is taken from the pooled data,
which is correct because X is pre-treatment and its mean is the same in both arms.

The full pipeline, with the variance-reduction number the dashboard shows:

```python
# 📁 backend/app/services/cuped_service.py
    @staticmethod
    def compute_cuped_effect(control_Y, control_X, treatment_Y, treatment_X, alpha: float = 0.05) -> CupedEffect:
        """Run the full CUPED pipeline and return a CupedEffect result.

        Pipeline:
          1. Estimate θ from the control group (Cov(Y,X) / Var(X)).
          2. Compute E[X] from the pooled covariate observations.
          3. Adjust both groups: Y_adj = Y - θ*(X - E[X]).
          4. Compute the adjusted effect (difference of adjusted means).
          5. Compute the pooled SE of the adjusted effect.
          6. Perform a two-tailed z-test to obtain p-value and CI.
          7. Compute variance reduction percentage for the control group.
        """
        ...
        # Step 1: Estimate theta from control group
        theta = CupedService.compute_theta(control_Y, control_X)

        # Step 2: E[X] from pooled covariate
        E_X = np.concatenate([control_X, treatment_X]).mean()

        # Step 3: Adjust both groups
        control_Y_adj = CupedService.apply_cuped(control_Y, control_X, theta, E_X)
        treatment_Y_adj = CupedService.apply_cuped(treatment_Y, treatment_X, theta, E_X)

        # Step 4: Adjusted means and effect
        adj_control_mean = float(control_Y_adj.mean())
        adj_treatment_mean = float(treatment_Y_adj.mean())
        adjusted_effect = adj_treatment_mean - adj_control_mean

        # Step 5: Pooled SE (Welch-style — different group sizes / variances)
        var_c = float(np.var(control_Y_adj, ddof=1)) if n_c > 1 else 0.0
        var_t = float(np.var(treatment_Y_adj, ddof=1)) if n_t > 1 else 0.0
        se = math.sqrt(var_c / n_c + var_t / n_t) if (n_c > 0 and n_t > 0) else 0.0
        se = max(se, 1e-12)  # guard against division-by-zero below

        # Step 6: Two-tailed z-test
        z_stat = adjusted_effect / se
        p_value = float(2.0 * (1.0 - stats.norm.cdf(abs(z_stat))))
        ...
        # Step 7: Variance reduction percentage (control group)
        var_original = float(np.var(control_Y, ddof=1)) if n_c > 1 else 0.0
        if var_original > 0.0:
            var_adj = float(np.var(control_Y_adj, ddof=1))
            reduction_pct = (1.0 - var_adj / var_original) * 100.0
        else:
            reduction_pct = 0.0
```

Winsorization:

```python
# 📁 backend/app/services/cuped_service.py
    @staticmethod
    def apply_winsorization(values: np.ndarray, percentile: float = 99.0) -> np.ndarray:
        """Clip metric values at the given upper percentile (Winsorization).

        Values above the specified percentile are clipped to the percentile
        value.  Values at or below the percentile are left unchanged.
        """
        values = np.asarray(values, dtype=float)
        upper = np.percentile(values, percentile)
        return np.clip(values, a_min=None, a_max=upper)
```

The experiment opts in with `variance_reduction_config` (the covariate's metadata key, the
winsorization percentile); `GET /results/{id}/cuped` reads Y and X from event metadata per
user and returns the `CupedEffect`. The CUPED++ variant (`apply_cuped_ratio`) handles ratio
metrics (revenue per session) by adjusting numerator and denominator jointly.

**Post-stratification** (EP-043, `post_stratification_service.py`) is the sibling technique:
instead of a continuous covariate, stratify users by a categorical one (platform, country)
and reweight each stratum's estimate to the population proportions — more powerful than CUPED
when arm sizes differ within strata. Same family, different data shape.

> **Watch Out!** X must be measured **before** assignment. A "covariate" observed during the
> experiment (sessions this week) can be affected by the treatment, and adjusting on it
> _introduces_ bias — the classic post-treatment-adjustment mistake. The configuration asks
> for a pre-period key on purpose; a zero-variance or uncorrelated X gives θ = 0 and no
> reduction, which is the honest outcome.

> **Did You Know?** New users have no pre-period data. CUPED handles them by giving them the
> covariate mean (their adjustment is zero) — they still count, they just don't benefit. A
> product with mostly new users gets little from CUPED; a product with returning users gets
> a lot. The variance-reduction percentage on the dashboard tells you which you are.

### Try It Yourself

```python
>>> import numpy as np
>>> from backend.app.services.cuped_service import CupedService
>>> rng = np.random.default_rng(7)
>>> n = 5000
>>> X = rng.gamma(2.0, 20.0, size=2 * n)                      # pre-period spend, heavy-tailed
>>> noise = rng.normal(0, 15, size=2 * n)
>>> Y = 0.6 * X + noise                                        # this period's spend, correlated with X
>>> Y[n:] += 3.0                                               # a true $3 lift in treatment
>>> eff = CupedService.compute_cuped_effect(Y[:n], X[:n], Y[n:], X[n:])
>>> round(eff.theta, 3), round(eff.adjusted_effect, 2), round(eff.adjusted_se, 3), round(eff.variance_reduction_pct, 1)
>>> # the raw comparison for contrast
>>> raw_se = ((Y[:n].var(ddof=1) / n) + (Y[n:].var(ddof=1) / n)) ** 0.5; round(raw_se, 3)
>>> CupedService.apply_winsorization(np.array([1, 2, 3, 1000.0]), 75).tolist()
```

Then correlate X with something unrelated (`X = rng.normal(size=2*n)`) and confirm θ ≈ 0 and
the reduction ≈ 0%.

### Key takeaways

- CUPED: `Y − θ(X − E[X])`, θ = Cov/Var on control, X strictly pre-experiment; same mean,
  variance × (1 − ρ²).
- The pipeline reports the adjusted effect, SE, p, CI and the variance-reduction
  percentage; new users get zero adjustment.
- Winsorize heavy tails (changes the estimand slightly); post-stratification for
  categorical covariates.
- Adjusting on a post-treatment variable introduces bias — the config asks for a pre-period
  key.

---

## Hour 8 — Bayesian A/B: Beta-Binomial posteriors, probability to be best, expected loss

### What & why

A frequentist p-value answers a question nobody asked ("how surprising is this data if
nothing's going on?"). A product manager asks: **what's the probability B is better than A,
and how much do I stand to lose if I pick wrong?** Bayesian analysis answers those directly by
treating each arm's conversion rate θ as uncertain, expressing that uncertainty as a
distribution, and updating it with data.

For conversion rates the maths is a gift: the **Beta** distribution is _conjugate_ to the
binomial, so if the prior on θ is Beta(α, β) and you observe c conversions in n trials, the
posterior is Beta(α + c, β + n − c). Updating is addition. Beta(1, 1) is the uniform
("uninformative") prior; Beta(α, β) can be read as "α − 1 prior successes and β − 1 prior
failures."

From the posteriors, three quantities by Monte Carlo (draw 10,000 samples from each arm's
Beta, then count):

- **Probability to be best (PtBB)** — the fraction of draws in which arm i has the highest θ.
- **Expected loss** — E[max_j θ_j − θ_i]: how much conversion rate you expect to give up by
  choosing arm i. A tiny expected loss means "even if i isn't best, it's nearly best."
- **Credible interval** — the central 95% of the posterior: an interval that _is_ "95%
  probability the true rate is in here," which a confidence interval is not.

Plus a **Bayes factor** (Savage–Dickey) as a frequentist-flavoured summary of evidence, and a
**stopping rule** built on expected loss and a ROPE (region of practical equivalence).

### How Experimently does it

The conjugate update and the credible interval:

```python
# 📁 backend/app/services/bayesian_service.py
def compute_posterior(prior: Dict[str, float], observations: Dict[str, int]) -> Dict[str, float]:
    """Compute Beta-Binomial conjugate posterior."""
    alpha_prior = float(prior["alpha"])
    beta_prior = float(prior["beta"])

    conversions = int(observations["conversions"])
    total = int(observations["total"])
    ...
    non_conversions = total - conversions

    # Conjugate Beta-Binomial update:
    #   alpha_posterior = alpha_prior + conversions
    #   beta_posterior  = beta_prior  + non_conversions
    alpha_posterior = alpha_prior + conversions
    beta_posterior = beta_prior + non_conversions

    return {"alpha": alpha_posterior, "beta": beta_posterior}
```

```python
# 📁 backend/app/services/bayesian_service.py
    else:
        # Default: Beta distribution
        alpha = float(posterior["alpha"])
        beta = float(posterior["beta"])
        dist = scipy_stats.beta(a=alpha, b=beta)
        lower, upper = dist.interval(level)
        return float(lower), float(upper)
```

The Monte Carlo, seeded, and the two summaries from one sample matrix:

```python
# 📁 backend/app/services/bayesian_service.py
def _draw_posterior_samples(posteriors: List[Dict[str, float]], n_samples: int, seed: Optional[int]) -> np.ndarray:
    """Draw ``n_samples`` from each Beta posterior; shape ``[n_variants, n_samples]``."""
    rng = make_rng(resolve_seed(seed, n_samples))
    return np.array([rng.beta(float(p["alpha"]), float(p["beta"]), n_samples) for p in posteriors])


def compute_probability_to_be_best(posteriors, n_samples: int = DEFAULT_N_SAMPLES, seed: Optional[int] = None, *, samples: Optional[np.ndarray] = None) -> List[float]:
    """Compute Probability to Be Best (PtBB) via Monte Carlo sampling.

    For each variant, draws n_samples from its Beta posterior and computes
    the fraction of samples in which that variant has the highest value.
    ...
        samples: Posterior draws already made for these posteriors, shape
            ``[n_variants, n_samples]``.  Supplied by callers that need more
            than one statistic from the same matrix; omit it and the function
            draws its own.
    """
    ...
    if samples is None:
        samples = _draw_posterior_samples(posteriors, n_samples, seed)
    return _ptbb_from_samples(samples)


def compute_expected_loss(posteriors, n_samples: int = DEFAULT_N_SAMPLES, seed: Optional[int] = None, *, samples: Optional[np.ndarray] = None) -> List[float]:
    """Compute Expected Loss (regret) for each variant via Monte Carlo.

    For variant i, the expected loss is E[max_j(theta_j) - theta_i],
    i.e., how much we expect to "lose" by choosing variant i instead of
    the best variant.
    """
```

The `samples=` parameter is an engineering detail with a statistical consequence: PtBB,
expected loss and the ROPE check are computed from the _same_ draws, so they're mutually
consistent (and the seed makes them reproducible across calls — Hour 12).

The Bayes factor and its label:

```python
# 📁 backend/app/services/bayesian_service.py
def compute_bayes_factor(prior: Dict[str, float], posterior: Dict[str, float], null_value: Optional[float] = None) -> float:
    """Compute the Bayes Factor using the Savage-Dickey density ratio.

    The Savage-Dickey density ratio for a point null H0: theta = theta_0 is:
        BF01 = p(theta=theta_0 | data) / p(theta=theta_0 | prior)
    BF01 > 1 means the data favor H0 (null hypothesis, no effect).

    We return BF10 = 1 / BF01, so:
        BF10 > 1 means the data favor H1 (alternative, there IS an effect).
        BF10 > 10 indicates strong evidence for a real difference.
    """
    ...
    prior_density = scipy_stats.beta.pdf(null_value, alpha_prior, beta_prior)
    posterior_density = scipy_stats.beta.pdf(null_value, alpha_post, beta_post)
    ...
    bf10 = prior_density / posterior_density
    return float(bf10)


def get_bayes_factor_label(bf: float) -> str:
    """Return a human-readable interpretation label for a Bayes Factor.

    Uses the Jeffreys / Kass-Raftery scale:
        1  –  3  : Anecdotal
        3  – 10  : Moderate
        10 – 30  : Strong
        >  30    : Very Strong
    """
```

The stopping rule — expected loss below a threshold means "the best arm is nearly certainly
good enough"; a ROPE means "the arms are practically equivalent, stop":

```python
# 📁 backend/app/services/bayesian_service.py
def should_stop(posteriors, config: BayesianConfig, n_samples: int = DEFAULT_N_SAMPLES, seed: Optional[int] = None, *, samples=None, losses=None) -> BayesianDecision:
    """Apply Bayesian stopping rules to determine whether to stop the experiment.

    Stopping rules (evaluated in order):
    1. STOP_WINNER: min(expected_loss) < config.loss_threshold, meaning one
       variant is clearly better than all others.
    2. STOP_EQUIVALENT (ROPE): If config.rope is set and the posterior
       probability that the difference between the best and worst variant
       falls within ROPE is > 0.95, stop as equivalent.
    3. CONTINUE: Otherwise, keep running.
    """
    ...
    min_loss = min(losses)

    # Rule 1: STOP_WINNER when expected loss of the best arm is below threshold
    if min_loss < config.loss_threshold:
        return BayesianDecision.STOP_WINNER

    # Rule 2: STOP_EQUIVALENT via ROPE (same draws as the loss above)
    if config.rope is not None:
        rope_lower, rope_upper = config.rope
        best_idx = losses.index(min_loss)
        best_samples = samples[best_idx]
        all_in_rope = True
        for i, _ in enumerate(posteriors):
            if i == best_idx:
                continue
            diff = best_samples - samples[i]
            prob_in_rope = float(np.mean((diff >= rope_lower) & (diff <= rope_upper)))
            if prob_in_rope <= 0.95:
                all_in_rope = False
                break
        if all_in_rope:
            return BayesianDecision.STOP_EQUIVALENT

    return BayesianDecision.CONTINUE
```

and the configuration the experiment carries:

```python
# 📁 backend/app/schemas/bayesian.py
class BayesianConfig(BaseModel):
    prior_family: PriorFamily = PriorFamily.BETA
    alpha: float = Field(1.0, gt=0, description="Prior alpha hyperparameter (> 0)")
    beta: float = Field(1.0, gt=0, description="Prior beta hyperparameter (> 0)")
    loss_threshold: float = Field(0.001, gt=0, description="Expected loss threshold for stopping")
    rope: Optional[List[float]] = Field(None, description="Region of Practical Equivalence [lower, upper]")
    credible_level: float = Field(0.95, ...)
```

`loss_threshold = 0.001` means "stop when choosing the best arm risks giving up less than
0.1 percentage points of conversion in expectation." That's a _decision-theoretic_ stopping
rule — it stops when the cost of being wrong is negligible, not when a p-value crosses a
line — and it's the rule the experiment scheduler honours (`bayesian_decision` on the row
drives automatic completion).

> **Watch Out!** The prior matters at small n. Beta(1, 1) is "uninformative" but it's still
> a claim that a 90% conversion rate is as plausible as 4%; with 20 users it pulls the
> posterior toward 50%. For a mature product, an informative prior (Beta(4, 96) for a ~4%
> baseline, say) is more honest and makes early PtBB numbers less excitable. The
> `alpha`/`beta` config fields exist for this; the default is a placeholder.

> **Did You Know?** Bayesian methods don't have a "peeking problem" in the frequentist
> sense — the posterior is what it is at every look. But a _stopping rule_ on the posterior
> (like `loss_threshold`) still has operating characteristics you should know: with an
> uninformative prior and an aggressive threshold, the rule can stop early on noise more
> often than an α = 0.05 frequentist would tolerate. That's why the platform shows the
> Bayesian block _alongside_ the sequential one rather than instead of it.

### Try It Yourself

```python
>>> from backend.app.services.bayesian_service import compute_posterior, compute_probability_to_be_best, compute_expected_loss, compute_bayes_factor, get_bayes_factor_label, should_stop
>>> from backend.app.schemas.bayesian import BayesianConfig
>>> prior = {"alpha": 1, "beta": 1}
>>> c = compute_posterior(prior, {"conversions": 40, "total": 1000}); t = compute_posterior(prior, {"conversions": 52, "total": 1000})
>>> compute_probability_to_be_best([c, t], seed=1)               # e.g. [0.10, 0.90]
>>> compute_expected_loss([c, t], seed=1)                          # loss of picking each
>>> bf = compute_bayes_factor(prior, t, null_value=0.04); round(bf, 2), get_bayes_factor_label(bf)
>>> should_stop([c, t], BayesianConfig(loss_threshold=0.001), seed=1)
>>> should_stop([c, t], BayesianConfig(loss_threshold=0.001, rope=[-0.005, 0.005]), seed=1)
>>> # ten times the data
>>> c10 = compute_posterior(prior, {"conversions": 400, "total": 10000}); t10 = compute_posterior(prior, {"conversions": 520, "total": 10000})
>>> compute_probability_to_be_best([c10, t10], seed=1), should_stop([c10, t10], BayesianConfig(), seed=1)
```

Then set an informative prior (`{"alpha": 4, "beta": 96}`) and rerun the n = 1,000 case —
PtBB moves toward 0.5, as it should with weak data.

### Key takeaways

- Beta-Binomial: posterior = Beta(α + c, β + n − c); credible intervals are probability
  statements.
- PtBB and expected loss by seeded Monte Carlo from one shared sample matrix; the Bayes
  factor (Savage–Dickey) labelled on the Jeffreys scale.
- Stopping: `STOP_WINNER` when min expected loss < `loss_threshold`; `STOP_EQUIVALENT` when
  every arm is within the ROPE with > 95% probability; the scheduler acts on it.
- The prior matters at small n; the block sits beside the frequentist ones.

---

## Hour 9 — Multi-armed bandits: Thompson Sampling, UCB1, ε-greedy — and what they cost

### What & why

An A/B test spends the whole run showing half the users the worse variant, so that at the
end you _know_ which is better. A **multi-armed bandit** spends the run learning _and_
exploiting: it shifts traffic toward arms that look better as evidence accumulates, so fewer
users see the loser. The price is inference: the arms are no longer exchangeable (later
users are routed by earlier outcomes), so the clean causal estimate an A/B test gives is
gone, and SRM warnings are expected rather than alarming.

Three algorithms, one interface (`compute_weights(variant_stats) → {variant_id: weight}`):

- **Thompson Sampling** — Bayesian: draw θ_i from each arm's Beta posterior, pick the max;
  repeat 10,000 times; the arm's weight is how often it won. Naturally explores uncertain
  arms and exploits confident ones. Stochastic (seeded here).
- **UCB1** — frequentist: score each arm by `mean + √(2·ln N / n_i)`; the second term is an
  optimism bonus that shrinks as an arm is pulled. Unplayed arms get +∞ so every arm is tried.
  Deterministic given counts.
- **ε-greedy** — the simplest: the empirically best arm gets `1 − ε + ε/n`, every other arm
  `ε/n`. Deterministic; explores a fixed fraction forever.

### How Experimently does it

Thompson Sampling — the posterior per arm, then the Monte Carlo from Hour 8 used as a
policy:

```python
# 📁 backend/app/services/bandit_service.py
class ThompsonSampling:
    """
    Bayesian MAB using Beta distribution posteriors.

    Each variant is modelled as a Bernoulli arm.  The posterior after
    observing ``s`` successes and ``f`` failures starting from an
    uninformative Beta(1, 1) prior is Beta(1+s, 1+f).

    We estimate the *selection probability* for each arm by drawing
    ``n_samples`` samples from every posterior and counting which arm
    produces the largest draw on each trial.
    """

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

    @staticmethod
    def compute_weights(variant_stats: List[VariantStats], seed: Optional[int] = None) -> Dict[str, float]:
        if len(variant_stats) == 1:
            return {variant_stats[0].variant_id: 1.0}

        alpha: List[float] = []
        beta_vals: List[float] = []
        for vs in variant_stats:
            # Posterior: Beta(prior_alpha + successes, prior_beta + failures)
            alpha.append(ThompsonSampling.PRIOR_ALPHA + vs.successes)
            beta_vals.append(ThompsonSampling.PRIOR_BETA + vs.failures)

        probs = ThompsonSampling.sample(alpha, beta_vals, ThompsonSampling.N_SAMPLES, seed=seed)
        return {vs.variant_id: p for vs, p in zip(variant_stats, probs)}
```

Notice the weight _is_ the probability to be best — Thompson Sampling routes traffic in
proportion to each arm's chance of being optimal, which is why it explores uncertain arms
(they win some draws) and converges on the winner (it wins almost all).

UCB1, with the infinity rule for unplayed arms:

```python
# 📁 backend/app/services/bandit_service.py
class UCB1:
    """
    Frequentist MAB using the UCB1 rule.

    Score for arm *i* after *n_i* pulls out of *N* total pulls::

        score_i = mean_reward_i + sqrt(2 * ln(N) / n_i)

    An arm that has never been pulled receives a score of +∞ so that it is
    tried at least once.
    """
    ...
    @staticmethod
    def compute_weights(variant_stats: List[VariantStats]) -> Dict[str, float]:
        ...
        scores = UCB1.compute_scores(counts, rewards, total_pulls)

        # Separate infinite from finite scores
        inf_indices = [i for i, s in enumerate(scores) if math.isinf(s)]
        ...
        if inf_indices:
            # Distribute weight equally among unplayed arms
            inf_weight = 1.0 / len(inf_indices)
            ...
        # All arms played: normalise finite scores
        total_score = sum(fin_scores)
        ...
        return {vs.variant_id: scores[i] / total_score for i, vs in enumerate(variant_stats)}
```

(Classical UCB1 pulls _only_ the top-scoring arm; the platform normalises scores into
weights so a fleet of concurrent requests spreads across arms proportionally — a practical
adaptation for a system that assigns thousands of users per tick, not one at a time.)

ε-greedy:

```python
# 📁 backend/app/services/bandit_service.py
class EpsilonGreedy:
    """
    Simple ε-greedy explore-exploit strategy.

    * Best arm (highest empirical mean):  weight = (1 - ε) + ε/n
    * All other arms:                     weight = ε / n

    Ties are broken by the first arm in the list.
    """

    DEFAULT_EPSILON: float = 0.1
```

The scheduler computes weights from the counts (sourced DynamoDB → Postgres → BanditState →
priors; Build book Day 19), seeds Thompson from `derive_seed(experiment_id, day, 10_000)`,
persists `BanditState.variant_weights`, and estimates the regret reduction the dashboard
shows:

```python
# 📁 backend/app/core/bandit_scheduler.py
    def estimate_regret_reduction(self, weights: Dict[str, float], variant_stats: Dict[str, VariantStats]) -> float:
        """
        Estimate percentage regret reduction vs. uniform allocation.

        Regret is defined as the opportunity cost of not always showing
        the best variant.  We compare:
        - Expected reward under current weights
        - Expected reward under uniform allocation
        """
        ...
        # Expected reward under current bandit weights
        bandit_reward = sum(weights.get(vid, 0.0) * rates.get(vid, 0.0) for vid in weights)

        # Expected reward under uniform allocation
        n = len(weights)
        ...
        uniform_weight = 1.0 / n
        uniform_reward = sum(uniform_weight * rates.get(vid, 0.0) for vid in weights)
```

And `/tracking/assign` routes _new_ users by those weights — deterministically per user, with
sticky rows still winning (Build book Day 4):

```python
# 📁 backend/app/api/v1/endpoints/tracking.py
def _select_bandit_variant(experiment: Experiment, bandit_state: Optional[BanditState], user_id: str) -> Optional[uuid.UUID]:
    """Pick a variant for a *new* user according to the bandit weights.

    Deterministic per ``(user_id, experiment.id)``: the user is hashed into a
    bucket in ``[0, 1)`` and the bucket is looked up in the cumulative
    distribution of the normalised weights, so repeated calls for the same
    user return the same variant before the sticky assignment row exists.
    """
```

#### What a bandit costs you

Say it plainly, because the results page can't: **a bandit's per-arm conversion rates are
biased estimates.** Users routed later are routed _because_ of earlier outcomes; if an arm
had a lucky start it gets more traffic and its later (regression-to-the-mean) users pull its
rate down — or a temporal effect (weekend vs weekday) lands unevenly across arms. Thompson
Sampling's exploration mitigates this but doesn't eliminate it. The honest uses of a bandit:
(1) a short-lived decision where optimising during the run matters more than knowing the
effect size afterwards (a promotion banner this week); (2) many arms where an A/B test's
equal split would waste most users on bad options; (3) a "always-on" optimisation that
never needs a report. For a decision you'll cite in a quarterly review, run an A/B test.

> **Watch Out!** Because a bandit is _supposed_ to drift from the configured split, the SRM
> check is skipped for `optimization_type != "fixed"`. That also means the one exchangeability
> alarm the platform has is off. If you need to detect a broken randomiser on a bandit, watch
> the weights the scheduler stores against what the assignment counts show — they should
> track.

> **Did You Know?** The bandit that "never learned" for months (Build book, Day 1) produced
> weights that were exactly uniform — indistinguishable on the dashboard from a healthy
> bandit whose arms happened to be equal. The `regret_reduction` estimate being 0.0% forever
> was the only visible symptom, and nobody was watching it. A metric that can't distinguish
> "broken" from "no signal" needs a companion (`pulls > 0`), which the `_has_pulls` fallback
> check now provides.

### Try It Yourself

```python
>>> from backend.app.services.bandit_service import VariantStats, ThompsonSampling, UCB1, EpsilonGreedy
>>> stats = [VariantStats("a", successes=40, failures=960, pulls=1000, total_reward=40),
...          VariantStats("b", successes=52, failures=948, pulls=1000, total_reward=52),
...          VariantStats("c", successes=0, failures=0, pulls=0, total_reward=0)]
>>> ThompsonSampling.compute_weights(stats, seed=1)      # c has Beta(1,1): wide, wins a fair share of draws
>>> UCB1.compute_weights(stats)                          # c unplayed → +∞ → all weight
>>> EpsilonGreedy.compute_weights(stats)                 # b best → 0.9 + 0.1/3; others 0.1/3
>>> stats[2] = VariantStats("c", successes=3, failures=97, pulls=100, total_reward=3)
>>> ThompsonSampling.compute_weights(stats, seed=1), UCB1.compute_weights(stats)
```

Then simulate a bandit run: 30 days, 1,000 users/day, true rates (0.04, 0.052, 0.03),
recompute Thompson weights each day from cumulative counts, and plot the share of traffic
each arm receives over time — and the _observed_ rate of arm b vs its true 0.052, to see
the bias.

### Key takeaways

- Thompson: weight = probability to be best from seeded Beta draws; UCB1: `mean + √(2 ln N /
  n_i)`, ∞ for unplayed, normalised; ε-greedy: best gets `1 − ε + ε/n`.
- The scheduler recomputes weights each tick from real counts (DynamoDB → Postgres →
  BanditState → priors), persists them, estimates regret reduction; `/assign` routes new
  users by them deterministically.
- Bandits trade inference for reward: rates are biased, SRM is skipped, exchangeability is
  gone — use them to optimise, not to measure.

---

## Part III Workshop

**Q1. Why is θ estimated on the control group only?**
The treatment might change the Y–X relationship; a pooled θ could then absorb part of the
treatment effect into the adjustment. Control-only θ can't bias the effect, only reduce
noise.

**Q2. A covariate measured during the experiment has ρ = 0.8 with the outcome. Should you
CUPED on it?**
No. Post-treatment covariates can be affected by the treatment; adjusting on them introduces
bias. CUPED requires pre-experiment X.

**Q3. What is a Beta(4, 96) prior saying, and when would you use it over Beta(1, 1)?**
"About 3 prior successes in ~98 prior trials" — a ~4% rate with modest confidence. Use it
for a mature product whose baseline is known, so early PtBB numbers aren't excitable.

**Q4. PtBB = 0.93 for B. Is that "B is 93% likely to be the better choice"?**
Yes — that's exactly what it is (given the model and prior), which is why it's the number
PMs want. A p-value of 0.07 would not have meant that.

**Q5. `should_stop` returns `STOP_EQUIVALENT`. What was true?**
For every non-best arm, the posterior difference from the best arm fell inside the ROPE with
probability > 0.95 — the arms are practically equivalent; stop and pick on other grounds.

**Q6. Why are bandit conversion rates biased, and what does the platform do about SRM for
bandits?**
Later users are routed by earlier outcomes, so arms aren't exchangeable and temporal effects
land unevenly; regression to the mean after a lucky start distorts rates. The SRM check is
skipped for non-fixed experiments because drift is intended.

**Quiz.** Thompson weight for an arm with Beta(1, 1) among arms with Beta(41, 961) and
Beta(53, 949)?
→ Substantial (often 30–50%): its wide posterior wins many draws. That's exploration —
and it's why an unplayed arm is tried quickly.

---

## Exercises

**E3.1 — Variance reduction.** Generate correlated (X, Y) with ρ ≈ 0.6 and a true lift; run
`compute_cuped_effect`; report the variance-reduction percentage and check it's ≈ ρ² · 100.
Then show the effect estimate is unchanged when you shift X by a constant.

**E3.2 — Winsorize or not.** Revenue per user: 99% of users spend ~N(20, 5), 1% spend
~N(2,000, 500). Compare the raw SE of the treatment–control difference with the SE after
`apply_winsorization(values, 99)`, and state what the estimand became.

**E3.3 — Prior sensitivity.** For 8 conversions in 100 vs 12 in 100, compute PtBB under
Beta(1, 1) and Beta(4, 96); explain the direction of the change.

**E3.4 — Stopping rule.** With `loss_threshold=0.001`, find (by simulation with seed 5) the
smallest n per arm at which 4% vs 5.2% true rates produce `STOP_WINNER` more than half the
time.

**E3.5 — Bandit bias.** Simulate 20 days of a two-arm Thompson bandit (true rates 0.04,
0.04 — _no difference_), 2,000 users/day, weights recomputed daily; report the final
observed rates and traffic shares, and explain why the winner's observed rate is likely
above 0.04.

---

## Solutions

**S3.1**

```python
import numpy as np
from backend.app.services.cuped_service import CupedService
rng = np.random.default_rng(11); n = 4000
X = rng.normal(100, 30, 2*n); Y = 0.6 * X + rng.normal(0, 24, 2*n)      # corr ≈ 0.6
Y[n:] += 2.0
e1 = CupedService.compute_cuped_effect(Y[:n], X[:n], Y[n:], X[n:])
e2 = CupedService.compute_cuped_effect(Y[:n], X[:n] + 500, Y[n:], X[n:] + 500)
round(e1.variance_reduction_pct, 1), round(np.corrcoef(Y[:n], X[:n])[0, 1] ** 2 * 100, 1)   # ≈ 36 and ≈ 36
round(e1.adjusted_effect, 3) == round(e2.adjusted_effect, 3)                                    # shift-invariant
```

**S3.2**

```python
rng = np.random.default_rng(2); n = 10000
def rev(): 
    v = rng.normal(20, 5, n); big = rng.random(n) < 0.01; v[big] = rng.normal(2000, 500, big.sum()); return v
c, t = rev(), rev()
raw_se = np.sqrt(c.var(ddof=1)/n + t.var(ddof=1)/n)
cw, tw = CupedService.apply_winsorization(c, 99), CupedService.apply_winsorization(t, 99)
win_se = np.sqrt(cw.var(ddof=1)/n + tw.var(ddof=1)/n)
raw_se, win_se      # the winsorized SE is several times smaller
```

The estimand is now "mean revenue with each user's spend capped at the 99th percentile" —
a robust mean, not total revenue; a treatment that _only_ changes whales is invisible to it.

**S3.3** — Beta(1,1): posteriors Beta(9, 93) vs Beta(13, 89), PtBB(B) ≈ 0.82. Beta(4, 96):
Beta(12, 188) vs Beta(16, 184), PtBB(B) ≈ 0.77. The informative prior pulls both toward 4%
and shrinks the difference, so B's advantage looks less certain — correct for 100 users.

**S3.4**

```python
from backend.app.services.bayesian_service import compute_posterior, should_stop, BayesianDecision
from backend.app.schemas.bayesian import BayesianConfig
rng = np.random.default_rng(5); cfg = BayesianConfig(loss_threshold=0.001); prior = {"alpha": 1, "beta": 1}
for n in (500, 1000, 2000, 4000, 8000):
    stops = 0
    for _ in range(200):
        c = compute_posterior(prior, {"conversions": int(rng.binomial(n, 0.04)), "total": n})
        t = compute_posterior(prior, {"conversions": int(rng.binomial(n, 0.052)), "total": n})
        stops += should_stop([c, t], cfg, seed=5) == BayesianDecision.STOP_WINNER
    print(n, stops / 200)
```

The crossing point is in the low thousands per arm for this lift and threshold — far fewer
users than the frequentist power calculation asks for, which is the double-edged
convenience of an expected-loss rule: it stops when being wrong is _cheap_, not when the
effect is _certain_.

**S3.5**

```python
from backend.app.services.bandit_service import VariantStats, ThompsonSampling
rng = np.random.default_rng(9); s = {"a": [0, 0], "b": [0, 0]}; w = {"a": 0.5, "b": 0.5}
for day in range(20):
    for arm, share in w.items():
        n = int(2000 * share); c = rng.binomial(n, 0.04); s[arm][0] += c; s[arm][1] += n - c
    stats = [VariantStats(k, successes=v[0], failures=v[1], pulls=v[0] + v[1]) for k, v in s.items()]
    w = ThompsonSampling.compute_weights(stats, seed=day)
{k: (round(v[0] / (v[0] + v[1]), 4), v[0] + v[1]) for k, v in s.items()}, w
```

With no true difference, whichever arm got a lucky first few days attracted more traffic; its
later users regress toward 0.04 but its cumulative rate keeps the early bump, and the other
arm — starved of traffic — has a noisier estimate. The "winner" typically shows a rate a
little above 0.04 and most of the traffic: the bias that makes bandit rates unfit for
reporting an effect.

---

**Next:** [Part IV — Many Metrics, Many Segments →](./part-4-many-comparisons.md)

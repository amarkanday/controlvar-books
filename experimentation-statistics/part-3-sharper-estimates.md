# Part III — Sharper Estimates

### Hours 7–9

[← Part II](./part-2-sequential.md) · [Contents](./README.md) · [Part IV →](./part-4-many-comparisons.md)

Part I asked "is there a difference?"; Part II asked "when may I look?". This Part asks
three sharper questions. Can pre-experiment data make the same users tell you more — CUPED
(Hour 7)? Can we answer the question the product manager actually asked, "what's the
probability B is better, and what do I lose if I'm wrong?" — the Bayesian analysis (Hour 8)?
And can the experiment _optimise while it runs_, sending more users to the better arm —
bandits, and what that costs in inference (Hour 9)? The Part ends with the warehouse
analysis, which computes the same results from per-variant sums without the raw rows ever
leaving the warehouse.

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
usable interval. Controlvar stores a winsorization setting and applies it nowhere: the library
function below is the method, and no results view calls it.

### How Controlvar does it

There are two layers, and it pays to keep them apart. The first is a small teaching library,
`CupedService`, which does CUPED on raw per-user arrays. The second is the estimator behind the
`GET /results/{id}/cuped` route, which works from per-arm sums. The route does not call the
library; the library is still worth reading because it is the method in its plainest form, and
the Try It runs it.

#### The library primitives

θ from the control group, applied to both:

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

Estimating θ on **control only** is a deliberate conservative choice in the library: the
treatment might change the Y–X relationship, and a θ fit on pooled data could then leak a little
treatment effect into the adjustment. Control-only θ can't. `E[X]` is taken from the pooled data,
which is correct because X is pre-treatment and its mean is the same in both arms.

The full pipeline, with the variance-reduction number it reports:

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

#### What the platform's CUPED route does

The route, `GET /results/{id}/cuped`, answers the question for an experiment with real data. Its
numbers are labelled `ga` in the analysis-status table (Part IV Hour 12); the route itself stays
`x-stability: beta` while its response shape is still being settled, so read the contract before
you build on it. Older copies of this book described a placeholder covariate (each user's
position in assignment order) that reduced almost no variance. That is gone: the covariate is now
each user's own history.

- **Y** is whether the user is a converting user, matched like every other analysis path
  (`converting_user_ids`, Part I Hour 2). The route analyses **proportion (conversion) metrics**;
  any other metric type is listed with `unavailable_reason: "not_a_proportion_metric"`.
- **X** is a 0/1 covariate: whether the user sent the covariate event in the
  `covariate_lookback_days` before their assignment. The event is the metric's own `event_name`,
  or that of the metric named by `covariate_metric_id`. The read is guarded twice, in
  `covariate_user_ids`:

  ```python
  # 📁 backend/app/services/event_matching.py
              if assigned - lookback <= happened < assigned and received_at < assigned:
                  found.add(user_id)
  ```

  The event must have _happened_ in `[assigned_at − lookback, assigned_at)` (an event at the
  moment of assignment is not history), **and** the server must have _received_ it before the
  assignment (`events.updated_at`). History sent after the user was assigned is not read for that
  user, so a client that back-fills "old" events cannot leak the treatment into the covariate.
- **The estimator is a regression, from sums.** `cuped_metric_result`, in
  `sufficient_stats_analysis.py`, fits `y ~ C(arm) + x` by ordinary least squares using five sums
  per arm (`n`, Σy, Σx, Σy², Σx², Σxy). So a caller with the same sums from somewhere else — a
  warehouse — gets the same numbers. The key lines:

  ```python
  # 📁 backend/app/services/sufficient_stats_analysis.py
      pooled_sxx = sum(a.sxx for a in parsed)
      pooled_sxy = sum(a.sxy for a in parsed)
      scale = max(1.0, sum(abs(a.sum_x) for a in parsed))
      theta = pooled_sxy / pooled_sxx if adjust and pooled_sxx > 1e-12 * scale else 0.0
  ```

  and, per arm, `adjusted_mean = y_mean - theta * (x_mean - x_grand)`. Note what differs from the
  library: θ is the **pooled within-arm slope over every arm**, not the control's alone. That
  is the regression's own coefficient on `x`, and it makes the adjusted means comparable. The
  price is the one the library avoided: an arm whose own Y–X relationship differs can come out
  _noisier_ after adjustment, so `variance_reduction_pct` **can be negative**, and adding an arm
  changes the other arms' adjusted effects.
- **Judging.** The effect is the treatment's adjusted mean minus the control's, with a
  two-sided z-test and an interval at the experiment's stored `confidence_level`;
  `corrected_p_value` applies its stored `correction_method` across the metric's treatments
  (Part IV Hour 10). `variance_reduction_pct` is
  `100 · (1 − Var(adjusted effect) / Var(unadjusted effect))`.
- **Reasons, not guesses.** A comparison that cannot be computed is listed with
  `unavailable_reason` and null numbers: `fewer_than_2_units`, `no_variation` (neither arm's
  outcome varies), `no_residual_variation` (the covariate explains everything, so the standard
  error would be 0 and a p-value would claim certainty the data cannot give),
  `no_control_variant`, `no_treatment_variant`, `covariate_metric_not_found`,
  `metric_has_no_event_name`, `result_invalid`. When nobody has covariate events, or everybody
  does, X does not vary within any arm and θ is exactly 0: the adjusted numbers equal the
  unadjusted ones. `covariate_coverage_pct` tells you how many of the comparison's users had
  any covariate event; a low figure is your warning that CUPED has little to work with.
- **The config** is the experiment's `variance_reduction_config`: `method` (`none`, `cuped`,
  `cuped_plus`, `winsorization`), `covariate_metric_id`, `covariate_lookback_days` (1–90,
  default 7) and `winsorization_percentile` (50–100, default 99). `none` means θ is exactly 0.
  `cuped_plus` is computed as `cuped` and each row's `method` says `cuped`. `winsorization` is
  **accepted and stored, and applied by no results view** (#1739). This route analyses
  proportions only and lists each one as `winsorization_needs_mean_metric` rather than clip a 0/1
  outcome to zero. The views that do analyse a mean — `/results`' revenue, count, duration and
  custom metrics (Part I Hour 2) — do not read the setting either, so a metric's numbers are the
  same whatever `winsorization_percentile` says, and one very large spender moves a revenue
  mean and widens its interval in full.
- Only the **API** carries it. The dashboard shows no CUPED figure.

`CupedService.apply_cuped_ratio` (a ratio-metric variant that ignores X) and the other library
helpers are not called by any route; there is no working ratio-metric CUPED (CUPED++) yet.

**Post-stratification** (`post_stratification_service.py`) is the sibling technique:
instead of a continuous covariate, stratify users by a categorical one (platform, country)
and reweight each stratum's estimate to the population proportions — more powerful than CUPED
when arm sizes differ within strata. The service implements the estimator, but nothing builds
its per-user rows from an experiment's assignments and events yet, so
`POST /results/{id}/post-stratification` answers **501** "not available yet" for every
existing experiment (404 for an unknown one, 422 for a malformed body). It used to answer
with numbers generated at random (issue #443); the 501 replaced them.

> **Watch Out!** X must be measured **before** assignment. A "covariate" observed during the
> experiment (sessions this week) can be affected by the treatment, and adjusting on it
> _introduces_ bias — the classic post-treatment-adjustment mistake. A zero-variance or
> uncorrelated X gives θ ≈ 0 and no reduction, which is the honest outcome. The route's
> `received_at < assigned` guard exists for exactly this reason.

> **Did You Know?** New users have no pre-period data. The usual treatment is to give them
> the covariate mean, so their adjustment is zero — they still count, they just don't
> benefit. The route's 0/1 covariate does this naturally: a user with no history has X = 0. A
> product with mostly new users gets little from CUPED (look at `covariate_coverage_pct`); a
> product with returning users gets a lot.

### Try It Yourself

The library, on continuous data:

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
(0.59, 3.36, 0.3, 55.6)
>>> # the raw comparison for contrast
>>> raw_se = ((Y[:n].var(ddof=1) / n) + (Y[n:].var(ddof=1) / n)) ** 0.5; round(raw_se, 3)
0.451
>>> CupedService.apply_winsorization(np.array([1, 2, 3, 1000.0]), 75).tolist()
[1.0, 2.0, 3.0, 252.25]
```

A 56% variance reduction shrinks the standard error from 0.451 to 0.300 — the same as
running with about 2.3 times the users. Then replace X with something unrelated
(`X2 = rng.normal(size=2*n)`, continuing the same generator): θ is noise (0.29 here, tiny on
a Y whose standard deviation is about 23) and the reduction is 0.02% — what a covariate with
no relationship to the outcome gives you.

Now the estimator the route uses, on a 0/1 covariate ("bought in the 7 days before assignment")
and a 0/1 outcome ("bought during the test"), from sums:

```python
>>> import numpy as np
>>> from backend.app.services.sufficient_stats_analysis import cuped_metric_result, BinomialVariant
>>> class M: id = "m"; name = "purchase"; is_primary = True
>>> C, T = BinomialVariant("c", "control", True), BinomialVariant("t", "treatment", False)
>>> rng = np.random.default_rng(21)
>>> def arm(n, lift):
...     x = rng.random(n) < 0.30                                  # past buyers
...     y = rng.random(n) < np.where(x, 0.30, 0.04) + lift       # past buyers convert far more often
...     x, y = x.astype(float), y.astype(float)
...     return n, y.sum(), x.sum(), (y * y).sum(), (x * x).sum(), (x * y).sum()
>>> nC, *sc = arm(20000, 0.0); nT, *st = arm(20000, 0.01)
>>> for adjust in (False, True):
...     r = cuped_metric_result([(C, nC, *sc), (T, nT, *st)], 0.95, "none", metric=M, adjust=adjust)[0]
...     print(adjust, round(r["adjusted_effect"], 4), round(r["adjusted_se"], 5), r["theta"], round(r["variance_reduction_pct"], 1))
False 0.0116 0.00325 0.0 0.0
True 0.0101 0.00302 0.2595... 13.4
```

Two things to notice. The standard error falls from 0.00325 to 0.00302 (13.4% less variance): a
binary covariate that only half-explains a rare outcome buys a modest reduction. And the
_estimate_ moved, from 0.0116 to 0.0101, because the two arms drew slightly different shares of
past buyers by chance; CUPED corrects for that imbalance as well as for noise, which is a second
reason it is worth having. The `adjust=False` row, with `theta == 0.0` and the unadjusted numbers, is what
`method: "none"` produces.

### Key takeaways

- CUPED: `Y − θ(X − E[X])`, X strictly pre-experiment; same mean, variance × (1 − ρ²).
- The library (`CupedService`) fits θ on the control only and works on arrays; the route fits
  the regression `y ~ C(arm) + x` from per-arm sums, with θ pooled within arms, so
  `variance_reduction_pct` can be negative.
- On the platform X is a 0/1 flag: did the user send the covariate event in the
  `covariate_lookback_days` before assignment, and did the server receive it before the user was
  assigned. Only proportion metrics are analysed; the numbers are `ga`, the route is
  `x-stability: beta`, and the dashboard shows no CUPED figure.
- Winsorize heavy tails (changes the estimand slightly) — by hand: the platform stores
  `winsorization` and clips nothing. Post-stratification for categorical covariates — whose
  route answers 501 until it has real per-user rows.
- Adjusting on a post-treatment variable introduces bias — X must be pre-experiment.

---

## Hour 8 — Bayesian A/B: Beta-Binomial posteriors, probability to be best, expected loss

### What & why

A frequentist p-value answers a question nobody asked ("how surprising is this data if
nothing's going on?"). A product manager asks: **what's the probability B is better than A,
and how much do I stand to lose if I pick wrong?** Bayesian analysis answers those directly by
treating each arm's conversion rate θ as uncertain, expressing that uncertainty as a
distribution, and updating it with data. The answers are only as good as the model and the
prior behind them, which is why this Hour spends time on both.

For conversion rates the maths is a gift: the **Beta** distribution is _conjugate_ to the
binomial, so if the prior on θ is Beta(α, β) and you observe c conversions in n trials, the
posterior is Beta(α + c, β + n − c). Updating is addition. Beta(1, 1) is the uniform
("uninformative") prior; Beta(α, β) can be read as "α − 1 prior successes and β − 1 prior
failures."

From the posteriors, three quantities (the first two by Monte Carlo — the results API draws
100,000 samples from each arm's Beta, then counts):

- **Probability to be best (PtBB)** — the fraction of draws in which arm i has the highest θ.
- **Expected loss** — E[max_j θ_j − θ_i]: how much conversion rate you expect to give up by
  choosing arm i. A tiny expected loss means "even if i isn't best, it's nearly best."
- **Credible interval** — the central 95% of the posterior (the 2.5th to 97.5th percentile; it
  is _equal-tailed_, not the highest-density interval, and the two differ on a skewed
  posterior): an interval that _is_ "95% probability the true rate is in here," which a
  confidence interval is not.

Plus a **stopping rule** built on probability to be best, expected loss and a ROPE (region
of practical equivalence), and — as a library function the API does not call — a **Bayes
factor** (Savage–Dickey) summarising evidence for a point null.

### How Controlvar does it

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
    rng = make_rng(resolve_seed(seed, n_samples, posteriors))
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
consistent (and the seed makes them reproducible across calls — Part IV Hour 12). A call with
no seed does not share one global stream: `resolve_seed(seed, n_samples, posteriors)` derives
a seed from a fingerprint of the posteriors themselves, so a repeated call on the same data
reproduces exactly while different data get independent Monte Carlo noise.
`DEFAULT_N_SAMPLES` — what the results API uses — is **100,000** per variant.

The Bayes factor and its label exist as library functions. Neither `/results` nor
`/results/{id}/bayesian` sets the response's `bayes_factor` field, which is optional and
always `null` today:

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

The stopping rule — a winner must be both **probably best** and **cheap to pick**; a ROPE
means "the arms are practically equivalent, stop":

```python
# 📁 backend/app/services/bayesian_service.py  (abridged)
#: The best variant's probability to be best must reach this before
#: ``STOP_WINNER`` is reported (#242).  An expected-loss threshold alone is
#: crossed by two identical arms once there is modest traffic at a low base
#: rate.  Under an A/A test the best arm's probability to be best is roughly
#: uniform on [0.5, 1], so this threshold puts a single-look false winner near
#: 2 x (1 - 0.975) = 5%.  ...
PROB_BEST_THRESHOLD: float = 0.975


def should_stop(posteriors, config: BayesianConfig, n_samples: int = DEFAULT_N_SAMPLES, seed: Optional[int] = None, *,
                samples=None, losses=None, ptbb=None) -> BayesianDecision:
    """Apply Bayesian stopping rules to determine whether to stop the experiment.

    Stopping rules (evaluated in order):
    1. STOP_WINNER: the variant most likely to be best has a probability to
       be best of at least :data:`PROB_BEST_THRESHOLD` **and** an expected
       loss below ``config.loss_threshold``.  ...
    2. STOP_EQUIVALENT (ROPE): If config.rope is set and the posterior
       probability that the difference between the best and worst variant
       falls within ROPE is > 0.95, stop as equivalent.
    3. CONTINUE: Otherwise, keep running.
    """
    ...
    min_loss = min(losses)

    # Rule 1: STOP_WINNER when the variant most likely to be best is likely
    # enough (#242) and choosing it is cheap enough.  With the threshold above
    # 0.5 at most one variant can qualify.  The leader here is argmax P(best);
    # the ROPE rule below compares against argmin expected loss, as before.
    leader = max(range(len(ptbb)), key=ptbb.__getitem__)
    if ptbb[leader] >= PROB_BEST_THRESHOLD and losses[leader] < config.loss_threshold:
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
# 📁 backend/app/schemas/bayesian.py  (abridged)
class BayesianConfig(BaseModel):
    # Every number must be finite: the config is stored as JSONB, which cannot
    # hold NaN or an infinity, so those answer 422 rather than reaching the
    # database ...
    model_config = ConfigDict(allow_inf_nan=False)

    prior_family: PriorFamily = PriorFamily.BETA
    alpha: float = Field(1.0, gt=0, description="Prior alpha hyperparameter (> 0)")
    beta: float = Field(1.0, gt=0, description="Prior beta hyperparameter (> 0)")
    loss_threshold: float = Field(0.001, gt=0, description=(
        "Expected loss the leading variant must be below for STOP_WINNER. "
        "STOP_WINNER also needs that variant's probability to be best to "
        "be at least 0.975, a fixed threshold that is not configurable."))
    rope: Optional[List[float]] = Field(None, description="Region of Practical Equivalence [lower, upper]")
    credible_level: float = Field(0.95, gt=0, lt=1, description="Credible interval level in (0, 1)")
```

`loss_threshold = 0.001` means "stop only if choosing the leader risks giving up less than
0.1 percentage points of conversion in expectation." The 0.975 floor on probability to be
best is what makes the rule safe: before it was added (issue #242), two _identical_ arms at a
1% base rate with 5,000 users each crossed the loss threshold every time, so an A/A test
"found" a winner on every look. With the floor, the same A/A runs say `STOP_WINNER` about 5%
of the time (the Try It measures it).

**It is a recommendation, not an automation.** The decision is returned as `decision` on
`GET /results/{id}/bayesian` (and in `bayesian_results` on `/results`) and recorded on the
experiment as `bayesian_decision`, which is response-only — a PUT cannot set it. Nothing
stops an experiment on it: the scheduler's comment says so in as many words. A human reads
the recommendation and ends the experiment.

**Turning it on.** Bayesian analysis is enabled through the experiments API:
`bayesian_enabled: true` on create or update, with an optional `bayesian_config` (omitted,
the default `BayesianConfig` is stored). A cloned experiment keeps its analysis settings.
It reads converting users on the primary metric (else the first) whatever the metric's type,
so a revenue primary metric gets a Beta-Binomial on the share of users who bought, not on
revenue per user.

**Lower is better.** Since engine 1.5.0 the analysis reads the primary metric's
`lower_is_better` (Part I Hour 2), and the way it does so keeps the seed meaningful:

```python
# 📁 backend/app/services/bayesian_service.py
        # One matrix of draws feeds PtBB, expected loss and the ROPE check.
        samples = _draw_posterior_samples(posteriors, n_samples, seed)
        if lower_is_better:
            # Negated after the seeded draw: the same stream, best = lowest.
            samples = -samples
        ptbb = _ptbb_from_samples(samples)
        losses = _expected_loss_from_samples(samples)
```

The draws are made exactly as a higher-is-better analysis makes them and negated afterwards, so
the random stream is unchanged and only the meaning of "best" moves. The probability to be best
becomes the probability of having the lowest rate; the expected loss becomes how far a variant's
rate is above the lowest, still non-negative and in the metric's own units, so the 0.975 floor
and `loss_threshold` keep their meaning. The ROPE is compared with the best variant's advantage
in the good direction. The posteriors and credible intervals are the rates themselves, unchanged.

> **Watch Out!** The prior matters at small n. Beta(1, 1) is "uninformative" but it's still
> a claim that a 90% conversion rate is as plausible as 4%; with 20 users it pulls the
> posterior toward 50%. For a mature product, an informative prior (Beta(4, 96) for a ~4%
> baseline, say) can be more honest and makes early PtBB numbers less excitable — provided you
> can justify it from real prior data, and write it down. The `alpha`/`beta` config fields exist
> for this; the default is a placeholder, so check how sensitive an early probability-to-be-best
> is to the choice.

> **Did You Know?** Bayesian methods don't have a "peeking problem" in the frequentist
> sense — the posterior is what it is at every look. But a _stopping rule_ on the posterior
> still has operating characteristics. The 0.975 floor was calibrated so that a **single
> look** at an A/A test calls a false winner about 5% of the time (2 × (1 − 0.975));
> `backend/tests/unit/stats_validation/test_bayesian_stop_winner.py` holds it there across
> base rates of 1%, 10% and 50%. Look every day and act on the first `STOP_WINNER`, and the
> rate inflates exactly as Part II Hour 4 describes. If you will peek, the mSPRT is the
> peeking-safe verdict. (The dashboard shows the Bayesian block, with its decision, at the end of
> the Overview tab for the primary metric, and adds an SRM qualifier when the sample-ratio check
> has failed; the numbers are the ones `GET /results/{id}/bayesian` returns.)

### Try It Yourself

```python
>>> from backend.app.services.bayesian_service import compute_posterior, compute_probability_to_be_best, compute_expected_loss, compute_bayes_factor, get_bayes_factor_label, should_stop
>>> from backend.app.schemas.bayesian import BayesianConfig
>>> prior = {"alpha": 1, "beta": 1}
>>> c = compute_posterior(prior, {"conversions": 40, "total": 1000}); t = compute_posterior(prior, {"conversions": 52, "total": 1000})
>>> compute_probability_to_be_best([c, t], seed=1)
[0.09847, 0.90153]
>>> compute_expected_loss([c, t], seed=1)                          # loss of picking each
[0.0125..., 0.00044...]
>>> bf = compute_bayes_factor(prior, t, null_value=0.04); round(bf, 3), get_bayes_factor_label(bf)
(0.098, 'Anecdotal')
>>> should_stop([c, t], BayesianConfig(loss_threshold=0.001), seed=1)   # loss 0.00044 < 0.001, but P(best) 0.90 < 0.975
<BayesianDecision.CONTINUE: 'CONTINUE'>
>>> should_stop([c, t], BayesianConfig(loss_threshold=0.001, rope=[-0.005, 0.005]), seed=1)
<BayesianDecision.CONTINUE: 'CONTINUE'>
>>> # ten times the data
>>> c10 = compute_posterior(prior, {"conversions": 400, "total": 10000}); t10 = compute_posterior(prior, {"conversions": 520, "total": 10000})
>>> compute_probability_to_be_best([c10, t10], seed=1), should_stop([c10, t10], BayesianConfig(), seed=1)
([0.0, 1.0], <BayesianDecision.STOP_WINNER: 'STOP_WINNER'>)
```

At n = 1,000 the expected-loss condition alone would have stopped; the probability floor is
what says "not yet". Then set an informative prior (`{"alpha": 4, "beta": 96}`) and rerun the
n = 1,000 case — PtBB moves toward 0.5 (0.895 here), as it should with weak data.

The direction, on an error metric where the treatment's rate fell from 5.2% to 3.0%:

```python
>>> from backend.app.services.bayesian_service import BayesianService
>>> errors = [{"conversions": 52, "total": 1000}, {"conversions": 30, "total": 1000}]
>>> BayesianService().analyze(errors, seed=1)["probability_to_be_best"], BayesianService().analyze(errors, seed=1, lower_is_better=True)["probability_to_be_best"]
([0.99357, 0.00643], [0.00643, 0.99357])
```

The same draws, negated: the two probabilities swap, and the decision (`STOP_WINNER` both times)
now points at the variant with fewer errors.

And the A/A calibration in miniature — two identical arms at 1%, 5,000 users each:

```python
>>> import numpy as np
>>> rng = np.random.default_rng(0); cfg = BayesianConfig(); stop = loss_only = 0
>>> for i in range(500):
...     a = compute_posterior(prior, {"conversions": int(rng.binomial(5000, 0.01)), "total": 5000})
...     b = compute_posterior(prior, {"conversions": int(rng.binomial(5000, 0.01)), "total": 5000})
...     stop += should_stop([a, b], cfg, n_samples=20_000, seed=i).value == "STOP_WINNER"
...     loss_only += min(compute_expected_loss([a, b], n_samples=20_000, seed=i)) < cfg.loss_threshold
>>> stop / 500, loss_only / 500
(0.052, 1.0)
```

### Key takeaways

- Beta-Binomial: posterior = Beta(α + c, β + n − c); credible intervals are probability
  statements.
- PtBB and expected loss by seeded Monte Carlo from one shared sample matrix; the Bayes
  factor (Savage–Dickey, Jeffreys labels) is available as a function.
- Stopping: `STOP_WINNER` when the leader's P(best) ≥ 0.975 (fixed) **and** its expected
  loss < `loss_threshold`; `STOP_EQUIVALENT` when every arm is within the ROPE with > 95%
  probability; otherwise `CONTINUE`. 100,000 draws per variant in the API.
- The decision is a recommendation recorded on the experiment; nothing acts on it. The
  dashboard shows it on the Overview tab. The Bayes factor is a library function the API does
  not fill.
- The prior matters at small n; enable the analysis with `bayesian_enabled` and
  `bayesian_config`.
- It follows the primary metric's `lower_is_better` by negating the seeded draws, and it
  analyses the share of users who converted whatever the metric's type.

---

## Hour 9 — Multi-armed bandits: Thompson Sampling, UCB1, ε-greedy — and what they cost

### What & why

An A/B test spends the whole run showing half the users the worse variant, so that at the
end you _know_ which is better. A **multi-armed bandit** spends the run learning _and_
exploiting: it shifts traffic toward arms that look better as evidence accumulates, so fewer
users see the loser. The price is inference: the arms are no longer exchangeable (later
users are routed by earlier outcomes), so the clean causal estimate an A/B test gives is
gone, and a skewed traffic split is expected rather than alarming.

Three algorithms, one interface (`compute_weights(variant_stats) → {variant_id: weight}`):

- **Thompson Sampling** — Bayesian: draw θ_i from each arm's Beta posterior, pick the max;
  repeat 10,000 times; the arm's weight is how often it won. Naturally explores uncertain
  arms and exploits confident ones. Stochastic (seeded here).
- **UCB1** — frequentist: score each arm by `mean + √(2·ln N / n_i)`; the second term is an
  optimism bonus that shrinks as an arm is pulled. Unplayed arms get +∞ so every arm is tried.
  Deterministic given counts.
- **ε-greedy** — the simplest: the empirically best arm gets `1 − ε + ε/n`, every other arm
  `ε/n`. Deterministic; explores a fixed fraction forever.

### How Controlvar does it

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

The scheduler computes weights from the counts, seeds Thompson from
`derive_seed(experiment_id, day, 10_000)`, persists `BanditState.variant_weights`, and
estimates a regret reduction. The counts come from the first source that has them, in this
order (Build book Day 19):

1. **DynamoDB real-time counters** (an optional module) — but only when they are **at least
   as complete** as PostgreSQL's: every variant's DynamoDB pulls _and_ successes must be ≥
   its PostgreSQL pulls and successes (`_at_least_as_complete`). A partial DynamoDB count —
   one manual increment, a counter that started late — never replaces the complete one; the
   scheduler logs a warning and uses PostgreSQL.
2. **PostgreSQL** — pulls are assignment rows; successes are **converting users** on the
   primary metric, counted exactly as `/results` counts them. With no metric configured,
   any event other than an exposure counts.
3. The previously persisted `BanditState`.
4. Zero-count priors (equal weights).

**Lower is better** (engine 1.6.0, #1385). A bandit that rewards conversions on an error or
churn metric sends its traffic to the arm with the _most_ errors. Every source above returns raw
counts — successes are converting users, as stored and reported — and the scheduler applies the
primary metric's direction once, at the algorithms' input:

```python
# 📁 backend/app/core/bandit_scheduler.py  (abridged)
def _as_rewards(
    stats: Dict[str, VariantStats], lower_is_better: bool
) -> Dict[str, VariantStats]:
    """Turn converting-user counts into the bandit's reward counts.

    Higher is better: a converting user is a success, unchanged.  Lower is
    better (an error or churn event): a user who did NOT convert is the
    success, so the arm with fewer converting users earns the traffic (#1385).
    ``total_reward`` (UCB1) follows ``successes``.
    """
    if not lower_is_better:
        return stats
    flipped: Dict[str, VariantStats] = {}
    for vid, vs in stats.items():
        successes = max(vs.pulls - vs.successes, 0)
        ...
```

The weights, the regret estimate and the history rows' posterior `alpha`/`beta` come from the
reward counts; the `successes` and `failures` the state and history store stay the converting
users. So a lower-is-better bandit's stored counts read exactly like a higher-is-better one's,
and only its weights show the direction.

The regret estimate is stored as `BanditState.regret_reduction_pct` (null when it is not
positive). The dashboard does not show it — read it from the bandit state if you want it:

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
# 📁 backend/app/services/bandit_routing.py
def pick_variant(weighted: WeightedVariants, experiment_id: str, user_id: str) -> Optional[uuid.UUID]:
    """Deterministic pick from *weighted* for ``(user_id, experiment_id)``.

    The user is hashed into a bucket in ``[0, 1)`` and the bucket is looked up
    in the cumulative distribution of the normalised weights, so repeated calls
    for the same user return the same variant before the sticky assignment row
    exists.
    """
    total = sum(w for _, w in weighted)
    ...
    bucket = int(digest, 16) % 10_000 / 10_000

    cumulative = 0.0
    for variant_id, weight in weighted:
        cumulative += weight / total
        if bucket < cumulative:
            return variant_id
```

`/tracking/assign` (and its batch form) build a `bandit_chooser` once per request, which reads
`BanditState` once and returns a function of the user id over plain values; a batch of N users
reads the weights once. A fixed-allocation experiment, or one with no state or only zero
weights, answers `None` and falls back to the ordinary traffic-allocation hashing. A variant
whose weight is zero or missing receives no traffic.

#### What a bandit costs you

Say it plainly, because the results page can't: **a bandit's per-arm conversion rates are
biased estimates.** Users routed later are routed _because_ of earlier outcomes; if an arm
had a lucky start it gets more traffic and its later (regression-to-the-mean) users pull its
rate down — or a temporal effect (weekend vs weekday) lands unevenly across arms. Thompson
Sampling's exploration mitigates this but doesn't eliminate it (E3.5 measures it). The honest
uses of a bandit: (1) a short-lived decision where optimising during the run matters more than
knowing the effect size afterwards (a promotion banner this week); (2) many arms where an A/B
test's equal split would waste most users on bad options; (3) an always-on optimisation that
never needs a report. For a decision you'll cite in a quarterly review, run an A/B test.

> **Watch Out!** Because a bandit is _supposed_ to drift from the configured split, the SRM
> check is skipped for `optimization_type != "fixed"`. That also means the one exchangeability
> alarm the platform has is off. If you need to detect a broken randomiser on a bandit, watch
> the weights the scheduler stores against what the assignment counts show — they should
> track.

> **Did You Know?** The bandit that "never learned" for months (Build book, Day 1) produced
> weights that were exactly uniform — indistinguishable in the stored state from a healthy
> bandit whose arms happened to be equal. The stored regret estimate sitting at 0.0% forever
> was the only symptom, and nobody was watching it. A metric that can't distinguish
> "broken" from "no signal" needs a companion (`pulls > 0`), which the `_has_pulls` fallback
> check now provides — and its sibling `_at_least_as_complete` stops a source that has _some_
> data from beating one that has all of it.

### Try It Yourself

```python
>>> from backend.app.services.bandit_service import VariantStats, ThompsonSampling, UCB1, EpsilonGreedy
>>> stats = [VariantStats("a", successes=40, failures=960, pulls=1000, total_reward=40),
...          VariantStats("b", successes=52, failures=948, pulls=1000, total_reward=52),
...          VariantStats("c", successes=0, failures=0, pulls=0, total_reward=0)]
>>> ThompsonSampling.compute_weights(stats, seed=1)      # c has Beta(1,1): a draw above ~0.05 wins
{'a': 0.006, 'b': 0.0484, 'c': 0.9456}
>>> UCB1.compute_weights(stats)                          # c unplayed → +∞ → all weight
{'a': 0.0, 'b': 0.0, 'c': 1.0}
>>> EpsilonGreedy.compute_weights(stats)                 # b best → 0.9 + 0.1/3; others 0.1/3
{'a': 0.0333..., 'b': 0.9333..., 'c': 0.0333...}
>>> stats[2] = VariantStats("c", successes=3, failures=97, pulls=100, total_reward=3)
>>> ThompsonSampling.compute_weights(stats, seed=1), UCB1.compute_weights(stats)
({'a': 0.0764, 'b': 0.7154, 'c': 0.2082}, {'a': 0.2152..., 'b': 0.2310..., 'c': 0.5537...})
```

The unplayed arm takes 95% of the Thompson weight: a uniform draw beats both played arms'
draws (which sit near 0.04 and 0.05) about 95% of the time. After 100 pulls at 3% it keeps
a fifth — still exploring, because 100 pulls leave its rate uncertain.

And a lower-is-better primary metric, where the counts are users who hit an error:

```python
>>> from backend.app.core.bandit_scheduler import _as_rewards
>>> raw = {"a": VariantStats("a", successes=52, failures=948, pulls=1000, total_reward=52),
...        "b": VariantStats("b", successes=30, failures=970, pulls=1000, total_reward=30)}
>>> ThompsonSampling.compute_weights(list(raw.values()), seed=1), ThompsonSampling.compute_weights(list(_as_rewards(raw, True).values()), seed=1)
({'a': 0.9933, 'b': 0.0067}, {'a': 0.007, 'b': 0.993})
```

Read as conversions, `a` would get 99% of the traffic; read as errors, `b` does.

Then simulate a bandit run: 30 days, 1,000 users/day, true rates (0.04, 0.052, 0.03),
recompute Thompson weights each day from cumulative counts, and plot the share of traffic
each arm receives over time — and the _observed_ rate of arm b vs its true 0.052, to see
the bias.

### Key takeaways

- Thompson: weight = probability to be best from seeded Beta draws; UCB1: `mean + √(2 ln N /
  n_i)`, ∞ for unplayed, normalised; ε-greedy: best gets `1 − ε + ε/n`.
- The scheduler recomputes weights each tick from real counts (DynamoDB only when at least
  as complete as PostgreSQL → PostgreSQL converting users → BanditState → priors), persists
  them and the regret estimate (not shown on the dashboard); `/assign` routes new users by
  them deterministically. A lower-is-better primary metric makes a user who did _not_ convert
  the reward (`_as_rewards`); the stored counts stay the converting users.
- Bandits trade inference for reward: rates are biased, SRM is skipped, exchangeability is
  gone — use them to optimise, not to measure.

---

## Beyond the Hours — Analysing from sufficient statistics (warehouse runs)

### What & why

Every proportion in Part I needed two numbers per variant, `(n, n_converted)`. A mean metric —
revenue per user, minutes watched — needs three: the unit count, the sum of the values and
the sum of their squares. Those are **sufficient statistics**: the estimator's answer is a
function of them alone, so you never need the rows. That is what lets the platform analyse an
experiment whose events live in a customer's data warehouse: the warehouse runs a `GROUP BY
variant` and returns a handful of aggregates per variant, and only those leave it.

There is one numerical trap. Σy² − (Σy)²/n subtracts two huge, nearly equal numbers when the
values sit far from zero (order values around 50,000, say), and the variance cancels away in
floating point. The fix is to **centre** first: pick k (the grand mean), and have the
warehouse return Σ(y − k) and Σ(y − k)². Comparisons use the centred means, in which k
cancels; k is added back only for the reported mean and interval.

### How Controlvar does it

This is the **full profile** (`modules/`), and the warehouse routes
(`/api/v1/warehouse/analysis/...`) are `x-stability: beta` — the request and response shapes
may still change. The numbers themselves come from the core estimators in
`backend/app/services/sufficient_stats_analysis.py`, which the analysis-status table labels
`ga` (`warehouse_proportion`, `warehouse_mean`; Part IV Hour 12):

- **Proportion metrics** go through `binomial_metric_result` — the very function `/results`
  uses (Part I Hour 2). Same counts in, identical numbers out.
- **Mean metrics** go through `mean_metric_result(k, [(variant, n, Σ(y−k), Σ(y−k)²), ...],
  alpha, correction_method, metric=...)`: per variant the mean, the sample variance and a
  t-interval `mean ± t(1 − α/2, n − 1)·√(var/n)`; per treatment **Welch's t-test**
  (`ttest_ind_from_stats` on the centred means), Cohen's d, observed power and the relative
  improvement (null when the control's mean is 0). It is also the function `/results` runs for a
  revenue, count, duration or custom metric: `per_user_mean_metric_result` builds these centred
  sums from each assigned user's value and calls it (Part I Hour 2), so the same per-user values
  give the same p-value from either path. What differs is the difference fields: a warehouse
  run's mean metrics get no `absolute_difference`, lift interval or impact, while its
  proportion metrics, through `binomial_metric_result`, get all of them.
- **Refusals, not guesses.** A variant with fewer than 2 units is "not computed"
  (`fewer_than_2_units`); sums no sample could produce — a negative variance, a non-finite
  sum — are `result_invalid`; two arms with no variation get a "no variation" note instead of
  a p-value. A run also refuses on `no_units`, `join_key_mismatch`,
  `too_many_variant_values` and `timezone_not_utc`, and a failed run never reports numbers.
- **SRM** is computed with the same `compute_srm` as Part I Hour 1 (skipped for adaptive
  allocation), and the warehouse run results view shows it, as the core results page does for
  `/results`.

```python
>>> import numpy as np
>>> from scipy import stats
>>> from backend.app.services.sufficient_stats_analysis import mean_metric_result, BinomialVariant
>>> class M: id = "rev"; name = "revenue"; metric_type = "revenue"; is_primary = True
>>> C, T = BinomialVariant("c", "control", True), BinomialVariant("t", "treatment", False)
>>> rng = np.random.default_rng(8)
>>> yc = rng.normal(50_000, 40, 2000); yt = rng.normal(50_003, 40, 2000)     # large offset, small effect
>>> k = np.concatenate([yc, yt]).mean()
>>> sums = lambda y: (len(y), float((y - k).sum()), float(((y - k) ** 2).sum()))
>>> t = mean_metric_result(k, [(C, *sums(yc)), (T, *sums(yt))], 0.05, "none", metric=M)["variants"][1]
>>> round(t["p_value"], 6), round(stats.ttest_ind(yt, yc, equal_var=False).pvalue, 6), t["statistical_test_used"]
(0.000144, 0.000144, 'welch_t_test')
```

Three sums per arm reproduce the raw-data Welch test to six decimals. `test_mean_metric_result.py`
holds the function to an exact reference.

### Key takeaways

- Proportions need `(n, n_converted)`; means need `(n, Σ(y−k), Σ(y−k)²)` centred on the grand
  mean so large offsets don't cancel the variance.
- Warehouse runs (full profile, beta routes) use the same proportion function as `/results`
  and the same Welch's-t mean function `/results` uses for revenue, count, duration and custom
  metrics; only aggregates leave the warehouse. A warehouse mean result carries no difference
  or impact intervals.
- Impossible sums are refused, too-small arms are "not computed", and SRM is checked.

---

## Part III Workshop

**Q1. Why does the `CupedService` library estimate θ on the control group only, and what does
the route do differently?**
The treatment might change the Y–X relationship; a pooled θ could then absorb part of the
treatment effect into the adjustment. Control-only θ can't bias the effect, only reduce
noise. The route instead fits the regression `y ~ C(arm) + x` and uses the pooled within-arm
slope over every arm, which keeps the adjusted means comparable at the cost that
`variance_reduction_pct` can come out negative.

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

**Q7. B's expected loss is 0.00044, below `loss_threshold = 0.001`, yet `should_stop` says
`CONTINUE`. Why, and will the scheduler end the experiment when it does say `STOP_WINNER`?**
B's probability to be best is 0.90, below the fixed 0.975 floor; both conditions are needed.
And no: the decision is a recommendation recorded on the experiment. Nothing stops an
experiment on it — a person does.

**Q8. Your `/cuped` response shows `variance_reduction_pct: 0.3` and `covariate_coverage_pct:
2`. Is CUPED useless for your product?**
Not necessarily. Only 2% of the comparison's users had any covariate event in the lookback
window, so there is almost nothing to adjust on: the product may have mostly new users, or the
covariate event may be the wrong one (try a different `covariate_metric_id`, or a longer
`covariate_lookback_days`). A reduction near 0 with high coverage would be the sign of a covariate
that simply does not predict the outcome.

**Q9. A bandit's primary metric is a checkout error, lower is better. Its stored state says arm
`b` has 30 successes in 1,000 pulls and 99% of the weight. Is that a bug?**
No. The stored `successes` are converting users — here, users who hit the error — and the
weights come from the reward counts, in which a user who did _not_ convert is the success. `b`
has the fewest errors, so it earns the traffic.

**Quiz.** Thompson weight for an arm with Beta(1, 1) among arms with Beta(41, 961) and
Beta(53, 949)?
→ About 95% (0.946 with `seed=1`). The other two posteriors are tight around 0.04 and 0.05,
so the unplayed arm wins every draw in which a uniform number exceeds about 0.05. That's
exploration at full strength — and it's why an unplayed arm is tried immediately.

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
raw_se, win_se      # ≈ 2.77 vs ≈ 0.074: about 37 times smaller
```

The estimand is now "mean revenue with each user's spend capped at the 99th percentile" —
a robust mean, not total revenue; a treatment that _only_ changes whales is invisible to it.

**S3.3** — Beta(1,1): posteriors Beta(9, 93) vs Beta(13, 89), PtBB(B) ≈ 0.822. Beta(4, 96):
Beta(12, 188) vs Beta(16, 184), PtBB(B) ≈ 0.788 (both with `seed=1`). The informative prior pulls both toward 4%
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

Output: `500 0.16`, `1000 0.295`, `2000 0.49`, `4000 0.71`, `8000 0.955`. The crossing
point is between 2,000 and 4,000 per arm. The frequentist plan for the same lift (4% → 5.2%
at 80% power, `calculate_required_sample_size(0.04, 0.012)`) is 4,783 per arm — the same
order. That is the 0.975 floor at work: an expected-loss rule alone stops when being wrong
is _cheap_ and would cross much earlier; with the floor, `STOP_WINNER` also needs the winner
to be nearly _certain_, which costs about as many users as a classical test.

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

With `default_rng(9)`: a 3.82% on 9,562 users, b 3.94% on 30,422, final weights 0.31 / 0.69
— a 76/24 traffic split between two identical arms. Repeat over 200 runs (`default_rng(1000 +
seed)` for each, the same loop) and the pattern is systematic: the arm that ends with more
traffic averages 4.04% on 71.5% of the users, the starved arm 3.80%. The mechanism is in the starved arm: an arm that has an unlucky start
loses traffic, so its low estimate is corrected slowly, while an arm that has a lucky start
keeps getting users who pull its rate back toward the truth. Both reported rates are
biased, in opposite directions — which is why bandit rates are unfit for reporting an
effect.

---

**Next:** [Part IV — Many Metrics, Many Segments →](./part-4-many-comparisons.md)

# Part IV — Many Metrics, Many Segments

### Hours 10–12

[← Part III](./part-3-sharper-estimates.md) · [Contents](./README.md)

An experiment may compare several treatments, measure several outcomes and report results for
multiple segments. These additional comparisons affect how significance should be interpreted.
Hour 10 covers multiple-testing correction. Hour 11 examines segment breakdowns and the platform's
heterogeneity, novelty and interaction checks, including their implementation limits.

Hour 12 covers reproducibility: random seeds, engine versions, snapshots, caching and the
`ga`/`beta` analysis labels. Together, these records explain how a reported result was produced.

---

## Hour 10 — Multiple comparisons: Bonferroni and Benjamini–Hochberg

### What & why

For m independent tests whose null hypotheses are all true, testing each at α = 0.05 gives a
probability of at least one false positive of `1 − 0.95^m`. That is about 14% for three tests,
40% for ten and 64% for twenty. A results page with several treatments and metrics therefore needs
a declared multiple-testing policy; choosing whichever uncorrected comparison is significant does
not preserve a 5% error rate for the collection.

Two philosophies of correction:

- **Family-wise error rate (FWER)** — control the probability of _any_ false positive.
  **Bonferroni** is the blunt instrument: compare each p to `α/m` (equivalently, multiply
  each p by m and cap at 1). Simple, valid under any dependence, and _conservative_ — with
  many tests the bar becomes so high that real effects are missed.
- **False discovery rate (FDR)** — control the expected _proportion_ of your declared
  discoveries that are false. **Benjamini–Hochberg (1995)** sorts the m p-values, finds the
  largest k with `p(k) ≤ (k/m)·α`, and rejects everything up to that rank. Adjusted
  p-values are `p(k)·m/k`, made monotone from the top down. Less conservative than
  Bonferroni; the right tool when you're screening many metrics and can tolerate a few
  false leads among many true ones.

Which to use is a policy question: for the **decision** metric (ship / don't ship), the
answer should be robust to the correction — a treatment that's only significant with
`correction=none` isn't a winner. For **guardrail** metrics, uncorrected is often
_preferable_: you'd rather over-detect a harm than miss it.

### How Experimently does it

The results endpoint takes a `correction_method` and applies it **across the treatment
variants of each metric** — the "which of my three variants beat control?" family:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
def adjusted_p_values(
    p_values: List[Optional[float]], method: str
) -> List[Optional[float]]:
    """Multiple-comparison correction across the treatment variants of one metric."""
    valid = [(i, p) for i, p in enumerate(p_values) if p is not None]
    adjusted: List[Optional[float]] = [None] * len(p_values)
    if not valid or method == "none":
        return adjusted
    k = len(valid)
    if method == "bonferroni":
        for i, p in valid:
            adjusted[i] = min(1.0, p * k)
        return adjusted
    if method == "benjamini_hochberg":
        ordered = sorted(valid, key=lambda ip: ip[1])
        running = 1.0
        for rank in range(k, 0, -1):
            i, p = ordered[rank - 1]
            running = min(running, p * k / rank)
            adjusted[i] = min(1.0, running)
        return adjusted
    return adjusted
```

It lives beside `binomial_metric_result` (Part I Hour 2), which calls it;
`AnalysisService._adjusted_p_values` is kept as an alias of it, which is why the Try It
below still works.

Read the BH branch carefully: it walks from the _largest_ rank down, keeping a running
minimum, so the adjusted p-values are non-decreasing in rank — the step-up procedure. A
`None` p-value (a variant with no data) is skipped and doesn't count toward k.

The adjusted value is the one that decides significance and the one the recommendation
quotes:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py  (in binomial_metric_result)
            if not v["is_control"]:
                p_value = v.get("p_value")
                adj = adjusted_by_id.get(v["variant_id"])
                decisive = adj if adj is not None else p_value
                ...
                entry.update(
                    p_value=p_value,
                    adjusted_p_value=adj,
                    is_significant=bool(decisive is not None and decisive < alpha),
```

```python
# 📁 backend/app/services/analysis_service.py
            p = (
                winner["adjusted_p_value"]
                if winner["adjusted_p_value"] is not None
                else winner["p_value"]
            )
            reason = (
                f"{winner['variant_name']} shows "
                f"{pct:.1f}% improvement on {primary['metric_name']} (p={p:.4f})."
```

The endpoint validates the method with a regex and includes it in the cache key, so
`?correction_method=bonferroni` and `?correction_method=none` never serve each other's
cached body:

```python
# 📁 backend/app/api/v1/endpoints/results.py
    correction_method: str = Query(
        default="none",
        pattern="^(none|bonferroni|benjamini_hochberg)$",
    ),
    ...

def _results_cache_key(experiment_id, confidence_level, correction_method, breakdown) -> str:
    return (
        f"results:{experiment_id}:{ENGINE_VERSION}:"
        f"{confidence_level}:{correction_method}:{breakdown or ''}"
    )
```

(The engine version in the key is Hour 12's: an upgraded engine never serves numbers the old
one cached.)

There's a second, metric-family implementation — `BenjaminiHochbergService.correct(p_values:
Dict[str, float], fdr_threshold)` (EP-043, `fdr_correction_service.py`) — that takes a
`{metric_name: p}` map, returns ranked `FDRResult`s with `is_significant = rank ≤ k*`. It backs
`POST /results/{id}/fdr-correction`, which corrects the `{metric_name: p}` map **you send in
the body** — it does not read the experiment's own p-values (the experiment id is only
checked to exist), and no dashboard screen calls it. Same maths, a different family:
variants-within-metric vs metrics-within-experiment. Know which family a number was
corrected over before you quote it.

```python
# 📁 backend/app/services/fdr_correction_service.py
        # Step 2-3: Find the largest rank k* where p(k) ≤ (k/m) * α
        cutoff_rank = 0
        for k, (name, p) in enumerate(sorted_items, start=1):
            bh_threshold = (k / m) * fdr_threshold
            if p <= bh_threshold:
                cutoff_rank = k
        # All hypotheses with rank ≤ cutoff_rank are rejected
```

> **Watch Out!** The default is `correction_method=none`, and the dashboard always uses the
> default: it requests `/results` with no `correction_method` and no `confidence_level`, so
> what it shows is uncorrected at 0.95. It says so — the p-value column is labelled
> **"unadjusted"**; a response that does carry a correction is labelled "adjusted
> (Bonferroni)" or "adjusted (BH)" with the raw p beside it, and the badge, row colour and
> recommendation all follow the one `is_significant` decision. A correction is chosen only
> through the API (and in the Sample Size tab's planner). So a reader of the default page, or
> a naïve API consumer reading `is_significant` from the default response, is reading
> _uncorrected_ significance for a multi-variant experiment. Read the `correction_method`
> field in the response, not just the verdict.

> **By the Way** — Bonferroni also belongs in the **power calculation**. If you'll test three
> treatments at α/3, the sample size per variant has to be computed at α/3, not α:
> `calculate_required_sample_size(baseline, mde, alpha=0.05/3)` returns a noticeably larger n
> (the `z_alpha` term grows from 1.96 to 2.39). `GET /results/{id}/sample-size` does this for
> you: pass `correction_method=bonferroni` (or `benjamini_hochberg`) and it plans each
> comparison at α / (variants − 1), reporting the `alpha` and `comparisons` it used. The
> dashboard's Sample Size tab exposes the same choice.

### Try It Yourself

```python
>>> from backend.app.services.analysis_service import AnalysisService
>>> ps = [0.01, 0.04, 0.20]
>>> AnalysisService._adjusted_p_values(ps, "none")
[None, None, None]
>>> AnalysisService._adjusted_p_values(ps, "bonferroni")
[0.03, 0.12, 0.6000000000000001]
>>> AnalysisService._adjusted_p_values(ps, "benjamini_hochberg")
[0.03, 0.06, 0.20000000000000004]
>>> AnalysisService._adjusted_p_values([0.01, None, 0.04], "bonferroni")   # k = 2, None skipped
[0.02, None, 0.08]
>>> from backend.app.services.fdr_correction_service import BenjaminiHochbergService
>>> [(r.metric_name, r.rank, round(r.adjusted_p_value, 3), r.is_significant)
...  for r in BenjaminiHochbergService().correct({"revenue": 0.001, "clicks": 0.03, "churn": 0.04, "time": 0.5})]
[('revenue', 1, 0.004, True), ('clicks', 2, 0.053, False), ('churn', 3, 0.053, False), ('time', 4, 0.5, False)]
```

Then the sample-size effect:

```python
>>> svc = AnalysisService(db=None)
>>> svc.calculate_required_sample_size(0.04, 0.008, alpha=0.05), svc.calculate_required_sample_size(0.04, 0.008, alpha=0.05 / 3)
(10317, 13761)
```

And the family-wise rate itself, by simulation: 10,000 experiments, 5 null metrics each, count
how often any raw p < 0.05, then how often any Bonferroni-adjusted p < 0.05.

### Key takeaways

- m tests at α give a `1 − (1−α)^m` chance of a false positive somewhere; correct or accept it.
- Bonferroni (FWER): `min(1, p·k)`; BH (FDR): rank, `p·k/rank`, monotone from the top.
- `adjusted_p_values` corrects across treatment variants per metric and drives
  `is_significant` and the recommendation; `BenjaminiHochbergService` (behind
  `POST /fdr-correction`) corrects a p-value map you supply. The method is validated, echoed,
  and part of the cache key.
- Default is `none`, and the dashboard always shows the default, labelled "unadjusted";
  `/sample-size` plans multi-arm experiments at the corrected α when asked.

---

## Hour 11 — Segments and heterogeneity: dimensional breakdowns, HTE, novelty, interactions

### What & why

Segment breakdowns can help investigate whether treatment effects differ across populations.
They also add comparisons, reduce the sample in each comparison and may be selected after examining
the data. The platform labels these results exploratory, applies Bonferroni correction by the
number of segments and reports a heterogeneous-treatment-effect (HTE) indicator. Treat an apparent
segment difference as a reason for further investigation or a planned follow-up experiment.

Three related checks belong alongside because they all attack exchangeability from the
outside. The platform has helper functions for all three, but its interactions and novelty
routes do not feed them outcome data yet — see "What the routes compute today" below:

- **Novelty** — the effect is real on day 1 and gone by day 14, because users tried the new
  thing once. A declining daily effect is the signature.
- **Interactions** — two concurrent experiments share users, and the combination behaves
  differently from the sum. The 2×2 table (A on/off × B on/off) tests it.
- **SUTVA** — the "stable unit treatment value assumption": my outcome depends only on _my_
  treatment. Social features, marketplaces and anything with contagion violate it; so does
  contamination (control users who saw the treatment).

### How Experimently does it

**Breakdowns.** `GET /results/{id}?breakdown=platform` groups users by an event metadata key,
computes per-segment results with the corrected α, and marks every segment exploratory. The
base α is `1 − confidence_level` of the request. A segment's denominator is the distinct
assigned users with an event carrying that segment value; its numerator is converting users
(Part I Hour 2). Every variant appears in every segment, and the control is the variant
flagged `is_control` — never guessed from ids or order:

```python
# 📁 backend/app/services/dimensional_analysis_service.py
    def get_adjusted_alpha(self, base_alpha: float, num_segments: int) -> float:
        """
        Compute the Bonferroni-corrected significance threshold.

        Returns base_alpha / num_segments when num_segments > 1, otherwise
        returns base_alpha unchanged (no correction needed for a single segment).
        """
        if num_segments > 1:
            return base_alpha / num_segments
        return base_alpha

    def compute_segment_results(self, segments, base_alpha: float = 0.05) -> List[SegmentResult]:
        ...
        num_segments = len(segments)
        adjusted_alpha = self.get_adjusted_alpha(base_alpha, num_segments)
        ...
            for variant_id, vdata in variant_map.items():
                ...
                v_ci = self._wilson_ci(v_conversions, v_total, adjusted_alpha)
                ...
                    p_value = self._two_prop_z_test(control_conversions, control_total, v_conversions, v_total)
                    is_significant = (p_value is not None) and (p_value < adjusted_alpha)
            ...
            results.append(
                SegmentResult(
                    segment_value=seg_val,
                    sample_size=total_sample,
                    adjusted_alpha=adjusted_alpha,
                    is_exploratory=True,
                    variants=variant_results,
                )
            )
```

Note the CI is _also_ widened to the corrected α — a segment's interval and its
significance verdict agree. The UI renders the label as a warning, not a footnote:

```tsx
// 📁 frontend/src/components/results/Breakdowns/SegmentComparisonTable.tsx
      {is_exploratory && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3" role="note" aria-label="Exploratory analysis warning">
          <p className="text-sm font-medium text-amber-800">Exploratory only</p>
          <p className="mt-0.5 text-xs text-amber-700">
            Segment breakdowns are exploratory analyses. Results use
            Bonferroni-corrected significance thresholds (α ={' '}
            {breakdown.adjusted_alpha.toFixed(4)}) and should not be the sole
            basis for shipping decisions.
          </p>
        </div>
      )}
```

**HTE detection** — a chi-square on a 2×N table of treatment vs control conversions across
segments, with two heuristics as backstop:

```python
# 📁 backend/app/services/dimensional_analysis_service.py
    def detect_hte(self, segment_results: List[SegmentResult]) -> bool:
        """
        Detect heterogeneous treatment effects (HTE) across segments.

        Uses a chi-squared-like test on the treatment conversion rates across
        segments.  Returns True when at least two segments differ significantly
        in their observed treatment effect.
        """
        ...
        contingency = np.array(
            [
                [int(c) for c in treat_conv],
                [int(c) for c in ctrl_conv],
            ]
        )
        ...
        try:
            chi2_stat, p_value, dof, _ = stats.chi2_contingency(contingency)
            # Use 0.05 as the HTE detection threshold
            if p_value < 0.05 and dof > 0:
                return True
        except Exception as exc:
            logger.debug("HTE chi-squared test failed: %s", exc)

        # Secondary heuristic: compare lift variance to mean lift
        if len(lift) >= 2:
            lift_std = float(np.std(lift, ddof=1))
            lift_mean = float(np.abs(np.mean(lift)))
            # High coefficient of variation in lift across segments → HTE
            if lift_mean > 0 and (lift_std / lift_mean) > 0.5:
                return True
            # Segments with opposing signs (positive and negative lift) → HTE
            if any(l > 0 for l in lift) and any(l < 0 for l in lift):
                return True

        return False
```

The chi-square calculation tests whether conversions are distributed differently across segments
in treatment and control. It is a proxy for treatment-effect heterogeneity and is sensitive to
segment-size imbalance. Additional heuristics examine variation in lifts and lifts with opposing
signs. An HTE value of `True` renders a red `role="alert"` indicator; investigate the breakdown before
relying on the aggregate result.

**Novelty** — regress the daily effect on day index; a slope below −0.05 is flagged:

```python
# 📁 backend/app/services/interaction_detection_service.py
    NOVELTY_SLOPE_THRESHOLD = -0.05  # Negative slope threshold to flag novelty
    ...
    @staticmethod
    def detect_novelty_effect(daily_effects: List[float]) -> NoveltyResult:
        """Detect novelty effects by fitting a linear regression to daily effect sizes.

        A significantly negative slope indicates that the treatment effect is
        declining over time, which is the signature of a novelty effect.
        """
        ...
        x = np.arange(len(daily_effects), dtype=float)
        y = np.array(daily_effects, dtype=float)

        slope, intercept, r_value, p_value, std_err = stats.linregress(x, y)

        # A clearly negative slope signals novelty
        has_novelty = bool(slope < InteractionDetectionService.NOVELTY_SLOPE_THRESHOLD)
```

(The threshold is on the slope, not on its p-value — a pragmatic choice that fires on a
visibly declining effect and stays quiet on noise around a flat line. The recommendation it
attaches is "extend the minimum runtime," which is the only real cure: let the novelty
dissipate and measure the steady state.)

**Interactions** — overlap by Jaccard, then a 2×2 chi-square without Yates' correction:

```python
# 📁 backend/app/services/interaction_detection_service.py
    OVERLAP_THRESHOLD = 0.30  # Flag pairs with > 30 % shared users

    @staticmethod
    def compute_user_overlap(users_a: Set[str], users_b: Set[str]) -> float:
        """Compute Jaccard similarity: |A ∩ B| / |A ∪ B|.

        Returns 0.0 when either set is empty.
        """
    ...
    @staticmethod
    def detect_interaction(control_only: int, treatment_a_only: int, treatment_b_only: int, both_treatments: int) -> InteractionResult:
        """Chi-squared test for interaction between experiments A and B.

        Constructs a 2×2 contingency table and tests for independence:

            +----------------+----------+-----------+
            |                | Exp-B ON | Exp-B OFF |
            +----------------+----------+-----------+
            | Exp-A ON       | both     | a_only    |
            | Exp-A OFF      | b_only   | control   |
            +----------------+----------+-----------+
        """
        table = np.array(
            [[both_treatments, treatment_a_only], [treatment_b_only, control_only]],
            dtype=float,
        )
        ...
        chi2, p_value, dof, expected = stats.chi2_contingency(table, correction=False)

        # Effect size: simple excess above additivity for the "both" cell
        ...
        interaction_effect_size = (
            (both_treatments - expected_both) / total if total > 0 else 0.0
        )

        has_interaction = bool(p_value < 0.05)
```

The structural fix for interactions is upstream of statistics: put the two experiments in a
**mutual exclusion group** (Build book Day 4) so no user is in both.

**SUTVA** — declared, not detected:

```python
# 📁 backend/app/services/interaction_detection_service.py
    @staticmethod
    def check_sutva(treatment_size: int, control_size: int, network_feature: bool = False, contamination_rate: float = 0.0) -> SUTVAResult:
        """Check for SUTVA (Stable Unit Treatment Value Assumption) violations.

        Network features inherently violate SUTVA because a user's outcome
        depends on the treatment status of other users.
        """
        has_violation = network_feature or contamination_rate > 0.05
```

A network feature is flagged on the experimenter's say-so (`network_feature=True`), because
no statistic on the outcome data can tell you the units interfere; a contamination rate
(control users who were exposed) above 5% is flagged when the caller supplies one.

#### What the routes compute today (beta)

The functions above are correct as functions — the Try It exercises them. The routes that
apply them to real experiments are **beta** (issue #219), and each response says so in
`analysis_status` and `analysis_notice`:

- **Interactions** (`GET /interactions/{a}/{b}` and the active-experiment scan,
  `scan_active_experiments`) measure **user overlap only** (Jaccard). `interaction_result`,
  `novelty_result` and `sutva_result` are `null` — not computed — and `overall_risk` comes
  from the overlap alone: `low` at ≤ 0.30, `medium` above 0.30 up to 0.60, `high` above
  0.60. Pairs at or under 0.30 are left out of the scan.
- **Novelty** (`.../novelty`) is not computed: `has_novelty` and `decline_rate` are `null`,
  and a null `has_novelty` means "not computed", never "no novelty".

So an interaction "risk" from the platform today means "these experiments share a lot of
users", which is the input to the mutual-exclusion decision — not a test of whether they
interact.

**Post-stratification** is the constructive sibling of the breakdown: instead of reporting
per-segment results, reweight each stratum's control and treatment means to the _pooled_
stratum proportions (Horvitz–Thompson), giving one overall estimate that's immune to stratum
imbalance between arms and usually lower-variance. The service implements it, but the route
(`POST /results/{id}/post-stratification`) answers **501** "not available yet" for every
existing experiment, because nothing builds its per-user rows from an experiment's data
(Part III Hour 7):

```python
# 📁 backend/app/services/post_stratification_service.py
class PostStratificationService:
    """
    Service for computing post-stratification variance-reduced effect estimates.

    The Horvitz-Thompson estimator reweights stratum-specific means by the
    *population* stratum proportions (estimated from the combined control +
    treatment sample), removing bias introduced when stratum proportions differ
    between the two groups.
    """

    # Minimum number of observations required per stratum per group
    MIN_STRATUM_SIZE = 2
```

> **Watch Out!** The forking-paths problem is not solved by Bonferroni. If you look at ten
> dimensions, find that `country=DE` is significant, and _then_ report "significant in
> Germany (Bonferroni-corrected over 6 countries)," you've corrected for 6 tests after
> running 60. The exploratory label is the platform's way of saying: a segment result is a
> **hypothesis**, and hypotheses get their own experiment.

> **Did You Know?** Simpson's paradox is HTE's evil twin: a treatment can be better in every
> segment and worse overall (or vice versa) if the segments are imbalanced between arms.
> That's exactly what post-stratification fixes (in theory — the platform's route answers 501)
> and what an SRM check (Part I Hour 1) on the segment level would catch — and why
> `detect_hte`'s "opposing signs" heuristic exists.

### Try It Yourself

```python
>>> from backend.app.services.dimensional_analysis_service import DimensionalAnalysisService
>>> svc = DimensionalAnalysisService()
>>> svc.get_adjusted_alpha(0.05, 1), svc.get_adjusted_alpha(0.05, 4)
(0.05, 0.0125)
>>> segs = {
...   "ios":     {"c": {"total": 4000, "conversions": 160, "is_control": True,  "variant_name": "control"},
...               "t": {"total": 4000, "conversions": 240, "is_control": False, "variant_name": "treatment"}},
...   "android": {"c": {"total": 4000, "conversions": 160, "is_control": True,  "variant_name": "control"},
...               "t": {"total": 4000, "conversions": 150, "is_control": False, "variant_name": "treatment"}},
... }
>>> res = svc.compute_segment_results(segs, base_alpha=0.05)
>>> [(r.segment_value, r.adjusted_alpha, [(v.variant_id, round(v.p_value, 4) if v.p_value is not None else None, v.is_significant) for v in r.variants]) for r in res]
[('ios', 0.025, [('c', None, False), ('t', 0.0, True)]), ('android', 0.025, [('c', None, False), ('t', 0.5624, False)])]
>>> svc.detect_hte(res)          # +2pp on iOS, −0.25pp on Android → True (opposing signs, and the chi-square)
True
>>> from backend.app.services.interaction_detection_service import InteractionDetectionService as I
>>> I.detect_novelty_effect([0.05, 0.04, 0.03, 0.02, 0.01, 0.0]).has_novelty      # slope −0.01 → False (threshold is −0.05)
>>> I.detect_novelty_effect([0.30, 0.22, 0.15, 0.08, 0.02, 0.0]).has_novelty      # slope ≈ −0.06 → True
>>> I.compute_user_overlap({"u1", "u2", "u3"}, {"u3", "u4"}), I.has_significant_overlap({"u1","u2","u3"}, {"u3","u4"})
(0.25, False)
>>> r = I.detect_interaction(control_only=1000, treatment_a_only=1100, treatment_b_only=1050, both_treatments=1400)
>>> r.has_interaction, round(r.p_value, 4), round(r.interaction_effect_size, 4)
(True, 0.0013, 0.0118)
>>> I.check_sutva(5000, 5000, network_feature=True).warning_message
```

### Key takeaways

- Breakdowns: α/segments, Wilson CI at the corrected α, `is_exploratory=True` always,
  rendered as an amber warning; a segment result is a hypothesis, not a decision.
- HTE: chi-square on the 2×N conversion table plus CV-of-lift > 0.5 and opposing-sign
  heuristics → red alert.
- Novelty: `linregress` slope on daily effects < −0.05 → "extend the runtime."
  Interactions: Jaccard > 0.30 then 2×2 chi-square (p < 0.05); prevent with a MEG. SUTVA:
  declared for network features, flagged for supplied contamination > 5%. These are
  functions; the beta routes compute **overlap only** and return the rest as `null`.
- Post-stratification gives one reweighted estimate instead of many small ones — in theory;
  the route answers 501.

---

## Hour 12 — Reproducibility: seeds, engine versions, snapshots, and the results contract

### What & why

A reported result may need to be reviewed months after the experiment. Reproducing it requires
more than the displayed value: the data, calculation version and random sampling inputs must be
known. Three sources of change complicate that work:

1. **Randomness** — Monte Carlo statistics (PtBB, expected loss, Thompson weights) differ
   run to run unless seeded, and a dashboard that polls would show a number that flickers.
2. **Code changes** — a fix to an estimator or a changed default prior legitimately changes
   the numbers; the old number wasn't wrong, it was computed by a different engine.
3. **Data changes** — late events arrive, users get reassigned by a bug fix, an experiment
   is edited; the inputs to "March's number" may no longer exist.

The platform's answer is a small discipline: every Monte Carlo draw uses a seed that is a
**deterministic function of the inputs**; an `ENGINE_VERSION` names the code that computed a
number — it is on the Bayesian, CUPED and bandit responses, on every snapshot row, and in the
`/results` cache key (it is not in the `/results` or `/sequential` response bodies); a
fingerprint test makes the version move whenever the numbers do; and every fresh computation
writes an **`analysis_snapshots`** row with the seed, sample count, engine version and the
full payload. Add the results **cache** (which makes "the number the user saw" a stable thing for
five minutes) and the response **contract** (which makes the shape stable across clients),
and a number becomes an artefact rather than a moment.

### How Experimently does it

The seed, from `core` so schemas can import it without cycles:

```python
# 📁 backend/app/core/stats_engine.py
#: Bumped whenever a change alters numbers a client could have persisted
#: (sampling strategy, estimator, default priors, thresholds).
ENGINE_VERSION: str = "1.2.0"

#: Mask that keeps the derived seed inside the signed 64-bit range.
_SEED_MASK = (1 << 63) - 1


def as_of_bucket(as_of: Optional[Union[datetime, date]] = None) -> str:
    """Return the reproducibility bucket for an analysis time.
    ...
    Returns:
        ISO calendar day, e.g. ``"2026-09-11"``.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    if isinstance(as_of, datetime):
        if as_of.tzinfo is not None:
            as_of = as_of.astimezone(timezone.utc)
        return as_of.date().isoformat()
    return as_of.isoformat()


def derive_seed(experiment_id: object, bucket: str, n_samples: int) -> int:
    """Derive the deterministic RNG seed for a Monte Carlo computation.
    ...
    Returns:
        A non-negative integer below 2**63.
    """
    key = f"{experiment_id}|{bucket}|{int(n_samples)}".encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=8).digest()
    return int.from_bytes(digest, "big") & _SEED_MASK


def make_rng(seed: int) -> np.random.Generator:
    """Return the platform's RNG for a seed (always ``default_rng``)."""
    return np.random.default_rng(int(seed))
```

Each design choice is doing a job. **blake2b**, not Python's `hash()`, because `hash()` is
salted per process and would differ between the API container and the scheduler container.
**The UTC day** as the bucket, because a daily-refreshed posterior doesn't need finer
granularity, and a coarser one means dashboard, API and scheduler all draw the same samples
on the same day. **`n_samples` in the key**, so a client asking for 10,000 draws and one
asking for 50,000 get different — and each independently reproducible — streams. **63 bits**,
so the seed fits a signed PostgreSQL `BIGINT` and `numpy` seeding. And **`default_rng`
always**, because the legacy `np.random.seed`/`np.random.beta` global state is not
reproducible across threads.

Wired into the Bayesian analysis (Part III Hour 8):

```python
# 📁 backend/app/services/analysis_service.py
        # Deterministic seed: same experiment + same UTC day + same n_samples
        # → byte-identical Monte Carlo draws.
        seed = derive_seed(experiment.id, as_of_bucket(as_of), n_samples)

        # Run full Bayesian analysis
        analysis = service.analyze(variant_observations, n_samples=n_samples, seed=seed)
        ...
        return BayesianResultsResponse(
            is_enabled=True,
            decision=decision,
            variant_results=variant_results,
            seed=seed,
            n_samples=int(n_samples),
            engine_version=ENGINE_VERSION,
        )
```

and echoed in the response schema so a consumer can store the provenance with the number:

```python
# 📁 backend/app/schemas/bayesian.py
    seed: Optional[int] = Field(None, ge=0, description="Deterministic RNG seed used for the Monte Carlo draws.")
    n_samples: Optional[int] = Field(None, ge=1, description="Monte Carlo samples drawn per variant.")
    engine_version: str = Field(ENGINE_VERSION, description="Statistics engine version that produced these results.")
```

The bandit scheduler does the same with `derive_seed(experiment_id, as_of_bucket(), 10_000)`
for Thompson weights and stamps `BanditState.engine_version` (Part III Hour 9).

**The version's history**, which is also a summary of what changed in the numbers:

| Version | What moved                                                                                     |
| ------- | ---------------------------------------------------------------------------------------------- |
| 1.0.0   | The first stamped version (never fingerprinted).                                               |
| 1.1.0   | Breakdowns: every variant in every segment, and the flagged control rather than a guessed one.  |
| 1.2.0   | `/results` intervals and significance at the requested confidence level (#454); the sequential confidence sequence became the inverted mSPRT (#231); a Bayesian `STOP_WINNER` needs P(best) ≥ 0.975 (#242). |

**The fingerprint test and the bump rule.** A version string only means something if it
moves whenever the numbers do, and nobody remembers to bump it by hand. So
`backend/tests/unit/services/test_engine_fingerprint.py` runs a fixed dataset through every
analysis path — frequentist, Bayesian (including an A/A case), CUPED, sequential, breakdowns —
hashes the canonical output, and compares it with the hash pinned for the current
`ENGINE_VERSION`:

```python
# 📁 backend/tests/unit/services/test_engine_fingerprint.py  (docstring, abridged)
"""
When this test fails, the engine's output changed.  Then:

* if the current ``ENGINE_VERSION`` has **not** shipped in a release, replace
  its hash below with the new one (the change is part of that version);
* if it **has** shipped, bump ``ENGINE_VERSION`` in
  ``backend/app/core/stats_engine.py`` (and ``test_stats_determinism.py`` and
  both OpenAPI snapshots, whose ``engine_version`` defaults follow it), and add
  the new version's hash here, keeping the old entries.

Never edit the hash of a released version.
"""
```

Its control id deliberately sorts _after_ the treatment's, so an engine that guessed the
control from the smaller id would produce a different hash. Two siblings pin the rest:
`test_sufficient_stats_fingerprint.py` pins the proportion engine (`binomial_metric_result`)
per version, and `test_stats_determinism.py` checks that every Monte Carlo path is
deterministic for a given seed and that the responses carry their provenance fields. A pull request that changes a number therefore cannot merge silently: it either
updates the unreleased version's hash or bumps the version.

**Snapshots.** One row per (experiment, kind, UTC day) for the results endpoints — upserted,
so a polling dashboard refreshes the day's row rather than growing the table — and one row
per tick for the bandit scheduler:

```python
# 📁 backend/app/models/analysis_snapshot.py
class AnalysisKind(str, enum.Enum):
    """Which engine produced a snapshot."""

    FREQUENTIST = "frequentist"
    BAYESIAN = "bayesian"
    CUPED = "cuped"
    SEQUENTIAL = "sequential"
    BANDIT = "bandit"


class AnalysisSnapshot(Base, BaseModel):
    """One persisted analysis result for an experiment.

    Attributes:
        ...
        seed: RNG seed used for Monte Carlo draws; ``NULL`` for closed-form
            analyses (frequentist, CUPED, sequential).
        n_samples: Monte Carlo samples per variant; ``NULL`` when no sampling.
        as_of: Time the underlying data was read (UTC).  The results
            endpoints store the UTC calendar day (the bucket the RNG seed is
            derived from), so ``(experiment_id, kind, as_of)`` holds one row
            per analysis per day and a polling dashboard refreshes that row
            instead of adding one per request; the bandit scheduler stores the
            exact tick instant, so each tick keeps its own row.
        payload: The response body (or scheduler state) as served.
    """

    __tablename__ = "analysis_snapshots"
    ...
            UniqueConstraint(
                "experiment_id",
                "kind",
                "as_of",
                name="uq_analysis_snapshot_experiment_kind_as_of",
            ),
```

The write is best-effort and isolated — it runs in its own session so it can never roll back
the request's transaction, and it never raises:

```python
# 📁 backend/app/services/analysis_snapshot_service.py
def record_snapshot(db, experiment_id, kind, payload, *, engine_version=ENGINE_VERSION, seed=None, n_samples=None, as_of=None) -> Optional[AnalysisSnapshot]:
    """Persist one analysis snapshot per experiment, kind and UTC day.

    Idempotent: repeated calls on the same day update that day's row in place
    (so a polling dashboard cannot grow the table) and the first call the next
    day starts a new one.

    The write happens in its own session on ``db``'s engine and commits
    itself; the caller's transaction is never committed or rolled back here
    (see the module docstring).  Never raises.
    """
    try:
        snapshot = build_snapshot(experiment_id, kind, payload, engine_version=engine_version, seed=seed, n_samples=n_samples, as_of=snapshot_day(as_of))
        with _audit_session(db) as session:
            return _upsert(session, snapshot)
    except Exception as exc:
        logger.warning("analysis_snapshots write failed for experiment %s (%s): %s", experiment_id, kind.value if isinstance(kind, AnalysisKind) else kind, exc)
        return None
```

Retention is `ANALYSIS_HISTORY_RETENTION_DAYS = 90` (0 disables the purge), and the purge
piggybacks on the metrics scheduler because it already holds an advisory lock — the
platform's "one scheduler does the housekeeping" pattern (Build book Day 17):

```python
# 📁 backend/app/core/metrics_scheduler.py
        # Piggyback the analysis-history purge on this tick: it is the
        # lowest-frequency scheduler that already holds an advisory lock, and
        # retention needs to run somewhere.
        try:
            from backend.app.services.analysis_snapshot_service import (
                purge_expired_history,
            )

            purge_expired_history()
        except Exception as exc:
            logger.warning("analysis history purge skipped: %s", exc)
```

**The cache and the contract.** The results endpoint caches the serialised
`ExperimentResultsResponse` in Redis under a key that includes every parameter that changes
the numbers — and only writes the snapshot on a _fresh_ computation, so cached reads don't
churn the audit row:

```python
# 📁 backend/app/api/v1/endpoints/results.py
    Results are cached in Redis (TTL = 5 min for running experiments,
    24 h for completed/paused ones).  Pass ``use_cache=false`` to force
    a fresh computation.
    ...
    cache_key = _results_cache_key(experiment_id, confidence_level, correction_method, breakdown)
    # = f"results:{experiment_id}:{ENGINE_VERSION}:{confidence_level}:{correction_method}:{breakdown or ''}"
    ...
    # --- Persist audit snapshots (best-effort; never fails the response) ---
    _record_results_snapshots(db, response)

    # --- Cache write ---
    try:
        cache = _get_cache_service()
        running_statuses = {"running", "active", "RUNNING", "ACTIVE"}
        ttl = 300 if raw_status in running_statuses else 86400
        cache.set(cache_key, response.model_dump_json(), expire=ttl)
    except Exception:
        pass  # Cache write failure is non-fatal
```

Because `ENGINE_VERSION` is in the key, an upgraded engine misses every entry the old one
wrote instead of serving its numbers for up to a day. `POST /results/{id}/invalidate-cache`
(superuser) clears `results:{id}:*` — the thing to reach for after a data backfill. The five-minute TTL is also why "I tracked an event and the
dashboard didn't change" is in the troubleshooting guide: the number is _stable_ for five
minutes by design.

The **contract** is the Pydantic response model (`schemas/results.py`) that every consumer —
the dashboard's `results.ts` service, the CSV export, the MCP server, the demos — reads. Its
stability is what lets a September client parse a March snapshot's `payload`. When a field's
meaning changes (not just its value), bump `ENGINE_VERSION`; when its shape changes, that's an
API version, and the snapshot's `engine_version` is how you know which parser to use.

**What is `ga` and what is `beta`.** Analysis responses also describe the implementation status of
their numbers. The definitions are maintained in `backend/app/core/analysis_status.py` and read
when responses are constructed:

| Analysis (route)                                  | Status | Why, in its `analysis_notice`                                                    |
| ------------------------------------------------- | ------ | -------------------------------------------------------------------------------- |
| `cuped` (`GET /results/{id}/cuped`)               | beta   | the covariate is not a pre-experiment metric yet, so the reduction is ~0 (#217)   |
| `interactions` (`GET /interactions/{a}/{b}`)      | beta   | only user overlap is measured; interaction, novelty, SUTVA are null (#219)        |
| `novelty` (`.../novelty`)                         | beta   | novelty is not computed; `has_novelty` null means not computed (#219)             |
| `sequential` (`GET /results/{id}/sequential`)     | beta   | the decision is mSPRT alone; `alpha_spending` is always empty (#232)              |
| `warehouse_proportion`, `warehouse_mean`          | ga     | — (computed from sufficient statistics by the core estimators)                    |

A notice is present **exactly** when the status is `beta` — the table refuses any other
combination at import. This is separate from a route's `x-stability` (`stable`/`beta`), which
is about the request and response _shape_: the warehouse routes are `x-stability: beta` while
their numbers are `ga`. `/results` itself, `/bayesian` and `/sample-size` carry no
`analysis_status`; they compute what their fields say. Post-stratification has no entry
because it computes nothing — it answers 501.

> **Watch Out!** "Seeded" means reproducible **given the same inputs**. If late events arrive
> tonight, tomorrow's recomputation for _today's_ bucket will differ — the seed is the same,
> the counts aren't. That's why the snapshot stores the **payload**, not just the seed: the
> row is the record of what was served; the seed is how you prove the Monte Carlo wasn't the
> reason it changed.

> **Did You Know?** The seed changing at UTC midnight is visible: a Bayesian PtBB of 0.9412
> at 23:59 UTC can be 0.9407 at 00:01 with identical data. The results API draws 100,000
> samples per variant, so the Monte Carlo error on a probability near 0.9 is
> √(0.9·0.1/100000) ≈ 0.00095 — about ±0.002 for a 95% band — so this is noise, not news.
> If a decision hinges on the third decimal, raise
> `n_samples` — the seed is derived from it, so the higher-precision run is reproducible too.

### Try It Yourself

```python
>>> from backend.app.core.stats_engine import derive_seed, as_of_bucket, make_rng, ENGINE_VERSION
>>> from datetime import datetime, timezone, timedelta
>>> ENGINE_VERSION
'1.2.0'
>>> as_of_bucket(datetime(2026, 9, 11, 23, 59, tzinfo=timezone.utc)), as_of_bucket(datetime(2026, 9, 12, 0, 1, tzinfo=timezone.utc))
('2026-09-11', '2026-09-12')
>>> as_of_bucket(datetime(2026, 9, 11, 20, 0, tzinfo=timezone(timedelta(hours=-5))))   # 20:00 EST = 01:00 UTC next day
'2026-09-12'
>>> s1 = derive_seed("3f2a...-experiment", "2026-09-11", 10_000); s2 = derive_seed("3f2a...-experiment", "2026-09-11", 10_000)
>>> s1 == s2, s1 < 2**63, derive_seed("3f2a...-experiment", "2026-09-11", 50_000) == s1
(True, True, False)
>>> make_rng(s1).beta(41, 961, 3).tolist() == make_rng(s1).beta(41, 961, 3).tolist()
True
```

Then, against a running stack with a Bayesian-enabled experiment: call
`GET /api/v1/results/{id}/bayesian` twice and diff the bodies (identical, including `seed`
and `engine_version: "1.2.0"`; the route is not cached, and each call records the decision on
the experiment);
query `SELECT kind, engine_version, seed, n_samples, as_of FROM experimentation.analysis_snapshots
WHERE experiment_id = '…' ORDER BY created_at DESC LIMIT 5;` and confirm one `bayesian` row for
today whose `seed` matches the response; then `POST /results/{id}/invalidate-cache`, call
`GET /results/{id}` again, and confirm the day's `frequentist` row was _updated_, not added.
And watch the fingerprint gate fire: change `PROB_BEST_THRESHOLD` in
`bayesian_service.py` to 0.95 in a scratch checkout and run
`python -m pytest backend/tests/unit/services/test_engine_fingerprint.py -q` — it fails,
naming the hash it got. That failure is the reminder to bump the version.

### Key takeaways

- Seed = `blake2b("{experiment_id}|{UTC day}|{n_samples}")` masked to 63 bits; `default_rng`
  only; stable across processes, machines and the dashboard/scheduler boundary.
- `ENGINE_VERSION` (1.2.0) is bumped when numbers a client could have persisted would change;
  `test_engine_fingerprint.py` forces it (update the unreleased hash, or bump a released
  version; never edit a released hash). It's on the Bayesian, CUPED and bandit responses,
  every snapshot, and the `/results` cache key.
- `analysis_status.py` labels each analysis `ga` or `beta`; a `beta` response carries an
  `analysis_notice` saying what is not computed.
- `analysis_snapshots`: one row per (experiment, kind, day) upserted, exact ticks for the
  bandit; payload + seed + n_samples + engine_version; best-effort, isolated session,
  90-day retention purged by the metrics scheduler.
- The results cache (5 min / 24 h, key = every numeric parameter, manual invalidation) and
  the Pydantic contract make "the number the user saw" a stable, parseable artefact.

---

## Part IV Workshop

**Q1. Five metrics, three treatments. How many p-values, and what's the chance of at least one
false positive at α = 0.05 with no correction (assuming independence)?**
15; `1 − 0.95^15 ≈ 0.54`.

**Q2. Bonferroni-adjusted p for raw p = 0.02 across 3 treatments? BH-adjusted if the other
two raw p's are 0.001 and 0.30?**
Bonferroni: 0.06. BH: ranks are 0.001 (1), 0.02 (2), 0.30 (3); running from the top:
0.30·3/3 = 0.30; 0.02·3/2 = 0.03; 0.001·3/1 = 0.003 → 0.03.

**Q3. A breakdown by `country` over 8 countries shows `DE` significant at p = 0.009. Is it?**
The corrected α is 0.05/8 = 0.00625, so no — and even if it were, it's exploratory: a
hypothesis for a follow-up experiment, not a decision.

**Q4. `detect_hte` returns True. What are the three ways that can happen?**
Chi-square on the 2×N treatment/control conversion table has p < 0.05; the coefficient of
variation of per-segment lifts exceeds 0.5; per-segment lifts have opposing signs.

**Q5. Why is the Monte Carlo seed keyed on the UTC _day_ rather than the request time?**
So every caller on the same day — dashboard poll, API consumer, scheduler — draws the same
samples and the persisted snapshot agrees with what was served; finer buckets would flicker,
coarser ones would freeze a daily-updated posterior.

**Q6. You bump a default prior from Beta(1,1) to Beta(2,2). What else must change?**
`test_engine_fingerprint.py` will fail, and that decides it. If the current `ENGINE_VERSION`
(1.2.0) has shipped — it has — bump it, update `test_stats_determinism.py` and both OpenAPI
snapshots (their `engine_version` defaults follow it), and add the new version's hash beside
the old ones; never edit 1.2.0's hash. A client could have persisted numbers computed under
the old prior, and the snapshot's `engine_version` is how a reader tells the two apart. The
`/results` cache key changes with it, so no old answer is served after the upgrade.

**Quiz.** A results response has `correction_method: "none"`, three treatments, one with
`p_value: 0.03, is_significant: true`. Should the PM ship?
→ Not on that alone: uncorrected across three arms, the family-wise rate at 0.05 is ~14%.
Re-request with `bonferroni` (0.09 → not significant) or `benjamini_hochberg`; check
`sample_size_adequate`, the response's `srm` (the dashboard doesn't show it) and the primary
metric's minimum sample size.

---

## Exercises

**E4.1 — Family-wise simulation.** 10,000 experiments × 5 null metrics (n = 2,000 per arm,
rate 0.04); count experiments with any raw p < 0.05, any Bonferroni-adjusted p < 0.05, and
any BH-adjusted p < 0.05 (use `_adjusted_p_values`). Report the three rates.

**E4.2 — Power under correction.** For baseline 4%, MDE +0.8pp, 80% power: required n per
variant at α = 0.05, 0.05/2, 0.05/3, 0.05/5. Plot n against the number of treatments.

**E4.3 — Segment trap.** Simulate a _null_ experiment (no effect anywhere), 10 segments of
1,000 users per arm; compute per-segment p-values; repeat 1,000 times and report how often at
least one segment is "significant" at α = 0.05 vs at α/10.

**E4.4 — Novelty.** Generate 21 daily effects that decay from +0.15 to 0 exponentially, plus
noise σ = 0.02; report the fitted slope and whether `detect_novelty_effect` flags it; then
find the decay rate at which it stops flagging.

**E4.5 — Reproducibility audit.** Write a function that, given an `analysis_snapshots` row of
kind `bayesian`, recomputes PtBB from the payload's posteriors using the row's `seed` and
`n_samples`, and asserts it matches the payload's `probability_to_be_best` to 1e-12.

---

## Solutions

**S4.1**

```python
import numpy as np
from scipy import stats
from backend.app.services.analysis_service import AnalysisService
rng = np.random.default_rng(1); n = 2000; any_raw = any_bonf = any_bh = 0
for _ in range(10_000):
    ps = []
    for _m in range(5):
        c, t = rng.binomial(n, 0.04), rng.binomial(n, 0.04)
        ps.append(stats.fisher_exact([[t, n - t], [c, n - c]])[1])
    any_raw += min(ps) < 0.05
    any_bonf += min(p for p in AnalysisService._adjusted_p_values(ps, "bonferroni")) < 0.05
    any_bh += min(p for p in AnalysisService._adjusted_p_values(ps, "benjamini_hochberg")) < 0.05
any_raw / 1e4, any_bonf / 1e4, any_bh / 1e4       # 0.1887, 0.0432, 0.0444
```

(≈0.19 rather than the 0.23 that `1 − 0.95^5` predicts because Fisher's test is conservative
at these counts — each null metric fires about 4% of the time, not 5%.
Under the complete null, BH controls FWER too — its advantage over Bonferroni shows only when
some hypotheses are true.)

**S4.2**

```python
svc = AnalysisService(db=None)
[(k, svc.calculate_required_sample_size(0.04, 0.008, alpha=0.05 / k)) for k in (1, 2, 3, 5)]
# [(1, 10317), (2, 12494), (3, 13761), (5, 15351)]  — +21%, +33%, +49%
```

**S4.3**

```python
rng = np.random.default_rng(3); hits_raw = hits_adj = 0
for _ in range(1000):
    ps = [stats.fisher_exact([[t, 1000 - t], [c, 1000 - c]])[1]
          for c, t in zip(rng.binomial(1000, 0.05, 10), rng.binomial(1000, 0.05, 10))]
    hits_raw += min(ps) < 0.05; hits_adj += min(ps) < 0.005
hits_raw / 1000, hits_adj / 1000       # 0.308 vs 0.035
```

Without correction, a null experiment shows a "significant segment" about three times in
ten (Fisher's conservatism again keeps it under the `1 − 0.95^10 ≈ 0.40` the arithmetic
predicts) — the number the amber warning exists to prevent from being screenshotted.

**S4.4**

```python
from backend.app.services.interaction_detection_service import InteractionDetectionService as I
rng = np.random.default_rng(4); days = np.arange(21)
for rate in (0.30, 0.20, 0.10, 0.05):
    y = 0.15 * np.exp(-rate * days) + rng.normal(0, 0.02, 21)
    r = I.detect_novelty_effect(y.tolist()); print(rate, round(r.decline_rate, 4), r.has_novelty)
```

A 0.15 effect decaying over 21 days has an average slope of at most −0.007/day — well above
the −0.05 threshold — so none of these flag. The threshold is calibrated for effects
expressed in the units the service receives (relative lifts in the results pipeline, where a
30% → 0% decline over a week is −0.05/day); feed it absolute conversion-rate differences and
it will never fire. Know your units before trusting a "no novelty" verdict.

**S4.5**

```python
from backend.app.services.bayesian_service import compute_probability_to_be_best
def audit(row):
    posts = [{"alpha": v["posterior"]["alpha"], "beta": v["posterior"]["beta"]} for v in row.payload["variant_results"]]
    ptbb = compute_probability_to_be_best(posts, n_samples=row.n_samples, seed=row.seed)
    served = [v["probability_to_be_best"] for v in row.payload["variant_results"]]
    assert all(abs(a - b) < 1e-12 for a, b in zip(ptbb, served)), (ptbb, served)
    return True
```

It passes only if `engine_version` matches the running code's `ENGINE_VERSION` — which is
the point: the assertion is the definition of "reproducible."

---

_You've finished Experimentation Statistics. The other two specializations are
[SDK Engineering](../sdk-engineering/README.md) and
[Platform Operations](../platform-operations/README.md); the core path is in the
[root README](../README.md)._ 📐

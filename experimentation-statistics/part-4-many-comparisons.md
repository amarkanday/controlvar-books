# Part IV — Many Metrics, Many Segments

### Hours 10–12

[← Part III](./part-3-sharper-estimates.md) · [Contents](./README.md)

Everything so far assumed one comparison: one metric, one treatment against one control,
one look (or a correctly-priced sequence of looks). Real experiments have five metrics, three
treatments and a "let's break it down by platform" request from the PM — and every extra
comparison is another lottery ticket for a false positive. Hour 10 is the correction
machinery; Hour 11 is the honest way to slice results (and the drift-and-contamination
checks that ride alongside — one now computed by the platform, two not); Hour 12 is the
engineering discipline that makes any number the platform ever showed recomputable: seeds,
engine versions, fingerprint tests, snapshots, the cache, and the table that says which
analyses are `ga` and which are `beta`.

---

## Hour 10 — Multiple comparisons: Bonferroni and Benjamini–Hochberg

### What & why

Run one test at α = 0.05 and, if nothing is going on, you'll be fooled 5% of the time. Run
_m_ independent tests and the chance that _at least one_ fools you is `1 − 0.95^m`: 14% for
three metrics, 40% for ten, 64% for twenty. An experiment with three treatments and five
metrics has fifteen p-values on its results page; a "winner somewhere" is close to a coin
flip under the null. A results page with several treatments and metrics needs a declared
multiple-testing policy; picking whichever uncorrected comparison happens to be significant does
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

### How Controlvar does it

The results endpoint takes a `correction_method` and applies it **across the treatment
variants of each metric** — the "which of my three variants beat control?" family:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
def adjusted_p_values(
    p_values: List[Optional[float]], method: str
) -> List[Optional[float]]:
    """Multiple-comparison correction across the treatment variants of one metric.

    Raises:
        UnknownCorrectionMethodError: ``method`` is not one of
            ``CORRECTION_METHODS``, whatever the p-values are. An unknown
            spelling used to mean "no correction", silently.
    """
    if method not in CORRECTION_METHODS:
        raise UnknownCorrectionMethodError(method)
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
    raise AssertionError(f"unreachable: {method!r}")  # pragma: no cover
```

It lives beside `binomial_metric_result` (Part I Hour 2), which calls it;
`AnalysisService._adjusted_p_values` is kept as an alias of it, which is why the Try It
below still works.

Read the BH branch carefully: it walks from the _largest_ rank down, keeping a running
minimum, so the adjusted p-values are non-decreasing in rank — the step-up procedure. A
`None` p-value (a variant with no data) is skipped and doesn't count toward k. And note the
first line: a method that is not one of `none`, `bonferroni` or `benjamini_hochberg` is
refused, even when there are no p-values to correct. An unknown spelling used to mean "no
correction" without a word. `UnknownCorrectionMethodError` is deliberately not a `ValueError`:
the results route answers a `ValueError` with 404 and the export reads one as "no results",
and either would have hidden a misspelt method. Raised, it reaches the route's
`unexpected_failure` (a 500), where somebody sees it.

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

**Where the method comes from** changed since the first edition of this book. The route's
parameters are optional, and omitted means "the experiment's own":

```python
# 📁 backend/app/api/v1/endpoints/results.py
    correction_method: Optional[str] = Query(
        default=None,
        pattern="^(none|bonferroni|benjamini_hochberg)$",
        description=(
            "Multiple-comparison correction for this request. Omit it to use "
            "the experiment's stored correction_method (benjamini_hochberg "
            "unless set otherwise). 'none' shows the uncorrected numbers."
        ),
    ),
```

`resolve_analysis_settings` (`backend/app/services/analysis_settings.py`) does the choosing:
the request's value where it names one, otherwise the experiment's stored
`correction_method` and `confidence_level`. A new experiment stores **`benjamini_hochberg` at
0.95** unless its creator chose otherwise, and both are locked once the experiment leaves
draft, so a running experiment cannot have its correction changed to find a winner. The
resolved values go into the cache key, so `?correction_method=bonferroni` and
`?correction_method=none` never serve each other's cached body, and the response echoes the
`correction_method` and `confidence_level` the numbers were computed under:

```python
# 📁 backend/app/api/v1/endpoints/results.py
def _results_cache_key(
    experiment_id: UUID,
    confidence_level: float,
    correction_method: str,
    breakdown: Optional[str],
) -> str:
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

> **Watch Out!** Read the `correction_method` field of the response, not just the verdict.
> The dashboard asks for the experiment's stored settings and shows what comes back: with a
> correction in force the p-value column carries the adjusted value with the raw p beside it
> ("unadjusted 0.0300"), and a footnote says how many variants were compared; with `none` the
> column is labelled **"unadjusted"**. The badge, the row colour and the recommendation all
> follow the one `is_significant` decision. But an API consumer who passes
> `?correction_method=none` (or whose experiment was stored with `none`) is reading
> _uncorrected_ significance for a multi-variant experiment, and the verdict looks the same
> either way. The first edition of this book warned that the default was `none`; it is no
> longer, which is exactly why the field is worth checking rather than assuming.

> **By the Way** — Bonferroni also belongs in the **power calculation**. If you'll test three
> treatments at α/3, the sample size per variant has to be computed at α/3, not α:
> `calculate_required_sample_size(baseline, mde, alpha=0.05/3)` returns a noticeably larger n
> (the `z_alpha` term grows from 1.96 to 2.39). `GET /results/{id}/sample-size` does this for
> you: under `bonferroni` or `benjamini_hochberg` (the experiment's stored method unless you
> pass another) it plans each comparison at α / (variants − 1), reporting the `alpha` and
> `comparisons` it used. The dashboard's Sample Size tab exposes the same choice.

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
- The method and level default to the experiment's stored ones (`benjamini_hochberg` at 0.95,
  locked after draft); a request may override them for that request. The dashboard shows the
  response's method and labels the raw p "unadjusted"; `/sample-size` plans multi-arm
  experiments at the corrected α.

---

## Hour 11 — Segments and heterogeneity: dimensional breakdowns, HTE, novelty, interactions

### What & why

"Does it work on iOS?" is a reasonable question and a statistical trap. Break a result into
_s_ segments and you've run _s_ more tests (Hour 10), on smaller samples (Hour 3), chosen
after seeing the data (the garden of forking paths). The platform's answer is to allow the
breakdown but label it **exploratory**, Bonferroni-correct it by the number of segments,
and flag **heterogeneous treatment effects (HTE)** — the situation where the effect genuinely
differs by segment, which is the one case where the breakdown deserves a follow-up
experiment rather than a decision.

Three related checks belong alongside because they all attack exchangeability from the
outside. The platform computes one of them (interactions, and only on the beta pair route) and
describes the other two for you to do by hand — see the end of the Interactions discussion below:

- **Novelty** — the effect is real on day 1 and gone by day 14, because users tried the new
  thing once. A declining daily effect is the signature.
- **Interactions** — two concurrent experiments share users, and the combination behaves
  differently from the sum. The 2×2 table (A on/off × B on/off) tests it.
- **SUTVA** — the "stable unit treatment value assumption": my outcome depends only on _my_
  treatment. Social features, marketplaces and anything with contagion violate it; so does
  contamination (control users who saw the treatment).

### How Controlvar does it

**Breakdowns.** `GET /results/{id}?breakdown=platform` groups users by an event metadata key,
computes per-segment results with the corrected α, and marks every segment exploratory. The
base α is `1 − confidence_level` of the analysis (the experiment's stored level unless the
request names one). A segment's denominator is the distinct
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

Be honest about what this is: the chi-square tests whether the _distribution of conversions
across segments_ differs between treatment and control, which is a proxy for "the effect
differs by segment" and is sensitive to segment-size imbalance; the heuristics (coefficient
of variation of lifts > 0.5; lifts of opposing sign) catch the cases that matter most to a
reader. HTE = `True` renders a red `role="alert"` and means "don't average this — investigate."

**Interactions** — two experiments that run at once share users, and a user can be in an arm of
each. There are two routes, and it matters which one you are reading:

- `GET /interactions/scan` (stable) reports the **overlap** of every pair of active experiments
  and nothing else: the Jaccard similarity of their assigned users, with `interaction_result`,
  `novelty_result` and `sutva_result` always `null` (not computed), and a risk level from the
  overlap alone.
- `GET /interactions/{a}/{b}` (**beta**) reports the real overlap of two experiments, in any
  status, and **tests** whether each treatment's effect depends on the other experiment's arm.

The overlap is a Jaccard coefficient, and the risk bands come from two constants:

```python
# 📁 backend/app/services/interaction_detection_service.py
    OVERLAP_THRESHOLD = 0.30  # Flag pairs with > 30 % shared users
    HIGH_OVERLAP_THRESHOLD = 0.60  # Above this the overlap alone is high risk
    ...
    @classmethod
    def risk_from_overlap(cls, overlap: float) -> str:
        if overlap > cls.HIGH_OVERLAP_THRESHOLD:
            return "high"
        if overlap > cls.OVERLAP_THRESHOLD:
            return "medium"
        return "low"
```

`low` at or below 0.30, `medium` above 0.30 up to 0.60, `high` above 0.60. The scan leaves
pairs at or under 0.30 out of its results.

The pair route's test is the interesting part. Take experiment E and one of its treatments, and
the arms of the other experiment O that share users with it. For the shared users the counts form
a 2 × J table: row 0 is E's control, row 1 is E's treatment, one column per arm of O, each cell
holding users and converters on E's primary conversion metric. The question is whether the
treatment's lift **in percentage points** is the same in every column. Under that hypothesis —
the _additive model_ — the rates are `p0j = bⱼ` and `p1j = bⱼ + d`: one baseline per arm of O and
one common difference `d`. The module fits that model by binomial maximum likelihood (by
bisection, with no start value to choose) and takes Pearson's X² of the counts against the
fitted rates, on J − 1 degrees of freedom:

```python
# 📁 backend/app/services/interaction_analysis.py
def interaction_test(n: Sequence, x: Sequence) -> InteractionTest:
    """Pearson's X^2 test of the additive model on one 2 x J table.

    Call it only on a table ``floor_reason`` accepts.
    """
    n_arr, x_arr = _as_tables(n, x)
    fitted = fit_additive_batch(n_arr[None], x_arr[None])
    statistic = float(pearson_statistic_batch(n_arr[None], x_arr[None], fitted)[0])
    ...
    df = n_arr.shape[1] - 1
    return InteractionTest(
        statistic=statistic,
        degrees_of_freedom=df,
        p_value=float(stats.chi2.sf(statistic, df)),
    )
```

A table is tested only when it clears a floor, because the chi-squared approximation is not
calibrated on thin or skewed cells: every cell needs at least `MIN_USERS_PER_CELL = 100` users,
and at least `MIN_EXPECTED_PER_CELL = 25` expected converters _and_ non-converters at the table's
pooled rate (`floor_reason`). Below the floor a row is listed with an `unavailable_reason`
(`too_few_shared_users`, `too_few_conversions`) instead of a p-value; so are experiments in the
same mutual exclusion group (`mutual_exclusion_group`), pairs with `no_shared_users`, an
experiment with `no_metric`, a primary metric that is not a conversion metric
(`not_a_proportion_metric`) and one with `no_control_variant`. The p-values of an experiment's
treatments are then corrected with the experiment's own stored `correction_method`, and
`is_significant` is judged at its `confidence_level`. The beta notice states the limits plainly:
`p_value` is uncorrected, pairs are tested one at a time with no correction across pairs, and
secondary and non-conversion metrics are not tested.

The structural fix for interactions is upstream of statistics: put the two experiments in a
**mutual exclusion group** (Build book Day 4) so no user is in both. The route knows this — it
refuses to test a pair that shares a group.

**Novelty and SUTVA** — described, not computed. The platform has no routine at v0.36.4 that
tests for a novelty effect or a SUTVA violation: the `novelty_result` and `sutva_result` fields
are kept in the scan's response shape and are `null`, and a `null` means "not computed", never
"no novelty". Treat them as checks to do by hand:

- **Novelty.** Compute the daily treatment effect, regress it on the day index, and look at the
  slope. A clearly negative slope is the signature; the only cure is time — extend the minimum
  runtime and measure the steady state. The Try It does this with `scipy.stats.linregress`.
  Mind the units: a cut-off on the slope means something only for effects expressed in one
  consistent unit, so pick the unit before you pick the threshold.
- **SUTVA.** There is no statistic on outcome data that can tell you units interfere. A network
  feature (social, marketplace, anything with contagion) violates it by construction, and
  contamination (control users who saw the treatment) violates it in practice; both are
  design questions. The remedy is a different randomisation unit — a cluster, a market, a time
  slice — not a different p-value.

So an interaction "risk" from `/scan` means "these experiments share a lot of users", which is
the input to the mutual-exclusion decision — not a test of whether they interact. If you want
the test, call the pair route.

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
>>> import numpy as np
>>> from scipy import stats
>>> stats.linregress(np.arange(6), [0.30, 0.22, 0.15, 0.08, 0.02, 0.0]).slope   # novelty by hand: a falling daily effect
-0.062
>>> from backend.app.services.interaction_detection_service import InteractionDetectionService as I
>>> I.compute_user_overlap({"u1", "u2", "u3"}, {"u3", "u4"}), I.has_significant_overlap({"u1", "u2", "u3"}, {"u3", "u4"})
(0.25, False)
>>> [I.risk_from_overlap(o) for o in (0.30, 0.31, 0.60, 0.61)]
['low', 'medium', 'medium', 'high']
>>> from backend.app.services import interaction_analysis as ia
>>> n = [[2000, 2000], [2000, 2000]]        # rows: E's control, E's treatment; columns: the other experiment's two arms
>>> ia.interaction_test(n, [[80, 80], [120, 120]]).p_value > 0.99             # +2pp in both columns: no interaction
True
>>> r = ia.interaction_test(n, [[80, 80], [120, 200]])                         # +2pp in one column, +6pp in the other
>>> round(r.statistic, 2), r.degrees_of_freedom, round(r.p_value, 5)
(14.28, 1, 0.00016)
>>> ia.floor_reason([[2000, 50], [2000, 50]], [[80, 2], [120, 3]]), ia.floor_reason(n, [[2, 2], [3, 3]])
('too_few_shared_users', 'too_few_conversions')
```

### Key takeaways

- Breakdowns: α/segments, Wilson CI at the corrected α, `is_exploratory=True` always,
  rendered as an amber warning; a segment result is a hypothesis, not a decision.
- HTE: chi-square on the 2×N conversion table plus CV-of-lift > 0.5 and opposing-sign
  heuristics → red alert.
- Interactions: `/scan` (stable) reports overlap only (Jaccard; low ≤ 0.30 < medium ≤ 0.60 <
  high). The beta pair route tests whether each treatment's lift differs across the other
  experiment's arms — an additive-model Pearson X² on a 2 × J table, with a floor of 100 users
  and 25 expected converters/non-converters per cell, corrected by the experiment's stored
  method. Prevent interactions with a mutual exclusion group.
- Novelty and SUTVA are not computed by the platform (`novelty_result`, `sutva_result` are
  `null`, which means "not computed"); check novelty by regressing daily effects on the day
  index, and treat SUTVA as a design question.
- Post-stratification gives one reweighted estimate instead of many small ones — in theory;
  the route answers 501.

---

## Hour 12 — Reproducibility: seeds, engine versions, snapshots, and the results contract

### What & why

A results page is evidence in a decision that someone will be asked to justify later — in a
retro, a quarterly review, an audit. "The dashboard said 0.94 in March" is only evidence if
you can _recompute_ 0.94 in September. Three things get in the way:

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

### How Controlvar does it

The seed, from `core` so schemas can import it without cycles:

```python
# 📁 backend/app/core/stats_engine.py
#: Bumped whenever a change alters numbers a client could have persisted
#: (sampling strategy, estimator, default priors, thresholds).
ENGINE_VERSION: str = "1.4.0"

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
| 1.3.0   | CUPED adjusts for each user's own pre-assignment events (`cuped_metric_result`) instead of a placeholder covariate (#217). |
| 1.4.0   | The mSPRT and the confidence sequence use the Agresti–Caffo variance, and Λ is computed in log space and capped (#854). |

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
the numbers — and only writes the snapshot on a _fresh_ computation under the experiment's
**stored** settings, so cached reads don't churn the audit row and a request that asks for
other settings (`?correction_method=none`, say) cannot leave a row that reads as the
experiment's own results:

```python
# 📁 backend/app/api/v1/endpoints/results.py
    Results are cached in Redis (TTL = 5 min for running experiments,
    24 h for completed/paused ones).  Pass ``use_cache=false`` to force
    a fresh computation.
    ...
    cache_key = _results_cache_key(
        experiment_id,
        settings.confidence_level,
        settings.correction_method,
        breakdown,
    )
    # = f"results:{experiment_id}:{ENGINE_VERSION}:{confidence_level}:{correction_method}:{breakdown or ''}"
    ...
    # --- Persist audit snapshots (best-effort; never fails the response) ---
    # Only a computation under the experiment's stored settings is recorded:
    # a request asking for other settings must not leave an audit row that
    # reads as the experiment's own results.
    if settings == stored:
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
(superuser) clears `results:{id}:*` — the thing to reach for after a data backfill. The five-minute TTL is also the first thing to rule out when "I tracked an event and the
dashboard didn't change": the number is _stable_ for five minutes by design.

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
| `cuped` (`GET /results/{id}/cuped`)               | ga     | — (the covariate is each user's own pre-assignment events, #217)                  |
| `interactions` (`GET /interactions/{a}/{b}`)      | beta   | tests each treatment's lift across the other experiment's arms; uncorrected across pairs, conversion metrics only (#219) |
| `sequential` (`GET /results/{id}/sequential`)     | beta   | the decision is mSPRT alone; `alpha_spending` is always empty (#232)              |
| `holdout` (`GET /holdout/{id}/results`)           | beta   | the combined effect of running experiments on users first seen while the holdout was active; shipped flags are not part of it (#445) |
| `warehouse_proportion`, `warehouse_mean`          | ga     | — (computed from sufficient statistics by the core estimators)                    |

A notice is present **exactly** when the status is `beta` — the table refuses any other
combination at import. This is separate from a route's `x-stability` (`stable`/`beta`), which
is about the request and response _shape_: the warehouse routes are `x-stability: beta` while
their numbers are `ga`, and the CUPED route is `x-stability: beta` while its numbers are `ga`.
`/results` itself, `/bayesian` and `/sample-size` carry no `analysis_status`; they compute what
their fields say. Post-stratification has no entry because it computes nothing — it answers
501, and `/interactions/scan` reports overlap only and says so rather than carrying a label.

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
'1.4.0'
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
and `engine_version: "1.4.0"`; the route is not cached, and each call records the decision on
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
- `ENGINE_VERSION` (1.4.0) is bumped when numbers a client could have persisted would change;
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
has shipped in a release, bump it, update `test_stats_determinism.py` and both OpenAPI
snapshots (their `engine_version` defaults follow it), and add the new version's hash beside
the old ones; never edit a released version's hash. (If it has not shipped, replace its own
hash: the change is part of that version.) A client could have persisted numbers computed under
the old prior, and the snapshot's `engine_version` is how a reader tells the two apart. The
`/results` cache key changes with it, so no old answer is served after the upgrade.

**Quiz.** A results response has `correction_method: "none"`, three treatments, one with
`p_value: 0.03, is_significant: true`. Should the PM ship?
→ Not on that alone: uncorrected across three arms, the family-wise rate at 0.05 is ~14%.
Re-request with `bonferroni` (0.09 → not significant) or `benjamini_hochberg`; check
`sample_size_adequate`, the response's `srm` (and that the recommendation is not `INCONCLUSIVE`
because of it) and the primary metric's minimum sample size.

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

**E4.4 — Novelty by hand.** Generate 21 daily effects that decay from +0.15 to 0 exponentially,
plus noise σ = 0.02; fit `scipy.stats.linregress` of the effect on the day index and report the
slope and its p-value; then find the decay rate at which a −0.05/day cut-off stops flagging, and
say what that implies about the units the effect is expressed in.

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
import numpy as np
from scipy import stats
rng = np.random.default_rng(4); days = np.arange(21)
for rate in (0.30, 0.20, 0.10, 0.05):
    y = 0.15 * np.exp(-rate * days) + rng.normal(0, 0.02, 21)
    r = stats.linregress(days, y); print(rate, round(r.slope, 4), r.pvalue)
```

Slopes of −0.0056, −0.0056, −0.0050 and −0.0046 per day, every one with p < 0.001: the decline
is unmistakable statistically, and a −0.05/day cut-off flags **none** of them. A 0.15 effect
decaying over 21 days has an average slope of at most −0.007/day, an order of magnitude inside
the cut-off. That is the lesson about units: a fixed threshold on a slope means something only
for effects expressed in the unit it was chosen for (a relative lift, where a 30% → 0% decline
over a week is −0.05/day). Fed absolute conversion-rate differences, the same cut-off never
fires. Choose the unit first, and prefer the slope's p-value and a plot to a magic number.

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

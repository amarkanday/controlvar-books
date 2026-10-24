# Experimentation Statistics

### _The Experimently Edition — The Maths Behind the Results Page, in 12 Hours_

> **Preface.** This book explains the statistical methods behind Experimently's results and how they are
> implemented. It covers randomisation checks, conversion-rate comparisons, power, sequential
> testing, variance reduction, Bayesian analysis, bandits and multiple comparisons. The examples
> connect calculations to service code and API responses, including the limits of each analysis.
> It is intended for engineers maintaining the results system and people interpreting its output.

---

## Who this book is for & prerequisites

- Engineers building or maintaining the results engine, the advanced-statistics services,
  the bandit scheduler, or the results UI.
- Analysts and PMs who want to know what the dashboard's numbers actually mean and when to
  distrust them.
- You need the **core four** books (especially the Build book's Days 6 and 19) — this
  assumes you know how assignments and events are stored, how results are computed and
  cached, and how the Monte Carlo seed works. Maths: comfortable with means, variances and
  the normal distribution; no measure theory.

## How to use this book

12 lessons in 4 Parts. Same rhythm: **What & why → How Experimently does it (real code) →
Try It Yourself → Key takeaways**, with a Workshop per Part. Code tagged `# 📁 path` is real
Experimently source, quoted as excerpts (abridged where marked). Every lesson ends with a
shell-runnable reproduction: the point of a statistics book about a codebase is that you can
recompute every number the platform shows. Run the Try Its from the repository root with the
virtualenv active and `APP_ENV=test`.

> **Reference commit.** Verified against getexperimently/experimently at `2fe9da1e`
> (`2fe9da1e0e725d867bef4d9e586ed3893c320032`, 0.17.0 plus unreleased changes). Every Try It
> and Solution number in this book was re-run against a `git archive` of that commit; the
> statistics engine there is `ENGINE_VERSION` 1.2.0.

### What is live, what is beta, what is API-only

| Analysis                                   | Status at the reference commit                                              |
| ------------------------------------------ | --------------------------------------------------------------------------- |
| `/results` (Fisher, intervals, correction) | live; every metric analysed as converting users / assigned users             |
| SRM check                                  | live, on the `/results` response; **not shown on the core dashboard**        |
| Sequential (mSPRT + confidence sequence)   | **beta**: no alpha-spending table (Part II)                                  |
| Bayesian (PtBB, expected loss, decision)   | live, **API-only**; the decision is a recommendation, nothing acts on it     |
| CUPED route                                | **beta**: placeholder covariate, reduces ~no variance (Part III Hour 7)      |
| Post-stratification route                  | answers **501** (not available yet)                                          |
| Interactions / novelty routes              | **beta**: overlap only; the rest is `null` (Part IV Hour 11)                 |
| Bandit weights and regret                  | live; regret is stored, not shown on the dashboard                           |
| Warehouse runs (full profile)              | numbers `ga`, routes `x-stability: beta` (Part III, Beyond the Hours)        |

Part IV Hour 12 explains the `analysis_status` table behind the beta labels.

> The interpretation of a result depends on its allocation, metric definition and statistical
> assumptions. Start with those conditions when investigating a surprising result; a computed
> p-value does not establish that the data collection was valid.

---

## Table of Contents

### Part I — Foundations _(Hours 1–3)_ → [`part-1-foundations.md`](./part-1-foundations.md)

- **Hour 1** — Randomisation, exchangeability, and the sample-ratio mismatch check
- **Hour 2** — Proportions: the z-test, Fisher's exact test, confidence intervals, effect size
- **Hour 3** — Power and sample size: what "minimum sample size" protects you from

### Part II — Peeking Safely _(Hours 4–6)_ → [`part-2-sequential.md`](./part-2-sequential.md)

- **Hour 4** — The peeking problem: why looking every day inflates false positives
- **Hour 5** — The mixture SPRT: an evidence ratio you can watch continuously
- **Hour 6** — Always-valid confidence sequences, and why there are no alpha-spending boundaries

### Part III — Sharper Estimates _(Hours 7–9)_ → [`part-3-sharper-estimates.md`](./part-3-sharper-estimates.md)

- **Hour 7** — CUPED: variance reduction from pre-experiment data (the platform's route is beta)
- **Hour 8** — Bayesian A/B: Beta-Binomial posteriors, probability to be best, expected loss
- **Hour 9** — Multi-armed bandits: Thompson Sampling, UCB1, ε-greedy — and what they cost
- _Beyond the Hours_ — Analysing from sufficient statistics (warehouse runs)

### Part IV — Many Metrics, Many Segments _(Hours 10–12)_ → [`part-4-many-comparisons.md`](./part-4-many-comparisons.md)

- **Hour 10** — Multiple comparisons: Bonferroni and Benjamini–Hochberg
- **Hour 11** — Segments and heterogeneity: dimensional breakdowns, HTE, novelty, interactions
- **Hour 12** — Reproducibility: seeds, engine versions, snapshots, and the results contract

---

## Where this fits

```
Core path:  Python → FastAPI → React/Next.js → Build a Platform
Specialize:                                    ├─ ⭐ Experimentation Statistics (this)
                                               ├─ SDK Engineering
                                               └─ Platform Operations
```

_Turn to [Part I](./part-1-foundations.md). 📐_

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Measurement and experiment design](../production-engineering/part-1-experiment-design.md#hour-1--measurement-and-experiment-design)
- [LLM evaluation engineering](../production-engineering/part-1-experiment-design.md#hour-3--llm-evaluation-engineering)

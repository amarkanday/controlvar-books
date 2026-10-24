# Experimentation Statistics

### _The Controlvar Edition — The Maths Behind the Results Page, in 12 Hours_

> **Preface.** On a Tuesday a dashboard says "treatment is 12% better, p = 0.03", and a
> product manager asks the only question that matters: _can we ship?_ Behind that one line sit
> half a dozen separate claims, and each is only as good as the method that produced it and the
> assumption that method makes. The p-value assumes the two groups were comparable, so the
> platform checks the split before it lets the page recommend anything. "Probability to be
> best 0.94" assumes a prior and a seed. "Stop now" assumes you looked at the data in a way the
> guarantee allows. This is a **specialization** book, not a core-path one. After the
> [core four](../README.md) (Python → FastAPI → React/Next.js → Build a Platform) you
> understand the whole system; this book goes deep on the part that makes Controlvar a
> _decision_ tool rather than a counting tool.
>
> You'll learn what each number means, how the code computes it, where it lies, and how the
> platform stops it lying (SRM checks, minimum sample sizes, always-valid p-values, seeded
> Monte Carlo). Read this if you'll touch `sufficient_stats_analysis.py` (where the proportion
> and mean maths now lives), `analysis_service.py`, `srm_service.py`,
> `sequential_testing_service.py`, `cuped_service.py`, `bayesian_service.py`,
> `bandit_service.py`, `interaction_analysis.py`, `core/stats_engine.py`,
> `core/analysis_status.py`, the results endpoint, or the results dashboard — or if you'll be
> the person a product manager asks "can we ship?" By Hour 12 you will be able to recompute
> every number on the results page from a shell, say which of them is a recommendation and which
> is a measurement, and explain why a statistically perfect p-value can still be arithmetic
> on a lie.
>
> Controlvar was called Experimently before 0.35.0; you will still meet the old name in some
> identifiers (the Python distribution is `experimently-platform`, for one).

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

12 lessons in 4 Parts. Every lesson has the same rhythm:

1. **What & why** — the concept in general terms, and the concrete failure that motivates it.
2. **How Controlvar does it** — real code, quoted as excerpts (abridged where marked).
3. **Try It Yourself** — a shell-runnable reproduction: the point of a statistics book about a
   codebase is that you can recompute every number the platform shows.
4. **Key takeaways** — the 3–5 things to remember.

Each Part ends with a **Workshop** (Q&A, quiz, exercises, solutions). Run the Try Its from the
repository root with the virtualenv active and `APP_ENV=test`, and point the database and Redis
settings at ports nothing listens on if you are working on a machine that also runs the real
stack: none of the numbers here needs a database.

> **Convention in this book.** Code blocks tagged `# 📁 path/to/file` are real Controlvar
> source at v0.36.4, quoted as excerpts. Blocks with no path are illustrative (the Try Its and
> solutions are code you run, not excerpts). `# ✅` marks the idiom we want and `# ❌` the
> anti-pattern it replaces. The sidebars follow the original books: **By the Way** for a
> side road, **Watch Out!** for a trap, **Did You Know?** for the thing you will want to tell
> someone. You will want the repository open next to this book, and a Python shell with `scipy`.

### What is live, what is beta, what is API-only

| Analysis                                   | Status at v0.36.4                                                                |
| ------------------------------------------ | -------------------------------------------------------------------------------- |
| `/results` (Fisher, intervals, correction) | live; every metric analysed as converting users / assigned users, at the experiment's stored level and correction |
| SRM check                                  | live: on the `/results` response and above the dashboard's tabs; a failed check turns the recommendation into `INCONCLUSIVE` |
| Sequential (mSPRT + confidence sequence)   | **beta**: no alpha-spending table (Part II)                                      |
| Bayesian (PtBB, expected loss, decision)   | live, on the Overview tab and the API; the decision is a recommendation, nothing acts on it |
| CUPED route                                | numbers `ga` (each user's own pre-assignment events), route `x-stability: beta`, API only (Part III Hour 7) |
| Post-stratification route                  | answers **501** (not available yet)                                              |
| Interactions                               | `/scan`: overlap only; the pair route is **beta** and tests the interaction (Part IV Hour 11) |
| Novelty, SUTVA                             | not computed (Part IV Hour 11)                                                   |
| Bandit weights and regret                  | live; regret is stored, not shown on the dashboard                               |
| Warehouse runs (full profile)              | numbers `ga`, routes `x-stability: beta` (Part III, Beyond the Hours)            |

Part IV Hour 12 explains the `analysis_status` table behind the beta labels.

## The one idea to anchor on

A randomised experiment makes two groups **exchangeable** — identical in expectation except for
the treatment. Every statistic on the platform is a way of asking "given exchangeability, how
surprising is this difference?" and every honesty check is a way of asking "was exchangeability
actually true?" Break exchangeability (an SRM, a bandit shifting traffic, a targeting bug) and
the p-value is arithmetic on a lie. The interpretation of any result therefore depends on its
allocation, its metric definition and its assumptions; when a result surprises you, start with
those conditions, because a computed p-value does not establish that the data collection was
valid.

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

- **Hour 7** — CUPED: variance reduction from pre-experiment data
- **Hour 8** — Bayesian A/B: Beta-Binomial posteriors, probability to be best, expected loss
- **Hour 9** — Multi-armed bandits: Thompson Sampling, UCB1, ε-greedy — and what they cost
- _Beyond the Hours_ — Analysing from sufficient statistics (warehouse runs)

### Part IV — Many Metrics, Many Segments _(Hours 10–12)_ → [`part-4-many-comparisons.md`](./part-4-many-comparisons.md)

- **Hour 10** — Multiple comparisons: Bonferroni and Benjamini–Hochberg
- **Hour 11** — Segments and heterogeneity: dimensional breakdowns, HTE, interactions, and the checks nobody computes yet
- **Hour 12** — Reproducibility: seeds, engine versions, snapshots, and the results contract

---

## Where this fits

```
Core path:  Python → FastAPI → React/Next.js → Build a Platform
Specialize:                                    ├─ ⭐ Experimentation Statistics (this)
                                               ├─ SDK Engineering
                                               └─ Platform Operations
```

## A note on accuracy

Verified against Controlvar v0.36.4 (`79f8ed18`; `79f8ed1897733c74b288dac14e036fabbbe85f12`). The
statistics engine there is `ENGINE_VERSION` 1.4.0. The numbers in the Try Its and Solutions of
Parts I–III, and of Hours 10 and 11 and the seed arithmetic of Hour 12, were re-run against a
clone of that tag, importing the app's own modules with the database and Redis pointed at ports
nothing listens on. What was **not** run: the steps that need a running stack (the `curl`
calls, the `analysis_snapshots` query, the cache invalidation in Hour 12), the dashboard, and
the warehouse routes themselves; those are read from the code. Excerpts tagged `# 📁` are
checked by the repository's snippet gate (`tools/README.md`: V2 and V3), which verifies that the
file exists at the pinned tag and that every identifier in the excerpt is in it; it does not
compare literals, so a changed default inside a quoted line is for you to re-check.

## Related reading — Production Engineering

The Production Engineering specialization picks up the experiment-design side of the same
questions; its own README states its scope and what was verified.

- [Measurement and experiment design](../production-engineering/part-1-experiment-design.md#hour-1--measurement-and-experiment-design)
- [LLM evaluation engineering](../production-engineering/part-1-experiment-design.md#hour-3--llm-evaluation-engineering)

_Turn to [Part I](./part-1-foundations.md). 📐_

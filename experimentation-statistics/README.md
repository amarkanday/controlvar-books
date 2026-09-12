# Experimentation Statistics

### _The Experimently Edition — The Maths Behind the Results Page, in 12 Hours_

> **Preface.** This is a **specialization** book, not a core-path one. After the
> [core four](../README.md) (Python → FastAPI → React/Next.js → Build a Platform) you
> understand the whole system; this book goes deep on the part that makes Experimently a
> _decision_ tool rather than a counting tool: the statistics. A results page is a set of
> claims — "treatment is 12% better," "p = 0.03," "probability to be best 0.94," "stop now" —
> and each claim is only as good as the method behind it and the assumptions it makes. You'll
> learn what each number means, how the code computes it, where it lies, and how the
> platform stops it lying (SRM checks, minimum sample sizes, always-valid p-values, seeded
> Monte Carlo). Read this if you'll touch `analysis_service.py`, `sequential_testing_service.py`,
> `cuped_service.py`, `bayesian_service.py`, `bandit_service.py`, the results endpoint, or the
> results dashboard — or if you'll be the person a product manager asks "can we ship?"

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
Experimently source. Every lesson ends with a shell-runnable reproduction: the point of a
statistics book about a codebase is that you can recompute every number the platform shows.

> **The one idea to anchor on:** a randomised experiment makes two groups
> **exchangeable** — identical in expectation except for the treatment. Every statistic on
> the platform is a way of asking "given exchangeability, how surprising is this
> difference?" and every honesty check is a way of asking "was exchangeability actually
> true?" Break exchangeability (an SRM, a bandit shifting traffic, a targeting bug) and the
> p-value is arithmetic on a lie.

---

## Table of Contents

### Part I — Foundations _(Hours 1–3)_ → [`part-1-foundations.md`](./part-1-foundations.md)

- **Hour 1** — Randomisation, exchangeability, and the sample-ratio mismatch check
- **Hour 2** — Proportions: the z-test, Fisher's exact test, confidence intervals, effect size
- **Hour 3** — Power and sample size: what "minimum sample size" protects you from

### Part II — Peeking Safely _(Hours 4–6)_ → [`part-2-sequential.md`](./part-2-sequential.md)

- **Hour 4** — The peeking problem: why looking every day inflates false positives
- **Hour 5** — The mixture SPRT: an evidence ratio you can watch continuously
- **Hour 6** — Always-valid confidence sequences and alpha-spending boundaries

### Part III — Sharper Estimates _(Hours 7–9)_ → [`part-3-sharper-estimates.md`](./part-3-sharper-estimates.md)

- **Hour 7** — CUPED: variance reduction from pre-experiment data
- **Hour 8** — Bayesian A/B: Beta-Binomial posteriors, probability to be best, expected loss
- **Hour 9** — Multi-armed bandits: Thompson Sampling, UCB1, ε-greedy — and what they cost

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

# Engineering field guides

[← Documentation home](../README.md)

These nine guides connect the existing books to engineering decisions you will make on Experimently.
They are intended for engineers who can already read the relevant language; you do not have to finish
the entire core curriculum before using them. Each guide includes a worked problem, an exercise with
a solution, and links to the implementation or product documentation that supports its claims.

## Pick a task

| I need to… | Start here | Evidence to produce |
|---|---|---|
| Design a trustworthy experiment | [Measurement and experiment design](measurement.md) | Metric contract and instrumentation reconciliation |
| Review an endpoint's access boundary | [Security engineering](security-engineering.md) | Actor/action matrix and denied-request tests |
| Reason about retries, replicas and outages | [Distributed correctness](distributed-correctness.md) | Failure matrix and recovery evidence |
| Change a schema or investigate a slow query | [Database evolution and performance](database-evolution.md) | Compatibility matrix and query-plan comparison |
| Run an experiment through a shipping decision | [Complete experiment walkthrough](experiment-lifecycle.md) | Decision record, including a no-ship branch |
| Compare prompts or AI models | [LLM evaluation engineering](llm-evaluation.md) | Versioned dataset, scoring rubric and failure accounting |
| Handle identifiers and retention | [Privacy and data lifecycle](privacy-data-lifecycle.md) | Data inventory and verified deletion scope |
| Ship, operate and retire a feature flag | [Feature-flag lifecycle](flag-lifecycle.md) | Owner, rollback rehearsal and removal plan |
| Make a change across the product | [Changing Experimently safely](changing-experimently.md) | Impact map, focused validation and release notes |

For a backend engineer, read measurement → distributed correctness → database evolution → changing
Experimently. For a dashboard engineer, read measurement → security engineering → flag lifecycle →
changing Experimently. For an experiment owner, read measurement → complete walkthrough → flag
lifecycle. LLM evaluation adds a separate measurement layer; it does not replace experiment design.

## What is verified

Implementation descriptions in this collection were reviewed on **2026-10-23** against app commit
**`07a430df5b54b0e5dd71ddca95071ced05e9e36c`**. Links to app code and product guides are pinned to that
commit. [sources.json](sources.json) records the reference and source paths for every guide.

“Implemented” describes that snapshot. “Recommended practice” describes an engineering procedure,
not a feature the platform automatically provides. “Illustrative” data and code teach a concept and
are not production results. No paid model calls, production deployments, destructive database
commands or external accounts are needed to read the lessons. Exercises that use the app should run
in a disposable development environment following the existing [onboarding guide](../guides/onboarding.md).

The older books retain their own reference commits. This collection does not silently update their
claims. When they disagree, compare the named snapshots before assuming the implementation regressed.
For current commands, use the product guide at the release you actually run.

## Review a guide after an app change

Compare its source paths in the manifest between the old and proposed app commits. Review behavioral
changes, not just moved filenames: a schema can stay identical while an estimator changes. Update the
guide, its pinned links, and its manifest reference together, then run the docs checks described in
[the tools guide](../tools/README.md). The collection deliberately has its own reference; do not
advance the older books' global reference without auditing them.

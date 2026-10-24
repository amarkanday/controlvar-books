# SDK Engineering

### _The Controlvar Edition — Sixteen SDKs, One Contract, in 12 Hours_

> **Preface.** This is a **specialization** book. The core four books explain how the
> platform decides and measures; this one is about the thin, boring, absolutely critical
> layer between the platform and the product code that calls it: the SDKs. Controlvar has
> sixteen of them — Python, JavaScript/TypeScript, React, React Native, an edge runtime
> client, two OpenFeature providers, Go, Java (plus a Spring Boot starter), Swift, Kotlin,
> Ruby, PHP, .NET, Elixir and Flutter — and in September 2026 every one of them was rewired
> to the same four-endpoint contract after the discovery that most of them had been bucketing
> users locally with a hash the server didn't share. That story is the spine of this book,
> and it has a second act: the server then adopted the SDKs' hash for experiment assignment,
> deleted the second decider that disagreed with both, and the JavaScript and Python SDKs
> gained an opt-in local flag evaluation mode that reproduces the server's answer from the
> server's own ruleset or asks. The book covers what the contract is and why "the server
> decides" is its first rule; how a client is built so that it is fast, safe and honest
> about failure; how the same design is ported across languages without drifting; and how you
> _prove_ sixteen implementations agree — golden vectors offline, a generated corpus for local
> evaluation, a live harness in CI, and two demo apps that consume the React SDK from source.
>
> Controlvar was called Experimently before 0.35.0; you will still meet the old name in some
> identifiers (an environment-variable fallback, a decoy value in the live harness).

---

## Who this book is for & prerequisites

- Engineers who will maintain an existing SDK, port the client to a seventeenth language, or
  integrate an SDK into a product.
- Backend engineers who touch the tracking or feature-flag endpoints — you are changing a
  contract sixteen clients depend on, and this book is where the contract lives.
- Prerequisites: the **core four** (especially Build book Days 4–5 on assignment and
  tracking, and Day 20 on SDKs). Fluency in one of Python or TypeScript; reading knowledge of
  the others is enough — the point of Part III is that the design is language-independent.

## How to use this book

12 lessons in 4 Parts, same rhythm as the rest of the library: **What & why → How Controlvar
does it (real code) → Try It Yourself → Key takeaways**, with a Workshop (Q&A, Quiz, Exercises,
Solutions) at the end of each Part. Every SDK has a `contract_smoke` entry point; the
Try-It-Yourself sections mostly ask you to run it, break it, and read what it prints. The
user-facing reference this book explains is in the app repository: `docs/sdk-guide.md`, and
one page per SDK under `docs/sdk/` (including `docs/sdk/local-evaluation.md`).

> **Convention in this book.** Blocks tagged `# 📁 path` are real Controlvar source at
> v0.36.4; blocks with no path are illustrative. `# ✅` marks the idiom and `# ❌` the
> anti-pattern it replaces. Keep a clone of
> [`Controlvar/controlvar`](https://github.com/Controlvar/controlvar) at `v0.36.4` open beside
> the book.

> **The one idea to anchor on:** an SDK is a **cache in front of four HTTP calls** plus a
> **policy for what to do when those calls fail**. Everything else — hooks, providers, DI
> starters, edge adapters — is packaging. The one exception, local flag evaluation in the
> JavaScript and Python SDKs, keeps the idea intact: it answers only what it can prove the
> server would answer, from the server's own ruleset, and asks otherwise. If you keep the
> cache semantics and the failure policy identical across languages, sixteen SDKs behave as
> one; if you let either drift, you have sixteen products.

---

## Table of Contents

### Part I — The Contract _(Hours 1–3)_ → [`part-1-the-contract.md`](./part-1-the-contract.md)

- **Hour 1** — Four endpoints and one rule: the server decides
- **Hour 2** — The consistent hash: the function the SDKs and the server's experiment assignment share, and the golden vectors
- **Hour 3** — Caching, fan-out, failure policy and local evaluation: the semantics every SDK shares

### Part II — Building a Client _(Hours 4–6)_ → [`part-2-building-a-client.md`](./part-2-building-a-client.md)

- **Hour 4** — Anatomy of the Python SDK: transport, request, cache, types
- **Hour 5** — The JavaScript SDK: fetch, timeouts, in-flight dedupe, `onError`
- **Hour 6** — The React SDK: provider, hooks, SSR `ServerClient`, and the demo apps

### Part III — Sixteen SDKs _(Hours 7–9)_ → [`part-3-sixteen-sdks.md`](./part-3-sixteen-sdks.md)

- **Hour 7** — Porting the design: Go, Java + Spring Boot, Swift, Kotlin, Ruby, PHP, .NET, Elixir, Flutter
- **Hour 8** — Standards and edges: OpenFeature providers, the edge runtime client, React Native
- **Hour 9** — Case study: two deciders, four hashes, and how the drift was closed

### Part IV — Proving Agreement _(Hours 10–12)_ → [`part-4-proving-agreement.md`](./part-4-proving-agreement.md)

- **Hour 10** — Unit testing a client: fake transports and recorded requests
- **Hour 11** — The live contract harness: `contract_smoke`, the runner, and the CI job
- **Hour 12** — Status, drift, change management and releases: how to change a contract sixteen clients depend on

---

## Where this fits

```
Core path:  Python → FastAPI → React/Next.js → Build a Platform
Specialize:                                    ├─ Experimentation Statistics
                                               ├─ ⭐ SDK Engineering (this)
                                               └─ Platform Operations
```

_Turn to [Part I](./part-1-the-contract.md). 🔌_

## A note on accuracy

Verified against Controlvar v0.36.4 (`79f8ed18`). Every snippet tagged `# 📁` was re-copied
from the file at that tag; where a snippet leaves lines out it says so with `...`. The counts
were measured there: the Python SDK's suite (210 tests, run with `pytest`) and the JavaScript
SDK's suite (205 tests, run with `npx jest`) were run for this edition, as were the Hour 1–3
Python examples that use `FakeTransport`, the hash arithmetic (buckets 69 and 79), the
agreement rate between the API and the undeployed Lambda hasher (0.4996 over 10,000 users),
`scripts/check_sdk_version.py`, and the Exercise E4.4 script. Not run for this edition: the
live harness against a backend (it needs PostgreSQL, Redis and the API), the Go, Java, Swift,
Kotlin, Ruby, PHP, .NET, Elixir and Flutter suites, the edge, React Native and OpenFeature
suites, the ShopLab demo, and the CI workflows themselves; for those the book quotes the code
and the workflow files, not a run. Files move; if a path 404s, `git grep` the symbol. To
re-check a claim yourself, clone the repository, check out `v0.36.4`, and use the commands in
the Try-It sections.

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book retains its own reference tag. See that book's README for scope and verification.

- [Measurement and experiment design](../production-engineering/part-1-experiment-design.md#hour-1--measurement-and-experiment-design)
- [Distributed correctness](../production-engineering/part-2-reliable-systems.md#hour-4--assignments-transactions-and-retries)
- [Security engineering](../production-engineering/part-3-security-and-privacy.md#hour-7--security-engineering-through-the-request-path)

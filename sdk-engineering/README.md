# SDK Engineering

### _The Experimently Edition — Sixteen SDKs, One Contract, in 12 Hours_

> **Preface.** This book explains the contract shared by Experimently's sixteen SDKs and the work involved
> in maintaining it across languages. It covers server assignment, flag evaluation, transport,
> caching and error handling, then compares client implementations and their tests. The assignment
> hash mismatch provides a case study in how independently maintained clients can diverge.
> Local flag evaluation is covered separately because it adds ruleset distribution and refresh
> behavior to the client.
>
> Verified against getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased
> changes, including three breaking ones: #747, #769, #770). No SDK package has been
> published to a registry at that commit; Hour 12 says what exists and how to install from
> source.

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

The 12 lessons are grouped into four Parts. Each introduces a contract requirement, examines its
implementation and provides an exercise. Workshops include questions, quizzes and worked solutions.
Code marked `# 📁 path` quotes source. The exercises use each SDK's `contract_smoke` entry point to
inspect normal behavior and failure cases. The app repository's `docs/sdk-guide.md` and individual
pages under `docs/sdk/` provide the user-facing reference.

> Keep assignment, evaluation, caching and failure behavior consistent across SDKs. Packaging
> differs by language, but an application should not receive a different decision because it uses
> a different client. Local flag evaluation adds a versioned ruleset; experiment assignment remains
> a server operation.

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

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Measurement and experiment design](../production-engineering/part-1-experiment-design.md#hour-1--measurement-and-experiment-design)
- [Distributed correctness](../production-engineering/part-2-reliable-systems.md#hour-4--assignments-transactions-and-retries)
- [Security engineering](../production-engineering/part-3-security-and-privacy.md#hour-7--security-engineering-through-the-request-path)

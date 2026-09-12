# SDK Engineering

### _The Experimently Edition — Fifteen Clients, One Contract, in 12 Hours_

> **Preface.** This is a **specialization** book. The core four books explain how the
> platform decides and measures; this one is about the thin, boring, absolutely critical
> layer between the platform and the product code that calls it: the SDKs. Experimently ships
> fifteen of them — Python, JavaScript/TypeScript, React, React Native, an edge runtime
> client, two OpenFeature providers, Go, Java (plus a Spring Boot starter), Swift, Kotlin,
> Ruby, PHP, .NET, Elixir and Flutter — and in September 2026 every one of them was rewired
> to the same four-endpoint contract after the discovery that most of them had been bucketing
> users locally with a hash the server didn't share. That story is the spine of this book:
> what the contract is and why "the server decides" is its first rule; how a client is built
> so that it is fast, safe and honest about failure; how the same design is ported across
> languages without drifting; and how you _prove_ fifteen implementations agree — golden
> vectors offline, a live harness in CI, and two demo apps that consume the React SDK from
> source.

---

## Who this book is for & prerequisites

- Engineers who will maintain an existing SDK, port the client to a sixteenth language, or
  integrate an SDK into a product.
- Backend engineers who touch the tracking or feature-flag endpoints — you are changing a
  contract fifteen clients depend on, and this book is where the contract lives.
- Prerequisites: the **core four** (especially Build book Days 4–5 on assignment and
  tracking, and Day 20 on SDKs). Fluency in one of Python or TypeScript; reading knowledge of
  the others is enough — the point of Part III is that the design is language-independent.

## How to use this book

12 lessons in 4 Parts, same rhythm as the rest of the library: **What & why → How Experimently
does it (real code) → Try It Yourself → Key takeaways**, with a Workshop per Part. Code tagged
`# 📁 path` is real Experimently source. Every SDK has a `contract_smoke` entry point; the
Try-It-Yourself sections mostly ask you to run it, break it, and read what it prints.

> **The one idea to anchor on:** an SDK is a **cache in front of four HTTP calls** plus a
> **policy for what to do when those calls fail**. Everything else — hooks, providers, DI
> starters, edge adapters — is packaging. If you keep the cache semantics and the failure
> policy identical across languages, fifteen SDKs behave as one; if you let either drift,
> you have fifteen products.

---

## Table of Contents

### Part I — The Contract _(Hours 1–3)_ → [`part-1-the-contract.md`](./part-1-the-contract.md)

- **Hour 1** — Four endpoints and one rule: the server decides
- **Hour 2** — The consistent hash: what it's for, what it's no longer for, and the golden vectors
- **Hour 3** — Caching, fan-out and failure policy: the semantics every SDK shares

### Part II — Building a Client _(Hours 4–6)_ → [`part-2-building-a-client.md`](./part-2-building-a-client.md)

- **Hour 4** — Anatomy of the Python SDK: transport, request, cache, types
- **Hour 5** — The JavaScript SDK: fetch, timeouts, in-flight dedupe, `onError`
- **Hour 6** — The React SDK: provider, hooks, SSR `ServerClient`, and the demo apps

### Part III — Fifteen Languages _(Hours 7–9)_ → [`part-3-fifteen-languages.md`](./part-3-fifteen-languages.md)

- **Hour 7** — Porting the design: Go, Java + Spring Boot, Swift, Kotlin, Ruby, PHP, .NET, Elixir, Flutter
- **Hour 8** — Standards and edges: OpenFeature providers, the edge runtime client, React Native
- **Hour 9** — The other implementation: Lambda assignment, DynamoDB and the drift problem

### Part IV — Proving Agreement _(Hours 10–12)_ → [`part-4-proving-agreement.md`](./part-4-proving-agreement.md)

- **Hour 10** — Unit testing a client: fake transports and recorded requests
- **Hour 11** — The live contract harness: `contract_smoke`, the runner, and the CI job
- **Hour 12** — Status, drift and change management: how to change a contract fifteen clients depend on

---

## Where this fits

```
Core path:  Python → FastAPI → React/Next.js → Build a Platform
Specialize:                                    ├─ Experimentation Statistics
                                               ├─ ⭐ SDK Engineering (this)
                                               └─ Platform Operations
```

_Turn to [Part I](./part-1-the-contract.md). 🔌_

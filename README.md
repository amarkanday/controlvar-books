# experimently-docs

Internal Experimently documentation kept **out of the app repo**
([`getexperimently/experimently`](https://github.com/getexperimently/experimently), public, Apache-2.0):
onboarding, operator guides, learning paths, troubleshooting and usage guides for engineers joining the
platform.

Experimently is an A/B-testing and feature-flag platform: a FastAPI backend (Python 3.11,
SQLAlchemy, Pydantic v2, PostgreSQL, Redis), a Next.js dashboard (TypeScript, React,
Tailwind), sixteen client SDKs (including two OpenFeature providers) that all speak one HTTP contract,
optional `modules/` that turn the **core** profile into the **full** one, signed container images on GHCR,
a production Compose file, AWS infrastructure defined in CDK, and a statistics engine
(Fisher's exact test, sequential/mSPRT, Bayesian, multi-armed bandits; CUPED in beta). About 91,000 lines of
application Python, 229,000 lines of backend tests (9,100+ test functions, 16,000+ tests collected), 52,000 lines of dashboard
TypeScript and 44,000 lines of SDK code (measured at `2fe9da1e`, v0.17.0 plus unreleased).

> **Updated 2026-10-03 (issue #1).** Every book and guide was audited claim by claim against the app and
> rewritten where the code had moved on. Each book's README names the app commit it was checked against:
> `a4a6dbd2` for most, `2fe9da1e` for *Experimentation Statistics* and *Learn Python* (both are v0.17.0 plus
> unreleased work). When the app moves on, re-check against the newer commit before trusting a specific.

## Guides — read these first

- **[`guides/onboarding.md`](./guides/onboarding.md)** — your first day and first week: accounts,
  the clone-to-running-dashboard checklist, how tests and PRs work here, and which book to
  read next.
- **[`guides/usage.md`](./guides/usage.md)** — how to _use_ the platform: create an experiment,
  target it, ship a flag with a gradual rollout and a safety net, read the results, integrate an
  SDK.
- **[`guides/troubleshooting.md`](./guides/troubleshooting.md)** — symptom → cause → fix, for
  local setup, tests, the API, the dashboard, SDKs, schedulers and AWS.

## Operator guides — for whoever runs the project

- **[`guides/deploying.md`](./guides/deploying.md)** — Docker Compose and AWS; the AWS path
  step by step (sign-in, secrets by complete ARN, DNS and certificate, the deploy role, `cdk diff` and
  `cdk deploy`), every later deploy, rollback and teardown. Founder-only steps are checklists.
- **[`guides/releasing.md`](./guides/releasing.md)** — conventional titles, the release-please pull
  request and how to check it, why `release.yml` must not be dispatched by hand after it, the docs site,
  verifying image signatures and SBOMs, scanning before a package goes public, and SDK releases.
- **[`guides/open-source.md`](./guides/open-source.md)** — running the public project: licensing and
  DCO, branch protection and required checks, secret scanning and the leak guard, security advisories,
  Dependabot, the package registries, the docs site and landing page, and how work is planned and reviewed.

## Learning path

An **eight-book learning path** that takes a new engineer from zero to extending the platform —
taught **through the real Experimently codebase**, in the O'Reilly/Sams "in N Hours/Days"
format. A **core path** of four books goes from absolute zero to understanding and extending
the whole system; then **four specializations** go deep on the subsystems that make the
product what it is.

**Core path — read in order:**

1. **[`learn-python-in-24-hours/`](./learn-python-in-24-hours/README.md)** — Python from
   absolute zero to the modern, typed Python this backend is written in (start here if you
   don't know Python).
2. **[`learn-fastapi-in-24-hours/`](./learn-fastapi-in-24-hours/README.md)** — the backend
   framework layer: FastAPI, Pydantic v2, dependency injection, SQLAlchemy 2, Alembic, the
   middleware stack, background schedulers, and testing with `TestClient`. Assumes the Python
   book.
3. **[`learn-react-nextjs-in-24-hours/`](./learn-react-nextjs-in-24-hours/README.md)** —
   TypeScript, React and Next.js from the ground up through the real dashboard: the API
   client, `AuthContext`, the pages router, forms, the targeting-rule builder, Recharts results,
   Jest and Playwright. The book that lets you _write_ screens, not just read them.
4. **[`build-a-platform-in-28-days/`](./build-a-platform-in-28-days/README.md)** — the **whole
   Experimently architecture** end-to-end: the assign → track → analyse loop, the data model,
   access by role, feature flags and the rules engine, the background schedulers and their
   advisory lock, safety auto-rollback, the statistics engine, local evaluation, the core/modules split,
   the SDK contract, testing and the required checks, signed containers and AWS. Assumes the three prior books. _After this capstone, you can read and
   extend the codebase._

**Specializations — after the core path, in any order:**

5. **[`experimentation-statistics/`](./experimentation-statistics/README.md)** — the maths the
   product is built on and how the code implements it: SRM, Fisher's exact test, power, the
   peeking problem and the mSPRT confidence sequence, CUPED (beta), Beta-Binomial Bayesian analysis,
   Thompson Sampling and UCB1 bandits, multiple-testing correction, warehouse analysis from summary
   statistics, and reproducible Monte Carlo (seeded RNG).
6. **[`sdk-engineering/`](./sdk-engineering/README.md)** — one HTTP contract, sixteen SDKs:
   the endpoints, why the server buckets users and when an SDK may evaluate flags locally (beta), the MD5
   consistent hash and its golden vectors, the anatomy of a client (transport, cache, 429 retry), the
   React SDK, porting to new languages, the live contract harness, SDK releases, and the ShopLab/StreamPulse
   demo apps.
7. **[`platform-operations/`](./platform-operations/README.md)** — running it: the compose
   and production compose files, the container entrypoint, bootstrap and seeds, the CDK
   stacks (9 core + 3 module: VPC, Aurora, Redis, ECS Fargate + ALB + CodeDeploy blue/green, analytics,
   Glue), secrets by complete ARN, structured logging, Prometheus and health probes, alarms, the deploy and
   rollback workflows, the migration task as the only schema writer, and releases.

8. **[`production-engineering/`](./production-engineering/README.md)** — decisions, reliability and
   safe change in 12 hours: experiment design and the full decision lifecycle, LLM evaluation,
   retries and replicas, database evolution, security and privacy, flag retirement, and a worked
   change review across API, storage, dashboard, SDKs and profiles. Four Parts with workshops,
   exercises and worked solutions. Verified at `07a430df5b54b0e5dd71ddca95071ced05e9e36c`
   (2026-10-23); the earlier books keep their original references. Experienced engineers can start
   with the book's task-based reading routes.

## Conventions used across every book

- Code blocks tagged `# 📁 path/to/file.py` (or `// 📁 path/to/file.ts`) are real, current
  Experimently source, quoted from the repository. Blocks with no path are illustrative.
- `# ✅` marks the idiom we want; `# ❌` marks the anti-pattern it replaces.
- Sidebars — **By the Way**, **Watch Out!**, **Did You Know?** — run throughout.
- Every Part ends with a **Workshop** (Q&A, a quiz, exercises with worked solutions).
- You need a clone of `getexperimently/experimently` open alongside the books; the
  _Try It Yourself_ / _Build It_ exercises send you into it. Files move — if a path 404s,
  `git grep` the symbol. The _patterns_ outlast the line numbers.

Snapshot for every quoted snippet: the app commit named in that book's README (see the note at the top).
Commits made before development moved into the public repository (up to about 2026-09-24) carry the old
private repository's PR numbers in their subjects, and those numbers mean something else on the public
tracker. The books cite commit hashes for that period.

## Checking the docs

Six optional checks hold the books to a pinned app commit: banned terms, paths, quoted snippets, links,
Try-It numbers and secrets. Run them with `make check` when you change a book; see
[`tools/README.md`](./tools/README.md). They are a tool, not a merge gate.

> Private repository. Not for external distribution.

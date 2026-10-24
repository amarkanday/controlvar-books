# Platform Operations

### _The Experimently Edition — Running the Platform, From Laptop to Production, in 12 Hours_

> **Preface.** This is a **specialization** book. The core four books teach you how
> Experimently is built; this one teaches you how it _runs_ — on your laptop, in a Docker
> Compose stack or a Helm release a customer can self-host, and on AWS behind an ALB with
> blue/green deploys — and how you know it's healthy, what you look at when it isn't, and
> how a change gets from a pull request to production without anyone holding their breath.
> Every piece of it is real: the development `docker-compose.yml` with its profiles and seeds,
> the production compose file and the Helm chart that run the published images, the two
> Dockerfiles with their `core` and `full` targets, the entrypoint that checks its
> configuration, waits for Postgres and bootstraps the schema (everywhere but AWS, where a
> one-shot migration task is the only schema writer), nine CDK stacks (twelve with the
> optional modules), the structured-log and Prometheus wiring, the five background schedulers
> and their advisory locks, the CI gates, release-please and the signed release images, the
> one `Deploy` workflow with its canary and its migration task, and the `Rollback` workflow
> and runbook. Read this if you'll be on call, if you'll deploy, if you'll set up a
> customer's self-hosted instance, or if you'll touch anything under `infrastructure/`,
> `charts/`, `deploy/compose/`, `.github/workflows/` or `backend/app/core/health.py`.
>
> Verified against getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased
> changes, including three breaking ones: #747, #769, #770).

---

## Who this book is for & prerequisites

- Engineers who will operate, deploy, or self-host Experimently; anyone joining the on-call
  rotation.
- Backend engineers adding a scheduler, a metric, a health check, a config setting, or a
  migration — each has an operational contract described here.
- Prerequisites: the **core four**, especially the Build book's Weeks 2 and 4 (configuration,
  schedulers, testing, containers, AWS). Comfort with Docker, a shell, and reading YAML.
  AWS familiarity helps in Part II but each service is introduced as it appears.

## How to use this book

12 lessons in 4 Parts, the usual rhythm: **What & why → How Experimently does it (real
code/config) → Try It Yourself → Key takeaways**, with a Workshop per Part. Configuration
and infrastructure files are quoted with `# 📁 path` like source code — because they are
source code. A tagged block is an excerpt: `...` marks lines or text left out, and everything
else is the file's own. A tag that ends in `(condensed)` marks a summary instead: the names
are real, but lines have been joined or shortened and the trailing comments are the book's.
Most Try-It-Yourself sections are things you can do on a laptop with Docker;
the AWS ones are written so you can follow them with `cdk synth` and reading, without an
account.

> **The one idea to anchor on:** an operations system is a set of **contracts between
> layers** — the container promises the orchestrator a liveness probe, the entrypoint
> promises the image a bootstrapped database, the scheduler promises its replicas a lock,
> the deploy promises the on-call engineer a rollback path, the health endpoint promises the
> load balancer a truthful 503. Each contract is small; the platform works because none of
> them lie. Every Hour here names one and shows where it's kept.

---

## Table of Contents

### Part I — The Local and Self-Hosted Stack _(Hours 1–3)_ → [`part-1-local-stack.md`](./part-1-local-stack.md)

- **Hour 1** — Docker Compose: services, profiles, health, and the `.env` contract; the production compose file and the Helm chart
- **Hour 2** — The images: two multi-stage Dockerfiles (`core` and `full`) and the nginx single-origin trick
- **Hour 3** — The entrypoint: check, wait, bootstrap, seed, hand over — and the Makefile

### Part II — AWS _(Hours 4–6)_ → [`part-2-aws.md`](./part-2-aws.md)

- **Hour 4** — The CDK app: nine stacks (twelve with the modules) and the order they depend on
- **Hour 5** — Serving traffic: two services, one load balancer, blue/green for the API
- **Hour 6** — Data services: Aurora, ElastiCache, DynamoDB, Kinesis → Firehose → S3 → Glue, OpenSearch

### Part III — Observability _(Hours 7–9)_ → [`part-3-observability.md`](./part-3-observability.md)

- **Hour 7** — Logs and request IDs: structlog, JSON in production, `X-Request-ID` end to end
- **Hour 8** — Health and metrics: liveness vs readiness, `/metrics` and its token, what to graph
- **Hour 9** — Schedulers and alerts: the advisory lock, run history, scheduler health, CloudWatch alarms, Slack/email

### Part IV — Release Engineering _(Hours 10–12)_ → [`part-4-release-engineering.md`](./part-4-release-engineering.md)

- **Hour 10** — CI: the PR QA gate, the release gate, security scans, and what "required" means
- **Hour 11** — From merge to production: release-please, signed images, and the Deploy workflow
- **Hour 12** — Rollback, secrets and backups: the decision tree, what rolls back by itself, the Rollback workflow

---

## Where this fits

```
Core path:  Python → FastAPI → React/Next.js → Build a Platform
Specialize:                                    ├─ Experimentation Statistics
                                               ├─ SDK Engineering
                                               └─ ⭐ Platform Operations (this)
```

_Turn to [Part I](./part-1-local-stack.md). 🛠️_

## Related engineering field guides

These task-based guides use a newer, explicitly pinned app snapshot; this book or guide retains
its original reference commit. See the collection index for scope and verification details.

- [Distributed correctness](../engineering-guides/distributed-correctness.md)
- [Database evolution and performance](../engineering-guides/database-evolution.md)
- [Privacy and data lifecycle](../engineering-guides/privacy-data-lifecycle.md)

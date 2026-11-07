# Platform Operations

### _The Controlvar Edition — Running the Platform, From Laptop to Production, in 12 Hours_

> **Preface.** This is a **specialization** book. The core four books teach you how
> Controlvar is built; this one teaches you how it _runs_ — on your laptop, in a Docker
> Compose stack a customer can self-host, and on AWS behind an ALB with blue/green deploys —
> and how you know it's healthy, what you look at when it isn't, and how a change gets from a
> pull request to production without anyone holding their breath. Every piece of it is real:
> the development `docker-compose.yml` with its profiles and seeds, the production compose
> file that runs the published images, the Dockerfiles with their `core` and `full` targets,
> the entrypoint that checks its configuration, waits for Postgres and bootstraps the schema
> (everywhere but AWS, where a one-shot migration task is the only schema writer), the short
> list of proxies whose forwarded headers the API believes, nine CDK
> stacks (twelve with the optional modules) and the beta installer that deploys them into your
> own account with one command, the structured-log and Prometheus wiring, the five
> background schedulers and their advisory locks, the CI gates, release-please and the signed
> release images, the one `Deploy` workflow with its canary and its migration task, and the
> `Rollback` workflow and runbook. Read this if you'll be on call, if you'll deploy, if you'll
> set up a customer's self-hosted instance, or if you'll touch anything under
> `infrastructure/`, `deploy/compose/`, `.github/workflows/` or `backend/app/core/health.py`.
>
> Controlvar was called Experimently before 0.35.0; you will still meet the old name in some
> identifiers (the `EXPERIMENTLY_PROFILE` variable, for one).

---

## Who this book is for & prerequisites

- Engineers who will operate, deploy, or self-host Controlvar; anyone joining the on-call
  rotation.
- Backend engineers adding a scheduler, a metric, a health check, a config setting, or a
  migration — each has an operational contract described here.
- Prerequisites: the **core four**, especially the Build book's Weeks 2 and 4 (configuration,
  schedulers, testing, containers, AWS). Comfort with Docker, a shell, and reading YAML.
  AWS familiarity helps in Part II but each service is introduced as it appears.

## How to use this book

12 lessons in 4 Parts, the usual rhythm: **What & why → How Controlvar does it (real
code/config) → Try It Yourself → Key takeaways**, with a Workshop per Part. Configuration
and infrastructure files are quoted with `# 📁 path` like source code — because they are
source code. A tagged block is an excerpt of the file at the commit named in "A note on accuracy"
below (the book's _pin_): `...` marks lines
or text left out, and everything else is the file's own. A tag that ends in `(condensed)` marks
a summary instead: the names are real, but lines have been joined or shortened and the trailing
comments are the book's. Most Try-It-Yourself sections are things you can do on a laptop with
Docker; the AWS ones are written so you can follow them with `cdk synth` and reading, without
an account.

> **The one idea to anchor on:** an operations system is a set of **contracts between
> layers** — the container promises the orchestrator a liveness probe, the entrypoint
> promises the image a bootstrapped database, the scheduler promises its replicas a lock,
> the deploy promises the on-call engineer a rollback path, the health endpoint promises the
> load balancer a truthful 503. Each contract is small; the platform works because none of
> them lie. Every Hour here names one and shows where it's kept.

---

## Table of Contents

### Part I — The Local and Self-Hosted Stack _(Hours 1–3)_ → [`part-1-local-stack.md`](./part-1-local-stack.md)

- **Hour 1** — Docker Compose: services, profiles, health, and the `.env` contract; the production Compose file, and backing it up
- **Hour 2** — The images: multi-stage Dockerfiles (`core` and `full`) and the nginx single-origin trick
- **Hour 3** — The entrypoint: check, wait, bootstrap, seed, hand over (and whose `X-Forwarded-For` to believe) — and the Makefile

### Part II — AWS _(Hours 4–6)_ → [`part-2-aws.md`](./part-2-aws.md)

- **Hour 4** — The CDK app: nine stacks (twelve with the modules), the order they depend on, and the one-command installer (beta)
- **Hour 5** — Serving traffic: two services, one load balancer, blue/green for the API
- **Hour 6** — Data services: Aurora, ElastiCache, DynamoDB, Kinesis → Firehose → S3 → Glue, OpenSearch

### Part III — Observability _(Hours 7–9)_ → [`part-3-observability.md`](./part-3-observability.md)

- **Hour 7** — Logs and request IDs: structlog, JSON in production, `X-Request-ID` end to end
- **Hour 8** — Health and metrics: liveness vs readiness, `/metrics` and its token, what to graph
- **Hour 9** — Schedulers and alerts: the advisory lock, run history, scheduler health, CloudWatch alarms, Slack/email

### Part IV — Release Engineering _(Hours 10–12)_ → [`part-4-release-engineering.md`](./part-4-release-engineering.md)

- **Hour 10** — CI: the PR QA gate, the release gate, security scans, the workflows that run only when they are needed, and what "required" means
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

## Related reading — Production Engineering

The Production Engineering specialization is verified against its own app snapshot; this book
is verified against Controlvar `09d815ea`. See each book README for scope and verification.

- [Distributed correctness](../production-engineering/part-2-reliable-systems.md#hour-4--assignments-transactions-and-retries)
- [Database evolution and performance](../production-engineering/part-2-reliable-systems.md#hour-6--database-evolution-and-performance)
- [Privacy and data lifecycle](../production-engineering/part-3-security-and-privacy.md#hour-8--data-inventories-identifiers-and-minimization)

## A note on accuracy

Verified against Controlvar `09d815ea` (`main` on 6 November 2026, after v0.43.2). Every `# 📁`
excerpt was re-copied from the file at that commit and checked by the repository's gates
(`tools/README.md`): each cited path must exist at the commit and each identifier in an excerpt
must occur in its file; and, beyond the gates, every line of an excerpt not marked `(condensed)`
was checked to occur in its file at that commit. Where the book names a release (the image tags
in Part I, the image verified in Part IV), it is v0.43.2, the release the pin's
`deploy/compose/compose.yml` names. Nothing here was run against a real AWS account; the AWS
lessons are read from the CDK source, the workflows and the app's own guides, and anything the
book says about behaviour in a deployed account is intent unless stated otherwise. To re-check,
run `CONTROLVAR_APP=<your clone> REF=09d815ea bash tools/check.sh platform-operations`.

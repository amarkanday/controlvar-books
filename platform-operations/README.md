# Platform Operations

### _The Experimently Edition — Running the Platform, From Laptop to Production, in 12 Hours_

> **Preface.** This book explains how to run and maintain Experimently. It covers the local Compose stack,
> self-hosted Compose and Helm deployments, and the AWS infrastructure defined in CDK. The
> remaining chapters cover logging, health checks, schedulers, releases and recovery. Read it
> when setting up an environment, changing infrastructure or preparing to operate the service.
> Deployment examples describe the configuration at the reference commit below.
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

The 12 lessons are grouped into four Parts. Each explains an operational requirement, examines the
relevant configuration and provides an exercise. Workshops include questions, quizzes and worked
solutions. Blocks marked `# 📁 path` quote source or configuration; `...` marks omissions. A
`(condensed)` label identifies a summary whose lines or comments have been edited for explanation.
Local exercises use Docker. The AWS lessons can be followed by reading stack definitions and
synthesized templates without deploying an account.

> Each layer exposes a different operational contract: startup establishes configuration and
> schema readiness, probes guide routing and restarts, scheduler locks coordinate workers, and
> deployment procedures define recovery. Check the relevant contract when diagnosing a failure.

---

## Table of Contents

### Part I — The Local and Self-Hosted Stack _(Hours 1–3)_ → [`part-1-local-stack.md`](./part-1-local-stack.md)

- **Hour 1** — Docker Compose: services, profiles, health, and the `.env` contract; the production compose file and the Helm chart
- **Hour 2** — Multi-stage images and single-origin serving
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

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Distributed correctness](../production-engineering/part-2-reliable-systems.md#hour-4--assignments-transactions-and-retries)
- [Database evolution and performance](../production-engineering/part-2-reliable-systems.md#hour-6--database-evolution-and-performance)
- [Privacy and data lifecycle](../production-engineering/part-3-security-and-privacy.md#hour-8--data-inventories-identifiers-and-minimization)

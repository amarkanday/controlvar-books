# Onboarding Guide

### _Your first day, first week and first month on Experimently_

[← experimently-docs](../README.md) · [Usage guide](./usage.md) · [Troubleshooting](./troubleshooting.md)

This guide is for an engineer who has just been given access to
`amarkanday/experimentation-platform`. It's opinionated about order: the fastest way to become
useful on this codebase is to get it _running_ before you read it, to read it through the
_core loop_ before the feature epics, and to ship one small change through the _real_ PR
process before anything ambitious. Everything here links to the longer books when you want the
why.

---

## 0. What you're joining

Experimently is an A/B-testing and feature-flag platform:

- a **FastAPI** backend (Python 3.11, SQLAlchemy 2, Pydantic v2, PostgreSQL, Redis) — ~66,000
  lines under `backend/app/`, 50+ API router modules, five background schedulers, a statistics
  engine;
- a **Next.js** dashboard (TypeScript, React 18, Tailwind) — ~25,000 lines under
  `frontend/src/`;
- **fifteen SDKs** under `sdk/` that all speak one HTTP contract;
- AWS infrastructure in **CDK (Python)** under `infrastructure/cdk/`;
- two demo apps (**ShopLab**, **StreamPulse**) under `demo/` that drive the platform through
  the public API exactly as a customer would;
- ~112,000 lines of backend tests (5,800+ test functions), 550+ dashboard tests, a live SDK
  contract harness, and a CI gate with six PR jobs.

It is an **open-core** product in the middle of its launch plan: the **Community Edition**
(`docker compose up`, local auth, no AWS) is real as of September 2026; the enterprise pieces
(Cognito/SSO, compliance audit signing, HIPAA, warehouse connectors) live in the same tree
behind settings. `docs/planning/open-core-launch-plan-2026-09.md` is the execution contract;
GitHub issues #71–#77 track the phases (P0 done; P1 next).

The one-sentence model of the product, which every book in this repo repeats: **an
experiment is two tables — `assignments` (who saw what) and `events` (what they did) — joined
on `(experiment_id, user_id)`; the server assigns by a deterministic hash, tracking is
append-only, and every statistic is a query over that join.**

---

## 1. Day one — get it running

### Accounts and tools

- GitHub access to `amarkanday/experimentation-platform` (and this repo).
- **Docker Desktop** (or another Docker with compose v2).
- **Python 3.11** (`python3.11 --version`; 3.9 reached EOL in 2025 and is not supported).
- **Node 22** (`.nvmrc`; `nvm use`).
- The `gh` CLI, authenticated (`gh auth status`) — the PR process below uses it.
- Optional but expected: **Claude Code** — the repo ships a `CLAUDE.md` with the team's
  working agreements and `.claude/commands/` skills (`/test-backend`, `/start-api`,
  `/migration`, `/quality`, `/commit`, `/autotest`, `/fix-imports`).

### The fastest path: containers

```bash
git clone https://github.com/amarkanday/experimentation-platform.git
cd experimentation-platform
docker compose up -d --wait            # postgres + redis + api + dashboard; ~2 min first time
open http://localhost:3000             # admin@demo.com / Demo1234!
```

The first start waits for Postgres, runs the schema bootstrap, and applies the `demo` seed
(an admin, three experiments, two flags). Add the demo apps and their traffic simulators with
the `demo` profile:

```bash
SEED=demo,shoplab,streampulse docker compose --profile demo up -d --wait
open http://localhost:3200             # ShopLab storefront
open http://localhost:3300             # StreamPulse mobile-app simulator
```

> **If you also run the local dev database** (next section) on 5432, start the compose stack
> under a different project name and ports so the two don't collide:
> `docker compose -p experimently up -d --wait` with `POSTGRES_HOST_PORT=5433
> REDIS_HOST_PORT=6381 API_HOST_PORT=8001 FRONTEND_HOST_PORT=3001` in `.env` or the shell.
> The test suite hard-wires `localhost:5432` unless you export `POSTGRES_PORT`.

### The development path: venv + local services

```bash
make venv                              # python3.11 -m venv venv && pip install -r backend/requirements.txt
source venv/bin/activate               # ALWAYS, before any python/pytest command
make db                                # docker compose up -d --wait postgres redis
make bootstrap                         # python -m backend.app.db.bootstrap (schema + first admin)
make dev                               # uvicorn backend.app.main:app --reload --port 8000
# in another shell:
make web                               # cd frontend && npm run dev  → http://localhost:3100
```

Three facts that will otherwise cost you your first afternoon:

1. **Everything runs from the repository root.** The backend is imported as `backend.app.*`;
   `uvicorn backend.app.main:app` is the entry point; alembic is
   `python -m alembic -c backend/app/db/alembic.ini …`. Never `cd backend` to run things.
2. **The schema is chosen at import time** from `APP_ENV`/`TESTING`
   (`experimentation` vs `test_experimentation`). Set environment variables _before_ importing
   the app; the test `conftest.py` does this for you.
3. **A fresh database is created by the bootstrap, not by `alembic upgrade head`** — the
   migration chain can't replay from empty (see the [Build book, Day 13](../build-a-platform-in-28-days/week-2-api-and-data.md#day-13--alembic-migrations-and-the-bootstrap)).

### Prove it works

```bash
curl -s localhost:8000/health/ready | python -m json.tool          # database healthy
open http://localhost:8000/api/v1/docs                              # every endpoint
export APP_ENV=test TESTING=true
python -m pytest backend/tests/unit/core/test_scheduler_lock.py -q  # harness works
```

Then log in on the dashboard, open **Experiments**, open one of the seeded experiments, and
look at its results page. You've now seen both halves of the product.

### Day-one reading (about an hour)

1. `CLAUDE.md` at the repo root — the team's working agreements, in the order they bite.
2. [Build book, Day 1](../build-a-platform-in-28-days/week-1-core-loop.md#day-1--the-big-picture-experimentlys-architecture-and-the-0-conversions-bug)
   — the architecture on one page and the bug that justifies it.
3. The [usage guide](./usage.md) — the product as a product manager sees it.

---

## 2. Week one — learn the core loop by driving it

### Do the trace

Work through [Week 1 of the Build book](../build-a-platform-in-28-days/week-1-core-loop.md)
with the API running. Its _Build It_ sections have you assign a user with `curl`, track an
event, reproduce a p-value in SQL and `scipy`, and finally walk one ShopLab experiment from a
React hook to the results page. When you can narrate `assign → track → results` naming the
file at each hop, you understand the product.

### Learn the harness

Run the suites the way CI does, separately:

```bash
source venv/bin/activate && export APP_ENV=test TESTING=true
python -m pytest backend/tests/unit/ -p no:cov -q -x          # ~3,700 tests
python -m pytest backend/tests/smoke/test_wiring.py -q          # every route resolves
python -m pytest backend/tests/integration/ -p no:cov -q       # needs Postgres on localhost:5432
cd frontend && npm test                                         # 550+ Jest tests
```

Read `backend/tests/conftest.py`'s header comment and the `client` fixture: one database per
pytest process, no truncation between tests, `dependency_overrides` for `get_db` and auth.
Then read one paired test — `backend/tests/unit/services/test_assignment_service_eligibility.py`
and its integration twin `TestAssignEligibility` in
`backend/tests/integration/api/test_tracking_api.py` — because that pairing is what the team
expects from you: fast mocked branch coverage next to one real end-to-end path.

### Read the code in this order

| Order | File                                             | Why first                                            |
| ----- | ------------------------------------------------ | ---------------------------------------------------- |
| 1     | `backend/app/main.py`                            | The app, the middleware onion, the lifespan          |
| 2     | `backend/app/api/api.py`                         | The product's table of contents                      |
| 3     | `backend/app/api/deps.py`                        | `get_db`, the three auth paths, the permission deps  |
| 4     | `backend/app/models/{experiment,assignment,event}.py` | The five tables                                 |
| 5     | `backend/app/services/assignment_service.py`     | Sticky → gates → hash                                |
| 6     | `backend/app/services/event_matching.py`         | The one rule every analysis path must use            |
| 7     | `backend/app/services/analysis_service.py` (`calculate_metric_results`) | Two counts to a verdict       |
| 8     | `backend/app/api/v1/endpoints/tracking.py`       | The SDK-facing endpoints                             |
| 9     | `backend/app/core/config.py`                     | Settings, environments, fail-closed validators       |
| 10    | `frontend/src/services/api.ts` + `contexts/AuthContext.tsx` | The dashboard's spine                     |

### Pick your book

By the end of week one, choose the path that matches what you'll work on:

- New to Python? [Learn Python in 24 Hours](../learn-python-in-24-hours/README.md).
- Know Python, new to FastAPI/SQLAlchemy/Pydantic? [Learn FastAPI in 24 Hours](../learn-fastapi-in-24-hours/README.md).
- Working on the dashboard? [Learn TypeScript, React & Next.js in 24 Hours](../learn-react-nextjs-in-24-hours/README.md).
- Everyone: [Build a Platform in 28 Days](../build-a-platform-in-28-days/README.md), then a
  specialization ([Statistics](../experimentation-statistics/README.md),
  [SDKs](../sdk-engineering/README.md), [Operations](../platform-operations/README.md)).

---

## 3. Your first change — the PR process, exactly

The team runs an auditable process, and it is not optional. Every piece of work has a GitHub
issue, the PR closes it, CI is green, a code review runs, and findings are addressed or
explicitly dismissed before merge.

### The loop

```bash
# 1. an issue for the work (or pick an existing one); note its number
gh issue create --title "Add decision_notes to experiments" --body "..." --label "launch-plan:P1"

# 2. a branch named for the work
git checkout -b feat/experiment-decision-notes

# 3. the change, with tests (unit twin + integration/API twin for anything on a hot path)

# 4. the local gate — what CI will run
make test                                                   # backend unit+integration+smoke, frontend test+build
make lint                                                   # black --check, isort, mypy, flake8
python standardize_metrics_imports.py --check               # the import-path guard

# 5. commit in the house format (or use the /commit skill)
git add <files>
git commit -m "Add decision_notes to experiments

- Nullable Text column + additive migration
- ExperimentUpdate/ExperimentResponse fields; service dict builder
- Dashboard textarea on the detail page with a Jest test

Closes #123"

# 6. push and open the PR with `Closes #N` in the body
git push -u origin feat/experiment-decision-notes
gh pr create --fill --body "Closes #123 ..."

# 7. wait for the PR QA Gate, Release Gate and Security Scan; then run the review skill
#    (in Claude Code: /code-review <PR#>) and address or dismiss every finding in a PR comment

# 8. merge; the issue closes
gh pr merge --squash
```

### What CI requires

Branch protection on `main` requires: **Release Gate Summary**, **Security Scan Summary**,
**Unit Tests**, **Smoke Tests**, **Frontend Tests**, **SDK Contract Tests**,
**integration-tests**. The **SDK Live Contract** and **Docker Smoke** jobs run on every PR too
(promoting them to required is a P1 item). [Build book, Day 24](../build-a-platform-in-28-days/week-4-production.md#day-24--cicd-the-pr-qa-gate-the-release-gate-and-the-security-scan)
explains what each catches.

### House rules you'll be reviewed against

- **Imports are fully qualified**: `from backend.app.models.metrics.metric import RawMetric`.
  Never `from app...`, never relative. (SQLAlchemy registers classes by import path; two
  spellings → two classes → "Class is not mapped".)
- **Pydantic v2 only**: `field_validator`, `model_validator`, `ConfigDict`, `pydantic_settings`.
- **Conversions are matched with `event_matching.py`**, never `event_type == "conversion"`.
- **Targeting rules go through `targeting_adapter.py`**; there are three stored shapes and
  you must not add a fourth.
- **Feature-flag and report dependencies are `async`; experiment dependencies are sync.**
  Check READ before UPDATE/DELETE.
- **Endpoints use `Depends(deps.get_db)`**, never `SessionLocal()` directly (the test harness
  overrides the dependency).
- **Migrations**: autogenerate, _read_ the file, `down_revision` is a revision _id_, prefer
  nullable/additive changes, never rely on the chain to build a fresh database.
- **Tests**: `hashed_password` on every test user; unit tests delete what they commit (the
  session fixture doesn't truncate); DB-backed auth tests live in `integration/api/`, not
  `integration/auth/` (that package runs under Cognito + moto with no Postgres in CI).
- **Semgrep** blocks `sqlalchemy.text(...)`; when an identifier genuinely can't be a bind
  parameter, add
  `# nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text`
  with a one-line justification.
- **Config lives in `pyproject.toml`** (pytest, coverage, packaging). Coverage is not in
  `addopts`; ask for `--cov=backend.app --cov-report=term`.
- **Commit messages** end with the attribution lines your tooling provides; PR bodies say
  `Closes #N` and end with the same attribution.

### A good first issue

Something that touches the whole stack but nobody's waiting on — the
[Week 2 capstone](../build-a-platform-in-28-days/week-2-api-and-data.md#day-14--capstone-add-a-field-end-to-end)
(a `decision_notes` column) is designed as exactly this. Alternatives: a missing operator in
the dashboard's targeting list, a `regression` marker on a test that guards a past bug, or
one of the P1 items in issue #73.

---

## 4. Your first month — where things are and who owns what

### Map of the repository

```
backend/app/
  main.py · api/{api.py,deps.py} · api/v1/endpoints/*.py
  core/      config · security · permissions · rules_engine · rule_compiler · evaluation_cache
             targeting_adapter · scheduler* · scheduler_lock · scheduler_tick · stats_engine
             health · logger · metrics
  services/  assignment · event · event_matching · analysis · srm · sequential_testing · cuped
             bayesian · bandit · feature_flag · rollout · safety · rules_evaluation · notification …
  models/    experiment · assignment · event · feature_flag · user · api_key · safety · bandit_state …
  schemas/   one Pydantic module per resource
  middleware/ rate_limiter · security_middleware · request_id · prometheus_metrics · logging
  db/        session · bootstrap · migrations/ (alembic)
backend/lambda/     assignment · event_processor · feature_flag_evaluation · shared (+ tests)
backend/scripts/    seed_demo_data · seed_shoplab · seed_streampulse · seed_sdk_contract · dump_openapi
backend/tests/      unit · integration · smoke · contract · performance · realistic · e2e
frontend/src/       pages · components · contexts · hooks · services · types · utils · tests
sdk/                js · python · react · go · java · ios · android · ruby · php · dotnet · elixir
                    flutter · react-native · edge · openfeature · openfeature-python
tests/sdk-contract/ golden-vectors.json · test_*_sdk · live/run_live_contract.py
demo/               shoplab · streampulse · setup-local.sh · setup-aws.sh · DEMO_GUIDE.md
infrastructure/     cdk/ (app.py + 14 stacks) · cloudwatch/ · constructs/ · tests/
docs/               architecture · api · sdk · statistics · testing · self-hosting · planning …
.github/workflows/  pr-qa-gate · release-gate · security-scan · integration-tests · nightly-qa
                    deploy-dev · deploy-prod · db-migrate · rollback · performance · infrastructure
```

### The feature epics

Most of the fifty router modules are epics with an ID in a comment. The ones you'll hear
about: EP-001 rules engine · EP-013 monitoring · EP-016 results engine · EP-019 Fargate deploy
· EP-021 sequential testing · EP-022 mutual exclusion & holdout · EP-030 Slack/email alerting ·
EP-033 compliance audit · EP-036 split-URL · EP-037 SSO · EP-050 HIPAA · EP-057 workspaces ·
EP-058 WebSocket results · Issue #21 CUPED · #22 bandits · #23 AI design + MCP · #25
interaction detection · #26 warehouse analytics · #28 dimensional analysis. `api/api.py` lists
them in mount order.

### The demos are your integration environment

`./demo/setup-local.sh` seeds and starts everything with simulators; StreamPulse's
`rollout_story.py --auto` plays a crash-spike → auto-rollback story in real time (set
`SAFETY_CHECK_INTERVAL_MINUTES=1 ROLLOUT_CHECK_INTERVAL_MINUTES=1`). When you change anything
on the assign/track/evaluate/results path, watch the demos — they are the platform's most
honest test.

### Working with Claude Code here

`CLAUDE.md` is loaded into every session and encodes the rules above. The skills:

| Skill            | Does                                                                          |
| ---------------- | ----------------------------------------------------------------------------- |
| `/test-backend`  | Activates the venv, sets `APP_ENV=test TESTING=true`, runs pytest             |
| `/start-api`     | Runs uvicorn from the root                                                    |
| `/migration`     | Autogenerates a migration, has you review it, applies it                      |
| `/quality`       | black, isort, mypy, flake8                                                    |
| `/fix-imports`   | Runs the metrics import-path standardiser                                     |
| `/commit`        | A commit in the house format                                                  |
| `/autotest`      | The multi-agent QA pipeline (data generator, scenario runner, validator…)     |
| `/code-review`   | The review every PR gets before merge                                         |

Keep at most three agents in parallel (API rate limits); the review skill counts as one.

### Who to ask

The repository owner reviews every PR. For history — why a decision was made — the
`docs/planning/` folder, the PR descriptions (they're written as design notes; read PR #65's
for the ShopLab bug list), and this repo's books are the record. If a path in a book 404s,
`git grep` the symbol; the patterns outlast the line numbers.

---

## 5. Checklist

**Day one**

- [ ] `docker compose up -d --wait` and log in at `:3000`
- [ ] venv, `make db`, `make bootstrap`, `make dev`, `make web`; `/health/ready` is green
- [ ] one unit test file passes locally
- [ ] read `CLAUDE.md` and Build book Day 1

**Week one**

- [ ] Build book Week 1, all _Build It_ exercises
- [ ] all four suites run locally (unit, smoke, integration, frontend)
- [ ] read the ten files in order above
- [ ] chosen a book

**First PR**

- [ ] issue created; branch named; tests paired (unit + API); `make test` + `make lint` green
- [ ] PR body `Closes #N`; CI green; review run; findings addressed; merged

**First month**

- [ ] Build book Weeks 2–4
- [ ] ran StreamPulse's rollout story end-to-end and can explain each step
- [ ] one specialization book started

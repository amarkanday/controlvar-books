# Onboarding Guide

### _Your first day, first week and first month on Experimently_

[← experimently-docs](../README.md) · [Usage guide](./usage.md) · [Troubleshooting](./troubleshooting.md)

> Verified against `getexperimently/experimently` at `a4a6dbd2` (0.17.0 plus unreleased changes,
> including three breaking ones: #747, #769, #770).

This guide is for an engineer who is starting work on
[`getexperimently/experimently`](https://github.com/getexperimently/experimently), the public,
Apache-2.0 repository (the SDKs under `sdk/` are MIT). It's opinionated about order: the fastest
way to become useful on this codebase is to get it _running_ before you read it, to read it
through the _core loop_ before the feature epics, and to ship one small change through the
_real_ PR process before anything ambitious. Everything here links to the longer books when you
want the why.

The published product documentation lives at
[getexperimently.github.io/experimently](https://getexperimently.github.io/experimently/); it is
deployed from release tags. The dashboard's `/docs` page links, by default, to the Markdown in the
GitHub repository, or, with `NEXT_PUBLIC_DOCS_URL` set at build time, to a built site such as the
published one.

---

## 0. What you're joining

Experimently is an A/B-testing and feature-flag platform:

- a **FastAPI** backend (Python 3.11, SQLAlchemy 2, Pydantic v2, PostgreSQL, Redis) under
  `backend/app/`: 36 core endpoint modules in `backend/app/api/v1/endpoints/` (plus the
  modules' own routers), five background schedulers, a statistics engine;
- a **Next.js 16** dashboard (TypeScript, **React 19**, Tailwind) under `frontend/src/`;
- **16 SDKs** under `sdk/` that all speak one HTTP contract;
- AWS infrastructure in **CDK (Python)** under `infrastructure/cdk/`, a Helm chart under
  `charts/experimently`, and a production compose file under `deploy/compose`;
- two demo apps (**ShopLab**, **StreamPulse**) under `demo/` that drive the platform through
  the public API exactly as a customer would;
- a large test suite (the app README counts "5,400+ backend tests, 640+ dashboard tests", and
  13 of the 16 SDKs run against a live backend in CI), and **20 required checks** on every
  pull request (listed in [section 3](#what-ci-requires)).

**One codebase, two profiles.** All of it is Apache-2.0; there are no editions and no licence
key. The **core** profile is `backend/` (and `frontend/`) alone. The **full** profile adds the
optional modules under `modules/`: workspaces, rbac (custom roles), sso, hipaa, compliance
(audit signing and report packs), warehouse, integrations, counters, etl and split_url.
`modules-manifest.txt` is the authority on what belongs to a module. The profile is decided by
whether `import modules` succeeds; the images and compose select it with
`EXPERIMENTLY_PROFILE=core|full` (Dockerfile targets `core` and `full`), and
`GET /api/v1/modules` reports what a running deployment serves. Nothing under `backend/` may
import `modules`.

The one-sentence model of the product, which every book in this repo repeats: **an
experiment is two tables — `assignments` (who saw what) and `events` (what they did) — joined
on `(experiment_id, user_id)`; the server assigns by a deterministic hash, tracking is
append-only, and every statistic is a query over that join.**

---

## 1. Day one — get it running

### Accounts and tools

- A GitHub account (the repository is public; you need write access only to push branches
  there instead of a fork).
- **Docker Desktop** (or another Docker with compose v2).
- **Python 3.11** (`python3.11 --version`; 3.9 reached EOL in 2025 and is not supported).
- **Node 22** (`.nvmrc`; `nvm use`).
- The `gh` CLI, authenticated (`gh auth status`) — the PR process below uses it.
- Optional but expected: **Claude Code** — the repo ships a `CLAUDE.md` with the team's
  working agreements, `.claude/commands/` skills (`/test-backend`, `/start-api`,
  `/migration`, `/quality`, `/commit`, `/autotest`, `/fix-imports`) and the review roles in
  `.claude/agents/`.

### The fastest path: containers

```bash
git clone https://github.com/getexperimently/experimently.git
cd experimently
docker compose up -d --wait            # postgres + redis + api + dashboard; ~2 min first time
open http://localhost:3000             # admin@demo.com / Demo1234!
```

The first start waits for Postgres, runs the schema bootstrap, and applies the `demo` seed
(an admin, three experiments, two flags). Add the demo apps and their traffic simulators with
the `demo` profile (or `make demo`):

```bash
SEED=demo,shoplab,streampulse docker compose --profile demo up -d --wait
open http://localhost:3200             # ShopLab storefront
open http://localhost:3300             # StreamPulse mobile-app simulator
```

`make up` runs the core stack the same way and `make down` stops it and drops its volumes.
For the full profile: `EXPERIMENTLY_PROFILE=full docker compose up -d --build`.

> **If you also run the local dev database** (next section) on 5432, start the compose stack
> under a different project name and ports so the two don't collide:
> `docker compose -p experimently up -d --wait` with `POSTGRES_HOST_PORT=5433
> REDIS_HOST_PORT=6381 API_HOST_PORT=8001 FRONTEND_HOST_PORT=3001` in `.env` or the shell.
> The test suite connects to `localhost:5432` unless you export `POSTGRES_PORT`.

### The development path: venv + local services

```bash
make venv                              # venv + backend/requirements.txt (+ the modules' requirements in a full checkout)
source venv/bin/activate               # ALWAYS, before any python/pytest command
make db                                # docker compose up -d --wait postgres redis
make bootstrap                         # python -m backend.app.db.bootstrap (schema + first admin)
make dev                               # ENVIRONMENT=development uvicorn backend.app.main:app --reload --port 8000
# in another shell:
make web                               # cd frontend && npm run dev  → http://localhost:3100
```

Use `make dev` rather than the `/start-api` skill: the skill file still says
`cd backend && uvicorn app.main:app`, which contradicts the rule below.

Four facts that will otherwise cost you your first afternoon:

1. **Everything runs from the repository root.** The backend is imported as `backend.app.*`;
   `uvicorn backend.app.main:app` is the entry point; alembic is
   `python -m alembic -c backend/app/db/alembic.ini …`. Never `cd backend` to run things.
2. **The API does not start without `ENVIRONMENT`.** `make dev` sets it; a hand-run uvicorn
   needs `ENVIRONMENT=development`.
3. **The schema is chosen at import time** from `APP_ENV`/`TESTING`
   (`experimentation` vs `test_experimentation`). Set environment variables _before_ importing
   the app; the test `conftest.py` does this for you.
4. **A fresh database is created by the bootstrap, not by `alembic upgrade heads`** — the
   migration chain can't replay from empty (see the [Build book, Day 13](../build-a-platform-in-28-days/week-2-api-and-data.md#day-13--alembic-migrations-and-the-bootstrap)).
   On an existing database the bootstrap runs `alembic upgrade heads`. `heads` is plural: a
   full checkout has **two** alembic heads (the core chain and the `modules` branch). That is
   the normal state; never `alembic merge` them (a merge file breaks every core checkout).

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
2. `CONTRIBUTING.md` — DCO sign-off, the Make targets, the test shell, the core/modules
   boundary.
3. [Build book, Day 1](../build-a-platform-in-28-days/week-1-core-loop.md#day-1--the-big-picture-experimentlys-architecture-and-the-0-conversions-bug)
   — the architecture on one page and the bug that justifies it.
4. The [usage guide](./usage.md) — the product as a product manager sees it.

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
source venv/bin/activate && export APP_ENV=test TESTING=true POSTGRES_SCHEMA=test_experimentation
python -m pytest backend/tests/unit/ -p no:cov -q -x          # needs Postgres too (see below)
python -m pytest backend/tests/smoke/test_wiring.py -q          # every route resolves
python -m pytest backend/tests/integration/ -p no:cov -q       # needs Postgres on localhost:5432
cd frontend && npm test                                         # the dashboard's Jest suite
```

Or through Make: `make test-unit`, `make test-integration`, `make test-modules` (full checkout),
`make test-frontend`, `make test-sdk` (each SDK's hash against the golden vectors).

The unit suite is **not** database-free: many of its files take the `db_session` fixture, so
start Postgres (`make db`) before running it. `POSTGRES_SCHEMA` must match the models (the
models bake the schema name into index and foreign-key names at import time).

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

`main` is protected: every change arrives by pull request, is squash-merged, and must pass 20
required checks. Commits must be signed off (DCO), and the pull request title becomes the
release note. The process is written down in `CLAUDE.md`; this section is the short version.

### Plan first, unless it's small

For anything beyond a single-file change, the plan comes before the code:

1. **Draft** the plan with the three drafting roles in `.claude/agents/`, in parallel:
   `software-architect` (design and spec), `qa-engineer` (verification: for each property, the
   gate and the planted defect that proves it fires) and `ux-designer` (what a dashboard user,
   SDK integrator or reader sees).
2. **`principal-engineer`** pressure-tests the merged plan, checking every factual premise by
   running something.
3. **`engineering-manager`** gives the final verdict (scope, size, sequence, what is
   irreversible). APPROVED, or APPROVED WITH CONDITIONS once the conditions are met, is the
   gate. Only then write code.

**The fast lane** (a founder decision, D29): a small, low-risk change — a docs fix, a one-line
bug fix with its regression test, a CI or test tweak — goes straight from builder to a reviewer,
who also checks its premises by running them. Anything touching auth, credentials,
migrations, deploy/infrastructure or a public API contract keeps the full process. Related
issues are batched into one plan and one PR.

A question that blocks the work and that nobody is available to answer goes to
`engineering-manager` and `principal-engineer`; their recommendation is followed and recorded
as a team decision. That never extends to anything irreversible (publishing, billing
infrastructure, a force push), which waits for a human.

### The loop

```bash
# 1. an issue for the work (or pick an existing one); note its number
gh issue create --title "Add decision_notes to experiments" --body "..."

# 2. a branch named for the work
git checkout -b feat/experiment-decision-notes

# 3. the change, with tests (unit twin + integration/API twin for anything on a hot path;
#    a bug fix carries a @pytest.mark.regression test that fails on the old code)

# 4. the local gate
make test      # test-backend (unit, integration, smoke) + test-modules + test-frontend
               # (npm test, tsc --noEmit, npm run build)
make lint      # ruff check + ruff format --check, lint-imports (core/modules boundary),
               # reuse lint, the requirements-lock check, eslint + tsc, hadolint, actionlint
grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'   # any hit is a defect

# 5. commit, signed off (DCO) -- every commit, or the DCO check fails
git add <files>
git commit -s -m "feat(experiments): record a decision note on an experiment

- Nullable Text column + additive migration
- ExperimentUpdate/ExperimentResponse fields; service dict builder
- Dashboard textarea on the detail page with a Jest test

Closes #123"

# 6. push and open the PR with a conventional title and `Closes #N` in the body
git push -u origin feat/experiment-decision-notes
gh pr create --title "feat(experiments): record a decision note on an experiment" --body "Closes #123 ..."

# 7. wait for all 20 required checks; run a review (in Claude Code: /code-review <PR#>)
#    and address or dismiss every finding in a PR comment

# 8. squash-merge; the title becomes the squash subject and the issue closes
gh pr merge --squash
```

`git commit -s` appends `Signed-off-by: Your Name <you@example.com>`, which must match the
commit's author or committer. To sign off a branch you already wrote: `git rebase --signoff
main`. Commits from Dependabot and github-actions are exempt.

**The PR title is the release note.** `main` is squash-merged, and release-please reads the
squash subject to write the changelog and choose the version. A title must be
`type(scope): description`, with `type` one of `feat fix perf deps docs build ci refactor test
chore revert style`; add `!` (and a `BREAKING CHANGE:` footer) for a breaking change. The
`conventional-title` job checks it, but it is **not** a required check: a prose title can
merge, and the change then silently drops out of the changelog. `gh pr merge --subject`
overrides the title and must follow the same rule. How a release is cut is in the releasing
guide (`guides/releasing.md`).

### What CI requires

Branch protection on `main` requires these **20** checks (read with
`gh api repos/getexperimently/experimently/branches/main/protection/required_status_checks`,
2026-10-03): `Release Gate Summary`, `Security Scan Summary`, `Unit Tests`, `Smoke Tests`,
`Frontend Tests`, `SDK Contract Tests`, `integration-tests`, `SDK Unit Tests`, `Browser E2E`,
`Docker Smoke`, `lint`, `regression-guard`, `SDK Live Contract / sdk-live-contract (core)`,
`core-build`, `full-build`, `Module Tests`, `Base Requirements Only`,
`CDK Stack Tests (Python)`, `DCO`, `Leak Guard`.

"Require branches to be up to date" (`strict`) is off. A pull request that changes only
documentation (`docs/` other than `docs/api/`, plus `mkdocs.yml`) takes a fast path: the
required checks still report under their real names, in seconds. mypy is not one of the
checks. [Build book, Day 24](../build-a-platform-in-28-days/week-4-production.md#day-24--cicd-the-pr-qa-gate-the-release-gate-and-the-security-scan)
explains what the gate jobs catch.

### House rules you'll be reviewed against

- **Imports are rooted at `backend.app`**: `from backend.app.models.metrics.metric import
  RawMetric`. Never root an import at `app` (`from app.models...` is a different module
  object, so SQLAlchemy sees two classes → "Class is not mapped"). A relative
  `from .base import Base` inside `backend/app/models/` is fine: it resolves to the same
  module. The check is the `grep` in step 4 (the `/fix-imports` skill runs it).
- **The core/modules boundary**: nothing under `backend/` imports `modules`. `lint-imports`
  (in `make lint`) enforces it, and `make core-build` proves the core profile stands alone by
  copying the tree, deleting `modules/`, rebuilding and testing; `make full-build` does the
  same with the modules.
- **Pydantic v2 only**: `field_validator`, `model_validator`, `ConfigDict`, `pydantic_settings`.
- **Conversions are matched with `event_matching.py`**, never `event_type == "conversion"`.
- **Targeting rules go through `targeting_adapter.py`**; do not add a fourth rule shape. Flag
  targeting is validated when it is saved: the legacy list shape, unknown keys at any level,
  nesting deeper than 10 or more than 1,000 nodes, unknown operators and RE2-refused regexes
  all answer 422.
- **Feature-flag and report dependencies are `async`; experiment dependencies are sync.**
  Check READ before UPDATE/DELETE. Access is by role, not ownership (see the
  [usage guide](./usage.md#1-signing-in-and-roles)); a flag change goes through
  `can_act_on_feature_flag`.
- **Endpoints use `Depends(deps.get_db)`**, never `SessionLocal()` directly (the test harness
  overrides the dependency).
- **Migrations**: export `POSTGRES_SCHEMA` matching the models, then autogenerate naming the
  head you extend — `--head <core head id>` (from `alembic heads`) or `--head modules@head`;
  _read_ the file; `down_revision` is a revision _id_. **Expand, then contract**: a migration
  must not drop or rename anything the previous release's code uses. Never rely on the chain to
  build a fresh database, and never `alembic merge` the two heads.
  `backend/tests/integration/database/test_autogenerate_is_empty.py` is the standing check.
- **Tests**: `hashed_password` on every test user; unit tests delete what they commit (the
  session fixture doesn't truncate); DB-backed auth tests live in `integration/api/`, not
  `integration/auth/` (that package runs under Cognito + moto).
- **Bug fixes carry a regression test.** The required `regression-guard` check fails a pull
  request labelled `bug` that changes no test file; mark the test `@pytest.mark.regression`
  and name it in the PR template.
- **The OpenAPI snapshots** (`docs/api/openapi-v1.stable.json` and
  `docs/api/openapi-v1.full.json`) are committed; regenerate them with `make openapi` when
  you change the API.
- **Runnable docs**: shell blocks in the app's `docs/` run in CI;
  `python scripts/doc_examples.py --check` runs them locally.
- **Dependency pins**: after changing a runtime pin, `make lock` regenerates the hashed locks
  the images install.
- **Semgrep** blocks `sqlalchemy.text(...)`; when an identifier genuinely can't be a bind
  parameter, add
  `# nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text`
  with a one-line justification.
- **Config lives in `pyproject.toml`** (pytest, coverage, ruff, import-linter). Coverage is
  not in `addopts`; ask for `--cov=backend.app --cov-report=term`.
- **A push to this repository is publication.** The required `Leak Guard` check
  (`scripts/leak_guard.py`) stands between a secret and the world.
- **Commit messages** are signed off and end with the attribution lines your tooling provides;
  PR bodies say `Closes #N`.

### A good first issue

Something that touches the whole stack but nobody's waiting on — the
[Week 2 capstone](../build-a-platform-in-28-days/week-2-api-and-data.md#day-14--capstone-add-a-field-end-to-end)
(a `decision_notes` column) is designed as exactly this. Alternatives: a missing operator in
the dashboard's targeting list, or a `regression` marker on a test that guards a past bug.

---

## 4. Your first month — where things are and who owns what

### Map of the repository

```
backend/app/
  main.py · api/{api.py,deps.py} · api/v1/endpoints/*.py (36 core modules)
  core/      config · security · permissions · rules_engine · rule_compiler · evaluation_cache
             targeting_adapter · consistent_hash · scheduler* · scheduler_lock · scheduler_tick
             stats_engine · health · logger · metrics
  services/  assignment · event · event_matching · analysis · srm · sequential_testing · cuped
             bayesian · bandit · feature_flag · rollout · safety · rules_evaluation · notification …
  models/    experiment · assignment · event · feature_flag · user · api_key · safety · bandit_state …
  schemas/   one Pydantic module per resource
  middleware/ rate_limiter · security_middleware · request_id · prometheus_metrics · logging
  db/        session · bootstrap · migrations/ (alembic, the core chain)
backend/lambda/     event_processor · feature_flag_evaluation · shared (+ tests); no stack deploys them
backend/scripts/    seed_demo_data · seed_shoplab · seed_streampulse · seed_sdk_contract · seed_guard
                    seed_markers · dump_openapi · check_targeting_rules · check_regex_rules
                    generate_ruleset_vectors · simulate_live_events
backend/tests/      unit · integration · smoke · contract · performance · realistic · e2e
modules/            the optional modules, same layout one level down: backend/app, backend/tests,
                    frontend/src, infrastructure/cdk (modules-manifest.txt lists every file)
frontend/src/       pages · components · contexts · hooks · services · types · utils · tests
sdk/                js · python · react · go · java · ios · android · ruby · php · dotnet · elixir
                    flutter · react-native · edge · openfeature · openfeature-python
tests/sdk-contract/ golden-vectors.json · ruleset-vectors.json · hash_contract.py
                    test_hash_contract.py · harness/ · live/   (make test-sdk)
demo/               shoplab · streampulse · setup-local.sh · teardown-local.sh · DEMO_GUIDE.md
infrastructure/     cdk/ (app.py + stacks/) · cloudwatch/ · constructs/ · tests/
charts/             experimently (the Helm chart)
deploy/             compose (the production compose file)
scripts/            core_build.sh · leak_guard.py · doc_examples.py · check_requirements_lock.py …
docs/               getting-started · self-hosting · api · sdk · feature-flags · auth · rbac
                    workspaces · hipaa · llm-evaluation · statistics · architecture · deployment …
.github/workflows/  pr-qa-gate · release-gate · security-scan · integration-tests · lint · dco
                    leak-guard · regression-guard · conventional-title · codeql · nightly-qa
                    deploy · db-migrate · rollback · release-please · release · sdk-release
                    sdk-unit-tests · docs · doc-examples · chart · chart-kind · compose-production
                    infrastructure-tests · performance-tests · cognito-integration-tests
                    third-party-licenses · dependabot-automerge · dependabot-licences · dependabot-lock
```

One `deploy.yml` serves staging and production (approval-gated), with `db-migrate.yml` and
`rollback.yml` beside it; the deploying guide (`guides/deploying.md`) covers them.

### The feature epics

Most endpoint modules are epics with an ID in a comment. The ones you'll hear about, core:
EP-001 rules engine · EP-013 monitoring · EP-016 results engine · EP-019 Fargate deploy ·
EP-021 sequential testing · EP-022 mutual exclusion & holdout · EP-030 Slack/email alerting ·
EP-058 WebSocket results · CUPED · bandits · AI design + MCP · interaction detection ·
dimensional analysis. **Modules** (full profile only): EP-057 workspaces · EP-037 SSO ·
EP-050 HIPAA · EP-033 compliance audit signing · EP-036 split-URL · warehouse analytics ·
custom roles (rbac) · integrations · counters · etl. (Some code comments cite "Issue #N" for
these; those are the old tracker's numbers and do not match this repository's issues.) `api/api.py`
lists the core routers in mount order; the modules register theirs from
`modules/backend/app/register.py`.

### The demos are your integration environment

`./demo/setup-local.sh` seeds and starts everything with simulators, and sets the rollout,
safety and bandit intervals to one minute itself; StreamPulse's `rollout_story.py --auto` plays
a crash-spike → auto-rollback story in real time. When you change anything on the
assign/track/evaluate/results path, watch the demos — they are the platform's most honest
test.

### Working with Claude Code here

`CLAUDE.md` is loaded into every session and encodes the rules above. The skills:

| Skill            | Does                                                                          |
| ---------------- | ----------------------------------------------------------------------------- |
| `/test-backend`  | Activates the venv, sets `APP_ENV=test TESTING=true`, runs pytest             |
| `/start-api`     | Starts uvicorn (its file still says `cd backend`; prefer `make dev`)          |
| `/migration`     | Autogenerates a migration, has you review it, applies it                      |
| `/quality`       | `ruff format`, `ruff check` and mypy (mypy is not run by any CI check)        |
| `/fix-imports`   | Runs the one `grep` for imports rooted at `app` (there is no script)          |
| `/commit`        | A commit in the house format                                                  |
| `/autotest`      | The multi-agent QA pipeline (data generator, scenario runner, validator…)     |
| `/code-review`   | Built into Claude Code: the review a PR gets before merge                     |

The planning roles (`software-architect`, `qa-engineer`, `ux-designer`, `principal-engineer`,
`engineering-manager`) and the QA agents (`reviewer`, `tester`, `auto-fixer`, `validator` and
others) are defined in `.claude/agents/`.

Dependabot patch and minor updates (and GitHub Actions majors) merge themselves once every
required check is green; other majors are ignored. If its queue grows, ask what is supposed to
drain it.

### Who to ask

The maintainer merges pull requests. For history — why something is the way it is — read
`CHANGELOG.md`, the pull request descriptions in `getexperimently/experimently` (they're
written as design notes), and this repo's books. If a path in a book 404s, `git grep` the
symbol; the patterns outlast the line numbers.

---

## 5. Checklist

**Day one**

- [ ] `docker compose up -d --wait` and log in at `:3000`
- [ ] venv, `make db`, `make bootstrap`, `make dev`, `make web`; `/health/ready` is green
- [ ] one unit test file passes locally
- [ ] read `CLAUDE.md`, `CONTRIBUTING.md` and Build book Day 1

**Week one**

- [ ] Build book Week 1, all _Build It_ exercises
- [ ] all four suites run locally (unit, smoke, integration, frontend)
- [ ] read the ten files in order above
- [ ] chosen a book

**First PR**

- [ ] issue created; branch named; plan reviewed (or the change qualifies for the fast lane)
- [ ] tests paired (unit + API); a bug fix has a `@pytest.mark.regression` test
- [ ] `make test` + `make lint` green; the import `grep` prints nothing
- [ ] every commit signed off (`git commit -s`)
- [ ] conventional PR title; body says `Closes #N`
- [ ] all 20 required checks green; review run; findings addressed; squash-merged

**First month**

- [ ] Build book Weeks 2–4
- [ ] ran StreamPulse's rollout story end-to-end and can explain each step
- [ ] ran `make core-build` once and know what it proves
- [ ] one specialization book started

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Production Engineering — contents and task routes](../production-engineering/README.md)
- [Changing Experimently safely](../production-engineering/part-4-safe-change.md#hour-11--changing-experimently-safely)

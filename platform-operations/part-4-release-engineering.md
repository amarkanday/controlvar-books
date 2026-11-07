# Part IV — Release Engineering

### Hours 10–12

[← Part III](./part-3-observability.md) · [Contents](./README.md)

How a change becomes a release, how a release becomes what an environment serves, and how
an environment goes back to the previous release when it has to. Hour 10 is CI: what runs
on every pull request, what branch protection requires, and why there are two gates rather
than one. Hour 11 is the release and the deploy: release-please cuts a tag and builds signed
images, and the one `Deploy` workflow takes that tag to staging or prod through a snapshot, a
one-shot migration task and a CodeDeploy canary. Hour 12 is the other direction: the
rollback decision tree, what CodeDeploy rolls back by itself, the `Rollback` workflow, the
database rollback order, the secrets a deployment needs, and the backups behind all of it.
Application rollback and database recovery have different requirements, so pay particular
attention to the order of operations when a release changes the schema.

> Verified against Controlvar `09d815ea` (`main` after v0.43.2). The workflows are read from
> `.github/workflows/` at that commit; nothing in this Part was run against a real AWS account, and where the app's own
> runbooks say a path has not been observed in one, so does this book.

---

## Hour 10 — CI: the PR QA gate, the release gate, security scans, and what "required" means

### What & why

Continuous integration is where "it works on my machine" is replaced with a list of things
that must be true before a change can merge. The platform runs several workflows on a pull
request, and the important distinction is between **required** checks — branch protection
blocks the merge until they report success — and **advisory** ones, which run and report but
don't block. The repository declares **20** required checks (listed below); integration,
performance and Cognito suites and the nightly run add slower coverage where a slow or flaky
job costs nobody a merge.

Two gates exist because they answer different questions. The **PR QA Gate** asks "does the
code work?" — tests in both profiles, contracts, containers, a real browser. The **Release
Gate** asks "is this commit releasable?" — a deterministic backend run with pinned test
dependencies from `docker-compose.test.yml`, the frontend build, Bandit and gitleaks — and
produces the one check-run name (`Release Gate Summary`) that the `Deploy` workflow looks up
before it will deploy a tag (Hour 11). It runs on pull requests **and on every push to
`main`**, so the commit a release tag points at always carries a summary.

### How Controlvar does it

The PR gate's shape — a classifier first, then one job per reason to exist:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml (condensed)
name: PR QA Gate

on:
  pull_request:
    branches: [main]

concurrency:
  group: pr-qa-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  changes:                 # is this a docs-only change? (scripts/classify_changes.py)
  unit-tests:              # Postgres service; unit, realistic and installer-sweep tests with -x; the Lambda suites
  module-tests:            # the optional modules' own tests, same shape, minutes not an hour
  base-requirements-only:  # the suite with only backend/requirements.txt installed
  smoke-tests:             # backend/tests/smoke: the wiring checks (routes exist, deps resolve)
  frontend-tests:          # jest; next build
  sdk-contracts:           # golden vectors, offline
  core-build:              # the core profile stands alone: copy the tree, delete modules/, rebuild, test
  full-build:              # the same sequence on the full tree, modules included
  sdk-live-contract:       # Postgres + Redis + backend + seed; the SDKs' contract smoke (SDK book)
  browser-e2e:             # five fail-hard browser journeys against the static export
  docker-smoke:            # build the images; boot compose; walk the golden path through them
```

`concurrency` with `cancel-in-progress` means a new push cancels the previous run — no queue
of stale results. The group is keyed on the pull request **number**, not the branch name:
two forks' `patch-1` branches share a branch name and would cancel each other's runs.

The `changes` job is what lets a typo fix skip forty minutes of tests. Its subtlety is that
branch protection requires checks to *report*: a workflow that simply did not run on a
docs-only change would leave every required check missing and the pull request blocked. So
the heavy jobs are **skipped** (a skipped plain job still reports under its name), and the
three whose required names would not survive a skip — `core-build` and `full-build`, which were
one matrix until a skipped matrix reported its unexpanded name, and `SDK Live Contract /
sdk-live-contract (core)` from a reusable workflow — always run and condition their steps
instead. A classifier that itself fails makes everything run: a skipped check is accepted by
protection, so "skip unless docs-only" would turn a crashed classifier into a green gate with
nothing run.

The unit job runs the Lambda suites too, with a comment that explains a past gap:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
      - name: Run unit tests
        env:
          APP_ENV: test
          TESTING: 'true'
          ...
        run: python -m pytest backend/tests/unit/ backend/tests/realistic/ backend/tests/installer_sweeps/ -p no:cov -q --tb=short -x --junitxml=unit-junit.xml

      ...
      # The Lambda functions (event processor, flag evaluation) and their
      # shared package carry tests of their own (240 collected) that ran
      # nowhere in CI. Each function's tests/ dir
      # must run in its own pytest invocation (they all define `tests.conftest`).
      - name: Run Lambda tests
        run: |
          for d in backend/lambda/event_processor backend/lambda/feature_flag_evaluation backend/lambda/shared; do
            python -m pytest "$d" -p no:cov -q --tb=short || exit 1
          done
```

`-x` (stop at first failure) in the PR gate is a speed choice: a red PR gate needs one
failure to act on, not the full list; the nightly runs without it. `-p no:cov` disables the
coverage plugin — coverage is a separate, explicit run. (No stack deploys a handler from
`backend/lambda/`; these suites keep the code that is there honest. Part II, Hour 4.) Between
the two steps sits one that is easy to miss: `scripts/check_junit_skips.py` fails the job if a
test that must run was *skipped* — in the workflow-reading tests, the statistical gates, the
seeded realistic scenarios, the docs tests, the AWS installer's harness and fault sweeps
(`backend/tests/installer_sweeps`, which need only bash and jq) and the ground-truth library's
cross-checks — because a skipped gate looks exactly like a passing one.

The Docker smoke is the job that most resembles a customer — it builds the images and walks
the same path a self-hoster would:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml (condensed)
  docker-smoke:
    name: Docker Smoke
    runs-on: ubuntu-latest
    timeout-minutes: 25
    env:
      SEED: demo,sdk-contract
      ...
      SECRET_KEY: ci-only-docker-smoke-secret-key-0123456789abcdef0123456789
    ...
      - name: Start the stack
        run: docker compose -f docker-compose.yml up -d --wait --no-build postgres redis api frontend

      - name: API readiness and auth gate
        run: |
          curl -sf localhost:8000/health/ready -o ready.json
          grep -q '"status":"healthy"' ready.json
          curl -sf localhost:8000/health/live >/dev/null
          code=$(curl -sL -o /dev/null -w '%{http_code}' localhost:8000/api/v1/experiments)
          ...
      - name: Login, create an API key, assign a user (sdk_contract_ab)
        run: |
          TOKEN=$(curl -sf -X POST localhost:8000/api/v1/auth/login ...
          curl -sf localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN" | jq -e '.role == "ADMIN"' >/dev/null
          KEY=$(curl -sf -X POST localhost:8000/api/v1/api-keys ...
          curl -sf -X POST localhost:8000/api/v1/tracking/assign ... -d '{"experiment_key":"sdk_contract_ab","user_id":"docker-smoke-user"}'

      - name: Dashboard through nginx
        run: |
          test "$(curl -s -o /dev/null -w '%{http_code}' localhost:3000/login)" = "200"
          # Fetch to files: piping curl into `grep -q` makes curl exit 23
```

That last comment is the `pipefail` trap from the Build book (Day 24): `grep -q` closes the
pipe as soon as it matches, `curl` gets `SIGPIPE`, exits 23, and `set -o pipefail` fails the
step on a success. Fetch to a file, then grep. The same job also builds the `full` image and
checks that every module-only dependency is in it and none is in `core` — the list is
derived from the two lock files, not typed. It runs the image with each misconfiguration the
settings preflight must refuse and greps for the exact refusal line, `FORWARDED_ALLOW_IPS=172.18.0.3/16`
among them; and once the stack is up, a step named "The API names the proxies it believes" reads
the API's `trusted proxies` start line (Part I, Hour 3) and fails on the `WARNING` form or an empty
network list.

The release gate — deterministic dependencies from a dedicated compose file, run through the
same script a developer uses locally, plus the security checks, summarised into one named
check:

```yaml
# 📁 .github/workflows/release-gate.yml
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
  workflow_dispatch:
  ...
  backend-gate:
    name: Backend Gate (Unit + Smoke)
    ...
      - name: Start deterministic backend test dependencies
        run: docker compose -f docker-compose.test.yml up -d postgres-test redis-test

      - name: Run backend release checks
        env:
          EP_TEST_PROFILE: compose
        run: ./scripts/run-backend-tests.sh release
  ...
  security-gate:
    name: Security Gate (Bandit + Gitleaks)
  ...
  gate-summary:
    name: Release Gate Summary
    runs-on: ubuntu-latest
    needs: [backend-gate, frontend-gate, security-gate]
    if: always()
    steps:
      - name: Fail if any gate failed
        env:
          RESULTS: ${{ toJSON(needs) }}
        run: |
          set -euo pipefail
          printf '%s\n' "$RESULTS" | jq -r 'to_entries[] | "\(.key)=\(.value.result)"' > results.txt
          test -s results.txt || { printf '::error::needs produced no results\n'; exit 1; }
          ...
```

Two things make the summary a reliable signal. `if: always()` means it runs even when a gate
fails, so there is always a `Release Gate Summary` check-run with a conclusion. And it reads
`toJSON(needs)` — the whole `{"job": {"result": …}}` object — instead of a typed list of
three `needs.<job>.result` comparisons, so a job added to `needs` is checked without anyone
remembering to add it to the list. (The typed list is exactly how a new failing gate once
went unread while the summary stayed green.) The test runner script:

```bash
# 📁 scripts/run-backend-tests.sh
# Modes:
#   unit        Run unit tests only
#   integration Run integration tests only
#   smoke       Run smoke wiring checks only
#   release     Run release gate backend checks (unit + smoke)
#   all         Run unit + integration + smoke

MODE="${1:-release}"
PROFILE="${EP_TEST_PROFILE:-external}" # external | compose

if [[ "${PROFILE}" == "compose" ]]; then
  # --wait blocks until the services' healthchecks pass, so the schema
  # bootstrap below never races Postgres' first-start initialisation.
  docker compose -f docker-compose.test.yml up -d --wait postgres-test redis-test
  trap 'docker compose -f docker-compose.test.yml down --remove-orphans >/dev/null 2>&1 || true' EXIT
  export POSTGRES_PORT="${POSTGRES_PORT:-5433}"
  export REDIS_PORT="${REDIS_PORT:-6380}"
```

Ports 5433/6380 so the test dependencies never collide with a developer's running stack —
the file CI uses is the one you use with `EP_TEST_PROFILE=compose` locally, which is the
point: "deterministic" means a pinned image and an empty database, the same in both places.
(It is not the same Postgres image as every other job: `docker-compose.test.yml` pins its
own, and the development compose file pins another. Deterministic, not identical.)

**Security scans** run on push, on pull requests and weekly, and their summary is required:

```yaml
# 📁 .github/workflows/security-scan.yml (condensed)
  python-security:     # bandit over backend/app (and modules/backend/app when present); pip-audit
  frontend-security:   # npm audit
  semgrep:             # semgrep scan (SAST) → report artifact
  secret-detection:    # the gitleaks binary over full history, --redact
  container-security:  # trivy over the built images
  dependency-review:   # pull requests only: advisories and denied licences in the diff
  security-summary:    # "Security Scan Summary": toJSON(needs), like the release gate
```

```yaml
# 📁 .github/workflows/security-scan.yml
      - name: Detect secrets with gitleaks
        run: gitleaks detect --source . --log-opts="--all" --redact --verbose
```

The gitleaks *binary*, not the gitleaks Action: the Action is licensed for organisations and
began failing every run once the repository moved to one; the scanner itself is open source.
`--redact` so a finding names the file and rule without reprinting the secret into a public
log.

The **Regression Guard** is the one process rule enforced by CI: a pull request labelled
`bug` must change a test.

```yaml
# 📁 .github/workflows/regression-guard.yml
      - name: Require a test with every bug fix
        ...
          if ! is_labelled bug; then
            echo "Not labelled \`bug\` — nothing to enforce."
            exit 0
          fi
```

And the **nightly** suite (`02:00 UTC`) runs what's too slow or too environment-dependent for
a pull request: integration tests, benchmarks, the smoke tests, the SDK contracts again (and a
check that every SDK release published with a local evaluator is pinned in the compatibility
gate), the browser E2E suite, and the ground-truth replay below at four times the users, once
clean and once with every fault combined, filing an issue when it fails. "When it fails" took a
correction worth knowing:

```yaml
# 📁 .github/workflows/nightly-qa.yml
  # A red night has to leave something behind that a human sees in the morning.
  nightly-failure-issue:
    name: File an issue on failure
    ...
    # A job that hits its timeout-minutes ends `cancelled`, not `failure`, so
    # failure() alone stayed false on 2026-11-02 and 2026-11-03 and no issue was
    # filed. A person cancelling the run files one too; that fails closed.
    if: ${{ always() && (contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')) }}
```

A timed-out job is the slowest kind of failure, and `failure()` did not count it, so the alerting
path was silent on exactly the nights it was needed.

**What branch protection requires.** Exactly these 20 check names, as `REQUIRED_CHECKS` in
`scripts/configure-repo.sh` declares them. That script is what applies branch protection, and it
changes required-check names and nothing else: before it writes, it runs
`scripts/check_required_checks.sh` against live protection and refuses, writing nothing, if live
differs from its body in any other way. What the live repository enforces is a setting only a
maintainer can read, with `gh api repos/Controlvar/controlvar/branches/main/protection/required_status_checks`.
The body the script writes is "what main has today": `strict` is **false**, so a branch need not
be up to date with `main` to merge, and there is **no** required pull request review:

| Workflow | Required checks it reports |
|---|---|
| PR QA Gate | `Unit Tests`, `Module Tests`, `Base Requirements Only`, `Smoke Tests`, `Frontend Tests`, `SDK Contract Tests`, `core-build`, `full-build`, `SDK Live Contract / sdk-live-contract (core)`, `Browser E2E`, `Docker Smoke` |
| Release Gate | `Release Gate Summary` |
| Security Scan | `Security Scan Summary` |
| Integration Tests | `integration-tests` |
| SDK Unit Tests | `SDK Unit Tests` |
| Infrastructure Tests | `CDK Stack Tests (Python)` |
| Lint | `lint` (ruff, eslint, tsc, and the rest of `make lint`) |
| Regression Guard | `regression-guard` |
| DCO | `DCO` (every commit carries a `Signed-off-by`) |
| Leak Guard | `Leak Guard` (`scripts/leak_guard.py`: secrets and false claims in tracked files) |

With `strict` off, two pull requests that are each green against the `main` they started from
can merge one after the other and leave `main` red, because neither was tested with the other.
What catches that is the Release Gate's run on every push to `main` (it is on the commit a release
is cut from, and Deploy looks it up there), and the nightly suite. `strict` on would close the gap
at the price of bringing every open pull request up to date, and re-running its checks, after each
merge; the script's comment says either setting is changed "on purpose, here and live together".

Not required, and worth knowing: **`conventional-title`** checks that a pull request title is
a conventional commit. It reports, but it does not block — and the title is the squash
subject release-please reads (Hour 11), so a prose title merges and then silently drops out
of the changelog. The nightly, performance and Cognito workflows, CodeQL and Doc Examples are
advisory too, and so are the newer workflows below.

**The workflows that run only when they are needed.** Several workflows are path-filtered or
dispatched by hand, and none of them is required. The path-filtered ones say why in their
headers: a path-filtered workflow does not report at all on a pull request that touches none of
its paths, and "a required check that does not report blocks a pull request for ever". The rule
used instead, in `installer-macos.yml`'s words: "a pull request that runs this job merges only
with it green at its head."

| Workflow | Runs when | What it proves |
|---|---|---|
| `ground-truth.yml` (Ground-truth Replay) | a pull request touching a file the API imports on the core profile (or its own files), the `e2e` label, or a dispatch | seeded, realistic traffic through the public API to a freshly booted platform, and the results page checked against the event contract and an independent reference (`tests/ground_truth`) |
| `scale-evidence.yml` (Scale evidence) | dispatched on `main`; a smoke run on pull requests touching the harness | the shipped Compose stack under k6 on a GitHub-hosted runner, with the release images `compose.yml` names: six request types, three runs each by default, merged into one results file a person commits verbatim |
| `installer-macos.yml` | changes under `deploy/aws-installer/` or its tests | the AWS installer's harness and fault sweeps on macOS's `/bin/bash` 3.2 and BSD tools, which `Unit Tests` runs on Linux |
| `demo-apps.yml` (Demo Apps) | changes under `demo/` and what its tests load | ShopLab's and StreamPulse's jest suites and the simulators' pytest suites, through the same `scripts/rc_evidence.py --suite` command the release-candidate evidence uses |
| `contributor-guard.yml` | every pull request, on `pull_request_target` (one from this repository passes at once) | that a fork changes only the areas open to outside contributors (`scripts/contributor_guard.py`); it runs main's copy of the script, checks nothing of the pull request out, and reads the file list through the API |

Two of these teach something general. `contributor-guard` runs on the privileged trigger
(`pull_request_target` runs main's code, here with a read-only token, before anyone approves a
fork's runs), so its shape is pinned by a test: no `if:` (a skipped job reports success), no
`continue-on-error`, no `concurrency:`, nothing installed, and every event value reaches the script
only through `env:`. And `scale-evidence` refuses to measure unless `compose.yml` is byte for byte
the release tag's and the images' revision labels are the tag's commit: a capacity number is
evidence only about what it measured. At this book's pin the harness exists and no results file
has been committed yet; #1756, which held up the measurement, was fixed in v0.43.2 (Part III,
Hour 9).

> **Watch Out!** "Required" is a branch-protection setting, not a property of the workflow
> file. A new job added to the PR gate isn't required until someone adds its exact check name
> in the repository settings — and renaming a required job leaves the old name required and
> never reported, which blocks every pull request. The `Release Gate Summary` name is
> load-bearing twice: protection requires it and the deploy looks it up.

> **Did You Know?** The Docker smoke job builds the images from scratch on every PR — slow
> (the 25-minute timeout is real) but the only check that would catch a broken `Dockerfile`,
> a module dependency missing from the `full` image, or an nginx template regression before a
> self-hoster does. It exists because those exact things broke once each.

### Try It Yourself

```bash
gh pr checks <number>                                   # the check list with required/advisory status
gh api repos/Controlvar/controlvar/branches/main/protection/required_status_checks --jq '.contexts'   # needs a token that can read the setting
EP_TEST_PROFILE=compose ./scripts/run-backend-tests.sh release    # exactly what the release gate runs
gh run list --workflow "Release Gate" --branch main --limit 3
gh api "repos/Controlvar/controlvar/commits/$(git rev-parse origin/main)/check-runs?check_name=Release%20Gate%20Summary" --jq '.check_runs[].conclusion'
```

The last command is the question the deploy asks (`scripts/verify_release_gate.sh` asks it
with `check_name=` and `--paginate`, because a release commit carries more than one page of
check runs and an unfiltered read can miss the gate).

### Key takeaways

- PR QA Gate: a docs-only classifier, then unit (+ Lambda suites), module, base-requirements,
  smoke, frontend, offline SDK contract, `core-build`/`full-build`, live SDK contract, Browser
  E2E, Docker smoke — `-x`, cancel-in-progress per pull request number.
- Release Gate: deterministic backend run via `run-backend-tests.sh release` on
  `docker-compose.test.yml`, frontend build, Bandit + gitleaks → one `Release Gate Summary`
  (`if: always()`, `toJSON(needs)`), on pull requests and every push to `main`.
- 20 required checks, including `Security Scan Summary`, `DCO` and `Leak Guard`;
  `conventional-title` is not required. "Required" lives in branch protection, which has
  `strict` off and no review rule; the push-to-`main` Release Gate and the nightly catch what
  two separately green pull requests break together.
- Path-filtered and dispatched workflows (ground-truth replay, k6 scale evidence, installer on
  macOS, demo apps) are never required, because a required check that does not report blocks a
  pull request for ever; nor is the contributor guard, which checks what a fork may change.

---

## Hour 11 — From merge to production: release-please, signed images, and the Deploy workflow

### What & why

Two separate machines take a change from `main` to an environment, and it pays to keep them
apart in your head.

**The release** happens on its own. Every merge to `main` is squash-merged with the pull
request title as its subject, and release-please reads those subjects as conventional commits
(`feat`, `fix`, `perf`, … with `!` for a breaking change). It keeps a release pull request
open — `chore(main): release X.Y.Z` — that bumps the version in every place it lives. Merging
that pull request creates the tag `vX.Y.Z` and a GitHub release, and release-please then
**calls** `release.yml`, which builds the four images, scans them, pushes them to GHCR and signs
them. Nobody runs the release workflow by hand on a normal day.

**The deploy** happens when a person asks for it. `Deploy` (`.github/workflows/deploy.yml`)
is dispatched with an environment (`staging` or `prod`), a release tag and a profile. One
workflow serves both environments; every AWS name is built from the environment input. It
refuses everything it can before anyone is asked to approve, asks for one approval, then
builds an image from the tag, snapshots the database, runs the migration as a **one-shot ECS
task**, registers a new API task-definition revision by digest, and hands that revision to
CodeDeploy for a blue/green canary. It approves the traffic shift itself, smoke-tests the
public URL, and rolls the dashboard out last.

Three design decisions are worth understanding before the YAML.

- **Migrations run before the code, separately, and only there.** The API tasks run with
  `RUN_MIGRATIONS=false`; the migration task is the only thing that writes the schema on AWS.
  A new task set must not start against an old schema, and replicas must not race to migrate
  (Part I, Hour 3). The consequence: `cdk deploy` alone creates no schema, so a new
  environment answers `/health` with 200 and real requests with 500 until its first Deploy.
- **The deploy is a task-definition revision, not a `cdk deploy`.** CloudFormation is not in
  the loop: a deploy takes minutes and a rollback is "put the previous revision back".
- **Every migration must be backward-compatible** (expand, then contract). The previous
  release has to keep working against the migrated schema, because that is what a rollback
  runs (Hour 12).

### How Controlvar does it

**The release.** release-please's own workflow ends by calling the release:

```yaml
# 📁 .github/workflows/release-please.yml
  release:
    name: build and publish the release
    needs: release-please
    if: needs.release-please.outputs.release_created == 'true'
    uses: ./.github/workflows/release.yml
    with:
      tag: ${{ needs.release-please.outputs.tag_name }}
```

`release.yml` has three entry points: `workflow_call` (this one, the normal path), a pushed
`v*` tag (a human pushing a pre-release tag such as `v1.2.0-rc.1`), and `workflow_dispatch`
with a `tag` input. The last one is for **recovering a failed called run**, after
`git ls-remote --tags origin vX.Y.Z` shows the tag exists — not for every release:
dispatching it beside a successful called run rebuilds the release and re-points its image
tags. The release pull request itself shows **zero checks** — events created by the workflow
token start no workflows — so it can never satisfy branch protection and is merged by an
administrator after checking its diff touches only the version files. Before 1.0, a breaking
change (`!`) bumps the **minor** version (`bump-minor-pre-major`).

What a release publishes, for version `X.Y.Z`:

- images `ghcr.io/controlvar/controlvar:{core,full}-X.Y.Z` and
  `ghcr.io/controlvar/controlvar-web:{core,full}-X.Y.Z`, `linux/amd64` only, public
  (anonymous pull works); a final release also moves the floating `:core` and `:full` tags;
- each image scanned for stored credentials with trivy (two passes) before it is pushed,
  then signed with cosign keyless and given an SPDX SBOM attestation;
- the version written in one source (`VERSION`) and checked everywhere else it appears by
  `scripts/check_version_sources.py`.

The documentation site is not part of that call. It is built from the tag by dispatching
`gh workflow run docs.yml --ref vX.Y.Z`, and it deploys only for a final `v*` tag (an rc tag
builds but does not deploy). The app's [CONTRIBUTING.md](https://github.com/Controlvar/controlvar/blob/09d815ea18273982d710fb444ba76f05a18bc5c6/CONTRIBUTING.md)
describes how a release is cut and re-run, and
[Releases, images and verification](https://github.com/Controlvar/controlvar/blob/09d815ea18273982d710fb444ba76f05a18bc5c6/docs/self-hosting/releases.md)
says what a release publishes and how to check it.

**The deploy's inputs.** The profile is a decision, not a default:

```yaml
# 📁 .github/workflows/deploy.yml
name: Deploy
...
on:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Environment to deploy to'
        required: true
        type: choice
        default: staging
        options: [staging, prod]
      version:
        description: 'Release tag to deploy, e.g. v1.2.3 (branch names and commit SHAs are refused)'
        required: true
        type: string
      profile:
        description: 'Image profile to build and deploy (full = modules included, signed compliance audit log)'
        required: true
        type: choice
        default: full
        options: [full, core]
      accept_unsigned_audit_log:
        ...
      override_alarms:
        ...
      override_alarms_reason:
        ...
```

`profile: core` builds an image with no `modules/`, so the compliance audit log is written
unsigned; the run refuses `core` unless `accept_unsigned_audit_log` is ticked, and says so in
its summary. `override_alarms` (with a mandatory reason) is the break-glass of Hour 12.

**Preflight, with no AWS credential.** The first job holds no AWS role and no environment
secret, so a wrong dispatch is refused before anyone is asked to approve it: the workflow
must be run from `main`; the core profile must be acknowledged; and `version` must be a
release tag whose commit is on `main` and passed the Release Gate:

```yaml
# 📁 .github/workflows/deploy.yml
      - name: The version is a release tag on main
        id: tag
        env:
          VERSION: ${{ inputs.version }}
        run: |
          set -euo pipefail
          ...
          if ! [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
            echo "::error::${not_a_tag}"
            exit 1
          fi
          if ! SHA="$(git rev-parse -q --verify "refs/tags/${VERSION}^{commit}")"; then
            echo "::error::${not_a_tag}"
            exit 1
          fi
          if ! git merge-base --is-ancestor "$SHA" HEAD; then
            echo "::error::${VERSION} (${SHA:0:7}) is not on main. Only a release cut from main can be deployed."
            exit 1
          fi
          echo "sha=$SHA" >> "$GITHUB_OUTPUT"

      - name: The Release Gate passed on that commit
        env:
          GH_TOKEN: ${{ github.token }}
          REPO: ${{ github.repository }}
          SHA: ${{ steps.tag.outputs.sha }}
          VERSION: ${{ inputs.version }}
        run: bash scripts/verify_release_gate.sh "$REPO" "$SHA" "$VERSION"
```

Note the pattern: operator input reaches the shell **only through `env:`**, never by
`${{ inputs.… }}` inside a `run:` block, because Actions substitutes the expression into the
script text before bash parses it — and the next job holds the environment's AWS role.

**One approval, then refusals.** The `deploy` job is the only one bound to
`environment: ${{ inputs.environment }}`, so the environment's required reviewer is asked
once. Before anything in AWS changes it refuses, each with a named error: an environment
without its `AWS_ACCOUNT_ID` or `AWS_ROLE_ARN` secret or its `PUBLIC_BASE_URL` variable (a
red run, never a silent skip); a role in the wrong account; stacks that are not deployed; a
profile the stacks were not deployed for; an API task family whose newest revision has no
usable `FORWARDED_ALLOW_IPS` (below); a secret a task definition references that does
not exist; a missing ECR repository; an earlier deployment still active; and an API 5xx alarm
in ALARM (checked again just before the snapshot). The concurrency group is
`deploy-<environment>` and is never cancelled: a deploy stopped halfway has migrated a
database and left a CodeDeploy deployment behind.

**Build once, by digest.** The API and dashboard images are built from the tag, pushed to
ECR (`experimentation-platform/backend` and `experimentation-platform/web`, created once per
account by hand), their labels checked against the requested profile and commit, and their
**digest** resolved. Everything after this names the image by digest, never by a tag that
could move.

> **Watch Out!** What an AWS environment runs is built by `Deploy` from the tag, not the
> signed image the release published to GHCR. Same source, same Dockerfile, different build —
> and a tag that already exists in the account's ECR repository (`:<tag>-<profile>`) is reused
> rather than rebuilt, with its labels checked against the requested commit and profile. So
> "the image I verified with cosign" and "the image prod runs" are two images.

**Snapshot, then migrate.** Always, before the migration:

```yaml
# 📁 .github/workflows/deploy.yml
      - name: Snapshot the database before migrating
        id: snapshot
        ...
        run: |
          set -euo pipefail
          id="pre-deploy-${TARGET_ENV}-${VERSION}-$(date -u +%Y%m%d-%H%M%S)"
          ...
          aws rds create-db-cluster-snapshot \
            --db-cluster-identifier "$DB_CLUSTER" \
            --db-cluster-snapshot-identifier "$id" >/dev/null
          timeout "$SNAPSHOT_DEADLINE_SECONDS" \
            aws rds wait db-cluster-snapshot-available --db-cluster-snapshot-identifier "$id"
          ...
      - name: Register the migration task definition with this image
        ...
      - name: Run the migration
        id: migrate
        ...
        run: |
          set -euo pipefail
          ...
          overrides='{"containerOverrides":[{"name":"backend","command":["python","-m","backend.app.db.bootstrap"]}]}'
          bash scripts/run_migration_task.sh "$ECS_CLUSTER" "$TASK_DEFINITION" "$network" "$overrides" "$MIGRATE_LOG_GROUP"
```

The migration family is per environment, `experimentation-migrate-<env>`, and the run
registers a revision of it naming **this** image's digest, so the migration and the API end
up on the same build and the same profile (the two profiles do not read the same
`alembic_version`: a full database has two heads). The subnets and security group come from
the Fargate stack's outputs. The command is `bootstrap`, not raw `alembic upgrade heads`:

```python
# 📁 infrastructure/cdk/stacks/migration_task_stack.py
IMAGE_WORKDIR = "/app"
ALEMBIC_CONFIG = "backend/app/db/alembic.ini"

#: **Not** raw ``alembic upgrade heads``.  The historical migration chain
#: cannot be replayed from an empty database -- ...
MIGRATION_COMMAND = [
    "python",
    "-m",
    "backend.app.db.bootstrap",
]
```

`bootstrap` handles both states: on a fresh database it creates the schema from the models
and stamps both heads; on an existing one it runs `alembic upgrade heads`. The first deploy
of an environment meets the empty database, which the raw chain cannot replay.
`backend/tests/unit/infrastructure/test_migration_task_command.py` checks that this command,
the deploy workflow and the `Database Migration` workflow agree with the image layout.

**Register the API revision, and refuse one that would migrate.** The new revision is copied
from the family's newest one with the image swapped. If the Fargate stack was last deployed
from an older checkout, that copy can lack `RUN_MIGRATIONS=false`, so the workflow reads back
what ECS stored and refuses before CodeDeploy is asked for anything:

```yaml
# 📁 .github/workflows/deploy.yml
      - name: Refuse an API revision that would run migrations on start
        id: api-td-check
        env:
          TASK_DEFINITION: ${{ steps.api-td.outputs.arn }}
        run: |
          set -euo pipefail
          python3 scripts/refuse_migrating_api_revision.py \
            --task-definition "$TASK_DEFINITION" \
            --environment "$TARGET_ENV" \
            --fargate-stack "$FARGATE_STACK"
```

The same copy can lack `FORWARDED_ALLOW_IPS` (Part II, Hour 5), and then the new image would see
every request as the load balancer's. So since v0.40.0 a second script refuses that, twice, with
no `if:`: once on the family's newest revision **before anything is built or changed**, and once
on what ECS stored for the registered ARN, just before the CodeDeploy deployment:

```yaml
# 📁 .github/workflows/deploy.yml
      - name: Refuse an API task definition family without FORWARDED_ALLOW_IPS
        run: |
          set -euo pipefail
          python3 -I -S scripts/refuse_api_revision_without_forwarded_allow_ips.py \
            --family "$ECS_BACKEND_TASK_FAMILY" \
            --release release \
            --environment "$TARGET_ENV" \
            --fargate-stack "$FARGATE_STACK"
```

Three habits in that script are worth stealing for any deploy guard:

```python
# 📁 scripts/refuse_api_revision_without_forwarded_allow_ips.py
Exit status: 0 go on (the setting is usable, or the release predates it); 1
refused; 2 could not tell (bad arguments, the AWS call failed, its answer
could not be read, it describes another revision, or main() raised: an
`::error::` naming the exception's type only). 2 also stops the deploy: "I
could not read it" is not "it is set".
```

First, **"could not tell" fails closed**: exit 2 stops the deploy exactly as a refusal does.
Second, it does not re-implement the rules. It imports `entry_problems` and
`every_address_problems` from `backend.app.core.proxy_trust` in its own checkout and applies the
staging-and-production rules, so the guard and the image's start-up check share one definition of
a usable entry. Third, it can only read: its whole allow-list of AWS operations is
`ecs describe-task-definition`. And it knows its history: a release from before the change still
passes the old fixed list to uvicorn and needs nothing from the task definition, so such a
release is skipped with a notice, but only when its entrypoint is positively identified by that
old line; anything it cannot identify is checked. `rollback.yml` runs neither of these two
checks.

**The canary.** The AppSpec is the whole contract with CodeDeploy — which task definition,
which container and port:

```yaml
# 📁 .github/workflows/deploy.yml
      - name: Create the CodeDeploy deployment (blue/green)
        ...
          appspec="$(jq -cn --arg td "$TASK_DEFINITION" '{
            version: 1,
            Resources: [{
              TargetService: {
                Type: "AWS::ECS::Service",
                Properties: {
                  TaskDefinition: $td,
                  LoadBalancerInfo: { ContainerName: "backend", ContainerPort: 8000 }
                }
              }
            }]
          }')"
          id="$(aws deploy create-deployment \
            --application-name "$CODEDEPLOY_APPLICATION" \
            --deployment-group-name "$CODEDEPLOY_DEPLOYMENT_GROUP" \
            --description "deploy ${VERSION} (${PROFILE})" \
            ${override[@]+"${override[@]}"} \
            --revision "$(jq -cn --arg c "$appspec" '{revisionType: "AppSpecContent", appSpecContent: {content: $c}}')" \
            --query deploymentId --output text)"
```

The application is `experimentation-platform-<env>` and the deployment group
`experimentation-<env>` (Part II, Hour 5). CodeDeploy starts the green task set and then
**waits up to 30 minutes for approval** before shifting traffic. The workflow gives it:
`scripts/shift_traffic.py` checks every target in the replacement target group is healthy,
sends `continue-deployment` once, and polls to a 2,400-second deadline
(`CODEDEPLOY_DEADLINE_SECONDS`): the replacement tasks starting, the approval, the canary and the
final shift. The canary is 10% of traffic for 15 minutes (`CANARY_10_PERCENT_15_MINUTES`, Part II),
then 100%; a synth test pins the deadline to stay 1,500 seconds longer than the canary. Success means the PRIMARY task set is this revision
**and** the `/api/*` listener rule forwards to its target group. While the deployment is
active — the canary and the hour after the shift, while the old task set is kept — the
group's two 5xx alarms watch it, and CodeDeploy rolls the API back by itself if either fires
(Hour 12).

**Smoke test through the public URL.** A real request, not a probe — `/health` answers 200
whatever the routes do:

```yaml
# 📁 .github/workflows/deploy.yml
      - name: Smoke test through the public URL
        ...
          url="${PUBLIC_BASE_URL%/}/api/v1/experiments/"
          expected='{"detail":"Not authenticated"}'
          ...
            if [ "$code" = "401" ] && [ "$body" = "$expected" ]; then
              echo "smoke: GET $url -> 401 $body"
              exit 0
            fi
```

Through `vars.PUBLIC_BASE_URL`, never the load balancer's DNS name, which the API's trusted
host check rejects. A failed smoke test rolls nothing back by itself; it fails the run and
points at the rollback line.

**The dashboard last.** Only once the API serves the new revision and answers: the dashboard
gets a new revision of `experimentation-dashboard-<env>` and an ECS rolling deployment with
the circuit breaker (`scripts/ecs_rolling_rollout.sh`), then its own smoke test (200). This
order leaves "new API, old dashboard" for the minutes of the rollout; the other order would
leave a new dashboard calling routes an old API may not have.

**The summary.** The run ends by printing the line to paste into `Rollback` if it is needed:
`environment=<env>, task_definition_arn=experimentation-backend-<env>:<n>,
dashboard_task_definition_arn=experimentation-dashboard-<env>:<m>` — the revisions that were
serving **before** this deploy.

**Before the first deploy of an environment** (each environment is its own AWS account, in
`us-west-2`; the app's [deployment guide](https://github.com/Controlvar/controlvar/blob/09d815ea18273982d710fb444ba76f05a18bc5c6/docs/deployment/deployment-guide.md)
has the full checklist):

- a GitHub environment named `staging` or `prod` with a required reviewer, the **secrets**
  `AWS_ACCOUNT_ID` (a secret, not a variable, so it is masked in public logs) and
  `AWS_ROLE_ARN` (an OIDC role in that account), and the **variable** `PUBLIC_BASE_URL`;
- the two ECR repositories, created by hand once per account and region;
- the stacks deployed (`ENVIRONMENT=<env> cdk deploy --all`, Part II), which leaves the
  environment without a schema until this workflow's first migration;
- a DNS record for the public hostname pointing at the load balancer (no stack creates it).

> **Watch Out!** A successful deploy's CodeDeploy deployment stays **active for an hour**
> after its traffic shift, while the group keeps the old task set — that hour is the rollback
> window. CodeDeploy refuses a second deployment meanwhile, so `Deploy` refuses up front
> ("Refuse while an earlier deployment is still active") rather than after a build, a
> snapshot and a migration. A hotfix inside the hour goes through `Rollback` first (Hour 12).

> **By the Way** — `aws deploy wait deployment-successful` is not used anywhere. It cannot
> approve a deployment, and the group waits for approval, so it could only ever time out.
> Both `Deploy` and `Rollback` approve the shift themselves once the replacement tasks are
> healthy.

### Try It Yourself

Without an AWS account, read `deploy.yml` end to end and answer from the YAML alone: which
job holds the AWS role (only `deploy`); what happens if the Release Gate failed on the tag's
commit (preflight fails; nobody is asked to approve); what happens if the migration task
exits non-zero (no API revision is registered and no CodeDeploy deployment exists; the
snapshot's identifier is in the log); what happens if the smoke test fails (the API has
already shifted, the dashboard is not rolled out, and the run points at the rollback line).
Then:

```bash
gh workflow view Deploy
gh run list --workflow Deploy --limit 5
git ls-remote --tags origin 'v*' | tail -n 3
```

Verify a published image — the GHCR packages are public, so no login is needed. The identity is
the repository that built the image and it is case-sensitive: releases before 0.35.0 were signed
under the former repository's name, and `controlvar/controlvar` in lower case matches no signature:

```bash
IMAGE=ghcr.io/controlvar/controlvar:core-0.43.2
cosign verify "$IMAGE" \
  --certificate-identity-regexp '^https://github\.com/Controlvar/controlvar/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation --type spdxjson "$IMAGE" \
  --certificate-identity-regexp '^https://github\.com/Controlvar/controlvar/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
docker inspect --format '{{index .Config.Labels "io.experimently.profile"}}' "$IMAGE"
```

### Key takeaways

- Release: conventional PR titles → release-please PR (zero checks, admin merge) → tag →
  `release.yml` called by release-please → signed, SBOM-attested amd64 images on GHCR. Dispatch `release.yml` only to recover a failed called run, and
  `docs.yml --ref vX.Y.Z` by hand.
- Deploy: one `Deploy` workflow for staging and prod; inputs environment, release tag,
  profile; preflight with no AWS credential; one approval; refusals before any change.
- Order: refuse a task family without `FORWARDED_ALLOW_IPS` → build by digest → snapshot →
  one-shot migration task (`bootstrap`, the only schema writer) → API revision (refused if it
  would migrate, or lacks `FORWARDED_ALLOW_IPS`) → CodeDeploy canary approved by the workflow →
  smoke test via `PUBLIC_BASE_URL` → dashboard rolling deploy → rollback line.

---

## Hour 12 — Rollback, secrets and backups: the decision tree, what rolls back by itself, the Rollback workflow

### What & why

A deploy that can't be undone in minutes is a deploy you'll be afraid to do. The rollback
runbook (`docs/deployment/rollback-runbook.md` in the app) opens with a **decision tree** and
a two-minute budget, and its first rule is "when in doubt, roll back" — hotfixes are for
one-line changes only. The tree starts with two questions an older design never asked,
because CodeDeploy may already have acted: *did an alarm already roll the API back?* and *is
CodeDeploy rolling back right now?*

There are three ways the API goes back to a previous revision:

1. **CodeDeploy, by itself** — while a deployment is active, either 5xx alarm firing stops it
   and rolls it back. Nothing to do but confirm it.
2. **The `Rollback` workflow** — the preferred manual path: audited, notified, through
   CodeDeploy, with the dashboard too.
3. **By hand with the AWS CLI** — the runbook's Method 2, for when GitHub Actions itself is
   the outage. This book does not reproduce it: follow the runbook.

Database rollbacks are their own section, because an application rollback against a migrated
schema can be worse than the bug — and the **order** matters.

The rest of the Hour is what a recovery stands on: the secrets a deployment needs, and the
backups.

> **The first rollback in an environment.** `Rollback` is the intended path and the one the
> runbook describes, and the runbook's first instruction is to rehearse it: two gated deploys
> of two tags in staging, then roll back to the first, before production ever relies on it. The
> runbook is also candid that parts of the alarm path (an alarm already in ALARM before a
> deploy is approved, for one) have not been rehearsed on AWS. Treat the first rollback in each
> environment as the test it is.

### How Controlvar does it

The decision tree, with the thresholds that need no deliberation:

```text
# 📁 docs/deployment/rollback-runbook.md
Post-deployment issue detected
          |
          v
Did CodeDeploy already roll the API back?
  (the run ended "Rolled back by an alarm", or get-deployment shows
   Stopped with ALARM_ACTIVE)
          |
    YES --+--→ AN ALARM ROLLED THE API BACK (that section below; no Rollback)
          |
    NO    v
Is CodeDeploy rolling back right now?
  (an active deployment's creator is codeDeployRollback)
          |
    YES --+--→ WAIT for it; Rollback refuses while it is active
          |
    NO    v
Is any of the following true?
  - Health check /health returning non-200
  - 5xx error rate > 5% (vs. pre-deploy baseline)
  - p99 API latency > 5000ms
  - Data corruption detected (missing rows, wrong values)
  - ECS tasks crash-looping (stopped reason in logs)
          ...
          |   Is it a database schema issue?
          |         |
          |   YES ──→ DATABASE FIRST, THEN APPLICATION (DB Rollback section, then Method 1)
          |   NO  ──→ HOTFIX (deploy new tag following standard procedure)
          |
          |   Only the dashboard is broken, and the API is fine?
          |   YES ──→ DASHBOARD ONLY (Method 2, the dashboard block)
```

"Is there a workaround (feature flag at 0%)?" — on the branch the excerpt elides — is the
platform eating its own cooking: a behaviour behind a Controlvar feature flag can be turned off in
seconds without a deploy, and the safety monitor may already have done it.

**What CodeDeploy rolls back by itself.** The deployment group carries two alarms,
`experimentation-api-5xx-blue-<env>` and `experimentation-api-5xx-green-<env>`, one per target
group. A minute counts against the API when its target group answers at least 5 target 5xx
**and** at least 5% of its requests with a 5xx; two such minutes of three fire the alarm.
While a deployment is active (its canary, and the hour after its shift), a firing alarm makes
CodeDeploy stop it and put the previous revision back. The runbook calls this expected
behaviour, not yet observed in a real account. What the alarms do **not** cover, so `Rollback`
is the response: a release that answers wrongly with a 2xx; the load balancer's own 502/504
(no target-group dimension); the dashboard; anything after that hour.

**The `Rollback` workflow** — the deploy's summary printed its inputs:

```yaml
# 📁 .github/workflows/rollback.yml
name: Rollback
...
on:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Environment to roll back'
        required: true
        type: choice
        default: staging
        options: [staging, prod]
      task_definition_arn:
        description: 'Revision to roll back to: an ARN, or family:revision (e.g. experimentation-backend-<environment>:42). A deploy run summary prints it.'
        required: true
        type: string
      dashboard_task_definition_arn:
        ...
      reason:
        description: 'Reason for rollback'
        required: true
        type: string
```

It puts the previous revision back **through CodeDeploy** — the only way on a service whose
deployment controller is CodeDeploy:

```yaml
# 📁 .github/workflows/rollback.yml
      - name: Roll back via CodeDeploy
        id: codedeploy
        if: steps.stop.outputs.already_serving != 'true'
        env:
          TARGET_ARN: ${{ steps.target.outputs.arn }}
          REASON: ${{ inputs.reason }}
        run: |
          set -euo pipefail
          ...
          DEPLOYMENT_ID="$(aws deploy create-deployment \
            --application-name "$CODEDEPLOY_APPLICATION" \
            --deployment-group-name "$CODEDEPLOY_DEPLOYMENT_GROUP" \
            --deployment-config-name CodeDeployDefault.ECSAllAtOnce \
            --description "rollback: $REASON" \
            --override-alarm-configuration '{"enabled":false,"ignorePollAlarmFailure":false,"alarms":[]}' \
            --revision "$(jq -cn --arg c "$APPSPEC" '{revisionType:"AppSpecContent",appSpecContent:{content:$c}}')" \
            --query deploymentId --output text)"
```

Read it for its choices:

- **It checks the target first.** A bare family (no `:revision`) is refused — it would
  resolve to the newest revision, which during an incident is likely the one you are rolling
  back *from* — and so is a revision that is not ACTIVE.
- **It stops a deployment already in flight**, unless that deployment is CodeDeploy's own
  rollback (its `creator` is `codeDeployRollback`): stopping that would put back the release
  it is rolling away from, so the run refuses instead. It also refuses when the API is already
  on the target with nothing in flight.
- **All at once, not a canary** (`CodeDeployDefault.ECSAllAtOnce`). The target was serving
  minutes ago; a canary would leave 90% of traffic on the release that is hurting users.
- **Alarms overridden for this deployment only.** The release being rolled back is usually
  what holds an alarm in ALARM, and CodeDeploy stops every deployment to the group while one
  is — this one included. The group's alarms watch the next deploy as before.
- **It approves its own shift** (the group's 30-minute approval wait would otherwise stop
  it), and reports success only once the target is the PRIMARY task set — never by reading
  `services[0].taskDefinition`, which on a CodeDeploy service never moves off the revision
  CloudFormation created.
- **Then the dashboard**, through its own rolling deployment, if a dashboard revision was
  given. The run's headline says which halves it did.

The `reason` input is the audit trail: it ends up in the CodeDeploy deployment's description,
the run log and the Slack message, so the post-incident review can reconstruct the decision.
The `Rollback` concurrency group is its own, not the deploy's: a rollback must never wait
behind a deploy of the release it is undoing.

**Fix forward while an alarm is firing.** Rollback overrides the alarms for its own
deployment, but a forward `Deploy` is stopped while one fires. When rolling back does not
help — the bug is also in the previous release, a dependency outage holds the alarm, or the
environment has no schema yet and requests are failing until the first migration — `Deploy`
has the break-glass: tick `override_alarms` and give `override_alarms_reason`. The run's name
then ends in `ALARMS OVERRIDDEN`, which is what the environment's reviewer sees at the
approval: the reviewer is the check. That one deployment is watched by nothing.

**The database rollback order.** The `Database Migration` workflow (`db-migrate.yml`) takes
an environment, a direction and a target revision id; it refuses relative steps, `head` and
`base` (a full install has two heads), shares the deploy's concurrency group, snapshots
first, and runs `alembic` with the image of the API's **PRIMARY task set**:

```yaml
# 📁 .github/workflows/db-migrate.yml
on:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Environment whose database to migrate'
        required: true
        type: choice
        default: staging
        options: [staging, prod]
      direction:
        description: 'Migration direction'
        required: true
        type: choice
        default: upgrade
        options: [upgrade, downgrade]
      target:
        ...
concurrency:
  group: deploy-${{ inputs.environment }}
  cancel-in-progress: false
```

That last property sets the order. The workflow can only downgrade a migration while the
release that contains it is serving, so **undo the migration first, then roll the API back**.
After an API rollback or an alarm rollback the serving image does not contain the
migration's file and there is no supported downgrade through the workflow; the runbook's
remaining path from there involves restoring the database and the Engineering Lead. Because
migrations are backward-compatible by rule, most rollbacks need no database step at all: the
older release simply runs against the newer schema (the API tasks never migrate on start).

> **By the Way** — the same order holds off AWS, more strictly. Where the API migrates on start
> (Docker Compose, Part I, and the Helm chart's init container), the older image runs the
> bootstrap when it starts, meets revisions it has no file for, and refuses to start: "this
> database was migrated by a newer Controlvar release than this image". The runbook's rule there is
> to downgrade first, with the newer release still running (one `alembic downgrade <id>` to each
> head of the release you go back to: its core head, and on a full install its modules head too;
> `alembic heads`, run with the older image, prints them), and only then start the older image.
> Each downgrade in between runs, and the runbook's Step 2 says what each one loses.

**Secrets.** What a deployment needs in Secrets Manager, per environment:

| Secret | Who makes it | Profile |
|---|---|---|
| `/<env>/experimentation/jwt-secret` | a person, before the first deploy | both |
| `/<env>/experimentation/first-superuser-password` | a person, before the first deploy | both |
| `/<env>/experimentation/audit-hmac-key` | a person, before the first deploy | `full` only |
| `experimentation-database-<env>-aurora-credentials` | the database stack (Aurora generates it) | both |

There is no database-password secret to create and no Redis secret at all (the API reaches
Redis by host and port over TLS). The CDK imports the three hand-made secrets by their
**complete ARN** — the name plus the six-character suffix Secrets Manager adds — passed at
synth time as `JWT_SECRET_ARN`, `FIRST_SUPERUSER_PASSWORD_SECRET_ARN` and (full profile)
`AUDIT_HMAC_KEY_SECRET_ARN`, in every environment (Part II, Hour 5). A secret recreated under
the same name gets a new suffix, so the input changes and the stacks are deployed again.
ECS resolves a task's secrets once, when the task starts. `Deploy` checks before building
that every secret its task definitions reference exists. Application API keys (`eptk_…`) are
a different thing: they are rotated in the dashboard and hashed at rest (SDK book, Hour 1).

**Rotation, restore and restart are not in this book.** The app's secrets-management,
rollback and disaster-recovery documents keep the rotation schedule and the procedures for
rotating a secret, restoring the database to a point in time, and restarting the API
service. The API is CodeDeploy-controlled, so a restart is a CodeDeploy deployment that names
the revision already serving, not an ECS-side forced redeployment, and
CodeDeploy refuses a deployment for about an hour after any deploy; the runbooks say how to
check that first. A rotation reaches the API only after such a restart, because ECS reads a
secret into a task when the task starts. This book quotes none of their commands.

**Backups.** What protects the data (`docs/deployment/disaster-recovery.md`):

- **Aurora**: continuous backup with point-in-time recovery and a daily automated snapshot,
  retained **35 days in prod and staging** and 1 day elsewhere; a manual snapshot
  `pre-deploy-<env>-<tag>-<time>` before every deploy's migration (kept until you delete it);
  and a final snapshot when the prod stack is deleted.
- **Redis**: a daily snapshot kept 7 days in prod, 3 in staging, 1 in dev — it is a cache and
  rate-limit store, so losing it loses no data.
- **The API and dashboard** hold no data: their images and task-definition revisions are the
  backup.

The disaster-recovery plan states an **RPO of 5 minutes** as a target (Aurora's restorable
time is typically within five minutes of the present, and only within the retention period), and
it is careful to give its recovery time as a measurement rather than a promise: in a staging
rehearsal on a database holding little data, the restore took about twelve minutes and the cutover
took the API down for about thirteen; a larger database restores more slowly. It walks through
task crashes, a total ECS outage, an Aurora writer failure, a full cluster loss, Redis loss, a
region failure (the CDK creates no second region), a security breach and complete data loss.

> **Watch Out!** A cluster deployed from an earlier version of the CDK app, which set no
> retention, keeps CloudFormation's default of one day until its database stack is
> redeployed. Check what a cluster actually keeps before you rely on 35 days:
> `aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER" --query
> 'DBClusters[].BackupRetentionPeriod'` (read-only).

> **Did You Know?** The names in all three workflows — cluster, services, families,
> CodeDeploy application and group, alarms, stacks — are built from the `environment` input,
> and `infrastructure/tests/test_workflow_names_exist.py` renders them for staging and prod
> and looks each one up in a real synth of that environment. The old workflows spelled names
> for one environment and got them wrong in ways no test saw; now a name that does not exist
> fails CI.

### Try It Yourself

A tabletop drill, no account required. Take the scenario "`Deploy` of v1.4.0 to prod shifted
traffic at 14:00; at 14:06 the 5xx rate is 8% and `/health` is 200":

1. Walk the decision tree. Inside the hour after the shift, which question do you ask first,
   and what would you expect to have happened already? (An alarm rollback, if the 5xx came
   from the API's targets; the deploy run would end "Rolled back by an alarm".)
2. Suppose the 5xx are the load balancer's own 502s. Why did no alarm act, and what do you do?
3. Write the `gh workflow run Rollback -f environment=prod -f task_definition_arn=… -f
   reason="…"` invocation from the deploy run's summary line.
4. Was there a migration in v1.4.0? `git diff v1.3.0..v1.4.0 --stat --
   backend/app/db/migrations/versions/ modules/backend/app/db/migrations/versions/`. If one
   dropped or renamed something v1.3.0 reads, what must happen before step 3?
5. List what you check in the first 5 minutes after the rollback (the runbook's
   post-rollback checklist).

Then time yourself against the runbook's "< 5 minutes from decision to rollback complete".

### Key takeaways

- The decision tree first asks whether CodeDeploy already rolled back or is rolling back;
  then the hard triggers (`/health` non-200, 5xx > 5%, p99 > 5 s, corruption, crash loops);
  flag-at-0% as the first workaround; hotfix only for trivial changes.
- Three paths back: CodeDeploy's alarm rollback while a deployment is active; the `Rollback`
  workflow (through CodeDeploy, all at once, alarms overridden for that deployment, dashboard
  too); the runbook's CLI method when Actions is down. Rehearse in staging first.
- Database: undo the migration while its release still serves, then roll the API back.
- Secrets: three made by hand plus Aurora's generated one, imported by complete ARN; no
  Redis secret. Rotation, restore and restart procedures live in the app's runbooks.
- Backups: Aurora 35 days in prod and staging, a snapshot before every deploy's migration;
  RPO 5 min is a target, and the recovery time is a measurement, not a promise.

---

## Part IV Workshop

**Q1. Why two gates, and which one does the deploy check?**
PR QA Gate proves the code works (tests, both profiles, contracts, containers, a browser);
Release Gate proves the commit is releasable under deterministic dependencies plus security
checks and produces the single `Release Gate Summary` check-run. `Deploy`'s preflight
verifies that one, by name, on the tag's commit — which is why the Release Gate also runs on
every push to `main`.

**Q2. The migration task exits 1. What state is the environment in?**
The old API is still serving: no API revision was registered and no CodeDeploy deployment
exists, because both come after the migration. The schema is whatever the failed bootstrap
left — usually unchanged, since Postgres DDL is transactional — and a `pre-deploy-…` snapshot
taken minutes earlier is named in the log. Investigate; restoring is a last resort and a
runbook procedure, not a reflex.

**Q3. Why does the deploy register a task-definition revision instead of running `cdk deploy`?**
Speed and reversibility: a revision is one API call, CodeDeploy does the traffic shift, and a
rollback is a CodeDeploy deployment of the previous revision. CloudFormation would take
minutes, and the CDK declares the `bootstrap` placeholder image unless pinned — the
release's digest lives in the revisions `Deploy` registers, not in the stack (Part II,
Hour 5).

**Q4. Inside the hour after a successful deploy, you need to ship a fix. What happens if you
dispatch `Deploy`?**
It is refused up front ("an earlier deployment is still active"): the deployment stays
active for the hour the group keeps the old task set, and CodeDeploy would refuse a second
one. Run `Rollback` first, then deploy the fix once Rollback's own deployment is no longer
active.

**Q5. Your environment has no schema yet, scanners are hitting it, and an API 5xx alarm is
in ALARM. Why does the first `Deploy` fail, and what is the way out?**
`Deploy` refuses while an API alarm is firing, and the requests fail precisely because the
first migration has not run. `Rollback` has nothing to go back to. The break-glass is
`override_alarms` with a reason: the run is named `ALARMS OVERRIDDEN`, the reviewer decides,
and that one deployment is unwatched.

**Q6. A `bug`-labelled PR changes only `assignment_service.py`. What happens?**
The `regression-guard` check fails: a bug fix must change a test (for backend tests, one
marked `@pytest.mark.regression` that fails on the old code), and `regression-guard` is
required.

**Quiz.** A pull request with the title "Fixed the thing" merges with every required check
green. What is missing from the next release, and why did nothing stop it?
→ The change is missing from the changelog and does not move the version: release-please
reads the squash subject, which is the title, and a non-conventional subject is invisible to
it. `conventional-title` reported the problem but is not a required check.

---

## Exercises

**E4.1 — Required checks audit.** Using
`REQUIRED_CHECKS` in `scripts/configure-repo.sh` (and, if you can read the setting,
`gh api repos/Controlvar/controlvar/branches/main/protection/required_status_checks`),
list the required contexts and match each to the workflow and job that reports it. Name the
jobs that run on a pull request but are not required, and argue for or against requiring
each.

**E4.2 — Why bootstrap.** The migration task runs `python -m backend.app.db.bootstrap`,
while `db-migrate.yml` runs raw `alembic`. From `migration_task_stack.py` and
`db-migrate.yml`, explain why each workflow uses the command it does, and what goes wrong if
you swap them.

**E4.3 — The approval.** `Deploy` asks the environment's reviewer once. Explain which job
holds `environment:`, why the preflight job holds no AWS credential, and what the reviewer
should read on the approval screen before approving (hint: the run name).

**E4.4 — A read-only incident script.** Write a bash script that, given an environment
name, prints the API's PRIMARY task-set revision, any CodeDeploy deployments not yet final
for the group, the state of both 5xx alarms, and the `gh workflow run Rollback …` line to
run — and changes nothing.

**E4.5 — Names under test.** Read `infrastructure/tests/test_workflow_names_exist.py`. Which
kinds of name does it check, against what, and what would happen to a pull request that
renamed the dashboard service in the CDK but not in `rollback.yml`?

---

## Solutions

**S4.1** — the 20 contexts map as in Hour 10's table: eleven from the PR QA Gate
(`core-build` and `full-build`, plain jobs that always run, and `SDK Live Contract /
sdk-live-contract (core)` from its reusable workflow), one each from Release Gate, Security Scan, Integration Tests,
SDK Unit Tests, Infrastructure Tests, Lint, Regression Guard, DCO and Leak Guard. Running but
not required: `conventional-title` (the strongest case for requiring it — a prose title
silently drops a change out of the release), `Dependency Review`, CodeQL, Doc Examples and
`contributor-guard` (it always runs, so it could be required without blocking anything; one
argument against is that only a maintainer merges a fork's pull request anyway, and the guard's
red check is already in front of them). The path-filtered ones — Ground-truth
Replay, Demo Apps, the installer on macOS, the scale smoke — cannot be required without blocking
every pull request that does not touch their paths. What the live repository enforces is a
setting, not a file, which is why the audit has a command as well as a script (and why
`scripts/check_required_checks.sh` exists: it compares the two).

**S4.2** — the migration task meets every state including an **empty** database (an
environment's first deploy), and the historical migration chain cannot be replayed from
empty; `bootstrap` creates the schema from the models and stamps both heads there, and runs
`alembic upgrade heads` on an existing database. `db-migrate.yml` performs **targeted**
operations — `current`, then `upgrade`/`downgrade <target>` — on a database that already
exists, and `bootstrap` cannot express a target. Swapped: a first deploy's raw `upgrade
heads` dies a couple of revisions in on an existing-table error; a manual downgrade through
`bootstrap` cannot downgrade at all.

**S4.3** — the `deploy` job is the only one with `environment: ${{ inputs.environment }}`,
so there is one approval per deploy, given before anything in AWS changes. Preflight holds no
AWS role so that a wrong dispatch (not from `main`, not a release tag, gate not passed, core
not acknowledged) is refused before a reviewer is asked and before any credential exists.
The reviewer should read the run name — `Deploy <tag> (<profile>) to <env>`, ending in
`-- ALARMS OVERRIDDEN` when the break-glass was ticked — and the preflight summary (the
profile, and the override reason).

**S4.4**

```bash
#!/usr/bin/env bash
# Read-only: describes, never changes.
set -euo pipefail
ENV=${1:?usage: $0 staging|prod}
primary=$(aws ecs describe-services --cluster "experimentation-$ENV" \
  --services "experimentation-backend-$ENV" \
  --query "services[0].taskSets[?status=='PRIMARY'].taskDefinition | [0]" --output text)
active=$(aws deploy list-deployments --application-name "experimentation-platform-$ENV" \
  --deployment-group-name "experimentation-$ENV" \
  --include-only-statuses Created Queued InProgress Baking Ready --query 'deployments' --output text)
alarms=$(aws cloudwatch describe-alarms \
  --alarm-names "experimentation-api-5xx-blue-$ENV" "experimentation-api-5xx-green-$ENV" \
  --query 'MetricAlarms[].[AlarmName,StateValue]' --output text)
echo "serving (PRIMARY): ${primary##*/}"
echo "deployments not final: ${active:-none}"
echo "alarms:"; echo "$alarms"
echo "rollback: gh workflow run Rollback -f environment=$ENV -f task_definition_arn=<family:revision from the deploy summary> -f reason='...'"
```

It reads the PRIMARY task set, not `services[0].taskDefinition` (frozen on a CodeDeploy
service), and lists deployments with the same non-final statuses `Rollback` uses. If one of
them was created by `codeDeployRollback`, the right action is to wait, not to dispatch.

**S4.5** — it renders every AWS name `deploy.yml`, `rollback.yml` and `db-migrate.yml`
build from the `environment` input, for staging and prod, and looks each up in a real synth
of that environment: a physical name must be on a resource of the right type, a stack must be
one the app creates, and a stack output a workflow reads must be one that stack publishes; an
environment value the test does not classify fails. A pull request that renamed the dashboard
service in the CDK alone fails `CDK Stack Tests (Python)` — a required check — so the
mismatch never reaches an incident.

---

_You've finished Platform Operations. The other two specializations are
[Experimentation Statistics](../experimentation-statistics/README.md) and
[SDK Engineering](../sdk-engineering/README.md); the core path is in the [root README](../README.md);
to contribute to Controlvar itself, start with its
[CONTRIBUTING.md](https://github.com/Controlvar/controlvar/blob/09d815ea18273982d710fb444ba76f05a18bc5c6/CONTRIBUTING.md)._ 🛠️

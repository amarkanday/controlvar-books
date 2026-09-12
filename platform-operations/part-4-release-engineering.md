# Part IV — Release Engineering

### Hours 10–12

[← Part III](./part-3-observability.md) · [Contents](./README.md)

How a change becomes production, and how production becomes the previous change when it
has to. Hour 10 is CI: what runs on every pull request, what runs nightly, what's a required
check and what's advisory, and why there are two gates rather than one. Hour 11 is the
production deploy: a tag, a verified release gate, a snapshot, a one-shot migration task, a
new task-definition revision, a CodeDeploy canary, and smoke tests. Hour 12 is the other
direction: the rollback decision tree, the three rollback methods, the database rollback
procedure, secrets and their rotation, and the drills that keep all of it real.

---

## Hour 10 — CI: the PR QA gate, the release gate, security scans, and what "required" means

### What & why

Continuous integration is where "it works on my machine" is replaced with a list of things
that must be true before a change can merge. The platform runs several workflows on a pull
request, and the important distinction is between **required** checks — branch protection
blocks the merge until they pass — and **advisory** ones, which run and report but don't
block. The required set is small and fast on purpose: unit tests, smoke wiring tests, the
frontend suite and build, the offline SDK contract, the live SDK contract, the Docker smoke,
and the release gate. Integration tests, performance, Cognito and the nightly suite run on a
schedule or on demand, where a slow or flaky job costs nobody a merge.

Two gates exist because they answer different questions. The **PR QA Gate** asks "does the
code work?" — tests, contracts, containers. The **Release Gate** asks "is this commit
releasable?" — a deterministic backend run with pinned test dependencies from
`docker-compose.test.yml`, the frontend build, Bandit and gitleaks — and produces the one
check-run name (`Release Gate Summary`) that the production deploy workflow looks up before
it will deploy a tag (Hour 11).

### How Experimently does it

The PR gate's shape — six jobs, each with its own services and its own reason to exist:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
name: PR QA Gate

on:
  pull_request:
    branches: [main]

concurrency:
  group: pr-qa-${{ github.head_ref }}
  cancel-in-progress: true

jobs:
  unit-tests:          # Postgres service; backend/tests/unit with -x; plus the Lambda suites
  smoke-tests:         # backend/tests/smoke — the wiring checks (routes exist, deps resolve)
  frontend-tests:      # Node 22; jest; next build
  sdk-contracts:       # golden vectors, Python + JS, offline
  sdk-live-contract:   # Postgres + Redis + backend + seed; every SDK's contract_smoke (SDK book, Hour 11)
  docker-smoke:        # build both images; boot compose; walk the golden path through the containers
```

`concurrency` with `cancel-in-progress` means a new push to the branch cancels the previous
run — no queue of stale results. The unit job runs the Lambda suites too, with a comment
that explains a past gap:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
      - name: Run unit tests
        env:
          APP_ENV: test
          TESTING: 'true'
          ...
        run: python -m pytest backend/tests/unit/ -p no:cov -q --tb=short -x

      # The Lambda functions (assignment, event processor, flag evaluation) carry
      # 301 tests of their own that ran nowhere in CI. Each function's tests/ dir
      # must run in its own pytest invocation (they all define `tests.conftest`).
      - name: Run Lambda tests
        run: |
          for d in backend/lambda/assignment backend/lambda/event_processor backend/lambda/feature_flag_evaluation backend/lambda/shared; do
            python -m pytest "$d" -p no:cov -q --tb=short || exit 1
          done
```

`-x` (stop at first failure) in the PR gate is a speed choice: a red PR gate needs one
failure to act on, not the full list; the nightly runs without it. `-p no:cov` disables the
coverage plugin, which the memory file notes "silently produces no report" with the repo's
`addopts` — coverage is a separate, explicit run.

The Docker smoke is the job that most resembles a customer — it builds both images and
walks the same path a self-hoster would:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
  # Builds both images and boots the compose stack exactly the way a
  # self-hoster does, then walks the golden path through the real containers:
  # readiness -> login -> user API key -> SDK assignment -> dashboard pages
  # (including a dynamic route served by nginx from the static export).
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
pipe as soon as it matches, `curl` gets `SIGPIPE`, exits 23, and `set -o pipefail` fails
the step on a success. Fetch to a file, then grep.

The release gate — deterministic dependencies from a dedicated compose file, run through
the same script a developer uses locally, plus the security checks, summarised into one
named check:

```yaml
# 📁 .github/workflows/release-gate.yml
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
        run: |
          if [ "${{ needs.backend-gate.result }}" != "success" ] || \
             [ "${{ needs.frontend-gate.result }}" != "success" ] || \
             [ "${{ needs.security-gate.result }}" != "success" ]; then
            echo "::error::Release gate failed"
            exit 1
          fi
          echo "Release gate passed"
```

`if: always()` on the summary is what makes it a reliable signal: it runs even when a gate
fails, so there's always a `Release Gate Summary` check-run with a conclusion — which is
what the deploy workflow queries by name. The test runner script:

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
the same file CI uses is the one you use with `EP_TEST_PROFILE=compose` locally, which is
the point: "deterministic" means the same Postgres 15 image and the same empty database in
both places.

**Security scans** run on push and on a schedule, and a subset is in the release gate:

```yaml
# 📁 .github/workflows/security-scan.yml
  python-security:     # bandit -r backend/app -ll (medium+, fail on high); pip-audit (informational)
  frontend-security:   # npm audit --audit-level=high
  semgrep:             # semgrep scan (SAST) → report artifact
  secret-detection:    # gitleaks over full history (needs pull-requests: read on PR events)
  container-security:  # trivy filesystem scan
```

```yaml
# 📁 .github/workflows/security-scan.yml
  secret-detection:
    name: Secret Detection
    runs-on: ubuntu-latest
    # gitleaks-action lists the PR's commits via the API on pull_request
    # events; the default token has no pull-requests scope, so grant it.
    permissions:
      contents: read
      pull-requests: read
```

(That permissions block is a fix from the "how CI got green" history in the memory file —
gitleaks was failing on PRs not because of secrets but because it couldn't list commits.)
The **Regression Guard** is the one process rule enforced by CI: a PR labelled `bug` must
touch a test, unless labelled `skip-regression-guard` with an explanation:

```yaml
# 📁 .github/workflows/regression-guard.yml
      - name: Require a test with every bug fix
        ...
          if ! is_labelled bug; then
            echo "Not labelled \`bug\` — nothing to enforce."
            exit 0
          fi

          if is_labelled skip-regression-guard; then
            echo "Labelled \`skip-regression-guard\` — the author has explained why in the description."
```

And the **nightly** suite (`02:00 UTC`) runs what's too slow or too environment-dependent
for a PR: the full integration suite, the smoke tests against a booted stack, the SDK
contracts again, the E2E/visual/a11y Playwright suites from the QA system.

| Workflow | Trigger | Required? | What it proves |
|---|---|---|---|
| PR QA Gate (6 jobs) | PR to main | yes | the code works, the containers boot, the SDKs agree |
| Release Gate (+ Summary) | PR to main, dispatch | yes | this commit is releasable; the name deploy checks |
| Lint | push, PR | yes | ruff, format, eslint, tsc |
| Regression Guard | PR | yes | bug fixes carry a test |
| Security Scan | push, schedule | advisory (Bandit/gitleaks also in the release gate) | no high-severity findings, no secrets |
| Nightly QA | 02:00 UTC | — | integration, E2E, contracts on a full stack |
| Integration / Performance / Cognito | dispatch, schedule | — | slow or environment-bound suites |

> **Watch Out!** "Required" is a branch-protection setting, not a property of the workflow
> file. The memory file records that CI went green and "branch protection requires the
> always-on gates" — if a new job is added to the PR gate, it isn't required until someone
> adds it in the repository settings. The `Release Gate Summary` check is the one whose name
> must never change: the deploy workflow greps for it.

> **Did You Know?** The Docker smoke job builds both images from scratch on every PR —
> which is slow (the 25-minute timeout is real) but is the only check that would catch a
> broken `Dockerfile`, a missing `docs/` copy, or a nginx template regression before a
> self-hoster does. It exists because those exact things broke once each.

### Try It Yourself

```bash
gh pr checks <number>                                   # the check list with required/advisory status
gh api repos/amarkanday/experimentation-platform/branches/main/protection --jq '.required_status_checks.contexts'
EP_TEST_PROFILE=compose ./scripts/run-backend-tests.sh release    # exactly what the release gate runs
gh run list --workflow "Release Gate" --limit 3
gh api "repos/amarkanday/experimentation-platform/commits/$(git rev-parse main)/check-runs" --jq '.check_runs[] | select(.name=="Release Gate Summary") | .conclusion'
```

Then open a PR labelled `bug` with no test change and watch the Regression Guard fail; add
the label `skip-regression-guard` with a reason and watch it pass.

### Key takeaways

- PR QA Gate: unit (+ Lambda suites), smoke wiring, frontend, offline SDK contract, live SDK
  contract, Docker smoke — fast, `-x`, cancel-in-progress. Release Gate: deterministic
  backend run via `run-backend-tests.sh release` on `docker-compose.test.yml`, frontend
  build, Bandit + gitleaks → one `Release Gate Summary` check (`if: always()`).
- Security scans (Bandit, pip-audit, npm audit, Semgrep, gitleaks, Trivy) on push and
  schedule; Regression Guard enforces a test per bug fix; nightly runs the slow suites.
- "Required" lives in branch protection; the summary check's name is load-bearing.

---

## Hour 11 — Deploying to production: tags, the release-gate check, the migration task, the canary

### What & why

A production deploy is a `workflow_dispatch` with one input: a git tag. Everything after
that is a chain of checks and irreversible-looking steps made reversible: verify the tag
exists and its commit passed the release gate; run the tests again; build and push
SHA-tagged images; snapshot the database; run migrations as a **one-shot ECS task** and
wait for its exit code; register a new task-definition revision with the new image; create
a CodeDeploy blue/green deployment and wait for the canary; run smoke tests against
production; notify. Each step is a place to stop, and the order is chosen so that a stop
leaves production in a known state.

Two design decisions are worth understanding before the YAML. **Migrations run before the
code, separately.** The new task set must not start against an old schema, and replicas
must not race each other to migrate (Part I, Hour 3) — so the migration is its own Fargate
task, run once, with a snapshot taken first. **The deploy is a task-definition revision, not
a `cdk deploy`.** The workflow reads the current task definition, swaps the image for the
SHA-tagged one, registers a new revision, and hands _that_ to CodeDeploy — no CloudFormation
in the loop, so a deploy takes minutes and a rollback is "point at the previous revision."

### How Experimently does it

The gates before anything happens — the tag must exist, and the release gate must have
passed on exactly that commit:

```yaml
# 📁 .github/workflows/deploy-prod.yml
name: Deploy to Production

on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Git tag or commit SHA to deploy (e.g. v1.2.3)'
        ...
      skip_smoke_tests:
        description: 'Skip smoke tests (emergency deploys only)'
...
  pre-deployment-checks:
    name: Pre-deployment Checks
    ...
      - name: Validate deploy target is an existing git tag
        run: |
          git fetch --tags --force
          git rev-parse "refs/tags/${{ inputs.version }}" >/dev/null

      - name: Verify release gate passed for target commit
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          TARGET_SHA=$(git rev-parse HEAD)
          RELEASE_GATE_RESULT=$(gh api "repos/${{ github.repository }}/commits/${TARGET_SHA}/check-runs" \
            --jq '.check_runs[] | select(.name=="Release Gate Summary") | .conclusion' || true)

          if [ "${RELEASE_GATE_RESULT}" != "success" ]; then
            echo "::error::Release Gate Summary is not successful for ${TARGET_SHA}. Result='${RELEASE_GATE_RESULT}'"
            exit 1
          fi
          echo "Release gate verified for ${TARGET_SHA}"
```

Then: notify `#deployments`, check for active incidents, confirm a recent backup exists.
The job graph:

```text
pre-deployment-checks → run-tests → build-and-push → run-migrations → deploy → smoke-tests → post-deployment
                                                   ↘ (deploy needs both build-and-push and run-migrations)
```

The migration job — snapshot first, then the one-shot task, then _wait for and read the
exit code_:

```yaml
# 📁 .github/workflows/deploy-prod.yml
  run-migrations:
    name: Run Database Migrations
    needs: build-and-push
    environment: production
    ...
      - name: Take pre-migration DB snapshot
        run: |
          SNAPSHOT_ID="pre-migration-$(date +%Y%m%d-%H%M%S)"
          aws rds create-db-cluster-snapshot \
            --db-cluster-identifier experimentation-prod \
            --db-cluster-snapshot-identifier $SNAPSHOT_ID
          echo "SNAPSHOT_ID=$SNAPSHOT_ID" >> $GITHUB_ENV

      - name: Run migrations via ECS task
        run: |
          # Run migration as a one-off ECS task
          TASK_ARN=$(aws ecs run-task \
            --cluster $ECS_CLUSTER \
            --task-definition experimentation-migrate \
            --launch-type FARGATE \
            --network-configuration "awsvpcConfiguration={subnets=[${{ secrets.PROD_PRIVATE_SUBNET_IDS }}],securityGroups=[${{ secrets.PROD_ECS_SG_ID }}],assignPublicIp=DISABLED}" \
            --overrides '{"containerOverrides":[{"name":"backend","command":["python","-m","alembic","-c","app/db/alembic.ini","upgrade","head"]}]}' \
            --query 'tasks[0].taskArn' \
            --output text)

          # Wait for completion
          aws ecs wait tasks-stopped --cluster $ECS_CLUSTER --tasks $TASK_ARN

          # Check exit code
          EXIT_CODE=$(aws ecs describe-tasks \
            --cluster $ECS_CLUSTER \
            --tasks $TASK_ARN \
            --query 'tasks[0].containers[0].exitCode' \
            --output text)

          if [ "$EXIT_CODE" != "0" ]; then
            echo "Migration failed with exit code $EXIT_CODE"
            echo "Pre-migration snapshot available: $SNAPSHOT_ID"
            exit 1
          fi
```

`environment: production` on this job is a GitHub Environment — it can require reviewers,
and it scopes the production secrets so no earlier job can read them. The task definition
it runs comes from the migration stack (Part II, Hour 5) — the same API image with the
command overridden:

```python
# 📁 infrastructure/cdk/stacks/migration_task_stack.py
    """
    One-shot ECS task definition for Alembic database migrations.

    This stack does NOT create a long-running ECS service. Instead it
    registers a Fargate task definition that can be invoked on-demand
    (e.g. from a CI/CD pipeline or a CodePipeline action) to run:

        python -m alembic -c app/db/alembic.ini upgrade head
    ...
    The task exits (succeeds or fails) after the migration completes, making
    it easy to detect failures and gate subsequent deployment steps.
    """
```

Note that the task's command path is `app/db/alembic.ini`, the pre-refactor form; the
image's working directory is `/app` and the entrypoint `cd`s there, and the Makefile/README
form is now `python -m alembic -c backend/app/db/alembic.ini` from the repo root. Both
resolve inside the image because the entrypoint runs the command from `/app` where
`backend/app/db/alembic.ini` exists — but `app/db/alembic.ini` does _not_ exist under `/app`.
This is a drift to verify before the next production migration (Exercise E4.2): the
`--overrides` command in the workflow should match the layout the image actually has.

The deploy itself — register a revision, create the deployment, wait for the canary:

```yaml
# 📁 .github/workflows/deploy-prod.yml
      - name: Update ECS task definition with new image
        id: task-def
        run: |
          # Get current task definition
          TASK_DEF=$(aws ecs describe-task-definition --task-definition experimentation-backend-prod --query taskDefinition)

          # Update image in task definition
          NEW_TASK_DEF=$(echo $TASK_DEF | python3 -c "
          import json, sys
          td = json.load(sys.stdin)
          for c in td['containerDefinitions']:
              if c['name'] == 'backend':
                  c['image'] = '${{ needs.build-and-push.outputs.backend-image }}'
          # Remove fields that can't be re-registered
          for key in ['taskDefinitionArn','revision','status','requiresAttributes',
                      'compatibilities','registeredAt','registeredBy']:
              td.pop(key, None)
          print(json.dumps(td))
          ")

          # Register new task definition
          NEW_TASK_DEF_ARN=$(aws ecs register-task-definition --cli-input-json "$NEW_TASK_DEF" --query 'taskDefinition.taskDefinitionArn' --output text)

      - name: Create CodeDeploy deployment (Blue/Green)
        id: codedeploy
        run: |
          DEPLOYMENT_ID=$(aws deploy create-deployment \
            --application-name experimentation-platform \
            --deployment-group-name experimentation-prod \
            --revision '{"revisionType":"AppSpecContent","appSpecContent":{"content":"{\"version\":1,\"Resources\":[{\"TargetService\":{\"Type\":\"AWS::ECS::Service\",\"Properties\":{\"TaskDefinition\":\"${{ steps.task-def.outputs.new-task-def }}\",\"LoadBalancerInfo\":{\"ContainerName\":\"backend\",\"ContainerPort\":8000}}}}]}"}}' \
            --query 'deploymentId' --output text)

      - name: Wait for deployment (10% traffic bake)
        run: |
          # Wait up to 20 minutes for deployment to complete
          timeout 1200 aws deploy wait deployment-successful \
            --deployment-id ${{ steps.codedeploy.outputs.deployment-id }} || {
            echo "Deployment timed out or failed - triggering rollback"
            aws deploy stop-deployment \
              --deployment-id ${{ steps.codedeploy.outputs.deployment-id }} \
              --auto-rollback-enabled
            exit 1
          }
```

The AppSpec is the whole contract with CodeDeploy: which task definition, which container
and port to wire to the target group. CodeDeploy does the rest per the deployment group
(Part II, Hour 5): green task set, test listener, 10% for 5 minutes, then 100%, blue kept an
hour. If the wait fails, `stop-deployment --auto-rollback-enabled` puts traffic back on blue
— a rollback that costs seconds because blue never left.

Smoke tests run against the live production URL with a dedicated smoke API key and token,
and can be skipped only by an explicit input:

```yaml
# 📁 .github/workflows/deploy-prod.yml
  smoke-tests:
    name: Post-deployment Smoke Tests
    needs: deploy
    if: github.repository == 'amarkanday/experimentation-platform' && vars.AWS_ACCOUNT_ID != '' && (!inputs.skip_smoke_tests)
    ...
      - name: Run smoke tests
        env:
          SMOKE_TEST_API_URL: ${{ secrets.PROD_API_URL }}
          SMOKE_TEST_API_KEY: ${{ secrets.PROD_SMOKE_TEST_API_KEY }}
          SMOKE_TEST_TOKEN: ${{ secrets.PROD_SMOKE_TEST_TOKEN }}
        run: |
          python -m pytest backend/tests/smoke/ -v --tb=short
```

(`backend/tests/smoke/test_production.py` is the file that "expects 7 failures offline" in
the memory notes — it targets a live server; in the PR gate only the wiring smoke tests run.)

> **Watch Out!** The `if:` on smoke tests includes `vars.AWS_ACCOUNT_ID != ''` — a fork or
> a repo without the variable skips them silently rather than failing. That's right for
> forks and wrong for a misconfigured production repo. If a deploy reports success suspiciously
> fast, check that the smoke job _ran_, not just that nothing failed.

> **By the Way** — `deployment_approval_wait_time=Duration.minutes(30)` in the CDK means a
> deployment can sit at "ready for traffic shift" waiting for a manual approve; the
> workflow's `aws deploy wait deployment-successful` with a 20-minute timeout would time out
> first and roll back. The current deployment config auto-continues (canary, no manual
> approval step in the AppSpec), so this doesn't bite — but if you add a manual approval,
> raise the timeout or the workflow will roll back every deploy.

### Try It Yourself

Without an AWS account, read the workflow end to end and answer, from the YAML alone: which
job holds the production secrets; what happens if `run-tests` fails (nothing is built);
what happens if the migration task exits 1 (the deploy job never starts, and the snapshot ID
is in the log); what happens if smoke tests fail (post-deployment reports it — the traffic
has already shifted, so this is a rollback decision, Hour 12). Then:

```bash
gh workflow view "Deploy to Production"
gh run list --workflow "Deploy to Production" --limit 5
git tag --list 'v*' | tail -3
```

With an account: `aws ecs list-task-definitions --family-prefix experimentation-backend-prod
--sort DESC --max-results 3` and `aws deploy list-deployments --application-name
experimentation-platform --deployment-group-name experimentation-prod` to see the revision
and deployment history the workflow produced.

### Key takeaways

- Deploy = dispatch with a tag; the tag must exist and its commit must have a successful
  `Release Gate Summary`.
- Order: tests → SHA-tagged images → RDS snapshot → one-shot migration task (wait, read
  exit code) → register a task-definition revision → CodeDeploy blue/green → smoke tests.
- The migration task is the same image with the command overridden; verify its alembic
  path matches the image layout.
- A failed canary wait runs `stop-deployment --auto-rollback-enabled`; blue is still there.

---

## Hour 12 — Rollback, secrets and drills: the decision tree, the two rollback methods, `/prod/experimentation/*`

### What & why

A deploy that can't be undone in minutes is a deploy you'll be afraid to do. The rollback
runbook's first page is a **decision tree** with a two-minute budget, and its first rule is
"when in doubt, roll back" — hotfixes are for one-line changes only. Three methods exist
because they trade speed against auditability: the GitHub Actions workflow (~3 minutes,
logged, runs verification), the AWS CLI directly (~2 minutes, when Actions is down or you
need to be faster), and CodeDeploy's automatic rollback (already happened if the canary
failed). Database rollbacks are their own section, because an application rollback against
a migrated schema can be worse than the bug.

Secrets are the other half of this Hour because rotation is a deploy too: the three
application secrets live in Secrets Manager under `/prod/experimentation/`, ECS injects them
at task start, and rotating one means a forced new deployment. The **operational drills**
document is what keeps all of this from being theory: an SLO-alert drill and a database
restore drill with pass criteria.

### How Experimently does it

The decision tree, with the thresholds that need no deliberation:

```text
# 📁 docs/deployment/rollback-runbook.md
Post-deployment issue detected
          |
          v
Is any of the following true?
  - Health check /health returning non-200
  - 5xx error rate > 5% (vs. pre-deploy baseline)
  - p99 API latency > 5000ms
  - Data corruption detected (missing rows, wrong values)
  - ECS tasks crash-looping (stopped reason in logs)
          |
    NO ---+--- YES
          |         |
          |         v
          |   Is there a workaround (feature flag at 0%)?
          |         |
          |   YES --+-- NO ──→ ROLLBACK IMMEDIATELY (go to Method 1)
          ...
          |   Is it a database schema issue?
          |         |
          |   YES ──→ ROLLBACK APPLICATION + DATABASE (Method 1 + DB Rollback section)
          |   NO  ──→ HOTFIX (deploy new tag following standard procedure)
```

"Is there a workaround (feature flag at 0%)?" is the platform eating its own cooking: a
behaviour behind an Experimently flag can be turned off in seconds without a deploy, and the
safety monitor may already have done it.

**Method 1** — find the previous task definition, dispatch the rollback workflow:

```bash
# 📁 docs/deployment/rollback-runbook.md
# Option A: List recent task definitions for the family (newest first)
# The current bad version is first; you want the one directly below it.
aws ecs list-task-definitions \
  --family-prefix experimentation-backend-prod \
  --sort DESC \
  --max-results 5 \
  --query 'taskDefinitionArns'
```

```yaml
# 📁 .github/workflows/rollback.yml
name: Rollback Production
on:
  workflow_dispatch:
    inputs:
      task_definition_arn:
        description: 'Previous ECS task definition ARN to rollback to (e.g. arn:aws:ecs:us-west-2:123:task-definition/backend:42)'
      reason:
        description: 'Reason for rollback'
...
      - name: Update ECS service to previous task definition
        run: |
          aws ecs update-service \
            --cluster experimentation-prod \
            --service experimentation-backend-prod \
            --task-definition "${{ inputs.task_definition_arn }}" \
            --force-new-deployment

      - name: Wait for rollback to stabilize
        run: |
          aws ecs wait services-stable --cluster experimentation-prod --services experimentation-backend-prod

      - name: Verify health after rollback
        run: |
          RUNNING=$(aws ecs describe-services ... --query 'services[0].runningCount' --output text)
      - name: Notify rollback result
        uses: slackapi/slack-github-action@v1.26.0
```

The `reason` input is the audit trail — it ends up in the Slack message and the run log, so
the post-incident review can reconstruct the decision. **Method 2** is the same two AWS
calls by hand, for when Actions itself is the outage; the runbook asks for a follow-up note
in `#deployments` so the audit log is complete. **Method 3** is CodeDeploy having already
rolled back a failed canary — check the deployment's status before doing anything else.

The **database rollback** section is the careful one: confirm the migration is the root
cause (compare `alembic current` with the previous tag), run a downgrade through the
`Database Migration` workflow — which takes a snapshot first and warns loudly on a
production downgrade — and only as a last resort restore the pre-migration snapshot:

```yaml
# 📁 .github/workflows/db-migrate.yml
      - name: Require approval for production downgrade
        if: inputs.environment == 'production' && inputs.direction == 'downgrade'
        run: |
          echo "::warning::PRODUCTION DOWNGRADE requested by ${{ github.actor }}"
          echo "Proceeding with production downgrade to revision: ${{ inputs.target }}"

      - name: Take pre-migration snapshot
        run: |
          DB_CLUSTER="experimentation-${{ inputs.environment }}"
          SNAPSHOT_ID="pre-migration-$(date +%Y%m%d-%H%M%S)"
          aws rds create-db-cluster-snapshot --db-cluster-identifier $DB_CLUSTER --db-cluster-snapshot-identifier $SNAPSHOT_ID
```

The rule the migration guidelines in `CLAUDE.md` and the Build book teach — every migration
must have a working `downgrade()` — is what makes this path exist at all.

**Secrets.** Three secrets, created before the first deploy, injected by ECS, rotated on a
schedule:

```text
# 📁 docs/deployment/secrets-management.md
| Secret | Rotation Frequency | Method | Requires Restart |
|--------|-------------------|--------|-----------------|
| `/prod/experimentation/db-password` | 180 days | Secrets Manager Lambda rotation (dual-user strategy) | No (connection pool reconnects) |
| `/prod/experimentation/jwt-secret` | 90 days | Manual rotation (see below) | Yes (force ECS restart) |
| `/prod/experimentation/redis-url` (auth token) | 365 days | Manual (ElastiCache token rotation) | Yes |
| `/prod/experimentation/cognito-config` | On Cognito pool change | Manual update | Yes |
| GitHub Actions deployment secrets | 365 days | Manual (GitHub Settings) | N/A |
```

```bash
# 📁 docs/deployment/secrets-management.md
### Rotate JWT Secret Immediately
# Rotating the JWT secret invalidates all active sessions immediately.
NEW_JWT_SECRET=$(openssl rand -base64 48)
aws secretsmanager put-secret-value --secret-id /prod/experimentation/jwt-secret --secret-string "$NEW_JWT_SECRET"
# Force ECS service to restart and pick up the new secret
# (New tasks fetch the new secret value at startup)
aws ecs update-service --cluster experimentation-prod --service experimentation-backend-prod --force-new-deployment
aws ecs wait services-stable --cluster experimentation-prod --services experimentation-backend-prod
```

"Requires restart: Yes" follows from how injection works — ECS resolves `secrets=` once, at
task start (Part II, Hour 5). The JWT secret is the one that logs everyone out, which is
the intended effect of an emergency rotation and a thing to schedule for a routine one.
Application-level API keys (`eptk_…`) are rotated in the dashboard, not in Secrets Manager;
they're hashed at rest (SDK book, Hour 1) so a database read doesn't expose them.

**Drills** — the two in `operational-drills.md`:

- **Drill 1, SLO alert validation**: for each alarm, inject the fault from a catalogue
  (kill tasks, saturate CPU, block the database), record time-to-alarm and time-to-page,
  pass if every alarm fires within its SLA. The alarms from Part III Hour 9's Exercise E3.4
  are the ones this drill would validate.
- **Drill 2, database restore**: restore the latest snapshot to a fresh cluster, run the
  consistency probes (`consistency-probes.sql`: row counts, assignment ↔ event integrity,
  the latest `alembic_version`), pass if the restore completes within the RTO and the probes
  match.

> **Watch Out!** The runbook's Method 1 and 2 examples reference the service as
> `experimentation-api-prod` in places and `experimentation-backend-prod` in others; the
> rollback workflow and the CDK stack use `experimentation-backend-prod`. A copy-pasted
> command against the wrong service name fails fast with "service not found" — but at 2 a.m.
> that's thirty seconds you don't have. Fix the doc (Exercise E4.5) and, more generally,
> read the workflow, not the prose, for names.

> **Did You Know?** The rollback workflow uses `aws ecs update-service --task-definition
> <previous>`, which — on a service whose deployment controller is CodeDeploy — is _not_ the
> primary path; CodeDeploy-managed services expect a new deployment. In practice the fastest
> guaranteed rollback within the hour after a deploy is CodeDeploy's own: the blue task set
> is still registered, and `aws deploy stop-deployment --auto-rollback-enabled` or a
> `create-deployment` pointing at the previous revision shifts traffic back in seconds.
> Reconciling the runbook with the CodeDeploy controller is a real follow-up.

### Try It Yourself

A tabletop drill, no account required. Take the scenario "deploy of v1.4.0 completed at
14:00; at 14:06 the ALB 5xx rate is 8% and `/health` is 200":

1. Walk the decision tree — which branch, which method?
2. Write the exact `aws ecs list-task-definitions` call and pick the ARN.
3. Write the `gh workflow run "Rollback Production" -f task_definition_arn=… -f
   reason="…"` invocation.
4. List the post-rollback checklist items you'd complete in the first 5 minutes.
5. Decide whether a database rollback is needed (hint: was there a migration in v1.4.0? —
   `git diff v1.3.0..v1.4.0 --stat -- backend/app/db/migrations/versions/`).

Then time yourself against the runbook's "< 5 minutes from decision to rollback complete."

### Key takeaways

- Decision tree with hard triggers (`/health` non-200, 5xx > 5%, p99 > 5 s, corruption,
  crash loops); flag-at-0% as the first workaround; hotfix only for trivial changes.
- Method 1 (workflow, audited, verified), Method 2 (CLI, faster, note afterwards), Method 3
  (CodeDeploy already did it); database rollback via the migration workflow's downgrade
  with a snapshot, restore as last resort.
- Secrets under `/prod/experimentation/*`, injected at task start, rotated on schedule;
  JWT rotation = forced deployment = everyone logged out.
- Drills validate alarms and restores; reconcile the runbook's names with the workflows.

---

## Part IV Workshop

**Q1. Why two gates, and which one does the deploy check?**
PR QA Gate proves the code works (tests, contracts, containers); Release Gate proves the
commit is releasable under deterministic dependencies plus security checks and produces the
single `Release Gate Summary` check-run. The deploy verifies that one, by name, on the tag's
commit.

**Q2. The migration task exits 1. What state is production in?**
Old code still serving (deploy never started), schema possibly partially migrated inside a
failed Alembic transaction (Postgres DDL is transactional, so usually not), and a
pre-migration snapshot named in the log. Investigate; restore only if the schema is
actually inconsistent.

**Q3. Why does the deploy register a task-definition revision instead of `cdk deploy`?**
Speed and reversibility: a revision is one API call and CodeDeploy does the traffic shift;
rollback is pointing at the previous revision. CloudFormation would take minutes and would
try to reconcile the image to `latest` (Part II, Hour 5).

**Q4. What does `stop-deployment --auto-rollback-enabled` rely on, and how long is it
available?**
The blue task set still being registered — CodeDeploy keeps it for an hour after success
(`terminate_blue_instances_on_deployment_success`), and during the deployment itself.

**Q5. After rotating the JWT secret, why must ECS be force-redeployed, and what do users
see?**
ECS resolves `secrets=` at task start; running tasks keep the old value. New tasks sign and
verify with the new secret; every existing session's token fails verification — users are
logged out.

**Q6. A `bug`-labelled PR changes only `assignment_service.py`. What happens?**
Regression Guard fails: a bug fix must touch a test, unless the author adds
`skip-regression-guard` and explains why in the description.

**Quiz.** The deploy workflow's smoke-test job was skipped, and the deployment says
success. Two possible reasons?
→ `skip_smoke_tests=true` was passed (emergency deploys only), or `vars.AWS_ACCOUNT_ID` is
empty in the repository — check the run's job list before trusting the green.

---

## Exercises

**E4.1 — Required checks audit.** Using `gh api .../branches/main/protection`, list the
required contexts and compare with the jobs in the PR gate, lint and release-gate
workflows; name any job that runs but isn't required, and argue whether it should be.

**E4.2 — Migration command drift.** Verify whether `python -m alembic -c
app/db/alembic.ini upgrade head` resolves inside the API image (`docker run --rm -e
WAIT_FOR_DB=false -e RUN_MIGRATIONS=false experimently-api:ce ls app/db/alembic.ini`); if
not, write the corrected `--overrides` for the deploy workflow and the migration stack's
`command=`, and explain why the entrypoint's own bootstrap doesn't have this problem.

**E4.3 — Approval gate.** Add a manual approval before `run-migrations` using a GitHub
Environment with required reviewers; describe what changes in the workflow file and in the
repository settings, and what the on-call engineer sees.

**E4.4 — Rollback drill script.** Write a bash script that, given nothing but the cluster and
service names, prints the current and previous task-definition ARNs, the age of the last
deployment, whether a CodeDeploy deployment is in progress, and the exact rollback command
to run for each of Methods 1–3.

**E4.5 — Runbook reconciliation.** Diff every service, cluster, family and application name
in `rollback-runbook.md` against `deploy-prod.yml`, `rollback.yml` and
`fargate_service_stack.py`; produce the corrected runbook section and a CI check (a script
in the lint workflow) that greps the runbook for names not present in the workflows.

---

## Solutions

**S4.1** — the required set (per the memory notes) is the always-on gates: the six PR QA
Gate jobs, `Release Gate Summary`, `lint`, `regression-guard`. `Security Scan` jobs run on
push/schedule and are advisory; Bandit and gitleaks are duplicated inside the release gate
so that the _releasability_ decision includes them without making the slower Trivy/Semgrep
runs block every PR. Reasonable — and the audit should confirm the list matches, since it
lives in settings, not in the repo.

**S4.2** — the image's `WORKDIR` is `/app` and the code is at `/app/backend/…`, so
`app/db/alembic.ini` does not exist there; `backend/app/db/alembic.ini` does. Corrected
override: `["python","-m","alembic","-c","backend/app/db/alembic.ini","upgrade","head"]`
(and the same in `migration_task_stack.py`). The entrypoint doesn't have the problem
because it calls `python -m backend.app.db.bootstrap`, which locates `alembic.ini` from
`__file__` (`_ALEMBIC_INI = _REPO_ROOT / "backend" / "app" / "db" / "alembic.ini"`) — path
resolution in code, not in a copy-pasted string. Better still: make the migration task run
`python -m backend.app.db.bootstrap`, which also handles a fresh database.

**S4.3** — in the repo's Settings → Environments → `production`, add required reviewers;
the workflow already has `environment: production` on `run-migrations` and `deploy`, so no
YAML change is needed — the job pauses with a "Review deployments" button, and the reviewer
sees the tag, the snapshot step about to run, and approves. Add a `timeout-minutes` on the
job so an unapproved deploy doesn't hang for six hours.

**S4.4**

```bash
#!/usr/bin/env bash
set -euo pipefail
C=${1:-experimentation-prod}; S=${2:-experimentation-backend-prod}; F=experimentation-backend-prod
cur=$(aws ecs describe-services --cluster "$C" --services "$S" --query 'services[0].taskDefinition' --output text)
prev=$(aws ecs list-task-definitions --family-prefix "$F" --sort DESC --max-results 2 --query 'taskDefinitionArns[1]' --output text)
age=$(aws ecs describe-services --cluster "$C" --services "$S" --query 'services[0].deployments[0].createdAt' --output text)
dep=$(aws deploy list-deployments --application-name experimentation-platform --deployment-group-name experimentation-prod --include-only-statuses InProgress --query 'deployments[0]' --output text)
echo "current: $cur"; echo "previous: $prev"; echo "last deployment: $age"; echo "codedeploy in progress: ${dep:-none}"
echo "M1: gh workflow run 'Rollback Production' -f task_definition_arn=$prev -f reason='…'"
echo "M2: aws ecs update-service --cluster $C --service $S --task-definition $prev --force-new-deployment"
[ "$dep" != "None" ] && echo "M3: aws deploy stop-deployment --deployment-id $dep --auto-rollback-enabled"
```

**S4.5** — the runbook's `experimentation-api-prod` should be `experimentation-backend-prod`
(service) everywhere; family `experimentation-backend-prod`, cluster `experimentation-prod`,
CodeDeploy application `experimentation-platform`, deployment group `experimentation-prod`
are consistent. CI check: extract `experimentation-[a-z-]+` tokens from the runbook and from
the three source files, and fail if the runbook has a token the sources don't — a five-line
script that makes the doc a tested artefact.

---

_You've finished Platform Operations — and with it the seven-book path. The other two
specializations are [Experimentation Statistics](../experimentation-statistics/README.md)
and [SDK Engineering](../sdk-engineering/README.md); the guides — onboarding,
troubleshooting, usage — are in [`guides/`](../guides/); the core path is in the
[root README](../README.md)._ 🛠️

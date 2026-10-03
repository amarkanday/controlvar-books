# Deploying Experimently

### _One Docker host, Kubernetes, or AWS: what to run, in what order, and what bites_

[← experimently-docs](../README.md) · [Cutting a release](./releasing.md) · [Running the open-source project](./open-source.md) · [Troubleshooting](./troubleshooting.md)

Verified against getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased changes, including three
breaking ones: #747, #769, #770). Commands and paths below were read from the app at that commit; the live
GitHub and registry readings are dated where they appear. The app's own pages are the reference and this guide
points at them: `docs/deployment/deployment-guide.md`, `docs/deployment/rollback-runbook.md`,
`docs/deployment/secrets-management.md`, `docs/deployment/iam-permissions.md`, `docs/self-hosting/cdk.md`,
`docs/self-hosting/kubernetes.md` and `docs/self-hosting/releases.md`.

---

## 0. Choose a target

| Target | What runs it | Start here |
|---|---|---|
| One Docker host | `deploy/compose/compose.yml` and the published images | [§1](#1-one-docker-host-docker-compose) |
| Kubernetes | the Helm chart in `charts/experimently` | [§2](#2-kubernetes-helm) |
| AWS, the project's own path | the CDK app in `infrastructure/cdk` and three GitHub workflows: Deploy, Rollback, Database Migration | [§3](#3-aws-cdk-and-github-actions) |

**Profiles apply to every target.** One codebase, Apache-2.0 throughout, two build profiles: **core**
(`backend/` only) and **full** (adds the optional modules under `modules/`; `modules-manifest.txt` is the
authority on what is a module). The full profile signs the audit log and needs `AUDIT_HMAC_KEY`. A core
deployment on AWS has an unsigned audit log, and the Deploy workflow makes you tick
`accept_unsigned_audit_log` to say you accept that.

**The images.** `ghcr.io/getexperimently/experimently` (the API) and `ghcr.io/getexperimently/experimently-web`
(the dashboard), tagged `core-X.Y.Z` and `full-X.Y.Z`, plus floating `:core` and `:full` that move on final
releases only. Every image is `linux/amd64` only; an arm64 host runs it under emulation. Both packages are
public: on 2026-10-03 an anonymous token and manifest `GET` for `core-0.17.0` answered 200 for both (the
command is in [Cutting a release, §5](./releasing.md#5-verify-what-shipped)).

**Where migrations run** depends on the target, and it matters for every upgrade:

| Target | Who writes the schema |
|---|---|
| Compose | the API container, on start (`RUN_MIGRATIONS=true`); it is the only writer |
| Helm | an init container runs the bootstrap before the API container starts |
| AWS | only the Deploy workflow's migration task. API tasks run with `RUN_MIGRATIONS=false` |

Source: the header of `backend/docker-entrypoint.sh`.

---

## 1. One Docker host (Docker Compose)

`deploy/compose/compose.yml` runs the published images. It never builds, ships no demo services, and listens on
the host only for the dashboard.

**You need:** Docker with Compose v2, `openssl` (or `python3`), a DNS name, and a TLS reverse proxy in front.

```bash
cd deploy/compose
PUBLIC_BASE_URL=https://experimently.example.com FIRST_SUPERUSER=you@example.com \
  EXPERIMENTLY_PROFILE=core ./generate-env.sh   # writes .env once, with generated secrets
docker compose up -d --wait                     # postgres + redis + api + web
docker compose logs -f api                      # the first start creates the schema and the admin
```

**Required settings.** Compose refuses to start (and `docker compose config` exits non-zero) while any of
`SECRET_KEY`, `FIRST_SUPERUSER`, `FIRST_SUPERUSER_PASSWORD`, `POSTGRES_PASSWORD` or `PUBLIC_BASE_URL` is unset or
empty. The full profile also needs `AUDIT_HMAC_KEY`. `deploy/compose/.env.example` lists every setting.

**Gotchas:**

- **The API answers only for the host in `PUBLIC_BASE_URL`.** It must be exactly what the browser shows: scheme
  and host, no path, no trailing slash. The proxy must forward the `Host` header unchanged.
- **`generate-env.sh` refuses to overwrite `.env`.** A second run would rotate `SECRET_KEY` (signing everyone
  out) and write passwords the existing database volume does not accept. To start again from nothing:
  `docker compose down -v`, then delete `.env`.
- **The dashboard listens on `127.0.0.1:8080` by default** (`HTTP_BIND`, `HTTP_PORT`). Put the proxy in front of
  it.
- **The compose project name is fixed at `experimently-selfhost`**, so it never adopts the development stack's
  containers or volumes.
- **The image versions in `compose.yml` are literal.** release-please moves them in each release; there are no
  floating tags in this file. To upgrade, take the new release's `compose.yml` and `docker compose up -d --wait`.
- **`FIRST_SUPERUSER` and its password are used only while the users table is empty.** Changing them later does
  nothing. Changing `AUDIT_HMAC_KEY` later makes existing audit signatures unverifiable.
- **An external database:** set `POSTGRES_SERVER` and start only `docker compose up -d api web`.

---

## 2. Kubernetes (Helm)

`docs/self-hosting/kubernetes.md` is the procedure, and it is tested as written: the `chart-kind` CI job extracts
its four blocks and runs them in order on kind with ingress-nginx.

**You need:** `kubectl`, `helm` 3.14 or later, `openssl`, `curl` and `jq`, and four shell variables: `CHART`,
`PUBLIC_BASE_URL`, `ADMIN_EMAIL`, `PROFILE` (`core` or `full`).

1. **Secrets, once.** Create the namespace and a Secret with generated values. The command never replaces a
   Secret: a second run fails with `AlreadyExists` and leaves it as it was.
2. **Install:**

   ```bash
   helm install experimently "$CHART" --namespace experimently \
     --set profile="$PROFILE" \
     --set publicBaseUrl="$PUBLIC_BASE_URL" \
     --set firstSuperuser="$ADMIN_EMAIL" \
     --set existingSecret=experimently-secrets \
     --set ingress.className=nginx \
     --wait --timeout 10m
   ```

   `ingress.className=nginx` is ingress-nginx's IngressClass; use your cluster's.
3. **Check:** `curl` on `$PUBLIC_BASE_URL/api/v1/experiments/` prints `401`, and logging in as the first
   administrator returns the role `ADMIN`. In the first seconds ingress-nginx may answer `503`; run it again.
4. **Upgrade:** point `CHART` at the new chart, then

   ```bash
   helm upgrade experimently "$CHART" --namespace experimently \
     --reset-then-reuse-values \
     --wait --timeout 10m
   ```

**Where to get the chart.** Today the chart is a `.tgz` release asset only; the project has decided it will ALSO
be an OCI package at `oci://ghcr.io/getexperimently/charts/experimently`; the first push and making it public are
founder-only and not done. Download `experimently-X.Y.Z.tgz` from the GitHub release. The release workflow checks
that the archive's `version` and `appVersion` are exactly the release's version before attaching it
(`scripts/check_chart_package.py`), so the chart installs the images of the same release.

---

## 3. AWS (CDK and GitHub Actions)

This is the path the project runs itself. It is long because each step guards against a failure that has
happened. The ordered checklist in the app is `docs/deployment/deployment-guide.md` §1; the per-deploy procedure
is its §3.

**One region.** The Deploy, Rollback and Database Migration workflows act in `us-west-2` (`AWS_REGION` at the top
of each). Create the stacks, ECR repositories, secrets and certificate there. To use another region, change all
three workflows first.

**Every AWS block below starts by setting the profile and region in that same shell.** `<profile>` is your own
AWS CLI profile name; use your own, never one copied from someone else's notes.

**Billing and irreversible steps are marked.** Each one needs a person to say yes at the moment it happens. They
are collected as a checklist in [§3.14](#314-founder-only-and-irreversible-steps-aws).

### 3.0 Decide first

Before anything else, decide and write down: one AWS account per environment; the region; the profile (`full` or
`core`); the domain (`app.<domain>`); a budget alarm and who owns the bill; and when a staging environment is torn
down. The project's own setup is an AWS Organization whose management account runs no workloads, a separate
member account per environment, IAM Identity Center sign-in with MFA, and an organisation-wide budget alarm.

**Tools:** Python 3.11 or later, Node 18 or later for the CDK CLI (`npm install -g aws-cdk`), Docker with
buildx, AWS CLI v2, and `gh`. Work from a fresh clone of `main`.

### 3.1 Sign in

```bash
aws configure sso --profile <profile>        # once
aws sso login --sso-session <sso-session>    # each day (or: aws sso login --profile <profile>)
export AWS_PROFILE=<profile> AWS_REGION=us-west-2
aws sts get-caller-identity --query Arn --output text   # check who you are; never paste the output
```

Check the account's Fargate On-Demand vCPU quota in the Service Quotas console now. A quota increase is a
request that takes time, so start it before anything else if the quota is low.

### 3.2 Secrets (billable: each secret is charged per month)

Create, with an identity that may call `secretsmanager:CreateSecret`:

| Secret | Profile | Value |
|---|---|---|
| `/<env>/experimentation/jwt-secret` | both | 32 characters or more; the app's docs use `openssl rand -base64 48` |
| `/<env>/experimentation/first-superuser-password` | both | `admin` is refused in staging and prod |
| `/<env>/experimentation/audit-hmac-key` | full only | |

The database credentials are generated by the database stack; there is nothing to create for them.

**Export each secret's complete ARN.** The task definitions name each secret by its complete ARN, which ends in a
six-character suffix Secrets Manager chose, and every `cdk synth`, `cdk diff`, `cdk deploy` and `cdk destroy`
reads the three ARNs from your shell. On `core`, leave `AUDIT_HMAC_KEY_SECRET_ARN` unset.

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-west-2
ENV=staging
export JWT_SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "/$ENV/experimentation/jwt-secret" --query ARN --output text)
export FIRST_SUPERUSER_PASSWORD_SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "/$ENV/experimentation/first-superuser-password" --query ARN --output text)
export AUDIT_HMAC_KEY_SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id "/$ENV/experimentation/audit-hmac-key" --query ARN --output text)
```

**Check each one by its ARN, with a control that fails.** A check by name passes even when the ARN is wrong, so
check by the ARN itself. Then prove the check can fail: the same lookup with the six-character suffix replaced
must fail with `ResourceNotFoundException`.

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-west-2
for a in "$JWT_SECRET_ARN" "$FIRST_SUPERUSER_PASSWORD_SECRET_ARN" ${AUDIT_HMAC_KEY_SECRET_ARN:+"$AUDIT_HMAC_KEY_SECRET_ARN"}; do
  aws secretsmanager describe-secret --secret-id "$a" --query Name --output text      # prints the name
  aws secretsmanager describe-secret --secret-id "${a%??????}XXXXXX" --query Name --output text 2>&1 \
    | grep -c ResourceNotFoundException                                                  # must print 1
done
```

> **Gotcha: the control must use a wrong suffix, not a missing one.** A control that strips the suffix and
> expects a failure is wrong. Secrets Manager *resolves* a partial ARN (one without the suffix) unless the
> secret name's last hyphen-separated word is exactly six characters long, because then that word is parsed as
> the suffix. `jwt-secret` fails that way; `first-superuser-password` and `audit-hmac-key` resolve. A control
> that cannot tell "missing" from "resolved" proves nothing. The lesson generalises: a control must fail
> differently from the thing it guards. Complete ARNs are required either way; synth enforces it.

- **Recreating a secret changes its ARN.** Re-export it and run `cdk deploy` again.
- **Keep ARNs out of issues, pull requests and chat.** An ARN carries the account ID. None of the commands above
  prints one.

### 3.3 DNS zone and certificate

1. **A zone for the environment.** In the environment's account, create a hosted zone for the environment's
   subdomain. In the account that holds the parent zone, add an `NS` record for that subdomain with the four name
   servers the new zone lists. Check: `dig NS <env-domain> +short` prints them.
2. **The certificate, in `us-west-2`** (the load balancer's region, not `us-east-1`), for `app.<env-domain>`,
   with `api.<env-domain>` as an additional name if you will use it. Add the validation CNAME records, then:

   ```bash
   export AWS_PROFILE=<profile> AWS_REGION=us-west-2
   aws acm wait certificate-validated --certificate-arn "$CERTIFICATE_ARN"
   aws acm describe-certificate --certificate-arn "$CERTIFICATE_ARN" --query Certificate.Status
   ```

   It must print `"ISSUED"`. **Irreversible in one sense:** the host name appears in public certificate
   transparency logs from the moment you request it.

### 3.4 A protected GitHub environment, before anything names it

**A workflow dispatched for an environment that does not exist creates it, unprotected.** So protect the
environment before any secret, variable or dispatch names it: a required reviewer, and deployment branches `main`
only.

```bash
gh api -X PUT repos/<owner>/<repo>/environments/<env> --input - <<EOF
{"reviewers":[{"type":"User","id":$(gh api user --jq .id)}],
 "deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
EOF
gh api -X POST repos/<owner>/<repo>/environments/<env>/deployment-branch-policies -f name=main -f type=branch
gh api repos/<owner>/<repo>/environments/<env> --jq '.protection_rules, .deployment_branch_policy'   # check
```

For prod, the reviewer must be someone other than the person who dispatches the run.

### 3.5 The deploy role (GitHub OIDC)

`docs/deployment/iam-permissions.md` has the three identities (bootstrap, `cdk deploy`, one workflow role per
environment) and their exact permissions.

1. **Decode a real token's subject before writing the trust policy.** Run the probe from `main` and approve it;
   it prints the token's `sub`, `aud` and `environment` claims and never the token:

   ```bash
   gh workflow run oidc-sub-probe.yml --ref main -f environment=staging
   ```

   It offers only `staging` today. The repository uses GitHub's immutable subject,
   `repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:<env>`; read your prefix with
   `gh api repos/<owner>/<repo>/actions/oidc/customization/sub`. A fork has a different one.
2. **Once per account, the OIDC provider:**

   ```bash
   export AWS_PROFILE=<profile> AWS_REGION=us-west-2
   aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
     --client-id-list sts.amazonaws.com
   ```

3. **The role.** Copy `infrastructure/cdk/github-actions-trust-policy.json` to `trust.json`, replace
   `<AWS_ACCOUNT_ID>` and `<ENVIRONMENT>`, compare its subject with what the probe printed, then:

   ```bash
   export AWS_PROFILE=<profile> AWS_REGION=us-west-2
   aws iam create-role --role-name experimently-deploy-<env> \
     --assume-role-policy-document file://trust.json --max-session-duration 10800
   aws iam put-role-policy --role-name experimently-deploy-<env> --policy-name deploy \
     --policy-document file://infrastructure/cdk/github-actions-deploy-policy.json
   ```

   **The maximum session must be 10800 seconds.** Deploy asks for 9000 (a 150-minute job); IAM's default
   maximum is one hour, and the run then fails at "Configure AWS credentials", before anything has changed.

**Re-apply the policy after any release that changes the workflows.** It is generated by `scripts/iam_actions.py`
from every AWS call the workflows and their scripts make, and a unit test fails when a workflow gains a call the
committed policy does not grant. A role left on an old copy fails mid-run on the first new call. On the project's
staging rehearsal that happened three ways: a missing `rds:AddTagsToResource` stopped Deploy at the snapshot, a
missing `codedeploy:GetApplicationRevision` stopped Rollback, and without `cloudwatch:DescribeAlarms` and
`codedeploy:GetDeploymentGroup` every deploy stops at "Could not read the API's alarms".

### 3.6 Environment secrets and variable

```bash
gh secret set AWS_ACCOUNT_ID --env <env>      # prompts for the 12 digits
gh secret set AWS_ROLE_ARN   --env <env>      # prompts; paste the role's ARN
gh variable set PUBLIC_BASE_URL --env <env> --body https://app.<env-domain>
```

- **`AWS_ACCOUNT_ID` is a secret, not a variable,** because workflow logs are public and a step's log header
  prints variables before anything can mask them. The workflows read only `secrets.AWS_ACCOUNT_ID`; a leftover
  variable is ignored and the run refuses as "not configured". Move it, then
  `gh variable delete AWS_ACCOUNT_ID --env <env>`.
- **A malformed `AWS_ROLE_ARN` fails the first run with "Request ARN is invalid".** Nothing in AWS changes. Set it
  again from `aws iam get-role --role-name experimently-deploy-<env> --query Role.Arn --output text`, typed
  straight into the `gh secret set` prompt.
- `SLACK_BOT_TOKEN`, a repository secret, is optional.

### 3.7 ECR repositories and the bootstrap images (billable: image storage)

Two repositories, once per account and region; no stack creates them. Their names keep the project's old prefix,
`experimentation-platform/`, and the workflows depend on exactly these names.

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-west-2
for r in backend web; do
  aws ecr create-repository --repository-name experimentation-platform/$r \
    --image-scanning-configuration scanOnPush=true
done
```

Then one image in each, tagged `bootstrap`: CloudFormation cannot create an ECS service without a task
definition, and a task definition must name an image. Build from the repository root, for amd64, with the profile
you will deploy (`full` here):

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-west-2
ECR=<account>.dkr.ecr.us-west-2.amazonaws.com
aws ecr get-login-password | docker login --username AWS --password-stdin "$ECR"
docker buildx build --platform linux/amd64 --target full -f backend/Dockerfile \
  -t "$ECR/experimentation-platform/backend:bootstrap" --push .
docker buildx build --platform linux/amd64 -f frontend/Dockerfile --build-arg EXPERIMENTLY_PROFILE=full \
  -t "$ECR/experimentation-platform/web:bootstrap" --push .
```

- **Build for amd64 explicitly.** A plain `docker build` on an Apple-silicon Mac builds arm64.
- **Deploy never writes the `bootstrap` tag,** so the revision CloudFormation registers cannot drift to a later
  build.
- **The repositories survive teardown.**

### 3.8 CDK bootstrap (once per account and region)

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-west-2
cd infrastructure/cdk && pip install -r requirements.txt
cdk bootstrap aws://<account>/us-west-2
```

### 3.9 Synth, diff, deploy (billable from here)

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-west-2 CDK_DEFAULT_REGION=us-west-2
export CDK_DEFAULT_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)   # never echo it
export ENVIRONMENT=staging EXPERIMENTLY_PROFILE=full
export CERTIFICATE_ARN=<certificate ARN> PUBLIC_BASE_URL=https://app.<env-domain> ALARM_EMAIL=<ops inbox>
# and the three *_SECRET_ARN from §3.2, in this same shell
cdk synth --all > /dev/null && echo "synth ok"
cdk diff --all > diff.txt 2>&1        # contains the account number: read it, do not paste it
cdk deploy --all --require-approval never
```

**Synth refuses**, with a message saying how to fix it:

- a secret ARN that is missing, partial, for another environment (`/prod/...` in staging), in another region or
  account, or in the wrong variable;
- a missing `ALARM_EMAIL` in staging or prod, a malformed one, or one at `example.com`, `example.net` or
  `example.org`;
- a missing `CERTIFICATE_ARN` or `PUBLIC_BASE_URL`.

`EXPERIMENTLY_PROFILE` is read by `infrastructure/cdk/app.py`; `full` needs `modules/` in the checkout.

**The stacks.** Nine in every checkout (`auth`, `vpc`, `dynamodb`, `database`, `redis`, `compute`, `monitoring`,
`fargate`, `migrations`); a full checkout adds three (`dynamodb-counters`, `analytics`, `glue-etl`). Each is named
`experimentation-<name>-<env>`. No stack deploys a handler from `backend/lambda/`; the assignment Lambda was
removed (#606). The stacks do deploy placeholder or inline functions: `DatabaseAccessLambda` in the compute stack
in every environment, and in a full checkout `AnalyticsLambda` (placeholder) and `ETLTriggerLambda` (a real inline
function).

**Then, straight away:**

1. **Confirm the alarm email with the token, not the link.** SNS emails `ALARM_EMAIL` a confirmation link.
   Clicking it leaves a subscription that anyone who receives an alarm email can cancel from its unsubscribe link.
   Copy the `Token=` value out of the link instead:

   ```bash
   export AWS_PROFILE=<profile> AWS_REGION=us-west-2
   TOPIC_ARN=$(aws sns list-topics --output text \
     --query "Topics[?ends_with(TopicArn, ':experimentation-alerts-$ENVIRONMENT')].TopicArn")
   aws sns confirm-subscription --topic-arn "$TOPIC_ARN" \
     --token "$TOKEN_FROM_THE_LINK" --authenticate-on-unsubscribe true
   aws sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" \
     --query "Subscriptions[].[Protocol,Endpoint,SubscriptionArn]" --output text
   ```

   The last command must show the `email` subscription with a real ARN, not `PendingConfirmation`. Until it does,
   alarms fire and nobody is told.
2. **Point the host names at the load balancer.** **No stack creates a DNS record**, so until you do `curl`
   prints `000`. Add an alias A record (not a CNAME) for `app.<env-domain>`, and `api.<env-domain>` if you use
   it, pointing at the load balancer named `experimentation-<env>`. `docs/self-hosting/cdk.md`, "Point the
   hostname at the load balancer", has the commands and the check.
3. **Run the first Deploy soon.** **`cdk deploy` creates no schema.** Until the first Deploy's migration runs,
   `/health` answers 200 and the load balancer's health checks pass, while real requests answer 500. Requests in
   that window, from scanners as much as people, can put a 5xx alarm into ALARM, and Deploy then refuses to start.
   The way through is Deploy's break-glass (§3.10), and a person decides at that moment whether to use it.

**Later `cdk deploy`s of a running environment:** deploy only `experimentation-fargate-<env>`, never `--all`, and
pin what is live. Releases reach the API and the dashboard outside CloudFormation, so an unpinned `cdk deploy`
silently routes the API to the empty target group, or puts the dashboard back on `:bootstrap`, while every probe
stays green. Two read-only scripts print the values and refuse a value that does not match what is running:

```bash
export AWS_PROFILE=<profile> AWS_REGION=us-west-2
python3 scripts/check_live_target_group.py --env "$ENVIRONMENT"                 # prints api_live_target_group
python3 scripts/check_dashboard_image.py --env "$ENVIRONMENT"                   # prints dashboard_image_tag
python3 scripts/check_live_target_group.py --env "$ENVIRONMENT" --expect <blue|green>
python3 scripts/check_dashboard_image.py --env "$ENVIRONMENT" --expect sha256:<hex>
cdk deploy "experimentation-fargate-$ENVIRONMENT" --require-approval never \
  -c backend_image_tag=<tag> -c api_live_target_group=<blue|green> -c dashboard_image_tag=sha256:<hex>
```

`docs/deployment/deployment-guide.md` §1.6 explains each pin, and what to do when the dashboard still runs
`:bootstrap`.

### 3.10 Every deploy: the Deploy workflow

1. **A release tag whose commit passed the Release Gate.** The Release Gate runs on every push to `main`. For a
   tag cut before that: `gh workflow run release-gate.yml --ref vX.Y.Z`, then wait for it.
2. **Actions → Deploy → Run workflow, from `main`** (any other ref is refused). Inputs:
   - `environment`: `staging` or `prod`;
   - `version`: the tag, `vX.Y.Z`;
   - `profile`: `full` or `core` (core also needs `accept_unsigned_audit_log` ticked);
   - optionally the break-glass, `override_alarms` with `override_alarms_reason`.
3. **Approve** when the environment asks. Nothing in AWS has changed before that.
4. **What the run does,** in order:
   - refuses, before any change: an unconfigured environment, the wrong account, missing stacks, a profile the
     stacks were not deployed for, missing secrets (checked by the ARN the task definition names,
     `scripts/check_task_secrets.py`), a missing ECR repository, an active CodeDeploy deployment, a dashboard
     service that is missing or mid-rollout;
   - builds `:<tag>-<profile>` into ECR, or reuses it, and checks its labels name the tag's commit;
   - snapshots the database (`pre-deploy-<env>-<tag>-<time>`) and runs the migration task by digest;
   - registers the API revision by digest, and refuses it unless it sets `RUN_MIGRATIONS=false`;
   - shifts traffic with CodeDeploy blue/green: healthy targets first, then the approval, then the canary,
     **10% for 5 minutes at the reference commit; the project has decided to lengthen it to 15 minutes before
     production**, then the rest. It succeeds when the new revision is PRIMARY and the `/api/*` rule forwards to
     its target group;
   - smoke-tests `GET /api/v1/experiments/` (401 `{"detail":"Not authenticated"}`), rolls the dashboard by digest,
     checks `GET /` answers 200 `text/html`, and checks the API once more;
   - prints a summary with the rollback line and the `cdk` pins for §3.9.
5. **Check afterwards** (`docs/deployment/deployment-guide.md` §5): `/health`, the 401 smoke request, and
   `aws ecs describe-services` for the backend and dashboard services.

**Gotchas,** each observed or pinned by a test:

- **One deploy per environment per hour.** CodeDeploy keeps the previous task set for 60 minutes after the shift
  and accepts no second deployment meanwhile. A deploy dispatched in that hour is refused at once, before anything
  is built. The refusal fired as designed on staging.
- **To ship a fix within that hour, run Rollback first,** then deploy the fix after Rollback's own deployment is
  no longer active.
- **The alarms** are `experimentation-api-5xx-blue-<env>` and `experimentation-api-5xx-green-<env>`. They watch
  the canary and the hour after it, and while either is in ALARM CodeDeploy stops the deployment and rolls the API
  back by itself. They do not catch a release that answers wrongly with a 2xx, the load balancer's own 502 and
  504, or the dashboard. Staging traffic may never reach the threshold, so a quiet staging deploy proves nothing
  about them.
- **A rollback in the hour after the shift comes after the run went green, and is not announced in
  `#deployments`.** The alarm emails `ALARM_EMAIL`; there is no OK action, so recovery is not emailed. The run
  summary gives the time the alarms stop watching and the command that shows whether a rollback happened.
- **`set-alarm-state` and `disable-alarm-actions` do not unblock a deploy.** CodeDeploy reads the alarms' state.
  The break-glass is `override_alarms` with a reason: that one deployment runs with **no alarm watching it**, the
  run name ends in `ALARMS OVERRIDDEN` so the reviewer sees it at the approval, and the next deploy is watched
  again. It worked on staging. Read `docs/deployment/rollback-runbook.md`, "Fix forward while an alarm is
  firing", before using it.
- **Every migration must be backward-compatible** (expand, then contract). A deploy migrates before it shifts
  traffic, and a rollback runs the older API on the newer schema.
- **The deploy job may run for up to 150 minutes.**
- **A newer dispatch waiting behind a running deploy replaces an older one that was still waiting.**

The forward Deploy path, the alarm auto-rollback and the break-glass have all completed on the project's staging
environment.

### 3.11 Rollback

**Method 1, Actions → Rollback,** is the intended path. Inputs, copied from the deploy summary's rollback line:
`environment`, `task_definition_arn` (`experimentation-backend-<env>:<n>`), optionally
`dashboard_task_definition_arn`, and `reason`. It refuses when the API is already on the target, and while
CodeDeploy's own rollback is active (a deployment whose `creator` is `codeDeployRollback`).

> **Caution: the first rollback in an environment.** At the reference commit the Rollback workflow has not yet
> completed green end-to-end against real AWS; each staging attempt stopped on a different problem. Treat the
> first rollback in a new environment as a rehearsal you watch closely, not as a routine step, and read
> `docs/deployment/rollback-runbook.md` before it.

- **Never re-dispatch Rollback within the hour.** It would revert the rollback.
- **Check a target revision's `RUN_MIGRATIONS` before rolling back to it.** An older revision without
  `RUN_MIGRATIONS=false` runs the bootstrap when a task starts, and that refuses a schema a newer release
  migrated (`docs/deployment/rollback-runbook.md`, "The rolled-back API runs against the current schema").

**When an alarm rolled the API back** (the run ended "Rolled back by an alarm"): do **not** run Rollback; the API
is already going back. Confirm it is back, put the dashboard back if the alarm fired after the shift (the
runbook's "An alarm rolled the API back" section), and do not redeploy the same tag: fix it and cut a new
release.

**The database.** The Database Migration workflow runs alembic with the image of the API's PRIMARY task set, so:

- **Undo a migration before you roll the API back.** Run Actions → Database Migration with `direction:
  downgrade` while the release that contains the migration is still serving.
- `target` must be a revision id: the `down_revision` of the migration you are undoing. `-1`, `head` and `base`
  are refused because a full image has two heads.
- **After an API rollback or an alarm rollback there is no supported downgrade through the workflow.** Do not
  dispatch it to try. Stop and escalate to whoever owns the environment's data; this guide gives no restore
  procedure.

### 3.12 Teardown

- Only `prod` keeps its data when its stacks are destroyed (`retains_data(env) == (env == "prod")`).
- `cdk destroy --all` synthesises the app like any other command: it needs `ALARM_EMAIL` and the three secret ARNs
  exported. `docs/self-hosting/cdk.md`, "Tearing an environment down", has the order for destroying stacks one by
  one.
- **Left behind, and still billing:** the `pre-deploy-*` and `pre-migration-*` database snapshots, the secrets, the
  ECR repositories and their images, the `CDKToolkit` bootstrap stack, the DNS records you created, and log groups
  AWS services created themselves. `docs/self-hosting/cdk.md`, "What `cdk destroy` leaves behind and bills", is
  the full table.
- Closing the account is the only teardown that guarantees nothing is left.

### 3.13 Not verified here

- The Rollback workflow completing green end-to-end on real AWS (§3.11).
- The prod path as a whole: no prod account or `prod` GitHub environment existed on 2026-10-03.
- Whether `cdk deploy --all` still diffs clean against a running staging environment at the reference commit.
- Compose and Helm on a real host. CI runs both (the compose production test and `chart-kind`); this guide's
  author did not.

### 3.14 Founder-only and irreversible steps (AWS)

Each needs a named person at the moment it happens. Read-only checks print no account values.

| Step | Who | Irreversible? | Read-only check afterwards |
|---|---|---|---|
| Create the AWS Organization and the environment's member account | founder | yes (an account is closed, not deleted) | `aws sts get-caller-identity --query Arn` under the new profile |
| Budget alarm | founder | no | the budget appears in the Billing console |
| Create the secrets | founder or account administrator | no, but billable | §3.2's by-ARN check and its failing control |
| `NS` delegation in the parent zone's account | founder (the holder of the parent zone) | no | `dig NS <env-domain> +short` |
| Request the certificate | founder or account administrator | yes: the name is in public CT logs | `aws acm describe-certificate ... --query Certificate.Status` prints `"ISSUED"` |
| Protect the GitHub environment | repository administrator | no | `gh api repos/<owner>/<repo>/environments/<env> --jq '.protection_rules, .deployment_branch_policy'` |
| `cdk deploy` | founder | no, but billable from the first stack | `aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE --query "StackSummaries[?contains(StackName, 'experimentation-')].StackName"` |
| Confirm the SNS subscription with the token | the owner of `ALARM_EMAIL` | no | `aws sns list-subscriptions-by-topic ...` shows no `PendingConfirmation` |
| Approve each environment deployment | the environment's reviewer (for prod, not the dispatcher) | the traffic shift can be rolled back; the migration may not be | the run summary, then §3.10 step 5 |
| `cdk deploy` of the database stack on a running environment (for example to apply the 35-day backup retention that `infrastructure/cdk/stacks/environments.py` sets for staging and prod) | founder | no, but it changes the live database cluster | run `cdk diff experimentation-database-<env>` first and deploy only if it shows `BackupRetentionPeriod` and nothing else |
| Tick `override_alarms` | the dispatcher, and the reviewer who approves it | the unwatched deployment cannot be made watched | the run name ends in `ALARMS OVERRIDDEN` |
| Create the production account and the `prod` environment, with a reviewer other than the dispatcher | founder | yes | `gh api repos/<owner>/<repo>/environments/prod --jq .protection_rules` |

# Part II — AWS

### Hours 4–6

[← Part I](./part-1-local-stack.md) · [Contents](./README.md) · [Part III →](./part-3-observability.md)

Hour 4 introduces the CDK stacks, their dependencies and the environment and profile settings that
select them. The reviewed checkout has nine core stacks and twelve with modules. Hour 5 traces
requests through the ALB to the API and dashboard services, then covers deployment and scaling.
Hour 6 examines the data services, analytics pipeline and backups.

Use synthesized templates to inspect resource settings alongside the stack definitions. Details
such as secret ARNs, network access and retention periods matter when comparing environments.

---

## Hour 4 — The CDK app: nine stacks (twelve with the modules) and the order they depend on

### What & why

Infrastructure-as-code has the same two virtues as application code: it's reviewable and
it's reproducible. Experimently's is the AWS CDK in Python — the same language as the
backend, so the same engineers can read it — organised as **one stack per concern** so a
change to monitoring doesn't touch the database, and CloudFormation can roll back one thing
at a time. The app file wires them with explicit `add_dependency` calls; CDK then deploys in
topological order. Every stack is named `experimentation-<name>-<env>`.

Three inputs decide what gets built:

- **`ENVIRONMENT`** — `dev`, `staging`, `prod` or `demo`. Sizes are decided in one place,
  `infrastructure/cdk/stacks/environments.py`, which every stack asks instead of comparing
  the name itself. Its rule is short: **prod keeps its data** when a stack is destroyed
  (RETAIN, and a final SNAPSHOT for Aurora), **every other environment is disposable**
  (DESTROY — staging included); prod gets the redundant shape (two NAT gateways, an Aurora
  writer and reader, three Redis nodes), everything else one of each.
- **The profile** — whether the checkout has `modules/`. The core profile synthesises nine
  stacks; a full checkout adds three module stacks (`dynamodb-counters`, `analytics`,
  `glue-etl`) loaded from `modules/infrastructure/cdk/stacks/`. `EXPERIMENTLY_PROFILE=core`
  or `full` overrides the default in either direction — the same variable the images use.
- **The synth inputs** — the account, the certificate, the public URL, the secret ARNs and
  the alarm address. Synth refuses to run without the ones it needs (Try It below).

**Environments and accounts.** The project deploys two long-lived environments, `staging`
and `prod`, through **one path**: the same stacks (`ENVIRONMENT=<env> cdk deploy`) and the
same Deploy, Rollback and Database Migration workflows, each dispatched with
`environment: staging` or `environment: prod` (Part IV). Each GitHub environment names its
own AWS account — staging and prod live in separate accounts — through two environment
**secrets**, `AWS_ACCOUNT_ID` and `AWS_ROLE_ARN` (the OIDC role), and one **variable**,
`PUBLIC_BASE_URL`. The account ID is a secret rather than a variable because workflow logs
are public. Everything is in **one region, `us-west-2`**: the three workflows pin it, so the
stacks, ECR repositories, secrets and certificate go there too. `dev` and `demo` are for
`cdk deploy` by hand.

### How Experimently does it

The entrypoint — environment validation, task counts per environment, account from
credentials only:

```python
# 📁 infrastructure/cdk/app.py
VALID_ENVIRONMENTS = ["dev", "staging", "prod", "demo"]
env_name = os.environ.get("ENVIRONMENT", "dev")
if env_name not in VALID_ENVIRONMENTS:
    raise ValueError(
        f"Invalid ENVIRONMENT '{env_name}'. Must be one of: {', '.join(VALID_ENVIRONMENTS)}"
    )
...
API_DESIRED_COUNT = {"dev": 2, "staging": 2, "prod": 3, "demo": 1}
DASHBOARD_DESIRED_COUNT = {"dev": 1, "staging": 1, "prod": 2, "demo": 1}

# Account/region come from the environment only: CDK sets CDK_DEFAULT_ACCOUNT
# from the active credentials; AWS_ACCOUNT_ID is the fallback an operator can
# export. No account id is hard-coded in the repository.
account = os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")
region = os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-west-2")
if not account:
    raise SystemExit(
        "AWS account id not set: export AWS_ACCOUNT_ID (or let the CDK CLI set "
        "CDK_DEFAULT_ACCOUNT from your credentials) before synthesizing."
    )
```

"No account id is hard-coded in the repository" is a security property and a portability
property at once: the repo can be public, and anyone can deploy it into their own account.
(The file still computes `db_instance_size` and `cache_node_type` for demo, but passes them to
nothing: the database and Redis stacks size themselves, Hour 6.)

The profile decision, made the same way the application makes it — by whether the modules
are *there*:

```python
# 📁 infrastructure/cdk/app.py
_PROFILE = os.environ.get("EXPERIMENTLY_PROFILE", "").strip().lower()
if _PROFILE not in ("", "core", "full"):
    raise SystemExit(
        f"EXPERIMENTLY_PROFILE must be 'core' or 'full', not {_PROFILE!r}"
    )
_MODULES_PRESENT = _MODULES_STACKS_DIR.is_dir()
...
ENABLE_MODULE_STACKS = _MODULES_PRESENT and _PROFILE != "core"
...
if ENABLE_MODULE_STACKS:
    AnalyticsStack = _load_modules_stack("analytics_stack", "AnalyticsStack")
    DynamoDBCountersStack = _load_modules_stack("dynamodb_counters_stack", "DynamoDBCountersStack")
    GlueETLStack = _load_modules_stack("glue_etl_stack", "GlueETLStack")
```

The module stacks are loaded by file path, so `cdk synth` never imports the `modules`
package (which would pull in the whole backend). `EXPERIMENTLY_PROFILE=full` on a core
checkout fails rather than quietly dropping them.

Before `App()` is even created, the secret ARNs are resolved and checked (Hour 5 explains
why they are complete ARNs):

```python
# 📁 infrastructure/cdk/app.py
secret_arns = resolve_secret_arns(
    os.environ,
    env_name=env_name,
    account=account,
    region=region,
    include_modules=ENABLE_MODULE_STACKS,
)

app = App()
```

The stacks and their dependencies, in the order the file declares them (constructor
arguments abridged):

```python
# 📁 infrastructure/cdk/app.py (condensed)
auth_stack = AuthenticationStack(
    app, f"experimentation-auth-{env_name}", environment=env_name, env=env
)
vpc_stack = VpcStack(
    app,
    f"experimentation-vpc-{env_name}",
    environment=env_name,
    nat_gateways=nat_gateway_count(env_name),
    env=env,
)
dynamodb_stack = DynamoDBTablesStack(
    app, f"experimentation-dynamodb-{env_name}", environment=env_name, env=env
)
if ENABLE_MODULE_STACKS:
    dynamodb_counters_stack = DynamoDBCountersStack(...)
database_stack = EnhancedDatabaseStack(...)
database_stack.add_dependency(vpc_stack)
redis_stack = ElastiCacheRedisStack(...)
redis_stack.add_dependency(vpc_stack)
compute_stack = ComputeStack(
    app,
    f"experimentation-compute-{env_name}",
    vpc=vpc_stack.vpc,
    env_name=env_name,
    env=env,
)
compute_stack.add_dependency(vpc_stack)
compute_stack.add_dependency(database_stack)
compute_stack.add_dependency(dynamodb_stack)
compute_stack.add_dependency(redis_stack)
analytics_stack = None
if ENABLE_MODULE_STACKS:
    analytics_stack = AnalyticsStack(...)
    analytics_stack.add_dependency(vpc_stack)
    analytics_stack.add_dependency(dynamodb_stack)
monitoring_stack = MonitoringStack(
    ...
    alarm_email=os.environ.get("ALARM_EMAIL"),
    redis_replication_group_id=redis_stack.replication_group_id,
    env=env,
)
monitoring_stack.add_dependency(vpc_stack)
monitoring_stack.add_dependency(database_stack)
if analytics_stack is not None:
    monitoring_stack.add_dependency(analytics_stack)
...
fargate_stack = FargateServiceStack(
    app,
    f"experimentation-fargate-{env_name}",
    ...
    certificate_arn=certificate_arn,
    public_base_url=public_base_url,
    include_modules=ENABLE_MODULE_STACKS,
    secret_arns=secret_arns,
    api_desired_count=API_DESIRED_COUNT[env_name],
    dashboard_desired_count=DASHBOARD_DESIRED_COUNT[env_name],
    db_host=database_stack.writer_host,
    db_credentials=database_stack.db_credentials,
    db_security_group=database_stack.rds_security_group,
    redis_host=redis_stack.primary_host,
    redis_port=redis_stack.primary_port,
    alarm_topic=monitoring_stack.alerts_topic,
    env=env,
)
fargate_stack.add_dependency(compute_stack)
fargate_stack.add_dependency(monitoring_stack)
fargate_stack.add_dependency(database_stack)
fargate_stack.add_dependency(redis_stack)
if ENABLE_MODULE_STACKS:
    fargate_stack.add_dependency(dynamodb_counters_stack)
migration_stack = MigrationTaskStack(...)
migration_stack.add_dependency(fargate_stack)
migration_stack.add_dependency(database_stack)
if ENABLE_MODULE_STACKS:
    glue_etl_stack = GlueETLStack(...)
    glue_etl_stack.add_dependency(analytics_stack)

app.synth()
```

Read as a layered map (module stacks in _italics_):

| Layer | Stacks | Depends on |
|---|---|---|
| Identity & network | `auth` (Cognito), `vpc` | — |
| Data | `database` (Aurora), `redis`, `dynamodb`, _`dynamodb-counters`_ | vpc (database, redis) |
| Compute | `compute`: the ECS cluster `experimentation-<env>`, the ECS tasks' security group, one database-access Lambda | vpc, database, dynamodb, redis |
| Analytics | _`analytics`_ (Kinesis, Firehose, S3, OpenSearch), _`glue-etl`_ | vpc, dynamodb; analytics |
| Observability | `monitoring` (SNS topic, alarms, dashboard) | vpc, database (+ analytics) |
| Serving | `fargate` (ALB, API and dashboard services, CodeDeploy, alarms, scaling), `migrations` (one-shot task definition) | compute, monitoring, database, redis (+ counters); fargate, database |

Two edges are worth noticing. `fargate` depends on `monitoring` because the API's 5xx alarms
announce automatic rollbacks on the monitoring stack's topic (Hour 5, Hour 9). And
`monitoring` depends on `database` because its Aurora CPU alarm reads the cluster's
identifier from an SSM parameter the database stack writes — a parameter rather than a
CloudFormation export, so nothing pins the database stack in place.

The VPC — two AZs, three subnet tiers, NAT gateways by environment, and endpoints so
private tasks reach S3, DynamoDB, ECR, CloudWatch Logs and Secrets Manager without crossing
the NAT:

```python
# 📁 infrastructure/cdk/stacks/vpc_stack.py (condensed)
        self.vpc = ec2.Vpc(
            self,
            "ExperimentationVPC",
            max_azs=2,
            nat_gateways=nat_gateways,
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            subnet_configuration=[
                ...subnet_type=ec2.SubnetType.PUBLIC,
                ...subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                ...subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
            ],
            gateway_endpoints={
                "S3": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.S3
                ),
                "DynamoDB": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.DYNAMODB
                ),
            },
        )
        self.vpc.add_interface_endpoint(
            "SecretsManagerEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
        )
        self.vpc.add_interface_endpoint(
            "ECREndpoint", service=ec2.InterfaceVpcEndpointAwsService.ECR
        )
```

`nat_gateways` comes from `nat_gateway_count(env_name)`: two in prod (one per AZ), one
everywhere else — each NAT gateway bills by the hour whether or not anything uses it.
`PRIVATE_ISOLATED` is where the production database lives (Hour 6): no route to the
internet at all, in either direction.

> **By the Way** — about Lambda. No stack deploys a handler from `backend/lambda/`; the
> assignment Lambda was removed (#606). The stacks do deploy placeholder or inline
> functions: `DatabaseAccessLambda` in the compute stack in every environment, and in a full
> checkout `AnalyticsLambda` (placeholder) and `ETLTriggerLambda` (a real inline function).
> There is no API Gateway stack any more. Every request from an SDK or the dashboard takes
> one path: ALB → Fargate → FastAPI.

> **Watch Out!** `cdk deploy --all` on a fresh account needs more than `cdk bootstrap`.
> Three things must exist first, and no stack creates them: the ECR repositories
> `experimentation-platform/backend` and `experimentation-platform/web` with a `bootstrap`
> image pushed to each (Hour 5); the Secrets Manager secrets under
> `/<env>/experimentation/` whose complete ARNs you pass in; and an ACM certificate. And one
> thing must happen after: **no stack creates a DNS record**. The host in `PUBLIC_BASE_URL`
> resolves only once you add an alias record for it, pointing at the load balancer
> `experimentation-<env>` (`docs/self-hosting/cdk.md` has the commands).

### Try It Yourself

Synth needs the inputs a real deploy needs. Placeholders are fine — nothing resolves an ARN
until a deployment, and nothing is deployed here:

```bash
cd infrastructure/cdk && pip install -r requirements.txt
export AWS_ACCOUNT_ID=123456789012 AWS_REGION=us-west-2 ENVIRONMENT=demo
export CERTIFICATE_ARN=arn:aws:acm:us-west-2:123456789012:certificate/00000000-0000-0000-0000-000000000000
export PUBLIC_BASE_URL=https://app.example.com
export JWT_SECRET_ARN=arn:aws:secretsmanager:us-west-2:123456789012:secret:/demo/experimentation/jwt-secret-AbCdEf
export FIRST_SUPERUSER_PASSWORD_SECRET_ARN=arn:aws:secretsmanager:us-west-2:123456789012:secret:/demo/experimentation/first-superuser-password-AbCdEf
export AUDIT_HMAC_KEY_SECRET_ARN=arn:aws:secretsmanager:us-west-2:123456789012:secret:/demo/experimentation/audit-hmac-key-AbCdEf
cdk ls                                    # 12 names, suffixed -demo, in a full checkout
EXPERIMENTLY_PROFILE=core cdk ls          # 9: no counters, analytics or glue-etl
cdk synth experimentation-fargate-demo > /tmp/fargate.yaml && grep -c "AWS::" /tmp/fargate.yaml
grep -n "DesiredCount\|Cpu:\|Memory:" /tmp/fargate.yaml | head
ENVIRONMENT=bogus cdk synth 2>&1 | tail -1                       # the ValueError
env -u PUBLIC_BASE_URL cdk synth 2>&1 | grep -m1 PUBLIC_BASE_URL # refused at synth
```

Now try staging. The secret ARNs name `/demo/...`, and `ALARM_EMAIL` is not set — read the
two refusals, then fix them:

```bash
ENVIRONMENT=staging cdk ls 2>&1 | tail -2           # JWT_SECRET_ARN names a secret of the demo environment
for v in JWT_SECRET_ARN FIRST_SUPERUSER_PASSWORD_SECRET_ARN AUDIT_HMAC_KEY_SECRET_ARN; do
  export $v="${!v/\/demo\//\/staging\/}"; done
ENVIRONMENT=staging cdk ls 2>&1 | tail -2           # ALARM_EMAIL is required in staging
ENVIRONMENT=staging ALARM_EMAIL=ops@your-domain.com cdk ls
ENVIRONMENT=prod ALARM_EMAIL=ops@your-domain.com cdk ls 2>&1 | tail -1   # the /staging/ ARNs: refused
```

(No credentials are needed to synth with `AWS_ACCOUNT_ID` set. The refusals never print the
value you gave — an ARN carries an account ID, and a synth log is not a place for one.)

### Key takeaways

- One stack per concern: **9** in the core profile (`auth`, `vpc`, `dynamodb`, `database`,
  `redis`, `compute`, `monitoring`, `fargate`, `migrations`), **12** in a full checkout
  (+ `dynamodb-counters`, `analytics`, `glue-etl` from `modules/infrastructure/cdk/stacks/`),
  all named `experimentation-<name>-<env>`, wired with explicit `add_dependency`.
- `ENVIRONMENT` ∈ dev/staging/prod/demo; `stacks/environments.py` decides: prod retains,
  everything else is disposable; prod is redundant, everything else single.
- Staging and prod: one Deploy path, separate AWS accounts, `us-west-2`, configured per
  GitHub environment (`AWS_ACCOUNT_ID`, `AWS_ROLE_ARN` secrets; `PUBLIC_BASE_URL` variable).
- Synth refuses missing inputs: certificate, public URL, complete secret ARNs, and
  `ALARM_EMAIL` in staging and prod. No account ID in the repo; no DNS record from any stack.
- No stack deploys a `backend/lambda/` handler; the only functions are placeholders or inline.

---

## Hour 5 — Serving traffic: two services, one load balancer, blue/green for the API

### What & why

The production request path is: **DNS** (the alias record you create) → **internet-facing
ALB** (HTTPS on 443; HTTP on 80 redirects) → **listener rules** → one of two ECS Fargate
services in private subnets:

- `/api/*`, `/health`, `/health/*` and `/metrics` go to the **API**, at its *live* target
  group (blue or green — whichever the last deployment left serving);
- **everything else** — the listener's default action — goes to the **dashboard**, a static
  Next.js export served by nginx on port 8080 in its own service.

One public origin for both means no CORS between the dashboard and its API, and one
certificate.

The two services are replaced differently, because they fail differently. The **dashboard**
serves static files: an ECS rolling deployment with the deployment circuit breaker (rollback
on) is enough. The **API** is where a bad release hurts, so it gets **blue/green with a
canary**: the old task set ("blue") keeps serving while a new one ("green") starts; once the
shift is approved, 10% of the API's traffic goes to green for five minutes, then the rest —
and blue is kept for an hour so the first rollback is a traffic switch, not a rebuild. At the
reference commit the canary is `CANARY_10_PERCENT_5_MINUTES`: 10% for 5 minutes; the project
has decided to lengthen it to 15 minutes before production.

The API service hands deployment control to CodeDeploy (`deployment_controller=CODE_DEPLOY`),
and that has consequences you will meet in every later Hour:

- **The pipeline owns what runs, not CloudFormation.** The Deploy workflow (Part IV) builds
  the image, registers a task-definition revision naming it **by digest**, and hands that to
  CodeDeploy. ECS refuses a task-definition change on a CodeDeploy-controlled service, so a
  `cdk deploy` cannot change what serves traffic. The stack's own task definition names a
  `bootstrap` image that exists only so the service can be created.
- **CloudWatch alarms can stop a deployment.** The API's two 5xx alarms (one per target
  group) are attached to the deployment group; while either is in ALARM, CodeDeploy stops and
  rolls back by itself.
- **The API never writes the schema.** Its tasks run with `RUN_MIGRATIONS=false`; the
  Deploy workflow's one-shot migration task is the only writer (Part IV). So `cdk deploy`
  alone leaves a new environment without a schema: `/health` answers 200 while real
  requests answer 500, until the first Deploy runs the migration.

Secrets reach the container as environment variables that ECS resolves from Secrets Manager
at task start — the image never contains them and CloudFormation never sees their values.

### How Experimently does it

**Secrets, by complete ARN.** The stack imports each secret from the ARN `app.py` validated:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        jwt_secret = secretsmanager.Secret.from_secret_complete_arn(
            self, "JwtSecret", secret_arns["JWT_SECRET_ARN"]
        )
        superuser_secret = secretsmanager.Secret.from_secret_complete_arn(
            self,
            "SuperuserPasswordSecret",
            secret_arns["FIRST_SUPERUSER_PASSWORD_SECRET_ARN"],
        )
        required_secrets = [jwt_secret, superuser_secret]
        audit_secret = None
        if include_modules:
            audit_secret = secretsmanager.Secret.from_secret_complete_arn(
                self,
                "AuditHmacSecret",
                secret_arns["AUDIT_HMAC_KEY_SECRET_ARN"],
            )
            required_secrets.append(audit_secret)
```

Why the *complete* ARN — the one ending in the six-character suffix Secrets Manager adds when
it creates a secret? Importing by name renders a *partial* ARN (no suffix) into the task
definition's `valueFrom`. Secrets Manager resolves a partial ARN unless the name's last hyphen-separated
word is exactly six characters long, because that word is then parsed as the suffix. Of the
three, `jwt-secret` is the one that fails ("secret" is six characters), and every task needs
it, so no task would start; `first-superuser-password` and `audit-hmac-key` would resolve.
(That is also why a check that the complete ARN is right needs a *wrong* suffix as its
negative control, not a missing one; see the deploying guide.) Nothing can derive the suffix
at synth, so the operator supplies it:
`JWT_SECRET_ARN` and `FIRST_SUPERUSER_PASSWORD_SECRET_ARN` always, `AUDIT_HMAC_KEY_SECRET_ARN`
for the full profile (it signs the audit log; a core image never reads it, and naming a
secret that does not exist would stop ECS starting the task). `stacks/secret_arns.py` checks
each value: a complete ARN, in the stack's account and region, for this environment's
`/<env>/experimentation/<name>`, and the right one of the three. Its refusal names the
read-only command that prints the right value:

```python
# 📁 infrastructure/cdk/stacks/secret_arns.py
def how_to_read(variable: str, env_name: str) -> str:
    """The read-only command that prints the value ``variable`` must hold."""
    name = secret_name(env_name, SECRET_INPUTS[variable])
    return (
        f"export {variable}=$(aws secretsmanager describe-secret "
        f"--secret-id {name} --query ARN --output text)"
    )
```

There is no database secret of your own and no Redis secret. The database credentials come
from the secret **Aurora generated** when the cluster was created (Hour 6), so they cannot
drift from what the cluster has. Redis has no AUTH token: the task gets a host and port and
speaks TLS. Both roles get `GetSecretValue` on exactly `required_secrets` — the **task role**
(what the running code may do) and the **execution role** (what ECS needs to *start* the
task: pull the image, write logs, inject the secrets).

**The task definition and its image.** The image tag is a deliberate non-answer:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        image_tag = self.node.try_get_context("backend_image_tag")
        if image_tag is None:
            image_tag = "bootstrap"
        if not isinstance(image_tag, str) or not image_tag.strip():
            raise ValueError(
                "backend_image_tag context must be a non-empty string; got "
                f"{image_tag!r}"
            )
        if image_tag == "latest":
            raise ValueError(
                "backend_image_tag must not be `latest`: any build can move "
                "that tag, which is #82"
            )
        self.backend_image_tag = image_tag
```

(`#82` in that message is a pre-move issue number from the old tracker; it does not refer to
issue #82 in this repository.)

`bootstrap` is a tag the pipeline never writes, so it cannot drift. `latest` is refused
outright, and so is an empty `-c backend_image_tag=` that would otherwise have pinned nothing
while looking pinned. Then the container — 1 vCPU / 2 GB, plain environment for
non-secrets, `secrets=` for the rest, a container health check on `/health`:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "BackendTaskDef",
            family=f"experimentation-backend-{env_name}",
            cpu=1024,  # 1 vCPU
            memory_limit_mib=2048,  # 2 GB
            task_role=task_role,
            execution_role=execution_role,
        )

        self.container = self.task_definition.add_container(
            "backend",
            image=ecs.ContainerImage.from_ecr_repository(
                self.ecr_repo, tag=self.backend_image_tag
            ),
            ...
            environment={
                "APP_ENV": env_name,
                "PUBLIC_BASE_URL": public_base_url,
                "LOG_LEVEL": "INFO",
                "PYTHONUNBUFFERED": "1",
                "POSTGRES_SERVER": db_host,
                "POSTGRES_DB": "experimentation",
                "POSTGRES_SCHEMA": "experimentation",
                "POSTGRES_PORT": "5432",
                "REDIS_HOST": redis_host,
                "REDIS_PORT": redis_port,
                "REDIS_SSL": "true",
                "RUN_MIGRATIONS": "false",
                "SEED": "",
            },
            secrets={
                "POSTGRES_USER": ecs.Secret.from_secrets_manager(
                    db_credentials, field="username"
                ),
                "POSTGRES_PASSWORD": ecs.Secret.from_secrets_manager(
                    db_credentials, field="password"
                ),
                "SECRET_KEY": ecs.Secret.from_secrets_manager(jwt_secret),
                "FIRST_SUPERUSER_PASSWORD": ecs.Secret.from_secrets_manager(
                    superuser_secret
                ),
                **(
                    {"AUDIT_HMAC_KEY": ecs.Secret.from_secrets_manager(audit_secret)}
                    if audit_secret is not None
                    else {}
                ),
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
            essential=True,
        )
```

Every environment value is something the application refuses to start without in staging
or production. `PUBLIC_BASE_URL` (required at synth) is the hostname the API answers on — the
`Host` header is attacker-controlled, so the app is told its own name rather than trusting
the request. `POSTGRES_SERVER` is the Aurora **writer** endpoint; the reader would refuse
every write. `REDIS_SSL=true` because the replication group refuses plaintext.
`RUN_MIGRATIONS=false` and an empty `SEED` because nothing but the migration task writes to
this database at start-up: an older image refuses to start against a schema a newer release
migrated, so a migrating entrypoint would turn every rollback, replacement and scale-out of
an old revision into a crash loop. `infrastructure/tests/test_api_task_does_not_migrate.py`
pins both values, and the Deploy workflow refuses an API revision that would migrate on start.

**The load balancer and its rules.** Two API target groups, blue and green, identical:
port 8000, health check `/health` every 30 s, 30 s deregistration. The HTTPS listener's
default is the dashboard; two rules send the API's paths to the live API group:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
API_PATH_RULES = (
    (10, ("/api/*",)),
    (11, ("/health", "/health/*", "/metrics")),
)
...
        https_listener_kwargs = dict(
            port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            default_target_groups=[self.dashboard.target_group],
            open=True,
        )
        ...
        live = self.node.try_get_context(API_LIVE_TARGET_GROUP_CONTEXT)
        if live is None:
            live = API_LIVE_TARGET_GROUP_DEFAULT
        live_target_groups = {
            "blue": self.blue_target_group,
            "green": self.green_target_group,
        }
        ...
        for priority, paths in API_PATH_RULES:
            self.https_listener.add_target_groups(
                f"ApiPaths{priority}",
                priority=priority,
                conditions=[elbv2.ListenerCondition.path_patterns(list(paths))],
                target_groups=[live_target_groups[live]],
            )
```

The second rule spells out three patterns instead of `/health*`, because `/health*` would
also match `/healthz` — the dashboard's own liveness path. The rules forward to the group the
`api_live_target_group` context names (default `blue`, right for a new environment), because
CloudFormation cannot know which group is live: CodeDeploy swaps them on every deployment.

The **test listener** is port **8443**, HTTPS, `open=False` (not reachable from the
internet), forwarding to green: during the approval window the new task set can be checked
before any production request reaches it.

**The dashboard service** lives in its own construct:

```python
# 📁 infrastructure/cdk/stacks/dashboard_service.py
        self.service = ecs.FargateService(
            self,
            "Service",
            service_name=f"experimentation-dashboard-{env_name}",
            cluster=cluster,
            task_definition=self.task_definition,
            desired_count=desired_count,
            min_healthy_percent=100,
            max_healthy_percent=200,
            deployment_controller=ecs.DeploymentController(
                type=ecs.DeploymentControllerType.ECS
            ),
            circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
            security_groups=[self.security_group],
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            ),
            assign_public_ip=False,
            health_check_grace_period=Duration.seconds(30),
        )
```

0.25 vCPU / 0.5 GB tasks, the image from the ECR repository `experimentation-platform/web`
(tag `bootstrap` by default, pinnable with `-c dashboard_image_tag=`, `latest` refused), a
`/healthz` health check, and `API_UPSTREAM=http://127.0.0.1:1`. That last one is deliberate:
the image's nginx proxies `/api/` to `API_UPSTREAM` in Docker Compose (Part I), but on AWS the
ALB owns `/api`, so a request that somehow reaches the dashboard for `/api` gets a 502 rather
than being forwarded somewhere. Min 100% / max 200% means even a one-task dashboard starts
its replacement before stopping the old task. The Deploy workflow rolls the dashboard *after*
the API answers its smoke request, with the same release's image by digest.

**The API service and its deployment group.** The service itself:
`service_name=f"experimentation-backend-{env_name}"`, `desired_count=api_desired_count`,
private subnets, `deployment_controller=CODE_DEPLOY`, 60 s health-check grace. Then the
CodeDeploy application `experimentation-platform-<env>` and its deployment group:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        self.deployment_group = codedeploy.EcsDeploymentGroup(
            self,
            "DeploymentGroup",
            application=self.codedeploy_app,
            deployment_group_name=f"experimentation-{env_name}",
            service=self.fargate_service,
            blue_green_deployment_config=codedeploy.EcsBlueGreenDeploymentConfig(
                listener=self.https_listener,
                test_listener=self.test_listener,
                blue_target_group=self.blue_target_group,
                green_target_group=self.green_target_group,
                # Allow up to 30 minutes of manual approval before auto-rolling
                # back, giving operators time to validate the deployment via the
                # test listener before committing the traffic shift.
                deployment_approval_wait_time=Duration.minutes(30),
                termination_wait_time=Duration.hours(1),
            ),
            deployment_config=codedeploy.EcsDeploymentConfig.CANARY_10_PERCENT_5_MINUTES,
            role=codedeploy_role,
            alarms=self.api_5xx_alarms,
            ignore_poll_alarms_failure=False,
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
                deployment_in_alarm=True,
            ),
        )
```

Read it line by line:

- **`deployment_approval_wait_time`** — CodeDeploy waits up to 30 minutes for the shift to
  be approved. The Deploy workflow approves it itself once every replacement task is healthy
  in green (Part IV); nobody clicks a button in the console.
- **`CANARY_10_PERCENT_5_MINUTES`** — 10% for 5 minutes, then 100% (to be lengthened to 15
  minutes before production, as above).
- **`termination_wait_time`** — blue stays alive for an hour after the shift.
- **`alarms`** — `experimentation-api-5xx-blue-<env>` and `experimentation-api-5xx-green-<env>`:
  a breaching minute is at least 5 target 5xx **and** at least 5% of that group's requests;
  two breaching minutes of three fire it. Both colours, because blue and green swap on every
  deployment, so an alarm on one would watch the wrong group half the time — and because a
  live release *already* failing at that rate should stop a new deploy too.
- **`ignore_poll_alarms_failure=False`** — if CodeDeploy cannot read the alarms, the
  deployment stops. It fails closed.
- **`deployment_in_alarm=True`** — an alarm stops the deployment *and* rolls it back, rather
  than leaving a half-shifted group. Each 5xx alarm also emails the monitoring topic
  (`ALARM_EMAIL`, Part III), so an automatic rollback never happens silently.

The same stack builds the alarms the deployment group does *not* use — per-colour
healthy-host alarms, a composite "no healthy API task" alarm that emails, and an ERROR-log
alarm — which Part III covers.

**Auto-scaling** — the floor is the environment's task count:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        scaling = self.fargate_service.auto_scale_task_count(
            min_capacity=api_desired_count,
            max_capacity=10,
        )

        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=Duration.seconds(60),
            scale_out_cooldown=Duration.seconds(30),
        )

        scaling.scale_on_memory_utilization(
            "MemoryScaling",
            target_utilization_percent=80,
            scale_in_cooldown=Duration.seconds(60),
            scale_out_cooldown=Duration.seconds(30),
        )
```

`min_capacity=api_desired_count`: prod 3, staging 2, dev 2, demo 1, up to 10. A fixed floor
of 3 would make Application Auto Scaling hold staging at 3 whatever `desired_count` said.
Scale-out is aggressive (30 s cooldown), scale-in conservative (60 s). With
`WEB_CONCURRENCY=1` per task (Part I), "scale tasks" is the only scaling axis — and every task
runs the five scheduler loops, coordinated by the advisory lock so each tick runs once
(Part III).

> **Watch Out!** Run `scripts/check_live_target_group.py --env <env>` before **every**
> `cdk deploy` of `experimentation-fargate-<env>` on a running environment. After an odd
> number of deployments green is live and blue is empty; a `cdk deploy` with the default
> `api_live_target_group=blue` would rewrite the API rules to an empty target group — every
> API request answers 503 while `/` and the dashboard's probes stay green. The script is
> read-only (four describe calls); it prints the value to pass as
> `-c api_live_target_group=<blue|green>`, and refuses outright while a deployment is in
> flight. Pin the images too (`-c backend_image_tag=…`, `-c dashboard_image_tag=sha256:…`),
> or the dashboard — which *is* re-pointed by a `cdk deploy`, being ECS-controlled — goes
> back to `web:bootstrap`.

> **Watch Out!** Some of this is intent that has not yet been observed in a real account:
> whether a CodeDeploy traffic shift rewrites the listener's **path rules** or only its
> default action (the Deploy workflow checks afterwards that `/api/*` forwards to the new
> task set's group, and fails if not), whether the hour after the shift is alarm-monitored,
> and what happens to an alarm already in ALARM before approval. Also note what the canary
> does *not* judge: only the 5xx alarms stop it. A release that answers wrongly with a 200,
> or with fewer errors than the alarm needs, goes to 100% — and because the alarm needs two
> of three minutes plus metric delay, a bad release is often caught after the shift rather
> than inside the five minutes.

> **Did You Know?** The stack's `Service.taskDefinition` keeps naming the `bootstrap`
> revision CloudFormation created, for the life of the service: it can only be changed with
> `UpdateService`, which ECS refuses on a CodeDeploy service. The revision actually serving is
> the PRIMARY task set's. Anything that reads "the service's task definition" to find the
> current release reads the wrong one — which is why the rollback tooling reads the task set
> (Part IV).

### Try It Yourself

With the Hour 4 variables exported:

```bash
cd infrastructure/cdk && ENVIRONMENT=prod ALARM_EMAIL=ops@your-domain.com \
  JWT_SECRET_ARN=arn:aws:secretsmanager:us-west-2:123456789012:secret:/prod/experimentation/jwt-secret-AbCdEf \
  FIRST_SUPERUSER_PASSWORD_SECRET_ARN=arn:aws:secretsmanager:us-west-2:123456789012:secret:/prod/experimentation/first-superuser-password-AbCdEf \
  AUDIT_HMAC_KEY_SECRET_ARN=arn:aws:secretsmanager:us-west-2:123456789012:secret:/prod/experimentation/audit-hmac-key-AbCdEf \
  cdk synth experimentation-fargate-prod > /tmp/f.yaml
grep -n "DeploymentController\|CODE_DEPLOY\|CodeDeployDefault" /tmp/f.yaml
grep -n -A3 "Secrets:" /tmp/f.yaml | head -30          # ValueFrom: complete ARNs, never values
grep -n "RUN_MIGRATIONS" -A1 /tmp/f.yaml               # "false"
grep -n "PathPattern\|Priority" -A3 /tmp/f.yaml | head -30
grep -n "MinCapacity\|MaxCapacity\|TargetValue" /tmp/f.yaml
grep -n "experimentation-api-5xx" /tmp/f.yaml
cdk synth -c backend_image_tag=latest experimentation-fargate-prod 2>&1 | grep -m1 latest   # refused
```

Then repeat the `MinCapacity` grep with `ENVIRONMENT=staging` (and `/staging/` ARNs): 2.

### Key takeaways

- One ALB, HTTPS only (80 redirects): `/api/*`, `/health`, `/health/*`, `/metrics` → the
  API's live target group; everything else → the dashboard service. Test listener 8443,
  closed to the internet. You create the DNS alias record.
- API: Fargate, 1 vCPU / 2 GB, CodeDeploy blue/green — `CANARY_10_PERCENT_5_MINUTES` at the
  reference commit (to be 15 minutes before production), 30-minute approval wait (approved
  by the Deploy workflow), blue kept one hour, rolled back automatically by either 5xx alarm.
- Dashboard: its own ECS service, rolling deploy with circuit breaker, `/healthz`, deployed
  after the API by the same workflow.
- Secrets by **complete ARN** (`JWT_SECRET_ARN`, `FIRST_SUPERUSER_PASSWORD_SECRET_ARN`, and
  `AUDIT_HMAC_KEY_SECRET_ARN` for full), checked at synth; recreate a secret → new ARN →
  update the input and `cdk deploy` again. Database credentials from Aurora's own secret;
  no Redis secret.
- The stack's image is `bootstrap` (never `latest`); the pipeline deploys by digest. The API
  runs `RUN_MIGRATIONS=false`: `cdk deploy` alone leaves no schema until the first Deploy.
- Scale from the environment's task count to 10. Run `scripts/check_live_target_group.py`
  before any `cdk deploy` of the Fargate stack on a running environment.

---

## Hour 6 — Data services: Aurora, ElastiCache, DynamoDB, Kinesis → Firehose → S3 → Glue, OpenSearch

### What & why

Each data service has a distinct job. **Aurora PostgreSQL** is the system of record —
experiments, assignments, events, users, everything the Build book's models describe.
**ElastiCache Redis** is the results cache and the distributed rate limiter's store — both
degrade gracefully without it. **DynamoDB** holds, in a full checkout, the real-time counters
the bandit scheduler reads first, plus five tables from the original design that nothing
uses. The **Kinesis → Firehose → S3** pipeline (full profile) is the analytics tap: events
land in a partitioned, gzipped data lake that **Glue** turns into Parquet nightly and Athena
queries; **OpenSearch** indexes events for ad-hoc search. Only Aurora and Redis are on the
hot path; a failure in the others is an analytics gap, not an outage.

### How Experimently does it

**Aurora** — encrypted, in the isolated subnet in prod, sized by environment:

```python
# 📁 infrastructure/cdk/stacks/enhanced_database_stack.py
AURORA_POSTGRES_VERSION = rds.AuroraPostgresEngineVersion.VER_15_17
...
        instance_count = aurora_instance_count(environment)

        instance_type = (
            ec2.InstanceType.of(ec2.InstanceClass.MEMORY5, ec2.InstanceSize.LARGE)
            if environment == "prod"
            else ec2.InstanceType.of(
                ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MEDIUM
            )
        )

        self.aurora_cluster = rds.DatabaseCluster(
            self,
            "AuroraCluster",
            engine=rds.DatabaseClusterEngine.aurora_postgres(
                version=AURORA_POSTGRES_VERSION
            ),
            credentials=rds.Credentials.from_secret(db_credentials),
            default_database_name="experimentation",
            instances=instance_count,
            instance_props=rds.InstanceProps(
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=(
                        ec2.SubnetType.PRIVATE_ISOLATED
                        if environment == "prod"
                        else ec2.SubnetType.PRIVATE_WITH_EGRESS
                    )
                ),
                instance_type=instance_type,
                security_groups=[rds_security_group],
                parameter_group=db_instance_parameter_group,
            ),
            parameter_group=db_cluster_parameter_group,
            subnet_group=db_subnet_group,
            storage_encrypted=True,
            storage_encryption_key=db_encryption_key,
            removal_policy=database_removal_policy(environment),
            backup=rds.BackupProps(
                retention=Duration.days(aurora_backup_retention_days(environment))
            ),
        )
```

PostgreSQL 15.17 (`AURORA_POSTGRES_VERSION`, pinned by a test: 15.3 is no longer orderable).
A writer and a reader in prod (`r5.large`), one `t3.medium` writer everywhere else. The
credentials are the secret Aurora generated at creation (`<stack>-aurora-credentials`, a
32-character password); the API task reads `username` and `password` from it, and connects
to the **writer** endpoint as `POSTGRES_SERVER`. Host, port and secret ARN also go to SSM
parameters under `/experimentation/<env>/database/` so operators and other stacks can find
them without hard-coding. `database_removal_policy` is SNAPSHOT in prod (a stack deletion
leaves a final snapshot) and DESTROY elsewhere — a snapshot is itself a billed resource that
a disposable environment should not leave behind.

**Backups — what exists and for how long.**

| What | prod | staging | dev / demo | Where it's set |
|---|---|---|---|---|
| Aurora automated backups (and so point-in-time restore window) | 35 days | 35 days | 1 day | `aurora_backup_retention_days`, `stacks/environments.py` |
| Aurora snapshot before every deploy's migration | yes | yes | — | Deploy workflow, `pre-deploy-<env>-<tag>-<time>` |
| Aurora final snapshot on stack deletion | yes | no | no | `database_removal_policy` |
| Redis snapshots | 7 days | 3 days | 1 day | `elasticache_redis_stack.py` |
| DynamoDB counters point-in-time recovery | on | on | on | `dynamodb_counters_stack.py` (full) |

35 days is the most Aurora allows; the value is set explicitly in every environment rather
than left to CloudFormation's default of 1, and lowering it later discards the older backups.
*Restoring* from any of these is an incident procedure with its own runbook in the app
repository (`docs/deployment/disaster-recovery.md`, `docs/deployment/rollback-runbook.md`). Its
restart step does not work on the CodeDeploy-controlled API service (pending #789), so until
that is fixed, stop and escalate to whoever owns the environment rather than run it. Part IV
says when you would reach for it, and this book does not reproduce its commands.

**Redis** — a replication group with failover when there's more than one node, encrypted in
transit and at rest, nightly snapshots:

```python
# 📁 infrastructure/cdk/stacks/elasticache_redis_stack.py
        cluster_size = redis_node_count(environment)
        instance_type = self._get_instance_type_for_env(environment)
        ...
        self.redis_cluster = elasticache.CfnReplicationGroup(
            self,
            "RedisCluster",
            replication_group_id=self.replication_group_id,
            ...
            engine="redis",
            engine_version="7.0",
            port=6379,
            cache_node_type=instance_type,
            num_cache_clusters=cluster_size,
            automatic_failover_enabled=cluster_size > 1,
            auto_minor_version_upgrade=True,
            multi_az_enabled=cluster_size > 1,
            ...
            snapshot_retention_limit=self._get_snapshot_retention_for_env(environment),
            snapshot_window="02:00-03:00",  # UTC
            preferred_maintenance_window="sun:04:00-sun:05:00",  # UTC
            at_rest_encryption_enabled=True,
            transit_encryption_enabled=True,
        )
        self.primary_host = self.redis_cluster.attr_primary_end_point_address
        self.primary_port = self.redis_cluster.attr_primary_end_point_port
```

Sizing: prod `cache.r6g.large` × 3 (a primary and two replicas, so failover and multi-AZ are
on); staging `cache.t4g.small` × 1; dev and demo `cache.t4g.medium` × 1. The replication
group has **no AUTH token**, and there is no Redis secret: the API task gets the **primary**
endpoint (the rate limiter `INCR`s and the caches `SET`, so a reader endpoint would fail every
write) as `REDIS_HOST`/`REDIS_PORT`, and `REDIS_SSL=true` because
`transit_encryption_enabled=True` refuses a plaintext connection. The literal
`replication_group_id` matters to Part III: its nodes are `<id>-001` to `<id>-00N`, and the
monitoring stack builds one CPU alarm per node from that.

**DynamoDB counters** (full profile, the `counters` module) — the table the bandit scheduler
reads first, named `experiment-counters-<env>`, keyed `pk`/`sk`, on-demand billing,
point-in-time recovery, TTL for cleanup, and a GSI so dashboards can query by experiment
without knowing the key format:

```python
# 📁 modules/infrastructure/cdk/stacks/dynamodb_counters_stack.py
        self.counters_table = dynamodb.Table(
            self,
            "ExperimentCountersTable",
            table_name=self.table_name,
            partition_key=dynamodb.Attribute(
                name="pk",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="sk",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
            point_in_time_recovery=True,
            # TTL enables automatic cleanup of old experiment data
            time_to_live_attribute="expires_at",
        )
        self.counters_table.add_global_secondary_index(
            index_name="experiment-id-index",
            ...
        )
```

The Fargate stack gives the API task the same name (`DYNAMODB_COUNTERS_TABLE`) and read/write
access to it, both from one function in `stacks/names.py`, so the two cannot disagree. (The
core `dynamodb` stack still creates the five tables of the original design —
`experimentation-assignments-<env>`, `-events-`, `-experiments-`, `-feature-flags-`,
`-overrides-` — now named per environment. Nothing uses them.)

**The event pipeline** (full profile, the `etl` module) — a Kinesis stream with 24-hour
retention, a Firehose delivery stream that batches into S3 with date partitions and gzip,
errors to their own prefix:

```python
# 📁 modules/infrastructure/cdk/stacks/analytics_stack.py
        self.events_stream = kinesis.Stream(
            self,
            "EventsStream",
            shard_count=1,  # Increase for production
            retention_period=Duration.hours(24),
            removal_policy=data_removal_policy(env_name),
        )
        ...
        delivery_stream = firehose.CfnDeliveryStream(
            self,
            "EventsDeliveryStream",
            delivery_stream_type="KinesisStreamAsSource",
            kinesis_stream_source_configuration=firehose.CfnDeliveryStream.KinesisStreamSourceConfigurationProperty(
                kinesis_stream_arn=self.events_stream.stream_arn,
                role_arn=firehose_role.role_arn,
            ),
            s3_destination_configuration=firehose.CfnDeliveryStream.S3DestinationConfigurationProperty(
                bucket_arn=self.data_lake_bucket.bucket_arn,
                buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(
                    interval_in_seconds=60, size_in_m_bs=5
                ),
                compression_format="GZIP",
                prefix="raw/events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/",
                error_output_prefix="errors/!{firehose:error-output-type}/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/",
                role_arn=firehose_role.role_arn,
            ),
        )
```

The `year=/month=/day=` prefix is Hive-style partitioning: Glue's crawler turns it into
partition columns, and an Athena query with `WHERE year='2026' AND month='09'` reads only
those objects. `shard_count=1` is the flagged production knob — one shard is 1 MB/s or 1,000
records/s in; the comment says what to raise. The same stack creates the OpenSearch domain
and the `AnalyticsLambda` placeholder consumer.

**Glue** — nightly JSON → Parquet, metrics aggregation, a crawler, EventBridge to trigger it,
every name carrying the environment:

```python
# 📁 modules/infrastructure/cdk/stacks/glue_etl_stack.py
"""
AWS CDK Stack: GlueETLStack (P3-A)

Provisions the full Glue ETL infrastructure for the experimentation platform:

Resources created:
    1. Glue IAM Role          — Glue service principal with S3, Glue catalog, and
                                CloudWatch permissions.
    2. Glue Database          — 'experimentation_<env>' catalog database.
    3. Glue ETL Job           — 'experimentation-events-etl-<env>' (JSON → Parquet).
    4. Glue Metrics Job       — 'experimentation-metrics-etl-<env>' (metric aggregation).
    5. Glue Crawler           — 'experimentation-crawler-<env>' for raw_events table.
    6. EventBridge Rule       — Daily cron trigger at 02:00 UTC.
    7. Lambda Invoker         — Lambda function that calls glue:StartJobRun from
                                EventBridge events.
    8. Athena Results Bucket  — S3 bucket for Athena query output.
"""
```

Item 7 is the `ETLTriggerLambda`, a real inline function. The database name uses an
underscore because Athena does not accept a hyphen there.

> **Watch Out!** The analytics stack sets **no explicit physical names** on the stream, the
> bucket, the delivery stream or the OpenSearch domain. They used to be random names drawn
> at synth time — so every `cdk deploy` replaced the data lake, and the retained old bucket
> was orphaned. Now CloudFormation derives each name from the stack name and logical ID:
> stable across synths, unique per account, but still not something to guess. Anything that
> needs a name (a runbook, a query) reads it from the stack's resources or outputs, and
> deleting and recreating the stack still gives new ones.

> **By the Way** — `REDIS_REQUIRED=false` (Part I) is a production decision too. With it
> false, a Redis failover (30–60 s) shows as a readiness _warning_ and slightly higher
> database load while the results cache is cold; with it true, every task goes unready and
> the ALB has nothing to route to. The platform's default is the graceful one; a deployment
> that relies on Redis for the distributed rate limiter across many tasks may choose the
> strict one.

### Try It Yourself

With the Hour 4 variables exported and the `/prod/` ARNs from Hour 5's Try It:

```bash
cd infrastructure/cdk && ENVIRONMENT=prod ALARM_EMAIL=ops@your-domain.com cdk synth > /tmp/all.yaml 2>/dev/null
grep -c "AWS::RDS::DBInstance" /tmp/all.yaml                       # 2 in prod
grep -n "BackupRetentionPeriod" /tmp/all.yaml                      # 35
grep -n "TransitEncryptionEnabled\|AtRestEncryptionEnabled\|SnapshotRetentionLimit" /tmp/all.yaml
grep -n "TimeToLiveSpecification" -A2 /tmp/all.yaml | head
grep -n "Prefix: raw/events" /tmp/all.yaml
grep -n "ScheduleExpression" /tmp/all.yaml                         # the 02:00 UTC cron
grep -n "DeletionPolicy" /tmp/all.yaml | sort | uniq -c            # Retain / Snapshot in prod
```

Repeat with `ENVIRONMENT=dev` (and `/dev/` ARNs) and compare: one instance, `BackupRetentionPeriod: 1`,
every `DeletionPolicy: Delete`.

Then, locally, start the `aws` profile and point the API at LocalStack
(`AWS_ENDPOINT_URL=http://localstack:4566`) to exercise the DynamoDB counters path without
an account: `docker compose --profile aws up -d --wait`.

### Key takeaways

- Aurora PostgreSQL 15.17, encrypted; prod: isolated subnets, writer + reader `r5.large`,
  final snapshot on delete; elsewhere one `t3.medium`, destroyed with the stack. Credentials
  from Aurora's own generated secret; the API uses the writer endpoint.
- Backups: Aurora 35 days in prod and staging, 1 elsewhere; a snapshot before every deploy's
  migration; Redis snapshots 7 / 3 / 1 days.
- Redis 7 replication group, TLS and at-rest encryption, no AUTH token and no secret; prod 3
  nodes with failover, everything else 1.
- Full profile only: DynamoDB counters (`pk`/`sk`, on-demand, PITR, TTL, GSI); Kinesis
  (1 shard, 24 h) → Firehose (60 s / 5 MB, gzip, Hive partitions, error prefix) → S3 → Glue
  nightly → Athena; OpenSearch. CloudFormation-generated names → read them, don't guess.

---

## Part II Workshop

**Q1. Why are the application secrets imported by complete ARN and not created by the
Fargate stack?**
Not created: so their values never pass through CloudFormation templates or state. By
complete ARN: a name renders a partial ARN into `valueFrom`, which Secrets Manager does not
resolve, so no task would start. Synth refuses a missing, partial, wrong-account,
wrong-region or wrong-environment ARN, and recreating a secret means updating the input and
running `cdk deploy` again.

**Q2. Trace a deploy at the infrastructure level: what does CodeDeploy do with the two
target groups?**
Starts green tasks registered to the (idle) green group; the test listener on 8443 points at
green; CodeDeploy waits (up to 30 minutes) for the shift to be approved — the Deploy workflow
approves once green is healthy; 10% of the API's traffic shifts to green for 5 minutes, then
the rest. Throughout, both 5xx alarms are polled; either one in ALARM stops and rolls back.
Blue stays (idle) for an hour, then is terminated. Next deploy, the roles swap.

**Q3. What does `database_removal_policy` give each environment, and why the difference?**
SNAPSHOT in prod: a `cdk destroy` or stack replacement leaves a final snapshot instead of
deleting the data. DESTROY elsewhere: a snapshot is a billed resource a disposable staging or
dev environment should not leave behind, and a rebuilt environment must not collide with its
predecessor.

**Q4. Why is the ALB health check `/health` and the image's `/health/live`?**
The ALB should pull a task with a broken DB connection out of rotation (readiness); the
container runtime should not restart a healthy process because the DB blipped (liveness).

**Q5. A Firehose delivery to S3 fails to parse a record. Where does it go?**
`errors/<error-type>/year=/month=/day=/` in the data lake bucket — separate from
`raw/events/`, so the crawler's `raw_events` table stays clean and the failures are
findable.

**Q6. Where does the bandit scheduler get its counts in production, and what happens if
DynamoDB is unreachable?**
In a full deployment, the DynamoDB counters table first; on failure, Postgres (assignments +
primary-metric conversions), then `BanditState`, then priors. A core deployment has no
counters table and starts at Postgres. Weights still update; they're just computed from the
slower source.

**Quiz.** `min_capacity=api_desired_count` and `WEB_CONCURRENCY=1`: at the floor, how many
scheduler loops exist in prod and in staging, and how many run each tick?
→ prod: 3 tasks × 5 schedulers = 15 loops; each tick exactly one task runs each scheduler
(the advisory lock), so 5 run and 10 skip. Staging: 2 × 5 = 10 loops, 5 run, 5 skip. Demo:
1 × 5 = 5, all run.

---

## Exercises

**E2.1 — Stack diff.** Synth `dev` and `prod` and produce a table of every property that
differs across the vpc, database, redis, fargate and dynamodb stacks (instance classes,
counts, subnet types, removal and deletion policies, backup retention, billing modes).

**E2.2 — A fourth secret.** Add `SLACK_BOT_TOKEN` from `/{env}/experimentation/slack-token`
to the API task. List every place a new secret must appear, from the synth input to the
container.

**E2.3 — Shard maths.** ShopLab and StreamPulse simulators at full rate plus a real product
produce 3,000 events/s of ~600 bytes. How many Kinesis shards, and what Firehose buffering
would you set? Show the arithmetic against the 1 MB/s / 1,000 rec/s per-shard limits.

**E2.4 — Isolation test.** Explain, with the subnet types and the VPC endpoints, why a prod
Aurora instance can't reach the internet, and how the migration task — run in the subnets
and security group the Fargate stack outputs — reaches Aurora and pulls its image.

**E2.5 — The careless `cdk deploy`.** Production has had three deploys since the stack was
created; revision 42 of `experimentation-backend-prod`, naming its image by digest, is
serving. Someone runs `cdk deploy experimentation-fargate-prod` with no `-c` options. What
does and does not change, what breaks, and what should they have run first?

---

## Solutions

**S2.1** — vpc: NAT gateways 1 vs 2. database: instances 1 vs 2; `t3.medium` vs `r5.large`;
`PRIVATE_WITH_EGRESS` vs `PRIVATE_ISOLATED`; DESTROY vs SNAPSHOT; backup retention 1 vs 35
days. redis: `cache.t4g.medium` × 1 vs `cache.r6g.large` × 3; failover and multi-AZ only in
prod; snapshots 1 vs 7 days. fargate: API desired count and scaling floor 2 vs 3; dashboard
1 vs 2; named log groups DESTROY vs RETAIN. dynamodb (and counters, analytics in a full
checkout): DESTROY vs RETAIN; counters on-demand in both.

**S2.2** — (1) `stacks/secret_arns.py`: a new input variable (say `SLACK_TOKEN_SECRET_ARN`)
in `SECRET_INPUTS`, mapped to `slack-token`, so `app.py` validates it; (2) export the
complete ARN before every synth (`aws secretsmanager describe-secret … --query ARN`), in CI
and by hand; (3) `secretsmanager.Secret.from_secret_complete_arn(self, "SlackSecret",
secret_arns["SLACK_TOKEN_SECRET_ARN"])` and `required_secrets.append(...)`, which grants both
the task role and the execution role `GetSecretValue`; (4) `"SLACK_BOT_TOKEN":
ecs.Secret.from_secrets_manager(slack_secret)` in `secrets=`; (5) the migration task stack
too, if the setting is read when the settings import (it builds the same settings);
(6) `docs/deployment/secrets-management.md` and the first-deploy checklist. Six places —
which is why the documents exist, and why the Deploy workflow checks each task definition's
secret references before it starts anything.

**S2.3** — 3,000 × 600 B = 1.8 MB/s and 3,000 rec/s → 3 shards on records (the binding
constraint), 4 for headroom. Firehose at 5 MB / 60 s flushes every ~3 s on size; raise to
64 MB / 300 s for fewer, larger Parquet-friendly objects, accepting five minutes of latency
into the lake.

**S2.4** — `PRIVATE_ISOLATED` subnets have no route to a NAT or IGW; nothing in or out of
the VPC except via endpoints. The migration task runs in the subnets the Fargate stack
outputs (`TaskSubnets`, `PRIVATE_WITH_EGRESS`) and in the ECS tasks' security group, which
the Fargate stack admits to Aurora on 5432 — the same rule that admits the API. It pulls its
image through the VPC's ECR and ECR-Docker interface endpoints plus the S3 gateway endpoint
(image layers are served from S3), writes logs through the CloudWatch Logs endpoint, and
reads its secrets through the Secrets Manager endpoint: none of it needs the NAT.

**S2.5** — What does *not* change: the API's running revision. ECS refuses a task-definition
change on a CodeDeploy-controlled service, and the stack's revision names `bootstrap`
anyway. What changes: (a) the listener's API rules are written against `blue`, the default —
after three deploys green is live and blue is empty, so every `/api/*` request answers 503
while `/` and the dashboard's health checks stay green, and the next CodeDeploy deployment
fails too; (b) the dashboard service, which ECS (not CodeDeploy) deploys, is re-pointed at
`web:bootstrap`. What they should have run: `scripts/check_live_target_group.py --env prod`,
then `cdk diff` and `cdk deploy` with `-c api_live_target_group=green`,
`-c backend_image_tag=<the tag in service>` and `-c dashboard_image_tag=sha256:<digest in
service>` (`scripts/check_dashboard_image.py` prints it) — and read the diff before
confirming.

---

**Next:** [Part III — Observability →](./part-3-observability.md)

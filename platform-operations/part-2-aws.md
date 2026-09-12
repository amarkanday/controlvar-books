# Part II — AWS

### Hours 4–6

[← Part I](./part-1-local-stack.md) · [Contents](./README.md) · [Part III →](./part-3-observability.md)

The same images, on AWS. Hour 4 is the map: fourteen CDK stacks in Python, the environment
switch that sizes them, and the dependency order that `cdk deploy --all` respects. Hour 5 is
the request path: an internet-facing ALB in front of ECS Fargate tasks that CodeDeploy
replaces blue/green with a 10% canary, secrets injected from Secrets Manager, auto-scaling on
CPU and memory. Hour 6 is everything the API talks to: Aurora PostgreSQL, ElastiCache Redis,
DynamoDB tables and counters, the Kinesis → Firehose → S3 event pipeline with Glue on top,
and an OpenSearch domain. Read it with `cdk synth` open; every claim here is a line in a
stack.

---

## Hour 4 — The CDK app: fourteen stacks and the order they depend on

### What & why

Infrastructure-as-code has the same two virtues as application code: it's reviewable and
it's reproducible. Experimently's is the AWS CDK in Python — the same language as the
backend, so the same engineers can read it — organised as **one stack per concern** so a
change to monitoring doesn't touch the database, and CloudFormation can roll back one thing
at a time. The app file wires them with explicit `add_dependency` calls; CDK then deploys in
topological order.

The environment is a single `ENVIRONMENT` variable (`dev`, `staging`, `prod`, `demo`) that
every stack reads to size itself: instance classes, replica counts, removal policies (destroy
in dev, retain in prod), subnet isolation. `demo` is the interesting one — a full, cheap
environment for sales demos: one Fargate task, `db.t3.small`, `cache.t3.micro`.

### How Experimently does it

The entrypoint — environment validation, demo sizing, account from credentials only:

```python
# 📁 infrastructure/cdk/app.py
# Environment determination
VALID_ENVIRONMENTS = ["dev", "staging", "prod", "demo"]
env_name = os.environ.get("ENVIRONMENT", "dev")
if env_name not in VALID_ENVIRONMENTS:
    raise ValueError(f"Invalid ENVIRONMENT '{env_name}'. Must be one of: {', '.join(VALID_ENVIRONMENTS)}")

# Demo environment uses smaller instance sizing to reduce cost
is_demo = env_name == "demo"
db_instance_size = "db.t3.small" if is_demo else "db.t3.medium"
cache_node_type = "cache.t3.micro" if is_demo else "cache.t3.small"
fargate_desired_count = 1 if is_demo else 2

# Define CDK environment (account and region)
# Account/region come from the environment only: CDK sets CDK_DEFAULT_ACCOUNT
# from the active credentials; AWS_ACCOUNT_ID is what CI (vars.AWS_ACCOUNT_ID)
# and demo/setup-aws.sh export. No account id is hard-coded in the repository.
account = os.environ.get("CDK_DEFAULT_ACCOUNT") or os.environ.get("AWS_ACCOUNT_ID")
region = os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION", "us-west-2")
if not account:
    raise SystemExit(
        "AWS account id not set: export AWS_ACCOUNT_ID (or let the CDK CLI set "
        "CDK_DEFAULT_ACCOUNT from your credentials) before synthesizing."
    )
```

"No account id is hard-coded in the repository" is a security property and a portability
property at once: the repo can be public, and a customer can deploy it into their own
account by exporting one variable.

The stacks and their dependencies, in the order the file declares them:

```python
# 📁 infrastructure/cdk/app.py
auth_stack = AuthenticationStack(app, f"experimentation-auth-{env_name}", environment=env_name, env=env)
vpc_stack = VpcStack(app, f"experimentation-vpc-{env_name}", env=env)
dynamodb_stack = DynamoDBTablesStack(app, f"experimentation-dynamodb-{env_name}", environment=env_name, env=env)
dynamodb_counters_stack = DynamoDBCountersStack(app, f"experimentation-dynamodb-counters-{env_name}", environment=env_name, env=env)
database_stack = EnhancedDatabaseStack(app, f"experimentation-database-{env_name}", vpc=vpc_stack.vpc, environment=env_name, env=env)
database_stack.add_dependency(vpc_stack)
redis_stack = ElastiCacheRedisStack(app, f"experimentation-redis-{env_name}", vpc=vpc_stack.vpc, environment=env_name, env=env)
redis_stack.add_dependency(vpc_stack)
compute_stack = ComputeStack(app, f"experimentation-compute-{env_name}", vpc=vpc_stack.vpc, env=env)
compute_stack.add_dependency(vpc_stack)
compute_stack.add_dependency(database_stack)
compute_stack.add_dependency(dynamodb_stack)
compute_stack.add_dependency(redis_stack)  # Add dependency on Redis stack
api_stack = ApiStack(app, f"experimentation-api-{env_name}", vpc=vpc_stack.vpc, compute=compute_stack, env=env)
api_stack.add_dependency(compute_stack)
analytics_stack = AnalyticsStack(app, f"experimentation-analytics-{env_name}", vpc=vpc_stack.vpc, env=env)
analytics_stack.add_dependency(vpc_stack)
analytics_stack.add_dependency(dynamodb_stack)
monitoring_stack = MonitoringStack(app, f"experimentation-monitoring-{env_name}", vpc=vpc_stack.vpc, env=env)
monitoring_stack.add_dependency(vpc_stack)
...
# EP-019: Production Deployment — ECS Fargate + ALB + Blue/Green + Migrations
fargate_stack = FargateServiceStack(app, f"experimentation-fargate-{env_name}", vpc=vpc_stack.vpc, ecs_cluster=compute_stack.ecs_cluster, ecs_security_group=compute_stack.ecs_security_group, env_name=env_name, certificate_arn=certificate_arn, env=env)
fargate_stack.add_dependency(compute_stack)
fargate_stack.add_dependency(database_stack)
fargate_stack.add_dependency(redis_stack)
migration_stack = MigrationTaskStack(app, f"experimentation-migrations-{env_name}", ecs_cluster=compute_stack.ecs_cluster, env_name=env_name, env=env)
migration_stack.add_dependency(fargate_stack)
migration_stack.add_dependency(database_stack)
glue_etl_stack = GlueETLStack(app, f"experimentation-glue-etl-{env_name}", data_lake_bucket=analytics_stack.data_lake_bucket, env_name=env_name, env=env)
glue_etl_stack.add_dependency(analytics_stack)

app.synth()
```

Read as a layered map:

| Layer | Stacks | Depends on |
|---|---|---|
| Identity & network | `auth` (Cognito), `vpc` | — |
| Data | `database` (Aurora), `redis`, `dynamodb`, `dynamodb-counters` | vpc |
| Compute | `compute` (ECS cluster, placeholder Lambdas, security groups) | vpc, database, dynamodb, redis |
| Serving | `fargate` (ALB, service, CodeDeploy, scaling), `api` (API Gateway → Lambdas), `migrations` (one-shot task) | compute (+ database, redis) |
| Analytics | `analytics` (Kinesis, Firehose, S3, OpenSearch), `glue-etl` | vpc, dynamodb; analytics |
| Observability | `monitoring` (dashboard, alarms, SNS) | vpc |

The VPC — two AZs, a NAT per AZ, three subnet tiers, and gateway/interface endpoints so
private tasks reach S3, DynamoDB and Secrets Manager without crossing the NAT:

```python
# 📁 infrastructure/cdk/stacks/vpc_stack.py
            max_azs=2,
            nat_gateways=2,  # One NAT Gateway per AZ for high availability
            ...
            subnet_configuration=[
                ...subnet_type=ec2.SubnetType.PUBLIC,
                ...subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                ...subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
            ],
            gateway_endpoints={
                "S3": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.S3),
                "DynamoDB": ec2.GatewayVpcEndpointOptions(service=ec2.GatewayVpcEndpointAwsService.DYNAMODB),
            },
        )
        self.vpc.add_interface_endpoint("SecretsManagerEndpoint", service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER, ...)
```

`PRIVATE_ISOLATED` is where the production database lives (Hour 6): no route to the
internet at all, in either direction.

> **By the Way** — the `compute` stack still creates the **placeholder** assignment Lambda
> (SDK book, Hour 9) and the `api` stack routes API Gateway to it. The real request path for
> every SDK and the dashboard is ALB → Fargate → FastAPI. When you read the stack list, keep
> that in mind: `api` is the Lambda/API-Gateway surface from the original architecture;
> `fargate` is the one serving traffic.

> **Watch Out!** `cdk deploy --all` on a fresh account needs `cdk bootstrap` first, and the
> Fargate stack references three Secrets Manager secrets **by name** that must exist before
> it deploys (Hour 12). The stack doesn't create them — a deliberate choice so secret values
> never pass through CloudFormation — and it fails clearly if they're missing.

### Try It Yourself

```bash
cd infrastructure/cdk && pip install -r requirements.txt
export AWS_ACCOUNT_ID=123456789012 ENVIRONMENT=demo
cdk ls                                              # the stack names, suffixed -demo
cdk synth experimentation-fargate-demo > /tmp/fargate.yaml && grep -c "AWS::" /tmp/fargate.yaml
grep -n "DesiredCount\|Cpu:\|Memory:" /tmp/fargate.yaml | head
ENVIRONMENT=prod cdk synth experimentation-database-prod | grep -n "DBInstanceClass\|SubnetType\|DeletionProtection\|StorageEncrypted"
ENVIRONMENT=bogus cdk synth 2>&1 | tail -1         # the ValueError
```

(No credentials are needed to synth with `AWS_ACCOUNT_ID` set; nothing is deployed.)

### Key takeaways

- One stack per concern, fourteen in all, wired with explicit `add_dependency`; `ENVIRONMENT`
  ∈ dev/staging/prod/demo sizes everything; no account ID in the repo.
- Layers: identity/network → data → compute → serving/analytics → monitoring.
- The VPC has two AZs, a NAT per AZ, public/private/isolated subnets, and endpoints for S3,
  DynamoDB and Secrets Manager.
- `fargate` serves traffic; `api`/`compute`'s Lambdas are the original placeholder surface.

---

## Hour 5 — Serving traffic: Fargate, ALB, blue/green with CodeDeploy, auto-scaling, secrets

### What & why

The production request path is: DNS → internet-facing ALB (HTTPS; HTTP redirects) → target
group → ECS Fargate tasks in private subnets running the API image → Aurora/Redis. The
interesting engineering is in how tasks get _replaced_. A rolling update swaps tasks
in-place and, if the new version is bad, you find out from users. **Blue/green with a
canary** keeps the old task set ("blue") serving while a new one ("green") starts, shifts
10% of traffic to green, waits five minutes watching health checks and alarms, then shifts
the rest — and keeps blue alive for an hour so rollback is a traffic switch, not a redeploy.

The service hands deployment control to CodeDeploy (`deployment_controller=CODE_DEPLOY`),
which is why the deploy workflow (Hour 11) registers a task definition and calls
`aws deploy create-deployment` rather than `aws ecs update-service`. Secrets reach the
container as environment variables that ECS resolves from Secrets Manager at task start —
the image never contains them and CloudFormation never sees their values.

### How Experimently does it

The task definition — 1 vCPU / 2 GB, plain env for non-secrets, `secrets=` for the three
that matter, and a container health check on `/health`:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        # --- Secrets from Secrets Manager ---
        # These secrets must be created manually (or by another stack) before
        # this stack is deployed.
        db_secret = secretsmanager.Secret.from_secret_name_v2(self, "DbSecret", f"/{env_name}/experimentation/db-password")
        jwt_secret = secretsmanager.Secret.from_secret_name_v2(self, "JwtSecret", f"/{env_name}/experimentation/jwt-secret")
        redis_secret = secretsmanager.Secret.from_secret_name_v2(self, "RedisSecret", f"/{env_name}/experimentation/redis-url")
        ...
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "BackendTaskDef",
            family=f"experimentation-backend-{env_name}",
            cpu=1024,           # 1 vCPU
            memory_limit_mib=2048,  # 2 GB
            task_role=task_role,
            execution_role=execution_role,
        )

        self.container = self.task_definition.add_container(
            "backend",
            image=ecs.ContainerImage.from_ecr_repository(self.ecr_repo, tag="latest"),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="backend", log_group=log_group),
            environment={
                "APP_ENV": env_name,
                "LOG_LEVEL": "INFO",
                "PYTHONUNBUFFERED": "1",
                "POSTGRES_DB": "experimentation",
                "POSTGRES_SCHEMA": "experimentation",
                "POSTGRES_PORT": "5432",
            },
            secrets={
                "POSTGRES_PASSWORD": ecs.Secret.from_secrets_manager(db_secret),
                "SECRET_KEY": ecs.Secret.from_secrets_manager(jwt_secret),
                "REDIS_URL": ecs.Secret.from_secrets_manager(redis_secret),
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

Two roles, least privilege each way: the **task role** is what the running code can do
(`GetSecretValue` on exactly those three secrets, plus whatever the app needs); the
**execution role** is what ECS needs to _start_ the task (pull from ECR, write logs, and
also `GetSecretValue` so it can inject the secrets before the container runs).

The ALB and the two target groups — identical health checks, short deregistration so old
tasks drain fast:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        self.alb = elbv2.ApplicationLoadBalancer(self, "BackendALB", vpc=vpc, internet_facing=True, ...)
        # HTTP (port 80) -> HTTPS (port 443) permanent redirect
        self.alb.add_redirect(source_port=80, ..., target_port=443, target_protocol=elbv2.ApplicationProtocol.HTTPS)

        # --- Blue Target Group (current / stable version) ---
        self.blue_target_group = elbv2.ApplicationTargetGroup(
            self, "BlueTargetGroup",
            ...
            port=8000,
            health_check=elbv2.HealthCheck(
                path="/health",
                interval=Duration.seconds(30),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
            deregistration_delay=Duration.seconds(30),
        )
        # --- Green Target Group (new version during blue/green deployment) ---
        self.green_target_group = elbv2.ApplicationTargetGroup(self, "GreenTargetGroup", ...)
```

`/health` is the readiness alias (Hour 8) — so a task whose database connection is broken
is pulled from rotation, which is what you want from a load balancer check (and _not_ what
you want from a liveness check, which is why the image uses `/health/live`).

The deployment group — canary, approval window, blue retention, automatic rollback:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        # --- CodeDeploy Deployment Group ---
        # Uses CANARY_10_PERCENT_5_MINUTES: routes 10 % of traffic to green,
        # waits 5 minutes for alarms/health checks, then shifts the remaining
        # 90 % if all checks pass.
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
                # Keep the blue (old) task set alive for 1 hour after a
                # successful deployment so that a quick rollback is possible
                # without a full re-deployment.
                terminate_blue_instances_on_deployment_success=codedeploy.InstanceTerminationWaitTime.after(Duration.hours(1)),
            ),
            deployment_config=codedeploy.EcsDeploymentConfig.CANARY_10_PERCENT_5_MINUTES,
            role=codedeploy_role,
            auto_rollback=codedeploy.AutoRollbackConfig(
                failed_deployment=True,
                stopped_deployment=True,
            ),
        )
```

The **test listener** is a second port on the ALB that points at green _before_ any
production traffic does — an operator (or a smoke test) can hit the new version directly
during the approval window. `auto_rollback` on `stopped_deployment=True` is what makes the
deploy workflow's "`aws deploy stop-deployment --auto-rollback-enabled`" a one-line
rollback.

Auto-scaling — asymmetric cooldowns:

```python
# 📁 infrastructure/cdk/stacks/fargate_service_stack.py
        # Scale between 3 (minimum for high availability across 3 AZs) and 10
        # tasks. Scale-out is aggressive (30 s cooldown) to respond quickly to
        # traffic spikes; scale-in is conservative (60 s) to avoid thrashing.
        scaling = self.fargate_service.auto_scale_task_count(min_capacity=3, max_capacity=10)

        scaling.scale_on_cpu_utilization("CpuScaling", target_utilization_percent=70, scale_in_cooldown=Duration.seconds(60), scale_out_cooldown=Duration.seconds(30))
        scaling.scale_on_memory_utilization("MemoryScaling", target_utilization_percent=80, scale_in_cooldown=Duration.seconds(60), scale_out_cooldown=Duration.seconds(30))
```

With `WEB_CONCURRENCY=1` per task (Hour 1), "scale tasks" is the only scaling axis, and
three tasks is the floor. Remember what that means for the schedulers: three processes, each
with five scheduler loops, coordinated by the advisory lock so each tick runs once (Hour 9).

> **Watch Out!** `image=...from_ecr_repository(self.ecr_repo, tag="latest")` is what the
> _stack_ declares; the deploy workflow registers a new task definition revision with an
> immutable, SHA-tagged image and hands _that_ to CodeDeploy (Hour 11). If you ever `cdk
> deploy` the Fargate stack after a release, CloudFormation will try to reconcile the task
> definition back to `latest` — read the diff before confirming. This is a known sharp
> edge of mixing CDK-declared task definitions with pipeline-registered revisions.

> **Did You Know?** The comment says "3 AZs" but the VPC has `max_azs=2`. The minimum of 3
> tasks still buys you something — a rolling failure of one task leaves two — but it's
> 2 AZs of redundancy, not 3. Comments drift; `cdk synth` doesn't.

### Try It Yourself

```bash
cd infrastructure/cdk && ENVIRONMENT=prod AWS_ACCOUNT_ID=123456789012 cdk synth experimentation-fargate-prod > /tmp/f.yaml
grep -n "DeploymentController\|CODE_DEPLOY" /tmp/f.yaml
grep -n -A3 "Secrets:" /tmp/f.yaml | head -20              # ValueFrom: the secret ARNs, never values
grep -n "MinCapacity\|MaxCapacity\|TargetValue" /tmp/f.yaml
grep -n "DeregistrationDelay\|HealthCheckPath" /tmp/f.yaml
```

Then read `docs/deployment/deployment-guide.md` alongside the workflow in Hour 11 and trace
one deploy from tag to traffic shift.

### Key takeaways

- ALB (HTTPS, HTTP→HTTPS redirect) → blue/green target groups (health `/health`, 30 s
  deregistration) → Fargate tasks (1 vCPU/2 GB, private subnets).
- CodeDeploy controller: `CANARY_10_PERCENT_5_MINUTES`, a test listener, 30-minute approval
  window, blue kept for an hour, auto-rollback on failure or stop.
- Secrets by name from `/{env}/experimentation/*`, resolved by ECS at task start; two roles,
  least privilege.
- Scale 3–10 tasks on CPU 70% / memory 80%, fast out, slow in; one worker per task.

---

## Hour 6 — Data services: Aurora, ElastiCache, DynamoDB, Kinesis → Firehose → S3 → Glue, OpenSearch

### What & why

Five data services, each for a distinct job. **Aurora PostgreSQL** is the system of record —
experiments, assignments, events, users, everything the Build book's models describe.
**ElastiCache Redis** is the results cache and the distributed rate limiter's store — both
degrade gracefully without it. **DynamoDB** holds the real-time counters the bandit
scheduler reads first, plus the tables the original Lambda path used. The **Kinesis →
Firehose → S3** pipeline is the analytics tap: events land in a partitioned, gzipped data
lake that **Glue** turns into Parquet nightly and Athena queries. **OpenSearch** indexes
events for ad-hoc search. Only the first two are on the hot path; a failure in the others is
an analytics gap, not an outage.

### How Experimently does it

**Aurora** — encrypted, in the isolated subnet in prod, sized by environment, snapshot on
delete:

```python
# 📁 infrastructure/cdk/stacks/enhanced_database_stack.py
        # Scale according to environment
        instance_count = 2 if environment in ["prod", "staging"] else 1

        instance_type = (
            ec2.InstanceType.of(ec2.InstanceClass.MEMORY5, ec2.InstanceSize.LARGE)
            if environment == "prod"
            else ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE3, ec2.InstanceSize.MEDIUM)
        )

        self.aurora_cluster = rds.DatabaseCluster(
            self,
            "AuroraCluster",
            engine=rds.DatabaseClusterEngine.aurora_postgres(version=rds.AuroraPostgresEngineVersion.VER_15_3),
            credentials=rds.Credentials.from_secret(db_credentials),
            default_database_name="experimentation",
            instances=instance_count,
            instance_props=rds.InstanceProps(
                vpc=vpc,
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=(ec2.SubnetType.PRIVATE_ISOLATED if environment == "prod" else ec2.SubnetType.PRIVATE_WITH_EGRESS)
                ),
                instance_type=instance_type,
                security_groups=[rds_security_group],
                parameter_group=db_instance_parameter_group,
            ),
            parameter_group=db_cluster_parameter_group,
            subnet_group=db_subnet_group,
            storage_encrypted=True,
            storage_encryption_key=db_encryption_key,
            removal_policy=RemovalPolicy.SNAPSHOT,
        )
```

Two instances in prod means a writer and a reader; the API's single `SQLALCHEMY_DATABASE_URI`
points at the cluster writer endpoint, and `RemovalPolicy.SNAPSHOT` means a stack
deletion leaves a final snapshot rather than the data. Connection details go to SSM
parameters so other stacks and operators can find the endpoint without hard-coding it.

**Redis** — a replication group with failover when there's more than one node, encrypted in
transit and at rest, nightly snapshots:

```python
# 📁 infrastructure/cdk/stacks/elasticache_redis_stack.py
        self.redis_cluster = elasticache.CfnReplicationGroup(
            self,
            "RedisCluster",
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
```

`transit_encryption_enabled=True` is why the secret is a `REDIS_URL` (a `rediss://` URL
with the auth token) rather than a host and port — the application's Redis client needs the
scheme to know to use TLS.

**DynamoDB counters** — the table the bandit scheduler reads, keyed `pk`/`sk`, on-demand
billing, point-in-time recovery, TTL for cleanup, and a GSI so dashboards can query by
experiment without knowing the key format:

```python
# 📁 infrastructure/cdk/stacks/dynamodb_counters_stack.py
        self.counters_table = dynamodb.Table(
            self,
            "ExperimentCountersTable",
            table_name=self.table_name,
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=billing_mode,
            removal_policy=removal_policy,
            point_in_time_recovery=True,
            # TTL enables automatic cleanup of old experiment data
            time_to_live_attribute="expires_at",
        )

        # GSI: look up all variants by experiment_id without knowing the pk
        # format.  The service stores ``experiment_id`` as a plain attribute
        # so dashboards can query via GSI instead of using begins_with on pk.
        self.counters_table.add_global_secondary_index(...)
```

(The `dynamodb-tables` stack creates the five tables of the original Lambda design —
assignments, events, experiments, feature flags, overrides. They exist; the SDK path doesn't
use them.)

**The event pipeline** — a Kinesis stream with 24-hour retention, a Firehose delivery
stream that batches into S3 with date partitions and gzip, errors to their own prefix:

```python
# 📁 infrastructure/cdk/stacks/analytics_stack.py
        self.events_stream = kinesis.Stream(
            self,
            "EventsStream",
            stream_name=stream_name,
            shard_count=1,  # Increase for production
            retention_period=Duration.hours(24),
        )
        ...
        delivery_stream = firehose.CfnDeliveryStream(
            self,
            "EventsDeliveryStream",
            delivery_stream_name=delivery_stream_name,
            delivery_stream_type="KinesisStreamAsSource",
            kinesis_stream_source_configuration=firehose.CfnDeliveryStream.KinesisStreamSourceConfigurationProperty(
                kinesis_stream_arn=self.events_stream.stream_arn,
                role_arn=firehose_role.role_arn,
            ),
            s3_destination_configuration=firehose.CfnDeliveryStream.S3DestinationConfigurationProperty(
                bucket_arn=self.data_lake_bucket.bucket_arn,
                buffering_hints=firehose.CfnDeliveryStream.BufferingHintsProperty(interval_in_seconds=60, size_in_m_bs=5),
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
records/s in; the comment says what to raise.

**Glue** — nightly JSON → Parquet, metrics aggregation, a crawler, EventBridge to trigger it:

```python
# 📁 infrastructure/cdk/stacks/glue_etl_stack.py
"""
AWS CDK Stack: GlueETLStack (P3-A)

Provisions the full Glue ETL infrastructure for the experimentation platform:

Resources created:
    1. Glue IAM Role          — Glue service principal with S3, Glue catalog, and
                                CloudWatch permissions.
    2. Glue Database          — 'experimentation' catalog database (if not already
                                created by analytics_stack).
    3. Glue ETL Job           — 'experimentation-events-etl' (JSON → Parquet).
    4. Glue Metrics Job       — 'experimentation-metrics-etl' (metric aggregation).
    5. Glue Crawler           — 'experimentation-crawler' for raw_events table.
    6. EventBridge Rule       — Daily cron trigger at 02:00 UTC.
    7. Lambda Invoker         — Lambda function that calls glue:StartJobRun from
                                EventBridge events.
    8. Athena Results Bucket  — S3 bucket for Athena query output.
"""
```

And the warehouse-native analytics feature (Statistics book context; `warehouse_service`)
is how the platform reads results back out of Snowflake/BigQuery/Redshift for customers who
export there — the data lake is the platform's own copy.

> **Watch Out!** The analytics stack generates **random names** for the stream, bucket and
> OpenSearch domain (`exp-events-<id>`, `exp-data-<id>-<region>`). That avoids global S3
> name collisions, but it means the names are only knowable from stack outputs — anything
> that needs them (the event-processor Lambda's environment, the Glue jobs, a runbook) must
> read the output, not guess the name. Deleting and recreating the stack gives new names.

> **By the Way** — `REDIS_REQUIRED=false` (Hour 1) is a production decision too. With it
> false, a Redis failover (30–60 s) shows as a readiness _warning_ and slightly higher
> database load while the results cache is cold; with it true, every task goes unready and
> the ALB has nothing to route to. The platform's default is the graceful one; a deployment
> that relies on Redis for the distributed rate limiter across many tasks may choose the
> strict one.

### Try It Yourself

```bash
cd infrastructure/cdk && ENVIRONMENT=prod AWS_ACCOUNT_ID=123456789012 cdk synth > /tmp/all.yaml 2>/dev/null
grep -c "AWS::RDS::DBInstance" /tmp/all.yaml                       # 2 in prod
grep -n "TransitEncryptionEnabled\|AtRestEncryptionEnabled" /tmp/all.yaml
grep -n "TimeToLiveSpecification" -A2 /tmp/all.yaml | head
grep -n "Prefix: raw/events" /tmp/all.yaml
grep -n "ScheduleExpression" /tmp/all.yaml                         # the 02:00 UTC cron
```

Then, locally, start the `aws` profile and point the API at LocalStack
(`AWS_ENDPOINT_URL=http://localstack:4566`) to exercise the DynamoDB counters path without
an account: `docker compose --profile aws up -d --wait`.

### Key takeaways

- Aurora 15.3, encrypted, isolated subnet and 2 instances in prod, snapshot on delete,
  endpoint in SSM. Redis 7 replication group, TLS + at-rest encryption, failover when > 1
  node, nightly snapshots.
- DynamoDB counters: `pk`/`sk`, on-demand, PITR, TTL, GSI by `experiment_id`; the original
  Lambda tables exist but aren't on the SDK path.
- Kinesis (1 shard, 24 h) → Firehose (60 s / 5 MB, gzip, Hive partitions, error prefix) →
  S3 → Glue nightly (Parquet, metrics, crawler) → Athena; OpenSearch for search.
- Random resource names → read stack outputs; Redis optional by default.

---

## Part II Workshop

**Q1. Why are the three application secrets referenced by name and not created by the
Fargate stack?**
So their values never pass through CloudFormation templates or state; the stack fails
clearly if they're missing, and rotation is independent of deploys.

**Q2. Trace a deploy at the infrastructure level: what does CodeDeploy do with the two
target groups?**
Starts green tasks registered to the green target group; the test listener points at green;
10% of the HTTPS listener's traffic shifts to green; after 5 minutes of passing checks the
remaining 90% shifts; blue stays registered (idle) for an hour, then terminates.

**Q3. What does `RemovalPolicy.SNAPSHOT` on Aurora protect you from?**
A `cdk destroy` (or a stack replacement) deleting the production data; a final snapshot is
taken instead.

**Q4. Why is the ALB health check `/health` and the image's `/health/live`?**
The ALB should pull a task with a broken DB connection out of rotation (readiness); the
container runtime should not restart a healthy process because the DB blipped (liveness).

**Q5. A Firehose delivery to S3 fails to parse a record. Where does it go?**
`errors/<error-type>/year=/month=/day=/` in the data lake bucket — separate from
`raw/events/`, so the crawler's `raw_events` table stays clean and the failures are
findable.

**Q6. Where does the bandit scheduler get its counts in production, and what happens if
DynamoDB is unreachable?**
DynamoDB `get_experiment_counters` first; on failure, Postgres (assignments + primary-metric
conversions), then `BanditState`, then priors. Weights still update; they're just computed
from the slower source.

**Quiz.** `min_capacity=3` and `WEB_CONCURRENCY=1`: how many scheduler loops exist, and
how many run each tick?
→ 3 tasks × 5 schedulers = 15 loops; each tick, exactly one task runs each scheduler (the
advisory lock), so 5 ticks run and 10 are skipped.

---

## Exercises

**E2.1 — Stack diff.** Synth `dev` and `prod` and produce a table of every property that
differs across the database, redis and fargate stacks (instance classes, counts, subnet
types, removal policies, billing modes).

**E2.2 — A fourth secret.** Add `SLACK_BOT_TOKEN` as `/{env}/experimentation/slack-token`:
the `from_secret_name_v2` reference, the task-role and execution-role policy statements, the
`secrets=` entry. List every place a new secret must appear.

**E2.3 — Shard maths.** ShopLab and StreamPulse simulators at full rate plus a real product
produce 3,000 events/s of ~600 bytes. How many Kinesis shards, and what Firehose buffering
would you set? Show the arithmetic against the 1 MB/s / 1,000 rec/s per-shard limits.

**E2.4 — Isolation test.** Explain, with the subnet types, why a prod Aurora instance can't
reach the internet and how the migration task (private subnet, `assignPublicIp=DISABLED`)
still pulls its image from ECR.

**E2.5 — CDK drift.** After a pipeline deploy has registered task-definition revision 42
with a SHA-tagged image, run `cdk diff experimentation-fargate-prod` (or reason about it)
and write down what CloudFormation would change and how you'd prevent it.

---

## Solutions

**S2.1** — database: instances 1 vs 2; `t3.medium` vs `r5.large`; `PRIVATE_WITH_EGRESS` vs
`PRIVATE_ISOLATED`. redis: node type and cluster size by env helper; failover/multi-AZ only
when size > 1; snapshot retention by env. fargate: task count from `app.py` (1 for demo,
otherwise 2 desired, 3 minimum via scaling). dynamodb: `DESTROY` in dev, `RETAIN`
otherwise; counters always on-demand.

**S2.2** — (1) `secretsmanager.Secret.from_secret_name_v2(self, "SlackSecret",
f"/{env_name}/experimentation/slack-token")`; (2) add its ARN to both roles'
`secretsmanager:GetSecretValue` resource lists; (3) `"SLACK_BOT_TOKEN":
ecs.Secret.from_secrets_manager(slack_secret)`; (4) the secrets-management doc's "Required
Secrets" section; (5) the deploy checklist. Five places — which is why the doc exists.

**S2.3** — 3,000 × 600 B = 1.8 MB/s and 3,000 rec/s → 3 shards on records (the binding
constraint), 4 for headroom. Firehose at 5 MB / 60 s flushes every ~3 s on size; raise to
64 MB / 300 s for fewer, larger Parquet-friendly objects, accepting five minutes of latency
into the lake.

**S2.4** — `PRIVATE_ISOLATED` subnets have no route to a NAT or IGW; nothing in or out of
the VPC except via endpoints. The migration task runs in `PRIVATE_WITH_EGRESS` (per the
`aws ecs run-task` network config) and reaches ECR through the NAT — or, better, through
ECR interface endpoints if added; it reaches Aurora across subnets inside the VPC via the
RDS security group rule.

**S2.5** — the stack declares the container image as `:latest`; `cdk diff` shows the task
definition's image changing from the SHA tag back to `latest`, creating revision 43 and (if
deployed) a CodeDeploy-managed service now pointing at an unpinned image. Prevent it by
either reading the current revision's image in the stack (a lookup or a parameter the
pipeline sets) or excluding the task-definition image from CDK ownership and treating the
pipeline as the source of truth for it — and, until then, never `cdk deploy` the fargate
stack without reading the diff.

---

**Next:** [Part III — Observability →](./part-3-observability.md)

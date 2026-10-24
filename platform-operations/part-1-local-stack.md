# Part I — The Local and Self-Hosted Stack

### Hours 1–3

[Contents](./README.md) · [Part II →](./part-2-aws.md)

Experimently uses the same application images in several deployment environments. Hour 1 compares
the development Compose stack, production Compose configuration and Helm chart. Hour 2 covers the
core and full image targets. Hour 3 follows startup through configuration checks, database readiness,
bootstrap, seeding and process handoff.

Start with the local stack before moving to AWS; it provides a convenient environment for inspecting
these behaviors.

> **Verified against** getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased
> changes). One codebase, Apache-2.0 throughout, built in two **profiles**: **core** (`backend/`
> only) and **full** (adds the optional modules under `modules/`). The profile is decided by
> whether `import modules` succeeds in the image, and `modules-manifest.txt` is the authority on
> what is a module.

---

## Hour 1 — Docker Compose: services, profiles, health, and the `.env` contract

### What & why

A compose file is an operational contract written as YAML: which processes exist, what they
need from each other, when they're considered up, and what an operator is allowed to change.
The repository has **two**, for two different jobs:

- **`docker-compose.yml`** at the repository root is the **development** stack: it _builds_ the
  images from your checkout, ships demo defaults (a demo admin, demo seeds, localhost CORS
  origins), and publishes every port on the host. It has four core services — `postgres`,
  `redis`, `api`, `frontend` — and three **compose profiles** that add optional ones: `demo`
  (ShopLab, StreamPulse and their traffic simulators), `tools` (pgAdmin), `aws` (LocalStack for
  the DynamoDB/Kinesis/S3 paths). A compose profile is off unless named.
- **`deploy/compose/compose.yml`** is the **production** file a self-hoster downloads. It runs
  the _published_ images, never builds, ships no demo services and no seeded users, and
  publishes exactly one port — the dashboard's, on loopback, for a TLS reverse proxy to sit in
  front of.

(The word "profile" does double duty: a _compose_ profile like `demo` adds services; a _build_
profile, `core` or `full`, decides what goes into the images. `EXPERIMENTLY_PROFILE` selects the
build profile in both files.)

Three ideas carry the development file. **Health-gated dependencies**: `api` waits for Postgres
and Redis to be _healthy_, not merely started; `frontend` waits for `api` to be healthy — and
`api`'s health is _readiness_ (DB reachable, bootstrap done), so `docker compose up --wait`
returns only when the whole thing can serve. **Every knob is `${VAR:-default}`**: a `.env` file
or the shell overrides any of them, defaults are for local development, and the header says
which three must change before anyone else can reach the stack. **First-start automation is
declared, not scripted**: `RUN_MIGRATIONS` and `SEED` are environment variables the entrypoint
reads (Hour 3), so "a fresh database becomes a usable platform" is a property of the image, not
of a README.

### How Experimently does it

The header is the operator's manual:

```yaml
# 📁 docker-compose.yml
# Experimently — local / self-hosted stack.
#
#   docker compose up -d --wait            # postgres + redis + api + dashboard
#   docker compose --profile demo up -d    # + ShopLab (:3200), StreamPulse (:3300)
#                                          #   and their traffic simulators
#   docker compose --profile tools up -d   # + pgAdmin (:5050)
#   docker compose --profile aws up -d     # + LocalStack (:4566)
#   docker compose down -v                 # stop and drop the volumes
#
# Every ${VAR:-default} below can be set in a `.env` file next to this file
# (compose reads it automatically) or in the shell. Defaults are for a local
# development stack: change SECRET_KEY, FIRST_SUPERUSER_PASSWORD and
# POSTGRES_PASSWORD before exposing the stack to anyone.
#
# First start: the api container waits for Postgres, creates the schema
# (RUN_MIGRATIONS=true -> python -m backend.app.db.bootstrap) and applies the
# seeds in SEED (default `demo`: admin@demo.com / Demo1234!, three experiments,
# two flags). Seeds run once per database (seed_markers table).
```

The image names follow the build profile, so a `core` and a `full` build can sit side by side:

```yaml
# 📁 docker-compose.yml
x-api-image: &api-image experimently-api:${EXPERIMENTLY_IMAGE_TAG:-${EXPERIMENTLY_PROFILE:-core}}
x-web-image: &web-image experimently-web:${EXPERIMENTLY_IMAGE_TAG:-${EXPERIMENTLY_PROFILE:-core}}
```

The `api` service — the build target is the profile, every environment variable grouped and
commented, the health check pointed at readiness, and a long `start_period` because first start
includes seeding:

```yaml
# 📁 docker-compose.yml
  api:
    build:
      context: .
      dockerfile: backend/Dockerfile
      # core | full — see backend/Dockerfile. `full` copies modules/ into the
      # image; `core` never reads it.
      target: ${EXPERIMENTLY_PROFILE:-core}
    image: *api-image
    environment:
      # --- environment ---
      ENVIRONMENT: ${ENVIRONMENT:-development}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      LOG_FORMAT: ${LOG_FORMAT:-console}          # json | console
      WEB_CONCURRENCY: ${WEB_CONCURRENCY:-1}       # 1 worker per container; scale containers instead
      # --- auth ---
      AUTH_PROVIDER: ${AUTH_PROVIDER:-local}       # local | cognito
      DEV_AUTH_BYPASS: ${DEV_AUTH_BYPASS:-false}   # never true outside development/test
      ...
      # --- database ---
      POSTGRES_SERVER: postgres
      POSTGRES_HOST: postgres
      ...
      RUN_MIGRATIONS: ${RUN_MIGRATIONS:-true}
      # --- seeds: comma list of demo, shoplab, streampulse, sdk-contract ---
      SEED: ${SEED-demo}                    # an explicit empty SEED= applies none
      SEED_FORCE: ${SEED_FORCE:-false}
      # --- redis ---
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_REQUIRED: ${REDIS_REQUIRED:-false}
      # --- http ---
      # Development origins: the local dashboard and demo apps. In staging or
      # production set CORS_ORIGINS to the sites that run the browser SDK.
      CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000,http://localhost:3100,http://localhost:3200,http://localhost:3300}
      METRICS_TOKEN: ${METRICS_TOKEN:-}
      # --- demo profile: where the seeds write the demo apps' API keys ...
      SHOPLAB_DEMO_DIR: /data/demo/shoplab
      STREAMPULSE_DEMO_DIR: /data/demo/streampulse
      SDK_CONTRACT_KEY_DIR: /data/demo/sdk-contract
      ...
      # --- optional AWS (aws profile / Cognito) ---
      AWS_REGION: ${AWS_REGION:-us-west-2}
      AWS_DEFAULT_REGION: ${AWS_REGION:-us-west-2}
    volumes:
      - demo_keys:/data/demo
    ...
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    # Readiness (DB reachable) rather than the image's liveness check, so
    # `docker compose up --wait` returns only once bootstrap + seeds are done
    # and the API answers. start_period covers the first-run seeding.
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/health/ready"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 300s
    restart: unless-stopped
```

Note `${SEED-demo}` — a single `-`, not `:-`. With `:-`, an explicitly empty `SEED=` would fall
back to `demo`; with `-`, only an _unset_ `SEED` does, so `SEED=` in `.env` really means "no
seeds".

`WEB_CONCURRENCY: 1` with "scale containers instead" is an operational decision worth naming:
one uvicorn worker per container keeps the five background schedulers to one set per process
(they coordinate across processes with a database lock — Hour 9), keeps memory predictable, and
lets the orchestrator do the scaling. `REDIS_REQUIRED: false` is the other one: the API degrades
without Redis (rate limiting falls back to memory, the results cache is skipped), so a Redis
outage doesn't fail readiness unless you say it should.

The dashboard service — port 8080 inside (unprivileged nginx), the build profile and a relative
API base baked in at build time, the upstream set at run time, and its own health check on
nginx's static `/healthz`:

```yaml
# 📁 docker-compose.yml
  frontend:
    build:
      context: .
      dockerfile: frontend/Dockerfile
      args:
        EXPERIMENTLY_PROFILE: ${EXPERIMENTLY_PROFILE:-core}
        # Relative: the dashboard calls /api/... on its own origin and nginx
        # proxies to the api container.
        NEXT_PUBLIC_API_URL: ${NEXT_PUBLIC_API_URL:-}
    image: *web-image
    environment:
      API_UPSTREAM: http://api:8000
      # connect-src of the CSP; add the API origin when NEXT_PUBLIC_API_URL is absolute.
      CSP_CONNECT_SRC: "${CSP_CONNECT_SRC:-'self' ws: wss:}"
    ports:
      - "${FRONTEND_HOST_PORT:-3000}:8080"
    depends_on:
      api:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://127.0.0.1:8080/healthz"]
      interval: 10s
      timeout: 5s
      retries: 6
      start_period: 10s
```

The demo profile shows the seed → key → simulator hand-off through a shared volume: the seed
writes the demo app's API key to `/data/demo/<app>/.api_key` on the `demo_keys` volume
before the API becomes healthy; the simulator (which depends on API health) reads it at
start:

```yaml
# 📁 docker-compose.yml
  shoplab-simulator:
    profiles: ["demo"]
    image: python:3.11-slim
    working_dir: /sim
    volumes:
      - ./demo/shoplab/simulator:/sim/demo/shoplab/simulator:ro
      - demo_keys:/data/demo:ro
    ...
    # The seed (SEED includes shoplab) writes /data/demo/shoplab/.api_key on the
    # shared volume before the api becomes healthy; read it at start.
    command:
      - sh
      - -c
      - >-
        test -f /data/demo/shoplab/.api_key
        || { echo "no /data/demo/shoplab/.api_key — start the api with SEED=demo,shoplab"; exit 1; };
        exec python demo/shoplab/simulator/traffic.py
        --api-url http://api:8000
        --api-key "$$(cat /data/demo/shoplab/.api_key)"
        --rate "$$SHOPLAB_SIM_RATE"
```

(Note `$$` — compose interpolates `$VAR` itself, so a `$` meant for the container's shell is
doubled.) The `aws` profile is LocalStack with just the services the platform's optional
AWS paths use: `SERVICES: dynamodb,s3,kinesis,sqs,cognito-idp`, pointed at with
`AWS_ENDPOINT_URL=http://localstack:4566`.

#### The production compose file

Hardening the development file with a `.env` is no longer how you self-host. The production file
is a different contract, stated in its header:

```yaml
# 📁 deploy/compose/compose.yml
# Experimently -- production compose file (self-hosting on one Docker host).
#
# This file runs the PUBLISHED images. It never builds, it ships no demo
# services and no seeded demo users, and it starts nothing that listens on the
# host except the dashboard. For a development stack built from a checkout,
# use the repository's docker-compose.yml instead.
#
#   ./generate-env.sh              # writes .env with generated secrets, once
#   docker compose up -d --wait    # postgres + redis + api + web
#   docker compose logs -f api     # first start creates the schema and admin
#
# Settings come from the `.env` file next to this file. The five marked
# REQUIRED have no default: compose refuses to start (and `docker compose
# config` exits non-zero) naming the first one that is unset or empty.
#
#   SECRET_KEY                 REQUIRED  signs sessions and tokens
#   FIRST_SUPERUSER            REQUIRED  email of the first administrator
#   FIRST_SUPERUSER_PASSWORD   REQUIRED  used only while the users table is empty
#   POSTGRES_PASSWORD          REQUIRED  the database password
#   PUBLIC_BASE_URL            REQUIRED  the URL people open, e.g.
#                                        https://experimently.example.com; the
#                                        API answers only for this host name
#   AUDIT_HMAC_KEY             full profile only: signs audit events
#   EXPERIMENTLY_PROFILE       core (default) or full
#   HTTP_BIND / HTTP_PORT      where the dashboard listens (127.0.0.1:8080);
#                              put your TLS reverse proxy in front of it
#   POSTGRES_SERVER            an external database host instead of the bundled
#                              one; then start only `docker compose up -d api web`
```

The "required" is enforced by compose itself, with the `${VAR:?message}` form — there is no
default to forget to change:

```yaml
# 📁 deploy/compose/compose.yml
name: experimently-selfhost

services:
  ...
  api:
    image: ghcr.io/getexperimently/experimently:${EXPERIMENTLY_PROFILE:-core}-0.17.0 # x-release-please-version
    environment:
      ENVIRONMENT: production
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      LOG_FORMAT: json
      ...
      PUBLIC_BASE_URL: "${PUBLIC_BASE_URL:?required: the URL people open, e.g. https://experimently.example.com}"
      SECRET_KEY: "${SECRET_KEY:?required: generate with openssl rand -hex 32}"
      ...
      RUN_MIGRATIONS: "true"
      # No seeds in production: the demo seed creates a user with a published
      # password.
      SEED: ""
  ...
  web:
    image: ghcr.io/getexperimently/experimently-web:${EXPERIMENTLY_PROFILE:-core}-0.17.0 # x-release-please-version
    ...
    ports:
      - "${HTTP_BIND:-127.0.0.1}:${HTTP_PORT:-8080}:8080"
```

Four things to notice. The project name is fixed (`experimently-selfhost`) so it never adopts the
development stack's containers and volumes. The image tags are literal versions that the release
process moves (`core-X.Y.Z` / `full-X.Y.Z`, the `x-release-please-version` marker) — no floating
tag. The images are public on GHCR (`ghcr.io/getexperimently/experimently` and
`ghcr.io/getexperimently/experimently-web`), signed with cosign keyless signatures, and built for
amd64 only. And `deploy/compose/generate-env.sh` writes the `.env` once — it refuses to overwrite
an existing one, because a second run would rotate `SECRET_KEY`, and write a
`FIRST_SUPERUSER_PASSWORD` and a `POSTGRES_PASSWORD` the existing database no longer accepts. The
`compose-production.yml` CI workflow boots this file the way a self-hoster does, on every pull
request that touches it or the images.

`PUBLIC_BASE_URL` is required for a reason beyond links: the API answers only for that host
name. When `ALLOWED_HOSTS` is unset it is derived from `PUBLIC_BASE_URL`'s host, and a request
with any other `Host` header is refused with 400 before a handler sees it — so your reverse proxy
must forward the `Host` header unchanged.

#### On Kubernetes: the Helm chart

The third way to self-host is the Helm chart in `charts/experimently` (values `profile`,
`publicBaseUrl`, `firstSuperuser`, `existingSecret`, `ingress.*`, validated at render time by
`values.schema.json`). The app's `docs/self-hosting/kubernetes.md` is the install guide, and the
`chart-kind` CI job runs its command blocks as written on a kind cluster. The one operational
difference from compose: the chart runs the bootstrap in an **init container**, not in the serving
container (Hour 3). Today the chart is a `.tgz` release asset only; the project has decided
it will ALSO be an OCI package at `oci://ghcr.io/getexperimently/charts/experimently`; the first push and
making it public are founder-only and not done.

> **Watch Out!** The three secrets in the development file's header. The default `SECRET_KEY`
> signs local JWTs (Build book Day 10); anyone who reads this public file can forge a session on
> a stack that kept it. The `FIRST_SUPERUSER_PASSWORD` default is the demo admin. They are allowed
> to exist because the development file is for development: start the same image with
> `ENVIRONMENT=production` and the entrypoint's settings preflight (Hour 3) refuses the
> placeholder key, the missing `PUBLIC_BASE_URL` and the demo seeds, all in one list, and the
> container exits 78 before it touches the database. For anything another person can reach, use
> the production file.

> **By the Way** — CORS. The development default lists the local dashboard and demo-app origins.
> In staging and production there is **no** default: with neither `CORS_ORIGINS` nor
> `BACKEND_CORS_ORIGINS` set, the API allows no cross-origin browser calls at all. That is right
> for the dashboard (same origin, Hour 2). Set `CORS_ORIGINS` only to the sites that run the
> **browser SDK**; entries are normalised to `scheme://host[:port]`, a malformed one is dropped
> with an error log, and any `DASHBOARD_ORIGINS` are added automatically.

> **By the Way** — `docker compose up -d --wait` vs `up -d`. Without `--wait`, compose
> returns as soon as containers are _started_; the API is still bootstrapping and seeding
> for up to a couple of minutes on first run, and a script that immediately `curl`s it
> gets connection refused. `--wait` blocks on the health checks — readiness, for the API —
> so the next line of your script can assume a working platform. `make up` and `make demo`
> both use it, and so does the production file's own instructions.

### Try It Yourself

```bash
python -c 'import secrets; print("SECRET_KEY=" + secrets.token_urlsafe(48))' >> .env
docker compose up -d --wait                        # returns when api is ready
docker compose ps                                  # four services, all healthy
curl -s localhost:3000/health/ready | jq .         # through nginx → api
docker compose logs api | grep entrypoint          # preflight / wait / bootstrap / seed lines
SEED=demo,shoplab,streampulse docker compose --profile demo up -d --wait
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
open http://localhost:3200                          # ShopLab
docker compose down -v                             # drop the volumes: next up re-seeds

# the production file refuses to start without its required settings
cd deploy/compose && docker compose config >/dev/null; echo "exit $?"   # names the first missing one
```

Then set `REDIS_REQUIRED=true`, `docker compose stop redis`, and watch `/health/ready` turn
503 while `/health/live` stays 200.

### Key takeaways

- Two compose files: `docker-compose.yml` builds from a checkout for development (four core
  services, compose profiles `demo`, `tools`, `aws`); `deploy/compose/compose.yml` runs the
  published, signed GHCR images for self-hosting, with five required settings and only the
  dashboard published, on loopback.
- `EXPERIMENTLY_PROFILE` (`core` default, or `full`) picks the build target and the image tag.
- Dependencies are health-gated; the API's compose health is _readiness_ so `--wait` means
  "can serve"; `start_period: 300s` covers first-run seeding.
- `PUBLIC_BASE_URL` decides which host the API answers for; CORS in staging and production lists
  only browser-SDK sites.
- Kubernetes: the Helm chart in `charts/experimently`, bootstrap in an init container.

---

<a id="hour-2--the-images-two-multi-stage-dockerfiles-and-the-nginx-single-origin-trick"></a>

## Hour 2 — Multi-stage images and single-origin serving

### What & why

An image is a promise about what's inside and how it behaves: what it listens on, how you
tell whether it's alive, what it does when started with no arguments, and — for the
platform's two images — how they find each other. The **API image** promises a Python
runtime with exactly the locked dependencies installed, the repository layout preserved under
`/app` so `backend.app.*` imports work, a non-root user, a liveness `HEALTHCHECK`, and the
entrypoint from Hour 3. The **dashboard image** promises a static Next.js export served by nginx,
with `/api/*`, `/ws/*` and `/health*` proxied to the API so the browser only ever talks to one
origin.

Three decisions shape both. **Multi-stage builds** keep the runtime image small and free of
compilers (the API's builder stage has gcc and libpq-dev; the runtime has libpq5 and curl, and
not even pip). **The build context is the repository root** for both — the API because its
imports are `backend.app.*`, the dashboard because the modules' dashboard code lives in
`modules/frontend/`, next to `frontend/`, not inside it. **The profile is a build target, not a
runtime switch**: `core` and `full` are different images, and each asserts at build time that it
contains what its profile says.

### How Experimently does it

The API image's contract, from its own header:

```dockerfile
# 📁 backend/Dockerfile
# Experimently API image.
#
# Build context is the REPOSITORY ROOT (the code imports `backend.app.*`):
#
#   docker build -f backend/Dockerfile -t experimently-api:core .
#   docker build -f backend/Dockerfile --target full -t experimently-api:full .
#
# Targets:
#   core  (default) the core profile — backend/ only
#   full  the full profile — backend/ plus modules/ and the module-only
#         dependencies (modules/requirements.lock). Needs a full checkout: on
#         a core one, which has no modules/ directory, it produces an image
#         with an empty /app/modules that refuses to start outside development.
#
# Runtime contract:
#   ENTRYPOINT backend/docker-entrypoint.sh  (waits for Postgres, bootstraps the
#              schema when RUN_MIGRATIONS=true, applies SEED=... once)
#   CMD        uvicorn backend.app.main:app --workers ${WEB_CONCURRENCY:-1}
#   HEALTHCHECK GET /health/live
```

The two shared stages that matter, and the layout rule:

```dockerfile
# 📁 backend/Dockerfile
FROM python:${PYTHON_VERSION}-slim AS builder
...
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements/runtime.lock /tmp/runtime.lock
RUN pip install --upgrade pip && pip install --require-hashes -r /tmp/runtime.lock

FROM python:${PYTHON_VERSION}-slim AS runtime
...
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser \
    ...

WORKDIR /app

# Application code. The layout under /app mirrors the repository so that
# `backend.app.main:app`, `python -m backend.app.db.bootstrap` and the seed
# scripts resolve exactly as they do from a checkout.
COPY --chown=appuser:appgroup backend/ /app/backend/
...
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=90s --retries=5 \
    CMD curl -fsS http://localhost:${PORT:-8000}/health/live || exit 1

ENTRYPOINT ["/app/backend/docker-entrypoint.sh"]
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Two details in the builder and runtime lines are deliberate. The builder installs
`runtime.lock`, the fully resolved closure of `runtime.txt` with a hash per artefact
(`make lock` writes it, `make lock-check` fails CI when they drift), so `--require-hashes` refuses
anything that does not match. And the runtime stage runs `apt-get upgrade` before installing: the
slim base image is rebuilt on its own schedule, and the image scan in CI fails on any fixable
HIGH, so the image takes the Debian security fixes itself rather than waiting. (The profile stages
then delete pip, setuptools and wheel from the image and assert they are gone: installing
packages into a running container is not a supported operation.)

Note the image `HEALTHCHECK` is **liveness** (`/health/live`) while compose's is
**readiness** (`/health/ready`). The image can't know whether a given deployment considers
Redis required; it promises only "the process answers." The orchestrator layers the
stricter check on top.

The profile targets — `full` adds the module-only dependencies and the `modules/` package; `core`
is _last_, so it is the default:

```dockerfile
# 📁 backend/Dockerfile
FROM runtime AS full

ENV EXPERIMENTLY_PROFILE=full
LABEL io.experimently.profile="full"

# The core closure *plus* the module dependencies.
COPY --from=builder-full /opt/venv /opt/venv
...
COPY --from=modules-src --chown=appuser:appgroup /modules/__init__.py /app/modules/
COPY --from=modules-src --chown=appuser:appgroup /modules/backend /app/modules/backend/
...

# Stage 4: core — the core image, with the core closure only: none of
# modules/requirements.lock and no modules/ directory. Last stage = default
# target, so a plain `docker build -f backend/Dockerfile .` builds this one.
FROM runtime AS core
ENV EXPERIMENTLY_PROFILE=core
LABEL io.experimently.profile="core"

COPY --from=builder /opt/venv /opt/venv
```

The stages in order are `builder`, `runtime`, `modules-src`, `builder-full`, `full`, `core`.
Every reference to `modules/` goes through the one `modules-src` stage, which copies the tree _or
an empty stand-in for it_ — so a classic (non-BuildKit) builder, which runs every stage, can still
build `core` from a checkout that has no `modules/` at all. A `--target full` build from such a
checkout is not refused at build time; it produces an image with an empty `/app/modules` that
refuses to start outside development.

The dashboard image — build the export with the profile, generate the nginx routes for the
dynamic pages from Next's own manifest (`frontend/scripts/nginx-routes.mjs` reads
`.next/routes-manifest.json`), then assert the bundle matches the profile:

```dockerfile
# 📁 frontend/Dockerfile
COPY frontend/ ./
# modules-alias.js looks for ../modules/frontend/src relative to the frontend dir.
COPY --from=modules-tree /modules/frontend/ /app/modules/frontend/
# Relative API base by default: nginx proxies /api/ to the backend. Pass an
# absolute URL only when the dashboard is served from a different origin.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
# The documentation site the dashboard links to (frontend/src/services/docs.ts).
# Empty means the published site; set it for a deployment that hosts its own.
ARG NEXT_PUBLIC_DOCS_URL=""
ENV NEXT_PUBLIC_DOCS_URL=${NEXT_PUBLIC_DOCS_URL}

RUN npm run build \
    && test -f .next/routes-manifest.json \
    ...
    && test -d out \
    # The bundle must match the profile: a core image carries no chunk that
    # names a module route, a full image carries them.
    && if [ "$EXPERIMENTLY_PROFILE" = "core" ]; then \
         ! grep -rlE '/api/v1/workspaces|/api/v1/rbac/' out/_next/static >/dev/null \
         && grep -rl 'Separate teams into workspaces' out/_next/static >/dev/null; \
       else \
         test -d /app/modules/frontend/src \
         && grep -rlE '/api/v1/rbac/' out/_next/static >/dev/null; \
       fi
```

That last `if` is a build-time assertion with both halves. A core bundle must contain no module
route **and** must contain the core stub page's text — without the positive half, the grep would
also pass when the page vanished entirely. A full bundle must contain the module routes. Either
way, an image whose contents disagree with its profile label fails to build rather than shipping.

The dashboard no longer renders the documentation itself, so `docs/` is not copied into the image.
"Read the guide" links go out through `docsUrl()` in `frontend/src/services/docs.ts`: by default
to the Markdown in the GitHub repository, or, with `NEXT_PUBLIC_DOCS_URL` set at build time, to a
built MkDocs site such as the published one at `https://getexperimently.github.io/experimently/`
(deployed from release tags by the `docs.yml` workflow).

The nginx config is a template with exactly two variables expanded — everything else that
looks like `$var` is nginx syntax and must survive:

```nginx
# 📁 frontend/nginx.conf
# This file is a template: `${API_UPSTREAM}` is substituted by
# frontend/docker-entrypoint.sh (envsubst) at container start, default
# http://api:8000. Everything else that looks like a variable ($uri, $host, ...)
# is nginx syntax and is left untouched because only API_UPSTREAM is expanded.
#
# Routing:
#   /api/*      -> API (REST + the /api/v1/ws/... WebSocket, upgrade headers set)
#   /ws/*       -> API (WebSocket paths mounted at the root, if any)
#   /health*    -> API health probes (so `curl :3000/health/ready` reaches the API)
#   /healthz    -> this container (static liveness for orchestrators)
#   everything else -> the static export in /usr/share/nginx/html; dynamic Next
#                      routes come from the generated experimently-routes.conf.
...
    location /api/ {
        proxy_pass ${API_UPSTREAM};
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
        # WebSocket (/api/v1/ws/experiments/{id}/results)
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
        client_max_body_size 5m;
    }
```

`proxy_set_header X-Request-ID $request_id` is where the request ID story (Hour 7) begins:
nginx mints one per request and the API adopts it. `proxy_read_timeout 3600s` and
`proxy_buffering off` are for the live-results WebSocket — a buffered, 60-second proxy
would silently drop the stream. `proxy_set_header Host $http_host` matters more than it looks:
the API refuses a `Host` it does not answer for (Hour 1), so the header must arrive unchanged.

The dashboard entrypoint does the substitution carefully:

```sh
# 📁 frontend/docker-entrypoint.sh
: "${API_UPSTREAM:=http://api:8000}"
# Strip a trailing slash: proxy_pass with a URI part would rewrite the path.
API_UPSTREAM="${API_UPSTREAM%/}"
export API_UPSTREAM
...
if [ -f "$TEMPLATE" ]; then
    envsubst '${API_UPSTREAM} ${CSP_CONNECT_SRC}' < "$TEMPLATE" > "$TARGET"
    echo "[entrypoint] rendered $TARGET (API_UPSTREAM=$API_UPSTREAM, connect-src=$CSP_CONNECT_SRC)" >&2
fi

# The stock image's template step would re-render *.template files with every
# environment variable; point it at an empty directory so ours stays intact.
export NGINX_ENVSUBST_TEMPLATE_DIR=/etc/nginx/templates-disabled

exec /docker-entrypoint.sh "$@"
```

Two traps documented in two comments: `proxy_pass http://api:8000/` (with the slash) makes
nginx rewrite the path and `/api/v1/x` arrives as `/v1/x`; and the stock nginx image's own
envsubst would expand `$host`, `$uri` and every other nginx variable into empty strings.

> **Did You Know?** The dashboard bundle has `NEXT_PUBLIC_API_URL=""` — an _empty_ API base.
> `apiFetch` (Build book Day 23) builds URLs as `${base}/api/v1/...`, so with an empty base
> the browser requests `/api/v1/...` on its own origin and nginx proxies it. That single
> choice removes CORS from the dashboard entirely: same origin, no preflight, nothing in
> `CORS_ORIGINS` for it. The `CSP_CONNECT_SRC` knob exists only for the split-origin case.

> **Watch Out!** Both images must be built from the repository root (`docker build -f
> backend/Dockerfile .`). `cd backend && docker build .` fails on `COPY backend/` —
> and if it didn't, the imports would. The compose file sets `context: .` for both; the
> release and deploy workflows do the same. And build the two images in the **same** profile:
> a core API behind a full dashboard shows module pages whose API routes are not there.

### Try It Yourself

```bash
docker build -f backend/Dockerfile -t experimently-api:core . && docker image ls experimently-api
docker run --rm experimently-api:core python -c "import backend.app.main"; echo "exit $?"   # 78: preflight wants ENVIRONMENT
docker run --rm -e ENVIRONMENT=development -e WAIT_FOR_DB=false -e RUN_MIGRATIONS=false \
  experimently-api:core python -c "import backend.app.main; print('imports ok')"
docker inspect experimently-api:core --format '{{index .Config.Labels "io.experimently.profile"}}'
docker build -f frontend/Dockerfile -t experimently-web:core .
docker run --rm -e API_UPSTREAM=http://host.docker.internal:8000/ experimently-web:core sh -c 'cat /etc/nginx/conf.d/default.conf | grep proxy_pass'   # trailing slash stripped
docker inspect experimently-api:core --format '{{json .Config.Healthcheck}}' | jq .
docker run --rm --entrypoint sh experimently-api:core -c 'command -v pip || echo "no pip in the image"'
```

In a full checkout, build `--target full -t experimently-api:full` too and compare the two
profile labels and `docker image ls` sizes.

### Key takeaways

- API image: builder (gcc, libpq-dev, `pip install --require-hashes -r runtime.lock`) → runtime
  (`apt-get upgrade`, libpq5, curl, non-root, `/app` mirrors the repo) → `modules-src` →
  `builder-full` → `full` → `core` (default, last); liveness `HEALTHCHECK`; entrypoint + uvicorn.
- Dashboard image: static export + generated nginx routes + a two-sided build-time assertion that
  the bundle matches its profile; docs are linked out, not copied in; unprivileged nginx on 8080
  proxies `/api`, `/ws`, `/health*` to `API_UPSTREAM`; only two template variables are expanded.
- Empty `NEXT_PUBLIC_API_URL` = single origin = no CORS for the dashboard; `X-Request-ID` is
  minted by nginx.
- Build context is always the repository root; build both images in the same profile.

---

## Hour 3 — The entrypoint: check, wait, bootstrap, seed, hand over — and the Makefile

### What & why

The gap between "a container started" and "a platform is usable" is: the settings have to make
sense for the environment, the database has to be reachable, the schema has to exist at the right
version, an administrator has to exist so someone can log in, and (in development) demo data has
to be present. The entrypoint closes that gap in five steps. Step 0 — a settings preflight —
cannot be skipped; steps 1–3 each can; every step fails loudly; and the database steps are safe to
run from several containers at once.

The bootstrap behind step 2 solves a specific historical problem: the Alembic migration chain was
autogenerated against databases that already had tables and can't be replayed from zero. So a
**fresh** database is created from the SQLAlchemy models and _stamped_ at the heads; an
**existing** one gets `alembic upgrade heads`. **Heads, plural**: a full checkout has two — the
core chain and the `modules` branch — and a core checkout has one, which `heads` names just the
same. Either way, an empty `users` table gets the first superuser from `FIRST_SUPERUSER` /
`FIRST_SUPERUSER_PASSWORD`. And because several containers may do this at once, the whole thing
runs under a blocking advisory lock keyed by schema name.

Who runs that step depends on where you are. In compose, the single API container does
(`RUN_MIGRATIONS=true`). On Kubernetes, the chart's init container does. On AWS, the API never
does: a separate migration task does, before each deploy (end of this Hour).

### How Experimently does it

The entrypoint's contract, and its safety property:

```bash
# 📁 backend/docker-entrypoint.sh
# Experimently API container entrypoint.
#
#   0. python -m backend.app.core.preflight: the settings check. Every problem
#      it finds is printed in one list and the container exits 78 (EX_CONFIG)
#      before anything else runs. It always requires ENVIRONMENT (or the
#      legacy APP_ENV), and refuses `test`, which is the test runner's; in
#      staging/production it also requires the secrets,
#      PUBLIC_BASE_URL and POSTGRES_SERVER, and refuses DATABASE_URL/_URI.
#   1. Wait for PostgreSQL (POSTGRES_SERVER/POSTGRES_PORT) to accept connections.
#   2. RUN_MIGRATIONS=true (default)  ->  python -m backend.app.db.bootstrap
#      (creates the schema on a fresh database, `alembic upgrade heads` otherwise).
#   3. SEED=<comma list of demo,shoplab,streampulse,sdk-contract>  ->  run the
#      matching backend/scripts/seed_*.py once. Completed seeds are recorded in
#      the `seed_markers` table (backend/scripts/seed_markers.py) so a restart
#      of the container does not re-seed. SEED_FORCE=true re-runs them.
#      demo, shoplab and streampulse are development-only: they are refused,
#      before step 1, unless ENVIRONMENT is development or test.
#   4. exec "$@" — by default uvicorn; `--workers ${WEB_CONCURRENCY:-1}` and the
#      proxy flags are appended when the command is uvicorn and the caller did
#      not pass them explicitly.
#
# Steps 1-3 can be skipped (step 0 cannot): RUN_MIGRATIONS=false, SEED= (empty),
# WAIT_FOR_DB=false. The script runs with `set -euo pipefail`: a failed
# bootstrap or seed stops the container instead of starting a half-initialised
# API.
set -euo pipefail
```

Step 0 and the demo-seed refusal come first, before the database wait, so a misconfigured
deployment is told everything that is wrong in one go instead of one refusal per restart:

```bash
# 📁 backend/docker-entrypoint.sh
refuse_demo_seeds_outside_development() {
    local seed_list="${SEED:-}"
    [ -z "$seed_list" ] && return 0
    local env_raw="${ENVIRONMENT:-${APP_ENV:-development}}"
    ...
    case "$env_name" in
        development|dev|demo|test) return 0 ;;
    esac
    ...
        case "$name" in
            demo|shoplab|streampulse)
                log "SEED=${seed_list} is for development and is refused when ENVIRONMENT=${env_raw}. Remove SEED."
                exit 78
                ;;
        esac
    ...
}
refuse_demo_seeds_outside_development

cd /app

python -m backend.app.core.preflight
```

Exit status 78 is `EX_CONFIG`: "your configuration is wrong", distinct from a crash. In staging
and production the preflight requires `SECRET_KEY`, `FIRST_SUPERUSER_PASSWORD` and (full profile)
`AUDIT_HMAC_KEY` to be present, long enough and not a published placeholder; requires
`PUBLIC_BASE_URL` (or an explicit `ALLOWED_HOSTS`) and `POSTGRES_SERVER`; and refuses
`DATABASE_URL`/`DATABASE_URI`, which the image does not read. It never prints a secret's value.
(`sdk-contract` is not a demo seed and is allowed everywhere.)

Step 1 uses `pg_isready` when present and a raw TCP probe otherwise, with a deadline:

```bash
# 📁 backend/docker-entrypoint.sh
wait_for_db() {
    local deadline=$(( $(date +%s) + DB_WAIT_TIMEOUT ))
    log "waiting for PostgreSQL at ${POSTGRES_SERVER}:${POSTGRES_PORT} (timeout ${DB_WAIT_TIMEOUT}s)"
    while true; do
        if command -v pg_isready >/dev/null 2>&1; then
            if pg_isready -q -h "$POSTGRES_SERVER" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB"; then
                break
            fi
        else
            # Fallback when postgresql-client is not installed: a raw TCP probe.
            ...
        fi
        if [ "$(date +%s)" -ge "$deadline" ]; then
            log "PostgreSQL did not become ready within ${DB_WAIT_TIMEOUT}s"
            exit 1
        fi
        sleep 1
    done
    log "PostgreSQL is ready"
}
```

Step 3 — seeds by name, once, with the marker check before and the mark after:

```bash
# 📁 backend/docker-entrypoint.sh
seed_script_for() {
    case "$1" in
        demo)          echo "backend/scripts/seed_demo_data.py" ;;
        shoplab)       echo "backend/scripts/seed_shoplab.py" ;;
        streampulse)   echo "backend/scripts/seed_streampulse.py" ;;
        sdk-contract)  echo "backend/scripts/seed_sdk_contract.py" ;;
        *)             echo "" ;;
    esac
}

if [ -n "$SEED" ]; then
    IFS=',' read -r -a seeds <<< "$SEED"
    for raw in "${seeds[@]}"; do
        name="$(echo "$raw" | tr -d '[:space:]')"
        [ -z "$name" ] && continue
        script="$(seed_script_for "$name")"
        if [ -z "$script" ]; then
            log "unknown seed '${name}' (expected one of demo, shoplab, streampulse, sdk-contract)"
            exit 1
        fi
        if [ "$SEED_FORCE" != "true" ] && python -m backend.scripts.seed_markers check "$name"; then
            log "seed '${name}' already applied; skipping (SEED_FORCE=true to re-run)"
            continue
        fi
        log "applying seed '${name}' (${script})"
        python "$script"
        python -m backend.scripts.seed_markers mark "$name"
        log "seed '${name}' recorded"
    done
fi
```

An unknown seed name is an error, not a warning — a typo in `SEED` should stop the
container, not silently give you a platform with no data. Step 4 appends the worker count
and the proxy flags only if the command is uvicorn and the caller didn't set them:

```bash
# 📁 backend/docker-entrypoint.sh
if [ "$1" = "uvicorn" ]; then
    has_workers=false
    for arg in "$@"; do
        case "$arg" in
            --workers|--workers=*) has_workers=true ;;
        esac
    done
    if [ "$has_workers" = "false" ]; then
        set -- "$@" --workers "$WEB_CONCURRENCY"
    fi
    ...
    set -- "$@" --proxy-headers \
        --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}" \
        --no-server-header
fi

log "starting: $*"
exec "$@"
```

`--proxy-headers` makes uvicorn read `X-Forwarded-Proto` and `X-Forwarded-For`, so the API sees
`https` and the real client address behind nginx or a load balancer. `--forwarded-allow-ips`
decides **whose** forwarded headers it believes: by default only loopback and the private
(RFC 1918) ranges, which is where a reverse proxy or a load balancer inside a VPC always sits and
where a client arriving from the internet never does. uvicorn then walks the `X-Forwarded-For`
list from the right, skips the trusted hops, and takes the first address it does not trust as the
client. `FORWARDED_ALLOW_IPS` overrides the list for a topology this does not fit — for example a
proxy reached over a public address. It matters because the rate limiter keys its buckets on the
client address: SDK paths share `SDK_RATE_LIMIT_PER_MINUTE` (default 6000 per minute per IP),
most other routes 300, and the auth endpoints stricter still. Trust the wrong hop and every client
looks like the proxy — one shared bucket.

`exec` replaces the shell with uvicorn so signals reach the server — a `docker stop` sends
SIGTERM to uvicorn, which runs the lifespan shutdown (schedulers stop cleanly), rather than
to a bash script that ignores it.

The bootstrap's decision, and its lock:

```python
# 📁 backend/app/db/bootstrap.py
# Every API replica runs the bootstrap on start-up (docker-entrypoint.sh).
# A blocking, session-level advisory lock serialises them: the first replica
# creates or upgrades the schema and the first administrator, the others wait
# and then find nothing left to do.  The key is derived from the schema name
# so two schemas in one database do not block each other.
BOOTSTRAP_LOCK_NAMESPACE = "experimently.bootstrap."
...

def _bootstrap_schema(engine: Engine, schema: str) -> str:
    cfg = alembic_config()

    ensure_schema(engine, schema)

    if has_alembic_version(engine, schema):
        # Before anything reads the revision set: a row that another recorded
        # row descends from makes every alembic command fail outright.
        prune_redundant_revisions(engine, schema, cfg)
        # The same guard migrations/env.py applies, so that `alembic upgrade
        # heads` and this bootstrap behave identically on a database from the
        # other profile.
        if may_run_alembic(cfg, recorded_revisions(engine, schema), schema):
            logger.info(
                "alembic_version present in schema %s: running upgrade heads", schema
            )
            command.upgrade(cfg, "heads")
        result = "upgraded"
        reconcile_with_models(engine, schema)
    else:
        logger.info(
            "Fresh database: creating schema %s from models and stamping heads",
            schema,
        )
        create_from_models(engine, schema)
        # POSTGRES_SCHEMA already names `schema` -- alembic_sees_schema().
        command.stamp(cfg, "heads")
        result = "created"

    ensure_first_superuser(engine, schema)
    return result
```

Three helpers do the work an existing database needs. `prune_redundant_revisions` deletes an
`alembic_version` row that is an ancestor of another recorded row (left by an older build; alembic
would otherwise refuse every command). `may_run_alembic` handles a database recorded at a revision
this build has no file for: if this build has nothing left to apply it logs a warning and skips;
if it does, it **refuses** with a message naming the revisions — which is what an older image does
against a database a newer release migrated. And `reconcile_with_models` creates the module tables
a core database lacks when a full build meets it. The same three run from `migrations/env.py`, so a
raw `alembic upgrade heads` behaves the same way as the bootstrap.

Note this is a _blocking_ advisory lock (`pg_advisory_lock`), unlike the schedulers'
non-blocking `pg_try_advisory_lock` (Hour 9): a container that arrives second should _wait_
for the schema, not skip it. And the OpenSSL probe near the top of the entrypoint — a
real-world wrinkle worth knowing:

```bash
# 📁 backend/docker-entrypoint.sh
# The OpenSSL statically linked into the `cryptography` wheel selects assembly
# code paths from the CPU flags the kernel advertises. Some arm64 VMs (Docker
# Desktop on Apple M4, for one) advertise SVE/SME that the guest cannot
# execute, and the import dies with SIGILL (exit 132). Probe once; if it
# crashes, tell OpenSSL to skip the optimised paths for this process tree.
if [ -z "${OPENSSL_armcap:-}" ]; then
    if ! python -c "from cryptography.hazmat.bindings._rust import openssl" >/dev/null 2>&1; then
        log "cryptography's OpenSSL crashed on this CPU; setting OPENSSL_armcap=0 (asm optimisations off)"
        export OPENSSL_armcap=0
    fi
fi
```

**The Makefile** is the same contracts for a laptop without containers for the app itself
(Postgres and Redis still come from compose):

```makefile
# 📁 Makefile
.PHONY: db
db: ## Start Postgres and Redis only
	$(COMPOSE) up -d --wait postgres redis

.PHONY: bootstrap
bootstrap: ## Create the schema and the first administrator (idempotent)
	ENVIRONMENT=development $(PY) -m backend.app.db.bootstrap

.PHONY: dev
dev: db bootstrap ## Run the API (:8000) with reload; start the dashboard with `make web`
	ENVIRONMENT=development AUTH_PROVIDER=local $(VENV)/bin/uvicorn backend.app.main:app --reload --port 8000

.PHONY: demo
demo: ## Build the images and run the full stack, including the demo apps
	SEED=demo,shoplab,streampulse $(COMPOSE) --profile demo up -d --build --wait
...
.PHONY: test
test: test-backend test-modules test-frontend ## Everything a pull request must pass
```

`make dev` is `db` → `bootstrap` → uvicorn with reload: the entrypoint's steps 1–2 and 4,
run by hand, with the same bootstrap module. `ENVIRONMENT=development` is on the recipe itself
because the API refuses to start with no `ENVIRONMENT`, and the bootstrap creates the first
administrator with the development default password only when it says development. The other
targets worth knowing: `make core-build` and `make full-build` (copy the tree, delete `modules/`
for the core one, rebuild and test — proof a profile stands alone), `make lock` / `make
lock-check` (the hashed requirements locks), `make openapi` (the committed API snapshots) and
`make lint-boundary` (the core/modules import contracts).

That's the point of putting the logic in `backend.app.db.bootstrap` rather than in the shell
script — one implementation, several callers: the entrypoint, the Makefile, CI's
`run-backend-tests.sh`, the Helm chart's init container, and the AWS migration task, whose
command is `python -m backend.app.db.bootstrap` (`MIGRATION_COMMAND` in
`infrastructure/cdk/stacks/migration_task_stack.py`).

> **Watch Out!** `RUN_MIGRATIONS=true` is the entrypoint's default and right for compose, where
> one API container is the only writer. On AWS it is not a recommendation but a pinned fact: the
> CDK API task definition sets `RUN_MIGRATIONS=false` and `SEED=""`
> (`infrastructure/cdk/stacks/fargate_service_stack.py`, pinned exactly by
> `infrastructure/tests/test_api_task_does_not_migrate.py`), the Deploy workflow refuses an API
> revision that would run migrations on start, and the migration task the Deploy workflow runs
> before shifting traffic is the only thing that writes the schema. Two reasons: an older image
> refuses a schema a newer release migrated, so an old revision started for a replacement, a
> scale-out or a rollback would exit instead of serving; and one writer is enough. The
> consequence to remember: **`cdk deploy` alone leaves a new environment with no schema** —
> `/health` answers 200 while real requests answer 500 — until the first Deploy runs the
> migration task (Hour 11). Every migration must also be backward-compatible (expand, then
> contract), because the old revision keeps serving against the new schema during the shift.

> **By the Way** — `seed_markers` is a table, not a file, on purpose. A marker file inside
> the container would vanish with the container; a marker in the database travels with the
> data. `python -m backend.scripts.seed_markers clear demo` is the escape hatch when you
> want a seed re-applied without `SEED_FORCE` re-applying all of them.

### Try It Yourself

```bash
docker compose up -d --wait postgres redis
docker compose run --rm -e SEED=demo,sdk-contract api true          # entrypoint only: preflight, wait, bootstrap, seed, then `true`
docker compose run --rm api python -m backend.scripts.seed_markers list
docker compose run --rm -e SEED=demo api true | grep "already applied"
docker compose run --rm -e SEED=nope api true; echo "exit $?"        # unknown seed → exit 1
docker compose run --rm -e ENVIRONMENT=production api true; echo "exit $?"   # preflight → exit 78, read the list
docker compose run --rm -e RUN_MIGRATIONS=false -e SEED= api python -c "print('skipped bootstrap and seeds')"
# bootstrap by hand, twice — second run is "upgraded" with nothing to do
source venv/bin/activate && make bootstrap && make bootstrap
```

Then run two bootstraps concurrently (`make bootstrap & make bootstrap; wait`) against a fresh
database and confirm from the logs that one created and the other upgraded (found nothing to do).

### Key takeaways

- Entrypoint: demo-seed refusal + settings preflight (exit 78, every problem at once, cannot be
  skipped) → wait (deadline) → bootstrap (`RUN_MIGRATIONS`) → seeds by name, once
  (`seed_markers`), unknown name fatal, demo seeds development-only → `exec` uvicorn with
  workers and proxy flags. `set -euo pipefail`: a failed step stops the container.
- Bootstrap: fresh DB → `create_all` + `stamp heads`; existing → `upgrade heads` (two heads in a
  full checkout); refuses a database a newer release migrated; first superuser if `users` is
  empty; serialised by a blocking advisory lock.
- Proxy headers are trusted only from loopback and private ranges unless `FORWARDED_ALLOW_IPS`
  says otherwise; the rate limiter depends on it.
- One bootstrap implementation, several callers. On AWS the API never migrates: the Deploy's
  migration task is the only writer, and `cdk deploy` alone leaves no schema.

---

## Part I Workshop

**Q1. Why is the API's compose health check `/health/ready` while the image's `HEALTHCHECK`
is `/health/live`?**
The image promises only "the process answers" — it can't know a deployment's dependency
policy. Compose adds readiness so `--wait` means "can serve"; the orchestrator layers the
stricter check.

**Q2. A customer runs `docker compose up -d` and their script immediately fails with
connection refused. What's the one-word fix?**
`--wait`. First start bootstraps (and in development seeds) for up to minutes; `--wait` blocks
on readiness.

**Q3. Why does `NEXT_PUBLIC_API_URL` default to empty?**
So the bundle calls `/api/...` on its own origin and nginx proxies to the API: one origin,
no CORS, no preflight, nothing to misconfigure. An absolute URL is only for split-origin
deployments (and then `CSP_CONNECT_SRC` must include it).

**Q4. What happens if `API_UPSTREAM` is set with a trailing slash, and where is that
handled?**
`proxy_pass http://api:8000/` would strip the matched prefix and send `/v1/x` instead of
`/api/v1/x`. The dashboard entrypoint strips the trailing slash before `envsubst`.

**Q5. You start three API containers with `RUN_MIGRATIONS=true` against a fresh database
(compose `--scale`). Who creates the schema — and why doesn't the same question arise on AWS?**
Whichever takes the blocking advisory lock `experimently.bootstrap.<schema>` first; the
others wait, then find `alembic_version` present and run an `upgrade heads` that has nothing
to do. On AWS no API task bootstraps at all: the task definition pins `RUN_MIGRATIONS=false` and
the Deploy workflow's migration task is the only writer.

**Q6. Why is an unknown `SEED` name fatal, and why is `SEED=demo` fatal in production?**
A typo would otherwise produce a platform with no data and no error; `set -euo pipefail` plus an
explicit `exit 1` makes the mistake visible at the only moment it can be fixed cheaply. The demo
seed creates a user with a published password, so outside development and test the entrypoint
refuses it (exit 78) before doing anything else.

**Q7. A self-hosted API answers every request with 400 behind the customer's reverse proxy,
while its health checks pass. What's the first thing to check?**
The `Host` header. The API answers only for `PUBLIC_BASE_URL`'s host (or `ALLOWED_HOSTS`); a proxy
that rewrites `Host` to `localhost:8080` gets every request refused. Forward it unchanged.

**Quiz.** Why `exec "$@"` rather than just `"$@"` at the end of the entrypoint?
→ So uvicorn _replaces_ the shell as PID 1's child and receives SIGTERM directly on
`docker stop`; the lifespan shutdown then stops the schedulers cleanly instead of the
container being killed after the grace period.

---

## Exercises

**E1.1 — Self-host on one VM.** Using `deploy/compose/compose.yml`, stand up a production
instance for `https://experiments.customer.example`: generate the `.env`, choose the profile, put
a TLS reverse proxy in front of `127.0.0.1:8080`, set `METRICS_TOKEN`, and sign in as the first
administrator. Then, in a scratch copy of the `.env`, delete `PUBLIC_BASE_URL` and read compose's
refusal; and run the image directly with `ENVIRONMENT=production` and a short `SECRET_KEY` and
read the preflight's list.

**E1.2 — Split origin.** Serve the dashboard from `localhost:3000` and the API directly on
`localhost:8000` (absolute `NEXT_PUBLIC_API_URL`); list every setting that has to change
(build arg, `CSP_CONNECT_SRC`, `CORS_ORIGINS`) and confirm a WebSocket results stream still
connects. Then say what else changes if the same split is done with `ENVIRONMENT=production`.

**E1.3 — A fifth seed.** Add `SEED=minimal` that creates one experiment and one flag, in a new
`seed_minimal.py` beside the other seed scripts; wire it through `seed_script_for`, the markers,
and the compose comment; decide whether it belongs on the development-only list; show it runs
once.

**E1.4 — Image size.** Compare `docker image ls` for the API image with and without the
builder stage (build a single-stage variant); report the difference and what's in it.

**E1.5 — Startup race.** In the development stack, run three `api` containers (`docker compose
up -d --scale api=3` — first drop the fixed host port in a `compose.override.yml`, or the second
container cannot bind 8000) against a fresh database; inspect the logs for the bootstrap lock
behaviour and the scheduler lock (Hour 9) behaviour; count how many containers ran each scheduler
tick.

---

## Solutions

**S1.1**

```bash
cd deploy/compose
PUBLIC_BASE_URL=https://experiments.customer.example \
FIRST_SUPERUSER=ops@customer.example EXPERIMENTLY_PROFILE=core ./generate-env.sh
echo "METRICS_TOKEN=$(openssl rand -hex 32)" >> .env
docker compose up -d --wait
# reverse proxy: terminate TLS for experiments.customer.example, proxy to
# 127.0.0.1:8080, forward Host unchanged (and X-Forwarded-For / -Proto)
```

`generate-env.sh` writes `SECRET_KEY`, `AUDIT_HMAC_KEY`, `POSTGRES_PASSWORD` and a
`FIRST_SUPERUSER_PASSWORD` (sign in with that one) into a file only you can read, and refuses to
run twice. `LOG_FORMAT=json`, `ENVIRONMENT=production` and an empty `SEED` are already fixed in the
file. `CORS_ORIGINS` stays unset unless a site runs the browser SDK against this API — the
dashboard is same-origin. Without `PUBLIC_BASE_URL`, `docker compose config` (and `up`) exits
non-zero with `required: the URL people open, …`. With `ENVIRONMENT=production` and a short key,
the entrypoint's preflight prints every problem it found — the key, `PUBLIC_BASE_URL`,
`POSTGRES_SERVER` if unset — and the container exits 78 before waiting for the database, which is
the intended failure mode.

**S1.2** — build arg `NEXT_PUBLIC_API_URL=http://localhost:8000`; `CSP_CONNECT_SRC="'self'
http://localhost:8000 ws://localhost:8000"`; API `CORS_ORIGINS` must include
`http://localhost:3000` (already in the development default). The WebSocket connects because the
`useExperimentStream` hook derives `ws://` from the API base and the CSP now allows it. In
production there is no CORS default: `CORS_ORIGINS` must name the dashboard's origin explicitly
(or set `DASHBOARD_ORIGINS`, which is added to the CORS list automatically and is also where an SSO
sign-in may return to), and `PUBLIC_BASE_URL` is now the API's own origin.

**S1.3** — a `minimal)` arm in `seed_script_for` that echoes the new script's path; the script
uses the same `ensure_schema`/`SessionLocal` helpers as `seed_sdk_contract.py`; markers work
unchanged because they key on the name. The compose comment listing seed names and the
entrypoint's error message both need the new name. If it creates sample users or data a
production install must never carry, add it to the `demo|shoplab|streampulse` arm of
`refuse_demo_seeds_outside_development` too.

**S1.4** — the single-stage image carries gcc, the libc and kernel headers, libpq-dev and pip.
Nothing in it is needed at runtime; it is also more surface for the image scan in CI (Hour 10),
which is the same reason the shipped image deletes pip and setuptools. Measure the difference
rather than trusting a number from a book: it depends on the base image and the architecture.

**S1.5** — bootstrap: one "Fresh database: creating schema … and stamping heads" line, two
"alembic_version present in schema …: running upgrade heads" lines (after waiting on the lock).
Schedulers: each tick, one container logs the tick's work and two log "lock held elsewhere, tick
skipped"; `scheduler_ticks_total{status="skipped"}` climbs on two containers and
`{status="success"}` on one.

---

**Next:** [Part II — AWS →](./part-2-aws.md)

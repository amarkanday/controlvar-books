# Part I — The Local and Self-Hosted Stack

### Hours 1–3

[Contents](./README.md) · [Part II →](./part-2-aws.md)

Before AWS, the stack that runs everywhere else: on a laptop, on a customer's VM, in CI. It's
one `docker-compose.yml` (Hour 1), two images (Hour 2), and an entrypoint that turns an
empty Postgres into a seeded, signed-in platform without a human in the loop (Hour 3).
Everything in Parts II–IV is a different way of running the same images with the same
entrypoint — so this Part is the foundation, not the warm-up.

---

## Hour 1 — Docker Compose: services, profiles, health, and the `.env` contract

### What & why

A compose file is an operational contract written as YAML: which processes exist, what they
need from each other, when they're considered up, and what an operator is allowed to change.
Experimently's has four core services — `postgres`, `redis`, `api`, `frontend` — and three
**profiles** that add optional ones: `demo` (ShopLab, StreamPulse and their traffic
simulators), `tools` (pgAdmin), `aws` (LocalStack for the DynamoDB/Kinesis/S3 paths). A
profile is off unless named; `docker compose up` gives you the platform, `--profile demo`
gives you the platform with two example products generating traffic.

Three ideas carry the file. **Health-gated dependencies**: `api` waits for Postgres and Redis
to be _healthy_, not merely started; `frontend` waits for `api` to be healthy — and `api`'s
health is _readiness_ (DB reachable, bootstrap done), so `docker compose up --wait` returns
only when the whole thing can serve. **Every knob is `${VAR:-default}`**: a `.env` file or
the shell overrides any of them, defaults are for local development, and the header says
which three must change before anyone else can reach the stack. **First-start automation is
declared, not scripted**: `RUN_MIGRATIONS` and `SEED` are environment variables the
entrypoint reads (Hour 3), so "a fresh database becomes a usable platform" is a property of
the image, not of a README.

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

The `api` service — every environment variable grouped and commented, the health check
pointed at readiness, and a long `start_period` because first start includes seeding:

```yaml
# 📁 docker-compose.yml
  api:
    build:
      context: .
      dockerfile: backend/Dockerfile
      target: ${EXPERIMENTLY_EDITION:-ce}
    image: *api-image
    environment:
      # --- edition / environment ---
      ENVIRONMENT: ${ENVIRONMENT:-development}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      LOG_FORMAT: ${LOG_FORMAT:-console}          # json | console
      WEB_CONCURRENCY: ${WEB_CONCURRENCY:-1}       # 1 worker per container; scale containers instead
      # --- auth ---
      AUTH_PROVIDER: ${AUTH_PROVIDER:-local}       # local | cognito
      DEV_AUTH_BYPASS: ${DEV_AUTH_BYPASS:-false}   # never true outside development/test
      # Placeholder for local development only. Generate a real one with
      #   python -c 'import secrets; print(secrets.token_urlsafe(48))'
      SECRET_KEY: ${SECRET_KEY:-dev-only-change-me-9f1c7b2e4a6d8e0f1a2b3c4d5e6f7a8b}
      FIRST_SUPERUSER: ${FIRST_SUPERUSER:-admin@demo.com}
      FIRST_SUPERUSER_PASSWORD: ${FIRST_SUPERUSER_PASSWORD:-Demo1234!}
      # --- database ---
      POSTGRES_SERVER: postgres
      ...
      RUN_MIGRATIONS: ${RUN_MIGRATIONS:-true}
      # --- seeds: comma list of demo, shoplab, streampulse, sdk-contract ---
      SEED: ${SEED:-demo}
      SEED_FORCE: ${SEED_FORCE:-false}
      # --- redis ---
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      REDIS_REQUIRED: ${REDIS_REQUIRED:-false}
      # --- http ---
      CORS_ORIGINS: ${CORS_ORIGINS:-http://localhost:3000,http://localhost:3100,http://localhost:3200,http://localhost:3300}
      METRICS_TOKEN: ${METRICS_TOKEN:-}
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

`WEB_CONCURRENCY: 1` with "scale containers instead" is an operational decision worth
naming: one uvicorn worker per container keeps the five background schedulers to one set per
process (they coordinate across processes with a database lock — Hour 9), keeps memory
predictable, and lets the orchestrator do the scaling. `REDIS_REQUIRED: false` is the other
one: the API degrades without Redis (rate limiting falls back to memory, the results cache is
skipped), so a Redis outage doesn't fail readiness unless you say it should.

The dashboard service — port 8080 inside (unprivileged nginx), a relative API base baked in
at build time, and the upstream set at run time:

```yaml
# 📁 docker-compose.yml
  frontend:
    build:
      context: .
      dockerfile: frontend/Dockerfile
      args:
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

> **Watch Out!** The three secrets in the header. The default `SECRET_KEY` signs local JWTs
> (Build book Day 10); anyone who reads this public file can forge a session on a stack
> that kept it. The `FIRST_SUPERUSER_PASSWORD` default is the demo admin. Compose makes
> overriding trivial (`.env`), which is exactly why the defaults are allowed to exist —
> but "before exposing the stack to anyone" means _before_, and `ENVIRONMENT=production`
> makes the config validators refuse the placeholder key (Build book Day 9).

> **By the Way** — `docker compose up -d --wait` vs `up -d`. Without `--wait`, compose
> returns as soon as containers are _started_; the API is still bootstrapping and seeding
> for up to a couple of minutes on first run, and a script that immediately `curl`s it
> gets connection refused. `--wait` blocks on the health checks — readiness, for the API —
> so the next line of your script can assume a working platform. `make up` and `make demo`
> both use it.

### Try It Yourself

```bash
cp .env.example .env 2>/dev/null || true          # if present; otherwise create one
python -c 'import secrets; print("SECRET_KEY=" + secrets.token_urlsafe(48))' >> .env
docker compose up -d --wait                        # returns when api is ready
docker compose ps                                  # four services, all healthy
curl -s localhost:3000/health/ready | jq .         # through nginx → api
docker compose logs api | grep entrypoint          # wait / bootstrap / seed lines
SEED=demo,shoplab,streampulse docker compose --profile demo up -d --wait
docker compose ps --format 'table {{.Name}}\t{{.Status}}'
open http://localhost:3200                          # ShopLab
docker compose down -v                             # drop the volumes: next up re-seeds
```

Then set `REDIS_REQUIRED=true`, `docker compose stop redis`, and watch `/health/ready` turn
503 while `/health/live` stays 200.

### Key takeaways

- Four core services, three profiles (`demo`, `tools`, `aws`); every knob is
  `${VAR:-default}` and the header names the three you must change.
- Dependencies are health-gated; the API's compose health is _readiness_ so `--wait` means
  "can serve"; `start_period: 300s` covers first-run seeding.
- One worker per container; Redis optional by default; seeds and bootstrap are declared via
  `SEED` / `RUN_MIGRATIONS` and run by the entrypoint.
- The demo profile hands API keys from seed to simulator through a shared volume.

---

## Hour 2 — The images: two multi-stage Dockerfiles and the nginx single-origin trick

### What & why

An image is a promise about what's inside and how it behaves: what it listens on, how you
tell whether it's alive, what it does when started with no arguments, and — for the
platform's two images — how they find each other. The **API image** promises a Python
runtime with the dependencies installed, the repository layout preserved under `/app` so
`backend.app.*` imports work, a non-root user, a liveness `HEALTHCHECK`, and the entrypoint
from Hour 3. The **dashboard image** promises a static Next.js export served by nginx, with
`/api/*`, `/ws/*` and `/health*` proxied to the API so the browser only ever talks to one
origin.

Two decisions shape both. **Multi-stage builds** keep the runtime image small and free of
compilers (the API's builder stage has gcc and libpq-dev; the runtime has libpq5 and curl).
**The build context is the repository root** for both — the API because its imports are
`backend.app.*`, the dashboard because the in-app docs pages render the markdown from
`docs/`, which lives next to `frontend/`, not inside it.

### How Experimently does it

The API image's contract, from its own header:

```dockerfile
# 📁 backend/Dockerfile
# Experimently API image.
#
# Build context is the REPOSITORY ROOT (the code imports `backend.app.*`):
#
#   docker build -f backend/Dockerfile -t experimently-api:ce .
#   docker build -f backend/Dockerfile --target ee -t experimently-api:ee .
#
# Targets:
#   ce  (default) community edition — backend/ only
#   ee  enterprise edition — additionally copies ee/ when the directory exists
#
# Runtime contract:
#   ENTRYPOINT backend/docker-entrypoint.sh  (waits for Postgres, bootstraps the
#              schema when RUN_MIGRATIONS=true, applies SEED=... once)
#   CMD        uvicorn backend.app.main:app --workers ${WEB_CONCURRENCY:-1}
#   HEALTHCHECK GET /health/live
```

The two stages that matter, and the layout rule:

```dockerfile
# 📁 backend/Dockerfile
FROM python:${PYTHON_VERSION}-slim AS builder
...
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements/runtime.txt /tmp/runtime.txt
RUN pip install --upgrade pip && pip install -r /tmp/runtime.txt

FROM python:${PYTHON_VERSION}-slim AS runtime
...
# libpq5: psycopg2 runtime; postgresql-client: pg_isready for the entrypoint;
# curl: HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

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

Note the image `HEALTHCHECK` is **liveness** (`/health/live`) while compose's is
**readiness** (`/health/ready`). The image can't know whether a given deployment considers
Redis required; it promises only "the process answers." The orchestrator layers the
stricter check on top.

The edition targets — `ee` copies an `ee/` directory that doesn't exist in the community
checkout, with a glob so the build doesn't fail without it, and `ce` is _last_ so it's the
default:

```dockerfile
# 📁 backend/Dockerfile
# `ee/` does not exist in the community checkout. `COPY ee* /app/ee/` uses a
# glob so the build does not fail when the directory is absent (a glob that
# matches nothing is tolerated by COPY, an explicit path is not); the
# `ee/requirements.txt` install is likewise guarded.
FROM runtime AS ee
...
COPY --chown=appuser:appgroup ee* /app/ee/
RUN if [ -f /app/ee/requirements.txt ]; then \
        pip install --no-cache-dir -r /app/ee/requirements.txt; \
    else \
        echo "ee/ not present in build context; image is functionally CE"; \
    fi
USER appuser

# Stage 4: ce — the community image. Last stage = default target, so a plain
# `docker build -f backend/Dockerfile .` produces CE.
FROM runtime AS ce
```

The dashboard image — build the export, generate nginx routes for the dynamic pages from
Next's own manifest, assert the docs were found, then serve:

```dockerfile
# 📁 frontend/Dockerfile
# Stage 1 builds the Next.js static export (output: 'export' -> out/) and
# generates the nginx location blocks for the dynamic routes from
# .next/routes-manifest.json. Stage 2 serves it with nginx-unprivileged on
# port 8080 and proxies /api/, /ws/ and /health* to API_UPSTREAM (default
# http://api:8000), so the browser talks to a single origin and the API base
# baked into the bundle can stay empty (NEXT_PUBLIC_API_URL="").
...
COPY frontend/ ./
# src/pages/docs/[...slug].tsx reads ../docs relative to the frontend dir.
COPY docs/ /app/docs/

# Relative API base by default: nginx proxies /api/ to the backend. Pass an
# absolute URL only when the dashboard is served from a different origin.
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}

RUN npm run build \
    && test -f .next/routes-manifest.json \
    && node scripts/nginx-routes.mjs --out /app/nginx/routes.conf \
    && test -d out \
    # The docs pages must have found the markdown; a placeholder means the
    # docs/ copy above is missing.
    && ! grep -rl "Documentation coming soon" out/docs >/dev/null
```

That last `grep` is a build-time assertion: if someone changes the build context and the
docs copy silently fails, the image build fails rather than shipping a dashboard whose docs
pages say "coming soon."

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
would silently drop the stream.

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
> choice removes CORS from the self-hosted deployment entirely: same origin, no preflight,
> no `CORS_ORIGINS` to get wrong. The `CSP_CONNECT_SRC` knob exists only for the split-origin
> case.

> **Watch Out!** Both images must be built from the repository root (`docker build -f
> backend/Dockerfile .`). `cd backend && docker build .` fails on `COPY backend/` —
> and if it didn't, the imports would. The compose file sets `context: .` for both; the
> deploy workflow does the same.

### Try It Yourself

```bash
docker build -f backend/Dockerfile -t experimently-api:ce . && docker image ls experimently-api
docker run --rm experimently-api:ce python -c "import backend.app.main; print('imports ok')"   # entrypoint runs first…
docker run --rm -e WAIT_FOR_DB=false -e RUN_MIGRATIONS=false experimently-api:ce python -c "import backend.app.main; print('imports ok')"
docker build -f frontend/Dockerfile -t experimently-web:ce .
docker run --rm -e API_UPSTREAM=http://host.docker.internal:8000/ experimently-web:ce sh -c 'cat /etc/nginx/conf.d/default.conf | grep proxy_pass'   # trailing slash stripped
docker inspect experimently-api:ce --format '{{json .Config.Healthcheck}}' | jq .
```

Then try `docker build --target ee -f backend/Dockerfile .` in the community checkout and
read the "ee/ not present" line in the build log.

### Key takeaways

- API image: builder (gcc, libpq-dev) → runtime (libpq5, curl, non-root, `/app` mirrors the
  repo) → `ee` (guarded copy) → `ce` (default, last); liveness `HEALTHCHECK`; entrypoint +
  uvicorn.
- Dashboard image: static export + generated nginx routes + a build-time docs assertion;
  unprivileged nginx on 8080 proxies `/api`, `/ws`, `/health*` to `API_UPSTREAM`; only two
  template variables are expanded.
- Empty `NEXT_PUBLIC_API_URL` = single origin = no CORS; `X-Request-ID` is minted by nginx.
- Build context is always the repository root.

---

## Hour 3 — The entrypoint: wait, bootstrap, seed, hand over — and the Makefile

### What & why

The gap between "a container started" and "a platform is usable" is: the database has to be
reachable, the schema has to exist at the right version, an administrator has to exist so
someone can log in, and (optionally) demo data has to be present. The entrypoint closes that
gap in four steps, each skippable, each failing loudly, and each safe to run on every start
of every replica — because in Fargate there is no "first" replica, there are three starting
at once.

The bootstrap behind step 2 solves a specific historical problem: the Alembic migration
chain was autogenerated against databases that already had tables and can't be replayed from
zero. So a **fresh** database is created from the SQLAlchemy models and _stamped_ at head; an
**existing** one gets `alembic upgrade head`. Either way, an empty `users` table gets the
first superuser from `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD`. And because three
replicas may do this at once, the whole thing runs under a blocking advisory lock keyed by
schema name.

### How Experimently does it

The entrypoint's contract, and its safety property:

```bash
# 📁 backend/docker-entrypoint.sh
# Experimently API container entrypoint.
#
#   1. Wait for PostgreSQL (POSTGRES_SERVER/POSTGRES_PORT) to accept connections.
#   2. RUN_MIGRATIONS=true (default)  ->  python -m backend.app.db.bootstrap
#      (creates the schema on a fresh database, `alembic upgrade head` otherwise).
#   3. SEED=<comma list of demo,shoplab,streampulse,sdk-contract>  ->  run the
#      matching backend/scripts/seed_*.py once. Completed seeds are recorded in
#      the `seed_markers` table (backend/scripts/seed_markers.py) so a restart
#      of the container does not re-seed. SEED_FORCE=true re-runs them.
#   4. exec "$@" — by default uvicorn; `--workers ${WEB_CONCURRENCY:-1}` and the
#      proxy flags are appended when the command is uvicorn and the caller did
#      not pass them explicitly.
#
# Every step can be skipped: RUN_MIGRATIONS=false, SEED= (empty),
# WAIT_FOR_DB=false. The script runs with `set -euo pipefail`: a failed
# bootstrap or seed stops the container instead of starting a half-initialised
# API.
set -euo pipefail
```

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
    set -- "$@" --proxy-headers --forwarded-allow-ips="*" --no-server-header
fi

log "starting: $*"
exec "$@"
```

`--proxy-headers --forwarded-allow-ips="*"` is what makes `X-Forwarded-Proto` and
`X-Forwarded-For` from nginx or the ALB trusted, so the API sees `https` and the client IP
(the rate limiter is per IP; without this, every request would come "from" the proxy).
`exec` replaces the shell with uvicorn so signals reach the server — a `docker stop` sends
SIGTERM to uvicorn, which runs the lifespan shutdown (schedulers stop cleanly), rather than
to a bash script that ignores it.

The bootstrap's decision and its lock:

```python
# 📁 backend/app/db/bootstrap.py
# Every API replica runs the bootstrap on start-up (docker-entrypoint.sh).
# A blocking, session-level advisory lock serialises them: the first replica
# creates or upgrades the schema and the first administrator, the others wait
# and then find nothing left to do.  The key is derived from the schema name
# so two schemas in one database do not block each other.
BOOTSTRAP_LOCK_NAMESPACE = "experimently.bootstrap."


def _bootstrap_locked(engine: Engine, schema: str) -> str:
    cfg = alembic_config()

    ensure_schema(engine, schema)

    if has_alembic_version(engine, schema):
        logger.info("alembic_version present in schema %s: running upgrade head", schema)
        command.upgrade(cfg, "head")
        result = "upgraded"
    else:
        logger.info("Fresh database: creating schema %s from models and stamping head", schema)
        create_from_models(engine, schema)
        # env.py reads POSTGRES_SCHEMA for the alembic_version location.
        os.environ["POSTGRES_SCHEMA"] = schema
        command.stamp(cfg, "head")
        result = "created"

    ensure_first_superuser(engine, schema)
    return result
```

Note this is a _blocking_ advisory lock (`pg_advisory_lock`), unlike the schedulers'
non-blocking `pg_try_advisory_lock` (Hour 9): a replica that arrives second should _wait_
for the schema, not skip it. And the OpenSSL probe at the top of the entrypoint — a
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
	$(PY) -m backend.app.db.bootstrap

.PHONY: dev
dev: db bootstrap ## Run the API (:8000) with reload; start the dashboard with `make web`
	AUTH_PROVIDER=local $(VENV)/bin/uvicorn backend.app.main:app --reload --port 8000

.PHONY: demo
demo: ## Build the images and run the full stack, including the demo apps
	SEED=demo,shoplab,streampulse $(COMPOSE) --profile demo up -d --build --wait
...
.PHONY: test
test: test-backend test-frontend ## Everything a pull request must pass
```

`make dev` is `db` → `bootstrap` → uvicorn with reload: the entrypoint's steps 1–2 and 4,
run by hand, with the same bootstrap module. That's the point of putting the logic in
`backend.app.db.bootstrap` rather than in the shell script — one implementation, three
callers (entrypoint, Makefile, CI's `run-backend-tests.sh`).

> **Watch Out!** `RUN_MIGRATIONS=true` on every replica is right for compose and for a
> single-service deployment, and wrong for the production pipeline — there, migrations run
> as a **separate one-shot ECS task** _before_ the new code deploys (Hour 11), and the
> service's task definition should run with `RUN_MIGRATIONS=false`. Running `upgrade head`
> from a replica of the _new_ version while replicas of the _old_ version are still serving
> is exactly the race the migration task exists to avoid.

> **By the Way** — `seed_markers` is a table, not a file, on purpose. A marker file inside
> the container would vanish with the container; a marker in the database travels with the
> data. `python -m backend.scripts.seed_markers clear demo` is the escape hatch when you
> want a seed re-applied without `SEED_FORCE` re-applying all of them.

### Try It Yourself

```bash
docker compose up -d --wait postgres redis
docker compose run --rm -e SEED=demo,sdk-contract api true          # entrypoint only: wait, bootstrap, seed, then `true`
docker compose run --rm api python -m backend.scripts.seed_markers list
docker compose run --rm -e SEED=demo api true | grep "already applied"
docker compose run --rm -e SEED=nope api true; echo "exit $?"        # unknown seed → exit 1
docker compose run --rm -e RUN_MIGRATIONS=false -e SEED= api python -c "print('skipped bootstrap and seeds')"
# bootstrap by hand, twice — second run is "upgraded" with nothing to do
source venv/bin/activate && python -m backend.app.db.bootstrap && python -m backend.app.db.bootstrap
```

Then run two bootstraps concurrently (`python -m backend.app.db.bootstrap & python -m
backend.app.db.bootstrap; wait`) against a fresh database and confirm from the logs that
one created and the other upgraded (found nothing to do).

### Key takeaways

- Entrypoint: wait (deadline) → bootstrap (`RUN_MIGRATIONS`) → seeds by name, once
  (`seed_markers`), unknown name is fatal → `exec` uvicorn with workers and proxy flags.
  `set -euo pipefail`: a failed step stops the container.
- Bootstrap: fresh DB → `create_all` + `stamp head`; existing → `upgrade head`; first
  superuser if `users` is empty; serialised across replicas by a blocking advisory lock.
- One bootstrap implementation, three callers; the Makefile is the same steps for a laptop.
- In production, migrations run as a separate task before deploy — not from replicas.

---

## Part I Workshop

**Q1. Why is the API's compose health check `/health/ready` while the image's `HEALTHCHECK`
is `/health/live`?**
The image promises only "the process answers" — it can't know a deployment's dependency
policy. Compose adds readiness so `--wait` means "can serve"; the orchestrator layers the
stricter check.

**Q2. A customer runs `docker compose up -d` and their script immediately fails with
connection refused. What's the one-word fix?**
`--wait`. First start bootstraps and seeds for up to minutes; `--wait` blocks on readiness.

**Q3. Why does `NEXT_PUBLIC_API_URL` default to empty?**
So the bundle calls `/api/...` on its own origin and nginx proxies to the API: one origin,
no CORS, no preflight, nothing to misconfigure. An absolute URL is only for split-origin
deployments (and then `CSP_CONNECT_SRC` must include it).

**Q4. What happens if `API_UPSTREAM` is set with a trailing slash, and where is that
handled?**
`proxy_pass http://api:8000/` would strip the matched prefix and send `/v1/x` instead of
`/api/v1/x`. The dashboard entrypoint strips the trailing slash before `envsubst`.

**Q5. Three Fargate replicas start on a fresh database. Who creates the schema?**
Whichever takes the blocking advisory lock `experimently.bootstrap.<schema>` first; the
others wait, then find `alembic_version` present and run an `upgrade head` that has nothing
to do.

**Q6. Why is an unknown `SEED` name fatal?**
A typo would otherwise produce a platform with no data and no error. `set -euo pipefail`
plus an explicit `exit 1` makes the mistake visible at the only moment it can be fixed
cheaply.

**Quiz.** Why `exec "$@"` rather than just `"$@"` at the end of the entrypoint?
→ So uvicorn _replaces_ the shell as PID 1's child and receives SIGTERM directly on
`docker stop`; the lifespan shutdown then stops the schedulers cleanly instead of the
container being killed after the grace period.

---

## Exercises

**E1.1 — Harden a self-hosted `.env`.** Write a `.env` for a customer VM: generated
`SECRET_KEY`, real admin credentials, `ENVIRONMENT=production`, `LOG_FORMAT=json`,
`REDIS_REQUIRED=true`, a `METRICS_TOKEN`, `CORS_ORIGINS` for their dashboard origin. Then
try starting with the placeholder key still set and read the validator's refusal.

**E1.2 — Split origin.** Serve the dashboard from `localhost:3000` and the API directly on
`localhost:8000` (absolute `NEXT_PUBLIC_API_URL`); list every setting that has to change
(build arg, `CSP_CONNECT_SRC`, `CORS_ORIGINS`) and confirm a WebSocket results stream still
connects.

**E1.3 — A fifth seed.** Add `SEED=minimal` that creates one experiment and one flag; wire
it through `seed_script_for`, the markers, and the compose comment; show it runs once.

**E1.4 — Image size.** Compare `docker image ls` for the API image with and without the
builder stage (build a single-stage variant); report the difference and what's in it.

**E1.5 — Startup race.** With `WEB_CONCURRENCY=1` and three `api` replicas (`docker compose
up --scale api=3`), inspect the logs for the bootstrap lock behaviour and the scheduler lock
(Hour 9) behaviour; count how many replicas ran each scheduler tick.

---

## Solutions

**S1.1**

```dotenv
ENVIRONMENT=production
SECRET_KEY=<token_urlsafe(48)>
FIRST_SUPERUSER=ops@customer.example
FIRST_SUPERUSER_PASSWORD=<generated>
POSTGRES_PASSWORD=<generated>
LOG_FORMAT=json
REDIS_REQUIRED=true
METRICS_TOKEN=<token_urlsafe(32)>
CORS_ORIGINS=https://experiments.customer.example
SEED=
```

With the placeholder `SECRET_KEY`, the config validator (Build book Day 9) raises at import
in production and the container exits — the entrypoint's `set -e` turns that into a
stopped container with the reason in the log, which is the intended failure mode.

**S1.2** — build arg `NEXT_PUBLIC_API_URL=http://localhost:8000`; `CSP_CONNECT_SRC="'self'
http://localhost:8000 ws://localhost:8000"`; API `CORS_ORIGINS` must include
`http://localhost:3000` (already in the dev default). The WebSocket connects because the
`useExperimentStream` hook derives `ws://` from the API base and the CSP now allows it.

**S1.3** — `minimal) echo "backend/scripts/seed_minimal.py" ;;` in `seed_script_for`; the
script uses the same `ensure_schema`/`SessionLocal` helpers as `seed_sdk_contract.py`;
markers work unchanged because they key on the name. The compose comment listing seed names
and the entrypoint's error message both need the new name.

**S1.4** — the single-stage image carries gcc, libc headers and pip caches: typically
200–300 MB more. Nothing in it is needed at runtime; it's also more surface for the Trivy
scan (Hour 10).

**S1.5** — bootstrap: one "Fresh database: creating…" line, two "alembic_version present…
upgrade head" lines. Schedulers: each tick, one replica logs the tick's work and two log
"lock held elsewhere, tick skipped"; `scheduler_ticks_total{status="skipped"}` climbs on two
replicas and `{status="success"}` on one.

---

**Next:** [Part II — AWS →](./part-2-aws.md)

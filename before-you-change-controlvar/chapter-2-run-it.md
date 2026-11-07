# Chapter 2 — Run it

**Outcome:** you can run Controlvar two ways, the whole stack in Docker or the API and dashboard with
reload, and you know the two things that stop it when they are wrong.

## Two ways to run it

**The whole stack in Docker, with demo data.** This is the quickest way to see the product. From the
repository root, `docker compose up -d --wait` builds the API and dashboard images from source, starts
four containers (PostgreSQL, Redis, the API and the dashboard), and creates and seeds the database on the
way. The dashboard is on http://localhost:3000, the API on http://localhost:8000 and its documentation at
http://localhost:8000/api/v1/docs. The demo account is `admin@demo.com` with the password `Demo1234!`.
`docker compose down -v` removes it all again, volumes included. This stack is for your own machine: it
runs with development settings and publishes its ports on `127.0.0.1` only.

**The development setup, with reload.** This is what you use while you change code. `CONTRIBUTING.md`
gives it as two commands and a second shell:

```bash
make venv install   # the virtualenv, the backend's dependencies, then the dashboard's
make dev            # Postgres and Redis in Docker, the bootstrap, then the API on :8000 with reload
make web            # in a second shell: the dashboard on :3100
```

Here is what `make dev` runs:

```makefile
# 📁 Makefile
dev: db bootstrap ## Run the API (:8000) with reload; start the dashboard with `make web`
	ENVIRONMENT=development AUTH_PROVIDER=local $(VENV)/bin/uvicorn backend.app.main:app --reload --port 8000
```

`db` starts PostgreSQL and Redis with `docker compose up -d --wait postgres redis`. `bootstrap` runs
`ENVIRONMENT=development python -m backend.app.db.bootstrap`. Then the API starts with reload. Run
`make help` to see every other target.

## The setting the API will not start without

The API refuses to serve when `ENVIRONMENT` was never set. The settings would otherwise fall back to
`development`, whose relaxed rules a deployment must choose rather than fall into. The refusal is in the
app's lifespan:

```python
# 📁 backend/app/main.py
    if not settings.environment_explicit:
        logger.error(ENVIRONMENT_NOT_SET_MESSAGE)
        raise EnvironmentNotSet(ENVIRONMENT_NOT_SET_MESSAGE)
```

`make dev` and `make bootstrap` set `ENVIRONMENT=development` for you. A bare
`uvicorn backend.app.main:app` stops at start-up. Importing the app is not refused, only serving it, so
tools that import it (the OpenAPI dump, for one) still work.

## The database: bootstrap, not upgrade

A fresh database is created by the bootstrap, `backend/app/db/bootstrap.py`. On an empty database it
creates the schema from the SQLAlchemy models and stamps the migration heads. On an existing database it
runs `alembic upgrade heads`. It also creates the first administrator, from `FIRST_SUPERUSER` and
`FIRST_SUPERUSER_PASSWORD`, when there are no users yet. Their defaults in `backend/app/core/config.py`
are `admin@example.com` and `admin`. That weak password is accepted only because `ENVIRONMENT` was set
to `development` (or `test`). With any other value, or with `ENVIRONMENT` unset, the bootstrap refuses to
create the administrator and creates nothing. The two settings are read only while the `users` table is
empty, so changing them later does nothing.

> **Watch Out!** Do not run `alembic upgrade heads` on an empty database. The historical migration chain
> cannot be replayed from nothing; that is why the bootstrap exists. And it is `heads`, plural, every
> time: a full checkout has two (Chapter 7).

## The test shell

Most backend tests need PostgreSQL on `localhost:5432`, even many under `backend/tests/unit/`.
`make db` starts it. If you run pytest by hand rather than through `make`, use the shell
`CONTRIBUTING.md` gives:

```bash
source venv/bin/activate
export APP_ENV=test TESTING=true POSTGRES_SCHEMA=test_experimentation
python -m pytest backend/tests/unit -p no:cov -q
```

`POSTGRES_SCHEMA` matters more than it looks: the models bake the schema name into index names and
foreign keys at import time, and the migrations reflect whatever schema it names. The two must agree.

## The demo apps

Two demo applications drive the platform through the public SDK path, as a customer's app would:
**ShopLab**, a storefront (`demo/shoplab`, http://localhost:3200), and **StreamPulse**, a phone drawn in a
web page (`demo/streampulse`, http://localhost:3300). `./demo/setup-local.sh` seeds the demo data and
starts the API, the dashboard, both apps and their traffic simulators. `make demo` builds the images and
runs the whole stack in Docker, the two demo apps included. `demo/DEMO_GUIDE.md` walks through them.

## Try it

1. Start the Docker stack and check you can sign in:

   ```bash
   docker compose up -d --wait
   TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
     -H 'content-type: application/json' \
     -d '{"email":"admin@demo.com","password":"Demo1234!"}' | jq -r .access_token)
   curl -s localhost:8000/api/v1/auth/me -H "Authorization: Bearer $TOKEN" | jq .role
   ```

   It prints `"ADMIN"`. Open http://localhost:3000 and sign in with the same account. Ask the API which
   profile it runs with `curl -s localhost:8000/api/v1/modules | jq`. Then stop it with
   `docker compose down -v`, so its ports are free for the next step.

2. Run the development setup: `make venv install`, then `make dev`. In a second shell, `make web`.
   Open http://localhost:3100 and sign in as `admin@example.com` with the password `admin`.

3. Watch the refusal. In a third shell, start the API on another port with `ENVIRONMENT` removed:

   ```bash
   source venv/bin/activate
   env -u ENVIRONMENT -u APP_ENV uvicorn backend.app.main:app --port 8001
   ```

   The log shows `ENVIRONMENT is not set: set it to development, test, staging or production before
   starting the API.`, then `Application startup failed. Exiting.`, and the process ends.
   (`APP_ENV` is removed too because the API accepts it as the legacy spelling.)

4. With `make db` running, run one test file in the test shell above:

   ```bash
   python -m pytest backend/tests/unit/core/test_environment_explicit.py -p no:cov -q
   ```

   Every test passes. This file is the one that pins the refusal you just saw.

## Go deeper

- [Build a Platform, Day 2](../build-a-platform-in-28-days/week-1-core-loop.md): the repository and the
  development setup in depth: the virtualenv, PostgreSQL, the schema variable, uvicorn and the dashboard.
- [Build a Platform, Day 9](../build-a-platform-in-28-days/week-2-api-and-data.md): `Settings`,
  environments and the validators that fail closed.
- [Build a Platform, Day 13](../build-a-platform-in-28-days/week-2-api-and-data.md): Alembic and the
  bootstrap.
- [Platform Operations, Hours 1 to 3](../platform-operations/part-1-local-stack.md): Compose, the images
  and the container entrypoint.

---

_Previous: [Chapter 1 — The map](./chapter-1-the-map.md). Next: [Chapter 3 — Where decisions
live](./chapter-3-where-decisions-live.md)._

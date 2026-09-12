# Usage Guide

### _How to use Experimently — as an engineer integrating it and as the person running the experiment_

[← experimently-docs](../README.md) · [Onboarding](./onboarding.md) · [Troubleshooting](./troubleshooting.md)

This guide walks the product end to end: sign in, create and run an experiment, integrate an
SDK, read the results, ship a feature flag with a gradual rollout and a safety net, and
administer the platform. Every step is shown twice where it matters — on the dashboard and as
the API call the dashboard makes — because the API is the product and the dashboard is its
first client. For the _why_ behind each screen, the [Build book](../build-a-platform-in-28-days/README.md)
is cross-referenced throughout.

Local URLs assumed: dashboard `http://localhost:3000` (compose) or `:3100` (`npm run dev`),
API `http://localhost:8000`, docs `http://localhost:8000/api/v1/docs`.

---

## 1. Signing in and roles

**Dashboard.** `/login` with an email and password. The Community Edition ships with
`AUTH_PROVIDER=local`; the first administrator is created by the bootstrap from
`FIRST_SUPERUSER`/`FIRST_SUPERUSER_PASSWORD` (compose default `admin@demo.com` /
`Demo1234!`). Ten failed attempts lock the address for fifteen minutes.

**API.**

```bash
API=http://localhost:8000/api/v1
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@demo.com","password":"Demo1234!"}' | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s $API/auth/me -H "Authorization: Bearer $TOKEN"
```

Tokens last 12 hours. `POST /auth/logout` ends a session.

**Roles.** Four, assigned per user (Admin → Users):

| Role        | Experiments & flags        | Users / roles     | Reports                | Admin pages |
| ----------- | -------------------------- | ----------------- | ---------------------- | ----------- |
| `ADMIN`     | everything                 | everything        | everything             | yes         |
| `DEVELOPER` | create, edit, delete, list | read              | read                   | yes         |
| `ANALYST`   | read, list                 | read              | create, edit, delete   | no          |
| `VIEWER`    | read, list                 | read              | read                   | no          |

A non-admin can modify what they own. Superuser is a separate bit that bypasses every check;
`/admin/*` and safety settings require it. ([Build book, Day 11](../build-a-platform-in-28-days/week-2-api-and-data.md#day-11--authorization-rbac-ownership-and-the-permission-dependencies))

---

## 2. Creating an experiment

### What you need to decide first

- **Hypothesis** — one sentence: "Showing a video hero instead of an image will increase
  purchase conversion."
- **Variants** — a control and one or more treatments, with traffic allocations that sum to
  100. Each variant carries a free-form `configuration` your app reads (`{"media": "video"}`).
- **Metrics** — at least one, exactly one primary. A metric is an `event_name` your app will
  track (`purchase`), a type (`conversion`, `revenue`, `count`, `duration`, `custom`), and a
  `minimum_sample_size` (default 100) below which the platform won't recommend shipping.
- **Key** — the stable identifier SDKs use (`hero_banner`). Generated from the name if you
  leave it blank; don't rename it once live.
- **Targeting** (optional) — who is eligible: rules over user attributes the SDK sends.
- **Advanced** (optional) — sequential testing, CUPED, Bayesian analysis, a bandit, a
  mutual-exclusion group. Section 6.

Sample size: use the **Power Calculator** (`/power-calculator`, no login needed) —
`POST /power/sample-size` with baseline rate, minimum detectable effect, power and α returns
users per variant; `POST /power/mde` answers the inverse.

### Dashboard

**Experiments → New experiment.** Name (the key fills in), description, hypothesis, type
(A/B, multivariate, split-URL, bandit), variants (mark one as control; allocations must sum to
100 — the form tells you the running total), metrics (mark one primary), and the targeting
rule builder. Save creates it as **Draft**.

### API

```bash
curl -s -X POST $API/experiments/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "name": "Hero banner test",
  "key": "hero_banner",
  "hypothesis": "A video hero increases purchase conversion",
  "experiment_type": "a_b",
  "variants": [
    {"name": "Control", "is_control": true, "traffic_allocation": 50, "configuration": {"media": "image"}},
    {"name": "Video hero", "is_control": false, "traffic_allocation": 50, "configuration": {"media": "video"}}
  ],
  "metrics": [
    {"name": "Purchase conversion", "event_name": "purchase", "metric_type": "conversion", "is_primary": true, "minimum_sample_size": 500},
    {"name": "Average order value", "event_name": "purchase", "metric_type": "revenue", "event_value_path": "order_value"}
  ],
  "sequential_testing_enabled": true,
  "sequential_testing_config": {"method": "msprt", "tau_squared": 0.001, "spending_function": "obrien_fleming", "planned_looks": 10},
  "bayesian_enabled": true
}'
```

A 422 tells you exactly what's wrong (`variants: Traffic allocations must sum to 100%
(currently 90%)`, `At least one variant must be marked as control`).

### Lifecycle

```
draft ──start──► active ──pause──► paused ──start──► active
                   │                                  │
                   └────────── complete ◄─────────────┘ ──► completed ──archive──► archived
```

- **Start** (`POST /experiments/{id}/start`): needs ≥ 2 variants, a control, ≥ 1 metric.
  From then on `/tracking/assign` will assign users.
- **Pause**: stops new assignments; existing users keep their variant; results stay
  readable. This is the experiment's kill switch.
- **Complete**: freezes it; results are cached for 24 hours.
- **Archive**: hides it from default lists.
- **Schedule** (`PUT /experiments/{id}/schedule` with `start_date`/`end_date`, UTC): the
  experiment scheduler activates/completes it on the dates (checked every 15 minutes).
- **Clone** (`POST /experiments/{id}/clone`) to iterate on a hypothesis.
- **Delete**: drafts only, and the request must carry both the id and the key
  (`DELETE /experiments/{id}?experiment_key=...`).

---

## 3. Integrating an SDK

### Get an API key

**Admin → API keys → Create** (or `POST /api-keys {"name": "web-prod"}`). The plaintext key
(`eptk_…`) is shown **once**; store it in your app's secret store. Keys are per user and can
expire.

### The contract every SDK implements

| Your app asks                                  | The SDK calls                                                             |
| ---------------------------------------------- | ------------------------------------------------------------------------- |
| Which variant is this user in?                 | `POST /api/v1/tracking/assign {experiment_key, user_id, context?}`        |
| Is this flag on for this user?                 | `GET /api/v1/feature-flags/evaluate/{key}?user_id=…&context=<json>`       |
| Record that this user did X                    | `POST /api/v1/tracking/track` / `POST /api/v1/tracking/batch` (≤ 100)     |
| Report a client-side error behind a flag       | `POST /api/v1/tracking/errors[/batch]`                                    |

Three rules to design around ([Build book, Day 20](../build-a-platform-in-28-days/week-3-hard-parts.md#day-20--sdks-and-the-endpoint-contract)):

1. **The server decides.** Assignment is `md5(user_id:experiment_id) % 100` on the server and
   stored; the same `user_id` always gets the same variant. Your job is a **stable user id**
   — an account id, or a persisted anonymous id (ShopLab uses a visitor cookie). A new id per
   page load produces a new "user" every time.
2. **Successful answers are cached per user + key** (5 minutes by default); failures never
   are. A paused experiment returns 404 → the convenience call returns `"control"`.
3. **Key-less events fan out** to every experiment and flag the SDK has cached for that
   user in this process. Assign first, then track.

### Python

```python
from experimentation import ExperimentationClient   # sdk/python

client = ExperimentationClient(api_url="http://localhost:8000", api_key="eptk_...")

variant = client.get_variant("hero_banner", user_id="user-42", user_attributes={"country": "US", "tier": "premium"})
if variant == "Video hero":
    render_video_hero()

client.track(user_id="user-42", event_name="purchase", event_value=49.0, properties={"order_value": 49.0})   # no key → fans out to hero_banner
```

`get_assignment` raises `ExperimentationError` when you need to know; `get_variant` swallows
and returns `default_variant` ("control"). `track` never raises.

### JavaScript / TypeScript

```typescript
import { ExperimentationClient } from '@experimentation-platform/js-sdk';   // sdk/js

const client = new ExperimentationClient({ apiUrl: 'http://localhost:8000', apiKey: 'eptk_...' });
const user = { userId: visitorId, attributes: { country: 'US' } };

const assignment = await client.getAssignment('hero_banner', user);          // { variantName, isControl, configuration, ... } — throws on 404
const variant = await client.getVariant('hero_banner', user);                // "control" on any failure
const flag = await client.evaluateFlag('search_v2', user);                    // { enabled, reason }
await client.track(user.userId, 'purchase', { value: 49 });                   // key-less → fans out
```

### React

```tsx
import { ExperimentationProvider, useExperiment, useFeatureFlag, useTrackEvent } from '@experimentation-platform/react-sdk';

// once, at the root — wait for a stable user id before rendering
<ExperimentationProvider config={{ apiKey, baseUrl }} user={{ userId, attributes }}>
  <App />
</ExperimentationProvider>

// in a component
const hero = useExperiment('hero_banner');                 // control defaults while loading: variantKey, variantName, isControl, configuration
const { isEnabled, loading } = useFeatureFlag('search_v2');
const track = useTrackEvent();                              // track(eventName, properties?, { experimentKey?, featureFlagKey?, value? })
<button onClick={() => track('hero_cta_click', { variant: hero.variantName })}>{hero.configuration?.cta ?? 'Shop'}</button>
```

Other SDKs (Go, Java + Spring Boot starter, Swift, Kotlin, Ruby, PHP, .NET, Elixir, Flutter,
React Native, an edge runtime, OpenFeature providers) are documented under `docs/sdk/` in the
app repo with the same three calls; `docs/sdk-guide.md` records which have been verified
against a live backend.

### Sending attributes for targeting

Pass user attributes as `context` (SDKs do this from `user.attributes`). Rules can reference
them as written (`country`) or with a prefix (`user.country`, `device.os_version`,
`app.version`); nested objects flatten to dotted keys. Attributes are assumed stable per user;
call the SDK's `clear_cache()` after changing them.

### The demo apps as a reference integration

`./demo/setup-local.sh` starts **ShopLab** (`:3200`, React SDK: five experiments and two flags
on a storefront) and **StreamPulse** (`:3300`, a mobile-app simulator with targeting by OS
version, region and tier, a kill switch and a scripted rollout story). Their source under
`demo/` is the canonical example of the SDK in an app.

---

## 4. Reading results

**Experiments → [experiment] → Results** (`/results/{id}`; API `GET /results/{id}`).

### The summary card

Status, days running, total participants, and a **recommendation**:

| Recommendation      | Meaning                                                | Action                      |
| ------------------- | ------------------------------------------------------ | --------------------------- |
| `SHIP_VARIANT`      | a treatment is significantly better on the primary metric _and every variant reached its minimum sample size_ | ship it |
| `KEEP_CONTROL`      | control is significantly better                        | don't ship                  |
| `CONTINUE_TESTING`  | not enough data                                        | wait                        |
| `INCONCLUSIVE`      | no meaningful difference                               | decide on other grounds     |

### The metric table

Per metric and variant: sample size, conversions, rate (or mean), relative improvement vs
control, p-value, significance at the chosen confidence level, effect size
(negligible/small/medium/large), confidence interval. Green = significant improvement, red =
significant degradation, grey = not significant. The primary metric is listed first.

`confidence_level` (0.80–0.99) and `correction_method` (`none`, `bonferroni`,
`benjamini_hochberg`) are query parameters; with several metrics, use a correction.

### The honesty checks — read these first

- **SRM (sample-ratio mismatch).** If the observed split doesn't match the configured
  allocation (chi-square p < 0.001) the results page warns. Every p-value below it is
  meaningless until you find the cause (a targeting or SDK bug on one platform, a bot).
  Bandits drift by design and will warn.
- **Sample size meter.** Green (> 100% of required), amber (80–100%), red (< 80%). Don't
  decide on red. "Days to significance" estimates the wait at the current rate.
- **Trend chart.** Cumulative (default, smooths noise) and daily. Healthy: parallel early,
  diverging, then stable. Crossing lines suggest interaction or a bug; a day-one spike is a
  novelty effect.

### The advanced blocks

- **Sequential** (if enabled): the mSPRT evidence trajectory against the stopping boundary,
  the always-valid p-value, and `can_stop`. You may look every day; the boundary accounts for
  it. ([Statistics book, Part II](../experimentation-statistics/README.md))
- **Bayesian** (if enabled): each variant's posterior, **probability to be best**, expected
  loss, a Bayes factor, and a stopping recommendation. Reproducible: two calls on the same day
  return identical numbers.
- **CUPED** (`/results/{id}/cuped`, if configured): the variance-reduced estimate and the
  percentage of variance removed by the pre-period covariate.
- **Breakdowns** (`?breakdown=platform|country|user_tier|<any metadata key>`): per-segment
  results with Bonferroni-corrected significance, marked exploratory. Use them to find
  heterogeneous effects, not to rescue an inconclusive experiment.
- **Bandit** (`GET /bandit/{id}`): current weights per variant, pulls, estimated regret
  reduction and a recommendation.
- **Interactions** (`GET /interactions/{a}/{b}`, `/interactions/scan`): overlap between two
  experiments' audiences, a statistical interaction test, novelty detection and SUTVA checks
  for experiments that may interfere.

### Live results

The results page opens a WebSocket (`/api/v1/ws/experiments/{id}/results`) and updates every
30 seconds without reloading. Results are otherwise cached for 5 minutes while an experiment
runs (24 hours once completed); `use_cache=false` forces a recomputation.

### Making the decision

1. Primary metric: right direction, meaningful effect size, adequate sample, no SRM?
2. Secondary metrics: anything unexpected (revenue up, satisfaction down)?
3. Trend: has the novelty spike settled?
4. Record the decision (the experiment description or `POST /experiments/{id}/metadata`) —
   institutional memory is the point.
5. Ship (roll the winning configuration to 100% — usually via a feature flag) and
   **Complete** the experiment.

### Export

`GET /export/experiments?format=csv|json`, `/export/variants`, `/export/feature-flags`,
`/export/reports/overview`, `/export/reports/experiments/{id}` — streaming downloads for
spreadsheets and BI. Warehouse-native analytics (Snowflake, BigQuery, Redshift, Databricks,
ClickHouse, MySQL) under `/warehouse/*` generate the analysis SQL and sync results from your
own warehouse.

---

## 5. Feature flags

### Create and turn on

**Feature Flags → New.** Key (immutable once live — the rollout hash uses it), name,
description, rollout percentage (start at 0), targeting rules. Save creates it **inactive**.
**Activate** to make it evaluable.

```bash
curl -s -X POST $API/feature-flags/ -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"key":"search_v2","name":"Search v2","rollout_percentage":0}'
curl -s -X POST $API/feature-flags/$FLAG_ID/activate -H "Authorization: Bearer $TOKEN"
```

### How a user's answer is decided

1. Flag inactive → `enabled: false, reason: inactive` (the kill switch).
2. First matching targeting rule → that rule's percentage decides (`reason: targeting_rule`).
3. Otherwise the flag's global percentage decides (`reason: rollout`).
4. Inclusion is `md5(user_id:flag_key) % 100 < percentage` — the same bucket for the same
   user, so raising the percentage never removes anyone and lowering it removes the newest
   users first. ([Build book, Day 15](../build-a-platform-in-28-days/week-3-hard-parts.md#day-15--feature-flags-evaluation-rollout-percentages-and-reason))

### Targeting rules

The rule builder writes groups of conditions joined by AND/OR. Attributes are whatever your
SDK sends as context; operators include `equals`, `not_equals`, `contains`, `starts_with`,
`in`, `not_in`, `greater_than`…, `regex`, `is_null`/`is_not_null`, `semver_eq|gt|gte|lt|lte`,
`geo_within_radius`, `time_window`, `array_contains`, `array_intersects`. Each rule carries
its own percentage.

Common patterns:

| Goal                                      | Rule                                                             |
| ----------------------------------------- | ---------------------------------------------------------------- |
| Employees always on                       | `employee equals true` → 100%                                    |
| Fixed app version only                    | `app.version semver_gte 3.2.1` → 100%, global stays at 25%       |
| Exclude a platform                        | `os equals android` → 0%                                         |
| Region launch                             | `country in ["DE","AT","CH"]` → 100%                             |
| Business hours only                       | `now time_window {start: "09:00", end: "17:00", tz: "Europe/Berlin"}` |

### Gradual rollout (schedules)

**Feature Flags → [flag] → Rollout schedule**, or `POST /rollout-schedules`:

```json
{
  "name": "Search v2 rollout", "feature_flag_id": "<id>", "max_percentage": 100, "min_stage_duration": 24,
  "stages": [
    {"name": "Internal", "stage_order": 1, "target_percentage": 5,  "trigger_type": "time_based", "start_date": "2026-09-15T09:00:00Z"},
    {"name": "Quarter",  "stage_order": 2, "target_percentage": 25, "trigger_type": "time_based", "start_date": "2026-09-17T09:00:00Z"},
    {"name": "Half",     "stage_order": 3, "target_percentage": 50, "trigger_type": "manual"},
    {"name": "Everyone", "stage_order": 4, "target_percentage": 100, "trigger_type": "manual"}
  ]
}
```

`POST /rollout-schedules/{id}/activate` starts it; the rollout scheduler advances
`time_based` stages when their `start_date` (UTC) passes (every 15 minutes); `manual` stages
advance with `POST /rollout-schedules/stages/{id}/advance`. Percentages must be non-decreasing;
a safety rollback cancels the schedule. (`metric_based` triggers are accepted but not yet
automated.)

### Safety net (automatic rollback)

Turn on the global switch once (superuser): `POST /safety/settings {"enable_automatic_rollbacks": true}`.
Then per flag:

```bash
curl -s -X POST $API/safety/feature-flags/$FLAG_ID/config -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "enabled": true,
  "metrics": {"error_rate": {"warning_threshold": 0.02, "critical_threshold": 0.05, "comparison_type": "greater_than"}},
  "rollback_percentage": 5
}'
```

The safety scheduler (every 5 minutes) computes `error_rate = errors ÷ evaluations` over the
last 15 minutes — errors from server-side evaluation failures **and** from your clients
(`POST /tracking/errors {"feature_flag_key", "user_id", "error_type", "message", "metadata"}`).
A critical breach rolls the flag back to `rollback_percentage` (not necessarily off), records
the reason, cancels the rollout schedule and notifies Slack/email. `GET
/safety/feature-flags/{id}/check` runs the check on demand; `POST .../rollback` is the manual
version. ([Build book, Day 18](../build-a-platform-in-28-days/week-3-hard-parts.md#day-18--safety-monitoring-and-automatic-rollback))

### Kill switch and bulk operations

**Deactivate** turns a flag off for everyone instantly (`reason: inactive`); clients treat it
as "off", not as an error. `POST /feature-flags/bulk-toggle` toggles many flags at once and
streams an audit trail over SSE.

### Feature flag vs experiment

Use an **experiment** when you need to _measure_ (variants, metrics, statistics). Use a
**flag** when you need to _control exposure_ (on/off, percentage, targeting, rollback). The
common pattern: run the experiment, then ship the winner behind a flag with a gradual rollout
and a safety config.

---

## 6. Advanced experiment features

- **Sequential testing** — `sequential_testing_enabled` + config (`msprt`/`always_valid`,
  `tau_squared`, spending function, planned looks). Enable it when you'll look before the
  planned end. Read `can_stop` on the Sequential tab.
- **CUPED** — `variance_reduction_config` names a pre-experiment covariate (an event-metadata
  key such as `pre_period_spend`) and optional winsorization. Expect tighter intervals when
  the covariate predicts the metric.
- **Bayesian** — `bayesian_enabled` + `bayesian_config` (priors, ROPE, decision thresholds).
  The experiment scheduler honours a `STOP_WINNER`/`STOP_FUTILE` decision.
- **Bandits** — `optimization_type: thompson_sampling | ucb1 | epsilon_greedy`. Every
  5 minutes the bandit scheduler recomputes weights from conversions; new users are routed by
  them (existing users stay sticky). Use for _optimising_ during the run, not for a clean
  causal estimate. `GET /bandit/{id}`; `PUT /bandit/{id}/weights` for a manual override.
- **Mutual exclusion groups** — `POST /mutual-exclusion-groups {"name", "traffic_allocation"}`
  then `POST /mutual-exclusion-groups/{id}/experiments {"experiment_id"}`. A user lands in at
  most one experiment of the group (equal slots by consistent hashing).
- **Global holdout** — `POST /holdout {"name", "holdout_percentage": 5}` (1–20%). Those users
  are in no experiment (`assigned: false, reason: holdout`) so you can measure the cumulative
  effect of everything you ship.
- **Split-URL** — `experiment_type: split_url` with `split_url_config` (variant URLs,
  cookie name); the edge router (Lambda@Edge / CloudFront construct) redirects users
  consistently.
- **Dimensional breakdowns** — `?breakdown=<dimension>` on results; dimensions come from
  event metadata keys.
- **Interaction detection** — `/interactions/scan` lists active experiments with audience
  overlap; `/interactions/{a}/{b}` tests for interaction, novelty and SUTVA violations.
- **AI assistance** — `/ai/*` (experiment design suggestions, results interpretation, sample
  size) and an MCP server (`/mcp`) exposing five tools to AI coding assistants; the
  experiment **wizard** (`/wizard`) is a guided draft-and-submit API for non-technical users.

---

## 7. Administration

**Admin** (developers and admins) has pages for:

- **Users** — invite, set role, activate/deactivate (`/users`).
- **API keys** — create (plaintext once), list, revoke (`/api-keys`; admins can list/revoke
  everyone's).
- **Roles** — custom roles and direct permission grants on top of the four base roles
  (`/rbac`), with an effective-permissions resolver.
- **Safety** — the global rollback switch, per-flag configs, rollback history (`/safety`).
- **Scheduler** — run history and health for the five schedulers (`/scheduler/health`);
  a failed or stale tick is the first thing to check when something "didn't happen".
- **Audit** — every change, filterable, exportable (`/audit-logs`); the enterprise edition
  adds HMAC-signed compliance events and SOC 2 / ISO 27001 report packs (`/compliance`).
- **Notifications** — Slack channel and email preferences per user, and the delivery log
  (`/notifications`).
- **Workspaces** — isolated project namespaces with their own members, invites and scoped
  API keys (`/workspaces`); experiments and flags may belong to a workspace.
- **Integrations** — Jira, Salesforce, GitHub configs and webhooks (`/integrations`).
- **SSO** — SAML 2.0 and OIDC (Google, GitHub, Microsoft) with JIT provisioning and role
  mapping (`/auth/sso`), when `AUTH_PROVIDER=cognito`/enterprise.

Operational endpoints (no login): `/health/live`, `/health/ready`, and `/metrics`
(Prometheus; token-guarded outside development).

---

## 8. A complete walkthrough (fifteen minutes)

1. `docker compose up -d --wait`; sign in at `:3000`.
2. Power calculator: baseline 4%, MDE 10% relative, power 80% → note users per variant.
3. New experiment `hero_banner`: two variants (50/50), primary metric `purchase`
   (`minimum_sample_size` from step 2), sequential testing on. **Start** it.
4. Admin → API keys → create `walkthrough`; copy the key.
5. Assign and track from a shell:

   ```bash
   KEY=eptk_...
   for u in $(seq 1 200); do
     curl -s -X POST $API/tracking/assign -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
       -d "{\"experiment_key\":\"hero_banner\",\"user_id\":\"u$u\"}" >/dev/null
     if [ $((RANDOM % 100)) -lt 8 ]; then
       curl -s -X POST $API/tracking/track -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
         -d "{\"experiment_key\":\"hero_banner\",\"user_id\":\"u$u\",\"event_type\":\"purchase\",\"value\":49}" >/dev/null
     fi
   done
   ```

6. Results: `use_cache=false`; read the sample meter (red — 200 users is far below the
   requirement), the SRM line (fine), the Sequential tab (inconclusive). That's the honest
   answer; the platform won't recommend shipping on this data.
7. Create flag `video_hero` at 0%, activate it, add a rule `employee equals true → 100%`, a
   4-stage schedule, and a safety config with `error_rate` critical at 5% and
   `rollback_percentage` 5. Evaluate it for `u1` with and without `context={"employee":true}`
   and watch `reason` change.
8. Report ten errors with `POST /tracking/errors`, run `GET /safety/feature-flags/{id}/check`,
   and — with automatic rollbacks on — watch Admin → Safety record the rollback on the next
   tick.

You've now used every part of the product that a customer would in their first week.

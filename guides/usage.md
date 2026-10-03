# Usage Guide

### _How to use Experimently — as an engineer integrating it and as the person running the experiment_

[← experimently-docs](../README.md) · [Onboarding](./onboarding.md) · [Troubleshooting](./troubleshooting.md)

> Verified against `getexperimently/experimently` at `a4a6dbd2` (0.17.0 plus unreleased changes,
> including three breaking ones: #747, #769, #770).

This guide walks the product end to end: sign in, create and run an experiment, integrate an
SDK, read the results, ship a feature flag with a gradual rollout and a safety net, and
administer the platform. Every step is shown twice where it matters — on the dashboard and as
the API call the dashboard makes — because the API is the product and the dashboard is its
first client. Where the dashboard cannot do something yet, this guide says so and gives the
API call. For the _why_ behind each screen, the [Build book](../build-a-platform-in-28-days/README.md)
is cross-referenced throughout.

Local URLs assumed: dashboard `http://localhost:3000` (compose) or `:3100` (`npm run dev`),
API `http://localhost:8000`, docs `http://localhost:8000/api/v1/docs`. The published product
documentation is at [getexperimently.github.io/experimently](https://getexperimently.github.io/experimently/).

**Core and full.** One codebase, Apache-2.0 throughout, in two build profiles: **core**, and
**full**, which adds the optional modules (workspaces, custom roles, SSO, HIPAA, compliance,
warehouse, integrations, counters, ETL, split-URL). Features from a module are marked
_(module)_ below; a core deployment does not serve them. `GET /api/v1/modules` tells you which
profile and modules a deployment serves. To run a deployment of your own, see the
[Operations book](../platform-operations/README.md) (Helm chart, production compose, AWS).

---

## 1. Signing in and roles

**Dashboard.** `/login` with an email and password. The compose stack runs with
`AUTH_PROVIDER=local` (its default); the first administrator is created by the bootstrap from
`FIRST_SUPERUSER`/`FIRST_SUPERUSER_PASSWORD` (compose default `admin@demo.com` /
`Demo1234!`). The email matches in any letter case. Ten failed attempts lock the address for
fifteen minutes. Under `AUTH_PROVIDER=cognito` the dashboard does not sign in (use the API),
and sign-up and confirmation answer 404 unless `COGNITO_SELF_SIGNUP_ENABLED` is set.

**API.**

```bash
API=http://localhost:8000/api/v1
TOKEN=$(curl -s -X POST $API/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"admin@demo.com","password":"Demo1234!"}' | python -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
curl -s $API/auth/me -H "Authorization: Bearer $TOKEN"
```

Tokens last 12 hours. `POST /auth/logout` ends a session. To change your own password, send
`current_password` and `new_password` to `POST /users/me/password`.

**Roles.** Four, assigned per user. Access is **by role, not by ownership**:

| Role        | Experiments                               | Feature flags                             | Reports              |
| ----------- | ----------------------------------------- | ----------------------------------------- | -------------------- |
| `ADMIN`     | create, change, schedule, delete **any**  | create, change, delete **any**            | everything           |
| `DEVELOPER` | create, change, schedule, delete **any**  | create, change, delete **any**            | read                 |
| `ANALYST`   | read (including every result)             | read                                      | create, edit, delete |
| `VIEWER`    | read (including every result)             | read                                      | read                 |

ANALYST and VIEWER change no experiment or flag, **not even one they created**. The creator is
recorded and shown as the owner, but ownership is not an access rule. The experiment page
offers changes only to roles that can make them.

**Superuser** is a separate bit that bypasses every check. The **admin area** (the dashboard's
`/admin/*` pages and the API's `/api/v1/admin/*`) is **superuser-only**: the `ADMIN` role alone
does not open it. The compose administrator is a superuser.
([Build book, Day 11](../build-a-platform-in-28-days/week-2-api-and-data.md#day-11--authorization-rbac-ownership-and-the-permission-dependencies))

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
  leave it blank; don't rename it once live (assignment hashes it).
- **Targeting** (optional) — who is eligible: rules over user attributes the SDK sends.
- **Advanced** (optional) — sequential testing, CUPED, Bayesian analysis, a bandit, a
  mutual-exclusion group. Section 6. These are **API only**: the dashboard has no controls for
  them.

Sample size: use the **Power Calculator** (`/power-calculator`, no login needed) —
`POST /power/sample-size` with baseline rate, minimum detectable effect, power and α returns
users per variant; `POST /power/mde` answers the inverse.

### Dashboard

**Experiments → New experiment** opens a five-step guided setup: **Type → Details → Variants**
(including targeting) **→ Estimate** (sample size) **→ Review**. The single-page form is the same
page with `?advanced`; both share one set of answers and one create request. Allocations must
sum to 100 (the form shows the running total) and one variant is the control. Save creates
the experiment as **Draft**. Your answers survive a sign-in or key error, but not a page
reload.

The dashboard offers **A/B and multivariate** only. Split-URL and bandit experiments need
fields the form does not send (`split_url_config`, `optimization_type`); create those through
the API.

On an existing experiment, the experiment page shows who can join and lets you change it while
the experiment is **draft or paused**.

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
(currently 90%)`, `At least one variant must be marked as control`). A create with any
`status` other than draft is refused.

### Lifecycle

```
draft ──start──► active ──pause──► paused ──start──► active
                   │                                  │
                   └────────── complete ◄─────────────┘ ──► completed ──archive──► archived
```

- **Status changes only through the actions** below. `PUT /experiments/{id}` refuses a status
  change and a `schedule` (422), and answers **400** when the experiment's state refuses the
  change. A 403 means only that your role cannot make it.
- **Start** (`POST /experiments/{id}/start`): needs ≥ 2 variants, a control, ≥ 1 metric.
  From then on `/tracking/assign` will assign users.
- **Pause**: stops new assignments; existing users keep their variant; results stay
  readable. This is the experiment's kill switch. **A paused experiment stays paused** until it
  is started again or a resume is scheduled (`resume_at`).
- **Complete**: freezes it; results are cached for 24 hours.
- **Archive**: hides it from default lists.
- **Targeting** can be changed only while the experiment is **draft or paused**.
- **Schedule** (`PUT /experiments/{id}/schedule` with `start_date`/`end_date`, and an IANA
  `time_zone`): the experiment scheduler activates/completes it on the dates (checked every
  15 minutes, `EXPERIMENT_SCHEDULER_INTERVAL_MINUTES`). On a draft, an update keeps the dates
  it omits and `null` clears one; an unknown time zone is refused.
- **Clone** (`POST /experiments/{id}/clone`) to iterate on a hypothesis.
- **Delete**: drafts only, and the request must carry both the id and the key
  (`DELETE /experiments/{id}?experiment_key=...`). ADMIN and DEVELOPER may delete any draft;
  deleting a non-draft answers **400**.

---

## 3. Integrating an SDK

### Get an API key

Any signed-in user creates their own keys with `POST /api-keys {"name": "web-prod"}`. The
dashboard's **Admin → API keys** page is in the superuser-only admin area. The plaintext key
(`eptk_…`) is shown **once**; store it in your app's secret store. Keys belong to a user, can
expire, and carry **scopes**: a key with the `sdk:ruleset` scope (for server-side local
evaluation, below) can be created only by ADMIN, DEVELOPER or a superuser (403 otherwise), and
`/sdk/ruleset` refuses a key without it.

### The contract every SDK implements

| Your app asks                                  | The SDK calls                                                             |
| ---------------------------------------------- | ------------------------------------------------------------------------- |
| Which variant is this user in?                 | `POST /api/v1/tracking/assign {experiment_key, user_id, context?}`        |
| Is this flag on for this user?                 | `GET /api/v1/feature-flags/evaluate/{key}?user_id=…&context=<json>`       |
| Record that this user did X                    | `POST /api/v1/tracking/track` / `POST /api/v1/tracking/batch` (≤ 100)     |
| Report a client-side error behind a flag       | `POST /api/v1/tracking/errors[/batch]`                                    |

The assign response carries `assigned` and, when the user is not eligible, `reason`
(`holdout`, `mutual_exclusion` or `targeting`): an ineligible user gets HTTP 200 with the
control variant and no stored assignment.

Three rules to design around ([Build book, Day 20](../build-a-platform-in-28-days/week-3-hard-parts.md#day-20--sdks-and-the-endpoint-contract)):

1. **The server decides.** Assignment hashes `"{user_id}:{experiment_key}"` with MD5 (the
   first four bytes, little-endian, divided by 2³²) on the server and stores the result; the
   same `user_id` always gets the same variant. Every SDK pins the same function with golden
   vectors. Your job is a **stable user id** — an account id, or a persisted anonymous id
   (ShopLab uses a visitor cookie). A new id per page load produces a new "user" every time.
2. **Successful answers are cached per user + key** (5 minutes by default); failures never
   are. A paused experiment returns 404 → the convenience call returns `"control"`.
3. **Key-less events fan out** to every experiment and flag the SDK has cached for that
   user in this process. Assign first, then track.

**Not yet on a registry.** The SDKs are 0.x beta and not yet published to PyPI or npm; install
them from a clone of the app repository (`pip install -e sdk/python`, or the package
directories under `sdk/`).

### Python

```python
from experimentation import ExperimentationClient   # sdk/python, distribution "experimently"

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
import { ExperimentationClient } from '@getexperimently/js-sdk';   // sdk/js

const client = new ExperimentationClient({ apiUrl: 'http://localhost:8000', apiKey: 'eptk_...' });
const user = { userId: visitorId, attributes: { country: 'US' } };

const assignment = await client.getAssignment('hero_banner', user);          // { variantName, isControl, configuration, assigned?, reason?, ... } — throws on 404
const variant = await client.getVariant('hero_banner', user);                // "control" on any failure
const flag = await client.evaluateFlag('search_v2', user);                    // { enabled, config, reason }
await client.track(user.userId, 'purchase', { value: 49 });                   // key-less → fans out
```

### React

```tsx
import { ExperimentationProvider, useExperiment, useFeatureFlag, useTrackEvent } from '@getexperimently/react-sdk';

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
app repo with the same three calls; `docs/sdk-guide.md` lists them.

### Local evaluation (beta, server-side)

The JS and Python SDKs can answer flags in-process: `evaluation: 'local'` /
`evaluation="local"`. They read the server's ruleset from `GET /sdk/ruleset` (which needs an
`sdk:ruleset` key), answer with the server's own answers, send anything they cannot answer
exactly to the server, and report evaluation counts to `POST /tracking/evaluations` so the
safety monitor keeps its denominator. See `docs/sdk/local-evaluation.md` in the app repo.

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

**Experiments → [experiment] → Results** (`/results/{id}`; API `GET /results/{id}`). ANALYST and
VIEWER can read every experiment's results. Results carry a beta notice.

### The summary card

Status, days running, total participants, and a **recommendation**:

| Recommendation      | Meaning                                                | Action                      |
| ------------------- | ------------------------------------------------------ | --------------------------- |
| `SHIP_VARIANT`      | a treatment is significantly better on the primary metric _and every variant reached its minimum sample size_ | ship it |
| `KEEP_CONTROL`      | control is significantly better                        | don't ship                  |
| `CONTINUE_TESTING`  | not enough data                                        | wait                        |
| `INCONCLUSIVE`      | no meaningful difference                               | decide on other grounds     |

### The metric table

Per metric and variant: sample size (distinct assigned users), conversions, rate, relative
improvement vs control, p-value, significance at the chosen confidence level, effect size
(negligible/small/medium/large), confidence interval. Green = significant improvement, red =
significant degradation, grey = not significant. The primary metric is listed first.

**What is counted.** Conversions are **converting users**, not events: a user who purchases
twice counts once. `/results` analyses every metric as a proportion (converting users over
assigned users) with Fisher's exact test; mean metrics with Welch's t are analysed only by the
warehouse module. Intervals honour `confidence_level`, and the significance shown and the
recommendation come from one decision.

`confidence_level` (0.80–0.99) and `correction_method` (`none`, `bonferroni`,
`benjamini_hochberg`) are query parameters; with several metrics, use a correction.

### The honesty checks — read these first

- **SRM (sample-ratio mismatch).** If the observed split doesn't match the configured
  allocation (chi-square p < 0.001) the results page warns. Every p-value below it is
  meaningless until you find the cause (a targeting or SDK bug on one platform, a bot).
  Bandits drift by design and will warn.
- **Sample size meter.** Green (> 100% of required), amber (80–100%), red (< 80%). Don't
  decide on red. "Days to significance" estimates the wait at the current rate. The Sample Size
  tab plans from the control's rate; when it cannot, the required size is `null` and
  `unavailable_reason` says why.
- **Trend chart.** Cumulative (default, smooths noise) and daily. Healthy: parallel early,
  diverging, then stable. Crossing lines suggest interaction or a bug; a day-one spike is a
  novelty effect.

### The advanced blocks

- **Sequential** (if enabled): the mSPRT evidence trajectory against the stopping boundary,
  the always-valid p-value, and `can_stop`. You may look every day; the boundary accounts for
  it. ([Statistics book, Part II](../experimentation-statistics/README.md))
- **Bayesian** (if enabled): each variant's posterior, **probability to be best**, expected
  loss, a Bayes factor, and a stopping recommendation; a winner needs a probability to be best
  of at least 0.975. Reproducible: two calls on the same day return identical numbers.
- **CUPED** (`/results/{id}/cuped`, if configured): **beta**. Its covariate is not yet a
  pre-experiment metric, so today it reduces almost no variance; do not expect tighter
  intervals from it.
- **Breakdowns** (`?breakdown=platform|country|user_tier|<any metadata key>`): per-segment
  results with Bonferroni-corrected significance, marked exploratory. Use them to find
  heterogeneous effects, not to rescue an inconclusive experiment.
- **Bandit** (`GET /bandit/{id}`): current weights per variant, pulls, estimated regret
  reduction and a recommendation.
- **Interactions** (`GET /interactions/{a}/{b}`, `/interactions/scan`, beta): overlap between
  two experiments' audiences, a statistical interaction test, novelty detection and SUTVA
  checks for experiments that may interfere.
- **Post-stratification** (`POST /results/{id}/post-stratification`): not available yet; it
  answers 501.

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
spreadsheets and BI. All exports together share one limit of **10 requests a minute** per
client.

**Warehouse analysis** _(module, beta)_: routes under `/warehouse/analysis/` (`connections`,
`sources`, `experiments/{id}/runs`, `runs/{id}`), with a dashboard page at `/warehouse`. The code knows
Snowflake, BigQuery and Amazon Athena, and every connector is **disabled** until its real check
has passed, so no deployment can use one yet. It analyses proportion and mean metrics with an
SRM check, and shows the run's SQL to ANALYST and above.

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

The create contract: an unknown field answers 422 (`extra_forbidden`); a key that already
exists answers **409**, and a key with capitals or spaces 422; `default_value` accepts only
`false` for now (`true` answers 422); the rules field is `targeting_rules` (there is no
`rules`); `status` is read-only (422 `read_only` if it disagrees — turn a flag on or off with
`is_active` or the activate route). An archived flag must be unarchived before it can be turned
on.

### How a user's answer is decided

1. Flag inactive → `enabled: false, reason: inactive` (the kill switch).
2. The user matches the targeting rules → they are bucketed with the rules object's
   `rollout_percentage` (100 unless set there) (`reason: targeting_rule`).
3. Otherwise the flag's global percentage decides (`reason: rollout`).
4. Inclusion is `md5(user_id:flag_key) % 100 < percentage` (the whole digest, a different
   function from experiment assignment) — the same bucket for the same user, so raising the
   percentage never removes anyone and lowering it removes the most recently added users first.
   ([Build book, Day 15](../build-a-platform-in-28-days/week-3-hard-parts.md#day-15--feature-flags-evaluation-rollout-percentages-and-reason))

### Targeting rules

The rule builder writes groups of conditions joined by AND/OR. Attributes are whatever your
SDK sends as context; operators include `equals`, `not_equals`, `contains`, `starts_with`,
`in`, `not_in`, `greater_than`…, `regex` (RE2 syntax), `is_null`/`is_not_null`,
`semver_eq|gt|gte|lt|lte`, `geo_within_radius`, `time_window`, `array_contains`,
`array_intersects`. There is **one** percentage for everyone who matches (the rules object's);
the builder has no per-rule percentage.

Rules are **validated when saved**: an unknown key at any level, an unknown operator, a value
the operator cannot use, a regex RE2 refuses, nesting deeper than 10 levels or more than 1,000
nodes all answer 422, naming the place (never the value). The legacy list shape is refused on
save; flags stored with it before still evaluate, but only `eq`, `ne`, `gt`, `lt`, `contains`
and `in` conditions match there. `python -m backend.scripts.check_targeting_rules` (and
`check_regex_rules`) lists stored rules the API would now refuse.

Common patterns:

| Goal                                      | Rules                                                     | Global % |
| ----------------------------------------- | --------------------------------------------------------- | -------- |
| Employees only                            | `employee equals true`                                    | 0        |
| Employees always, plus a quarter of users | `employee equals true`                                    | 25       |
| A minimum app version only                | `app.version semver_gte 3.2.1`                            | 0        |
| Everyone except one platform              | `os not_equals android`                                   | 0        |
| Region launch                             | `country in ["DE","AT","CH"]`                             | 0        |

A user who does not match falls through to the global percentage, so "exclude" means a rule
that the excluded users do **not** match (`not_equals`, `not_in`) with the global percentage
at 0.

### Gradual rollout (schedules)

Schedules are **API only**: the flag page shows a flag's schedule but cannot create one.
`POST /rollout-schedules`:

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
advance with `POST /rollout-schedules/stages/{id}/advance`. Percentages must be non-decreasing.
A safety rollback **pauses** every active schedule of the flag, and a paused schedule is never
advanced. (`metric_based` triggers are accepted but not yet automated.)

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
A critical breach rolls the flag back, records the reason, pauses its rollout schedules and
notifies Slack/email. What the rollback does depends on `rollback_percentage` (0–100; anything
else answers 422):

- **0**: the flag is turned **off** for everyone, users matched by a targeting rule included
  (`reason: inactive`);
- **1–100**: only the global percentage drops to that value; users matched by a targeting rule
  keep the rules' percentage.

An archived flag, one already off, or one already at or below the target is skipped.
`GET /safety/feature-flags/{id}/check` runs the check on demand; `POST .../rollback` is the
manual version, and records who ran it.
([Build book, Day 18](../build-a-platform-in-28-days/week-3-hard-parts.md#day-18--safety-monitoring-and-automatic-rollback))

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

All of these are set through the API; the dashboard's create and edit screens have no
controls for them.

- **Sequential testing** — `sequential_testing_enabled` + config (`msprt`/`always_valid`,
  `tau_squared`, spending function, planned looks). Enable it when you'll look before the
  planned end. Read `can_stop` on the Sequential tab.
- **CUPED** (beta) — `variance_reduction_config` names a covariate (an event-metadata key
  such as `pre_period_spend`) and optional winsorization. Its covariate is not yet a
  pre-experiment metric, so it reduces almost no variance today.
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
- **Split-URL** _(module)_ — `experiment_type: split_url` with `split_url_config` (variant
  URLs, cookie name); the edge router redirects users consistently. The dashboard does not
  offer this type.
- **Dimensional breakdowns** — `?breakdown=<dimension>` on results; dimensions come from
  event metadata keys.
- **Interaction detection** — `/interactions/scan` lists active experiments with audience
  overlap; `/interactions/{a}/{b}` tests for interaction, novelty and SUTVA violations.
- **AI assistance** — `/ai/*` (experiment design suggestions, results interpretation, sample
  size) and an MCP manifest (`GET /mcp/manifest`) for AI coding assistants. The old
  `/wizard` draft-and-submit API is **deprecated**; the guided setup is the dashboard's
  browser-side wizard (section 2).

---

## 7. Administration

The **admin area** is for **superusers** only (the `ADMIN` role alone does not open it). Its
pages:

- **Users** — invite, set role, activate/deactivate, with search and paging (`/users`;
  `PATCH /admin/users/{id}`, beta, changes role or active status). You cannot remove your own
  superuser access or deactivate yourself (400), and email addresses are unique whatever their
  letter case (409).
- **API keys** — create (plaintext once), list, revoke (`/api-keys`; admins can list/revoke
  everyone's).
- **Roles** _(rbac module)_ — custom roles and direct permission grants on top of the four
  base roles (`/rbac`), with an effective-permissions resolver.
- **Safety** — the global rollback switch, per-flag configs, rollback history (`/safety`).
- **Scheduler** — run history and health for the five schedulers (`/scheduler/health`);
  a failed or stale tick is the first thing to check when something "didn't happen".
- **Audit** — every flag and experiment create, change, delete and toggle, filterable,
  exportable (`/audit-logs`). The **compliance** module adds HMAC-signed audit events and SOC 2 /
  ISO 27001 report packs (`/compliance`).
- **Notifications** — Slack channel and email preferences per user, and the delivery log
  (`/notifications`).

Outside the admin area, from the modules:

- **Workspaces** _(module)_ — its own pages at `/workspaces`. A workspace **groups users** into
  a team with members, invites and workspace API keys. It does **not** decide which
  experiments, flags or API keys a user can see or change, and experiments and flags are not
  created inside one; the platform role decides that.
- **Integrations** _(module)_ — Jira, Salesforce, GitHub configs and webhooks
  (`/integrations`), API only: there is no dashboard page.
- **SSO** _(module)_ — OIDC sign-in through `okta`, `google` or `github`, with JIT provisioning
  and role mapping (`/auth/sso`); the login page shows **Sign in with SSO**. It needs
  `AUTH_PROVIDER=local`. SAML 2.0 sign-in is not available yet, and `microsoft`, `azure_ad`
  and `onelogin` are not supported.

Operational endpoints (no login): `/health/live`, `/health/ready`, and `/metrics`
(Prometheus; token-guarded outside development). An unexpected error answers a fixed message
with the request ID; grep the API log for it.

---

## 8. A complete walkthrough (fifteen minutes)

1. `docker compose up -d --wait`; sign in at `:3000` (the compose admin is a superuser).
2. Power calculator: baseline 4%, MDE 10% relative, power 80% → note users per variant.
3. New experiment `hero_banner` through the API call in section 2 (two variants at 50/50,
   primary metric `purchase` with `minimum_sample_size` from step 2, sequential testing on;
   the dashboard cannot turn sequential testing on). **Start** it from the dashboard or with
   `POST /experiments/{id}/start`.
4. Create an API key `walkthrough` (Admin → API keys, or `POST /api-keys`); copy the key.
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
7. Create flag `video_hero` at 0%, activate it, add a rule `employee equals true` (the rules'
   percentage defaults to 100), a 4-stage schedule with `POST /rollout-schedules` (API only),
   and a safety config with `error_rate` critical at 5% and `rollback_percentage` 5. Evaluate it
   for `u1` with and without `context={"employee":true}` and watch `reason` change.
8. Raise the flag's global percentage to 50% (safety skips a flag already at or below its
   `rollback_percentage`), report ten errors with `POST /tracking/errors`, run
   `GET /safety/feature-flags/{id}/check`, and — with automatic rollbacks on — watch
   Admin → Safety record the rollback to 5% on the next tick.

You've now used every part of the product that a customer would in their first week.

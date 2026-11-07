# Part IV — Data & Forms

### Hours 14–17

[← Part III](./part-3-nextjs.md) · [Contents](./README.md) · [Part V →](./part-5-ui.md)

Data in, data out. This Part is the services layer — one typed object per API resource,
mirroring the backend's routes (Hour 14); the new-experiment form — one reducer behind a
guided wizard and a single-page view, validation that mirrors the server, and failures mapped
by status (Hour 15); lists with filters, offset pagination and lifecycle actions that keep
the UI honest while a request is in flight and never offer what the user may not do
(Hour 16); and a WebSocket hook with reconnect, terminal close codes and a
subprotocol token (Hour 17).

---

## Hour 14 — The services layer: one object per resource

### What & why

A **service** is a plain object (or class with static methods) whose methods map one-to-one
onto API routes: `ExperimentsService.list()` is `GET /api/v1/experiments/`, `.start(id)` is
`POST /api/v1/experiments/{id}/start`. Each method is typed with the mirror types from Part I
and calls `apiFetch<T>` — nothing else. Pages call services; services call `apiFetch`;
`apiFetch` calls `fetch`. That's the whole data path, and each layer has one job:

- **Page**: local state, when to fetch, what to render.
- **Service**: which route, which method, which query/body, which response type.
- **`apiFetch`**: base URL, token, JSON, errors, 401.

The payoff is testability at each seam: a page test mocks `apiFetch` (or the service); a
service test mocks `global.fetch`; `apiFetch`'s own tests mock `fetch` and assert headers.
And a URL guard test (Hour 23) checks every service path against the live OpenAPI spec.

#### Why a comment on every lifecycle method

`ExperimentsService` documents the backend's state machine next to the methods that drive
it — `start: draft|paused → active; pause: active → paused; …` — because the page's
`ACTIONS_BY_STATUS` table depends on it and a reviewer should see both in one place.

### In the wild

The experiments service — query parameters mapped by name, lifecycle transitions documented:

```typescript
// 📁 frontend/src/services/experiments.ts
/**
 * Query parameters accepted by `GET /api/v1/experiments/`
 * (`status_filter`, `skip`, `limit`, `search`, `sort_by`, `sort_order`).
 */
export interface ExperimentListParams {
  status?: ExperimentStatus;
  skip?: number;
  limit?: number;
  search?: string;
  sort_by?: 'created_at' | 'updated_at' | 'name' | 'status';
  sort_order?: 'asc' | 'desc';
}

const BASE = '/api/v1/experiments';

export const ExperimentsService = {
  async list(params?: ExperimentListParams): Promise<ExperimentListResponse> {
    return apiFetch<ExperimentListResponse>(BASE, {
      query: {
        status_filter: params?.status,
        skip: params?.skip,
        limit: params?.limit,
        search: params?.search,
        sort_by: params?.sort_by,
        sort_order: params?.sort_order,
      },
    });
  },

  async get(id: string): Promise<Experiment> {
    return apiFetch<Experiment>(`${BASE}/${id}`);
  },

  /**
   * `POST /api/v1/experiments`. Pass `redirectOn401: false` to get the 401 as
   * an error instead of being sent to the login page; `/experiments/new` does,
   * so an expired session never costs the answers on screen.
   */
  async create(data: CreateExperimentRequest, options: CreateOptions = {}): Promise<Experiment> {
    return apiFetch<Experiment>(BASE, { method: 'POST', json: data, ...options });
  },
  ...
  async update(id: string, data: Partial<Experiment>): Promise<Experiment> {
    return apiFetch<Experiment>(`${BASE}/${id}`, { method: 'PUT', json: data });
  },

  // The route requires `experiment_key`, which must equal the experiment id
  // (a confirmation that the caller means this experiment). Only a DRAFT
  // experiment can be deleted.
  async delete(id: string): Promise<void> {
    await apiFetch<void>(`${BASE}/${id}`, {
      method: 'DELETE',
      query: { experiment_key: id },
    });
  },

  // Lifecycle transitions — each returns the updated experiment.
  // start: draft|paused → active; pause: active → paused;
  // complete: active|paused → completed; archive: any non-archived → archived.
  async start(id: string): Promise<Experiment> {
    return apiFetch<Experiment>(`${BASE}/${id}/start`, { method: 'POST' });
  },
  ...
};
```

Note the query mapping — the client-side name `status` becomes the server's `status_filter`,
and `undefined` values are dropped by `buildQuery`, so an unset filter sends no parameter at
all. Two methods carry a decision from the API into the client: `create` accepts options so
one caller can opt out of the 401 redirect, and `delete` sends the `experiment_key` the route
requires as a confirmation. More methods you did not see in the excerpt are worth a look in
the file: `estimateSampleSize` (advice for the wizard's Estimate step, also with
`redirectOn401: false`), `clone` (`POST .../clone` with no body), `schedule`
(`PUT .../schedule` with only the dates that changed), and two more `PUT`s to the same URL as
`update`.

That last point is a small lesson in what a type is for. `update`, `updateDetails` and
`updateDesign` all send `PUT /api/v1/experiments/{id}`, and they differ only in their body type:

```typescript
// 📁 frontend/src/services/experiments.ts
/**
 * The body of a details edit: the changed subset of these three fields and
 * nothing else. The update route replaces variants and metrics wholesale when
 * a request carries them, so this type has no room for either.
 */
export interface ExperimentDetailsUpdate {
  name?: string;
  description?: string | null;
  hypothesis?: string | null;
}
...
  /**
   * `PUT /api/v1/experiments/{id}` with exactly `{variants, metrics}`, both
   * complete. The API replaces both lists in one commit, on a DRAFT
   * experiment only (400 otherwise, for every role); `buildDesignPayload`
   * builds the body.
   */
  async updateDesign(id: string, body: ExperimentDesignUpdate): Promise<Experiment> {
    return apiFetch<Experiment>(`${BASE}/${id}`, { method: 'PUT', json: body });
  },
```

The server route is one function with a dangerous default — send `variants` and it replaces every
variant — so the client gives each caller a body type that can say only what that caller means. A
details edit *cannot* carry variants by accident; it would not compile. `Partial<Experiment>`
(the general `update`) can say anything; its one caller outside the tests, the targeting section,
passes a literal `{ targeting_rules: ... }`, so what it sends is at least visible where it is sent.

Those methods, with `delete`, are driven by one component, `ExperimentManageSection` on the detail
page, and each button is offered by its own permission check: **Clone** for a user who may create
experiments; **Schedule**, **Edit details** and **Edit variants and metrics** for a user who may
change them, and only on a draft; **Delete draft** for a user who may delete them. It is the same
shape as Hour 15's create form in small: every failure is mapped by status to a fixed sentence in
the page's own words (`ERROR_COPY`), and nothing the server wrote in an error body is shown.

A service that also owns the **normalisation** of two backend shapes for one resource — the
comment explains a real inconsistency and the helpers hide it from pages:

```typescript
// 📁 frontend/src/services/featureFlags.ts
/**
 * Feature flag as returned by the API.
 *
 * Two shapes exist on the backend:
 * - `GET /feature-flags/{id}`, activate/deactivate and `PUT` return the service
 *   dict: `{ status: "active"|"inactive", rules, rollout_percentage, ... }`.
 * - `GET /feature-flags/` items are validated by `FeatureFlagReadExtended`:
 *   `{ status: "active"|"inactive"|"archived", is_active, targeting_rules, ... }`
 *   where `is_active` is derived from `status` server-side.
 * Use `isFlagOn()` / `flagRules()` instead of reading either field directly.
 */
export interface FeatureFlag {
  id: string;
  name: string;
  key: string;
  status?: FeatureFlagStatus | string;
  is_active?: boolean;
  rollout_percentage: number;
  targeting_rules?: TargetingRules | Record<string, unknown> | null;
  rules?: TargetingRules | Record<string, unknown> | null;
  ...
}

export function isFlagOn(flag: Pick<FeatureFlag, 'status' | 'is_active'>): boolean {
  if (typeof flag.status === 'string') return flag.status.toLowerCase() === 'active';
  return flag.is_active === true;
}

export function flagRules(flag: Pick<FeatureFlag, 'targeting_rules' | 'rules'>): object | null {
  const value = flag.targeting_rules ?? flag.rules ?? null;
  return value && typeof value === 'object' ? (value as object) : null;
}
```

`Pick<FeatureFlag, 'status' | 'is_active'>` types the helper to accept _anything with those
two fields_ — a list row or a detail object. This is the mirror principle applied honestly:
when the backend has two shapes, the service admits it and gives pages one function.

A class-with-statics variant, with optional query parameters coerced to `undefined` so
they're omitted:

```typescript
// 📁 frontend/src/services/results.ts
export class ResultsService {
  static async getResults(
    experimentId: string,
    params?: ResultsQuery
  ): Promise<ExperimentResultsResponse> {
    return apiFetch<ExperimentResultsResponse>(`/api/v1/results/${experimentId}`, {
      query: {
        breakdown: params?.breakdown || undefined,
        correction_method: params?.correction_method,
        confidence_level: params?.confidence_level,
      },
      timeoutMs: LONG_REQUEST_TIMEOUT_MS,
    });
  }
  ...
  /**
   * The planned sample size. Send only what the user changed: anything left
   * out is decided by the server (the observed control rate, the effect saved
   * with the experiment as its primary metric's `expected_effect`, or a 5%
   * relative MDE when none is saved, 80% power, and the experiment's own
   * confidence level and correction).
   */
  static async getSampleSize(
    experimentId: string,
    overrides?: SampleSizeOverrides
  ): Promise<SampleSizeResult> {
    return apiFetch<SampleSizeResult>(`/api/v1/results/${experimentId}/sample-size`, {
      query: {
        baseline_conversion_rate: overrides?.baseline_conversion_rate,
        mde: overrides?.mde,
        power_target: overrides?.power_target,
        confidence_level: overrides?.confidence_level,
        correction_method: overrides?.correction_method,
      },
      timeoutMs: LONG_REQUEST_TIMEOUT_MS,
    });
  }
  ...
}
```

Every method in this class passes `timeoutMs: LONG_REQUEST_TIMEOUT_MS`: computing results can take
longer than `apiFetch`'s 30-second default (Hour 3), and these routes are reads or safe to repeat,
which is the condition `api.ts` sets for the long deadline. The deadline is a property of the route,
so the service is where it is chosen; no page or component passes one.

`getResults` takes a typed `ResultsQuery`: `correction_method` and `confidence_level` are optional,
and a setting left out is the experiment's own stored one (the results dashboard reads them from the
experiment and sends them explicitly, and falls back to sending none, letting the server use the stored
settings, if the experiment cannot be read). `getSampleSize` takes a typed
`SampleSizeOverrides` object and sends only the fields the user changed; anything left `undefined`
is dropped, and the server fills in its defaults. The
results dashboard's sample-size panel lets the user change these (Hour 20).

(Two styles — an object literal and a class with statics — coexist; both are "a namespace of
typed route calls." Match the file you're in.)

The query builder that makes `undefined` mean "omit":

```typescript
// 📁 frontend/src/services/api.ts
export function buildQuery(query?: Record<string, QueryValue>): string {
  if (!query) return '';
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null) continue;
    params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : '';
}
```

> **Watch Out!** `params?.breakdown || undefined` in `ResultsService` uses `||` on purpose:
> an empty string `''` should be omitted too. That's the one place `||` beats `??` — when
> the falsy values you want to drop include `''`. (For numbers like `mde`, the code uses no
> coercion so `0` would be sent — which is correct there.)

> **Did You Know?** Services never import React and never hold state. That's what lets the
> same `ExperimentsService` be used from a page, a hook, a test, or a Node script — and
> what lets the Playwright scenario runner seed data through the API with plain `fetch`.

### Try It Yourself

Write `RolloutSchedulesService` for `/api/v1/rollout-schedules` in the house style: `list`,
`get`, `create`, `activate`, `pause`, `cancel`, `advanceStage(stageId)`, each typed with a
mirror interface, with a comment documenting the schedule's state machine. Then check your
paths against the API:

```bash
cd frontend && npm run openapi:dump && grep -o '"/api/v1/rollout-schedules[^"]*"' src/tests/fixtures/openapi.json | sort -u
```

Then compare with the real one, which arrived with the flag page's rollout section:
`frontend/src/services/rolloutSchedules.ts`. Three differences are worth a minute each. It has no
`list` or `get`: the flag page reads schedules through `FeatureFlagsService.listRolloutSchedules`,
filtered by the flag, and the comment at the top says so. Its request types are narrower than the
API's: `DashboardTrigger` is `'manual' | 'time_based'`, because a `metric_based` stage "never starts
by itself" and the dashboard does not offer one. And its header names the permission every route
needs (UPDATE on the schedule's flag) and the date format it sends (naive UTC, which the API reads
as UTC). A service file is where those facts belong: next to the calls they govern.

### Common mistakes

- **Calling `apiFetch` from a page.** Add a service method.
- **Renaming query parameters "for clarity."** The service maps client names to server
  names in one place; don't spread that mapping.
- **A service that stores state or imports React.**
- **Sending `''` for an absent optional filter** — coerce to `undefined`.

### Key takeaways

- One service per resource; methods map 1:1 to routes; typed with mirror interfaces; only
  `apiFetch` inside.
- Query objects drop `undefined`/`null`; use `|| undefined` to also drop `''`.
- When the backend has two shapes, the service documents it and exposes helpers
  (`isFlagOn`, `flagRules`).
- Lifecycle methods carry the state machine in a comment; the page's table mirrors it.

---

## Hour 15 — Forms: one reducer, two views, server messages

### What & why

A form in this dashboard is **controlled inputs bound to state**, a **client-side validator
that mirrors the server's rules** for instant feedback, and a **submit function** that builds
the request payload, calls the service, and turns a refusal into words the user can act on.
Nothing is saved until submit; the server is the authority; the client's validator exists so
the user doesn't wait for a round trip to learn that allocations must add up to 100.

`/experiments/new` is the dashboard's one big form, and it has **two views**:

- **Guided setup** (the default): five steps — Type, Details, Variants, Estimate, Review —
  one part of the form at a time, with the step in the URL (`?step=details`) so Back and
  Forward work.
- **The single-page form** at `/experiments/new?advanced`: every field on one page, one
  submit button.

Both views read the same state and dispatch to the same reducer (Hour 6's sidebar), run the
same validators and send the same request. A link at the top of each switches to the other,
and the answers carry over because there is only one copy of them.

Four rules keep forms honest:

1. **Keep the form in a pure module.** State shape, reducer, validators and payload builder
   live in `src/components/experiments/new/formState.ts`, which imports no React. Views
   render slices of it; tests call it directly.
2. **Mirror, don't reinvent.** `validateForm` implements the same rules as
   `ExperimentCreate`'s validators — one control, allocations sum to 100, one primary metric —
   with messages close to the server's. When they diverge, the server wins.
3. **Build the payload from state, trimming and normalising.** `name.trim()`, `description
   || undefined` (so an empty textarea isn't sent as `""`), `Number(x) || 0`,
   `targeting_rules: null` when there are no groups.
4. **Map a refusal by status, never by its words.** A 409 is "that key is taken" whatever the
   sentence says; the page writes its own copy and offers the fix.

### In the wild

The page owns the state and the submit, and picks a view:

```tsx
// 📁 frontend/src/pages/experiments/new.tsx
export default function NewExperimentPage() {
  const router = useRouter();
  const auth = useOptionalAuth();
  const { effective, loading: permissionsLoading } = useEffectivePermissions();
  const [state, dispatch] = useReducer(experimentFormReducer, undefined, createInitialFormState);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<CreateError | null>(null);
  ...
  const allowed = canCreateExperiment(auth?.user, effective);

  // Nothing reads the query before the router has it: a static export renders
  // first with an empty one, which would drop `?advanced` and `?step`. Nor is
  // the notice shown while the effective map may still add EXPERIMENT CREATE
  // (#891): neither the notice nor the form until it answers.
  if (!router.isReady || (!allowed && permissionsLoading)) {
    return (
      <div className="flex-1 bg-slate-50">
        <PageTitle title="New Experiment" />
        <p className="sr-only" data-testid="new-experiment-loading">
          Loading
        </p>
      </div>
    );
  }

  // `?advanced` carries no value, so it parses to "" — check for the key.
  const advanced = 'advanced' in router.query;
```

Three details to notice. The page renders nothing but a loading marker until `router.isReady`
(Hour 12): on a static export `query` is `{}` on the first render, which would silently drop
`?advanced`. `'advanced' in router.query` checks for the key, because `?advanced` has no value
and parses to `""`, which is falsy. And `canCreateExperiment(auth?.user, effective)` decides
whether to show a form at all: a user whose role does not create experiments, and who holds no
grant that does, gets a notice (`data-testid="create-not-allowed"`) saying their role can view
experiments but not create them, because the API would refuse the create (Hour 16). The loading
branch waits for that answer too: while the effective map (Hour 9) might still add `experiment:
create`, the page shows neither the notice nor the form.

The client-side mirror of the server's validators, split at its seams so a step can check
only its own part:

```typescript
// 📁 frontend/src/components/experiments/new/formState.ts
export function checkName(name: string): string | null {
  if (!name.trim()) return 'Name is required.';
  return null;
}

export function checkVariants(variants: VariantFormData[]): string | null {
  if (variants.length === 0) return 'Add at least one variant.';
  if (variants.some((v) => !v.name.trim())) return 'Every variant needs a name.';
  if (!variants.some((v) => v.is_control)) return 'One variant must be marked as control.';
  const total = allocationTotalOf(variants);
  if (total !== 100) return `Variant allocations must add up to 100% (currently ${total}%).`;
  const empty = variants.findIndex(hasNoShare);
  if (empty >= 0) return `“${variants[empty].name.trim()}”: ${ZERO_SHARE}`;
  const problems = configurationProblems(variants);
  const first = problems.findIndex((p) => p !== null);
  if (first >= 0) {
    return `The configuration of “${variants[first].name.trim()}” needs fixing: ${problems[first]}`;
  }
  return null;
}

export function checkMetrics(metrics: MetricFormData[]): string | null {
  if (metrics.length === 0) return 'Add at least one metric.';
  if (metrics.some((m) => !m.name.trim() || !m.event_name.trim())) {
    return 'Every metric needs a name and an event name.';
  }
  // The API stores one metric per name per experiment and refuses a repeat.
  const names = metrics.map((m) => m.name.trim());
  if (new Set(names).size !== names.length) return 'Metric names must be unique.';
  if (!metrics.some((m) => m.is_primary)) return 'Choose a primary metric.';
  return null;
}

/** Client-side mirror of the `ExperimentCreate` validators; returns the first problem. */
export function validateForm(
  name: string,
  variants: VariantFormData[],
  metrics: MetricFormData[],
): string | null {
  return checkName(name) ?? checkVariants(variants) ?? checkMetrics(metrics);
}
```

`??` chains the checks: the first non-null message wins, in the order name, variants, metrics.
One check here is the client's own: a variant at 0% passes the sum and is refused anyway
(`ZERO_SHARE`, "This version gets 0% of traffic. Give it a share, or remove it."). Its doc comment
gives the reason: "the API accepts it, but no user is ever assigned to it". A mirror may be
*stricter* than the server when the thing it refuses can only be a mistake; it must never be
looser. Compare with the server:

```python
# 📁 backend/app/schemas/experiment.py
        if not any(variant.is_control for variant in self.variants):
            raise ValueError("At least one variant must be marked as control")
        ...
        if total_allocation != 100:
            raise ValueError(
                f"Traffic allocations must sum to 100% (currently {total_allocation}%)"
            )
```

Same rules, similar words, two places — deliberately. The client says "Variant allocations
must add up to 100% (currently 120%)."; the server says "Traffic allocations must sum to 100%
(currently 120%)". The client's check is instant; the server's is law.

Each wizard step validates only what it asks for, using the same pieces:

```typescript
// 📁 frontend/src/components/experiments/new/formState.ts
export const FORM_STEPS = ['type', 'details', 'variants', 'estimate', 'review'] as const;
export type FormStep = (typeof FORM_STEPS)[number];

export function validateStep(step: FormStep, state: ExperimentFormState): string | null {
  switch (step) {
    case 'type':
      return null;
    case 'details':
      return checkName(state.name) ?? checkMetrics(state.metrics);
    case 'variants':
      return checkVariants(state.variants);
    case 'estimate':
      return plannedEffectProblem(state.plannedEffectPct);
    case 'review':
      return validateForm(state.name, state.variants, state.metrics);
  }
}

export function maxReachableStep(state: ExperimentFormState): number {
  const review = FORM_STEPS.indexOf('review');
  for (let i = 0; i < review; i += 1) {
    if (validateStep(FORM_STEPS[i], state) !== null) return i;
  }
  return review;
}
```

`as const` makes `FORM_STEPS` a tuple of literals, and `(typeof FORM_STEPS)[number]` derives
the union `'type' | 'details' | 'variants' | 'estimate' | 'review'` from it — one list, one
type, no drift. The `switch` has no `default`: under `strict`, adding a step to the tuple makes
the function fail to compile until the new case is handled. `maxReachableStep` is how the
wizard keeps the URL honest: Next is refused while the current step has a problem (shown in
`data-testid="step-error"`), and a hand-edited `?step=review` on an incomplete form is moved
back to the first step that still needs an answer. The Estimate step calls
`ExperimentsService.estimateSampleSize` for advice; the one thing it can save is the minimum
detectable effect, and `validateStep('estimate', ...)` fails only when that is typed but cannot be
saved (blank is fine).

The payload builder is a pure function of the state:

```typescript
// 📁 frontend/src/components/experiments/new/formState.ts
export function buildCreatePayload(state: ExperimentFormState): CreateExperimentRequest {
  const { name, key, description, hypothesis, type, rules, variants, metrics } = state;
  const plannedEffect = plannedEffectFraction(state.plannedEffectPct);
  const payload: CreateExperimentRequest = {
    name: name.trim(),
    key: (key || generateKey(name)).trim() || undefined,
    description: description.trim() || undefined,
    hypothesis: hypothesis.trim() || undefined,
    experiment_type: type,
    targeting_rules: rules.groups.length > 0 ? (rules as unknown as Record<string, unknown>) : null,
    variants: variants.map((v) => {
      const variant: CreateExperimentRequest['variants'][number] = {
        name: v.name.trim(),
        description: v.description.trim() || undefined,
        is_control: v.is_control,
        traffic_allocation: Number(v.traffic_allocation) || 0,
      };
      // Validation refuses a bad configuration before this is called; blank is none.
      const configuration = parseConfiguration(v.configuration_text);
      if (configuration.ok && configuration.value !== undefined) {
        variant.configuration = configuration.value;
      }
      return variant;
    }),
    metrics: metrics.map((m) => {
      const metric: CreateExperimentRequest['metrics'][number] = {
        name: m.name.trim(),
        event_name: m.event_name.trim(),
        metric_type: m.metric_type,
        is_primary: m.is_primary,
      };
      if (m.is_primary && plannedEffect !== undefined) metric.expected_effect = plannedEffect;
      // Sent only when true; without it the API stores false.
      if (m.lower_is_better === true) metric.lower_is_better = true;
      return metric;
    }),
    confidence_level: state.confidenceLevel,
    correction_method: state.correctionMethod,
  };
  if (state.bayesianEnabled) payload.bayesian_enabled = true;
  if (state.sequentialEnabled) payload.sequential_testing_enabled = true;
  return payload;
}
```

`plannedEffectFraction(state.plannedEffectPct)` turns what the user typed on the Estimate step
into a fraction, or `undefined`.
`const payload: CreateExperimentRequest = {...}` — annotating the payload makes the compiler
check it against the mirror of `ExperimentCreate`, so a missing required field is a build
error. `rules as unknown as Record<string, unknown>` is a double assertion: the rule builder's
typed shape is a `Record<string, unknown>` for the API's purposes, and TypeScript needs the
detour through `unknown` because the two types don't overlap structurally. The Review step
renders this same payload, so what the user checks is exactly what is sent.

Two things the payload builder does are worth noticing because they are about **omission**. A
variant's `configuration` is typed as JSON text (`configuration_text`), parsed by
`parseConfiguration`, and added to the request only when it is a non-blank JSON object within
16,384 UTF-8 bytes (the dashboard's own limit, counted by `utf8ByteLength` because `.length` counts
UTF-16 units: "é" would count as one byte instead of two); a blank box leaves the key out, and an
array, a string or `null` is refused on the client because the API refuses it too. And the planned
effect goes on the primary metric only, as `expected_effect`; with none, no metric carries the key
at all. A metric's `lower_is_better` (for a metric where less is the win, such as a load time) is
the same idea: sent only when true, because "without it the API stores false". The state also holds
the results settings the user chose on the same step: `confidenceLevel` and `correctionMethod` are
always sent, `bayesian_enabled` and `sequential_testing_enabled` only when switched on. The pattern
is the one this Hour started with: a client-side check in the API's own terms, and a payload that
says nothing the user was not asked.

The submit — one function for both views:

```tsx
// 📁 frontend/src/pages/experiments/new.tsx
  const submit = async (view: CreateView) => {
    if (submittingRef.current) return;
    setError(null);

    const problem = validateForm(state.name, state.variants, state.metrics);
    if (problem) {
      setError({ message: problem });
      if (configurationProblems(state.variants).some((p) => p !== null)) {
        setRevealProblems((n) => n + 1);
      }
      return;
    }

    // A blank condition ("+ Add Group" starts with one) would be refused by
    // the API; say so at the rules before sending anything.
    const targeting = checkTargeting(state.rules);
    if (targeting.length > 0) {
      setError({ message: TARGETING_INCOMPLETE, targeting });
      return;
    }

    submittingRef.current = true;
    setIsSubmitting(true);
    let createdId: string;
    let sentKey: string | undefined;
    try {
      // No login redirect on a 401: that would discard every answer. The
      // session copy says what to do instead, and the answers stay.
      const payload = buildCreatePayload(state);
      sentKey = payload.key;
      const created = await ExperimentsService.create(payload, { redirectOn401: false });
      createdId = created.id;
    } catch (err) {
      setError(describeCreateError(err, sentKey, view));
      submittingRef.current = false;
      setIsSubmitting(false);
      return;
    }

    // Created. Nothing below may report a failure to create, and nothing may
    // ask whether to leave: the answers are saved.
    guardArmedRef.current = false;
    ...
  };
```

Read it as a pipeline. Validate the form — and if a variant's configuration is the problem, bump
`revealProblems`, the counter `VariantsEditor` watches (Hour 6) to open that row and focus its
field, so the problem never sits inside a closed section. A counter rather than a boolean, because
the child must react to *every* refusal, including a second one in a row; `true` → `true` is no
change. Then validate the targeting rules (`checkTargeting` finds a
blank attribute, operator or value, or a group with no conditions, and the problems are shown
at the rule builder, not at the foot of the form). Guard against a double submit with a
**ref** as well as state: `submittingRef.current` changes synchronously, so a second click
before the re-render is still refused. Send with `{ redirectOn401: false }` (Hour 14), so an
expired session produces a message instead of a trip to `/login` that throws the answers away.
After success, disarm the leave guard (`useLeaveGuard` asks before a reload or a closed tab
discards unsaved answers) and navigate — falling back to a full page load if `router.push`
does not move (Hour 10).

The failure mapping, by status:

```typescript
// 📁 frontend/src/components/experiments/new/createErrors.ts
export function describeCreateError(err: unknown, sentKey: string | undefined, view: CreateView): CreateError {
  if (err instanceof ApiError) {
    if (err.status === 401) return { message: SESSION_EXPIRED_CREATE };
    if (err.status === 403) return { message: `${sentence(err.message)} ${ROLE_CANNOT_CREATE}` };
    if (err.status === 409) return { message: duplicateKeyMessage(sentKey, view), editDetails: true };
    if (err.status === 422) {
      const split = splitTargetingIssues(err.detail);
      if (split) return { message: TARGETING_REFUSED, ...split };
    }
  }
  if (err instanceof ApiError) return { message: err.message };
  return { message: CREATE_FAILED };
}
```

- **401**: "Your session has expired. Sign in again in another tab, then press Create
  Experiment again. Your answers are still here."
- **403**: the API's reason, then what the role means and who can help.
- **409**: the page's own sentence with the key it sent — "An experiment with the key
  “checkout_v2” already exists." — and `editDetails: true`, which renders an **Edit details**
  button that goes to the key field (on the Details step in the wizard).
- **422 naming `targeting_rules`**: each problem rewritten in the builder's numbering
  ("Group 1, Condition 2: unknown operator") and shown at the rules; any other item in the
  same 422 is kept and shown at the foot.
- **any other `ApiError`**: the message `apiFetch` built — for a server error, it carries the
  request ID; for a create that timed out, it says the request "may or may not have been
  carried out" and asks the user to reload and check (Hour 3).
- **an exception that is not an API answer**: `CREATE_FAILED`, “Something went wrong while
  creating the experiment. Check the experiments list before trying again.” The exception's own
  text is never shown, and the sentence says what the page genuinely does not know: whether the
  experiment was saved.

That refines the Creed's rule 5. "Show the server's message when they disagree" is still the
default, but where the **status alone** says what happened and what to do, the page says it
in its own words and offers the fix. It never reads the words of an error to decide what it
is: the wording can change, the status code is the contract.

#### The same rules in four newer forms

The dashboard has gained four forms since the create form was written, and each one is the rules
above applied to a new shape. Read them as worked examples.

**Edit variants and metrics, on a draft.** `ExperimentDesignForm` is the create form's reducer and
editors again (Hour 6), started from the stored experiment. What it adds is a rule about *not*
sending:

```tsx
// 📁 frontend/src/components/experiments/ExperimentDesignForm.tsx
  const [state, dispatch] = useReducer(experimentFormReducer, experiment, formStateFromExperiment);
  // What an unchanged form would send; a save equal to it sends nothing.
  const [seeded] = useState(() => JSON.stringify(buildDesignPayload(formStateFromExperiment(experiment))));
  ...
    const body = buildDesignPayload(state);
    if (JSON.stringify(body) === seeded) {
      onCancel();
      return;
    }
```

`seeded` is a snapshot taken once, by a lazy initialiser, and never updated — a value, not state,
so its setter is not even named. Saving a form nobody changed closes it without a request. Before
that, `checkDesign` runs guided setup's own checks and stricter ones of its own (at least two
variants, exactly one control, exactly one primary), and a refusal moves focus to the field it is
about. The save
itself is the one `PUT` of exactly `{variants, metrics}` from Hour 14.

**A flag's rollout schedule.** `RolloutScheduleManager` on the flag page creates and runs a
schedule (stages that raise the flag's percentage over time). Its validator is the clearest case
of a client mirror that is deliberately *stricter* than the server, with the reason written down:

```tsx
// 📁 frontend/src/components/feature-flags/RolloutScheduleManager.tsx
/**
 * The whole stage list, as it would be after a change, checked the way the
 * API checks a new schedule and more: every stage named, every target a whole
 * number from 0 to 100, and no target below the one before it, in
 * `stage_order`. The API checks the order only on create, and by list
 * position, so the form runs this before every create, add and update. A
 * target above `max` (the schedule's `max_percentage`) is refused too.
 */
export function checkStages(stages: Array<{ name: string; target: string }>, max: number): StageProblem[] {
...
/**
 * A `datetime-local` value as naive UTC, `YYYY-MM-DDTHH:MM:SS`, with no `Z` and
 * no offset. Stage dates are sent as naive UTC; the API takes a value with no
 * zone as UTC (`UTCDateTime`).
 */
export function localInputToNaiveUtc(value: string): string | null {
  const d = localInputToDate(value);
  return d ? d.toISOString().slice(0, 19) : null;
}
...
/**
 * The fixed sentence for a failed request, chosen by the answer's status only;
 * a time-out shows the dashboard's own time-out sentence (#1040).
 */
export function rolloutErrorCopy(err: unknown): string {
  if (!isApiError(err)) return ROLLOUT_ERROR_COPY.fallback;
  if (err.timedOut) return err.message;
  if (err.status === 0) return UNREACHABLE;
  return ROLLOUT_ERROR_COPY[err.status] ?? ROLLOUT_ERROR_COPY.fallback;
}
```

Three habits in three functions. `checkStages` validates the list *as it would be after the
change*, so editing stage 2 cannot quietly put it below stage 1. `localInputToNaiveUtc` is Hour 4's
time-zone trap from the other side: a `<input type="datetime-local">` holds a time with no zone, in
the browser's zone, and the function turns it into the zone-less UTC the API expects (another
helper, `readBack`, then shows the user both: "Saved as Mon, Nov 2, 2026, 9:00 AM PST (17:00
UTC)."). And `rolloutErrorCopy` is the create form's status mapping with Hour 3's ordering rule: a
time-out is checked before `status === 0`, because both are `0`. The update, like every edit form
here, sends only what changed (`stageChanges`: "The update body: exactly the fields that
changed.").

**Exclusion groups and the global holdout.** The two pages under `/experiments/` follow the same
pattern (fixed copy by status, only the changed fields sent), and one detail in
`frontend/src/utils/exclusionGroups.ts` is worth stealing. A group's share
of traffic is stored as a fraction (`0.5`) and edited as a whole percent (`50`), and
`groupChanges` compares "the share ... as the whole percent the form showed, so saving an
unchanged form never rounds a stored 0.333 to 0.33". Compare the value the user *saw*, not the
value you converted it into, or a save with no change becomes a change.

**Your API keys.** `/account/api-keys` lets any user make keys of their own, including one with the
`mcp:read` scope (for tools such as the Controlvar MCP server). Ticking that box changes which
expiry choices are offered — "No expiry" is not one for an `mcp:read` key — and the form keeps its
state consistent without an effect. The checkbox's own `onChange` clears a "No expiry" pick as it
ticks the box ("the default takes its place"), and the value the select shows is derived:

```typescript
// 📁 frontend/src/components/account/ownKeyExpiry.ts
/**
 * The choice in effect: what the user picked, if it is offered for this key,
 * otherwise the default. `null` means the user has not picked one.
 */
export function effectiveExpiry(picked: ExpiryChoice | null, mcpRead: boolean): ExpiryChoice {
  if (picked !== null && expiryOptions(mcpRead).some((option) => option.value === picked)) return picked;
  return defaultExpiry(mcpRead);
}
```

The component keeps `pickedExpiry` in state and computes
`const expiry = effectiveExpiry(pickedExpiry, mcpRead);` on every render — Hour 6's "derive, don't
duplicate". Compare the alternative: an effect that watched `mcpRead` and corrected the expiry
would run after the render that needed it, so for one painted frame the select would hold a choice
the key cannot have. A change made in the event handler, plus a value derived during render, has
no such frame. The scope's value, label and help text live in one
file, `frontend/src/components/admin/api-keys/mcpReadScope.ts`, which the admin dialog and this
page both import, so the two screens cannot describe the scope differently. One honest caveat: the
API-key components call `apiFetch` themselves with a path constant (`OWN_KEYS_API_PATH`) instead of
going through a service, which is the exception to Hour 14's rule rather than the rule.

> **Watch Out!** A `<form>` without `e.preventDefault()` in its submit handler reloads the
> page — and Next's dev server makes that look like "my state disappeared." The single-page
> view's handler is `e.preventDefault(); void submit('advanced');`. The wizard has no `<form>`
> at all: every button is `type="button"`, and Enter in a text field on the first three steps
> presses Next, so a key press can never submit half an experiment.

> **Did You Know?** `noValidate` on the single-page view's
> `<form data-testid="new-experiment-form" noValidate>` turns off the browser's own validation
> bubbles so the page's messages are the only ones. And Next's route announcer renders a
> `role="alert"` region, so a Playwright test that queries `getByRole('alert')` can find _two_
> alerts — which is why the form's error carries `data-testid="form-error"` and the login
> error `data-testid="login-error"`.

> **Did You Know?** `useForm` (Hour 8) is a generic hook for the simple case — validators per
> field, `touched`, a submit pipeline. No page uses it today: the login form is small enough
> for `useState`, and the experiment form needed a reducer. It stays available, with its own
> tests, for the next simple form.

### Try It Yourself

Open `/experiments/new` and walk the wizard. Watch the URL change at each step, press the
browser's Back button, then switch to the single-page form with the link at the top: your
answers are there. Now edit the URL to `?step=review` on a fresh tab and see the notice that
sends you back to the first step.

Then break the mirror on purpose: in a scratch copy, change `total !== 100` in `checkVariants`
to `total < 100`, submit variants at 60/60 from the single-page form, and watch the _server's_
message (containing `Traffic allocations must sum to 100% (currently 120%)`) appear in the
error box via `ApiError.message`. Revert, and run the form's tests:

```bash
cd frontend && npx jest src/tests/components/experiment-form-state.test.ts src/tests/pages/experiment-new.test.tsx --watchAll=false
```

### Common mistakes

- **Validating only on the client** (or only on the server). Both; the server wins.
- **Sending `""` for empty optional fields.** `|| undefined`.
- **Unannotated payloads.** `const payload: CreateExperimentRequest` catches drift.
- **A double-submit guard in state only** — a second click before the re-render gets through;
  use a ref as well.
- **Parsing an error's words** to decide what happened. Map by status.
- **A 401 redirect from a form with answers on it** — pass `redirectOn401: false` and say what
  to do.

### Key takeaways

- One `useReducer` over a pure `formState.ts`: state, reducer, validators, payload; two views
  (guided wizard, `?advanced` single page) share it, so answers carry over.
- `validateForm` = `checkName ?? checkVariants ?? checkMetrics`, mirroring the server;
  `validateStep` and `maxReachableStep` gate the wizard.
- `submit(view)`: validate, check targeting, ref-guarded send with `redirectOn401: false`,
  `describeCreateError` by status, navigate with a hard-navigation fallback.
- Permission first: `canCreateExperiment(user, effective)` shows a notice instead of a form the
  API would refuse, and neither while the answer is still coming.
- The newer forms repeat the rules: reuse the reducer (`ExperimentDesignForm`), send nothing for
  an unchanged form, validate the list as it will be (`checkStages`), compare what the user saw
  (`groupChanges`), derive rather than repair state (`effectiveExpiry`), and check `timedOut`
  before `status === 0`.

---

## Hour 16 — Lists, pagination, and lifecycle actions

### What & why

List pages combine three things: a **fetch driven by filters** (status, search), **offset
pagination** (`skip`/`limit`, `total` from the server), and **row actions** that change state
on the server and then in the UI. The dashboard's pattern is `useApi` with the filter in the
dependency list — change the filter, the hook refetches — and per-row actions that either
refetch or update the local copy after the API confirms. (`useApi` returns a `setData` for that
purpose, but at this release no page calls it: the flag list, below, keeps its own state, because it
needs to guard against overlapping requests and revert a single row.)

For a detail page's **lifecycle actions**, the page must stay honest during the request: the
clicked button shows its pending label, all actions are disabled, a destructive action asks
for confirmation first, and the response — the updated resource — replaces local state so the
next allowed actions come from the server's truth, not from what the client assumed.

### In the wild

The list fetch — one hook, the filter in the deps:

```tsx
// 📁 frontend/src/pages/experiments/index.tsx
  const [statusFilter, setStatusFilter] = useState<ExperimentStatus | 'all'>('all');
  ...
  const { data, loading: isLoading, error } = useApi<ExperimentListResponse>(
    () => ExperimentsService.list(statusFilter !== 'all' ? { status: statusFilter } : undefined),
    [statusFilter],
  );
```

Changing the `<select>` sets `statusFilter`; `useApi` sees a new dep and re-executes; the
mounted guard drops any stale response. `data` is `ExperimentListResponse | null` — the
page renders a skeleton until it isn't. The response carries `items`, `total`, `skip`,
`limit`, so a pager is `skip + limit < total` for "next" and `skip - limit` for "previous."

The detail page's action runner — pending state, confirmation, the server's response as the
new truth:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const runAction = async (action: LifecycleAction) => {
    if (!experiment) return;
    setConfirming(null);
    setActionError(null);
    setPendingAction(action);
    try {
      const updated = await ExperimentsService[action](experiment.id);
      onSaved(updated);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : `Failed to ${action} experiment`);
    } finally {
      setPendingAction(null);
    }
  };

  const onActionClick = (action: LifecycleAction) => {
    if (ACTION_META[action].confirm) {
      setActionError(null);
      setConfirming(action);
      return;
    }
    void runAction(action);
  };
```

`ExperimentsService[action](experiment.id)` — indexing the service by the action name is
type-safe because `LifecycleAction` is exactly the four method names. `onSaved(updated)`
puts the server's answer into state, so the buttons for the _next_ state come from
`ACTIONS_BY_STATUS[updated.status]` — the server said "active", so "Pause" and "Complete" appear.
`onSaved` is one line, `setExperiment((previous) => keepOwnerName(previous, updated))`, and it
exists because of a trap in "the response replaces local state": only `GET /experiments/{id}`
carries `owner_name`; the lifecycle routes and the saves answer without it. Setting state straight
from their answer would turn the owner's name on the page back into a short id after the first
click. `keepOwnerName` keeps the name from the earlier read when the owner is unchanged and the new
answer says nothing. When a response is a partial view of the resource, merge it; do not replace
with it. The confirm step is inline state
(`confirming`), not `window.confirm`, so it's testable and styleable:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
const ACTION_META: Record<
  LifecycleAction,
  { label: string; pending: string; className: string; confirm?: string }
> = {
  start: {
    label: 'Start',
    pending: 'Starting…',
    className: 'bg-green-600 text-white hover:bg-green-700',
  },
  pause: {
    label: 'Pause',
    pending: 'Pausing…',
    className: 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50',
  },
  complete: {
    label: 'Complete',
    pending: 'Completing…',
    className: 'bg-blue-600 text-white hover:bg-blue-700',
    confirm: 'Complete this experiment? Assignment stops and the status cannot be reverted.',
  },
  archive: {
    label: 'Archive',
    pending: 'Archiving…',
    className: 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50',
    confirm: 'Archive this experiment? It will be hidden from the default list.',
  },
};
```

#### Who may act: by permission, not by ownership

Which buttons a user sees depends on two things: the experiment's status
(`ACTIONS_BY_STATUS`) and what the user **may do**. Access to experiments, like access to feature
flags, is by permission, and a permission comes from one of three places. A superuser has all of
them. A built-in role has a fixed set: ADMIN and DEVELOPER may create, start, pause, complete and
archive any experiment; ANALYST and VIEWER may read all of them and change none, including ones
they created. And in the full profile a custom role or a direct grant can **add** a permission to
anyone — `experiment: update` for an ANALYST, say (only experiment and feature-flag create, read,
update, delete and list, and audit-log read, can be granted). The dashboard mirrors that rule in one
small module, so the list, the forms and the detail page cannot disagree:

```typescript
// 📁 frontend/src/utils/experimentPermissions.ts
const ROLES_THAT_CAN_CHANGE = ['ADMIN', 'DEVELOPER'] as const;

export function canChangeExperiment(user: PermissionUser, effective?: EffectivePermissions): boolean {
  // No session yet: keep the buttons rather than flickering them in; the
  // API still decides.
  if (!user) return true;
  if (user.is_superuser) return true;
  return (
    ROLES_THAT_CAN_CHANGE.includes(user.role as 'ADMIN' | 'DEVELOPER') ||
    holds(effective, 'experiment', 'update')
  );
}
```

The last line is the whole effective-permissions change on the client: the role list **or** the
user's own map, read by `holds` from `frontend/src/utils/effectivePermissions.ts`, whose header
states the formula every helper follows —
`allowed = is_superuser || ROLE_LIST.includes(role) || holds(effective, resource, action)` — and the
fail-closed reading of a missing map ("only the role lists apply, so the dashboard is never more
permissive than the API"). The role list is still pinned from the other side:
`backend/tests/unit/core/test_dashboard_experiment_roles.py` reads this TypeScript file as text
and holds `ROLES_THAT_CAN_CHANGE` equal to the roles `permissions.py` gives EXPERIMENT UPDATE (and
DELETE), so a role added on one side only fails a Python test instead of offering a button the API
refuses.

`canCreateExperiment` is its twin for creation and `canDeleteExperiment` for deleting a draft, each
asking about its own action, because a grant can give one without the other. The list page hides
"+ New Experiment" (`data-testid="new-experiment-btn"`) when creation is not allowed, and
`/experiments/new` shows a notice instead of the form (Hour 15). The detail page keeps the status's
actions but offers none, and explains why instead of leaving an unexplained gap:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const statusActions = ACTIONS_BY_STATUS[status] ?? [];
  const mayChange = canChangeExperiment(user, effective);
  // Not refused yet: the effective map may still add EXPERIMENT UPDATE (#891).
  // Until it answers the page shows neither the buttons nor the note.
  const mayChangePending = !mayChange && permissionsLoading;
  // Offer only what the API would accept: a user without EXPERIMENT UPDATE
  // (by role, custom role or direct grant) is refused every lifecycle change,
  // including on an experiment they own.
  const actions = mayChange ? statusActions : [];
  // A null owner (the creator's account was removed) is nobody, so it is
  // never you.
  const isOwner =
    user !== null && experiment.owner_id !== null && user.id === experiment.owner_id;
  const resultsAvailable = status !== 'draft';
  const showRoleNote = !mayChange && !mayChangePending && statusActions.length > 0;
```

`showRoleNote` renders `data-testid="experiment-role-note"`: "Starting, pausing, completing
and archiving an experiment needs permission to change experiments, which the ADMIN and
DEVELOPER roles have; you are ANALYST. You can read its results." `isOwner` only changes the
wording ("You own this experiment, but ..."). Three principles are at work. **The UI is never more
permissive than the API**: a button that always answers 403 is a bug, even though the API is what
enforces. **A hidden control needs a reason on screen**, or the user thinks the page is broken. And
**a refusal is shown only once it is certain**: `mayChangePending` keeps both the buttons and the
note off the screen while the map that might admit the user is on its way (Hour 9), so an ANALYST
with a grant never reads "you may not" a moment before the buttons appear. The note's words follow
the same rule as the code: they name the permission ("permission to change experiments") and
mention the roles as the usual holders of it, because a role is no longer the only way to get one.

Not every control can be granted. The global holdout and archiving an exclusion group check the
ADMIN role itself on the server (`_require_admin`), so their helper takes no map at all:

```typescript
// 📁 frontend/src/utils/experimentPermissions.ts
const ROLES_THAT_ADMINISTER_ELIGIBILITY = ['ADMIN'] as const;

export function canAdministerEligibility(
  user: { role?: string | null; is_superuser?: boolean } | null | undefined,
): boolean {
  if (!user) return false;
  if (user.is_superuser) return true;
  return ROLES_THAT_ADMINISTER_ELIGIBILITY.includes(user.role as 'ADMIN');
}
```

Read its first line against `canChangeExperiment`'s. For a lifecycle button, no session yet means
`true` ("keep the buttons rather than flickering them in; the API still decides"), and inside the
dashboard that branch is hardly reached, since `RequireAuth` renders a protected page only once the
user is known. For an administrative control the default is `false`: "these controls appear only
once the user is known to be an ADMIN or a superuser". Which way a helper answers when it does not
know yet is a decision, and the file writes it down beside each one.

A "copy" affordance with a transient state — `copied` flips on for 1.5 seconds, and the
clipboard being unavailable is swallowed rather than surfaced:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const copyKey = async () => {
    if (!experiment?.key) return;
    try {
      await navigator.clipboard?.writeText(experiment.key);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };
```

The detail page loads its single resource with its own `load` callback rather than `useApi`,
because it needs to replace the resource after every action. The **flag list** goes further,
and it is the best example in the dashboard of keeping a list honest while requests are in
flight. It does not use `useApi` either; it hand-rolls the load so it can guard against
overlapping requests, and it toggles optimistically:

```tsx
// 📁 frontend/src/pages/feature-flags/index.tsx
  /**
   * Sequence number of the most recent list request. Switching filters quickly
   * leaves the earlier request in flight; without this guard a slow "On"
   * response can land after a fast "Off" one and paint the wrong rows (which a
   * subsequent toggle would then act on).
   */
  const requestSeq = useRef(0);
  ...
  /**
   * Optimistic on/off: flip locally, call enable/disable, and on failure revert
   * only this row — a whole-list snapshot would also undo any other toggle that
   * succeeded while this request was in flight.
   */
  const handleToggle = async (flag: FeatureFlag) => {
    const next = !isFlagOn(flag);
    const previousStatus = flag.status;
    const previousIsActive = flag.is_active;
    ...
    setFlags((current) =>
      current.map((f) =>
        f.id === flag.id ? { ...f, status: next ? 'active' : 'inactive', is_active: next } : f,
      ),
    );
    try {
      const res = await FeatureFlagsService.setEnabled(flag.id, next);
      ...
    } catch (err) {
      setFlags((current) =>
        current.map((f) =>
          f.id === flag.id ? { ...f, status: previousStatus, is_active: previousIsActive } : f,
        ),
      );
      ...
    }
  };
```

Two ideas to take from it. The **sequence number** is the list's version of Hour 7's `cancelled`
flag: every request takes a number, and only the latest may write state. And the revert restores
**one row** (`previousStatus`, `previousIsActive`) through the functional `setFlags((current) => ...)`
form, never a snapshot of the whole list, because a snapshot would also undo a different toggle that
succeeded while this one was in flight. Toggle errors are keyed by flag id (`toggleErrors`) for the
same reason: two can fail at once, and both messages matter.

The page test that pins the lifecycle — a routed mock whose state transitions, then
assertions on which buttons appear:

```tsx
// 📁 frontend/src/tests/pages/experiment-detail.test.tsx
function install(current: Experiment) {
  let state = current;
  const transition = (status: Experiment['status']) => () => {
    state = { ...state, status };
    return state;
  };
  mockedApiFetch.mockImplementation(
    routedApi([
      { path: '/api/v1/experiments/exp-1', handler: () => state },
      { method: 'POST', path: '/api/v1/experiments/exp-1/start', handler: transition('active') },
      { method: 'POST', path: '/api/v1/experiments/exp-1/pause', handler: transition('paused') },
      { method: 'POST', path: '/api/v1/experiments/exp-1/complete', handler: transition('completed') },
      { method: 'POST', path: '/api/v1/experiments/exp-1/archive', handler: transition('archived') },
    ]) as unknown as typeof apiFetch,
  );
}
```

> **Watch Out!** After a lifecycle action, don't set the status locally
> (`setExperiment({ ...experiment, status: 'active' })`) — use the server's response. The
> backend may refuse (`400: Experiment must have at least one metric defined`), and the
> only way to show the right buttons and the right error is to render what it returned.

> **Did You Know?** The list endpoint's `limit` is capped at 500 server-side (`Query(100,
> ge=1, le=500)`), and the dashboard's default page size follows the server's default of
> 100. A pager that asks for more gets a 422 whose message `apiFetch` will show — one more
> reason the client mirrors the server's bounds.

### Try It Yourself

Add a "previous / next" pager to the experiments list using `data.skip`, `data.limit` and
`data.total`, holding `skip` in state and including it in `useApi`'s deps. Then write the
test: mock `apiFetch` with `routedApi`, assert the `skip` query parameter on the second
request. Finally, click "Complete" on an active experiment in the running dashboard and
watch the buttons change to "Archive" only after the response arrives.

### Common mistakes

- **Filters outside the hook's deps** — the list doesn't refetch.
- **Local status updates instead of the server's response.**
- **`window.confirm`** — untestable, unstyled; use inline state.
- **Refetching the whole list for one row change** when updating that one row in state will do.
- **Gating on ownership** (`isOwner`) instead of permission, or **hiding a control without
  saying why**.
- **Reading only `user.role`** where the API also admits a custom role or a direct grant —
  pass the effective map, and keep a pending state while it loads.

### Key takeaways

- Lists: `useApi(fetcher, [filters])`; the response's `skip`/`limit`/`total` drive paging.
- Actions: pending label + disabled buttons + inline confirm + the server's response as new
  state + `finally` to clear.
- Index services by action name when the action union equals the method names.
- Offer actions by permission (`canChangeExperiment(user, effective)`, `canCreateExperiment`,
  `canDeleteExperiment`): the role list or the effective map, mirroring the API; show neither
  control nor note while the map is pending; explain a hidden control (`experiment-role-note`).
- A control the API keeps for the ADMIN role itself (`canAdministerEligibility`) takes no map and
  answers `false` until the user is known.
- Tests pin transitions with a stateful routed mock.

---

## Hour 17 — WebSockets in React: `useExperimentStream`

### What & why

The results page shows live numbers without polling: a WebSocket to
`/api/v1/ws/experiments/{id}/results` delivers a snapshot on connect and every 30 seconds,
and accepts `{"action": "refresh"}` and `{"action": "ping"}`. Wrapping that in a hook means
handling the things a raw socket doesn't: **authentication** without headers, **reconnect**
with a cap, **terminal close codes** that must _not_ reconnect (a bad token would loop
forever), **cleanup** on unmount or id change, and **parsing** snake_case JSON into typed
state.

Two design decisions to know:

- **The token rides as a subprotocol**, not a query parameter. Browsers can't set headers on
  the WebSocket handshake, but they can offer `Sec-WebSocket-Protocol` entries; the hook
  offers `['experimently.bearer', token]` and the backend echoes the first. (That string is the
  product's former name, kept on purpose: it is a wire contract between a deployed dashboard
  and a deployed API, and renaming it would break a dashboard and an API of different
  versions talking to each other.) URLs end up in access logs; subprotocols don't.
- **Refs for the socket and counters, state for what renders.** `wsRef`, `reconnectCount`,
  `reconnectTimerRef`, `intentionalDisconnect` are `useRef`s — mutable, no re-render;
  `snapshot`, `status`, `error` are state.

### In the wild

The URL and the auth protocols:

```typescript
// 📁 frontend/src/hooks/useExperimentStream.ts
/**
 * Build the streaming URL. The origin comes from `NEXT_PUBLIC_WS_URL`,
 * `NEXT_PUBLIC_API_URL` or, for same-origin deployments, `window.location`.
 * The token is never put in the URL (URLs end up in access logs); see
 * `buildStreamProtocols`.
 */
export function buildStreamUrl(experimentId: string): string {
  return `${wsBase()}/api/v1/ws/experiments/${encodeURIComponent(experimentId)}/results`;
}

/**
 * Browsers cannot set headers on a WebSocket handshake, but they can offer
 * subprotocols. The token rides as `Sec-WebSocket-Protocol: experimently.bearer, <token>`
 * and the backend echoes `experimently.bearer` on accept. Returns `undefined`
 * when there is no token so the socket is opened without a protocol list.
 */
export function buildStreamProtocols(): string[] | undefined {
  const token = getToken();
  return token ? [WS_AUTH_SUBPROTOCOL, token] : undefined;
}
```

The connect logic — every socket event mapped to state, and the close handler that decides
between "give up," "retry," and "the user asked for this":

```typescript
// 📁 frontend/src/hooks/useExperimentStream.ts
  const connect = useCallback(() => {
    if (!experimentId) return;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    const url = buildStreamUrl(experimentId);
    setStatus('connecting');
    setError(null);
    intentionalDisconnect.current = false;

    try {
      const protocols = buildStreamProtocols();
      const ws = protocols ? new WebSocket(url, protocols) : new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectCount.current = 0;
        setStatus('connected');
        setError(null);
      };

      ws.onmessage = (event: MessageEvent) => {
        try {
          const raw = JSON.parse(event.data as string) as Record<string, unknown>;
          const parsed = parseSnapshot(raw);
          setSnapshot(parsed);
        } catch (parseError) {
          console.warn('[useExperimentStream] Failed to parse message:', parseError);
        }
      };

      ws.onerror = () => {
        setStatus('error');
        setError('WebSocket connection error');
      };

      ws.onclose = (event?: CloseEvent) => {
        wsRef.current = null;

        if (intentionalDisconnect.current) {
          setStatus('disconnected');
          return;
        }

        // The backend accepts and then closes with 4401 when the token is
        // missing, expired or invalid. Reconnecting with the same token can
        // only loop, so stop and surface it.
        const code = event?.code;
        if (code !== undefined && TERMINAL_CLOSE_CODES.has(code)) {
          setStatus('unauthorized');
          setError(
            code === WS_CLOSE_UNAUTHORIZED
              ? 'Not authorized to stream results; sign in again.'
              : `Connection refused by the server (code ${code})`,
          );
          return;
        }

        // Attempt reconnect
        if (reconnectCount.current < maxReconnectAttempts) {
          reconnectCount.current += 1;
          setStatus('connecting');
          reconnectTimerRef.current = setTimeout(() => {
            connect();
          }, reconnectDelayMs);
        } else {
          setStatus('disconnected');
          setError(`Lost connection after ${maxReconnectAttempts} reconnect attempts`);
        }
      };
    } catch (err) {
      setStatus('error');
      setError(String(err));
    }
  }, [experimentId, reconnectDelayMs, maxReconnectAttempts]);
```

Read `onclose` as a decision tree: intentional → stop quietly; terminal code (`4401`) →
stop loudly; otherwise → retry up to the cap with a delay. `connect` is `useCallback`'d on
its inputs so the reconnect timer can call it and the effect can depend on it.

Disconnect, and the actions the results panel calls:

```typescript
// 📁 frontend/src/hooks/useExperimentStream.ts
  const disconnect = useCallback(() => {
    intentionalDisconnect.current = true;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setStatus('disconnected');
  }, []);

  const refresh = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'refresh' }));
    }
  }, []);
```

The effect that ties connect/disconnect to the id and to unmount:

```typescript
// 📁 frontend/src/hooks/useExperimentStream.ts
  useEffect(() => {
    if (!experimentId || autoConnect === false) return;
    connect();
    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [experimentId]);
```

The dependency list is `[experimentId]` alone, on purpose, with the lint rule switched off for that
line: `connect` and `disconnect` are re-created whenever the reconnect settings change, and an
effect that listed them would tear the socket down and reopen it for a change that is not a new
experiment. Change the experiment id → cleanup closes the old socket (marked intentional, so no
reconnect) → the effect opens a new one. Unmount → closed. And the defensive parser from
Hour 1 turns each message into typed state.

The backend's side of the contract, for reference — the same close code and subprotocol:

```python
# 📁 backend/app/api/v1/endpoints/websocket_results.py
WS_CLOSE_UNAUTHORIZED = 4401
WS_AUTH_SUBPROTOCOL = "experimently.bearer"
```

> **Watch Out!** A reconnect loop on an auth failure is the classic WebSocket bug: the
> server closes, the client reconnects with the same bad token, the server closes again,
> five times a second, forever. The `TERMINAL_CLOSE_CODES` check is the fix — and it's why
> the backend _accepts then closes with a code_ rather than refusing the handshake, which
> browsers report as an opaque error the client couldn't distinguish from a network blip.

> **Did You Know?** `WebSocket` isn't available in Jest's jsdom by default, so the hook's
> tests install a fake `global.WebSocket` class that records `send` calls and lets the test
> fire `onmessage`/`onclose` with chosen codes — the same technique as mocking `fetch`.

### Try It Yourself

In the running dashboard, open a results page's Live tab, then in another tab log out; watch
the panel report "Not authorized to stream results; sign in again." rather than
reconnecting. Then stop the API container and watch the reconnect count climb to five. Read
the hook's test file (`src/tests/hooks/useExperimentStream.test.ts`) to see both cases
scripted.

### Common mistakes

- **Token in the URL** — logged everywhere; use the subprotocol.
- **Reconnecting on every close** — auth failures loop; check terminal codes.
- **Socket in state instead of a ref** — every message re-creates handlers.
- **No cleanup on id change** — two sockets, interleaved snapshots.

### Key takeaways

- Refs for the socket, counters and the intentional flag; state for snapshot/status/error.
- `onclose` decides: intentional → quiet stop; `4401`/terminal → `unauthorized`; else retry
  with a cap and a delay.
- Token via `Sec-WebSocket-Protocol: experimently.bearer, <token>`; URL from `wsBase()`.
- Parse every message defensively; cleanup closes on id change and unmount.

---

## Part IV Workshop

**Q1. What are the three layers of the data path and the one job of each?**
Page (state, when to fetch, what to render), service (which route/method/body/response type),
`apiFetch` (base URL, token, JSON, errors, 401).

**Q2. Why does `featureFlags.ts` have `isFlagOn()` and `flagRules()`?**
The backend returns two shapes for a flag (the service dict with `status`/`rules`, and the
list schema with `status`/`is_active`/`targeting_rules`); the helpers give pages one answer
for "is it on" and "what are the rules" instead of spreading the inconsistency.

**Q3. `validateForm` says allocations must add up to 100 and so does the server. Why both?**
The client's check is instant feedback; the server's is authoritative and covers every
client. The words differ slightly ("Variant allocations must add up to 100%" vs "Traffic
allocations must sum to 100%"). When the server refuses something the client allowed, its
message reaches the user through `ApiError.message` — unless the status alone says what to do
(Q7).

**Q4. After `POST /experiments/{id}/start` succeeds, what should the page set as state and
why?**
The experiment object the server returned — its `status` drives `ACTIONS_BY_STATUS`, and
only the server knows whether the transition was allowed.

**Q5. Why is the WebSocket token sent as a subprotocol and not `?token=`?**
Browsers can't set handshake headers, and query strings end up in access logs; the
subprotocol carries the token without either problem, and the backend echoes
`experimently.bearer` to accept.

**Q6. The socket closes with code 4401. What does the hook do, and why not reconnect?**
Sets `status: 'unauthorized'` with a message and stops. The token is bad; reconnecting with
it would loop.

**Q7. A create answers 409. What does the page show, and why not the API's text?**
"An experiment with the key “…” already exists." with the key the page sent, plus an **Edit
details** button that goes to the key field. `describeCreateError` maps by status alone; a
409 on create means the key is taken whatever its sentence says, so the page states it in its
own words and offers the fix. It never parses an error's words to decide what it is.

**Q8. Why does the new-experiment page render only a loading marker until `router.isReady`?**
On a static export the first render has an empty `query`, so `'advanced' in router.query` would
be false and a `?step=` would be lost; waiting for the router keeps the view the URL asked for.

**Q9. `updateDetails` and `updateDesign` send the same `PUT` to the same URL. Why two methods?**
Because the route replaces every variant and metric when a body carries them. Each method's body
type can say only what its caller means: `ExperimentDetailsUpdate` has no room for variants, and
`updateDesign` takes exactly `{variants, metrics}`, both complete. A details edit cannot replace
the variants by accident; it would not compile.

**Q10. An exclusion group stores a share of `0.333`. The user opens the edit form, which shows
33, changes only the name and saves. Is `traffic_allocation` sent?**
No. `groupChanges` compares the share as the whole percent the form showed (33 = 33), so only the
name is sent and the stored 0.333 is not rounded to 0.33.

**Quiz.** In `ResultsService.getResults`, why `params?.breakdown || undefined` rather than
`??`?
→ To drop an empty string as well as `undefined`; `''` would otherwise be sent as
`breakdown=` and rejected.

---

## Exercises

**E4.1 — A service.** Write `SafetyService` with `getSettings`, `updateSettings`,
`getFlagConfig(flagId)`, `updateFlagConfig(flagId, body)`, `check(flagId)`, `rollback(flagId,
percentage, reason)` against `/api/v1/safety/*`, typed with mirror interfaces.

**E4.2 — A form.** Sketch the flag-create page's `validateForm(key, name, rollout)`
mirroring `FeatureFlagBase`'s rules (key pattern `^[a-z0-9][a-z0-9_-]*$`, rollout 0–100), and
the payload construction with `|| undefined` for optional strings.

**E4.3 — A pager.** Write the state, `useApi` call and two handlers for offset pagination on
the flags list, disabling "next" when `skip + limit >= total`.

**E4.4 — A confirmed toggle.** Using `useApi`'s `setData`, write `toggleFlag(flag, next)` that
updates the row after `FeatureFlagsService.setEnabled` resolves, and on failure changes nothing
but shows a toast.

**E4.5 — Close codes.** The stream hook already treats `4401`, `4403` and `1008` as terminal,
with a special message only for `4401`. Give `4403` ("forbidden for this experiment") its own
message, and explain why `4403` and `1008` (policy violation) are terminal while `1006`
(abnormal closure) must _not_ be.

---

## Solutions

**S4.1**

```typescript
export interface SafetySettings { enable_automatic_rollbacks: boolean; default_metrics?: Record<string, MetricThreshold> | null }
export interface MetricThreshold { warning_threshold?: number | null; critical_threshold?: number | null; comparison_type: 'greater_than' | 'less_than' | 'equal_to' }
export interface FlagSafetyConfig { id: string; feature_flag_id: string; enabled: boolean; metrics: Record<string, MetricThreshold>; rollback_percentage: number }
export interface SafetyCheckResponse { feature_flag_id: string; is_healthy: boolean; metrics: Array<{ name: string; current_value: number; threshold: number; is_healthy: boolean }>; last_checked: string }

const BASE = '/api/v1/safety';
export const SafetyService = {
  getSettings: () => apiFetch<SafetySettings>(`${BASE}/settings`),
  updateSettings: (body: Partial<SafetySettings>) => apiFetch<SafetySettings>(`${BASE}/settings`, { method: 'POST', json: body }),
  getFlagConfig: (flagId: string) => apiFetch<FlagSafetyConfig>(`${BASE}/feature-flags/${flagId}/config`),
  updateFlagConfig: (flagId: string, body: Partial<FlagSafetyConfig>) => apiFetch<FlagSafetyConfig>(`${BASE}/feature-flags/${flagId}/config`, { method: 'POST', json: body }),
  check: (flagId: string) => apiFetch<SafetyCheckResponse>(`${BASE}/feature-flags/${flagId}/check`),
  rollback: (flagId: string, percentage: number, reason: string) =>
    apiFetch<{ success: boolean; message: string }>(`${BASE}/feature-flags/${flagId}/rollback`, { method: 'POST', json: { percentage, reason } }),
};
```

**S4.2**

```typescript
export function validateFlagForm(key: string, name: string, rollout: number): string | null {
  if (!name.trim()) return 'Name is required.';
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(key)) return 'Key must be lowercase alphanumeric characters, hyphens, or underscores only.';
  if (!Number.isInteger(rollout) || rollout < 0 || rollout > 100) return 'Rollout percentage must be a whole number from 0 to 100.';
  return null;
}
const payload: CreateFeatureFlagRequest = {
  key: key.trim(), name: name.trim(),
  description: description.trim() || undefined,
  rollout_percentage: Number(rollout) || 0,
  targeting_rules: rules.groups.length > 0 ? rules : null,
};
```

**S4.3**

```tsx
const [skip, setSkip] = useState(0);
const limit = 50;
const { data, loading } = useApi(() => FeatureFlagsService.list({ skip, limit }), [skip]);
const next = () => { if (data && skip + limit < data.total) setSkip(skip + limit); };
const prev = () => setSkip(Math.max(0, skip - limit));
<button onClick={prev} disabled={skip === 0 || loading}>Previous</button>
<button onClick={next} disabled={!data || skip + limit >= data.total || loading}>Next</button>
```

**S4.4**

```tsx
const { addToast } = useNotifications();
const toggleFlag = async (flag: FeatureFlag, next: boolean) => {
  try {
    const res = await FeatureFlagsService.setEnabled(flag.id, next);
    if (data) {
      setData({
        ...data,
        items: data.items.map((f) =>
          f.id === flag.id ? { ...f, status: res.status, is_active: res.status === 'active' } : f,
        ),
      });
    }
  } catch (err) {
    addToast(err instanceof Error ? err.message : 'Could not update the flag', 'error');
  }
};
```

(`setData` takes a value, not an updater function, so the new list is built from the current
`data`. This is not optimistic in the strict sense — the row changes only after the server
confirms — so there is nothing to revert. The real flag list is optimistic: it sets first and
reverts that one row in `catch`, as in Hour 16.)

**S4.5**

```typescript
// the set is unchanged: new Set([WS_CLOSE_UNAUTHORIZED, 4403, 1008])
        if (code !== undefined && TERMINAL_CLOSE_CODES.has(code)) {
          setStatus('unauthorized');
          setError(
            code === WS_CLOSE_UNAUTHORIZED ? 'Not authorized to stream results; sign in again.'
            : code === 4403 ? 'You do not have permission to view this experiment.'
            : `Connection refused by the server (code ${code})`,
          );
          return;
        }
```

`4403` and `1008` are the server's decision about *this* request — the user may not see the
experiment, or the handshake broke a rule — and reconnecting with the same token sends the same
request and gets the same answer, in a loop. `1006` means the connection dropped without a
close frame — a network blip, a proxy timeout, a restarted API. That's exactly the case
reconnecting is _for_; treating it as terminal would make every deploy permanently disconnect
every open dashboard.

---

**Next:** [Part V — UI →](./part-5-ui.md)

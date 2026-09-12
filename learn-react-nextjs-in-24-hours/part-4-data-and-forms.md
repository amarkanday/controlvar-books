# Part IV — Data & Forms

### Hours 14–17

[← Part III](./part-3-nextjs.md) · [Contents](./README.md) · [Part V →](./part-5-ui.md)

Data in, data out. This Part is the services layer — one typed object per API resource,
mirroring the backend's routes (Hour 14); forms that hold local state, validate the way the
server does, and show the server's own words when the two disagree (Hour 15); lists with
filters, offset pagination and lifecycle actions that keep the UI honest while a request is
in flight (Hour 16); and a WebSocket hook with reconnect, terminal close codes and a
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

  async create(data: CreateExperimentRequest): Promise<Experiment> {
    return apiFetch<Experiment>(BASE, { method: 'POST', json: data });
  },

  async update(id: string, data: Partial<Experiment>): Promise<Experiment> {
    return apiFetch<Experiment>(`${BASE}/${id}`, { method: 'PUT', json: data });
  },

  async delete(id: string): Promise<void> {
    await apiFetch<void>(`${BASE}/${id}`, { method: 'DELETE' });
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
all.

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
  static async getResults(experimentId: string, params?: { metric_id?: string; breakdown?: string }): Promise<ExperimentResultsResponse> {
    return apiFetch<ExperimentResultsResponse>(`/api/v1/results/${experimentId}`, {
      query: {
        metric_id: params?.metric_id || undefined,
        breakdown: params?.breakdown || undefined,
      },
    });
  }

  static async getSampleSize(experimentId: string, params?: { mde?: number; power?: number }): Promise<SampleSizeResult> {
    return apiFetch<SampleSizeResult>(`/api/v1/results/${experimentId}/sample-size`, {
      query: { mde: params?.mde, power: params?.power },
    });
  }
  ...
}
```

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

> **Watch Out!** `params?.metric_id || undefined` in `ResultsService` uses `||` on purpose:
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

## Hour 15 — Forms: local state, client validation, server messages

### What & why

A form in this dashboard is **controlled inputs bound to local state**, a **client-side
validator that mirrors the server's rules** for instant feedback, and a **submit handler**
that builds the request payload, calls the service, and shows the server's message when the
server disagrees. Nothing is saved until submit; the server is the authority; the client's
validator exists so the user doesn't wait for a round trip to learn that allocations must
sum to 100.

Three rules keep forms honest:

1. **Mirror, don't reinvent.** `validateForm` implements the same rules as
   `ExperimentCreate`'s validators — one control, allocations sum to 100, one primary metric
   — and its messages are close to the server's. When they diverge, the server wins and its
   message (`ApiError.message`, the Pydantic sentence) is shown.
2. **Build the payload from state, trimming and normalising.** `name.trim()`, `description
   || undefined` (so an empty textarea isn't sent as `""`), `Number(x) || 0`, `targeting_rules:
   null` when there are no groups.
3. **Disable while submitting; never double-submit.** `isSubmitting` gates the button and
   shows a pending label; `finally` clears it.

### In the wild

The client-side mirror of the server's validators — exported so it's unit-tested by itself:

```typescript
// 📁 frontend/src/pages/experiments/new.tsx
/** Client-side mirror of the `ExperimentCreate` validators; returns the first problem. */
export function validateForm(
  name: string,
  variants: VariantFormData[],
  metrics: MetricFormData[],
): string | null {
  if (!name.trim()) return 'Name is required.';
  if (variants.length === 0) return 'Add at least one variant.';
  if (variants.some((v) => !v.name.trim())) return 'Every variant needs a name.';
  if (!variants.some((v) => v.is_control)) return 'One variant must be marked as control.';
  const total = variants.reduce((sum, v) => sum + (Number(v.traffic_allocation) || 0), 0);
  if (total !== 100) return `Variant allocations must add up to 100% (currently ${total}%).`;
  if (metrics.length === 0) return 'Add at least one metric.';
  if (metrics.some((m) => !m.name.trim() || !m.event_name.trim())) {
    return 'Every metric needs a name and an event name.';
  }
  if (!metrics.some((m) => m.is_primary)) return 'Choose a primary metric.';
  return null;
}
```

Compare with the server:

```python
# 📁 backend/app/schemas/experiment.py
        if not any(variant.is_control for variant in self.variants):
            raise ValueError("At least one variant must be marked as control")
        ...
        if total_allocation != 100:
            raise ValueError(f"Traffic allocations must sum to 100% (currently {total_allocation}%)")
```

Same rules, similar words, two places — deliberately. The client's are instant; the
server's are law.

Form state as separate `useState`s for scalars and arrays of records:

```tsx
// 📁 frontend/src/pages/experiments/new.tsx
  const [name, setName] = useState('');
  const [key, setKey] = useState('');
  const [keyEdited, setKeyEdited] = useState(false);
  const [description, setDescription] = useState('');
  const [hypothesis, setHypothesis] = useState('');
  const [type, setType] = useState<ExperimentType>('a_b');
  const [rules, setRules] = useState<TargetingRules>(createEmptyRules());
  const [variants, setVariants] = useState<VariantFormData[]>(DEFAULT_VARIANTS);
  const [metrics, setMetrics] = useState<MetricFormData[]>(DEFAULT_METRICS);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
```

Record handlers that keep invariants — removing a metric re-elects a primary; setting a
control clears the others:

```tsx
// 📁 frontend/src/pages/experiments/new.tsx
  const handleRemoveMetric = (index: number) => {
    const next = metrics.filter((_, i) => i !== index);
    if (next.length > 0 && !next.some((m) => m.is_primary)) next[0] = { ...next[0], is_primary: true };
    setMetrics(next);
  };

  const handleSetPrimary = (index: number) => {
    setMetrics(metrics.map((m, i) => ({ ...m, is_primary: i === index })));
  };
```

The submit pipeline — prevent default, validate, build the payload, call, navigate, show the
server's message on failure:

```tsx
// 📁 frontend/src/pages/experiments/new.tsx
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    const problem = validateForm(name, variants, metrics);
    if (problem) {
      setError(problem);
      return;
    }

    setIsSubmitting(true);
    try {
      const payload: CreateExperimentRequest = {
        name: name.trim(),
        key: (key || generateKey(name)).trim() || undefined,
        description: description.trim() || undefined,
        hypothesis: hypothesis.trim() || undefined,
        experiment_type: type,
        targeting_rules: rules.groups.length > 0 ? (rules as unknown as Record<string, unknown>) : null,
        variants: variants.map((v) => ({
          name: v.name.trim(),
          description: v.description.trim() || undefined,
          is_control: v.is_control,
          traffic_allocation: Number(v.traffic_allocation) || 0,
        })),
        metrics: metrics.map((m) => ({
          name: m.name.trim(),
          event_name: m.event_name.trim(),
          metric_type: m.metric_type,
          is_primary: m.is_primary,
        })),
      };

      const created = await ExperimentsService.create(payload);
      await router.push(`/experiments/${created.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create experiment');
    } finally {
      setIsSubmitting(false);
    }
  };
```

`const payload: CreateExperimentRequest = {...}` — annotating the payload makes the compiler
check it against the mirror of `ExperimentCreate`, so a missing required field is a build
error. `rules as unknown as Record<string, unknown>` is a double assertion: the rule builder's
typed shape is a `Record<string, unknown>` for the API's purposes, and TypeScript needs the
detour through `unknown` because the two types don't overlap structurally. (`noValidate` on
the `<form>` disables the browser's own validation bubbles so the page's messages are the
only ones.)

The generic hook alternative, for simpler forms — validators per field, `touched`, a
submit pipeline that captures the server's message:

```tsx
// 📁 frontend/src/hooks/useForm.ts
export type Validator<T> = (value: unknown, allValues: T) => string | null;

export interface UseFormOptions<T extends Record<string, unknown>> {
  initialValues: T;
  validators?: Partial<Record<keyof T, Validator<T>>>;
  onSubmit: (values: T) => Promise<void> | void;
}
```

```tsx
// (usage shape — login and the flag form)
const form = useForm({
  initialValues: { email: '', password: '' },
  validators: { email: (v) => (typeof v === 'string' && v.includes('@') ? null : 'Enter a valid email') },
  onSubmit: async (values) => { await login(values.email, values.password); },
});
<input value={form.values.email} onChange={(e) => form.setValue('email', e.target.value)} onBlur={() => form.setTouched('email')} />
{form.touched.email && form.errors.email && <p role="alert">{form.errors.email}</p>}
<button disabled={form.isSubmitting}>Sign in</button>
{form.submitError && <p role="alert" data-testid="login-error">{form.submitError}</p>}
```

> **Watch Out!** A `<form>` without `e.preventDefault()` in its submit handler reloads the
> page — and Next's dev server makes that look like "my state disappeared." The first line of
> every submit handler is `e.preventDefault()`.

> **Did You Know?** Next's route announcer renders a `role="alert"` region, so a Playwright
> test that queries `getByRole('alert')` on the login page can find _two_ alerts. The
> login error carries `data-testid="login-error"` for exactly that reason — a real lesson
> from the E2E suite.

### Try It Yourself

Break the mirror on purpose: in a scratch copy of `validateForm`, change `total !== 100` to
`total < 100`, submit variants at 60/60, and watch the _server's_ message
(`variants: Traffic allocations must sum to 100% (currently 120%)`) appear in the error box
via `ApiError.message`. Then run the validator's tests:

```bash
cd frontend && npx jest src/tests/pages/experiment-new.test.tsx -t "validateForm" --watchAll=false
```

### Common mistakes

- **Validating only on the client** (or only on the server). Both; the server wins.
- **Sending `""` for empty optional fields.** `|| undefined`.
- **Unannotated payloads.** `const payload: CreateExperimentRequest` catches drift.
- **No `isSubmitting` gate** — double submits create duplicates.

### Key takeaways

- Controlled inputs → local state; `validateForm` mirrors server rules and is exported for
  tests; server messages are shown verbatim on disagreement.
- Payloads are annotated with the request mirror type and built with `trim()`, `||
  undefined`, `Number(x) || 0`.
- `e.preventDefault()`, `isSubmitting` in `finally`, navigate on success.
- `useForm<T>` for simpler forms: validators, `touched`, `submitError`.

---

## Hour 16 — Lists, pagination, and lifecycle actions

### What & why

List pages combine three things: a **fetch driven by filters** (status, search), **offset
pagination** (`skip`/`limit`, `total` from the server), and **row actions** that change state
on the server and then in the UI. The dashboard's pattern is `useApi` with the filter in the
dependency list — change the filter, the hook refetches — and per-row actions that either
refetch or update the local copy (`setData`) after the API confirms.

For a detail page's **lifecycle actions**, the page must stay honest during the request: the
clicked button shows its pending label, all actions are disabled, a destructive action asks
for confirmation first, and the response — the updated resource — replaces local state so the
next allowed actions come from the server's truth, not from what the client assumed.

### In the wild

The list fetch — one hook, the filter in the deps:

```tsx
// 📁 frontend/src/pages/experiments/index.tsx
  const [statusFilter, setStatusFilter] = useState<ExperimentStatus | 'all'>('all');

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
      setExperiment(updated);
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
type-safe because `LifecycleAction` is exactly the four method names. `setExperiment(updated)`
means the buttons for the _next_ state come from `ACTIONS_BY_STATUS[updated.status]` — the
server said "active", so "Pause" and "Complete" appear. The confirm step is inline state
(`confirming`), not `window.confirm`, so it's testable and styleable:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
const ACTION_META: Record<LifecycleAction, { label: string; pending: string; className: string; confirm?: string }> = {
  start: { label: 'Start', pending: 'Starting…', className: 'bg-green-600 text-white hover:bg-green-700' },
  pause: { label: 'Pause', pending: 'Pausing…', className: 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50' },
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

A derived flag that gates a UI affordance from the auth context, and a "copy" affordance
with a transient state:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const isOwner = user !== null && user.id === experiment.owner_id;
  const resultsAvailable = status !== 'draft';
  const primaryMetric = experiment.metrics.find((m) => m.is_primary) ?? experiment.metrics[0];
  ...
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

Optimistic-style local update after an API confirmation — the flag list toggles a row
without refetching the whole page, using `useApi`'s `setData`:

```tsx
// (shape used by the flags list)
const { data, setData } = useApi(() => FeatureFlagsService.list(), []);
const toggle = async (flag: FeatureFlag, next: boolean) => {
  const updated = next ? await FeatureFlagsService.activate(flag.id) : await FeatureFlagsService.deactivate(flag.id);
  setData((prev) => prev && { ...prev, items: prev.items.map((f) => (f.id === flag.id ? { ...f, ...updated } : f)) });
};
```

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
      ...
    ]),
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
- **Refetching the whole list for one row change** when `setData` will do.

### Key takeaways

- Lists: `useApi(fetcher, [filters])`; the response's `skip`/`limit`/`total` drive paging.
- Actions: pending label + disabled buttons + inline confirm + the server's response as new
  state + `finally` to clear.
- Index services by action name when the action union equals the method names.
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
  offers `['experimently.bearer', token]` and the backend echoes the first. URLs end up in
  access logs; subprotocols don't.
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
// 📁 frontend/src/hooks/useExperimentStream.ts (shape)
  useEffect(() => {
    if (autoConnect && experimentId) connect();
    return () => {
      disconnect();
    };
  }, [experimentId, autoConnect, connect, disconnect]);
```

Change the experiment id → cleanup closes the old socket (marked intentional, so no
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

**Q3. `validateForm` says allocations must sum to 100 and so does the server. Why both?**
The client's check is instant feedback; the server's is authoritative and covers every
client. When they disagree the server's message is shown verbatim via `ApiError.message`.

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

**E4.4 — Optimistic toggle.** Using `useApi`'s `setData`, write `toggleFlag(flag, next)` that
updates the row after `activate`/`deactivate` resolves, and reverts nothing on failure but
shows a toast.

**E4.5 — Close codes.** Add a second terminal code `4403` ("forbidden for this experiment")
to the stream hook's handling with its own message, and explain why `1006` (abnormal
closure) must _not_ be terminal.

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
const { addToast } = useNotification();
const toggleFlag = async (flag: FeatureFlag, next: boolean) => {
  try {
    const updated = next ? await FeatureFlagsService.activate(flag.id) : await FeatureFlagsService.deactivate(flag.id);
    setData((prev) => prev && { ...prev, items: prev.items.map((f) => (f.id === flag.id ? { ...f, ...updated } : f)) });
  } catch (err) {
    addToast(err instanceof Error ? err.message : 'Could not update the flag', 'error');
  }
};
```

(Not optimistic in the strict sense — the row changes only after the server confirms — so
there's nothing to revert; a truly optimistic version would set first and revert in
`catch`.)

**S4.5**

```typescript
const TERMINAL_CLOSE_CODES = new Set([4401, 4403]);
...
        if (code !== undefined && TERMINAL_CLOSE_CODES.has(code)) {
          setStatus('unauthorized');
          setError(
            code === 4401 ? 'Not authorized to stream results; sign in again.'
            : code === 4403 ? 'You do not have permission to view this experiment.'
            : `Connection refused by the server (code ${code})`,
          );
          return;
        }
```

`1006` means the connection dropped without a close frame — a network blip, a proxy timeout,
a restarted API. That's exactly the case reconnecting is _for_; treating it as terminal would
make every deploy permanently disconnect every open dashboard.

---

**Next:** [Part V — UI →](./part-5-ui.md)

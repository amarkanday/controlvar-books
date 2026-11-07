# Part I — TypeScript Essentials

### Hours 1–4

[← Back to Contents](./README.md)

Four hours of TypeScript — exactly the subset the dashboard uses, taught through the code that
uses it. Why a JavaScript codebase volunteers for a compiler (Hour 1); the type vocabulary
that describes API data — interfaces, literal unions, `Record` (Hour 2); generics and
`unknown`, which are how one `apiFetch` serves every endpoint safely (Hour 3); and narrowing,
optional chaining and the discipline of mirroring the backend's schemas field for field
(Hour 4). Open a [Playground](https://www.typescriptlang.org/play) tab and type the examples —
TypeScript is learned through red squiggles.

---

## Hour 1 — Why TypeScript? The field that wasn't there

### What & why

TypeScript is JavaScript with a **static type system**: annotations the compiler checks
_before_ the code runs, then erases. The bundle that ships is plain JavaScript with zero type
checks in it. The value is one sentence: **catch a category of bugs at your desk instead of in
a user's browser.**

Two load-bearing words. _Static_: types are checked by reading the code, not by running it.
_Erased_: an annotation is a compile-time claim, not a runtime guard — `status: ExperimentStatus`
doesn't stop a malformed API response from delivering `"ACTIVE"` where `"active"` was
expected. So the discipline is twofold: precise types for data you _control_, and validation
(or defensive parsing) at the edges for data you _don't_.

#### The field that wasn't there

The dashboard's results hook parses WebSocket snapshots from the server. The server sends
snake_case JSON; the hook converts it to camelCase. Before the fields were typed and parsed
explicitly, a renamed server field (`participant_count` → `participants`, in a refactor) would
have produced `NaN` participant counts on the live panel — silently, because JavaScript's
`Number(undefined)` is `NaN` and `NaN` renders as "NaN". The fix is the shape you'll see in
Hour 17:

```typescript
// 📁 frontend/src/hooks/useExperimentStream.ts
/**
 * Convert snake_case server fields to camelCase VariantResult.
 * The server sends snake_case JSON; we normalise here so consumers see
 * camelCase consistently.
 */
function parseVariant(raw: Record<string, unknown>): VariantResult {
  return {
    key: String(raw.key ?? ''),
    name: String(raw.name ?? ''),
    participantCount: Number(raw.participant_count ?? 0),
    conversionCount: Number(raw.conversion_count ?? 0),
    conversionRate: Number(raw.conversion_rate ?? 0),
    relativeLift: Number(raw.relative_lift ?? 0),
    pValue: raw.p_value != null ? Number(raw.p_value) : null,
    isControl: Boolean(raw.is_control),
  };
}
```

The input is `Record<string, unknown>` — "an object with string keys and values we know
nothing about" — and every field is explicitly coerced with a default. The output is a
`VariantResult` the rest of the app can trust. That one function is the thesis: **types are
precise inside, defensive at the edge.**

The complementary discipline is at the top of the types file:

```typescript
// 📁 frontend/src/types/experiments.ts
/**
 * Experiment types. Field names mirror the backend response/request schemas in
 * `backend/app/schemas/experiment.py` (`ExperimentResponse`, `ExperimentCreate`,
 * `VariantResponse`, `MetricResponse`) — do not rename them client-side.
 */
```

The dashboard's types are hand-written mirrors of the Pydantic schemas, snake_case included.
There's no generated client; the mirror is kept honest by tests that hit the real shapes and
by a URL guard (Hour 23). When the backend adds a field, someone adds it here.

### In the wild

`tsconfig.json` turns on the setting that makes everything else matter:

```json
// 📁 frontend/tsconfig.json
{
  "compilerOptions": {
    "target": "es5",
    "strict": true,
    "noEmit": true,
    "moduleResolution": "bundler",
    "isolatedModules": true,
    "jsx": "react-jsx",
    "baseUrl": ".",
    "paths": {
      "@/*": [
        "./src/*"
      ],
      "@modules/*": [
        "../modules/frontend/src/*",
        "./src/modules-stub/*"
      ],
      ...
    },
    ...
  },
  "include": [
    "next-env.d.ts",
    "**/*.ts",
    "**/*.tsx",
    "../modules/frontend/src/**/*.ts",
    "../modules/frontend/src/**/*.tsx"
  ]
}
```

`"strict": true` — no implicit `any`, `null` and `undefined` are distinct types, and the
checks that make types trustworthy. `"noEmit": true` — TypeScript only _checks_; Next.js's
compiler (SWC, driven by Turbopack) does the emitting. `"paths"` gives the `@/` alias you'll
see in every import (`import { apiFetch } from '@/services/api'`). And `"target": "es5"` is
why `ApiError` calls `Object.setPrototypeOf(this, ApiError.prototype)` — subclassing `Error`
needs that fix-up when compiled to ES5.

Three settings are there because of the stack, not taste:

- **`"jsx": "react-jsx"` and `"moduleResolution": "bundler"`** are what Next 16 requires. If
  either is missing, `next build` rewrites the file on every build ("The following mandatory
  changes were made to your tsconfig.json"), so they are committed as Next wants them.
  `react-jsx` is the automatic JSX runtime: a component file no longer needs
  `import React` just to use JSX.
- **`"@modules/*"`** is the seam between the core dashboard and the optional modules
  (workspaces, warehouse, SSO, custom roles). It lists two targets: the real module code under
  `modules/frontend/src/` first, a stub tree under `frontend/src/modules-stub/` second. Hour 10
  shows the pages that use it.
- The real `paths` block also routes `react`, `react/*`, `next`, `next/*` and
  `@testing-library/*` to the dashboard's own `node_modules`, because code in the modules tree sits
  outside the package and would otherwise find no `node_modules` above it.

A second file, `frontend/tsconfig.core.json`, extends this one and points `@modules/*` at the
stub tree **alone** (and excludes `../modules`). `npx tsc --noEmit -p tsconfig.core.json` is
how CI proves the core dashboard type-checks with the modules deleted.

### A second way to feel the gap

```typescript
function conversionRate(v: { conversions: number; sample_size: number }) {
  return v.conversions / v.sample_size;
}
conversionRate({ conversions: 52, sample_size: 1000 });      // ✅
conversionRate({ conversions: 52 });                          // ❌ compile error: sample_size missing
conversionRate(JSON.parse('{"conversions": 52}'));            // ✅ compiles — JSON.parse returns any
```

The third line is the edge: `JSON.parse` returns `any`, which satisfies every type. The
compiler can't protect you from data you didn't validate — which is why `apiFetch<T>`'s
return type is a _promise_ you make to the compiler about the API, backed by tests against
the real API.

### Try It Yourself

In the Playground with `strict` on:

```typescript
type Status = 'draft' | 'active' | 'paused';
const s: Status = 'ACTIVE';                       // ❌ — the casing bug, caught

function parse(raw: Record<string, unknown>) {
  return { n: Number(raw.participant_count ?? 0) };
}
parse({ participants: 12 }).n;                    // 0, not NaN — defensive default
```

Then click the ".JS" tab: every annotation is gone. Now run the real check in the repo:
`cd frontend && npx tsc --noEmit` — it's what `npm run build` runs first.

### Common mistakes

- **Believing an annotation guards runtime data.** It's erased; parse at the edge.
- **`any` to silence an error.** `unknown` + narrowing (Hour 3).
- **Renaming a server field client-side** "for style." The types mirror the schema; keep
  snake_case.

### Key takeaways

- TypeScript checks before runtime and erases; zero runtime cost, zero runtime guarantee.
- Precise types inside; defensive parsing (`Record<string, unknown>` + coercion) at the
  edge.
- `strict: true`, `noEmit`, the `@/` alias; the types mirror `backend/app/schemas`.

---

## Hour 2 — Types, interfaces, and literal unions (`ExperimentStatus`)

### What & why

Three ways to name a shape:

- **`type Alias = ...`** — a name for any type, including unions: `type ExperimentStatus =
  'draft' | 'active' | ...`.
- **`interface Name { ... }`** — an object shape, extendable. The dashboard uses interfaces
  for API records and props.
- **Literal unions** — `'a_b' | 'mv' | 'split_url' | 'bandit'` — a type with exactly these
  values. They're TypeScript's enums-without-runtime: the compiler rejects `'ab'`, editors
  autocomplete, and `Record<ExperimentStatus, string>` forces a label for every status.

Optional fields (`description?: string`) mean "may be absent"; `| null` means "may be present
and null" — the API sends both kinds, and the mirror types say which. `readonly` isn't used
much here; immutability comes from the React idiom of never mutating state (Hour 6).

#### Why `Record<Status, string>` for labels

A lookup object typed `Record<ExperimentStatus, string>` is a compile-time completeness
check: add a status to the union and every label table without it fails to compile. That's
how five status colours, labels and lifecycle-action tables stay in sync with the backend's
enum.

### In the wild

The mirror types — unions for enums, interfaces for records, optionality matching the schema:

```typescript
// 📁 frontend/src/types/experiments.ts
export type ExperimentStatus = 'draft' | 'active' | 'paused' | 'completed' | 'archived';
export type ExperimentType = 'a_b' | 'mv' | 'split_url' | 'bandit';
export type MetricType = 'conversion' | 'revenue' | 'count' | 'duration' | 'custom';
export type OptimizationType = 'fixed' | 'thompson_sampling' | 'ucb1' | 'epsilon_greedy';

/** `VariantResponse` */
export interface ExperimentVariant {
  id: string;
  name: string;
  description?: string | null;
  is_control: boolean;
  /** Percentage of traffic (0–100). */
  traffic_allocation: number;
  configuration?: Record<string, unknown> | null;
  experiment_id?: string;
  created_at?: string;
  updated_at?: string;
}

/** `ExperimentResponse` */
export interface Experiment {
  id: string;
  name: string;
  key: string | null;
  description?: string | null;
  hypothesis?: string | null;
  experiment_type: ExperimentType | string;
  status: ExperimentStatus;
  targeting_rules: Record<string, unknown> | null;
  tags?: string[] | null;
  /** Null once the creator's account has been removed. */
  owner_id: string | null;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
  updated_at: string;
  variants: ExperimentVariant[];
  metrics: ExperimentMetric[];
  ...
}
```

Read the nuances: `key: string | null` (present, may be null — the column is nullable);
`description?: string | null` (may be absent or null); `experiment_type: ExperimentType |
string` (the union plus `string` — deliberately loose, because the backend may add a type
before the dashboard does, and rendering an unknown type as text beats a crash);
`configuration?: Record<string, unknown> | null` (a JSON object of unknown shape); and
`owner_id: string | null` (the doc comment says why: it is null once the creator's account has been
removed, so any code that compares it to the signed-in user is comparing against a value that can be
absent).

Completeness-checked lookup tables:

```typescript
// 📁 frontend/src/types/experiments.ts
export const EXPERIMENT_STATUS_LABELS: Record<ExperimentStatus, string> = {
  draft: 'Draft',
  active: 'Active',
  paused: 'Paused',
  completed: 'Completed',
  archived: 'Archived',
};
```

```typescript
// 📁 frontend/src/pages/experiments/[id].tsx
/** Lifecycle transitions exposed per status (mirrors the backend guards). */
export type LifecycleAction = 'start' | 'pause' | 'complete' | 'archive';

export const ACTIONS_BY_STATUS: Record<ExperimentStatus, LifecycleAction[]> = {
  draft: ['start'],
  active: ['pause', 'complete'],
  paused: ['start', 'complete'],
  completed: ['archive'],
  archived: [],
};
```

That second table is the backend's state machine (Build book, usage guide) as a type-checked
constant — and it's exported so the page test can assert against it.

Request types separate from response types, with the create payload's optionality matching
`ExperimentCreate`:

```typescript
// 📁 frontend/src/types/experiments.ts
/** `ExperimentCreate` */
export interface CreateExperimentRequest {
  name: string;
  key?: string;
  description?: string;
  hypothesis?: string;
  experiment_type: ExperimentType;
  targeting_rules?: Record<string, unknown> | null;
  tags?: string[];
  status?: ExperimentStatus;
  variants: VariantInput[];
  metrics: MetricInput[];
  optimization_type?: OptimizationType;
}
```

A union of literal strings for a result-engine enum, and a tuple type for a confidence
interval:

```typescript
// 📁 frontend/src/types/results.ts
export type RecommendationAction =
  | 'SHIP_VARIANT'
  | 'KEEP_CONTROL'
  | 'CONTINUE_TESTING'
  | 'INCONCLUSIVE';

export type EffectSizeLabel = 'negligible' | 'small' | 'medium' | 'large';

export interface SegmentVariantResult {
  variant_id: string;
  variant_name: string;
  is_control: boolean;
  sample_size: number;
  conversions: number | null;
  mean: number;
  confidence_interval: [number, number] | null;
  p_value: number | null;
  is_significant: boolean;
}
```

`[number, number]` is a **tuple**: exactly two numbers, lower and upper. An array type
`number[]` would allow zero or twenty.

> **Watch Out!** `interface` vs `type`: an interface can be extended and merged; a type alias
> can express unions and tuples. The dashboard's rule of thumb — `interface` for object
> shapes (records, props), `type` for unions, tuples and aliases — matches what each can do.
> Don't convert one to the other in review without a reason.

> **Did You Know?** The backend stores `FeatureFlagStatus` upper-case (`"ACTIVE"`, the
> database enum) but the API's JSON lower-cases it: the flag list schema declares
> `status: Literal["active", "inactive", "archived"]`. So the dashboard's union in
> `frontend/src/services/featureFlags.ts` is `'active' | 'inactive' | 'archived'`, the same
> casing as `ExperimentStatus`. Encode what the API *sends*, not what the database stores — and
> because one older response shape still reaches the dashboard, `isFlagOn()` lower-cases
> before comparing rather than trusting the casing.

### Try It Yourself

```typescript
type ExperimentStatus = 'draft' | 'active' | 'paused' | 'completed' | 'archived';
const labels: Record<ExperimentStatus, string> = { draft: 'Draft', active: 'Active', paused: 'Paused', completed: 'Completed' };
// ❌ Property 'archived' is missing — the completeness check

interface Variant { id: string; name: string; description?: string | null; traffic_allocation: number }
const v: Variant = { id: '1', name: 'Control', traffic_allocation: 50 };      // ✅ description absent
const w: Variant = { id: '2', name: 'B', description: null, traffic_allocation: 50 };   // ✅ null allowed
const ci: [number, number] = [0.04, 0.06];
const bad: [number, number] = [0.04];                                          // ❌
```

Then open `frontend/src/types/results.ts` and count the literal unions; for each, find the
backend enum or `Literal` it mirrors.

### Common mistakes

- **`string` where a literal union exists.** `status: string` accepts `"ACTIVE"`; the union
  doesn't.
- **`?` vs `| null` confusion.** Match the schema: nullable columns are `| null`; fields the
  API may omit are `?`.
- **A plain object for a label table.** `Record<Union, T>` gets you the completeness check.

### Key takeaways

- `type` for unions/tuples/aliases, `interface` for object shapes.
- Literal unions mirror backend enums exactly (casing included); `Record<Union, T>` enforces
  completeness.
- `?` = may be absent, `| null` = may be null; request and response types are separate
  mirrors.
- `[number, number]` is a tuple, not an array.

---

## Hour 3 — Generics and `unknown`: `apiFetch<T>` and `ApiError.detail`

### What & why

A **generic** is a type parameter: `function apiFetch<T>(path: string): Promise<T>` says
"this returns whatever you tell me it returns." The caller supplies `T` —
`apiFetch<Experiment>('/api/v1/experiments/1')` — and gets a typed promise without the
function knowing anything about experiments. That's how one fetch wrapper serves fifty
endpoints with full typing at each call site. Generic _classes_ and _hooks_ work the same
way: `useApi<T>(fetcher: () => Promise<T>)` returns `{ data: T | null, ... }`.

**`unknown`** is the type for "a value we haven't looked at yet." Unlike `any`, you can't
_do_ anything with an `unknown` until you narrow it (`typeof`, `instanceof`, `in`, a type
guard). It's the honest type for a parsed JSON body, an error's `detail`, or a caught
exception — and the dashboard uses it at every edge.

#### Why `apiFetch` is generic but `ApiError.detail` is `unknown`

The success body's shape is known to the caller (they know which endpoint they called), so a
generic lets them state it. The error body's shape is _not_ known — FastAPI returns a string
`detail`, an object `detail` (`{"code": ...}`), or Pydantic's array of `{loc, msg, type}` —
so `detail` is `unknown` and one function, `messageForDetail`, narrows it into a sentence.

### In the wild

The generic wrapper — `T` defaults to `unknown` so an untyped call is still safe:

```typescript
// 📁 frontend/src/services/api.ts
export async function apiFetch<T = unknown>(
  path: string,
  options: ApiFetchOptions = {},
): Promise<T> {
  const {
    query,
    json,
    auth = true,
    redirectOn401 = true,
    headers: extraHeaders,
    body: rawBody,
    timeoutMs: rawTimeoutMs = DEFAULT_REQUEST_TIMEOUT_MS,
    signal: callerSignal,
    ...rest
  } = options;
  const timeoutMs = checkTimeout(rawTimeoutMs);

  const url = apiUrl(path, query);
  const headers: Record<string, string> = { Accept: 'application/json', ...(extraHeaders ?? {}) };

  let body: BodyInit | null | undefined = rawBody;
  if (json !== undefined) {
    body = JSON.stringify(json);
    if (!hasHeader(headers, 'content-type')) headers['Content-Type'] = 'application/json';
  } else if (typeof rawBody === 'string' && !hasHeader(headers, 'content-type')) {
    headers['Content-Type'] = 'application/json';
  }

  const token = auth ? getToken() : null;
  if (token && !hasHeader(headers, 'authorization')) {
    headers.Authorization = `Bearer ${token}`;
  }

  const init: RequestInit = { ...rest, headers };
  if (body !== undefined) init.body = body;

  const deadline = startDeadline(timeoutMs, callerSignal);
  init.signal = deadline.signal;
  try {
    let response: LooseResponse;
    try {
      response = (await fetch(url, init)) as LooseResponse;
    } catch (err) {
      if (deadline.expired) throw timeoutError(timeoutMs, init.method ?? 'GET', err);
      throw new ApiError({ status: 0, detail: unreachableMessage(), cause: err });
    } finally {
      deadline.stop();
    }

    if (response.ok) {
      return await readSuccessBody<T>(response, path);
    }

    throw await failureFrom(response, auth, redirectOn401);
  } finally {
    // After the body is read, so a caller's abort still cancels the read.
    deadline.release();
  }
}
```

The wrapper reads top to bottom as the life of one request: build the URL, build the headers (JSON
body gets its `Content-Type`, the bearer token is attached unless the caller passed an
`Authorization` header of its own), start a deadline, call `fetch`, and branch. A thrown `fetch`
becomes an `ApiError` with `status: 0`, and there are now two reasons it can throw. Either the browser
could not reach the API at all (the `unreachableMessage()` case), or the **deadline** fired first.
A 2xx goes to `readSuccessBody<T>`. Anything else is handed to `failureFrom`, which builds the error.

#### The deadline: why a request now gives up

Before #1592 `apiFetch` had no deadline of its own: a request to an API that accepted the
connection and then said nothing waited for as long as the browser let it. Now every
`apiFetch` waits at most `DEFAULT_REQUEST_TIMEOUT_MS` (30,000 ms) **for the response headers**; the
few routes that are slow by nature (results, the audit-log export, the warehouse checks) wait
`LONG_REQUEST_TIMEOUT_MS` (120,000 ms) instead. The body is not timed: a response that
started and then stalls is waited for, which is what the `stop()` in the inner `finally` means.

Three details are worth reading closely, because each is a decision written as code:

- `checkTimeout(rawTimeoutMs)` throws a `TypeError` for anything that is not a number of
  milliseconds above 0 and at most `MAX_REQUEST_TIMEOUT_MS` (2,147,483,647, the longest delay
  `setTimeout` honours; a longer one fires at once). There is deliberately no "no deadline" value.
- `startDeadline` makes one `AbortController` per request and aborts it from a timer **or** from the
  caller's own `signal`, forwarded by a listener. Its `expired` flag is set only by the timer, so a
  caller that cancels its own request never sees "timed out".
- `timeoutError` decides what a time-out *means*. A GET, HEAD or OPTIONS changed nothing, so the
  message says "Try again". Any other method may still have been carried out by the API after the
  dashboard stopped waiting, so the `ApiError` is `outcomeUnknown` and its message says so: "the
  request may or may not have been carried out. Reload the page and check before you try again."

```typescript
// 📁 frontend/src/services/api.ts
function timeoutError(timeoutMs: number, method: string, cause: unknown): ApiError {
  const outcomeUnknown = !SAFE_METHODS.has(method.toUpperCase());
  const message = timeoutMessage(timeoutMs, outcomeUnknown);
  return new ApiError({ status: 0, detail: message, message, cause, timedOut: true, outcomeUnknown });
}
```

A time-out is still `status: 0`, so code that turns an error into words checks `timedOut` **before**
it checks for an unreachable API. The login page's `loginErrorMessage` does exactly that
(`if (err.timedOut) return signInTimeoutMessage();` comes before `if (err.status === 0) ...`), and so
do the newer components that map a failure to their own sentences (Hour 15). Here is
`failureFrom`:

```typescript
// 📁 frontend/src/services/api.ts
/** The `ApiError` for a non-2xx response; a 401 also clears the token. */
async function failureFrom(
  response: LooseResponse,
  auth: boolean,
  redirectOn401: boolean,
): Promise<ApiError> {
  const status = response.status ?? 0;
  const { detail, json: bodyIsJson } = await readErrorDetail(response);
  const requestId = readRequestId(response);

  if (status === 401 && auth) {
    clearToken();
    if (redirectOn401 && !isOnLoginPage()) {
      redirectToLogin();
    }
  }

  let message: string;
  if (status >= 500 && !bodyIsJson) {
    message = serverErrorMessage(status, requestId);
  } else {
    message =
      detail !== undefined
        ? messageForDetail(status, detail)
        : response.statusText || messageForDetail(status, undefined);
    ...
  }

  return new ApiError({ status, detail, message, requestId, retryAfter: readRetryAfter(response) });
}
```

Two details in the error half are about the people reading the message, not the compiler.
`readErrorDetail` returns `{ detail, json }`, so `failureFrom` knows whether the body was JSON at
all; a 5xx whose body is not JSON (a proxy's HTML page) gets `serverErrorMessage(status,
requestId)` instead of a garbled `statusText`. And `readRequestId` reads the `X-Request-ID`
response header: a 5xx message carries `Request ID: ...`, which an administrator searches the
API log for. `ApiError` keeps it as `requestId`, keeps a `Retry-After` header as `retryAfter`
(the API's rate limiter sends one with a 429), and its `isServerError` getter is `status >= 500`.
The error is built in its own function because a second caller needs it: `apiDownload`, which
fetches a file (the audit-log export) with the same token and the same error handling, waits the
long deadline for the headers, and does not parse the body as JSON.

Typed call sites — the service supplies `T`:

```typescript
// 📁 frontend/src/services/experiments.ts
  async get(id: string): Promise<Experiment> {
    return apiFetch<Experiment>(`${BASE}/${id}`);
  },

  async create(data: CreateExperimentRequest, options: CreateOptions = {}): Promise<Experiment> {
    return apiFetch<Experiment>(BASE, { method: 'POST', json: data, ...options });
  },
```

(`options` is `CreateOptions`, `{ redirectOn401?: boolean }`: `/experiments/new` passes
`{ redirectOn401: false }` so an expired session does not throw away a half-filled form —
Hour 15.)

`unknown` at the error edge, narrowed step by step:

```typescript
// 📁 frontend/src/services/api.ts
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly code?: string;
  readonly requestId?: string;
  readonly retryAfter?: number;
  readonly timedOut: boolean;
  readonly outcomeUnknown: boolean;
  ...
}

function detailCode(detail: unknown): string | undefined {
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const code = (detail as Record<string, unknown>).code;
    if (typeof code === 'string') return code;
  }
  return undefined;
}

/** Human-readable message for an error body. */
export function messageForDetail(status: number, detail: unknown): string {
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    // FastAPI/pydantic validation errors: [{loc, msg, type}, …]
    const parts = detail
      .map((item) => {
        if (item && typeof item === 'object') {
          const rec = item as Record<string, unknown>;
          const loc = Array.isArray(rec.loc)
            ? rec.loc.filter((p) => p !== 'body').join('.')
            : '';
          const msg = typeof rec.msg === 'string' ? rec.msg : '';
          return loc && msg ? `${loc}: ${msg}` : msg || loc;
        }
        return typeof item === 'string' ? item : '';
      })
      .filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  if (detail && typeof detail === 'object') {
    const rec = detail as Record<string, unknown>;
    if (typeof rec.message === 'string' && rec.message) return rec.message;
    if (typeof rec.msg === 'string' && rec.msg) return rec.msg;
    if (typeof rec.code === 'string' && rec.code) return rec.code;
  }
  return status ? `Request failed with status ${status}` : 'Request failed';
}
```

Every branch narrows before it uses: `typeof detail === 'string'`, `Array.isArray(detail)`,
`typeof item === 'object'`, then `as Record<string, unknown>` (a narrowing assertion _after_
the check, not instead of it), then `typeof rec.msg === 'string'`. That's the discipline:
`as` is acceptable when a check just proved it; `as` to silence the compiler is not.

A **type guard** — a function whose return type `err is ApiError` teaches the compiler:

```typescript
// 📁 frontend/src/services/api.ts
export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}
```

```typescript
// 📁 frontend/src/pages/experiments/[id].tsx
    } catch (err) {
      const status = isApiError(err) ? err.status : 0;
```

`catch (err)` gives `unknown` under `strict`; `isApiError(err)` narrows it so `err.status` is
legal in the true branch.

A generic hook, with the type flowing from the fetcher to the result:

```typescript
// 📁 frontend/src/hooks/useApi.ts
export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: DependencyList = [],
  options?: UseApiOptions,
): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null);
  ...
}
```

`useApi(() => ExperimentsService.get(id), [id])` infers `T = Experiment` from the fetcher;
`data` is `Experiment | null`. And a generic with a constraint — `useForm<T extends
Record<string, unknown>>` — so `keyof T` is usable for field names:

```typescript
// 📁 frontend/src/hooks/useForm.ts
export function useForm<T extends Record<string, unknown>>(
  options: UseFormOptions<T>,
): UseFormResult<T> {
```

> **Watch Out!** `as` is not a conversion; it's you telling the compiler to believe you.
> `(parsed as { detail: unknown }).detail` in `readErrorDetail` is guarded by
> `'detail' in (parsed as object)` — the check makes the assertion honest. An `as` with no
> check nearby is a review comment.

> **Did You Know?** `Promise<T>` and `Promise<void>` are how async functions are typed;
> `apiFetch<void>` on a DELETE returns `undefined` for a 204, and `readSuccessBody` handles
> that explicitly (`if (status === 204 || status === 205) return undefined as T;`). The one
> `as T` there is the "I know this is `undefined` and the caller asked for `void`" case.

### Try It Yourself

```typescript
async function apiFetch<T = unknown>(path: string): Promise<T> {
  const r = await fetch(path);
  return (await r.json()) as T;              // the one honest `as`: the caller's promise about the API
}
interface Experiment { id: string; status: 'draft' | 'active' }
const e = await apiFetch<Experiment>('/api/v1/experiments/1');   // e: Experiment
const u = await apiFetch('/anything');                              // u: unknown
u.id;                                                               // ❌ Object is of type 'unknown'
if (typeof u === 'object' && u && 'id' in u) console.log((u as { id: unknown }).id);   // ✅ narrowed first

function message(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(String).join('; ');
  return 'Request failed';
}
```

Then call `messageForDetail(422, [{ loc: ['body', 'variants'], msg: 'Traffic allocations must sum to 100% (currently 90%)', type: 'value_error' }])`
in a Jest REPL (or a scratch test) and read the string it builds.

### Common mistakes

- **`any` anywhere.** `unknown` + narrowing. `grep -rn ": any" frontend/src` should stay
  near zero.
- **`as` without a check.** Narrow, then assert.
- **Forgetting `T` at the call site** — `apiFetch(path)` returns `unknown` and every use
  needs a narrow. Supply it.
- **Generic constraints missing** — `keyof T` on an unconstrained `T` is too wide.

### Key takeaways

- Generics let one function serve many types: `apiFetch<T>`, `useApi<T>`, `useForm<T
  extends Record<string, unknown>>`.
- `unknown` at the edges (error bodies, caught errors, parsed JSON); narrow with `typeof`,
  `Array.isArray`, `in`, `instanceof`, type guards (`err is ApiError`).
- `as` only after a check (or for the caller's promise about the API).
- `Promise<T>` types async results; 204 → `undefined as T`.
- Every `apiFetch` has a deadline: 30 s for the headers by default, `timeoutMs` to change it, no
  way to switch it off. A time-out is `status: 0` with `timedOut`; a timed-out write is also
  `outcomeUnknown`. Check `timedOut` before treating `status: 0` as "unreachable".

---

## Hour 4 — Narrowing, optional chaining, and mirroring the backend schema

### What & why

Under `strict`, `null` and `undefined` are not members of other types: `key: string | null`
won't `.toLowerCase()` until you've proven it's a string. TypeScript **narrows** a union as
you check it — `if (experiment.key)`, `typeof rawId === 'string'`, `status === 'draft'` —
and the checked branch sees the narrower type. Two operators do most of the daily work:

- **`?.`** optional chaining: `hero.configuration?.cta` is `undefined` if `configuration`
  is `null`/`undefined`, instead of throwing.
- **`??`** nullish coalescing: `raw.key ?? ''` uses `''` only when the left side is
  `null`/`undefined` — unlike `||`, which also replaces `0`, `''` and `false`. (The
  zero-conversions bug in a results table is a `||`.)

And **discriminated unions** — objects with a literal `kind`/`status` field — let a `switch`
narrow the whole object: `if (snapshot.status === 'error') snapshot.error` is legal only in
that branch.

#### Mirroring the schema, and where the mirror is deliberately loose

`Experiment.experiment_type` is `ExperimentType | string`. That's not sloppiness: a backend
that adds a type before the dashboard ships should render as text, not crash a page that
`switch`es on the union. The rule the codebase follows: **exact unions for things the
dashboard branches on (status, actions), loose `| string` for things it only displays.**

### In the wild

Narrowing the router's query parameter — it can be a string, an array, or undefined:

```typescript
// 📁 frontend/src/pages/experiments/[id].tsx
  const rawId = router.query.id;
  const id = typeof rawId === 'string' ? rawId : undefined;
  ...
  const load = useCallback(async () => {
    if (!id) return;
```

Narrowing a caught error and reading a field only the narrowed type has:

```typescript
// 📁 frontend/src/pages/experiments/[id].tsx
    } catch (err) {
      const status = isApiError(err) ? err.status : 0;
      setLoadError({
        status,
        message: err instanceof Error ? err.message : 'Failed to load experiment',
      });
```

`??` and `?.` in the defensive parser — `??` so a legitimate `0` survives, and a `!= null`
(loose, on purpose: it covers both `null` and `undefined`) for a nullable p-value:

```typescript
// 📁 frontend/src/hooks/useExperimentStream.ts
    participantCount: Number(raw.participant_count ?? 0),
    pValue: raw.p_value != null ? Number(raw.p_value) : null,
    ...
    daysRunning: raw.days_running != null ? Number(raw.days_running) : null,
    error: raw.error != null ? String(raw.error) : undefined,
```

A `switch` over a literal union with a `Record` lookup, and a guard that renders `—` for
absent dates:

```typescript
// 📁 frontend/src/pages/experiments/[id].tsx
function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const d = parseApiTimestamp(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString();
}
```

`parseApiTimestamp` (`frontend/src/utils/apiTimestamp.ts`) is a small defence against a real trap: the
API sends some times without a zone (`2026-10-11T05:02:00`), and `new Date()` would read that as the
browser's local time instead of UTC. The helper appends `Z` only to a string that names no zone and
otherwise falls back to `new Date(value)`. Note what the guard does with an unparseable string: it
shows the raw value rather than "Invalid Date".

Optional chaining into a JSON blob the app doesn't control, with a typed reader that
normalises it — from the demo app that consumes the SDK:

```typescript
// 📁 demo/shoplab/src/pages/index.tsx
function readHeroConfig(configuration: Record<string, unknown> | null) {
  const cfg = configuration ?? {};
  return {
    media: cfg.media === 'video' ? 'video' : 'image',
    headline: typeof cfg.headline === 'string' && cfg.headline ? cfg.headline : HERO_DEFAULTS.headline,
    cta: typeof cfg.cta === 'string' && cfg.cta ? cfg.cta : HERO_DEFAULTS.cta,
  } as const;
}
```

`as const` makes the literal values readonly literals (`'video' | 'image'`), so
`media === 'video'` downstream is type-checked rather than a string comparison.

A discriminated union that is also generic — the state of one read on the experiment page (its
exclusion group, or the global holdout):

```typescript
// 📁 frontend/src/hooks/useExperimentEligibility.ts
export type EligibilityRead<T> =
  | { kind: 'loading' }
  | { kind: 'present'; value: T }
  | { kind: 'absent' }
  | { kind: 'refused' }
  | { kind: 'error' };

type Answer<T> = Exclude<EligibilityRead<T>, { kind: 'loading' }>;
```

Only the `present` member has a `value`, so the page can read it only after checking `kind`:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
        {holdout.kind === 'present' && (
          <dd data-testid="experiment-holdout">
            <Link href="/experiments/holdouts" className={NOTE_LINK}>
              {holdoutLabel(holdout.value)}
            </Link>
          </dd>
        )}
```

Why five members and not `GlobalHoldout | null`? Because `null` would have to mean four different
things — not loaded yet, none active, not allowed to see it, could not be read — and the page says
something different for each (`ELIGIBILITY_COPY`, with a test id per state). The parser that feeds it
draws the same line: JSON `null` from the API is `absent` ("None active"), but an empty body, an
inactive holdout or any other odd shape is `error`, "never 'none', which would be a claim the page
cannot back". `Exclude<EligibilityRead<T>, { kind: 'loading' }>` is a built-in utility type
removing one member from the union: an *answer* is anything but `loading`.

The mirror, with its loose-on-purpose field and its `Record<string, unknown>` blobs:

```typescript
// 📁 frontend/src/types/experiments.ts
  experiment_type: ExperimentType | string;
  status: ExperimentStatus;
  targeting_rules: Record<string, unknown> | null;
  configuration?: Record<string, unknown> | null;
```

`status` is the exact union (the page branches on it: `ACTIONS_BY_STATUS[experiment.status]`);
`experiment_type` is loose (the page only labels it); `targeting_rules` and `configuration`
are opaque JSON the dashboard passes through (the rule builder has its own typed shape,
Hour 19). The flag type has a newer field written the same way: `value_type?: string` in
`frontend/src/services/featureFlags.ts` is documented as "read as any string so a type a later
release adds is still shown", even though the API today sends one of `boolean`, `string`,
`number` or `json`.

> **Watch Out!** `||` for defaults is the JavaScript zero trap: `conversions || '—'` renders
> `—` for a real zero. Use `??` for "missing," and be explicit (`value === null ? '—' : value`)
> when `0` and `null` mean different things — as they do in a results table where `null`
> p-value means "not computed" and `0` means "very significant."

> **Did You Know?** `strictNullChecks` is why `key: string | null` forces `experiment.key ??
> experiment.id` before use, and why the dashboard's key-generation helper runs on the client
> as the user types: `generateKey(name)` in `components/experiments/new/formState.ts` (re-exported
> from `pages/experiments/new.tsx`) mirrors the server's
> `ExperimentService.generate_key` (minus the random suffix) so the field is never null in a
> request the dashboard builds.

### Try It Yourself

```typescript
type Snapshot = { status: 'active'; variants: string[] } | { status: 'error'; error: string };
function describe(s: Snapshot) {
  if (s.status === 'error') return s.error;          // ✅ narrowed
  return s.variants.length;                          // ✅ the other branch
}

const q: string | string[] | undefined = ['a'];
const id = typeof q === 'string' ? q : undefined;    // never an array

const cfg: Record<string, unknown> | null = null;
cfg?.cta;                                            // undefined, no throw
(cfg ?? {}).cta;                                     // same idea

0 || 'fallback';   // 'fallback' — the zero trap
0 ?? 'fallback';   // 0
```

Then find every `!= null` in `frontend/src/hooks/useExperimentStream.ts` and explain why loose
inequality is the right tool there (hint: two values, one check).

### Common mistakes

- **`||` for defaults on numeric or boolean fields.** `??`.
- **Branching on a `| string` union.** If you need to branch, tighten the type (and the
  backend contract).
- **Accessing a possibly-null field without narrowing** — the compiler tells you; don't
  `!`-assert it away.
- **`as` to skip a narrow.** Check first.

### Key takeaways

- `strict` makes `null`/`undefined` explicit; narrow with `typeof`, `===`, `instanceof`,
  guards; the branch sees the narrower type.
- `?.` to reach into maybe-null; `??` for nullish defaults (never `||` for numbers);
  `!= null` when one check should cover both.
- Discriminated unions + `switch`/`===` narrow whole objects.
- Mirror the schema exactly where the dashboard branches; loosen with `| string` where it
  only displays; JSON blobs are `Record<string, unknown>`.

---

## Part I Workshop

**Q1. Why is `raw.participant_count ?? 0` inside `Number(...)` better than
`Number(raw.participant_count)`?**
`Number(undefined)` is `NaN`, which renders as "NaN"; the `?? 0` default makes a missing
field a visible zero instead. The parser is the defensive edge for data the dashboard doesn't
control.

**Q2. What does `Record<ExperimentStatus, string>` buy that `{ [k: string]: string }`
doesn't?**
Completeness: every member of the union must have an entry, so adding a status to the union
breaks every label/colour/action table that forgot it — at compile time.

**Q3. `catch (err)` — what is `err`'s type under `strict`, and how does the codebase read
`err.status`?**
`unknown`. `isApiError(err)` is a type guard (`err is ApiError`) that narrows it; in the
true branch `err.status` is legal.

**Q4. Why is `experiment_type` typed `ExperimentType | string` while `status` is exactly
`ExperimentStatus`?**
The page branches on `status` (`ACTIONS_BY_STATUS`), so it must be exact; it only labels
`experiment_type`, so an unknown value should render as text rather than break the type
— loose on purpose.

**Q5. `conversions || '—'` vs `conversions ?? '—'` for a table cell?**
`??`. `||` replaces a real `0` with `—`; `??` only replaces `null`/`undefined`.

**Q6. Where is `as` legitimate in `api.ts`, and what makes it so?**
After a check that proves the shape — `'detail' in (parsed as object)` then
`(parsed as { detail: unknown }).detail` — and at the API-contract edge (`(await
r.json()) as T`, the caller's promise). Never to silence the compiler.

**Q7. Why does `tsconfig.json` list two targets for `@modules/*`, and what does
`tsconfig.core.json` change?**
The first target is the real module code (`../modules/frontend/src/*`), the second the stub
tree (`./src/modules-stub/*`); the first that contains the file wins, so a full checkout gets
the module and a module it does not carry falls back to the stub. `tsconfig.core.json` keeps
only the stub target and excludes `../modules`, so a core type-check cannot quietly resolve
to module code that a core build will not have.

**Q8. A `POST` to create something times out after 30 seconds. Why does the dashboard not say
"it failed"?**
Because it does not know. The API may have received the request and carried it out after the
dashboard stopped waiting. `timeoutError` marks any method other than GET, HEAD or OPTIONS as
`outcomeUnknown`, and the message asks the user to reload and check before trying again, so a
retry cannot create a second copy unseen.

**Quiz.** `const id = typeof rawId === 'string' ? rawId : undefined;` — what types can
`router.query.id` be, and which does this exclude?
→ `string | string[] | undefined`; it excludes the array (a repeated query parameter),
leaving `string | undefined`.

---

## Exercises

**E1.1 — Mirror a schema.** Given the backend's `RolloutStage` response fields (`id`,
`name`, `stage_order: int`, `target_percentage: int`, `trigger_type: "time_based" |
"metric_based" | "manual"`, `start_date: Optional[datetime]`, `status: "pending" |
"in_progress" | "completed"`), write the `RolloutStage` interface and two literal-union
types in the house style, with a `Record<..., string>` label table for `status`.

**E1.2 — A defensive parser.** Write `parseSafetyCheck(raw: Record<string, unknown>)`
returning `{ isHealthy: boolean; metrics: Array<{ name: string; currentValue: number;
threshold: number }> }` from the snake_case server shape, with defaults that never produce
`NaN`.

**E1.3 — Narrow an error.** Write `describeError(err: unknown): string` that returns
`ApiError.message` with its status for API errors, `err.message` for other `Error`s, and
`String(err)` otherwise — no `any`, no unchecked `as`.

**E1.4 — Generic hook signature.** Write the signature (no body) of `usePaginated<T>(fetcher:
(skip: number, limit: number) => Promise<{ items: T[]; total: number }>, pageSize?: number)`
returning `{ items: T[]; total: number; page: number; next(): void; prev(): void; loading:
boolean }`.

**E1.5 — Spot the traps.** Find the three bugs:

```typescript
function cell(v: { conversions: number | null; rate: number }) {
  return `${v.conversions || '—'} (${(v.rate * 100).toFixed(1)}%)`;
}
const status: string = experiment.status;
const actions = ACTIONS_BY_STATUS[status];
const cfg = (experiment.targeting_rules as { groups: unknown[] }).groups;
```

---

## Solutions

**S1.1**

```typescript
export type TriggerType = 'time_based' | 'metric_based' | 'manual';
export type RolloutStageStatus = 'pending' | 'in_progress' | 'completed';

/** `RolloutStageResponse` */
export interface RolloutStage {
  id: string;
  name: string;
  stage_order: number;
  target_percentage: number;
  trigger_type: TriggerType;
  start_date: string | null;
  status: RolloutStageStatus;
}

export const STAGE_STATUS_LABELS: Record<RolloutStageStatus, string> = {
  pending: 'Pending',
  in_progress: 'In progress',
  completed: 'Completed',
};
```

The dashboard now has a mirror of its own, written when the flag page gained its rollout-schedule
section (Hour 15): `RolloutStage` in `frontend/src/services/featureFlags.ts`. Compare yours with
it. It calls the trigger union `RolloutTriggerType`, it carries the fields this exercise left out
(`rollout_schedule_id`, `trigger_configuration`, `completed_date`, `started_at` and the
timestamps), and its status union has a fourth member, `'failed'`, because the backend's
`RolloutStageStatus` enum has one. The exercise's field list was incomplete; a mirror copied from a
description instead of from the schema inherits the description's gaps. That is why the house
rule says to mirror the schema itself.

**S1.2**

```typescript
export function parseSafetyCheck(raw: Record<string, unknown>) {
  const rawMetrics = Array.isArray(raw.metrics) ? raw.metrics : [];
  return {
    isHealthy: Boolean(raw.is_healthy),
    metrics: rawMetrics.map((m) => {
      const rec = (m && typeof m === 'object' ? m : {}) as Record<string, unknown>;
      return {
        name: String(rec.name ?? ''),
        currentValue: Number(rec.current_value ?? 0),
        threshold: Number(rec.threshold ?? 0),
      };
    }),
  };
}
```

**S1.3**

```typescript
export function describeError(err: unknown): string {
  if (isApiError(err)) return `${err.message} (HTTP ${err.status})`;
  if (err instanceof Error) return err.message;
  return String(err);
}
```

**S1.4**

```typescript
export function usePaginated<T>(
  fetcher: (skip: number, limit: number) => Promise<{ items: T[]; total: number }>,
  pageSize = 25,
): { items: T[]; total: number; page: number; next(): void; prev(): void; loading: boolean };
```

**S1.5** — (1) `v.conversions || '—'` renders `—` for zero conversions; use `v.conversions
?? '—'` (or `=== null`). (2) `const status: string = experiment.status` widens the union, so
`ACTIONS_BY_STATUS[status]` is an error (or, with an index signature, silently `undefined`);
keep `ExperimentStatus`. (3) `experiment.targeting_rules as { groups: unknown[] }` asserts a
shape without checking, and `targeting_rules` may be `null` — narrow first
(`if (rules && typeof rules === 'object' && Array.isArray((rules as {groups?: unknown}).groups))`)
or route it through the typed rule-builder parser.

---

**Next:** [Part II — React Fundamentals →](./part-2-react.md)

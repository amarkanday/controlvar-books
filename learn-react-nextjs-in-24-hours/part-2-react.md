# Part II — React Fundamentals

### Hours 5–9

[← Part I](./part-1-typescript.md) · [Contents](./README.md) · [Part III →](./part-3-nextjs.md)

React in five hours, the way the dashboard uses it: components as functions returning JSX
(Hour 5), the one-way flow of props in and state held by `useState` (Hour 6), effects for
"do something after render" and the cancellation discipline that stops stale responses
(Hour 7), custom hooks — the unit of reuse — and the rules that make them work (Hour 8), and
context for the two things every page needs without prop-drilling: who's logged in and how
to show a toast (Hour 9). Have `make web` running and edit a component to see it hot-reload.

---

## Hour 5 — Components and JSX

### What & why

A **component** is a function that takes a `props` object and returns **JSX** — HTML-like
syntax that compiles to `React.createElement` calls describing what to render. React calls
the function, compares the description to the last one, and updates the DOM minimally.
Three consequences:

1. **Rendering is a function of inputs.** Same props and state → same output. A component
   doesn't _do_ things; it _describes_ things. Side effects go in event handlers or effects
   (Hour 7).
2. **JSX is expressions.** `{expr}` interpolates any expression; `cond && <X/>` and
   `cond ? <A/> : <B/>` branch; `items.map(item => <Row key={item.id} .../>)` repeats —
   with a stable `key` per item so React can track identity.
3. **Attributes are props.** `className` (not `class`), `onClick={fn}` (a function, not a
   string), `htmlFor`, and `data-testid` for tests. Boolean props: `disabled={cond}`.

The dashboard's components come in three sizes: **pages** (a default export under
`src/pages/`, Hour 10), **feature components** (`ResultsDashboard`, `TargetingRuleBuilder`),
and **primitives** (`FlagToggle`, `PageTitle`). Small, named, one job each.

### In the wild

A primitive — one function, typed props, JSX with computed classes, accessibility attributes,
a `data-testid` for tests:

```tsx
// 📁 frontend/src/components/feature-flags/FlagToggle.tsx
export interface FlagToggleProps {
  on: boolean;
  onChange: (next: boolean) => void;
  disabled?: boolean;
  /** Accessible name, e.g. "Turn checkout_v2 on". */
  label: string;
  'data-testid'?: string;
}

/**
 * Accessible on/off switch (`role="switch"`, `aria-checked`) used by the flag
 * list rows and the flag detail header.
 */
export function FlagToggle({ on, onChange, disabled = false, label, ...rest }: FlagToggleProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      data-state={on ? 'on' : 'off'}
      data-testid={rest['data-testid'] ?? 'flag-toggle'}
      className={[
        'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        on ? 'bg-green-600' : 'bg-slate-300',
      ].join(' ')}
    >
      <span
        aria-hidden="true"
        className={[
          'inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform',
          on ? 'translate-x-5' : 'translate-x-0.5',
        ].join(' ')}
      />
    </button>
  );
}
```

Read what it _doesn't_ do: it holds no state (`on` comes from the parent), it doesn't call
the API (`onChange` is the parent's job), it just describes a switch. That's a **controlled
component** — the standard shape for anything reusable.

Conditional rendering — early returns for loading and error states, then the main tree:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  if (!id || loading) {
    return (
      <div className="flex-1 bg-slate-50 flex items-center justify-center">
        <PageTitle title="Experiment" />
        <div className="text-center" data-testid="experiment-loading">
          <div className="inline-block w-6 h-6 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
          <p className="text-slate-500 mt-2 text-sm">Loading experiment…</p>
        </div>
      </div>
    );
  }

  if (loadError || !experiment) {
    const notFound = loadError?.status === 404;
    return (
      <div className="flex-1 bg-slate-50 flex items-center justify-center">
        <PageTitle title={notFound ? 'Experiment not found' : 'Experiment'} />
        <div className="text-center max-w-md px-4" data-testid={notFound ? 'experiment-not-found' : 'experiment-error'}>
          ...
          {!notFound && loadError?.message && (
            <p className="text-sm text-red-600 mb-4">{loadError.message}</p>
          )}
          ...
        </div>
      </div>
    );
  }
```

Three renders for three states, chosen by early return — the same "early return over
nesting" rule the Python book teaches. `{cond && <X/>}` renders `X` only when `cond` is
truthy; `{a ? <X/> : <Y/>}` picks one. And after the guards, the compiler knows
`experiment` is not null.

A list with keys — the lifecycle buttons, driven by the status table from Hour 2 and by the
user's role (abridged where marked `...`):

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const statusActions = ACTIONS_BY_STATUS[status] ?? [];
  const mayChange = canChangeExperiment(user);
  // Offer only what the API would accept: a role without EXPERIMENT UPDATE is
  // refused every lifecycle change, including on an experiment it owns.
  const actions = mayChange ? statusActions : [];
  ...
              {actions.map((action) => {
                const meta = ACTION_META[action];
                const busy = pendingAction === action;
                return (
                  <button
                    key={action}
                    type="button"
                    onClick={() => onActionClick(action)}
                    disabled={pendingAction !== null}
                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${meta.className}`}
                    data-testid={`action-${action}`}
                  >
                    {busy ? meta.pending : meta.label}
                  </button>
                );
              })}
```

`key={action}` — stable and unique per item. `onClick={() => onActionClick(action)}` — an
arrow so the handler is _called_ on click, not during render.

`canChangeExperiment(user)` (from `frontend/src/utils/experimentPermissions.ts`) is a plain
function of the user, not of the experiment: ADMIN and DEVELOPER (or a superuser) may start,
pause, complete and archive **any** experiment; ANALYST and VIEWER may change none, even one
they created. It mirrors the API's own check, so the page never offers a button the API would
refuse. An ANALYST sees the status and a sentence instead of buttons
(`data-testid="experiment-role-note"`), worded differently when they own the experiment. Hour
16 comes back to this.

Composition — a page assembling feature components, each with one prop:

```tsx
// 📁 frontend/src/components/results/ResultsDashboard/ResultsDashboard.tsx
import { ExperimentSummary } from './ExperimentSummary';
import { SampleSizeMeter } from './SampleSizeMeter';
import { ConversionChart } from '@/components/results/Visualizations/ConversionChart';
import { TrendChart } from '@/components/results/Visualizations/TrendChart';
import { MetricComparisonTable } from '@/components/results/MetricComparison/MetricComparisonTable';
import { SequentialMonitor } from '@/components/results/Sequential/SequentialMonitor';
...
export function ResultsDashboard({ experimentId }: ResultsDashboardProps) {
```

A fragment (`<>...</>`) to return siblings without a wrapper element, from the app root:

```tsx
// 📁 frontend/src/pages/_app.tsx
  return (
    <ErrorBoundary>
      <Head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
        <title>Controlvar</title>
      </Head>
      <ModulesProvider>
        <AuthProvider>
          <NotificationProvider>
            {page}
            <ToastContainer />
          </NotificationProvider>
        </AuthProvider>
      </ModulesProvider>
    </ErrorBoundary>
  );
```

> **Watch Out!** `onClick={onActionClick(action)}` — with parentheses and no arrow — _calls_
> the function during render and passes its return value (`undefined`) as the handler. The
> button does nothing and the action fires on every render. Arrow functions in JSX handlers
> are the idiom; `useCallback` (Hour 8) when identity matters.

> **Did You Know?** `data-testid` attributes are how the Jest page tests and the Playwright
> scenario runner find elements without depending on Tailwind classes or copy. The dashboard
> uses them on every state a test cares about (`experiment-loading`, `experiment-not-found`,
> `action-start`). Query by role/text first in tests; test ids are for structure that has no
> semantic handle.

### Try It Yourself

In a scratch file under `frontend/src/components/` (delete it after):

```tsx
import React from 'react';

interface StatusBadgeProps { status: 'draft' | 'active' | 'paused'; count?: number }

export function StatusBadge({ status, count }: StatusBadgeProps) {
  const colour = { draft: 'bg-slate-200', active: 'bg-green-200', paused: 'bg-amber-200' }[status];
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${colour}`} data-testid={`badge-${status}`}>
      {status}
      {count !== undefined && <span className="ml-1 font-mono">{count}</span>}
    </span>
  );
}

export function BadgeList({ items }: { items: StatusBadgeProps[] }) {
  return <ul>{items.map((i) => <li key={i.status}><StatusBadge {...i} /></li>)}</ul>;
}
```

Render `<BadgeList items={[...]} />` from any page and watch it hot-reload; then remove the
`key` and read the console warning.

### Common mistakes

- **Calling a handler in JSX** (`onClick={fn()}`).
- **Missing or index-based `key`s** in lists — React can't track identity; state jumps rows.
- **`class` instead of `className`**, `for` instead of `htmlFor`.
- **Side effects in the render body** (fetching, `setTimeout`) — effects and handlers only.

### Key takeaways

- A component is a pure function of props (and state) that returns JSX; side effects live in
  handlers and effects.
- `{expr}`, `&&`, `?:`, `.map` with stable `key`s, fragments.
- Controlled primitives (`FlagToggle`): state and API calls belong to the parent.
- `data-testid` on states tests need; early returns for loading/error/success.

---

## Hour 6 — Props and state (`useState`)

### What & why

**Props** flow down: a parent passes values (and callbacks) to a child, and the child treats
them as read-only. **State** is a component's own memory across renders: `const [value,
setValue] = useState(initial)`. Calling `setValue` schedules a re-render with the new value;
React re-runs the function; the JSX reflects the change.

Rules that keep state predictable:

- **Never mutate state.** `setVariants([...variants, newOne])`, `setVariants(variants.map(...))`,
  `{ ...prev, [field]: value }` — always a new object or array. React compares by identity;
  a mutated array is "the same" and nothing re-renders.
- **Updates may be batched and asynchronous.** After `setCount(count + 1)`, `count` is still
  the old value in this render. Use the **functional form** `setCount(c => c + 1)` when the
  next value depends on the previous.
- **Lift state up** to the nearest common parent when two components need it; pass it down
  as props and changes up as callbacks (`onChange`).
- **Derive, don't duplicate.** If a value can be computed from state (`allocationTotal` from
  `variants`), compute it during render (or `useMemo`) rather than storing a second copy.

### In the wild

A page with several independent pieces of state, each `useState` typed by its initial
value or explicitly:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<{ status: number; message: string } | null>(null);
  const [pendingAction, setPendingAction] = useState<LifecycleAction | null>(null);
  const [confirming, setConfirming] = useState<LifecycleAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
```

`useState<Experiment | null>(null)` — the explicit type parameter because `null` alone
would infer `null` forever. Six booleans-or-nulls rather than one big object: each changes
independently, and separate state keeps updates simple.

Immutable updates on arrays of records — add, remove, change one field, set exactly one
control. In the new-experiment form these live in a **reducer** (a pure function from the old
state and an action to the new state; the sidebar below says why), but each case is exactly
the update you would write in a `setVariants(...)` call:

```tsx
// 📁 frontend/src/components/experiments/new/formState.ts
    case 'addVariant':
      return {
        ...state,
        variants: [
          ...state.variants,
          {
            name: `Variant ${state.variants.length}`,
            description: '',
            traffic_allocation: 0,
            is_control: false,
          },
        ],
      };
    case 'removeVariant':
      return { ...state, variants: state.variants.filter((_, i) => i !== action.index) };
    case 'changeVariant':
      return {
        ...state,
        variants: state.variants.map((v, i) =>
          i === action.index ? { ...v, [action.field]: action.value } : v,
        ),
      };
    case 'setControl':
      return {
        ...state,
        variants: state.variants.map((v, i) => ({ ...v, is_control: i === action.index })),
      };
```

Spread (`...state.variants`, `{ ...v, [action.field]: action.value }`), `filter`, `map` — never
`push`, `splice`, or `v.name = ...`. `setControl` is the "exactly one" idiom: map every item,
setting the flag by index comparison. Note the outer `{ ...state, variants: ... }`: the whole
form is one object, so every case copies it and replaces one field.

Derived state computed during render, by a plain function rather than stored:

```tsx
// 📁 frontend/src/components/experiments/new/formState.ts
export function allocationTotalOf(variants: VariantFormData[]): number {
  return variants.reduce((sum, v) => sum + (Number(v.traffic_allocation) || 0), 0);
}
```

```tsx
// 📁 frontend/src/components/experiments/new/VariantsEditor.tsx
export function VariantsEditor({ variants, dispatch, revealProblems = 0 }: VariantsEditorProps) {
  const allocationTotal = allocationTotalOf(variants);
```

(`Number(x) || 0` here is a legitimate `||`: `NaN` from an empty input should become 0.) The
sum is cheap, so there is no `useMemo`; it is recomputed on every render, and the same
function is used by the validator, so the number on screen and the number checked cannot
differ.

The functional update form — the previous value is the input:

```tsx
// 📁 frontend/src/hooks/useForm.ts
  const setValue = useCallback(
    (field: keyof T, value: T[keyof T]) => {
      setValues((prev) => {
        const next = { ...prev, [field]: value };
        const fieldError = validateField(field, value, next);
        setErrors((prevErrors) => {
          const updated = { ...prevErrors };
          if (fieldError) {
            updated[field] = fieldError;
          } else {
            delete updated[field];
          }
          return updated;
        });
        return next;
      });
    },
    [validateField],
  );
```

```tsx
// 📁 frontend/src/contexts/NotificationContext.tsx
      setToasts((prev) => [...prev, toast]);
      ...
    setToasts((prev) => prev.filter((t) => t.id !== id));
```

`setToasts((prev) => ...)` is correct even if several toasts are added in one tick; `setToasts([...toasts,
toast])` would drop all but the last, because `toasts` in the closure is stale.

A derived name that a "key edited?" flag protects — state for a user decision, not for a
computed value:

```tsx
// 📁 frontend/src/components/experiments/new/formState.ts
    case 'setName':
      return {
        ...state,
        name: action.name,
        key: state.keyEdited ? state.key : generateKey(action.name),
      };
    case 'setKey':
      return { ...state, key: action.key, keyEdited: true };
```

The key _follows_ the name until the user edits it; `keyEdited` is the one field of
`ExperimentFormState` that records the decision. The input only says what happened:
`onChange={(e) => dispatch({ type: 'setName', name: e.target.value })}`.

#### Sidebar: `useReducer`, when one `useState` per field stops scaling

`/experiments/new` used to hold nine `useState`s and a handler per change. It now holds one:

```tsx
// 📁 frontend/src/pages/experiments/new.tsx
  const [state, dispatch] = useReducer(experimentFormReducer, undefined, createInitialFormState);
```

`useReducer(reducer, initialArg, init)` calls `init(initialArg)` once for the first state,
then every `dispatch(action)` runs `reducer(state, action)` and re-renders with the result.
The actions are a **discriminated union** (Hour 4's narrowing, applied to events):

```tsx
// 📁 frontend/src/components/experiments/new/formState.ts
export type ExperimentFormAction =
  | { type: 'setName'; name: string }
  | { type: 'setKey'; key: string }
  ...
  | { type: 'addVariant' }
  | { type: 'removeVariant'; index: number }
  ...
```

Inside `case 'removeVariant':` the compiler knows `action.index` exists; in `case
'addVariant':` it knows it does not. Three things made the reducer worth it here:

1. **Two views share one state.** The page shows either a step-by-step wizard or a
   single-page form (Hour 15). Both receive `state` and `dispatch`; switching views keeps
   every answer because there is only one copy.
2. **The logic is testable without React.** `formState.ts` imports no React at all; its tests
   call `experimentFormReducer(state, action)` and compare objects.
3. **Child components stay dumb.** `VariantsEditor` takes `variants` and `dispatch` and
   says what happened; it does not know how the state is shaped above it.

Reach for `useReducer` when several fields change together or more than one component
changes the same state; keep `useState` for independent values like `pendingAction` above.

> **Watch Out!** `useState` initialises once. `useState(props.value)` does _not_ track later
> changes to `props.value` — that's the "controlled vs uncontrolled" confusion. If the parent
> owns the value, don't copy it into state; render from the prop and call `onChange`
> (`FlagToggle`). If the child owns it, initialise once and ignore later prop changes on
> purpose.

> **Did You Know?** State updates inside event handlers are batched (one render), and since
> React 18 (the dashboard runs React 19) they're batched everywhere, including after `await`. So in `runAction`,
> `setExperiment(updated)` followed by `setPendingAction(null)` in `finally` is one
> re-render, not two.

### Try It Yourself

```tsx
function Allocations() {
  const [variants, setVariants] = React.useState([{ name: 'Control', pct: 50 }, { name: 'B', pct: 50 }]);
  const total = variants.reduce((s, v) => s + v.pct, 0);
  return (
    <div>
      {variants.map((v, i) => (
        <label key={v.name}>
          {v.name}
          <input type="number" value={v.pct}
                 onChange={(e) => setVariants(variants.map((x, j) => (j === i ? { ...x, pct: Number(e.target.value) || 0 } : x)))} />
        </label>
      ))}
      <button type="button" onClick={() => setVariants((prev) => [...prev, { name: `V${prev.length}`, pct: 0 }])}>add</button>
      <p className={total === 100 ? 'text-green-700' : 'text-red-700'}>total {total}%</p>
    </div>
  );
}
```

Render it, then break it: replace the `map` with `variants[i].pct = ...; setVariants(variants)`
and notice nothing updates.

### Common mistakes

- **Mutating state** (`push`, `splice`, assignment) — no re-render.
- **`setX(x + 1)` twice** expecting +2 — use `setX((x) => x + 1)`.
- **Copying props into state** when the parent owns the value.
- **Storing derived values** (`total`) in state — compute them.

### Key takeaways

- Props down, callbacks up; state is a component's memory; `setState` schedules a
  re-render.
- Never mutate: spread/`map`/`filter`; functional updates when the next depends on the
  previous.
- Derive during render (`useMemo` if expensive); keep decisions (`keyEdited`) as small
  booleans.
- Type `useState<T | null>(null)` explicitly.

---

## Hour 7 — Effects and cleanup (`useEffect`, the cancelled flag)

### What & why

`useEffect(fn, deps)` runs `fn` **after** a render, and again whenever a value in `deps`
changes. It's for synchronising with things outside React: fetching data when an id
changes, subscribing to a WebSocket, adding a window listener. If `fn` returns a function,
React calls it **before the next run and on unmount** — that's cleanup, and it's where most
effect bugs are prevented.

The one discipline that matters most in this codebase: **an async effect must ignore
results that arrive after it's been superseded.** Navigate from experiment A to B while A's
fetch is in flight, and A's response would overwrite B's state — unless the effect's cleanup
flipped a `cancelled` flag that the callback checks. Every fetching hook and effect in the
dashboard and the React SDK does this.

Dependencies are not optional. `[]` means "once, on mount"; `[id]` means "when `id`
changes"; omitting the array means "after every render" (almost never right). The
`react-hooks/exhaustive-deps` lint rule catches missing deps; where the codebase disables it,
there's a comment saying why.

### In the wild

The canonical cancelled-flag effect — from the React SDK's hook, used by the dashboard's
demo apps and identical in shape to the dashboard's own:

```tsx
// 📁 sdk/react/src/hooks/useExperiment.ts
export function useExperiment(experimentKey: string): ExperimentAssignment {
  const { client, user } = useExperimentation();
  const [state, setState] = useState<ExperimentAssignment>(() =>
    defaultAssignment(experimentKey, { loading: true })
  );

  useEffect(() => {
    let cancelled = false;
    setState(defaultAssignment(experimentKey, { loading: true }));

    client
      .assignExperiment(user, experimentKey)
      .then(assignment => {
        if (!cancelled) setState(assignment);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const error = err instanceof Error ? err : new Error(String(err));
          setState(defaultAssignment(experimentKey, { error }));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [client, user, experimentKey]);

  return state;
}
```

Trace a key change: effect 1 runs (key A), request in flight; key becomes B; React calls
effect 1's cleanup (`cancelled = true` for A), then runs effect 2 (key B). When A's response
arrives, `if (!cancelled)` is false — ignored. When B's arrives, it's applied. Note also
`useState(() => ...)` — a **lazy initialiser** so the default object is built once, not on
every render.

The dashboard's `useApi` hook does the same with a ref that outlives renders:

```tsx
// 📁 frontend/src/hooks/useApi.ts
  const mountedRef = useRef(true);

  const execute = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetcher();
      if (mountedRef.current) {
        setData(result);
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    mountedRef.current = true;
    if (immediate) {
      execute();
    }
    return () => {
      mountedRef.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [execute, immediate]);
```

`useRef` holds a mutable value that doesn't trigger renders — the right home for "am I still
mounted." The two `eslint-disable` comments are deliberate: `deps` is the _caller's_ list,
so the hook can't enumerate it for the linter.

A page effect that just calls a memoised loader — the loader captures `id`, so the effect
depends on the loader:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setLoadError(null);
    try {
      const data = await ExperimentsService.get(id);
      setExperiment(data);
    } catch (err) {
      ...
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);
```

`void load()` — the effect callback must not _return_ a promise (React would treat it as a
cleanup function); `void` discards it. And because `load` is `useCallback`'d on `[id]`, the
effect re-runs exactly when the id changes.

A subscription with cleanup — the cross-tab auth sync:

```tsx
// 📁 frontend/src/contexts/AuthContext.tsx
  // Keep tabs in sync: logging out (or in) elsewhere updates this tab too.
  useEffect(() => {
    if (typeof window === 'undefined') return undefined;
    const onStorage = (event: StorageEvent) => {
      if (event.key !== TOKEN_STORAGE_KEY && event.key !== null) return;
      if (!getToken()) {
        applyAnonymous();
      } else {
        void loadMe();
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [applyAnonymous, loadMe]);
```

Add on run, remove on cleanup — the listener never leaks, and the `typeof window` guard
keeps it safe during Next.js's server-side render, where there's no `window`.

A timer with cleanup via a ref, in the toast provider:

```tsx
// 📁 frontend/src/contexts/NotificationContext.tsx
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const removeToast = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);
```

> **Watch Out!** In development with React's Strict Mode, effects run **twice** on mount
> (mount → cleanup → mount) to surface missing cleanup. A fetch effect without a cancelled
> flag shows a double request and possibly a flash of stale data; with the flag it's
> invisible. If "my effect runs twice" is your symptom, the answer is cleanup, not removing
> Strict Mode.

> **Did You Know?** `useEffect` callbacks can't be `async` because an async function returns
> a promise and React expects either nothing or a cleanup function. The idiom is to define
> an async function inside (or outside, memoised) and call it with `void`, exactly as
> `void load()` does.

### Try It Yourself

```tsx
function Detail({ id }: { id: string }) {
  const [data, setData] = React.useState<string | null>(null);
  React.useEffect(() => {
    let cancelled = false;
    setData(null);
    new Promise<string>((r) => setTimeout(() => r(`loaded ${id}`), id === 'a' ? 800 : 100))
      .then((v) => { if (!cancelled) setData(v); });
    return () => { cancelled = true; };
  }, [id]);
  return <p>{data ?? 'loading…'}</p>;
}
// mount with id="a", switch to id="b" after 200 ms: shows "loaded b", never "loaded a"
```

Remove the `cancelled` guard and watch "loaded a" overwrite "loaded b" 600 ms later. Then
open `frontend/src/hooks/useExperimentStream.ts` and find the cleanup that closes the socket.

### Common mistakes

- **No cleanup on async effects** — stale responses win races.
- **Missing dependencies** — the effect sees stale values; obey the lint rule or comment
  why not.
- **`async` effect callbacks** — return a promise, break cleanup.
- **Fetching in render** instead of an effect.

### Key takeaways

- `useEffect(fn, deps)` runs after render when deps change; the returned function is
  cleanup (before the next run and on unmount).
- Every async effect sets a `cancelled` flag (or a mounted ref) in cleanup and checks it
  before `setState`.
- Subscriptions and timers add in the effect, remove in cleanup; guard `window` for SSR.
- `useCallback` a loader on its inputs, then `useEffect(() => { void load(); }, [load])`.

---

## Hour 8 — Custom hooks and the rules of hooks (`useApi`, `useForm`)

### What & why

A **custom hook** is a function whose name starts with `use` and that calls other hooks. It's
how you extract stateful logic — fetch-with-loading-and-error, a form's values and
validation, a WebSocket connection — into something reusable that _feels_ like a built-in.
A hook returns whatever the caller needs (a value, an object of values and functions) and
owns its own state.

The **rules of hooks** exist because React tracks hooks by _call order_:

1. Call hooks only at the top level of a component or custom hook — never inside `if`,
   loops, or after an early return.
2. Call hooks only from components or other hooks.

The `react-hooks/rules-of-hooks` lint rule enforces both. And two companions matter for
hooks that return functions:

- **`useCallback(fn, deps)`** returns the same function identity until `deps` change — so
  a returned `refetch` can safely be an effect dependency downstream.
- **`useMemo(compute, deps)`** memoises a value the same way.

### In the wild

`useApi` — the dashboard's generic "call this fetcher, give me data/loading/error/refetch":

```tsx
// 📁 frontend/src/hooks/useApi.ts
export interface UseApiResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
  setData: (value: T | null) => void;
}

export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: DependencyList = [],
  options?: UseApiOptions,
): UseApiResult<T> {
  const { immediate = true } = options ?? {};
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(immediate);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const execute = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetcher();
      if (mountedRef.current) {
        setData(result);
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    mountedRef.current = true;
    if (immediate) {
      execute();
    }
    return () => {
      mountedRef.current = false;
    };
  }, [execute, immediate]);

  return { data, loading, error, refetch: execute, setData };
}
```

Usage: `const { data: flags, loading, refetch } = useApi(() => FeatureFlagsService.list(), []);`.
The `setData` in the result is there for local updates — a row could be changed in place after
the API confirms, without a refetch. At this release no page calls it (the flag list in Hour 16
keeps its own state, which lets it also guard against overlapping requests), so treat it as
available rather than established.

`useForm` — a generic form hook with validators per field and a submit pipeline. It is a good
example of a generic hook, but be clear what it is: **no page uses it today** (only its own
test, `frontend/src/tests/hooks/useForm.test.ts`, imports it). The one big form,
`/experiments/new`, uses a reducer instead (Hour 6's sidebar, Hour 15).

```tsx
// 📁 frontend/src/hooks/useForm.ts
export type Validator<T> = (value: unknown, allValues: T) => string | null;

export interface UseFormOptions<T extends Record<string, unknown>> {
  initialValues: T;
  validators?: Partial<Record<keyof T, Validator<T>>>;
  onSubmit: (values: T) => Promise<void> | void;
}

export function useForm<T extends Record<string, unknown>>(
  options: UseFormOptions<T>,
): UseFormResult<T> {
  const { initialValues, validators, onSubmit } = options;
  const [values, setValues] = useState<T>(initialValues);
  const [errors, setErrors] = useState<FormErrors<T>>({});
  const [touched, setTouchedState] = useState<Partial<Record<keyof T, boolean>>>({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  ...
  const handleSubmit = useCallback(
    async (e?: FormEvent) => {
      if (e) e.preventDefault();
      setSubmitError(null);

      const validationErrors = validateAll(values);
      setErrors(validationErrors);

      // Mark all as touched
      const allTouched: Partial<Record<keyof T, boolean>> = {};
      for (const key of Object.keys(values) as Array<keyof T>) {
        allTouched[key] = true;
      }
      setTouchedState(allTouched);

      if (Object.keys(validationErrors).length > 0) return;

      setIsSubmitting(true);
      try {
        await onSubmit(values);
      } catch (err) {
        setSubmitError(err instanceof Error ? err.message : String(err));
      } finally {
        setIsSubmitting(false);
      }
    },
    [values, validateAll, onSubmit],
  );
```

`e.preventDefault()` stops the browser's native form submission; `touched` lets the UI show
errors only for fields the user has visited (or all, on submit); `submitError` captures the
server's message (`ApiError.message` — which for a 422 is the Pydantic sentence).

The WebSocket hook's public surface — state plus stable action functions:

```tsx
// 📁 frontend/src/hooks/useExperimentStream.ts
export interface UseExperimentStreamResult {
  snapshot: ExperimentSnapshot | null;
  status: ConnectionStatus;
  error: string | null;
  connect: () => void;
  disconnect: () => void;
  refresh: () => void;
  ping: () => void;
}
```

`useCallback` so a returned function is stable for consumers' effects, and the context
hook that throws when used outside its provider (Hour 9):

```tsx
// 📁 sdk/react/src/context/ExperimentationProvider.tsx
/** Access the SDK client and current user. Must be used inside ExperimentationProvider. */
export function useExperimentation(): ExperimentationContextValue {
  const ctx = useContext(ExperimentationContext);
  if (!ctx) {
    throw new Error('useExperimentation must be used within ExperimentationProvider');
  }
  return ctx;
}
```

A thin hook composed from another — the smallest custom hook in the repo:

```tsx
// 📁 sdk/react/src/hooks/useVariant.ts
export function useVariant(flagKey: string): string | null {
  const { variant } = useFeatureFlag(flagKey);
  return variant;
}
```

> **Watch Out!** A hook called after an early return — `if (!id) return null; const { data }
> = useApi(...)` — changes the number of hooks between renders and React throws "Rendered
> fewer hooks than expected." Hooks first, returns after. The page in Hour 5 declares all
> seven `useState`s and the `useCallback`/`useEffect` before its first `return`.

> **Did You Know?** `useCallback(fn, deps)` with the _caller's_ `deps` (as `useApi` does)
> is a deliberate trade: the hook can't know what `fetcher` closes over, so it trusts the
> caller's list, the way `useEffect` does. The lint rule is disabled on that line with a
> comment; that's the only acceptable form of disabling it.

### Try It Yourself

Write `useToggle` and `useDebouncedValue` in a scratch file:

```tsx
export function useToggle(initial = false): [boolean, () => void] {
  const [on, setOn] = React.useState(initial);
  const toggle = React.useCallback(() => setOn((v) => !v), []);
  return [on, toggle];
}

export function useDebouncedValue<T>(value: T, ms = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}
```

Use `useDebouncedValue(search)` in the experiments list to delay the `search` query; then
move `useToggle` inside an `if` and read the lint error.

### Common mistakes

- **Hooks inside conditions/loops/after returns.**
- **Returning fresh functions without `useCallback`** when consumers put them in effect
  deps — infinite loops.
- **Duplicating `useApi`'s logic in a page.** Use the hook.
- **A hook that doesn't start with `use`** — the lint rule can't check it.

### Key takeaways

- Custom hooks extract stateful logic; they own state and return what callers need.
- Rules: top level only, from components/hooks only; call order is identity.
- `useCallback`/`useMemo` for stable identities; `useRef` for mutable non-render state.
- `useApi` (fetch + loading + error + refetch + setData) and `useExperimentStream` (socket) are
  the house hooks; `useForm` (values, errors, touched, submit pipeline) is available but unused
  by any page.

---

## Hour 9 — Context (`AuthContext`, `NotificationContext`)

### What & why

**Context** delivers a value to every component below a provider without passing it through
each level's props. `createContext<T | undefined>(undefined)` makes a context; a
`<XProvider>` component holds the state and renders `<XContext.Provider value={...}>`; a
`useX()` hook reads it with `useContext` and **throws** if there's no provider — a
programming error caught at the first render, not a `undefined is not a function` later.

Use context for app-wide concerns that many components need and few change: the current
user, toasts, which optional modules this server has. Don't use it for data one page owns —
that's state (Hour 6) or a service call. `_app.tsx` mounts three providers once, around every
page: `ModulesProvider` (what `GET /api/v1/modules` says is installed), `AuthProvider` and
`NotificationProvider`. A fourth, `WorkspaceProvider`, belongs to the optional workspaces
module (`modules/frontend/src/contexts/WorkspaceContext.tsx`) and is mounted by that module's
own pages, not by `_app`.

#### Why the auth context never trusts the stored token

Only the token is persisted (`localStorage["experimently.token"]`). On mount, the provider
calls `/auth/me`; a 401 clears the token; the user object is _always_ derived from the
server. A revoked or expired token can't resurrect a session, and a stale user object can't
outlive its permissions.

### In the wild

The auth context's contract and provider:

```tsx
// 📁 frontend/src/contexts/AuthContext.tsx
export type AuthStatus = 'loading' | 'authenticated' | 'anonymous';

export interface AuthContextValue {
  user: UserMe | null;
  status: AuthStatus;
  /** Convenience: `status === 'authenticated'`. */
  isAuthenticated: boolean;
  /** `POST /api/v1/auth/login`; stores the token and resolves with the user. */
  login: (email: string, password: string) => Promise<UserMe>;
  /** `POST /api/v1/auth/logout` (best effort), then clears the token. */
  logout: () => Promise<void>;
  /** Re-fetch `/auth/me` (e.g. after a profile change). */
  refresh: () => Promise<UserMe | null>;
  /** True when the current user has one of the given roles. */
  hasRole: (...roles: Role[]) => boolean;
  /**
   * Why a stored session could not be restored, when the reason was not a
   * rejected token: a server error or an unreachable API. The login page shows
   * it, so the redirect there is not silent. `null` otherwise.
   */
  sessionError: string | null;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

/**
 * Only the bearer token is persisted (`localStorage["experimently.token"]`).
 * The user object is always re-derived from `GET /api/v1/auth/me` on mount so
 * a stale or revoked token can never resurrect a session.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserMe | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [sessionError, setSessionError] = useState<string | null>(null);
  const mounted = useRef(true);

  const applyAnonymous = useCallback(() => {
    if (!mounted.current) return;
    setUser(null);
    setStatus('anonymous');
  }, []);

  const loadMe = useCallback(async (): Promise<UserMe | null> => {
    if (!getToken()) {
      applyAnonymous();
      return null;
    }
    try {
      const me = await apiFetch<UserMe>(ME_PATH, { redirectOn401: false });
      if (mounted.current) {
        setUser(me);
        setStatus('authenticated');
        setSessionError(null);
      }
      return me;
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        // apiFetch already cleared the token.
        applyAnonymous();
        return null;
      }
      // API unreachable or 5xx: keep the token for the next reload but treat
      // the session as anonymous so protected pages fall back to /login, and
      // say why there.
      if (mounted.current) setSessionError(sessionErrorMessage(err));
      applyAnonymous();
      return null;
    }
  }, [applyAnonymous]);

  useEffect(() => {
    mounted.current = true;
    void loadMe();
    return () => {
      mounted.current = false;
    };
  }, [loadMe]);
  ...
  const login = useCallback(async (email: string, password: string): Promise<UserMe> => {
    const result = await apiFetch<LoginResponse>(LOGIN_PATH, {
      method: 'POST',
      json: { email, password },
      auth: false,
    });
    setToken(result.access_token);
    if (mounted.current) {
      setUser(result.user);
      setStatus('authenticated');
      setSessionError(null);
    }
    return result.user;
  }, []);
```

Three states, not a boolean: `loading` (we don't know yet), `authenticated`, `anonymous`. A
boolean would force protected pages to redirect during the initial `/auth/me` — the
`loading` state is what lets `RequireAuth` show a spinner instead. `redirectOn401: false`
on the `/auth/me` call because the provider handles a 401 itself; `auth: false` on login
because there's no token yet.

A 401 and a 5xx end the same way (anonymous, sent to `/login`) but for different reasons, and
the user deserves to know which. `sessionError` carries the second kind:
`sessionErrorMessage(err)` turns an unreachable API into `unreachableMessage()` and a 5xx into
"Signed out because the server returned an error (HTTP 503). Request ID: ...", and the login
page shows it. A rejected token sets nothing: being asked to sign in again explains itself.

The value is assembled with `useMemo` so consumers don't re-render on every provider
render, and the hook throws without a provider:

```tsx
// 📁 frontend/src/contexts/AuthContext.tsx (shape)
  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      status,
      isAuthenticated: status === 'authenticated',
      login,
      logout,
      refresh: loadMe,
      hasRole,
      sessionError,
    }),
    [user, status, login, logout, loadMe, hasRole, sessionError],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
}

export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext) ?? null;
}
```

`useOptionalAuth()` is the escape hatch: it returns `null` instead of throwing, for components
that must also render where no provider exists (the experiments list and `/experiments/new`
are rendered bare in some tests). A missing provider is then a value to handle, not a crash.

The toast context — small state, two stable functions, a ref for timers:

```tsx
// 📁 frontend/src/contexts/NotificationContext.tsx
interface NotificationContextValue {
  toasts: Toast[];
  addToast: (message: string, variant?: ToastVariant, duration?: number) => void;
  removeToast: (id: string) => void;
}

const NotificationContext = createContext<NotificationContextValue | undefined>(undefined);

let toastCounter = 0;

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const removeToast = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (message: string, variant: ToastVariant = 'info', duration = 5000) => {
      toastCounter += 1;
      const id = `toast-${toastCounter}`;
      const toast: Toast = { id, message, variant, duration };
      setToasts((prev) => [...prev, toast]);

      if (duration > 0) {
        const timer = setTimeout(() => {
          removeToast(id);
        }, duration);
        timersRef.current.set(id, timer);
      }
    },
    [removeToast],
  );

  return (
    <NotificationContext.Provider value={{ toasts, addToast, removeToast }}>
      {children}
    </NotificationContext.Provider>
  );
}
```

Consumers read what they need — the nav reads `status` and `user`; the detail page reads
`user` to decide which buttons to offer (by role) and how to word the note it shows instead
(by ownership):

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
  const { user } = useAuth();
  ...
  const mayChange = canChangeExperiment(user);
  ...
  // A null owner (the creator's account was removed) is nobody, so it is
  // never you.
  const isOwner =
    user !== null && experiment.owner_id !== null && user.id === experiment.owner_id;
```

Ownership labels; it does not grant. Notice the null check: `owner_id` is `string | null` (Hour 1), and
a removed creator must never compare equal to anyone. Access to experiments and flags is by role (Hour 16).

And the providers are mounted once, in order, around every page:

```tsx
// 📁 frontend/src/pages/_app.tsx
      <ModulesProvider>
        <AuthProvider>
          <NotificationProvider>
            {page}
            <ToastContainer />
          </NotificationProvider>
        </AuthProvider>
      </ModulesProvider>
```

`ModulesProvider` is outermost because nothing in it depends on the user: it asks
`GET /api/v1/modules` once, caches the answer for five minutes, and **treats any failure as the
core profile**, so a dashboard that cannot reach its backend never shows module menus it cannot
back. Its hook, `useModules()`, returns that same core default when there is no provider at
all — the opposite choice from `useAuth()`, which throws. Both are deliberate: rendering the
shell without module chrome is always safe, rendering a page without knowing the user is not.

The React SDK's provider shows the memoisation rule for a context whose value is an object —
recreate only when the primitives that matter change:

```tsx
// 📁 sdk/react/src/context/ExperimentationProvider.tsx
export function ExperimentationProvider({ config, user, children }: ProviderProps) {
  // Stabilise the client — only recreate when a config primitive changes, so
  // callers may pass an inline config object without losing the cache.
  const client = useMemo(
    () => new ExperimentationClient(config),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [config.apiKey, config.baseUrl, config.timeoutMs, config.cacheTtlMs]
  );

  // Stabilise the user reference by content so hooks only re-run when the
  // identity or attributes actually change.
  const attributesKey = JSON.stringify(user.attributes ?? null);
  const value = useMemo(
    () => ({ client, user }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [client, user.userId, attributesKey]
  );
```

Without that, an inline `user={{ userId, attributes }}` in a parent would be a new object
every render, every hook depending on `user` would re-run, and every experiment would be
re-assigned on every keystroke.

> **Watch Out!** A context value that's a fresh object each render (`value={{ toasts,
> addToast, removeToast }}` without memoisation) re-renders every consumer on every provider
> render. It's acceptable for the toast provider (it renders rarely, consumers are few); the
> auth and SDK providers memoise because they're read everywhere. Know which you're writing.

> **Did You Know?** The tests mock `useAuth` wholesale — `jest.mock('@/contexts/AuthContext',
> () => ({ useAuth: () => mockUseAuth() }))` — so a page test can say "as an admin" or "as a
> viewer" in one line, without rendering the provider or hitting `/auth/me`. That's a benefit
> of the `useX()` hook shape: the hook is the seam.

### Try It Yourself

Build a `ThemeContext` in a scratch file — provider with `useState<'light' | 'dark'>`, a
memoised value `{ theme, toggle }`, a `useTheme()` that throws without a provider — and use it
from a button. Then call `useTheme()` in a component _outside_ the provider and read the
thrown message. Finally, open `frontend/src/tests/pages/experiment-detail.test.tsx` and find
how `mockUseAuth` signs in an ADMIN vs an ANALYST, and an owner vs a non-owner. Which test
proves that an ANALYST who owns an experiment still gets no lifecycle button?

### Common mistakes

- **Context for page-local data.** State or a service call.
- **A hook that returns `undefined` outside its provider** instead of throwing — unless, like
  `useModules()` or `useOptionalAuth()`, a safe default is the documented contract.
- **Unmemoised object values** on hot providers — every consumer re-renders.
- **Persisting the user object** instead of re-deriving it from the API.

### Key takeaways

- `createContext<T | undefined>` + a provider that owns state + a `useX()` that throws without
  a provider.
- Auth: three states (`loading | authenticated | anonymous`), token-only persistence, user
  re-derived from `/auth/me`, cross-tab sync, `hasRole`, and `sessionError` so a failed
  restore is never a silent sign-out.
- Notifications: `addToast`/`removeToast` with timers in a ref.
- Memoise provider values (and the SDK client) on the primitives that matter; tests mock the
  `useX()` hook.

---

## Part II Workshop

**Q1. Why does `FlagToggle` take `on` and `onChange` rather than holding its own state?**
It's a controlled primitive: the parent owns the value (it came from the API) and the API
call; the toggle only describes a switch. That keeps it reusable in the list and the detail
header and lets tests assert on the callback.

**Q2. `setVariants(variants.push(v))` — what's wrong?**
Two things: `push` mutates the existing array (same identity → no re-render) and returns the
new _length_, so state becomes a number. Use `setVariants([...variants, v])`.

**Q3. What does the `cancelled` flag in `useExperiment`'s effect prevent?**
A response for a superseded key (or an unmounted component) overwriting current state. The
cleanup sets it before the next run, and the `.then` checks it.

**Q4. Why can't a `useEffect` callback be `async`?**
It would return a promise, and React expects either nothing or a cleanup function. Define an
async function and call it with `void`, or use a memoised loader.

**Q5. Why does `AuthContext` have three statuses instead of `isAuthenticated: boolean`?**
`loading` distinguishes "not yet known" from "anonymous," so `RequireAuth` can show a spinner
during the initial `/auth/me` instead of redirecting every hard refresh to `/login`.

**Q6. Why does `ExperimentationProvider` memoise `user` on `userId` + a JSON string of
attributes?**
So an inline `user={{...}}` object in the parent doesn't count as "the user changed" on every
render — which would re-run every `useExperiment`/`useFeatureFlag` effect and re-assign on
each keystroke.

**Q7. `/experiments/new` replaced nine `useState`s with one `useReducer`. What does the
reducer give the page that the `useState`s did not?**
One copy of the form that two views (the guided wizard and the single-page form) both read
and dispatch to, so switching views keeps every answer; a pure `experimentFormReducer` that
tests can call without rendering anything; and child editors that only say what happened
(`dispatch({ type: 'removeVariant', index })`) without knowing the state's shape.

**Quiz.** Name the two rules of hooks and the lint rule that enforces them.
→ Top level only (no conditions/loops/after returns); from components or custom hooks only.
`react-hooks/rules-of-hooks` (with `react-hooks/exhaustive-deps` for dependency lists).

---

## Exercises

**E2.1 — A controlled primitive.** Write `PercentInput({ value, onChange, label })` that
renders a labelled number input clamped to 0–100 on change, in the `FlagToggle` style
(controlled, accessible, `data-testid`).

**E2.2 — Immutable updates.** Given `stages: { name: string; pct: number }[]` in state, write
handlers to add a stage with `pct` equal to the previous stage's, remove by index, and
"normalise" so the last stage becomes 100 — all without mutation.

**E2.3 — A cancelled effect.** Write `useFlag(key: string)` that calls
`FeatureFlagsService.get(key)` on mount and whenever `key` changes, with loading/error state
and the cancelled-flag discipline.

**E2.4 — A context.** Write `WorkspaceContext` with a provider holding `workspaceId:
string | null` (persisted to `localStorage`), a memoised value `{ workspaceId, select(id) }`,
and a `useWorkspace()` that throws without a provider.

**E2.5 — Find the bugs.** Three problems:

```tsx
function Row({ flag }: { flag: Flag }) {
  const [on, setOn] = useState(flag.enabled);
  if (!flag.key) return null;
  const { addToast } = useNotifications();
  useEffect(async () => { await FeatureFlagsService.get(flag.key); }, []);
  return <FlagToggle on={on} onChange={() => { flag.enabled = !on; setOn(flag.enabled); addToast('ok'); }} label={flag.key} />;
}
```

---

## Solutions

**S2.1**

```tsx
interface PercentInputProps { value: number; onChange: (next: number) => void; label: string; 'data-testid'?: string }

export function PercentInput({ value, onChange, label, ...rest }: PercentInputProps) {
  const id = React.useId();
  return (
    <label htmlFor={id} className="flex items-center gap-2 text-sm">
      {label}
      <input
        id={id}
        type="number"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(Math.max(0, Math.min(100, Number(e.target.value) || 0)))}
        data-testid={rest['data-testid'] ?? 'percent-input'}
        className="w-20 rounded border border-slate-300 px-2 py-1"
      />
    </label>
  );
}
```

**S2.2**

```tsx
const addStage = () =>
  setStages((prev) => [...prev, { name: `Stage ${prev.length + 1}`, pct: prev[prev.length - 1]?.pct ?? 0 }]);
const removeStage = (index: number) => setStages((prev) => prev.filter((_, i) => i !== index));
const normalise = () =>
  setStages((prev) => prev.map((s, i) => (i === prev.length - 1 ? { ...s, pct: 100 } : s)));
```

**S2.3**

```tsx
export function useFlag(key: string) {
  const [flag, setFlag] = useState<FeatureFlag | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    FeatureFlagsService.get(key)
      .then((f) => { if (!cancelled) setFlag(f); })
      .catch((err: unknown) => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [key]);

  return { flag, loading, error };
}
```

**S2.4**

```tsx
interface WorkspaceValue { workspaceId: string | null; select: (id: string | null) => void }
const WorkspaceContext = createContext<WorkspaceValue | undefined>(undefined);
const KEY = 'controlvar.workspace';

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [workspaceId, setWorkspaceId] = useState<string | null>(() => {
    try { return typeof window === 'undefined' ? null : window.localStorage.getItem(KEY); } catch { return null; }
  });
  const select = useCallback((id: string | null) => {
    setWorkspaceId(id);
    try { id ? window.localStorage.setItem(KEY, id) : window.localStorage.removeItem(KEY); } catch { /* ignore */ }
  }, []);
  const value = useMemo(() => ({ workspaceId, select }), [workspaceId, select]);
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error('useWorkspace must be used within WorkspaceProvider');
  return ctx;
}
```

**S2.5** — (1) `useNotifications()` is called after an early return — a rules-of-hooks
violation (hook count changes when `flag.key` is falsy); move the return below all hooks.
(2) `useEffect(async () => ...)` returns a promise — wrap in a sync callback with `void`, and
add the cancelled flag; also the deps `[]` ignore `flag.key`. (3) `flag.enabled = !on`
mutates a prop; the row should either be controlled by the parent (`on={flag.enabled}`,
`onChange` calls the parent) or keep its own state without touching the prop —
`setOn((v) => !v)`.

---

**Next:** [Part III — Next.js →](./part-3-nextjs.md)

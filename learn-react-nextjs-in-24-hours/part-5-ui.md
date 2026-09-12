# Part V — UI

### Hours 18–20

[← Part IV](./part-4-data-and-forms.md) · [Contents](./README.md) · [Part VI →](./part-6-production.md)

Three hours on what the user sees. Tailwind — utility classes as the design system, and the
few tokens the dashboard adds (Hour 18); the targeting rule builder — the most complex piece
of client state, shaped exactly like the JSON the backend expects (Hour 19); and charts with
Recharts, including the mocking trick that lets 550 Jest tests run without a canvas (Hour 20).

---

## Hour 18 — Tailwind and the design tokens

### What & why

**Tailwind** is a utility-class CSS framework: instead of writing a `.card` rule, you compose
`rounded-lg border border-slate-200 bg-white p-6` on the element. The classes are generated
from a config and purged to what you actually use, so the shipped CSS is small; the design
system lives in the config's tokens (colours, spacing scale, fonts) rather than in a separate
stylesheet.

Why a utility framework for a dashboard? Because a dashboard is mostly layout — tables,
cards, forms, badges — and utilities make layout local and readable: the element's
appearance is in the element, there's no cascade to reason about, and deleting a component
deletes its styling. The costs — long `className` strings, a learning curve for the names —
are real and accepted. Three conventions keep it legible here:

1. **Class lists as arrays joined with `' '`** when they're conditional, so each concern is
   one line.
2. **Small named constants** for repeated input/button classes within a page
   (`inputClass`, `smallInputClass`).
3. **`data-*` attributes for state** (`data-state="on"`, `data-is-control`) so tests and
   the scenario runner read state without parsing class names.

### In the wild

The config — content globs (what to scan for classes), one extended colour scale, one font,
one plugin:

```js
// 📁 frontend/tailwind.config.js
module.exports = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#eff6ff',
          100: '#dbeafe',
          ...
          600: '#2563eb',
          700: '#1d4ed8',
          ...
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}
```

`extend` adds to Tailwind's defaults rather than replacing them; `primary` is a blue scale
(mostly used as `blue-600` in practice — the dashboard leans on the default palette:
`slate` for chrome, `blue` for primary actions, `green`/`red`/`amber` for status). The
`typography` plugin provides `prose` classes for the markdown docs pages.

Conditional class composition, one concern per line:

```tsx
// 📁 frontend/src/components/feature-flags/FlagToggle.tsx
      className={[
        'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors',
        'focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        on ? 'bg-green-600' : 'bg-slate-300',
      ].join(' ')}
```

Line 1 is layout, line 2 is focus (keyboard users get a ring; mouse users don't —
`focus-visible`), line 3 is the disabled state, line 4 is the on/off colour. A `data-state`
attribute carries the state for tests.

Named class constants for a form page, and a colour table typed by the status union:

```tsx
// 📁 frontend/src/pages/experiments/new.tsx
const inputClass =
  'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400';
const smallInputClass =
  'rounded border border-slate-300 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400';
```

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
const ACTION_META: Record<LifecycleAction, { label: string; pending: string; className: string; confirm?: string }> = {
  start: { label: 'Start', pending: 'Starting…', className: 'bg-green-600 text-white hover:bg-green-700' },
  pause: { label: 'Pause', pending: 'Pausing…', className: 'bg-white text-slate-700 border border-slate-300 hover:bg-slate-50' },
  ...
};
```

Layout: a page shell with a max width, responsive padding (`px-4 sm:px-6 lg:px-8`), and
vertical rhythm (`space-y-6`); a loading skeleton built from grey boxes and `animate-pulse`:

```tsx
// 📁 frontend/src/pages/experiments/[id].tsx
    <div className="flex-1 bg-slate-50" data-testid="experiment-detail">
      <PageTitle title={experiment.name} />
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
```

```tsx
// 📁 frontend/src/components/results/ResultsDashboard/ResultsDashboard.tsx
function LoadingSkeleton() {
  return (
    <div className="space-y-6 animate-pulse" data-testid="loading-skeleton">
      <div className="h-36 bg-slate-200 rounded-xl" />
      <div className="h-8 bg-slate-200 rounded w-64" />
      <div className="h-72 bg-slate-200 rounded-xl" />
      <div className="h-48 bg-slate-200 rounded-xl" />
    </div>
  );
}
```

Responsive prefixes (`sm:`, `md:`, `lg:`) are mobile-first: `hidden md:flex` in the shell's
nav means "hidden by default, flex from the medium breakpoint" — the desktop nav — while a
separate mobile menu handles small screens. The empty state of the rule builder uses a
dashed border and centred text — a pattern reused for every "nothing here yet":

```tsx
// 📁 frontend/src/components/targeting/TargetingRuleBuilder.tsx
      {rules.groups.length === 0 && (
        <div className="rounded-lg border-2 border-dashed border-slate-200 p-6 text-center">
          <p className="text-sm text-slate-500 mb-3">
            No targeting rules — all users will match
          </p>
```

> **Watch Out!** Tailwind only generates classes it can _see_ as complete strings in the
> scanned files. `` `bg-${colour}-600` `` produces nothing — the purge can't find
> `bg-green-600`. Build full class names (`on ? 'bg-green-600' : 'bg-slate-300'`) or look them
> up from a table of complete strings (`ACTION_META[action].className`).

> **Did You Know?** Accessibility is in the classes and attributes together: `sr-only`
> renders text for screen readers only (`<span className="sr-only">Loading…</span>`),
> `role="status"`/`aria-live="polite"` announce the spinner, `role="switch"`/`aria-checked`
> make the toggle a real switch, `aria-current="page"` marks the active nav item, and the
> shell's first element is a skip link. The nightly axe audit checks these on ten pages.

### Try It Yourself

Restyle the `StatusBadge` from Hour 5 with a `Record<Status, string>` of _complete_ class
strings, add `focus-visible:ring-2` to a button, and confirm with the browser's inspector
that the ring appears on Tab but not on click. Then break purging on purpose with a template
literal and watch the colour vanish in `npm run build`'s output.

### Common mistakes

- **Dynamic class fragments** — purged away.
- **One giant `className` string** for conditional styling — use an array with one concern
  per line.
- **Styling state with classes only** — add `data-*` for tests.
- **`focus:` instead of `focus-visible:`** — rings on every click.

### Key takeaways

- Utilities in the element; tokens in `tailwind.config.js` (`primary`, Inter, `typography`).
- Arrays-joined class lists, named constants per page, complete class names only.
- `data-state`/`data-is-control` for tests; `sr-only`, roles and `aria-*` for a11y.
- Mobile-first responsive prefixes; skeletons with `animate-pulse`.

---

## Hour 19 — The targeting rule builder: state shaped like the API

### What & why

The rule builder is the dashboard's most complex client state: a tree of groups of
conditions, each condition with an attribute, an operator whose validity depends on the
attribute's type, and a value whose input depends on the operator. Three decisions make it
tractable:

1. **The state is the API shape.** `TargetingRules` in `types/targeting.ts` is
   `{ logical_operator, groups: [{ logical_operator, conditions: [{ attribute, operator,
   value }] }] }` — the exact "dashboard shape" the backend's `targeting_adapter` accepts.
   Client-only fields (`id` on groups and conditions, for React keys) are stripped by
   `rulesToJson` on the way out. No transformation layer, no drift.
2. **Controlled all the way down.** `TargetingRuleBuilder` takes `value` and `onChange`;
   it renders `RuleGroupCard`s that take a group and `onChange`; they render
   `ConditionRow`s that take a condition and `onChange`. Every edit is an immutable update
   bubbled to the page, which holds the one copy of the tree in `useState`.
3. **Operators are derived from attribute type.** `OPERATORS_BY_TYPE` maps `string | number
   | boolean | array | semver` to allowed operators; changing the attribute re-validates the
   operator; changing to a no-value operator clears the value.

### In the wild

The types — the API's shape plus client ids:

```typescript
// 📁 frontend/src/types/targeting.ts
export type OperatorType =
  | 'equals' | 'not_equals' | 'contains' | 'not_contains'
  | 'starts_with' | 'ends_with' | 'greater_than' | 'less_than'
  | 'greater_than_or_equal' | 'less_than_or_equal'
  | 'in' | 'not_in' | 'regex' | 'is_null' | 'is_not_null'
  | 'semver_eq' | 'semver_gt' | 'semver_lt' | 'semver_gte' | 'semver_lte'
  | 'geo_within_radius' | 'time_window' | 'array_contains' | 'array_intersects';

export type LogicalOperator = 'AND' | 'OR';

export interface TargetingCondition {
  id: string;
  attribute: string;
  operator: OperatorType;
  value: string | number | boolean | string[] | null;
}

export interface TargetingRuleGroup {
  id: string;
  logical_operator: LogicalOperator;
  conditions: TargetingCondition[];
}

export interface TargetingRules {
  logical_operator: LogicalOperator;
  groups: TargetingRuleGroup[];
}

export const OPERATORS_BY_TYPE: Record<string, OperatorType[]> = {
  string: ['equals', 'not_equals', 'contains', 'not_contains', 'starts_with', 'ends_with', 'regex', 'in', 'not_in', 'is_null', 'is_not_null'],
  number: ['equals', 'not_equals', 'greater_than', 'less_than', 'greater_than_or_equal', 'less_than_or_equal', 'in', 'not_in', 'is_null', 'is_not_null'],
  boolean: ['equals', 'not_equals', 'is_null', 'is_not_null'],
  array: ['array_contains', 'array_intersects', 'is_null', 'is_not_null'],
  semver: ['semver_eq', 'semver_gt', 'semver_lt', 'semver_gte', 'semver_lte'],
};
```

That `OperatorType` union is the same list as the backend adapter's `_SIMPLE_OPERATOR_MAP` +
`_SEMVER_OPERATOR_MAP` (Build book, Day 16). Add an operator on one side without the other
and the flag falls back to its global rollout — the mirror discipline again, with a real
failure mode.

The root component — derives a display total, updates immutably, delegates to cards:

```tsx
// 📁 frontend/src/components/targeting/TargetingRuleBuilder.tsx
export function TargetingRuleBuilder({ value, onChange, readOnly = false, className = '' }: TargetingRuleBuilderProps) {
  const rules = value ?? createEmptyRules();

  const totalConditions = rules.groups.reduce((sum, group) => sum + group.conditions.length, 0);

  const handleRootLogicalOperatorChange = (op: LogicalOperator) => {
    onChange({ ...rules, logical_operator: op });
  };

  const handleAddGroup = () => {
    onChange({ ...rules, groups: [...rules.groups, createEmptyGroup()] });
  };

  const handleGroupChange = (index: number, updated: TargetingRuleGroup) => {
    const newGroups = rules.groups.map((g, i) => (i === index ? updated : g));
    onChange({ ...rules, groups: newGroups });
  };

  const handleRemoveGroup = (index: number) => {
    const newGroups = rules.groups.filter((_, i) => i !== index);
    onChange({ ...rules, groups: newGroups });
  };
```

Every handler is `onChange({ ...rules, ... })` — spread the tree, replace one branch. The
page's `setRules` receives the new tree; nothing is mutated.

The leaf — a controlled row whose operator list depends on the attribute and whose value
input depends on the operator:

```tsx
// 📁 frontend/src/components/targeting/ConditionRow.tsx
const NO_VALUE_OPERATORS: OperatorType[] = ['is_null', 'is_not_null'];
const MULTI_VALUE_OPERATORS: OperatorType[] = ['in', 'not_in'];

export function ConditionRow({ condition, onChange, onRemove, readOnly = false }: ConditionRowProps) {
  const availableOperators = getOperatorsForAttribute(condition.attribute);
  const showValueInput = !NO_VALUE_OPERATORS.includes(condition.operator);
  const isMultiValue = MULTI_VALUE_OPERATORS.includes(condition.operator);

  const handleAttributeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newAttribute = e.target.value;
    const newOperators = getOperatorsForAttribute(newAttribute);
    // If current operator is not in new operator list, reset to first available
    const newOperator = newOperators.includes(condition.operator)
      ? condition.operator
      : newOperators[0];
    onChange({ ...condition, attribute: newAttribute, operator: newOperator });
  };

  const handleOperatorChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newOperator = e.target.value as OperatorType;
    const newValue = NO_VALUE_OPERATORS.includes(newOperator) ? null : condition.value;
    onChange({ ...condition, operator: newOperator, value: newValue });
  };
```

Read `handleAttributeChange`: changing `country` (string) to `app.version` (semver) checks
whether the current operator (`equals`) is valid for the new type; it isn't, so the operator
resets to `semver_eq`. The invariant "operator is valid for attribute type" is maintained by
the handler, not by a validator after the fact. `e.target.value as OperatorType` is a
justified assertion — the `<select>`'s options are built from `OperatorType` values.

Attribute suggestions via a `<datalist>` — native autocomplete without a library:

```tsx
// 📁 frontend/src/components/targeting/ConditionRow.tsx
  const datalistId = `attr-suggestions-${condition.id}`;
  ...
      <input
        data-testid="condition-attribute"
        type="text"
        list={datalistId}
        value={condition.attribute}
        onChange={handleAttributeChange}
        disabled={readOnly}
        placeholder="attribute"
```

```typescript
// 📁 frontend/src/types/targeting.ts
export const COMMON_ATTRIBUTES: AttributeSuggestion[] = [
  { value: 'user.country', label: 'User Country', type: 'string', examples: ['US', 'UK', 'DE'] },
  { value: 'user.plan', label: 'User Plan', type: 'string', examples: ['free', 'pro', 'enterprise'] },
  { value: 'app.version', label: 'App Version', type: 'semver', examples: ['1.0.0', '2.3.1'] },
  ...
];
```

(The suggestions use the `user.`/`app.` prefixes the backend's `expand_context` aliases, so
a rule written as `user.country` matches an SDK that sent `{"country": "US"}`.)

Validation and serialisation — errors as a list of human sentences, and ids stripped for the
API:

```typescript
// 📁 frontend/src/utils/targeting.ts
export function validateRules(rules: TargetingRules): ValidationResult {
  const errors: string[] = [];

  for (let gi = 0; gi < rules.groups.length; gi++) {
    const group = rules.groups[gi];
    for (let ci = 0; ci < group.conditions.length; ci++) {
      const condition = group.conditions[ci];
      const condLabel = `Group ${gi + 1}, Condition ${ci + 1}`;

      if (!condition.attribute || condition.attribute.trim() === '') {
        errors.push(`${condLabel}: attribute is required`);
      }
      ...
      // Value is required unless operator is is_null or is_not_null
      const noValueOperators: OperatorType[] = ['is_null', 'is_not_null'];
      if (!noValueOperators.includes(condition.operator)) {
        if (condition.value === null || condition.value === undefined || condition.value === '') {
          errors.push(`${condLabel}: value is required for operator "${condition.operator}"`);
        }
      }
    }
  }

  return { valid: errors.length === 0, errors };
}

export function rulesToJson(rules: TargetingRules): RulesJson {
  return {
    logical_operator: rules.logical_operator,
    groups: rules.groups.map((group) => ({
      logical_operator: group.logical_operator,
      conditions: group.conditions.map(({ attribute, operator, value }) => ({ attribute, operator, value })),
    })),
  };
}
```

`createEmptyCondition`/`createEmptyGroup` mint client ids with a counter plus timestamp —
deliberately not `crypto.randomUUID()`, so the same code runs in Jest's Node environment:

```typescript
// 📁 frontend/src/utils/targeting.ts
// Simple counter-based ID generator that works in both browser and Node test environments
let _idCounter = 0;
function generateId(): string {
  _idCounter += 1;
  const timestamp = Date.now();
  return `id-${timestamp}-${_idCounter}`;
}
```

> **Watch Out!** The `id` fields exist for React `key`s and stable `datalist` ids; they must
> never reach the API (the backend ignores unknown keys today, but the contract is the three
> documented fields). `rulesToJson` strips them — use it, or `rules as unknown as
> Record<string, unknown>` as the experiment form does only because the backend tolerates
> extras. Prefer `rulesToJson`.

> **Did You Know?** "No targeting rules — all users will match" in the empty state is
> literally the backend's semantics: an experiment or flag with `targeting_rules: null`
> skips the eligibility check, and a rule group with no conditions evaluates to `True`.
> The builder's copy was written from the rules engine's docstring.

### Try It Yourself

Build a rule in the dashboard's flag editor — `app.version semver_gte 3.2.1 AND user.country in
US,DE` — save it, then read it back from the API and compare with `rulesToJson`'s output:

```bash
curl -s $API/feature-flags/$FID -H "Authorization: Bearer $TOKEN" | python -c 'import sys,json; print(json.dumps(json.load(sys.stdin)["targeting_rules"], indent=2))'
```

Then change the attribute from `app.version` to `user.plan` in the UI and watch the
operator reset from `semver_gte` to `equals`.

### Common mistakes

- **Storing rules in a client-only shape and converting on save.** Keep the API shape; strip
  ids.
- **Letting an invalid attribute/operator pair persist** — reset in the handler.
- **`Math.random()`/`crypto` ids** that break in tests or SSR.
- **Adding an operator to the union without the backend's map** (or vice versa).

### Key takeaways

- The tree state _is_ the dashboard rule shape; ids are client-only and stripped by
  `rulesToJson`.
- Controlled all the way down; every edit is a spread-and-replace bubbled to one `useState`.
- Operators derive from attribute type (`OPERATORS_BY_TYPE`); handlers maintain the
  invariant; no-value operators clear the value.
- The operator union mirrors the backend adapter's map — keep them in sync.

---

## Hour 20 — Charts with Recharts (and mocking them)

### What & why

**Recharts** is a declarative charting library: a chart is a tree of components
(`<BarChart data={...}><XAxis/><YAxis/><Bar dataKey="value"/></BarChart>`) inside a
`<ResponsiveContainer>` that sizes to its parent. It fits React's model — data in, JSX out —
and the dashboard uses it for the conversion-rate bars, the daily/cumulative trend lines and
the sequential-testing trajectory.

Two engineering decisions around it:

1. **Chart data is derived in the component from typed props.** `variants.map(v => ({ name:
   v.variant_name, value: v.mean, isControl: v.is_control, fill: ... }))` — the chart never
   sees API types directly, and the mapping is where control gets its colour.
2. **Recharts is mocked in Jest.** It renders SVG via layout measurements jsdom can't
   provide, so `jest.config.js` maps `recharts` to `src/__mocks__/recharts.tsx`, where each
   chart component is a `<div data-testid=...>` that exposes the props tests care about. A
   page test can assert "three bars, one marked control" without a browser.

### In the wild

The conversion chart — derive data, build one `Bar` per variant (so tests can find each),
format axes:

```tsx
// 📁 frontend/src/components/results/Visualizations/ConversionChart.tsx
const CONTROL_COLOR = '#3b82f6'; // blue-500
const VARIANT_COLORS = ['#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];

export function ConversionChart({ variants }: ConversionChartProps) {
  if (!variants.length) {
    return (
      <div className="flex items-center justify-center h-48 text-slate-500">
        No variant data available
      </div>
    );
  }

  // Build one data-point per variant for side-by-side comparison
  const chartData = variants.map((v, idx) => ({
    name: v.variant_name,
    value: v.mean,
    variantId: v.variant_id,
    isControl: v.is_control,
    fill: v.is_control
      ? CONTROL_COLOR
      : VARIANT_COLORS[(idx - 1) % VARIANT_COLORS.length],
  }));

  // Build one Bar per data point so tests can query individual bars
  return (
    <div className="w-full h-72" data-testid="conversion-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis tickFormatter={(v: number) => `${(v * 100).toFixed(1)}%`} />
          <Tooltip formatter={(v) => [`${((v as number) * 100).toFixed(2)}%`, 'Rate']} />
          <Legend />
          {chartData.map((entry) => (
            <Bar
              key={entry.variantId}
              dataKey="value"
              name={entry.name}
              fill={entry.fill}
              {...({ 'data-is-control': String(entry.isControl) } as object)}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
```

The colour constants are Tailwind's palette as hex (`#3b82f6` is `blue-500`) because SVG
fills can't use classes. `(v * 100).toFixed(1)}%` — rates arrive as fractions and are
formatted at the edge. The odd-looking `{...({ 'data-is-control': ... } as object)}` spreads
a custom attribute Recharts' `Bar` props don't declare — a typed escape hatch so the mock
(and the DOM) can expose control-ness. (`Tooltip formatter={(v) => ... (v as number)}` is
the recharts 3.10 typing fix from the Dependabot upgrade.)

A chart with local UI state — cumulative vs daily — and a data union across series:

```tsx
// 📁 frontend/src/components/results/Visualizations/TrendChart.tsx
type ViewMode = 'cumulative' | 'daily';

export function TrendChart({ series }: TrendChartProps) {
  const [viewMode, setViewMode] = useState<ViewMode>('cumulative');

  if (!series.length) {
    return (
      <div className="flex items-center justify-center h-48 text-slate-500" data-testid="trend-empty">
        No trend data available
      </div>
    );
  }

  // Collect all unique dates across all series
  const allDates = Array.from(new Set(series.flatMap((s) => ...
```

The mock — every chart primitive becomes an inspectable `div`:

```tsx
// 📁 frontend/src/__mocks__/recharts.tsx
export const ResponsiveContainer = ({ children }: any) => (
  <div data-testid="responsive-container">{children}</div>
);

export const BarChart = ({ children }: any) => (
  <div data-testid="bar-chart">{children}</div>
);

export const Bar = ({ name, children, 'data-is-control': dataIsControl, ...rest }: any) => (
  <div data-testid="variant-bar" data-is-control={dataIsControl} data-fill={rest.fill}>
    {String(name ?? '')}
    {children}
  </div>
);

export const LineChart = ({ children }: any) => <div data-testid="line-chart">{children}</div>;
export const Line = ({ name, children }: any) => <div data-testid="trend-line">{String(name ?? '')}{children}</div>;
export const XAxis = () => <div data-testid="x-axis" />;
export const YAxis = () => <div data-testid="y-axis" />;
export const CartesianGrid = () => <div />;
export const Tooltip = () => <div />;
export const Legend = () => <div />;
```

and the mapping that turns it on for every test:

```js
// 📁 frontend/jest.config.js
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/src/$1',
    '\\.(css|less|scss|png|jpg|jpeg|gif|svg)$': 'identity-obj-proxy',
    '^recharts$': '<rootDir>/src/__mocks__/recharts.tsx',
  },
```

A test then reads structure, not pixels:

```tsx
// (shape of the ConversionChart tests)
render(<ConversionChart variants={variants} />);
const bars = screen.getAllByTestId('variant-bar');
expect(bars).toHaveLength(3);
expect(bars.filter((b) => b.getAttribute('data-is-control') === 'true')).toHaveLength(1);
```

The results dashboard assembles the charts under tabs and loads three endpoints in
parallel:

```tsx
// 📁 frontend/src/components/results/ResultsDashboard/ResultsDashboard.tsx
  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [r, d, s] = await Promise.all([
        ResultsService.getResults(experimentId),
        ResultsService.getDailyResults(experimentId),
        ResultsService.getSampleSize(experimentId),
      ]);
      setResults(r);
```

`Promise.all` — three requests in flight at once, one `await`; a single failure rejects the
whole thing into the `catch`. (`Promise.allSettled` would let the page render the parts that
succeeded; a trade the dashboard hasn't needed yet.)

> **Watch Out!** `ResponsiveContainer` measures its parent, so the parent needs a real height
> (`h-72`). Without it the chart renders at zero height and looks "broken" — a CSS bug that
> presents as a charting bug. Every chart wrapper in the codebase sets a height class.

> **Did You Know?** The `Tooltip formatter` typing changed in recharts 3.10 (the Dependabot
> upgrade PR notes "Fix Tooltip formatter typing for recharts 3.10"); the `(v as number)`
> cast is the residue. When a library major bump lands, `npx tsc --noEmit` is the first
> thing to run — the type errors are the changelog.

### Try It Yourself

Add a `p_value` line to the tooltip of `ConversionChart` (format `p = 0.032`) and write the
Jest assertion that the mock's `Bar`s still number one per variant. Then run the chart tests:

```bash
cd frontend && npx jest src/tests/components -t "Chart" --watchAll=false
```

Finally, temporarily remove the `recharts` line from `moduleNameMapper` and run one chart
test to see why the mock exists.

### Common mistakes

- **Passing API types straight to the chart.** Map to chart data in the component.
- **No height on the container** — zero-height chart.
- **Testing charts without the mock** — jsdom can't lay out SVG.
- **Colours from Tailwind classes in SVG** — use hex constants.

### Key takeaways

- Recharts is declarative; derive chart data from typed props; one `Bar` per variant for
  testability; format at the edge.
- `moduleNameMapper` swaps Recharts for `src/__mocks__/recharts.tsx` in Jest; tests read
  `data-testid`/`data-*` on the stubs.
- Heights on containers; hex colours; `Promise.all` for parallel loads.

---

## Part V Workshop

**Q1. Why does `` className={`bg-${colour}-600`} `` produce no styling?**
Tailwind generates only classes it finds as complete strings in the scanned source; the
template fragment never matches `bg-green-600`, so it's purged. Use complete names or a table.

**Q2. What does `focus-visible:ring-2` do that `focus:ring-2` doesn't?**
Shows the ring only for keyboard focus (Tab), not for mouse clicks — the accessible default.

**Q3. Why are `id`s on `TargetingCondition` and `TargetingRuleGroup`, and where do they go
on save?**
They're React `key`s and `datalist` ids for the builder; `rulesToJson` strips them so the
API receives exactly `{attribute, operator, value}` per condition.

**Q4. A user changes a condition's attribute from `user.country` to `app.version`. What
happens to its operator, and where?**
`handleAttributeChange` recomputes the allowed operators for the new type (`semver`); if the
current one isn't allowed, it resets to the first allowed (`semver_eq`). The invariant is
kept in the handler.

**Q5. Why is Recharts mocked in Jest, and how?**
It renders SVG from layout measurements jsdom can't provide; `jest.config.js` maps
`recharts` to `src/__mocks__/recharts.tsx`, where each primitive is a `div` with
`data-testid`/`data-*` attributes tests can query.

**Q6. Why one `<Bar>` per variant instead of one `<Bar>` over the whole dataset?**
So tests can query individual bars (`getAllByTestId('variant-bar')`) and read
`data-is-control`/`data-fill` per variant; one `Bar` would render one stub.

**Quiz.** Which of these are safe Tailwind class expressions: `on ? 'bg-green-600' :
'bg-slate-300'`, `` `text-${size}` ``, `ACTION_META[action].className`?
→ The first and third (complete strings in source); the second is purged.

---

## Exercises

**E5.1 — A badge system.** Write `StatusBadge` for `ExperimentStatus` using a
`Record<ExperimentStatus, string>` of complete Tailwind class strings, with `data-status`
and `aria-label`.

**E5.2 — A new operator.** Add `not_regex` end to end: the `OperatorType` union,
`OPERATORS_BY_TYPE.string`, `OPERATOR_LABELS`, `validateRules` (needs a value), and name the
backend file that must also change.

**E5.3 — Builder invariant.** Write a unit test for `ConditionRow`'s attribute-change
behaviour: render with `{ attribute: 'user.country', operator: 'contains', value: 'U' }`,
change the attribute to `app.version`, assert `onChange` was called with `operator:
'semver_eq'`.

**E5.4 — A chart.** Write `SampleSizeChart({ required, current })` rendering two bars
(required vs current) with `data-testid="sample-bar"`, and the Jest assertion that the
current bar's `data-fill` is red below 80% and green at/above 100%.

**E5.5 — Parallel loads.** Rewrite `fetchAll` with `Promise.allSettled` so the overview
renders when results succeed but daily data fails, showing an inline error for the trend tab
only.

---

## Solutions

**S5.1**

```tsx
const BADGE_CLASSES: Record<ExperimentStatus, string> = {
  draft: 'bg-slate-100 text-slate-700',
  active: 'bg-green-100 text-green-800',
  paused: 'bg-amber-100 text-amber-800',
  completed: 'bg-blue-100 text-blue-800',
  archived: 'bg-slate-200 text-slate-500',
};

export function StatusBadge({ status }: { status: ExperimentStatus }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${BADGE_CLASSES[status]}`}
          data-status={status} aria-label={`Status: ${EXPERIMENT_STATUS_LABELS[status]}`}>
      {EXPERIMENT_STATUS_LABELS[status]}
    </span>
  );
}
```

**S5.2** — `types/targeting.ts`: add `| 'not_regex'` to `OperatorType`, `'not_regex'` after
`'regex'` in `OPERATORS_BY_TYPE.string`, `not_regex: 'does not match regex'` in
`OPERATOR_LABELS`. `utils/targeting.ts`: add `'not_regex'` to `validOperators` (it needs a
value, so no change to `noValueOperators`). Backend: `backend/app/core/targeting_adapter.py`'s
`_SIMPLE_OPERATOR_MAP` must map `"not_regex"` to an engine operator — and since the engine
has no `NOT_MATCH_REGEX`, `schemas/targeting_rule.py::OperatorType` and
`rules_engine.apply_operator` need it too. Without the backend change the whole rule set is
uninterpretable and the flag falls back to its global rollout.

**S5.3**

```tsx
it('resets the operator when the attribute type changes', () => {
  const onChange = jest.fn();
  render(<ConditionRow condition={{ id: 'c1', attribute: 'user.country', operator: 'contains', value: 'U' }} onChange={onChange} onRemove={() => {}} />);
  fireEvent.change(screen.getByTestId('condition-attribute'), { target: { value: 'app.version' } });
  expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ attribute: 'app.version', operator: 'semver_eq' }));
});
```

**S5.4**

```tsx
export function SampleSizeChart({ required, current }: { required: number; current: number }) {
  const ratio = required > 0 ? current / required : 0;
  const fill = ratio >= 1 ? '#16a34a' : ratio >= 0.8 ? '#d97706' : '#dc2626';
  const data = [{ name: 'Required', value: required, fill: '#94a3b8' }, { name: 'Current', value: current, fill }];
  return (
    <div className="w-full h-48" data-testid="sample-size-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <XAxis dataKey="name" /><YAxis />
          {data.map((d) => <Bar key={d.name} dataKey="value" name={d.name} fill={d.fill} />)}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// test
const bars = screen.getAllByTestId('variant-bar');   // the mock's Bar test id
expect(bars[1]).toHaveAttribute('data-fill', '#dc2626');   // for current/required < 0.8
```

**S5.5**

```tsx
  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    const [r, d, s] = await Promise.allSettled([
      ResultsService.getResults(experimentId),
      ResultsService.getDailyResults(experimentId),
      ResultsService.getSampleSize(experimentId),
    ]);
    if (r.status === 'fulfilled') setResults(r.value); else setError(r.reason instanceof Error ? r.reason.message : 'Failed to load results');
    if (d.status === 'fulfilled') { setDaily(d.value); setDailyError(null); } else setDailyError(d.reason instanceof Error ? d.reason.message : 'Trend data unavailable');
    if (s.status === 'fulfilled') setSampleSize(s.value);
    setLoading(false);
  }, [experimentId]);
```

The trend tab renders `dailyError` inline (`data-testid="trend-error"`) while the overview
uses `results` as before.

---

**Next:** [Part VI — Production →](./part-6-production.md)

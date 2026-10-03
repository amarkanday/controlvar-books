# Part III — Sixteen SDKs

### Hours 7–9

[← Part II](./part-2-building-a-client.md) · [Contents](./README.md) · [Part IV →](./part-4-proving-agreement.md)

The Python and JavaScript SDKs are the references; the other fourteen are ports or adapters.
This Part is about porting well. Hour 7 walks the nine "native" ports — Go, Java (and its Spring Boot
starter), Swift, Kotlin, Ruby, PHP, .NET, Elixir, Flutter — and shows that each one is the
same five decisions expressed in its language's idiom for concurrency, errors and lifetime.
Hour 8 covers the three that sit on a standard or a special runtime: the OpenFeature
providers, the edge client, React Native. Hour 9 is a case study with an ending: a _second
implementation of the server's decision_, the four hash functions that drifted apart, and the
two changes that closed it.

---

## Hour 7 — Porting the design: Go, Java + Spring Boot, Swift, Kotlin, Ruby, PHP, .NET, Elixir, Flutter

### What & why

A port has to preserve five things exactly and is free to change everything else:

| Must match | Free to vary |
|---|---|
| The four endpoints, bodies and headers | HTTP library (`net/http`, OkHttp, `URLSession`, `Net::HTTP`, curl, `HttpClient`, `:httpc`, `package:http`) |
| Cache per user + key, TTL, successes only | Cache structure (map + lock, LRU, `NSCache`, ETS) |
| Fan-out rule (keyed → `/track`, key-less → `/batch` over cached entries, empty → nothing) | Concurrency model (goroutines, coroutines, async/await, threads, processes) |
| Failure policy: detailed call fails loudly, convenience call returns a safe default, `track` never fails | How "fails loudly" is spelled (`error`, exception, `throws`, `{:error, _}`, `null`) |
| The hash and the golden vectors | Everything about packaging and DI |
| `context` sent on flag evaluation; `assigned`/`reason` passed through on assignment | |

The last row is where the ports are not all there yet; the Watch Out at the end of this
section names the gaps. The second column is where language taste lives. The first column is the contract, and the
docblock at the top of every client restates it — deliberately, in nearly the same words, so
a reviewer can diff the _promises_ across ports without reading the code.

### How Experimently does it

**Go** — an interface, context-aware calls, errors returned alongside a safe value:

```go
// 📁 sdk/go/client.go
// Client is the main interface for interacting with Experimently.
// Implementations must be safe for concurrent use by multiple goroutines.
type Client interface {
	// EvaluateFlag evaluates a feature flag for the given user via
	// GET /api/v1/feature-flags/evaluate/{flagKey}?user_id=... The server
	// decides; successful results are cached per user + key for the cache TTL.
	// On failure it returns a disabled EvalResult together with the error.
	EvaluateFlag(ctx context.Context, flagKey string, user *User) (*EvalResult, error)

	// GetAssignment assigns the user to an experiment variant via
	// POST /api/v1/tracking/assign (sticky server-side; records the exposure).
	// Successful assignments are cached per user + key for the cache TTL.
	// On failure it returns (nil, err).
	GetAssignment(ctx context.Context, experimentKey string, user *User) (*Assignment, error)
	...
	// Close releases all resources held by the client (HTTP connections, caches).
	// It is safe to call more than once. After Close, the client must not be used.
	Close() error
}
```

Go's idiom for "fail loudly but give a safe default" is to return both — the disabled
result _and_ the error — so `enabled, _ := c.EvaluateFlag(...)` is the convenience form and
`if err != nil` is the detailed form, from one method:

```go
// 📁 sdk/go/client.go
	var resp evaluateResponse
	if err := c.http.Get(ctx, path, &resp); err != nil {
		// Degrade gracefully: the flag is reported off. Failures are not cached.
		return &EvalResult{Key: flagKey, Enabled: false}, fmt.Errorf("evaluate flag %q: %w", flagKey, err)
	}
```

The 429 policy shows up as data on the error rather than a retry: Go callers commonly own
their retry loop (`context` deadlines, their own backoff), so the SDK exposes `RetryAfter`
and lets them:

```go
// 📁 sdk/go/http.go
// APIError is returned when the server responds with a non-2xx HTTP status code:
// 401 bad API key, 404 experiment/flag unknown or not ACTIVE, 422 invalid event,
// 429 rate limited (see RetryAfter).
type APIError struct {
	StatusCode int
	Message    string
	// RetryAfter is the raw Retry-After header, set on 429 responses.
	RetryAfter string
}
```

`Close()` exists because Go's `http.Transport` keeps idle connections. The Python SDK's
`close()` is a no-op in server mode because `urllib` keeps none; it does real work only in
local mode, where there is a refresh thread to stop. Same contract, different resource model.

**Java** — a builder-configured client and a Spring Boot auto-configuration so a Spring app
gets an injectable bean from four properties:

```java
// 📁 sdk/java/spring-boot-starter/src/main/java/com/getexperimently/sdk/spring/ExperimentationAutoConfiguration.java
@AutoConfiguration
@ConditionalOnClass(ExperimentationClient.class)
@EnableConfigurationProperties(ExperimentationProperties.class)
@ConditionalOnProperty(prefix = "experimentation", name = "api-key")
public class ExperimentationAutoConfiguration {

    @Bean
    @ConditionalOnMissingBean
    public ExperimentationClient experimentationClient(ExperimentationProperties properties) {
        SdkConfig config = SdkConfig.builder(properties.getApiKey(), properties.getBaseUrl())
                .timeoutMs(properties.getTimeoutMs())
                .cacheSize(properties.getCacheSize())
                // SdkConfig.Builder uses milliseconds; properties expose seconds for user-friendliness
                .cacheTtlMs((long) properties.getCacheTtlSeconds() * 1000L)
                .build();
        return new ExperimentationClient(config);
    }
}
```

`@ConditionalOnProperty(... name = "api-key")` means the starter does nothing until
`experimentation.api-key` is set — no bean, no startup failure, no accidental calls to
`localhost:8000` from a test profile. The comment about milliseconds vs seconds is the kind of
unit boundary a port introduces and must document (the deleted Lambda copy had a worse one;
Hour 9).

**Swift** — `async throws`, `NSCache`, an offline store, and the same in-flight dedupe as JS:

```swift
// 📁 sdk/ios/Sources/Experimently/ExperimentationClient.swift
/// ## How it works
/// - **The server decides.** `evaluateFlag` calls `GET /api/v1/feature-flags/evaluate/{key}`
///   and `getAssignment` calls `POST /api/v1/tracking/assign`; no bucketing happens on device.
/// - **Caching.** Successful results are cached in memory per user + key for `cacheTTL`
///   seconds (NSCache) and, when `enableOfflineFallback` is on, persisted to `UserDefaults`.
///   Failures are never cached.
/// - **Failure.** On a network or server error the cached value is returned when present;
///   otherwise the call throws (`flagNotFound` / `experimentNotFound` for HTTP 404, which never
///   falls back to a stale persisted value).
/// - **Tracking.** `track` never throws. An event carrying `experimentKey`/`featureFlagKey`
///   goes to `POST /api/v1/tracking/track`; an event without keys is fanned out through
///   `POST /api/v1/tracking/batch` to every cached assignment and evaluated flag of that user
///   (nothing cached → nothing sent).
///
/// ## Thread Safety
/// The client is safe to use from multiple threads and Swift concurrency tasks. Caches are
/// lock-protected and concurrent calls for the same user + key share one in-flight request.
public class ExperimentationClient: ExperimentationClientProtocol {
```

The mobile SDKs (Swift, Kotlin, Flutter, React Native) add one thing the server-side ones
don't: an **offline store**. A phone loses the network; the last successful evaluation is
persisted (`UserDefaults` / `SharedPreferences` / `AsyncStorage`) and served when the API is
unreachable. The rule that keeps this honest: **a definitive 404 never falls back to a stale
persisted value** — "the flag was turned off" must not be masked by "the flag was on last
week."

**Kotlin** — suspend functions on `Dispatchers.IO`, a `SupervisorJob` scope for
fire-and-forget:

```kotlin
// 📁 sdk/android/sdk/src/main/kotlin/com/getexperimently/android/ExperimentationClient.kt
 * Successful results are also written to the [OfflineStore] (SharedPreferences in
 * production) and read back when the API cannot be reached. Failures are never cached.
 * There is no local bucketing and no "fetch all flag definitions" path: the former
 * `refreshFlags()` and `SdkConfig.enableLocalEval` are gone / no-ops.
 *
 * All network operations use Kotlin Coroutines and run on Dispatchers.IO.
 * The client is thread-safe and suitable for use across multiple coroutines.
 */
class ExperimentationClient(
    private val config: SdkConfig,
    private val httpClient: HttpClient = HttpClient(config.baseUrl, config.apiKey, config.timeoutMs),
    private val flagCache: ResultCache<EvalResult> = ResultCache(config.cacheSize, config.cacheTtlMs),
    private val assignmentCache: ResultCache<Assignment> = ResultCache(config.cacheSize, config.cacheTtlMs),
    private val offlineStore: OfflineStore = OfflineStore()
) {
    // Background scope for fire-and-forget operations (SupervisorJob: child failures don't cancel parent)
    private val backgroundScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
```

"`refreshFlags()` and `enableLocalEval` are gone / no-ops" is the rewiring's fingerprint: the
pre-September SDKs downloaded flag definitions and evaluated locally; the methods remain as
no-ops so existing integrations compile, and the docblock says so.

**Ruby, PHP, .NET, Elixir, Flutter** — the same docblock, five more idioms:

```ruby
# 📁 sdk/ruby/lib/experimently/client.rb
  # Flag evaluation and experiment assignment are decided by the server; the
  # SDK never buckets users locally. Successful results are cached per
  # user + key for +cache_ttl+ seconds; failures are never cached.
  #
  #   - evaluate_flag    : GET  /api/v1/feature-flags/evaluate/{key}?user_id=...
  #   - get_assignment   : POST /api/v1/tracking/assign   (sticky on the server)
  #   - track            : POST /api/v1/tracking/track    (with a key)
  #                        POST /api/v1/tracking/batch    (fan-out without a key)
  #   - track_batch      : POST /api/v1/tracking/batch
  ...
  # All public methods are safe to call from multiple threads: the cache is
  # mutex-protected and every request opens its own connection.
  class Client
    # Maximum events per POST /api/v1/tracking/batch request.
    BATCH_LIMIT = 100
```

PHP uses `ext-curl` and `ext-json` only (the "no dependencies" rule, PHP edition); .NET
exposes `EvaluateFlagAsync` / `GetAssignmentAsync` / `TrackAsync` returning `Task<T>` over
`HttpClient`; Flutter's `evaluateFlag` returns the cached value or a disabled result and
"neither throws" — Dart's convention for a client used from widgets.

Elixir is the port that most changes shape, because the BEAM's unit of state is a process:

```elixir
# 📁 sdk/elixir/lib/experimently/client.ex
  GenServer client for the Experimently public API.

  Flag evaluation and experiment assignment are decided by the server; the
  SDK never buckets users locally. Successful results are cached per
  user + key (in a per-client ETS cache) for `cache_ttl` seconds; failures are
  never cached.
  ...
  The GenServer owns the configuration, the cache and the in-flight
  fire-and-forget track requests. Evaluations, assignments and batches run in
  the **calling** process, so many processes can use one client concurrently;
  only `track/5` is delegated to the client (it spawns one process per request
  and `stop/1` waits for them, up to `timeout`, before exiting).
```

The design point: evaluations run in the _caller's_ process (no GenServer bottleneck on the
hot path; the ETS table is the shared cache), and only `track` — fire-and-forget — goes
through the server so that `stop/1` can drain in-flight sends. It's the fan-out rule and
"track never fails" mapped onto supervision trees, and it's why "track is fire-and-forget"
has to be a contract-level statement: an Elixir port that ran `track` synchronously in the
caller would be _correct_ but would give up the drain-on-stop guarantee that this one gives.

> **Watch Out!** Units. The Java properties expose `cacheTtlSeconds` while the builder takes
> milliseconds; Swift's `cacheTTL` is seconds; JS's `cacheTtlMs` is milliseconds; Python's
> `cache_ttl_seconds` is seconds. Each is idiomatic; each is documented at the boundary.
> A port review checklist item: _every_ duration parameter names its unit in the identifier.

> **Watch Out!** Two contract rows are not yet met by every port, and both are open issues
> rather than design choices. **Context on flag evaluation:** only the React, JavaScript
> and Python SDKs send the user's attributes as `context` today; the others evaluate with
> `user_id` alone, so a flag's attribute targeting never matches through them (Ruby's
> `evaluate_flag` takes `_attributes` and ignores it). Issue #438 tracks the most visible
> case, native mobile: React Native, iOS, Android and Flutter. **`assigned` / `reason` (#316):** the
> JavaScript and Python SDKs pass both through (unknown when an older server omits them),
> React defaults a missing `assigned` to `true`, and the other ports drop both fields — so
> their callers cannot tell an ineligible user from an enrolled one. A port review should
> check these two rows explicitly until the issues close.

> **Did You Know?** The Android SDK's Kotlin touches no `android.*` API — deliberately, so
> `sdk/android/jvm` can compile it for a plain JVM and the contract smoke runs on a Linux CI
> runner with no Android SDK and no emulator. Designing the port so its core is
> platform-free is what made it testable in CI at all (Hour 11).

### Try It Yourself

Pick two ports you can run and diff their promises:

```bash
cd sdk/go && go test -race ./... 2>&1 | tail -3
cd ../ruby && bundle exec rspec 2>&1 | tail -3
# The docblocks, side by side:
sed -n '/How it works/,/Thread Safety/p' ../ios/Sources/Experimently/ExperimentationClient.swift
sed -n '1,40p' ../elixir/lib/experimently/client.ex
```

Then run the same smoke through both: `python tests/sdk-contract/live/run_live_contract.py
--sdk go --sdk ruby` — one JSON line each, same shape.

### Key takeaways

- Five invariants (endpoints, cache, fan-out, failure policy, hash); everything else is
  idiom — and the docblock restates the invariants in every port.
- Errors are spelled per language: `(value, error)` in Go, exceptions in Java/PHP/Ruby,
  `async throws` in Swift, `{:ok, _} | {:error, _}` in Elixir, "never throws" in Dart.
- Mobile ports add an offline store with the 404-never-falls-back rule; server ports add
  lifecycle (`Close`, `stop/1`) where the runtime holds resources.
- Watch the unit boundaries; keep cores platform-free so CI can run them.
- Known gaps: only React, JS and Python send `context` on flag evaluation (native mobile is
  #438); most ports drop `assigned`/`reason` (#316).

---

## Hour 8 — Standards and edges: OpenFeature providers, the edge runtime client, React Native

### What & why

Three SDKs aren't ports of the client; they're adapters that put the client behind
something else. **OpenFeature** is the CNCF standard flag API — an application codes against
`client.getBooleanValue('flag', false, context)` and plugs in a vendor _provider_; shipping
one means an OpenFeature app can adopt Experimently without changing call sites. The **edge
client** runs in Cloudflare Workers, Vercel Edge and Deno Deploy, where there are no Node
built-ins, request budgets are hundreds of milliseconds, and a memory cache lives only as
long as an isolate — so it adds a shared store (KV). **React Native** is the React SDK's
shape on a device: `fetch` and hooks, but persistent storage is `AsyncStorage` and nothing
may throw into a UI thread.

### How Experimently does it

**OpenFeature (JS)** — the resolution rules map the platform's `{enabled, config}` onto
OpenFeature's typed resolutions, and `targetingKey` becomes `user_id`:

```ts
// 📁 sdk/openfeature/src/ExperimentationProvider.ts
/**
 * OpenFeature Provider for Experimently.
 *
 * Implements the @openfeature/server-sdk `Provider` interface on top of
 * `@getexperimently/js-sdk`. Every evaluation is decided by the
 * server (`GET /api/v1/feature-flags/evaluate/{flag_key}?user_id=…`); the
 * JS SDK caches successful answers per user + flag for `cacheTtlMs` and never
 * caches failures.
 *
 * Resolution rules:
 *   boolean → `enabled`
 *   string  → `config.variant` when it is a string (or `config` itself when it is a string)
 *   number  → `config.value` when it is a number (or `config` itself when it is a number)
 *   object  → `config` when it is a non-null object
 *   anything else → the caller's default with reason DEFAULT; a disabled flag
 *   yields the default with reason DISABLED for non-boolean types.
 *
 * Context mapping: `targetingKey` → `user_id`. Other context attributes are
 * NOT sent — the evaluate endpoint takes only the user id.
 *
 * Experiments and event tracking are not OpenFeature concepts; use
 * `provider.client` (the underlying JS SDK client) for `getAssignment`,
 * `getVariant`, `track` and `trackBatch`.
 */
```

```ts
// 📁 sdk/openfeature/src/ExperimentationProvider.ts
  /** Resolve `targetingKey` → `user_id` and evaluate through the JS SDK (cached per user + flag). */
  private async evaluate(flagKey: string, context: EvaluationContext | undefined): Promise<Lookup> {
    const userId = context?.targetingKey;
    if (typeof userId !== 'string' || userId.length === 0) {
      return { ok: false, errorCode: ErrorCode.TARGETING_KEY_MISSING, errorMessage: 'targetingKey is required: it is sent to the server as user_id' };
    }

    const cached = this.client.getEvaluatedFlags(userId).includes(flagKey);
    try {
      const evaluation = await this.client.evaluateFlag(flagKey, { userId });
      return {
        ok: true,
        evaluation,
        reason: cached ? StandardResolutionReasons.CACHED : StandardResolutionReasons.TARGETING_MATCH,
      };
    } catch (err) {
      const status = (err as { status?: number })?.status;
      ...
      if (status === 404) {
        return { ok: false, errorCode: ErrorCode.FLAG_NOT_FOUND, ...
```

Two things to notice. The provider _composes_ the JS SDK rather than reimplementing the
contract — the cache, dedupe and error codes come for free, and the golden vectors already
cover its hash. And it's honest about the mismatch between the standards: OpenFeature has no
concept of experiments or events, so those are reached through `provider.client`, and the
provider reports the SDK's cache hit as OpenFeature's `CACHED` reason. The Python provider
mirrors it (`resolve_boolean_details` etc., `Reason.CACHED` / `TARGETING_MATCH` / `DISABLED`
/ `ERROR`), and the same docblock rules apply. The JS provider is the package
`@getexperimently/openfeature-provider` and takes `@openfeature/server-sdk` as a peer
dependency, so the application chooses the OpenFeature version.

The server once had OpenFeature-shaped routes of its own. The evaluate routes were removed in
#345 (they never returned a result), and the flag listing under `/api/v1/openfeature/` was
deprecated in favour of the ruleset and **removed** in #770, in the release after 0.17.0; it
now answers 404. The providers never used either: they evaluate through the JS and Python
SDKs, which is why nothing on the client side changed.

**Edge** — zero Node built-ins, a 500 ms default timeout, and a two-level cache:

```ts
// 📁 sdk/edge/src/client.ts
/**
 * EdgeExperimentationClient — the main client for edge environments.
 *
 * Design goals:
 *  - Zero Node.js built-ins (no `fs`, no `crypto` module, no `Buffer`)
 *  - Works in Cloudflare Workers, Vercel Edge Functions, Deno Deploy, and any
 *    WinterCG-compatible runtime
 *  - AbortController-based timeouts (Web standard, supported everywhere)
 *
 * Behaviour:
 *  - **The server decides.** ...
 *  - **Caching.** Successful results are cached in memory per user + key for
 *    `cacheTtlMs`, and optionally in a shared `store` (Cloudflare KV, Deno KV)
 *    so other isolates can reuse them. Failures are never cached. Concurrent
 *    calls for the same user + key share one in-flight request.
 *  - **Failure values.** `evaluateFlag` resolves to a disabled evaluation and
 *    `getAssignment` to `null` when the server cannot be reached; `track`
 *    never throws.
 */
const DEFAULT_CACHE_TTL_MS = 60_000; // 1 minute
const DEFAULT_TIMEOUT_MS = 500;      // 500ms — edge functions must be fast
```

```ts
// 📁 sdk/edge/src/types.ts
export interface EdgeStore {
  get(key: string): Promise<string | null>;
  put(key: string, value: string, ttlMs: number): Promise<void>;
}
```

The lookup order is memory → shared store → network, and store I/O is best-effort (a KV
outage degrades to "no shared cache," never to an error). The Deno adapter shows the store
keyed exactly like the memory cache — per user + key — so the fan-out rule still holds
across isolates:

```ts
// 📁 sdk/edge/src/adapters/deno.ts
 * Deno KV layout:
 *   Key:   ["ep", "flag:{userId}:{flagKey}"] / ["ep", "assign:{userId}:{experimentKey}"]
 *   Value: JSON string of the FlagEvaluation / Assignment
 *   TTL:   kvTtlMs (defaults to cacheTtlMs)
```

The edge client used to be able to download a bootstrap payload of flags from
`/api/v1/edge/bootstrap`. That route was deprecated in favour of the ruleset (#495) and
**removed** in #770, in the release after 0.17.0; it now answers 404. The Cloudflare and Deno
adapters keep their old bootstrap and refresh methods as `@deprecated` no-ops ("There is no
bootstrap payload any more"), so existing Workers still compile.

The edge client's failure policy is "never throws, safe values" for _every_ method — the
JS SDK's convenience wrappers become the only API, because an edge function has no one to
catch an exception and a 500 there takes the whole page down. Same policy family, the
strictest setting.

**React Native** — the JS SDK's shape with `AsyncStorage` persistence and, again, nothing
that throws:

```ts
// 📁 sdk/react-native/src/client.ts
 * Flag evaluation and experiment assignment are decided **by the server**
 * (`GET /api/v1/feature-flags/evaluate/{key}?user_id=…`,
 * `POST /api/v1/tracking/assign`); nothing is bucketed locally. Successful
 * results are cached in memory per user + key (configurable TTL) and, when
 * `offlineFallback` is on, persisted to AsyncStorage so they survive app
 * restarts and can be served as last-known values when the API is unreachable.
 *
 * None of the public methods throw: `evaluateFlag` resolves to a disabled
 * evaluation and `getAssignment` to `null` on failure (failures are never
 * cached); `track` is fire-and-forget. Swallowed failures are reported to
 * `config.onError`.
```

React Native has no device runtime in CI, but it is still in the live contract: its smoke is a
Jest test under `testEnvironment: node` that writes its JSON report to a file (Hour 11), and
the `SDK Live Contract` job runs it on every pull request. The SDK was designed so that this
is possible: its client is plain TypeScript over `fetch`, with the RN-specific storage behind
an interface and mocked in tests. What that run does not prove is behaviour on a device
(Metro bundling, real `AsyncStorage`, backgrounding).

> **By the Way** — the OpenFeature providers send **no** attributes even though the evaluate
> endpoint accepts `context`. That's a documented gap, not an oversight: OpenFeature's
> `EvaluationContext` is a flat map with its own conventions, and mapping it onto the
> platform's aliased attribute names (`country` ↔ `user.country`) deserves a decision, not a
> default. Until then, targeting rules on OpenFeature-evaluated flags only see `user_id`.

> **Watch Out!** The edge TTL is one minute, not five: a Worker isolate lives seconds to
> minutes, so a five-minute memory TTL would rarely matter, and the shared KV store's own
> TTL (`kvTtlMs`) defaults to the same 60 s — a kill-switched flag propagates to the edge in
> a minute instead of five. Shorter TTL, more traffic; the platform's SDK rate limit is the
> backstop.

### Try It Yourself

```bash
cd sdk/openfeature && npm run build --silent && node --input-type=module -e "
import { OpenFeature } from '@openfeature/server-sdk';
import { ExperimentationProvider } from './dist/index.js';
import { readFileSync } from 'node:fs';
const p = new ExperimentationProvider({ apiKey: readFileSync('../../tests/sdk-contract/live/.api_key','utf8').trim(), baseUrl: 'http://localhost:8000' });
await OpenFeature.setProviderAndWait(p);
const client = OpenFeature.getClient();
console.log(await client.getBooleanDetails('sdk_contract_flag', false, { targetingKey: 'u-hour8' }));   // reason TARGETING_MATCH
console.log(await client.getBooleanDetails('sdk_contract_flag', false, { targetingKey: 'u-hour8' }));   // reason CACHED
console.log(await client.getBooleanDetails('sdk_contract_flag', false, {}));                            // TARGETING_KEY_MISSING
console.log(await client.getStringDetails('sdk_contract_flag', 'dflt', { targetingKey: 'u-hour8' }));   // config null → DEFAULT
"
```

Then read `sdk/edge/src/adapters/deno.ts` and `sdk/edge/src/adapters/cloudflare.ts` side by
side: the Cloudflare adapter is a KV-backed `EdgeStore` whose `get` / `put` map onto
`KVNamespace.get` / `put({expirationTtl})`, with KV's 60-second minimum TTL handled.

### Key takeaways

- OpenFeature providers compose the JS/Python SDK, map `targetingKey` → `user_id`, resolve
  boolean from `enabled` and typed values from `config`, report `CACHED` on SDK cache hits;
  experiments and tracking go through the underlying client.
- Edge: no Node built-ins, 500 ms timeout, 60 s TTL, memory → KV → network, never throws.
  The bootstrap route is removed (#770); the adapters keep no-op shims.
- React Native: JS SDK shape + `AsyncStorage` + `onError`; testable under plain node by
  design, and run by the live contract in CI.

---

## Hour 9 — Case study: two deciders, four hashes, and how the drift was closed

### What & why

"The server decides" assumes there is one server. Until October 2026 the repository held a
second decider.
`backend/lambda/` held a second implementation of experiment assignment — a Python handler
meant to run behind API Gateway with DynamoDB as its store, built for latency targets the
FastAPI service could not promise — with its own consistent hash, its own targeting
evaluator and its own unit conventions. It was well tested, and it was _not the same
decider_.

This Hour is the story of that drift from discovery to close, in three acts, because it is
the clearest lesson the SDK work produced: **every second implementation of a decision is a
drift liability, and the cost of drift in an experimentation system is not a crash — it's an
SRM you can't explain.** It also shows the two ways to close drift: make the implementations
agree by contract, or delete one of them. The project did both.

### How Experimently does it

**Act 1 — the hash finding.** Nobody had compared the bucketing functions with each other.
When someone did, there were four, and they disagreed. The finding is recorded in the message
of commit `b46e5259`, and the backend's hash module keeps the record in its docstring. (The
`#81` in the quoted docstrings is a number from the project's tracker before it moved to
`getexperimently/experimently`; on the current tracker the same number names another item, so
read the commits instead.)

```python
# 📁 backend/app/core/consistent_hash.py
HISTORY. There were four experiment/flag hashes, and they disagreed (#81).

    openfeature.py          MD5("{user}:{key}"), first 4 bytes LE, / 2^32 (removed, #241)
    assignment_service.py   MD5("{user}:{experiment.id}"), FULL digest, % 100
    global_holdout / MEG    MD5("{user}:{salt}"), first 4 bytes LE, % 100
    lambda/shared           MurmurHash3-ish of the user, salted "{key}_variant"

The first is the contract: ``tests/sdk-contract/golden-vectors.json`` pins it
and the SDKs implement it. The others were free to drift because
nothing compared them.
```

Two of the disagreements were measured, and both are silent. For `user-123` / `my-flag` the
contract puts the user in bucket **69** and the API's assignment put them in bucket **79**,
because reading the whole 128-bit digest modulo 100 is a different number from reading the
first four bytes little-endian. And the API hashed the experiment's UUID primary key, which no
client has, so nothing outside the server could have agreed with it even in principle.

The Lambda's copy was a third shape. Its shared hasher (still in the repository, deployed by
nothing) uses the SDK byte order but a suffixed salt and the other divisor:

```python
# 📁 backend/lambda/shared/consistent_hash.py
        # Calculate hash for variant assignment
        variant_hash = self._hash(user_id, f"{hash_salt}_variant")
        variant_value = variant_hash / self.MAX_HASH_VALUE

        # Assign to variant based on cumulative allocation
        cumulative = 0.0
        for variant in variants:
            cumulative += variant.get("allocation", 0)
            if variant_value < cumulative:
                return variant.get("key")
```

```python
# 📁 backend/lambda/shared/consistent_hash.py
    MAX_HASH_VALUE = 0xFFFFFFFF  # 2^32 - 1
```

Side by side, as they stood before the fixes:

| | API assignment (before `b46e5259`) | Lambda copy (deleted in #606) |
|---|---|---|
| Hash input | `"{user_id}:{experiment.id}"` (UUID) | `"{user_id}:{experiment_key}_variant"` (key + suffix) |
| Normalisation | full 128-bit digest `% 100` | first 4 bytes LE ÷ (2³² − 1) |
| Traffic gate | none (allocation is per variant) | a separate `_traffic` hash against `traffic_allocation` (0.0–1.0) |
| Allocation units | percent per variant, summing to 100 | fraction per variant, summing to 1.0 |
| Eligibility | holdout → mutual exclusion → targeting | its own targeting evaluator; its handler passed no holdout or mutual exclusion config |

Given the same user and experiment, the two chose the same variant about half the time on a
50/50 split — they were independent functions. Each was sticky within itself, so a product
that used only one would have looked fine; a product whose web tier used the API and whose
mobile tier used the Lambda would have had users in two variants at once.

The units row is the memorable one. In the same package, flag `rollout_percentage` is 0–100
while experiment `traffic_allocation` is 0.0–1.0, and nothing in the types tells them apart:

```python
# 📁 backend/lambda/feature_flag_evaluation/evaluator.py
        # Use consistent hashing to determine if user is in rollout
        # Get bucket from 0-99 (100 buckets for percentage)
        bucket = self.hasher.get_bucket(
            user_id=user_id, experiment_key=flag_config.key, num_buckets=100
        )

        # User is included if their bucket is less than rollout percentage
        return bucket < rollout_percentage
```

A config copied from one to the other is silently wrong: a `0.5` rollout is 0%, a `50`
allocation is "everyone, and then some".

**Act 2 — make the API agree with the contract (commit `b46e5259`).** The golden-vector hash already had
the strongest claim: every SDK and the vectors file implemented it. So the API moved to it
(commit `b46e5259`), over the experiment's public `key`:

```python
# 📁 backend/app/services/assignment_service.py
        Uses `backend.app.core.consistent_hash`, which is the algorithm
        `tests/sdk-contract/golden-vectors.json` pins and the SDKs
        implement. Before #81 this method had its own: MD5 of
        ``"{user_id}:{experiment.id}"`` read as one 128-bit integer modulo 100.
        ...
        Assignment is sticky -- `assign_user` only reaches this for a user with
        no existing row -- so changing the function re-buckets nobody who has
        already been assigned.
```

Three details made this a safe change rather than a re-bucketing incident. Assignment is
sticky, so nobody already assigned moved. The backend's new test reads the same
`golden-vectors.json` the SDKs are held to, so the server cannot drift from them again
without a red build (Hour 2 quotes it). And the change deliberately touched **only**
experiment assignment: the flag rollout function, holdout, mutual exclusion and bandit
routing each kept their own hash, because changing any of them re-buckets users.

**Act 3 — delete the second decider (#606).** Aligning the Lambda would have meant porting
the hash _and_ the holdout, mutual exclusion and targeting gates, then keeping them aligned
for ever. And it turned out the Lambda copy was worse than misaligned: its handler passed no
mutual exclusion or holdout configuration at all, so through it a user would be enrolled in
every experiment of an exclusion group. No stack had ever deployed it. So it was deleted
(commit `1599459c`), and the API became the only code that assigns a user to an experiment.
The directory's README now says so:

```markdown
<!-- 📁 backend/lambda/README.md -->
# Lambda Functions (not deployed)

This directory contains AWS Lambda code for event processing and feature flag evaluation.
**No stack deploys it**; see `docs/integrations/aws.md` for the functions the stacks do deploy.
Experiment assignment happens only in the API (`POST /api/v1/tracking/assign`), which applies
the global holdout, mutual exclusion groups and targeting.
```

and `test_nothing_deploys_backend_lambda` in `infrastructure/tests/test_lambda_functions_doc.py`
pins it. Precisely: no stack deploys a handler from `backend/lambda/`; the assignment Lambda
was removed (#606). The stacks do deploy placeholder or inline functions —
`DatabaseAccessLambda` in the compute stack in every environment, and in a full checkout
`AnalyticsLambda` (a placeholder) and `ETLTriggerLambda` (a real inline function that starts
the Glue jobs). What remains under `backend/lambda/` — `event_processor/`,
`feature_flag_evaluation/` and `shared/` — is code with tests and no deployment.

**One decider, many readers.** The pattern that _doesn't_ drift is the one the bandit
scheduler uses. It can read per-variant counters from DynamoDB, an optional module, before
PostgreSQL — but only when they are at least as complete as PostgreSQL's for every variant
(#426); otherwise it counts assignments and converting users in PostgreSQL itself. Those
counters were meant to be written by the event-processor Lambda from the Kinesis stream;
that Lambda is not deployed, and the analytics stack's Kinesis consumer is a placeholder.
Either way it is a data path, not a decision path: readers count what the one decider
assigned.

> **Watch Out!** "It is well tested" was never "it agrees with the API". The Lambda's tests
> verified the Lambda against itself; no test fed the same `(user, experiment)` to both
> deciders. The fix was not that missing pairwise test but something stronger: **every**
> implementation is now checked against one file (`golden-vectors.json`), so agreement is
> transitive. When you find yourself writing a second implementation of a decision, write
> the shared vector file first.

> **Did You Know?** The Lambda hasher's docstring still says "MurmurHash3-like … In
> production, consider using mmh3 library". It never happened, which was lucky: switching it
> to Murmur would have added a _fifth_ bucket function to the family. The golden vectors pin
> the SDK hash precisely so that a well-meant "performance improvement" can't do this.

### Try It Yourself

Reproduce Act 1's numbers with nothing but `hashlib`:

```python
>>> import hashlib, struct
>>> user, key, exp_id = "user-123", "my-flag", "3f2a9b1c-0000-4000-8000-000000000001"
>>> d = hashlib.md5(f"{user}:{key}".encode()).digest()
>>> int(struct.unpack("<I", d[:4])[0] / 2**32 * 100)                 # the contract: API assignment today
69
>>> int(d.hex(), 16) % 100                                            # the old API reading (and flag rollout today)
79
>>> lam = struct.unpack("<I", hashlib.md5(f"{user}:{key}_variant".encode()).digest()[:4])[0] / 0xFFFFFFFF
>>> old_api = int(hashlib.md5(f"{user}:{exp_id}".encode()).hexdigest(), 16) % 100   # UUID-salted, as before b46e5259
```

Then read the two commits that closed it, and run the test that keeps it closed:

```bash
git show --stat b46e5259     # the API adopts the contract hash
git show --stat 1599459c     # the second decider is deleted
python -m pytest backend/tests/unit/core/test_consistent_hash.py -q -o addopts=""
```

Exercise E3.5 measures the agreement rate between the two deciders.

### Key takeaways

- Four bucketing functions existed for experiments and flags, and nothing compared them; the
  API and the Lambda copy chose the same variant about half the time.
- The API adopted the golden-vector hash over the experiment's key (commit `b46e5259`); stickiness meant
  nobody already assigned moved, and the backend's test now reads the SDKs' vector file.
- The Lambda copy, which also skipped mutual exclusion and holdout, was deleted (#606). No
  stack deploys a handler from `backend/lambda/`; experiment assignment happens only in the
  API.
- Close drift by contract (one vector file every implementation reads) or by deletion. One
  decider, many readers.

---

## Part III Workshop

**Q1. List the five things a port must preserve exactly.**
Endpoints/bodies/headers; per-user+key cache with TTL, successes only; the fan-out rule;
the failure policy (loud detailed call, safe convenience call, `track` never fails); the
hash and its golden vectors. Two more rows are part of the contract but not yet met by every
port: sending `context` on flag evaluation (#438) and passing `assigned`/`reason` through
(#316).

**Q2. Go returns `(&EvalResult{Enabled:false}, err)` on failure. Which two contract
behaviours does that single return satisfy?**
"Detailed call fails loudly" (the error) and "convenience call returns a safe default" (the
disabled result) — Go's multiple returns let one method be both.

**Q3. Why does the Spring Boot starter condition on `experimentation.api-key`?**
So the bean only exists when configured: no accidental client in tests, no startup failure
from a missing key, and `@ConditionalOnMissingBean` lets an app override it.

**Q4. A mobile SDK has a persisted evaluation `enabled: true` and gets a 404 from the server.
What does it return?**
It throws / returns disabled — never the stale `true`. A definitive 404 means the flag is
gone or inactive; masking that with last week's value would be the worst kind of wrong.

**Q5. Why do the OpenFeature providers report `CACHED` as a reason?**
Because the underlying SDK's cache is where the answer came from, and OpenFeature's standard
reasons include `CACHED`; the provider checks `getEvaluatedFlags(userId)` before evaluating
so it can tell.

**Q6. Before the fixes in Hour 9, two teams integrated the same experiment — one through the
API, one through the Lambda copy. What went wrong, how would you have detected it, and what
closed it?**
Users got different variants per surface: the two deciders used independent hash functions,
agreeing about half the time on a 50/50 split, and the Lambda copy also skipped mutual
exclusion and holdout. Detection: an SRM per surface, and a join of the two assignment stores
on `user_id`. What closed it: the API moved to the golden-vector hash (commit `b46e5259`), then the second
decider was deleted (#606), so there is one assignment path.

**Quiz.** Elixir runs `evaluate_flag` in the caller's process but `track` in the GenServer.
What contract guarantee does the second half buy?
→ `stop/1` can wait for in-flight fire-and-forget sends; "track never fails" _and_ "track
isn't lost on shutdown."

---

## Exercises

**E3.1 — Port review.** Take the Ruby client and, without running it, verify each of the
five invariants by reading: cite the line for each. Note any duration parameter whose unit
isn't in its name.

**E3.2 — A sixteenth port.** Write the client in a language not in `sdk/` (Rust, Lua, bash
with `curl`) implementing all five invariants, plus a `contract_smoke` that prints the JSON
line the harness expects (Hour 11). Verify the hash against the golden vectors first.

**E3.3 — OpenFeature attributes.** Propose (in a short design note) how
`EvaluationContext` attributes should map to the platform's `context` parameter, including
the `targetingKey` → `user_id` rule and the aliasing (`country` ↔ `user.country`). List one
attribute name that would collide.

**E3.4 — Edge store.** Read the Cloudflare KV adapter in `sdk/edge/src/adapters/cloudflare.ts`
and write a test for it: with two simulated isolates sharing one in-memory `KVNamespace`
fake, show that the second isolate's first `evaluateFlag` makes no network call, and that a
`cacheTtlMs` of 10 s reaches KV as `expirationTtl: 60`.

**E3.5 — Agreement test, after the fact.** Write the cross-decider test that would have caught
Hour 9's drift: feed 1,000 `(user, experiment)` pairs to the backend's `bucket_of` and to
`ConsistentHasher.assign_variant` in `backend/lambda/shared/consistent_hash.py` (still in the
repository, undeployed) with equivalent 50/50 configs, and report the agreement rate. Then
argue why the project deleted the second decider instead of aligning it.

---

## Solutions

**S3.1** — endpoints: the docblock lists all four; cache: `flag_cache_key` /
`assignment_cache_key` and the mutex note; fan-out: `track` with `experiment_key: nil,
feature_flag_key: nil` builds from `assignments(user_id)` + `evaluated_flags(user_id)`;
failure policy: Ruby takes the strict setting — `evaluate_flag` rescues `NetworkError,
APIError` and returns a disabled evaluation, `get_assignment` returns `nil`, `track`
rescues — so there is no "loud" call; note it as a documented deviation (the docblock says
so). Hash: `Evaluator.hash_user` in `evaluator.rb`. Unit: `cache_ttl` is documented as
seconds in `config.rb` but the identifier doesn't say so — a candidate for
`cache_ttl_seconds`. Also: `evaluate_flag` ignores `_attributes` ("the evaluate endpoint
only takes the user id"), so targeting rules on Ruby-evaluated flags see only `user_id`.

**S3.2** — the smoke's JSON must be `{"sdk": "<name>", "assign": {"variant_name",
"is_control", "sticky"}, "flag": {"enabled"}, "track": {"ok"}, "fanout": {"ok"}}` on one
stdout line, exit 0; and `"sticky"` must be proven by clearing the cache between two assigns.
Add the entry to `MANIFEST` in `run_live_contract.py` with its required executables.

**S3.3** — map `targetingKey` → `user_id` (required), every other top-level attribute →
`context[key]` verbatim, nested objects flattened by the server's `expand_context`; document
that `user.country` and a top-level `country` are aliases so a rule written either way
matches. Collision: an OpenFeature context that supplies both `country` and `user.country`
with different values — pick top-level wins and say so.

**S3.4** — the adapter's store does exactly this:

```ts
// 📁 sdk/edge/src/adapters/cloudflare.ts
/** Cloudflare KV rejects `expirationTtl` values below 60 seconds. */
const KV_MIN_TTL_SECONDS = 60;
```

and its `put` passes `Math.max(KV_MIN_TTL_SECONDS, seconds)` (KV's minimum TTL is 60 s —
another unit boundary). Fake `KVNamespace` as a `Map` with a recorded `put`. Isolate A
evaluates → memory + KV; isolate B (a fresh client, same fake) evaluates → memory miss → KV
hit → the mocked `fetch` is never called; the recorded `put` options show
`{ expirationTtl: 60 }`.

**S3.5**

```python
import sys
sys.path.insert(0, "backend/lambda")          # `lambda` is a keyword: import `shared` directly
from shared.consistent_hash import ConsistentHasher
from backend.app.core.consistent_hash import bucket_of

h = ConsistentHasher()
variants = [{"key": "control", "allocation": 0.5}, {"key": "treatment", "allocation": 0.5}]
users = [f"user-{i}" for i in range(1000)]
agree = sum(
    ("control" if bucket_of(u, "checkout_redesign") < 50 else "treatment")
    == h.assign_variant(u, "checkout_redesign", variants, 1.0)
    for u in users
)
print(agree / len(users))                     # ~0.5: independent functions
```

Agreement is about 0.5 (0.4996 over 10,000 users at the reference commit) — the two are
independent. Aligning the Lambda would have meant porting the hash _and_ the holdout, mutual
exclusion and targeting gates, and keeping them aligned for ever, for a function no stack
deployed. Deleting it left one decider; that is the cheaper invariant.

---

**Next:** [Part IV — Proving Agreement →](./part-4-proving-agreement.md)

# Part III — Fifteen Languages

### Hours 7–9

[← Part II](./part-2-building-a-client.md) · [Contents](./README.md) · [Part IV →](./part-4-proving-agreement.md)

The Python and JavaScript SDKs are the references; the other thirteen are ports. This Part is
about porting well. Hour 7 walks the nine "native" ports — Go, Java (and its Spring Boot
starter), Swift, Kotlin, Ruby, PHP, .NET, Elixir, Flutter — and shows that each one is the
same five decisions expressed in its language's idiom for concurrency, errors and lifetime.
Hour 8 covers the three that sit on a standard or a special runtime: the OpenFeature
providers, the edge client, React Native. Hour 9 is the cautionary tale: the Lambda
assignment service, a _second implementation of the server_, and what drift between two
deciders costs.

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

The second column is where language taste lives. The first column is the contract, and the
docblock at the top of every client restates it — deliberately, in nearly the same words, so
a reviewer can diff the _promises_ across ports without reading the code.

### How Experimently does it

**Go** — an interface, context-aware calls, errors returned alongside a safe value:

```go
// 📁 sdk/go/client.go
// Client is the main interface for interacting with the Experimentation Platform.
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

`Close()` exists because Go's `http.Transport` keeps idle connections; the Python SDK has no
`close()` because `urllib` keeps none. Same contract, different resource model.

**Java** — a builder-configured client and a Spring Boot auto-configuration so a Spring app
gets an injectable bean from four properties:

```java
// 📁 sdk/java/spring-boot-starter/src/main/java/com/experimentationplatform/sdk/spring/ExperimentationAutoConfiguration.java
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
unit boundary a port introduces and must document (the Lambda has a worse one; Hour 9).

**Swift** — `async throws`, `NSCache`, an offline store, and the same in-flight dedupe as JS:

```swift
// 📁 sdk/ios/Sources/ExperimentationSDK/ExperimentationClient.swift
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
// 📁 sdk/android/sdk/src/main/kotlin/com/experimentationplatform/android/ExperimentationClient.kt
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
# 📁 sdk/ruby/lib/experimentation_platform/client.rb
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
# 📁 sdk/elixir/lib/experimentation_platform/client.ex
  GenServer client for the Experimentation Platform public API.

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
sed -n '/How it works/,/Thread Safety/p' ../ios/Sources/ExperimentationSDK/ExperimentationClient.swift
sed -n '1,40p' ../elixir/lib/experimentation_platform/client.ex
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
 * OpenFeature Provider for the Experimentation Platform.
 *
 * Implements the @openfeature/server-sdk `Provider` interface on top of
 * `@experimentation-platform/js-sdk`. Every evaluation is decided by the
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
/ `ERROR`), and the same docblock rules apply.

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

React Native is the one SDK the harness can't run against a real runtime — "unit tests only
(no device runtime)" in the status table — so its contract smoke is a Jest test under
`testEnvironment: node` that writes its JSON report to a file (Hour 11). The SDK was designed
so that this is possible: its client is plain TypeScript over `fetch`, with the RN-specific
storage behind an interface and mocked in tests.

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

Then read `sdk/edge/src/adapters/deno.ts` and sketch a Cloudflare KV `EdgeStore` (its `get`
/ `put` map directly onto `KVNamespace.get` / `put({expirationTtl})`).

### Key takeaways

- OpenFeature providers compose the JS/Python SDK, map `targetingKey` → `user_id`, resolve
  boolean from `enabled` and typed values from `config`, report `CACHED` on SDK cache hits;
  experiments and tracking go through the underlying client.
- Edge: no Node built-ins, 500 ms timeout, 60 s TTL, memory → KV → network, never throws.
- React Native: JS SDK shape + `AsyncStorage` + `onError`; testable under plain node by
  design.

---

## Hour 9 — The other implementation: Lambda assignment, DynamoDB and the drift problem

### What & why

"The server decides" assumes there is one server. There isn't, quite. `backend/lambda/`
holds a second implementation of assignment and flag evaluation — Python handlers meant to
run behind API Gateway with DynamoDB as their store, built for the P50 < 20 ms / 10k rps
targets the FastAPI service can't promise. It has its own consistent hash, its own targeting
evaluator, its own holdout and mutual-exclusion logic, and its own unit conventions. It's
well-tested (71+ tests) and it is _not the same decider_.

This Hour is about that honestly: what the Lambda does, where it disagrees with the API, and
what the platform does (and doesn't) do about it. The lesson generalises: every second
implementation of a decision is a drift liability, and the cost of drift in an
experimentation system is not a crash — it's an SRM you can't explain.

### How Experimently does it

The Lambda's hash, from the shared module — SDK byte order, SDK-style normalisation, but a
salt convention nobody else uses:

```python
# 📁 backend/lambda/shared/consistent_hash.py
        # Use experiment_key as salt if none provided
        hash_salt = salt or experiment_key

        # Calculate hash for traffic allocation check
        traffic_hash = self._hash(user_id, f"{hash_salt}_traffic")
        traffic_value = traffic_hash / self.MAX_HASH_VALUE

        # Check if user is in traffic allocation
        if traffic_value >= traffic_allocation:
            return None  # User excluded from experiment

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
    def _hash(self, user_id: str, salt: str) -> int:
        """
        Calculate MurmurHash3-like hash for consistent assignment.

        Uses Python's hashlib as a substitute for MurmurHash3.
        In production, consider using mmh3 library for better performance.
        """
        # Combine user_id and salt
        combined = f"{user_id}:{salt}".encode("utf-8")

        # Use MD5 for deterministic hashing (first 4 bytes as uint32)
        hash_bytes = hashlib.md5(combined).digest()[:4]
        hash_value = struct.unpack("<I", hash_bytes)[0]

        return hash_value
```

Compare with the API's assignment (Build book Day 4):

| | FastAPI `AssignmentService` | Lambda `AssignmentService` |
|---|---|---|
| Hash input | `"{user_id}:{experiment.id}"` (UUID) | `"{user_id}:{experiment_key}_variant"` (key + suffix) |
| Normalisation | full 128-bit digest `% 100` | first 4 bytes LE ÷ (2³² − 1) |
| Traffic gate | none (allocation is per-variant) | separate `_traffic` hash vs `traffic_allocation` (0.0–1.0) |
| Allocation units | percent per variant, summing to 100 | fraction per variant, summing to 1.0 |
| Stickiness | `assignments` table (Postgres) | `assignments` table (DynamoDB) |
| Eligibility | holdout → MEG → targeting, via the rules engine + adapter | holdout → MEG → targeting, via the Lambda's own evaluator |
| Ineligible | 200 + `assigned: false` + reason | `{"variant": null, "excluded": true}` |

Two systems given the same user and experiment will, in general, choose _different_
variants. Each is internally consistent (sticky within itself), so a product that used only
one would be fine. A product whose web tier used the API and whose mobile tier used the
Lambda would have users in two variants at once.

The unit drift is the memorable one — the memory file for this repo has carried it for
months:

```python
# 📁 backend/lambda/feature_flag_evaluation/evaluator.py
        rollout_percentage = flag_config.rollout_percentage

        # 0% rollout - no users included
        if rollout_percentage <= 0:
            return False

        # 100% rollout - all users included
        if rollout_percentage >= 100:
            return True

        # Use consistent hashing to determine if user is in rollout
        # Get bucket from 0-99 (100 buckets for percentage)
        bucket = self.hasher.get_bucket(user_id=user_id, experiment_key=flag_config.key, num_buckets=100)

        # User is included if their bucket is less than rollout percentage
        return bucket < rollout_percentage
```

`rollout_percentage` is 0–100 for flags; `traffic_allocation` is 0.0–1.0 for experiments —
in the same Lambda package. Nothing in the type system distinguishes them, and a config
copied from one to the other is silently wrong (a `0.5` rollout is 0%; a `50` allocation is
"everyone, and then some").

What's actually deployed? The CDK compute stack ships a **placeholder**:

```python
# 📁 infrastructure/cdk/stacks/compute_stack.py
        # Create a dummy Lambda function for assignment
        self.assignment_lambda = lambda_.Function(
            self,
            "AssignmentLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            code=lambda_.Code.from_inline(
                "def handler(event, context):\n"
                "    return {\n"
                "        'statusCode': 200,\n"
                "        'body': '{\"message\": \"Assignment Lambda placeholder\"}'\n"
                "    }\n"
            ),
```

and the API Gateway stack routes `GET /assignments` to it. So today: **every SDK calls the
FastAPI service** (the contract), the Lambda assignment code exists and is tested but is
not what the placeholder function runs, and the drift is latent rather than live. That is
the state to preserve until the Lambda is either retired or made a thin proxy — and it's
why the SDK contract (Part I) names `POST /api/v1/tracking/assign` on the API, never the
Gateway path.

The one place the two _do_ meet is real: the bandit scheduler reads DynamoDB counters first
(`get_experiment_counters`) before falling back to Postgres (Build book Day 19). The
counters are written by the event-processor Lambda from the Kinesis stream. That's a data
path, not a decision path — the Lambda counts what the API assigned — and it's the pattern
that _doesn't_ drift: one decider, many readers.

> **Watch Out!** "It has 71 tests" is not "it agrees with the API." The Lambda's tests
> verify the Lambda against itself. There is no cross-implementation test that feeds the same
> `(user, experiment)` to both deciders and asserts the same variant — because they would
> fail. If you're asked to wire the real Lambda handler into CDK, that test is the first
> thing to write, and making it pass means one of the two hash functions changes (with a
> re-bucketing of every live experiment).

> **Did You Know?** The consistent-hash docstring says "MurmurHash3-like … In production,
> consider using mmh3." It never happened, which is lucky: switching the Lambda to Murmur
> would have added a _fourth_ bucket function to the family. The golden vectors pin the SDK
> hash precisely so that a well-meant "performance improvement" can't do this to the SDKs.

### Try It Yourself

```python
>>> import hashlib, struct
>>> user, key, exp_id = "user-123", "checkout_redesign", "3f2a9b1c-0000-4000-8000-000000000001"
>>> api_bucket = int(hashlib.md5(f"{user}:{exp_id}".encode()).hexdigest(), 16) % 100            # FastAPI: variant by cumulative %
>>> lam = struct.unpack("<I", hashlib.md5(f"{user}:{key}_variant".encode()).digest()[:4])[0] / 0xFFFFFFFF   # Lambda: [0,1) vs cumulative fractions
>>> sdk = struct.unpack("<I", hashlib.md5(f"{user}:{key}".encode()).digest()[:4])[0] / 2**32              # SDK utility hash
>>> api_bucket, round(lam, 4), round(sdk, 4)
```

For a 50/50 experiment, count over 10,000 synthetic users how often `api_bucket < 50` and
`lam < 0.5` agree — it's ~50%, i.e. independent. Then run the Lambda's own tests to see
what they _do_ guarantee: `python -m pytest backend/lambda/assignment/tests -q -o addopts=""`.

### Key takeaways

- The Lambda is a second decider with its own hash (`_variant`/`_traffic` suffixed salt,
  ÷ 2³² − 1), its own evaluator and mixed units (0–100 vs 0.0–1.0); it agrees with the API
  ~50% of the time on a 50/50 split.
- The deployed CDK function is a placeholder and all SDKs use the API — the drift is
  latent. Keep it that way until there's a cross-implementation agreement test.
- One decider, many readers (DynamoDB counters feeding the bandit scheduler) is the pattern
  that doesn't drift.

---

## Part III Workshop

**Q1. List the five things a port must preserve exactly.**
Endpoints/bodies/headers; per-user+key cache with TTL, successes only; the fan-out rule;
the failure policy (loud detailed call, safe convenience call, `track` never fails); the
hash and its golden vectors.

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

**Q6. Two teams integrate the same experiment — one via the Python SDK, one via a hand-rolled
call to the Lambda. What goes wrong and how would you detect it?**
Users get different variants per surface (independent hash functions). Detect with an SRM
per surface and a join of `assignments` in Postgres vs DynamoDB on `user_id`.

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

**E3.4 — Edge store.** Implement a Cloudflare KV `EdgeStore` and show, with two simulated
isolates sharing one KV, that the second isolate's first `evaluateFlag` makes no network
call.

**E3.5 — Agreement test.** Write the cross-decider test described in Hour 9: feed 1,000
`(user, experiment)` pairs to the FastAPI `_hash_user_to_variant` and the Lambda's
`assign_variant` with equivalent configs; report the agreement rate; mark it `xfail` with a
reason.

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

**S3.4**

```ts
const kvStore = (ns: KVNamespace): EdgeStore => ({
  get: k => ns.get(k),
  put: (k, v, ttlMs) => ns.put(k, v, { expirationTtl: Math.max(60, Math.ceil(ttlMs / 1000)) }),
});
```

(KV's minimum TTL is 60 s — another unit boundary.) Isolate A evaluates → memory + KV;
isolate B (fresh memory) evaluates → memory miss → KV hit → no `fetch`.

**S3.5** — build an `Experiment` with two 50% variants and a Lambda `ExperimentConfig` with
two 0.5 variants and `traffic_allocation=1.0`; for each user compare the chosen variant's
_position_; agreement ≈ 0.5. `@pytest.mark.xfail(reason="FastAPI and Lambda use different
bucket functions; see SDK book Hour 9", strict=True)` — `strict` so that if someone
unifies them, the test starts failing-to-fail and gets promoted.

---

**Next:** [Part IV — Proving Agreement →](./part-4-proving-agreement.md)

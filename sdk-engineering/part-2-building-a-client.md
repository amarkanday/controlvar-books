# Part II — Building a Client

### Hours 4–6

[← Part I](./part-1-the-contract.md) · [Contents](./README.md) · [Part III →](./part-3-sixteen-sdks.md)

This Part examines the Python, JavaScript and React implementations of the SDK contract. Hour 4
covers Python's synchronous transport and thread-safe client. Hour 5 covers JavaScript in Node and
the browser, including timeouts, concurrent requests and error handling. Hour 6 covers the React
provider, hooks and server-side rendering entry point, with examples from the two demo applications.

---

## Hour 4 — Anatomy of the Python SDK: transport, request, cache, types

### What & why

The Python SDK separates transport, request handling, caching and result types. The transport
interface allows HTTP implementations to be replaced in tests or integrations. A shared request
method handles serialization, response decoding, typed errors and 429 retry. Assignment and flag
caches use `UserKeyCache`. Frozen dataclasses provide named result fields and value equality without
allowing callers to mutate the returned records.

The constraint that shaped it: **no runtime dependencies**. A server-side SDK gets installed
into applications with their own pinned `requests`/`urllib3`/`httpx` versions; a dependency
of our own is a conflict waiting to happen. `urllib.request` is in the standard library and
is enough for four JSON endpoints.

Local mode (Hour 3) adds two more modules, also standard library only: `local.py` (569
lines: the refresh thread, backoff, `ready()`/`status()`, and the counts it sends to
`POST /api/v1/tracking/evaluations`) and `evaluator.py` (495 lines: the operators and the
`md5-mod100-v1` bucket). Nothing in this Hour depends on them: a client created without
`evaluation="local"` never fetches a ruleset.

### How Experimently does it

The transport protocol and its default — note the rule that HTTP errors are _responses_,
not exceptions:

```python
# 📁 sdk/python/experimentation/transport.py
class Transport(Protocol):
    """Anything with ``send(method, url, headers, body, timeout) -> Response``.

    Implementations must return a :class:`Response` for *every* HTTP status
    (including 4xx/5xx) and raise :class:`TransportError` only when no
    response was received.
    """
    ...

class UrllibTransport:
    """Default transport built on :func:`urllib.request.urlopen` (no third-party deps)."""

    def send(self, method, url, headers, body, timeout) -> Response:
        request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as raw:  # noqa: S310 - caller-provided URL
                return Response(raw.status, dict(raw.headers.items()), raw.read())
        except urllib.error.HTTPError as exc:
            # A response *was* received; surface it so the client can inspect status/body.
            headers_out = dict(exc.headers.items()) if exc.headers is not None else {}
            try:
                payload = exc.read()
            except Exception:  # pragma: no cover - defensive
                payload = b""
            return Response(exc.code, headers_out, payload or b"")
        except (OSError, http.client.HTTPException, ValueError) as exc:
            # URLError, socket.timeout/TimeoutError and ConnectionError are OSError
            # subclasses; ValueError covers malformed URLs.
            raise TransportError(f"{method} {url} failed: {exc}") from exc
```

`urllib` raises `HTTPError` for 4xx/5xx by default; the transport catches it and converts it
back into a `Response`, because the _client_ owns the policy for what a 404 or a 429 means.
A transport that decided "404 is an exception" would have put contract semantics in the
wrong layer.

The single request method — URL building, JSON, the retry, the error:

```python
# 📁 sdk/python/experimentation/client.py
    def _request(self, method: str, path: str, *, body=None, query=None) -> Any:
        """Perform one API call and return the decoded JSON body (``None`` if empty).

        Retries exactly once on 429, sleeping for ``Retry-After`` (capped at
        :data:`MAX_RETRY_AFTER_SECONDS`). Raises :class:`ExperimentationError`
        for any non-2xx response, network failure or undecodable body.
        """
        url = self.api_url + path
        if query:
            url += "?" + urlencode(query, quote_via=quote)
        data = None if body is None else json.dumps(body, default=_json_default).encode("utf-8")

        response = self._send(method, url, data)
        if response.status == 429:
            wait = _retry_after_seconds(response)
            logger.debug("429 from %s %s; retrying once after %.1fs", method, path, wait)
            self._sleep(wait)
            response = self._send(method, url, data)

        if response.status < 200 or response.status >= 300:
            raise ExperimentationError(
                f"{method} {path} failed with HTTP {response.status}",
                status=response.status,
                body=response.text(),
            )
        if not response.body:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ExperimentationError(f"{method} {path} returned a non-JSON body", status=response.status, body=response.text()) from exc
```

Two details worth a second look. `urlencode(query, quote_via=quote)` — the default
`quote_plus` would turn the spaces in a JSON `context` into `+`, which the server decodes as
literal plus signs; `quote` percent-encodes. And `self._sleep = time.sleep` as an instance
attribute — tests replace it with a recorder so a 429 test runs in microseconds (Hour 10).

`get_assignment` — cache check, body, request, **validation of the response shape**, cache
write:

```python
# 📁 sdk/python/experimentation/client.py
    def get_assignment(self, experiment_key: str, user_id: str, user_attributes=None) -> Assignment:
        cached = self._assignments.get(user_id, experiment_key)
        if cached is not None:
            return cached

        body: Dict[str, Any] = {"experiment_key": experiment_key, "user_id": user_id}
        if user_attributes is not None:
            body["context"] = dict(user_attributes)
        data = self._request("POST", "/api/v1/tracking/assign", body=body)

        if not isinstance(data, dict) or not isinstance(data.get("variant_name"), str):
            raise ExperimentationError(
                "POST /api/v1/tracking/assign returned an unexpected body",
                status=200,
                body=json.dumps(data),
            )
        assignment = Assignment(
            experiment_key=str(data.get("experiment_key") or experiment_key),
            user_id=str(data.get("user_id") or user_id),
            variant_id=None if data.get("variant_id") is None else str(data["variant_id"]),
            variant_name=data["variant_name"],
            is_control=bool(data.get("is_control", False)),
            configuration=data.get("configuration"),
            # Only taken when the server sent them: a missing ``assigned`` stays
            # None (unknown), never False, so older servers are not mistaken for
            # "not enrolled".
            assigned=data["assigned"] if isinstance(data.get("assigned"), bool) else None,
            reason=data["reason"] if isinstance(data.get("reason"), str) else None,
        )
        self._assignments.set(user_id, experiment_key, assignment)
        return assignment
```

The shape check matters more than it looks: a proxy that returns an HTML 200 error page, or a
misconfigured `api_url` that points at the dashboard, would otherwise produce an
`Assignment` with `variant_name=None` — and _cache it_. The check turns that into a raised
error with the body attached, and the failure is never cached. Note also how `assigned` is
read: only a real boolean is taken, so a server that predates the field yields `None`
("unknown"), never `False` ("not enrolled").

The types:

```python
# 📁 sdk/python/experimentation/types.py
@dataclass(frozen=True)
class Assignment:
    """A user's (sticky) variant assignment, from ``POST /api/v1/tracking/assign``."""

    experiment_key: str
    user_id: str
    variant_id: Optional[str]
    variant_name: str
    is_control: bool
    configuration: Optional[Dict[str, Any]]
    assigned: Optional[bool] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class FlagEvaluation:
    """A server-side flag decision, from ``GET /api/v1/feature-flags/evaluate/{key}``.

    ``reason`` says why the server decided as it did (``"targeting_rule"``,
    ``"rollout"``, ``"inactive"`` or ``"error"``); ``None`` when the server did
    not send one. A local answer carries the reason the server would have given.
    ``source`` is set only with ``evaluation="local"``: ``"local"`` when answered
    in-process from the ruleset, ``"server"`` when the server evaluated it.
    """

    key: str
    enabled: bool
    config: Any = None
    reason: Optional[str] = None
    source: Optional[str] = None
```

And the batch path, with the index arithmetic that keeps error positions meaningful across
chunks:

```python
# 📁 sdk/python/experimentation/client.py
    def track_batch(self, events: Iterable[Mapping[str, Any]]) -> BatchResult:
        """Send raw track bodies via ``POST /api/v1/tracking/batch`` in chunks of 100. Never raises.
        ...
        A chunk that fails outright counts every one of its events as a failure; error
        ``index`` values are absolute positions in ``events``.
        """
        result = BatchResult()
        normalised = [self._normalise_event(event) for event in events]
        for start in range(0, len(normalised), BATCH_LIMIT):
            chunk = normalised[start : start + BATCH_LIMIT]
            try:
                data = self._request("POST", "/api/v1/tracking/batch", body={"events": chunk})
            except Exception as exc:  # noqa: BLE001 - never raise
                logger.warning("track_batch chunk starting at %d failed: %s", start, exc)
                result.failure_count += len(chunk)
                result.errors.append({"index": start, "count": len(chunk), "status": getattr(exc, "status", None), "error": str(exc)})
                continue
            if isinstance(data, dict):
                result.success_count += int(data.get("success_count") or 0)
                result.failure_count += int(data.get("failure_count") or 0)
                for error in data.get("errors") or []:
                    entry = dict(error) if isinstance(error, Mapping) else {"error": error}
                    if isinstance(entry.get("index"), int):
                        entry["index"] += start
                    result.errors.append(entry)
```

> **By the Way** — `ExperimentationClient` is safe to share between threads: the caches
> lock, the transport is stateless, and `urllib` opens a connection per call (no pool to
> corrupt). That's the intended usage — one client per process, created at startup. It has a
> `close()` (and `with ExperimentationClient(...) as client:`), but in server mode it is a
> no-op: there is nothing to release. In local mode `close()` stops the refresh thread and
> sends the evaluation counts not yet sent. A pooled transport (`requests.Session`) can be
> injected by anyone who needs keep-alive; it just has to implement `send`.

> **Watch Out!** `get_all_flags` is **not** cached and **not** part of the fan-out — it
> returns `{flag_key: bool}` with no `config` or `reason`, so there's nothing complete to
> cache, and a key-less `track` would otherwise attribute events to flags the user was never
> shown a decision about. Use `get_feature_flag` for anything you'll track against.

### Try It Yourself

```python
>>> import logging; logging.basicConfig(level=logging.DEBUG)
>>> from experimentation import ExperimentationClient
>>> c = ExperimentationClient("http://localhost:8000", open("tests/sdk-contract/live/.api_key").read().strip())
>>> a = c.get_assignment("sdk_contract_ab", "u-hour4", {"country": "US"}); a
Assignment(experiment_key='sdk_contract_ab', user_id='u-hour4', variant_id='…', variant_name='treatment', is_control=False, configuration=None, assigned=True, reason='assigned')
>>> a == c.get_assignment("sdk_contract_ab", "u-hour4")        # cached, equal (frozen dataclass)
True
>>> c.get_feature_flag("sdk_contract_flag", "u-hour4")
FlagEvaluation(key='sdk_contract_flag', enabled=True, config=None, reason='rollout', source=None)
>>> c.track("u-hour4", "purchase", event_value=9.99)            # fans out to both
True
>>> c.get_feature_flag("nope", "u-hour4")
Traceback (most recent call last): ...
experimentation.types.ExperimentationError: GET /api/v1/feature-flags/evaluate/nope failed with HTTP 404
>>> try: c.get_feature_flag("nope", "u-hour4")
... except Exception as e: print(e.status, e.body)
404 {"detail":"Feature flag with key 'nope' not found"}
```

Then inject a transport that prints every request (`class Spy(UrllibTransport): def
send(self, *a): print(a[0], a[1]); return super().send(*a)`) and watch the cache work.

### Key takeaways

- Four modules: transport (HTTP errors are responses, not exceptions), one `_request`
  (URL, JSON, one 429 retry, typed error), two `UserKeyCache`s, frozen dataclasses.
- Response shapes are validated before caching; a bad 200 raises and is not cached.
- No runtime dependencies; thread-safe; one client per process; inject a transport for
  pooling or tests. `close()` matters only in local mode.
- `get_all_flags` is uncached and outside the fan-out by design.

---

## Hour 5 — The JavaScript SDK: fetch, timeouts, in-flight dedupe, `onError`

### What & why

The JavaScript SDK supports browsers and Node, which impose different transport constraints.
Browser `fetch` needs explicit timeout handling through `AbortController`, and initial page renders
can issue concurrent requests for the same user. Browser cryptography does not provide MD5 through
`crypto.subtle`; injected `fetch` functions also need the correct binding. Node supplies global
`fetch` from version 18, while integrations may inject another implementation. An `onError` hook
makes failures observable for operations such as `track` that do not reject.

The additions over Python, then: an abort-based timeout, **in-flight deduplication** (ten
components asking for the same flag share one request), a custom `fetch` slot, `onError`,
and a `Retry-After` parser that also accepts HTTP-dates.

### How Experimently does it

The class contract:

```ts
// 📁 sdk/js/src/client.ts
/**
 * Client for the Experimently public API.
 *
 * - By default (`evaluation: 'server'`) flag evaluation and experiment assignment
 *   are decided by the server. With `evaluation: 'local'` (server-side code only)
 *   flags are answered in-process from the server's ruleset whenever the answer is
 *   provably the server's, and by the server otherwise; experiments always stay on
 *   the server. `consistentHash` is exported as a utility only; it is not the flag
 *   bucketing function.
 * - Successful evaluations/assignments are cached per user + key for
 *   `cacheTtlMs`; failures are never cached. Concurrent calls for the same
 *   user + key share one in-flight request.
 * - `getAssignment`, `evaluateFlag`, `getAllFlags` and `fetchAssignments` throw
 *   `ExperimentationError`; `getVariant` / `isFeatureEnabled` return safe
 *   defaults instead; `track` / `trackBatch` never reject.
 * - A 429 is retried once after the server's `Retry-After` (capped at `timeoutMs`).
 */
export class ExperimentationClient {
  ...
  /** Requests currently in flight, so concurrent callers share one fetch. */
  private readonly inflight = new Map<string, Promise<unknown>>();
```

Dedupe — the promise is the cache entry until it settles, and it's removed on both success
and failure so a failed request is retried by the next caller (never-cache-failures, applied
to in-flight state):

```ts
// 📁 sdk/js/src/client.ts
  /** Share a single pending promise between concurrent calls for the same key. */
  private dedupe<T>(key: string, run: () => Promise<T>): Promise<T> {
    const pending = this.inflight.get(key) as Promise<T> | undefined;
    if (pending) return pending;
    const promise = run().then(
      value => {
        this.inflight.delete(key);
        return value;
      },
      err => {
        this.inflight.delete(key);
        throw err;
      }
    );
    this.inflight.set(key, promise);
    return promise;
  }
```

The key is `` `flag` + NUL + userId + NUL + flagKey `` (written `\u0000` in the source) — a
NUL separator so a user ID containing a colon can't collide with another user + key pair.
`evaluateFlag` tries a local answer first (local mode only), then the cache, then wraps its
fetch in the dedupe:

```ts
// 📁 sdk/js/src/client.ts
  async evaluateFlag(flagKey: string, user: UserContext): Promise<FlagEvaluation> {
    const { userId, attributes } = requireUser(user);
    if (this.local) {
      const answer = this.local.answer(flagKey, userId, attributes);
      if (answer !== DEFER) {
        const evaluation: FlagEvaluation = {
          key: flagKey,
          enabled: answer.enabled,
          config: null,
          reason: answer.reason,
          source: 'local',
        };
        // Recorded for the key-less track() fan-out, exactly as a server answer is.
        this.flags.set(userId, flagKey, evaluation);
        return evaluation;
      }
    }
    const cached = this.flags.get(userId, flagKey);
    if (cached) return cached;

    return this.dedupe(`flag\u0000${userId}\u0000${flagKey}`, async () => {
      const data = await this.requestJson<FlagEvaluateResponse>(
        `/api/v1/feature-flags/evaluate/${encode(flagKey)}?user_id=${encode(userId)}${contextQuery(attributes)}`
      );
      const evaluation: FlagEvaluation = {
        key: flagKey,
        enabled: Boolean(data?.enabled),
        config: data?.config === undefined ? null : data.config,
      };
      if (typeof data?.reason === 'string') evaluation.reason = data.reason;
      if (this.local) evaluation.source = 'server';
      this.flags.set(userId, flagKey, evaluation);
      return evaluation;
    });
  }
```

The local branch runs
**before** the cache read: a local answer is computed from this call's attributes, so it is
never stale in the way a cached server answer can be. It is still written to the cache, so the
key-less `track()` fan-out attributes events to it like any other evaluation.

The request — abort timeout, unbound fetch, typed error codes, one retry:

```ts
// 📁 sdk/js/src/client.ts
  private async request(path: string, init: RequestInitLite = {}): Promise<Response> {
    const method = init.method ?? 'GET';
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    try {
      // Call fetch unbound: binding it to the client breaks browsers ("Illegal invocation").
      const fetchImpl = this.customFetch ?? globalThis.fetch;
      if (typeof fetchImpl !== 'function') {
        throw new ExperimentationError('No fetch implementation available; pass config.fetch', { code: 'NETWORK_ERROR' });
      }
      response = await fetchImpl(`${this.apiUrl}${path}`, {
        method,
        headers: { 'X-API-Key': this.apiKey, 'Content-Type': 'application/json', Accept: 'application/json' },
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
        signal: controller.signal,
      });
    } catch (err) {
      if (err instanceof ExperimentationError) throw err;
      if (controller.signal.aborted) {
        throw new ExperimentationError(`Request timed out after ${this.timeoutMs} ms (${method} ${path})`, { code: 'TIMEOUT', cause: err });
      }
      const message = err instanceof Error ? err.message : String(err);
      throw new ExperimentationError(`Network error (${method} ${path}): ${message}`, { code: 'NETWORK_ERROR', cause: err });
    } finally {
      clearTimeout(timer);
    }

    if (response.status === 429 && !init.retried) {
      await sleep(this.retryAfterMs(response));
      return this.request(path, { ...init, retried: true });
    }
    return response;
  }
```

The error type has a `code` the Python one doesn't need — in Python the distinction between
"no response" and "bad response" is `status is None`; in JS, where a timeout and a CORS
failure both reject `fetch` with a generic `TypeError`, an explicit code is the only way for
callers to tell them apart:

```ts
// 📁 sdk/js/src/errors.ts
export type ExperimentationErrorCode =
  /** The server answered with a non-2xx status; see `status` and `body`. */
  | 'HTTP_ERROR'
  /** `fetch` rejected (DNS, connection refused, CORS, ...). */
  | 'NETWORK_ERROR'
  /** The request exceeded `timeoutMs`. */
  | 'TIMEOUT'
  /** The server answered 2xx but the body was not what the SDK expects. */
  | 'INVALID_RESPONSE';
```

`onError` — the only window into swallowed failures, and itself guarded so a throwing
handler can't break fire-and-forget. Local mode added three operations to it, all background
work with no caller to reject to:

```ts
// 📁 sdk/js/src/types.ts
export type SwallowedOperation = 'track' | 'trackBatch' | 'refresh' | 'flush' | 'evaluate';
...
  /**
   * Called with the error whenever `track` / `trackBatch` swallow a failure, and in local mode
   * when a ruleset refresh (`'refresh'`), an evaluation-count report (`'flush'`) or a local
   * evaluation (`'evaluate'`, which then goes to the server) fails.
   * Those operations never reject; this is the only way to observe their failures.
   */
  onError?: (error: ExperimentationError, operation: SwallowedOperation) => void;
```

```ts
// 📁 sdk/js/src/client.ts
  private report(err: unknown, operation: SwallowedOperation): void {
    if (!this.onError) return;
    try {
      this.onError(asExperimentationError(err), operation);
    } catch {
      // a throwing error handler must not break fire-and-forget semantics
    }
  }
```

And `Retry-After` in both forms the RFC allows:

```ts
// 📁 sdk/js/src/client.ts
  /** Delay before the single 429 retry: `Retry-After` seconds or HTTP-date, capped at `timeoutMs`. */
  private retryAfterMs(response: Response): number {
    let ms = DEFAULT_RETRY_AFTER_MS;
    const header = typeof response.headers?.get === 'function' ? response.headers.get('Retry-After') : null;
    if (header) {
      const seconds = Number(header);
      if (Number.isFinite(seconds)) {
        ms = seconds * 1000;
      } else {
        const date = Date.parse(header);
        if (!Number.isNaN(date)) ms = date - Date.now();
      }
    }
    return Math.min(Math.max(ms, 0), this.timeoutMs);
  }
```

The fan-out is the same rule, expressed as `expand` — one function that produces either
one keyed body or the list:

```ts
// 📁 sdk/js/src/client.ts
  /** Build the wire bodies for one event: a single keyed body, or the fan-out list. */
  private expand(event: TrackEvent): TrackBody[] {
    const base: TrackBody = { event_type: event.eventType ?? event.eventName, event_name: event.eventName, user_id: event.userId };
    if (event.value !== undefined) base.value = event.value;
    if (event.properties !== undefined) base.metadata = event.properties;
    if (event.timestamp !== undefined) base.timestamp = toIso(event.timestamp);

    if (event.experimentKey || event.featureFlagKey) {
      const body: TrackBody = { ...base };
      if (event.experimentKey) body.experiment_key = event.experimentKey;
      if (event.featureFlagKey) body.feature_flag_key = event.featureFlagKey;
      return [body];
    }

    return [
      ...this.getAssignments(event.userId).map(a => ({ ...base, experiment_key: a.experimentKey })),
      ...this.getEvaluatedFlags(event.userId).map(flagKey => ({ ...base, feature_flag_key: flagKey })),
    ];
  }
```

> **Watch Out!** The retry cap is `timeoutMs`, not a fixed 5 s as in Python — because the
> whole call's budget in a browser is the timeout, and a 4 s `Retry-After` on a 5 s timeout
> would leave 1 s for the retry. Two SDKs, same rule ("retry once, bounded"), different
> bound derived from the environment. That's the kind of divergence that's acceptable;
> Part III is about the kind that isn't.

> **Did You Know?** The JS SDK ships its own 182-line `md5.ts`. Not because MD5 is hard to
> find — because the SDK must run in browsers (no Node `crypto`), at the edge (no Node
> built-ins at all) and synchronously (`crypto.subtle.digest` is async and doesn't do MD5).
> It's the price of exporting `consistentHash` everywhere; the golden vectors are what make
> shipping a hand-rolled hash acceptable.

### Try It Yourself

```bash
cd sdk/js && npm run build --silent && node --input-type=module -e "
import { ExperimentationClient } from './dist/index.js';
import { readFileSync } from 'node:fs';
const c = new ExperimentationClient({ apiUrl: 'http://localhost:8000', apiKey: readFileSync('../../tests/sdk-contract/live/.api_key','utf8').trim(),
  onError: (e, op) => console.log('swallowed', op, e.code, e.status) });
const user = { userId: 'u-hour5', attributes: { country: 'US' } };
const [a, b, c2] = await Promise.all([c.getAssignment('sdk_contract_ab', user), c.getAssignment('sdk_contract_ab', user), c.evaluateFlag('sdk_contract_flag', user)]);
console.log(a === b, a.variantName, c2.enabled, c2.reason);          // same object: deduped
await c.track('u-hour5', 'purchase', { value: 3 });                    // fan-out
await c.track('u-hour5', 'purchase', { experimentKey: 'nope' });       // 404 → onError, no throw
try { await c.evaluateFlag('nope', user) } catch (e) { console.log(e.code, e.status, e.message) }
"
```

Then set `timeoutMs: 1` and observe `code: 'TIMEOUT'`; then `apiUrl: 'http://localhost:1'`
for `NETWORK_ERROR`.

### Key takeaways

- Same semantics as Python plus what the environment demands: `AbortController` timeouts,
  in-flight dedupe keyed by user + key (cleared on settle), injectable `fetch`, `onError`
  for swallowed failures (`track`, `trackBatch`, and in local mode `refresh`, `flush`,
  `evaluate`), a bundled MD5.
- Local mode answers before the cache, and the constructor refuses it in a browser.
- `ExperimentationError.code` ∈ HTTP_ERROR | NETWORK_ERROR | TIMEOUT | INVALID_RESPONSE —
  the distinction `fetch` doesn't give you.
- `Retry-After` accepts seconds or an HTTP-date; the single retry is capped at `timeoutMs`.

---

## Hour 6 — The React SDK: provider, hooks, SSR `ServerClient`, and the demo apps

### What & why

A React integration must represent loading, success and failure as state, then update components
when the result arrives. It also needs a way to share the current user across components. Server-side
rendering introduces a separate lifetime: `getServerSideProps` has no DOM or component hooks and
must not share a user's cached state with unrelated requests.

The React SDK's answers: a **provider** holds one client and one user; **hooks** wrap each
contract call in `useState` + `useEffect` with loading/error states and _safe defaults while
loading_ (control, flag off); a **`ServerClient`** exposes the same calls as never-throwing
promises for SSR; and the client itself is a browser-flavoured port of the JS client (fetch +
abort + dedupe + per-user cache). ShopLab and StreamPulse consume it from source through a
path alias, which is how the SDK gets exercised by real UI on every demo run.

### How Experimently does it

The provider — one client per config, one context value per user _content_:

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

  return <ExperimentationContext.Provider value={value}>{children}</ExperimentationContext.Provider>;
}
```

Both memos are correctness, not optimisation. A client recreated on every render would have
an empty cache on every render — every hook would refetch, and the fan-out would have nothing
to fan out to. And keying the context value on `attributesKey` is how the React SDK solves
the "attributes changed, cache is stale" problem from Hour 3: a new attributes object with
the same content doesn't re-run hooks; changed content does, and the hooks refetch with the
new `context`.

A hook — safe default while loading, the answer when it arrives, the default again (with
`error`) on failure, and a cancellation flag so a stale response can't overwrite a newer
one:

```ts
// 📁 sdk/react/src/hooks/useExperiment.ts
/**
 * Assigns the current user to an experiment via `POST /api/v1/tracking/assign`.
 *
 * While loading or on error the control defaults are returned
 * (`variantKey: 'control'`, `variantName: 'Control'`, `variantId: null`,
 * `isControl: true`, `configuration: null`). Re-assigns when the experiment
 * key or user changes (the server keeps assignments sticky).
 */
export function useExperiment(experimentKey: string): ExperimentAssignment {
  const { client, user } = useExperimentation();
  const [state, setState] = useState<ExperimentAssignment>(() => defaultAssignment(experimentKey, { loading: true }));

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

`defaultAssignment` is the React SDK's version of "return control on failure": the shape a
component renders against is complete at every moment, so `if (hero.variantKey ===
'treatment')` is safe during loading (false), after failure (false, with `error` set) and
after success:

```ts
// 📁 sdk/react/src/client/ExperimentationClient.ts
export function defaultAssignment(experimentKey: string, overrides: Partial<Pick<ExperimentAssignment, 'loading' | 'error'>> = {}): ExperimentAssignment {
  return {
    experimentKey,
    variantKey: 'control',
    variantName: 'Control',
    variantId: null,
    isControl: true,
    configuration: null,
    loading: false,
    error: null,
    ...overrides,
  };
}
```

`useTrackEvent` returns a stable function bound to the current user — stable so it can go in
effect dependency arrays without retriggering:

```ts
// 📁 sdk/react/src/hooks/useTrackEvent.ts
export function useTrackEvent(): TrackEventFn {
  const { client, user } = useExperimentation();

  return useCallback<TrackEventFn>(
    (eventName, properties, options) => {
      void client.trackEvent(user.userId, eventName, properties, options);
    },
    [client, user.userId]
  );
}
```

The SSR entry point — same calls, never throws, no hooks:

```ts
// 📁 sdk/react/src/ssr/index.ts
/**
 * SSR utilities for the Experimently React SDK.
 *
 * Use these utilities in server-side rendering contexts (Next.js getServerSideProps,
 * Remix loaders, etc.) where browser APIs are not available. ServerClient never throws.
 *
 * @example
 * import { ServerClient } from '@getexperimently/react-sdk/ssr';
 *
 * export async function getServerSideProps({ req }) {
 *   const client = new ServerClient({ apiKey: process.env.API_KEY, baseUrl: process.env.API_URL });
 *   const user = { userId: req.session.userId };
 *   const flags = await client.getAll(['new-checkout', 'redesign'], user);
 *   const hero = await client.assignExperiment('shoplab_hero_banner', user);
 *   return { props: { flags, hero } };
 * }
 */
```

```ts
// 📁 sdk/react/src/client/ServerClient.ts
  /** `POST /api/v1/tracking/assign` — never throws; returns the control defaults on failure. */
  async assignExperiment(experimentKey: string, user: UserContext): Promise<ExperimentAssignment> {
    try {
      return await this.inner.assignExperiment(user, experimentKey);
    } catch (err) {
      return defaultAssignment(experimentKey, { error: toError(err) });
    }
  }
```

This is also how the React SDK runs in the live contract harness: "through its SSR entry
point (ServerClient): no DOM, no React render, so it runs under plain node like the other JS
SDKs" (Hour 11).

**The demo apps.** ShopLab mounts the provider once the visitor ID exists (a sticky
assignment needs a stable user ID, so the app deliberately renders nothing until it has one):

```tsx
// 📁 demo/shoplab/src/pages/_app.tsx
export default function ShopLabApp({ Component, pageProps }: AppProps) {
  // The visitor id only exists in the browser, so wait for it before rendering any
  // page: every hook below needs a stable user id for sticky assignments.
  const [visitor, setVisitor] = useState<Visitor | null>(null);
  useEffect(() => {
    setVisitor(getOrCreateVisitor());
  }, []);

  const config = useMemo(() => ({ apiKey: API_KEY, baseUrl: API_URL, timeoutMs: 5000 }), []);
  const user = useMemo(() => (visitor ? { userId: visitor.id, attributes: visitor.attributes } : null), [visitor]);

  return (
    <>
      ...
      {user ? (
        <ExperimentationProvider config={config} user={user}>
          <EventLogProvider>
            <Layout>
              <Component {...pageProps} />
            </Layout>
            <ExperimentlyPanel />
          </EventLogProvider>
        </ExperimentationProvider>
      ) : (
        <div className="min-h-screen bg-neutral-50" aria-busy="true" aria-label="Loading ShopLab" />
      )}
    </>
  );
}
```

and consumes the SDK from source via an alias, so a change to `sdk/react/src` is exercised by
the storefront on the next reload:

```json
// 📁 demo/shoplab/tsconfig.json
      "@getexperimently/react-sdk": [
        "../../sdk/react/src/index.ts"
      ]
```

```js
// 📁 demo/shoplab/next.config.js
const ALIAS_TARGETS = {
  '@getexperimently/react-sdk': SDK_ENTRY,
  // Force a single React copy. ...
  react: path.resolve(__dirname, 'node_modules/react'),
  'react-dom': path.resolve(__dirname, 'node_modules/react-dom'),
};
```

The `react` and `react-dom` entries force one copy of React: without them the SDK source could
resolve React from its own directory and every hook would throw "Invalid hook call". One alias
map, two spellings: webpack takes the absolute paths as they are, and Turbopack
(the default bundler since Next 16) gets them rewritten relative to the package. StreamPulse
uses the same alias.

The checkout page tracks with no key — the fan-out attributes the purchase to every
experiment and flag the storefront showed this visitor:

```ts
// 📁 demo/shoplab/src/pages/checkout.tsx
    track('begin_checkout', { items: cartCount(cart.items), flow }, { value: cartTotal(cart.items) });
    ...
    track('purchase', { items, flow, order_id: orderId }, { value: orderTotal });
```

And the demo's own tests mock the SDK at the package name, with setters for the state a page
needs and a `trackEventMock` to assert on — the pattern for any app that consumes the SDK:

```tsx
// 📁 demo/shoplab/src/__mocks__/experimently-sdk.tsx
/**
 * Manual mock of `@getexperimently/react-sdk` for jest.
 *
 * jest.config.js maps the package name here, so pages/components under test get these
 * hooks instead of the real provider. Tests configure return values with `__setExperiment`,
 * `__setFlag` and `__setUser`, and assert on `trackEventMock`.
 */
export const trackEventMock = jest.fn<void, TrackArgs>();
```

> **Watch Out!** The hooks' `useEffect` depends on `user` by reference — which is why the
> provider memoises the context value by content. If you ever bypass the provider and pass a
> fresh `{ userId }` object each render, every hook refetches on every render (the cache
> saves the network, but you'll see loading flicker). Use the provider.

> **By the Way** — `useMultipleFlags(keys)` serialises its key array to JSON for the effect
> dependency, for the same reason: `['a','b']` is a new array every render. It's a
> recurring React SDK lesson — _identity is not content_ — and the SDK absorbs it so
> integrators don't have to.

### Try It Yourself

```bash
cd sdk/react && npm test -- --silent > /tmp/react-sdk-tests.txt 2>&1; tail -5 /tmp/react-sdk-tests.txt   # hooks, provider, SSR, HOC
cd ../../demo/shoplab && npm run dev                          # http://localhost:3200
```

Open the storefront, open the **Experimently panel** (it uses `useExperiment` ×4 and
`useFeatureFlag` ×2), and in DevTools' Network tab watch: four `/assign` and two `/evaluate`
calls on first load, none on navigation (cached), and one `/batch` with six entries when you
complete a checkout. Then change the visitor's attributes in localStorage and reload — the
provider re-keys, the hooks refetch.

### Key takeaways

- Provider: one memoised client, context value keyed by user _content_ (attributes JSON) —
  the cache survives renders and refreshes when attributes really change.
- Hooks: complete default shape while loading and on error (control / off), cancellation
  guard against stale responses, stable `track` function.
- `ServerClient` = the same calls, never throwing, for SSR and for the contract harness.
- The demo apps consume the SDK from source via an alias and mock it at the package name
  in their tests.

---

## Part II Workshop

**Q1. Why does the Python transport convert `HTTPError` back into a `Response`?**
So the client, not the transport, decides what a 4xx/5xx means (404 → default variant, 429 →
retry). Policy belongs in one layer.

**Q2. A proxy returns `200 OK` with an HTML body for `/tracking/assign`. What does each SDK
do?**
Python: `_request` raises "returned a non-JSON body" (`INVALID_RESPONSE` in JS) — nothing is
cached, `get_variant` returns `"control"`. Without the shape check, a `None` variant would
have been cached for five minutes.

**Q3. Ten components call `useFeatureFlag('x')` on first render. How many requests?**
One. The first call puts a promise in `inflight` under the user + key; the other nine get
the same promise; when it settles, the value is in the cache for the TTL.

**Q4. When is `onError` called, and why can it not throw?**
When `track`/`trackBatch` swallow a failure, and in local mode when a ruleset refresh, an
evaluation-count report or a local evaluation fails — background work with no caller to
reject to; it's the only window into them. It's wrapped in try/catch because a throwing
handler would turn fire-and-forget into a rejection.

**Q5. What does `useExperiment` return between mount and the server's answer, and why is
that safe?**
`defaultAssignment(key, { loading: true })` — control, `isControl: true`, `configuration:
null`. A component branching on `variantKey === 'treatment'` renders control until told
otherwise, which is the right default for an experiment.

**Q6. Why does ShopLab render nothing until it has a visitor ID?**
Sticky assignment needs a stable `user_id`; rendering with a placeholder ID would assign
(and record an exposure for) a user that doesn't exist, then re-assign the real one.

**Quiz.** The provider's `useMemo` for the client depends on `[apiKey, baseUrl, timeoutMs,
cacheTtlMs]`, not `[config]`. What breaks if you "fix" it to `[config]`?
→ Callers passing an inline `config={{...}}` create a new object every render → a new client
every render → empty cache every render → every hook refetches and the fan-out has nothing
cached. The lint suppression is deliberate.

---

## Exercises

**E2.1 — Pooled transport.** Implement `RequestsTransport` (using `requests.Session`) that
satisfies the `Transport` protocol, including the "HTTP errors are responses" rule, and run
the Python SDK's test suite against it via a fixture.

**E2.2 — Dedupe under failure.** With a fake `fetch` that rejects after 50 ms, call
`evaluateFlag` three times concurrently, then once more after they settle. Show 1 request
for the first three and a 2nd request for the fourth (the failed promise was evicted).

**E2.3 — HTTP-date Retry-After.** Make a fake `fetch` return 429 with `Retry-After: <date 2 s
in the future>` then 200; assert the client waited ≈2 s and one retry happened; then with a
date in the past, assert the wait was 0.

**E2.4 — A hook.** Write `useAssignmentConfig(experimentKey, field, fallback)` that returns
`assignment.configuration?.[field] ?? fallback` with the same loading/error semantics, and
test it with the ShopLab mock's `__setExperiment`.

**E2.5 — SSR round trip.** In a Next.js page, assign in `getServerSideProps` with
`ServerClient`, pass the assignment as a prop, and render the variant without a loading
state; then confirm in the DB that only one exposure was recorded even though the client
provider also mounts.

---

## Solutions

**S2.1**

```python
import requests
from experimentation.transport import Response, TransportError

class RequestsTransport:
    def __init__(self): self._s = requests.Session()
    def send(self, method, url, headers, body, timeout):
        try:
            r = self._s.request(method, url, headers=dict(headers), data=body, timeout=timeout)
        except requests.RequestException as exc:
            raise TransportError(f"{method} {url} failed: {exc}") from exc
        return Response(r.status_code, dict(r.headers), r.content)   # 4xx/5xx are responses
```

`requests` doesn't raise on status unless you call `raise_for_status()`, so the rule holds
for free; only connection/timeout errors become `TransportError`.

**S2.2** — `inflight` holds the rejecting promise; all three callers receive the same
rejection; the `err =>` branch deletes the key, so the fourth call runs `fetch` again. Two
requests total.

**S2.3** — `retryAfterMs` takes the `Date.parse(header) - Date.now()` branch; a past date
gives a negative number, clamped to 0 by `Math.max(ms, 0)`; both are capped at `timeoutMs`.

**S2.4**

```ts
export function useAssignmentConfig<T>(experimentKey: string, field: string, fallback: T): T & { loading: boolean } {
  const a = useExperiment(experimentKey);
  const v = (a.configuration as Record<string, unknown> | null)?.[field];
  return Object.assign(v === undefined ? fallback : (v as T), { loading: a.loading });
}
```

**S2.5** — the server-side `assignExperiment` creates the assignment row and one exposure;
when the browser provider mounts and `useExperiment` calls `/assign` again, the server finds
the sticky row and returns it without a new exposure (`AssignmentService.assign_user`
returns existing assignments as-is). One exposure, two calls — the contract's stickiness at
work.

---

**Next:** [Part III — Sixteen SDKs →](./part-3-sixteen-sdks.md)

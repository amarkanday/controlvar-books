# Part II — Building a Client

### Hours 4–6

[← Part I](./part-1-the-contract.md) · [Contents](./README.md) · [Part III →](./part-3-sixteen-sdks.md)

Part I fixed the semantics. This Part reads three real implementations of them, chosen because
each one faces a different environment. The Python SDK (Hour 4) is the reference: synchronous,
dependency-free, thread-safe, with a transport abstraction that makes it testable. The
JavaScript SDK (Hour 5) has to work in Node and in browsers, where `fetch` has no timeout,
requests are concurrent by default, and "never reject" needs an escape hatch. The React SDK
(Hour 6) wraps the same client in a provider and hooks, adds an SSR entry point, and is the
one two demo apps consume from source — the integration the rest of the platform is judged
by. Keep `sdk/python/controlvar/`, `sdk/js/src/` and `sdk/react/src/` open as you read.

---

## Hour 4 — Anatomy of the Python SDK: transport, request, cache, types

### What & why

The Python SDK's server mode is a client of about 940 lines plus a handful of small modules —
and the small modules are the design. **Transport** isolates HTTP so the client can be tested
without a network and users can swap `urllib` for `requests` or `httpx`. **Request** is one
private method pair that turns a `(method, path, body, query)` into decoded JSON or a typed
error, with the 429 retry in exactly one place. **Cache** is the `UserKeyCache` from Hour 3,
twice. **Types** are frozen dataclasses so callers get attribute access, equality (the contract
smoke compares two assignments with `==`) and nothing mutable. Two modules arrived with 0.2.0
and 0.3.0: **attribution** (the 24-hour record of Hour 3) and **exposure** (the one path
between `on_exposure` and the client). (The package on PyPI and on disk is `controlvar`; the
class is still called `ExperimentationClient`, as it is in every SDK.)

The constraint that shaped it: **no runtime dependencies**. A server-side SDK gets installed
into applications with their own pinned `requests`/`urllib3`/`httpx` versions; a dependency
of our own is a conflict waiting to happen. `urllib.request` is in the standard library and
is enough for four JSON endpoints.

Local mode (Hour 3) adds two more modules, also standard library only: `local.py` (575
lines: the refresh thread, backoff, `ready()`/`status()`, fork safety, and the counts it sends
to `POST /api/v1/tracking/evaluations`) and `evaluator.py` (573 lines: the operators, the
`md5-mod100-v1` bucket and the flag values). Nothing in this Hour depends on them: a client
created without `evaluation="local"` never fetches a ruleset.

### How Controlvar does it

The transport protocol and its default — note the rule that HTTP errors are _responses_,
not exceptions:

```python
# 📁 sdk/python/controlvar/transport.py
class Transport(Protocol):
    """Anything with ``send(method, url, headers, body, timeout) -> Response``.

    Implementations must return a :class:`Response` for *every* HTTP status
    (including 4xx/5xx) and raise :class:`TransportError` only when no
    response was received.
    """
    ...

class UrllibTransport:
    """Default transport built on :func:`urllib.request.urlopen` (no third-party deps)."""

    def send(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout: float,
    ) -> Response:
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
            raise TransportError(failure_message(method, url, exc)) from exc
```

`urllib` raises `HTTPError` for 4xx/5xx by default; the transport catches it and converts it
back into a `Response`, because the _client_ owns the policy for what a 404 or a 429 means.
A transport that decided "404 is an exception" would have put contract semantics in the
wrong layer.

The last line changed for a reason worth a sentence. The message used to name the whole URL,
and a flag evaluation's URL carries `?user_id=…&context=…`: the user's id and attributes,
copied into every log line that printed the error. `failure_message` names the URL without its
query, and removes the query from the exception's own text too, because some exceptions
repeat the URL (#1556). An error message is output, and output leaks.

The request path is a pair: `_exchange` sends and retries, `_request` turns the response into
JSON or an error. `report_error` calls `_exchange` directly, because it needs the raw status
(only a 201 means the report was stored); every other call goes through `_request`:

```python
# 📁 sdk/python/controlvar/client.py
    def _exchange(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, str]] = None,
    ) -> Response:
        """Send one API call and return its response, whatever the status.
        ...
        """
        url = self.api_url + path
        if query:
            url += "?" + urlencode(query, quote_via=quote)
        data = None
        if body is not None:
            # Only json.dumps is in the try. A lone surrogate in the query makes urlencode above
            # raise UnicodeEncodeError, also a ValueError, and that keeps its own path.
            try:
                encoded = json.dumps(body, default=_json_default, allow_nan=False)
            except ValueError as exc:
                raise ExperimentationError(
                    f"{method} {path} was not sent: its body holds a value JSON cannot represent ({exc})"
                ) from exc
            data = encoded.encode("utf-8")

        response = self._send(method, url, data)
        if response.status == 429:
            wait = _retry_after_seconds(response)
            logger.debug("429 from %s %s; retrying once after %.1fs", method, path, wait)
            self._sleep(wait)
            response = self._send(method, url, data)
        return response

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
        query: Optional[Mapping[str, str]] = None,
    ) -> Any:
        """Perform one API call and return the decoded JSON body (``None`` if empty).
        ...
        """
        response = self._exchange(method, path, body=body, query=query)
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
            raise ExperimentationError(
                f"{method} {path} returned a non-JSON body",
                status=response.status,
                body=response.text(),
            ) from exc
```

Three details worth a second look. `urlencode(query, quote_via=quote)` — the default
`quote_plus` would turn the spaces in a JSON `context` into `+`, which the server decodes as
literal plus signs; `quote` percent-encodes. `allow_nan=False` — Python's `json.dumps`
happily writes `NaN` and `Infinity`, which are not JSON; the SDK now refuses to send such a
body at all (a `track(event_value=float("nan"))` returns `False` and sends nothing, #1760), and
the server answers 422 to anyone else who sends one. And `self._sleep = time.sleep` as an
instance attribute — tests replace it with a recorder so a 429 test runs in microseconds
(Hour 10).

`get_assignment` — cache check, body, request, **validation of the response shape**, cache
write, the fan-out record, and last of all the exposure hook:

```python
# 📁 sdk/python/controlvar/client.py
    def get_assignment(
        self,
        experiment_key: str,
        user_id: str,
        user_attributes: Optional[Mapping[str, Any]] = None,
    ) -> Assignment:
        ...
        cached = self._assignments.get(user_id, experiment_key)
        if cached is not None:
            return cached

        body: Dict[str, Any] = {"experiment_key": experiment_key, "user_id": user_id}
        if user_attributes is not None:
            body["context"] = dict(user_attributes)
        try:
            data = self._request("POST", "/api/v1/tracking/assign", body=body)
        except ExperimentationError as exc:
            # A 404: a key-less track() must no longer reach this experiment.
            if exc.status == 404:
                self._attributed.forget_experiment(user_id, experiment_key)
            raise

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
        new = self._assignments.set_new(user_id, experiment_key, assignment)
        self._attributed.record_experiment(user_id, assignment.experiment_key, assignment.reason)
        # Last, once the answer is stored and recorded, with no lock held. The server recorded an
        # exposure for this request only if it said ``assigned`` True (a missing field is not
        # taken as True). Threads that asked for the same new user and experiment at once each
        # sent a request; only the one whose answer filled the empty slot calls the hook.
        if new and assignment.assigned is True:
            self._exposed(user_id, assignment)
        return assignment
```

The shape check matters more than it looks: a proxy that returns an HTML 200 error page, or a
misconfigured `api_url` that points at the dashboard, would otherwise produce an
`Assignment` with `variant_name=None` — and _cache it_. The check turns that into a raised
error with the body attached, and the failure is never cached. Note also how `assigned` is
read: only a real boolean is taken, so a server that predates the field yields `None`
("unknown"), never `False` ("not enrolled").

The last three statements are 0.2.0 and 0.3.0, and each answers a concurrency or ordering
question. `set_new` checks and stores under one lock and says whether the slot was empty: the
Python client has no in-flight dedupe (threads, not promises), so two threads asking for the
same new user at once each send a request, the server records each, and only the one whose
answer filled the slot calls the hook. The hook counts answers the client stored, not
requests, which is the one difference from JavaScript, where the two calls would have shared
one request in the first place. The hook runs after the answer is cached and recorded, with no
lock held, so a slow or re-entrant hook can neither block other threads nor see a half-stored
answer. And a 404 removes the experiment from the fan-out record, so a key-less event never
credits an arm the server no longer serves.

The types:

```python
# 📁 sdk/python/controlvar/types.py
@dataclass(frozen=True)
class Assignment:
    """A user's (sticky) variant assignment, from ``POST /api/v1/tracking/assign``.

    ``assigned`` is ``True`` when the server enrolled the user and ``False`` when
    it did not (global holdout, mutual exclusion group or targeting rules) and
    returned the control variant instead, with no exposure recorded. ``reason``
    says why: ``"assigned"``, ``"holdout"``, ``"mutual_exclusion"`` or
    ``"targeting"``. Both are ``None`` when the server predates the fields; such
    a server cannot tell you, so do not read ``None`` as either answer. For an
    experiment that is not ACTIVE, ``assigned`` is ``False`` and ``reason`` is its
    status (``"draft"``, ``"paused"``, ``"completed"`` or ``"archived"``).
    """

    experiment_key: str
    user_id: str
    variant_id: Optional[str]
    variant_name: str
    is_control: bool
    configuration: Optional[Dict[str, Any]]
    assigned: Optional[bool] = None
    reason: Optional[str] = None
...
@dataclass(frozen=True)
class FlagEvaluation:
    """A server-side flag decision, from ``GET /api/v1/feature-flags/evaluate/{key}``.

    ``reason`` says why the server decided as it did (``"targeting_rule"``,
    ``"rollout"``, ``"inactive"`` or ``"error"``); ``None`` when the server did
    not send one. A local answer carries the reason the server would have given.
    ``source`` is set only with ``evaluation="local"``: ``"local"`` when answered
    in-process from the ruleset, ``"server"`` when the server evaluated it.
    ...
    ``value`` and ``value_type`` come after the older fields, so code that
    builds a ``FlagEvaluation`` by position keeps working.
    """

    key: str
    enabled: bool
    config: Any = None
    reason: Optional[str] = None
    source: Optional[str] = None
    value: Any = None
    value_type: Optional[str] = None
```

The last sentence of that docstring is a compatibility rule you will meet again in every
port: **new fields go at the end, with defaults**. A dataclass is also a positional
constructor, so `FlagEvaluation("f", True, None, "rollout")` written against 0.1 still builds
the same object against 0.3.

The typed getters (`get_string_value` and its two siblings) share one check, and its order is
the design: no value at all, then the wrong type, then a value that does not match the type it
claims. Each failure is a different `code`, so a caller's `on_error` can tell "the server is
too old" from "you asked a string flag for a number":

```python
# 📁 sdk/python/controlvar/client.py
def _served_value(evaluation: FlagEvaluation, flag_key: str, value_type: str, method: str) -> Any:
    """The value ``evaluation`` serves when it is of ``value_type`` (a copy); otherwise raises
    :class:`ExperimentationError` with ``code`` ``"INVALID_RESPONSE"`` or ``"TYPE_MISMATCH"``."""
    flag = json.dumps(str(flag_key), ensure_ascii=False)
    value = evaluation.value
    if value is None:
        raise ExperimentationError(
            f"Flag {flag} came back without a value (from a server without flag values, or one that "
            f"cannot serve this flag's stored value); {method} returned your default",
            code="INVALID_RESPONSE",
        )
    served = evaluation.value_type if isinstance(evaluation.value_type, str) else _json_type_of(value)
    if served != value_type:
        raise ExperimentationError(
            f"Flag {flag} serves {_describe_type(served)}; {method} returned your default",
            code="TYPE_MISMATCH",
        )
    if not is_value_of_type(value, value_type):
        raise ExperimentationError(
            f"Flag {flag} serves {_describe_type(value_type)}, but the server's value is not one; "
            f"{method} returned your default",
            code="INVALID_RESPONSE",
        )
    return copy_value(value)
```

The getter that calls it catches everything, logs, passes the error to
`on_error(error, "value")` and returns the default; the messages all end "returned your
default", so a log line says what the caller got as well as why.

And the batch path, with the index arithmetic that keeps error positions meaningful across
chunks:

```python
# 📁 sdk/python/controlvar/client.py
    def track_batch(self, events: Iterable[Mapping[str, Any]]) -> BatchResult:
        """Send raw track bodies via ``POST /api/v1/tracking/batch`` in chunks of 100. Never raises.
        ...
        ``event_type`` defaults to ``event_name`` when missing. A chunk that
        fails outright counts every one of its events as a failure; error
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
                result.errors.append(
                    {
                        "index": start,
                        "count": len(chunk),
                        "status": getattr(exc, "status", None),
                        "error": str(exc),
                    }
                )
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

> **By the Way** — `ExperimentationClient` is safe to share between threads: the caches and
> the fan-out record lock, the transport is stateless, and `urllib` opens a connection per
> call (no pool to corrupt). `on_exposure` runs in the calling thread, and an `async def` hook
> is refused at construction, because the client would never await it and its body would
> never run. That's the intended usage — one client per process, created at startup. It has a
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
>>> from controlvar import ExperimentationClient
>>> c = ExperimentationClient("http://localhost:8000", open("tests/sdk-contract/live/.api_key").read().strip())
>>> a = c.get_assignment("sdk_contract_ab", "u-hour4", {"country": "US"}); a
Assignment(experiment_key='sdk_contract_ab', user_id='u-hour4', variant_id='…', variant_name='treatment', is_control=False, configuration=None, assigned=True, reason='assigned')
>>> a == c.get_assignment("sdk_contract_ab", "u-hour4")        # cached, equal (frozen dataclass)
True
>>> c.get_feature_flag("sdk_contract_flag", "u-hour4")
FlagEvaluation(key='sdk_contract_flag', enabled=True, config=None, reason='rollout', source=None, value=True, value_type='boolean')
>>> c.get_string_value("sdk_contract_value_string", "u-hour4", default="dflt")
'on-copy'
>>> c.get_number_value("sdk_contract_flag", "u-hour4", default=0)   # a boolean flag: TYPE_MISMATCH, logged
0
>>> c.track("u-hour4", "purchase", event_value=9.99)            # fans out to all three
True
>>> c.track("u-hour4", "purchase", event_value=float("nan"))    # refused before sending
False
>>> c.get_feature_flag("nope", "u-hour4")
Traceback (most recent call last): ...
controlvar.types.ExperimentationError: GET /api/v1/feature-flags/evaluate/nope failed with HTTP 404
>>> try: c.get_feature_flag("nope", "u-hour4")
... except Exception as e: print(e.status, e.body)
404 {"detail":"Feature flag with key 'nope' not found"}
```

Then inject a transport that prints every request (`class Spy(UrllibTransport): def
send(self, *a): print(a[0], a[1]); return super().send(*a)`) and watch the cache work.

### Key takeaways

- Small modules: transport (HTTP errors are responses, not exceptions; error messages carry no
  query string), one `_exchange`/`_request` pair (URL, JSON without NaN, one 429 retry, typed
  error), two `UserKeyCache`s, the fan-out record, frozen dataclasses with new fields last.
- Response shapes are validated before caching; a bad 200 raises and is not cached.
- `on_exposure` is called once per stored `assigned: true` answer, after it is stored, with
  no lock held; typed getters check value, type, then shape, and return the caller's default.
- No runtime dependencies; thread-safe; one client per process; inject a transport for
  pooling or tests. `close()` matters only in local mode.
- `get_all_flags` is uncached and outside the fan-out by design.

---

## Hour 5 — The JavaScript SDK: fetch, timeouts, in-flight dedupe, `onError`

### What & why

The JS SDK implements the same contract for two hostile-in-different-ways environments.
**Browsers**: `fetch` has no timeout (you need `AbortController`), a page fires many
concurrent calls for the same user on first render, `crypto.subtle` has no MD5 and is
async anyway, and "call `fetch` bound to an object" throws `Illegal invocation`. **Node**: the
global `fetch` exists from 18 onward but integrators on older runtimes or with `undici`
wrappers need to inject their own. And in both, `track` "never rejects" means an unhandled
failure is invisible — so there's an `onError` hook.

The additions over Python, then: an abort-based timeout, **in-flight deduplication** (ten
components asking for the same flag share one request), a custom `fetch` slot, `onError`,
and a `Retry-After` parser that also accepts HTTP-dates. The 0.2.0 and 0.3.0 features are the
same as Python's under camelCase names: `onExposure`, `eventId`, `reportError` and the typed
getters `getStringValue`, `getNumberValue` and `getJsonValue`.

### How Controlvar does it

The class contract:

```ts
// 📁 sdk/js/src/client.ts
/**
 * Client for the Controlvar public API.
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
 * - Every successful answer also records its key for the key-less `track()`
 *   fan-out, for 24 hours, or the cache lifetime if longer (see `attribution.ts`); `clearCache()` keeps that record.
 * - `getAssignment`, `evaluateFlag`, `getAllFlags` and `fetchAssignments` throw
 *   `ExperimentationError`; `getVariant` / `isFeatureEnabled` return safe
 *   defaults instead; `getStringValue` / `getNumberValue` / `getJsonValue` return
 *   the caller's default and pass the reason to `onError(err, 'value')`;
 *   `track` / `trackBatch` / `reportError` never reject.
 * - A 429 is retried once after the server's `Retry-After` (capped at `timeoutMs`).
 */
export class ExperimentationClient {
  ...
  /** The keys a key-less `track()` is sent to; outlives the cache entries. */
  private readonly attributed: AttributedKeys;
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
          // A copy: a caller changing it must not change the ruleset every user is answered from.
          value: copyValue(answer.value),
          valueType: answer.valueType,
          source: 'local',
        };
        // Recorded for the key-less track() fan-out, exactly as a server answer is.
        this.flags.set(userId, flagKey, evaluation);
        this.attributed.recordFlag(userId, flagKey);
        return evaluation;
      }
    }
    // Only a server answer is reused here. A local one stays in the cache (getCachedFlags), but
    // it was computed from a ruleset that no longer answers this call (discarded, too stale, closed,
    // the flag now remote or gone, or attributes it cannot compare), so the server is asked (#1709).
    const cached = this.flags.get(userId, flagKey);
    if (cached && cached.source !== 'local') return cached;

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
      // Only copied when the server sent them: an older server's answer has neither.
      if (data?.value !== undefined && data.value !== null) evaluation.value = data.value;
      if (typeof data?.value_type === 'string') evaluation.valueType = data.value_type;
      if (this.local) evaluation.source = 'server';
      this.flags.set(userId, flagKey, evaluation);
      this.attributed.recordFlag(userId, flagKey);
      return evaluation;
    });
  }
```

The local branch runs **before** the cache read: a local answer is computed from this call's
attributes, so it is never stale in the way a cached server answer can be. It is still written
to the cache (`getCachedFlags` returns it) and to the fan-out record. And the cache read below
it skips a local entry: the one line `cached.source !== 'local'` is the whole of the #1712
fix of Hour 3. If the ruleset declined this call, the cached local answer came from a ruleset
that no longer applies, and only the server may answer. The `copyValue` comment is the other
quiet rule: a JSON value is an object, and handing out the ruleset's own object would let one
caller's mutation change the answer every other user gets.

The request — abort timeout, unbound fetch, typed error codes, one retry:

```ts
// 📁 sdk/js/src/client.ts
  private async request(path: string, init: RequestInitLite = {}): Promise<Response> {
    const method = init.method ?? 'GET';
    const url = `${this.apiUrl}${path}`;
    const shown = withoutQuery(path);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response: Response;
    try {
      // Call fetch unbound: binding it to the client breaks browsers ("Illegal invocation").
      const fetchImpl = this.customFetch ?? globalThis.fetch;
      if (typeof fetchImpl !== 'function') {
        throw new ExperimentationError('No fetch implementation available; pass config.fetch', {
          code: 'NETWORK_ERROR',
        });
      }
      response = await fetchImpl(url, {
        method,
        headers: {
          ...(init.headers ?? {}),
          'X-API-Key': this.apiKey,
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: init.body === undefined ? undefined : JSON.stringify(init.body),
        signal: controller.signal,
      });
    } catch (err) {
      if (err instanceof ExperimentationError) throw err;
      if (controller.signal.aborted) {
        throw new ExperimentationError(
          `Request timed out after ${this.timeoutMs} ms (${method} ${shown})`,
          { code: 'TIMEOUT', cause: err }
        );
      }
      let message = err instanceof Error ? err.message : String(err);
      // A fetch implementation may name the URL in its own message, as passed or as the URL
      // parser writes it (`'` as `%27`, for one). The query is removed in both forms.
      for (const query of [path.slice(shown.length), serialisedQuery(url)]) {
        if (query.length > 1) message = message.split(query).join('');
      }
      throw new ExperimentationError(`Network error (${method} ${shown}): ${message}`, {
        code: 'NETWORK_ERROR',
        cause: err,
      });
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

`shown` is the same fix as Python's `failure_message`, with one more case: a `fetch` may quote
the URL as its own parser rewrote it, so the query is removed in both spellings.

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
  | 'INVALID_RESPONSE'
  /**
   * A typed getter (`getStringValue`, `getNumberValue`, `getJsonValue`) was called for a flag
   * that serves another type; it returned the caller's default.
   */
  | 'TYPE_MISMATCH'
  /**
   * Only passed to `onError`, with the operation `'exposure'`: your `onExposure` threw, or the
   * Promise it returned rejected. `cause` is what it threw; the answer is unchanged.
   */
  | 'HOOK_ERROR';
```

`onError` — the only window into swallowed failures, and itself guarded so a throwing
handler can't break fire-and-forget. Every feature that added a "never rejects" method added
an operation to it: local mode three (background work with no caller to reject to), then
error reports, typed getters and the exposure hook:

```ts
// 📁 sdk/js/src/types.ts
export type SwallowedOperation =
  | 'track'
  | 'trackBatch'
  | 'refresh'
  | 'flush'
  | 'evaluate'
  | 'reportError'
  | 'value'
  | 'exposure';
...
  /**
   * Called with the error whenever `track` / `trackBatch` / `reportError` swallow a failure,
   * whenever a typed getter returns the caller's default (`'value'`), whenever `onExposure`
   * throws or its Promise rejects (`'exposure'`), and in local mode when a ruleset refresh
   * (`'refresh'`), an evaluation-count report (`'flush'`) or a local evaluation (`'evaluate'`,
   * which then goes to the server) fails.
   * Those operations never reject; this is the only way to observe their failures.
   */
  onError?: (error: ExperimentationError, operation: SwallowedOperation) => void;
```

The exposure hook is where JavaScript needs more care than Python. A hook may be `async`, and
the client deliberately does not wait for it, so a rejected Promise nobody handles would end a
Node process as an unhandled rejection. `callExposureHook` attaches the rejection handler
itself:

```ts
// 📁 sdk/js/src/exposure.ts
  try {
    const result: unknown = hook(exposure);
    if (result !== null && (typeof result === 'object' || typeof result === 'function')) {
      // Read `then` once, inside the try: a getter that throws is the hook's failure too.
      const then = (result as { then?: unknown }).then;
      if (typeof then === 'function') then.call(result, undefined, reportSafely);
    }
  } catch (thrown) {
    reportSafely(thrown);
  }
```

Python made the opposite choice for the same problem: it refuses an `async def` hook at
construction, because a coroutine that is never awaited never runs. Same contract (a hook's
failure must never change or delay an answer, in the words of
`sdk/python/controlvar/exposure.py`), two idioms.

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

The fan-out is the same rule, expressed as `expand` — one function that produces one keyed
body, the list, or the single body with no key:

```ts
// 📁 sdk/js/src/client.ts
  /**
   * Build the wire bodies for one event: a single keyed body, the fan-out list, or,
   * with nothing to attribute it to, the single body with no key.
   */
  private expand(event: TrackEvent): TrackBody[] {
    const base: TrackBody = {
      event_type: event.eventType ?? event.eventName,
      event_name: event.eventName,
      user_id: event.userId,
    };
    if (event.value !== undefined) base.value = event.value;
    if (event.properties !== undefined) base.metadata = event.properties;
    if (event.timestamp !== undefined) base.timestamp = toIso(event.timestamp);
    // The caller's id, copied into every fan-out entry below. None is made here.
    if (event.eventId !== undefined && event.eventId !== null && event.eventId !== '') {
      base.event_id = event.eventId;
    }

    if (event.experimentKey || event.featureFlagKey) {
      const body: TrackBody = { ...base };
      if (event.experimentKey) body.experiment_key = event.experimentKey;
      if (event.featureFlagKey) body.feature_flag_key = event.featureFlagKey;
      return [body];
    }

    const { experiments, flags } = this.attributed.keys(event.userId);
    const bodies: TrackBody[] = [
      ...experiments.map(experimentKey => ({ ...base, experiment_key: experimentKey })),
      ...flags.map(flagKey => ({ ...base, feature_flag_key: flagKey })),
    ];
    return bodies.length > 0 ? bodies : [base];
  }
```

Before the fix the last statement read the cache (`getAssignments`, `getEvaluatedFlags`) and
returned an empty list for a user whose answers had expired; `track` then sent nothing. Now the
record decides, and an empty record yields `[base]`, which `track` sends to `/track`.

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
  onError: (e, op) => console.log('swallowed', op, e.code, e.status),
  onExposure: x => console.log('exposure', x.experimentKey, x.variantName) });
const user = { userId: 'u-hour5', attributes: { country: 'US' } };
const [a, b, c2] = await Promise.all([c.getAssignment('sdk_contract_ab', user), c.getAssignment('sdk_contract_ab', user), c.evaluateFlag('sdk_contract_flag', user)]);
console.log(a === b, a.variantName, c2.enabled, c2.reason, c2.value, c2.valueType);   // same object: deduped; one 'exposure' line
console.log(await c.getStringValue('sdk_contract_value_string', user, 'dflt'));      // 'on-copy'
console.log(await c.getNumberValue('sdk_contract_flag', user, 0));                   // 0, and 'swallowed value TYPE_MISMATCH'
await c.track('u-hour5', 'purchase', { value: 3, eventId: 'order-5' });               // fan-out, one id in every entry
await c.track('u-hour5', 'purchase', { experimentKey: 'nope' });       // 404 → onError, no throw
try { await c.evaluateFlag('nope', user) } catch (e) { console.log(e.code, e.status, e.message) }
"
```

The two concurrent `getAssignment` calls share one request, so the server records one exposure
and `onExposure` prints once. Then set `timeoutMs: 1` and observe `code: 'TIMEOUT'`; then
`apiUrl: 'http://localhost:1'` for `NETWORK_ERROR`, and note that neither message contains
`user_id` or `context`.

### Key takeaways

- Same semantics as Python plus what the environment demands: `AbortController` timeouts,
  in-flight dedupe keyed by user + key (cleared on settle), injectable `fetch`, `onError`
  for swallowed failures (`track`, `trackBatch`, `reportError`, `value`, `exposure`, and in
  local mode `refresh`, `flush`, `evaluate`), a bundled MD5.
- Local mode answers before the cache, the cache never reuses a local answer, and the
  constructor refuses local mode in a browser.
- `ExperimentationError.code` ∈ HTTP_ERROR | NETWORK_ERROR | TIMEOUT | INVALID_RESPONSE |
  TYPE_MISMATCH | HOOK_ERROR — the distinctions `fetch` doesn't give you.
- An async `onExposure` is not awaited, and its rejection is caught; a deduped call does not
  call the hook twice.
- `Retry-After` accepts seconds or an HTTP-date; the single retry is capped at `timeoutMs`.

---

## Hour 6 — The React SDK: provider, hooks, SSR `ServerClient`, and the demo apps

### What & why

React changes the shape of the problem. A component can't `await` in render; it needs a
value _now_ (loading), then a value _later_ (the answer), and it re-renders when either
changes. The user is app-wide state, not a parameter. And the same code may run on the
server (Next.js `getServerSideProps`) where there is no DOM, no hooks and no place to keep
a per-session cache.

The React SDK's answers: a **provider** holds one client and one user; **hooks** wrap each
contract call in `useState` + `useEffect` with loading/error states and _safe defaults while
loading_ (control, flag off); a **`ServerClient`** exposes the same calls as never-throwing
promises for SSR; and the client itself is a browser-flavoured port of the JS client (fetch +
abort + dedupe + per-user cache). ShopLab and StreamPulse consume it from source through a
path alias, which is how the SDK gets exercised by real UI on every demo run.

### How Controlvar does it

The provider — one client per config, one context value per user _content_, and (since the
React SDK's first release, 0.1.0) an optional `bootstrap`:

```tsx
// 📁 sdk/react/src/context/ExperimentationProvider.tsx
export function ExperimentationProvider({ config, user, bootstrap, children }: ProviderProps) {
  // The client below only ever sees the wrapper, so the Provider refuses a
  // value that is not a function itself, as the client's constructor would.
  const onExposure = checkOnExposure(config.onExposure);
  // The latest render's onExposure, set during render (an effect does not run
  // in a server render, and useLayoutEffect warns there), so the wrapper calls
  // it without the client being recreated when an inline function changes.
  const exposureRef = useRef(onExposure);
  exposureRef.current = onExposure;

  // Stabilise the client — only recreate when a config primitive changes, so
  // callers may pass an inline config object without losing the cache.
  const client = useMemo(
    () =>
      new ExperimentationClient({
        ...config,
        // Returns what the hook returns, so the client's guard sees a Promise
        // it returns and handles its rejection.
        onExposure: (exposure: Exposure) => exposureRef.current?.(exposure),
      }),
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

  // From the props alone, so the server render and the hydration decide the same.
  const read = useMemo(() => readBootstrap(bootstrap, user.userId), [bootstrap, user.userId]);
  ...
  return (
    <ExperimentationContext.Provider value={value}>
      <BootstrapContext.Provider value={read}>{children}</BootstrapContext.Provider>
    </ExperimentationContext.Provider>
  );
}
```

Both memos are correctness, not optimisation. A client recreated on every render would have
an empty cache on every render — every hook would refetch, and the fan-out would have nothing
to fan out to. And keying the context value on `attributesKey` is how the React SDK solves
the "attributes changed, cache is stale" problem from Hour 3: a new attributes object with
the same content doesn't re-run hooks; changed content does, and the hooks refetch with the
new `context`.

The `onExposure` wiring is the same problem one level up. An inline `onExposure={...}` is a
new function every render, and putting it in the client's memo would recreate the client (and
empty its cache) on every render. So the client gets one stable wrapper, and the wrapper calls
whatever function the latest render left in a ref. The comment says why the ref is written
during render rather than in an effect: effects do not run in a server render.

`bootstrap` is the answer to a flicker. A server-rendered page that assigns in the browser
first paints control (the loading default), then repaints the treatment when `/assign`
answers. `ServerClient.bootstrap({ flags, experiments }, user)` asks the server for those
answers during the server render and returns them as plain JSON; passed to the provider, each
hook whose key it holds _starts_ from the server's answer. Three guards keep it honest: it is
used only when its `userId` is the provider's (otherwise it is ignored with a warning, so
answers computed for one user are never shown to another), it is read from the props alone
(so the server render and the hydration decide the same and React sees no mismatch), and each
entry must have the shape of the route's own response, or it is ignored with a warning.

A hook — safe default while loading (or the bootstrapped answer), the answer when it arrives,
the default again (with `error`) on failure, and a cancellation flag so a stale response can't
overwrite a newer one:

```ts
// 📁 sdk/react/src/hooks/useExperiment.ts
/**
 * Assigns the current user to an experiment via `POST /api/v1/tracking/assign`.
 *
 * While loading or on error the control defaults are returned
 * (`variantKey: 'control'`, `variantName: 'Control'`, `variantId: null`,
 * `isControl: true`, `configuration: null`). When the Provider's `bootstrap`
 * has this experiment, it starts from that assignment with `loading: false`
 * instead, then asks the server as always; if that request fails it keeps the
 * bootstrapped assignment, with `error` set. Re-assigns when the experiment
 * key or user changes (the server keeps assignments sticky).
 */
export function useExperiment(experimentKey: string): ExperimentAssignment {
  const { client, user } = useExperimentation();
  const bootstrap = useBootstrap();
  const [seed] = useState<Seed<ExperimentAssignment> | null>(() => {
    const value = bootstrap.experiments.get(experimentKey);
    return value ? { key: experimentKey, user, value } : null;
  });
  const [state, setState] = useState<ExperimentAssignment>(
    () => seed?.value ?? defaultAssignment(experimentKey, { loading: true })
  );
  // The bootstrapped assignment while the hook still shows it; cleared by its
  // own answer and by a change of key or user.
  const seeded = useRef(seed);

  useEffect(() => {
    let cancelled = false;
    const kept =
      seeded.current && seeded.current.key === experimentKey && seeded.current.user === user
        ? seeded.current.value
        : null;
    if (!kept) {
      seeded.current = null;
      setState(defaultAssignment(experimentKey, { loading: true }));
    }

    client
      .assignExperiment(user, experimentKey)
      .then(assignment => {
        if (!cancelled) {
          seeded.current = null;
          setState(assignment);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const error = err instanceof Error ? err : new Error(String(err));
          setState(kept ? { ...kept, error } : defaultAssignment(experimentKey, { error }));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [client, user, experimentKey]);

  return state;
}
```

Notice that a bootstrapped hook still sends its `/assign` after hydration, and that request is
recorded: the server stores an exposure for every `/assign` it answers with `assigned: true`,
for a returning user too ("the user recorded as seen in it on every call", in
`AssignmentService._sticky_result`). The live contract (Hour 11) pins exactly that: one
exposure row after the server render, one more after hydration. So the bootstrap changes what
the user _sees first_, never what the server _records_. The React SDK also keeps its own
exposure hook out of the server render: `onExposure` is never called during one.

`defaultAssignment` is the React SDK's version of "return control on failure": the shape a
component renders against is complete at every moment, so `if (hero.variantKey ===
'treatment')` is safe during loading (false), after failure (false, with `error` set) and
after success:

```ts
// 📁 sdk/react/src/client/ExperimentationClient.ts
export function defaultAssignment(
  experimentKey: string,
  overrides: Partial<Pick<ExperimentAssignment, 'loading' | 'error'>> = {}
): ExperimentAssignment {
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
 * SSR utilities for the Controlvar React SDK.
 *
 * Use these utilities in server-side rendering contexts (Next.js getServerSideProps,
 * Remix loaders, etc.) where browser APIs are not available. ServerClient never throws.
 *
 * @example
 * import { ServerClient } from '@controlvar/react-sdk/ssr';
 *
 * export async function getServerSideProps({ req }) {
 *   const client = new ServerClient({ apiKey: process.env.API_KEY, baseUrl: process.env.API_URL });
 *   const user = { userId: req.session.userId };
 *   const flags = await client.getAll(['new-checkout', 'redesign'], user);
 *   const hero = await client.assignExperiment('shoplab_hero_banner', user);
 *   return { props: { flags, hero } };
 * }
 *
 * `client.bootstrap({ flags, experiments }, user)` returns the answers in the
 * form `ExperimentationProvider`'s `bootstrap` prop takes, so the page's first
 * render shows them.
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
            <ControlvarPanel />
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
      "@controlvar/react-sdk": [
        "../../sdk/react/src/index.ts"
      ]
```

```js
// 📁 demo/shoplab/next.config.js
const ALIAS_TARGETS = {
  '@controlvar/react-sdk': SDK_ENTRY,
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

The storefront's "Controlvar panel" shows what the SDK did, and it changed in one telling way.
It used to call `useExperiment` for all four experiments so that it could display them, which
assigned every visitor to all four — and recorded an exposure for each — on any page. Now it
assigns nothing; it reads the experiments the pages have assigned from the client
(`useAssignedExperiments` in `demo/shoplab/src/lib/assignments.ts`, over
`client.getAssignments`), and evaluates only the two flags it displays:

```tsx
// 📁 demo/shoplab/src/components/ControlvarPanel.tsx
  // The panel assigns no experiment: an assignment records that the visitor saw the
  // experiment, so only the page that uses one assigns it. The panel shows what the
  // pages have assigned. Flags have no such record and are evaluated here.
  const assigned = useAssignedExperiments();
  const newSearch = useFeatureFlag(FLAG_KEYS.newSearch);
  const freeShipping = useFeatureFlag(FLAG_KEYS.freeShippingBanner);
```

That is the exposure rule from the integrator's side: **an assignment is a measurement**. A
debugging widget that assigns users to show them their variants is enrolling them in
experiments they never saw, and diluting every result.

The checkout page tracks with no key — the fan-out attributes the purchase to every
experiment and flag the storefront answered for this visitor in the last 24 hours:

```ts
// 📁 demo/shoplab/src/pages/checkout.tsx
    track('begin_checkout', { items: cartCount(cart.items), flow }, { value: cartTotal(cart.items) });
    ...
    track('purchase', { items, flow, order_id: orderId }, { value: orderTotal });
```

And the demo's own tests mock the SDK at the package name, with setters for the state a page
needs and a `trackEventMock` to assert on — the pattern for any app that consumes the SDK:

```tsx
// 📁 demo/shoplab/src/__mocks__/controlvar-sdk.tsx
/**
 * Manual mock of `@controlvar/react-sdk` for jest.
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

Open the storefront and DevTools' Network tab. The home page sends one `/assign` (its hero
experiment) and one `/evaluate` per distinct flag on the page and in the **Controlvar panel**
(the free-shipping banner flag is used by both, and the dedupe makes it one request). Each
page that renders an experiment assigns it when you first visit it; nothing is re-sent on a
return visit while the cache holds the answer. Complete a checkout and watch one `/batch`
whose entries are exactly the experiments and flags you were answered — open the panel to
compare. Then change the visitor's attributes in localStorage and reload — the provider
re-keys, the hooks refetch.

### Key takeaways

- Provider: one memoised client, context value keyed by user _content_ (attributes JSON) —
  the cache survives renders and refreshes when attributes really change; `onExposure`
  reaches the client through a ref, so an inline hook does not recreate it.
- Hooks: complete default shape while loading and on error (control / off), or the
  bootstrapped answer; cancellation guard against stale responses; stable `track` function.
- `ServerClient` = the same calls, never throwing, for SSR and for the contract harness;
  `ServerClient.bootstrap()` gives the provider the server's answers for the first paint.
- Every `/assign` answered `assigned: true` records an exposure; assign only what you render.
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
When `track`/`trackBatch`/`reportError` swallow a failure, when a typed getter returns the
caller's default, when `onExposure` throws or rejects, and in local mode when a ruleset
refresh, an evaluation-count report or a local evaluation fails — work with no caller to
reject to; it's the only window into them. It's wrapped in try/catch because a throwing
handler would turn fire-and-forget into a rejection.

**Q4b. Ten components mount at once and each calls `getAssignment('hero', user)` for a new
user. How many times is `onExposure` called?**
Once. The ten calls share one in-flight request, the server records one exposure, and the hook
runs inside that one request's handler. A later call is answered from the cache and calls
nothing. (In Python, which has no dedupe, two threads may send two requests; `set_new` makes
sure only the one that filled the empty slot calls the hook.)

**Q5. What does `useExperiment` return between mount and the server's answer, and why is
that safe?**
`defaultAssignment(key, { loading: true })` — control, `isControl: true`, `configuration:
null` — unless the provider's `bootstrap` holds the experiment, in which case the server's
answer with `loading: false`. A component branching on `variantKey === 'treatment'` renders
control until told otherwise, which is the right default for an experiment.

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

**E2.5 — SSR round trip.** In a Next.js page, call `ServerClient.bootstrap({ experiments:
['sdk_contract_ab'] }, user)` in `getServerSideProps`, pass the result as the provider's
`bootstrap`, and render the variant without a loading state; then count in the DB the
assignment rows and the exposure events for that user after the server render and again after
hydration.

---

## Solutions

**S2.1**

```python
import requests
from controlvar.transport import Response, TransportError

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

**S2.5** — after the server render: one assignment row and one exposure event. After
hydration: still one assignment row, and two exposure events. The hook's `/assign` finds the
sticky row and returns the same variant (`AssignmentService.assign_user` returns existing
assignments as-is), but `_sticky_result` records the user as seen again on every call that
asks for it. One assignment, two exposures — stickiness is about the _variant_, not about how
often the user was seen. (An earlier edition of this book said one exposure; the live contract
now pins two, in the React bootstrap row of Hour 11.)

---

**Next:** [Part III — Sixteen SDKs →](./part-3-sixteen-sdks.md)

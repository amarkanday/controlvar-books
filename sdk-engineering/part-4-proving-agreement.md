# Part IV — Proving Agreement

### Hours 10–12

[← Part III](./part-3-sixteen-sdks.md) · [Contents](./README.md)

The SDK test suites check agreement at several levels. Hour 10 covers fake transports, recorded
requests and the generated corpus for local evaluation. Hour 11 covers the `contract_smoke` entry
points and the live-backend runner. Hour 12 reviews coverage limits, contract changes and SDK releases.
Passing an offline test and passing a live integration test provide different evidence; both are
useful when maintaining clients in several languages.

---

## Hour 10 — Unit testing a client: fake transports and recorded requests

### What & why

A client's unit tests should never touch the network, but they must test the network
_behaviour_: what's sent (method, path, headers, body, query encoding), how each response
status is handled, what's cached, what's retried. The tool for that is a **fake transport** —
a drop-in for the HTTP layer that records requests and serves canned responses — and the
design rule that makes it possible is the transport seam from Hour 4: the client depends on
`send(method, url, headers, body, timeout) → Response`, so anything with that shape can stand
in.

The pattern pays off in three ways. Tests are fast (the Python SDK's whole suite — 187 tests
at the reference commit, local mode included — runs in about a second). Tests are _precise_ — "the second call made no request" is an assertion on
`transport.calls()`, not an inference from timing. And the same fake is exported for
integrators (`experimentation.testing`), so an application's tests can drive the real SDK
against scripted responses instead of mocking the SDK's methods and hoping the mock matches.

### How Experimently does it

The fake, with its three resolution layers (FIFO queue → route table → 404):

```python
# 📁 sdk/python/experimentation/testing.py
class FakeTransport:
    """Records requests and serves canned responses (thread-safe).

    Responses are resolved in this order:

    1. a queued outcome from :meth:`respond` / :meth:`fail` (FIFO);
    2. a route registered with :meth:`route` / :meth:`route_error`, matched on
       method + path (query string ignored);
    3. otherwise a ``404`` with a JSON ``detail``.
    """
    ...
    def respond(self, status: int = 200, json: Any = None, body=None, headers=None) -> "FakeTransport":
        """Queue one response for the next request."""
    def fail(self, exc: Exception) -> "FakeTransport":
        """Queue an exception to be raised by the next request."""
    def route(self, method: str, path: str, status: int = 200, json: Any = None, body=None, headers=None) -> "FakeTransport":
        """Serve a persistent response for ``method path``."""
    ...
    @property
    def last(self) -> RecordedRequest: ...
    def calls(self) -> List[str]:
        """``["POST /api/v1/tracking/assign", ...]`` in order."""
```

The queue is for sequences ("500 then 200"; "429 then 200"); routes are for steady state
("assign always returns treatment"); the default 404 means an unexpected call fails the
test instead of silently succeeding. `RecordedRequest` parses the URL so assertions read
naturally:

```python
# 📁 sdk/python/experimentation/testing.py
@dataclass
class RecordedRequest:
    """One request seen by :class:`FakeTransport`."""

    method: str
    url: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None
    timeout: float = 0.0

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    @property
    def query(self) -> Dict[str, str]:
        return dict(parse_qsl(urlsplit(self.url).query, keep_blank_values=True))

    def json(self) -> Any:
        return None if self.body is None else _json.loads(self.body.decode("utf-8"))
```

Now the contract rules as tests. Stickiness and cache scoping:

```python
# 📁 sdk/python/tests/test_client.py
    def test_sticky_cache_hit_makes_one_request(self, client, transport):
        assign_route(transport)
        first = client.get_assignment("checkout_flow", "user-1")
        second = client.get_assignment("checkout_flow", "user-1")
        assert first is second
        assert transport.calls() == ["POST /api/v1/tracking/assign"]

    def test_cache_is_per_user_and_key(self, client, transport):
        assign_route(transport)
        client.get_assignment("checkout_flow", "user-1")
        client.get_assignment("checkout_flow", "user-2")
        client.get_assignment("hero_banner", "user-1")
        assert len(transport.requests) == 3
```

Failures raise with status and are never cached:

```python
# 📁 sdk/python/tests/test_client.py
    def test_404_raises_with_status_and_body(self, client, transport):
        transport.respond(404, json={"detail": "Active experiment with key 'nope' not found"})
        with pytest.raises(ExperimentationError) as info:
            client.get_assignment("nope", "user-1")
        assert info.value.status == 404
        assert "not found" in info.value.body
        assert "404" in str(info.value)

    def test_failure_is_never_cached(self, client, transport):
        transport.respond(500, body=b"boom")
        assign_route(transport)
        with pytest.raises(ExperimentationError):
            client.get_assignment("checkout_flow", "user-1")
        assert client.get_assignment("checkout_flow", "user-1").variant_name == "treatment"
        assert len(transport.requests) == 2
```

Time without sleeping — the cache's clock is a module-level indirection so a test can move
it:

```python
# 📁 sdk/python/experimentation/cache.py
# Indirection so tests can patch ``experimentation.cache._now``.
_now = time.monotonic
```

```python
# 📁 sdk/python/tests/test_client.py
    def test_cache_expires_after_ttl(self, client, transport):
        flag_route(transport)
        with mock.patch("experimentation.cache._now") as now:
            now.return_value = 1000.0
            client.get_feature_flag("new_search", "user-1")
            now.return_value = 1299.0
            client.get_feature_flag("new_search", "user-1")
            assert len(transport.requests) == 1
            now.return_value = 1300.0
            client.get_feature_flag("new_search", "user-1")
            assert len(transport.requests) == 2
```

And the retry, with `_sleep` replaced so the test runs in microseconds and asserts the
_wait_ rather than experiencing it:

```python
# 📁 sdk/python/tests/test_client.py
    @pytest.fixture
    def no_sleep(self, client):
        client._sleep = mock.Mock()
        return client._sleep

    def test_retries_once_after_retry_after(self, client, transport, no_sleep):
        transport.respond(429, headers={"Retry-After": "2"}).respond(200, json=FLAG_RESPONSE)
        ...
        no_sleep.assert_called_once_with(2.0)

    @pytest.mark.parametrize(
        "header, expected",
        [({"Retry-After": "60"}, 5.0), ({}, 1.0), ({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, 1.0)],
    )
    def test_retry_after_is_capped_or_defaulted(self, client, transport, no_sleep, header, expected):
```

(That third case documents a deliberate simplification: the Python SDK treats an HTTP-date
`Retry-After` as unparseable and uses the 1 s default; the JS SDK parses it. Both are
"bounded single retry"; the test pins which one this port does.)

The JS SDK does the same with a fake `fetch` — the seam there is `config.fetch` / the global
— and its dedupe test is the one that can't be written any other way:

```ts
// 📁 sdk/js/__tests__/client.test.ts
  it('shares one in-flight request between concurrent callers', async () => {
    const fetchMock = mockFetch(assignment);
    const client = new ExperimentationClient(baseConfig);
    const [a, b] = await Promise.all([client.getAssignment('checkout', user), client.getAssignment('checkout', user)]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
  });
```

Its fixtures spell out the wire format so the tests double as documentation — the
URL-encoded context is a literal:

```ts
// 📁 sdk/js/__tests__/client.test.ts
const user: UserContext = { userId: 'user-123', attributes: { device: 'mobile', country: 'US' } };
/** `encodeURIComponent(JSON.stringify(user.attributes))` */
const userContext = '%7B%22device%22%3A%22mobile%22%2C%22country%22%3A%22US%22%7D';
const expectedHeaders = {
  'X-API-Key': 'test-api-key',
  'Content-Type': 'application/json',
  Accept: 'application/json',
};
```

**A corpus generated from the server.** Local evaluation (Hour 3) makes a stronger promise
than any hand-written fixture can check: _for every input, the SDK either asks the server or
gives exactly the server's answer_. Hand-written cases would only prove that the SDK agrees
with whoever wrote them. So the cases are not written by hand: they are **generated by
running the server's own evaluator**.

```python
# 📁 backend/scripts/generate_ruleset_vectors.py
Every answer in the file comes from the server. For each corpus flag the
generator builds the ruleset entry with
:mod:`backend.app.services.sdk_ruleset` (what ``GET /api/v1/sdk/ruleset``
serves), and for each case it runs the flag service's own
``FeatureFlagService.evaluate_flag_detailed`` -- the body of
``/feature-flags/evaluate/{key}`` -- on the same stored flag, user and
context. Nothing in the file is written by hand.
```

`tests/sdk-contract/ruleset-vectors.json` holds 2,172 cases over 144 flags at the reference
commit; 1,294 are marked `must_local`. Each SDK's suite asserts both directions — every case
is deferred or answered exactly, every `must_local` case is answered locally, and nothing
else is — so an SDK that "plays safe" by deferring everything fails as surely as one that
guesses:

```ts
// 📁 sdk/js/__tests__/evaluator.test.ts
  test('every must_local case is answered locally', () => {
    const missed = results.filter(({ testCase, result }) => testCase.must_local && result === DEFER).map(r => r.testCase.id);
```

The JavaScript suite (`evaluator.test.ts`, `local.test.ts`) and the Python suite
(`test_evaluator.py`, `test_local.py`) run it; on the backend,
`backend/tests/unit/services/test_sdk_ruleset_vectors.py` regenerates the file in-process and
fails if the committed copy is stale, so a change to the rules engine that moves any answer
cannot land without the corpus — and therefore the SDKs — seeing it. It is the golden-vector
idea from Hour 2 applied to a whole evaluator: one file, produced by the reference, read by
every implementation.

> **By the Way** — the test names are the contract, phrased as behaviours: "is sticky: the
> second call is served from the cache without a request", "never caches a failure — the
> next call re-fetches", "throws ExperimentationError with status 404 when the experiment is
> not ACTIVE". When you port the SDK, port the test _names_ first; they're the checklist
> from Hour 7, and a port whose suite has the same names is a port you can review by diff.

> **Watch Out!** A fake transport tests the client's side of the contract, not the server's.
> If the backend changed the assign response to `variantName` (camelCase), every SDK unit
> test would still pass — the fixtures are hand-written. That gap is exactly what Hour 11
> closes. Unit tests prove "the client does what we designed"; only the live harness proves
> "what we designed is what the server serves."

### Try It Yourself

```bash
cd sdk/python && python -m pytest -q -o addopts="" > /tmp/py-sdk.txt 2>&1; tail -1 /tmp/py-sdk.txt   # 187 passed at the reference commit
cd ../js && npm ci && npx jest --silent > /tmp/js-sdk.txt 2>&1; grep '^Tests:' /tmp/js-sdk.txt      # 184 passed at the reference commit
```

Then write a test for a rule that isn't covered yet — say, "a 2xx with an empty body from
`/tracking/track` counts as accepted" — using `transport.route("POST",
"/api/v1/tracking/track", status=204)` and asserting `client.track(...) is True`. Run it;
read `_request`'s `if not response.body: return None` to see why it passes.

### Key takeaways

- The transport seam makes the client testable offline; `FakeTransport` resolves queue →
  route → 404 and records parsed requests; it's exported for integrators.
- Contract rules become assertions on `calls()`, `last.json()`, `last.query`, exception
  `status`, and the patched clock / `_sleep`.
- JS does the same through a fake `fetch`; dedupe is only testable this way.
- Local evaluation is held to a corpus generated by the server's own evaluator, in both
  directions (answer exactly, or defer; never defer a `must_local` case).
- Unit tests prove the client matches the design; they can't prove the design matches the
  server.

---

## Hour 11 — The live contract harness: `contract_smoke`, the runner, and the CI job

### What & why

The SDK changes followed failures that isolated client tests had missed: requests used nonexistent
endpoints, and local assignment used a different hash from the backend. Live-backend tests exercise
the connection between the implementations. They check persisted assignment stickiness, flag
evaluation, accepted events and fan-out for events without an experiment key.

The design has three parts. Every SDK has a **`contract_smoke`** entry point: a small
program in the SDK's own language that runs the four calls and prints **one JSON line** to
stdout (anything else to stderr), exit 0 on success. A **runner** (`run_live_contract.py`)
knows how to invoke each one, checks the backend is up, runs them with a seeded API key,
validates the JSON, and prints a PASS/FAIL/SKIP table. And a **CI job** starts Postgres,
Redis and the backend, seeds fixtures, and runs the runner with `--strict` for every SDK
whose toolchain is available on Linux — as a required check on every pull request.

The "one JSON line" contract is what makes sixteen SDKs testable by one Python script (378
lines at the reference commit): the runner never parses Go or Swift output formats, only
JSON. Since local evaluation arrived it does two more things, both on the server's side of
the contract. **Before** the SDKs it checks `GET /api/v1/sdk/ruleset` once: the plain key
gets 403, the `sdk:ruleset` key gets 200 with the contract flag marked `local`, and sending
the ETag back gets 304 (the `ruleset (server)` row). **After** the SDKs it runs each SDK's
local smoke (the `python (local)` and `js (local)` rows) and reads the database around it,
through the backend's own `SafetyService.get_error_metrics`: no evaluate call may have reached
the server, and the safety denominator must have grown by exactly the number of local
evaluations. That is why the runner now needs the backend's Python environment and the
`POSTGRES_*` variables.

### How Experimently does it

The smoke for Python, which sets the pattern — note step 1 _clears the cache between two
assigns_ so the second call really reaches the server, which is the only way to prove
stickiness is the server's and not the client cache's:

```python
# 📁 sdk/python/examples/contract_smoke.py
    client = ExperimentationClient(api_url, api_key)

    # 1. Assign twice. The cache is cleared in between so the second call really
    #    goes to the server and proves the assignment is sticky there.
    try:
        first = client.get_assignment(experiment_key, user_id, attributes)
        client.clear_cache()
        second = client.get_assignment(experiment_key, user_id, attributes)
    except ExperimentationError as exc:
        fail(f"assign failed: {exc} (status={exc.status}, body={exc.body!r})")
    if first.variant_name not in ("control", "treatment"):
        fail(f"unexpected variant_name {first.variant_name!r}")
    sticky = first == second
    if not sticky:
        fail(f"assignment not sticky: {first.variant_name} then {second.variant_name}")

    # 2. Evaluate the flag.
    try:
        flag = client.get_feature_flag(flag_key, user_id)
    except ExperimentationError as exc:
        fail(f"flag evaluation failed: {exc} (status={exc.status}, body={exc.body!r})")
    ...
    # 3. Track with an experiment key -> POST /tracking/track.
    if not client.track(user_id, "purchase", event_value=12.5, experiment_key=experiment_key):
        fail("track(purchase) with experiment_key was not accepted")

    # 4. Track without a key -> fans out to the cached assignment + flag via /tracking/batch,
    #    then an explicit 2-event batch.
    if not client.track(user_id, "page_view", properties={"page": "/smoke"}):
        fail("track(page_view) without a key was not accepted")
    ...
    print(json.dumps({
        "sdk": "python",
        "assign": {"variant_name": first.variant_name, "is_control": first.is_control, "sticky": sticky},
        "flag": {"enabled": flag.enabled},
        "track": {"ok": True},
        "fanout": {"ok": True},
    }, separators=(",", ":")))
```

Step 4 depends on step 1 and 2 having populated the cache — the smoke tests the fan-out rule
in the only way it can be tested: by tracking after assigning. A smoke that tracked first
would send nothing and (correctly) report `False`.

The runner's manifest — one shell command and its required executables per SDK, with
comments where a runtime needed a trick:

```python
# 📁 tests/sdk-contract/live/run_live_contract.py
# sdk name -> (shell command run from the repo root, required executables)
MANIFEST: dict[str, tuple[str, tuple[str, ...]]] = {
    "python": ("python sdk/python/examples/contract_smoke.py", ("python",)),
    "openfeature-python": ("python sdk/openfeature-python/examples/contract_smoke.py", ("python",)),
    "js": ("cd sdk/js && npm run build --silent && node examples/contract_smoke.mjs", ("node", "npm")),
    ...
    # The React SDK through its SSR entry point (ServerClient): no DOM, no React
    # render, so it runs under plain node like the other JS SDKs.
    "react": ("cd sdk/react && npm run build --silent && node examples/contract_smoke.mjs", ("node", "npm")),
    # React Native ships no build output and its client is plain TypeScript over
    # fetch, so the smoke is a Jest test under `testEnvironment: node` (no device,
    # no emulator, no Metro). Jest owns stdout, so its output goes to stderr and
    # the report is read back from the file the test writes.
    "react-native": (
        "cd sdk/react-native && npx jest --config jest.contract.config.js --runInBand 1>&2 "
        "&& cat .contract_smoke.json",
        ("node", "npx"),
    ),
    "go": ("cd sdk/go && go run ./examples/contract_smoke", ("go",)),
    "java": ("bash sdk/java/examples/contract_smoke.sh", ("java",)),  # Maven via ./mvnw
    # The Android SDK's Kotlin sources touch no android.* API, so sdk/android/jvm
    # compiles them for a plain JVM and the smoke runs on a Linux runner with no
    # Android SDK and no emulator (the Gradle/AAR build still runs nightly).
    "android": ("bash sdk/android/examples/contract_smoke.sh", ("java",)),  # Maven via ./mvnw
    "ios": ("cd sdk/ios && swift run -q contract-smoke", ("swift",)),
    "ruby": ("ruby -Isdk/ruby/lib sdk/ruby/examples/contract_smoke.rb", ("ruby",)),
    "php": ("php sdk/php/examples/contract_smoke.php", ("php",)),
    "dotnet": ("dotnet run --project sdk/dotnet/examples/ContractSmoke", ("dotnet",)),
    "elixir": ("cd sdk/elixir && mix run examples/contract_smoke.exs", ("mix",)),
    "flutter": ("cd sdk/flutter && dart run example/contract_smoke.dart", ("dart",)),
}

# SDKs with a local-evaluation mode -> the command that runs their local smoke
# (with EXPERIMENTLY_LOCAL_API_KEY set). Run after every SDK above, because the
# errors it posts count against the shared contract flag.
LOCAL_MANIFEST: dict[str, tuple[str, tuple[str, ...]]] = {
    "python": ("python sdk/python/examples/local_eval_smoke.py", ("python",)),
    "js": ("cd sdk/js && npm run build --silent && node examples/local_eval_smoke.mjs", ("node", "npm")),
}
```

Java and Android need only `java`: each module brings its own Maven through `./mvnw`, so the
runner image does not have to.

The validation — every field the JSON must carry, with the reason each one exists:

```python
# 📁 tests/sdk-contract/live/run_live_contract.py
EXPECTED_VARIANTS = {"control", "treatment"}
...
def validate(sdk: str, payload: dict) -> list[str]:
    problems: list[str] = []
    if payload.get("sdk") != sdk:
        problems.append(f"sdk field is {payload.get('sdk')!r}, expected {sdk!r}")
    assign = payload.get("assign") or {}
    if assign.get("variant_name") not in EXPECTED_VARIANTS:
        problems.append(f"assign.variant_name={assign.get('variant_name')!r}")
    if not isinstance(assign.get("is_control"), bool):
        problems.append("assign.is_control is not a bool")
    if assign.get("sticky") is not True:
        problems.append("assignment was not sticky")
    flag = payload.get("flag") or {}
    if flag.get("enabled") is not True:
        problems.append(f"flag.enabled={flag.get('enabled')!r} (seeded flag is 100% on)")
    if (payload.get("track") or {}).get("ok") is not True:
        problems.append("track failed")
    if (payload.get("fanout") or {}).get("ok") is not True:
        problems.append("fan-out track failed")
    return problems
```

`is_control` must be a _bool_ — a port that serialised it as `"false"` would fail here, which
is a real class of bug in string-typed languages. `flag.enabled` must be `True` because the
seeded flag is at 100%: the harness tests the flag path end to end, not just that a response
came back.

And the run: last stdout line only (build tools chatter before it), stderr's last line as
the failure detail, a per-SDK timeout that includes builds. The subprocess part lives in
`_run_command`, which returns a 3-tuple (status, detail, the parsed JSON object), so that
other checks can reuse it; `run_one` adds the validation. Abridged where marked `...`:

```python
# 📁 tests/sdk-contract/live/run_live_contract.py
def _run_command(command: str, env: dict[str, str], timeout: int) -> tuple[str, str, dict]:
    """Run a smoke command; (status, detail, the JSON object on its last stdout line)."""
    started = time.time()
    ...
    except subprocess.TimeoutExpired:
        return "FAIL", f"timed out after {timeout}s", {}
    elapsed = time.time() - started
    stdout_lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or stdout_lines or ["(no output)"])[-1]
        return "FAIL", f"exit {proc.returncode}: {tail[:300]}", {}
    if not stdout_lines:
        return "FAIL", "no JSON line on stdout", {}
    try:
        payload = json.loads(stdout_lines[-1])
    except json.JSONDecodeError:
        return "FAIL", f"last stdout line is not JSON: {stdout_lines[-1][:200]}", {}
    if not isinstance(payload, dict):
        return "FAIL", f"last stdout line is not a JSON object: {stdout_lines[-1][:200]}", {}
    return "PASS", f"{elapsed:.1f}s", payload


def run_one(sdk: str, command: str, env: dict[str, str], timeout: int) -> tuple[str, str]:
    """Return (status, detail) where status is PASS / FAIL."""
    status, detail, payload = _run_command(command, env, timeout)
    if status != "PASS":
        return status, detail
    problems = validate(sdk, payload)
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", f"{payload['assign']['variant_name']} in {detail}"
```

The fixtures come from one seed script — an ACTIVE experiment `sdk_contract_ab` with
control/treatment, an ACTIVE flag `sdk_contract_flag` at 100%, and two API keys: a plain one
written to `tests/sdk-contract/live/.api_key` (or `EXPERIMENTLY_API_KEY`), and one with the
`sdk:ruleset` scope written to `tests/sdk-contract/live/.api_key_local` (or
`EXPERIMENTLY_LOCAL_API_KEY`). Two keys, so the run can show the ruleset refusing the first
and serving the second.

The CI job is now a call to a reusable workflow, `_platform.yml`, which owns the whole boot
sequence (services, migrations, the seed, the API, a check that the API is running the
profile it was asked for) so that it lives in one place:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
  sdk-live-contract:
    needs: changes
    if: ${{ !cancelled() }}
    name: SDK Live Contract
    uses: ./.github/workflows/_platform.yml
    with:
      skip: ${{ needs.changes.outputs.docs_only == 'true' }}
      profile: core
      seed: sdk-contract
      suite: sdk-live-contract
      sdks: >-
        python openfeature-python js openfeature edge react react-native
        go java android ruby php dotnet
```

and the called workflow turns the list into `--sdk` flags:

```yaml
# 📁 .github/workflows/_platform.yml
          case "$SUITE" in
            sdk-live-contract)
              args=""
              for sdk in $SDKS; do args="$args --sdk $sdk"; done
              # shellcheck disable=SC2086
              python tests/sdk-contract/live/run_live_contract.py --strict $args
              ;;
```

`--strict` with an explicit list is the right shape for CI: a missing toolchain is a
_failure_ (someone broke the runner image), but only the SDKs the image can run are listed,
so Flutter, Elixir and iOS don't produce permanent red. That list is thirteen of the sixteen
SDKs. The job is a required check, `SDK Live Contract / sdk-live-contract (core)`; on a
docs-only change the caller still runs and `skip` turns every step into a no-op, because a
skipped caller would never report the required name at all.

> **Watch Out!** The smoke uses a fresh random `user_id` per run (`smoke-<uuid>`) so runs
> don't inherit stickiness from each other — a rerun with the same user would pass the
> "sticky" check trivially. Set `CONTRACT_USER_ID` only when debugging a specific user.

> **Did You Know?** The job's Java setup is used twice — for the Java SDK and for the Android
> SDK's JVM build (`sdk/android/jvm/` compiles the same Kotlin sources for a plain JVM). That
> is how Android got into the CI list without an Android SDK or an emulator on the runner:
> the port was designed with a platform-free core (Hour 7), and the harness only needed a
> JDK.

### Try It Yourself

```bash
source venv/bin/activate
export APP_ENV=development POSTGRES_SERVER=localhost POSTGRES_DB=experimentation POSTGRES_SCHEMA=experimentation
python backend/scripts/seed_sdk_contract.py
uvicorn backend.app.main:app --port 8000 &
python tests/sdk-contract/live/run_live_contract.py --sdk python --sdk js --sdk go
# PASS ruleset (server)     403 unscoped, 200 scoped (… flags), 304 on ETag
# PASS python               treatment in 0.4s
# PASS js                   control in 3.1s
# PASS go                   treatment in 1.8s
# PASS python (local)       … local evaluations, 0 evaluate calls; denominator +…, errors +…, error rate …
# PASS js (local)           …
```

(Timings, variants and counts vary per run; the shape is what matters.)

Then break the contract deliberately: in the backend, rename `variant_name` to
`variantName` in the assign response schema, restart, and rerun — every SDK fails with
"assign.variant_name=None" while every SDK unit suite stays green. Revert. Then pause the
experiment in the dashboard and rerun: "assign failed: … HTTP 404" — the smoke checks that
the fixture is what the seed promised.

### Key takeaways

- `contract_smoke` per SDK: four calls, cache cleared between two assigns to prove
  server-side stickiness, one JSON line, exit code.
- The runner: manifest of commands + toolchains, health check, seeded key, last-line JSON,
  `validate()` on every field (including `is_control` being a bool), PASS/FAIL/SKIP.
- The runner also checks the ruleset endpoint (403 / 200 / 304) and, for JS and Python,
  that local evaluations reached the safety denominator and made no evaluate calls.
- CI: the reusable `_platform.yml` boots the stack and seeds it; `--strict` with an explicit
  list of 13 SDKs. Required on every PR.
- This is the test that catches "green unit tests, wrong endpoint."

---

## Hour 12 — Status, drift, change management and releases: how to change a contract sixteen clients depend on

### What & why

Changing a shared contract requires coordination across SDKs and releases. A renamed response
field, a new endpoint or a revised fan-out policy can affect several independently maintained
clients. This lesson records what each SDK has been tested against, explains how to assess and
sequence a contract change, and describes the per-SDK release process.

### How Experimently does it

**What each SDK has been run against.** There are three levels of proof, and the strongest
one is written in a workflow, not in a document. Read it from the code at the reference
commit:

| SDK | Live contract in CI (`pr-qa-gate.yml`) | Golden vectors (`hash_contract.py`) | Local mode |
|---|---|---|---|
| Python | yes | yes | yes (beta) |
| OpenFeature (Python) | yes | through Python's hash | — |
| JavaScript / TypeScript | yes | yes | yes (beta) |
| OpenFeature (JS) | yes | through JS's hash | — |
| Edge | yes | yes | — |
| React | yes (through `ServerClient`) | exports no hash | — |
| React Native | yes (Jest under node) | yes | — |
| Go | yes | yes | — |
| Java + Spring Boot starter | yes | its own unit tests | — |
| Android (Kotlin) | yes (the JVM build) | its own unit tests | — |
| Ruby | yes | its own unit tests | — |
| PHP | yes | its own unit tests | — |
| .NET | yes | its own unit tests | — |
| iOS (Swift) | no (no Swift toolchain on the Linux runner) | its own unit tests (macOS) | — |
| Elixir | no | its own unit tests | — |
| Flutter / Dart | no | its own unit tests | — |

Thirteen of the sixteen run against a live backend on every pull request. The other three are
exercised by their unit suites in the SDK unit-test workflow, and by the harness on any
machine that has their toolchain (`--sdk ios`, `--sdk elixir`, `--sdk flutter`). The runner's
`SKIP` status exists so that "not run here" is never misreported as "passing".

The prose status table in the app's `docs/sdk-guide.md` is the second copy of this fact, and
at the reference commit it shows exactly the drift the Watch Out below predicts: it still
grades Android "not yet" and React Native "unit tests only", although the CI job runs both,
and its unit-test counts are older than the suites (the Python suite has 187 tests at the
reference commit). When the document and the workflow disagree, the workflow is what ran.

**What counts as a contract change.** Anything an SDK unit test would have to change to stay
green: a field name or type in any of the four responses; a status code's meaning (404 vs
200-with-reason); the query/body encoding of `context`; the batch limit; a header. For local
evaluation, also: the ruleset's `schema` integer, its `bucketing` name, and the operator list.
Things that are _not_ contract changes: adding an optional response field (SDKs ignore
unknown fields — the Python client reads with `.get`, the JS client with `data?.field`);
adding a new `reason` value (SDKs pass it through as a string); changing rate limits (SDKs
already handle 429).

`assigned` and `reason` on the assign response are the worked example of an additive change
(#315): the server added them, old SDKs ignored them, the JavaScript and Python SDKs started
reading them — and, being careful, read a _missing_ field as "unknown" rather than `false`.
The other ports have not followed yet (#316), which is what "port to the rest" in step 4 below
looks like while it is still in progress.

**The order of operations** for a real contract change, derived from how the rewiring was
done:

1. **Server first, additively.** Add the new field/endpoint alongside the old. The harness
   must stay green with the _old_ SDKs — that's the compatibility check.
2. **Update the reference SDKs (Python, JS) and their unit tests.** Port the test _names_
   as well as the code; they're the checklist the other fourteen will copy.
3. **Update the smoke** if the new behaviour is observable end to end (a new field in the
   JSON line, a new `validate()` check). Run the harness locally for the references.
4. **Port to the rest**, by the Hour 7 table — invariants first, idiom second — and run each
   SDK's suite plus the harness for every toolchain you have. Open an issue for every port
   you did not reach, so the gap is visible (#316 and #438 are two such issues).
5. **Deprecate, then remove the old behaviour on the server**, in a separate, later change,
   after the SDK releases that stop using it. Old SDK versions in the wild keep working until
   then.
6. **Bump `ENGINE_VERSION`** if any number a client could have persisted changes meaning
   (Statistics book, Hour 12) — the results and bandit endpoints carry it.

Step 5 has actually run, end to end. The server once listed flags for two clients of its own
design: an edge bootstrap payload and an OpenFeature-shaped flag listing. When local
evaluation shipped `GET /api/v1/sdk/ruleset`, both were deprecated in its favour (#345 for
the OpenFeature listing, which also removed OpenFeature evaluate routes that never returned a
result; #495 for the bootstrap). The SDKs stopped using them, and the edge adapters turned
their bootstrap methods into `@deprecated` no-ops so existing Workers kept compiling. Then
#770 **removed** `/api/v1/edge/bootstrap` and `/api/v1/openfeature/flags` — a breaking
change, marked `feat(api)!`, landing in the release after 0.17.0, where both answer 404. The
regression test that came with it pins the removal three ways: the routes are absent from the
mounted route set, absent from the OpenAPI document, and a valid API key gets exactly 404.

The one thing not to do is the thing the pre-rewiring SDKs did: put a decision in the
client. Every rule in this book — the server decides, never cache failures, fan out over what
you served, raise-vs-swallow — is a rule about _where policy lives_. Local evaluation is the
careful exception that proves it: it ships the server's ruleset and the server's bucket, it is
held to a corpus the server generated, and it asks the server whenever it cannot be sure.
Keep policy on the server, and a contract change is a coordinated field rename. Let it into
the clients, and a contract change is sixteen re-implementations of a decision, each with its
own drift.

**Releasing an SDK.** SDKs version independently of the platform — a fix to the JavaScript
client should not force a release of the API images — so each has its own tag,
`sdk/<name>/vX.Y.Z`. That shape is Go's requirement (a module in a subdirectory is released
by a `<subdir>/vX.Y.Z` tag), reused for every SDK so there is one convention. One workflow,
`.github/workflows/sdk-release.yml`, publishes one SDK per tag:

```yaml
# 📁 .github/workflows/sdk-release.yml
on:
  push:
    tags: ['sdk/*/v*']
  workflow_dispatch:
    inputs:
      sdk:
        description: 'SDK directory name under sdk/, e.g. js or openfeature-python.'
        type: string
        required: true
```

Its first job refuses anything the plumbing cannot actually publish. `scripts/check_sdk_version.py`
reads the version from the SDK's own manifest and fails when the tag disagrees, and its
table says how each SDK ships:

```python
# 📁 scripts/check_sdk_version.py
ECOSYSTEMS: dict[str, str] = {
    "python": "pypi",
    "openfeature-python": "pypi",
    "js": "npm",
    "edge": "npm",
    "openfeature": "npm",
    "react": "npm",
    "react-native": "npm",
    "go": "tag-only",
    "java": "unwired",
    "dotnet": "unwired",
    "ruby": "unwired",
    "php": "unwired",
    "elixir": "unwired",
    "flutter": "unwired",
    "android": "unwired",
    "ios": "unwired",
}
```

`pypi` and `npm` SDKs are built, tested and published by **trusted publishing** — OpenID
Connect from the workflow, no long-lived token in any secret — from an `sdk-release`
environment; npm packages are published with provenance. Go has nothing to upload: the tag
_is_ the release, because `go get` resolves it. An `unwired` SDK has a real manifest but no
publishing identity yet, so tagging one is **refused** rather than going green having
published nothing.

The registry names and the versions in the manifests at the reference commit:

| Directory | Registry name | Manifest version |
|---|---|---|
| `sdk/js` | npm `@getexperimently/js-sdk` | 0.1.0 |
| `sdk/react` | npm `@getexperimently/react-sdk` | 1.1.0 |
| `sdk/edge` | npm `@getexperimently/edge-sdk` | 0.2.0 |
| `sdk/react-native` | npm `@getexperimently/react-native-sdk` | 0.1.0 |
| `sdk/openfeature` | npm `@getexperimently/openfeature-provider` | 0.1.0 |
| `sdk/python` | PyPI `experimently` (imported as `experimentation`) | 0.1.0 |
| `sdk/openfeature-python` | PyPI `experimently-openfeature` | 0.1.0 |
| `sdk/go` | module `github.com/getexperimently/experimently/sdk/go` | the tag |

**Nothing has been published yet.** At the reference commit no SDK release tag has been
pushed: the npm names return 404, and the PyPI names (and NuGet's `Experimently.SDK`) are held
by 0.0.0 placeholder packages that say "not released yet" and contain no client. So
`npm install @getexperimently/js-sdk` fails and `pip install experimently` installs only the
placeholder. Every SDK page in the app's docs gives a from-source route instead; for the two
references:

```bash
# Python: from a clone, or straight from the repository
pip install -e sdk/python
pip install "experimently @ git+https://github.com/getexperimently/experimently.git#subdirectory=sdk/python"
# JavaScript: build and pack, then install the tarball in your app
cd sdk/js && npm ci && npm run build && npm pack      # writes getexperimently-js-sdk-0.1.0.tgz
```

The first publish of each package is an irreversible act — a version number on PyPI or npm can
never be reused — so it is done by a maintainer, by hand, once the registry has been told to
trust the workflow. Nothing in this book asks you to publish.

> **By the Way** — the golden-vector job and the live-contract job are _both_ in the PR gate
> because they fail for different reasons. A hash change fails offline in seconds and points
> at exactly one function. A live failure takes minutes, needs the log, and can be the
> server, the seed, the runner image or the SDK. Cheap and precise first; expensive and
> comprehensive second — the same ordering as unit tests before integration tests.

> **Watch Out!** A status written in prose rots silently: if the CI job is edited to drop
> `php` from its `sdks:` list, nothing flags a document that still says PHP is verified in
> CI — and the reverse happened for real, when Android and React Native joined the job and
> the guide's table kept calling them untested. When you touch the job's SDK list, touch
> every document that restates it in the same pull request, or better, generate the
> document from the workflow (Exercise E4.4). The table and the workflow are the same fact
> written twice.

### Try It Yourself

Do a dry run of a contract change without shipping it. Pretend `configuration` on the assign
response is being renamed to `variant_config`:

1. List every SDK file that reads it (`grep -rn '"configuration"\|\.configuration\|configuration:' sdk/`).
2. For Python and JS, write the unit test that would fail (`test_maps_response` /
   "maps the server response to an Assignment") and note what the additive server change
   looks like (return both keys).
3. Decide whether the smoke's JSON line needs a new field. (It doesn't: `configuration`
   isn't in `validate()`.) Argue whether it should be.
4. Write the issue you would open for each port you would not reach in the same change.

Then dry-run a release without publishing anything:

```bash
python scripts/check_sdk_version.py js                       # prints the manifest version
python scripts/check_sdk_version.py js --expect 9.9.9        # refused: the tag would not match
python scripts/check_sdk_version.py java --expect 1.0.0      # refused: java has no publishing identity yet
```

### Key takeaways

- The workflow is the source of truth for what ran: 13 of 16 SDKs pass the live contract on
  every pull request; iOS, Elixir and Flutter rely on their unit suites. Prose copies drift.
- Contract change = anything an SDK unit test must change for; optional additions and new
  enum strings are not.
- Order: server additively → reference SDKs + tests → smoke → ports (issues for the rest) →
  deprecate, then remove (as #345/#495 → #770 did) → `ENGINE_VERSION` if numbers changed
  meaning.
- Releases: one tag per SDK (`sdk/<name>/vX.Y.Z`), trusted publishing for PyPI and npm, Go by
  tag, everything else refused until wired. Nothing is published yet; install from source.
- Keep policy on the server; the SDK stays a cache in front of four calls.

---

## Part IV Workshop

**Q1. What can a fake transport prove, and what can't it?**
It proves the client's wire behaviour matches the design (paths, bodies, encodings, caching,
retries, error mapping). It can't prove the design matches what the server serves — the
fixtures are hand-written.

**Q2. Why does the smoke clear the cache between its two assign calls?**
To force the second call to the server; otherwise the client cache would make the assignment
look sticky even if the server re-bucketed. Stickiness is a _server_ property and must be
tested there.

**Q3. Why one JSON line on stdout, and why the last line?**
One parser for sixteen SDKs in a dozen languages; the last line because build tools (`npm run build`, `mix
run`, `dotnet run`) print before the program does.

**Q4. The harness marks an SDK `SKIP`. What does that tell you about the SDK?**
Nothing: the toolchain wasn't present, so nothing ran. `SKIP` is not `PASS`, and no status
may be upgraded on the strength of it. With `--strict`, the same situation is `FAIL` — which
is what CI wants.

**Q5. Is adding `reason` to the flag response a contract change? Was renaming `enabled` to
`isEnabled`?**
No — SDKs ignore unknown fields and pass `reason` through as a string; older tests stay
green. Yes — every SDK's "maps the response" test would fail; that's the definition.

**Q6. Why remove the old server behaviour in a separate, later PR?**
Because old SDK versions are installed in applications you don't control; they keep working
against the additive server until their owners upgrade. The removal is the last step, after
releases — as with the edge bootstrap and OpenFeature flag listings, deprecated first and
removed in #770 (a `!` change) once nothing used them.

**Q7. Someone pushes a release tag for the Ruby SDK, version 0.2.0. What happens?**
The release workflow's first job refuses it: Ruby is `unwired` in
`scripts/check_sdk_version.py`, so there is no registry to publish to, and a tag that built,
tested and published nothing would otherwise go green. Wiring Ruby means registering the
account and moving it out of `unwired` in the same change.

**Quiz.** A PR edits `pr-qa-gate.yml` to drop `dotnet` from the `sdks:` list "because the .NET
setup step is slow." What else must the PR change, and what should the reviewer ask?
→ Every document that says .NET is verified in CI (the SDK guide's table, this book's Hour 12
table), and the reviewer should ask whether making the step faster (caching) is better than
losing the only live execution of that SDK anyone has.

---

## Exercises

**E4.1 — Contract-as-tests audit.** For the Python and JS suites, produce a two-column
list: each Hour 3 rule and the test name(s) that assert it. Identify any rule with no test in
one of the two.

**E4.2 — Harness extension.** Add `"configuration"` to the smoke's JSON and a `validate()`
check that it is either `null` or an object. Update the Python and JS smokes; run the
harness; then decide whether the seed should give the treatment variant a non-null
configuration so the check is meaningful.

**E4.3 — A failing smoke, diagnosed.** Change the seed so the flag is at 0% and run the
harness; read the failure line; then read the backend log for the corresponding request
(`X-Request-ID` if you have it, else the path) and write the two-sentence incident note.

**E4.4 — Status hygiene.** Write a tiny script that parses the `sdks:` list out of
`pr-qa-gate.yml` and the "Verified live" column out of `docs/sdk-guide.md`, and fails if
the two disagree in either direction. Run it at the reference commit and read what it finds.
Propose where in CI it would run.

**E4.5 — Change plan.** Write the six-step order-of-operations plan for adding a new
endpoint `GET /api/v1/tracking/assignments/{user_id}?experiment_key=` as a first-class SDK
method (`get_assignment_record`), including which SDKs get it first and what the smoke would
check.

---

## Solutions

**S4.1** — Python: cache per user+key → `test_cache_is_per_user_and_key`; successes only →
`test_failure_is_never_cached`, `test_404_raises_and_is_not_cached`; TTL →
`test_cache_expires_after_ttl`; attributes not in key →
`test_cache_key_ignores_attributes_until_clear_cache`; fan-out → the `track` tests; raise vs
swallow → `test_404_raises_with_status_and_body` vs `test_returns_default_on_404`; 429 →
`test_retries_once_after_retry_after`, `test_retry_after_is_capped_or_defaulted`. JS adds
"shares one in-flight request" (no Python equivalent — the Python client has no dedupe,
which is itself a documented difference: threads, not promises).

**S4.2** — `"assign": {..., "configuration": first.configuration}` in the smokes;
`if assign.get("configuration") is not None and not isinstance(assign["configuration"], dict): problems.append(...)`.
The seed's variants have no configuration, so the check passes vacuously; give treatment
`{"button": "green"}` in `seed_sdk_contract.py` and tighten the check to "treatment has an
object" — now a port that drops the field fails.

**S4.3** — `FAIL python  flag.enabled=False (seeded flag is 100% on)`. Backend log: the
evaluate request returned 200 with `reason: "rollout"` and `enabled: false` — the server was
right; the fixture was wrong. Note: "Smoke red on flag path; root cause seed drift
(rollout_percentage=0), not SDK or server. Fix: reseed with `--reset`; add a seed assertion
that the flag is at 100%."

**S4.4**

```python
import re, pathlib
job = pathlib.Path(".github/workflows/pr-qa-gate.yml").read_text()
block = job.split("suite: sdk-live-contract")[1].split("sdks: >-")[1]
ci = set(" ".join(line.strip() for line in block.splitlines()[1:3]).split())
table = pathlib.Path("docs/sdk-guide.md").read_text()
rows = re.findall(r"\| [^|]*? \| `sdk/([\w-]+)` \| [^|]*? \| ([^|]*?) \|", table)
claimed = {name for name, grade in rows if grade.startswith(("yes", "in CI only"))}
print("CI runs, the guide does not claim:", sorted(ci - claimed))
print("the guide claims, CI does not run:", sorted(claimed - ci))
```

At the reference commit the first line names `android` and `react-native` (the guide's stale
rows) and the second names `ios`, which the guide calls "yes" from a run on a developer's
machine — a third grade the script should learn rather than flag. (The guide lists the
`openfeature-python` provider under the `sdk/openfeature-python` row, so directory names match
the job's names here; check that before trusting a clean result.) Run it as a step in the
`SDK Contract Tests` job, next to the golden vectors: cheap, offline, and it fails the PR
that lets the two facts diverge.

**S4.5** — (1) endpoint already exists; confirm its response shape and add
`experiment_key` filtering additively. (2) Python `get_assignment_record(user_id,
experiment_key) -> Optional[Assignment]` (not cached; a read, like `get_assignments`), JS
`fetchAssignmentRecord`; tests for path encoding, 404 → `None`/`null`, no cache write. (3)
Smoke: after step 1, fetch the record and assert `variant_name` equals the assignment's —
this makes the smoke check the server's _stored_ row, a stronger stickiness proof; add
`"record": {"matches": true}` to the JSON and `validate()`. (4) Go, Java, Ruby, PHP, .NET
(CI-runnable) first, mobile after; status table per SDK. (5) nothing to remove. (6) no
`ENGINE_VERSION` change — no statistics involved.

---

_You've finished SDK Engineering. The other two specializations are
[Experimentation Statistics](../experimentation-statistics/README.md) and
[Platform Operations](../platform-operations/README.md); the core path is in the
[root README](../README.md)._ 🔌

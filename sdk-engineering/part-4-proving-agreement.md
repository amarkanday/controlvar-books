# Part IV — Proving Agreement

### Hours 10–12

[← Part III](./part-3-fifteen-languages.md) · [Contents](./README.md)

Fifteen implementations of one contract are only "one SDK" if you can prove it. This Part is
the proof machinery. Hour 10 is the unit level: fake transports and recorded requests that
let every contract rule be asserted offline in milliseconds. Hour 11 is the integration
level: `contract_smoke`, one entry point per SDK that prints one JSON line, and the runner
that executes them against a real backend on every pull request. Hour 12 is what to do with
the proof — the status table, what "verified live" means and doesn't, and how to change a
contract fifteen clients depend on without breaking any of them.

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

The pattern pays off in three ways. Tests are fast (the Python SDK's 97 tests run in well
under a second). Tests are _precise_ — "the second call made no request" is an assertion on
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
cd sdk/python && python -m pytest -q -o addopts="" 2>&1 | tail -3        # 97 passed
cd ../js && npm test -- --silent 2>&1 | tail -5                           # 105 passed
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
- Unit tests prove the client matches the design; they can't prove the design matches the
  server.

---

## Hour 11 — The live contract harness: `contract_smoke`, the runner, and the CI job

### What & why

The September rewiring was triggered by a discovery that unit tests could never have made:
SDKs with green suites were calling endpoints that didn't exist, or bucketing locally with a
hash the server didn't share. The fix wasn't more unit tests; it was a test that runs each
SDK **against the real backend** and checks the things that matter — assignment is sticky
_on the server_, the flag comes back enabled, events are accepted, key-less events fan out.

The design has three parts. Every SDK has a **`contract_smoke`** entry point: a small
program in the SDK's own language that runs the four calls and prints **one JSON line** to
stdout (anything else to stderr), exit 0 on success. A **runner** (`run_live_contract.py`)
knows how to invoke each one, checks the backend is up, runs them with a seeded API key,
validates the JSON, and prints a PASS/FAIL/SKIP table. And a **CI job** starts Postgres,
Redis and the backend, seeds fixtures, and runs the runner with `--strict` for every SDK
whose toolchain is available on Linux — as a required check on every pull request.

The "one JSON line" contract is what makes fifteen languages testable by one 200-line
Python script: the runner never parses Go or Swift output formats, only JSON.

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
    "java": ("bash sdk/java/examples/contract_smoke.sh", ("java", "mvn")),
    # The Android SDK's Kotlin sources touch no android.* API, so sdk/android/jvm
    # compiles them for a plain JVM and the smoke runs on a Linux runner with no
    # Android SDK and no emulator (the Gradle/AAR build still runs nightly).
    "android": ("bash sdk/android/examples/contract_smoke.sh", ("java", "mvn")),
    "ios": ("cd sdk/ios && swift run -q contract-smoke", ("swift",)),
    "ruby": ("ruby -Isdk/ruby/lib sdk/ruby/examples/contract_smoke.rb", ("ruby",)),
    "php": ("php sdk/php/examples/contract_smoke.php", ("php",)),
    "dotnet": ("dotnet run --project sdk/dotnet/examples/ContractSmoke", ("dotnet",)),
    "elixir": ("cd sdk/elixir && mix run examples/contract_smoke.exs", ("mix",)),
    "flutter": ("cd sdk/flutter && dart run example/contract_smoke.dart", ("dart",)),
}
```

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
the failure detail, a per-SDK timeout that includes builds:

```python
# 📁 tests/sdk-contract/live/run_live_contract.py
def run_one(sdk: str, command: str, env: dict[str, str], timeout: int) -> tuple[str, str]:
    """Return (status, detail) where status is PASS / FAIL."""
    ...
    stdout_lines = [line for line in proc.stdout.strip().splitlines() if line.strip()]
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or stdout_lines or ["(no output)"])[-1]
        return "FAIL", f"exit {proc.returncode}: {tail[:300]}"
    if not stdout_lines:
        return "FAIL", "no JSON line on stdout"
    try:
        payload = json.loads(stdout_lines[-1])
    except json.JSONDecodeError:
        return "FAIL", f"last stdout line is not JSON: {stdout_lines[-1][:200]}"
    problems = validate(sdk, payload)
    if problems:
        return "FAIL", "; ".join(problems)
    return "PASS", f"{payload['assign']['variant_name']} in {elapsed:.1f}s"
```

The fixtures come from one seed script — an ACTIVE experiment `sdk_contract_ab` with
control/treatment, an ACTIVE flag `sdk_contract_flag` at 100%, and an API key written to
`tests/sdk-contract/live/.api_key` (or `EXPERIMENTLY_API_KEY`). The CI job assembles the
whole thing:

```yaml
# 📁 .github/workflows/pr-qa-gate.yml
  # Live SDK contract: every SDK's contract_smoke against a real backend.
  # Proves the SDKs call endpoints the backend actually serves (the golden-vector
  # job above only covers hashing). Toolchains not available on ubuntu-latest
  # (Android, Flutter, Elixir, iOS) are exercised by their unit tests only.
  sdk-live-contract:
    name: SDK Live Contract
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        ...
      redis:
        image: redis:7
        ...
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v7
        with: { python-version: '3.11' }
      - name: Set up Node.js
        uses: actions/setup-node@v7
        with: { node-version: '22' }
      - name: Set up Go
        uses: actions/setup-go@v5
        with: { go-version: '1.22.x', cache-dependency-path: sdk/go/go.sum }
      - name: Set up Java
        uses: actions/setup-java@v4
        with: { distribution: temurin, java-version: '17', cache: maven }
      - name: Set up .NET
        uses: actions/setup-dotnet@v4
        with: { dotnet-version: '6.0.x' }
      ...
      - name: Seed contract fixtures
        run: python backend/scripts/seed_sdk_contract.py

      - name: Start backend
        run: |
          nohup python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 > backend.log 2>&1 &
          for i in $(seq 1 60); do
            curl -sf http://127.0.0.1:8000/health > /dev/null && exit 0
            sleep 1
          done
          echo "backend did not become healthy"; tail -n 100 backend.log; exit 1

      - name: Run live SDK contract
        run: >
          python tests/sdk-contract/live/run_live_contract.py --strict
          --sdk python --sdk openfeature-python
          --sdk js --sdk openfeature --sdk edge
          --sdk go --sdk java --sdk ruby --sdk php --sdk dotnet

      - name: Backend log (on failure)
        if: failure()
        run: tail -n 200 backend.log
```

`--strict` with an explicit `--sdk` list is the right shape for CI: a missing toolchain is a
_failure_ (someone broke the runner image), but only the SDKs the image can run are listed,
so Android/Flutter/Elixir/iOS don't produce permanent red. The backend log is dumped on
failure because the most common smoke failure is a server-side 500, and the SDK's one-line
stderr won't say why.

> **Watch Out!** The smoke uses a fresh random `user_id` per run (`smoke-<uuid>`) so runs
> don't inherit stickiness from each other — a rerun with the same user would pass the
> "sticky" check trivially. Set `CONTRACT_USER_ID` only when debugging a specific user.

> **Did You Know?** The CI job's `Set up Java` is used twice — for the Java SDK and for the
> Android SDK's JVM build — but Android isn't in the CI `--sdk` list. It's in the manifest
> and runnable locally (`--sdk android`) on any machine with a JDK, which is what "rewired
> by inspection, tests not executed anywhere" in the status table means: the harness _can_
> run it; nobody has yet, on a machine that has the toolchain.

### Try It Yourself

```bash
source venv/bin/activate
export APP_ENV=development POSTGRES_SERVER=localhost POSTGRES_DB=experimentation POSTGRES_SCHEMA=experimentation
python backend/scripts/seed_sdk_contract.py
uvicorn backend.app.main:app --port 8000 &
python tests/sdk-contract/live/run_live_contract.py --sdk python --sdk js --sdk go
# PASS python               treatment in 0.4s
# PASS js                   control in 3.1s
# PASS go                   treatment in 1.8s
```

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
- CI: Postgres + Redis + backend + seed + `--strict` with an explicit SDK list; backend log
  on failure. Required on every PR.
- This is the test that catches "green unit tests, wrong endpoint."

---

## Hour 12 — Status, drift and change management: how to change a contract fifteen clients depend on

### What & why

The last hour is process, because the hardest part of fifteen SDKs is not writing them — it's
_changing_ them. A field renamed on the server is fifteen releases; a new endpoint is fifteen
features; a fix to the fan-out rule is fifteen chances to introduce a behavioural fork.
The platform's defences are a **status table** that says exactly how much each SDK has been
proven, a **drift discipline** for what counts as a contract change, and an **order of
operations** for making one.

### How Experimently does it

The status table is the source of truth for how far to trust each port — three grades, not
two:

```markdown
<!-- 📁 docs/sdk-guide.md -->
"Verified live" means the SDK's `contract_smoke` entry point passed
`tests/sdk-contract/live/run_live_contract.py` against a running backend (see
[tests/sdk-contract/README.md](../tests/sdk-contract/README.md)); the `SDK Live Contract` CI job repeats
this on every pull request for the SDKs whose toolchain is available on Linux.

| SDK | Location | Unit tests | Verified live | Docs |
|---|---|---|---|---|
| React | `sdk/react` | 215 (jest) | yes — also runs the ShopLab demo | [react.md](sdk/react.md) |
| JavaScript / TypeScript | `sdk/js` | 105 (jest) | yes | [javascript.md](sdk/javascript.md) |
| OpenFeature (JS) | `sdk/openfeature` | 51 (jest) | yes | [openfeature.md](sdk/openfeature.md) |
| Edge (Cloudflare Workers) | `sdk/edge` | 101 (jest) | yes | [edge.md](sdk/edge.md) |
| React Native | `sdk/react-native` | jest | unit tests only (no device runtime) | [react-native.md](sdk/react-native.md) |
| Python | `sdk/python` | 97 (pytest) | yes | [python.md](sdk/python.md) |
| OpenFeature (Python) | `sdk/openfeature-python` | 79 (pytest) | yes | [openfeature.md](sdk/openfeature.md) |
| Go | `sdk/go` | 51 (`go test -race`) | yes | [go.md](sdk/go.md) |
| Java + Spring Boot starter | `sdk/java` | 77 + 30 (JUnit 5) | yes | [java.md](sdk/java.md) |
| iOS (Swift) | `sdk/ios` | 71 (XCTest) | yes | [ios.md](sdk/ios.md) |
| Ruby | `sdk/ruby` | 109 (RSpec) | yes | [ruby.md](sdk/ruby.md) |
| PHP | `sdk/php` | PHPUnit | in CI only (no PHP on the dev machine) | [php.md](sdk/php.md) |
| .NET | `sdk/dotnet` | xUnit | in CI only (no .NET on the dev machine) | [dotnet.md](sdk/dotnet.md) |
| Android (Kotlin) | `sdk/android` | JUnit 5 | not yet (needs the Android SDK) | [android.md](sdk/android.md) |
| Flutter / Dart | `sdk/flutter` | `dart test` | not yet (needs the Dart SDK) | [flutter.md](sdk/flutter.md) |
| Elixir | `sdk/elixir` | ExUnit | not yet (needs Elixir) | [elixir.md](sdk/elixir.md) |

Rows marked "not yet" were rewired by inspection and reviewed line by line, but their tests and smoke
have not been executed anywhere; run `run_live_contract.py --sdk <name> --strict` on a machine with the
toolchain before relying on them.
```

Three grades: **verified live** (the harness passed, and CI repeats it), **in CI only** (the
harness passes on the runner image; the dev machine can't run it), **not yet** (reviewed,
never executed). The honesty of that third grade is the point: a status table with only
"yes" in it is a table nobody trusts. The runner's `SKIP` status exists so that "not yet"
never has to be misreported as "passing."

**What counts as a contract change.** Anything an SDK unit test would have to change to stay
green: a field name or type in any of the four responses; a status code's meaning (404 vs
200-with-reason); the query/body encoding of `context`; the batch limit; a header. Things
that are _not_ contract changes: adding an optional response field (SDKs ignore unknown
fields — the Python client reads with `.get`, the JS client with `data?.field`); adding a
new `reason` value (SDKs pass it through as a string); changing rate limits (SDKs already
handle 429).

**The order of operations** for a real contract change, derived from how the rewiring was
done:

1. **Server first, additively.** Add the new field/endpoint alongside the old. The harness
   must stay green with the _old_ SDKs — that's the compatibility check.
2. **Update the reference SDKs (Python, JS) and their unit tests.** Port the test _names_
   as well as the code; they're the checklist the other thirteen will copy.
3. **Update the smoke** if the new behaviour is observable end to end (a new field in the
   JSON line, a new `validate()` check). Run the harness locally for the references.
4. **Port to the rest**, by the Hour 7 table — invariants first, idiom second — and run each
   SDK's suite plus the harness for every toolchain you have. Update the status table row
   to say what was actually executed.
5. **Only then remove the old behaviour on the server**, in a separate PR, after the SDK
   releases are published. Old SDK versions in the wild will keep working until then.
6. **Bump `ENGINE_VERSION`** if any number a client could have persisted changes meaning
   (Statistics book, Hour 12) — SDK responses carry it for the advanced-stats endpoints.

The one thing not to do is the thing the pre-rewiring SDKs did: put a decision in the
client. Every rule in this book — the server decides, never cache failures, fan out over what
you served, raise-vs-swallow — is a rule about _where policy lives_. Keep it on the server,
and a contract change is a coordinated field rename. Let it into the clients, and a contract
change is fifteen re-implementations of a decision, each with its own drift.

> **By the Way** — the golden-vector job and the live-contract job are _both_ in the PR gate
> because they fail for different reasons. A hash change fails offline in seconds and points
> at exactly one function. A live failure takes minutes, needs the log, and can be the
> server, the seed, the runner image or the SDK. Cheap and precise first; expensive and
> comprehensive second — the same ordering as unit tests before integration tests.

> **Watch Out!** "In CI only" rows can rot silently: if the CI job is edited to drop `--sdk
> php`, PHP's row still says "in CI only" and nothing flags it. When you touch the job's
> `--sdk` list, touch the status table in the same PR. The table and the workflow are the
> same fact written twice.

### Try It Yourself

Do a dry run of a contract change without shipping it. Pretend `configuration` on the assign
response is being renamed to `variant_config`:

1. List every SDK file that reads it (`grep -rn '"configuration"\|\.configuration\|configuration:' sdk/`).
2. For Python and JS, write the unit test that would fail (`test_maps_response` /
   "maps the server response to an Assignment") and note what the additive server change
   looks like (return both keys).
3. Decide whether the smoke's JSON line needs a new field. (It doesn't: `configuration`
   isn't in `validate()`.) Argue whether it should be.
4. Write the status-table diff you'd ship with step 4 of the order of operations.

### Key takeaways

- Three trust grades — verified live / in CI only / not yet — and `SKIP` so the third can't
  be misreported.
- Contract change = anything an SDK unit test must change for; optional additions and new
  enum strings are not.
- Order: server additively → reference SDKs + tests → smoke → ports + status table → remove
  old → `ENGINE_VERSION` if numbers changed meaning.
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
One parser for fifteen languages; the last line because build tools (`npm run build`, `mix
run`, `dotnet run`) print before the program does.

**Q4. The harness marks an SDK `SKIP`. What does that mean for the status table?**
Nothing changed: the toolchain wasn't present. `SKIP` is not `PASS`; the row stays at its
previous grade. With `--strict`, the same situation is `FAIL` — which is what CI wants.

**Q5. Is adding `reason` to the flag response a contract change? Was renaming `enabled` to
`isEnabled`?**
No — SDKs ignore unknown fields and pass `reason` through as a string; older tests stay
green. Yes — every SDK's "maps the response" test would fail; that's the definition.

**Q6. Why remove the old server behaviour in a separate, later PR?**
Because old SDK versions are installed in applications you don't control; they keep working
against the additive server until their owners upgrade. The removal is the last step, after
releases.

**Quiz.** A PR edits `pr-qa-gate.yml` to drop `--sdk dotnet` "because the .NET setup step is
slow." What else must the PR change, and what should the reviewer ask?
→ The status table's .NET row (from "in CI only" to "not yet"), and the reviewer should ask
whether making the step faster (caching) is better than losing the only execution of that
SDK anyone has.

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

**E4.4 — Status hygiene.** Write a tiny script that parses the `--sdk` list out of
`pr-qa-gate.yml` and the "Verified live" column out of `docs/sdk-guide.md`, and fails if
any SDK is "in CI only"/"yes" in the table but absent from the job (or vice versa). Propose
where in CI it would run.

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
ci = set(re.findall(r"--sdk (\S+)", job.split("Run live SDK contract")[1].split("Backend log")[0]))
table = pathlib.Path("docs/sdk-guide.md").read_text()
rows = re.findall(r"\| .*? \| `sdk/([\w-]+)` \| .*? \| (yes|in CI only|not yet)", table)
claimed = {name for name, grade in rows if grade in ("yes", "in CI only")}
assert claimed == ci, (claimed - ci, ci - claimed)
```

(Directory names and manifest names differ for two rows — `openfeature-python`, `react` —
map them explicitly.) Run it as a step in the `SDK Contract Tests` job, next to the golden
vectors: cheap, offline, and it fails the PR that lets the two facts diverge.

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

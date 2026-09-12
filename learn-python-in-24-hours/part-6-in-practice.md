# Part VI — In Practice

### Hours 21–24

[← Part V](./part-5-concurrency.md) · [Contents](./README.md)

The last four hours are the Python this platform actually depends on beyond the language:
the standard library's hashing and randomness modules and the discipline of determinism
(Hour 21), the NumPy/SciPy layer under every statistic (Hour 22), `pytest` and the testing
idioms the codebase expects (Hour 23), and — finally — the style guide that collects every
convention from the previous twenty-three hours into a review checklist (Hour 24).

---

## Hour 21 — `hashlib`, `struct`, `secrets`: determinism and randomness done right

### What & why

Experimentation is built on two opposite needs: **deterministic** functions that give the
same answer everywhere forever (which variant is user X in?) and **unpredictable** values
nobody can guess (an API key). Python's standard library has a module for each, and mixing
them up is a real bug class.

- **`hashlib`** — cryptographic hash functions (`md5`, `sha256`, `blake2b`) as _pure
  functions of bytes_. The platform uses MD5 for bucketing (fast, uniform, available in
  every language; `usedforsecurity=False` tells linters and FIPS builds it's not a security
  primitive), SHA-256 for API-key storage (fast, one-way; a random 128-bit key needs no slow
  hash), and BLAKE2b for Monte Carlo seeds (a keyed-size digest that packs into 8 bytes).
- **`struct`** — packs and unpacks bytes to fixed-width integers. `struct.unpack("<I",
  b)` reads 4 bytes as a **little-endian unsigned 32-bit int** — the cross-SDK convention.
- **`secrets`** — cryptographically random values for tokens and keys
  (`secrets.token_hex(16)`). Never `random` for anything security-relevant.
- **`random` / NumPy's `default_rng`** — pseudo-random for statistics, always **seeded** in
  this codebase so results are reproducible.

#### Why three different hash-to-number recipes

You've now seen three ways the codebase turns MD5 output into a bucket, and they are
deliberately different functions:

| Use                          | Recipe                                                     | Where                                  |
| ---------------------------- | ---------------------------------------------------------- | -------------------------------------- |
| Variant assignment           | `int(hexdigest, 16) % 100` (whole 128-bit digest)          | `assignment_service._hash_user_to_variant` |
| Flag rollout, rule rollout   | same, salted with `flag.key` / `rule.id`                   | `feature_flag_service`, `rules_engine` |
| Holdout, MEG, SDK utility    | first 4 bytes, `struct.unpack("<I")`, `% 100` or `/ 2**32` | `global_holdout_service`, `mutual_exclusion_service`, every SDK |

They're all deterministic and uniform; they're just not the same function, and the salts
(`experiment.id`, `flag.key`, `HOLDOUT_SALT`, the group's salt) make them independent. The
"first four bytes little-endian ÷ 2³²" recipe is the one pinned across fifteen languages by
golden vectors, because it's the one every language can implement without a big-integer
type.

### In the wild

Whole-digest bucketing:

```python
# 📁 backend/app/services/assignment_service.py
        hash_input = f"{user_id}:{experiment.id}"
        hash_value = int(
            hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest(), 16
        )
        ...
        bucket = hash_value % 100
```

First-four-bytes bucketing — the cross-SDK form:

```python
# 📁 backend/app/services/global_holdout_service.py
    @staticmethod
    def _get_holdout_bucket_static(user_id: str) -> int:
        """Get holdout bucket for a user (0-99). Static version."""
        combined = f"{user_id}:{HOLDOUT_SALT}".encode("utf-8")
        hash_bytes = hashlib.md5(combined, usedforsecurity=False).digest()[:4]
        hash_int = struct.unpack("<I", hash_bytes)[0]
        return hash_int % 100
```

```python
# 📁 sdk/python/experimentation/hashing.py
_HASH_DIVISOR = 4294967296  # 2 ** 32


def consistent_hash(user_id: str, flag_key: str) -> float:
    """Return the bucket for ``(user_id, flag_key)`` as a float in ``[0.0, 1.0)``."""
    (value,) = struct.unpack("<I", _digest(user_id, flag_key)[:4])
    return value / _HASH_DIVISOR
```

`.digest()` (raw bytes) vs `.hexdigest()` (hex text); `[:4]` slices bytes; `"<I"` is
little-endian (`<`) unsigned int (`I`); `struct.unpack` always returns a tuple, hence
`[0]` or the `(value,)` unpacking. Dividing by 2³² (not 2³² − 1) keeps the result in
`[0, 1)`; the JS SDK's comment marks that off-by-one as a real historical disagreement.

SHA-256 for API keys, `secrets` for generating them, and a constant-time compare:

```python
# 📁 backend/app/models/api_key.py
def generate_api_key() -> str:
    """Generate a random API key with a prefix."""
    prefix = "eptk"  # Short for "experimentation toolkit"
    random_part = secrets.token_hex(16)
    return f"{prefix}_{random_part}"
```

```python
# 📁 backend/app/core/security.py
def hash_api_key(api_key: str) -> str:
    """
    Hash an API key using SHA-256 for secure storage.

    Unlike passwords, API keys need fast lookup (not bcrypt) but must not
    be stored in plaintext.
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def verify_api_key(plaintext_key: str, hashed_key: str) -> bool:
    computed = hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()
    return hmac.compare_digest(computed, hashed_key)
```

`hmac.compare_digest` compares in constant time so an attacker can't learn a prefix from
response timing. (Passwords use `bcrypt` with 12 rounds — deliberately slow — via the
`bcrypt` package, not `hashlib`.)

BLAKE2b for a compact, deterministic seed, masked to fit foreign systems:

```python
# 📁 backend/app/core/stats_engine.py
#: Mask that keeps the derived seed inside the signed 64-bit range.
_SEED_MASK = (1 << 63) - 1


def derive_seed(experiment_id: object, bucket: str, n_samples: int) -> int:
    """Derive the deterministic RNG seed for a Monte Carlo computation."""
    key = f"{experiment_id}|{bucket}|{int(n_samples)}".encode("utf-8")
    digest = hashlib.blake2b(key, digest_size=8).digest()
    return int.from_bytes(digest, "big") & _SEED_MASK


def make_rng(seed: int) -> np.random.Generator:
    """Return the platform's RNG for a seed (always ``default_rng``)."""
    return np.random.default_rng(int(seed))
```

`digest_size=8` asks BLAKE2b for exactly 8 bytes; `int.from_bytes(..., "big")` is the
big-endian counterpart of `struct.unpack(">Q")`; `& ((1 << 63) - 1)` clears the top bit
so the value fits a signed PostgreSQL `BIGINT`. Every Monte Carlo draw on the platform goes
through `make_rng(derive_seed(...))` — same inputs, same samples, byte for byte.

`secrets.token_hex(32)` is also the recommended way to generate the settings secrets the
production validators demand:

```python
# 📁 backend/app/core/config.py
                    "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
```

> **Watch Out!** `random.random()` and an unseeded `np.random` are fine for a demo
> simulator and wrong for anything a dashboard shows — two calls give two answers and a
> support ticket. `hashlib` for "same input, same output"; seeded `default_rng` for
> "reproducible randomness"; `secrets` for "nobody can guess." Never `random` for keys.

> **Did You Know?** `usedforsecurity=False` is a Python 3.9+ argument that matters on
> FIPS-mode systems, where MD5 is disabled for security use; without it, `hashlib.md5`
> raises on such a host. Every MD5 call in the backend passes it — and it doubles as a
> comment to reviewers and Bandit that this is bucketing, not cryptography.

### Try It Yourself

```python
>>> import hashlib, struct, secrets, hmac
>>> d = hashlib.md5(b"user-42:hero_banner", usedforsecurity=False)
>>> d.hexdigest(), len(d.digest())
>>> int(d.hexdigest(), 16) % 100                                  # whole-digest bucket
>>> struct.unpack("<I", d.digest()[:4])[0] % 100                  # first-4-bytes bucket — different!
>>> struct.unpack("<I", d.digest()[:4])[0] / 2**32                # the SDK float
>>> from backend.app.services.global_holdout_service import GlobalHoldoutService
>>> from sdk.python.experimentation.hashing import consistent_hash   # if sdk/python is importable; else copy the function
>>> secrets.token_hex(16), len(secrets.token_hex(16))
>>> hmac.compare_digest("abc", "abc"), hmac.compare_digest("abc", "abd")
>>> hashlib.blake2b(b"exp|2026-09-11|10000", digest_size=8).digest()
>>> int.from_bytes(hashlib.blake2b(b"exp|2026-09-11|10000", digest_size=8).digest(), "big") & ((1 << 63) - 1)
```

Then run the golden vectors: `python -m pytest tests/sdk-contract/test_python_sdk.py -q -o
addopts=""` — and open `tests/sdk-contract/golden-vectors.json` to see what "pinned across
fifteen languages" looks like.

### Common mistakes

- **Hashing a `str`.** `.encode()` first.
- **Using the whole-digest recipe to predict an SDK bucket** (or vice versa). Different
  functions; check which one the caller uses.
- **`random` for tokens.** `secrets`.
- **`==` on secrets.** `hmac.compare_digest`.

### Key takeaways

- `hashlib` = pure functions of bytes: MD5 (bucketing, `usedforsecurity=False`), SHA-256
  (API keys), BLAKE2b (seeds).
- `struct.unpack("<I", digest[:4])` is the cross-SDK integer; `int(hex, 16)` the
  whole-digest one; salts make buckets independent.
- `secrets` for keys and settings secrets; `hmac.compare_digest` to check them.
- Seeded `default_rng` everywhere randomness is shown to a user.

---

## Hour 22 — NumPy and SciPy: arrays, RNGs, and a statistical test

### What & why

**NumPy** gives Python fast arrays: `np.ndarray` holds numbers in contiguous memory, and
operations are _vectorised_ — `Y - theta * (X - E_X)` runs over a million elements in C
without a Python loop. **SciPy** builds on it: `scipy.stats` has the distributions and
tests (`fisher_exact`, `norm.cdf`, `chi2.sf`, `ttest_ind`) the results engine calls.
**pandas** appears at the edges (daily results, export) for tabular data.

The four things you need to read the statistics services:

1. **Arrays and `dtype`.** `np.asarray(x, dtype=float)` converts a list (or an array) to a
   float array — the first line of every CUPED function, so integers and lists behave
   identically.
2. **Vectorised arithmetic and reductions.** `np.var(X, ddof=1)`, `np.cov(Y, X, ddof=1)`,
   `np.mean(winner == i)`, `np.argmax(samples, axis=0)`. `ddof=1` means the sample
   (n − 1) estimator; `axis=` picks the dimension.
3. **The RNG.** `np.random.default_rng(seed)` returns a `Generator`; `rng.beta(a, b,
   size=...)` draws arrays. Always seeded here.
4. **SciPy tests return tuples or result objects.** `stats.fisher_exact(table)` →
   `(odds_ratio, p_value)`; `stats.ttest_ind(...)` → an object with `.pvalue`;
   `stats.norm.cdf(z)` and `chi2.sf(stat, dof)` are plain floats.

### In the wild

CUPED's θ — `asarray`, `var`, `cov`, and a guard against a constant covariate:

```python
# 📁 backend/app/services/cuped_service.py
    @staticmethod
    def compute_theta(control_Y: np.ndarray, control_X: np.ndarray) -> float:
        """Compute the OLS coefficient for CUPED adjustment.

        θ = Cov(Y, X) / Var(X)
        """
        X = np.asarray(control_X, dtype=float)
        Y = np.asarray(control_Y, dtype=float)

        var_x = np.var(X, ddof=1) if len(X) > 1 else 0.0
        if var_x == 0.0:
            return 0.0

        # Using np.cov with ddof=1 (sample covariance) for consistency
        cov_matrix = np.cov(Y, X, ddof=1)
        cov_yx = cov_matrix[0, 1]
        return float(cov_yx / var_x)

    @staticmethod
    def apply_cuped(Y: np.ndarray, X: np.ndarray, theta: float, E_X: float) -> np.ndarray:
        """Y_cuped = Y - θ * (X - E[X])"""
        Y = np.asarray(Y, dtype=float)
        X = np.asarray(X, dtype=float)
        return Y - theta * (X - E_X)
```

`np.cov(Y, X)` returns a 2×2 matrix; `[0, 1]` is the off-diagonal covariance. `float(...)`
converts a NumPy scalar (`np.float64`) to a Python float before it leaves the function —
important for JSON serialisation and for `==` comparisons in tests.

Monte Carlo with a seeded generator, `argmax` over an axis, and a boolean-mean trick:

```python
# 📁 backend/app/services/bayesian_service.py
def _draw_posterior_samples(posteriors: List[Dict[str, float]], n_samples: int, seed: Optional[int]) -> np.ndarray:
    """Draw ``n_samples`` from each Beta posterior; shape ``[n_variants, n_samples]``."""
    rng = make_rng(resolve_seed(seed, n_samples))
    return np.array([
        rng.beta(float(p["alpha"]), float(p["beta"]), n_samples)
        for p in posteriors
    ])


def compute_probability_to_be_best(posteriors, n_samples: int = DEFAULT_N_SAMPLES, seed: Optional[int] = None) -> List[float]:
    ...
    # Draw samples from each posterior (shape: [n_variants, n_samples])
    samples = _draw_posterior_samples(posteriors, n_samples, seed)

    # For each sample, find which variant is the winner
    winner = np.argmax(samples, axis=0)  # shape: [n_samples]

    return [float(np.mean(winner == i)) for i in range(len(posteriors))]
```

`np.argmax(samples, axis=0)` — for each of the `n_samples` columns, the index of the
largest row (variant); `winner == i` is a boolean array; its mean is the fraction of
samples where variant `i` won. Ten thousand samples, two NumPy calls, no loop.

The same idea shaped for the bandit, with `bincount` instead of a loop of means:

```python
# 📁 backend/app/services/bandit_service.py
        rng = make_rng(ThompsonSampling.resolve_seed(seed, n_samples))
        draws = rng.beta(alpha, beta, size=(n_samples, n_variants))

        # For each sample pick the arm with the maximum draw
        winners = np.argmax(draws, axis=1)

        # Count wins per arm and normalise
        counts = np.bincount(winners, minlength=n_variants).astype(float)
        probabilities = (counts / n_samples).tolist()
        return probabilities
```

`rng.beta(alpha, beta, size=(n, k))` broadcasts a list of `k` alphas and betas into an
`n × k` matrix; `axis=1` argmaxes across each row; `bincount` counts occurrences of each
index; `.tolist()` converts back to Python floats for JSON.

SciPy tests — three different return shapes in one service:

```python
# 📁 backend/app/services/analysis_service.py
                # Run Fisher's exact test
                try:
                    odds_ratio, p_value = stats.fisher_exact(contingency_table)
```

```python
# 📁 backend/app/services/analysis_service.py
        z = (p_t - p_c) / se
        p_value = 2.0 * (1.0 - float(stats.norm.cdf(abs(z))))
        return min(1.0, max(0.0, p_value))

    def welch_t_test(self, control_values: List[float], treatment_values: List[float]) -> float:
        result = stats.ttest_ind(control_values, treatment_values, equal_var=False)
        return float(result.pvalue)
```

```python
# 📁 backend/app/services/srm_service.py
    statistic = sum((obs[k] - expected[k]) ** 2 / expected[k] for k in keys)
    dof = len(keys) - 1
    p_value = float(chi2_dist.sf(statistic, dof))
    p_value = min(1.0, max(0.0, p_value))
```

`sf` is the survival function (`1 − cdf`), numerically better than `1 - cdf` in the far
tail. Every p-value is clamped to `[0, 1]` and converted with `float(...)` before it's
stored.

`math` for scalars where NumPy would be overkill — the mSPRT statistic:

```python
# 📁 backend/app/services/sequential_testing_service.py
        Z_n = delta / math.sqrt(V_n)
        ratio = V_n / (V_n + tau_squared)
        lambda_ratio = math.sqrt(ratio) * math.exp(tau_squared * Z_n ** 2 / (2.0 * (V_n + tau_squared)))
```

> **Watch Out!** NumPy scalars leak. `np.float64(0.05) == 0.05` is `True`, but
> `json.dumps(np.float64(0.05))` raises and `isinstance(np.float64(1), float)` is `True`
> while `isinstance(np.int64(1), int)` is `False`. The codebase wraps every returned number
> in `float(...)` or `.tolist()` for this reason. Do the same.

> **Did You Know?** The versions are pinned together for a reason: `numpy==1.26.4` because
> `scipy==1.17.1` needs ≥ 1.26 and NumPy 2.x is a separate major bump; `pandas==2.2.3`
> because `statsmodels` needs ≥ 2.2. A Dependabot grouped update once tried to move them
> independently and was unresolvable; the pins in `backend/requirements.txt` carry comments
> saying so.

### Try It Yourself

```python
>>> import numpy as np
>>> from scipy import stats
>>> X = np.asarray([1, 2, 3, 4, 5], dtype=float); Y = np.asarray([2, 4, 5, 4, 5], dtype=float)
>>> theta = np.cov(Y, X, ddof=1)[0, 1] / np.var(X, ddof=1); theta
>>> Y_adj = Y - theta * (X - X.mean()); Y.var(ddof=1), Y_adj.var(ddof=1)     # variance reduced, mean unchanged
>>> rng = np.random.default_rng(42)
>>> draws = rng.beta([41, 53], [960, 948], size=(10_000, 2)); draws.shape
>>> winners = np.argmax(draws, axis=1); np.bincount(winners, minlength=2) / 10_000   # P(best) per arm
>>> np.random.default_rng(42).beta([41, 53], [960, 948], size=(10_000, 2)).sum() == draws.sum()   # seeded → identical
>>> stats.fisher_exact([[52, 948], [40, 960]])
>>> stats.chi2.sf(3.84, 1)                                                     # ≈ 0.05
>>> type(np.mean([1.0, 2.0])), type(float(np.mean([1.0, 2.0])))
```

Then reproduce today's probability-to-be-best for a demo experiment with the real seed —
the Build book's Day 19 exercise — and confirm two runs give byte-identical output.

### Common mistakes

- **Python loops over arrays.** Vectorise (`Y - theta * (X - E_X)`), reduce with `axis=`.
- **`ddof` mismatch** between `var` and `cov` (both `ddof=1` here).
- **Unseeded `default_rng()`** in anything a user sees.
- **Returning NumPy scalars** from services. `float(...)`, `.tolist()`.

### Key takeaways

- `np.asarray(x, dtype=float)` at every boundary; vectorised arithmetic; `axis=` for
  reductions; `ddof=1` for sample statistics.
- `default_rng(seed)` → `rng.beta(...)`; `argmax`/`bincount`/`mean(bool)` for Monte Carlo
  summaries.
- SciPy: `fisher_exact` (tuple), `ttest_ind(...).pvalue`, `norm.cdf`, `chi2.sf`; clamp and
  `float()` every p-value.
- Pins are coupled: numpy 1.26 / scipy 1.17 / pandas 2.2.

---

## Hour 23 — `pytest`: fixtures, parametrize, `monkeypatch`, mocks, async tests

### What & why

`pytest` discovers `test_*.py` files, `Test*` classes and `test_*` functions (configured
in `pyproject.toml`'s `[tool.pytest.ini_options]`), runs them, and reports. Its power is in
five features this codebase leans on:

1. **Plain `assert`.** `assert resp.status_code == 200, resp.text` — pytest rewrites the
   assertion to show both sides on failure; the optional message adds context.
2. **Fixtures.** Functions decorated `@pytest.fixture` whose _name_ a test lists as a
   parameter; pytest calls them and injects the result. Scopes: `function` (default — fresh
   per test), `session` (once per run). `yield` fixtures clean up after the test. Factory
   fixtures return a function (`make_experiment(**kwargs)`).
3. **`@pytest.mark.parametrize`.** One test body, many inputs, each reported separately.
4. **`monkeypatch`.** A fixture that sets attributes/environment for one test and reverts
   automatically. `unittest.mock.patch`/`MagicMock` for fakes with call recording.
5. **Markers.** `@pytest.mark.asyncio` (run on an event loop), `@pytest.mark.unit`,
   `regression`, `slow` — declared in `pyproject.toml`.

Plus the harness facts from the FastAPI book: the venv, `APP_ENV=test TESTING=true`, a
database per process, no truncation, unique data, `-p no:cov -q` for speed.

### In the wild

A factory fixture with overridable defaults:

```python
# 📁 backend/tests/integration/conftest.py
def make_experiment(db_session: Session, admin_user: User) -> Callable:
    """
    Factory fixture for creating Experiment objects.

    Returns a callable that creates and commits an Experiment to db_session.
    Default fields can be overridden via kwargs.
    """
    def _make_experiment(**kwargs) -> Experiment:
        defaults = {
            "name": "Test Experiment",
            "status": ExperimentStatus.DRAFT,
            "owner_id": admin_user.id,
            "description": "Integration test experiment",
            "hypothesis": "Integration hypothesis",
        }
        defaults.update(kwargs)
        experiment = Experiment(**defaults)
        db_session.add(experiment)
        db_session.commit()
        db_session.refresh(experiment)
        return experiment

    return _make_experiment
```

`defaults.update(kwargs)` then `Experiment(**defaults)` — Hours 4 and 6 in a fixture. A
fixture that depends on it and adds unique data:

```python
# 📁 backend/tests/integration/api/test_tracking_api.py
def active_experiment(db_session, make_experiment):
    """An ACTIVE experiment with a control and a treatment variant."""
    suffix = uuid.uuid4().hex[:8]
    experiment = make_experiment(
        name=f"Tracking API {suffix}",
        key=f"tracking-api-{suffix}",
        status=ExperimentStatus.ACTIVE,
    )
```

A `yield` fixture that parks conflicting rows and restores them — setup before the `yield`,
teardown after:

```python
# 📁 backend/tests/integration/api/test_tracking_api.py
@pytest.fixture
def active_holdout(db_session):
    """An active GlobalHoldout at HOLDOUT_PERCENTAGE; any other active holdout is
    parked for the duration of the test and restored afterwards."""
    parked = db_session.query(GlobalHoldout).filter(GlobalHoldout.is_active == True).all()
    for row in parked:
        row.is_active = False
    holdout = GlobalHoldout(name=f"tracking-holdout-{uuid.uuid4().hex[:8]}", description="Eligibility test holdout",
                            holdout_percentage=HOLDOUT_PERCENTAGE, is_active=True)
    db_session.add(holdout)
    db_session.commit()
    db_session.refresh(holdout)
    ...
```

`parametrize` — one body, ten cases, each named in the report:

```python
# 📁 backend/tests/unit/core/test_targeting_adapter.py
    @pytest.mark.parametrize(
        "dashboard_op, engine_op",
        [
            ("equals", OperatorType.EQUALS),
            ("not_equals", OperatorType.NOT_EQUALS),
            ("contains", OperatorType.CONTAINS),
            ("not_contains", OperatorType.NOT_CONTAINS),
            ("starts_with", OperatorType.STARTS_WITH),
            ("ends_with", OperatorType.ENDS_WITH),
            ("greater_than", OperatorType.GREATER_THAN),
            ("less_than", OperatorType.LESS_THAN),
            ("greater_than_or_equal", OperatorType.GREATER_THAN_OR_EQUAL),
            ("less_than_or_equal", OperatorType.LESS_THAN_OR_EQUAL),
        ],
    )
```

`monkeypatch` for seams you don't own and for settings, reverted automatically:

```python
# 📁 backend/tests/conftest.py
    monkeypatch.setattr(
        "backend.app.services.auth_service.CognitoAuthService.get_user_with_groups",
        mock_get_user_with_groups
    )
```

`MagicMock` for a stand-in that records calls — a unit test of the eligibility gate with a
fake session and fake variants:

```python
# 📁 backend/tests/unit/services/test_assignment_service_eligibility.py
def _variant(name, is_control, allocation=50):
    variant = MagicMock()
    variant.id = uuid4()
    variant.name = name
    variant.is_control = is_control
    variant.traffic_allocation = allocation
    return variant


@pytest.fixture
def experiment():
    exp = MagicMock(spec=Experiment)
    exp.id = uuid4()
    exp.status = ExperimentStatus.ACTIVE
    exp.mutual_exclusion_group_id = None
    exp.targeting_rules = None
```

`MagicMock(spec=Experiment)` restricts the mock to attributes the real class has — an
access to a misspelled attribute raises instead of returning another mock. That's the
_defence_ against the Hour 1 bug in mock-based tests: `spec` your mocks.

An async test, and `pytest.raises` as a context manager:

```python
@pytest.mark.asyncio
async def test_viewer_cannot_update_flag(viewer_user, feature_flag):
    with pytest.raises(HTTPException) as exc:
        await deps.can_update_feature_flag(feature_flag=feature_flag, current_user=viewer_user)
    assert exc.value.status_code == 403
```

The set-comprehension assertion for "all the same," and the pattern of _reading the
database back_ as the oracle:

```python
# 📁 backend/tests/integration/api/test_tracking_api.py
        reasons = {admin_client.post(...).json()["reason"] for user_id in inside}
        assert reasons == {"holdout"}
        ...
            assert _assignment_rows(db_session, active_experiment, user_id) == []
```

The commands, and the config that shapes them:

```bash
source venv/bin/activate && export APP_ENV=test TESTING=true
python -m pytest backend/tests/unit -p no:cov -q                       # fast
python -m pytest backend/tests/unit/core/test_targeting_adapter.py -k semver -v   # one thing, verbose
python -m pytest backend/tests/unit -x --tb=short                      # stop at first failure
python -m pytest backend/tests --cov=backend.app --cov-report=term     # coverage, explicitly
```

```toml
# 📁 pyproject.toml
[tool.pytest.ini_options]
testpaths = ["backend/tests"]
python_files = "test_*.py"
python_functions = "test_*"
python_classes = "Test*"
# Coverage is NOT in addopts: it slows every run and silently produces no
# report under some plugin combinations. Ask for it explicitly:
#   pytest --cov=backend.app --cov-report=term
addopts = ""
markers = [
    "unit: unit tests (fast, no external dependencies)",
    "integration: integration tests (require the database)",
    "e2e: end-to-end tests (require the full stack)",
    "regression: guards a specific past bug",
    "slow: slow running tests",
```

> **Watch Out!** `patch.object(SomeClass, "__new__")` permanently breaks the class's
> constructor slot in CPython and caused cross-suite failures here — patch methods or use
> fixtures, never `__new__`. And a `MagicMock` without `spec` answers _any_ attribute with
> another mock, which is exactly how `get_counters` passed its tests.

> **Did You Know?** The `regression` marker exists so a test that guards a specific past
> bug can say so: `@pytest.mark.regression` with the PR number in the docstring. Grep for
> it when you're about to "simplify" something.

### Try It Yourself

Write `scratch_test_bucket.py` (delete it afterwards):

```python
import hashlib, pytest

def bucket(user_id: str, salt: str) -> int:
    return int(hashlib.md5(f"{user_id}:{salt}".encode(), usedforsecurity=False).hexdigest(), 16) % 100

@pytest.fixture
def salt():
    return "hero_banner"

@pytest.mark.parametrize("user_id", ["u1", "u2", "u3"])
def test_bucket_is_deterministic(user_id, salt):
    assert bucket(user_id, salt) == bucket(user_id, salt)

def test_bucket_in_range(salt):
    assert {bucket(f"u{i}", salt) for i in range(1000)} <= set(range(100))

def test_uniform_enough(salt):
    counts = [0] * 100
    for i in range(20_000):
        counts[bucket(f"u{i}", salt)] += 1
    assert min(counts) > 120 and max(counts) < 280       # ~200 each

def test_uses_env(monkeypatch):
    monkeypatch.setenv("SALT", "x"); import os; assert os.environ["SALT"] == "x"
```

`python -m pytest scratch_test_bucket.py -v -o addopts=""` — note the three parametrised
names in the output. Then `MagicMock(spec=...)` yourself:
`from unittest.mock import MagicMock; m = MagicMock(spec=["get_experiment_counters"]);
m.get_counters` — `AttributeError`.

### Common mistakes

- **Fixed names/keys in fixtures.** Unique suffixes; the session doesn't truncate.
- **Mocks without `spec`.** They answer anything.
- **`await`-less calls to async deps in tests** — the test passes vacuously.
- **Asserting only on JSON.** Read the row.

### Key takeaways

- `assert` with a message; fixtures by name (factory and `yield` shapes); `parametrize`
  for tables of cases; `monkeypatch` for seams and settings; `MagicMock(spec=...)` for
  fakes; `@pytest.mark.asyncio` for async.
- Unique data, database as oracle, unit tests name their integration twin.
- Config in `pyproject.toml`; coverage explicit; `-p no:cov -q` for speed, `-x --tb=short`
  for feedback.

---

## Hour 24 — Putting it together: the Experimently Python style guide

### What & why

Twenty-three hours of conventions, collected as the checklist a reviewer applies. The
tooling enforces the mechanical half:

```toml
# 📁 pyproject.toml
[tool.black]
line-length = 88

[tool.isort]
profile = "black"
line_length = 88

[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true
check_untyped_defs = true
disallow_untyped_decorators = true
no_implicit_optional = true
strict_optional = true

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
disallow_incomplete_defs = false
```

`make lint` runs `black --check`, `isort --check`, `mypy` and `flake8`; `make format` fixes
what's fixable. `disallow_untyped_defs = true` is the line that makes Hour 12 mandatory:
every function in `backend/app` needs annotations, or `mypy` fails the gate (tests are
exempt). `no_implicit_optional` means `x: int = None` is an error — write `Optional[int]`.

### The style guide

**Language and layout**

- Python 3.11; `black` (88 columns) and `isort` (black profile) decide formatting — don't
  argue with them.
- snake_case functions and variables, PascalCase classes, SCREAMING_CASE module constants,
  `_leading_underscore` for internal names. Named constants over magic strings
  (`REASON_HOLDOUT`, `LOCK_NAMESPACE`, `_SEED_MASK`).
- Google-style docstrings on every public function and class: one line, blank line,
  `Args`/`Returns`/`Raises`. Module docstrings that explain _why_ the module exists
  (`scheduler_lock.py`, `event_matching.py` are the models).
- Comments explain decisions and traps, not syntax. `# nosemgrep`/`# nosec`/`# noqa`
  markers carry a justification.

**Imports**

- Fully qualified from the repository root: `from backend.app.…`. Never `from app.`,
  never relative in new code. New models registered in `backend/app/models/__init__.py`.
- Import the defining module, not a re-export. Local (in-function) imports only to break a
  cycle or defer a heavy dependency, with a comment.
- Run from the repository root; `python -m module` for scripts; behaviour under
  `if __name__ == "__main__":`.

**Values and control flow**

- `is None` / `is not None`; `x if x is not None else default` when `0`/`""` are valid;
  `x or default` only when they aren't.
- Early returns over nesting; `enumerate`/`zip`/`any`/`all`; `A if c else B` for the
  ternary; explicit comparisons for numbers (`<= 0`, `>= 100`).
- Clamp with `max(lo, min(hi, x))`; guard `isinstance(x, bool)` before `isinstance(x, int)`.
- Units are not types: percentages are 0–100 in the API, 0.0–1.0 in the Lambdas — read
  the docstring.

**Data**

- Lists of tuples for ordered records of two or three fields; dataclasses beyond that;
  Pydantic at any boundary; dicts at the service/endpoint seam validated by response
  models.
- `.get(default)` for optional keys, `setdefault` to add without overwriting, `list(d.items())`
  before mutating in a loop, `.copy()` before changing a shared dict.
- `frozenset` for constant sets; `sorted()` before anything order-sensitive on a set; one
  `for`/one `if`/one line per comprehension.
- f-strings everywhere except `logger.*(“%s”, …)` and data-driven templates; `.encode()`
  before hashing; raw strings for regex; never build SQL with f-strings.
- `class X(str, Enum)` for values that cross JSON; plain `Enum` in the ORM; convert at the
  boundary, never compare across.

**Functions and classes**

- Type hints on every signature (`mypy` is strict); `Optional[X] = None` for optional;
  `Any` is a confession; `# type: ignore[code]` names the code.
- Keyword arguments for long calls; keyword-only (`*`) parameters for anything with a
  default that shouldn't be positional; `None` defaults for containers.
- Services: constructor-injected `db`, composed services, `add → flush → commit → refresh`.
  `@staticmethod` for pure helpers, `@classmethod` for factories/coercers, `@property` for
  computed attributes, `__repr__` on anything logged or asserted.
- `@wraps` on every decorator; `@contextmanager` generators yield exactly once inside
  `try/finally`; generators consumed once; `next(gen, default)` for first-match.

**Errors**

- Catch specific exceptions in tuples; `except HTTPException: raise` before any broad
  handler; `except asyncio.CancelledError: raise` in task loops; never a bare `except:`.
- Broad catches are loud (log at warning/error, count a metric) and tested against the
  real collaborator. `raise New(...) from exc` when translating.
- Domain exceptions (`InvalidTokenError`, `AccountLockedError`) with data, not strings to
  parse.

**Time and randomness**

- Naive means UTC; normalise at comparisons (`_as_utc`); write naive to naive columns; ISO
  strings always carry `+00:00`; `perf_counter`/`monotonic` for durations.
- `hashlib` for determinism (`usedforsecurity=False` on MD5), `secrets` for keys,
  `hmac.compare_digest` to check them, seeded `default_rng` for Monte Carlo; `float()`/`.tolist()`
  on every NumPy value that leaves a function.

**Concurrency**

- `async def` only awaits; blocking work goes to `to_thread`/`run_in_threadpool`; `def`
  endpoints for heavy handlers; consistent per module.
- Tasks are owned by the lifespan; `cancel()` then `await`; let `CancelledError`
  propagate; sessions never cross a thread boundary.
- `threading.Lock`/`RLock` for cross-thread structures (short critical sections, `deque`
  windows), `asyncio.Lock` for coroutine-only state mutated across awaits, `ContextVar` for
  per-request values (copy before mutate).

**Tests**

- Venv active; `APP_ENV=test TESTING=true`; env before import; unique data;
  `hashed_password` on users; unit tests delete what they commit.
- Fixtures by name, factories for rows, `yield` for setup/teardown, `parametrize` for
  tables, `monkeypatch` for seams and settings, `MagicMock(spec=...)`, `@pytest.mark.asyncio`
  and `await` for async deps.
- Mock what you don't own; drive what you do; the database is the oracle; unit tests name
  their integration twin; `regression` marker for bug guards.

### Try It Yourself

Run the gate on the real code and read one finding of each kind:

```bash
make lint 2>&1 | tail -20
black --check backend/app | head -5
mypy backend/app/core/stats_engine.py backend/app/services/event_matching.py
flake8 backend/app/core/scheduler_lock.py
```

Then review the most recent merged PR (`gh pr list --state merged --limit 1`) against the
guide, section by section, and write down each item it touches and whether it complied.
That review is what you'll be asked to do on every pull request from now on.

### Key takeaways

- The mechanical half is tooling (`black`, `isort`, `mypy` strict, `flake8`); the
  judgement half is this list.
- Every item traces to an Hour, and most trace to a bug the platform actually had.
- Review against it; ship when `make lint` and `make test` are green.

---

## Part VI Workshop

**Q1. Three MD5-to-bucket recipes exist. Which one does an SDK implement, and why that one?**
First four bytes, little-endian unsigned int, divided by 2³² — because it needs no
big-integer type and is pinned across fifteen languages by golden vectors. The server's
variant hash (whole digest `% 100`) is a different function on purpose.

**Q2. Why SHA-256 for API keys but bcrypt for passwords?**
Keys are random 128-bit values looked up on every SDK request — no brute-force risk, fast
lookup needed; SHA-256 is one-way and fast. Passwords are low-entropy and checked once per
login; bcrypt's deliberate slowness is the defence.

**Q3. What does `np.argmax(samples, axis=0)` give for a `[n_variants, n_samples]` array?**
An array of length `n_samples`: for each sample column, the index of the variant with the
largest draw — the "winner" of that Monte Carlo trial.

**Q4. Why `float(np.mean(winner == i))` rather than `np.mean(...)`?**
`np.mean` returns `np.float64`, which `json.dumps` rejects and which can surprise `==` and
`isinstance` checks; `float()` converts to a plain Python float before the value leaves the
function.

**Q5. `MagicMock()` vs `MagicMock(spec=Experiment)` — which would have caught
`get_counters`?**
`spec`: a spec'd mock raises `AttributeError` for attributes the real class lacks; a bare
`MagicMock` answers anything with another mock.

**Q6. `x: int = None` — what does `mypy` say under this repo's config?**
An error: `no_implicit_optional = true` forbids a `None` default on a non-Optional type.
Write `x: Optional[int] = None`.

**Quiz.** Which two modules are exempt from `disallow_untyped_defs`, and why?
→ `tests.*` (an override in `pyproject.toml`): test functions are short, fixture-injected
and read better untyped; the application code has no such exemption.

---

## Exercises

**E6.1 — Two hashes.** Implement both bucket recipes (`whole_digest_bucket` and
`first_four_le_bucket`) and find, over 10,000 user ids and one salt, the fraction of ids
where they disagree. What does that number tell you about "use the SDK hash to predict a
variant"?

**E6.2 — Reproducible Monte Carlo.** Write `ptbb(conversions, totals, n_samples, seed)`
using `default_rng`, Beta(1+c, 1+n−c) posteriors, `argmax` and `bincount`, and assert two
calls with the same seed return identical lists and two seeds return different ones.

**E6.3 — A parametrised regression test.** Write a `@pytest.mark.regression` test for
`_as_utc` (Hour 17) parametrised over a naive datetime, an aware UTC datetime and an aware
`+02:00` datetime, asserting each result is aware UTC and the instants match.

**E6.4 — Spec your mocks.** Rewrite this test so the misspelled method fails:

```python
def test_scheduler_reads_counters():
    svc = MagicMock()
    svc.get_counters.return_value = {}
    assert stats_from(svc) is not None
```

**E6.5 — Style review.** List every style-guide item this function violates and rewrite
it:

```python
from app.models.experiment import Experiment
def get(db, id, active=True, tags=[]):
    e = db.query(Experiment).filter(Experiment.id==id).first()
    if active == True and e.status == "active":
        tags.append("active")
    try:
        return {"e": e, "t": tags, "when": str(datetime.utcnow()) }
    except:
        pass
```

---

## Solutions

**S6.1**

```python
import hashlib, struct

def whole_digest_bucket(uid, salt):
    return int(hashlib.md5(f"{uid}:{salt}".encode(), usedforsecurity=False).hexdigest(), 16) % 100

def first_four_le_bucket(uid, salt):
    d = hashlib.md5(f"{uid}:{salt}".encode(), usedforsecurity=False).digest()[:4]
    return struct.unpack("<I", d)[0] % 100

ids = [f"u{i}" for i in range(10_000)]
disagree = sum(whole_digest_bucket(u, "s") != first_four_le_bucket(u, "s") for u in ids) / len(ids)
print(disagree)     # ≈ 0.99 — they agree only by chance (1 in 100)
```

They're unrelated functions of the same digest; the SDK utility predicts _flag rollout_
buckets only where the server also uses the first-four-bytes recipe (holdout, MEG) and never
the variant hash. Call `/tracking/assign`.

**S6.2**

```python
import numpy as np

def ptbb(conversions, totals, n_samples=10_000, seed=0):
    rng = np.random.default_rng(seed)
    alpha = [1 + c for c in conversions]
    beta = [1 + n - c for c, n in zip(conversions, totals)]
    draws = rng.beta(alpha, beta, size=(n_samples, len(alpha)))
    winners = np.argmax(draws, axis=1)
    return (np.bincount(winners, minlength=len(alpha)) / n_samples).tolist()

a = ptbb([40, 52], [1000, 1000], seed=7); b = ptbb([40, 52], [1000, 1000], seed=7); c = ptbb([40, 52], [1000, 1000], seed=8)
assert a == b and a != c
```

**S6.3**

```python
from datetime import datetime, timezone, timedelta
import pytest
from backend.app.core.rollout_scheduler import _as_utc

@pytest.mark.regression   # PR #65: naive DB timestamps vs aware now() raised on every rollout tick
@pytest.mark.parametrize("value", [
    datetime(2026, 9, 11, 12, 0, 0),
    datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc),
    datetime(2026, 9, 11, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))),
])
def test_as_utc_returns_aware_utc_for_the_same_instant(value):
    result = _as_utc(value)
    assert result.tzinfo == timezone.utc
    assert result == datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    assert result <= datetime.now(timezone.utc)   # the comparison that used to raise
```

**S6.4**

```python
from unittest.mock import MagicMock
from backend.app.services.dynamodb_counter_service import DynamoDBCounterService

def test_scheduler_reads_counters():
    svc = MagicMock(spec=DynamoDBCounterService)
    svc.get_experiment_counters.return_value = {}
    assert stats_from(svc) is not None
    svc.get_experiment_counters.assert_called_once()
```

With `spec=`, `svc.get_counters` raises `AttributeError` — the test now fails if the
production code calls a method the real service doesn't have.

**S6.5** — Violations: `from app.` import (fully qualify); no type hints (`mypy` strict);
`id` shadows a builtin; mutable default `tags=[]`; `active == True` (use `if active`);
comparing an ORM enum to a string (`e.status == ExperimentStatus.ACTIVE`); `e` may be
`None` (guard it); mutating the caller's `tags`; `datetime.utcnow()` stringified without an
offset (use `datetime.now(timezone.utc).isoformat()` for a computed timestamp); a bare
`except: pass` swallowing everything; single-letter names; `black` would reformat the
spacing.

```python
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.models.experiment import Experiment, ExperimentStatus


def get_experiment_summary(
    db: Session, experiment_id: UUID, *, active_only: bool = True, tags: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """Return a small summary dict for an experiment, or None when it does not exist."""
    experiment = db.query(Experiment).filter(Experiment.id == experiment_id).first()
    if experiment is None:
        return None
    result_tags = list(tags or [])
    if active_only and experiment.status == ExperimentStatus.ACTIVE:
        result_tags.append("active")
    return {
        "experiment": experiment,
        "tags": result_tags,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
```

---

_You've done the twenty-four hours. The [FastAPI book](../learn-fastapi-in-24-hours/README.md)
starts exactly here. ⏱️_

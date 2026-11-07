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
  hash) and for the fixed-length key that recognises an event sent twice, and BLAKE2b for
  Monte Carlo seeds (a keyed-size digest that packs into 8 bytes).
- **`struct`** — packs and unpacks bytes to fixed-width integers. `struct.unpack("<I",
  b)` reads 4 bytes as a **little-endian unsigned 32-bit int** — the cross-SDK convention.
- **`secrets`** — cryptographically random values for tokens and keys
  (`secrets.token_hex(16)`). Never `random` for anything security-relevant.
- **`random` / NumPy's `default_rng`** — pseudo-random for statistics, always **seeded** in
  this codebase so results are reproducible.

#### Why there is more than one hash-to-number recipe

You've now seen several ways the codebase turns MD5 output into a bucket, and they are
deliberately different functions. `backend/app/core/consistent_hash.py` keeps the list in
its docstring; the ones this book has met:

| Use                       | Recipe                                                                 | Where |
| ------------------------- | ---------------------------------------------------------------------- | ----- |
| Experiment assignment     | `MD5("{user}:{experiment.key}")`, first 4 bytes LE ÷ 2³², × 100 (UUID only when `key` is empty) | `assignment_service._hash_user_to_variant` via `consistent_hash.bucket_of` |
| SDK hash utility          | the same function, as a float in `[0, 1)`                             | every SDK; pinned by `tests/sdk-contract/golden-vectors.json` |
| Flag rollout              | `MD5("{user}:{flag.key}")`, whole 128-bit digest, `% 100`              | `feature_flag_service._evaluate_percentage_rollout` |
| Rule rollout              | `MD5("{user}:{rule.id}")`, whole digest, `% 100`                       | `rules_engine.should_include_in_rollout` |
| Bandit routing            | `MD5("{user}:{experiment.id}:bandit")`, whole digest, `% 10000`        | `services/bandit_routing.py` |
| Holdout                   | `MD5("{user}:{holdout.hash_salt}")`, first 4 bytes LE, `% 100`          | `global_holdout_service.holdout_bucket` |
| Mutual-exclusion group    | `MD5("{user}:{salt}")`, first 4 bytes LE, ÷ 2³²                        | `mutual_exclusion_service` |

They're all deterministic and uniform; they're just not the same function, and the salts
make them independent. The "first four bytes little-endian ÷ 2³²" recipe is the contract one
because every language can implement it without a big-integer type.

Experiment assignment used to be a whole-digest `% 100` over `"{user}:{experiment.id}"`
too. That was a bug, fixed in commit `b46e5259`, and a silent one: for `user-123`/`my-flag`
the whole-digest reading gives bucket **79** and the contract gives **69**, and the old code
hashed the experiment's UUID, which no SDK has. An SDK evaluating locally and the API could
put one user in two different variants, with no error anywhere. Now the server assigns with
the contract function, salted with the experiment's public `key`. (The flag-rollout function
was not changed: changing any of these re-buckets users, so each moves only as a deliberate,
stated change.)

### In the wild

The contract function — `struct` doing the one step that is easy to get wrong:

```python
# 📁 backend/app/core/consistent_hash.py
def hash_user(user_id: str, key: str) -> float:
    """A stable float in [0.0, 1.0) for *user_id* under *key*.

    ``key`` is whatever namespaces the decision -- a flag key, an experiment
    key, a holdout salt. The same user under two different keys gets two
    unrelated positions, which is what stops every experiment bucketing the
    same users together.

    >>> round(hash_user("user-123", "my-flag"), 16)
    0.6927449859213084
    """
    digest = hashlib.md5(
        f"{user_id}:{key}".encode("utf-8"), usedforsecurity=False
    ).digest()
    (uint32,) = struct.unpack_from("<I", digest[:4])
    return uint32 / HASH_DIVISOR


def bucket_of(user_id: str, key: str) -> int:
    """The same position as an integer in [0, 100).
    ...
    """
    return int(hash_user(user_id, key) * BUCKET_COUNT)
```

The `>>>` lines in the docstring are a **doctest**: an example that is also a checkable
claim (`python -m doctest` runs it). `HASH_DIVISOR` and `BUCKET_COUNT` are annotated
`Final[int]`, which tells a type checker they must never be reassigned.

Whole-digest bucketing — the flag rollout:

```python
# 📁 backend/app/services/feature_flag_service.py
        hash_input = f"{user_id}:{flag.key}"
        hash_value = int(
            hashlib.md5(hash_input.encode(), usedforsecurity=False).hexdigest(), 16
        )

        # Get bucket (0-99)
        bucket = hash_value % 100
```

First-four-bytes bucketing, read as `% 100` — the holdout — and the SDK's own copy of the
contract function:

```python
# 📁 backend/app/services/global_holdout_service.py
def holdout_bucket(user_id: str, salt: str) -> int:
    combined = f"{user_id}:{salt}".encode("utf-8")
    hash_bytes = hashlib.md5(combined, usedforsecurity=False).digest()[:4]
    hash_int = struct.unpack("<I", hash_bytes)[0]
    return hash_int % 100
```

```python
# 📁 sdk/python/controlvar/hashing.py
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
    """Derive the deterministic RNG seed for a Monte Carlo computation.
    ...
    """
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

SHA-256 once more, as a fingerprint of _several_ strings. An SDK may send an event with an
`event_id`, and an event sent again with the same `event_id` (a retry, say) is stored once
(since v0.40.0, #1543). The server recognises "the same event" by a key built from four parts:

```python
# 📁 backend/app/services/event_dedup.py
def _encoded(part: str) -> bytes:
    """``<byte length>:<UTF-8 bytes>``: the length is ASCII decimal digits."""
    data = part.encode("utf-8")
    return str(len(data)).encode("ascii") + b":" + data


def dedup_key(
    experiment_id: Optional[Union[UUID, str]],
    feature_flag_id: Optional[Union[UUID, str]],
    event_name: str,
    event_id: str,
) -> str:
    """The 64-character key of one event within its user.
    ...
    """
    digest = hashlib.sha256()
    for part in (
        _id_text(experiment_id),
        _id_text(feature_flag_id),
        event_name,
        event_id,
    ):
        digest.update(_encoded(part))
    return digest.hexdigest()
```

Why not `hashlib.sha256(f"{experiment}:{flag}:{name}:{event_id}".encode())`? Because a
separator separates only when no part can contain it, and `event_name` and `event_id` are
strings a client chooses: `("a:b", "c")` and `("a", "b:c")` both join to `a:b:c`, and two
different events would share one key. Writing each part's length in front of it removes the
ambiguity — `2:ab` + `1:c` is not `1:a` + `2:bc` — because a reader of the bytes always knows
where each part ends. The module docstring says it in one line: ``("ab", "c")`` and
``("a", "bc")`` differ, "which a separator would not guarantee." Three more details:

- the length counts **bytes**, not characters (`len("é")` is 1, `len("é".encode("utf-8"))`
  is 2), because bytes are what gets hashed;
- `digest.update(...)` feeds the hash piece by piece, which gives the same digest as hashing
  the concatenation once;
- the result is always 64 hex characters, whatever the input (the docstring names "a
  255-character `event_id` of four-byte characters"), so it fits a fixed `String(64)` column:
  `event_dedup_keys`, whose primary key is the user id plus this key.

The bucketing hashes earlier in this Hour join with `:` and that is fine for them: a
collision there only puts two (user, key) pairs in the same bucket, which every bucketing hash
does by design. For an _identity_ — is this the same event? — a collision would silently drop a
real one. And because the key is stored, its encoding can never change quietly:
`backend/tests/unit/services/test_event_dedup.py` pins the exact bytes and digests, since a
different encoding would stop recognising every event already stored.

`secrets.token_hex(32)` is also the recommended way to generate the settings secrets the
production validators demand:

```python
# 📁 backend/app/core/config.py
                    'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'
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
>>> from backend.app.services.global_holdout_service import holdout_bucket
>>> from sdk.python.controlvar.hashing import consistent_hash   # from the repo root; else copy the function
>>> secrets.token_hex(16), len(secrets.token_hex(16))
>>> hmac.compare_digest("abc", "abc"), hmac.compare_digest("abc", "abd")
>>> hashlib.blake2b(b"exp|2026-09-11|10000", digest_size=8).digest()
>>> int.from_bytes(hashlib.blake2b(b"exp|2026-09-11|10000", digest_size=8).digest(), "big") & ((1 << 63) - 1)
>>> def enc(part): data = part.encode("utf-8"); return str(len(data)).encode("ascii") + b":" + data
>>> ":".join(["a:b", "c"]) == ":".join(["a", "b:c"])            # the separator collides
True
>>> enc("ab") + enc("c"), enc("a") + enc("bc"), enc("é")
(b'2:ab1:c', b'1:a2:bc', b'2:\xc3\xa9')
>>> from backend.app.services.event_dedup import dedup_key
>>> dedup_key(None, None, "purchase", "order-1001")
'9f9e77c14972cd70e3e493c3a8b0c9aa865a817b545dfbf037e29a13f2160257'
>>> hashlib.sha256(b"0:0:8:purchase10:order-1001").hexdigest() == dedup_key(None, None, "purchase", "order-1001")
True
```

For `user-42`/`hero_banner` the three readings give 74 (whole digest `% 100`), 91 (first four
bytes `% 100`, the holdout reading) and 0.1705… (÷ 2³²), so `bucket_of` gives 17. Same digest,
three numbers. The last lines rebuild the dedup key by hand: no experiment and no flag are
`0:` each, then `8:purchase` and `10:order-1001`.

Then run the golden vectors, from the repository root with the venv active:

```bash
python -m pytest tests/sdk-contract -q -o addopts=""        # the harness's own tests (network and node)
python tests/sdk-contract/hash_contract.py python           # the Python SDK against the vectors
```

The first command also checks the _published_ SDK releases against the server's ruleset
(`test_published_ruleset.py`): it downloads them from npm and PyPI and runs the JavaScript one
with `node`, and it fails, rather than skips, without either. The second needs nothing but the
repository.

The second prints `python: OK -- 9/9 hash vectors, 2/2 rollout vectors, 9/9 md5 digests`.
(`make test-sdk` runs both, plus the JavaScript, edge, React Native and Go SDKs, which need
their toolchains.) Open `tests/sdk-contract/golden-vectors.json` to see what the contract
pins. Five SDKs run against it in the `SDK Contract Tests` job; iOS and Android check their
own hash in their unit tests, and `hash_contract.py --list` names the rest and why they are
not run there.

### Common mistakes

- **Hashing a `str`.** `.encode()` first.
- **Using the whole-digest recipe to predict an experiment or SDK bucket** (or the contract
  recipe to predict a flag rollout). Different functions; check which one the caller uses.
- **`random` for tokens.** `secrets`.
- **`==` on secrets.** `hmac.compare_digest`.
- **Hashing `f"{a}:{b}"` as an identity** when a part can contain `:`. Length-prefix each
  part (in bytes), as `dedup_key` does.

### Key takeaways

- `hashlib` = pure functions of bytes: MD5 (bucketing, `usedforsecurity=False`), SHA-256
  (API keys), BLAKE2b (seeds).
- `struct.unpack("<I", digest[:4])` is the cross-SDK integer, and experiment assignment uses
  it (`bucket_of`, salted with `experiment.key`); `int(hex, 16)` is the whole-digest one that
  flag rollout uses; salts make buckets independent.
- `secrets` for keys and settings secrets; `hmac.compare_digest` to check them.
- Several strings into one stored key: `<byte length>:<bytes>` per part, then SHA-256 — a
  fixed length, and no two different tuples encode to the same bytes — and pin the encoding
  with a test, because once keys are stored it can never change.
- Seeded `default_rng` everywhere randomness is shown to a user.

---

## Hour 22 — NumPy and SciPy: arrays, RNGs, and a statistical test

### What & why

**NumPy** gives Python fast arrays: `np.ndarray` holds numbers in contiguous memory, and
operations are _vectorised_ — `Y - theta * (X - E_X)` runs over a million elements in C
without a Python loop. **SciPy** builds on it: `scipy.stats` has the distributions and
tests (`fisher_exact`, `norm.cdf`, `chi2.sf`, `ttest_ind`) the statistics services call.
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
   `(odds_ratio, p_value)` (in SciPy 1.17 a `SignificanceResult` that unpacks like a tuple); `stats.ttest_ind(...)` → an object with `.pvalue`;
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
    def apply_cuped(
        Y: np.ndarray,
        X: np.ndarray,
        theta: float,
        E_X: float,
    ) -> np.ndarray:
        """Apply the CUPED transformation to a set of observations.

        Y_cuped = Y - θ * (X - E[X])
        ...
        """
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
def _draw_posterior_samples(
    posteriors: List[Dict[str, float]],
    n_samples: int,
    seed: Optional[int],
) -> np.ndarray:
    """Draw ``n_samples`` from each Beta posterior; shape ``[n_variants, n_samples]``."""
    rng = make_rng(resolve_seed(seed, n_samples, posteriors))
    return np.array(
        [rng.beta(float(p["alpha"]), float(p["beta"]), n_samples) for p in posteriors]
    )


def _ptbb_from_samples(samples: np.ndarray) -> List[float]:
    """Probability to be best per variant from an already-drawn sample matrix."""
    winner = np.argmax(samples, axis=0)  # shape: [n_samples]
    return [float(np.mean(winner == i)) for i in range(samples.shape[0])]
```

(`compute_probability_to_be_best` draws the samples with the first function — or takes a
matrix a caller already drew, so two statistics can share one set of draws — and returns
`_ptbb_from_samples(samples)`.)

`np.argmax(samples, axis=0)` — for each of the `n_samples` columns, the index of the
largest row (variant); `winner == i` is a boolean array; its mean is the fraction of
samples where variant `i` won. Ten thousand samples, two NumPy calls, no loop.

The same idea shaped for the bandit, with `bincount` instead of a loop of means:

```python
# 📁 backend/app/services/bandit_service.py
        rng = make_rng(ThompsonSampling.resolve_seed(seed, n_samples, alpha, beta))
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

SciPy tests — three different return shapes. The results endpoint analyses a conversion
metric as a proportion (converting users over assigned users) with Fisher's exact test, which
returns a tuple:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
            contingency_table = [
                [variant_conversions, variant_non_conversions],
                [control_conversions, control_non_conversions],
            ]

            try:
                odds_ratio, p_value = stats.fisher_exact(contingency_table)
                is_significant = p_value < alpha
```

A revenue, count, duration or custom metric (`MEAN_METRIC_TYPES`) is analysed as its mean per
assigned user, with Welch's t-test. The comparison is made from each variant's _sums_ — its
user count, the sum of `y - k` and the sum of `(y - k)²` — so SciPy's `ttest_ind_from_stats`
takes each arm's mean, standard deviation and size, and returns a result object with
`.pvalue`:

```python
# 📁 backend/app/services/sufficient_stats_analysis.py
    # The centred means go in, never k + c: at a large k the reconstructed
    # means round away most of the difference.
    p_value = float(
        stats.ttest_ind_from_stats(
            arm.c,
            math.sqrt(arm.var),
            arm.n,
            control.c,
            math.sqrt(control.var),
            control.n,
            equal_var=False,
        ).pvalue
    )
```

The comment is Hour 2's `0.1 + 0.2 != 0.3` grown up. The sums are kept centred on a constant
`k` (the grand mean), so `arm.c` is the arm's mean _minus_ `k`. Adding `k` back before the test
would give two large, nearly equal floats, and a 64-bit float keeps only about 16 significant
digits: subtract two such numbers and most of the digits that carried the difference are gone.
The test only needs the difference, so the centred values go in.

The analysis service also carries a z-test (a plain float from `norm.cdf`) and a Welch's t-test
over raw lists (a result object with `.pvalue`):

```python
# 📁 backend/app/services/analysis_service.py
        z = (p_t - p_c) / se
        p_value = 2.0 * (1.0 - float(stats.norm.cdf(abs(z))))
        return min(1.0, max(0.0, p_value))

    def welch_t_test(
        self,
        control_values: List[float],
        treatment_values: List[float],
    ) -> float:
        """Welch's t-test for two independent groups (unequal variances).

        Returns the two-tailed p-value. Returns NaN when the within-group
        variance is zero (identical data), which callers should handle.
        """
        result = stats.ttest_ind(control_values, treatment_values, equal_var=False)
        return float(result.pvalue)
```

(Same test, two inputs: `ttest_ind` takes the observations, `ttest_ind_from_stats` takes
summaries of them. Working from summaries is that module's design — its docstring says it
"reads no database", so a caller with the same sums from elsewhere, such as the warehouse
module, gets the same numbers.)

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
        log_lambda = 0.5 * math.log(ratio) + tau_squared * Z_n**2 / (
            2.0 * (V_n + tau_squared)
        )
        lambda_ratio = math.exp(min(log_lambda, _LOG_FLOAT_MAX))
```

The statistic is a product, `sqrt(ratio) * exp(...)`, but the code adds in log space and
only exponentiates at the end, after capping the log at the largest value a `float` can
hold: an overwhelming difference would otherwise make `math.exp` raise `OverflowError`.
Doing arithmetic on logs and exponentiating once is the standard way to keep a product of
very large and very small factors finite.

> **Watch Out!** NumPy scalars leak. `np.float64` happens to subclass `float`
> (`isinstance(np.float64(1), float)` is `True`, and `json.dumps` accepts it), but
> `isinstance(np.int64(1), int)` is `False` and `json.dumps(np.int64(1))` raises
> `TypeError: Object of type int64 is not JSON serializable`; so does `np.float32`. The
> codebase wraps returned numbers in `float(...)` or `.tolist()` so the type never depends on
> which NumPy scalar the arithmetic happened to produce. Do the same.

> **Did You Know?** The versions are pinned together for a reason: `numpy==1.26.4` because
> `scipy==1.17.1` needs ≥ 1.26 and NumPy 2.x is a separate major bump; `pandas==2.3.3`
> because `statsmodels==0.15.0` needs ≥ 2.2. A Dependabot grouped update once tried to move them
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
- SciPy: `fisher_exact` (tuple), `ttest_ind(...).pvalue` and `ttest_ind_from_stats(...).pvalue`,
  `norm.cdf`, `chi2.sf`; clamp and `float()` every p-value.
- Subtracting two large, nearly equal floats loses the difference; keep values centred, as the
  mean comparison does.
- Pins are coupled: numpy 1.26 / scipy 1.17 / pandas 2.3 / statsmodels 0.15.

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
   `regression`, `slow`, `smoke`, `modules`, `benchmark` — declared in `pyproject.toml`.
   With `asyncio_mode = "auto"` set there, pytest-asyncio runs any `async def` test on an
   event loop even without the marker; the marker is still written, for readers.

Plus the harness facts from the FastAPI book: the venv, `APP_ENV=test TESTING=true` (the
current name for the first is `ENVIRONMENT=test`; `APP_ENV` still works and logs a
deprecation warning), a database per process, no truncation, unique data, `-p no:cov -q` for
speed. Running everything collects 34,246 tests at `09d815ea` (30,394 under
`backend/tests`, 3,852 under `modules/backend/tests`; measured with
`pytest --collect-only -q`, leaving out eight warehouse test files that need `duckdb` and
`sqlglot`).

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
    parked = (
        db_session.query(GlobalHoldout).filter(GlobalHoldout.is_active == True).all()
    )
    for row in parked:
        row.is_active = False
    holdout = GlobalHoldout(
        name=f"tracking-holdout-{uuid.uuid4().hex[:8]}",
        description="Eligibility test holdout",
        holdout_percentage=HOLDOUT_PERCENTAGE,
        is_active=True,
    )
    db_session.add(holdout)
    db_session.commit()
    db_session.refresh(holdout)
    yield holdout
    db_session.rollback()
    ...
    for row in parked:
        db_session.merge(row).is_active = True
    db_session.commit()
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
        mock_get_user_with_groups,
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
async def test_viewer_cannot_create_flag(viewer_user):
    with pytest.raises(HTTPException) as exc:
        await deps.can_create_feature_flag(current_user=viewer_user)
    assert exc.value.status_code == 403
```

(Use a non-superuser of each role: the "admin" fixtures are usually superusers, which
bypass every permission check, so a test with one proves nothing about the role table.)

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
...
testpaths = ["backend/tests", "modules/backend/tests"]
python_files = "test_*.py"
python_functions = "test_*"
python_classes = "Test*"
asyncio_mode = "auto"
# Coverage is NOT in addopts: it slows every run and silently produces no
# report under some plugin combinations. Ask for it explicitly:
#   pytest --cov=backend.app --cov-report=term
addopts = ""
markers = [
    "unit: unit tests (fast, no external dependencies)",
    "integration: integration tests (require the database)",
    "e2e: end-to-end tests (require the full stack)",
    "regression: guards a specific past bug",
    "modules: needs the modules package; skipped in a core build",
    "smoke: wiring checks that must hold for any build of this tree",
    "slow: slow running tests",
    "benchmark: absolute wall-clock check; skipped unless RUN_BENCHMARKS=1 (backend/tests/benchmark_gate.py)",
    ...
```

`modules/backend/tests` is the optional modules' suite; in a core checkout the directory is
absent. The `benchmark` marker exists because a test that asserts one wall-clock timing
(`assert duration < 0.1`) is flaky on a busy CI runner; timings are taken as the best of
several runs, and absolute ones run only when asked.

> **Watch Out!** `patch.object(SomeClass, "__new__")` permanently breaks the class's
> constructor slot in CPython and caused cross-suite failures here — patch methods or use
> fixtures, never `__new__`. And a `MagicMock` without `spec` answers _any_ attribute with
> another mock, which is exactly how `get_counters` passed its tests.

> **Did You Know?** The `regression` marker exists so a test that guards a specific past
> bug can say so: `@pytest.mark.regression` with the pull request number in the docstring.
> Every bug fix carries one, and CI checks the habit: the `regression-guard` job fails a pull
> request labelled `bug` that changes no test file. Grep for the marker when you're about to
> "simplify" something.

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

## Hour 24 — Putting it together: the Controlvar Python style guide

### What & why

Twenty-three hours of conventions, collected as the checklist a reviewer applies. The
tooling enforces the mechanical half, and one tool does the Python part of it: **ruff**,
which replaced `black` (formatting), `isort` (import order) and `flake8` (lint):

```toml
# 📁 pyproject.toml
[tool.ruff]
# Ruff replaces black (format), isort (import order) and flake8 (lint).
# `make lint` and the `lint` CI job run exactly what is configured here.
line-length = 88
target-version = "py311"
...
[tool.ruff.lint]
...
select = [
    "F",      # pyflakes — real errors (undefined names, unused imports/vars)
    "E4",     # import placement/shadowing
    "E7",     # statement-level errors (comparisons, ambiguous names)
    "E9",     # syntax / IO errors
    "W",      # trailing whitespace, missing newline at EOF
    "I",      # import sorting (replaces isort)
    "C4",     # flake8-comprehensions
    "PIE",    # misc lints (duplicate class fields, unnecessary placeholders)
    "RET501", # `return None` when None is the only return
    "RUF010", # f-string conversions: use !r / !s instead of repr()/str()
    "RUF022", # __all__ kept sorted
    "RUF100", # noqa comments that no longer suppress anything
    "T20",    # no print()/pprint() in shipped code (scripts are exempt below)
    "EXE",    # shebang present iff the file is executable
    "LOG",    # logging misuse
    "PLE",    # pylint errors
    "UP032",  # str.format() that should be an f-string
]
...
ignore = [
    # `== None` / `== True` build SQL expressions in SQLAlchemy; ruff's
    # suggestion (`is None` / `is True`) evaluates in Python and produces
    # `WHERE false`, silently returning no rows.
    "E711",
    "E712",  # `== True` is how SQLAlchemy builds a boolean filter expression
    "E741",  # `l`/`I` appear in statistical code where they match the formula
    "F403",  # star imports in a handful of aggregating modules
]
```

`make format` runs `ruff format` and `ruff check --fix` over `backend/ modules/ scripts/`.
`make lint` runs everything the `lint` CI job runs: `ruff check` and `ruff format --check`
on the same three directories, `lint-imports` (the `[tool.importlinter]` contracts from
Hour 9), `reuse lint` (every file carries a licence), the requirements-lock check, and, for
the rest of the repository, `eslint` + `tsc`, `hadolint` and `actionlint`. Read the
comments in the `select` list: each family maps to an Hour — `T20` (no `print` in shipped
code; log instead), `LOG`, `C4` (comprehensions, Hour 7), `I` (import order, Hour 9),
`RUF100` (no stale `noqa`, Hour 13) — and the `ignore` list is the SQLAlchemy exception to
Hour 2's `is None` rule.

**`mypy` is configured but not gated.** The same file carries a strict `[tool.mypy]` block:

```toml
# 📁 pyproject.toml
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

No CI job, `make` target or pre-commit hook runs it, and `mypy backend/app/` reports 1,053
errors in 156 files (mypy 1.20.2, the pinned requirements, measured for this edition). So `disallow_untyped_defs = true` states the
goal — every function in `backend/app` annotated — not something a pull request is refused
for. Treat it as a tool you run on the file you're changing: new code should be clean under
it, even though the tree isn't. `no_implicit_optional` means `x: int = None` is an error —
write `Optional[int]`.

### The style guide

**Language and layout**

- Python 3.11; `ruff format` (88 columns) decides formatting and ruff's `I` rules decide
  import order — don't argue with them. `make format` before you push.
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
  never relative in new code. A new core model goes into `CORE_MODEL_MODULES` in
  `backend/app/models/__init__.py`; nothing in `backend/app` imports `modules` (`lint-imports`).
- Import the defining module, not a re-export. Local (in-function) imports only to break a
  cycle or defer a heavy dependency, with a comment.
- Run from the repository root; `python -m module` for scripts; behaviour under
  `if __name__ == "__main__":`.

**Values and control flow**

- `is None` / `is not None`; `x if x is not None else default` when `0`/`""` are valid;
  `x or default` only when they aren't.
- Early returns over nesting; `enumerate`/`zip`/`any`/`all`; `A if c else B` for the
  ternary; explicit comparisons for numbers (`<= 0`, `>= 100`).
- Before comparing neighbours, put the records in the order their consumer uses
  (`sorted(self.stages, key=lambda stage: stage.stage_order)`), not the order they arrived in.
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
- Regexes compiled once at module level; `fullmatch` or `\A…\Z` for a whole-string check;
  on text from outside, a character class or a bounded quantifier, never nested repetition.
  Where the program already knows the structure (the matched route), don't parse text for it.
- `class X(str, Enum)` for values that cross JSON; plain `Enum` in the ORM; convert at the
  boundary, never compare across.

**Functions and classes**

- Type hints on every signature (the `[tool.mypy]` config is strict, though no gate runs it);
  `Optional[X] = None` for optional; `Any` is a confession; `# type: ignore[code]` names the
  code.
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
- An endpoint's catch-all is `raise unexpected_failure(e, "<operation>", "<sentence>",
  db=db, logger=logger)`: rollback, the exception type and traceback in the log under the
  request ID, a fixed sentence to the caller. Never `detail=str(e)` on a 500, and log the
  exception's type rather than its text where the text can carry user data.
- Domain exceptions (`InvalidTokenError`, `AccountLockedError`) with data, not strings to
  parse.

**Time and randomness**

- Naive means UTC; normalise at comparisons (`_as_utc`); write naive to naive columns; ISO
  strings always carry `+00:00` (`normalize_event_timestamp`, enforced by the
  `UTCTimestampString` column type); an aware value meets a `timestamp without time zone`
  column only through `UTCDateTime`, never through the session's time zone; a local
  wall-clock time is read in its `zoneinfo` zone and converted to UTC before comparing;
  `perf_counter`/`monotonic` for durations.
- `hashlib` for determinism (`usedforsecurity=False` on MD5), `secrets` for keys,
  `hmac.compare_digest` to check them, seeded `default_rng` for Monte Carlo; several strings
  into one stored key are length-prefixed, not joined with a separator (`dedup_key`);
  `float()`/`.tolist()` on every NumPy value that leaves a function.

**Concurrency**

- `async def` only awaits; blocking work goes to `to_thread`/`run_in_threadpool` (the
  scheduler lock does, through `async_scheduler_lock`); a scheduler tick's synchronous work
  goes through `run_in_worker`, a coroutine body as `lambda: asyncio.run(body())`; an
  `async def` over synchronous code is not concurrent, however many `await`s it has; `def`
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
  their integration twin; a `@pytest.mark.regression` test with every bug fix (the
  `regression-guard` job checks a `bug`-labelled pull request changes a test).

### Try It Yourself

Run the gate on the real code and read one finding of each kind:

```bash
ruff check backend/ modules/ scripts/           # "All checks passed!" on a clean tree
ruff format --check backend/ modules/ scripts/  # "... files already formatted"
lint-imports                                    # "Contracts: 3 kept, 0 broken."
mypy backend/app/core/stats_engine.py           # not a gate: read what it says, and why
```

(`make lint` runs the first three and more, including the frontend's `eslint` and `tsc`,
which need `npm install` run in `frontend/` first.) Then give ruff something to find — a scratch file with
`print(x)` and `if x == None: return None` in a function. `ruff check scratch.py` reports
`T201` for the `print` and `RET501` for the `return None`, and says nothing about `x == None`:
that rule is switched off for SQLAlchemy's sake (Hour 2).

Then review the most recent merged PR (`gh pr list --state merged --limit 1`) against the
guide, section by section, and write down each item it touches and whether it complied.
That review is what you'll be asked to do on every pull request from now on.

### Key takeaways

- The mechanical half is tooling: `ruff` (format, import order, lint) and `lint-imports`,
  run by `make lint` and CI; `mypy` is configured, not gated. The judgement half is this list.
- Every item traces to an Hour, and most trace to a bug the platform actually had.
- Review against it; ship when `make lint` and `make test` are green.

---

## Part VI Workshop

**Q1. Several MD5-to-bucket recipes exist. Which one does an SDK implement, and why that
one?**
First four bytes, little-endian unsigned int, divided by 2³² — because it needs no
big-integer type, and `tests/sdk-contract/golden-vectors.json` pins it (five SDKs are run
against the vectors in CI; iOS and Android test their own hash in their unit tests). It is
also the server's **experiment-assignment** hash (`consistent_hash.bucket_of`, salted with
the experiment's `key`), so an SDK and the API put a user in the same variant. It is _not_
the flag-rollout function, which reads the whole digest `% 100`.

**Q2. Why SHA-256 for API keys but bcrypt for passwords?**
Keys are random 128-bit values looked up on every SDK request — no brute-force risk, fast
lookup needed; SHA-256 is one-way and fast. Passwords are low-entropy and checked once per
login; bcrypt's deliberate slowness is the defence.

**Q3. What does `np.argmax(samples, axis=0)` give for a `[n_variants, n_samples]` array?**
An array of length `n_samples`: for each sample column, the index of the variant with the
largest draw — the "winner" of that Monte Carlo trial.

**Q4. Why `float(np.mean(winner == i))` rather than `np.mean(...)`?**
`np.mean` returns an `np.float64`. That one happens to subclass `float`, so `json.dumps`
accepts it, but its siblings (`np.int64`, `np.float32`) are rejected and none of them has a
plain `repr`; `float()` converts to a plain Python float before the value leaves the
function, so the type never depends on which NumPy scalar the arithmetic produced.

**Q5. `MagicMock()` vs `MagicMock(spec=Experiment)` — which would have caught
`get_counters`?**
`spec`: a spec'd mock raises `AttributeError` for attributes the real class lacks; a bare
`MagicMock` answers anything with another mock.

**Q6. `x: int = None` — what does `mypy` say under this repo's config?**
An error: `no_implicit_optional = true` forbids a `None` default on a non-Optional type.
Write `x: Optional[int] = None`.

**Q7. The bucketing hashes join `f"{user_id}:{key}"`, but `dedup_key` length-prefixes its
parts. Why the difference?**
`event_name` and `event_id` are client-chosen text that may contain `:`, so a joined string
can make two different events one key, and the second would be dropped as a duplicate. A
length prefix (in bytes) makes every tuple of parts encode differently. For bucketing, a
collision only puts two pairs in the same bucket, which is harmless.

**Quiz.** Which modules are exempt from `disallow_untyped_defs`, and does that exemption
change what a pull request must pass?
→ `tests.*` (an override in `pyproject.toml`): test functions are short, fixture-injected
and read better untyped; the application code has no such exemption. It changes nothing a
pull request must pass, because no gate runs `mypy`. (Running it also prints
`unused section(s): module = ['tests.*']`: the test files live under `backend/tests`, so the
pattern matches no module.)

---

## Exercises

**E6.1 — Two hashes.** Implement both bucket recipes (`whole_digest_bucket` and
`first_four_le_bucket`) and find, over 10,000 user ids and one salt, the fraction of ids
where they disagree. What does that number tell you about "use the SDK hash to predict a
flag rollout"?

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

They're unrelated functions of the same digest. The SDK hash agrees with the server's
_experiment assignment_ (which uses the contract function, `bucket_of`) but not with its flag
rollout, which reads the whole digest — for flags, ask the server
(`GET /api/v1/feature-flags/evaluate/{key}`). And for an experiment, call `/tracking/assign`
anyway: assignment is sticky, and holdouts, mutual exclusion and targeting can make a user
ineligible before any hash is consulted.

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

@pytest.mark.regression   # commit 56c49e4f: naive DB timestamps vs aware now() raised on every rollout tick
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
from modules.backend.app.services.dynamodb_counter_service import DynamoDBCounterService  # a full checkout

def test_scheduler_reads_counters():
    svc = MagicMock(spec=DynamoDBCounterService)
    svc.get_experiment_counters.return_value = {}
    assert stats_from(svc) is not None
    svc.get_experiment_counters.assert_called_once()
```

With `spec=`, `svc.get_counters` raises `AttributeError` — the test now fails if the
production code calls a method the real service doesn't have.

**S6.5** — Violations: `from app.` import (fully qualify); no type hints (the house rule, and what the `[tool.mypy]` config asks for);
`id` shadows a builtin; mutable default `tags=[]`; `active == True` (use `if active`);
comparing an ORM enum to a string (`e.status == ExperimentStatus.ACTIVE`); `e` may be
`None` (guard it); mutating the caller's `tags`; `datetime.utcnow()` stringified without an
offset (use `datetime.now(timezone.utc).isoformat()` for a computed timestamp); a bare
`except: pass` swallowing everything; `datetime` used but never imported; single-letter
names. Tooling catches only some of these: `ruff check` reports `I001` (import block),
`F821` (undefined `datetime`) and `E722` (bare `except`), and `ruff format` would fix the
spacing — but it says nothing about `active == True`, because `E712` is ignored for
SQLAlchemy's sake. The rest is review.

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

# Feature-flag lifecycle: create, operate and retire

[← Field guides](README.md) · Related: [Complete experiment walkthrough](experiment-lifecycle.md)

**Outcome:** ship a flag with an owner, a tested failure behavior and a removal path. Rollout is one
stage of the lifecycle; a flag that remains forever can become an undocumented dependency.

## Classify the flag before implementing it

A release flag exposes new code gradually. An experiment flag supports a measured comparison. An
operational kill switch protects a risky integration. A configuration flag may be long-lived. Record
which one you need, because their defaults, owners, evidence and expected lifetimes differ.

Recommended metadata includes a stable key, purpose, owner, creation date, intended removal date or
review cadence, safe fallback, consuming services, expected payload shape and operational runbook.
This is a team-owned record; do not assume Experimently automatically enforces owner assignment,
expiry, stale-flag detection or code-reference discovery at this snapshot.

Name keys for a capability rather than a temporary ticket. Do not reuse a retired key for a different
meaning while old clients may still send it. A boolean whose sense changes from “new checkout” to
“disable checkout” can create a particularly dangerous migration.

## Implement both branches and the failure path

The [creation guide][create] describes targeting and evaluation. Identify what the application does
when evaluation is unavailable, the key is missing, context is malformed or the SDK cannot use its
cached state. Test both enabled and disabled branches with valid payloads and a safe fallback.
The server's result is only one part of the behavior; the application interprets it.

Treat configuration payloads as a contract. Validate values and bound risky settings in the consumer.
For a payload change, account for old mobile releases and rolling server deployments. A new server
payload can reach old clients that cannot be upgraded immediately. Expand support before relying on
new fields, and preserve compatible defaults through the supported client window.

Targeting can contain identifiers and business-sensitive values. Read [security engineering](security-engineering.md)
before distributing a ruleset. Keep `sdk:ruleset` keys on servers; a flag's convenient client API does
not make every credential or targeting rule safe to embed in an application.

## Targeting and rollout are separate controls

Read the [rollout guide][rollouts] before assuming a percentage is an absolute exposure ceiling.
At the reviewed snapshot, an active flag at 0% can still serve users matched by targeting rules.
Changing only the global percentage is therefore not a universal kill switch. Test representative
users inside and outside each targeting condition, as well as the percentage buckets.

Document the ordering and evaluation behavior that the server actually implements. A label such as
“internal-only” has no effect unless targeting expresses and enforces that population. For a flag
with multiple consumers, verify that they send equivalent context and interpret the same enabled state.

## Rehearse operational rollback

The [safety guide][safety] describes supported metrics, thresholds, monitoring enablement and the
global automatic-rollback setting. Error rate needs error reports and an evaluation denominator;
latency needs its actual metric source. Unsupported metric names can be accepted without providing
a useful monitored signal, so validate a configured metric by producing known observations.

Safety checks run periodically. They are not an instantaneous circuit breaker. A rollback target of
zero turns the flag inactive and sets its percentage to zero; a nonzero target only lowers the global
percentage and may leave targeted users enabled. Rollback also pauses the active rollout schedule.
Know which action the operator intends before an incident.

In a disposable environment, rehearse a threshold breach and a manual rollback. Confirm flag status,
schedule state, representative users, audit/rollback records and notifications separately. For
[local SDK evaluation][local], measure how refresh and fallback behavior affect the time until clients
observe the new state. Do not promise global immediate shutdown from an API response alone.

## Stabilize, then remove

After the rollout has met the agreed observation window, decide whether the flag remains an
operational control or becomes cleanup work. If it stays, give it a review cadence and continue testing
its off branch. An unexercised kill switch can fail precisely when needed.

For retirement, search backend, frontend, SDK examples, demos, deployment configuration and maintained
client versions for the key and derived configuration. Replace the branch with the chosen behavior,
stop obsolete evaluation/event calls and verify the resulting application behavior. The code change
must be deployed before the server-side flag disappears. Old mobile clients may require keeping a
compatible flag much longer than web code.

Archive or delete using the supported [API behavior][api] only after the consumer check. Archive,
disable and delete are different operations; test what each supported client does for the resulting
state. Preserve the decision and rollout evidence under the project's retention policy. Do not
reuse the key until the compatibility policy says it is safe, if ever.

## Exercise and worked answer

A flag has a 20% global rollout and a rule enabling all paid accounts. An incident responder sets
rollout to 0%, but paid accounts still receive the feature. What should the runbook explain?

**Answer:** matching targeting can enable users independently of the global percentage. Verify the
implemented inactive/rollback-to-zero behavior for a full stop, and check application defaults and
SDK refresh delays. Rehearse both paid and non-paid users. A nonzero rollback target is not equivalent
to disabling the flag, and a stale client ruleset can delay observation even after server state changes.

**Review artifact:** flag record, branch/failure tests, rollback rehearsal evidence, consumer inventory
and a removal or long-term review date.

[create]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/create.md
[rollouts]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/rollouts.md
[safety]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/safety.md
[local]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk/local-evaluation.md
[api]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/endpoints.md

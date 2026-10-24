# Part IV — Safe Change

### Hours 10–12

[← Part 3](./part-3-security-and-privacy.md) · [Contents](./README.md)

This Part follows a change from release flag to maintenance: define its failure behavior, review
every affected surface, and assemble a decision from evidence. No deployment or CI/CD configuration
is created by these lessons.

> **Verified against** getexperimently/experimently at `07a430df5b54b0e5dd71ddca95071ced05e9e36c`
> (source review, 2026-10-23). Recommended procedures and illustrative examples are identified
> in the lessons; live-stack exercises and paid provider calls were not run for this edition.

---

## Hour 10 — Feature-flag lifecycle: create, operate and retire

**Outcome:** ship a flag with an owner, a tested failure behavior and a removal path. Rollout is one
stage of the lifecycle; a flag that remains forever can become an undocumented dependency.

### What & why

#### Classify the flag before implementing it

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

### How Experimently does it

#### Implement both branches and the failure path

The [creation guide][h10-create] describes targeting and evaluation. Identify what the application does
when evaluation is unavailable, the key is missing, context is malformed or the SDK cannot use its
cached state. Test both enabled and disabled branches with valid payloads and a safe fallback.
The server's result is only one part of the behavior; the application interprets it.

Treat configuration payloads as a contract. Validate values and bound risky settings in the consumer.
For a payload change, account for old mobile releases and rolling server deployments. A new server
payload can reach old clients that cannot be upgraded immediately. Expand support before relying on
new fields, and preserve compatible defaults through the supported client window.

Targeting can contain identifiers and business-sensitive values. Read [security engineering](part-3-security-and-privacy.md#hour-7--security-engineering-through-the-request-path)
before distributing a ruleset. Keep `sdk:ruleset` keys on servers; a flag's convenient client API does
not make every credential or targeting rule safe to embed in an application.

#### Targeting and rollout are separate controls

Read the [rollout guide][h10-rollouts] before assuming a percentage is an absolute exposure ceiling.
At the reviewed snapshot, an active flag at 0% can still serve users matched by targeting rules.
Changing only the global percentage is therefore not a universal kill switch. Test representative
users inside and outside each targeting condition, as well as the percentage buckets.

Document the ordering and evaluation behavior that the server actually implements. A label such as
“internal-only” has no effect unless targeting expresses and enforces that population. For a flag
with multiple consumers, verify that they send equivalent context and interpret the same enabled state.

#### Rehearse operational rollback

The [safety guide][h10-safety] describes supported metrics, thresholds, monitoring enablement and the
global automatic-rollback setting. Error rate needs error reports and an evaluation denominator;
latency needs its actual metric source. Unsupported metric names can be accepted without providing
a useful monitored signal, so validate a configured metric by producing known observations.

Safety checks run periodically. They are not an instantaneous circuit breaker. A rollback target of
zero turns the flag inactive and sets its percentage to zero; a nonzero target only lowers the global
percentage and may leave targeted users enabled. Rollback also pauses the active rollout schedule.
Know which action the operator intends before an incident.

In a disposable environment, rehearse a threshold breach and a manual rollback. Confirm flag status,
schedule state, representative users, audit/rollback records and notifications separately. For
[local SDK evaluation][h10-local], measure how refresh and fallback behavior affect the time until clients
observe the new state. Do not promise global immediate shutdown from an API response alone.

#### Stabilize, then remove

After the rollout has met the agreed observation window, decide whether the flag remains an
operational control or becomes cleanup work. If it stays, give it a review cadence and continue testing
its off branch. An unexercised kill switch can fail precisely when needed.

For retirement, search backend, frontend, SDK examples, demos, deployment configuration and maintained
client versions for the key and derived configuration. Replace the branch with the chosen behavior,
stop obsolete evaluation/event calls and verify the resulting application behavior. The code change
must be deployed before the server-side flag disappears. Old mobile clients may require keeping a
compatible flag much longer than web code.

Archive or delete using the supported [API behavior][h10-api] only after the consumer check. Archive,
disable and delete are different operations; test what each supported client does for the resulting
state. Preserve the decision and rollout evidence under the project's retention policy. Do not
reuse the key until the compatibility policy says it is safe, if ever.

### Try It Yourself

A flag has a 20% global rollout and a rule enabling all paid accounts. An incident responder sets
rollout to 0%, but paid accounts still receive the feature. What should the runbook explain?

**Answer:** matching targeting can enable users independently of the global percentage. Verify the
implemented inactive/rollback-to-zero behavior for a full stop, and check application defaults and
SDK refresh delays. Rehearse both paid and non-paid users. A nonzero rollback target is not equivalent
to disabling the flag, and a stale client ruleset can delay observation even after server state changes.

### Key takeaways

A flag needs an owner, failure behavior and retirement policy. Targeting, global rollout,
server rollback and client refresh are separate controls to rehearse.

**Review artifact:** flag record, branch/failure tests, rollback rehearsal evidence, consumer inventory
and a removal or long-term review date.

---

## Hour 11 — Changing Experimently safely

**Outcome:** take one proposed behavior change across API, storage, dashboard, SDKs, profiles and
documentation without overlooking a dependent surface. This is a worked review method, not a request
to implement the illustrative feature below.

### What & why

#### Begin with an observable change

Suppose users ask to display a human-readable owner label on an experiment. Define the behavior:
new experiments may store an optional label; old experiments still load; the label appears on the
dashboard; clearing it works; it is not an authorization boundary. This label is **hypothetical**
and is not claimed as an existing Experimently field.

Choose the authority and constraints before writing code. Is the label free text or a user reference?
Can it be empty? What limits and escaping apply? Does it contain personal information? Who may
change it? Would changing ownership ever be expected to change access? A cosmetic label and a real
permission-bearing owner are different contracts and need different designs.

### How Experimently does it

#### Map the impact

| Surface | Questions for the owner-label example |
|---|---|
| Persistence | Nullable column, migration, historical rows and old writers |
| API schemas | Create/update/read shapes; omission versus explicit null |
| Service and routes | Existing permission checks; transaction and audit behavior |
| Dashboard | Create/edit/display; empty state; error handling and safe rendering |
| SDKs | Do typed models parse experiment responses or reject unknown fields? |
| Exports | Is the label intentionally included or explicitly outside their contract? |
| Core/full | Is it core behavior with no import of optional modules? |
| Docs/examples | How users set, clear and interpret the label |
| Deployment | Mixed-version operation and rollback behavior |

Check [modules and profiles][h11-modules] and the actual registration hooks before assigning code to a
module. Core must work when the module package is absent. A full-profile-only import hidden behind
a dashboard condition does not make a backend dependency safe.

#### Follow existing paths rather than creating parallel ones

Trace experiment creation and update through schemas, routes and services. Reuse their permissions
and validation conventions. Ensure read responses serialize the label consistently. For partial
updates, distinguish “field omitted, preserve the stored value” from “field is null, clear it.”
An unconditional default can silently erase values written by another client.

Do not trust the dashboard to protect the API. Test that a role without update permission cannot
change the label and that refusal leaves the row unchanged. For display, use the existing rendering
pattern rather than interpreting the label as HTML. Apply payload minimization and logging rules
from [privacy](part-3-security-and-privacy.md#hour-8--data-inventories-identifiers-and-minimization).

Use the [database guide](part-2-reliable-systems.md#hour-6--database-evolution-and-performance) to plan an additive migration. Verify old readers and
writers against the expanded schema and new readers against rows with no label. Treat any required
backfill as a separate operational problem. Review the migration graph and actual deployment path;
a label addition does not justify rewriting migration history.

#### Respect contracts even for an additive field

Read [API stability][h11-stability]. The core and full OpenAPI snapshots pin stable operation shapes and
their referenced components. An additive field can still produce a snapshot difference that requires
deliberate review. The dashboard URL fixture and module-only path fixture have distinct purposes.
Do not accept a regenerated snapshot without inspecting what changed.

Use the repository's generation entry point from the app root in a prepared development environment:

```bash
make openapi
```

This developer command regenerates checked-in fixtures. Review the diff; it is not a runtime request
and is not a proof that the change is backward-compatible. Avoid copying undocumented private
generator invocations into a new workflow.

An API schema test does not prove behavior or numerical correctness. For a statistics change,
check [analysis status][h11-status] and [engine identity][h11-engine] as well. A stable route shape and
production-ready numbers are different promises; estimator changes can require engine-version and
reproducibility updates even when JSON fields remain identical.

#### Validate the behavior at its boundaries

For this example, meaningful cases include create without the label, create with a label, update
with omission, update with explicit null, invalid length, forbidden update, reload from the database
and dashboard display/clear. Test the user-visible result rather than merely mirroring an assignment
statement in a unit test. Prove old rows work and compare core/full responses where applicable.

If SDK models consume the affected response, identify which languages need changes. SDK contract
fixtures, language unit tests and live backend tests have different coverage; see the [SDK contract
guide][sdk]. An endpoint no SDK uses does not need mechanical edits to all sixteen SDKs. Conversely,
a shared assignment or tracking change needs a deliberate compatibility review across consumers.

Use the app's [testing guide][h11-testing] and [Makefile][h11-makefile] for current commands. Run focused
checks during implementation, then the required suites appropriate to the final impact. Run the
core build when the profile boundary is affected. Do not describe a check as passed if you only
read its source or if its environment could not start.

#### Make the result reviewable

A useful PR explains the trigger and resulting behavior, contract/compatibility decisions, migration
plan, validation evidence and remaining limits. Update the relevant product guide alongside the code.
If a screen is added or removed, update [dashboard/API availability][h11-dashboard]. Include instructions
that another engineer can follow from a clean environment, and use the [runnable docs system][h11-examples]
when a product-guide shell example can execute against the local stack.

### Try It Yourself

The label appears correctly in a new experiment. An edit form always submits `owner_label: null`
when the field is not displayed, and a new serializer requires the label on old records. Is the
feature complete?

**Answer:** no. Hidden-form submission can erase a value unintentionally, and old rows can fail to
serialize. Preserve omitted fields, define explicit clearing, tolerate historical nulls and test
both client versions. Review authorization and response snapshots before claiming compatibility.

### Key takeaways

Map a behavior change across storage, schemas, permissions, UI, consumers and profiles. Review
contract changes and distinguish omitted fields from explicit clearing in a partial update.

**Review artifact:** an impact map with “affected,” “unaffected, because…” or “needs verification”
for each surface, plus concrete passing evidence and an honest account of anything unverified.

---

## Hour 12 — A release review from evidence to decision

**Outcome:** assemble a review packet for the hypothetical owner-label change from Hour 11,
including its rollout, missing evidence and maintenance obligations.

### What & why

A change can pass one layer's checks while violating another layer's contract. The API may serialize
a new field correctly while an old writer clears it, a browser exposes a server-only credential,
or a rollback leaves a stale SDK ruleset serving old behavior. A release decision needs evidence
about those boundaries, not just a list of files edited.

The packet below is a recommended review procedure. It is not an automatic release gate and does
not create a new CI/CD requirement for this documentation repository.

### How Experimently does it

Start with the actual schema, permissions, profile hooks, migration behavior and contract snapshots
reviewed in the preceding lessons. For the hypothetical label, record the observable behavior,
including omission, explicit clearing, historical records and old clients. Name the authority for the
value and state that it does not change object access.

Use an evidence table with three statuses: verified, failed and unverified. Link each verified entry
to its result; state the environment and revision. A check you could not start is unverified, even
if its source looks correct. A relevant failure is a reason to fix the change or explain a deliberate
scope decision before rollout; it is not repaired by changing the report to green.

| Boundary | Evidence for the example | Recovery question |
|---|---|---|
| Request and permission | Create/update/clear plus denied mutation | Does refusal leave state unchanged? |
| Database and versions | Old rows and old/new writers | Can the previous application still serve? |
| API and consumers | Reviewed snapshots and affected typed clients | Which clients remain deployed? |
| Flags and cached state | Enabled/disabled/failure-path rehearsal | How long until each client sees rollback? |
| Profiles | Core/full impact and import boundary | Does core run with module code absent? |
| Data flow | Minimized label/log/export scope | Which copies outlive the release? |
| Documentation | Updated behavior, commands and source reference | Can another engineer reproduce the review? |

Separate the operational decision from the statistical decision. A flag may safely expose a change
that has not demonstrated business benefit; conversely, a favorable experiment does not make an
incompatible migration safe. If the feature changes measurement, preserve the unit, event definition,
correction and reproducibility settings in its decision record.

For a reversible rollout, name the owner, initial population, observation window, signal sources,
stop action and verification after stopping. Inspect server state and application behavior, including
local SDK refresh. For database recovery, use the established runbook and record compatibility and
potential data loss instead of inventing a universal downgrade command.

Finally, record maintenance: remove the temporary flag when supported clients no longer need it,
keep long-lived controls exercised, update source pins when the implementation changes and retain
decision evidence under the agreed data policy. Completion includes the handoff, not just deployment.

### Try It Yourself

The owner-label patch passes its new serializer test. There is no test for explicit null, the
previous API version has not been exercised against the expanded schema, and the rollout plan says
only “turn off if problems.” Write the review outcome.

**Worked solution:** the serializer behavior is verified in its stated environment. Explicit clearing
and mixed-version compatibility are unverified, so do not describe the patch as release-ready on
that evidence alone. Add focused create/update/clear, historical-row and mixed-writer checks. Define
the rollout owner, signals, stop action and client-state verification, then record the results. Keep
review and deployment approval distinct; no new CI/CD workflow is required to produce this packet.

### Key takeaways

A review packet ties a claim to evidence and a recovery decision. Unverified work remains visible,
and rollout, statistical benefit and compatibility are separate judgments.

**Review artifact:** a filled evidence table, deployment/rollback handoff and cleanup owner, with
failed or unverified rows stated explicitly.

---

## Workshop

### Q&A

**Why can 0% rollout leave users enabled?** Targeting can enable users independently of the global
percentage. Test the supported inactive/rollback-to-zero behavior and client refresh.

**Why review generated OpenAPI changes?** A generation command updates fixtures; it does not decide
whether a stable contract change is acceptable.

### Quiz

1. Is a cosmetic owner label an authorization model?
2. Does an omitted update field mean the same thing as explicit null?
3. Can a missing check be counted as a passing check?

**Answers:** (1) No. (2) Not necessarily: omission should preserve state when that is the declared
partial-update contract, while null may explicitly clear it. (3) No; record it as unverified.

### Exercise with worked solution

Combine the owner-label change with a gradual rollout. Name the evidence that belongs in its PR.

**Solution:** include the observable before/after behavior, create/update/clear and permission tests,
historical-row and mixed-version checks, reviewed API snapshot changes, core/full impact assessment,
flag failure/rollback rehearsal and a cleanup owner. Record checks not executed and do not add a
pipeline merely to document a procedure.

---

[← Part 3](./part-3-security-and-privacy.md) · [Contents](./README.md)

[h10-create]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/create.md
[h10-rollouts]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/rollouts.md
[h10-safety]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/feature-flags/safety.md
[h10-local]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk/local-evaluation.md
[h10-api]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/endpoints.md
[h11-modules]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/getting-started/modules.md
[h11-stability]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/stability.md
[h11-status]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/analysis_status.py
[h11-engine]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/stats_engine.py
[h11-sdk]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk-guide.md
[h11-testing]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/development/testing-guide.md
[h11-makefile]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/Makefile
[h11-dashboard]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/guides/dashboard-and-api.md
[h11-examples]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/development/doc-examples.md

# Changing Experimently safely

[← Field guides](README.md) · Related: [Database evolution](database-evolution.md)

**Outcome:** take one proposed behavior change across API, storage, dashboard, SDKs, profiles and
documentation without overlooking a dependent surface. This is a worked review method, not a request
to implement the illustrative feature below.

## Begin with an observable change

Suppose users ask to display a human-readable owner label on an experiment. Define the behavior:
new experiments may store an optional label; old experiments still load; the label appears on the
dashboard; clearing it works; it is not an authorization boundary. This label is **hypothetical**
and is not claimed as an existing Experimently field.

Choose the authority and constraints before writing code. Is the label free text or a user reference?
Can it be empty? What limits and escaping apply? Does it contain personal information? Who may
change it? Would changing ownership ever be expected to change access? A cosmetic label and a real
permission-bearing owner are different contracts and need different designs.

## Map the impact

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

Check [modules and profiles][modules] and the actual registration hooks before assigning code to a
module. Core must work when the module package is absent. A full-profile-only import hidden behind
a dashboard condition does not make a backend dependency safe.

## Follow existing paths rather than creating parallel ones

Trace experiment creation and update through schemas, routes and services. Reuse their permissions
and validation conventions. Ensure read responses serialize the label consistently. For partial
updates, distinguish “field omitted, preserve the stored value” from “field is null, clear it.”
An unconditional default can silently erase values written by another client.

Do not trust the dashboard to protect the API. Test that a role without update permission cannot
change the label and that refusal leaves the row unchanged. For display, use the existing rendering
pattern rather than interpreting the label as HTML. Apply payload minimization and logging rules
from [privacy](privacy-data-lifecycle.md).

Use the [database guide](database-evolution.md) to plan an additive migration. Verify old readers and
writers against the expanded schema and new readers against rows with no label. Treat any required
backfill as a separate operational problem. Review the migration graph and actual deployment path;
a label addition does not justify rewriting migration history.

## Respect contracts even for an additive field

Read [API stability][stability]. The core and full OpenAPI snapshots pin stable operation shapes and
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
check [analysis status][status] and [engine identity][engine] as well. A stable route shape and
production-ready numbers are different promises; estimator changes can require engine-version and
reproducibility updates even when JSON fields remain identical.

## Validate the behavior at its boundaries

For this example, meaningful cases include create without the label, create with a label, update
with omission, update with explicit null, invalid length, forbidden update, reload from the database
and dashboard display/clear. Test the user-visible result rather than merely mirroring an assignment
statement in a unit test. Prove old rows work and compare core/full responses where applicable.

If SDK models consume the affected response, identify which languages need changes. SDK contract
fixtures, language unit tests and live backend tests have different coverage; see the [SDK contract
guide][sdk]. An endpoint no SDK uses does not need mechanical edits to all sixteen SDKs. Conversely,
a shared assignment or tracking change needs a deliberate compatibility review across consumers.

Use the app's [testing guide][testing] and [Makefile][makefile] for current commands. Run focused
checks during implementation, then the required suites appropriate to the final impact. Run the
core build when the profile boundary is affected. Do not describe a check as passed if you only
read its source or if its environment could not start.

## Make the result reviewable

A useful PR explains the trigger and resulting behavior, contract/compatibility decisions, migration
plan, validation evidence and remaining limits. Update the relevant product guide alongside the code.
If a screen is added or removed, update [dashboard/API availability][dashboard]. Include instructions
that another engineer can follow from a clean environment, and use the [runnable docs system][examples]
when a product-guide shell example can execute against the local stack.

## Exercise and worked answer

The label appears correctly in a new experiment. An edit form always submits `owner_label: null`
when the field is not displayed, and a new serializer requires the label on old records. Is the
feature complete?

**Answer:** no. Hidden-form submission can erase a value unintentionally, and old rows can fail to
serialize. Preserve omitted fields, define explicit clearing, tolerate historical nulls and test
both client versions. Review authorization and response snapshots before claiming compatibility.

**Review artifact:** an impact map with “affected,” “unaffected, because…” or “needs verification”
for each surface, plus concrete passing evidence and an honest account of anything unverified.

[modules]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/getting-started/modules.md
[stability]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/stability.md
[status]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/analysis_status.py
[engine]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/stats_engine.py
[sdk]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk-guide.md
[testing]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/development/testing-guide.md
[makefile]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/Makefile
[dashboard]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/guides/dashboard-and-api.md
[examples]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/development/doc-examples.md

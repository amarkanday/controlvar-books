# Privacy and the data lifecycle

[← Field guides](README.md) · Related: [Security engineering](security-engineering.md)

**Outcome:** inventory the data an integration creates, minimize unnecessary collection and describe
what retention or deletion work actually covers. This is an engineering guide to data flow, not a
statement of legal compliance or an interpretation of a jurisdiction's requirements.

## Draw the data flow before collecting it

Start with the browser/mobile client or application server, then follow assignment, event collection,
storage, analysis, exports, logs and backups. For LLM features, add provider requests, responses,
evaluation records and judge calls. Each destination can create a separately retained copy.

The [assignment model][assignments] stores the external user ID and targeting context. The [event
model][events] stores identifiers, event names, numeric values, metadata and timestamps. The [LLM
models][llm] add model input/output and evaluation-related data. The fact that a field is JSON does
not make it harmless; open-ended metadata often becomes an accidental store for addresses or text.

Recommended inventory columns are purpose, owner, source, fields, destination, readers, retention,
deletion mechanism and verification evidence. Include derived results and exports, not just base tables.

| Example destination | Data to examine | Engineering question |
|---|---|---|
| Assignment context | Plan, region, targeting attributes | Can the rule work with a coarser category? |
| Event metadata | Page paths, identifiers, free text | Can query strings or customer text be omitted? |
| Ruleset download | Targeting conditions and their values | Which servers receive a complete copy? |
| Logs | Failure details and request metadata | Are values redacted before emission? |
| CSV/JSON exports | Downloaded results or records | Who owns access and expiry after download? |
| LLM provider | Prompt, response and judge input | Does the approved data flow allow this transfer? |
| Backups | Historic database state | What happens if a restore reintroduces removed data? |

## Choose identifiers deliberately

Prefer stable opaque application identifiers over emails or telephone numbers. Keep identity lookup
in the application system that already owns it when possible. A hash of an email is not automatically
anonymous: it can remain linkable or guessable. Scope identifiers to the intended environment or
population while preserving the stable unit the experiment needs.

Do not rotate experiment identifiers casually during a running study. Changing the effective user
ID can change allocation and split a person's history. If an identity reset is required, assess the
measurement impact and record the decision. Minimization and statistical consistency need a shared design.

Use the smallest targeting context that implements the rule. “Paid tier” may be enough instead of
an entire account document. Avoid raw URLs with tokens or query parameters, stack traces containing
request bodies, and user-provided text in event names. Review client code as well as backend schemas.

## Retention requires an executing mechanism

A policy document or a `retention_expires_at` field does not prove data is removed on schedule. For
each class of data, identify the job or service that enforces retention, its failure signal, the
clock it uses and what it excludes. Treat the [security policies][policies] as policy statements to
verify against deployment configuration and code, not as proof that every listed control is deployed.

Distinguish platform users from the external shopper IDs in assignments and events. Deleting a
dashboard login is not a subject-erasure workflow for every external user identifier. Foreign-key
cascades and SET NULL behavior can preserve or detach data; inspect the actual relationships.

The app [README][app] explicitly places right-to-erasure tooling on the roadmap. Do not promise a
complete self-service erasure API at this snapshot. A manually planned workflow must inventory the
actual copies, verify its scope and record limitations. Deleting an experiment or setting a user
inactive is not a substitute for that work.

## Recommended deletion investigation

First establish the identifier mapping and authorized scope through the organization's process.
Inventory assignment rows, events, context, segment membership, LLM records, derived datasets,
exports, caches and retained logs/backups that may contain it. Determine which are in scope for the
request and which have independent retention requirements; document that decision with the owner.

Build the procedure in a disposable dataset, with pre- and post-operation counts and integrity
checks. Keep a protected evidence record that does not reproduce the sensitive payload. Verify
downstream exports and processes separately. Do not publish ad hoc production DELETE statements as
a universal recipe: relationships, retention duties and analysis requirements differ.

Plan for restore. A backup may contain data absent from the live database. Document expiry and
access controls and define how approved removals are reapplied after a recovery before exposing the
restored service. Do not claim immediate physical deletion from all historical copies unless the
storage and recovery procedures actually support it.

## Export and local-evaluation copies

The [export API][exports] produces files that have their own lifecycle after download; a platform
retention job cannot erase a file on someone's laptop. Define access, storage location and expiry
for the export itself. Result summaries and raw event exports are different scopes; inspect the
specific endpoint rather than assuming one contains everything.

The [local-evaluation guide][local] explains that downloaded rules include targeting values. Restrict
distribution, keep scoped keys on servers and account for process-held copies. Revoking a key stops
future requests; it does not undo a prior download.

## Exercise and worked answer

A team hashes customer emails, sends full checkout URLs as metadata, then deletes a dashboard account
and declares all customer data erased. What is missing?

**Answer:** hashed emails may remain identifiable; URLs may contain personal data or tokens; platform
accounts and external experiment subjects are different identities. Inventory linked events,
assignments, context, exports, rulesets, model records and backups. Verify real removal/retention
mechanisms and restore handling. Document the actual scope and remaining copies rather than claiming
a platform-wide erasure operation the implementation does not provide.

**Review artifact:** a field-level data map, minimized payload example, retention enforcement evidence
and a deletion/restore scope statement with named owners.

[assignments]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/assignment.py
[events]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/event.py
[llm]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/llm_experiment.py
[policies]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/security/security-policies.md
[app]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/README.md
[exports]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/data-export.md
[local]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk/local-evaluation.md

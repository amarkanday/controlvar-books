# Part III — Security and Privacy

### Hours 7–9

[← Part 2](./part-2-reliable-systems.md) · [Contents](./README.md) · [Part 4 →](./part-4-safe-change.md)

This Part follows authority and data: which actor can perform an action, which fields an integration
needs, and what a deletion or retention process actually covers. The engineering recommendations
are not claims of certification or automatic tenant isolation.

> **Verified against** getexperimently/experimently at `07a430df5b54b0e5dd71ddca95071ced05e9e36c`
> (source review, 2026-10-23). Recommended procedures and illustrative examples are identified
> in the lessons; live-stack exercises and paid provider calls were not run for this edition.

---

## Hour 7 — Security engineering through the request path

**Outcome:** review a route's actual access boundary and produce tests proving both permitted and
refused behavior. Begin with the existing [onboarding guide](../guides/onboarding.md) and a disposable
instance. This lesson is about implemented controls and engineering practice, not certification.

### What & why

#### Authentication is the beginning of the decision

Trace a request through credential extraction, authentication, current account state, permission
evaluation, resource lookup and mutation. “The route has an authentication dependency” answers only
who presented the request. It does not prove that this actor may perform this action on this object.

Read [request dependencies][h7-deps] and [permissions][h7-permissions] together. Identify whether the route
accepts a user access token or an API key. Management operations use user authentication; SDK routes
have their own API-key path. Do not accept either credential everywhere merely because both resolve
to a user. Credentials have different intended exposure and capabilities.

### How Experimently does it

#### Know the current API-key boundary

The [API-key guide][h7-keys] describes service integration and rotation. At this snapshot, `sdk:ruleset`
is the specifically enforced scope for ruleset download, local evaluation reports and batch assignment.
The [scope dependency][h7-scope] validates the **presented key**, exact scope membership and the key
owner's current ability to update feature flags. Demoting the owner affects subsequent requests.

Names such as `read` and `write` are not a general, enforced permission vocabulary. A stored scope
string does not create least privilege unless a route checks it. A second, privileged key owned by
the same user must not grant privileges to the unprivileged key being presented.

The ruleset contains targeting values for every flag. Keep its scoped key on a server. A browser or
mobile application's embedded credential can be extracted, so treat that client as untrusted.
Use keys without the ruleset scope there only with a deployment design that accepts the SDK routes'
actual public exposure. A server-side integration or application proxy can narrow access; it is a
recommended architecture choice, not an automatic platform permission boundary.

#### Workspaces do not provide tenant isolation

The [workspace overview][h7-workspaces] states that membership groups people; access to experiments and
flags follows platform roles. A workspace label or workspace dropdown does not establish an object-level
authorization predicate. Do not describe an instance shared by separate organizations as isolated tenants
on that basis. If a feature requires isolation, specify the resource ownership model and enforce it at
every relevant read, write, export and background-job path before making that promise.

#### Build an actor/action matrix

For a proposed route, write its intended matrix before implementation. Verify current role behavior
against the permission helpers instead of copying the illustrative policies below into production.

| Scenario | Assertion to make |
|---|---|
| Missing, expired or invalid credential | Request fails before protected data is returned |
| Valid user, insufficient action permission | Request is refused and no mutation occurs |
| API key on a user-management route | Credential type does not bypass management authentication |
| Valid key without `sdk:ruleset` on a scoped route | 403; no ruleset or batch side effect |
| Another key of the same owner has the scope | Presented unscoped key still fails |
| Scoped key whose owner has been demoted | Next scoped request is refused |
| Actor changes a resource identifier | Authorization is evaluated for the requested object |
| Core profile receives a module-only URL | Module absence is handled without importing module code |

Assert database state after a refused mutation. A 403 response after a commit is a broken boundary.
For reads, assert that response bodies and error details do not expose protected fields. For exports,
test the export path explicitly; the dashboard hiding a button proves nothing about the endpoint.

#### Threat-model one concrete flow

Take “server downloads local evaluation rules.” Assets include targeting values and the service key.
Actors include the service, its operator and an attacker with a browser key. Entry points include
the download route, key creation, logs and deployment configuration. Boundaries include browser/server,
API/database and application/log sink.

Walk through key theft, account demotion, key substitution, cached rules persisting after revocation
and secrets accidentally logged in validation errors. Key revocation prevents future authenticated
downloads; it cannot recall rules already copied to another process. Record the expected refresh and
shutdown behavior rather than promising immediate erasure from every client.

### Try It Yourself

A developer adds `scopes=["read"]` to a browser key and calls it read-only. The same user's second
key has `sdk:ruleset`. What should the reviewer challenge?

**Answer:** `read` is not general route enforcement. Inspect every exposed endpoint rather than
inferring restrictions from the label. The browser key must fail scoped routes independently of the
second key. Prove refusal with integration tests, check owner demotion, and avoid shipping any scoped
ruleset key to the browser. If the product needs a truly restricted public key, that requires a new
enforced contract and tests, not documentation alone.

### Key takeaways

Authentication identifies the caller; route checks enforce actions and resource boundaries.
Inspect the presented credential and its current authority rather than trusting labels or UI controls.

**Review artifact:** an actor/action matrix, one threat flow, and tests that show denied requests leave
protected data and database state untouched. Use the existing [permission tests][h7-tests] as a starting point.

---

## Hour 8 — Data inventories, identifiers and minimization

**Outcome:** trace the copies an integration creates and reduce payloads without changing the
experiment's unit of assignment. This is an engineering lesson, not a statement of legal compliance.

### What & why

#### Draw the data flow before collecting it

Start with the browser/mobile client or application server, then follow assignment, event collection,
storage, analysis, exports, logs and backups. For LLM features, add provider requests, responses,
evaluation records and judge calls. Each destination can create a separately retained copy.

The [assignment model][h8-assignments] stores the external user ID and targeting context. The [event
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

### How Experimently does it

#### Choose identifiers deliberately

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

### Try It Yourself

The integration sends a customer email, full account JSON and the checkout URL for a rule that
only tests whether the shopper has a paid plan. Design a smaller payload without changing the unit
of assignment.

**Answer:** use the stable opaque shopper ID and the smallest paid-plan attribute the rule needs.
Remove the email, unrelated account fields and URL query data. Verify that the reduced context still
produces the intended targeting result. Keep the lookup mapping in its authorized source system.

### Key takeaways

Minimization starts with the rule and observation unit. Opaque identifiers can reduce exposed
content while remaining linkable; they are not an automatic anonymity guarantee.

**Review artifact:** a purpose/field/destination inventory and before/after synthetic payloads,
with an owner for each retained field.

---

## Hour 9 — Retention, deletion and recovery

**Outcome:** verify which mechanism enforces a retention policy and describe the actual scope of
deletion, exports and restore handling. This lesson does not interpret jurisdictional requirements.

### What & why

#### Retention requires an executing mechanism

A policy document or a `retention_expires_at` field does not prove data is removed on schedule. For
each class of data, identify the job or service that enforces retention, its failure signal, the
clock it uses and what it excludes. Treat the [security policies][h9-policies] as policy statements to
verify against deployment configuration and code, not as proof that every listed control is deployed.

Distinguish platform users from the external shopper IDs in assignments and events. Deleting a
dashboard login is not a subject-erasure workflow for every external user identifier. Foreign-key
cascades and SET NULL behavior can preserve or detach data; inspect the actual relationships.

The app [README][h9-app] explicitly places right-to-erasure tooling on the roadmap. Do not promise a
complete self-service erasure API at this snapshot. A manually planned workflow must inventory the
actual copies, verify its scope and record limitations. Deleting an experiment or setting a user
inactive is not a substitute for that work.

### How Experimently does it

#### Recommended deletion investigation

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

#### Export and local-evaluation copies

The [export API][h9-exports] produces files that have their own lifecycle after download; a platform
retention job cannot erase a file on someone's laptop. Define access, storage location and expiry
for the export itself. Result summaries and raw event exports are different scopes; inspect the
specific endpoint rather than assuming one contains everything.

The [local-evaluation guide][h9-local] explains that downloaded rules include targeting values. Restrict
distribution, keep scoped keys on servers and account for process-held copies. Revoking a key stops
future requests; it does not undo a prior download.

### Try It Yourself

A team hashes customer emails, sends full checkout URLs as metadata, then deletes a dashboard account
and declares all customer data erased. What is missing?

**Answer:** hashed emails may remain identifiable; URLs may contain personal data or tokens; platform
accounts and external experiment subjects are different identities. Inventory linked events,
assignments, context, exports, rulesets, model records and backups. Verify real removal/retention
mechanisms and restore handling. Document the actual scope and remaining copies rather than claiming
a platform-wide erasure operation the implementation does not provide.

### Key takeaways

Retention needs an executing mechanism, and deletion needs a verified scope. Downloads, caches
and restored backups can outlive a live-database change and require separate ownership.

**Review artifact:** a field-level data map, minimized payload example, retention enforcement evidence
and a deletion/restore scope statement with named owners.

---

## Workshop

### Q&A

**Why is a workspace not automatically a tenant boundary?** The reviewed implementation groups
users but applies platform roles to experiment and flag access. Labels and membership alone do not
add a resource-isolation predicate.

**Why is an expiry column not sufficient retention evidence?** There must be an executing mechanism
with verified scope, scheduling, failure signals and recovery behavior.

### Quiz

1. Does a stored `read` scope make every API route read-only?
2. Is a hashed email necessarily anonymous?
3. Does deleting a platform login remove every external shopper's events?

**Answers:** no to all three. Inspect enforced route checks, identity linkability, and the separate
platform-user/external-subject data relationships.

### Exercise with worked solution

An operator reports that an identifier is absent from the live database after cleanup. Describe
what remains to verify before claiming completion across the data flow.

**Solution:** verify the authorized scope, linked assignment/context/event/LLM data, segments,
derived stores, downloaded exports, cached rules and applicable logs/backups. Record retained copies
and owners, and define how restore avoids reintroducing data into the exposed service.

---

[← Part 2](./part-2-reliable-systems.md) · [Contents](./README.md) · [Part 4 →](./part-4-safe-change.md)

[h7-deps]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/deps.py
[h7-permissions]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/permissions.py
[h7-scope]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/sdk_scope.py
[h7-keys]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/security/api-keys.md
[h7-workspaces]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/workspaces/overview.md
[h7-tests]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/tests/unit/api/test_permission_deps.py
[h8-assignments]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/assignment.py
[h8-events]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/event.py
[h8-llm]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/llm_experiment.py
[h8-policies]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/security/security-policies.md
[h8-app]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/README.md
[h8-exports]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/data-export.md
[h8-local]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk/local-evaluation.md
[h9-assignments]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/assignment.py
[h9-events]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/event.py
[h9-llm]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/models/llm_experiment.py
[h9-policies]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/security/security-policies.md
[h9-app]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/README.md
[h9-exports]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/api/data-export.md
[h9-local]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/sdk/local-evaluation.md

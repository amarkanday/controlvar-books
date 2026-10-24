# Security engineering through the request path

[← Field guides](README.md) · Related: [Privacy and data lifecycle](privacy-data-lifecycle.md)

**Outcome:** review a route's actual access boundary and produce tests proving both permitted and
refused behavior. Begin with the existing [onboarding guide](../guides/onboarding.md) and a disposable
instance. This lesson is about implemented controls and engineering practice, not certification.

## Authentication is the beginning of the decision

Trace a request through credential extraction, authentication, current account state, permission
evaluation, resource lookup and mutation. “The route has an authentication dependency” answers only
who presented the request. It does not prove that this actor may perform this action on this object.

Read [request dependencies][deps] and [permissions][permissions] together. Identify whether the route
accepts a user access token or an API key. Management operations use user authentication; SDK routes
have their own API-key path. Do not accept either credential everywhere merely because both resolve
to a user. Credentials have different intended exposure and capabilities.

## Know the current API-key boundary

The [API-key guide][keys] describes service integration and rotation. At this snapshot, `sdk:ruleset`
is the specifically enforced scope for ruleset download, local evaluation reports and batch assignment.
The [scope dependency][scope] validates the **presented key**, exact scope membership and the key
owner's current ability to update feature flags. Demoting the owner affects subsequent requests.

Names such as `read` and `write` are not a general, enforced permission vocabulary. A stored scope
string does not create least privilege unless a route checks it. A second, privileged key owned by
the same user must not grant privileges to the unprivileged key being presented.

The ruleset contains targeting values for every flag. Keep its scoped key on a server. A browser or
mobile application's embedded credential can be extracted, so treat that client as untrusted.
Use keys without the ruleset scope there only with a deployment design that accepts the SDK routes'
actual public exposure. A server-side integration or application proxy can narrow access; it is a
recommended architecture choice, not an automatic platform permission boundary.

## Workspaces do not provide tenant isolation

The [workspace overview][workspaces] states that membership groups people; access to experiments and
flags follows platform roles. A workspace label or workspace dropdown does not establish an object-level
authorization predicate. Do not describe an instance shared by separate organizations as isolated tenants
on that basis. If a feature requires isolation, specify the resource ownership model and enforce it at
every relevant read, write, export and background-job path before making that promise.

## Build an actor/action matrix

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

## Threat-model one concrete flow

Take “server downloads local evaluation rules.” Assets include targeting values and the service key.
Actors include the service, its operator and an attacker with a browser key. Entry points include
the download route, key creation, logs and deployment configuration. Boundaries include browser/server,
API/database and application/log sink.

Walk through key theft, account demotion, key substitution, cached rules persisting after revocation
and secrets accidentally logged in validation errors. Key revocation prevents future authenticated
downloads; it cannot recall rules already copied to another process. Record the expected refresh and
shutdown behavior rather than promising immediate erasure from every client.

## Exercise and worked answer

A developer adds `scopes=["read"]` to a browser key and calls it read-only. The same user's second
key has `sdk:ruleset`. What should the reviewer challenge?

**Answer:** `read` is not general route enforcement. Inspect every exposed endpoint rather than
inferring restrictions from the label. The browser key must fail scoped routes independently of the
second key. Prove refusal with integration tests, check owner demotion, and avoid shipping any scoped
ruleset key to the browser. If the product needs a truly restricted public key, that requires a new
enforced contract and tests, not documentation alone.

**Review artifact:** an actor/action matrix, one threat flow, and tests that show denied requests leave
protected data and database state untouched. Use the existing [permission tests][tests] as a starting point.

[deps]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/deps.py
[permissions]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/core/permissions.py
[scope]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/app/api/sdk_scope.py
[keys]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/security/api-keys.md
[workspaces]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/docs/workspaces/overview.md
[tests]: https://github.com/getexperimently/experimently/blob/07a430df5b54b0e5dd71ddca95071ced05e9e36c/backend/tests/unit/api/test_permission_deps.py

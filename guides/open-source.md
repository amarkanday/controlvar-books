# Running the open-source project

### _Settings, contributions, Dependabot, security reports and registries, for whoever maintains it_

[← experimently-docs](../README.md) · [Deploying Experimently](./deploying.md) · [Cutting a release](./releasing.md)

Verified against getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased changes, including three
breaking ones: #747, #769, #770). Repository settings change; every setting below is a dated reading with the
read-only command that reproduces it. Run the command before relying on the reading.

---

## 1. What the project is

- **One public repository,** `getexperimently/experimently`. Everything is Apache-2.0 except `sdk/`, which is
  MIT. `REUSE.toml` records the licence of every file; `reuse lint` runs in `make lint` and requires
  `LICENSES/Apache-2.0.txt` to match `LICENSE` byte for byte.
- **Contributions are certified with the DCO, not a CLA.** A contribution is made under the licence of the
  directory it changes (CONTRIBUTING.md).
- **Docs site:** `https://getexperimently.github.io/experimently/`, built from the app's `docs/` with MkDocs and
  deployed from a release tag ([Cutting a release §3.8](./releasing.md#38-deploy-the-docs-site-by-hand)).

## 2. Repository settings (read on 2026-10-03)

```bash
gh api repos/getexperimently/experimently \
  --jq '{allow_squash_merge,allow_merge_commit,allow_rebase_merge,allow_auto_merge,delete_branch_on_merge,squash_merge_commit_title,squash_merge_commit_message,has_discussions}'
```

| Setting | Reading |
|---|---|
| Merge methods | squash only |
| Squash commit | subject = PR title, body = PR body |
| Auto-merge | on |
| Delete branch on merge | on |
| Discussions | off |

The organisation is on GitHub's Team plan (`gh api orgs/getexperimently --jq .plan.name` prints `team`), which
sets how many jobs run in parallel. Secret scanning, push protection and private vulnerability reporting are on;
`scripts/configure-repo.sh` is what asserts them (§9).

**Environments:**

| Environment | Protection | Deployment policy |
|---|---|---|
| `staging` | a required reviewer | branch `main` |
| `sdk-release` | a required reviewer | tag `sdk/*/v*` |
| `github-pages` | none | branch `main` and tag `v*` |

There was no `prod` environment. Read them with
`gh api repos/getexperimently/experimently/environments/<env>` and
`gh api repos/getexperimently/experimently/environments/<env>/deployment-branch-policies`.

## 3. Branch protection: a dated reading

Read on 2026-10-03 with:

```bash
gh api repos/getexperimently/experimently/branches/main/protection \
  --jq '{reviews: .required_pull_request_reviews, enforce_admins: .enforce_admins.enabled,
         strict: .required_status_checks.strict, checks: (.required_status_checks.contexts | length),
         linear: .required_linear_history.enabled, force_pushes: .allow_force_pushes.enabled}'
```

`main` required **20 status checks**, with `strict` off (a branch need not be up to date with `main` before it
merges). It required linear history, refused force pushes, required **no pull-request review**
(`required_pull_request_reviews: null`), and had **`enforce_admins` off**.

Why it is set this way: the project has one maintainer, so a required review would be a review nobody else can
give. And the release pull request has no checks at all (GitHub runs no workflow for its own token's events), so
it can only be merged with `gh pr merge --admin`, which works because `enforce_admins` is off
([Cutting a release §3.6](./releasing.md#36-merge-it)).

- [ ] **When a second maintainer gets write access, require one approving review on `main`.**

The 20 required checks were: `Release Gate Summary`, `Security Scan Summary`, `Unit Tests`, `Smoke Tests`,
`Frontend Tests`, `SDK Contract Tests`, `integration-tests`, `SDK Unit Tests`, `Browser E2E`, `Docker Smoke`,
`lint`, `regression-guard`, `SDK Live Contract / sdk-live-contract (core)`, `core-build`, `full-build`,
`Module Tests`, `Base Requirements Only`, `CDK Stack Tests (Python)`, `DCO`, `Leak Guard`.

**Two things that are not what they look like:**

- **`conventional-title` is not required.** It reports on every pull request but does not block, and a prose
  title then drops out of the changelog. Read the title before you merge.
- **`.github/CODEOWNERS` requests reviews; it does not require them.** With no review rule, an owner's review is
  asked for and not enforced.

## 4. Reviewing and merging a contribution

The checks contributors most often trip on:

- **DCO.** Every commit needs a `Signed-off-by:` line matching its author or committer. The fix is
  `git rebase --signoff main` and a push. Commits from Dependabot and github-actions are exempt. DCO is one of the
  20 required checks; where CONTRIBUTING.md still calls it advisory, it is out of date.
- **Leak Guard** (`scripts/leak_guard.py`). It scans every tracked file, each pushed commit **including its
  message**, and the pull request's **title and body**. It matches home-directory paths and real-looking AWS
  account IDs by shape (the documented placeholder `123456789012` is allowed), and refuses some unverifiable
  claims. Contributors can run it before pushing: `pre-commit install` and
  `pre-commit install --hook-type pre-push` wire it into `.pre-commit-config.yaml`'s hooks. An absolute home path
  has reached `main` through a pull request body before; the body is scanned for that reason.
- **regression-guard.** A pull request labelled `bug` must add or change a test file. The escape hatch is the
  `skip-regression-guard` label, with the reason in the description. The same job requires the `docs-demotion`
  label when a doc example stops being executed.
- **lint.** `make lint` runs ruff, `lint-imports`, `reuse lint`, the requirements-lock check, eslint and tsc, and
  hadolint and actionlint when installed.
- **core-build / full-build.** `make core-build` copies the tree into a directory with **no `.git`** and deletes
  `modules/`. A test that shells out to git passes in a checkout and fails there.

**How to merge:** once the title is a conventional commit and the checks are green,

```bash
gh pr merge <n> -R getexperimently/experimently --auto --squash
```

`--auto` bypasses nothing: the merge happens only when all 20 checks pass. With `strict` off, a run of merges
lands one after another without each being rebased, so watch `main`'s own run after a batch.

**Database migrations** in a pull request: a full checkout has two alembic heads, the core chain and the
`modules` branch, kept apart on purpose. Never run `alembic merge` on them: the merge file lands in the core
versions directory and breaks every core checkout. Use `upgrade heads`, and a new revision names the head it
extends (`--head <core head id>` or `--head modules@head`). Every migration is expand-then-contract across
releases, because a deploy migrates before traffic moves and a rollback runs the older code on the newer schema.
`backend/tests/integration/database/test_autogenerate_is_empty.py` is the standing check that the models and the
migrations agree.

## 5. Dependabot

- **What merges itself** (`.github/workflows/dependabot-automerge.yml`): patch and minor bumps in any ecosystem,
  and github-actions bumps including majors, through `gh pr merge --auto --squash` once all 20 checks pass. Other
  majors are left for a person, and `.github/dependabot.yml` ignores them anyway.
- **Bumps that need a generated file regenerated** have companion workflows: `dependabot-lock.yml` regenerates
  `backend/requirements/runtime.lock`, and `dependabot-licences.yml` regenerates `THIRD_PARTY_LICENSES.md`.
- `.github/dependabot.yml` declares 18 ecosystems (`grep -c package-ecosystem .github/dependabot.yml`).
- **If the queue grows, ask what drains it.** Lowering rates and limits has been tried three times and the queue
  came back within a day each time; the cause was that nothing merged the bumps.

## 6. Security reports and advisories

**Intake:** GitHub private vulnerability reporting (Security → Report a vulnerability, or
`https://github.com/getexperimently/experimently/security/advisories/new`), or `security@getexperimently.com`.
Blank issues are off, and the issue chooser sends security reports to the private route.

**The targets `SECURITY.md` promises:**

| Stage | Target |
|---|---|
| Acknowledge | 3 business days |
| Confirm or dispute, with a CVSS v3.1 severity | 10 business days |
| Fix released, Critical or High | 14 days from confirmation |
| Fix released, Medium or Low | the next scheduled release |
| Public disclosure | 90 days from the report, or the day the fix ships, whichever is first |

**The flow:**

1. Draft the GitHub security advisory privately.
2. Fix it in a **public** pull request with a neutral title, tests named for the behaviour, and a neutral
   description. (A temporary private fork runs no CI and enforces no branch protection, which is why the fix is
   not made there.)
3. Cut the release ([Cutting a release](./releasing.md)). Keep the release notes neutral too.
4. Pull the patched images, both profiles, `--platform linux/amd64`, and check the fix in them. Use
   `--entrypoint python` or `docker exec`: the image's entrypoint migrates a database on any `docker run` unless
   `RUN_MIGRATIONS=false`.
5. Publish the advisory.
6. Request the CVE, and check later that the advisory shows its CVE id.

**Nothing public names a vulnerability until its fix has shipped.**

**Supported versions:** until 1.0.0, `SECURITY.md` supports only the tip of `main`.

## 7. Releases

[Cutting a release](./releasing.md) is the procedure: conventional titles, the release pull request and its eight
files, the `--admin` merge, why `release.yml` is never dispatched after a release, dispatching `docs.yml` with
`--ref vX.Y.Z`, verifying signatures, and SDK releases.

## 8. Registries and public packages

**Names, read on 2026-10-03:**

| Registry | Name | State |
|---|---|---|
| GHCR | `experimently`, `experimently-web` | public; anonymous pull answered 200 |
| PyPI | `experimently`, `experimently-openfeature` | a `0.0.0` placeholder each |
| npm | the `@getexperimently` scope | no package published yet (`@getexperimently/js-sdk` answered 404) |
| NuGet | `Experimently.SDK` | a `0.0.0` placeholder, unlisted |
| Maven Central | namespace `com.getexperimently` | verified by a DNS TXT record on the apex domain |
| Packagist, RubyGems, Hex, pub.dev | | not claimed |

**The apex domain's TXT records hold the Maven verification token beside the mail SPF record.** When editing
them, keep the SPF value: removing it breaks the domain's mail.

**Package visibility on GitHub is whole-package and one-way.** A public package cannot be made private again, and
it exposes every version it has ever held. Before any package goes public, scan every version
([Cutting a release §7](./releasing.md#7-before-a-package-goes-public-scan-every-version)). Each release now scans
each image before pushing it.

**Trusted publishing.** `sdk-release.yml` publishes to PyPI and npm with OIDC; no registry token is stored. Each
registry is configured once with the owner `getexperimently`, the repository `experimently`, the workflow
`sdk-release.yml` and the environment `sdk-release`.

**The Helm chart.** Today the chart is a `.tgz` release asset only; the project has decided it will ALSO be an
OCI package at `oci://ghcr.io/getexperimently/charts/experimently`; the first push and making it public are
founder-only and not done.

## 9. Settings drift

- **`scripts/check_required_checks.sh`** compares `main`'s live protection with what `scripts/configure-repo.sh`
  would write: the required-check names, `strict`, the review rule, the app each check is pinned to, and the
  other protection flags. It only reads. Run it with your own `gh` login: the Actions token cannot read branch
  protection. Exit 0 is no drift, 1 is drift, 2 is "could not compare".
- **`scripts/configure-repo.sh [--dry-run]`** re-asserts the required-check names, secret scanning, push
  protection and private vulnerability reporting. It refuses to write anything when live protection differs in
  anything other than the check names, because those other settings are the founder's decision and a write would
  silently replace them.

## 10. The docs site and the landing page

- **The docs site** builds on every push to `main` that touches the docs (MkDocs, strict), and deploys only from a
  release tag. Dispatch it after each release. When a deploy fails within seconds rather than in the build,
  suspect the `github-pages` environment's deployment policy before the site. Diagnose from the deployment's
  statuses (`gh api repos/getexperimently/experimently/deployments/<id>/statuses`) rather than the job log, which
  can be gone.
- **The landing page,** getexperimently.com, is built by AWS Amplify from `amplify.yml` (`npm run build:marketing`
  in `frontend/`) on every push to `main`. **Merging a change to it is publishing it.** Write "built on AWS", not
  trademark phrasing, and no count that nothing checks.

## 11. How changes are decided

The rules that generalise, from the app repository's `CLAUDE.md`:

- **Plan before code** for anything beyond a one-file change: the premises checked by running something, the
  design, what is irreversible, and how each property will be verified. Small documentation fixes and one-line
  fixes with their regression test take a shorter path.
- **Every gate is tamper-tested:** break the thing it guards and watch it fail before trusting it.
- **Every bug fix carries a regression test** that fails on the old code.
- **The merge bar:** merge when a review round finds nothing that would break a deployment or lose data; anything
  else becomes a tracked issue.

## 12. Founder-only and irreversible steps (running the project)

| Step | Who | Irreversible? | Read-only check afterwards |
|---|---|---|---|
| Allow Actions to create pull requests | organisation owner | no | release-please opens or updates its pull request |
| Change branch protection (the review rule, `strict`, `enforce_admins`) | founder | no | §3's `gh api` reading, then `scripts/check_required_checks.sh` |
| Require one review once a second maintainer has write access | founder | no | §3's reading shows `required_pull_request_reviews` set |
| Make a package public | organisation owner | **yes**, every version | the anonymous token check in [Cutting a release §5](./releasing.md#5-verify-what-shipped) |
| First Helm OCI push and making it public | founder | yes | an anonymous `tags/list` on the chart's GHCR path answers 200 |
| Claim a registry name; publish to a registry | founder | yes | the registry's page or JSON |
| Delete package versions, tags or releases | organisation owner | yes | the version, tag or release is gone from its listing |
| Install a GitHub App | organisation owner | no | `gh api orgs/getexperimently/installations --jq '.installations[].app_slug'` |
| Edit DNS in the apex zone (keep the SPF value) | founder | no, but mail breaks while it is wrong | `dig TXT <domain> +short` still shows the `v=spf1` record |
| The `github-pages` environment's `v*` tag policy | repository administrator | no | `gh api repos/getexperimently/experimently/environments/github-pages/deployment-branch-policies` |
| Publish a security advisory | maintainer, only after the patched release's images are checked | yes | the advisory is public and the fix's release is tagged |
| Announcements | founder | yes | |

### Not verified here

- The organisation-level Actions permission, npm scope membership, and the PyPI and npm trusted-publisher
  settings: each needs an owner's login to read.
- That the Maven TXT token is Central's.
- The Amplify app's repository connection: it needs the founder's AWS account.

## Related reading — Production Engineering

The Production Engineering specialization uses a newer, explicitly pinned app snapshot; this
book or guide retains its original reference commit. See the book README for scope and verification.

- [Security engineering](../production-engineering/part-3-security-and-privacy.md#hour-7--security-engineering-through-the-request-path)
- [Privacy and data lifecycle](../production-engineering/part-3-security-and-privacy.md#hour-8--data-inventories-identifiers-and-minimization)

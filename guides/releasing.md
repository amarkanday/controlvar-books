# Cutting a release

### _From conventional titles to signed images, the docs site and the SDKs_

[← experimently-docs](../README.md) · [Deploying Experimently](./deploying.md) · [Running the open-source project](./open-source.md)

Verified against getexperimently/experimently at `a4a6dbd2` (0.17.0 plus unreleased changes, including three
breaking ones: #747, #769, #770). Live GitHub and registry readings are dated where they appear.

The app repository's `CLAUDE.md`, section "Releasing, and the docs site", carries the same rules for
contributors. This guide matches that section as corrected by getexperimently/experimently#793. If the two ever
disagree, the app repository is fixed first and this guide follows it. The app's own reference is
`docs/self-hosting/releases.md`.

---

## 1. How a release happens, in one paragraph

Every pull request is squash-merged to `main` with its title as the commit subject. `release-please` reads
those subjects and keeps one standing pull request, `chore(main): release X.Y.Z`, that bumps the version and
writes the changelog. Merging that pull request makes `release-please.yml` tag the merge commit `vX.Y.Z`, open a
GitHub release, and **call `release.yml` itself through `workflow_call`**, which builds, scans and signs the
images, publishes them, and attaches the Helm chart to the release (the chart is not signed; only the images
are). Nobody runs `release.yml` by hand on the normal path. The docs site is
the one thing the release does not deploy: you dispatch it.

```
PR titles (conventional) ──squash──▶ main ──▶ release-please.yml
                                                  │ keeps "chore(main): release X.Y.Z" open
                         merge it with --admin ──▶│ tags vX.Y.Z, opens the GitHub release
                                                  └─ workflow_call ─▶ release.yml (images, SBOMs, chart, :core/:full)
                         then, by hand ──────────▶ gh workflow run docs.yml --ref vX.Y.Z
```

---

## 2. Before your first release (one-time)

| Setting | State on 2026-10-03 | Check |
|---|---|---|
| The organisation lets GitHub Actions create pull requests | needed by release-please; an org owner sets it under the organisation's Settings → Actions → Workflow permissions | release-please fails with "GitHub Actions is not permitted to create or approve pull requests" when it is off |
| Merges | squash only; the squash subject is the PR title, the body the PR body | `gh api repos/getexperimently/experimently --jq '{allow_squash_merge,allow_merge_commit,allow_rebase_merge,squash_merge_commit_title,squash_merge_commit_message}'` |
| `enforce_admins` on `main` | off, which is what lets `--admin` merge the release PR ([open-source guide §3](./open-source.md#3-branch-protection-a-dated-reading)) | `gh api repos/getexperimently/experimently/branches/main/protection --jq .enforce_admins.enabled` |
| GitHub Pages | `build_type: workflow` | `gh api repos/getexperimently/experimently/pages --jq .build_type` |
| The `github-pages` environment | deployment policy: branch `main` **and** tag `v*` | `gh api repos/getexperimently/experimently/environments/github-pages/deployment-branch-policies --jq '.branch_policies[] \| "\(.type):\(.name)"'` |

**Tools:** `gh` with admin rights on the repository (for `--admin`), and for verifying: `cosign`, `curl`, and
Docker.

---

## 3. A normal release, step by step

### 3.1 Every pull request title is a conventional commit

- **Form:** `type(scope)!: description`. **Types:** `feat fix perf deps docs build ci refactor test chore revert
  style`.
- **The title is the release note.** `main` is squash-merged and the squash subject is the PR title, which is
  what release-please parses. A title that is not a conventional commit is silently left out of the changelog
  and does not move the version.
- **`conventional-title` is not a required check.** It reports on every pull request but does not block, so a
  prose title can still merge. Check the title before you merge.
- `gh pr merge --subject "<subject>"` overrides the title, and has to satisfy the same rule.

### 3.2 Breaking changes

- Mark them with `!` after the type **and** a `BREAKING CHANGE:` footer in the PR body (the body becomes the
  squash body).
- **Before 1.0 a breaking change bumps the minor version, not the major** (`"bump-minor-pre-major": true` in
  `release-please-config.json`). That is why 0.17.0 plus three breaking changes becomes 0.18.0.
- Write the `BREAKING CHANGE:` footer as one paragraph. Release-please has been seen to keep only its first
  paragraph; anything longer, finish by hand in the release notes.

### 3.3 When a merged commit was unparsable

release-please skips a commit whose subject it cannot parse; this has happened several times in the project's
history. To repair it after the merge, edit the **merged pull request's body** to add a block holding the message
release-please should use instead:

```
BEGIN_COMMIT_OVERRIDE
fix(api): the corrected subject

BREAKING CHANGE: if there is one
END_COMMIT_OVERRIDE
```

Then re-run release-please so the release pull request is regenerated: `gh workflow run release-please.yml`.

### 3.4 What the changelog shows

From `release-please-config.json`: `feat`, `fix`, `perf`, `deps`, `docs` and `build` get sections; `ci`,
`refactor`, `test` and `chore` are hidden.

### 3.5 Review the release pull request

The standing pull request is titled `chore(main): release X.Y.Z` (on 2026-10-03, #749 for 0.18.0).

- **It has zero checks.** GitHub runs no workflow for an event its own `GITHUB_TOKEN` created, so with 20 checks
  required it can never satisfy branch protection. Nothing in the pull request is wrong; this is expected.
- **Diff it, and expect exactly these 8 files:**

  | File | What changes |
  |---|---|
  | `CHANGELOG.md` | the new section |
  | `VERSION` | the version |
  | `.release-please-manifest.json` | the version |
  | `docs/api/openapi-v1.full.json` | `info.version` only |
  | `docs/api/openapi-v1.stable.json` | `info.version` only |
  | `frontend/src/tests/fixtures/openapi.json` | `info.version` only |
  | `charts/experimently/Chart.yaml` | `version` and `appVersion` |
  | `deploy/compose/compose.yml` | the two image lines |

  The last five are the `extra-files` in `release-please-config.json`. Check it:

  ```bash
  gh pr view <n> -R getexperimently/experimently --json files --jq '.files[].path'   # exactly the 8 above
  gh pr diff <n> -R getexperimently/experimently                                     # only version strings change in the last five
  ```

- **If anything but the version string changed in one of the three OpenAPI files,** the generator and the
  committed snapshots have diverged. The fix is `make openapi` in an ordinary pull request, not an edit to the
  release pull request.

### 3.6 Merge it

```bash
gh pr merge <n> -R getexperimently/experimently --squash --admin
```

`--admin` is required because the pull request has no checks, and it works because `enforce_admins` is off on
`main`. `strict` is also off, so there is no need to update the branch first.

### 3.7 The release builds itself

The merge's push to `main` runs `release-please.yml`. It tags `vX.Y.Z`, creates the GitHub release, and calls
`release.yml` with the tag. Watch that one run:

```bash
gh run list -R getexperimently/experimently --workflow=release-please.yml --limit 1
gh run view <id> -R getexperimently/experimently
```

Expect, under "build and publish the release": `tag matches the tree`, `API image (core)`, `API image (full)`,
`dashboard image (core)`, `dashboard image (full)`, `GitHub release`, `Helm chart`, and last `move :core and
:full`.

> **Do not also dispatch `release.yml`.** `gh workflow run release.yml -f tag=vX.Y.Z` after a release builds the
> whole release a second time and re-points `core-X.Y.Z`, `full-X.Y.Z`, `:core` and `:full` to new digests. That
> happened to 0.17.0: two signed builds, and the first one's images were left as untagged orphans. The dispatch
> exists **only to recover a run that failed**, and only after the tag exists:
>
> ```bash
> git ls-remote --tags origin vX.Y.Z          # must print the tag before you dispatch anything
> gh workflow run release.yml -R getexperimently/experimently -f tag=vX.Y.Z
> ```

### 3.8 Deploy the docs site, by hand

The tag release-please pushes triggers no workflow, and `release.yml` does not call `docs.yml`. Dispatch it on
the tag ref:

```bash
gh workflow run docs.yml -R getexperimently/experimently --ref vX.Y.Z
```

`docs.yml` deploys only when the ref is a `v*` tag **with no `-` in it** (a pre-release builds but never replaces
the live site), and only in `getexperimently/experimently`. If the deploy fails within seconds rather than in the
build, suspect the `github-pages` environment's tag policy before the site.

### 3.9 Edit the release notes if needed

When a release closes a security advisory that is still embargoed, keep the notes neutral: describe the
behaviour that changed, not the hole ([open-source guide §6](./open-source.md#6-security-reports-and-advisories)).

---

## 4. What `release.yml` does

**Three ways in** (`.github/workflows/release.yml`):

| Entry | When |
|---|---|
| `workflow_call` from `release-please.yml` | every normal release |
| `push` of a `v*` tag | a tag pushed by a human, e.g. a release-candidate rehearsal; a human's token does start workflows |
| `workflow_dispatch` with `tag` | recovery of a failed run only, after the tag exists |

**The jobs, in order:**

1. **`verify`** ("tag matches the tree"): `scripts/check_version_sources.py --expect <tag>` compares the tag with
   `VERSION`, the manifest, the build metadata, `settings.VERSION`, `Chart.yaml` and the compose image lines.
   Nothing is built until they agree.
2. **Four image builds**, `experimently` and `experimently-web`, each in `core` and `full`, `linux/amd64` only.
   Each image is scanned for stored credentials **before it is pushed**, in two passes: the whole image with
   trivy's default rules, then the project's own files with every rule and skip pattern off. There is no
   allow-list. After the push the job checks that the pushed image is the image it scanned, then signs it with
   cosign keyless and attaches an SPDX SBOM attestation.
3. **`publish`** ("GitHub release"): the release notes, with pull and verify commands, and the SBOMs attached.
4. **`chart`** ("Helm chart"): `helm lint --strict`, `helm package`, `scripts/check_chart_package.py` checks the
   archive's `version` and `appVersion`, then the `.tgz` is attached to the release.
5. **`floating-tags`** ("move :core and :full"): last, and for final releases only. It re-points `:core` and
   `:full` at this release. This is the step users feel: `docker pull ...:core` changes.

**The Helm chart.** Today the chart is a `.tgz` release asset only; the project has decided it will ALSO be an
OCI package at `oci://ghcr.io/getexperimently/charts/experimently`; the first push and making it public are
founder-only and not done.

**If the credential scan fails** (the job stops at "Scan the image for stored credentials" or "Scan the payload
with every allow rule off"; the summary lists file, rule and line, never the matched text):

1. **Revoke and replace the credential first.** The image is built from the tagged tree, so the credential is
   almost certainly in the public git history too.
2. **Remove the file and cut a new patch release.** Re-running the same tag rebuilds the same tree and fails the
   same way.
3. **Versioned tags already pushed** by the other image jobs stay until an organisation owner deletes those
   package versions. `:core` and `:full` stay on the previous release.

---

## 5. Verify what shipped

For each of the four images (`experimently` and `experimently-web`, `core` and `full`):

```bash
IMAGE=ghcr.io/getexperimently/experimently:core-X.Y.Z

cosign verify "$IMAGE" \
  --certificate-identity-regexp '^https://github.com/getexperimently/experimently/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

cosign verify-attestation --type spdxjson "$IMAGE" \
  --certificate-identity-regexp '^https://github.com/getexperimently/experimently/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

docker pull --platform linux/amd64 "$IMAGE"
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' "$IMAGE"   # X.Y.Z
docker inspect --format '{{index .Config.Labels "io.experimently.profile"}}' "$IMAGE"            # core
```

Then check that the floating tags moved to the same digest as the versioned ones:

```bash
for t in core-X.Y.Z core; do
  docker buildx imagetools inspect "ghcr.io/getexperimently/experimently:$t" --format '{{json .Manifest}}' | jq -r .digest
done    # the two lines must be the same digest
```

**Anonymous pull.** A local `docker pull` does not prove anyone can pull: Docker Desktop can supply GitHub
credentials even with an empty `DOCKER_CONFIG`. Ask the registry directly, with no credentials:

```bash
PKG=experimently    # and experimently-web
TOKEN=$(curl -s "https://ghcr.io/token?scope=repository:getexperimently/$PKG:pull" | jq -r .token)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOKEN" \
  -H 'Accept: application/vnd.oci.image.index.v1+json' \
  "https://ghcr.io/v2/getexperimently/$PKG/manifests/core-X.Y.Z"     # 200
```

On 2026-10-03 that printed 200 for both packages at `core-0.17.0`.

**Security advisories** that the release fixes are published only after the patched release's images have been
pulled and checked this way ([open-source guide §6](./open-source.md#6-security-reports-and-advisories)).

---

## 6. A release-candidate rehearsal

```bash
git tag vX.Y.0-rc.1 && git push origin vX.Y.0-rc.1
```

A tag pushed with a human's credentials starts `release.yml` (and the docs build) directly. It publishes the
versioned image tags and a GitHub pre-release. It does **not** move `:core` or `:full`, and it does **not**
deploy the docs site.

**Cleaning up is irreversible and founder-approved:** `gh release delete vX.Y.0-rc.N`,
`git push origin :refs/tags/vX.Y.0-rc.N`, and an organisation owner deleting the rc package versions in GHCR.

Never push a rehearsal or tamper-test branch whose head is the same commit as a real pull request's head: the
checks on that shared commit get mixed up with the pull request's.

---

## 7. Before a package goes public: scan every version

**Visibility on GitHub Packages applies to the whole package, every version it has ever had, and a public
package cannot be made private again.** So the flip is founder-only, and it comes after every version in the
package has been scanned, not just the latest:

1. **List every version** of the package, tagged and untagged:

   ```bash
   gh api --paginate orgs/getexperimently/packages/container/<pkg>/versions \
     --jq '.[] | [.name, (.metadata.container.tags | join(","))] | @tsv' > versions.tsv
   ```

2. **Scan each image by digest** for stored credentials. Signature and attestation entries
   (`sha256-<hex>.sig`, `.att`) are not images; record that they were not scanned:

   ```bash
   trivy image --image-src remote --platform linux/amd64 --scanners secret \
     ghcr.io/getexperimently/<pkg>@sha256:<hex>
   ```

   When the project's two image packages went public, each had about 450 versions: every release tag and every
   untagged image no tag referenced were scanned.
3. **Founder flips the visibility** (package settings → Change visibility). The organisation must allow public
   packages first.
4. **Check anonymously** with the token check in §5.
5. **Only then** change anything (CI, docs) that assumes the package is public.

Each release now runs the credential scan before every push (§4), so a package that is already public stays
covered version by version. The same rule applies to the first Helm OCI package when it is made public.

---

## 8. SDK releases

SDKs version independently of the platform. Each is released by its own tag, in the form Go requires for a module
in a subdirectory, reused for every SDK:

```
sdk/js/v<X.Y.Z>      -> npm  @getexperimently/js-sdk
sdk/python/v<X.Y.Z>  -> PyPI experimently
sdk/go/v<X.Y.Z>      -> the Go module (the tag is the release)
```

Pushing the tag runs `.github/workflows/sdk-release.yml`. It can also be dispatched with `sdk` and `version`;
dispatch it on the tag ref (`--ref sdk/<name>/vX.Y.Z`), because the `sdk-release` environment admits only
`sdk/*/v*` tags.

**Which SDKs publish** (`scripts/check_sdk_version.py`):

| Kind | SDKs |
|---|---|
| PyPI | `python`, `openfeature-python` |
| npm | `js`, `edge`, `openfeature`, `react`, `react-native` |
| tag only | `go` |
| `unwired`: tagging is refused, nothing is published | `java`, `dotnet`, `ruby`, `php`, `elixir`, `flutter`, `android`, `ios` |

**The gates:**

- **The `sdk-release` environment:** a required reviewer, and only `sdk/*/v*` tags may deploy to it (read on
  2026-10-03 with `gh api repos/getexperimently/experimently/environments/sdk-release`).
- **Trusted publishing (OIDC).** No registry token is stored anywhere. Each registry must be told once to trust
  this repository, the workflow file `sdk-release.yml` and the environment `sdk-release`. Until it is, the publish
  step fails with an authentication error, which is the intended behaviour.
- **Publish `sdk/js` before `sdk/openfeature`.** The provider's release refuses until the matching
  `@getexperimently/js-sdk` is on npm.

**Not done yet:** no SDK has been published through `sdk-release.yml`. On 2026-10-03 the npm registry answered
404 for `@getexperimently/js-sdk`, and PyPI held only a `0.0.0` placeholder for `experimently` and
`experimently-openfeature`. npm's trusted-publisher setting needs the package to exist first, so the first npm
publish is a founder decision still to be made.

---

## 9. Founder-only and irreversible steps (releasing)

| Step | Who | Irreversible? | Read-only check afterwards |
|---|---|---|---|
| Allow Actions to create pull requests (organisation setting) | organisation owner | no | the next `release-please.yml` run opens or updates the release PR |
| Merge the release pull request with `--admin` | maintainer with admin rights | the tag and the versioned images, once published, are public | `git ls-remote --tags origin vX.Y.Z`; §3.7's run is green |
| Dispatch `release.yml` to recover a failed run | maintainer | re-points the versioned tags | `gh run view` of the recovery run; §5 |
| `floating-tags` moves `:core` and `:full` | automated, last job | yes, for anyone pulling the floating tag | §5's digest comparison |
| Publish an SDK to a registry | founder, at the `sdk-release` approval | yes: a published version cannot be reused | the registry's page or JSON for that version |
| Make a package public | founder (organisation owner) | **yes, whole package, every version** | §5's anonymous token check |
| First Helm OCI push, and making it public | founder | yes | an anonymous `tags/list` on the chart's GHCR path answers 200 |
| Delete package versions, rc tags or rc releases | organisation owner | yes | `gh release view vX.Y.0-rc.N` fails; the version is gone from the package's version list |
| Force push | nobody, on `main`: force pushes are refused by branch protection | yes | `gh api repos/getexperimently/experimently/branches/main/protection --jq .allow_force_pushes.enabled` is `false` |

### Not verified here

- The organisation-level Actions permission (it needs an organisation owner's token to read).
- Any npm or PyPI publish through `sdk-release.yml`: none has run against a registry. The PyPI trusted-publisher
  configuration needs a PyPI login to read.
- Whether the redundant 0.17.0 dispatch also re-signed the dashboard images; only the API package's versions were
  listed.

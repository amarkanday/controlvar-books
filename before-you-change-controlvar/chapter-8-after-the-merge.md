# Chapter 8 — After the merge

**Outcome:** you can follow your change from its squash commit on `main` to a line in the release notes,
a tag and published images, and you can write a title that gets it there.

## The title is the release note

`main` is squash-merged, so your pull request becomes one commit whose subject is the pull request title.
GitHub adds the number: #1732's commit on `main` is
`fix(api): the feature-flag list pages in a stable order (oldest first) (#1732)`.

A tool called release-please reads those subjects to write the changelog and choose the next version. It
understands only conventional commits, `type(optional scope): description`. A title in prose is
invisible to it, silently: the release is still cut, and your work is simply missing from it. The header
of `.github/workflows/conventional-title.yml` records when that happened: a change whose merge subject was
prose, and a release pull request that then held three old `docs:` commits and nothing from the change
that had just landed.

The `conventional-title` check holds every pull request title to this pattern, and re-runs when you edit
the title:

```yaml
# 📁 .github/workflows/conventional-title.yml
          TYPES='feat|fix|perf|deps|docs|build|ci|refactor|test|chore|revert|style'

          # type(optional scope)optional !: space, then something.
          PATTERN="^(${TYPES})(\([a-z0-9][a-z0-9._/-]*\))?!?: .+"
```

The type decides where your change appears. `release-please-config.json` maps each one to a section:

| Type | Section in `CHANGELOG.md` |
|---|---|
| `feat` | Features |
| `fix` | Bug Fixes |
| `perf` | Performance |
| `deps` | Dependencies |
| `docs` | Documentation |
| `build` | Build System |
| `ci`, `refactor`, `test`, `chore` | hidden: they appear in no section |

`revert` and `style` are accepted and ignored. The scope becomes the bold prefix of the line
(`**api:**`), so pick the area a reader would look for. Write the description as what a user now sees
("the feature-flag list pages in a stable order"), not what you did ("add list_order to CRUDBase"): it
is read by people deciding whether to upgrade.

A change that breaks something a user relies on gets a `!` after the type or scope, and a
`BREAKING CHANGE:` line in the commit body saying what breaks. Release 0.43.0 has one:
`fix(api)!: bound the scheduler history limit to 1..1000`, whose footer became the entry under "⚠
BREAKING CHANGES": "GET /api/v1/scheduler/{name}/history answers 422 for a limit of 0, a negative limit
or a limit above 1,000."

## From the release pull request to the release

1. **release-please keeps a standing release pull request** that accumulates every releasable commit
   since the last release.
2. **A maintainer merges it.** Merging writes `CHANGELOG.md`, bumps `VERSION` and
   `.release-please-manifest.json`, writes the new version into five more files (both OpenAPI snapshots,
   the dashboard's copy, the Helm chart and the Compose file), tags the merge commit `vX.Y.Z` and opens a
   GitHub release. Controlvar is before 1.0, and its configuration makes a release with a feature or a
   breaking change move the minor version. 0.43.0 carried both; 0.43.1 and 0.43.2 carried fixes (and one
   documentation change) and no feature, and moved the patch.
3. **`release.yml` builds, signs and publishes.** `release-please.yml` calls it directly once a release
   is created, rather than waiting for the tag, because a tag pushed with GitHub's own token starts no
   other workflow. It publishes the API and dashboard images for both profiles, tagged `core-X.Y.Z` and
   `full-X.Y.Z`, and a final release moves `:core` and `:full` to them. `docs/self-hosting/releases.md`
   lists what a release publishes and how to verify it.

The SDKs are released separately, each by its own tag, `sdk/<name>/vX.Y.Z`, through
`.github/workflows/sdk-release.yml`. A fix to an SDK reaches users when that SDK is next released, not
with the platform release.

> **By the Way.** Development moved into this public repository on 2026-09-25 (the note at the top of
> `CHANGELOG.md` says so). A pull request number in an older commit subject belongs to the private
> repository the project used before, and points at something else on the public tracker. Cite the
> commit hash for that period.

## Try it

1. Read the squash commit's subject:

   ```bash
   git log -1 --format=%s dbafee71
   ```

2. Find the same change in the release notes:

   ```bash
   grep -n 'stable order (oldest first)' CHANGELOG.md
   ```

   One line, under 0.43.0's Bug Fixes: `* **api:** the feature-flag list pages in a stable order
   (oldest first)`, with links to the pull request and the commit. The type chose the section, the scope
   the prefix, the description the words.

3. Find the first release that shipped it:

   ```bash
   git tag --contains dbafee71 --list 'v*' --sort=v:refname
   ```

   The first line is `v0.43.0`.

4. See how a breaking change is marked, and what it became:

   ```bash
   git log -1 --format=%B 6e3075e2
   git show v0.43.0:CHANGELOG.md | grep -n -m1 -A2 'BREAKING CHANGES'
   ```

5. Test your own title against the check's pattern before you open the pull request:

   ```bash
   TITLE='fix(api): the feature-flag list pages in a stable order (oldest first)'
   printf '%s' "$TITLE" | grep -qE '^(feat|fix|perf|deps|docs|build|ci|refactor|test|chore|revert|style)(\([a-z0-9][a-z0-9._/-]*\))?!?: .+' && echo OK || echo "not a conventional title"
   ```

   It prints `OK`. Try `TITLE='Fixed the flag list order'` and it does not.

## Where to go from here

You have the map, a running stack, the decisions, the gates, the process, one change from start to
finish, the changes that reach far, and the path to a release. Pick an issue, open the lesson in the
other books that covers its part of the code, and start with the test.

## Go deeper

- [Platform Operations, Hour 11](../platform-operations/part-4-release-engineering.md): from merge to
  production: release-please, signed images and the deploy workflow.
- [Platform Operations, Hour 12](../platform-operations/part-4-release-engineering.md): rollback, and what
  rolls back by itself.
- [SDK Engineering, Hour 12](../sdk-engineering/part-4-proving-agreement.md): releasing the SDKs.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): CI/CD and the release
  flow.

---

_Previous: [Chapter 7 — Changes that act at a distance](./chapter-7-changes-at-a-distance.md). Back to
[the start](./README.md)._

# Chapter 6 — A first change, start to finish

**Outcome:** you can take a small bug from an issue to a merged pull request, with every check green for
the right reason, using a real fix as the model.

## The example

Issue #1612 said: "the feature-flag list has no defined order across pages". `GET /api/v1/feature-flags/`
pages with `skip` and `limit`, and the query had no `ORDER BY`. Without one, PostgreSQL returns rows in
whatever order it finds them, and an `UPDATE` can move a row. A script paging through the flags could see
one twice and miss another.

Pull request #1732 fixed it. It was merged on 4 November 2026 as commit `dbafee71`, and it is small
enough to read in a few minutes: `git show dbafee71`. Here is the path it took, step by step, with what
each step asks of you.

## 1. The issue

For anything that is not small, open an issue first, so the shape is agreed before you write code. A
typo or a one-line bug can go straight to a pull request. The bug report form (`.github/ISSUE_TEMPLATE/bug_report.yml`)
asks for what happened, what you expected, and the smallest steps that show it: "A curl command, a
failing test, or a seed script beats a description." It labels the issue `bug`, and that label will
matter at step 7.

> **Watch Out!** A security problem is never an issue. A public issue for a vulnerability is itself the
> disclosure. Follow `SECURITY.md` instead.

## 2. The test, first

Write the test that fails on today's code before you change anything. It proves you understood the bug,
and it is the evidence a reviewer will ask for. The fix for #1612 added
`backend/tests/integration/api/test_flag_list_order.py` with three tests, each marked
`@pytest.mark.regression`. Here is one:

```python
# 📁 backend/tests/integration/api/test_flag_list_order.py
@pytest.mark.regression
def test_walking_the_pages_returns_every_flag_exactly_once_oldest_first(
    admin_client, make_feature_flag, db_session, marker
):
    a, b, c = _three_flags_with_the_oldest_updated(
        make_feature_flag, db_session, marker
    )

    seen = []
    for skip in range(3):
        resp = admin_client.get(
            "/api/v1/feature-flags/",
            params={"search": marker, "limit": 1, "skip": skip},
        )
        assert resp.status_code == 200, resp.text
        seen.extend(item["key"] for item in resp.json()["items"])
    assert seen == [a.key, b.key, c.key]
```

Notice three things. The test is named for what a user would see, not for the code. It lives under
`integration/` because it needs PostgreSQL. And it creates three flags, then updates the oldest, which is
the operation that moves a row: without that update the old code might pass by luck.

Run it on the old code and watch it fail. The pull request said what it saw: "On main (source fix
stashed): 3 failed".

## 3. The smallest fix

The fix is three lines of behaviour. The generic list helper learned to order by a list of columns a
subclass names, empty by default so no other list changes:

```python
# 📁 backend/app/crud/base.py
        # A subclass that pages for a caller names its order (``list_order``):
        # LIMIT/OFFSET without ORDER BY follows heap order, which an UPDATE moves.
        for column in self.list_order:
            query = query.order_by(getattr(self.model, column))
```

and the flag CRUD named its order:

```python
# 📁 backend/app/crud/crud_feature_flag.py
    #: The list pages oldest first; ``id`` breaks a tie in ``created_at`` (#1612).
    list_order = ("created_at", "id")
```

Both columns are `NOT NULL` and `created_at` is indexed, so no migration was needed. The pull request
says so, because a reviewer would ask.

## 4. Everything the change touches

A three-line fix touched nine files. That is normal here, and it is the part newcomers miss:

- the two source files and the new test file;
- `docs/api/endpoints.md`, which now says the list is ordered oldest first, and its entry in the claims
  registry, `docs/_claims/docs/api/endpoints.md.toml`, which names the test that holds the sentence
  (Chapter 4);
- the description of the MCP server's `list_feature_flags` tool, in `sdk/mcp/tools.json`,
  `sdk/mcp/src/tools.ts` and `backend/app/api/v1/endpoints/mcp.py`, and the MCP contract runner;
- and one file it did **not** touch, said out loud: "The OpenAPI snapshots do not change."

Ask of every change: who else describes this behaviour? A docs page, an SDK's comment, a tool
description, a snapshot.

## 5. Before you push

`make lint && make test` is what `CONTRIBUTING.md` asks for. At the least, run the files near your change:
the new test, the tests of the code you edited, and any gate that reads what you touched. The #1732
author ran the neighbouring flag-list, MCP, docs and OpenAPI-snapshot tests, and
`scripts/doc_claims.py check`.

## 6. Sign off every commit

Controlvar uses the Developer Certificate of Origin, not a contributor licence agreement. You keep your
copyright and certify that you have the right to submit the change under the licence of the files you
touch: Apache-2.0 everywhere except `sdk/`, which is MIT. Sign off with `-s`:

```bash
git commit -s -m "fix(api): the feature-flag list pages in a stable order"
```

`-s` adds a `Signed-off-by: Your Name <your.email@example.com>` line, and it must match the commit's
author or committer. The `DCO` check, a required one, runs `scripts/check_dco.py` over the pull request's
commits. Run the same script before you push, and fix a branch you already wrote with
`git rebase --signoff main`:

```bash
python3 scripts/check_dco.py --range main..HEAD
```

## 7. The title, the template, and what the checks do

**The title** is a conventional commit, because it becomes the release note (Chapter 8). #1732's was
`fix(api): the feature-flag list pages in a stable order (oldest first)`.

**The description** follows `.github/pull_request_template.md`. Its regression-test section is the one
reviewers read first:

```markdown
<!-- 📁 .github/pull_request_template.md -->
## Regression test

<!-- Required for anything labelled `bug`. Name the test and say, in one line,
     how it fails on the old code. A fix without a test that would have caught
     the bug is a fix that comes back. Mark it `@pytest.mark.regression`
     (backend) so the guard can find it. -->
```

#1732's description had three short parts: **Defect** (what was wrong, and who it hurt), **Fix** (what
changed, and what deliberately did not), and **Evidence** (the new tests, what they printed on the old
code and on the new, and the neighbouring tests run). It ended with `Closes #1612`. Copy that shape.

**Then the checks run**, and three of them are about the pull request rather than the code:

- **Your checks wait.** On a pull request from a fork, the workflows wait for a maintainer to approve
  them, after every push. "Workflows awaiting approval" is that safety step, not a verdict.
- **`contributor-guard`** says which files a fork may change. Code, tests and docs under `backend/`,
  `frontend/`, `sdk/`, `demo/`, `docs/`, `tests/` and the module code are open; workflows, `scripts/`,
  dependency and build files, infrastructure, and files other checks read (such as `docs/_claims/`) are
  changed by a maintainer. A red result means "a maintainer makes this part of the change", not "the
  change is rejected". #1732 came from the repository itself, so the guard passed at once. From a fork,
  it would have listed the claims file, and a maintainer would have added that part.
- **`regression-guard`** fails a pull request labelled `bug` that changes no test file. The waiver is a
  `skip-regression-guard` label, which a maintainer adds when the description says why the fix cannot
  be tested.

**The merge** is a squash: your branch's commits become one commit on `main`, and its subject is the
pull request title. Branches are not updated for you; if `main` moves and the pull request conflicts,
you may be asked to rebase. Leave "Allow edits from maintainers" ticked so a maintainer can push a small
fix to your branch.

## Try it

1. See the whole change: `git show --stat dbafee71`. Nine files, three of them the fix and its test.

2. Ask the contributor guard which of its files a fork could change. It uses only the standard library:

   ```bash
   python3 -c "
   import sys
   sys.path.insert(0, 'scripts')
   import contributor_guard as g
   for p in ['backend/app/crud/crud_feature_flag.py', 'docs/api/endpoints.md',
             'docs/_claims/docs/api/endpoints.md.toml', 'scripts/check_dco.py',
             'backend/requirements.txt']:
       rule = g.path_rule(p)
       print(p, '->', g.reason(rule, p) if rule else 'open to contributors')
   "
   ```

   The first two are open to contributors. The claims file is one of the "files that other checks read",
   `scripts/` is "repository scripts and CI checks", and the requirements file is "dependency and build
   files".

3. Watch the regression tests guard the fix. With `make db` running, in the test shell from Chapter 2:

   ```bash
   python -m pytest backend/tests/integration/api/test_flag_list_order.py -p no:cov -q
   ```

   `3 passed`. Now take the fix out: in `backend/app/crud/crud_feature_flag.py`, change
   `list_order = ("created_at", "id")` to `list_order = ()`, and run it again. `3 failed`; the first shows
   the second flag where the oldest should be. Put the line back with
   `git checkout -- backend/app/crud/crud_feature_flag.py`.

4. Check a sign-off the way the DCO job does:

   ```bash
   python3 scripts/check_dco.py --range dbafee71~1..dbafee71
   ```

   It prints `ok` beside the commit and `all 1 commit(s) carry a matching sign-off`.

## Go deeper

- [Build a Platform, Day 14](../build-a-platform-in-28-days/week-2-api-and-data.md): add a field end to
  end, from the model to the dashboard.
- [Production Engineering, Hour 11](../production-engineering/part-4-safe-change.md): one change taken
  across storage, the API, the dashboard, the contract and both profiles.
- [Learn FastAPI, Hour 23](../learn-fastapi-in-24-hours/part-6-testing-and-operating.md): database-backed
  tests and the harness.
- [Learn Python, Hour 23](../learn-python-in-24-hours/part-6-in-practice.md): pytest fixtures and
  parametrize.

---

_Previous: [Chapter 5 — The process](./chapter-5-the-process.md). Next: [Chapter 7 — Changes that act at a
distance](./chapter-7-changes-at-a-distance.md)._

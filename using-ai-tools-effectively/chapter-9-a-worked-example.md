# Chapter 9 — A worked example

**Outcome:** you can follow one real change from its issue to its release, and name at each step what the
agent produced, what checked it, and what that check found.

## The change, and why this one

The example is Controlvar pull request [#1464](https://github.com/Controlvar/controlvar/pull/1464),
"fix(scheduler): a scheduled draft that cannot start stays a draft". It is small: one application file, 28
lines changed.
Everything about it is public: the issue, the pull request description, its commits and the release. And
its description records a review round that found what the first version's tests had missed, even though
the first version came with its own table of planted defects. Its squash commit names a Claude model as
co-author, and its description ends with the "Generated with Claude Code" footer.

The whole thing took one day. On the public tracker, the issue was opened at 03:35 UTC on 2026-10-30, the
pull request at 05:27, and it merged at 06:59. It shipped in release 0.39.0 the same day.

## Step 1: the issue names the premise

Issue [#1454](https://github.com/Controlvar/controlvar/issues/1454) asked for two settings in the
dashboard that only the API had. One was scheduling a draft experiment's start and end dates. The issue
ends with a section for the plan:

> **Things the plan must settle**
>
> - **Time zones.** The API stores UTC without a zone marker. The picker works in the user's time zone
>   and must send the correct instant. The API refuses a start more than 10 minutes in the past.
> - **What the scheduler does with these dates.** It starts a draft and completes an active experiment
>   on a 15-minute interval. The copy must say so truthfully.
> - **Roles, and the capability table.**

The second bullet is the one that matters. A screen that says "it starts by itself at this time" is a
promise about the scheduler. The issue tells the plan to check that promise before making it.

## Step 2: the plan review measures it

The pull request's description says what the check found:

> The experiment scheduler started every DRAFT whose `start_date` had passed, without the check
> `POST /start` makes. Measured in the plan review: a draft with one variant, or one whose metrics a `PUT`
> removed after scheduling (the update accepts `metrics: []`), went ACTIVE at the next tick. The new
> schedule screen (PR 4) makes "it starts by itself" a dashboard promise, so this fix lands first.

Three of the plan's headings (Chapter 3) are visible in that paragraph. PREMISES: "the scheduler starts
a draft" was checked by running it, and it started drafts it should not have. SPLIT: the server fix became
its own pull request. SEQUENCE: it lands before the screen, because the screen's words would otherwise be
false. The screen arrived later the same day as pull request
[#1471](https://github.com/Controlvar/controlvar/pull/1471).

## Step 3: the fix

The fix calls the same check that the Start button's route uses, inside the scheduler's loop. At
`09d815ea` it reads:

```python
# 📁 backend/app/core/scheduler.py
            starts = ExperimentService(db)
            for experiment in experiments_to_activate:
                experiment_id = experiment.id
                savepoint = db.begin_nested()
                try:
                    resumed = experiment.status == ExperimentStatus.PAUSED
                    # A draft's scheduled first start makes the check Start
                    # makes (#1454); a draft that fails it stays a draft, its
                    # start_date kept, and is checked again next tick. A
                    # scheduled resume is not checked.
                    if not resumed and not starts._validate_experiment_for_start(
                        experiment
                    ):
                        savepoint.rollback()
                        logger.warning(NOT_READY_TO_START, experiment_id)
                        continue
                    ...
                except Exception:
                    savepoint.rollback()
                    failed_count += 1
                    logger.exception("Error activating experiment %s", experiment_id)
                    continue
```

The description lists what the fix promises. Each line is a property, and each property needs a test that
fails when it breaks:

- the check is the service's own, "reused, not copied";
- a draft that fails it stays a draft, keeps its start date, and gets no audit row and no notification;
- it "is not counted in the tick's `items_failed`, so a tick is not recorded as failed for a draft nobody
  has fixed yet";
- it is logged once per tick, as fixed text with the experiment's id and nothing a user typed;
- a paused experiment's scheduled resume is not checked;
- "the check runs inside the row's existing `try`", so an unexpected error is handled as before.

## Step 4: the first version's evidence

The first head, commit `766184c9`, came with three kinds of evidence.

**Red on the old code.** Four new tests, run against `main` before the fix: three failed, each with the
draft `ACTIVE` where it should be `DRAFT`. The fourth, the paused-resume case, passed, as it should,
because that behaviour was not meant to change.

**A table of plants.** Seven defects, each planted, run and reverted. Among them: checking the paused
resume too, `break` instead of `continue`, clearing the start date, putting the experiment's name in the
warning, no warning at all. Every one made at least one test fail.

**Two documentation gates.** The fix added a sentence to `docs/guides/user-guide.md` and registered it in
the claims registry (Chapter 2). Rewording the sentence without the registry fails
`scripts/doc_claims.py check`. Rewording an older sentence that another test quotes fails that test. Both
were planted and shown failing.

That looks thorough. It was not complete.

## Step 5: what the review found

The description's next section begins:

> Round 1 (the reviewer's P4, P7 and P2, which survived on the first head). Each was run against the
> draft-validation, row-isolation and pause-semantics files (57 tests); each fails exactly one test:

"Survived" means the reviewer planted a defect and every test still passed. Three did:

| The reviewer's plant | Which promise it breaks | Caught on the first head? |
|---|---|---|
| count a skipped draft in `items_failed` | not counted as failed | no |
| write an audit row for the skipped draft | no audit row | no: two tests did check "no audit row", but in ticks that stored nothing |
| move the check outside the row's `try` | "the check runs inside the row's existing `try`" | no |

So this is what the first version got wrong. The code was right. The tests checked the outcome a user
sees (the status, the date, the warning, the notification), and not three of the promises the fix itself
listed. Those three were sentences with no test that would fail if they became false: Chapter 2's rule
again, inside a pull request.

What caught it was not reading the tests. It was **planting new defects** (Chapter 7). The author's seven
plants were the defects the author had thought of. The reviewer's were the ones the reviewer suspected.

The round-1 commit, `ac6e0cf9`, changes one file, the test file, and no application code. It adds a test
that a skipped draft leaves `items_failed` unchanged, a test that a check which raises is handled inside
the row (counted once, rolled back, logged, and the rest of the tick still runs), and one assertion in an
existing test, in a tick that does commit:

```python
# 📁 backend/tests/integration/services/test_scheduler_draft_validation.py
    # This tick commits (the valid draft started), so a row written for the
    # skipped draft would be stored: none is.
    assert _starts_audited(db_session, invalid) == 0
```

The first of the new tests, at `09d815ea`:

```python
# 📁 backend/tests/integration/services/test_scheduler_draft_validation.py
def test_d5_a_skipped_draft_is_not_counted_as_failed(client, db_session, developer):
    """A draft nobody has fixed yet is not a failed item: a tick that skips it
    reports as many failures as the tick before it, when it was not due."""
    _, before = _tick_with_counts(db_session)
    experiment_id = _create(client, developer, variants=ONE_VARIANT)
    start = _schedule_due(client, developer, experiment_id)

    _, after = _tick_with_counts(db_session)

    assert _row(db_session, experiment_id) == (ExperimentStatus.DRAFT, start)
    assert after["items_failed"] == before["items_failed"]
```

Note that it compares with a tick run just before, not with zero: the test database is shared, so other
tests' rows may already count. The description says so: "It is a delta because the database is shared."
After round 1, each of the three plants fails exactly one test.

## Step 6: checking the story

A story like this is itself a claim. For this book it was checked at the two heads, each exported with
`git archive` into a plain directory and run against a scratch PostgreSQL 14:

- the new tests against the code before the fix: `3 failed, 1 passed`;
- the first head with `break` for `continue`: two tests fail, as the author's table says;
- the first head with each of the three plants from the table above: `4 passed` every time, so each
  plant survives;
- the second head with the same three plants: `1 failed, 5 passed` every time, a different test each
  time. Counting the skip fails `assert after["items_failed"] == before["items_failed"]` with
  `assert 3 == 2`, and the audit row fails `assert 1 == 0`, the texts the pull request quotes. Moving
  the check out of the `try` fails the new raising-check test, with the tick's own error in the log.

## Step 7: what the change said it did not check

The pull request ends with this:

> - A real 15-minute scheduler loop. The tests call one tick directly, as the existing scheduler tests do.
> - The full integration suite on CI's Postgres image. I ran Homebrew Postgres 14 with session zone UTC.
>   CI is the evidence for the rest of `backend/tests/integration`.
> - browser-e2e: no Docker here. CI is the evidence.
> - `test_sdk_release_wiring.py`'s two cases (environment, above). CI is the evidence.

Each item says what was not run and where the evidence comes from instead. And one sentence in the
description is worth a reviewer's minute: the fix also edited three existing unit tests, whose fake drafts
had no variants or metrics and would now be refused, and it says "No assertion changed." That is easy to
check, and it is the claim that matters most when an agent edits tests: those edits add 19 lines and
remove none.

## Step 8: merged, released, and the promise kept

The squash commit on `main` is `522fdc99`. It carries a `Signed-off-by` line and a `Co-authored-by` line
naming the model. Its title is a conventional commit, so it appears in the 0.39.0 release notes under Bug
Fixes: "**scheduler:** a scheduled draft that cannot start stays a draft".

The dashboard screen in #1471 could then say a true thing. Its help text, at `09d815ea`:

```ts
// 📁 frontend/src/components/experiments/ExperimentScheduleSection.tsx
export const START_HELP =
  'It starts by itself at this time. A background check runs every 15 minutes by default, so it ' +
  'can start up to that much later. Until then it stays a draft, and Start still starts it at ' +
  'once. Start before the scheduled start moves the start to now. If it is not ready to start ' +
  '(two variants, a control and a metric), it stays a draft until it is.';
```

The last sentence is the one the plan review made possible.

## What to take from it

- **The plan found the bug.** Nobody reported it. The issue asked the plan to check a premise, and the
  check was run, not argued.
- **The split let the true fix ship first.** The screen waited a few hours for the server.
- **The author's tamper table looked complete and was not.** Seven plants, all caught, and three gaps.
- **A reviewer who plants finds what a reviewer who reads does not.**
- **The round-1 fix changed only tests.** The diff of the diffs (Chapter 7) was one file, and that is
  where the review had pointed.
- **Every promise in the description now has a test.** That was the bar.

## Try it

1. Read the pull request's description on GitHub, or with `gh pr view 1464 --repo Controlvar/controlvar`.
   Find the two plant tables and the NOT VERIFIED list.

2. Fetch its two heads (Chapter 7 did this) and confirm that round 1 changed only tests, and that the
   fix's edits to the existing unit tests only added lines:

   ```bash
   git fetch origin pull/1464/head:pr-1464
   git diff --stat 766184c9 ac6e0cf9
   git diff --numstat 766184c9~1 766184c9 -- backend/tests/unit
   ```

   The second command lists three files with `0` in the deletions column.

3. With PostgreSQL running (`make db`) and in the test shell from
   [Before You Change Controlvar, Chapter 2](../before-you-change-controlvar/chapter-2-run-it.md), run the
   tests at your commit:

   ```bash
   python -m pytest backend/tests/integration/services/test_scheduler_draft_validation.py -p no:cov -q
   ```

   At `09d815ea`: `8 passed` (a later change added a split URL case, run twice).

4. Plant the defect the review found. In `backend/app/core/scheduler.py`, in the branch that logs
   `NOT_READY_TO_START`, add `failed_count += 1` on the line before its `continue`. Run step 3 again: `1
   failed, 7 passed`, and the failure is `assert 3 == 2`. Put the file back and check:

   ```bash
   git checkout -- backend/app/core/scheduler.py
   git status --short
   ```

5. Ask your AI tool to read the fix and its tests, and to name one more promise in the description that
   no test would catch if it broke. Then ask it to prove its answer the way the review did: plant the
   defect, run the tests, show that they all pass, and revert. If every test fails, the promise was
   covered after all, and that is a useful answer too.

## Go deeper

- [Before You Change Controlvar, Chapter 6](../before-you-change-controlvar/chapter-6-a-first-change.md):
  another real fix, from the issue to the merge, seen as a contributor.
- [Build a Platform, Day 17](../build-a-platform-in-28-days/week-3-hard-parts.md): the background
  schedulers and their lock.
- [Production Engineering, Hour 5](../production-engineering/part-2-reliable-systems.md): schedulers,
  replicas and stale state.

---

_Previous: [Chapter 8 — Safety rails](./chapter-8-safety-rails.md). Next: [Chapter 10 — When not to use an
agent](./chapter-10-when-not-to-use-an-agent.md)._

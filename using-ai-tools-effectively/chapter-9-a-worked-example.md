# Chapter 9 — A worked example

At 03:35 UTC on 30 October 2026, issue [#1454](https://github.com/Controlvar/controlvar/issues/1454) was
opened against Controlvar. It asked for two settings in the dashboard that only the API offered, and one
of them was scheduling a draft experiment's start and end dates. The issue ended with a short section
headed "Things the plan must settle", and one item in it told whoever planned the work to find out what
the background scheduler actually does with those dates, so that the screen's text would "say so
truthfully".

The plan review settled it by measurement. A scheduled draft with one variant, which the Start button
refuses, went `ACTIVE` at the scheduler's next tick. So did a draft whose metrics had been removed after
it was scheduled. The scheduler had been starting, on its own, experiments that a person clicking Start
could not start, and the dashboard work about to begin would have put a promise on screen, "it starts by
itself", on top of that behaviour. Less than two hours after the issue was opened, at
05:27, a pull request with the fix was open. It merged at 06:59 and shipped in release 0.39.0, published at
22:33 UTC the same day. The screen followed in a separate pull request, after the fix.

This chapter follows that one change, pull request [#1464](https://github.com/Controlvar/controlvar/pull/1464),
from the issue to the release. At each step you will see what the agent produced, what checked it, and
what the check found. Every earlier chapter appears somewhere in it: a premise checked by running it
(Chapter 3), a description that lists what it did not verify (Chapter 5), planted defects (Chapter 6), and
a review aimed at what the fixes changed (Chapter 7). Its most useful part is a failure. The first version
came with a table of seven planted defects, every one caught, and the review still found three promises
that no test held.

## Why this change

Three things make #1464 a good specimen. It is small: one application file, `backend/app/core/scheduler.py`,
with 28 lines changed, plus tests and one sentence of documentation. It is public from end to end, so you
can read the issue, the description, both heads of the branch and the release notes yourself, and the
exercises have you do it. And it was written with an agent in the way this book describes. Each of the
branch's own commits, and the squash commit on `main`, carries a `Co-authored-by` trailer naming a Claude
model, and the description ends with the "Generated with Claude Code" footer.

Size matters for a worked example for the same reason it matters in review (Chapter 7). A small change lets
you hold every promise it makes in your head at once, and that is what this chapter asks you to do.

## The issue names a premise

Here is the end of the issue, as it reads on the public tracker:

> **Things the plan must settle**
>
> - **Time zones.** The API stores UTC without a zone marker. The picker works in the user's time zone
>   and must send the correct instant. The API refuses a start more than 10 minutes in the past.
> - **What the scheduler does with these dates.** It starts a draft and completes an active experiment
>   on a 15-minute interval. The copy must say so truthfully.
> - **Roles, and the capability table.**

The second item carries the weight. A screen that says "it starts by itself at this time" is a promise
about code the screen does not contain. The issue does not leave that promise to whoever writes the
screen. It lists it among the things the plan has to settle, and it gives the standard: the text must say
what the scheduler does, truthfully.

That is a small habit with a large effect. The issue could have described the feature alone. It also
described the premise the feature stood on, and so the plan's reviewers had a reason to test the
scheduler before anyone drew a date picker. Chapter 3's plan has a heading for exactly this, PREMISES,
"every factual claim, each with what you ran to check it". An issue that names the claims in advance makes
that heading hard to fill with guesses.

## The plan measures it

The pull request's description reports what the check found:

> The experiment scheduler started every DRAFT whose `start_date` had passed, without the check
> `POST /start` makes. Measured in the plan review: a draft with one variant, or one whose metrics a `PUT`
> removed after scheduling (the update accepts `metrics: []`), went ACTIVE at the next tick. The new
> schedule screen (PR 4) makes "it starts by itself" a dashboard promise, so this fix lands first.

Three of the plan's nine headings show through that paragraph. PREMISES: the claim "the scheduler starts a
draft" was checked by running it, and it turned out to be true in a way nobody wanted, since it started
drafts that should not start. SPLIT: the server fix became a pull request of its own, separate from the
screen. SEQUENCE: the fix had to land first, because until it did, the screen's words would be false. The
screen arrived as pull request [#1471](https://github.com/Controlvar/controlvar/pull/1471), opened at 09:21
and merged at 10:11, more than three hours after the fix.

Notice the word "Measured". The description could have said the scheduler "does not appear to validate
drafts", which is what reading the code would give you. A reading is an opinion about the code; a
measurement is a fact about it, and it came with the two cases that failed. Those two cases became the
first two tests.

Notice also what the split cost. The dashboard work waited for a server fix that the issue had not asked
for. The alternative, one pull request with the fix and the screen together, would have been larger to
review, and the engineering manager's brief (Chapter 7) asks for "the smallest piece that closes a
property on its own". The server fix was that piece, and it merged within two hours of being opened.

## The fix

The fix makes the scheduler call the same check that the Start button's route calls, inside the code that
activates each due experiment. At `09d815ea` it reads:

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

Read it from the outside in. Each due experiment gets its own savepoint, so one row the database refuses is
rolled back alone and the rest of the tick goes on; that structure was already there. The new lines sit
inside the `try`. `resumed` separates the two reasons an experiment can be due: a draft whose start date
has passed, and a paused experiment whose scheduled resume has passed. Only the first is checked. The check
is `_validate_experiment_for_start`, the service's own method, the one the Start route uses. A draft that
fails it is rolled back to its savepoint, logged, and skipped with `continue`. Nothing else happens to it.
Its start date stays, so the next tick checks it again, and once someone fixes the draft it starts at the
first tick after the fix.

The log line is a constant defined near the top of the file:

```python
# 📁 backend/app/core/scheduler.py
#: The one warning for a due draft the check below refuses. Fixed text: the id
#: is the only value in it, never the experiment's name or anything a user typed.
NOT_READY_TO_START = (
    "Scheduled start skipped for experiment %s: it is not ready to start (it "
    "needs at least two variants, a control and a metric, and a split URL "
    "experiment needs a valid split_url_config), so it stays a draft"
)
```

The clause about split URL experiments is newer than the fix; a later pull request added it, and the section
"After the merge" comes back to it. The comment above the constant is the part that dates from #1464, and
it states a promise: the warning carries the id and nothing a user typed. A promise like that is easy to
break in a later edit, by someone who wants a friendlier log line with the experiment's name in it. So it
needs a test, and the next section is about which promises got one.

## Every promise is a property

The description lists what the fix guarantees, and each line is written as a property you could test. In
the description's order, a draft that fails the check:

- stays `DRAFT` with its `start_date` unchanged;
- gets no audit row and no notification;
- "is not counted in the tick's `items_failed`, so a tick is not recorded as failed for a draft nobody has
  fixed yet";
- is logged once per tick at WARNING, as fixed text whose only value is the experiment's id;
- does not stop the tick: the scheduler goes on to the next experiment.

Three more lines describe what does not change. A paused experiment's scheduled resume is not checked. An
unexpected exception keeps the old handling, because "the check runs inside the row's existing `try`, so
the savepoint is rolled back, the failure is counted and logged, and the tick goes on". And the check is
the service's own: "The rules are reused, not copied."

That last line deserves a moment. Reusing the method means the Start button and the scheduler cannot
drift apart: a rule added to one is added to both. A copied rule would have needed its own tests and, worse,
its own maintenance. You will see this pay off in the section on what happened after the merge.

Writing promises this way turns review into bookkeeping. For each line you can ask which test fails if
the line becomes false, and if no test does, the line is a claim with nothing behind it. Here is that
ledger for the finished pull request, built from its description. The tests are named by their prefix:
D1 is `test_d1_a_one_variant_draft_stays_a_draft_until_it_has_two`, and so on.

| Promise | Test that holds it | Planted defect that proved it |
|---|---|---|
| stays a draft, start date kept | D1, D2, D3 | the start date cleared: D1, D2 and D3 fail |
| no audit row | D3, in a tick that commits | an audit row written: D3 fails |
| no notification | D1 | none planted |
| not counted in `items_failed` | D5 | `failed_count += 1` on a skip: D5 fails |
| one warning, fixed text | D1, D2 | the name added, or no warning: D1 and D2 fail |
| a paused resume is not checked | D4 | the check run on a resume: D4 fails |
| an error keeps the old handling | D6 | the check moved above the `try`: D6 fails |
| one invalid draft does not stop the tick | D3, D4 | `break` for `continue`: D3 and D4 fail |

The ledger has one row with no plant, "no notification". That is fine as long as you know it: D1 asserts
that no notification was sent, so the property is held, and the claim that it would fail has not been
watched. The more interesting thing about the ledger is how its second, fourth and seventh rows looked on
the first head: none of the three had a test that could fail.

## The first head's evidence

The first head, commit `766184c9`, came with three kinds of evidence. First, the new tests had been run
against `main` before the fix, and the description pastes the result. Trimmed to the summary lines:

```text
FAILED ...::test_d1_a_one_variant_draft_stays_a_draft_until_it_has_two
FAILED ...::test_d2_a_draft_whose_metrics_were_removed_after_scheduling_stays_a_draft
FAILED ...::test_d3_a_valid_draft_starts_in_the_same_tick_as_one_that_cannot
=================== 3 failed, 1 passed (D4: unchanged behaviour) ===================
```

Three tests fail on the old code, each with the draft `ACTIVE` where it should be `DRAFT`. The fourth
passes, and the annotation says why that is correct: D4 covers the paused resume, whose behaviour the fix
was meant to leave alone. A test that passes on the old code is fine when it pins something that must not
change, and the description labels D4 as that kind.

Second came a table of seven planted defects, each planted, run against a real PostgreSQL and reverted.
Among them were checking the paused resume too, `break` in place of `continue`, clearing the start date,
putting the experiment's name in the warning, and no warning at all. Every one made at least one test
fail. Two of the seven were aimed at the documentation. The fix added one sentence to
`docs/guides/user-guide.md` and registered it in the claims registry (Chapter 2), and the table shows that
rewording the sentence without updating the registry fails `scripts/doc_claims.py check`, while rewording
an older sentence that another test quotes fails that test.

Third, there was a claim about existing tests. The fix had to edit three unit-test files, because their fake
drafts had no variants or metrics and the scheduler would now, correctly, refuse them. The description says
each fake draft gained two variants and a metric, and then: "No assertion changed." When an agent edits
existing tests, that is the claim to check before any other, because weakening an old assertion is the
quiet way to make a change pass. It is also cheap to check:

```bash
git diff --numstat 766184c9~1 766184c9 -- backend/tests/unit
```

```text
3	0	backend/tests/unit/schedulers/test_scheduler_notifications.py
3	0	backend/tests/unit/services/test_audit_log_rules.py
13	0	backend/tests/unit/services/test_experiment_scheduler.py
```

Nineteen lines added, none removed. An assertion cannot be changed without removing a line, so the claim
holds. A reviewer with less time could have stopped at this point, with red-on-old evidence, seven plants
and a clean edit of the old tests. The change looked finished.

## What the review planted

The description's next section begins:

> Round 1 (the reviewer's P4, P7 and P2, which survived on the first head). Each was run against the
> draft-validation, row-isolation and pause-semantics files (57 tests); each fails exactly one test:

"Survived" means the reviewer planted a defect and every test still passed. The reviewer's plants were
different from the author's, and three of them survived:

| The reviewer's plant | The promise it breaks | After round 1 |
|---|---|---|
| `failed_count += 1` on a skip | not counted in `items_failed` | D5 fails: `assert 3 == 2` |
| an audit row written for the skipped draft | no audit row | D3 fails: `assert 1 == 0` |
| the check hoisted out of the row's `try` | an error keeps the old handling | D6 fails: the tick aborts |

Each survivor has its own reason. Nothing on the first head looked at `items_failed` at all; the tests
read what a user would see, the status, the date, the warning and the notification, and the count is
something only the scheduler's run record shows. The audit row is subtler. Two tests on the first head, D1
and D2, did assert "no audit row" for the skipped draft, in ticks where no valid draft starts. The pull
request does not give the mechanism, so what follows is our reading of why the audit-row plant survived.
The round-1 commit message says only that the new assertion is made "in a tick that commits", and the
comment it adds (shown below) says that in such a tick a row written for the skipped draft "would be
stored". In a tick where nothing starts, a stray row was never stored, and the assertion was true for a
reason unrelated to the code under test. And the `try` placement had no test because no test made the
check itself raise.

None of the three is a bug in the code. Round 1 changed no application code, so the scheduler that merged
is the scheduler of the first head. What was missing was the evidence for three of the promises the
description made, which is Chapter 2's rule reaching inside a pull request: a claim about behaviour needs a
test, or it should be written as intent.

The author's seven plants had all been caught. They were the defects the author thought of, and an author
plants what it imagines going wrong. The reviewer planted what it suspected the tests did not look at.
That is why Chapter 7 asks the reviewer to plant its own defects instead of trusting the author's table.

### The round-1 commit

The answer came ten minutes after the first commit. Commit `ac6e0cf9`, at 05:36 UTC, is titled
"test(scheduler): pin the skipped draft's audit, count and error handling", and it changes one file, the
test file, with no application code at all. That is the diff of the diffs from Chapter 7, and it is small
enough to read in full. It adds one assertion to D3, in a tick that does commit:

```python
# 📁 backend/tests/integration/services/test_scheduler_draft_validation.py
    # This tick commits (the valid draft started), so a row written for the
    # skipped draft would be stored: none is.
    assert _starts_audited(db_session, invalid) == 0
```

The comment is the whole lesson of the audit-row survivor, written where the next editor will see it. The
round also adds two tests. D6 makes the check raise for one draft and asserts that the row is rolled back,
counted once, logged once, and that a valid draft due in the same tick still starts. D5 holds the count:

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

Notice that it compares two ticks and never asserts zero. The test database is shared, so other tests'
rows may already be due and already count; the description says so in a sentence, "It is a delta because
the database is shared." The module's docstring adds that the counts are "only compared with a tick run
just before (a delta), never asserted outright". The obvious assertion, a count of zero, would have
depended on whatever other tests had left in the database. Reading a test for what else could make it
pass or fail is part of reviewing it.

After round 1, each of the three plants fails exactly one test, with the texts in the table above.

## What this edition checked, and what it did not

A story like this is itself a set of claims, and it would be odd for this book to skip checking its own.
Some of it needs only git and the public tracker, and the exercises at the end run those parts: the two
heads exist with those subjects, round 1 changed only the test file, the edits to the old unit tests added
lines and removed none, and the claims registry names the tests that hold the user guide's sentence.

The plant results need PostgreSQL. This edition was prepared without a database, so the numbers above are
the pull request's own, quoted from its description. The first edition of this chapter did run them: it
exported both heads with `git archive` and ran the tests against a scratch PostgreSQL 14, and the book's
README records the results, which matched the description. If you have the test environment, the seventh
and eighth exercises repeat the part that matters most.

> **Note**
>
> **Quote the run, or run it again.** When you retell an agent's work, as a book or a review or a release
> note does, each number is either one you reproduced or one you are quoting. Both are honest when they are
> labelled. The failure is a quoted number presented as your own measurement, and it is the same failure
> as the "it passed" of Chapter 5, one step removed.

## What the change said it did not check

The description ends with a NOT VERIFIED list of four items. The first is the real 15-minute schedule
itself: "The tests call one tick directly, as the existing scheduler tests do." The other three:

> - The full integration suite on CI's Postgres image. I ran Homebrew Postgres 14 with session zone UTC.
>   CI is the evidence for the rest of `backend/tests/integration`.
> - browser-e2e: no Docker here. CI is the evidence.
> - `test_sdk_release_wiring.py`'s two cases (environment, above). CI is the evidence.

Each item says what was not run and where the evidence will come from instead. That second half is what
makes the list useful. A reviewer reading it knows which CI checks settle the open questions, and knows
that a red `Browser E2E` on this pull request is news, while a green one is the evidence the author
promised. The last item refers back to the description's own local results, which recorded two failures in
`smoke/test_sdk_release_wiring.py` caused by a missing package in that machine's environment, "unrelated to
this diff", and handed them to CI.

Compare that with the illustrative report in Chapter 5, which ended "NOT VERIFIED: nothing." A real change
always leaves something unverified. This list is what an agent's report looks like when the brief asks for
it and the reviewer reads it.

## Merged, released, and the promise kept

The squash commit on `main` is `522fdc99`, committed at 06:59 UTC. It carries a `Signed-off-by` trailer,
so it passes the DCO check of Chapter 8, and a `Co-authored-by` trailer naming the model. Its subject is
the pull request's title, a conventional commit, so release-please could read it. In the 0.39.0 release
notes, published at 22:33 UTC the same day, it appears under Bug Fixes (its two links trimmed here):

> * **scheduler:** a scheduled draft that cannot start stays a draft

The screen in #1471 could then make a true promise. Its help text at `09d815ea`:

```ts
// 📁 frontend/src/components/experiments/ExperimentScheduleSection.tsx
export const START_HELP =
  'It starts by itself at this time. A background check runs every 15 minutes by default, so it ' +
  'can start up to that much later. Until then it stays a draft, and Start still starts it at ' +
  'once. Start before the scheduled start moves the start to now. If it is not ready to start ' +
  '(two variants, a control and a metric), it stays a draft until it is.';
```

Read it against the issue. "It starts by itself" is the promise the issue asked the plan to check. "Every
15 minutes by default, so it can start up to that much later" is the truthful copy the issue asked for.
The last sentence exists because of the plan review: before #1464 it would have been false, and the
screen would have shipped a promise the scheduler broke. The user guide gained a matching sentence, and the
claims registry ties it to the tests that hold it, so rewording it without updating the registry fails a
check (Chapter 2).

## After the merge

Code moves on, and a worked example should show how this one aged. By `09d815ea` four later commits had
changed the scheduler file, and one of them extended this fix. Pull request
[#1560](https://github.com/Controlvar/controlvar/pull/1560), on 1 November 2026, made the API refuse a split
URL experiment whose URL variants do not match its variants. Because the scheduler calls the service's own
check, the scheduler refused those drafts too, with no change to the check's call site. The constant's
text grew a clause, the user guide's sentence grew the same clause, and the test file gained D7, a
parametrised test that runs twice. The registry entry for the sentence now names four tests:

```text
# 📁 docs/_claims/docs/guides/user-guide.md.toml
text = "A draft that is not ready to start (it needs two variants, a control and a metric, and a split URL experiment needs a valid split_url_config) stays a draft until it is."
by = [
    "test:backend/tests/integration/services/test_scheduler_draft_validation.py::test_d1_a_one_variant_draft_stays_a_draft_until_it_has_two",
    "test:backend/tests/integration/services/test_scheduler_draft_validation.py::test_d2_a_draft_whose_metrics_were_removed_after_scheduling_stays_a_draft",
    "test:backend/tests/integration/services/test_scheduler_draft_validation.py::test_d3_a_valid_draft_starts_in_the_same_tick_as_one_that_cannot",
    "test:backend/tests/integration/services/test_scheduler_draft_validation.py::test_d7_a_split_url_draft_that_cannot_split_stays_a_draft",
]
```

That is "reused, not copied" paying off two days later: the later pull request changed the constant's
text and nothing at the place where the scheduler calls the check. A copied rule would have needed a
second edit, in a place the later pull request had no reason to look. Notice also what did not change:
the dashboard's help text still lists "two variants, a control and a metric" and says nothing about split
URLs. The user guide and the registry moved with the code; the screen's copy is a separate promise, and
keeping it current is a separate job.

## Reading it back

The round-1 fix changed only tests, and the diff of the diffs was one file, which is where the review had
pointed. By the merge, every promise in the description had a test that asserts it, and all but one had
been watched to fail under a plant. That was the bar, and it is a bar you can apply to any agent's pull
request: list its promises, and for each one ask to see the test and the plant.

## Try it

Steps 1 to 6 need only git, a clone of Controlvar and the `gh` command-line tool; output was taken at
`09d815ea` on macOS. Steps 7 and 8 need PostgreSQL and the test environment.

1. Read the pull request's summary from the public tracker:

   ```bash
   gh pr view 1464 --repo Controlvar/controlvar --json title,createdAt,mergedAt,additions,deletions
   ```

   ```text
   {"additions":490,"createdAt":"2026-10-30T05:27:33Z","deletions":4,"mergedAt":"2026-10-30T06:59:14Z","title":"fix(scheduler): a scheduled draft that cannot start stays a draft"}
   ```

   Then open its description in a browser and find the two plant tables and the NOT VERIFIED list.

2. Fetch the branch's heads. Public pull requests can be fetched by number:

   ```bash
   git fetch origin pull/1464/head:pr-1464
   git log --oneline -4 pr-1464
   ```

   ```text
   d75e4f3d Merge origin/main (982041c2, #1452) into this branch
   982041c2 fix(dashboard): the experiment page shows its exclusion group and the global holdout, read-only (#1452)
   ac6e0cf9 test(scheduler): pin the skipped draft's audit, count and error handling
   766184c9 fix(scheduler): a scheduled draft that cannot start stays a draft
   ```

   The branch's own commits are the bottom two: the first head and the round-1 fix. The top two are a
   merge of `main`, which the review did not need to read.

3. Look at the diff of the diffs, and confirm round 1 touched no application code:

   ```bash
   git diff --stat 766184c9 ac6e0cf9
   git diff --quiet 766184c9 ac6e0cf9 -- backend/app; echo "exit $?"
   ```

   ```text
    .../services/test_scheduler_draft_validation.py    | 85 ++++++++++++++++++++--
    1 file changed, 79 insertions(+), 6 deletions(-)
   exit 0
   ```

   `git diff --quiet` exits 0 when there is no difference, so `exit 0` means nothing under
   `backend/app` changed in round 1.

4. Check the claim "No assertion changed" yourself:

   ```bash
   git diff --numstat 766184c9~1 766184c9 -- backend/tests/unit
   ```

   ```text
   3	0	backend/tests/unit/schedulers/test_scheduler_notifications.py
   3	0	backend/tests/unit/services/test_audit_log_rules.py
   13	0	backend/tests/unit/services/test_experiment_scheduler.py
   ```

   The second column is lines removed. All zeros.

5. Find the sentence's entry in the claims registry, and the tests it names:

   ```bash
   grep -c 'test_scheduler_draft_validation.py::' docs/_claims/docs/guides/user-guide.md.toml
   ```

   ```text
   4
   ```

   Open the file at the entry for "not ready to start" to see which four.

6. Count the tests in the file today:

   ```bash
   grep -c '^def test_' backend/tests/integration/services/test_scheduler_draft_validation.py
   grep -n -A2 '^@pytest.mark.parametrize' backend/tests/integration/services/test_scheduler_draft_validation.py
   ```

   ```text
   7
   477:@pytest.mark.parametrize(
   478-    "has_config", [False, True], ids=["no-config", "url-variants-not-its-variants"]
   479-)
   ```

   Seven test functions, one of them parametrised over two cases, so a run collects eight.

7. With PostgreSQL running (`make db`) and the test shell from
   [Before You Change Controlvar, Chapter 2](../before-you-change-controlvar/chapter-2-run-it.md), run the
   tests. NOT RUN for this edition, which used no database:

   ```bash
   python -m pytest backend/tests/integration/services/test_scheduler_draft_validation.py -p no:cov -q
   ```

   At `09d815ea` the file holds the eight cases from step 6. The pull request reports "6 passed" after
   round 1, when the file held six.

8. Plant the defect the review found. In `backend/app/core/scheduler.py`, in the branch that logs
   `NOT_READY_TO_START`, add `failed_count += 1` on the line before its `continue`, and run step 7 again.
   NOT RUN for this edition; the pull request reports that D5 fails with `assert 3 == 2`. Then put the file
   back and confirm the tree is clean:

   ```bash
   git restore backend/app/core/scheduler.py
   git status --short
   ```

9. Ask your AI tool to read the fix and its tests, and to name one promise in the description that no
   test would catch if it broke. Then ask it to prove its answer the way the review did: plant the defect,
   run the tests, show that they all pass, and revert. If a test fails, the promise was covered after all,
   and that is a useful answer too.

## Summary

Pull request #1464 went from an issue to a release in one day, and every step was checked by something
aimed where the step before had not looked. The issue named a premise, the plan review measured it and
found a real defect, and the split let the fix ship before the screen that depended on it. The author's
evidence was thorough and still covered only the defects the author imagined; the reviewer's plants found
three promises with no test, and the fix for that changed only tests. By the merge, each promise in the
description had a test behind it, and the sentence on the dashboard was true. Chapter 10 asks
the opposite question: which work in this story, and in yours, should never have gone to an agent at all.

## Further reading

- [Before You Change Controlvar, Chapter 6](../before-you-change-controlvar/chapter-6-a-first-change.md):
  another real fix, from the issue to the merge, seen as a contributor.
- [Build a Platform, Day 17](../build-a-platform-in-28-days/week-3-hard-parts.md): the background
  schedulers, and how each tick runs on one replica at a time.
- [Production Engineering, Hour 5](../production-engineering/part-2-reliable-systems.md): schedulers,
  replicas and stale state.

---

_Previous: [Chapter 8 — Safety rails](./chapter-8-safety-rails.md). Next: [Chapter 10 — When not to use an
agent](./chapter-10-when-not-to-use-an-agent.md)._

# Chapter 7 — Reviewing agent work

At 08:26 UTC on 3 November 2026, pull request [#1686](https://github.com/Controlvar/controlvar/pull/1686)
was opened to fix two bugs in Controlvar's public tracking API. An event whose `timestamp` sat at the very
edge of the date range, with an offset that pushed it past that range in UTC, made `/track` and `/events`
answer 500 ([#1684](https://github.com/Controlvar/controlvar/issues/1684)). Event metadata nested about 255
levels deep made the same two routes store the event and then answer 500
([#1685](https://github.com/Controlvar/controlvar/issues/1685)). The first head, `74bc3904`, fixed both,
with regression tests. A review asked for changes: two blocking findings, one of them a red Unit Tests
check, and four smaller ones labelled N1 to N4. N4 was about one line of `docs/api/endpoints.md`, the
description of the batch route's response, and the answer the pull request records makes that line say
which kind of bad entry refuses the whole batch with a 422 and which is reported in `errors` while the
rest are stored.

At 08:55 the round-1 commit, `a8e8db0e`, answered all six. Its answer to N4 inserted three words, "as a
request", into the middle of the existing sentence and added a clause after it. At 09:08 the Unit Tests
check failed again, for a new reason, and `core-build` failed at 09:30. The sentence was more than
documentation. `tests/ground_truth/ground_truth/contract.py` applies the documented rules for which events
count in an experiment's results, and computes the numbers the results API must show; each rule quotes,
word for word, the sentence of the documentation that makes it the contract. One of those quotes was this
sentence, and a unit test fails whenever a quoted sentence is no longer on its page. A rewording made to
answer a nit had broken a test in a directory the change never touched.

The round-2 commit, `956d825f` at 10:09, put the sentence back verbatim and gave N4's distinction a
sentence of its own. The pull request merged at 13:52, and its description keeps the lesson: "any edit to
`docs/api/endpoints.md` must run `backend/tests/unit/ground_truth`." Nothing reached `main` broken. A test
written for exactly this kind of drift did its job thirteen minutes after the commit. The interest of the
case is where the bug came from. The round of fixes created it, in that round's smallest part, the answer
to a nit.

This chapter is about reviewing work that arrives in rounds: where to aim the review after each round of
fixes, what to ask of an agent's tests, why each round gets a fresh reviewer, how to keep a change small
enough to review, and when to stop. A reconstruction of how a review of #1686's round 1 could have found
its bug comes near the end, and the exercises fetch both this pull request and Chapter 9's.

## Aim at the diff of the diffs

The first review of a change reads all of it. The second should not. After a round of fixes, most of the
change is what it was, and the risk sits in what the fixes changed. `CLAUDE.md` says so in its section on
reviewing your own fixes:

```text
# 📁 CLAUDE.md
After a round of fixes, aim the next review at the **diff of the diffs** —
what the fixes changed — rather than at the whole change again. That is where
the remaining risk is. And distinguish the two kinds of finding: a bug that
became *reachable* because a gate was removed is pre-existing and finite;
a bug the last fix *created* is the signal that the mechanism needs a pin.
```

In git terms, the diff of the diffs is the diff between the head the reviewer saw and the head that
answers the review. When the fixes are their own commit, it is that commit. For #1686's first round:

```bash
git diff --stat 74bc3904 a8e8db0e
```

```text
 backend/app/schemas/tracking.py                    | 37 ++++++----------------
 ...t_tracking_edge_timestamp_and_metadata_depth.py | 33 +++++++++++++++++++
 .../db/test_events_created_at_utc_normaliser.py    | 21 +++++++++---
 .../unit/schemas/test_event_metadata_depth.py      | 15 ++++++++-
 docs/_claims/docs/api/endpoints.md.toml            | 13 ++++++--
 docs/api/endpoints.md                              |  8 +++--
 6 files changed, 89 insertions(+), 38 deletions(-)
```

Six files, against the ten of the first head. A reviewer of the whole change would read the original
fix again; a reviewer of this diff reads one schema change, three test files and two documentation files,
and asks of each what it could break. The last line of the list is where the break was: eight changed lines
in a documentation page, written to answer three of the smaller findings. One of those edits, N4's
`Response` line, is the one that broke a test.

The second paragraph of that `CLAUDE.md` passage matters as much as the first. A bug that was already
there, and became reachable, is a finite list: fix it and move on. A bug that the last fix created means
the thing being changed has effects somewhere nobody is looking. That calls for a test that pins the
mechanism, not another round of reading. `CLAUDE.md` names three such mechanisms and the pins that hold
them:

```text
# 📁 CLAUDE.md
Alembic's revision graph, alembic's reflection scope and the dependency
closure all have non-local effects: a correct-looking local edit changes
behaviour somewhere no local test looks. Those properties are asserted
literally — the exact head set, the exact apply order, an autogenerate that
produces an **empty** migration against a freshly bootstrapped database — so a
silent reorder or a widened reflection fails loudly with a diff instead of
quietly proposing `op.drop_table` against a production schema. Do not relax
those tests to make a change pass; they are the contract.
```

The quoted sentences in `contract.py` are a mechanism of the same kind, a sentence in one directory that a
rule in another relies on, and `test_contract_citations.py` is its pin. That is why #1686's story ends
well. The fix created a bug, the pin fired, and the author wrote the mechanism into the description so the
next person editing that page runs the right suite first.

The last sentence of the block is for agents in particular. An agent whose change fails a pinning test
will, if allowed, edit the test until it passes. The pin exists to stop exactly that, and a review should
treat any edit to a pinning test as a finding until the description explains it.

## What to ask of the tests

Agent-written tests are the part of a change most likely to look right and prove nothing. The principal
engineer's brief has five questions for every one:

```text
# 📁 .claude/agents/principal-engineer.md
- **Could it fail?** If the guard has only ever been seen to pass, it is not a
  guard. Demand that the plan says which defect it will plant and watch fail.
- **Does it compare a copy against a copy?** A test that re-implements the
  thing it validates cannot detect divergence. One here re-implemented
  Starlette's rule inside the test and could never have fired.
- **Does it drive the real thing?** Gates that matched on a middleware class
  *name* passed when the middleware was swapped for one that would have failed
  every health check.
- **Is a tested gate already available?** If CI already answers the question,
  the plan should run that, not improvise a grep. Three improvised checks in
  one session here were each wrong in a different way.
- **Does it assert a wall-clock timing?** Three flaky gates came from that.
  Assert the deterministic thing instead.
```

The first question has a strong form: plant the defect yourself, as the reviewer, instead of trusting the
author's list of plants. The author plants what it thought of; the reviewer plants what it suspects.
Chapter 9's pull request is a case where three of the reviewer's plants passed every test of the first
version, and that was the finding its review round existed to produce. It is not a one-off. Pull request
[#1474](https://github.com/Controlvar/controlvar/pull/1474), merged the same day, introduces a table with
"these are the reviewer's surviving plants", each re-run against both test files after the fix and shown
failing.

The second question needs care, because `test_contract_citations.py` compares a copy against a copy on
purpose. The question is aimed at a test that copies the logic it claims to check, as the test that
re-implemented Starlette's rule did; such a test, in the brief's words, "cannot detect divergence". The
citation test checks a different property, that two texts stay the same. When agreement between copies
is the property you want, comparing them is the right test, and the useful review question becomes which
copy is the source.

The fourth question is the one #1686's lesson answers. When CI already has a tested suite for a question,
such as `backend/tests/unit/ground_truth` for "does the documentation still say what the contract quotes",
run it, and do not stand in for it with a `grep` of your own. The fifth needs no example: an assertion
about how long something took fails on a busy machine and passes on a quiet one, whatever the code does.

### Changes to the tests that were already there

Then check what the change did to the existing tests. Chapter 9's pull request edited three existing unit
tests and said "No assertion changed", a claim a reviewer can check in a minute, and the one most worth
checking: a change that weakens an old assertion to pass has hidden its own regression.

Pull request #1686's round 1 did change an old assertion, and it shows how to tell the two cases apart.
`backend/tests/unit/db/test_events_created_at_utc_normaliser.py` had expected an `OverflowError` at the
edge of the date range; it now expects the live normaliser to raise a `ValueError`. The description says
why. Turning that `OverflowError` into a `ValueError` is the fix itself, the change that lets every
handler answer 422. The test also covers the frozen copy of the normaliser inside an already shipped
migration, `1ab99332f0ba`, which still raises `OverflowError`, and the migration catches both. The
description adds: "The shipped revision is untouched." So the new expectation states the behaviour the fix
set out to produce, and the old behaviour is still pinned where it still exists. That is a legitimate
edit. The suspicious kind changes an expectation the fix never meant to move, or deletes one, and says
nothing.

## A fresh reviewer each round

Every round gets a new reviewer invocation (Chapter 4). That sounds wasteful, and it has two benefits. The
new reviewer has no stake in the last verdict, and it reads the conditions from the file, not from memory.
The engineering manager's brief makes the conditions explicit. It receives the principal engineer's review
and must "say for each whether it is met, or carried as one of yours", and it is told what an easy
approval costs:

```text
# 📁 .claude/agents/engineering-manager.md
You are not a rubber stamp. A plan you approve that then takes three review
rounds is your failure as much as the author's.
```

The author's side is just as short: meet the conditions, one by one, by name. Pull request #1686's
round-1 section has a bullet for each finding, led by "Blocking 1", "Blocking 2", "N1", "N2, N3" and "N4",
and in each a test, a run with its counts, or the edit made. A revision that answers each condition with
"noted" has met none of them, and a fresh reviewer, reading the old verdict by path, will say so.

The code reviewer, `reviewer`, gives its findings in a simpler shape: grouped by severity, then one
verdict.

```text
# 📁 .claude/agents/reviewer.md
**CRITICAL** — Must fix before merge (bugs, security holes, broken auth)
**WARNING** — Should fix (missing error handling, wrong patterns, no tests for important path)
**SUGGESTION** — Optional improvement (readability, naming, minor optimization)

End with a one-line verdict:
- `APPROVED` — No criticals or warnings
- `APPROVED WITH SUGGESTIONS` — Only suggestions remain
- `CHANGES REQUESTED` — One or more criticals or warnings
```

Severity tells the author what must be fixed. It does not tell the next reviewer where to look, and #1686
shows why the two differ: the lowest-severity finding produced the round's only new bug. Review a fix to a
suggestion as closely as a fix to a critical finding, because its author will have given it less thought.

## Keep each change small enough to review

Review rounds multiply with the size of the change. The engineering manager's brief calls this a
"measured, expensive failure mode": a change that bundles a stable part with an unstable part takes
several review rounds while the stable part waits. Its example is the same pull request `CLAUDE.md` cites,
#236, from the repository's private era (on the public tracker #236 is something else): a part that was
correct in round one shipped weeks later, because it was tied to the part that generated every finding.
The brief's instruction is three words, "**Default to splitting.**", and one question:

```text
# 📁 .claude/agents/engineering-manager.md
Ask: what is the smallest piece that closes a property on its own? Can any part
merge today?
```

An agent left alone tends to do the opposite. It notices a second problem while fixing the first, and
fixes both, and the review of the first now waits on the second. Ask for the split in the plan, under the
SPLIT heading (Chapter 3), and refuse a pull request that quietly grew. Chapter 9 shows the split working:
the scheduler fix in #1464 merged on its own, and the dashboard screen that depended on it followed a few
hours later.

## When to stop

Every review round finds something. Without a rule, rounds never end. `CLAUDE.md` has two, one for code
and one for plans. The merge bar:

```text
# 📁 CLAUDE.md
Merge when a review round produces no finding that would break a deployment or
lose data. Everything else becomes a tracked issue. Without a bar, review
rounds never end, because each one finds something.
```

Pull request #1686 merged after round 2 with four items still under NOT VERIFIED, among them "Nothing under
uvicorn or the core profile", with CI named as the first run of the core build. Those are open questions,
written down where a reviewer will see them. The bar asks something narrower: whether any finding of the
last round would break a deployment or lose data.

The cap, for plans:

```text
# 📁 CLAUDE.md
**Then meet the conditions rather than noting them**, and apply the cap: two
rejected revisions and you stop, work the uncontested parts, and write down
what is contested and why.
```

`CLAUDE.md` gives the reason where it sets the same cap for a question nobody is there to answer:
"Iterating against reviewers with nobody to break a tie is how the fix-round spiral starts, and that is the
failure this whole process exists to prevent." Two rejected revisions is where revising stops: the
uncontested parts go ahead, and what is contested is written down, with why.

## A round-1 review, reconstructed

The review of #1686's round 1 is not public; its findings appear only as the labels the pull request
answers. What follows is a reconstruction, not a record: the commands a reviewer aiming at the diff of
the diffs could have run on `a8e8db0e`, and what they print. The question for each changed file is the one
from Chapter 5: what reads this without naming it.

For the schema and the three test files, the readers are the test suites, and the pull request lists the
runs. The documentation is different: it is read by people, and in this repository also by tests. Start
from the changed line:

```bash
git diff 74bc3904 a8e8db0e -- docs/api/endpoints.md
```

```text
[...]
-- **Response**: 200 OK `{"success_count": int, "failure_count": int, "duplicate_count": int, "errors": [...]|null}`; 422 above 100 events, or one entry invalid (its `event_id` among them), and none is stored
+- **Response**: 200 OK `{"success_count": int, "failure_count": int, "duplicate_count": int, "errors": [...]|null}`; 422 above 100 events, or one entry invalid as a request (its `event_id` among them), and none is stored; an entry that is well formed but refused when its event is built (metadata nested too deep, an edge `timestamp`, a key not found) is reported in `errors` and the others are stored
```

The old sentence was edited in place, with words inserted into its middle. So search the whole tree for a
distinctive fragment of the old wording, and see who else holds it:

```bash
git grep -n -o -E 'one entry invalid[a-z ]*' a8e8db0e
```

```text
a8e8db0e:docs/_claims/docs/api/endpoints.md.toml:1921:one entry invalid as a request
a8e8db0e:docs/api/endpoints.md:1121:one entry invalid as a request
a8e8db0e:tests/ground_truth/ground_truth/contract.py:174:one entry invalid
```

The first two hits moved together: the claims file is the registry Chapter 2 describes, and round 1
updated it with the page. The third did not move, and it is a Python file under `tests/`. Its docstring
says what it does with the sentence:

```text
# 📁 tests/ground_truth/ground_truth/contract.py
Each rule below is stated once, in the function that applies it, and its
docstring quotes the sentence of the docs that makes it the contract, as a
paragraph starting ``Contract (<page>):``. A unit test holds every quoted
sentence to its page.
```

That is the finding the Unit Tests check produced thirteen minutes after the commit, reached with two
commands and a file opened at a line number. The search covered the whole tree only because the question
was what reads this line, and that is the habit worth keeping. A review that asks only whether the new
sentence is correct would have passed it, because it is.

## Try it

Each step needs only a clone of Controlvar, git and a shell; the fetches read public pull request heads
from GitHub. The output is what each command printed at `09d815ea` on macOS. Steps 6 and 7 need your AI
tool or your own judgement and have no output.

1. Fetch the pull request from Chapter 9 and look at its heads. Public pull requests can be fetched by
   number:

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

   At the top is a merge of `main` into the branch, then the commit from `main` it brought in, then the
   branch's own two commits: `ac6e0cf9` (the round-1 fixes) and `766184c9` (the first head).

2. Look at that pull request's diff of the diffs:

   ```bash
   git diff --stat 766184c9 ac6e0cf9
   git diff --quiet 766184c9 ac6e0cf9 -- backend/app; echo "exit $?"
   ```

   ```text
    .../services/test_scheduler_draft_validation.py    | 85 ++++++++++++++++++++--
    1 file changed, 79 insertions(+), 6 deletions(-)
   exit 0
   ```

   One file, the test file. `exit 0` from `git diff --quiet` means no difference under `backend/app`:
   round 1 changed no application code at all. The code was right and the tests were unfinished; Chapter
   9 says what they were missing.

3. Fetch #1686 and list its own commits, following the branch's first parents so the merged-in commits
   from `main` stay out of the list:

   ```bash
   git fetch origin pull/1686/head:pr-1686
   git log --oneline --first-parent -5 pr-1686
   ```

   ```text
   8a875381 Merge origin/main into fix/tracking-edge-timestamp-and-metadata-depth
   1ac8b5c1 Merge origin/main into fix/tracking-edge-timestamp-and-metadata-depth
   956d825f docs(api): restore the /batch 422 sentence the ground-truth contract quotes (#1686)
   a8e8db0e fix(api): /events answers 422 for a JSON string nested past the recursion limit (#1684, #1685)
   74bc3904 fix(api): tracking answers 422, not 500, for an edge timestamp or over-deep metadata (#1684, #1685)
   ```

4. Compare the two rounds' sizes:

   ```bash
   git diff --stat a8e8db0e 956d825f
   git diff --quiet a8e8db0e 956d825f -- backend; echo "exit $?"
   ```

   ```text
    docs/_claims/docs/api/endpoints.md.toml | 7 ++++++-
    docs/api/endpoints.md                   | 2 +-
    2 files changed, 7 insertions(+), 2 deletions(-)
   exit 0
   ```

   Round 2 touched two documentation files and nothing under `backend/`. Round 1 touched six files, among
   them the API schema. The smaller diff is the one that fixed the larger one's only new bug.

5. Run the reconstructed review's search at both heads:

   ```bash
   git grep -n -o -E 'one entry invalid[a-z ]*' a8e8db0e
   git grep -n -o -E 'one entry invalid[a-z ]*' 956d825f
   ```

   ```text
   a8e8db0e:docs/_claims/docs/api/endpoints.md.toml:1921:one entry invalid as a request
   a8e8db0e:docs/api/endpoints.md:1121:one entry invalid as a request
   a8e8db0e:tests/ground_truth/ground_truth/contract.py:174:one entry invalid
   956d825f:docs/_claims/docs/api/endpoints.md.toml:1921:one entry invalid
   956d825f:docs/api/endpoints.md:1121:one entry invalid
   956d825f:tests/ground_truth/ground_truth/contract.py:174:one entry invalid
   ```

   At the round-2 head all three copies agree again.

6. Make a change of your own in two commits: a fix, then a fix to the fix. Ask your AI tool to review it
   twice, in two fresh sessions: once given the whole diff, once given only the second commit. Compare
   where the findings land, and whether either review asked what reads the lines that changed.

7. Write your project's merge bar in one sentence. If you cannot, your review rounds have no end.

## Summary

A round of fixes is a new change, and its bugs are the ones nobody has reviewed yet. In #1686 a reworded
sentence, written to answer the least serious finding, broke a test in another directory, and a pin
written for that kind of drift caught it. So aim each review after the first at what the fixes changed,
ask of every changed line what reads it, plant your own defects in an agent's tests, and check every edit
to an existing assertion against what the fix meant to change. A fresh reviewer each round reads the
conditions from the file; a merge bar and a cap on rounds say when to stop. Chapter 8 turns to the rails
that stay in place whoever is reviewing.

## Further reading

- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md): the
  habits underneath the process, including the merge bar.
- [Before You Change Controlvar, Chapter 7](../before-you-change-controlvar/chapter-7-changes-at-a-distance.md):
  the changes whose effects land far from where they are made, and the tests that pin them.
- [Production Engineering, Hour 11](../production-engineering/part-4-safe-change.md): a worked review of a
  change that crosses the API, storage, dashboard and SDKs.
- In this book, [Chapter 5](./chapter-5-checking-claims.md#the-search-came-back-empty) on readers that a
  search for a name does not find, and [Chapter 9](./chapter-9-a-worked-example.md) for a review round
  that found what the first version's tests had missed.

---

_Previous: [Chapter 6 — Tamper-testing](./chapter-6-tamper-testing.md). Next: [Chapter 8 — Safety
rails](./chapter-8-safety-rails.md)._

# Chapter 7 — Reviewing agent work

**Outcome:** you know where to aim a review after each round of fixes, what to ask of an agent's tests,
how to keep review rounds from spiralling, and when to stop.

## Aim at the diff of the diffs

The first review of a change reads all of it. The second should not. After a round of fixes, most of the
change is what it was; the risk is in what the fixes changed. `CLAUDE.md`:

```text
# 📁 CLAUDE.md
After a round of fixes, aim the next review at the **diff of the diffs** —
what the fixes changed — rather than at the whole change again. That is where
the remaining risk is. And distinguish the two kinds of finding: a bug that
became *reachable* because a gate was removed is pre-existing and finite;
a bug the last fix *created* is the signal that the mechanism needs a pin.
```

In git terms, the diff of the diffs is the diff between the head the reviewer saw and the head that
answers the review. When the fixes are their own commit, it is that commit. The second paragraph matters
just as much. A bug that was already there, and became reachable, is a finite list: fix it and move on. A
bug that the last fix created means the thing being changed has effects nobody is looking at. That calls
for a test that pins the mechanism, not another round of reading:

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

The last sentence is for agents in particular. An agent whose change fails a pinning test will, if
allowed, edit the test until it passes. The pin exists to stop exactly that.

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

The first question has a strong form: plant the defect yourself, as the reviewer, rather than trusting
the author's list of plants. The author plants what it thought of. The reviewer plants what it suspects.
Chapter 9's pull request is a case where three of the reviewer's plants passed every test of the first
version. That is the finding the round was for.

And check what the change did to the tests that already existed. Chapter 9's change edited three
existing unit tests, and its description says so and adds "No assertion changed." That sentence is a
claim a reviewer can check in a minute, and the one most worth checking: a change that weakens an old
assertion to pass has hidden its own regression.

## A fresh reviewer each round

Every round gets a new reviewer invocation (Chapter 4). That sounds wasteful, and it has two benefits. The
new reviewer has no stake in the last verdict, and it reads the conditions from the file, not from
memory. The engineering manager's brief makes the conditions explicit. It receives the principal
engineer's review and must "say for each whether it is met, or carried as one of yours", and it is told
what an easy approval costs:

```text
# 📁 .claude/agents/engineering-manager.md
You are not a rubber stamp. A plan you approve that then takes three review
rounds is your failure as much as the author's.
```

And the author's side of it is one line in `CLAUDE.md`: "**Then meet the conditions rather than noting
them**". A revision that lists each condition with "noted" has met none of them.

The code reviewer, `reviewer`, uses a simpler shape: findings by severity, then one verdict.

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

## Keep each change small enough to review

Review rounds multiply with the size of the change. The engineering manager's brief calls this a
"measured, expensive failure mode": a change that bundles a stable part with an unstable part takes
several review rounds while the stable part waits. Its example is the same pull request `CLAUDE.md` cites
(#236, from the repository's private era): a part that was correct in round one shipped weeks later,
because it was tied to the part that generated every finding. The brief's instruction is two words,
"**Default to splitting.**", and one question:

```text
# 📁 .claude/agents/engineering-manager.md
Ask: what is the smallest piece that closes a property on its own? Can any part
merge today?
```

An agent left alone tends to do the opposite. It notices a second problem while fixing the first, and
fixes both. Ask for the split in the plan (the SPLIT heading, Chapter 3), and refuse a pull request that
quietly grew.

## When to stop

Every review round finds something. Without a rule, rounds never end. `CLAUDE.md` has two.

The merge bar:

```text
# 📁 CLAUDE.md
Merge when a review round produces no finding that would break a deployment or
lose data. Everything else becomes a tracked issue. Without a bar, review
rounds never end, because each one finds something.
```

The cap, for plans:

```text
# 📁 CLAUDE.md
**Then meet the conditions rather than noting them**, and apply the cap: two
rejected revisions and you stop, work the uncontested parts, and write down
what is contested and why.
```

The cap matters more with agents than with people. A person who disagrees with a reviewer for the third
time goes and asks someone. An agent revises again. Two rejected revisions is the point at which the
disagreement goes to a person, in writing.

## Try it

1. Fetch the pull request from Chapter 9 and look at its two heads. Public pull requests can be fetched
   by number:

   ```bash
   git fetch origin pull/1464/head:pr-1464
   git log --oneline -4 pr-1464
   ```

   At the top is a merge of `main` into the branch, then a commit from `main`, then the branch's own two
   commits: `ac6e0cf9` (the round-1 fixes) and `766184c9` (the first head).

2. Look at the diff of the diffs:

   ```bash
   git diff --stat 766184c9 ac6e0cf9
   git diff --quiet 766184c9 ac6e0cf9 -- backend/app; echo "exit $?"
   ```

   One file changed, the test file: 79 lines added, 6 removed. The second command prints `exit 0`: round 1
   changed no application code at all. The code was right; the tests were not finished. Chapter 9 says
   what they were missing.

3. Make a change of your own in two commits: a fix, then a fix to the fix. Ask your AI tool to review it
   twice, in two fresh sessions: once given the whole diff, once given only the second commit. Compare
   where the findings land.

4. Write your project's merge bar in one sentence. If you cannot, your review rounds have no end.

## Go deeper

- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md): the
  habits underneath the process, including the merge bar.
- [Before You Change Controlvar, Chapter 7](../before-you-change-controlvar/chapter-7-changes-at-a-distance.md):
  the changes whose effects land far from where they are made, and the tests that pin them.
- [Production Engineering, Hour 11](../production-engineering/part-4-safe-change.md): a worked review of a
  change that crosses the API, storage, dashboard and SDKs.

---

_Previous: [Chapter 6 — Tamper-testing](./chapter-6-tamper-testing.md). Next: [Chapter 8 — Safety
rails](./chapter-8-safety-rails.md)._

# Chapter 6 — Tamper-testing

At 22:44 UTC on 29 September 2026, issue [#470](https://github.com/Controlvar/controlvar/issues/470) was
opened against Controlvar under a modest title: "two results-access tests run as ADMIN because of client
fixture order". The tests were `test_developer_can_access_results` and `test_analyst_can_access_results`
in `backend/tests/integration/api/test_bayesian_results_api.py`, and their names promised that a developer
and an analyst could read an experiment's results. The issue's author had checked that promise in the only
way that settles it: made `GET /api/v1/results/{id}` refuse a DEVELOPER, ran the tests, and watched them
pass, then did the same for an ANALYST. The verdict fits in one line: "The route itself is fine; these
tests just don't check what they say."

The cause sat in a helper behind the integration suite's role-client fixtures.
`make_client_for_user`, in `backend/tests/integration/conftest.py`, signed a test client in by writing
`app.dependency_overrides`, and that dictionary belongs to the whole application. Every client the helper
had handed out then acted as the user of its most recent call. A test that asked for both the admin's
client and the developer's ran every request as whichever fixture pytest happened to set up last, and here
that was the admin, a superuser, who passes every permission check there is. Controlvar decides access by
role, and these were the tests meant to hold two roles' access in place. A change that took results away
from developers would have left them green, however many times they ran.

Pull request [#474](https://github.com/Controlvar/controlvar/pull/474) was opened at 23:27 and merged at
00:47 the next morning. Its Verification section begins by repeating the issue's experiment against two
versions of the test. With the route made to refuse a developer, the fixed test failed with
`{"detail":"TAMPER-470"}`, the text of the planted refusal, and the same test taken from `main` passed. The
pair is the whole argument: the old test cannot see the defect and the new one can. The pull request also
added a static scan that refuses any test asking for two signed-in clients, and the scan found a third
broken test that the issue had not listed, `test_every_role_that_may_list_still_can`, which "requested all
four role clients, so all four requests ran as VIEWER".

This chapter is about that move: breaking the thing a check guards, on purpose, to see whether the check
notices. It covers why a check passes for the wrong reason, how to write a tamper down and choose one the
check can see, where to run it, what to conclude when it does not fire, and how a gate can tamper-test
itself on every run. A worked example takes an agent's tamper evidence apart, and the exercises plant
defects in your own clone and remove them again.

## Running a check again proves nothing

When an agent writes a fix and its test together, both come from the same understanding of the problem.
If that understanding is wrong, the test is wrong in the same way, and it passes. Running it again repeats
the same reasoning with the same result. The only independent signal is to break the code on purpose and
watch the test fail. `CLAUDE.md` calls the alternative the most common defect in the repository, and gives
a list from one review cycle:

```text
# 📁 CLAUDE.md
The most frequent defect in this repository is not broken code — it is a check
that passes for the wrong reason. Found in one review cycle: `find -name
'workspaces*'` matching the chunk *directory* instead of the page it guarded;
a "core image ships no module packages" assertion made of six hand-typed names
that already missed two; a `profile: core` CI input that selected nothing,
so the check named `(core)` tested the full profile; a `make` recipe guard
whose `exit 0` ended only its own line's shell; `--steps <typo>` skipping every
step and exiting `OK`; a CI gate that called `load_modules(force=True)` and
thereby erased the failure it existed to detect; a route allow-list keyed on
prefixes, so a new anonymous route under an existing prefix passed; and a CDK
test that stubbed `App.synth` to a no-op, so the app was never synthesised.

So: **when you add or change a gate, break the thing it guards and watch it
fail.** Paste that evidence in the report. A gate you have only ever seen pass
is not a gate. This applies to assertions, CI steps, shell guards, lint scopes
and tests alike.
```

The eight cases fall into three shapes, and it is worth learning the shapes, since the next case will not
be on the list. The first is a check aimed at a stand-in for the thing it guards. The `find` matched a
directory whose name looked like the page's; the route allow-list matched on a prefix where it meant a
route. Issue #470 has this shape: the fixture's name said developer, and the request was the admin's.

The second shape is a check with nothing in it. Six hand-typed names can only ever find those six. A
`profile: core` input that selected nothing ran the full profile under a job named `(core)`, and a typo in
`--steps` skipped every step and still printed `OK`. A check over an empty set passes, because there is
nothing for it to fail on.

The third shape is a check that removes its own failure. An `exit 0` inside a `make` recipe ends only the
shell that runs its own line, so the recipe carries on. A gate that called `load_modules(force=True)`
erased, in `CLAUDE.md`'s words, "the failure it existed to detect", and a CDK test that stubs `App.synth`
never synthesises the app it claims to test. Every case in the list passed, and none of them would have
been caught by running it again.

## Writing the tamper down

The QA engineer's role owns this. Its brief asks, for each property in a plan, for the defect that will be
planted to show the property's check works:

```text
# 📁 .claude/agents/qa-engineer.md
**A tamper for every gate.** The most frequent defect here is not broken code
but a check that passes for the wrong reason (CLAUDE.md, "Every gate must be
tamper-tested"). For each property: the gate, and the specific defect you will
plant to watch it fail. "Run the tests" is not a verification. A tamper that
plants nothing the gate could see is not one either.
```

And it says what shape the answer takes:

```text
# 📁 .claude/agents/qa-engineer.md
    VERIFICATION  table: property | gate | where it runs | planted defect | expected failure text
```

The last column is the one that makes a tamper checkable. "The test failed" could mean it failed for any
reason, including a typo in the plant. An expected failure text, such as #474's `TAMPER-470`, says which
assertion should fire and with what, and a reader can compare it with the run.

You will find such tables throughout Controlvar's pull requests. Pull request
[#722](https://github.com/Controlvar/controlvar/pull/722), which made a paused rollout schedule stay paused
while a tick or a manual advance was already under way, has a section titled "Planted defects, each shown
red, then restored". Each row names a plant and the failure it produced, down to the diffs (`50 != 10`,
`COMPLETED != PAUSED`), and the last row puts back `main`'s versions of the rollout scheduler and the
rollout service, which turns all five of its new interleave tests red. That row is the one to look for in
any table. It runs the new tests against the old code, so it proves the tests detect the bug they were
written for.
`CLAUDE.md` makes the same demand of every bug fix, as a `@pytest.mark.regression` test "that fails on the
old code".

Two rules make a tamper count. It must plant something the check can see, which the next section takes
up. And it must be undone, and the undoing checked: Chapter 9's pull request heads its plants "each run
where the gate runs, against my Postgres, then reverted; the tree was clean after each". Pull request #474
ends its list the same way: "All plants were removed."

## Choosing a plant the check can see

A plant tests a check only if it breaks the property the check claims to hold, inside what the check
actually reads. For #470 the property was "a developer can read results", so the plant made the route
refuse a developer. A plant that broke something else, such as the results calculation, would have failed
the old test and the new one alike and said nothing about which user was making the request. The QA
brief's last sentence covers this case: "A tamper that plants nothing the gate could see is not one either."

The other half of a good plant is a control, a case the check must accept. Without one, a check that
rejects everything looks the same as a check that works. The tamper table in #1053, the pull request that
opens Chapter 4, starts with a row whose plant is "none (control)" and whose result is a pass with exit 0,
and only then lists the plants that should fail. Pull request #474 ran its scan over `main`'s version of
the file, where the scan had to fire, and over the fixed tree, where it had to stay quiet.

For a check over many files, plant one offender of each shape the check claims to catch. The
[guide to this library's own gates](../tools/README.md) gives the same instruction: "plant one defect per
rule shape, run the gate, see it fail; remove it, see it pass." One plant shows that the check can fire.
One plant per shape shows that each of its rules can.

## A check you generate can die on the way

Agents write checks into files: a `grep` in a slash command, a pattern in a workflow, a command in an
instruction file. Each layer of quoting between the agent and the place the check finally runs can change
it, and nothing announces the change. `CLAUDE.md` records one case:

```text
# 📁 CLAUDE.md
That includes a check you *generate* rather than type. A grep written into four
instruction files through a Python f-string arrived double-escaped (`\\s` for
`\s`) and matched nothing — it would have reported "clean" for ever. Every
layer of quoting between you and the thing that runs is a place for the check
to die silently, so run the emitted command against a planted offender and
watch it fire.
```

The commit that brought the check in, `34a7eaba` at 01:31 UTC on 21 September 2026, tells the story in its
author's own words: "I wrote that grep in double-escaped the first time, so it matched nothing -- a check
that would have reported clean for ever. Caught by running each of the four copies against a planted
offender." The mechanism is small. In a regular expression `\s` means whitespace, and `\\s` means a
literal backslash followed by the letter s. Python source needs `\\` to produce one backslash, so text
that passes through one more layer of escaping than its author planned arrives with two. An import line
never starts with a backslash, so the pattern can never match one.

The check in question is the one in `/fix-imports` (Chapter 2). The worked example later in this chapter
runs both versions of it against the same plants.

## Tamper where the gate runs

A tamper run on your laptop proves the gate works on your laptop. `CLAUDE.md` gives three cases from one
pull request:

```text
# 📁 CLAUDE.md
"Verify in the environment the code runs in" applies to the *gate and its
tests*, and this is where it keeps biting. Three times in one PR:

- a gate test made its fixture with `git worktree`, which fails in
  `core_build.sh`'s deliberately non-repository copy;
- a licence gate covering six npm closures was **vacuous in CI**, because the
  job installs only `frontend` — it "found" something locally purely because
  this laptop had the other five installed;
- the test written to fix that took the same shortcut and passed locally,
  failed in CI.

Cheap moves that would have caught all three: `mv frontend/node_modules` aside
and re-run; `git archive HEAD` into a plain directory and run there; build the
image and inspect it rather than reasoning about the `COPY`.
```

The middle case is the dangerous one for agents. The gate "found" something locally, so the tamper seemed
to work, and it worked only because of what happened to be installed on that machine. Chapter 5 covered
the general rule: a run has an address. A tamper is a run like any other, and its address matters as much.

Pull request #474 took the cheap move. Its new scan "also passed (3/3) in a copy of the tree with no
`modules/` and no `.git`", which is how `scripts/core_build.sh` runs the suite. And it named the move it did
not take: "The core-profile run used a plain copy of the tree, not `scripts/core_build.sh`."

Sometimes the only place a gate runs is CI. Pull request #1053 had changed which directories the Unit
Tests job checks for skipped tests, and the honest tamper for that is a planted skip in CI. Its author
opened a throwaway draft pull request, #1054, carrying the branch plus one commit that added
`pytest.skip("planted skip (A1 tamper)")` to one test. The job went red at the skip-check step and nowhere
else, while the real pull request's run stayed green. The draft was closed and its branch deleted. Putting
the plant on its own pull request kept it away from the real one, whose checks a reviewer reads as the
evidence.

## When a tamper does not fire

Sometimes you plant a defect and the gate stays green. Before you decide the gate is broken, look at what
you planted:

```text
# 📁 CLAUDE.md
**And when a tamper does not fire, suspect the probe before the gate.** A
gate that looks broken is usually a bad probe — the example-key case above cost
a round-trip of believing gitleaks was rule-less again.
```

The "example-key case" is in the same section of `CLAUDE.md`: the `gitleaks` secret scanner deliberately
ignores the sample access key that AWS publishes in its own documentation. A probe made of that key passes
a scanner that is working perfectly, because the scanner was built to let exactly that string through. The
general form is a probe the gate is designed to accept: an example value, a pattern on an allow-list, a
file in a directory the gate excludes. Read the gate's exclusions before you read its result.

The other outcome is real: the gate cannot see your plant, and the right response is to say so. Pull
request #474's "Not verified" section lists three shapes its scan does not see, among them fixtures
requested through `request.getfixturevalue`. Another shape it missed, a fixture that sets the override
itself without the helper, was filed as issue [#476](https://github.com/Controlvar/controlvar/issues/476)
sixteen minutes after #474 was opened, and closed on 2 October. The scan's docstring now cites both issues.
A tamper that cannot fire, written down, is the next issue. Left out of the report, it is a hole that
looks covered.

## Gates that test themselves

The leak guard (Chapter 8) does not wait for someone to remember to tamper-test it. Its workflow plants an
offender on every run:

```yaml
# 📁 .github/workflows/leak-guard.yml
      - name: Prove the guard still fires
        # A gate whose rules have silently stopped matching reports "clean"
        # for ever and looks identical to a healthy one. So plant an offender
        # on every run and require the guard to reject it. This has caught a
        # double-escaped pattern in this repository before.
```

The step builds a scratch repository, writes a fake AWS account id into it, assembled from two halves so
that the workflow file itself contains no such id, and then fails the job if the guard accepts it:

```yaml
# 📁 .github/workflows/leak-guard.yml
          if python3 scripts/leak_guard.py --staged >/dev/null 2>&1; then
            echo "::error::leak-guard accepted a planted AWS account id -- the guard is not working"
            exit 1
          fi
```

Notice which way round the condition is. The step fails when the guard succeeds. A tamper written into a
workflow has to invert the usual sense of the exit status, and an `if` that reads naturally ("if the guard
passes, carry on") would be the bug.

The scan from #474 protects itself against the second shape, the check with nothing in it:

```python
# 📁 backend/tests/smoke/test_role_client_fixtures.py
def test_the_scan_is_not_vacuous():
    """The scan sees the tests that use these fixtures, so a wrong root or a
    broken parse cannot turn the gate into a pass."""
    users = [
        func.name
        for _, funcs in _modules()
        for func in funcs
        if func.name.startswith("test") and _params(func) & _ALL
    ]
    assert len(users) > 100, len(users)
```

The floor of 100 is not a measurement of anything about roles. It is there so that a scan pointed at the
wrong directory, or one whose parser has quietly stopped matching, fails loudly instead of reporting that
no test misuses the fixtures. The pull request adds, in one line: "There is no allow-list." A check whose
rules can rot, or whose input can vanish, should carry its own tamper.

## A new gate's first failures are evidence

The opposite mistake is to treat a new gate's first red run as the gate's fault and loosen it until it
passes. `CLAUDE.md` describes a sweep whose first run failed two of its checks:

```text
# 📁 CLAUDE.md
The first run of the export sweep failed two of its five checks: ten forbidden
claims and 197 dangling references. None of the 197 was a dead link -- 205 of
them were the *word* "project", matched by a directory entry with no trailing
slash, and ten were `.env.dev`, a file the docs legitimately tell you to
create. Eight of the ten claim hits were absolute paths in the `.claude/agents`
and `.claude/commands` files that had just been *carved out* of the exclusion
list to be kept; the other two were the script matching its own pattern.
```

Its rules are to read the evidence before fixing anything, fix the gate's precision first, report what is
left, and treat every exception you carve out as a new obligation:

```text
# 📁 CLAUDE.md
- **An exception you carve into an exclusion list is a new obligation.** The
  moment `.claude/agents` was kept, its files had to meet the same bar as the
  rest of the tree, and they did not. Re-run every sweep over whatever you just
  decided to keep.
```

Look at which files failed: the agents' own instruction files. Instructions are content, and they leak and
drift like any other content, as Chapter 4's stale briefs showed.

## What a tamper costs

A tamper costs at least one more run of the check. The CI tamper in #1053 took a second pull request and a
test run of twelve and a half minutes in the Unit Tests job. Every plant is also a change to a working tree
that must be put back, and a plant left behind is a defect you introduced yourself. That is why #474, #722
and Chapter 9's pull request each say their plants were removed, restored or reverted.

It is also only safe where a broken guard cannot reach anything real. A tamper that disables a check on
outbound calls, run in a shell with real credentials, can make the very call the check was there to stop.
Chapter 8 describes the rail that makes Controlvar's tampers safe: no test can reach real AWS, whatever the
test does.

So match the effort to what the check is protecting. A check that guards a role's access, a secret or a
schema deserves a plant per shape and a run where it runs. A check whose failure would cost nothing, such
as one that only formats output, needs none. Between those, the cheapest useful tamper is the one in this
chapter's worked example: one plant the check must catch, one it must accept, both run in a minute.

## A tamper report, checked

Here is the verification section an agent might hand back after being asked to add the import check to
`.claude/commands/fix-imports.md`. It is illustrative: no agent wrote it, and the check in it is the
double-escaped version from `34a7eaba`'s story. Every path in it exists at `09d815ea`.

```text
Updated the check in .claude/commands/fix-imports.md:
    grep -rnE '^\\s*(from|import) app\.' backend/ modules/ --include='*.py'

How it was verified
1. On the tree: no output, exit 1. No app-rooted imports.
2. Tamper: planted `from .base import Base` in a new file in backend/app/models/,
   planted_imports.py. No output, exit 1, as expected for a relative import.
   Plant removed; git status clean.
```

It looks careful. It has a run, a plant, a removal and a clean tree. Start with the first line: a check
seen only passing, which on its own proves nothing about whether the check can fail.

The second line is a tamper, but read what it planted. `CLAUDE.md` says that a relative `from .base import
Base` inside `backend/app/models/` "is NOT" the defect, so the check is meant to ignore it. A plant the
check must accept is a control. It is a useful row in a table, and it cannot show that the check fires.
Create a file named `planted_imports.py` in `backend/app/models/` with that single line, and run the real
check and the report's version side by side:

```bash
grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'; echo "exit $?"
grep -rnE '^\\s*(from|import) app\.' backend/ modules/ --include='*.py'; echo "exit $?"
```

```text
exit 1
exit 1
```

The two are indistinguishable on this plant, and on a clean tree. Now add the line the check exists for, as
the second line of the same file, `from app.models.metrics.metric import RawMetric`, and run the same two
commands:

```text
backend/app/models/planted_imports.py:2:from app.models.metrics.metric import RawMetric
exit 0
exit 1
```

The single-escaped pattern, the one `.claude/commands/fix-imports.md` carries at `09d815ea`, reports the
planted line and exits 0, and it says nothing about line 1, the relative import, as it should. The
report's pattern stays silent on both, and would have said "clean" for ever. The report to send back keeps
its control row and adds the plant that should fire, with the expected output written before the run:
`planted_imports.py:2` and exit 0.

Then do what the report claimed to do, and check it:

```bash
rm backend/app/models/planted_imports.py
git status --short; echo "exit $?"
```

```text
exit 0
```

No output from `git status --short` means the tree is as you found it.

## Try it

Each step needs only a clone of Controlvar, git, Python 3 and a shell, and leaves nothing behind. The
output is what each command printed at `09d815ea` on macOS. Steps 3 to 5 need your AI tool or the test
environment and were not run for this book.

1. Repeat the worked example. In your editor, create `planted_imports.py` in `backend/app/models/` with two
   lines, `from .base import Base` and `from app.models.metrics.metric import RawMetric`, then:

   ```bash
   grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'; echo "exit $?"
   grep -rnE '^\\s*(from|import) app\.' backend/ modules/ --include='*.py'; echo "exit $?"
   rm backend/app/models/planted_imports.py
   git status --short
   ```

   ```text
   backend/app/models/planted_imports.py:2:from app.models.metrics.metric import RawMetric
   exit 0
   exit 1
   ```

   The first `grep` finds the planted line; the second, with `\\s` for `\s`, finds nothing.
   `git status --short` prints nothing once the plant is gone.

2. Tamper-test the leak guard without planting anything in your clone. Its `--stdin` mode scans text
   instead of files, so you can feed it an account id assembled from two halves, then a control, then let
   it scan the tree:

   ```bash
   printf 'role = "arn:aws:iam::%s%s:role/x"\n' 9876 54321098 | python3 scripts/leak_guard.py --stdin; echo "exit $?"
   printf 'role = "arn:aws:iam::ACCOUNT:role/x"\n' | python3 scripts/leak_guard.py --stdin; echo "exit $?"
   python3 scripts/leak_guard.py; echo "exit $?"
   ```

   ```text
   leak-guard: 1 finding(s)

     pull request title/body
         aws-account-id: [...]
         an AWS account id. Use a documented placeholder such as 123456789012.

   This repository is public. A push is publication -- there is no export step
   left to strip these out afterwards. Fix them here.
   exit 1
   leak-guard: clean (37 bytes of pull request text)
   exit 0
   leak-guard: clean (3685 of every tracked file)
   exit 0
   ```

   The finding's value is cut from this listing, because the guard that checks this library would refuse
   it too. The pipe here is harmless: the exit status you read is the guard's own, the last command in the
   pipe. The guard accepts 123456789012, the placeholder its message recommends, so a plant has to use
   some other number.

3. If you have the test environment from
   [Before You Change Controlvar, Chapter 2](../before-you-change-controlvar/chapter-2-run-it.md), plant a
   test that asks for both `admin_client` and `developer_client` in a new file under
   `backend/tests/integration/api/`, and run
   `python -m pytest backend/tests/smoke/test_role_client_fixtures.py -p no:cov -q`. The scan should
   name your test. Delete the file and run it again.

4. Ask your AI tool to write a small test for any pure function in `backend/app/core/`. Then ask it to
   plant the defect the test guards, run the test, show the failure, and revert. If it cannot make its own
   test fail, the test was decoration.

5. Ask your tool to do the same for a check it did not write, such as the `grep` in
   `.claude/commands/fix-imports.md`. Watch whether its first plant is one the check can see, or a control
   like the worked example's relative import.

## Summary

A check that has only ever passed has told you nothing about whether it can fail. Issue #470's tests passed
because they asked the admin, and only a route broken on purpose showed it. Tamper-testing is that
experiment made routine: for each check, a plant it must catch and a control it must accept, each with
the failure text you expect, run where the check really runs, and undone afterwards. When a plant does not
fire, suspect the probe first, and when the check truly cannot see it, write that down. Chapter 7 turns
from the checks to the people and agents reviewing a change, and where they should look after each round
of fixes.

## Further reading

- [Before You Change Controlvar, Chapter 4](../before-you-change-controlvar/chapter-4-tests-and-gates.md):
  the rule for gates, and a planted documentation edit that the claims check catches.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): CI, the required checks
  and tamper-tested gates.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md#hour-12--a-release-review-from-evidence-to-decision-the-test-that-could-not-fail):
  the test that could not fail.
- In this book, [Chapter 5](./chapter-5-checking-claims.md#an-execution-has-an-address) on why a run has
  an address, and [Chapter 8](./chapter-8-safety-rails.md) on the rails that make breaking things safe.

---

_Previous: [Chapter 5 — Checking an agent's claims](./chapter-5-checking-claims.md). Next: [Chapter 7 —
Reviewing agent work](./chapter-7-reviewing-agent-work.md)._

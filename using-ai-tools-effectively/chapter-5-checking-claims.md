# Chapter 5 — Checking an agent's claims

At 00:39 UTC on 25 September 2026, pull request [#55](https://github.com/Controlvar/controlvar/pull/55)
was opened against Controlvar. An agent wrote it: the description ends with the Claude Code footer, and
each of its commits names a Claude model as co-author. Development had just moved into the public
repository, so every push was now publication, and this change added the check that would stand between
a push and the world: `scripts/leak_guard.py`, a scanner for account ids, home-directory paths and other
things that must never be published. The description said every rule had been planted against and watched
to fire, and it pasted the evidence:

```text
Tamper evidence -- every rule planted against and watched to fire, because a
gate that has only ever been seen to pass is not a gate:

    20 passed in 2.77s   backend/tests/unit/scripts/test_leak_guard.py
```

At 00:53 the `core-build` check failed. The fix commit, `1b59251b`, quotes its log:

```text
    FAILED backend/tests/unit/scripts/test_leak_guard.py::
           test_the_repository_itself_is_clean
      AssertionError: leak-guard: git rev-parse --show-toplevel failed:
      fatal: not a git repository (or any of the parent directories): .git

    1 failed, 3555 passed, 82 skipped in 320.67s
    core-build: FAILED at step unit (exit 1) after 340s
```

On the same commit, the `Unit Tests` check had run the same file and passed. The guard itself was fine. The
test asks it to scan "the real tree", the guard lists the tree with `git ls-files`, and
`scripts/core_build.sh` runs the suite in a copy of the tree that has no `.git` directory, deliberately, to
prove that the core profile stands alone. "20 passed" was a true sentence about one directory on one
machine.

The stakes were higher than one red check. The easy way to turn `core-build` green was to skip the test
whenever the guard failed, and that would have made it a test that can never fail, guarding the one script
the project now relied on to keep its public history clean. The fix, committed at 00:54, skips only when
there is no `.git` to scan. The pull request merged at 01:08, and at 01:31 the next one,
[#56](https://github.com/Controlvar/controlvar/pull/56), merged with the lesson written into `CLAUDE.md`:

```text
# 📁 CLAUDE.md
`scripts/core_build.sh` copies the tree into a directory with **no `.git`**, and
a test that shells out to git passes everywhere except there. That is what
broke `core-build` on the pull request that introduced the leak guard.
```

A claim that something passed stands on four things: the command, the place it ran, what it printed and
its exit status. A report usually shows one or two of them. This chapter covers four ways a check can
really run and still mislead you: it ran in the wrong place, a flag did not do what its name says, a search
came back empty, or the exit status was hidden. For each you will see why it happens, the case from
Controlvar's history, how to spot it in a report, and when checking for it costs more than it saves. Then
we check one illustrative report line by line, and the exercises reproduce each failure on your own clone.

## An execution has an address

The principal engineer's brief (Chapter 3) states the rule this chapter refines:

```text
# 📁 .claude/agents/principal-engineer.md
So: **do not accept a claim about behaviour without an execution.** Open the
file. Run the command. Check whether the hits are code or comments. If a claim
cannot be checked cheaply, say so and mark it an assumption the plan must state
rather than rely on.
```

Pull request #55 shows why the rule needs a second half. The agent had executed, and its claim came with a
count and a timing. What it lacked was an address, and `CLAUDE.md` is blunt about which address tells you
least:

```text
# 📁 CLAUDE.md
So: a green local suite is the weakest evidence available. It says the code
works in the environment least like the one it runs in.
```

"Weakest" is a ranking. A local run still counts, and the four sections that follow are four ways a real
run ranks lower than its report suggests.

## It ran in the wrong place

A developer machine accumulates. It gains the packages from every requirements file anyone installed, both
of Controlvar's profiles (a full clone has `modules/`), a database migrated to the newest revision, a
`.git` directory, and `node_modules` for whichever JavaScript packages were last built. None of that shows
in a test result, so a test that leans on any of it without meaning to passes on the machine and fails
where the state is missing. An agent working in your clone inherits all of it and, unless told otherwise,
runs every check there.

The reverse of #55, a check green in CI for the wrong reason, is worse because nothing turns red.
`CLAUDE.md` records an earlier case:

```text
# 📁 CLAUDE.md
- a licence gate covering six npm closures was **vacuous in CI**, because the
  job installs only `frontend` — it "found" something locally purely because
  this laptop had the other five installed;
```

Tried locally, the gate found what it should and looked proven. In CI it had nothing to read and passed
every time. From the outside, a check that works only where you tried it looks exactly like a check that
works.

The fix in #55 shows the right response to a failure that happens only somewhere else. Its condition asks
about the place and ignores the result:

```python
# 📁 backend/tests/unit/scripts/test_leak_guard.py
def test_the_repository_itself_is_clean():
    """The real tree, scanned by the real guard. No fixtures involved.
    ...
    The skip is keyed on the ABSENCE OF `.git`, which is a structural fact
    about that copy, and NOT on the guard returning non-zero. A guard that
    breaks for any other reason still fails this test. [...]
    """
    if not (REPO_ROOT / ".git").exists():
        pytest.skip(
            ...
        )
```

Notice what the condition never looks at: the guard's exit status. Whatever goes wrong with the guard, in
any environment, still fails the test. Only the missing repository is excused.

> **Warning**
>
> **A skip is a status too.** A skipped test does not fail the run, and pytest prints the reason for a skip
> only when asked. `scripts/core_build.sh` asks: it runs its sessions with `-rs`, so that "each skip is
> listed with its reason". When an agent reports a pass count, ask for the skip count beside it, and for the
> reason behind any skip it added.

### Asking where

The question to ask is where the check ran, and how that place differs from where the code will run. To
the agent the place is not a variable, so it rarely says. The QA engineer's brief makes the answer part of
the job, asking each plan to "Name the cheap move that reproduces the real environment (`git archive` into
a plain directory, `scripts/core_build.sh`, moving `node_modules` aside, building the image) or say the PR
run is the first evidence."

Those moves come in rough order of cost. `git archive HEAD` into an empty directory leaves out `.git`, every
untracked file and every installed package; it reproduces the opening failure, and the first exercise runs
it. `scripts/core_build.sh` is the real thing and the expensive one: the failing run above stopped at its
unit step after 340 seconds, and its bootstrap step needs PostgreSQL.

Not every claim depends on where it ran. A test that touches no files and no services is unlikely to care.
When a claim does depend on the place and you cannot reproduce it, say so. Chapter 9's pull request had no
Docker for its browser tests, and its NOT VERIFIED list said: "browser-e2e: no Docker here. CI is the
evidence." Send back a report that ran everything locally and does not say so: its reader will take
local evidence for evidence about CI, and no one will be watching the part that never ran there.

## The flag did not do what its name says

A flag's name is a summary written for the common case. `--no-deps` sounds as though it leaves dependencies
out; `-e` in a shell script sounds as though it stops at the first error. Neither name says what happens at
the edges: a missing input, something the tool cannot resolve, a failing command that is not the last one
in a pipe. An agent picks flags the way most of us do, by name, and writes them into a script as though it
had watched them work.

`CLAUDE.md` keeps a list of eight things that were believed and turned out false, all found in one pass of
work on the project's P3.5 and P3.6 gates. Two of them:

```text
# 📁 CLAUDE.md
Almost every wrong thing written into the P3.5/P3.6 gates came from believing a
name instead of running the thing. In one pass, all of these were false:

- `pip-audit --no-deps` does **not** stop pip resolving. 75 pins in
  `backend/requirements.txt` audited **179** packages, including one the file
  does not pin. `--disable-pip` is what stops it, and pip-audit rejects that
  flag on an unhashed file unless `--no-deps` is *also* given.
[...]
- `rm -rf` on a path that does not exist **exits 0**. Every cleanup line in a
  Dockerfile fails open unless something afterwards asserts the result.
```

The numbers make the first entry useful. Someone counted 75 pins and 179 audited packages; without the
count, "we audit our pins with `--no-deps`" would have sat in the workflow looking correct. The second entry
has a shape that recurs in this chapter: given nothing to work on, the tool reports success. The third
exercise shows the shell's version, and what the `pipefail` in `scripts/core_build.sh`'s
`set -euo pipefail` adds to `-e`.

In a report, look for the sentence in which a flag carries the weight, such as "with `--no-deps`, only our
own pins are audited". It is a claim about behaviour made on the strength of a name. Ask for one run against
a case whose answer you already know, chosen so that the claim would fail if it were false: here, a
requirements file with one pin and a count of what was audited. The software architect's brief puts the
request in five words: "a name is not a behaviour."

The rule in `CLAUDE.md` is scoped: "before a flag, a default or an absence-of-error becomes load-bearing,
run it once and look." A flag that only shapes output, such as `-q` on pytest, carries no weight. The hard
case is a flag you cannot probe safely because running it acts on something real, such as a deploy or a
forced push; there the probe would be the irreversible act. Read the tool's source or its own tests, write
the behaviour into the plan as an assumption, and treat the step as one that waits for a person (Chapter 1).

## The search came back empty

Absence is the hardest thing to prove, and a search is the cheapest way to seem to prove it. A search finds
strings, though, and a dependency is a behaviour. Code can read a file without spelling its name: by joining
a directory constant to a filename at runtime, by listing a directory, by matching a glob, or by asking a
framework for "the nearest" configuration file above some path. The principal engineer's brief records one
such premise:

```text
# 📁 .claude/agents/principal-engineer.md
- *"The root `package.json` is unused"* — four separate negative checks agreed,
  and a test opened it at a path built from a constant at runtime.
```

`CLAUDE.md` tells the story. Before a deletion, four negative checks, among them a `git grep` and a search
of every workflow for an `npm ci` at the root, came back empty, and all four were right. CI failed anyway.
`frontend/src/tests/modules-alias.test.ts` opens the root `package.json` and asserts that it declares no
module `"type"`, because Turbopack takes a file's module format from the nearest `package.json` above it,
and the modules' dashboard code in `modules/frontend/src` sits below the repository root. `CLAUDE.md` draws
the conclusion, and its list of five shapes is the part to keep:

```text
# 📁 CLAUDE.md
No search for the string `package.json` could have found that. The consumer
builds the path at runtime, and what it asserts is the file's *contents*.

So a deletion is justified by **running the suites that could plausibly touch
the thing**, not by a grep that comes back empty. Ask what would read this
without naming it: a path composed from a root constant, a directory listing, a
glob, a framework resolving "the nearest X above Y", a fixture discovering
files. [...]
```

> **Note**
>
> **Lessons are claims too.** At `09d815ea`, `git grep -l package.json` lists 87 files, and the test above
> is one of them; its line 113 reads `fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8')`. So a
> search does reach it. What no search tells you is that this hit, among 87, concerns the file at the root.
> The lesson holds; only its first sentence overstates.

A search that comes back full misleads from the other side. The same brief tells of a workflow said to run
`cdk deploy` because the string was in it: "Both occurrences of that string are in comments, one of which
says the opposite." Until someone has opened the file at each hit, a count of hits is a count of places
nobody has read.

### Reading a claim of absence

Watch for "unused", "nothing references", "safe to delete", and any count of hits offered as a conclusion.
For a negative claim, ask which suites could read the thing without naming it, whether they ran, and where.
For a count, ask which hits are code and which are comments. A search is better evidence for a positive
claim with a name in it, such as the callers of a function called by name.

Running every suite that could read a file is not always cheap: the frontend suite needs an `npm ci`, and
other suites need a database. When the thing removed is cheap to restore and CI runs every suite, letting CI
be the evidence is a fair trade. In effect that is what happened here: CI caught the deletion, and the root
`package.json` is still there at `09d815ea`. The trade is fair only when the report's NOT VERIFIED list says
it is being made, so that a reviewer knows which check will settle it.

## The exit status was hidden

Every command ends with a number, and that number is the only part of its output a script can trust. The
commonest way to hide it looks the most innocent. A pipeline's exit status is that of its last command, and
`tail` succeeds whenever it can read its input, whether the test run in front of it passed, failed or was
killed halfway. `CLAUDE.md` gives the rule and what it cost:

```text
# 📁 CLAUDE.md
Never pipe a command whose failure matters through `tail` or `head`: it
replaces the exit status with the pipe's, and a killed run then reads as
success. Three agents and I were each fooled by this in one session. Redirect
to a file and read the file.
```

Agents reach for `| tail` because a full run's output is long and an agent reads everything a command
prints. The same section records a subtler way to lose part of a command:

```text
# 📁 CLAUDE.md
A comment inside a `\`-continued command ends it. Explaining an `aws ecs
run-task` flag on its own line mid-continuation left the command running with
four arguments and the rest parsed as separate commands; actionlint's SC2215
caught it, and that code is not among the five the lint job suppresses. Put the
comment above the command.
```

Nothing there failed in a way a person would notice, and the linter caught it only because its warning was
not one the lint job had switched off. A third way is to collect the status and throw it away. The pass over
the P3.5 and P3.6 gates found that too: `--strict` "did nothing, because the exit status was discarded", so
a package the audit tool could not resolve went unaudited while the gate's output said all was well.

The fix commit from the opening shows the good form. It kept the line that carries the status,
`core-build: FAILED at step unit (exit 1) after 340s`, and showed the same test file run in a `git archive`
copy (`19 passed, 1 skipped`) and in a git clone (`20 passed`), each result with its place and its numbers.

So read the shape of every command before its output. A test command followed by `| tail`, `| head` or
`| grep` has had its status replaced, and a command whose status is never printed has no status in the
report at all. The QA engineer's brief lists "a `tail` that swallows an exit code" among the silent failures
a plan must name. Ask for the number. A pipe is harmless only when you are not relying on its status, or
inside a script that sets `pipefail`. Bash also keeps each stage's status in the `PIPESTATUS` array:

```bash
bash -c 'false | tail -n 1; echo "exit ${PIPESTATUS[0]}"'
```

```text
exit 1
```

This is the cheapest of the four rules to follow, a redirect and an `echo $?` with no environment to
rebuild, which is the best reason not to accept a report that broke it.

## What a report should contain

Controlvar's pull request template asks for evidence in this form:

```text
# 📁 .github/pull_request_template.md
## How it was verified

<!-- The commands you actually ran and what they printed. For example:
     `make test-unit` → 4185 passed
     `npx playwright test --project=journeys` → 5 passed
     manual: logged in, created an experiment, saw it on /experiments -->
```

That covers the command and its output. Ask for the other two parts: the place (a clone with everything
installed, a `git archive` copy, a CI job by name) and the exit status, as a number. Then ask for the list
of what it did not run. Chapter 9's pull request ends with four such items, each saying where the evidence
will come from instead. It is the list an agent is least inclined to write, and NOT VERIFIED is the one
heading of a plan that `CLAUDE.md` calls mandatory (Chapter 3).

## A report, checked line by line

Here is a report of the kind an agent might hand back after the task "remove the root `package.json` if
nothing uses it". It is illustrative: no agent wrote it, and no such pull request exists. Every command and
path in it exists at `09d815ea`, and the change is the one `CLAUDE.md` says was once attempted.

```text
Removed package.json from the repository root. Nothing uses it.

How it was verified
1. git grep -l package.json lists 87 files. None of them reads the root one;
   they are about the package.json files under frontend/, sdk/ and demo/.
2. make lint: clean.
3. make test-unit 2>&1 | tail -n 3: no failures.
4. python3 scripts/leak_guard.py: leak-guard: clean.

NOT VERIFIED: nothing.
```

It is short, and every line has a command. Start with the first line, because the whole claim rests on
it. It is a search offered as proof of absence, so look for the reader a search would not recognise:

```bash
git grep -n "REPO_ROOT, 'package.json'"
```

```text
frontend/src/tests/modules-alias.test.ts:113:        fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8'),
```

The count of 87 was true. The conclusion was false, and the counterexample was one of the 87.

The second line, `make lint: clean`, is probably true and beside the point:

```bash
grep -n -E 'npm run lint|not installed' Makefile
```

```text
200:	cd frontend && npm run lint && npx tsc --noEmit
204:	else echo "hadolint not installed (brew install hadolint) - skipped"; fi
207:	else echo "actionlint not installed (brew install actionlint) - skipped"; fi
```

For the dashboard, `make lint` runs the linter and the type checker; the test that reads the root file
belongs to another target. And two linters, when missing, print a line ending in `skipped` and let the
target carry on, so "clean" on a machine without them covers two fewer tools than in CI.

The third line has one problem you can see in the command, the pipe through `tail`, and one you find in the
`Makefile`:

```bash
grep -n -A1 '^test-unit:' Makefile
```

```text
133:test-unit: ## Backend unit tests
134-	APP_ENV=test TESTING=true $(PYTEST) backend/tests/unit -q
```

Backend unit tests only. The suite that reads the file runs under `make test-frontend`, and it needs the
dashboard's `node_modules`, which a fresh clone does not have.

The fourth line holds up. Run it and you get `leak-guard: clean (3685 of every tracked file)` and exit 0, in
a git clone, which is where the Leak Guard workflow runs it too. It says nothing about whether the file was
used, and it was not offered as evidence of that. A review that treats every line as suspect is as hard to
act on as one that trusts every line.

The NOT VERIFIED line is the one to send back. "Nothing" is never true of a real change, and here the suite
that could have caught the problem never ran. The honest version is two sentences: "The frontend suite
(`make test-frontend`) was not run; this clone has no `node_modules`. The `Frontend Tests` check is the
evidence." That check is one of the twenty that `scripts/configure-repo.sh` requires, and this is what it
runs:

```bash
grep -n -E 'name: Frontend Tests|cd frontend && npm test' .github/workflows/pr-qa-gate.yml
```

```text
367:    name: Frontend Tests
383:        run: cd frontend && npm test -- --passWithNoTests --watchAll=false
```

With that line in the report, its reviewer would have known which check to watch, and a red `Frontend Tests`
run would have settled what four lines of evidence had only seemed to.

## Try it

Each step runs in a clone of Controlvar with only git, Python 3 and a shell, and leaves nothing behind. The
output is what each command printed at `09d815ea` on macOS; on a newer `main` some counts differ.

1. Run the leak guard where `core-build` runs it, in a copy of the tree with no `.git`, and then in your
   clone:

   ```bash
   d=$(mktemp -d)
   git archive HEAD | tar -x -C "$d"
   (cd "$d" && python3 scripts/leak_guard.py; echo "exit $?")
   python3 scripts/leak_guard.py; echo "exit $?"
   rm -rf "$d"
   ```

   ```text
   leak-guard: git rev-parse --show-toplevel failed: fatal: not a git repository (or any of the parent directories): .git
   exit 2
   leak-guard: clean (3685 of every tracked file)
   exit 0
   ```

   Exit 2 is the code the guard's docstring reserves for a usage error: it refuses to call clean a tree it
   could not list.

2. Watch a pipe hide a failure, and then make it visible:

   ```bash
   bash -c 'false | tail -n 1; echo "exit $?"'
   bash -c 'set -o pipefail; false | tail -n 1; echo "exit $?"'
   ```

   ```text
   exit 0
   exit 1
   ```

   `false` fails both times. Only the second command lets you see it.

3. Watch two commands report success over nothing:

   ```bash
   bash -e -c 'false | true; echo "still running"'; echo "exit $?"
   rm -rf ./no-such-dir; echo "exit $?"
   ```

   ```text
   still running
   exit 0
   exit 0
   ```

   `-e` sees only the last command of a pipe, so the script ran on past `false`; `pipefail` closes that
   gap. `rm -rf` reported success at removing a directory that was never there. Both behaviours are
   documented. The names just do not mention them.

4. Watch a comment end a command. The single quotes carry all three lines to bash as one script:

   ```bash
   bash -c 'echo one \
     # explain the next argument
     two'
   echo "exit $?"
   ```

   ```text
   one
   bash: line 3: two: command not found
   exit 127
   ```

   The comment ended the `echo`, so `two` became a command of its own.

5. Find the reader a negative search misses:

   ```bash
   git grep -l package.json | wc -l
   git grep -n "REPO_ROOT, 'package.json'"
   ```

   ```text
         87
   frontend/src/tests/modules-alias.test.ts:113:        fs.readFileSync(path.join(REPO_ROOT, 'package.json'), 'utf8'),
   ```

   Then ask your AI tool: "Is the repository root `package.json` used by anything? Answer yes or no, and
   show the command that proves it." If it answers "no" after a search, show it the second command and ask
   what other suites could read a file without naming it.

6. Ask your tool to run one test file and report. Then ask: "What was the exact command, where did it run,
   and what was the exit status?" Run the command yourself and compare. If your tool piped the run through
   `tail`, run it again without the pipe.

## Summary

A claim that something passed describes one run in one place, and a report usually shows only its result.
In pull request #55 the result was true and the place was wrong, and only a check that ran somewhere else
could say so. The four failures in this chapter share that anatomy: the execution was real and its output
true, and the conclusion reached further than the run did. You catch them by asking where each check ran
and what its exit status was, and by insisting on the list of what did not run. Chapter 6 turns the same
suspicion on the checks themselves.

## Further reading

- [Before You Change Controlvar, Chapter 4](../before-you-change-controlvar/chapter-4-tests-and-gates.md):
  what a pull request must pass, and why a green local suite is the weakest evidence.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md#hour-12--a-release-review-from-evidence-to-decision-the-test-that-could-not-fail):
  a claim ties to evidence, and evidence ties to an environment.
- [Build a Platform, Day 22](../build-a-platform-in-28-days/week-4-production.md#day-22--testing-strategy-the-pyramid-the-harness-and-the-hot-path-lesson):
  the testing strategy and the harness.
- In this book, [Chapter 3](./chapter-3-plan-before-code.md#the-plans-shape) asks these questions before
  any code exists, under ENVIRONMENTS and NOT VERIFIED.

---

_Previous: [Chapter 4 — Briefing an agent](./chapter-4-briefing-an-agent.md). Next: [Chapter 6 —
Tamper-testing](./chapter-6-tamper-testing.md)._

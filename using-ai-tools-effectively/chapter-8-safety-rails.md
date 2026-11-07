# Chapter 8 — Safety rails

At 15:18 UTC on 26 September 2026, pull request [#178](https://github.com/Controlvar/controlvar/pull/178)
was opened against Controlvar with the title "test: no test can reach real AWS credentials or the aws
binary". Its description starts with the reason, and the reason is a near miss. During a tamper test, the
deliberate breaking of a guard that Chapter 6 teaches, a unit test whose guard had been switched off sent a
real `aws deploy stop-deployment`. That command stops a deployment in progress. The call failed, and the
description says why: "It failed only because the machine had no valid credentials." Nothing in the test
suite stood between a broken test and a real account. A tamper test breaks a guard on purpose, which makes
it the moment when the code under test is least to be trusted, and at that moment nothing else was there.

The pull request then measured how close the miss had been, on the machine where it was written. That
machine had a `~/.aws` directory holding credential profiles. With the new plugin disabled, a probe test
found the real `aws` binary and resolved credentials from the shared credentials file; with the plugin
enabled, it found a fake binary and dummy credentials. The description draws the conclusion in one line:
"So without the gate, any test on this machine would have reached the real binary and real credentials."
The fix merged at 16:59 the same day. Like most changes here, its commit names a Claude model as co-author.

The lesson of that afternoon is about where to put a rule. `CLAUDE.md` already had a section on how to run
tests, and the tamper test was the project's own rule at work: break the thing a check guards and watch it
fail. What was missing was something that would hold when a test, or the person or agent running it, did
the wrong thing. This chapter is about those mechanisms, which we call rails: the plugin that keeps every
test away from real AWS, the guard that stands between a push and the public, the checks that hold a pull
request to the project's conventions, and the guard that keeps outsiders away from the agents'
instructions. For each you will see what it does, the incident behind it, and the gap it admits to. The
chapter ends with the two rules `CLAUDE.md` gives for the moment an agent proposes a cleverer fix, and
with one illustrative proposal for a new rail, checked against everything before it.

## A rule is read, a rail holds

An instruction works through a chain of events. The agent has to load the file, find the sentence, connect
it to what it is doing, and apply it at the right moment. Every link can fail, and the agent starts each
session with no memory of the last (Chapter 4). Chapters 3 to 7 try to make that chain stronger with
plans, briefs, checks and reviews. A rail takes the chain away. It acts whether or not anyone has read a
word, because it sits in the path the work has to take: the test runner, the push, the branch protection
on `main`.

Controlvar keeps both. `CLAUDE.md` states the rule in its section on testing, and the sentence describes a
mechanism, added in the same pull request as the mechanism itself:

```text
# 📁 CLAUDE.md
- **No test reaches real AWS.** The root `conftest.py` loads
  `backend/tests/no_real_aws.py` (the SDKs carry a copy): dummy credentials, no
  `~/.aws`, and a failing `aws` first on `PATH` that prints `NO-REAL-AWS`. A
  test that needs the CLI prepends its own fake `aws` to `PATH`; that one wins.
```

Each rail has a price. The AWS plugin has tests of its own, the leak guard's workflow plants an account id
on every run to prove the guard still fires, and a hook that fails for a bad reason gets skipped, as the
leak guard's own source admits further on, unless someone fixes it. The rails below each stand in front
of harm that is hard to undo: an action on a real account, a string in public history, a change the
release notes never show.

## A fake `aws` first on the PATH

The plugin is `backend/tests/no_real_aws.py`, and its docstring opens with the incident:

```text
# 📁 backend/tests/no_real_aws.py
Why: during a tamper test a unit test whose guard was deliberately broken sent
a real ``aws deploy stop-deployment``. It failed only because that machine had
no valid credentials. On a developer machine that does hold them it would have
acted on a real account.
```

It works on the two ways a test can reach AWS. Python code reaches it through boto3, which finds
credentials by walking a chain of sources: environment variables, the files under `~/.aws`, profiles,
session tokens, container and instance metadata. Shell code reaches it through the `aws` command, found by
searching `PATH`. The plugin rewrites both, at `pytest_configure` before any test is collected and again
before every test, in case an earlier test left something behind. It sets the two key variables to the
dummy value `no-real-aws-dummy`, turns off the instance metadata service with `AWS_EC2_METADATA_DISABLED`,
points `AWS_CONFIG_FILE` and `AWS_SHARED_CREDENTIALS_FILE` at files that do not exist, unsets twelve other
variables, and puts this script first on `PATH`:

```python
# 📁 backend/tests/no_real_aws.py
FAKE_AWS = f"""#!/bin/sh
# Installed by backend/tests/no_real_aws.py: no test may run the real aws CLI.
printf '%s\\n' "$*" >> "$(dirname "$0")/calls.log"
echo "{MARKER}: the aws CLI is blocked in tests (called: aws $*)." >&2
echo "{MARKER}: put your own fake aws first on PATH." >&2
exit {FAKE_EXIT}
"""
```

`MARKER` is `NO-REAL-AWS` and `FAKE_EXIT` is 97. The key variables are set to a dummy and never removed,
because, in the docstring's words, "moto and the other mocks need *some* credentials". The fake records
its arguments in `calls.log`, and the plugin's own test reads that file to prove the call was attempted.
Its exit status and its marker give a test two things to assert, and the test does assert both.

The first application comes "before collection, so before any import-time client or session fixture",
and the second because "a test may leave the environment dirty".

A rail that covers everything rarely exists, and this one says what it misses:

```text
# 📁 backend/tests/no_real_aws.py
What it cannot reach: a subprocess started with a hand-built environment that
drops these variables but keeps ``HOME``. Such a subprocess would read
``~/.aws`` again if it used boto3. It would still find the fake ``aws`` if it
inherited ``PATH``.
```

The next person who writes a test that spawns a subprocess
with its own environment knows that the rail does not cover it, and knows why. A rail that claimed to be
complete would invite exactly that test to lean on it.

> **Note**
>
> **No way around it, on purpose.** Pull request #178 could have added a marker that lets a test opt out
> and reach real AWS. It did not, and its description explains: "A `real_aws` marker with nothing behind it
> would be an escape hatch nobody reviewed, so the marker waits for a test that needs it." The one test in
> the suite that said it needed real credentials had been skipped unconditionally since before that pull
> request.

The rail stops at the edge of pytest. A script you run by hand, or a command an agent types into your
shell, gets your real environment, and for those the protection is the rule in Chapter 1: an action that
cannot be undone waits for a person at the moment it happens. The rail covers the suite, which is where
tamper tests happen.

## A rail can make a failure quieter

Three days later, an issue showed what the first rail could not do on its own. Issue
[#433](https://github.com/Controlvar/controlvar/issues/433), opened at 17:51 UTC on 29 September 2026, is
titled "Event-processor Lambda tests call real S3; one bandit scheduler test sleeps a fixed time". The
second rail's docstring explains how a test can call S3 and still pass:

```text
# 📁 backend/tests/no_outbound_network.py
Why: the Lambda functions build real boto3 clients on a cold start. A test
that forgets to stub one of them does not fail. ``no_real_aws`` gives it dummy
credentials, the request goes to the real AWS endpoint, AWS answers
``InvalidAccessKeyId``, and the handler under test catches that and carries on
(#433: the event processor's handler tests sent ``PutObject`` to S3 on every
run). The test stays green; it is merely slower, and depends on the network.
```

Read that with the first rail in mind. With dummy credentials, the request could never act on an account,
so the near miss of #178 could not repeat. A request still left the machine on every run, and the handler's
own error handling made the failure invisible. The second rail, `backend/tests/no_outbound_network.py`,
makes any attempt to reach a non-local address fail the test, for every test under `backend/lambda/`,
`modules/lambda/` and the showcase tests. It deliberately leaves the rest of `backend/tests` alone, because
the integration tests reach PostgreSQL and Redis by host name in CI.

A rail changes what failure looks like, and the new kind of failure can be quieter than the old. When an
agent adds a rail, ask what a test that the rail catches now does, step by step, until something either
fails or reports success. If the answer ends in "and the code under test catches it", the rail has moved
the problem.

## A push is publication

Controlvar is developed in the open. There is no private copy that changes are filtered through on their
way out, and `CLAUDE.md` puts it in one sentence:

```text
# 📁 CLAUDE.md
Development happens in this repository now. There is no private repository and
no export step: a push here is publication, and `scripts/leak_guard.py` is what
stands between a secret and the world (see `.github/workflows/leak-guard.yml`).
```

For an agent, that is a sharp edge. An agent writes a great deal of text that is not code: commit messages,
pull request descriptions, reports of what it ran. That text is where the details of its environment
surface, such as an absolute path in a pasted error or a value it read from your machine. Once a commit
reaches `main`, its message is part of the published history.

The guard faced a design problem that its docstring explains at length. A list of forbidden strings would
have to be published with the guard, which defeats the purpose. So the guard matches the shape of what it
forbids:

```text
# 📁 scripts/leak_guard.py
The two `id` rules are matched by SHAPE instead: any AWS account id in an AWS
context, any developer home path. That is strictly stronger than the literals
were -- it catches the next contributor's home directory and a second AWS
account, neither of which a literal list would have known about -- and it
publishes nothing.
```

The same docstring records why the guard reads more than files:

```text
# 📁 scripts/leak_guard.py
Commit messages are scanned because that is precisely how this escaped once
before: the tree was clean, the sweep reported PASS, and sixteen commit
messages in the published history contained an absolute home path. A check
that reads only files is a check that has already missed this.
```

Sixteen messages, with a clean tree and a passing check. That earlier check read the place where secrets
usually are and missed the place an author's environment leaks into. So the repository runs the guard at
three points, each in the mode that fits it. `--staged` runs as a pre-commit hook. `--push` runs as
a pre-push hook and reads the outgoing commit messages. In CI, the Leak Guard workflow runs on every push
and every pull request, forks included, scans the changed range with its commit messages and then every
tracked file, and reads the pull request's title and body as well, because GitHub composes the squash
commit's message from them. `Leak Guard` is one of the twenty checks `scripts/configure-repo.sh` requires
before anything merges.

The hooks and the workflow do different jobs. A hook is a convenience that catches a mistake before it
leaves your machine, and a person can skip it. The workflow is the rail. The guard's own source admits as
much about hooks, in the function that works out what a push contains:

```text
# 📁 scripts/leak_guard.py
    every branch, the first time it is pushed -- it exits non-zero, and a
    pre-push hook that dies there is a pre-push hook everybody learns to skip
    with --no-verify. Measured, not assumed:
```

A hook that fails for a bad reason teaches its users to switch it off. The guard handles the branch with
no upstream so that the hook keeps earning its place, and the required check catches what a skipped hook
lets through. Chapter 6 shows the last piece: on every run, the workflow plants an account id in a scratch
repository and fails if the guard does not reject it.

## Rails on the pull request itself

Three more checks hold every pull request to the project's conventions, whatever an agent's instructions
said or failed to say.

The first is the DCO check, `scripts/check_dco.py`: every commit that is not a merge must carry a
`Signed-off-by:` trailer matching its author or committer. Its docstring is frank about where it came from:

```text
# 📁 scripts/check_dco.py
``CONTRIBUTING.md`` tells contributors "A pull request with unsigned commits
will be blocked by the DCO check until every commit carries a sign-off".  Until
this script existed, that sentence was false: there was no DCO check anywhere
in the repository and no app installed.  A contributor-facing promise about
observable behaviour has to be enforced or rewritten as intent, and the promise
is the one we want, so this enforces it.
```

That is Chapter 2's rule, "A claim about behaviour needs a test, or say it is intent", applied to a
sentence in a guide for contributors. The same docstring records a measurement made before the check was
switched on: Dependabot signs its commits with an address that does not match its author, so a strict
comparison "rejects every Dependabot pull request, which this repository has a great many of -- measured,
not assumed; the first version of this script did exactly that". Bots are therefore exempted by an explicit
list of addresses. The check matters for agents in a particular way: `CLAUDE.md`'s own commit template
(Chapter 1) has a `Co-Authored-By` line and no sign-off, so an agent that follows the template to the
letter writes commits this check refuses. The rail catches what the instruction got wrong.

The second check is the pull request's title. `main` is squash-merged, the squash commit's subject is the
title, and release-please builds the changelog and picks the next version from those subjects. The
`conventional-title` workflow records what happened before it existed (the numbers are from the
repository's earlier, private home):

```text
# 📁 .github/workflows/conventional-title.yml
# That is exactly what happened to P3.5 (#165, and #167). Its merge subject was
# prose, release-please could not parse it, and the release pull request it
# then opened (#166) contained three historical `docs:` commits and nothing
# from the change that had just landed. Its log shows it skipping 81 such
# commits with `Error: unexpected token ' ' at 1:7`.
```

Eighty-one commits skipped, each with an error in a log. The release was still cut. A title written as
prose fails no build and stops no merge; the work simply never appears in the release notes. A plain
descriptive sentence makes a natural title, for a person or an agent, and it is exactly what this check
refuses. The check itself is a regular expression over one string, with twelve types, and the fourth
exercise runs it.

The third is `regression-guard`: a pull request labelled `bug` must change at least one test file. The
workflow recognises tests by path, from `tests/` directories and `test_*.py` files to `*.test.tsx` specs.
A `skip-regression-guard` label lets an author opt out, and the workflow's message for that case assumes
the reason is in the description. One comment in the workflow is worth copying into any project that
writes its own required checks:

```text
# 📁 .github/workflows/regression-guard.yml
# The job always runs and always reports: a pull request without the `bug`
# label passes the first step immediately. That matters because a job skipped
# by a top-level `if:` reports nothing at all, which leaves a required check
# pending forever.
```

A required check that never reports is a merge that never happens, and it shows as pending, exactly like a
check that is still running. `DCO` and `regression-guard` are both among the twenty required checks;
`conventional-title` runs on every pull request into `main` beside them.

## Only maintainers change the instructions

If agents act on what is in the repository, then whoever can change `CLAUDE.md` or `.claude/agents/` can
change what the agents do. The contributor guard, added on 1 November 2026 in pull request
[#1570](https://github.com/Controlvar/controlvar/pull/1570), runs on every pull request from a fork and
refuses any path outside the areas open to outside contributors. Its rules are an allow-list, read in order:

```text
# 📁 scripts/contributor_guard.py
2. location  under ``backend/``, ``modules/backend/``, ``modules/frontend/``,
             ``frontend/``, ``sdk/``, ``demo/``, ``docs/`` or ``tests/``, or
             exactly ``README.md``; and not under
             ``backend/tests/unit/infrastructure/``, nor exactly one of the
             package files above the CI-check tests (REFUSED_FILES);
3. dot       no segment starts with ``.``;
4. scripts   no segment is ``scripts``;
5. claims    not under ``docs/_claims/`` or ``docs/security/``;
```

`CLAUDE.md` sits at the root, outside every allowed area, and `.claude/` fails both the location rule and
the dot rule. The workflows fail the same two. The guards, the leak guard among them, live under
`scripts/`. And the claims registry, which ties sentences in the documentation to the tests that hold them
(Chapter 2), has a rule of its own. A fork can still propose a change to any of these; a red result means,
in the docstring's words, "a maintainer makes this part of the change", which is a different thing from a
rejection.

The guard runs on a contribution before anyone has reviewed it, and it is careful about what it touches:

```text
# 📁 scripts/contributor_guard.py
Only metadata, through the API, with the workflow's read-only token: the pull
request (its head, before and after), every page of its file list (100 a
page; the number of entries must equal ``changed_files``), and the head
commit's tree (which must not be ``truncated``). Nothing of the pull request
is checked out or run.
```

Two ideas carry over to any setup where agents act on other people's work. The agents' instructions must
not be editable by the people whose work they review. And nothing in a contribution runs with the agent's
permissions before a person has looked at it. Notice also the counting in that paragraph: the file list
must have exactly `changed_files` entries, so a list cut short by paging cannot pass as a short list. The
docstring lists the conditions under which the guard stops, and heads the list "fail closed, never
'nothing to check'": the same refusal to read silence as success that Chapter 5 asked of an exit status.

## When an agent proposes a cleverer fix

Rails catch actions. Two sections of `CLAUDE.md` guard against a different kind of harm, the change that
looks more thorough than what it replaces. An agent can produce one in minutes, and in review it reads
as diligence.

The first is titled "Prefer the dumbest thing that works". It opens with a count and ends with a rule:

```text
# 📁 CLAUDE.md
Twice in this repository a "safer" rewrite was less safe than what it replaced.
...
a boring construct you can reason about over a clever one you cannot.
```

The case behind it is the readiness sanitiser, which replaced a plain 200-character truncation with three
regular expressions. `CLAUDE.md` records two ways the rewrite did worse than the line it replaced, one of
them on inputs its own docstring gave as examples. The truncation had done less, and everything it did
was right. When an agent proposes replacing something simple, ask first what the simple version got
right, and ask for a test that holds it to that.

The second section is about scope:

```text
# 📁 CLAUDE.md
Three defects in one cycle came from moving logic out of `db/bootstrap.py` into
`migrations/env.py` "so every path gets it". `reconcile_with_models` then ran
`create_all` on *every* `alembic upgrade` that reached head — creating
unmigrated tables in production, outside the migration transaction, invisible
to the next autogenerate. `may_run_alembic`'s upgrade-shaped test made
`downgrade` and `stamp` silent no-ops. And the third repair was left behind,
so the raw `alembic upgrade heads` that production actually runs still died.

Widening where something runs widens what it can break. List the callers, say
what each now gets, and scope the change to the ones that asked for it.
```

"So every path gets it" sounds like care. It is a change to every caller at once, made while thinking about
one. The software architect's brief carries the same instruction: "Before moving logic into a shared path,
list the callers and what each now gets."

The pull request that opened this chapter is the rule done well. The AWS plugin is exactly a piece of
logic moved into a shared path: one root `conftest.py` that every suite loads. Its description has a
section headed "Every conftest root CI runs (the shared-path rule: callers enumerated)", a table of each
test root, the workflows that run it, and what it now gets. Building that table found things. Two of the
four Lambda directories had no conftest at all. The two SDK suites have their own root directory, so the
repository-root conftest never loads for them, and each needed its own copy. A test in the same pull
request now runs each root and checks that it lists the fixture, so a root added later cannot quietly miss
the rail.

## A proposed rail, checked

Here is a proposal of the kind an agent might write after reading about the near miss. It is illustrative:
no agent wrote it, and no such pull request exists. Everything it names exists at `09d815ea`.

```text
Safety: two new rails.

1. CLAUDE.md now says, under Testing: "Never call the real aws CLI from a
   test."
2. New pre-commit hook: runs python3 scripts/leak_guard.py before every
   commit, so a secret can never be committed.

Verified: python3 scripts/leak_guard.py prints "leak-guard: clean".
```

Take the first item. It is a sentence, and the near miss happened during a tamper test, the one situation
in which guards are broken on purpose. A sentence would not have stopped the unit test that sent the call,
because no reader was involved; the test ran code. It is a useful sentence for a person writing a new
test. It is not a rail, and the report calls it one.

The second item is a hook, and hooks live in your clone. A fresh clone of Controlvar has none installed
(`find .git/hooks -type f ! -name '*.sample'` counts zero), and the repository's own hooks arrive only when
someone runs `pre-commit install`. The guard's source, quoted above, says what people do with a hook that
annoys them. The repository already has two hooks for the guard, `--staged` before a commit and `--push`
before a push, so the proposal duplicates the first of them and adds nothing that holds. The rail is the
required `Leak Guard` check, which no local setting can skip. "Can never be committed" is also the wrong
claim: a commit stays on your machine until it is pushed. What must never happen is publication, which
the push hook and the required check stand in front of.

The verification line has the problem Chapter 5 described: the command is right and the conclusion
reaches further than the run. Run without arguments, the guard reads "every tracked file, and no
messages", in the words of its own usage text. Commit messages, which is how the sixteen leaks happened,
were never read. The command that reads them names a range:

```bash
python3 scripts/leak_guard.py --range HEAD~5..HEAD; echo "exit $?"
```

```text
leak-guard: clean (42 of files changed in HEAD~5..HEAD, 5 commit messages)
exit 0
```

That run says what it read, and so it can be checked. What the proposal should have said is shorter than
what it did say: the instruction is guidance, the hooks already exist, and the rail is the required check,
which is tamper-tested on every run.

## Try it

Each step runs in a clone of Controlvar with git, Python 3 and a shell. The output is what each command
printed at `09d815ea` on macOS; on a newer `main` some counts differ.

1. Run the leak guard over every tracked file, and then over the last five commits with their messages:

   ```bash
   python3 scripts/leak_guard.py; echo "exit $?"
   python3 scripts/leak_guard.py --range HEAD~5..HEAD; echo "exit $?"
   ```

   ```text
   leak-guard: clean (3685 of every tracked file)
   exit 0
   leak-guard: clean (42 of files changed in HEAD~5..HEAD, 5 commit messages)
   exit 0
   ```

   Only the second run read any commit messages, and it says how many.

2. Check whether your clone has any hooks installed:

   ```bash
   find .git/hooks -type f ! -name '*.sample' | wc -l
   ```

   ```text
          0
   ```

   Git ships example hooks with a `.sample` suffix, which never run. A fresh clone has nothing else until
   `pre-commit install` adds the repository's hooks.

3. Run the DCO check on the squash commit from Chapter 9, and on a commit from March 2026, before the check
   existed. The `sed` replaces the author's e-mail address in the output:

   ```bash
   bash -c 'python3 scripts/check_dco.py --range 522fdc99~1..522fdc99 | sed -E "s/[^ ]+@[^ ]+/<email>/g"; echo "exit ${PIPESTATUS[0]}"'
   bash -c 'python3 scripts/check_dco.py --range ce9f1c62~1..ce9f1c62 | sed -E "s/[^ ]+@[^ ]+/<email>/g"; echo "exit ${PIPESTATUS[0]}"'
   ```

   ```text
   ok   522fdc99 <email>

   all 1 commit(s) carry a matching sign-off
   exit 0
   MISSING ce9f1c62 <email>

   1 of 1 commit(s) have no `Signed-off-by:` matching their author or committer:

     ce9f1c62 authored by <email> (sign-offs found: none)

   Sign off every commit with `git commit -s`. To fix a branch you have
   [...]
   exit 1
   ```

   The exit status comes from `PIPESTATUS[0]`, the check's own, so the `sed` cannot hide a failure
   (Chapter 5). The refusal says how to fix the branch, which is what makes a rail tolerable to the
   people it stops.

4. Run the title check's pattern, copied from `.github/workflows/conventional-title.yml`, against three
   titles. Save this as `title.sh` outside your clone and run `bash title.sh`:

   ```bash
   TYPES='feat|fix|perf|deps|docs|build|ci|refactor|test|chore|revert|style'
   PATTERN="^(${TYPES})(\([a-z0-9][a-z0-9._/-]*\))?!?: .+"
   for t in 'fix(scheduler): a scheduled draft that cannot start stays a draft' \
            'Scheduler: drafts that cannot start stay drafts' \
            'Fix(scheduler): drafts stay drafts'; do
     if printf '%s' "$t" | grep -qE "$PATTERN"; then echo "pass  $t"; else echo "fail  $t"; fi
   done
   ```

   ```text
   pass  fix(scheduler): a scheduled draft that cannot start stays a draft
   fail  Scheduler: drafts that cannot start stay drafts
   fail  Fix(scheduler): drafts stay drafts
   ```

   The third title fails on one capital letter. Ask your AI tool to title a change of its own, and run its
   title through the script.

5. Ask the contributor guard which of the rails' own files a fork may change:

   ```bash
   python3 -B -c 'import sys; sys.path.insert(0, "scripts"); import contributor_guard as g; [print(p, g.path_rule(p)) for p in ["CLAUDE.md", ".github/workflows/leak-guard.yml", "scripts/leak_guard.py", "docs/_claims/docs/guides/user-guide.md.toml", "docs/guides/user-guide.md"]]'
   ```

   ```text
   CLAUDE.md location
   .github/workflows/leak-guard.yml location
   scripts/leak_guard.py location
   docs/_claims/docs/guides/user-guide.md.toml claims
   docs/guides/user-guide.md None
   ```

   `None` means no rule refuses the path. The user guide is open to contributors; the registry that ties
   its sentences to tests is not.

6. With the test environment from
   [Before You Change Controlvar, Chapter 2](../before-you-change-controlvar/chapter-2-run-it.md), run the
   rail's own tests. NOT RUN for this edition, which used no virtual environment:

   ```bash
   python -m pytest backend/tests/unit/test_no_real_aws.py -p no:cov -q
   ```

   Among them, `test_aws_cli_runs_the_blocking_fake` calls `aws sts get-caller-identity` and expects exit
   97 and `NO-REAL-AWS` on standard error.

7. Ask your AI tool: "I want every code path to get the repairs that `backend/app/db/bootstrap.py` makes.
   Tell me where they should go." If it proposes a shared place, ask it to list every caller of that place
   and what each would now get, and compare its list with the section on cleverer fixes above.

## Summary

A rail is a mechanism in the path the work has to take, so it holds on the day an instruction is missed.
Controlvar's came from incidents: a tamper test that sent a real AWS command, sixteen commit messages that
published a home path, a release that silently dropped a change, a promise in a contributor guide that
nothing enforced. The good ones say what they cannot cover, refuse to read silence as success, and are
tamper-tested where they run. For the changes rails cannot catch, two rules remain: prefer the simple
construct you can reason about, and list the callers before you widen a path. Chapter 9 follows one real
change past every one of these checks, from its issue to its release.

## Further reading

- [Before You Change Controlvar, Chapter 4](../before-you-change-controlvar/chapter-4-tests-and-gates.md):
  the harness plugins and the required checks.
- [Before You Change Controlvar, Chapter 6](../before-you-change-controlvar/chapter-6-a-first-change.md):
  DCO, the title, the template, and what contributor-guard and regression-guard do on a real pull request.
- [Before You Change Controlvar, Chapter 8](../before-you-change-controlvar/chapter-8-after-the-merge.md):
  how the title becomes a line in the release notes.
- [Production Engineering, Hour 6](../production-engineering/part-2-reliable-systems.md): database
  evolution while the previous release is still running.

---

_Previous: [Chapter 7 — Reviewing agent work](./chapter-7-reviewing-agent-work.md). Next: [Chapter 9 — A
worked example](./chapter-9-a-worked-example.md)._

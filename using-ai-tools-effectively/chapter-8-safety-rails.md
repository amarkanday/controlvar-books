# Chapter 8 — Safety rails

**Outcome:** you know the mechanisms in Controlvar that stop an agent's mistake from reaching a real
cloud account, the public, or `main`, and two rules for the moment an agent proposes a clever fix.

## Rails, not rules

Chapters 3 to 7 are about judgement: plans, briefs, checks and reviews. Judgement fails sometimes. A rail
is a mechanism that holds when it does: it does not depend on the agent reading an instruction, or on a
reviewer noticing. Each rail below exists because something got through once.

## No test reaches real AWS

Tamper-testing (Chapter 6) means breaking things on purpose. That is safe only if a broken guard cannot
reach anything real. Here is what happened before this rail existed:

```text
# 📁 backend/tests/no_real_aws.py
Why: during a tamper test a unit test whose guard was deliberately broken sent
a real ``aws deploy stop-deployment``. It failed only because that machine had
no valid credentials. On a developer machine that does hold them it would have
acted on a real account.
```

The fix is a pytest plugin that the repository's root `conftest.py` loads into every session. Before any
test is collected, and again before every test, it replaces the credentials with dummies, points the AWS
config files at paths that do not exist, and puts a fake `aws` first on `PATH`:

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

`MARKER` is `NO-REAL-AWS` and `FAKE_EXIT` is 97. The plugin's docstring also says what it cannot stop:

```text
# 📁 backend/tests/no_real_aws.py
What it cannot reach: a subprocess started with a hand-built environment that
drops these variables but keeps ``HOME``. Such a subprocess would read
``~/.aws`` again if it used boto3. It would still find the fake ``aws`` if it
inherited ``PATH``.
```

A rail that names its own gap is worth more than one that claims to be complete, because the next person
knows where not to lean on it. The same `conftest.py` loads a second rail for the Lambda and showcase
suites: a test there that opens a connection to anything but loopback fails
(`backend/tests/no_outbound_network.py`).

## A push is publication

Controlvar is developed in public. Nothing filters a commit between an agent's branch and the world:

```text
# 📁 CLAUDE.md
Development happens in this repository now. There is no private repository and
no export step: a push here is publication, and `scripts/leak_guard.py` is what
stands between a secret and the world (see `.github/workflows/leak-guard.yml`).
```

The leak guard scans every tracked file, and the commit messages of the pushed range:

```text
# 📁 scripts/leak_guard.py
Commit messages are scanned because that is precisely how this escaped once
before: the tree was clean, the sweep reported PASS, and sixteen commit
messages in the published history contained an absolute home path. A check
that reads only files is a check that has already missed this.
```

It also reads the pull request's title and body, because GitHub builds the squash commit from them. Its
workflow comment explains: the branch commits had been fixed after the guard rejected them, but the pull
request description had not, and the merge commit carried the path. Commit messages and descriptions are
where an agent writes its report of what it ran, so they are exactly where a local path or a value from
the agent's machine turns up.

Two details are worth copying. The guard matches secrets by **shape**, not by a list, so it can be public
without publishing what it forbids. And its workflow tamper-tests it on every run (Chapter 6).

## Sign-offs, titles and tests

Three checks hold a pull request to the project's rules whatever the agent's instructions say. `DCO` and
`regression-guard` are among the twenty required checks; `conventional-title` runs on every pull request
beside them.

**DCO.** Every commit carries a `Signed-off-by:` line. The check's own docstring records that the promise
came before the check:

```text
# 📁 scripts/check_dco.py
``CONTRIBUTING.md`` tells contributors "A pull request with unsigned commits
will be blocked by the DCO check until every commit carries a sign-off".  Until
this script existed, that sentence was false: there was no DCO check anywhere
in the repository and no app installed.  A contributor-facing promise about
observable behaviour has to be enforced or rewritten as intent, and the promise
is the one we want, so this enforces it.
```

That is Chapter 2's rule ("A claim about behaviour needs a test, or say it is intent") applied to a
sentence in a contributor guide. The same docstring notes that the script's first version rejected every
Dependabot pull request: "measured, not assumed; the first version of this script did exactly that". And
recall from Chapter 2 that `CLAUDE.md`'s own commit template has no sign-off line. The check is what makes
that drift visible.

**The title.** `main` is squash-merged, so the pull request title becomes the commit subject, and the
changelog is built from subjects:

```text
# 📁 CLAUDE.md
**The pull request title is the release note.** `main` is squash-merged and
GitHub makes the squash subject the pull request title, which is what
release-please parses to build the changelog and choose the version bump. A
title that is not a conventional commit is invisible to it -- the release is
still cut, the work simply does not appear in it and does not move the
version. That is what happened to P3.5 (#165/#167): its subject was prose, and
the release pull request it produced contained three historical `docs:`
commits and nothing from the change that had just landed.
```

(#165 and #167 are numbers from the repository's private era.) The `conventional-title` check refuses a
title that does not start with one of the twelve types. An agent that writes prose titles has its work
vanish from the release notes without an error anywhere.

**A test with every fix.** The `regression-guard` check fails a pull request labelled `bug` that changes
no test file, and the pull request template asks for the test by name.

## Only maintainers change the instructions

The contributor guard runs on every pull request from a fork. It reads the list of changed files through
the API and refuses any file outside the areas open to outside contributors:

```text
# 📁 scripts/contributor_guard.py
2. location  under ``backend/``, ``modules/backend/``, ``modules/frontend/``,
             ``frontend/``, ``sdk/``, ``demo/``, ``docs/`` or ``tests/``, or
             exactly ``README.md``; and not under
             ``backend/tests/unit/infrastructure/``, nor exactly one of the
             package files above the CI-check tests (REFUSED_FILES);
3. dot       no segment starts with ``.``;
4. scripts   no segment is ``scripts``;
```

`CLAUDE.md` is not in those areas, and `.claude/` fails both the location rule and the dot rule. So the
files that tell the agents how to work cannot be changed from outside. Neither can the workflows that
check the agents' output. And the guard itself never runs the contribution:

```text
# 📁 scripts/contributor_guard.py
Only metadata, through the API, with the workflow's read-only token: the pull
request (its head, before and after), every page of its file list (100 a
page; the number of entries must equal ``changed_files``), and the head
commit's tree (which must not be ``truncated``). Nothing of the pull request
is checked out or run.
```

If you let agents act on pull requests from other people, the same two ideas apply: the agents'
instructions are not editable by the people whose work they review, and nothing in the contribution runs
with the agent's permissions.

## Two rules for clever fixes

Agents are good at producing a thorough-looking replacement for something simple. Two sections of
`CLAUDE.md` are about what goes wrong next.

### Prefer the dumbest thing that works

```text
# 📁 CLAUDE.md
Twice in this repository a "safer" rewrite was less safe than what it replaced.
[...]
In redaction, authentication, and anything parsing untrusted text: bound every
quantifier, truncate before processing rather than after, compare on bytes
(Starlette decodes headers as latin-1 and `hmac.compare_digest` raises
`TypeError` on a non-ASCII `str` — this has been a live 500 twice), and prefer
a boring construct you can reason about over a clever one you cannot.
```

The story between those lines is a sanitiser that replaced a plain 200-character truncation with three
regular expressions, and turned out both slower and less thorough than the truncation. When an agent
proposes a cleverer replacement, ask first what the simple version got right.

### List the callers before you widen a path

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

"So every path gets it" sounds like thoroughness. It is a change to every caller at once. The software
architect's brief carries the same rule: "Before moving logic into a shared path, list the callers and
what each now gets."

## Try it

1. Watch the AWS rail catch a call. Create `./backend/tests/unit/test_try_no_aws.py` with this content:

   ```python
   import subprocess


   def test_the_aws_cli_is_blocked():
       r = subprocess.run(["aws", "sts", "get-caller-identity"], capture_output=True, text=True)
       assert r.returncode == 97
       assert "NO-REAL-AWS" in r.stderr
   ```

   Run it in the test shell, then delete it:

   ```bash
   python -m pytest ./backend/tests/unit/test_try_no_aws.py -p no:cov -q
   rm ./backend/tests/unit/test_try_no_aws.py
   ```

   `1 passed`: the `aws` the test reached was the fake, and it answered with exit 97.

2. Run the leak guard over your clone, then over the last few commit messages too:

   ```bash
   python3 scripts/leak_guard.py; echo "exit $?"
   python3 scripts/leak_guard.py --range HEAD~5..HEAD; echo "exit $?"
   ```

   On a clean clone both print `leak-guard: clean` and `exit 0`; the second also counts the commit
   messages it read.

3. Make an unsigned commit on a throwaway branch, check it, and throw the branch away:

   ```bash
   git switch -c try-dco
   git commit --allow-empty -m 'test: a commit with no sign-off'
   python3 scripts/check_dco.py --range HEAD~1..HEAD; echo "exit $?"
   git switch -
   git branch -D try-dco
   ```

   The check prints `MISSING` with the commit, explains how to fix it with `git rebase --signoff`, and
   exits 1. Now check a real one: `python3 scripts/check_dco.py --range 522fdc99~1..522fdc99` says the
   commit carries a matching sign-off.

4. Ask your AI tool: "I want every code path to get the repairs that `backend/app/db/bootstrap.py`
   makes. Where should they go?" If it proposes a shared place, ask it to list every caller of that place
   and what each would now get. Compare with the section above.

## Go deeper

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

# Chapter 5 — Checking an agent's claims

**Outcome:** you can tell evidence from assertion in an agent's report, and you know four ways a check an
agent ran can mislead you even when it really ran.

## A green run is the weakest evidence

An agent's report usually ends "all tests pass". That is a true statement about one environment: the one
the agent was sitting in. `CLAUDE.md` explains why that is the environment least worth trusting:

```text
# 📁 CLAUDE.md
Three consecutive review rounds on one pull request each shipped a regression
created by the previous round's fix. Every one of them was invisible locally
and obvious in CI, for the same reason: **the developer environment is the one
place those bugs cannot appear.** The venv has every package installed, the
tree has both profiles present, the database is already migrated. CI, the
container and production have none of those properties.

So: a green local suite is the weakest evidence available. It says the code
works in the environment least like the one it runs in.
```

The principal engineer's brief turns that into one rule for every claim:

```text
# 📁 .claude/agents/principal-engineer.md
So: **do not accept a claim about behaviour without an execution.** Open the
file. Run the command. Check whether the hits are code or comments. If a claim
cannot be checked cheaply, say so and mark it an assumption the plan must state
rather than rely on.
```

This chapter covers four ways a claim can survive an execution and still be wrong.

## 1. It ran in the wrong place

`CLAUDE.md` names three places where "it works here" does not carry over:

```text
# 📁 CLAUDE.md
- Before claiming a dependency change is safe, install **exactly** what the
  target installs. `backend/requirements.txt` is what every test job and
  developer venv gets; `backend/requirements/runtime.lock` is what the image
  gets; `modules/requirements.txt` is what the full image adds. A pin that
  moves between them changes which environments can import it.
- Before claiming a profile works, build the tree that profile actually ships:
  `scripts/core_build.sh` (a copy with `modules/` deleted), not the checkout
  with an environment variable set. Nothing under `backend/` reads
  `EXPERIMENTLY_PROFILE`; the profile is decided by whether `import modules`
  succeeds.
- Before claiming a migration works, rehearse the **transitions**, not the
  states: a core database met by a full image, a full database met by a core
  image, and a database at the previous release's revision upgraded with
  `heads`. Print the `alembic_version` rows and the affected constraints after
  each. `db/bootstrap.py` repairs things the raw `alembic upgrade heads` that
  production runs does not.
```

Each bullet is the same question: what is true in my environment that is not true where this runs? The
plan's ENVIRONMENTS heading (Chapter 3) asks it in advance. When an agent reports a result, ask it after:
where did you run this, and how does that place differ from CI?

## 2. The flag did not do what its name says

An agent picks flags by their names, as people do. `CLAUDE.md` keeps a list of names that lied:

```text
# 📁 CLAUDE.md
Almost every wrong thing written into the P3.5/P3.6 gates came from believing a
name instead of running the thing. In one pass, all of these were false:

- `pip-audit --no-deps` does **not** stop pip resolving. 75 pins in
  `backend/requirements.txt` audited **179** packages, including one the file
  does not pin. `--disable-pip` is what stops it, and pip-audit rejects that
  flag on an unhashed file unless `--no-deps` is *also* given.
- `--strict` did nothing, because the exit status was discarded. A package
  pip-audit cannot resolve comes back as `skip_reason` with no `vulns`, so the
  gate printed "no advisories" over a package it never audited.
- `rm -rf` on a path that does not exist **exits 0**. Every cleanup line in a
  Dockerfile fails open unless something afterwards asserts the result.
[...]
So: before a flag, a default or an absence-of-error becomes load-bearing, run
it once and look. One command is cheaper than the review round that finds it.
```

The habit is small. When an agent's change depends on what a flag does, ask for one run of that flag
against a case where the answer is known.

## 3. The search came back empty

An empty search is the most convincing wrong evidence there is. The principal engineer's brief lists one
case, and `CLAUDE.md` tells it in full:

```text
# 📁 CLAUDE.md
Before deleting a file, four negative checks said the repository root
`package.json` was unused: no `git grep` hit, no lockfile beside it, no
`npm ci` at the root in any workflow, no `/` entry in `.github/dependabot.yml`.
Every one of them was true. CI failed anyway —
`frontend/src/tests/modules-alias.test.ts` opens it at a path built from
`REPO_ROOT`, asserting the repo root declares no module `"type"`, because
Turbopack takes the module format from the nearest package.json above
`modules/frontend/src`.
```

And then the rule:

```text
# 📁 CLAUDE.md
So a deletion is justified by **running the suites that could plausibly touch
the thing**, not by a grep that comes back empty. Ask what would read this
without naming it: a path composed from a root constant, a directory listing, a
glob, a framework resolving "the nearest X above Y", a fixture discovering
files. And note which suites you could not run locally — the frontend suite is
the one that caught this, and `make lint` never reaches it in a checkout with
no `frontend/node_modules`.
```

The other half of the problem is a search that comes back full. The brief's second example is a workflow
said to run `cdk deploy` because the string was found in it: "Both occurrences of that string are in
comments, one of which says the opposite. A grep count is not a finding." A hit is a place to look, not a
fact.

> **By the Way.** Lessons are claims too. At `09d815ea`, `git grep -l package.json` lists 87 files, and
> the test above is one of them: its line reads `path.join(REPO_ROOT, 'package.json')`. So a search
> *does* reach it. What no search tells you is that this hit, among 87, is about the file at the root.
> The lesson holds; it is worth knowing which part of it you checked.

## 4. The exit status was hidden

A command can fail and still print what success looks like. The commonest way is a pipe:

```text
# 📁 CLAUDE.md
Never pipe a command whose failure matters through `tail` or `head`: it
replaces the exit status with the pipe's, and a killed run then reads as
success. Three agents and I were each fooled by this in one session. Redirect
to a file and read the file.
```

Agents reach for `| tail` constantly, because it keeps the output short. Ask for `> run.log 2>&1`, then
`echo $?`, then a look at the file. Two more traps from the same section:

```text
# 📁 CLAUDE.md
A comment inside a `\`-continued command ends it. Explaining an `aws ecs
run-task` flag on its own line mid-continuation left the command running with
four arguments and the rest parsed as separate commands; actionlint's SC2215
caught it, and that code is not among the five the lint job suppresses. Put the
comment above the command.
```

```text
# 📁 CLAUDE.md
Never assert a single wall-clock timing. Three separate flaky gates in this
repository came from `assert duration < X` or `assert duration2 < duration1`;
take the best of several runs (what `timeit` does), or assert on the
deterministic thing the timing was standing in for.
```

## What a report should contain

The pull request template asks for the same thing this chapter does:

```text
# 📁 .github/pull_request_template.md
## How it was verified

<!-- The commands you actually ran and what they printed. For example:
     `make test-unit` → 4185 passed
     `npx playwright test --project=journeys` → 5 passed
     manual: logged in, created an experiment, saw it on /experiments -->
```

So when an agent reports, ask for three things: the command, where it ran, and what it printed,
including the exit status. Then ask for the fourth thing it will not offer: what it did not run. The pull
request in Chapter 9 ends with a "NOT VERIFIED" list of four items, each saying where the evidence will
come from instead ("CI is the evidence"). That list is the most useful part of the report.

## Try it

1. Watch a pipe hide a failure, then stop it hiding:

   ```bash
   bash -c 'false | tail -n 1; echo "exit $?"'
   bash -c 'set -o pipefail; false | tail -n 1; echo "exit $?"'
   ```

   The first prints `exit 0`, the second `exit 1`.

2. Watch a cleanup succeed at nothing:

   ```bash
   rm -rf ./no-such-dir; echo "exit $?"
   ```

   It prints `exit 0`.

3. Ask your AI tool: "Is the repository root `package.json` used by anything? Answer yes or no, and show
   the command that proves it." If it answers "no" after a search, show it this:

   ```bash
   grep -n "REPO_ROOT, 'package.json'" frontend/src/tests/modules-alias.test.ts
   ```

   Then ask it what other suites could read a file without naming it.

4. Ask your tool to run one test file and report. Then ask: "What was the exact command, where did it run,
   and what was the exit status?" Run the command yourself and compare. If your tool piped it through
   `tail`, run it again without.

## Go deeper

- [Before You Change Controlvar, Chapter 4](../before-you-change-controlvar/chapter-4-tests-and-gates.md):
  what a pull request must pass, and why a green local suite is the weakest evidence.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md): a claim ties to
  evidence, and evidence ties to an environment.
- [Build a Platform, Day 22](../build-a-platform-in-28-days/week-4-production.md): the testing strategy
  and the harness.

---

_Previous: [Chapter 4 — Briefing an agent](./chapter-4-briefing-an-agent.md). Next: [Chapter 6 —
Tamper-testing](./chapter-6-tamper-testing.md)._

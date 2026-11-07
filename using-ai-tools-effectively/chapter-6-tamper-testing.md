# Chapter 6 — Tamper-testing

**Outcome:** you can prove that a check works by breaking the thing it guards, in the place the check
runs, and you can tell a broken check from a bad probe.

## A check that passes for the wrong reason

When an agent writes a fix and its test together, both come from the same understanding of the problem.
If that understanding is wrong, the test is wrong in the same way, and it passes. Running the test again
proves nothing new. The only independent signal is to break the code on purpose and watch the test fail.
`CLAUDE.md` calls the alternative the most common defect in the repository:

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

Every example in that list passed. None of them would have been caught by running it again.

## Writing the tamper down

The QA role owns this. Its brief asks, for each property, for the defect it will plant:

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

You will find that table in Controlvar's pull requests. Pull request
[#722](https://github.com/Controlvar/controlvar/pull/722), which made a paused rollout schedule stay
paused while work was already running, has a section titled "Planted defects, each shown red, then
restored": one row per plant, with the failure it produced. The last row runs the new tests against the
old code, so the table also proves the tests detect the bug they were written for. Chapter 9 walks
through another such table, and the row its first version was missing.

Two rules make a tamper count:

- **It must plant something the gate can see.** Removing a line the test never reaches proves nothing.
- **It must be undone, and the undoing checked.** The pull request in Chapter 9 says of its plants:
  "each run where the gate runs, against my Postgres, then reverted; the tree was clean after each".

## A check you generate can die on the way

Agents write checks into files: a `grep` in a script, a pattern in a workflow, a command in an instruction
file. Each layer of quoting between the agent and the place the check runs can change it.

```text
# 📁 CLAUDE.md
That includes a check you *generate* rather than type. A grep written into four
instruction files through a Python f-string arrived double-escaped (`\\s` for
`\s`) and matched nothing — it would have reported "clean" for ever. Every
layer of quoting between you and the thing that runs is a place for the check
to die silently, so run the emitted command against a planted offender and
watch it fire.
```

The import check in `/fix-imports` (Chapter 2) is one of those `grep`s. The first Try it step below plants
an offender for it and runs both versions.

## Tamper where the gate runs

A tamper run on your laptop proves the gate works on your laptop:

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

The middle case is the dangerous one for agents. The gate "found" something locally, so the tamper
seemed to work. It worked because of what happened to be installed on that machine.

## When a tamper does not fire

Sometimes you plant a defect and the gate stays green. Before you decide the gate is broken, look at what
you planted:

```text
# 📁 CLAUDE.md
**And when a tamper does not fire, suspect the probe before the gate.** A
gate that looks broken is usually a bad probe — the example-key case above cost
a round-trip of believing gitleaks was rule-less again.
```

The "example-key case": the secret scanner deliberately ignores the sample key that AWS publishes in its
own documentation. A probe made of that key passes a scanner that is working perfectly.

## A gate that tests itself

The leak guard (Chapter 8) does not wait for someone to remember to tamper-test it. Its workflow plants
an offender on every run:

```yaml
# 📁 .github/workflows/leak-guard.yml
      - name: Prove the guard still fires
        # A gate whose rules have silently stopped matching reports "clean"
        # for ever and looks identical to a healthy one. So plant an offender
        # on every run and require the guard to reject it. This has caught a
        # double-escaped pattern in this repository before.
```

The step builds a scratch repository, writes a fake AWS account id into it (assembled from two halves, so
the workflow file itself contains no such id), and then fails the job if the guard accepts it:

```yaml
# 📁 .github/workflows/leak-guard.yml
          if python3 scripts/leak_guard.py --staged >/dev/null 2>&1; then
            echo "::error::leak-guard accepted a planted AWS account id -- the guard is not working"
            exit 1
          fi
```

When a check matters and its rules can rot, make the tamper part of the check.

## A new gate's first failures are evidence

The opposite mistake is to treat a new gate's first red run as the gate's fault and loosen it until it
passes. `CLAUDE.md` describes a sweep whose first run failed twice:

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

Its rules: read the evidence before fixing anything, fix the gate's precision first, report what is left,
and treat every exception you carve out as a new obligation:

```text
# 📁 CLAUDE.md
- **An exception you carve into an exclusion list is a new obligation.** The
  moment `.claude/agents` was kept, its files had to meet the same bar as the
  rest of the tree, and they did not. Re-run every sweep over whatever you just
  decided to keep.
```

Note which files failed: the agents' own instruction files. Instructions are content, and they leak and
drift like any other.

## Try it

1. Tamper-test the import check from `/fix-imports`, and a double-escaped copy of it. Plant one offending
   import, run both, remove the plant:

   ```bash
   dir=backend/app
   printf 'from app.models.metrics.metric import RawMetric\n' > "$dir/planted_import.py"
   grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'; echo "exit $?"
   grep -rnE '^\\s*(from|import) app\.' backend/ modules/ --include='*.py'; echo "exit $?"
   rm "$dir/planted_import.py"
   git status --short
   ```

   The first `grep` prints the planted line and `exit 0`. The second, with `\\s` for `\s`, prints nothing
   and `exit 1`: on a clean tree it would have said "clean" for ever. `git status --short` prints nothing.

2. Tamper-test the leak guard the way its workflow does, in a scratch repository, so nothing is planted in
   your clone:

   ```bash
   tmp=$(mktemp -d)
   mkdir -p "$tmp/scripts"
   cp scripts/leak_guard.py "$tmp/scripts/"
   (
     cd "$tmp" && git init -q -b main . &&
     hi=9876 && lo=54321098 &&
     printf 'role = "arn:aws:iam::%s%s:role/x"\n' "$hi" "$lo" > planted.py &&
     git add -A &&
     python3 scripts/leak_guard.py --staged
     echo "exit $?"
   )
   rm -rf "$tmp"
   ```

   The parentheses run the steps in a subshell, so your own shell never leaves the clone. It reports one
   finding, `aws-account-id`, in `planted.py`, and `exit 1`. Run
   `python3 scripts/leak_guard.py` in your clone for the other half: `leak-guard: clean` and exit 0.

3. Ask your AI tool to write a small test for any pure function in `backend/app/core/`. Then ask it to
   plant the defect the test guards, run the test, show the failure, and revert. If it cannot make its own
   test fail, the test was decoration.

4. Ask your tool to do the same for a check it did not write, such as one of the `grep` lines in
   `.claude/commands/`. Watch whether it plants something the check can actually see.

## Go deeper

- [Before You Change Controlvar, Chapter 4](../before-you-change-controlvar/chapter-4-tests-and-gates.md):
  the rule for gates, and a planted documentation edit that the claims check catches.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): CI, the required checks
  and tamper-tested gates.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md): the test that could
  not fail.

---

_Previous: [Chapter 5 — Checking an agent's claims](./chapter-5-checking-claims.md). Next: [Chapter 7 —
Reviewing agent work](./chapter-7-reviewing-agent-work.md)._

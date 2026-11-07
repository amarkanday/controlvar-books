# Chapter 5 — The process

**Outcome:** you know how changes are planned and reviewed in Controlvar, which parts of that apply to
your pull request, and the habits that make a review short.

## Plan, review, sign-off, then build

`CLAUDE.md` holds the maintainers' working agreement. It is written for Claude Code, the AI assistant the
maintainers work with, but it is plain prose, it is public, and its section "How work is done here:
plan, review, sign-off, then build" is the process every change beyond a single file goes through:

1. **Draft the plan with three drafting roles**, in parallel. `software-architect` writes the design
   and the spec, `qa-engineer` writes the verification and what is not verified, and `ux-designer`
   writes the side a user or reader sees: the dashboard, the demo apps, error messages, the SDK
   surface, the documentation. The author merges the three into one plan and says where they disagreed.
2. **`principal-engineer` pressure-tests the plan**: every factual premise is checked by running
   something.
3. **`engineering-manager` gives the final verdict**: scope, size, sequence, and what is irreversible or
   waits on a human. A rejection or a condition is not advice; it is addressed and the plan re-submitted.
4. **Only then is code written.**

All five review the plan, not the code. The roles are defined in `.claude/agents/`, one file each, and
each file is a brief you can read:

| Role | Its one question |
|---|---|
| `software-architect` | What is the contract, what are the parts and their boundaries, and which alternatives were rejected? |
| `qa-engineer` | For each thing this change claims, what would we see if it were false, and will a check see it where that check runs? |
| `ux-designer` | What does a person outside the repository see and do: a dashboard user, an SDK integrator, a self-hoster, a reader? |
| `principal-engineer` | Is every factual claim in this plan true, and how do we know? |
| `engineering-manager` | Is this the right work, in the right order, at the right size? |

Why so much ceremony? `CLAUDE.md` says: three review rounds in a row on one pull request each found a
defect in the previous round's fix, and twice it nearly shipped a silent outage. A plan reviewed before
the code is written is the cheapest place to find a false premise.

## The shape of a plan

Every plan has the same nine headings:

```text
# 📁 CLAUDE.md
GOAL          what this closes, and how you will know it is closed
PREMISES      every factual claim, each with what you ran to check it
DESIGN        what changes, and what deliberately does not
SPLIT         why this is one change and not several
SEQUENCE      what must be true before each step
ENVIRONMENTS  where it runs vs where you can verify it
IRREVERSIBLE  what needs a human at the moment it happens
VERIFICATION  per property: the gate, and the defect that proves it fires
NOT VERIFIED  what you could not check, and why
```

"The last line is mandatory and it is not an admission of weakness." It tells a reviewer where to look.

For a change with a **contract** (a new setting, a new gate, a new endpoint, a format other things
parse) the plan also carries a short spec: what it does, the contract with its values named, where it
runs, how it fails (the silent ways too), and for each property the defect that proves its check works.
`CLAUDE.md`'s example of naming values: `ALLOWED_HOSTS` accepts `example.com` and `*.example.com`, and
refuses `*example.com`, which matches nothing.

## The habits underneath

The process is a frame for a few habits. They apply to a one-line fix as much as to a new subsystem.

- **Check every premise by running something.** The principal engineer's brief says it in one line: "do
  not accept a claim about behaviour without an execution." Its examples are all premises that looked
  reasonable and were false, such as "the root `package.json` is unused": four separate searches agreed,
  and a test opened the file at a path it built at runtime.
- **Say what you did not verify.** A plan, a pull request description or a commit message that says "I
  could not run X, so CI is the first thing that will" points the reviewer at the risk.
- **Break a gate before you trust it** (Chapter 4).
- **A claim about behaviour needs a test, or it is written as intent.** A status code, a default or a
  command in prose is something an operator will follow.
- **Never pipe a command whose failure matters through `head` or `tail`.** The pipe replaces the exit
  status, and a failed run reads as success. Redirect to a file and read the file.
- **Know when to stop.** The merge bar: "Merge when a review round produces no finding that would break a
  deployment or lose data. Everything else becomes a tracked issue."

## What this means for your pull request

You are not expected to convene five reviewers. For a contributor the contract is `CONTRIBUTING.md`:
open an issue first for anything that is not small, so the shape is agreed before you spend an evening
on it; write a test that fails without your change; run `make lint && make test`; sign off your
commits; use a conventional title; and describe what breaks without the change, not only what you did.

The habits carry over directly. The pull request template's "How it was verified" asks for "the commands
you actually ran and what they printed": that is the plan's PREMISES and VERIFICATION in miniature. Its
"Risk and rollback" asks, for anything that touches authentication, migrations, the SDK contract or a
public API, what breaks if you are wrong and how to undo it. And keep it focused: `CONTRIBUTING.md` notes
that a refactor and a bug fix in one branch take three times as long to review, and a reverted refactor
takes the fix with it.

## Try it

1. Read the start of one role's brief:

   ```bash
   sed -n 1,20p .claude/agents/principal-engineer.md
   ```

2. Write a nine-line plan, one line per heading, for issue #1612: "the feature-flag list has no defined
   order across pages". The flag list API pages with `skip` and `limit`. Spend ten minutes, then compare
   with the answer below.

3. Check the plan's first premise by running something. Look for an `ORDER BY` in the generic list query
   before the fix, and after it:

   ```bash
   git show dbafee71~1:backend/app/crud/base.py | grep -n order_by
   echo "exit $?"
   grep -n order_by backend/app/crud/base.py
   ```

   Before the fix: no lines, and `exit 1`. After it: one line, the `order_by` that the fix added.

**One possible answer to step 2**, built from what the merged pull request says:

```text
GOAL          GET /api/v1/feature-flags/ pages in a stable order; closed when walking the pages
              returns every flag exactly once, even after a flag was updated
PREMISES      the list query has LIMIT/OFFSET and no ORDER BY (grep: no order_by in crud/base.py);
              created_at and id are NOT NULL and created_at is indexed, so no migration
DESIGN        CRUDBase gets list_order, empty by default (no other list changes);
              the flag CRUD sets ("created_at", "id")
SPLIT         one change: the order, and the documentation sentence that states it
SEQUENCE      the failing test first, then the fix
ENVIRONMENTS  the tests need PostgreSQL: the integration suite, locally with make db and in CI
IRREVERSIBLE  none
VERIFICATION  three regression tests; with the fix taken out they fail
NOT VERIFIED  how clients that page the list today behave when the order changes
```

## Go deeper

- [Production Engineering, Hour 11](../production-engineering/part-4-safe-change.md): changing
  Controlvar safely, one change across storage, API, dashboard, contract and profiles.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md): a release review
  from evidence to decision, and the test that could not fail.
- [Build a Platform, Day 24](../build-a-platform-in-28-days/week-4-production.md): tamper-tested gates.

---

_Previous: [Chapter 4 — Tests and gates](./chapter-4-tests-and-gates.md). Next: [Chapter 6 — A first
change, start to finish](./chapter-6-a-first-change.md)._

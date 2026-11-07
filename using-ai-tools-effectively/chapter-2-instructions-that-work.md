# Chapter 2 — Instructions that work

**Outcome:** you can tell a useful written instruction from a vague one, find the places where
Controlvar's instructions have gone stale, and keep your own instruction file true.

## The project's memory

An agent starts every session knowing nothing about your project. Whatever it should know, it has to
read. In Controlvar that is `CLAUDE.md`, at the root of the repository: 1,308 lines at commit `09d815ea`.
It holds four kinds of content:

- **Commands**: how to set up, run, test, lint and migrate, written to be pasted.
- **Conventions**: imports rooted at `backend.app`, Pydantic v2 patterns, which permission dependencies
  are async and which are not.
- **Implementation notes**: for each subsystem, the one function that owns a decision, and the wrong
  pattern to avoid.
- **The reasons**: sections with titles like "Verifying a change (read this before saying something
  works)", each built from an incident.

It is a working file, not a document written once. `git log -- CLAUDE.md` lists 28 commits at
`09d815ea`. Only 7 of them are `docs:` commits; the other 21 are fixes, features, tests and CI changes
that also changed what an agent needs to know.

## What makes an instruction work

Four properties separate an instruction an agent can follow from one it has to guess at. Each is in
`CLAUDE.md`.

**It runs as written.** A command with the real flags and paths, not a description of one:

```bash
# 📁 CLAUDE.md
# Database migrations. A full checkout has TWO heads -- the core chain and the
# `modules` branch -- so it is always `heads`, and a new revision has to name
# the head it extends (`alembic heads` prints both).
python -m alembic -c backend/app/db/alembic.ini upgrade heads
python -m alembic -c backend/app/db/alembic.ini heads
```

**It names the one place.** When a decision lives in one function, the instruction says which, and
names the tempting wrong version:

```text
# 📁 CLAUDE.md
- The tracking API stores the event's own name in `event_type` (e.g. `"purchase"`), while `EventService.track_conversion` stores `"conversion"`. **Every analysis path must match conversions with `backend/app/services/event_matching.py`** (`conversion_event_filter(metric.event_name)`), never `Event.event_type == "conversion"`.
```

An agent that writes a new results query has no reason to suspect that two writers store two different
values. Without this line it would have to find that out by reading every writer.

**It says what the other choice breaks.** "Do not" is weak on its own. An agent that does not know why has
nothing to go on when its case looks a little different. This one gives the mechanism:

```text
# 📁 CLAUDE.md
  - **Two heads is the steady state of a full checkout, not a problem**: the core chain and
    the `modules` branch are separate on purpose, so that deleting `modules/` leaves a
    consistent core chain. Never run `alembic merge` on them. The merge file lands in the
    *core* versions directory with `down_revision = (<core head>, 'modules_0001_rbac')`, and
    every core checkout -- which has no `modules_0001_rbac` -- then fails to load the script
    directory at all. Use `upgrade heads` (plural) and `--head <head>` on a new revision.
```

**It carries its story.** The later sections of `CLAUDE.md` are rules with the incident attached. The
plan-first rule, for example, cites the pull request whose three review rounds each found a defect in the
previous round's fix (Chapter 3). A story lets an agent, or a new person, apply a rule to a case the rule
did not name.

> **By the Way.** An instruction file is also read by people. `CLAUDE.md` is plain prose, so it doubles
> as a description of how the project actually works. [Before You Change Controlvar,
> Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md) reads its process section that way.

## Instructions go stale

A written instruction is a claim, and claims drift from the code they describe. Three examples, all true at
`09d815ea`.

**A cited script that never existed.** `CLAUDE.md` once told readers to verify the import convention with
a script and a test. It now says:

```text
# 📁 CLAUDE.md
4. **Verification**: `grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'`.
   No output means the convention holds, which is the state of `main`. There is
   no script and no dedicated test for this: `standardize_metrics_imports.py`
   and `backend/tests/unit/metrics/test_metrics_imports.py` were cited here for
   months and neither has ever existed in this repository.
```

An agent told to run a file that does not exist has two choices: stop, or write a file by that name and
run it. Either way the instruction has cost more than it saved.

**An index that drifted from what it indexes.** The slash command `/quality` was rewritten in the change
that moved the lint gate to ruff:

```text
# 📁 .claude/commands/quality.md
Execute formatting, linting and type checking. Ruff replaced black, isort and
flake8: one tool, configured in `[tool.ruff]` in `pyproject.toml`, the same
version in the venv, pre-commit and the `lint` CI job.
```

The README that lists the commands was not:

```text
# 📁 .claude/commands/README.md
/quality fix            # Apply formatting and sorting fixes
/quality black          # Run only black formatter
/quality mypy           # Run only type checking
```

The README was edited twice after that rewrite and kept these lines. An agent that reads the index first
will believe there is a `black` mode.

**An instruction that disagrees with a gate.** The commit template in `CLAUDE.md` (Chapter 1) and the
`/commit` command both end with a `Co-Authored-By` line and no `Signed-off-by` line. `CONTRIBUTING.md`
tells contributors to commit with `git commit -s`, and the `DCO` check, one of the twenty required ones,
refuses a pull request with an unsigned commit. An agent that follows only those two files writes a commit
that cannot merge. The same section also ends with "Push with `git push origin main`", while its first
paragraph says `main` is squash-merged from pull requests.

> **Watch Out!** None of these is hidden. Each one is found by reading two files side by side, or by one
> `grep`. But an agent pointed at one instruction acts on that instruction; nothing makes it compare the
> two unless you ask. Make the comparison a task.

## Keeping it true

`CLAUDE.md` has two sections about exactly this. The first is about the sentence beside the code:

```text
# 📁 CLAUDE.md
Code is checked by tests; the justification beside it is checked by nothing, and
it is written from intent before the thing is measured. In one PR: a
`MANIFEST.in` comment that named the wrong mechanism, a CodeQL header
describing an upload that had become conditional, "neither branch below would
accept it" about a mixed SPDX expression that was accepted, and "all three are
clean" about three files that are *empty* — not the same claim. Write the
comment after measuring, or write it as intent.
```

The second, "A claim about behaviour needs a test, or say it is intent", is about any sentence a reader
will act on. Its examples include a README that promised a status code only three routes returned, and it
ends (📁 `CLAUDE.md`):

> Operators follow prose. If a statement is about observable behaviour — a status code, a default, a
> command — either pin it with a test or write it as intent, not as fact.

For the user-facing documentation, Controlvar enforces the second rule with a tool. Every sentence of a
registered page has an entry in `docs/_claims/` that names the test holding it, and
`scripts/doc_claims.py check` refuses a page and its registry that disagree. Chapter 9's fix added one:

```toml
# 📁 docs/_claims/docs/guides/user-guide.md.toml
[[unit]]
text = "A draft that is not ready to start (it needs two variants, a control and a metric, and a split URL experiment needs a valid split_url_config) stays a draft until it is."
by = [
    "test:backend/tests/integration/services/test_scheduler_draft_validation.py::test_d1_a_one_variant_draft_stays_a_draft_until_it_has_two",
    "test:backend/tests/integration/services/test_scheduler_draft_validation.py::test_d2_a_draft_whose_metrics_were_removed_after_scheduling_stays_a_draft",
    "test:backend/tests/integration/services/test_scheduler_draft_validation.py::test_d3_a_valid_draft_starts_in_the_same_tick_as_one_that_cannot",
    "test:backend/tests/integration/services/test_scheduler_draft_validation.py::test_d7_a_split_url_draft_that_cannot_split_stays_a_draft",
]
```

`CLAUDE.md` itself has no registry. `docs/_claims/` holds one for each of the root files `README.md` and
`SECURITY.md`, and none for `CLAUDE.md`. Its truth depends on whoever edits it. Three habits keep an
instruction file close to true when an agent does much of the editing:

1. **Ask for the evidence with the edit.** When an agent adds a sentence about behaviour, ask for the
   command or test that shows it. If there is none, the sentence is written as intent.
2. **Correct in place, and say what was wrong.** The import section above does not just drop the dead
   script; it says the script never existed, so the next reader does not go looking for it.
3. **Read the instructions against the gates.** Every rule in the file that a check also enforces is a
   place where the two can disagree. The sign-off case above is one.

## Slash commands: instructions you reuse

A slash command is an instruction file for one repeated task. `.claude/commands/fix-imports.md` is a good
model. It gives the background, then the check, then what must *not* be changed, because an over-eager fix
is the likely failure:

```text
# 📁 .claude/commands/fix-imports.md
`from .base import Base` inside `backend/app/models/` is **not** this defect and
must not be "fixed": a relative import there resolves to
`backend.app.models.base`, the same module object as the fully-qualified path.
The hazard is only the wrong *root package*.
```

Its check is one `grep`, and it says what a hit means: "a hit is the defect". Chapter 6 tamper-tests that
`grep`.

## Try it

1. Ask your AI tool: "According to `CLAUDE.md`, how are migrations applied in a full checkout, and why is
   the word plural?" Check its answer against the lines it cites:

   ```bash
   grep -n 'TWO heads' CLAUDE.md
   grep -n 'steady state of a full checkout' CLAUDE.md
   ```

2. Find the index that drifted:

   ```bash
   grep -n black .claude/commands/README.md .claude/commands/quality.md
   ```

   The README offers `/quality black`; `quality.md` names black only as what ruff replaced.

3. Find the missing sign-off:

   ```bash
   grep -n -i -E 'signed-off|commit -s' CLAUDE.md .claude/commands/commit.md CONTRIBUTING.md
   ```

   Only `CONTRIBUTING.md` answers.

4. Prove an absence with history, not with a search of today's tree:

   ```bash
   git log --all --oneline -- '*standardize_metrics_imports.py'
   ls docs/_claims/*.toml
   ```

   The first prints nothing: no commit ever touched such a file. The second lists the registries for the
   root files: `README.md.toml` and `SECURITY.md.toml`, and nothing for `CLAUDE.md`.

5. Ask your tool to find one more sentence in `CLAUDE.md` that is false at your commit, and to give the
   command that proves it. Run that command yourself. Then judge the answer: did your tool run something,
   or did it reason its way to a claim?

## Go deeper

- [Before You Change Controlvar, Chapter 4](../before-you-change-controlvar/chapter-4-tests-and-gates.md):
  the claims registry and the other gates that read documentation, with a planted edit to watch it fail.
- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md):
  the process sections of `CLAUDE.md`, read as a contributor.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md): a claim ties to
  evidence, and evidence ties to an environment.

---

_Previous: [Chapter 1 — What the agents do, and what people keep](./chapter-1-who-does-what.md). Next:
[Chapter 3 — Plan before code](./chapter-3-plan-before-code.md)._

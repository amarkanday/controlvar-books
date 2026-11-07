# Chapter 2 — Instructions that work

At 22:39:46 UTC on 2 October 2026, pull request [#591](https://github.com/Controlvar/controlvar/pull/591)
merged release 0.17.0 into `main` as commit `dd072baf`. The push started the usual workflows, and among
them was Release Please. When Release Please creates a release, it calls the release workflow itself, the
one that builds the container images, signs them and publishes them. Fifteen seconds later, at 22:40:01,
a second run of that same release workflow started on the same commit. This one was dispatched by hand.
GitHub keeps both runs (the fifth exercise prints this list):

```text
2026-10-02T22:40:01Z	workflow_dispatch	Release	success
2026-10-02T22:39:48Z	push	Release Please	success
...
```

Both succeeded, which was the problem. Whoever dispatched the second run had done what `CLAUDE.md`, the
instruction file every agent in the repository reads first, told them to do. Its section on releases
said, at the time:

```text
- **The tag it pushes triggers nothing.** `release.yml` has a
  `workflow_dispatch` with a tag input for exactly this: `gh workflow run
  release.yml -f tag=vX.Y.Z`. A tag pushed by a human token DOES trigger
```

That is `CLAUDE.md` as it stood before commit `0d7a34c5`, from `git show 0d7a34c5~1:CLAUDE.md`. Its first
sentence is true: a tag pushed with GitHub's own token starts no workflow. The advice drawn from it was
false, because the release never depended on the tag. `release-please.yml` calls `release.yml` directly,
as a reusable workflow, whenever a release is created, and it already did so on 25 September, when
`CLAUDE.md` first appeared in the public history. For as long as the file had been public, it had told
every reader to build each release twice.

The correction came the next day. Pull request [#793](https://github.com/Controlvar/controlvar/pull/793),
merged at 20:37 UTC on 3 October as `0d7a34c5`, says what the second build cost: "Doing that builds the
release a second time and moves its image tags; 0.17.0 got two `release.yml` runs that way, and the first
signed build was orphaned." A signed build is the artefact the project's release documentation
(`docs/self-hosting/releases.md`) tells people how to verify. The instruction had been followed exactly,
which is the whole purpose of an instruction file, and that is why a wrong line in one does more harm
than a wrong line in an ordinary document.

This chapter is about writing instructions an agent can follow and keeping them true afterwards. It looks
at what `CLAUDE.md` holds, the four properties that make an instruction in it useful, the ways its
instructions go stale (with cases from its history, two of them still live at the book's pinned commit),
and the habits and tools that catch drift. Then it checks an illustrative edit line by line, the way you
would check one an agent proposes.

## The project's memory

An agent starts every session knowing nothing about your project. Whatever it should know, it has to
read, and in Controlvar the first thing it reads is `CLAUDE.md` at the root of the repository: 1,308
lines at `09d815ea`. The file holds four kinds of content. There are commands, written to be pasted: how
to set up, run, test, lint and migrate. There are conventions, such as imports rooted at `backend.app`
and which permission dependencies are async. There are implementation notes, which name, for each
subsystem, the one function that owns a decision. And there are reasons: sections with titles like
"Verifying a change (read this before saying something works)", each built from an incident.

It is a working file, edited far more often by changes to code than by changes to documentation.
`git log -- CLAUDE.md` lists 28 commits at `09d815ea`. Seven of them have a `docs` type, and #793 is one;
the other 21 are fixes, features, tests, CI changes and one chore that also changed what an agent needs
to know, and
carried the instruction with them in the same pull request. That is the healthy pattern. When `ac8c0159`
(#1604) changed what a rollout schedule allows, it added four numbered rules to the rollout section of
`CLAUDE.md` in the same diff, and its reviewer saw the code and the instruction together.

The release instruction is the other case. It described a workflow, and no change to that workflow ever
had to touch the paragraph. Nothing failed when the two disagreed. Most of `CLAUDE.md` is in that
position: read by every agent and checked by no test.

## What makes an instruction work

Four properties separate an instruction an agent can follow from one it has to guess at. Each is
somewhere in `CLAUDE.md`, and each is worth asking for when an agent proposes an edit to your own
instruction file.

The first is that it runs as written. An agent will paste a command exactly, so the command has to be
the real one, with the real flags and paths, in the order they are needed:

```bash
# 📁 CLAUDE.md
# Correct workflow for running tests:
cd "$(git rev-parse --show-toplevel)"
source venv/bin/activate
export APP_ENV=test
export TESTING=true
python -m pytest backend/tests/ -v
```

Notice the `cd` line. It makes the block correct wherever in the clone the reader happens to be, which
is the kind of detail a person supplies without thinking and an agent will not supply unless the
instruction does.

The second property is that it names the one place. When a decision lives in one function, the
instruction says which, and names the tempting wrong version beside it:

```text
# 📁 CLAUDE.md
- The tracking API stores the event's own name in `event_type` (e.g. `"purchase"`), while `EventService.track_conversion` stores `"conversion"`. **Every analysis path must match conversions with `backend/app/services/event_matching.py`** (`conversion_event_filter(metric.event_name)`), never `Event.event_type == "conversion"`.
```

An agent writing a new results query has no reason to suspect that two writers store two different
values in the same column. Without this line it would find out only by reading every writer, and the
obvious query, the one quoted as wrong, returns numbers that look plausible and undercount.

The third is that it says what the other choice breaks. "Do not" on its own is weak. An agent that does
not know why has nothing to go on when its case looks a little different from the one the rule names.
This one gives the mechanism in a parenthesis:

```text
# 📁 CLAUDE.md
- In tests, "admin" fixtures are usually superusers, which bypass every check. Test
  permissions with a non-superuser of each role, and create flags through the API when
  the owner matters (fixtures that write `owner_id` directly hide create bugs).
```

A superuser passes every permission check, so a permission test run as one can never fail. With that
reason in hand, an agent can apply the rule to a resource the line does not mention.

The fourth is that it carries its story. The later sections of `CLAUDE.md` are rules with the incident
attached, and the corrected release section is now one of them:

```text
# 📁 CLAUDE.md
  release. **Do not dispatch `release.yml` after a release.** A second run
  builds the release again and moves its image tags to the new digests; that
  happened to 0.17.0, two runs, and the first signed build was orphaned.
  `gh workflow run release.yml -f tag=vX.Y.Z` is only for re-running a release
  whose own `release.yml` run failed after the tag existed.
```

A story lets an agent, or a new person, apply a rule to a case the rule did not name. It also makes the
rule harder to delete by accident: a reader who wants to remove "do not dispatch" has to argue with
0.17.0 first. Not every line needs all four properties. "Use Python 3.11+" needs no story. The more an
instruction costs when it is wrong, the more of them it should have.

> **Note**
>
> **An instruction file is also read by people.** `CLAUDE.md` is plain prose, so it doubles as a
> description of how the project works today. [Before You Change Controlvar,
> Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md) reads its process sections that way,
> as a contributor would.

## Instructions go stale

A written instruction is a claim, and claims drift from the code they describe. The mechanism is simple.
A change to a workflow, a script or a command index is reviewed against its own tests, and none of those
tests reads the paragraph in `CLAUDE.md` that describes it. A test fails when the code is wrong. An
instruction that has become wrong fails nothing, so it waits for the first reader who acts on it.

The release instruction was one such case. Here are three more. The first has since been corrected; the
other two were still live at `09d815ea`.

### A cited script that never existed

`CLAUDE.md` once told readers to verify the import convention with a script and a dedicated test. The
slash command `/fix-imports` stopped citing them on 21 September (`34a7eaba`). `CLAUDE.md` now says:

```text
# 📁 CLAUDE.md
4. **Verification**: `grep -rnE '^\s*(from|import) app\.' backend/ modules/ --include='*.py'`.
   No output means the convention holds, which is the state of `main`. There is
   no script and no dedicated test for this: `standardize_metrics_imports.py`
   and `backend/tests/unit/metrics/test_metrics_imports.py` were cited here for
   months and neither has ever existed in this repository.
```

An agent told to run a file that does not exist has two choices: stop and report, or write a file by
that name and run it. The second is worse than it sounds, because the agent then reports that the check
passed, and the check is one it wrote a minute earlier. Notice also how the correction is written. It
keeps the dead names and says they never existed, so the next reader who remembers them stops looking.

### An index that drifted from what it indexes

The slash command `/quality` was rewritten on 12 September (`7c013348`), in the change that moved the
lint gate to ruff:

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

The README was edited twice after that rewrite, on 21 and 30 September, and both edits kept these lines.
An agent that reads the index first will believe there is a `black` mode. Indexes drift this way because
the edit that changes a command is made in the command's own file, and the index lives somewhere the
author of that edit has no reason to open.

### An instruction that disagrees with a gate

The commit template in `CLAUDE.md` (Chapter 1) and the `/commit` command both end with a
`Co-Authored-By` line and no `Signed-off-by` line. `CONTRIBUTING.md` tells contributors to commit with
`git commit -s`, and the `DCO` check, one of the twenty required ones, refuses a pull request with an
unsigned commit. An agent that follows only the first two files writes a commit that cannot merge. The
same section of `CLAUDE.md` ends with "Push with `git push origin main`", while its own first paragraph
says that `main` is squash-merged from pull requests.

This kind is the cheapest to find, because the gate is the evidence. A red `DCO` check on an agent's
pull request reports a contradiction between two instruction files, and the lasting fix belongs in the
files.

> **Warning**
>
> **None of these is hidden.** Each one is found by reading two files side by side, or by one `grep`. An
> agent pointed at one instruction acts on that instruction, and nothing makes it compare the file with
> another unless you ask. Make the comparison a task of its own, and give it the pair to compare.

## Keeping it true

Two sections of `CLAUDE.md` address the problem. The first is about the sentence written beside the
code:

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

The second widens the rule to any sentence a reader will act on:

```text
# 📁 CLAUDE.md
### A claim about behaviour needs a test, or say it is intent
...
statement is about observable behaviour — a status code, a default, a command
— either pin it with a test or write it as intent, not as fact.
```

For the user-facing documentation, Controlvar enforces the second rule with a tool. Every sentence of a
registered page has an entry in `docs/_claims/` that names what verifies it, a test or a recorded
exemption, and `scripts/doc_claims.py check` refuses a page whose registry disagrees with it. Chapter 9's
fix added one entry, and a later change extended it. At `09d815ea` it reads:

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

One sentence, four tests. If the behaviour changes, a test fails; if the sentence changes, the registry
no longer matches it. `CLAUDE.md` has no registry: the root files with one are `README.md` and
`SECURITY.md`. One test does read it, though, without naming it.
`backend/tests/unit/docs/test_api_docs_links.py` walks the repository's Markdown pages and refuses a link
to the API docs at their old address, and `CLAUDE.md` is one of those pages. The description of #793
shows the test working on that file. After moving the line to the new address, the author put the old
line back as a probe:

```text
- I appended the old `- API Documentation: http://localhost:8000/docs`
line to CLAUDE.md as a test:
`test_no_page_links_to_the_api_docs_at_the_old_path` failed on
`CLAUDE.md:1273` (exit 1). I then removed it.
```

That is from the commit message of `0d7a34c5`. The test catches one kind of mistake in 1,308 lines. The
rest of the file stays true only through the habits of whoever edits it, and when an agent does much of
the editing, three habits carry most of the weight.

Ask for the evidence with the edit. When an agent proposes a sentence about behaviour, ask for the
command that shows it. The description of #793 is the model: it lists what was run, down to the probe
line it appended and the test that failed on it at `CLAUDE.md:1273`. If there is none, write the sentence as intent ("the release
workflow is meant to..."), so a reader can tell the difference.

Correct in place, and say what was wrong. The import section keeps the dead script's name and says it
never existed. The release section replaces the bad command with "do not dispatch" and names the release
that was built twice. A silent fix leaves the old instruction alive in every reader's memory and every
old copy of the file.

Read the instructions against the gates and the workflows. Every rule in the file that a check or a
workflow also encodes is a place where the two can disagree. The sign-off case is one; the release
instruction was another, and one `grep` of `release-please.yml` would have found it on the first day.

> **Note**
>
> **Corrections drift too.** Pull request #793 set the dashboard's line to read "`npm run dev` =
> `next dev -p 3100`". Nine days later, commit `3bf2ae71` (#1154) changed that script in
> `frontend/package.json` to `next dev -p 3100 -H 127.0.0.1`. The port in `CLAUDE.md` is still right; the
> command quoted beside it no longer matches the script exactly. The correction was true when it was made,
> and nothing tied it to the file it described.

## Checking a proposed edit

Here is an edit of the kind an agent might propose for the quick-reference part of an instruction file.
It is illustrative: no agent wrote it. Three of its four claims stood in `CLAUDE.md`, in some form, until
`0d7a34c5` corrected them; the fourth is in the file today.

```text
Development quick reference
- `make lint` runs ruff, mypy and the frontend linters; keep it clean.
- The dashboard's dev server is at http://localhost:3000.
- API documentation: http://localhost:8000/docs
- Apply migrations with `python -m alembic -c backend/app/db/alembic.ini upgrade heads`.
```

Each line is a claim about behaviour, so each needs an execution. Start with the first, which a reader
would act on every day. If `make lint` runs mypy, the string is in the Makefile, the workflows or the
pre-commit configuration:

```bash
grep -rn mypy .github/workflows Makefile .pre-commit-config.yaml; echo "exit $?"
```

```text
exit 1
```

No hits, and grep's exit status 1 says so. This is a negative search, the kind Chapter 5 warns about, so
ask what could run mypy without naming it here. The `lint` target's recipe lists its commands one by one
(ruff, eslint, tsc, hadolint, actionlint and the rest), and the same search over `scripts/` finds the
word only in a cache directory's name. The claim is false, and `CLAUDE.md` now says mypy is "NOT gated,
NOT clean (#788)".

The second line names a port. The dev server's command is in the dashboard's package file:

```bash
grep -n '"dev"' frontend/package.json
```

```text
7:    "dev": "next dev -p 3100 -H 127.0.0.1",
```

Port 3100, not 3000. A reader who opened `localhost:3000` would find nothing listening, or something
else entirely.

The third line is about a route. The application declares where its documentation lives:

```bash
grep -n -E 'docs_url|/api/v1/docs' backend/app/main.py
```

```text
184:    docs_url=None,
360:@app.get("/api/v1/docs", include_in_schema=False)
```

`docs_url=None` turns off the framework's default page at `/docs`, and line 360 serves the documentation
at `/api/v1/docs` instead. In Controlvar's own repository this line would never have reached review,
because the stale-link test above refuses it in any Markdown page.

The fourth line holds. `upgrade heads`, plural, is what `CLAUDE.md` gives in every command it shows, and
it warns that the singular fails. The plural is deliberate: the migration history has two heads, one for
the core and one for the optional modules. A review that marked this line as suspect too would be harder
to act on than one that trusted every line.

So the reply to the agent is short. Keep the fourth line. Replace the first with "`make lint` runs what
the `lint` CI job runs; mypy is not part of it", the second with port 3100, and the third with
`/api/v1/docs`, and attach the three commands to the pull request as the evidence for each.

## Slash commands: instructions you reuse

A slash command is an instruction file for one repeated task. `.claude/commands/fix-imports.md` is a good
model of the form. It gives the background, then the check, then what must not be changed, because an
over-eager fix is the likely failure for a task like this:

```text
# 📁 .claude/commands/fix-imports.md
`from .base import Base` inside `backend/app/models/` is **not** this defect and
must not be "fixed": a relative import there resolves to
`backend.app.models.base`, the same module object as the fully-qualified path.
The hazard is only the wrong *root package*.
```

Without that paragraph, an agent asked to fix imports can "fix" every relative import it finds,
producing a large diff that changes nothing and hides the one line that mattered. The command names the
exception and the reason for it, so the agent can tell the two cases apart. Its check is one `grep`, and
it says what a hit means: "a hit is the defect". Chapter 6 tamper-tests that `grep`.

## Try it

Each step needs only a clone of Controlvar and git; the fifth also needs the GitHub CLI, signed in. The
output was taken at `09d815ea` on macOS.

1. Read the release instruction as it stood before the correction, and the workflow line that made it
   false, both at the same commit:

   ```bash
   git show 0d7a34c5~1:CLAUDE.md | grep -n -A2 'The tag it pushes triggers nothing'
   git show 0d7a34c5~1:.github/workflows/release-please.yml | grep -n 'uses: ./'
   ```

   ```text
   1102:- **The tag it pushes triggers nothing.** `release.yml` has a
   1103-  `workflow_dispatch` with a tag input for exactly this: `gh workflow run
   1104-  release.yml -f tag=vX.Y.Z`. A tag pushed by a human token DOES trigger
   57:    uses: ./.github/workflows/release.yml
   ```

   The contradiction sat in two files of one commit, a `grep` apart.

2. Find the index that drifted:

   ```bash
   grep -n black .claude/commands/quality.md .claude/commands/README.md
   ```

   ```text
   .claude/commands/quality.md:3:Execute formatting, linting and type checking. Ruff replaced black, isort and
   .claude/commands/quality.md:10:- ruff format: code formatting (the black replacement, 88 columns)
   .claude/commands/README.md:102:/quality black          # Run only black formatter
   ...
   ```

   The README offers `/quality black`; `quality.md` names black only as what ruff replaced.

3. Find the missing sign-off:

   ```bash
   grep -n -i -E 'signed-off|commit -s' CLAUDE.md .claude/commands/commit.md CONTRIBUTING.md
   ```

   ```text
   CONTRIBUTING.md:27:git commit -s -m "Fix flag evaluation for empty targeting rules"
   CONTRIBUTING.md:33:Signed-off-by: Your Name <your.email@example.com>
   ```

   Only `CONTRIBUTING.md` answers.

4. Prove an absence with history, which a search of today's tree cannot do, and list the claims
   registries for the root files:

   ```bash
   git log --all --oneline -- '*standardize_metrics_imports.py'; echo "exit $?"
   ls docs/_claims/*.toml
   ```

   ```text
   exit 0
   docs/_claims/README.md.toml
   docs/_claims/SECURITY.md.toml
   ```

   The first command prints no commits and exits 0: no commit on any branch ever touched such a file. The
   second shows registries for `README.md` and `SECURITY.md`, and none for `CLAUDE.md`.

5. See the two builds of 0.17.0 for yourself:

   ```bash
   gh run list -R Controlvar/controlvar --commit "$(git rev-parse dd072baf)" --limit 50 \
     --json name,event,createdAt,conclusion \
     --jq '.[] | select(.name|test("^Release")) | [.createdAt,.event,.name,.conclusion]|@tsv'
   ```

   ```text
   2026-10-02T22:40:01Z	workflow_dispatch	Release	success
   2026-10-02T22:39:48Z	push	Release Please	success
   2026-10-02T22:39:48Z	push	Release Gate	success
   ```

   Release Gate runs the backend's unit and smoke checks and publishes nothing. The other two each
   built the release.

6. Ask your AI tool to find one more sentence in `CLAUDE.md` that is false at your commit, and to give
   the command that proves it. Run that command yourself. Then judge the answer by what it rests on: a
   command your tool ran, or a conclusion it reasoned its way to.

## Summary

An instruction file is read as instructions, so a wrong line in it is acted on with the same care as a
right one; 0.17.0 was built twice because `CLAUDE.md` said to dispatch a workflow that already ran by
itself. The instructions that work run as written, name the one place a decision lives, say what the
other choice breaks and carry the incident that produced them. They go stale because nothing that changes
the code they describe has to read them, and they stay true through evidence attached to each edit,
corrections that say what was wrong, and a deliberate comparison with the gates and workflows. Chapter 3
moves from the instructions an agent reads to the plan it writes before any code.

## Further reading

- [Before You Change Controlvar, Chapter 4](../before-you-change-controlvar/chapter-4-tests-and-gates.md):
  the claims registry and the other gates that read documentation, with a planted edit to watch it fail.
- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md):
  the process sections of `CLAUDE.md`, read as a contributor.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md): a claim ties to
  evidence, and evidence ties to an environment.

---

_Previous: [Chapter 1 — What the agents do, and what people keep](./chapter-1-who-does-what.md). Next:
[Chapter 3 — Plan before code](./chapter-3-plan-before-code.md)._

# Chapter 4 — Briefing an agent

At 08:17 UTC on 7 October 2026, pull request [#1053](https://github.com/Controlvar/controlvar/pull/1053)
was opened to repair two of the role files in Controlvar's `.claude/agents/` directory.
`scenario-runner.md` briefs an agent to walk the dashboard in a browser, the way a user would;
`data-generator.md` briefs one to fill a running stack with realistic experiment data. Both start the same
way, by signing in, and both had been telling the agent to sign in wrongly. Their sign-in sent
`{"username": ...}` to `POST /api/v1/auth/login`, and the API takes an email address, so that body
"answers 422". The accounts they named, `admin@example.com` and, in the scenario runner's file, three more
like it, exist "on no stack (401)". The scenario runner was also told to compare its screenshots with
baselines in a `__screenshots__` directory under `frontend/tests/e2e/`, and that directory does not exist.

None of this was new. The data generator's file was added on 2 May 2026 (`a0b3696d`) and edited twice
before October, and its sign-in line survived both edits. Every walk either brief described began with a
step that could not succeed, and an agent following it had nothing else to correct it from: the brief is
the whole of what it is told. The pull request fixed both files, checked them by running what they say,
and merged at 08:57.

This chapter covers why each agent starts with nothing, what every brief must carry, why a path beats a
paste, and what Controlvar's role files ask of the agents that read them. It ends with a brief assembled
from #1053 and checked against the repository.

## Every agent starts empty

An agent remembers nothing from one call to the next. Each call starts from the prompt and from whatever
files the agent chooses to read, and when it returns, everything it worked out goes with it except what it
wrote down. `CLAUDE.md` builds Controlvar's review process around that fact:

```text
# 📁 CLAUDE.md
**Every reviewer is a fresh invocation with no memory of its previous round.**
It cannot recall the conditions it set last time. So:

- **Persist every verdict to disk the moment it arrives** — the plan's own
  directory, `reviews/v<n>-<role>.md`, with the numbered conditions verbatim.
```

`CLAUDE.md` also records why: the verdicts of the first round run this way were not kept where the next
round could find them, and the engineering manager had to ask for its own conditions back. The second
rule follows from the first:

```text
# 📁 CLAUDE.md
- **Pass prior reviews by path, not by paste.** A path cannot be paraphrased
  into something easier to satisfy.
```

When you paste, you summarise, and your summary is written by the person who wants the plan approved. A
condition that read "the test must fail on the old code, in CI's environment" could become "add a test"
(an invented example), and the reviewer, reading your paste, cannot tell. Given the path, it reads its own
words. Pull request #1053 shows the habit in public. Its description quotes the principal engineer's
condition word for word, with the cut marked, and then answers both halves of it:

```text
> llm_experiment_lifecycle.py:62 is a stale test, not a product defect.
Delete it. [...] If any offline realistic file joins the unit job, add
`backend/tests/realistic` to that job's `check_junit_skips.py` scope.
```

The `drafts/` and `reviews/` folders that hold such verdicts, like `DECISIONS.md`, live with the
maintainers' plans and are not in the repository (Chapter 1); the convention is the part that travels.

## What a brief carries

`CLAUDE.md` lists what every reviewer's prompt must carry:

```text
# 📁 CLAUDE.md
**What each prompt must carry:**

1. The path to the plan, and to any previous version.
2. The path to `DECISIONS.md`, with the settled items named — *"these are
   settled, do not re-litigate them, but DO judge whether the revision
   discharges them."* Reviewers will otherwise reopen founder decisions, which
   wastes a round.
3. Any facts discovered since the last round, so they are checked rather than
   assumed.
4. **The specific things you most want pressure-tested** — including the
   weaknesses you already suspect. Naming your own weak point gets it examined
   rather than missed; the Stream A red-main hazard was found because it was
   asked about directly.
5. Read-only constraints, explicitly: no writes, no AWS calls that create, no
   publishing.
```

Each item closes a gap that a fresh agent cannot close for itself. The first gives it the text to read
and, with the previous version, something to diff. The second keeps a reviewer with no memory from
reopening every question in view, and its second half, "DO judge whether the revision discharges them",
keeps settled from meaning unchecked; #1053 cites such a ruling by its place, "as ruled in the EM
verdict's section (c)". The third treats what you learned between rounds as a premise to check. In #1053
that was the names of the ten browser tools, given with the version of the `@playwright/mcp` package they
came from.

The fourth item is the one most often left out, because it means admitting a doubt, and `CLAUDE.md`'s
case for it is a real one: the Stream A red-main hazard "was found because it was asked about directly".
The fifth is about limits. Chapter 1 showed that an agent's tool list is not a sandbox, so a brief says in
words what the agent may not do.

## A role is a brief, and a brief goes stale

The roles live in `.claude/agents/`, but nothing about them needs a special tool. `CLAUDE.md` says what to
do when the tool in use cannot see them:

```text
# 📁 CLAUDE.md
They are defined in `.claude/agents/`. **If those names are not in your
available-agents list, they were added after your session started** — the list
is fixed at session start. Fall back to `general-purpose` and open the prompt
with "You are the ENGINEERING MANAGER. Read your brief first:
```

The prompt then names the role's file. A role is a file of instructions, and any agent that can read
files can be told to read it first and act as it says. It also has the weakness of every instruction
file: it describes the code as it was when someone wrote it. Pull request #1053 is that failure inside a
brief. After the fix, the scenario runner's file says which sign-in is refused, and which account must
never be used to check roles:

```text
# 📁 .claude/agents/scenario-runner.md
`admin@demo.com` is a superuser, and a superuser passes every permission check.
Walk anything about roles as `dev@demo.com`, `analyst@demo.com` and
`viewer@demo.com`, never as the admin.
...
For the API cross-checks, the same sign-in from the shell. The body is
`{"email", "password"}`; a `username` field is refused with 422:
```

It names the failure an agent will meet if it does the obvious wrong thing, so an agent that sees a 422
knows what it means. And it closes a trap unrelated to signing in: a walk run as a superuser passes every
permission check, so it proves nothing about roles. Chapter 6 opens on tests that fell into that trap.

Keep a brief true the way #1053 did: run the commands it tells an agent to run, against the code as it is
today, and when that needs something you do not have, say so. Under "Not verified", #1053 opens with "The
agents were not dispatched. I ran the commands they now describe, not the agents."

## What the briefs ask of the agent

The role files also tell the agent how to work, and two habits recur. The first is to open the file
before designing against it:

```text
# 📁 .claude/agents/software-architect.md
Read the request, the existing plan draft if there is one, `DECISIONS.md`, and
any prior reviews **by path**. Then read the code the design touches — do not
design against a file you have not opened. When your design depends on how a
tool behaves (a flag, a default, a parser), run it once and say what you ran;
a name is not a behaviour.
```

The second is to say "nothing" when there is nothing. `CLAUDE.md` tells a drafter with nothing to say for
a change that it "says so in one line; do not invent scope to fill a section", and the designer's brief
repeats it for its own section:

```text
# 📁 .claude/agents/ux-designer.md
**What does not change for the user.** If nothing user-visible changes, say so
in one line and stop — do not invent scope.
```

## A brief, assembled

Here is a principal engineer's brief for the second round of review of a plan like the one behind #1053.
It is illustrative: no such brief is in the repository, and the `plans/` directory is one you would keep
outside it. Every other path in it exists at `09d815ea`, and its facts come from the pull request.

```text
You are the PRINCIPAL ENGINEER. Read your brief first: .claude/agents/principal-engineer.md

Plan: plans/qa-agents/plan-v2.md. Previous version: plans/qa-agents/plan-v1.md.
Your verdict on v1: plans/qa-agents/reviews/v1-principal-engineer.md. Say, for each
of its numbered conditions, whether v2 meets it.

Settled, do not reopen, but judge whether v2 discharges them:
  - role walks use dev@demo.com, analyst@demo.com and viewer@demo.com; the admin
    is a superuser
New since v1, check these instead of accepting them:
  - the sign-in body is {"email", "password"} (LoginRequest, backend/app/schemas/auth.py)
  - the accounts are DEMO_USERS in backend/scripts/seed_demo_data.py
Pressure-test first:
  - I am least sure about scenario-runner's scenarios 1-4: their step labels are the old ones.

You are read-only: no file writes, no commits, no GitHub calls that create anything,
no AWS calls. Return your verdict in your brief's shape.
```

Save the answer unedited to `plans/qa-agents/reviews/v2-principal-engineer.md` before you act on it.

Now check the brief as its reader will. The lines to verify are the two under "New since v1", where an
author is most likely to be repeating a memory. Start with the sign-in body:

```bash
grep -n -A6 'class LoginRequest' backend/app/schemas/auth.py
```

```text
20:class LoginRequest(BaseModel):
21-    """Body for ``POST /api/v1/auth/login`` (local provider)."""
22-
23-    # Plain ``str`` (not EmailStr): a malformed address is simply an unknown
24-    # user (401), and local accounts such as ``admin@localhost`` stay usable.
25-    email: str = Field(..., min_length=1, max_length=320)
26-    password: str = Field(..., min_length=1, max_length=256)
```

Two fields, `email` and `password`, and the comment explains the old briefs' other status: an address the
API does not know is a 401. The accounts check the same way: `DEMO_USERS` starts at line 146 of the seed
script and lists the four `@demo.com` addresses. Keep the weak point after both checks pass. It is the one
thing a reviewer cannot find alone, and it comes from the pull request's own "Not verified" list.

## Try it

You need a clone of Controlvar, git and a shell. The output is what each command printed at `09d815ea` on
macOS. Steps 3 and 4 need an AI tool; they are prompts, and have no output to show.

1. Read the history of one brief:

   ```bash
   git log --format='%h %ad %s' --date=short -- .claude/agents/data-generator.md
   ```

   ```text
   92177409 2026-10-17 feat!: rename the project to Controlvar (#1277)
   a84620e1 2026-10-07 test(qa): the data generator converts users by the variant the server assigns, and its seed and dates are reproducible (#1064)
   381dd9ea 2026-10-07 test(qa): the QA agents use today's sign-in and seeds, and the offline realistic tests run in the unit job (#1053)
   6df9e101 2026-09-27 fix(test): the data seeder fails loudly and reports what the platform stored (#245)
   24cdb9e0 2026-09-13 P3.1: repeatable history export with the acceptance sweep (#133)
   a0b3696d 2026-05-02 Add EP-059 Claude QA agents and Playwright MCP server config
   ```

   Two edits came before #1053, and neither touched the sign-in. (The `#133` in the September subject is a
   number from the repository's earlier, private home; on the public tracker it means something else.)

2. Read the old brief's sign-in:

   ```bash
   git show 381dd9ea~1:.claude/agents/data-generator.md | grep -n -o '{"username": "[^"]*"'
   ```

   ```text
   55:{"username": "admin@example.com"
   ```

   Set it beside the `email` field from the worked example. Nothing went red while the two disagreed, from
   May to October.

3. Brief a reviewer with all five items on a plan of your own (the one from Chapter 3 will do) in a fresh
   session, and save its verdict unedited. Revise the plan to meet one condition and not another. Then ask
   two fresh sessions whether each condition is met, giving one the old verdict by path and the other your
   own one-line summary of it, and note which one caught the condition you left unmet.

4. Add one line to the brief, "I am least sure that ...", with your real weak point, and check whether
   the reviewer's first finding is about it.

## Summary

An agent knows what its brief tells it and what it reads for itself, and nothing from any earlier round.
In #1053, two role files told every agent that read them to sign in with a body the API refuses, and
nothing outside the files could correct them. So a brief carries the paths to the plan and its last
verdict, the settled decisions, what you learned since, your own weak point and the limits in words;
verdicts are saved as they arrive and passed on by path; and a brief is checked the way code is, by
running what it says. Chapter 5 turns to what comes back: the agent's report, and the claims in it.

## Further reading

- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md): the
  habits underneath the process, and what applies to a contributor's pull request.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md#hour-12--a-release-review-from-evidence-to-decision-the-test-that-could-not-fail):
  assembling a review packet in which every check is "passed", "failed" or "unverified".
- In this book, [Chapter 3](./chapter-3-plan-before-code.md) gives the plan these briefs point at, and
  [Chapter 6](./chapter-6-tamper-testing.md) opens on the superuser trap the scenario runner's brief now
  warns about.

---

_Previous: [Chapter 3 — Plan before code](./chapter-3-plan-before-code.md). Next: [Chapter 5 — Checking
an agent's claims](./chapter-5-checking-claims.md)._

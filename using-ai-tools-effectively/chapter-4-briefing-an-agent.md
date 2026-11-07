# Chapter 4 — Briefing an agent

**Outcome:** you can write a brief that gets a fresh agent to do one job well, and you know what to keep
on disk between rounds so that nothing depends on an agent's memory.

## Every agent starts empty

A person who reviewed your plan yesterday remembers what they asked for. An agent does not. Each call
starts from nothing but the prompt and the files it chooses to read. `CLAUDE.md` builds the review process
around that fact:

```text
# 📁 CLAUDE.md
**Every reviewer is a fresh invocation with no memory of its previous round.**
It cannot recall the conditions it set last time. So:

- **Persist every verdict to disk the moment it arrives** — the plan's own
  directory, `reviews/v<n>-<role>.md`, with the numbered conditions verbatim.
```

It learned this the hard way: the first round's verdict was not saved, and the manager had to ask for its
own conditions back. The second rule follows from the first:

```text
# 📁 CLAUDE.md
- **Pass prior reviews by path, not by paste.** A path cannot be paraphrased
  into something easier to satisfy.
```

When you paste, you summarise, and your summary is written by the person who wants the plan approved. A
condition that read "the test must fail on the old code, in CI's environment" becomes "add a test". The
reviewer, reading your paste, cannot tell. Given the path, it reads its own words.

The same applies to drafts. `CLAUDE.md` asks for each draft to be saved as `drafts/v<n>-<role>.md` and each
verdict as `reviews/v<n>-<role>.md`, so that the plan's whole history is files, not memory.

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

Each item closes a specific gap:

1. **Paths to the plan and its last version.** The agent reads the current text itself, and can diff it.
2. **What is settled.** A reviewer with no memory will reopen every question it can see. Naming the
   settled ones keeps the round on what is still open. Note the second half: "DO judge whether the
   revision discharges them". Settled does not mean unchecked.
3. **What you learned since.** A fact you found between rounds is a premise like any other. Give it to
   the reviewer to check, not to accept.
4. **Your weak points.** You know where you are least sure. The reviewer does not, unless you say. An
   agent asked to "review the plan" spreads its attention evenly; an agent asked "is the migration safe
   when the old image is still running?" looks there first.
5. **The limits, in words.** Chapter 1 showed that a tool list is not a sandbox. Say what the agent may
   not do.

## A role is just a brief

The roles live in `.claude/agents/`, but nothing about them needs a special tool. `CLAUDE.md` says what to
do when the tool in use cannot see them:

```text
# 📁 CLAUDE.md
They are defined in `.claude/agents/`. **If those names are not in your
available-agents list, they were added after your session started** — the list
is fixed at session start. Fall back to `general-purpose` and open the prompt
with "You are the ENGINEERING MANAGER. Read your brief first:
```

The prompt then names the role's file. That is the portable form of the whole idea: a role is a file of
instructions, and any agent that can read files can be told to read it first and act as it says.

## What the briefs ask of the agent

The role files also tell the agent how to work. Three habits recur.

**Open the file before designing against it.**

```text
# 📁 .claude/agents/software-architect.md
Read the request, the existing plan draft if there is one, `DECISIONS.md`, and
any prior reviews **by path**. Then read the code the design touches — do not
design against a file you have not opened. When your design depends on how a
tool behaves (a flag, a default, a parser), run it once and say what you ran;
a name is not a behaviour.
```

**Say "nothing" when there is nothing.** An agent given a section to fill will fill it. Both `CLAUDE.md`
and the designer's brief tell it not to:

```text
# 📁 CLAUDE.md
Persist every draft to `drafts/v<n>-<role>.md` and every verdict to
`reviews/v<n>-<role>.md`. A drafter with nothing to say for this change (no
user-facing effect, say) says so in one line; do not invent scope to fill a
section.
```

```text
# 📁 .claude/agents/ux-designer.md
**What does not change for the user.** If nothing user-visible changes, say so
in one line and stop — do not invent scope.
```

**Stay inside the change.** The code reviewer's brief ends with:

```text
# 📁 .claude/agents/reviewer.md
4. Do not suggest changes outside the scope of what was changed
```

A review that wanders produces findings nobody asked for, and each one becomes a round.

## A brief, assembled

Put together, a reviewer's brief looks like this. It is an illustration, not a file in the repository:

```text
You are the PRINCIPAL ENGINEER. Read your brief first: .claude/agents/principal-engineer.md

Plan: plans/flag-order/plan-v2.md. Previous version: plans/flag-order/plan-v1.md.
Your verdict on v1: plans/flag-order/reviews/v1-principal-engineer.md. Say, for each of
its numbered conditions, whether v2 meets it.

Settled, do not reopen, but judge whether v2 discharges them:
  - the list is ordered by creation time, then id
New since v1, check rather than accept:
  - created_at is NOT NULL on the flags table
Pressure-test first:
  - I am least sure that no client depends on today's order.

You are read-only: no file writes, no commits, no GitHub calls that create anything,
no AWS calls. Return your verdict in your brief's shape.
```

Then save the answer, as it arrived, to `plans/flag-order/reviews/v2-principal-engineer.md`.

## Try it

1. Take the `plan.md` you wrote in Chapter 3, or write a short one. Brief a reviewer with all five items,
   in a fresh session. Save its verdict to a file, unedited.

2. Revise the plan to meet one condition and not another. In a new fresh session, pass the old verdict
   **by path** and ask the reviewer to say, for each condition, whether it is met. Did it catch the one
   you left?

3. Now do the same with a third fresh session, but **paste your own one-line summary** of the
   conditions instead of the path. Compare the two answers. Which was stricter?

4. Add one line to the brief: "I am least sure that ...", with your real weak point. Check whether the
   reviewer's first finding is about it.

## Go deeper

- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md): the
  habits underneath the process, and what applies to a contributor's pull request.
- [Production Engineering, Hour 12](../production-engineering/part-4-safe-change.md): assembling a review
  packet in which every check is "passed", "failed" or "unverified".

---

_Previous: [Chapter 3 — Plan before code](./chapter-3-plan-before-code.md). Next: [Chapter 5 — Checking
an agent's claims](./chapter-5-checking-claims.md)._

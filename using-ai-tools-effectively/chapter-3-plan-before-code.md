# Chapter 3 — Plan before code

**Outcome:** you know why Controlvar plans every change beyond a single file before any code is written,
who drafts the plan and who reviews it, and what the plan must say.

## Why a plan comes first

An agent writes code quickly. That is the problem. A change built on a false premise is finished, tested
and described before anyone notices the premise, and then every fix is built on the same premise.
`CLAUDE.md` says what that cost here:

```text
# 📁 CLAUDE.md
**Why this exists.** Three consecutive review rounds on PR #236, each finding a
defect in the previous round's *fix*, twice nearly shipping a silent outage.
The plan-first instruction dates from 2026-09-22; the sign-off requirement was
added after a session in which it was not followed and the same traps were
walked into again -- a mandatory setting that would have failed the first
deploy, and a wildcard pattern that matches nothing and refuses all traffic
while the health checks stay green.
```

(That #236 is a pull request number from the repository's earlier, private home. On the public tracker,
#236 is an unrelated issue.)

The public pull request that added the reviewers, [#89](https://github.com/Controlvar/controlvar/pull/89),
says who did not follow the rule. Its description ends with the "Generated with Claude Code" footer, its
commit names a Claude model as co-author, and it speaks in the first person:

> The plan-first half of this is not new -- it dates from 2026-09-22, after PR #236 took three review
> rounds with each round finding a defect in the previous round's FIX. What is new is the sign-off, and
> it was added because I did not follow the weaker version. In the session this lands in I started
> executing without a plan and walked into two of the three traps that #236's write-up records by name:
> a setting made mandatory in production before the task definition carried it, which the repository's
> own regression test caught, and a `*example.com` wildcard that matches nothing and would refuse all
> traffic while the exempt health checks stayed green.

That is the lesson of the chapter in one paragraph. "Write a plan first" was already an instruction, and
it was skipped. What fixed it was not a stronger word in the instruction file. It was a step with a
reviewer in it: nobody writes code until the plan has a verdict.

## Who drafts, and who reviews

The process is five roles in order. `CLAUDE.md` gives it as four steps:

```text
# 📁 CLAUDE.md
1. **Draft the plan with the three drafting roles** (`.claude/agents/`), in
   parallel: `software-architect` writes DESIGN and SPEC, `qa-engineer` writes
   VERIFICATION and NOT VERIFIED, `ux-designer` writes the user- and
   reader-facing side (dashboard, demo apps, error messages, SDK surface,
   documentation). You merge their drafts into one document with the shape
   below, and say where they disagreed and what you chose.
2. **`principal-engineer` pressure-tests the merged plan**: every factual
   premise checked by running something.
3. **`engineering-manager` gives the final verdict**, with the principal
   engineer's findings in front of it: scope, size, sequence, what is
   irreversible or blocked on a human, and whether the principal engineer's
   conditions are met or accepted. A rejection or a condition is not advice;
   address it and re-submit. An APPROVED verdict, or APPROVED WITH CONDITIONS
   whose conditions you have met, is the gate.
4. **Only then write code.**
```

[Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md) has a
table of each role's one question. Here the interesting part is why the work is split across separate
agents at all, when one agent could write the whole plan.

**The author is a poor checker of its own premises.** The principal engineer's brief says why:

```text
# 📁 .claude/agents/principal-engineer.md
A plan's tests are the part most likely to be worthless, because the author
writes them from the mental model that is itself in doubt. Ask of each one:

- **Could it fail?** If the guard has only ever been seen to pass, it is not a
  guard. Demand that the plan says which defect it will plant and watch fail.
```

A second agent with a different brief reads the same plan with a different question. It has not built
the mental model, so it has nothing to defend.

**The drafting roles came from evidence.** They were added after two review rounds in which the author
wrote the plan alone:

```text
# 📁 CLAUDE.md
All five review the PLAN, not code. (Founder instruction, 2026-09-25: the
drafting roles were added after two rounds in which the author wrote the plan
alone and the reviewers each found a premise the author had not checked.)
```

**Each role answers in a fixed shape.** The architect's brief ends like this:

```text
# 📁 .claude/agents/software-architect.md
Return, in this shape:

    DESIGN        what changes, component by component
    SPEC          per contract: what / contract / where / failure modes
    ALTERNATIVES  rejected designs, one line of reason each
    NOT CHANGED   the edge of the blast radius
    PREMISES      every factual claim the design rests on, with what you ran
    OPEN          what you could not decide, and who should
```

A fixed shape does two jobs. Three drafts in the same shape can be merged. And an empty heading is
visible: an agent that has nothing under PREMISES has not checked anything, and you can see that without
reading the rest.

**One heading forces a cheaper idea.** The architect must name at least one design it rejected:

```text
# 📁 .claude/agents/software-architect.md
**The alternatives.** Name at least one design you rejected and the reason. The
cheapest review finding is "a dumber thing works" — find it before the
reviewers do. CLAUDE.md: *prefer the dumbest thing that works*.
```

## The plan's shape

The merged plan has nine headings:

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

The last line is mandatory and it is not an admission of weakness. "This venv
has no `constructs._jsii`, so CI is the first thing that runs the synth suite"
is the sentence that tells a reviewer where to look.
```

Read the headings as a list of the ways agent work goes wrong. PREMISES is there because agents assert.
ENVIRONMENTS is there because they verify where they happen to be (Chapter 5). VERIFICATION asks for "the
defect that proves it fires" because a check seen only passing proves nothing (Chapter 6). SPLIT is there
because one large change takes more review rounds than two small ones (Chapter 7). IRREVERSIBLE is the
list from Chapter 1. And NOT VERIFIED is the one an agent is least inclined to write, so it is the one
the process insists on.

### A spec, when there is a contract

When a change adds something other code depends on (a setting, an endpoint, a gate, a format), the plan
also carries a short spec:

```text
# 📁 CLAUDE.md
* **What** it does, in one paragraph.
* **The contract**: inputs, outputs, defaults, and what is refused. Name the
  values, not the shapes: `ALLOWED_HOSTS` accepts `example.com` and
  `*.example.com`; it refuses `*example.com`, which matches nothing.
* **Where it runs** and what must already be true there.
* **Failure modes**, including the silent ones. What does it look like when
  this is misconfigured rather than broken?
* **Verification**: for each property, the defect that proves the check works.
```

The `*example.com` line is the second trap from the story above, turned into an example.

## Verdicts are gates, not advice

The two reviewers each end with a verdict: `APPROVED`, `APPROVED WITH CONDITIONS` or `REJECTED`, and a
numbered list of conditions. Both briefs say what to reject:

```text
# 📁 .claude/agents/engineering-manager.md
Reject plans that cannot be checked. A plan with no verification strategy, or
whose verification is "run the tests", is not yet a plan.
```

```text
# 📁 .claude/agents/principal-engineer.md
Reject a plan whose premises you could not verify. "I could not check X, and
the plan depends on it" is a finding, not a gap in your review.
```

And `CLAUDE.md` tells the author how to take a rejection:

```text
# 📁 CLAUDE.md
**Expect rejection, and do not treat it as failure.** Across two rounds the
reviewers caught a required check that was green for the wrong reason, a
documentation tag that destroys the fence it annotates, a deploy that reports
healthy while rejecting every request, a migration that can run production's
image against staging, and a release gate no tag can satisfy. Each of those
was cheaper to find in review than in production.
```

Chapter 9 follows one plan through this. The issue asked for a dashboard screen; the plan review measured
that the server already did something the screen would promise it did not, and the server fix was split
out and shipped first.

## Try it

1. Read the two verdict shapes side by side:

   ```bash
   sed -n '/^## Verdict/,$p' .claude/agents/principal-engineer.md
   sed -n '/^Then produce a verdict/,$p' .claude/agents/engineering-manager.md
   ```

   One asks whether the claims are true. The other asks whether this is the right work, in the right
   order, at the right size. Neither asks whether the code is good: there is no code yet.

2. Pick a small open issue on Controlvar's public tracker, one labelled `bug`. Ask your AI tool for a plan
   with the nine headings, and tell it not to write code. Save the answer as `plan.md` outside the clone.

3. Start a fresh session. Give it this, and nothing else: "Read `.claude/agents/principal-engineer.md`
   and follow it. Review the plan in `plan.md` against this repository. You are read-only: do not change
   any file." Count the premises it checked by running a command, and the ones it checked by reading
   alone.

4. Look at the plan's NOT VERIFIED section. If it is empty, or says "none", send the plan back. Every real
   change has something its author could not check.

## Go deeper

- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md): each
  role's one question, the habits underneath, and a nine-line plan for a real issue.
- [Production Engineering, Hour 11](../production-engineering/part-4-safe-change.md): changing Controlvar
  safely, one change across storage, API, dashboard, contract and profiles.

---

_Previous: [Chapter 2 — Instructions that work](./chapter-2-instructions-that-work.md). Next:
[Chapter 4 — Briefing an agent](./chapter-4-briefing-an-agent.md)._

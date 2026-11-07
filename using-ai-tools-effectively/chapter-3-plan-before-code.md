# Chapter 3 — Plan before code

At 06:13 UTC on 25 September 2026, pull request [#89](https://github.com/Controlvar/controlvar/pull/89)
was opened against Controlvar with the title "docs: plan, review and sign-off before building". An agent
wrote it: the description ends with the Claude Code footer, and the commit it was merged as names a Claude
model as co-author. Most pull requests describe a change. This one also described a failure, in the first
person, and the failure was the agent's own:

```text
The plan-first half of this is not new -- it dates from 2026-09-22, after PR
#236 took three review rounds with each round finding a defect in the previous
round's FIX. What is new is the sign-off, and it was added because I did not
follow the weaker version. In the session this lands in I started executing
without a plan and walked into two of the three traps that #236's write-up
records by name: a setting made mandatory in production before the task
definition carried it, which the repository's own regression test caught, and a
`*example.com` wildcard that matches nothing and would refuse all traffic while
the exempt health checks stayed green.
```

(That #236 is a pull request number from the repository's earlier, private home. On the public tracker,
#236 is an unrelated issue.)

Both traps are quiet until the moment they are not. The first is about order. Controlvar's API refuses
to start in production when a required setting is missing, and in production its settings come from the
task definition that the infrastructure code builds. Ship code that demands a new setting before the task
definition supplies it, and the first deploy starts containers that refuse to serve a request. The second
is about a value. A host allow-list entry written `*example.com`, without the dot after the asterisk, is
a literal hostname containing an asterisk, and it matches nothing. Every request is refused. The load
balancer's health probes are exempt from the check, so they pass, and the deploy reports itself healthy
while serving nobody.

Neither trap was new. Both had been found once already, in review of an earlier pull request, and
written up by name. A rule to plan first had been in force for three days. The agent had the knowledge and
the rule, skipped the plan, and fell into the traps anyway. A test caught the first, and the description
puts the second in the conditional: it "would refuse all traffic". What changed afterwards was the
process. Pull request #89 gained four more commits that day, among them "a blocking question goes to the
team, not to a halt" at 07:07 and "three drafting roles, and the manager's final verdict" at 15:27, and
merged at 20:53. From then on the rule has been that no code is written for a change beyond a single file
until the plan has a verdict.

This chapter explains that step: why a plan comes first when an agent does the building, who drafts it
and who reviews it, the nine headings it must fill, and the short spec it carries when the change has a
contract. Along the way it writes the spec that would have caught the second trap, from the code that now
refuses it, and checks it with commands.

## Why a plan comes first

An agent writes code quickly. That is the problem a plan solves. A change built on a false premise is
finished, tested and described before anyone notices the premise, and then every fix is built on the
same premise. The tests agree with the code, because the same mental model wrote both. The description
agrees with the tests. A reviewer reading the result sees a consistent piece of work, and has to find the
false premise inside it without being told where to look. `CLAUDE.md` records what that cost:

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

A plan moves the premises to the front, written down and separate from the code, where they can be
checked one at a time. It is also cheap to change. Rejecting a plan costs the minutes it took to write;
rejecting a pull request costs the build, the tests, the description and a review round, and the next
round reviews a fix written under pressure. The three rounds on #236 are the expensive version of that
arithmetic.

The first trap shows how directly a plan can catch a defect. The engineering manager's brief now names
it as a question every plan is asked:

```text
# 📁 .claude/agents/engineering-manager.md
**Is the order right?** A plan that removes the old mechanism before the new one
is proven, or that makes a setting mandatory before the thing that supplies it
exists, is a sequencing bug. Ask what must be true before each step, and whether
the plan establishes it or assumes it.
```

Written as a plan, the change would have had a SEQUENCE line saying "the task definition carries the
value, then the code requires it". A reviewer reading that line would have seen the problem. In code,
the same ordering is spread across a settings validator and an infrastructure stack, and nothing on the
screen shows which one ships first.

## Who drafts, and who reviews

The process is five roles in a fixed order. `CLAUDE.md` gives it as four steps:

```text
# 📁 CLAUDE.md
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

The first step, cut from the listing above for length, has the three drafting roles write in parallel:
`software-architect` the DESIGN and SPEC, `qa-engineer` the VERIFICATION and NOT VERIFIED sections, and
`ux-designer` whatever a user or reader will see. The author merges the three drafts into one plan and
says where they disagreed and what was chosen. [Before You Change Controlvar,
Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md) has a table of each role's one
question. The question here is why the work is split across separate agents at all, when one agent could
write the whole plan in less time.

### Why the author is not the checker

The principal engineer's brief gives the reason, and it applies to people as much as to agents:

```text
# 📁 .claude/agents/principal-engineer.md
A plan's tests are the part most likely to be worthless, because the author
writes them from the mental model that is itself in doubt. Ask of each one:

- **Could it fail?** If the guard has only ever been seen to pass, it is not a
  guard. Demand that the plan says which defect it will plant and watch fail.
- **Does it compare a copy against a copy?** A test that re-implements the
  thing it validates cannot detect divergence. One here re-implemented
  Starlette's rule inside the test and could never have fired.
- **Does it drive the real thing?** Gates that matched on a middleware class
  *name* passed when the middleware was swapped for one that would have failed
  every health check.
```

The third question comes from the same history as the traps in the opening. Pull request #61, merged a
little over two hours before #89 was opened, lists the class-name gates among #236's findings. A second
agent with a different brief reads the same plan with a different question. It did not build the mental
model, so it has nothing to defend, and its brief tells it to run something before it believes anything.

The drafting roles came from the same kind of evidence. They were added on the afternoon of #89 itself,
after two rounds of reviewing plans that one author had written alone:

```text
# 📁 CLAUDE.md
All five review the PLAN, not code. (Founder instruction, 2026-09-25: the
drafting roles were added after two rounds in which the author wrote the plan
alone and the reviewers each found a premise the author had not checked.)
```

### A fixed shape for every draft

Each role answers in a fixed shape. The architect's brief ends like this:

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

A fixed shape does two jobs. Three drafts in the same shape can be merged section by section. And an
empty heading is visible: an agent with nothing under PREMISES has not checked anything, and you can see
that without reading the rest. NOT CHANGED tells a reviewer where the change stops.

One heading exists to force a cheaper idea into the open:

```text
# 📁 .claude/agents/software-architect.md
**The alternatives.** Name at least one design you rejected and the reason. The
cheapest review finding is "a dumber thing works" — find it before the
reviewers do. CLAUDE.md: *prefer the dumbest thing that works*.
```

The allow-list story has an example of a dumber thing working. According to #61's description, the
first round of #236 defaulted the list to the load balancer's own DNS name, a host no user ever sends,
which would have refused every request. The design that merged in #61 has one setting to set: the list
is derived from `PUBLIC_BASE_URL`, the address users already type, unless an operator names hosts
explicitly. The description gives the reasoning in one line: "Two independently-set values that must
agree is a defect waiting to happen; a default taken from the URL we publish cannot have that shape."

## The plan's shape

The merged plan has nine headings, in this order:

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

Most of the headings answer a failure that `CLAUDE.md` records elsewhere. PREMISES answers claims
asserted without a run; ENVIRONMENTS answers verifying in the one place that cannot show the bug, since
"the developer environment is the one place those bugs cannot appear" ("Verifying a change", Chapter 5).
VERIFICATION asks for "the defect that proves it fires" because, in the same file's words, "A gate you
have only ever seen pass is not a gate" (Chapter 6). SPLIT answers the history of #236: its work was
later split into two pull requests, and in the words of #61's description, the half that had been stable
throughout "merged in minutes once it stood alone". IRREVERSIBLE is the list from Chapter 1.

SEQUENCE is where the opening's first trap would have surfaced, as shown above. The second trap belongs
to a part of the plan that only some changes need.

NOT VERIFIED is mandatory, and the description of #89 says why in a line: "Naming what you could not
check is where a reviewer looks first". The example in the listing is a good model. It names the missing piece (`constructs._jsii`), says what it
means (the synth suite has not run), and says where the evidence will come from (CI).

### A spec, when there is a contract

When a change adds something other code or other people depend on, such as a setting, an endpoint, a gate
or a file format, the plan also carries a short spec:

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

The example in the second bullet is the opening's second trap, turned into the rule for writing every
contract: name the values. "Accepts hostname patterns" is a shape, and `*example.com` fits it. Only a
list of concrete values accepted and refused forces someone to decide what happens to that one.

## A spec, written and checked

Here is the spec a plan might have carried for the allow-list. It is illustrative: no agent wrote it, and
it was written for this book from the code at `09d815ea`. Every value in it can be checked against that
code, and the rest of this section does so.

```text
SPEC  ALLOWED_HOSTS

What         The hostnames the API answers to. Unset, it is derived from the host
             in PUBLIC_BASE_URL; set, it replaces the derived value.
Contract     Comma-separated or a JSON array. Accepts example.com and
             *.example.com. Refuses in every environment, as matching
             nothing: *example.com  .example.com  ex*ample.com  *.*.com  *.
             Refuses * in staging and production.
Where        Settings validation when the API starts, and the container's
             start-up check. Staging and production refuse to start when no
             hostname is known.
Failure      Silent if a pattern matches nothing: every request is refused while
             the exempt health probes pass.
Verification The five refused patterns are parametrised in one test; accept
             *example.com in the rule and watch that test fail.
```

Start with the contract, because it is the part a reviewer can execute. The rule that decides it is
plain Python with no dependencies beyond the standard library, so you can ask it directly from a clone:

```bash
python3 -B -c 'import sys; sys.path.insert(0, "backend/app/core"); import settings_rules as r; [print(repr(p), "->", r.allowed_host_pattern_error(p) or "accepted") for p in ["example.com", "*.example.com", "*example.com", ".example.com"]]'
```

```text
'example.com' -> accepted
'*.example.com' -> accepted
'*example.com' -> ALLOWED_HOSTS entry '*example.com' is not a hostname or a `*.` wildcard and would match nothing, refusing every request while the health probes -- which are exempt -- stayed green. Write `example.com` or `*.example.com`.
'.example.com' -> ALLOWED_HOSTS entry '.example.com' is not a hostname or a `*.` wildcard and would match nothing, refusing every request while the health probes -- which are exempt -- stayed green. Write `example.com` or `*.example.com`.
```

The two accepted values and the two refused ones match the spec. Notice that the refusal message carries
the failure mode with it. An operator who mistypes the pattern learns at start-up what would have
happened, and the fix, in one line.

The failure mode is the part a spec most often leaves out, and here the code states it. The validator
that calls the rule says so in its docstring:

```python
# 📁 backend/app/core/config.py
    @field_validator("ALLOWED_HOSTS")
    @classmethod
    def validate_allowed_host_patterns(cls, v: List[str]) -> List[str]:
        """Refuse patterns that parse but can never match.

        This is the silent-outage class and it has already been shipped once:
        a wildcard written `*example.com` (no dot) is not a wildcard. It is a
        literal hostname containing an asterisk, it matches nothing, and a
        deployment configured with it refuses every request while every health
        check stays green -- because the probes are exempt. [...]
```

"It has already been shipped once" points back at the history in the opening; #61's description says
that the second round of #236 "shipped that shape". The docstring is the spec's failure-mode line, kept
beside the code it describes.

The verification line names a test, so find it:

```bash
grep -n -E '^\s+\("' backend/tests/unit/core/test_config_allowed_hosts.py
```

```text
72:            ("*example.com", "the classic: a wildcard without its dot"),
73:            (".example.com", "a bare suffix is not a pattern"),
74:            ("ex*ample.com", "an asterisk in the middle"),
75:            ("*.*.com", "two wildcards"),
76:            ("*.", "a wildcard with nothing after it"),
```

Five refused patterns, each with its reason, and the spec lists the same five. Running the test needs the
project's virtual environment, and so does planting the defect the spec names, which this book did not
do. A plan would say so under NOT VERIFIED, and leave the planted run to the pull request.

The staging and production rule for `*` lives in the start-up check as well:

```bash
grep -n 'is refused in staging and production' backend/app/core/preflight.py
```

```text
257:            "ALLOWED_HOSTS=* is refused in staging and production. Name the "
```

That leaves the "Where" line, and the first trap. A setting that production must have is only safe once
the task definition carries it, and Controlvar pins that with a test that reads the infrastructure code
itself. Its docstring says how:

```text
# 📁 backend/tests/unit/infrastructure/test_deployment_secrets.py
The check is derived rather than typed: the environment is read out of the CDK
stacks and handed to the real settings classes in a clean subprocess.  A secret
that stops being injected fails this test with the field that rejected its
default, whatever that field turns out to be.
```

The test does not keep its own list of settings to look for. It takes the environment the stacks would
give a container and asks the real settings classes whether they can start with it. The principal
engineer's third question, whether a gate drives the real thing, gets a yes. The validator that makes
staging and production refuse to start without a hostname names this test in its own docstring, and a
spec with that "Where" line would name it too, as the gate for its SEQUENCE step.

## Verdicts are gates

The two reviewers each end with a verdict, `APPROVED`, `APPROVED WITH CONDITIONS` or `REJECTED`, and a
numbered list of conditions, each one checkable. The two verdicts answer different questions. The
principal engineer's lists premises, environments, dependencies, failure modes and verification: whether
the plan's claims are true. The engineering manager's lists scope, size, sequence, the irreversible and
the blocked: whether this is the right work, in the right order, at the right size. Neither asks whether
the code is good, because there is no code yet.

Both briefs say what to reject:

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

The second rule changes what an honest NOT VERIFIED line costs. A premise the reviewer could not check
is a reason to reject, so the author is pushed to check it, or to redesign so that the plan no longer
depends on it.

`CLAUDE.md` tells the author how to take a rejection:

```text
# 📁 CLAUDE.md
**Expect rejection, and do not treat it as failure.** Across two rounds the
reviewers caught a required check that was green for the wrong reason, a
documentation tag that destroys the fence it annotates, a deploy that reports
healthy while rejecting every request, a migration that can run production's
image against staging, and a release gate no tag can satisfy. Each of those
was cheaper to find in review than in production.
```

The third item has the shape of the second trap in the opening, a deploy that looks healthy while it
serves nobody, and this time a reviewer found it. That is the process working as intended: the same
class of defect, caught at the cheapest point.

Rejection has a limit. After two rejected revisions the author stops revising, works the uncontested
parts and writes down what is contested (Chapter 1 quotes the rule). We would add one habit of our own:
treat a verdict as holding only for the plan it read. If the work turns up a premise the plan got wrong,
send the plan back, because the reviewers approved a design that no longer exists. Chapter 9 follows one
plan through all of this. The issue asked for a dashboard screen; the plan review measured that the
scheduler started drafts it should not have, which the new screen would have promised it did not, and
the server fix was split out and shipped first.

## When a plan costs more than it saves

The rule is scoped: "for anything beyond a single-file change". A typo fix, a version bump or a one-line
correction to a comment does not need five agents, and the spec section says so in as many words: "A spec
is not ceremony for a one-line fix." Chapter 10 looks at the changes that are too small for the process
and at what the repository does with them instead.

Size is not the only test, though. A one-file change that makes a setting mandatory has a sequence, and
a one-line change to a host pattern has a contract. The question to ask is whether the change has
premises someone else should check, an order that matters, or a value other things depend on. If it has
any of them, a nine-line plan costs less than the review round it saves.

## Try it

Steps 1 to 3 need a clone of Controlvar, Python 3 and git; step 2 also needs the GitHub CLI, signed in.
Steps 4 to 6 use your AI tool. The output was taken at `09d815ea` on macOS.

1. Read the two verdict shapes side by side:

   ```bash
   sed -n '/^## Verdict/,/^    CONDITIONS/p' .claude/agents/principal-engineer.md
   sed -n '/^Then produce a verdict/,/^    CONDITIONS/p' .claude/agents/engineering-manager.md
   ```

   ```text
   ## Verdict

       VERDICT: APPROVED | APPROVED WITH CONDITIONS | REJECTED

       PREMISES      each factual claim, and what you ran to check it
       FALSE/UNKNOWN the ones that did not hold, or could not be checked
       ENVIRONMENTS  where this runs vs where the plan verifies it
       DEPENDENCIES  what the change now needs to be true elsewhere
       FAILURE MODES what breaks, and what it looks like when it does
       VERIFICATION  for each gate: what defect proves it works
       CONDITIONS    numbered, each one checkable
   Then produce a verdict in this shape:

       VERDICT: APPROVED | APPROVED WITH CONDITIONS | REJECTED

       SCOPE        does it answer the request, and only the request
       SIZE         should this be split, and where
       SEQUENCE     what must be true before each step
       IRREVERSIBLE what needs a human at the moment it happens
       BLOCKED      what needs someone else
       CONDITIONS   numbered, each one checkable
   ```

   One asks whether the claims are true; the other asks whether this is the right work in the right order.
   Both end in CONDITIONS, numbered so that the next round can be checked against them.

2. Read how the process grew in one day, from the commits of #89 as GitHub keeps them:

   ```bash
   gh pr view 89 -R Controlvar/controlvar --json commits \
     --jq '.commits[] | [.committedDate, .messageHeadline] | @tsv'
   ```

   ```text
   2026-09-25T06:13:52Z	docs: plan, review and sign-off before building
   2026-09-25T07:07:21Z	docs: a blocking question goes to the team, not to a halt
   2026-09-25T07:16:44Z	docs: how to actually consult the team
   2026-09-25T15:27:31Z	docs: three drafting roles, and the manager's final verdict
   ...
   ```

   The fifth line, trimmed here, is a merge of `main` into the branch at 20:39. The squash commit on
   `main` is `399bcced`; these branch commits are not in a clone's history, which is why the command asks
   GitHub.

3. Ask the allow-list rule about values the spec above does not mention. Decide first what you think the
   spec should say about each, then run:

   ```bash
   python3 -B -c 'import sys; sys.path.insert(0, "backend/app/core"); import settings_rules as r; [print(repr(p), "->", r.allowed_host_pattern_error(p) or "accepted") for p in ["*.example.com.", "EXAMPLE.com"]]'
   ```

   ```text
   '*.example.com.' -> accepted
   'EXAMPLE.com' -> accepted
   ```

   Both pass this rule, which only refuses patterns that can never match. Whether they then match a
   request is the middleware's business, and the spec above is silent on both. Where your guess and the
   output differ, you have found a line the spec should have had.

4. Pick a small open issue on Controlvar's public tracker, one labelled `bug`. Ask your AI tool for a plan
   with the nine headings, and tell it not to write code. Save the answer as `plan.md` outside the clone.

5. Start a fresh session. Give it this, and nothing else: "Read `.claude/agents/principal-engineer.md`
   and follow it. Review the plan in `plan.md` against this repository. You are read-only: do not change
   any file." Count the premises it checked by running a command, and the ones it checked by reading
   alone.

6. Look at the plan's NOT VERIFIED section. If it is empty, or says "none", send the plan back. Every real
   change has something its author could not check.

## Summary

An agent can finish a change built on a false premise before anyone sees the premise, and its tests will
agree with it. Controlvar's answer is a plan written before any code, drafted by three roles and checked by
two reviewers whose verdicts are gates. Pull request #89 made that a rule after an agent skipped the plan
and walked into two traps the repository had already recorded: a setting required before the task
definition supplied it, which a plan's SEQUENCE line exposes, and a host pattern that matches nothing,
which a spec that names its values refuses. The nine headings are a list of the ways agent work goes
wrong, NOT VERIFIED most of all. Chapter 4 turns to the briefs that tell each of those agents what to do.

## Further reading

- [Before You Change Controlvar, Chapter 5](../before-you-change-controlvar/chapter-5-the-process.md): each
  role's one question, the habits underneath, and a nine-line plan for a real issue.
- [Production Engineering, Hour 11](../production-engineering/part-4-safe-change.md): changing Controlvar
  safely, one change across storage, API, dashboard, contract and profiles.

---

_Previous: [Chapter 2 — Instructions that work](./chapter-2-instructions-that-work.md). Next:
[Chapter 4 — Briefing an agent](./chapter-4-briefing-an-agent.md)._

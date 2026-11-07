# Before You Change Controlvar

### _A short guide for your first change: the map, the gates, the process, and one real fix from issue to release_

> **Preface.** The first change you make to a codebase you did not write is the hardest one. Not because
> the code is hard, but because you do not yet know where things live, what will break, or what the
> people who review your pull request will look for. This guide is the short answer to those three
> questions for **[Controlvar](https://github.com/Controlvar/controlvar)**, the open-source A/B-testing
> and feature-flag platform the rest of this library teaches with. It does not teach Python, FastAPI or
> statistics: the other eight books do that, and every chapter here links to the lessons that go deeper.
> It teaches the things you need on the day you open your editor: which directory a change belongs in,
> how to run the stack, the one function that owns each decision, the checks a pull request must pass,
> how changes are planned and reviewed, and the changes whose effects land far from where you made them.
> Chapter 6 walks one real bug fix from its issue to its line in the release notes.

---

## Who this is for

- Anyone about to make a first change to Controlvar: a bug fix, a small feature, a documentation fix.
- Reviewers and new maintainers who want the same map in one place.
- Readers of the other books who want to see how the pieces they learned are held together by tests and
  process.

You need to read code in some language. You do not need to have finished the other books: where a
chapter leans on one, it says which lesson to read.

## What you need

A clone of Controlvar, open beside this guide:

```bash
git clone https://github.com/Controlvar/controlvar.git
cd controlvar
```

Chapter 2 runs the stack, and needs Docker with Compose v2, Python 3.11 and Node 22 (the versions in
`.python-version` and `.nvmrc`). Chapters 3, 4, 6 and 7 run single test files and small Python snippets
in the virtual environment Chapter 2 builds; Chapter 6's tests also need PostgreSQL, which `make db`
starts. Commands are run from the repository root.

## How to use this guide

There are eight short chapters. Read them in order the first time; after that, use them as a checklist.

Every chapter has the same shape:

1. **The idea**, in plain words, with the real file it lives in.
2. **The code**, where it helps: blocks tagged `# 📁 path` are quoted from Controlvar's source.
3. **Try it**: a few steps to run on your clone, with what you should see.
4. **Go deeper**: the lessons in the other books that cover the same ground in depth.

Sidebars mark a **Watch Out!** (a trap people fall into) and a **By the Way** (context).

## Chapters

1. **[The map](./chapter-1-the-map.md)**: the directories, the four layers of the backend, the two
   profiles, and where tests go.
2. **[Run it](./chapter-2-run-it.md)**: the Docker stack with demo data, the development setup with
   reload, and the setting that stops the API when it is missing.
3. **[Where decisions live](./chapter-3-where-decisions-live.md)**: one function for each question the
   product answers, and why a change goes there and nowhere else.
4. **[Tests and gates](./chapter-4-tests-and-gates.md)**: what a pull request must pass, how to run the
   part you touched, and the gates that read documentation and contracts.
5. **[The process](./chapter-5-the-process.md)**: plan, review, sign-off, then build; the shape of a plan;
   and what of it applies to your pull request.
6. **[A first change, start to finish](./chapter-6-a-first-change.md)**: one real fix, from the issue to
   the merge: the failing test, the sign-off, the title, the template and the checks.
7. **[Changes that act at a distance](./chapter-7-changes-at-a-distance.md)**: migrations and the two
   heads, the statistics engine's version, the SDK contract, the OpenAPI snapshots and the core profile.
8. **[After the merge](./chapter-8-after-the-merge.md)**: how your pull request title becomes a line in
   the release notes, and what a release publishes.

## Where this fits

```text
Core path:  Python → FastAPI → React/Next.js → Build a Platform
Specialize:                                    ├─ Experimentation Statistics
                                               ├─ SDK Engineering
                                               ├─ Platform Operations
                                               └─ Production Engineering
Contribute: Before You Change Controlvar (this guide): read it before your first pull request
```

You can read this guide first and come back to the others, or read it last as the bridge from reading
the code to changing it.

_Turn to [Chapter 1](./chapter-1-the-map.md)._

---

## A note on accuracy

Checked against Controlvar commit `09d815ea` on `main`, nine commits after release 0.43.2. That is newer
than the release the other books are pinned to, so a path or a count here may be ahead of them. Every
`# 📁` block was copied from the file at that commit, and the library's checks
([`tools/README.md`](../tools/README.md)) hold each cited path and each identifier in a quoted block to
that commit: `REF=09d815ea tools/check.sh before-you-change-controlvar`.

**What was run.** At that commit, in a fresh clone: the single test files the chapters name (the hash,
engine-fingerprint, ruleset-vector, OpenAPI-snapshot, core-boundary, flag-route and environment tests,
and the three regression tests of Chapter 6 against a scratch PostgreSQL), the two planted defects in
Chapters 6 and 7 (each failed as described, then passed again once restored), the contributor-guard and
DCO commands, `scripts/doc_claims.py check` before and after the planted documentation edit,
`scripts/doc_examples.py --check`, `scripts/core_build.sh --list-steps`, the ruleset generator's `--check`,
`alembic heads`, and the API started without `ENVIRONMENT`.

**What was not run.** Anything that needs Docker: `docker compose up`, `make dev`, `make web`, `make demo`,
the demo apps, a full `make test`, `make lint` and `make core-build`. Those steps are written from the
repository's own README, CONTRIBUTING.md and Makefile at the same commit. Nothing in CI was run, and the
pull request flow in Chapter 6 is read from the workflows and from one merged pull request, not
re-enacted.

Code moves on. If a path here no longer exists on `main`, `git grep` the symbol; the habits outlast the
line numbers.

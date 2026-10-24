# Docs gates

Six checks that hold these books to the app at one pinned commit, `tools/REF`. Python 3.8+ and git, no
packages. They read the app from a local clone of `getexperimently/experimently`:

```bash
git -C "$EXPERIMENTLY_APP" fetch --tags origin      # REF must be in the clone
export EXPERIMENTLY_APP=~/src/experimently          # wherever your clone is
git add -A                                          # V6 refuses while untracked files exist
make check                                          # every gate, whole repository
make check SCOPE="platform-operations"              # one pull request's directories
```

`tools/check.sh` runs every gate even when one fails, prints a summary, and exits 0 only if all six pass.
Exit codes per gate: 0 clean, 1 findings, 2 setup error (fails closed: a gate that scanned nothing, could not
find REF, or could not parse leak_guard's output never reports clean), 3 not implemented for this scope.

## The gates

| Gate | Script | Checks | Scope |
|---|---|---|---|
| V1 | `banned_terms.py` | Words and commands false at REF: the old repository name, the old edition names, tags, targets, variable and directory, a two-heads-unsafe alembic command, the singular `head` with upgrade/stamp, the linters ruff replaced, the old per-environment deploy workflows, the removed assignment Lambda, the old SDK count, service-restart commands, internal process references, real SSO names, the Helm OCI wording, removed routes named without saying they are removed. Lines are folded within a paragraph, so a command wrapped over two lines still matches. | scoped |
| V2 | `paths_at_ref.py` | Every app path in inline code, and every path-shaped token in a fence, exists at REF (`git cat-file -e REF:path`). | scoped |
| V3 | `snippets.py` | Every `📁 path` segment: the path is a file at REF, and every identifier in the segment (comments stripped) occurs in that file. | scoped |
| V4 | `links.py` | Relative links and images resolve; every `#anchor` is a GitHub heading slug (or an explicit `id`/`name`) in the target. | always the whole tree |
| V5 | `numbers.py` | STUB: inventories the Try-It numbers; exits 3 when learn-python or statistics is in scope. The learn-python and statistics pull requests implement it in the released image at the REF tag. | scoped |
| V6 | `secrets.py` | The app's `scripts/leak_guard.py` taken from REF and run over every tracked file, plus: a bare 12-digit number (only `123456789012` allowed; UUID tails and longer tokens ignored), a real-shape API key (the `eptk_demo_` keys are not that shape), AWS key ids, GitHub tokens. `--stdin` scans a pull request title and body. | scoped findings; leak_guard itself always scans the tree |

## REF

`tools/REF` holds a pinned commit or a `vX.Y.Z` tag; moving refs are refused. To re-pin when a release is
tagged, change `tools/REF`, paste `git -C "$EXPERIMENTLY_APP" diff --stat <old> <new>` into the pull request,
re-check the cited paths the diff touches, and paste a fresh `make check`.

## The allowlist, `tools/allow.txt`

```
GATE | file | reason | key
```

`key` is the offending line's text, stripped (V3: `<tagged path>::<identifier>`). It is last, so it may contain
` | `. Matching on text, not line numbers, means a line that moves keeps its entry and a NEW occurrence of the same
defect elsewhere still fails. Every entry needs a reason a reviewer can check. An entry that no longer matches is
reported as a note; remove it.

## What no gate checks

- V3 compares identifiers, not literals: a changed number or string inside a quoted snippet whose identifiers
  all still exist is not caught.
- Untagged code is teaching code and is not compared with anything.
- Pedagogy, and whether AWS operations behave as described.
- These gates run where they are invoked. This repository has no CI, so a pull request is checked only if its
  author runs `make check` and pastes the output.

## Testing a gate

Change a gate, then break the thing it guards and watch it fail, in a fresh clone after `git add -A`: plant one
defect per rule shape, run the gate, see it fail; remove it, see it pass. A gate seen only passing is not a gate.

## Book-specific source manifests

`production-engineering-sources.json` records the reviewed app revision, date and source paths for
each Part of the Production Engineering book. It is review metadata, not a new automated gate.
The book's README documents its scoped check command using a `REF` override; the older books keep
the global reference and their own verification notices. Update a Part's source links and manifest
together when reviewing it against a newer app revision.

#!/usr/bin/env python3
"""V5: the Try-It numbers in learn-python and statistics, re-run at REF. (STUB)

What it is meant to do (not built yet):
  * extract each `>>>` line with its printed output, and each line carrying an
    expectation comment (`# ≈ X` or `# → X`);
  * run them in the RELEASED image at REF's tag, never a local venv:
      docker run --rm --platform linux/amd64 --entrypoint python \\
        ghcr.io/controlvar/controlvar:full-X.Y.Z
    (`--entrypoint` skips the migrate-on-start entrypoint);
  * compare with a stated relative tolerance and fixed seeds;
  * exclude timing lines (`perf_counter`) by name.

What the stub does today: it inventories those lines in scope, and
  * exits 3 (NOT IMPLEMENTED, counts as a failure) when its scope includes
    learn-python-in-24-hours/ or experimentation-statistics/, so neither book can
    merge on a green run that never checked a number;
  * exits 0 when the scope holds no book with Try-It numbers.
"""

from __future__ import annotations

import re
import sys

from common import base_parser, in_scope, markdown_files, normalise_scope, read

BOOKS = ("learn-python-in-24-hours", "experimentation-statistics")
PROMPT = re.compile(r"^\s*>>>")
EXPECT = re.compile(r"#\s*(?:≈|→)\s*\S")


def main() -> int:
    ap = base_parser("numbers.py", __doc__.split("\n")[0], needs_app=False)
    args = ap.parse_args()
    scope = normalise_scope(args.paths)
    files = markdown_files(scope)
    count = prompts = timing = 0
    for rel in files:
        for line in read(rel).split("\n"):
            if PROMPT.match(line):
                prompts += 1
            if EXPECT.search(line):
                count += 1
            if "perf_counter" in line:
                timing += 1
    books_in_scope = [b for b in BOOKS if any(in_scope(f, [b]) for f in files)]
    if books_in_scope:
        print(f"V5: in scope: {prompts} `>>>` lines, {count} `# ≈`/`# →` expectation comments, {timing} perf_counter lines; "
              f"books: {', '.join(books_in_scope)}")
        print("V5: NOT IMPLEMENTED -- this tool does not re-run the numbers yet (the books' numbers were re-run by "
              "hand at REF); the other five gates are the ones that must pass.")
        return 3
    print(f"V5: out of scope (no Try-It book in scope; {count} expectation lines seen)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

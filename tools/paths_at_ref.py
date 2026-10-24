#!/usr/bin/env python3
"""V2: every app path the docs quote exists at REF.

Checked:
  * outside fences: path-shaped tokens inside inline code spans;
  * inside fences: path-shaped tokens anywhere on the line;
  * a few root files, only when an inline code span is exactly that name.

A path is one of the app's top-level directories followed by `/...`. A token
preceded by `/`, `.`, a word character or `-` is not a path (URLs, `../x`,
`foo-backend/`). `:NN`, `#L..` and trailing punctuation are stripped. Tokens
holding a placeholder (`<`, `{`, `*`, `$`, `...`) are skipped. Existence is
`git -C $CONTROLVAR_APP cat-file -e REF:path`, so a file and a directory both
count.
"""

from __future__ import annotations

import re
import sys
from typing import Dict, List

from common import (
    Finding,
    base_parser,
    finish,
    git,
    inline_code_spans,
    iter_md,
    markdown_files,
    normalise_scope,
    read,
    resolve_ref_and_app,
)

TOP = r"(?:backend|frontend|sdk|infrastructure|docs|scripts|modules|charts|deploy|demo|\.github)"
PATH = re.compile(r"(?<![\w./-])(" + TOP + r"/[\w.@+\-\[\]/]*)")
ROOT_FILES = {"Makefile", "docker-compose.yml", "pyproject.toml", "CLAUDE.md", "modules-manifest.txt"}
SKIP = re.compile(r"[<{*$]|\.\.\.|…")


def clean(token: str) -> str:
    token = re.sub(r"#L\d.*$", "", token)
    token = re.sub(r":\d.*$", "", token)
    token = token.rstrip(".,;:)]}'\"")
    return token.rstrip("/")


def candidates(rel: str, text: str):
    for ln in iter_md(text):
        if ln.is_marker:
            continue
        if ln.in_fence:
            pieces = [ln.text]
        else:
            pieces = inline_code_spans(ln.text)
            for span in pieces:
                if span.strip() in ROOT_FILES:
                    yield ln.no, span.strip(), ln.text
        for piece in pieces:
            for m in PATH.finditer(piece):
                raw = m.group(1)
                # the placeholder test looks a little past the token, for `x/{id}` and `x/<name>`
                tail = piece[m.end(1): m.end(1) + 1]
                if SKIP.search(raw) or (tail and tail in "<{*$"):
                    continue
                p = clean(raw)
                if "/" not in p:
                    continue
                yield ln.no, p, ln.text


def main() -> int:
    ap = base_parser("paths_at_ref.py", __doc__.split("\n")[0])
    args = ap.parse_args()
    scope = normalise_scope(args.paths)
    ref, app = resolve_ref_and_app(args)
    files = markdown_files(scope)
    cache: Dict[str, bool] = {}
    findings: List[Finding] = []
    checked = 0
    for rel in files:
        seen = set()
        for no, p, line in candidates(rel, read(rel)):
            if (no, p) in seen:
                continue
            seen.add((no, p))
            checked += 1
            if p not in cache:
                cache[p] = git(["cat-file", "-e", f"{ref}:{p}"], cwd=app, check=False).returncode == 0
            if not cache[p]:
                findings.append(Finding("V2", rel, no, "missing-at-ref", f"{p} does not exist at {ref[:12]}", line.strip()))
    if checked == 0:
        print("V2: no app paths found in scope; nothing to check")
    return finish("V2", findings, scope, f"{checked} path references ({len(cache)} distinct) in {len(files)} files at {ref[:12]}")


if __name__ == "__main__":
    sys.exit(main())

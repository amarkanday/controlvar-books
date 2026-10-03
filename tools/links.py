#!/usr/bin/env python3
"""V4: every relative link resolves, and every #anchor names a heading.

Links are read outside fences, after inline code spans are blanked (a `](x)`
inside code is not a link). Inline links, images and reference definitions
(`[id]: target`) are checked. Absolute URLs and mailto are skipped. A `#frag`
must equal the GitHub slug of a heading in the target file (lower-cased,
punctuation other than `-` and `_` removed, spaces to `-`, a repeated slug gets
`-1`, `-2`, ...), or an explicit `id="..."`/`name="..."` anchor.

The whole tree is the default scope even for a one-book change: links cross
books, and renaming a heading in one book breaks an anchor in another.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Set

from common import ROOT, Finding, base_parser, finish, iter_md, markdown_files, normalise_scope, read, strip_inline_code

LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
REFDEF = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s|$)")
HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
HTML_ANCHOR = re.compile(r"""<a\s[^>]*\b(?:id|name)\s*=\s*["']([^"']+)["']""", re.I)
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def slugify(text: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)  # images
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)  # links -> text
    t = re.sub(r"<[^>]+>", "", t)  # html
    t = t.replace("`", "")
    t = re.sub(r"(\*\*|__|\*|~~)", "", t)
    t = re.sub(r"(?<!\w)_|_(?!\w)", "", t)
    t = t.strip().lower()
    t = re.sub(r"[^\w\- ]", "", t)
    return t.replace(" ", "-")


_anchor_cache: Dict[str, Set[str]] = {}


def anchors(rel: str) -> Set[str]:
    if rel not in _anchor_cache:
        seen: Dict[str, int] = {}
        out: Set[str] = set()
        for ln in iter_md(read(rel)):
            if ln.in_fence or ln.is_marker:
                continue
            m = HEADING.match(ln.text)
            if m:
                base = slugify(m.group(2))
                n = seen.get(base, 0)
                out.add(base if n == 0 else f"{base}-{n}")
                seen[base] = n + 1
            for a in HTML_ANCHOR.findall(ln.text):
                out.add(a)
        _anchor_cache[rel] = out
    return _anchor_cache[rel]


def check(rel: str, target: str, no: int, line: str, findings: List[Finding]) -> None:
    if SCHEME.match(target) or target.startswith("//"):
        return
    path, _, frag = target.partition("#")
    if path:
        dest = (ROOT / rel).parent / path
        try:
            dest_rel = dest.resolve().relative_to(ROOT)
        except ValueError:
            findings.append(Finding("V4", rel, no, "outside-repo", f"{target} points outside this repository", line.strip()))
            return
        if not dest.exists():
            findings.append(Finding("V4", rel, no, "missing-file", f"{target}: {dest_rel} does not exist", line.strip()))
            return
        if dest.is_dir():
            readme = dest / "README.md"
            md = str(readme.resolve().relative_to(ROOT)) if readme.exists() else None
        else:
            md = str(dest_rel) if str(dest_rel).endswith(".md") else None
    else:
        md = rel
    if frag:
        if md is None:
            findings.append(Finding("V4", rel, no, "anchor-unchecked", f"{target}: anchor on a non-markdown target", line.strip()))
        elif frag not in anchors(md):
            findings.append(Finding("V4", rel, no, "missing-anchor", f"{target}: no heading slug {frag!r} in {md}", line.strip()))


def main() -> int:
    ap = base_parser("links.py", __doc__.split("\n")[0], needs_app=False)
    args = ap.parse_args()
    scope = normalise_scope(args.paths)
    files = markdown_files(scope)
    findings: List[Finding] = []
    n = 0
    for rel in files:
        for ln in iter_md(read(rel)):
            if ln.in_fence or ln.is_marker:
                continue
            text = strip_inline_code(ln.text)
            targets = [m.group(1) for m in LINK.finditer(text)]
            m = REFDEF.match(text)
            if m:
                targets.append(m.group(1))
            for t in targets:
                n += 1
                check(rel, t, ln.no, ln.text, findings)
    return finish("V4", findings, scope, f"{n} links in {len(files)} files")


if __name__ == "__main__":
    sys.exit(main())

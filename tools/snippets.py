#!/usr/bin/env python3
"""V3: every snippet tagged `📁 path` still matches the app source at REF.

The books' convention: a fenced block carrying a `# 📁 path` (or `// 📁 path`)
line quotes real source. For each tag, the lines after it, up to the next tag or
the end of the fence, form a segment. The gate checks that:

  * the path exists at REF; and
  * every identifier in the segment ([A-Za-z_] followed by 2+ word characters,
    after comments are stripped) occurs as an identifier in that file at REF.

Comments are stripped because the books annotate quoted code with their own
remarks. Untagged code is teaching code and out of scope by construction.

Known limit: a changed literal (a number, a string's punctuation) or a reordered
call is not detected when every identifier still exists somewhere in the file.
"""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Dict, List, Optional, Set

from common import Finding, base_parser, finish, iter_md, markdown_files, normalise_scope, read, resolve_ref_and_app

TAG = re.compile(r"^\s*(?:#|//|--|;|<!--|/\*|\*|\{/\*)\s*📁\s*([^\s`]+)")
IDENT = re.compile(r"[A-Za-z_]\w{2,}", re.ASCII)

HASH_LANGS = {
    "python", "py", "bash", "sh", "shell", "zsh", "console", "yaml", "yml", "toml", "dockerfile",
    "makefile", "make", "ruby", "rb", "elixir", "ex", "exs", "nginx", "conf", "ini", "properties", "r",
}
SLASH_LANGS = {
    "ts", "tsx", "typescript", "js", "jsx", "javascript", "mjs", "go", "java", "kotlin", "kt", "swift",
    "c", "cpp", "csharp", "cs", "dart", "rust", "scss", "css", "php", "groovy", "gradle",
}
BLOCK_COMMENT = re.compile(r"/\*.*?\*/")


def strip_comments(line: str, lang: str) -> str:
    use_hash = lang in HASH_LANGS or lang not in SLASH_LANGS
    use_slash = lang in SLASH_LANGS or lang not in HASH_LANGS
    if lang == "php":
        use_hash = True
    s = line
    if use_slash:
        s = BLOCK_COMMENT.sub(" ", s)
        if re.match(r"^\s*//", s):
            return ""
        s = re.sub(r"\s//.*$", "", s)
    if use_hash:
        if re.match(r"^\s*#", s):
            return ""
        s = re.sub(r"\s#.*$", "", s)
    return s


def clean_path(p: str) -> str:
    p = p.rstrip(".,;:)")
    p = re.sub(r"-->$|\*/$", "", p)
    p = re.sub(r":\d.*$", "", p)
    return p


class Source:
    def __init__(self, app, ref: str):
        self.app = app
        self.ref = ref
        self.cache: Dict[str, Optional[Set[str]]] = {}

    def idents(self, path: str) -> Optional[Set[str]]:
        if path not in self.cache:
            r = subprocess.run(["git", "show", f"{self.ref}:{path}"], cwd=str(self.app), capture_output=True)
            if r.returncode != 0 or not r.stdout or r.stdout.startswith(b"tree "):
                self.cache[path] = None
            else:
                self.cache[path] = set(IDENT.findall(r.stdout.decode("utf-8", errors="replace")))
        return self.cache[path]


def main() -> int:
    ap = base_parser("snippets.py", __doc__.split("\n")[0])
    args = ap.parse_args()
    scope = normalise_scope(args.paths)
    ref, app = resolve_ref_and_app(args)
    src = Source(app, ref)
    files = markdown_files(scope)
    findings: List[Finding] = []
    segments = flagged = 0
    for rel in files:
        cur: Optional[str] = None
        cur_line = 0
        bad_here = False
        for ln in iter_md(read(rel)):
            if not ln.in_fence:
                cur = None
                continue
            m = TAG.match(ln.text)
            if m:
                cur = clean_path(m.group(1))
                cur_line = ln.no
                segments += 1
                bad_here = False
                if src.idents(cur) is None:
                    findings.append(Finding("V3", rel, ln.no, "path-missing",
                                            f"tagged path {cur} is not a file at {ref[:12]}", f"{cur}::<path>"))
                    flagged += 1
                    cur = None  # nothing to compare the segment with
                continue
            if cur is None:
                continue
            idents = src.idents(cur) or set()
            code = strip_comments(ln.text, ln.lang)
            for name in sorted(set(IDENT.findall(code))):
                if name not in idents:
                    findings.append(Finding("V3", rel, ln.no, "identifier-missing",
                                            f"{name!r} not in {cur} at {ref[:12]} (snippet tagged at line {cur_line})",
                                            f"{cur}::{name}"))
                    if not bad_here:
                        flagged += 1
                        bad_here = True
    return finish("V3", findings, scope,
                  f"{segments} tagged segments in {len(files)} files, {flagged} segment(s) with a finding, at {ref[:12]}")


if __name__ == "__main__":
    sys.exit(main())

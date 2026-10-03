"""Shared plumbing for the docs gates in tools/.

Every gate:
  * takes optional scope paths (files or directories, relative to the repo root);
    with none, it checks the whole repository;
  * reads REF from tools/REF unless --ref is given, and refuses a moving ref;
  * finds the app repository through --app or $EXPERIMENTLY_APP;
  * exits 0 clean, 1 on findings, 2 on a setup or usage error (fail closed),
    3 when the gate is not implemented for the requested scope.

Findings are suppressed only by an exact entry in tools/allow.txt:

    GATE | file | reason | key

The key is last and may contain " | " (a markdown table row); the reason may
not. `key` is the text of the offending line, stripped of surrounding whitespace
(V3 uses `<tagged path>::<identifier>` instead). Matching on text rather than
line numbers means an edit that moves a line keeps its entry, and a NEW
occurrence of the same defect on a different line still fails.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, NamedTuple, NoReturn, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
ALLOW_FILE = TOOLS / "allow.txt"
REF_FILE = TOOLS / "REF"

# A pinned commit (7-40 hex) or a release tag. Never a branch: "true at main"
# is not something a reader can reproduce, and the plan forbids chasing main.
REF_SHAPE = re.compile(r"^(?:[0-9a-f]{7,40}|v[0-9]+\.[0-9]+\.[0-9]+)$")


class Finding(NamedTuple):
    gate: str
    file: str
    line: int
    rule: str
    message: str
    key: str


def die(msg: str, code: int = 2) -> NoReturn:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def git(args: Sequence[str], cwd: Path = ROOT, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r


def base_parser(gate: str, description: str, needs_app: bool = True) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"tools/{gate}", description=description)
    ap.add_argument("paths", nargs="*", help="scope: files or directories (default: whole repository)")
    if needs_app:
        ap.add_argument("--ref", help="app commit or release tag (default: tools/REF)")
        ap.add_argument("--app", help="path to a clone of getexperimently/experimently (default: $EXPERIMENTLY_APP)")
    return ap


def resolve_ref_and_app(args: argparse.Namespace) -> Tuple[str, Path]:
    ref = args.ref or (REF_FILE.read_text().strip() if REF_FILE.is_file() else "")
    if not ref:
        die("no REF: pass --ref or write tools/REF")
    if not REF_SHAPE.match(ref):
        die(f"REF {ref!r} is not a pinned commit or a vX.Y.Z tag; moving refs (main, HEAD, origin/...) are refused")
    app_s = args.app or os.environ.get("EXPERIMENTLY_APP", "")
    if not app_s:
        die("set EXPERIMENTLY_APP (or --app) to a clone of getexperimently/experimently")
    app = Path(app_s).expanduser()
    if not (app / ".git").exists():
        die("EXPERIMENTLY_APP is not a git clone")
    r = git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=app, check=False)
    if r.returncode != 0:
        die(f"REF {ref} is not in the app clone; run `git -C \"$EXPERIMENTLY_APP\" fetch --tags origin`")
    return r.stdout.decode().strip(), app


def repo_files(untracked: bool = True) -> List[str]:
    args = ["ls-files", "-z", "--cached"]
    if untracked:
        args += ["--others", "--exclude-standard"]
    out = git(args).stdout.decode()
    return sorted({p for p in out.split("\0") if p})


def in_scope(rel: str, scope: Sequence[str]) -> bool:
    if not scope:
        return True
    for s in scope:
        s = s.strip("/")
        if s in ("", "."):
            return True
        if rel == s or rel.startswith(s + "/"):
            return True
    return False


def normalise_scope(paths: Sequence[str]) -> List[str]:
    out = []
    for p in paths:
        ap = (Path.cwd() / p).resolve()
        try:
            rel = ap.relative_to(ROOT)
        except ValueError:
            die(f"scope path {p!r} is outside the repository")
        if not ap.exists():
            die(f"scope path {p!r} does not exist")
        out.append(str(rel) if str(rel) != "." else ".")
    return out


def markdown_files(scope: Sequence[str]) -> List[str]:
    files = [f for f in repo_files() if f.endswith(".md") and in_scope(f, scope)]
    if not files:
        die("no markdown files in scope: a gate that scans nothing must not report clean")
    return files


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


FENCE_OPEN = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([^\s`]*)")


class MdLine(NamedTuple):
    no: int
    text: str
    in_fence: bool  # True for lines inside a fence (not the fence markers themselves)
    is_marker: bool
    lang: str


def iter_md(text: str) -> Iterator[MdLine]:
    fence: Optional[str] = None
    lang = ""
    for i, line in enumerate(text.split("\n"), 1):
        if fence is None:
            m = FENCE_OPEN.match(line)
            if m:
                fence = m.group(2)
                lang = m.group(3).lower()
                yield MdLine(i, line, False, True, lang)
                continue
            yield MdLine(i, line, False, False, "")
        else:
            s = line.strip()
            if s and s[0] == fence[0] and set(s) == {fence[0]} and len(s) >= len(fence):
                yield MdLine(i, line, False, True, lang)
                fence = None
                lang = ""
                continue
            yield MdLine(i, line, True, False, lang)


INLINE_CODE = re.compile(r"(`+)(.+?)\1")


def inline_code_spans(line: str) -> List[str]:
    return [m.group(2) for m in INLINE_CODE.finditer(line)]


def strip_inline_code(line: str) -> str:
    return INLINE_CODE.sub(lambda m: " " * len(m.group(0)), line)


class Allow:
    def __init__(self, path: Path = ALLOW_FILE):
        self.entries: Set[Tuple[str, str, str]] = set()
        self.used: Set[Tuple[str, str, str]] = set()
        if not path.is_file():
            return
        for n, raw in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            parts = raw.split(" | ", 3)
            if len(parts) != 4:
                die(f"tools/allow.txt:{n}: expected `GATE | file | reason | key`")
            # The key comes last so it may itself contain " | " (a table row).
            gate, file, reason, key = (x.strip() for x in parts)
            if not (gate and file and key and reason):
                die(f"tools/allow.txt:{n}: GATE, file, reason and key must all be non-empty")
            self.entries.add((gate, file, key))

    def allows(self, f: Finding) -> bool:
        k = (f.gate, f.file, f.key)
        if k in self.entries:
            self.used.add(k)
            return True
        return False

    def unused(self, gate: str, scope: Sequence[str]) -> List[Tuple[str, str, str]]:
        return sorted(e for e in self.entries - self.used if e[0] == gate and in_scope(e[1], scope))


def finish(gate: str, findings: Iterable[Finding], scope: Sequence[str], summary: str) -> int:
    allow = Allow()
    shown, allowed = [], 0
    for f in findings:
        if allow.allows(f):
            allowed += 1
        else:
            shown.append(f)
    for f in shown:
        print(f"{f.gate} {f.file}:{f.line}: [{f.rule}] {f.message}")
    for e in allow.unused(gate, scope):
        print(f"{gate} note: allow.txt entry matched nothing (remove it?): {e[1]} | {e[2]}")
    print(f"{gate}: {len(shown)} finding(s), {allowed} allowlisted; {summary}")
    return 1 if shown else 0

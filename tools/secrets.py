#!/usr/bin/env python3
"""V6: no secret, account number or forbidden claim in the docs.

Two layers:

1. The app's own tested gate, `scripts/leak_guard.py`, EXTRACTED FROM REF (so a
   checkout on another branch cannot change the gate), run from this
   repository's root over every tracked file. leak_guard reads `git ls-files`
   only, so this gate first REFUSES while any untracked, non-ignored file
   exists: `git add -A`, then run it. Its findings are parsed, filtered to the
   scope and to tools/allow.txt; if it fails and its output cannot be parsed,
   this gate fails (closed).

2. Rules leak_guard does not have:
     bare-account   a bare 12-digit number not inside a longer token or a UUID
                    tail; only the AWS documentation placeholder 123456789012
                    is allowed. (leak_guard matches an account only in an AWS
                    context such as an ARN.)
     api-key        a real-shape API key, eptk_ + 32 hex. The public demo keys
                    (eptk_demo_...) do not have that shape.
     aws-key-id     AKIA/ASIA + 16.
     github-token   ghp_/gho_/ghu_/ghs_/ghr_ + 20 or more, and github_pat_.

`--stdin` scans text on standard input (a pull request title and body) with
both layers.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List

from common import ROOT, Finding, base_parser, finish, git, in_scope, normalise_scope, repo_files, resolve_ref_and_app

EXTRA = [
    ("bare-account", re.compile(r"(?<![0-9A-Za-z_.-])[0-9]{12}(?![0-9A-Za-z_])"),
     "a 12-digit number shaped like an AWS account id; use 123456789012 or <account>"),
    ("api-key", re.compile(r"\beptk_[0-9a-f]{32}\b"), "a real-shape Controlvar API key"),
    ("aws-key-id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "an AWS access key id"),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}"), "a GitHub token"),
]
ALLOWED_ACCOUNT = {"123456789012"}

REPORT = re.compile(r"^  (?P<loc>\S.*)\n      (?P<rule>[\w-]+): (?P<value>.*)$", re.M)


def extra_findings(rel: str, text: str) -> List[Finding]:
    out = []
    for no, line in enumerate(text.split("\n"), 1):
        for rule, pat, why in EXTRA:
            for m in pat.finditer(line):
                if rule == "bare-account" and m.group(0) in ALLOWED_ACCOUNT:
                    continue
                out.append(Finding("V6", rel, no, rule, f"{why} (value withheld)", line.strip()))
    return out


def leak_guard(ref: str, app: Path, stdin_text: str = None):
    src = git(["show", f"{ref}:scripts/leak_guard.py"], cwd=app).stdout
    with tempfile.TemporaryDirectory() as d:
        lg = Path(d) / "leak_guard.py"
        lg.write_bytes(src)
        cmd = [sys.executable, str(lg)] + (["--stdin"] if stdin_text is not None else [])
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, input=stdin_text)
    return r


def parse_leak_guard(r) -> List[tuple]:
    if r.returncode == 0:
        return []
    if r.returncode != 1:
        print(r.stdout + r.stderr, file=sys.stderr)
        print("V6: leak_guard did not run cleanly (exit %d); failing closed" % r.returncode, file=sys.stderr)
        sys.exit(2)
    found = [(m.group("loc"), m.group("rule"), m.group("value")) for m in REPORT.finditer(r.stderr)]
    if not found:
        print(r.stderr, file=sys.stderr)
        print("V6: leak_guard reported a failure this gate could not parse; failing closed", file=sys.stderr)
        sys.exit(2)
    return found


def main() -> int:
    ap = base_parser("secrets.py", __doc__.split("\n")[0])
    ap.add_argument("--stdin", action="store_true", help="scan standard input (a PR title and body)")
    args = ap.parse_args()
    ref, app = resolve_ref_and_app(args)

    if args.stdin:
        text = sys.stdin.read()
        findings = extra_findings("<stdin>", text)
        for loc, rule, value in parse_leak_guard(leak_guard(ref, app, text)):
            findings.append(Finding("V6", "<stdin>", 0, f"leak-guard:{rule}", "leak_guard finding (value withheld)", ""))
        for f in findings:
            print(f"V6 <stdin>:{f.line}: [{f.rule}] {f.message}")
        print(f"V6: {len(findings)} finding(s) in {len(text)} bytes of standard input")
        return 1 if findings else 0

    scope = normalise_scope(args.paths)
    untracked = [p for p in git(["ls-files", "-z", "--others", "--exclude-standard"]).stdout.decode().split("\0") if p]
    if untracked:
        print("V6: refusing: untracked files are invisible to leak_guard. Run `git add -A` first. Untracked:")
        for p in untracked[:20]:
            print(f"  {p}")
        return 2

    findings: List[Finding] = []
    lines_cache = {}
    for loc, rule, value in parse_leak_guard(leak_guard(ref, app)):
        rel, _, no = loc.rpartition(":")
        if not rel or not no.isdigit():
            rel, no = loc, "0"
        if not in_scope(rel, scope):
            continue
        if rel not in lines_cache:
            p = ROOT / rel
            lines_cache[rel] = p.read_text(encoding="utf-8", errors="replace").split("\n") if p.is_file() else []
        lines = lines_cache[rel]
        n = int(no)
        key = lines[n - 1].strip() if 0 < n <= len(lines) else ""
        shown = "(value withheld)" if rule in ("aws-account-id", "home-directory-path") else value[:80]
        findings.append(Finding("V6", rel, n, f"leak-guard:{rule}", shown, key))

    scanned = 0
    for rel in repo_files(untracked=False):
        if not in_scope(rel, scope):
            continue
        p = ROOT / rel
        if not p.is_file():
            continue
        data = p.read_bytes()
        if b"\0" in data[:8192]:
            continue
        scanned += 1
        findings.extend(extra_findings(rel, data.decode("utf-8", errors="replace")))
    if scanned == 0:
        print("V6: no files in scope; failing closed")
        return 2
    findings.sort(key=lambda f: (f.file, f.line, f.rule))
    return finish("V6", findings, scope, f"leak_guard at {ref[:12]} over every tracked file; {len(EXTRA)} extra rules over {scanned} files")


if __name__ == "__main__":
    sys.exit(main())

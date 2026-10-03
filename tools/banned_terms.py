#!/usr/bin/env python3
"""V1: refuse words and commands that are false at REF, or that leak internal process.

Each markdown file is read with single newlines folded into spaces (same length,
so offsets still map to line numbers): a command wrapped across two lines, such
as `alembic` at the end of one line and `merge` at the start of the next, still
matches. Paragraph breaks (blank lines) are kept, so a match never spans two
paragraphs.

A rule may carry an EXEMPT pattern that is searched in a bounded window around
the match. That is how "never run alembic merge" passes while advice to run it
does not, and how a removed route may be named only as removed.

The canonical facts behind each rule are in the internal FACTS.md of the docs
update; the reason text below is what an editor sees.
"""

from __future__ import annotations

import re
import sys
from typing import List, NamedTuple, Optional

from common import Finding, base_parser, finish, markdown_files, normalise_scope, read


class Rule(NamedTuple):
    id: str
    pattern: "re.Pattern[str]"
    why: str
    exempt: Optional["re.Pattern[str]"] = None
    before: int = 0  # window size before the match searched for `exempt`
    after: int = 0  # window size after the match


NEG = re.compile(r"\b(?:[Nn]ever|NEVER|[Dd]o not|DO NOT|[Dd]on't|DON'T)\b")

RULES: List[Rule] = [
    Rule("old-repo", re.compile(r"amarkanday/experimentation-platform"),
         "the app is getexperimently/experimently (the bare name experimentation-platform is still live for ECR, CodeDeploy and logs)"),
    Rule("edition-tag", re.compile(r":(?:ce|ee)\b"),
         "image tags are core-X.Y.Z / full-X.Y.Z; there are no ce/ee images"),
    Rule("edition-target", re.compile(r"--target\s+(?:ce|ee)\b"),
         "Dockerfile targets are core and full"),
    Rule("edition-var", re.compile(r"EXPERIMENTLY_EDITION"),
         "there is no edition variable; the profile is core or full"),
    Rule("edition-dir", re.compile(r"(?<![\w./-])ee/"),
         "there is no ee/ directory; optional code lives under modules/"),
    Rule("edition-name", re.compile(r"(?i)\b(?:community|enterprise)\s+edition\b"),
         "there are no editions; one codebase, Apache-2.0, profiles core and full"),
    Rule("alembic-merge",
         re.compile(r"(?:\balembic\b|\$ALEMBIC\b|alembic\.ini)(?:\s+-{1,2}[\w-]+(?:[ =][^\s`]+)?){0,4}\s+merge\b"),
         "never alembic merge: two heads are the normal state; use `upgrade heads`",
         exempt=NEG, before=120),
    Rule("merge-heads", re.compile(r"merge\s+-m\s+[\"']merge heads[\"']"),
         "never alembic merge: two heads are the normal state"),
    Rule("upgrade-head", re.compile(r"\b(?:upgrade|stamp)\s+head\b"),
         "a full checkout has two heads: `upgrade heads` / `stamp heads`"),
    Rule("old-linters", re.compile(r"\b(?:black|isort|flake8)\s+--check\b|\[tool\.(?:black|isort)\]|\bflake8\b"),
         "ruff replaced black, isort and flake8",
         exempt=re.compile(r"(?i)\breplac"), before=80, after=0),
    Rule("old-deploy-workflow", re.compile(r"deploy-(?:prod|dev)\.yml"),
         "one path for every environment: deploy.yml, rollback.yml, db-migrate.yml"),
    Rule("assignment-lambda", re.compile(r"AssignmentLambda|backend/lambda/assignment"),
         "the assignment Lambda was removed (#606)"),
    Rule("sdk-count", re.compile(r"\b(?:[Ff]ifteen|15)\s+(?:client\s+)?(?:SDKs|languages|clients)\b"),
         "there are 16 SDKs (13 run against a live backend in CI)"),
    Rule("ops-command", re.compile(r"--force-new-deployment|\bwait\s+services-stable\b"),
         "no restart/rotation/restore commands beyond what deploy.yml and rollback.yml do (plan C9)"),
    Rule("internal-ref",
         re.compile(r"STATE\.md|launch-board|\bBOARD\b|experimently-internal|launch-readiness|"
                    r"(?<![\w/.-])security/|scratchpad|MEMORY\.md|\bT[1-9][0-9]{1,2}\b(?!:)"),
         "internal process references (status notes, board, team-decision numbers, private paths) never appear in the docs"),
    Rule("sso-name",
         re.compile(r"--sso-session[ =](?![<$\"'])\S+|\[(?:sso-session|profile)\s+(?!<)[^\]\s]+|AWS_PROFILE=(?![<$\"'])\S+"),
         "use placeholders: <sso-session>, <profile>"),
    Rule("helm-oci", re.compile(r"(?i)\bnot an OCI\b"),
         "D20: the chart will ALSO be an OCI package; today it is a release asset only (plan C11)"),
    Rule("removed-route", re.compile(r"edge/bootstrap|openfeature/flags"),
         "removed in the release after 0.17.0 (#770): name it only as removed",
         exempt=re.compile(r"(?i)\bremoved\b|\b404\b|no longer"), before=300, after=300),
]


def fold(text: str) -> str:
    # Same length as the input: offsets keep their line numbers.
    return re.sub(r"(?<!\n)\n(?!\n)", " ", text)


def window(folded: str, start: int, end: int, before: int, after: int) -> str:
    # Bounded, and never across a paragraph break.
    lo = max(0, start - before)
    hi = min(len(folded), end + after)
    left = folded[lo:start]
    right = folded[end:hi]
    if "\n\n" in left:
        left = left[left.rindex("\n\n") + 2:]
    if "\n\n" in right:
        right = right[: right.index("\n\n")]
    return left + folded[start:end] + right


def scan(rel: str, text: str) -> List[Finding]:
    folded = fold(text)
    lines = text.split("\n")
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln) + 1)
    out = []
    for r in RULES:
        for m in r.pattern.finditer(folded):
            if r.exempt is not None and r.exempt.search(
                window(folded, m.start(), m.end(), r.before, r.after)
            ):
                continue
            # line number of the match start
            lo, hi = 0, len(lines)
            while lo < hi:
                mid = (lo + hi) // 2
                if starts[mid + 1] <= m.start():
                    lo = mid + 1
                else:
                    hi = mid
            no = lo + 1
            out.append(Finding("V1", rel, no, r.id, f"{m.group(0)!r}: {r.why}", lines[no - 1].strip()))
    return out


def main() -> int:
    ap = base_parser("banned_terms.py", __doc__.split("\n")[0], needs_app=False)
    args = ap.parse_args()
    scope = normalise_scope(args.paths)
    files = markdown_files(scope)
    findings: List[Finding] = []
    for rel in files:
        findings.extend(scan(rel, read(rel)))
    findings.sort(key=lambda f: (f.file, f.line, f.rule))
    return finish("V1", findings, scope, f"{len(files)} markdown files, {len(RULES)} rules")


if __name__ == "__main__":
    sys.exit(main())

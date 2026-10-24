#!/usr/bin/env bash
# Run every docs gate (V1-V6) and fail if any of them fails.
#
#   CONTROLVAR_APP=<clone of Controlvar/controlvar> tools/check.sh [PATH ...]
#   make check SCOPE="learn-python-in-24-hours sdk-engineering"
#
# PATHs (relative to the repository root) scope V1, V2, V3, V5 and V6 to one
# pull request's files. V4 (links) always checks the whole tree, because links
# and anchors cross books. With no PATH every gate checks the whole repository.
#
# REF comes from tools/REF; `REF=v0.18.0 tools/check.sh` overrides it. A moving
# ref (main, HEAD, origin/...) is refused.
#
# Every gate runs even when an earlier one fails, so one run shows everything.
# Exit status: 0 only when every gate exits 0. A gate that is not implemented for
# the scope (V5 today) exits 3, and that is a failure.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

root="$(git rev-parse --show-toplevel)"
cd "$root"

export CONTROLVAR_APP="${CONTROLVAR_APP:-${EXPERIMENTLY_APP:-}}"
if [ -z "$CONTROLVAR_APP" ]; then
  echo "error: set CONTROLVAR_APP to a clone of Controlvar/controlvar" >&2
  exit 2
fi

ref_args=()
if [ -n "${REF:-}" ]; then
  ref_args=(--ref "$REF")
fi
scope=("$@")

names=()
codes=()
run() {
  local name="$1"
  shift
  echo "==== $name"
  local rc=0
  "$@" || rc=$?
  names+=("$name")
  codes+=("$rc")
  echo
}

run "V1 banned terms" python3 tools/banned_terms.py ${scope[@]+"${scope[@]}"}
run "V2 paths at REF" python3 tools/paths_at_ref.py ${ref_args[@]+"${ref_args[@]}"} ${scope[@]+"${scope[@]}"}
run "V3 tagged snippets" python3 tools/snippets.py ${ref_args[@]+"${ref_args[@]}"} ${scope[@]+"${scope[@]}"}
run "V4 links (whole tree)" python3 tools/links.py
run "V5 numbers" python3 tools/numbers.py ${scope[@]+"${scope[@]}"}
run "V6 secrets" python3 tools/secrets.py ${ref_args[@]+"${ref_args[@]}"} ${scope[@]+"${scope[@]}"}

echo "==== summary (REF ${REF:-$(cat tools/REF)})"
failed=0
for i in "${!names[@]}"; do
  case "${codes[$i]}" in
    0) verdict="pass" ;;
    1) verdict="FAIL (findings)" ;;
    3) verdict="FAIL (not implemented for this scope)" ;;
    *) verdict="FAIL (setup error, exit ${codes[$i]})" ;;
  esac
  [ "${codes[$i]}" = 0 ] || failed=1
  printf '  %-24s %s\n' "${names[$i]}" "$verdict"
done
exit "$failed"

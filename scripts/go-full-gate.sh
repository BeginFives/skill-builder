#!/usr/bin/env bash
# golang-engineer :: Stop / SubagentStop full gate
#
# Before the turn is allowed to finish, run the whole definition-of-done across the module:
# build + vet + golangci-lint + `go test -race`. On failure it BLOCKS completion (exit 2) so
# Claude keeps working until the suite is green. This is the "cannot ship red" guarantee.
#
# Loop safety: if the payload says stop_hook_active, we already blocked once — exit 0 so the
# session can never get stuck in an infinite stop loop.
#
# Degrades gracefully (exit 0) with no Go toolchain or no go.mod at the project root.
#
# Escape hatches:
#   GOLANG_ENGINEER_SKIP_FULL_GATE=1  -> disable entirely
#   GOLANG_ENGINEER_SKIP_TESTS=1      -> build+vet+lint only (skip the -race test run)
set -uo pipefail

[ "${GOLANG_ENGINEER_SKIP_FULL_GATE:-0}" = "1" ] && exit 0

json=$(cat)

# Never re-block if this stop hook is already what's keeping us alive.
case "$json" in
  *'"stop_hook_active":true'* | *'"stop_hook_active": true'*) exit 0 ;;
esac

root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" 2>/dev/null || exit 0
[ -f go.mod ] || exit 0
command -v go >/dev/null 2>&1 || exit 0

fail() {
  {
    printf 'golang-engineer FULL GATE FAILED (module: %s)\n\n' "$root"
    printf '%s\n\n' "$1"
    printf 'The definition of done is not met (review-rubric §10). Fix it, then finish.\n'
  } >&2
  exit 2
}

if ! out=$(go build ./... 2>&1); then
  fail "go build ./...:
$out"
fi

if ! out=$(go vet ./... 2>&1); then
  fail "go vet ./...:
$out"
fi

if command -v golangci-lint >/dev/null 2>&1; then
  if ! out=$(golangci-lint run 2>&1); then
    fail "golangci-lint run:
$out"
  fi
fi

if [ "${GOLANG_ENGINEER_SKIP_TESTS:-0}" != "1" ]; then
  if ! out=$(go test -race ./... 2>&1); then
    fail "go test -race ./...:
$out"
  fi
fi

exit 0

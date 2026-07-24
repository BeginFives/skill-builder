#!/usr/bin/env bash
# golang-engineer :: PostToolUse gate (THE enforcement payload)
#
# After any Go file is written/edited, build + vet (+ golangci-lint) that file's package.
# On failure this BLOCKS the turn with exit code 2; stderr is fed back to Claude, which must
# fix it before continuing. This is what makes the rubric's [TOOL] items un-skippable —
# the model cannot "forget" to run them.
#
# Degrades gracefully (exit 0, no block) when: not a .go file, no Go toolchain, or the file
# is not inside a Go module. Never blocks on something the developer can't act on.
#
# Escape hatches:
#   GOLANG_ENGINEER_SKIP_GATE=1  -> disable this gate entirely
#   GOLANG_ENGINEER_SKIP_LINT=1  -> run build+vet but skip golangci-lint
set -uo pipefail

[ "${GOLANG_ENGINEER_SKIP_GATE:-0}" = "1" ] && exit 0

json=$(cat)

get_file_path() {
  if command -v jq >/dev/null 2>&1; then
    jq -r '.tool_input.file_path // empty' 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import sys,json
d=json.load(sys.stdin)
ti=d.get("tool_input") or {}
print(ti.get("file_path") or "")' 2>/dev/null
  else
    sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
  fi
}

file=$(printf '%s' "$json" | get_file_path)

[ -n "${file:-}" ] || exit 0
case "$file" in *.go) ;; *) exit 0 ;; esac
[ -f "$file" ] || exit 0
command -v go >/dev/null 2>&1 || exit 0

dir=$(cd "$(dirname "$file")" 2>/dev/null && pwd) || exit 0
cd "$dir" || exit 0

# Only gate code that lives in a module; otherwise `go build` can't resolve anything.
gomod=$(go env GOMOD 2>/dev/null)
case "${gomod:-}" in "" | "/dev/null") exit 0 ;; esac

fail() {
  {
    printf 'golang-engineer gate FAILED in package: %s\n\n' "$dir"
    printf '%s\n\n' "$1"
    printf 'This is a hard blocker (review-rubric §10). Fix it before doing anything else.\n'
  } >&2
  exit 2
}

if ! out=$(go build -o /dev/null ./ 2>&1); then
  fail "go build:
$out"
fi

if ! out=$(go vet ./ 2>&1); then
  fail "go vet:
$out"
fi

if [ "${GOLANG_ENGINEER_SKIP_LINT:-0}" != "1" ] && command -v golangci-lint >/dev/null 2>&1; then
  if ! out=$(golangci-lint run ./ 2>&1); then
    fail "golangci-lint:
$out"
  fi
fi

exit 0

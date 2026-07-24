#!/usr/bin/env bash
# python-engineer :: PostToolUse gate (the enforcement payload)
#
# After any Python file is written/edited, run the fast per-edit check. On failure this
# BLOCKS the turn with exit code 2; stderr is fed back to Claude, which must fix it before continuing.
#
# Design note: this is the FAST check. Full build, tests and the vulnerability scan run in the Stop
# full-gate, so a single edit never pays for the whole suite. For file-oriented languages that is a
# per-file linter; for build-oriented languages it is the project's compile step (see LINT_TAKES_FILE).
#
# Degrades gracefully (exit 0) when: wrong file type, tool not installed, or no project root.
#
# Escape hatches:
#   PYTHON_ENGINEER_SKIP_GATE=1  -> disable this gate entirely
set -uo pipefail

[ "${PYTHON_ENGINEER_SKIP_GATE:-0}" = "1" ] && exit 0

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
case "$file" in *.py) ;; *) exit 0 ;; esac
[ -f "$file" ] || exit 0

# A project root is any directory holding one of the marker files (|-separated: e.g. Maven or Gradle).
markers="pyproject.toml|setup.py|setup.cfg"
has_marker() {
  local d="$1" m
  local IFS='|'
  for m in $markers; do
    [ -f "$d/$m" ] && return 0
  done
  return 1
}

dir=$(cd "$(dirname "$file")" 2>/dev/null && pwd) || exit 0
root="$dir"
while [ "$root" != "/" ] && ! has_marker "$root"; do
  root=$(dirname "$root")
done
has_marker "$root" || exit 0

lint_cmd="uvx ruff check"
[ -n "$lint_cmd" ] || exit 0
# shellcheck disable=SC2086
command -v ${lint_cmd%% *} >/dev/null 2>&1 || exit 0

cd "$root" || exit 0
# LINT_TAKES_FILE=true  -> per-file linter; pass the edited file
# LINT_TAKES_FILE=false -> project-wide check (e.g. a compile step); takes no file argument
if [ "true" = "true" ]; then
  # shellcheck disable=SC2086
  out=$($lint_cmd "$file" 2>&1)
  rc=$?
else
  # shellcheck disable=SC2086
  out=$($lint_cmd 2>&1)
  rc=$?
fi

if [ $rc -ne 0 ]; then
  {
    printf 'python-engineer gate FAILED for: %s\n\n' "$file"
    printf '%s\n\n' "$out"
    printf 'This is a hard blocker (review-rubric tooling gate). Fix it before doing anything else.\n'
  } >&2
  exit 2
fi

exit 0

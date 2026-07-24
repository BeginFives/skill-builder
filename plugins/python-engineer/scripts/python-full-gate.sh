#!/usr/bin/env bash
# python-engineer :: Stop full gate
#
# Before the turn may finish, run the whole definition-of-done across the project.
# On failure it BLOCKS completion (exit 2) so Claude keeps working until it is green.
#
# Loop safety: if the payload says stop_hook_active we already blocked once — exit 0 so the session
# can never get stuck in an infinite stop loop.
#
# Escape hatches:
#   PYTHON_ENGINEER_SKIP_FULL_GATE=1  -> disable entirely
#   PYTHON_ENGINEER_SKIP_TESTS=1      -> skip the test run
set -uo pipefail

[ "${PYTHON_ENGINEER_SKIP_FULL_GATE:-0}" = "1" ] && exit 0

json=$(cat)
case "$json" in
  *'"stop_hook_active":true'* | *'"stop_hook_active": true'*) exit 0 ;;
esac

root="${CLAUDE_PROJECT_DIR:-$PWD}"
cd "$root" 2>/dev/null || exit 0

# A project root is any directory holding one of the marker files (|-separated: e.g. Maven or Gradle).
has_marker() {
  local m
  local IFS='|'
  for m in pyproject.toml|setup.py|setup.cfg; do
    [ -f "$m" ] && return 0
  done
  return 1
}
has_marker || exit 0

fail() {
  {
    printf 'python-engineer FULL GATE FAILED (project: %s)\n\n' "$root"
    printf '%s\n\n' "$1"
    printf 'The definition of done is not met. Fix it, then finish.\n'
  } >&2
  exit 2
}

run_step() {
  # run_step "<label>" "<command>"; skips silently if the command is empty or not installed
  local label="$1" cmd="$2"
  [ -n "$cmd" ] || return 0
  # shellcheck disable=SC2086
  command -v ${cmd%% *} >/dev/null 2>&1 || return 0
  local out
  # shellcheck disable=SC2086
  if ! out=$($cmd 2>&1); then
    fail "$label:
$out"
  fi
}

run_step "" ""
run_step "uvx ruff check"  "uvx ruff check"
run_step "uvx mypy"  "uvx mypy"
if [ "${PYTHON_ENGINEER_SKIP_TESTS:-0}" != "1" ]; then
  run_step "uv run pytest -q" "uv run pytest -q"
fi

exit 0

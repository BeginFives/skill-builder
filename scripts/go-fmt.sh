#!/usr/bin/env bash
# golang-engineer :: PostToolUse formatter
#
# Auto-formats the edited Go file. This hook NEVER blocks (always exit 0) — blocking
# on whitespace the tool can fix itself just wastes a turn (rubric §10 is a [TOOL] item).
#
# Reads the hook payload as JSON on stdin; extracts .tool_input.file_path.
set -uo pipefail

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
command -v gofmt >/dev/null 2>&1 || exit 0

gofmt -w "$file" >/dev/null 2>&1 || true
if command -v goimports >/dev/null 2>&1; then
  goimports -w "$file" >/dev/null 2>&1 || true
fi

exit 0

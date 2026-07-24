#!/usr/bin/env bash
# java-engineer :: PostToolUse formatter
#
# Auto-formats the edited Java file. NEVER blocks (always exit 0) — blocking on whitespace
# the tool can fix itself just wastes a turn.
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
case "$file" in *.java) ;; *) exit 0 ;; esac
[ -f "$file" ] || exit 0

fmt_cmd="google-java-format -i"
[ -n "$fmt_cmd" ] || exit 0
# shellcheck disable=SC2086
command -v ${fmt_cmd%% *} >/dev/null 2>&1 || exit 0

# shellcheck disable=SC2086
$fmt_cmd "$file" >/dev/null 2>&1 || true
exit 0

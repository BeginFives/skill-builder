#!/usr/bin/env python3
"""Scaffold a new <lang>-engineer member of the engineering-skills plugin.

This does the DETERMINISTIC half of adding a language: create the file tree, substitute the
language's tool table into the shared templates, wire the hooks, and chmod the gate scripts.
It deliberately does NOT write the language expertise — every generated file carries TODO(author)
markers where real judgment is required. Structure is mechanical; expertise is not.

Usage:
    scripts/new-lang.py python
    scripts/new-lang.py rust --dry-run
    scripts/new-lang.py mylang --profile path/to/profile.json
    scripts/new-lang.py python --root /tmp/testplugin      # scaffold elsewhere (testing)
    scripts/new-lang.py python --force                     # overwrite an existing member
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REQUIRED_KEYS = [
    "lang", "lang_title", "ext", "ext_case", "project_marker", "toolchain_bin",
    "fmt_cmd", "lint_cmd", "type_cmd", "build_cmd", "test_cmd", "vuln_cmd", "triggers",
]

# template -> (destination relative to plugin root, executable?)
RENDER_PLAN = [
    ("SKILL.md.tmpl",            "skills/{lang}-engineer/SKILL.md",                       False),
    ("review-rubric.md.tmpl",    "skills/{lang}-engineer/references/review-rubric.md",     False),
    ("agent.md.tmpl",            "agents/{lang}-reviewer.md",                              False),
    ("implement.SKILL.md.tmpl",  "skills/{lang}-implement/SKILL.md",                       False),
    ("review.SKILL.md.tmpl",     "skills/{lang}-review/SKILL.md",                          False),
    ("fmt.sh.tmpl",              "scripts/{lang}-fmt.sh",                                  True),
    ("gate.sh.tmpl",             "scripts/{lang}-gate.sh",                                 True),
    ("full-gate.sh.tmpl",        "scripts/{lang}-full-gate.sh",                            True),
]

# Reference stubs so the SKILL.md index never points at a missing file.
REFERENCE_STUBS = [
    ("concurrency.md", "Concurrency, cancellation & leak-freedom"),
    ("errors.md", "Error handling"),
    ("logging-observability.md", "Logging, correlation (MDC) & observability"),
    ("testing.md", "Testing"),
    ("project-layout.md", "Project layout, dependency injection & boundaries"),
    ("performance.md", "Performance"),
]

STUB_BODY = """# {title} — {lang_title}

<!-- TODO(author): write this reference for {lang_title}.

     Follow the golang-engineer house style, which the A/B evals validated:
       * open with one line: "Load this when ..."
       * a "## Contents" anchor list
       * a bolded GOVERNING RULE
       * concrete, copy-adaptable templates the model adapts instead of reinventing
       * name the failure, not just the virtue ("FAIL if ...")

     Every code template MUST be compiled/run before shipping. Untested templates are how a
     skill teaches the model to write code that does not build.
-->
"""


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def load_profile(plugin_root: Path, lang: str, explicit: str | None) -> dict:
    path = Path(explicit) if explicit else plugin_root / "skills/new-lang-engineer/profiles" / f"{lang}.json"
    if not path.is_file():
        die(f"no profile at {path}. Pass --profile, or add a profile JSON with keys: {', '.join(REQUIRED_KEYS)}")
    profile = json.loads(path.read_text())
    missing = [k for k in REQUIRED_KEYS if k not in profile]
    if missing:
        die(f"profile {path} is missing keys: {', '.join(missing)}")
    return profile


def build_substitutions(p: dict) -> dict:
    exts = [e.strip() for e in p["ext_case"].split("|") if e.strip()]
    gate_cmds = " · ".join(c for c in (p["fmt_cmd"], p["lint_cmd"], p["type_cmd"],
                                       p["build_cmd"], p["test_cmd"], p["vuln_cmd"]) if c)
    subs = {
        "LANG": p["lang"],
        "LANG_TITLE": p["lang_title"],
        "LANG_UPPER": "".join(ch if ch.isalnum() else "_" for ch in p["lang"]).upper(),
        "EXT": p["ext"],
        # bash `case` alternatives: template writes "*.{{EXT_CASE_GLOB}}"
        "EXT_CASE_GLOB": "|*.".join(exts),
        "PROJECT_MARKER": p["project_marker"],
        "TOOLCHAIN_BIN": p["toolchain_bin"],
        "FMT_CMD": p["fmt_cmd"],
        "LINT_CMD": p["lint_cmd"],
        "TYPE_CMD": p["type_cmd"],
        "BUILD_CMD": p["build_cmd"],
        "TEST_CMD": p["test_cmd"],
        "VULN_CMD": p["vuln_cmd"],
        "TRIGGERS": p["triggers"],
        "GATE_CMDS": gate_cmds,
    }
    return subs


def render(text: str, subs: dict) -> str:
    for key, value in subs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def merge_hooks(hooks_path: Path, snippet: dict, dry_run: bool) -> str:
    """Append the new language's hook entries without disturbing existing ones."""
    if hooks_path.is_file():
        data = json.loads(hooks_path.read_text())
    else:
        data = {"hooks": {}}
    hooks = data.setdefault("hooks", {})
    added = []
    for event, entries in snippet.items():
        bucket = hooks.setdefault(event, [])
        for entry in entries:
            # idempotent: skip if an identical command set is already wired
            new_cmds = {h.get("command") for h in entry.get("hooks", [])}
            if any({h.get("command") for h in existing.get("hooks", [])} == new_cmds for existing in bucket):
                continue
            bucket.append(entry)
            added.append(event)
    if not dry_run:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(json.dumps(data, indent=2) + "\n")
    return ", ".join(sorted(set(added))) or "(already wired)"


def main() -> None:
    ap = argparse.ArgumentParser(description="Scaffold a <lang>-engineer plugin member.")
    ap.add_argument("lang", help="language slug, e.g. python (must match a profile in profiles/)")
    ap.add_argument("--root", default=None, help="plugin root (default: repo containing this script)")
    ap.add_argument("--profile", default=None, help="explicit path to a profile JSON")
    ap.add_argument("--force", action="store_true", help="overwrite an existing member")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written, write nothing")
    args = ap.parse_args()

    script_root = Path(__file__).resolve().parent.parent
    plugin_root = Path(args.root).resolve() if args.root else script_root
    templates = script_root / "skills/new-lang-engineer/templates"
    if not templates.is_dir():
        die(f"templates not found at {templates}")

    profile = load_profile(script_root, args.lang, args.profile)
    subs = build_substitutions(profile)
    lang = subs["LANG"]

    existing = plugin_root / f"skills/{lang}-engineer/SKILL.md"
    if existing.exists() and not args.force and not args.dry_run:
        die(f"{lang}-engineer already exists at {existing}. Use --force to overwrite.")

    written = []
    for tmpl_name, dest_tmpl, executable in RENDER_PLAN:
        tmpl = templates / tmpl_name
        if not tmpl.is_file():
            die(f"missing template {tmpl}")
        out_path = plugin_root / dest_tmpl.format(lang=lang)
        content = render(tmpl.read_text(), subs)
        if not args.dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            if executable:
                os.chmod(out_path, 0o755)
        written.append(str(out_path.relative_to(plugin_root)) + (" (+x)" if executable else ""))

    refs_dir = plugin_root / f"skills/{lang}-engineer/references"
    for fname, title in REFERENCE_STUBS:
        out_path = refs_dir / fname
        if out_path.exists() and not args.force:
            continue
        if not args.dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(STUB_BODY.format(title=title, lang_title=subs["LANG_TITLE"]))
        written.append(str(out_path.relative_to(plugin_root)) + "  [stub]")

    snippet = json.loads(render((templates / "hooks.snippet.json.tmpl").read_text(), subs))
    hooks_result = merge_hooks(plugin_root / "hooks/hooks.json", snippet, args.dry_run)

    prefix = "[dry-run] would write" if args.dry_run else "wrote"
    print(f"{prefix} {len(written)} files for {lang}-engineer under {plugin_root}:")
    for w in written:
        print(f"  {w}")
    print(f"hooks: {hooks_result}")
    print(f"""
NEXT — the part a script cannot do (author these, then verify):
  1. skills/{lang}-engineer/SKILL.md         fill every TODO(author): the constitution rules with
                                             their "Why:", the [SELF] list, language-specific
                                             anti-rationalization rows.
  2. skills/{lang}-engineer/references/       replace the 6 stubs with real content + canonical
                                             templates. COMPILE/RUN every template before shipping.
  3. .../references/review-rubric.md          fill each section with concrete PASS/FAIL lines; tag
                                             [TOOL: <linter>] wherever a linter already catches it.
  4. Verify the gates actually block: feed a hook payload to scripts/{lang}-gate.sh with a
     deliberately broken file and confirm exit code 2, and a clean file and confirm 0.
  5. Add evals (evals/) and A/B the skill before trusting it.""")


if __name__ == "__main__":
    main()

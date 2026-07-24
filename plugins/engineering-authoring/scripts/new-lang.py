#!/usr/bin/env python3
"""Scaffold a new <lang>-engineer PLUGIN inside this marketplace repo.

This does the DETERMINISTIC half of adding a language: create the plugin tree, substitute the
language's tool table into the shared templates, write the plugin manifest and hook wiring, register
the plugin in the marketplace, and chmod the gate scripts. It deliberately does NOT write the
language expertise — every generated file carries TODO(author) markers where judgment is required.
Structure is mechanical; expertise is not.

Each language is its own installable plugin, so a Go developer never pays the context or hook cost of
Rust. The shared blueprint lives here once, so the scaffolding cannot drift between members.

Usage:
    new-lang.py python                 # scaffold plugins/python-engineer/
    new-lang.py rust --dry-run         # preview
    new-lang.py python --force         # overwrite an existing member
    new-lang.py python --check         # CI drift detector: regenerate and diff vs committed
    new-lang.py mylang --profile p.json
    new-lang.py python --repo /tmp/x   # target another marketplace repo (testing)
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

REQUIRED_KEYS = [
    "lang", "lang_title", "ext", "ext_case", "project_marker", "toolchain_bin",
    "fmt_cmd", "lint_cmd", "lint_takes_file", "type_cmd", "build_cmd", "test_cmd", "vuln_cmd", "triggers",
]

# template -> (destination relative to the PLUGIN root, executable?)
RENDER_PLAN = [
    ("plugin.json.tmpl",         ".claude-plugin/plugin.json",                          False),
    ("SKILL.md.tmpl",            "skills/{lang}-engineer/SKILL.md",                      False),
    ("review-rubric.md.tmpl",    "skills/{lang}-engineer/references/review-rubric.md",   False),
    ("agent.md.tmpl",            "agents/{lang}-reviewer.md",                            False),
    ("implement.SKILL.md.tmpl",  "skills/{lang}-implement/SKILL.md",                     False),
    ("review.SKILL.md.tmpl",     "skills/{lang}-review/SKILL.md",                        False),
    ("hooks.snippet.json.tmpl",  "hooks/hooks.json",                                     False),
    ("fmt.sh.tmpl",              "scripts/{lang}-fmt.sh",                                True),
    ("gate.sh.tmpl",             "scripts/{lang}-gate.sh",                               True),
    ("full-gate.sh.tmpl",        "scripts/{lang}-full-gate.sh",                          True),
]

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
       * where the domain has competing tools (concurrency especially), open with a
         "which tool, when" decision matrix (situation | reach for | not), and tell the
         reader to prefer the higher row = the simpler tool
       * concrete, copy-adaptable templates the model adapts instead of reinventing
       * name the failure, not just the virtue ("FAIL if ...")

     Every code template MUST be compiled/run before shipping. Untested templates are how a
     skill teaches the model to write code that does not build.
-->
"""

# files whose content is fully generated (no authoring expected) — these are drift-checked
GENERATED_EXACT = (".claude-plugin/plugin.json", "hooks/hooks.json")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def load_profile(authoring_root: Path, lang: str, explicit: str | None) -> dict:
    path = Path(explicit) if explicit else authoring_root / "skills/new-lang-engineer/profiles" / f"{lang}.json"
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
    return {
        "LANG": p["lang"],
        "LANG_TITLE": p["lang_title"],
        "LANG_UPPER": "".join(ch if ch.isalnum() else "_" for ch in p["lang"]).upper(),
        "EXT": p["ext"],
        "EXT_CASE_GLOB": "|*.".join(exts),  # template writes "*.{{EXT_CASE_GLOB}}"
        "PROJECT_MARKER": p["project_marker"],
        "TOOLCHAIN_BIN": p["toolchain_bin"],
        "FMT_CMD": p["fmt_cmd"],
        "LINT_CMD": p["lint_cmd"],
        "LINT_TAKES_FILE": p["lint_takes_file"],
        "TYPE_CMD": p["type_cmd"],
        "BUILD_CMD": p["build_cmd"],
        "TEST_CMD": p["test_cmd"],
        "VULN_CMD": p["vuln_cmd"],
        "TRIGGERS": p["triggers"],
        "GATE_CMDS": gate_cmds,
    }


def render(text: str, subs: dict) -> str:
    for key, value in subs.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def is_stub(rel: str, lang: str) -> bool:
    return rel.startswith(f"skills/{lang}-engineer/references/") and not rel.endswith("review-rubric.md")


def plan_files(templates: Path, subs: dict) -> dict:
    """Return {plugin-relative path: (rendered content, executable)}."""
    lang = subs["LANG"]
    out = {}
    for tmpl_name, dest_tmpl, executable in RENDER_PLAN:
        tmpl = templates / tmpl_name
        if not tmpl.is_file():
            die(f"missing template {tmpl}")
        content = render(tmpl.read_text(), subs)
        if dest_tmpl == "hooks/hooks.json":
            # the snippet is just the events object; a plugin hooks.json wraps it
            content = json.dumps({"hooks": json.loads(content)}, indent=2) + "\n"
        out[dest_tmpl.format(lang=lang)] = (content, executable)
    for fname, title in REFERENCE_STUBS:
        out[f"skills/{lang}-engineer/references/{fname}"] = (
            STUB_BODY.format(title=title, lang_title=subs["LANG_TITLE"]), False)
    return out


def register_in_marketplace(repo: Path, lang: str, dry_run: bool) -> str:
    path = repo / ".claude-plugin/marketplace.json"
    if not path.is_file():
        return "(no marketplace.json — skipped)"
    data = json.loads(path.read_text())
    plugins = data.setdefault("plugins", [])
    name = f"{lang}-engineer"
    if any(p.get("name") == name for p in plugins):
        return "already registered"
    plugins.append({
        "name": name,
        "source": f"./plugins/{name}",
        "category": "development",
        "tags": [lang, "code-quality", "code-review", "hooks"],
    })
    if not dry_run:
        path.write_text(json.dumps(data, indent=2) + "\n")
    return "registered"


def run_check(plugin_root: Path, files: dict, lang: str) -> None:
    """CI drift detector: the mechanically-generated files must still match the templates."""
    drifted, missing = [], []
    for rel, (content, _x) in files.items():
        if is_stub(rel, lang):
            continue  # stubs are meant to be replaced by authored content
        if not (rel in GENERATED_EXACT or rel.startswith("scripts/")):
            continue  # SKILL.md / rubric / agent are authored after generation
        target = plugin_root / rel
        if not target.is_file():
            missing.append(rel)
        elif target.read_text() != content:
            drifted.append(rel)
    if missing or drifted:
        print(f"DRIFT for {lang}-engineer:")
        for m in missing:
            print(f"  missing:  {m}")
        for d in drifted:
            print(f"  drifted:  {d}")
            a = (plugin_root / d).read_text().splitlines()
            b = files[d][0].splitlines()
            for line in list(difflib.unified_diff(a, b, "committed", "generated", lineterm=""))[:20]:
                print(f"    {line}")
        sys.exit(1)
    print(f"no drift: {lang}-engineer generated scaffolding matches the templates")


def main() -> None:
    ap = argparse.ArgumentParser(description="Scaffold a <lang>-engineer plugin.")
    ap.add_argument("lang", help="language slug, e.g. python (must match a profile)")
    ap.add_argument("--repo", default=None, help="marketplace repo root (default: this plugin's repo)")
    ap.add_argument("--profile", default=None, help="explicit path to a profile JSON")
    ap.add_argument("--force", action="store_true", help="overwrite an existing member")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="drift detector: diff regenerated scaffolding against what is committed")
    args = ap.parse_args()

    authoring_root = Path(__file__).resolve().parent.parent          # plugins/engineering-authoring
    repo = Path(args.repo).resolve() if args.repo else authoring_root.parent.parent
    templates = authoring_root / "skills/new-lang-engineer/templates"
    if not templates.is_dir():
        die(f"templates not found at {templates}")

    profile = load_profile(authoring_root, args.lang, args.profile)
    subs = build_substitutions(profile)
    lang = subs["LANG"]
    plugin_root = repo / "plugins" / f"{lang}-engineer"
    files = plan_files(templates, subs)

    if args.check:
        run_check(plugin_root, files, lang)
        return

    existing = plugin_root / f"skills/{lang}-engineer/SKILL.md"
    if existing.exists() and not args.force and not args.dry_run:
        die(f"{lang}-engineer already exists at {plugin_root}. Use --force to overwrite.")

    written = []
    for rel, (content, executable) in files.items():
        out_path = plugin_root / rel
        stub = is_stub(rel, lang)
        if stub and out_path.exists() and not args.force:
            continue
        if not args.dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(content)
            if executable:
                os.chmod(out_path, 0o755)
        written.append(rel + (" (+x)" if executable else ("  [stub]" if stub else "")))

    market = register_in_marketplace(repo, lang, args.dry_run)

    prefix = "[dry-run] would write" if args.dry_run else "wrote"
    print(f"{prefix} {len(written)} files to plugins/{lang}-engineer/ :")
    for w in sorted(written):
        print(f"  {w}")
    print(f"marketplace: {market}")
    print(f"""
NEXT — the part a script cannot do (author these, then verify):
  1. skills/{lang}-engineer/SKILL.md         fill every TODO(author): constitution rules with their
                                             "Why:", the [SELF] list, language-specific
                                             anti-rationalization rows.
  2. skills/{lang}-engineer/references/       replace the 6 stubs with real content + canonical
                                             templates. COMPILE/RUN every template before shipping.
  3. .../references/review-rubric.md          concrete PASS/FAIL lines; tag [TOOL: <linter>] wherever
                                             a linter already catches it.
  4. Verify the gate actually blocks (expect exit 2):
       printf '{{"tool_name":"Edit","tool_input":{{"file_path":"/abs/broken.{subs['EXT']}"}}}}' \\
         | bash plugins/{lang}-engineer/scripts/{lang}-gate.sh; echo "exit=$?"
  5. Add evals and A/B the skill before trusting it.""")


if __name__ == "__main__":
    main()

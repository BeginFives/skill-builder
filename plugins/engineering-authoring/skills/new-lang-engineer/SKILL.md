---
name: new-lang-engineer
description: Scaffolds a new <lang>-engineer member of the engineering-skills plugin — the skill, rubric, reviewer subagent, implement/review workflows, gate scripts and hook wiring — from the shared blueprint. Invoke explicitly to add a language (python, rust, typescript, java, or a custom profile).
disable-model-invocation: true
argument-hint: "[language slug, e.g. python]"
---

# /new-lang-engineer

Add a language to this plugin. The split is deliberate:

- **The script does the deterministic half** — file tree, tool-table substitution, hook wiring, chmod.
- **You do the judgment half** — the language's actual expertise. A generator cannot know what makes
  Rust ownership or Python asyncio go wrong in production.

Do not hand-create the tree; the script exists so every member has an identical shape and the hook
wiring can't drift.

## 1. Scaffold

Each language is its **own installable plugin** under `plugins/<lang>-engineer/`, so a Go developer
never pays the context or hook cost of Rust. The generator writes the whole plugin and registers it
in the marketplace:

```bash
plugins/engineering-authoring/scripts/new-lang.py <lang>            # python | rust | typescript | java
plugins/engineering-authoring/scripts/new-lang.py <lang> --dry-run  # preview first
plugins/engineering-authoring/scripts/new-lang.py <lang> --check    # CI drift detector
```

`--check` regenerates the mechanical files (plugin manifest, hooks, gate scripts) and diffs them
against what's committed — run it in CI so a hand-edited member can't silently drift from the mold.

For a language with no profile, copy `skills/new-lang-engineer/profiles/python.json`, fill in the tool
table, and pass `--profile path/to/profile.json`. A profile is just the language's commands:

| key | meaning |
|---|---|
| `ext`, `ext_case` | file extensions the hooks match (`ts\|tsx` for multiples) |
| `project_marker` | the file that identifies a project root (`pyproject.toml`, `Cargo.toml`) |
| `fmt_cmd` / `lint_cmd` / `type_cmd` | per-file formatter, linter, type-checker |
| `build_cmd` / `test_cmd` / `vuln_cmd` | project-wide gate commands |
| `triggers` | keywords for the skill description — the #1 activation lever |

Leave a command empty when the language has no equivalent; the gates skip empty and not-installed
commands instead of failing.

## 2. Author the expertise (the part that matters)

The generated files are correct in *shape* and empty in *substance*. Every one carries
`TODO(author)` markers. Work through them:

1. **`SKILL.md`** — the constitution. Keep every rule's `*Why:*` clause; reason-based rules survive
   paraphrase and novel situations, bare imperatives don't. Add the `[SELF]` list (what no linter can
   see in this language) and 3–5 language-specific anti-rationalization rows — the excuses *this*
   language invites ("the GIL makes this safe", "I'll unwrap it for now", "any is fine here").
2. **`references/*.md`** — replace the six stubs. Each: "Load this when…", a Contents list, a bolded
   governing rule, and canonical copy-adaptable templates.
3. **`references/review-rubric.md`** — concrete PASS/FAIL lines per section. Tag `[TOOL: <linter>]`
   wherever a linter already catches it, so effort concentrates on the `[SELF]` items.

**Verify every code template compiles/runs before shipping it.** An untested template teaches the
model to write code that doesn't build — we caught exactly that bug in `golang-engineer` only because
we compiled the templates.

Keep `SKILL.md` under ~500 lines; bulk belongs in references, loaded on demand.

## 3. Verify the gates actually block

Never assume a hook works. Feed it a payload directly:

```bash
# should BLOCK -> exit 2
printf '{"tool_name":"Edit","tool_input":{"file_path":"/abs/path/broken.<ext>"}}' \
  | bash plugins/<lang>-engineer/scripts/<lang>-gate.sh; echo "exit=$?"

# should PASS -> exit 0
printf '{"tool_name":"Edit","tool_input":{"file_path":"/abs/path/clean.<ext>"}}' \
  | bash plugins/<lang>-engineer/scripts/<lang>-gate.sh; echo "exit=$?"
```

If the language's linter isn't installed locally, prove the *blocking path* with a stub linter on
`PATH` that exits non-zero — otherwise you've only tested the graceful-degradation branch.

Also confirm it exits 0 for a non-matching file type, when the toolchain is absent, and outside a
project root — a gate that blocks on something the developer can't act on is worse than no gate.

## 4. Prove it earns its keep

Add evals under `evals/` and A/B the skill (baseline = no skill, enabled = skill) before trusting it.
Expect a humbling result: on well-scoped single tasks, capable models already know most language
best-practice, so **the measurable wins are usually behavioral (scope discipline) and the enforcement
layer — not the facts.** Keep only the content that moves an eval.

## What is shared vs language-specific

| Shared (comes from the blueprint) | Language-specific (you author) |
|---|---|
| 3-layer architecture; hook wiring; gate-script shape | the tool table (profile) |
| scope-discipline section; anti-rationalization format | the language's rules and excuses |
| Definition-of-Done block; `[TOOL]`/`[SELF]` legend | which items are `[TOOL]` vs `[SELF]` |
| reviewer-subagent shape; implement/review workflows | the rubric's concrete PASS/FAIL lines |

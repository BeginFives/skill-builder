# engineering-skills

Production-grade engineering skills for Claude Code, with **enforcement**, not just advice.
A marketplace of per-language plugins. First member: **`golang-engineer`**.

## Install

This repo is a marketplace, so it installs the same way whether it lives on your disk or on GitHub.
**Local — no publishing required:**

```
/plugin marketplace add /Users/los/Development/skill-builder
/plugin install golang-engineer@engineering-skills
```

If the repo is ever made public, the identical flow works from GitHub — nothing else changes:

```
/plugin marketplace add BeginFives/skill-builder
```

Each language is a **separate plugin**, so you install only what you work in — a Go developer never
pays the context or hook cost of Rust.

| Plugin | What it is |
|---|---|
| `golang-engineer` | MAANG-principal-grade Go, enforced by blocking gates |
| `engineering-authoring` | the mold: `/new-lang-engineer` + generator for adding a language |

## Why this exists

Most coding skills are prose: the model reads good advice, agrees with it, and still forgets it under
load. A/B runs on this repo's own eval harness confirmed it — a frontier model already *knows*
production Go, so a knowledge-only skill moves the needle very little on single tasks.

So each plugin separates three layers and lets each do its job:

| Layer | Where | Determinism |
|---|---|---|
| **Knowledge** — the rubric | `skills/<lang>-engineer/` + `references/` | low on its own |
| **Workflow** — the gated loop | `/<lang>-implement`, `/<lang>-review`, reviewer subagent | medium |
| **Enforcement** — the toolchain, blocking | `hooks/` + `scripts/` | **high — this is the point** |

The hooks make the rubric's mechanical items **un-skippable**. Guidance and enforcement can't drift,
because the hooks and the workflow skills call the *same* scripts.

## Repo layout

```
.claude-plugin/marketplace.json   lists the plugins
plugins/
  golang-engineer/                ← installable plugin
    .claude-plugin/plugin.json
    skills/golang-engineer/       the knowledge + the 16-section rubric
      ├── SKILL.md                constitution, scope discipline, anti-rationalization table
      └── references/             concurrency, errors, logging/observability, testing,
                                  project-layout, performance, data-stores, security,
                                  resilience, review-rubric.md, .golangci.yml
    skills/go-implement/          /go-implement — gated plan→RED→implement→gate→review
    skills/go-review/             /go-review — delegates to the read-only reviewer
    agents/go-reviewer.md         isolated reviewer bound to the rubric
    hooks/hooks.json              wires the gates
    scripts/                      the gates themselves
  engineering-authoring/          ← the mold (templates, profiles, new-lang.py)
evals/                            A/B eval harness + fixtures (dev-only, not shipped)
BLUEPRINT.md                      design + the empirical findings behind it
```

## What blocks, and when

| Trigger | Script | Behavior |
|---|---|---|
| PostToolUse on `Write`/`Edit`/`MultiEdit` of a `.go` file | `go-fmt.sh` | `gofmt`/`goimports` the file. **Never blocks** — blocking on whitespace wastes a turn. |
| …then, same event | `go-gate.sh` | `go build` + `go vet` (+ `golangci-lint`) on that file's **package**. **Blocks (exit 2)** on failure; the output is fed back to fix. |
| `Stop` (turn trying to finish) | `go-full-gate.sh` | `go build ./...` + `go vet ./...` + `golangci-lint run` + **`go test -race ./...`**. **Blocks completion** until green. |

Verified behavior: compile errors, `vet` findings, failing tests and real data races all block; a
clean package passes; non-Go files, a missing toolchain and code outside a module pass through
silently rather than blocking on something you can't act on. The Stop gate honors `stop_hook_active`,
so it can never loop forever.

### Escape hatches

| Env var | Effect |
|---|---|
| `GOLANG_ENGINEER_SKIP_GATE=1` | disable the per-edit gate |
| `GOLANG_ENGINEER_SKIP_LINT=1` | per-edit gate runs build+vet but skips `golangci-lint` |
| `GOLANG_ENGINEER_SKIP_FULL_GATE=1` | disable the Stop gate |
| `GOLANG_ENGINEER_SKIP_TESTS=1` | Stop gate skips the `-race` test run |

Generated members use the same pattern with their own prefix (`PYTHON_ENGINEER_SKIP_GATE`, …).

## Requirements

Go toolchain (built and verified on Go 1.25). `golangci-lint` and `govulncheck` are optional — the
gates use them when present and skip them when not. A canonical config ships at
`skills/golang-engineer/references/.golangci.yml` (golangci-lint v1 schema; run
`golangci-lint migrate` for v2). Its linter set converts self-attested rubric items into
machine-checked ones: `errcheck`, `contextcheck`, `noctx`, `bodyclose`, `rowserrcheck`,
`sqlclosecheck`, `copyloopvar`, `gosec`.

## Adding a language

```bash
plugins/engineering-authoring/scripts/new-lang.py python --dry-run   # preview
plugins/engineering-authoring/scripts/new-lang.py python             # scaffold the plugin
plugins/engineering-authoring/scripts/new-lang.py python --check     # CI drift detector
```

Profiles ship for `python`, `rust`, `typescript`, `java`; any other language needs only a small
profile JSON (file extensions, project marker, tool table). The generator does the **deterministic**
half — plugin tree, manifest, tool-table substitution, hook wiring, marketplace registration,
`chmod` — so every member has an identical shape. It deliberately does **not** write the language
expertise: every generated file carries `TODO(author)` markers, and `/new-lang-engineer` walks you
through filling them.

| Shared (from the blueprint) | Language-specific (you author) |
|---|---|
| 3-layer architecture, hook wiring, gate-script shape | the tool table (profile) |
| scope discipline, anti-rationalization format | the language's rules and excuses |
| Definition-of-Done block, `[TOOL]`/`[SELF]` legend | the rubric's concrete PASS/FAIL lines |
| reviewer-subagent shape, implement/review workflows | which items are `[TOOL]` vs `[SELF]` |

`--check` regenerates the mechanical files and diffs them against what's committed, so a hand-edited
member can't silently drift from the mold. **Verify the generated gate actually blocks before
trusting it** — the skill documents exactly how.

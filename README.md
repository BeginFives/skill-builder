# engineering-skills

Production-grade engineering skills for Claude Code, with **enforcement**, not just advice.
First member: **`golang-engineer`** — Go to a MAANG-principal-engineer bar.

## Why this exists

Most coding skills are prose: the model reads good advice, agrees with it, and still forgets it under
load. Measured A/B runs on this repo's own eval harness confirmed it — a frontier model already
*knows* production Go, so a knowledge-only skill moves the needle very little on single tasks.

So this plugin separates three layers and lets each do its job:

| Layer | Where | Determinism |
|---|---|---|
| **Knowledge** — the rubric | `skills/golang-engineer/` + `references/` | low on its own |
| **Workflow** — the gated loop | `/go-implement`, `/go-review`, `go-reviewer` subagent | medium |
| **Enforcement** — the toolchain, blocking | `hooks/` + `scripts/` | **high — this is the point** |

The hooks make the rubric's mechanical items **un-skippable**. Guidance and enforcement can't drift,
because the hooks and the workflow skills call the *same* scripts.

## What's inside

```
.claude-plugin/plugin.json     manifest
skills/golang-engineer/        the knowledge + the rubric (auto-loads on Go work)
  ├── SKILL.md                 constitution, scope discipline, anti-rationalization table
  └── references/              concurrency, errors, logging/observability, testing,
                               project-layout, performance, data-stores, security,
                               resilience, review-rubric.md, .golangci.yml
skills/go-implement/           /go-implement — the gated plan→RED→implement→gate→review loop
skills/go-review/              /go-review — delegates to the read-only reviewer subagent
skills/new-lang-engineer/      the mold: templates + profiles for adding a language
agents/go-reviewer.md          isolated, read-only reviewer bound to the rubric
hooks/hooks.json               wires the gates
scripts/                       the gates themselves (single source of truth) + new-lang.py
evals/                         A/B eval harness + fixtures (dev-only)
```

## Adding a language

`golang-engineer` is member #1 of a family. To add another:

```bash
scripts/new-lang.py python --dry-run   # preview
scripts/new-lang.py python             # scaffold
```

Profiles ship for `python`, `rust`, `typescript`, `java`; any other language just needs a small
profile JSON (its file extensions, project marker, and tool table). The generator does the
**deterministic** half — file tree, tool-table substitution, hook wiring (idempotent), `chmod` — so
every member has an identical shape and the wiring can't drift. It deliberately does **not** write
the language expertise: every generated file carries `TODO(author)` markers where real judgment is
required, and `/new-lang-engineer` walks you through filling them.

| Shared (from the blueprint) | Language-specific (you author) |
|---|---|
| 3-layer architecture, hook wiring, gate-script shape | the tool table (profile) |
| scope discipline, anti-rationalization format | the language's rules and excuses |
| Definition-of-Done block, `[TOOL]`/`[SELF]` legend | the rubric's concrete PASS/FAIL lines |
| reviewer-subagent shape, implement/review workflows | which items are `[TOOL]` vs `[SELF]` |

Verify the generated gate actually blocks before trusting it — the skill documents exactly how.

## What blocks, and when

| Trigger | Script | Behavior |
|---|---|---|
| PostToolUse on `Write`/`Edit`/`MultiEdit` of a `.go` file | `go-fmt.sh` | `gofmt`/`goimports` the file. **Never blocks** (exit 0) — blocking on whitespace wastes a turn. |
| …then, same event | `go-gate.sh` | `go build` + `go vet` (+ `golangci-lint`) on that file's **package**. **Blocks (exit 2)** on failure; the compiler/vet output is fed back to fix. |
| `Stop` (turn trying to finish) | `go-full-gate.sh` | `go build ./...` + `go vet ./...` + `golangci-lint run` + **`go test -race ./...`**. **Blocks completion** until green. |

Verified behavior: compile errors, `vet` findings, failing tests, and real data races all block; a
clean package passes; non-Go files, a missing Go toolchain, and code outside a module all pass
through silently rather than blocking on something you can't act on. The Stop gate honors
`stop_hook_active`, so it can never loop forever.

### Escape hatches

| Env var | Effect |
|---|---|
| `GOLANG_ENGINEER_SKIP_GATE=1` | disable the per-edit gate |
| `GOLANG_ENGINEER_SKIP_LINT=1` | per-edit gate runs build+vet but skips `golangci-lint` |
| `GOLANG_ENGINEER_SKIP_FULL_GATE=1` | disable the Stop gate |
| `GOLANG_ENGINEER_SKIP_TESTS=1` | Stop gate skips the `-race` test run |

## Requirements

Go toolchain (built and verified on Go 1.25). `golangci-lint` and `govulncheck` are optional — the
gates use them when present and skip them when not. A canonical config ships at
`skills/golang-engineer/references/.golangci.yml` (golangci-lint v1 schema; run
`golangci-lint migrate` for v2). Its linter set is chosen to convert self-attested rubric items into
machine-checked ones: `errcheck`, `contextcheck`, `noctx`, `bodyclose`, `rowserrcheck`,
`sqlclosecheck`, `copyloopvar`, `gosec`.

## Usage

`golang-engineer` loads automatically when you work on Go. For the disciplined loop use
`/go-implement <what to build>`; to review a change use `/go-review`. The rubric at
`skills/golang-engineer/references/review-rubric.md` is the definition of done — the model emits it
filled in, and the hooks enforce its mechanical half.

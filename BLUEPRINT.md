# Engineering Skills — Blueprint

Building the best language-engineer skills on the market. First member: **`golang-engineer`**.
Goal: code that meets a MAANG principal engineer's bar — correct concurrency, no goroutine/memory
leaks, no panics, disciplined error handling, structured + MDC/contextual logging, first-class
observability — and does so **deterministically and dependably** enough to move to production.

Decisions locked (2026-07-19):
- **Enforcement:** blocking hooks (PostToolUse/Stop gates that run the Go toolchain and block on failure).
- **Packaging:** an installable **plugin** (`engineering-skills`) bundling skill + subagent + workflow skills + hooks.

---

## 1. The core thesis — why most skills fail, and how we win

A skill that "beats a principal engineer" is **not a longer prompt full of good advice.** Advice is
non-deterministic: the model reads it, agrees, and still forgets it under load. The research across
the top repos in this space (compound-engineering 23k★, superpowers 257k★, spec-kit 122k★) converges
on one uncomfortable truth: **they are all workflow/process frameworks with almost zero deep language
expertise, and their "determinism" is mostly aspirational** — it only becomes real where they drop
into deterministic *scripts* (spec-kit's scaffolding, compound-engineering's Python brackets).

Our edge is to separate three layers and make each do its job:

| Layer | Container | Job | Determinism |
|---|---|---|---|
| **Knowledge** | `golang-engineer` skill + `references/` + `review-rubric.md` | The *what*: the Go production rubric | Low alone |
| **Workflow** | `/go-implement`, `/go-review` skills + `go-reviewer` subagent | The *how*: brainstorm → plan → TDD → implement → verify, with STOP gates | Medium |
| **Enforcement** | **hooks** running `gofmt`/`go vet`/`golangci-lint`/`govulncheck`/`go test -race` | The *proof*: code literally cannot pass unless it compiles clean & tests green | **High** |

**The market gap = our wedge:** nobody combines (a) deep principal-engineer domain knowledge as a
*checkable rubric* with (b) *hard toolchain enforcement via hooks*. Everyone else stops at prose.

---

## 2. What the five reference repos actually teach (judged, unbiased)

Star counts are **virality signals, not quality signals** — treat none as an approved standard.

| Repo | ★ (reality-checked) | What it really is | Steal | Reject |
|---|---|---|---|---|
| **EveryInc/compound-engineering** | ~23k (Every's media audience) | Mature multi-phase orchestration; machine-readable phase envelopes; risk-driven persona review with JSON findings; "compounding" knowledge docs | Progressive-disclosure spine; **persona review roster → JSON findings + dedup**; phase envelopes/unit-IDs; grounding validators; report-only + explicit apply gate | Over-engineered; 13-platform duplication; **auto external code-egress footgun**; shallow determinism; zero Go depth |
| **obra/superpowers** | ~257k (virality) | Opinionated SDLC as forced, auto-discovered markdown skills | **Eval-driven "TDD-for-skills"** (baseline-fail → close loopholes → re-test); **Skill-Discovery-Optimization** (description = pure triggers, never a workflow summary); **anti-rationalization tables**; evidence-in-report; two-stage review | Enforcement is ALL-CAPS prompt-pressure (probabilistic, not deterministic); over-gates trivial work; no language expertise |
| **github/spec-kit** | ~122k (GitHub brand) | `spec → plan → tasks → implement` CLI, agent-agnostic | **"Constitution" = non-negotiable invariants re-checked at gates**; **script-based deterministic scaffolding**; explicit STOP gates; artifacts-as-memory | Gates are LLM-grading-LLM (no ground truth); TDD is prompt-theater; waterfall; 10-command ceremony |
| **dswh/relay-crm-stdd** | ~2 (personal/workshop) | Teaching repo: SDLC as ~18 cross-referencing skills, "STDD" | SDLC decomposition into composable skills; **deep-modules glossary** (Ousterhout/Feathers) → re-express in Go; **vertical-slice/tracer-bullet** planning; **fresh-context review with pushed-in standards + stronger model** | Thin/prose-heavy; TS-only; non-deterministic ("smart zone/dumb zone"); Linear/Docker coupling; unvetted |
| **open-gsd/gsd-core** | ~0.5–1k (early) | Context-rot management: 5-phase loop, persistent `STATE.md`/`CONTEXT.md`, fresh-context subagents | **Persistent decision artifacts** surviving compaction; verification-as-required-gate; context-aware task decomposition (chunks that fit a fresh window) | Doc-heavy; not empirically validated; parallel-wave coordination underspecified |

**The pattern:** the whole market is Layer 1 + some Layer 2. **All the deep Go craft, and all the hard
Layer-3 enforcement, we build ourselves.** That's exactly the part nobody has done well.

---

## 3. Architecture — the `engineering-skills` plugin

Each Claude Code primitive maps to exactly one role. (Custom commands are now merged into skills — a
"command" is a skill with `disable-model-invocation: true`.)

```
engineering-skills/
├── .claude-plugin/
│   └── plugin.json              # {name, description, version} — ONLY file in here
├── README.md
├── skills/
│   ├── golang-engineer/         # KNOWLEDGE (model-invocable; paths: "**/*.go, **/go.mod")
│   │   ├── SKILL.md             # lean <500-line hub: high-signal conventions + WHY, links references/
│   │   └── references/          # progressive disclosure — loaded on demand, one level deep
│   │       ├── concurrency.md   #   goroutine ownership, context, errgroup, channels, race
│   │       ├── errors.md        #   %w wrapping, sentinel vs typed, errors.Is/As, handle-once
│   │       ├── logging-obs.md   #   slog, MDC/correlation IDs, OpenTelemetry, RED/USE
│   │       ├── testing.md       #   table-driven, -race, fuzz, golden, testcontainers
│   │       ├── project-layout.md#   cmd/internal/pkg, DI, accept-interfaces-return-structs
│   │       ├── performance.md   #   pprof, escape analysis, allocation discipline
│   │       └── review-rubric.md #   SINGLE SOURCE OF TRUTH (research/go-production-rubric.md)
│   ├── go-implement/            # WORKFLOW entrypoint (disable-model-invocation: true)
│   │   └── SKILL.md             #   plan → RED test → implement → run gate script → done-when-green
│   ├── go-review/               # WORKFLOW entrypoint (context: fork; agent: go-reviewer)
│   │   └── SKILL.md
│   └── new-lang-engineer/       # TEMPLATE generator (the mold for python/rust/ts/java members)
│       ├── SKILL.md
│       └── templates/           # SKILL.md.tmpl, agent.md.tmpl, implement/review tmpls, hooks.snippet.json
├── agents/
│   └── go-reviewer.md           # SUBAGENT: read-only (Write/Edit denied), skills:[golang-engineer]
│                                #   (preloads the rubric so review criteria never drift), color: green
├── hooks/
│   └── hooks.json               # ENFORCEMENT:
│                                #   PostToolUse(Write|Edit) → go-fmt.sh (auto-fix, exit 0, never blocks)
│                                #                           → go-gate.sh (vet+lint+build changed pkg; BLOCK on fail)
│                                #   Stop/SubagentStop       → go-full-gate.sh (build+vet+lint+test ./...; BLOCK until green)
├── scripts/                     # ONE source of truth, called by BOTH hooks AND workflow skills
│   ├── go-fmt.sh                #   gofmt -w + goimports; exit 0
│   ├── go-gate.sh               #   vet + golangci-lint + build on changed package; block on failure
│   └── go-full-gate.sh          #   build+vet+lint+test ./... ; block Stop until green
├── .lsp.json                    # gopls — native code intelligence (Anthropic ships an official Go LSP)
└── .mcp.json                    # OPTIONAL: govulncheck, `go test -json`, pkg.go.dev doc lookup (only for gaps)
```

**Composition story (why nothing duplicates):** `golang-engineer` is authored **once** and consumed
three ways — inline in the main thread (auto-loads on `*.go`), preloaded into the `go-reviewer`
subagent via `skills:`, and forked into by `/go-review`. The hooks enforce, un-skippably, the *exact*
standards the skill teaches, because hooks and workflow skills call the **same `scripts/`**. Guidance
and enforcement cannot drift.

**Hook blocking semantics (the determinism dial):**
- Formatters **auto-fix and exit 0** — never block (blocking on whitespace wastes turns).
- `go vet`/lint/build/test **return `{decision:"block", reason:<errors>}`** after an edit → the errors are fed back to Claude to force a fix.
- `Stop`/`SubagentStop` **block session completion** until the full suite is green.
- Scope every hook with a `Write|Edit` matcher + a `.go` check so it stays cheap for non-Go work.
- Paths via `${CLAUDE_PLUGIN_ROOT}` (install dir changes on update).

---

## 4. The determinism / verification loop

```
/go-implement <task>
   │
   ├─ 1. PLAN     restate goal + acceptance criteria; name goroutine ownership up front
   ├─ 2. RED      write a failing table-driven test; RUN it; paste the RED output (evidence, not "trust me")
   ├─ 3. GREEN    implement minimally; self-run the §1–§9,§11–12 rubric checkboxes
   ├─ 4. GATE     scripts/go-gate.sh on the changed package        ◄── hook also fires here, BLOCKS on fail
   ├─ 5. REVIEW   /go-review → go-reviewer subagent (fresh context, read-only, rubric preloaded,
   │              handed the diff + vet/lint output); returns JSON findings
   ├─ 6. FIX      address blocking findings; re-gate
   └─ 7. DONE     Stop hook runs scripts/go-full-gate.sh (build+vet+lint+test -race ./...); BLOCKS until green
```

Enforced by **hooks** (deterministic): formatting, vet, lint, build, `govulncheck`, `-race`, tests.
Prompted + rubric-checked (model): concurrency ownership, error wrapping, context propagation, MDC,
observability, layout, performance. **The valuable, hard-to-automate judgment is checkable; the
mechanical truth is enforced.**

---

## 5. Encoding domain expertise

The full **Go Production Rubric** lives in [research/go-production-rubric.md](research/go-production-rubric.md)
— 12 categories, every item a PASS/FAIL gate, sourced from Effective Go / Google / Uber style guides,
go.dev/blog, OTel docs. Sections 1–4 + 10 are hard blockers. This file becomes
`skills/golang-engineer/references/review-rubric.md` verbatim.

The skill also ships **canonical templates** the model adapts rather than reinvents: errgroup+SetLimit
worker pool, context-aware `slog.Handler` that injects trace/request IDs (MDC), graceful-shutdown HTTP
server, OTel span helper (`RecordError`+`SetStatus`), recover middleware, table-driven+fuzz test,
functional-options constructor.

---

## 6. Reusable template — one blueprint, N languages

`new-lang-engineer` is a real, runnable member that stamps out a new `<lang>-engineer` skill +
`<lang>-reviewer` subagent + implement/review skills + a hooks snippet. Spinning up a language becomes
**"fill the blueprint + swap the tooling table + wire the hook matcher"**, not a rewrite.

| Concern | Go | Python | Rust | TypeScript |
|---|---|---|---|---|
| format | gofmt/goimports | black/ruff format | rustfmt | prettier |
| lint | go vet + staticcheck + golangci-lint | ruff + mypy | clippy | eslint + tsc |
| test (+race/parallel) | `go test -race` | pytest | `cargo test` | vitest/jest |
| vuln | govulncheck | pip-audit | cargo-audit | npm audit |
| shared (language-agnostic) | rubric skeleton, review subagent shape, gate-hook wiring, TDD loop, MDC/observability principles |||

---

## 7. Risks & how we beat the reference repos

- **Prompt-pressure ≠ determinism** (superpowers' failure): we push enforcement into **hooks**, not ALL-CAPS. ✅ decided.
- **Soft LLM-graded gates** (spec-kit's failure): our gates are the **real toolchain** with objective pass/fail. ✅
- **Over-gating trivial work** (superpowers/spec-kit): a **lite fast-path** — trivial diffs skip the heavy review roster; gates stay cheap and local; documented human-override escape hatch.
- **Context bloat** (a skill-authoring trap): SKILL.md < 500 lines; bulk in `references/` loaded on demand; the reviewer runs in an isolated subagent so its output never pollutes the main thread.
- **Security footgun** (compound-engineering's auto-egress): any cross-model/external step is **opt-in with explicit consent**.
- **Toolchain assumption:** blocking hooks need a Go toolchain present; the gate scripts must degrade gracefully (detect + warn, don't hard-crash) when it isn't.
- **LLM-written-test-passes-trivially:** require **RED evidence** before GREEN, and have the reviewer check that tests exercise error/branch paths, not mocks.

---

## 8. Phased build plan

- **v0 — knowledge core (proof of value):** `golang-engineer` SKILL.md + `references/` (rubric first). Build 3–5 **evals first** (add-endpoint-with-error-wrapping; fix-a-data-race; write-table-driven-tests; refactor-to-accept-context; review-a-diff). Baseline with skill **disabled**, then write just enough to close the gaps. A/B in fresh sessions.
- **v1 — enforcement + workflow:** add `scripts/` gate scripts, `hooks.json` (fmt auto-fix + blocking gates), `go-reviewer` subagent (rubric preloaded), `/go-implement` + `/go-review`. Wrap in `plugin.json`. Now determinism is real.
- **v2 — harden + fine-tune:** tune the `description` with should-trigger / should-not-trigger prompts (must NOT fire on Rust/Python); add fuzz/observability templates; lite fast-path; measure defect-rate on the eval set and iterate until output is dependable.
- **v3 — templatize:** extract `new-lang-engineer`; generate `python-engineer` as the second member to prove the mold.

**How we "fine-tune for determinism":** the eval harness (superpowers' TDD-for-skills, automated by
the `skill-creator` plugin) is the flywheel — run representative Go tasks, capture the model's actual
bad-Go rationalizations ("I'll handle the error later", "skip ctx for now", "return `interface{}` to be
flexible"), write targeted rubric/anti-pattern content that closes each loophole, re-test under
pressure until compliance holds. Determinism is *earned* by closing measured gaps, not declared.

---

## 9. Broader roadmap / possibilities

- **Language-engineer family:** go → python → rust → typescript → java (from the one template).
- **Cross-cutting engineering skills** (same enforcement backbone): `api-designer`, `db-schema-designer`, `observability-engineer`, `security-reviewer`, `perf-engineer`, `distributed-systems`.
- **The meta layer:** `new-lang-engineer` (the mold) + a shared `review-subagent` shape + shared gate-hook wiring reused across all members.

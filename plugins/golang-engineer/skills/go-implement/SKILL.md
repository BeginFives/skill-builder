---
name: go-implement
description: Runs the gated Go implementation loop — plan, failing test (RED), implement, run the toolchain gate, review, finish green. Invoke explicitly when you want the full disciplined workflow for a Go change.
disable-model-invocation: true
argument-hint: "[what to build]"
---

# /go-implement

The gated loop for a Go change. Each step has an exit condition — do not advance past a red step.

## 1. Plan (short)
Restate the goal and the acceptance criteria in 3–6 lines. Name up front:
- which package(s) change,
- any goroutine you'll spawn — and its **owner + stop condition**,
- anything crossing a trust boundary (untrusted input, user-supplied URL, SQL).

Load `golang-engineer` and read the reference matching the work (`concurrency.md`, `data-stores.md`,
`security.md`, `resilience.md`, `logging-observability.md`) **before** writing that category of code.
Adapt its canonical template rather than reinventing one.

**Scope discipline:** implement what was asked. No unrequested constructors, options, interfaces,
config, or refactors — a bigger diff is not a better diff.

## 2. RED
Write the failing test first and **run it**. Paste the failing output. If it passes immediately, the
test doesn't test the new behavior — fix the test, not the claim.

## 3. Implement
Minimal code to go green. Author to the constitution by default: `ctx` first and propagated,
errors-as-values wrapped with `%w`, handle-once, `slog` with correlation IDs, explicit timeouts.

## 4. Gate
Run, from the module root:

```bash
gofmt -l . && go vet ./... && go test -race ./...
```

Plus `golangci-lint run` and `govulncheck ./...` when installed. The PostToolUse hook already runs
build/vet/lint on each edited package and **blocks** on failure — treat any block as the next thing
to fix, never as noise to route around.

## 5. Review
Run `/go-review` (or delegate to the `go-reviewer` subagent) on the diff. Fix blockers; re-gate.

## 6. Finish
Emit the **Definition of Done** block from
`../golang-engineer/references/review-rubric.md`, each line marked PASS/FAIL with a ≤10-word
reason on any FAIL. The Stop hook runs the full suite and will not let the turn finish red.

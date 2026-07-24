---
name: python-implement
description: Runs the gated Python implementation loop — plan, failing test (RED), implement, run the toolchain gate, review, finish green. Invoke explicitly when you want the full disciplined workflow for a Python change.
disable-model-invocation: true
argument-hint: "[what to build]"
---

# /python-implement

The gated loop for a Python change. Each step has an exit condition — do not advance past a red step.

## 1. Plan (short)
Restate the goal and acceptance criteria in 3–6 lines. Name up front: which module(s) change, any
concurrency you'll start (and its **owner + stop condition**), and anything crossing a trust boundary.

Load `python-engineer` and read the reference matching the work **before** writing that category of
code. Adapt its canonical template rather than reinventing one.

**Scope discipline:** implement what was asked. No unrequested abstractions, constructors, config, or
refactors — a bigger diff is not a better diff.

## 2. RED
Write the failing test first and **run it**. Paste the failing output. If it passes immediately, the
test doesn't test the new behavior — fix the test, not the claim.

## 3. Implement
Minimal code to go green, authored to the constitution: cancellation threaded, errors handled once,
structured logs with correlation IDs, explicit timeouts.

## 4. Gate
Run, from the project root:

```bash
uvx ruff check
uv run pytest -q
```

Plus type-check and vulnerability scan when configured. The PostToolUse hook already runs the gate on
each edited file and **blocks** on failure — treat any block as the next thing to fix, never as noise.

## 5. Review
Run `/python-review` (or delegate to the `python-reviewer` subagent) on the diff. Fix blockers; re-gate.

## 6. Finish
Emit the **Definition of Done** block from `../python-engineer/references/review-rubric.md`, each
line PASS/FAIL with a ≤10-word reason on any FAIL. The Stop hook runs the full suite and will not let
the turn finish red.

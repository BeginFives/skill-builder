# golang-engineer evals

Evaluation-driven development for the skill. The loop (per Anthropic's skill-authoring guidance and
superpowers' "TDD-for-skills"):

1. **Baseline** — run each task in `evals.json` in a **fresh session with the skill DISABLED**. Record
   what the model does wrong. `common_failures_without_skill` are the failures we've already observed.
2. **Enable** — run the same task in a fresh session with the skill enabled.
3. **Grade** — score the output against `expected_behavior` (each bullet is a checkable assertion).
   The skill earns a line of content only where *enabled* beats *baseline*.
4. **Close the gap** — for every failure the enabled run still shows, add the *minimal* SKILL.md /
   reference content that fixes it, then re-run. Don't add content that doesn't move an eval.

Grade **triggering** and **output quality** separately:
- **Output quality:** the `output_evals` above.
- **Triggering:** `trigger_evals` — the skill must fire on `should_trigger` and stay silent on
  `should_not_trigger` (esp. Rust/Python/TS and generic questions). Tune the `description` until both hold.

## Fixtures
- `cases/race/counter.go` — data race to fix
- `cases/fetch/user.go` — function to make context-aware
- `cases/money/parse.go` — parser to test (real implementation)
- `cases/review/server.go` — violation-rich change to review

Fixtures are standalone (no `go.mod`) and some contain deliberately bad code — never build them as a
module. Automating this loop with the `skill-creator` plugin (`evals.json` → per-eval subagents →
blind A/B grading) is the v2 fine-tuning step.

---
name: python-review
description: Reviews the current Python diff against the python-engineer production rubric in an isolated read-only subagent, returning findings ranked by severity. Invoke explicitly to review Python changes.
disable-model-invocation: true
argument-hint: "[optional: files, or a git ref like main...HEAD]"
---

# /python-review

Review the current Python change against the production rubric.

**Delegate to the `python-reviewer` subagent** (via the Agent tool) rather than reviewing inline. Two
reasons: it gets a clean context so a long implementation session can't bias the review, and its
searching/reading output stays out of this conversation.

Pass it:
1. **The scope** — `$ARGUMENTS` if given; otherwise the working diff (`git diff`, `git diff --staged`,
   or `git diff main...HEAD`). If it isn't a git repo, name the changed files.
2. **The rubric's resolved path** — pass the absolute path of
   `../python-engineer/references/review-rubric.md` (resolved from this skill's directory) into the
   subagent prompt. The subagent has no base directory of its own, so a bare relative path won't resolve.
3. **The gate output** you already have, so it verifies rather than re-runs everything.

The subagent is read-only — it reports, it does not fix.

## After it returns

- Fix **blockers** before finishing, then re-run the gate.
- Triage major/minor findings — fix, or state explicitly why you're deferring.
- Ignore any finding that just asks for unrequested abstraction; a minimal correct diff is not a defect.

If the diff is trivial (a comment, a constant, a one-line fix with no risk signals), say so and skip
the heavy review — the gate already covers it.

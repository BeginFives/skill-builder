---
name: python-engineer
description: >-
  Write, review, refactor, and debug idiomatic, production-grade Python to a MAANG
  principal-engineer bar. Covers asyncio (task ownership, TaskGroup, cancellation, timeouts, never
  blocking the loop), threads vs processes and the GIL, exception discipline and chaining, type
  hints with mypy --strict, contextvars correlation logging, OpenTelemetry observability,
  SQLAlchemy/DB-API pitfalls, security (pickle, yaml, shell=True, SSRF), dataclasses and Protocols,
  pytest and hypothesis, and the toolchain (ruff, mypy, pytest, pip-audit). Use whenever the user
  writes, reviews, debugs, tests, or optimizes Python code, edits .py files, works with
  pyproject.toml or requirements.txt, or asks about asyncio, await, type hints, pytest, packaging,
  or Django/FastAPI/Flask — even if they don't say the word "Python".
---

# python-engineer

Produce Python a principal engineer would approve on the first pass. `ruff` and `mypy` gate the
*mechanical* layer; **you** gate the *judgment* layer — task ownership, cancellation, event-loop
discipline, transaction boundaries — which is where the outages actually live.

**The bar is defined, not implied.** `references/review-rubric.md` is the definition of done: a
PASS/FAIL checklist whose hard-blocker sections a change may not fail.

Baseline is **Python 3.11+** (`TaskGroup`, `asyncio.timeout`, `ExceptionGroup`/`except*`). Where a
rule depends on a newer version, it says so.

## How to work

1. **Read the matching reference before writing that category of code** — and adapt its canonical
   template instead of reinventing one.
2. **Author to the constitution by default.** You may deviate on non-blocker sections with a one-line
   comment naming the tradeoff — but §1–4 and §10 are hard blockers with **no comment override**.
3. **RED before GREEN** — when fixing a bug or adding behavior, write and run the failing test first,
   show its output, then show it pass.
4. **Self-run the rubric**, concentrating on the `[SELF]` items no tool can catch (listed below).
5. **Emit the Definition of Done** — end every code response with the copyable block from
   `references/review-rubric.md`, each line PASS/FAIL (+ a ≤10-word reason on any FAIL).

## Scope discipline — match the change to the request

The most common failure of a "principal-grade" skill is **gold-plating**: turning a one-line change
into a refactor. A bigger diff is not a better diff.

- Do exactly what was asked, and no more. Adding a field is a field — **not** a new base class,
  factory, plugin registry, or settings module.
- Do not introduce new abstractions, async, dependencies, validation, or observability the task
  didn't ask for, however "principled" they'd look in greenfield code.
- The constitution below describes how to build *new* components well; it is **not** a mandate to
  retrofit that machinery onto a trivial edit.
- If the surrounding code genuinely needs improvement, say so in one line — don't silently do it.

## Non-negotiables (the constitution — each with its reason)

- **Every task has an owner that awaits it — and a strong reference.** Prefer `asyncio.TaskGroup`.
  *Why: the event loop holds only a **weak** reference to a bare `create_task` result, so a task
  nobody stored can be garbage-collected mid-flight and silently vanish.*
- **`CancelledError` is a signal, not an error.** Clean up in `finally` and re-raise; never swallow it.
  *Why: it inherits from `BaseException` precisely so blanket handlers don't eat it — swallowing it
  breaks cancellation for everything above you.*
- **Never `except:` bare, and never `except Exception` as a blanket.** *Why: a bare except also
  catches `KeyboardInterrupt` and `SystemExit`, so your process stops responding to Ctrl-C and to
  orderly shutdown.*
- **Handle an error exactly once, and chain the cause with `raise X from e`.** *Why: log-and-reraise
  double-reports one failure across layers, and losing `__cause__` deletes the traceback that would
  have explained it.*
- **Never block the event loop.** No sync I/O, CPU work, or `time.sleep` inside `async def` — use
  `asyncio.to_thread` / `run_in_executor`. *Why: one blocking call stalls **every** other task on
  that loop, so an unrelated request times out.*
- **Every `await` that can block carries a deadline** (`asyncio.timeout`). *Why: without one, a hung
  dependency becomes an unbounded resource leak.*
- **Type hints at every public boundary, checked with `mypy --strict`.** *Why: `Any` at a boundary
  silently disables the checker for everything downstream of it.*
- **No mutable default arguments, no late-binding closures in loops.** *Why: `def f(x=[])` creates
  the list **once at definition**, shared across every call — the classic Python bug.*
- **Logs use lazy `%s` formatting and correlation IDs from `contextvars`.** Secrets never logged.
  *Why: an f-string is evaluated even when the level is disabled, and it destroys the structure that
  makes logs queryable.*
- **Observability is not optional:** a span per unit of work (`record_exception` **and**
  `set_status(ERROR)`) and RED metrics with bounded label cardinality. *Why: you cannot operate what
  you cannot see; unbounded labels take down the metrics backend.*
- **Treat everything crossing a trust boundary as hostile:** never unpickle untrusted bytes, use
  `yaml.safe_load`, never `shell=True` with interpolation, parameterize SQL. *Why: in Python these
  aren't hardening niceties — each one is remote code execution.*
- **The mechanical layer is enforced by tooling.** `uvx ruff format` · `uvx ruff check` · `uvx mypy` ·
  `uv run pytest -q` are the hook gate (`uvx pip-audit` runs in CI). *Why: they catch classes of bug no
  amount of reading will — but they are blind to the judgment layer below.*

## The tools are blind to these — you are the only gate

Be accurate about the split. `ruff` genuinely catches bare `except` (E722), mutable default
arguments (B006) and unused imports; `mypy` catches type errors; `bandit`/`pip-audit` catch some
security issues. **None of them catch these `[SELF]` items:**

- task **ownership** and whether the task reference is actually held
- **cancellation** correctness (`CancelledError` re-raised, deadlines on every blocking await)
- **blocking the event loop** from inside `async def`
- **contextvars** seeded at the edge — and that they do *not* cross into `run_in_executor` threads
- span `record_exception` + `set_status`, and metric label cardinality
- session/transaction boundaries, and the N+1 query
- whether validation actually happens *before* use, and secret redaction

"ruff will catch it" is not available here. You are the gate.

## Don't rationalize past the rules

Pre-empt the specific excuse — these are the ones Python actually invites:

| The excuse | The reality | Do this instead |
|---|---|---|
| "I'll `create_task` and move on" | the loop keeps only a weak ref — it can be GC'd mid-flight | hold the reference, or use `TaskGroup` |
| "`CancelledError` is just an error" | it's `BaseException`; swallowing it breaks cancellation | clean up in `finally`, then re-raise |
| "a bare `except:` is fine here" | it eats `KeyboardInterrupt` and `SystemExit` too | `except Exception`, or narrower |
| "one blocking call in async won't hurt" | it stalls every other task on the loop | `asyncio.to_thread` / `run_in_executor` |
| "an f-string in the log reads better" | evaluated even when the level is off; unstructurable | `log.info("x=%s", x)` |
| "`def f(items=[])` is convenient" | the default is created once and shared across calls | `items=None` sentinel |
| "`yaml.load` is fine, it's our own file" | true until it isn't — then it's RCE | `yaml.safe_load` |
| "pickle is the easy cache format" | unpickling untrusted bytes **is** RCE | JSON, or a signed/validated format |
| "`shell=True` is simpler" | shell injection on any interpolated value | an args list, no shell |
| "threads will speed up this CPU work" | the GIL means they won't | `ProcessPoolExecutor`, or a native lib |
| "mypy is noisy — I'll annotate `Any`" | `Any` at a boundary disables checking downstream | type it properly, or `object` + narrowing |
| "small refactor while I'm here" | unsolicited complexity is a regression | minimal diff; no new abstractions unless asked |

## Concurrency — which model? (full matrix in `references/concurrency.md`)

**The bound picks the model — the GIL is why.** Reach for the first that fits:

- **I/O-bound, async-native libraries** → `asyncio` with `TaskGroup` (not bare `create_task`).
- **I/O-bound but the library blocks** (`requests`, sync DB driver) → `await asyncio.to_thread(...)`, never call it inline in a coroutine.
- **I/O-bound, small, no async stack** → a `ThreadPoolExecutor`.
- **CPU-bound pure Python** → `ProcessPoolExecutor` — threads give **no** speedup (the GIL serializes bytecode).
- **CPU-bound, heavy numeric** → a native lib (`numpy`/`polars`) or a release-the-GIL extension.
- **First failure cancels the rest** → `asyncio.TaskGroup` (raises an `ExceptionGroup`).
- **Bound in-flight work against a scarce resource** → `asyncio.Semaphore`, not unbounded fan-out.

## Reference index (progressive disclosure — load on demand)

Read the file whose topic matches the change; don't load all of them.

| Read this when… | File |
|---|---|
| The definition of done / reviewing any diff | `references/review-rubric.md` |
| asyncio, tasks, cancellation, threads vs processes, a leak | `references/concurrency.md` |
| Designing exceptions, chaining, failure paths | `references/errors.md` |
| Logging, contextvars correlation, traces, metrics | `references/logging-observability.md` |
| Writing tests, pytest, fixtures, mocks, hypothesis | `references/testing.md` |
| Structuring a package, typing, dataclasses, Protocols, DI | `references/project-layout.md` |
| Touching a database — DB-API, SQLAlchemy, pools, transactions | `references/data-stores.md` |
| Handling untrusted input, pickle, YAML, subprocess, user URLs | `references/security.md` |
| A path is hot and you need to prove a speedup | `references/performance.md` |

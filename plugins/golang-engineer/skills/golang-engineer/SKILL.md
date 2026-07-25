---
name: golang-engineer
description: >-
  Write, review, refactor, and debug idiomatic, production-grade Go to a MAANG principal-engineer
  bar. Covers concurrency (goroutines, channels, context, errgroup, race-freedom), error handling
  and wrapping, structured + correlation-ID (MDC) logging, OpenTelemetry observability, HTTP/gRPC
  servers, table-driven tests, fuzzing, benchmarks, project layout, and the go toolchain (vet,
  staticcheck, golangci-lint, govulncheck, test -race). Use whenever the user writes, reviews,
  debugs, tests, or optimizes Go / Golang code, edits .go files, works with go.mod/go.sum, or asks
  about goroutines, channels, interfaces, context, error handling, leaks, or Go performance — even
  if they don't say the word "Go".
---

# golang-engineer

Produce Go that a Google/Uber principal engineer would approve on the first pass: correct
concurrency, no goroutine or memory leaks, no panics on expected paths, disciplined errors,
structured + correlated logging, first-class observability. The toolchain gates the *mechanical*
layer; **you** gate the *judgment* layer — ownership, context, error discipline, observability —
which is where the leaks and outages actually live.

**The bar is defined, not implied.** `references/review-rubric.md` is the definition of done: a
15-section PASS/FAIL checklist. Sections 1–4 (concurrency, leaks, errors, context) and 10 (tooling)
are **hard blockers** — code failing any of them is not shippable regardless of features.

## How to work

1. **Read the matching reference before writing that category of code** — open `concurrency.md`
   before hand-rolling a worker pool, `logging-observability.md` before wiring logs/traces, etc., and
   adapt its canonical template instead of reinventing one.
2. **Author to the constitution by default** — context-first signatures, errors-as-values, goroutine
   ownership stated in a comment, `slog` for logs, explicit timeouts. You may deviate on the
   *non-blocker* sections (§5–9, §11–15) with a one-line comment naming the tradeoff — but **§1–4 and
   §10 are hard blockers with no comment override.** A commented-out leak is still a leak.
3. **RED before GREEN** — when fixing a bug or adding behavior, write and run the failing test first,
   show its failing output, then show it pass after the change. Emitted test evidence is the strongest
   proof available before the enforcement hooks exist.
4. **Self-run the rubric**, concentrating on the `[SELF]` items no tool can catch (listed below). Open
   `references/review-rubric.md` and fix each FAIL.
5. **Emit the Definition of Done** — end every code response with the copyable block from
   `references/review-rubric.md`, each line marked PASS/FAIL (+ a ≤10-word reason on any FAIL). This
   emitted checklist — not a silent "I reviewed it" — is what makes the output dependable.

## Scope discipline — match the change to the request

The most common failure of a "principal-grade" skill is **gold-plating**: turning a one-line change
into a refactor. A bigger diff is not a better diff — a principal is trusted precisely because their
change touches only what the task needs.

- Do exactly what was asked, and no more. Adding a struct field is a field — **not** a new
  constructor, functional options, an interface, or a config system.
- Do not introduce new abstractions, goroutines, dependencies, validation, timeouts, or observability
  the task didn't ask for, however "principal" they'd look in greenfield code.
- The constitution and references below describe how to build *new* components well; they are **not** a
  mandate to retrofit that machinery onto a trivial edit. Constructor-based DI is for building a
  component, not for wiring one new field into an existing struct.
- If the surrounding code genuinely needs improvement, say so in one line — don't silently do it.

## Non-negotiables (the constitution — each with its reason)

These are the high-signal conventions Claude would otherwise get subtly wrong. Explanations are the
point — follow the *reason*, not a rule.

- **Errors are values; panic is not control flow.** Return errors; wrap with `fmt.Errorf("doing X: %w", err)`
  to preserve the chain; match with `errors.Is`/`errors.As`. `panic`/`recover` only at a process edge
  (HTTP/gRPC recovery middleware, top of a worker goroutine, `main`). *Why: a panic in a library takes
  down the whole process; a swallowed or unwrapped error is undebuggable in production.*
- **Handle each error exactly once** — return it *or* log it on a given path, never both. *Why: log-and-return
  double-reports and scatters the same failure across layers.*
- **`context.Context` is the first parameter, always propagated, never stored in a struct, never nil.**
  Put a deadline on every outbound I/O call and `defer cancel()` immediately. *Why: context is how a
  request's cancellation and timeout reach every goroutine and socket it spawned — dropping it leaks them.*
- **Every goroutine has a named owner and a stop condition.** No fire-and-forget, none in `init()`.
  Whoever starts it can wait for it (`WaitGroup`/`errgroup`/done-channel) and it exits on `<-ctx.Done()`.
  *Why: fire-and-forget goroutines are the #1 leak and the hardest bug to find.*
- **Fan-out uses `errgroup.WithContext`; bound it with `g.SetLimit(n)`.** *Why: the first error cancels
  siblings and is returned by `Wait`; unbounded `for … { go f() }` over real input exhausts memory.*
- **Channels: the sender closes, capacity is 0 or 1 unless justified, every blocking op has a
  `select { case <-ctx.Done(): … }` escape.** *Why: closing from the receiver panics; unexplained
  buffers hide backpressure bugs; an unescapable blocking op deadlocks on cancel.*
- **Logs go through `log/slog` with typed attributes; correlation/trace IDs are injected by a
  context-aware handler (MDC), not threaded by hand; secrets never logged.** *Why: unstructured logs
  aren't queryable, and per-call ID plumbing drifts the moment someone forgets it.*
- **Observability is not optional:** a span per unit of work (`RecordError` **and** `SetStatus` on
  failure) and RED metrics (rate/errors/duration). *Why: you cannot operate what you cannot see.*
- **Servers set explicit timeouts and shut down gracefully.** *Why: a zero-value `http.Server` has
  infinite timeouts — a trivial Slowloris DoS.*
- **SQL: bound the pool, put `ctx` on every query, parameterize everything, roll back every tx.**
  *Why: an unbounded pool exhausts the database, string-built SQL is injection, and a missed tx
  rollback corrupts data on the error path.*
- **Treat everything from a trust boundary as hostile — validate it, bound its size, parameterize it,
  block SSRF.** *Why: `govulncheck` scans your dependencies, not the injection/SSRF/overflow bugs you write.*
- **Retry only idempotent operations, with capped exponential backoff + jitter that honors `ctx`.**
  *Why: fixed-interval retries on non-idempotent calls cause retry storms and double-execution.*
- **The mechanical layer is enforced by tooling.** `gofmt`, `go vet`, `staticcheck`, `golangci-lint`
  (with the named linter set in `references/.golangci.yml`), `govulncheck`, and `go test -race` are the
  gate for formatting, races, and lint-catchable bugs. *Why: `-race` and the linters catch classes of
  bug no amount of reading will — but they are blind to the judgment layer below, which is on you.*

## The tools are blind to these — you are the only gate

`gofmt`, `vet`, `staticcheck`, `golangci-lint`, and the enforcement hooks catch the *mechanical* layer.
They cannot see the judgment layer — and the judgment layer is the entire reason this skill exists. No
linter will ever catch a FAIL in these `[SELF]` items:

- goroutine **ownership** + a stop condition (§1)
- **context** propagation and deadlines on I/O (§4)
- **handle-once** discipline and error-wrapping intent (§3)
- **MDC** correlation-ID injection (§6)
- span `RecordError` **and** `SetStatus`, and RED metrics (§7)
- **accept-interfaces / return-structs**, interface-at-the-consumer (§11)
- secret redaction in logs and errors (§5, §9)

"The linter will tell me" is not available here. You are the gate.

## Don't rationalize past the rules

Under load you will be tempted to talk yourself out of the constitution. Pre-empt the specific excuse:

| The excuse | The reality | Do this instead |
|---|---|---|
| "I'll handle the error later" | unhandled errors are the top production-debugging cost | wrap+return now, or `_ =` with a comment saying why it's safe |
| "context is overkill for this helper" | a dropped `ctx` is an uncancellable op that leaks | `ctx` is the first param the moment the func does any I/O |
| "fire-and-forget, it's short-lived" | it is the #1 goroutine leak | name an owner + stop condition, or don't spawn it |
| "a mutex is simpler than a channel here" | often true — and the inverse is too | guard a *field* with a mutex; *hand off* with a channel |
| "return `any` to stay flexible" | that pushes type errors to runtime | return the concrete type; accept a small interface |
| "I'll add the span/metric later" | unobserved code is unoperatable | `RecordError`+`SetStatus` on the error path now |
| "log it *and* return it, to be safe" | double-logs the same failure across layers | handle once: return (wrap) **or** log, never both |
| "panic is fine, this case is impossible" | a library panic crashes the whole process | return an error; `panic` only at a process edge |
| "buffer the channel to be safe" | an unexplained buffer hides a backpressure/leak bug | capacity 0 or 1 unless a comment names the bound |
| "close the channel from the receiver" | closing from the receiver panics | the sender closes |
| "the linter will catch it" | linters are blind to the `[SELF]` items above | check the `[SELF]` items yourself, every time |
| "small refactor while I'm here" | unsolicited complexity is a regression | minimal diff; no new abstractions/goroutines/config unless asked |

## Two decisions worth a flowchart

Use flowcharts only where branching is real (linear steps don't need them).

**Which concurrency primitive?**
- Independent tasks, want the first error to cancel the rest → `errgroup.WithContext` (+ `SetLimit` to bound).
- Bounded stream of work items over time → worker pool: producer closes the jobs channel, N workers `select` on jobs and `ctx.Done()`.
- Protect shared mutable state → `sync.Mutex` (or `sync.RWMutex` if reads dominate); one-time init → `sync.Once`; a counter → `sync/atomic`.
- One-shot signal/handoff → channel. *If you're reaching for a channel to guard a field, use a mutex instead.*

**Should this be an interface?**
- Define it **at the consumer**, only when there's a real second implementation or a test fake needs a seam. **Accept interfaces, return concrete structs.** Keep it small (1–3 methods).
- Don't return `interface{}`/`any` "to be flexible," and don't define a producer-side interface with one implementation — that's premature abstraction.

## Reference index (progressive disclosure — load on demand)

Read the file whose topic matches the change; don't load all of them.

| Read this when… | File |
|---|---|
| The definition of done / reviewing any diff | `references/review-rubric.md` |
| Writing goroutines, channels, context, worker pools, or fixing a race/leak | `references/concurrency.md` |
| Designing error types, wrapping, sentinels vs typed, ret/log decisions | `references/errors.md` |
| Adding logging, correlation IDs (MDC), traces, or metrics | `references/logging-observability.md` |
| Writing tests, table-driven suites, fuzzing, golden files, or integration tests | `references/testing.md` |
| Structuring a package/module, DI, interfaces, HTTP/gRPC servers + graceful shutdown, config & secrets | `references/project-layout.md` |
| Touching a SQL database — pool config, transactions, queries, nullable columns | `references/data-stores.md` |
| Handling untrusted input, fetching user-supplied URLs, decoding bodies, size math | `references/security.md` |
| Calling a dependency that can fail/throttle — retries, backoff, rate limits, breakers | `references/resilience.md` |
| A path is hot and you need to reduce allocations / prove a speedup | `references/performance.md` |

Each reference carries canonical, copy-adaptable templates (worker pool, MDC `slog.Handler`,
graceful-shutdown server, OTel span helper, table-driven+fuzz test, functional-options constructor).
Adapt them; don't reinvent them.

# Go Production Rubric — the checkable "principal engineer" standard

> This is the crown-jewel content for the `golang-engineer` skill. Every item is a concrete
> PASS/FAIL gate. A change ships only if it passes every **applicable** item. Sections 1–4
> (concurrency, leaks, errors, context) and 10 (tooling gates) are **hard blockers** — failing
> any is not MAANG-grade regardless of features. Sections 5–9, 11–12 elevate "correct" to
> "production-grade and operable."
>
> Sourced from: Effective Go, Google Go Style Guide, Uber Go Style Guide, go.dev/blog
> (context, pipelines, go1.13-errors, slog), pkg.go.dev (errgroup, net/http, log/slog),
> OpenTelemetry-Go docs, staticcheck, golangci-lint, govulncheck.

## Definition of Done — emit this block, filled in, with every code response

Copy this and mark each line **PASS**/**FAIL** (+ a ≤10-word reason on any FAIL). This emitted evidence
— not a silent "I reviewed it" — is what makes output dependable. §1–4 and §10 are hard blockers with
**no comment override** (a commented-out leak is still a leak).

```
[SELF] goroutines — every one has an owner + stop condition (or none spawned)         §1
[SELF] context    — first param, propagated, deadline on I/O, defer cancel            §4
[SELF] errors     — returned-or-logged once, %w-wrapped, none ignored, no edge panic  §3
[SELF] leaks      — timers/tickers Stop()'d, resources Closed, no unbounded map/goroutine  §2
[SELF] log/obs    — slog + correlation IDs, no secrets; span RecordError+SetStatus on error §5–7
[TOOL] gofmt -l . (empty) · go vet ./... · staticcheck ./... · golangci-lint run       §10
[TOOL] go test -race ./... · govulncheck ./... · go mod tidy (no diff)                 §10
```

**Legend used throughout this rubric:** `[TOOL]` = a named check catches it (the enforcement hooks run
these — you get them for free); `[SELF]` = **no tool can see it, you are the only gate.** Concentrate
your review effort on the `[SELF]` items — the `[TOOL]` items are already covered by the gate.

## 1. Concurrency & goroutine lifecycle
- [ ] **OWNERSHIP** — every `go` / `g.Go(...)` has an identifiable owner that can wait for it (WaitGroup / errgroup / done-channel). No fire-and-forget.
- [ ] **TERMINATION** — every goroutine has a documented stop condition (`<-ctx.Done()` arm, closed channel, or bounded loop). No goroutine can block forever.
- [ ] **NO init() GOROUTINES** — background work is owned by a struct exposing Start/Stop/Close; nothing spawned in `init()` or package-var initializers.
- [ ] **CONTEXT FIRST** — `ctx context.Context` is the first param on every request-path function; never stored in a struct; never nil (use `context.TODO()` as placeholder only).
- [ ] **CANCEL DEFERRED** — every `WithCancel/WithTimeout/WithDeadline` is immediately followed by `defer cancel()` (govet `lostcancel`).
- [ ] **DEADLINES** — outbound RPC/HTTP/DB calls carry a deadline via ctx; no unbounded blocking on a request path.
- [ ] **ERRGROUP** — fan-out uses `errgroup.WithContext` (first error cancels siblings, `Wait` returns it); bounded parallelism uses `g.SetLimit(n)`, not a hand-rolled semaphore.
- [ ] **WORKER POOLS** — pool size is bounded/configurable; channels have justified capacities; pool drains and exits on ctx cancel.
- [ ] **CHANNELS** — buffered capacity is 0 or 1 unless a comment justifies more (Uber); direction (`chan<-`/`<-chan`) specified in signatures; blocking send/recv guarded by `select { case <-ctx.Done(): ... }`.
- [ ] **SYNC PRIMITIVES** — `sync.Mutex/WaitGroup` used by zero value, never copied (govet `copylocks`); `wg.Add` before `go`, never inside the goroutine; prefer `sync.Once` for once-init.
- [ ] **RACE-FREE** — no shared mutable state without mutex/channel/atomic; clean under `go test -race`; maps never concurrently written.
- [ ] **LOOP CAPTURE** — on Go <1.22, loop vars rebound before capture in goroutines/closures (fixed 1.22+, but be explicit).
- [ ] **GOROUTINE RECOVER** — every spawned goroutine running non-trivial work has its own `recover`. An unrecovered panic in *any* goroutine crashes the whole process, and server middleware does NOT cover goroutines a handler spawns.

## 2. Leaks (goroutine & memory)
- [ ] No goroutine blocked forever on a channel whose counterpart returned early (the classic write-after-consumer-gone leak).
- [ ] `time.NewTicker/NewTimer` are `Stop()`'d (`defer t.Stop()`); `time.After` not used inside a hot select-loop (allocates each iteration).
- [ ] No `defer` inside an unbounded/hot loop (defers stack to function return); close explicitly or extract a func.
- [ ] Every acquired resource (file, conn, rows, `resp.Body`, lock) released on all paths; `rows.Err()` checked.
- [ ] Caches/maps that grow with input have a bound (TTL / LRU / size cap). No unbounded global map.
- [ ] Small windows sliced out of large backing arrays are copied, not pinned.

## 3. Errors as values — no panics
- [ ] `panic` only for programmer bugs / impossible states; `recover()` only at a process edge (HTTP/gRPC recovery middleware, top of a worker goroutine, `main`) and it re-logs/handles the value.
- [ ] Errors returned as values; none ignored (`_ =` / bare call on an error-returning func) without a justifying comment — includes `resp.Body.Close`, `Unmarshal`, db ops.
- [ ] Wrapping uses `fmt.Errorf("context: %w", err)` (`%w` at the end) when the caller may unwrap; `%v` only to deliberately hide internals.
- [ ] Sentinels (`var ErrX = errors.New(...)`) matched with `errors.Is`; typed errors with `errors.As`. No `err.Error() == "..."` string matching after wrapping.
- [ ] **Handle once** — an error is returned OR logged on a given path, never both.
- [ ] Error strings lowercase, no trailing punctuation, no "failed to" noise.
- [ ] `os.Exit` / `log.Fatal` only in `main()`; libraries return errors.
- [ ] No **typed nil** returned as `error` (a nil `*T` in an `error` interface is non-nil, so `err != nil` is wrongly true); return `nil` literally. Aggregate independent failures with `errors.Join`.

## 4. Context: propagation, cancellation, deadlines
- [ ] ctx threaded through every downstream call (I/O, DB, RPC) and honored; never dropped mid-chain.
- [ ] `context.Value` carries only request-scoped metadata (IDs, auth) via **unexported typed keys** — never optional business params or raw string keys.
- [ ] No `context.TODO()` left in shipped non-test code.

## 5. Structured logging (log/slog)
- [ ] Uses `log/slog` (JSONHandler in prod), not `fmt.Printf`/`log.Printf`.
- [ ] Typed attributes (`slog.String/Int/...`) or `LogAttrs` on hot paths; `msg` is a stable constant; no field interpolation into the message.
- [ ] `*Context` methods (`InfoContext`/`LogAttrs(ctx,...)`) used so handlers can read ctx; single `slog.Default` set at startup; level driven by a `slog.LevelVar`.
- [ ] Levels correct (Debug/Info/Warn/Error); Error reserved for actionable failures.
- [ ] **No secrets/PII** — sensitive types implement `slog.LogValuer` to redact; expensive values deferred via `LogValuer`/guarded by `Enabled`.

## 6. Contextual logging / MDC (correlation, request, trace IDs)
- [ ] Request/correlation/trace IDs seeded into ctx by edge middleware (generate if absent, e.g. from `traceparent` / `X-Request-ID`).
- [ ] A custom `slog.Handler` reads those IDs off ctx in `Handle()` and attaches them to **every** record — not threaded manually into each call.
- [ ] IDs propagate outbound (HTTP headers / gRPC metadata) so downstream logs correlate; the same trace ID appears in logs **and** spans.

## 7. Observability (OpenTelemetry, RED/USE)
- [ ] Tracer/Meter providers initialized at startup with a `service.name` resource + batch/periodic exporter; both `Shutdown()` on exit.
- [ ] Units of work: `ctx, span := tracer.Start(ctx, name)` + `defer span.End()`; the **returned** ctx is propagated.
- [ ] On error: `span.RecordError(err)` **AND** `span.SetStatus(codes.Error, msg)` (RecordError alone doesn't set status).
- [ ] W3C tracecontext propagator registered; extracted inbound, injected outbound.
- [ ] Metrics cover **RED** (Rate, Errors, Duration histogram) for services and **USE** for resources; attribute/label cardinality bounded (no user IDs/URLs as raw labels).
- [ ] Instruments created once at init (not per-request); correct type (Counter monotonic / UpDownCounter / Histogram / Gauge).

## 8. HTTP / gRPC servers
- [ ] `http.Server` sets `ReadHeaderTimeout`, `ReadTimeout`, `WriteTimeout`, `IdleTimeout`, `MaxHeaderBytes`. **FAIL on a zero-value server (infinite timeouts → Slowloris/DoS).**
- [ ] `http.Client`/transport sets a `Timeout` + pool limits; requests use `http.NewRequestWithContext`. No `http.Get`/`DefaultClient`/`DefaultServeMux` in prod paths.
- [ ] Graceful shutdown: SIGINT/SIGTERM → `srv.Shutdown(ctx-with-deadline)`; `ErrServerClosed` treated as clean; in-flight requests drain.
- [ ] Middleware chain: panic-recovery (outermost), request-ID/trace seeding, logging, metrics, timeout; handlers use `r.Context()`.
- [ ] Request bodies size-limited (`http.MaxBytesReader`); inputs validated before use.
- [ ] gRPC: keepalive + per-RPC deadlines from ctx + interceptors (recovery/logging/tracing/metrics); statuses use `codes.*`; `GracefulStop()`.

## 9. Configuration & secrets
- [ ] Config loaded from env/flags/file into a typed struct, **validated at startup (fail fast)** — no lazy mid-request failures, no silent defaults for required values.
- [ ] No secrets hardcoded/committed/logged; read from env/secret manager; scrubbed from logs, errors, and `String()`/`%v`.
- [ ] No mutable package-level global config; config injected. Timeouts/limits configurable, not magic numbers.

## 10. Tooling & CI gates — the mechanical gate (all `[TOOL]`; the enforcement hooks run these)
This is the layer tools own, not where the skill's value lives — see the `[SELF]` sections above for that.
- [ ] `gofmt -l .` prints nothing; imports ordered by `goimports`.
- [ ] `go vet ./...` clean.
- [ ] `staticcheck ./...` clean; `golangci-lint run` passes with **at least** the named linter set
      (`errcheck, govet, staticcheck, contextcheck, noctx, bodyclose, rowserrcheck, sqlclosecheck,
      copyloopvar, ineffassign, unconvert, gosec`) — ship the canonical `references/.golangci.yml`,
      don't defer to "the repo config".
- [ ] `go test -race ./...` passes.
- [ ] `govulncheck ./...` reports no reachable known vulnerabilities.
- [ ] `go mod tidy` leaves no diff; deps minimal/pinned; no leftover `replace` hacks.
- [ ] Build reproducible; `unused`/`deadcode` clean.

> Several items the sections above mark `[SELF]` become `[TOOL]` once the named linters are enabled:
> unchecked errors/`resp.Body.Close` → **errcheck/bodyclose**, missing `ctx` on outbound calls →
> **noctx/contextcheck**, unchecked `rows.Err`/unclosed rows → **rowserrcheck/sqlclosecheck**, leaked
> cancel → **govet lostcancel**, copied lock → **govet copylocks**, loop-var capture → **copyloopvar**.
> Enable them and you shrink the surface you must self-check.

## 11. Project layout & dependency injection
- [ ] Standard layout: `cmd/` binaries, `internal/` private, `pkg/` only for genuinely reusable exports. No `util`/`common`/`helpers` dumping grounds.
- [ ] Deps injected via constructors; **accept interfaces, return structs**; interfaces defined at the consumer, kept small.
- [ ] Compile-time interface checks (`var _ Iface = (*Impl)(nil)`) where implementations must satisfy an interface.
- [ ] No import cycles; package names lowercase nouns, no stutter (`foo.FooService`). Enums start at `iota+1` or have an explicit `Invalid` zero.

## 12. Performance (measure first)
- [ ] Optimization claims backed by pprof (CPU/heap/block/mutex) or benchmarks — not guessed.
- [ ] Hot-path `Benchmark` funcs use `b.ReportAllocs()`/`b.ResetTimer()`; allocations understood.
- [ ] Slices/maps preallocated with known capacity (`make(T, 0, n)`); no per-iteration allocs in hot loops (`time.After`, repeated `fmt.Sprintf`, `[]byte`↔`string` round-trips).
- [ ] Escape analysis (`go build -gcflags=-m`) checked for hot values that unexpectedly escape; `sync.Pool` for high-churn buffers where profiling justifies it.
- [ ] `strings.Builder`/`bytes.Buffer` for loop concatenation; `io.Copy`/streaming over full-buffering large payloads.

## 13. Data stores (database/sql)
- [ ] Pool configured at startup: `SetMaxOpenConns`/`SetMaxIdleConns`/`SetConnMaxLifetime`/`SetConnMaxIdleTime`. FAIL on an unconfigured (unbounded) pool.
- [ ] Every query uses a `…Context` method carrying the request ctx. `[TOOL: noctx/contextcheck]` FAIL on `db.Query`/`Exec`/`QueryRow` on a request path.
- [ ] All SQL parameterized (`$1`/`?`); no user input concatenated into SQL (injection).
- [ ] Transactions: `BeginTx(ctx)` → `defer tx.Rollback()` → work → `Commit`; statements use `tx.…Context`. FAIL if a tx can return without commit or rollback.
- [ ] `rows.Close()` deferred **and** `rows.Err()` checked after the loop. `[TOOL: sqlclosecheck/rowserrcheck]`
- [ ] Nullable columns scanned into `sql.Null[T]`/`NullXxx`, not plain types.
- [ ] Schema changes are versioned forward-only migrations, not ad-hoc `ALTER` at boot.

## 14. Security & input validation ([SELF]; enable `gosec` for `[TOOL]` coverage)
- [ ] Untrusted input validated AFTER decode, before use; bodies capped with `http.MaxBytesReader`; lengths/counts bounded before `make()`.
- [ ] User-supplied URLs fetched only after scheme+host allowlist and blocking loopback/private/link-local IPs (SSRF). FAIL on `http.Get` of a raw user URL.
- [ ] No shell-string interpolation into `exec`; args passed as a slice; paths cleaned/confined (no traversal).
- [ ] Narrowing integer conversions bounds-checked (`gosec` G115); no silent `int64`→`int32`/`uint` casts on untrusted sizes.
- [ ] `unsafe`/`cgo` avoided unless justified in a comment and isolated.
- [ ] JSON: `DisallowUnknownFields` on strict inputs; pointer/`omitempty` to distinguish absent vs zero; `json.Number` where precision matters.

## 15. Resilience
- [ ] Retries target only **idempotent** operations and only transient errors (timeouts/429/5xx), never 4xx/validation.
- [ ] Backoff is capped exponential with **full jitter**, honors ctx (selects on `ctx.Done()` while waiting) and `Retry-After`; attempt count bounded. FAIL on a fixed-sleep retry loop.
- [ ] Non-idempotent writes that must be retryable use an **idempotency key** deduped server-side.
- [ ] Outbound call rate bounded (`x/time/rate`); a breaker/degradation path exists for a down dependency where it matters.
- [ ] An overall deadline caps the whole retry chain (timeout budget) so retries can't exceed the caller's ctx.

---

### How the skill uses this rubric
1. **Author** code context-first, errors-as-values, goroutine-ownership-documented **by default**.
2. **Self-run** every applicable checkbox before returning; for any FAIL, fix or add a one-line justifying comment naming the tradeoff.
3. **Emit** the section-10 commands as the definition of done.
4. The **hooks** (not the model's memory) enforce section 10 deterministically; the **rubric** makes sections 1–9, 11–12 checkable by the model and the reviewer subagent.

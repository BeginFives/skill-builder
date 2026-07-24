# Concurrency, context & leak-freedom

Load this when writing goroutines, channels, worker pools, or context plumbing — or when fixing a
race or a leak. The governing rule: **every goroutine has an owner who can wait for it and a stop
condition it honors.** If you can't name both, don't spawn it.

## Contents
- [Choosing a concurrency tool](#choosing-a-concurrency-tool)
- [The ownership triad](#the-ownership-triad)
- [context.Context rules](#contextcontext-rules)
- [errgroup — the default for fan-out](#errgroup--the-default-for-fan-out)
- [Bounded worker pool over a jobs channel](#bounded-worker-pool-over-a-jobs-channel)
- [Channel discipline](#channel-discipline)
- [sync primitives](#sync-primitives)
- [The leak catalogue](#the-leak-catalogue)
- [Races](#races)

## Choosing a concurrency tool
Reach for the **first row that fits** — most "concurrency bugs" are the wrong tool for the job.

| The situation | Reach for | Not |
|---|---|---|
| Fan out independent tasks; the **first error should cancel the rest** | `errgroup.WithContext` + `g.SetLimit(n)` to bound | a raw `WaitGroup` (drops errors); one goroutine per unbounded input |
| A **bounded stream** of work items over time | a worker pool: producer closes the jobs channel, N workers `select` on jobs and `ctx.Done()` | unbounded `go` per item |
| Protect **shared mutable state** | `sync.Mutex` (or `RWMutex` if reads dominate); a counter → `sync/atomic`; one-time init → `sync.Once` | a channel to guard a field — that's mutual exclusion, use a mutex |
| **One-shot signal / handoff** between goroutines | a channel (unbuffered, or size 1 with a reason) | a mutex + flag polling |
| **Bound concurrency** against a scarce resource (a pool, a rate limit) | `g.SetLimit(n)` or a buffered-channel semaphore | capping nothing and hoping |
| **Cancellation / deadlines** across a call tree | `context.Context` — first param, `WithTimeout` on I/O, `defer cancel()` | a shared `bool`/`chan struct{}` you hand-roll |

When two rows both fit, prefer the higher one — it is the simpler tool. Each is expanded below.

## The ownership triad
For every `go` / `g.Go(...)`, a reviewer must be able to point to:
1. **Who starts it** (the owner).
2. **The exact condition that stops it** — a `<-ctx.Done()` arm, a closed channel, or a bounded loop.
3. **How the owner learns it finished** — `WaitGroup.Wait`, `errgroup.Wait`, or a done channel.

State ownership in a comment at the spawn site. No goroutines in `init()` or package-var initializers —
background work belongs to a struct exposing `Start`/`Stop`/`Close`.

## context.Context rules
- First parameter, named `ctx`. Never store it in a struct field. Never pass `nil` (use `context.TODO()`
  only as a temporary placeholder, never in shipped code).
- Propagate it into every downstream call (DB, RPC, HTTP). A dropped ctx = an uncancellable operation.
- Put a deadline on outbound I/O and defer the cancel **immediately**:
  ```go
  ctx, cancel := context.WithTimeout(ctx, 2*time.Second)
  defer cancel() // releases the timer even if the parent outlives this call
  req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil) // preferred over req.WithContext
  if err != nil {
      return err
  }
  resp, err := client.Do(req)
  ```
- `ctx.Value` carries only request-scoped metadata (trace/request IDs, auth), keyed by an **unexported
  typed key** — never optional business parameters, never raw string keys:
  ```go
  type ctxKey int
  const requestIDKey ctxKey = 0
  func RequestIDFromContext(ctx context.Context) (string, bool) {
      id, ok := ctx.Value(requestIDKey).(string)
      return id, ok
  }
  ```

- **Detach background work with `context.WithoutCancel`** (Go 1.21+): when work must outlive the
  request that started it (an async write, cleanup), derive a ctx that keeps the *values* but drops the
  cancellation — don't hand it `context.Background()`, which loses the trace/request IDs.
- **Know *why* it cancelled** with `context.WithCancelCause` + `context.Cause(ctx)` (deadline vs a
  specific failure); `context.AfterFunc` schedules a cleanup for when a ctx is done.

## errgroup — the default for fan-out
Prefer `golang.org/x/sync/errgroup` over hand-rolled `WaitGroup` + error channel + semaphore. The
derived context cancels on the first non-nil error; `Wait` returns that error; `SetLimit(n)` bounds
parallelism (`g.Go` blocks until a slot frees).

```go
func fetchAll(ctx context.Context, urls []string) ([]Result, error) {
    results := make([]Result, len(urls))
    g, ctx := errgroup.WithContext(ctx)
    g.SetLimit(8) // bounded parallelism — never one goroutine per unbounded input

    for i, url := range urls { // Go 1.22+: i, url are per-iteration; safe to capture
        g.Go(func() error {
            r, err := fetch(ctx, url) // ctx is the *derived* one: cancels when a sibling fails
            if err != nil {
                return fmt.Errorf("fetch %s: %w", url, err)
            }
            results[i] = r // distinct index per goroutine → no shared-write race
            return nil
        })
    }
    if err := g.Wait(); err != nil {
        return nil, err // first error; siblings already cancelled via ctx
    }
    return results, nil
}
```

Writing to `results[i]` at a unique index is race-free. Never `append` to a shared slice from multiple
goroutines — that races on the header.

## Bounded worker pool over a jobs channel
Use when work arrives as a stream and you want a fixed number of long-lived workers. The **producer
closes the jobs channel**; workers exit on close or on cancel.

```go
func process(ctx context.Context, jobs <-chan Job, workers int) error {
    g, ctx := errgroup.WithContext(ctx)
    for w := 0; w < workers; w++ {
        g.Go(func() error {
            for {
                select {
                case <-ctx.Done():
                    return ctx.Err() // cancellation escape — no worker blocks forever
                case job, ok := <-jobs:
                    if !ok {
                        return nil // jobs closed by producer → clean exit
                    }
                    if err := handle(ctx, job); err != nil {
                        return fmt.Errorf("job %s: %w", job.ID, err)
                    }
                }
            }
        })
    }
    return g.Wait()
}
```

**The producer must honor cancellation too.** When ctx is cancelled the workers return, so a producer
blocked on `jobs <- job` with no receiver leaks forever. Send with a `select`, never a bare send:
```go
for _, j := range items {
    select {
    case jobs <- j:
    case <-ctx.Done():
        return ctx.Err()
    }
}
close(jobs) // producer closes
```

## Channel discipline
- **The sender closes, never the receiver.** Closing from the receiver (or closing twice) panics.
- Buffer capacity is **0 or 1** unless a comment justifies a larger bound and names what limits it under load (Uber rule). An unexplained `make(chan T, 1000)` hides a backpressure decision.
- Specify direction in signatures: `func emit(out chan<- T)`, `func consume(in <-chan T)`.
- Every potentially-blocking send/receive in a long-lived goroutine has a `select { case <-ctx.Done(): return ctx.Err() … }` arm.
- Don't use a channel to guard a struct field — use a mutex. Channels are for handoff, not mutual exclusion.

## sync primitives
- `sync.Mutex`/`sync.WaitGroup` are used by **zero value** and never copied by value (govet `copylocks`
  catches copies). Embed a mutex as a named field (`mu sync.Mutex`), not anonymously.
- `wg.Add(1)` is called **before** `go`, never inside the goroutine (a race between `Add` and `Wait`).
- `sync.Once` for one-time init; `sync/atomic` (or `atomic.Int64` etc.) for simple counters/flags;
  `sync.RWMutex` only when reads genuinely dominate and profiling shows contention.

## The leak catalogue
Each of these is a concrete FAIL:
- **Blocked-forever goroutine:** it writes to a channel after the only reader returned early (e.g. the
  consumer errored out and stopped reading). Fix: buffer the channel to the known count, or add a
  `ctx.Done()` arm, or use errgroup so a sibling failure cancels it.
- **Ticker/Timer without Stop:** `t := time.NewTicker(d); defer t.Stop()`. `time.After` inside a
  `select`-loop allocates a new timer each iteration that lives until it fires — reuse a `time.Timer`.
- **`defer` in a hot/unbounded loop:** defers stack until the *function* returns, holding the resource
  the whole time. Close explicitly each iteration or extract a helper function.
- **Unreleased resource:** every `file`/`rows`/`resp.Body`/`conn`/lock is released on all paths;
  check `rows.Err()` after iterating; a `resp.Body` you don't read+close leaks a connection.
- **Unbounded cache/map:** a global map that only grows is a slow OOM. Add TTL, LRU, or a size cap.

## Slices, maps & aliasing
- A sub-slice shares the parent's backing array: `append` to it can overwrite the parent, and a
  returned sub-slice lets the caller clobber your data. Copy (or `slices.Clip`) before handing out or
  appending to a slice you don't own.
- Writing to a `nil` map panics — initialize with `make` before writing.
- Map iteration order is randomized; never depend on it (`slices.Sorted(maps.Keys(m))` for order).
- `sync.OnceFunc`/`OnceValue`/`OnceValues` (Go 1.21+) beat `sync.Once`+a flag for lazy init.

## Races
- No shared mutable state without a mutex/atomic covering **all** accesses, or single-goroutine
  ownership with channel handoff. A mutex around *some* accesses is still a race.
- Maps are never written concurrently — that panics at runtime, not just under `-race`.
- `go test -race ./...` is part of the standard test command; a reported race is a blocker.
- Don't "fix" a race with `time.Sleep`. Synchronize with channels/`WaitGroup`/`sync`.
- On Go < 1.22, rebind loop variables before capturing in a goroutine (`i := i`). Go 1.22+ scopes them
  per-iteration, but be explicit about the version you target.

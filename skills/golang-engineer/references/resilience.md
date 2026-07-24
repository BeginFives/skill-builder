# Resilience: retries, backoff, rate limiting, breakers

Load this when calling a dependency that can fail or throttle (any network/RPC/DB call). Governing
rule: **retry only idempotent operations, with capped exponential backoff + jitter, honoring `ctx` and
`Retry-After` — and never retry your way into a retry storm.** Naive retry loops are a top incident cause.

## Contents
- [Retry only idempotent operations](#retry-only-idempotent-operations)
- [Exponential backoff with full jitter](#exponential-backoff-with-full-jitter)
- [Idempotency keys](#idempotency-keys)
- [Rate limiting](#rate-limiting)
- [Circuit breaking & degradation](#circuit-breaking--degradation)
- [Timeout budget](#timeout-budget)

## Retry only idempotent operations
Retrying is safe for reads and idempotent writes (PUT/DELETE with the same key). Retrying a
non-idempotent write (create-charge, append) can double-execute — make it idempotent first (below) or
do not retry it. Retry only on *transient* errors (timeouts, 429, 502/503/504, connection resets), not
on 400/401/403/404/validation errors, which will fail identically every time.

## Exponential backoff with full jitter
Fixed-interval retries synchronize clients into a thundering herd. Use exponential growth with **full
jitter** (random in `[0, backoff]`), cap the delay and the attempt count, honor `ctx` while waiting,
and respect a server's `Retry-After`:
```go
func withRetry(ctx context.Context, maxAttempts int, op func(context.Context) error) error {
    const base, maxDelay = 100 * time.Millisecond, 10 * time.Second
    var err error
    for attempt := 0; attempt < maxAttempts; attempt++ {
        if err = op(ctx); err == nil || !isRetryable(err) {
            return err // success, or a permanent error — don't retry
        }
        backoff := min(base<<attempt, maxDelay)
        delay := time.Duration(rand.Int64N(int64(backoff))) // full jitter: [0, backoff)
        t := time.NewTimer(delay)
        select {
        case <-ctx.Done():
            t.Stop()
            return ctx.Err() // stop retrying the moment the caller's deadline passes
        case <-t.C:
        }
    }
    return fmt.Errorf("after %d attempts: %w", maxAttempts, err)
}
```
`rand.Int64N` is `math/rand/v2` (Go 1.22+), fine for jitter (not for security). Prefer a vetted library
(`cenkalti/backoff`, `avast/retry-go`) in real code, but the shape above is the contract to hold.

## Idempotency keys
To make a create-style write safely retryable, send a client-generated idempotency key (a UUID) the
server dedupes on: the first request performs the work and records the key+result; retries with the
same key return the stored result instead of re-executing. This is what lets you retry writes at all.

## Rate limiting
Bound the rate of your *outbound* calls (and protect your own endpoints) with a token bucket —
`golang.org/x/time/rate`. `Wait` blocks until a token is available or ctx is cancelled:
```go
lim := rate.NewLimiter(rate.Limit(100), 20) // 100 rps sustained, burst 20
if err := lim.Wait(ctx); err != nil {        // respects ctx cancellation/deadline
    return err
}
```

## Circuit breaking & degradation
When a dependency is clearly down, stop hammering it: a breaker trips OPEN after a failure threshold,
fails fast for a cooldown, then probes HALF-OPEN before closing. Pair it with graceful degradation —
serve a cached/default/partial response instead of failing the whole request when a non-critical
dependency is unavailable. Use a maintained library (`sony/gobreaker`) rather than hand-rolling the
state machine.

## Timeout budget
Retries multiply latency — a per-attempt 2s timeout × 3 attempts is a 6s+ worst case. Set an overall
deadline on the *outer* `ctx` so the retry loop can't exceed the caller's budget, and derive each
attempt's timeout from the remaining budget rather than a fixed per-attempt value.

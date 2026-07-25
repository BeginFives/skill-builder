# Error handling

Load this when designing error types, deciding how to wrap, or choosing return-vs-log. Governing
rule: **errors are values, handled exactly once, with their chain preserved.**

## Contents
- [Wrapping](#wrapping)
- [Sentinel vs typed errors](#sentinel-vs-typed-errors)
- [Inspecting: Is / As](#inspecting-is--as)
- [Handle exactly once](#handle-exactly-once)
- [Panic & recover — only at the edge](#panic--recover--only-at-the-edge)
- [Style](#style)

## Wrapping
Wrap with `%w` to preserve the chain so callers can `errors.Is`/`As` through it. Put the wrap verb at
the end and describe the operation, not the error:
```go
if err := repo.Save(ctx, u); err != nil {
    return fmt.Errorf("save user %s: %w", u.ID, err)
}
```
Use `%v` (not `%w`) **only** when you deliberately want to sever the chain at a boundary — e.g. an API
layer that must not leak internal error types to clients. Don't double-wrap redundant context ("save
user: save user: …"). Add context the caller doesn't already have (an ID, a filename), not a restatement.

## Sentinel vs typed errors
**Sentinel** — a static, comparable error the caller matches by identity:
```go
var ErrNotFound = errors.New("not found") // exported as ErrXxx
```
**Typed** — when the caller needs structured fields:
```go
type ValidationError struct {
    Field string
    Msg   string
    inner error // optional wrapped cause; returned by Unwrap below
}
func (e *ValidationError) Error() string { return e.Field + ": " + e.Msg }
```
Name sentinels `ErrXxx` and typed errors `XxxError`. Implement `Unwrap() error` on a typed error only
if it carries an inner cause:
```go
func (e *ValidationError) Unwrap() error { return e.inner }
```

## Inspecting: Is / As
Never compare with `==` after wrapping, and never string-match `err.Error()`:
```go
if errors.Is(err, ErrNotFound) {           // matches sentinels through the chain
    return http.StatusNotFound
}
var ve *ValidationError
if errors.As(err, &ve) {                    // extracts a typed error through the chain
    slog.Warn("invalid input", "field", ve.Field)
}
```

## Typed nil & aggregation
- **Never return a typed nil pointer as an `error`.** A `nil` `*MyError` placed in an `error` interface
  is non-nil (`err != nil` is true) — the single most notorious Go bug:
  ```go
  func do() error {
      var e *MyError // nil pointer
      // ... e never set ...
      return e       // BUG: caller sees a NON-nil error. Return `nil` literally instead.
  }
  ```
  Return `nil` explicitly on the success path, or check `if e != nil` before returning.
- **Aggregate with `errors.Join`** (Go 1.20+) when independent operations can each fail (validation
  batch, multi-resource cleanup): `err := errors.Join(v1, v2, v3)` — nil inputs are dropped, and
  `errors.Is`/`errors.As` traverse the joined tree.

## Handle exactly once
On any given path, an error is **either** returned (optionally wrapped) **or** logged — never both.
The layer that finally decides to drop, degrade, or surface it does the logging; every layer above
only adds `%w` context and returns.
```go
// WRONG — double handling: logged here AND returned, so it's logged again upstream.
if err != nil {
    log.Error("save failed", "err", err)
    return err
}
// RIGHT — add context and return; let the top of the stack log once.
if err != nil {
    return fmt.Errorf("save user %s: %w", u.ID, err)
}
```
Never ignore an error with `_` on a fallible call without a comment justifying why it's safe — this
includes `resp.Body.Close()`, `json.Unmarshal`, and DB calls (`errcheck` flags these).

## Panic & recover — only at the edge
`panic` is for programmer bugs and impossible states, not expected failures (a missing file, a bad
request, a network blip all return `error`). `recover()` lives only at a trusted process edge and must
re-log or convert the value — never a naked `recover()` that silently swallows:
```go
// HTTP recovery middleware — the boundary where a panicking handler must not kill the server.
func Recover(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        defer func() {
            if v := recover(); v != nil {
                slog.ErrorContext(r.Context(), "panic recovered", "panic", v)
                http.Error(w, "internal error", http.StatusInternalServerError)
            }
        }()
        next.ServeHTTP(w, r)
    })
}
```
**An unrecovered panic in _any_ goroutine crashes the entire process** — and the server middleware
above only protects the request goroutine, not goroutines a handler spawns. So every goroutine you
spawn to run non-trivial work needs its own recover:
```go
go func() {
    defer func() {
        if v := recover(); v != nil {
            slog.ErrorContext(ctx, "worker panic recovered", "panic", v)
        }
    }()
    doWork(ctx)
}()
```
`os.Exit`/`log.Fatal` belong only in `main()`; libraries return errors.

## Style
- Error strings are lowercase, no trailing punctuation, no "failed to" noise (`"connect to db: %w"`,
  not `"Failed to connect to DB.: %w"`) — they get wrapped and concatenated.
- Return early on error; keep the happy path un-indented.
- Don't log an error and continue as if it succeeded — either recover meaningfully or propagate.

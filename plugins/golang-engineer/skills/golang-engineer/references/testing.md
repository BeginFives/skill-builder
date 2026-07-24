# Testing

Load this when writing tests. Governing rule: **test observable behavior through the exported API,
table-driven, and always under `-race`.** A test that passes trivially or tests a mock is worse than
no test — it manufactures false confidence.

## Contents
- [Table-driven tests](#table-driven-tests)
- [Race, parallel, cleanup](#race-parallel-cleanup)
- [Assertions](#assertions)
- [Golden files](#golden-files)
- [Fuzzing](#fuzzing)
- [HTTP & integration tests](#http--integration-tests)
- [Determinism](#determinism)

## Table-driven tests
Named subtests via `t.Run`; each case a struct with a descriptive name. Test the exported behavior,
not unexported internals.
```go
func TestParseAmount(t *testing.T) {
    tests := []struct {
        name    string
        in      string
        want    int64
        wantErr error
    }{
        {"whole", "10", 1000, nil},
        {"cents", "10.50", 1050, nil},
        {"empty", "", 0, ErrEmpty},
        {"garbage", "abc", 0, ErrSyntax},
    }
    for _, tc := range tests {
        t.Run(tc.name, func(t *testing.T) {
            t.Parallel()
            got, err := ParseAmount(tc.in)
            if !errors.Is(err, tc.wantErr) {   // match by identity, not string
                t.Fatalf("err = %v, want %v", err, tc.wantErr)
            }
            if got != tc.want {
                t.Errorf("got %d, want %d", got, tc.want)
            }
        })
    }
}
```

## Race, parallel, cleanup
- The standard command is `go test -race ./...`. CI runs it; a race is a blocker.
- `t.Parallel()` in independent subtests; ensure no shared mutable state and no order dependence.
- Teardown via `t.Cleanup(func(){ … })`, not manual defers that a `t.Fatal` can skip.
- Mark helpers with `t.Helper()` so failures report the caller's line.

## Assertions
Stdlib checks are fine; `testify` is idiomatic at scale — `require` for preconditions that must abort
the test, `assert` for soft checks that should keep going. For structs, compare with
`cmp.Diff(want, got)` and fail on a non-empty diff (readable failures beat `reflect.DeepEqual`).

## Golden files
For large expected output, store a golden file and gate updates behind a flag:
```go
var update = flag.Bool("update", false, "update golden files")
// … if *update { os.WriteFile(golden, got, 0o644) } else { compare against golden }
```
Goldens are reviewed artifacts, not dumping grounds — diff them in code review.

## Fuzzing
Every parser, decoder, or deserializer gets a fuzz target with a seed corpus. Fuzzing finds the panics
and infinite loops table tests miss.
```go
func FuzzParseAmount(f *testing.F) {
    f.Add("10.50")
    f.Add("")
    f.Fuzz(func(t *testing.T, s string) {
        _, _ = ParseAmount(s) // must never panic on any input
    })
}
```
Run with `go test -run=Fuzz` (corpus) or `go test -fuzz=FuzzParseAmount` (active fuzzing).

## HTTP & integration tests
- Use `net/http/httptest` for handlers and fake upstreams — a real transport, not a hand-rolled mock:
  ```go
  srv := httptest.NewServer(handler)
  defer srv.Close()
  ```
- Put slow/external-dependency tests behind a build tag so unit tests stay fast and hermetic:
  ```go
  //go:build integration
  ```
  and run them with `go test -tags=integration`. Use `testcontainers-go` for a real Postgres/Redis
  rather than mocking the driver.

## Determinism
- Never `time.Sleep` to synchronize — use channels, `sync`, or `testing/synctest` for deterministic
  concurrency tests. On **Go 1.25+ it is stable**: `synctest.Test(t, func(t *testing.T){ … })` +
  `synctest.Wait()`. (In Go 1.24 it was experimental behind `GOEXPERIMENT=synctest` with `synctest.Run`.)
- Inject a clock (`func() time.Time`) rather than calling `time.Now()` inside the unit under test.
- Cover error and branch paths, not just the happy path — coverage is a floor, not a target, and the
  untested branch is where the production bug lives.

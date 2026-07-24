# Performance

Load this only when a path is genuinely hot and you need to reduce allocations or prove a speedup.
Governing rule: **measure first.** Optimization without a benchmark or profile is a guess, and a guess
that adds complexity is a net loss. Do not optimize cold paths.

## Contents
- [Benchmark before optimizing](#benchmark-before-optimizing)
- [Profiling with pprof](#profiling-with-pprof)
- [Escape analysis](#escape-analysis)
- [Allocation discipline](#allocation-discipline)

## Benchmark before optimizing
```go
func BenchmarkRender(b *testing.B) {
    in := makeInput()
    b.ReportAllocs()
    for b.Loop() { // Go 1.24+: excludes setup automatically AND keeps in/result alive so
        _ = Render(in) // the compiler can't optimize the call away (a common 0ns/op bug)
    }
}
```
Run and compare with `benchstat`:
```
go test -bench=BenchmarkRender -benchmem -count=10 ./... > new.txt
benchstat old.txt new.txt   # only trust deltas that survive variance
```
A change is a "speedup" only when `benchstat` shows it's significant across runs — a single run is noise.

## Profiling with pprof
- Benchmarks: `go test -bench=. -cpuprofile=cpu.out -memprofile=mem.out`, then `go tool pprof cpu.out`.
- Live services: expose `net/http/pprof` on an **internal** admin port only, then
  `go tool pprof http://localhost:6060/debug/pprof/profile`. Never expose pprof publicly.
- Profile types: CPU (where time goes), heap (what allocates), block & mutex (contention). Find the
  real hot spot before touching anything — intuition about Go hot spots is usually wrong.

## Escape analysis
See what escapes to the heap:
```
go build -gcflags='-m' ./...   # "escapes to heap" lines show heap allocations
```
Common avoidable escapes: returning a pointer to a local that could be a value; passing a value through
an `interface{}`/`any` (boxing); capturing a variable in a closure that outlives the call. Keep hot-path
values on the stack where feasible — but only after a profile says the path matters.

## Allocation discipline
- Preallocate slices/maps when the size is known: `make([]T, 0, n)`, `make(map[K]V, n)`. Growing a
  slice by `append` reallocates and copies repeatedly.
- Reuse buffers for high-churn work with `sync.Pool` — but only where a profile shows GC pressure; a
  misused pool is a bug source.
- Build strings with `strings.Builder` (or `bytes.Buffer`) in loops, not `+=`. Use `strconv` over
  `fmt.Sprintf` on hot paths.
- Avoid per-iteration allocations in hot loops: `time.After` in a select-loop, repeated `fmt.Sprintf`,
  and `[]byte`↔`string` round-trips all allocate. Stream large payloads with `io.Copy` instead of
  reading the whole thing into memory.
- Right-size, don't over-tune: readability wins on the 99% of code that isn't hot. Leave a comment with
  the benchmark number when you do trade clarity for speed, so the next reader knows it was measured.

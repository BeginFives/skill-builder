# Performance — Python

Load this when a path is measurably hot, or when deciding how a workload should run at all.
Governing rule: **measure first, and pick the concurrency model before you micro-optimize anything.**
The wrong model caps your speedup at ~1x, and no amount of tightened inner loop recovers it.

## Contents
- [Step 0 — name the bound](#step-0--name-the-bound)
- [Templates per bound](#templates-per-bound)
- [Profiling: cProfile for repros, py-spy for live](#profiling-cprofile-for-repros-py-spy-for-live)
- [Micro-benchmarks with timeit](#micro-benchmarks-with-timeit)
- [Memory with tracemalloc](#memory-with-tracemalloc)
- [Hot-path mechanics](#hot-path-mechanics)
- [Caching](#caching)
- [Knowing when to stop](#knowing-when-to-stop)

## Step 0 — name the bound
Answer "where does the wall clock actually go?" before writing one line of optimization. One profile,
or 60 seconds of `py-spy top`, answers it.

| Bound | Model | Why |
|---|---|---|
| I/O, async-native client | `asyncio` + `TaskGroup` | thousands of sockets on one thread; waiting costs no thread |
| I/O, blocking driver only | threads (`asyncio.to_thread` / `ThreadPoolExecutor`) | the GIL is released around blocking syscalls |
| CPU, pure Python | processes (`ProcessPoolExecutor`) | one GIL per interpreter; threads buy nothing |
| CPU, array / dataframe / text math | numpy, polars, pyarrow, or a Rust/C extension | moves the loop out of the interpreter — 10–100x, beats any pool |
| CPU **and** heavy I/O | processes, each running its own event loop | one pool cannot serve both bounds |

Measured (CPython 3.14, 12-core macOS, 4x a 3M-iteration integer loop):
`serial 0.42s · 4 threads 0.44s (0.97x) · 4 processes 0.21s (1.99x)`.

- **FAIL if** you put CPU-bound Python behind a `ThreadPoolExecutor` and called it a speedup — you paid
  context-switch overhead and opened a shared-state race surface for 0.97x.
- **FAIL if** an `async def` calls anything blocking (`requests`, a sync DB driver, `time.sleep`, a slow
  `open().read()`). It stalls the entire loop: every other task on that thread stops until it returns.
- Free-threading (`python3.13t`+, PEP 703; officially supported but still non-default in 3.14) removes
  the GIL. Do not design around it unless you control the interpreter build **and** have measured it.
- *3.14:* `concurrent.futures.InterpreterPoolExecutor` (PEP 734) runs work in per-thread subinterpreters —
  cheaper than processes, still needs picklable payloads. Benchmark it against processes; don't assume.

## Templates per bound
```python
# I/O-bound, async-native client: one thread, thousands of calls in flight.
async def fetch_all(ids: Sequence[int]) -> list[Row]:
    async with asyncio.TaskGroup() as tg:                # 3.11+
        tasks = [tg.create_task(fetch(i)) for i in ids]
    return [t.result() for t in tasks]

# I/O-bound with a blocking driver: never call it inline inside a coroutine.
async def load(row_id: int) -> Row:
    return await asyncio.to_thread(legacy_driver.get, row_id)

# CPU-bound pure Python: processes. The worker must be importable at module
# level (it gets pickled) and chunksize must amortize per-task IPC.
def digest_all(chunks: Sequence[bytes]) -> list[str]:
    with ProcessPoolExecutor() as pool:
        return list(pool.map(digest, chunks, chunksize=64))
```
- `to_thread` uses the loop's **shared** default executor, sized `min(32, cpu+4)` (16 on a 12-core box).
  A burst of slow blocking calls queues behind itself and starves everything else — give a hot subsystem
  its own bounded `ThreadPoolExecutor` and `loop.run_in_executor(pool, ...)`.
- **FAIL if** `pool.map` runs millions of tiny tasks at the default `chunksize=1`: pickling per task
  dominates and the pool loses to the serial loop. Size the chunk so each task is ≥ ~10ms of work.

## Profiling: cProfile for repros, py-spy for live
```
python -m cProfile -o prof.out -m yourpkg.cli --flags     # whole run, no code edits
```
```python
with cProfile.Profile() as pr:                            # or scope it to the suspect path
    handle(request)
pstats.Stats(pr).strip_dirs().sort_stats("tottime").print_stats(15)
pstats.Stats(pr).dump_stats("after.out")                  # keep it; diff vs before.out
```
- `tottime` = self time (who burns the CPU); `cumtime` = including callees (who is *responsible*). Read both.
- cProfile taxes every call: measured **4.1x** inflation on a call-heavy loop vs **1.0x** on a call-free
  one. It therefore over-blames small hot functions — confirm the winner with `timeit` before rewriting.
- It profiles only the calling thread and mis-attributes `await` time; nesting it under
  `python -m cProfile` raises `ValueError: Another profiling tool is already active`.
- **FAIL if** you profile without production-shaped input. A profile over 10 rows optimizes the branch
  that doesn't run at 10M.

```
py-spy top --pid 4242                              # live, no restart, sampling
py-spy record --pid 4242 -d 30 -s -o flame.svg     # -s: include subprocesses
py-spy dump --pid 4242                             # every thread's stack, for a wedged process
```
- Sampling and **out-of-process**: no instrumentation, no import hook, no code change — the only
  profiler to reach for on a running prod service. `py-spy dump` is the first command of a "worker is
  hung" incident: it answers "stuck where?" without restarting anything.
- `--gil` restricts to threads actually holding the GIL (true CPU); `--idle` includes sleeping threads
  (tells you where you *block*). Needs ptrace: `sudo` on macOS, `--cap-add SYS_PTRACE` in Docker.

## Micro-benchmarks with timeit
```
python -m timeit -s "parts = [str(i) for i in range(2000)]" "''.join(parts)"
```
```python
samples = timeit.repeat(stmt, setup, repeat=7, number=200, globals=globals())
best = min(samples) / 200               # min = the run least polluted by other load
spread = max(samples) / min(samples)    # > ~1.2 => the machine is too noisy to trust today
```
Why a naive `time.time()` loop misleads:
- `time.time()` is wall clock and NTP-adjustable, so it can go *backwards*. Use `time.perf_counter()` —
  and never for a single shot.
- One sample is noise. `repeat` then `min` gives you the floor; a mean folds in other processes' load.
- `timeit` disables the cyclic GC while timing, so an unrelated collection isn't billed to your snippet.
  Put `gc.enable()` in `setup` when GC pressure is precisely what you're measuring.
- **FAIL if** `setup` builds a one-shot object the statement consumes. `setup="g=(i*i for i in
  range(100_000))"` with `stmt="sum(g)"` measured 3.8ms vs 16ms for the honest form — a fake 4.4x,
  because every run after the first sums an exhausted generator. Build the consumable inside `stmt`.

## Memory with tracemalloc
```python
tracemalloc.start(10)                   # 10 frames: shows the caller, not just the alloc site
before = tracemalloc.take_snapshot()
serve_n_requests(1000)
after = tracemalloc.take_snapshot()
for stat in after.compare_to(before, "lineno")[:10]:
    print(stat)                         # app/cache.py:41: size=4903 KiB (+4903 KiB), count=501 (+501)
```
- Two snapshots at the *same point* of a steady-state loop; growth between them is the leak. One
  snapshot proves nothing — it can't distinguish live working set from garbage.
- It only sees the CPython allocator — numpy buffers, arrow, and C-extension `malloc` are invisible. If
  RSS climbs while tracemalloc stays flat, it's native memory: use `memray` or RSS sampling.
- It roughly doubles allocation cost. Gate it behind a flag; never leave it on by default in prod.

## Hot-path mechanics
The cost model that explains all of these: a Python-level call or attribute lookup costs ~10–100x the
C-level operation underneath it. The win is almost always *"run the loop in C"*, not *"tighten the
Python loop"*.

| WRONG | RIGHT | Why |
|---|---|---|
| `x in big_list` | `x in big_set` (or dict) | 161 µs → 11 ns measured @20k items; by far the most common real fix |
| `rows = list(read_all())`, then loop | `for r in read_all():` (generator) | constant memory, first row available immediately |
| `out += part` in a loop | `"".join(parts)` | see below |
| `list.pop(0)` / `insert(0, x)` | `collections.deque` | O(n) → O(1) |
| `sorted(xs)[:10]` | `heapq.nsmallest(10, xs)` | O(n log n) → O(n log k) |
| `for i in range(len(a)): f(a[i])` | `[f(v) for v in a]` / `map` | the iteration runs in C |
| `df = pd.concat([df, row])` per row | collect rows, construct once | quadratic copies |

**String building.** CPython has an in-place-realloc trick that *hides* `+=`'s quadratic behavior — until
anything else holds a reference to an intermediate (a log call, an append, a captured f-string). Measured
at 32k parts: `"".join` **0.18 ms** · `+=` unaliased **1.1 ms** · `+=` with one live alias **86 ms**.
Always `join`; the fast case is an accident you don't control.

**`__slots__`** for objects you hold by the hundred-thousand:
```python
@dataclass(slots=True, frozen=True)     # slots=True: 3.10+
class Point:
    x: float
    y: float
```
Measured 48 B/instance vs 344 B for the `__dict__` version. Costs: no `__dict__` (no ad-hoc attributes,
no monkeypatching in tests), no `weakref` unless you add `__weakref__`, and multiple inheritance gets
finicky. **FAIL if** you added `__slots__` to a class with twelve live instances.

**Local binding is folklore now.** Hoisting `append = out.append` out of a loop was real on 3.8.
Measured on 3.14 it is consistently *slower* than the plain loop (254 µs vs 241 µs) because the
specializing interpreter (3.11+) already caches the attribute lookup, and the bound-method local defeats
that specialization. Rule: never apply this from memory — benchmark it on the interpreter you ship, or
write a comprehension and skip the question entirely.

## Caching
```python
@functools.lru_cache(maxsize=4096)      # a maxsize on anything keyed by user input
def parse_rule(source: str) -> tuple[str, ...]:
    return tuple(source.split(","))

metrics.gauge("rule_cache.size", parse_rule.cache_info().currsize)   # export it; it's a bound you own
```
- `@functools.cache` is `lru_cache(maxsize=None)` — **an unbounded dict keyed by your arguments.** Safe
  only when the key space is finite and small (an enum, a config name). **FAIL** on anything request-derived.
- Entries hold strong references to args *and* results, so a bounded cache of large objects is still a
  memory ceiling you chose. Size it by bytes you can afford, not by "entries that feel fine".
- **FAIL: `@cache` on a method.** `self` becomes part of the key, so the cache pins every instance that
  ever called it. Use `functools.cached_property` for per-instance memoization.
  [TOOL: `ruff` B019 catches this]
- `lru_cache` does not collapse concurrent misses: N threads missing the same key all compute it. If the
  miss is expensive, add a per-key lock — or accept the duplicate work deliberately.
- Unhashable arguments raise `TypeError`. Cache on scalars, not on dicts or dataclasses holding lists.

## Knowing when to stop
- ~95% of code is cold, and cold code's only performance requirement is that it stays easy to read for
  the day it becomes hot. Do not optimize it.
- Ship an optimization only when (a) a profile named the line, (b) a benchmark shows the delta survives
  repeats, and (c) the delta matters at production input size. Two out of three is a guess.
- When you trade clarity for speed, leave the evidence *in the code*:
  ```python
  # join, not +=: 86ms -> 0.18ms at 32k parts (bench: tests/bench_render.py::test_join)
  ```
  Without the number, the next reader cannot tell an optimization from a superstition — so they will
  neither trust it nor dare to simplify it.
- A numpy/polars/Rust rewrite is a permanent dependency and a hiring constraint. Take it when the
  measurement says 10x, not 20%.

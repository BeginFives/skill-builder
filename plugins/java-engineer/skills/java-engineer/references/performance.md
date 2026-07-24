# Performance — Java

Load this only when a path is genuinely hot and you need to cut latency, allocation, or GC pressure.
Governing rule: **measure first, with JMH or a profiler.** The JIT makes intuition unusually unreliable
in Java: the same method is interpreted, then C1, then C2-compiled, and C2 will delete work whose result
you never use. A number you did not get from JMH or a profiler is not evidence. Do not optimize cold paths.

## Contents
- [JMH is the only credible microbenchmark](#jmh-is-the-only-credible-microbenchmark)
- [Profiling real workloads: JFR and async-profiler](#profiling-real-workloads-jfr-and-async-profiler)
- [Allocation discipline](#allocation-discipline)
- [Strings on hot paths](#strings-on-hot-paths)
- [Streams vs loops, and why parallel streams are usually wrong](#streams-vs-loops-and-why-parallel-streams-are-usually-wrong)
- [GC: pick a collector, then lower the allocation rate](#gc-pick-a-collector-then-lower-the-allocation-rate)
- [Bounded caches](#bounded-caches)
- [Warmup and startup](#warmup-and-startup)
- [When not to optimize](#when-not-to-optimize)

## JMH is the only credible microbenchmark
```java
long t0 = System.nanoTime();
for (int i = 0; i < reps; i++) hash(in);   // WRONG: result unused -> C2 deletes the call
System.out.println((System.nanoTime() - t0) / (double) reps + " ns/op");
```
Measured on JDK 25: this prints **0.025 ns/op**. The same loop with the result accumulated prints
**13.6 ns/op**. The hand-rolled version was 500x wrong — it timed an empty loop. Add missing warmup (the
first thousands of invocations are interpreted) and constant folding of `static final` inputs, and a
nanoTime loop can be wrong in either direction by any factor. Use JMH:
```java
@BenchmarkMode(Mode.AverageTime) @OutputTimeUnit(TimeUnit.NANOSECONDS)
@State(Scope.Benchmark)                          // Scope.Thread if the benchmark mutates the state
@Fork(value = 2, jvmArgs = {"-Xms1g", "-Xmx1g"}) // fresh JVMs: JIT profiles don't leak between variants
@Warmup(iterations = 5, time = 1)                // let C2 compile before anything is recorded
@Measurement(iterations = 10, time = 1)
public class RenderBenchmark {
  @Param({"10", "1000"}) public int size;        // size effects are where the surprises live
  private List<Order> orders;                    // NOT `final` with a literal: that gets folded away

  @Setup(Level.Trial) public void setUp() { orders = Fixtures.orders(size); }

  @Benchmark public String csv() { return Render.csv(orders); }   // returned -> JMH consumes it
  @Benchmark public void perLine(Blackhole bh) {
    for (Order o : orders) bh.consume(Render.line(o));            // many results -> sink each one
  }
}
```
Scaffold with `mvn archetype:generate -DarchetypeGroupId=org.openjdk.jmh
-DarchetypeArtifactId=jmh-java-benchmark-archetype -DarchetypeVersion=1.37`, then
`mvn clean package && java -jar target/benchmarks.jar RenderBenchmark -prof gc`.
- **JDK 23+ gotcha:** javac no longer discovers annotation processors from the classpath. Declare
  `jmh-generator-annprocess` under the compiler plugin's `<annotationProcessorPaths>` (or pass
  `-proc:full`), or JMH silently generates nothing and the run reports "no matching benchmarks".
- Always run `-prof gc` and quote `gc.alloc.rate.norm` (B/op) beside ns/op: bytes/op is stable, ns/op isn't.
- FAIL if: the benchmark result is neither returned nor `Blackhole.consume`d (dead-code elimination);
  inputs are `static final` literals (constant folding); `@Fork(0)` or no fork (profile pollution from
  the previous variant); a "win" is claimed from one run with overlapping `±` error bars.

## Profiling real workloads: JFR and async-profiler
A microbenchmark proves a change; only a profile of the real workload tells you *what* to change.
```bash
-XX:+UnlockDiagnosticVMOptions -XX:+DebugNonSafepoints    # accurate stacks for any sampling profiler
-XX:StartFlightRecording:settings=profile,maxsize=512m,filename=/var/log/app.jfr,dumponexit=true
jcmd <pid> JFR.start name=prof settings=profile duration=120s filename=/tmp/app.jfr   # or on demand
jfr summary /tmp/app.jfr        # event counts; open in JDK Mission Control for the real analysis

asprof -e cpu    -d 60 -f /tmp/cpu.html   <pid>   # async-profiler: sharpest flame graphs
asprof -e alloc  -d 60 -f /tmp/alloc.html <pid>   # allocation *sites*, which a heap dump cannot show
asprof -e wall -t -d 60 -f /tmp/wall.html <pid>   # blocked/waiting time, which CPU profiles hide
```
- Latency problem ⇒ start with `wall` (or JFR's socket/file-read events): most p99 in a service is
  waiting, not computing, and a CPU profile of a waiting thread is empty. Allocation problem ⇒ `alloc`.
- FAIL if you conclude from a heap dump that something is hot: a heap dump shows what *survived*, not
  what *allocated*. JFR is the prod default (always-on-cheap, joins GC/JIT/I/O on one timeline);
  async-profiler is what you attach when JFR says "somewhere in this method".

## Allocation discipline
Allocation is a pointer bump; the cost is the GC work its survivors create. The lever is **bytes per
operation**, measured with `-prof gc`.
- **Escape analysis is real but fragile.** HotSpot scalar-replaces objects that provably don't escape —
  but only when the allocating method inlines, and storing to a field, returning, or passing through a
  megamorphic call site defeats it. Rely on it, then verify: `gc.alloc.rate.norm` near 0 or nothing.
- **Boxing is the #1 avoidable allocation.** `Integer.valueOf` caches only −128..127; everything else is
  a 16-byte object plus an 8-byte reference. `List<Long>` costs ~4x `long[]`. A `Map<Long, V>` boxes on
  every `get`/`put`. For hot long/int-keyed maps use fastutil (`Long2ObjectOpenHashMap`) or Eclipse
  Collections; for hot numeric aggregates use `long[]`/`IntStream`, never `Stream<Integer>`.
- **Pre-size collections.** `HashMap.newHashMap(n)` (Java 19+) fits `n` entries without a rehash;
  `new HashMap<>(n)` still resizes at `0.75 * n`. `new ArrayList<>(n)` avoids the grow-and-copy chain.
- **Hoist per-call buffers** out of loops (`byte[] buf = new byte[8192]` once, then `in.read(buf)`), and
  stream large payloads instead of materializing a whole response into a `byte[]`.
- **Exceptions are not control flow** — `fillInStackTrace` walks the stack and dominates the cost. When
  a "failure" is a routine signal, make it stackless with
  `super(msg, null, false, false)` (no suppression, no writable stack trace).
- FAIL if a hot path calls `Pattern.compile` (or `String.matches`, which compiles internally) per call.
  Hoist to a `private static final Pattern`.

## Strings on hot paths
Measured with JMH on JDK 25 (`"order:" + id + ":" + sku`, per call):

| form | time | garbage |
|---|---|---|
| `a + b + c` in one expression | ~9 ns | 64 B |
| hand-rolled `StringBuilder` | ~11 ns | 112 B |
| `String.format("order:%d:%s", …)` | ~160 ns | 520 B |

- javac compiles a **single** concatenation expression to `invokedynamic` + `StringConcatFactory`, which
  sizes the buffer exactly once. WRONG: hand-rolling `new StringBuilder().append(a).append(b).toString()`
  for a one-line concat — slower *and* more garbage.
- The exception is accumulation: `s += x` **inside a loop** is O(n²), a fresh `String` per iteration.
  RIGHT: one pre-sized `StringBuilder` outside the loop, `append` inside.
- `String.format`/`MessageFormat` parse the pattern and box every argument: fine on startup and error
  paths, ~18x too slow per request. In logging use the logger's `{}` placeholders, so the formatting
  never happens when the level is disabled.

## Streams vs loops, and why parallel streams are usually wrong
Streams are usually fine; boxing and parallelism are what cost. Same sum over 1 000 records (JMH, JDK 25):

| form | time | garbage |
|---|---|---|
| indexed `for` loop | 0.35 µs | 0 B |
| `.stream().mapToLong(…).sum()` | 0.47 µs | 256 B |
| `.stream().map(…).reduce(0L, Long::sum)` | 4.2 µs | 44 800 B |
| `.parallelStream().mapToLong(…).sum()` | 28 µs | 13 900 B |

- A sequential stream costs ~1.3x a loop and reads better: keep it. The 12x loss is **boxing**, not the
  stream. Rewrite `map`+`reduce` over numbers as `mapToLong`/`mapToInt`, not as a loop.
- `parallelStream()` was **60x slower** here, and that is the normal outcome: it runs on the shared
  `ForkJoinPool.commonPool`, so a blocking or long task in one request starves every other user of that
  pool (including other parallel streams and `CompletableFuture` defaults) across the whole JVM.
- FAIL if `parallelStream()` appears near I/O, or over a collection not known to be large with heavy
  per-element CPU work. For I/O fan-out use an owned executor or virtual threads (see `concurrency.md`);
  for real data parallelism submit to your **own** pool so the blast radius is yours:
  `pool.submit(() -> list.parallelStream()…).get()`.

## GC: pick a collector, then lower the allocation rate
- **G1** (default) is right for almost everything: balanced throughput, ~100-200 ms worst pauses. **ZGC**
  when p99 pause is the SLO and heaps are large — sub-millisecond pauses for ~10-15% throughput and more
  footprint. Java 21: `-XX:+UseZGC -XX:+ZGenerational` (never run non-generational ZGC in a service);
  Java 24+: ZGC is generational only and the flag is gone.
- The lever you control is **allocation rate × survival rate** — halving bytes/op halves young
  collections. No flag combination rescues a service allocating 4 GB/s of medium-lived objects.
- **Humongous allocations (G1):** region = heap/2048 clamped to 1–32 MB, and any object ≥ 50% of a region
  goes straight into contiguous old-gen regions, skipping young collection and fragmenting the heap. On
  an 8 GB heap the region is 4 MB, so **every 2 MB+ array is humongous** — usually "read the whole
  file/response into a `byte[]`". Check `jcmd <pid> GC.heap_info`; fix by chunking, and only then
  consider `-XX:G1HeapRegionSize=32m`.
```bash
-XX:MaxRAMPercentage=75          # containers: derive the heap from the cgroup limit (default is 25%!)
-XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=/var/log -XX:+ExitOnOutOfMemoryError  # dump, then die
-Xlog:gc*:file=/var/log/gc.log:time,uptime,level,tags:filecount=5,filesize=20M
```
- FAIL if: a fixed `-Xmx` is baked into a container image (it desyncs from the memory limit); GC logging
  is off (it is what makes a production GC incident diagnosable afterwards); a PR adds a page of tuning
  flags from a blog. Set heap, collector, logging — then change one thing at a time.

## Bounded caches
```java
this.cache = Caffeine.newBuilder()
    .maximumSize(10_000)                      // hard bound -> eviction, not an OOM
    .expireAfterWrite(Duration.ofMinutes(10)) // staleness bound
    .refreshAfterWrite(Duration.ofMinutes(1)) // reload off the request path
    .recordStats()                            // hitRate/evictionCount -> metrics, or you're guessing
    .build(store::load);                      // one load per key: concurrent misses don't stampede
```
- FAIL: `private final Map<K,V> cache = new ConcurrentHashMap<>()` with no bound — under key churn it is
  a heap leak with good PR. Every cache needs a size (or `maximumWeight` + `weigher`) **and** a TTL.
- FAIL: `computeIfAbsent` whose mapping function does I/O — it runs under the bin lock, blocking
  unrelated keys in the same bin, and throws on recursive update.
- Export `stats()`: a cache below ~80% hit rate usually buys nothing for its memory and staleness cost —
  delete it and fix the underlying query instead.

## Warmup and startup
The first thousands of invocations run interpreted, then C1; C2 arrives later. Cold p99 is not
steady-state p99 — never let a container take traffic before a readiness check has exercised its hot path.
```bash
java -XX:AOTCacheOutput=app.aot -jar app.jar   # Java 25 training run (24: AOTMode=record then create)
java -XX:AOTCache=app.aot       -jar app.jar   # later starts load pre-linked classes: JEP 483/514
```
On Java 21 the equivalent is AppCDS: `-XX:ArchiveClassesAtExit=app.jsa`, then `-XX:SharedArchiveFile=app.jsa`.

## When not to optimize
- Check the algorithm, the N+1 query, and the network round-trip count **first**: they outrank every JIT
  and GC concern by orders of magnitude, and the profile usually points there anyway.
- Shaving 2 µs off a path inside a 40 ms request is not a win, it is added risk. 99% of code is cold and
  readability wins there without argument.
- When you do trade clarity for speed, leave the evidence in the code so the next reader doesn't undo it:
  ```java
  // JMH RenderBenchmark (JDK 21, c7i.2xlarge): 160 ns / 520 B per call -> 9 ns / 64 B.
  // Re-measure before "simplifying" this back to String.format.
  ```
- FAIL a PR that claims "faster" with no benchmark or profile attached — including your own.

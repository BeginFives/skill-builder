# Concurrency, cancellation & leak-freedom — Java

Load this when starting threads, executors, virtual threads or async pipelines — or when fixing a
hang, a leak, or a race. The governing rule: **every task has an owner that can await it and a stop
condition it honors.** If you can't name both, don't start it.

Baseline is **Java 25 LTS**: virtual threads, records, sealed types, and pattern matching for switch
(with `when` guards) are all **final**. `ScopedValue` is **final** in 25 (JEP 506). `StructuredTaskScope`
is still **preview** in 25 (JEP 505) — every snippet using it is marked and paired with a stable form.

## Contents
- [Choosing a concurrency tool](#choosing-a-concurrency-tool)
- [The ownership triad](#the-ownership-triad)
- [ExecutorService lifecycle](#executorservice-lifecycle)
- [Virtual threads — the default for blocking I/O](#virtual-threads--the-default-for-blocking-io)
- [Structured concurrency — the modern fan-out (Java 25 preview)](#structured-concurrency--the-modern-fan-out-java-25-preview)
- [Scoped values — request context that crosses forks](#scoped-values--request-context-that-crosses-forks)
- [CompletableFuture done right](#completablefuture-done-right)
- [Interruption is the cancellation protocol](#interruption-is-the-cancellation-protocol)
- [Timeouts on every blocking wait](#timeouts-on-every-blocking-wait)
- [Shared state](#shared-state)
- [ThreadLocal in pooled threads](#threadlocal-in-pooled-threads)
- [The leak catalogue](#the-leak-catalogue)

## Choosing a concurrency tool
Reach for the **first row that fits** — most "concurrency bugs" are really the wrong tool for the job.
The governing split for threads: **blocking/waiting → virtual; computing → platform.**

| The situation | Reach for | Not |
|---|---|---|
| Fan out many **independent blocking-I/O** calls (HTTP/DB/RPC) | `Executors.newVirtualThreadPerTaskExecutor()` — one virtual thread per task | a bounded platform pool; **never pool** virtual threads |
| Fan out where the **first failure should cancel the siblings**, joined as a unit | `StructuredTaskScope.open(Joiner.awaitAllSuccessfulOrThrow())` **(Java 25 preview)**; stable fallback: virtual-thread executor + `invokeAll(tasks, timeout, unit)` | hand-rolled `Future` juggling with manual cancellation |
| **CPU-bound** parallelism | a **platform** `ThreadPoolExecutor` sized ≈ CPU cores; or `parallelStream()` for a pure, side-effect-free map/reduce | virtual threads (no speedup — carrier-bound); `parallelStream` for *blocking* work (it uses the shared common pool) |
| **Compose** async stages (transform/combine results without blocking a thread) | `CompletableFuture` **with an explicit executor** | `supplyAsync(fn)` on the default common pool for blocking work |
| **Bound in-flight** calls against a limited resource (a 20-conn DB pool, a rate-limited API) | a `Semaphore` **permit count** | capping the *thread* count — virtual threads are free, the dependency is not |
| Guard **shared mutable state** | immutability first; then `ConcurrentHashMap`/atomics, or `synchronized`/`ReentrantLock` for a critical section | a queue/handoff for what is really just mutual exclusion |
| **Periodic / delayed** work | `ScheduledExecutorService` (own its lifecycle, name its threads) | `java.util.Timer` (one thread; one task's exception kills the rest) |
| Carry **request-scoped context** into forks | `ScopedValue` (final in 25) — see [Scoped values](#scoped-values--request-context-that-crosses-forks) | `ThreadLocal` (doesn't propagate into forks; leaks in pools) |

Each row is expanded below. When two rows both seem to fit, prefer the higher one — it is the simpler tool.

## The ownership triad
For every `submit` / `fork` / `Thread.start`, a reviewer must be able to point to:
1. **Who owns it** — the object holding the executor/scope, with a `close()` on its lifecycle.
2. **The exact condition that stops it** — interruption, a poison pill, a bounded loop, a deadline.
3. **How the owner learns it finished** — `Future.get`, `awaitTermination`, `scope.join`.

FAIL: `Thread.startVirtualThread(...)`, a `CompletableFuture.runAsync(...)` whose result is dropped, a
`static final ExecutorService` nobody closes. *Why: fire-and-forget work has no failure path — the
exception lands in an unobserved `Future`, so the outage is invisible until the heap fills.*

## ExecutorService lifecycle
`ExecutorService` is `AutoCloseable` (Java 19+), so a call-scoped executor belongs in try-with-resources:
```java
try (ExecutorService pool = Executors.newFixedThreadPool(8)) {   // closed on every path
    return pool.invokeAll(tasks).stream().map(this::getOrThrow).toList();
}   // close() = shutdown() + await; interrupting THIS thread escalates to shutdownNow()
```

A long-lived pool owned by a component needs the sequence spelled out — `shutdown()` alone never
interrupts anything, `shutdownNow()` alone discards queued work:
```java
static void shutdownGracefully(ExecutorService pool, Duration grace) {
    pool.shutdown();                                    // stop accepting; drain the queue
    try {
        if (!pool.awaitTermination(grace.toMillis(), TimeUnit.MILLISECONDS)) {
            pool.shutdownNow();                         // interrupt the stragglers
            if (!pool.awaitTermination(5, TimeUnit.SECONDS)) {
                log.error("pool did not terminate");    // report — don't pretend it stopped
            }
        }
    } catch (InterruptedException e) {
        pool.shutdownNow();
        Thread.currentThread().interrupt();             // restore: our caller still needs to see it
    }
}
```

**FAIL: `newFixedThreadPool`/`newSingleThreadExecutor` on an ingest path** — their `LinkedBlockingQueue`
is *unbounded*, so overload is a slow OOM with no backpressure signal (`newCachedThreadPool` is worse:
unlimited thread creation). Bound the queue and pick a rejection policy deliberately:
```java
new ThreadPoolExecutor(n, n, 0L, TimeUnit.MILLISECONDS,
    new ArrayBlockingQueue<>(queueDepth),                 // bounded
    Thread.ofPlatform().name("ingest-", 0).factory(),     // named: unnamed threads = unreadable dumps
    new ThreadPoolExecutor.CallerRunsPolicy());           // backpressure onto the submitter
```

FAIL: `submit()` for work whose failure nobody observes — it buries the throwable in the `Future`. Use
`execute()` (the `UncaughtExceptionHandler` fires) or actually inspect the `Future`.

## Virtual threads — the default for blocking I/O
`Executors.newVirtualThreadPerTaskExecutor()` (final since Java 21) is the default executor *for
blocking-I/O fan-out*: a parked virtual thread costs no OS thread, so 10 000 concurrent calls are fine.
One virtual thread per task — never a bounded pool.

- **They do not speed up CPU-bound work** — parallelism is still bounded by carrier count; size a
  platform pool for compute.
- **Never pool virtual threads.** They are cheap and disposable; pooling reintroduces the queueing limit
  you were escaping and revives ThreadLocal retention. One per task.
- **Unbounded virtual threads still overwhelm the dependency.** The thread count is free; the downstream
  is not. Bound in-flight calls with a `Semaphore` bulkhead — see below — not by capping the threads.

**Pinning — corrected for Java 24+ (this baseline is 25).** A virtual thread that blocked inside a
`synchronized` block/method used to **pin** its carrier (Java 21–23): enough pinned carriers and the
scheduler starved. **JEP 491 (final in JDK 24) removed that.** On Java 24 and 25 `synchronized` **no
longer pins** — a `synchronized` method holding a blocking call releases its carrier like any other
blocking point. The old advice *"swap every `synchronized` for `ReentrantLock` to avoid pinning"* is
**obsolete on this baseline** — do not repeat it.
```java
// Java 24+: this NO LONGER pins the carrier. synchronized is fine again for the vthread's sake.
synchronized (lock) { return blockingGet(k); }
```
Residual pinning in 24+ is narrow: only a blocking call reached through a **native (JNI) frame** or run
**during class initialization** (`<clinit>`) still pins — rare, and not something ordinary app locks
trigger. Observe it with the `jdk.VirtualThreadPinned` JFR event (the `-Djdk.tracePinnedThreads` flag
was the 21–23 diagnostic and is a no-op now). You **still** prefer `ReentrantLock` when you need
`tryLock`/timed/interruptible acquisition, fairness, or multiple `Condition`s — a **capability** choice,
not a pinning workaround:
```java
if (lock.tryLock(200, TimeUnit.MILLISECONDS)) {                  // timed + interruptible acquisition
    try { return blockingGet(k); } finally { lock.unlock(); }
} else { throw new TimeoutException("lock busy"); }
```

**Semaphore bulkhead — bound concurrency against a limited resource.** A permit count, not a thread
count, is what protects a 20-connection pool or a rate-limited API from a fan-out of thousands of
virtual threads:
```java
private final Semaphore dbPermits = new Semaphore(20);          // never > 20 concurrent queries

<T> T withDb(Callable<T> call) throws Exception {
    if (!dbPermits.tryAcquire(1, TimeUnit.SECONDS))            // timed: shed load, don't queue forever
        throw new RejectedExecutionException("db bulkhead full");
    try { return call.call(); }
    finally { dbPermits.release(); }                          // release on EVERY path, incl. exception
}
```
The virtual threads stay unbounded and cheap; the *permits* are the ceiling on the scarce resource.

## Structured concurrency — the modern fan-out (Java 25 preview)
The default shape for "call N things and combine them": the scope *is* the owner, a sibling's failure
cancels the rest, and `close()` cannot return while a subtask is alive. **`StructuredTaskScope` is still
PREVIEW in JDK 25 (JEP 505)** — compile **and** run with `--enable-preview`, and the API is not frozen.
JDK 25 **removed** the old `ShutdownOnFailure`/`ShutdownOnSuccess` subclasses; the shape now is
`open(Joiner…)`:
```java
// Java 25 PREVIEW — compile & run with --enable-preview
try (var scope = StructuredTaskScope.open(
        Joiner.<Object>awaitAllSuccessfulOrThrow(),             // all must succeed; 1st failure cancels siblings
        cf -> cf.withTimeout(Duration.ofSeconds(2)))) {         // deadline for the WHOLE fan-out
    Subtask<User> user          = scope.fork(() -> fetchUser(id));    // fork() returns Subtask<T>
    Subtask<List<Order>> orders = scope.fork(() -> fetchOrders(id));
    scope.join();                                               // throws on first subtask failure / timeout
    return new Page(user.get(), orders.get());                  // get() legal only AFTER a clean join()
}
```
The `Joiner` picks the policy: `awaitAllSuccessfulOrThrow()` (all-or-nothing), `anySuccessfulResultOrThrow()`
(first success wins — hedging; losers are cancelled), `allSuccessfulOrThrow()` (every subtask, each a
success), `awaitAll()` (wait for every outcome, then inspect each `Subtask`). `ScopedValue` bindings
propagate into these forks (next section) — a bare `ThreadLocal` does not.

**Because it is preview, always keep a stable path.** Where `--enable-preview` is off the table, fan out
on a try-with-resources virtual-thread executor (`invokeAll` has a timeout overload and cancels the
remainder), or compose `CompletableFuture`s — not hand-rolled latches:
```java
// STABLE (no preview): same fan-out on a virtual-thread-per-task executor + invokeAll with a deadline
try (var exec = Executors.newVirtualThreadPerTaskExecutor()) {
    List<Callable<Object>> tasks = List.of(() -> fetchUser(id), () -> fetchOrders(id));
    List<Future<Object>> fs = exec.invokeAll(tasks, 2, TimeUnit.SECONDS);  // cancels stragglers at deadline
    for (Future<Object> f : fs)
        if (f.isCancelled()) throw new TimeoutException("fan-out exceeded 2s");
    return new Page((User) fs.get(0).get(), (List<Order>) fs.get(1).get());
}
```
`invokeAll` does **not** give you sibling-cancels-on-first-failure for free (each task is independent).
When that semantic matters and preview is off, wrap the first failure and cancel the rest yourself — or
wait for `StructuredTaskScope` to go final and adopt it then.

## Scoped values — request context that crosses forks
`ScopedValue` (JEP 506) is **final in JDK 25** — no `--enable-preview`. It is the immutable,
structured-concurrency-aware replacement for `ThreadLocal` when carrying request-scoped context (tenant,
auth principal, correlation id). It **rebinds, never mutates**: the value is visible only inside the
`run`/`call` body you wrap, unbinds automatically at scope exit (no `remove()` to forget), and — unlike a
bare `ThreadLocal` — **propagates into `StructuredTaskScope` forks**, so children see it without you
threading it through every signature.
```java
static final ScopedValue<Principal> CALLER = ScopedValue.newInstance();

ScopedValue.where(CALLER, principal).run(() -> handle(request));   // .call(...) for a returning body
// the binding covers exactly this call tree — and nothing after it returns

Principal who() { return CALLER.isBound() ? CALLER.get() : ANON; } // read anywhere below, incl. forks
```
Contrast with `ThreadLocal`: a `ThreadLocal` is mutable, must be cleared in a `finally` or it leaks into
the next pooled request (below), and is **not** inherited by a `StructuredTaskScope` fork — the child
reads `null`. Use `ScopedValue` for new request-context plumbing; reserve `ThreadLocal` for what you
can't rebind (e.g. a third-party API that reads one). MDC/logging correlation still rides `ThreadLocal`
inside SLF4J — see `logging-observability.md`: seed the MDC at the edge, clear it in
`finally`, and bridge from a `ScopedValue` at the log boundary rather than mutating context mid-request.

## CompletableFuture done right
- **Always pass an explicit executor** to `supplyAsync`/`thenApplyAsync`. FAIL without one: work lands on
  the *common* ForkJoinPool — sized `availableProcessors() - 1` and shared JVM-wide, so one blocking
  call starves every parallel stream in the process. On a 1-vCPU container its parallelism is 0.
- **Never `join()`/`get()` on the common pool from a request thread** — the classic fan-out-then-block
  deadlock. Compose, and block once at the edge, with a timeout.
- Every chain ends in `exceptionally` or `handle`; a future that completes exceptionally with no
  consumer logs nothing, anywhere.
```java
CompletableFuture<Quote> quote(String sym, ExecutorService io) {
    return CompletableFuture.supplyAsync(() -> lookup(sym), io)
        .orTimeout(2, TimeUnit.SECONDS)                 // completes exceptionally w/ TimeoutException
        .thenApplyAsync(c -> new Quote(sym, c), io)
        .exceptionally(ex -> { log.warn("quote failed sym={}", sym, ex); return Quote.unavailable(sym); });
}
List<CompletableFuture<Quote>> fs = syms.stream().map(s -> quote(s, io)).toList();
CompletableFuture<List<Quote>> all = CompletableFuture.allOf(fs.toArray(CompletableFuture[]::new))
        .thenApply(v -> fs.stream().map(CompletableFuture::join).toList()); // safe: allOf gated it
```

**`orTimeout` and `cancel(true)` do not stop the running supplier** — a `CompletableFuture` has no thread
to interrupt, so they unblock *you* while the work keeps burning the executor. When it must actually
stop, submit through an `ExecutorService` and cancel that `Future`, or use a structured scope.

## Interruption is the cancellation protocol
The interrupt flag is the only cancellation signal the JDK has. Clearing it without acting makes the
task uncancellable and makes `shutdownNow()` hang forever.
```java
String take(BlockingQueue<String> q) throws InterruptedException { return q.take(); } // RIGHT: propagate

catch (InterruptedException e) {                    // RIGHT: signature is fixed — restore, then bail
    Thread.currentThread().interrupt();             // the flag is the only signal upstack
    throw new ShutdownException("interrupted awaiting work", e);
}
catch (InterruptedException e) { return null; }     // WRONG: flag cleared → shutdownNow() hangs forever
```

A long-running worker checks the flag *and* uses bounded waits, so it has two stop conditions:
```java
while (!Thread.currentThread().isInterrupted()) {
    String job;
    try { job = q.poll(1, TimeUnit.SECONDS); }               // never blocks forever
    catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
    if (job != null) handle(job);
}
```
`Thread.interrupted()` **clears** the flag; `isInterrupted()` does not. Reaching for the former by
accident silently eats the cancellation.

## Timeouts on every blocking wait
FAIL: any no-arg `Future.get()`, `lock.lock()`, `queue.take()`, or socket read on a request path.
*Why: an unbounded wait turns one slow dependency into thread exhaustion.*
```java
try { return f.get(3, TimeUnit.SECONDS); }
catch (TimeoutException e)     { f.cancel(true); throw e; }   // stop waiting AND stop the work
catch (ExecutionException e)   { throw new GatewayException("quote " + sym, e.getCause()); }
catch (InterruptedException e) { f.cancel(true); Thread.currentThread().interrupt();
                                 throw new CancellationException("interrupted: " + sym); }
```
Prefer `tryLock(timeout)` wherever a lock is held across I/O; give every HTTP/JDBC client both connect
and read timeouts — several default to infinite.

## Shared state
- **Immutability first.** A `record` whose components are copied defensively (`List.copyOf` in the
  compact constructor) needs no synchronization at all — the cheapest correct answer.
- `ConcurrentHashMap` over `synchronized(map)`, using its **atomic** ops (`computeIfAbsent`, `merge`,
  `putIfAbsent`). FAIL: `if (!map.containsKey(k)) map.put(k, v)` — check-then-act races even on a
  concurrent map. A `computeIfAbsent` mapping function must not touch the same map (it deadlocks).
- `volatile` gives visibility and ordering for **one field**, not atomicity — `volatile int n; n++`
  still races. Use `AtomicInteger`/`AtomicReference.updateAndGet`; `LongAdder` for contended counters.
- `synchronized` for short in-memory critical sections; `ReentrantLock` when you need `tryLock`/timed/
  interruptible acquisition, fairness, or multiple `Condition`s. On Java 24+ this is a **capability**
  choice: `synchronized` no longer pins virtual threads (JEP 491), so "use `ReentrantLock` to avoid
  pinning" is obsolete — pick `synchronized` for a plain short critical section and reach for
  `ReentrantLock` only when you need one of those extra capabilities.
- **Double-checked locking is a trap.** Prefer the holder idiom — `private static final class Lazy {
  static final Holder INSTANCE = new Holder(); }` — class init is JVM-guaranteed thread-safe and
  lock-free after. If you must DCL, the field is `volatile` or a reader sees a half-built object.
- Never publish `this` from a constructor (registering a listener, starting a thread) — another thread
  can observe the object before its final fields are initialized.

## ThreadLocal in pooled threads
A pooled *platform* thread outlives the request. A `ThreadLocal` set and not removed is read by the
*next* request on that thread (a tenant/auth leak) and pins its value — and in an app server its
classloader — forever. This is the classic "redeploy OOMs the container" leak, and exactly how MDC
context bleeds across requests.
```java
CURRENT.set(tenantId);
try { work.run(); }
finally { CURRENT.remove(); }   // MANDATORY. Not set(null): the entry itself must go.
```
For new request-scoped context, prefer `ScopedValue` (final in JDK 25) — see [Scoped values](#scoped-values--request-context-that-crosses-forks):
it can't leak because it unbinds at scope exit, and it propagates into structured-concurrency forks that
a `ThreadLocal` never reaches. Keep `ThreadLocal` where you truly can't rebind, and always pair `set`
with `remove()` in a `finally`.

## The leak catalogue
Each is a concrete FAIL:
- **Executor never shut down** — its non-daemon threads keep the JVM alive, each retaining its last
  task's stack and its ThreadLocals. Own it: try-with-resources or `@PreDestroy`/`close()`.
- **ThreadLocal without `remove()` in a `finally`** — see above.
- **Unbounded work queue** — bound it and pick a rejection policy, or you deferred the OOM, not avoided it.
- **Swallowed `InterruptedException`** — the task becomes uncancellable and shutdown hangs.
- **`Future` never observed** — the exception is captured and lost; the failure is silent.
- **Cancelled `ScheduledFuture` retained** until its scheduled time — `setRemoveOnCancelPolicy(true)`.
- **Cache/map that only grows** — size cap or TTL (Caffeine); "it's only per-user" is how heaps die.
- **Unreleased resource** — `Connection`/`InputStream` in try-with-resources, every lock in a `finally`.

Confirm with a heap dump or `jcmd <pid> Thread.print`, never by reasoning alone — and a race "fixed"
with `Thread.sleep` is not fixed.

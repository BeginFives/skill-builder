# Concurrency, cancellation & leak-freedom — Java

Load this when starting threads, executors, virtual threads or async pipelines — or when fixing a
hang, a leak, or a race. The governing rule: **every task has an owner that can await it and a stop
condition it honors.** If you can't name both, don't start it.

## Contents
- [The ownership triad](#the-ownership-triad)
- [ExecutorService lifecycle](#executorservice-lifecycle)
- [Virtual threads — and when they don't help](#virtual-threads--and-when-they-dont-help)
- [Structured concurrency — the modern fan-out](#structured-concurrency--the-modern-fan-out)
- [CompletableFuture done right](#completablefuture-done-right)
- [Interruption is the cancellation protocol](#interruption-is-the-cancellation-protocol)
- [Timeouts on every blocking wait](#timeouts-on-every-blocking-wait)
- [Shared state](#shared-state)
- [ThreadLocal in pooled threads](#threadlocal-in-pooled-threads)
- [The leak catalogue](#the-leak-catalogue)

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

## Virtual threads — and when they don't help
`Executors.newVirtualThreadPerTaskExecutor()` (Java 21, final) replaces the pool *for blocking I/O*: a
parked virtual thread costs no OS thread, so 10 000 concurrent calls are fine.

- **They do not speed up CPU-bound work** — parallelism is still bounded by carrier count; size a
  platform pool for compute.
- **Never pool virtual threads.** They are cheap and disposable; pooling reintroduces the queueing limit
  you were escaping and revives ThreadLocal retention. One per task.
- **Unbounded virtual threads still overwhelm the dependency.** Cap in-flight calls with a `Semaphore`
  at the client — that is the bulkhead, not the thread count.
- **Pinning (Java 21–23):** blocking inside `synchronized` pins the carrier; enough pinned carriers and
  the JVM stalls. Diagnose with `-Djdk.tracePinnedThreads=full` (21–23) or the `jdk.VirtualThreadPinned`
  JFR event. JEP 491 (JDK 24) removed it for `synchronized`; native (JNI) frames still pin. On a 21
  baseline, assume `synchronized` + blocking = pinned.
  ```java
  synchronized (lock) { return blockingGet(k); }                    // WRONG — carrier pinned
  lock.lock(); try { return blockingGet(k); } finally { lock.unlock(); }   // RIGHT — carrier released
  ```

## Structured concurrency — the modern fan-out
The default answer to "call N things and combine them": the scope *is* the owner, sibling failure
cancels the rest, and `close()` cannot return while a subtask is alive. **PREVIEW in Java 21–25** —
needs `--enable-preview` at compile *and* run, and the API is not frozen.
```java
try (var scope = new StructuredTaskScope.ShutdownOnFailure()) {     // Java 21–24 (JEP 453/462/480/499)
    Subtask<User> user = scope.fork(() -> fetchUser(id));           // fork returns Subtask<T>
    Subtask<List<Order>> orders = scope.fork(() -> fetchOrders(id));
    scope.joinUntil(Instant.now().plusSeconds(2));                  // deadline for the WHOLE fan-out
    scope.throwIfFailed(GatewayException::new);                     // first failure cancelled the sibling
    return new Page(user.get(), orders.get());                      // get() is legal only after join
}
```

**JDK 25 rewrote this API** (JEP 505) and the two forms do not cross-compile, so write the one matching
the target JDK — `ShutdownOnFailure`/`ShutdownOnSuccess` are gone, `join()` returns the result and
throws `FailedException`, and the deadline moves into config:
```java
try (var scope = StructuredTaskScope.open(Joiner.awaitAllSuccessfulOrThrow(),   // JDK 25 shape
                                          cf -> cf.withTimeout(Duration.ofSeconds(2)))) {
    Subtask<User> user = scope.fork(() -> fetchUser(id));
    Subtask<List<Order>> orders = scope.fork(() -> fetchOrders(id));
    scope.join();                                           // FailedException on the 1st failure
    return new Page(user.get(), orders.get());
}   // Joiner.anySuccessfulResultOrThrow() = first success wins (hedging); losers are cancelled.
```

With preview flags off the table, `invokeAll` on a try-with-resources executor (it has a timeout
overload and cancels the remainder) is the supported fallback — not hand-rolled latches.

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
- `synchronized` for short critical sections; `ReentrantLock` when you need `tryLock`, fairness, multiple
  conditions — or when a virtual thread may block inside it.
- **Double-checked locking is a trap.** Prefer the holder idiom — `private static final class Lazy {
  static final Holder INSTANCE = new Holder(); }` — class init is JVM-guaranteed thread-safe and
  lock-free after. If you must DCL, the field is `volatile` or a reader sees a half-built object.
- Never publish `this` from a constructor (registering a listener, starting a thread) — another thread
  can observe the object before its final fields are initialized.

## ThreadLocal in pooled threads
A pooled thread outlives the request. A `ThreadLocal` set and not removed is read by the *next* request
on that thread (a tenant/auth leak) and pins its value — and in an app server its classloader — forever.
This is the classic "redeploy OOMs the container" leak, and exactly how MDC context bleeds across requests.
```java
CURRENT.set(tenantId);
try { work.run(); }
finally { CURRENT.remove(); }   // MANDATORY. Not set(null): the entry itself must go.
```
`ScopedValue` (JEP 506 — **final** in JDK 25, preview in 21–24) is the better answer for request-scoped
data: immutable, auto-unbound at scope exit, inherited by structured subtasks.

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

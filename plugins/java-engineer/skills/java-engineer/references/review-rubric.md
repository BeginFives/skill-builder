# Java Production Rubric — the checkable "principal engineer" standard

> This is the crown-jewel content for the `java-engineer` skill. Every item is a concrete
> PASS/FAIL gate. A change ships only if it passes every **applicable** item. Sections 1–4
> (concurrency, leaks, exceptions, cancellation) and 10 (tooling gate) are **hard blockers** —
> failing any is not principal-grade regardless of features. Sections 5–9, 11–14 elevate "correct"
> to "production-grade and operable."
>
> Sourced from: the JDK javadoc (`java.util.concurrent`, `java.net.http`, `java.time`,
> `AutoCloseable`), JEP 444 (virtual threads) and JEP 453 (structured concurrency), *Effective
> Java* 3e, *Java Concurrency in Practice*, the Google Java Style Guide, the SLF4J/Logback
> manuals, OpenTelemetry-Java and Micrometer docs, the Spring Framework/Boot reference, HikariCP
> docs, OWASP Top 10 + Cheat Sheet Series, and the SpotBugs / find-sec-bugs / Error Prone /
> NullAway bug-pattern catalogs.

## Definition of Done — emit this block, filled in, with every code response

Copy this and mark each line **PASS**/**FAIL** (+ a ≤10-word reason on any FAIL). This emitted evidence
— not a silent "I reviewed it" — is what makes output dependable. §1–4 and §10 are hard blockers with
**no comment override** (a commented-out leak is still a leak).

```
[SELF] concurrency  — every executor/thread has an owner + shutdown path (or none started)  §1
[SELF] cancellation — timeout on every outbound call; interrupt rethrown or re-asserted     §4
[SELF] exceptions   — handled once, cause chained, none swallowed, no printStackTrace       §3
[SELF] leaks        — try-with-resources on every AutoCloseable; MDC/ThreadLocal cleared    §2
[SELF] log/obs      — SLF4J {} + MDC traceId, no secrets; recordException + setStatus(ERROR) §5–7
[TOOL] google-java-format -i (no diff) · mvn -q compile · mvn -q test                       §10
[TOOL] mvn org.owasp:dependency-check-maven:check (no actionable CVE)                       §10
```

**Legend used throughout this rubric:** `[TOOL]` = a named check catches it (the enforcement hooks run
these — you get them for free); `[SELF]` = **no tool can see it, you are the only gate.** Concentrate
your review effort on the `[SELF]` items — the `[TOOL]` items are already covered by the gate.

## 1. Concurrency & task lifecycle — HARD BLOCKER (no comment override)

- [ ] **OWNERSHIP** — FAIL if an `ExecutorService` is created without a shutdown path: try-with-resources (JDK 21+ `ExecutorService` is `AutoCloseable`), a `finally { shutdown(); }`, `@PreDestroy`, or a Spring `DisposableBean`. An executor with no shutdown keeps non-daemon threads alive and the JVM never exits.
- [ ] **NO RAW THREADS** — FAIL on `new Thread(...).start()` in application code. Work belongs to an executor or a `StructuredTaskScope` that someone can join.
- [ ] **NO STATIC/`init` SPAWNS** — FAIL if a thread or scheduled task is started from a static initializer, a constructor, or a field initializer. Background work is owned by a component with explicit `start()`/`close()`.
- [ ] **FUTURES ARE JOINED** — FAIL if `executor.submit(...)` returns a `Future` that is dropped: `submit` captures the exception *into the Future*, so an unread Future silently swallows every failure. Use `execute()` only when the task itself logs its own failures. `[TOOL: ErrorProne FutureReturnValueIgnored]`
- [ ] **BOUNDED POOLS** — FAIL on `Executors.newCachedThreadPool()` on a request path (unbounded platform threads) and on `newFixedThreadPool`'s unbounded `LinkedBlockingQueue`. Use `ThreadPoolExecutor` with a bounded queue **and** an explicit `RejectedExecutionHandler` — the rejection policy is the back-pressure design.
- [ ] **VIRTUAL THREADS** — FAIL if blocking-I/O fan-out on JDK 21+ uses a small platform pool where `Executors.newVirtualThreadPerTaskExecutor()` is correct; equally FAIL on *pooling* virtual threads (they are per-task and cheap — a pool re-adds the queueing you removed).
- [ ] **PINNING (version-aware)** — On the Java 25 baseline, `synchronized` no longer pins virtual threads (JEP 491, final in JDK 24), so it is *not* a pinning FAIL. FAIL only when (a) the target runtime is JDK 21–23, where `synchronized` around a blocking call pins the carrier — use `ReentrantLock` there; or (b) blocking happens inside a native/JNI frame, which still pins on every version. Prefer `ReentrantLock` for `tryLock`/timed/interruptible acquisition or multiple `Condition`s — not for pinning.
- [ ] **EXPLICIT EXECUTOR** — FAIL on `CompletableFuture.supplyAsync(fn)` / `.thenApplyAsync(fn)` with no executor argument: the default is the shared `ForkJoinPool.commonPool()` (size = cores−1), which must never see blocking work. Same FAIL for blocking inside `parallelStream()`.
- [ ] **NO DROPPED ASYNC FAILURES** — FAIL if a `CompletableFuture` chain has no terminal `exceptionally`/`handle`/`whenComplete`; an exception in a stage with no downstream consumer disappears entirely.
- [ ] **UNCAUGHT-EXCEPTION PATH** — FAIL if a spawned task's `Throwable` has nowhere to go: wrap the task body in try/catch that logs, or set a `Thread.UncaughtExceptionHandler` on the pool's `ThreadFactory`. Also FAIL if the `ThreadFactory` produces unnamed threads — `pool-3-thread-7` is useless in a thread dump.
- [ ] **CONSISTENT SYNCHRONIZATION** — FAIL if a mutable field is accessed under a lock on one path and without it on another, or published across threads without `volatile`/`final`/an atomic. `[TOOL: SpotBugs IS2_INCONSISTENT_SYNC, ErrorProne GuardedBy]`
- [ ] **CONCURRENT COLLECTIONS** — FAIL on a `HashMap`/`ArrayList` shared across threads; and FAIL on check-then-act against a `ConcurrentHashMap` (`containsKey` then `put`) — use `computeIfAbsent`/`merge`/`putIfAbsent`, which are the only atomic forms.
- [ ] **NO COMPOUND OPS ON `volatile`** — FAIL on `volatile int count; count++` — `volatile` gives visibility, not atomicity. Use `AtomicInteger`/`LongAdder`. `[TOOL: SpotBugs VO_VOLATILE_INCREMENT]`
- [ ] **SAFE LAZY INIT** — FAIL on double-checked locking without a `volatile` field; prefer the holder-class idiom or an `enum` singleton. `[TOOL: SpotBugs DC_DOUBLECHECK]`
- [ ] **PRIVATE LOCKS** — FAIL on `synchronized(this)`, `synchronized` on a public field, or locking a `String`/boxed value — any caller can then deadlock you. Lock a `private final Object`/`ReentrantLock`.
- [ ] **CORRECT WAITING** — FAIL on `wait()` outside a `while (condition)` loop (spurious wakeups), on `Thread.sleep` used as inter-thread coordination, and on any busy-wait without a `Condition`/`BlockingQueue`.
- [ ] **THREADLOCAL HYGIENE** — FAIL if a `ThreadLocal.set` on a pooled thread has no `remove()` in a `finally` — the next request on that thread inherits the previous one's state, and in a container it pins the classloader.
- [ ] **NO LEGACY PRIMITIVES** — FAIL on `Thread.stop/suspend/resume`, on `synchronized` `Vector`/`Hashtable` in new code, and on `Timer`/`TimerTask` (one uncaught exception kills the timer thread) — use `ScheduledExecutorService`.

## 2. Resource & memory leaks — HARD BLOCKER (no comment override)

- [ ] FAIL if any `AutoCloseable` — `InputStream`/`OutputStream`, `Reader`/`Writer`, `Connection`, `Statement`, `ResultSet`, `Socket`, JDK `HttpResponse` body stream, Kafka consumer, Netty `ByteBuf` — is opened outside try-with-resources. `[TOOL: SpotBugs OBL_UNSATISFIED_OBLIGATION, OS_OPEN_STREAM]`
- [ ] FAIL on a hand-rolled `finally { close(); }` where try-with-resources fits: a throw from `close()` in a `finally` *replaces* the real exception instead of being suppressed.
- [ ] FAIL on `Files.lines`, `Files.walk`, `Files.list`, `Files.find`, `DirectoryStream` used without try-with-resources — these Streams hold OS file descriptors and are the #1 "Too many open files" cause.
- [ ] FAIL on a `static` `Map`/`List`/`Set` cache with no eviction. A cache is `Caffeine`/Guava with `maximumSize` **and** `expireAfterWrite`, or a bounded `LinkedHashMap` with `removeEldestEntry` — otherwise it is a memory leak with a nice name.
- [ ] FAIL if a registered listener / subscription / `ScheduledFuture` / JMX bean / shutdown hook has no matching deregistration or `cancel()` on the teardown path.
- [ ] FAIL if a `scheduleAtFixedRate`/`scheduleWithFixedDelay` task body is not wrapped in try/catch(Throwable): a single escaped exception silently cancels **all** future executions, and nothing logs it.
- [ ] FAIL on `readAllBytes()` / `new String(stream.readAllBytes())` / full-buffering a request or response body of unbounded size — stream it, or cap it before reading (see §8, §14).
- [ ] FAIL on unbounded accumulation into a `StringBuilder`, `List`, or `byte[]` whose size is driven by input volume rather than a fixed bound.
- [ ] FAIL on a non-static inner class or a `this`-capturing lambda stored in a long-lived registry/cache — it pins the entire enclosing object graph. `[TOOL: SpotBugs SIC_INNER_SHOULD_BE_STATIC]`
- [ ] FAIL on `finalize()` (removed/deprecated, unpredictable, resurrects objects) — use `Cleaner` or, better, an explicit `close()`.
- [ ] FAIL on a per-request `HttpClient`, `RestTemplate`, `WebClient`, `ObjectMapper`, `SSLContext`, `DocumentBuilderFactory`, or `Pattern.compile` — each carries its own executor/pool/parser and leaks it. Build once, inject, reuse.
- [ ] FAIL on an unbounded `LinkedBlockingQueue`/`ConcurrentLinkedQueue` between producer and consumer (default capacity `Integer.MAX_VALUE`): give it a bound and state what happens when it is full.
- [ ] FAIL if a small extracted value keeps a large object alive (a field of a giant parsed DTO, one element of a huge array) — copy the value and drop the reference.
- [ ] FAIL if a heap-growth or OOM claim ships without a heap histogram / JFR / `-XX:+HeapDumpOnOutOfMemoryError` artifact backing it.

## 3. Exceptions — HARD BLOCKER (no comment override)

- [ ] FAIL on an empty or log-only-at-`debug` catch block. Swallowing is a decision that needs a written reason on the line. `[TOOL: SpotBugs DE_MIGHT_IGNORE, Checkstyle EmptyCatchBlock]`
- [ ] FAIL on `catch (InterruptedException e)` that neither rethrows nor calls `Thread.currentThread().interrupt()` — clearing the interrupt flag makes the task permanently uncancellable. `[TOOL: ErrorProne InterruptedExceptionSwallowed]`
- [ ] FAIL on `throw new RuntimeException(e.getMessage())` or `new XException(e.toString())` — that discards the cause and the entire stack. Always chain: `new XException("loading order id=" + id, e)`.
- [ ] FAIL on log-and-rethrow (`log.error(...); throw e;`) — one failure must produce one log entry, at the layer that decides what to do about it.
- [ ] FAIL on `e.printStackTrace()` anywhere in application code (writes to stderr, bypasses the log pipeline, loses MDC).
- [ ] FAIL on `catch (Throwable)` / `catch (Error)` anywhere but a process edge (top of a worker task, a `@ControllerAdvice`, `main`) — and even there `OutOfMemoryError`/`StackOverflowError` must be rethrown, not absorbed.
- [ ] FAIL on catching `NullPointerException`, `IndexOutOfBoundsException`, `NumberFormatException`, or `ClassCastException` as flow control instead of checking the precondition. `[TOOL: ErrorProne]`
- [ ] FAIL on exceptions as control flow on a hot path (parse-by-exception, "try the cache then catch NotFound") — fill-in-stack-trace is the cost.
- [ ] FAIL on `throws Exception` / `throws Throwable` in a public signature — declare the precise types the caller can act on.
- [ ] FAIL on a checked exception the caller cannot meaningfully recover from (*Effective Java* Item 71) — use an unchecked domain exception or a result/`Optional` return.
- [ ] FAIL if an exception message omits the identifying values needed to debug it (id, key, path, status code) — and equally FAIL if it embeds a secret, token, or PII.
- [ ] FAIL on `Optional.get()` without a preceding `isPresent()` — use `orElseThrow(...)` with a real message. `[TOOL: ErrorProne OptionalGetWithoutIsPresent, NullAway]`
- [ ] FAIL on returning `null` for a collection, array, or `Optional`; return an empty one. FAIL on `Optional` used as a field, parameter, or collection element — it is a return type.
- [ ] FAIL on `System.exit()` outside `main`/an explicit shutdown handler; a library that kills the JVM is not a library.
- [ ] FAIL if an internal exception crosses the API boundary carrying SQL text, stack frames, class names, or hostnames into a client response (see §8's `@ControllerAdvice` requirement).
- [ ] FAIL if try-with-resources suppressed exceptions are never surfaced — at minimum they reach the log via `getSuppressed()` on the diagnostic path.
- [ ] FAIL on `assert` used to validate runtime input — assertions are disabled by default. Use `Objects.requireNonNull` / `IllegalArgumentException` / Bean Validation.

## 4. Cancellation, deadlines & timeouts — HARD BLOCKER (no comment override)

- [ ] FAIL on `Future.get()` or `CompletableFuture.join()` with no timeout on a request path — that is an unbounded block on someone else's failure.
- [ ] FAIL on `java.net.http.HttpClient` built without `connectTimeout(...)` **and** requests built without `.timeout(...)` — the JDK client has **no default read timeout**; a hung peer hangs you forever.
- [ ] FAIL on `RestTemplate`, `WebClient`, OkHttp, or Apache HttpClient left at construction defaults (a bare `new RestTemplate()` has infinite connect *and* read timeouts).
- [ ] FAIL on a JDBC path with no `Statement.setQueryTimeout`, no driver socket timeout, and no pool `connectionTimeout` (see §13) — three separate clocks, all required.
- [ ] FAIL on `BlockingQueue.take()`/`put()` on a request path; use `poll(timeout, unit)` / `offer(timeout, unit)` and define the behavior when it expires.
- [ ] FAIL on `lock.lock()` where contention can stall a request indefinitely and `tryLock(timeout, unit)` with a fallback is the correct shape.
- [ ] FAIL if a long-running loop or compute never checks `Thread.currentThread().isInterrupted()` — `Future.cancel(true)` cannot stop code that never looks.
- [ ] FAIL if a task catches `InterruptedException` and keeps working: cancellation must actually terminate the work, not just be observed.
- [ ] FAIL on `ExecutorService.shutdown()` without `awaitTermination(...)` followed by `shutdownNow()` — shutdown that doesn't wait doesn't drain.
- [ ] FAIL on a retry loop where each attempt has its own timeout but the chain has no overall deadline budget — 3 retries × 30s is a 90s request (see §8).
- [ ] FAIL on a `CompletableFuture` stage that depends on a remote call and has no `orTimeout`/`completeOnTimeout`.
- [ ] FAIL on `@Async`, `@Scheduled`, or message-listener methods with no internal timeout — they are request paths without an HTTP client to bound them.
- [ ] FAIL if the timeout value is a magic number in the call site rather than a named, configurable setting (see §9).

## 5. Structured logging (SLF4J)

- [ ] FAIL on `System.out.println` / `System.err.println` / `printStackTrace` in application code. `[TOOL: Checkstyle RegexpSinglelineJava, SpotBugs]`
- [ ] FAIL on a concatenated log message (`log.info("user " + id + " failed")`) — use SLF4J placeholders `log.info("user lookup failed userId={}", id)` so the message stays a groupable constant and argument formatting stays lazy.
- [ ] FAIL if a `log.debug`/`trace` call on a hot path builds an expensive argument eagerly without `isDebugEnabled()` or a supplier/fluent form.
- [ ] FAIL on a logger that is not `private static final Logger log = LoggerFactory.getLogger(Foo.class)` — a non-static logger allocates per instance; the wrong class literal breaks per-package levels. `[TOOL: SpotBugs LOGGER_SHOULD_BE_NON_STATIC / Checkstyle]`
- [ ] FAIL on `log.error("failed: " + e)` or `log.error(msg, e.getMessage())` — the `Throwable` must be the **last** argument and unformatted: `log.error("charge failed orderId={}", id, e)`, or the stack trace is lost.
- [ ] FAIL if production output is not machine-parseable JSON (logstash-logback-encoder / ECS layout) — a human-readable-only log is not queryable at 3am.
- [ ] FAIL on any secret or PII in a log line: password, token, `Authorization` header, API key, PAN, full email, national ID. Types carrying secrets must override `toString()` to redact — and note that a `record`'s generated `toString()` prints every component.
- [ ] FAIL on per-request success logging at INFO on a high-QPS path without sampling; ERROR is reserved for actionable, page-worthy failures, WARN for degraded-but-handled.
- [ ] FAIL on logging inside a loop whose iteration count is user-controlled (log-volume DoS).
- [ ] FAIL on user-controlled content written into a log line without stripping CR/LF — log forging splits one entry into two attacker-authored ones. `[TOOL: find-sec-bugs CRLF_INJECTION_LOGS]`
- [ ] FAIL on two logging backends on the classpath (log4j *and* logback) or a missing bridge (`jul-to-slf4j`, `log4j-over-slf4j`, `jcl-over-slf4j`) — half the libraries then log nowhere.
- [ ] FAIL if log level is compiled in rather than configurable per package at runtime.

## 6. Correlation IDs / MDC

- [ ] FAIL if a request/correlation ID is not seeded at the entry edge — a `OncePerRequestFilter`, servlet `Filter`, or gRPC `ServerInterceptor` that reads `traceparent`/`X-Request-Id` and **generates one when absent**.
- [ ] FAIL on `MDC.put(...)` without `MDC.remove(...)`/`MDC.clear()` in a `finally`. On a pooled thread the previous request's ID leaks onto the next one — logs that lie are worse than logs that are missing.
- [ ] FAIL if MDC is not propagated across an executor boundary: a plain `executor.submit(task)` runs on a thread with an empty (or stale) MDC. Capture `MDC.getCopyOfContextMap()` and restore it in a decorating `ExecutorService` / Spring `TaskDecorator` / OTel `Context.taskWrapping(executor)`.
- [ ] FAIL if MDC is assumed to survive a `CompletableFuture` chain or a Reactor/WebFlux pipeline — reactive operators hop threads; use `contextWrite` + a `ContextSnapshot`/`MDCContextLifter`, not bare MDC.
- [ ] FAIL if the logback/log4j2 encoder does not emit the MDC keys (`%X{traceId}` in the pattern, or `includeMdcKeyName`/`includeMdc=true` in the JSON encoder) — an ID that is set but not printed is an ID that does not exist.
- [ ] FAIL if correlation IDs are hand-threaded as method parameters through business code instead of riding in MDC/`Context`.
- [ ] FAIL if the `traceId`/`spanId` in the logs are not the OTel span's own IDs (use the OTel logback appender/MDC instrumentation) — logs and traces must join on the same key.
- [ ] FAIL if the ID is not injected into outbound HTTP headers / gRPC metadata / message properties, so downstream services log the same trace.

## 7. Observability (OpenTelemetry + Micrometer)

- [ ] FAIL if the OTel SDK is not initialized at startup with a `service.name` resource and a **batch** span processor, and not closed on shutdown — an unflushed batch means the spans for the crash you care about are the ones you lose.
- [ ] FAIL on `tracer.spanBuilder(name).startSpan()` without `try (Scope s = span.makeCurrent()) { ... } finally { span.end(); }` — an unended span never exports and leaks its context.
- [ ] FAIL on an error path that calls `span.recordException(e)` but not `span.setStatus(StatusCode.ERROR, msg)` — `recordException` alone leaves the span **green** in every dashboard.
- [ ] FAIL if trace context is not carried into async work (`Context.current().wrap(runnable)` / `Context.taskWrapping(executor)`) — the child silently becomes a new root trace.
- [ ] FAIL if the W3C `traceparent` propagator is not registered and not extracted inbound / injected outbound at every HTTP, gRPC, and messaging boundary.
- [ ] FAIL if an endpoint or consumer lacks the RED trio: a request counter, an error counter (or an outcome tag), and a latency **histogram** with SLO buckets — an average latency is not an SLO.
- [ ] FAIL on unbounded metric tag cardinality: user id, order id, raw URI path, exception message, or free-form input as a tag. Use the templated route (`/orders/{id}`) and a bounded outcome enum.
- [ ] FAIL if a `Counter`/`Timer` is resolved from the registry per request instead of once at construction — the registry lookup and tag map allocation land on the hot path.
- [ ] FAIL if `Timer.record` wraps only the happy path — the failure path must be timed too (try/finally or `Timer.Sample.stop`), or your p99 excludes every timeout.
- [ ] FAIL if JVM metrics binders are not registered (heap/non-heap, GC pause, thread states, class loading, FD count) — a Java service without GC and thread metrics cannot be debugged in production.
- [ ] FAIL if application thread pools and connection pools are not instrumented (`ExecutorServiceMetrics`, Hikari metrics) — queue depth and pool saturation are the first signal of every latency incident.
- [ ] FAIL if liveness and readiness are the same check, or readiness reports UP while a required dependency is down (traffic gets routed into a broken instance).

## 8. I/O boundaries & servers

- [ ] FAIL on a server with no request/idle timeouts and no request-size caps: `server.tomcat.connection-timeout`, `server.tomcat.max-swallow-size`, `server.max-http-request-header-size`, `spring.servlet.multipart.max-file-size` / `max-request-size`. Defaults that allow a slow, huge request are a Slowloris/DoS vector.
- [ ] FAIL if graceful shutdown is not enabled (`server.shutdown=graceful` + `spring.lifecycle.timeout-per-shutdown-phase`) and application executors are not drained — SIGTERM otherwise kills in-flight requests mid-transaction.
- [ ] FAIL on per-call construction of `HttpClient`/`RestTemplate`/`WebClient`/`ObjectMapper` — build once as a bean, inject it (see §2).
- [ ] FAIL on `HttpURLConnection` / `new URL(...).openStream()` in new code; use `java.net.http.HttpClient` (or the framework client) with timeouts, redirect policy, and a bounded body handler.
- [ ] FAIL on connection-pool defaults left untouched for Apache HttpClient (2 per route / 20 total) or Reactor Netty — the pool ceiling silently serializes your traffic under load.
- [ ] FAIL on `new String(bytes)`, `FileReader`, `FileWriter`, `PrintWriter(file)`, or `String.getBytes()` with no explicit charset — the platform default differs between your laptop and the container. `[TOOL: ErrorProne DefaultCharset]`
- [ ] FAIL on `File` + string path concatenation instead of `Path`/`Files`; FAIL on `File.createTempFile` for sensitive data without explicit owner-only permissions.
- [ ] FAIL if the filter/interceptor chain is not ordered: correlation/MDC → tracing → metrics → auth → body-size limit → handler, with a `@ControllerAdvice`/`ProblemDetail` handler mapping exceptions to sanitized responses with correct status codes.
- [ ] FAIL if stack traces reach clients (`server.error.include-stacktrace=always`, `include-message=always` on a public API).
- [ ] FAIL on a blocking call (`block()`, JDBC, `Thread.sleep`, synchronous file I/O) inside a WebFlux chain running on the event loop — one blocked event-loop thread stalls thousands of requests.
- [ ] FAIL on gRPC without per-call deadlines, without recovery/logging/tracing `ServerInterceptor`s, or without `shutdown()` + `awaitTermination()`.
- [ ] FAIL on a queue/Kafka consumer with unbounded prefetch/concurrency, no DLQ or poison-pill path, and no idempotent handling of redelivery (at-least-once delivery is a guarantee that redelivery *will* happen).
- [ ] FAIL if a retry targets a non-idempotent write without an idempotency key, retries a 4xx/validation error, or uses a fixed `Thread.sleep` backoff — retries are capped exponential **with jitter**, honor `Retry-After`, and stop at the caller's deadline.
- [ ] FAIL if a critical dependency has no fallback/circuit-breaker path where degraded service is better than a cascading failure.

## 9. Configuration & secrets

- [ ] FAIL on `System.getenv`/`System.getProperty` calls scattered through business code instead of one typed binding (`@ConfigurationProperties` + `@Validated`, or a record built and validated in `main`).
- [ ] FAIL if a required config value has a silent default and only fails on the first request that needs it — validate the whole config at startup and refuse to boot (fail fast, not fail late).
- [ ] FAIL on any credential, token, keystore password, or connection string with embedded password committed in `application.yml`/`.properties`/source/tests — read from env or a secret manager.
- [ ] FAIL if a secret can reach a log, an exception message, a `record`/Lombok `@Data` `toString()`, or an unsecured actuator `/env` / `/configprops` endpoint. Actuator must be restricted and its values sanitized.
- [ ] FAIL on mutable `public static` configuration or a global singleton holder — configuration is injected, so tests can vary it.
- [ ] FAIL on hard-coded timeouts, pool sizes, batch sizes, and retry counts — name them and make them configurable (they are the knobs you will need mid-incident).
- [ ] FAIL on environment branching in code (`if ("prod".equals(env))`) rather than profiles/config values.
- [ ] FAIL if `Locale`, `TimeZone`, or charset come from the platform default — pin `Locale.ROOT`, `ZoneOffset.UTC`, `StandardCharsets.UTF_8` explicitly; store and compute in UTC (`Instant`), format at the edge.
- [ ] FAIL if a feature flag or kill switch has no default-safe value when the flag source is unreachable.

## 10. Tooling gate — HARD BLOCKER (all `[TOOL]`; the hooks run these, no comment override)

This is the layer tools own, not where the skill's value lives — see the `[SELF]` sections above for that.

- [ ] `google-java-format -i` leaves **no diff** (the PostToolUse hook runs it on every edited `.java` file; hand-formatting must survive it unchanged).
- [ ] `mvn -q compile` is clean — zero errors and no new warnings. Add `-Xlint:all` and treat `unchecked`, `rawtypes`, `deprecation`, and `this-escape` as failures; `-Werror` on new modules.
- [ ] `mvn -q test` passes — with **no** test newly `@Disabled`/`@Ignore`d, no `Thread.sleep` inserted to stabilize a race, and no assertion weakened to make it green.
- [ ] `mvn org.owasp:dependency-check-maven:check` reports nothing actionable at or above the configured CVSS threshold. Every suppression carries a written justification and a review date — a blanket suppression file is a FAIL.
- [ ] Dependencies are hygienic: no version ranges, transitive versions pinned via `dependencyManagement`/a BOM, and `mvn dependency:analyze` shows no used-undeclared or declared-unused entries.
- [ ] New behavior ships with a test that fails before the change and passes after (RED→GREEN evidence in the response), and concurrency changes ship with a test that actually exercises contention.

**Recommended additions — enable these and the gate covers far more of §1–3, §11, §14:**

| Add | Catches (turning `[SELF]` into `[TOOL]`) |
|---|---|
| **SpotBugs** (+ **find-sec-bugs**) | unclosed resources (`OBL_*`, `OS_OPEN_STREAM`), inconsistent sync (`IS2_*`), `volatile` increments, ignored exceptions, SQL injection, XXE, weak crypto, predictable random, path traversal |
| **Error Prone** | dropped `Future`s, swallowed `InterruptedException`, `Optional.get`, `ReferenceEquality`, `DefaultCharset`, `JavaUtilDate`, `EqualsHashCode`, `StringConcatenationInLoop` |
| **NullAway** | nullability contract violations at compile time (the NPE class of bug, gone) |
| **Checkstyle** (google_checks) | empty catch blocks, `printStackTrace`, import order, naming, missing javadoc on public API |
| **JaCoCo** | coverage floor on changed lines, so §10's test line is measurable |

## 11. Types, layout & dependency injection

- [ ] FAIL on a `util`/`common`/`helpers`/`misc` package — name packages by domain capability, not by grammar.
- [ ] FAIL on field or setter injection (`@Autowired` on a field, `@Inject` on a setter). Constructor injection with `final` fields only: it makes dependencies explicit, the object immutable, and the class testable without a container.
- [ ] FAIL when a collaborator is `new`ed inside business logic (a client, clock, or random source) — inject it, including `Clock` so time is testable.
- [ ] FAIL on a mutable JavaBean DTO where a `record` fits; and FAIL on a `record` whose component is a mutable array/collection without a defensive copy in the compact constructor (a `record` is shallowly immutable only).
- [ ] FAIL on returning an internal collection directly — return `List.copyOf(...)`/`Collections.unmodifiableList(...)`.
- [ ] FAIL on a type used as a `Map` key or `Set` element without a correct `equals`/`hashCode` pair (or a `record`). `[TOOL: ErrorProne EqualsHashCode, SpotBugs HE_*]`
- [ ] FAIL when a closed set of variants is modeled with an enum + nullable fields or boolean flags where a **sealed interface + records + exhaustive `switch`** makes illegal states unrepresentable.
- [ ] FAIL on raw types and unchecked casts without a `@SuppressWarnings("unchecked")` carrying a one-line reason. `[TOOL: javac -Xlint:unchecked,rawtypes]`
- [ ] FAIL on `double`/`float` for money — `BigDecimal` with an explicit scale and `RoundingMode`, or minor units in `long`. `[TOOL: SpotBugs FL_MATH_USING_FLOAT_PRECISION]`
- [ ] FAIL on `java.util.Date`, `Calendar`, or `SimpleDateFormat` (mutable and not thread-safe — a static `SimpleDateFormat` is a live data race). Use `java.time`. `[TOOL: ErrorProne JavaUtilDate]`
- [ ] FAIL on `==` comparison of `String`s or boxed numbers. `[TOOL: ErrorProne ReferenceEquality, SpotBugs ES_COMPARING_STRINGS_WITH_EQ]`
- [ ] FAIL on a boolean flag parameter that selects behavior (`process(order, true)`) — two methods or an enum.
- [ ] FAIL on a package cycle, on internal implementation types exposed across a module boundary (keep them package-private or unexported in `module-info`), and on a class that is public only because it was easier.
- [ ] FAIL on Lombok `@Data`/`@Builder` on an entity or secret-carrying type (generated `toString`/`equals` leak fields and break JPA identity).
- [ ] FAIL if a nullable parameter or return value in a NullAway-enabled module is unannotated, or if a public entry point does not `Objects.requireNonNull` its required arguments.

## 12. Performance (measure first)

- [ ] FAIL on any performance claim not backed by a JMH benchmark or an async-profiler/JFR profile — "this should be faster" is not a measurement.
- [ ] FAIL on a JMH benchmark without `@Warmup`, `@Fork`, and a `Blackhole`/returned result: JIT dead-code elimination and constant folding will happily report a nanosecond for code that never ran. A `System.nanoTime()` loop is not a benchmark.
- [ ] FAIL on `String` concatenation inside a loop — use a pre-sized `StringBuilder`. `[TOOL: ErrorProne StringConcatenationInLoop, SpotBugs SBSC_USE_STRINGBUFFER_CONCATENATION]`
- [ ] FAIL on `parallelStream()` without a measurement showing the work is CPU-bound, cheaply splittable, and free of blocking or shared mutable state — it borrows the shared common ForkJoinPool from the whole JVM.
- [ ] FAIL on an N+1 pattern: a remote call, cache lookup, or query executed per element inside a loop/stream. Batch it.
- [ ] FAIL on boxing in a hot loop (`Stream<Integer>` where `IntStream` fits, `Map<Long, ...>` autoboxing per lookup) and on collecting a large stream to a `List` only to iterate it once.
- [ ] FAIL on `new ArrayList<>()`/`new HashMap<>()` when the final size is known — pre-size it and skip the rehash churn.
- [ ] FAIL on `Pattern.compile`, `DateTimeFormatter` construction, or `ObjectMapper` creation inside a per-request method — hoist to `static final`.
- [ ] FAIL on `String.format`/`MessageFormat` on a hot path where concatenation or a `StringBuilder` suffices.
- [ ] FAIL on `synchronized` around a hot shared counter where `LongAdder`/`AtomicLong` fits, and on lock scope wider than the state it protects.
- [ ] FAIL on reflection, dynamic proxies, or annotation scanning on a per-request path where a cached handle or compile-time binding works.
- [ ] FAIL if a hot-path change ships without a before/after number in the response, or if GC/heap flags are tuned without a GC log to justify them.
- [ ] FAIL on premature optimization of a cold path at the cost of readability — the rubric demands evidence in *both* directions.

## 13. Data stores (JDBC / JPA)

- [ ] FAIL on SQL, JPQL, or HQL assembled by string concatenation with user input — bind via `PreparedStatement` parameters or named parameters, always. `[TOOL: find-sec-bugs SQL_INJECTION_JDBC/JPA/SPRING]`
- [ ] FAIL on an unconfigured connection pool. HikariCP must set `maximumPoolSize` (sized against the DB's real connection ceiling **across all replicas**), `connectionTimeout`, `maxLifetime` (below the DB/proxy idle kill), `idleTimeout`, `validationTimeout`, and `leakDetectionThreshold`.
- [ ] FAIL on `Connection`, `Statement`, `PreparedStatement`, or `ResultSet` outside try-with-resources. `[TOOL: SpotBugs OBL_UNSATISFIED_OBLIGATION]`
- [ ] FAIL on `@Transactional` on a private, package-private, or self-invoked method — the Spring proxy never applies it and you silently run without a transaction.
- [ ] FAIL on any remote I/O — an HTTP call, a queue publish, an S3 put — inside an open transaction: it pins a pooled DB connection for the duration of somebody else's latency.
- [ ] FAIL if a transaction expects rollback on a **checked** exception without `@Transactional(rollbackFor = ...)` — Spring rolls back on unchecked only; a caught-and-logged exception inside the transaction rolls back nothing.
- [ ] FAIL on `FetchType.EAGER` associations, on `spring.jpa.open-in-view=true` in production (it holds a connection for the entire HTTP response render), and on lazy access outside the session — use fetch joins or an `EntityGraph`.
- [ ] FAIL on an N+1 select from iterating a `@OneToMany`, and on `findAll()` against a table that grows — every list endpoint is paginated with a bounded max page size.
- [ ] FAIL on a large result set read without `setFetchSize`/streaming — the driver may materialize every row in heap.
- [ ] FAIL on a schema change outside a versioned Flyway/Liquibase migration, or on a migration that isn't backward compatible with the currently deployed code (expand → migrate → contract, never a destructive single step).
- [ ] FAIL on a nullable column mapped to a primitive (`long`/`boolean`), and on `double`/`float` for a money column.
- [ ] FAIL on a read-modify-write with no transaction, no `@Version` optimistic lock, and no conditional update — that is silent last-write-wins data loss under concurrency.
- [ ] FAIL on a new query against a large table shipped without an `EXPLAIN`/index plan, and on a `SELECT *` where the columns are known.
- [ ] FAIL if a batch/bulk write is issued row-by-row where `addBatch`/`saveAll` with a bounded batch size is available, or if a batch has no chunking bound.

## 14. Security & input validation (`[SELF]`; enable find-sec-bugs for `[TOOL]` coverage)

- [ ] FAIL on a request DTO reaching business logic without validation — `@Valid` plus Bean Validation constraints, with explicit **length/size/range bounds** on every string, collection, and number before it is used to allocate (`new byte[n]`), loop, or query.
- [ ] FAIL on binding a request body directly onto a JPA entity or domain object (mass assignment) — bind to a DTO and map explicitly.
- [ ] FAIL on deserializing untrusted data with `ObjectInputStream`, or with Jackson polymorphic typing enabled (`enableDefaultTyping`/unbounded `@JsonTypeInfo`) — both are remote code execution. `[TOOL: find-sec-bugs OBJECT_DESERIALIZATION]`
- [ ] FAIL on XML/YAML parsing without hardening: `disallow-doctype-decl`, external entities off, `FEATURE_SECURE_PROCESSING`, SnakeYAML `SafeConstructor`. `[TOOL: find-sec-bugs XXE_*]`
- [ ] FAIL on `Runtime.exec(String)` or any shell-string interpolation — `ProcessBuilder` with an argument **list** and no `sh -c`. `[TOOL: find-sec-bugs COMMAND_INJECTION]`
- [ ] FAIL on a user-supplied path segment used without `base.resolve(name).normalize().startsWith(base)` confinement (and rejection of absolute paths and `..`). `[TOOL: find-sec-bugs PATH_TRAVERSAL_IN]`
- [ ] FAIL on fetching a user-supplied URL without a scheme+host allowlist and rejection of loopback/link-local/private/metadata addresses, with redirects disabled or re-validated after each hop (SSRF, cloud metadata theft).
- [ ] FAIL on `Math.random()` or `new Random()` for tokens, session IDs, salts, or nonces — `SecureRandom`. `[TOOL: find-sec-bugs PREDICTABLE_RANDOM]`
- [ ] FAIL on MD5/SHA-1 for a security purpose, ECB mode, a hard-coded key or IV, a reused nonce, or a password stored with anything but bcrypt/scrypt/Argon2/PBKDF2 at a current work factor. `[TOOL: find-sec-bugs WEAK_MESSAGE_DIGEST, ECB_MODE, HARD_CODE_KEY]`
- [ ] FAIL on a `TrustManager` that accepts everything or a `HostnameVerifier` returning `true` — including in "just for the test environment" code that ships. `[TOOL: find-sec-bugs WEAK_HOSTNAME_VERIFIER]`
- [ ] FAIL on comparing a secret, token, or HMAC with `String.equals` — use `MessageDigest.isEqual` (constant time).
- [ ] FAIL on holding a password or key in a `String` for longer than the call — use `char[]`/`byte[]` and clear it.
- [ ] FAIL if authorization is enforced only at the controller/UI and not at the service or query layer, and FAIL on any id taken from the request and used without an ownership check (IDOR).
- [ ] FAIL on permissive CORS (`@CrossOrigin("*")` with credentials) and on CSRF disabled for cookie-authenticated state-changing endpoints.
- [ ] FAIL on user input echoed into an HTML/JS response without contextual output encoding, or into a header without CR/LF stripping.
- [ ] FAIL on a dependency with a known, reachable CVE left unsuppressed and unjustified. `[TOOL: dependency-check]`

## 15. Design — principles & patterns (`[SELF]`; no tool catches these)
See `design-principles.md` and `design-patterns.md`. Scope discipline applies:
flag genuine design defects, but a minimal correct diff is never a deficiency and unrequested
abstraction is a regression, not a virtue.
- [ ] **ILLEGAL STATES** — FAIL if an invariant is enforced by a runtime check that a type could have made unrepresentable (a validating `record` canonical constructor, a `sealed` type, an `enum`, or a typed wrapper over a naked `String`/`int`).
- [ ] **EXHAUSTIVENESS** — FAIL on an `instanceof`/`switch` chain over the subtypes of what should be a `sealed` type: make it sealed and let an exhaustive `switch` force every site to handle a new variant at compile time.
- [ ] **PATTERN vs FEATURE** — FAIL on a GoF pattern that a language feature already subsumes: a Strategy class hierarchy where a functional interface + lambda suffices, a hand-rolled Singleton where an `enum` (or DI) is correct, a Visitor where a sealed type + pattern-matching `switch` is clearer, `java.util.Observable` (removed) instead of listeners / `Flow`.
- [ ] **COMPOSITION** — FAIL on inheritance used purely for code reuse where delegation/composition is the right relationship (no genuine is-a, or a subclass that overrides to *remove* behavior — an LSP break).
- [ ] **DEPEND ON ABSTRACTIONS** — FAIL if a class news-up its own collaborators instead of accepting them (constructor injection), or if an interface has a single implementation created reflexively — that is speculative abstraction, not DIP.
- [ ] **COHESION / SURFACE** — FAIL on a class with several unrelated reasons to change (SRP), and on public members that could be package-private — the smallest surface that works is the correct one.

---

### How the skill uses this rubric

1. **Author** code with resource lifetime, timeouts, and exception chaining designed in **by default** — not retrofitted after review.
2. **Self-run** every applicable checkbox before returning; for any FAIL, fix it or add a one-line comment naming the tradeoff. §1–4 and §10 have **no comment override**.
3. **Emit** the Definition-of-Done block above, filled in, as the evidence that the review happened.
4. The **hooks** (not the model's memory) enforce §10 deterministically; the **rubric** makes §1–9 and §11–14 checkable by the model and by the reviewer subagent.

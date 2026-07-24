# Logging, correlation (MDC) & observability — Java

Load this when adding logs, correlation/trace IDs, spans, or metrics. Governing rule: **the request
id and trace id are seeded once at the edge into MDC, carried explicitly across every thread hop, and
cleared in a `finally` — never passed as a parameter, never left behind on a pooled thread.**

## Contents
- [SLF4J: API only, parameterized, levels](#slf4j-api-only-parameterized-levels)
- [MDC: seed once at the edge](#mdc-seed-once-at-the-edge)
- [MDC across thread boundaries — the pitfall](#mdc-across-thread-boundaries--the-pitfall)
- [Structured JSON output](#structured-json-output)
- [Secrets & PII](#secrets--pii)
- [Tracing with OpenTelemetry](#tracing-with-opentelemetry)
- [Metrics: RED with Micrometer](#metrics-red-with-micrometer)

## SLF4J: API only, parameterized, levels
Compile against `org.slf4j:slf4j-api` only; the binding (`logback-classic`, `log4j-slf4j2-impl`) goes
in the **application's** runtime scope. FAIL if a library module depends on an implementation — it
collides with the app's choice ("multiple bindings", or a silent no-op logger).
```java
// static final = one lookup per class; lookupClass() still names the right logger after copy-paste
private static final Logger log = LoggerFactory.getLogger(MethodHandles.lookup().lookupClass());
log.info("order placed user={} order={} amount_cents={}", userId, order.id(), order.amountCents());
log.error("charge failed order={}", orderId, e);   // trailing Throwable => stack trace; NO {} for it
```
- **WRONG** `log.debug("payload=" + payload)` — concatenation and `toString()` run *before* the call,
  so you pay it even with DEBUG off, and the result is one opaque string no backend can index.
- **WRONG** `log.error("failed: {}", e.getMessage())` — discards the stack trace, the only part that
  says where it broke. Pass `e` as the final argument instead.
- Guard with `if (log.isDebugEnabled())` only when *building the arguments* is expensive
  (serialization, an extra query); guarding a cheap call is noise.

For real fields rather than a sentence, use the SLF4J 2.x fluent API — JSON encoders emit these as
top-level keys (`"order_id":"o-42","amount_cents":1999`):
```java
log.atInfo().setMessage("order placed").setCause(e)  // setCause optional
    .addKeyValue("order_id", order.id()).addKeyValue("amount_cents", order.amountCents()).log();
```

| Level | Use for | FAIL if |
|---|---|---|
| ERROR | actionable failure — the request died or someone gets paged | used for expected 4xx / validation |
| WARN | recovered oddity: retry succeeded, fallback taken, quota near | used as "ERROR but I'm not sure" |
| INFO | coarse business milestones, O(1) per request | it scales with input size |
| DEBUG/TRACE | developer detail, off in prod | it dumps a payload containing PII |

**Handle once.** `catch (X e) { log.error(...); throw e; }` at three layers yields three ERRORs for
one failure and triples alert volume. Log where you *stop* it; propagate elsewhere (see `errors.md`).

## MDC: seed once at the edge
MDC is the per-thread key/value map every appender can read. Seed it in one filter and every downstream
line is correlated with **zero per-call plumbing**.
```java
@Order(Ordered.HIGHEST_PRECEDENCE) // ahead of security/error handling, or those logs lose the id
public final class CorrelationFilter extends OncePerRequestFilter { // a plain Filter is identical
  private static final String HEADER = "X-Request-Id";
  @Override protected void doFilterInternal(
      HttpServletRequest req, HttpServletResponse res, FilterChain chain)
      throws ServletException, IOException {
    String in = req.getHeader(HEADER); // untrusted: bound it or drop it
    String id = (in == null || in.isBlank() || in.length() > 64) ? UUID.randomUUID().toString() : in;
    res.setHeader(HEADER, id);
    MDC.put("request_id", id);
    SpanContext sc = Span.current().getSpanContext();
    if (sc.isValid()) MDC.put("trace_id", sc.getTraceId()); // the OTel agent injects this for you
    try {
      chain.doFilter(req, res);
    } finally {
      MDC.clear(); // NOT optional: this thread goes straight back to the pool
    }
  }
}
```
Reuse the inbound id, and inject it on outbound calls, so both services' logs join up. For additions
*inside* a request use try-with-resources so the key cannot outlive its block:
`try (MDC.MDCCloseable ignored = MDC.putCloseable("order_id", order.id())) { … }`. FAIL if MDC is
written without a matching `remove`/`clear`/`putCloseable` on **every** path — that leak is invisible
in tests (fresh threads) and corrupts prod (reused threads).

## MDC across thread boundaries — the pitfall
MDC is a `ThreadLocal`; Logback's adapter is deliberately *non*-inheritable (log4j2 inherits only
with `log4j2.isThreadContextMapInheritable`), and inheritance would not save you anyway — **a pool
thread is not a child thread**, it was created long before your request. Every hand-off drops it:
```java
pool.submit(() -> log.info("charged"));                              // -> no request_id
CompletableFuture.supplyAsync(this::charge).thenAccept(this::audit); // -> commonPool, no request_id
Thread.ofVirtual().start(() -> log.info("v"));                       // -> new thread, no request_id
```
Worse than empty: a worker that sets MDC and never clears it hands its stale `request_id` to the
**next** request on that thread — confidently wrong correlation, which nobody notices. Carry it
explicitly, once, at the executor:
```java
public final class MdcContext {
  /** Captures the SUBMITTING thread's MDC now; installs it around the task on the worker. */
  public static Runnable wrap(Runnable task) {
    Map<String, String> captured = MDC.getCopyOfContextMap(); // null when empty
    return () -> {
      Map<String, String> previous = MDC.getCopyOfContextMap();
      apply(captured);
      try { task.run(); }
      finally { apply(previous); } // restore, not clear - the worker may be running nested work
    };
  }
  /** One wrapper carrying BOTH the MDC and the OpenTelemetry context. */
  public static Executor propagating(Executor delegate) {
    Executor withSpan = Context.taskWrapping(delegate); // io.opentelemetry.context.Context
    return command -> withSpan.execute(wrap(command));
  }
  private static void apply(Map<String, String> m) { if (m == null) MDC.clear(); else MDC.setContextMap(m); }
}
```
Hand call sites *only* the wrapped executor — including `supplyAsync(this::charge, tracked)` and
`Executors.newVirtualThreadPerTaskExecutor()`, whose threads start with an empty MDC like any other.
In Spring, `executor.setTaskDecorator(MdcContext::wrap)` covers `@Async` and that executor in one line.
Two notes: the OTel agent instruments executors, so `trace_id` survives a hop even unwrapped — **your
business MDC keys do not**; and `StructuredTaskScope` forks are new threads too, so a `ScopedValue`
(final in **Java 25**) request context must be copied into MDC inside the fork.

## Structured JSON output
Emit JSON in prod so MDC keys become queryable fields (`logstash-logback-encoder`, or log4j2
`JsonTemplateLayout`):
```xml
<encoder class="net.logstash.logback.encoder.LogstashEncoder">
  <includeMdcKeyName>request_id</includeMdcKeyName>  <!-- allowlist; otherwise EVERY MDC key ships -->
  <includeMdcKeyName>trace_id</includeMdcKeyName>
  <customFields>{"service":"orders","env":"prod"}</customFields>
</encoder>
```
Dev pattern: `%d{HH:mm:ss.SSS} %-5level [%thread] [%X{request_id:-none}] %logger{20} - %msg%n`.
`AsyncAppender` is MDC-safe (each event snapshots the map at construction) but `includeCallerData`
defaults to `false`, so `%class`/`%line` come out wrong. Configure in XML, and keep levels changeable
at runtime (`<configuration scan="true">`, Actuator `/loggers`).

## Secrets & PII
Never log credentials, tokens, auth headers, card/account numbers, or raw request bodies — and never
put them in MDC either, since the encoder ships MDC wholesale. The Java-specific trap: **`record` (and
Lombok `@Data`) generate a `toString()` printing every component**, so one `log.debug("req={}", req)`
leaks the password. Fix it by type, once, so no call site can get it wrong:
```java
record Secret(String value) { @Override public String toString() { return "REDACTED"; } }
record LoginRequest(String user, Secret password) {}
// LoginRequest[user=u, password=REDACTED]     vs    LoginRequestLeaky[user=u, password=hunter2]
```

## Tracing with OpenTelemetry
Run the agent (`-javaagent:opentelemetry-javaagent.jar`) first: it covers servlet, JDBC, HTTP clients
and Kafka, and does W3C `traceparent` extract/inject for you. FAIL if you hand-roll a span around
something it already instruments — duplicate spans. Write spans for business units of work only.
```java
private static final Tracer TRACER = GlobalOpenTelemetry.getTracer("com.acme.orders");
private static final AttributeKey<Long> LINE_COUNT = AttributeKey.longKey("order.line_count");
Receipt place(Order order) {
  Span span = TRACER.spanBuilder("Orders.place").setSpanKind(SpanKind.INTERNAL)
      .setAttribute(LINE_COUNT, (long) order.lines().size()).startSpan();
  try (Scope scope = span.makeCurrent()) {   // parents everything downstream, on THIS thread only
    return charge(order);
  } catch (RuntimeException e) {
    span.recordException(e);
    span.setStatus(StatusCode.ERROR, e.getClass().getSimpleName()); // recordException alone does NOT fail the span
    throw e;
  } finally {
    span.end();                              // in finally, always
  }
}
```
- FAIL: `span.end()` outside `finally` — the throw path never exports the trace you most needed.
- FAIL: `makeCurrent()` without try-with-resources — the `Scope` never closes, so every later span on
  that pooled thread is parented to a dead one. The current span does not cross threads either: same
  `Context.taskWrapping` wrapper as MDC.
- Span *names* are low-cardinality (`Orders.place`, never `Orders.place o-42`); ids go in attributes.

## Metrics: RED with Micrometer
RED per endpoint and per dependency: **R**ate, **E**rrors, **D**uration. Spring Boot Actuator already
publishes `http.server.requests` — don't reimplement it; add business meters only.
```java
private final MeterRegistry registry;
private final Timer ok, failed;
private final Queue<?> pending; // STRONG ref: the registry holds only a WEAK one to a gauged object
OrderMetrics(MeterRegistry registry, Queue<?> pending) { // every meter registered ONCE, right here
  this.registry = registry;
  this.pending = pending;
  this.ok = timer(registry, "success");
  this.failed = timer(registry, "failure");
  Gauge.builder("orders.pending", pending, Queue::size).register(registry);
}
static Timer timer(MeterRegistry r, String outcome) {
  return Timer.builder("orders.place").publishPercentileHistogram() // server-side buckets
      .tag("outcome", outcome).register(r);
}
<T> T time(Supplier<T> op) {
  Timer.Sample sample = Timer.start(registry);
  boolean success = false;
  try { T r = op.get(); success = true; return r; }
  finally { sample.stop(success ? ok : failed); } // pre-resolved timers: no lookup on the hot path
}
```
- **Cardinality is the outage.** Each distinct tag combination is a new time series. FAIL if a tag
  value comes from user input, a user/order/request id, an exception message, or a raw URI path — tag
  the route *template* (`/orders/{id}`), map unknown values to `"other"`, and add a backstop **before
  any meter is registered** (Micrometer warns and ignores filters added later):
  `registry.config().meterFilter(MeterFilter.maximumAllowableTags("orders.place","outcome",20,MeterFilter.deny()));`
- A gauged object referenced only by the lambda gets collected and the gauge reports `NaN` forever
  (measured: `2.0` before GC, `NaN` after) — hold it in a field, as above.
- Prefer `publishPercentileHistogram()` over `publishPercentiles()`: per-instance quantiles cannot be
  averaged across a fleet, so a p99 built from them is a fiction.

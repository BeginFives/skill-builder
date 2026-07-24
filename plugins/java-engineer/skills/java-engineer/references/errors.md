# Error handling — Java

Load this when designing exception types, writing a `catch`, or deciding log-vs-propagate. Governing
rule: **every failure is handled exactly once, by the layer that can actually decide something, with its
cause chained the whole way up.**

## Contents
- [Checked vs unchecked](#checked-vs-unchecked)
- [Never swallow](#never-swallow)
- [Handle exactly once](#handle-exactly-once)
- [Never lose the cause](#never-lose-the-cause)
- [Custom exception design](#custom-exception-design)
- [Never catch Throwable or Error](#never-catch-throwable-or-error)
- [Exceptions are not control flow](#exceptions-are-not-control-flow)
- [try-with-resources and finally](#try-with-resources-and-finally)
- [Optional discipline](#optional-discipline)
- [Null-safety at boundaries](#null-safety-at-boundaries)
- [The process edge](#the-process-edge)

## Checked vs unchecked
**Unchecked by default.** Checked *only* when the immediate caller has a realistic recovery — retry another
provider, fall back to cache, re-prompt. If the next five frames can only rethrow, checked buys nothing.

**FAIL if a signature declares `throws Exception`** (outside `main` and tests, where it's fine): it forces
callers into `catch (Exception e)`, which then also eats their own NPEs, and it erases which failures are
possible. Checked types also don't cross lambdas, `Stream`, or `CompletableFuture`. Declare the narrowest
set, or translate at the component boundary — one exception type per component:
```java
Optional<User> find(UserId id) {              // absence is normal -> Optional, not an exception
    try (var conn = ds.getConnection(); var ps = conn.prepareStatement(SELECT_BY_ID)) {
        ps.setString(1, id.value());
        try (var rs = ps.executeQuery()) { return rs.next() ? Optional.of(map(rs)) : Optional.empty(); }
    } catch (SQLException e) {                // no caller anywhere can act on a SQLException
        throw new UserLookupException(id.value(), e);      // unchecked, cause preserved
    }
}
```

## Never swallow
Three shapes, all FAIL:
- **Empty catch.** `// ignored` is not a justification — state *why* it's safe, or don't catch it.
- **Catch, log, continue** with a fabricated default — the caller can no longer tell success from failure.
- **Blanket `catch (Exception e)`** over a wide block — it catches the bug in *your* code and reports it as
  a downstream failure. Catch the narrowest type; use multi-catch `catch (A | B e)`.

`InterruptedException` is the costliest swallow: catching it **clears** the interrupt flag, so dropping it
makes the thread permanently uncancellable and stalls shutdown. Always restore it:
```java
catch (InterruptedException e) { Thread.currentThread().interrupt(); throw new IllegalStateException(
    "interrupted while awaiting lock", e); }
```

## Handle exactly once
A failure is **either** propagated (with added context) **or** logged — never both; only the layer that
turns it into an outcome (an HTTP status, a retry, a fallback, a deliberate drop) logs it. *Why:
double-logging scatters one incident across N layers as N stack traces with N different messages, and
multiplies the alert count so nobody can tell how many things actually broke.*
```java
Response handle(Request req) {
    try { return Response.ok(service.execute(req)); }
    catch (ValidationException e) {
        log.warn("rejected request id={}", req.id(), e);   // caller's fault: warn, don't page
        return Response.badRequest(e.getMessage());
    } catch (RuntimeException e) {
        log.error("request failed id={}", req.id(), e);    // logged ONCE, with the whole chain
        return Response.serverError("internal error");
    }
}
// WRONG - logged here AND rethrown, so it is logged again upstream:
catch (RuntimeException e) { log.error("execute failed", e); throw e; }
// WRONG - message only: the throwable must be the LAST arg or SLF4J prints no stack trace:
catch (RuntimeException e) { log.error("execute failed: {}", e.getMessage()); }
```

## Never lose the cause
Every translation passes the cause: `throw new XException("context " + id, e)`. *Why: without it the trace
starts at your translation site and the frame that actually failed is gone forever — you debug by guesswork.*
- Every custom exception has a `(String, Throwable)` constructor; FAIL if a subtype offers only `(String)`.
- **FAIL: `new RuntimeException(e.getMessage())`** (or `e.toString()`) — losing the cause with extra steps.
- Add context the cause *lacks* — the operation plus identifiers — not a restatement: `"load profile 42"`
  wrapping `"connection reset"`, never `"error"` wrapping `"error"`.
- Entering a lambda/stream that can't declare `IOException`: `new UncheckedIOException(msg, e)`. A second,
  independent failure gets `addSuppressed(other)` — never overwrite the first.

## Custom exception design
Few, meaningful, carrying context as **typed fields** — not one class per call site, and not context
smuggled only inside a formatted message.
```java
final class PaymentDeclinedException extends Exception {  // checked: caller can offer another method
    private static final long serialVersionUID = 1L;
    private final DeclineReason reason;                   // enum: queryable by callers, Serializable
    PaymentDeclinedException(String orderId, DeclineReason reason, Throwable cause) {
        super("payment declined for order " + orderId + ": " + reason, cause);
        this.reason = reason;
    }
    DeclineReason reason() { return reason; }
}
```
- **FAIL: the catch-all wrapper** — `class ServiceException extends RuntimeException`, no fields, no distinct
  meaning, every failure funnelled into it. It destroys the caller's ability to react differently.
- Give every exception `serialVersionUID` and Serializable field types (`-Xlint:serial` flags both).
- Java 21: when several failures share a handler, an **`abstract sealed`** base (abstract, or the `switch`
  isn't exhaustive) plus pattern matching means a new subtype breaks the build instead of silently defaulting.

## Never catch Throwable or Error
`OutOfMemoryError`, `StackOverflowError`, `LinkageError` mean the JVM or classpath is broken. *Why: catching
them keeps a corrupted process serving traffic — a fast crash the scheduler replaces beats silent data
corruption.* FAIL for `catch (Throwable`, `catch (Error`, and any task loop that swallows `Exception`.
```java
Runnable guarded(Runnable work) {             // Errors are NOT caught: let the JVM die loudly
    return () -> { try { work.run(); } catch (RuntimeException e) { log.error("task failed", e); } };
}
```
`ExecutorService.submit(task)` buries the failure in the returned `Future` — **if nobody calls `get()`, the
exception is never observed and the task vanishes silently.** Use `execute()` with a guarded body instead.

## Exceptions are not control flow
Constructing a throwable runs `fillInStackTrace()`, which walks and captures every frame — orders of
magnitude costlier than a return, and it scales with stack depth. A throw per loop iteration is a real
regression, not a micro-optimization.
```java
// WRONG - "not found" is an expected outcome; every miss pays fillInStackTrace().
for (var id : ids) { try { out.add(repo.require(id)); } catch (UserNotFoundException e) { } }
// RIGHT - expected absence is a value.
for (var id : ids) repo.find(id).ifPresent(out::add);
// If a signal genuinely must be an exception (framework early-exit), preallocate it stackless:
private RetrySignal() { super("retry", null, false, false); }  // suppression off, stack trace off
```
Ops corollary: once a site throws often enough the JIT substitutes a preallocated stackless instance and you
get an exception with an *empty* trace — run production with `-XX:-OmitStackTraceInFastThrow`.

## try-with-resources and finally
Every `AutoCloseable` goes in try-with-resources. **FAIL for `close()` in a `finally` block:** if the body
throws *and* `close()` throws, the close exception **replaces** the real one. try-with-resources keeps the
body's exception primary and attaches close failures as **suppressed** (closed in reverse order).
```java
try (var in = Files.newInputStream(from); var out = Files.newOutputStream(to)) {
    in.transferTo(out);
} catch (IOException e) {
    for (Throwable s : e.getSuppressed()) log.warn("close failed during copy {}", from, s);
    throw new UncheckedIOException("copy " + from + " -> " + to, e);
}
```
- **`finally` must not `return`, `break`, or `continue`** — it discards both the pending exception and the
  pending return: `try { return parse(s); } finally { return -1; }` returns `-1` and the exception is
  *gone*. javac warns "finally clause cannot complete normally" — treat that warning as an error.
- Don't close a resource you don't own — closing a stream the caller passed in breaks every other holder.

## Optional discipline
- **Return type only.** Never a field (`Optional` isn't `Serializable`, and "null" vs "empty" becomes two
  distinct absent states); never a parameter (it forces every caller to wrap — overload, or `@Nullable`).
- **Never `.get()`.** Use `orElseThrow(() -> new UserNotFoundException(id))` — a supplier naming what was
  missing; bare `orElseThrow()` only where a preceding check makes absence a genuine bug.
- **Never the `isPresent()`/`get()` pair** — a null check with extra steps. Use `map`/`ifPresent`/`orElse`.
- **Never return a null collection or array, and never `Optional<List<T>>`** — return `List.of()` or an
  empty array so no caller needs a null check: for a collection, absent and empty mean the same thing.
- `Optional.of` throws on null — use `ofNullable` at any boundary that can produce one. It allocates per
  call, so keep `Optional` out of hot loops and entity types.
```java
Optional<User> find(UserId id);                                    // absence is normal
User require(UserId id) { return find(id).orElseThrow(() -> new UserNotFoundException(id)); }
```

## Null-safety at boundaries
Validate where the caller's frame is still on the stack. *Why: a `requireNonNull` naming the parameter tells
you who passed the null; an NPE three layers deep only tells you which field was dereferenced.*
```java
Order(String id, List<String> lines) {
    this.id = Objects.requireNonNull(id, "id");           // message form ALWAYS: it is the diagnosis
    this.lines = List.copyOf(Objects.requireNonNull(lines, "lines"));  // copy null-checks and freezes
    if (lines.isEmpty()) throw new IllegalArgumentException("order " + id + " has no lines");
}
```
- Bad argument -> `IllegalArgumentException`; bad object state -> `IllegalStateException`; null argument ->
  `NullPointerException` (the JDK convention `requireNonNull` already follows).
- Annotate the API surface (JSpecify `@Nullable`, `@NullMarked` on the package) so callers' static analysis
  works — but annotations are not runtime checks; a trust boundary needs both.

## The process edge
`System.exit` belongs only in `main`. *Why: a library that exits removes every caller's ability to handle,
retry, or clean up.* This is also the one place a blanket `catch (Exception e)` is correct.
```java
public static void main(String[] args) {
    Thread.setDefaultUncaughtExceptionHandler(     // else a failure in a thread you don't own vanishes
        (t, e) -> { log.error("uncaught in {}", t.getName(), e); Runtime.getRuntime().halt(70); });
    try { App.create(args).run(); }
    catch (ConfigException e) { System.err.println("config: " + e.getMessage()); System.exit(2); }
    catch (Exception e) { log.error("startup failed", e); System.exit(1); }   // 2 vs 1: alert on config
}
```

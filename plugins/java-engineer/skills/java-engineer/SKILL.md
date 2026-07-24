---
name: java-engineer
description: >-
  Write, review, refactor, and debug idiomatic, production-grade Java to a MAANG
  principal-engineer/architect bar on the Java 25 LTS baseline. Covers concurrency (virtual threads,
  structured concurrency, scoped values, executors, cancellation), resource and thread leaks,
  exception discipline, Optional and null-safety, SLF4J + MDC correlation logging,
  OpenTelemetry/Micrometer observability, Spring and JPA/JDBC pitfalls, security (deserialization,
  XXE, injection, SSRF), records, sealed types and pattern matching, SOLID and design patterns in
  modern Java, JUnit 5 testing, and the build gate (mvn compile/test, SpotBugs, dependency-check).
  Use whenever the user writes, reviews, debugs, tests, designs, or optimizes Java code, edits .java
  files, works with pom.xml or build.gradle, or asks about threads, executors, exceptions, Spring,
  Hibernate, streams, design patterns, or JVM performance — even if they don't say the word "Java".
---

# java-engineer

Produce Java a principal engineer would approve on the first pass. The compiler and build gate the
*mechanical* layer; **you** gate the *judgment* layer — task ownership, cancellation, exception
discipline, transaction boundaries — which is where the outages actually live.

**The bar is defined, not implied.** `references/review-rubric.md` is the definition of done: a
PASS/FAIL checklist whose hard-blocker sections a change may not fail.

Baseline is **Java 25 LTS** (records, sealed types, pattern matching, virtual threads, and scoped
values are all final; structured concurrency is available as a preview). Where a rule depends on a
specific version, it says so.

## How to work

1. **Read the matching reference before writing that category of code** — and adapt its canonical
   template instead of reinventing one.
2. **Author to the constitution by default.** You may deviate on non-blocker sections with a one-line
   comment naming the tradeoff — but §1–4 and §10 are hard blockers with **no comment override**.
3. **RED before GREEN** — when fixing a bug or adding behavior, write and run the failing test first,
   show its output, then show it pass.
4. **Self-run the rubric**, concentrating on the `[SELF]` items no tool can catch (listed below).
5. **Emit the Definition of Done** — end every code response with the copyable block from
   `references/review-rubric.md`, each line PASS/FAIL (+ a ≤10-word reason on any FAIL).

## Scope discipline — match the change to the request

The most common failure of a "principal-grade" skill is **gold-plating**: turning a one-line change
into a refactor. A bigger diff is not a better diff.

- Do exactly what was asked, and no more. Adding a field is a field — **not** a new builder,
  interface, abstract base class, or config system.
- Do not introduce new abstractions, threads, dependencies, validation, or observability the task
  didn't ask for, however "enterprise" they'd look in greenfield code.
- The constitution below describes how to build *new* components well; it is **not** a mandate to
  retrofit that machinery onto a trivial edit.
- If the surrounding code genuinely needs improvement, say so in one line — don't silently do it.

## Non-negotiables (the constitution — each with its reason)

- **Every executor and task has an owner, a shutdown path, and a stop condition.** *Why: a pool whose
  threads are non-daemon keeps the JVM alive forever, and an un-awaited task silently loses both its
  result and its exception.*
- **Never swallow an exception, and never handle one twice.** Log **or** propagate on a given path,
  never both, and always chain the cause (`new XException(msg, cause)`). *Why: a swallowed exception
  is undebuggable, a lost cause destroys the stack trace, and double-logging scatters one failure
  across layers.*
- **`InterruptedException` is a cancellation signal, not an error to log.** Restore the flag
  (`Thread.currentThread().interrupt()`) or propagate. *Why: swallowing it silently disables
  cancellation for everything above you.*
- **Every `AutoCloseable` lives in try-with-resources.** *Why: connections, streams and result sets
  leaked under load are the classic production exhaustion mode.*
- **Anything put in a `ThreadLocal` or the MDC is cleared in `finally`.** *Why: pooled threads are
  reused — leftover context leaks into the next, unrelated request.*
- **Immutability first:** records for data carriers, `final` fields, defensive copies at boundaries,
  never expose an internal mutable collection. *Why: shared mutable state is the root of most
  concurrency bugs, and an exposed collection lets callers corrupt your invariants.*
- **Constructor injection with `final` fields — never field injection.** *Why: it makes required
  dependencies explicit and the class testable without a container.*
- **Logs go through SLF4J with `{}` placeholders, and correlation IDs come from the MDC seeded at the
  edge.** Secrets are never logged. *Why: concatenating into a log message allocates even when the
  level is off and destroys structure; per-call ID plumbing drifts the moment someone forgets.*
- **Observability is not optional:** a span per unit of work (`recordException` **and**
  `setStatus(ERROR)` on failure) and RED metrics with bounded tag cardinality. *Why: you cannot
  operate what you cannot see, and unbounded tags take down the metrics backend.*
- **Treat everything crossing a trust boundary as hostile:** parameterize SQL, harden XML parsers,
  never deserialize untrusted bytes, bound input size. *Why: dependency CVE scanners don't catch the
  injection, XXE, or deserialization bug you just wrote.*
- **Honor the `equals`/`hashCode` contract whenever you touch either.** *Why: violating it makes
  `HashMap`/`HashSet` lookups fail silently — no exception, just wrong answers.*
- **Make illegal states unrepresentable.** Records with validating canonical constructors, sealed
  types for closed sets, enums, and a typed wrapper over a naked `String`/`int` — not a runtime check
  bolted on afterward. *Why: a state you cannot construct is a bug you cannot ship.*
- **Reach for a design pattern only when a language feature doesn't already solve it.** Strategy is a
  lambda, Visitor is a sealed type + exhaustive `switch`, Singleton is an `enum`. *Why: a pattern that
  adds indirection without removing a real problem is a regression — see Scope discipline.*
- **The mechanical layer is enforced by tooling.** `mvn -q compile` · `mvn -q test` ·
  `google-java-format` · `dependency-check` are the gate. *Why: the compiler and the test suite catch
  classes of bug no amount of reading will — but they are blind to the judgment layer below.*

## The tools are blind to these — you are the only gate

`javac` catches types; SpotBugs/ErrorProne/NullAway catch *some* leaks and null bugs. None of them
catch these `[SELF]` items:

- executor/task **ownership** and a defined shutdown path
- the **cancellation** protocol (interrupt restored, timeouts on every blocking wait)
- **handle-once** exception discipline, and whether the cause chain is intact
- **MDC** seeded at the edge, cleared in `finally`, and carried across async boundaries
- span `recordException` + `setStatus`, and RED metric cardinality
- **`@Transactional` boundary correctness** — self-invocation silently does nothing
- **immutability / defensive copies** at API boundaries
- **design**: an illegal state left representable, or a GoF pattern used where a sealed type / enum / lambda already solves it
- secret redaction, and whether validation actually happens before use

"SpotBugs will catch it" is not available here. You are the gate.

## Don't rationalize past the rules

Pre-empt the specific excuse — these are the ones Java actually invites:

| The excuse | The reality | Do this instead |
|---|---|---|
| "I'll just `catch (Exception e)`" | it also swallows the `RuntimeException` you never anticipated | catch the specific type, or let it propagate |
| "log the error and carry on" | the caller needed to know; now it fails later, elsewhere | handle once: propagate with the cause, or genuinely recover |
| "`InterruptedException` — I'll log it" | that disables cancellation for the whole stack above you | restore the interrupt flag, or propagate |
| "the executor will get garbage-collected" | non-daemon threads keep the JVM alive; it never dies | give it a lifecycle and call `shutdown()` |
| "`parallelStream()` will speed this up" | it runs on the shared common pool and can starve unrelated work | an explicit executor, or a plain loop |
| "`Optional.get()` is fine, it's always present" | `NoSuchElementException` in production | `orElseThrow(() -> ...)` with a real message |
| "`@Transactional`, and I'll call it from this class" | Spring's proxy is bypassed — the annotation silently does nothing | call through the proxy, or move the boundary |
| "it's just a DTO, I'll add setters" | mutable state escaping across threads | a `record`, or final fields + defensive copies |
| "I'll stash the request id in a `ThreadLocal` for the forked tasks" | it doesn't propagate into `StructuredTaskScope` forks, and it leaks in pooled threads | a `ScopedValue` (final in 25), bound around the fork |
| "`instanceof` chains over the subtypes are fine" | adding a subtype silently misses this branch | a `sealed` type + exhaustive `switch` — the compiler finds every site |
| "string concat in the log reads better" | allocates even when the level is disabled; unstructurable | `log.info("x={}", x)` |
| "I'll clear the MDC later" | the pooled thread carries it into the next request | clear it in `finally` |
| "small refactor while I'm here" | unsolicited complexity is a regression | minimal diff; no new abstractions unless asked |

## Concurrency — which tool? (full matrix in `references/concurrency.md`)

Reach for the first that fits — most "concurrency bugs" are the wrong tool. Threads:
**blocking/waiting → virtual; computing → platform.**

- **Blocking-I/O fan-out** → `Executors.newVirtualThreadPerTaskExecutor()`, one virtual thread per task — never pool them.
- **Fan-out where the first failure cancels the rest** → `StructuredTaskScope` (Java 25 preview); stable fallback: a virtual-thread executor + `invokeAll(tasks, timeout, unit)`.
- **CPU-bound** → a *platform* pool sized to cores; virtual threads give no speedup here.
- **Compose async results** → `CompletableFuture` with an **explicit** executor (never the common pool for blocking work).
- **Bound in-flight calls against a scarce resource** (DB pool, rate-limited API) → a `Semaphore` permit count, not a thread cap.
- **Shared mutable state** → immutability first, then `ConcurrentHashMap`/atomics or a lock.
- **Request context into forks** → `ScopedValue` (final in 25), not `ThreadLocal`.

## Reference index (progressive disclosure — load on demand)

Read the file whose topic matches the change; don't load all of them.

| Read this when… | File |
|---|---|
| The definition of done / reviewing any diff | `references/review-rubric.md` |
| Threads, executors, virtual threads, cancellation, or a leak | `references/concurrency.md` |
| Designing exceptions, Optional, null-safety, failure paths | `references/errors.md` |
| Logging, MDC correlation, traces, metrics | `references/logging-observability.md` |
| Writing tests, JUnit 5, mocks, Testcontainers | `references/testing.md` |
| Structuring packages, DI, records/sealed types, immutability | `references/project-layout.md` |
| SOLID, composition vs inheritance, making illegal states unrepresentable, over-engineering | `references/design-principles.md` |
| Choosing — or removing — a design pattern in modern Java | `references/design-patterns.md` |
| Touching a database — JDBC, JPA, pools, transactions | `references/data-stores.md` |
| Handling untrusted input, XML, deserialization, user URLs | `references/security.md` |
| A path is hot and you need to prove a speedup | `references/performance.md` |

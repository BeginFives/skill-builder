# Design patterns — Java

Load this before reaching for a named GoF pattern. Governing rule: **a pattern is a workaround for a
missing language feature — reach for one only when no feature already solves the problem.** In modern
Java (records, sealed types, pattern-matching `switch`, enums, lambdas) many classic patterns collapse
into a single declaration. Indirection you add must *remove* a real problem — a naming, a lifecycle, a
variation point. Indirection that only adds a layer is a regression a reviewer should reject.

## Contents
- [The decision rule](#the-decision-rule)
- [Strategy & Command → a functional interface + lambda](#strategy--command--a-functional-interface--lambda)
- [Singleton → enum, DI, or the holder idiom](#singleton--enum-di-or-the-holder-idiom)
- [Factory → static factory methods stay valuable](#factory--static-factory-methods-stay-valuable)
- [Abstract Factory → dependency injection](#abstract-factory--dependency-injection)
- [Builder → still right, but not for a 2-field value](#builder--still-right-but-not-for-a-2-field-value)
- [Visitor → sealed interface + exhaustive switch](#visitor--sealed-interface--exhaustive-switch)
- [State → sealed interface + pattern-matching switch](#state--sealed-interface--pattern-matching-switch)
- [Observer → listeners / Flow, never Observable](#observer--listeners--flow-never-observable)
- [Decorator / Adapter / Proxy / Facade — still relevant](#decorator--adapter--proxy--facade--still-relevant)
- [Template Method → composition, a lambda step](#template-method--composition-a-lambda-step)
- [Chain of Responsibility → a list of functional handlers](#chain-of-responsibility--a-list-of-functional-handlers)
- [Idioms worth naming](#idioms-worth-naming)
- [The anti-cargo-cult rule](#the-anti-cargo-cult-rule)

## The decision rule
Before writing a pattern class, name the feature that would delete it:

| Classic pattern | Modern Java first answer | Keep the pattern when |
|---|---|---|
| Strategy, Command | a lambda behind a functional interface | the behavior owns state/lifecycle, not just a call |
| Singleton | enum, or a DI-managed single instance | never hand-roll lazy init — use the holder idiom |
| Factory Method / Static Factory | `of()` / `valueOf()` / named static factory | almost always keep — it hides the concrete type |
| Abstract Factory | constructor injection (DI wires the impl) | you must pick a family at runtime with no container |
| Builder | a record's canonical constructor | many optional params, or a step builder |
| Visitor | `sealed` + exhaustive `switch` + record patterns | the type set is open (then Visitor is the lesser evil) |
| State | `sealed` + pattern-matching `switch` | states carry heavy behavior better as classes |
| Observer | `Consumer` listeners / `java.util.concurrent.Flow` | — (`java.util.Observable` is deprecated; do not use) |
| Template Method | pass the varying step as a lambda | the variation is a genuine subtype, not "a step" |
| Chain of Responsibility | a `List` of functional handlers | — |

## Strategy & Command → a functional interface + lambda
A Strategy is a family of interchangeable algorithms. In Java that is a functional interface and a
lambda — `Comparator` is the canonical case; a whole hierarchy of comparator subclasses collapses into
one composed expression:
```java
record Employee(String name, int salary, int age) {}
in.stream()
  .sorted(Comparator.comparingInt(Employee::salary).reversed()  // the strategy IS the comparator
                    .thenComparing(Employee::name))
  .toList();
```
Command is the same move — a request captured as a `Runnable`/`Callable`/`Consumer`, storable and
replayable. An undo stack is a `Deque<Runnable>`, not a `Command` interface with N implementing classes:
```java
Deque<Runnable> undo = new ArrayDeque<>();
undo.push(() -> doc.reinsert(range));   // the command
undo.pop().run();                       // execute / undo
```
**Keep a named type only when the behavior carries state or a lifecycle** — a `RetryPolicy` with config
and metrics is a class; a one-line comparison is a lambda. **FAIL: a `Strategy` interface with one
`execute` method and five one-line subclasses** — that is a lambda wearing a costume, and every subclass
is a file a reader must open. Give the functional interface a domain name (`PricingRule`, not
`Strategy`) so call sites read as intent.

## Singleton → enum, DI, or the holder idiom
Prefer a **DI-managed single instance** — the container owns exactly one, and tests inject a fake. When
you truly need a language-level singleton, the **enum singleton** is the safest: serialization-proof,
reflection-proof, thread-safe by class init.
```java
enum Registry {
    INSTANCE;
    private final Map<String, Handler> handlers = new ConcurrentHashMap<>();
    void register(String k, Handler h) { handlers.put(k, h); }
}
```
For *lazy* initialization, use the **initialization-on-demand holder idiom** — class initialization is
JVM-guaranteed thread-safe and lock-free after the first touch:
```java
final class Config {
    private Config() {}
    private static final class Holder { static final Config INSTANCE = new Config(); }
    static Config instance() { return Holder.INSTANCE; }   // safe, lazy, no synchronization on the hot path
}
```
**FAIL: the classic lazy `if (instance == null) instance = new X()`** — two callers race and build two
instances. **FAIL: double-checked locking on a non-`volatile` field** — a reader can observe a
half-constructed object (its final fields not yet visible). If you insist on DCL the field must be
`volatile`; the holder idiom is strictly better and needs no lock at all. A `synchronized` accessor
"fixes" the race but serializes every read forever — the holder idiom avoids both.

## Factory → static factory methods stay valuable
This one usually **stays**. A static factory names intent, can cache or pool, and returns a type the
caller can't name — `List.of(...)` hands back an unmodifiable implementation you can't `new`. Prefer it
over a public constructor whenever the name adds meaning or the concrete type should stay hidden:
```java
record Money(long minorUnits, Currency currency) {
    static Money usd(long cents) { return new Money(cents, Currency.getInstance("USD")); }  // named, validated
}
```
Conventions worth following: `of(...)` for a value from its parts, `valueOf(...)` for conversion,
`from(...)` for a single-arg adapt, `getInstance()` for a managed instance. The GoF *Factory Method*
(subclass overrides a `create()` hook) is rarely what you want — reserve it for a genuine framework
extension point; a plain static factory or a `Supplier<T>` parameter covers the rest.

## Abstract Factory → dependency injection
An Abstract Factory exists to pick a *family* of related objects. In an app with a DI container that is
just **constructor injection** — the container is the factory, and the class declares what it needs:
```java
final class OrderService {
    private final Clock clock;
    private final IdGen ids;
    OrderService(Clock clock, IdGen ids) { this.clock = clock; this.ids = ids; }  // collaborators supplied
}
```
Wiring lives in one composition root; tests pass fakes (`Instant::now`, `() -> "id-1"`). **FAIL: a class
that `new`s its own collaborators** — it can't be tested without the real dependencies and can't swap
implementations. Keep a hand-written Abstract Factory only when you must choose a family at runtime with
no container available (e.g. a platform/back-end selector in a library).

## Builder → still right, but not for a 2-field value
A Builder earns its place for **many optional parameters** (avoiding a telescoping constructor) and for
**step builders** that enforce required order. But a record's canonical constructor already gives you
immutability, `equals`/`hashCode`, and validation — so **do not write a builder for a 2-field value**:
```java
record Range(int lo, int hi) {                       // no builder: the compact constructor is the whole guard
    Range { if (lo > hi) throw new IllegalArgumentException("lo > hi: " + lo + " > " + hi); }
}
```
When there genuinely are many optionals, pair the builder *with* a record so the built value stays
immutable and self-validating:
```java
record HttpCall(String method, String uri, Map<String, String> headers, Duration timeout) {
    HttpCall { headers = Map.copyOf(headers); }      // defensive copy + freeze
    static Builder to(String uri) { return new Builder(uri); }
    static final class Builder {
        private final String uri; private String method = "GET";
        private final Map<String, String> headers = new LinkedHashMap<>();
        private Duration timeout = Duration.ofSeconds(30);
        private Builder(String uri) { this.uri = uri; }
        Builder header(String k, String v) { headers.put(k, v); return this; }
        Builder timeout(Duration d) { this.timeout = d; return this; }
        HttpCall build() { return new HttpCall(method, uri, headers, timeout); }
    }
}
```

## Visitor → sealed interface + exhaustive switch
This is the flagship modernization. The GoF Visitor exists only to fake dispatch over a closed type set
in a language without pattern matching — `accept(Visitor)` double dispatch, one `visitX` per variant,
and adding a variant silently forces edits across every visitor. **A `sealed` interface plus an
exhaustive `switch` with record-deconstruction patterns replaces the whole apparatus**:
```java
sealed interface Expr permits Num, Add, Mul, Neg {}
record Num(double value) implements Expr {}
record Add(Expr left, Expr right) implements Expr {}
record Mul(Expr left, Expr right) implements Expr {}
record Neg(Expr operand) implements Expr {}

static double eval(Expr e) {
    return switch (e) {                              // NO default clause
        case Num(double v)     -> v;                  // deconstruct the record right in the label
        case Add(var l, var r) -> eval(l) + eval(r);
        case Mul(var l, var r) -> eval(l) * eval(r);
        case Neg(var o)        -> -eval(o);
    };   // add a 5th Expr and every exhaustive switch stops compiling — the failure you WANT
}
```
`when` guards refine a case without a nested `if`:
```java
case Num(double v) when v == 0 -> "zero";
case Num(double v)             -> "literal " + v;
```
**The tradeoff, stated honestly (the Expression Problem).** Adding a *variant* under `sealed` breaks
every exhaustive switch — which is the good failure: the compiler hands you the exact list of sites to
update. Adding an *operation* is one new method with a local switch. Classic Visitor is the mirror image:
a new operation is one new visitor, but a new variant means editing every visitor with no compiler help
at all. Since the variant set is usually the more stable axis and silent fall-through is the expensive
bug, **sealed + exhaustive switch wins for any closed domain.** Never write `default -> throw` over a
sealed type — it disables the exhaustiveness check and hides the next added variant.

## State → sealed interface + pattern-matching switch
A closed set of states with transitions is the same shape. Model states as records under a `sealed`
interface and make each transition a **pure function returning the next state** — no mutable `state`
field hidden inside per-state objects, and illegal transitions are unrepresentable:
```java
sealed interface ConnState permits Disconnected, Connecting, Connected {}
record Disconnected() implements ConnState {}
record Connecting(Instant since) implements ConnState {}
record Connected(String sessionId) implements ConnState {}

static ConnState onTimeout(ConnState s) {
    return switch (s) {
        case Connecting c   -> new Disconnected();   // only this state reacts
        case Disconnected d -> d;
        case Connected c    -> c;
    };
}
```
Keep the GoF State (one class per state, behavior methods) only when each state carries substantial
behavior that reads better as a type than as a `switch` arm.

## Observer → listeners / Flow, never Observable
For in-process notification, a `CopyOnWriteArrayList` of `Consumer` listeners is the whole pattern — and
returning an unsubscribe handle removes the classic listener leak:
```java
final class Button {
    private final List<Consumer<ClickEvent>> listeners = new CopyOnWriteArrayList<>();
    Runnable onClick(Consumer<ClickEvent> l) { listeners.add(l); return () -> listeners.remove(l); }
    void click() { var e = new ClickEvent(Instant.now()); listeners.forEach(l -> l.accept(e)); }
}
```
When you need **backpressure** (a fast producer, a slow consumer), use the standardized reactive SPI
`java.util.concurrent.Flow` with the built-in `SubmissionPublisher` rather than hand-rolling a subject:
```java
try (var pub = new SubmissionPublisher<PriceTick>()) {
    pub.subscribe(new Flow.Subscriber<>() {
        private Flow.Subscription sub;
        public void onSubscribe(Flow.Subscription s) { sub = s; s.request(1); }  // demand, not push
        public void onNext(PriceTick t) { handle(t); sub.request(1); }
        public void onError(Throwable t) { log.error("stream", t); }
        public void onComplete() { }
    });
    ticks.forEach(pub::submit);
}   // close() awaits delivery
```
**FAIL: `java.util.Observable` / `java.util.Observer`.** They are deprecated (since Java 9) — still on
the classpath in Java 25, but do not use them: `Observable` is a concrete class (forces inheritance),
its notify/change protocol is error-prone, and it is not generic. Use listeners or `Flow`.

## Decorator / Adapter / Proxy / Facade — still relevant
These wrap rather than replace, so no language feature deletes them — but their weight drops.

**Decorator** adds behavior around a delegate. For a single-method interface it is a lambda:
```java
static Repo timed(Repo delegate, LongConsumer nanos) {
    return id -> { long t = System.nanoTime();
                   try { return delegate.load(id); } finally { nanos.accept(System.nanoTime() - t); } };
}
```
The JDK's own I/O streams are Decorators (`BufferedInputStream` wraps `InputStream`); for a multi-method
interface, still a hand-written wrapper class.

**Adapter** bridges an incompatible interface — again a lambda when the target is functional:
```java
OurLog adapt(System.Logger jdk) { return msg -> jdk.log(System.Logger.Level.WARNING, msg); }
```

**Proxy** is the one that stays substantial. Use `java.lang.reflect.Proxy` when **one cross-cutting
concern** (logging, retries, transactions, metrics) must wrap **many methods or an interface you don't
control at compile time** — writing N wrappers by hand is the thing the dynamic proxy removes:
```java
static <T> T logging(Class<T> iface, T target, System.Logger log) {
    return (T) Proxy.newProxyInstance(iface.getClassLoader(), new Class<?>[]{iface},
        (p, method, args) -> {
            log.log(System.Logger.Level.DEBUG, "-> " + method.getName());
            try { return method.invoke(target, args); }
            catch (InvocationTargetException e) { throw e.getCause(); }   // unwrap so callers see the real cause
        });
}
```
Cost: reflection, no compile-time type checking of the handler, harder stack traces. For a single
interface prefer a hand-written wrapper or an interface `default` method; for framework-scale
cross-cutting the container (Spring AOP, CDI interceptors) already does exactly this — don't rebuild it.

**Facade** stays a plain class: one narrow, intention-revealing entry point over a multi-step subsystem.
No feature replaces it; keep it *thin* — a facade that grows business logic has become a god object.

## Template Method → composition, a lambda step
The GoF Template Method fixes an algorithm skeleton in an abstract base and lets subclasses override
hook methods. That couples the variation to an inheritance hierarchy. Prefer **hoisting the invariant
skeleton into one method and passing the varying step as a function** — the open/close, the retry, the
timing stay in one place; the variant is an argument:
```java
static <T> List<T> withReader(Path p, Function<BufferedReader, List<T>> parse) throws IOException {
    try (var r = Files.newBufferedReader(p)) { return parse.apply(r); }   // resource handling = invariant
}
```
Reach for an abstract base only when the variation is a **genuine subtype** (an `is-a` with its own
identity and multiple methods), not merely "a different step in the middle."

## Chain of Responsibility → a list of functional handlers
The linked-list-of-handler-objects-each-holding-`next` is a `List` of functions and a `stream`. Each
handler returns `Optional` — present means "handled, stop"; the chain short-circuits on the first hit:
```java
@FunctionalInterface interface Handler { Optional<Response> handle(Request r); }

static Response dispatch(Request r, List<Handler> chain) {
    return chain.stream()
        .flatMap(h -> h.handle(r).stream())      // Optional::stream flattens 0-or-1
        .findFirst()
        .orElseThrow(() -> new IllegalStateException("unhandled: " + r));
}
```
A filter chain (servlet style) is the same idea folded the other way — a `List` of
`UnaryOperator`/`BiFunction` composed into one pipeline. No per-node class, no `setNext` wiring.

## Idioms worth naming
These are the patterns modern Java *adds*, worth reaching for by name:
- **Value object → `record`.** Immutable, `equals`/`hashCode`/`toString` for free, deconstructible in
  `switch`. The default answer for any data-carrying type; add a compact constructor to validate/copy.
- **Closed hierarchy → `sealed`.** Makes the variant set finite and the compiler your exhaustiveness
  checker — the foundation under the Visitor and State replacements above.
- **Scoped cleanup (RAII) → try-with-resources.** Any `AutoCloseable` is released deterministically in
  reverse order, with body exceptions kept primary and close failures suppressed. This is the resource
  discipline pattern — see `concurrency.md` and `errors.md`.
- **Null-object-ish return → `Optional` (return type only) / an empty collection.** Absence becomes a
  value the caller can `map`/`orElse` over instead of a null to guard — never a null `List`, return
  `List.of()`.
- **Functional options → varargs of configuring lambdas**, but only when a builder or record reads
  worse. In Java it usually reads worse; default to the builder/record and don't import a Go idiom for
  its own sake.

## The anti-cargo-cult rule
This is Scope discipline applied to design: **do not add a pattern where a language feature already
solves the problem.** Each GoF pattern was, in its era, a workaround for something the language couldn't
express — and Java can now express most of them directly. Before you write a `*Factory`, `*Strategy`,
`*Visitor`, `*Manager`, or `*Singleton` class, ask which record, sealed type, enum, or lambda would make
it disappear, and whether the indirection removes a real problem (a lifecycle, a hidden type, a variation
point) or merely adds a layer. A pattern that adds indirection without removing a problem is not
"enterprise-grade" — it is a regression: more files to open, more names to learn, more places for the
next change to touch. The reviewer's default on a hand-rolled pattern is *"show me the feature this
beats."*

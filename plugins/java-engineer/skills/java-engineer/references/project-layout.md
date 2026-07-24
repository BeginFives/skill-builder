# Project layout, types, DI & boundaries — Java

Load this when structuring a package/module, wiring dependencies, designing a data type, or loading
config. Governing rule: **types are immutable and dependencies arrive through the constructor;
nothing is reachable statically, and nothing is `public` by accident.**

## Contents
- [Layout & module boundaries](#layout--module-boundaries)
- [Constructor injection — always](#constructor-injection--always)
- [Interfaces & seams](#interfaces--seams)
- [Immutability first](#immutability-first)
- [Value contracts: equals, hashCode, Comparable](#value-contracts-equals-hashcode-comparable)
- [Sealed types & exhaustive switch](#sealed-types--exhaustive-switch)
- [Builders & scope discipline](#builders--scope-discipline)
- [No static mutable state](#no-static-mutable-state)
- [Config & secrets](#config--secrets)

## Layout & module boundaries
```
src/main/java/com/acme/orders/     one feature: its API type, its ports, its implementations
src/main/resources/                config templates — never secrets
src/test/java/com/acme/orders/     mirrors main; same package ⇒ can test package-private types
```
- **Package by feature, not by layer.** FAIL if the top level is `controller/ service/ repository/
  dto/`: one feature change then touches four packages, every class must be `public` to cross them,
  and the layering is unenforced anyway. *Why: things that change together belong together — a
  feature package can hide its internals, a layer package cannot.*
- **Package-private is the default access level;** `public` only on what another package must call.
  *Why: every `public` type is a compatibility promise and a refactor you must negotiate.*
- One Maven/Gradle module per independently releasable artifact — never one per layer. "No cycles,
  no reach-in" is a wish unless a test enforces it: an ArchUnit rule in `src/test/java`
  (`noClasses().that().resideInAPackage("..billing..").should().dependOnClassesThat()...`).
- Pin `<release>21</release>` and `-parameters` in the build. *Why: frameworks that bind by
  parameter name (config binding, JSON, JPA) degrade silently without `-parameters`.*

## Constructor injection — always
```java
interface OrderRepository { Optional<Order> findById(String id); void save(Order o); } // port: consumer-defined

public final class OrderService {          // public: the one type other packages call
    private final OrderRepository repo;    // final: assigned once, safely published, never re-wired
    private final PaymentGateway payments;

    public OrderService(OrderRepository repo, PaymentGateway payments) { // required deps, visible
        this.repo = Objects.requireNonNull(repo, "repo");
        this.payments = Objects.requireNonNull(payments, "payments");
    }
}
```
- **FAIL: field injection** — `@Autowired private OrderRepository repo;`. It cannot be `new`ed in a
  unit test (needs reflection or a container), it hides the dependency count so a 9-dependency god
  class looks like a 2-dependency one, and the field is left non-final. Setter injection has the
  same defects plus a window where the object is half-built.
- With a single constructor Spring injects **without any annotation** — the wiring stays plain Java,
  and the same class works under Spring, under a test, and under `main`.
- FAIL if a constructor does work: no I/O, no thread starts, no registering into a global, no
  passing `this` to anything. *Why: a partially-constructed object that escapes is visible to other
  threads with non-final fields unset.* Constructors assign and validate; a `start()` does work.
- A circular constructor dependency is a design error, not a puzzle for `@Lazy` — extract the shared
  piece into a third type.
- **Java 25 (JEP 513, needs `--release 25`)** allows statements *before* `super(...)`, so a subclass
  rejects invalid input before the superclass constructor runs: `Sub(int v) { if (v < 0) throw …; super(v); }`.

## Interfaces & seams
- Define the interface **at the consumer**, name it for what the consumer needs (`OrderRepository`),
  and keep it to 1–3 methods. The implementation lives beside it and stays package-private.
- **FAIL: the reflexive one-implementation interface.** `OrderServiceImpl implements OrderService`
  with one impl and no test fake doubles the file count, breaks go-to-definition, and buys nothing —
  classes mock and subclass fine. Add the interface when a *second* impl or a hand-written fake exists.
- Do not add an interface, constructor, or builder to an existing class just to wire in one new
  field — that is a field, not a refactor (SKILL.md → Scope discipline).

## Immutability first
```java
record Invoice(String id, List<LineItem> lines, Map<String, String> tags, Instant issuedAt) {
    Invoice {                              // validate + copy on the way IN
        Objects.requireNonNull(id, "id");
        lines = List.copyOf(lines);        // immutable snapshot; also rejects null elements
        tags  = Map.copyOf(tags);
    }                                      // accessors now hand out immutable views for free
}

final class Cart {
    private final List<LineItem> lines = new ArrayList<>();
    List<LineItem> linesWrong() { return lines; }                        // WRONG
    List<LineItem> lines()      { return List.copyOf(lines); }           // RIGHT: snapshot
    List<LineItem> liveView()   { return Collections.unmodifiableList(lines); } // RIGHT: read-only view
}
```
- **FAIL: returning an internal mutable collection, array, or `Date`.** This is a bug, not a style
  nit: `cart.linesWrong().clear()` compiles and erases every invariant `add()` enforces, and a
  "thread-safe" class stops being thread-safe the instant its internals escape. Copy in *and* out.
- `record` does **not** deep-freeze: one holding a `List` still aliases the caller's list unless the
  compact constructor copies it.
- `List.of`/`Map.of`/`Set.of` for constants; `List.copyOf` for a snapshot; `unmodifiableList` for a
  live read-only view — know which you handed out; they differ once the source keeps mutating.
- FAIL: `java.util.Date`, `Calendar`, `SimpleDateFormat` (mutable; the last is not thread-safe).
  Use `java.time`: `Instant` for timestamps, `Duration` for spans, `DateTimeFormatter` (immutable).
- `Optional` is a *return* type. FAIL as a field or parameter — it adds a third null-vs-empty state
  and forces `Optional.of(x)` on every caller.

## Value contracts: equals, hashCode, Comparable
- **Identity fields must be `final`.** FAIL if `equals`/`hashCode` reads a mutable field — verified:
  `map.put(k, v); k.region = "eu";` → `map.get(k) == null` while `map.size() == 1`. The entry is
  stranded in the wrong bucket: a silent lookup failure *and* a leak, with no exception anywhere.
- `compareTo`/`Comparator` must be consistent with `equals` (`compare == 0` iff `equals`) for any
  type that can enter a `TreeMap`/`TreeSet` — those use ordering, not `equals`. A `Money` whose
  `compareTo` ignores `currency` gives `HashSet.size()==2` and `TreeSet.size()==1` for the same two
  elements: a distinct element silently vanishes.
- Build comparators with `Comparator.comparing(...).thenComparing(...)`. FAIL: `(a, b) -> a.x < b.x
  ? -1 : 1` — never returning 0 breaks antisymmetry, and TimSort throws
  `IllegalArgumentException: Comparison method violates its general contract!` only past its merge
  threshold (no throw at n=64, throws at n=256+): green unit test, production incident.
- Prefer a `record` — it derives both from all components correctly. Override only for a *narrower*
  identity, and document it. Value types are `final` or records: an `equals` widened in a subclass is
  asymmetric, so `a.equals(b) != b.equals(a)` and collections behave differently by reference type.

## Sealed types & exhaustive switch
```java
public sealed interface PaymentResult {          // nested subtypes: `permits` is inferred
    record Captured(String authCode, long amountMinor) implements PaymentResult {}
    record Declined(String reason) implements PaymentResult {}
    record Pending(Duration retryAfter) implements PaymentResult {}

    static String describe(PaymentResult r) {
        return switch (r) {                      // no `default` — that is the point
            case Captured(String auth, long minor) -> "captured " + minor + " (" + auth + ")";
            case Declined d when d.reason().isBlank() -> "declined: unknown";
            case Declined d -> "declined: " + d.reason();
            case Pending(Duration after) -> "retry in " + after;
        };
    }
}
```
- FAIL: a `default ->` arm, or an `instanceof` chain ending in a "shouldn't happen" throw. *Why: with
  no default, adding a permitted subtype turns every switch into a compile error — the compiler
  becomes your migration checklist instead of a 3am pager.*
- Seal what you own and intend to close (results, protocol frames, AST nodes, state machines); for an
  open extension point, a plain interface is correct.

## Builders & scope discipline
```java
record Endpoint(String host, int port) {}                  // 2 fields: a record. A builder is noise.
static Builder builder(Endpoint endpoint) { ... }          // required args in the factory
static final class Builder {
    private Duration readTimeout = Duration.ofSeconds(5);  // documented defaults
    Builder readTimeout(Duration d) { this.readTimeout = d; return this; }
    HttpClientConfig build() {                             // validate ONCE, here
        if (readTimeout.isNegative()) throw new IllegalArgumentException("readTimeout < 0");
        return new HttpClientConfig(this);                 // half-built config never escapes
    }
}
```
- Reach for a builder only at ≥4–5 params, several optional-with-defaults, or adjacent same-typed
  params callers will transpose. **FAIL: a builder on a 2-field class** — that is a record.
- Required args go in the static factory so they cannot be omitted; optional ones in the setters.
  Prefer a *named* factory over a bare constructor when the name carries information
  (`Money.ofMinor(1250)`) — mandatory when two would share a signature.

## No static mutable state
- **FAIL: any non-final static field, or a mutated `static final` collection/array.** Tests then
  share state and fail by execution order, unrelated features couple invisibly, and class-init order
  becomes behavior. OK: `static final` immutable values and `private static final Logger log`.
- FAIL: a static utility holder that needs a dependency (`OrderUtils.validate(...)` reaching for a
  repository or `Instant.now()`) — that is an object with a constructor, not a static method.
- Inject `Clock`; call `clock.instant()`, never `Instant.now()` inline. *Why: `Clock.fixed(...)`
  makes expiry/retention logic testable without sleeping or mocking statics.*
- No behavior in static initializers — wiring belongs in one readable composition root (`main`, or a
  single `@Configuration`).

## Config & secrets
```java
record ServerConfig(int port, Duration requestTimeout, URI databaseUrl, Secret dbPassword) {
    ServerConfig {   // an invalid config is now unrepresentable
        if (port < 1 || port > 65_535) throw new IllegalArgumentException("PORT out of range: " + port);
        if (requestTimeout.isNegative() || requestTimeout.isZero())
            throw new IllegalArgumentException("REQUEST_TIMEOUT must be > 0: " + requestTimeout);
        Objects.requireNonNull(databaseUrl, "DATABASE_URL");
    }
    static ServerConfig fromEnv(Map<String, String> env) { /* parse + require(env, "DATABASE_URL") */ }
}
```
- Validate at construction and construct at startup: fail with the offending **key name** before the
  first request. FAIL if a missing/invalid setting surfaces as an NPE on request #1 at 3am.
- Secrets come from env / a secret manager — never `application.yml` in the repo, never `toString()`.
  Wrap them in a type whose `toString()` returns `***` (see logging-observability.md for redaction).
- Timeouts, pool sizes, and limits are typed (`Duration`, not `int seconds`) with documented
  defaults — not magic numbers at call sites.
- Spring: `@ConfigurationProperties` on a `record` + `@Validated` gives constructor binding and
  startup failure for free. FAIL: `@Value("${...}")` on fields — untyped, unvalidated, uninspectable.

## Transactions
`@Transactional` boundaries, self-invocation, propagation, and lazy-loading outside the transaction
belong to **data-stores.md** — read it before annotating a service method.

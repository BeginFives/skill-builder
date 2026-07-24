# Design principles — Java

Load this when shaping a type, an interface seam, or a class hierarchy — or when a reviewer says
"this is over-engineered" or "this won't extend." Governing rule: **make illegal states
unrepresentable, depend on abstractions, and keep the change small. A principle is a reason to keep a
diff correct and minimal — never a license to add machinery.**

These are the *judgment* layer; the compiler cannot check them. Mechanics they build on — records,
sealed types, constructor injection, immutability, `Optional` — live in `project-layout.md` and
`errors.md`; this file is when and why, not how to declare a record again.

## Contents
- [SRP — one reason to change](#srp--one-reason-to-change)
- [OCP — extend by adding a type, not editing a switch](#ocp--extend-by-adding-a-type-not-editing-a-switch)
- [LSP — a subtype must honor the supertype's contract](#lsp--a-subtype-must-honor-the-supertypes-contract)
- [ISP — small role interfaces, at the consumer](#isp--small-role-interfaces-at-the-consumer)
- [DIP — depend on an abstraction, inject the detail](#dip--depend-on-an-abstraction-inject-the-detail)
- [Composition over inheritance](#composition-over-inheritance)
- [Make illegal states unrepresentable](#make-illegal-states-unrepresentable)
- [Immutability, cohesion, encapsulation, Demeter, fail-fast](#immutability-cohesion-encapsulation-demeter-fail-fast)
- [DRY vs YAGNI vs over-engineering](#dry-vs-yagni-vs-over-engineering)

## SRP — one reason to change
A class has **one reason to change** — one actor/axis whose churn edits it. The unit is a
reason-to-change, not "one method."
- **FAIL: the `-Manager`/`-Service` that parses the request, applies the business rule, persists, and
  formats the response.** Four reasons to change collide in one file: a DB-schema change and a
  wire-format change both edit it, and each risks breaking the other. Split along the axes — a parser,
  a policy, a repository — each testable alone. *Why: coupling unrelated change-reasons means every
  edit re-tests the whole class and every team waits on the same file.*
- Cohesion is SRP's positive form: a class whose fields are all used by most of its methods has one
  job; one where method A touches fields 1–2 and method B touches fields 3–4 is two classes wearing
  one name.

## OCP — extend by adding a type, not editing a switch
Open to extension, closed to modification. Two correct Java shapes, and you pick by **where the
behavior lives**:

- **Behavior on the type → truly open.** Put the operation on a `sealed` interface; each variant
  implements it. A new variant is a *new file* — no existing call site changes.
```java
sealed interface Discount permits PercentOff, FlatOff, None {
    long applyMinor(long grossMinor);                       // behavior lives on the variant
}
record PercentOff(int pct) implements Discount {
    PercentOff { if (pct < 0 || pct > 100) throw new IllegalArgumentException("pct"); }
    public long applyMinor(long g) { return g - (g * pct) / 100; }
}
record FlatOff(long amountMinor) implements Discount {
    public long applyMinor(long g) { return Math.max(0, g - amountMinor); }
}
enum None implements Discount { INSTANCE; public long applyMinor(long g) { return g; } }
```
- **Behavior must be centralized → the exhaustive switch is your safety net.** When the operation
  can't live on the type (it needs a collaborator the data type shouldn't know), a `sealed` type + a
  `switch` with **no `default`** makes adding a variant a *compile error at every site* — the compiler
  is your migration checklist, not a 3am pager. See `project-layout.md` → Sealed types (don't
  re-derive the switch here).
- **FAIL: `switch (shape.type)` / an `instanceof` ladder ending in `default: throw`.** A new case
  compiles clean, falls through the `default`, and throws in production. That is a closed set modeled
  with an open construct — the exact thing OCP forbids.

## LSP — a subtype must honor the supertype's contract
A subtype must be substitutable everywhere the supertype is expected: **no strengthened precondition,
no weakened postcondition, no broken invariant.** English "is-a" is not behavioral "is-a."

- **Broken invariant — Rectangle/Square.** `Rectangle` promises width and height vary independently.
  `Square` can't keep that promise, so code correct against `Rectangle` breaks:
```java
class Rectangle { protected int w, h; /* setWidth, setHeight, area = w*h */ }
class Square extends Rectangle {                       // "a square is a rectangle" — in English only
    @Override void setWidth(int s)  { this.w = s; this.h = s; }   // strengthens: also mutates height
    @Override void setHeight(int s) { this.w = s; this.h = s; }
}
Rectangle r = new Square(2);
r.setWidth(3); r.setHeight(4);                         // a Rectangle caller expects area 12…
r.area();                                              // …gets 16 — the subtype broke the contract
```
- **Strengthened precondition.** A subtype that rejects input the supertype accepts is not
  substitutable — the caller can't tell which instance it holds:
```java
class EventSink   { void accept(String e, int retryCount) {/* accepts any count, incl. 0 */} }
class StrictSink extends EventSink {
    @Override void accept(String e, int retryCount) {
        if (retryCount < 1) throw new IllegalArgumentException("retryCount >= 1");  // FAIL: strengthened
        super.accept(e, retryCount);
    }
}
void drain(EventSink s, List<String> es) { for (var e : es) s.accept(e, 0); }  // 0 is legal per EventSink
// drain(new StrictSink(), …) throws — same code, different subtype: LSP violated.
```
- The fix is almost always **composition or sealed siblings**, not a hierarchy that can't preserve the
  contract. `record`s are implicitly `final` — they can't be subclassed, so they sidestep this whole
  failure class by construction. Inherit only when the override honors *everything* the parent accepts
  and promises.

## ISP — small role interfaces, at the consumer
A client shouldn't depend on methods it doesn't call. Define a **role interface named for what the
consumer needs**, beside the consumer, at 1–3 methods — not a fat producer interface every caller
drags in whole.
```java
interface PriceLookup { long priceMinor(String sku); }        // the one capability Quoter needs
final class Quoter {
    private final PriceLookup prices;                          // not a 20-method CatalogService
    Quoter(PriceLookup prices) { this.prices = Objects.requireNonNull(prices); }
    long quoteMinor(List<String> skus) { return skus.stream().mapToLong(prices::priceMinor).sum(); }
}
```
- **FAIL: a test that must stub 15 methods to exercise one.** That is the interface telling you it is
  too fat — the client depends on a surface it never touches. One class may *implement* several role
  interfaces; a client should *depend on* only the one it uses.
- This is the consumer-defined seam from `project-layout.md` → Interfaces & seams, viewed from the
  client's side: keep the port narrow so the fake is trivial and the coupling is minimal.

## DIP — depend on an abstraction, inject the detail
High-level policy depends on an interface; the concrete detail arrives through the constructor. The
**direction** is the whole point: the consumer *owns* the port (`OrderRepository`), the adapter
implements it, so the dependency arrow points at the abstraction — not at Postgres.
- **FAIL: `new PostgresOrderRepository()` inside `OrderService`.** The policy now depends on the
  detail: you can't test it without a database and can't swap the store without editing the policy.
  The composition root (`main` / one `@Configuration`) is the *only* place that names concretes.
- The constructor-injection mechanics — `final` fields, `Objects.requireNonNull`, no field injection,
  Spring auto-wiring a single constructor — are in `project-layout.md` → Constructor injection. Don't
  restate them; DIP is the *why* (invertible, testable, swappable), that file is the *how*.

## Composition over inheritance
Default to composition; reach for inheritance only for a **genuine, stable is-a whose base contract
you fully honor.** In Java the composition tools are `record` + `final` + delegation.
```java
interface Cache<K, V> { Optional<V> get(K k); void put(K k, V v); }

final class CountingCache<K, V> implements Cache<K, V> {      // adds counting by DELEGATING…
    private final Cache<K, V> delegate;                       // …to any Cache, not by subclassing one
    private long hits, misses;
    CountingCache(Cache<K, V> delegate) { this.delegate = Objects.requireNonNull(delegate); }
    public Optional<V> get(K k) {
        Optional<V> v = delegate.get(k);
        if (v.isPresent()) hits++; else misses++;
        return v;
    }
    public void put(K k, V v) { delegate.put(k, v); }
    long hits() { return hits; }
}
```
- **FAIL: inheriting to reuse code** — `class PriceList extends ArrayList<Money>`. You just published
  every `List` mutator as `PriceList`'s API and promised `List`'s entire contract forever; a caller
  can `.clear()` your prices. Hold an `ArrayList` in a field and expose the four methods you mean.
  "Inherit to be substituted, not to reuse."
- **The fragile base class problem:** a superclass whose method calls its own overridable methods can
  break every subclass when it changes an internal call — a coupling delegation doesn't create.
- **When inheritance IS right:** a stable base *you own* with a real is-a and a contract subclasses
  honor — a template-method abstract base, or a `sealed` base whose permitted subtypes you control.
  Inheritance across a module boundary you don't own is a standing liability; composition isn't.

## Make illegal states unrepresentable
Parse raw input at the boundary into a type that *cannot* hold an invalid state; downstream code then
never re-checks. Each tool closes off a class of bug:

- **Typed wrapper over a naked `String`/`int`.** `record EmailAddress(String value){…}` /
  `record CustomerId(String value){…}` — validated once at construction, and the compiler now stops
  you passing an order id where a customer id is expected. A method taking `(String, String, String)`
  invites transposed arguments; `(CustomerId, EmailAddress, Sku)` doesn't compile wrong.
- **`record` with a validating canonical constructor** — an invalid instance can't exist:
```java
record EmailAddress(String value) {
    EmailAddress {                                            // runs on every construction path
        Objects.requireNonNull(value, "value");
        if (value.indexOf('@') < 1) throw new IllegalArgumentException("not an email: " + value);
    }
}
```
- **`sealed` interface for a closed set** — the type system enumerates the legal states, and there is
  no room for an illegal combination. This is the direct replacement for the anti-pattern below.
  **FAIL: an enum + nullable fields / boolean flags to model variants** — `status == IN_TRANSIT` with
  `trackingNumber == null` is a *representable-but-illegal* state you must defend against everywhere.
  A `sealed interface Shipment { record InTransit(String tracking, Instant eta) … }` makes the
  tracking number present exactly when — and only when — it means something.
- **`enum`** for a fixed finite set; reserve no silently-valid zero value (mirror `project-layout.md`
  on enum zero-states).
- **`Optional` over `null`** for absence at a *return* boundary — never a field or parameter (see
  `errors.md` → Optional discipline; don't re-argue it here).
- **Parse, don't validate.** A `validate(String): void` throws away what it learned — you still hold a
  bare `String` afterward, so every downstream function re-validates or trusts blindly. A
  `parse(String): EmailAddress` returns a *narrower type that carries the proof*: validated once,
  trusted everywhere after. Push parsing to the edge; keep the core total.

## Immutability, cohesion, encapsulation, Demeter, fail-fast
One cluster, because they reinforce each other:
- **Immutability by default.** Records, `final` fields, defensive copies at the boundary — the
  cheapest correct concurrency story (a value that never changes needs no lock). Mechanics and the
  copy-in/copy-out rule are in `project-layout.md` → Immutability first. For request-scoped context,
  the immutable `ScopedValue` (final in JDK 25) *rebinds rather than mutates* and auto-unbinds at
  scope exit — prefer it to a mutable `ThreadLocal`; see `concurrency.md`.
- **High cohesion, low coupling.** Things that change together live together — package by feature, not
  layer (`project-layout.md` → Layout). A practical coupling metric: **the number of collaborators you
  must construct to test one class.** If a unit test needs six mocks, the class is over-coupled, not
  the test under-built.
- **Encapsulation.** Package-private is the default access level; `public` is a compatibility promise
  you make on purpose (`project-layout.md`). Publish *behavior*, hide *representation* — never return
  an internal mutable collection (that hands callers a key to your invariants).
- **Law of Demeter — talk to friends, not strangers.** **FAIL:
  `order.getCustomer().getAddress().getCountry().getCode()`** — a train wreck that couples the caller
  to four types' shapes and NPEs at any link. Put a method where the data is
  (`order.shippingCountryCode()`) or pass the one value you need, not the whole graph. Tell, don't
  ask.
- **Fail-fast at construction and boundary.** `Objects.requireNonNull(x, "x")` with the parameter
  name, validation in the canonical constructor, config rejected at startup with the offending key
  (`project-layout.md` → Config; `errors.md` → Null-safety at boundaries). The invariant this buys:
  **an object that exists is valid** — no downstream code has to wonder.

## DRY vs YAGNI vs over-engineering
This is the [Scope discipline](../SKILL.md) rule as a design principle: **a principle justifies making
a change small and correct — it never justifies growing the diff for a caller that doesn't exist yet.**

- **DRY is about knowledge, not characters.** Deduplicate a *business rule* that must change in
  lockstep. Do **not** merge two blocks that are identical *by coincidence today* and will diverge
  tomorrow — a false-DRY coupling forces both callers through one path and is worse than the
  duplication it "fixed." Ask "must these change together *by rule*?" before hoisting.
- **YAGNI.** Build for the requirement in front of you. The abstraction added "for future
  flexibility" freezes the wrong seam and is dead weight until — if ever — that future arrives; when it
  does, you'll know the real shape, which is rarely the one you guessed.
- **Name the over-engineering, and don't ship it** (each is a concrete FAIL):
  - an **interface with one implementation and no test fake** — deletes a file's worth of indirection,
    breaks go-to-definition, buys nothing (`project-layout.md` → the reflexive one-impl interface).
  - a **factory or builder for a value you `new` in exactly one place.**
  - a **config knob nobody asked for** — every option is a state to test and a support question.
  - a **type parameter `<T>` used at exactly one type**, or a `strategy`/plugin system for a branch
    that will only ever have two cases.
- The discipline: apply a principle to make the diff *smaller and safer*. If applying it *grows* the
  diff without a second concrete case/caller/impl that needs the seam, you are gold-plating — stop.
  Add the seam when the second case arrives; the exhaustive `switch` or the new failing test will tell
  you it's time.

# Testing — Java

Load this when writing or reviewing tests. Governing rule: **test observable behavior through the
public API, parameterized over a table of cases, with real collaborators wherever they are cheap.**
A test that only asserts on mocks tests the mock, not your code — worse than no test, because it
manufactures confidence and welds the suite to the current call graph.

## Contents
- [Shape, naming, lifecycle](#shape-naming-lifecycle)
- [Parameterized tests — the table-driven form](#parameterized-tests--the-table-driven-form)
- [AssertJ, not assertEquals](#assertj-not-assertequals)
- [Exception paths](#exception-paths)
- [Mockito discipline](#mockito-discipline)
- [Testcontainers over hand-rolled fakes](#testcontainers-over-hand-rolled-fakes)
- [Determinism](#determinism)
- [Concurrency tests](#concurrency-tests)
- [Property-based testing (jqwik)](#property-based-testing-jqwik)
- [Coverage](#coverage)

## Shape, naming, lifecycle
One behavior per test — a failure should name the broken behavior without a debugger. Name it
`method_condition_expectation`; `@DisplayName` carries the sentence, `@Nested` groups by fixture state.
```java
@Nested @DisplayName("with a balance of 5.00")
class WithFiveDollars {
  private final Account account = new Account(Money.ofCents(500));   // fresh instance per test
  @Test void withdrawWithinBalance_debits() {
    account.withdraw(Money.ofCents(200));
    assertThat(account.balance()).isEqualTo(Money.ofCents(300));
  }
}
```
- New instance per test method, so instance fields are the safe fixture; `@BeforeAll` is `static` unless
  `@TestInstance(PER_CLASS)`. FAIL if a test mutates a `@BeforeAll` fixture — order dependence as setup.
- FAIL if a test name contains "and", or the body holds two unrelated assertion groups: that is two tests.
- FAIL if a test depends on execution order — JUnit's order is deterministic but *unspecified*. Surefire
  runs `*Test`/`*Tests`: name external-dependency tests `*IT`, bind to failsafe, keep `mvn test` hermetic.

## Parameterized tests — the table-driven form
A record per case gives typed fields and, via `toString`, a readable case name in the failure report.
```java
record ParseCase(String name, String input, long wantCents, Class<? extends Throwable> wantThrown) {
  @Override public String toString() { return name; }        // becomes the JUnit display name
}
static Stream<ParseCase> parseCases() {
  return Stream.of(
      new ParseCase("whole units", "10",    1000, null),
      new ParseCase("cents",       "10.50", 1050, null),
      new ParseCase("empty",       "",         0, IllegalArgumentException.class),
      new ParseCase("non-numeric", "abc",      0, NumberFormatException.class));
}
@ParameterizedTest @MethodSource("parseCases")
void parse(ParseCase tc) {
  if (tc.wantThrown() != null) {
    assertThatThrownBy(() -> Money.parse(tc.input())).isInstanceOf(tc.wantThrown());
    return;
  }
  assertThat(Money.parse(tc.input()).cents()).isEqualTo(tc.wantCents());
}
```
- `@CsvSource({"10, 1000", "10.50, 1050"})` for purely scalar cases; `@EnumSource(Status.class)` wherever
  code switches over an enum, so adding a constant fails the test instead of silently falling through.
- FAIL if adding a case means editing the test body — then the table is not really the input.

## AssertJ, not assertEquals
**WRONG:** `assertEquals(expected, actual)` — argument order is memorized rather than read, and failure is
`expected: <X> but was: <Y>` with no structural diff. **RIGHT:** `assertThat(actual).isEqualTo(expected)`
reads in call order and prints *where* values diverged: a 20-second diagnosis instead of a 20-minute one.
```java
assertThat(orders).hasSize(2)
    .extracting(Order::id, Order::status)
    .containsExactly(tuple("o-1", "CONFIRMED"), tuple("o-2", "PENDING"));
assertThat(actual).usingRecursiveComparison().ignoringFields("createdAt").isEqualTo(expected);
```
- Add context with `.as("total for order %s", order.id())`; `SoftAssertions.assertSoftly(…)` reports every
  failure in one run when several independent facts about one result matter.
- FAIL if a test's only assertion is `isNotNull()` — that asserts the method returned, not that it is right.

## Exception paths
```java
assertThatThrownBy(() -> account.withdraw(Money.ofCents(900)))
    .isInstanceOf(InsufficientFundsException.class)
    .hasMessageContaining("900")
    .hasNoCause();
assertThat(account.balance()).isEqualTo(Money.ofCents(500));   // post-condition: nothing was debited
var ex = assertThrows(InsufficientFundsException.class, () -> account.withdraw(Money.ofCents(900)));
assertThat(ex.shortfall()).isEqualTo(Money.ofCents(400));      // when you need the object itself
```
- **Always assert the post-condition after the throw** — the expensive bug is the half-applied mutation.
- FAIL if only a broad type is asserted: `isInstanceOf(IllegalArgumentException.class)` also passes for an
  unrelated `NumberFormatException`, and `isInstanceOf(Exception.class)` passes for an NPE thrown by your
  own setup. Pin the message, a typed field, or `.hasRootCauseInstanceOf(...)` too.

## Mockito discipline
The rule: **mock roles, not values, and only types you own.**
- Mock a `PaymentGateway` — a role your code depends on. Never mock a record, DTO, value object,
  collection, `Optional`, or `String`: the real one is cheaper and is the truth.
- **Never mock a type you don't own** (`HttpClient`, JDBC, an AWS/Stripe SDK, `Clock`) — you would encode
  your *guess* at its contract, so a version bump breaks prod while the test stays green. Use the
  library's own double: WireMock, Testcontainers, `Clock.fixed`.
- Prefer a hand-written fake for stateful collaborators: real behavior catches ordering and idempotency
  bugs a per-call stub cannot express.

**WRONG — asserts the mock; still passes when `rename` writes the wrong name:**
```java
when(repo.find("u1")).thenReturn(Optional.of(new User("u1", "Grace")));
new UserService(repo).rename("u1", "Ada");
verify(repo).find("u1");
verify(repo).save(any());
```
**RIGHT — real fake, assertion on resulting state:**
```java
var repo = new InMemoryUserRepository();
repo.save(new User("u1", "Grace"));
new UserService(repo).rename("u1", "Ada");
assertThat(repo.find("u1")).contains(new User("u1", "Ada"));
```
- Verify only interactions that **are** the behavior — "charged exactly once", "never charged twice"
  (`verify(gw, never())`), or `ArgumentCaptor` when *what* was sent is the point. Verifying getters and
  queries makes the test a transcript of the implementation and blocks every safe refactor.
- Keep `@ExtendWith(MockitoExtension.class)` strict stubs — an unused stub failing the build is the tool
  telling you the stub is dead; do not downgrade to `LENIENT`. Never `spy()` the class under test or use
  `CALLS_REAL_METHODS` on it: a half-real object tests neither half, and the mocked half hides the bug.

## Testcontainers over hand-rolled fakes
H2 standing in for Postgres accepts SQL that Postgres rejects and skips the constraint, isolation, `JSONB`,
and upsert behavior you depend on — it green-lights precisely the change that breaks production.
```java
@Testcontainers
class OrderRepositoryIT {
  @Container static final PostgreSQLContainer<?> PG = new PostgreSQLContainer<>("postgres:16-alpine");
  @BeforeAll static void migrate() {
    Flyway.configure().dataSource(PG.getJdbcUrl(), PG.getUsername(), PG.getPassword()).load().migrate();
  }
}
```
- `static` container = one per class (fast); instance field = one per test (isolated, slow). Default to
  static plus a per-test reset in `@BeforeEach`, and pin the image tag — `latest` makes CI unreproducible.
- For HTTP dependencies use WireMock, not a mocked client: it exercises your serialization, timeout, and
  retry code, all of which a mocked client skips entirely.

## Determinism
- Inject `java.time.Clock`. FAIL on any `Instant.now()`/`LocalDate.now()` inside a unit a test must pin.
  ```java
  final class TokenIssuer {
    private final Clock clock;
    TokenIssuer(Clock clock) { this.clock = clock; }
    Token issue(Duration ttl) { var now = clock.instant(); return new Token(now, now.plus(ttl)); }
  }
  // test: new TokenIssuer(Clock.fixed(Instant.parse("2026-01-01T00:00:00Z"), ZoneOffset.UTC))
  ```
  To move time forward, advance a mutable test `Clock` subclass — never sleep.
- **Never `Thread.sleep` to synchronize**: too short is flaky, too long is slow, a loaded CI box gives both.
  Use a `CountDownLatch`/`CompletableFuture` when you control the signal, `Awaitility` when you can only
  observe the effect.
  ```java
  await().atMost(Duration.ofSeconds(5)).pollInterval(Duration.ofMillis(20))
         .untilAsserted(() -> assertThat(repo.find("o-1")).isPresent());
  ```
- Pin ambient state (UTC, `Locale.ROOT`, UTF-8) and seed randomness, logging the seed — what is left to
  the default passes on your laptop and fails in CI, which teaches the team to distrust the suite.

## Concurrency tests
```java
@Test @Timeout(10)
void incrementIsAtomicUnderContention() {
  var counter = new Counter();
  int threads = 64, perThread = 1_000;
  var start = new CountDownLatch(1);                              // makes them contend at one instant
  try (var pool = Executors.newVirtualThreadPerTaskExecutor()) {  // Java 21+; close() awaits every task
    for (int i = 0; i < threads; i++) pool.submit(() -> { start.await();
      for (int j = 0; j < perThread; j++) counter.increment(); return null; });
    start.countDown();
  }
  assertThat(counter.value()).isEqualTo((long) threads * perThread);
}
```
- Without the latch the tasks run essentially serially and prove nothing. FAIL on a "concurrency test"
  that never forces overlap. `@Timeout` on anything that can hang — a hung test stalls the whole pipeline.
- A green concurrency test is weak evidence: one interleaving happened to work. For publication-safety or
  lock-free claims use jcstress; otherwise design the sharing away.

## Property-based testing (jqwik)
Every parser, serializer, encoder, or normalizer gets a round-trip property. Examples find the bugs you
thought of; properties find the ones you didn't, and shrink the counterexample to a minimal input.
```java
@Property
void formatThenParseRoundTrips(@ForAll @LongRange(min = -1_000_000, max = 1_000_000) long cents) {
  var original = Money.ofCents(cents);
  assertThat(Money.parse(original.format())).isEqualTo(original);
}
@Property
void parseFailsOnlyAsDocumented(@ForAll String anyInput) {
  try { Money.parse(anyInput); }
  catch (IllegalArgumentException expected) { /* the only failure mode the contract allows */ }
}
```
jqwik records the failing seed so the regression reproduces. Surefire's default includes skip
`*Properties` classes — add the include or name the class `*Test`, or the suite silently never runs.

## Coverage
Coverage is a floor, not a target — 100% line coverage with zero assertions is achievable and worthless.
Set a JaCoCo minimum on new code, then spend review attention on what it cannot see:
- Every `catch` and error branch is *exercised*, not merely traversed — the untested branch is where the
  incident lives, because it is the code that only runs on the worst day. Boundaries too: empty, one,
  max, overflow, and null wherever null is legal.
- FAIL if coverage is high but no test makes a dependency fail, time out, or return a partial result.
- FAIL on assertion-free tests — "it didn't throw" must be stated with
  `assertThatCode(...).doesNotThrowAnyException()`, not merely implied by a bare call.

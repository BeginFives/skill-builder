# Databases (JDBC / JPA)

Load this when touching a SQL database. Governing rule: **the pool is bounded and explicitly
configured, every statement is a `PreparedStatement` with placeholders, every JDBC handle is acquired
in a try-with-resources header, and every transaction has one boundary you can point at.** Nearly
every database outage traces to a violation of one of those four.

## Contents
- [Pool configuration](#pool-configuration)
- [try-with-resources: every handle](#try-with-resources-every-handle)
- [Parameterized queries only](#parameterized-queries-only)
- [Transactions in plain JDBC](#transactions-in-plain-jdbc)
- [@Transactional: the four silent failures](#transactional-the-four-silent-failures)
- [N+1, lazy loading, open-session-in-view](#n1-lazy-loading-open-session-in-view)
- [Batch writes](#batch-writes)
- [Optimistic locking](#optimistic-locking)
- [Map DB errors to domain errors](#map-db-errors-to-domain-errors)

## Pool configuration
A `DataSource` is a pool, not a connection. Configure it at startup — the defaults are for demos:
```java
var cfg = new HikariConfig();
cfg.setJdbcUrl(jdbcUrl); cfg.setUsername(user); cfg.setPassword(password);
cfg.setPoolName("orders-primary");     // appears in metrics and in leak-detection stack traces
cfg.setMaximumPoolSize(20);            // TOTAL = this x instances, must fit inside db max_connections
cfg.setMinimumIdle(20);                // == max: fixed-size pool, no churn, no cold-start latency
cfg.setConnectionTimeout(2_000);       // fail fast when saturated; MUST be < the caller's SLA
cfg.setMaxLifetime(600_000); cfg.setKeepaliveTime(120_000); // stay under the DB/LB idle kill; ping idle
cfg.setLeakDetectionThreshold(10_000); // logs the stack that borrowed and never returned it
cfg.setAutoCommit(false);              // transactions are explicit; no accidental per-statement commit
DataSource ds = new HikariDataSource(cfg);
```
**Size the pool to the database, not to your thread count** — a connection is a backend process, so
8 instances x 20 must fit `max_connections` with headroom for migrations, replicas, and a human with
`psql`. Small pools are *faster* under load: 20 connections executing serially beat 200 thrashing the
DB scheduler. Keep `connectionTimeout + queryTimeout` under the caller's deadline, or it gives up
while you still hold the connection.

**The unbounded-pool outage.** Any pool that grows without bound (`DriverManager.getConnection()` per
request, DBCP `maxTotal=-1`, `maximumPoolSize=500` set to "fix" timeouts) moves the queue out of your
app and into the database; the DB hits `max_connections` and *every* service sharing it fails to
connect, healthy ones included. **FAIL if the fix for `SQLTransientConnectionException: Connection is
not available` is a bigger pool** — demand exceeds DB capacity, and a short `connectionTimeout` sheds
it visibly instead.

**Virtual threads (21+):** JDBC blocks, so the pool *is* your concurrency limiter — 10k virtual
threads queue on 20 connections and `connectionTimeout` bounds that queue. On 21 a driver's
`synchronized` around socket I/O pins the carrier thread (JDK 24+, JEP 491, removes that); never grow
the pool to "keep virtual threads busy".

## try-with-resources: every handle
A borrowed connection that is never returned is the classic leak: the pool drains, then every request
fails with a connection timeout while the DB sits idle.
```java
try (Connection c = ds.getConnection(); PreparedStatement ps = c.prepareStatement(BY_CUSTOMER)) {
  ps.setString(1, customer);
  ps.setInt(2, limit);
  ps.setQueryTimeout(2);   // seconds: a slow query must not pin a pooled connection indefinitely
  ps.setFetchSize(500);    // bound the rows the driver materializes for large result sets
  try (ResultSet rs = ps.executeQuery()) {
    var out = new ArrayList<Order>();
    while (rs.next()) {
      out.add(new Order(rs.getLong("id"), rs.getString("customer"), rs.getLong("amount_cents"),
          rs.getObject("shipped_at", OffsetDateTime.class))); // NULL-safe; getLong returns 0 for NULL
    }
    return out;
  }
}
```
- **FAIL if** a `Connection`/`Statement`/`ResultSet` is assigned outside a try-with-resources header —
  a `finally { rs.close(); }` chain is not equivalent and swallows suppressed exceptions.
- **FAIL if** a method that *receives* a `Connection` closes it: whoever opens it closes it, or a
  helper hands the caller's connection back to the pool mid-transaction.
- **FAIL if** a `Stream<T>`-returning query is consumed outside its transaction or its
  try-with-resources — the cursor and its connection stay open.
- Nullable columns: `rs.getObject(col, Long.class)` returns `null`; `getLong`/`getInt` return `0` for
  NULL and quietly corrupt data.

## Parameterized queries only
Values are placeholders. String-built SQL is injection — not for internal callers, not for "it's only
an int": `// WRONG: executeQuery("SELECT ... WHERE customer = '" + name + "'")`. Identifiers can't be
parameterized, so gate them on a fixed allowlist:
```java
static final Set<String> SORTABLE = Set.of("id", "created_at", "amount_cents");
if (!SORTABLE.contains(requested)) throw new IllegalArgumentException("unsortable column");
String sql = BASE + " ORDER BY " + requested;  // safe ONLY because `requested` is from a fixed set
```
Same rule in JPQL/HQL — `:named` parameters, never concatenation; a concatenated `@Query` is
injectable exactly like JDBC. Every unbounded query needs `LIMIT`/`Pageable`: `findAll()` on a growing
table is a future OOM.

## Transactions in plain JDBC
`setAutoCommit(false)`, do the work, commit; every other path rolls back explicitly.
```java
try (Connection c = ds.getConnection()) {
  c.setAutoCommit(false);
  try (var debit = c.prepareStatement("UPDATE accounts SET cents=cents-? WHERE id=? AND cents>=?");
       var credit = c.prepareStatement("UPDATE accounts SET cents=cents+? WHERE id=?")) {
    debit.setLong(1, cents); debit.setLong(2, from); debit.setLong(3, cents);
    if (debit.executeUpdate() != 1) {          // 0 rows == insufficient funds, NOT success
      throw new IllegalStateException("insufficient funds: " + from);
    }
    credit.setLong(1, cents); credit.setLong(2, to);
    credit.executeUpdate();
    c.commit();
  } catch (SQLException | RuntimeException e) {
    c.rollback();
    throw e;
  }
}
```
**FAIL if** `executeUpdate()`'s row count is ignored — a conditional `UPDATE` that matched nothing is
the most common silent business-logic bug. A transaction holds row locks *and* a pool connection, so
keep it short and **never do remote I/O inside one**: that multiplies a slow dependency into pool
exhaustion.

## @Transactional: the four silent failures
All four fail *silently*: the code runs, the annotation does nothing, you find out during an incident.
**1. Self-invocation.** `@Transactional` is a proxy; `this.method()` never crosses it.
```java
// WRONG — settle() runs with NO transaction: this.settle() bypasses the proxy entirely
public void importAll(long batchId) { this.settle(batchId); }
@Transactional void settle(long batchId) { ... }
// RIGHT — cross a bean boundary (ledger is an injected bean), so the call goes through the proxy
public void importAll(long batchId) { ledger.settle(batchId); }
// RIGHT — or take the boundary programmatically, with no proxy involved at all
txTemplate.executeWithoutResult(status -> ledger.settle(batchId));
```
**2. Visibility.** `@Transactional` on a `private` method is never applied, and `final` methods or
classes can't be advised by CGLIB — public methods on a bean are the only form that works everywhere.
**3. Checked exceptions commit.** Default rollback is `RuntimeException`/`Error` only, so a checked
exception thrown out of the method **commits**; use `@Transactional(rollbackFor = Exception.class)`.
**4. Swallowing an inner failure.** Once any participant marks the transaction rollback-only, catching
the exception and returning normally yields `UnexpectedRollbackException` at commit — let it
propagate, or use `Propagation.REQUIRES_NEW` for genuinely independent work.

Boundaries live at the **service** layer, not the repository — one business operation is one
transaction, and repository-level transactions make a two-step operation non-atomic. Reads get
`@Transactional(readOnly = true)` (skips dirty-check/flush, allows replica routing); writes get a
`timeout`. Anything that must survive a rollback (events, metrics, outbox sends) publishes *after*
commit, never inside the method.

## N+1, lazy loading, open-session-in-view
The N+1 SELECT is the most common Java performance incident: one query for the parents, then one more
per parent the moment an association is touched.
```java
@Entity @Table(name = "orders")
class OrderRow {
  // SEQUENCE, not IDENTITY: IDENTITY forces a round trip per insert and disables JDBC batching.
  @Id @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "orders_seq")
  @SequenceGenerator(name = "orders_seq", sequenceName = "orders_seq", allocationSize = 50)
  Long id;
  @Version long version;
  // MUST be explicit: @ManyToOne/@OneToOne default to EAGER and fan out every query
  @ManyToOne(fetch = FetchType.LAZY) @JoinColumn(name = "customer_id") CustomerRow customer;
  // @BatchSize turns N round trips into N/100 where a fetch join can't apply
  @OneToMany(mappedBy = "order", fetch = FetchType.LAZY) @BatchSize(size = 100)
  List<LineRow> lines = new ArrayList<>();
}

interface OrderRepository extends JpaRepository<OrderRow, Long> {
  @EntityGraph(attributePaths = {"customer", "lines"})    // declarative fix: one query
  List<OrderRow> findByCustomerId(long customerId);

  @Query("select distinct o from OrderRow o join fetch o.lines where o.id in :ids")
  List<OrderRow> findAllWithLines(@Param("ids") Collection<Long> ids);
}
```
**FAIL if** a collection `join fetch` is combined with `Pageable` — Hibernate loads *every* matching
row and paginates in memory. Page the IDs first, then fetch by ID list (or use `@BatchSize`).

**`LazyInitializationException` means an entity escaped its transaction.** The fix is not a wider
session; it is projecting inside the boundary, so records leave and managed entities never do:
```java
@Transactional(readOnly = true)
List<OrderView> forCustomer(long id) { return repo.findByCustomerId(id).stream()
    .map(o -> new OrderView(o.id, o.customer.name, o.amountCents, o.lines.size())).toList(); }
```
**Set `spring.jpa.open-in-view=false` explicitly.** Boot defaults it to `true`, holding the
persistence context — and typically a pooled connection — open for the whole request including view
rendering. It *hides* `LazyInitializationException` by letting the serializer trigger lazy loads, so
N+1 queries fire from the presentation layer and connections stay pinned for the full request.

## Batch writes
Chunk, flush the tail, and confirm the driver actually batches:
```java
try (PreparedStatement ps = c.prepareStatement(
    "INSERT INTO orders(id, customer, amount_cents) VALUES (?, ?, ?)")) {
  int n = 0;
  for (Order o : orders) {
    ps.setLong(1, o.id()); ps.setString(2, o.customer()); ps.setLong(3, o.cents());
    ps.addBatch();
    if (++n % 500 == 0) ps.executeBatch();  // chunk: one giant batch blows heap and holds locks
  }
  ps.executeBatch();                        // flush the tail — omitting this silently drops rows
}
```
`addBatch` is a lie unless the driver rewrites it: PostgreSQL needs `reWriteBatchedInserts=true`,
MySQL `rewriteBatchedStatements=true` in the JDBC URL, or you still pay a round trip per row;
`BatchUpdateException.getUpdateCounts()` tells you which rows failed. For JPA set
`hibernate.jdbc.batch_size`, `order_inserts=true`, `order_updates=true`, and flush+clear on the same
interval — without `clear()` the persistence context retains every entity and OOMs:
`em.persist(row); if (i > 0 && i % BATCH == 0) { em.flush(); em.clear(); }`

## Optimistic locking
A read-modify-write without `@Version` is a lost update. `@Version` makes every UPDATE
`... WHERE id = ? AND version = ?`; a 0-row update throws `OptimisticLockException` (Spring:
`ObjectOptimisticLockingFailureException`). **Retry outside the transaction** — the conflict surfaces
at flush/commit, *after* the annotated method body returned, so a `try/catch` inside it never sees it:
```java
for (int attempt = 1; ; attempt++) {                   // OUTSIDE any @Transactional method
  try { ledger.settle(batchId); return; }              // ledger.settle is the transactional bean
  catch (ObjectOptimisticLockingFailureException e) { if (attempt == 3) throw e; }
}
```
Reach for `@Lock(LockModeType.PESSIMISTIC_WRITE)` (with a lock timeout) only when contention makes
retrying costlier than blocking — pessimistic locks serialize throughput and deadlock.

## Map DB errors to domain errors
Nothing above the repository should see `java.sql`. Translate at the boundary so callers decide
*retry / conflict / give up* without parsing vendor codes:
```java
public OrderStoreException translate(String op, SQLException e) {
  StoreError kind = switch (e) {
    case SQLTimeoutException t -> new Unavailable(op, t);
    case SQLTransientConnectionException t -> new Unavailable(op, t); // pool exhausted / no route
    default -> switch (String.valueOf(e.getSQLState())) {
      case "23505", "23000" -> new Conflict(op, e);    // unique violation
      case "40001", "40P01" -> new Retryable(op, e);   // serialization failure / deadlock
      default -> new StoreFailure(op, e);
    };
  };
  return new OrderStoreException(kind, e);
}
```
With `sealed interface StoreError permits Conflict, Retryable, Unavailable, StoreFailure`, callers
switch exhaustively and the compiler catches a new case. Retry **only** `Retryable`/`Unavailable`,
only for idempotent operations, with jittered backoff — retrying a unique violation just burns the
pool. Always keep the `SQLException` as the cause (`getSQLState()`/`getErrorCode()` are what make an
incident diagnosable), and never log SQL with its bound parameter values: that is how PII reaches the
log index.

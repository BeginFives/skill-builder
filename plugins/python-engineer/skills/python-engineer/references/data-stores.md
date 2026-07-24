# Databases (DB-API, SQLAlchemy 2.0, Alembic)

Load this when touching a SQL database. Governing rule: **the pool is bounded, every query is
parameterized, and every transaction has an explicit boundary owned by exactly one caller.** Nearly
every database incident is an unbounded pool, an interpolated string, or a transaction nobody owned.

## Contents
- [Parameterize every query](#parameterize-every-query)
- [Connection & cursor lifetime](#connection--cursor-lifetime)
- [Pool configuration](#pool-configuration)
- [Session lifecycle & transaction boundary](#session-lifecycle--transaction-boundary)
- [expire_on_commit & DetachedInstanceError](#expire_on_commit--detachedinstanceerror)
- [N+1 SELECTs and result-set size](#n1-selects-and-result-set-size)
- [Async: one connection per task](#async-one-connection-per-task)
- [Migrations (Alembic)](#migrations-alembic)
- [Map driver errors to domain errors](#map-driver-errors-to-domain-errors)

## Parameterize every query
Values go through the driver as parameters; the SQL text is a constant. **FAIL if any SQL string is
built with an f-string, `%`, `.format()`, or `+`** — that is injection, and the one review finding with
no acceptable justification.
```python
# WRONG — verified: with hostile = "1 OR 1=1" this returns every row in the table.
cur.execute(f"SELECT name FROM users WHERE id = {user_id}")
# RIGHT
cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))          # psycopg / pymysql
cur.execute("SELECT name FROM users WHERE org = %(org)s", {"org": org})  # named form
```
The placeholder is driver-specific — read `driver.paramstyle`, don't guess. Verified: `sqlite3` is
`qmark` (`?` / `:name`), `psycopg` and `pymysql` are `pyformat` (`%s` / `%(name)s`), `oracledb` is
`named` (`:name`), and `asyncpg` isn't DB-API at all (`$1`, `$2`).
`%s` is a *placeholder*, not printf — `cur.execute("… = %s" % value)` is the injection, not the fix.
Identifiers (table/column/sort direction) can't be parameterized: check them against a frozen
allowlist first, then interpolate only the allowlisted literal —
`if order_by not in SORTABLE: raise ValueError(...)`.
`[TOOL: ruff check --select S608]` flags both f-string and `%`-built SQL — enable it in `pyproject.toml`.

## Connection & cursor lifetime
Acquire from a pool, never `connect()` per query on a request path (TCP + TLS + auth every call). Use
`with` for release on every path — but know what `with` means for *your* driver, because it differs:
`with sqlite3.connect(…)` commits/rolls back and **does not close**; `with psycopg.connect(…)` commits
*and* closes; `with pool.connection()` commits and returns the connection to the pool; `with
conn.cursor()` closes only the cursor. **FAIL if code relies on `with conn` to close a `sqlite3`
connection** — the handle leaks. On 3.12+, `sqlite3.connect(..., autocommit=False)` gives real PEP 249
semantics (the legacy default implicitly commits DDL and opens transactions only for DML).

## Pool configuration
An unconfigured pool is an outage waiting for traffic: it opens connections until the server's
`max_connections` is gone, and then *every* service on that database fails, not just yours:
```python
engine = create_engine(
    dsn,
    pool_size=20,        # steady-state conns per process
    max_overflow=10,     # burst headroom; hard ceiling = pool_size + max_overflow
    pool_timeout=5,      # FAIL fast when saturated — default 30s outlives your request budget
    pool_pre_ping=True,  # cheap liveness check; kills "server closed the connection" after failover
    pool_recycle=1800,   # < the DB/proxy idle timeout, so you never hand out a dead conn
)
```
Budget `(pool_size + max_overflow) × processes × replicas` under the server's `max_connections`, plus
headroom for migrations and admin sessions. Exhaustion is loud and specific — verified:
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 2 overflow 0 reached`. In an incident that means a
leaked session or a transaction held across a network call, **not** a pool that needs to be bigger.
Same knobs elsewhere: `ConnectionPool(dsn, min_size=5, max_size=20, max_lifetime=1800, timeout=5.0)`
(psycopg), `create_pool(dsn, min_size=5, max_size=20, command_timeout=5.0)` (asyncpg). Behind a
transaction-mode pooler (PgBouncer) or in Lambda use `poolclass=NullPool` — a client pool in front of
a server pool double-counts, and prepared statements break across pooled backends.

## Session lifecycle & transaction boundary
One session per request/unit of work, injected, never a module-level global. **FAIL if a `Session` is
created at import time or shared across requests** — its identity map grows forever and one request's
rollback discards another's pending state.
```python
SessionLocal = sessionmaker(engine, expire_on_commit=False)

def get_session() -> Iterator[Session]:  # one per request: commit on success, rollback on error
    with SessionLocal() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

SessionDep = Annotated[Session, Depends(get_session)]  # FastAPI; same shape as a plain @contextmanager

@app.get("/users/{user_id}")                                  # the handler declares the dependency
def read_user(user_id: int, session: SessionDep) -> UserOut:  # …and never commits or closes anything
    return UserOut.model_validate(load_user(session, user_id))
```
- The **outermost** caller commits. Repository functions `flush()` (to get IDs and fire constraints)
  and never `commit()` — a commit buried in a helper splits one unit of work into two. Spell a nested
  boundary out with `with session.begin():`, or `begin_nested()` for a rollback-able savepoint.
- Keep transactions short and **never** make an HTTP call inside one — it pins a pooled connection and
  holds row locks for the length of someone else's outage.
- `scoped_session` (thread-local) is legacy and outright wrong under asyncio, where one thread
  interleaves many requests. Use explicit DI.

## expire_on_commit & DetachedInstanceError
By default `commit()` expires every loaded instance, so the next attribute access re-SELECTs. If the
session is already closed, that access raises `DetachedInstanceError` — Python's
`LazyInitializationException`, and it fails in the *serializer*, after your handler "succeeded":
```python
with SessionLocal() as s:
    org = s.get(Org, 1)
members = org.users   # DetachedInstanceError — relationship never loaded, session is gone (verified)
```
Fix by choosing what leaves the session, not by widening the session's lifetime:
`sessionmaker(expire_on_commit=False)` stops the post-commit refresh storm; eager-load the
relationships you will actually touch (below); best, convert to a DTO/`dataclass`/Pydantic model
**inside** the session, so no ORM instance escapes and a caller cannot trigger a load at all.

## N+1 SELECTs and result-set size
A loop over parents touching a lazy relationship emits one query per parent. Measured, 2 orgs:
**lazy = 3 SELECTs, `selectinload` = 2** — at 500 rows that is 501 round trips.
```python
# WRONG: for org in s.scalars(select(Org)):  ->  org.users fires a SELECT per iteration
stmt = select(Org).options(selectinload(Org.users))   # 1 extra SELECT … WHERE id IN (…)
stmt = select(User).options(joinedload(User.org))     # single JOIN; best for many-to-one
```
- `selectinload` for collections (safe with `LIMIT`/`OFFSET`); `joinedload` for scalar many-to-one —
  on a collection it multiplies rows and needs `.unique()` on the result.
- Make it a test failure, not a production surprise: `lazy="raise"` on the relationship, or
  `.options(raiseload("*"))` in a fixture, turns an accidental lazy load into an exception.
- **FAIL if an unbounded query is materialized** — `.all()` with no `LIMIT` is an OOM with a waiting
  period. Stream it: `select(User).execution_options(yield_per=500)` (verified over 2000 rows).

## Async: one connection per task
A `Session`/connection is a single logical cursor. **FAIL if one session is used from two concurrent
tasks** — verified failure: `InvalidRequestError: This session is provisioning a new connection;
concurrent operations are not permitted`. Create the session *inside* the task:
```python
engine = create_async_engine(dsn, pool_size=20, max_overflow=10, pool_pre_ping=True, pool_recycle=1800)
Sessions = async_sessionmaker(engine, expire_on_commit=False)

async def fetch_email(user_id: int) -> str | None:
    async with Sessions() as session:                 # one session per task — never pass one in
        async with asyncio.timeout(5):                # 3.11+: bound the DB call, not just the request
            return await session.scalar(select(User.email).where(User.id == user_id))

async with asyncio.TaskGroup() as tg:                 # 3.11+: structured, cancels siblings on failure
    tasks = [tg.create_task(fetch_email(i)) for i in ids]
```
- Lazy loads are illegal under async: touching an unloaded attribute raises `MissingGreenlet`
  (verified). Eager-load it, or `await session.refresh(obj, ["users"])`. Install `sqlalchemy[asyncio]`
  — the async engine needs `greenlet` and dies at first connect without it.
- Raw asyncpg: `async with pool.acquire() as conn, conn.transaction():` — one task owns that
  connection for the whole transaction and releases it on every path; set `command_timeout` so a slow
  statement cannot outlive the request.

## Migrations (Alembic)
Schema changes are versioned, reviewed, forward-only files applied by `alembic upgrade head` in a
deploy step — **FAIL if application code runs `create_all()` or ad-hoc `ALTER` at boot** (racy across
replicas, invisible in review, no rollback story). Verified end-to-end against SQLite:
```python
revision: str = "0001"             # this file's id; down_revision chains it to its parent
down_revision: str | None = None

def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)  # unique = the real constraint
```
- `alembic revision --autogenerate` drafts; **you review the diff** — it turns a rename into drop+add
  (deleting the data) and misses type widenings and server defaults.
- Never edit an applied migration; roll forward with a new revision. Expand/contract anything the
  running old version still reads: add nullable → backfill → dual-write → NOT NULL → drop, per deploy.
- Postgres index builds lock writes: `op.create_index(..., postgresql_concurrently=True)` in a
  non-transactional revision. Keep data backfills out of DDL migrations — batch them separately.

## Map driver errors to domain errors
The repository is where driver exceptions stop. **FAIL if `DBAPIError`/`IntegrityError` escapes to a
handler**: it couples every caller to the driver, and its `str()` carries the SQL and often the
parameter values — straight into a log line as PII.
```python
def create_user(session: Session, email: str, org_id: int) -> User:
    user = User(email=email, org_id=org_id)
    session.add(user)
    try:
        session.flush()                        # the constraint fires here, inside our boundary
    except IntegrityError as exc:
        session.rollback()                     # the session is unusable until you do
        raise DuplicateEmail(email) from exc   # `from` keeps the cause for the log, not the caller
    return user
```
- SQLAlchemy wraps the driver exception; `exc.orig` is the real one. Discriminate on that, never on
  message text: `isinstance(exc.orig, psycopg.errors.UniqueViolation)` (SQLSTATE `23505`).
- Raw psycopg: `UniqueViolation` → conflict; `psycopg.OperationalError` → unavailable/retryable. Pool
  exhaustion arrives as `psycopg_pool.PoolTimeout`, a subclass of `OperationalError` (verified), so
  that one `except` already covers it — a 503, not a 500 with a stack trace.
- After **any** exception in a transaction, roll back before reuse: a session handed back to the pool
  mid-transaction poisons the next request that borrows it.

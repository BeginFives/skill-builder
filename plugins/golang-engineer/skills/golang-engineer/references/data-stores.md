# Databases (database/sql)

Load this when touching a SQL database. Governing rule: **the pool is bounded and configured, every
query carries the request `ctx`, every query is parameterized, and every transaction is rolled back on
all paths.** Most production database incidents trace to a violation of one of those four.

## Contents
- [Pool configuration](#pool-configuration)
- [Always *Context](#always-context)
- [Parameterized queries only](#parameterized-queries-only)
- [Transactions](#transactions)
- [Rows: close & check Err](#rows-close--check-err)
- [Nullable columns](#nullable-columns)
- [Migrations](#migrations)

## Pool configuration
`*sql.DB` is a pool, not a connection — an unconfigured pool defaults to unlimited connections and will
exhaust the database's `max_connections` under load. Configure it explicitly at startup:
```go
db, err := sql.Open("pgx", dsn) // Open does not connect; it validates the DSN
if err != nil {
    return nil, fmt.Errorf("open db: %w", err)
}
db.SetMaxOpenConns(25)                  // cap total conns; size to DB max_connections / replica count
db.SetMaxIdleConns(25)                  // keep idle == open to avoid churn under steady load
db.SetConnMaxLifetime(5 * time.Minute)  // recycle conns so LB/failover changes propagate
db.SetConnMaxIdleTime(5 * time.Minute)  // reclaim idle conns
if err := db.PingContext(ctx); err != nil { // actually connect, with a deadline
    return nil, fmt.Errorf("ping db: %w", err)
}
```

## Always *Context
The rubric demands `ctx` everywhere — SQL is where it most often gets dropped. Use the `…Context`
variants so a request deadline/cancel actually reaches the database:
```go
row := db.QueryRowContext(ctx, "SELECT name FROM users WHERE id = $1", id)
_, err := db.ExecContext(ctx, "UPDATE users SET name = $1 WHERE id = $2", name, id)
rows, err := db.QueryContext(ctx, "SELECT id, name FROM users WHERE org = $1", org)
```
Never `db.Query`/`db.Exec`/`db.QueryRow` (the ctx-less forms) on a request path.

## Parameterized queries only
Pass values as placeholders (`$1`/`?`), **never** build SQL with `fmt.Sprintf`/string concatenation —
that is SQL injection. Identifiers that must be dynamic (table/column names) can't be parameterized;
validate them against a fixed allowlist instead of interpolating user input.
```go
// WRONG: db.QueryContext(ctx, "SELECT * FROM users WHERE name = '"+name+"'")
// RIGHT:
rows, err := db.QueryContext(ctx, "SELECT id FROM users WHERE name = $1", name)
```

## Transactions
Begin with ctx, `defer tx.Rollback()` immediately, do the work, then `Commit`. Rollback after a
successful Commit is a harmless no-op, so the defer is always safe and covers every early-return path:
```go
func transfer(ctx context.Context, db *sql.DB, from, to string, cents int64) (err error) {
    tx, err := db.BeginTx(ctx, nil)
    if err != nil {
        return fmt.Errorf("begin: %w", err)
    }
    defer tx.Rollback() // no-op if Commit succeeded; rolls back on any early return/panic-free error

    if _, err = tx.ExecContext(ctx, "UPDATE accounts SET cents = cents - $1 WHERE id = $2", cents, from); err != nil {
        return fmt.Errorf("debit: %w", err)
    }
    if _, err = tx.ExecContext(ctx, "UPDATE accounts SET cents = cents + $1 WHERE id = $2", cents, to); err != nil {
        return fmt.Errorf("credit: %w", err)
    }
    if err = tx.Commit(); err != nil {
        return fmt.Errorf("commit: %w", err)
    }
    return nil
}
```
All statements inside a tx use the `tx.…Context` methods, not `db.…`. Keep transactions short — a long
tx holds locks and a pool connection.

## Rows: close & check Err
Iterating rows leaks a connection if you forget `Close`, and silently drops errors if you forget
`rows.Err()` (the loop ends on error *or* completion — you can't tell which without it):
```go
rows, err := db.QueryContext(ctx, "SELECT id, name FROM users WHERE org = $1", org)
if err != nil {
    return nil, fmt.Errorf("query: %w", err)
}
defer rows.Close()
var users []User
for rows.Next() {
    var u User
    if err := rows.Scan(&u.ID, &u.Name); err != nil {
        return nil, fmt.Errorf("scan: %w", err)
    }
    users = append(users, u)
}
if err := rows.Err(); err != nil { // REQUIRED — surfaces an iteration error vs clean end
    return nil, fmt.Errorf("rows: %w", err)
}
```
The `bodyclose`/`rowserrcheck`/`sqlclosecheck` linters (in `.golangci.yml`) catch missing Close/Err.

## Nullable columns
A NULL scanned into a plain `string`/`int` errors. Use `sql.Null[T]` (Go 1.22+) or the classic
`sql.NullString`/`sql.NullInt64`, and implement `driver.Valuer`/`sql.Scanner` for custom types:
```go
var email sql.Null[string]
_ = row.Scan(&email)
if email.Valid { use(email.V) }
```

## Migrations
Schema changes are versioned, forward-only, reviewed files applied by a migration tool
(golang-migrate, goose, atlas) — never ad-hoc `ALTER` from application code at boot. Each migration is
idempotent-safe to re-run detection via the tool's version table. Roll forward with a new migration
rather than editing an applied one.

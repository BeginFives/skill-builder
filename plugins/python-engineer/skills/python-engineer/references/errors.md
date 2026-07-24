# Error handling — Python

Load this when designing exception types, deciding what to catch, or choosing raise-vs-log. Governing
rule: **catch the narrowest exception you can act on, handle it exactly once, and never break the chain.**

## Contents
- [What to catch](#what-to-catch)
- [Handle exactly once](#handle-exactly-once)
- [Chaining — raise ... from](#chaining--raise--from)
- [Exception hierarchy design](#exception-hierarchy-design)
- [ExceptionGroup and except*](#exceptiongroup-and-except)
- [suppress, EAFP, and hot paths](#suppress-eafp-and-hot-paths)
- [Cleanup — finally and context managers](#cleanup--finally-and-context-managers)
- [assert and exit](#assert-and-exit)
- [Style](#style)

## What to catch
**FAIL on `except:` and `except BaseException:`.** `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`
and `asyncio.CancelledError` are **not** `Exception` subclasses — a bare `except` eats Ctrl-C, eats
`sys.exit()`, and eats task cancellation, so shutdown hangs and cancelled work keeps running.
```python
try:
    run()
except:                      # WRONG — eats SystemExit/CancelledError; the process won't die
    log.error("oops")

try:                         # RIGHT — the narrowest clause you have a distinct response for
    cfg = tomllib.loads(path.read_text())
except OSError as e:
    raise ConfigError(str(path), "unreadable") from e
except tomllib.TOMLDecodeError as e:
    raise ConfigError(str(path), "malformed toml") from e
```
`except Exception` is correct **only** at a boundary that converts a failure into a response, a retry,
or an exit code — request handler, worker-loop iteration, `main()`. Elsewhere it is a smell: a handler
that can't name what it catches can't know what it recovers from. `[TOOL: ruff E722, BLE001]` If you
must catch `BaseException` (last-ditch cleanup), **re-raise it**. And `except Exception` around an
`await` will not trap cancellation — by design; let it propagate (see `concurrency.md`).

## Handle exactly once
On any path an exception is **either** raised onward (with context) **or** logged and converted — never
both. Double-handling scatters one failure across five log lines at three severities, burying the origin.
```python
except OrderError:           # WRONG — logged here AND re-raised, so every layer above logs it again
    log.exception("save failed")
    raise

except OrderError as e:      # RIGHT (mid-layer) — add context, no logging
    raise CheckoutError(f"reserve inventory for {order_id}") from e

# RIGHT (boundary) — logged exactly once, converted, not re-raised.
except OrderNotFoundError as e:
    log.info("order missing", extra={"order_id": e.order_id})
    return Response(404)
except AppError:
    log.exception("checkout failed", extra={"order_id": order_id})   # exception(), not error()
    return Response(500)
```
`log.exception()` (or `log.error(..., exc_info=True)`) only inside an active handler — plain
`log.error(f"{e}")` throws away the traceback, the only part worth having. `[TOOL: ruff TRY400]`
**Swallowing is the same defect wearing a hat:** `except X: pass`, `except X: continue`, and
`except X: log.warning(...)` followed by code that proceeds as if nothing failed. Ignoring a failure
needs a comment saying why; degrading to a default needs a `warning` naming the fallback, or every
dashboard reads it as success. `[TOOL: ruff S110, S112]`
Need context but not a new type? `e.add_note(f"order_id={order_id}")` (3.11+) before re-raising.

## Chaining — raise ... from
Three distinct behaviours; pick deliberately:
| Form | `__cause__` | Traceback reads | Use for |
|---|---|---|---|
| `raise X from e` | `e` | "direct cause of" | every translation across a layer |
| `raise X` inside `except` | `None` (`__context__` set) | "During handling … another exception occurred" | never — that phrasing means *your handler crashed* |
| `raise X from None` | `None`, context suppressed | clean | hiding an implementation detail (a `KeyError` from an internal dict) |
```python
def load(order_id: str) -> Order:
    try:
        return _ROWS[order_id]
    except KeyError:
        raise OrderNotFoundError(order_id) from None  # dict internals are not the caller's problem
```
FAIL if a `raise` inside an `except` has no `from` `[TOOL: ruff B904]`, and never
`raise MyError(str(e))` — that stringifies the cause and drops its traceback. To re-raise unchanged use
bare `raise`: `raise e` re-adds the current frame, listing it twice in the traceback (verified).
`[TOOL: ruff TRY201]`

## Exception hierarchy design
**One base per package**, so a caller can quarantine your library with a single clause:
```python
class OrderError(Exception):
    """Base for every error this package raises."""

class OrderNotFoundError(OrderError):
    def __init__(self, order_id: str) -> None:
        super().__init__(order_id)         # args MUST mirror __init__'s parameters
        self.order_id = order_id

    def __str__(self) -> str:
        return f"order not found: {self.order_id}"

class InvalidOrderError(OrderError, ValueError): ...   # dual-inherit only if the builtin fits exactly
```
- **Carry structured context as attributes, not baked into the message.** *Why: a handler that must
  `re.match` your message string to recover the order id is coupled to your prose.*
- **One subclass per handling decision, not per call site.** If no caller would catch it separately,
  it doesn't need its own class.
- **`super().__init__(*)` must take the same values as `__init__`.** `BaseException.__reduce__` rebuilds
  the exception as `cls(*self.args)`, so passing one formatted message to a two-arg `__init__` makes a
  cross-process re-raise die with `TypeError: __init__() missing 1 required positional argument` —
  verified with `ProcessPoolExecutor`; it hits every multiprocessing/Celery path.
- Name them `XxxError` `[TOOL: ruff N818]`. Never `raise Exception(...)` — nobody can catch that narrowly.

## ExceptionGroup and except*
`asyncio.TaskGroup` raises an **`ExceptionGroup` even when exactly one child failed** — the classic
migration bug is code that still says `except TimeoutError` and now matches nothing (verified).
```python
try:
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(fetch(u)) for u in urls]
except* TimeoutError as eg:
    log.warning("upstream timeouts", extra={"count": len(eg.exceptions)})
except* FetchError as eg:
    raise BatchError("batch fetch failed") from eg
else:
    results = [t.result() for t in tasks]
```
- **Every matching `except*` clause runs** (unlike `except`, where the first wins); a plain
  `except Exception` sees **no** leaf inside a group. Never wrap a single sequential error in a group.
- **SyntaxError, not a lint:** `break`/`continue`/`return` cannot appear in an `except*` block, and one
  `try` cannot mix `except` and `except*` (both verified). Restructure with a flag or a helper function.
- Filter programmatically with `eg.split(TimeoutError)` → `(matched, rest)`, or `eg.subgroup(pred)`.

## suppress, EAFP, and hot paths
```python
with contextlib.suppress(FileNotFoundError):
    cache_path.unlink()          # exactly one statement — the thing you're guarding
```
FAIL if a `suppress` block holds more than the guarded call: it doesn't resume, it **abandons the rest
of the block** (verified — statements after the raise point never run). Never `suppress(Exception)`;
that is a bare except with better PR. **3.12+:** `suppress` also strips matching leaves out of an
`ExceptionGroup` and re-raises the remainder (verified) — another reason to keep the clause narrow.
**EAFP over LBYL for anything shared.** `if path.exists(): open(path)` is a TOCTOU race; so is
`if key in d` across threads, and `if not exists: INSERT`. Do the operation, catch the failure.
**But exceptions are not control flow on hot paths.** Measured here over 1M iterations: `d['k']` 12.8 ns,
the same inside `try` with nothing raised 11.4 ns (**3.11+ zero-cost — never avoid `try` for speed**),
`d.get('z')` miss 15.2 ns, raise+catch `KeyError` **92.9 ns**. One miss is free; a miss per item over
10M rows is a 6× tax — return `None` or a sentinel there.

## Cleanup — finally and context managers
**FAIL on `return`, `break`, or `continue` inside `finally`** — it discards the in-flight exception
entirely (verified: the function returns 1, the `ValueError` is gone). `[TOOL: ruff B012]`; **3.14+** also
warns (PEP 765). Prefer a context manager over hand-rolled try/finally, **`yield` inside the `try`**:
```python
@contextlib.contextmanager
def leased(pool: Pool) -> Iterator[Conn]:
    conn = pool.get()
    try:
        yield conn
    finally:           # omit this try/finally and an exception in the body leaks the lease — verified
        pool.put(conn)
```
Use `contextlib.ExitStack` (or `AsyncExitStack` + `@asynccontextmanager`) when the resource count is
dynamic; it unwinds in reverse on every path. In async code a `finally` that `await`s during
cancellation needs shielding — see `concurrency.md`.

## assert and exit
- **`assert` is not validation.** `python -O` deletes it (verified: the same invalid input sails through),
  so an `assert` guarding external input is a hole, not a check. Assert internal invariants and narrow
  types; `raise ValueError` for anything crossing a boundary. `[TOOL: ruff S101]`
- **`sys.exit()` only at the entrypoint.** In library code it raises `SystemExit` into a caller who never
  asked to die, and any bare `except` upstream swallows it (verified). Libraries raise; `main()` translates:
```python
def main(argv: Sequence[str] | None = None) -> int:
    try:
        run(parse_args(argv))
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":       # the ONLY sys.exit in the package
    sys.exit(main())
```
Uncaught exceptions in a thread or a bare `asyncio` task die in a default handler, not in `main()` —
so a worker's top-level `except Exception` is that unit's boundary, and it must log there.

## Style
- Messages: lowercase, no trailing period, no "failed to" noise, name the subject —
  `"order not found: ord-42"`, not `"Failed to find the order!"`; they get wrapped and concatenated.
- **No secrets, tokens, or PII in exception messages** — they land in logs, tracebacks, and error
  trackers, none of which redact.
- Return early; keep the happy path un-indented, and keep only the raising statement inside `try` so the
  handler can't catch an exception from three lines you never meant to guard.

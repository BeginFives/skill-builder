# Concurrency, cancellation & leak-freedom — Python

Load this when writing `async` code, threads, or process pools — or when fixing a hang, a leak, or work
that silently never finished. The governing rule: **every unit of concurrency has an owner holding a
strong reference, a deadline, and a cancellation path it re-raises.** If you can't name all three, don't
start it. Baseline 3.11+.

## Contents
- [Choosing a concurrency model](#choosing-a-concurrency-model)
- [Task ownership — the weak-reference footgun](#task-ownership--the-weak-reference-footgun)
- [TaskGroup — the default fan-out](#taskgroup--the-default-fan-out)
- [ExceptionGroup and except\*](#exceptiongroup-and-except)
- [Deadlines on every await](#deadlines-on-every-await)
- [The cancellation protocol](#the-cancellation-protocol)
- [gather and wait — the legacy paths](#gather-and-wait--the-legacy-paths)
- [Never block the event loop](#never-block-the-event-loop)
- [Threads, the GIL and processes](#threads-the-gil-and-processes)
- [Queues, backpressure and shutdown](#queues-backpressure-and-shutdown)
- [The leak catalogue](#the-leak-catalogue)

## Choosing a concurrency model
**The bound picks the model — the GIL is why.** I/O-bound work waits, so many can share one thread
(`asyncio`) or a few threads; CPU-bound pure-Python work computes, and the GIL serializes bytecode so
threads give **no** speedup — that needs processes or a native library. Get this wrong and every
downstream micro-optimization is irrelevant. Reach for the first row that fits:

| The situation | Reach for | Not |
|---|---|---|
| **I/O-bound**, many concurrent calls, libraries are async-native | `asyncio` — `TaskGroup` over bare `create_task` | threads (heavier); processes (pointless IPC) |
| I/O-bound but the library **blocks** (`requests`, a sync DB driver, `time.sleep`) | `await asyncio.to_thread(fn, …)` from async, else a `ThreadPoolExecutor` | calling it inline in a coroutine — it stalls the whole loop |
| I/O-bound, small and simple, no async stack in play | a `ThreadPoolExecutor` | rewriting the world to async for a handful of calls |
| **CPU-bound** pure Python | `ProcessPoolExecutor` (worker importable at module level; tune `chunksize`) | threads or async — the GIL means no speedup |
| CPU-bound, heavy numeric | a native lib (`numpy`/`polars`) or a release-the-GIL extension (Cython/Rust) | pure-Python parallelism |
| Fan-out where the **first failure cancels the siblings** | `asyncio.TaskGroup` (3.11+) — raises an `ExceptionGroup`, cancels the rest | `gather()` without `return_exceptions`/`except*` handling |
| **Bound in-flight** work against a scarce resource (a pool, a rate limit) | `asyncio.Semaphore` (async) or a bounded pool | unbounded fan-out |
| Background work that must **outlive the request** | own the task (a strong ref) + a shutdown path, or a separate worker | a bare `create_task` — it can be GC'd mid-flight |

Do not mix a blocking call into the async row: an `async def` that calls anything blocking stalls every
other task on the loop (see [Never block the event loop](#never-block-the-event-loop)). Templates per
bound live in `performance.md`.

## Task ownership — the weak-reference footgun
`asyncio.create_task` returns the **only strong reference**: the loop's registry is a `WeakSet` (still
true in 3.14). A pending task nobody holds gets collected mid-flight — stderr shows `Task was destroyed
but it is pending!` and the work simply never happens. It is *intermittent*, because a timer or
ready-queue handle often roots the task by accident: it survives in dev and vanishes under load.

**FAIL: a bare `create_task(...)`/`ensure_future(...)` whose result is discarded.** `ruff` catches this
via `RUF006`, which is **not** in the default rule set — select it. `TaskGroup` and `gather` root their
children; bare `create_task` does not. Prefer a TaskGroup; when work must outlive the scope, own it:

```python
class BackgroundTasks:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()

    def spawn(self, coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)                        # the loop holds only a WEAK ref
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._log_failure)    # else the error surfaces only at GC, if ever
        return task

    @staticmethod
    def _log_failure(task: asyncio.Task[Any]) -> None:
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.error("background task %s failed", task.get_name(), exc_info=exc)

    async def aclose(self, timeout: float = 5.0) -> None:
        tasks = list(self._tasks)                    # snapshot: done-callbacks mutate the set
        for task in tasks:
            task.cancel()
        _, pending = await asyncio.wait(tasks, timeout=timeout) if tasks else ((), ())
        if pending:
            log.warning("%d task(s) ignored cancellation", len(pending))
```

No tasks at import time and no module-level `asyncio.run` — background work belongs to a component with
`start`/`aclose` wired into the app lifespan.

## TaskGroup — the default fan-out
```python
async def fetch_all(urls: Sequence[str], *, limit: int = 8, timeout: float = 2.0) -> list[Result]:
    sem = asyncio.Semaphore(limit)
    out: list[Result | None] = [None] * len(urls)

    async def one(i: int, url: str) -> None:
        async with sem:                          # bound it: never one task per unbounded input
            out[i] = await fetch(url)            # unique index per task → no shared-write race

    async with asyncio.timeout(timeout):         # whole-fan-out deadline
        async with asyncio.TaskGroup() as tg:    # first failure cancels the siblings
            for i, url in enumerate(urls):
                tg.create_task(one(i, url), name=f"fetch-{i}")
    return [r for r in out if r is not None]
```

Semantics you are relying on (verified on 3.14):
- The first exception cancels every sibling and `__aexit__` **waits** for them — no orphans, ever.
- The group raises `ExceptionGroup` (`BaseExceptionGroup` when a `CancelledError` is inside).
- A child *you* `.cancel()` is **not** an error to the group — that is how you stop workers.
- `tg.create_task()` after the body exits raises `RuntimeError: TaskGroup ... is finished`; spawn only
  from inside, and never let a task handle escape to be awaited later.
- An **outer** `asyncio.timeout` firing surfaces as a plain `TimeoutError`, not an `ExceptionGroup`.

**FAIL: one task per unbounded input** (a task per row of a table). *Why: 100k coroutines each holding a
connection is an OOM plus a thundering herd on the dependency you were trying to protect.*

## ExceptionGroup and except\*
**FAIL: `except SomeError` around a `TaskGroup`** — it does not match; the error arrives wrapped.

```python
try:
    async with asyncio.TaskGroup() as tg:
        ...
except* ConnectionError as eg:      # eg.exceptions: matching leaves from ALL failed children
    metrics.upstream_errors.inc(len(eg.exceptions))
except* ValueError as eg:           # both clauses can fire for the same group — no first-match-wins
    raise BadRequestError(str(eg)) from eg
```

You cannot mix `except*` and plain `except` in one `try`. Do **not** unwrap a group to its first
exception so old handlers keep working — that drops the other failures; use `eg.subgroup`/`eg.split`
deliberately instead.

## Deadlines on every await
**FAIL: any `await` touching network, disk, a subprocess, or a contended lock without an enclosing
deadline.** *Why: a dependency that hangs rather than fails becomes an unbounded pile of tasks, sockets
and memory — the shape of most cascading outages.*

```python
async with asyncio.timeout(2.0):    # 3.11+; cancels the body, raises TimeoutError at this boundary
    resp = await client.get(url)
```

- Nesting composes: an inner deadline firing does not consume the outer one (verified).
- `asyncio.timeout_at(loop.time() + budget)` for an **absolute** deadline propagated down a call chain;
  `cm.reschedule(...)` extends it. *Why: relative timeouts re-arm at every hop, so a 5-deep chain of "2s"
  quietly costs 10s.*
- Prefer the library's phase-aware timeout (`httpx.Timeout(connect=…, read=…)`) with `asyncio.timeout`
  around the whole operation as backstop. *Why: per-phase says which phase broke; the outer one
  guarantees the call returns.* `wait_for(aw, t)` is the older spelling, still fine for one awaitable.
- Catch the builtin `TimeoutError`; `asyncio.TimeoutError` is an alias for it since 3.11.

## The cancellation protocol
`asyncio.CancelledError` inherits `BaseException`, so `except Exception` correctly misses it. That fact is
the only thing protecting cancellation from your error handling — do not defeat it.

- **Re-raise, always.** `except asyncio.CancelledError: pass`/`return` is a FAIL: the task becomes
  uncancellable, so `asyncio.timeout` blows past its deadline and `TaskGroup.__aexit__` never returns.
- **Never `except BaseException:` or `contextlib.suppress(BaseException)` around an await.**
- **Cleanup goes in `finally`, bounded.** One `cancel()` delivers exactly one `CancelledError`, so awaits
  in `finally` *do* run (verified) — but a second cancel or interpreter shutdown cuts them off.
- **Translating a cancel requires `uncancel()`:** if you catch `CancelledError` and return normally, call
  `asyncio.current_task().uncancel()` and re-raise while the count is `> 0`. *Why: otherwise you swallow
  an outer `timeout`/`TaskGroup`'s cancellation and hang it.*
- **`shield()` sparingly** — only for work that must not be interrupted (a commit, a lease release). The
  awaiting side still raises `CancelledError` immediately and the shielded task keeps running, so it needs
  its own strong reference and its own deadline.

```python
async def pump(conn: Conn) -> None:
    try:
        while True:
            await handle(await conn.recv())
    finally:                                       # runs on cancel AND on error
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(CLOSE_TIMEOUT):
                await conn.aclose()                # bounded: cleanup must not hang shutdown
```

## gather and wait — the legacy paths
- `gather(a, b)` — the first exception propagates **while the siblings keep running** (verified: the
  survivor completed long after the caller had handled the error). Orphaned work, orphaned connections.
  **FAIL for fan-out** — use `TaskGroup`.
- `gather(*aws, return_exceptions=True)` never raises; exceptions arrive positionally. **Inspect every
  element** (`isinstance(r, BaseException)`) or it is silent data loss. Legitimate only for best-effort
  fan-out where you want all outcomes (shutdown, cache warm).
- `asyncio.wait(...)` returns `(done, pending)` and **cancelling `pending` is your job** — leaving it is a
  leak. Since 3.11 it rejects coroutines (`TypeError: Passing coroutines is forbidden`); pass Tasks.

## Never block the event loop
**FAIL: `time.sleep`, `requests`/`urllib`, a large `open().read()`, `subprocess.run`, a sync DB driver,
`json.loads` of a 50 MB body, or a tight CPU loop inside a coroutine.** *Why: one blocked coroutine
freezes every other task on that loop — including heartbeats and the timeout timers — so the symptom is a
fleet-wide timeout storm that looks like a network fault.*

`ruff`'s `ASYNC2xx` rules catch the usual suspects **by default** (`ASYNC251` `time.sleep`, `ASYNC210`
blocking HTTP, `ASYNC230` `open`). Run `asyncio.run(main(), debug=True)` in dev: the loop logs
`Executing <Task ...> took 0.204 seconds` for any callback over 100 ms.

```python
_IO_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="blocking-io")
loop = asyncio.get_running_loop()
# run_in_executor takes no kwargs → bind them with functools.partial
digest = await loop.run_in_executor(_IO_POOL, functools.partial(_digest, path, algo="sha256"))
```

- `await asyncio.to_thread(fn, *args)` is the one-liner (it propagates `contextvars`); a named executor is
  better on a hot path because it bounds and labels the threads.
- **A cancelled `to_thread` does not stop the thread** (verified: the worker ran to completion after the
  await was cancelled). A timeout bounds *your wait*, not *the work* — so the pool must be bounded, or one
  slow dependency exhausts it.
- Never call `asyncio.run()` inside a running loop; from a non-loop thread use
  `asyncio.run_coroutine_threadsafe(coro, loop)` (returns a `concurrent.futures.Future`).
- Use `asyncio.Lock`/`Semaphore`, never `threading.Lock` in async code (it blocks the loop). Asyncio
  primitives are not thread-safe; `loop.call_soon_threadsafe` is the only safe cross-thread entry.

## Threads, the GIL and processes
Stated accurately: on the default build the GIL serializes bytecode, so **threads help I/O-bound work and
C extensions that release the GIL (`hashlib`, `zlib`, numpy, most DB drivers), and do nothing for
pure-Python CPU work.** Measured here on 4 CPU-bound jobs: serial 1.48s, 4 threads 1.47s (zero speedup),
4 processes 0.48s.

- 3.13+ ships an experimental free-threaded build (`python3.13t`, PEP 703): opt-in, ABI-incompatible with
  most wheels, and slower single-threaded. Do not plan capacity on it; do not assume threads scale CPU.
- CPU-bound → `ProcessPoolExecutor`. Arguments and returns must be **picklable** (a lambda or local
  function raises `PicklingError: Can't pickle local object`), the target importable at module level, and
  every call pays pickle + IPC — batch with `chunksize`, never per item.
- **`if __name__ == "__main__":` is mandatory**: the child re-imports your module on every platform now —
  macOS/Windows `spawn`, and 3.14 changed the Linux default from `fork` to `forkserver`. Without the guard
  you fork-bomb yourself. Never fork a process that already has threads or a running loop; locks held at
  fork time deadlock the child.
- **FAIL: a module-level executor never shut down** — the interpreter hangs at exit or drops the tail of
  your logs. Use the context manager, or `shutdown(wait=True, cancel_futures=True)`.
- *(3.14, experimental)* `concurrent.futures.InterpreterPoolExecutor` (PEP 734) runs jobs in
  subinterpreters with their own GIL — real CPU parallelism without process IPC. Verified working; a
  candidate, not yet a default.

## Queues, backpressure and shutdown
```python
queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=workers * 2)   # NEVER the unbounded default

async def consume() -> None:
    while True:
        job = await queue.get()
        try:
            await handle(job)
        finally:
            queue.task_done()                    # in finally, or a raising handler hangs join()

async with asyncio.TaskGroup() as tg:
    consumers = [tg.create_task(consume(), name=f"consumer-{i}") for i in range(workers)]
    async for item in source:
        await queue.put(item)                    # blocks when full → backpressure onto the producer
    await queue.join()
    for c in consumers:                          # explicit cancel; a cancelled child is not an error
        c.cancel()
```

**FAIL: `asyncio.Queue()` with no `maxsize`.** *Why: it converts a slow consumer into an OOM and removes
the only signal that would have told you to shed load.* `asyncio.Queue` is not thread-safe; 3.13+ adds
`Queue.shutdown()` as a cleaner alternative to sentinel values.

For process shutdown, install `loop.add_signal_handler(sig, stop.set)` for `SIGINT`/`SIGTERM` (POSIX; on
Windows `signal.signal`), drain in a `finally` under its own `asyncio.timeout`, and close every owned
executor, client and task set there. *Why: an unbounded drain just means the orchestrator SIGKILLs you
mid-write.*

## The leak catalogue
Each is a concrete FAIL, named by the message it prints:
- **`coroutine 'x' was never awaited`** — a dropped `await`; nothing ran. It fires at GC, far from the
  call site, and cannot be caught — but `mypy` flags it statically by default (`unused-coroutine`).
- **`Task was destroyed but it is pending!`** — unreferenced or un-awaited task. Fix with `TaskGroup` or
  the owner set above; cancel **and await** everything in `aclose`.
- **`Task exception was never retrieved`** — a fire-and-forget task raised and nobody looked. Attach a
  logging done-callback, or use a TaskGroup.
- **`Unclosed client session`** — a module-level `httpx.AsyncClient`/aiohttp session never closed. Own it
  in the app lifespan or use `async with` per scope.
- **Unbounded anything** — queue without `maxsize`, `gather` over unbounded input, a cache that only
  grows, an executor left at default `max_workers` on a 96-core box.
- **Executor/threads never shut down** — non-daemon threads block interpreter exit; `atexit` handlers and
  the last log lines are lost.
- **Blocking cleanup** — a `finally` that awaits without a deadline turns a cancel into a hang.

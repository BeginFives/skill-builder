# Python Production Rubric — the checkable "principal engineer" standard

> This is the crown-jewel content for the `python-engineer` skill. Every item is a concrete
> PASS/FAIL gate. A change ships only if it passes every **applicable** item. Sections 1–4
> (task lifecycle, leaks, exceptions, cancellation) and 10 (tooling gate) are **hard blockers** —
> failing any is not principal-grade regardless of features, and they have **no comment override**
> (a commented-out leak is still a leak). Sections 5–9, 11–14 elevate "correct" to "production-grade
> and operable."
>
> Sourced from: PEP 8 / PEP 20, the Google Python Style Guide, CPython docs (asyncio,
> `asyncio.TaskGroup`, `contextvars`, `concurrent.futures`, the logging cookbook, `contextlib`),
> PEP 484/561/604/654 (`ExceptionGroup`/`except*`), OpenTelemetry-Python, structlog, pydantic v2 /
> pydantic-settings, SQLAlchemy 2.0 async docs, and the rule references for ruff, mypy, bandit,
> and pip-audit.

## Definition of Done — emit this block, filled in, with every code response

Copy this and mark each line **PASS**/**FAIL** (+ a ≤10-word reason on any FAIL). This emitted evidence
— not a silent "I reviewed it" — is what makes output dependable. §1–4 and §10 are hard blockers with
**no comment override**.

```
[SELF] tasks      — every task/thread owned, awaited, cancelled on shutdown (or none started) §1
[SELF] leaks      — every resource in `with`/`aclose`; queues, caches, pools bounded          §2
[SELF] exceptions — narrow except, `raise ... from`, nothing swallowed, handled exactly once  §3
[SELF] cancel     — timeout on every I/O call; CancelledError re-raised, deadline propagated  §4
[SELF] log/obs    — structured logs + contextvar IDs, no secrets; span records error + status §5–7
[TOOL] uvx ruff format (no diff) · uvx ruff check · uvx mypy · uv run pytest -q               §10
[CI]   uvx pip-audit (no actionable finding)                                                  §10
```

**Legend used throughout this rubric:** `[TOOL: <tool>]` = a named check catches it; the enforcement
**hooks run the core gate** (`ruff format`, `ruff check`, `mypy`, `pytest`) on every edit and at turn
end, so those are covered automatically. `[CI]` = also tool-caught but networked (`pip-audit`) — run it
in CI, **not** the fast local hook. Everything untagged is `[SELF]` = **no tool can see it, you are the
only gate.** Concentrate review effort on the untagged items.

## 1. Concurrency & task lifecycle — HARD BLOCKER (no comment override)

- [ ] **TASK REFERENCE** — FAIL if the result of `asyncio.create_task(...)` / `ensure_future(...)` is
      not stored — the loop holds only a weak reference and the task can be garbage-collected
      mid-flight, silently losing the work. `[TOOL: ruff RUF006 — bare-expression form only; a task
      stored in a variable that is never awaited is [SELF]]`
- [ ] **OWNERSHIP** — FAIL if a task/thread has no identifiable owner that can await it. Prefer
      `async with asyncio.TaskGroup()` (3.11+) / `anyio.create_task_group()`: it awaits every child
      and cancels siblings on the first exception. Bare `gather()` leaves siblings running.
- [ ] **GATHER RESULTS** — FAIL if `asyncio.gather(..., return_exceptions=True)` results are not
      inspected for `BaseException` members — failures are returned as ordinary values and vanish.
- [ ] **TERMINATION** — FAIL if a long-lived task/thread has no stop condition (a stop `Event`,
      a sentinel on the queue, a `CancelledError` arm). "It exits when the process does" is not one.
- [ ] **SHUTDOWN JOIN** — FAIL if a background task is cancelled but not awaited afterwards
      (`t.cancel()` then `await asyncio.gather(t, return_exceptions=True)`) — cancellation is not
      complete until the task actually finishes its `finally` blocks.
- [ ] **NO IMPORT-TIME SPAWN** — FAIL if a task/thread/pool is started at import time or in a
      module-level initializer — imports run before config, logging, and the event loop exist. Own it
      in an object with explicit `start()`/`aclose()`, or an ASGI `lifespan`.
- [ ] **NO LOOP IN LIBRARY CODE** — FAIL if library code calls `asyncio.run()` / `get_event_loop()` /
      `new_event_loop()`; running the loop is the entrypoint's decision. (`get_event_loop()` with no
      running loop is deprecated since 3.12 and raises in 3.14.)
- [ ] **NOTHING BLOCKING ON THE LOOP** — FAIL if a coroutine calls a blocking API (`time.sleep`,
      `requests.*`, `open(...).read()`, `subprocess.run`, `psycopg2`, a sync SDK) — it stalls every
      other request on that worker. Offload with `await asyncio.to_thread(...)` or use an async
      driver. `[TOOL: ruff ASYNC251 (time.sleep), ASYNC210 (blocking HTTP), ASYNC230 (open),
      ASYNC220–222 (subprocess) — an arbitrary blocking third-party client is [SELF]]`
- [ ] **NO BUSY-WAIT** — FAIL on `while not done: await asyncio.sleep(0.1)` where an `asyncio.Event`
      or `Condition` expresses the wait directly. `[TOOL: ruff ASYNC110]`
- [ ] **BOUNDED FAN-OUT** — FAIL if one task/thread is created per item of a caller-sized collection.
      Bound it with an `asyncio.Semaphore`, a fixed worker pool over a bounded `Queue`, or an anyio
      capacity limiter — 50k concurrent sockets is a self-inflicted outage.
- [ ] **EXECUTOR LIFECYCLE** — FAIL if `ThreadPoolExecutor`/`ProcessPoolExecutor` is used without
      `with` or an explicit `shutdown(wait=True)`; interpreter exit blocks on non-daemon workers.
      FAIL if `max_workers` is left default on a request path (defaults scale with CPU count, not
      with the downstream's capacity).
- [ ] **GIL REALITY** — FAIL if CPU-bound work is put on threads and described as parallel: on the
      GIL-enabled interpreter it is serialized plus overhead. Use processes (or justify the
      free-threaded build explicitly).
- [ ] **THREAD SAFETY** — FAIL if state is mutated from more than one thread without a `Lock`.
      "The GIL makes it atomic" is false for read-modify-write (`x += 1`) and check-then-act
      (`if k not in d: d[k] = ...`).
- [ ] **AWAIT IS AN INTERLEAVE POINT** — FAIL if an `await` sits between a check and the mutation
      that depends on it without an `asyncio.Lock`. Single-threaded does not mean atomic.
- [ ] **CLOSURE CAPTURE** — FAIL if a callback/coroutine scheduled inside a loop captures the loop
      variable by reference instead of binding it (default arg / `functools.partial`).
      `[TOOL: ruff B023]`
- [ ] **CROSS-THREAD ASYNCIO** — FAIL if an asyncio primitive is touched from another thread without
      `loop.call_soon_threadsafe()` / `asyncio.run_coroutine_threadsafe()`. FAIL if `daemon=True` is
      the shutdown strategy — daemon threads are killed mid-write.

## 2. Resource & memory leaks — HARD BLOCKER (no comment override)

- [ ] FAIL if a file/socket/lock is acquired without `with` (or `close()` in `finally`) — refcount
      cleanup is a CPython implementation detail, not a contract. `[TOOL: ruff SIM115 for `open`]`
- [ ] FAIL if an `httpx.AsyncClient` / `aiohttp.ClientSession` / DB engine is constructed per request
      instead of once per process and closed on shutdown — fd exhaustion, no connection reuse, and
      "Unclosed client session" warnings under load.
- [ ] FAIL if an async resource is closed with `close()` but the coroutine is never awaited
      (`await client.aclose()`, `async with`) — the shutdown never actually runs.
- [ ] FAIL if a generator holding a resource may be abandoned unexhausted without
      `contextlib.closing`/`aclosing` — its `finally` runs whenever GC decides, if ever.
- [ ] FAIL on an unbounded `asyncio.Queue()` / `queue.Queue()` between a fast producer and a slow
      consumer — set `maxsize` so backpressure exists instead of RSS growth.
- [ ] FAIL if a cache is a module-level `dict` keyed by request data. Bound it (`lru_cache(maxsize=)`,
      TTL, LRU). FAIL if `@lru_cache`/`@cache` decorates a **method** — it pins `self` for the process
      lifetime, leaking every instance.
- [ ] FAIL if exception objects are retained (retry state, error lists, a `results` dict) — the
      attached traceback pins every frame and every local in it. Store a typed record or `str(exc)`.
- [ ] FAIL if a large `bytes`/array/DataFrame stays reachable through a closure, default argument, or
      long-lived instance attribute; copy the small slice out rather than pinning the whole buffer.
- [ ] FAIL if a mutable default argument or mutable class attribute accumulates across calls or
      instances. `[TOOL: ruff B006, B008, RUF012]`
- [ ] FAIL if a handler/callback/subscription/metric/signal is registered per request or per instance
      and never unregistered — the registry is a leak with a nice name.
- [ ] FAIL if `multiprocessing.shared_memory` / `mmap` / a temp file is not explicitly closed and
      `unlink()`ed, or if cleanup lives only in `__del__` on an object that participates in a cycle.

## 3. Exceptions — HARD BLOCKER (no comment override)

- [ ] FAIL on bare `except:` — it also catches `KeyboardInterrupt`, `SystemExit` and
      `asyncio.CancelledError`. `[TOOL: ruff E722]`
- [ ] FAIL on `except Exception:` anywhere but a process edge (request handler, worker top, `main`,
      a retry decorator), and even there it must log with `exc_info` and re-raise or convert.
      `[TOOL: ruff BLE001]`
- [ ] FAIL on `except ...: pass` / `except ...: continue` — a swallowed error is undebuggable. If
      ignoring is genuinely correct, say so with `contextlib.suppress(SpecificError)`.
      `[TOOL: ruff S110, S112, SIM105]`
- [ ] FAIL if a re-raise inside an `except` block loses the cause — `raise NewError(...) from err`,
      or `from None` when hiding the internal cause is deliberate. `[TOOL: ruff B904]`
- [ ] FAIL if `logger.error(...)` is used inside an `except` block instead of `logger.exception(...)`
      / `exc_info=True` — the traceback is discarded at the only moment it existed.
      `[TOOL: ruff TRY400, LOG007]`
- [ ] **HANDLE ONCE** — FAIL if the same error is both logged and re-raised on one path: one failure
      becomes N log lines across N layers, none of them the whole story. Log at the boundary that
      decides what to do; propagate everywhere else.
- [ ] FAIL if `except BaseException` (or bare `except`) can swallow `asyncio.CancelledError` —
      cancellation stops working and `TaskGroup`/shutdown hangs forever.
- [ ] FAIL if a domain failure raises bare `Exception`/`RuntimeError`/`ValueError` instead of a
      module error hierarchy with one base class callers can catch narrowly.
      `[TOOL: ruff TRY002; N818 for the `…Error` suffix]`
- [ ] FAIL on `return`/`break`/`continue` inside `finally` — it discards the in-flight exception.
      `[TOOL: ruff B012]`
- [ ] FAIL if concurrent failures are collapsed to the first one — aggregate with `ExceptionGroup` /
      `except*` (3.11+). `TaskGroup` raises a group, so a handler written as `except MyError` will
      **not** match and the error escapes as unhandled.
- [ ] FAIL if `sys.exit()` / `os._exit()` / `raise SystemExit` appears outside the `__main__`
      entrypoint — libraries raise, entrypoints exit.
- [ ] FAIL if an `assert` guards a runtime invariant or validates input — `python -O` deletes it.
      `[TOOL: ruff S101 outside tests]`
- [ ] FAIL if the exception message interpolates a secret, a full row, or PII — messages land in logs,
      tracebacks, and error trackers.
- [ ] FAIL if a failure leaves an object half-mutated: perform the fallible call first, mutate after,
      so a raised exception cannot leave inconsistent state behind.

## 4. Cancellation, deadlines & timeouts — HARD BLOCKER (no comment override)

- [ ] FAIL if any outbound call (HTTP, DB, cache, queue, subprocess, raw socket) has no explicit
      timeout. `requests` has **no** default timeout and will hang forever; `httpx`/`aiohttp` defaults
      are not your SLO. `[TOOL: ruff S113 for requests/httpx calls without `timeout=`]`
- [ ] FAIL if an internal `await` that can block on another task/peer has no `asyncio.timeout(...)` /
      `wait_for(...)` bound.
- [ ] FAIL if `except asyncio.CancelledError` does not re-raise after cleanup — swallowing it makes
      the task uncancellable and turns graceful shutdown into a hang.
- [ ] FAIL if cleanup after cancellation performs an `await` that can itself be cancelled without
      `asyncio.shield` — the rollback/close never runs. FAIL, conversely, on `shield()` used to make
      ordinary work uncancellable.
- [ ] FAIL if a cancelled/timed-out write has partial side effects with no compensation — `wait_for`
      cancels the coroutine, it does not roll back what the peer already committed.
- [ ] FAIL if the deadline is a per-hop constant instead of the inbound request's remaining budget —
      downstream work keeps burning capacity after the client has disconnected.
- [ ] FAIL if a retry chain has no overall deadline: N attempts × per-attempt timeout must not exceed
      the caller's budget.
- [ ] FAIL if work offloaded via `to_thread`/`run_in_executor` is treated as cancellable —
      cancelling the future does **not** interrupt the running thread. The thread must poll a stop
      flag, or the timeout is a lie.
- [ ] FAIL if SIGTERM does not trigger cancellation of in-flight work with a bounded drain window
      (accept-stop → cancel → await → close pools → flush telemetry).
- [ ] FAIL if a deadline is computed from `time.time()` instead of `time.monotonic()` — an NTP step
      breaks the timeout.
- [ ] FAIL if a timeout is configured only as a total and the failure mode needs connect/read/write
      split (a healthy connect with a dead read is the common outage shape).

## 5. Structured logging

- [ ] FAIL if the log message is f-string/`%`/`.format()`-interpolated — it destroys grouping and pays
      the formatting cost even when the level is disabled. Pass fields:
      `logger.info("user.created", extra={"user_id": uid})` or structlog kwargs.
      `[TOOL: ruff G001, G002, G004]`
- [ ] FAIL on `print()` for anything operational.
- [ ] FAIL on root-logger calls (`logging.info(...)`) instead of a module logger
      (`logger = logging.getLogger(__name__)`). `[TOOL: ruff LOG015, LOG002]`
- [ ] FAIL if a library module calls `basicConfig()`, attaches handlers, or sets levels — that is the
      application's decision; a library adds a `NullHandler` at most.
- [ ] FAIL if production output is not JSON (structlog `JSONRenderer` / `python-json-logger`) — grep
      is not a query engine.
- [ ] FAIL if the message is not a stable event name: `f"failed to fetch user {uid}"` should be
      `"user.fetch.failed"` plus a `user_id` field.
- [ ] FAIL if levels are wrong: `error` is for actionable, human-worthy failures. An expected 404 is
      `info`/`debug`; a retry that later succeeded is `warning` at most.
- [ ] FAIL if secrets/PII can reach a log line — tokens, passwords, cookies, full request bodies,
      emails, card numbers. Redact at the type (`pydantic.SecretStr`, a custom `__repr__`, a logging
      filter), never at each call site. Note that `@dataclass` prints **every** field in its default
      `__repr__`.
- [ ] FAIL if logging is configured more than once / handlers are added on a logger that also
      propagates — every line is emitted twice. Configure once at startup with `dictConfig`.
- [ ] FAIL if a hot-path log eagerly builds an expensive value; guard with `logger.isEnabledFor(...)`
      or pass a lazy object.
- [ ] FAIL if async code logs through a handler doing blocking file/network I/O on the loop — use
      `QueueHandler` + `QueueListener`.

## 6. contextvars & correlation (MDC)

- [ ] FAIL if a request/correlation/trace ID is threaded manually through function signatures —
      seed a `contextvars.ContextVar` in edge middleware instead.
- [ ] FAIL if request-scoped state lives in a module global or `threading.local()` in async code —
      every coroutine on that loop shares one thread, so requests overwrite each other's IDs.
- [ ] FAIL if the ID is not attached to **every** record automatically (a `logging.Filter` or
      structlog `merge_contextvars` processor). Per-call `extra={"request_id": ...}` will be forgotten
      exactly where the incident happens.
- [ ] FAIL if an inbound `traceparent` / `X-Request-ID` is ignored instead of adopted — generate one
      only when it is absent.
- [ ] FAIL if the ID is not injected into outbound headers / message metadata — the trace dies at
      your process boundary.
- [ ] FAIL if a `ContextVar` mutated inside a spawned task is expected to be visible to the parent —
      `create_task` copies the context; changes do not propagate upward. Use the returned value.
- [ ] FAIL if context is dropped when handing work to an executor — pass
      `contextvars.copy_context().run(fn, ...)`.
- [ ] FAIL if the trace ID in logs and the trace ID in spans are different values — both must come
      from the same OTel context, or logs and traces cannot be joined.
- [ ] FAIL if `ContextVar` carries business parameters — it is request-scoped metadata only (IDs,
      tenant, auth principal). Anything the function's behavior depends on is an argument.

## 7. Observability

- [ ] FAIL if `TracerProvider`/`MeterProvider` are not configured at startup with a `service.name`
      resource and a batch/periodic exporter, and `shutdown()` on exit — without the flush the last
      (i.e. the crash-adjacent) spans are lost.
- [ ] FAIL if a meaningful unit of work has no span
      (`with tracer.start_as_current_span("checkout.submit")`).
- [ ] FAIL if the error path only calls `span.record_exception(exc)` — the status stays `UNSET` and
      the trace looks healthy. Also `span.set_status(Status(StatusCode.ERROR, msg))`.
- [ ] FAIL on high-cardinality span names (`GET /users/42`) — the name is the route template, the ID
      is an attribute.
- [ ] FAIL on unbounded metric attribute values (user ID, raw URL, exception message) — cardinality
      explosion takes down the metrics backend, not just the dashboard.
- [ ] FAIL if instruments (counters/histograms) are created per request instead of once at module or
      class init.
- [ ] FAIL if a service lacks RED: request rate, error count (or a status attribute), and a latency
      **histogram**. A mean latency gauge is not RED.
- [ ] FAIL if instrumentation is hand-rolled where an official one exists
      (`opentelemetry-instrumentation-{fastapi,httpx,sqlalchemy,asyncpg,logging}`).
- [ ] FAIL if liveness probes check dependencies (a DB blip becomes a restart storm) or if readiness
      checks nothing at all.

## 8. I/O boundaries & servers

- [ ] FAIL if the server runs with default/absent timeouts — set uvicorn
      `--timeout-keep-alive`/`--timeout-graceful-shutdown`, gunicorn `--timeout`/`--graceful-timeout`.
      A worker with no timeout is a Slowloris target.
- [ ] FAIL if startup/shutdown work is not in an ASGI `lifespan` handler — import-time init and the
      deprecated `@app.on_event` both run at the wrong moment and break testing.
- [ ] FAIL if shutdown does not drain in order: stop accepting → cancel background tasks → await them
      with a bounded timeout → close clients/pools/DB engine → flush the telemetry batch processor.
- [ ] FAIL if the HTTP client is created per call or is `requests` on an async path. One shared
      `httpx.AsyncClient` with explicit `limits=httpx.Limits(max_connections=…,
      max_keepalive_connections=…)` and `timeout=`.
- [ ] FAIL if a request body is read unbounded (`await request.body()` on an upload path) — cap the
      size before buffering and stream anything large.
- [ ] FAIL if input is not validated by a schema at the edge (pydantic v2 model) before use, and FAIL
      if a strict input model doesn't reject unknown fields (`model_config = ConfigDict(extra="forbid")`).
- [ ] FAIL if a response returns the ORM/internal object instead of an explicit response model —
      that is how `password_hash` ships.
- [ ] FAIL if unhandled exceptions become raw 500s with a traceback in the body: register an
      exception handler mapping domain errors to status codes. `debug=True`/`reload` in production is
      an automatic FAIL. `[TOOL: ruff S201 for Flask debug]`
- [ ] FAIL if retries are unbounded, non-jittered, or applied to non-idempotent operations. Retry only
      transient failures (timeout/429/5xx), with capped exponential backoff + full jitter, honoring
      `Retry-After`. FAIL on a `time.sleep(1)`-in-a-loop retry.
- [ ] FAIL if a non-idempotent write is retried without an idempotency key deduped server-side.
- [ ] FAIL if `BackgroundTasks` is used for work that must survive a crash or restart — it is
      in-process, best-effort only. Use a real queue.
- [ ] FAIL if `subprocess.run(...)` has no `timeout=` or does not check the return code / stderr.

## 9. Configuration & secrets

- [ ] FAIL if config is read via scattered `os.environ[...]` / `os.getenv(...)` at point of use —
      load once into a typed settings object (`pydantic-settings`) **validated at startup**, so a bad
      value fails at boot rather than on a rare code path at 3am.
- [ ] FAIL if a required setting has a silent fallback
      (`os.getenv("DB_URL", "sqlite:///:memory:")`) — that is a production incident wearing a default.
- [ ] FAIL on any hardcoded credential, token, or key. `[TOOL: ruff S105, S106, S107; bandit B105–B107]`
- [ ] FAIL if a secret is a plain `str` on the settings object — wrap it (`SecretStr`) so a settings
      dump, `repr`, or traceback cannot print it.
- [ ] FAIL if real secrets live in a committed `.env`/config file, or if `.env` loading is the
      production path instead of the environment/secret manager.
- [ ] FAIL if config is read at **import** time into module-level globals — imports happen before the
      app configures anything, and the value can never be overridden in a test.
- [ ] FAIL if timeouts, pool sizes, retry counts, and limits are inline magic numbers instead of named
      settings with documented units.
- [ ] FAIL if the settings singleton is imported directly by every module instead of being injected
      (constructor argument or a DI provider) — untestable without global monkeypatching.
- [ ] FAIL if startup logs a "config dump" that includes secrets or feature-flag values you would not
      paste in a ticket.

## 10. Tooling gate — HARD BLOCKER, all `[TOOL]` (the enforcement hooks run these)

This is the layer tools own, not where the skill's value lives — see the `[SELF]` sections above for
that. A red gate is not negotiable and has no comment override.

- [ ] `uvx ruff format` leaves no diff (use `--check` in CI).
- [ ] `uvx ruff check` is clean **with a real rule set selected**. Ruff's default is only
      `["E4", "E7", "E9", "F"]`, which catches almost none of the rules cited in this rubric. Ship a
      `[tool.ruff.lint] select = [...]` in `pyproject.toml` covering at least:
      `E, F, W, I, N, UP, ANN, ASYNC, S, BLE, B, A, C4, DTZ, T20, LOG, G, PIE, PT, RET, SIM, TID, ARG,
      PTH, ERA, PL, TRY, PERF, RUF` — don't defer to "whatever the repo has".
- [ ] `uvx mypy` clean in strict mode (`disallow_untyped_defs`, `warn_return_any`,
      `warn_unused_ignores`, `no_implicit_optional`, `warn_unreachable`, `strict_equality`). FAIL if
      it is green only because a blanket `ignore_missing_imports` / `ignore_errors` hides the package.
- [ ] `uv run pytest -q` passes, deterministically — no ordering, wall-clock, or network dependence.
      New behavior has a test that **failed before** the change (show the red, then the green).
- [ ] `uvx pip-audit` reports nothing actionable; `uv.lock` is committed and in sync with
      `pyproject.toml`.
- [ ] FAIL if this diff adds a blanket `# noqa` or bare `# type: ignore` without a rule code and a
      reason. `[TOOL: ruff PGH003, PGH004]`
- [ ] FAIL if a rule is disabled repo-wide (or a file added to `exclude`) to make one line pass.

> Several items the sections above leave to `[SELF]` become `[TOOL]` once the rule set above is
> selected: mutable default args → **B006/B008/RUF012**, bare/blind except and silent `pass` →
> **E722/BLE001/S110/S112**, lost cause on re-raise → **B904**, lost traceback → **TRY400/LOG007**,
> f-strings in logs → **G004**, dangling `create_task` → **RUF006**, blocking calls in `async def` →
> **ASYNC210/230/251**, missing `timeout=` on requests/httpx → **S113**, f-string SQL → **S608**,
> `shell=True` → **S602/S605**, naive datetimes → **DTZ005**. Enable them and you shrink the surface
> you must self-check. What stays `[SELF]` forever: task ownership and shutdown joins, cancellation
> semantics, deadline propagation, handle-once discipline, correlation-ID plumbing, span status,
> cardinality, pool sizing, and every design decision in §8–§14.

## 11. Typing, project layout & dependency injection

- [ ] FAIL if a public function/method lacks parameter and return annotations.
      `[TOOL: ruff ANN001/ANN201; mypy disallow_untyped_defs]`
- [ ] FAIL on `Any` in a public signature where a `Protocol`, `TypedDict`, generic, or union belongs —
      `Any` silently disables checking for every caller. `[TOOL: ruff ANN401; mypy warn_return_any]`
- [ ] FAIL if a distributed package ships annotations without a `py.typed` marker — consumers see
      `Any` for your entire API.
- [ ] FAIL if an import is moved inside a function to break a cycle — the cycle is the design defect;
      invert it with a `Protocol` defined at the **consumer**.
- [ ] FAIL if a class reaches for a module-level client/singleton instead of taking it in `__init__` —
      it cannot be tested without patching globals.
- [ ] FAIL if tests must `patch("module.attr")` to substitute a collaborator; inject a
      `Protocol`-typed dependency instead. Patching is a symptom, not a strategy.
- [ ] FAIL on `utils.py` / `helpers.py` / `common.py` dumping grounds — modules are named for the
      domain concept they own.
- [ ] FAIL on `from x import *`, on import-time side effects (network, file, DB, thread), and on an
      `__init__.py` that runs logic instead of declaring a thin explicit `__all__`.
      `[TOOL: ruff F403; PLC0415 for in-function imports; PLW0603 for `global`]`
- [ ] FAIL if data crossing a module boundary is an untyped `dict` — use a frozen `dataclass`
      (`slots=True`) or pydantic model so the shape is checkable and immutable by default.
- [ ] FAIL if `TYPE_CHECKING`-only imports are executed at runtime (import cost) or a runtime-needed
      import is hidden behind `TYPE_CHECKING` (NameError at runtime). `[TOOL: ruff TC/TCH]`
- [ ] FAIL if `typing` constructs are stale for the target version — `X | None` over `Optional[X]`,
      built-in generics over `typing.List`, `Self`/`override` where they clarify intent.
      `[TOOL: ruff UP]`

## 12. Performance (measure first)

- [ ] FAIL if a speedup is claimed without a number — `pytest-benchmark`/`timeit` result or a
      `cProfile`/`py-spy`/`austin` profile. "Should be faster" is not evidence.
- [ ] FAIL if the change micro-optimizes while the real cost is an N+1 query or a serial `await` in a
      loop — profile before touching anything.
- [ ] FAIL if independent awaits run sequentially where a `TaskGroup`/`gather` with a bounded
      semaphore would overlap them.
- [ ] FAIL if a loop builds a string with `+=` instead of collecting parts and `"".join(parts)`.
- [ ] FAIL if a whole file/response/result set is materialized into a list when iteration or streaming
      would do (`for row in cursor`, `yield`, `iter_content`, generator pipelines).
- [ ] FAIL if a membership test runs against a `list` inside a loop where a `set`/`dict` makes it O(1).
- [ ] FAIL if `try/except` sits inside a tight loop for a **common** case — exceptions are cheap only
      when rare. `[TOOL: ruff PERF203]`
- [ ] FAIL on `copy.deepcopy` on a hot path, or element-wise Python loops where a vectorized
      NumPy/polars operation exists.
- [ ] FAIL if `__slots__`, lookup hoisting, or a `ProcessPoolExecutor` is introduced without a profile
      justifying it — and, for processes, without accounting for the pickling cost of the payload.
- [ ] FAIL if the optimization sacrifices readability on a cold path; cold code stays obvious.

## 13. Data stores

- [ ] FAIL if the engine/pool is created per request — one engine per process, with
      `pool_size`, `max_overflow`, `pool_timeout`, `pool_recycle`, `pool_pre_ping=True` set
      deliberately. An unconfigured pool behind a proxy that reaps idle connections gives you
      random "server closed the connection" errors.
- [ ] FAIL if a session/connection is not scoped by a context manager
      (`async with AsyncSession(engine) as s:`) — a leaked session holds a pooled connection until GC.
- [ ] FAIL if SQL is assembled by f-string/`%`/`+` interpolation of any value — parameterize
      (`text("... WHERE id = :id"), {"id": x}`). `[TOOL: ruff S608 catches the literal f-string form;
      SQL assembled across variables or helper functions is [SELF]]`
- [ ] FAIL if a transaction can exit without commit **or** rollback — use `async with session.begin()`.
      FAIL if `commit()` is called inside a per-row loop.
- [ ] FAIL if a lazy ORM relationship is touched inside a loop (N+1) or after the session closed
      (`DetachedInstanceError`) — use `selectinload`/`joinedload` and assert the query count in a test.
- [ ] FAIL if a user-facing read has no `LIMIT`, or paginates with `OFFSET` on a large table where
      keyset pagination is required.
- [ ] FAIL if a transaction stays open across an external HTTP call — it holds locks and a pooled
      connection for the duration of someone else's outage.
- [ ] FAIL if a blocking driver (`psycopg2`, `pymysql`, sync `redis`) is called from `async def`.
- [ ] FAIL if schema changes are not versioned forward-only migrations (alembic), or if
      `Base.metadata.create_all()` runs at boot in production.
- [ ] FAIL if a nullable column maps to a non-`Optional` attribute, or money is stored/computed as
      `float` instead of `Decimal`/integer minor units.
- [ ] FAIL if a `datetime` is naive — store and compare timezone-aware UTC.
      `[TOOL: ruff DTZ005 for `datetime.now()` without `tz`]`
- [ ] FAIL if bulk work issues single-row INSERTs where `executemany`/bulk insert/`COPY` exists.

## 14. Security & input validation

- [ ] FAIL if untrusted data is deserialized with `pickle`, `yaml.load` (non-`safe_load`), `marshal`,
      `shelve`, or `jsonpickle` — all of them execute code. `[TOOL: ruff S301, S506]`
- [ ] FAIL on `eval`/`exec`/`__import__`/`getattr` driven by input. `[TOOL: ruff S102, S307]`
- [ ] FAIL on `subprocess(..., shell=True)` or `os.system` with interpolated input — pass an argv
      list. `[TOOL: ruff S602, S605, S607]`
- [ ] FAIL if a path derived from input is not confined —
      `(base / user_part).resolve().is_relative_to(base.resolve())`. FAIL on `extractall()` of an
      untrusted archive without member filtering (`filter="data"`). `[TOOL: ruff S202]`
- [ ] FAIL if a user-supplied URL is fetched without a scheme + host allowlist and a block on
      loopback/link-local/private ranges — including **after** redirects (SSRF to the metadata
      endpoint). `[TOOL: ruff S310 flags the raw `urlopen`; allowlist correctness is [SELF]]`
- [ ] FAIL on `verify=False` or any TLS context that skips certificate/hostname verification.
      `[TOOL: ruff S501]`
- [ ] FAIL if a token/signature is compared with `==` instead of `hmac.compare_digest` (timing), or
      generated with `random` instead of `secrets`. `[TOOL: ruff S311]`
- [ ] FAIL on MD5/SHA-1 for a security purpose, or a fast hash for passwords — use
      argon2/bcrypt/scrypt. `[TOOL: ruff S324]`
- [ ] FAIL if untrusted XML is parsed with stdlib `xml.*` instead of `defusedxml` (XXE, billion
      laughs). `[TOOL: ruff S313–S320]`
- [ ] FAIL if an allocation size comes from input unbounded — `range(int(user_n))`, `b"x" * n`,
      a trusted `Content-Length`, or a decompression bomb (`zipfile`/`gzip` expanded without a cap).
- [ ] FAIL if a regex runs over untrusted input with nested quantifiers (catastrophic backtracking) —
      bound the input length or use a linear-time matcher.
- [ ] FAIL if `int()`/`float()` on untrusted text is unguarded — `float("inf")`/`float("nan")` poison
      comparisons, and huge integer literals are a CPU DoS. Validate through the schema instead.
- [ ] FAIL if authorization is checked at the route but not on the object (IDOR) — the ID in the path
      must be scoped to the caller's tenant/user in the query itself.
- [ ] FAIL if the process binds `0.0.0.0` where it should be loopback, or ships with debug/reload
      enabled. `[TOOL: ruff S104, S201]`
- [ ] FAIL if a `pip-audit` finding is waived without a named reason and an upgrade path.

---

### How the skill uses this rubric

1. **Author** code with task ownership, explicit timeouts, and handled-once errors **by default** —
   not retrofitted after review.
2. **Self-run** every applicable checkbox before returning; for any FAIL, fix it or add a one-line
   comment naming the tradeoff — except in §1–4 and §10, where there is **no comment override**.
3. **Emit** the Definition-of-Done block above, filled in, as the evidence that closes the response.
4. The **hooks** (not the model's memory) enforce §10 deterministically; the **rubric** makes §1–9 and
   §11–14 checkable by the model and by the reviewer subagent.

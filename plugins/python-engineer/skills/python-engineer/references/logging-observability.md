# Logging, correlation (MDC) & observability — Python

Load this when adding logs, correlation/trace IDs, spans, or metrics. Governing rule: **the app
configures logging once at its entrypoint, and every line is correlated by IDs a filter/processor
reads out of `contextvars` — never by an argument threaded through call sites.**

## Contents
- [Configure once, at the entrypoint](#configure-once-at-the-entrypoint)
- [Call-site rules: lazy formatting, extras, exceptions](#call-site-rules-lazy-formatting-extras-exceptions)
- [MDC: contextvars + a handler filter](#mdc-contextvars--a-handler-filter)
- [What contextvars propagate into](#what-contextvars-propagate-into)
- [structlog: real JSON, including library logs](#structlog-real-json-including-library-logs)
- [Secrets & redaction](#secrets--redaction)
- [Tracing with OpenTelemetry](#tracing-with-opentelemetry)
- [Metrics: RED & cardinality](#metrics-red--cardinality)

## Configure once, at the entrypoint
Module code gets exactly one logging line: `log = logging.getLogger(__name__)` at module scope. **FAIL
if importable code calls `basicConfig`, `dictConfig`, `addHandler`, or `setLevel`** — it hijacks the
app's config and duplicates or silences its output. (A published package may add
`logging.getLogger("mypkg").addHandler(logging.NullHandler())` in `__init__`, nothing more.)

```python
LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,  # True (the default) silently kills every existing logger
    "filters": {"context": {"()": ContextFilter}},                       # defined below
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s "
                                       "[%(request_id)s] %(message)s"}},
    "handlers": {"stdout": {"class": "logging.StreamHandler", "stream": "ext://sys.stdout",
                            "formatter": "plain",
                            "filters": ["context"]}},   # on the HANDLER — see MDC below
    "root": {"level": "INFO", "handlers": ["stdout"]},
}
logging.config.dictConfig(LOGGING)      # once, in main()
logging.captureWarnings(True)           # warnings join the pipeline instead of bare stderr
```
Log to stdout and let the platform ship it — **FAIL if a containerized service opens its own log
file**; you then own rotation, disk, and a second copy of every line. Levels: DEBUG (dev detail), INFO
(milestone), WARNING (recoverable oddity), ERROR (actionable — a human triages it).

**3.12+**: handler I/O blocks the calling thread and stalls the event loop, so put a queue in front —
add `"queue": {"class": "logging.handlers.QueueHandler", "handlers": ["stdout"], "filters": ["context"]}`
to the `handlers` map and make `["queue"]` the root's only handler. `dictConfig` builds the listener;
you must start and drain it (that "queue" key is what `getHandlerByName("queue")` below resolves):
```python
qh = cast(logging.handlers.QueueHandler, logging.getHandlerByName("queue"))
if (listener := qh.listener) is not None:
    listener.start()
    atexit.register(listener.stop)   # stop() drains the queue — skip it and you lose records
```
**FAIL if the contextvar filter stays on the downstream handler once a queue is in front**: filters on
the `QueueHandler` run on the calling thread (context intact), the downstream handler runs on the
listener thread where every `ContextVar` reads its default — `[req-42]` silently becomes `[-]`.

## Call-site rules: lazy formatting, extras, exceptions
```python
log.info(f"user {uid} did {action}")             # WRONG
log.info("user %s did %s", uid, action)          # RIGHT
```
*Two reasons, both load-bearing:* the f-string is interpolated — including `str()`/`__repr__` of every
operand — **before** `log.debug` is even called, so a disabled level still pays full price; and it
collapses the template into the message, so the aggregator sees a million distinct messages instead of
one groupable `user %s did %s` with `record.args` carried separately. `%`-style only, never `.format()`
or `+`; ruff enforces it under `G` (`flake8-logging-format`) and `LOG`. **Arguments** are still
evaluated eagerly — guard expensive ones with `if log.isEnabledFor(logging.DEBUG):`.

Structured fields go in `extra`; the message stays a constant: `log.info("order placed",
extra={"order_id": oid, "amount_cents": cents})`. **FAIL if an `extra` key collides with a `LogRecord`
attribute** (`message`, `name`, `args`, `module`, `exc_info`, …) — it raises `KeyError: "Attempt to
overwrite 'message' in LogRecord"` at the call site. Namespace the keys, or use structlog (below).

`log.exception(...)` == `log.error(..., exc_info=True)`, and is valid **only inside an `except`
block** — elsewhere it emits a useless `NoneType: None`. Attach the traceback where you *decide to
stop*: handle once — log **or** `raise ... from exc`, never both, or one failure is scattered across
three layers. Set `sys.excepthook` / `threading.excepthook` so a crash lands in the pipeline too.

## MDC: contextvars + a handler filter
Python's MDC is `contextvars`. Declare the `ContextVar` at **module scope** (per-call creation leaks
and never resolves), seed it at the edge, and let a `logging.Filter` stamp every record.

```python
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")   # default is mandatory

class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True                        # a filter returning False DROPS the record

class RequestIDMiddleware:                 # pure ASGI; same shape for WSGI/Starlette middleware
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        rid = headers.get(b"x-request-id", b"").decode() or uuid.uuid4().hex  # reuse inbound ID
        token = request_id_var.set(rid)
        try:
            await self.app(scope, receive, send)
        finally:
            request_id_var.reset(token)    # required: workers reuse contexts across requests
```
Every `log.info(...)` downstream now carries `request_id` with zero plumbing. Two failures this shape
prevents:
- **FAIL if the filter is attached to a logger instead of a handler.** Logger filters run only for
  records logged *directly* to that logger, so a record from `httpx` reaching your handler gets no
  `request_id` and the formatter dies: `ValueError: Formatting field not found in record`. Handler
  filters run for every record reaching the handler.
- **FAIL if the `ContextVar` has no default** — `LookupError` on the first line logged outside a
  request (startup, background jobs).

Propagate outbound: send the value as `X-Request-ID` (plus the W3C `traceparent`, below) on every
downstream call so the next service's logs join this request.

## What contextvars propagate into
The property that makes MDC work in async Python, and the one place it silently breaks (verified, 3.14):

| Mechanism | Ambient context visible? |
|---|---|
| `asyncio.create_task` / `TaskGroup.create_task` | **Yes** — a *copy* taken at creation |
| `asyncio.to_thread(fn)` | **Yes** — it copies the context for you |
| `loop.run_in_executor(pool, fn)` | **No** |
| `Executor.submit(fn)` / `threading.Thread(target=fn)` | **No** |
| `multiprocessing` / subprocesses | **No** — pass the ID explicitly |

```python
def in_context(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Callable[[], T]:
    ctx = contextvars.copy_context()
    return functools.partial(ctx.run, fn, *args, **kwargs)

await loop.run_in_executor(pool, in_context(blocking_db_call, order_id))   # correlated
pool.submit(in_context(blocking_db_call, order_id))                       # correlated
```
Because a task gets a **copy**, a `.set()` inside a task is invisible to its parent and to siblings —
seed at the request edge, never inside a spawned unit expecting it to escape.

## structlog: real JSON, including library logs
`structlog.contextvars.merge_contextvars` is the MDC processor. Route structlog **through** stdlib
`logging` so third-party records (`httpx`, `sqlalchemy`, `uvicorn`) render as the same JSON — one
pipeline, one schema:
```python
shared: list[Any] = [
    structlog.contextvars.merge_contextvars,          # <- the MDC step
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    redact,                                           # see below
]
structlog.configure(
    processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
formatter = structlog.stdlib.ProcessorFormatter(
    foreign_pre_chain=shared,                         # applies to logging.getLogger() records
    processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer()],  # ConsoleRenderer in dev — one flag
)
handler = logging.StreamHandler(sys.stdout); handler.setFormatter(formatter)
logging.getLogger().handlers[:] = [handler]
```
Bind at the edge with the context manager so reset is structural:
`with structlog.contextvars.bound_contextvars(request_id=rid, trace_id=tid): ...` (or
`bind_contextvars(...)` → `reset_contextvars(**tokens)`). `log.exception("charge failed",
order_id=oid)` then renders the traceback into an `exception` field.

## Secrets & redaction
Never log credentials, tokens, cookies, full card/account numbers, or PII — defense in depth, since a
code-review rule alone will not hold:
```python
_SENSITIVE = {"password", "token", "authorization", "api_key", "ssn", "card_number"}

def redact(_logger: WrappedLogger, _name: str, event: EventDict) -> EventDict:
    for k in event:
        if k.lower() in _SENSITIVE:
            event[k] = "[REDACTED]"
    return event
```
Give secret-bearing types a `__repr__` returning `"<Secret>"` — **FAIL if a dataclass holding a token
uses the default repr**, because one `log.info("cfg=%s", cfg)` or one traceback frame dumps it. Keep
secrets out of exception messages and out of URLs (query strings land in access logs).

## Tracing with OpenTelemetry
Build the `TracerProvider` with a `service.name` resource + batch exporter at startup and `shutdown()`
it on exit; `trace.get_tracer(__name__)` at module scope is safe (it returns a proxy).
```python
tracer = trace.get_tracer("orders")

def place(order_id: str) -> str:
    with tracer.start_as_current_span("Service.place") as span:
        span.set_attribute("order.tier", tier)          # low-cardinality only
        try:
            charge(order_id)
        except TimeoutError as exc:
            span.record_exception(exc)                                  # both calls, always
            span.set_status(Status(StatusCode.ERROR, "charge failed"))
            return "degraded"
        return "ok"
```
- An exception that **escapes** the `with` block is recorded and sets `StatusCode.ERROR` automatically
  (`record_exception=True`, `set_status_on_exception=True` are the defaults — unlike Go, do not
  double-record it). **FAIL if a *handled* failure leaves the span UNSET**: swallow the exception and
  you must make both calls yourself, or the span never shows up as an error.
- Cross-service: `extract(inbound_headers)` at the edge, passed as `start_as_current_span(...,
  context=parent)`; `inject(outbound_headers)` before each client call. **FAIL if `inject` runs
  outside an active span** — it writes nothing and the trace silently splits.
- Correlate logs to traces in the same handler filter, from `ctx =
  trace.get_current_span().get_span_context()`: `record.trace_id = f"{ctx.trace_id:032x}" if
  ctx.is_valid else "-"` (`:016x` for `span_id`) — zero-padded hex is what backends index on.
- Sample by trace (`ParentBased(TraceIdRatioBased(r))`), never per-span: mixed decisions break traces.

## Metrics: RED & cardinality
**RED** per service: **R**ate, **E**rrors, **D**uration (histogram); USE (utilization, saturation,
errors) for resources. Create instruments **once at module scope**, never per request.
```python
REQUESTS = meter.create_counter("http.server.requests", unit="1")
LATENCY = meter.create_histogram("http.server.duration", unit="s")
REQUESTS.add(1, {"route": "/orders/{id}", "method": "GET", "status": "200"})
```
- Pick by shape: `Counter` (monotonic), `UpDownCounter` (±, e.g. in-flight), `Histogram` (latency,
  sizes), observable gauge (point-in-time, via callback).
- **FAIL if any attribute is unbounded** — user ID, order ID, raw path, full URL, error *message*. Use
  the route **template**, an error *class*, a status *code*: cardinality is the product of every
  label's domain, and one unbounded label takes down the metrics backend.
- Record latency around the whole handler including the failure path (`try/finally`), or the histogram
  describes only the happy path and the p99 is a lie.

# Logging, correlation (MDC) & observability

Load this when adding logs, correlation/trace IDs, spans, or metrics. Governing rule: **every log
line and span on a request is automatically correlated by IDs pulled from `context.Context` — never
threaded by hand.**

## Contents
- [Structured logging with slog](#structured-logging-with-slog)
- [MDC: correlation IDs via a context-aware handler](#mdc-correlation-ids-via-a-context-aware-handler)
- [Secrets & redaction](#secrets--redaction)
- [Tracing with OpenTelemetry](#tracing-with-opentelemetry)
- [Metrics: RED & USE](#metrics-red--use)

## Structured logging with slog
Use `log/slog` (stdlib since 1.21), `JSONHandler` in production. Typed attributes, a **stable constant
message**, never string interpolation into the message. Use the `*Context` methods so a handler can
read ctx. Drive the level from a `slog.LevelVar` so it's changeable at runtime.
```go
levelVar := new(slog.LevelVar) // defaults to Info
h := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: levelVar})
slog.SetDefault(slog.New(&CorrelationHandler{Handler: h})) // wrap for MDC (below)

slog.InfoContext(ctx, "order placed", "order_id", id, "amount_cents", cents)
// hot path — typed Attrs avoid the ...any boxing:
slog.LogAttrs(ctx, slog.LevelInfo, "order placed",
    slog.String("order_id", id), slog.Int("amount_cents", cents))
```
Levels: Debug (dev detail), Info (normal milestones), Warn (recoverable oddity), Error (actionable
failure only). Log the `%w`-wrapped error at the point you decide to stop.

## MDC: correlation IDs via a context-aware handler
Go has no thread-locals — **context propagation IS the MDC mechanism.** Seed IDs into ctx at the edge,
then a custom `slog.Handler` attaches them to every record automatically.

```go
// 1) A handler that reads IDs off ctx and adds them to every log line.
type CorrelationHandler struct{ slog.Handler }

func (h *CorrelationHandler) Handle(ctx context.Context, r slog.Record) error {
    if id, ok := RequestIDFromContext(ctx); ok { // accessor from concurrency.md
        r.AddAttrs(slog.String("request_id", id))
    }
    if sc := trace.SpanContextFromContext(ctx); sc.HasTraceID() {
        r.AddAttrs(slog.String("trace_id", sc.TraceID().String()))
    }
    return h.Handler.Handle(ctx, r)
}
func (h *CorrelationHandler) WithAttrs(as []slog.Attr) slog.Handler {
    return &CorrelationHandler{h.Handler.WithAttrs(as)}
}
func (h *CorrelationHandler) WithGroup(name string) slog.Handler {
    return &CorrelationHandler{h.Handler.WithGroup(name)}
}

var _ slog.Handler = (*CorrelationHandler)(nil) // compile-time proof the wrapper satisfies the interface

// 2) Middleware seeds the ID at the request edge (reuse an inbound one for cross-service correlation).
func RequestID(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        id := r.Header.Get("X-Request-ID")
        if id == "" {
            id = uuid.NewString()
        }
        ctx := context.WithValue(r.Context(), requestIDKey, id)
        w.Header().Set("X-Request-ID", id)
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}
```
Now `slog.InfoContext(ctx, …)` anywhere downstream carries `request_id` and `trace_id` with zero
per-call plumbing. Propagate the ID outbound (set the header / gRPC metadata on downstream calls) so
the next service's logs join the same trace.

## Secrets & redaction
Never log secrets, tokens, or PII. Make sensitive types self-redacting via `slog.LogValuer`:
```go
type Token string
func (Token) LogValue() slog.Value { return slog.StringValue("REDACTED") }
```
Also keep secrets out of `String()`/`%v` output and out of wrapped error messages.

## Tracing with OpenTelemetry
Initialize a `TracerProvider` at startup with a `service.name` resource and a batch exporter; `Shutdown`
it on exit. A span per meaningful unit of work; propagate the **returned** ctx; on error set **both**
`RecordError` and `SetStatus` (RecordError alone does not mark the span failed).
```go
var tracer = otel.Tracer("orders")

func (s *Service) Place(ctx context.Context, o Order) (err error) {
    ctx, span := tracer.Start(ctx, "Service.Place")
    defer span.End()
    span.SetAttributes(attribute.String("order.id", o.ID)) // low-cardinality only

    if err = s.charge(ctx, o); err != nil {          // pass the span's ctx down
        span.RecordError(err)
        span.SetStatus(codes.Error, "charge failed")
        return fmt.Errorf("charge: %w", err)
    }
    return nil
}
```
Register the W3C tracecontext propagator (`otel.SetTextMapPropagator(propagation.TraceContext{})`) and
extract on inbound / inject on outbound boundaries so traces span services. Keep span attributes
low-cardinality — never a raw user ID or full URL as an attribute key.

## Metrics: RED & USE
- **RED** for services: **R**ate (requests/sec), **E**rrors (failures/sec), **D**uration (latency
  histogram). **USE** for resources: **U**tilization, **S**aturation, **E**rrors.
- Create instruments **once** at init, not per request. Pick the right type: `Counter` (monotonic),
  `UpDownCounter` (+/−), `Histogram` (distributions/latency), `Gauge` (point-in-time).
- Bound label/attribute cardinality — no user IDs, request IDs, or URLs as metric labels (that's a
  cardinality explosion). Use exemplars to link a latency bucket back to a trace where supported.

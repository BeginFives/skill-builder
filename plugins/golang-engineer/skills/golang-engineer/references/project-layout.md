# Project layout, dependency injection, servers & config

Load this when structuring a package/module, wiring dependencies, standing up a server, or loading
config. Governing rule: **dependencies are injected through constructors; wiring is explicit in
`main`; there is no hidden global state.**

## Contents
- [Layout](#layout)
- [Interfaces & DI](#interfaces--di)
- [Functional-options constructor](#functional-options-constructor)
- [Config & secrets](#config--secrets)
- [Graceful-shutdown HTTP server](#graceful-shutdown-http-server)

## Layout
- `cmd/<binary>/main.go` — entrypoints; thin, just wiring.
- `internal/…` — private packages the module owns; the compiler forbids external import.
- `pkg/…` — only for code genuinely meant to be imported by other modules. When unsure, use `internal/`.
- Package names are lowercase nouns describing contents (`order`, `billing`). No `util`, `common`,
  `helpers`, `base` dumping grounds; no stutter (`order.Order`, not `order.OrderService`). No import cycles.
- Enums start at `iota+1` or reserve the zero value as an explicit `Invalid` so a zero-valued field
  isn't silently a valid state.

## Interfaces & DI
- **Accept interfaces, return concrete structs.** Define the interface **at the consumer**, keep it
  small (1–3 methods), and only when a real second implementation or a test fake needs the seam.
- Inject dependencies via constructors; no service locators, no package-level singletons for mutable state.
- **This is for building a component.** Do not add a `NewX` constructor (or options, or an interface) to
  an existing struct just to wire in one new field — that's over-engineering a trivial edit. Match the
  change to the request (see SKILL.md → Scope discipline).
- Verify satisfaction at compile time where it matters: `var _ Store = (*PostgresStore)(nil)`.
- No `init()` side effects that wire behavior — wiring happens in `main`/constructors where it's visible.
```go
type Store interface { // defined by the consumer (Service), not the producer
    Save(ctx context.Context, o Order) error
    Get(ctx context.Context, id string) (Order, error)
}

type Service struct {
    store  Store
    logger *slog.Logger
}

func NewService(store Store, logger *slog.Logger) *Service { // deps in, concrete struct out
    return &Service{store: store, logger: logger}
}
```

## Functional-options constructor
When a constructor has optional/growing configuration, prefer functional options over a giant param
list or a mutable config struct:
```go
// Alternative form of the Service above — pick one; don't combine them in one file. This variant
// carries a timeout field the options set.
type Service struct {
    store   Store
    logger  *slog.Logger
    timeout time.Duration
}

type Option func(*Service)

func WithTimeout(d time.Duration) Option { return func(s *Service) { s.timeout = d } }

func NewService(store Store, opts ...Option) *Service {
    s := &Service{store: store, timeout: 5 * time.Second} // sensible defaults
    for _, opt := range opts {
        opt(s)
    }
    return s
}
```

## Generics
Prefer concrete types and interfaces; reach for type parameters only for genuine container/algorithm
reuse or to remove `any`/reflection. Use `comparable` or a custom constraint; don't add a type
parameter used once, and don't genericize where a plain interface reads clearer. "Accept interfaces"
still applies — generics complement interfaces, they don't replace them.

## Config & secrets
- Load config from env/flags/file into a **typed struct, validated at startup** — fail fast with a
  clear error on anything missing or invalid; never defer a config failure to the first request.
- No secrets hardcoded or committed. Read them from env / a secret manager; scrub them from logs,
  errors, and `%v` output (see the `slog.LogValuer` pattern in logging-observability.md).
- Timeouts, pool sizes, and limits are configurable with documented defaults — not magic numbers.
```go
type Config struct {
    Addr         string        `env:"ADDR"          default:":8080"`
    ReadTimeout  time.Duration `env:"READ_TIMEOUT"  default:"5s"`
    DatabaseURL  string        `env:"DATABASE_URL"` // required
}

func (c Config) Validate() error {
    if c.DatabaseURL == "" {
        return errors.New("DATABASE_URL is required")
    }
    return nil
}
```

## Time handling
- Measure elapsed time with `time.Since(start)` (monotonic) — never subtract two wall-clock
  `time.Now()` values; NTP/leap adjustments corrupt the delta.
- Store and compare timestamps in UTC (`time.Now().UTC()`); apply the timezone only at display
  boundaries. Use `time.Duration`-typed config, not bare int seconds.

## Graceful-shutdown HTTP server
Explicit timeouts (a zero-value `http.Server` has infinite ones — a Slowloris DoS) and a drain on
SIGINT/SIGTERM so in-flight requests finish.
```go
func run(ctx context.Context, cfg Config, h http.Handler) error {
    srv := &http.Server{
        Addr:              cfg.Addr,
        Handler:           h,
        ReadHeaderTimeout: 5 * time.Second,  // always set — cheap Slowloris defense
        ReadTimeout:       cfg.ReadTimeout,
        WriteTimeout:      10 * time.Second,
        IdleTimeout:       120 * time.Second,
        MaxHeaderBytes:    1 << 20,
    }

    errCh := make(chan error, 1)
    go func() {
        if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
            errCh <- err // real startup/serve error
        }
    }()

    select {
    case err := <-errCh:
        return err
    case <-ctx.Done(): // ctx cancelled on SIGINT/SIGTERM (signal.NotifyContext in main)
        shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
        defer cancel()
        return srv.Shutdown(shutdownCtx) // stop accepting, drain in-flight
    }
}
```
Wire the signal in `main` so the ctx cancels on SIGINT/SIGTERM:
```go
ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
defer stop()
if err := run(ctx, cfg, handler); err != nil {
    slog.Error("server exited", "err", err)
    os.Exit(1)
}
```
gRPC equivalent: set keepalive params + per-RPC deadlines from ctx, add recovery/logging/tracing/metrics
interceptors, return `codes.*` statuses, and stop with `GracefulStop()`.

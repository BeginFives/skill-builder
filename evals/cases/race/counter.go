// Eval fixture — deliberately contains a data race for the model to FIX.
// Not part of any module; do not "go build" this at repo root.
package cache

// Counter tallies hits per key. It is shared across concurrent HTTP handlers.
type Counter struct {
	counts map[string]int
}

func New() *Counter {
	return &Counter{counts: make(map[string]int)}
}

// Inc is called from many goroutines at once.
func (c *Counter) Inc(key string) {
	c.counts[key]++ // BUG: concurrent map write — data race, may panic at runtime
}

func (c *Counter) Get(key string) int {
	return c.counts[key] // BUG: unsynchronized read racing with Inc
}

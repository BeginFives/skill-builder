// Eval fixture — Produce leaks its goroutine if the consumer stops early (e.g. ctx cancelled).
// Make it cancellation-safe. Not part of any module.
package leak

import "context"

// Produce sends every item to out, then closes out. It is started as `go Produce(ctx, items, out)`.
// If the consumer stops reading (its ctx is cancelled), the bare send below blocks forever and the
// goroutine leaks.
func Produce(ctx context.Context, items []int, out chan<- int) {
	for _, it := range items {
		out <- it // blocks forever once the receiver is gone; ctx is ignored
	}
	close(out)
}

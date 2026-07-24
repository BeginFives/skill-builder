// Eval fixture — implement ProcessAll leak-free. Not part of any module.
package pool

import "context"

type Job struct{ ID string }
type Result struct{ ID string }

// ProcessAll should process every job concurrently with at most `workers`
// goroutines running at once, stop cleanly on ctx cancellation, and return
// the first error encountered (cancelling the rest). Implement it leak-free.
func ProcessAll(ctx context.Context, jobs []Job, workers int) ([]Result, error) {
	panic("implement me")
}

// do is the per-job unit of work (already provided).
func do(ctx context.Context, j Job) (Result, error) {
	return Result{ID: j.ID}, nil
}

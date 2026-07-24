// Eval fixture — improve CallWithRetry to production-grade retry semantics. Not part of any module.
package retry

import (
	"context"
	"errors"
	"time"
)

var (
	ErrTransient = errors.New("transient")
	ErrPermanent = errors.New("permanent")
)

// callOnce performs one attempt against a flaky dependency (provided).
func callOnce(ctx context.Context) error {
	_ = ctx
	return ErrTransient
}

// CallWithRetry retries callOnce on failure.
func CallWithRetry(ctx context.Context) error {
	for i := 0; i < 3; i++ { // fixed attempts, fixed sleep, ignores ctx, retries any error
		if err := callOnce(ctx); err == nil {
			return nil
		}
		time.Sleep(time.Second)
	}
	return ErrTransient
}

// Eval fixture — a function to write TABLE-DRIVEN TESTS for (including error paths).
// Not part of any module; do not "go build" this at repo root.
package money

import (
	"errors"
	"math"
	"strconv"
	"strings"
)

var (
	ErrEmpty  = errors.New("empty amount")
	ErrSyntax = errors.New("invalid amount syntax")
)

// ParseAmount parses a non-negative dollar string like "10" or "10.50" into integer
// cents. It returns ErrEmpty for "" and ErrSyntax for anything malformed (including
// signed or out-of-range input). Amounts are unsigned.
func ParseAmount(s string) (int64, error) {
	s = strings.TrimSpace(s)
	if s == "" {
		return 0, ErrEmpty
	}
	if s[0] == '-' || s[0] == '+' { // amounts are non-negative and unsigned
		return 0, ErrSyntax
	}
	dollars, cents, hasCents := strings.Cut(s, ".")
	d, err := strconv.ParseInt(dollars, 10, 64)
	if err != nil {
		return 0, ErrSyntax
	}
	if d > (math.MaxInt64-99)/100 { // guard d*100 (+99 cents) against overflow
		return 0, ErrSyntax
	}
	total := d * 100
	if hasCents {
		// exactly two ASCII digits — reject "-5", "+5", " 5", "5", "123"
		if len(cents) != 2 || cents[0] < '0' || cents[0] > '9' || cents[1] < '0' || cents[1] > '9' {
			return 0, ErrSyntax
		}
		c, _ := strconv.ParseInt(cents, 10, 64)
		total += c
	}
	return total, nil
}

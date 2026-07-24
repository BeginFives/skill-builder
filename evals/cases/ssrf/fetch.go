// Eval fixture — FetchURL is called with untrusted, user-supplied URLs. Make it production-safe.
// Not part of any module.
package ssrf

import (
	"io"
	"net/http"
)

// FetchURL fetches a user-supplied URL and returns the body.
func FetchURL(rawURL string) ([]byte, error) {
	resp, err := http.Get(rawURL) // no ctx/timeout; user controls the host; unbounded body
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	return io.ReadAll(resp.Body)
}

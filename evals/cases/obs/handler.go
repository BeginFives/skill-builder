// Eval fixture — add request-scoped structured logging with a correlation ID.
// Not part of any module.
package obs

import "net/http"

// Handle greets a user. Add request-scoped structured logging with a
// correlation/trace ID (extracted from or seeded into the request context),
// logged once at the boundary, with no PII/secret in the fields.
func Handle(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")
	_, _ = w.Write([]byte("hello " + name))
}

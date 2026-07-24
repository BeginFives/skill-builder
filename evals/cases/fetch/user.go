// Eval fixture — a function to REFACTOR to be context-aware and production-safe.
// Not part of any module; do not "go build" this at repo root.
package fetch

import (
	"encoding/json"
	"net/http"
)

type User struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// FetchUser retrieves a user from the upstream API.
func FetchUser(id string) (*User, error) {
	resp, err := http.Get("https://api.example.com/users/" + id) // no ctx, DefaultClient has no timeout
	if err != nil {
		return nil, err // not wrapped
	}
	defer resp.Body.Close()

	var u User
	json.NewDecoder(resp.Body).Decode(&u) // decode error ignored
	return &u, nil
}

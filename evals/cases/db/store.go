// Eval fixture — make this store production-ready (fix GetAccount, implement Transfer atomically).
// Not part of any module.
package db

import "database/sql"

type Account struct {
	ID    string
	Cents int64
}

// GetAccount loads an account by id.
func GetAccount(db *sql.DB, id string) (*Account, error) {
	row := db.QueryRow("SELECT id, cents FROM accounts WHERE id = '" + id + "'") // string-built SQL, no ctx
	var a Account
	if err := row.Scan(&a.ID, &a.Cents); err != nil {
		return nil, err // not wrapped; sql.ErrNoRows not mapped to a not-found error
	}
	return &a, nil
}

// Transfer moves `cents` from one account to another. It must be atomic. Implement it.
func Transfer(db *sql.DB, from, to string, cents int64) error {
	panic("implement me")
}

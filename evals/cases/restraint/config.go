// Eval fixture — a trivial change that must stay trivial. Not part of any module.
package restraint

// AppVersion is the build version, injected at release time.
const AppVersion = "1.4.2"

// Config holds service configuration.
type Config struct {
	Addr        string
	ReadTimeout int
}

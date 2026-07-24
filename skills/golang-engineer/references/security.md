# Security & input validation

Load this when handling untrusted input, making outbound requests to user-supplied targets, decoding
bodies, or doing size/length arithmetic. Governing rule: **treat everything crossing a trust boundary
as hostile — validate it, bound it, and parameterize it.** `govulncheck` scans your *dependencies*;
this file is about the code *you* write. Enable `gosec` in golangci-lint to machine-catch much of it.

## Contents
- [Injection](#injection)
- [SSRF: outbound requests to untrusted URLs](#ssrf-outbound-requests-to-untrusted-urls)
- [Bounding input size](#bounding-input-size)
- [JSON decoding](#json-decoding)
- [Integer conversions](#integer-conversions)
- [unsafe / cgo](#unsafe--cgo)
- [Secrets](#secrets)

## Injection
- **SQL:** parameterized queries only (see data-stores.md) — never build SQL from user input.
- **Command:** never pass user input to `exec.Command` via a shell (`sh -c "…"+input`). Call the binary
  directly with an args slice: `exec.CommandContext(ctx, "convert", "-resize", size, path)`; validate
  `size`/`path` against an allowlist first.
- **Path traversal:** clean and confine user-supplied paths — `filepath.Clean`, reject `..`, and verify
  the result stays under a known root (Go 1.24+ `os.Root`/`root.Open` enforces this).

## SSRF: outbound requests to untrusted URLs
If a user supplies a URL you fetch, an attacker will point it at `169.254.169.254` (cloud metadata) or
internal services. Allowlist the scheme and host, and block private/link-local/loopback IPs — including
after DNS resolution:
```go
func safeDialURL(raw string) (*url.URL, error) {
    u, err := url.Parse(raw)
    if err != nil || (u.Scheme != "https" && u.Scheme != "http") {
        return nil, fmt.Errorf("bad url")
    }
    ips, err := net.LookupIP(u.Hostname())
    if err != nil {
        return nil, err
    }
    for _, ip := range ips {
        if ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || ip.IsUnspecified() {
            return nil, fmt.Errorf("blocked host")
        }
    }
    return u, nil
}
```
For defense in depth, also enforce it in the `http.Transport`'s `DialContext` / `Control` so a
TOCTOU DNS rebind can't slip a private IP past the pre-check.

## Bounding input size
- Cap request bodies: `r.Body = http.MaxBytesReader(w, r.Body, 1<<20)` before decoding.
- Cap decompression: never `io.Copy` a gzip/zip stream to memory unbounded (zip bomb) — use
  `io.LimitReader` on the decompressed stream.
- Cap anything derived from a length/count field in untrusted input before `make([]T, n)`.

## JSON decoding
- Reject unknown fields on strict API inputs: `dec := json.NewDecoder(r.Body); dec.DisallowUnknownFields()`.
- Stream-decode bounded bodies with `json.NewDecoder(r.Body).Decode(&v)` rather than reading all then `Unmarshal`.
- Use `json.Number` (via `dec.UseNumber()`) when large integers must not lose precision to float64.
- `omitempty` does **not** omit zero-valued structs, `false`, or `0`; use a pointer (or `*T`) when you
  must distinguish "absent" from "zero".
- **Validate after decode, before use** — decoding success is not validity.

## Integer conversions
Narrowing conversions silently wrap: `int64` → `int32` or `int` → `uint` on attacker-controlled sizes
is a real bug class (`gosec` G115). Bounds-check before converting, and prefer `strconv` with explicit
bit sizes over unchecked casts.

## unsafe / cgo
Avoid `unsafe.Pointer` and `cgo` unless there's a measured, documented reason — both defeat the memory
safety and race detector guarantees the rest of this skill relies on. If used, isolate behind a small,
reviewed, well-tested package and leave a comment justifying it.

## Secrets
Read secrets from env / a secret manager; never hardcode, commit, log, or put them in errors. Redact
sensitive types with `slog.LogValuer` (see logging-observability.md) and keep them out of `String()`/`%v`.

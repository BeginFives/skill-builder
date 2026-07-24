# Security & input validation — Python

Load this when parsing, deserializing, executing, or fetching anything from outside the process —
request bodies, config files, uploaded archives, cache blobs, user-supplied URLs. Governing rule:
**everything crossing a trust boundary is hostile — never deserialize, execute, or shell out to it.**
`uvx pip-audit` scans your *dependencies*; this file is about the bugs *you* write. Enable ruff's
bandit rules (`lint.select = ["S"]`) to machine-catch part of it — the rest is `[SELF]`.

## Contents
- [Deserialization is remote code execution](#deserialization-is-remote-code-execution)
- [eval / exec / dynamic attribute access](#eval--exec--dynamic-attribute-access)
- [Command injection](#command-injection)
- [SQL injection](#sql-injection)
- [Path traversal & archive extraction](#path-traversal--archive-extraction)
- [SSRF: outbound requests to user-supplied URLs](#ssrf-outbound-requests-to-user-supplied-urls)
- [XML](#xml)
- [Bounding input size & decompression bombs](#bounding-input-size--decompression-bombs)
- [Passwords, tokens, secrets](#passwords-tokens-secrets)
- [Footguns worth one line each](#footguns-worth-one-line-each)

## Deserialization is remote code execution

`pickle.loads` on attacker-controlled bytes is RCE, not a parsing bug: `__reduce__` runs before you
inspect the result, so no post-load validation helps. **FAIL if untrusted bytes reach** `pickle` /
`marshal` / `shelve` / `dill` / `cloudpickle`, `joblib.load`, `pandas.read_pickle`,
`numpy.load(allow_pickle=True)`, `torch.load` (pass `weights_only=True`), a Celery/Redis/disk cache
whose codec is pickle, or any `.pkl` on shared storage. "It's only our own cache" is not a trust
argument — the *store* is the boundary, and whoever writes to it owns your process. Use JSON /
msgspec / protobuf into a declared schema. If a Python-object cache is truly unavoidable,
authenticate the bytes so `loads` only ever sees what you wrote:

```python
def seal(obj: object) -> bytes:                      # _KEY from the secret manager
    body = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    return hmac.digest(_KEY, body, hashlib.sha256) + body

def unseal(blob: bytes) -> object:
    tag, body = blob[:32], blob[32:]
    if not hmac.compare_digest(tag, hmac.digest(_KEY, body, hashlib.sha256)):
        raise ValueError("cache entry not authentic")   # never reaches loads
    return pickle.loads(body)  # noqa: S301 — authenticated above
```

**YAML is the same bug class.** `yaml.load(raw)` / `Loader=yaml.Loader` / `UnsafeLoader` executes
`!!python/object/apply:os.system ['id']`. Use `yaml.safe_load` (`safe_load_all` for streams) — it
raises `ConstructorError` on those tags — then check the shape before use.

## eval / exec / dynamic attribute access

**FAIL if user input reaches** `eval`, `exec`, `compile`, `__import__`, `importlib.import_module`, or
`getattr(obj, name)` with an unvalidated name; `{}` as globals is not a sandbox, because the object
graph is reachable from anything that leaks a builtin. The one narrow safe form is
`ast.literal_eval(raw)` — literals only, rejects `__import__('os').system('id')` with `ValueError` —
and bound `len(raw)` first, since deeply nested literals are a parser DoS. Two adjacent traps:
**attacker-supplied format strings** walk the object graph — verified,
`"{0.__class__.__init__.__globals__}".format(cfg)` reads module globals — so never
`.format`/`%`/`Template.substitute` a *template* that came from a user, only its arguments; and
**`jinja2.Environment()` defaults to
`autoescape=False`** (verified — `{{ x }}` renders `<script>` raw), so pass
`autoescape=select_autoescape()`. A user-supplied template is SSTI → RCE.

## Command injection

```python
ALLOWED_FORMATS = frozenset({"png", "webp", "jpeg"})

def convert(src: str, dst: str, fmt: str, *, timeout: float = 30.0) -> None:
    if fmt not in ALLOWED_FORMATS:
        raise ValueError(f"unsupported format: {fmt!r}")
    subprocess.run(["/usr/bin/ffmpeg", "-i", src, "-f", fmt, dst], check=True,
                   capture_output=True, timeout=timeout, shell=False,
                   env={"PATH": "/usr/bin:/bin"})
```

- **FAIL if `shell=True` appears anywhere near interpolation** — likewise `os.system` / `os.popen`.
  With an args list the metacharacters stay one argv entry and are inert (verified: `"; rm -rf /"`
  arrives as literal text). `shlex.split` is a parser, not a sanitizer.
- `shlex.quote` only where a shell is unavoidable (`ssh host f"cat -- {shlex.quote(p)}"`, `sudo sh -c`);
  quote each *value*, never the assembled string.
- Absolute path or a pinned `env["PATH"]` — inherited `PATH` is attacker-influenced in many deploys.
  Always `timeout=`; `check=True` so a failure is not silently ignored.

## SQL injection

Parameterize — always (see data-stores.md). **FAIL if SQL is built with an f-string, `%`, `+`, or
`.format`**, including "just the WHERE clause" and ORM escapes like `.filter(text(f"..."))`. Pass
values as the driver's second argument (`cur.execute("... WHERE id = %s", (uid,))`) or bound params
(`text("... :id"), {"id": uid}`). Identifiers and `ORDER BY` columns **cannot** be parameterized —
map them through a literal allowlist dict; never quote-and-hope.

## Path traversal & archive extraction

```python
def resolve_within(root: Path, user_path: str) -> Path:
    root = root.resolve(strict=True)
    # `root / "/etc/passwd"` silently discards root, so this check is mandatory.
    target = (root / user_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"path escapes root: {user_path!r}")
    return target
```

`resolve()` before comparing — it also collapses symlinks aimed out of the root, and stripping `".."`
textually is not equivalent (verified rejects: `../../etc/passwd`, `/etc/passwd`, `sub/../../escape`).
Archive members are user paths too — the classic `tarfile` CVE is a member named `../../x`:

```python
with tarfile.open(archive, "r:*") as tf:
    if sum(m.size for m in tf) > MAX_UNCOMPRESSED:  # header metadata: cheap check, not truth
        raise ValueError("archive too large when expanded")
    tf.extractall(dest, filter="data")
```

`filter="data"` is **required on 3.11–3.13** (omitting it gets the unsafe legacy behavior plus a
DeprecationWarning); 3.14+ defaults to it. Verified: it raises `tarfile.OutsideDestinationError` on a
traversing member. `zipfile` will not create symlinks, but still put every name through
`resolve_within` and cap bytes as you copy.

## SSRF: outbound requests to user-supplied URLs

Assume the URL points at `169.254.169.254` (cloud metadata) or an internal admin port.

```python
ALLOWED_SCHEMES = frozenset({"https"})

def safe_target(raw: str) -> tuple[str, int, list[str]]:
    u = urlsplit(raw)
    if u.scheme not in ALLOWED_SCHEMES:          # blocks file://, gopher://, data:
        raise ValueError(f"scheme not allowed: {u.scheme!r}")
    if not (host := u.hostname):
        raise ValueError("missing host")
    port, ips = u.port or 443, []
    for *_, sockaddr in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(sockaddr[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError(f"blocked address {ip} for host {host!r}")
        ips.append(str(ip))                      # connect to these, not to `host`
    return host, port, ips
```

Verified rejects: `file://`, `http://` (not allowlisted), `127.0.0.1`, `169.254.169.254`, `[::1]`,
`10.0.0.5`. Two follow-ups or the check is theater: **redirects re-enter the trust boundary**
(`requests`/`httpx` follow by default — set `allow_redirects=False` and re-validate each hop), and
the name can re-resolve between check and connect (DNS rebinding), so dial the **pinned** IP with the
`Host` header set. An egress firewall is the real fix.

## XML

Use `defusedxml` — `defusedxml.ElementTree.fromstring` raises `EntitiesForbidden` on an XXE payload
(verified). Why not the defaults: stdlib `xml.etree` does **not** fetch external entities (verified:
"undefined entity") but *does* expand internal ones, so billion-laughs is a live memory DoS; lxml on
current libxml2 also refuses external entities, but `resolve_entities=True` — still in half the
examples online — restores full local-file disclosure (verified reading a file off disk). Version-
dependent defaults are not a security control. Forced onto lxml:
`etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)`. Same care for `xmlrpc`,
`plistlib`, and SOAP clients; never build XML/HTML by string concatenation.

## Bounding input size & decompression bombs

```python
def copy_bounded(src: IO[bytes], dst: IO[bytes], limit: int) -> None:
    written = 0
    while chunk := src.read(64 * 1024):
        written += len(chunk)
        if written > limit:
            raise ValueError("decompressed stream exceeds limit")
        dst.write(chunk)
```

- **Declared sizes are attacker metadata.** `Content-Length`, the gzip trailer, and `ZipInfo.file_size`
  are all forgeable — bound the bytes you actually read (verified: 10 MiB of `A` compresses to ~10 KB).
- Bounded inflate without a file: `d = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)`,
  `out = d.decompress(blob, max_length=limit)`, then **`if not d.eof or d.unconsumed_tail: raise`** —
  `max_length` truncates silently, it does not error.
- Cap the request body before the parser sees it (ASGI frameworks do not by default); bound anything
  used as a count or allocation size; 3.11+ already caps `int("9" * 5000)` at 4300 digits with
  `ValueError` — do not raise `sys.set_int_max_str_digits()` for untrusted input.
- `re` has no timeout: a user-supplied pattern, or a nested-quantifier pattern over user input, is
  catastrophic backtracking. Bound input length, or use `regex` with `timeout=`.

## Passwords, tokens, secrets

```python
_ph = PasswordHasher()                        # argon2-cffi; argon2id defaults

def verify_password(stored: str, password: str) -> tuple[bool, str | None]:
    """Return (ok, new_hash_if_params_outdated)."""     # rehash on login keeps params current
    try:
        _ph.verify(stored, password)
    except (VerifyMismatchError, InvalidHashError):
        return False, None
    return True, _ph.hash(password) if _ph.check_needs_rehash(stored) else None
```

- **FAIL if a password is hashed with sha256/sha1/md5** — salted or not, HMAC'd or not: they are fast
  by design, which is precisely what an offline cracker wants. argon2id (above) or bcrypt; stdlib-only
  fallback `hashlib.scrypt`. bcrypt silently truncates at 72 bytes — pre-hash, or use argon2.
- Tokens, reset links, session ids, nonces: `secrets.token_urlsafe(32)`. **FAIL if `random`** — the
  Mersenne Twister is reconstructible from observed output, so one leaked token predicts the rest.
- Compare secrets with `hmac.compare_digest` (`secrets.compare_digest` is the same object), never `==`;
  store only `sha256(token)` server-side, since a token is a password you issued.
- Secrets never reach logs, `repr`, exception messages, or URLs — wrap them so they cannot: a
  `__slots__` holder whose `__repr__` returns `"Secret(***)"`, with `__str__ = __repr__` and an
  explicit `.reveal()`, or pydantic `SecretStr` (verified: the value survives neither `f"{s}"` nor
  `f"{s!r}"`). See logging-observability.md for redaction at the sink.

## Footguns worth one line each

- **`assert user.is_admin` is not an authz check** — `python -O` deletes it (verified). Raise instead.
- Non-security digests: `hashlib.md5(b, usedforsecurity=False)` — states intent, survives FIPS builds.
- `tempfile.mktemp` is a TOCTOU race — use `NamedTemporaryFile`/`mkstemp`; create secret files with
  `os.open(..., O_CREAT, 0o600)`, never a later `chmod`.
- Never `verify=False` / `ssl._create_unverified_context()` — "just in staging" is how that line ships.
- JWTs: verify the signature and pin `algorithms=[...]`; `options={"verify_signature": False}` in
  application code is a decoder, not an authenticator.
- Keep "it parsed" distinct from "it is valid" — range, length, and enum checks are yours to write.

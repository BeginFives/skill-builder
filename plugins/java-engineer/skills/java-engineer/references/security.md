# Security & input validation

Load this when parsing untrusted payloads (JSON/XML/serialized bytes), fetching user-supplied URLs,
touching the filesystem or a shell with user data, or sizing anything from a number an attacker sent.
Governing rule: **treat everything crossing a trust boundary as hostile — validate it, bound it,
parameterize it.** CVE scanners check your *dependencies*; this file is about the code *you* write.
Baseline Java 21 — records and sealed types are the enforcement mechanism here, not decoration.

## Contents
- [Deserialization — Java's signature RCE](#deserialization--javas-signature-rce)
- [XXE — XML parsers are unsafe by default](#xxe--xml-parsers-are-unsafe-by-default)
- [Injection: SQL, command, path](#injection-sql-command-path)
- [SSRF on user-supplied URLs](#ssrf-on-user-supplied-urls)
- [Bounding input](#bounding-input)
- [Logging injection & the Log4Shell class](#logging-injection--the-log4shell-class)
- [Secrets, tokens & randomness](#secrets-tokens--randomness)

## Deserialization — Java's signature RCE

**FAIL if `ObjectInputStream.readObject` ever sees bytes you did not produce and authenticate.** Native
serialization instantiates and runs code named *by the payload* — a gadget chain in any classpath jar turns
"parse this cookie" into RCE, and the damage happens *during* `readObject`, so there is nothing left to
validate afterwards. Use JSON/protobuf into a `record` with an explicit schema; serialized Java bytes are
never a wire format, cache value, session cookie, or queue payload for anything externally reachable. If
legacy forces it, HMAC the bytes first and install a JEP 290 deny-by-default filter:

```java
ois.setObjectInputFilter(ObjectInputFilter.Config.createFilter(
    "maxdepth=5;maxarray=1000;maxbytes=16384;com.acme.Token;java.lang.String;!*")); // !* denies the rest
```

**Jackson polymorphic typing is the same footgun**: `activateDefaultTyping` / `enableDefaultTyping` /
`@JsonTypeInfo(use = Id.CLASS)` let the JSON name the class to construct. A `PolymorphicTypeValidator`
narrows that; a **sealed interface + `Id.NAME` + explicit `@JsonSubTypes`** removes the choice, closing
the constructible set at compile time.

```java
@JsonTypeInfo(use = JsonTypeInfo.Id.NAME, property = "kind")
@JsonSubTypes({@JsonSubTypes.Type(value = Card.class, name = "card"),
               @JsonSubTypes.Type(value = Ach.class,  name = "ach")})
sealed interface Payment permits Card, Ach {}

static final ObjectMapper MAPPER = JsonMapper.builder(
        JsonFactory.builder().streamReadConstraints(StreamReadConstraints.builder()   // Jackson 2.15+
            .maxStringLength(1 << 20).maxNestingDepth(64).maxNumberLength(1000).build()).build())
    .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)   // unknown field = reject, not ignore
    .enable(DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES).build();
```

`StreamReadConstraints` stops a 10 KB body costing gigabytes. **Parse success is not validity** — a
compact-constructor check on the record, or `@Valid` at the edge, still has to run.

## XXE — XML parsers are unsafe by default

Every JAXP factory resolves external entities unless told not to: `<!ENTITY x SYSTEM "file:///etc/passwd">`
exfiltrates files, nested entities ("billion laughs") exhaust the heap, `http://` entities give blind SSRF.
**FAIL if a factory parsing untrusted XML is built without hardening.** Build them once and inject that
factory; never `newInstance()` inline.

```java
DocumentBuilderFactory f = DocumentBuilderFactory.newInstance();
f.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);   // the one that matters
f.setFeature("http://xml.org/sax/features/external-general-entities", false);
f.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
f.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
f.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
f.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
f.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
f.setXIncludeAware(false);
f.setExpandEntityReferences(false);
```

- `SAXParserFactory`: the same four `setFeature` calls plus `FEATURE_SECURE_PROCESSING`.
- `XMLInputFactory` (StAX): `setProperty(SUPPORT_DTD, false)` **and** `IS_SUPPORTING_EXTERNAL_ENTITIES,
  false`. It skips the DOCTYPE rather than rejecting it, so the error surfaces later as "entity was
  referenced, but not declared" — safe, just not a clean "DOCTYPE disallowed".
- Same for `TransformerFactory`, `SchemaFactory`, `Validator`, and JAXB (hand `Unmarshaller` a hardened
  `XMLStreamReader`, never a raw `InputStream`): `ACCESS_EXTERNAL_DTD`/`_STYLESHEET` set to `""`.
- `disallow-doctype-decl` throws on *any* DOCTYPE — a producer legitimately sending one is a contract to
  renegotiate, not a flag to flip back.

## Injection: SQL, command, path

**SQL — parameterize; the only escaping that works is not concatenating.**

```java
private static final Map<String, String> SORT_COLUMNS = Map.of("email", "u.email", "created", "u.created_at");

String col = SORT_COLUMNS.get(sortKey);                    // identifiers can't be bound → allowlist them
if (col == null) throw new IllegalArgumentException("bad sort key");
try (PreparedStatement ps = c.prepareStatement(
        "SELECT u.id, u.email FROM users u WHERE u.email LIKE ? ORDER BY " + col + " LIMIT ?")) {
  ps.setString(1, emailLike);
  ps.setInt(2, Math.clamp(limit, 1, 500));                 // Java 21+: bind AND clamp the limit
  try (ResultSet rs = ps.executeQuery()) { return mapUsers(rs); }
}
```

WRONG: a string-built `WHERE`, JPA `createQuery("... where name = " + name)`, or a Spring Data `@Query`
containing `#{...}` SpEL derived from a request parameter (SpEL injection ⇒ RCE).

**Command — pass an args list; there is no shell to escape.**

```java
if (!size.matches("\\d{1,4}x\\d{1,4}")) throw new IllegalArgumentException("bad size");
Process p = new ProcessBuilder("convert", in.toString(), "-resize", size, out.toString()).start();
```

FAIL on `new ProcessBuilder("sh", "-c", "convert " + arg)` or `Runtime.getRuntime().exec(String)` — the
single-String `exec` splits on whitespace, a different bug with the same blast radius. Validate each arg
even in list form; a leading `-` still smuggles a flag.

**Path traversal — normalize, then prove containment.**

```java
Path base = root.toRealPath();                      // resolve symlinks on the root once
Path p = base.resolve(userPath).normalize();        // collapse ../ BEFORE the check
if (!p.startsWith(base)) throw new AccessDeniedException(userPath);
```

Reverse those two lines and `../` still escapes. `String.startsWith` is not containment (`/data/user2`
starts with `/data/user`) — use `Path.startsWith`, and `toRealPath()` the result if it may be a symlink.

## SSRF on user-supplied URLs

An attacker aims your fetch at `169.254.169.254` (cloud metadata ⇒ IAM credentials) or an internal admin
port. Allowlist the scheme, then reject internal addresses **after DNS resolution, for every record**:

```java
static URI vetOutboundUrl(String raw) throws IOException {
  URI u = URI.create(raw).normalize();
  if (u.getScheme() == null || !u.getScheme().equalsIgnoreCase("https"))
    throw new IOException("scheme not allowed: " + u.getScheme());
  if (u.getHost() == null || u.getHost().isBlank()) throw new IOException("no host");
  for (InetAddress a : InetAddress.getAllByName(u.getHost()))
    if (isInternal(a)) throw new IOException("blocked target: " + a);
  return u;
}

static boolean isInternal(InetAddress a) {
  if (a.isLoopbackAddress() || a.isAnyLocalAddress() || a.isLinkLocalAddress()) return true;
  if (a.isSiteLocalAddress() || a.isMulticastAddress()) return true;
  byte[] b = a.getAddress();                                              // ranges the JDK predicates miss:
  if (b.length == 4) {
    int b0 = b[0] & 0xff, b1 = b[1] & 0xff;
    return (b0 == 100 && b1 >= 64 && b1 <= 127) || (b0 == 192 && b1 == 0); // 100.64/10 CGNAT, 192.0.0/24
  }
  return (b[0] & 0xfe) == 0xfc;                                            // fc00::/7 IPv6 unique-local
}
```

`HttpClient.newBuilder().followRedirects(Redirect.NEVER)` is **not optional** — a `302` to
`http://169.254.169.254/` walks straight past the pre-check; handle redirects yourself, re-vetting each hop.
This stays TOCTOU-racy against DNS rebinding, so where it matters the real control is at the network edge
(egress proxy, allowlisted security group) and this is defense in depth. Every outbound call also gets
`connectTimeout` + per-request `timeout` and a bounded response body.

## Bounding input

Untrusted numbers must never size an allocation; untrusted streams must never run unbounded.

- **Bodies:** cap at the edge (`spring.codec.max-in-memory-size`, `server.tomcat.max-http-form-post-size`,
  proxy `client_max_body_size`) *and* in code — `byte[] b = in.readNBytes(max + 1); if (b.length > max)
  throw …`. Reading one past the limit detects overflow instead of silently truncating.
- **Collections:** `new ArrayList<>()`, never `new ArrayList<>(header.itemCount)`; a claimed count of
  `Integer.MAX_VALUE` is a one-packet OOM. Same for `new byte[len]` / `ByteBuffer.allocate(len)`.
- **Decompression bombs:** count *decompressed* bytes while copying and abort at a cap — `ZipEntry.getSize()`
  is attacker-supplied metadata, never a bound. Cap entry count too.
- **Zip slip** is path traversal wearing a hat: `Path out = root.resolve(e.getName()).normalize(); if
  (!out.startsWith(root)) throw new IOException("zip slip: " + e.getName());` before opening the output.

## Logging injection & the Log4Shell class

**FAIL if untrusted input reaches the log *message pattern* instead of an argument.** Log4Shell was exactly
that: `log.info(userAgent)` made the message a template, and the template language could reach JNDI/LDAP
and load a remote class. `log.info("request rejected: {}", userAgent)` is a constant pattern with a data
argument and never expands attacker text as syntax — the rule that keeps messages greppable keeps them
safe. `String.format(fmt, …)` and `"…" + userInput` fail it identically.

Then truncate and strip control characters from the values —
`s.substring(0, Math.min(s.length(), max)).replaceAll("\\p{Cntrl}", "_")` — because a `\r\n` inside a
username forges a whole log entry ("admin logged in"), which is how audit trails get laundered. Structured
JSON logging makes forgery moot for the *value*, but truncation still matters: an unbounded field turns the
log pipeline into the DoS target. See logging-observability.md.

## Secrets, tokens & randomness

- **`SecureRandom`, never `Random`/`Math.random`/`ThreadLocalRandom`** for tokens, session IDs, resets or
  nonces — `Random` is a 48-bit LCG whose whole future is recoverable from two outputs. A shared
  `new SecureRandom()` is thread-safe and non-blocking; reserve `getInstanceStrong()` for long-lived key
  material, as it can block on entropy-starved hosts. 32 random bytes →
  `Base64.getUrlEncoder().withoutPadding().encodeToString(b)`.
- **Compare secrets with `MessageDigest.isEqual(a, b)`**, not `String.equals` — `equals` short-circuits on
  the first differing byte, a timing oracle for token/HMAC forgery.
- **`char[]` over `String` wherever the API allows** (`KeyStore.load`, `PBEKeySpec`, `Console.readPassword`):
  a `String` is immutable and lives in the heap until GC, so it lands in every heap dump and crash report.
  `Arrays.fill(pw, '\0')` in a `finally`.
- **Never hardcode, commit, log, or `toString()` a secret.** Records auto-generate a `toString()` printing
  every component, so `record DbConfig(String url, String password)` leaks by default — override it.
- Passwords: Argon2id or bcrypt with a per-user salt, never a bare `MessageDigest`. **Java 25** adds
  `javax.crypto.KDF` (JEP 510) for real HKDF instead of hand-rolled derivation — `KDF.getInstance(
  "HKDF-SHA256").deriveKey("AES", HKDFParameterSpec.ofExtract().addIKM(ikm).addSalt(salt)
  .thenExpand(info, 32))`.
- The `SecurityManager` is permanently disabled as of **Java 24** — there is no in-JVM sandbox to fall back
  on. Least privilege now lives in the container, the DB grants, and the network policy.

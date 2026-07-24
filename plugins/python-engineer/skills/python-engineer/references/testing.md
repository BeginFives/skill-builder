# Testing — Python

Load this when writing or reviewing tests. Governing rule: **test observable behavior through the
public API — parametrized, hermetic, deterministic.** A test that only asserts on mocks tests your
mocks: it stays green while the code it "covers" breaks.

## Contents
- [Baseline config](#baseline-config)
- [Parametrize the table-driven form](#parametrize-the-table-driven-form)
- [Fixtures and the conftest boundary](#fixtures-and-the-conftest-boundary)
- [Exception paths](#exception-paths)
- [Mock discipline](#mock-discipline)
- [Async and cancellation](#async-and-cancellation)
- [Property-based tests](#property-based-tests)
- [Integration and real dependencies](#integration-and-real-dependencies)
- [Determinism](#determinism)
- [Coverage](#coverage)

## Baseline config
`pytest`, not `unittest`. Tests mirror the package under `tests/`; the command is `uv run pytest -q`.
```toml
[tool.pytest.ini_options]
addopts = "-q --strict-markers --strict-config -m 'not integration'"
testpaths = ["tests"]
markers = ["integration: needs real external services"]
filterwarnings = ["error"]   # a Deprecation/ResourceWarning is a failure, not a log line
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```
- Plain `assert got == want`; pytest rewrites it to print both sides. FAIL if `assert response` or
  `assert result is not None` is the whole check — on failure it tells you nothing actionable.
- Rewriting reaches only test modules and `conftest.py`: a shared assertion helper elsewhere prints a
  bare `AssertionError` until the root conftest calls `pytest.register_assert_rewrite("testsupport")`.

## Parametrize the table-driven form
One test function, a case list, an explicit `id=` on every case so a CI failure names itself.
```python
@pytest.mark.parametrize(
    ("raw", "want"),
    [
        pytest.param("10", 1000, id="whole"),
        pytest.param("-0.01", -1, id="negative"),
    ],
)
def test_parse_amount(raw: str, want: int) -> None:
    assert parse_amount(raw) == want
```
Parametrize the *rejection* cases the same way, over `(raw, want_msg)` with `pytest.raises`. FAIL if a
`for` loop inside one test body replaces parametrize — the first failure hides every later case.

## Fixtures and the conftest boundary
`conftest.py` serves its directory subtree; put each fixture at the **closest common ancestor** of its
users, never in a root grab-bag. FAIL if `conftest.py` holds test functions or imports test modules.
```python
# tests/conftest.py
@pytest.fixture(scope="session")
def rate_table() -> dict[str, Decimal]:      # expensive AND immutable -> build once
    return {"EUR": Decimal("0.9")}

@pytest.fixture                              # function scope for anything mutable
def rates(rate_table: dict[str, Decimal]) -> Iterator[FakeRates]:
    fake = FakeRates(dict(rate_table))       # copy — never hand out the session object
    yield fake
    fake.calls.clear()                       # code after yield runs even when the test fails
```
```python
def test_config_prefers_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "app.toml"                      # per-test dir; never the CWD or /tmp/fixed
    cfg.write_text("[server]\nport = 8080\n", encoding="utf-8")
    monkeypatch.setenv("APP_CONFIG", str(cfg))       # both undone at teardown
    monkeypatch.delenv("APP_PORT", raising=False)
    assert load_config().port == 8080
```
- FAIL if a session/module-scoped fixture yields mutable state: one test mutates it, another fails
  only in a particular order — the classic irreproducible CI flake.
- FAIL if `os.environ[...] = ...` or a hardcoded temp path appears in a test: the first leaks into
  every test that runs after it, the second collides under `-n auto`.
- `autouse=True` is invisible global state — reserve it for guard rails (see hermeticity below).

## Exception paths
```python
def test_parse_amount_rejects_sub_cent() -> None:
    with pytest.raises(ParseError, match=r"invalid amount: '1\.005'"):   # note the escaped dot
        parse_amount("1.005")

def test_parse_amount_preserves_cause() -> None:
    with pytest.raises(ParseError) as exc_info:
        parse_amount("abc")
    assert isinstance(exc_info.value.__cause__, InvalidOperation)  # `raise ... from exc` held
```
- `match=` is `re.search` over `str(exc)`, not equality: an unescaped `.` matches anything (green test,
  wrong message) and an unescaped `(` is a regex error. `re.escape(...)` for literal text.
- FAIL if `pytest.raises(Exception)` guards a domain error — it also swallows the `AttributeError`
  from your own typo, so the test passes for a reason unrelated to the behavior under test.
- For `TaskGroup` failures (3.11+ `ExceptionGroup`) assert *into* the group, never unwrap it:
  `assert exc_info.group_contains(ValueError, match="boom")` (pytest 8+).

## Mock discipline
**Patch where the name is looked up, not where it is defined.** The most common mocking mistake, and
it fails *silently* — green test, unpatched production path.
```python
# app/report.py
from app.clock import now          # <- the name is bound into app.report at import time

def build() -> Report:
    return Report(created_at=now())

# WRONG — app.report still holds its own reference; the real clock runs and the assert lies.
with patch("app.clock.now", return_value=FROZEN): ...
# RIGHT — patch the name in the module that looks it up.
with patch("app.report.now", return_value=FROZEN):
    assert build().created_at == FROZEN
```
- **`autospec=True` always.** A bare `MagicMock` answers to any attribute: `client.serch("EUR")`
  returns a Mock and the suite is green forever. `create_autospec(Rates, instance=True)` raises
  `AttributeError` on that typo and `TypeError` when the real signature drifts under you.
- **Prefer a real object, then a fake, then a mock — in that order.** A fake is a small in-memory
  implementation of the same `Protocol`; it exercises real call semantics and survives refactors.
- **FAIL if a test's only assertions are `mock.assert_called_once_with(...)`** — that asserts the test's
  own wiring. Assert the return value or the state change; assert on calls only when the call *is* the
  contract ("we do not retry a 4xx").
- Patch code you own — your adapter — not a library's internals; `patch("httpx.Client.send")` breaks
  on their next release. `monkeypatch.setattr` and pytest-mock's `mocker` undo themselves, while a
  bare `patch()` started outside a `with` block leaks into the rest of the session.

## Async and cancellation
With `asyncio_mode = "auto"` an `async def test_*` just runs. Test what production actually does —
cancellation and timeout — not only the happy await.
```python
async def test_worker_releases_resources_on_cancel() -> None:
    w = Worker()
    task = asyncio.create_task(w.run())
    await w.started.wait()            # sync on an Event — NEVER on asyncio.sleep()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert w.cleaned_up               # the `finally` really ran

async def test_timeout() -> None:
    with pytest.raises(TimeoutError):  # 3.11+: asyncio.TimeoutError is an alias of the builtin
        async with asyncio.timeout(0.01):
            await never_returns()
```
FAIL if a test sleeps to "let the task get going" — a race tuned to your laptop that flakes on a loaded
CI box. Anything with a cancellation path gets a cancellation test, or its `finally` goes unexercised
under exactly the conditions it exists for.

## Property-based tests
Every parser, serializer, and codec gets `hypothesis`. Table cases encode what you thought of;
properties find what you did not.
```python
@given(st.integers(min_value=-(10**12), max_value=10**12))
def test_format_parse_roundtrip(cents: int) -> None:
    assert parse_amount(format_amount(cents)) == cents

@given(st.text(max_size=32))  # the property that finds crashes
def test_parse_rejects_cleanly(raw: str) -> None:
    with contextlib.suppress(ParseError):
        parse_amount(raw)             # any OTHER exception type fails the property
```
That second property found `Decimal("INFINITY")` → `OverflowError` in under a second — a real crash no
hand-written table would have listed. Pin each counterexample with `@example(...)` so it becomes a
permanent regression case, and cache `.hypothesis/examples` in CI so finds are replayed.

## Integration and real dependencies
Unit tests are hermetic. Enforce that rather than trusting it:
```python
@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("unit tests must not touch the network")
    monkeypatch.setattr(socket.socket, "connect", deny)
```
- HTTP boundaries: `httpx.MockTransport(handler)` runs the real client machinery (timeouts, redirects,
  encoding) against a fake wire — strictly better than mocking the client object.
- A real dependency gets a real container, not a mocked driver: mocking a driver tests your
  understanding of the query rather than the query.
  ```python
  @pytest.fixture(scope="session")
  def pg_url() -> Iterator[str]:
      with PostgresContainer("postgres:16-alpine") as pg:
          yield pg.get_connection_url()
  ```
- Mark them `@pytest.mark.integration`, deselected by the `addopts` above, run explicitly with
  `uv run pytest -m integration`. `--strict-markers` turns a typo'd marker into an error instead of a
  suite that silently never runs.

## Determinism
- **Inject the clock** — `datetime.now()` called inside the unit under test is untestable. Take it as a
  field: `clock: Callable[[], datetime] = _utc_now`, and pass `clock=lambda: t0` from the test.
  `freezegun`/`time-machine` are the fallback for third-party code you cannot inject into — not the
  first choice, because they patch time globally for the whole process.
- **Never sleep to synchronize** — not `time.sleep`, not `asyncio.sleep`. Use `Event`, `Queue`, or
  `join()`. A sleep is a bet on machine speed, and CI eventually calls it.
- Seed randomness explicitly (an injected `random.Random(1234)`, never the module global), and add
  `pytest-randomly` so order is shuffled: an order-dependent test then fails on your machine rather
  than during someone else's release. Never assert on `set` order — sort before comparing.

## Coverage
`[tool.coverage.run] branch = true` plus `[tool.coverage.report] fail_under = 85` and `exclude_also`
for `if TYPE_CHECKING:` / `raise NotImplementedError` / `@overload`. Coverage is a floor, never a
target, and `branch = true` is the half that earns its keep: the uncovered `except` branch is exactly
where the production bug lives. FAIL if a new error path ships with only its happy path exercised.

# Project layout, typing, DI & boundaries — Python

Load this when structuring a package, defining a type that crosses a layer, wiring dependencies, or
loading config. Governing rule: **every boundary is typed and machine-checked (`mypy --strict` clean,
no `Any` escaping a public signature), and every dependency arrives through a constructor — never a
module-level global or an import-time side effect.**

## Contents
- [Layout](#layout)
- [pyproject.toml — the single config file](#pyprojecttoml--the-single-config-file)
- [Typing at boundaries](#typing-at-boundaries)
- [Dataclasses over dicts](#dataclasses-over-dicts)
- [Protocol vs ABC](#protocol-vs-abc)
- [Constructor injection; no import-time work](#constructor-injection-no-import-time-work)
- [The two evaluation-time bugs](#the-two-evaluation-time-bugs)
- [Circular imports](#circular-imports)
- [Config & secrets](#config--secrets)

## Layout
```
pyproject.toml
src/acme_orders/__init__.py         # re-exports only; executes no work
src/acme_orders/__main__.py         # entrypoint: the ONLY place that constructs concretes
src/acme_orders/config.py           # typed settings, validated at startup
src/acme_orders/domain/order.py     # types + the Protocols the domain needs; imports no adapter
src/acme_orders/adapters/memory.py  # implementations; import domain, never the reverse
tests/
```
- **Use `src/`.** Without it `cwd` is `sys.path[0]`, so `import acme_orders` resolves to the working
  copy and tests never exercise what you ship — a missing `packages =`, an unlisted data file, or a
  module absent from the wheel passes CI and fails on install. With `src/`, an uninstalled import
  raises `ModuleNotFoundError`: tests can only pass against the installed artifact.
- Dependencies point **inward**: `adapters → domain`, never back. `__main__` may import everything.
- Modules are lowercase nouns for their contents (`order`, `billing`). FAIL on `utils.py`,
  `helpers.py`, `common.py` — an unnamed responsibility is where import cycles come from.

## pyproject.toml — the single config file
One file configures packaging *and* every tool. FAIL if lint/type/test settings live in `setup.cfg`,
`mypy.ini`, `.flake8`, or CI-only CLI flags — split config drifts and CI stops matching local runs.
```toml
[project]
name = "acme-orders"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["pydantic-settings>=2.0"]
[project.scripts]
acme-orders = "acme_orders.__main__:main"
[dependency-groups]                       # PEP 735: dev deps are NOT runtime deps; pin them so
dev = ["mypy>=1.11", "pytest>=8", "ruff>=0.6"]   # local and CI resolve the same versions
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["src/acme_orders"]            # src/ layout must be declared, not inferred

[tool.ruff]
line-length = 100
[tool.ruff.lint]
select = ["E", "F", "B", "I", "UP", "SIM", "RUF", "ASYNC", "S", "TID", "PL"]  # B = bugbear
[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]                     # assert is the point in tests

[tool.mypy]
python_version = "3.11"                   # check the version you SHIP, not the one you run
strict = true
warn_unreachable = true
files = ["src", "tests"]
[[tool.mypy.overrides]]                   # per-module escape hatch; never a blanket ignore
module = ["untyped_third_party.*"]
ignore_missing_imports = true
```

## Typing at boundaries
Hints on every public function, method, and dataclass field; `mypy --strict` (or `pyright`) is a
**gate**, not advice.
- **FAIL if `Any` appears in a public signature.** `Any` is not "unknown", it is "stop checking".
  Verified: `mypy --strict` *and* `pyright` report **zero** errors on
  `def f(p: dict[str, Any]) -> None: print(p["totl"].centss + "x")` — misspelled key, nonexistent
  attribute, `str + Decimal`, all invisible. The same typo behind a dataclass is a check-time
  `"Order" has no attribute "totl"; maybe "total"?`.
- Genuinely dynamic input takes `object` and narrows via `isinstance`/`match` (or a `TypedDict` /
  `Protocol`) — `object` forces the narrowing that `Any` skips.
- `# type: ignore[code]` names the code and carries a reason; bare `# type: ignore` is a FAIL — it
  suppresses future unrelated errors on that line forever. Stubs are dependencies: add `types-*`
  rather than widening `ignore_missing_imports`.
- Accept the widest abstract type you use (`Iterable`/`Sequence`/`Mapping`), return the concrete one —
  an `Iterable` return leaks your laziness decision to every caller.
```python
from collections.abc import Sequence     # NOT typing.Sequence (deprecated alias)
from typing import Literal, Self         # Self (3.11+): fluent methods, classmethod ctors

type OrderId = UUID                      # PEP 695, 3.12+; on 3.11: OrderId: TypeAlias = UUID
Status = Literal["pending", "shipped", "cancelled"]   # closed set -> exhaustive `match` checking
```

## Dataclasses over dicts
**A `dict[str, Any]` passed between layers is the top design smell in Python review.** No declared
keys, no invariants, no failure until something downstream reads a key nobody wrote. Parse into a
typed object at the edge; pass the object.
```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Order:
    id: OrderId
    status: Status
    total: Decimal
    tags: tuple[str, ...] = ()                       # immutable default; NOT list[str] = []
    lines: list[str] = field(default_factory=list)   # mutable default MUST use default_factory

    def __post_init__(self) -> None:                 # invariants enforced once, at construction
        if self.total < 0:
            raise ValueError(f"total must be non-negative, got {self.total}")
```
- `frozen=True` → hashable, safe to share across threads/tasks, and mutation raises at the mutation
  site instead of surfacing as a mystery three layers later. `slots=True` → no per-instance
  `__dict__`: less memory, faster attribute access, typo'd assignment raises instead of silently
  creating a field (and saves nothing if any base class is unslotted).
- Two verified `slots=True` traps, both because the decorator rebuilds the class: zero-arg `super()`
  raises `TypeError: super(type, obj)…` on **3.11–3.13** (fixed in 3.14) — use `super(Child, self)`;
  and `functools.cached_property` raises `TypeError: No '__dict__' attribute` on **every** version.
- `pydantic.BaseModel` where data is *untrusted input* needing coercion (HTTP bodies, config, queue
  messages); dataclasses for internal types you construct yourself.

## Protocol vs ABC
**Define the `Protocol` at the CONSUMER**, listing only the methods that consumer calls. The
implementation never imports it, so the dependency arrow stays inward and any correctly-shaped object
is a test fake.
```python
class OrderStore(Protocol):            # declared in domain/order.py, next to its user
    def get(self, order_id: UUID) -> Order | None: ...
    def save(self, order: Order) -> None: ...

class InMemoryOrderStore:              # in adapters/; imports Order, not OrderStore
    def __init__(self) -> None:
        self._rows: dict[UUID, Order] = {}
    def get(self, order_id: UUID) -> Order | None:
        return self._rows.get(order_id)
    def save(self, order: Order) -> None:
        self._rows[order.id] = order
```
- Reach for **ABC** only when you own the hierarchy *and* share implementation (a template method), or
  need `isinstance` to mean something; `@abstractmethod` then blocks instantiation at runtime.
- FAIL on `@runtime_checkable` used as validation: `isinstance` checks **method names only** —
  verified `True` for a class whose `save()` takes no arguments at all.
- FAIL on a Protocol with one implementation and no test seam — that is a header file, not a boundary.

## Constructor injection; no import-time work
```python
class OrderService:
    def __init__(self, *, store: OrderStore, logger: logging.Logger) -> None:
        self._store, self._logger = store, logger  # keyword-only deps; nothing constructed here

def main() -> int:                                 # __main__.py — the composition root
    logging.basicConfig(level=logging.INFO)
    settings = Settings()                          # env-populated; validates or raises here
    service = OrderService(store=PostgresOrderStore(settings.database_url),
                           logger=logging.getLogger("acme.orders"))
    return run(service, settings)
```
- FAIL on module-level `db = create_engine(os.environ["DB_URL"])`, `client = Client()`, or
  `logging.basicConfig()` outside the entrypoint: import order becomes load-bearing, `--collect-only`
  opens sockets, and no test can substitute the dependency.
- `__init__.py` re-exports and sets `__all__`. Nothing else. FAIL if importing the package reads env,
  opens a connection, or configures logging.
- **Scope discipline:** this is how to build a *new* component — do not add a constructor, Protocol, or
  settings class to wire in one new field (SKILL.md → Scope discipline).

## The two evaluation-time bugs
Same root cause: Python evaluates defaults once at `def` time, and closures capture *variables*, not
values. Ruff catches both (`B006`, `B023`) **only when `B` is in `select`** — hence the config above.
```python
def add(item: str, bucket: list[str] = []) -> list[str]:   # WRONG: one list, shared forever
    bucket.append(item)                                    # add("a")->['a'] add("b")->['a','b']
    return bucket

def add(item: str, bucket: list[str] | None = None) -> list[str]:   # RIGHT
    bucket = [] if bucket is None else bucket
    bucket.append(item)
    return bucket
```
Same trap for `dict`, `set`, `datetime.now()`, any object built in a default; in dataclasses use
`field(default_factory=...)`.
```python
[lambda: n for n in names]              # WRONG: every lambda returns the LAST n -> c,c,c

def bind(n: str) -> Callable[[], str]:
    return lambda: n
[bind(n) for n in names]                # RIGHT: one frame -> one binding -> a,b,c
```
`lambda n=n: n` also works but `mypy --strict` rejects it (`Cannot infer type of lambda`) — prefer the
factory or `functools.partial`.

## Circular imports
A cycle is a **design signal — two modules share a responsibility that belongs in a third**, or an
arrow points the wrong way (an adapter imported by its domain). Fix the boundary. Function-local
imports and lazy `importlib` merely move `ImportError: cannot import name 'x' from partially
initialized module` to the first request.
```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:                     # legitimate: a type-only edge is not a runtime edge
    import logging
```
Caveat: `TYPE_CHECKING` imports do not exist at runtime, so anything introspecting annotations breaks
— verified `PydanticUserError: 'M' is not fully defined` for a `BaseModel` field typed that way.
Import normally for pydantic models, `get_type_hints`, and DI frameworks.

## Config & secrets
Load into a **typed, frozen settings object validated at startup**: a bad value kills the process at
boot, not as a `None` on the first request at 3am.
```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACME_", frozen=True, extra="forbid")

    database_url: PostgresDsn                  # no default -> missing env var fails at startup
    api_token: SecretStr                       # repr()s as '**********'; .get_secret_value() to read
    request_timeout_s: float = Field(default=5.0, gt=0, le=60)
    max_workers: int = Field(default=8, ge=1, le=64)
```
Without pydantic: a frozen dataclass with a `from_env(cls, env)` classmethod that collects **all**
missing/invalid keys and raises one `ValueError` — taking `env` as a parameter keeps it testable.
- FAIL on `os.environ["X"]` read at the point of use: the failure is deferred and untestable.
- `extra="forbid"` turns a typo'd `ACME_TIMOUT` into a boot error instead of a silently ignored knob.
- Secrets come from env or a secret manager, never a committed file; wrap them in `SecretStr` (or a
  type with a redacting `__repr__`) so they cannot leak via logs, tracebacks, or f-strings.
- Timeouts, pool sizes, and limits are typed config with documented defaults — not magic numbers.

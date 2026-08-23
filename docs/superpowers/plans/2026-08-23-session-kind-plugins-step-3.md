# Session-Kind Plugins — Step 3: Scenarios

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make scenario participation resolve through per-kind adapters instead of a hardcoded browser/terminal binary, so a plugin kind can join a scenario and core stops knowing which pool a participant lives in.

**Architecture:** Every kind supplies a `ScenarioAdapter` built by `create_scenario_adapter(pool)`. Browsers get a `BrowserScenarioAdapter` holding the code that lives inline in `scenarios_pool.py` today; plugins supply their own. Core narrows with `isinstance` against the four runtime-checkable capability Protocols step 1 already shipped, so a capability is derived from what an adapter implements and never declared. `Participant`'s ten terminal-specific fields collapse into a free-form `options: dict` that core passes through opaquely.

**Tech Stack:** Python 3.11+, `typing.Protocol` (`@runtime_checkable`), pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-session-kind-plugins-design.md` — this plan implements §7.1, §7.2, §7.3, §7.4, §7.5, and the step-3 line of §12.

**Depends on:**
- Steps 1 and 2 (`feat/session-kind-plugins-step-1`, PR #140 — now carrying both).
- `fix/recording-name-hyphen-ambiguity` (`5a71c66`). Step 3 is where plugin kinds first reach scenario YAML and recording filenames, which is exactly what that fix made safe. **Merge it before starting.**

## What step 1 already built — do not rebuild it

`src/octowright/plugins/contract.py` **already defines** all five Protocols and the derivation helper, and they are currently unconsumed:

- `ScenarioAdapter` (mandatory floor: `resolve_participant`)
- `SupportsMacros`, `SupportsSync`, `SupportsDialogPolicy`, `SupportsMockRoutes`
- `_CAPABILITY_PROTOCOLS` and `capabilities_of(adapter) -> frozenset[str]`
- `SessionKindPlugin.create_scenario_adapter(pool)`
- `PluginRegistry.register(...)` already stores `capabilities=capabilities_of(adapter)` on `LoadedPlugin`

**This step wires them. It does not define them.** A task that redefines a Protocol or re-derives capabilities has misread the plan.

## Global Constraints

- **SPDX header** on every new `.py` file, verbatim:
  ```python
  # SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
  # SPDX-License-Identifier: Apache-2.0
  # SPDX-Comment: Part of octowright.
  #
  ```
- **`from __future__ import annotations`** as the first import in every new module.
- **777-line cap** on any `src/**/*.py` (`scripts/check_max_loc.py`) — the project-wide limit. `scenarios_pool.py` is **550** and `scenarios.py` is **535**, so both have headroom, but this step adds to both: put dispatch helpers in the new module named in File Structure rather than growing `scenarios_pool.py` past ~700.
- **Ruff `select`** is `["E", "F", "I", "UP", "B", "SIM", "ARG", "RUF", "TID"]`, `line-length = 120`. `BLE`/`ANN`/`PLW` are NOT enabled — never add a `# noqa` for them; RUF100 flags unused directives.
- **`make lint` must exit 0.** Check the vulture gate with `uv run --active python scripts/check_vulture.py`, never by running `vulture` on one file. **Do not edit `.ci/vulture-baseline.json` or `.ci/xenon-baseline.json`;** both are ratchets. If xenon trips, decompose the function — never baseline it.
- **Commits must be signed.** Never `--no-gpg-sign` or `--no-verify`. If signing stalls, stop and ask.
- **Never** add a `Co-Authored-By` trailer or any AI-assistance mention to a commit message.
- **Do not touch `CHANGELOG.md`.** Do not push, do not open a PR.
- `pyproject.toml` sets `asyncio_mode = "auto"`, so `@pytest.mark.asyncio` is optional.
- Run tests with `uv run --active pytest`.
- **Terminal keeps its existing hardcoded branch.** Extraction is step 5. Every adapter-driven path in this plan is added *beside* the terminal branch, never replacing it. `tests/terminal/` and `tests/test_scenarios_terminal.py` must stay green.

## The one decision this plan makes that the spec left open

Spec §7.3 says "Terminal's adapter implements `resolve_participant` and nothing else," which reads as though terminal becomes adapter-driven in step 3. It does not — terminal is not a registered plugin until step 5, and registering it as an in-core pseudo-plugin was considered and rejected as scope that belongs with the extraction.

**Consequence for §7.2.** `Participant.options` still lands in this step, because the YAML break should happen once rather than twice. Core's terminal branch reads its connector settings out of `options` instead of dedicated fields. So the dataclass loses the ten terminal-specific fields now, while the terminal *branch* survives until step 5.

**Shape after this step:**

| Kind | Path |
|---|---|
| `terminal` | hardcoded branch, unchanged, deletes in step 5 |
| browser engines | `BrowserScenarioAdapter` |
| plugin kinds | the plugin's own adapter |

Browsers and plugins therefore share one code path from this step onward, which is what makes step 5 a deletion rather than a rewrite.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `src/octowright/scenario_adapters.py` | `BrowserScenarioAdapter` — the five handlers, holding exactly the code inline in `scenarios_pool.py` today. |
| `src/octowright/scenario_kinds.py` | Dispatch for the scenario layer: resolve a participant's adapter, report its capabilities, and answer "does this kind support X". Exists so `scenarios_pool.py` does not absorb it. |
| `tests/test_scenario_adapters.py` | `BrowserScenarioAdapter` against a fake pool. |
| `tests/test_scenario_kinds.py` | Adapter resolution and capability narrowing. |
| `tests/test_scenario_options.py` | `Participant.options` parsing, validation, and the terminal branch reading from it. |
| `tests/test_scenario_plugin_participation.py` | End-to-end: a reference-plugin participant in a scenario. |

**Modified:**

| Path | Change |
|---|---|
| `src/octowright/scenarios.py` | `Participant.options` replaces ten terminal fields; `_validate_participant_kind` becomes registry-aware; `resolve_terminal_launch` reads `options`. |
| `src/octowright/scenarios_pool.py` | `start` group-by; `run_macro` / `wait_for_sync` / `_apply_fixtures` / `_run_startup_macros` dispatch through adapters; `stop` / `remap` / rollback handle plugin pools. |
| `src/octowright/server/scenarios.py` | Call sites that thread `terminal_pool`. |
| `tests/plugins/reference/plugin.py` | `create_scenario_adapter` returns a partial adapter. |
| `tests/plugins/reference/pool.py` | Whatever the adapter needs to resolve an id. |
| `examples/scenarios/browser-plus-terminal.yaml` | `connector_type:` moves under `options:`. |

---

## Task 1: `BrowserScenarioAdapter`

The four skip sites in `scenarios_pool.py` are not permission checks — the code immediately after each is browser-specific, which is why a capability flag alone generalizes nothing. Moving that code behind an adapter is what lets a plugin's participant take the same path.

The four sites and the capability each becomes:

| Site | Line | Capability |
|---|---|---|
| `run_macro._run` | ~417 | `macros` |
| `_run_startup_macros._run_for_participant` | ~522 | `macros` (same handler) |
| `wait_for_sync._wait` | ~457 | `sync` |
| `_apply_fixtures._apply` | ~496 | `dialog_policy` + `mock_routes` (two, not one) |

**Files:**
- Create: `src/octowright/scenario_adapters.py`
- Test: `tests/test_scenario_adapters.py`

**Interfaces:**
- Consumes: `octowright.plugins.contract.{ScenarioAdapter, SupportsMacros, SupportsSync, SupportsDialogPolicy, SupportsMockRoutes}`; `octowright.macros.run_macro`.
- Produces:
  - `octowright.scenario_adapters.BrowserScenarioAdapter` with `__init__(self, pool: Any)` and methods `resolve_participant(spec, persona) -> dict`, `run_macro(instance_id, *, name, args) -> None`, `wait_for_sync(instance_id, *, selector, text, url, timeout_ms) -> None`, `set_dialog_policy(instance_id, policy) -> None`, `install_mock_routes(instance_id, routes) -> None`
  - `octowright.scenario_adapters.browser_scenario_adapter(pool) -> BrowserScenarioAdapter`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scenario_adapters.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins.contract import (
    ScenarioAdapter,
    SupportsDialogPolicy,
    SupportsMacros,
    SupportsMockRoutes,
    SupportsSync,
    capabilities_of,
)
from octowright.scenario_adapters import BrowserScenarioAdapter


class _Page:
    url = "https://shop.test/orders"

    async def wait_for_url(self, url: str, timeout: int | None = None) -> None:
        self.waited = url


class _Operation:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.page = _Page()
        self.dialog_policy: str | None = None
        self.mocked: list[dict[str, Any]] = []
        self.waited_for: dict[str, Any] | None = None

    def operation(self, name: str) -> _Operation:
        return _Operation()

    async def wait_for(self, *, selector=None, text=None, timeout_ms=None) -> None:
        self.waited_for = {"selector": selector, "text": text, "timeout_ms": timeout_ms}

    async def set_dialog_policy(self, policy: str) -> None:
        self.dialog_policy = policy

    async def mock_route(self, pattern, *, status=200, body=None, content_type=None, headers=None) -> None:
        self.mocked.append({"pattern": pattern, "status": status, "body": body, "content_type": content_type})


class _Pool:
    def __init__(self) -> None:
        self.sessions = {"br0wser01": _Session()}

    def get(self, instance_id: str) -> _Session:
        return self.sessions[instance_id]


@pytest.fixture
def adapter():
    pool = _Pool()
    return BrowserScenarioAdapter(pool), pool


def test_browser_adapter_satisfies_every_capability_protocol(adapter):
    """A browser supports all four, which is what makes it the reference shape."""
    ad, _ = adapter
    assert isinstance(ad, ScenarioAdapter)
    assert isinstance(ad, SupportsMacros)
    assert isinstance(ad, SupportsSync)
    assert isinstance(ad, SupportsDialogPolicy)
    assert isinstance(ad, SupportsMockRoutes)
    assert capabilities_of(ad) == {"macros", "sync", "dialog_policy", "mock_routes"}


async def test_wait_for_sync_by_selector_uses_the_session(adapter):
    ad, pool = adapter
    await ad.wait_for_sync("br0wser01", selector="#done", text=None, url=None, timeout_ms=500)
    assert pool.sessions["br0wser01"].waited_for == {"selector": "#done", "text": None, "timeout_ms": 500}


async def test_wait_for_sync_by_url_skips_the_wait_when_already_there(adapter):
    ad, pool = adapter
    # _Page.url already matches, so page.wait_for_url must not be called.
    await ad.wait_for_sync("br0wser01", selector=None, text=None, url=r"shop\.test/orders", timeout_ms=None)
    assert not hasattr(pool.sessions["br0wser01"].page, "waited")


async def test_wait_for_sync_by_url_waits_when_it_does_not_match(adapter):
    ad, pool = adapter
    await ad.wait_for_sync("br0wser01", selector=None, text=None, url=r"checkout", timeout_ms=1000)
    assert pool.sessions["br0wser01"].page.waited == "checkout"


async def test_set_dialog_policy_reaches_the_session(adapter):
    ad, pool = adapter
    await ad.set_dialog_policy("br0wser01", "accept")
    assert pool.sessions["br0wser01"].dialog_policy == "accept"


async def test_install_mock_routes_applies_every_route_with_its_defaults(adapter):
    ad, pool = adapter
    await ad.install_mock_routes(
        "br0wser01",
        [{"pattern": "**/api/x", "body": "{}"}, {"pattern": "**/api/y", "status": 404}],
    )
    mocked = pool.sessions["br0wser01"].mocked
    assert [m["pattern"] for m in mocked] == ["**/api/x", "**/api/y"]
    assert mocked[0]["status"] == 200, "status defaults to 200"
    assert mocked[0]["content_type"] == "application/json", "content_type defaults to JSON"
    assert mocked[1]["status"] == 404


async def test_run_macro_dispatches_to_the_macro_runner(adapter, monkeypatch):
    ad, pool = adapter
    seen: dict[str, Any] = {}

    async def _fake_run_macro(*, session, name, args):
        seen.update({"session": session, "name": name, "args": args})

    import octowright.macros as macros_mod

    monkeypatch.setattr(macros_mod, "run_macro", _fake_run_macro)
    await ad.run_macro("br0wser01", name="login", args={"user": "tanuki"})
    assert seen["name"] == "login"
    assert seen["args"] == {"user": "tanuki"}
    assert seen["session"] is pool.sessions["br0wser01"]


def test_resolve_participant_returns_browser_launch_kwargs(adapter):
    """The floor method: turn a Participant into what the pool's launcher needs."""
    from octowright.scenarios import Participant

    ad, _ = adapter
    spec = Participant(persona="tanuki-tim", kind="chromium", role="player", url="https://shop.test")
    resolved = ad.resolve_participant(spec, None)
    assert resolved["kind"] == "chromium"
    assert resolved["url"] == "https://shop.test"
    assert resolved["profile"] == "tanuki-tim"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/test_scenario_adapters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.scenario_adapters'`.

- [ ] **Step 3: Write the adapter**

Create `src/octowright/scenario_adapters.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The browser kind's scenario adapter.

Core used to inline this behind ``if p.get("kind") == "terminal": return ...``
checks. The problem with that shape was never the check -- it was the code
*after* it, which reached into a browser session (``pool.get(...)``,
``session.page``, ``session.wait_for``). Swapping the check for a capability
flag would have left that body intact, so a plugin declaring ``macros`` would
still have been looked up in the browser pool.

So each kind supplies an adapter instead, and the adapter resolves the instance
id against its own pool. That is what lets core stop knowing which pool a
participant lives in.

Capabilities are not declared here. ``contract.capabilities_of`` derives them by
checking which Protocols this class satisfies, so implementing ``run_macro`` IS
the claim to ``macros``; there is no second place to keep in sync.
"""

from __future__ import annotations

import re
from typing import Any

#: Matches ``_apply_fixtures``'s historical defaults exactly. Changing one is a
#: behaviour change to every existing scenario, not a tidy-up.
_MOCK_ROUTE_DEFAULT_STATUS = 200
_MOCK_ROUTE_DEFAULT_CONTENT_TYPE = "application/json"

#: ``wait_for_sync``'s url branch used this when the caller passed no timeout.
_URL_WAIT_DEFAULT_TIMEOUT_MS = 30000


class BrowserScenarioAdapter:
    """Scenario participation for a browser session.

    Implements all four capability Protocols, which is what makes it the
    reference shape: a plugin adapter is measured against how much of this it
    chooses to provide.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        """Turn a ``Participant`` into launch kwargs for the browser roster.

        ``persona`` is accepted and unused: the browser roster resolves persona
        state itself from the profile name. The parameter stays because it is
        part of the ``ScenarioAdapter`` floor -- a terminal adapter needs it to
        read ``app.ssh`` defaults -- and a floor method that varies by kind is
        not a floor.
        """
        return {
            "kind": spec.kind,
            "url": spec.url,
            "profile": spec.persona,
            "viewport_w": spec.viewport_w,
            "viewport_h": spec.viewport_h,
            "stabilize": spec.stabilize,
            "record_video": spec.record_video,
            "trace": spec.trace,
        }

    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
        from octowright import macros as _macros

        session = self._pool.get(instance_id)
        await _macros.run_macro(session=session, name=name, args=args)

    async def wait_for_sync(
        self,
        instance_id: str,
        *,
        selector: str | None,
        text: str | None,
        url: str | None,
        timeout_ms: int | None,
    ) -> None:
        session = self._pool.get(instance_id)
        if selector or text:
            await session.wait_for(selector=selector, text=text, timeout_ms=timeout_ms)
        elif url:
            async with session.operation("scenario_wait_for_sync"):
                # Already-there is a pass, not a wait: re.search against the
                # live url first, exactly as the inline version did.
                if not re.search(url, session.page.url):
                    await session.page.wait_for_url(url, timeout=timeout_ms or _URL_WAIT_DEFAULT_TIMEOUT_MS)
        else:
            await session.wait_for(selector=None, text=None, timeout_ms=timeout_ms)

    async def set_dialog_policy(self, instance_id: str, policy: str) -> None:
        session = self._pool.get(instance_id)
        await session.set_dialog_policy(policy)

    async def install_mock_routes(self, instance_id: str, routes: list[dict[str, Any]]) -> None:
        session = self._pool.get(instance_id)
        for mr in routes:
            await session.mock_route(
                mr["pattern"],
                status=mr.get("status", _MOCK_ROUTE_DEFAULT_STATUS),
                body=mr.get("body"),
                content_type=mr.get("content_type", _MOCK_ROUTE_DEFAULT_CONTENT_TYPE),
                headers=mr.get("headers"),
            )


def browser_scenario_adapter(pool: Any) -> BrowserScenarioAdapter:
    """Factory mirroring a plugin's ``create_scenario_adapter(pool)``.

    Core's own kind goes through the same shape as a plugin's so the dispatch
    layer has exactly one way to obtain an adapter.
    """
    return BrowserScenarioAdapter(pool)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/test_scenario_adapters.py -v`
Expected: all pass.

- [ ] **Step 5: Verify nothing regressed**

Run: `uv run --active pytest -k "scenario" -q` and `uv run --active python scripts/check_vulture.py`.
Expected: green. `scenarios_pool.py` is untouched by this task, so the existing scenario suite must be unaffected — report the count.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/scenario_adapters.py tests/test_scenario_adapters.py
git commit -m "feat(scenarios): browser scenario adapter

The four skip sites were never permission checks -- the code after each one
reached into a browser session, so replacing the check with a capability
flag would have left a plugin's participant being looked up in the browser
pool. Each kind supplies an adapter that resolves ids against its own pool
instead. Capabilities stay derived: implementing run_macro IS the claim to
macros, so there is no second place to keep in sync.

Nothing dispatches through this yet; wiring follows."
```

---

## Task 2: `Participant.options` replaces the terminal field block

`Participant` carries **ten** terminal-specific fields (`connector_type`, `command`, `cols`, `rows`, `host`, `port`, `user`, `key_path`, `known_hosts`, `insecure_no_host_check`) — every one of them meaningless to a browser and to any future plugin. They collapse into one free-form `options: dict` that core passes through opaquely and the owning kind validates.

**This is a breaking YAML change**, accepted under the spec's no-migration decision. It lands now rather than in step 5 so the break happens once.

Terminal keeps its branch this step, so `resolve_terminal_launch` reads the same values out of `options` instead of dedicated attributes.

**Files:**
- Modify: `src/octowright/scenarios.py` (`Participant` ~line 42; `_validate_participant_kind` ~line 76; the YAML parser ~line 163; `resolve_terminal_launch` ~line 480)
- Modify: `examples/scenarios/browser-plus-terminal.yaml`
- Test: `tests/test_scenario_options.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `Participant.options: dict[str, Any]` (defaults to `{}`, never `None`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scenario_options.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.scenarios import Participant, resolve_terminal_launch


def test_participant_defaults_options_to_an_empty_dict():
    p = Participant(persona="tanuki-tim", kind="chromium", role="player")
    assert p.options == {}, "never None -- callers index it without a guard"


def test_participant_no_longer_carries_terminal_fields():
    """The ten terminal fields are gone; a plugin's settings live in options."""
    p = Participant(persona="tanuki-tim", kind="terminal", role="monitor")
    for gone in (
        "connector_type",
        "command",
        "cols",
        "rows",
        "host",
        "port",
        "user",
        "key_path",
        "known_hosts",
        "insecure_no_host_check",
    ):
        assert not hasattr(p, gone), f"{gone} must move under options"


def test_yaml_parses_options_through_opaquely():
    from octowright.scenarios import load_yaml_scenario

    spec = load_yaml_scenario(
        "name: demo\n"
        "participants:\n"
        "  - persona: tanuki-tim\n"
        "    kind: terminal\n"
        "    role: monitor\n"
        "    options:\n"
        "      connector_type: pty\n"
        "      command: /bin/zsh\n"
        "      cols: 100\n",
        "demo",
    )
    assert spec.participants[0].options == {"connector_type": "pty", "command": "/bin/zsh", "cols": 100}


def test_yaml_rejects_a_non_mapping_options():
    from octowright.scenarios import load_yaml_scenario

    with pytest.raises(ValueError, match="options must be a mapping"):
        load_yaml_scenario(
            "name: demo\nparticipants:\n  - persona: t\n    kind: chromium\n    role: player\n    options: nope\n",
            "demo",
        )


def test_terminal_launch_reads_connector_type_from_options():
    p = Participant(
        persona="tanuki-tim",
        kind="terminal",
        role="monitor",
        options={"command": "/bin/zsh", "cols": 100, "rows": 40},
    )
    launch = resolve_terminal_launch(p)
    assert launch["kind"] == "pty", "omitted connector_type still defaults to pty"
    assert launch["profile"] == "tanuki-tim"
    assert launch["connector_config"]["command"] == "/bin/zsh"


def test_terminal_launch_honours_an_explicit_connector_type():
    p = Participant(
        persona="tanuki-tim",
        kind="terminal",
        role="monitor",
        options={"connector_type": "ssh", "host": "box.test", "user": "tim", "known_hosts": "/tmp/kh"},
    )
    launch = resolve_terminal_launch(p)
    assert launch["kind"] == "ssh"
    assert launch["connector_config"]["host"] == "box.test"
    assert launch["connector_config"]["username"] == "tim"


def test_an_unsupported_connector_type_is_still_refused():
    from octowright.scenarios import Scenario, _validate_participant_kind

    p = Participant(persona="t", kind="terminal", role="monitor", options={"connector_type": "carrier-pigeon"})
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError, match="unsupported connector_type"):
        _validate_participant_kind(s, p)


def test_a_terminal_participant_still_cannot_declare_startup_macros():
    from octowright.scenarios import Scenario, _validate_participant_kind

    p = Participant(persona="t", kind="terminal", role="monitor", startup_macros=["login"])
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError, match="startup_macros"):
        _validate_participant_kind(s, p)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/test_scenario_options.py -v`
Expected: FAIL — `Participant` has no `options` field, and the terminal-field assertions fail because the attributes still exist.

- [ ] **Step 3: Replace the field block**

In `src/octowright/scenarios.py`, replace the ten terminal fields on `Participant` with one:

```python
    # Kind-specific settings, passed through opaquely and validated by whichever
    # kind owns the participant. This replaced ten terminal-only fields that a
    # browser participant could never use and that no plugin could extend
    # without a core change -- the dataclass is public plugin API, so every
    # field on it is a compatibility commitment.
    options: dict[str, Any] = field(default_factory=dict)
```

Delete `connector_type`, `command`, `cols`, `rows`, `host`, `port`, `user`, `key_path`, `known_hosts`, `insecure_no_host_check`.

In the YAML parser (~line 163), replace the `connector_type` default line with:

```python
    raw_options = raw.get("options")
    if raw_options is None:
        options: dict[str, Any] = {}
    elif isinstance(raw_options, dict):
        options = dict(raw_options)
    else:
        raise ValueError(f"scenario participant {raw.get('persona')!r}: options must be a mapping")
```

and pass `options=options` to the `Participant(...)` construction, dropping `connector_type=connector_type`.

In `_validate_participant_kind`, read the connector type from options:

```python
    if p.kind == "terminal":
        connector_type = p.options.get("connector_type") or "pty"
        if connector_type not in SUPPORTED_TERMINAL_KINDS:
            raise ValueError(
                f"scenario {s.name!r}: terminal participant has unsupported connector_type {connector_type!r} "
                f"(expected one of {list(SUPPORTED_TERMINAL_KINDS)})"
            )
```

leaving the `startup_macros` check below it unchanged.

In `resolve_terminal_launch`, read every value from `options`:

```python
    opts = p.options
    connector_type = opts.get("connector_type") or "pty"
    if connector_type == "ssh":
        persona = _load_persona_or_none(p.persona)
        ssh = (getattr(persona, "app", {}) or {}).get("ssh", {}) or {}

        def _pick(key: str) -> Any:
            value = opts.get(key)
            return value if value is not None else ssh.get(key)

        port = opts.get("port") if opts.get("port") is not None else int(ssh.get("port", SSH_DEFAULT_PORT))
        insecure_opt = opts.get("insecure_no_host_check")
        insecure = bool(insecure_opt) if insecure_opt is not None else bool(ssh.get("insecure_no_host_check", False))
        cfg = ssh_connector_config(
            host=_pick("host"),
            port=port,
            user=_pick("user"),
            key_path=_pick("key_path"),
            password=None,
            known_hosts=_pick("known_hosts"),
            insecure_no_host_check=insecure,
        )
    else:
        cfg = pty_connector_config(command=opts.get("command"), cols=opts.get("cols"), rows=opts.get("rows"))
    return {"kind": connector_type, "connector_config": cfg, "label": None, "profile": p.persona, "protected": False}
```

The participant-override → persona-`app.ssh` → omit precedence is unchanged, and **no password is read from a scenario** — that rule is unchanged and load-bearing, because scenarios are persisted to disk.

- [ ] **Step 4: Update the example scenario**

In `examples/scenarios/browser-plus-terminal.yaml`, move every terminal setting under `options:`. Read the file first and preserve its comments; only the terminal participant's keys move.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --active pytest tests/test_scenario_options.py -v`
Expected: all pass.

- [ ] **Step 6: Fix the fallout**

Run: `uv run --active pytest -k "scenario" -q`.
Other suites construct `Participant(connector_type=...)` and will fail. Update each call site to `options={...}`. **Do not** re-add a compatibility shim: the no-migration decision is explicit, and a shim would leave two ways to say the same thing. Report which files you touched and how many call sites moved.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/scenarios.py examples/scenarios/browser-plus-terminal.yaml \
        tests/test_scenario_options.py
git commit -m "feat(scenarios): options dict replaces the terminal field block

Participant carried ten terminal-only fields, every one meaningless to a
browser and unextendable by a plugin without a core change -- and the
dataclass is public plugin API, so each field was a compatibility
commitment. They collapse into one options mapping core passes through
opaquely and the owning kind validates.

Breaking YAML change under the no-migration decision: connector_type and
the SSH/PTY settings move under options. It lands now rather than with the
terminal extraction so the break happens once. No password is read from a
scenario -- unchanged, and load-bearing since scenarios are persisted."
```

---

## Task 3: Scenario dispatch module

`scenarios_pool.py` is 550 lines and every later task adds to it. The adapter lookup, the capability question, and the "which pool owns this participant" question are one responsibility with their own tests, so they go in their own module — the same split `http/routes/_session_kinds.py` made for the HTTP layer.

**Files:**
- Create: `src/octowright/scenario_kinds.py`
- Test: `tests/test_scenario_kinds.py`

**Interfaces:**
- Consumes: `octowright.scenario_adapters.browser_scenario_adapter`; `octowright.server.plugin_state.registry`; `octowright.plugins.contract.capabilities_of`.
- Produces:
  - `octowright.scenario_kinds.TERMINAL_KIND: str` — the one literal, so later tasks stop spelling it
  - `octowright.scenario_kinds.adapter_for(kind: str, *, browser_pool: Any) -> Any | None`
  - `octowright.scenario_kinds.capabilities_for(kind: str, *, browser_pool: Any) -> frozenset[str]`
  - `octowright.scenario_kinds.supports(kind: str, capability: str, *, browser_pool: Any) -> bool`
  - `octowright.scenario_kinds.pool_for_kind(kind: str, *, browser_pool: Any, terminal_pool: Any | None) -> Any`
  - `octowright.scenario_kinds.known_kinds(*, include_plugins: bool = True) -> list[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scenario_kinds.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins.registry import PluginRegistry
from octowright.scenario_kinds import (
    TERMINAL_KIND,
    adapter_for,
    capabilities_for,
    known_kinds,
    pool_for_kind,
    supports,
)
from octowright.server import plugin_state


class _RefAdapter:
    """A partial adapter: the floor and nothing else."""

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        return {"label": spec.persona}


class _MacroAdapter(_RefAdapter):
    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
        return None


class _Descriptor:
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None
    frontend = None

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> Any:
        raise AssertionError("not used")

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


@pytest.fixture
def registered():
    original = plugin_state.registry()
    reg = PluginRegistry()
    reg.register(_Descriptor("refkind"), pool="REFPOOL", adapter=_RefAdapter(), discovered=None)
    reg.register(_Descriptor("macrokind"), pool="MACROPOOL", adapter=_MacroAdapter(), discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg
    finally:
        plugin_state.set_registry(original)


def test_browser_kinds_resolve_to_the_browser_adapter():
    from octowright.scenario_adapters import BrowserScenarioAdapter

    ad = adapter_for("chromium", browser_pool="BROWSERPOOL")
    assert isinstance(ad, BrowserScenarioAdapter)


def test_terminal_has_no_adapter_this_step():
    """Terminal keeps its hardcoded branch until step 5, so it resolves to None."""
    assert adapter_for(TERMINAL_KIND, browser_pool="BROWSERPOOL") is None


def test_a_plugin_kind_resolves_to_its_registered_adapter(registered):
    ad = adapter_for("refkind", browser_pool="BROWSERPOOL")
    assert isinstance(ad, _RefAdapter)


def test_an_unknown_kind_resolves_to_none(registered):
    assert adapter_for("nosuchkind", browser_pool="BROWSERPOOL") is None


def test_browser_capabilities_are_all_four():
    assert capabilities_for("chromium", browser_pool="BROWSERPOOL") == {
        "macros",
        "sync",
        "dialog_policy",
        "mock_routes",
    }


def test_a_partial_adapter_reports_no_capabilities(registered):
    assert capabilities_for("refkind", browser_pool="BROWSERPOOL") == frozenset()
    assert supports("refkind", "macros", browser_pool="BROWSERPOOL") is False


def test_capabilities_are_derived_from_what_the_adapter_implements(registered):
    assert capabilities_for("macrokind", browser_pool="BROWSERPOOL") == {"macros"}
    assert supports("macrokind", "macros", browser_pool="BROWSERPOOL") is True
    assert supports("macrokind", "sync", browser_pool="BROWSERPOOL") is False


def test_terminal_supports_nothing_without_being_special_cased():
    assert capabilities_for(TERMINAL_KIND, browser_pool="BROWSERPOOL") == frozenset()


def test_pool_for_kind_routes_by_kind(registered):
    assert pool_for_kind("chromium", browser_pool="BROWSERPOOL", terminal_pool="TERMPOOL") == "BROWSERPOOL"
    assert pool_for_kind(TERMINAL_KIND, browser_pool="BROWSERPOOL", terminal_pool="TERMPOOL") == "TERMPOOL"
    assert pool_for_kind("refkind", browser_pool="BROWSERPOOL", terminal_pool="TERMPOOL") == "REFPOOL"


def test_pool_for_an_unknown_kind_raises(registered):
    with pytest.raises(KeyError, match="nosuchkind"):
        pool_for_kind("nosuchkind", browser_pool="B", terminal_pool=None)


def test_known_kinds_lists_browsers_terminal_and_plugins(registered):
    kinds = known_kinds()
    assert "chromium" in kinds
    assert TERMINAL_KIND in kinds
    assert "refkind" in kinds
    assert kinds == sorted(kinds), "sorted so an error message is stable"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/test_scenario_kinds.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.scenario_kinds'`.

- [ ] **Step 3: Write the dispatch module**

Create `src/octowright/scenario_kinds.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Which adapter, which pool, and what a kind can do.

Lives beside ``scenarios_pool`` rather than inside it because that module is
already 550 lines and every scenario task adds to it, and because "resolve a
participant's kind" is one responsibility with its own tests -- the same split
``http/routes/_session_kinds.py`` made for the HTTP layer.

Terminal is deliberately absent from the adapter path. It keeps its hardcoded
branch until step 5 extracts it, so ``adapter_for`` returns ``None`` for it and
callers fall through to that branch. It is NOT special-cased in
``capabilities_for``: a kind with no adapter supports nothing, which is already
the right answer for terminal and stays right when it becomes a plugin.
"""

from __future__ import annotations

from typing import Any

from octowright.defaults import SUPPORTED_KINDS
from octowright.plugins.contract import capabilities_of
from octowright.scenario_adapters import browser_scenario_adapter

#: The one place this literal is spelled. Callers ask for it by name so the
#: step-5 deletion is a search for one symbol rather than for a string.
TERMINAL_KIND = "terminal"


def _plugin_registry() -> Any:
    from octowright.server import plugin_state

    return plugin_state.registry()


def adapter_for(kind: str, *, browser_pool: Any) -> Any | None:
    """Return the scenario adapter for ``kind``, or ``None`` if it has none.

    ``None`` means "not adapter-driven", which today is terminal (hardcoded
    branch) and any unregistered kind. Callers must handle it rather than
    assume every participant has an adapter.
    """
    if kind in SUPPORTED_KINDS:
        return browser_scenario_adapter(browser_pool)
    if kind == TERMINAL_KIND:
        return None
    registry = _plugin_registry()
    if kind in registry.kinds():
        return registry.get_plugin(kind).adapter
    return None


def capabilities_for(kind: str, *, browser_pool: Any) -> frozenset[str]:
    """What ``kind`` can do in a scenario, derived from its adapter.

    A kind with no adapter supports nothing. That is deliberately not a
    terminal special case -- it is the general rule, and terminal happens to be
    its most visible instance right now.
    """
    adapter = adapter_for(kind, browser_pool=browser_pool)
    return frozenset() if adapter is None else capabilities_of(adapter)


def supports(kind: str, capability: str, *, browser_pool: Any) -> bool:
    return capability in capabilities_for(kind, browser_pool=browser_pool)


def pool_for_kind(kind: str, *, browser_pool: Any, terminal_pool: Any | None) -> Any:
    """Resolve which pool owns sessions of ``kind``."""
    if kind in SUPPORTED_KINDS:
        return browser_pool
    if kind == TERMINAL_KIND:
        return terminal_pool
    registry = _plugin_registry()
    pools = registry.pools()
    if kind not in pools:
        raise KeyError(f"no pool for scenario participant kind {kind!r}")
    return pools[kind]


def known_kinds(*, include_plugins: bool = True) -> list[str]:
    """Every kind a scenario participant may name, for error messages.

    Sorted so a validation failure reads the same on every machine -- entry
    point enumeration order is installation-dependent.
    """
    kinds = set(SUPPORTED_KINDS) | {TERMINAL_KIND}
    if include_plugins:
        kinds |= set(_plugin_registry().kinds())
    return sorted(kinds)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/test_scenario_kinds.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/scenario_kinds.py tests/test_scenario_kinds.py
git commit -m "feat(scenarios): kind dispatch for the scenario layer

Adapter lookup, capability derivation and pool routing are one
responsibility with their own tests, and scenarios_pool.py is already 550
lines with every remaining task adding to it -- the same split
http/routes/_session_kinds.py made for the HTTP layer.

Terminal resolves to no adapter because it keeps its hardcoded branch until
step 5. That is not a special case in capabilities_for: a kind with no
adapter supports nothing, which is the general rule and stays correct when
terminal becomes a plugin."
```

---

## Task 4: Registry-resolved participant kind validation

`_validate_participant_kind` branches `if p.kind == "terminal": … elif p.kind not in SUPPORTED_KINDS: raise`. A plugin kind hits the `elif` and is rejected. It becomes: a browser engine, `terminal`, or a registered kind — and the error lists what *is* available, so a typo or a disabled plugin is self-diagnosing.

The `startup_macros` rule generalizes with it: instead of "a terminal participant cannot declare `startup_macros`", it becomes "a kind whose adapter has no `run_macro` cannot", which covers every future plugin for free. Terminal keeps producing the same error because it has no adapter and therefore no `macros`.

**Files:**
- Modify: `src/octowright/scenarios.py` (`_validate_participant_kind` ~line 76)
- Test: `tests/test_scenario_kinds.py` (extend)

**Interfaces:**
- Consumes: `scenario_kinds.{known_kinds, supports, TERMINAL_KIND}` (Task 3).
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scenario_kinds.py`:

```python
def test_a_registered_plugin_kind_validates(registered):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    p = Participant(persona="tanuki-tim", kind="refkind", role="player")
    s = Scenario(name="demo", participants=[p])
    _validate_participant_kind(s, p)  # must not raise


def test_an_unregistered_kind_is_refused_and_the_error_lists_what_is_available(registered):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    p = Participant(persona="tanuki-tim", kind="notaplugin", role="player")
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError) as excinfo:
        _validate_participant_kind(s, p)
    message = str(excinfo.value)
    assert "notaplugin" in message
    assert "refkind" in message, "a disabled or mistyped plugin must be self-diagnosing"
    assert "chromium" in message


def test_a_kind_without_macros_cannot_declare_startup_macros(registered):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    p = Participant(persona="tanuki-tim", kind="refkind", role="player", startup_macros=["login"])
    s = Scenario(name="demo", participants=[p])
    with pytest.raises(ValueError, match="startup_macros"):
        _validate_participant_kind(s, p)


def test_a_kind_with_macros_may_declare_startup_macros(registered):
    from octowright.scenarios import Participant, Scenario, _validate_participant_kind

    p = Participant(persona="tanuki-tim", kind="macrokind", role="player", startup_macros=["login"])
    s = Scenario(name="demo", participants=[p])
    _validate_participant_kind(s, p)  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/test_scenario_kinds.py -v`
Expected: FAIL — a registered plugin kind is rejected as unsupported.

- [ ] **Step 3: Make validation registry-aware**

Replace `_validate_participant_kind` in `src/octowright/scenarios.py`:

```python
def _validate_participant_kind(s: Scenario, p: Participant) -> None:
    """Validate a participant's kind against every kind that can actually run.

    Three families are legal: a browser engine, ``terminal`` (still core's own
    until the extraction step), and any kind a registered plugin claims. The
    error names what IS available, because the two ways to get here -- a typo
    and a plugin that is installed but not enabled -- are indistinguishable to
    the operator otherwise.

    ``startup_macros`` is gated on the ``macros`` capability rather than on
    ``kind != "terminal"``. Terminal still produces the same refusal (it has no
    adapter, so no capabilities), and every future kind is covered without
    another special case.
    """
    from octowright.scenario_kinds import TERMINAL_KIND, known_kinds, supports

    if p.kind == TERMINAL_KIND:
        connector_type = p.options.get("connector_type") or "pty"
        if connector_type not in SUPPORTED_TERMINAL_KINDS:
            raise ValueError(
                f"scenario {s.name!r}: terminal participant has unsupported connector_type {connector_type!r} "
                f"(expected one of {list(SUPPORTED_TERMINAL_KINDS)})"
            )
    elif p.kind not in known_kinds():
        raise ValueError(
            f"scenario {s.name!r}: participant has unsupported kind {p.kind!r} "
            f"(known kinds: {known_kinds()}) -- a plugin kind must be enabled via OCTOWRIGHT_PLUGINS"
        )

    if p.startup_macros and not supports(p.kind, "macros", browser_pool=None):
        raise ValueError(
            f"scenario {s.name!r}: participant {p.persona!r} of kind {p.kind!r} cannot declare startup_macros "
            "(its adapter provides no run_macro)"
        )
```

`browser_pool=None` is safe here and deliberate: `capabilities_for` only needs the adapter's *type*, and `BrowserScenarioAdapter(None)` is never driven — validation runs before any pool exists. A later task must not "fix" this by threading a pool into validation.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/test_scenario_kinds.py -v`
Expected: all pass.

- [ ] **Step 5: Verify the existing validation suite**

Run: `uv run --active pytest tests/test_scenarios_validation_regressions.py tests/test_scenarios_terminal.py -q`.
Expected: green. The terminal error text changed slightly (it now names the resolved connector type rather than the raw field) — if a test asserts the old string, update the assertion, and say so in your report.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/scenarios.py tests/test_scenario_kinds.py
git commit -m "feat(scenarios): validate participant kinds against the registry

A plugin kind previously fell through to 'unsupported kind'. Validation now
accepts a browser engine, terminal, or any registered kind, and the error
lists what is available -- a typo and an installed-but-not-enabled plugin
are otherwise indistinguishable to the operator.

startup_macros is gated on the macros capability instead of on
kind != terminal. Terminal still refuses (no adapter, so no capabilities)
and every future kind is covered without another special case."
```

---

## Task 5: `start()` — the binary partition becomes a group-by

`start()` splits participants into `terminal_specs` / `browser_specs`. It becomes: group by kind, resolve each kind's pool, launch each group, reassemble in declaration order. Browsers keep the roster (it batches window creation), terminals keep their individual launches, and a plugin kind launches through its own pool.

**Files:**
- Modify: `src/octowright/scenarios_pool.py` (`start` ~line 165, `_launch_participants` ~line 225, `_close_launched` ~line 291, `_rollback_start` ~line 305, `_pool_for` ~line 327)
- Test: `tests/test_scenario_plugin_participation.py`

**Interfaces:**
- Consumes: `scenario_kinds.{pool_for_kind, adapter_for, TERMINAL_KIND}` (Task 3); `Participant.options` (Task 2).
- Produces: `ScenarioPool._launch_plugin_participants(pool, specs, launched_by_index, errors) -> list[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scenario_plugin_participation.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins.registry import PluginRegistry
from octowright.scenarios import Participant, Scenario
from octowright.scenarios_pool import ScenarioPool
from octowright.server import plugin_state


class _RefSession:
    def __init__(self, instance_id: str, persona: str | None) -> None:
        self.instance_id = instance_id
        self.kind = "refkind"
        self.profile = persona


class _RefPool:
    """Minimal pool: launch records what it was asked for and hands back an id."""

    def __init__(self) -> None:
        self.launched: list[dict[str, Any]] = []
        self.closed: list[str] = []
        self._n = 0

    async def launch(self, **kwargs: Any) -> dict[str, Any]:
        self._n += 1
        instance_id = f"ref{self._n:09d}"
        self.launched.append(kwargs)
        return {"instance_id": instance_id, "kind": "refkind", "label": kwargs.get("label")}

    async def close(self, instance_id: str, *, force: bool = False) -> dict[str, Any]:
        self.closed.append(instance_id)
        return {"instance_id": instance_id, "closed": True}


class _RefAdapter:
    def __init__(self, pool: _RefPool) -> None:
        self._pool = pool

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        return {"label": spec.persona, "profile": spec.persona}


class _Descriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None
    frontend = None

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> Any:
        return _RefAdapter(pool)

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _BrowserPool:
    async def spawn_roster(self, roster: list[dict[str, Any]], **_: Any) -> list[dict[str, Any]]:
        return [{"instance_id": f"br{i:010d}", "kind": r.get("kind", "chromium")} for i, r in enumerate(roster)]

    async def close(self, instance_id: str, *, force: bool = False) -> None:
        return None


@pytest.fixture
def registered():
    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _RefPool()
    reg.register(_Descriptor(), pool=pool, adapter=_RefAdapter(pool), discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg, pool
    finally:
        plugin_state.set_registry(original)


async def test_a_plugin_participant_launches_through_its_own_pool(registered):
    _, ref_pool = registered
    spec = Scenario(
        name="mixed",
        participants=[
            Participant(persona="tanuki-tim", kind="chromium", role="player"),
            Participant(persona="ref-rita", kind="refkind", role="monitor"),
        ],
    )
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)

    assert len(live.participants) == 2
    assert live.participants[0]["kind"] == "chromium"
    assert live.participants[1]["kind"] == "refkind"
    assert ref_pool.launched == [{"label": "ref-rita", "profile": "ref-rita"}]


async def test_participants_reassemble_in_declaration_order(registered):
    """Grouping by kind must not reorder the roster -- roles line up by index."""
    spec = Scenario(
        name="interleaved",
        participants=[
            Participant(persona="a", kind="refkind", role="monitor"),
            Participant(persona="b", kind="chromium", role="player"),
            Participant(persona="c", kind="refkind", role="spectator"),
        ],
    )
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    assert [p["persona"] for p in live.participants] == ["a", "b", "c"]
    assert [p["role"] for p in live.participants] == ["monitor", "player", "spectator"]
    assert [p["kind"] for p in live.participants] == ["refkind", "chromium", "refkind"]


async def test_a_scenario_naming_an_unregistered_kind_fails_before_launching_anything(registered):
    _, ref_pool = registered
    spec = Scenario(name="bad", participants=[Participant(persona="x", kind="nosuchkind", role="player")])
    sp = ScenarioPool()
    with pytest.raises((RuntimeError, KeyError, ValueError)):
        await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    assert ref_pool.launched == [], "nothing may launch for an unresolvable roster"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/test_scenario_plugin_participation.py -v`
Expected: FAIL — `start()` partitions on `kind == "terminal"`, so `refkind` is treated as a browser and goes to the roster.

- [ ] **Step 3: Group by kind**

In `start()`, replace the two-way partition with a three-way grouping. Keep the terminal-pool-missing guard exactly as it is:

```python
# Group by kind rather than splitting browser/terminal: browsers keep
# the roster (it batches window creation), terminals keep their
# individual launches, and a plugin kind launches through its own pool.
from octowright.scenario_kinds import TERMINAL_KIND

terminal_specs = [(i, p) for i, p in enumerate(spec.participants) if p.kind == TERMINAL_KIND]
if terminal_specs and terminal_pool is None:
    raise RuntimeError(
        f"scenario {effective_name!r} has terminal participant(s) but the octowright[terminal] "
        "extra is not installed (terminal_pool is unavailable)"
    )
browser_specs = [(i, p) for i, p in enumerate(spec.participants) if p.kind in SUPPORTED_KINDS]
plugin_specs = [
    (i, p) for i, p in enumerate(spec.participants) if p.kind != TERMINAL_KIND and p.kind not in SUPPORTED_KINDS
]
```

Import `SUPPORTED_KINDS` from `octowright.defaults` at the top of the module if it is not already imported.

Thread `plugin_specs` into `_launch_participants` and collect a third id list. Add the plugin launcher beside `_launch_terminals`:

```python
    async def _launch_plugin_participants(
        self,
        plugin_specs: list[tuple[int, Any]],
        launched_by_index: dict[int, dict[str, Any]],
        errors: list[str],
    ) -> list[str]:
        """Launch each plugin participant through the pool its kind registered.

        Sequential rather than gathered, matching ``_launch_terminals``: a
        plugin pool makes no concurrency promise, and a scenario roster is
        small enough that the wall-clock cost is not worth the first
        cross-plugin race report.
        """
        from octowright.scenario_kinds import adapter_for, pool_for_kind

        launched_ids: list[str] = []
        for index, p in plugin_specs:
            pool = pool_for_kind(p.kind, browser_pool=None, terminal_pool=None)
            adapter = adapter_for(p.kind, browser_pool=None)
            if adapter is None:
                errors.append(f"participant kind {p.kind!r} has no scenario adapter")
                continue
            try:
                launch_kwargs = adapter.resolve_participant(p, _load_persona_or_none(p.persona))
                launched = await pool.launch(**launch_kwargs)
            except Exception as e:
                errors.append(f"participant {p.persona!r} ({p.kind}) failed to launch: {e!r}")
                continue
            entry = dict(launched)
            entry.setdefault("kind", p.kind)
            launched_by_index[index] = entry
            launched_ids.append(entry["instance_id"])
        return launched_ids
```

Import `_load_persona_or_none` from `octowright.scenarios` inside the method (module-level would be a cycle).

Extend `_close_launched` and `_rollback_start` to take the plugin ids and close them through `pool_for_kind`, and extend `_pool_for` so an already-launched participant dict routes by its recorded `kind`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/test_scenario_plugin_participation.py -v`
Expected: all pass.

- [ ] **Step 5: Verify the scenario suite**

Run: `uv run --active pytest -k "scenario" -q` and `uv run --active pytest tests/test_scenarios_terminal.py -q`.
Expected: green. Report counts and `scenarios_pool.py`'s line count.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/scenarios_pool.py tests/test_scenario_plugin_participation.py
git commit -m "feat(scenarios): group participants by kind instead of the browser/terminal split

start() partitioned into terminal_specs and browser_specs, so any kind that
was not terminal went to the browser roster -- which is why a plugin
participant could not launch. It now groups by kind and resolves each
group's pool: browsers keep the roster because it batches window creation,
terminals keep their individual launches, plugins go through their own pool.
Reassembly stays keyed on declaration index so roles and personas line up."
```

---

## Task 6: `run_macro` and startup macros through the adapter

Two sites share one capability. Both currently reach `browser_pool.get(...)` directly after the terminal check; both become "ask the kind's adapter, or report that it has no `macros`".

**Files:**
- Modify: `src/octowright/scenarios_pool.py` (`run_macro` ~line 389, `_run_startup_macros` ~line 514)
- Test: `tests/test_scenario_plugin_participation.py` (extend)

**Interfaces:**
- Consumes: `scenario_kinds.{adapter_for, supports}`; `SupportsMacros`.
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scenario_plugin_participation.py`:

```python
class _MacroRefAdapter(_RefAdapter):
    def __init__(self, pool: _RefPool) -> None:
        super().__init__(pool)
        self.ran: list[tuple[str, str, dict[str, Any]]] = []

    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None:
        self.ran.append((instance_id, name, args))


@pytest.fixture
def registered_with_macros():
    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _RefPool()
    adapter = _MacroRefAdapter(pool)
    reg.register(_Descriptor(), pool=pool, adapter=adapter, discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg, pool, adapter
    finally:
        plugin_state.set_registry(original)


async def test_run_macro_reports_a_kind_without_the_capability(registered):
    spec = Scenario(name="s", participants=[Participant(persona="a", kind="refkind", role="monitor")])
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    result = await sp.run_macro(scenario_id=live.scenario_id, macro="login", browser_pool=_BrowserPool())
    outcome = result["results"][0]
    assert outcome["ok"] is False
    assert "macros" in outcome["error"], "the error must name the missing capability, not the kind"


async def test_run_macro_dispatches_to_an_adapter_that_has_the_capability(registered_with_macros):
    _, _, adapter = registered_with_macros
    spec = Scenario(name="s", participants=[Participant(persona="a", kind="refkind", role="monitor")])
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    result = await sp.run_macro(
        scenario_id=live.scenario_id, macro="login", browser_pool=_BrowserPool(), args={"u": "x"}
    )
    assert result["results"][0]["ok"] is True
    assert adapter.ran == [(live.participants[0]["instance_id"], "login", {"u": "x"})]


async def test_startup_macros_run_through_the_adapter(registered_with_macros):
    _, _, adapter = registered_with_macros
    spec = Scenario(
        name="s",
        participants=[Participant(persona="a", kind="refkind", role="monitor", startup_macros=["boot"])],
    )
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    assert [name for _id, name, _args in adapter.ran] == ["boot"]
    assert live.participants[0]["kind"] == "refkind"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/test_scenario_plugin_participation.py -v`
Expected: FAIL — `run_macro` calls `browser_pool.get(...)` on a `refkind` instance id and raises.

- [ ] **Step 3: Dispatch both sites through the adapter**

Replace `run_macro`'s inner `_run`:

```python
            async def _run(p: dict[str, Any]) -> ScenarioParticipantOutcome:
                from octowright.scenario_kinds import adapter_for

                kind = p.get("kind") or ""
                adapter = adapter_for(kind, browser_pool=browser_pool)
                if not isinstance(adapter, SupportsMacros):
                    return {
                        "instance_id": p["instance_id"],
                        "ok": False,
                        "error": f"kind {kind!r} does not support macros (its adapter provides no run_macro)",
                    }
                try:
                    await adapter.run_macro(p["instance_id"], name=macro, args=args or {})
                    return {"instance_id": p["instance_id"], "ok": True}
                except Exception as e:
                    return {"instance_id": p["instance_id"], "ok": False, "error": repr(e)}
```

Import `SupportsMacros` from `octowright.plugins.contract` at module level.

Replace `_run_startup_macros`'s `_run_for_participant` body the same way — skip when the adapter is not `SupportsMacros` (terminal still skips, now for the general reason), otherwise `await adapter.run_macro(...)`, keeping the existing failure-collection and `log.warning` shape untouched. `_run_startup_macros` needs the browser pool to build a browser adapter, which it already receives as its first parameter.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/test_scenario_plugin_participation.py -v`
Expected: all pass.

- [ ] **Step 5: Verify browsers and terminals are unaffected**

Run: `uv run --active pytest -k "scenario" -q` and `uv run --active pytest tests/test_scenarios_terminal.py -q`.
Expected: green. A terminal participant's `run_macro` error text changed from "terminal sessions do not support browser macros" to the capability-shaped message — update any test asserting the old string and say so in your report.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/scenarios_pool.py tests/test_scenario_plugin_participation.py
git commit -m "feat(scenarios): run macros through the kind's adapter

Both macro sites reached browser_pool.get() straight after the terminal
check, which is why a capability flag alone would not have generalized
them: the body was browser-specific, not the check. They now ask the kind's
adapter, and a kind whose adapter has no run_macro is reported by the
missing capability rather than by being named terminal."
```

---

## Task 7: `wait_for_sync` and fixtures through the adapter

`wait_for_sync` is the `sync` capability. `_apply_fixtures` is **two** capabilities, not one: `_validate_fixtures` accepts exactly `dialog_policy` and `mock_routes`, and `_apply_fixtures` does nothing but dispatch to those two — so a single `fixtures` capability would either need an undefined precedence against its own constituents or apply the same fixture twice. Core keeps `_apply_fixtures` as the dispatcher and calls two capability handlers. The fixture vocabulary in scenario YAML is unchanged.

**Files:**
- Modify: `src/octowright/scenarios_pool.py` (`wait_for_sync` ~line 440, `_apply_fixtures` ~line 490)
- Test: `tests/test_scenario_plugin_participation.py` (extend)

**Interfaces:**
- Consumes: `SupportsSync`, `SupportsDialogPolicy`, `SupportsMockRoutes`; `scenario_kinds.adapter_for`.
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scenario_plugin_participation.py`:

```python
class _SyncingAdapter(_RefAdapter):
    def __init__(self, pool: _RefPool) -> None:
        super().__init__(pool)
        self.synced: list[dict[str, Any]] = []
        self.policies: list[str] = []
        self.routes: list[list[dict[str, Any]]] = []

    async def wait_for_sync(self, instance_id, *, selector, text, url, timeout_ms) -> None:
        self.synced.append({"instance_id": instance_id, "selector": selector, "url": url})

    async def set_dialog_policy(self, instance_id: str, policy: str) -> None:
        self.policies.append(policy)

    async def install_mock_routes(self, instance_id: str, routes: list[dict[str, Any]]) -> None:
        self.routes.append(routes)


@pytest.fixture
def registered_full():
    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _RefPool()
    adapter = _SyncingAdapter(pool)
    reg.register(_Descriptor(), pool=pool, adapter=adapter, discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg, pool, adapter
    finally:
        plugin_state.set_registry(original)


async def test_wait_for_sync_reports_a_kind_without_the_capability(registered):
    spec = Scenario(name="s", participants=[Participant(persona="a", kind="refkind", role="monitor")])
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    result = await sp.wait_for_sync(scenario_id=live.scenario_id, browser_pool=_BrowserPool(), selector="#x")
    outcome = result["results"][0]
    assert outcome["ok"] is False
    assert "sync" in outcome["error"]


async def test_wait_for_sync_dispatches_to_a_capable_adapter(registered_full):
    _, _, adapter = registered_full
    spec = Scenario(name="s", participants=[Participant(persona="a", kind="refkind", role="monitor")])
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    await sp.wait_for_sync(scenario_id=live.scenario_id, browser_pool=_BrowserPool(), selector="#ready")
    assert adapter.synced[0]["selector"] == "#ready"


async def test_fixtures_dispatch_to_both_capability_handlers(registered_full):
    _, _, adapter = registered_full
    spec = Scenario(
        name="s",
        participants=[Participant(persona="a", kind="refkind", role="monitor")],
        fixtures={"dialog_policy": "accept", "mock_routes": [{"pattern": "**/x", "body": "{}"}]},
    )
    sp = ScenarioPool()
    await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    assert adapter.policies == ["accept"]
    assert adapter.routes == [[{"pattern": "**/x", "body": "{}"}]]


async def test_fixtures_skip_a_kind_that_cannot_apply_them(registered):
    """A partial adapter is skipped silently -- fixtures are best-effort setup."""
    spec = Scenario(
        name="s",
        participants=[Participant(persona="a", kind="refkind", role="monitor")],
        fixtures={"dialog_policy": "accept"},
    )
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    assert len(live.participants) == 1, "an inapplicable fixture must not fail the scenario"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/test_scenario_plugin_participation.py -v`
Expected: FAIL — both sites call `browser_pool.get(...)` on a plugin instance id.

- [ ] **Step 3: Dispatch both sites**

Replace `wait_for_sync`'s inner `_wait`:

```python
async def _wait(p: dict[str, Any]) -> ScenarioParticipantOutcome:
    from octowright.scenario_kinds import adapter_for

    kind = p.get("kind") or ""
    adapter = adapter_for(kind, browser_pool=browser_pool)
    if not isinstance(adapter, SupportsSync):
        return {
            "instance_id": p["instance_id"],
            "ok": False,
            "error": f"kind {kind!r} does not support sync (its adapter provides no wait_for_sync)",
        }
    try:
        await adapter.wait_for_sync(p["instance_id"], selector=selector, text=text, url=url, timeout_ms=timeout_ms)
        return {"instance_id": p["instance_id"], "ok": True}
    except Exception as e:
        return {"instance_id": p["instance_id"], "ok": False, "error": repr(e)}
```

Replace `_apply_fixtures`'s inner `_apply`:

```python
    async def _apply(p: dict[str, Any]) -> None:
        from octowright.scenario_kinds import adapter_for

        adapter = adapter_for(p.get("kind") or "", browser_pool=browser_pool)
        # Two capabilities, not one: _validate_fixtures accepts exactly these
        # two keys and this function does nothing but dispatch to them, so a
        # single "fixtures" capability would need an undefined precedence
        # against its own constituents. A kind that supports one and not the
        # other gets the one it supports.
        if dialog_policy and isinstance(adapter, SupportsDialogPolicy):
            await adapter.set_dialog_policy(p["instance_id"], dialog_policy)
        if mock_routes and isinstance(adapter, SupportsMockRoutes):
            await adapter.install_mock_routes(p["instance_id"], list(mock_routes))
```

Import `SupportsSync`, `SupportsDialogPolicy` and `SupportsMockRoutes` at module level.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/test_scenario_plugin_participation.py -v`
Expected: all pass.

- [ ] **Step 5: Verify the suite**

Run: `uv run --active pytest -k "scenario" -q`, `uv run --active pytest tests/test_scenario_sync.py -q`, `uv run --active pytest tests/test_scenarios_terminal.py -q`.
Expected: green. Report counts.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/scenarios_pool.py tests/test_scenario_plugin_participation.py
git commit -m "feat(scenarios): sync and fixtures through the kind's adapter

Fixtures dispatch to TWO capability handlers rather than one. _validate_fixtures
accepts exactly dialog_policy and mock_routes and _apply_fixtures does nothing
but dispatch to them, so a single fixtures capability would need an undefined
precedence against its own constituents, or would apply the same fixture twice.
A kind supporting one and not the other now gets the one it supports."
```

---

## Task 8: `stop`, `remap`, and dropping `terminal_pool` from the tool layer

`stop` and `remap_participants` still take `terminal_pool` explicitly and route on it. They route through `pool_for_kind` instead, using each participant's recorded `kind`. `terminal_pool` stays a parameter — terminal is not registered — but stops being the thing that decides.

**Files:**
- Modify: `src/octowright/scenarios_pool.py` (`stop` ~line 335, `remap_participant` ~line 82, `remap_participants` ~line 134, `_pool_for` ~line 327)
- Modify: `src/octowright/server/scenarios.py` (call sites at lines 93, 112, 147, 229)
- Test: `tests/test_scenario_plugin_participation.py` (extend)

**Interfaces:**
- Consumes: `scenario_kinds.pool_for_kind` (Task 3).
- Produces: no new public names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scenario_plugin_participation.py`:

```python
async def test_stopping_a_mixed_scenario_closes_each_kind_through_its_own_pool(registered):
    _, ref_pool = registered
    spec = Scenario(
        name="mixed",
        participants=[
            Participant(persona="tanuki-tim", kind="chromium", role="player"),
            Participant(persona="ref-rita", kind="refkind", role="monitor"),
        ],
    )
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    ref_id = live.participants[1]["instance_id"]

    await sp.stop(scenario_id=live.scenario_id, browser_pool=_BrowserPool(), terminal_pool=None)
    assert ref_pool.closed == [ref_id], "the plugin's own pool must close its session"
    assert sp.maybe_get(live.scenario_id) is None


async def test_a_plugin_pool_failing_to_close_does_not_strand_the_scenario(registered):
    _, ref_pool = registered

    async def _boom(instance_id: str, *, force: bool = False) -> dict[str, Any]:
        raise RuntimeError("close exploded")

    spec = Scenario(name="s", participants=[Participant(persona="a", kind="refkind", role="monitor")])
    sp = ScenarioPool()
    live = await sp.start(spec=spec, browser_pool=_BrowserPool(), terminal_pool=None)
    ref_pool.close = _boom  # type: ignore[assignment]

    await sp.stop(scenario_id=live.scenario_id, browser_pool=_BrowserPool(), terminal_pool=None)
    assert sp.maybe_get(live.scenario_id) is None, "a failing close must still retire the scenario"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/test_scenario_plugin_participation.py -v`
Expected: FAIL — `_pool_for` returns the browser pool for a `refkind` participant, so the plugin session is never closed through its own pool.

- [ ] **Step 3: Route by recorded kind**

Replace `_pool_for` so it delegates:

```python
    @staticmethod
    def _pool_for(p: dict[str, Any], browser_pool: Any, terminal_pool: Any | None) -> Any:
        """Which pool owns an already-launched participant.

        Routes on the participant's RECORDED kind rather than on whether a
        terminal pool happens to be present, so a third kind needs no branch
        here at all.
        """
        from octowright.scenario_kinds import pool_for_kind

        return pool_for_kind(p.get("kind") or "", browser_pool=browser_pool, terminal_pool=terminal_pool)
```

Confirm `stop`, `remap_participant`, `remap_participants`, `_close_launched` and `_rollback_start` all obtain their pool through `_pool_for` (or `pool_for_kind` directly) rather than testing for terminal. Where a close is already wrapped in a try/except, leave the isolation as it is; where it is not, keep the existing behaviour rather than adding new swallowing in this task.

In `src/octowright/server/scenarios.py`, leave the `terminal_pool=terminal_pool` arguments in place — the parameter still exists and terminal still needs it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/test_scenario_plugin_participation.py -v`
Expected: all pass.

- [ ] **Step 5: Verify the suite**

Run: `uv run --active pytest -k "scenario" -q` and `uv run --active pytest tests/test_scenarios_terminal.py -q`.
Expected: green. Report counts and `scenarios_pool.py`'s line count.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/scenarios_pool.py tests/test_scenario_plugin_participation.py
git commit -m "feat(scenarios): route teardown by the participant's recorded kind

_pool_for tested for a terminal pool's presence, so a third kind needed a
third branch in every teardown path. It delegates to pool_for_kind and
routes on the kind each participant recorded at launch, which is what lets
stop, remap and rollback handle plugin sessions without knowing they exist."
```

---

## Task 9: The reference plugin grows a partial adapter

The reference plugin is the in-repo consumer that fails CI when the contract drifts. It grows the **floor and nothing else** — `resolve_participant`, no capabilities — because that is the case core is most likely to get wrong: a kind that can join a scenario but cannot run macros, sync, or take fixtures.

**Files:**
- Modify: `tests/plugins/reference/plugin.py`, `tests/plugins/reference/pool.py`
- Test: `tests/plugins/test_reference_scenario.py` (create)

**Interfaces:**
- Consumes: `PluginContext`; `ScenarioAdapter`.
- Produces: `tests.plugins.reference.plugin.ReferenceScenarioAdapter`

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_reference_scenario.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.plugins.contract import (
    ScenarioAdapter,
    SupportsDialogPolicy,
    SupportsMacros,
    SupportsMockRoutes,
    SupportsSync,
    capabilities_of,
)
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from octowright.scenarios import Participant
from tests.plugins.reference.plugin import plugin


def _adapter(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind=plugin.kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    pool = plugin.create_pool(ctx)
    return plugin.create_scenario_adapter(pool)


def test_the_reference_adapter_is_the_floor_and_nothing_more(tmp_path):
    adapter = _adapter(tmp_path)
    assert isinstance(adapter, ScenarioAdapter)
    assert not isinstance(adapter, SupportsMacros)
    assert not isinstance(adapter, SupportsSync)
    assert not isinstance(adapter, SupportsDialogPolicy)
    assert not isinstance(adapter, SupportsMockRoutes)
    assert capabilities_of(adapter) == frozenset()


def test_resolve_participant_produces_launch_kwargs_the_pool_accepts(tmp_path):
    adapter = _adapter(tmp_path)
    spec = Participant(persona="ref-rita", kind="refkind", role="monitor", options={"note": "hello"})
    resolved = adapter.resolve_participant(spec, None)
    assert resolved["label"] == "ref-rita"
    assert resolved["profile"] == "ref-rita"
    assert resolved["note"] == "hello", "options pass through opaquely"


async def test_the_resolved_kwargs_actually_launch(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind=plugin.kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    pool = plugin.create_pool(ctx)
    adapter = plugin.create_scenario_adapter(pool)
    spec = Participant(persona="ref-rita", kind="refkind", role="monitor")
    launched = await pool.launch(**adapter.resolve_participant(spec, None))
    assert launched["instance_id"]
    await pool.close(launched["instance_id"], force=True)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_reference_scenario.py -v`
Expected: FAIL — `ReferencePlugin.create_scenario_adapter` currently returns `None`.

- [ ] **Step 3: Give the reference plugin an adapter**

In `tests/plugins/reference/plugin.py`, add:

```python
class ReferenceScenarioAdapter:
    """The mandatory floor and nothing else.

    Deliberately partial: the interesting case for core is a kind that can JOIN
    a scenario but cannot run macros, sync, or take fixtures. A full adapter
    would exercise the same paths the browser adapter already covers, and would
    not prove that the capability narrowing actually narrows.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        # options pass through opaquely: core validated nothing inside them, and
        # the plugin is the only party that knows what its own settings mean.
        return {"label": spec.persona, "profile": spec.persona, **dict(spec.options)}
```

and change `create_scenario_adapter` to return one:

```python
    def create_scenario_adapter(self, pool: Any) -> Any:
        return ReferenceScenarioAdapter(pool)
```

Its current signature is `def create_scenario_adapter(self, pool: ReferencePool) -> None:` with a comment saying scenario participation "arrives in build step 3" — that comment is now stale, and the `-> None` return annotation must change too, or mypy will reject returning an adapter. Replace both rather than leaving a contradiction. `ReferencePool.launch` accepts `**_: Any`, so the extra `options` keys are absorbed — confirm that before writing, and say so in your report if the signature differs.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_reference_scenario.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run every gate**

Run: `uv run --active pytest tests/plugins -q`
Run: `uv run --active pytest -m "not live_browser and not memory_isolated" -q`
Run: `make lint` — **report the exit code explicitly.**
Expected: all green, `make lint` exit 0. `make lint` covers the whole tree and may surface issues from any task on this branch; fix what is genuinely broken, and never satisfy a gate by weakening it.

- [ ] **Step 6: Commit**

```bash
git add tests/plugins/reference/plugin.py tests/plugins/reference/pool.py \
        tests/plugins/test_reference_scenario.py
git commit -m "test(plugins): reference plugin grows a partial scenario adapter

The floor and nothing else, deliberately: the case core is most likely to
get wrong is a kind that can join a scenario but cannot run macros, sync or
take fixtures. A full adapter would only re-cover the browser adapter's
paths and would not prove the capability narrowing narrows."
```

---

## Done criteria

- `uv run --active pytest -m "not live_browser and not memory_isolated"` green.
- `make lint` exit 0.
- `tests/test_scenarios_terminal.py` and `tests/terminal/` green — terminal still runs on its own path.
- A scenario mixing a browser and a plugin participant starts, runs macros against the browser only, and stops, closing each through its own pool.
- A plugin kind with no adapter capabilities is refused from `run_macro`/`wait_for_sync` with an error naming the missing **capability**, not the kind.
- `octowright_status()["plugins"]` still `[]` on a default install.
- No push, no PR, no `CHANGELOG.md` edit, no baseline edit.

## Not in this step

- `/api/plugins`, plugin asset serving, `mountStream`, core-owned page chrome, the fallback renderer (step 4).
- Deleting terminal from core and standing up `octowright-terminal` (step 5) — including terminal's own `ScenarioAdapter`, which arrives with the extraction.
- Core owning the plugin session-detail envelope (step 4) — carried from step 2.
- Removing `terminal_pool` from `ScenarioPool`'s signatures. It stays until terminal is registered, because nothing else can supply that pool.

## Carried-forward findings

Deferred during step 2 and still open. None blocks this step; fix opportunistically only if a task already touches the code.

- `activate`'s core-tool-collision branch does not run the `on_rollback` hook, so a plugin refused there keeps its capability profile registered.
- `recording_truncated` is in `CONTROL_ACTIONS` but `_write_truncation_marker` writes it directly, so that member has no writer through `record_control`.
- `record_control` duplicates `record`'s encode/write/flush sequence.
- `SessionLaunch.commit()` does not compare `record.label`/`record.profile` against what `begin_session` received.
- Two plugins registering the same capability-profile name is silent last-write-wins.
- `DuplicatePluginNameError` is constructed but never raised.
- `PluginRegistry.maybe_get` has no production caller since `find_plugin_session` became the wired resolver — retire one.
- `_parse_artifact_registered_line` and `_registered_from_row` both type-check `artifact_id`.
- No HTTP-level test drives `DELETE /api/sessions/{id}` against a registered plugin session.
- `pools.items()` is evaluated outside `_close_plugin_pools_on_shutdown`'s guard.

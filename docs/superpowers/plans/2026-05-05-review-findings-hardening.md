# Review Findings Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four review findings around scenario startup, scenario remapping, HTTP launch defaults, and stale HTTP MCP session tracking.

**Architecture:** Keep the changes local to the affected modules, add regression tests first, then implement the minimal behavior changes to pass them. Prepare for future DI by threading `browser_pool` explicitly into scenario remap validation instead of deepening hidden global-state coupling.

**Tech Stack:** Python 3.11, pytest, Starlette, Playwright abstractions, FastMCP

---

### Task 1: Fail Scenario Startup When Startup Macros Fail

**Files:**
- Modify: `src/octowright/scenarios_pool.py`
- Test: `tests/test_scenarios_pool.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_start_cleans_up_and_fails_when_startup_macro_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = ScenarioPool()

    class FakeBrowserPool:
        def __init__(self) -> None:
            self.closed: list[str] = []

        async def spawn_roster(self, _specs: list[dict[str, object]]) -> dict[str, object]:
            return {
                "launched": [
                    {"instance_id": "i1", "log_path": "/tmp/i1.jsonl", "kind": "chromium", "profile": "cosmo"},
                ],
                "errors": [],
            }

        async def close(self, instance_id: str) -> dict[str, object]:
            self.closed.append(instance_id)
            return {"closed": True}

        def get(self, instance_id: str) -> object:
            return object()

    browser_pool = FakeBrowserPool()
    spec = Scenario(
        name="demo",
        participants=[Participant(persona="cosmo", kind="chromium", role="player", startup_macros=["login"])],
    )

    monkeypatch.setattr("octowright.scenarios.resolve_launch_kwargs", lambda p: {"kind": p.kind, "profile": p.persona})

    async def fail_macro(*, session: object, name: str, args: dict[str, object]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr("octowright.macros.run_macro", fail_macro)

    with pytest.raises(RuntimeError, match="startup macro"):
        await pool.start(spec=spec, browser_pool=browser_pool)

    assert browser_pool.closed == ["i1"]
    assert pool.list_live() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scenarios_pool.py::test_start_cleans_up_and_fails_when_startup_macro_fails -v`
Expected: FAIL because startup macro errors are only logged and the scenario remains started.

- [ ] **Step 3: Write minimal implementation**

```python
async def _run_startup_macros(browser_pool: Any, live: LiveScenario) -> None:
    failures: list[dict[str, str]] = []

    async def _run_for_participant(participant_dict: dict[str, Any], participant_spec: Any) -> None:
        for macro_name in resolve_startup_macros(participant_spec):
            session = browser_pool.get(participant_dict["instance_id"])
            try:
                await _macros.run_macro(session=session, name=macro_name, args={})
            except Exception as e:
                failures.append(
                    {
                        "instance_id": participant_dict["instance_id"],
                        "persona": participant_dict["persona"],
                        "macro": macro_name,
                        "error": repr(e),
                    }
                )

    await _asyncio.gather(
        *(_run_for_participant(pd, ps) for pd, ps in zip(live.participants, live.spec.participants, strict=True))
    )
    if failures:
        raise RuntimeError(f"startup macro failures: {failures}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_scenarios_pool.py::test_start_cleans_up_and_fails_when_startup_macro_fails -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_scenarios_pool.py src/octowright/scenarios_pool.py
git commit -m "fix: fail scenario startup on macro errors"
```

### Task 2: Validate Scenario Remaps Against the Live Browser Pool

**Files:**
- Modify: `src/octowright/scenarios_pool.py`
- Modify: `src/octowright/server/scenarios.py`
- Test: `tests/test_scenarios_pool.py`
- Test: `tests/test_server_scenarios_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_remap_participant_rejects_unknown_replacement_instance() -> None:
    pool = ScenarioPool()
    live = LiveScenario(
        scenario_id="s1",
        name="demo",
        spec=object(),
        participants=[{"instance_id": "old", "persona": "cosmo", "role": "player", "kind": "chromium"}],
    )
    pool._live["s1"] = live

    class FakeBrowserPool:
        def maybe_get(self, instance_id: str) -> object | None:
            return None

    with pytest.raises(ValueError, match="new_instance_id"):
        pool.remap_participant(
            scenario_id="s1",
            old_instance_id="old",
            new_instance_id="missing",
            browser_pool=FakeBrowserPool(),
        )


def test_remap_participant_rejects_kind_mismatch() -> None:
    pool = ScenarioPool()
    live = LiveScenario(
        scenario_id="s1",
        name="demo",
        spec=object(),
        participants=[{"instance_id": "old", "persona": "cosmo", "role": "player", "kind": "chromium"}],
    )
    pool._live["s1"] = live

    replacement = SimpleNamespace(instance_id="new", kind="firefox", profile="cosmo")

    class FakeBrowserPool:
        def maybe_get(self, instance_id: str) -> object | None:
            return replacement if instance_id == "new" else None

    with pytest.raises(ValueError, match="kind"):
        pool.remap_participant(
            scenario_id="s1",
            old_instance_id="old",
            new_instance_id="new",
            browser_pool=FakeBrowserPool(),
        )


def test_remap_participant_rejects_profile_mismatch() -> None:
    pool = ScenarioPool()
    live = LiveScenario(
        scenario_id="s1",
        name="demo",
        spec=object(),
        participants=[{"instance_id": "old", "persona": "cosmo", "role": "player", "kind": "chromium"}],
    )
    pool._live["s1"] = live

    replacement = SimpleNamespace(instance_id="new", kind="chromium", profile="ziggy")

    class FakeBrowserPool:
        def maybe_get(self, instance_id: str) -> object | None:
            return replacement if instance_id == "new" else None

    with pytest.raises(ValueError, match="profile"):
        pool.remap_participant(
            scenario_id="s1",
            old_instance_id="old",
            new_instance_id="new",
            browser_pool=FakeBrowserPool(),
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_scenarios_pool.py -k remap_participant -v`
Expected: FAIL because remap does not yet accept `browser_pool` and does not validate replacements.

- [ ] **Step 3: Write minimal implementation**

```python
def remap_participant(
    self,
    *,
    scenario_id: str,
    old_instance_id: str,
    new_instance_id: str,
    browser_pool: Any,
    role: str | None = None,
) -> dict[str, Any]:
    live = self.get(scenario_id)
    matches = [p for p in live.participants if p.get("instance_id") == old_instance_id]
    if role is not None:
        matches = [p for p in matches if p.get("role") == role]
    if not matches:
        ...
    if len(matches) > 1:
        ...
    replacement = browser_pool.maybe_get(new_instance_id)
    if replacement is None:
        raise ValueError(f"new_instance_id={new_instance_id!r} is not a live browser")
    target = matches[0]
    expected_kind = target.get("kind")
    if expected_kind is not None and getattr(replacement, "kind", None) != expected_kind:
        raise ValueError("replacement browser kind does not match scenario participant")
    expected_persona = target.get("persona")
    replacement_profile = getattr(replacement, "profile", None)
    if expected_persona and replacement_profile is not None and replacement_profile != expected_persona:
        raise ValueError("replacement browser profile does not match scenario participant persona")
    target["instance_id"] = new_instance_id
    return {...}
```

And thread the dependency through:

```python
def scenario_remap_participants(scenario_id: str, remaps: list[dict[str, Any]]) -> dict[str, Any]:
    return scenario_pool.remap_participants(scenario_id=scenario_id, remaps=remaps, browser_pool=pool)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scenarios_pool.py -k remap_participant -v`
Expected: PASS

- [ ] **Step 5: Add and run the tool-surface regression**

```python
def test_scenario_remap_participants_passes_browser_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_remap_participants(*, scenario_id: str, remaps: list[dict[str, object]], browser_pool: object) -> dict[str, object]:
        seen["scenario_id"] = scenario_id
        seen["remaps"] = remaps
        seen["browser_pool"] = browser_pool
        return {"scenario_id": scenario_id, "applied": [], "count": 0}

    monkeypatch.setattr(server_scenarios.scenario_pool, "remap_participants", fake_remap_participants)

    result = server_scenarios.scenario_remap_participants("s1", [{"old_instance_id": "a", "new_instance_id": "b"}])

    assert result["scenario_id"] == "s1"
    assert seen["browser_pool"] is server_scenarios.pool
```

Run: `uv run pytest tests/test_server_scenarios_tools.py::test_scenario_remap_participants_passes_browser_pool -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_scenarios_pool.py tests/test_server_scenarios_tools.py src/octowright/scenarios_pool.py src/octowright/server/scenarios.py
git commit -m "fix: validate scenario participant remaps"
```

### Task 3: Align HTTP Session Launch With Browser Pool Defaults

**Files:**
- Modify: `src/octowright/http/routes/sessions.py`
- Test: `tests/test_http_server_writes.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_session_launch_omits_headed_when_not_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_launch(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {
            "instance_id": "abc123",
            "kind": "chromium",
            "label": None,
            "profile": None,
            "url": "https://octowright.com",
            "log_path": str(Path("recordings") / "abc123.jsonl"),
        }

    monkeypatch.setattr(_state.pool, "launch", fake_launch)

    client = TestClient(build_app())
    response = client.post("/api/sessions", json={"kind": "chromium"})

    assert response.status_code == 201
    assert seen["headed"] is None


def test_session_launch_preserves_explicit_headed_false(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_launch(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {
            "instance_id": "abc123",
            "kind": "chromium",
            "label": None,
            "profile": None,
            "url": "https://octowright.com",
            "log_path": str(Path("recordings") / "abc123.jsonl"),
        }

    monkeypatch.setattr(_state.pool, "launch", fake_launch)

    client = TestClient(build_app())
    response = client.post("/api/sessions", json={"kind": "chromium", "headed": False})

    assert response.status_code == 201
    assert seen["headed"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_http_server_writes.py -k session_launch_omits_headed_when_not_provided -v`
Expected: FAIL because the route currently injects `headed=True`.

- [ ] **Step 3: Write minimal implementation**

```python
launch_kwargs: dict[str, Any] = {
    "kind": kind,
    "url": payload.get("url") or DEFAULT_URL,
    "label": payload.get("label"),
    "profile": payload.get("profile"),
    "viewport_w": payload.get("viewport_w"),
    "viewport_h": payload.get("viewport_h"),
    "headed": payload.get("headed") if "headed" in payload else None,
    "stabilize": payload.get("stabilize", False),
    "record_video": payload.get("record_video", False),
    "trace": payload.get("trace", False),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_http_server_writes.py -k "session_launch_omits_headed_when_not_provided or session_launch_preserves_explicit_headed_false" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_http_server_writes.py src/octowright/http/routes/sessions.py
git commit -m "fix: align http session launch defaults"
```

### Task 4: Clear Stale MCP Session Manager State on Non-Leader App Builds

**Files:**
- Modify: `src/octowright/http/app.py`
- Test: `tests/test_http_app_lifespan.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_app_non_leader_clears_stale_mcp_session_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    import octowright.http.app as http_app

    http_app._mcp_session_manager = SimpleNamespace(_server_instances={"stale": object()})

    app = http_app.build_app(mcp_leader=False)

    assert app is not None
    assert http_app.get_mcp_active_session_count() == 0
    assert http_app._mcp_session_manager is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_http_app_lifespan.py::test_build_app_non_leader_clears_stale_mcp_session_manager -v`
Expected: FAIL because `_mcp_session_manager` is left untouched.

- [ ] **Step 3: Write minimal implementation**

```python
def build_app(*, mcp_leader: bool = False) -> Starlette:
    global _mcp_session_manager

    routes: list[Any] = list(all_routes())
    lifespan = None
    _mcp_session_manager = None

    if mcp_leader:
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_http_app_lifespan.py::test_build_app_non_leader_clears_stale_mcp_session_manager -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_http_app_lifespan.py src/octowright/http/app.py
git commit -m "fix: clear stale mcp app session state"
```

### Task 5: Full Verification

**Files:**
- Verify only: `tests/test_scenarios_pool.py`
- Verify only: `tests/test_server_scenarios_tools.py`
- Verify only: `tests/test_http_server_writes.py`
- Verify only: `tests/test_http_app_lifespan.py`
- Verify only: `tests/`

- [ ] **Step 1: Run focused regression tests**

Run: `uv run pytest tests/test_scenarios_pool.py tests/test_server_scenarios_tools.py tests/test_http_server_writes.py tests/test_http_app_lifespan.py -v`
Expected: PASS

- [ ] **Step 2: Run the full Python suite**

Run: `make test`
Expected: PASS with coverage threshold met

- [ ] **Step 3: Commit the integrated fix set if working incrementally is not possible**

```bash
git add src/octowright/scenarios_pool.py src/octowright/server/scenarios.py src/octowright/http/routes/sessions.py src/octowright/http/app.py tests/test_scenarios_pool.py tests/test_server_scenarios_tools.py tests/test_http_server_writes.py tests/test_http_app_lifespan.py
git commit -m "fix: harden scenario and http runtime validation"
```

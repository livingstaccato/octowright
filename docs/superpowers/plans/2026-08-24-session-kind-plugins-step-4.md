# Session-Kind Plugins — Step 4: Frontend Contract

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a plugin ship dashboard UI — served from core, discovered by the SPA, and mounted into a pane core owns — so a session kind renders its own view without touching the WebSocket, the cursor protocol, or the auth notice.

**Architecture:** Core serves a plugin's prebuilt assets at `GET /plugins/{name}/{path}` and advertises them at `GET /api/plugins`. The SPA replaces its hardcoded `kind === "terminal"` branch with a registry lookup plus a dynamic import. Core renders the page chrome and does the history → timeline → tail wiring itself; the plugin implements exactly one function, `mountStream(el, ctx) -> StreamHandle`. Every failure path — no frontend, version mismatch, import failure, mount failure — falls back to a generic timeline **with a visible reason**.

**Tech Stack:** Python 3.11+, Starlette, TypeScript SPA (`packages/octowright-frontend/`), vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-session-kind-plugins-design.md` — this plan implements §8.5, §8.6, §8.7, §8.8, and the step-4 line of §12.

**Depends on:** Steps 1–3 (PR #140). `FrontendAsset` already exists in `plugins/contract.py` from step 1 — `renderer_api_version`, `asset_dir`, `module_path`, `layout` — and is currently consumed by nothing.

## What already exists — do not rebuild it

- `plugins/contract.FrontendAsset` (step 1), and `SessionKindPlugin.frontend: FrontendAsset | None`.
- `plugins/state.registry()` — the light-layer registry accessor (moved below the tool layer after step 3).
- `PluginRegistry.get_plugin(kind).descriptor.frontend` is the path to a kind's declared assets.
- `octowright_status()["plugins"]` already reports per-plugin status rows.
- The lazy-chunk dynamic-import pattern the SPA needs is already in use at `packages/octowright-frontend/src/session.ts:665`.

## Global Constraints

- **SPDX header** on every new `.py` file, verbatim:
  ```python
  # SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
  # SPDX-License-Identifier: Apache-2.0
  # SPDX-Comment: Part of octowright.
  #
  ```
- **`from __future__ import annotations`** as the first import in every new Python module.
- **777-line cap** on any `src/**/*.py` (`scripts/check_max_loc.py`).
- **Ruff `select`** is `["E", "F", "I", "UP", "B", "SIM", "ARG", "RUF", "TID"]`, `line-length = 120`. `ARG` **is** enabled, so a scoped `# noqa: ARG00N` with a stated reason is legitimate; `BLE`/`ANN`/`PLW` are **not** enabled and a noqa for those is itself a defect (RUF100 flags unused directives).
- **Do not edit `.ci/vulture-baseline.json` or `.ci/xenon-baseline.json`.** If xenon trips, decompose. Never satisfy a gate by weakening it.
- **Never pass `-q` to pytest** — `pyproject.toml` already bakes it into `addopts`, and a second one becomes `-qq`, which suppresses the summary line. Use `--no-cov` on scoped runs only; the full-suite run must satisfy the real coverage gate.
- **Run pytest from the repo root.** Invoking from a subdirectory changes collection and makes reported counts meaningless.
- **Frontend:** `cd packages/octowright-frontend && npm run test` (vitest). Tests are colocated `*.test.ts` beside their source; follow that, not `tests/`.
- Commits must be signed. Never `--no-gpg-sign` or `--no-verify`. If signing stalls, stop and ask.
- Never add a `Co-Authored-By` trailer or any AI-assistance mention to a commit message.
- **Do not touch `CHANGELOG.md`.** Do not push, do not open a PR.
- **Do not stage `.octowright/config.yaml`** — a test rewrites its `label:` from the CWD basename. Pre-existing churn. Stage explicitly; never `git add -A`.
- **Terminal keeps its own frontend path.** `session-terminal.ts` and `bootTerminalSession` are untouched by this step; step 5 deletes them when terminal becomes an external plugin.

## The decision this plan makes, and why

Spec §8.6 describes core owning the chrome while "the plugin" implements `mountStream`, using `bootTerminalSession` as the worked example. Read literally that suggests migrating terminal onto the new contract here.

**It does not.** Terminal is not a plugin until step 5, and migrating its frontend now would mean rewriting `session-terminal.ts` twice — once onto core's generic path, once again when it moves out to `octowright-terminal`. This matches how steps 2 and 3 handled terminal: the generic path is built *beside* the hardcoded one, and terminal moves in the extraction step.

So this step's `mountStream` contract is exercised by the **reference plugin's** renderer, and `session-terminal.ts` is left alone. Step 5 deletes it and ships `mountStream` from the external package.

**Consequence worth stating:** until step 5, core carries two stream page implementations — `bootTerminalSession` (terminal's) and `bootStreamSession` (generic). They will look similar. That is deliberate duplication with a scheduled end, not an oversight, and the generic one must not be "simplified" by making terminal call it.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `src/octowright/http/routes/plugin_assets.py` | `GET /plugins/{name}/{path}` — path-contained static serving from a plugin's `asset_dir`. |
| `src/octowright/http/routes/plugins_api.py` | `GET /api/plugins` — the renderer registry the SPA reads. |
| `packages/octowright-frontend/src/plugin-contract.d.ts` | Published types: `StreamContext`, `SessionEvent`, `StreamHandle`, `MountStream`. |
| `packages/octowright-frontend/src/session-stream.ts` | `bootStreamSession` — core-owned chrome + history/timeline/tail wiring, calling one plugin function. |
| `packages/octowright-frontend/src/session-fallback.ts` | The generic timeline + raw-JSONL view, always rendered with a visible reason. |
| `packages/octowright-frontend/src/plugin-registry.ts` | Fetches `/api/plugins`, checks `rendererApiVersion`, resolves a kind to a module URL. |
| `tests/plugins/test_plugin_assets_route.py` | Containment battery for the asset route. |
| `tests/plugins/test_plugins_api_route.py` | The registry payload. |
| `packages/octowright-frontend/src/session-stream.test.ts` | Chrome wiring, feed ordering, destroy. |
| `packages/octowright-frontend/src/session-fallback.test.ts` | Every fallback trigger renders its reason. |
| `packages/octowright-frontend/src/plugin-registry.test.ts` | Version gate and lookup. |

**Modified:**

| Path | Change |
|---|---|
| `src/octowright/http/app.py` | Register both routes **before** the SPA catchall mount. |
| `packages/octowright-frontend/src/session.ts` | Replace the `kind === "terminal"` branch with a registry lookup + dynamic import. |
| `packages/octowright-frontend/src/types.ts` | `connector_type` and the `"telnet"` kind member leave; `SessionSummary` keeps free-form `extra`. |
| `tests/plugins/reference/plugin.py` | Declare a `FrontendAsset`. |
| `tests/plugins/reference/assets/renderer.js` | A minimal `mountStream` the contract tests drive. |

---

## Task 1: Serving a plugin's assets

A plugin ships prebuilt UI; core serves it. `{name}` is the **entry-point name** (the configured identity, §4.2) rather than the kind, because that is what an operator writes in `OCTOWRIGHT_PLUGINS` and what appears in a URL — and unlike a kind, it may contain a hyphen.

Path containment is the whole risk here: `{path}` is attacker-influenceable in the same way a recording-supplied path is, so it goes through the same resolve-then-contain discipline `RECORDINGS_DIR` uses, with symlinks resolved **before** the prefix check.

**Gating is deliberately weaker than the session APIs.** Spec §8.5: these are static assets from an operator-enabled package, they carry no session data, and the dashboard shell must boot before pairing completes. So it sits behind the Host/Origin guard like the SPA mount — `pairing_exempt=True` — not behind dashboard pairing. Gating it would leave a paired dashboard unable to load the very code that renders its panes.

**Files:**
- Create: `src/octowright/http/routes/plugin_assets.py`
- Modify: `src/octowright/http/app.py`
- Test: `tests/plugins/test_plugin_assets_route.py`

**Interfaces:**
- Consumes: `octowright._paths.reject_unsafe_path(candidate, root, *, label) -> Path`; `octowright.plugins.state.registry()`; `octowright.http.exposure.guard_sensitive_http`.
- Produces: route `GET /plugins/{name}/{path:path}`; handler `plugin_asset`; `plugin_asset_routes() -> list[Route]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/plugins/test_plugin_assets_route.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from octowright.plugins.contract import FrontendAsset
from octowright.plugins.registry import PluginRegistry
from octowright.plugins import state as plugin_state


class _Descriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None

    def __init__(self, frontend: FrontendAsset | None) -> None:
        self.frontend = frontend

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _Discovered:
    """Minimal stand-in carrying the entry-point NAME, which is the URL segment."""

    def __init__(self, name: str) -> None:
        self.name = name

    def status_row(self, state: str) -> dict[str, Any]:
        return {"name": self.name, "state": state}


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Register one plugin whose assets live in a real directory on disk."""
    from starlette.testclient import TestClient

    from octowright.http.app import build_app

    asset_dir = tmp_path / "assets"
    asset_dir.mkdir()
    (asset_dir / "renderer.js").write_text("export function mountStream() {}\n", encoding="utf-8")
    (asset_dir / "style.css").write_text(".x{}\n", encoding="utf-8")
    nested = asset_dir / "sub"
    nested.mkdir()
    (nested / "deep.js").write_text("//deep\n", encoding="utf-8")

    original = plugin_state.registry()
    reg = PluginRegistry()
    frontend = FrontendAsset(renderer_api_version=1, asset_dir=asset_dir, module_path="renderer.js", layout="stream")
    reg.register(_Descriptor(frontend), pool=object(), adapter=None, discovered=_Discovered("my-plugin"))
    plugin_state.set_registry(reg)
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
    try:
        yield TestClient(build_app()), asset_dir
    finally:
        plugin_state.set_registry(original)


def test_a_declared_asset_is_served(served):
    client, _ = served
    resp = client.get("/plugins/my-plugin/renderer.js")
    assert resp.status_code == 200
    assert "mountStream" in resp.text


def test_a_nested_asset_is_served(served):
    client, _ = served
    assert client.get("/plugins/my-plugin/sub/deep.js").status_code == 200


def test_an_unknown_plugin_name_is_404(served):
    client, _ = served
    assert client.get("/plugins/nosuchplugin/renderer.js").status_code == 404


def test_a_plugin_with_no_frontend_is_404(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from octowright.http.app import build_app

    original = plugin_state.registry()
    reg = PluginRegistry()
    reg.register(_Descriptor(None), pool=object(), adapter=None, discovered=_Discovered("bare"))
    plugin_state.set_registry(reg)
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
    try:
        client = TestClient(build_app())
        assert client.get("/plugins/bare/renderer.js").status_code == 404
    finally:
        plugin_state.set_registry(original)


@pytest.mark.parametrize("path", ["../secret.txt", "sub/../../secret.txt", "..%2Fsecret.txt"])
def test_traversal_is_refused(served, tmp_path, path):
    client, asset_dir = served
    (tmp_path / "secret.txt").write_text("do not serve me", encoding="utf-8")
    resp = client.get(f"/plugins/my-plugin/{path}")
    assert resp.status_code in (400, 404)
    assert "do not serve me" not in resp.text


def test_a_symlink_escaping_the_asset_dir_is_refused(served, tmp_path):
    client, asset_dir = served
    outside = tmp_path / "outside.js"
    outside.write_text("escaped", encoding="utf-8")
    (asset_dir / "escape.js").symlink_to(outside)
    resp = client.get("/plugins/my-plugin/escape.js")
    assert resp.status_code in (400, 404)
    assert "escaped" not in resp.text


def test_a_missing_file_under_a_valid_plugin_is_404(served):
    client, _ = served
    assert client.get("/plugins/my-plugin/nope.js").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_plugin_assets_route.py -v --no-cov`
Expected: FAIL — every request 404s because the route does not exist (the SPA catchall answers).

- [ ] **Step 3: Write the route**

Create `src/octowright/http/routes/plugin_assets.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Static assets a session-kind plugin ships for the dashboard.

Addressed by the plugin's ENTRY-POINT NAME rather than its kind: the name is the
configured identity an operator writes in ``OCTOWRIGHT_PLUGINS``, and unlike a
kind it may contain a hyphen, which suits a URL segment.

Gated like the static SPA mount, NOT like the session APIs. These are static
files from an operator-enabled package carrying no session data, and the
dashboard shell has to boot before pairing completes -- gating them would leave
a paired dashboard unable to load the code that renders its own panes.

``{path}`` is caller-supplied, so it goes through the same resolve-then-contain
discipline every other caller-influenced path in this codebase uses, with
symlinks resolved before the prefix check.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from octowright._paths import reject_unsafe_path
from octowright.http.exposure import guard_sensitive_http

#: Extensions the dashboard can actually use. Deliberately closed for the same
#: reason ARTIFACT_MIME_ALLOWLIST is: this route serves from the dashboard's own
#: origin, so anything it hands back runs beside the pairing bearer.
_ASSET_SUFFIXES: frozenset[str] = frozenset({".js", ".mjs", ".css", ".map", ".woff2", ".svg", ".png"})


def _asset_dir_for(name: str) -> Path | None:
    """The declared asset directory for entry-point ``name``, or None."""
    from octowright.plugins.state import registry

    reg = registry()
    for row in reg.status_rows():
        if row.get("name") != name:
            continue
        kind = row.get("kind")
        if not isinstance(kind, str) or kind not in reg.kinds():
            return None
        frontend = reg.get_plugin(kind).descriptor.frontend
        return None if frontend is None else Path(frontend.asset_dir)
    return None


async def plugin_asset(request: Request) -> Response:
    """GET /plugins/{name}/{path} — serve one file from a plugin's asset dir."""
    name = request.path_params["name"]
    rel = request.path_params["path"]

    asset_dir = _asset_dir_for(name)
    if asset_dir is None:
        return JSONResponse({"error": "no such plugin frontend"}, status_code=404)

    candidate = asset_dir / rel
    if candidate.suffix not in _ASSET_SUFFIXES:
        return JSONResponse({"error": f"asset type {candidate.suffix!r} is not served"}, status_code=404)
    try:
        resolved = reject_unsafe_path(candidate, asset_dir, label="plugin asset")
    except ValueError:
        return JSONResponse({"error": "asset path escapes the plugin's asset dir"}, status_code=404)
    if not resolved.is_file():
        return JSONResponse({"error": "no such asset"}, status_code=404)
    return FileResponse(path=str(resolved), filename=resolved.name)


def plugin_asset_routes() -> list[Route]:
    # pairing_exempt: see the module docstring -- the shell must boot before
    # pairing completes, and these files carry no session data.
    return [
        Route(
            "/plugins/{name}/{path:path}",
            guard_sensitive_http(plugin_asset, pairing_exempt=True),
            methods=["GET"],
        )
    ]
```

- [ ] **Step 4: Register the route before the SPA catchall**

In `src/octowright/http/app.py`, immediately before `routes.extend(_frontend_routes(host=host))`:

```python
    # Plugin assets, before the SPA catchall for the same reason /new-tab is:
    # StaticFiles at "/" would otherwise swallow them.
    from octowright.http.routes.plugin_assets import plugin_asset_routes

    routes.extend(plugin_asset_routes())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_plugin_assets_route.py -v --no-cov`
Expected: all pass. If the traversal cases return 200, stop — the containment check is not doing its job and that is the whole point of this task.

- [ ] **Step 6: Verify nothing regressed**

Run: `uv run --active pytest -k "http or dashboard or pairing" --no-cov` and `uv run --active python scripts/check_vulture.py`. Report counts.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/http/routes/plugin_assets.py src/octowright/http/app.py \
        tests/plugins/test_plugin_assets_route.py
git commit -m "feat(http): serve a plugin's dashboard assets

Addressed by entry-point name rather than kind: the name is the configured
identity an operator writes in OCTOWRIGHT_PLUGINS, and unlike a kind it may
contain a hyphen, which suits a URL segment.

Gated like the static SPA mount rather than the session APIs. These are
static files from an operator-enabled package with no session data, and the
shell must boot before pairing completes -- gating them would leave a paired
dashboard unable to load the code that renders its own panes.

The caller-supplied path goes through the same resolve-then-contain check
every other caller-influenced path here uses, symlinks resolved first, plus
a closed suffix allowlist because this serves from the dashboard's origin."
```

---

## Task 2: `GET /api/plugins`

The registry the SPA reads to decide what to import. One row per kind that declares a frontend.

`moduleUrl` is built from the entry-point name and `module_path`, so the SPA never composes a plugin URL itself — the same reasoning that keeps plugins out of path composition on the artifact side.

**Files:**
- Create: `src/octowright/http/routes/plugins_api.py`
- Modify: `src/octowright/http/app.py`
- Test: `tests/plugins/test_plugins_api_route.py`

**Interfaces:**
- Consumes: `octowright.plugins.state.registry()`.
- Produces: route `GET /api/plugins`; handler `list_plugin_frontends`; `plugins_api_routes() -> list[Route]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/plugins/test_plugins_api_route.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins import state as plugin_state
from octowright.plugins.contract import FrontendAsset
from octowright.plugins.registry import PluginRegistry


class _Descriptor:
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None

    def __init__(self, kind: str, display_name: str, frontend: FrontendAsset | None) -> None:
        self.kind = kind
        self.display_name = display_name
        self.frontend = frontend

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _Discovered:
    def __init__(self, name: str) -> None:
        self.name = name

    def status_row(self, state: str) -> dict[str, Any]:
        return {"name": self.name, "state": state}


@pytest.fixture
def client_with(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from octowright.http.app import build_app

    def _build(*entries):
        original = plugin_state.registry()
        reg = PluginRegistry()
        for name, kind, display, frontend in entries:
            reg.register(
                _Descriptor(kind, display, frontend), pool=object(), adapter=None, discovered=_Discovered(name)
            )
        plugin_state.set_registry(reg)
        monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
        return TestClient(build_app()), original

    yield _build
    # each test restores its own original inside the test body


def _asset(tmp_path, module_path="renderer.js", version=1, layout="stream"):
    d = tmp_path / "a"
    d.mkdir(exist_ok=True)
    return FrontendAsset(renderer_api_version=version, asset_dir=d, module_path=module_path, layout=layout)


def test_a_kind_with_a_frontend_is_listed(client_with, tmp_path):
    client, original = client_with(("my-plugin", "refkind", "Reference Kind", _asset(tmp_path)))
    try:
        body = client.get("/api/plugins").json()
        assert body["refkind"] == {
            "moduleUrl": "/plugins/my-plugin/renderer.js",
            "rendererApiVersion": 1,
            "displayName": "Reference Kind",
            "layout": "stream",
        }
    finally:
        plugin_state.set_registry(original)


def test_a_kind_without_a_frontend_is_absent(client_with, tmp_path):
    client, original = client_with(
        ("with-ui", "hasui", "Has UI", _asset(tmp_path)),
        ("no-ui", "noui", "No UI", None),
    )
    try:
        body = client.get("/api/plugins").json()
        assert "hasui" in body
        assert "noui" not in body, "a kind with no frontend must not appear at all"
    finally:
        plugin_state.set_registry(original)


def test_no_plugins_is_an_empty_object(client_with):
    client, original = client_with()
    try:
        assert client.get("/api/plugins").json() == {}
    finally:
        plugin_state.set_registry(original)


def test_module_url_is_built_from_the_entry_point_name(client_with, tmp_path):
    """The SPA never composes a plugin URL itself, so core owns this join."""
    client, original = client_with(("dash-named", "k", "K", _asset(tmp_path, module_path="dist/main.mjs")))
    try:
        body = client.get("/api/plugins").json()
        assert body["k"]["moduleUrl"] == "/plugins/dash-named/dist/main.mjs"
    finally:
        plugin_state.set_registry(original)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_plugins_api_route.py -v --no-cov`
Expected: FAIL — `/api/plugins` does not exist.

- [ ] **Step 3: Write the route**

Create `src/octowright/http/routes/plugins_api.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The renderer registry the dashboard reads.

One row per kind that actually declares a frontend; a kind without one is
absent rather than present-and-null, so the SPA's check is a lookup miss rather
than a null test.

``moduleUrl`` is joined here, not in the SPA. Core owns every URL a plugin is
reachable at, for the same reason it owns artifact path composition: the one
place that builds it is the one place that has to be right.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from octowright.http.exposure import guard_sensitive_http


def _frontend_rows() -> dict[str, dict[str, Any]]:
    from octowright.plugins.state import registry

    reg = registry()
    by_kind: dict[str, dict[str, Any]] = {}
    names_by_kind = {row.get("kind"): row.get("name") for row in reg.status_rows() if row.get("kind")}
    for kind in reg.kinds():
        descriptor = reg.get_plugin(kind).descriptor
        frontend = descriptor.frontend
        if frontend is None:
            continue
        name = names_by_kind.get(kind)
        if not name:
            continue
        by_kind[kind] = {
            "moduleUrl": f"/plugins/{name}/{frontend.module_path}",
            "rendererApiVersion": frontend.renderer_api_version,
            "displayName": descriptor.display_name,
            "layout": frontend.layout,
        }
    return by_kind


async def list_plugin_frontends(_request: Request) -> JSONResponse:
    """GET /api/plugins — kind → renderer descriptor."""
    return JSONResponse(_frontend_rows())


def plugins_api_routes() -> list[Route]:
    # pairing_exempt for the same reason the asset route is: the shell reads this
    # to decide what to import, before pairing has necessarily completed. It
    # exposes only what an operator already enabled -- no session data.
    return [Route("/api/plugins", guard_sensitive_http(list_plugin_frontends, pairing_exempt=True), methods=["GET"])]
```

- [ ] **Step 4: Register it**

In `src/octowright/http/app.py`, beside the plugin-asset registration from Task 1:

```python
    from octowright.http.routes.plugins_api import plugins_api_routes

    routes.extend(plugins_api_routes())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_plugins_api_route.py -v --no-cov`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/http/routes/plugins_api.py src/octowright/http/app.py \
        tests/plugins/test_plugins_api_route.py
git commit -m "feat(http): advertise plugin renderers at /api/plugins

One row per kind that actually declares a frontend, so the SPA's check is a
lookup miss rather than a null test. moduleUrl is joined here rather than in
the SPA: core owns every URL a plugin is reachable at, for the same reason it
owns artifact path composition -- the one place that builds it is the one
place that has to be right."
```

---

## Task 3: The published type contract

A third party builds against types, not a prose sketch. This task ships only declarations — no logic, no tests of its own beyond compiling.

**Files:**
- Create: `packages/octowright-frontend/src/plugin-contract.d.ts`

**Interfaces:**
- Produces: `StreamContext`, `SessionEvent`, `StreamHandle`, `MountStream`.

- [ ] **Step 1: Write the declarations**

Create `packages/octowright-frontend/src/plugin-contract.d.ts`:

```ts
// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * The contract a session-kind plugin's dashboard renderer implements.
 *
 * Core owns the page chrome, the WebSocket, the cursor protocol and the auth
 * notice. A plugin implements exactly one function and receives batches.
 */

/**
 * One recorded JSONL row. Kind-specific fields ride along untyped.
 *
 * Structurally identical to core's internal `RecordingEvent` (`types.ts`), and
 * declared separately on purpose: this file is the PUBLISHED contract, and a
 * third party should not have to import core's internal types to build against
 * it. TypeScript is structural, so core passes these to `renderTimeline` with
 * no conversion.
 *
 * Two identical interfaces can silently diverge, so a test pins their
 * compatibility (Task 3, step 2) rather than trusting the comment.
 */
export interface SessionEvent {
  ts: string;
  action: string;
  [field: string]: unknown;
}

/** What core hands a renderer at mount time. */
export interface StreamContext {
  /** The session this pane renders. */
  sessionId: string;
  /** Whether the session is still live; a closed session receives history only. */
  live: boolean;
  /** The kind that selected this renderer. */
  kind: string;
}

export interface StreamHandle {
  /**
   * Receive a batch of events in JSONL order.
   *
   * Historical events are fed before any live event. Delivery is
   * AT-LEAST-ONCE: a `/tail` reconnect may replay a batch, so a renderer must
   * tolerate a repeat.
   */
  feed(events: SessionEvent[]): void;

  /** Idempotent. Core always calls it on teardown once a handle exists. */
  destroy(): void;
}

/**
 * May be async; core awaits it before the first `feed`.
 *
 * Throwing (or rejecting) yields no handle, so there is nothing to destroy —
 * core switches the pane to its fallback renderer with a visible reason.
 */
export type MountStream = (el: HTMLElement, ctx: StreamContext) => StreamHandle | Promise<StreamHandle>;
```

- [ ] **Step 2: Pin compatibility with core's internal event type, then verify it compiles**

`SessionEvent` and core's `RecordingEvent` are structurally identical and
declared separately on purpose (see the docstring). Nothing stops them
diverging, and the failure mode is ugly: core keeps compiling while every
third-party renderer built against the published type breaks.

Create `packages/octowright-frontend/src/plugin-contract.test.ts`:

```ts
import { describe, expectTypeOf, it } from "vitest";

import type { SessionEvent } from "./plugin-contract.js";
import type { RecordingEvent } from "./types.js";

describe("published contract", () => {
  it("SessionEvent stays assignable to core's RecordingEvent in both directions", () => {
    // The published type is declared standalone so a third party need not
    // import core internals -- but core feeds these straight into
    // renderTimeline, which takes RecordingEvent. If the two ever diverge,
    // this fails here rather than in someone else's plugin.
    expectTypeOf<SessionEvent>().toMatchTypeOf<RecordingEvent>();
    expectTypeOf<RecordingEvent>().toMatchTypeOf<SessionEvent>();
  });
});
```

If `expectTypeOf` is unavailable in this vitest version, assert it with a
compile-time assignment pair instead (`const _a: RecordingEvent = {} as SessionEvent;`
and the reverse) and say which you used.

Run: `cd packages/octowright-frontend && npx tsc --noEmit && npx vitest run src/plugin-contract.test.ts`
Expected: clean, 1 passing.

- [ ] **Step 3: Commit**

```bash
git add packages/octowright-frontend/src/plugin-contract.d.ts \
        packages/octowright-frontend/src/plugin-contract.test.ts
git commit -m "feat(frontend): publish the plugin renderer type contract

A third party builds against types rather than a prose sketch. Declarations
only: StreamContext, SessionEvent, StreamHandle and MountStream, with the
three rules a renderer cannot read off core's source -- mount may be async,
feed receives batches in JSONL order with history before live, and delivery
is at-least-once because a tail reconnect may replay.

SessionEvent is declared standalone rather than importing core's identical
RecordingEvent, so a third party builds against the published surface alone.
A type test pins the two assignable in both directions: without it they can
diverge while core still compiles and every external renderer breaks."
```

---

## Task 4: The fallback renderer

Built before the thing it backstops, because every later task's failure path lands here. Spec §8.7: a blank panel with a console error is the worst possible failure for something a third party built, so **every** fallback renders a visible reason.

Four triggers: the kind has no frontend, `rendererApiVersion` mismatches, the module import fails, or `mountStream` throws.

**Files:**
- Create: `packages/octowright-frontend/src/session-fallback.ts`
- Test: `packages/octowright-frontend/src/session-fallback.test.ts`

**Interfaces:**
- Consumes: `renderTimeline` from `./timeline.js`.
- Produces: `mountFallbackStream(el, ctx, reason: FallbackReason) -> StreamHandle`; `type FallbackReason`.

- [ ] **Step 1: Write the failing tests**

Create `packages/octowright-frontend/src/session-fallback.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { mountFallbackStream } from "./session-fallback.js";

const ctx = { sessionId: "s1", live: false, kind: "refkind" };

describe("mountFallbackStream", () => {
  it("renders a visible reason for every trigger", () => {
    const triggers = [
      { code: "no-frontend" as const, match: /no renderer/i },
      { code: "version-mismatch" as const, match: /version/i },
      { code: "import-failed" as const, match: /load/i },
      { code: "mount-failed" as const, match: /render/i },
    ];
    for (const t of triggers) {
      const el = document.createElement("div");
      mountFallbackStream(el, ctx, { code: t.code, detail: "boom" });
      expect(el.textContent ?? "").toMatch(t.match);
    }
  });

  it("names the kind so an operator knows which plugin failed", () => {
    const el = document.createElement("div");
    mountFallbackStream(el, ctx, { code: "import-failed", detail: "404" });
    expect(el.textContent).toContain("refkind");
  });

  it("surfaces the underlying detail rather than swallowing it", () => {
    const el = document.createElement("div");
    mountFallbackStream(el, ctx, { code: "mount-failed", detail: "TypeError: x is not a function" });
    expect(el.textContent).toContain("TypeError: x is not a function");
  });

  it("still shows events, so a failed renderer is degraded not blank", () => {
    const el = document.createElement("div");
    const handle = mountFallbackStream(el, ctx, { code: "no-frontend", detail: "" });
    handle.feed([{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }]);
    expect(el.textContent).toContain("ref_ready");
  });

  it("destroy is idempotent", () => {
    const el = document.createElement("div");
    const handle = mountFallbackStream(el, ctx, { code: "no-frontend", detail: "" });
    handle.destroy();
    expect(() => handle.destroy()).not.toThrow();
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd packages/octowright-frontend && npx vitest run src/session-fallback.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the fallback**

Create `packages/octowright-frontend/src/session-fallback.ts`:

```ts
// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * The renderer core uses when a plugin's own cannot run.
 *
 * Every path here renders a VISIBLE reason. A blank pane with a console error
 * is the worst possible failure for something a third party built: the operator
 * sees nothing, the plugin author hears nothing, and the dashboard looks broken
 * rather than degraded.
 *
 * It is a real renderer, not an error box — it still shows the session's events,
 * so a kind whose renderer failed is degraded rather than useless.
 */

import type { SessionEvent, StreamContext, StreamHandle } from "./plugin-contract.js";
import { appendTimelineEvents, renderTimeline } from "./timeline.js";

export type FallbackCode = "no-frontend" | "version-mismatch" | "import-failed" | "mount-failed";

export interface FallbackReason {
  code: FallbackCode;
  detail: string;
}

const HEADLINE: Record<FallbackCode, string> = {
  "no-frontend": "This session kind ships no renderer — showing the generic timeline.",
  "version-mismatch": "This renderer targets a different dashboard version — showing the generic timeline.",
  "import-failed": "This kind's renderer failed to load — showing the generic timeline.",
  "mount-failed": "This kind's renderer failed to render — showing the generic timeline.",
};

export function mountFallbackStream(
  el: HTMLElement,
  ctx: StreamContext,
  reason: FallbackReason,
): StreamHandle {
  el.innerHTML = "";
  el.classList.add("session-stream--fallback");

  const notice = document.createElement("div");
  notice.className = "session-stream-fallback-notice";
  notice.setAttribute("data-testid", "stream-fallback-notice");
  notice.setAttribute("data-fallback-code", reason.code);
  // Name the kind: with several plugins enabled, "a renderer failed" does not
  // tell an operator which package to look at.
  notice.textContent = `${HEADLINE[reason.code]} (kind: ${ctx.kind}${reason.detail ? ` — ${reason.detail}` : ""})`;

  const timeline = document.createElement("div");
  timeline.className = "session-stream-fallback-timeline";
  timeline.setAttribute("data-testid", "stream-fallback-timeline");

  el.append(notice, timeline);

  let base: string | null = null;
  let destroyed = false;

  return {
    feed(events: SessionEvent[]): void {
      if (destroyed || events.length === 0) return;
      if (base === null) {
        base = events[0]?.ts ?? new Date().toISOString();
        renderTimeline(timeline, events);
        return;
      }
      appendTimelineEvents(timeline, events, base);
    },
    destroy(): void {
      destroyed = true;
    },
  };
}
```

Signatures verified against the tree: `renderTimeline(container, events, opts?)` and `appendTimelineEvents(container, newEvents, baseIso, opts?)`, both taking `RecordingEvent[]` — structurally identical to `SessionEvent`, so they accept it with no conversion. `getLogger(...).warn` is also confirmed present. If any of that has changed by the time you implement, use the real signatures and say so in your report.

- [ ] **Step 4: Run to verify they pass**

Run: `cd packages/octowright-frontend && npx vitest run src/session-fallback.test.ts`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/octowright-frontend/src/session-fallback.ts \
        packages/octowright-frontend/src/session-fallback.test.ts
git commit -m "feat(frontend): generic fallback renderer with a visible reason

Built before the thing it backstops, because every remaining task's failure
path lands here. A blank pane with a console error is the worst possible
failure for something a third party built -- the operator sees nothing and
the author hears nothing -- so all four triggers render a reason naming the
kind. It stays a real renderer showing the session's events, so a kind whose
renderer failed is degraded rather than useless."
```

---

## Task 5: The plugin registry client

Fetches `/api/plugins` once and answers "what renders this kind, and may I use it?". The version gate lives here, not in the boot path, so the boot path has one decision to make.

Spec §8.7: `renderer_api_version` is checked by the **dashboard** against the version its SPA implements — deliberately separate from `plugin_api_version`, which the loader checks. Collapsing them makes this path unreachable, since a version-mismatched plugin would be refused at load and never reach `/api/plugins`.

**Files:**
- Create: `packages/octowright-frontend/src/plugin-registry.ts`
- Test: `packages/octowright-frontend/src/plugin-registry.test.ts`

**Interfaces:**
- Produces: `RENDERER_API_VERSION: number`; `PluginFrontend`; `loadPluginRegistry(fetchImpl?) -> Promise<Map<string, PluginFrontend>>`; `resolveRenderer(registry, kind) -> {moduleUrl, layout} | FallbackReason`.

- [ ] **Step 1: Write the failing tests**

Create `packages/octowright-frontend/src/plugin-registry.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";

import { RENDERER_API_VERSION, loadPluginRegistry, resolveRenderer } from "./plugin-registry.js";

function fakeFetch(body: unknown, ok = true) {
  return vi.fn().mockResolvedValue({ ok, json: async () => body });
}

describe("loadPluginRegistry", () => {
  it("maps kind to its renderer descriptor", async () => {
    const reg = await loadPluginRegistry(
      fakeFetch({
        refkind: {
          moduleUrl: "/plugins/p/renderer.js",
          rendererApiVersion: RENDERER_API_VERSION,
          displayName: "Ref",
          layout: "stream",
        },
      }) as never,
    );
    expect(reg.get("refkind")?.moduleUrl).toBe("/plugins/p/renderer.js");
  });

  it("is empty rather than throwing when the endpoint fails", async () => {
    const reg = await loadPluginRegistry(vi.fn().mockRejectedValue(new Error("offline")) as never);
    expect(reg.size).toBe(0);
  });

  it("is empty rather than throwing on a non-ok response", async () => {
    const reg = await loadPluginRegistry(fakeFetch({}, false) as never);
    expect(reg.size).toBe(0);
  });
});

describe("resolveRenderer", () => {
  const entry = (version: number) => ({
    moduleUrl: "/plugins/p/renderer.js",
    rendererApiVersion: version,
    displayName: "Ref",
    layout: "stream" as const,
  });

  it("resolves a matching version", () => {
    const reg = new Map([["refkind", entry(RENDERER_API_VERSION)]]);
    expect(resolveRenderer(reg, "refkind")).toEqual({
      moduleUrl: "/plugins/p/renderer.js",
      layout: "stream",
    });
  });

  it("refuses a mismatched version with that reason", () => {
    const reg = new Map([["refkind", entry(RENDERER_API_VERSION + 1)]]);
    const out = resolveRenderer(reg, "refkind");
    expect(out).toMatchObject({ code: "version-mismatch" });
    expect((out as { detail: string }).detail).toContain(String(RENDERER_API_VERSION));
  });

  it("reports no-frontend for an unknown kind", () => {
    expect(resolveRenderer(new Map(), "nosuch")).toMatchObject({ code: "no-frontend" });
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd packages/octowright-frontend && npx vitest run src/plugin-registry.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the registry client**

Create `packages/octowright-frontend/src/plugin-registry.ts`:

```ts
// SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
// SPDX-License-Identifier: Apache-2.0
// SPDX-Comment: Part of octowright.

/**
 * What renders a kind, and whether this dashboard may use it.
 *
 * The version gate lives here rather than in the boot path so the boot path
 * has exactly one decision: render the plugin's module, or render the fallback
 * with the reason this module already produced.
 *
 * RENDERER_API_VERSION is the version THIS SPA implements. It is deliberately
 * separate from the backend's plugin_api_version, which the loader checks:
 * collapsing them would make this path unreachable, because a mismatched
 * plugin would be refused at load and never reach /api/plugins -- and a plugin
 * whose UI is a version behind should not be refused wholesale.
 */

import type { FallbackReason } from "./session-fallback.js";
import { getLogger } from "./telemetry.js";

const log = getLogger("octowright.frontend.plugin-registry");

/** Bump when the StreamContext/StreamHandle contract changes shape. */
export const RENDERER_API_VERSION = 1;

export interface PluginFrontend {
  moduleUrl: string;
  rendererApiVersion: number;
  displayName: string;
  layout: "browser" | "stream";
}

export async function loadPluginRegistry(
  fetchImpl: typeof fetch = fetch,
): Promise<Map<string, PluginFrontend>> {
  // A dashboard that cannot reach /api/plugins still has to render browser
  // sessions, so this degrades to "no plugin renderers" rather than failing
  // the page.
  try {
    const resp = await fetchImpl("/api/plugins");
    if (!resp.ok) {
      log.warn({ event: "plugin_registry_not_ok", status: resp.status });
      return new Map();
    }
    const body = (await resp.json()) as Record<string, PluginFrontend>;
    return new Map(Object.entries(body));
  } catch (err) {
    log.warn({ event: "plugin_registry_fetch_failed", error: String(err) });
    return new Map();
  }
}

export function resolveRenderer(
  registry: Map<string, PluginFrontend>,
  kind: string,
): { moduleUrl: string; layout: "browser" | "stream" } | FallbackReason {
  const entry = registry.get(kind);
  if (!entry) {
    return { code: "no-frontend", detail: "" };
  }
  if (entry.rendererApiVersion !== RENDERER_API_VERSION) {
    return {
      code: "version-mismatch",
      detail: `plugin targets renderer API v${entry.rendererApiVersion}, dashboard implements v${RENDERER_API_VERSION}`,
    };
  }
  return { moduleUrl: entry.moduleUrl, layout: entry.layout };
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd packages/octowright-frontend && npx vitest run src/plugin-registry.test.ts`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/octowright-frontend/src/plugin-registry.ts \
        packages/octowright-frontend/src/plugin-registry.test.ts
git commit -m "feat(frontend): plugin renderer registry with the version gate

The gate lives here rather than in the boot path so the boot path has one
decision: render the module, or render the fallback with the reason this
module already produced.

RENDERER_API_VERSION is what this SPA implements, deliberately separate from
the backend's plugin_api_version. Collapsing them would make the mismatch
path unreachable -- a mismatched plugin would be refused at load and never
reach /api/plugins -- and a plugin whose UI is one version behind should not
be refused wholesale.

A dashboard that cannot reach /api/plugins degrades to no plugin renderers
rather than failing the page: browser sessions must still render."
```

---

## Task 6: The core-owned stream page

`bootTerminalSession` builds a header/slot/timeline/footer layout and then calls back into core for eight things — `renderHeader`, `renderFooter`, `installDashboardAuthRequiredNotice`, `renderTimeline`, `appendTimelineEvents`, `openTail`, `getEvents`, `tailWebSocketUrl`. The only plugin-owned code is `mountTerminalView` and `view.feedEvents(...)`.

This task builds the generic version of that: core does all eight, the plugin does one.

**`session-terminal.ts` is not modified.** Terminal moves onto this in step 5, when it becomes an external plugin. Until then core carries both, deliberately.

**Files:**
- Create: `packages/octowright-frontend/src/session-stream.ts`
- Test: `packages/octowright-frontend/src/session-stream.test.ts`

**Interfaces:**
- Consumes: `getEvents`, `tailWebSocketUrl` from `./api.js`; `installDashboardAuthRequiredNotice`, `renderFooter`, `renderHeader` from `./session.js`; `openTail` from `./tail.js`; `renderTimeline`, `appendTimelineEvents` from `./timeline.js`; `mountFallbackStream` (Task 4).
- Produces: `bootStreamSession(root, sessionId, detail, mount, opts?) -> Promise<void>`; `importRenderer(moduleUrl) -> Promise<{mountStream: MountStream} | FallbackReason>` — wraps the dynamic import so a 404 or syntax error becomes `code: "import-failed"` rather than an unhandled rejection. Task 7 consumes it; it lives here because it belongs with the other failure handling.

- [ ] **Step 1: Write the failing tests**

Create `packages/octowright-frontend/src/session-stream.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";

import { bootStreamSession } from "./session-stream.js";
import type { StreamHandle } from "./plugin-contract.js";

vi.mock("./api.js", () => ({
  getEvents: vi.fn().mockResolvedValue({
    events: [{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }],
    cursor: 42,
  }),
  tailWebSocketUrl: vi.fn().mockReturnValue("ws://x/tail"),
}));

const detail = { kind: "refkind", live: false, id: "s1" } as never;

function recordingMount() {
  const fed: unknown[][] = [];
  let destroyed = 0;
  const mount = vi.fn(
    (): StreamHandle => ({
      feed: (events) => fed.push(events),
      destroy: () => {
        destroyed += 1;
      },
    }),
  );
  return { mount, fed, destroyed: () => destroyed };
}

describe("bootStreamSession", () => {
  it("gives the plugin a mount element and feeds it recorded history", async () => {
    const root = document.createElement("div");
    const { mount, fed } = recordingMount();
    await bootStreamSession(root, "s1", detail, mount);
    expect(mount).toHaveBeenCalledOnce();
    expect(fed[0]).toEqual([{ ts: "2026-08-24T00:00:00Z", action: "ref_ready" }]);
  });

  it("renders core's chrome, not the plugin's", async () => {
    const root = document.createElement("div");
    const { mount } = recordingMount();
    await bootStreamSession(root, "s1", detail, mount);
    expect(root.querySelector('[data-testid="session-header"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="session-timeline"]')).not.toBeNull();
    expect(root.querySelector('[data-testid="session-footer"]')).not.toBeNull();
  });

  it("awaits an async mountStream before the first feed", async () => {
    const root = document.createElement("div");
    const order: string[] = [];
    const mount = vi.fn(async () => {
      await Promise.resolve();
      order.push("mounted");
      return { feed: () => order.push("fed"), destroy: () => {} };
    });
    await bootStreamSession(root, "s1", detail, mount);
    expect(order).toEqual(["mounted", "fed"]);
  });

  it("falls back with a reason when mountStream throws", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(() => {
      throw new TypeError("x is not a function");
    });
    await bootStreamSession(root, "s1", detail, mount);
    const notice = root.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice).not.toBeNull();
    expect(notice?.getAttribute("data-fallback-code")).toBe("mount-failed");
    expect(notice?.textContent).toContain("x is not a function");
  });

  it("falls back when an async mountStream rejects, and destroys nothing", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(async () => {
      throw new Error("async boom");
    });
    await bootStreamSession(root, "s1", detail, mount);
    expect(root.querySelector('[data-testid="stream-fallback-notice"]')).not.toBeNull();
  });

  it("a feed that throws switches to the fallback rather than breaking the page", async () => {
    const root = document.createElement("div");
    const mount = vi.fn(
      (): StreamHandle => ({
        feed: () => {
          throw new Error("feed exploded");
        },
        destroy: () => {},
      }),
    );
    await bootStreamSession(root, "s1", detail, mount);
    const notice = root.querySelector('[data-testid="stream-fallback-notice"]');
    expect(notice?.getAttribute("data-fallback-code")).toBe("mount-failed");
  });
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd packages/octowright-frontend && npx vitest run src/session-stream.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the boot path**

Create `packages/octowright-frontend/src/session-stream.ts`. Model the layout and wiring on `session-terminal.ts` — read it first — but with these differences:

- It takes a `MountStream` parameter instead of importing a terminal view.
- It awaits `mount(...)` before the first `feed`.
- `mount`, and every `feed`, is wrapped so a throw switches the pane to `mountFallbackStream` with `code: "mount-failed"` and the error's message as `detail`, rather than propagating.
- `destroy` is called on teardown only when a handle was actually obtained.

Keep the existing tail ordering exactly: start the tail **after** the history cursor so the first WS frame does not replay deltas already fed.

- [ ] **Step 4: Run to verify they pass**

Run: `cd packages/octowright-frontend && npx vitest run src/session-stream.test.ts`
Expected: all pass.

- [ ] **Step 5: Confirm terminal is untouched**

Run: `git diff --stat -- packages/octowright-frontend/src/session-terminal.ts`
Expected: empty. If it is not, you have migrated terminal — revert that; it is step 5's job.

- [ ] **Step 6: Commit**

```bash
git add packages/octowright-frontend/src/session-stream.ts \
        packages/octowright-frontend/src/session-stream.test.ts
git commit -m "feat(frontend): core-owned stream page calling one plugin function

bootTerminalSession builds the chrome and then calls back into core for eight
things -- header, footer, auth notice, timeline render and append, tail,
getEvents, tail url -- with only mountTerminalView and feedEvents actually
terminal-specific. This is the generic version: core does all eight, the
plugin does one.

Every plugin call is wrapped. A mountStream that throws or rejects yields no
handle, so there is nothing to destroy and the pane switches to the fallback;
a feed that throws does the same rather than breaking the page.

session-terminal.ts is deliberately untouched -- terminal moves onto this in
the extraction step, and migrating it now would rewrite it twice."
```

---

## Task 7: Registry-driven dispatch

`session.ts:661` branches `if (detail.kind === "terminal")` and statically names the terminal module. It becomes a registry lookup plus a dynamic import of whatever `moduleUrl` the kind advertises.

**Terminal keeps its branch**, checked first, until step 5.

**Files:**
- Modify: `packages/octowright-frontend/src/session.ts`
- Test: `packages/octowright-frontend/src/session-stream.test.ts` (extend)

**Interfaces:**
- Consumes: `loadPluginRegistry`, `resolveRenderer` (Task 5); `bootStreamSession` (Task 6); `mountFallbackStream` (Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `packages/octowright-frontend/src/session-stream.test.ts` a describe block covering the dispatch decision itself, exercised through a small exported helper rather than the whole page boot:

```ts
describe("renderer dispatch", () => {
  it("imports the advertised module for a registered kind", async () => {
    // The import is dynamic, so the test asserts the URL the dispatcher chose
    // rather than mocking the module system.
    const { chooseRenderer } = await import("./session-stream.js");
    const reg = new Map([
      [
        "refkind",
        {
          moduleUrl: "/plugins/p/renderer.js",
          rendererApiVersion: 1,
          displayName: "Ref",
          layout: "stream" as const,
        },
      ],
    ]);
    expect(chooseRenderer(reg, "refkind")).toEqual({ moduleUrl: "/plugins/p/renderer.js", layout: "stream" });
  });

  it("chooses the fallback reason for an unregistered kind", async () => {
    const { chooseRenderer } = await import("./session-stream.js");
    expect(chooseRenderer(new Map(), "nosuch")).toMatchObject({ code: "no-frontend" });
  });
});
```

If `chooseRenderer` is simply `resolveRenderer` re-exported, say so in your report and drop this block rather than testing the same function twice under two names.

- [ ] **Step 2: Run to verify they fail**

Run: `cd packages/octowright-frontend && npx vitest run src/session-stream.test.ts`

- [ ] **Step 3: Wire the dispatch**

In `session.ts`, replace the hardcoded branch with:

```ts
  if (detail.kind === "terminal") {
    // Terminal still ships inside core; it moves onto the plugin path in the
    // extraction step. Checked first so its behaviour is unchanged -- including
    // the opts passthrough and the completion log, both of which the existing
    // branch has and which tests depend on.
    const { bootTerminalSession } = await import("./session-terminal.js");
    await bootTerminalSession(root, sessionId, detail, {
      ...(opts.webSocketCtor ? { webSocketCtor: opts.webSocketCtor } : {}),
    });
    log.info({ event: "session_boot_complete", session_id: sessionId, kind: "terminal" });
    return;
  }

  const registry = await loadPluginRegistry();
  const chosen = resolveRenderer(registry, detail.kind);
  if (!("code" in chosen)) {
    const { bootStreamSession, importRenderer } = await import("./session-stream.js");
    const mod = await importRenderer(chosen.moduleUrl);
    const mount =
      "code" in mod
        ? (el: HTMLElement, ctx: StreamContext) => mountFallbackStream(el, ctx, mod)
        : mod.mountStream;
    await bootStreamSession(root, sessionId, detail, mount);
    return;
  }
  if (!BROWSER_KINDS.has(detail.kind)) {
    // A non-browser kind we cannot render: the fallback, with its reason.
    const { bootStreamSession } = await import("./session-stream.js");
    await bootStreamSession(root, sessionId, detail, (el, ctx) =>
      mountFallbackStream(el, ctx, chosen),
    );
    return;
  }
  // A browser kind: fall through to the existing browser page below, unchanged.
```

`importRenderer` comes from Task 6 — it wraps the dynamic import so a 404 or syntax error becomes a `FallbackReason` with `code: "import-failed"` rather than an unhandled rejection.

**`BROWSER_KINDS` does not exist yet and you must add it.** `types.ts` has a `Kind` *type* union (`"chromium" | "firefox" | "webkit" | "terminal"`) but no runtime value, and a type cannot be tested against at runtime. Add a `const BROWSER_KINDS: ReadonlySet<string> = new Set(["chromium", "firefox", "webkit"])` in `session.ts` beside the dispatch, deriving it from the `Kind` union is not possible without a codegen step — so add a comment saying the two must stay in step, and note in your report that this is a hand-maintained mirror.

Read the surrounding function before writing: the existing browser path must remain the default for browser kinds, and the early `return` shape must match what is already there.

- [ ] **Step 4: Run the full frontend suite**

Run: `cd packages/octowright-frontend && npm run test`
Expected: all pass, including the 34 pre-existing files.

- [ ] **Step 5: Commit**

```bash
git add packages/octowright-frontend/src/session.ts \
        packages/octowright-frontend/src/session-stream.ts \
        packages/octowright-frontend/src/session-stream.test.ts
git commit -m "feat(frontend): dispatch session renderers through the registry

session.ts named the terminal module statically. It now looks the kind up in
/api/plugins and dynamically imports whatever that kind advertises, with an
import failure becoming a visible fallback rather than an unhandled
rejection.

Terminal keeps its branch, checked first, until the extraction step."
```

---

## Task 8: Types cleanup

Spec §8.8 says `connector_type` and the telnet member leave `types.ts` **with the plugin**. Terminal does not become a plugin until step 5, so most of this is step 5's.

**Verified against the tree before scoping this task:**
- `telnet` is not in the `Kind` union at all — `types.ts:1` is `"chromium" | "firefox" | "webkit" | "terminal"`. It lives inside `connector_type`'s own union at `types.ts:72`, so it leaves when that field does.
- `connector_type` is still **used** by terminal's own tests (`session-terminal.test.ts:66,108`, `terminal-view.test.ts:57`). Removing it now breaks them, and terminal is still core's until step 5.

So this task removes only the genuinely dead one: `ScenarioParticipant.connector_type` in `src/octowright/mcp_types.py`, declared and populated by nothing since the options collapse (confirmed during step 3's review). The frontend field stays until terminal leaves.

**Files:**
- Modify: `src/octowright/mcp_types.py`

- [ ] **Step 1: Confirm the field is genuinely dead**

Run: `grep -rn "connector_type" src/ tests/ | grep -v "octowright/terminal\|test_scenarios_terminal\|scenarios.py"`

Expect the only `ScenarioParticipant` hit to be its declaration. If anything **writes** it, stop and report — the step-3 review's finding that it is unpopulated would then be wrong, and removing it would be a behaviour change rather than a cleanup.

- [ ] **Step 2: Remove it**

Delete the `connector_type` line from `ScenarioParticipant` in `src/octowright/mcp_types.py`. `total=False` already, so nothing else changes.

- [ ] **Step 3: Verify**

From the repo root: `uv run --active pytest -k "mcp_types or scenario" --no-cov`, and confirm `packages/octowright-frontend/src/types.ts` is **untouched** (`git diff --stat -- packages/octowright-frontend/src/types.ts` empty). Touching it would break terminal's own tests, which is step 5's problem to solve when terminal actually leaves.

- [ ] **Step 4: Commit**

```bash
git add src/octowright/mcp_types.py
git commit -m "refactor(types): drop the dead ScenarioParticipant.connector_type

Declared and populated by nothing since the options collapse moved terminal's
settings under a free-form mapping.

The frontend's connector_type stays for now: spec 8.8 has it leaving with the
plugin, and terminal is still core's until the extraction step -- its own
tests still read the field, so removing it here would break them to no
purpose. telnet is not in the Kind union at all; it lives inside
connector_type's own union and leaves with it."
```

---

## Task 9: The reference plugin grows a renderer

The in-repo consumer that fails CI when the contract drifts. It grows the smallest real renderer: a `mountStream` that appends each event's `action` to its element, enough to prove mount → feed → destroy end to end without pulling a UI library.

**Files:**
- Create: `tests/plugins/reference/assets/renderer.js`
- Modify: `tests/plugins/reference/plugin.py`
- Test: `tests/plugins/test_reference_frontend.py`

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_reference_frontend.py` asserting: the reference plugin declares a `FrontendAsset`; its `asset_dir` exists and contains `module_path`; `GET /plugins/<name>/<module_path>` serves it; and `GET /api/plugins` lists its kind with a matching `moduleUrl` and `rendererApiVersion`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --active pytest tests/plugins/test_reference_frontend.py -v --no-cov`
Expected: FAIL — `plugin.frontend` is `None`.

- [ ] **Step 3: Give the reference plugin a renderer**

Create `tests/plugins/reference/assets/renderer.js` exporting `mountStream(el, ctx)` returning `{feed, destroy}`, and declare the `FrontendAsset` on `ReferencePlugin` with `renderer_api_version=1`, `layout="stream"`, `module_path="renderer.js"`, and `asset_dir` pointing at that directory.

- [ ] **Step 4: Run every gate**

From the repo root, in the foreground:
- `uv run --active pytest tests/plugins --no-cov`
- `uv run --active pytest -m "not live_browser and not memory_isolated"` (no `--no-cov`)
- `cd packages/octowright-frontend && npm run test`
- `make lint` — report the exit code explicitly.

`make lint` covers the whole tree and may surface issues from any task on this branch. Fix what is genuinely broken; never satisfy a gate by weakening it.

- [ ] **Step 5: Commit**

```bash
git add tests/plugins/reference/assets/renderer.js tests/plugins/reference/plugin.py \
        tests/plugins/test_reference_frontend.py
git commit -m "test(plugins): reference plugin grows a renderer

The in-repo consumer that fails CI when the contract drifts now exercises the
frontend seam end to end: a declared FrontendAsset, an asset served through
the containment route, and a kind listed at /api/plugins. The renderer itself
is deliberately minimal -- mount, feed, destroy -- so it proves the contract
rather than a UI library."
```

---

## Done criteria

- `uv run --active pytest -m "not live_browser and not memory_isolated"` green.
- `cd packages/octowright-frontend && npm run test` green.
- `make lint` exit 0.
- A traversing or symlinked asset path is refused; a legitimate nested asset is served.
- Every fallback trigger renders a visible reason naming the kind.
- `session-terminal.ts` unchanged — terminal still renders through its own path.
- `octowright_status()["plugins"]` unchanged from step 3 on a default install.
- No push, no PR, no `CHANGELOG.md` edit, no baseline edit.

## Not in this step

- Deleting terminal from core and standing up `octowright-terminal`, including migrating its renderer onto `mountStream` (step 5).
- `layout: "browser"` — the value exists in `FrontendAsset` and is reported by `/api/plugins`, but core's browser page is not yet pluggable. Only `"stream"` is wired. A plugin declaring `"browser"` gets the fallback, which is honest rather than silently wrong.
- **`connector_type` leaving `packages/octowright-frontend/src/types.ts`** (spec §8.8). It leaves *with the plugin*, and terminal is still core's — its own tests read the field. Removing it here would break them for no gain. Step 5.

## Carried-forward findings

Open from steps 1–3; none blocks this step.

- `activate`'s core-tool-collision branch does not run the `on_rollback` hook, so a plugin refused there keeps its capability profile registered.
- `recording_truncated` is in `CONTROL_ACTIONS` but `_write_truncation_marker` writes it directly, so that member has no writer through `record_control`.
- `record_control` duplicates `record`'s encode/write/flush sequence.
- `SessionLaunch.commit()` does not compare `record.label`/`record.profile` against what `begin_session` received.
- Two plugins registering the same capability-profile name is silent last-write-wins.
- `DuplicatePluginNameError` is constructed but never raised.
- `PluginRegistry.maybe_get` has no production caller since `find_plugin_session` became the wired resolver — retire one.
- No HTTP-level test drives `DELETE /api/sessions/{id}` against a registered plugin session.
- `_apply_fixtures` no longer performs the incidental liveness probe the pre-step-3 code did as a side effect; deliberate, recorded.

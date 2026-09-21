# Atomic File Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-class `browser_upload_files` operation that captures a Playwright file chooser before clicking its trigger, assigns validated files, and remains atomic through recording, replay, and export.

**Architecture:** Keep `browser_set_input_files` as the direct-input primitive and add `BrowserSession.upload_files` for trigger-driven uploads. The new session method validates paths first, resolves one CSS or semantic trigger, wraps its click in `page.expect_file_chooser`, records one `upload_files` action, and is exposed through a new MCP tool. Macro and export layers treat `upload_files` as a distinct action so no generated workflow decomposes it into the broken click-then-assignment sequence.

**Tech Stack:** Python 3.11, Playwright async API, MCP 2.0 decorators, pytest/pytest-anyio, Ruff/mypy/ty, PlantUML documentation guards.

---

## File map

- `src/octowright/session/core_interaction_mixin.py`: session-level atomic chooser operation and defensive validation for direct macro callers.
- `src/octowright/server/browser/input.py`: MCP input validation, tool description, response outline, and direct-input guidance.
- `src/octowright/server/browser/__init__.py`, `src/octowright/server/__init__.py`: public re-exports.
- `src/octowright/types.py`: typed `UploadFilesAction` macro shape.
- `src/octowright/macros/runtime.py`, `src/octowright/macros/lint.py`: replay dispatch and static validation.
- `src/octowright/export.py`, `src/octowright/export_ts.py`: Python and TypeScript recording exporters.
- `src/octowright/artifacts/script_export_actions.py`: standalone artifact runner implementation.
- `tests/test_uploads.py`, `tests/test_session_interaction_mixin_branches.py`: chooser ordering, validation, recording, and failures.
- `tests/test_server_browser_input_tools.py`: MCP surface and forwarding.
- `tests/test_macro_runtime_branches.py`, `tests/test_macro_lint_helpers.py`: replay and lint contract.
- `tests/test_export.py`, `tests/macro_lint/test_cli_export_execution.py`: generated script behavior.
- `tests/test_atomic_upload_live.py`: real-browser upload, follow-up interaction, and close lifecycle.
- `README.md`, `CHANGELOG.md`, `docs/architecture/mcp-tool-inventory.md`, `docs/architecture/mcp-tool-surface.puml`, `docs/architecture/mcp-tool-surface.svg`: public inventory and release documentation.

### Task 1: Session-level atomic chooser behavior

**Files:**
- Modify: `tests/test_uploads.py`
- Modify: `tests/test_session_interaction_mixin_branches.py`
- Modify: `src/octowright/session/core_interaction_mixin.py`

- [ ] **Step 1: Write failing tests for listener ordering, assignment, and the single recorded action**

Add fakes to `tests/test_uploads.py` that expose the order of chooser registration, trigger click, and file assignment:

```python
class FakeChooser:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    async def set_files(self, paths: list[str], *, timeout: int) -> None:
        self.events.append(("set_files", paths, timeout))


class FakeChooserInfo:
    def __init__(self, chooser: FakeChooser) -> None:
        self.value = self._value(chooser)

    @staticmethod
    async def _value(chooser: FakeChooser) -> FakeChooser:
        return chooser


class FakeChooserContext:
    def __init__(self, events: list[object], chooser: FakeChooser) -> None:
        self.events = events
        self.info = FakeChooserInfo(chooser)

    async def __aenter__(self) -> FakeChooserInfo:
        self.events.append("chooser_armed")
        return self.info

    async def __aexit__(self, *_exc: object) -> None:
        self.events.append("chooser_captured")


class FakeTrigger:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    async def click(self, *, timeout: int) -> None:
        self.events.append(("click", timeout))
```

Extend `FakePage` with `expect_file_chooser`, `locator`, and an `events` list, then add:

```python
@pytest.mark.anyio
async def test_upload_files_arms_chooser_before_click_and_records_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    session = _make_session(tmp_path)
    upload = tmp_path / "report.pdf"
    upload.write_bytes(b"pdf")

    result = await session.upload_files(
        paths=[str(upload)], selector="#upload", timeout_ms=1234
    )

    assert session.page.events == [
        ("expect_file_chooser", 1234),
        "chooser_armed",
        ("locator", "#upload"),
        ("click", 1234),
        "chooser_captured",
        ("set_files", [str(upload)], 1234),
    ]
    assert result == {
        "ok": True,
        "paths": [str(upload)],
        "selector": "#upload",
    }
    rows = [json.loads(line) for line in session.log_path.read_text().splitlines()]
    upload_rows = [row for row in rows if row["action"] == "upload_files"]
    assert upload_rows == [
        {
            "ts": upload_rows[0]["ts"],
            "action": "upload_files",
            "paths": [str(upload)],
            "selector": "#upload",
        }
    ]
    assert not any(row["action"] in {"click", "click_by", "set_input_files"} for row in rows)
```

- [ ] **Step 2: Run the new test to verify RED**

Run:

```bash
uv run pytest tests/test_uploads.py::test_upload_files_arms_chooser_before_click_and_records_once -v
```

Expected: FAIL with `AttributeError: 'BrowserSession' object has no attribute 'upload_files'`.

- [ ] **Step 3: Add failing validation and semantic-locator tests**

In `tests/test_session_interaction_mixin_branches.py`, add tests that:

```python
@pytest.mark.anyio
async def test_upload_files_validates_path_before_resolving_trigger(
    self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", allowed)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    page = MagicMock()
    page.url = "about:blank"
    page.expect_file_chooser = MagicMock()
    session = _make_session(tmp_path, page=page)

    with pytest.raises(ValueError, match="outside the allowed roots"):
        await session.upload_files(paths=[str(outside)], selector="#upload")

    page.expect_file_chooser.assert_not_called()


@pytest.mark.anyio
async def test_upload_files_uses_semantic_trigger_and_exact_flags(
    self, tmp_path: Path, _allow_tmp_uploads: Path
) -> None:
    upload = tmp_path / "report.txt"
    upload.write_text("report")
    page, locator, chooser = _chooser_page_fakes()
    session = _make_session(tmp_path, page=page)
    session._locator = AsyncMock(return_value=locator)

    await session.upload_files(
        paths=[str(upload)],
        role="button",
        role_name="Upload files",
        role_exact=True,
    )

    session._locator.assert_awaited_once_with(
        role="button",
        role_name="Upload files",
        role_exact=True,
        label=None,
        label_exact=False,
        text=None,
        text_exact=False,
        test_id=None,
    )
    chooser.set_files.assert_awaited_once_with(
        [str(upload)], timeout=DEFAULT_ACTION_TIMEOUT_MS
    )
```

Parametrize defensive rejections for no locator, selector plus a semantic locator, `role_name` without `role`, and an exact flag without its finder. Assert `expect_file_chooser` is never called. Add a chooser assignment failure test asserting no `upload_files` row is written.

- [ ] **Step 4: Run the session tests to verify RED**

Run:

```bash
uv run pytest tests/test_uploads.py tests/test_session_interaction_mixin_branches.py -k 'upload_files' -v
```

Expected: all new cases FAIL because `upload_files` is absent.

- [ ] **Step 5: Implement the minimal session method**

In `src/octowright/session/core_interaction_mixin.py`, import `DEFAULT_ACTION_TIMEOUT_MS` and add:

```python
@gated_operation("browser_upload_files")
async def upload_files(
    self,
    *,
    paths: list[str],
    selector: str | None = None,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    label_exact: bool = False,
    text: str | None = None,
    text_exact: bool = False,
    test_id: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    from octowright.session.upload_paths import validate_upload_path

    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list of file paths")
    validated = [str(validate_upload_path(path)) for path in paths]
    semantic = {"role": role, "label": label, "text": text, "test_id": test_id}
    provided = [name for name, value in semantic.items() if value is not None]
    if bool(selector) + len(provided) != 1:
        raise ValueError("provide exactly one upload trigger: selector or role/label/text/test_id")
    if role_name is not None and role is None:
        raise ValueError("role_name requires role")
    if role_exact and role is None:
        raise ValueError("role_exact requires role")
    if label_exact and label is None:
        raise ValueError("label_exact requires label")
    if text_exact and text is None:
        raise ValueError("text_exact requires text")

    timeout = timeout_ms or DEFAULT_ACTION_TIMEOUT_MS
    finders = {
        "role": role,
        "role_name": role_name,
        "role_exact": role_exact,
        "label": label,
        "label_exact": label_exact,
        "text": text,
        "text_exact": text_exact,
        "test_id": test_id,
    }
    locator = self._target().locator(selector) if selector else await self._locator(**finders)
    async with self.page.expect_file_chooser(timeout=timeout) as chooser_info:
        await locator.click(timeout=timeout)
    chooser = await chooser_info.value
    await chooser.set_files(validated, timeout=timeout)

    recorded = {key: value for key, value in finders.items() if value not in (None, False)}
    if selector:
        recorded["selector"] = selector
    self.recorder.record("upload_files", paths=validated, **recorded)
    return {"ok": True, "paths": validated, **recorded}
```

- [ ] **Step 6: Run the session tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_uploads.py tests/test_session_interaction_mixin_branches.py -k 'upload_files or set_input_files' -v
```

Expected: PASS, including the pre-existing direct-input cases.

- [ ] **Step 7: Commit the session behavior**

```bash
git add src/octowright/session/core_interaction_mixin.py tests/test_uploads.py tests/test_session_interaction_mixin_branches.py
git commit -m "feat: add atomic session file upload"
```

### Task 2: MCP tool and public exports

**Files:**
- Modify: `tests/test_server_browser_input_tools.py`
- Modify: `src/octowright/server/browser/input.py`
- Modify: `src/octowright/server/browser/__init__.py`
- Modify: `src/octowright/server/__init__.py`

- [ ] **Step 1: Write failing MCP forwarding and validation tests**

Add tests that call:

```python
out = await _input.browser_upload_files(
    "i",
    ["/tmp/report.pdf"],
    role="button",
    role_name="Upload files",
    role_exact=True,
    timeout_ms=2500,
    response_mode="outline",
)
```

Assert `session.upload_files` receives every field by keyword, the result retains `ok`, `paths`, and the locator, and `browser_page_outline("i")` is included. Parametrize invalid calls for empty paths, no locator, multiple locators, and modifier-only locators; assert the pool is never consulted. Extend the description test to require both `browser_upload_files` and `browser_set_input_files` to warn against a preceding trigger click.

- [ ] **Step 2: Run the MCP tests to verify RED**

Run:

```bash
uv run pytest tests/test_server_browser_input_tools.py -k 'upload_files or descriptions' -v
```

Expected: FAIL because `browser_upload_files` is not registered or exported.

- [ ] **Step 3: Implement and export `browser_upload_files`**

Add the MCP tool beside `browser_set_input_files` in `input.py` with the approved signature. Perform structural input checks before `browser_operation`, then call:

```python
result = await session.upload_files(
    paths=paths,
    selector=selector,
    role=role,
    role_name=role_name,
    role_exact=role_exact,
    label=label,
    label_exact=label_exact,
    text=text,
    text_exact=text_exact,
    test_id=test_id,
    timeout_ms=timeout_ms,
)
return await _with_outline(instance_id, dict(result), response_mode)
```

Describe it as the only tool to use after identifying a visible upload trigger. Update `browser_set_input_files` to say it targets the input directly and must not follow a trigger click. Re-export the new function from both package `__init__.py` files.

- [ ] **Step 4: Run MCP and registry tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_server_browser_input_tools.py tests/test_tool_profiles.py -v
```

Expected: PASS. The new tool remains all-only, matching the existing direct-input tool's capability placement.

- [ ] **Step 5: Commit the MCP surface**

```bash
git add src/octowright/server/browser/input.py src/octowright/server/browser/__init__.py src/octowright/server/__init__.py tests/test_server_browser_input_tools.py
git commit -m "feat: expose atomic browser file upload"
```

### Task 3: Macro type, lint, and replay support

**Files:**
- Modify: `src/octowright/types.py`
- Modify: `src/octowright/macros/runtime.py`
- Modify: `src/octowright/macros/lint.py`
- Modify: `tests/test_macro_runtime_branches.py`
- Modify: `tests/test_macro_lint_helpers.py`

- [ ] **Step 1: Write failing replay and lint tests**

Add a runtime test:

```python
@pytest.mark.anyio
async def test_upload_files_replays_as_one_atomic_action(self) -> None:
    session = _full_session()
    await _dispatch_via_simple(
        session,
        {
            "action": "upload_files",
            "paths": ["/uploads/report.pdf"],
            "role": "button",
            "role_name": "Upload files",
            "role_exact": True,
        },
    )
    session.upload_files.assert_awaited_once_with(
        paths=["/uploads/report.pdf"],
        role="button",
        role_name="Upload files",
        role_exact=True,
    )
    session.click_by.assert_not_called()
    session.set_input_files.assert_not_called()
```

Add lint cases asserting that `upload_files` is known, requires non-empty `paths`, accepts exactly one trigger locator, and rejects selector-plus-role and modifier-only shapes.

- [ ] **Step 2: Run macro tests to verify RED**

Run:

```bash
uv run pytest tests/test_macro_runtime_branches.py tests/test_macro_lint_helpers.py -k 'upload_files' -v
```

Expected: FAIL because the action is unknown and has no runtime mapping.

- [ ] **Step 3: Add the typed action and runtime mapping**

In `types.py`, define `UploadFilesAction` with `paths`, optional `selector`, and the semantic locator fields, then add it to `MacroAction`. In `runtime.py`, add:

```python
"upload_files": "upload_files",
```

to `_ACTION_MAP`; the existing simple-dispatch keyword forwarding then preserves the recorded locator and paths.

- [ ] **Step 4: Add action-specific lint validation**

Add `"upload_files": ("paths",)` to `_SIMPLE_REQUIRED`, and extend the semantic finder check so both `click_by` and `upload_files` require exactly one finder, while `upload_files` also treats `selector` as a valid mutually-exclusive finder. Validate that `paths` is a non-empty list.

- [ ] **Step 5: Run macro tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_macro_runtime_branches.py tests/test_macro_lint_helpers.py tests/test_macro_lint.py -v
```

Expected: PASS with the new atomic action and all existing actions unchanged.

- [ ] **Step 6: Commit macro support**

```bash
git add src/octowright/types.py src/octowright/macros/runtime.py src/octowright/macros/lint.py tests/test_macro_runtime_branches.py tests/test_macro_lint_helpers.py
git commit -m "feat: replay atomic file uploads"
```

### Task 4: Python, TypeScript, and artifact exporters

**Files:**
- Modify: `src/octowright/export.py`
- Modify: `src/octowright/export_ts.py`
- Modify: `src/octowright/artifacts/script_export_actions.py`
- Modify: `tests/test_export.py`
- Modify: `tests/macro_lint/test_cli_export_coverage.py`
- Modify: `tests/macro_lint/test_cli_export_execution.py`

- [ ] **Step 1: Write failing Python and TypeScript exporter assertions**

Add `upload_files` recording entries using a semantic trigger and assert Python contains the ordered sequence:

```python
async with page.expect_file_chooser() as chooser_info:
    await page.get_by_role('button', name='Upload files', exact=True).click()
chooser = await chooser_info.value
await chooser.set_files(['/uploads/report.pdf'])
```

Assert TypeScript contains:

```typescript
const chooserPromise = page.waitForEvent('filechooser');
await page.getByRole("button", { name: "Upload files", exact: true }).click();
const chooser = await chooserPromise;
await chooser.setFiles(["/uploads/report.pdf"]);
```

Add selector-trigger cases and assert the direct `set_input_files` output remains unchanged.

- [ ] **Step 2: Run exporter tests to verify RED**

Run:

```bash
uv run pytest tests/test_export.py -k 'upload_files' -v
```

Expected: FAIL because both exporters omit the unknown action.

- [ ] **Step 3: Add locator renderers and atomic handlers**

Implement focused `_py_upload_files` and `_ts_upload_files` helpers that render one supported trigger locator and the listener-before-click ordering. Register them under `"upload_files"` in `_PY_HANDLERS` and `_TS_HANDLERS`. Keep `set_input_files` handlers unchanged.

- [ ] **Step 4: Add artifact-script execution support test-first**

Extend the fake page in `tests/macro_lint/test_cli_export_execution.py` with an async file-chooser context and chooser that log `expect_file_chooser`, `click`, and `set_files`. Add `upload_files` to the coverage fixture and assert the generated runner executes those events in that order.

Then add a `"upload_files"` action template in `script_export_actions.py` that uses the same atomic sequence and resolves selector or semantic locators through the runner's existing page/locator helpers.

- [ ] **Step 5: Run all exporter tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_export.py tests/macro_lint/test_cli_export_coverage.py tests/macro_lint/test_cli_export_execution.py -v
```

Expected: PASS; generated Python also compiles and the TypeScript fixture passes its existing syntax checks.

- [ ] **Step 6: Commit exporter support**

```bash
git add src/octowright/export.py src/octowright/export_ts.py src/octowright/artifacts/script_export_actions.py tests/test_export.py tests/macro_lint/test_cli_export_coverage.py tests/macro_lint/test_cli_export_execution.py
git commit -m "feat: export atomic file uploads"
```

### Task 5: Live-browser regression and public documentation

**Files:**
- Create: `tests/test_atomic_upload_live.py`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/architecture/mcp-tool-inventory.md`
- Modify: `docs/architecture/mcp-tool-surface.puml`
- Regenerate: `docs/architecture/mcp-tool-surface.svg`

- [ ] **Step 1: Write the live-browser regression test**

Create a Chromium test that stages one file, launches a local data page containing a hidden file input, visible upload button, status output, and a second button. Call `session.upload_files` by exact role/name, assert the page reports the staged filename, click the second button, and close the browser under the normal timeout:

```python
@pytest.mark.asyncio
@pytest.mark.live_browser
async def test_atomic_upload_allows_followup_interaction_and_clean_close(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    upload = tmp_path / "report.txt"
    upload.write_text("atomic upload")
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    pool = BrowserPool(recordings_dir=tmp_path / "recordings")
    launched = await pool.launch(
        kind="chromium",
        headed=False,
        url=_atomic_upload_data_url(),
    )
    instance_id = launched["instance_id"]
    session = pool.get(instance_id)
    try:
        await session.upload_files(
            paths=[str(upload)],
            role="button",
            role_name="Upload files",
            role_exact=True,
        )
        assert await session.evaluate("document.querySelector('#selected').textContent") == "report.txt"
        await session.click("#after")
        assert await session.evaluate("document.querySelector('#after').textContent") == "worked"
        assert (await pool.close(instance_id))["closed"] is True
    finally:
        await pool.shutdown()
```

- [ ] **Step 2: Run the live test and confirm GREEN**

Run:

```bash
uv run pytest tests/test_atomic_upload_live.py -v
```

Expected: PASS with a successful upload, follow-up click, and clean close.

- [ ] **Step 3: Update the public tool contract and release notes**

Add both upload tools to the README table with the explicit decision rule. Add a 0.25.0 Fixed entry describing the orphaned native chooser and the atomic operation. Add `browser_upload_files` to the all-only inventory, changing all-only from 29 to 30 and the core-install total from 131 to 132 in inventory, README, and PlantUML source.

- [ ] **Step 4: Regenerate the diagram and run documentation guards**

Run:

```bash
make diagrams
uv run python scripts/check_tool_inventory_docs.py
uv run python scripts/check_agent_docs_sync.py
```

Expected: the SVG is regenerated and both guards exit 0 without drift.

- [ ] **Step 5: Commit regression coverage and documentation**

```bash
git add tests/test_atomic_upload_live.py README.md CHANGELOG.md docs/architecture/mcp-tool-inventory.md docs/architecture/mcp-tool-surface.puml docs/architecture/mcp-tool-surface.svg
git commit -m "fix: prevent orphaned native upload choosers"
```

### Task 6: Release-candidate verification

**Files:**
- Verify only; fix failures in the owning task's files and commit the correction separately.

- [ ] **Step 1: Run focused upload, macro, and export coverage**

```bash
uv run pytest tests/test_uploads.py tests/test_session_interaction_mixin_branches.py tests/test_server_browser_input_tools.py tests/test_macro_runtime_branches.py tests/test_macro_lint_helpers.py tests/test_export.py tests/macro_lint/test_cli_export_coverage.py tests/macro_lint/test_cli_export_execution.py tests/test_atomic_upload_live.py -v
```

Expected: PASS.

- [ ] **Step 2: Run the complete core quality gate**

```bash
PIPAPI_PYTHON_LOCATION="$PWD/.venv/bin/python" make ci
```

Expected: lint, type checks, security checks, documentation guards, audit, and the complete core test suite all PASS with coverage above 83%.

- [ ] **Step 3: Run terminal and frontend gates**

```bash
ci/run_terminal_plugin_tests.sh
cd packages/octowright-frontend && npm ci && npm test -- --run && npm run build
```

Expected: terminal plugin tests PASS; frontend tests and production build PASS.

- [ ] **Step 4: Build and inspect release artifacts**

```bash
rm -rf dist packages/octowright-terminal/dist
uv run python -m build
uv run python -m build packages/octowright-terminal
uv run twine check dist/* packages/octowright-terminal/dist/*
uv run python scripts/check_packaged_dashboard_assets.py dist/*.whl
```

Expected: core and terminal wheels/sdists build, Twine validates every artifact, and packaged dashboard assets pass.

- [ ] **Step 5: Verify branch cleanliness and release diff**

```bash
git status --short
git log --oneline origin/main..HEAD
git diff --check origin/main...HEAD
```

Expected: clean status, the atomic-upload commits appear after the existing 0.25.0 release commits, and no whitespace errors are reported.

- [ ] **Step 6: Move to branch-finishing workflow**

Invoke `superpowers:verification-before-completion`, then `superpowers:requesting-code-review`, and finally `superpowers:finishing-a-development-branch`. Push `codex/release-0.25.0`, create the required pull request, attach it to the task, and wait for all protected-branch CI checks before reporting the release candidate merge-ready.

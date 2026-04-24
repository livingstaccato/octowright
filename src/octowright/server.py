from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from provide.telemetry import get_logger

from . import goldens as goldens_mod
from . import macros as macro_mod
from . import profiles as profile_mod
from . import video as video_mod
from .defaults import DEFAULT_ACTION_TIMEOUT_MS, RECORDINGS_DIR
from .export import export_script
from .pool import BrowserPool

log = get_logger(__name__)

pool = BrowserPool()
mcp = FastMCP(
    "octowright",
    instructions=(
        "Launch and drive multiple headed Playwright browsers in parallel. "
        "Each browser has an instance_id; pass it to every per-browser tool. "
        "Every action is recorded to a JSONL log that can be exported as a Playwright script. "
        "Use the `profile` arg on browser_launch to persist cookies/localStorage/IndexedDB across runs."
    ),
)


@mcp.tool(structured_output=False, description=(
    "Launch a browser. kind = 'chromium' | 'firefox' | 'webkit'. "
    "DEFAULT IS HEADED — leave headed=True unless you have a specific "
    "background-verification reason (automated health check, scripted parity "
    "run, CI). If a human will look at the window, stay headed. "
    "If profile is given, uses a persistent on-disk user-data-dir so cookies, "
    "localStorage, and IndexedDB survive close/relaunch (recommended for Discord, "
    "Slack, etc.). Profiles are scoped per-kind: (kind, profile) is the identity. "
    "The window title is prefixed with [profile] (or [label] if no profile) so "
    "parallel instances can be told apart in cmd-\\` and the Window menu. "
    "Pass stabilize=True to freeze Date.now, kill CSS animations, and make "
    "requestAnimationFrame synchronous — recommended for reproducible test runs. "
    "Pass trace=True to record a full Playwright trace (screenshots + snapshots + sources) "
    "for post-mortem debugging. Resulting .zip can be viewed with `npx playwright show-trace`. "
    "Returns instance_id."
))
async def browser_launch(
    kind: str = "chromium",
    url: str | None = None,
    headed: bool = True,
    label: str | None = None,
    viewport_w: int | None = None,
    viewport_h: int | None = None,
    profile: str | None = None,
    stabilize: bool = False,
    record_video: bool = False,
    trace: bool = False,
) -> dict[str, Any]:
    return await pool.launch(
        kind=kind,
        url=url,
        headed=headed,
        label=label,
        viewport_w=viewport_w,
        viewport_h=viewport_h,
        profile=profile,
        stabilize=stabilize,
        record_video=record_video,
        trace=trace,
    )


@mcp.tool(structured_output=False, description="List all live browser instances.")
def browser_list() -> list[dict[str, Any]]:
    return pool.list()


@mcp.tool(structured_output=False, description="Close one browser instance by id.")
async def browser_close(instance_id: str) -> dict[str, Any]:
    return await pool.close(instance_id)


@mcp.tool(structured_output=False, description="Close every live browser instance.")
async def browser_close_all() -> dict[str, Any]:
    return await pool.close_all()


@mcp.tool(structured_output=False, description="Navigate an instance to a URL.")
async def browser_navigate(instance_id: str, url: str) -> dict[str, Any]:
    return await pool.get(instance_id).navigate(url)


@mcp.tool(structured_output=False, description="Click a selector on an instance.")
async def browser_click(instance_id: str, selector: str) -> dict[str, Any]:
    await pool.get(instance_id).click(selector)
    return {"ok": True}


@mcp.tool(structured_output=False, description="Type text into a selector on an instance.")
async def browser_type(
    instance_id: str, selector: str, text: str, delay_ms: int | None = None
) -> dict[str, Any]:
    await pool.get(instance_id).type_text(selector, text, delay_ms)
    return {"ok": True}


@mcp.tool(structured_output=False, description="Fill an input selector with a value (faster than type).")
async def browser_fill(instance_id: str, selector: str, value: str) -> dict[str, Any]:
    await pool.get(instance_id).fill(selector, value)
    return {"ok": True}


@mcp.tool(structured_output=False, description="Press a keyboard key on an instance.")
async def browser_press_key(instance_id: str, key: str) -> dict[str, Any]:
    await pool.get(instance_id).press_key(key)
    return {"ok": True}


@mcp.tool(structured_output=False, description="Screenshot an instance to disk. If path omitted, writes next to the recording.")
async def browser_screenshot(instance_id: str, path: str | None = None) -> dict[str, Any]:
    session = pool.get(instance_id)
    target = Path(path) if path else session.log_path.with_suffix(".png")
    out = await session.screenshot(target)
    return {"path": str(out)}


@mcp.tool(structured_output=False, description="Return the accessibility-tree snapshot for an instance.")
async def browser_snapshot(instance_id: str) -> dict[str, Any]:
    return await pool.get(instance_id).snapshot()


@mcp.tool(structured_output=False, description="Evaluate a JavaScript expression in an instance's page.")
async def browser_evaluate(instance_id: str, expression: str) -> dict[str, Any]:
    result = await pool.get(instance_id).evaluate(expression)
    return {"result": result}


@mcp.tool(structured_output=False, description="Collected console messages since launch.")
def browser_console_messages(instance_id: str) -> list[dict[str, Any]]:
    return list(pool.get(instance_id).console)


@mcp.tool(structured_output=False, description="Wait for a selector, text, or network-idle. Provide one of selector / text, or neither for network-idle.")
async def browser_wait_for(
    instance_id: str,
    selector: str | None = None,
    text: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    await pool.get(instance_id).wait_for(selector, text, timeout_ms)
    return {"ok": True}


@mcp.tool(structured_output=False, description="Path to the JSONL action log for an instance.")
def browser_recording_path(instance_id: str) -> dict[str, Any]:
    return {"path": str(pool.get(instance_id).log_path)}


@mcp.tool(structured_output=False, description="Export a replayable Playwright script (python | ts) from an instance's recording.")
def browser_export_script(
    instance_id: str,
    format: str = "python",
    out_path: str | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    suffix = ".py" if format == "python" else ".ts"
    target = Path(out_path) if out_path else session.log_path.with_suffix(suffix)
    result = export_script(session.log_path, target, fmt=format)
    return {"path": str(result)}


@mcp.tool(structured_output=False, description="List saved browser profiles. Pass kind to filter to one engine.")
def profile_list(kind: str | None = None) -> list[dict[str, Any]]:
    return profile_mod.list_profiles(kind)


@mcp.tool(structured_output=False, description=(
    "Delete a saved browser profile (wipes all cookies, localStorage, IndexedDB, and "
    "saved logins for that profile). Refuses if a live instance is using it."
))
def profile_delete(kind: str, name: str) -> dict[str, Any]:
    if pool.profile_in_use(kind, name):
        log.warning("octowright.profile.delete_refused", kind=kind, profile=name, reason="in_use")
        raise RuntimeError(f"profile {kind}/{name} is in use by a live browser; close it first")
    path = profile_mod.delete_profile(kind, name)
    log.info("octowright.profile.deleted", kind=kind, profile=name, path=str(path))
    return {"deleted": True, "path": str(path)}


@mcp.tool(structured_output=False, description=(
    "Save the current recording of a live instance as a named, reusable macro. "
    "`parameters` is a dict mapping parameter NAME to its literal VALUE in this "
    "recording — those values get replaced by {{name}} placeholders in the saved "
    "macro. Example: parameters={\"email\":\"me@example.com\",\"password\":\"hunter2\"}. "
    "Drops launch/close/snapshot entries by default. Returns the saved macro path."
))
def macro_save(
    instance_id: str,
    name: str,
    description: str | None = None,
    parameters: dict[str, str] | None = None,
    include_launch: bool = False,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    path = macro_mod.save_macro(
        recording_path=session.log_path,
        name=name,
        description=description,
        parameters=parameters,
        include_launch=include_launch,
    )
    return {"saved": True, "name": name, "path": str(path)}


@mcp.tool(structured_output=False, description="List saved macros with their parameters and metadata.")
def macro_list() -> list[dict[str, Any]]:
    return macro_mod.list_macros()


@mcp.tool(structured_output=False, description=(
    "Replay a saved macro against a live browser instance. `args` supplies values "
    "for any {{placeholders}} the macro declares. Lifecycle actions (launch, close, "
    "snapshot) are skipped. Returns {macro, executed, skipped, args_used}."
))
async def macro_run(
    instance_id: str,
    name: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    return await macro_mod.run_macro(session=session, name=name, args=args)


@mcp.tool(structured_output=False, description="Delete a saved macro by name. Raises if the macro does not exist.")
def macro_delete(name: str) -> dict[str, Any]:
    path = macro_mod.delete_macro(name)
    return {"deleted": True, "name": name, "path": str(path)}


@mcp.tool(structured_output=False, description=(
    "Replay several saved macros in order against one live instance. "
    "`names` is the list of macro names; `args_list[i]` supplies args for `names[i]`. "
    "By default a failing step aborts the chain (stop_on_failure=True); pass False "
    "to keep going and collect per-step outcomes."
))
async def macro_run_sequence(
    instance_id: str,
    names: list[str],
    args_list: list[dict[str, Any]] | None = None,
    stop_on_failure: bool = True,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    return await macro_mod.run_sequence(
        session=session,
        names=names,
        args_list=args_list,
        stop_on_failure=stop_on_failure,
    )


@mcp.tool(structured_output=False, description=(
    "Assert the page URL matches `pattern`. `pattern` is a regex by default; "
    "pass mode='equals' for exact match or mode='contains' for substring."
))
async def browser_expect_url(
    instance_id: str, pattern: str, mode: str = "regex",
) -> dict[str, Any]:
    session = pool.get(instance_id)
    actual = await macro_mod._check_url(session.page, pattern, mode)
    session.recorder.record("expect_url", pattern=pattern, mode=mode)
    return {"ok": True, "url": actual}


@mcp.tool(structured_output=False, description=(
    "Assert an element matching `selector` contains `text`. "
    "mode: 'contains' (default), 'equals', or 'regex'. `timeout_ms` controls how long "
    "to poll while waiting for the element (default 5000)."
))
async def browser_expect_text(
    instance_id: str, selector: str, text: str,
    mode: str = "contains", timeout_ms: int | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    actual = await macro_mod._check_text(session.page, selector, text, mode, timeout_ms)
    session.recorder.record("expect_text", selector=selector, text=text, mode=mode, timeout_ms=timeout_ms)
    return {"ok": True, "text": actual}


@mcp.tool(structured_output=False, description=(
    "Assert that at least one element matching `selector` exists (or not, if present=False). "
    "Waits up to `timeout_ms` for the condition."
))
async def browser_expect_selector(
    instance_id: str, selector: str, present: bool = True,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    await macro_mod._check_selector(session.page, selector, present, timeout_ms)
    session.recorder.record("expect_selector", selector=selector, present=present, timeout_ms=timeout_ms)
    return {"ok": True, "selector": selector, "present": present}


@mcp.tool(structured_output=False, description=(
    "Assert a JavaScript expression evaluates to a truthy value (or equals `equals` "
    "if supplied). The expression runs in the page, like browser_evaluate."
))
async def browser_expect_js(
    instance_id: str, expression: str, equals: Any = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    result = await macro_mod._check_js(session.page, expression, equals)
    session.recorder.record("expect_js", expression=expression, equals=equals)
    return {"ok": True, "result": result}


@mcp.tool(structured_output=False, description=(
    "Save the current page's accessibility tree as a named golden snapshot. "
    "Later calls to golden_assert will compare the live tree against this one."
))
async def golden_save(
    instance_id: str, name: str, description: str | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    tree = await session.snapshot()
    url = session.page.url
    path = goldens_mod.save_golden(name=name, tree=tree, url=url, description=description)
    return {"saved": True, "name": name, "path": str(path)}


@mcp.tool(structured_output=False, description=(
    "Compare the current page's accessibility tree against a saved golden. "
    "Raises RuntimeError with a diff summary on mismatch."
))
async def golden_assert(instance_id: str, name: str) -> dict[str, Any]:
    session = pool.get(instance_id)
    actual = await session.snapshot()
    expected = goldens_mod.load_golden(name)["tree"]
    diffs = goldens_mod.diff_trees(expected, actual)
    if diffs:
        raise RuntimeError({"golden": name, "diffs": diffs[:20], "diff_count": len(diffs)})
    return {"ok": True, "diffs": 0}


@mcp.tool(structured_output=False, description="List saved goldens.")
def golden_list() -> list[dict[str, Any]]:
    return goldens_mod.list_goldens()


@mcp.tool(structured_output=False, description="Delete a saved golden by name.")
def golden_delete(name: str) -> dict[str, Any]:
    path = goldens_mod.delete_golden(name)
    return {"deleted": True, "name": name, "path": str(path)}


@mcp.tool(structured_output=False, description=(
    "Return the path to the video file recorded for an instance. "
    "Only populated after the instance is closed (Playwright finalises the file on close)."
))
def browser_video_path(instance_id: str) -> dict[str, Any]:
    session = pool.get(instance_id)
    return {"video_path": str(session.video_path) if session.video_path else None}


@mcp.tool(structured_output=False, description=(
    "Extract frames from a recorded video via ffmpeg. Supply exactly one of fps (frames/second) "
    "or at_times (list of second-timestamps). Frames land in out_dir (default: next to the video)."
))
def browser_extract_frames(
    video_path: str,
    out_dir: str | None = None,
    fps: float | None = None,
    at_times: list[float] | None = None,
) -> dict[str, Any]:
    from pathlib import Path
    vp = Path(video_path)
    odir = Path(out_dir) if out_dir else vp.with_suffix(".frames")
    frames = video_mod.extract_frames(vp, odir, fps=fps, at_times=at_times)
    return {"video": str(vp), "out_dir": str(odir), "frames": [str(f) for f in frames]}


@mcp.tool(structured_output=False, description=(
    "List all pages/tabs for an instance. The active page (the one every other "
    "per-instance tool targets) has is_active=True. Popups opened by the browser "
    "are tracked automatically and appear here."
))
def page_list(instance_id: str) -> list[dict[str, Any]]:
    return pool.get(instance_id).list_pages()


@mcp.tool(structured_output=False, description=(
    "Switch the active page for an instance. Subsequent tool calls (click, fill, "
    "evaluate, etc.) target the newly-active page."
))
async def page_switch(instance_id: str, index: int) -> dict[str, Any]:
    return await pool.get(instance_id).switch_page(index)


@mcp.tool(structured_output=False, description=(
    "Close one page/tab for an instance. Refuses if it's the only remaining page "
    "(use browser_close to shut the whole instance instead)."
))
async def page_close(instance_id: str, index: int) -> dict[str, Any]:
    return await pool.get(instance_id).close_page(index)


@mcp.tool(structured_output=False, description=(
    "Launch several browsers in parallel from a list of launch specs. Each spec is "
    "a dict accepting any subset of: kind, url, headed, label, profile, viewport_w, "
    "viewport_h, stabilize, record_video. Returns {launched: [...], errors: [...]}."
))
async def browser_spawn_roster(specs: list[dict[str, Any]]) -> dict[str, Any]:
    return await pool.spawn_roster(specs)


@mcp.tool(structured_output=False, description=(
    "Set the dialog-handling policy for an instance. `policy` is 'accept', 'dismiss', "
    "or 'manual'. When 'accept' is used with a prompt dialog, `prompt_text` supplies "
    "the response string. Default policy is 'dismiss'."
))
def browser_set_dialog_policy(
    instance_id: str, policy: str, prompt_text: str | None = None,
) -> dict[str, Any]:
    return pool.get(instance_id).set_dialog_policy(policy, prompt_text)


@mcp.tool(structured_output=False, description=(
    "Intercept requests matching `url_pattern` and fulfill them with a stubbed response. "
    "Useful for making tests deterministic when the target app calls external services. "
    "url_pattern is a glob (e.g. '**/api/users') or regex (Playwright auto-detects). "
    "Body defaults to empty. content_type defaults to application/json."
))
async def browser_mock_route(
    instance_id: str,
    url_pattern: str,
    status: int = 200,
    body: str | None = None,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).mock_route(
        url_pattern, status=status, body=body,
        content_type=content_type, headers=headers,
    )


@mcp.tool(structured_output=False, description=(
    "Remove a previously-installed mock for `url_pattern`. Raises if no mock was active."
))
async def browser_unmock_route(instance_id: str, url_pattern: str) -> dict[str, Any]:
    return await pool.get(instance_id).unmock_route(url_pattern)


@mcp.tool(structured_output=False, description=(
    "Upload one or more files into an <input type=file> element. `paths` is a list "
    "of absolute file paths on this machine."
))
async def browser_set_input_files(
    instance_id: str, selector: str, paths: list[str],
) -> dict[str, Any]:
    return await pool.get(instance_id).set_input_files(selector, paths)


@mcp.tool(structured_output=False, description=(
    "Run all test macros in a directory, producing a JUnit XML report. A macro is "
    "considered a test if its description starts with [test]. Spawns one ephemeral "
    "browser per test (kind defaults to 'webkit'). Returns {passed, failed, total, "
    "report_path, results: [per-test summary]}."
))
async def run_test_suite(
    macros_dir: str | None = None,
    kind: str = "webkit",
    tag: str | None = None,
    out_path: str | None = None,
) -> dict[str, Any]:
    from . import runner
    return await runner.run_suite(
        macros_dir=macros_dir,
        kind=kind,
        tag=tag,
        out_path=out_path,
        pool=pool,
    )


@mcp.tool(structured_output=False, description=(
    "Switch the active target to an iframe. Subsequent click/fill/type/evaluate/wait_for "
    "calls target the frame instead of the top-level page. Exactly one of selector, name, "
    "or url_pattern. Use browser_reset_frame to switch back."
))
async def browser_switch_frame(
    instance_id: str,
    selector: str | None = None,
    name: str | None = None,
    url_pattern: str | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).switch_frame(
        selector=selector, name=name, url_pattern=url_pattern,
    )


@mcp.tool(structured_output=False, description="Reset the active target to the top-level page.")
async def browser_reset_frame(instance_id: str) -> dict[str, Any]:
    return await pool.get(instance_id).reset_frame()


@mcp.tool(structured_output=False, description="List all frames on the active page (including main).")
def browser_list_frames(instance_id: str) -> list[dict[str, Any]]:
    return pool.get(instance_id).list_frames()


@mcp.tool(structured_output=False, description=(
    "List downloads that have been saved for an instance. Each entry has url, "
    "suggested_filename, path, timestamp."
))
def browser_downloads(instance_id: str) -> list[dict[str, Any]]:
    return pool.get(instance_id).list_downloads()


@mcp.tool(structured_output=False, description=(
    "Block until the next download completes for an instance, or raise if timeout exceeded. "
    "Returns the new download record."
))
async def browser_wait_for_download(
    instance_id: str, timeout_ms: int = 15000,
) -> dict[str, Any]:
    return await pool.get(instance_id).wait_for_download(timeout_ms=timeout_ms)


@mcp.tool(structured_output=False, description=(
    "Click an element matched by an ARIA role, label, visible text, or data-testid. "
    "More resilient than CSS selectors. Provide exactly one of role/label/text/test_id. "
    "When role is used, role_name narrows to an accessible name (e.g. 'Submit')."
))
async def browser_click_by(
    instance_id: str,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).click_by(
        role=role, role_name=role_name, role_exact=role_exact,
        label=label, text=text, test_id=test_id, timeout_ms=timeout_ms,
    )


@mcp.tool(structured_output=False, description=(
    "Fill an input matched by ARIA role, label, or data-testid. Provide value plus "
    "exactly one of role/label/test_id."
))
async def browser_fill_by(
    instance_id: str,
    value: str,
    role: str | None = None,
    role_name: str | None = None,
    label: str | None = None,
    test_id: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).fill_by(
        value, role=role, role_name=role_name,
        label=label, test_id=test_id, timeout_ms=timeout_ms,
    )


@mcp.tool(structured_output=False, description=(
    "Read the inner text of an element matched by role, label, text, or data-testid. "
    "Useful for assertions that need a value rather than just a boolean match."
))
async def browser_get_text_by(
    instance_id: str,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).get_text_by(
        role=role, role_name=role_name, role_exact=role_exact,
        label=label, text=text, test_id=test_id, timeout_ms=timeout_ms,
    )


def registered_tool_names() -> list[str]:
    """Used by `cli.py selftest` to verify registration without a client."""
    return sorted(t.name for t in mcp._tool_manager.list_tools())


def recordings_dir() -> Path:
    return RECORDINGS_DIR

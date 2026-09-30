# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``browser_export_script`` reads the rows the recorder actually writes.

The emitters in ``export.py`` / ``export_ts.py`` read a recording row by field
name, and the recorder writes those rows from the session methods. Hand-built
fixture rows let the two sides drift apart unnoticed: the emitters read
``url_pattern`` and ``files`` while the recorder wrote ``pattern`` and
``paths``, so exporting a real ``browser_mock_route`` raised ``KeyError`` and
``set_input_files`` exported an empty file list -- and every test passed,
because the tests had invented the same wrong names.

So every row here comes from calling the real session method against a mock
page and capturing what it handed ``recorder.record``. Each case passes
distinctive values in; the exported source must carry every one of them out.
A handler the exporters gain later must add a case (or an explicit exclusion
naming why), which ``test_every_emitter_has_a_recorder_case`` enforces.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import defaults
from octowright.browser_pool.launch_helpers import _record_launch_event
from octowright.export import _PY_HANDLERS, export_script
from octowright.export_ts import _TS_HANDLERS
from octowright.session.core import BrowserSession


def _locator() -> MagicMock:
    locator = MagicMock()
    for name in ("click", "fill", "inner_text", "aria_snapshot", "count", "wait_for"):
        setattr(locator, name, AsyncMock(return_value=""))
    # The password probe's shape for an ordinary text box, so values are recorded.
    locator.evaluate = AsyncMock(return_value={"type": "text", "ac": ""})
    locator.first = locator
    return locator


def _page() -> AsyncMock:
    page = AsyncMock()
    page.url = "https://recorded.test/current"
    page.is_closed = MagicMock(return_value=False)
    page.on = MagicMock()
    page.locator = MagicMock(side_effect=lambda *_a, **_k: _locator())
    for finder in ("get_by_role", "get_by_label", "get_by_text", "get_by_test_id"):
        setattr(page, finder, MagicMock(side_effect=lambda *_a, **_k: _locator()))
    page.frames = []
    # Truthy only for the cases' own expressions: the session also evaluates
    # its own probes (is this a password field?), which must answer no.
    page.evaluate = AsyncMock(side_effect=lambda expression, *_a: "sentinel" in str(expression))
    return page


def _session(tmp_path: Path) -> BrowserSession:
    page = _page()
    context = MagicMock()
    context.new_page = AsyncMock(return_value=_page())
    return BrowserSession(
        instance_id="contract",
        kind="chromium",
        label="t",
        url="https://recorded.test/",
        page=page,
        context=context,
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


def _launch_row(tmp_path: Path) -> dict[str, Any]:
    recorder = MagicMock()
    _record_launch_event(
        recorder,
        instance_id="contract",
        kind="chromium",
        label=None,
        profile=None,
        user_data_dir=None,
        target_url="https://sentinel-launch.test/",
        headless=True,
        log_viewport={"mode": "fixed", "w": 1111, "h": 777},
        stabilize=False,
        record_video=False,
        video_dir=None,
        trace=False,
        har_path=None,
        har_mode="full",
        har_url_filter=None,
        har_content=None,
        badge=False,
        badge_position="top-right",
        tile=False,
        ephemeral=False,
        session=False,
        disable_automation_controlled=False,
    )
    return _rows(recorder)[0]


def _rows(recorder: MagicMock) -> list[dict[str, Any]]:
    """What ``Recorder.record(action, **fields)`` would have written, minus ``ts``."""
    return [{"action": c.args[0], **c.kwargs} for c in recorder.record.call_args_list]


async def _mock_then_unmock(s: BrowserSession) -> None:
    await s.mock_route("**/sentinel-mock/**", status=201, body="sentinel-body", content_type="text/sentinel")
    await s.unmock_route("**/sentinel-mock/**")


async def _open_then_switch_then_close(s: BrowserSession) -> None:
    await s.open_url("https://sentinel-tab.test/")
    await s.switch_page(1)
    await s.close_page(0)


async def _switch_frame(s: BrowserSession) -> None:
    frame = MagicMock(url="https://frame.test/", name="sentinel-frame")
    frame.name = "sentinel-frame"
    frame.set_input_files = AsyncMock()
    s.page.frames = [s.page, frame]
    s.page.frame = MagicMock(return_value=frame)
    await s.switch_frame(name="sentinel-frame")
    await s.reset_frame()


def _upload_file(tmp_path: Path) -> str:
    staged = defaults.UPLOAD_STAGING_DIR / "sentinel-upload.txt"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text("x", encoding="utf-8")
    return str(staged.resolve())


async def _upload(s: BrowserSession, path: str) -> None:
    chooser = MagicMock()
    chooser.set_files = AsyncMock()

    class _Info:
        value: Any

        async def __aenter__(self) -> _Info:
            future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            future.set_result(chooser)
            self.value = future
            return self

        async def __aexit__(self, *_a: Any) -> None:
            return None

    s.page.expect_file_chooser = MagicMock(return_value=_Info())
    await s.upload_files(paths=[path], selector="#sentinel-upload")


#: action kind -> (drive the session, values the exported source must carry).
#: A value has no quote, so apart from a Windows path's backslashes (doubled
#: in both) it appears literally in a Python ``repr`` and a JSON string.
Case = tuple[Callable[[BrowserSession, str], Awaitable[Any]], list[str]]

CASES: dict[str, Case] = {
    "navigate": (lambda s, _p: s.navigate("https://sentinel-nav.test/"), ["https://sentinel-nav.test/"]),
    "click": (lambda s, _p: s.click("#sentinel-click"), ["#sentinel-click"]),
    "fill": (lambda s, _p: s.fill("#sentinel-fill", "sentinel-value"), ["#sentinel-fill", "sentinel-value"]),
    "click_by": (lambda s, _p: s.click_by(role="button", role_name="sentinel-role"), ["button", "sentinel-role"]),
    "fill_by": (
        lambda s, _p: s.fill_by(value="sentinel-fill-by", label="sentinel-label"),
        ["sentinel-fill-by", "sentinel-label"],
    ),
    "type": (
        lambda s, _p: s.type_text("#sentinel-type", "sentinel-typed", delay_ms=17),
        ["#sentinel-type", "sentinel-typed", "17"],
    ),
    "press_key": (lambda s, _p: s.press_key("SentinelKey"), ["SentinelKey"]),
    "evaluate": (lambda s, _p: s.evaluate("sentinelExpression()"), ["sentinelExpression()"]),
    "wait_for": (lambda s, _p: s.wait_for("#sentinel-wait", None, None), ["#sentinel-wait"]),
    "hover": (lambda s, _p: s.hover("#sentinel-hover"), ["#sentinel-hover"]),
    "select_option": (lambda s, _p: s.select_option("#sentinel-select", index=3), ["#sentinel-select", "3"]),
    "drag": (lambda s, _p: s.drag("#sentinel-src", "#sentinel-dst"), ["#sentinel-src", "#sentinel-dst"]),
    "navigate_back": (lambda s, _p: s.navigate_back(), ["go"]),
    "expect_url": (lambda s, _p: s.expect_url("recorded.test", mode="contains"), ["recorded.test"]),
    "expect_selector": (lambda s, _p: s.expect_selector("#sentinel-present"), ["#sentinel-present"]),
    "expect_js": (lambda s, _p: s.expect_js("sentinelCheck()"), ["sentinelCheck()"]),
    "mark_network_clean": (lambda s, _p: s.mark_network_clean(), ["mark_network_clean"]),
    "expect_network_clean": (lambda s, _p: s.expect_network_clean(settle_timeout_ms=0), ["expect_network_clean"]),
    "open_url": (lambda s, _p: _open_then_switch_then_close(s), ["https://sentinel-tab.test/"]),
    "switch_page": (lambda s, _p: _open_then_switch_then_close(s), ["[1]"]),
    "close_page": (lambda s, _p: _open_then_switch_then_close(s), ["0"]),
    "switch_frame": (lambda s, _p: _switch_frame(s), ["sentinel-frame"]),
    "reset_frame": (lambda s, _p: _switch_frame(s), ["= page"]),
    "mock_route": (
        lambda s, _p: _mock_then_unmock(s),
        ["**/sentinel-mock/**", "201", "sentinel-body", "text/sentinel"],
    ),
    "unmock_route": (lambda s, _p: _mock_then_unmock(s), ["**/sentinel-mock/**"]),
    "set_dialog_policy": (lambda s, _p: s.set_dialog_policy("dismiss"), ["dismiss"]),
    "set_input_files": (lambda s, p: s.set_input_files("#sentinel-input", [p]), ["#sentinel-input", "{path}"]),
    "upload_files": (lambda s, p: _upload(s, p), ["#sentinel-upload", "{path}"]),
}

#: Emitter kinds with no case above, and why a recorder-built row is not possible.
EXCLUDED = {
    "launch": "built from the pool's _record_launch_event in every case below, not a session method",
    "screenshot": "writes a file through the real page; the path contract is covered by test_export.py",
    "resize": "viewport_ops needs a measured window; its row is width/height ints, covered by test_export.py",
    "expect_text": "needs an element handle; its mode field is covered by test_the_export_keeps_what_the_assertion_meant",
    "expect_no_text": "needs a frame scan; the exporters refuse it outright (see _UNSUPPORTED)",
    "if": "macro control flow: written by a macro author, never by the recorder",
    "if_not": "macro control flow: written by a macro author, never by the recorder",
    "while": "macro control flow: written by a macro author, never by the recorder",
    "while_not": "macro control flow: written by a macro author, never by the recorder",
}


def test_every_emitter_has_a_recorder_case() -> None:
    assert set(_PY_HANDLERS) == set(_TS_HANDLERS)
    assert set(CASES) | set(EXCLUDED) == set(_PY_HANDLERS)
    assert not set(CASES) & set(EXCLUDED)


@pytest.fixture
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path / "staging")
    return tmp_path


async def _recorded(tmp_path: Path, kind: str) -> tuple[list[dict[str, Any]], list[str]]:
    drive, expected = CASES[kind]
    session = _session(tmp_path)
    path = _upload_file(tmp_path)
    await drive(session, path)
    rows = [row for row in _rows(session.recorder) if row["action"] == kind]
    assert rows, f"{kind}: the session method recorded no {kind!r} row"
    return rows, [value.replace("{path}", path) for value in expected]


@pytest.mark.parametrize("fmt", ["python", "ts"])
@pytest.mark.parametrize("kind", sorted(CASES))
async def test_the_exported_source_carries_what_the_recorder_wrote(staging: Path, kind: str, fmt: str) -> None:
    rows, expected = await _recorded(staging, kind)
    log = staging / "r.jsonl"
    entries = [_launch_row(staging), *rows]
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    out = export_script(log, staging / ("out.py" if fmt == "python" else "out.ts"), fmt=fmt)
    source = out.read_text(encoding="utf-8")
    if fmt == "python":
        compile(source, "<exported>", "exec")
    rendered = source.split("sentinel-launch.test", 1)[1]
    for value in expected:
        # A Windows upload path has backslashes, which both a Python repr and a
        # JSON string spell doubled.
        spelled = value.replace("\\", "\\\\")
        assert spelled in rendered, f"{kind} ({fmt}): {value!r} missing from\n{rendered}"


def _export_rows(tmp_path: Path, rows: list[dict[str, Any]], fmt: str) -> str:
    log = tmp_path / "rows.jsonl"
    entries = [{"action": "launch", "kind": "chromium", "url": "https://x.test/"}, *rows]
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    out = export_script(log, tmp_path / ("rows.py" if fmt == "python" else "rows.ts"), fmt=fmt)
    return out.read_text(encoding="utf-8")


@pytest.mark.parametrize("fmt", ["python", "ts"])
def test_the_method_parameter_spellings_still_export(tmp_path: Path, fmt: str) -> None:
    """A hand-written row may carry the session parameter names instead of the recorder's."""
    source = _export_rows(
        tmp_path,
        [
            {"action": "mock_route", "url_pattern": "**/legacy-mock/**"},
            {"action": "unmock_route", "url_pattern": "**/legacy-mock/**"},
            {"action": "set_input_files", "selector": "input", "files": ["/legacy/file"]},
        ],
        fmt,
    )
    assert source.count("**/legacy-mock/**") == 2
    assert "/legacy/file" in source


@pytest.mark.parametrize("fmt", ["python", "ts"])
@pytest.mark.parametrize("kind", ["mock_route", "unmock_route"])
def test_a_row_naming_two_different_patterns_is_refused(tmp_path: Path, fmt: str, kind: str) -> None:
    """Which spelling wins is the choice a crafted row would make; neither is picked."""
    row = {"action": kind, "pattern": "https://app.test/**", "url_pattern": "https://evil.test/**"}
    with pytest.raises(ValueError, match="conflicting"):
        _export_rows(tmp_path, [row], fmt)


async def _element_session(tmp_path: Path, inner_text: str) -> BrowserSession:
    session = _session(tmp_path)
    element = MagicMock()
    element.inner_text = AsyncMock(return_value=inner_text)
    session.page.wait_for_selector = AsyncMock(return_value=element)
    session.page.query_selector = AsyncMock(return_value=None)
    return session


#: (drive, python fragment, ts fragment): a recorded field that changes what
#: the assertion MEANS, so carrying the values through is not enough.
MEANING_CASES: dict[str, tuple[Callable[[BrowserSession], Awaitable[Any]], str, str]] = {
    "expect_selector-absent": (
        lambda s: s.expect_selector("#gone", present=False),
        "if await page.locator('#gone').count() > 0: raise",
        'if (await page.locator("#gone").count() > 0) throw',
    ),
    "expect_js-equals": (
        lambda s: s.expect_js("sentinelCount()", equals=True),
        "if await page.evaluate('sentinelCount()') != True: raise",
        'if (!pyEquals(await page.evaluate("sentinelCount()"), true)) throw',
    ),
    "expect_text-equals": (
        lambda s: s.expect_text("#t", "exact words", mode="equals"),
        "if await page.locator('#t').inner_text() != 'exact words': raise",
        'if ((await page.locator("#t").innerText()) !== "exact words") throw',
    ),
    "expect_text-regex": (
        lambda s: s.expect_text("#t", "ex.ct", mode="regex"),
        "if not __import__('re').search('ex.ct', await page.locator('#t').inner_text()): raise",
        'if (!new RegExp("ex.ct").test(await page.locator("#t").innerText())) throw',
    ),
    "wait_for-expression": (
        lambda s: s.wait_for(None, None, 1000, expression="sentinelReady()"),
        "await page.wait_for_function('sentinelReady()')",
        'await page.waitForFunction("sentinelReady()");',
    ),
}


@pytest.mark.parametrize("fmt", ["python", "ts"])
@pytest.mark.parametrize("case", sorted(MEANING_CASES))
async def test_the_export_keeps_what_the_assertion_meant(tmp_path: Path, case: str, fmt: str) -> None:
    """``present=False`` exported as a presence check inverted the assertion."""
    drive, py_fragment, ts_fragment = MEANING_CASES[case]
    session = await _element_session(tmp_path, "exact words")
    await drive(session)
    kind = case.split("-", 1)[0]
    rows = [row for row in _rows(session.recorder) if row["action"] == kind]
    source = _export_rows(tmp_path, rows, fmt)
    if fmt == "python":
        compile(source, "<exported>", "exec")
    assert (py_fragment if fmt == "python" else ts_fragment) in source, source


# A value that closes the literal it is spliced into, in either language, and
# calls PWNED. Embedded as data it appears only inside its own quoted literal.
_PAYLOAD = "x'\"`); PWNED(); ('\n PWNED() // \u2028 PWNED() #"

_CONTROL_ROWS = [
    {"action": "if", "selector": "#s"},
    {"action": "while_not", "expression": "e()"},
    {"action": "if_not", "text": "t"},
]


async def _every_recorded_row(tmp_path: Path) -> list[dict[str, Any]]:
    rows = [_launch_row(tmp_path)]
    for kind in sorted(CASES):
        recorded, _expected = await _recorded(tmp_path / kind, kind)
        rows.extend(recorded)
    for case in MEANING_CASES.values():
        session = await _element_session(tmp_path, "exact words")
        await case[0](session)
        rows.extend(_rows(session.recorder))
    return rows + _CONTROL_ROWS


@pytest.mark.parametrize("fmt", ["python", "ts"])
async def test_no_recorded_field_is_spliced_into_the_source_raw(staging: Path, fmt: str) -> None:
    """Every field of every row, replaced by a payload, stays a literal or is refused.

    A recording or saved macro can be attacker-controlled; the TS select_option
    emitter spliced ``index`` in bare, so ``{"index": "0 }); evil(); ({"}``
    became code in the exported script.
    """
    for kind in CASES:
        (staging / kind).mkdir()
        _upload_file(staging / kind)
    literal = repr(_PAYLOAD) if fmt == "python" else json.dumps(_PAYLOAD)
    checked = 0
    for row in await _every_recorded_row(staging):
        for field in [key for key in row if key != "action"]:
            poisoned = {**row, field: _PAYLOAD}
            try:
                block = [{"action": "click", "selector": "#b"}, {"action": "end_block"}] if row in _CONTROL_ROWS else []
                source = _export_rows(staging, [poisoned, *block], fmt)
            except ValueError:
                continue  # refused at export time: nothing was written
            body = source.split("https://x.test/", 1)[1] if row["action"] != "launch" else source
            assert body.count("PWNED") == 3 * body.count(literal), f"{row['action']}.{field} ({fmt}):\n{body}"
            if fmt == "python":
                compile(source, "<exported>", "exec")
            checked += 1
    assert checked > 50


@pytest.mark.parametrize("fmt", ["python", "ts"])
def test_select_option_index_must_be_a_number(tmp_path: Path, fmt: str) -> None:
    row = {"action": "select_option", "selector": "#s", "index": "0 }); require('fs').rmSync('/'); ({"}
    with pytest.raises(ValueError, match="index"):
        _export_rows(tmp_path, [row], fmt)


@pytest.mark.parametrize("fmt", ["python", "ts"])
def test_a_critical_point_cannot_end_its_comment(tmp_path: Path, fmt: str) -> None:
    log = tmp_path / "r.jsonl"
    log.write_text(json.dumps({"action": "launch", "kind": "chromium", "url": "https://x.test/"}) + "\n")
    point = "checkout\nPWNED()\u2028PWNED()"
    out = export_script(log, tmp_path / f"o.{fmt}", fmt=fmt, manifest={"critical_points": [point]})
    source = out.read_text(encoding="utf-8")
    prefix = "#" if fmt == "python" else "//"
    assert [line for line in source.splitlines() if "PWNED" in line] == [f"{prefix} - checkout PWNED() PWNED()"]


@pytest.mark.parametrize("fmt", ["python", "ts"])
def test_an_infinite_number_is_refused_as_a_value_error(tmp_path: Path, fmt: str) -> None:
    """``int(float('inf'))`` raises OverflowError; export failures are ValueErrors."""
    log = tmp_path / "r.jsonl"
    rows = [{"action": "launch", "kind": "chromium", "url": "https://x.test/"}, {"action": "switch_page", "index": 1}]
    log.write_text("\n".join(json.dumps(r) for r in rows).replace('"index": 1', '"index": Infinity') + "\n")
    with pytest.raises(ValueError, match="index"):
        export_script(log, tmp_path / f"o.{fmt}", fmt=fmt)


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("python", "await _upload_target.locator('#sentinel-input').set_input_files("),
        ("ts", 'await uploadTarget.locator("#sentinel-input").setInputFiles('),
    ],
)
async def test_set_input_files_exports_into_the_selected_frame(staging: Path, fmt: str, expected: str) -> None:
    """The session method targets the active frame, so the exported line must too."""
    session = _session(staging)
    path = _upload_file(staging)
    await _switch_frame(session)
    session.recorder.record.reset_mock()
    await session.switch_frame(name="sentinel-frame")
    await session.set_input_files("#sentinel-input", [path])
    source = _export_rows(staging, _rows(session.recorder), fmt)
    assert expected in source.split("sentinel-frame", 1)[1]

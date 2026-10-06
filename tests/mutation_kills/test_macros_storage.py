# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What macro storage writes, lists, loads and refuses, pinned record by record.

Every test writes under a temporary ``MACROS_DIR``; nothing touches the real one.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

import pytest

from octowright import defaults
from octowright._json_text import dumps_utf8_safe
from octowright.macros import storage
from octowright.macros.parameter_specs import SPECS_KEY
from octowright.macros.privacy import REDACTED, assertion_text_digest

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z$")
MARKER = defaults.REDACTED_INPUT_PLACEHOLDER
TYPED = "zebrin4"
# Fixture values live in constants, not under a credential key: the secret scanner flags those.
SHARED = "admin"
SEEN = "seen"
UNSEEN = "unseen"


@pytest.fixture(autouse=True)
def macros_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "macros"
    monkeypatch.setattr(storage, "MACROS_DIR", root)
    return root


def _recording(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "recording.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _saved(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# --- lock, slug, paths, time --------------------------------------------------------


def test_the_write_lock_times_out_with_a_retry_message_and_is_released_after_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(storage, "MACRO_WRITE_LOCK_TIMEOUT_SECONDS", 0.05)
    held = threading.Event()
    done = threading.Event()

    def hold() -> None:
        with storage.macro_write_lock():
            held.set()
            done.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert held.wait(5)
        with pytest.raises(storage.MacroWriteLockTimeout) as raised, storage.macro_write_lock():
            pass
        assert str(raised.value) == (
            "timed out after 0.05s waiting for another macro save, write or delete to finish; retry"
        )
        assert isinstance(raised.value, TimeoutError)
    finally:
        done.set()
        thread.join(5)
    with storage.macro_write_lock():
        pass
    entered: list[bool] = []

    def probe() -> None:
        with storage.macro_write_lock():
            entered.append(True)

    prober = threading.Thread(target=probe)
    prober.start()
    prober.join(5)
    assert entered == [True]


def test_slug_collapses_unsafe_runs_and_trims_dashes_and_dots() -> None:
    assert storage.slug("  report sync/v2  ") == "report-sync-v2"
    assert storage.slug("..-a.b_c-..") == "a.b_c"
    with pytest.raises(ValueError) as raised:
        storage.slug(" -. ")
    assert str(raised.value) == "macro name ' -. ' produced an empty slug"


def test_slug_trims_only_dashes_and_dots() -> None:
    assert storage.slug("XmacroX") == "XmacroX"


def test_macro_path_is_the_slug_under_the_macros_dir(macros_dir: Path) -> None:
    assert storage.macro_path("report sync") == (macros_dir / "report-sync.json").resolve()


def test_a_macro_file_symlinked_out_of_the_macros_dir_is_refused_by_name(tmp_path: Path, macros_dir: Path) -> None:
    macros_dir.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (macros_dir / "evil.json").symlink_to(outside)
    with pytest.raises(Exception) as raised:
        storage.load_macro("evil")
    assert (
        str(raised.value) == f"macro name 'evil' {str(macros_dir / 'evil.json')!r} resolves outside {str(macros_dir)!r}"
    )


def test_now_iso_is_utc_with_a_z_suffix() -> None:
    assert ISO.match(storage.now_iso())


# --- helpers ------------------------------------------------------------------------


def test_parameters_sharing_a_value_are_refused_by_name_and_never_by_value() -> None:
    with pytest.raises(ValueError) as raised:
        storage._value_to_name({"username": SHARED, "password": SHARED, "b": "x", "a": "x", "c": "y"})
    assert str(raised.value) == (
        "parameters 'a', 'b'; 'password', 'username' share the same value, so save_macro cannot tell which "
        "recorded field belongs to which parameter; record them with distinct values"
    )
    assert storage._value_to_name({"a": "1", "b": "2"}) == {"1": "a", "2": "b"}


def test_field_label_prefers_the_selector() -> None:
    assert storage._field_label({"selector": "#pw", "action": "fill"}, 2) == "#pw"
    assert storage._field_label({"selector": "", "action": "fill"}, 2) == "action 2 (fill)"
    assert storage._field_label({}, 4) == "action 4 (?)"


def test_redacted_fields_names_every_marker_by_index_and_key() -> None:
    actions = [{"action": "fill", "value": MARKER}, {"action": "type", "text": REDACTED, "value": "x"}]
    assert storage._redacted_fields(actions) == [(0, "value"), (1, "text")]


def test_unmatched_credential_parameters_lists_only_credential_names_absent_from_the_recording() -> None:
    entries = [{"value": "seen", "n": 3}]
    params = dict.fromkeys(["password"], SEEN) | dict.fromkeys(["token", "note"], UNSEEN) | {"api_key": TYPED}
    params["note"] = UNSEEN + "2"
    assert storage._unmatched_credential_parameters(entries, params) == ["api_key", "token"]


LEAD = (
    "the recording holds {n} input field(s) redacted at record time ({fields}), because "
    "OCTOWRIGHT_REDACT_INPUTS or the session's scrub set hid the typed value; saving would write the "
    "redaction marker into the macro and replay would type it into the page. "
)


def test_redaction_refusal_with_no_candidate_says_to_declare_one() -> None:
    assert storage._redaction_refusal("#pw", 1, []) == LEAD.format(n=1, fields="#pw") + (
        "Declare a credential-named parameter (for example `password`) for that field, or re-record "
        "with OCTOWRIGHT_REDACT_INPUTS=off in a trusted environment."
    )


def test_redaction_refusal_with_several_fields_says_only_one_binds() -> None:
    assert storage._redaction_refusal("#a, #b", 2, ["password"]) == LEAD.format(n=2, fields="#a, #b") + (
        "Only a single redacted field can be bound automatically; re-record the fields separately, "
        "or with OCTOWRIGHT_REDACT_INPUTS=off in a trusted environment."
    )


def test_redaction_refusal_with_several_candidates_names_them() -> None:
    assert storage._redaction_refusal("#pw", 1, ["password", "token"]) == LEAD.format(n=1, fields="#pw") + (
        "Parameters 'password', 'token' could each fill it; declare only the one that was typed there."
    )


# --- save_macro ---------------------------------------------------------------------


def test_save_writes_the_whole_macro_and_keeps_created_at_and_specs_on_resave(tmp_path: Path, macros_dir: Path) -> None:
    rec = _recording(
        tmp_path,
        [
            {"ts": "t", "action": "launch", "url": "https://shop.test/"},
            {"ts": "t", "action": "fill", "selector": "#email", "value": "buyer@example.test"},
            {"ts": "t", "action": "close"},
        ],
    )
    dest = storage.save_macro(
        recording_path=rec, name="login flow", description="logs in", parameters={"email": "buyer@example.test"}
    )
    assert dest == (macros_dir / "login-flow.json").resolve()
    first = _saved(dest)
    assert ISO.match(first["created_at"])
    assert first == {
        "name": "login flow",
        "description": "logs in",
        "parameters": ["email"],
        "created_at": first["created_at"],
        "updated_at": first["created_at"],
        "actions": [{"ts": "t", "action": "fill", "selector": "#email", "value": "{{email}}"}],
    }
    assert dest.read_text(encoding="utf-8") == dumps_utf8_safe(first, indent=2)

    specs = {"email": {"sensitive": True}}
    dest.write_text(json.dumps({**first, "created_at": "2020-01-01T00:00:00Z", SPECS_KEY: specs}), encoding="utf-8")
    storage.save_macro(recording_path=rec, name="login flow", include_launch=True)
    second = _saved(dest)
    assert second == {
        "name": "login flow",
        "description": None,
        "parameters": [],
        "created_at": "2020-01-01T00:00:00Z",
        "updated_at": second["updated_at"],
        "actions": [
            {"ts": "t", "action": "launch", "url": "https://shop.test/"},
            {"ts": "t", "action": "fill", "selector": "#email", "value": "buyer@example.test"},
        ],
        SPECS_KEY: specs,
    }
    assert ISO.match(second["updated_at"])


def test_save_refuses_a_slug_collision_with_another_display_name(tmp_path: Path, macros_dir: Path) -> None:
    rec = _recording(tmp_path, [{"ts": "t", "action": "click", "selector": "#go"}])
    storage.save_macro(recording_path=rec, name="report sync")
    with pytest.raises(ValueError) as raised:
        storage.save_macro(recording_path=rec, name="report-sync")
    assert str(raised.value) == (
        "macro name 'report-sync' collides with existing macro 'report sync' (both map to report-sync.json); "
        "choose a distinct name or delete the existing macro first with `macro_delete name='report sync'`"
    )


@pytest.mark.parametrize("content", ["not json", "[1, 2]", json.dumps({"description": "nameless"})])
def test_save_overwrites_an_unreadable_listless_or_nameless_file(
    tmp_path: Path, macros_dir: Path, content: str
) -> None:
    macros_dir.mkdir(parents=True)
    (macros_dir / "m.json").write_text(content, encoding="utf-8")
    rec = _recording(tmp_path, [{"ts": "t", "action": "click", "selector": "#go"}])
    saved = _saved(storage.save_macro(recording_path=rec, name="m"))
    assert saved["name"] == "m"
    assert saved["created_at"] == saved["updated_at"]
    assert SPECS_KEY not in saved


def test_save_binds_the_one_redacted_field_to_the_one_unmatched_credential_parameter(tmp_path: Path) -> None:
    rec = _recording(
        tmp_path,
        [
            {"ts": "t", "action": "fill", "selector": "#email", "value": "buyer@example.test"},
            {"ts": "t", "action": "fill", "selector": "#password", "value": MARKER},
        ],
    )
    saved = _saved(
        storage.save_macro(recording_path=rec, name="m", parameters={"email": "buyer@example.test", "password": TYPED})
    )
    assert saved["actions"] == [
        {"ts": "t", "action": "fill", "selector": "#email", "value": "{{email}}"},
        {"ts": "t", "action": "fill", "selector": "#password", "value": "{{password}}"},
    ]


def test_save_refuses_two_redacted_fields_naming_both(tmp_path: Path, macros_dir: Path) -> None:
    rec = _recording(
        tmp_path,
        [
            {"ts": "t", "action": "fill", "selector": "#password", "value": MARKER},
            {"ts": "t", "action": "fill", "value": MARKER},
        ],
    )
    with pytest.raises(ValueError) as raised:
        storage.save_macro(recording_path=rec, name="m", parameters={"password": TYPED})
    assert str(raised.value) == LEAD.format(n=2, fields="#password, action 1 (fill)") + (
        "Only a single redacted field can be bound automatically; re-record the fields separately, "
        "or with OCTOWRIGHT_REDACT_INPUTS=off in a trusted environment."
    )
    assert not macros_dir.exists()


def test_save_refuses_a_redacted_field_with_no_parameter(tmp_path: Path) -> None:
    rec = _recording(tmp_path, [{"ts": "t", "action": "fill", "selector": "#password", "value": MARKER}])
    with pytest.raises(
        ValueError, match=r"^the recording holds 1 input field\(s\) redacted at record time \(#password\)"
    ):
        storage.save_macro(recording_path=rec, name="m")


def test_save_binds_a_redacted_assertion_to_the_parameter_its_digest_matches(tmp_path: Path) -> None:
    text = defaults.REDACTED_ASSERTION_TEXT
    rec = _recording(
        tmp_path,
        [
            {"ts": "t", "action": "fill", "selector": "#user", "value": "alice"},
            {
                "ts": "t",
                "action": "expect_no_text",
                "text": text,
                "text_digest": assertion_text_digest("alice"),
                "snapshot": "x",
            },
            {"ts": "t", "action": "expect_no_text", "text": text, "text_digest": assertion_text_digest("nobody")},
            {"ts": "t", "action": "expect_text", "text": text, "text_digest": "keep"},
        ],
    )
    saved = _saved(storage.save_macro(recording_path=rec, name="m", parameters={"user": "alice"}))
    assert saved["actions"] == [
        {"ts": "t", "action": "fill", "selector": "#user", "value": "{{user}}"},
        {"ts": "t", "action": "expect_no_text", "text": "{{user}}"},
        {"ts": "t", "action": "expect_no_text", "text": text},
        {"ts": "t", "action": "expect_text", "text": text, "text_digest": "keep"},
    ]


def test_a_redacted_assertion_matching_two_parameters_stays_a_marker() -> None:
    text = defaults.REDACTED_ASSERTION_TEXT
    action = {"action": "expect_no_text", "text": text, "text_digest": assertion_text_digest("same")}
    assert storage._bind_assertion(action, {"a": "same", "b": "same"}) == {"action": "expect_no_text", "text": text}
    plain = {"action": "expect_no_text", "text": "Traceback", "text_digest": assertion_text_digest("Traceback")}
    assert storage._bind_assertion(plain, {"a": "Traceback"}) == {"action": "expect_no_text", "text": "Traceback"}


# --- list / load / write / delete ----------------------------------------------------


def test_list_macros_without_a_directory_is_empty() -> None:
    assert storage.list_macros() == []


def test_list_macros_reports_every_readable_macro_newest_first(macros_dir: Path) -> None:
    macros_dir.mkdir(parents=True)
    (macros_dir / "old.json").write_text(
        json.dumps(
            {
                "name": "Old one",
                "description": "d",
                "parameters": ["p"],
                "created_at": "2020-01-01T00:00:00Z",
                "updated_at": "2020-01-02T00:00:00Z",
                "actions": [{}, {}],
            }
        ),
        encoding="utf-8",
    )
    (macros_dir / "new.json").write_text(json.dumps({"updated_at": "2021-01-01T00:00:00Z"}), encoding="utf-8")
    (macros_dir / "bare.json").write_text(json.dumps({}), encoding="utf-8")
    (macros_dir / "broken.json").write_text("{", encoding="utf-8")
    (macros_dir / "notes.txt").write_text("{}", encoding="utf-8")
    assert storage.list_macros() == [
        {
            "name": "new",
            "description": None,
            "parameters": [],
            "path": str(macros_dir / "new.json"),
            "created_at": None,
            "updated_at": "2021-01-01T00:00:00Z",
            "action_count": 0,
        },
        {
            "name": "Old one",
            "description": "d",
            "parameters": ["p"],
            "path": str(macros_dir / "old.json"),
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2020-01-02T00:00:00Z",
            "action_count": 2,
        },
        {
            "name": "bare",
            "description": None,
            "parameters": [],
            "path": str(macros_dir / "bare.json"),
            "created_at": None,
            "updated_at": None,
            "action_count": 0,
        },
    ]


def test_load_macro_reads_the_file_or_says_how_to_find_one(macros_dir: Path) -> None:
    with pytest.raises(FileNotFoundError) as raised:
        storage.load_macro("nope")
    assert str(raised.value) == (
        f"no macro named 'nope' at {(macros_dir / 'nope.json').resolve()}; list saved macros with `macro_list` or "
        "record one with `macro_save instance_id=<id> name='nope'`"
    )
    macros_dir.mkdir(parents=True)
    (macros_dir / "m.json").write_text(json.dumps({"name": "m", "actions": [1]}), encoding="utf-8")
    assert storage.load_macro("m") == {"name": "m", "actions": [1]}


def test_write_macro_replaces_exactly_and_resets_created_at(macros_dir: Path) -> None:
    macros_dir.mkdir(parents=True)
    (macros_dir / "m.json").write_text(
        json.dumps({"name": "m", "created_at": "2020-01-01T00:00:00Z", SPECS_KEY: {"p": {"sensitive": True}}}),
        encoding="utf-8",
    )
    given = {"name": "ignored", "actions": [{"action": "click"}], "updated_at": "old"}
    dest = storage.write_macro(name="m", macro=given)
    assert dest == (macros_dir / "m.json").resolve()
    saved = _saved(dest)
    assert saved == {
        "name": "m",
        "actions": [{"action": "click"}],
        "updated_at": saved["updated_at"],
        "created_at": saved["updated_at"],
    }
    assert ISO.match(saved["updated_at"])
    assert given == {"name": "ignored", "actions": [{"action": "click"}], "updated_at": "old"}
    assert dest.read_text(encoding="utf-8") == dumps_utf8_safe(saved, indent=2)


def test_write_macro_creates_the_directory_and_keeps_a_given_created_at(macros_dir: Path) -> None:
    saved = _saved(storage.write_macro(name="fresh", macro={"created_at": "2019-01-01T00:00:00Z"}))
    assert saved["created_at"] == "2019-01-01T00:00:00Z"
    assert saved["name"] == "fresh"


def test_write_macro_refuses_a_slug_collision(macros_dir: Path) -> None:
    storage.write_macro(name="report sync", macro={})
    with pytest.raises(ValueError, match=r"^macro name 'report-sync' collides with existing macro 'report sync'"):
        storage.write_macro(name="report-sync", macro={})


def test_write_compiled_macro_carries_created_at_and_specs_and_reports_a_shrink(macros_dir: Path) -> None:
    macros_dir.mkdir(parents=True)
    specs = {"pin": {"sensitive": True}}
    (macros_dir / "m.json").write_text(
        json.dumps({"name": "m", "parameters": ["pin"], "created_at": "2020-01-01T00:00:00Z", SPECS_KEY: specs}),
        encoding="utf-8",
    )
    dest, findings = storage.write_compiled_macro(name="m", macro={"parameters": ["pin"]})
    assert findings == []
    saved = _saved(dest)
    assert saved == {
        "parameters": ["pin"],
        "name": "m",
        "updated_at": saved["updated_at"],
        "created_at": "2020-01-01T00:00:00Z",
        SPECS_KEY: specs,
    }
    loosened = {"pin": {"sensitive": False}}
    dest, findings = storage.write_compiled_macro(name="m", macro={"parameters": ["pin"], SPECS_KEY: loosened})
    assert [code for code, _ in findings] == ["sensitive_parameters_shrank"]
    assert _saved(dest)[SPECS_KEY] == loosened


def test_write_compiled_macro_of_a_new_macro_reports_no_findings(macros_dir: Path) -> None:
    dest, findings = storage.write_compiled_macro(name="brand new", macro={"parameters": []})
    assert findings == []
    assert dest == (macros_dir / "brand-new.json").resolve()


def test_write_compiled_macro_without_a_created_at_on_disk_stamps_now(macros_dir: Path) -> None:
    macros_dir.mkdir(parents=True)
    (macros_dir / "m.json").write_text(json.dumps({"name": "m"}), encoding="utf-8")
    dest, findings = storage.write_compiled_macro(name="m", macro={})
    saved = _saved(dest)
    assert findings == []
    assert saved == {"name": "m", "updated_at": saved["updated_at"], "created_at": saved["updated_at"]}


def test_delete_macro_removes_the_file_or_says_it_is_missing(macros_dir: Path) -> None:
    with pytest.raises(FileNotFoundError) as raised:
        storage.delete_macro("gone")
    assert str(raised.value) == (
        f"no macro named 'gone' at {(macros_dir / 'gone.json').resolve()}; list saved macros with `macro_list`"
    )
    dest = storage.write_macro(name="m", macro={})
    assert storage.delete_macro("m") == dest
    assert not dest.exists()

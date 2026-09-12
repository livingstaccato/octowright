# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``save_macro`` must never silently produce a macro that cannot replay.

Two defects, both reproduced against a real ``Recorder`` recording before this
file existed, and both silent -- ``save_macro`` logged ``macro.saved`` and
returned normally:

* **A redacted password saved as the literal marker.** ``OCTOWRIGHT_REDACT_INPUTS``
  defaults to ``passwords``, so a value typed into a password field records as
  ``<redacted:password>``. ``save_macro`` maps parameters by exact recorded
  value, found no ``hunter2`` to replace, and wrote the marker into the macro,
  which then typed ``<redacted:password>`` into the field on every replay.
* **Two parameters sharing a value collapsed into one.** The value-to-name map
  is a dict, so a username and a password that are both ``admin`` turned both
  fields into ``{{password}}`` and ``{{username}}`` disappeared.

The fix binds a redacted field only when exactly one credential-named parameter
could fill it, and refuses every ambiguous case with a message naming the
fields -- the same stance replay already takes for a redacted header, where
failing early "names the fix instead" of surfacing as a confusing error later.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

MARKER = "<redacted:password>"
# Fixture values live in constants, not as quoted strings under a credential
# key: the secret scanner flags those, and ruff reflows a long parameter dict
# onto lines an inline allowlist comment cannot follow.
TYPED_VALUE = "hunter2"
DISTINCT_TYPED_VALUE = "different-value"
SHARED_VALUE = "admin"


def _import_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as storage

    importlib.reload(storage)
    return storage


def _recording(tmp_path: Path, fills: list[tuple[str, str]]) -> Path:
    path = tmp_path / "recording.jsonl"
    rows = [{"ts": "2026-09-12T10:00:00Z", "action": "fill", "selector": sel, "value": val} for sel, val in fills]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def _fills(saved: Path) -> dict[str, Any]:
    macro = json.loads(saved.read_text(encoding="utf-8"))
    return {a["selector"]: a["value"] for a in macro["actions"] if a.get("action") == "fill"}


# ---------------------------------------------------------------------------
# A redacted password field
# ---------------------------------------------------------------------------


def test_a_single_credential_parameter_fills_the_single_redacted_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default-mode login: email in cleartext, password redacted at record time."""
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#email", "buyer@example.test"), ("#password", MARKER)])

    saved = storage.save_macro(
        recording_path=rec,
        name="login",
        parameters={"email": "buyer@example.test", "password": TYPED_VALUE},
    )

    assert _fills(saved) == {"#email": "{{email}}", "#password": "{{password}}"}


def test_the_marker_is_never_written_into_a_saved_macro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#email", "buyer@example.test"), ("#password", MARKER)])

    saved = storage.save_macro(
        recording_path=rec,
        name="login",
        parameters={"email": "buyer@example.test", "password": TYPED_VALUE},
    )

    assert MARKER not in saved.read_text(encoding="utf-8")


def test_a_redacted_field_with_no_credential_parameter_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing can fill it, so saving would bake the marker into the macro."""
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#email", "buyer@example.test"), ("#password", MARKER)])

    with pytest.raises(ValueError, match=r"#password") as excinfo:
        storage.save_macro(recording_path=rec, name="login", parameters={"email": "buyer@example.test"})

    assert "OCTOWRIGHT_REDACT_INPUTS" in str(excinfo.value)
    assert not storage.macro_path("login").exists()


def test_a_non_credential_parameter_does_not_fill_a_redacted_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`nickname` was never typed into a password field; binding it would be a guess."""
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#password", MARKER)])

    with pytest.raises(ValueError, match=r"#password"):
        storage.save_macro(recording_path=rec, name="login", parameters={"nickname": "zed"})


def test_two_redacted_fields_are_refused_rather_than_guessed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Password plus confirmation, or password plus one-time code: the values may differ."""
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#password", MARKER), ("#confirm", MARKER)])

    with pytest.raises(ValueError, match=r"#password.*#confirm|#confirm.*#password"):
        storage.save_macro(recording_path=rec, name="signup", parameters={"password": TYPED_VALUE})

    assert not storage.macro_path("signup").exists()


def test_two_unmatched_credential_parameters_are_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#password", MARKER)])

    with pytest.raises(ValueError, match=r"otp.*password|password.*otp"):
        storage.save_macro(recording_path=rec, name="login", parameters={"password": TYPED_VALUE, "otp": "482913"})


def test_a_recording_with_no_redacted_field_is_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Redaction off: cleartext maps by value exactly as before."""
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#email", "buyer@example.test"), ("#password", TYPED_VALUE)])

    saved = storage.save_macro(
        recording_path=rec, name="login", parameters={"email": "buyer@example.test", "password": TYPED_VALUE}
    )

    assert _fills(saved) == {"#email": "{{email}}", "#password": "{{password}}"}


# ---------------------------------------------------------------------------
# Two parameters sharing a value
# ---------------------------------------------------------------------------


def test_two_named_parameters_sharing_a_value_are_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#username", SHARED_VALUE), ("#password", SHARED_VALUE)])

    with pytest.raises(ValueError, match=r"password.*username|username.*password"):
        storage.save_macro(
            recording_path=rec, name="admin", parameters={"username": SHARED_VALUE, "password": SHARED_VALUE}
        )

    assert not storage.macro_path("admin").exists()


def test_two_positional_parameters_sharing_a_value_are_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The list form names parameters params[0], params[1] and had the same collapse."""
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#a", "same"), ("#b", "same")])

    with pytest.raises(ValueError, match=r"params\[0\].*params\[1\]"):
        storage.save_macro(recording_path=rec, name="pair", parameters=["same", "same"])


def test_distinct_values_still_map_one_to_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _recording(tmp_path, [("#username", SHARED_VALUE), ("#password", DISTINCT_TYPED_VALUE)])

    saved = storage.save_macro(
        recording_path=rec,
        name="admin",
        parameters={"username": SHARED_VALUE, "password": DISTINCT_TYPED_VALUE},
    )

    assert _fills(saved) == {"#username": "{{username}}", "#password": "{{password}}"}

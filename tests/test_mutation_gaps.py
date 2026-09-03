# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Assertions for behaviour that was correct but unguarded.

Each test here kills a specific mutant that survived the 2026-08-27 mutation
run (run 33050127725) and was confirmed, against the *whole* test tree rather
than mutmut's selection, to have no test asserting it anywhere. The code under
test was already right in every case -- what was missing was anything that
would notice if it stopped being right.

That distinction is the point. A line like ``strict_json=True`` or
``.replace("-", "_")`` is a deliberate decision someone made once; without an
assertion, deleting it is a silent, green-tested behaviour change. Each test
below names the decision it pins.

**Re-verified 2026-09-03**, because the mutmut nightly had been dead since
2026-08-31 (it failed every night while reporting ``survived: 0``, which reads
like a passing score rather than a harness that never started -- see the root
conftest). Nothing in here could have been re-run in that window, so every one
of the five mutants below was applied to ``src/`` by hand and its test watched
to fail. All five still die. A test that only passes against correct code
proves nothing about whether it would notice the code becoming wrong.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# macros/storage.py -- save_macro(..., strict_json=True)
# ---------------------------------------------------------------------------


def test_save_macro_rejects_a_malformed_recording_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``save_macro`` must refuse a recording it cannot fully parse.

    ``iter_macro_actions``'s own ``strict_json`` parameter is well tested in
    test_macro_recording_import_branches.py -- both branches. What nothing
    asserted is that ``save_macro`` *chooses* the strict one. Flip that single
    keyword to falsy and the malformed line is skipped instead: the macro saves
    successfully, silently missing whatever the unparsable line contained, and
    the whole suite stays green.
    """
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as storage

    importlib.reload(storage)

    recording = tmp_path / "rec.jsonl"
    recording.write_text(
        json.dumps({"action": "navigate", "url": "https://example.test/"}) + "\n" + "{not valid json\n",
        encoding="utf-8",
    )

    with pytest.raises(json.JSONDecodeError):
        storage.save_macro(recording_path=recording, name="partial")


# ---------------------------------------------------------------------------
# macros/execution.py -- key normalization in _redact_args_for_response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["api-key", "access-key", "auth-token", "API-KEY", "Api-Key"])
def test_hyphenated_credential_arg_names_are_redacted(key: str) -> None:
    """A credential arg spelled with hyphens must not survive into ``args_used``.

    ``_SENSITIVE_ARG_KEY_PARTS`` is spelled with underscores (``api_key``,
    ``access_key``), so the only thing that makes ``api-key`` match is the
    ``.replace("-", "_")`` in the normalization step. No test in the repository
    passed a hyphenated key, which left that replace deletable without failure
    -- and macro args are echoed back to the caller, so the value would ship.
    """
    from octowright.macros._redact import _REDACTED_MACRO_VALUE
    from octowright.macros.execution import _redact_args_for_response

    out = _redact_args_for_response({key: "s3kr3t-value"})

    assert out[key] == _REDACTED_MACRO_VALUE
    assert "s3kr3t-value" not in json.dumps(out)


def test_non_credential_arg_names_are_left_readable() -> None:
    """The companion assertion: normalization must not over-redact.

    Without this, a mutant that redacts *everything* would also pass the test
    above. Diagnostic args are the reason ``args_used`` is echoed at all.
    """
    from octowright.macros.execution import _redact_args_for_response

    out = _redact_args_for_response({"order-id": "A-1234", "order_id": "A-1234"})

    assert out == {"order-id": "A-1234", "order_id": "A-1234"}


# ---------------------------------------------------------------------------
# personas.py -- create_persona's mkdir(parents=True)
# ---------------------------------------------------------------------------


def test_create_persona_scaffolds_a_missing_profiles_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """First run: ``~/.config/octowright/profiles/`` does not exist yet.

    Every existing persona test points ``OCTOWRIGHT_PROFILES_DIR`` at a
    pytest ``tmp_path``, which pytest has already created -- so ``parents=True``
    never did any work under test and could be dropped with the suite green.
    On a real first run the parent is genuinely absent and its removal raises
    ``FileNotFoundError`` before the persona is ever written.
    """
    profiles_root = tmp_path / "config" / "octowright" / "profiles"
    assert not profiles_root.exists()

    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(profiles_root))
    from octowright import defaults

    importlib.reload(defaults)
    from octowright import personas

    importlib.reload(personas)

    pdir = personas.create_persona("tanuki-tim", display_name="Tanuki Tim")

    assert pdir.is_dir()
    assert (pdir / "profile.yaml").is_file()
    assert pdir.parent == profiles_root


# ---------------------------------------------------------------------------
# personas.py -- list_personas entries carry "path"
# ---------------------------------------------------------------------------


def test_list_personas_reports_the_persona_directory_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``path`` is part of the list entry contract and nothing read it.

    The dashboard editor and ``resolve.suggest_for_url`` consume these entries.
    With no assertion, the key could be renamed or its value replaced with a
    literal and every test still passed.
    """
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    from octowright import defaults

    importlib.reload(defaults)
    from octowright import personas

    importlib.reload(personas)

    created = personas.create_persona("ziggy", display_name="Ziggy Zebra")

    entries = personas.list_personas()

    assert len(entries) == 1
    assert entries[0]["path"] == str(created)
    assert Path(entries[0]["path"]).is_dir()
    assert entries[0]["display_name"] == "Ziggy Zebra"

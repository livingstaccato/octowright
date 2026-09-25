# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A sequence is an ordered list of macros and their arguments.

Secrets never live in the file: an argument written {"credential": name} is
resolved from the persona when the sequence runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright import defaults, sequences
from octowright.personas import Persona


def write(path: Path, doc: object) -> Path:
    path.write_text(json.dumps(doc))
    return path


def test_steps_load_in_order(tmp_path: Path) -> None:
    steps = sequences.load_sequence(
        write(
            tmp_path / "j.json",
            [
                {"macro": "login", "args": {"username": {"credential": "username"}}},
                {"macro": "page", "args": {"route": "/fleet"}},
            ],
        )
    )
    assert [s.macro for s in steps] == ["login", "page"]
    assert steps[1].args == {"route": "/fleet"}


@pytest.mark.parametrize(
    ("doc", "message"),
    [
        ({"macro": "x"}, "must be a list"),
        ([], "no steps"),
        ([{"args": {}}], r"step 0: 'macro'"),
        ([{"macro": "x", "args": []}], r"step 0: 'args'"),
        ([{"macro": "x", "args": {"a": {"credential": ""}}}], r"step 0 argument 'a'"),
        ([{"macro": "x", "args": {"a": {"artifact": "../up.png"}}}], r"step 0 argument 'a'"),
        ([{"macro": "x", "args": {"a": {"other": 1}}}], r"step 0 argument 'a'"),
    ],
)
def test_malformed_sequences_are_refused(tmp_path: Path, doc: object, message: str) -> None:
    with pytest.raises(sequences.SequenceError, match=message):
        sequences.load_sequence(write(tmp_path / "j.json", doc))


def test_credentials_and_artifacts_resolve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setenv("LAB_USER", "admin")
    persona = Persona(name="lab", credentials={"username_env": "LAB_USER"})
    steps = sequences.load_sequence(
        write(
            tmp_path / "j.json",
            [
                {
                    "macro": "login",
                    "args": {"username": {"credential": "username"}, "shot": {"artifact": "entry.png"}, "route": "/"},
                },
            ],
        )
    )
    names, args = sequences.resolve_steps(steps, persona=persona, artifacts=tmp_path / "run")
    assert names == ["login"]
    assert args == [{"username": "admin", "shot": str(tmp_path / "run" / "entry.png"), "route": "/"}]


def test_the_recordings_root_itself_can_hold_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The lab image points OCTOWRIGHT_RECORDINGS and --artifacts at the same /evidence."""
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path)
    steps = sequences.load_sequence(write(tmp_path / "j.json", [{"macro": "m", "args": {"s": {"artifact": "a.png"}}}]))
    _, args = sequences.resolve_steps(steps, persona=None, artifacts=tmp_path)
    assert args == [{"s": str(tmp_path.resolve() / "a.png")}]


def test_a_credential_the_persona_lacks_fails_before_anything_runs(tmp_path: Path) -> None:
    steps = sequences.load_sequence(
        write(tmp_path / "j.json", [{"macro": "login", "args": {"password": {"credential": "password"}}}])
    )
    with pytest.raises(sequences.SequenceError, match="step 0 argument 'password'"):
        sequences.resolve_steps(steps, persona=Persona(name="lab"), artifacts=None)


def test_a_credential_needs_a_persona(tmp_path: Path) -> None:
    steps = sequences.load_sequence(
        write(tmp_path / "j.json", [{"macro": "m", "args": {"p": {"credential": "password"}}}])
    )
    with pytest.raises(sequences.SequenceError, match="needs --persona"):
        sequences.resolve_steps(steps, persona=None, artifacts=None)


def test_artifacts_must_sit_under_the_recordings_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path / "recordings")
    steps = sequences.load_sequence(write(tmp_path / "j.json", [{"macro": "m", "args": {"s": {"artifact": "a.png"}}}]))
    with pytest.raises(sequences.SequenceError, match="artifacts"):
        sequences.resolve_steps(steps, persona=None, artifacts=tmp_path / "elsewhere")

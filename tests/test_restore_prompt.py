# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Clearing Chromium's "Restore pages?" prompt before opening a profile.

Chromium records how the last run ended in ``<profile>/Preferences`` under
``profile.exit_type``. Anything other than ``Normal`` makes the next launch of
that profile open with a "Chrome didn't shut down correctly / Restore pages?"
bubble, and the flag is sticky: it survives until a run of that same profile
exits cleanly, which an agent-driven browser frequently never does.

Octowright is a reliable source of dirty exits — the crash under investigation
kills the browser process outright, ``octowright restart`` SIGKILLs the leader,
and the orphan reaper kills browsers whose driver died. Two of a real machine's
27 persona profiles were sitting at ``Crashed`` when this was written.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from octowright.browser_pool.restore_prompt import (
    RESTORE_PROMPT_ENV,
    clear_crash_restore_prompt,
    suppress_restore_prompt,
)


def _write_prefs(profile_dir: Path, body: dict, *, name: str = "Default") -> Path:
    leaf = profile_dir / name
    leaf.mkdir(parents=True, exist_ok=True)
    prefs = leaf / "Preferences"
    prefs.write_text(json.dumps(body), encoding="utf-8")
    return prefs


def _exit_type(prefs: Path) -> str | None:
    return json.loads(prefs.read_text(encoding="utf-8"))["profile"].get("exit_type")


# ─── the fix itself ──────────────────────────────────────────────────────────


def test_a_crashed_profile_is_reset_to_normal(tmp_path: Path) -> None:
    prefs = _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed"}})

    clear_crash_restore_prompt(tmp_path)

    assert _exit_type(prefs) == "Normal"


def test_a_sessionended_profile_is_reset_too(tmp_path: Path) -> None:
    """``SessionEnded`` (a kill during shutdown) prompts exactly like Crashed."""
    prefs = _write_prefs(tmp_path, {"profile": {"exit_type": "SessionEnded"}})

    clear_crash_restore_prompt(tmp_path)

    assert _exit_type(prefs) == "Normal"


def test_every_profile_directory_is_cleared(tmp_path: Path) -> None:
    """A user-data-dir can hold more than ``Default``."""
    default = _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed"}})
    second = _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed"}}, name="Profile 1")

    clear_crash_restore_prompt(tmp_path)

    assert _exit_type(default) == "Normal"
    assert _exit_type(second) == "Normal"


def test_exited_cleanly_is_repaired_only_when_chromium_uses_it(tmp_path: Path) -> None:
    """Set the legacy key when it is there; never introduce one that is not.

    Writing a key Chromium did not put there means guessing at a schema we do
    not own, and the observed profiles carry ``exit_type`` alone.
    """
    legacy = _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed", "exited_cleanly": False}})
    modern = _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed"}}, name="Profile 1")

    clear_crash_restore_prompt(tmp_path)

    assert json.loads(legacy.read_text(encoding="utf-8"))["profile"]["exited_cleanly"] is True
    assert "exited_cleanly" not in json.loads(modern.read_text(encoding="utf-8"))["profile"]


def test_unrelated_preferences_survive(tmp_path: Path) -> None:
    """The file holds the profile's whole configuration — rewrite two keys, not it."""
    prefs = _write_prefs(
        tmp_path,
        {
            "profile": {"exit_type": "Crashed", "name": "Tanuki Tim"},
            "credentials_enable_service": False,
            "extensions": {"settings": {"abc": {"state": 1}}},
        },
    )

    clear_crash_restore_prompt(tmp_path)

    body = json.loads(prefs.read_text(encoding="utf-8"))
    assert body["profile"]["name"] == "Tanuki Tim"
    assert body["credentials_enable_service"] is False
    assert body["extensions"] == {"settings": {"abc": {"state": 1}}}


# ─── no churn on the common case ─────────────────────────────────────────────


def test_a_clean_profile_is_not_rewritten(tmp_path: Path) -> None:
    """Almost every launch is of a clean profile; it must cost zero writes.

    Rewriting unconditionally would touch the file on every single launch, for
    no change, inside a tree whose permissions are a documented control.
    """
    prefs = _write_prefs(tmp_path, {"profile": {"exit_type": "Normal"}})
    before = prefs.stat().st_mtime_ns

    clear_crash_restore_prompt(tmp_path)

    assert prefs.stat().st_mtime_ns == before


def test_a_clean_profile_with_a_stale_legacy_flag_is_rewritten(tmp_path: Path) -> None:
    """``exit_type`` alone is not the whole signal when the legacy key is present."""
    prefs = _write_prefs(tmp_path, {"profile": {"exit_type": "Normal", "exited_cleanly": False}})

    clear_crash_restore_prompt(tmp_path)

    assert json.loads(prefs.read_text(encoding="utf-8"))["profile"]["exited_cleanly"] is True


# ─── best-effort: never break a launch ───────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    ["{not json", "[]", '"a string"', '{"profile": "not an object"}', ""],
    ids=["malformed", "list", "scalar", "profile-not-a-map", "empty"],
)
def test_unreadable_preferences_never_raise(tmp_path: Path, body: str) -> None:
    leaf = tmp_path / "Default"
    leaf.mkdir(parents=True)
    (leaf / "Preferences").write_text(body, encoding="utf-8")

    clear_crash_restore_prompt(tmp_path)  # must not raise


def test_a_fresh_profile_directory_is_a_noop(tmp_path: Path) -> None:
    """First launch of a persona: the dir exists, Chromium has written nothing."""
    clear_crash_restore_prompt(tmp_path)  # must not raise


def test_a_missing_profile_directory_is_a_noop(tmp_path: Path) -> None:
    clear_crash_restore_prompt(tmp_path / "never-created")  # must not raise


def test_a_preferences_that_is_a_directory_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "Default" / "Preferences").mkdir(parents=True)

    clear_crash_restore_prompt(tmp_path)  # must not raise


# ─── the opt-out ─────────────────────────────────────────────────────────────


def test_the_policy_is_on_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RESTORE_PROMPT_ENV, raising=False)
    assert suppress_restore_prompt() is True


def test_an_empty_value_still_means_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matches OCTOWRIGHT_RECORDINGS_PRIVATE: only an explicit token opts out."""
    monkeypatch.setenv(RESTORE_PROMPT_ENV, "")
    assert suppress_restore_prompt() is True


@pytest.mark.parametrize("token", ["0", "off", "false", "no", "never", "none", "disabled", "OFF"])
def test_a_falsey_token_opts_out(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv(RESTORE_PROMPT_ENV, token)
    assert suppress_restore_prompt() is False


def test_opting_out_leaves_the_prompt_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RESTORE_PROMPT_ENV, "off")
    prefs = _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed"}})

    clear_crash_restore_prompt(tmp_path)

    assert _exit_type(prefs) == "Crashed"


class TestProfileInUse:
    """The rewrite must not land on a profile another process is holding.

    The docstring used to argue the write "races nothing" because the browser is
    not running -- true only of the browser THIS call is about to start. A second
    process launching the same persona would read-modify-write `Preferences`
    under a live Chromium, violating its single-writer assumption, in the window
    before Playwright's own lock check refused the launch.

    The caller prunes provably-dead locks first, so a lock still present means a
    live (or unverifiable) owner.
    """

    @staticmethod
    def _lock(user_data_dir: Path, pid: int) -> None:
        import socket

        os.symlink(f"{socket.gethostname()}-{pid}", user_data_dir / "SingletonLock")

    @pytest.mark.skipif(os.name == "nt", reason="Chromium writes the Singleton trio only on POSIX")
    def test_a_locked_profile_is_left_alone(self, tmp_path: Path) -> None:
        prefs = _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed"}})
        self._lock(tmp_path, 999_999)

        clear_crash_restore_prompt(tmp_path)

        assert _exit_type(prefs) == "Crashed", "wrote into a profile something else holds"

    @pytest.mark.skipif(os.name == "nt", reason="Chromium writes the Singleton trio only on POSIX")
    def test_an_unlocked_profile_is_still_cleared(self, tmp_path: Path) -> None:
        """The guard must not disable the feature on the ordinary path."""
        prefs = _write_prefs(tmp_path, {"profile": {"exit_type": "Crashed"}})

        clear_crash_restore_prompt(tmp_path)

        assert _exit_type(prefs) == "Normal"

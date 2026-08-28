# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The read-side artifact tools: digest, listing, and critical-point retrieval.

Three MCP-facing entry points whose survivors shared a theme -- the branch
selection and the caps were never observed. `macro_digest` takes either a macro
name or a recording path and nothing had driven the recording arm;
`list_macro_artifacts` clamps its limit with `max(0, ...)` and nothing had
passed a limit low enough to tell that apart from `max(1, ...)`; and
`macro_artifact_critical_points_get` could report `ok: True` on a missing
manifest with the suite still green.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests._macro_artifact_fixtures import (
    _FakeSession,
    _reload,
    _stub_replay,
    _write_macro,
    restore_reloaded_defaults,
)


@pytest.fixture(autouse=True)
def _restore_reloaded_defaults():
    yield
    restore_reloaded_defaults()


# ---------------------------------------------------------------------------
# macro_digest -- two input modes, one of them never driven
# ---------------------------------------------------------------------------


def test_digest_requires_one_of_its_two_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Neither argument is a caller error, not an empty digest.

    `name is None and recording_path is None` flipped to `is not None` makes
    the guard fire when a recording path *was* supplied -- rejecting the valid
    call and accepting the empty one.
    """
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="provide either name or recording_path"):
        macro_artifacts.macro_digest()


def test_digest_of_a_macro_names_its_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The macro arm: source type, name, and a real path on disk."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    result = macro_artifacts.macro_digest(name="login")

    assert result["source"]["type"] == "macro"
    assert result["source"]["name"] == "login"
    assert Path(result["source"]["path"]).exists()


def test_digest_of_a_recording_names_its_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The recording arm, which no test had entered.

    Everything below the `if name is not None` early return -- containment,
    the read, the digest call and the source block -- was unreachable from the
    suite, so all of it could be replaced with `None` unnoticed.
    """
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    from octowright.artifacts.paths import ArtifactStore

    recording = ArtifactStore().root / "sample.jsonl"
    recording.parent.mkdir(parents=True, exist_ok=True)
    recording.write_text(
        json.dumps({"ts": 1, "action": "navigate", "url": "https://example.test/"}) + "\n",
        encoding="utf-8",
    )

    result = macro_artifacts.macro_digest(recording_path=str(recording))

    assert result["source"] == {"type": "recording", "path": str(recording)}


def test_digest_rejects_a_recording_outside_the_artifact_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`recording_path` is caller-supplied, so containment is load-bearing."""
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError):
        macro_artifacts.macro_digest(recording_path=str(outside))


# ---------------------------------------------------------------------------
# list_macro_artifacts -- the limit clamp
# ---------------------------------------------------------------------------


def test_listing_reports_its_default_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The documented default is 20 and the payload echoes it back."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    assert macro_artifacts.list_macro_artifacts()["limit"] == 20


def test_a_zero_limit_returns_nothing_rather_than_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`max(0, limit)` vs `max(1, limit)` differ only here.

    With the floor at 1, asking for zero artifacts returns one -- a caller
    paginating down to an empty page instead gets a row it already showed.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    listing = macro_artifacts.list_macro_artifacts(limit=0)

    assert listing["artifacts"] == []
    assert listing["count"] == 0
    assert listing["limit"] == 0


def test_a_negative_limit_is_clamped_to_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A negative slice bound would count from the end and return real rows."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    listing = macro_artifacts.list_macro_artifacts(limit=-5)

    assert listing["artifacts"] == []
    assert listing["limit"] == 0


# ---------------------------------------------------------------------------
# macro_artifact_critical_points_get
# ---------------------------------------------------------------------------


def test_get_reports_a_missing_manifest_as_not_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`ok: False` is the contract; `ok: True` with an error beside it is not."""
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    result = macro_artifacts.macro_artifact_critical_points_get("never-planned")

    assert result["ok"] is False
    assert result["error"] == "Manifest not found."
    assert "critical_points" not in result


def test_get_returns_an_empty_list_for_an_artifact_with_none_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The default must be iterable -- callers loop over it without checking."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    result = macro_artifacts.macro_artifact_critical_points_get("login")

    assert result["ok"] is True
    assert result["critical_points"] == []
    assert Path(result["paths"]["manifest"]).name == "artifact.json"


def test_get_round_trips_what_set_stored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", [{"id": "cp1", "description": "Login succeeds"}])

    result = macro_artifacts.macro_artifact_critical_points_get("login")

    assert [cp["id"] for cp in result["critical_points"]] == ["cp1"]
    assert result["critical_points"][0]["description"] == "Login succeeds"


# ---------------------------------------------------------------------------
# export_macro_cli
#
# Exporting also writes the artifact manifest, and that half was unasserted:
# the exports record, the plan metadata, and -- on a re-export over an existing
# artifact -- the merge that keeps run history alive.
# ---------------------------------------------------------------------------


def test_export_records_what_it_wrote_in_the_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`exports` names the real file and its kind.

    Nothing read this back, so the whole entry could become `None` -- and every
    consumer iterates it -- or the path could become `str(None)`, pointing at a
    file that does not exist while claiming an export succeeded.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    result = macro_artifacts.export_macro_cli(name="login")

    assert result["ok"] is True
    assert Path(result["path"]).exists()

    listed = macro_artifacts.list_macro_artifacts(name="login")["artifacts"][0]
    assert listed["exports"] == [{"path": result["path"], "kind": "python-cli"}]


def test_export_carries_its_arguments_into_the_generated_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`args` become the argparse defaults of the emitted CLI.

    Only a macro that *declares* a parameter produces a parser line at all,
    which is why the value has to be threaded all the way through: drop the
    `args=` keyword and the generated script still runs, still parses, and
    silently defaults the parameter to empty.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(
        name="login",
        macro={
            "name": "login",
            "description": "Login flow",
            "parameters": ["user"],
            "actions": [{"action": "fill", "selector": "#user", "value": "{{user}}"}],
        },
    )

    result = macro_artifacts.export_macro_cli(name="login", args={"user": "tanuki-tim"})

    script = Path(result["path"]).read_text(encoding="utf-8")
    assert "--user" in script
    assert "tanuki-tim" in script

    listed = macro_artifacts.list_macro_artifacts(name="login")["artifacts"][0]
    assert listed["parameters"] == {"user": "tanuki-tim"}
    assert listed["metadata"]["missing_args"] == []


def test_re_exporting_keeps_the_run_history_and_critical_points(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The merge branch: exporting over an existing artifact must not reset it.

    `export_macro_cli` builds a *fresh* plan manifest and only then merges the
    existing one over it. Skip that merge -- or pass either side as `None` --
    and an export silently discards `latest_run` and every configured critical
    point, so the next verify has nothing to verify against. Reaching it needs
    an artifact that already exists, which no test had.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", [{"id": "cp1"}])
    before = macro_artifacts.list_macro_artifacts(name="login")["artifacts"][0]

    macro_artifacts.export_macro_cli(name="login")

    after = macro_artifacts.list_macro_artifacts(name="login")["artifacts"][0]
    assert [cp["id"] for cp in after["critical_points"]] == ["cp1"]
    assert after["created_at"] == before["created_at"]


def test_export_records_the_plan_metadata_it_built(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The same `_manifest_for_plan` claims, reached through the export path."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)

    macro_artifacts.export_macro_cli(name="login")
    meta = macro_artifacts.list_macro_artifacts(name="login")["artifacts"][0]["metadata"]

    assert meta["ready"] is True
    assert meta["action_count"] == 1
    assert meta["missing_args"] == []
    assert len({meta["paths"][k] for k in ("artifact_dir", "runs_dir", "exports_dir")}) == 3


def test_max_chars_reaches_both_digest_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`max_chars` is threaded to `digest_macro` and `digest_recording_text` alike.

    Dropping the keyword at either call site leaves the digest working on the
    4000-character default -- a caller asking for a small summary silently gets
    a large one, and the `cap` the payload reports is not the cap it was asked
    for.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(
        name="login",
        macro={
            "name": "login",
            "description": "x" * 500,
            "parameters": [],
            "actions": [{"action": "navigate", "url": "https://example.test/"}],
        },
    )

    small = macro_artifacts.macro_digest(name="login", max_chars=40)
    assert small["cap"] == 40
    assert small["truncated"] is True
    assert len(small["summary"]) == 40

    from octowright.artifacts.paths import ArtifactStore

    recording = ArtifactStore().root / "sample.jsonl"
    recording.parent.mkdir(parents=True, exist_ok=True)
    recording.write_text(
        "\n".join(json.dumps({"ts": i, "action": "navigate", "url": f"https://example.test/{i}"}) for i in range(50))
        + "\n",
        encoding="utf-8",
    )

    from_recording = macro_artifacts.macro_digest(recording_path=str(recording), max_chars=40)
    assert from_recording["cap"] == 40
    assert from_recording["truncated"] is True


def test_planning_records_the_manifest_it_built(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`plan_macro_artifact` writes a manifest and nothing read it back.

    Its `_manifest_for_plan` call site could pass `None` for any keyword while
    the suite stayed green -- the helper's own tests cover the helper, not the
    argument threading here. Uses a macro with an unsupplied parameter so
    `missing_args` and `ready` carry real values, which also pins the return
    contract: `ok` is `not missing_args`, so an incomplete plan reports False
    while still writing a usable manifest.
    """
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(
        name="login",
        macro={
            "name": "login",
            "description": "Login flow",
            "parameters": ["user", "password"],
            "actions": [{"action": "navigate", "url": "https://example.test/login"}],
        },
    )

    planned = macro_artifacts.plan_macro_artifact("login", args={"user": "tanuki"})

    assert planned["ok"] is False  # a required argument is missing
    assert planned["missing_args"] == ["password"]
    assert planned["args_used"] == {"user": "tanuki"}
    assert Path(planned["paths"]["macro_path"]).exists()
    assert len(set(planned["paths"].values())) == 5  # no two paths collapse to one

    listed = macro_artifacts.list_macro_artifacts(name="login")["artifacts"][0]
    meta = listed["metadata"]

    assert listed["parameters"] == {"user": "tanuki"}
    assert meta["missing_args"] == ["password"]
    assert meta["ready"] is False
    assert meta["description"] == "Login flow"
    assert meta["action_count"] == 1
    assert len({meta["paths"][k] for k in ("artifact_dir", "runs_dir", "exports_dir")}) == 3


def test_planning_with_every_argument_supplied_is_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The other side of `ok: not missing_args`."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(
        name="login",
        macro={"name": "login", "parameters": ["user"], "actions": [{"action": "navigate"}]},
    )

    planned = macro_artifacts.plan_macro_artifact("login", args={"user": "tanuki"})

    assert planned["ok"] is True
    assert planned["missing_args"] == []
    assert macro_artifacts.list_macro_artifacts(name="login")["artifacts"][0]["metadata"]["ready"] is True


# ---------------------------------------------------------------------------
# macro_artifact_delete
#
# The artifact store was the only store with no delete -- goldens, macros,
# profiles and personas all have one. That was survivable only because
# `recordings_cleanup` swept it by accident, `ArtifactStore` being rooted under
# the recordings dir. Excluding artifacts from that sweep was right, but it
# removed the sole path that ever reclaimed the space.
# ---------------------------------------------------------------------------


def test_deleting_an_artifact_removes_its_runs_and_exports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})
    macro_artifacts.macro_artifact_critical_points_set("login", [{"id": "cp1"}])
    macro_artifacts.export_macro_cli(name="login")

    from octowright.artifacts.paths import ArtifactStore

    artifact_dir = ArtifactStore().macro_dir("login")
    assert (artifact_dir / "artifact.json").exists()

    result = macro_artifacts.delete_macro_artifact("login")

    assert result["deleted"] is True
    assert result["name"] == "login"
    assert not artifact_dir.exists()
    assert macro_artifacts.list_macro_artifacts()["artifacts"] == []


def test_deleting_reports_how_many_runs_went_with_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The count is the only signal of what was discarded, once it is gone."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    _stub_replay(monkeypatch, macro_artifacts)
    macro_artifacts.plan_macro_artifact("login", args={})

    session = _FakeSession(tmp_path)
    for _ in range(3):
        asyncio.run(
            macro_artifacts.run_macro_artifact(session=session, name="login", args={}, capture=False, verify=False)
        )

    assert macro_artifacts.delete_macro_artifact("login")["runs_removed"] == 3


def test_deleting_an_artifact_leaves_the_macro_itself_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The two stores are separate, and so are their deletes."""
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact("login", args={})

    macro_artifacts.delete_macro_artifact("login")

    assert storage.load_macro("login")["name"] == "login"


def test_deleting_a_missing_artifact_names_the_tool_that_lists_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    with pytest.raises(FileNotFoundError, match="macro_artifact_list"):
        macro_artifacts.delete_macro_artifact("never-planned")


def test_delete_refuses_a_name_that_escapes_the_artifact_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The name reaches the filesystem, so containment is load-bearing.

    `macro_dir` runs it through `reject_unsafe_path`; this is a delete, so the
    consequence of losing that is worse than for the read paths already covered.
    """
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    victim = tmp_path / "outside"
    victim.mkdir()

    with pytest.raises((ValueError, FileNotFoundError)):
        macro_artifacts.delete_macro_artifact("../../../outside")

    assert victim.exists()

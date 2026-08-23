# Session-Kind Plugins — Step 2: Core-Owned Artifacts and Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give plugins a contained, durably-registered side-artifact API, and make the dashboard's session list, detail, close, and closed-recording classification resolve through the plugin registry instead of a hardcoded second kind.

**Architecture:** A new `octowright/plugins/artifacts.py` issues reserve/commit artifact handles whose commit writes a durable `artifact_registered` control row into the session's own JSONL. The HTTP layer gains a `state.plugin_registry` seam and a `http/routes/_session_kinds.py` dispatch module, so `list_sessions` / `session_detail` / `session_close` iterate registered pools. Closed-recording discovery learns the uniform `session_start` opening row. Daemon shutdown closes every registered pool.

**Tech Stack:** Python 3.11+, Starlette, `typing.Protocol`, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-session-kind-plugins-design.md` — this plan implements §5.2, §6.7, §8.1, §8.2, §8.3, §8.4, and the step-2 line of §12.

**Depends on:** Step 1 (`feat/session-kind-plugins-step-1`, PR #140). Every module under `src/octowright/plugins/` and `src/octowright/server/plugin_state.py` must exist before Task 1.

## Global Constraints

- **SPDX header** on every new `.py` file, verbatim:
  ```python
  # SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
  # SPDX-License-Identifier: Apache-2.0
  # SPDX-Comment: Part of octowright.
  #
  ```
- **`from __future__ import annotations`** as the first import in every new module.
- **777-line cap** on any `src/**/*.py` — the project-wide limit, and exactly what `scripts/check_max_loc.py` enforces. Split a module when its responsibilities have genuinely diverged, not to chase a lower threshold.
- **Ruff `select`** is `["E", "F", "I", "UP", "B", "SIM", "ARG", "RUF", "TID"]`, `line-length = 120`. `BLE`/`ANN`/`PLW` are NOT enabled — never add a `# noqa` for them, RUF100 flags unused directives.
- **`make lint` must exit 0.** Check the vulture gate with `uv run --active python scripts/check_vulture.py` — never by running `vulture` on one file, whose scope and confidence threshold both differ. **Do not edit `.ci/vulture-baseline.json`;** it is a deliberately-empty ratchet.
- **Commits must be signed.** Never `--no-gpg-sign` or `--no-verify`. If signing stalls, stop and ask.
- **Never** add a `Co-Authored-By: Claude` trailer or any AI-assistance mention to a commit message.
- **Do not touch `CHANGELOG.md`.** Do not push, do not open a PR.
- `pyproject.toml` sets `asyncio_mode = "auto"`, so `@pytest.mark.asyncio` is optional.
- Run tests with `uv run --active pytest`.
- **Terminal stays on its existing path.** Extraction is step 5. Every registry-driven branch in this plan is added *alongside* the existing `terminal_pool` branch, never replacing it. `tests/terminal/` must stay green after every task.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `src/octowright/plugins/artifacts.py` | `ArtifactHandle`, `ctx.artifact` path issuance, containment, directory securing, commit → control row. Read side: parse `artifact_registered` rows back into validated absolute paths. |
| `src/octowright/http/routes/_session_kinds.py` | Registry dispatch for the HTTP layer: iterate pools, resolve a session by id across kinds, build a kind's detail payload, close by kind. Exists because "resolve a session across every registered kind" is one responsibility with its own tests. |
| `tests/plugins/test_artifacts.py` | Write-side artifact tests (containment battery, commit, replace, MIME allowlist). |
| `tests/plugins/test_artifact_reads.py` | Read-side tests (re-resolution, re-containment, uncommitted invisible). |
| `tests/plugins/test_discovery_session_start.py` | `session_start` classification, including a kind whose plugin is absent. |
| `tests/plugins/test_dashboard_registry.py` | Registry-driven list / detail / close. |
| `tests/plugins/test_artifact_route.py` | The artifact media route. |
| `tests/plugins/test_shutdown_teardown.py` | Every registered pool closed at daemon exit. |
| `tests/plugins/test_reference_artifacts.py` | The reference plugin's Tier-2 artifact. |

**Modified:**

| Path | Change |
|---|---|
| `src/octowright/plugins/session_launch.py` | `PluginContext.artifact(...)`. |
| `src/octowright/http/discovery.py` | `_read_first_opening` accepts `session_start`; `_summarise_recording` reads kind/label/profile off it. |
| `src/octowright/http/state.py` | `plugin_registry` module property forwarding to `server.plugin_state.registry()`. |
| `src/octowright/http/routes/sessions.py` | List / detail / close call into `_session_kinds`. |
| `src/octowright/http/routes/media.py` | `GET /api/sessions/{id}/artifacts/{artifact_id}`. |
| `src/octowright/cli/serve.py` | Shutdown closes every registered pool. |
| `tests/plugins/reference/pool.py` | Writes and commits a Tier-2 transcript artifact. |

`src/octowright/recorder.py` is deliberately **not** modified: `artifact_registered` is already in `CONTROL_ACTIONS` from step 1.

---

## Task 1: Artifact reserve/commit

Core's own browser sessions write video, HAR, downloads, and traces, so a flat "plugins never write files" rule would give plugins strictly less than browsers have. A plugin that needs a real file reserves a path, writes to it, and commits.

Path composition is precisely where this project's disk-containment bugs have lived — `browser_export_script`'s `out_path`, the HAR path recovered from a poisoned launch record, `save_as` materializing a `NNN-..` parent, and (in step 1) `begin_session`'s own `instance_id`. Handing plugins a path composer would reopen that class of bug in code core does not review.

**Files:**
- Create: `src/octowright/plugins/artifacts.py`
- Modify: `src/octowright/plugins/session_launch.py` (add `PluginContext.artifact` after `redaction_mode`)
- Test: `tests/plugins/test_artifacts.py`

**Interfaces:**
- Consumes: `octowright._paths.reject_unsafe_path(candidate, root, *, label) -> Path`; `octowright.private_paths.secure_artifact_tree(leaf, root)`; `Recorder.record_control(action, **fields)`; `octowright.plugins.errors.PluginError`.
- Produces:
  - `octowright.plugins.artifacts.ARTIFACT_MIME_ALLOWLIST: frozenset[str]`
  - `octowright.plugins.artifacts.ArtifactError(PluginError)`
  - `octowright.plugins.artifacts.ArtifactHandle` with `.artifact_id: str`, `.path: Path`, `.commit(*, mime_type: str) -> None`
  - `octowright.plugins.artifacts.reserve_artifact(*, recorder, instance_id, recordings_dir, artifact_id, suffix) -> ArtifactHandle`
  - `PluginContext.artifact(session: Any, name: str, suffix: str) -> ArtifactHandle`

- [ ] **Step 1: Write the failing tests**

Create `tests/plugins/test_artifacts.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from octowright.plugins.artifacts import ArtifactError, reserve_artifact
from octowright.recorder import Recorder


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture
def recording(tmp_path):
    log_path = tmp_path / "20260823T000000Z-refkind-refsess01.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    yield recorder, log_path, tmp_path
    recorder.close()


def _reserve(recording, artifact_id="transcript", suffix=".txt"):
    recorder, _log_path, root = recording
    return reserve_artifact(
        recorder=recorder,
        instance_id="refsess01",
        recordings_dir=root,
        artifact_id=artifact_id,
        suffix=suffix,
    )


def test_reserved_path_is_contained_and_its_directory_exists(recording):
    _, _, root = recording
    handle = _reserve(recording)
    assert handle.path.parent.exists(), "core must create the artifact dir before handing out the path"
    assert handle.path.resolve().is_relative_to(root.resolve())
    assert not handle.path.exists(), "reserve must not create the file itself"


def test_artifact_directory_is_owner_only(recording, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS_PRIVATE", "1")
    handle = _reserve(recording)
    assert stat.S_IMODE(handle.path.parent.stat().st_mode) == 0o700


def test_commit_writes_a_control_row_with_a_relative_path(recording):
    recorder, log_path, root = recording
    handle = _reserve(recording)
    handle.path.write_text("hello")
    handle.commit(mime_type="text/plain")
    recorder.close()

    row = [r for r in _rows(log_path) if r["action"] == "artifact_registered"][-1]
    assert row["artifact_id"] == "transcript"
    assert row["mime_type"] == "text/plain"
    assert not Path(row["path"]).is_absolute(), "path must be stored relative to the recordings root"
    assert (root / row["path"]).resolve() == handle.path.resolve()


def test_uncommitted_artifact_writes_no_row(recording):
    recorder, log_path, _ = recording
    handle = _reserve(recording)
    handle.path.write_text("orphan")
    recorder.close()
    assert [r for r in _rows(log_path) if r["action"] == "artifact_registered"] == []


def test_committing_the_same_id_twice_records_both(recording):
    recorder, log_path, _ = recording
    first = _reserve(recording)
    first.path.write_text("v1")
    first.commit(mime_type="text/plain")
    second = _reserve(recording)
    second.path.write_text("v2")
    second.commit(mime_type="text/plain")
    recorder.close()

    rows = [r for r in _rows(log_path) if r["action"] == "artifact_registered"]
    assert len(rows) == 2, "both commits are recorded; the reader takes the last"
    assert rows[-1]["artifact_id"] == "transcript"


def test_commit_rejects_a_mime_type_outside_the_allowlist(recording):
    handle = _reserve(recording)
    handle.path.write_text("x")
    with pytest.raises(ArtifactError, match="mime type"):
        handle.commit(mime_type="application/x-msdownload")


@pytest.mark.parametrize("artifact_id", ["../escape", "a/b", "/abs", "..", "", "with space", "UPPER"])
def test_bad_artifact_ids_are_refused(recording, artifact_id):
    with pytest.raises(ArtifactError):
        _reserve(recording, artifact_id=artifact_id)


@pytest.mark.parametrize("suffix", ["../x", "/x", "no-dot", ".a/b"])
def test_bad_suffixes_are_refused(recording, suffix):
    with pytest.raises(ArtifactError):
        _reserve(recording, suffix=suffix)


def test_a_symlinked_artifact_dir_cannot_escape(recording, tmp_path):
    _, _, root = recording
    outside = tmp_path.parent / "outside-root"
    outside.mkdir(exist_ok=True)
    # Pre-create the artifact dir as a symlink pointing out of the root; the
    # containment check must resolve it before deciding.
    art_dir = root / "session-artifacts" / "refsess01"
    art_dir.parent.mkdir(parents=True, exist_ok=True)
    art_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="resolves outside"):
        _reserve(recording)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_artifacts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.plugins.artifacts'`.

- [ ] **Step 3: Write the artifacts module**

Create `src/octowright/plugins/artifacts.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tier-2 side artifacts: core issues the path, the plugin writes, then commits.

Core's own browser sessions write video, HAR, downloads and traces, so a flat
"plugins never write files" rule would give plugins strictly less than browsers
have. What plugins do NOT get is a path composer: every disk-containment bug
this project has paid for lived in path composition, and each was fixed by
routing through one resolve-and-contain choke point. This module is that choke
point for plugin artifacts.

Registration is durable rather than in-memory. A note on the live session
object dies at close and at daemon restart, and closed-session artifact
scanning recognizes only fixed browser sidecars — so an in-memory registry
would make plugin artifacts invisible to exactly the readers that need them.
Commit therefore writes an ``artifact_registered`` control row into the
session's own JSONL, which also survives the plugin being uninstalled.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from octowright._paths import reject_unsafe_path
from octowright.plugins.errors import PluginError
from octowright.private_paths import secure_artifact_tree
from octowright.recorder import Recorder

#: Artifact ids become a path segment and a URL segment, so they get the same
#: syntax every other plugin identifier does.
_ARTIFACT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")

#: A suffix is a real extension, never a path fragment.
_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9.]{0,15}\Z")

#: Directory under the recordings root that holds one session's artifacts.
_ARTIFACT_DIRNAME = "session-artifacts"

#: Types the dashboard is willing to serve back. Deliberately closed: an
#: artifact route that echoed a plugin-declared Content-Type would let an
#: enabled plugin serve active content from the dashboard's own origin, where
#: the pairing bearer lives.
ARTIFACT_MIME_ALLOWLIST: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/csv",
        "application/json",
        "application/zip",
        "application/octet-stream",
        "image/png",
        "image/jpeg",
        "image/svg+xml",
        "video/webm",
        "video/mp4",
    }
)


class ArtifactError(PluginError):
    """An artifact id, suffix, or mime type was refused."""


@dataclass
class ArtifactHandle:
    """A reserved artifact path plus the commit that makes it real.

    An artifact exists once committed. A reserved-but-never-committed path is
    referenced by nothing and is pruned by ordinary age-based cleanup.
    """

    artifact_id: str
    path: Path
    _recorder: Recorder
    _relative_path: str

    def commit(self, *, mime_type: str) -> None:
        """Register the artifact durably. Call only after the file is written."""
        if mime_type not in ARTIFACT_MIME_ALLOWLIST:
            raise ArtifactError(f"mime type {mime_type!r} is not in the artifact allowlist")
        self._recorder.record_control(
            "artifact_registered",
            artifact_id=self.artifact_id,
            path=self._relative_path,
            mime_type=mime_type,
        )


def reserve_artifact(
    *,
    recorder: Recorder,
    instance_id: str,
    recordings_dir: Path,
    artifact_id: str,
    suffix: str,
) -> ArtifactHandle:
    """Issue a contained artifact path and secure its directory.

    ``instance_id`` is passed in rather than parsed back out of the recording
    filename. ``http/artifacts.instance_id_from_recording_name`` already owns
    that parsing rule, and this module sits below the HTTP layer and must not
    import it — so re-deriving it here would be a second copy of a rule that
    could drift from the first. The caller always has the id already.

    The directory is created and locked BEFORE the path is returned, because
    core cannot secure a file it does not write — the directory is the control,
    consistent with how ``secure_artifact_tree`` already treats captures,
    goldens and macros.
    """
    if not _ARTIFACT_ID_RE.match(artifact_id):
        raise ArtifactError(f"artifact id {artifact_id!r} must match {_ARTIFACT_ID_RE.pattern}")
    if not _SUFFIX_RE.match(suffix):
        raise ArtifactError(f"artifact suffix {suffix!r} must match {_SUFFIX_RE.pattern}")
    if not _ARTIFACT_ID_RE.match(instance_id) and not instance_id.isalnum():
        raise ArtifactError(f"instance id {instance_id!r} is not a safe path segment")

    art_dir = recordings_dir / _ARTIFACT_DIRNAME / instance_id
    art_dir.mkdir(parents=True, exist_ok=True)
    # Resolve-and-contain AFTER mkdir so a pre-existing symlinked directory is
    # followed and then rejected, rather than being silently written through.
    contained_dir = reject_unsafe_path(art_dir, recordings_dir, label="plugin artifact directory")
    # secure_artifact_tree, NOT secure_directory: the former gates on
    # OCTOWRIGHT_RECORDINGS_PRIVATE (these files live under the recordings
    # root, so that is the policy that governs them) while the latter gates on
    # the PROFILES policy, and it also locks the intermediate
    # ``session-artifacts/`` directory — a world-readable intermediate would
    # leak session ids from the path names alone, which is the same argument
    # its docstring already makes for the nested captures tree.
    secure_artifact_tree(contained_dir, recordings_dir)

    path = contained_dir / f"{artifact_id}{suffix}"
    relative = str(path.relative_to(recordings_dir.resolve()))
    return ArtifactHandle(
        artifact_id=artifact_id,
        path=path,
        _recorder=recorder,
        _relative_path=relative,
    )
```

- [ ] **Step 4: Add `PluginContext.artifact`**

In `src/octowright/plugins/session_launch.py`, add to the imports:

```python
from octowright.plugins.artifacts import ArtifactHandle, reserve_artifact
```

and add this method to `PluginContext`, immediately after `redaction_mode`:

```python
    def artifact(self, session: Any, name: str, suffix: str) -> ArtifactHandle:
        """Reserve a contained side-artifact path for ``session``.

        The plugin writes to the returned ``.path`` and then calls
        ``.commit(mime_type=...)``. It never composes a path itself.
        """
        return reserve_artifact(
            recorder=session.recorder,
            instance_id=session.instance_id,
            recordings_dir=self.recordings_dir,
            artifact_id=name,
            suffix=suffix,
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_artifacts.py -v`
Expected: all pass.

- [ ] **Step 6: Verify nothing regressed**

Run: `uv run --active pytest tests/plugins -q`
Run: `uv run --active python scripts/check_vulture.py`
Expected: green; vulture reports no findings.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/plugins/artifacts.py src/octowright/plugins/session_launch.py tests/plugins/test_artifacts.py
git commit -m "feat(plugins): contained side artifacts with reserve/commit

Plugins get real files without getting a path composer. Every
disk-containment bug this project has paid for lived in path composition,
so core issues the path, creates and locks the directory before handing it
over, and the plugin only writes and commits. Commit writes an
artifact_registered control row carrying a path relative to the recordings
root, so registration survives close, restart, and plugin uninstall — an
in-memory note would be invisible to exactly the readers that need it."
```

---

## Task 2: Reading artifacts back

Registration only pays off if a reader can turn those rows back into paths it trusts. The stored path is relative, so a recording moved between machines still resolves — and it is re-contained on read, so a hand-edited recording cannot point the media route at `/etc/passwd`.

**Files:**
- Modify: `src/octowright/plugins/artifacts.py`
- Test: `tests/plugins/test_artifact_reads.py`

**Interfaces:**
- Consumes: `ARTIFACT_MIME_ALLOWLIST`, `reserve_artifact` (Task 1).
- Produces:
  - `octowright.plugins.artifacts.RegisteredArtifact` — frozen dataclass with `artifact_id: str`, `path: Path`, `mime_type: str`
  - `octowright.plugins.artifacts.read_registered_artifacts(log_path: Path, recordings_dir: Path) -> list[RegisteredArtifact]`

- [ ] **Step 1: Write the failing tests**

Create `tests/plugins/test_artifact_reads.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

from octowright.plugins.artifacts import read_registered_artifacts, reserve_artifact
from octowright.recorder import Recorder


def _recording(tmp_path: Path) -> tuple[Recorder, Path]:
    log_path = tmp_path / "20260823T000000Z-refkind-refsess01.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    return recorder, log_path


def _commit(recorder: Recorder, root: Path, body: str) -> Path:
    handle = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=root, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text(body)
    handle.commit(mime_type="text/plain")
    return handle.path


def test_committed_artifact_reads_back_as_an_absolute_contained_path(tmp_path):
    recorder, log_path = _recording(tmp_path)
    _commit(recorder, tmp_path, "hello")
    recorder.close()

    found = read_registered_artifacts(log_path, tmp_path)
    assert [a.artifact_id for a in found] == ["transcript"]
    assert found[0].path.is_absolute()
    assert found[0].path.read_text() == "hello"
    assert found[0].mime_type == "text/plain"


def test_uncommitted_artifact_is_invisible(tmp_path):
    recorder, log_path = _recording(tmp_path)
    handle = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=tmp_path, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text("orphan")
    recorder.close()
    assert read_registered_artifacts(log_path, tmp_path) == []


def test_last_commit_of_an_id_wins(tmp_path):
    recorder, log_path = _recording(tmp_path)
    _commit(recorder, tmp_path, "v1")
    _commit(recorder, tmp_path, "v2")
    recorder.close()

    found = read_registered_artifacts(log_path, tmp_path)
    assert len(found) == 1
    assert found[0].path.read_text() == "v2"


def test_a_row_pointing_outside_the_root_is_dropped(tmp_path):
    recorder, log_path = _recording(tmp_path)
    recorder.record_control(
        "artifact_registered", artifact_id="evil", path="../../../../etc/passwd", mime_type="text/plain"
    )
    recorder.close()
    assert read_registered_artifacts(log_path, tmp_path) == []


def test_a_row_with_a_disallowed_mime_type_is_dropped(tmp_path):
    recorder, log_path = _recording(tmp_path)
    path = _commit(recorder, tmp_path, "x")
    # Bypass commit's own allowlist check to simulate a hand-edited recording.
    recorder.record_control(
        "artifact_registered",
        artifact_id="transcript",
        path=str(path.relative_to(tmp_path.resolve())),
        mime_type="application/x-msdownload",
    )
    recorder.close()
    assert read_registered_artifacts(log_path, tmp_path) == []


def test_a_missing_file_is_dropped(tmp_path):
    recorder, log_path = _recording(tmp_path)
    recorder.record_control(
        "artifact_registered", artifact_id="gone", path="session-artifacts/refsess01/gone.txt", mime_type="text/plain"
    )
    recorder.close()
    assert read_registered_artifacts(log_path, tmp_path) == []


def test_an_unparsable_line_does_not_break_the_scan(tmp_path):
    recorder, log_path = _recording(tmp_path)
    _commit(recorder, tmp_path, "hello")
    recorder.close()
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    assert [a.artifact_id for a in read_registered_artifacts(log_path, tmp_path)] == ["transcript"]


def test_a_recording_that_does_not_exist_yields_nothing(tmp_path):
    assert read_registered_artifacts(tmp_path / "absent.jsonl", tmp_path) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_artifact_reads.py -v`
Expected: FAIL — `ImportError: cannot import name 'read_registered_artifacts'`.

- [ ] **Step 3: Add the read side**

Append to `src/octowright/plugins/artifacts.py`:

```python
@dataclass(frozen=True)
class RegisteredArtifact:
    """A committed artifact, re-resolved and re-contained at read time."""

    artifact_id: str
    path: Path
    mime_type: str


def read_registered_artifacts(log_path: Path, recordings_dir: Path) -> list[RegisteredArtifact]:
    """Return the artifacts a recording registered, newest commit per id.

    Every row is re-validated rather than trusted: the recording is a file on
    disk that a local user can edit, so a stored path is re-resolved against
    the recordings root and a stored mime type is re-checked against the
    allowlist. A row that fails either check, or whose file is gone, is
    dropped rather than raising — one bad row must not hide the good ones.
    """
    found: dict[str, RegisteredArtifact] = {}
    try:
        raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for raw in raw_lines:
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("action") != "artifact_registered":
            continue
        artifact = _registered_from_row(entry, recordings_dir)
        if artifact is not None:
            found[artifact.artifact_id] = artifact

    return [found[key] for key in sorted(found)]


def _registered_from_row(entry: dict[str, object], recordings_dir: Path) -> RegisteredArtifact | None:
    artifact_id = entry.get("artifact_id")
    stored = entry.get("path")
    mime_type = entry.get("mime_type")
    if not isinstance(artifact_id, str) or not isinstance(stored, str) or not isinstance(mime_type, str):
        return None
    if mime_type not in ARTIFACT_MIME_ALLOWLIST:
        return None
    try:
        resolved = reject_unsafe_path(recordings_dir / stored, recordings_dir, label="registered artifact")
    except ValueError:
        return None
    if not resolved.is_file():
        return None
    return RegisteredArtifact(artifact_id=artifact_id, path=resolved, mime_type=mime_type)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_artifact_reads.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/plugins/artifacts.py tests/plugins/test_artifact_reads.py
git commit -m "feat(plugins): read committed artifacts back, re-validated

A recording is a file a local user can edit, so a stored path is
re-resolved against the recordings root and a stored mime type re-checked
against the allowlist at read time rather than trusted. A row that fails
either check, or whose file is gone, is dropped instead of raising — one
bad row must not hide the good ones."
```

---

## Task 3: `session_start` in closed-recording discovery

`_read_first_opening` hardcodes `("launch", "terminal_start")` and `_summarise_recording` hardcodes the terminal branch. Making that registry-driven would be wrong, because recordings outlive plugins: uninstall a plugin and its recordings would degrade to `kind: "unknown"` with null metadata, losing renderer selection and artifact association.

Since core owns the launch transaction, core writes the opening row itself, and discovery reads `kind` off it without knowing what kinds exist.

**Terminal keeps its `terminal_start` branch.** It is unmoved until step 5, so discovery must recognize all three shapes.

**Files:**
- Modify: `src/octowright/http/discovery.py` (`_read_first_opening` ~line 61, `_summarise_recording` ~line 85)
- Test: `tests/plugins/test_discovery_session_start.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new public names; `_summarise_recording` gains a `session_start` branch.

- [ ] **Step 1: Write the failing tests**

Create `tests/plugins/test_discovery_session_start.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

from octowright.http.discovery import _summarise_recording
from octowright.recorder import Recorder


def _write(tmp_path: Path, name: str, opening: dict) -> Path:
    log_path = tmp_path / name
    recorder = Recorder(log_path)
    fields = dict(opening)
    action = fields.pop("action")
    if action == "session_start":
        recorder.record_control(action, **fields)
    else:
        recorder.record(action, **fields)
    recorder.close()
    return log_path


def test_session_start_supplies_kind_label_and_profile(tmp_path):
    log_path = _write(
        tmp_path,
        "20260823T000000Z-refkind-sessionzz01.jsonl",
        {"action": "session_start", "kind": "refkind", "label": "demo", "profile": "tanuki"},
    )
    summary = _summarise_recording(log_path)
    assert summary is not None
    assert summary["kind"] == "refkind"
    assert summary["label"] == "demo"
    assert summary["profile"] == "tanuki"
    assert summary["live"] is False


def test_a_kind_whose_plugin_is_gone_still_classifies(tmp_path):
    # The whole point: no plugin is installed here, and the recording is still
    # reported with its real kind rather than degrading to "unknown".
    log_path = _write(
        tmp_path,
        "20260823T000000Z-neverinstalled-sessionzz01.jsonl",
        {"action": "session_start", "kind": "neverinstalled", "label": None, "profile": None},
    )
    summary = _summarise_recording(log_path)
    assert summary is not None
    assert summary["kind"] == "neverinstalled"


def test_browser_launch_rows_are_unchanged(tmp_path):
    log_path = _write(
        tmp_path,
        "20260823T000000Z-chromium-sessionzz01.jsonl",
        {"action": "launch", "kind": "chromium", "label": "shop", "profile": "tanuki", "url": "https://x.test"},
    )
    summary = _summarise_recording(log_path)
    assert summary is not None
    assert summary["kind"] == "chromium"
    assert summary["url"] == "https://x.test"


def test_terminal_start_rows_are_unchanged(tmp_path):
    # Terminal is unmoved until step 5; its recordings must keep classifying.
    log_path = _write(
        tmp_path,
        "20260823T000000Z-terminal-sessionzz01.jsonl",
        {"action": "terminal_start", "connector_type": "pty"},
    )
    summary = _summarise_recording(log_path)
    assert summary is not None
    assert summary["kind"] == "terminal"
    assert summary["connector_type"] == "pty"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_discovery_session_start.py -v`
Expected: FAIL — the two `session_start` tests report `kind == "unknown"`, because `_read_first_opening` does not recognize the action.

- [ ] **Step 3: Teach discovery the uniform opening row**

In `src/octowright/http/discovery.py`, replace `_read_first_opening`'s docstring and action tuple:

```python
def _read_first_opening(jsonl_path: Path) -> dict[str, Any] | None:
    """Find a recording's opening event.

    Three shapes exist and all three must classify:

    * ``launch`` — a browser session.
    * ``session_start`` — the uniform row core's plugin launch transaction
      writes. It carries ``kind``/``label``/``profile``, so discovery
      classifies a plugin's recording with ZERO plugin knowledge. That matters
      because recordings outlive plugins: registry-driven classification would
      turn every recording of an uninstalled plugin into ``unknown``.
    * ``terminal_start`` — the built-in terminal subsystem, which still writes
      its own opening row until it moves out to a plugin.
    """
    try:
        with jsonl_path.open(encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("action") in ("launch", "session_start", "terminal_start"):
                    return entry
    except OSError:
        return None
    return None
```

In `_summarise_recording`, add this branch immediately **before** the existing `terminal_start` branch:

```python
    if opening.get("action") == "session_start":
        # Core wrote this row, so every field is core's own and needs no
        # plugin to interpret it.
        return {
            "id": instance_id,
            "kind": opening.get("kind") or "unknown",
            "label": opening.get("label"),
            "profile": opening.get("profile"),
            "url": opening.get("url"),
            "started_at": started,
            "live": False,
            "log_path": str(jsonl_path),
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_discovery_session_start.py -v`
Expected: 4 passed.

- [ ] **Step 5: Verify existing discovery behaviour is intact**

Run: `uv run --active pytest -k "discovery or sessions" -q` and `uv run --active pytest tests/terminal -q`.
Expected: green. Report the exact commands you ran and their counts.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/http/discovery.py tests/plugins/test_discovery_session_start.py
git commit -m "feat(http): classify closed recordings from the session_start row

Recordings outlive plugins, so classification must not need one. Core owns
the launch transaction and writes a uniform opening row carrying
kind/label/profile; discovery reads it and never learns what kinds exist.
Registry-driven classification would instead turn every recording of an
uninstalled plugin into kind: unknown with null metadata, losing renderer
selection and artifact association. Browser launch and terminal_start rows
are untouched."
```

---

## Task 4: The HTTP registry seam and registry-driven live list

`_live_summary` is already getattr-defensive and, per its own comment, terminal sessions "serialize through it cleanly." It needs no changes — only its caller does.

**Files:**
- Create: `src/octowright/http/routes/_session_kinds.py`
- Modify: `src/octowright/http/state.py` (add a `plugin_registry` property to `_StateModule` after the `terminal_pool` setter; add to the `TYPE_CHECKING` block and to `__all__`)
- Modify: `src/octowright/http/routes/sessions.py` (`list_sessions`, line 42)
- Test: `tests/plugins/test_dashboard_registry.py`

**Interfaces:**
- Consumes: `octowright.server.plugin_state.registry() -> PluginRegistry`; `PluginRegistry.pools() -> dict[str, SessionPool]`, `.get_plugin(kind) -> LoadedPlugin` (with `.pool`, `.descriptor`).
- Produces:
  - `octowright.http.state.plugin_registry` — read-only module property
  - `octowright.http.routes._session_kinds.iter_plugin_sessions() -> Iterator[Any]`
  - `octowright.http.routes._session_kinds.find_plugin_session(instance_id: str) -> tuple[str, Any] | None` — returns `(kind, session)`

- [ ] **Step 1: Write the failing tests**

Create `tests/plugins/test_dashboard_registry.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from octowright.plugins.registry import PluginRegistry
from octowright.server import plugin_state


@dataclass
class _Session:
    instance_id: str
    kind: str = "refkind"
    label: str | None = None
    profile: str | None = None
    url: str | None = None
    log_path: Path = Path("/tmp/x.jsonl")
    protected: bool = False
    started_at: str = "2026-08-23T00:00:00Z"
    recorder: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Pool:
    sessions: dict[str, _Session] = field(default_factory=dict)

    def maybe_get(self, instance_id: str) -> _Session | None:
        return self.sessions.get(instance_id)

    def iter_sessions(self):
        return iter(list(self.sessions.values()))


class _Descriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None
    frontend = None

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> None:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {"id": session.instance_id, "kind": session.kind, "refkind_specific": True}


@pytest.fixture
def registered():
    """Install a one-plugin registry and restore the real one afterwards."""
    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _Pool({"refsess01": _Session("refsess01")})
    reg.register(_Descriptor(), pool=pool, adapter=None, discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg, pool
    finally:
        plugin_state.set_registry(original)


def test_state_exposes_the_live_registry(registered):
    from octowright.http import state

    reg, _ = registered
    assert state.plugin_registry is reg


def test_iter_plugin_sessions_spans_every_registered_pool(registered):
    from octowright.http.routes._session_kinds import iter_plugin_sessions

    assert [s.instance_id for s in iter_plugin_sessions()] == ["refsess01"]


def test_find_plugin_session_returns_kind_and_session(registered):
    from octowright.http.routes._session_kinds import find_plugin_session

    found = find_plugin_session("refsess01")
    assert found is not None
    kind, session = found
    assert kind == "refkind"
    assert session.instance_id == "refsess01"
    assert find_plugin_session("nope") is None


def test_no_plugins_registered_is_an_empty_iteration():
    from octowright.http.routes._session_kinds import find_plugin_session, iter_plugin_sessions

    assert list(iter_plugin_sessions()) == []
    assert find_plugin_session("refsess01") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_dashboard_registry.py -v`
Expected: FAIL — `AttributeError: module 'octowright.http.state' has no attribute 'plugin_registry'`, and a missing `_session_kinds` module.

- [ ] **Step 3: Add the state seam**

In `src/octowright/http/state.py`, add to `_StateModule` after the `terminal_pool` setter:

```python
    @property
    def plugin_registry(self) -> Any:
        # The live session-kind plugin registry. Forwarded through the same
        # seam as the pools so HTTP-layer code only ever reads plugin state
        # via ``state.<name>``. Deliberately read-only: the registry is
        # replaced through ``plugin_state.set_registry``, and a second write
        # path would let the two disagree.
        from octowright.server import plugin_state

        return plugin_state.registry()
```

Add `plugin_registry: Any` to the `TYPE_CHECKING` block beside `terminal_pool`, and `"plugin_registry"` to `__all__` in its sorted position (the list is alphabetical; it goes immediately before `"pool"`).

- [ ] **Step 4: Add the dispatch module**

Create `src/octowright/http/routes/_session_kinds.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Registry dispatch for the HTTP session routes.

Lives beside the routes rather than inside ``sessions.py`` because "resolve a
session across every registered kind" is one responsibility with its own tests,
and ``sessions.py`` is already the largest module in this package.

Core keeps no parallel session table: a plugin's ``SessionPool`` is the single
registry for its kind, so every lookup here iterates the registered pools.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from octowright.http import state


def iter_plugin_sessions() -> Iterator[Any]:
    """Yield every live session across every registered plugin pool."""
    for pool in state.plugin_registry.pools().values():
        yield from pool.iter_sessions()


def find_plugin_session(instance_id: str) -> tuple[str, Any] | None:
    """Resolve ``instance_id`` across registered pools.

    Returns ``(kind, session)`` or ``None``. Instance ids are unique across
    all pools — core enforces that at launch commit — so the first match is
    the only match.
    """
    for kind, pool in state.plugin_registry.pools().items():
        session = pool.maybe_get(instance_id)
        if session is not None:
            return kind, session
    return None
```

- [ ] **Step 5: Wire the live list**

In `src/octowright/http/routes/sessions.py`, add the import:

```python
from octowright.http.routes._session_kinds import iter_plugin_sessions
```

and extend `list_sessions`:

```python
async def list_sessions(_request: Request) -> JSONResponse:
    pool = state.pool
    live = [_live_summary(s) for s in pool.iter_sessions()]
    # Terminal sessions live in a separate pool that only exists when the
    # optional `octowright[terminal]` extra is installed. `_live_summary` is
    # getattr-defensive, so terminal sessions serialize through it cleanly.
    terminal_pool = state.terminal_pool
    if terminal_pool is not None:
        live += [_live_summary(s) for s in terminal_pool.iter_sessions()]
    # Session-kind plugins serialize through the same getattr-defensive
    # summariser. Terminal keeps its own branch above until it moves out to a
    # plugin of its own.
    live += [_live_summary(s) for s in iter_plugin_sessions()]
    live_paths = {s["log_path"] for s in live}
    closed = _closed_sessions(state.RECORDINGS_DIR, live_paths)
    return JSONResponse({"live": live, "closed": closed})
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_dashboard_registry.py -v`
Expected: 4 passed.

Run: `uv run --active pytest -k "sessions" -q` and `uv run --active pytest tests/terminal -q`.
Expected: green. Report what you ran.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/http/state.py src/octowright/http/routes/_session_kinds.py \
        src/octowright/http/routes/sessions.py tests/plugins/test_dashboard_registry.py
git commit -m "feat(http): registry-driven live session list

state.plugin_registry joins pool/scenario_pool/terminal_pool on the single
module-property seam, read-only because the registry is replaced through
plugin_state.set_registry and a second write path would let the two
disagree. Dispatch lives in its own module because resolving a session across
every registered kind is one responsibility with its own tests."
```

---

## Task 5: Registry-driven session detail

`_terminal_session_detail` short-circuits before the browser-only builder because terminal sessions have no page/console/video. That short-circuit is right; being hardcoded to one kind is not. Core dispatches by kind through the registry and falls back to the browser builder.

Committed Tier-2 artifacts appear in the payload so the dashboard can link them.

**Files:**
- Modify: `src/octowright/http/routes/_session_kinds.py`
- Modify: `src/octowright/http/routes/sessions.py` (`session_detail`, line 177)
- Test: `tests/plugins/test_dashboard_registry.py` (extend)

**Interfaces:**
- Consumes: `find_plugin_session` (Task 4); `read_registered_artifacts` (Task 2); `LoadedPlugin.descriptor.session_detail(session) -> dict`.
- Produces: `octowright.http.routes._session_kinds.plugin_session_detail(kind: str, session: Any) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/test_dashboard_registry.py`:

```python
def test_plugin_session_detail_uses_the_descriptor(registered):
    from octowright.http.routes._session_kinds import plugin_session_detail

    _, pool = registered
    detail = plugin_session_detail("refkind", pool.sessions["refsess01"])
    assert detail["refkind_specific"] is True
    assert detail["kind"] == "refkind"
    assert detail["artifacts"] == []


def test_plugin_session_detail_includes_committed_artifacts(registered, tmp_path, monkeypatch):
    from octowright.http import state as http_state
    from octowright.http.routes._session_kinds import plugin_session_detail
    from octowright.plugins.artifacts import reserve_artifact
    from octowright.recorder import Recorder

    log_path = tmp_path / "20260823T000000Z-refkind-refsess01.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    handle = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=tmp_path, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text("hello")
    handle.commit(mime_type="text/plain")
    recorder.close()

    _, pool = registered
    session = pool.sessions["refsess01"]
    session.log_path = log_path
    monkeypatch.setattr(http_state, "RECORDINGS_DIR", tmp_path)

    detail = plugin_session_detail("refkind", session)
    assert [a["artifact_id"] for a in detail["artifacts"]] == ["transcript"]
    assert detail["artifacts"][0]["mime_type"] == "text/plain"
    # The absolute path is deliberately NOT exposed to the dashboard.
    assert "path" not in detail["artifacts"][0]


def test_a_descriptor_that_raises_yields_a_degraded_detail(registered):
    from octowright.http.routes._session_kinds import plugin_session_detail

    class _Boom(_Descriptor):
        def session_detail(self, session: Any) -> dict[str, Any]:
            raise RuntimeError("plugin detail exploded")

    reg, pool = registered
    reg.register(_Boom(), pool=pool, adapter=None, discovered=None)
    detail = plugin_session_detail("refkind", pool.sessions["refsess01"])
    assert detail["id"] == "refsess01"
    assert detail["kind"] == "refkind"
    assert "detail_error" in detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_dashboard_registry.py -v`
Expected: FAIL — `ImportError: cannot import name 'plugin_session_detail'`.

- [ ] **Step 3: Add the detail builder**

Append to `src/octowright/http/routes/_session_kinds.py`:

```python
def plugin_session_detail(kind: str, session: Any) -> dict[str, Any]:
    """Build a plugin session's dashboard detail payload.

    A plugin raising here is caught and rendered as a degraded detail rather
    than a 500: an enabled plugin shares the leader's process, but a bad
    detail builder must not take a dashboard page down with it.

    Artifacts are reported by id and mime type only. The absolute path stays
    server-side — the dashboard fetches through the artifact route, which
    re-validates containment on every request.
    """
    from octowright.plugins.artifacts import read_registered_artifacts

    try:
        detail = dict(state.plugin_registry.get_plugin(kind).descriptor.session_detail(session))
    except Exception as exc:  # a bad plugin must not 500 the dashboard
        state.log.warning(
            "octowright.http.plugin_session_detail_failed",
            kind=kind,
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )
        detail = {"id": getattr(session, "instance_id", None), "kind": kind, "detail_error": repr(exc)}

    artifacts = read_registered_artifacts(Path(session.log_path), Path(state.RECORDINGS_DIR))
    detail["artifacts"] = [
        {"artifact_id": a.artifact_id, "mime_type": a.mime_type, "bytes": a.path.stat().st_size} for a in artifacts
    ]
    return detail
```

Add `from pathlib import Path` to the module's imports.

- [ ] **Step 4: Wire the route**

In `src/octowright/http/routes/sessions.py`, extend the import to
`from octowright.http.routes._session_kinds import find_plugin_session, iter_plugin_sessions, plugin_session_detail`
and extend `session_detail`:

```python
async def session_detail(request: Request) -> JSONResponse:
    sid = request.path_params["id"]
    # Terminal sessions are browser-shaped only in the summary; short-circuit
    # before the browser-only detail builder.
    terminal_pool = state.terminal_pool
    if terminal_pool is not None:
        term = terminal_pool.maybe_get(sid)
        if term is not None:
            return JSONResponse(_terminal_session_detail(term))
    # Same short-circuit, now registry-driven: a plugin's session has no
    # page/console/video either, and its own descriptor knows its shape.
    plugin_found = find_plugin_session(sid)
    if plugin_found is not None:
        kind, plugin_session = plugin_found
        return JSONResponse(plugin_session_detail(kind, plugin_session))
    live = _live_session_or_none(sid)
    if live is not None:
        return await _live_session_detail_response(live)
    return _closed_session_detail_response(sid)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_dashboard_registry.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/http/routes/_session_kinds.py src/octowright/http/routes/sessions.py \
        tests/plugins/test_dashboard_registry.py
git commit -m "feat(http): registry-driven session detail with artifacts

The terminal short-circuit before the browser-only detail builder was the
right shape and the wrong scope: a plugin's session has no page, console or
video either. Dispatch by kind through the registry, fall back to the
browser builder. A plugin raising in its own detail builder degrades to a
minimal payload rather than 500-ing the page. Committed artifacts are
reported by id and mime type; the absolute path stays server-side."
```

---

## Task 6: Registry-driven close

`ProtectedTerminalCloseError` is imported directly by `sessions.py` — the single core-imports-a-plugin-by-name line the whole design inverts. Core now also catches `ProtectedSessionCloseError`, which plugins raise.

The terminal branch stays until step 5, so both are handled.

**Files:**
- Modify: `src/octowright/http/routes/_session_kinds.py`
- Modify: `src/octowright/http/routes/sessions.py` (`session_close`, line 329)
- Test: `tests/plugins/test_dashboard_registry.py` (extend)

**Interfaces:**
- Consumes: `find_plugin_session` (Task 4); `octowright.plugins.errors.ProtectedSessionCloseError`; `SessionPool.close(instance_id, *, force) -> CloseResult`.
- Produces: `octowright.http.routes._session_kinds.close_plugin_session(instance_id: str, *, force: bool) -> dict[str, Any] | None` — the close payload, or `None` when `instance_id` is not a plugin session; raises `ProtectedSessionCloseError` for the 409 path.

- [ ] **Step 1: Write the failing tests**

Append to `tests/plugins/test_dashboard_registry.py`:

```python
async def test_close_plugin_session_closes_and_reports(registered):
    from octowright.http.routes._session_kinds import close_plugin_session

    _, pool = registered
    closed: list[str] = []

    async def _close(instance_id: str, *, force: bool = False):
        closed.append(instance_id)
        pool.sessions.pop(instance_id)
        return {"instance_id": instance_id, "kind": "refkind", "closed": True}

    pool.close = _close  # type: ignore[attr-defined]
    result = await close_plugin_session("refsess01", force=False)
    assert result == {"instance_id": "refsess01", "kind": "refkind", "closed": True}
    assert closed == ["refsess01"]


async def test_close_plugin_session_returns_none_for_an_unknown_id(registered):
    from octowright.http.routes._session_kinds import close_plugin_session

    assert await close_plugin_session("nope", force=False) is None


async def test_protected_close_propagates_for_the_409_mapping(registered):
    from octowright.http.routes._session_kinds import close_plugin_session
    from octowright.plugins.errors import ProtectedSessionCloseError

    _, pool = registered

    async def _close(instance_id: str, *, force: bool = False):
        if not force:
            raise ProtectedSessionCloseError(f"refkind {instance_id!r} is protected; pass force=True to close it")
        return {"instance_id": instance_id, "closed": True}

    pool.close = _close  # type: ignore[attr-defined]
    with pytest.raises(ProtectedSessionCloseError):
        await close_plugin_session("refsess01", force=False)
    assert await close_plugin_session("refsess01", force=True) == {"instance_id": "refsess01", "closed": True}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_dashboard_registry.py -v`
Expected: FAIL — `ImportError: cannot import name 'close_plugin_session'`.

- [ ] **Step 3: Add the close helper**

Append to `src/octowright/http/routes/_session_kinds.py`:

```python
async def close_plugin_session(instance_id: str, *, force: bool) -> dict[str, Any] | None:
    """Close ``instance_id`` if it belongs to a registered plugin pool.

    Returns the pool's ``CloseResult`` as a plain dict, or ``None`` when the id
    is not a plugin session so the caller falls through to the browser path.
    ``ProtectedSessionCloseError`` propagates — the route maps it to 409,
    mirroring the browser and terminal paths.
    """
    found = find_plugin_session(instance_id)
    if found is None:
        return None
    kind, _session = found
    pool = state.plugin_registry.pools()[kind]
    return dict(await pool.close(instance_id, force=force))
```

- [ ] **Step 4: Wire the route**

In `src/octowright/http/routes/sessions.py`, add these imports:

```python
from octowright.http.routes._session_kinds import (
    close_plugin_session,
    find_plugin_session,
    iter_plugin_sessions,
    plugin_session_detail,
)
from octowright.plugins.errors import ProtectedSessionCloseError
```

Add a helper beside `_maybe_close_terminal`:

```python
async def _maybe_close_plugin(sid: str, *, force: bool) -> JSONResponse | None:
    """Close ``sid`` if it is a live plugin session, else return ``None``.

    ``ProtectedSessionCloseError`` is core's own type, raised by the plugin —
    which is what removes this module's direct import of a specific plugin's
    error class.
    """
    try:
        result = await close_plugin_session(sid, force=force)
    except ProtectedSessionCloseError as e:
        return JSONResponse({"error": str(e).replace("force=True", "force=true")}, status_code=409)
    if result is None:
        return None
    state.log.info("octowright.http.plugin_session_closed", instance_id=sid)
    await publish_dashboard_invalidation("sessions")
    return JSONResponse({"closed": True, "instance_id": sid, **result})
```

In `session_close`, call it immediately after the terminal one:

```python
    terminal_close = await _maybe_close_terminal(sid, force=force)
    if terminal_close is not None:
        return terminal_close
    plugin_close = await _maybe_close_plugin(sid, force=force)
    if plugin_close is not None:
        return plugin_close
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_dashboard_registry.py -v`
Expected: 10 passed.

Run: `uv run --active pytest tests/terminal -q`
Expected: green — the terminal close path is untouched.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/http/routes/_session_kinds.py src/octowright/http/routes/sessions.py \
        tests/plugins/test_dashboard_registry.py
git commit -m "feat(http): registry-driven session close

Core catches ProtectedSessionCloseError — its own type, raised by the
plugin — instead of importing a specific plugin's error class by name. The
409-on-protected-without-force mapping is unchanged, and the terminal
branch stays until terminal moves out to a plugin of its own."
```

---

## Task 7: Serving a committed artifact

The dashboard needs a way to fetch what it links. Every request re-resolves and re-contains the stored path, so a hand-edited recording cannot turn this route into an arbitrary file read.

**Files:**
- Modify: `src/octowright/http/routes/media.py` (handler beside `session_trace` line 130; route registration in `routes()` line 359)
- Test: `tests/plugins/test_artifact_route.py`

**Interfaces:**
- Consumes: `read_registered_artifacts` (Task 2); `octowright.http.exposure.guard_sensitive_http`.
- Produces: route `GET /api/sessions/{id}/artifacts/{artifact_id}`; handler `session_artifact`.

- [ ] **Step 1: Write the failing tests**

Create `tests/plugins/test_artifact_route.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.plugins.artifacts import reserve_artifact
from octowright.recorder import Recorder


def _session_with_artifact(root: Path, sid: str = "sessionzz01") -> Path:
    log_path = root / f"20260823T000000Z-refkind-{sid}.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    handle = reserve_artifact(
        recorder=recorder, instance_id=sid, recordings_dir=root, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text("recorded output")
    handle.commit(mime_type="text/plain")
    recorder.close()
    return log_path


@pytest.fixture
def client_with_recordings(tmp_path, monkeypatch):
    from starlette.testclient import TestClient

    from octowright.http import state as http_state
    from octowright.http.app import build_app

    monkeypatch.setattr(http_state, "RECORDINGS_DIR", tmp_path)
    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING", "0")
    return TestClient(build_app()), tmp_path


def test_committed_artifact_is_served(client_with_recordings):
    client, root = client_with_recordings
    _session_with_artifact(root)
    resp = client.get("/api/sessions/sessionzz01/artifacts/transcript")
    assert resp.status_code == 200
    assert resp.text == "recorded output"
    assert resp.headers["content-type"].startswith("text/plain")


def test_unknown_artifact_is_404(client_with_recordings):
    client, root = client_with_recordings
    _session_with_artifact(root)
    assert client.get("/api/sessions/sessionzz01/artifacts/nosuch").status_code == 404


def test_unknown_session_is_404(client_with_recordings):
    client, _ = client_with_recordings
    assert client.get("/api/sessions/zzz999zzz999/artifacts/transcript").status_code == 404


def test_a_traversing_artifact_id_is_refused(client_with_recordings):
    client, root = client_with_recordings
    _session_with_artifact(root)
    resp = client.get("/api/sessions/sessionzz01/artifacts/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (400, 404)


def test_a_row_pointing_outside_the_root_is_not_served(client_with_recordings, tmp_path):
    client, root = client_with_recordings
    log_path = root / "20260823T000000Z-refkind-sessionzz02.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    secret = tmp_path.parent / "secret-not-served.txt"
    secret.write_text("do not serve me")
    recorder.record_control("artifact_registered", artifact_id="leak", path=f"../{secret.name}", mime_type="text/plain")
    recorder.close()

    assert client.get("/api/sessions/sessionzz02/artifacts/leak").status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_artifact_route.py -v`
Expected: FAIL — 404 on the first test, because the route does not exist.

- [ ] **Step 3: Add the route**

In `src/octowright/http/routes/media.py`, add the handler beside `session_trace`:

```python
async def session_artifact(request: Request) -> Response:
    """GET /api/sessions/{id}/artifacts/{artifact_id} — serve a committed plugin artifact.

    Everything is re-derived from the recording on every request: the row's
    stored path is relative and is re-resolved against the recordings root,
    and its mime type is re-checked against the allowlist. A recording is a
    file a local user can edit, so neither is trusted from one request to the
    next.
    """
    from octowright.plugins.artifacts import read_registered_artifacts

    sid = request.path_params["id"]
    artifact_id = request.path_params["artifact_id"]
    if not _valid_session_id(sid):
        return JSONResponse({"error": "invalid session id"}, status_code=400)

    root = Path(state.RECORDINGS_DIR)
    for log_path in sorted(root.glob("*.jsonl")):
        if f"-{sid}" not in log_path.stem:
            continue
        for artifact in read_registered_artifacts(log_path, root):
            if artifact.artifact_id == artifact_id:
                return FileResponse(
                    path=str(artifact.path),
                    media_type=artifact.mime_type,
                    filename=artifact.path.name,
                )
    return JSONResponse({"error": "no such artifact for this session"}, status_code=404)
```

Register it in `routes()`, beside the trace route:

```python
(
    Route(
        "/api/sessions/{id}/artifacts/{artifact_id}",
        guard_sensitive_http(session_artifact),
        methods=["GET"],
    ),
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_artifact_route.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/http/routes/media.py tests/plugins/test_artifact_route.py
git commit -m "feat(http): serve committed plugin artifacts

Everything is re-derived from the recording per request: the stored path is
relative and re-resolved against the recordings root, and the mime type is
re-checked against the allowlist. A recording is a file a local user can
edit, so neither is trusted between requests."
```

---

## Task 8: Shutdown teardown for every registered pool

Today only the browser pool and the optional terminal pool are torn down at leader shutdown. A plugin pool that owns an SSH connection or a subprocess would survive the daemon exit.

**Files:**
- Modify: `src/octowright/cli/serve.py` (add beside `_close_terminal_pool_on_shutdown` line 554; call it in the `finally` block at line 548)
- Test: `tests/plugins/test_shutdown_teardown.py`

**Interfaces:**
- Consumes: `PluginRegistry.pools() -> dict[str, SessionPool]`.
- Produces: `octowright.cli.serve._close_plugin_pools_on_shutdown(registry: Any, *, log: Any) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/plugins/test_shutdown_teardown.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from octowright.cli.serve import _close_plugin_pools_on_shutdown


class _Recorder:
    def __init__(self) -> None:
        self.closed: list[bool] = []


class _Pool:
    def __init__(self, rec: _Recorder, *, boom: bool = False) -> None:
        self._rec = rec
        self._boom = boom

    async def close_all(self, *, force: bool = False) -> None:
        if self._boom:
            raise RuntimeError("teardown exploded")
        self._rec.closed.append(force)


class _Registry:
    def __init__(self, pools: dict[str, Any]) -> None:
        self._pools = pools

    def pools(self) -> dict[str, Any]:
        return self._pools


class _Log:
    def __init__(self) -> None:
        self.debug_calls: list[tuple[str, dict[str, Any]]] = []

    def debug(self, event: str, **fields: Any) -> None:
        self.debug_calls.append((event, fields))


async def test_every_registered_pool_is_force_closed():
    rec_a, rec_b = _Recorder(), _Recorder()
    registry = _Registry({"a": _Pool(rec_a), "b": _Pool(rec_b)})
    await _close_plugin_pools_on_shutdown(registry, log=_Log())
    assert rec_a.closed == [True]
    assert rec_b.closed == [True]


async def test_one_failing_pool_does_not_stop_the_others():
    rec = _Recorder()
    log = _Log()
    registry = _Registry({"bad": _Pool(_Recorder(), boom=True), "good": _Pool(rec)})
    await _close_plugin_pools_on_shutdown(registry, log=log)
    assert rec.closed == [True], "a failing pool must not abort teardown of the rest"
    assert any("plugin_pool_close_failed" in event for event, _ in log.debug_calls)


async def test_no_registry_is_a_no_op():
    await _close_plugin_pools_on_shutdown(None, log=_Log())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/plugins/test_shutdown_teardown.py -v`
Expected: FAIL — `ImportError: cannot import name '_close_plugin_pools_on_shutdown'`.

- [ ] **Step 3: Add the teardown**

In `src/octowright/cli/serve.py`, add beside `_close_terminal_pool_on_shutdown`:

```python
async def _close_plugin_pools_on_shutdown(registry: Any, *, log: Any) -> None:
    """Best-effort close of every registered session-kind plugin pool.

    Without this a plugin pool holding a subprocess, socket or SSH connection
    survives the daemon exit — survivable for a local PTY, not for a remote
    session. Each pool is closed independently so one plugin's bad teardown
    cannot strand the others.
    """
    if registry is None:
        return
    for kind, pool in registry.pools().items():
        try:
            await pool.close_all(force=True)
        except Exception as exc:  # best-effort teardown; don't block shutdown
            log.debug("shutdown.plugin_pool_close_failed", kind=kind, error=repr(exc))
```

and call it in the `finally` block, immediately after the terminal close:

```python
        await _close_terminal_pool_on_shutdown(_st.terminal_pool, log=_log)
        from octowright.server import plugin_state as _plugin_state

        await _close_plugin_pools_on_shutdown(_plugin_state.registry(), log=_log)
        await shutdown_browser_pool_on_shutdown(pool, log=_log)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_shutdown_teardown.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/cli/serve.py tests/plugins/test_shutdown_teardown.py
git commit -m "feat(cli): close every registered plugin pool at leader shutdown

Only the browser and optional terminal pools were torn down, so a plugin
pool holding a subprocess, socket or SSH connection survived the daemon
exit. Each pool closes independently: one plugin's bad teardown must not
strand the others."
```

---

## Task 9: The reference plugin grows a Tier-2 artifact

The reference plugin is the in-repo consumer that fails CI when the contract drifts. Step 1 gave it a pool, tools and protected close; artifacts are the seam this step adds, so it grows one.

**Files:**
- Modify: `tests/plugins/reference/pool.py`
- Test: `tests/plugins/test_reference_artifacts.py`

**Interfaces:**
- Consumes: `PluginContext.artifact(session, name, suffix)` (Task 1); `read_registered_artifacts` (Task 2).
- Produces: `ReferencePool.write_transcript(instance_id: str, body: str) -> str` — writes and commits a `transcript` artifact, returning its `artifact_id`.

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_reference_artifacts.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from octowright.plugins.artifacts import read_registered_artifacts
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from tests.plugins.reference.plugin import plugin


@pytest.fixture
def pool(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind=plugin.kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    return plugin.create_pool(ctx), tmp_path


async def test_reference_pool_writes_and_commits_a_transcript(pool):
    ref_pool, root = pool
    launched = await ref_pool.launch(label="demo")
    artifact_id = ref_pool.write_transcript(launched["instance_id"], "line one\nline two\n")
    assert artifact_id == "transcript"

    found = read_registered_artifacts(Path(launched["log_path"]), root)
    assert [a.artifact_id for a in found] == ["transcript"]
    assert found[0].path.read_text() == "line one\nline two\n"
    assert found[0].mime_type == "text/plain"


async def test_the_transcript_lives_inside_the_recordings_root(pool):
    ref_pool, root = pool
    launched = await ref_pool.launch()
    ref_pool.write_transcript(launched["instance_id"], "x")
    found = read_registered_artifacts(Path(launched["log_path"]), root)
    assert found[0].path.resolve().is_relative_to(root.resolve())


async def test_the_artifact_directory_is_owner_only(pool, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS_PRIVATE", "1")
    ref_pool, root = pool
    launched = await ref_pool.launch()
    ref_pool.write_transcript(launched["instance_id"], "x")
    found = read_registered_artifacts(Path(launched["log_path"]), root)
    assert stat.S_IMODE(found[0].path.parent.stat().st_mode) == 0o700


async def test_writing_a_transcript_for_an_unknown_session_raises(pool):
    ref_pool, _ = pool
    with pytest.raises(KeyError):
        ref_pool.write_transcript("nosuchid", "x")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_reference_artifacts.py -v`
Expected: FAIL — `AttributeError: 'ReferencePool' object has no attribute 'write_transcript'`.

- [ ] **Step 3: Give the reference pool an artifact**

In `tests/plugins/reference/pool.py`, add to `ReferencePool`:

```python
    def write_transcript(self, instance_id: str, body: str) -> str:
        """Write and commit a Tier-2 transcript artifact.

        The reference plugin's whole job is to exercise a seam from a
        consumer's side, so this deliberately uses the ordinary reserve →
        write → commit sequence rather than a shortcut: it never composes a
        path, and the artifact is invisible until commit.
        """
        session = self.get(instance_id)
        handle = self._ctx.artifact(session, "transcript", ".txt")
        handle.path.write_text(body, encoding="utf-8")
        handle.commit(mime_type="text/plain")
        return handle.artifact_id
```

`ReferencePool.__init__` already stores the context as `self._ctx`. Confirm that before writing; if the attribute has a different name, use the existing one and say so in your report.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_reference_artifacts.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run every gate**

Run: `uv run --active pytest tests/plugins -q`
Run: `uv run --active pytest -m "not live_browser and not memory_isolated" -q`
Run: `make lint` — report the exit code explicitly.
Expected: all green, `make lint` exit 0.

- [ ] **Step 6: Commit**

```bash
git add tests/plugins/reference/pool.py tests/plugins/test_reference_artifacts.py
git commit -m "test(plugins): reference plugin grows a Tier-2 artifact

The in-repo consumer that fails CI when the contract drifts now exercises
the artifact seam from a plugin author's side: reserve, write, commit, with
no path composed anywhere in plugin code."
```

---

## Done criteria

- `uv run --active pytest -m "not live_browser and not memory_isolated"` green.
- `make lint` exit 0.
- `tests/terminal/` unchanged and green — terminal still runs on its own path.
- A recording written by a kind whose plugin is not installed still classifies with its real kind.
- `octowright_status()["plugins"]` unchanged from step 1 (`[]` on a default install).
- No push, no PR, no `CHANGELOG.md` edit, no `.ci/vulture-baseline.json` edit.

## Not in this step

Deferred to later build steps, per spec §12:

- `ScenarioAdapter`, `BrowserScenarioAdapter`, derived capabilities, the group-by partition, `options:` replacing `connector_type` (step 3).
- `/api/plugins`, plugin asset serving, `mountStream`, core-owned page chrome, the fallback renderer (step 4).
- Deleting terminal from core and standing up `octowright-terminal` (step 5).

## Known gap this step surfaced but does not close

**`instance_id_from_recording_name` cannot parse a hyphenated plugin kind.**
`recorder.new_log_path` composes `{stamp}-{kind}-{instance_id}[-{label}]`, and
`http/artifacts.instance_id_from_recording_name` reads the id as
`stem.split("-")[2]`. That is exact only while both the kind and the instance id
are hyphen-free — true of every shipping session today (core kinds are
`chromium`/`firefox`/`webkit`/`terminal`, real ids are `uuid4().hex[:12]`), and
not guaranteed for plugins: `plugins/identity.py`'s `NAME_RE` permits `-` in a
kind, and a plugin supplies its own `instance_id`.

Two consequences, both currently unreachable because no plugin loads on a
default install:

- A plugin whose kind contains a hyphen (`my-plugin`) parses to `plugin`, so
  every artifact/video/trace/markdown lookup for its sessions 404s.
- A never-created session id that equals a prefix token of another session's
  kind or id resolves to that other session's recording.

The resolver is shared by `session_artifact`, `session_trace`, `session_video`,
`session_markdown` and `_screenshot_dir_for`, so this is one fix in one place,
not five.

**The obvious one-liner is wrong.** `stem.split("-", 2)[2]` looks like it widens
the id to "everything after the kind", but the label is appended *after* the
instance id, so for the common labeled case
`20260823T000000Z-chromium-abc123def456-my-repo` it returns
`abc123def456-my-repo`. Labels are the default (`get_default_label()` derives
username/repo), so that change would break resolution for nearly every real
browser recording. Any real fix has to disambiguate the id from the label —
by constraining plugin kinds/ids to be hyphen-free at the point core composes
the filename, or by changing the filename scheme itself.

Close this before plugin kinds become reachable (step 3–5), not after.

## Carried-forward findings from step 1

These were triaged fine-to-defer during step 1 and are listed here so they are not lost. None blocks this step; fix opportunistically only if a task already touches the code.

- `activate`'s core-tool-collision branch does not run the `on_rollback` hook, so a plugin refused there keeps its capability profile registered.
- `recording_truncated` is in `CONTROL_ACTIONS` but `_write_truncation_marker` still writes it directly, so that member has no writer and does not consume the control budget.
- `record_control` duplicates `record`'s encode/write/flush sequence.
- `PluginContext.redaction_mode()` has no test coverage. **This step is the natural place to fix it** — Task 1 touches `PluginContext` — but it is not required.
- `SessionLaunch.commit()` does not compare `record.label`/`record.profile` against what `begin_session` received.
- Two plugins registering the same capability-profile name is silent last-write-wins.
- `DuplicatePluginNameError` is constructed but never raised.
- `PluginContext.id_in_use` stays publicly callable without `exclude_kind`.

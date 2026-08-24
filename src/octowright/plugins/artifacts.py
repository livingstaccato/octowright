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
from octowright.plugins.identity import INSTANCE_ID_RE
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
#: enabled plugin serve arbitrary content back through the dashboard's own
#: origin, where the pairing bearer lives. Note ``image/svg+xml`` is active
#: content (it can carry ``<script>``) and this allowlist alone does not
#: neutralize it — what actually stops it from executing is the artifact
#: route's ``FileResponse`` serving with ``content_disposition_type="attachment"``,
#: never ``inline``. See the comment at ``http/routes/media.py::session_artifact``.
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
    # One regex, shared with the launch transaction that issued this id. The
    # earlier form here was ``_ARTIFACT_ID_RE.match(instance_id) or
    # instance_id.isalnum()`` -- a double branch that existed only because
    # nothing upstream validated the id, so this had to accept both the
    # letters-first artifact syntax and core's digit-leading ``uuid4().hex``.
    # ``begin_session`` now settles the syntax at composition time, so this is a
    # straight reuse rather than a second, looser opinion.
    if not INSTANCE_ID_RE.fullmatch(instance_id):
        raise ArtifactError(f"instance id {instance_id!r} must match {INSTANCE_ID_RE.pattern}")

    # Resolved once and reused: ``_lock_tree``'s walk from leaf to root is a
    # plain ``Path.relative_to`` segment comparison, not resolve-aware, so
    # handing it an unresolved root while the leaf is already resolved (as
    # ``reject_unsafe_path`` returns) makes the comparison fail whenever the
    # root is reached through a symlink hop (e.g. macOS ``/tmp`` ->
    # ``/private/tmp``) — the walk then silently stops without ever locking
    # the intermediate ``session-artifacts/`` directory. Both the security
    # walk and the stored relative path must agree on the same resolved root.
    resolved_root = recordings_dir.resolve()

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
    secure_artifact_tree(contained_dir, resolved_root)

    path = contained_dir / f"{artifact_id}{suffix}"
    relative = str(path.relative_to(resolved_root))
    return ArtifactHandle(
        artifact_id=artifact_id,
        path=path,
        _recorder=recorder,
        _relative_path=relative,
    )


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

    "Newest commit wins" applies to a failing row too: the per-id slot is
    overwritten by every row for that id, valid or not, so a later hand-edit
    that fails validation retracts an earlier valid commit rather than
    leaving it visible underneath. Only the final winner per id is kept.

    Streams the file rather than materializing it (``read_text().splitlines()``
    holds the whole file AND a list of every line resident at once — roughly
    double the file size). This is called per ``GET /api/sessions/{id}`` for a
    plugin session and per artifact fetch, and a streaming-output plugin is
    exactly the case that grows one recording without bound (see
    ``OCTOWRIGHT_RECORDING_MAX_BYTES``, off by default). Opened in binary and
    decoded per line, not via a text-mode handle, so a torn write mid-multibyte
    sequence raises ``UnicodeDecodeError`` scoped to the one line it broke
    (caught below) instead of a text iterator that can't isolate the bad byte
    range from its buffered read.
    """
    latest: dict[str, RegisteredArtifact | None] = {}
    try:
        fh = log_path.open("rb")
    except OSError:
        return []

    with fh:
        for raw_bytes in fh:
            try:
                line = raw_bytes.decode("utf-8").strip()
            except UnicodeDecodeError:
                # A torn write must not hide the good rows around it, same as
                # a malformed JSON line below.
                continue
            if not line:
                continue
            parsed = _parse_artifact_registered_line(line, recordings_dir)
            if parsed is None:
                continue
            artifact_id, artifact = parsed
            latest[artifact_id] = artifact

    return [artifact for _artifact_id, artifact in sorted(latest.items()) if artifact is not None]


def _parse_artifact_registered_line(line: str, recordings_dir: Path) -> tuple[str, RegisteredArtifact | None] | None:
    """Parse one JSONL line into ``(artifact_id, registered-or-None)``.

    Returns ``None`` when the line isn't an ``artifact_registered`` row at
    all (malformed JSON, wrong shape, or no usable id) -- distinct from a
    recognized row whose *contents* fail validation, which still reports its
    id so ``read_registered_artifacts`` can retract an earlier valid commit
    for it (see that function's "newest commit wins" note).
    """
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(entry, dict) or entry.get("action") != "artifact_registered":
        return None
    artifact_id = entry.get("artifact_id")
    if not isinstance(artifact_id, str):
        return None
    return artifact_id, _registered_from_row(entry, recordings_dir)


def _registered_from_row(entry: dict[str, object], recordings_dir: Path) -> RegisteredArtifact | None:
    artifact_id = entry.get("artifact_id")
    stored = entry.get("path")
    mime_type = entry.get("mime_type")
    if not isinstance(artifact_id, str) or not isinstance(stored, str) or not isinstance(mime_type, str):
        return None
    # The path and mime type are both re-validated below; the id was the one
    # row field taken on trust ("every row is re-validated" was incomplete).
    # An arbitrary hand-edited id would otherwise flow into the session-detail
    # payload and, from there, into the DOM.
    if not _ARTIFACT_ID_RE.match(artifact_id):
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

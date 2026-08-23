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

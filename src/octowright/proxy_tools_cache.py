# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The last ``tools/list`` answer a leader gave, kept for a slow start.

While a follower is still electing its leader, a ``tools/list`` waits for one
like any other call -- up to the election budget, about 40 seconds at the
defaults -- and a client whose own timeout for that call is shorter sees it
fail and starts the session with no tools. So the follower keeps a copy of the
leader's answer on disk (``defaults.TOOLS_LIST_CACHE_PATH``), and a
``tools/list`` that has waited ``GRACE_SECONDS`` while the leader is still
being elected is answered from it.

The copy is used only under the conditions the answer depends on: the same
Octowright version, ``OCTOWRIGHT_PROFILE`` and ``OCTOWRIGHT_PLUGINS`` (a daemon
the follower spawns inherits all three, and the profile decides which tools
register) and the same negotiated protocol version (the result is shaped for
it). Only a first page is answered or kept. The grace leaves a running leader
in charge of the answer: an election that finds one returns well inside it, so
only a cold or contended start reaches the copy. Anything else -- no copy, a
copy taken under other conditions, an unreadable file -- waits for the leader
exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCRequest, JSONRPCResponse
from provide.telemetry import get_logger

from octowright import defaults
from octowright._bridge_message_helpers import message_root
from octowright._paths import atomic_write_text
from octowright.version import VERSION

log = get_logger(__name__)

# How long a pre-leader tools/list waits for the leader before the copy answers.
GRACE_SECONDS = 5.0

# What this process last wrote: (path, sha256 of the body, the file's stat
# signature right after the write). ``remember`` runs on the bridge's event loop
# for every tools/list answer, and the answer rarely changes, so an unchanged
# answer to a file nobody has touched since is recognised from this and a stat,
# without reading the file back. Anything else -- another process rewrote it, it
# was removed, a different path -- falls through to the read-and-compare.
_last_written: tuple[Path, str, tuple[int, int, int]] | None = None


def _stat_signature(path: Path) -> tuple[int, int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _key(protocol_version: str) -> dict[str, str]:
    return {
        "version": VERSION,
        "profile": os.environ.get("OCTOWRIGHT_PROFILE", "").strip(),
        "plugins": os.environ.get("OCTOWRIGHT_PLUGINS", "").strip(),
        "protocol_version": protocol_version,
    }


def _first_page_request(message: SessionMessage | None) -> JSONRPCRequest | None:
    root = message_root(message) if message is not None else None
    if not isinstance(root, JSONRPCRequest) or root.method != "tools/list":
        return None
    params = root.params if isinstance(root.params, dict) else {}
    return None if params.get("cursor") is not None else root


def _protocol_version(handshake: Any) -> str | None:
    version = handshake.get("protocolVersion") if isinstance(handshake, dict) else None
    return version if isinstance(version, str) else None


def cached_answer(message: SessionMessage, handshake: Any) -> SessionMessage | None:
    """The kept answer to ``message``, a first-page ``tools/list``, for a client
    told ``handshake``; None when there is none for these conditions."""
    request = _first_page_request(message)
    protocol_version = _protocol_version(handshake)
    if request is None or protocol_version is None:
        return None
    try:
        kept = json.loads(defaults.TOOLS_LIST_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(kept, dict) or kept.get("key") != _key(protocol_version):
        return None
    result = kept.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return None
    return SessionMessage(JSONRPCResponse(jsonrpc="2.0", id=request.id, result=result))


def remember(request: SessionMessage | None, answer: SessionMessage, handshake: Any) -> None:
    """Keep the leader's ``answer`` to ``request`` when it is a whole first page
    of ``tools/list``. Best effort: a failed write leaves the old copy."""
    body = _cacheable_body(request, answer, handshake)
    if body is not None:
        _write_unless_unchanged(defaults.TOOLS_LIST_CACHE_PATH, body)


def _cacheable_body(request: SessionMessage | None, answer: SessionMessage, handshake: Any) -> str | None:
    """The file body for ``answer``, or None when it is not a whole first page."""
    root = message_root(answer)
    protocol_version = _protocol_version(handshake)
    if _first_page_request(request) is None or protocol_version is None or not isinstance(root, JSONRPCResponse):
        return None
    result = root.result
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list) or result.get("nextCursor"):
        return None
    return json.dumps({"key": _key(protocol_version), "result": result}, sort_keys=True)


def _write_unless_unchanged(path: Path, body: str) -> None:
    """Write ``body`` unless this process last wrote exactly it and the file is
    untouched since -- then not even a read is needed."""
    global _last_written
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    last = _last_written
    if last is not None and last[0] == path and last[1] == digest and last[2] == _stat_signature(path):
        return
    try:
        if not (path.is_file() and path.read_text(encoding="utf-8") == body):
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(path, body)
    except OSError as exc:
        log.debug("octowright.bridge.tools_list_cache_write_failed", path=str(path), error=repr(exc))
        return
    signature = _stat_signature(path)
    _last_written = None if signature is None else (path, digest, signature)

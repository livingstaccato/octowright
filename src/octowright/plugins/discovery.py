# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Entry-point discovery and daemon-scoped enable resolution.

Discovery is deliberately **metadata only**. ``importlib.metadata`` yields an
entry point's name, target, and owning distribution without importing it, and
that is the whole trust boundary: installing a package — including a
transitive dependency — must not silently extend a browser-driving daemon.
Resolving the descriptor (and with it ``kind`` and ``plugin_api_version``)
requires an import, so those fields exist only for a plugin an operator
explicitly enabled.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from importlib.metadata import EntryPoint
from importlib.metadata import entry_points as _entry_points
from pathlib import Path

import yaml
from provide.telemetry import get_logger

from octowright import config_paths
from octowright.plugins.errors import DuplicatePluginNameError, PluginLoadError
from octowright.plugins.identity import validate_name

log = get_logger("octowright.plugins.discovery")

ENTRY_POINT_GROUP = "octowright.session_kinds"


@dataclass(frozen=True)
class DiscoveredPlugin:
    """What core knows about a plugin before deciding to load it."""

    name: str
    distribution: str | None
    version: str | None
    entry_point: str
    ep: EntryPoint
    #: Set when two installed distributions claim this entry-point name. The
    #: name is then unloadable — never resolved by enumeration order — but it
    #: is still *reported*, and every other plugin still loads.
    conflict: str | None = None

    def status_row(self, state: str) -> dict[str, object]:
        """The status shape for a plugin whose descriptor has not been resolved."""
        return {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
            "entry_point": self.entry_point,
            "state": state,
        }


def discover(entry_points: Iterable[EntryPoint] | None = None) -> list[DiscoveredPlugin]:
    """Enumerate installed session-kind plugins without importing any of them.

    ``entry_points`` is injectable so tests can supply fakes; production passes
    nothing and reads the real group.
    """
    eps = list(entry_points) if entry_points is not None else list(_entry_points(group=ENTRY_POINT_GROUP))
    found: dict[str, DiscoveredPlugin] = {}
    for ep in eps:
        try:
            validate_name(ep.name, label="entry-point name")
        except PluginLoadError as exc:
            # A malformed name is one bad package, not a broken daemon.
            log.warning("octowright.plugins.bad_entry_point_name", name=ep.name, error=str(exc))
            continue
        if ep.name in found:
            # Built, not raised. A duplicate must never be resolved by
            # enumeration order (that varies by machine), but raising here
            # erased *every* plugin from status — a correctly configured
            # neighbour was then reported `missing`, which is actively
            # misleading. The name is marked unloadable instead; the
            # exception type still owns the condition and its wording.
            found[ep.name] = replace(
                found[ep.name],
                conflict=str(
                    DuplicatePluginNameError(
                        f"two distributions declare the {ENTRY_POINT_GROUP} entry point {ep.name!r} "
                        f"({found[ep.name].entry_point!r} and {ep.value!r}); resolving by enumeration "
                        "order would vary by machine, so neither is loaded"
                    )
                ),
            )
            log.warning("octowright.plugins.duplicate_entry_point_name", name=ep.name)
            continue
        dist = getattr(ep, "dist", None)
        found[ep.name] = DiscoveredPlugin(
            name=ep.name,
            distribution=getattr(dist, "name", None),
            version=getattr(dist, "version", None),
            entry_point=ep.value,
            ep=ep,
        )
    return [found[name] for name in sorted(found)]


def default_config_path() -> Path:
    """User-level plugin config. ``user_config_dir()`` already ends in ``octowright``."""
    return config_paths.user_config_dir() / "plugins.yaml"


def _load_config_plugins(path: Path) -> list[str] | None:
    """Load plugin names from a config file. Returns None if config is malformed."""
    try:
        text = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(loaded, dict):
        log.warning("octowright.plugins.bad_config", path=str(path))
        return None
    plugins = loaded.get("plugins")
    if not isinstance(plugins, list):
        log.warning("octowright.plugins.bad_config", path=str(path))
        return None
    return [str(item).strip() for item in plugins if str(item).strip()]


def enabled_names(
    *,
    env: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> list[str]:
    """Resolve which plugins an operator enabled, by entry-point name.

    Daemon-scoped on purpose. ``.octowright/config.yaml`` is found by walking
    up from CWD, so enabling plugins there would make the MCP tool surface
    depend on which directory the daemon happened to be spawned in.
    """
    source = env if env is not None else dict(os.environ)
    raw = source.get("OCTOWRIGHT_PLUGINS", "").strip()
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]

    path = config_path if config_path is not None else default_config_path()
    if not path.exists():
        return []
    result = _load_config_plugins(path)
    return result if result is not None else []

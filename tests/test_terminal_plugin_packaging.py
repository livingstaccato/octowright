# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Packaging invariants for the ``octowright-terminal`` distribution.

The plugin ships from the same GitHub Release as core but keeps its own
version line, and both of those facts have a non-obvious failure mode that
only shows up during a release. These pin them from core's suite, which runs
everywhere -- the plugin's own suite is skipped wherever uterm is absent.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_PYPROJECT = REPO_ROOT / "packages" / "octowright-terminal" / "pyproject.toml"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

# The first core release whose wheel contains ``octowright/plugins/``. An
# install against anything older resolves fine and then dies at daemon start
# with ModuleNotFoundError, so this floor is the only thing that turns a
# runtime crash into a resolver error the user can read.
CORE_PLUGINS_FLOOR = "0.17.0"

PUBLISH_ACTION = "pypa/gh-action-pypi-publish"


def _plugin_metadata() -> dict[str, Any]:
    return tomllib.loads(PLUGIN_PYPROJECT.read_text(encoding="utf-8"))["project"]


def _release_jobs() -> dict[str, Any]:
    return yaml.safe_load(RELEASE_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _steps(job: str) -> list[dict[str, Any]]:
    return _release_jobs()[job]["steps"]


def _publish_steps(job: str) -> list[dict[str, Any]]:
    return [step for step in _steps(job) if PUBLISH_ACTION in str(step.get("uses", ""))]


def test_plugin_pins_the_core_release_that_carries_plugin_machinery() -> None:
    """The ``octowright`` dependency must carry the ``octowright.plugins`` floor."""
    deps = _plugin_metadata()["dependencies"]
    core = [d for d in deps if d == "octowright" or d.startswith("octowright>") or d.startswith("octowright=")]
    assert core == [f"octowright>={CORE_PLUGINS_FLOOR}"], (
        f"expected a pinned octowright>={CORE_PLUGINS_FLOOR} floor, found {core!r}. "
        "An unpinned core dependency installs cleanly against a core with no "
        "octowright.plugins and fails at daemon start instead."
    )


def test_plugin_declares_publishable_metadata() -> None:
    """A distribution with no readme/urls/classifiers renders a blank PyPI page."""
    project = _plugin_metadata()
    assert project.get("readme") == "README.md"
    assert project.get("license-files"), "license-files must ship the Apache text with the wheel"
    for key in ("authors", "keywords", "classifiers", "urls"):
        assert project.get(key), f"project.{key} is required for a publishable distribution"


def test_plugin_version_is_independent_of_core() -> None:
    """The plugin's version is its own literal, never derived from core's.

    Deliberate: locking the two would force a plugin release on every core
    release even when nothing in the plugin changed. The literal is what makes
    ``skip-existing`` on its publish steps correct rather than a papered-over
    error.
    """
    project = _plugin_metadata()
    assert "version" in project, "plugin version must be a literal"
    assert "version" not in project.get("dynamic", []), "plugin version must not be dynamic"


def test_release_builds_the_plugin_outside_dist() -> None:
    """The plugin must never build into ``dist/``.

    ``scripts/check_wheel_assets.py`` globs ``dist/*.whl`` and inspects
    ``wheels[0]``. A second wheel in that directory can be the one it picks,
    and the plugin ships no dashboard -- so the asset gate would fail on a
    perfectly correct build, during a release, for a reason nothing names.
    """
    build_steps = [s for s in _steps("build") if "run" in s]
    plugin_builds = [s for s in build_steps if "packages/octowright-terminal" in s["run"]]
    assert plugin_builds, "release.yml must build the terminal plugin distribution"
    for step in plugin_builds:
        assert "--outdir dist-terminal" in step["run"], (
            f"plugin build must target its own directory, got: {step['run']!r}"
        )


def test_only_the_plugin_publish_steps_skip_existing() -> None:
    """Independent versions make plugin re-uploads routine and core's a defect.

    Most core releases re-present a plugin version the index already has, so
    the plugin's steps set ``skip-existing``. Core's must not: a re-upload
    there means the release is wrong, and silently skipping would hide it.
    """
    for job in ("publish-pypi", "publish-testpypi"):
        steps = _publish_steps(job)
        assert len(steps) == 2, f"{job} should publish core and the plugin, found {len(steps)}"
        core_step, plugin_step = steps
        core_with = core_step.get("with") or {}
        plugin_with = plugin_step.get("with") or {}

        assert "packages-dir" not in core_with, f"{job}: core publishes from the default dist/"
        assert not core_with.get("skip-existing"), f"{job}: core must not skip an existing upload"

        assert plugin_with.get("packages-dir") == "dist-terminal/", f"{job}: plugin dir"
        assert plugin_with.get("skip-existing") is True, f"{job}: plugin must skip an existing upload"

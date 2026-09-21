# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path
from subprocess import run

import pytest

from scripts import check_wheel_assets

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_tar(path: Path, names: set[str]) -> None:
    with tarfile.open(path, "w:gz") as tf:
        for name in names:
            payload = b"x"
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            import io

            tf.addfile(info, io.BytesIO(payload))


def _write_zip(path: Path, names: set[str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(name, "x")


def test_packaging_asset_check_requires_frontend_in_wheel_and_sdist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    required = check_wheel_assets.REQUIRED_PACKAGE_FILES
    _write_zip(dist / "octowright.whl", required)
    _write_tar(dist / "octowright.tar.gz", required - {"octowright/server/frontend/index.html"})
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match="sdist missing required files"):
        check_wheel_assets.main()


def test_packaging_asset_check_accepts_frontend_in_wheel_and_sdist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    required = check_wheel_assets.REQUIRED_PACKAGE_FILES
    _write_zip(dist / "octowright.whl", required)
    # The version in the synthetic root dir is a placeholder — the normaliser
    # strips the first segment unconditionally, so any version reads the same.
    _write_tar(dist / "octowright.tar.gz", {f"octowright-X.Y.Z/{name}" for name in required})
    monkeypatch.chdir(tmp_path)

    check_wheel_assets.main()


def test_sdist_includes_root_release_files_without_nested_dependency_trees(tmp_path: Path) -> None:
    """Root-only sdist globs must not collect ignored frontend dependencies.

    Hatch applies its include patterns against the full source tree.  Without
    a root anchor, patterns such as ``README.md`` also select the copies nested
    under ``node_modules`` and make the source distribution host-dependent.
    """
    run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    sdist = next(tmp_path.glob("octowright-*.tar.gz"))
    with tarfile.open(sdist, "r:gz") as tf:
        names = {member.name.split("/", maxsplit=1)[1] for member in tf.getmembers() if "/" in member.name}

    assert {"README.md", "REUSE.toml", "Makefile", "pyproject.toml"} <= names
    assert "src/octowright/__init__.py" in names
    assert not any("node_modules" in Path(name).parts for name in names)
    assert "packages/octowright-terminal/pyproject.toml" not in names

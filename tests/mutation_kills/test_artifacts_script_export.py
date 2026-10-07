# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exported macro CLI's rendering and how it is written to disk."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from octowright import http_headers
from octowright.artifacts.script_export import _module_source, render_macro_cli, write_macro_cli
from octowright.config_paths import upload_staging_dir, user_config_dir
from octowright.macros.scrub_engine import _serialized_variants
from octowright.session.upload_paths import check_upload_path, upload_roots

ACTIONS = [{"action": "fill", "selector": "#q", "value": "{{region}}"}]


def test_the_actions_are_embedded_as_indented_json() -> None:
    script = render_macro_cli(name="m", macro={"actions": ACTIONS})

    assert f"ACTIONS_JSON = {json.dumps(ACTIONS, indent=2)!r}\n" in script


def test_a_macro_without_actions_embeds_an_empty_list() -> None:
    script = render_macro_cli(name="m", macro={})

    assert "ACTIONS_JSON = '[]'\n" in script


def test_a_supplied_argument_becomes_its_flag_default() -> None:
    script = render_macro_cli(name="m", macro={"parameters": ["region"], "actions": ACTIONS}, args={"region": "eu"})

    assert "    parser.add_argument('--region', dest='region', default='eu')\n" in script


@pytest.mark.parametrize(
    "source",
    [
        _serialized_variants,
        http_headers.is_credential_header,
        user_config_dir,
        upload_staging_dir,
        upload_roots,
        check_upload_path,
    ],
)
def test_each_rendered_source_is_followed_by_exactly_two_blank_lines(source: object) -> None:
    script = render_macro_cli(name="m", macro={"actions": ACTIONS})
    body = inspect.getsource(source).rstrip()  # type: ignore[arg-type]

    assert body + "\n\n\n" in script
    assert body + "\n\n\n\n" not in script


def test_a_module_without_the_future_import_is_refused_by_name() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        _module_source(json)

    assert str(excinfo.value) == "json must import annotations from __future__ to be rendered"


def test_write_creates_missing_parents_and_renders_with_evidence_by_default(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "er" / "m.py"

    written = write_macro_cli(path=path, name="m", macro={"parameters": ["region"], "actions": ACTIONS})

    assert written == path
    assert path.read_text(encoding="utf-8") == render_macro_cli(
        name="m", macro={"parameters": ["region"], "actions": ACTIONS}
    )
    assert "class _Evidence:" in path.read_text(encoding="utf-8")


def test_write_passes_args_and_the_evidence_choice_through(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    macro = {"parameters": ["region"], "actions": ACTIONS}

    write_macro_cli(path=path, name="m", macro=macro, args={"region": "eu"}, include_evidence=False)

    assert path.read_text(encoding="utf-8") == render_macro_cli(
        name="m", macro=macro, args={"region": "eu"}, include_evidence=False
    )
    assert "class _Evidence:" not in path.read_text(encoding="utf-8")


# The refusal walks the parent with O_NOFOLLOW/O_DIRECTORY and dir_fd
# (``_paths._open_parent``); Windows has none of them, as in tests/test_paths.py.
@pytest.mark.skipif(os.name == "nt", reason="directory descriptors are POSIX-only")
def test_write_refuses_a_symlinked_directory_under_its_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "link").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(ValueError, match="is not a plain directory"):
        write_macro_cli(path=root / "link" / "m.py", name="m", macro={"actions": ACTIONS}, root=root)

    assert list(elsewhere.iterdir()) == []

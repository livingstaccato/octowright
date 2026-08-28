# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""No authored store may sit inside an age-based sweep unprotected.

Generalises the defect where `recordings_cleanup` deleted the macro artifact
store: artifacts live at ``<RECORDINGS_DIR>/artifacts``, inside the tree the
sweep walks, so any artifact whose files had not been touched recently was
pruned along with the disposable recordings -- hand-authored critical points
and their whole verification history, gone quietly.

That was fixed by adding ``artifacts`` to ``PRESERVED_SUBDIRS``. Nothing stops
the *next* store being placed under a swept root, and the failure is silent:
the sweep reports how much it reclaimed, not what it destroyed. Age is a fair
proxy for "this recording is disposable" and the opposite for anything a person
wrote -- the authored file that stops changing is the one that still works.

The layout check runs in a subprocess with every ``OCTOWRIGHT_*`` variable
stripped, so it pins the layout the project *ships* rather than whatever the
test session happens to have configured.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from octowright.recording_cleanup import PRESERVED_SUBDIRS, find_stale_files

#: Roots holding something a person authored or a credential that age does not
#: make disposable. A new one belongs here.
_STORE_NAMES = (
    "PROFILES_DIR",
    "MACROS_DIR",
    "GOLDENS_DIR",
    "SCENARIOS_DIR",
    "UPLOAD_STAGING_DIR",
)

#: Roots an age/size sweep walks, mapped to what that sweep refuses to delete.
#: ``captures`` is deliberately empty: ``cleanup_captures`` has no preserve
#: list at all, so nothing may nest under it.
_SWEEPS = {
    "RECORDINGS_DIR": PRESERVED_SUBDIRS,
    "CAPTURES_DIR": (),
}

_PROBE = """
import json
from octowright import defaults
from octowright.artifacts.paths import ArtifactStore

names = %r + %r
out = {n: str(getattr(defaults, n)) for n in names}
out["ARTIFACT_STORE"] = str(ArtifactStore().root)
print(json.dumps(out))
"""


def _shipped_layout() -> dict[str, Path]:
    """Resolve every root with no OCTOWRIGHT_* override in the environment."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("OCTOWRIGHT_")}
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE % (_STORE_NAMES, tuple(_SWEEPS))],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"could not resolve the shipped layout: {proc.stderr.strip()}")
    return {k: Path(v) for k, v in json.loads(proc.stdout).items()}


def _relative_to_or_none(child: Path, parent: Path) -> Path | None:
    try:
        return child.relative_to(parent)
    except ValueError:
        return None


def test_no_authored_store_sits_unprotected_inside_a_swept_root() -> None:
    layout = _shipped_layout()
    stores = {n: layout[n] for n in (*_STORE_NAMES, "ARTIFACT_STORE")}

    for store_name, store in stores.items():
        for sweep_name, preserved in _SWEEPS.items():
            relative = _relative_to_or_none(store, layout[sweep_name])
            if relative is None:
                continue
            assert relative.parts, f"{store_name} IS {sweep_name} ({store}); an age-based sweep would walk it whole"
            assert relative.parts[0] in preserved, (
                f"{store_name} ({store}) sits inside {sweep_name} ({layout[sweep_name]}) under "
                f"{relative.parts[0]!r}, which that sweep does not preserve ({preserved}). "
                f"Either move the store outside the swept root, or add {relative.parts[0]!r} to "
                f"that sweep's preserve list -- otherwise age alone will delete it."
            )


def test_the_artifact_store_is_the_one_nested_store_and_it_is_preserved() -> None:
    """Pins the arrangement, not just the rule: exactly one store nests today.

    A second one appearing is not automatically wrong, but it is a decision
    someone should make deliberately rather than discover from a sweep.
    """
    layout = _shipped_layout()
    nested = [
        name
        for name in (*_STORE_NAMES, "ARTIFACT_STORE")
        if _relative_to_or_none(layout[name], layout["RECORDINGS_DIR"]) is not None
    ]
    assert nested == ["ARTIFACT_STORE"]
    assert _relative_to_or_none(layout["ARTIFACT_STORE"], layout["RECORDINGS_DIR"]).parts[0] in PRESERVED_SUBDIRS


def test_the_preserve_list_is_not_empty() -> None:
    """Guards the parametrisation below, which would otherwise vanish silently.

    An empty ``PRESERVED_SUBDIRS`` collects zero cases, and a suite that runs no
    tests reports green -- the same shape as a gate passing over nothing.
    """
    assert PRESERVED_SUBDIRS, "emptying PRESERVED_SUBDIRS would delete the artifact store"


@pytest.mark.parametrize("preserved", PRESERVED_SUBDIRS)
def test_every_preserved_subdir_actually_survives_a_sweep(tmp_path: Path, preserved: str) -> None:
    """The layout check above is only worth anything if the list is enforced.

    Parametrised over the list itself, so a name added to ``PRESERVED_SUBDIRS``
    is covered without anyone remembering to extend this file.
    """
    recordings = tmp_path / "recordings"
    (recordings / preserved / "macros" / "checkout").mkdir(parents=True)
    authored = recordings / preserved / "macros" / "checkout" / "artifact.json"
    authored.write_text("{}", encoding="utf-8")
    disposable = recordings / "20200101T000000Z-chromium-abc123def456.jsonl"
    disposable.write_text("{}\n", encoding="utf-8")

    ancient = 0.0
    for path in (authored, disposable):
        os.utime(path, (ancient, ancient))

    stale = find_stale_files(recordings, days=1.0)
    stale_paths = {entry.path for entry in stale}
    assert disposable in stale_paths, "an old recording is still disposable"
    assert authored not in stale_paths, f"{preserved!r} is in PRESERVED_SUBDIRS but the sweep would delete it"

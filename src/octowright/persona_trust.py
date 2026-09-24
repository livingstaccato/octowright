# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Roots a persona's Chromium trusts, kept out of every other browser.

Chromium on Linux reads trust from ``$HOME/.pki/nssdb``. Importing a private
CA there trusts it in every browser the user runs, including their everyday
one, and a private CA with no name constraints can then vouch for any site.
So each persona with ``trusted_roots`` gets its own store under its own
directory, rebuilt from the PEM files on every launch, and only that
persona's Chromium is started with ``HOME`` pointing at it.

Rebuilt rather than updated: the store must hold exactly the roots named now.
A lab that reissues its root on every rebuild would otherwise leave each old
root trusted beside the new one.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 - fixed argv to NSS certutil, no shell
from pathlib import Path
from typing import Any

from octowright import personas
from octowright.private_paths import secure_profile_tree

_PEM_HEADER = "-----BEGIN CERTIFICATE-----"
_TRUST_DIR = "trust-home"


class TrustError(Exception):
    """A persona's trusted roots cannot be applied; the launch must not proceed."""


def _certutil() -> str:
    found = shutil.which("certutil")
    if not found:
        raise TrustError("trusted_roots needs NSS certutil on PATH (Debian/Ubuntu: libnss3-tools)")
    return found


def _check_root(path: Path) -> None:
    if not path.is_file():
        raise TrustError(f"trusted root {path} is not a readable file")
    head = path.read_text(encoding="ascii", errors="replace")[:4096]
    if _PEM_HEADER not in head:
        raise TrustError(f"trusted root {path} is not a PEM certificate")


def build_trust_home(persona: str, roots: list[Path]) -> Path:
    """Rebuild *persona*'s private HOME so its NSS store holds exactly *roots*."""
    for root in roots:
        _check_root(root)
    certutil = _certutil()
    home = personas.persona_dir(persona) / _TRUST_DIR
    if home.exists():
        shutil.rmtree(home)
    nssdb = home / ".pki" / "nssdb"
    nssdb.mkdir(parents=True, mode=0o700)
    secure_profile_tree(nssdb, personas.PROFILES_DIR)
    database = f"sql:{nssdb}"
    try:
        subprocess.run(  # nosec B603 - fixed argv
            [certutil, "-d", database, "-N", "--empty-password"], check=True, capture_output=True, timeout=30
        )
        for index, root in enumerate(roots):
            subprocess.run(  # nosec B603 - fixed argv
                [certutil, "-d", database, "-A", "-t", "C,,", "-n", f"octowright-trust-{index}", "-i", str(root)],
                check=True,
                capture_output=True,
                timeout=30,
            )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # certutil's stderr names the file and the reason; neither is secret,
        # but the message stays fixed so no future input can leak through it.
        raise TrustError(f"certutil could not import the trusted roots for persona {persona!r}") from None
    return home


def persona_trust_launch_kwargs(profile: str | None, kind: str) -> dict[str, Any]:
    """Playwright launch kwargs that give *profile*'s Chromium its own trust.

    Returns ``{}`` for a launch with no persona, a profile that is not a saved
    persona, or a persona without ``trusted_roots``. A malformed persona file
    raises: unlike ``base_url``, a persona that may have asked for trust must
    never be launched without it.
    """
    if not profile:
        return {}
    try:
        persona = personas.load_persona(profile)
    except FileNotFoundError:
        return {}
    if not persona.trusted_roots:
        return {}
    if kind != "chromium":
        raise TrustError(f"persona {profile!r} has trusted_roots, which apply to Chromium only; refusing {kind}")
    home = build_trust_home(profile, [Path(p).expanduser() for p in persona.trusted_roots])
    return {"env": {**os.environ, "HOME": str(home)}}

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

Linux only. Chromium on macOS and Windows reads the OS trust store, never
``$HOME/.pki/nssdb``, so a launch there would start without the trust the
persona asked for; it is refused instead. Moving ``HOME`` moves every other
per-user lookup too: ``XAUTHORITY`` is carried over from the real home when it
is unset, and user fonts, GTK settings and dconf are not (see
docs/personas.md).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess  # nosec B404 - fixed argv to NSS certutil, no shell
import sys
from pathlib import Path
from typing import Any

import yaml

from octowright import personas
from octowright.private_paths import secure_profile_tree
from octowright.request_errors import InvalidRequestError

_PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)
_TRUST_DIR = personas.TRUST_HOME_DIRNAME


class TrustError(InvalidRequestError):
    """A persona's trusted roots cannot be applied; the launch must not proceed.

    An ``InvalidRequestError``: every cause is the persona's configuration or
    the host it asked to run on, never the engine, so ``BrowserPool.launch``
    must not file it under ``engine_health`` (issue #214).
    """


def _trusted_roots_supported() -> bool:
    """Whether this host's Chromium reads trust from ``$HOME/.pki/nssdb``."""
    return sys.platform.startswith("linux")


def _certutil() -> str:
    found = shutil.which("certutil")
    if not found:
        raise TrustError("trusted_roots needs NSS certutil on PATH (Debian/Ubuntu: libnss3-tools)")
    return found


def _certificates(path: Path) -> list[str]:
    """Every PEM certificate in *path*, in file order.

    Split here because ``certutil -A -i`` imports only a file's first
    certificate, which would leave the rest of a bundle silently untrusted.
    """
    if not path.is_file():
        raise TrustError(f"trusted root {path} is not a readable file")
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        raise TrustError(f"trusted root {path} is not a readable file") from None
    blocks = _PEM_BLOCK.findall(text)
    if not blocks:
        raise TrustError(f"trusted root {path} is not a PEM certificate")
    return blocks


def _fresh_home(persona: str) -> Path:
    home = personas.persona_dir(persona) / _TRUST_DIR
    if home.is_symlink():
        # rmtree refuses a symlink with a raw OSError, and following it would
        # empty whatever it points at. Neither is ours to decide.
        raise TrustError(f"persona {persona!r} trust store {home} is a symlink; remove it")
    try:
        if home.exists():
            shutil.rmtree(home)
        nssdb = home / ".pki" / "nssdb"
        nssdb.mkdir(parents=True, mode=0o700)
    except OSError as exc:
        raise TrustError(f"persona {persona!r} trust store {home} cannot be rebuilt: {exc.strerror}") from None
    secure_profile_tree(nssdb, personas.PROFILES_DIR)
    return home


def build_trust_home(persona: str, roots: list[Path]) -> Path:
    """Rebuild *persona*'s private HOME so its NSS store holds exactly *roots*.

    A root file may be a bundle; each certificate in it is imported.
    """
    certificates = [cert for root in roots for cert in _certificates(root)]
    certutil = _certutil()
    home = _fresh_home(persona)
    database = f"sql:{home / '.pki' / 'nssdb'}"
    try:
        subprocess.run(  # nosec B603 - fixed argv
            [certutil, "-d", database, "-N", "--empty-password"], check=True, capture_output=True, timeout=30
        )
        for index, certificate in enumerate(certificates):
            subprocess.run(  # nosec B603 - fixed argv
                [certutil, "-d", database, "-A", "-t", "C,,", "-n", f"octowright-trust-{index}", "-a"],
                input=certificate.encode("ascii") + b"\n",
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

    Blocking (an rmtree and certutil subprocesses): call it off the event loop.
    """
    if not profile:
        return {}
    try:
        persona = personas.load_persona(profile)
    except FileNotFoundError:
        return {}
    except (ValueError, yaml.YAMLError) as exc:
        raise TrustError(f"persona {profile!r} cannot be read, so its trusted_roots cannot be applied: {exc}") from None
    if not persona.trusted_roots:
        return {}
    if not _trusted_roots_supported():
        raise TrustError(f"persona {profile!r} has trusted_roots, which is supported on Linux only; refusing to launch")
    if kind != "chromium":
        raise TrustError(f"persona {profile!r} has trusted_roots, which apply to Chromium only; refusing {kind}")
    home = build_trust_home(profile, [Path(p).expanduser() for p in persona.trusted_roots])
    return {"env": _child_env(home)}


def _child_env(home: Path) -> dict[str, str]:
    """The daemon's environment with ``HOME`` moved to *home*.

    X11 falls back to ``$HOME/.Xauthority`` when ``XAUTHORITY`` is unset, so a
    headed Chromium would lose the display cookie with the move; point it at
    the real one.
    """
    env = {**os.environ, "HOME": str(home)}
    if "XAUTHORITY" not in os.environ:
        cookie = Path.home() / ".Xauthority"
        if cookie.is_file():
            env["XAUTHORITY"] = str(cookie)
    return env

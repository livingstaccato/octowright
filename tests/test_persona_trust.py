# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Trust for a persona lives in that persona's own NSS store.

Chromium on Linux reads trust from $HOME/.pki/nssdb. Importing a root there
for the real HOME trusts it for every browser the user runs, so a persona's
roots go into a store under the persona's own directory and only that
persona's Chromium is started with HOME pointing at it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from octowright import persona_trust, personas

needs_tools = pytest.mark.skipif(
    shutil.which("certutil") is None or shutil.which("openssl") is None,
    reason="needs NSS certutil and openssl",
)


def make_root(directory: Path, name: str) -> Path:
    key, pem = directory / f"{name}.key", directory / f"{name}.pem"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
            "-subj", f"/CN={name}", "-keyout", str(key), "-out", str(pem),
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return pem


def nicknames(home: Path) -> set[str]:
    out = subprocess.run(
        ["certutil", "-d", f"sql:{home / '.pki/nssdb'}", "-L"], check=True, capture_output=True, text=True
    ).stdout
    return {line.rsplit(None, 1)[0].strip() for line in out.splitlines()[4:] if line.strip()}


def write_persona(root: Path, name: str, doc: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "profile.yaml").write_text(doc, encoding="utf-8")


@needs_tools
def test_the_store_holds_exactly_the_persona_roots(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path / "profiles")
    root = make_root(tmp_path, "lab-root")
    home = persona_trust.build_trust_home("lab", [root])
    assert home.is_relative_to(tmp_path / "profiles" / "lab")
    assert nicknames(home) == {"octowright-trust-0"}


@needs_tools
def test_rebuilding_replaces_the_old_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A lab rebuild issues a new root; the old one must not linger beside it."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path / "profiles")
    old, new = make_root(tmp_path, "old"), make_root(tmp_path, "new")
    persona_trust.build_trust_home("lab", [old, new])
    home = persona_trust.build_trust_home("lab", [new])
    assert nicknames(home) == {"octowright-trust-0"}
    listed = subprocess.run(
        ["certutil", "-d", f"sql:{home / '.pki/nssdb'}", "-L", "-n", "octowright-trust-0"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "CN=new" in listed


def test_a_missing_root_file_is_refused_before_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path / "profiles")
    with pytest.raises(persona_trust.TrustError, match=r"trusted root .* is not a readable file"):
        persona_trust.build_trust_home("lab", [tmp_path / "absent.pem"])


def test_a_file_that_is_not_a_certificate_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path / "profiles")
    junk = tmp_path / "junk.pem"
    junk.write_text("not a certificate\n")
    with pytest.raises(persona_trust.TrustError, match="not a PEM certificate"):
        persona_trust.build_trust_home("lab", [junk])


def test_missing_certutil_is_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(persona_trust.shutil, "which", lambda _name: None)
    pem = tmp_path / "r.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\n")
    with pytest.raises(persona_trust.TrustError, match="certutil"):
        persona_trust.build_trust_home("lab", [pem])


@pytest.mark.parametrize("kind", ["firefox", "webkit"])
def test_other_engines_are_refused_not_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    write_persona(tmp_path, "lab", "name: lab\ntrusted_roots: [/x.pem]\n")
    with pytest.raises(persona_trust.TrustError, match="Chromium only"):
        persona_trust.persona_trust_launch_kwargs("lab", kind)


def test_personas_without_roots_launch_as_before(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    write_persona(tmp_path, "plain", "name: plain\n")
    assert persona_trust.persona_trust_launch_kwargs("plain", "chromium") == {}
    assert persona_trust.persona_trust_launch_kwargs("never-created", "chromium") == {}
    assert persona_trust.persona_trust_launch_kwargs(None, "chromium") == {}


def test_a_malformed_persona_that_may_want_trust_is_not_launched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unlike base_url, trust must not fall back to 'none' on a broken file."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    write_persona(tmp_path, "broken", "name: broken\ntrusted_roots: /not-a-list.pem\n")
    with pytest.raises(ValueError, match="trusted_roots"):
        persona_trust.persona_trust_launch_kwargs("broken", "chromium")


@needs_tools
def test_launch_kwargs_point_home_at_the_store_and_keep_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    root = make_root(tmp_path, "lab-root")
    write_persona(tmp_path, "lab", f"name: lab\ntrusted_roots: [{root}]\n")
    monkeypatch.setenv("OCTOWRIGHT_SENTINEL", "kept")
    kwargs = persona_trust.persona_trust_launch_kwargs("lab", "chromium")
    assert Path(kwargs["env"]["HOME"]).is_relative_to(tmp_path / "lab")
    assert kwargs["env"]["OCTOWRIGHT_SENTINEL"] == "kept"


def test_the_persistent_launch_path_applies_persona_trust() -> None:
    """The MCP daemon and `octowright test` both open contexts here."""
    source = Path(persona_trust.__file__).parent.joinpath("browser_pool/launch_helpers.py").read_text()
    assert "persona_trust_launch_kwargs(profile, kind)" in source

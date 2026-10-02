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

import asyncio
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from octowright import persona_trust, personas
from octowright.browser_pool import BrowserPool, launch_helpers
from octowright.request_errors import InvalidRequestError

#: Scoped trust is a Linux feature (Chromium reads $HOME/.pki/nssdb there
#: only), and Windows ships an unrelated System32 certutil.exe, so a tool that
#: merely exists is not enough.
needs_tools = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("certutil") is None or shutil.which("openssl") is None,
    reason="needs Linux, NSS certutil and openssl",
)


@pytest.fixture
def on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the Linux path's logic on any host."""
    monkeypatch.setattr(persona_trust, "_trusted_roots_supported", lambda: True)


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
@pytest.mark.usefixtures("on_linux")
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


def test_a_non_linux_host_is_refused_not_launched_untrusted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """macOS and Windows Chromium never read $HOME/.pki/nssdb: launching would drop the trust."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    monkeypatch.setattr(persona_trust, "_trusted_roots_supported", lambda: False)
    write_persona(tmp_path, "lab", "name: lab\ntrusted_roots: [/x.pem]\n")
    with pytest.raises(persona_trust.TrustError, match="Linux only"):
        persona_trust.persona_trust_launch_kwargs("lab", "chromium")
    # A persona without roots is unaffected on any host.
    write_persona(tmp_path, "plain", "name: plain\n")
    assert persona_trust.persona_trust_launch_kwargs("plain", "chromium") == {}


def test_the_platform_check_matches_the_host() -> None:
    assert persona_trust._trusted_roots_supported() is sys.platform.startswith("linux")


def test_a_trust_refusal_is_the_callers_mistake_not_an_engine_fault() -> None:
    assert issubclass(persona_trust.TrustError, InvalidRequestError)


def test_a_persona_file_that_is_not_yaml_is_a_trust_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    write_persona(tmp_path, "broken", "name: [unclosed\n")
    with pytest.raises(persona_trust.TrustError, match="broken"):
        persona_trust.persona_trust_launch_kwargs("broken", "chromium")


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_symlinked_trust_home_is_refused_and_left_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(persona_trust, "_certutil", lambda: "certutil-never-run")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep").write_text("kept")
    (tmp_path / "profiles" / "lab").mkdir(parents=True)
    (tmp_path / "profiles" / "lab" / "trust-home").symlink_to(elsewhere, target_is_directory=True)
    pem = tmp_path / "r.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\nAA==\n-----END CERTIFICATE-----\n")
    with pytest.raises(persona_trust.TrustError, match="symlink"):
        persona_trust.build_trust_home("lab", [pem])
    assert (elsewhere / "keep").read_text() == "kept"


@needs_tools
def test_every_certificate_in_a_bundle_is_trusted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """certutil -A -i imports only a file's first certificate; the rest must not vanish."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path / "profiles")
    first, second, third = (make_root(tmp_path, n) for n in ("first", "second", "third"))
    bundle = tmp_path / "bundle.pem"
    bundle.write_text(first.read_text() + second.read_text())
    home = persona_trust.build_trust_home("lab", [bundle, third])
    assert nicknames(home) == {"octowright-trust-0", "octowright-trust-1", "octowright-trust-2"}
    subjects = subprocess.run(
        ["certutil", "-d", f"sql:{home / '.pki/nssdb'}", "-L", "-n", "octowright-trust-1"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "CN=second" in subjects


# X11 and Path.home() following $HOME are Linux facts; Windows' home is USERPROFILE.
_REAL_LINUX = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="X11 XAUTHORITY lookup is Linux-only")


def _fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """A saved persona with roots, a store builder that records its thread, and a real HOME."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path / "profiles")
    write_persona(tmp_path / "profiles", "lab", "name: lab\ntrusted_roots: [/x.pem]\n")
    store = tmp_path / "store"
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    monkeypatch.setenv("HOME", str(real_home))
    monkeypatch.setattr(persona_trust, "build_trust_home", lambda _persona, _roots: store)
    return store, real_home


@_REAL_LINUX
@pytest.mark.usefixtures("on_linux")
def test_the_real_xauthority_is_kept_when_home_moves(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """X11 looks for $HOME/.Xauthority; moving HOME must not lose the display cookie."""
    _store, real_home = _fake_home(monkeypatch, tmp_path)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    (real_home / ".Xauthority").write_bytes(b"cookie")
    env = persona_trust.persona_trust_launch_kwargs("lab", "chromium")["env"]
    assert env["XAUTHORITY"] == str(real_home / ".Xauthority")


@_REAL_LINUX
@pytest.mark.usefixtures("on_linux")
def test_xauthority_is_left_as_found_otherwise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _store, real_home = _fake_home(monkeypatch, tmp_path)
    monkeypatch.delenv("XAUTHORITY", raising=False)
    assert "XAUTHORITY" not in persona_trust.persona_trust_launch_kwargs("lab", "chromium")["env"]
    monkeypatch.setenv("XAUTHORITY", "/run/user/1/xauth")
    (real_home / ".Xauthority").write_bytes(b"cookie")
    assert persona_trust.persona_trust_launch_kwargs("lab", "chromium")["env"]["XAUTHORITY"] == "/run/user/1/xauth"


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[Any] = []

    async def new_page(self) -> object:
        page = object()
        self.pages.append(page)
        return page


class _FakeBrowserType:
    def __init__(self) -> None:
        self.persistent_kwargs: dict[str, Any] | None = None

    async def launch_persistent_context(self, _user_data_dir: str, **kwargs: Any) -> _FakeContext:
        self.persistent_kwargs = kwargs
        return _FakeContext()


async def _open(browser_type: _FakeBrowserType, kind: str = "chromium") -> None:
    await launch_helpers._open_browser_context(
        browser_type=browser_type,
        kind=kind,
        profile="lab",
        session_user_data_dir=None,
        headless=True,
        viewport_kwargs={},
        ctx_video_kwargs={},
        ctx_har_kwargs={},
        launch_kwargs={"args": ["--kept"]},
    )


@pytest.mark.usefixtures("on_linux")
async def test_the_persistent_launch_path_applies_persona_trust_off_the_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The MCP daemon and `octowright test` both open contexts here.

    The store rebuild is an rmtree plus certutil subprocesses with a 30s
    timeout each, so it must not run on the event loop thread.
    """
    store, _real_home = _fake_home(monkeypatch, tmp_path)
    threads: list[int] = []
    monkeypatch.setattr(
        persona_trust, "build_trust_home", lambda _persona, _roots: threads.append(threading.get_ident()) or store
    )
    browser_type = _FakeBrowserType()
    await _open(browser_type)
    assert browser_type.persistent_kwargs is not None
    assert browser_type.persistent_kwargs["env"]["HOME"] == str(store)
    assert browser_type.persistent_kwargs["args"] == ["--kept"]
    assert threads and threads[0] != threading.get_ident()
    assert asyncio.get_running_loop() is not None


def _pool_opening(monkeypatch: pytest.MonkeyPatch, pool: BrowserPool, kind: str) -> None:
    async def _impl(_options: dict[str, Any], _sp: object) -> dict[str, Any]:
        await _open(_FakeBrowserType(), kind)
        return {"instance_id": "never"}

    monkeypatch.setattr(pool, "_launch_impl", _impl)


@pytest.mark.parametrize(
    ("doc", "kind"),
    [
        ("name: lab\ntrusted_roots: [/x.pem]\n", "firefox"),
        ("name: lab\ntrusted_roots: /not-a-list.pem\n", "chromium"),
        ("name: [unclosed\n", "chromium"),
    ],
    ids=["wrong-engine", "malformed-persona", "not-yaml"],
)
@pytest.mark.usefixtures("on_linux")
async def test_a_trust_refusal_leaves_engine_health_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, doc: str, kind: str
) -> None:
    """A persona's own misconfiguration must not report the engine broken (issue #214)."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    write_persona(tmp_path, "lab", doc)
    pool = BrowserPool()
    _pool_opening(monkeypatch, pool, kind)
    with pytest.raises(persona_trust.TrustError):
        await pool.launch(kind=kind)
    assert pool.engine_health() == {}
    assert pool.refusals()["total"] == 1

# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that a persona's trusted root reaches its Chromium, and only its.

The unit tests prove the store is built and the launch kwargs point at it.
This proves the thing that matters: a real Chromium launched as the trusting
persona loads a page signed by that root, and one launched as any other
persona refuses it.
"""

from __future__ import annotations

import http.server
import shutil
import ssl
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from octowright.browser_pool import BrowserPool
from tests.test_engine_matrix_live import _configure_runtime_paths, _maybe_skip_live_engine
from tests.test_persona_trust import make_root, write_persona

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or shutil.which("certutil") is None or shutil.which("openssl") is None,
    reason="needs Linux, NSS certutil and openssl",
)


def issue_leaf(directory: Path, ca: Path) -> tuple[Path, Path]:
    ca_key = ca.with_suffix(".key")
    key, csr, leaf = directory / "leaf.key", directory / "leaf.csr", directory / "leaf.pem"
    ext = directory / "leaf.ext"
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\n")
    subprocess.run(
        ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
         "-keyout", str(key), "-out", str(csr)],
        check=True, capture_output=True,
    )  # fmt: skip
    subprocess.run(
        ["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca), "-CAkey", str(ca_key),
         "-CAcreateserial", "-days", "1", "-extfile", str(ext), "-out", str(leaf)],
        check=True, capture_output=True,
    )  # fmt: skip
    return leaf, key


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<html><body>signed-by-the-persona-root</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    ca = make_root(tmp_path, "persona-ca")
    leaf, key = issue_leaf(tmp_path, ca)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(leaf), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ca, f"https://localhost:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
@pytest.mark.live_browser
async def test_only_the_trusting_persona_loads_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, served: tuple[Path, str]
) -> None:
    pytest.importorskip("playwright")
    ca, url = served
    _, profiles = _configure_runtime_paths(monkeypatch, tmp_path)
    write_persona(profiles, "trusting", f"name: trusting\ntrusted_roots: [{ca}]\n")
    write_persona(profiles, "stranger", "name: stranger\n")

    pool = BrowserPool()
    try:
        try:
            trusting = await pool.launch(kind="chromium", headed=False, url="about:blank", profile="trusting")
        except Exception as exc:
            _maybe_skip_live_engine(exc)
        session = pool.get(trusting["instance_id"])
        await session.navigate(url)
        assert "signed-by-the-persona-root" in await session.page.content()
        await pool.close(trusting["instance_id"])

        stranger = await pool.launch(kind="chromium", headed=False, url="about:blank", profile="stranger")
        with pytest.raises(Exception, match="ERR_CERT_AUTHORITY_INVALID"):
            await pool.get(stranger["instance_id"]).navigate(url)
        await pool.close(stranger["instance_id"])
    finally:
        await pool.shutdown()

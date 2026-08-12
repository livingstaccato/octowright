# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright dashboard`` — mint a pairing code and print the /pair URL.

The pairing bootstrap (see ``octowright.http.pairing``): this command runs as
the operator's own user, so it can read the 0600 lockfile and hold the
capability token a different-user/sandboxed process can't. It mints a
single-use short-TTL code over ``/api/pair/mint`` and prints a validated
``http://HOST:PORT/pair#<code>`` URL to the tty — the out-of-band channel
that carries the credential to the human.

The code rides the URL *fragment*: never sent on navigation, never in access
logs or Referer. ``--open`` deliberately does NOT pass the URL as browser argv
(world-readable ``/proc/PID/cmdline`` would leak the code); it writes a
0700-dir redirect page and opens the ``file://`` path instead.
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from time import sleep

import click

from octowright.cli._root import cli
from octowright.defaults import DASHBOARD_REMOTE_ALLOWED_ENV

_RAW_HOST_RE = re.compile(r"^[^\x00-\x20\x7f/?#@\\]+$")
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}\.?$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$"
)
_PAIR_CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_REDIRECT_CLEANUP_DELAY_SECONDS = 5.0


def _invalid_dashboard_host() -> click.ClickException:
    return click.ClickException("invalid dashboard host in the leader lockfile")


def _host_candidate(host: str) -> tuple[str, bool]:
    if not isinstance(host, str) or not _RAW_HOST_RE.fullmatch(host):
        raise _invalid_dashboard_host()
    if host.startswith("["):
        if not host.endswith("]"):
            raise _invalid_dashboard_host()
        return host[1:-1], True
    if host.endswith("]"):
        raise _invalid_dashboard_host()
    return host, False


def _normalized_hostname(candidate: str, bracketed: bool) -> tuple[str, bool]:
    if bracketed or not _HOSTNAME_RE.fullmatch(candidate):
        raise _invalid_dashboard_host() from None
    normalized = candidate.removesuffix(".").lower()
    return normalized, normalized == "localhost"


def _normalized_dashboard_host(host: str) -> tuple[str, bool]:
    candidate, bracketed = _host_candidate(host)
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return _normalized_hostname(candidate, bracketed)
    normalized_address = (address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else address
    url_host = f"[{address.compressed}]" if isinstance(address, ipaddress.IPv6Address) else address.compressed
    return url_host, normalized_address.is_loopback


def _validated_dashboard_base(host: str, port: int) -> str:
    """Build an HTTP origin from trusted-shape lockfile metadata."""
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise click.ClickException("invalid dashboard port in the leader lockfile")
    url_host, loopback = _normalized_dashboard_host(host)

    if not loopback and os.environ.get(DASHBOARD_REMOTE_ALLOWED_ENV) != "1":
        raise click.ClickException(
            f"remote dashboard access is disabled for {host!r}; set {DASHBOARD_REMOTE_ALLOWED_ENV}=1 to opt in"
        )
    return f"http://{url_host}:{port}"


def _mint_code(base: str, token: str) -> str:
    request = urllib.request.Request(
        f"{base}/api/pair/mint",
        method="POST",
        headers={"X-Octowright-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:  # nosec B310  # fixed loopback http URL
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise click.ClickException(f"leader refused the pairing mint ({exc.code}): {detail}") from exc
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"could not reach the leader at {base}: {exc}") from exc
    code = body.get("code")
    if not isinstance(code, str) or not _PAIR_CODE_RE.fullmatch(code):
        raise click.ClickException("leader returned no valid pairing code")
    return code


def _open_via_redirect_file(url: str) -> None:
    """Open ``url`` without putting the pairing code in browser argv.

    ``webbrowser.open(url)`` would exec the browser with the code-bearing URL
    as an argument — world-readable via ``/proc/PID/cmdline`` on Linux — so we
    write a redirect page into a fresh ``mkdtemp`` dir (0700 by default) and
    hand the browser only the ``file://`` path."""
    redirect_dir = Path(tempfile.mkdtemp(prefix="octowright-pair-"))
    redirect = redirect_dir / "pair.html"
    try:
        redirect.write_text(
            f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={html.escape(url, quote=True)}">',
            encoding="utf-8",
        )
        webbrowser.open(redirect.as_uri())
        # ``webbrowser.open`` can return before a cold-starting browser reads
        # the file, so leave a short grace period before removing the secret.
        sleep(_REDIRECT_CLEANUP_DELAY_SECONDS)
    finally:
        shutil.rmtree(redirect_dir, ignore_errors=True)


@cli.command()
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the pairing URL in the default browser (via a private redirect file, not argv).",
)
def dashboard(open_browser: bool) -> None:
    """Mint a single-use dashboard pairing code and print the /pair URL.

    Needed when OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING is enabled; harmless
    otherwise (the bearer is simply never checked).
    """
    from octowright import singleton

    info = singleton.read_lock()
    if info is None:
        raise click.ClickException("no running octowright daemon (lockfile absent) — start one with `octowright serve`")
    base = _validated_dashboard_base(info.http_host, info.http_port)
    if not info.token:
        # Pre-token lockfile or gate disabled: nothing to pair against.
        click.echo("This leader has no capability token; pairing is unavailable.")
        click.echo(f"Dashboard: {base}/")
        return
    code = _mint_code(base, info.token)
    url = f"{base}/pair#{code}"
    click.echo("Open this URL in your browser (single-use, expires in 60s):")
    click.echo(f"  {url}")
    if open_browser:
        _open_via_redirect_file(url)

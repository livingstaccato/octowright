# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright dashboard`` — mint a pairing ticket and print the /pair URL.

The pairing bootstrap (see ``octowright.http.pairing``): this command runs as
the operator's own user, so it can read the 0600 lockfile and hold the
capability token a different-user/sandboxed process can't. It mints a
single-use short-TTL ticket over ``/api/pair/mint`` and prints
``http://127.0.0.1:PORT/pair#<ticket>`` to the tty — the out-of-band channel
that carries the credential to the human.

The ticket rides the URL *fragment*: never sent on navigation, never in access
logs or Referer. ``--open`` deliberately does NOT pass the URL as browser argv
(world-readable ``/proc/PID/cmdline`` would leak the ticket); it writes a
0700-dir redirect page and opens the ``file://`` path instead.
"""

from __future__ import annotations

import json
import tempfile
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import click

from octowright.cli._root import cli


def _mint_ticket(base: str, token: str) -> str:
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
    ticket = body.get("ticket")
    if not isinstance(ticket, str) or not ticket:
        raise click.ClickException(f"leader returned no ticket: {body!r}")
    return ticket


def _open_via_redirect_file(url: str) -> None:
    """Open ``url`` without putting the ticket in browser argv.

    ``webbrowser.open(url)`` would exec the browser with the ticket-bearing URL
    as an argument — world-readable via ``/proc/PID/cmdline`` on Linux — so we
    write a redirect page into a fresh ``mkdtemp`` dir (0700 by default) and
    hand the browser only the ``file://`` path."""
    redirect_dir = Path(tempfile.mkdtemp(prefix="octowright-pair-"))
    redirect = redirect_dir / "pair.html"
    redirect.write_text(
        f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={url}">',
        encoding="utf-8",
    )
    webbrowser.open(redirect.as_uri())


@cli.command()
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the pairing URL in the default browser (via a private redirect file, not argv).",
)
def dashboard(open_browser: bool) -> None:
    """Mint a single-use dashboard pairing ticket and print the /pair URL.

    Needed when OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING is enabled; harmless
    otherwise (the cookie is simply never checked).
    """
    from octowright import singleton

    info = singleton.read_lock()
    if info is None:
        raise click.ClickException("no running octowright daemon (lockfile absent) — start one with `octowright serve`")
    base = f"http://127.0.0.1:{info.http_port}"
    if not info.token:
        # Pre-token lockfile or gate disabled: nothing to pair against.
        click.echo("This leader has no capability token; pairing is unavailable.")
        click.echo(f"Dashboard: {base}/")
        return
    ticket = _mint_ticket(base, info.token)
    url = f"{base}/pair#{ticket}"
    click.echo("Open this URL in your browser (single-use, expires in 60s):")
    click.echo(f"  {url}")
    if open_browser:
        _open_via_redirect_file(url)

# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The SSRF host check must classify the host the BROWSER will resolve.

``check_navigation_url`` classified ``urlsplit(url).hostname``. That is not
WHATWG host parsing, which is what Chromium/Firefox/WebKit (and the ``new URL``
Playwright calls on every ``page.goto``) actually apply. Three differences let a
poisoned macro walk straight past ``block-private``:

* an **empty trailing label** -- ``169.254.169.254.`` -- which the WHATWG IPv4
  parser drops but ``_parse_ipv4_parts`` rejects outright (empty part), so the
  host fell through to "not an IP" and was treated as a public name;
* **UTS46 mapping** -- U+3002, U+FF0E and U+FF61 all map to ``.``, and
  fullwidth digits map to their ASCII forms, so a host spelled with them is
  the metadata IP to a browser and an opaque string to ``urlsplit``;
* **percent-decoding** of host bytes -- ``127.0.0.%31`` is ``127.0.0.1``.

All of these were confirmed reaching a real loopback server through headless
Chromium while the guard reported the navigation allowed. The cloud-metadata
range is the one this module names as the reason it exists.

The fix normalizes the host the way the browser does before classifying. These
tests pin each family plus the public hosts that must keep working.
"""

from __future__ import annotations

import pytest

from octowright import ssrf


@pytest.fixture(autouse=True)
def _block_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.delenv("OCTOWRIGHT_SSRF_ALLOW", raising=False)


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("http://169.254.169.254/latest/meta-data/", "control: plain metadata IP"),
        ("http://169.254.169.254./latest/meta-data/", "empty trailing label"),
        ("http://127.0.0.1.:6286/api/sessions", "trailing dot on loopback"),
        ("http://10.0.0.5./", "trailing dot on RFC1918"),
        ("http://169\u3002254\u3002169\u3002254/latest/", "U+3002 ideographic full stop"),
        ("http://127\uff0e0\uff0e0\uff0e1:6286/", "U+FF0E fullwidth full stop"),
        ("http://127\uff610\uff610\uff611:6286/", "U+FF61 halfwidth ideographic full stop"),
        ("http://\uff11\uff12\uff17.0.0.1:6286/", "fullwidth digits"),
        ("http://127.0.0.%31:6286/", "percent-encoded final octet"),
        ("http://%31%32%37.0.0.1:6286/", "percent-encoded leading octet"),
        ("http://localhost.:6286/", "trailing dot on localhost"),
        ("http://foo.localhost./", "trailing dot on a *.localhost name"),
        ("http://169.254.169.254%2E/latest/", "percent-encoded trailing dot"),
    ],
)
def test_non_public_hosts_are_refused_however_they_are_spelled(url: str, why: str) -> None:
    with pytest.raises(ValueError, match="non-public host"):
        ssrf.check_navigation_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://www.google.com/search?q=1",
        "https://m\u00fcnchen.de/",
        "https://example.com./",
        "https://sub.example.co.uk/path",
        "http://93.184.216.34/",
    ],
)
def test_public_hosts_still_pass(url: str) -> None:
    ssrf.check_navigation_url(url)


def test_the_allowlist_still_wins_for_an_internal_target() -> None:
    import os

    os.environ["OCTOWRIGHT_SSRF_ALLOW"] = "10.0.0.5"
    try:
        ssrf.check_navigation_url("http://10.0.0.5/")
    finally:
        del os.environ["OCTOWRIGHT_SSRF_ALLOW"]

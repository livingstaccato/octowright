# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A navigation the SSRF guard client-redirected is recorded as the redirect it really was.

Under ``block-private`` the browser sees a 200 for each hop but the last (see
``ssrf_guard``). The guard tags the request with the 3xx it got; the network
row shows that, so ``browser_network_requests`` keeps the chain.
"""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace

from octowright import ssrf_guard
from octowright.request_failures import NetworkLedger
from octowright.session.core_network_mixin import SessionNetworkMixin


class _Request:
    """Weakly referenceable, as Playwright's Request is."""

    url = "https://app.example/start"
    method = "GET"
    resource_type = "document"


def _subject() -> SessionNetworkMixin:
    subj = SessionNetworkMixin.__new__(SessionNetworkMixin)
    subj._network_requests = deque(maxlen=10)
    subj._network = NetworkLedger()
    subj._network_requests_dropped = 0
    return subj


def test_a_client_redirected_hop_records_its_real_status_and_location() -> None:
    request = _Request()
    ssrf_guard._CLIENT_REDIRECTS[request] = {
        "status": 302,
        "status_text": "Found",
        "location": "https://app.example/next",
    }
    subj = _subject()
    subj._handle_response(SimpleNamespace(request=request, status=200, status_text="OK"))
    (row,) = subj.get_network_requests()["requests"]
    assert row["status"] == 302
    assert row["status_text"] == "Found"
    assert row["redirect_location"] == "https://app.example/next"
    assert row["served_as"] == "client_redirect"


def test_an_ordinary_response_is_unchanged() -> None:
    subj = _subject()
    subj._handle_response(SimpleNamespace(request=_Request(), status=200, status_text="OK"))
    (row,) = subj.get_network_requests()["requests"]
    assert row["status"] == 200
    assert "redirect_location" not in row and "served_as" not in row


def test_a_request_that_cannot_be_weakly_referenced_is_not_an_error() -> None:
    assert ssrf_guard.client_redirect_of(SimpleNamespace(url="x")) is None

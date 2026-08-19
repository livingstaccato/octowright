# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The host-relative relaxation must not admit a WHATWG authority form.

``_reject_unsafe_url`` skips the scheme deny-list AND the SSRF host check for a
single-leading-slash URL, on the grounds that one slash is a path on the
context's own ``base_url`` and therefore same-origin by construction. That is
true under RFC 3986. It is NOT true under the WHATWG URL Standard, which is what
Chromium/Firefox/WebKit — and Playwright's ``base_url`` resolution — actually
implement:

* for a special scheme (http/https), ``\\`` is equivalent to ``/``, so
  ``/\\evil.test/x`` is an AUTHORITY, not a path;
* ASCII tab, LF and CR are REMOVED from the URL before parsing, so
  ``/<TAB>/evil.test/x`` becomes ``//evil.test/x``.

Verified against node's WHATWG parser with base ``http://good.test/app/``:
every form below resolves to host ``evil.test``. The old check tested
``startswith("//")`` and so passed all of them, while correctly blocking the two
spellings it reasoned about (``//host`` and ``http://host``). With
``OCTOWRIGHT_SSRF_POLICY=block-private`` a poisoned macro could therefore
navigate ``/\\169.254.169.254/latest/meta-data/`` and read cloud-metadata
credentials back through ``browser_read_markdown`` — the exact attack that
policy exists to stop, and not one of its documented scope limits (which are:
off by default, and DNS-rebinding of a public hostname).
"""

from __future__ import annotations

import importlib

import pytest

#: Forms that a single leading slash makes look same-origin but that WHATWG
#: resolves to a different host. Kept as (label, url) so a failure names the form.
_AUTHORITY_FORMS: list[tuple[str, str]] = [
    ("backslash", "/\\169.254.169.254/latest/meta-data/"),
    ("slash-backslash", "/\\/169.254.169.254/x"),
    ("backslash-slash", "/\\169.254.169.254/x"),
    ("tab", "/\t/169.254.169.254/x"),
    ("newline", "/\n/169.254.169.254/x"),
    ("carriage-return", "/\r/169.254.169.254/x"),
    ("tab-inside-backslash", "/\\\t169.254.169.254/x"),
]

#: Genuinely same-origin paths that must keep working untouched.
_SAME_ORIGIN_PATHS: list[str] = [
    "/",
    "/orders",
    "/orders/123?q=1",
    "/a/b/c.html#frag",
    "/orders?next=//evil.test",  # a query VALUE is not an authority
    "/path-with-backslash-later/a\\b",  # backslash after the first segment
]


@pytest.fixture
def guard(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """`_reject_unsafe_url` with block-private active, freshly imported.

    The policy is read at import time, so both modules are reloaded after the
    env var is set.
    """
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.delenv("OCTOWRIGHT_SSRF_ALLOW", raising=False)
    import octowright.session.core_page_mixin as page_mixin
    import octowright.ssrf as ssrf

    importlib.reload(ssrf)
    importlib.reload(page_mixin)
    yield page_mixin._reject_unsafe_url
    # Restore both modules under the ambient (unset) policy for later tests.
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    importlib.reload(ssrf)
    importlib.reload(page_mixin)


@pytest.mark.parametrize(("label", "url"), _AUTHORITY_FORMS, ids=[label for label, _u in _AUTHORITY_FORMS])
def test_authority_forms_are_refused(guard, label: str, url: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        guard(url)


@pytest.mark.parametrize("url", _SAME_ORIGIN_PATHS)
def test_genuine_host_relative_paths_still_pass(guard, url: str) -> None:  # type: ignore[no-untyped-def]
    guard(url)


def test_the_two_forms_that_already_worked_still_work(guard) -> None:  # type: ignore[no-untyped-def]
    """Regression floor: don't fix the new forms by breaking the old checks."""
    with pytest.raises(ValueError):
        guard("//169.254.169.254/x")
    with pytest.raises(ValueError):
        guard("http://169.254.169.254/latest/meta-data/")


def test_an_authority_form_is_treated_exactly_like_a_protocol_relative_one(guard) -> None:  # type: ignore[no-untyped-def]
    """Equivalent spellings must get identical treatment — that IS the fix.

    Canonicalizing folds `/\\host` and `/<TAB>/host` into `//host`, and `//host`
    has always been rejected as scheme-less (it falls through the same-origin
    test to the absolute-URL checks, which require a scheme). So every WHATWG
    authority spelling now lands on the same error, for the same reason, rather
    than one spelling being checked and the others waved through. This holds for
    a public host too: the point is that the guard no longer mistakes any of
    these for a same-origin path.
    """
    for form in ("//example.com/x", "/\\example.com/x", "/\t/example.com/x"):
        with pytest.raises(ValueError, match="missing scheme"):
            guard(form)


def test_canonicalization_applies_with_the_policy_off_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same-origin misclassification is independent of the SSRF policy.

    `block-private` is opt-in, but "is this a path or an authority?" is not a
    policy question — an authority form must never be classified as same-origin
    even with the policy off, or enabling the policy later would be the only
    thing standing between a macro and a cross-origin navigation it disguised.
    """
    monkeypatch.delenv("OCTOWRIGHT_SSRF_POLICY", raising=False)
    import octowright.session.core_page_mixin as page_mixin
    import octowright.ssrf as ssrf

    importlib.reload(ssrf)
    importlib.reload(page_mixin)
    with pytest.raises(ValueError, match="missing scheme"):
        page_mixin._reject_unsafe_url("/\\example.com/x")
    # A genuine path is still fine with the policy off.
    page_mixin._reject_unsafe_url("/orders")


# ---------------------------------------------------------------------------
# C0 controls before the scheme (WHATWG strips them; str.strip() does not)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix",
    ["\x00", "\x01", "\x08", "\x0b", "\x0e", "\x1b", "\x1f", " ", "\x01\x02 "],
)
@pytest.mark.parametrize("scheme_url", ["file:///etc/passwd", "javascript:alert(1)", "view-source:http://x/"])
def test_a_c0_control_before_the_scheme_does_not_defeat_the_deny_list(prefix: str, scheme_url: str) -> None:
    """`str.strip()` removes only Python whitespace, but the WHATWG parser
    strips every C0 control or space (U+0000-U+0020) before parsing. So
    `\\x01file:///etc/passwd` read as scheme `\\x01file` to the guard -- not on
    the deny-list -- and as `file` to Chromium, which loaded the file. Confirmed
    live: the browser returned the file's contents.
    """
    from octowright.session.core_page_mixin import _reject_unsafe_url

    with pytest.raises(ValueError):
        _reject_unsafe_url(prefix + scheme_url)


def test_an_ordinary_url_with_surrounding_whitespace_still_works() -> None:
    from octowright.session.core_page_mixin import _reject_unsafe_url

    _reject_unsafe_url("  https://example.com/  ")

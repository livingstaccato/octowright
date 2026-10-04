# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""One base for every safety refusal: a verdict on a macro, never a page that did not cooperate.

A macro's ``try`` suppresses its body's errors and ``try_each`` moves on to its
next branch (``octowright.conditional``); both re-raise a ``SafetyStop`` instead,
so a refusal always fails the run and is reported rather than reading as a
missing cookie banner. The refusals that are one:

- the credential guard's (``credential_sinks.CredentialSafetyStop``);
- the SSRF policy's (``ssrf.SsrfRefusal``) -- a navigation's own URL, a hop it
  was redirected to, a subresource;
- the classified-screenshot boundary's (``macros.screenshot_refusal.ScreenshotRefused``).

Each also keeps the type it had (``ValueError``, ``InvalidRequestError``,
``RuntimeError``), so no caller that catches one changes. Lives at the package
root, beside ``request_errors``, so all three can subclass it without reaching
into one another.
"""

from __future__ import annotations

__all__ = ["SafetyStop"]


class SafetyStop(Exception):
    """A safety check refused a step; no ``try`` or ``try_each`` may catch it."""

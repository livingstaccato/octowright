# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Record-time input redaction for terminal sessions.

Mirrors HostedSessionRuntime's password masking and honours the existing
OCTOWRIGHT_REDACT_INPUTS modes, read on every call as core reads them:
  off       -> record literal sends
  passwords -> mask at detected password prompts + password-sourced sends (default)
  all       -> mask every send
The connector always receives the real bytes; only the recording is masked.
"""

from __future__ import annotations

import os
import re
from typing import Any

# Copied verbatim from provide.uterm.server.runtime (HostedSessionRuntime._log_snapshot).
_PASSWORD_PROMPT_RE = re.compile(r"(?i)(?:password|passphrase)[^\n]*:\s*$")


def is_password_prompt(screen: str) -> bool:
    return bool(_PASSWORD_PROMPT_RE.search(screen.rstrip()))


def _redaction_mode() -> str:
    """``OCTOWRIGHT_REDACT_INPUTS`` as of this call.

    Not ``defaults.INPUT_REDACTION_MODE``: that is an import-time snapshot,
    while core reads the variable per call, so a policy change reached browser
    recordings but not terminal ones. Not core's own resolver either, which
    the plugin's declared core floor predates.
    """
    return os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "passwords").strip().lower() or "passwords"


def should_mask(*, at_password_prompt: bool, password_source: bool) -> bool:
    mode = _redaction_mode()
    if mode == "off":
        return False
    if mode == "all":
        return True
    # "passwords" (default) and any unrecognised value: fail safe to masking creds.
    return at_password_prompt or password_source


def input_fields(text: str, *, masked: bool) -> dict[str, Any]:
    if masked:
        return {"keys": "***", "byte_count": len(text.encode("utf-8"))}
    return {"keys": text}

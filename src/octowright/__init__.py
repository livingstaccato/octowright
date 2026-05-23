# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

import os as _os

from octowright.version import VERSION, __version__

# Default OTel service name so OpenObserve / any OTLP backend labels our
# spans and metrics as "octowright" without the operator setting it. A
# user-supplied value wins.
_os.environ.setdefault("PROVIDE_TELEMETRY_SERVICE_NAME", "octowright")

# Revert the earlier change in cli/serve.py — handled centrally now so
# all entrypoints (selftest, persona, scenario, etc.) get the default.

__all__ = ["VERSION", "__version__"]

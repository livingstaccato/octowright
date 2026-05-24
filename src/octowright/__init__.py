# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from octowright.version import VERSION, __version__

# Default-env application happens in octowright.defaults, which is imported
# by every CLI entrypoint via the modules they load. Keep __init__.py
# logic-free per project convention.

__all__ = ["VERSION", "__version__"]

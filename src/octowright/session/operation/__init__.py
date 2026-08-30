# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Namespace package for session-operation machinery; see ``operation.gate``.

Holds no content of its own -- everything lives in the ``gate`` subpackage.
A directory level exists here (rather than a single ``operation_gate.py``
module) so the package split doesn't stop at one underscore: hierarchy goes
in directory paths, not packed into a filename.
"""

from __future__ import annotations

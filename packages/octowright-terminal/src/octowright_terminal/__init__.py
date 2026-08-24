# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal session kind for octowright, as a session-kind plugin.

Deliberately imports nothing at module scope: core resolves
``octowright_terminal.plugin:plugin`` from an entry point, and the uterm import
must not happen until a pool is actually built.
"""

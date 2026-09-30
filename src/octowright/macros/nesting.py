# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What is nested in a macro's actions: the one walk every such question uses.

"Does this run reach an ``expect_network_clean``?" and "which arguments feed an
``expect_no_text``?" each had their own walker, and they disagreed about
``macro_call`` without meaning to. A question now picks whether to follow calls
and asks the same generator.

Imports nothing from the macros package at module level, so ``privacy`` (which
``substitution`` imports) can use it without a cycle.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from provide.telemetry import get_logger

log = get_logger(__name__)

MacroLoader = Callable[[str], dict[str, Any]]


class RunMacros:
    """Each macro a run names, loaded from disk at most once.

    A ``macro_call`` was read up to three times per dispatch -- the network
    scan, the nested call's privacy step, and the dispatch itself -- each a
    synchronous file read and parse on the event loop. One instance lives for
    one run (or one sequence), so an edit to a macro file takes effect on the
    next run, never halfway through one. A failed load is remembered too and
    raised again, so every consumer sees the same answer.

    Callers must not mutate what they get back: it is shared. Dispatch hands
    it to ``substitute``, which deep-copies.
    """

    def __init__(self, load: MacroLoader) -> None:
        self._load = load
        self._loaded: dict[str, dict[str, Any] | Exception] = {}

    def __call__(self, name: str) -> dict[str, Any]:
        if name not in self._loaded:
            try:
                self._loaded[name] = self._load(name)
            except Exception as exc:
                self._loaded[name] = exc
        entry = self._loaded[name]
        if isinstance(entry, Exception):
            raise entry
        return entry


def _names_a_placeholder(node: Any) -> bool:
    """Whether a ``{{placeholder}}`` sits where a walk reads: an action kind or a call's name."""
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            if any(isinstance(item.get(key), str) and "{{" in item[key] for key in ("action", "name")):
                return True
            stack.extend(item.values())
    return False


def _called_actions(node: dict[str, Any], load_macro: MacroLoader) -> Any:
    """The actions a ``macro_call`` runs, expanded only where the walk would read a placeholder.

    Substituting a whole macro (a deep copy) just to look at its action kinds
    was the common cost; a called macro whose kinds and call names are literal
    reads the same raw as expanded.
    """
    called = load_macro(node["name"]).get("actions", [])
    if not _names_a_placeholder(called):
        return called
    # Local import: substitution imports privacy, which imports this module.
    from octowright.macros.substitution import substitute

    call_args = node.get("args")
    return substitute(called, call_args if isinstance(call_args, dict) else {})


def iter_nested_actions(actions: Any, *, load_macro: MacroLoader | None = None) -> Iterator[dict[str, Any]]:
    """Every action dict in *actions*, at any depth: ``try``/``if_selector`` bodies included.

    With *load_macro*, a ``macro_call`` is followed into the macro it runs, with
    its call args substituted where that matters (`_called_actions`). Each
    macro is followed once, so recursion terminates. A called macro that cannot
    be loaded or expanded contributes nothing: dispatching it fails anyway.
    """
    followed: set[str] = set()
    stack: list[Any] = [actions]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
            continue
        if not isinstance(node, dict):
            continue
        if "action" in node:
            yield node
        name = node.get("name")
        if (
            load_macro is not None
            and node.get("action") == "macro_call"
            and isinstance(name, str)
            and name not in followed
        ):
            followed.add(name)
            try:
                stack.append(_called_actions(node, load_macro))
            except Exception as exc:
                log.debug("octowright.macro.nested_scan_unloadable", macro=name, error=repr(exc))
        stack.extend(reversed(list(node.values())))

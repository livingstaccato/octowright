# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal's scenario participation.

Implements the mandatory floor and NOTHING else. Core derives capabilities from
the Protocols an adapter satisfies, so the absence of run_macro here is what
makes a terminal participant declaring startup_macros a validation error -- the
behaviour core used to hardcode, now falling out of the contract.

The validation and kwarg resolution below moved out of core's scenarios.py:
they are terminal's rules about terminal's own options, and core had no
business knowing that `cols` must be an int. Both bodies are unchanged except
for the one thing the new call boundary forces: ``resolve_participant`` is
handed a bare ``(spec, persona)`` pair with no ``Scenario`` in reach, so
``_validate_options`` drops the ``scenario {s.name!r}:`` prefix core's version
put on its error messages, and ``_resolve_launch`` takes ``persona`` as a
parameter instead of loading it itself (its caller already has one -- the
same persona every other adapter's ``resolve_participant`` receives).
"""

from __future__ import annotations

from typing import Any, cast

from octowright_terminal.connector_config import (
    SSH_DEFAULT_PORT,
    SUPPORTED_TERMINAL_KINDS,
    pty_connector_config,
    ssh_connector_config,
)


def _validate_options(p: Any) -> None:
    """Validate the ``options`` terminal owns, at LAUNCH time.

    Moved (aside from the dropped scenario-name prefix -- see the module
    docstring) from core's ``scenarios._validate_terminal_options``, which ran
    at scenario-load time. It does not any more: ``options`` is opaque to core,
    so this now runs from ``resolve_participant``, i.e. when the participant is
    launched. Core's ``scenarios._validate_participant_kind`` states the same
    boundary from its side.

    The check still earns its place at the later moment. ``options`` is opaque
    to core, so the YAML parser's int check does not reach these -- they used
    to be typed ``Participant`` fields. Without this, a string ``cols`` surfaces
    deep inside the uterm connector instead of at the scenario seam.
    """
    connector_type = p.options.get("connector_type") or "pty"
    if connector_type not in SUPPORTED_TERMINAL_KINDS:
        raise ValueError(
            f"terminal participant has unsupported connector_type {connector_type!r} "
            f"(expected one of {list(SUPPORTED_TERMINAL_KINDS)})"
        )
    for opt in ("cols", "rows", "port"):
        value = p.options.get(opt)
        # bool is an int subclass, so a bare isinstance check would pass `cols: true`
        # -- the same trap _validate_optional_ints guards against.
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(
                f"terminal participant {p.persona!r} options.{opt} must be an integer, got {type(value).__name__}"
            )


def _resolve_launch(p: Any, persona: Any) -> dict[str, Any]:
    """Return kwargs for ``terminal_pool.launch(**kwargs)`` from a terminal participant.

    Moved from core's ``scenarios.resolve_terminal_launch``, which loaded its
    own persona via a private core helper because it had no other way to get
    one. ``resolve_participant`` is always handed an already-resolved persona
    by its caller (the same one every other adapter's ``resolve_participant``
    receives), so this takes it as a parameter instead -- the rest of the body
    is unchanged.

    Note ``terminal_pool.launch``'s ``kind`` is the *connector* type (pty/ssh);
    the session's own kind is always ``"terminal"``. SSH fields resolve
    participant-override -> persona ``app['ssh']`` default -> omit. No password
    is read from the scenario (scenarios are persisted): key-based /
    known_hosts auth only.
    """
    opts = p.options
    connector_type = opts.get("connector_type") or "pty"
    if connector_type == "ssh":
        ssh = (getattr(persona, "app", {}) or {}).get("ssh", {}) or {}

        def _pick(key: str) -> Any:
            value = opts.get(key)
            return value if value is not None else ssh.get(key)

        port_opt = opts.get("port")
        # cast, not int(): parity with pre-options behaviour, where a participant-supplied
        # port was passed through unconverted (only the persona/ssh fallback was int()'d).
        # opts.get(...) is Any on this untyped dict, so mypy strict needs *something* here --
        # cast satisfies it with zero runtime effect.
        port = cast(int, port_opt) if port_opt is not None else int(ssh.get("port", SSH_DEFAULT_PORT))
        insecure_opt = opts.get("insecure_no_host_check")
        insecure = bool(insecure_opt) if insecure_opt is not None else bool(ssh.get("insecure_no_host_check", False))
        cfg = ssh_connector_config(
            host=_pick("host"),
            port=port,
            user=_pick("user"),
            key_path=_pick("key_path"),
            password=None,
            known_hosts=_pick("known_hosts"),
            insecure_no_host_check=insecure,
        )
    else:
        cfg = pty_connector_config(command=opts.get("command"), cols=opts.get("cols"), rows=opts.get("rows"))
    return {"kind": connector_type, "connector_config": cfg, "label": None, "profile": p.persona, "protected": False}


class TerminalScenarioAdapter:
    """The adapter that replaces core's hardcoded terminal branch.

    Implements only ``resolve_participant`` -- the mandatory floor. It does
    NOT implement ``run_macro`` / ``wait_for_sync`` / ``set_dialog_policy`` /
    ``install_mock_routes``, so ``capabilities_of`` (which derives support from
    Protocol conformance, not a declared string) reports that terminal supports
    none of them. That is the real behaviour, not a placeholder pending a
    later step -- terminal cannot run macros or sync today, in any scenario.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        _validate_options(spec)
        return _resolve_launch(spec, persona)

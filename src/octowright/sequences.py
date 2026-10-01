# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Macro sequence files: an ordered list of macros run in one browser.

A file holds the same ``names`` and ``args_list`` that ``macro_run_sequence``
takes, as data. A macro is one piece of behaviour; a sequence is the order a
check walks them in. Keeping the order as data leaves each macro small and replayable on its
own, and lets the same macros serve an unattended run and a person exploring.

Arguments may be literals, ``{"credential": name}`` (resolved from the persona
at run time, so no secret is ever written into a sequence), or
``{"artifact": file}`` (a path under the run's artifacts directory, so a macro
never hard-codes where evidence goes).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from octowright import defaults, personas
from octowright._paths import reject_unsafe_path

_ARTIFACT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class SequenceError(Exception):
    """A sequence cannot run as written. Messages never carry resolved values."""


@dataclass(frozen=True)
class SequenceStep:
    macro: str
    args: dict[str, Any]

    @property
    def credential_args(self) -> frozenset[str]:
        """The args written as ``{"credential": name}``, by name.

        Resolution turns each into a plain string, and the macro would then
        classify it by its parameter name alone -- so ``{"pin": {"credential":
        "pin"}}`` reached the recording and a failure payload in the clear,
        because ``pin`` does not look like a credential. The runner hands these
        names to ``run_macro(credential_args=...)``, which treats them as
        credential-tier whatever they are called, the same way a ``macro_call``
        keeps its caller's credential classified across a rename.
        """
        return frozenset(key for key, value in self.args.items() if isinstance(value, dict) and "credential" in value)


def _check_arg(index: int, key: str, value: Any) -> None:
    if not isinstance(value, dict):
        return
    if set(value) == {"credential"} and isinstance(value["credential"], str) and value["credential"]:
        return
    if (
        set(value) == {"artifact"}
        and isinstance(value["artifact"], str)
        and _ARTIFACT_NAME.fullmatch(value["artifact"])
    ):
        return
    raise SequenceError(
        f'step {index} argument {key!r}: an object must be {{"credential": name}} or {{"artifact": file}}'
    )


def load_sequence(path: Path) -> list[SequenceStep]:
    """Parse and shape-check a sequence file. Resolves nothing."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, list):
        raise SequenceError("a sequence must be a list of steps")
    if not doc:
        raise SequenceError("a sequence has no steps")
    steps: list[SequenceStep] = []
    for index, raw in enumerate(doc):
        if not isinstance(raw, dict) or not isinstance(raw.get("macro"), str) or not raw["macro"]:
            raise SequenceError(f"step {index}: 'macro' must be a non-empty string")
        args = raw.get("args", {})
        if not isinstance(args, dict):
            raise SequenceError(f"step {index}: 'args' must be an object")
        for key, value in args.items():
            _check_arg(index, key, value)
        steps.append(SequenceStep(macro=raw["macro"], args=dict(args)))
    return steps


def _artifacts_root(artifacts: Path | None) -> Path | None:
    if artifacts is None:
        return None
    try:
        return reject_unsafe_path(Path(artifacts), defaults.RECORDINGS_DIR, label="artifacts directory")
    except ValueError:
        raise SequenceError("the artifacts directory must sit under OCTOWRIGHT_RECORDINGS") from None


def _resolve_value(where: str, value: Any, persona: personas.Persona | None, artifacts: Path | None) -> Any:
    if isinstance(value, dict) and "credential" in value:
        if persona is None:
            raise SequenceError(f"{where}: a credential argument needs --persona")
        try:
            return personas.resolve_credential(persona, value["credential"])
        except personas.MissingCredential:
            raise SequenceError(
                f"{where}: persona {persona.name!r} cannot supply credential {value['credential']!r}"
            ) from None
    if isinstance(value, dict) and "artifact" in value:
        if artifacts is None:
            raise SequenceError(f"{where}: an artifact argument needs --artifacts")
        return str(artifacts / value["artifact"])
    return value


def resolve_steps(
    steps: list[SequenceStep], *, persona: personas.Persona | None, artifacts: Path | None
) -> tuple[list[str], list[dict[str, Any]]]:
    """Turn steps into ``run_sequence`` inputs, resolving every reference first.

    Everything is resolved before the caller launches anything, so a sequence
    naming a credential its persona cannot supply fails without a browser.
    """
    root = _artifacts_root(artifacts)
    names: list[str] = []
    resolved: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        resolved.append(
            {
                key: _resolve_value(f"step {index} argument {key!r}", value, persona, root)
                for key, value in step.args.items()
            }
        )
        names.append(step.macro)
    return names, resolved

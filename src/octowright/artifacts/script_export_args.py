# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""How an exported macro CLI takes its arguments: names, flags, defaults, the call.

Split out of ``script_export`` (at the repository's LOC ceiling), which renders
the script around these pieces.
"""

from __future__ import annotations

import keyword
import re
from typing import Any

from octowright.macros.privacy import MacroArgPrivacy, is_sensitive_arg_key, scrub_sensitive_values


def _function_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", name.strip().lower()).strip("_") or "macro"
    if cleaned[0].isdigit():
        cleaned = f"macro_{cleaned}"
    return f"run_{cleaned}"


def _parameters(macro: dict[str, Any]) -> list[tuple[str, str]]:
    raw = macro.get("parameters", [])
    if isinstance(raw, dict):
        raw = list(raw)
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    parameters = []
    for param in raw:
        if not isinstance(param, str):
            continue
        ident = _identifier(param)
        base = ident
        index = 2
        while ident in seen:
            ident = f"{base}_{index}"
            index += 1
        seen.add(ident)
        parameters.append((param, ident))
    return parameters


def _identifier(value: str) -> str:
    cleaned = re.sub(r"\W+", "_", value.strip()).strip("_") or "arg"
    if cleaned[0].isdigit():
        cleaned = f"arg_{cleaned}"
    if keyword.iskeyword(cleaned):
        cleaned = f"{cleaned}_"
    return cleaned


def _parser_line(parameter: tuple[str, str], args: dict[str, Any] | None, privacy: MacroArgPrivacy) -> str:
    original, ident = parameter
    flag = re.sub(r"[^A-Za-z0-9-]+", "-", original.strip()).strip("-") or ident.replace("_", "-")
    default = _safe_default(original, args, privacy)
    return f"    parser.add_argument('--{flag}', dest='{ident}', default={default!r})"


def _signature(parameters: list[tuple[str, str]], include_evidence: bool) -> str:
    fn_params = [f"{ident}: str = ''" for _original, ident in parameters]
    if include_evidence:
        fn_params.append("evidence_dir: str = ''")
    fn_params.append("trusted_origins: tuple[str, ...] = ()")
    return ", ".join(fn_params)


def _parser_lines(
    parameters: list[tuple[str, str]],
    args: dict[str, Any] | None,
    include_evidence: bool,
    privacy: MacroArgPrivacy,
) -> str:
    parser_lines = "\n".join(_parser_line(param, args, privacy) for param in parameters)
    if include_evidence:
        parser_lines = _append_parser_line(
            parser_lines,
            "    parser.add_argument('--evidence-dir', default='', help='Optional directory for result/evidence logs')",
        )
    return _append_parser_line(
        parser_lines,
        "    parser.add_argument('--trusted-origin', action='append', default=[], "
        "help='Origin (scheme://host[:port]) the macro may send a credential header to and type a "
        "credential on; repeatable')",
    )


def _call_args(parameters: list[tuple[str, str]], include_evidence: bool) -> list[str]:
    call_args = [f"{ident}=ns.{ident}" for _original, ident in parameters]
    if include_evidence:
        call_args.append("evidence_dir=ns.evidence_dir")
    call_args.append("trusted_origins=tuple(ns.trusted_origin)")
    return call_args


def _append_parser_line(existing: str, line: str) -> str:
    return f"{existing}\n{line}" if existing else line


def _safe_default(param: str, args: dict[str, Any] | None, privacy: MacroArgPrivacy) -> str:
    """A default the script may carry in its source: never a classified value.

    Classified the way live replay classifies it (``privacy``), so an argument
    the macro feeds to ``expect_no_text``, or declares sensitive in its
    ``parameter_specs``, is never baked in, whatever its name.
    """
    if is_sensitive_arg_key(param) or param in privacy.assertion_args or param in privacy.credential_args:
        return ""
    # A None default renders as "" below, the same as this one.
    value = (args or {}).get(param, "")  # pragma: no mutate
    rendered = str(value) if value is not None else ""
    scrubbed = scrub_sensitive_values(rendered, privacy.blind_scrub(args or {}))
    return rendered if scrubbed == rendered else ""


def _args_dict(parameters: list[tuple[str, str]]) -> str:
    return "{" + ", ".join(f"{original!r}: {ident}" for original, ident in parameters) + "}"

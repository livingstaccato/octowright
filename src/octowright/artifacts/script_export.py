# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import keyword
import re
from pathlib import Path
from typing import Any

from octowright._paths import atomic_write_text
from octowright.artifacts.script_export_actions import STATE_HELPERS, render_dispatch_chain
from octowright.macros.privacy import (
    ARG_PRIVACY_CLASSIFIER_VERSION,
    FIELD_NAME_PATTERN,
    SENSITIVE_KEY_PAIRS,
    SENSITIVE_KEY_TOKENS,
    is_sensitive_arg_key,
    scrub_sensitive_values,
    sensitive_arg_values,
)


def render_macro_cli(
    *,
    name: str,
    macro: dict[str, Any],
    args: dict[str, Any] | None = None,
    include_evidence: bool = True,
) -> str:
    parameters = _parameters(macro)
    fn_name = _function_name(name)
    signature = _signature(parameters, include_evidence)
    action_json = json.dumps(macro.get("actions", []), indent=2)
    parser_lines = _parser_lines(parameters, args, include_evidence)
    call_args = _call_args(parameters, include_evidence)
    doc = f"Import-safe CLI wrapper for Octowright macro {name}."
    placeholder_re = r"\{\{([^}]+)\}\}"
    evidence_helpers, evidence_setup, _evidence_close = _evidence_render_parts(include_evidence)
    state_helpers = STATE_HELPERS
    # 20 spaces: inside `for ... in enumerate(ACTIONS)` inside the raw-action
    # handler and cleanup `try`, then `async with`, then the function body.
    dispatch_chain = render_dispatch_chain(" " * 20)

    return f"""\
{doc!r}

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

from playwright.async_api import async_playwright

ACTIONS_JSON = {action_json!r}
ACTIONS: list[dict[str, Any]] = json.loads(ACTIONS_JSON)
_ARG_PRIVACY_CLASSIFIER_VERSION = {ARG_PRIVACY_CLASSIFIER_VERSION!r}
_SENSITIVE_KEY_TOKENS = {tuple(sorted(SENSITIVE_KEY_TOKENS))!r}
_SENSITIVE_KEY_PAIRS = {tuple(sorted(SENSITIVE_KEY_PAIRS))!r}
_MAX_ENCODING_DEPTH = 3
_LIFECYCLE_SKIP = {{"launch", "close", "snapshot"}}
_PLACEHOLDER_RE = {placeholder_re!r}
_FIELD_NAME_RE = re.compile({FIELD_NAME_PATTERN!r})


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _key_tokens(key: object) -> tuple[str, ...]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return tuple(part for part in re.split(r"[^A-Za-z0-9]+", text.lower()) if part)


def _is_sensitive_arg_key(key: object) -> bool:
    tokens = _key_tokens(key)
    adjacent = set(zip(tokens, tokens[1:]))
    return any(token in _SENSITIVE_KEY_TOKENS for token in tokens) or bool(
        adjacent.intersection(_SENSITIVE_KEY_PAIRS)
    )


def _redact_nested_args(value: Any) -> Any:
    if isinstance(value, dict):
        return {{
            str(key): "<redacted>"
            if _is_sensitive_arg_key(key)
            else _redact_nested_args(item)
            for key, item in value.items()
        }}
    if isinstance(value, list):
        return [_redact_nested_args(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_nested_args(item) for item in value)
    return value


def _redact_args(args: dict[str, Any]) -> dict[str, Any]:
    redacted = {{
        str(key): "<redacted>"
        if _is_sensitive_arg_key(key)
        else _redact_nested_args(value)
        for key, value in args.items()
    }}
    return _redact_value(redacted, _sensitive_arg_values(args))


def _collect_sensitive_values(value: Any, *, inherited: bool) -> set[str]:
    values: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            branch_sensitive = inherited or _is_sensitive_arg_key(key)
            if inherited and key not in (None, "") and not _FIELD_NAME_RE.fullmatch(str(key)):
                values.add(str(key))
            values.update(_collect_sensitive_values(item, inherited=branch_sensitive))
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            values.update(_collect_sensitive_values(item, inherited=inherited))
    elif inherited and value not in (None, ""):
        values.add(str(value))
    return values


def _sensitive_arg_values(args: dict[str, Any]) -> list[str]:
    values: set[str] = set()
    for key, value in args.items():
        values.update(
            _collect_sensitive_values(value, inherited=_is_sensitive_arg_key(key))
        )
    return sorted(values, key=len, reverse=True)


def _serialized_variants(value: str) -> list[str]:
    variants: set[str] = {{
        value,
        json.dumps(value, ensure_ascii=True)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
    }}
    frontier = set(variants)
    for _ in range(_MAX_ENCODING_DEPTH):
        frontier = {{
            encoded
            for item in frontier
            for encoded in (quote(item, safe=""), quote_plus(item, safe=""))
        }}
        variants.update(frontier)
    return sorted((item for item in variants if item), key=len, reverse=True)


def _redact_value(value: Any, sensitive_values: list[str]) -> Any:
    if isinstance(value, str):
        redacted = value
        for sensitive in sensitive_values:
            variants = _serialized_variants(sensitive)
            for variant in variants:
                flags = re.IGNORECASE if "%" in variant else 0
                if len(sensitive) < 4:
                    redacted = re.sub(
                        rf"(?<![A-Za-z0-9]){{re.escape(variant)}}(?![A-Za-z0-9])",
                        "<redacted>",
                        redacted,
                        flags=flags,
                    )
                else:
                    redacted = re.sub(
                        re.escape(variant), "<redacted>", redacted, flags=flags
                    )
        return redacted
    if isinstance(value, dict):
        return {{
            str(_redact_value(str(key), sensitive_values)): _redact_value(item, sensitive_values)
            for key, item in value.items()
        }}
    if isinstance(value, list):
        return [_redact_value(item, sensitive_values) for item in value]
    return value


def _redact_action(action: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    redacted = {{key: _redact_value(value, _sensitive_arg_values(args)) for key, value in action.items()}}
    if redacted.get("action") in {{"fill", "type", "fill_by"}}:
        for key in ("value", "text"):
            if key in redacted:
                redacted[key] = "<redacted>"
    return redacted


def _resolve(value: Any, args: dict[str, str]) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in args:
                raise KeyError(f"placeholder {{{{key}}}} has no matching CLI argument")
            return str(args[key])

        return re.sub(_PLACEHOLDER_RE, repl, value)
    if isinstance(value, dict):
        return {{key: _resolve(item, args) for key, item in value.items()}}
    if isinstance(value, list):
        return [_resolve(item, args) for item in value]
    return value


# Resolve a semantic (ARIA) locator, mirroring session/locators.build_locator.
# `exact` is forwarded because dropping it silently changes WHICH element the
# script acts on: Playwright renders exact matching as a case-sensitive
# whole-string selector and inexact as a case-insensitive substring one.
def _locator(page: Any, action: dict[str, Any]) -> Any:
    if action.get("role") is not None:
        options: dict[str, Any] = {{}}
        if action.get("role_name") is not None:
            options["name"] = action["role_name"]
            if action.get("role_exact"):
                options["exact"] = True
        return page.get_by_role(action["role"], **options)
    if action.get("label") is not None:
        return page.get_by_label(action["label"], exact=bool(action.get("label_exact")))
    if action.get("text") is not None:
        return page.get_by_text(action["text"], exact=bool(action.get("text_exact")))
    if action.get("test_id") is not None:
        return page.get_by_test_id(action["test_id"])
    raise RuntimeError(f"action has no ARIA locator: {{action!r}}")

{state_helpers}
{evidence_helpers}
async def {fn_name}({signature}) -> dict[str, int]:
    args = {_args_dict(parameters)}
{evidence_setup}    print(json.dumps({{"event": "args", "args": _redact_args(args)}}, sort_keys=True))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
        except BaseException:
            try:
                await browser.close()
            except Exception:
                pass
            raise
        # Tabs, the active iframe, installed route mocks and the dialog policy
        # are session state live; in a standalone script they live here.
        state: dict[str, Any] = {{
            "pages": [page],
            "index": 0,
            "frame": None,
            "routes": {{}},
            "header_routes": {{}},
            "dialog_policy": "manual",
            "dialog_prompt_text": None,
            "dialog_pages": [],
        }}
        executed = 0
        skipped = 0
        failure = None
        safe_error = None
        result = None
        try:
            try:
                for index, raw_action in enumerate(ACTIONS):
                    action = _resolve(raw_action, args)
                    kind = action.get("action")
                    log_record = {{"event": "action", "index": index, "action": _redact_action(action, args)}}
                    print(json.dumps(log_record, sort_keys=True))
                    if evidence is not None:
                        evidence.record(log_record)
                    if kind in _LIFECYCLE_SKIP:
                        skipped += 1
{dispatch_chain}
                result = {{"executed": executed, "skipped": skipped}}
            except Exception as exc:
                safe_error = str(_redact_value(str(exc), _sensitive_arg_values(args)))
            if safe_error is None:
                if evidence is not None:
                    try:
                        evidence.finish(result)
                    except Exception as secondary:
                        failure = RuntimeError(
                            str(_redact_value(str(secondary), _sensitive_arg_values(args)))
                        )
            else:
                if evidence is not None:
                    try:
                        evidence.finish({{"status": "failed", "error": safe_error}})
                    except Exception as secondary:
                        safe_error += "; evidence cleanup: " + str(
                            _redact_value(str(secondary), _sensitive_arg_values(args))
                        )
                failure = RuntimeError(safe_error)
        finally:
            active_error = sys.exception()
            try:
                await browser.close()
            except Exception as secondary:
                if active_error is None:
                    safe_close = str(_redact_value(str(secondary), _sensitive_arg_values(args)))
                    failure = RuntimeError(
                        safe_close if failure is None else f"{{failure}}; browser close: {{safe_close}}"
                    )
        if failure is not None:
            raise failure from None
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
{parser_lines}
    ns = parser.parse_args()
    try:
        result = asyncio.run({fn_name}({", ".join(call_args)}))
    except Exception as exc:
        print(json.dumps({{"event": "error", "error": str(exc)}}, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
"""


def write_macro_cli(
    *,
    path: Path,
    name: str,
    macro: dict[str, Any],
    args: dict[str, Any] | None = None,
    include_evidence: bool = True,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        render_macro_cli(name=name, macro=macro, args=args, include_evidence=include_evidence),
        encoding="utf-8",
    )
    return path


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


def _parser_line(parameter: tuple[str, str], args: dict[str, Any] | None) -> str:
    original, ident = parameter
    flag = re.sub(r"[^A-Za-z0-9-]+", "-", original.strip()).strip("-") or ident.replace("_", "-")
    default = _safe_default(original, args)
    return f"    parser.add_argument('--{flag}', dest='{ident}', default={default!r})"


def _signature(parameters: list[tuple[str, str]], include_evidence: bool) -> str:
    fn_params = [f"{ident}: str = ''" for _original, ident in parameters]
    if include_evidence:
        fn_params.append("evidence_dir: str = ''")
    return ", ".join(fn_params)


def _parser_lines(parameters: list[tuple[str, str]], args: dict[str, Any] | None, include_evidence: bool) -> str:
    parser_lines = "\n".join(_parser_line(param, args) for param in parameters)
    if include_evidence:
        parser_lines = _append_parser_line(
            parser_lines,
            "    parser.add_argument('--evidence-dir', default='', help='Optional directory for result/evidence logs')",
        )
    return parser_lines or "    pass"


def _call_args(parameters: list[tuple[str, str]], include_evidence: bool) -> list[str]:
    call_args = [f"{ident}=ns.{ident}" for _original, ident in parameters]
    if include_evidence:
        call_args.append("evidence_dir=ns.evidence_dir")
    return call_args


def _evidence_render_parts(include_evidence: bool) -> tuple[str, str, str]:
    if not include_evidence:
        return "", "    evidence = None\n", ""
    return _evidence_helpers(), "    evidence = _Evidence(evidence_dir)\n", "            evidence.finish(result)\n"


def _append_parser_line(existing: str, line: str) -> str:
    return f"{existing}\n{line}" if existing else line


def _safe_default(param: str, args: dict[str, Any] | None) -> str:
    if is_sensitive_arg_key(param):
        return ""
    value = (args or {}).get(param, "")
    rendered = str(value) if value is not None else ""
    scrubbed = scrub_sensitive_values(rendered, sensitive_arg_values(args or {}))
    return rendered if scrubbed == rendered else ""


def _args_dict(parameters: list[tuple[str, str]]) -> str:
    return "{" + ", ".join(f"{original!r}: {ident}" for original, ident in parameters) + "}"


def _evidence_helpers() -> str:
    return """\
class _Evidence:
    def __init__(self, evidence_dir: str) -> None:
        self.dir = Path(evidence_dir).expanduser() if evidence_dir else None
        self.records: list[dict[str, Any]] = []
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)

    def record(self, payload: dict[str, Any]) -> None:
        record = {"ts": _now(), **payload}
        self.records.append(record)
        if self.dir is not None:
            with (self.dir / "action-log.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\\n")

    def finish(self, result: dict[str, Any]) -> None:
        if self.dir is None:
            return
        (self.dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        (self.dir / "evidence.json").write_text(
            json.dumps({"records": self.records}, indent=2, sort_keys=True),
            encoding="utf-8",
        )


"""

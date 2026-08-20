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

_SENSITIVE_DEFAULT_PARTS = ("password", "passwd", "pwd", "token", "secret", "email", "username")


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
    evidence_helpers, evidence_setup, evidence_close = _evidence_render_parts(include_evidence)
    state_helpers = STATE_HELPERS
    # 16 spaces: inside `for ... in enumerate(ACTIONS)` inside `try` inside
    # `async with` inside the function body.
    dispatch_chain = render_dispatch_chain(" " * 16)

    return f"""\
{doc!r}

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

ACTIONS_JSON = {action_json!r}
ACTIONS: list[dict[str, Any]] = json.loads(ACTIONS_JSON)
_SENSITIVE_PARTS = {_SENSITIVE_DEFAULT_PARTS!r}
_LIFECYCLE_SKIP = {{"launch", "close", "snapshot"}}
_PLACEHOLDER_RE = {placeholder_re!r}


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _redact_args(args: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {{}}
    for key, value in args.items():
        lowered = key.lower()
        redacted[key] = "<redacted>" if any(part in lowered for part in _SENSITIVE_PARTS) else value
    return redacted


def _sensitive_arg_values(args: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key, value in args.items():
        lowered = key.lower()
        if value and any(part in lowered for part in _SENSITIVE_PARTS):
            values.append(str(value))
    return values


def _redact_value(value: Any, sensitive_values: list[str]) -> Any:
    if isinstance(value, str):
        redacted = value
        for sensitive in sensitive_values:
            redacted = redacted.replace(sensitive, "<redacted>")
        return redacted
    if isinstance(value, dict):
        return {{key: _redact_value(item, sensitive_values) for key, item in value.items()}}
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
        page = await browser.new_page()
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
{evidence_close}            return result
        except Exception as exc:
            if evidence is not None:
                evidence.finish({{"status": "failed", "error": str(exc)}})
            raise
        finally:
            await browser.close()


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
    if any(part in param.lower() for part in _SENSITIVE_DEFAULT_PARTS):
        return ""
    value = (args or {}).get(param, "")
    return str(value) if value is not None else ""


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

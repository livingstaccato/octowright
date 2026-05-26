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

_SENSITIVE_DEFAULT_PARTS = ("password", "passwd", "pwd", "token", "secret", "email", "username")


def render_macro_cli(*, name: str, macro: dict[str, Any], args: dict[str, Any] | None = None) -> str:
    parameters = _parameters(macro)
    fn_name = _function_name(name)
    signature = ", ".join(f"{ident}: str = ''" for _original, ident in parameters)
    action_json = json.dumps(macro.get("actions", []), indent=2)
    parser_lines = "\n".join(_parser_line(param, args) for param in parameters) or "    pass"
    call_args = ", ".join(f"{ident}=ns.{ident}" for _original, ident in parameters)
    doc = f"Import-safe CLI wrapper for Octowright macro {name}."

    return f"""\
{doc!r}

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from playwright.async_api import async_playwright

ACTIONS: list[dict[str, Any]] = {action_json}


def _resolve(value: Any, args: dict[str, str]) -> Any:
    if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
        return args.get(value[2:-2], "")
    return value


async def {fn_name}({signature}) -> dict[str, int]:
    args = {_args_dict(parameters)}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        executed = 0
        skipped = 0
        try:
            for action in ACTIONS:
                kind = action.get("action")
                if kind == "navigate":
                    await page.goto(_resolve(action["url"], args))
                    executed += 1
                elif kind == "click":
                    await page.click(_resolve(action["selector"], args))
                    executed += 1
                elif kind == "fill":
                    await page.fill(_resolve(action["selector"], args), _resolve(action.get("value", ""), args))
                    executed += 1
                else:
                    skipped += 1
            return {{"executed": executed, "skipped": skipped}}
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
{parser_lines}
    ns = parser.parse_args()
    result = asyncio.run({fn_name}({call_args}))
    print(result)


if __name__ == "__main__":
    main()
"""


def write_macro_cli(*, path: Path, name: str, macro: dict[str, Any], args: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, render_macro_cli(name=name, macro=macro, args=args), encoding="utf-8")
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


def _safe_default(param: str, args: dict[str, Any] | None) -> str:
    if any(part in param.lower() for part in _SENSITIVE_DEFAULT_PARTS):
        return ""
    value = (args or {}).get(param, "")
    return str(value) if value is not None else ""


def _args_dict(parameters: list[tuple[str, str]]) -> str:
    return "{" + ", ".join(f"{original!r}: {ident}" for original, ident in parameters) + "}"

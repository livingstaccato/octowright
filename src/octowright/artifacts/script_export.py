# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import inspect
import json
import keyword
import re
from pathlib import Path
from typing import Any

from octowright import credential_input, credential_sinks, drawn_text
from octowright._paths import atomic_write_text
from octowright.artifacts.script_export_actions import STATE_HELPERS, render_dispatch_chain
from octowright.config_paths import upload_staging_dir, user_config_dir
from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS
from octowright.macros import scrub_admission
from octowright.macros.calls import actions_assert_network_clean
from octowright.macros.privacy import (
    ARG_PRIVACY_CLASSIFIER_VERSION,
    BLIND_SCRUB_POLICY_ENV,
    CONTEXTUAL_TOKEN_TOKENS,
    CREDENTIAL_SUBSTRING_TOKENS,
    CREDENTIAL_TOKEN_TOKENS,
    DEPLURALIZE_MIN_LENGTH,
    FIELD_NAME_PATTERN,
    IDENTITY_TOKEN_TOKENS,
    PLACEHOLDER_PATTERN,
    SENSITIVE_KEY_PAIRS,
    SUBSTRING_TOKENS,
    TOKEN_TOKENS,
    MacroArgPrivacy,
    _serialized_variants,
    is_sensitive_arg_key,
    scrub_sensitive_values,
)
from octowright.session.upload_paths import check_upload_path, upload_roots


def _module_source(module: Any) -> str:
    """A standard-library-only module as script source: everything after its ``__future__`` import.

    The script opens with that import itself, and it may only appear first.
    """
    _header, marker, body = inspect.getsource(module).partition("from __future__ import annotations\n")
    if not marker:
        raise RuntimeError(f"{module.__name__} must import annotations from __future__ to be rendered")
    return body.strip()


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
    # The macro's positional privacy -- the same view live replay builds -- so
    # ``args_used`` and the script's own log agree about one macro.
    privacy = MacroArgPrivacy.for_macro(macro.get("actions", []))
    hard_redacted_args = sorted(privacy.assertion_args)
    # Decided at render time by the predicate replay uses; a text search of
    # ACTIONS_JSON also matched the string inside a selector or a typed value.
    watch_network = actions_assert_network_clean(macro.get("actions", []))
    parser_lines = _parser_lines(parameters, args, include_evidence, privacy)
    call_args = _call_args(parameters, include_evidence)
    doc = f"Import-safe CLI wrapper for Octowright macro {name}."
    evidence_helpers, evidence_setup, _evidence_close = _evidence_render_parts(include_evidence)
    state_helpers = STATE_HELPERS
    # 20 spaces: inside `for ... in enumerate(ACTIONS)` inside the raw-action
    # handler and cleanup `try`, then `async with`, then the function body.
    dispatch_chain = render_dispatch_chain(" " * 20)
    # Rendered from the live scrubber's own source rather than hand-mirrored:
    # the copy had already lost the HTML-escaped spellings.
    serialized_variants = inspect.getsource(_serialized_variants).rstrip()
    # Same reason, whole module: expect_no_text's collector, comparison, limit,
    # frame rules and messages are the ones replay runs (see drawn_text).
    drawn_text_source = _module_source(drawn_text)
    # And the credential-sink guard: the sink set, alias rules, own-origin
    # exemption and credential-fill origin check that macro_run enforces. The
    # script substituted with a bare re.sub before, so it ran what replay refused.
    credential_sinks_source = _module_source(credential_sinks)
    # And how a credential step types: one key at a time into a checked
    # document, the fill into a checked element -- the session's own helpers.
    credential_input_source = _module_source(credential_input)
    # And which identity/contextual values are too short or common to scrub (#247).
    scrub_admission_source = _module_source(scrub_admission)
    # The live upload allowlist, so an exported set_input_files cannot read a
    # file macro_run would refuse (~/.ssh/id_rsa).
    # The default staging dir is rendered as its resolver, not its value: the
    # value is the exporting user's absolute path, which names their home and
    # does not exist on CI or for anyone else.
    upload_source = "\n\n\n".join(
        inspect.getsource(fn).rstrip() for fn in (user_config_dir, upload_staging_dir, upload_roots, check_upload_path)
    )

    return f"""\
{doc!r}

from __future__ import annotations

import argparse
import asyncio
import html
import json
import os
import platform
import re
import sys
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

from playwright.async_api import async_playwright

ACTIONS_JSON = {action_json!r}
# Arguments substituted into an expect_no_text's text: they ARE the forbidden
# text, so they are redacted whatever they are named (as live replay does).
_HARD_REDACTED_ARGS = frozenset({hard_redacted_args!r})
ACTIONS: list[dict[str, Any]] = json.loads(ACTIONS_JSON)
_ARG_PRIVACY_CLASSIFIER_VERSION = {ARG_PRIVACY_CLASSIFIER_VERSION!r}
_SUBSTRING_TOKENS = {tuple(sorted(SUBSTRING_TOKENS))!r}
_TOKEN_TOKENS = {tuple(sorted(TOKEN_TOKENS))!r}
_CREDENTIAL_SUBSTRING_TOKENS = {tuple(sorted(CREDENTIAL_SUBSTRING_TOKENS))!r}
_CREDENTIAL_TOKEN_TOKENS = {tuple(sorted(CREDENTIAL_TOKEN_TOKENS))!r}
_IDENTITY_TOKEN_TOKENS = {tuple(sorted(IDENTITY_TOKEN_TOKENS))!r}
_CONTEXTUAL_TOKEN_TOKENS = {tuple(sorted(CONTEXTUAL_TOKEN_TOKENS))!r}
_SENSITIVE_KEY_PAIRS = {tuple(sorted(SENSITIVE_KEY_PAIRS))!r}
_DEPLURALIZE_MIN_LENGTH = {DEPLURALIZE_MIN_LENGTH!r}
_BLIND_SCRUB_POLICY_ENV = {BLIND_SCRUB_POLICY_ENV!r}
_TIER_RANK = {{"contextual": 1, "identity": 2, "credential": 3}}
_MAX_ENCODING_DEPTH = 3
_DEFAULT_ACTION_TIMEOUT_MS = {DEFAULT_ACTION_TIMEOUT_MS}
# What an exported fill/type waits when the step names no timeout: it passes
# none, so Playwright's own default applies, and a credential step, which has
# to pass one, passes the same. The script sets no default timeout of its own.
_PLAYWRIGHT_DEFAULT_TIMEOUT_MS = 30000
_LIFECYCLE_SKIP = {{"launch", "close", "snapshot"}}
_PLACEHOLDER_RE = {PLACEHOLDER_PATTERN!r}
_FIELD_NAME_RE = re.compile({FIELD_NAME_PATTERN!r})


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _key_tokens(key: object) -> tuple[str, ...]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    return tuple(part for part in re.split(r"[^A-Za-z0-9]+", text.lower()) if part)


def _depluralized(token: str) -> str:
    if len(token) >= _DEPLURALIZE_MIN_LENGTH and token.endswith("s"):
        return token[:-1]
    return token


def _matches(
    key: object,
    substrings: tuple[str, ...],
    whole_tokens: tuple[str, ...],
    *,
    include_pairs: bool = False,
) -> bool:
    tokens = _key_tokens(key)
    if any(token in "_".join(tokens) for token in substrings):
        return True
    candidates = set(tokens) | {{_depluralized(token) for token in tokens}}
    if candidates.intersection(whole_tokens):
        return True
    adjacent = set(zip(tokens, tokens[1:]))
    return include_pairs and bool(adjacent.intersection(_SENSITIVE_KEY_PAIRS))


def _is_sensitive_arg_key(key: object) -> bool:
    return _matches(key, _SUBSTRING_TOKENS, _TOKEN_TOKENS, include_pairs=True)


def _privacy_tier(key: object) -> str | None:
    if _matches(
        key,
        _CREDENTIAL_SUBSTRING_TOKENS,
        _CREDENTIAL_TOKEN_TOKENS,
        include_pairs=True,
    ):
        return "credential"
    if _matches(key, (), _IDENTITY_TOKEN_TOKENS):
        return "identity"
    if _matches(key, (), _CONTEXTUAL_TOKEN_TOKENS):
        return "contextual"
    return None


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
        if _is_sensitive_arg_key(key) or key in _HARD_REDACTED_ARGS
        else _redact_nested_args(value)
        for key, value in args.items()
    }}
    policy = _blind_scrub_policy()
    blind_policy = "credentials" if policy == "reject" else policy
    return _redact_value(redacted, _blind_scrub_arg_values(args, policy=blind_policy))


def _stronger_tier(inherited: str | None, own: str | None) -> str | None:
    if inherited is None:
        return own
    if own is None or _TIER_RANK[inherited] >= _TIER_RANK[own]:
        return inherited
    return own


def _child_path(path: str, key: object) -> str:
    rendered = str(key)
    if _FIELD_NAME_RE.fullmatch(rendered):
        return f"{{path}}.{{rendered}}" if path else rendered
    return f"{{path}}[<key>]"


def _collect_classified_values(
    value: Any,
    *,
    inherited: str | None,
    path: str,
) -> set[tuple[str, str, str]]:
    values: set[tuple[str, str, str]] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if inherited is not None and key not in (None, "") and not _FIELD_NAME_RE.fullmatch(str(key)):
                values.add((str(key), f"{{path}}[<key>]", inherited))
            branch_tier = _stronger_tier(inherited, _privacy_tier(key))
            values.update(
                _collect_classified_values(
                    item,
                    inherited=branch_tier,
                    path=_child_path(path, key),
                )
            )
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            values.update(
                _collect_classified_values(
                    item,
                    inherited=inherited,
                    path=f"{{path}}[{{index}}]",
                )
            )
    elif isinstance(value, (set, frozenset)):
        for index, item in enumerate(sorted(value, key=repr)):
            values.update(
                _collect_classified_values(
                    item,
                    inherited=inherited,
                    path=f"{{path}}[{{index}}]",
                )
            )
    elif inherited is not None and value not in (None, ""):
        values.add((str(value), path, inherited))
    return values


def _classified_arg_values(args: dict[str, Any]) -> list[tuple[str, str, str]]:
    values: set[tuple[str, str, str]] = set()
    for key, value in args.items():
        values.update(
            _collect_classified_values(
                value,
                inherited=_privacy_tier(key),
                path=str(key),
            )
        )
    return sorted(values, key=lambda item: (item[1], -_TIER_RANK[item[2]], item[0]))


def _sensitive_arg_values(args: dict[str, Any]) -> list[str]:
    return sorted({{item[0] for item in _classified_arg_values(args)}}, key=lambda value: (-len(value), value))


def _blind_scrub_policy() -> str:
    raw = os.environ.get(_BLIND_SCRUB_POLICY_ENV, "credentials").strip().lower()
    if raw in {{"credentials", "all", "reject"}}:
        return raw
    raise ValueError(
        f"{{_BLIND_SCRUB_POLICY_ENV}} must be 'credentials', 'all', or 'reject'"
    )


def _blind_scrub_arg_values(args: dict[str, Any], *, policy: str | None = None) -> list[str]:
    resolved = policy or _blind_scrub_policy()
    classified = _classified_arg_values(args)
    rejected = [item for item in classified if item[2] != "credential"]
    if resolved == "reject" and rejected:
        details = ", ".join(sorted({{f"{{item[1]}} ({{item[2]}})" for item in rejected}}))
        raise ValueError(f"non-credential classified arguments refused: {{details}}")
    credentials = {{item[0] for item in classified if item[2] == "credential"}}
    # Under "all", an identity/contextual value too short or common to scrub is
    # left out, as live replay leaves it out (scrub_admission, #247).
    selected = credentials | {{
        item[0]
        for item in classified
        if resolved == "all" and item[2] != "credential" and scrub_exemption_reason(item[0]) is None
    }}
    return sorted(selected, key=lambda value: (-len(value), value))


{scrub_admission_source}


{serialized_variants}


{drawn_text_source}


{credential_sinks_source}


{credential_input_source}


{upload_source}


def _is_credential_arg(key: str) -> bool:
    return _privacy_tier(key) == "credential"


def _upload_paths(paths: Any) -> list[str]:
    # Resolved here, where the script runs, by the rule replay uses: the
    # running user's config dir unless OCTOWRIGHT_UPLOAD_STAGING_DIR says otherwise.
    roots = upload_roots(upload_staging_dir(), os.environ.get("OCTOWRIGHT_UPLOAD_ROOTS", ""))
    return [str(check_upload_path(path, roots)) for path in paths or []]


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


def _redact_action(
    action: dict[str, Any],
    sensitive_values: list[str],
) -> dict[str, Any]:
    redacted = {{key: _redact_value(value, sensitive_values) for key, value in action.items()}}
    if redacted.get("action") in _REDACT_VALUE_ACTIONS:
        for key in ("value", "text"):
            if key in redacted:
                redacted[key] = "<redacted>"
    return redacted


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

def _check_credential_fill(
    state: dict[str, Any], index: int, action: dict[str, Any], trusted: Any, url: Any = None
) -> None:
    # The live rule (offsite_credential_origin): read off the active frame
    # immediately before the step, then again off the frame that owns the
    # document the value is typed into (_credential_check). Warn mode prints
    # each origin once per step.
    if url is None:
        url = getattr(_target(state), "url", "")
    shown = offsite_credential_origin(action, url, trusted)
    if shown is None:
        return
    if credential_fill_mode() != "warn":
        raise credential_fill_refusal(action, shown)
    if (index, shown) in state.setdefault("offsite_reported", set()):
        return
    state["offsite_reported"].add((index, shown))
    record = {{"event": "credential_fill_offsite", "index": index, "action": action.get("action"), "origin": shown}}
    print(json.dumps(record, sort_keys=True), file=sys.stderr)


class _ScriptSession:
    # What credential_input needs of a session. A script has one active page
    # and nothing else driving it, so there is no gate for an operation to take.
    def __init__(self, state: dict[str, Any]) -> None:
        self.page = _page(state)

    def operation(self, _name: str) -> Any:
        return self

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: Any) -> None:
        return None


def _credential_check(state: dict[str, Any], index: int, action: dict[str, Any], trusted: Any) -> Any:
    # A credential step checks the document that receives the value, as it
    # receives it (checked_fill / checked_type): Playwright's wait survives a
    # navigation, a selector can enter a child frame, and keys follow focus.
    # None for any other step, which dispatches as it always did.
    if not action.get(CREDENTIAL_FILL_MARKER):
        return None
    return lambda url: _check_credential_fill(state, index, action, trusted, url=url)


async def _credential_input(action: dict[str, Any], typing: Any) -> None:
    # A credential step the page stopped names the step, as replay does.
    try:
        await typing
    except CredentialInputStopped as exc:
        raise credential_input_stopped(action, str(exc), started=exc.started) from exc


{evidence_helpers}
async def {fn_name}({signature}) -> dict[str, int]:
    args = {_args_dict(parameters)}
    sensitive_values = _blind_scrub_arg_values(args)
    sensitive_values = sorted(
        set(sensitive_values) | {{str(v) for k, v in args.items() if k in _HARD_REDACTED_ARGS and v}},
        key=lambda value: (-len(value), value),
    )
{evidence_setup}    print(json.dumps({{"event": "args", "args": _redact_args(args)}}, sort_keys=True))
    # --trusted-origin plays the part a live session's launch URL does: the one
    # origin a credential header may go to and a credential may be typed on.
    trusted = parse_allowed_origins(list(trusted_origins))
    # Expanded whole before the browser opens, as macro_run does, so a refused
    # sink leaves nothing half done.
    actions = expand_actions(
        ACTIONS, args, is_credential=_is_credential_arg, placeholder=_PLACEHOLDER_RE, trusted_origins=trusted
    )
    # Page code can read a typed credential back, so a run that carries one runs none.
    credential_names = credential_args_in(ACTIONS, is_credential=_is_credential_arg, placeholder=_PLACEHOLDER_RE)
    refuse_page_code(actions, credential_names)
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
            "network": NetworkLedger(),
            # An assertion nested in try/if_selector counts too.
            "watch_network": {watch_network!r},
        }}
        state["sensitive_values"] = sensitive_values
        _watch_network(state, page)
        _watch_context(state, page)
        executed = 0
        skipped = 0
        failure = None
        safe_error = None
        result = None
        try:
            try:
                for index, action in enumerate(actions):
                    kind = action.get("action")
                    log_record = {{
                        "event": "action",
                        "index": index,
                        "action": _redact_action(action, sensitive_values),
                    }}
                    print(json.dumps(log_record, sort_keys=True))
                    if evidence is not None:
                        evidence.record(log_record)
                    _check_credential_fill(state, index, action, trusted)
                    if kind in _LIFECYCLE_SKIP:
                        skipped += 1
{dispatch_chain}
                result = {{"executed": executed, "skipped": skipped}}
            except Exception as exc:
                safe_error = str(_redact_value(str(exc), sensitive_values))
            if safe_error is None:
                if evidence is not None:
                    try:
                        evidence.finish(result)
                    except Exception as secondary:
                        failure = RuntimeError(
                            str(_redact_value(str(secondary), sensitive_values))
                        )
            else:
                if evidence is not None:
                    try:
                        evidence.finish({{"status": "failed", "error": safe_error}})
                    except Exception as secondary:
                        safe_error += "; evidence cleanup: " + str(
                            _redact_value(str(secondary), sensitive_values)
                        )
                failure = RuntimeError(safe_error)
        finally:
            active_error = sys.exception()
            try:
                await browser.close()
            except Exception as secondary:
                if active_error is None:
                    safe_close = str(_redact_value(str(secondary), sensitive_values))
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


def _evidence_render_parts(include_evidence: bool) -> tuple[str, str, str]:
    if not include_evidence:
        return "", "    evidence = None\n", ""
    return _evidence_helpers(), "    evidence = _Evidence(evidence_dir)\n", "            evidence.finish(result)\n"


def _append_parser_line(existing: str, line: str) -> str:
    return f"{existing}\n{line}" if existing else line


def _safe_default(param: str, args: dict[str, Any] | None, privacy: MacroArgPrivacy) -> str:
    """A default the script may carry in its source: never a classified value.

    Classified the way live replay classifies it (``privacy``), so an argument
    the macro feeds to ``expect_no_text`` is never baked in, whatever its name.
    """
    if is_sensitive_arg_key(param) or param in privacy.assertion_args:
        return ""
    value = (args or {}).get(param, "")
    rendered = str(value) if value is not None else ""
    scrubbed = scrub_sensitive_values(rendered, privacy.blind_scrub(args or {}))
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

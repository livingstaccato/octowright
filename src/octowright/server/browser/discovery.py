# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Compact DOM discovery tools for browser sessions."""

from __future__ import annotations

import math
from typing import Any, cast

from octowright.mcp_types import (
    BrowserActionSuggestion,
    BrowserFieldCandidate,
    BrowserFieldsResult,
    BrowserFindFieldResult,
    BrowserHeadingCandidate,
    BrowserLandmarkCandidate,
    BrowserPageOutlineResult,
    BrowserToolAction,
)
from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.server.browser.discovery_links import (
    _action_with_fallback,
    _clean_limit,
    _clean_link_candidate,
)
from octowright.server.profiles import annotate_next_actions_for_profile
from octowright.session._protocols import SessionLike
from octowright.text_scoring import weighted_text_score

_FIELD_EXTRACTOR_JS = """
({ selector, limit }) => {
  const root = selector ? document.querySelector(selector) : document.body;
  if (!root) return [];
  const nodes = Array.from(root.querySelectorAll("input:not([type='hidden']), textarea, select, [contenteditable='true']"));
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };
  const cssPath = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const name = el.getAttribute("name");
    if (name) return `${el.tagName.toLowerCase()}[name=${JSON.stringify(name)}]`;
    const tag = el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (!parent) return tag;
    const peers = Array.from(parent.children).filter((child) => child.tagName === el.tagName);
    if (peers.length <= 1) return tag;
    return `${tag}:nth-of-type(${peers.indexOf(el) + 1})`;
  };
  const labelFor = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria) return aria;
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy.split(/\\s+/).map((id) => document.getElementById(id)?.innerText || "").join(" ").trim();
      if (text) return text;
    }
    if (el.id) {
      const label = document.querySelector(`label[for=${CSS.escape(el.id)}]`);
      if (label?.innerText) return label.innerText;
    }
    const closest = el.closest("label");
    if (closest?.innerText) return closest.innerText;
    return "";
  };
  const out = [];
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    const type = tag === "input" ? (el.getAttribute("type") || "text") : tag;
    const label = labelFor(el).replace(/\\s+/g, " ").trim();
    const placeholder = el.getAttribute("placeholder") || "";
    const name = el.getAttribute("name") || "";
    const value = el instanceof HTMLSelectElement ? el.value : "";
    out.push({
      name,
      type,
      tag,
      label: label || null,
      placeholder: placeholder || null,
      value: value || null,
      selector: cssPath(el),
      required: Boolean(el.required || el.getAttribute("aria-required") === "true"),
      disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
      visible: visible(el),
    });
    if (out.length >= limit) break;
  }
  return out;
}
"""


def _clean_field_candidate(raw: Any, instance_id: str) -> BrowserFieldCandidate:
    if not isinstance(raw, dict):
        return {"name": "", "type": "unknown", "visible": False}
    out: BrowserFieldCandidate = {
        "name": str(raw.get("name") or "")[:200],
        "type": str(raw.get("type") or "unknown")[:80],
        "visible": bool(raw.get("visible")),
    }
    if raw.get("required"):
        out["required"] = True
    if raw.get("disabled"):
        out["disabled"] = True
    _copy_string_fields(
        raw,
        out,
        {
            "tag": 40,
            "label": 200,
            "placeholder": 200,
            "value": 200,
            "selector": 300,
        },
    )
    out["action"] = _field_action(instance_id, out)
    return out


def _copy_string_fields(raw: dict[str, Any], out: BrowserFieldCandidate, fields: dict[str, int]) -> None:
    mutable = cast("dict[str, Any]", out)
    for key, limit in fields.items():
        if raw.get(key):
            mutable[key] = str(raw[key])[:limit]


def _field_action(instance_id: str, field: BrowserFieldCandidate) -> BrowserActionSuggestion:
    selector = field.get("selector")
    if field.get("label"):
        action = _action_with_fallback(
            "browser_fill",
            {"instance_id": instance_id, "label": str(field["label"]), "response_mode": "outline"},
            selector,
        )
        action["requires_args"] = ["value"]
        return action
    role = _field_role(field)
    role_name = str(field.get("placeholder") or field.get("name") or "").strip()
    if role and role_name:
        action = _action_with_fallback(
            "browser_fill",
            {"instance_id": instance_id, "role": role, "role_name": role_name, "response_mode": "outline"},
            selector,
        )
        action["requires_args"] = ["value"]
        return action
    if selector:
        return {
            "tool": "browser_fill",
            "args": {"instance_id": instance_id, "selector": selector, "response_mode": "outline"},
            "requires_args": ["value"],
        }
    return {
        "tool": "browser_fill",
        "args": {"instance_id": instance_id, "response_mode": "outline"},
        "requires_args": ["value"],
    }


def _field_role(field: BrowserFieldCandidate) -> str | None:
    tag = str(field.get("tag") or "").lower()
    field_type = str(field.get("type") or "").lower()
    if tag == "textarea":
        return "textbox"
    if tag == "select":
        return "combobox"
    if field_type == "search":
        return "searchbox"
    if field_type in {"email", "password", "tel", "text", "url", "number"}:
        return "textbox"
    return None


async def _extract_browser_fields(
    session: SessionLike,
    instance_id: str,
    selector: str,
    limit: int,
) -> list[BrowserFieldCandidate]:
    # Every caller already holds a browser_operation(...) lease under its own
    # tool name; this re-enters it (same task) rather than forwarding a
    # second, dynamic operation name -- a fixed literal keeps this scanner-
    # provable without a caller-supplied name.
    async with session.operation("browser_extract_fields"):
        target = session._target()
        candidates = await target.evaluate(_FIELD_EXTRACTOR_JS, {"selector": selector, "limit": _clean_limit(limit)})
    if not isinstance(candidates, list):
        candidates = []
    return [_clean_field_candidate(item, instance_id) for item in candidates]


def _fields_next_actions(instance_id: str) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_find_field", "args": {"instance_id": instance_id, "query": "<intent>", "limit": 8}},
            {"tool": "browser_page_outline", "args": {"instance_id": instance_id, "limit": 20}},
        ]
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Return compact form/input candidates from the active page/frame without "
        "taking an aria snapshot. Use before browser_snapshot when you only need "
        "to find fields to fill. Each candidate includes an action payload with "
        "browser_fill locator args and a CSS fallback; add the value before filling."
    ),
)
async def browser_fields(instance_id: str, selector: str = "body", limit: int = 50) -> BrowserFieldsResult:
    cap = _clean_limit(limit)
    async with browser_operation(pool, instance_id, "browser_fields") as session:
        fields = await _extract_browser_fields(session, instance_id, selector, cap + 1)
        title = await session.page.title()
        target = session._target()
        return {
            "url": target.url,
            "title": title,
            "fields": fields[:cap],
            "total": len(fields),
            "truncated": len(fields) > cap,
            "next_actions": _fields_next_actions(instance_id),
        }


def _score_field(field: BrowserFieldCandidate, query: str) -> tuple[float, str]:
    fields = {
        "label": (field.get("label") or "").lower(),
        "placeholder": (field.get("placeholder") or "").lower(),
        "name": (field.get("name") or "").lower(),
        "type": (field.get("type") or "").lower(),
        "selector": (field.get("selector") or "").lower(),
    }
    score, reason = weighted_text_score(
        fields,
        query,
        reduced_fields={"selector"},
        reduced_score=30.0,
        separators=("_", "-"),
    )
    if field.get("visible"):
        score += 5.0
    if field.get("disabled"):
        score -= 20.0
    return score, reason


@mcp.tool(
    structured_output=False,
    description=(
        "Find likely form/input fields for an intent string using compact page-side "
        "field extraction and local scoring. Returns candidates only; it does not fill. "
        "Each candidate includes an action payload with browser_fill locator args "
        "and a CSS fallback; add the value before filling."
    ),
)
async def browser_find_field(
    instance_id: str,
    query: str,
    selector: str = "body",
    limit: int = 8,
) -> BrowserFindFieldResult:
    cap = _clean_limit(limit)
    async with browser_operation(pool, instance_id, "browser_find_field") as session:
        fields = await _extract_browser_fields(session, instance_id, selector, 200)
        ranked: list[BrowserFieldCandidate] = []
        for rank, field in enumerate(fields, start=1):
            score, reason = _score_field(field, query)
            if score <= 0:
                continue
            item = cast("BrowserFieldCandidate", dict(field))
            item["rank"] = rank
            item["score"] = score
            item["reason"] = reason
            ranked.append(item)
        ranked.sort(key=lambda item: (float(item.get("score") or 0), bool(item.get("visible"))), reverse=True)
        title = await session.page.title()
        target = session._target()
        return {
            "query": query,
            "url": target.url,
            "title": title,
            "fields": ranked[:cap],
            "total": len(ranked),
            "truncated": len(ranked) > cap,
            "next_actions": _fields_next_actions(instance_id),
        }


_PAGE_OUTLINE_JS = """
({ selector, limit }) => {
  const root = selector ? document.querySelector(selector) : document.body;
  if (!root) return { headings: [], landmarks: [], links: [], fields: [], counts: {} };
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };
  const compactText = (el, max = 160) => (el.innerText || el.textContent || "")
    .replace(/\\s+/g, " ")
    .trim()
    .slice(0, max);
  const cssPath = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const name = el.getAttribute("name");
    if (name) return `${el.tagName.toLowerCase()}[name=${JSON.stringify(name)}]`;
    if (el instanceof HTMLAnchorElement && el.getAttribute("href")) {
      return `a[href=${JSON.stringify(el.getAttribute("href"))}]`;
    }
    const tag = el.tagName.toLowerCase();
    const parent = el.parentElement;
    if (!parent) return tag;
    const peers = Array.from(parent.children).filter((child) => child.tagName === el.tagName);
    if (peers.length <= 1) return tag;
    return `${tag}:nth-of-type(${peers.indexOf(el) + 1})`;
  };
  const labelFor = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria) return aria;
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy.split(/\\s+/).map((id) => document.getElementById(id)?.innerText || "").join(" ").trim();
      if (text) return text;
    }
    if (el.id) {
      const label = document.querySelector(`label[for=${JSON.stringify(el.id)}]`);
      if (label?.innerText) return label.innerText;
    }
    const closest = el.closest("label");
    if (closest?.innerText) return closest.innerText;
    return "";
  };
  const links = Array.from(root.querySelectorAll("a[href], [role='link'], button, input[type='submit'], input[type='button']"));
  const fields = Array.from(root.querySelectorAll("input:not([type='hidden']), textarea, select, [contenteditable='true']"));
  const headings = Array.from(root.querySelectorAll("h1,h2,h3,h4,h5,h6,[role='heading']"));
  const landmarks = Array.from(root.querySelectorAll("main,nav,aside,header,footer,section,form,[role='main'],[role='navigation'],[role='search'],[role='banner'],[role='contentinfo'],[role='complementary'],[role='form'],[role='region']"));
  const roleFor = (el) => el.getAttribute("role") || ({
    main: "main",
    nav: "navigation",
    aside: "complementary",
    header: "banner",
    footer: "contentinfo",
    form: "form",
    section: "region",
  }[el.tagName.toLowerCase()] || el.tagName.toLowerCase());
  return {
    headings: headings.slice(0, limit).map((el) => ({
      level: Number(el.getAttribute("aria-level") || el.tagName.slice(1) || 0),
      text: compactText(el),
      selector: cssPath(el),
      visible: visible(el),
    })).filter((item) => item.text),
    landmarks: landmarks.slice(0, limit).map((el) => ({
      role: roleFor(el),
      text: compactText(el, 180),
      selector: cssPath(el),
      visible: visible(el),
    })),
    links: links.slice(0, limit).map((el) => ({
      text: el instanceof HTMLInputElement ? (el.value || el.getAttribute("aria-label") || "") : compactText(el),
      href: el instanceof HTMLAnchorElement ? el.href : el.getAttribute("formaction"),
      role: el.getAttribute("role") || (el instanceof HTMLAnchorElement ? "link" : "button"),
      label: el.getAttribute("aria-label") || null,
      title: el.getAttribute("title") || null,
      selector: cssPath(el),
      visible: visible(el),
    })).filter((item) => item.text || item.label || item.title || item.href),
    fields: fields.slice(0, limit).map((el) => {
      const tag = el.tagName.toLowerCase();
      const type = tag === "input" ? (el.getAttribute("type") || "text") : tag;
      const label = labelFor(el).replace(/\\s+/g, " ").trim();
      return {
        name: el.getAttribute("name") || "",
        type,
        tag,
        label: label || null,
        placeholder: el.getAttribute("placeholder") || null,
        value: el instanceof HTMLSelectElement ? el.value : null,
        selector: cssPath(el),
        required: Boolean(el.required || el.getAttribute("aria-required") === "true"),
        disabled: Boolean(el.disabled || el.getAttribute("aria-disabled") === "true"),
        visible: visible(el),
      };
    }),
    counts: {
      headings: headings.length,
      landmarks: landmarks.length,
      links: links.length,
      fields: fields.length,
    },
  };
}
"""


def _clean_heading_candidate(raw: Any) -> BrowserHeadingCandidate:
    if not isinstance(raw, dict):
        return {"level": 0, "text": "", "visible": False}
    try:
        level = int(raw.get("level") or 0)
    except (TypeError, ValueError):
        level = 0
    if math.isnan(level):
        level = 0
    out: BrowserHeadingCandidate = {
        "level": level,
        "text": str(raw.get("text") or "")[:200],
        "visible": bool(raw.get("visible")),
    }
    if raw.get("selector"):
        out["selector"] = str(raw["selector"])[:300]
    return out


def _clean_landmark_candidate(raw: Any) -> BrowserLandmarkCandidate:
    if not isinstance(raw, dict):
        return {"role": "unknown", "text": "", "visible": False}
    out: BrowserLandmarkCandidate = {
        "role": str(raw.get("role") or "unknown")[:80],
        "text": str(raw.get("text") or "")[:240],
        "visible": bool(raw.get("visible")),
    }
    if raw.get("selector"):
        out["selector"] = str(raw["selector"])[:300]
    return out


def _outline_items(raw: dict[str, Any], key: str) -> list[Any]:
    value = raw.get(key)
    return value if isinstance(value, list) else []


def _outline_counts(raw: dict[str, Any]) -> dict[str, int]:
    counts = raw.get("counts")
    if not isinstance(counts, dict):
        return {"headings": 0, "landmarks": 0, "links": 0, "fields": 0}
    return {key: max(0, int(counts.get(key) or 0)) for key in ("headings", "landmarks", "links", "fields")}


def _outline_next_actions(instance_id: str) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_find_link", "args": {"instance_id": instance_id, "query": "<intent>", "limit": 8}},
            {"tool": "browser_find_field", "args": {"instance_id": instance_id, "query": "<intent>", "limit": 8}},
            {"tool": "browser_read_markdown", "args": {"instance_id": instance_id, "response_mode": "summary"}},
            {
                "tool": "capture_create",
                "args": {"instance_id": instance_id, "source": "snapshot", "response_mode": "summary"},
            },
        ]
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Return a compact DOM outline for the active page/frame: headings, landmarks, "
        "key links, and fields in one bounded call. Use this as the first browse "
        "orientation step before heavier snapshots or markdown reads."
    ),
)
async def browser_page_outline(instance_id: str, selector: str = "body", limit: int = 20) -> BrowserPageOutlineResult:
    cap = _clean_limit(limit)
    async with browser_operation(pool, instance_id, "browser_page_outline") as session:
        target = session._target()
        raw = await target.evaluate(_PAGE_OUTLINE_JS, {"selector": selector, "limit": cap})
        if not isinstance(raw, dict):
            raw = {}
        counts = _outline_counts(raw)
        title = await session.page.title()
        return {
            "url": target.url,
            "title": title,
            "headings": [_clean_heading_candidate(item) for item in _outline_items(raw, "headings")[:cap]],
            "landmarks": [_clean_landmark_candidate(item) for item in _outline_items(raw, "landmarks")[:cap]],
            "links": [_clean_link_candidate(item, instance_id) for item in _outline_items(raw, "links")[:cap]],
            "fields": [_clean_field_candidate(item, instance_id) for item in _outline_items(raw, "fields")[:cap]],
            "counts": counts,
            "truncated": any(count > cap for count in counts.values()),
            "next_actions": _outline_next_actions(instance_id),
        }

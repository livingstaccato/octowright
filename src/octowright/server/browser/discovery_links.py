# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Compact link/button discovery tools for browser sessions."""

from __future__ import annotations

from typing import Any, cast

from octowright.mcp_types import (
    BrowserActionSuggestion,
    BrowserFindLinkResult,
    BrowserLinkCandidate,
    BrowserLinksResult,
    BrowserToolAction,
)
from octowright.server._state import mcp, pool
from octowright.server.profiles import annotate_next_actions_for_profile
from octowright.text_scoring import weighted_text_score

_LINK_EXTRACTOR_JS = """
({ selector, limit }) => {
  const root = selector ? document.querySelector(selector) : document.body;
  if (!root) return [];
  const nodes = Array.from(root.querySelectorAll("a[href], [role='link'], button, input[type='submit'], input[type='button']"));
  const visible = (el) => {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };
  const textFor = (el) => {
    if (el instanceof HTMLInputElement) return el.value || el.getAttribute("aria-label") || "";
    return el.innerText || el.textContent || el.getAttribute("aria-label") || "";
  };
  const cssPath = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
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
  const out = [];
  for (const el of nodes) {
    const label = el.getAttribute("aria-label") || "";
    const title = el.getAttribute("title") || "";
    const text = textFor(el).replace(/\\s+/g, " ").trim();
    const role = el.getAttribute("role") || (el instanceof HTMLAnchorElement ? "link" : "button");
    const rawHref = el instanceof HTMLAnchorElement ? el.href : el.getAttribute("formaction");
    if (!text && !label && !title && !rawHref) continue;
    out.push({
      text,
      href: rawHref || null,
      role,
      label: label || null,
      title: title || null,
      selector: cssPath(el),
      visible: visible(el),
    });
    if (out.length >= limit) break;
  }
  return out;
}
"""


def _clean_limit(limit: int) -> int:
    return max(1, min(int(limit), 200))


def _action_with_fallback(tool: str, args: dict[str, Any], selector: str | None) -> BrowserActionSuggestion:
    action: BrowserActionSuggestion = {"tool": tool, "args": args}
    if selector:
        fallback_args = dict(args)
        fallback_args.pop("role", None)
        fallback_args.pop("role_name", None)
        fallback_args.pop("label", None)
        fallback_args.pop("text", None)
        fallback_args["selector"] = selector
        action["fallback_args"] = fallback_args
    return action


def _link_action(instance_id: str, link: BrowserLinkCandidate) -> BrowserActionSuggestion:
    role = str(link.get("role") or "").strip()
    role_name = str(link.get("label") or link.get("text") or link.get("title") or "").strip()
    selector = link.get("selector")
    if role and role_name:
        return _action_with_fallback(
            "browser_click",
            {"instance_id": instance_id, "role": role, "role_name": role_name, "response_mode": "outline"},
            selector,
        )
    if link.get("label"):
        return _action_with_fallback(
            "browser_click",
            {"instance_id": instance_id, "label": str(link["label"]), "response_mode": "outline"},
            selector,
        )
    if link.get("text"):
        return _action_with_fallback(
            "browser_click",
            {"instance_id": instance_id, "text": str(link["text"]), "response_mode": "outline"},
            selector,
        )
    if selector:
        return {
            "tool": "browser_click",
            "args": {"instance_id": instance_id, "selector": selector, "response_mode": "outline"},
        }
    return {"tool": "browser_click", "args": {"instance_id": instance_id, "response_mode": "outline"}}


def _clean_link_candidate(raw: Any, instance_id: str) -> BrowserLinkCandidate:
    if not isinstance(raw, dict):
        return {"text": "", "visible": False}
    out: BrowserLinkCandidate = {
        "text": str(raw.get("text") or "")[:200],
        "visible": bool(raw.get("visible")),
    }
    if raw.get("href"):
        out["href"] = str(raw["href"])[:500]
    if raw.get("role"):
        out["role"] = str(raw["role"])[:40]
    if raw.get("label"):
        out["label"] = str(raw["label"])[:200]
    if raw.get("title"):
        out["title"] = str(raw["title"])[:200]
    if raw.get("selector"):
        out["selector"] = str(raw["selector"])[:300]
    out["action"] = _link_action(instance_id, out)
    return out


async def _extract_browser_links(instance_id: str, selector: str, limit: int) -> tuple[Any, list[BrowserLinkCandidate]]:
    session = pool.get(instance_id)
    target = session._target()
    candidates = await target.evaluate(_LINK_EXTRACTOR_JS, {"selector": selector, "limit": _clean_limit(limit)})
    if not isinstance(candidates, list):
        candidates = []
    return session, [_clean_link_candidate(item, instance_id) for item in candidates]


def _links_next_actions(instance_id: str) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_find_link", "args": {"instance_id": instance_id, "query": "<intent>", "limit": 8}},
            {"tool": "browser_page_outline", "args": {"instance_id": instance_id, "limit": 20}},
        ]
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Return compact visible navigation candidates from the active page/frame "
        "without taking an aria snapshot or markdown dump. Use this before "
        "browser_snapshot when you only need to find where to click next. "
        "Each candidate includes an action payload with browser_click args and a CSS fallback."
    ),
)
async def browser_links(instance_id: str, selector: str = "body", limit: int = 50) -> BrowserLinksResult:
    cap = _clean_limit(limit)
    session, links = await _extract_browser_links(instance_id, selector, cap + 1)
    title = await session.page.title()
    target = session._target()
    truncated = len(links) > cap
    return {
        "url": target.url,
        "title": title,
        "links": links[:cap],
        "total": len(links),
        "truncated": truncated,
        "next_actions": _links_next_actions(instance_id),
    }


def _score_link(link: BrowserLinkCandidate, query: str) -> tuple[float, str]:
    fields = {
        "text": (link.get("text") or "").lower(),
        "label": (link.get("label") or "").lower(),
        "title": (link.get("title") or "").lower(),
        "href": (link.get("href") or "").lower(),
    }
    score, reason = weighted_text_score(fields, query, reduced_fields={"href"})
    if link.get("visible"):
        score += 5.0
    return score, reason


@mcp.tool(
    structured_output=False,
    description=(
        "Find likely links/buttons for an intent string using compact page-side "
        "link extraction and local scoring. Returns candidates only; it does not click. "
        "Each candidate includes an action payload with browser_click args and a CSS fallback."
    ),
)
async def browser_find_link(
    instance_id: str,
    query: str,
    selector: str = "body",
    limit: int = 8,
) -> BrowserFindLinkResult:
    cap = _clean_limit(limit)
    session, links = await _extract_browser_links(instance_id, selector, 200)
    ranked: list[BrowserLinkCandidate] = []
    for rank, link in enumerate(links, start=1):
        score, reason = _score_link(link, query)
        if score <= 0:
            continue
        item = cast("BrowserLinkCandidate", dict(link))
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
        "links": ranked[:cap],
        "total": len(ranked),
        "truncated": len(ranked) > cap,
        "next_actions": _links_next_actions(instance_id),
    }

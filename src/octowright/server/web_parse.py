# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""HTML parsing helpers for HTTP-first web discovery."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

from octowright.mcp_types import WebLinkCandidate

MAX_LINKS = 200
MAX_HEADINGS = 20


class PageDiscoveryParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.canonical: str | None = None
        self.headings: list[str] = []
        self.links: list[WebLinkCandidate] = []
        self._form_actions: list[str | None] = []
        self._capture: str | None = None
        self._text_parts: list[str] = []
        self._current_link: WebLinkCandidate | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name.lower(): value or "" for name, value in attrs}
        for handler in (
            self._handle_form_start,
            self._handle_text_capture_start,
            self._handle_canonical_start,
            self._handle_anchor_start,
            self._handle_control_start,
        ):
            if handler(tag, attr):
                return

    def handle_endtag(self, tag: str) -> None:
        for handler in (
            self._handle_form_end,
            self._handle_title_end,
            self._handle_heading_end,
            self._handle_anchor_end,
            self._handle_button_end,
        ):
            if handler(tag):
                return

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text_parts.append(data)

    def _start_capture(self, name: str) -> None:
        self._capture = name
        self._text_parts = []

    def _stop_capture(self) -> None:
        self._capture = None
        self._text_parts = []

    def _text(self) -> str:
        return " ".join("".join(self._text_parts).split())[:200]

    def _current_form_action(self) -> str | None:
        for action in reversed(self._form_actions):
            if action:
                return action
        return None

    def _handle_form_start(self, tag: str, attrs: dict[str, str]) -> bool:
        if tag != "form":
            return False
        action = attrs.get("action")
        self._form_actions.append(urljoin(self.base_url, action) if action else None)
        return True

    def _handle_text_capture_start(self, tag: str, attrs: dict[str, str]) -> bool:
        del attrs
        if tag == "title":
            self._start_capture("title")
            return True
        if tag in {"h1", "h2", "h3"}:
            self._start_capture(tag)
            return True
        return False

    def _handle_canonical_start(self, tag: str, attrs: dict[str, str]) -> bool:
        if tag != "link" or attrs.get("rel", "").lower() != "canonical" or not attrs.get("href"):
            return False
        self.canonical = urljoin(self.base_url, attrs["href"])
        return True

    def _handle_anchor_start(self, tag: str, attrs: dict[str, str]) -> bool:
        if tag != "a" or not attrs.get("href") or len(self.links) >= MAX_LINKS:
            return False
        self._current_link = self._candidate_from_attrs(tag, attrs, href=attrs["href"])
        self._start_capture("a")
        return True

    def _handle_control_start(self, tag: str, attrs: dict[str, str]) -> bool:
        if tag not in {"button", "input"} or len(self.links) >= MAX_LINKS:
            return False
        href = attrs.get("formaction") or attrs.get("href") or self._current_form_action()
        if not (href or attrs.get("aria-label") or attrs.get("title") or attrs.get("value")):
            return False
        self._current_link = self._candidate_from_attrs(tag, attrs, href=href)
        if tag == "input":
            self._finish_link(attrs.get("value", ""))
        else:
            self._start_capture("button")
        return True

    def _handle_form_end(self, tag: str) -> bool:
        if tag != "form":
            return False
        if self._form_actions:
            self._form_actions.pop()
        return True

    def _handle_title_end(self, tag: str) -> bool:
        if self._capture != "title" or tag != "title":
            return False
        self.title = self._text()
        self._stop_capture()
        return True

    def _handle_heading_end(self, tag: str) -> bool:
        if self._capture not in {"h1", "h2", "h3"} or tag != self._capture:
            return False
        text = self._text()
        if text and len(self.headings) < MAX_HEADINGS:
            self.headings.append(text)
        self._stop_capture()
        return True

    def _handle_anchor_end(self, tag: str) -> bool:
        if self._capture != "a" or tag != "a":
            return False
        self._finish_link(self._text())
        self._stop_capture()
        return True

    def _handle_button_end(self, tag: str) -> bool:
        if self._capture != "button" or tag != "button":
            return False
        self._finish_link(self._text())
        self._stop_capture()
        return True

    def _candidate_from_attrs(self, tag: str, attrs: dict[str, str], *, href: str | None) -> WebLinkCandidate:
        candidate: WebLinkCandidate = {"tag": tag}
        if href:
            candidate["href"] = urljoin(self.base_url, href)
        if attrs.get("aria-label"):
            candidate["label"] = attrs["aria-label"][:200]
        if attrs.get("title"):
            candidate["title"] = attrs["title"][:200]
        if attrs.get("rel"):
            candidate["rel"] = attrs["rel"][:100]
        return candidate

    def _finish_link(self, text: str) -> None:
        if self._current_link is None:
            return
        candidate = self._current_link
        clean_text = " ".join(text.split())[:200]
        if clean_text:
            candidate["text"] = clean_text
        if candidate.get("href") or candidate.get("text") or candidate.get("label") or candidate.get("title"):
            self.links.append(candidate)
        self._current_link = None


def parse_page(base_url: str, html: str) -> tuple[str, str | None, list[str], list[WebLinkCandidate]]:
    parser = PageDiscoveryParser(base_url)
    parser.feed(html)
    return parser.title, parser.canonical, parser.headings, parser.links

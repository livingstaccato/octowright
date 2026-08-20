# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from octowright import defaults
from octowright.browser_pool.visuals import _BADGE_POSITION_DEFAULT, _BADGE_POSITIONS
from octowright.defaults import SUPPORTED_KINDS, get_default_url
from octowright.http_headers import validate_extra_http_header_urls, validate_extra_http_headers

#: Playwright's ``channel`` param picks a real installed browser build instead
#: of the bundled one (e.g. system Chrome/Edge, for native GPU/DRM/codec
#: parity a bundled headless build lacks). Fixed allowlist, not passthrough --
#: an unrecognized string here is almost certainly a typo the caller wants to
#: know about immediately rather than as an opaque Playwright launch failure.
SUPPORTED_CHANNELS = frozenset(
    {
        "chrome",
        "chrome-beta",
        "chrome-dev",
        "chrome-canary",
        "msedge",
        "msedge-beta",
        "msedge-dev",
        "msedge-canary",
    }
)

# Env var gating `executable_path`/`launch_args` on the LIVE browser_launch
# call path. Both are a code-execution primitive (an arbitrary local binary +
# arbitrary argv, spawned as a child of the octowright daemon) -- see the
# docstrings on LaunchOptions.executable_path / .launch_args, which are
# already excluded from untrusted JSONL replay (from_launch_record) for the
# same reason. The identical risk on the live MCP call path (equally
# reachable via an agent whose tool args are steered by indirect prompt
# injection from a page it's browsing) had no mitigation until this gate.
# OFF by default -- matches the OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS /
# OCTOWRIGHT_ALLOW_ARBITRARY_CRED_CMDS / OCTOWRIGHT_ALLOW_PY_SCENARIOS
# precedent for exactly this class of risky-but-legitimate power-user
# feature. Falsey tokens mirror recorder._PRIVATE_OFF.
ALLOW_EXECUTABLE_PATH_ENV = "OCTOWRIGHT_ALLOW_EXECUTABLE_PATH"
_ALLOW_EXECUTABLE_PATH_TOKENS_OFF = frozenset({"", "0", "off", "false", "no", "never", "none", "disabled"})


def _executable_path_allowed() -> bool:
    """Return True iff OCTOWRIGHT_ALLOW_EXECUTABLE_PATH opts into
    executable_path/launch_args on the live browser_launch call path. Read at
    call time (not cached) so tests can monkeypatch the env var freely."""
    return os.environ.get(ALLOW_EXECUTABLE_PATH_ENV, "").strip().lower() not in _ALLOW_EXECUTABLE_PATH_TOKENS_OFF


# Escape hatch for the recurring headed-Chromium crash characterised on
# Chrome 148 / macOS 26: a deterministic main-process CHECK abort reached
# through native macOS UI plus the Metal GPU path. OFF by default.
#
# Deliberately a BOOLEAN with a fixed flag set rather than more `launch_args`.
# launch_args is arbitrary argv and is therefore gated behind
# OCTOWRIGHT_ALLOW_EXECUTABLE_PATH (a code-execution opt-in) -- far too heavy a
# door to make someone open just to turn the GPU off while their browsers are
# crashing. A boolean grants no new power, so it needs no gate.
#
# Chromium-only: these are Chromium flags, and Firefox/WebKit would be handed
# argv they do not understand.
DISABLE_GPU_ENV = "OCTOWRIGHT_DISABLE_GPU"
_DISABLE_GPU_TOKENS_OFF = frozenset({"", "0", "off", "false", "no", "never", "none", "disabled"})
#: Applied together: disabling the GPU without also disabling GPU compositing
#: leaves the compositor on a path that still touches the driver.
GPU_DISABLE_ARGS = ("--disable-gpu", "--disable-gpu-compositing")


def resolve_disable_gpu(explicit: bool | None) -> bool:
    """Whether to launch Chromium with the GPU disabled.

    Precedence: an explicit per-launch arg wins; else ``OCTOWRIGHT_DISABLE_GPU``;
    else off. Read at call time so an operator can flip it without a restart.

    HONEST SCOPE: this is an escape hatch, not a proven fix. The crash it exists
    for is characterised (native macOS UI + Metal on Chrome 148 / macOS 26) but
    this mitigation has NOT been confirmed to prevent it -- it gives an operator
    whose browsers are crashing something to try in one launch argument.
    """
    if explicit is not None:
        return explicit
    return os.environ.get(DISABLE_GPU_ENV, "").strip().lower() not in _DISABLE_GPU_TOKENS_OFF


def resolve_protected(explicit: bool | None, *, headed: bool, ephemeral: bool) -> tuple[bool, str]:
    """Decide a browser's effective `protected` flag + the reason it was chosen.

    Precedence: an explicit arg wins; else OCTOWRIGHT_PROTECT_BROWSERS protects
    all; else a headed, non-ephemeral browser is protected by default; else not.
    The reason drives the close-refusal message.
    """
    if explicit is not None:
        return explicit, "explicit"
    if defaults.PROTECT_BROWSERS_DEFAULT:
        return True, "all_default"
    if defaults.PROTECT_HEADED_DEFAULT and headed and not ephemeral:
        return True, "headed_default"
    return False, "unprotected"


@dataclass(frozen=True)
class LaunchOptions:
    kind: str = "chromium"
    url: str | None = None
    headed: bool | None = None
    label: str | None = None
    viewport_w: int | None = None
    viewport_h: int | None = None
    profile: str | None = None
    #: Origin that relative navigation resolves against (Playwright's context
    #: ``base_url``). Explicit here for callers that drive octowright as a
    #: library with no saved persona; otherwise the persona's ``default_url``
    #: supplies it. Set both and this one wins -- the caller is more specific.
    base_url: str | None = None
    stabilize: bool = False
    record_video: bool = False
    trace: bool = False
    har: bool = False
    har_path: str | None = None
    har_mode: str = "minimal"
    har_url_filter: str | None = None
    har_content: str | None = None
    badge: bool = True
    badge_position: str = _BADGE_POSITION_DEFAULT
    tile: bool = False
    ephemeral: bool = False
    session: bool = False
    protected: bool | None = None
    protected_reason: str = "explicit"
    #: Real installed browser build (see SUPPORTED_CHANNELS) instead of the
    #: Playwright-bundled one -- e.g. native Metal/GPU parity a bundled
    #: headless build lacks. Launch-time only: never read back from a JSONL
    #: recording (see from_launch_record) or carried across handoff/relaunch,
    #: since a persisted browser-selection setting has no way to notice the
    #: named channel became uninstalled/unavailable on a later run.
    channel: str | None = None
    #: Absolute path to a specific browser binary, bypassing both the bundled
    #: build and `channel`. NEVER read from a JSONL recording (see
    #: from_launch_record) -- an untrusted recording able to name an arbitrary
    #: local executable is a code-execution primitive, not a browser choice.
    #: Live-call/library-caller only.
    executable_path: str | None = None
    #: Extra native CLI flags appended after octowright's own required
    #: Chromium args (new-tab extension, tiling, /dev/shm workaround -- see
    #: BrowserPool._build_launch_kwargs). Same untrusted-JSONL exclusion as
    #: channel/executable_path: replaying a poisoned recording must not be
    #: able to inject launch flags that weaken the sandbox.
    launch_args: list[str] | None = None
    #: Extra HTTP headers on EVERY request this browser makes -- Playwright's
    #: context-level ``extra_http_headers``. Chosen over a route interceptor
    #: because it is the only layer that also covers popups, new tabs and
    #: subresources, and because it was measured to apply to the SSRF guard's
    #: own validation fetch too (chromium/firefox/webkit, Playwright 1.62), so
    #: the hop the guard checks and the hop the browser makes carry the same
    #: headers. A route-level injector could diverge from the guard.
    #:
    #: NEVER read back from a JSONL recording (see ``from_launch_record``),
    #: for the reason ``channel``/``executable_path``/``launch_args`` are not:
    #: a poisoned recording that can set an arbitrary request header on a
    #: relaunched browser could attach an ``Authorization`` or ``Cookie`` of
    #: its choosing to every site that browser subsequently visits.
    extra_http_headers: dict[str, str] | None = None
    #: URL glob patterns limiting where ``extra_http_headers`` are sent. With
    #: none, the headers are context-level and ride EVERY request the browser
    #: makes -- including cross-origin subresources, which on Chromium makes
    #: them CORS-preflighted so a third party that does not echo
    #: ``Access-Control-Allow-Headers`` rejects them (measured; observed in the
    #: field as blocked font/CDN requests). With patterns, the headers are
    #: applied by a CONTEXT route matching only those URLs, which still follows
    #: popups and new tabs but leaves everyone else's requests untouched.
    #: Ignored when ``extra_http_headers`` is unset.
    extra_http_headers_urls: list[str] | None = None
    #: Launch Chromium with the GPU disabled (see ``resolve_disable_gpu``).
    #: ``None`` defers to ``OCTOWRIGHT_DISABLE_GPU``; ``True``/``False`` force it.
    disable_gpu: bool | None = None

    @classmethod
    def from_mapping(cls, options: dict[str, Any]) -> LaunchOptions:
        launch_options = cls(
            kind=options.get("kind", "chromium"),
            url=options.get("url"),
            headed=options.get("headed"),
            label=options.get("label"),
            viewport_w=options.get("viewport_w"),
            viewport_h=options.get("viewport_h"),
            profile=options.get("profile"),
            base_url=options.get("base_url"),
            stabilize=options.get("stabilize", False),
            record_video=options.get("record_video", False),
            trace=options.get("trace", False),
            har=options.get("har", False),
            har_path=options.get("har_path"),
            har_mode=options.get("har_mode", "minimal"),
            har_url_filter=options.get("har_url_filter"),
            har_content=options.get("har_content"),
            badge=options.get("badge", True),
            badge_position=options.get("badge_position", _BADGE_POSITION_DEFAULT),
            tile=options.get("tile", False),
            ephemeral=options.get("ephemeral", False),
            session=options.get("session", False),
            protected=options.get("protected"),
            channel=options.get("channel"),
            executable_path=options.get("executable_path"),
            launch_args=options.get("launch_args"),
            extra_http_headers=options.get("extra_http_headers"),
            extra_http_headers_urls=options.get("extra_http_headers_urls"),
            disable_gpu=options.get("disable_gpu"),
        )
        launch_options.validate()
        return launch_options

    @classmethod
    def from_launch_record(cls, record: dict[str, Any]) -> LaunchOptions:
        """Translate a JSONL ``launch`` event back into ``LaunchOptions``.

        Unlike ``from_mapping`` (flat dict matching the kwarg names), the
        recording shape uses a nested ``viewport`` dict and stores video
        capture as a ``video_dir`` path rather than a ``record_video`` bool.
        Pre-this-schema recordings also lack the explicit ``headed`` field;
        the historical default for those is ``True``. HAR rotation is the
        caller's job — chain ``.with_har_rotated()`` if you want it.

        Treats every JSONL field as untrusted: a malicious actor able to
        drop a crafted ``*.jsonl`` into ``RECORDINGS_DIR`` (other local user,
        poisoned CI step, etc.) could otherwise pick the engine, profile, or
        HAR write-target on relaunch. ``validate()`` rejects unknown
        ``kind``/``badge_position``/``har_mode``/``har_content`` values, and
        the ``har_path`` containment check below blocks write-anywhere.
        """
        viewport = record.get("viewport") if isinstance(record.get("viewport"), dict) else None
        har_path = record.get("har_path")
        if har_path is not None:
            # HAR writes go under RECORDINGS_DIR by construction in the live
            # launch path. Enforce that on the JSONL replay path too, so a
            # poisoned record can't redirect HAR writes anywhere on disk.
            # Read defaults.RECORDINGS_DIR dynamically so tests that
            # monkeypatch it (or reload defaults after setenv) see the
            # current value, not the import-time snapshot.
            from octowright import defaults as _defaults
            from octowright._paths import safe_under

            if not safe_under(Path(har_path), _defaults.RECORDINGS_DIR):
                har_path = None
        return cls.from_mapping(
            {
                "kind": record.get("kind", "chromium"),
                "url": record.get("url") or get_default_url(),
                "label": record.get("label"),
                "profile": record.get("profile"),
                "viewport_w": viewport.get("w") if viewport else None,
                "viewport_h": viewport.get("h") if viewport else None,
                "headed": record.get("headed", True),
                "stabilize": record.get("stabilize", False),
                "record_video": bool(record.get("video_dir")),
                "trace": record.get("trace", False),
                "har": bool(record.get("har")) and har_path is not None,
                "har_path": har_path,
                "har_mode": record.get("har_mode", "minimal"),
                "har_url_filter": record.get("har_url_filter"),
                "har_content": record.get("har_content"),
                "badge": record.get("badge", True),
                "badge_position": record.get("badge_position", _BADGE_POSITION_DEFAULT),
                "tile": record.get("tile", False),
                "ephemeral": record.get("ephemeral", False),
                "session": record.get("session", False),
            }
        )

    def validate(self) -> None:
        if self.kind not in SUPPORTED_KINDS:
            raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {self.kind!r}")
        if self.badge_position not in _BADGE_POSITIONS:
            raise ValueError(f"badge_position must be one of {sorted(_BADGE_POSITIONS)}, got {self.badge_position!r}")
        if self.ephemeral and self.session:
            raise ValueError("ephemeral and session are mutually exclusive")
        if self.profile and self.session:
            raise ValueError("profile and session are mutually exclusive")
        if self.har_mode not in {"full", "minimal"}:
            raise ValueError("har_mode must be one of ['full', 'minimal']")
        if self.har_content is not None and self.har_content not in {"omit", "embed", "attach"}:
            raise ValueError("har_content must be one of ['omit', 'embed', 'attach']")
        self._validate_browser_selection()
        self._validate_headers()

    def _validate_headers(self) -> None:
        """Header checks, split out because ``to_pool_kwargs`` needs them too.

        ``validate()`` is only reached from ``from_mapping`` (the HTTP body
        path); the MCP ``browser_launch`` builds a ``LaunchOptions`` directly
        and then calls ``to_pool_kwargs``, so anything checked only here was
        unchecked on the tool surface an LLM drives.
        """
        validate_extra_http_headers(self.extra_http_headers)
        validate_extra_http_header_urls(self.extra_http_headers_urls)

    def _validate_browser_selection(self) -> None:
        if self.channel is not None and self.channel not in SUPPORTED_CHANNELS:
            raise ValueError(f"channel must be one of {sorted(SUPPORTED_CHANNELS)}, got {self.channel!r}")
        if (self.executable_path is not None or self.launch_args is not None) and not _executable_path_allowed():
            raise ValueError(
                "executable_path/launch_args are disabled by default (arbitrary local "
                f"process execution) -- set {ALLOW_EXECUTABLE_PATH_ENV}=1 to opt in"
            )
        if self.executable_path is not None and not Path(self.executable_path).expanduser().is_file():
            raise ValueError(f"executable_path {self.executable_path!r} does not exist or is not a file")

    def promoted_profile(self) -> str | None:
        if self.profile is None and self.label is not None and not self.ephemeral and not self.session:
            return self.label
        return self.profile

    def session_name(self, instance_id: str) -> str:
        return self.label or instance_id

    def to_pool_kwargs(self) -> dict[str, Any]:
        """Flatten back to the kwarg dict accepted by ``BrowserPool.launch``.

        This is the canonical shape every call site (``browser_launch``,
        ``browser_quick_launch``, HTTP ``session_launch``, JSONL relaunch)
        funnels through, so adding a new field is a one-line edit here +
        one new dataclass attribute above.
        """
        self._validate_headers()
        return {
            "kind": self.kind,
            "url": self.url,
            "headed": self.headed,
            "label": self.label,
            "viewport_w": self.viewport_w,
            "viewport_h": self.viewport_h,
            "profile": self.profile,
            "stabilize": self.stabilize,
            "record_video": self.record_video,
            "trace": self.trace,
            "har": self.har,
            "har_path": self.har_path,
            "har_mode": self.har_mode,
            "har_url_filter": self.har_url_filter,
            "har_content": self.har_content,
            "badge": self.badge,
            "badge_position": self.badge_position,
            "tile": self.tile,
            "ephemeral": self.ephemeral,
            "session": self.session,
            "protected": self.protected,
            "channel": self.channel,
            "executable_path": self.executable_path,
            "launch_args": self.launch_args,
            "extra_http_headers": self.extra_http_headers,
            "extra_http_headers_urls": self.extra_http_headers_urls,
            "disable_gpu": self.disable_gpu,
        }

    def with_har_rotated(self) -> LaunchOptions:
        """Return a copy whose ``har_path`` won't clobber an existing recording.

        No-op when ``har_path`` is unset. When set, picks the next free
        sibling path via ``rotate_har_path`` and re-flags ``har=True`` so
        relaunch/handoff callers that copy a session's HAR config don't
        have to also remember to set ``har``.
        """
        if not self.har_path:
            return self
        # Local import to avoid a launch_helpers → options circular import.
        from octowright.browser_pool.launch_helpers import rotate_har_path

        rotated = rotate_har_path(Path(self.har_path))
        return replace(self, har=True, har_path=str(rotated) if rotated else None)

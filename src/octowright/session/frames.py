# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from playwright.async_api import Frame

    from octowright.session._protocols import SessionLike


async def switch_frame_impl(
    session: SessionLike,
    *,
    selector: str | None,
    name: str | None,
    url_pattern: str | None,
) -> tuple[Frame, dict[str, Any]]:
    """Resolve an iframe and return (frame, info_dict).
    Exactly one of selector / name / url_pattern must be given.
    """
    provided = [k for k, v in (("selector", selector), ("name", name), ("url_pattern", url_pattern)) if v is not None]
    if len(provided) != 1:
        raise ValueError(f"exactly one of selector/name/url_pattern must be set; got: {provided}")

    # Takes the same fixed lease the public switch_frame() method uses,
    # reentrantly, before touching session.page -- so a direct caller of this
    # helper gets the same target-selection coherence as going through the
    # session method (see SessionOpsMixin.switch_frame).
    async with session.operation("browser_switch_frame"):
        page = session.page
        frame: Frame | None = None
        if selector is not None:
            owner_attr = page.frame_locator(selector).owner
            owner = cast(Callable[[], Any], owner_attr)() if callable(owner_attr) else owner_attr
            handle = await owner.element_handle()
            if handle is None:
                raise RuntimeError(f"no element matches iframe selector {selector!r}")
            frame = await handle.content_frame()
            if frame is None:
                raise RuntimeError(f"no frame found for selector {selector!r}")
        elif name is not None:
            frame = page.frame(name=name)
            if frame is None:
                raise RuntimeError(f"no frame with name={name!r}")
        else:
            assert url_pattern is not None  # nosec B101
            frame = page.frame(url=re.compile(url_pattern))
            if frame is None:
                raise RuntimeError(f"no frame matching url_pattern={url_pattern!r}")

        frames = page.frames
        index = frames.index(frame) if frame in frames else -1
        return frame, {"index": index, "url": frame.url, "name": frame.name}


async def list_frames_impl(session: SessionLike) -> list[dict[str, Any]]:
    async with session.operation("browser_list_frames"):
        return [
            {"index": i, "name": frame.name, "url": frame.url, "is_active": frame is session.active_frame}
            for i, frame in enumerate(session.page.frames)
        ]

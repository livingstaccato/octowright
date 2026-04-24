from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page


async def switch_frame_impl(
    page: "Page",
    *,
    selector: str | None,
    name: str | None,
    url_pattern: str | None,
) -> tuple["Frame", dict[str, Any]]:
    """Resolve an iframe and return (frame, info_dict).
    Exactly one of selector / name / url_pattern must be given.
    """
    provided = [x for x in (selector, name, url_pattern) if x is not None]
    if len(provided) != 1:
        raise ValueError("Exactly one of selector, name, or url_pattern must be provided")

    frame: "Frame | None" = None
    if selector is not None:
        handle = await page.frame_locator(selector).owner().element_handle()
        frame = await handle.content_frame()
        if frame is None:
            raise RuntimeError(f"no frame found for selector {selector!r}")
    elif name is not None:
        frame = page.frame(name=name)
        if frame is None:
            raise RuntimeError(f"no frame with name={name!r}")
    else:
        assert url_pattern is not None
        frame = page.frame(url=re.compile(url_pattern))
        if frame is None:
            raise RuntimeError(f"no frame matching url_pattern={url_pattern!r}")

    frames = page.frames
    index = frames.index(frame) if frame in frames else -1
    return frame, {"index": index, "url": frame.url, "name": frame.name}


def list_frames_impl(page: "Page", active_frame: "Frame | None") -> list[dict[str, Any]]:
    return [
        {
            "index": i,
            "name": f.name,
            "url": f.url,
            "is_active": f is active_frame,
        }
        for i, f in enumerate(page.frames)
    ]

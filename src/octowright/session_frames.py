from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page


async def switch_frame_impl(
    page: Page,
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

    frame: Frame | None = None
    if selector is not None:
        # FrameLocator.owner is a property returning the iframe Locator on Playwright 1.50+;
        # the parens-form keeps backward compat with older versions but mypy's stubs
        # describe it as a non-callable Locator. Suppress the false-positive operator error.
        handle = await page.frame_locator(selector).owner().element_handle()  # type: ignore[operator]
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
        assert url_pattern is not None
        frame = page.frame(url=re.compile(url_pattern))
        if frame is None:
            raise RuntimeError(f"no frame matching url_pattern={url_pattern!r}")

    frames = page.frames
    index = frames.index(frame) if frame in frames else -1
    return frame, {"index": index, "url": frame.url, "name": frame.name}


def list_frames_impl(page: Page, active_frame: Frame | None) -> list[dict[str, Any]]:
    return [
        {
            "index": i,
            "name": f.name,
            "url": f.url,
            "is_active": f is active_frame,
        }
        for i, f in enumerate(page.frames)
    ]

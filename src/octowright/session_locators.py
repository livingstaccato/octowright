from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Locator, Page


def build_locator(
    target: Page | Frame,
    *,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
) -> Locator:
    provided = [k for k, v in (("role", role), ("label", label), ("text", text), ("test_id", test_id)) if v is not None]
    if len(provided) != 1:
        raise ValueError(f"exactly one of role/label/text/test_id must be set; got: {provided}")
    if role is not None:
        kwargs: dict[str, Any] = {}
        if role_name is not None:
            kwargs["name"] = role_name
            kwargs["exact"] = role_exact
        return target.get_by_role(role, **kwargs)  # type: ignore[arg-type]
    if label is not None:
        return target.get_by_label(label)
    if text is not None:
        return target.get_by_text(text)
    assert test_id is not None
    return target.get_by_test_id(test_id)

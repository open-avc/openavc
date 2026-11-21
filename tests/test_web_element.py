"""The web element: what the schema keeps, what the review says about it.

A ``web`` element embeds an http(s) page in the same sandboxed frame the
plugin and custom elements run in, with **no bridge**: the remote page
receives nothing and its messages reach nothing, so the sandbox is the whole
of its reach. The schema enforces the sandbox whitelist (``allow-scripts`` is
always set by the renderer and never belongs in the list), and the review
treats a missing ``url`` like every other "draws an empty box" case.

web 元素：模式保留什么，评审对它说什么。web 元素把 http(s) 页面嵌进与
plugin/custom 相同的沙箱帧，且**无桥**：远程页面收不到任何东西，其消息也
到达不了任何地方，沙箱就是它的全部能力边界。模式强制沙箱白名单
（allow-scripts 由渲染器恒定添加，不属于此列表），评审把缺失 url 视为与其他
"空框"相同的用例。

Every device and URL here is invented. This tests a platform capability.
"""

from __future__ import annotations



from openavc.core.project_loader import UIElement
from openavc.ui.page_review import content_findings

PAGES = {"main"}
DEVICES: set[str] = set()
MACROS: set[str] = set()


# --- Schema: the sandbox whitelist ---------------------------------------------


def test_a_web_element_round_trips_its_url_and_tokens() -> None:
    el = UIElement.model_validate({
        "id": "cam_web", "type": "web",
        "url": "https://camera.local/ui",
        "sandbox_permissions": ["allow-forms", "allow-popups"],
    })
    assert el.url == "https://camera.local/ui"
    assert el.sandbox_permissions == ["allow-forms", "allow-popups"]


def test_tokens_off_the_whitelist_are_dropped_and_logged(caplog) -> None:
    with caplog.at_level("WARNING", logger="openavc.core.project_loader"):
        el = UIElement.model_validate({
            "id": "cam_web", "type": "web", "url": "https://camera.local/ui",
            "sandbox_permissions": ["allow-forms", "allow-scripts", "allow-top-navigation"],
        })
    # allow-scripts is the renderer's own, always; the escape token never lands.
    # allow-scripts 归渲染器所有；逃逸 token 绝不落地。
    assert el.sandbox_permissions == ["allow-forms"]
    assert "allow-top-navigation" in caplog.text


def test_every_token_dropped_leaves_none() -> None:
    el = UIElement.model_validate({
        "id": "cam_web", "type": "web", "url": "https://camera.local/ui",
        "sandbox_permissions": ["allow-pointer-lock"],
    })
    assert el.sandbox_permissions is None


def test_url_scheme_is_kept_as_written() -> None:
    # The schema keeps the string; the scheme rules live in the editor and the
    # renderer, which refuse anything but http(s) at draw time.
    # 模式保留字符串；scheme 规则由编辑器与渲染器执行，绘制时拒绝 http(s)
    # 之外的一切。
    el = UIElement.model_validate({"id": "w", "type": "web", "url": "javascript:alert(1)"})
    assert el.url == "javascript:alert(1)"


# --- The review: an empty box, said the same way as the others -----------------


def _messages(element: dict) -> list[str]:
    return [f.message for f in content_findings(element)]


def test_a_web_element_without_a_url_says_so() -> None:
    said = _messages({"id": "cam_web", "type": "web"})
    assert any(
        "has no url" in m and "draws an empty box" in m
        for m in said
    ), said


def test_a_web_element_with_a_url_is_clean() -> None:
    assert _messages({"id": "cam_web", "type": "web", "url": "https://camera.local/ui"}) == []


def test_a_bound_value_counts_as_supplied() -> None:
    # The show.value binding is what switches the page at runtime, so a bound
    # element with no static url still draws (show.value 绑定即运行时换源，
    # 因此绑定了但无静态 url 的元素仍可绘制).
    element = {
        "id": "cam_web", "type": "web",
        "bindings": {"show": {"value": {"key": "var.camera_stream"}}},
    }
    assert _messages(element) == []

# encoding=utf-8

import asyncio
import sys
from pathlib import Path

import pytest


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from playwright._impl._errors import TargetClosedError
from modules import slider

sys.path.remove(_AUTOVISOR_ROOT)


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message, **_kwargs):
        self.messages.append(("info", message))

    def warn(self, message, **_kwargs):
        self.messages.append(("warn", message))


class _Item:
    def __init__(self, visible):
        self.visible = visible

    async def is_visible(self):
        return bool(self.visible())


class _Locator:
    def __init__(self, page, selector, visible_items):
        self.page = page
        self.selector = selector
        self.visible_items = visible_items

    async def count(self):
        return len(self.visible_items)

    async def is_visible(self):
        return bool(self.visible_items[0](self.page))

    async def all(self):
        return [
            _Item(lambda page=self.page, visible=visible: visible(page))
            for visible in self.visible_items
        ]


class _Page:
    def __init__(self, selectors=None):
        self.selectors = selectors or {}
        self.waits = []

    def locator(self, selector):
        return _Locator(self, selector, self.selectors.get(selector, []))

    async def wait_for_timeout(self, timeout_ms):
        self.waits.append(timeout_ms)


def _always(value):
    return lambda _page: value


def test_slider_visibility_recognizes_current_background_image_class():
    page = _Page({"img.yidun_bg-img": [_always(True)]})

    assert asyncio.run(slider.is_slider_visible(page)) is True


def test_slider_visibility_checks_all_matching_legacy_elements():
    page = _Page({".yidun_bgimg": [_always(False), _always(True)]})

    assert asyncio.run(slider.is_slider_visible(page)) is True


def test_slider_detection_waits_only_within_configured_bound():
    page = _Page(
        {
            "div.yidun_slider": [
                lambda current_page: len(current_page.waits) >= 1
            ]
        }
    )

    result = asyncio.run(
        slider.wait_for_slider_appearance(
            page,
            timeout_ms=500,
            poll_ms=250,
        )
    )

    assert result is True
    assert page.waits == [250]


def test_slider_drag_result_requires_challenge_to_be_hidden():
    visible = _Page({"div.yidun_slider": [_always(True)]})
    hidden = _Page({"div.yidun_slider": [_always(False)]})

    assert asyncio.run(slider.confirm_slider_drag_result(visible)) is False
    assert asyncio.run(slider.confirm_slider_drag_result(hidden)) is True
    assert visible.waits == [slider.SLIDER_RESULT_SETTLE_MS]


def test_slider_verify_skips_quickly_when_challenge_is_absent(monkeypatch):
    monkeypatch.setattr(slider, "cv2", object())
    monkeypatch.setattr(slider, "np", object())
    page = _Page()
    log = _Logger()

    result = asyncio.run(
        slider.slider_verify(
            page,
            detection_timeout_ms=400,
            logger_instance=log,
        )
    )

    assert result is True
    assert page.waits == [200, 200]
    assert log.messages == [("info", "未检测到滑块验证，继续等待登录结果.")]


def test_slider_verify_falls_back_to_manual_on_detection_error(monkeypatch):
    monkeypatch.setattr(slider, "cv2", object())
    monkeypatch.setattr(slider, "np", object())

    class Page:
        def locator(self, _selector):
            raise RuntimeError("challenge DOM changed")

    log = _Logger()
    result = asyncio.run(slider.slider_verify(Page(), logger_instance=log))

    assert result is False
    assert any("改为手动处理" in message for _, message in log.messages)


def test_slider_verify_propagates_closed_page(monkeypatch):
    monkeypatch.setattr(slider, "cv2", object())
    monkeypatch.setattr(slider, "np", object())

    class Page:
        def locator(self, _selector):
            raise TargetClosedError("closed during slider detection")

    with pytest.raises(TargetClosedError, match="closed during slider"):
        asyncio.run(slider.slider_verify(Page()))


def test_slider_verify_retries_visible_failed_challenge(monkeypatch):
    monkeypatch.setattr(slider, "cv2", object())
    monkeypatch.setattr(slider, "np", object())

    async def detected(*_args, **_kwargs):
        return True

    attempts = []

    async def fail_progress(_page):
        attempts.append(True)
        raise ValueError("image unavailable")

    monkeypatch.setattr(slider, "wait_for_slider_appearance", detected)
    monkeypatch.setattr(slider, "progress_img", fail_progress)
    page = _Page()
    log = _Logger()

    result = asyncio.run(
        slider.slider_verify(
            page,
            max_retries=2,
            logger_instance=log,
        )
    )

    assert result is False
    assert attempts == [True, True]
    assert page.waits == [1000]
    assert sum("自动滑块验证失败" in message for _, message in log.messages) == 2
    assert log.messages[-1] == ("warn", "自动过滑块验证失败,请手动验证!")

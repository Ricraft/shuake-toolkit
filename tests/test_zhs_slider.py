import asyncio

from scripts import fetch_zhs_courses


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


class _Item:
    def __init__(self, visible):
        self.visible = visible

    async def is_visible(self):
        return bool(self.visible())


class _Page:
    def __init__(self, selectors=None):
        self.selectors = selectors or {}
        self.waits = []

    def locator(self, selector):
        return _Locator(
            self,
            selector,
            self.selectors.get(selector, []),
        )

    async def wait_for_timeout(self, timeout_ms):
        self.waits.append(timeout_ms)


def _always(value):
    return lambda _page: value


def test_slider_visibility_recognizes_current_background_image_class():
    page = _Page(
        {
            "img.yidun_bg-img": [_always(True)],
        }
    )

    assert asyncio.run(fetch_zhs_courses.is_slider_visible(page)) is True


def test_slider_visibility_checks_all_matching_legacy_elements():
    page = _Page(
        {
            ".yidun_bgimg": [_always(False), _always(True)],
        }
    )

    assert asyncio.run(fetch_zhs_courses.is_slider_visible(page)) is True


def test_slider_detection_waits_within_bound_for_late_challenge():
    page = _Page(
        {
            "div.yidun_slider": [
                lambda current_page: len(current_page.waits) >= 1
            ],
        }
    )

    detected = asyncio.run(
        fetch_zhs_courses.wait_for_slider_appearance(
            page,
            timeout_ms=500,
            poll_ms=250,
        )
    )

    assert detected is True
    assert page.waits == [250]


def test_slider_drag_result_requires_challenge_to_be_hidden():
    visible_page = _Page(
        {
            "div.yidun_slider": [_always(True)],
        }
    )
    hidden_page = _Page(
        {
            "div.yidun_slider": [_always(False)],
        }
    )

    assert (
        asyncio.run(
            fetch_zhs_courses.confirm_slider_drag_result(visible_page)
        )
        is False
    )
    assert (
        asyncio.run(
            fetch_zhs_courses.confirm_slider_drag_result(hidden_page)
        )
        is True
    )
    assert visible_page.waits == [
        fetch_zhs_courses.SLIDER_RESULT_SETTLE_MS
    ]


def test_slider_detection_error_is_not_treated_as_no_challenge(capsys):
    class _BrokenPage:
        def locator(self, _selector):
            raise RuntimeError("page already closed")

        async def wait_for_timeout(self, _timeout_ms):
            return None

    result = asyncio.run(
        fetch_zhs_courses.handle_slider_with_retry(
            _BrokenPage(),
            max_retries=1,
        )
    )

    assert result is False
    assert "滑块验证处理异常: page already closed" in capsys.readouterr().out

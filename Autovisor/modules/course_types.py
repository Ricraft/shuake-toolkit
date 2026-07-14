# encoding=utf-8
"""课程 URL 分类与页面元数据。

这里只保存纯数据和纯判断，供主流程、后台视频任务和测试共同复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit


class CourseKind(str, Enum):
    NORMAL = "normal"
    FUSION = "fusion"
    HIKE = "hike"
    NATIONAL_WISDOM = "national_wisdom"
    MEETING = "meeting"


@dataclass(frozen=True)
class CourseAdapter:
    kind: CourseKind
    title_selectors: tuple[str, ...]
    default_title: str
    title_suffix: str = ""

    def format_title(self, title: str) -> str:
        suffix = f"，{self.title_suffix}" if self.title_suffix else ""
        return f"当前课程:<<{title}>>{suffix}"


ADAPTERS = {
    CourseKind.NORMAL: CourseAdapter(
        CourseKind.NORMAL,
        (".source-name", ".course-name", ".title", "h1", "h2"),
        "普通课程",
    ),
    CourseKind.FUSION: CourseAdapter(
        CourseKind.FUSION,
        (".source-name", ".course-name", ".title", "h1", "h2", ".header-title"),
        "新版课程",
        "是新版课程",
    ),
    CourseKind.HIKE: CourseAdapter(
        CourseKind.HIKE,
        (".course-name", ".source-name", ".title", "h1", "h2"),
        "智慧共享课",
        "是智慧共享课",
    ),
    CourseKind.NATIONAL_WISDOM: CourseAdapter(
        CourseKind.NATIONAL_WISDOM,
        (".course-name", ".source-name", ".title", "h1", "h2", ".header-title"),
        "全国智慧共享课",
        "是全国智慧共享课",
    ),
    CourseKind.MEETING: CourseAdapter(
        CourseKind.MEETING,
        (
            ".course-name",
            ".source-name",
            ".title",
            "h1",
            "h2",
            ".header-title",
            ".meeting-title",
        ),
        "见面课",
        "是见面课",
    ),
}


@dataclass(frozen=True)
class CourseProfile:
    url: str
    kind: CourseKind

    @classmethod
    def from_url(cls, url: str) -> "CourseProfile":
        hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
        if hostname in {"lc.zhihuishu.com", "live.zhihuishu.com"}:
            kind = CourseKind.MEETING
        elif hostname == "wisdom-mooc.zhihuishu.com":
            kind = CourseKind.NATIONAL_WISDOM
        elif hostname == "hike.zhihuishu.com":
            kind = CourseKind.HIKE
        elif hostname == "fusioncourseh5.zhihuishu.com":
            kind = CourseKind.FUSION
        else:
            kind = CourseKind.NORMAL
        return cls(url=url, kind=kind)

    @property
    def adapter(self) -> CourseAdapter:
        return ADAPTERS[self.kind]

    @property
    def is_new_version(self) -> bool:
        return self.kind is CourseKind.FUSION

    @property
    def is_hike_class(self) -> bool:
        return self.kind is CourseKind.HIKE

    @property
    def is_national_wisdom(self) -> bool:
        return self.kind is CourseKind.NATIONAL_WISDOM

    @property
    def is_meeting_class(self) -> bool:
        return self.kind is CourseKind.MEETING

    def working_loop_options(self) -> dict[str, bool]:
        return {
            "is_new_version": self.is_new_version,
            "is_hike_class": self.is_hike_class,
            "is_national_wisdom": self.is_national_wisdom,
            "is_meeting_class": self.is_meeting_class,
        }

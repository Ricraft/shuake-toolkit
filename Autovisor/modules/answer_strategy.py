# encoding=utf-8
"""把题库答案转换为可执行的页面选项点击动作。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from modules.question_bank_client import _match_option


@dataclass(frozen=True)
class AnswerAction:
    kind: Literal["index", "text"]
    value: int | str
    option_value: str | None = None


_TRUE_ANSWERS = {"对", "正确", "是", "√", "true", "yes", "right"}
_FALSE_ANSWERS = {"错", "错误", "否", "×", "false", "no", "wrong"}


def _raw_option_value(raw_options, index: int) -> str | None:
    if not raw_options or index >= len(raw_options):
        return None
    option = raw_options[index]
    if not isinstance(option, (list, tuple)) or not option:
        return None
    value = option[0]
    return None if value is None else str(value)


def _fallback_action(answer_part: str) -> AnswerAction:
    normalized = answer_part.strip()
    lowered = normalized.lower()
    if lowered in _TRUE_ANSWERS:
        return AnswerAction("text", "对")
    if lowered in _FALSE_ANSWERS:
        return AnswerAction("text", "错")
    upper = normalized.upper()
    if len(upper) == 1 and "A" <= upper <= "D":
        return AnswerAction("index", ord(upper) - ord("A"))
    return AnswerAction("text", normalized)


def build_answer_actions(
    answer: str,
    options: list | None = None,
    raw_options: list | None = None,
) -> list[AnswerAction]:
    """生成点击动作；优先按选项文本匹配，失败后再使用传统规则。"""
    if not answer or not str(answer).strip():
        return []

    normalized_answer = str(answer).strip()
    if options:
        matched_indices = _match_option(normalized_answer, options)
        if matched_indices:
            return [
                AnswerAction(
                    "index",
                    index,
                    _raw_option_value(raw_options, index),
                )
                for index in matched_indices
                if 0 <= index < len(options)
            ]

    parts = [part.strip() for part in normalized_answer.split("###") if part.strip()]
    return [_fallback_action(part) for part in parts]

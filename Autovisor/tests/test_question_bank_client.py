# encoding=utf-8

import sys
from pathlib import Path


_AUTOVISOR_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _AUTOVISOR_ROOT)

from modules.question_bank_client import (
    _extract_answer_from_json,
    _extract_last_balanced_json,
    _match_option,
    query_question_bank,
)

sys.path.remove(_AUTOVISOR_ROOT)


def test_balanced_json_extraction_handles_chinese_and_braces_in_string():
    text = '中文分析 {"draft":"忽略"} 最终 {"answer":"答案{一}"}'
    assert _extract_last_balanced_json(text) == '{"answer":"答案{一}"}'
    assert _extract_answer_from_json(text) == "答案{一}"


def test_option_letter_fallback_ignores_empty_options():
    assert _match_option("B", ["", "B. 正确答案"]) == [1]


def test_malformed_answer_item_returns_without_retrying():
    calls = []

    class Response:
        status = 200

        @staticmethod
        def read():
            return b'{"success": true, "data": ["invalid"]}'

        @staticmethod
        def close():
            return None

    class Connection:
        def __init__(self, *_args, **_kwargs):
            calls.append("connect")

        @staticmethod
        def request(*_args, **_kwargs):
            return None

        @staticmethod
        def getresponse():
            return Response()

        @staticmethod
        def close():
            return None

    import modules.question_bank_client as client

    original = client.http.client.HTTPConnection
    client.http.client.HTTPConnection = Connection
    try:
        result = query_question_bank(
            "题目",
            qb_url="http://127.0.0.1:8083/query",
            max_retries=5,
            sleep=lambda _seconds: None,
        )
    finally:
        client.http.client.HTTPConnection = original

    assert result == (None, False)
    assert calls == ["connect"]

from scripts.ai_connectivity_test import AIConnectivityTester


def test_chat_endpoint_normalization_for_custom_openai_compatible_base_urls():
    assert (
        AIConnectivityTester._normalize_chat_endpoint(
            "OTHER", "https://example.invalid/v1"
        )
        == "https://example.invalid/v1/chat/completions"
    )
    assert (
        AIConnectivityTester._normalize_chat_endpoint(
            "OTHER", "https://example.invalid/v1/chat/completions/"
        )
        == "https://example.invalid/v1/chat/completions"
    )


def test_models_endpoint_builder_does_not_duplicate_v1():
    assert (
        AIConnectivityTester._build_models_endpoint("https://example.invalid/v1")
        == "https://example.invalid/v1/models"
    )
    assert (
        AIConnectivityTester._build_models_endpoint(
            "https://example.invalid/v1/chat/completions"
        )
        == "https://example.invalid/v1/models"
    )
    assert (
        AIConnectivityTester._build_models_endpoint("https://example.invalid")
        == "https://example.invalid/v1/models"
    )


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


def test_tool_completion_rejects_provider_without_tool_support(monkeypatch):
    called = []
    monkeypatch.setattr(
        "scripts.ai_connectivity_test.requests.post",
        lambda *args, **kwargs: called.append(True),
    )
    success, message, payload = AIConnectivityTester.chat_completion_with_tools(
        provider="TONGYI",
        api_url="https://example.invalid/v1",
        api_key="k",
        model="qwen",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
    )
    assert success is False
    assert "\u4e0d\u652f\u6301\u5de5\u5177\u8c03\u7528" in message
    assert payload == {"content": "", "tool_calls": []}
    assert called == []


def test_tool_completion_parses_tool_calls_and_sends_tool_roles(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_status",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr("scripts.ai_connectivity_test.requests.post", fake_post)
    success, message, payload = AIConnectivityTester.chat_completion_with_tools(
        provider="OTHER",
        api_url="https://example.invalid/v1",
        api_key="k",
        model="model-x",
        messages=[
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        ],
        tools=[{"type": "function", "function": {"name": "get_status"}}],
    )

    assert success is True
    assert payload["content"] == ""
    assert payload["tool_calls"] == [
        {"id": "call_1", "name": "get_status", "arguments": "{}"}
    ]
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["tool_choice"] == "auto"
    assert captured["payload"]["tools"][0]["function"]["name"] == "get_status"
    roles = [item["role"] for item in captured["payload"]["messages"]]
    assert roles == ["assistant", "tool"]
    assert captured["payload"]["messages"][0]["tool_calls"] == [{"id": "call_1"}]


def test_tool_completion_maps_client_errors_to_unsupported(monkeypatch):
    monkeypatch.setattr(
        "scripts.ai_connectivity_test.requests.post",
        lambda *args, **kwargs: _FakeResponse(400, {"error": "bad request"}),
    )
    success, message, payload = AIConnectivityTester.chat_completion_with_tools(
        provider="OTHER",
        api_url="https://example.invalid/v1",
        api_key="k",
        model="model-x",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
    )
    assert success is False
    assert "\u4e0d\u652f\u6301\u5de5\u5177\u8c03\u7528" in message
    assert payload["tool_calls"] == []

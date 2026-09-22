from src.ai_service import AIConnectivityService


class FakeTester:
    PLATFORM_NAMES = {"SILICON": "硅基流动", "OTHER": "自定义"}
    DEFAULT_MODELS = {"SILICON": "default-silicon"}
    connectivity_result = (True, "连接成功", {"latency_ms": 10})
    models_result = (True, "获取成功", [{"id": "model-a"}])
    chat_result = (True, "OK", "你好呀")
    connectivity_calls = []
    model_calls = []
    chat_calls = []
    tools_calls = []

    @classmethod
    def reset(cls):
        cls.connectivity_result = (True, "连接成功", {"latency_ms": 10})
        cls.models_result = (True, "获取成功", [{"id": "model-a"}])
        cls.chat_result = (True, "OK", "你好呀")
        cls.connectivity_calls = []
        cls.model_calls = []
        cls.chat_calls = []
        cls.tools_calls = []
        cls.tools_result = (
            True,
            "OK",
            {
                "content": "好的",
                "tool_calls": [
                    {"id": "c1", "name": "get_status", "arguments": "{}"}
                ],
            },
        )

    @classmethod
    def test_connectivity(cls, **kwargs):
        cls.connectivity_calls.append(kwargs)
        return cls.connectivity_result

    @classmethod
    def fetch_model_list(cls, **kwargs):
        cls.model_calls.append(kwargs)
        return cls.models_result

    @classmethod
    def chat_completion(cls, **kwargs):
        cls.chat_calls.append(kwargs)
        return cls.chat_result

    @classmethod
    def chat_completion_with_tools(cls, **kwargs):
        cls.tools_calls.append(kwargs)
        return cls.tools_result


def make_service(log=None):
    FakeTester.reset()
    return AIConnectivityService(
        log=log,
        tester_loader=lambda: FakeTester,
        timeout=12,
    )


def test_invalid_payload_and_missing_key_are_rejected_before_loading_tester():
    loads = []
    service = AIConnectivityService(tester_loader=lambda: loads.append(True))

    invalid = service.test_connectivity(None)
    missing_key = service.fetch_model_list(
        {"provider": "SILICON", "api_url": "https://example.invalid/v1"}
    )

    assert invalid["ok"] is False
    assert "格式错误" in invalid["message"]
    assert missing_key["ok"] is False
    assert missing_key["models"] == []
    assert loads == []


def test_builtin_connectivity_uses_default_endpoint_and_normalizes_fields():
    service = make_service()

    result = service.test_connectivity(
        {
            "provider": " silicon ",
            "api_url": "",
            "api_key": " key-value ",
            "model": " model-a ",
        }
    )

    assert result["ok"] is True
    assert result["details"] == {"latency_ms": 10}
    assert FakeTester.connectivity_calls == [
        {
            "provider": "SILICON",
            "api_url": "",
            "api_key": "key-value",
            "model": "model-a",
            "timeout": 12,
        }
    ]


def test_custom_connectivity_and_model_fetch_require_api_url():
    service = make_service()

    connectivity = service.test_connectivity(
        {"provider": "OTHER", "api_key": "key"}
    )
    models = service.fetch_model_list(
        {"provider": "SILICON", "api_key": "key"}
    )

    assert connectivity["ok"] is False
    assert connectivity["message"] == "API 地址不能为空"
    assert models["ok"] is False
    assert models["message"] == "API 地址不能为空"
    assert FakeTester.connectivity_calls == []
    assert FakeTester.model_calls == []


def test_failures_propagate_without_leaking_key_to_logs():
    logs = []
    service = make_service(logs.append)
    FakeTester.connectivity_result = (False, "认证失败", {"status": 401})
    FakeTester.models_result = (False, "模型接口不可用", None)

    connectivity = service.test_connectivity(
        {"provider": "OTHER", "api_url": "https://example.invalid", "api_key": "secret"}
    )
    models = service.fetch_model_list(
        {"provider": "OTHER", "api_url": "https://example.invalid", "api_key": "secret"}
    )

    assert connectivity == {
        "ok": False,
        "success": False,
        "message": "认证失败",
        "details": {"status": 401},
    }
    assert models == {
        "ok": False,
        "success": False,
        "message": "模型接口不可用",
        "models": [],
    }
    assert all("secret" not in message for message in logs)


def test_loader_exceptions_return_stable_failure_shapes():
    unavailable = AIConnectivityService(
        tester_loader=lambda: (_ for _ in ()).throw(ImportError("missing"))
    )
    connectivity = unavailable.test_connectivity(
        {"provider": "SILICON", "api_key": "key"}
    )
    models = unavailable.fetch_model_list(
        {
            "provider": "SILICON",
            "api_url": "https://example.invalid",
            "api_key": "key",
        }
    )

    assert connectivity["ok"] is False
    assert connectivity["message"] == "AI连通性测试模块加载失败"
    assert models["ok"] is False
    assert models["message"] == "AI模块加载失败"
    assert models["models"] == []


def test_tester_call_exceptions_return_stable_failure_shapes():
    class BrokenTester(FakeTester):
        @classmethod
        def test_connectivity(cls, **_kwargs):
            raise RuntimeError("network down")

        @classmethod
        def fetch_model_list(cls, **_kwargs):
            raise RuntimeError("service unavailable")

    service = AIConnectivityService(tester_loader=lambda: BrokenTester)
    connectivity = service.test_connectivity(
        {"provider": "SILICON", "api_key": "key"}
    )
    models = service.fetch_model_list(
        {
            "provider": "SILICON",
            "api_url": "https://example.invalid",
            "api_key": "key",
        }
    )

    assert connectivity == {
        "ok": False,
        "success": False,
        "message": "测试过程发生错误: network down",
    }
    assert models == {
        "ok": False,
        "success": False,
        "message": "获取模型列表时发生错误: service unavailable",
        "models": [],
    }


def test_chat_normalizes_provider_messages_and_uses_default_model():
    service = make_service()

    result = service.chat(
        {
            "provider": " custom ",
            "api_url": "https://example.invalid/v1",
            "api_key": " secret ",
            "model": "",
        },
        [
            {"role": "system", "content": "系统提示"},
            {"role": "tool", "content": "非法角色会降级"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": ""},
        ],
    )

    assert result == {
        "ok": True,
        "success": True,
        "message": "OK",
        "reply": "你好呀",
    }
    assert FakeTester.chat_calls == [
        {
            "provider": "OTHER",
            "api_url": "https://example.invalid/v1",
            "api_key": "secret",
            "model": "",
            "messages": [
                {"role": "system", "content": "系统提示"},
                {"role": "user", "content": "非法角色会降级"},
                {"role": "user", "content": "你好"},
            ],
            "timeout": 90,
        }
    ]


def test_chat_rejects_empty_custom_endpoint_before_network_call():
    service = make_service()

    result = service.chat(
        {"provider": "OTHER", "api_url": "", "api_key": "secret"},
        [{"role": "user", "content": "你好"}],
    )

    assert result == {
        "ok": False,
        "success": False,
        "message": "API 地址不能为空",
    }
    assert FakeTester.chat_calls == []


def test_chat_rejects_successful_but_empty_model_reply():
    logs = []
    service = make_service(logs.append)
    FakeTester.chat_result = (True, "OK", "   ")

    result = service.chat(
        {"provider": "SILICON", "api_key": "secret"},
        [{"role": "user", "content": "你好"}],
    )

    assert result == {
        "ok": False,
        "success": False,
        "message": "AI 模型返回了空回复",
    }
    assert any("模型返回空回复" in line for line in logs)


def test_chat_with_tools_requires_key_and_tool_definitions():
    service = make_service()
    missing_key = service.chat_with_tools(
        {"provider": "SILICON", "api_url": "https://example.invalid/v1"},
        [{"role": "user", "content": "hi"}],
        [{"type": "function"}],
    )
    assert missing_key["ok"] is False
    assert "API Key" in missing_key["message"]

    empty_tools = service.chat_with_tools(
        {"provider": "SILICON", "api_url": "https://example.invalid/v1", "api_key": "k"},
        [{"role": "user", "content": "hi"}],
        [],
    )
    assert empty_tools["ok"] is False
    assert FakeTester.tools_calls == []


def test_chat_with_tools_forwards_tools_and_returns_tool_calls():
    service = make_service()
    tools = [{"type": "function", "function": {"name": "get_status"}}]
    result = service.chat_with_tools(
        {
            "provider": "SILICON",
            "api_url": "https://example.invalid/v1",
            "api_key": "secret",
            "model": "model-x",
        },
        [{"role": "user", "content": "现在什么状态"}],
        tools,
    )
    assert result["ok"] is True
    assert result["reply"] == "\u597d\u7684"
    assert result["tool_calls"] == [
        {"id": "c1", "name": "get_status", "arguments": "{}"}
    ]
    call = FakeTester.tools_calls[0]
    assert call["tools"] == tools
    assert call["model"] == "model-x"
    assert call["timeout"] == 90


def test_chat_with_tools_reports_failure_without_tool_calls():
    service = make_service()
    FakeTester.tools_result = (False, "\u5f53\u524d\u6a21\u578b\u4e0d\u652f\u6301\u5de5\u5177\u8c03\u7528", {})
    result = service.chat_with_tools(
        {"provider": "SILICON", "api_url": "https://example.invalid/v1", "api_key": "k"},
        [{"role": "user", "content": "hi"}],
        [{"type": "function"}],
    )
    assert result["ok"] is False
    assert result["tool_calls"] == []
    assert "\u4e0d\u652f\u6301" in result["message"]

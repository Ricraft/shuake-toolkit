"""Web-facing AI connectivity and model-list orchestration."""

from __future__ import annotations

from collections.abc import Callable


def _load_default_tester():
    from scripts.ai_connectivity_test import AIConnectivityTester

    return AIConnectivityTester


class AIConnectivityService:
    def __init__(
        self,
        *,
        log: Callable[[str], None] | None = None,
        tester_loader: Callable[[], object] = _load_default_tester,
        timeout: int = 30,
    ):
        self.log = log or (lambda _message: None)
        self.tester_loader = tester_loader
        self.timeout = timeout

    @staticmethod
    def _normalize_config(config) -> dict | None:
        if not isinstance(config, dict):
            return None
        provider = (
            str(config.get("provider", "SILICON") or "SILICON")
            .strip()
            .upper()
        )
        if provider == "CUSTOM":
            provider = "OTHER"
        return {
            "provider": provider,
            "api_url": str(config.get("api_url", "") or "").strip(),
            "api_key": str(config.get("api_key", "") or "").strip(),
            "model": str(config.get("model", "") or "").strip(),
        }

    @staticmethod
    def _normalize_messages(messages) -> list[dict]:
        if not isinstance(messages, list):
            return []
        allowed_roles = {"system", "user", "assistant"}
        normalized = []
        for message in messages[-32:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "user") or "user").strip().lower()
            if role not in allowed_roles:
                role = "user"
            content = str(message.get("content", "") or "").strip()
            if not content:
                continue
            if len(content) > 4000:
                content = content[:4000] + "…"
            normalized.append({"role": role, "content": content})
        return normalized

    @staticmethod
    def _failure(message: str, *, models: bool = False) -> dict:
        result = {"ok": False, "success": False, "message": message}
        if models:
            result["models"] = []
        return result

    def _tester(self, *, models: bool = False):
        try:
            return self.tester_loader(), None
        except Exception as exc:
            self.log(f"[AI] 连通性模块加载失败: {exc}")
            message = "AI模块加载失败" if models else "AI连通性测试模块加载失败"
            return None, self._failure(message, models=models)

    @staticmethod
    def _provider_name(tester, provider: str) -> str:
        names = getattr(tester, "PLATFORM_NAMES", {})
        return names.get(provider, provider) if isinstance(names, dict) else provider

    def test_connectivity(self, config) -> dict:
        normalized = self._normalize_config(config)
        if normalized is None:
            return self._failure("AI连通性测试配置格式错误")
        if not normalized["api_key"]:
            return self._failure("API Key 不能为空")
        if normalized["provider"] == "OTHER" and not normalized["api_url"]:
            return self._failure("API 地址不能为空")

        tester, failure = self._tester()
        if failure:
            return failure
        provider = normalized["provider"]
        self.log(
            f"[AI测试] 正在测试 {self._provider_name(tester, provider)} 连通性..."
        )
        try:
            success, message, details = tester.test_connectivity(
                provider=provider,
                api_url=normalized["api_url"],
                api_key=normalized["api_key"],
                model=normalized["model"],
                timeout=self.timeout,
            )
        except Exception as exc:
            self.log(f"[AI测试] 测试过程发生错误: {exc}")
            return self._failure(f"测试过程发生错误: {exc}")

        success = bool(success)
        if success:
            self.log(f"[AI测试] 连通性测试成功: {message}")
        else:
            self.log(f"[AI测试] 连通性测试失败: {message}")
        return {
            "ok": success,
            "success": success,
            "message": str(message),
            "details": details if isinstance(details, dict) else {},
        }

    def chat(self, config, messages) -> dict:
        normalized = self._normalize_config(config)
        if normalized is None:
            return self._failure("AI 聊天配置格式错误")
        if not normalized["api_key"]:
            return self._failure("API Key 不能为空，请先在右侧完成模型配置")
        if normalized["provider"] == "OTHER" and not normalized["api_url"]:
            return self._failure("API 地址不能为空")
        clean_messages = self._normalize_messages(messages)
        if not clean_messages:
            return self._failure("聊天消息格式错误")

        tester, failure = self._tester()
        if failure:
            return failure
        chat_fn = getattr(tester, "chat_completion", None)
        if chat_fn is None:
            return self._failure("AI 聊天模块不可用")

        provider = normalized["provider"]
        model = normalized["model"] or getattr(tester, "DEFAULT_MODELS", {}).get(
            provider, ""
        )
        try:
            success, message, reply = chat_fn(
                provider=provider,
                api_url=normalized["api_url"],
                api_key=normalized["api_key"],
                model=model,
                messages=clean_messages,
                timeout=90,
            )
        except Exception as exc:
            self.log(f"[AI聊天] 对话过程发生错误: {exc}")
            return self._failure(f"对话过程发生错误: {exc}")

        success = bool(success)
        if not success:
            self.log(f"[AI聊天] 对话失败: {message}")
        reply_text = str(reply or "").strip()
        if success and not reply_text:
            self.log("[AI聊天] 对话失败: 模型返回空回复")
            return self._failure("AI 模型返回了空回复")
        return {
            "ok": success,
            "success": success,
            "message": str(message),
            "reply": reply_text,
        }

    def chat_with_tools(self, config, messages, tools) -> dict:
        normalized = self._normalize_config(config)
        if normalized is None:
            return self._failure("AI 聊天配置格式错误")
        if not normalized["api_key"]:
            return self._failure("API Key 不能为空，请先在右侧完成模型配置")
        if normalized["provider"] == "OTHER" and not normalized["api_url"]:
            return self._failure("API 地址不能为空")
        if not isinstance(messages, list) or not messages:
            return self._failure("聊天消息格式错误")
        if not isinstance(tools, list) or not tools:
            return self._failure("工具定义为空")

        tester, failure = self._tester()
        if failure:
            return failure
        chat_fn = getattr(tester, "chat_completion_with_tools", None)
        if chat_fn is None:
            return self._failure("AI 工具调用模块不可用")

        provider = normalized["provider"]
        model = normalized["model"] or getattr(tester, "DEFAULT_MODELS", {}).get(
            provider, ""
        )
        try:
            success, message, payload = chat_fn(
                provider=provider,
                api_url=normalized["api_url"],
                api_key=normalized["api_key"],
                model=model,
                messages=messages,
                tools=tools,
                timeout=90,
            )
        except Exception as exc:
            self.log(f"[AI工具] 调用过程发生错误: {exc}")
            return self._failure(f"调用过程发生错误: {exc}")

        payload = payload if isinstance(payload, dict) else {}
        return {
            "ok": bool(success),
            "success": bool(success),
            "message": str(message),
            "reply": str(payload.get("content") or ""),
            "tool_calls": list(payload.get("tool_calls") or []),
        }

    def fetch_model_list(self, config) -> dict:
        normalized = self._normalize_config(config)
        if normalized is None:
            return self._failure("AI模型配置格式错误", models=True)
        if not normalized["api_key"]:
            return self._failure("API Key 不能为空", models=True)
        if not normalized["api_url"]:
            return self._failure("API 地址不能为空", models=True)

        tester, failure = self._tester(models=True)
        if failure:
            return failure
        provider = normalized["provider"]
        self.log(
            f"[AI模型] 正在获取 {self._provider_name(tester, provider)} 的模型列表..."
        )
        try:
            success, message, models = tester.fetch_model_list(
                provider=provider,
                api_url=normalized["api_url"],
                api_key=normalized["api_key"],
                timeout=self.timeout,
            )
        except Exception as exc:
            self.log(f"[AI模型] 获取模型列表时发生错误: {exc}")
            return self._failure(f"获取模型列表时发生错误: {exc}", models=True)

        success = bool(success)
        normalized_models = list(models) if isinstance(models, (list, tuple)) else []
        if success:
            self.log(f"[AI模型] 成功获取 {len(normalized_models)} 个模型")
        else:
            self.log(f"[AI模型] 获取模型列表失败: {message}")
        return {
            "ok": success,
            "success": success,
            "message": str(message),
            "models": normalized_models,
        }

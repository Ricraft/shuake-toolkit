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
        return {
            "provider": str(config.get("provider", "SILICON") or "SILICON")
            .strip()
            .upper(),
            "api_url": str(config.get("api_url", "") or "").strip(),
            "api_key": str(config.get("api_key", "") or "").strip(),
            "model": str(config.get("model", "") or "").strip(),
        }

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
